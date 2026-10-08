"""Calibration-only crop/water fit with confirmed field dry biomass."""
from pathlib import Path
from copy import deepcopy
from functools import lru_cache,partial
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime,timezone
import argparse,json,sys,time
import numpy as np
import pandas as pd
from scipy.optimize import least_squares

ROOT=Path(__file__).resolve().parents[1]
sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT/'source_snapshots/native'));sys.path.insert(0,str(ROOT/'analysis_source'))
from research.ncp_irrigation.model import simulate_season
from calibration_core import balanced_weights,scales_from_calibration,window_predictions
from hydraulic_control import adjust as hydraulic_adjust
from biomass_constraints import confirmed_targets, residual_weights


def write(p,value):
    p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')


def specification(crop):
    if crop=='wheat':
        names=['transpiration_multiplier','soil_evaporation_coefficient','readily_available_water_fraction',
            'RUE_multiplier','leaf_allocation_multiplier','grain_number_multiplier','assimilation_water_stress_exponent',
            'root_density_decay_m_inv','maximum_root_depth_mm','log10_root_extraction_day']
        low=np.array([.5,.1,.3,.75,.65,.6,.25,0.,1500.,-3.])
        high=np.array([1.5,1.3,.75,1.3,1.4,1.5,2.,6.,2000.,-.7])
        x0=np.array([1.,.7622365049036547,.749747889598533,1.,1.,1.,.5861598758367449,.528336578536599,1999.1134051977456,-1.5371978155446837])
        prior=np.array([.3,.6,.2,.3,.3,.5,.5,3.,350.,1.])
    else:
        names=['transpiration_multiplier','soil_evaporation_coefficient','readily_available_water_fraction','RUE_multiplier',
            'early_leaf_allocation_multiplier','late_leaf_allocation_multiplier','grain_number_multiplier',
            'assimilation_water_stress_exponent','root_density_decay_m_inv','maximum_root_depth_mm']
        low=np.array([.5,.1,.3,.75,.65,.65,.6,.25,0.,1000.])
        high=np.array([1.5,1.3,.75,1.5,1.5,1.5,1.5,2.,6.,1700.])
        x0=np.array([1.,.23427513058644428,.30000000478348904,1.,1.,1.,1.,.2500436058797683,1.3497164464863076,1699.997951577429])
        prior=np.array([.3,.6,.2,.5,.4,.4,.5,.5,3.,350.])
    return names,low,high,x0,prior


def adjusted(payload,x,crop):
    p=deepcopy(payload)
    if p['inputs']['crop']!=crop:return p
    c=p['parameters']['crop'];c['transpiration_coefficient']=min(1.5,c['transpiration_coefficient']*float(x[0]));c['soil_evaporation_coefficient']=float(x[1])
    c['profile']['rue_g_mj']*=float(x[3]);g=c['growth_process'];h=p['parameters']['hydrology']
    if crop=='wheat':
        c['profile']['kernels_per_g_flowering_biomass']*=float(x[5])
        for k in ['leaf_allocation_multiplier','early_leaf_allocation_multiplier','late_leaf_allocation_multiplier']:
            if k in g:g[k]=min(2.,g[k]*float(x[4]))
        g['assimilation_water_stress_exponent']=float(x[6]);depth=x[8]
        h.update(root_density_decay_m_inv=float(x[7]),root_extraction_fraction_day=float(10**x[9]))
    else:
        c['profile']['kernels_per_g_flowering_biomass']*=float(x[6])
        g['early_leaf_allocation_multiplier']=min(2.,g['early_leaf_allocation_multiplier']*float(x[4]));g['late_leaf_allocation_multiplier']=min(2.,g['late_leaf_allocation_multiplier']*float(x[5]))
        g['assimilation_water_stress_exponent']=float(x[7]);depth=x[9]
        h.update(root_density_decay_m_inv=float(x[8]),root_extraction_fraction_day=1.)
    c['profile']['root_max_mm']=min(float(depth),sum(r['thickness_mm'] for r in p['inputs']['soil_layers']))
    h.update(plant_water_stress_method='root_zone_depletion',readily_available_water_fraction=float(x[2]))
    if p.get('presowing'):p['presowing']['parameters']=deepcopy(p['parameters'])
    return p


