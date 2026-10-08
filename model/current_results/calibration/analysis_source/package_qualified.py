"""Package calibration/testing and explicitly conditional station ET comparisons."""
from pathlib import Path
from datetime import datetime, timezone
from io import StringIO
import hashlib
import json
import shutil
import numpy as np
import pandas as pd

import package_crop_run as figures
from documented_field_metrics import losses

ROOT = Path(__file__).resolve().parents[1]
PREVIOUS = ROOT.parents[1] / 'model/2026-10-07_maize_extraction_calibration'


def exports(station, field, frozen):
    availability = pd.read_csv(ROOT / 'data/case_inventory.csv').merge(
        pd.DataFrame({'variable': sorted(station.variable.unique())}), how='cross')
    original = pd.read_csv(ROOT / 'data/observations.csv', low_memory=False)
    counts = original.groupby(['case_id', 'variable']).size().rename('matched_records').reset_index()
    availability = availability.merge(counts, how='left', on=['case_id', 'variable'])
    availability['matched_records'] = availability.matched_records.fillna(0).astype(int)
    availability['prior_growth_fitting_eligible'] = availability.split.eq('calibration') & availability.variable.ne('et') & availability.matched_records.gt(0)
    availability['fitting_eligible'] = False
    availability['comparison_kind'] = np.where(availability.variable.eq('et'), 'conditional_ET_management_unconfirmed',
        np.where(availability.split.eq('calibration'), 'conditional_transfer_calibration_partition', 'conditional_transfer_retrospective_testing'))
    availability['trajectory_retained'] = True
    availability['residual_based_exclusion'] = False
    availability.to_csv(ROOT / 'tables/case_data_availability.csv', index=False)
    weather, soils, irrigation, initial = [], [], [], []
    for kind in ['station', 'field']:
        for path in sorted((ROOT / 'inputs/resolved' / frozen['selected_version'] / kind).glob('*.json')):
            p = json.loads(path.read_text())
            case = path.name.removesuffix('_crop.json') if kind == 'field' else path.stem
            for segment, inputs in [('crop', p['inputs'])] + ([('presowing', p['presowing']['inputs'])] if p.get('presowing') else []):
                tags = dict(case_id=case, kind=kind, segment=segment)
                weather.extend(dict(row, **tags) for row in inputs['weather'])
                soils.extend(dict(row, layer=i, **tags) for i, row in enumerate(inputs['soil_layers']))
                irrigation.extend(dict(row, **tags) for row in inputs.get('irrigation_events', []))
                role = 'overridden_by_presowing_state' if segment == 'crop' and p.get('presowing') else 'supplied_initialization'
                initial.extend(dict(layer=i, theta=theta, role=role, **tags) for i, theta in enumerate(inputs['initial_theta']))
    for rows, name in [(weather, 'qualified_exact_weather.csv.gz'), (soils, 'qualified_exact_soil.csv'),
                       (irrigation, 'qualified_exact_irrigation.csv'), (initial, 'qualified_initial_profiles.csv')]:
        destination=ROOT/'data'/name
        frame=pd.DataFrame(rows)
        if destination.exists():
            pd.testing.assert_frame_equal(pd.read_csv(StringIO(frame.to_csv(index=False)),low_memory=False),
                pd.read_csv(destination,low_memory=False),check_dtype=False,check_exact=False,rtol=1e-12,atol=1e-12)
        else:frame.to_csv(destination,index=False)
    parameters, histories = [], []
    for crop in ['wheat', 'maize']:
        card = frozen['candidates']['management_refit'][crop]
        for name, value, lo, hi in zip(card['vector_order'], card['vector'], card['lower_bounds'], card['upper_bounds']):
            parameters.append(dict(crop=crop, parameter=name, value=value, lower_bound=lo, upper_bound=hi,
                                   native_model_changed=False, daily_ET_fitted=False, estimated_in_water_fit=name in card['estimated_parameter_names']))
        histories.append(pd.read_csv(ROOT / 'parameters/candidates' / (crop + '_corrected_matric_history.csv')))
    pd.DataFrame(parameters).to_csv(ROOT / 'tables/fitted_parameters.csv', index=False)
    pd.concat(histories).to_csv(ROOT / 'tables/calibration_history.csv', index=False)
    checks = []
    metrics = pd.read_csv(ROOT / 'tables/field_metrics.csv')
    for (version, crop, split), group in metrics.groupby(['version', 'crop', 'split']):
        g = group.set_index('variable')
        f = field[field.version.eq(version) & field.crop.eq(crop) & field.split.eq(split)]
        e = g.loc['seasonal_et']
        values = dict(RMSE_le50=e.rmse<=50, nRMSE_le15=e.nrmse_percent<=15,
            absolute_bias_le10pct=abs(e.bias)<=.1*f.observed_et_mm.mean(), NSE_positive=e.nse>0,
            response_RMSE_le50=g.loc['seasonal_et_response', 'rmse']<=50,
            field_yield_nRMSE_le20=g.loc['yield', 'nrmse_percent']<=20)
        checks.append(dict(version=version, crop=crop, split=split, **values, scientific_checks_pass=all(values.values())))
    pd.DataFrame(checks).to_csv(ROOT / 'tables/seasonal_ET_working_checks.csv', index=False)
    return availability


