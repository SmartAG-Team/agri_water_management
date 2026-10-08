"""Estimate water responses from documented field management, retaining growth fits."""
from pathlib import Path
from functools import lru_cache
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime,timezone
import argparse,hashlib,json,os,time
import numpy as np
import pandas as pd
import extraction_fit as legacy
from qualified_fit import qualified_data,run,write
from bounded_calibration import fit_bounded,from_unit

ROOT=Path(__file__).resolve().parents[1]
WATER_NAMES=['transpiration_multiplier','soil_evaporation_coefficient','readily_available_water_fraction',
    'assimilation_water_stress_exponent','root_density_decay_m_inv','maximum_root_depth_mm',
    'log10_root_extraction_day','root_compensation_fraction']


def specification(crop):
    names,lo,hi,center,prior=legacy.specification(crop)
    indices=[names.index(n) for n in WATER_NAMES]
    return WATER_NAMES.copy(),lo[indices],hi[indices],center[indices],prior[indices]


@lru_cache(maxsize=2)
def inherited(crop):
    return json.loads((ROOT/'source_snapshots/starting_parameters'/f'{crop}.json').read_text())


def full_vector(water_vector,crop):
    water=np.asarray(water_vector,float);_,lo,hi,*_=specification(crop)
    if water.shape!=(8,) or not np.isfinite(water).all() or np.any(water<lo) or np.any(water>hi):
        raise ValueError('Eight water coefficients within their inherited bounds required')
    names=legacy.specification(crop)[0];full=np.array(inherited(crop)['vector'],float)
    full[[names.index(n) for n in WATER_NAMES]]=water
    return full


def adjusted(payload,vector,crop):
    full=np.asarray(vector,float);names=legacy.specification(crop)[0]
    if full.shape!=(len(names),) or not np.isfinite(full).all():raise ValueError('Complete inherited parameter vector required')
    original=inherited(crop)['vector']
    if any(full[i]!=original[i] for i,n in enumerate(names) if n not in WATER_NAMES):
        raise ValueError('Multisite growth coefficients are fixed in this water refit')
    return legacy.adjusted(payload,full,crop)


@lru_cache(maxsize=2)
def field_data(crop):
    _,field,payloads,_,_,wf,fs,contrasts=qualified_data(crop)
    used=set(field.case_id);payloads={c:p for c,p in payloads.items() if c in used}
    assert len(payloads)==12 and all(c.startswith('yang2024') for c in payloads)
    assert set(field.harvest_year)=={2016,2017,2018} and field.split.eq('calibration').all()
    return field,payloads,wf,fs,contrasts


def case_prediction(item):
    case,payload,vector,crop=item
    result=run(adjusted(payload,full_vector(vector,crop),crop))
    return case,dict(seasonal_et=result.summary['et_mm'],yield_=result.summary['yield_kg_ha'],
        harvest_biomass=result.summary['biomass_kg_ha'])


def residuals(vector,crop,pool):
    field,payloads,wf,fs,contrasts=field_data(crop);pred=np.full(len(field),np.nan);ets={}
    for case,values in pool.map(case_prediction,[(c,p,np.asarray(vector),crop) for c,p in payloads.items()]):
        values['yield']=values.pop('yield_');g=field[field.case_id.eq(case)]
        pred[g.index]=g.variable.map(values).to_numpy();ets[case]=values['seasonal_et']
    assert np.isfinite(pred).all()
    response=np.array([(ets[b]-ets[a]-o)/50. for a,b,o in contrasts])
    *_,center,prior=specification(crop)
    return np.r_[wf*(pred-field.value.to_numpy())/fs,np.sqrt(.1/len(response))*response,
        np.sqrt(.01/8)*(np.asarray(vector)-center)/prior]


