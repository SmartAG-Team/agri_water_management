"""Export current-parameter publication evidence and separately retain historical audits."""
from pathlib import Path
from copy import deepcopy
import hashlib
import csv
import gzip
import json
import shutil

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection
from matplotlib.colors import Normalize, TwoSlopeNorm, BoundaryNorm
import numpy as np
import pandas as pd
from pyproj import Transformer

import regional_recalculation as driver

ROOT = Path(__file__).resolve().parents[1]/'regional'
BLUE = '#2166ac'
ORANGE = '#d6604d'


def save(fig, name):
    driver.close_figure_axes(fig)
    for extension in ['png', 'pdf']:
        fig.savefig(ROOT/f'figures/{name}.{extension}', dpi=350, facecolor='white')
    plt.close(fig)


def raw_forcing():
    weather = pd.read_csv(ROOT/'data/used_daily_weather.csv.gz')
    weather['year'] = pd.to_datetime(weather.date).dt.year
    weather['mean_temperature_c'] = (weather.tmin_c+weather.tmax_c)/2
    units = weather.groupby(['point', 'year'], as_index=False).agg(
        precipitation_mm=('precipitation_mm', 'sum'), reference_et0_mm=('reference_et0_mm', 'sum'),
        mean_temperature_c=('mean_temperature_c', 'mean'), n_days=('date', 'size'))
    reps = pd.read_csv(ROOT/'data/representative_cells.csv')
    units = units.merge(reps[['representative_id', 'represented_area_ha']], left_on='point', right_on='representative_id', validate='many_to_one')
    units.to_csv(ROOT/'tables/raw_forcing_by_representative_year.csv', index=False)
    regional = []
    for year, frame in units.groupby('year'):
        regional.append(dict(year=int(year), represented_area_ha=float(frame.represented_area_ha.sum()),
            **{variable: float(np.average(frame[variable], weights=frame.represented_area_ha))
               for variable in ['precipitation_mm', 'reference_et0_mm', 'mean_temperature_c']}))
    regional = pd.DataFrame(regional);regional.to_csv(ROOT/'tables/raw_forcing_regional_annual.csv', index=False)
    fig, axes = plt.subplots(3, 1, figsize=(8.5, 7.3), sharex=True, layout='constrained')
    for letter, ax, variable, unit, color in zip('abc', axes,
            ['precipitation_mm', 'reference_et0_mm', 'mean_temperature_c'],
            ['Precipitation (mm yr⁻¹)', 'Reference ET₀ (mm yr⁻¹)', 'Mean air temperature (°C)'],
            [BLUE, ORANGE, '#555555']):
        ax.plot(regional.year, regional[variable], marker='o', ms=3, color=color)
        ax.set_ylabel(unit);ax.set_xlim(1996, 2025);ax.set_xticks([1996, 2002, 2008, 2014, 2020, 2025])
        ax.axvline(2013.5, color='#777777', ls='--', lw=.8)
        ax.text(.015,.95,f'({letter})',transform=ax.transAxes,va='top',weight='bold')
    axes[0].set_title('Daily forcing: 32 area-weighted representative units')
    axes[-1].set_xlabel('Calendar year')
    save(fig, 'raw_regional_forcing')
    start, end = driver.calendar()[0][1], driver.calendar()[-1][2]
    used = weather[weather.date.between(start, end)].drop(columns=['year', 'mean_temperature_c'])
    # Select unchanged CSV record text so the exact consumed subset retains all
    # source decimal precision as well as the original full forcing snapshot.
    with gzip.open(ROOT/'data/used_daily_weather.csv.gz','rt',newline='') as source, \
            gzip.open(ROOT/'data/exact_simulated_daily_weather.csv.gz','wt',newline='') as destination:
        header=source.readline();destination.write(header)
        date_index=next(csv.reader([header])).index('date')
        for line in source:
            day=next(csv.reader([line]))[date_index]
            if start<=day<=end:destination.write(line)
    return dict(archived_point_days=len(weather), archived_date_range=[weather.date.min(),weather.date.max()],
        actually_simulated_point_days=len(used), actually_simulated_date_range=[start,end],
        archived_weather_sha256=driver.sha(ROOT/'data/used_daily_weather.csv.gz'),
        actual_used_weather_sha256=driver.sha(ROOT/'data/exact_simulated_daily_weather.csv.gz'),
        actual_used_subset_retains_source_record_text=True,
        complete_calendar_archive_in_raw_figure=True, missing_forcing_values=int(used.isna().sum().sum()))


