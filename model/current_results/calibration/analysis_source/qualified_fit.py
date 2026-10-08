"""Fit documented seasonal ET; incomplete-log daily ET remains conditional."""
from pathlib import Path
from functools import lru_cache
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
import sys
import time

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'analysis_source'))
from fit_objective import specification, adjusted, data, run
from calibration_core import window_predictions
from bounded_calibration import fit_bounded, from_unit


@lru_cache(maxsize=2)
def qualified_data(crop):
    obs, field, payloads, ws, ss, wf, fs, periods, wp, ps, contrasts, scales = data(crop, 'corrected_matric')
    quality = pd.read_csv(ROOT / 'data/daily_ET_fitting_eligibility.csv')
    conditional = quality[quality.crop.eq(crop)]
    assert not conditional.eligible_for_water_parameter_fitting.any()
    assert set(obs[obs.variable.eq('et')].case_id) == set(conditional.case_id)
    keep = obs.variable.ne('et').to_numpy()
    trusted = obs[keep].copy().reset_index(drop=True)
    ws, ss = ws[keep], ss[keep]
    # Keep the original growth/yield contribution instead of renormalizing it.
    assert np.isclose(np.sum(ws**2), .4)
    wf = wf.copy()
    seasonal = field.variable.eq('seasonal_et').to_numpy()
    assert np.isclose(np.sum(wf[seasonal]**2), .175)
    # Transfer the excluded station ET (.10) and its sum constraint (.05)
    # to the documented field seasonal-ET contribution, .175 -> .325.
    wf[seasonal] *= np.sqrt(.325/.175)
    selected = set(trusted.case_id) | set(field.case_id)
    payloads = {case: payload for case, payload in payloads.items() if case in selected}
    assert field.case_id.nunique() == 12 and set(field.harvest_year) == {2016., 2017., 2018.}
    assert all('2019' not in case for case in field.case_id)
    assert trusted.groupby('source_group_id').split.nunique().eq(1).all()
    expected = {'Fengqiu', 'Luancheng', 'Shangqiu', 'Yucheng'} | ({'Gucheng'} if crop == 'maize' else set())
    assert set(trusted.site) == expected
    return trusted, field, payloads, ws, ss, wf, fs, contrasts


def case_prediction(item):
    case, payload, vector, crop, observations = item
    result = run(adjusted(payload, vector, crop))
    if case.startswith('yang2024'):
        return case, {'seasonal_et': result.summary['et_mm'], 'yield': result.summary['yield_kg_ha'],
                      'harvest_biomass': result.summary['biomass_kg_ha']}
    return case, window_predictions(result.daily, observations)


