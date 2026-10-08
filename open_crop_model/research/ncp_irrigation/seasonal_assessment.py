"""Documented harvest units and coverage-aware ET observation operators.

An observed-date sum remains an observed-date sum even with 90% coverage.
No missing ET is filled from the model being assessed.
"""
import math
import pandas as pd


def harvest_density_kg_ha(value, unit):
    if unit != 'g/m2':
        raise ValueError('Documented dry-weight density in g/m2 required')
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError('Nonnegative finite harvest density required')
    return value * 10.


def aggregate_et(observations, simulation, start_date, end_date, minimum_coverage=.9):
    start, end = pd.Timestamp(start_date), pd.Timestamp(end_date)
    if end < start or not 0 < minimum_coverage <= 1:
        raise ValueError('Invalid season interval or coverage threshold')
    observed, simulated = observations.copy(), simulation.copy()
    observed['date'] = pd.to_datetime(observed.date)
    simulated['date'] = pd.to_datetime(simulated.date)
    for frame, column in [(observed, 'value'), (simulated, 'et_mm')]:
        if frame.date.duplicated().any() or not frame.date.between(start, end).all():
            raise ValueError('ET dates must be unique and within the season')
        if not frame[column].map(lambda x: math.isfinite(float(x)) and float(x) >= 0).all():
            raise ValueError('Finite nonnegative ET required; apply documented QC upstream')
    expected = pd.date_range(start, end)
    if sorted(simulated.date.tolist()) != expected.tolist():
        raise ValueError('Simulation must cover every season day exactly once')
    paired = observed[['date','value']].merge(simulated[['date','et_mm']], on='date', validate='one_to_one')
    obs, sim = float(paired.value.sum()), float(paired.et_mm.sum())
    coverage = len(paired) / len(expected)
    return dict(n_expected_days=len(expected), n_observed_days=len(paired),
                n_missing_days=len(expected)-len(paired), coverage=coverage,
                season_coverage_eligible=coverage >= minimum_coverage,
                exact_complete_season=coverage == 1.,
                observed_matched_sum_mm=obs, simulated_matched_sum_mm=sim,
                simulated_full_sum_mm=float(simulated.et_mm.sum()),
                matched_error_mm=sim-obs,
                relative_matched_error=(sim-obs)/obs if obs > 0 else None,
                observation_basis='sum_on_observed_dates_without_gap_filling')
