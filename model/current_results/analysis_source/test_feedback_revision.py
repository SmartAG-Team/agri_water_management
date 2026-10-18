"""Guard the statistical grain, moisture units and geographic scale in feedback figures."""
import math

import pandas as pd
import pytest
from pyproj import Geod

import feedback_revision as revision


def test_station_scores_balance_sites_and_use_unbinned_selected_records():
    frame = pd.DataFrame([
        dict(version='management_refit', crop='wheat', split='calibration', variable='biomass',
             site='A', source_group_id='A2020', case_id='A1', value=0., predicted=0.),
        dict(version='management_refit', crop='wheat', split='calibration', variable='biomass',
             site='A', source_group_id='A2020', case_id='A1', value=2000., predicted=4000.),
        dict(version='management_refit', crop='wheat', split='calibration', variable='biomass',
             site='B', source_group_id='B2020', case_id='B1', value=4000., predicted=2000.),
        dict(version='reference', crop='wheat', split='calibration', variable='biomass',
             site='A', source_group_id='A2020', case_id='A1', value=900000., predicted=0.),
    ])
    scores = revision.station_scores(frame)
    assert len(scores) == 1
    row = scores.iloc[0]
    assert row.n == 3
    assert row.rmse == pytest.approx(math.sqrt(3.))
    assert row.r_squared == pytest.approx(2. / 11.)
    assert row.unit == 't ha⁻¹'


@pytest.mark.parametrize('variable,source_scale', [('et', 1.), ('biomass', 1000.)])
def test_seasonal_scores_use_equal_paired_bins_and_displayed_units(variable, source_scale):
    rows = [dict(crop='wheat', split='calibration', variable=variable, bin=i,
                 observed=o * source_scale, predicted=p * source_scale,
                 site_years=coverage)
            for i, (o, p, coverage) in enumerate([(0., 0., 100), (2., 4., 1), (4., 2., 1)])]
    rows += [dict(crop='wheat', split='validation', variable=variable, bin=i,
                  observed=o * source_scale, predicted=(o + 1.) * source_scale,
                  site_years=1) for i, o in enumerate([1., 3.])]
    rows.append(dict(crop='wheat', split='calibration', variable=variable, bin=8,
                     observed=float('nan'), predicted=100., site_years=1))
    result = revision.seasonal_mean_scores(pd.DataFrame(rows)).set_index('split')
    assert result.loc['calibration', 'n'] == 3
    assert result.loc['calibration', 'r_squared'] == pytest.approx(.25)
    assert result.loc['calibration', 'rmse'] == pytest.approx(math.sqrt(8. / 3.))
    assert result.loc['validation', 'n'] == 2
    assert result.loc['validation', 'r_squared'] == pytest.approx(1.)
    assert result.loc['validation', 'rmse'] == pytest.approx(1.)


def test_seasonal_scores_reject_duplicate_bins():
    row = dict(crop='maize', split='validation', variable='et', bin=3,
               observed=2., predicted=3.)
    with pytest.raises(ValueError, match='Duplicate'):
        revision.seasonal_mean_scores(pd.DataFrame([row, row]))


def test_field_scores_keep_partitions_and_convert_reported_grain_only_once():
    frame = pd.DataFrame([
        dict(crop='wheat', split=split, observed_et_mm=observed,
             predicted_et_mm=observed + 2., yield_13pct_kg_ha=observed * 1000.,
             grain_13pct_kg_ha=(observed + 2.) * 1000.)
        for split, values in [('calibration', [1., 2., 3.]), ('validation', [4., 5.])]
        for observed in values
    ])
    scores = revision.field_scores(frame).set_index(['split', 'variable'])
    assert scores.loc[('calibration', 'grain'), 'n'] == 3
    assert scores.loc[('validation', 'grain'), 'n'] == 2
    assert scores.rmse.to_numpy() == pytest.approx([2., 2., 2., 2.])
    assert scores.r_squared.to_numpy() == pytest.approx([1., 1., 1., 1.])
    assert scores.loc[('calibration', 'grain'), 'unit'] == 't ha⁻¹'


@pytest.mark.parametrize('latitude', [32.35, 36., 40.])
def test_scale_bar_matches_ellipsoidal_ground_distance(latitude):
    longitudes = revision.scale_bar_longitudes(112.4, latitude, 200.)
    geod = Geod(ellps='WGS84')
    assert longitudes[0] == 112.4
    for longitude, expected in zip(longitudes[1:], [100000., 200000.]):
        assert geod.inv(longitudes[0], latitude, longitude, latitude)[2] == pytest.approx(expected, abs=.01)


def test_document_rebuild_preserves_existing_citations_in_unchanged_prose():
    previous = {'blocks': [{'paragraphs': ['Soil inputs retain their source [CITE:soil].',
                                          'A changed interpretation [CITE:old].']}]}
    rebuilt = {'blocks': [{'paragraphs': ['Soil inputs retain their source.',
                                         'A revised interpretation.']}]}
    revision.restore_unchanged_citations(rebuilt, previous)
    assert rebuilt['blocks'][0]['paragraphs'] == ['Soil inputs retain their source [CITE:soil].',
                                               'A revised interpretation.']


def test_map_scale_backing_does_not_cover_mapped_rotation_cells():
    import revise_manuscript_figures as figures
    cells = pd.read_csv(figures.REG / 'data/all_source_cell_mapping.csv')
    fig, ax = figures.plt.subplots()
    try:
        figures.map_axes(ax)
        figures.map_decorations(ax)
        backing = ax.patches[0].get_bbox()
        x, y = figures.xy(cells.longitude, cells.latitude)
        # Bounds of each actual 0.1-degree plotted source-cell rectangle.
        edge_x, edge_y = figures.xy([.05], [.05])
        origin_x, origin_y = figures.xy([0.], [0.])
        half_x, half_y = edge_x[0] - origin_x[0], edge_y[0] - origin_y[0]
        intersects = ((x + half_x > backing.x0) & (x - half_x < backing.x1) &
                      (y + half_y > backing.y0) & (y - half_y < backing.y1))
        assert not intersects.any(), f'Scale backing intersects {int(intersects.sum())} mapped cells'
    finally:
        figures.plt.close(fig)
