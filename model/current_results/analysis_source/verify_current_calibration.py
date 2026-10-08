"""Verify the consolidated calibration and replay selected exact inputs locally."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import sys

import numpy as np
import pandas as pd

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
CAL = ROOT / 'calibration'
sys.path.insert(0, str(CAL / 'source_snapshots/native'))
from research.ncp_irrigation.model import simulate_season


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    records = []
    def check(name, condition):
        records.append({'check': name, 'passed': bool(condition)})
        if not condition:
            raise ValueError(name)

    copied = json.loads((ROOT / 'verification/source_consolidation.json').read_text())
    for name, digest in copied['copied_files_sha256'].items():
        check('Exact consolidated source ' + name, sha(CAL / name) == digest)
    frozen = json.loads((CAL / 'parameters/frozen_model.json').read_text())
    selected = frozen['selected_version']
    check('Current selected parameter set', selected == 'management_refit')
    check('Parameter selection excludes field testing', not frozen['testing_used_for_selection'])
    check('Field calibration years explicit', frozen['field_calibration_years'] == [2016, 2017, 2018])
    check('Field retrospective testing year explicit', frozen['field_testing_years'] == [2019])
    check('Station ET not used for current fitting', not frozen['daily_ET_used_for_fitting'])
    for crop, card in frozen['candidates'][selected].items():
        check('Converged current fit ' + crop, card['success'])
        check('Eight water coefficients ' + crop, card['free_parameters'] == 8)
        check('Inherited growth coefficients ' + crop, card['growth_coefficients_unchanged'])

    field = pd.read_csv(CAL / 'predictions/field_comparisons.csv')
    field = field[field.version.eq(selected)]
    metrics = pd.read_csv(CAL / 'tables/field_metrics.csv')
    for (crop, split), group in field.groupby(['crop', 'split']):
        check(f'Unique field treatment-years {crop}/{split}',
              not group.duplicated(['harvest_year', 'treatment']).any())
        for variable, observed, predicted in [
            ('seasonal_et', 'observed_et_mm', 'predicted_et_mm'),
            ('yield', 'yield_13pct_kg_ha', 'grain_13pct_kg_ha'),
            ('biomass', 'observed_biomass_kg_ha', 'predicted_biomass_kg_ha')]:
            original = group[observed].to_numpy(); simulated = group[predicted].to_numpy()
            error = simulated - original
            rmse = float(np.sqrt(np.mean(error**2)))
            score = metrics[metrics.version.eq(selected) & metrics.crop.eq(crop) &
                            metrics.split.eq(split) & metrics.variable.eq(variable)].iloc[0]
            check(f'Independent field RMSE {crop}/{split}/{variable}', np.isclose(rmse, score.rmse))
            check(f'Independent field bias {crop}/{split}/{variable}', np.isclose(error.mean(), score.bias))
            check(f'Independent field nRMSE {crop}/{split}/{variable}',
                  np.isclose(100 * rmse / original.mean(), score.nrmse_percent))
        errors = []
        for _, year in group.groupby('harvest_year'):
            year = year.set_index('treatment')
            errors.append((year.loc['W3', 'predicted_et_mm'] - year.loc['W0', 'predicted_et_mm']) -
                          (year.loc['W3', 'observed_et_mm'] - year.loc['W0', 'observed_et_mm']))
        score = metrics[metrics.version.eq(selected) & metrics.crop.eq(crop) &
                        metrics.split.eq(split) & metrics.variable.eq('seasonal_et_response')].iloc[0]
        check(f'Independent irrigation response {crop}/{split}',
              np.isclose(np.sqrt(np.mean(np.asarray(errors)**2)), score.rmse))

    replays = []
    for crop in ['wheat', 'maize']:
        for treatment in ['W0', 'W3']:
            filename = f'yang2024_{crop}_2018_2019_{treatment}_crop.json'
            payload = json.loads((CAL / 'inputs/resolved' / selected / 'field' / filename).read_text())
            state = None
            if payload.get('presowing'):
                state = simulate_season(payload['presowing']['inputs'], payload['parameters']).final_state
            result = simulate_season(payload['inputs'], payload['parameters'], state)
            case = filename.removesuffix('_crop.json')
            saved = field[field.case_id.eq(case)].iloc[0]
            for label, a, b in [('ET', result.summary['et_mm'], saved.predicted_et_mm),
                                ('dry biomass', result.summary['biomass_kg_ha'], saved.predicted_biomass_kg_ha),
                                ('grain moisture conversion', result.summary['yield_kg_ha'] / .87, saved.grain_13pct_kg_ha)]:
                check(f'Exact native replay {case}/{label}', np.isclose(a, b, rtol=0, atol=1e-8))
            replays.append({'case': case, 'days': len(result.daily)})

    receipt = {'all_checks_passed': True, 'verified_at_utc': datetime.now(timezone.utc).isoformat(),
               'check_count': len(records), 'checks': records, 'selected_version': selected,
               'exact_native_replays': replays, 'field_testing_retrospective': True,
               'current_fit_uses_documented_field_management': True,
               'regional_scenarios_are_conditional': True,
               'field_accuracy_criteria_pass': False, 'source_sha256': sha(__file__)}
    (ROOT / 'verification/current_calibration_checks.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(f'{len(records)} current calibration checks passed; four native field replays matched.')


if __name__ == '__main__':
    main()