def current_tables():
    audit = ROOT/'verification/historical_parameter_comparison_audit';audit.mkdir(exist_ok=True)
    mixed = ['all_parameter_policy_years', 'policy_period_means', 'policy_contrasts', 'contrast_summary',
             'spatial_unit_annual_results', 'spatial_unit_period_means', 'source_cell_map_values', 'spatial_latitude_bands']
    for name in mixed+['allocation_changes','allocation_turnover']:
        source = ROOT/f'tables/{name}.csv'
        archived = audit/source.name
        if not archived.exists():shutil.copy2(source, archived)
        if name not in mixed:
            if source.exists():source.unlink()
            continue
        frame = pd.read_csv(archived)
        if 'parameter_set' in frame:
            frame = frame[frame.parameter_set.eq('source_screened')].copy()
        if 'policy' in frame:
            frame = frame[~frame.policy.str.startswith('archived_targeted')].copy()
        frame = frame.drop(columns=[column for column in frame if column.startswith('archived_') or column in
            ['screened_minus_archived_quota', 'minimum_changed_share']], errors='ignore')
        frame.to_csv(source,index=False)
    for stem in ['regional_policy_parameter_sensitivity','Figure_S25_spatial_parameter_comparison','Figure_5_spatial_policy_outcomes']:
        for extension in ['png','pdf']:
            source = ROOT/f'figures/{stem}.{extension}'
            if source.exists():shutil.move(str(source), str(audit/source.name))
    old_workbook = ROOT/'tables/revised_regional_results.xlsx'
    if old_workbook.exists():shutil.move(str(old_workbook), str(audit/old_workbook.name))
    units = pd.read_csv(ROOT/'tables/spatial_unit_period_means.csv')
    units.to_csv(ROOT/'tables/current_spatial_unit_period_means.csv',index=False)
    cells = pd.read_csv(ROOT/'tables/source_cell_map_values.csv')
    cells.to_csv(ROOT/'tables/current_source_cell_map_values.csv',index=False)
    old_geo = ROOT/'data/spatial_policy_cells.geojson'
    if old_geo.exists() and not (audit/old_geo.name).exists():shutil.move(str(old_geo),str(audit/old_geo.name))
    features=[]
    keep=['zone_id','representative_id','used_area_ha','source_screened_targeted_mean_quota',
        'source_screened_delta_yield_kg_ha','source_screened_delta_modeled_total_et_mm','source_screened_grain_gain_years']
    for row in cells.to_dict('records'):
        x,y=row['longitude'],row['latitude']
        ring=[[x-.05,y-.05],[x+.05,y-.05],[x+.05,y+.05],[x-.05,y+.05],[x-.05,y-.05]]
        properties={key:row[key] for key in keep};properties['uniform_quota_fraction']=.5
        features.append(dict(type='Feature',geometry=dict(type='Polygon',coordinates=[ring]),properties=properties))
    driver.write_json(old_geo,dict(type='FeatureCollection',features=features))
    old_figure_manifest=ROOT/'verification/figure_exports.json'
    if old_figure_manifest.exists() and not (audit/old_figure_manifest.name).exists():
        shutil.move(str(old_figure_manifest),str(audit/old_figure_manifest.name))
    bands = pd.read_csv(ROOT/'tables/spatial_latitude_bands.csv')
    bands.to_csv(ROOT/'tables/current_spatial_latitude_bands.csv',index=False)
    driver.write_json(ROOT/'verification/table_identifier_semantics.json',dict(
        source_screened='compatibility identifier for the selected 7 October management_refit engine and effective shared cards',
        refit_targeted='current-parameter allocation selected only from 1997–2013',
        primary_current_annual_table='tables/regional_policy_annual_results.csv',
        primary_current_adaptive_table='tables/policy_comparison.csv',
        historical_tables_directory=str(audit.relative_to(ROOT)),
        active_publication_tables_contain_only_current_parameters=True))
    return cells,bands


