"""A measured surface peak can coexist with persistent deeper root activity."""
from dataclasses import replace
import math
import pytest
from core.hydrology.types import SoilLayerParameters,HydrologyParameters,SoilColumnState,DualDomainState,WaterDayForcing
from core.hydrology.balance import _root_activity,step_water_day


def parameters(widths=(100.,100.,200.,200.,400.),**kwargs):
    assert 'root_activity_background_fraction' in HydrologyParameters.__dataclass_fields__
    assert 'root_activity_reference_depth_mm' in HydrologyParameters.__dataclass_fields__
    layers=tuple(SoilLayerParameters(z,.05,.1,.3,.45,0.) for z in widths)
    return HydrologyParameters(layers,substeps=1,drainage_rate_day=0.,**kwargs)


def test_background_profile_reproduces_conditional_mass_integrals():
    p=parameters(root_density_decay_m_inv=12.,root_activity_background_fraction=.4)
    activity=_root_activity(p,1000.)
    top=0.;expected=[]
    for layer in p.layers:
        bottom=top+layer.thickness_mm
        shallow=(math.exp(-12*top/1000)-math.exp(-12*bottom/1000))/(1-math.exp(-12))
        expected.append(.6*shallow+.4*(bottom-top)/1000)
        top=bottom
    assert [a*l.thickness_mm/1000 for a,l in zip(activity,p.layers)]==pytest.approx(expected,abs=1e-12)
    assert expected[-1]>.16


@pytest.mark.parametrize('depth',[0.,35.,245.,1000.,2500.])
def test_activity_mean_and_root_front_limits_are_preserved(depth):
    p=parameters(root_density_decay_m_inv=12.,root_activity_background_fraction=.4)
    a=_root_activity(p,depth)
    assert all(math.isfinite(x) and x>=0. for x in a)
    represented=min(depth,1000.)
    assert sum(x*l.thickness_mm for x,l in zip(a,p.layers))==pytest.approx(represented,abs=1e-10)
    top=0.
    for x,l in zip(a,p.layers):
        if top>=represented:assert x==0.
        top+=l.thickness_mm


@pytest.mark.parametrize('normalization',['rooted_volume_mean','surface'])
@pytest.mark.parametrize('background',[0.,.4,1.])
def test_zero_decay_remains_uniform_rooted_volume(normalization,background):
    p=parameters(root_density_decay_m_inv=0.,root_activity_background_fraction=background,
                 root_activity_normalization=normalization)
    assert _root_activity(p,245.)==pytest.approx([1.,1.,45/200.,0.,0.],abs=1e-12)


def test_full_background_is_uniform_even_with_nonzero_surface_decay():
    p=parameters(root_density_decay_m_inv=17.,root_activity_background_fraction=1.)
    assert _root_activity(p,245.)==pytest.approx([1.,1.,45/200.,0.,0.],abs=1e-12)


def test_surface_normalization_does_not_strengthen_existing_shallow_roots():
    p=parameters(widths=(100.,400.,500.),root_density_decay_m_inv=12.,root_activity_background_fraction=.4,
                 root_activity_normalization='surface')
    assert _root_activity(p,500.)[:2]==pytest.approx(_root_activity(p,1000.)[:2],abs=1e-12)


def test_zero_background_replays_archived_exponential_fluxes():
    p=parameters(widths=(100.,900.),root_density_decay_m_inv=12.,root_extraction_fraction_day=.05)
    state=DualDomainState(1.,SoilColumnState([30.,270.]),None)
    forcing=WaterDayForcing('2020-01-01',root_depth_mm=1000.,potential_transpiration_mm=3.)
    old=step_water_day(state,forcing,p)
    explicit=step_water_day(state,forcing,replace(p,root_activity_background_fraction=0.,root_activity_reference_depth_mm=750.))
    assert old.state==explicit.state and old.fluxes==explicit.fluxes


def test_deep_background_can_supply_a_crop_above_dry_surface_soil():
    p=parameters(widths=(100.,900.),root_density_decay_m_inv=17.,root_extraction_fraction_day=.05)
    state=DualDomainState(1.,SoilColumnState([10.,270.]),None)
    forcing=WaterDayForcing('2020-01-01',root_depth_mm=1000.,potential_transpiration_mm=3.)
    a=step_water_day(state,forcing,p)
    b=step_water_day(state,forcing,replace(p,root_activity_background_fraction=.4))
    assert a.fluxes['transpiration_mm']<b.fluxes['transpiration_mm']==pytest.approx(3.)
    assert b.state.wet.water_mm[0]==10.
    assert state.storage_mm()-b.state.storage_mm()==pytest.approx(b.fluxes['transpiration_mm'],abs=1e-10)
    assert abs(b.balance_residual_mm)<1e-10


@pytest.mark.parametrize('normalization',['rooted_volume_mean','surface'])
@pytest.mark.parametrize('background',[.4,1.])
def test_accepted_tiny_decay_and_reference_have_stable_uniform_limit(normalization,background):
    p=parameters(root_density_decay_m_inv=1e-312,root_activity_background_fraction=background,
                 root_activity_reference_depth_mm=1e-9,root_activity_normalization=normalization)
    assert _root_activity(p,245.)==pytest.approx([1.,1.,45/200.,0.,0.],abs=1e-12)


@pytest.mark.parametrize('value',[-.1,1.1,float('nan'),float('inf'),True])
def test_invalid_background_fraction_is_rejected(value):
    with pytest.raises(ValueError):parameters(root_activity_background_fraction=value)


@pytest.mark.parametrize('value',[0.,-1.,float('nan'),float('inf'),True])
def test_invalid_reference_depth_is_rejected(value):
    with pytest.raises(ValueError):parameters(root_activity_reference_depth_mm=value)
