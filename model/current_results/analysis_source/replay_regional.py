"""Prepare and replay a fresh regional run from the immutable current archive."""
from __future__ import annotations

import argparse
import ast
from copy import deepcopy
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

sys.dont_write_bytecode = True
SOURCE_DEFAULT = Path(__file__).resolve().parents[1]/'regional'
KERNEL_FUNCTIONS = {
    'source_module','close_figure_axes','archived_plot_module','sha','write_json','freeze_source','verify_manifest',
    'segment_parameters','calendar','training_allocation','policy_results','aggregate_rotations','select_management_rules',
    'route_quota','resume_output','worker_signature','period_grain_retention','verify_selections','paired_spatial_results',
    'verify_run','initialize_worker','simulate_representative','run_workers','response_seasons','spatial_checks','aggregate_adaptive',
}


def sha(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):value.update(block)
    return value.hexdigest()


def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False)+'\n')


def native_hashes(root):
    return {p.relative_to(root).as_posix():sha(p) for p in sorted(Path(root).rglob('*.py')) if '__pycache__' not in p.parts}


def verify_source(source,calibration=None):
    source=Path(source)
    manifest=json.loads((source/'verification/file_manifest.json').read_text())
    for relative,item in manifest.items():
        if sha(source/relative)!=item['sha256']:raise ValueError('Archived file changed: '+relative)
    native=native_hashes(source/'source_snapshots/native_process')
    identity=json.loads((source/'verification/native_source_identity.json').read_text())
    if native!=identity['files'] or len(native)!=143:raise ValueError('Selected native source identity differs')
    report=dict(source_archive_verified=True,archived_file_count=len(manifest),native_python_files=len(native),
        native_file_identity_sha256=hashlib.sha256(json.dumps(native,sort_keys=True).encode()).hexdigest())
    if calibration is not None:
        other=native_hashes(Path(calibration)/'source_snapshots/native')
        if native!=other:raise ValueError('Calibration and regional native Python source differ')
        report['calibration_native_content_exact']=True
    return report


def node_source(text,node):
    return ''.join(text.splitlines(keepends=True)[node.lineno-1:node.end_lineno])


def replace_once(text,old,new):
    if text.count(old)!=1:raise ValueError('Frozen source adaptation anchor differs: '+old[:90])
    return text.replace(old,new,1)


