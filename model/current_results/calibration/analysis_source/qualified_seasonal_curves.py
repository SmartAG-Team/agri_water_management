from pathlib import Path
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from diagnostic_plot_helpers import plt, BLUE, style, save
from package_crop_run import selected_version
ROOT=Path(__file__).resolve().parents[1]

def draw():
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
            period = 'conditional transfer\ncalibration years' if split == 'calibration' else 'conditional transfer\ntesting years'
            if variable == 'et':
                period = 'conditional ET · ' + ('calibration years' if split == 'calibration' else 'testing years')
            style(ax, row * 3 + col, crop.capitalize() + ' · ' + period)
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
         'All eligible sites are retained; testing years have been previously inspected. Station growth '
         'comparisons are conditional because complete matched irrigation logs are unverified. These '
         'curves do not establish independent management validation. Station ET remains '
         'conditional because irrigation-log completeness and lysimeter boundaries are unresolved. '
         'Station observations are excluded from the new water fit; growth coefficients retain their earlier multisite fitting history; these panels do not '
         'establish calibration accuracy or independent ET validation.')