def spatial_figure(cells,bands):
    transform = Transformer.from_crs('EPSG:4326','EPSG:6933',always_xy=True)
    vertices=[]
    for row in cells.itertuples():
        x,y=transform.transform([row.longitude-.05,row.longitude+.05,row.longitude+.05,row.longitude-.05],
            [row.latitude-.05,row.latitude-.05,row.latitude+.05,row.latitude+.05])
        vertices.append(np.column_stack([x,y])/1000)
    fig,axes=plt.subplots(2,3,figsize=(10.5,8.8),layout='constrained')
    columns=['source_screened_targeted_mean_quota','uniform_quota_fraction',
        'source_screened_delta_yield_kg_ha','source_screened_delta_modeled_total_et_mm','source_screened_grain_gain_years']
    cells=cells.copy();cells['uniform_quota_fraction']=.5
    titles=['Targeted mean quota','Uniform quota','Grain difference','ET difference','Positive grain years']
    units=['Fraction of conventional quota','Fraction of conventional quota','t ha⁻¹ yr⁻¹','mm yr⁻¹','Years (of 12)']
    for ax,letter,column,title,unit in zip(axes.flat,'abcde',columns,titles,units):
        values=cells[column].to_numpy(dtype=float,copy=True)
        if column.endswith('yield_kg_ha'):values/=1000
        if column.endswith('quota') or column=='uniform_quota_fraction':norm=Normalize(0,1);cmap='cividis'
        elif column.endswith('grain_gain_years'):norm=BoundaryNorm(np.arange(-.5,13.5),256);cmap='cividis'
        else:
            limit=max(float(np.max(np.abs(values))),1e-9);norm=TwoSlopeNorm(vmin=-limit,vcenter=0,vmax=limit);cmap='RdBu'
        collection=PolyCollection(vertices,array=values,cmap=cmap,norm=norm,edgecolors='none',rasterized=True)
        ax.add_collection(collection)
        ax.set_facecolor('#f0f0f0')
        xx,yy=transform.transform([112,121.5],[32,41]);ax.set_xlim(xx[0]/1000,xx[1]/1000);ax.set_ylim(yy[0]/1000,yy[1]/1000)
        lons,lats=[113,116,119],[33,36,39]
        x,_=transform.transform(lons,[36]*3);_,y=transform.transform([116]*3,lats)
        ax.set_xticks(np.array(x)/1000,[f'{v}°E' for v in lons]);ax.set_yticks(np.array(y)/1000,[f'{v}°N' for v in lats])
        ax.set_aspect('equal');ax.set_title(f'({letter}) {title}')
        colorbar=fig.colorbar(collection,ax=ax,orientation='horizontal',pad=.04,shrink=.95,aspect=22)
        colorbar.set_label(unit)
        if column.endswith('grain_gain_years'):colorbar.set_ticks([0,3,6,9,12])
    ax=axes.flat[5]
    wanted=['Southern (32–36°N)','Central (36–38°N)','Northern (38–41°N)']
    frame=bands.set_index('latitude_band').loc[wanted]
    values=frame.source_screened_delta_yield_kg_ha/1000
    ax.barh(['Southern','Central','Northern'],values,color=BLUE,edgecolor='#333333',linewidth=.4)
    ax.axvline(0,color='#555555',lw=.8);ax.set_xlabel('Grain difference (t ha⁻¹ yr⁻¹)')
    ax.set_title('(f) Area-weighted latitude bands');ax.invert_yaxis()
    save(fig,'Figure_7_current_spatial_policy_outcomes')
    cells.to_csv(ROOT/'tables/Figure_7_values.csv',index=False)


