"""Pre-season relative hydroclimatic classes and fixed-quota native responses.

All thresholds and candidate selections use training harvest years2003–2013.
Regional GRACE storage is shared across the domain; representative antecedent
precipitation is spatially varying. Existing simulations are fixed-history
conditional scenarios, not continuously rerun class-switching policies.
"""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,shutil
import numpy as np
import pandas as pd
import xarray as xr
ROOT=Path(__file__).resolve().parents[1];PROJECT=ROOT.parents[1];PACKAGE=PROJECT/'model/2026-10-03_submission_package';OLD=PACKAGE/'runs/grace_context';MODEL=PACKAGE/'runs/regional_quota_conductivity'
TRAIN=range(2003,2014);TEST=range(2014,2026);YEARS=list(TRAIN)+list(TEST);QUOTAS=[0.,.25,.5,.75,1.]
CLASS_ORDER=['lower_storage_dry','lower_storage_wet','higher_storage_dry','higher_storage_wet'];CLASS_LABEL={'lower_storage_dry':'Lower storage + dry','lower_storage_wet':'Lower storage + wet','higher_storage_dry':'Higher storage + dry','higher_storage_wet':'Higher storage + wet','missing_storage':'Storage unavailable'}
def write_json(path,value):path.write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
def sha(f):
 h=hashlib.sha256()
 with Path(f).open('rb') as q:
  for b in iter(lambda:q.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def safe(records):return pd.DataFrame(records).replace({np.nan:None}).to_dict('records')
def sources_and_storage():
 for n in ['CSR_GRACE_GRACE-FO_RL0603_Mascons_all-corrections.nc','CSR_GRACE_GRACE-FO_RL0603_mascons_mapping_file.nc','CSR_GRACE_GRACE-FO_RL06_Mascons_v02_LandMask.nc']:
  target=ROOT/'source_snapshots/grace'/n
  if not target.exists():shutil.copy2(PROJECT/'data/grace/raw/csr_rl0603'/n,target)
 for n in ['CSR_RL0603_time_axis.csv','csr_source.html']:
  shutil.copy2(PROJECT/'data/grace/metadata'/n,ROOT/'source_snapshots/grace'/n)
 for n in ['used_daily_weather.csv.gz','used_crop_parameters.json','used_hydraulic_profiles.json','representative_cells.csv','all_source_cell_mapping.csv','rotation_summaries.csv','all_season_summaries.csv','frozen_training_allocation.csv','verification.json','preparation.json','regional_scenarios_source.py']:
  shutil.copy2(MODEL/n,ROOT/'source_snapshots/model'/n)
 shutil.copy2(PACKAGE/'source_snapshots/native_model_84457b3.zip',ROOT/'source_snapshots/model/native_model_84457b3.zip')
 shutil.copy2(OLD/'exact_regional_weights.nc',ROOT/'source_snapshots/grace/original_exact_regional_weights.nc')
 shutil.copy2(OLD/'observed_domain_twsa.csv',ROOT/'source_snapshots/grace/original_unmasked_domain_twsa.csv')
 shutil.copy2(PACKAGE/'data/regional/operational_plain_base_exact_geometry.geojson',ROOT/'source_snapshots/operational_plain_geometry.geojson')
 source=ROOT/'source_snapshots/grace/CSR_GRACE_GRACE-FO_RL0603_Mascons_all-corrections.nc';land=ROOT/'source_snapshots/grace/CSR_GRACE_GRACE-FO_RL06_Mascons_v02_LandMask.nc';mapping=ROOT/'source_snapshots/grace/CSR_GRACE_GRACE-FO_RL0603_mascons_mapping_file.nc'
 with xr.open_dataset(ROOT/'source_snapshots/grace/original_exact_regional_weights.nc') as fullw:
  w=fullw.domain_weight_m2;w=w.isel(lat=np.flatnonzero(w.sum('lon').values>0),lon=np.flatnonzero(w.sum('lat').values>0)).load()
 with xr.open_dataset(source) as raw,xr.open_dataset(land) as lm,xr.open_dataset(mapping) as mp:
  sub=raw.sel(lat=w.lat,lon=w.lon).load();land_mask=lm.LO_val.sel(lat=w.lat,lon=w.lon).load();mapping_sub=mp.sel(lat=w.lat,lon=w.lon).load();landw=w.where(land_mask==1,0.);values=sub.lwe_thickness.values.astype('float64')*10.;valid=np.isfinite(values);denom=np.sum(landw.values[None,:,:]*valid,axis=(1,2));main=np.nansum(values*landw.values[None,:,:],axis=(1,2))/denom;old=np.nansum(values*w.values[None,:,:],axis=(1,2))/np.sum(w.values[None,:,:]*valid,axis=(1,2))
  assert np.allclose(denom,landw.sum().item(),rtol=1e-12)
  sub.to_netcdf(ROOT/'data/used_csr_native_monthly_subset.nc');mapping_sub.to_netcdf(ROOT/'data/used_csr_native_mascon_mapping_subset.nc');land_mask.to_dataset(name='LO_val').to_netcdf(ROOT/'data/used_csr_landmask_subset.nc')
  xr.Dataset({'domain_area_weight_m2':w,'csr_land_domain_weight_m2':landw}).to_netcdf(ROOT/'data/exact_grace_support_weights.nc')
  attrs=dict(raw.attrs);mapping_attrs=dict(mp.attrs)
  ids=mapping_sub.mascon_id.values;area=landw.values;inventory=[]
  for identifier in np.unique(ids[area>0]):
   q=ids==identifier;tags=sorted(set(mapping_sub.mascon_tag.values[q].tolist()));inventory.append(dict(mascon_id=int(identifier),source_tags=','.join(tags),number_publication_pixels_in_support=int(np.count_nonzero(q & (area>0))),domain_land_support_area_km2=float(area[q].sum()/1e6),regional_weight_fraction=float(area[q].sum()/area.sum())))
  pd.DataFrame(inventory).to_csv(ROOT/'data/mascon_support_inventory.csv',index=False)
 axis=pd.read_csv(ROOT/'source_snapshots/grace/CSR_RL0603_time_axis.csv');assert len(axis)==len(main);frame=axis.copy();frame['observed_twsa_mm']=main;frame['unmasked_same_domain_twsa_mm']=old;frame['valid_land_support_fraction']=denom/float(landw.sum());frame['land_support_area_km2']=float(landw.sum()/1e6);frame.to_csv(ROOT/'data/observed_regional_twsa_monthly.csv',index=False)
 metadata=dict(dataset_doi=attrs['id'],source_version=attrs['product_version'],source_attributes=attrs,mapping_attributes=mapping_attrs,source_publication_grid_deg=.25,source_native_estimation='About1degreeequal-area geodesicmascons',effective_resolution='Roughly300km;CSR warnsaboutregionsbelow200000km² andsinglegridpointanalysis',domain_area_km2=float(w.sum()/1e6),land_support_area_km2=float(landw.sum()/1e6),landmask_excluded_support_km2=float((w.sum()-landw.sum())/1e6),land_support_fraction=float(landw.sum()/w.sum()),n_native_mascon_ids_in_support=len(inventory),aggregation='Onephysicalarea-weightedregionalTWSseries usingofficialCSRlandmask; noindependentpoint/fieldTWSclasses',unit_conversion='Sourcefloat32cm castfloat64 then×10=mm; original1025kg/m³CSRconvention retained',reference_period='2004–2009mean removed inCSRsource',storage_components='Totalwaterstorage includingsoil/surface/groundwater/snow; nogroundwaterdecomposition',official_source_url='https://www2.csr.utexas.edu/grace/RL0603_mascons.html',source_files={str(f.relative_to(ROOT)):dict(bytes=f.stat().st_size,sha256=sha(f)) for f in [source,land,mapping]},mapping_history_note='Officialdownloadedmapping file retains publisherhistoryCreatedbyChatGPTfromCSVcolumns; no IDs generated bythisanalysis')
 write_json(ROOT/'audit/grace_source_support.json',metadata)
 return frame,metadata

def pre_storage(frame,months=(8,9),required=1,column='observed_twsa_mm'):
 d=frame.copy();d['year']=pd.to_datetime(d.nominal_month).dt.year;d['m']=pd.to_datetime(d.nominal_month).dt.month;rows=[]
 for y in YEARS:
  q=d[(d.year==y-1)&d.m.isin(months)];n=len(q);available=n>=required;end=pd.to_datetime(q.source_interval_end).max() if n else pd.NaT
  if n:assert end<=pd.Timestamp(f'{y-1}-10-12')
  rows.append(dict(harvest_year=y,period='training' if y in TRAIN else 'testing',antecedent_calendar_year=y-1,storage_expected_nominal_months=','.join(f'{y-1}-{m:02d}' for m in months),storage_observed_nominal_months=','.join(q.nominal_month),storage_n_observed_months=n,storage_required_months=required,storage_available=available,storage_complete_window=(n==len(months)),pre_sowing_twsa_mm=float(q[column].mean()) if available else np.nan,last_source_observation_interval_end=end.strftime('%Y-%m-%d') if n else '',scenario_sowing_date=f'{y-1}-10-12',release_available_before_sowing_verified=False))
 return pd.DataFrame(rows)

def weighted_quantile(values,weights,q):
 order=np.argsort(np.asarray(values),kind='stable');values=np.asarray(values)[order];weights=np.asarray(weights)[order];index=np.searchsorted(np.cumsum(weights),weights.sum()*q,side='left');return float(values[min(index,len(values)-1)])

def antecedent_weather():
 reps=pd.read_csv(ROOT/'source_snapshots/model/representative_cells.csv');weather=pd.read_csv(ROOT/'source_snapshots/model/used_daily_weather.csv.gz');dates=pd.to_datetime(weather.date);weather['harvest_year']=dates.dt.year+1;used=weather[dates.dt.month.between(7,9)&weather.harvest_year.isin(YEARS)].copy();assert len(used)==32*92*23
 used.to_csv(ROOT/'data/antecedent_daily_weather_used.csv.gz',index=False)
 agg=used.groupby(['point','harvest_year'],as_index=False).agg(antecedent_precipitation_mm=('precipitation_mm','sum'),antecedent_reference_et0_mm=('reference_et0_mm','sum'),n_antecedent_days=('date','size'))
 agg['antecedent_p_et0_ratio']=agg.antecedent_precipitation_mm/agg.antecedent_reference_et0_mm;agg=agg.merge(reps[['representative_id','zone_id','latitude','longitude','represented_area_ha']],left_on='point',right_on='representative_id',validate='many_to_one').drop(columns='point');agg['period']=np.where(agg.harvest_year.le(2013),'training','testing');agg['antecedent_start_date']=(agg.harvest_year-1).astype(str)+'-07-01';agg['antecedent_end_date']=(agg.harvest_year-1).astype(str)+'-09-30';agg.to_csv(ROOT/'data/antecedent_precipitation_by_representative_year.csv',index=False)
 return agg

def memberships(climate,storage,tws_quantile=.5,p_quantile=.5):
 tws_threshold=float(storage[storage.period.eq('training')].pre_sowing_twsa_mm.quantile(tws_quantile));training=climate[climate.period.eq('training')];p_threshold=weighted_quantile(training.antecedent_precipitation_mm,training.represented_area_ha,p_quantile)
 c=climate.merge(storage.drop(columns='period'),on='harvest_year',validate='many_to_one');c['storage_threshold_mm']=tws_threshold;c['precipitation_threshold_mm']=p_threshold;c['relative_storage']=np.where(c.pre_sowing_twsa_mm<tws_threshold,'lower_storage','higher_storage');c['relative_precipitation']=np.where(c.antecedent_precipitation_mm<p_threshold,'dry','wet');c['relative_class']=c.relative_storage+'_'+c.relative_precipitation;c.loc[~c.storage_available,['relative_class','relative_storage']]=['missing_storage','missing_storage'];c['class_available']=c.storage_available;c['decision_measurement_cutoff_date']=(c.harvest_year-1).astype(str)+'-10-11';return c,dict(storage_threshold_mm=tws_threshold,precipitation_threshold_mm=p_threshold,storage_quantile=tws_quantile,precipitation_quantile=p_quantile,training_harvest_years=list(TRAIN),grain_response_used_for_class_thresholds=False)

METRICS=['yield_kg_ha','crop_et_mm','modeled_total_et_mm','irrigation_mm','annual_bottom_drainage_mm','annual_runoff_mm']
def joined_predictions(c):
 r=pd.read_csv(ROOT/'source_snapshots/model/rotation_summaries.csv');q=r[r.harvest_year.isin(YEARS)].merge(c.drop(columns='represented_area_ha'),on=['representative_id','harvest_year'],validate='many_to_one')
 baseline=q[q.fraction.eq(1.)][['representative_id','harvest_year']+METRICS].rename(columns={m:'full_'+m for m in METRICS});q=q.merge(baseline,on=['representative_id','harvest_year'],validate='many_to_one')
 for m in METRICS:q['delta_'+m]=q[m]-q['full_'+m]
 q['paired_grain_retention_fraction']=q.yield_kg_ha/q.full_yield_kg_ha
 return q

def response_tables(q,persist=True):
 years=[]
 for (period,clas,fraction,year),g in q.groupby(['period','relative_class','fraction','harvest_year']):
  area=g.represented_area_ha;row=dict(period=period,relative_class=clas,quota_fraction=fraction,harvest_year=int(year),n_representatives=len(g),class_area_ha=float(area.sum()))
  for m in METRICS:row['mean_'+m]=float(np.average(g[m],weights=area));row['mean_full_'+m]=float(np.average(g['full_'+m],weights=area));row['paired_delta_'+m]=float(np.average(g['delta_'+m],weights=area))
  row['grain_retention_pct']=100*row['mean_yield_kg_ha']/row['mean_full_yield_kg_ha'];years.append(row)
 annual=pd.DataFrame(years)
 if persist:annual.to_csv(ROOT/'tables/class_quota_yearly_responses.csv',index=False)
 rows=[]
 for (period,clas,fraction),g in q.groupby(['period','relative_class','fraction']):
  a=g.represented_area_ha;y=annual[(annual.period==period)&(annual.relative_class==clas)&(annual.quota_fraction==fraction)];row=dict(period=period,relative_class=clas,class_available=(clas!='missing_storage'),quota_fraction=fraction,n_representative_years=len(g),n_unique_representatives=int(g.representative_id.nunique()),n_harvest_years=int(g.harvest_year.nunique()),total_class_area_year_ha=float(a.sum()),mean_annual_class_area_ha=float(a.sum()/g.harvest_year.nunique()))
  for m in METRICS:row['mean_'+m]=float(np.average(g[m],weights=a));row['mean_full_'+m]=float(np.average(g['full_'+m],weights=a));row['paired_delta_'+m]=float(np.average(g['delta_'+m],weights=a));row['annual_mean_'+m+'_minimum']=float(y['mean_'+m].min());row['annual_mean_'+m+'_maximum']=float(y['mean_'+m].max());row['annual_mean_'+m+'_std_ddof1']=float(y['mean_'+m].std(ddof=1))
  row['grain_retention_pct']=100*row['mean_yield_kg_ha']/row['mean_full_yield_kg_ha'];row['area_weighted_mean_paired_grain_retention_pct']=100*float(np.average(g.paired_grain_retention_fraction,weights=a));row['annual_grain_retention_pct_minimum']=float(y.grain_retention_pct.min());row['annual_grain_retention_pct_maximum']=float(y.grain_retention_pct.max());row['annual_grain_retention_pct_std_ddof1']=float(y.grain_retention_pct.std(ddof=1));row['field_irrigation_reduction_pct']=100*(1-fraction);rows.append(row)
 out=pd.DataFrame(rows)
 if persist:out.to_csv(ROOT/'tables/class_quota_responses.csv',index=False)
 return out,annual

def select_candidates(responses,annual,criteria=(.90,.95,.98),persist=True):
 rows=[]
 for criterion in criteria:
  for clas,g in responses[responses.period.eq('training')&responses.class_available].groupby('relative_class'):
   accepted=g[g.grain_retention_pct.ge(100*criterion)].sort_values('quota_fraction');assert len(accepted);candidate=accepted.iloc[0];rows.append(dict(relative_class=clas,grain_retention_target_pct=100*criterion,primary_criterion=(criterion==.95),selected_quota_fraction=float(candidate.quota_fraction),field_irrigation_mm=float(candidate.mean_irrigation_mm),training_grain_retention_pct=float(candidate.grain_retention_pct),training_grain_retention_annual_minimum_pct=float(candidate.annual_grain_retention_pct_minimum),training_grain_retention_annual_maximum_pct=float(candidate.annual_grain_retention_pct_maximum),training_retention_above_target_percentage_points=float(candidate.grain_retention_pct-100*criterion),training_crop_et_mm=float(candidate.mean_crop_et_mm),training_modeled_crop_plus_fallow_et_mm=float(candidate.mean_modeled_total_et_mm),training_n_harvest_years=int(candidate.n_harvest_years),selection_uses_testing_responses=False))
 selected=pd.DataFrame(rows)
 if persist:selected.to_csv(ROOT/'tables/training_selected_quotas.csv',index=False)
 evaluation=[]
 for row in selected.to_dict('records'):
  match=responses[responses.period.eq('testing')&responses.relative_class.eq(row['relative_class'])&responses.quota_fraction.eq(row['selected_quota_fraction'])]
  item=dict(**row,testing_class_available=bool(len(match)))
  if len(match):
   s=match.iloc[0];y=annual[annual.period.eq('testing')&annual.relative_class.eq(row['relative_class'])&annual.quota_fraction.eq(row['selected_quota_fraction'])];item.update(testing_grain_retention_pct=float(s.grain_retention_pct),testing_retention_criterion_met=bool(s.grain_retention_pct>=row['grain_retention_target_pct']),testing_n_harvest_years=int(s.n_harvest_years),testing_n_years_meeting_criterion=int(y.grain_retention_pct.ge(row['grain_retention_target_pct']).sum()),testing_annual_retention_min_pct=float(y.grain_retention_pct.min()),testing_annual_retention_max_pct=float(y.grain_retention_pct.max()))
   for m in METRICS:item['testing_mean_'+m]=float(s['mean_'+m]);item['testing_full_'+m]=float(s['mean_full_'+m]);item['testing_paired_delta_'+m]=float(s['paired_delta_'+m])
  else:item.update(testing_grain_retention_pct=np.nan,testing_retention_criterion_met=None,testing_n_harvest_years=0,testing_n_years_meeting_criterion=0)
  evaluation.append(item)
 e=pd.DataFrame(evaluation)
 if persist:e.to_csv(ROOT/'tables/selected_quota_testing_evaluation.csv',index=False)
 return selected,e


def existing_allocation_classes(c):
 rotation=pd.read_csv(ROOT/'source_snapshots/model/rotation_summaries.csv');allocation=pd.read_csv(ROOT/'source_snapshots/model/frozen_training_allocation.csv');h=rotation[rotation.harvest_year.isin(YEARS)].merge(c[['representative_id','harvest_year','relative_class','period']],on=['representative_id','harvest_year'],validate='many_to_one');rows=[]
 for reduction in [.25,.5,.75]:
  for kind in ['uniform','targeted']:
   q=h.merge(allocation[allocation.reduction_fraction.eq(reduction)],on=['representative_id','fraction'],validate='many_to_one');q['share']=q.area_share if kind=='targeted' else q.fraction.eq(1-reduction).astype(float);q['active_area']=q.represented_area_ha*q.share
   for (period,clas,year),g in q.groupby(['period','relative_class','harvest_year']):
    a=g.active_area;row=dict(period=period,relative_class=clas,harvest_year=int(year),policy=f'{kind}_{int(reduction*100)}pct',class_area_ha=float(a.sum()),grain_production_t=float((a*g.yield_kg_ha).sum()/1000),field_irrigation_m3=float((a*g.irrigation_mm).sum()*10),crop_et_volume_m3=float((a*g.crop_et_mm).sum()*10),modeled_crop_plus_fallow_et_volume_m3=float((a*g.modeled_total_et_mm).sum()*10));rows.append(row)
 out=pd.DataFrame(rows);out.to_csv(ROOT/'tables/existing_frozen_allocations_by_class_year.csv',index=False)
 summary=[]
 for (period,clas,policy),g in out.groupby(['period','relative_class','policy']):
  a=g.class_area_ha.sum();summary.append(dict(period=period,relative_class=clas,policy=policy,n_harvest_years=int(g.harvest_year.nunique()),mean_grain_kg_ha=float(g.grain_production_t.sum()*1000/a),mean_field_irrigation_mm=float(g.field_irrigation_m3.sum()/(a*10)),mean_crop_et_mm=float(g.crop_et_volume_m3.sum()/(a*10)),mean_modeled_crop_plus_fallow_et_mm=float(g.modeled_crop_plus_fallow_et_volume_m3.sum()/(a*10))))
 pd.DataFrame(summary).to_csv(ROOT/'tables/existing_frozen_allocations_by_class.csv',index=False)


def summaries_and_candidates():
 monthly=pd.read_csv(ROOT/'data/observed_regional_twsa_monthly.csv');storage=pre_storage(monthly);storage.to_csv(ROOT/'data/pre_sowing_storage_availability.csv',index=False);climate=antecedent_weather();c,threshold=memberships(climate,storage);c.to_csv(ROOT/'data/class_memberships.csv',index=False);q=joined_predictions(c);q.to_csv(ROOT/'data/class_fixed_quota_predictions.csv',index=False);responses,annual=response_tables(q);selected,evaluation=select_candidates(responses,annual);existing_allocation_classes(c)
 counts=[]
 for (period,clas),g in c.groupby(['period','relative_class']):counts.append(dict(period=period,relative_class=clas,n_representative_years=len(g),n_harvest_years=int(g.harvest_year.nunique()),class_area_year_ha=float(g.represented_area_ha.sum()),fraction_of_period_area_year=float(g.represented_area_ha.sum()/c[c.period.eq(period)].represented_area_ha.sum())))
 pd.DataFrame(counts).to_csv(ROOT/'tables/class_counts_and_area_exposure.csv',index=False);write_json(ROOT/'audit/frozen_thresholds_and_candidates.json',dict(primary_thresholds=threshold,training_selected_candidates=safe(selected.to_dict('records')),testing_evaluation=safe(evaluation.to_dict('records')),primary_retention_target_fraction=.95,sensitivity_retention_target_fractions=[.90,.98],area_weighting='Representativephysicalcroparea×harvestyear; TWSthreshold uses11annualregionalstates withequalyearweight',precipitation_window='July1–September30 inyearprecedingharvest; all92days strictlybeforeOct12sowing',main_GRACE_window='August/September ofyearprecedingharvest; meanavailablemonthlysolutions withmin1; nointerpolation orzero filling',interpretation='Relativeantecedenthydroclimaticclasses anddescriptivefixedquotaresponse; notabsolute water scarcity, groundwater classification orcontinuousadaptivepolicy evaluation'))
 print('TABLESREADY',threshold,flush=True);print(evaluation[evaluation.primary_criterion].to_string(index=False),flush=True)
 return monthly,climate,c,responses,annual,selected,evaluation,threshold


def sensitivity_tables(monthly,climate,main_classes):
 scenarios=[]
 for tq in [.25,.5,.75]:
  for pq in [.25,.5,.75]:scenarios.append((f'storage_q{int(tq*100)}_precip_q{int(pq*100)}',(8,9),1,'observed_twsa_mm',tq,pq))
 scenarios += [('aug_sep_both_required',(8,9),2,'observed_twsa_mm',.5,.5),('july_sep_available',(7,8,9),1,'observed_twsa_mm',.5,.5),('unmasked_support_sensitivity',(8,9),1,'unmasked_same_domain_twsa_mm',.5,.5)]
 rows=[];thresholds=[]
 for name,months,required,column,tq,pq in scenarios:
  c,th=memberships(climate,pre_storage(monthly,months,required,column),tq,pq);q=joined_predictions(c);r,a=response_tables(q,persist=False);s,e=select_candidates(r,a,persist=False);different=c.relative_class.to_numpy()!=main_classes.relative_class.to_numpy();thresholds.append(dict(sensitivity=name,**th,storage_months=','.join(map(str,months)),storage_required_months=required,storage_support=('unmasked' if column.startswith('unmasked') else 'CSR_landmask'),testing_storage_available_years=int(c[c.period.eq('testing')&c.class_available].harvest_year.nunique()),total_area_year_fraction_changed_vs_main=float(np.sum(c.represented_area_ha*different)/c.represented_area_ha.sum())))
  for row in e.to_dict('records'):rows.append(dict(sensitivity=name,**row))
 pd.DataFrame(thresholds).to_csv(ROOT/'tables/threshold_and_window_sensitivity.csv',index=False);all_e=pd.DataFrame(rows);all_e.to_csv(ROOT/'tables/candidate_selection_sensitivity.csv',index=False)
 ranges=[]
 for (clas,target),g in all_e.groupby(['relative_class','grain_retention_target_pct']):ranges.append(dict(relative_class=clas,grain_retention_target_pct=target,minimum_training_selected_quota_fraction=float(g.selected_quota_fraction.min()),maximum_training_selected_quota_fraction=float(g.selected_quota_fraction.max()),n_declared_sensitivity_scenarios=len(g),testing_grain_retention_minimum_pct=float(g.testing_grain_retention_pct.min()) if g.testing_grain_retention_pct.notna().any() else np.nan,testing_grain_retention_maximum_pct=float(g.testing_grain_retention_pct.max()) if g.testing_grain_retention_pct.notna().any() else np.nan,interpretation='Descriptivedeclaredthreshold/window/retentioncriteria sensitivity; no confidence interval andnoresponse-basedselectionoftheprimarydefinition'))
 pd.DataFrame(ranges).to_csv(ROOT/'tables/selection_sensitivity_ranges.csv',index=False)


def make_figures(monthly,climate,c,responses,annual,selected,evaluation,threshold):
 import matplotlib
 matplotlib.use('Agg')
 import matplotlib.pyplot as plt
 from matplotlib.lines import Line2D
 from matplotlib.patches import Polygon as PolygonPatch
 from shapely.geometry import shape
 import matplotlib.dates as mdates
 plt.rcParams.update({'font.family':'DejaVu Sans','font.size':9,'axes.labelsize':9,'axes.titlesize':10,'axes.linewidth':.7,'xtick.labelsize':8,'ytick.labelsize':8,'pdf.fonttype':42,'ps.fonttype':42,'axes.spines.top':False,'axes.spines.right':False})
 colors={'lower_storage_dry':'#BB5C38','lower_storage_wet':'#D0A63F','higher_storage_dry':'#6163A6','higher_storage_wet':'#2D7F83'};blue='#31688E';orange='#BA703C';grey='#777777'
 def label(ax,index):ax.text(.01,.99,f'({index})',transform=ax.transAxes,ha='left',va='top',weight='bold',fontsize=11)
 def export(fig,name):fig.savefig(ROOT/f'figures/{name}.png',dpi=600,bbox_inches='tight');fig.savefig(ROOT/f'figures/{name}.pdf',bbox_inches='tight');plt.close(fig)
 # Actual geographic polygons; class marks are32actualrepresentativecells, not independentGRACE pixels.
 geo=json.loads((ROOT/'source_snapshots/operational_plain_geometry.geojson').read_text());geom=shape(geo['features'][0]['geometry']) if geo.get('type')=='FeatureCollection' else shape(geo['geometry']) if geo.get('type')=='Feature' else shape(geo)
 polygons=list(geom.geoms) if geom.geom_type=='MultiPolygon' else [geom]
 fig,axs=plt.subplots(1,2,figsize=(8.8,5.2),layout='constrained')
 for ax,y,panel in zip(axs,[2005,2020],['a','b']):
  for poly in polygons:
   xy=np.asarray(poly.exterior.coords);ax.add_patch(PolygonPatch(xy,facecolor='#eeeeee',edgecolor='#999999',lw=.35))
  q=c[c.harvest_year.eq(y)]
  for clas,g in q.groupby('relative_class'):ax.scatter(g.longitude,g.latitude,s=42,marker='^' if clas.endswith('dry') else 'o',c=colors[clas],edgecolors='white',linewidths=.45,zorder=3)
  storage=float(q.pre_sowing_twsa_mm.iloc[0]);state='Higher storage' if q.relative_storage.iloc[0]=='higher_storage' else 'Lower storage';ax.set_title(f'{y}: {state}\nRegional pre-season TWSA {storage:.1f} mm',pad=9);ax.set_xlim(112.1,122);ax.set_ylim(31.9,41.1);ax.set_aspect(1/np.cos(np.deg2rad(36)));ax.set_xlabel('Longitude (°E)');ax.set_ylabel('Latitude (°N)');ax.set_xticks([113,116,119,122]);ax.set_yticks([32,35,38,41]);ax.grid(alpha=.16,lw=.45);label(ax,panel)
  features=[]
  for row in q.to_dict('records'):
   props={k:row[k] for k in ['representative_id','zone_id','harvest_year','represented_area_ha','relative_class','pre_sowing_twsa_mm','antecedent_precipitation_mm','antecedent_p_et0_ratio']};props['GRACE_is_shared_regional_state']=True;features.append({'type':'Feature','geometry':{'type':'Point','coordinates':[row['longitude'],row['latitude']]},'properties':props})
  write_json(ROOT/f'data/class_map_{y}.geojson',dict(type='FeatureCollection',features=features))
 handles=[Line2D([],[],marker='^' if x.endswith('dry') else 'o',ls='',color=colors[x],label=CLASS_LABEL[x],markersize=6) for x in CLASS_ORDER];fig.legend(handles=handles,loc='outside lower center',ncol=2,frameon=False,fontsize=8);export(fig,'preseason_classes_map')
 # Raw observed monthly record retains missing-month gaps; no visual interpolation.
 fig,axs=plt.subplots(2,1,figsize=(9,6.2),layout='constrained');ax=axs[0];m=monthly[monthly.nominal_month.le('2025-12')].set_index(pd.to_datetime(monthly[monthly.nominal_month.le('2025-12')].nominal_month))['observed_twsa_mm'];m=m.reindex(pd.date_range('2002-04-01','2025-12-01',freq='MS'));ax.plot(m.index,m.values,color='#8D9CA3',lw=.85,label='Observed monthly TWSA');storage=pd.read_csv(ROOT/'data/pre_sowing_storage_availability.csv');dates=pd.to_datetime(storage.scenario_sowing_date);ax.scatter(dates,storage.pre_sowing_twsa_mm,s=25,c=np.where(storage.period.eq('training'),blue,orange),zorder=3,label='Pre-season observed mean');ax.axhline(threshold['storage_threshold_mm'],color='#333333',ls='--',lw=.9,label='Training median');ax.axvspan(pd.Timestamp('2002-07-01'),pd.Timestamp('2013-01-01'),color=blue,alpha=.05);ax.set_ylabel('Regional TWS anomaly (mm)');ax.set_xlim(pd.Timestamp('2002-01-01'),pd.Timestamp('2025-12-31'));ax.set_xticks(pd.to_datetime(['2002-01-01','2006-01-01','2010-01-01','2014-01-01','2018-01-01','2022-01-01','2025-12-01']));ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y'));ax.set_xlabel('Observation calendar year');ax.legend(frameon=False,ncol=3,fontsize=8,loc='lower left');label(ax,'a');ax.grid(axis='y',alpha=.15)
 ax=axs[1];means=[];bounds=[]
 for y,g in climate.groupby('harvest_year'):
  means.append(np.average(g.antecedent_precipitation_mm,weights=g.represented_area_ha));bounds.append([weighted_quantile(g.antecedent_precipitation_mm,g.represented_area_ha,.1),weighted_quantile(g.antecedent_precipitation_mm,g.represented_area_ha,.9)])
 bounds=np.array(bounds);ax.fill_between(YEARS,bounds[:,0],bounds[:,1],color=blue,alpha=.12,label='Spatial 10th–90th percentiles');ax.plot(YEARS,means,color=blue,marker='o',markersize=3,lw=1,label='Area-weighted antecedent precipitation');ax.axhline(threshold['precipitation_threshold_mm'],color='#333333',ls='--',lw=.9,label='Training weighted median');ax.axvspan(2002.5,2013.5,color=blue,alpha=.04);ax.set_xlim(2002.5,2025.5);ax.set_xticks([2003,2007,2011,2014,2018,2022,2025]);ax.set_ylabel('July–September precipitation (mm)');ax.set_xlabel('Following winter-wheat harvest year');ax.legend(frameon=False,ncol=3,fontsize=7.7,loc='upper left');ax.grid(axis='y',alpha=.15);label(ax,'b');export(fig,'preseason_storage_and_precipitation')
 # Fixed-history grain responses, including training-only higher-storage classes.
 fig,axs=plt.subplots(2,2,figsize=(9.2,6.5),layout='constrained')
 for ax,clas,panel in zip(axs.flat,CLASS_ORDER,['a','b','c','d']):
  for period,color,style in [('training',blue,'-'),('testing',orange,'--')]:
   q=responses[responses.relative_class.eq(clas)&responses.period.eq(period)].sort_values('quota_fraction')
   if len(q):ax.plot(q.quota_fraction*100,q.grain_retention_pct,marker='o',ms=4,color=color,ls=style,label=f'{period.capitalize()} mean ({int(q.n_harvest_years.iloc[0])} years)');ax.fill_between(q.quota_fraction*100,q.annual_grain_retention_pct_minimum,q.annual_grain_retention_pct_maximum,color=color,alpha=.08)
  candidate=selected[selected.relative_class.eq(clas)&selected.primary_criterion].iloc[0];ax.axvline(candidate.selected_quota_fraction*100,color='#666666',ls=':',lw=.8);ax.axhline(95,color='#333333',ls='--',lw=.8);ax.set_xlim(-3,103);ax.set_ylim(20,105);ax.set_xticks([0,25,50,75,100]);ax.set_ylabel('Grain retention (%)');ax.set_xlabel('Irrigation fraction (%)');ax.set_title(CLASS_LABEL[clas]);ax.grid(axis='y',alpha=.15);ax.legend(frameon=False,fontsize=7.7,loc='lower right');label(ax,panel)
  if clas.startswith('higher'):ax.text(.04,.13,'No testing class exposure',transform=ax.transAxes,fontsize=8,color=grey)
 export(fig,'class_fixed_quota_responses')
 # Primary95%-trainingcriterion only; sensitivity choices are retained in tables.
 fig,axs=plt.subplots(2,2,figsize=(9.2,6.5),layout='constrained');names=['Lower storage\nDry','Lower storage\nWet','Higher storage\nDry','Higher storage\nWet'];main=selected[selected.primary_criterion].set_index('relative_class').loc[CLASS_ORDER];ax=axs[0,0];bars=ax.bar(range(4),main.selected_quota_fraction*100,color=[colors[c] for c in CLASS_ORDER],width=.65);ax.set_xticks(range(4),names);ax.set_ylim(0,100);ax.set_ylabel('Selected irrigation fraction (%)');ax.set_title('Training-selected candidates');ax.grid(axis='y',alpha=.15);label(ax,'a')
 for bar,v in zip(bars,main.field_irrigation_mm):ax.text(bar.get_x()+bar.get_width()/2,bar.get_height()+2,f'{v:.0f} mm',ha='center',fontsize=8)
 test=evaluation[evaluation.primary_criterion&evaluation.testing_class_available].set_index('relative_class').loc[CLASS_ORDER[:2]];ax=axs[0,1];x=np.arange(2);means=test.testing_grain_retention_pct.to_numpy();low=test.testing_annual_retention_min_pct.to_numpy();high=test.testing_annual_retention_max_pct.to_numpy();ax.errorbar(x,means,yerr=np.vstack([means-low,high-means]),fmt='o',color=blue,ms=6,capsize=4,lw=1,label='Mean and annual range');ax.axhline(95,color='#333333',ls='--',lw=.9,label='95% training target');ax.set_xticks(x,['Lower storage\nDry','Lower storage\nWet']);ax.set_xlim(-.5,1.5);ax.set_ylim(0,105);ax.set_ylabel('Grain retention (%)');ax.set_title('Testing: unchanged candidates');ax.grid(axis='y',alpha=.15);ax.legend(frameon=False,fontsize=7.8,loc='lower left');label(ax,'b')
 for i,mean in enumerate(means):ax.annotate(f'{mean:.1f}%',(i,mean),xytext=(8,-9),textcoords='offset points',fontsize=8)
 ax=axs[1,0];width=.33
 for offset,kind,color in [(-width/2,'full',grey),(width/2,'mean',blue)]:
  crop=test[f'testing_{kind}_crop_et_mm'].to_numpy();total=test[f'testing_{kind}_modeled_total_et_mm'].to_numpy();ax.bar(x+offset,crop,width,color=color,label='Full irrigation: crop ET' if kind=='full' else 'Selected candidate: crop ET');ax.bar(x+offset,total-crop,width,bottom=crop,color=color,alpha=.3,hatch='///',label='Fallow ET' if kind=='full' else None)
 ax.set_xticks(x,['Lower storage\nDry','Lower storage\nWet']);ax.set_ylim(0,1150);ax.set_ylabel('Actual ET (mm)');ax.set_title('Testing: crop + fallow ET');ax.legend(frameon=False,fontsize=7.4,loc='upper right');ax.grid(axis='y',alpha=.15);label(ax,'c')
 ax=axs[1,1];ax.bar(x-width/2,test.testing_full_irrigation_mm,width,color=grey,label='Full irrigation');ax.bar(x+width/2,test.testing_mean_irrigation_mm,width,color=blue,label='Selected candidate');ax.set_xticks(x,['Lower storage\nDry','Lower storage\nWet']);ax.set_ylabel('Field irrigation (mm)');ax.set_ylim(0,440);ax.set_title('Testing: applied irrigation');ax.legend(frameon=False,fontsize=7.8,loc='upper right');ax.grid(axis='y',alpha=.15);label(ax,'d');export(fig,'training_selected_quota_evaluation')
 captions={
 'preseason_classes_map':'Pre-season hydroclimatic classes for (a) the 2005 training harvest year and (b) the 2020 testing harvest year. Points show 32 actual AgERA5 representative source cells. July–September precipitation in the preceding calendar year is below or above the frozen training weighted median of 431.74 mm. August/September regional total-water-storage anomaly (TWSA) is below or above the training median of 13.3823 mm. The same GRACE storage state applies to the entire study area in each year; point colors do not represent independent GRACE observations. The operational study-area outline provides geographic context. CSR land-mask storage support is 395,969.0 km² within the 404,657.9 km² outline. Classes indicate relative antecedent hydroclimatic conditions; groundwater components and absolute water scarcity are unresolved.',
 'preseason_storage_and_precipitation':'(a) Observed CSR monthly TWSA and available August/September pre-season means. Missing months remain gaps; the horizontal reference is the training 2003–2013 harvest-year median. Observation calendar dates are displayed; pre-season states relate to the following winter-wheat harvest year. (b) Area-weighted July–September antecedent precipitation at 32 representatives, with spatial 10th–90th percentiles and the frozen training weighted median. Bands describe represented spatial climate variation, not confidence intervals. September GRACE publication before October 12 sowing is unverified; the indicators describe retrospective pre-season measurements.',
 'class_fixed_quota_responses':'(a–d) Area and year weighted winter-wheat plus summer-maize dry-grain retention under five fixed irrigation histories, relative to the matched 100% irrigation history. Solid lines show training 2003–2013 class means; dashed lines show testing 2014–2025 class means where regional storage is available. Shading shows the minimum–maximum range of annual class means, not a confidence interval. The 95% reference and vertical training-selected irrigation fraction use training data only. Four classes occur in training; only lower-storage dry and wet classes occur in nine testing years. Testing harvest years 2014, 2018 and 2019 lack August/September GRACE; 2017 uses August only. Responses describe fixed irrigation histories with continuous soil state; dynamic class-switching management has not been simulated.',
 'training_selected_quota_evaluation':'(a) Least irrigation among five quotas retaining at least 95% of training class-mean full-irrigation dry grain: 50%, 25%, 75% and 25% for lower-storage dry/wet and higher-storage dry/wet conditions, respectively. (b) The unchanged lower-storage candidates are evaluated over nine available testing years. Means represent matched area and year weighted grain retention; bars show annual class-mean ranges, not confidence intervals. Dry 50% irrigation retains 91.71% (annual range 84.50–100%; 3/9 years at least 95%); wet 25% irrigation retains 95.23% (67.27–100%; 6/9 years at least 95%). Higher-storage classes have no testing exposure. (c) Crop actual ET plus separately hatched fallow ET under full and selected fixed irrigation histories; (d) applied field irrigation. Testing harvest years 2014, 2018 and 2019 lack pre-season GRACE; 2017 uses August only. Soil state remains continuous within each original fixed irrigation history. Dynamic class-switching management has not been simulated, and mean retention does not guarantee annual performance.'}
 for name,caption in captions.items():(ROOT/f'figures/{name}_caption.txt').write_text(caption+'\n')


def finish(c,responses,evaluation,threshold):
 checks=[]
 def check(name,passed,**e):checks.append(dict(check=name,passed=bool(passed),**e))
 check('Exactly32representatives23harvestyears',len(c)==32*23 and c.groupby('harvest_year').size().eq(32).all())
 check('Precipitation92daysstrictlybeforeOctober12sowing',c.n_antecedent_days.eq(92).all() and (pd.to_datetime(c.antecedent_end_date)<pd.to_datetime(c.scenario_sowing_date)).all())
 check('OneGRACEstateperyearsharedacrossall32points',c.groupby('harvest_year').pre_sowing_twsa_mm.nunique(dropna=False).eq(1).all())
 check('Missingstorage2014_2018_2019unfilled',sorted(c[~c.class_available].harvest_year.unique())==[2014,2018,2019] and c[~c.class_available].pre_sowing_twsa_mm.isna().all())
 check('2017partialAugustonlyexplicit',c[c.harvest_year.eq(2017)].storage_n_observed_months.eq(1).all() and c[c.harvest_year.eq(2017)].storage_observed_nominal_months.eq('2016-08').all())
 check('All4trainingbutonly2testingclasses',set(c[c.period.eq('training')].relative_class)==set(CLASS_ORDER) and set(c[c.period.eq('testing')&c.class_available].relative_class)==set(CLASS_ORDER[:2]))
 check('Everyfullquotapairedgrainretention100pct',np.allclose(responses[responses.quota_fraction.eq(1.)].grain_retention_pct,100))
 check('FieldIis380mm×quota',np.allclose(responses.mean_irrigation_mm,380*responses.quota_fraction))
 check('CropfallowETatleastcropET',np.all(responses.mean_modeled_total_et_mm>=responses.mean_crop_et_mm))
 check('ThresholdTWSusesonlytrainingstates',abs(threshold['storage_threshold_mm']-c[c.period.eq('training')].drop_duplicates('harvest_year').pre_sowing_twsa_mm.median())<1e-10)
 train=c[c.period.eq('training')];check('ThresholdPusesonlytrainingareaweightedantecedentP',threshold['precipitation_threshold_mm']==weighted_quantile(train.antecedent_precipitation_mm,train.represented_area_ha,.5))
 for row in evaluation[evaluation.primary_criterion].to_dict('records'):
  q=responses[responses.period.eq('training')&responses.relative_class.eq(row['relative_class'])];allowed=q[q.grain_retention_pct.ge(95)];check('Leastfeasibletrainingquota_'+row['relative_class'],row['selected_quota_fraction']==allowed.quota_fraction.min() and not row['selection_uses_testing_responses'])
 for name in ['preseason_classes_map','preseason_storage_and_precipitation','class_fixed_quota_responses','training_selected_quota_evaluation']:check('PNGandPDF_'+name,(ROOT/f'figures/{name}.png').stat().st_size>10000 and (ROOT/f'figures/{name}.pdf').stat().st_size>10000)
 check('Dry95trainingcandidatefailsheldoutcriterion',evaluation[evaluation.primary_criterion&evaluation.relative_class.eq('lower_storage_dry')].testing_retention_criterion_met.iloc[0]==False)
 check('Wet95trainingcandidatemeetsclassmeanheldoutcriterion',evaluation[evaluation.primary_criterion&evaluation.relative_class.eq('lower_storage_wet')].testing_retention_criterion_met.iloc[0]==True)
 total_area=c[c.harvest_year.eq(2003)].represented_area_ha.sum();check('Staticphysicalcropareafromnativecontextpreserved',abs(total_area-9866601.682522975)<1e-6)
 source_grace=ROOT/'source_snapshots/grace';catalog=pd.read_csv(PROJECT/'data/grace/catalog/files.csv')
 for name in ['CSR_GRACE_GRACE-FO_RL0603_Mascons_all-corrections.nc','CSR_GRACE_GRACE-FO_RL0603_mascons_mapping_file.nc','CSR_GRACE_GRACE-FO_RL06_Mascons_v02_LandMask.nc']:
  expected=catalog[catalog.relative_path.str.endswith(name)].sha256.iloc[0];check('RawobservedCSRsourceSHA_'+name,sha(source_grace/name)==expected)
 checks_passed=all(x['passed'] for x in checks);write_json(ROOT/'audit/quality_checks.json',dict(all_checks_passed=checks_passed,check_count=len(checks),checks=checks,status='relativehydroclimaticstratification_andconditionalfixedhistoryresponses',real_time_release_availability_verified=False,GRACE_groundwater_decomposition=False,dynamic_class_switching_simulated=False,model_or_threshold_fit_to_testing_outcomes=False,probability_based_class_or_strategy_uncertainty=False));assert checks_passed
 report=f'''Relative pre-season hydroclimatic classes and conditional irrigation responses

One CSR RL06.3 total-water-storage anomaly series covers the study area, with official land-mask support of 395,969.0 km². Total storage includes groundwater, soil water, surface water and snow; groundwater has not been separated. CSR estimates on approximately 120 km mascons have an effective resolution near 300 km. Spatial precipitation classes describe 32 actual AgERA5 source cells representing 9,866,601.7 ha of the fixed 2020 mapped wheat–maize footprint. The operational study area lacks certification as an official North China Plain boundary.

Training harvest years 2003–2013 define an August/September regional storage median of {threshold['storage_threshold_mm']:.6f} mm and an area-weighted July–September precipitation median of {threshold['precipitation_threshold_mm']:.2f} mm in the preceding calendar year. Values below the medians define lower-storage and dry conditions; equality belongs to the higher category. All measurement windows end before the declared October 12 wheat sowing. Historical measurement dates precede sowing, whereas publication before sowing remains unverified. The indicator therefore describes retrospective pre-season conditions.

Four relative classes occur in training. All available testing pre-season storage values lie below the training median, leaving two populated testing classes. Harvest years 2014, 2018 and 2019 lack August/September GRACE and remain unclassified. Harvest year 2017 has August only. Missing storage values remain empty.

The primary selection criterion requires at least 95% of training class-mean full-irrigation dry grain. The least-irrigation candidates among five fixed histories are 50% irrigation for lower-storage dry conditions, 25% for lower-storage wet conditions, 75% for higher-storage dry conditions and 25% for higher-storage wet conditions. Training retention for the lower-storage dry candidate is 95.077%, only 0.077 percentage points above the criterion. Storage-window, threshold and 90%/98% retention sensitivities remain separate from the primary definition.

In nine available testing years, the dry 50% candidate retains 91.707% of matched full-irrigation grain. Annual class means range from 84.503% to 100%, with three of nine years meeting 95%. Crop actual ET is 743.985 mm versus 802.442 mm under full irrigation; crop + fallow ET is 757.011 versus 815.686 mm. Field irrigation is 190 versus 380 mm. The wet 25% candidate retains 95.225% on average. Annual class means range from 67.268% to 100%, with six of nine years meeting 95%. Crop actual ET is 788.792 versus 826.924 mm; crop + fallow ET is 799.489 versus 837.698 mm. Field irrigation is 95 versus 380 mm. Higher-storage candidates have no testing exposure. Class-mean retention does not guarantee annual performance.

Grain represents modeled dry matter from frozen shared crop cards at native commit 84457b3. The five irrigation histories carry continuous soil state from 1996 spinup through wheat, maize and fallow. Class grouping preserves these histories. Year-to-year switching between irrigation fractions requires separate continuous-state simulations. The crop model contains no GRACE-informed groundwater reservoir or aquifer capillary-rise response. GRACE storage supplies regional context rather than simulated irrigation water. The comparisons support conditional response analysis; causal storage effects, independent field validation and operational policy performance remain unresolved.

Interannual ranges and declared threshold sensitivities describe conditional variation without statistical confidence or probability interpretation. Transferred hydraulics, frozen cultivar responses, uniform calendars and medoid approximation remain material model limitations. The native source-cell approximation checks and published maize benchmark provide incomplete evidence for regional irrigation-response accuracy. The final 2025 crop + fallow cycle ends October 5 rather than October 11.

CSR source and scale guidance: https://www2.csr.utexas.edu/grace/RL0603_mascons.html
Dataset DOI: 10.15781/cgq9-nh24
'''
 (ROOT/'REPORT.txt').write_text(report)
 records=[]
 for f in sorted(ROOT.rglob('*')):
  if f.is_file() and f.name not in ['file_manifest.json','completion.json'] and '__pycache__' not in f.parts:records.append(dict(path=str(f.relative_to(ROOT)),bytes=f.stat().st_size,sha256=sha(f)))
 write_json(ROOT/'audit/file_manifest.json',dict(sealed_utc=datetime.now(timezone.utc).isoformat(),n_files=len(records),files=records,manifest_itself_excluded=True));write_json(ROOT/'audit/completion.json',dict(completed_utc=datetime.now(timezone.utc).isoformat(),quality_checks_passed=True,n_sealed_files=len(records),run_status='conditional_research_analysis_complete',primary_selection_fixed_before_testing=True,primary_definition_changed_to_improve_testing=False))
 print('COMPLETED',ROOT,'CHECKS',len(checks),'FILES',len(records),flush=True)


def main():
 import argparse
 global ROOT
 parser=argparse.ArgumentParser();parser.add_argument('--output-dir',type=Path,default=ROOT);args=parser.parse_args();ROOT=args.output_dir.resolve()
 if (ROOT/'audit/completion.json').exists():raise FileExistsError('Completedclassanalysisisimmutable;use--output-dirforaNEWrunfolder')
 for sub in ['analysis_source','data','source_snapshots/grace','source_snapshots/model','figures','tables','audit']:(ROOT/sub).mkdir(parents=True,exist_ok=True)
 if ROOT!=Path(__file__).resolve().parents[1]:shutil.copy2(__file__,ROOT/'analysis_source/build_classes.py')
 sources_and_storage();results=summaries_and_candidates();monthly,climate,c,responses,annual,selected,evaluation,threshold=results;sensitivity_tables(monthly,climate,c);make_figures(*results);finish(c,responses,evaluation,threshold)

if __name__=='__main__':main()
