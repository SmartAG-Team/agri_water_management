"""Conditional 0–2 m storage diagnostics; source sampling dates are unconfirmed."""
from pathlib import Path
import gzip
import json

import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from diagnostic_plot_helpers import plt, style, save


def draw(root: Path, selected: str):
    field = pd.read_csv(root / 'predictions/field_comparisons.csv')
    source = pd.read_csv(root / 'data/wuqiao_used_seasonal_observations.csv')
    assert not source.duplicated(['crop', 'harvest_year', 'treatment']).any()
    source = source.set_index(['crop', 'harvest_year', 'treatment'])
    rows, paths = [], {}
    for case in field.itertuples():
        payload = json.loads((root / 'inputs/resolved' / case.version / 'field' /
                              (case.case_id + '_crop.json')).read_text())
        assert abs(sum(layer['thickness_mm'] for layer in payload['inputs']['soil_layers']) - 2000.) < 1e-8
        with gzip.open(root / 'predictions/full_daily' / case.version / 'field' /
                       (case.case_id + '.json.gz'), 'rt') as stream:
            daily = pd.DataFrame(json.load(stream))
        observed = source.loc[(case.crop, case.harvest_year, case.treatment)]
        assert observed.soil_water_storage_depth_cm == 200.
        initial = float(daily.soil_storage_initial_mm.iloc[0])
        final = float(daily.soil_storage_final_mm.iloc[-1])
        depletion = initial - final
        row = dict(case_id=case.case_id, version=case.version, crop=case.crop,
                   split=case.split, harvest_year=case.harvest_year, treatment=case.treatment,
                   observed_initial_storage_mm=observed.soil_water_storage_initial_mm,
                   observed_depletion_mm=observed.soil_water_depletion_mm,
                   observed_final_storage_mm=observed.soil_water_storage_initial_mm-observed.soil_water_depletion_mm,
                   simulated_initial_storage_mm=initial, simulated_final_storage_mm=final,
                   simulated_depletion_mm=depletion,
                   conditional_depletion_difference_mm=depletion-observed.soil_water_depletion_mm,
                   source_sampling_dates_confirmed=False,
                   independent_of_source_water_balance_ET=False,
                   diagnostic_not_independent_validation=True)
        rows.append(row)
        paths[(case.version, case.case_id)] = daily
    output = pd.DataFrame(rows)
    output.to_csv(root / 'tables/field_soil_water_diagnostics.csv', index=False)
    colors = {'W0': '#40576D', 'W1': '#439C9D', 'W2': '#DCAC50', 'W3': '#A85F78'}
    chosen = output[output.version.eq(selected)]
    fig, axes = plt.subplots(2, 4, figsize=(11.4, 5.9), sharex='row', sharey=True, layout='constrained')
    for i, crop in enumerate(['wheat', 'maize']):
        for j, year in enumerate([2016, 2017, 2018, 2019]):
            ax = axes[i, j]
            for row in chosen[chosen.crop.eq(crop) & chosen.harvest_year.eq(year)].itertuples():
                daily = paths[(selected, row.case_id)]
                days = (pd.to_datetime(daily.date)-pd.to_datetime(daily.date.iloc[0])).dt.days+1
                ax.plot(np.r_[0, days], np.r_[row.simulated_initial_storage_mm, daily.soil_storage_final_mm],
                        color=colors[row.treatment], lw=1.2)
                ax.plot([0, days.iloc[-1]], [row.observed_initial_storage_mm, row.observed_final_storage_mm],
                        marker='o', ls='none', ms=4, color=colors[row.treatment], markeredgecolor='white', markeredgewidth=.4)
            ax.set_xlim(left=0)
            ax.set_ylim(0, max(800., float(chosen[['simulated_initial_storage_mm', 'observed_initial_storage_mm',
                                                  'simulated_final_storage_mm', 'observed_final_storage_mm']].max().max())*1.08))
            ax.set_xlabel('Days after sowing')
            if j == 0:
                ax.set_ylabel('0–2 m soil-water storage (mm)')
            style(ax, i*4+j, f'{crop.capitalize()} {year} · '+('calibration' if year<2019 else 'testing'))
    handles = [Line2D([], [], color=color, label=treatment) for treatment, color in colors.items()]
    handles.append(Line2D([], [], marker='o', ls='none', color='black', label='Reported seasonal endpoints'))
    fig.legend(handles=handles, loc='outside lower center', ncol=5, frameon=False, fontsize=8)
    save(root, fig, 'Field_soil_water_storage',
         'Simulated 0–2 m soil-water storage and source-reported seasonal endpoints. Observed final storage is '
         'initial storage minus reported depletion. Sampling dates and layer-specific initial moisture are '
         'unconfirmed; reported endpoints are positioned at the model season boundaries for diagnostic comparison. '
         'The source ET is calculated from the same seasonal water balance, so storage does not provide independent '
         'ET validation. Presowing simulation can change model storage before sowing. Wheat treatment labels describe '
         'current irrigation; maize labels describe preceding wheat treatments. All cases remain included.')