def policy_figure():
    annual=pd.read_csv(ROOT/'tables/regional_policy_annual_results.csv')
    frame=annual[annual.period.eq('testing')].copy()
    frame.to_csv(ROOT/'tables/Figure_3_current_values.csv',index=False)
    fig,axes=plt.subplots(2,2,figsize=(9.2,7.4),layout='constrained')
    for policy,color,marker,label in [('uniform',ORANGE,'s','Uniform'),('targeted',BLUE,'o','Targeted')]:
        rows=[]
        for cut in [0,25,50,75]:
            name='conventional' if cut==0 else f'{policy}_{cut}pct'
            group=frame[frame.policy.eq(name)]
            rows.append(dict(cut=cut,grain_mean=group.grain_production_t.mean()/1e6,
                grain_sd=group.grain_production_t.std(ddof=1)/1e6,
                et_mean=group.modeled_total_et_volume_m3.mean()/1e9,
                et_sd=group.modeled_total_et_volume_m3.std(ddof=1)/1e9))
        values=pd.DataFrame(rows)
        axes[0,0].errorbar(values.cut,values.grain_mean,yerr=values.grain_sd,color=color,marker=marker,capsize=3,label=label)
        axes[0,1].errorbar(values.cut,values.et_mean,yerr=values.et_sd,color=color,marker=marker,capsize=3,label=label)
        for cut in [25,50,75]:
            group=frame[frame.policy.eq(f'{policy}_{cut}pct')]
            x,y=group.modeled_total_et_volume_m3/1e9,group.grain_production_t/1e6
            axes[1,1].scatter(x,y,color=color,marker=marker,s=18,alpha=.3)
            axes[1,1].scatter(x.mean(),y.mean(),color=color,marker=marker,s=60,edgecolors='white')
    axes[0,0].set_ylabel('Annual dry grain (million t)');axes[0,0].legend(frameon=False)
    axes[0,1].set_ylabel('Crop + fallow ET (km³ yr⁻¹)')
    for ax in axes[0]:ax.set_xlabel('Field irrigation reduction (%)');ax.set_xticks([0,25,50,75])
    for cut,color,marker in [(25,BLUE,'o'),(50,ORANGE,'s'),(75,'#555555','^')]:
        targeted=frame[frame.policy.eq(f'targeted_{cut}pct')].set_index('harvest_year')
        uniform=frame[frame.policy.eq(f'uniform_{cut}pct')].set_index('harvest_year')
        axes[1,0].plot(targeted.index,(targeted.grain_production_t-uniform.grain_production_t)/1e6,
            color=color,marker=marker,ms=3,label=f'{cut}% cut')
    axes[1,0].axhline(0,color='#777777',lw=.8)
    axes[1,0].set(xlabel='Evaluation harvest year',ylabel='Targeted − uniform grain (million t)',xticks=[2014,2018,2022,2025])
    axes[1,0].legend(frameon=False,ncol=3,fontsize=11,loc='upper center',bbox_to_anchor=(.5,1.13))
    axes[1,1].set(xlabel='Crop + fallow ET (km³ yr⁻¹)',ylabel='Annual dry grain (million t)')
    for letter,ax in zip('abcd',axes.flat):ax.text(.015,.98,f'({letter})',transform=ax.transAxes,va='top',weight='bold')
    save(fig,'Figure_3_policy_tradeoffs')
    driver.write_json(ROOT/'verification/current_figure_scope.json',dict(
        Figure_3=dict(parameters='selected management_refit',calendar_years=list(range(2014,2026)),
            conventional_baseline='current parameters at full scheduled irrigation',
            error_bars='standard deviation across twelve conditional weather-year scenarios; not confidence intervals',
            allocation_selected_in=list(range(1997,2014))),
        Figure_7=dict(reduction_fraction=.5,parameters='selected management_refit',
            comparator='uniform current-parameter irrigation at the same regional water budget',
            grain_unit='dry grain, kg ha-1 before plotting conversion to tonnes',
            source_cell_representation='static mapped support with representative-unit mixtures; no field-level allocation inference')))


