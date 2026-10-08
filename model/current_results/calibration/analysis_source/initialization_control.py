"""Conservative remapping of complete antecedent volumetric moisture profiles."""
from copy import deepcopy
from datetime import date

import numpy as np


def previous_month(start_date):
    start=date.fromisoformat(start_date)
    return (start.year-1,12) if start.month==1 else (start.year,start.month-1)


def remap_profile(percent,source_thickness,target_thickness):
    values=np.asarray(percent,float)
    source=np.asarray(source_thickness,float)
    target=np.asarray(target_thickness,float)
    if values.ndim!=1 or source.shape!=values.shape or target.ndim!=1 or not len(target):
        raise ValueError('Matching one-dimensional source profile and layer widths required')
    if not np.isfinite(values).all() or np.any(values<0.) or np.any(values>100.):
        raise ValueError('Finite volumetric percentages in [0,100] required')
    if not np.isfinite(source).all() or not np.isfinite(target).all() or np.any(source<=0.) or np.any(target<=0.):
        raise ValueError('Finite positive source and target widths required')
    if abs(source.sum()-target.sum())>1e-8:
        raise ValueError('Depth extrapolation is not permitted')
    se=np.r_[0.,source.cumsum()];te=np.r_[0.,target.cumsum()]
    overlap=np.maximum(0.,np.minimum(te[1:,None],se[None,1:])-np.maximum(te[:-1,None],se[None,:-1]))
    theta=(overlap@(values/100.))/target
    if abs(theta@target-(values/100.)@source)>1e-8:
        raise ArithmeticError('Profile-storage remapping does not conserve water')
    return theta


def apply_profile(payload,theta,year,month):
    if payload.get('presowing') or payload.get('initial_state'):
        raise ValueError('Existing carryover states require a separate initialization protocol')
    if previous_month(payload['inputs']['start_date'])!=(year,month):
        raise ValueError('The preceding complete calendar month is required')
    layers=payload['inputs']['soil_layers'];values=np.asarray(theta,float)
    if values.shape!=(len(layers),) or not np.isfinite(values).all():
        raise ValueError('Complete finite target profile required')
    if any(v<layer['air_dry']-1e-12 or v>layer['saturation']+1e-12 for v,layer in zip(values,layers)):
        raise ValueError('Measured profile conflicts with model physical bounds; no clipping allowed')
    p=deepcopy(payload);p['inputs']['initial_theta']=values.tolist()
    return p
