"""Reproduce study checks or full regional simulations using repository files."""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
CURRENT=ROOT/'model/current_results'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=['quick','full'],default='quick')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--workers',type=int,default=8)
    args=parser.parse_args()
    if sys.version_info[:2]!=(3,12):raise RuntimeError('Use Python 3.12 with the pinned requirements')
    if args.workers<1:raise ValueError('Workers must be positive')
    output=args.output or ROOT/'model'/('reproduction_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    output=output.resolve()
    if output==CURRENT or CURRENT in output.parents:raise ValueError('Completed current results are preserved')
    if output.exists() and any(output.iterdir()):raise FileExistsError('Choose a fresh output directory')
    (output/'verification').mkdir(parents=True,exist_ok=True)
    env=os.environ.copy();env['PYTHONDONTWRITEBYTECODE']='1';env['MPLBACKEND']='Agg'
    steps=[]
    def run(label,arguments):
        command=[sys.executable,'-B']+arguments
        log=output/'verification'/(label+'.log')
        print(label,flush=True)
        with log.open('w') as handle:
            result=subprocess.run(command,cwd=ROOT,env=env,stdout=handle,stderr=subprocess.STDOUT)
        record={'step':label,'command':arguments,'returncode':result.returncode,'log':str(log.relative_to(output))}
        steps.append(record)
        if result.returncode:
            print(log.read_text()[-6000:],file=sys.stderr)
            raise RuntimeError('Reproduction failed: '+label)
    run('process_tests',['-m','pytest','-c','open_crop_model/pytest.ini','open_crop_model/tests','-q'])
    run('calibration_source_and_exact_field_replays',[str(CURRENT/'analysis_source/verify_current_calibration.py')])
    run('regional_source_identity',[str(CURRENT/'analysis_source/replay_regional.py'),'--stage','check',
                                    '--calibration-root',str(CURRENT/'calibration')])
    run('results_conclusions_highlights_numbers',[str(CURRENT/'verification/2026-10-08_final_results_numbers_audit.py')])
    run('publication_and_source_bindings',[str(CURRENT/'analysis_source/verify_current_product.py')])
    regional=output/'regional'
    run('regional_continuous_benchmark',[str(CURRENT/'analysis_source/replay_regional.py'),'--stage','benchmark',
                                        '--output',str(regional),'--workers',str(args.workers)])
    if args.mode=='full':
        run('full_regional_recalculation',[str(CURRENT/'analysis_source/replay_regional.py'),'--stage','run',
                                          '--output',str(regional),'--workers',str(args.workers)])
    receipt={'all_steps_passed':all(s['returncode']==0 for s in steps),'mode':args.mode,
             'completed_utc':datetime.now(timezone.utc).isoformat(),'python':sys.version,
             'source_root':str(ROOT),'external_model_checkout_required':False,
             'remote_datasets_or_credentials_required':False,'steps':steps,
             'scientific_scope':'Reproduction of frozen calibration and conditional model scenarios; no new independent validation.',
             'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (output/'verification/reproduction.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print('Reproduction passed:',output,flush=True)


if __name__=='__main__':main()
