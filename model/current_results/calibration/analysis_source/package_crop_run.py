"""Package frozen two-crop calibration with exact source-backed seasonal curves."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import sys
from io import StringIO

import numpy as np
import pandas as pd
from matplotlib.lines import Line2D

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'analysis_source'))
from diagnostic_plot_helpers import plt, BLUE, style, save, raw_observations, raw_station_dynamics


def selected_version():
    frozen = json.loads((ROOT / 'parameters/frozen_model.json').read_text())
    return frozen['selected_version']


def register_analysis_sources():
    path=ROOT/'verification/input_manifest.json'
    manifest=json.loads(path.read_text());known={r['snapshot'] for r in manifest}
    for source in sorted((ROOT/'analysis_source').glob('*.py')):
        target=source.relative_to(ROOT).as_posix()
        if target not in known:
            manifest.append(dict(source=str(source.resolve()),snapshot=target,
                sha256=hashlib.sha256(source.read_bytes()).hexdigest()))
    for row in manifest:
        assert hashlib.sha256((ROOT/row['snapshot']).read_bytes()).hexdigest()==row['sha256'],row['snapshot']
    path.write_text(json.dumps(manifest,indent=2)+'\n')


def calibration_losses():
    from metric_helpers import data_loss
    station=pd.read_csv(ROOT/'predictions/station_comparisons.csv',low_memory=False)
    field=pd.read_csv(ROOT/'predictions/field_comparisons.csv')
    losses=data_loss(station[station.split.eq('calibration')],field[field.split.eq('calibration')])
    frozen=json.loads((ROOT/'parameters/frozen_model.json').read_text())
    for row in losses.itertuples():
        expected=frozen['candidates'][row.version][row.crop]['calibration_data_loss']
        assert abs(row.calibration_data_loss-expected)<1e-10,(row.version,row.crop,row.calibration_data_loss,expected)
    losses.to_csv(ROOT/'tables/calibration_data_losses.csv',index=False)


def harvest_figures():
    all_field=pd.read_csv(ROOT/'predictions/field_comparisons.csv')
    selected=all_field[all_field.version.eq(selected_version())]
    biomass=pd.read_csv(ROOT/'data/confirmed_field_biomass.csv').set_index('case_id')
    yields=pd.read_csv(ROOT/'data/wuqiao_digitized_annual_yields.csv').set_index(['crop','harvest_year','treatment'])
    for observed,predicted,stem,label in [
        ('observed_biomass_kg_ha','predicted_biomass_kg_ha','Field_harvest_biomass','Aboveground dry biomass (t ha⁻¹)'),
        ('yield_13pct_kg_ha','grain_13pct_kg_ha','Field_grain_yield','Grain yield at 13% moisture (t ha⁻¹)')]:
        fig,axes=plt.subplots(2,4,figsize=(11.4,5.9),sharey='row',layout='constrained')
        for i,crop in enumerate(['wheat','maize']):
            rows=selected[selected.crop.eq(crop)]
            for j,year in enumerate([2016,2017,2018,2019]):
                ax=axes[i,j];g=rows[rows.harvest_year.eq(year)].sort_values('treatment')
                if stem=='Field_harvest_biomass':
                    se=biomass.loc[g.case_id,'standard_error_kg_ha'].to_numpy()
                else:
                    index=pd.MultiIndex.from_frame(g[['crop','harvest_year','treatment']])
                    se=yields.loc[index,'standard_error_kg_ha'].to_numpy()
                x=np.arange(4)
                ax.errorbar(x,g[observed]/1000.,yerr=se/1000.,color='black',marker='o',ms=4,
                            lw=1,capsize=2,label='Published mean ± SE')
                ax.plot(x,g[predicted]/1000.,color=BLUE,marker='s',ms=3,lw=1.3,label='Frozen calibrated model')
                ax.set_xticks(x,['W0','W1','W2','W3'])
                upper=max(float(rows[observed].max()),float(rows[predicted].max()))/1000.
                ax.set_ylim(0,upper*1.25)
                if j==0:ax.set_ylabel(label)
                style(ax,i*4+j,f'{crop.capitalize()} {year} · '+('calibration' if year<2019 else 'testing'))
        fig.legend(*axes[0,0].get_legend_handles_labels(),loc='outside lower center',ncol=2,frameon=False,fontsize=8)
        caption=('Harvest treatment responses from Wuqiao. Means and standard errors represent three field-plot replicates. '
            'The 2016–2018 crop-treatment seasons enter calibration; 2019 testing is retrospective. '
            'W0–W3 denote current wheat irrigation and preceding wheat irrigation for maize. '
            'The complete 32 crop-treatment seasons remain included. ')
        caption+=('Aboveground biomass includes oven-dried grain and residues following ground-level cutting; '
                  'the Table 1 mass basis is confirmed by Methods section 2.3.1.' if stem=='Field_harvest_biomass' else
                  'Annual grain means and standard errors are digitized from Supplementary Figure S2; both '
                  'observed and simulated yields use 13% grain moisture. Digitization uncertainty is separate from replicate SE.')
        save(ROOT,fig,stem,caption)


def report():
    frozen=json.loads((ROOT/'parameters/frozen_model.json').read_text())
    metrics=pd.read_csv(ROOT/'tables/field_metrics.csv')
    lines=['Crop-water calibration with antecedent measured initialization','',
        'Field calibration comprises the two normal rainfall years, 2016 and 2017, and the first dry year, 2018. '
        'The final dry year, 2019, remains outside the objective. Earlier field trait calibration already used 2017; '
        'that year remains in calibration. Testing is retrospective because outcomes were inspected during previous development. '
        'The original normal-year calibration and two-dry-year extrapolation results remain archived separately.','',
        'The retained crop mechanism computes radiation-based assimilation, water-limited growth, canopy expansion and senescence, '
        'grain production and layered soil-water balance. Each crop has eleven fitted parameters, including one bounded root-uptake compensation fraction. Soil properties, '
        'weather, irrigation dates and amounts, phenology, source units and quality exclusions remain unchanged. Lagged measured moisture replaces initial_theta at 26 Fengqiu cases under one pre-crop rule for both partitions; precise sampling/aggregation and subplot correspondence remain unresolved. '
        'Trait multipliers obey case-specific limits of 1.5 for the study transpiration-coefficient prior and 2.0 for leaf allocation. '
        'The empirical assimilation-water response is retained. Local water stress and extraction capacities constrain the new compensated uptake; the reference retains bulk root-zone depletion.','',
        'Calibration includes 3,771 station observations across 71 crop cases and all five eligible sites, '
        'together with 24 Wuqiao crop-treatment seasons. ET, grain yield and confirmed harvest dry biomass constrain each field case. '
        'Station partitions and objective weights remain unchanged. Ground-level cutting and oven drying establish the field biomass basis; '
        'grain yield is expressed at 13% moisture. [Yang et al. (2024)](https://doi.org/10.1016/j.agwat.2024.108726).','',
        'Model selection uses equal-crop mean calibration data loss before final-year evaluation. The complete evaluation retains '
        '132 station cases, 7,397 station observations and 32 field crop-treatment seasons per model.','',
        f'The selected version is {frozen["selected_version"]}. Calibration data loss is '
        f'{frozen["mean_crop_calibration_data_loss"]["reference"]:.6f} for the retained reference and '
        f'{frozen["mean_crop_calibration_data_loss"]["soil_refit"]:.6f} for the new calibration.','',
        '| Version | Crop | Partition | ET RMSE (mm) | ET nRMSE (%) | ET bias (mm) | ET R² | W3−W0 ET error (mm) | Yield nRMSE (%) | Biomass nRMSE (%) |',
        '|---|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for version in ['reference','soil_refit']:
        for crop in ['wheat','maize']:
            for split in ['calibration','validation']:
                g=metrics[metrics.version.eq(version)&metrics.crop.eq(crop)&metrics.split.eq(split)].set_index('variable')
                e,y,b,d=g.loc['seasonal_et'],g.loc['yield'],g.loc['biomass'],g.loc['seasonal_et_response']
                lines.append(f'| {version} | {crop.capitalize()} | '+('Calibration' if split=='calibration' else 'Retrospective testing')+
                    f' | {e.rmse:.2f} | {e.nrmse_percent:.2f} | {e.bias:.2f} | {e.r_squared:.3f} | {d.rmse:.2f} | {y.nrmse_percent:.2f} | {b.nrmse_percent:.2f} |')
    lines+=['',
        'The working criteria require seasonal ET RMSE ≤50 mm, nRMSE ≤15%, absolute bias ≤10% of observed mean, positive NSE, '
        'W3−W0 ET-response error ≤50 mm and field-yield nRMSE ≤20%. These are study criteria rather than universal journal thresholds.','',
        'The final-year field test contains four treatment means per crop and one annual irrigation-response contrast. '
        'It provides limited evidence of transfer across years. The earlier two-year drought-extrapolation test represents models '
        'that did not fit 2018 observations; those results cannot be treated as independent validation of the new calibration.','',
        'Field ET derives from 0–2 m soil-water balance with negligible runoff and drainage assumed by the source. '
        'Layer-specific initial water, hydraulic properties, exact storage-sampling dates and residue cover remain uncertain. '
        'Unresolved lysimeter crop-plot management and bottom boundaries make some daily station ET comparisons conditional. '
        'Parameter convergence and numerical water/carbon closure do not establish irrigation-policy accuracy.','']
    (ROOT/'evaluation.md').write_text('\n'.join(lines))


def field_figure():
    q = pd.read_csv(ROOT / 'predictions/field_comparisons.csv')
    q = q[q.version.eq(selected_version())]
    fig, axes = plt.subplots(2, 4, figsize=(11.4, 5.7), sharey=True, layout='constrained')
    for i, crop in enumerate(['wheat', 'maize']):
        for j, year in enumerate([2016, 2017, 2018, 2019]):
            ax = axes[i, j]
            g = q[q.crop.eq(crop) & q.harvest_year.eq(year)].sort_values('treatment')
            assert len(g) == 4 and g.et_eligible.all()
            ax.plot(range(4), g.observed_et_mm, color='black', marker='o', ms=4, lw=1)
            ax.plot(range(4), g.predicted_et_mm, color=BLUE, marker='s', ms=3, lw=1.3)
            ax.set_xticks(range(4), ['W0', 'W1', 'W2', 'W3'])
            ax.set_ylim(0, max(600., float(max(q.observed_et_mm.max(), q.predicted_et_mm.max())) * 1.06))
            if j == 0:
                ax.set_ylabel('Seasonal actual ET (mm)')
            style(ax, i * 4 + j, f'{crop.capitalize()} {year} · ' + ('calibration' if year < 2019 else 'testing'))
    fig.legend(handles=[Line2D([], [], color='black', marker='o', label='Published water-balance ET'),
                        Line2D([], [], color=BLUE, marker='s', label='Frozen calibrated model')],
               loc='outside lower center', ncol=2, frameon=False, fontsize=8)
    save(ROOT, fig, 'Seasonal_field_ET_frozen_calibration',
         'Seasonal actual ET under the frozen calibration selected with confirmed field biomass. The 2016–2018 '
         'treatment-seasons contribute to calibration; 2019 testing is retrospective. '
         'W0–W3 denote current wheat irrigation and preceding wheat irrigation for maize. '
         'Current maize management is identical among treatments in each year. Published ET '
         'is inferred from 0–2 m water balance assuming negligible runoff and drainage; numeric '
         'observational dispersion is unavailable. Simulated actual ET is soil evaporation '
         'plus transpiration plus canopy evaporation. All 32 field treatment-seasons are retained.')


def seasonal_curves():
    q = pd.read_csv(ROOT / 'predictions/station_comparisons.csv', low_memory=False)
    q = q[q.version.eq(selected_version()) & q.variable.isin(['lai', 'biomass', 'et'])].copy()
    inv = pd.read_csv(ROOT / 'data/case_inventory.csv').set_index('case_id')
    start = pd.to_datetime(q.case_id.map(inv.start_date))
    q['das'] = ((pd.to_datetime(q.window_start) - start).dt.days +
                (pd.to_datetime(q.window_end) - start).dt.days) / 2
    q['bin'] = np.floor(q.das / q.crop.map({'wheat': 15, 'maize': 7})).astype(int)
    keys = ['crop', 'split', 'variable', 'site', 'source_group_id', 'case_id', 'bin']
    cases = q.groupby(keys)[['value', 'predicted', 'das']].mean().reset_index()
    years = cases.groupby([k for k in keys if k != 'case_id'])[['value', 'predicted', 'das']].mean().reset_index()
    rows = []
    for (crop, split, variable, bin_index), g in years.groupby(['crop', 'split', 'variable', 'bin']):
        sites = g.groupby('site')[['value', 'predicted', 'das']].mean()
        weights = np.array([1 / (g.site.nunique() * g.site.eq(r.site).sum()) for r in g.itertuples()])
        observed = sites.value.mean()
        effective_n = 1 / np.sum(weights ** 2)
        se = np.sqrt(np.sum(weights * (g.value - observed) ** 2) /
                     (1 - 1 / effective_n) / effective_n) if effective_n > 1 else np.nan
        rows.append(dict(crop=crop, split=split, variable=variable, bin=bin_index,
                         das=sites.das.mean(), observed=observed, predicted=sites.predicted.mean(),
                         observed_SE=se, sites=len(sites), site_years=len(g),
                         effective_site_years=effective_n, version=selected_version()))
    curves = pd.DataFrame(rows)
    curves.to_csv(ROOT / 'tables/balanced_seasonal_curves.csv', index=False)
    fig, axes = plt.subplots(4, 3, figsize=(10.1, 10), layout='constrained')
    variables = [('lai', 'LAI (m² m⁻²)', 1),
                 ('biomass', 'Aboveground dry biomass (t ha⁻¹)', .001),
                 ('et', 'Daily actual ET (mm d⁻¹)', 1)]
    for row, (crop, split) in enumerate([(c, s) for c in ['wheat', 'maize'] for s in ['calibration', 'validation']]):
        for col, (variable, label, factor) in enumerate(variables):
            ax = axes[row, col]
            g = curves[curves.crop.eq(crop) & curves.split.eq(split) & curves.variable.eq(variable)].sort_values('bin')
            ax.errorbar(g.das, g.observed * factor, yerr=g.observed_SE * factor,
                        color='black', marker='o', ms=3, ls='none', capsize=2, lw=.8)
            for _, part in g.groupby(g.bin.diff().gt(1).cumsum()):
                ax.plot(part.das, part.predicted * factor, color=BLUE, lw=1.3)
            # Common scales within each crop's calibration/testing comparison.
            all_crop = curves[curves.crop.eq(crop) & curves.variable.eq(variable)]
            upper = max(float((all_crop.observed + all_crop.observed_SE.fillna(0)).max()),
                        float(all_crop.predicted.max())) * factor
            ax.set_ylim(0, upper * 1.1)
            all_das = curves[curves.crop.eq(crop)].das
            ax.set_xlim(min(0, float(all_das.min()) - 5), float(all_das.max()) + 5)
            ax.set_xlabel('Days after sowing')
            ax.set_ylabel(label)
            style(ax, row * 3 + col, crop.capitalize() + ' · ' + ('calibration' if split == 'calibration' else 'testing'))
    fig.legend(handles=[Line2D([], [], color='black', marker='o', ls='none', label='Observed mean ± descriptive SE'),
                        Line2D([], [], color=BLUE, label='Frozen calibrated model')],
               loc='outside lower center', ncol=2, frameon=False, fontsize=8)
    save(ROOT, fig, 'Multisite_crop_seasonal_curves',
         'Observed and simulated LAI, aboveground dry biomass and actual ET at matched sampling '
         'windows, shown in 15-day wheat and 7-day maize bins and original calibration/testing partitions. '
         'Cases receive equal weights within site-years, site-years within sites, and sites within '
         'available bins. The contributing population can change among bins; pooled curves are '
         'descriptive matched-window comparisons, not the trajectory of one field. Error bars are '
         'weighted descriptive standard errors across site-year means, not model or measurement '
         'uncertainty. Missing bins remain gaps. Accuracy uses original unbinned observations. '
         'All eligible sites are retained; testing years have been previously inspected.')


def diagnostics():
    observations = pd.read_csv(ROOT / 'data/observations.csv', low_memory=False)
    inventory = pd.read_csv(ROOT / 'data/case_inventory.csv')
    variables = pd.DataFrame({'variable': sorted(observations.variable.unique())})
    availability = inventory.merge(variables, how='cross').merge(
        observations.groupby(['case_id', 'variable']).size().rename('matched_records').reset_index(),
        on=['case_id', 'variable'], how='left')
    availability['matched_records'] = availability.matched_records.fillna(0).astype(int)
    availability['trajectory_retained'] = True
    availability['new_residual_exclusion'] = False
    availability.to_csv(ROOT / 'tables/case_data_availability.csv', index=False)
    f = pd.read_csv(ROOT / 'predictions/field_comparisons.csv')
    flags = f[['case_id', 'version', 'crop', 'split', 'germination_start_date', 'anthesis_date',
               'grain_13pct_kg_ha', 'yield_13pct_kg_ha']].copy()
    flags['positive_observed_grain_but_simulated_zero'] = f.yield_13pct_kg_ha.gt(0) & f.grain_13pct_kg_ha.lt(1e-6)
    flags['observations_excluded_by_residual'] = False
    flags['field_biomass_basis_confirmed'] = True
    flags['field_biomass_used_for_fitting'] = f.version.eq('soil_refit') & f.split.eq('calibration')
    flags.to_csv(ROOT / 'tables/quality_flags.csv', index=False)
    metrics = pd.read_csv(ROOT / 'tables/field_metrics.csv')
    checks = []
    for row in metrics[metrics.variable.eq('seasonal_et')].itertuples():
        g = f[f.version.eq(row.version) & f.crop.eq(row.crop) & f.split.eq(row.split)]
        other = metrics[metrics.version.eq(row.version) & metrics.crop.eq(row.crop) & metrics.split.eq(row.split)].set_index('variable')
        criteria = dict(RMSE_le50=row.rmse <= 50, nRMSE_le15=row.nrmse_percent <= 15,
                        absolute_bias_le10pct=abs(row.bias) <= .1 * g.observed_et_mm.mean(),
                        NSE_positive=row.nse > 0, response_RMSE_le50=other.loc['seasonal_et_response', 'rmse'] <= 50)
        checks.append(dict(version=row.version, crop=row.crop, split=row.split, **criteria,
                           working_ET_checks_pass=all(criteria.values()),
                           confirmed_field_yield_nRMSE_le20=other.loc['yield', 'nrmse_percent'] <= 20,
                           field_biomass_is_conditional=False))
    pd.DataFrame(checks).to_csv(ROOT / 'tables/seasonal_ET_working_checks.csv', index=False)
    residuals = f.copy()
    residuals['ET_error_mm'] = residuals.predicted_et_mm - residuals.observed_et_mm
    residuals['squared_ET_error'] = residuals.ET_error_mm ** 2
    residuals['split_SSE_share_percent'] = 100 * residuals.squared_ET_error / residuals.groupby(
        ['version', 'crop', 'split']).squared_ET_error.transform('sum')
    residuals.to_csv(ROOT / 'tables/ET_residual_concentration.csv', index=False)
    daily = pd.read_csv(ROOT / 'predictions/daily_predictions.csv.gz', low_memory=False)
    daily = daily[daily.case_id.str.startswith('yang2024')]
    rows = []
    for (version, case), g in daily.groupby(['version', 'case_id']):
        active = g[g.potential_transpiration_after_interception_mm.ge(.5)]
        rows.append(dict(version=version, case_id=case, crop=g.crop.iloc[0], split=g.split.iloc[0],
                         root_depth_max_mm=g.root_depth_mm.max(), active_demand_days=len(active),
                         mean_water_factor=active.water_factor.mean(),
                         mean_FAO_stress=active.root_zone_stress_factor.mean(),
                         strong_stress_days=int(active.water_factor.lt(.5).sum()),
                         total_soil_evaporation_mm=g.soil_evaporation_mm.sum(),
                         total_transpiration_mm=g.transpiration_mm.sum(),
                         total_canopy_evaporation_mm=g.canopy_evaporation_mm.sum(),
                         maximum_LAI=g.lai.max()))
    pd.DataFrame(rows).to_csv(ROOT / 'tables/crop_water_diagnostics.csv', index=False)
    weather, irrigation, soils = [], [], []
    for kind in ['station', 'field']:
        for path in sorted((ROOT / 'inputs/resolved' / selected_version() / kind).glob('*.json')):
            p = json.loads(path.read_text())
            case = path.name.removesuffix('_crop.json') if kind == 'field' else path.stem
            segments = [('crop', p['inputs'])] + ([('presowing', p['presowing']['inputs'])] if p.get('presowing') else [])
            for segment, inputs in segments:
                weather.extend(dict(d, case_id=case, kind=kind, segment=segment) for d in inputs['weather'])
                irrigation.extend(dict(d, case_id=case, kind=kind, segment=segment)
                                  for d in inputs.get('irrigation_events', []))
                soils.extend(dict(d, case_id=case, kind=kind, segment=segment, layer_index=i)
                             for i, d in enumerate(inputs['soil_layers']))
    for frame, filename in [(pd.DataFrame(weather), 'exact_used_weather.csv.gz'),
                            (pd.DataFrame(irrigation), 'exact_used_irrigation.csv'),
                            (pd.DataFrame(soils), 'exact_used_soil_layers.csv')]:
        destination = ROOT / 'data' / filename
        if destination.exists():
            # Unchanged scenario inputs retain their exact archived bytes.
            serialized = pd.read_csv(StringIO(frame.to_csv(index=False)), low_memory=False)
            retained = pd.read_csv(destination, low_memory=False)
            pd.testing.assert_frame_equal(serialized, retained, check_dtype=False,
                                          check_exact=False, rtol=1e-12, atol=1e-12)
        else:
            frame.to_csv(destination, index=False)
    parameter_rows, history, calibration_station, calibration_field = [], [], [], []
    for path in sorted((ROOT / 'parameters/candidates').glob('*.json')):
        c = json.loads(path.read_text())
        for name, value, low, high in zip(c['vector_order'], c['vector'], c['lower_bounds'], c['upper_bounds']):
            parameter_rows.append(dict(crop=c['crop'], control=c['control'], parameter=name, value=value,
                                       lower_bound=low, upper_bound=high, field_biomass_fitted=True))
        history.append(pd.read_csv(path.with_name(path.stem + '_history.csv')))
        suffix = c['crop'] + '_' + c['control'] + '.csv'
        calibration_station.append(pd.read_csv(ROOT / 'data' / ('used_calibration_station_' + c['crop'] + '.csv'), low_memory=False).assign(control=c['control']))
        calibration_field.append(pd.read_csv(ROOT / 'data' / ('used_calibration_field_' + c['crop'] + '.csv')).assign(control=c['control']))
    pd.DataFrame(parameter_rows).to_csv(ROOT / 'tables/fitted_parameters.csv', index=False)
    pd.concat(history).to_csv(ROOT / 'tables/calibration_history.csv', index=False)
    pd.concat(calibration_station).to_csv(ROOT / 'tables/used_station_calibration.csv', index=False)
    pd.concat(calibration_field).to_csv(ROOT / 'tables/used_field_calibration.csv', index=False)


def workbook():
    tables = {}
    for variable, g in pd.read_csv(ROOT / 'data/observations.csv', low_memory=False).groupby('variable'):
        tables['Observed_' + variable] = g
    for variable, g in pd.read_csv(ROOT / 'predictions/station_comparisons.csv', low_memory=False).groupby('variable'):
        tables['Predicted_' + variable] = g
    files = [('Confirmed_field_biomass', 'data/confirmed_field_biomass.csv'),
             ('Field_partition', 'data/field_partition.csv'),
             ('Calibration_losses', 'tables/calibration_data_losses.csv'),
             ('Raw_field_water', 'data/wuqiao_used_seasonal_observations.csv'),
             ('Annual_yield', 'data/wuqiao_digitized_annual_yields.csv'),
             ('Rain_source_audit', 'data/source_observations_with_rain_audit.csv'),
             ('Case_inventory', 'data/case_inventory.csv'), ('Partitions', 'data/partitions.csv'),
             ('Case_data_availability', 'tables/case_data_availability.csv'),
             ('Used_station_calibration', 'tables/used_station_calibration.csv'),
             ('Used_field_calibration', 'tables/used_field_calibration.csv'),
             ('Field_predictions', 'predictions/field_comparisons.csv'),
             ('Field_metrics', 'tables/field_metrics.csv'), ('Station_metrics', 'tables/station_metrics.csv'),
             ('Site_metrics', 'tables/site_metrics.csv'), ('ET_contrasts', 'tables/field_ET_contrasts.csv'),
             ('Seasonal_curves', 'tables/balanced_seasonal_curves.csv'),
             ('ET_residual_concentration', 'tables/ET_residual_concentration.csv'),
             ('Crop_water_diagnostics', 'tables/crop_water_diagnostics.csv'),
             ('Initialization_applied', 'data/initialization_applied.csv'),
             ('Initialization_availability', 'data/initialization_availability.csv'),
             ('Field_soil_water', 'tables/field_soil_water_diagnostics.csv'),
             ('Fitted_parameters', 'tables/fitted_parameters.csv'),
             ('Calibration_history', 'tables/calibration_history.csv'),
             ('Quality_flags', 'tables/quality_flags.csv'),
             ('Working_accuracy_checks', 'tables/seasonal_ET_working_checks.csv'),
             ('Exact_used_weather', 'data/exact_used_weather.csv.gz'),
             ('Exact_used_irrigation', 'data/exact_used_irrigation.csv'),
             ('Exact_used_soil_layers', 'data/exact_used_soil_layers.csv')]
    for label, path in files:
        tables[label] = pd.read_csv(ROOT / path, low_memory=False)
    tables['Source_SHA256'] = pd.DataFrame(json.loads((ROOT / 'verification/input_manifest.json').read_text()))
    with pd.ExcelWriter(ROOT / 'tables/validation_data_and_results.xlsx', engine='openpyxl') as writer:
        for label, frame in tables.items():
            frame.to_excel(writer, sheet_name=label[:31], index=False)
            sheet = writer.sheets[label[:31]]
            sheet.freeze_panes = 'A2'
            sheet.auto_filter.ref = sheet.dimensions
    return len(tables)


def main():
    if (ROOT / 'verification/artifact_manifest.json').exists():
        raise RuntimeError('Completed run preserved; use a separate directory.')
    register_analysis_sources()
    calibration_losses()
    diagnostics()
    # The raw observations are unchanged; preserve the exact sealed source exports.
    for stem in ['Raw_field_water_observations', 'Raw_eligible_station_dynamics', 'Raw_confirmed_field_biomass']:
        for extension in ['.png', '.pdf', '_caption.txt']:
            assert (ROOT / 'figures' / (stem + extension)).exists()
    field_figure()
    seasonal_curves()
    harvest_figures()
    from field_temporal_et import draw
    draw(ROOT, selected_version())
    from field_soil_water import draw as draw_soil_water
    draw_soil_water(ROOT, selected_version())
    report()
    sheets = workbook()
    for row in json.loads((ROOT / 'verification/input_manifest.json').read_text()):
        assert hashlib.sha256((ROOT / row['snapshot']).read_bytes()).hexdigest() == row['sha256'], row['snapshot']
    (ROOT / 'verification/packaging_receipt.json').write_text(json.dumps(dict(
        workbook_sheets=sheets, figure_pairs=9, selected_version=selected_version(),
        testing_retrospective=True, regional_promoted=False,
        packaged_at_utc=datetime.now(timezone.utc).isoformat()), indent=2) + '\n')
    print('Packaged both crop fits:', sheets, 'sheets and nine PNG/PDF figure pairs.', flush=True)


if __name__ == '__main__':
    main()
