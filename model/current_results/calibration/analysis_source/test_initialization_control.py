from copy import deepcopy

import numpy as np
import pytest

from initialization_control import previous_month, remap_profile, apply_profile


def test_previous_month_excludes_crop_start_month():
    assert previous_month('2005-11-04') == (2005,10)
    assert previous_month('2005-01-01') == (2004,12)


def test_depth_overlap_mapping_conserves_storage():
    theta=remap_profile([10.,20.],[100.,100.],[80.,70.,50.])
    np.testing.assert_allclose(theta,[.1,12/70,.2])
    assert abs(np.dot(theta,[80.,70.,50.])-30.)<1e-12


@pytest.mark.parametrize('values',[[10.,np.nan],[10.,-1.],[10.,101.]])
def test_invalid_source_values_rejected(values):
    with pytest.raises(ValueError):remap_profile(values,[100.,100.],[100.,100.])


def test_depth_extrapolation_rejected():
    with pytest.raises(ValueError):remap_profile([10.,20.],[100.,100.],[100.,150.])


def payload():
    return {'inputs':{'start_date':'2005-11-04','initial_theta':[.2,.2],
        'soil_layers':[{'thickness_mm':100.,'air_dry':.05,'saturation':.5}]*2,
        'weather':[{'date':'2005-11-04','precipitation_mm':0.}],
        'irrigation_events':[{'date':'2005-11-04','amount_mm':20.}]},
        'parameters':{'hydrology':{'plant_water_stress_method':'compensated_layer_depletion'}}}


def test_only_initial_theta_changes_without_mutating_original():
    p=payload();before=deepcopy(p)
    changed=apply_profile(p,[.3,.4],2005,10)
    assert p==before and changed['inputs']['initial_theta']==[.3,.4]
    changed['inputs']['initial_theta']=p['inputs']['initial_theta']
    assert changed==p


def test_post_start_measurement_rejected():
    with pytest.raises(ValueError):apply_profile(payload(),[.3,.4],2005,11)


def test_out_of_bounds_measurement_rejected_without_clipping():
    with pytest.raises(ValueError):apply_profile(payload(),[.3,.51],2005,10)


def test_presowing_state_not_silently_overridden():
    p=payload();p['presowing']={'inputs':{'start_date':'2005-10-01'}}
    with pytest.raises(ValueError):apply_profile(p,[.3,.4],2005,9)