def run(p):
    initial=None
    if p.get('presowing'):initial=simulate_season(p['presowing']['inputs'],p['parameters']).final_state
    return simulate_season(p['inputs'],p['parameters'],initial)


@lru_cache(maxsize=4)
def data(crop,control):
    obs=pd.read_csv(ROOT/'data/observations.csv',low_memory=False)
    obs=obs[obs.crop.eq(crop)&obs.split.eq('calibration')].copy().reset_index(drop=True)
    f=pd.read_csv(ROOT/'data/reference_field_comparisons.csv')
    f=f[f.version.eq('supplied_fit')&f.crop.eq(crop)&f.split.eq('calibration')]
    rows=[]
    for r in f.itertuples():
        for variable,value in [('seasonal_et',r.observed_et_mm),('yield',r.yield_13pct_kg_ha*.87)]:
            rows.append(dict(case_id=r.case_id,site='Wuqiao',crop=crop,split='calibration',source_group_id=r.source_group_id,
                harvest_year=r.harvest_year,treatment=r.treatment,variable=variable,value=value))
    confirmation=json.loads((ROOT/'data/biomass_eligibility.json').read_text())
    biomass=confirmed_targets(f,confirmation)
    field=pd.concat([pd.DataFrame(rows),biomass],ignore_index=True)
    assert field.case_id.nunique()==12 and set(field.harvest_year)=={2016.,2017.,2018.}
    assert field.variable.value_counts().to_dict()=={'seasonal_et':12,'yield':12,'harvest_biomass':12}
    assert 'Wuqiao-2019' not in set(field.source_group_id)
    payloads={case:hydraulic_adjust(json.loads((ROOT/'inputs/station'/(case+'.json')).read_text()),control) for case in obs.case_id.unique()}
    payloads.update({case:hydraulic_adjust(json.loads((ROOT/'inputs/field'/(case+'_crop.json')).read_text()),control) for case in field.case_id.unique()})
    scales=scales_from_calibration(obs);ws=np.sqrt(.5*balanced_weights(obs));ss=obs.variable.map(scales).to_numpy()
    wf=residual_weights(field);fs=np.array([max(50.,.15*r.value) if r.variable=='seasonal_et' else max(1000.,.2*r.value) for r in field.itertuples()])
    periods=obs[obs.variable.eq('et')].groupby(['site','source_group_id','case_id']).value.sum().rename('value').reset_index();periods['variable']='observed_day_ET'
    wp=np.sqrt(.05*balanced_weights(periods));ps=.15*np.maximum(20.,periods.value.to_numpy())
    contrasts=[]
    for _,g in field[field.variable.eq('seasonal_et')].groupby('harvest_year'):
        a,b=g[g.treatment.eq('W0')].iloc[0],g[g.treatment.eq('W3')].iloc[0];contrasts.append((a.case_id,b.case_id,float(b.value-a.value)))
    return obs,field,payloads,ws,ss,wf,fs,periods,wp,ps,contrasts,scales


def residuals(x,crop,control):
    obs,f,payloads,ws,ss,wf,fs,periods,wp,ps,contrasts,_=data(crop,control)
    predicted=np.full(len(obs),np.nan);fp=np.full(len(f),np.nan);et={}
    for case,p in payloads.items():
        result=run(adjusted(p,x,crop))
        if case.startswith('yang2024'):
            g=f[f.case_id.eq(case)];values={'seasonal_et':result.summary['et_mm'],'yield':result.summary['yield_kg_ha'],
                'harvest_biomass':result.summary['biomass_kg_ha']}
            fp[g.index]=g.variable.map(values).to_numpy();et[case]=values['seasonal_et']
        else:
            g=obs[obs.case_id.eq(case)];predicted[g.index]=window_predictions(result.daily,g)
    assert np.isfinite(predicted).all() and np.isfinite(fp).all()
    totals=np.array([predicted[obs[obs.case_id.eq(r.case_id)&obs.variable.eq('et')].index].sum() for r in periods.itertuples()])
    response=np.array([(et[b]-et[a]-value)/50. for a,b,value in contrasts])
    *_,center,prior=specification(crop)
    return np.r_[ws*(predicted-obs.value.to_numpy())/ss,wf*(fp-f.value.to_numpy())/fs,
        wp*(totals-periods.value.to_numpy())/ps,np.sqrt(.1/len(response))*response,np.sqrt(.01/len(x))*(x-center)/prior]


