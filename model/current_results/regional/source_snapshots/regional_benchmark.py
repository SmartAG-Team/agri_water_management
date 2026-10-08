"""Benchmark the exact frozen regional process without changing production inputs."""
from pathlib import Path
import json
import sys
import time
import numpy as np
import pandas as pd
import regional_recalculation as driver


def main():
    root = Path(__file__).resolve().parents[1]/'regional'
    driver.verify_run(root)
    driver.initialize_worker(root)
    point = pd.read_csv(root/'data/representative_cells.csv').iloc[0].to_dict()
    state, results = None, []
    started = time.monotonic()
    for crop, start, end, year in driver.calendar()[:4]:
        forcing = driver._WORKER['weather'][0].loc[start:end].reset_index().to_dict('records')
        layers = driver._WORKER['soils'][0]
        inputs = dict(crop=crop, maturity_group='middle', latitude_deg=point['latitude'], elevation_m=point['elevation_m'],
            start_date=start, end_date=end, weather=forcing, soil_layers=layers, technology=driver.TECH,
            et0_method='provided', irrigation_events=[], management_class='fertilized')
        if state is None:
            inputs['initial_theta'] = [l['wilting_point']+.8*(l['field_capacity']-l['wilting_point']) for l in layers]
        if crop != 'fallow':
            inputs['cutting_date'] = end
            schedule = [(start, 60), (f'{year}-03-25', 90), (f'{year}-04-15', 90), (f'{year}-05-10', 60)] if crop == 'wheat' else [(start, 80)]
            inputs['irrigation_events'] = [dict(event_id=f'quota_{i}', date=day, amount_mm=amount, measurement_location='field')
                for i, (day, amount) in enumerate(schedule)]
        previous = state.storage_mm() if state is not None else None
        tick = time.monotonic()
        result = driver._WORKER['simulate'](inputs, driver.segment_parameters(crop, driver._WORKER['cards'], driver._WORKER['hydro']), state)
        state = result.final_state
        results.append(dict(crop=crop, n_days=len(forcing), seconds=time.monotonic()-tick,
            initial_storage_mm=result.summary['initial_storage_mm'], final_storage_mm=result.summary['final_storage_mm'],
            storage_continuity_error_mm=result.summary['initial_storage_mm']-previous if previous is not None else 0.,
            yield_kg_ha=result.summary['yield_kg_ha'], et_mm=result.summary['et_mm'],
            maximum_water_residual_mm=max(abs(d['balance_residual_mm']) for d in result.daily),
            maximum_carbon_residual_kg_ha=max(abs(d['crop_carbon_residual_kg_ha']) for d in result.daily)))
    daily_seconds = sum(r['seconds'] for r in results)/sum(r['n_days'] for r in results)
    production_histories = 32*5+8*5+32*6+2
    history_days = sum((pd.Timestamp(end)-pd.Timestamp(start)).days+1 for _,start,end,_ in driver.calendar())
    receipt = dict(frozen_protocol_sha256=driver.sha(root/'parameters/frozen_protocol.json'),
        workers=8, sample_results=results, measured_seconds_per_simulated_day=daily_seconds,
        total_history_count=production_histories, total_simulated_days=production_histories*history_days,
        estimated_wall_seconds_at_8_workers=daily_seconds*production_histories*history_days/8,
        measured_benchmark_wall_seconds=time.monotonic()-started)
    driver.write_json(root/'verification/runtime_benchmark.json', receipt)
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
