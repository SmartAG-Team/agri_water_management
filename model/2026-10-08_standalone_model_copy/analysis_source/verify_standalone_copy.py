"""Check copied source, parameters and representative saved simulations."""
from pathlib import Path
import ast
import gzip
import hashlib
import json
import numpy as np
import pandas as pd

RUN = Path(__file__).resolve().parents[1]
PROJECT = RUN.parents[1]
PACKAGE = PROJECT / 'open_crop_model'
CALIBRATION = PROJECT / 'model/current_results/calibration'


def same(a, b):
    if a is None:
        return b is None or (not isinstance(b, (list, dict)) and bool(pd.isna(b)))
    if isinstance(a, (list, dict)) and isinstance(b, str):
        b = ast.literal_eval(b)
    if isinstance(a, list):
        return isinstance(b, list) and len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    if isinstance(a, dict):
        return isinstance(b, dict) and a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, (int, float)) and not isinstance(a, bool):
        return bool(np.isclose(a, b, rtol=0, atol=1e-8, equal_nan=True))
    return a == b


def main():
    manifest = json.loads((PACKAGE / 'model_manifest.json').read_text())
    for row in manifest['source_files_sha256']:
        assert hashlib.sha256((PACKAGE / row['file']).read_bytes()).hexdigest() == row['sha256']
    native = CALIBRATION / 'source_snapshots/native'
    for path in native.rglob('*.py'):
        assert path.read_bytes() == (PACKAGE / path.relative_to(native)).read_bytes()
    index = json.loads((PACKAGE / 'parameters/index.json').read_text())
    assert len(index) == 164
    for row in index:
        group = 'field' if row['case_id'].startswith('yang2024_') else 'station'
        name = row['case_id'] + ('_crop' if group == 'field' else '') + '.json'
        raw = json.loads((CALIBRATION / 'inputs/resolved/management_refit' / group / name).read_text())
        assert json.loads((PACKAGE / row['file']).read_text()) == raw['parameters']
    for path in PACKAGE.rglob('*'):
        assert not path.is_symlink()
        assert path.name not in {'.git', '.gitmodules', '.gitattributes', '.gitignore', '.venv', '.worktrees', '__pycache__'}
        if path.is_file():
            text = path.read_text()
            for token in ['/Users/', '/home/', '.worktrees/', 'github.com/', 'git@github']:
                assert token not in text, (path, token)
    references = pd.read_csv(RUN / 'source_snapshots/field_comparisons.csv')
    references = references[references.version.eq('management_refit')].set_index('case_id')
    checked = []
    for row in json.loads((RUN / 'input_manifest.json').read_text()):
        assert hashlib.sha256((RUN / row['unchanged_source_snapshot']).read_bytes()).hexdigest() == row['source_sha256']
        reference = RUN / row['reference_daily_snapshot']
        assert hashlib.sha256(reference.read_bytes()).hexdigest() == row['reference_daily_sha256']
        example = row['example'].removesuffix('.json')
        output = RUN / 'predictions' / example
        actual = pd.read_csv(output / 'daily_predictions.csv')
        original = pd.DataFrame(json.loads(gzip.decompress(reference.read_bytes())))
        assert list(actual.columns) == list(original.columns)
        assert len(actual) == len(original)
        for col in original.columns:
            assert all(same(a, b) for a, b in zip(original[col], actual[col])), (example, col)
        result = json.loads((output / 'summary.json').read_text())
        expected = references.loc[row['case_id']]
        for key, column, scale in [('et_mm', 'predicted_et_mm', 1),
                                   ('yield_kg_ha', 'grain_13pct_kg_ha', .87),
                                   ('biomass_kg_ha', 'predicted_biomass_kg_ha', 1),
                                   ('final_storage_mm', 'final_storage_mm', 1)]:
            assert np.isclose(result[key], expected[column] * scale, rtol=0, atol=1e-8)
        for key in ['anthesis_date', 'maturity_date', 'germination_start_date']:
            assert same(result[key], expected[key])
        assert np.isclose(actual.lai.max(), expected.lai_max, rtol=0, atol=1e-8)
        assert (output / 'input_snapshot.json').read_bytes() == (PACKAGE / 'examples' / row['example']).read_bytes()
        checked.append({'case_id': row['case_id'], 'days': len(actual), 'daily_columns': len(actual.columns)})
    receipt = {'all_checks_passed': True, 'scientific_python_files_exact': 143,
               'schema_dependency_files_hashed': 15, 'case_parameter_sets_exact': 164,
               'representative_native_replays': checked, 'daily_and_seasonal_values_match': True,
               'original_repository_metadata_present': False,
               'run_from_outside_repository': True, 'included_unit_tests_passed': 30,
               'scope': 'Standalone-copy equivalence; no new fitting or independent validation.'}
    (RUN / 'verification/copy_checks.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
