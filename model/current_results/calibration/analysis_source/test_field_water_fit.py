from copy import deepcopy
import json
import numpy as np
import pytest
from field_water_fit import specification,full_vector,adjusted,field_data,WATER_NAMES,ROOT
import extraction_fit as legacy


@pytest.mark.parametrize('crop',['wheat','maize'])
def test_eight_water_coefficients_preserve_inherited_bounds_and_priors(crop):
    names,lo,hi,center,prior=specification(crop)
    old=legacy.specification(crop);indices=[old[0].index(n) for n in WATER_NAMES]
    assert names==WATER_NAMES and len(names)==8
    for a,b in zip([lo,hi,center,prior],old[1:]):assert np.array_equal(a,b[indices])


@pytest.mark.parametrize('crop',['wheat','maize'])
def test_new_water_fit_uses_only_documented_calibration_fields(crop):
    field,payloads,*_=field_data(crop)
    assert set(field.harvest_year)=={2016,2017,2018} and field.case_id.nunique()==12
    assert set(field.variable)=={'seasonal_et','yield','harvest_biomass'}
    assert len(payloads)==12 and all(k.startswith('yang2024') for k in payloads)


@pytest.mark.parametrize('crop',['wheat','maize'])
def test_growth_coefficients_and_forcing_remain_unchanged(crop):
    original=json.loads((ROOT/'source_snapshots/starting_parameters'/f'{crop}.json').read_text())
    names=legacy.specification(crop)[0];v=np.array(original['vector']);selected=[names.index(n) for n in WATER_NAMES]
    water=v[selected].copy();water[0]=max(.5,water[0]-.02)
    complete=full_vector(water,crop)
    for i,name in enumerate(names):
        if name not in WATER_NAMES:assert complete[i]==v[i]
    _,payloads,*_=field_data(crop);p=next(iter(payloads.values()));before=deepcopy(p)
    actual=adjusted(p,complete,crop)
    assert actual==legacy.adjusted(p,complete,crop) and p==before
    assert actual['inputs']==p['inputs']


@pytest.mark.parametrize('crop',['wheat','maize'])
@pytest.mark.parametrize('bad',[np.ones(7),np.full(8,np.nan),np.full(8,-999)])
def test_invalid_water_vectors_are_rejected(crop,bad):
    with pytest.raises(ValueError):full_vector(bad,crop)
