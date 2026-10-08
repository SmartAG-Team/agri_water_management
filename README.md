# North China Plain irrigation study

[![Reproduce research checks](https://github.com/SmartAG-Team/agri_water_management/actions/workflows/reproduce.yml/badge.svg)](https://github.com/SmartAG-Team/agri_water_management/actions/workflows/reproduce.yml)

Data, executable model source and reproducible analysis for *Irrigation strategies under contrasting water-storage and rainfall conditions in the North China Plain*.

The [manuscript](model/current_results/publication/documents/manuscript.docx), [manuscript PDF](model/current_results/publication/documents/manuscript.pdf) and [supplementary material](model/current_results/publication/documents/supplementary_material.docx) accompany a self-contained implementation of **Open Crop Model**. The repository includes the fitted parameters, exact observation and weather subsets, regional inputs, predictions, figure data and publication sources used in the study.

## Research idea

Winter wheat and summer maize occupy different rainfall seasons in the North China Plain, but their water balances are connected through soil-water storage. An irrigation reduction can affect the current crop, the water left for the next crop, and the distribution of production across the region. Consequently, regional grain retention, water delivered to fields and water consumed by evapotranspiration (ET) need to be assessed together.

The paper addresses three connected questions:

1. **Where should a limited irrigation supply be allocated?** Uniform reductions give every location the same fraction of conventional irrigation. Spatial targeting assigns irrigation according to simulated marginal grain returns, while keeping exactly the same regional delivery budget.
2. **Who gains and who loses?** A positive regional production difference can coexist with persistent local losses. The analysis maps the magnitude, area and annual persistence of these contrasts.
3. **Does regional storage information improve annual decisions beyond rainfall?** Strategies combining GRACE terrestrial-water-storage anomalies with antecedent rainfall are compared with rainfall-only strategies using the same candidate quotas, production targets, selection years and monitoring coverage.

The crop model connects these decisions to their agronomic consequences. Daily crop growth responds to weather and water supply, while a layered soil-water balance represents infiltration, evaporation, root uptake, redistribution, runoff and bottom drainage. Soil water passes through wheat, maize and intervening fallows without a seasonal reset. Irrigation strategies therefore operate on complete rotation histories, including the delayed effects of earlier management.

Field irrigation and ET have different accounting boundaries. ET can be supplied by rainfall and stored soil water as well as irrigation; changes in irrigation can also alter drainage, runoff and final storage. A reduction in field delivery therefore need not produce an equal reduction in ET or groundwater depletion.

## Experimental design and principal findings

Multisite observations support crop-growth parameter estimates. Documented Wuqiao irrigation treatments constrain eight water-response coefficients per crop using 2016–2018 observations; 2019 provides retrospective testing. The selected parameter set is `management_refit`.

Regional simulations represent **9.87 million ha** of mapped wheat–maize rotation in **3,641 source cells**, grouped into **32 climate–soil representatives**. Each representative has five continuous irrigation histories at fractions 0, 0.25, 0.50, 0.75 and 1.00 of a declared conventional schedule: 300 mm for wheat and 80 mm for maize per rotation. Application dates remain fixed. Eight additional source cells assess aggregation differences.

| Period | Role |
|---|---|
| 1996 | Initialization and spin-up before the first wheat crop |
| 1997–2013 | Selection of fixed spatial allocations under common irrigation budgets |
| 2003–2013 | Selection of storage–rainfall and rainfall-only quota rules |
| 2014–2025 | Comparison of frozen strategies using continuous crop and soil-water histories |

Spatial allocation maximizes mean combined wheat–maize grain under an identical delivery budget. Annual strategies select the lowest candidate quota meeting a 95% or 98% mean grain-retention target in their selection period. Thresholds and choices remain fixed in the later comparison period.

| Comparison | Principal result |
|---|---|
| Targeted versus uniform allocation at a 50% irrigation cut | Targeting retained 0.732 t dry grain ha⁻¹ yr⁻¹ more, equivalent to 7.219 Mt yr⁻¹ or 4.87% of uniform production. Crop-plus-fallow ET increased by 20.76 mm yr⁻¹. |
| Distribution of the same allocation contrast | Mean grain production was lower under targeting on 37.60% of mapped rotation area; losses persisted in all twelve comparison years on 21.34%. |
| Annual strategies over nine years with eligible GRACE observations | The 95% and 98% strategies retained 96.27% and 99.63% of conventional grain production. The 95% strategy reduced irrigation by 130.72 mm and ET by 36.84 mm per rotation. |
| Storage–rainfall versus matched rainfall-only decisions | Selected quotas and resulting histories were identical. Storage information provided no additional decision benefit within the tested design and coverage. |

These are **conditional model scenarios**. They support joint attention to regional production, consumptive use and local production safeguards. They do not establish independently validated field benefits or causal groundwater responses.

## Data and provenance

Reproduction uses the exact local subsets and derived inputs included with the completed run. It requires no Earth Engine account, provider login, separate model checkout or historical workspace archive.

| Data | Coverage and purpose | Included files |
|---|---|---|
| Multisite crop observations | Fengqiu, Gucheng, Luancheng, Shangqiu and Yucheng; intermittent crop seasons during 1998–2022. Phenology, LAI, biomass, harvest and available daily ET support growth estimation and conditional transfer comparisons. | [Calibration observations and availability](model/current_results/calibration/data/), [source dataset identifiers](model/current_results/publication/tables/trial_dataset_sources.csv), [exact site–crop years and partitions](model/current_results/publication/tables/trial_years_and_partitions.csv) |
| Wuqiao irrigation experiment | Four treatment groups in each crop during 2016–2019. Dated management, initial total soil-water storage, seasonal ET, biomass and digitized annual grain means support the water fit and retrospective test. | [Resolved model inputs](model/current_results/calibration/inputs/resolved/), [seasonal observations](model/current_results/calibration/data/wuqiao_used_seasonal_observations.csv), [digitized annual grain observations](model/current_results/calibration/data/wuqiao_digitized_annual_yields.csv) |
| AgERA5 v2.0 weather | Daily regional forcing for 1996–2025: temperature, rainfall, radiation, reference ET, vapor pressure and wind. Exact field forcing is retained separately. | [Regional forcing actually simulated](model/current_results/regional/data/exact_simulated_daily_weather.csv.gz), [field and station forcing actually used](model/current_results/calibration/data/exact_used_weather.csv.gz) |
| SoilGrids v2.0 and hydraulic derivation | Texture, bulk density, organic carbon and coarse fragments support layered hydraulic profiles through 2 m for regional simulations. | [Used hydraulic profiles](model/current_results/regional/data/used_hydraulic_profiles.json), [calibration soil inputs](model/current_results/calibration/data/exact_used_soil_layers.csv) |
| ChinaCP/CPM crop maps and SRTM terrain | The fixed 2020 wheat–maize footprint, corrected for cropland fractions, supplies rotation-area weights; terrain supports the lowland study-domain definition. | [Source-cell mapping and area weights](model/current_results/regional/data/all_source_cell_mapping.csv), [regional source snapshots](model/current_results/regional/source_snapshots/) |
| CSR GRACE/GRACE-FO storage | 259 observed monthly regional anomalies from April 2002 to July 2026. Available August–September observations preceding wheat sowing support annual quota classification. | [Monthly regional storage series](model/current_results/regional/data/observed_regional_twsa_monthly.csv), [decision-period availability](model/current_results/regional/data/pre_sowing_storage_availability.csv) |

The station archive contains 132 crop–plot–season cases, with eligible scored targets in 127. The 7,397 scored records include LAI, in-season biomass, daily ET and harvest samples; daily measurements and within-year quadrats are not independent growing seasons. Whole site-years retain their original partitions, and site contributions are balanced. Raw values, quality exclusions, matched observation windows and initialization assumptions remain inspectable in the calibration files.

At Wuqiao, maize treatment labels identify preceding wheat irrigation schedules; maize irrigation is identical among treatments within each year. The field comparisons retain treatment-specific initial storage and its assumed distribution among soil layers.

Water fluxes use mm; regional biomass and grain use dry matter. Wuqiao grain observations use 13% moisture: observed grain is multiplied by 0.87 for dry-matter fitting, and simulated dry grain is divided by 0.87 for comparison on the published basis. Station ET comparisons use matched measurement days, while regional ET includes complete crop and fallow periods. GRACE anomalies represent total terrestrial storage, including stores other than groundwater.

Source snapshots and SHA-256 records accompany the inputs and scientific engine. Original absolute paths in provenance records identify historical sources; execution uses repository-relative files. Missing observations remain missing. The GRACE comparison has nine eligible later years, with gaps in 2014, 2018 and 2019; matched strategies use conventional irrigation during those gaps.

Provider terms and attribution are documented in [DATA_SOURCES.md](DATA_SOURCES.md) and the [dataset catalogue](data/catalog/datasets.json). NESDC observations retain the recorded CC BY-NC 4.0 terms. Downloaded journal articles and publisher supplements are not redistributed.

## Reproduce the results

### 1. Install from a clean checkout

Python **3.12** is the tested interpreter. The pinned [requirements](requirements.txt) cover numerical calculations, process tests and publication generation.

```sh
git clone https://github.com/SmartAG-Team/agri_water_management.git
cd agri_water_management
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

All subsequent commands run from the repository root. After dependency installation, scientific reproduction uses local files without remote downloads or credentials.

The exact full replay was verified with **CPython 3.12.14 on macOS arm64**. Linux CI verifies every field-experiment output and all scored station crop outputs at the same strict tolerance. Linux station soil-water profiles and daily drainage can differ from the reference trajectory; the complete diagnostics retain these differences. Across all 164 Linux cases, daily ET differed by at most 3.01×10⁻¹¹ mm and biomass/grain by at most 1.10×10⁻¹¹ kg ha⁻¹, while the largest daily storage/drainage difference was 2.16 mm. Exact state matching uses the reference platform.

### 2. Run the quick reproduction

```sh
.venv/bin/python scripts/reproduce.py --mode quick
```

The quick workflow performs six steps:

1. Runs the crop and soil-water process tests.
2. Verifies frozen calibration sources and metrics, then reruns four 2019 field cases: wheat and maize under W0 and W3. Their ET, biomass and moisture-adjusted grain must match the saved predictions.
3. Verifies the regional archive and exact equality of its 143 native Python files with the calibration engine.
4. Independently recomputes the 405 audited Results, Conclusions and Highlights quantities from the included result tables.
5. Checks the bindings among scientific sources, publication tables and documents.
6. Prepares a fresh regional run without copying previous predictions and simulates four consecutive segments totaling 644 days, checking soil-water continuity and water and carbon conservation.

Quick mode checks the supplied regional results and a new continuous benchmark. **Full mode regenerates the regional policy results.** Both modes use the published fitted parameters; neither repeats parameter optimization or raw-data downloading and spatial preprocessing.

Each invocation creates `model/reproduction_<UTC timestamp>/`. Success prints `Reproduction passed: <path>`. The directory contains:

| Output | Meaning |
|---|---|
| `verification/reproduction.json` | Overall status, executed commands and completion metadata |
| `verification/*.log` | Output from each reproduction step; the failed step's log identifies any error |
| `regional/verification/replay_preparation.json` | Provenance of the copied inputs and confirmation that prior predictions were excluded |
| `regional/verification/replay_benchmark.json` | Fresh benchmark predictions, timing, continuity and conservation checks |

The source-check scripts also refresh verification receipts under `model/current_results/verification/`. The frozen scientific inputs and regional predictions remain unchanged.

### 3. Recalculate all regional scenarios

```sh
.venv/bin/python scripts/reproduce.py --mode full --workers 8 \
  --output model/full_reproduction
```

Choose a new or empty output directory, or omit `--output` to obtain a timestamped directory. The driver rejects a populated destination and preserves the completed `model/current_results/` run.

Full mode runs the quick checks, reproduces both crop-specific calibration objectives, and reruns all 164 selected field and station cases. Every daily output field is compared with the archived prediction, including soil-water states and presowing carry-over. It then recalculates continuous fixed-quota crop/fallow responses and source-cell checks, reselects spatial allocations, reselects annual quota rules, and simulates the storage–rainfall and rainfall-only histories. Regional tables and PNG/PDF figures are exported and five principal tables are compared with the published results:

| Compared table | Result checked |
|---|---|
| `regional_policy_annual_results.csv` | Annual grain production and water accounting under spatial allocations |
| `policy_comparison.csv` | Annual-strategy outcomes and production retention |
| `contrast_summary.csv` | Targeted-minus-uniform production and water contrasts |
| `training_selected_quotas.csv` | Selected annual quotas and selection-period retention |
| `full_quota_spatial_metrics.csv` | Source-cell aggregation checks across irrigation levels |

All columns must agree after sorting by the documented keys; numeric tolerances are `rtol=1e-10` and `atol=1e-6`. The comparison receipt is `regional/verification/published_results_comparison.json` within the new reproduction directory.

Full mode also recalculates geospatial distributions, connected loss areas, loss frequencies and annual exposure from the fresh regional outputs. Five geospatial tables are compared with the publication tables, with a separate receipt in `verification/spatial_reproduction_comparison.json`.

The full simulation contains 45,704 crop/fallow segments. The archived eight-worker benchmark estimated about 37 minutes for simulation; hardware, worker count and export processing affect elapsed time. `--workers 1` runs sequentially. Stage-by-stage preparation, benchmarking and replay are described in the [regional replay guide](model/current_results/analysis_source/REGIONAL_REPLAY_README.md).

### 4. Repeat the field-water calibration

The exact original [calibration source dependencies](scripts/calibration_source/) and bounded optimizer are included. The inherited multisite growth estimates remain fixed, while eight water-response coefficients per crop are estimated from the original 2016–2018 Wuqiao targets. The 2019 observations remain outside the fit.

```sh
.venv/bin/python scripts/refit_water.py --stage check \
  --output model/calibration_objective_check
.venv/bin/python scripts/refit_water.py --stage fit \
  --output model/water_calibration_refit
```

`check` independently reconstructs the selected data loss and complete objective, including priors. `fit` additionally repeats the original optimizer from the included starting parameter vectors. Both commands create a separate run with observations, weather, initialization inputs, scientific sources and SHA-256 provenance. The refit receipt reports convergence and differences from the published vectors; optimization can vary slightly across numerical libraries and platforms. These commands repeat the documented water refit; the earlier multisite growth coefficients are supplied as frozen inherited inputs.

### 5. Explore a single crop season

```sh
.venv/bin/python open_crop_model/run.py open_crop_model/examples/wheat_W0.json
.venv/bin/python open_crop_model/run.py open_crop_model/examples/maize_W0.json
```

Each command creates a separate run under `model/` with daily predictions, a seasonal summary, exact input and weather snapshots, parameters and SHA-256 provenance. These examples demonstrate the model interface; the reproduction driver supplies the paper-level comparisons. Input structure, parameter precedence and the Python interface are documented in the [Open Crop Model README](open_crop_model/README.md).

### 6. Rebuild publication assets

The publication figures and Word documents can be regenerated from the included result tables and manuscript sources:

```sh
.venv/bin/python -B model/current_results/analysis_source/current_spatial_analysis.py
.venv/bin/python -B model/current_results/analysis_source/remove_panel_titles.py
.venv/bin/python -B model/current_results/analysis_source/update_current_paper.py
.venv/bin/python -B model/current_results/analysis_source/export_publication_workbook.py
.venv/bin/python -B model/current_results/analysis_source/finalize_publication_assets.py
```

These commands update `model/current_results/publication/`, including figures, figure-data workbooks, editable Word documents and figure-collection PDFs. They use the included current results; a full reproduction writes its own regional exports to the new run directory. Inline Word equations are editable Office Math.

Complete manuscript PDFs are included. The optional [reading-copy renderer](model/current_results/publication/analysis_source/render_document_previews.py) uses macOS Quick Look and Google Chrome. Scientific simulations and numerical comparisons run independently of those applications.

## Repository guide

The [reproduction evidence](model/current_results/verification/reproduction_evidence/summary.json) records the clean-checkout verification on 8 October 2026. On the reference platform, all five principal regional tables matched exactly after 45,704 fresh crop/fallow segments, and all 164 field and station cases matched their archived daily outputs within floating-point precision. Repeating both water-parameter optimizers also recovered the published vectors and calibration losses exactly. Five geospatial tables independently matched the fresh recalculation. GitHub Actions checks the sources, manuscript quantities, continuous benchmark, calibration cases and both objectives on macOS and Linux, using the platform scopes described above.

| Content | Location |
|---|---|
| Unified quick/full reproduction | [`scripts/reproduce.py`](scripts/reproduce.py) |
| Fresh-versus-published regional comparison | [`scripts/compare_reproduction.py`](scripts/compare_reproduction.py) |
| All 164 selected calibration case replays | [`scripts/replay_calibration.py`](scripts/replay_calibration.py) |
| Original water calibration objective and optimizer | [`scripts/refit_water.py`](scripts/refit_water.py) |
| Standalone crop model and selected parameter cards | [`open_crop_model/`](open_crop_model/) |
| Exact calibration observations, management, weather and resolved inputs | [`calibration/`](model/current_results/calibration/) |
| Frozen regional inputs, policy rules and continuous scenario results | [`regional/`](model/current_results/regional/) |
| Manuscript and supplementary material | [`model/current_results/publication/documents/`](model/current_results/publication/documents/) |
| Figure PNG/PDF pairs and graphical abstract | [`publication/figures/`](model/current_results/publication/figures/) |
| Paper tables, observation coverage and figure data | [`publication/tables/`](model/current_results/publication/tables/) |
| Numerical audits and source-integrity records | [`verification/`](model/current_results/verification/) |

## Interpretation and limitations

The 2019 Wuqiao test was previously inspected during development and is retrospective. Complete station irrigation histories remain unconfirmed, and not all field accuracy criteria are met. The [calibration evaluation](model/current_results/calibration/evaluation.md) retains the relevant errors and criteria. Reproducible numerical agreement establishes computational consistency, while independent agronomic validation requires additional observations.

Regional results depend on a static 2020 rotation footprint, representative climate–soil units, prescribed irrigation dates and nutrition modifiers, and a 2 m soil profile with free drainage and no capillary supply. Dynamic nitrogen cycling and measured groundwater abstraction are absent. GRACE availability before the actual sowing decision is unverified, and the later comparison includes only the lower-storage class. These assumptions delimit transfer to farm scheduling and groundwater management.

Future evaluation requires independent site-years with complete management and joint crop/soil-water measurements, regional uncertainty analysis, prospective comparisons using information available at each decision date, and accounting for pumping, recharge, delivery capacity and local production risks. The manuscript's Section 4.6 develops these limitations and future directions.

Repository citation metadata are provided in [CITATION.cff](CITATION.cff); dataset reuse remains subject to the original provider terms in [DATA_SOURCES.md](DATA_SOURCES.md).