def generate_kernel(original):
    tree=ast.parse(original)
    imports=[node_source(original,node) for node in tree.body if isinstance(node,(ast.Import,ast.ImportFrom))]
    # The removed historical plotting block held an import still needed by
    # the current adaptive figure exporter.
    imports.append('from matplotlib.figure import Figure\n')
    constants=[node_source(original,node) for node in tree.body if isinstance(node,ast.Assign)
        and any(isinstance(target,ast.Name) and target.id in {'FRACTIONS','REDUCTIONS','POLICIES','TECH','_WORKER'} for target in node.targets)]
    functions={node.name:node_source(original,node) for node in tree.body if isinstance(node,ast.FunctionDef)}
    body=[functions[name] for name in functions if name in KERNEL_FUNCTIONS]
    allocation=functions['allocations']
    first=allocation.index("    old = pd.read_csv(root/'source_snapshots/archived_regional/frozen_training_allocation.csv')")
    last=allocation.index("    memberships = pd.read_csv(root/'data/class_memberships.csv')",first)
    current='''    revised = primary.copy()
    revised.policy = revised.policy.str.replace('targeted_', 'refit_targeted_', regex=False)
    revised['parameter_set'] = 'source_screened'
    revised.to_csv(root/'tables/all_parameter_policy_years.csv', index=False)
    revised.groupby(['parameter_set','period','policy'],as_index=False).mean(numeric_only=True).to_csv(root/'tables/policy_period_means.csv',index=False)
    primary[primary.period.eq('testing')].groupby(['policy','reduction_fraction'],as_index=False).mean(numeric_only=True).to_csv(root/'tables/conditional_policy_testing_means.csv',index=False)
    contrasts=[]
    for year,g in primary.groupby('harvest_year'):
        g=g.set_index('policy')
        for cut in REDUCTIONS:
            target,uniform=g.loc[f'targeted_{int(cut*100)}pct'],g.loc[f'uniform_{int(cut*100)}pct']
            contrasts.append(dict(parameter_set='source_screened',harvest_year=int(year),period=target.period,
                reduction_fraction=cut,policy='refit_targeted',additional_grain_t=target.grain_production_t-uniform.grain_production_t,
                additional_et_m3=target.modeled_total_et_volume_m3-uniform.modeled_total_et_volume_m3,
                additional_drainage_m3=target.bottom_drainage_volume_m3-uniform.bottom_drainage_volume_m3,
                field_water_difference_m3=target.field_irrigation_m3-uniform.field_irrigation_m3))
    pd.DataFrame(contrasts).to_csv(root/'tables/policy_contrasts.csv',index=False)
'''
    body.append(allocation[:first]+current+allocation[last:])
    spatial=functions['spatial_tables']
    spatial=replace_once(spatial,"    old = pd.read_csv(root/'source_snapshots/archived_regional/rotation_summaries.csv')\n",'')
    spatial=replace_once(spatial,"    allocations = {'source_screened': pd.read_csv(root/'parameters/frozen_training_allocation.csv'),\n        'archived': pd.read_csv(root/'source_snapshots/archived_regional/frozen_training_allocation.csv')}","    allocations = {'source_screened': pd.read_csv(root/'parameters/frozen_training_allocation.csv')}")
    spatial=replace_once(spatial,"    for label, response in [('archived', old), ('source_screened', new)]:","    for label, response in [('source_screened', new)]:")
    a=spatial.index("    changes = pd.read_csv(root/'tables/allocation_changes.csv')")
    b=spatial.index("    selected = units[units.reduction_fraction.eq(.5)]",a)
    spatial=spatial[:a]+spatial[b:]
    spatial=replace_once(spatial,"    selected = selected.reset_index().merge(turnover[turnover.reduction_fraction.eq(.5)][['representative_id', 'minimum_changed_share']], on='representative_id', validate='one_to_one')","    selected = selected.reset_index()")
    spatial=replace_once(spatial,"    cells['screened_minus_archived_quota'] = cells.source_screened_targeted_mean_quota-cells.archived_targeted_mean_quota\n",'')
    spatial=replace_once(spatial,", 'minimum_changed_share'",'')
    spatial=replace_once(spatial,"        for parameter in ['archived', 'source_screened']:","        for parameter in ['source_screened']:")
    body.append(spatial)
    analyze=functions['analyze']
    a=analyze.index("    publication = archived_plot_module(root, 'publication_figures')")
    b=analyze.index("    adaptive = archived_plot_module(root, 'export_results')",a)
    analyze=analyze[:a]+analyze[b:]
    a=analyze.index("    tables = ['regional_policy_annual_results'")
    b=analyze.index("    seasons = pd.read_csv(root/'predictions/all_season_summaries.csv')",a)
    analyze=analyze[:a]+analyze[b:]
    # Figure is a current-input exploration, with no archived-model values.
    marker="    seasons = pd.read_csv(root/'predictions/all_season_summaries.csv')"
    raw='''    cells=pd.read_csv(root/'tables/source_cell_map_values.csv')
    fig,axes=plt.subplots(2,2,figsize=(8.5,7.5),layout='constrained')
    for ax,column,label in zip(axes.flat,['training_mean_precipitation_mm','training_mean_et0_mm','training_climatic_deficit_mm','profile_available_water_mm'],['Training precipitation (mm yr⁻¹)','Training reference ET₀ (mm yr⁻¹)','Training climatic deficit (mm yr⁻¹)','Available water in 2 m profile (mm)']):
        dots=ax.scatter(cells.longitude,cells.latitude,c=cells[column],s=6,marker='s',cmap='viridis')
        ax.set(xlabel='Longitude (°E)',ylabel='Latitude (°N)',xlim=(112,121.5),ylim=(32,41))
        fig.colorbar(dots,ax=ax,label=label,orientation='horizontal',pad=.05)
    close_figure_axes(fig)
    for extension in ['png','pdf']:fig.savefig(root/f'figures/raw_climate_soil_maps.{extension}',dpi=350)
    plt.close(fig)
'''
    analyze=replace_once(analyze,marker,raw+marker)
    body.append(analyze)
    result='\n'.join(imports)+"\nsys.dont_write_bytecode=True\n"+'\n'.join(constants)+'\n\n'+'\n\n'.join(body)
    ast.parse(result)
    if '/model/2026-' in result or "PROJECT/'model/2026-" in result or 'archived_regional/' in result:
        raise ValueError('Fresh kernel retains a historical-path dependency')
    return result


