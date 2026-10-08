"""Watch the existing fits and preserve sequential freeze/evaluation receipts."""
from pathlib import Path
from datetime import datetime,timezone
import json,os,subprocess,sys,time

ROOT=Path(__file__).resolve().parents[1]
PIDS={'wheat':37329,'maize':37330}
STATE=ROOT/'verification/documented_water_pipeline.json'


def state(stage,**extra):
    STATE.write_text(json.dumps(dict(stage=stage,pipeline_pid=os.getpid(),fit_pids=PIDS,
        updated_at_utc=datetime.now(timezone.utc).isoformat(),scientific_goal_achieved=False,
        manuscript_promoted=False,regional_promoted=False,**extra),indent=2)+'\n')


def main():
    if STATE.exists():raise RuntimeError('Existing pipeline preserved; inspect its process')
    while True:
        complete=[]
        for crop,pid in PIDS.items():
            path=ROOT/'parameters/candidates'/f'{crop}_corrected_matric.json'
            if path.exists():
                try:card=json.loads(path.read_text())
                except json.JSONDecodeError:continue
                if not card['success']:raise RuntimeError(crop+' did not converge; no testing')
                assert not card['testing_used'];complete.append(crop)
            else:
                live=subprocess.run(['ps','-p',str(pid),'-o','command='],text=True,capture_output=True)
                expected='documented_management_water_calibration/analysis_source/field_water_fit.py --crop '+crop
                if live.returncode or expected not in live.stdout:
                    raise RuntimeError(crop+' process is terminal or missing without a candidate')
        state('calibration',completed_crops=complete,testing_started=False)
        if len(complete)==2:break
        time.sleep(10)
    for label,script,receipt in [('freeze','freeze_qualified.py','parameters/frozen_model.json'),
        ('evaluate','evaluate_qualified.py','verification/run_receipt.json'),
        ('package','package_qualified.py','verification/packaging_receipt.json'),
        ('verify','verify_qualified.py','verification/independent_checks.json')]:
        if (ROOT/receipt).exists():raise RuntimeError('Existing stage receipt preserved: '+receipt)
        log=ROOT/'verification'/f'postfit_{label}.log';state(label,log=str(log.relative_to(ROOT)))
        print('Starting',label,flush=True)
        with log.open('x') as handle:
            result=subprocess.run([sys.executable,'-u','-B',str(ROOT/'analysis_source'/script)],stdout=handle,stderr=subprocess.STDOUT)
        if result.returncode:raise RuntimeError(label+' failed; inspect '+str(log))
        if label=='verify':
            # Verifier stdout is the durable scientific/numerical receipt.
            value=json.loads(log.read_text());(ROOT/receipt).write_text(json.dumps(value,indent=2)+'\n')
        assert (ROOT/receipt).exists()
    state('awaiting_visual_review',testing_completed=True,independent_numerical_checks_passed=True)
    print('Fits, frozen retrospective evaluation and numerical checks complete; figures await visual review.',flush=True)


if __name__=='__main__':
    try:main()
    except Exception as error:
        state('failed',error=str(error));raise