def report(frozen):
    metrics=pd.read_csv(ROOT/'tables/field_metrics.csv')
    lines=['Documented-management water calibration','',
        'Eight existing water-response coefficients per crop are calibrated against documented Wuqiao treatment management in 2016–2018. '
        'RUE, leaf allocation and grain-number coefficients retain their earlier multisite estimates, supported by 480 wheat and 433 maize '
        'growth/yield observations across all four eligible wheat sites and five maize sites. Source station irrigation-log completeness '
        'remains unconfirmed, so station outcomes do not identify water-response coefficients in this refit. All station observations '
        'and whole site-year partitions remain available for conditional transfer evaluation. No source value or native equation is changed.','',
        'The field objective retains weights of 0.325 for seasonal ET, 0.175 for dry grain yield, 0.15 for confirmed dry aboveground '
        'biomass and 0.1 for W3−W0 ET response. Total prior weight is 0.01 over the eight estimated coefficients. Bounds and prior '
        'centres/scales retain their inherited values. Reference and candidate scores use this same field-only objective; scores from '
        'the former combined station/field objective are not directly comparable.','',
        'The 2019 field year remains outside fitting and selection. Testing is retrospective because its outcomes were inspected '
        'during earlier development. Station calibration-partition curves are transfer diagnostics after the water refit, with an '
        'earlier growth-calibration history; they are not direct new water calibration or independent management validation.','',
        f'The selected version is {frozen["selected_version"]}. Reference field calibration loss is '
        f'{frozen["mean_crop_calibration_data_loss"]["reference"]:.6f}; candidate loss is '
        f'{frozen["mean_crop_calibration_data_loss"]["management_refit"]:.6f}. Selection precedes testing.','',
        '| Version | Crop | Partition | ET RMSE (mm) | ET nRMSE (%) | ET R² | ET NSE | ET response error (mm) | Yield nRMSE (%) | Biomass nRMSE (%) |',
        '|---|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for (version,crop,split),group in metrics.groupby(['version','crop','split']):
        g=group.set_index('variable');e=g.loc['seasonal_et'];label='Calibration' if split=='calibration' else 'Retrospective testing'
        lines.append(f'| {version} | {crop.title()} | {label} | {e.rmse:.2f} | {e.nrmse_percent:.2f} | {e.r_squared:.3f} | {e.nse:.3f} | '
            f'{g.loc["seasonal_et_response","rmse"]:.2f} | {g.loc["yield","nrmse_percent"]:.2f} | {g.loc["biomass","nrmse_percent"]:.2f} |')
    checks=pd.read_csv(ROOT/'tables/seasonal_ET_working_checks.csv')
    test=checks[checks.version.eq(frozen['selected_version'])&checks.split.eq('validation')]
    passed=len(test)==2 and test.scientific_checks_pass.all()
    lines+=['','Working criteria are ET RMSE ≤50 mm, nRMSE ≤15%, absolute bias ≤10% of observed mean, positive NSE, '
        'ET-response error ≤50 mm and field-yield nRMSE ≤20%, alongside biomass and LAI plausibility. Four testing means '
        'per crop and one annual contrast provide limited evidence of transfer across years. These are study criteria, not universal '
        'publication thresholds.','',
        'Published field ET is a 0–2 m water-balance estimate under negligible runoff and drainage assumptions. Vertical initial '
        'water profiles and exact storage sampling order remain unconfirmed. Station management uncertainty also affects growth '
        'and soil-water comparisons. No phase ET estimate with unconfirmed sampling windows enters this refit.','',
        'The selected version passes the numerical field criteria; source uncertainty and growth plausibility still govern scientific acceptance.'
        if passed else 'The selected version does not meet all field testing criteria. No manuscript or regional-policy promotion follows.']
    (ROOT/'evaluation.md').write_text('\n'.join(lines)+'\n')


def workbook():
    sheets = {}
    obs = pd.read_csv(ROOT / 'data/observations.csv', low_memory=False)
    for variable, group in obs.groupby('variable'):
        sheets['Observed_' + variable] = group
    files = [('Management_ET_eligibility', 'data/daily_ET_fitting_eligibility.csv'),
        ('Conditional_calibration_ET', 'data/conditional_ET_observations.csv'),
        ('Used_station_wheat', 'data/used_qualified_station_wheat.csv'),
        ('Used_station_maize', 'data/used_qualified_station_maize.csv'),
        ('Inherited_growth_wheat', 'data/inherited_growth_calibration_wheat.csv'),
        ('Inherited_growth_maize', 'data/inherited_growth_calibration_maize.csv'),
        ('New_water_field_wheat', 'data/used_new_water_field_wheat.csv'),
        ('New_water_field_maize', 'data/used_new_water_field_maize.csv'),
        ('Used_field_wheat', 'data/used_qualified_field_wheat.csv'),
        ('Used_field_maize', 'data/used_qualified_field_maize.csv'),
        ('Station_comparisons', 'predictions/station_comparisons.csv'),
        ('Field_comparisons', 'predictions/field_comparisons.csv'),
        ('Case_summaries', 'predictions/case_summaries.csv'),
        ('Field_metrics', 'tables/field_metrics.csv'), ('Station_metrics', 'tables/station_metrics.csv'),
        ('Site_metrics', 'tables/site_metrics.csv'), ('Calibration_losses', 'tables/calibration_data_losses.csv'),
        ('Balanced_seasonal_curves', 'tables/balanced_seasonal_curves.csv'),
        ('Field_ET_response', 'tables/field_ET_contrasts.csv'),
        ('Field_soil_water', 'tables/field_soil_water_diagnostics.csv'),
        ('Case_availability', 'tables/case_data_availability.csv'),
        ('Fitted_parameters', 'tables/fitted_parameters.csv'), ('Calibration_history', 'tables/calibration_history.csv'),
        ('Working_accuracy_checks', 'tables/seasonal_ET_working_checks.csv'),
        ('Exact_weather', 'data/qualified_exact_weather.csv.gz'), ('Exact_soil', 'data/qualified_exact_soil.csv'),
        ('Exact_irrigation', 'data/qualified_exact_irrigation.csv'), ('Initial_profiles', 'data/qualified_initial_profiles.csv'),
        ('Confirmed_field_biomass', 'data/confirmed_field_biomass.csv'),
        ('Field_grain_yield', 'data/wuqiao_digitized_annual_yields.csv'),
        ('Field_ET_observations', 'data/wuqiao_used_seasonal_observations.csv'),
        ('Initialization_applied', 'data/initialization_applied.csv')]
    for name, path in files:
        sheets[name] = pd.read_csv(ROOT / path, low_memory=False)
    sheets['Source_SHA256'] = pd.DataFrame(json.loads((ROOT / 'verification/input_manifest.json').read_text()))
    sheets['Frozen_selection'] = pd.DataFrame([dict(property=k, value=json.dumps(v))
        for k, v in json.loads((ROOT / 'parameters/frozen_model.json').read_text()).items()])
    with pd.ExcelWriter(ROOT / 'tables/validation_data_and_results.xlsx') as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name[:31], index=False)
            sheet = writer.sheets[name[:31]]; sheet.freeze_panes='A2'; sheet.auto_filter.ref=sheet.dimensions
    return len(sheets)