def residuals(vector, crop, pool):
    obs, field, payloads, ws, ss, wf, fs, contrasts = qualified_data(crop)
    tasks = [(case, payload, np.asarray(vector), crop, obs[obs.case_id.eq(case)])
             for case, payload in payloads.items()]
    predictions = np.full(len(obs), np.nan)
    field_predictions = np.full(len(field), np.nan)
    ets = {}
    for case, values in pool.map(case_prediction, tasks):
        if case.startswith('yang2024'):
            group = field[field.case_id.eq(case)]
            field_predictions[group.index] = group.variable.map(values).to_numpy()
            ets[case] = values['seasonal_et']
        else:
            predictions[obs[obs.case_id.eq(case)].index] = values
    assert np.isfinite(predictions).all() and np.isfinite(field_predictions).all()
    response = np.array([(ets[b] - ets[a] - observed)/50. for a, b, observed in contrasts])
    *_, center, prior = specification(crop)
    return np.r_[ws*(predictions-obs.value.to_numpy())/ss,
                 wf*(field_predictions-field.value.to_numpy())/fs,
                 np.sqrt(.1/len(response))*response,
                 np.sqrt(.01/len(vector))*(np.asarray(vector)-center)/prior]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def fit(crop):
    target = ROOT / 'parameters/candidates' / (crop + '_corrected_matric.json')
    if target.exists():
        raise RuntimeError('Completed candidate preserved')
    for row in json.loads((ROOT / 'verification/input_manifest.json').read_text()):
        assert hashlib.sha256((ROOT / row['snapshot']).read_bytes()).hexdigest() == row['sha256']
    previous = json.loads((ROOT / 'source_snapshots/starting_parameters' / (crop + '.json')).read_text())
    names, low, high, _, _ = specification(crop)
    initial = np.array(previous['vector'])
    assert previous['vector_order'] == names and np.all(initial >= low) and np.all(initial <= high)
    obs, field, payloads, ws, ss, wf, fs, contrasts = qualified_data(crop)
    obs.to_csv(ROOT / 'data' / ('used_qualified_station_' + crop + '.csv'), index=False)
    field.to_csv(ROOT / 'data' / ('used_qualified_field_' + crop + '.csv'), index=False)
    started = time.monotonic()
    history = []
    with ProcessPoolExecutor(max_workers=4) as pool:
        function = lambda vector: residuals(vector, crop, pool)
        reference = function(initial)
        reference_loss = float(reference[:-11] @ reference[:-11])
        write(ROOT / 'verification' / ('starting_qualified_objective_' + crop + '.json'), dict(
            reference_calibration_data_loss=reference_loss, native_model_changed=False,
            daily_ET_used_for_fitting=False, station_growth_yield_weight=.4, field_seasonal_ET_weight=.325,
            field_grain_yield_weight=.175, field_biomass_weight=.15, field_ET_response_weight=.1,
            coefficient_prior_weight=.01, field_calibration_years=[2016, 2017, 2018], testing_used=False,
            station_records=len(obs), station_cases=obs.case_id.nunique(), eligible_sites=sorted(obs.site.unique())))
        print(crop, 'PID', os.getpid(), 'qualified reference loss', round(reference_loss, 6), flush=True)

        def callback(intermediate_result):
            physical = from_unit(intermediate_result.x, low, high)
            residual = np.asarray(intermediate_result.fun)
            history.append(dict(crop=crop, iteration=len(history)+1,
                calibration_data_loss=float(residual[:-11] @ residual[:-11]),
                objective_loss=float(residual @ residual), elapsed_s=time.monotonic()-started,
                vector=json.dumps(physical.tolist())))
            pd.DataFrame(history).to_csv(target.with_name(target.stem + '_history.csv'), index=False)
            print(crop, 'iteration', len(history), 'qualified loss', round(history[-1]['calibration_data_loss'], 6),
                  'seconds', round(history[-1]['elapsed_s']), flush=True)

        result = fit_bounded(function, initial, low, high, workers=None, difference_step=.002,
            ftol=8e-5, xtol=8e-5, gtol=8e-5, max_nfev=40, callback=callback, x_scale='jac')
        final = function(result.physical_x)
    record = dict(crop=crop, control='corrected_matric', vector=result.physical_x.tolist(), vector_order=names,
        success=bool(result.success), message=str(result.message), status=int(result.status),
        nfev=result.nfev, njev=result.njev, optimality=float(result.optimality),
        seconds=time.monotonic()-started, objective_loss=float(final@final),
        calibration_data_loss=float(final[:-11]@final[:-11]), reference_calibration_data_loss=reference_loss,
        lower_bounds=low.tolist(), upper_bounds=high.tolist(), station_cases=obs.case_id.nunique(),
        station_records=len(obs), eligible_sites=sorted(obs.site.unique()), field_cases=12,
        field_years=[2016, 2017, 2018], field_testing_years=[2019], testing_used=False,
        testing_previously_inspected=True, daily_ET_used_for_fitting=False,
        daily_ET_retained_as_conditional_comparisons=True, native_model_changed=False,
        soil_initialization_rule_changed=False, phenology_changed=False, coefficient_priors_unchanged=True,
        root_mass_shape_imposed=False, station_growth_yield_weight=.4, field_seasonal_ET_weight=.325,
        field_grain_yield_weight=.175, field_biomass_weight=.15, field_ET_response_weight=.1,
        free_parameters=11, extra_free_parameters=0, frozen_at_utc=datetime.now(timezone.utc).isoformat(),
        manuscript_promoted=False, regional_promoted=False)
    write(target, record)
    if not result.success:
        raise RuntimeError('Calibration did not converge; testing is not authorized by the pipeline')
    print('FROZEN QUALIFIED CANDIDATE', crop, record['calibration_data_loss'], flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--crop', choices=['wheat', 'maize'], required=True)
    fit(parser.parse_args().crop)
