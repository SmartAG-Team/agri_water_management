"""Calibration-only transport experiment with conserved water and crop cards."""
from pathlib import Path
from copy import deepcopy
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime,timezone
import gzip,json,sys,time
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT/'source_snapshots/native'))
sys.path.insert(0,str(ROOT/'analysis_source'))
from research.ncp_irrigation.model import simulate_season
from core.hydrology.types import SoilLayerParameters
from core.hydrology.matric import theta_from_head
from calibration_core import window_predictions,variable_metrics
from retention_priors import reconstruct

CONTROLS=['bucket','legacy_matric','corrected_matric','corrected_curve_thresholds']


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')


def adjust(payload,control):
    p=deepcopy(payload)
    if control!='bucket':
        p['parameters']['hydrology']['drainage_method']='matric_gradient'
        p['parameters']['hydrology']['matric_interface_method']='harmonic_nodal' if control=='legacy_matric' else 'relative_arithmetic'
        for inputs in [p['inputs']]+([p['presowing']['inputs']] if p.get('presowing') else []):
            inputs['soil_layers']=[reconstruct(row) for row in inputs['soil_layers']]
    if control=='corrected_curve_thresholds':
        for inputs in [p['inputs']]+([p['presowing']['inputs']] if p.get('presowing') else []):
            for row in inputs['soil_layers']:
                layer=SoilLayerParameters(**row)
                row['field_capacity']=theta_from_head(-3300.,layer)
                row['wilting_point']=theta_from_head(-150000.,layer)
                assert row['air_dry']<row['wilting_point']<row['field_capacity']<row['saturation']
    if p.get('presowing'):p['presowing']['parameters']=deepcopy(p['parameters'])
    return p


def task(item):
    kind,filename,control=item;case=filename.removesuffix('_crop.json') if kind=='field' else filename.removesuffix('.json')
    payload=json.loads((ROOT/'inputs'/kind/filename).read_text())
    started=time.monotonic();initial=None;pre=None
    try:
        p=adjust(payload,control)
        write(ROOT/'inputs/resolved'/control/kind/filename,p)
        if p.get('presowing'):
            pre=simulate_season(p['presowing']['inputs'],p['parameters']);initial=pre.final_state
        result=simulate_season(p['inputs'],p['parameters'],initial)
    except (ArithmeticError,ValueError,AssertionError) as error:
        return dict(case_id=case,version=control,kind=kind,success=False,error=str(error),seconds=time.monotonic()-started),pd.DataFrame(),pd.DataFrame()
    daily=pd.DataFrame(result.daily);full=pd.concat([pd.DataFrame(pre.daily),daily],ignore_index=True) if pre else daily
    assert full.balance_residual_mm.abs().max()<1e-6 and full.crop_carbon_residual_kg_ha.abs().max()<1e-6
    assert np.allclose(daily.et_mm,daily.transpiration_mm+daily.soil_evaporation_mm+daily.canopy_evaporation_mm,atol=1e-8,rtol=0)
    out=ROOT/'predictions/full_daily'/control/kind/(case+'.json.gz');out.parent.mkdir(parents=True,exist_ok=True)
    with gzip.open(out,'wt',encoding='utf-8') as stream:json.dump(result.daily,stream,ensure_ascii=False)
    summary=dict(case_id=case,version=control,kind=kind,crop=p['inputs']['crop'],success=True,
        predicted_et_mm=result.summary['et_mm'],predicted_biomass_kg_ha=result.summary['biomass_kg_ha'],
        grain_13pct_kg_ha=result.summary['yield_kg_ha']/.87,final_storage_mm=result.summary['final_storage_mm'],
        germination_start_date=result.summary.get('germination_start_date'),anthesis_date=result.summary['anthesis_date'],
        maturity_date=result.summary['maturity_date'],root_max_mm=float(daily.root_depth_mm.max()),
        soil_evaporation_mm=float(daily.soil_evaporation_mm.sum()),transpiration_mm=float(daily.transpiration_mm.sum()),
        canopy_evaporation_mm=float(daily.canopy_evaporation_mm.sum()),
        drainage_mm=float(daily.bottom_drainage_mm.sum()),runoff_mm=float(daily.runoff_mm.sum()),
        capillary_rise_mm=float(daily.capillary_rise_mm.sum()),
        balance_residual_max_mm=float(full.balance_residual_mm.abs().max()),
        carbon_residual_max_kg_ha=float(full.crop_carbon_residual_kg_ha.abs().max()),seconds=time.monotonic()-started)
    obs=pd.DataFrame()
    if kind=='field':
        source=pd.read_csv(ROOT/'data/previous_field_comparisons.csv')
        r=source[source.version.eq('bucket')&source.case_id.eq(case)].iloc[0]
        for key in ['site','split','source_group_id','harvest_year','treatment','observed_et_mm',
            'yield_13pct_kg_ha','observed_biomass_kg_ha']:summary[key]=r[key]
        summary['water_balance_proxy_mm']=summary['predicted_et_mm']+summary['drainage_mm']+summary['runoff_mm']-summary['capillary_rise_mm']
    else:
        r=pd.read_csv(ROOT/'data/case_inventory.csv');r=r[r.case_id.eq(case)].iloc[0]
        for key in ['site','split','source_group_id']:summary[key]=r[key]
        obs=pd.read_csv(ROOT/'data/observations.csv',low_memory=False)
        obs=obs[obs.case_id.eq(case)].copy();assert obs.split.eq('calibration').all()
        obs['predicted']=window_predictions(result.daily,obs);obs['version']=control
    scalar=daily.drop(columns=[c for c in daily if daily[c].apply(lambda x:isinstance(x,(list,dict))).any()])
    return summary,obs,scalar.assign(case_id=case,version=control,site=summary['site'],split='calibration')


