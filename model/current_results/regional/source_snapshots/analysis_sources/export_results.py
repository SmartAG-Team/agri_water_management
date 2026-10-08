"""Publication exports and independent checks of frozen adaptive simulations."""
from pathlib import Path
import hashlib
import json

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
COLORS = {'adaptive_95': '#0072B2', 'adaptive_98': '#D55E00'}
LABELS = {'adaptive_95': '95% training target', 'adaptive_98': '98% training target'}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def figures():
    plt.rcParams.update({'font.family': 'Arial', 'font.size': 8, 'pdf.fonttype': 42,
        'axes.spines.top': False, 'axes.spines.right': False,
        'axes.labelcolor': '#222222', 'text.color': '#222222'})
    frame = pd.read_csv(ROOT / 'tables/regional_policy_annual.csv')
    test = frame[frame.period.eq('testing') & frame.policy.isin(COLORS)]
    test.to_csv(ROOT / 'tables/figure_adaptive_testing_values.csv', index=False)
    fig, axs = plt.subplots(2, 2, figsize=(7.2, 5.7), layout='constrained')
    for policy, g in test.groupby('policy'):
        color = COLORS[policy]
        for ax, y in [(axs[0, 0], 'irrigation_mm'), (axs[0, 1], 'grain_retention_pct'),
                      (axs[1, 0], 'et_reduction_mm')]:
            ax.plot(g.harvest_year, g[y], '-', color=color, lw=1.2, label=LABELS[policy])
            observed = g.class_available_area_pct.gt(0)
            ax.scatter(g.loc[observed, 'harvest_year'], g.loc[observed, y], s=18,
                       marker='o' if policy == 'adaptive_95' else 's', color=color, zorder=3)
            ax.scatter(g.loc[~observed, 'harvest_year'], g.loc[~observed, y], s=23,
                       marker='o' if policy == 'adaptive_95' else 's', facecolors='white',
                       edgecolors=color, lw=.9, zorder=4)
        axs[1, 1].plot(g.irrigation_reduction_mm, g.grain_retention_pct, linestyle='none',
                       marker='o' if policy == 'adaptive_95' else 's', ms=4.5,
                       color=color, label=LABELS[policy])
        missing = g[g.class_available_area_pct.eq(0)]
        axs[1, 1].scatter(missing.irrigation_reduction_mm, missing.grain_retention_pct,
            marker='o' if policy == 'adaptive_95' else 's', s=23,
            facecolors='white', edgecolors=color, zorder=4)
    for ax in [axs[0, 0], axs[0, 1], axs[1, 0]]:
        ax.set_xlim(2013.5, 2025.5)
        ax.set_xticks([2014, 2016, 2018, 2020, 2022, 2025])
        ax.set_xlabel('Harvest year')
        for year in [2014, 2018, 2019]:
            ax.axvspan(year-.35, year+.35, color='#EEEEEE', zorder=0)
    axs[0, 0].axhline(380, color='#555555', ls='--', lw=.8)
    axs[0, 0].set_ylabel('Field irrigation (mm)')
    axs[0, 0].set_ylim(0, 420)
    axs[0, 0].text(2025.2, 391, 'Conventional', ha='right', fontsize=7, color='#555555')
    axs[0, 0].legend(frameon=False, fontsize=7, loc='lower left')
    axs[0, 1].axhline(100, color='#555555', ls='--', lw=.8)
    axs[0, 1].axhline(95, color='#777777', ls=':', lw=.8)
    axs[0, 1].set_ylabel('Conventional grain retained (%)')
    axs[0, 1].set_ylim(85, 104)
    axs[1, 0].axhline(0, color='#555555', lw=.8)
    axs[1, 0].set_ylabel('Actual ET reduction (mm)')
    axs[1, 0].set_ylim(-5, max(test.et_reduction_mm)*1.2)
    axs[1, 1].axhline(95, color='#777777', ls=':', lw=.8)
    axs[1, 1].set_xlabel('Field irrigation reduction (mm)')
    axs[1, 1].set_ylabel('Conventional grain retained (%)')
    axs[1, 1].set_xlim(-10, 300)
    axs[1, 1].set_ylim(85, 104)
    axs[1, 1].text(.98, .08, 'Open markers: missing-storage years\nConventional irrigation applied',
                    transform=axs[1, 1].transAxes, ha='right', fontsize=7)
    for ax, panel in zip(axs.flat, 'abcd'):
        ax.text(.02, .98, f'({panel})', transform=ax.transAxes, ha='left', va='top',
                weight='bold', fontsize=10)
        ax.grid(axis='y', color='#EEEEEE', lw=.6)
    for extension in ['png', 'pdf']:
        fig.savefig(ROOT / f'figures/adaptive_irrigation_testing.{extension}', dpi=600)
    plt.close(fig)
    # Daily storage makes the unchanged, continuous soil-state trajectory visible.
    fig, axs = plt.subplots(2, 1, figsize=(7.2, 4.7), sharex=True, layout='constrained')
    plotted = []
    for ax, rid, panel in zip(axs, [0, 31], 'ab'):
        d = pd.read_csv(ROOT / f'predictions/per_representative/rep_{rid:03d}.daily.csv.gz')
        d = d[(d.date >= '2018-10-12') & (d.date <= '2020-10-05')].copy()
        plotted.append(d)
        for policy in ['conventional_replay', 'adaptive_95', 'adaptive_98']:
            q = d[d.policy.eq(policy)]
            color = COLORS.get(policy, '#555555')
            label = LABELS.get(policy, 'Conventional')
            ax.plot(pd.to_datetime(q.date), q.soil_storage_final_mm, lw=.9, color=color,
                    ls='--' if policy == 'conventional_replay' else '-', label=label)
        ax.set_ylabel('0–2 m soil storage (mm)')
        ax.text(.015, .96, f'({panel}) Simulation unit {rid}', transform=ax.transAxes,
                ha='left', va='top', weight='bold', fontsize=9)
        ax.grid(axis='y', color='#EEEEEE', lw=.6)
        ax.axvline(pd.Timestamp('2019-10-12'), color='#999999', lw=.8, ls=':')
    axs[0].legend(frameon=False, loc='lower left', ncol=3, fontsize=7)
    axs[1].set_xlabel('Date')
    for extension in ['png', 'pdf']:
        fig.savefig(ROOT / f'figures/continuous_soil_storage.{extension}', dpi=600)
    plt.close(fig)
    pd.concat(plotted, ignore_index=True).to_csv(ROOT / 'tables/figure_storage_values.csv.gz', index=False)


