"""Run frozen retrospective testing after the two existing fits converge."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "verification/postfit_pipeline.json"
FIT_PIDS = {"wheat": 56382, "maize": 56383}


def write_state(stage, **extra):
    value = dict(stage=stage, pipeline_pid=os.getpid(),
                 scientific_goal_achieved=False, manuscript_promoted=False,
                 regional_promoted=False, updated_at_utc=datetime.now(timezone.utc).isoformat(), **extra)
    STATE.write_text(json.dumps(value, indent=2) + "\n")


def check_inputs():
    rows = json.loads((ROOT / "verification/input_manifest.json").read_text())
    assert len({r["snapshot"] for r in rows}) == len(rows)
    for row in rows:
        assert hashlib.sha256((ROOT / row["snapshot"]).read_bytes()).hexdigest() == row["sha256"], row["snapshot"]


def fits_ready():
    ready = []
    for crop, pid in FIT_PIDS.items():
        path = ROOT / "parameters/candidates" / (crop + "_corrected_matric.json")
        if path.exists():
            try:
                card = json.loads(path.read_text())
            except json.JSONDecodeError:
                return False  # A candidate file can be observed during its write.
            if not card["success"]:
                raise RuntimeError(crop + " calibration terminated without convergence.")
            assert card["testing_used"] is False
            ready.append(crop)
        else:
            result = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                                    text=True, capture_output=True, check=False)
            expected = "fit_soil_initialization.py --crop " + crop
            if result.returncode != 0 or expected not in result.stdout:
                raise RuntimeError(crop + " fit is terminal or missing without a successful candidate.")
    return len(ready) == 2


def main():
    if STATE.exists():
        raise RuntimeError("Existing pipeline state preserved; inspect its process and logs.")
    if (ROOT / "verification/artifact_manifest.json").exists():
        raise RuntimeError("Sealed run preserved.")
    check_inputs()
    write_state("waiting_for_existing_fits", fit_pids=FIT_PIDS)
    print("Watching existing wheat and maize fits; no fits restarted.", flush=True)
    while not fits_ready():
        time.sleep(10)
    check_inputs()
    stages = [
        ("freeze", "freeze_selection.py", "parameters/frozen_model.json"),
        ("evaluate", "evaluate_frozen_model.py", "verification/run_receipt.json"),
        ("package", "package_crop_run.py", "verification/packaging_receipt.json"),
        ("verify", "verify_run.py", "verification/independent_checks.json"),
    ]
    for name, script, receipt in stages:
        if (ROOT / receipt).exists():
            raise RuntimeError("Existing stage receipt preserved: " + receipt)
        logfile = ROOT / "verification" / ("postfit_" + name + ".log")
        write_state(name, log=logfile.relative_to(ROOT).as_posix())
        print("Starting", name, flush=True)
        with logfile.open("x") as output:
            completed = subprocess.run([sys.executable, "-u", str(ROOT / "analysis_source" / script)],
                                       cwd=ROOT.parents[1], stdout=output, stderr=subprocess.STDOUT, check=False)
        if completed.returncode != 0:
            write_state("failed", failed_stage=name, exit_code=completed.returncode,
                        log=logfile.relative_to(ROOT).as_posix())
            raise RuntimeError(name + " failed; inspect the preserved stage log.")
        assert (ROOT / receipt).exists(), receipt
    write_state("awaiting_visual_review", fits_converged=True, testing_completed=True,
                numerical_verification_completed=True, figures_visually_reviewed=False)
    print("Testing and numerical checks complete; figures require visual review before sealing.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        if not STATE.exists() or json.loads(STATE.read_text()).get("pipeline_pid") == os.getpid():
            write_state("failed", error=str(error))
        raise
