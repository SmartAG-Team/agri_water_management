# Regional archive verification and fresh replay

Run these commands from the project workspace using its existing `.venv`.
The controlling archive is `model/current_results/regional`. Historical model
directories are unnecessary. Original absolute paths in provenance records are
metadata; verification checks the frozen local snapshots.

## Required current-result checks

```sh
.venv/bin/python model/current_results/analysis_source/replay_regional.py \
  --stage check \
  --source-root model/current_results/regional \
  --calibration-root model/current_results/calibration
```

This verifies the sealed regional file manifest and all 143 native Python files,
then requires exact Python-content equality with the selected calibration engine.
The native file-identity SHA-256 is
`4caae16b13539138b278b0b77a3611c02a85ab95f4af41d7fe41b3142f5eed6e`.
The optional `--calibration-root` comparison is unnecessary for a standalone
regional archive; its frozen native identity remains verifiable locally.

## Prepare and benchmark a fresh run

Choose an empty output directory outside the sealed regional archive.

```sh
.venv/bin/python model/current_results/analysis_source/replay_regional.py \
  --stage prepare --output /tmp/agri_regional_replay_smoke

.venv/bin/python /tmp/agri_regional_replay_smoke/analysis_source/replay_regional.py \
  --stage benchmark --output /tmp/agri_regional_replay_smoke
```

Preparation copies frozen weather, hydraulic profiles, footprint and class
inputs, effective shared crop cards, selected native source, and necessary
source provenance. Existing regional predictions, allocations, policy decisions,
historical comparison predictions, and calibration reference-prediction tables
are excluded. The four-segment benchmark simulates 644 consecutive days at
representative 0 and writes `verification/replay_benchmark.json`; it performs no
regional rerun. Prepared inputs and executable sources are self-contained.

## Execute the full fresh replay when required

```sh
.venv/bin/python model/current_results/analysis_source/replay_regional.py \
  --stage run --output model/2026-10-07_regional_replay --workers 8
```

The stages are `responses`, `allocations`, `adaptive`, `analyze`, and `export`.
Each stage can also be invoked separately with the same `--output` and
`--workers` arguments. Preparation runs automatically for `run` and `benchmark`.
The frontend keeps completed replay products immutable; later invocations verify
their manifests without replacing results.

The replay preserves the 1996 initial fallow, continuous soil-water carry-over,
five fixed quotas, eight explicit source-cell checks, six adaptive/rainfall
histories, and two conventional replay histories. Allocation selection uses
1997–2013, class-rule selection uses 2003–2013, and evaluation uses 2014–2025.
The final simulation ends on 2025-10-05, matching the completed source run.
Native and effective parameters remain fixed; allocations and class decisions
are recomputed from the newly simulated training responses. Current-only tables
and PNG/PDF figures are exported with raw-input explorations and quality checks.

## Legacy preparation caveat

`regional_recalculation.py --stage prepare` is the retained historical
preparation entry point and contains its original source-directory defaults.
It is not the fresh-run interface after historical-directory relocation.
`replay_regional.py` derives a purely local execution kernel from the frozen
scientific functions and supplies current-only preparation and analysis stages.
The completed regional directory and its sealed scientific source remain intact.

Regional outputs are conditional scenarios. Numerical conservation and source-cell
aggregation checks do not establish independent field validation or causal
groundwater effects. GRACE availability is retrospective; operational release
before the decision date remains unverified.
