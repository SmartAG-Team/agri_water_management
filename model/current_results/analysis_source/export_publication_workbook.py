"""Export only the current paper tables and their selected supporting data."""
from pathlib import Path
import json
import re

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PUB = ROOT / 'publication'
CAL = ROOT / 'calibration'
REG = ROOT / 'regional'


def selected(frame):
    return frame[frame.version.eq('management_refit')] if 'version' in frame.columns else frame


def main():
    frozen = json.loads((CAL / 'parameters/frozen_model.json').read_text())
    target = PUB / 'tables/paper_data_and_tables.xlsx'
    with pd.ExcelWriter(target, engine='openpyxl') as writer:
        for document in ['manuscript', 'supplementary']:
            blocks = json.loads((PUB / 'analysis_source' / (document + '_blocks.json')).read_text())['blocks']
            for block in blocks:
                if 'table' not in block:
                    continue
                label = re.match(r'Table (S?\d+)\.', block['table_caption']).group(1)
                file = PUB / block['table']
                pd.read_csv(file).to_excel(writer, sheet_name=(label + '_' + file.stem)[:31], index=False)
        extra = {
            'Station_metrics': PUB / 'tables/current_station_metrics.csv',
            'Field_comparisons': PUB / 'tables/current_field_comparisons.csv',
            'Station_source_records': CAL / 'predictions/station_comparisons.csv',
            'Field_source_records': CAL / 'data/wuqiao_used_seasonal_observations.csv',
            'Field_ET_response': CAL / 'tables/field_ET_contrasts.csv',
            'Case_availability': CAL / 'tables/case_data_availability.csv',
            'Water_fit_coefficients': CAL / 'tables/fitted_parameters.csv',
            'Calibration_history': CAL / 'tables/calibration_history.csv',
            'Regional_annual_scenarios': REG / 'tables/regional_policy_annual_results.csv',
            'Storage_rainfall_controls': REG / 'tables/policy_comparison.csv',
            'Grain_gain_reconciliation': PUB / 'tables/grain_gain_reconciliation.csv',
            'Spatial_distribution': PUB / 'tables/current_spatial_distribution.csv',
            'Spatial_loss_frequency': PUB / 'tables/current_spatial_loss_frequency.csv',
            'Spatial_loss_components': PUB / 'tables/current_spatial_loss_components.csv',
            'Spatial_annual_exposure': PUB / 'tables/current_spatial_annual_exposure.csv',
        }
        for name, file in extra.items():
            selected(pd.read_csv(file)).to_excel(writer, sheet_name=name, index=False)
        metadata = {key: value for key, value in frozen.items() if key != 'candidates'}
        pd.DataFrame([{'Property': key, 'Value': json.dumps(value, ensure_ascii=False)}
                      for key, value in metadata.items()]).to_excel(writer, sheet_name='Frozen_selection', index=False)
        for run, title in [(CAL, 'Calibration'), (REG, 'Regional')]:
            records = json.loads((run / 'verification/input_manifest.json').read_text())
            pd.DataFrame(records).to_excel(writer, sheet_name=title + '_source_SHA256', index=False)
    print('Current publication workbook: 14 paper tables and 18 supporting-data sheets.')


if __name__ == '__main__':
    main()
