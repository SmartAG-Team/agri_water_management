"""Freeze the equal-crop calibration selection before retrospective testing."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json

import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def main():
    target = ROOT / 'parameters/frozen_model.json'
    if target.exists():
        raise RuntimeError('Existing frozen selection is preserved')
    fitted, reference, hashes = {}, {}, {}
    for crop in ['wheat', 'maize']:
        name = crop + '_corrected_matric.json'
        path = ROOT / 'parameters/candidates' / name
        card = json.loads(path.read_text())
        assert card['success'] and not card['testing_used']
        assert card['field_years'] == [2016, 2017, 2018]
        assert 'Wuqiao-2019' not in card['calibration_groups']
        assert card['field_biomass_basis_confirmed'] and card['individual_coefficient_priors_unchanged']
        assert card['free_parameters'] == 11 and card['extra_free_parameters'] == 0
        assert card['vector_order'][-1] == 'root_compensation_fraction'
        assert len(card['vector']) == 11 and 0 <= card['vector'][-1] <= 1
        assert np.all(np.array(card['vector']) >= card['lower_bounds'])
        assert np.all(np.array(card['vector']) <= card['upper_bounds'])
        fitted[crop] = card
        source = ROOT / 'source_snapshots/starting_candidates' / name
        original = json.loads(source.read_text())
        replay_path = ROOT / 'verification' / ('starting_objective_' + crop + '.json')
        replay = json.loads(replay_path.read_text())
        assert replay['R59_reference_replayed_under_new_initialization'] and not replay['testing_used']
        original['historical_R59_calibration_data_loss']=original['calibration_data_loss']
        original['calibration_data_loss']=replay['reference_calibration_data_loss']
        original['reference_initialization_rule_updated']=True
        original['reference_replayed_not_refitted'] = True
        reference[crop] = original
        for source_path in [path, source, replay_path]:
            hashes[source_path.relative_to(ROOT).as_posix()] = hashlib.sha256(source_path.read_bytes()).hexdigest()
    losses = dict(reference=float(np.mean([x['calibration_data_loss'] for x in reference.values()])),
                  soil_refit=float(np.mean([x['calibration_data_loss'] for x in fitted.values()])))
    selected = min(losses, key=losses.get)
    frozen = dict(selected_version=selected,
                  candidates=dict(reference=reference, soil_refit=fitted),
                  mean_crop_calibration_data_loss=losses,
                  selection_criterion='Equal-crop mean calibration data loss; no test outcomes',
                  fit_sha256=hashes, testing_used_for_selection=False,
                  testing_retrospective=True, field_calibration_years=[2016, 2017, 2018],
                  field_testing_years=[2019], station_partitions_unchanged=True,
                  field_biomass_basis_confirmed=True, field_biomass_in_calibration_objective=True,
                  original_ET_yield_station_and_response_weights_preserved=True,
                  extra_free_parameters=0, individual_coefficient_priors_unchanged=True,
                  native_model_changed=False, soil_initialization_rule_changed=True, root_uptake_method='compensated_layer_depletion',
                  empirical_assimilation_water_response_retained=True,
                  manuscript_promoted=False, regional_promoted=False,
                  frozen_at_utc=datetime.now(timezone.utc).isoformat())
    target.write_text(json.dumps(frozen, indent=2) + '\n')
    print('Frozen selection:', selected, losses, flush=True)


if __name__ == '__main__':
    main()