def fit(crop):
    target=ROOT/'parameters/candidates'/f'{crop}_corrected_matric.json'
    if target.exists():raise RuntimeError('Completed candidate preserved')
    for row in json.loads((ROOT/'verification/input_manifest.json').read_text()):
        assert hashlib.sha256((ROOT/row['snapshot']).read_bytes()).hexdigest()==row['sha256'],row['snapshot']
    old=inherited(crop);all_names,all_lo,all_hi,*_=legacy.specification(crop)
    names,lo,hi,center,prior=specification(crop);indices=[all_names.index(n) for n in names]
    initial=np.array(old['vector'])[indices];field,payloads,*_=field_data(crop)
    field.to_csv(ROOT/'data'/f'used_new_water_field_{crop}.csv',index=False)
    previous=pd.read_csv(ROOT/'source_snapshots/previous_field_comparisons.csv')
    from qualified_metrics import losses
    station=pd.read_csv(ROOT/'source_snapshots/previous_station_comparisons.csv',low_memory=False)
    parts=losses(station,previous);parts=parts[parts.version.eq('management_refit')&parts.crop.eq(crop)].iloc[0]
    expected=sum(parts[n] for n in ['field_seasonal_ET_loss','field_grain_yield_loss','field_biomass_loss','response_loss'])
    started=time.monotonic();history=[]
    with ProcessPoolExecutor(max_workers=4) as pool:
        function=lambda v:residuals(v,crop,pool)
        before=function(initial);reference_loss=float(before[:-8]@before[:-8])
        assert abs(reference_loss-expected)<1e-10
        write(ROOT/'verification'/f'starting_qualified_objective_{crop}.json',dict(
            reference_calibration_data_loss=reference_loss,objective='documented_field_water',
            reference_replayed=True,testing_used=False,daily_ET_used_for_fitting=False,
            station_growth_used_in_new_water_objective=False,inherited_growth_sites=old['eligible_sites']))
        print(crop,'PID',os.getpid(),'documented-field reference loss',reference_loss,flush=True)
        def callback(intermediate_result):
            physical=from_unit(intermediate_result.x,lo,hi);r=np.asarray(intermediate_result.fun)
            history.append(dict(crop=crop,iteration=len(history)+1,calibration_data_loss=float(r[:-8]@r[:-8]),
                objective_loss=float(r@r),elapsed_s=time.monotonic()-started,vector=json.dumps(full_vector(physical,crop).tolist())))
            pd.DataFrame(history).to_csv(target.with_name(target.stem+'_history.csv'),index=False)
            print(crop,'iteration',len(history),'field loss',history[-1]['calibration_data_loss'],'seconds',round(history[-1]['elapsed_s']),flush=True)
        result=fit_bounded(function,initial,lo,hi,workers=None,difference_step=.002,
            ftol=8e-5,xtol=8e-5,gtol=8e-5,max_nfev=40,callback=callback,x_scale='jac')
        final=function(result.physical_x)
    complete=full_vector(result.physical_x,crop)
    record=dict(crop=crop,control='corrected_matric',vector=complete.tolist(),vector_order=all_names,
        estimated_parameter_names=names,success=bool(result.success),message=str(result.message),status=int(result.status),
        nfev=result.nfev,njev=result.njev,optimality=float(result.optimality),seconds=time.monotonic()-started,
        objective_loss=float(final@final),calibration_data_loss=float(final[:-8]@final[:-8]),reference_calibration_data_loss=reference_loss,
        objective_definition='documented_field_water',lower_bounds=all_lo.tolist(),upper_bounds=all_hi.tolist(),
        water_lower_bounds=lo.tolist(),water_upper_bounds=hi.tolist(),free_parameters=8,extra_free_parameters=0,
        station_records_used_in_new_water_fit=0,inherited_growth_records=480 if crop=='wheat' else 433,
        eligible_sites=old['eligible_sites'],inherited_multisite_growth_coefficients=True,
        fixed_growth_parameters=[n for n in all_names if n not in names],field_cases=12,field_years=[2016,2017,2018],field_testing_years=[2019],
        testing_used=False,testing_previously_inspected=True,daily_ET_used_for_fitting=False,native_model_changed=False,
        existing_coefficient_bounds_unchanged=True,other_coefficient_priors_unchanged=True,
        growth_coefficients_unchanged=True,phenology_changed=False,soil_initialization_rule_changed=False,
        frozen_at_utc=datetime.now(timezone.utc).isoformat(),manuscript_promoted=False,regional_promoted=False)
    write(target,record)
    if not result.success:raise RuntimeError('Water calibration did not converge; no testing')
    print('FROZEN DOCUMENTED-MANAGEMENT CANDIDATE',crop,record['calibration_data_loss'],flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--crop',choices=['wheat','maize'],required=True)
    fit(parser.parse_args().crop)
