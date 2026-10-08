"""Execute the frozen regional stages and preserve progress and execution metadata."""
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import subprocess
import sys
import time


def write(path, value):
    temporary = path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(value, indent=2)+'\n')
    temporary.replace(path)


def main():
    product = Path(__file__).resolve().parents[1]
    root = product/'regional'
    source = product/'analysis_source/regional_recalculation.py'
    state_path = root/'verification/pipeline_state.json'
    env = dict(os.environ, OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1', NUMEXPR_NUM_THREADS='1',
        PYTHONDONTWRITEBYTECODE='1', MPLBACKEND='Agg')
    completion=root/'verification/completion.json'
    if completion.exists() and json.loads(completion.read_text()).get('current_only_publication_exports'):
        # A rerun verifies the completed product without replacing its results,
        # timestamps, figures, progress receipt or exact forcing export.
        command=[sys.executable,str(source),'--stage','analyze','--run-root',str(root),'--workers','8']
        raise SystemExit(subprocess.run(command,env=env).returncode)
    started = time.monotonic()
    state = dict(status='running', pid=os.getpid(), workers=8,
        started_at_utc=datetime.now(timezone.utc).isoformat(), completed_stages=[], commands=[])
    for stage in ['responses', 'allocations', 'adaptive', 'analyze']:
        command = [sys.executable, str(source), '--stage', stage, '--run-root', str(root), '--workers', '8']
        state.update(current_stage=stage, elapsed_seconds=time.monotonic()-started)
        state['commands'].append(command);write(state_path, state)
        print(datetime.now(timezone.utc).isoformat(), 'starting', stage, flush=True)
        result = subprocess.run(command, env=env)
        if result.returncode:
            state.update(status='failed', returncode=result.returncode, elapsed_seconds=time.monotonic()-started)
            write(state_path, state)
            raise SystemExit(result.returncode)
        state['completed_stages'].append(stage)
    exporter = product/'analysis_source/regional_export.py'
    if exporter.exists():
        state.update(current_stage='exports', elapsed_seconds=time.monotonic()-started);write(state_path,state)
        command = [sys.executable, str(exporter)]
        state['commands'].append(command)
        result = subprocess.run(command, env=env)
        if result.returncode:
            state.update(status='failed', returncode=result.returncode);write(state_path,state)
            raise SystemExit(result.returncode)
        state['completed_stages'].append('exports')
    state.update(status='complete', current_stage=None, elapsed_seconds=time.monotonic()-started,
        completed_at_utc=datetime.now(timezone.utc).isoformat());write(state_path,state)
    print('Current regional product complete', flush=True)


if __name__ == '__main__':
    main()
