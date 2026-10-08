"""Model cumulative ET trajectories with independently reported seasonal endpoints."""
from pathlib import Path
import gzip
import json

import pandas as pd
from matplotlib.lines import Line2D

from diagnostic_plot_helpers import plt, style, save


def draw(root: Path, version: str):
    field = pd.read_csv(root / 'predictions/field_comparisons.csv')
    field = field[field.version.eq(version)]
    colors = {'W0': '#40576D', 'W1': '#439C9D', 'W2': '#DCAC50', 'W3': '#A85F78'}
    figure, axes = plt.subplots(2, 4, figsize=(11.4, 5.9), sharex='row', sharey=True, layout='constrained')
    for row, crop in enumerate(['wheat', 'maize']):
        for column, year in enumerate([2016, 2017, 2018, 2019]):
            axis = axes[row, column]
            cases = field[field.crop.eq(crop) & field.harvest_year.eq(year)]
            assert len(cases) == 4
            for case in cases.itertuples():
                filename = root / 'predictions/full_daily' / version / 'field' / (case.case_id + '.json.gz')
                with gzip.open(filename, 'rt') as stream:
                    daily = pd.DataFrame(json.load(stream))
                dates = pd.to_datetime(daily.date)
                days = (dates - dates.iloc[0]).dt.days
                cumulative = daily.et_mm.cumsum()
                assert abs(cumulative.iloc[-1] - case.predicted_et_mm) < 1e-8
                color = colors[case.treatment]
                axis.plot(days, cumulative, color=color, lw=1.2)
                axis.plot(days.iloc[-1], case.observed_et_mm, marker='o', ls='none',
                          color=color, markeredgecolor='white', markeredgewidth=.4, ms=4.5)
            axis.set_xlim(left=0)
            axis.set_ylim(0, max(600., field[['observed_et_mm', 'predicted_et_mm']].max().max() * 1.08))
            axis.set_xlabel('Days after sowing')
            if column == 0:
                axis.set_ylabel('Cumulative actual ET (mm)')
            split = 'calibration' if year < 2019 else 'retrospective testing'
            style(axis, row * 4 + column, f'{crop.capitalize()} {year} · {split}')
    handles = [Line2D([], [], color=color, lw=1.4, label=treatment) for treatment, color in colors.items()]
    handles += [Line2D([], [], color='black', marker='o', ls='none', label='Observed seasonal total')]
    figure.legend(handles=handles, loc='outside lower center', ncol=5, frameon=False, fontsize=8)
    save(root, figure, 'Cumulative_field_ET',
         'Cumulative simulated actual evapotranspiration and published seasonal water-balance ET totals at Wuqiao. '
         'Colored lines are model trajectories; colored endpoint markers are observed seasonal totals. '
         'The field dataset does not contain observed daily ET trajectories. Wheat treatment labels describe '
         'current irrigation, whereas maize labels describe the preceding wheat treatment; current maize '
         'management is shared within each year. The 2016–2018 seasons enter calibration and 2019 is '
         'retrospective testing. Actual ET comprises transpiration, soil evaporation and canopy evaporation. '
         'Observed ET assumes negligible runoff and drainage; numerical replicate dispersion is unavailable.')
