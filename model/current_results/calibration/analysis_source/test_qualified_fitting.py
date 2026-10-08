import numpy as np
import pytest
from qualified_fit import qualified_data


@pytest.mark.parametrize('crop,sites', [('wheat', 4), ('maize', 5)])
def test_incomplete_log_ET_is_conditional_and_all_growth_sites_remain(crop, sites):
    obs, field, payloads, ws, ss, wf, fs, contrasts = qualified_data(crop)
    assert obs.variable.ne('et').all() and obs.site.nunique() == sites
    assert obs.split.eq('calibration').all() and field.split.eq('calibration').all()
    assert field.case_id.nunique() == 12 and set(field.harvest_year) == {2016, 2017, 2018}
    assert set(payloads) == set(obs.case_id) | set(field.case_id)


@pytest.mark.parametrize('crop', ['wheat', 'maize'])
def test_known_treatment_ET_receives_only_excluded_ET_contribution(crop):
    obs, field, payloads, ws, ss, wf, fs, contrasts = qualified_data(crop)
    assert np.sum(ws**2) == pytest.approx(.4)
    assert np.sum(wf[field.variable.eq('seasonal_et')]**2) == pytest.approx(.325)
    assert np.sum(wf[field.variable.eq('yield')]**2) == pytest.approx(.175)
    assert np.sum(wf[field.variable.eq('harvest_biomass')]**2) == pytest.approx(.15)
    assert len(contrasts) == 3 and all('2019' not in a and '2019' not in b for a, b, _ in contrasts)
