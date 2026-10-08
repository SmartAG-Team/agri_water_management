"""Wait for these fit processes, then freeze and evaluate; never restart fits."""
from pathlib import Path
import argparse
import json
import os
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--wheat-pid', type=int, required=True)
    parser.add_argument('--maize-pid', type=int, required=True)
    args = parser.parse_args()
    pids = {'wheat': args.wheat_pid, 'maize': args.maize_pid}
    receipt = ROOT / 'verification/qualified_pipeline.json'
    while True:
        complete = []
        for crop, pid in pids.items():
            path = ROOT / 'parameters/candidates' / (crop + '_corrected_matric.json')
            if path.exists():
                card = json.loads(path.read_text())
                if not card['success']:
                    raise RuntimeError(crop + ' fit did not converge; no testing')
                complete.append(crop)
            elif not alive(pid):
                raise RuntimeError(crop + ' process stopped without a completed candidate; inspect its existing log')
        receipt.write_text(json.dumps(dict(stage='calibration', fit_pids=pids, completed_crops=complete,
            calibration_qualification_preflight_passed=True, testing_started=False,
            native_model_changed=False, scientific_goal_achieved=False), indent=2) + '\n')
        if len(complete) == 2:
            break
        time.sleep(10)
    for name in ['freeze_qualified.py', 'evaluate_qualified.py']:
        subprocess.run([sys.executable, str(ROOT / 'analysis_source' / name)], check=True)
    receipt.write_text(json.dumps(dict(stage='full_evaluation_completed', fit_pids=pids,
        frozen_before_testing=True, testing_retrospective=True,
        independent_verification_pending=True, figures_and_workbook_pending=True,
        manuscript_promoted=False, regional_promoted=False, scientific_goal_achieved=False), indent=2) + '\n')


if __name__ == '__main__':
    main()