def generate_classes(original):
    tree=ast.parse(original)
    constants=[]
    for node in tree.body:
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id in {'TRAIN','TEST','YEARS','QUOTAS','CLASS_ORDER','CLASS_LABEL','METRICS'} for t in node.targets):
            constants.append(ast.unparse(node))
    functions=[node_source(original,node) for node in tree.body if isinstance(node,ast.FunctionDef) and node.name in {'response_tables','select_candidates'}]
    return "from pathlib import Path\nimport numpy as np\nimport pandas as pd\nROOT=Path(__file__).resolve().parents[2]\n"+'\n'.join(constants)+'\n\n'+'\n\n'.join(functions)


def generate_exporter(original):
    original=replace_once(original,'import regional_recalculation as driver','import regional_kernel as driver')
    original=replace_once(original,"ROOT = Path(__file__).resolve().parents[1]/'regional'","ROOT = Path(__file__).resolve().parents[1]")
    tree=ast.parse(original);node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='current_tables')
    clean='''def current_tables():
    for name,target in [('spatial_unit_period_means','current_spatial_unit_period_means'),('source_cell_map_values','current_source_cell_map_values'),('spatial_latitude_bands','current_spatial_latitude_bands')]:
        shutil.copy2(ROOT/f'tables/{name}.csv',ROOT/f'tables/{target}.csv')
    cells=pd.read_csv(ROOT/'tables/source_cell_map_values.csv')
    bands=pd.read_csv(ROOT/'tables/spatial_latitude_bands.csv')
    driver.write_json(ROOT/'verification/table_identifier_semantics.json',dict(source_screened='compatibility identifier for the selected management_refit effective shared cards',current_only=True,historical_predictions_used=False))
    return cells,bands
'''
    original=replace_once(original,node_source(original,node),clean)
    original=replace_once(original,"['regional_benchmark.py','regional_pipeline.py','regional_export.py']","['replay_regional.py','regional_kernel.py','regional_export.py']")
    ast.parse(original)
    return original


