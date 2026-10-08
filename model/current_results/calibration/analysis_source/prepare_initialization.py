"""Preserve sources and apply one antecedent-profile rule to both partitions."""
from pathlib import Path
import hashlib
import json
import shutil
import sys

import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
OLD=ROOT.parent/'2026-10-07_compensated_root_uptake'
CONTROL=ROOT.parent/'2026-10-07_measured_soil_initialization_controls'
sys.path.insert(0,str(CONTROL/'analysis_source'))
from initialization_control import previous_month,remap_profile,apply_profile


def main():
    if (ROOT/'verification/input_manifest.json').exists():raise RuntimeError('Existing preparation preserved')
    manifest=[]
    def copy(source,relative):
        target=ROOT/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
        manifest.append(dict(source=str(source.resolve()),snapshot=relative,sha256=hashlib.sha256(target.read_bytes()).hexdigest()))
    files=['observations.csv','case_inventory.csv','partitions.csv','confirmed_field_biomass.csv',
        'biomass_eligibility.json','source_observations_with_rain_audit.csv','wuqiao_used_seasonal_observations.csv',
        'wuqiao_digitized_annual_yields.csv','field_partition.csv']
    for name in files:copy(OLD/'data'/name,'data/'+name)
    for name in ['primary_methods_and_table1.txt','supplement_extracted.json']:
        source=OLD/'source_snapshots'/name
        if source.exists():copy(source,'source_snapshots/'+name)
    for name in ['station_comparisons.csv','field_comparisons.csv']:
        copy(OLD/'predictions'/name,'source_snapshots/R59_'+name)
    for crop in ['wheat','maize']:
        copy(OLD/'parameters/candidates'/(crop+'_corrected_matric.json'),'source_snapshots/starting_candidates/'+crop+'_corrected_matric.json')
    for p in sorted((OLD/'source_snapshots/native').rglob('*.py')):
        copy(p,'source_snapshots/native/'+p.relative_to(OLD/'source_snapshots/native').as_posix())
    helpers=['baseline_objective.py','fit_objective.py','bounded_calibration.py','calibration_core.py','hydraulic_control.py',
        'retention_priors.py','biomass_constraints.py','audit_math.py','metric_helpers.py','diagnostic_plot_helpers.py',
        'calibration_audit.py','climate_partition.py','field_source_identity.py','field_temporal_et.py','field_soil_water.py']
    for name in helpers:copy(OLD/'analysis_source'/name,'analysis_source/'+name)
    copy(CONTROL/'analysis_source/initialization_control.py','analysis_source/initialization_control.py')
    copy(CONTROL/'analysis_source/test_initialization_control.py','analysis_source/test_initialization_control.py')
    profiles={}
    for p in sorted((CONTROL/'source_snapshots/soil_profiles').glob('*.csv')):
        copy(p,'source_snapshots/soil_profiles/'+p.name)
        frame=pd.read_csv(p,header=None)
        if frame.shape[1]==17:profiles[p.name.split('__')[0]]=(p.name,frame.iloc[3:])
    copy(CONTROL/'predictions/station_comparisons.csv','source_snapshots/R60_control_comparisons.csv')
    inventory=pd.read_csv(ROOT/'data/case_inventory.csv').set_index('case_id')
    applied=[];availability=[]
    for kind in ['station','field']:
        for source in sorted((OLD/'inputs'/kind).glob('*.json')):
            filename=source.name
            raw=json.loads(source.read_text())
            original_reference=OLD/'inputs/resolved/compensated_refit'/kind/filename
            reference=json.loads(original_reference.read_text())
            copy(source,'source_snapshots/original_inputs/'+kind+'/'+filename)
            copy(original_reference,'source_snapshots/original_reference_inputs/'+kind+'/'+filename)
            changed=False;reason='No audited antecedent profile at this site/plot'
            case=filename.removesuffix('_crop.json') if kind=='field' else source.stem
            if kind=='station' and inventory.loc[case,'site']=='Fengqiu':
                plot=case.split('-',3)[-1].removesuffix(' (2)')
                if plot in profiles:
                    file,frame=profiles[plot];year,month=previous_month(reference['inputs']['start_date'])
                    row=frame[pd.to_numeric(frame.iloc[:,0],errors='coerce').eq(year)&pd.to_numeric(frame.iloc[:,1],errors='coerce').eq(month)]
                    reason='No unique complete profile in the previous calendar month'
                    if len(row)==1:
                        percent=pd.to_numeric(row.iloc[0,2:17],errors='coerce').to_numpy(float)
                        if np.isfinite(percent).all():
                            try:
                                theta=remap_profile(percent,[100.]*15,[l['thickness_mm'] for l in reference['inputs']['soil_layers']])
                                raw=apply_profile(raw,theta,year,month);reference=apply_profile(reference,theta,year,month)
                                changed=True;reason='Lagged measured initialization; precise timing/aggregation/subplot unresolved'
                                applied.append(dict(case_id=case,crop=inventory.loc[case,'crop'],split=inventory.loc[case,'split'],
                                    source_group_id=inventory.loc[case,'source_group_id'],site='Fengqiu',plot=plot,
                                    profile_year=year,profile_month=month,source_file=file,source_row=int(row.index[0])+1,
                                    percent=json.dumps(percent.tolist()),mapped_theta=json.dumps(theta.tolist()),
                                    source_storage_mm=float(percent.sum()),confirmed_sowing_day_state=False))
                            except ValueError as e:reason=str(e)
            for relative,payload in [('inputs/'+kind+'/'+filename,raw),('inputs/reference/'+kind+'/'+filename,reference)]:
                target=ROOT/relative;target.parent.mkdir(parents=True,exist_ok=True)
                target.write_text(json.dumps(payload,ensure_ascii=False)+'\n')
                manifest.append(dict(source='derived from unchanged archived input; initial_theta rule only',snapshot=relative,
                    sha256=hashlib.sha256(target.read_bytes()).hexdigest()))
            availability.append(dict(case_id=case,kind=kind,initialization_changed=changed,reason=reason,
                                     original_case_retained=True,new_observation_exclusion=False))
    applied=pd.DataFrame(applied);availability=pd.DataFrame(availability)
    assert len(applied)==26 and applied.split.eq('calibration').sum()==11 and applied.split.eq('validation').sum()==15
    applied.to_csv(ROOT/'data/initialization_applied.csv',index=False)
    availability.to_csv(ROOT/'data/initialization_availability.csv',index=False)
    station=pd.read_csv(ROOT/'source_snapshots/R59_station_comparisons.csv',low_memory=False)
    station=station[station.version.eq('compensated_refit')].copy();station['version']='supplied_fit'
    new=pd.read_csv(ROOT/'source_snapshots/R60_control_comparisons.csv',low_memory=False)
    new=new[new.version.eq('measured_profile')].set_index('observation_id')
    mask=station.observation_id.isin(new.index)
    assert mask.sum()==102 and station.loc[mask,'split'].eq('calibration').all()
    station.loc[mask,'predicted']=new.loc[station.loc[mask,'observation_id'],'predicted'].to_numpy()
    station.to_csv(ROOT/'data/reference_station_comparisons.csv',index=False)
    field=pd.read_csv(ROOT/'source_snapshots/R59_field_comparisons.csv')
    field=field[field.version.eq('compensated_refit')].copy();field['version']='supplied_fit'
    field.to_csv(ROOT/'data/reference_field_comparisons.csv',index=False)
    for stem in ['Raw_field_water_observations','Raw_eligible_station_dynamics','Raw_confirmed_field_biomass']:
        for extension in ['.png','.pdf','_caption.txt']:copy(OLD/'figures'/(stem+extension),'figures/'+stem+extension)
    for p in sorted((ROOT/'analysis_source').glob('*.py'))+list((ROOT/'data').glob('*'))+[ROOT/'implementation_plan.md']:
        if not p.is_file():continue
        rel=p.relative_to(ROOT).as_posix()
        if rel not in {r['snapshot'] for r in manifest}:
            manifest.append(dict(source='run-specific derived artifact',snapshot=rel,sha256=hashlib.sha256(p.read_bytes()).hexdigest()))
    (ROOT/'verification/input_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (ROOT/'verification/preparation.json').write_text(json.dumps(dict(initialization_changed_cases=26,changed_calibration_cases=11,
        changed_station_testing_cases=15,original_station_cases_retained=132,field_cases_retained=32,
        original_observations_retained=7397,field_inputs_unchanged=True,weather_management_soil_parameters_unchanged=True,
        conditional_soil_initialization=True,testing_simulated=False,native_model_changed=False),indent=2)+'\n')
    print('Prepared',len(manifest),'source/input hashes; initialization rule applied to 11 calibration and 15 testing cases.')


if __name__=='__main__':main()
