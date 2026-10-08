"""Replay every selected field and station case without an external checkout."""
from datetime import datetime, timezone
from pathlib import Path
import argparse
import gzip
import hashlib
import json
import math
import shutil
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'model/current_results/calibration'
sys.dont_write_bytecode = True


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--diagnostic', action='store_true', help='Measure all platform differences before checking tolerances')
    args = parser.parse_args()
    output = args.output.resolve()
    if output == SOURCE or SOURCE in output.parents:
        raise ValueError('Preserve the completed calibration archive')
    if output.exists() and any(output.iterdir()):
        raise FileExistsError('Choose a new or empty output directory')
    output.mkdir(parents=True, exist_ok=True)
    for source, destination in [
        (SOURCE / 'inputs/resolved/management_refit', output / 'inputs'),
        (SOURCE / 'data', output / 'data'),
        (SOURCE / 'parameters', output / 'parameters'),
        (SOURCE / 'source_snapshots/native', output / 'source_snapshots/native'),
    ]:
        shutil.copytree(source, destination, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    (output / 'verification').mkdir()
    (output / 'predictions').mkdir()
    source_manifest = {
        p.relative_to(output).as_posix(): sha(p)
        for folder in ['inputs', 'data', 'parameters', 'source_snapshots']
        for p in sorted((output / folder).rglob('*')) if p.is_file()
    }
    native = output / 'source_snapshots/native'
    sys.path.insert(0, str(native))
    from research.ncp_irrigation.model import simulate_season
    if Path(simulate_season.__code__.co_filename).resolve() != (native / 'research/ncp_irrigation/model.py').resolve():
        raise AssertionError('Simulation engine imported outside this reproduction run')
    saved = pd.read_csv(SOURCE / 'predictions/case_summaries.csv')
    saved = saved[saved.version.eq('management_refit')].set_index('case_id')
    cases = sorted((output / 'inputs').rglob('*.json'))
    if len(cases) != 164 or saved.index.duplicated().any():
        raise AssertionError('Expected 164 unique selected calibration cases')
    numeric_values = 0
    maximum_difference = 0.
    numeric_differences = {}
    mismatches = []
    mismatch_count = 0

    def compare(actual, expected, location):
        nonlocal numeric_values, maximum_difference, mismatch_count
        if isinstance(expected, dict):
            if not isinstance(actual, dict) or actual.keys() != expected.keys():
                raise AssertionError('Different daily fields: ' + location)
            for key in expected:
                compare(actual[key], expected[key], location + '/' + key)
        elif isinstance(expected, list):
            if not isinstance(actual, list) or len(actual) != len(expected):
                raise AssertionError('Different list length: ' + location)
            for index, (a, b) in enumerate(zip(actual, expected)):
                compare(a, b, location + '/' + str(index))
        elif isinstance(expected, (int, float)) and not isinstance(expected, bool):
            numeric_values += 1
            if math.isnan(expected) and math.isnan(actual):
                return
            parts = location.split('/')
            field = parts[2] if len(parts)>2 else parts[-1]
            difference = abs(actual - expected)
            numeric_differences[field] = max(numeric_differences.get(field, 0.), difference)
            if not math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-8):
                mismatch_count += 1
                if not args.diagnostic:
                    raise AssertionError(f'Numerical mismatch: {location}; actual={actual!r}, expected={expected!r}, difference={difference!r}')
                if len(mismatches)<50:
                    mismatches.append(dict(location=location, actual=actual, expected=expected, difference=difference))
            maximum_difference = max(maximum_difference, abs(actual - expected))
        elif actual != expected:
            raise AssertionError('Value mismatch: ' + location)

    records = []
    reproduced = []
    for index, path in enumerate(cases, 1):
        before_mismatches = mismatch_count
        payload = json.loads(path.read_text())
        state = None
        presowing = payload.get('presowing')
        if presowing:
            pre = simulate_season(presowing['inputs'], payload['parameters'])
            state = pre.final_state
        if payload.get('initial_state') is not None and not presowing:
            raise AssertionError('Recorded initialization has no reproducible presowing inputs')
        result = simulate_season(payload['inputs'], payload['parameters'], state)
        case_id = path.stem.removesuffix('_crop')
        daily = json.loads(json.dumps(result.daily))
        reference = SOURCE / 'predictions/full_daily/management_refit' / path.parent.name / (case_id + '.json.gz')
        with gzip.open(reference, 'rt') as handle:
            compare(daily, json.load(handle), case_id)
        values = {
            'predicted_et_mm': result.summary['et_mm'],
            'predicted_biomass_kg_ha': result.summary['biomass_kg_ha'],
            'grain_13pct_kg_ha': result.summary['yield_kg_ha'] / .87,
            'final_storage_mm': result.summary['final_storage_mm'],
        }
        for name, value in values.items():
            compare(value, float(saved.at[case_id, name]), case_id + '/' + name)
        target = output / 'predictions' / path.parent.name
        target.mkdir(exist_ok=True)
        with gzip.open(target / (case_id + '.json.gz'), 'wt') as handle:
            json.dump(daily, handle)
        if presowing:
            with gzip.open(target / (case_id + '.presowing.json.gz'), 'wt') as handle:
                json.dump(pre.daily, handle)
        row = saved.loc[case_id].to_dict()
        row.update(case_id=case_id, **values)
        reproduced.append(row)
        records.append(dict(case_id=case_id, group=path.parent.name, crop=payload['inputs']['crop'],
                            days=len(daily), presowing_recomputed_with_selected_parameters=bool(presowing),
                            all_daily_fields_match=mismatch_count == before_mismatches, reference_daily_sha256=sha(reference)))
        if index % 20 == 0 or index == len(cases):
            print(f'Calibration cases reproduced: {index}/{len(cases)}', flush=True)
    pd.DataFrame(reproduced).to_csv(output / 'predictions/case_summaries.csv', index=False)
    receipt = dict(all_cases_passed=mismatch_count == 0, selected_version='management_refit',
                   completed_utc=datetime.now(timezone.utc).isoformat(), cases=len(records),
                   field_cases=sum(r['group'] == 'field' for r in records),
                   station_cases=sum(r['group'] == 'station' for r in records),
                   crop_days=sum(r['days'] for r in records), numeric_values_compared=numeric_values,
                   maximum_absolute_difference=maximum_difference,
                   maximum_differences_by_field=numeric_differences,
                   diagnostic_mismatch_examples=mismatches,
                   values_exceeding_tolerance=mismatch_count,
                   tolerances=dict(rtol=1e-12, atol=1e-8), source_files_sha256=source_manifest,
                   parameter_optimization_repeated=False, prior_predictions_copied_to_inputs=False,
                   external_checkout_or_credentials_required=False, records=records, driver_sha256=sha(__file__))
    (output / 'verification/calibration_replay.json').write_text(json.dumps(receipt, indent=2) + '\n')
    if mismatches:
        print(json.dumps(numeric_differences,indent=2),flush=True)
        raise AssertionError('Measured platform differences exceed the strict replay tolerance; see calibration_replay.json')
    print('All 164 selected field and station cases match the published daily predictions', flush=True)


if __name__ == '__main__':
    main()