def prepare(source,output):
    source,output=Path(source).resolve(),Path(output).resolve()
    if output==source or source in output.parents:raise ValueError('Fresh output must be outside the sealed source run')
    if (output/'verification/input_manifest.json').exists():
        load_kernel(output).verify_run(output);return
    if output.exists() and any(output.iterdir()):raise ValueError('Fresh output must be empty')
    verify_source(source)
    for folder in ['analysis_source','data','parameters','predictions/per_representative','adaptive/per_representative','spatial/per_representative','tables','figures','verification','source_snapshots']:
        (output/folder).mkdir(parents=True,exist_ok=True)
    manifest=[]
    def copy(relative,destination=None):
        target=output/(destination or relative);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source/relative,target)
        manifest.append(dict(source=str((source/relative).resolve()),snapshot=str(target.relative_to(output)),sha256=sha(target)))
    entries=json.loads((source/'verification/input_manifest.json').read_text())
    for row in entries:
        relative=row['snapshot']
        if relative.startswith('source_snapshots/archived_regional/'):continue
        if relative in {'data/calibration_provenance/reference_field_comparisons.csv','data/calibration_provenance/reference_station_comparisons.csv'}:continue
        if relative.startswith('source_snapshots/analysis_sources/') and Path(relative).name not in {'build_classes.py','export_results.py','retention_priors.py'}:continue
        if relative=='source_snapshots/regional_recalculation.py':copy(relative,'source_snapshots/original_regional_driver.py');continue
        copy(relative)
    for relative in ['verification/native_source_identity.json','verification/parameter_transfer.json']:
        copy(relative)
    driver=(source/'source_snapshots/regional_recalculation.py').read_text()
    kernel=generate_kernel(driver)
    (output/'analysis_source/regional_kernel.py').write_text(kernel)
    original_classes=output/'source_snapshots/analysis_sources/build_classes.py'
    class_source=original_classes.read_text();original_classes.rename(original_classes.with_name('original_build_classes.py'))
    original_classes.write_text(generate_classes(class_source))
    # Replace the corresponding hash with the purely numerical class helper.
    for row in manifest:
        if row['snapshot']=='source_snapshots/analysis_sources/build_classes.py':
            row['snapshot']='source_snapshots/analysis_sources/original_build_classes.py'
    exporter=(source/'source_snapshots/regional_export.py').read_text()
    (output/'analysis_source/regional_export.py').write_text(generate_exporter(exporter))
    shutil.copy2(Path(__file__),output/'analysis_source/replay_regional.py')
    for relative in ['analysis_source/regional_kernel.py','analysis_source/regional_export.py','analysis_source/replay_regional.py','source_snapshots/analysis_sources/build_classes.py']:
        path=output/relative;manifest.append(dict(source=str(path.resolve()),snapshot=relative,sha256=sha(path)))
    protocol=deepcopy(json.loads((source/'parameters/frozen_protocol.json').read_text()))
    protocol.update(driver_sha256=sha(output/'analysis_source/regional_kernel.py'),
        source_fingerprint=hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest(),
        replay_source_protocol_sha256=sha(source/'parameters/frozen_protocol.json'),
        fresh_replay=True,previous_predictions_copied=False,historical_comparison_predictions_used=False)
    write(output/'verification/input_manifest.json',manifest);write(output/'parameters/frozen_protocol.json',protocol)
    write(output/'verification/replay_preparation.json',dict(
        controlling_source=str(source),original_source_paths_are_metadata_only=True,previous_predictions_copied=False,
        historical_comparison_predictions_used=False,native_source_and_effective_cards_unchanged=True,
        original_driver_sha256=sha(source/'source_snapshots/regional_recalculation.py'),
        pure_kernel_sha256=protocol['driver_sha256'],copied_input_files=len(manifest)))
    load_kernel(output).verify_run(output)
    if any((output/'predictions').rglob('*.csv')) or any((output/'adaptive').rglob('*.csv')) or any((output/'spatial').rglob('*.csv')):
        raise AssertionError('Prediction files were copied during fresh preparation')


def load_kernel(output):
    path=str(Path(output)/'analysis_source')
    if path not in sys.path:sys.path.insert(0,path)
    module=importlib.import_module('regional_kernel')
    if Path(module.__file__).resolve()!=(Path(output)/'analysis_source/regional_kernel.py').resolve():
        raise AssertionError('Replay kernel imported outside fresh run')
    return module


