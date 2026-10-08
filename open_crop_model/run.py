"""Run a crop season with the selected calibrated parameters."""
from argparse import ArgumentParser
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import shutil
import pandas as pd
from calibrated import load_parameters, simulate_season


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path, help='JSON with inputs and optional presowing segment')
    parser.add_argument('--parameters', type=Path, help='Explicit shared or case-specific parameter JSON')
    parser.add_argument('--output', type=Path, help='New or empty output directory; defaults to a separate model/ run')
    args = parser.parse_args()
    payload = json.loads(args.input.read_text())
    inputs = payload['inputs']
    if args.parameters:
        parameters = json.loads(args.parameters.read_text())
    elif 'parameters' in payload:
        parameters = payload['parameters']
    else:
        parameters = load_parameters(inputs['crop'])
    output = args.output
    if output is None:
        stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d_%H%M%S_%f')
        output = Path(__file__).resolve().parent.parent / 'model' / f'{stamp}_{inputs["crop"]}_standalone'
    if output.exists() and any(output.iterdir()):
        parser.error('Output directory contains an existing run')
    state = None
    presowing = payload.get('presowing')
    if presowing:
        pre = simulate_season(presowing['inputs'], presowing.get('parameters', parameters))
        state = pre.final_state
    result = simulate_season(inputs, parameters, state)
    output.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.input, output / 'input_snapshot.json')
    (output / 'parameters_used.json').write_text(json.dumps(parameters, ensure_ascii=False, indent=2) + '\n')
    pd.DataFrame(result.daily).to_csv(output / 'daily_predictions.csv', index=False)
    pd.DataFrame(inputs['weather']).to_csv(output / 'weather.csv', index=False)
    if presowing:
        pd.DataFrame(pre.daily).to_csv(output / 'presowing_predictions.csv', index=False)
        pd.DataFrame(presowing['inputs']['weather']).to_csv(output / 'presowing_weather.csv', index=False)
    (output / 'summary.json').write_text(json.dumps(result.summary, ensure_ascii=False, indent=2) + '\n')
    manifest = {
        'calibration_version': 'management_refit',
        'input_sha256': hashlib.sha256((output / 'input_snapshot.json').read_bytes()).hexdigest(),
        'parameters_sha256': hashlib.sha256((output / 'parameters_used.json').read_bytes()).hexdigest(),
        'grain_basis': 'dry matter kg/ha',
    }
    (output / 'run_manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'Completed {inputs["crop"]}: {output}')


if __name__ == '__main__':
    main()
