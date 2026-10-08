"""Calibration-only objective with one bounded root compensation parameter."""
from copy import deepcopy

import numpy as np

import baseline_objective as baseline
from calibration_core import window_predictions

ROOT = baseline.ROOT
data = baseline.data
run = baseline.run


def specification(crop):
    names, low, high, center, prior = baseline.specification(crop)
    return (names + ['root_compensation_fraction'], np.r_[low, 0.],
            np.r_[high, 1.], np.r_[center, .5], np.r_[prior, .3])


def adjusted(payload, vector, crop):
    vector = np.asarray(vector, dtype=float)
    if vector.shape != (11,) or not np.isfinite(vector).all():
        raise ValueError('Eleven finite crop-fit parameters required')
    if not 0. <= vector[-1] <= 1.:
        raise ValueError('Root compensation fraction must be in [0,1]')
    p = baseline.adjusted(payload, vector[:-1], crop)
    if p['inputs']['crop'] != crop:
        return p
    p['parameters']['hydrology'].update(
        plant_water_stress_method='compensated_layer_depletion',
        root_compensation_fraction=float(vector[-1]))
    if p.get('presowing'):
        p['presowing']['parameters'] = deepcopy(p['parameters'])
    return p


def residuals(vector, crop, control):
    obs, field, payloads, ws, ss, wf, fs, periods, wp, ps, contrasts, _ = data(crop, control)
    predicted = np.full(len(obs), np.nan)
    fp = np.full(len(field), np.nan)
    et = {}
    for case, payload in payloads.items():
        result = run(adjusted(payload, vector, crop))
        if case.startswith('yang2024'):
            g = field[field.case_id.eq(case)]
            values = {'seasonal_et': result.summary['et_mm'],
                      'yield': result.summary['yield_kg_ha'],
                      'harvest_biomass': result.summary['biomass_kg_ha']}
            fp[g.index] = g.variable.map(values).to_numpy()
            et[case] = values['seasonal_et']
        else:
            g = obs[obs.case_id.eq(case)]
            predicted[g.index] = window_predictions(result.daily, g)
    assert np.isfinite(predicted).all() and np.isfinite(fp).all()
    totals = np.array([predicted[obs[obs.case_id.eq(r.case_id) & obs.variable.eq('et')].index].sum()
                       for r in periods.itertuples()])
    response = np.array([(et[b] - et[a] - observed) / 50.
                         for a, b, observed in contrasts])
    *_, center, prior = specification(crop)
    return np.r_[ws * (predicted - obs.value.to_numpy()) / ss,
                 wf * (fp - field.value.to_numpy()) / fs,
                 wp * (totals - periods.value.to_numpy()) / ps,
                 np.sqrt(.1 / len(response)) * response,
                 np.sqrt(.01 / len(vector)) * (vector - center) / prior]