def verify_and_workbook():
    checks = []
    def check(name, value, evidence=None):
        checks.append(dict(check=name, passed=bool(value), evidence=evidence))
        assert value, name
    receipts = json.loads((ROOT / 'verification/simulation_completion.json').read_text())
    decisions = pd.read_csv(ROOT / 'data/annual_policy_decisions.csv')
    predictions = pd.read_csv(ROOT / 'predictions/seasonal_predictions.csv')
    classes = pd.read_csv(ROOT / 'data/class_memberships.csv')
    summary = pd.read_csv(ROOT / 'tables/policy_evaluation_summary.csv')
    for name, expected in json.loads((ROOT / 'verification/input_manifest.json').read_text()).items():
        check('Unchanged input '+name, sha(ROOT / name) == expected)
    check('Native simulation segments complete', len(predictions) == 7656)
    check('Conventional replay exact', max(receipts['replay_maximum_difference'].values()) == 0)
    check('No annual soil-state reset', receipts['storage_continuity_error_max_mm'] == 0)
    check('Daily water conservation', receipts['daily_water_balance_residual_max_mm'] < 1e-6)
    check('Annual water conservation', receipts['annual_water_balance_residual_max_mm'] < 1e-6)
    check('Missing class retains full irrigation', decisions.loc[~decisions.class_available, 'quota_fraction'].eq(1).all())
    check('No new fit to training or testing outcomes', receipts['training_or_testing_outcomes_used_for_new_fit'] is False)
    crops = predictions[predictions.crop.isin(['wheat', 'maize']) & predictions.policy.isin(COLORS)]
    check('One quota shared by wheat and subsequent maize',
          crops.groupby(['policy', 'representative_id', 'harvest_year']).quota_fraction.nunique().eq(1).all())
    check('Measured antecedent periods before sowing',
          (classes.loc[classes.class_available, 'last_source_observation_interval_end']
           <= classes.loc[classes.class_available, 'scenario_sowing_date']).all())
    for rid in range(32):
        daily = pd.read_csv(ROOT / f'predictions/per_representative/rep_{rid:03d}.daily.csv.gz',
                            usecols=['policy', 'date', 'balance_residual_mm'])
        for policy, g in daily.groupby('policy'):
            dates = pd.to_datetime(g.date)
            expected = pd.date_range('1996-01-01', '2025-10-05')
            check(f'Continuous daily history {rid} {policy}',
                  len(dates) == len(expected) and dates.is_unique
                  and np.array_equal(dates.values, expected.values))
            check(f'Daily residual {rid} {policy}', g.balance_residual_mm.abs().max() < 1e-6)
    check('Primary class-available testing target failure retained',
          summary.query("policy == 'adaptive_95' and period == 'testing' and scope == 'class_available_years'").grain_retention_pct.iloc[0] < 95)
    check('Fallback years reported separately',
          sorted(summary.query("period == 'testing'").n_years.unique()) == [9, 12])
    (ROOT / 'verification/quality_checks.json').write_text(json.dumps(dict(
        checks=checks, check_count=len(checks), all_checks_passed=True,
        field_validation_performed=False, operational_GRACE_release_timing_verified=False), indent=2))
    # One worksheet per observation, input or result type. Full weather remains
    # in the unchanged compressed source file, identified by its input checksum.
    sheets = {
        'Observed_GRACE': pd.read_csv(ROOT / 'data/observed_regional_twsa_monthly.csv'),
        'Pre_sowing_storage': pd.read_csv(ROOT / 'data/pre_sowing_storage_availability.csv'),
        'Antecedent_precipitation': pd.read_csv(ROOT / 'data/antecedent_precipitation_by_representative_year.csv'),
        'Water_classes': classes,
        'Training_quota_rules': pd.read_csv(ROOT / 'data/training_selected_quotas.csv'),
        'Annual_decisions': decisions,
        'Representatives': pd.read_csv(ROOT / 'data/representative_cells.csv'),
        'Crop_season_predictions': predictions,
        'Rotation_predictions': pd.read_csv(ROOT / 'predictions/rotation_predictions.csv'),
        'Regional_annual_results': pd.read_csv(ROOT / 'tables/regional_policy_annual.csv'),
        'Strategy_evaluation': summary,
        'Class_evaluation': pd.read_csv(ROOT / 'tables/adaptive_class_evaluation.csv'),
        'Verification': pd.DataFrame(checks),
        'Source_checksums': pd.DataFrame([dict(file=n, sha256=v) for n, v in json.loads(
            (ROOT / 'verification/input_manifest.json').read_text()).items()])}
    with pd.ExcelWriter(ROOT / 'tables/adaptive_data_and_results.xlsx', engine='openpyxl') as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name, index=False)
    return len(checks)


def main():
    assert not (ROOT / 'verification/completion.json').exists(), 'Completed experiment is immutable'
    figures()
    count = verify_and_workbook()
    (ROOT / 'verification/completion.json').write_text(json.dumps(dict(
        simulation_and_export_complete=True, checks_passed=count,
        scientifically_field_validated=False, adaptive_rules_fitted_to_testing=False), indent=2))
    manifest = {str(p.relative_to(ROOT)): sha(p) for p in ROOT.rglob('*') if p.is_file()
                and '__pycache__' not in p.parts and 'native_process' not in p.parts}
    (ROOT / 'verification/file_manifest.json').write_text(json.dumps(manifest, indent=2))
    print(f'{count} source, decision and simulation checks passed; publication exports complete')


if __name__ == '__main__':
    main()
