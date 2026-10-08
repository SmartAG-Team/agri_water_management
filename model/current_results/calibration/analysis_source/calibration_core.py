"""Calibration contracts and conservative-model observation accounting."""
from copy import deepcopy
from datetime import date
import math

import numpy as np
import pandas as pd

VARIABLES=['lai','biomass','harvest_biomass','yield','et']
BOUNDS=([.6,.6,.3,.01,-2.,.6],[1.8,1.4,1.8,1.5,0.,1.6])
NAMES=['rue_multiplier','sla_multiplier','transpiration_coefficient',
       'soil_evaporation_coefficient','log10_root_extraction_fraction_day','grain_number_multiplier']


def verify_baseline_replay(original,replay):
    if any(f.observation_id.isna().any() or f.observation_id.duplicated().any()
           for f in [original,replay]):
        raise ValueError('Replay requires unique observation IDs')
    if set(original.observation_id)!=set(replay.observation_id):
        raise ValueError('Replay observation identities differ')
    a=original.set_index('observation_id').loc[replay.observation_id]
    b=replay.set_index('observation_id')
    for column in ['case_id','variable','window_start','window_end','value']:
        if not a[column].equals(b[column]):
            raise ValueError('Replay target metadata differs: '+column)
    difference=float(np.max(np.abs(a.predicted.to_numpy()-b.predicted.to_numpy())))
    if not np.isfinite(difference) or difference>1e-8:
        raise ValueError(('Original baseline did not replay',difference))
    return difference


def require_calibration(frame):
    if frame.empty or not frame.split.eq('calibration').all():
        raise ValueError('Only nonempty calibration records can fit or scale parameters')


def matched_seasonal_means(frame,bin_width):
    if bin_width<=0:raise ValueError('Positive seasonal bin width required')
    f=frame.copy()
    f['bin']=(f.das//bin_width).astype(int)
    # Both channels retain exactly the same dates, plots and site-years.
    paired=f.groupby(['source_group_id','case_id','bin'])[['value','predicted']].mean()
    years=paired.groupby(['source_group_id','bin']).mean()
    return (years.value.groupby('bin').agg(['mean','std','count']),
            years.predicted.groupby('bin').agg(['mean','std','count']))


def balanced_weights(frame):
    if frame.empty:raise ValueError('Nonempty observation frame required')
    f=frame.reset_index(drop=True)
    if f[['variable','site','source_group_id','case_id']].isna().any().any():
        raise ValueError('Every observation needs a variable, site, year and case')
    w=np.zeros(len(f))
    for _,v in f.groupby('variable'):
        for _,s in v.groupby('site'):
            for _,y in s.groupby('source_group_id'):
                for _,c in y.groupby('case_id'):
                    w[c.index]=1/(f.variable.nunique()*v.site.nunique()*s.source_group_id.nunique()*y.case_id.nunique()*len(c))
    if abs(w.sum()-1)>1e-10:raise ValueError('Weights do not sum to one')
    return w


def inner_partitions(inventory):
    if inventory.empty:raise ValueError('Case inventory required')
    if inventory.groupby('source_group_id').split.nunique().gt(1).any():
        raise ValueError('An outer site-year crosses calibration/testing')
    labels={g:'outer_testing' for g in inventory.source_group_id.unique()}
    calibration=inventory[inventory.split.eq('calibration')]
    for _,site in calibration.groupby('site'):
        groups=sorted(site.source_group_id.unique(),key=lambda g:int(g.rsplit('-',1)[1]))
        if len(groups)<2:raise ValueError('At least two calibration years per site required')
        count=min(len(groups)-1,max(1,math.ceil(len(groups)*.25)))
        held=set(groups[-count:])
        labels.update({g:'inner_holdout' if g in held else 'inner_training' for g in groups})
    return labels


def window_predictions(daily,observations):
    dates=np.array([row['date'] for row in daily])
    if len(set(dates))!=len(dates) or (len(dates)>1 and not np.all(dates[1:]>dates[:-1])):
        raise ValueError('Daily predictions must have unique ordered dates')
    columns={'lai':'lai','biomass':'biomass_kg_ha','harvest_biomass':'biomass_kg_ha',
             'yield':'yield_kg_ha','et':'et_mm'}
    vectors={v:np.array([r[columns[v]] for r in daily],float) for v in observations.variable.unique()}
    values=[]
    for row in observations.itertuples():
        start=int(np.searchsorted(dates,row.window_start,side='left'))
        end=int(np.searchsorted(dates,row.window_end,side='right'))
        expected=(date.fromisoformat(row.window_end)-date.fromisoformat(row.window_start)).days+1
        selected=dates[start:end]
        if expected<1 or len(selected)!=expected or any((date.fromisoformat(b)-date.fromisoformat(a)).days!=1 for a,b in zip(selected,selected[1:])):
            raise ValueError('Incomplete daily coverage of observation window')
        values.append(float(vectors[row.variable][start:end].mean()))
    return np.array(values)


def adjust_parameters(parameters,vector):
    x=np.asarray(vector,float)
    if len(x)!=6 or not np.isfinite(x).all() or np.any(x<BOUNDS[0]) or np.any(x>BOUNDS[1]):
        raise ValueError('Joint parameter vector outside numerical calibration bounds')
    adjusted=deepcopy(parameters)
    crop=adjusted['crop'];profile=crop['profile']
    profile['rue_g_mj']*=x[0]
    profile['sla_max_m2_g']*=x[1]
    profile['sla_min_m2_g']*=x[1]
    profile['kernels_per_g_flowering_biomass']*=x[5]
    crop.update(transpiration_coefficient=float(x[2]),soil_evaporation_coefficient=float(x[3]))
    adjusted.setdefault('hydrology',{})['root_extraction_fraction_day']=float(10**x[4])
    return adjusted


def scales_from_calibration(frame):
    require_calibration(frame)
    scales={}
    for variable,g in frame.groupby('variable'):
        w=balanced_weights(g);value=g.value.to_numpy(float);mean=float(w@value)
        floor=.5 if variable in ['lai','et'] else 500.
        scales[variable]=max(floor,float(np.sqrt(w@((value-mean)**2))))
    return scales


def variable_metrics(frame):
    rows=[]
    for (crop,split,variable),g in frame.groupby(['crop','split','variable']):
        w=balanced_weights(g);o=g.value.to_numpy(float);s=g.predicted.to_numpy(float)
        mean=float(w@o);pmean=float(w@s)
        mse=float(w@((s-o)**2));var=float(w@((o-mean)**2));pvar=float(w@((s-pmean)**2))
        covariance=float(w@((o-mean)*(s-pmean)))
        rows.append(dict(crop=crop,split=split,variable=variable,n=len(g),n_sites=g.site.nunique(),
            n_site_years=g.source_group_id.nunique(),rmse=mse**.5,bias=float(w@(s-o)),
            nse=1-mse/var if var>1e-12 else None,
            r_squared=covariance**2/(var*pvar) if var*pvar>1e-12 else None))
    return pd.DataFrame(rows)