def score(o,p):
    o,p=np.asarray(o,float),np.asarray(p,float);mse=float(np.mean((p-o)**2));variance=float(np.var(o))
    return dict(n=len(o),rmse=mse**.5,bias=float((p-o).mean()),nrmse_percent=100*mse**.5/o.mean(),
        nse=1-mse/variance if variance>1e-12 else None,
        r_squared=float(np.corrcoef(o,p)[0,1]**2) if len(o)>2 and variance*np.var(p)>1e-12 else None)


def main():
    if (ROOT/'verification/artifact_manifest.json').exists():raise RuntimeError('Completed run preserved.')
    obs=pd.read_csv(ROOT/'data/observations.csv',low_memory=False);obs=obs[obs.split.eq('calibration')]
    fields=pd.read_csv(ROOT/'data/previous_field_comparisons.csv')
    fields=fields[fields.version.eq('bucket')&fields.split.eq('calibration')]
    assert obs.case_id.nunique()==71 and len(obs)==3771 and obs.site.nunique()==5 and len(fields)==16
    write(ROOT/'protocol.json',dict(experiment='Calibration-only corrected matric interface assessment',controls=CONTROLS,
        calibration_only=True,testing_used=False,parameters_fitted=False,native_changed=True,
        field_calibration_years=[2016,2017],station_cases=71,field_cases=16,station_records=3771,
        all_five_eligible_station_sites_retained=True,whole_site_years_preserved=True,
        root_depth_traits_irrigation_weather_initial_bulk_storage_tillage_mulch_and_crop_cards_fixed=True,
        matric_curve_threshold_heads_mm=dict(field_capacity=-3300.,wilting_point=-150000.),
        matric_retention_curve_priors_independent_but_unmeasured=True,
        missing_retention_curves_reconstructed_from_supplied_FC_WP=True,
        missing_curve_pressure_head_assumptions_mm=[-3300.,-150000.],
        field_biomass_basis_confirmed=False,source_field_ET_assumes_negligible_drainage_and_runoff=True,
        water_balance_proxy_kept_separate_from_actual_ET=True,regional_promoted=False))
    obs.to_csv(ROOT/'data/used_calibration_observations.csv',index=False)
    fields.to_csv(ROOT/'data/used_field_calibration_observations.csv',index=False)
    tasks=[('station',case+'.json',control) for control in CONTROLS for case in sorted(obs.case_id.unique())]
    tasks += [('field',case+'_crop.json',control) for control in CONTROLS for case in sorted(fields.case_id)]
    results=[]
    with ProcessPoolExecutor(max_workers=4) as pool:
        for i,result in enumerate(pool.map(task,tasks,chunksize=1),1):
            results.append(result)
            if i%32==0:print('Evaluated',i,'of',len(tasks),'calibration cases',flush=True)
    summaries=pd.DataFrame([r[0] for r in results]);summaries.to_csv(ROOT/'predictions/case_summaries.csv',index=False)
    comparisons=pd.concat([r[1] for r in results if len(r[1])],ignore_index=True)
    comparisons.to_csv(ROOT/'predictions/station_comparisons.csv',index=False)
    daily=pd.concat([r[2] for r in results if len(r[2])],ignore_index=True);daily.to_csv(ROOT/'predictions/daily_predictions.csv.gz',index=False)
    field=summaries[summaries.kind.eq('field')];field.to_csv(ROOT/'predictions/field_comparisons.csv',index=False)
    summaries[~summaries.success].to_csv(ROOT/'tables/process_failures.csv',index=False)
    pd.concat([variable_metrics(g).assign(version=v) for v,g in comparisons.groupby('version')]).to_csv(ROOT/'tables/station_metrics.csv',index=False)
    pd.concat([variable_metrics(g).assign(version=v,site=s) for (v,s),g in comparisons.groupby(['version','site'])]).to_csv(ROOT/'tables/site_metrics.csv',index=False)
    rows=[];contrasts=[]
    for (version,crop),g in field[field.success].groupby(['version','crop']):
        for variable,o,p in [('actual_ET','observed_et_mm','predicted_et_mm'),
            ('water_balance_proxy','observed_et_mm','water_balance_proxy_mm'),
            ('yield','yield_13pct_kg_ha','grain_13pct_kg_ha'),('biomass','observed_biomass_kg_ha','predicted_biomass_kg_ha')]:
            rows.append(dict(version=version,crop=crop,split='calibration',variable=variable,**score(g[o],g[p])))
        for year,y in g.groupby('harvest_year'):
            if not {'W0','W3'}<=set(y.treatment):continue
            a,b=y[y.treatment.eq('W0')].iloc[0],y[y.treatment.eq('W3')].iloc[0]
            contrasts.append(dict(version=version,crop=crop,harvest_year=year,observed_mm=b.observed_et_mm-a.observed_et_mm,
                predicted_mm=b.predicted_et_mm-a.predicted_et_mm))
    pd.DataFrame(rows).to_csv(ROOT/'tables/field_metrics.csv',index=False)
    pd.DataFrame(contrasts).to_csv(ROOT/'tables/field_ET_contrasts.csv',index=False)
    baseline=field[field.version.eq('bucket')].set_index('case_id')
    reference=fields.set_index('case_id')
    for col in ['predicted_et_mm','predicted_biomass_kg_ha','grain_13pct_kg_ha']:
        assert np.allclose(baseline[col],reference.loc[baseline.index,col],atol=1e-8,rtol=0)
    previous=pd.read_csv(ROOT/'data/previous_station_comparisons.csv',low_memory=False)
    previous=previous[previous.version.eq('bucket')].set_index('observation_id')
    baseline=comparisons[comparisons.version.eq('bucket')].set_index('observation_id')
    assert np.allclose(baseline.predicted,previous.loc[baseline.index,'predicted'],atol=1e-8,rtol=0)
    oldfield=pd.read_csv(ROOT/'data/previous_field_comparisons.csv')
    oldfield=oldfield[oldfield.version.eq('matric_prior')].set_index('case_id')
    replay=field[field.version.eq('legacy_matric')].set_index('case_id')
    for col in ['predicted_et_mm','predicted_biomass_kg_ha','grain_13pct_kg_ha']:
        assert np.allclose(replay[col],oldfield.loc[replay.index,col],atol=1e-8,rtol=0)
    oldstation=pd.read_csv(ROOT/'data/previous_station_comparisons.csv',low_memory=False)
    oldstation=oldstation[oldstation.version.eq('matric_prior')].set_index('observation_id')
    replay=comparisons[comparisons.version.eq('legacy_matric')].set_index('observation_id')
    assert np.allclose(replay.predicted,oldstation.loc[replay.index,'predicted'],atol=1e-8,rtol=0)
    write(ROOT/'verification/run_receipt.json',dict(completed_at_utc=datetime.now(timezone.utc).isoformat(),
        attempted_simulations=len(tasks),successful_simulations=int(summaries.success.sum()),
        failed_simulations=int((~summaries.success).sum()),parameters_fitted=False,testing_used=False,
        baseline_replayed_exactly=True,legacy_matric_replayed_exactly=True,observations_excluded_by_residual=False,regional_promoted=False))
    print(pd.DataFrame(rows).round(3).to_string(index=False),flush=True)


if __name__=='__main__':main()
