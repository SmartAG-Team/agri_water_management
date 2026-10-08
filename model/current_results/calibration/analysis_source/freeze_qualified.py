"""Select from the same management-qualified objective before testing."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def main():
    target = ROOT / 'parameters/frozen_model.json'
    if target.exists():
        raise RuntimeError('Frozen selection preserved')
    candidates, references, hashes = {}, {}, {}
    for crop in ['wheat', 'maize']:
        path = ROOT / 'parameters/candidates' / (crop + '_corrected_matric.json')
        card = json.loads(path.read_text())
        starting = ROOT / 'source_snapshots/starting_parameters' / (crop + '.json')
        original = json.loads(starting.read_text())
        receipt_path = ROOT / 'verification' / ('starting_qualified_objective_' + crop + '.json')
        receipt = json.loads(receipt_path.read_text())
        assert card['success'] and not card['testing_used'] and not card['daily_ET_used_for_fitting']
        assert card['field_years'] == [2016, 2017, 2018] and card['field_testing_years'] == [2019]
        assert card['free_parameters'] == 8
        assert card['extra_free_parameters'] == 0
        assert card.get('coefficient_priors_unchanged',card.get('other_coefficient_priors_unchanged',False)) and not card['native_model_changed']
        assert np.all(np.array(card['vector']) >= card['lower_bounds'])
        assert np.all(np.array(card['vector']) <= card['upper_bounds'])
        original['historical_combined_calibration_data_loss'] = original['calibration_data_loss']
        original['calibration_data_loss'] = receipt['reference_calibration_data_loss']
        original['reference_replayed_with_qualified_objective'] = True
        candidates[crop], references[crop] = card, original
        for source in [path, starting, receipt_path]:
            hashes[str(source.relative_to(ROOT))] = hashlib.sha256(source.read_bytes()).hexdigest()
    losses = {version:float(np.mean([row['calibration_data_loss'] for row in cards.values()]))
              for version, cards in [('reference', references), ('management_refit', candidates)]}
    selection = min(losses, key=losses.get)
    target.write_text(json.dumps(dict(selected_version=selection,
        candidates=dict(reference=references, management_refit=candidates),
        mean_crop_calibration_data_loss=losses,
        selection_criterion='Equal-crop mean documented-field water calibration loss before testing',
        fit_sha256=hashes, testing_used_for_selection=False, testing_retrospective=True,
        field_calibration_years=[2016, 2017, 2018], field_testing_years=[2019],
        station_partitions_unchanged=True, daily_ET_used_for_fitting=False,
        conditional_ET_retained=True, native_model_changed=False, extra_free_parameters=0,
        wheat_parameters_retained=False,maize_extraction_rate_estimated=True, station_growth_used_for_new_water_fitting=False, inherited_multisite_growth_calibration_retained=True, objective_definition="documented_field_water",
        soil_initialization_rule_changed=False, root_mass_shape_imposed=False,
        manuscript_promoted=False, regional_promoted=False,
        frozen_at_utc=datetime.now(timezone.utc).isoformat()), indent=2) + '\n')
    print('Frozen qualified selection', selection, losses, flush=True)


if __name__ == '__main__':
    main()