def verify_current_metrics():
    annual=pd.read_csv(ROOT/'tables/regional_policy_annual_results.csv')
    contrasts=pd.read_csv(ROOT/'tables/policy_contrasts.csv')
    fixed=pd.read_csv(ROOT/'predictions/all_season_summaries.csv')
    rotations=pd.read_csv(ROOT/'predictions/rotation_summaries.csv')
    adaptive=pd.read_csv(ROOT/'tables/policy_comparison.csv')
    adaptive_annual=pd.read_csv(ROOT/'tables/adaptive_regional_policy_annual.csv')
    decisions=pd.read_csv(ROOT/'data/annual_policy_decisions.csv')
    matched_checks={}
    for target in ['95','98']:
        left=decisions[decisions.policy.eq('adaptive_'+target)].sort_values(['representative_id','harvest_year'])
        right=decisions[decisions.policy.eq('rain_'+target+'_matched')].sort_values(['representative_id','harvest_year'])
        same_decisions=bool(np.array_equal(left.quota_fraction.to_numpy(),right.quota_fraction.to_numpy()))
        a=adaptive_annual[adaptive_annual.policy.eq('adaptive_'+target)].set_index('harvest_year').sort_index()
        b=adaptive_annual[adaptive_annual.policy.eq('rain_'+target+'_matched')].set_index('harvest_year').sort_index()
        variables=['grain_production_t','irrigation_mm','modeled_total_et_mm','annual_bottom_drainage_mm']
        differences={variable:float((a[variable]-b[variable]).abs().max()) for variable in variables}
        matched_checks[target]=dict(decisions_identical=same_decisions,n_decisions=len(left),maximum_output_differences=differences,
            incremental_storage_information_benefit_identified=False)
        if same_decisions and max(differences.values())>1e-7:raise AssertionError('Identical strategies produced different outcomes')
    driver.write_json(ROOT/'verification/matched_information_controls.json',matched_checks)
    cards=json.loads((ROOT/'parameters/used_crop_parameters.json').read_text())
    hydrology=json.loads((ROOT/'parameters/used_hydrology_by_crop.json').read_text())
    exact_parameters=True
    for crop in ['wheat','maize']:
        selected=json.loads((ROOT/f'source_snapshots/selected_resolved_field/yang2024_{crop}_2015_2016_W0_crop.json').read_text())['parameters']
        exact_parameters &= selected==dict(crop=cards[crop],hydrology=hydrology[crop])
    transfer_path=ROOT/'verification/parameter_transfer.json'
    transfer=json.loads(transfer_path.read_text())
    transfer['canonical_effective_parameters_sha256']={crop:hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
        for crop,value in transfer['effective_parameters'].items()}
    driver.write_json(transfer_path,transfer)
    checks=dict(complete_current_policy_years=len(annual)==7*29,
        exact_selected_field_parameters=exact_parameters,
        correct_current_policy_names=set(annual.policy)=={'conventional','uniform_25pct','uniform_50pct','uniform_75pct',
            'targeted_25pct','targeted_50pct','targeted_75pct'},
        no_historical_parameter_rows=contrasts.parameter_set.eq('source_screened').all(),
        exact_fixed_segments=len(fixed)==32*5*116,
        exact_rotation_responses=len(rotations)==32*5*29,
        equal_water_budget=float(contrasts.field_water_difference_m3.abs().max())<.01,
        exact_adaptive_evaluation_scopes=set(adaptive.scope)=={'common_GRACE_available','all_testing_years'},
        exact_scope_lengths=adaptive.groupby('scope').n_years.first().to_dict()=={'all_testing_years':12,'common_GRACE_available':9})
    if not all(checks.values()):raise AssertionError(checks)
    receipt=dict(all_checks_passed=True,checks={name:bool(value) for name,value in checks.items()},
        represented_area_ha=float(pd.read_csv(ROOT/'data/representative_cells.csv').represented_area_ha.sum()),
        maximum_current_equal_water_difference_m3=float(contrasts.field_water_difference_m3.abs().max()),
        maximum_current_water_residual_mm=float(fixed.maximum_daily_residual_mm.max()),
        maximum_current_carbon_residual_kg_ha=float(fixed.maximum_carbon_residual_kg_ha.max()),
        maximum_storage_discontinuity_mm=float(fixed.storage_continuity_error_mm.abs().max()),
        results_classification='conditional regional scenarios; model numerical consistency and aggregation checks',
        retrospective_GRACE_information=True, operational_release_before_decisions_confirmed=False)
    driver.write_json(ROOT/'verification/current_publication_checks.json',receipt)
    return receipt