def benchmark(output):
    kernel=load_kernel(output);root=Path(output);kernel.verify_run(root);kernel.initialize_worker(root)
    point=kernel.pd.read_csv(root/'data/representative_cells.csv').iloc[0].to_dict()
    state=None;rows=[];tick=time.monotonic()
    for crop,start,end,year in kernel.calendar()[:4]:
        weather=kernel._WORKER['weather'][0].loc[start:end].reset_index().to_dict('records');layers=kernel._WORKER['soils'][0]
        inputs=dict(crop=crop,maturity_group='middle',latitude_deg=point['latitude'],elevation_m=point['elevation_m'],start_date=start,end_date=end,
            weather=weather,soil_layers=layers,technology=kernel.TECH,et0_method='provided',irrigation_events=[],management_class='fertilized')
        if state is None:inputs['initial_theta']=[l['wilting_point']+.8*(l['field_capacity']-l['wilting_point']) for l in layers]
        if crop!='fallow':
            inputs['cutting_date']=end
            schedule=[(start,60),(f'{year}-03-25',90),(f'{year}-04-15',90),(f'{year}-05-10',60)] if crop=='wheat' else [(start,80)]
            inputs['irrigation_events']=[dict(event_id=f'quota_{i}',date=day,amount_mm=amount,measurement_location='field') for i,(day,amount) in enumerate(schedule)]
        previous=state.storage_mm() if state is not None else None
        result=kernel._WORKER['simulate'](inputs,kernel.segment_parameters(crop,kernel._WORKER['cards'],kernel._WORKER['hydro']),state);state=result.final_state
        rows.append(dict(crop=crop,n_days=len(weather),initial_storage_mm=result.summary['initial_storage_mm'],final_storage_mm=result.summary['final_storage_mm'],
            storage_continuity_error_mm=result.summary['initial_storage_mm']-previous if previous is not None else 0.,yield_kg_ha=result.summary['yield_kg_ha'],et_mm=result.summary['et_mm'],
            maximum_water_residual_mm=max(abs(d['balance_residual_mm']) for d in result.daily),maximum_carbon_residual_kg_ha=max(abs(d['crop_carbon_residual_kg_ha']) for d in result.daily)))
    report=dict(four_segments=len(rows),modeled_days=sum(row['n_days'] for row in rows),sample_results=rows,elapsed_seconds=time.monotonic()-tick,
        all_conservation_checks_passed=all(row['maximum_water_residual_mm']<1e-6 and row['maximum_carbon_residual_kg_ha']<1e-6 and abs(row['storage_continuity_error_mm'])<1e-7 for row in rows))
    if not report['all_conservation_checks_passed']:raise AssertionError(report)
    write(root/'verification/replay_benchmark.json',report);print(json.dumps(report,indent=2))


def verify_completed(root):
    kernel=load_kernel(root);kernel.verify_run(root)
    for relative,item in json.loads((Path(root)/'verification/file_manifest.json').read_text()).items():
        if sha(Path(root)/relative)!=item['sha256']:raise ValueError('Completed replay artifact changed: '+relative)


def execute(output,stage,workers):
    root=Path(output);kernel=load_kernel(root);kernel.verify_run(root)
    if (root/'verification/completion.json').exists() and json.loads((root/'verification/completion.json').read_text()).get('current_only_publication_exports'):
        verify_completed(root);print('Completed fresh replay verified without mutation');return
    stages=['responses','allocations','adaptive','analyze','export'] if stage=='run' else [stage]
    for item in stages:
        print('Replay stage:',item,flush=True)
        if item=='responses':kernel.run_workers(root,'responses',workers);kernel.run_workers(root,'spatial',workers)
        elif item=='allocations':kernel.allocations(root)
        elif item=='adaptive':kernel.verify_selections(root);kernel.run_workers(root,'adaptive',workers);kernel.aggregate_adaptive(root)
        elif item=='analyze':kernel.analyze(root)
        elif item=='export':
            subprocess.run([sys.executable,str(root/'analysis_source/regional_export.py')],check=True)
            verify_completed(root)


def main():
    for name in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']:
        os.environ[name]='1'
    os.environ['MPLBACKEND']='Agg';os.environ['PYTHONDONTWRITEBYTECODE']='1'
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',choices=['check','prepare','benchmark','run','responses','allocations','adaptive','analyze','export'],required=True)
    parser.add_argument('--source-root',type=Path,default=SOURCE_DEFAULT)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--calibration-root',type=Path)
    parser.add_argument('--workers',type=int,default=8)
    args=parser.parse_args()
    if args.workers<1:parser.error('--workers must be positive')
    if args.stage=='check':
        print(json.dumps(verify_source(args.source_root.resolve(),args.calibration_root),indent=2));return
    if args.output is None:parser.error('--output is required for fresh replay stages')
    output=args.output.resolve()
    if args.stage in ['prepare','benchmark','run']:prepare(args.source_root,output)
    if args.stage=='prepare':print('Fresh inputs prepared; no previous predictions copied:',output)
    elif args.stage=='benchmark':benchmark(output)
    else:execute(output,args.stage,args.workers)


if __name__=='__main__':main()
