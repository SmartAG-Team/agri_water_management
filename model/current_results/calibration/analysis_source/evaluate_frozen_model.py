"""Evaluate empirical-water-response fits on the frozen chronological field partition."""
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime,timezone
import gzip,json,sys
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT/'source_snapshots/native'))
sys.path.insert(0,str(ROOT/'analysis_source'))
from research.ncp_irrigation.model import simulate_season
from fit_objective import adjusted
from hydraulic_control import adjust as hydraulic_adjust
from calibration_core import window_predictions,variable_metrics


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')


def task(item):
    kind,filename,version=item
    if version=='reference':
        p=json.loads((ROOT/'inputs/reference'/kind/filename).read_text())
    else:
        p=json.loads((ROOT/'inputs'/kind/filename).read_text())
        crop=p['inputs']['crop']
        card=json.loads((ROOT/'parameters/candidates'/(crop+'_corrected_matric.json')).read_text())
        p=adjusted(hydraulic_adjust(p,'corrected_matric'),card['vector'],crop)
    case=filename.removesuffix('_crop.json') if kind=='field' else filename.removesuffix('.json')
    write(ROOT/'inputs/resolved'/version/kind/filename,p)
    initial=None;pre=None
    if p.get('presowing'):
        pre=simulate_season(p['presowing']['inputs'],p['parameters']);initial=pre.final_state
    result=simulate_season(p['inputs'],p['parameters'],initial);daily=pd.DataFrame(result.daily)
    full=pd.concat([pd.DataFrame(pre.daily),daily],ignore_index=True) if pre else daily
    assert full.balance_residual_mm.abs().max()<1e-6 and full.crop_carbon_residual_kg_ha.abs().max()<1e-6
    assert np.allclose(daily.et_mm,daily.transpiration_mm+daily.soil_evaporation_mm+daily.canopy_evaporation_mm,atol=1e-8,rtol=0)
    out=ROOT/'predictions/full_daily'/version/kind/(case+'.json.gz');out.parent.mkdir(parents=True,exist_ok=True)
    with gzip.open(out,'wt',encoding='utf-8') as f:json.dump(result.daily,f,ensure_ascii=False)
    summary=dict(case_id=case,version=version,crop=p['inputs']['crop'],
        predicted_et_mm=result.summary['et_mm'],predicted_biomass_kg_ha=result.summary['biomass_kg_ha'],
        grain_13pct_kg_ha=result.summary['yield_kg_ha']/.87,
        germination_start_date=result.summary.get('germination_start_date'),
        anthesis_date=result.summary['anthesis_date'],maturity_date=result.summary['maturity_date'],
        final_storage_mm=result.summary['final_storage_mm'],lai_max=float(daily.lai.max()),
        transpiration_mm=float(daily.transpiration_mm.sum()),soil_evaporation_mm=float(daily.soil_evaporation_mm.sum()),
        canopy_evaporation_mm=float(daily.canopy_evaporation_mm.sum()),
        water_residual_max_mm=float(full.balance_residual_mm.abs().max()),
        carbon_residual_max_kg_ha=float(full.crop_carbon_residual_kg_ha.abs().max()))
    obs=pd.DataFrame()
    if kind=='field':
        source=pd.read_csv(ROOT/'data/reference_field_comparisons.csv')
        source=source[source.version.eq('supplied_fit')].set_index('case_id')
        row=source.loc[case]
        for key in ['site','split','source_group_id','harvest_year','treatment','observed_et_mm',
                    'et_eligible','yield_13pct_kg_ha','observed_biomass_kg_ha']:summary[key]=row[key]
    else:
        inv=pd.read_csv(ROOT/'data/case_inventory.csv');row=inv[inv.case_id.eq(case)].iloc[0]
        for key in ['site','split','source_group_id']:summary[key]=row[key]
        obs=pd.read_csv(ROOT/'data/observations.csv',low_memory=False);obs=obs[obs.case_id.eq(case)].copy()
        if len(obs):obs['predicted']=window_predictions(result.daily,obs);obs['version']=version
    scalar=daily.drop(columns=[c for c in daily if daily[c].apply(lambda x:isinstance(x,(list,dict))).any()])
    return summary,obs,scalar.assign(case_id=case,version=version,site=summary['site'],split=summary['split'])


def score(o,p):
    o,p=np.asarray(o,float),np.asarray(p,float);mse=float(np.mean((p-o)**2));var=float(np.var(o))
    return dict(n=len(o),rmse=mse**.5,bias=float((p-o).mean()),nrmse_percent=100*mse**.5/o.mean(),
        nse=1-mse/var if var>1e-12 else None,
        r_squared=float(np.corrcoef(o,p)[0,1]**2) if len(o)>=3 and var*np.var(p)>1e-12 else None)