def main():
    driver.verify_run(ROOT)
    if not (ROOT/'verification/completion.json').exists():raise ValueError('Complete the exact regional simulations first')
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,'pdf.fonttype':42,'axes.linewidth':.7})
    weather=raw_forcing()
    cells,bands=current_tables()
    policy_figure()
    spatial_figure(cells,bands)
    checks=verify_current_metrics()
    driver.write_json(ROOT/'verification/figure_exports.json',{
        str(path.relative_to(ROOT)):dict(sha256=driver.sha(path),format=path.suffix[1:].upper(),current_parameters_only=True)
        for path in sorted((ROOT/'figures').glob('*')) if path.suffix in ['.png','.pdf']})
    tables=['regional_policy_annual_results','conditional_policy_testing_means','policy_period_means','contrast_summary',
        'policy_contrasts','current_spatial_unit_period_means','current_spatial_latitude_bands','policy_comparison',
        'adaptive_regional_policy_annual','training_selected_quotas','training_selection_candidates','full_quota_spatial_metrics',
        'management_response_metrics','class_quota_responses','class_quota_yearly_responses','selected_quota_testing_evaluation',
        'class_counts_and_area_exposure','raw_forcing_regional_annual']
    with pd.ExcelWriter(ROOT/'tables/current_regional_results.xlsx',engine='openpyxl') as writer:
        for name in tables:pd.read_csv(ROOT/f'tables/{name}.csv').to_excel(writer,sheet_name=name[:31],index=False)
        pd.read_csv(ROOT/'parameters/frozen_training_allocation.csv').to_excel(writer,sheet_name='Frozen_spatial_allocation',index=False)
    for name in ['regional_benchmark.py','regional_pipeline.py','regional_export.py']:
        shutil.copy2(Path(__file__).parent/name,ROOT/'source_snapshots'/name)
    driver.write_json(ROOT/'verification/publication_export_receipt.json',dict(
        all_checks_passed=True,raw_weather=weather,current_metrics=checks,
        current_only_publication_figures=True,raw_data_and_scenario_figures_retained=True,
        figure_export_formats=['PNG','PDF'],spatial_map_projection='EPSG:6933',source_cell_interpolation=False))
    provisional=ROOT/'verification/fixed_response_progress.json'
    if provisional.exists():provisional.unlink()
    completion=json.loads((ROOT/'verification/completion.json').read_text())
    completion.update(current_only_publication_exports=True,
        maximum_equal_water_difference_m3=checks['maximum_current_equal_water_difference_m3'],
        native_process_changed=False,native_model_modified_for_this_regional_run=False,
        native_engine_matches_selected_field_run=True,engine_differs_from_archived_regional_source=True)
    driver.write_json(ROOT/'verification/completion.json',completion)
    spatial_path=ROOT/'verification/spatial_check_completion.json'
    spatial=json.loads(spatial_path.read_text())
    spatial.update(native_process_changed=False,native_model_modified_for_this_regional_run=False,
        native_engine_matches_selected_field_run=True,engine_differs_from_archived_regional_source=True)
    driver.write_json(spatial_path,spatial)
    excluded={'file_manifest.json','pipeline_state.json'}
    manifest={str(path.relative_to(ROOT)):dict(sha256=driver.sha(path),bytes=path.stat().st_size)
        for path in sorted(ROOT.rglob('*')) if path.is_file() and '__pycache__' not in path.parts
        and path.name not in excluded and path.suffix!='.log'}
    driver.write_json(ROOT/'verification/file_manifest.json',manifest)
    print('Current-only publication tables, raw forcing and spatial PNG/PDF exports verified',flush=True)


if __name__=='__main__':main()
