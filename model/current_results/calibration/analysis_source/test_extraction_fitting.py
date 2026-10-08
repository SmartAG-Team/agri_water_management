from copy import deepcopy
import numpy as np
import pytest
import qualified_fit as base
from extraction_fit import specification, adjusted


def test_maize_fitting_exposes_bounded_extraction_parameter():
    names,lo,hi,center,prior=specification('maize')
    assert len(names)==12 and names[-1]=='log10_root_extraction_day'
    assert lo[-1]==-3. and hi[-1]==0.
    assert center[-1]==0. and prior[-1]==1.


def test_maize_extraction_reaches_hydrology_without_changing_other_inputs():
    obs,field,payloads,*_=base.qualified_data('maize')
    payload=next(iter(payloads.values()));original=deepcopy(payload)
    vector=np.r_[base.specification('maize')[3],np.log10(.03)]
    expected=base.adjusted(payload,vector[:-1],'maize')
    actual=adjusted(payload,vector,'maize')
    assert actual['parameters']['hydrology']['root_extraction_fraction_day']==pytest.approx(.03)
    actual['parameters']['hydrology']['root_extraction_fraction_day']=expected['parameters']['hydrology']['root_extraction_fraction_day']
    assert actual==expected and payload==original
    assert obs.site.nunique()==5 and obs.variable.ne('et').all()
    assert set(field.harvest_year)=={2016,2017,2018}


def test_wheat_parameter_contract_and_predictions_are_unchanged():
    assert specification('wheat')[0]==base.specification('wheat')[0]
    obs,field,payloads,*_=base.qualified_data('wheat')
    p=next(iter(payloads.values()));v=base.specification('wheat')[3]
    assert adjusted(p,v,'wheat')==base.adjusted(p,v,'wheat')


@pytest.mark.parametrize('vector',[np.ones(11),np.full(12,np.nan),np.r_[np.ones(11),-4.],np.r_[np.ones(11),.1]])
def test_invalid_maize_vector_cannot_reach_simulation(vector):
    with pytest.raises(ValueError):adjusted({'inputs':{'crop':'maize'}},vector,'maize')
