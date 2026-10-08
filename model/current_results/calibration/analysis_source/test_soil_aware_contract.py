import json
from pathlib import Path

import numpy as np
import pandas as pd

from audit_math import same_json
from fit_objective import data,adjusted

ROOT=Path(__file__).resolve().parents[1]


def test_same_rule_keeps_all_cases_and_original_partitions():
    applied=pd.read_csv(ROOT/'data/initialization_applied.csv')
    availability=pd.read_csv(ROOT/'data/initialization_availability.csv')
    assert len(applied)==26 and applied.split.eq('calibration').sum()==11 and applied.split.eq('validation').sum()==15
    assert len(availability)==164 and availability.original_case_retained.all()
    assert not availability.new_observation_exclusion.any()
    assert applied.site.eq('Fengqiu').all()
    for row in applied.itertuples():
        p=json.loads((ROOT/'inputs/station'/(row.case_id+'.json')).read_text())
        original=json.loads((ROOT/'source_snapshots/original_inputs/station'/(row.case_id+'.json')).read_text())
        np.testing.assert_allclose(p['inputs']['initial_theta'],json.loads(row.mapped_theta))
        p['inputs']['initial_theta']=original['inputs']['initial_theta']
        assert same_json(p,original)


def test_changed_testing_predictions_not_replayed_from_old_inputs():
    f=pd.read_csv(ROOT/'data/reference_station_comparisons.csv',low_memory=False)
    missing=f[~f.reference_prediction_replay_eligible]
    assert len(missing)>0 and missing.split.eq('validation').all() and missing.predicted.isna().all()
    assert f[f.split.eq('calibration')].predicted.notna().all()


def test_all_site_calibration_and_final_year_separation():
    sites=set();cases=records=0
    for crop in ['wheat','maize']:
        obs,field,payloads,*_=data(crop,'corrected_matric')
        assert obs.split.eq('calibration').all() and field.split.eq('calibration').all()
        assert field.case_id.nunique()==12 and set(field.harvest_year)=={2016,2017,2018}
        assert 'Wuqiao-2019' not in set(field.source_group_id)
        card=json.loads((ROOT/'source_snapshots/starting_candidates'/(crop+'_corrected_matric.json')).read_text())
        assert len(card['vector'])==11
        for payload in payloads.values():
            p=adjusted(payload,card['vector'],crop)
            assert p['parameters']['hydrology']['plant_water_stress_method']=='compensated_layer_depletion'
            assert same_json(p['inputs'],payload['inputs'])
        sites.update(obs.site);cases+=obs.case_id.nunique();records+=len(obs)
    assert len(sites)==5 and cases==71 and records==3771