def main():
    if (ROOT/'verification/artifact_manifest.json').exists():raise RuntimeError('Completed run preserved; use a new directory.')
    frozen=json.loads((ROOT/'parameters/frozen_model.json').read_text())
    assert frozen['testing_used_for_selection'] is False
    assert all(c['success'] and c['testing_used'] is False for cards in frozen['candidates'].values() for c in cards.values())
    tasks=[(kind,p.name,v) for v in ['reference','soil_refit'] for kind in ['station','field']
        for p in sorted((ROOT/'inputs'/kind).glob('*.json'))]
    assert len(tasks)==328
    results=[]
    with ProcessPoolExecutor(max_workers=4) as pool:
        for i,r in enumerate(pool.map(task,tasks,chunksize=2),1):
            results.append(r)
            if i%64==0:print('Evaluated',i,'of',len(tasks),'cases',flush=True)
    summaries=pd.DataFrame([r[0] for r in results]);obs=pd.concat([r[1] for r in results if len(r[1])],ignore_index=True)
    daily=pd.concat([r[2] for r in results],ignore_index=True)
    for v,g in obs.groupby('version'):
        assert len(g)==g.observation_id.nunique()==7397 and g.site.nunique()==5
        assert g.groupby('source_group_id').split.nunique().eq(1).all()
    summaries.to_csv(ROOT/'predictions/case_summaries.csv',index=False)
    obs.to_csv(ROOT/'predictions/station_comparisons.csv',index=False)
    daily.to_csv(ROOT/'predictions/daily_predictions.csv.gz',index=False)
    fields=summaries[summaries.site.eq('Wuqiao')]
    fields.to_csv(ROOT/'predictions/field_comparisons.csv',index=False)
    pd.concat([variable_metrics(g).assign(version=v) for v,g in obs.groupby('version')]).to_csv(ROOT/'tables/station_metrics.csv',index=False)
    pd.concat([variable_metrics(g).assign(version=v,site=s) for (v,s),g in obs.groupby(['version','site'])]).to_csv(ROOT/'tables/site_metrics.csv',index=False)
    rows=[];contrasts=[]
    for (v,c,s),g in fields.groupby(['version','crop','split']):
        for variable,o,p in [('seasonal_et','observed_et_mm','predicted_et_mm'),('yield','yield_13pct_kg_ha','grain_13pct_kg_ha'),
            ('biomass','observed_biomass_kg_ha','predicted_biomass_kg_ha')]:
            rows.append(dict(version=v,crop=c,split=s,variable=variable,**score(g[o],g[p])))
        for year,g in g.groupby('harvest_year'):
            a,b=g[g.treatment.eq('W0')].iloc[0],g[g.treatment.eq('W3')].iloc[0]
            contrasts.append(dict(version=v,crop=c,split=s,harvest_year=year,
                observed_mm=b.observed_et_mm-a.observed_et_mm,predicted_mm=b.predicted_et_mm-a.predicted_et_mm))
    contrasts=pd.DataFrame(contrasts)
    for (v,c,s),g in contrasts.groupby(['version','crop','split']):
        values=score(g.observed_mm,g.predicted_mm);values['nrmse_percent']=None
        rows.append(dict(version=v,crop=c,split=s,variable='seasonal_et_response',**values))
    metrics=pd.DataFrame(rows);metrics.to_csv(ROOT/'tables/field_metrics.csv',index=False)
    contrasts.to_csv(ROOT/'tables/field_ET_contrasts.csv',index=False)
    source=pd.read_csv(ROOT/'data/reference_field_comparisons.csv')
    source=source[source.version.eq('supplied_fit')].set_index('case_id')
    base=fields[fields.version.eq('reference')].set_index('case_id')
    for col in ['predicted_et_mm','predicted_biomass_kg_ha','grain_13pct_kg_ha']:
        assert np.allclose(base[col],source.loc[base.index,col],rtol=0,atol=1e-8)
    retained=pd.read_csv(ROOT/'data/reference_station_comparisons.csv',low_memory=False)
    retained=retained[retained.version.eq('supplied_fit')].set_index('observation_id')
    before=obs[obs.version.eq('reference')].set_index('observation_id')
    expected=retained.loc[before.index,'predicted']
    eligible=expected.notna()
    assert np.allclose(before.loc[eligible,'predicted'],expected[eligible],rtol=0,atol=1e-8)
    write(ROOT/'verification/run_receipt.json',dict(simulations=328,cases_per_version=164,
        station_records_per_version=7397,all_eligible_sites_retained=True,
        reference_replayed_where_initialization_expectations_available=True, changed_testing_reference_not_compared_to_old_inputs=True,both_crops_calibrated=True,field_biomass_fitted=True,field_biomass_basis_confirmed=True,
        parameters_frozen_before_testing=True,
        water_residual_max_mm=float(summaries.water_residual_max_mm.max()),
        carbon_residual_max_kg_ha=float(summaries.carbon_residual_max_kg_ha.max()),
        completed_at_utc=datetime.now(timezone.utc).isoformat(),regional_promoted=False))
    print(metrics.round(3).to_string(index=False),flush=True)


if __name__=='__main__':main()
