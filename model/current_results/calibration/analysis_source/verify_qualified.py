"""Independent whole-run checks; numerical integrity is not scientific acceptance."""
from pathlib import Path
import gzip
import hashlib
import json
import re
import numpy as np
import pandas as pd
import pymupdf
from audit_math import close, same_json, weights, moments
from documented_field_metrics import losses

ROOT = Path(__file__).resolve().parents[1]


def main():
    manifest = json.loads((ROOT / 'verification/input_manifest.json').read_text())
    for entry in manifest:
        assert hashlib.sha256((ROOT / entry['snapshot']).read_bytes()).hexdigest() == entry['sha256'], entry['snapshot']
    frozen = json.loads((ROOT / 'parameters/frozen_model.json').read_text())
    assert not frozen['testing_used_for_selection'] and frozen['testing_retrospective']
    assert not frozen['daily_ET_used_for_fitting'] and not frozen['native_model_changed']
    for path, expected in frozen['fit_sha256'].items():
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == expected
    source = pd.read_csv(ROOT / 'data/observations.csv', low_memory=False)
    quality = pd.read_csv(ROOT / 'data/daily_ET_fitting_eligibility.csv')
    excluded = pd.read_csv(ROOT / 'data/conditional_ET_observations.csv', low_memory=False)
    assert len(excluded) == 2858 and len(quality) == 22 and not quality.eligible_for_water_parameter_fitting.any()
    assert excluded.variable.eq('et').all() and excluded.split.eq('calibration').all()
    assert set(excluded.observation_id) == set(source[source.variable.eq('et') & source.split.eq('calibration')].observation_id)
    assert set(quality.case_id) == set(excluded.case_id)
    for row in quality.itertuples():
        payload = json.loads((ROOT / 'inputs/station' / (row.case_id + '.json')).read_text())
        assert payload['source_log_completeness'] == 'not established' == row.source_log_completeness
        assert len(payload['inputs'].get('irrigation_events', [])) == row.irrigation_events
    used_records = 0
    for crop in ['wheat', 'maize']:
        used = pd.read_csv(ROOT / 'data' / ('used_qualified_station_' + crop + '.csv'), low_memory=False)
        expected = source[source.crop.eq(crop) & source.split.eq('calibration') & source.variable.ne('et')]
        assert set(used.observation_id) == set(expected.observation_id) and used.variable.ne('et').all()
        assert used.site.nunique() == (4 if crop == 'wheat' else 5)
        assert used.groupby('source_group_id').split.nunique().eq(1).all()
        used_records += len(used)
        card = frozen['candidates']['management_refit'][crop]
        assert card['success'] and not card['testing_used'] and card['field_years'] == [2016, 2017, 2018]
        assert card['free_parameters'] == 8
        assert card['extra_free_parameters'] == 0
        assert np.all(np.array(card['vector']) >= card['lower_bounds']) and np.all(np.array(card['vector']) <= card['upper_bounds'])
        original=json.loads((ROOT/'source_snapshots/starting_parameters'/(crop+'.json')).read_text())
        from field_water_fit import WATER_NAMES
        for i,name in enumerate(card['vector_order']):
            if name not in WATER_NAMES: close(card['vector'][i],original['vector'][i],'inherited growth coefficient')
        assert card['station_records_used_in_new_water_fit']==0 and card['growth_coefficients_unchanged']
    assert used_records == 913
    station = pd.read_csv(ROOT / 'predictions/station_comparisons.csv', low_memory=False)
    field = pd.read_csv(ROOT / 'predictions/field_comparisons.csv')
    summary = pd.read_csv(ROOT / 'predictions/case_summaries.csv')
    assert len(station) == 14794 and len(field) == 64 and len(summary) == 328
    assert set(station.version) == {'reference', 'management_refit'}
    original_station = pd.read_csv(ROOT / 'source_snapshots/previous_station_comparisons.csv', low_memory=False)
    original_station = original_station[original_station.version.eq('management_refit')].set_index('observation_id')
    original_field = pd.read_csv(ROOT / 'source_snapshots/previous_field_comparisons.csv')
    original_field = original_field[original_field.version.eq('management_refit')].set_index('case_id')
    for version, group in station.groupby('version'):
        assert group.observation_id.nunique() == len(group) == 7397 and group.site.nunique() == 5
        assert group.groupby('source_group_id').split.nunique().eq(1).all()
        for name in ['case_id', 'site', 'crop', 'split', 'variable', 'value', 'window_start', 'window_end']:
            pd.testing.assert_series_equal(group[name].reset_index(drop=True),
                original_station.loc[group.observation_id, name].reset_index(drop=True), check_names=False, check_dtype=False)
        expected_eligible = pd.Series(False,index=group.index)
        assert group.fitting_eligible.eq(expected_eligible).all()
        assert group[group.variable.eq('et')].comparison_status.eq('conditional_ET_management_unconfirmed').all()
        assert group.irrigation_log_completeness.eq('not established').all()
        assert not group.independent_management_validation.any()
        if version == 'reference':
            close(group.predicted, original_station.loc[group.observation_id, 'predicted'], 'R61 reference replay')
    for version, group in field.groupby('version'):
        assert len(group) == 32 and group.groupby('source_group_id').split.nunique().eq(1).all()
        assert group[group.harvest_year.eq(2019)].split.eq('validation').all()
        for name in ['observed_et_mm', 'yield_13pct_kg_ha', 'observed_biomass_kg_ha']:
            close(group[name], original_field.loc[group.case_id, name], 'unchanged field observation')
        if version == 'reference':
            for name in ['predicted_et_mm', 'grain_13pct_kg_ha', 'predicted_biomass_kg_ha']:
                close(group[name], original_field.loc[group.case_id, name], 'R61 field replay')
    trajectories = windows = 0
    for path in sorted((ROOT / 'inputs/resolved').rglob('*.json')):
        kind, version = path.parent.name, path.parent.parent.name
        case = path.name.removesuffix('_crop.json') if kind == 'field' else path.stem
        payload = json.loads(path.read_text())
        original = json.loads((ROOT / 'inputs/reference' / kind / path.name).read_text())
        assert same_json(payload['inputs'], original['inputs']), 'Weather, soil, initial moisture or management changed'
        if version == 'reference':
            assert same_json(payload, original)
        else:
            raw = json.loads((ROOT / 'inputs' / kind / path.name).read_text())
            crop = payload['inputs']['crop']
            card = frozen['candidates']['management_refit'][crop]
            x = card['vector']
            c, base, h = payload['parameters']['crop'], raw['parameters']['crop'], payload['parameters']['hydrology']
            close(c['transpiration_coefficient'], min(1.5, base['transpiration_coefficient']*x[0]), 'bounded crop transpiration')
            close(c['soil_evaporation_coefficient'], x[1], 'soil evaporation fit')
            close(h['readily_available_water_fraction'], x[2], 'root depletion fit')
            close(c['profile']['rue_g_mj'], base['profile']['rue_g_mj']*x[3], 'RUE fit')
            assert same_json(c.get('wheat_phenology'), base.get('wheat_phenology'))
            assert same_json(c.get('maize_phenology'), base.get('maize_phenology'))
            if crop == 'wheat':
                grain, stress, decay, depth = 5, 6, 7, 8
                for key in ['leaf_allocation_multiplier', 'early_leaf_allocation_multiplier', 'late_leaf_allocation_multiplier']:
                    if key in base['growth_process']:
                        close(c['growth_process'][key], min(2., base['growth_process'][key]*x[4]), 'wheat allocation')
                close(h['root_extraction_fraction_day'], 10**x[9], 'wheat root extraction')
            else:
                grain, stress, decay, depth = 6, 7, 8, 9
                for key, index in [('early_leaf_allocation_multiplier', 4), ('late_leaf_allocation_multiplier', 5)]:
                    close(c['growth_process'][key], min(2., base['growth_process'][key]*x[index]), 'maize allocation')
                close(h['root_extraction_fraction_day'], 10**x[11], 'maize root extraction estimated')
            close(c['profile']['kernels_per_g_flowering_biomass'], base['profile']['kernels_per_g_flowering_biomass']*x[grain], 'grain sink')
            close(c['growth_process']['assimilation_water_stress_exponent'], x[stress], 'growth water response')
            close(h['root_density_decay_m_inv'], x[decay], 'root activity shape')
            close(c['profile']['root_max_mm'], min(x[depth], sum(l['thickness_mm'] for l in payload['inputs']['soil_layers'])), 'root depth')
            close(h['root_compensation_fraction'], x[10], 'root compensation')
            assert h['plant_water_stress_method'] == 'compensated_layer_depletion'
        if payload.get('presowing'):
            assert same_json(payload['presowing']['inputs'], original['presowing']['inputs'])
        with gzip.open(ROOT / 'predictions/full_daily' / version / kind / (case + '.json.gz'), 'rt') as handle:
            daily = pd.DataFrame(json.load(handle))
        assert daily.date.tolist() == [row['date'] for row in payload['inputs']['weather']]
        assert daily.balance_residual_mm.abs().max() < 1e-6 and daily.crop_carbon_residual_kg_ha.abs().max() < 1e-6
        close(daily.et_mm, daily.transpiration_mm+daily.soil_evaporation_mm+daily.canopy_evaporation_mm, 'ET components')
        close(daily.storage_final_mm-daily.storage_initial_mm,
              daily.precipitation_mm+daily.irrigation_field_mm+daily.capillary_rise_mm-daily.et_mm-daily.runoff_mm-daily.bottom_drainage_mm,
              'native daily water closure')
        theta = np.array(daily.soil_theta.tolist()); layers = payload['inputs']['soil_layers']
        assert np.all(theta >= np.array([l['air_dry'] for l in layers])-1e-9)
        assert np.all(theta <= np.array([l['saturation'] for l in layers])+1e-9)
        row = summary[summary.version.eq(version) & summary.case_id.eq(case)].iloc[0]
        close(daily.et_mm.sum(), row.predicted_et_mm, 'seasonal ET')
        close(daily.biomass_kg_ha.iloc[-1], row.predicted_biomass_kg_ha, 'harvest biomass')
        close(daily.yield_kg_ha.iloc[-1]/.87, row.grain_13pct_kg_ha, 'grain moisture conversion')
        if kind == 'station':
            observations = station[station.version.eq(version) & station.case_id.eq(case)]
            for obs in observations.itertuples():
                column = {'lai':'lai', 'biomass':'biomass_kg_ha', 'harvest_biomass':'biomass_kg_ha', 'yield':'yield_kg_ha', 'et':'et_mm'}[obs.variable]
                span = daily[daily.date.ge(obs.window_start) & daily.date.le(obs.window_end)]
                assert len(span) == (pd.Timestamp(obs.window_end)-pd.Timestamp(obs.window_start)).days+1
                close(span[column].mean(), obs.predicted, 'independent observation window')
                windows += 1
        trajectories += 1
    assert trajectories == 328 and windows == 14794
    metric_rows = 0
    for filename, by_site in [('station_metrics.csv', False), ('site_metrics.csv', True)]:
        for metric in pd.read_csv(ROOT / 'tables' / filename).itertuples():
            group = station[station.version.eq(metric.version) & station.crop.eq(metric.crop) & station.split.eq(metric.split) & station.variable.eq(metric.variable)]
            if by_site:
                group = group[group.site.eq(metric.site)]
            values = moments(group.value, group.predicted, weights(group))
            for name in ['rmse', 'bias', 'nse', 'r_squared']:
                close(getattr(metric, name), values[name], 'independent station metric')
            metric_rows += 1
    contrasts = pd.read_csv(ROOT / 'tables/field_ET_contrasts.csv')
    for row in contrasts.itertuples():
        group = field[field.version.eq(row.version) & field.crop.eq(row.crop) & field.split.eq(row.split) & field.harvest_year.eq(row.harvest_year)].set_index('treatment')
        close(row.observed_mm, group.loc['W3','observed_et_mm']-group.loc['W0','observed_et_mm'], 'observed irrigation response')
        close(row.predicted_mm, group.loc['W3','predicted_et_mm']-group.loc['W0','predicted_et_mm'], 'predicted irrigation response')
    for metric in pd.read_csv(ROOT / 'tables/field_metrics.csv').itertuples():
        if metric.variable == 'seasonal_et_response':
            group = contrasts[contrasts.version.eq(metric.version) & contrasts.crop.eq(metric.crop) & contrasts.split.eq(metric.split)]
            values = moments(group.observed_mm, group.predicted_mm); values['nrmse_percent'] = np.nan
            if len(group) < 3: values['r_squared'] = np.nan
        else:
            group = field[field.version.eq(metric.version) & field.crop.eq(metric.crop) & field.split.eq(metric.split)]
            o, p = {'seasonal_et':('observed_et_mm','predicted_et_mm'), 'yield':('yield_13pct_kg_ha','grain_13pct_kg_ha'),
                    'biomass':('observed_biomass_kg_ha','predicted_biomass_kg_ha')}[metric.variable]
            values = moments(group[o], group[p])
        for name in ['rmse', 'bias', 'nrmse_percent', 'nse', 'r_squared']:
            close(getattr(metric, name), values[name], 'independent field metric')
        metric_rows += 1
    calculated = losses(station, field)
    exported = pd.read_csv(ROOT / 'tables/calibration_data_losses.csv')
    for row in calculated.itertuples():
        match = exported[exported.version.eq(row.version) & exported.crop.eq(row.crop)].iloc[0]
        close(row.calibration_data_loss, match.calibration_data_loss, 'exported objective loss')
        close(row.calibration_data_loss, frozen['candidates'][row.version][row.crop]['calibration_data_loss'], 'frozen objective loss')
    means = calculated.groupby('version').calibration_data_loss.mean()
    assert frozen['selected_version'] == means.idxmin()
    checks = pd.read_csv(ROOT / 'tables/seasonal_ET_working_checks.csv')
    metrics = pd.read_csv(ROOT / 'tables/field_metrics.csv')
    for row in checks.itertuples():
        group = metrics[metrics.version.eq(row.version) & metrics.crop.eq(row.crop) & metrics.split.eq(row.split)].set_index('variable')
        data = field[field.version.eq(row.version) & field.crop.eq(row.crop) & field.split.eq(row.split)]
        et = group.loc['seasonal_et']
        criteria = dict(RMSE_le50=et.rmse<=50, nRMSE_le15=et.nrmse_percent<=15,
            absolute_bias_le10pct=abs(et.bias)<=.1*data.observed_et_mm.mean(), NSE_positive=et.nse>0,
            response_RMSE_le50=group.loc['seasonal_et_response','rmse']<=50,
            field_yield_nRMSE_le20=group.loc['yield','nrmse_percent']<=20)
        for name, expected in criteria.items(): assert getattr(row, name) == expected
        assert row.scientific_checks_pass == all(criteria.values())
    for path in (ROOT / 'figures').glob('*.pdf'):
        assert pymupdf.open(path).page_count == 1 and path.with_suffix('.png').exists()
    workbook = pd.ExcelFile(ROOT / 'tables/validation_data_and_results.xlsx')
    wm = pd.read_excel(workbook, sheet_name='Source_SHA256')
    assert set(zip(wm.snapshot, wm.sha256)) == {(row['snapshot'], row['sha256']) for row in manifest}
    for sheet, file in [('Field_metrics','tables/field_metrics.csv'), ('Station_comparisons','predictions/station_comparisons.csv'),
                        ('Management_ET_eligibility','data/daily_ET_fitting_eligibility.csv')]:
        assert len(pd.read_excel(workbook, sheet_name=sheet)) == len(pd.read_csv(ROOT / file))
    if (ROOT / 'verification/artifact_manifest.json').exists():
        for entry in json.loads((ROOT / 'verification/artifact_manifest.json').read_text()):
            assert hashlib.sha256((ROOT / entry['path']).read_bytes()).hexdigest() == entry['sha256'], entry['path']
    testing = checks[checks.version.eq(frozen['selected_version']) & checks.split.eq('validation')]
    print(json.dumps(dict(source_snapshots=len(manifest), trajectories=trajectories, observation_windows=windows,
        independent_metric_rows=metric_rows, all_eligible_sites_retained=True, whole_site_years_preserved=True,
        all_uncertain_management_ET_retained_as_conditional=True, daily_ET_not_used_for_fitting=True,
        calibration_selection_precedes_testing=True, testing_retrospective=True,
        original_weather_soil_initialization_and_irrigation_preserved=True, native_budgets_closed=True,
        numerical_and_provenance_checks_passed=True,
        field_working_criteria_pass=bool(len(testing)==2 and testing.scientific_checks_pass.all()),
        scientific_goal_achieved=False), indent=2))


if __name__ == '__main__':
    main()
