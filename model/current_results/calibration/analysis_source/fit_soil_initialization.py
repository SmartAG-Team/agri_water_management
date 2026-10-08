"""Fit bounded compensation using unchanged targets and case-specific ceilings."""
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from functools import partial
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
from bounded_calibration import fit_bounded, from_unit
from fit_objective import data, residuals, specification
from calibration_audit import independent_losses


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n')


def fit(crop):
    control = 'corrected_matric'
    path = ROOT / 'parameters/candidates' / (crop + '_' + control + '.json')
    if path.exists():
        raise RuntimeError('Existing candidate is preserved')
    manifest = json.loads((ROOT / 'verification/input_manifest.json').read_text())
    for row in manifest:
        assert hashlib.sha256((ROOT / row['snapshot']).read_bytes()).hexdigest() == row['sha256'], row['snapshot']
    previous = json.loads((ROOT / 'source_snapshots/starting_candidates' / path.name).read_text())
    names, low, high, _, _ = specification(crop)
    initial = np.array(previous['vector'])
    assert names == previous['vector_order']
    assert np.all(initial >= low) and np.all(initial <= high)
    obs, field, *_ = data(crop, control)
    assert field.case_id.nunique() == 12 and set(field.harvest_year) == {2016, 2017, 2018}
    assert 'Wuqiao-2019' not in set(field.source_group_id)
    obs.to_csv(ROOT / 'data' / ('used_calibration_station_' + crop + '.csv'), index=False)
    field.to_csv(ROOT / 'data' / ('used_calibration_field_' + crop + '.csv'), index=False)
    reference_station = pd.read_csv(ROOT / 'data/reference_station_comparisons.csv', low_memory=False)
    reference_field = pd.read_csv(ROOT / 'data/reference_field_comparisons.csv')
    independent = independent_losses(reference_station, reference_field)
    reference = independent[independent.crop.eq(crop)].iloc[0].to_dict()
    fun = partial(residuals, crop=crop, control=control)
    start = fun(initial)
    reference_loss = float(start[:-11] @ start[:-11])
    assert abs(reference_loss - reference['calibration_data_loss']) < 1e-10
    start_data_loss = reference_loss
    write(ROOT / 'verification' / ('starting_objective_' + crop + '.json'), dict(
        R59_reference_replayed_under_new_initialization=True,
        reference_calibration_data_loss=reference_loss,
        independent_reference_blocks=reference,
        starting_candidate_calibration_data_loss=start_data_loss,
        initial_compensation_fraction=float(initial[-1]), compensation_prior_center=.5,
        compensation_prior_spread=.3, prior_weight=.01,
        field_calibration_years=[2016, 2017, 2018], field_testing_years=[2019],
        testing_used=False, original_observation_weights_preserved=True))
    print(crop, 'PID', os.getpid(), 'reference loss', round(reference_loss, 6),
          'updated-initialization starting loss', round(start_data_loss, 6), flush=True)
    history = []
    started = time.monotonic()

    def callback(intermediate_result):
        physical = from_unit(intermediate_result.x, low, high)
        residual = np.asarray(intermediate_result.fun)
        row = dict(crop=crop, iteration=len(history) + 1,
                   objective_loss=float(residual @ residual),
                   calibration_data_loss=float(residual[:-11] @ residual[:-11]),
                   elapsed_s=time.monotonic() - started, vector=json.dumps(physical.tolist()))
        history.append(row)
        pd.DataFrame(history).to_csv(path.with_name(path.stem + '_history.csv'), index=False)
        print(crop, 'iteration', len(history), 'calibration loss', round(row['calibration_data_loss'], 6),
              'compensation', round(physical[-1], 4), 'seconds', round(row['elapsed_s']), flush=True)

    path.parent.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(max_workers=4) as pool:
        result = fit_bounded(fun, initial, low, high, workers=pool.map, difference_step=.002,
                             ftol=3e-5, xtol=3e-5, gtol=3e-5, max_nfev=60,
                             callback=callback, x_scale='jac')
    physical = result.physical_x
    final = fun(physical)
    record = dict(crop=crop, control=control, vector=physical.tolist(), vector_order=names,
                  success=bool(result.success), message=str(result.message), status=int(result.status),
                  nfev=result.nfev, njev=result.njev, optimality=float(result.optimality),
                  seconds=time.monotonic() - started, objective_loss=float(final @ final),
                  calibration_data_loss=float(final[:-11] @ final[:-11]),
                  reference_calibration_data_loss=reference_loss,
                  starting_calibration_data_loss=start_data_loss,
                  lower_bounds=low.tolist(), upper_bounds=high.tolist(),
                  station_cases=obs.case_id.nunique(), station_records=len(obs),
                  eligible_sites=sorted(obs.site.unique()), field_cases=12,
                  field_years=[2016, 2017, 2018], field_testing_years=[2019],
                  calibration_groups=sorted(set(obs.source_group_id) | set(field.source_group_id)),
                  testing_used=False, testing_previously_inspected=True,
                  field_biomass_fitted=True, field_biomass_basis_confirmed=True,
                  original_ET_yield_station_and_response_weights_preserved=True,
                  field_biomass_weight=.15, free_parameters=11, extra_free_parameters=0,
                  native_model_changed=False, calibration_partition_changed=False, soil_initialization_rule_changed=True,
                  individual_coefficient_priors_unchanged=True,
                  compensation_prior_center=.5, compensation_prior_spread=.3,
                  compensation_method='compensated_layer_depletion',
                  empirical_assimilation_water_response_retained=True,
                  fitting_coordinates='unit box', absolute_unit_difference_step=.002,
                  optimizer_max_nfev=60, frozen_at_utc=datetime.now(timezone.utc).isoformat(),
                  manuscript_promoted=False, regional_promoted=False)
    write(path, record)
    if not result.success:
        raise RuntimeError('Calibration did not converge; candidate is not accepted for testing')
    print('FROZEN CANDIDATE', crop, record['calibration_data_loss'], flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--crop', choices=['wheat', 'maize'], required=True)
    fit(parser.parse_args().crop)
