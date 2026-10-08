"""Compare geospatial statistics recalculated from a fresh regional run."""
from pathlib import Path
import argparse
import json
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fresh', required=True, type=Path, help='Full reproduction root containing publication/tables')
    args = parser.parse_args()
    records = []
    for name in ['current_spatial_distribution.csv', 'current_spatial_loss_components.csv',
                 'current_spatial_loss_frequency.csv', 'current_spatial_annual_exposure.csv',
                 'current_spatial_component_cells_50pct.csv']:
        actual = pd.read_csv(args.fresh / 'publication/tables' / name)
        expected = pd.read_csv(ROOT / 'model/current_results/publication/tables' / name)
        pd.testing.assert_frame_equal(actual, expected, check_exact=False, rtol=1e-10, atol=1e-8)
        records.append(dict(table=name, rows=len(actual), all_columns_match=True))
    output = args.fresh / 'verification/spatial_reproduction_comparison.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(dict(all_comparisons_passed=True, tables=records,
                                     numeric_rtol=1e-10, numeric_atol=1e-8), indent=2) + '\n')
    print('Five fresh geospatial result tables match the published results')


if __name__ == '__main__':
    main()