def main():
    if (ROOT / 'verification/artifact_manifest.json').exists():
        raise RuntimeError('Sealed run preserved')
    frozen = json.loads((ROOT / 'parameters/frozen_model.json').read_text())
    station = pd.read_csv(ROOT / 'predictions/station_comparisons.csv', low_memory=False)
    completeness={p.stem:json.loads(p.read_text())['source_log_completeness'] for p in (ROOT/'inputs/station').glob('*.json')}
    station['irrigation_log_completeness']=station.case_id.map(completeness)
    assert station.irrigation_log_completeness.eq('not established').all()
    growth=station.variable.ne('et')
    station.loc[growth,'comparison_status']=np.where(station.loc[growth,'split'].eq('calibration'),
        'conditional_transfer_calibration_partition','conditional_transfer_retrospective_testing')
    station['independent_management_validation']=False
    station.to_csv(ROOT/'predictions/station_comparisons.csv',index=False)
    field = pd.read_csv(ROOT / 'predictions/field_comparisons.csv')
    calculated = losses(station, field)
    for row in calculated.itertuples():
        assert abs(row.calibration_data_loss-frozen['candidates'][row.version][row.crop]['calibration_data_loss']) < 1e-10
    calculated.to_csv(ROOT / 'tables/calibration_data_losses.csv', index=False)
    figures.register_analysis_sources()
    exports(station, field, frozen)
    for stem in ['Raw_field_water_observations', 'Raw_eligible_station_dynamics', 'Raw_confirmed_field_biomass']:
        for suffix in ['.png', '.pdf', '_caption.txt']:
            shutil.copy2(PREVIOUS / 'figures' / (stem + suffix), ROOT / 'figures' / (stem + suffix))
    figures.field_figure(); figures.harvest_figures()
    from qualified_seasonal_curves import draw
    draw()
    from field_temporal_et import draw as draw_et
    from field_soil_water import draw as draw_soil
    draw_et(ROOT, frozen['selected_version']); draw_soil(ROOT, frozen['selected_version'])
    report(frozen)
    count = workbook()
    (ROOT / 'verification/packaging_receipt.json').write_text(json.dumps(dict(workbook_sheets=count, figure_pairs=9,
        selected_version=frozen['selected_version'], daily_ET_fitted=False, testing_retrospective=True,
        scientific_goal_achieved=False, manuscript_promoted=False, regional_promoted=False,
        packaged_at_utc=datetime.now(timezone.utc).isoformat()), indent=2) + '\n')
    print('Packaged qualified calibration:', count, 'sheets; nine figure pairs', flush=True)


if __name__ == '__main__':
    main()