def fit(crop,control):
    target=ROOT/'parameters/candidates'/(crop+'_'+control+'.json')
    if target.exists():raise RuntimeError('Completed candidate preserved.')
    obs,f,payloads,*rest=data(crop,control);names,low,high,initial,_=specification(crop)
    high[0]=min(high[0],1.5/max(p['parameters']['crop']['transpiration_coefficient'] for p in payloads.values()))
    leaves=[(4,'early_leaf_allocation_multiplier'),(5,'late_leaf_allocation_multiplier')] if crop=='maize' else [(4,k) for k in ['leaf_allocation_multiplier','early_leaf_allocation_multiplier','late_leaf_allocation_multiplier']]
    for index,key in leaves:
        values=[p['parameters']['crop']['growth_process'][key] for p in payloads.values() if key in p['parameters']['crop']['growth_process']]
        if values:high[index]=min(high[index],2/max(values))
    obs.to_csv(ROOT/'data'/('calibration_station_'+crop+'_'+control+'.csv'),index=False)
    f.to_csv(ROOT/'data'/('calibration_field_'+crop+'_'+control+'.csv'),index=False)
    history=[];started=time.monotonic();function=partial(residuals,crop=crop,control=control)
    def callback(intermediate_result):
        history.append(dict(crop=crop,control=control,iteration=len(history)+1,loss=float(intermediate_result.fun@intermediate_result.fun),
            elapsed_s=time.monotonic()-started,vector=json.dumps(intermediate_result.x.tolist())))
        pd.DataFrame(history).to_csv(ROOT/'parameters/candidates'/(crop+'_'+control+'_history.csv'),index=False)
        print(crop,control,'iteration',len(history),'loss',round(history[-1]['loss'],5),'seconds',round(history[-1]['elapsed_s']),flush=True)
    with ProcessPoolExecutor(max_workers=2) as workers:
        opt=least_squares(function,np.clip(initial,low+1e-6,high-1e-6),bounds=(low,high),diff_step=.005,x_scale='jac',
            max_nfev=45,ftol=4e-4,xtol=4e-4,gtol=4e-4,callback=callback,workers=workers.map)
    final=function(opt.x)
    record=dict(crop=crop,control=control,vector=opt.x.tolist(),vector_order=names,success=bool(opt.success),message=opt.message,
        nfev=opt.nfev,seconds=time.monotonic()-started,objective_loss=float(final@final),calibration_data_loss=float(final[:-len(opt.x)]@final[:-len(opt.x)]),
        lower_bounds=low.tolist(),upper_bounds=high.tolist(),station_cases=obs.case_id.nunique(),station_records=len(obs),
        eligible_sites=sorted(obs.site.unique()),field_cases=8,field_years=[2016,2017],
        calibration_groups=sorted(set(obs.source_group_id)|set(f.source_group_id)),testing_used=False,
        field_biomass_fitted=True,field_biomass_basis_confirmed=True,frozen_at_utc=datetime.now(timezone.utc).isoformat(),regional_promoted=False)
    write(target,record)
    if not opt.success:raise RuntimeError('Fit did not converge; candidate not frozen for testing.')
    print('FROZEN CANDIDATE',crop,control,record['calibration_data_loss'],flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--crop',choices=['wheat','maize'],required=True)
    parser.add_argument('--control',choices=['corrected_matric','corrected_curve_thresholds'],required=True)
    args=parser.parse_args();fit(args.crop,args.control)
