# Open Crop Model

Standalone calibrated wheat–maize crop and soil-water model. The daily engine represents phenological development, canopy growth, aboveground biomass, grain formation, evapotranspiration and layered soil-water balance. Irrigation methods include flood, surface drip, micro-sprinkler and sprinkler.

The selected parameter version is `management_refit`. `parameters/wheat.json` and `parameters/maize.json` contain effective shared crop and hydrology coefficients. `parameters/cases/` preserves the 164 resolved case-specific parameter sets, including cultivar and site adjustments; `parameters/index.json` lists them. The scientific engine is frozen with this calibration.

## Run a season

From the containing repository:

```sh
python -m pip install -r open_crop_model/requirements.txt
python open_crop_model/run.py open_crop_model/examples/wheat_W0.json
python open_crop_model/run.py open_crop_model/examples/maize_W0.json
```

Each command creates a separate directory under `model/`, containing daily predictions, seasonal summary, exact input snapshot, weather, parameters and SHA-256 records. An explicit `--output model/new_run_name` selects a new or empty directory. Existing runs are preserved.

An explicit parameter file can select a case-specific configuration:

```sh
python open_crop_model/run.py INPUT.json --parameters open_crop_model/parameters/cases/CASE_ID.json --output model/new_case_run
```

Input JSON contains `inputs` and optionally `parameters` and `presowing`. Explicit `--parameters` takes priority; otherwise embedded parameters take priority over the shared crop card. A presowing segment carries its simulated soil-water state into the crop season.

Presowing and crop segments use the selected case parameters, matching the archived calibration evaluation. Six station inputs also retain earlier `initial_state` snapshots as provenance; execution recomputes their presowing states from the included forcing rather than importing those earlier states.

## Python interface

With this directory on the Python import path:

```python
from calibrated import load_parameters, simulate_season

parameters = load_parameters("wheat")
result = simulate_season(inputs, parameters)
daily_predictions = result.daily
seasonal_summary = result.summary
soil_water_state = result.final_state
```

Inputs require complete daily weather, explicit soil layers and initial water contents, crop dates, and dated irrigation or a specified irrigation policy. Site-specific soil, cultivar and management information remains part of the input configuration.

## Units and calibration scope

Water fluxes are mm, soil-water content is volumetric, LAI is m² m⁻², and biomass and grain outputs are kg dry matter ha⁻¹. Wuqiao grain comparisons use a 13% moisture basis: simulated dry grain is divided by 0.87 before comparison with those observations.

Wuqiao 2016–2018 treatments supplied the current water calibration. The repeatedly inspected 2019 treatments provide retrospective evaluation. Station comparisons remain conditional where irrigation completeness is unconfirmed. Field accuracy criteria are not all met; this package preserves the selected calibration and does not establish independent field validation.

## Source integrity and checks

`model_manifest.json` records parameter scope and scientific source SHA-256 values. The package contains regular local files and has no version-control or worktree dependency.

```sh
python -m pip install -r open_crop_model/requirements-dev.txt
python -m pytest -c open_crop_model/pytest.ini open_crop_model/tests -q
```
