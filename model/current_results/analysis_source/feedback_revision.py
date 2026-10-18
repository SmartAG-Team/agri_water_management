"""Source-backed figure statistics and manuscript integration for reviewer feedback."""
from pathlib import Path
import re

import pandas as pd
from pyproj import Geod
from scipy.optimize import brentq

from current_crop_publication import score, weights

ROOT = Path(__file__).resolve().parents[1]
PUB = ROOT / 'publication'
STATION_METRIC_NOTE = (
    'Colored annotations give calibration and retrospective-evaluation R² and RMSE for the plotted '
    'seasonal means. Each paired bin has equal weight, and counts refer to bins rather than individual '
    'observations or independent growing seasons. R² is squared Pearson correlation; RMSE uses the '
    'corresponding vertical-axis units. Record-level accuracy is evaluated separately.'
)
FIELD_METRIC_NOTE = (
    'Colored annotations give separate calibration and retrospective-testing R², RMSE and n, '
    'using equally weighted treatment-season means. R² is squared Pearson correlation and '
    'measures association rather than agreement with the identity line; RMSE is in the axis units.'
)
MAP_NOTE = 'North arrows indicate geographic north; scale bars use WGS84 ellipsoidal ground distances at their displayed latitude.'


def station_scores(frame):
    selected = frame[frame.version.eq('management_refit') & frame.variable.isin(['lai', 'biomass', 'et'])]
    rows = []
    for (crop, split, variable), group in selected.groupby(['crop', 'split', 'variable']):
        factor = .001 if variable == 'biomass' else 1.
        metrics = score(group.value * factor, group.predicted * factor, weights(group))
        rows.append(dict(crop=crop, split=split, variable=variable,
                         unit={'lai': 'm² m⁻²', 'biomass': 't ha⁻¹', 'et': 'mm d⁻¹'}[variable],
                         **metrics))
    return pd.DataFrame(rows)


def seasonal_mean_scores(curves):
    """Score exactly the displayed paired means, with equal weight per available bin."""
    rows = []
    for (crop, split, variable), group in curves.groupby(['crop', 'split', 'variable']):
        if group.bin.duplicated().any():
            raise ValueError(f'Duplicate seasonal bins: {crop}/{split}/{variable}')
        paired = group.dropna(subset=['observed', 'predicted'])
        factor = .001 if variable == 'biomass' else 1.
        metrics = score(paired.observed * factor, paired.predicted * factor)
        rows.append(dict(crop=crop, split=split, variable=variable, sample_basis='paired bins',
                         unit={'lai': 'm² m⁻²', 'biomass': 't ha⁻¹', 'et': 'mm d⁻¹'}[variable],
                         **metrics))
    return pd.DataFrame(rows)


def field_scores(frame):
    if 'version' in frame:
        frame = frame[frame.version.eq('management_refit')]
    rows = []
    for (crop, split), group in frame.groupby(['crop', 'split']):
        for variable, observed, predicted, factor, unit in [
            ('et', 'observed_et_mm', 'predicted_et_mm', 1., 'mm'),
            ('grain', 'yield_13pct_kg_ha', 'grain_13pct_kg_ha', .001, 't ha⁻¹'),
        ]:
            rows.append(dict(crop=crop, split=split, variable=variable, unit=unit,
                             **score(group[observed] * factor, group[predicted] * factor)))
    return pd.DataFrame(rows)


def scale_bar_longitudes(longitude, latitude, length_km):
    """Equal-latitude endpoints whose ground distances match the scale labels."""
    geod = Geod(ellps='WGS84')
    return [longitude] + [brentq(
        lambda end: geod.inv(longitude, latitude, end, latitude)[2] - distance,
        longitude, longitude + 20.,
    ) for distance in [length_km * 500., length_km * 1000.]]


def restore_unchanged_citations(document, previous):
    """Retain reviewed source attribution when regenerated prose is otherwise identical."""
    attributed = {
        re.sub(r'\s*\[CITE:[^\]]+\]', '', paragraph): paragraph
        for block in previous['blocks'] for paragraph in block.get('paragraphs', [])
        if '[CITE:' in paragraph
    }
    for block in document['blocks']:
        block['paragraphs'] = [attributed.get(p, p) for p in block.get('paragraphs', [])]


def apply_manuscript(article, supplement):
    """Integrate complementary statistics and cartographic definitions before numbering."""
    methods = next(b for b in article['blocks'] if b.get('heading', '').startswith('2.8.'))
    clarification = (' R² describes association and can remain high despite systematic bias; '
                     'agreement with observations is assessed jointly with RMSE, bias and NSE. '
                     'Seasonal-curve annotations use paired plotted bin means with equal weights across '
                     'bins and counts of paired bins. Treatment-season scatter annotations use individual '
                     'treatment means. Each partition is scored separately in the displayed variable units.')
    if clarification.strip() not in methods['paragraphs'][0]:
        methods['paragraphs'][0] += clarification
    methods['paragraphs'][0] = methods['paragraphs'][0].replace(
        'Station scores give equal weight', 'Record-level station scores give equal weight')
    methods['paragraphs'][1] = methods['paragraphs'][1].replace(
        'Accuracy scores use unbinned records.',
        'Record-level accuracy scores use unbinned records; seasonal-curve annotations describe the paired bin means.')
    station = next(b for b in article['blocks'] if b.get('heading', '').startswith('3.1.'))
    note = (' Record-level performance scores use unbinned observations with hierarchical weights; '
            'the seasonal-curve annotations quantify agreement between the plotted bin means.')
    if note.strip() not in station['paragraphs'][0]:
        station['paragraphs'][0] += note
    for block in article['blocks']:
        image = block.get('figure', '')
        addition = (STATION_METRIC_NOTE if image.endswith('current_Figure_4_multisite_seasonal_curves.png')
                    else FIELD_METRIC_NOTE if image.endswith('current_Figure_5_Wuqiao_calibration_testing.png')
                    else MAP_NOTE if image.endswith(('Figure_1_domain_and_observed_storage.png',
                                                     'Figure_7_current_spatial_policy_outcomes.png')) else '')
        if addition and addition not in block['caption']:
            block['caption'] += ' ' + addition
        if block.get('table') == 'tables/main_benchmark_metrics.csv':
            block.update(
                table_caption='Table 3. Wuqiao bias, relative error and efficiency by fitting and testing partition.',
                columns=['Crop', 'Partition', 'Target', 'n', 'Unit', 'Bias', 'nRMSE_pct', 'NSE'],
                column_weights=[.6, .9, 1.0, .3, .65, .65, .75, .6],
                header_labels={'Partition': 'Period', 'Bias': 'MBE', 'nRMSE_pct': 'nRMSE (%)'},
            )
    # Annual-grain R² and RMSE are in the main scatter figure; this table has
    # the distinct purpose of evaluating within-year irrigation contrasts.
    annual = pd.read_csv(PUB / 'tables/current_annual_yield_evaluation.csv')
    annual[annual.Comparison.eq('Within-year W1–W3 minus W0')].to_csv(
        PUB / 'tables/supplement_annual_yield_evaluation.csv', index=False)
    table = next(b for b in supplement['blocks'] if b.get('table') == 'tables/supplement_annual_yield_evaluation.csv')
    table['table_caption'] = 'Table S5. Wuqiao within-year grain-treatment contrasts by fitting and testing partition.'
    return article, supplement
