"""Confirmed harvest dry-biomass constraints for separated-year calibration."""
import numpy as np
from calibration_core import balanced_weights


def confirmed_targets(frame, confirmation):
    required=dict(confirmed=True, quantity='aboveground_dry_biomass_including_grain',
                  units='kg_ha', measurement='ground_level_cut_oven_dried_constant_weight')
    if any(confirmation.get(k)!=v for k,v in required.items()):
        raise ValueError('Confirmed aboveground dry-mass measurement basis required.')
    if frame.empty or not frame.split.eq('calibration').all():
        raise ValueError('Only nonempty calibration records are eligible.')
    if frame.case_id.isna().any() or frame.case_id.duplicated().any():
        raise ValueError('Unique matched crop-season treatment identities required.')
    values=frame.observed_biomass_kg_ha.to_numpy(float)
    if not np.isfinite(values).all() or np.any(values<=0.):
        raise ValueError('Finite positive dry-mass observations required.')
    result=frame[['case_id','crop','site','source_group_id','harvest_year','treatment','split']].copy()
    result['variable']='harvest_biomass'
    result['value']=values
    return result


def residual_weights(frame):
    if not frame.split.eq('calibration').all():
        raise ValueError('Calibration-only weights required.')
    expected={'seasonal_et','yield','harvest_biomass'}
    if set(frame.variable)!=expected:
        raise ValueError('ET, grain yield and confirmed harvest biomass required.')
    weights=np.zeros(len(frame))
    original=frame.variable.isin(['seasonal_et','yield']).to_numpy()
    weights[original]=np.sqrt(.35*balanced_weights(frame.loc[original]))
    weights[~original]=np.sqrt(.15*balanced_weights(frame.loc[~original]))
    return weights
