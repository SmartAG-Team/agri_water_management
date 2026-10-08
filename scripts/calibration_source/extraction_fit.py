"""Estimate the existing maize root-water extraction coefficient."""
from pathlib import Path
from copy import deepcopy
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime,timezone
import hashlib,json,os,time
import numpy as np
import pandas as pd
import qualified_fit as base
from bounded_calibration import fit_bounded,from_unit

ROOT=Path(__file__).resolve().parents[1]


def specification(crop):
    names,low,high,center,prior=base.specification(crop)
    if crop=='wheat':return names,low,high,center,prior
    if crop!='maize':raise ValueError('Unknown crop')
    return names+['log10_root_extraction_day'],np.r_[low,-3.],np.r_[high,0.],np.r_[center,0.],np.r_[prior,1.]


def adjusted(payload,vector,crop):
    vector=np.asarray(vector,float)
    if crop=='wheat':return base.adjusted(payload,vector,crop)
    if crop!='maize' or vector.shape!=(12,) or not np.isfinite(vector).all() or not -3.<=vector[-1]<=0.:
        raise ValueError('Twelve finite maize coefficients and extraction rate in [0.001,1] required')
    p=base.adjusted(payload,vector[:-1],crop)
    if p['inputs']['crop']==crop:
        p['parameters']['hydrology']['root_extraction_fraction_day']=float(10**vector[-1])
        if p.get('presowing'):p['presowing']['parameters']=deepcopy(p['parameters'])
    return p


def case_prediction(item):
    case,payload,vector,observations=item
    result=base.run(adjusted(payload,vector,'maize'))
    if case.startswith('yang2024'):
        return case,{'seasonal_et':result.summary['et_mm'],'yield':result.summary['yield_kg_ha'],
                     'harvest_biomass':result.summary['biomass_kg_ha']}
    return case,base.window_predictions(result.daily,observations)


def residuals(vector,pool):
    obs,field,payloads,ws,ss,wf,fs,contrasts=base.qualified_data('maize')
    tasks=[(case,p,np.asarray(vector),obs[obs.case_id.eq(case)]) for case,p in payloads.items()]
    predicted=np.full(len(obs),np.nan);fp=np.full(len(field),np.nan);ets={}
    for case,values in pool.map(case_prediction,tasks):
        if case.startswith('yang2024'):
            g=field[field.case_id.eq(case)];fp[g.index]=g.variable.map(values).to_numpy();ets[case]=values['seasonal_et']
        else:predicted[obs[obs.case_id.eq(case)].index]=values
    assert np.isfinite(predicted).all() and np.isfinite(fp).all()
    responses=np.array([(ets[b]-ets[a]-value)/50. for a,b,value in contrasts])
    *_,center,prior=specification('maize')
    return np.r_[ws*(predicted-obs.value.to_numpy())/ss,wf*(fp-field.value.to_numpy())/fs,
                 np.sqrt(.1/len(responses))*responses,np.sqrt(.01/12)*(np.asarray(vector)-center)/prior]


def fit():
    target=ROOT/'parameters/candidates/maize_corrected_matric.json'
    if target.exists():raise RuntimeError('Completed fit preserved')
    for row in json.loads((ROOT/'verification/input_manifest.json').read_text()):
        assert hashlib.sha256((ROOT/row['snapshot']).read_bytes()).hexdigest()==row['sha256'],row['snapshot']
    previous=json.loads((ROOT/'source_snapshots/starting_parameters/maize.json').read_text())
    names,low,high,center,prior=specification('maize')
    reference=np.r_[previous['vector'],0.];initial=reference.copy();initial[-1]=np.log10(.03)
    obs,field,*_=base.qualified_data('maize')
    assert len(obs)==433 and obs.site.nunique()==5 and obs.variable.ne('et').all()
    obs.to_csv(ROOT/'data/used_qualified_station_maize.csv',index=False)
    field.to_csv(ROOT/'data/used_qualified_field_maize.csv',index=False)
    started=time.monotonic();history=[]
    with ProcessPoolExecutor(max_workers=4) as pool:
        ref=residuals(reference,pool);begin=residuals(initial,pool)
        reference_loss=float(ref[:-12]@ref[:-12]);initial_loss=float(begin[:-12]@begin[:-12])
        assert abs(reference_loss-previous['calibration_data_loss'])<1e-10
        controls=pd.read_csv(ROOT/'source_snapshots/extraction_controls/calibration_data_losses.csv')
        expected=controls[controls.version.eq('extraction_0.03')].calibration_data_loss.iloc[0]
        assert abs(initial_loss-expected)<1e-10
        base.write(ROOT/'verification/starting_qualified_objective_maize.json',dict(
            reference_calibration_data_loss=reference_loss,initial_calibration_data_loss=initial_loss,
            initial_extraction_fraction_day=.03,free_parameters=12,extra_free_parameters=1,
            reference_replayed=True,calibration_controls_replayed=True,native_model_changed=False,
            daily_ET_used_for_fitting=False,testing_used=False))
        print('maize PID',os.getpid(),'reference',reference_loss,'extraction start',initial_loss,flush=True)
        def callback(intermediate_result):
            physical=from_unit(intermediate_result.x,low,high);res=np.asarray(intermediate_result.fun)
            history.append(dict(crop='maize',iteration=len(history)+1,calibration_data_loss=float(res[:-12]@res[:-12]),
                objective_loss=float(res@res),elapsed_s=time.monotonic()-started,vector=json.dumps(physical.tolist())))
            pd.DataFrame(history).to_csv(target.with_name(target.stem+'_history.csv'),index=False)
            print('maize iteration',len(history),'loss',history[-1]['calibration_data_loss'],'seconds',round(history[-1]['elapsed_s']),flush=True)
        result=fit_bounded(lambda vector:residuals(vector,pool),initial,low,high,workers=None,difference_step=.002,
            ftol=8e-5,xtol=8e-5,gtol=8e-5,max_nfev=40,callback=callback,x_scale='jac')
        final=residuals(result.physical_x,pool)
    record=dict(crop='maize',control='corrected_matric',vector=result.physical_x.tolist(),vector_order=names,
        success=bool(result.success),message=str(result.message),status=int(result.status),nfev=result.nfev,njev=result.njev,
        optimality=float(result.optimality),seconds=time.monotonic()-started,objective_loss=float(final@final),
        calibration_data_loss=float(final[:-12]@final[:-12]),reference_calibration_data_loss=reference_loss,
        lower_bounds=low.tolist(),upper_bounds=high.tolist(),station_cases=obs.case_id.nunique(),station_records=len(obs),
        eligible_sites=sorted(obs.site.unique()),field_cases=12,field_years=[2016,2017,2018],field_testing_years=[2019],
        testing_used=False,testing_previously_inspected=True,daily_ET_used_for_fitting=False,native_model_changed=False,
        maize_extraction_rate_estimated=True,root_extraction_fraction_day=float(10**result.physical_x[-1]),
        free_parameters=12,extra_free_parameters=1,other_coefficient_priors_unchanged=True,phenology_changed=False,
        soil_initialization_rule_changed=False,root_mass_shape_imposed=False,
        station_growth_conditioned_on_unconfirmed_management=True,frozen_at_utc=datetime.now(timezone.utc).isoformat(),
        manuscript_promoted=False,regional_promoted=False)
    base.write(target,record)
    if not result.success:raise RuntimeError('Fit did not converge; no testing')
    print('FROZEN MAIZE EXTRACTION CANDIDATE',record['calibration_data_loss'],flush=True)


if __name__=='__main__':fit()
