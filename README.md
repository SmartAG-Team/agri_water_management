# North China Plain irrigation study

Data, executable model source and reproducible analysis for *Irrigation strategies under contrasting water-storage and rainfall conditions in the North China Plain*.

The repository contains the standalone **Open Crop Model**, the selected `management_refit` parameters, exact calibration observations and weather, continuous regional inputs and results, figure data, manuscript source and publication files. Original downloaded datasets and historical experiments are retained outside the public repository; they are unnecessary for the reproduction commands below.

## Reproduce from a clean checkout

Python **3.12** is the tested interpreter. Install the pinned numerical and publication dependencies:

```sh
git clone https://github.com/SmartAG-Team/agri_water_management.git
cd agri_water_management
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/reproduce.py --mode quick
```

The quick reproduction checks scientific source integrity, runs the included process tests, reruns representative wheat and maize field seasons with the exact frozen inputs, verifies the regional source archive, prepares a fresh continuous regional benchmark, and independently recomputes every audited Results, Conclusions and Highlights quantity. It writes a separate run under `model/`. No credentials or remote datasets are required after the initial dependency installation.

## Full regional recalculation

```sh
.venv/bin/python scripts/reproduce.py --mode full --workers 8 \
  --output model/full_reproduction
```

This recalculates continuous crop/fallow responses, irrigation allocations and annual storage–rainfall and rainfall-only strategies from the frozen weather and soil inputs. It preserves the 1996 initialization, 1997–2013 allocation-selection period, 2003–2013 annual-strategy selection period and 2014–2025 comparison period. Full recalculation has 45,704 crop/fallow segments; the original eight-worker benchmark estimated approximately 37 minutes, with actual runtime depending on hardware and output processing. Existing completed runs are preserved.

## Publication and numerical results

| Content | Location |
|---|---|
| Manuscript and supplementary material | [`model/current_results/publication/documents/`](model/current_results/publication/documents/) |
| Figure PNG/PDF pairs and graphical abstract | [`publication/figures/`](model/current_results/publication/figures/) |
| Paper tables and supporting data | [`publication/tables/`](model/current_results/publication/tables/) |
| Exact calibration inputs, observations and comparisons | [`calibration/`](model/current_results/calibration/) |
| Frozen regional inputs and continuous scenario results | [`regional/`](model/current_results/regional/) |
| Numerical claims and source-integrity records | [`verification/`](model/current_results/verification/) |
| Standalone crop model | [`open_crop_model/`](open_crop_model/) |

The selected water calibration uses Wuqiao 2016–2018; 2019 supplies retrospective testing. The multisite comparisons retain their original whole site-year partitions and conditional management interpretation. Regional results are conditional model scenarios. Source-integrity checks and reproducible numerical agreement do not establish independent field validation or causal groundwater responses.

Effective shared RUE is 3.082080164 for wheat and 3.379973918 g dry matter MJ⁻¹ intercepted PAR for maize, with PAR fraction 0.45. Specific leaf area and senescence are stage-specific; maximum LAI is a simulated output.

## Rebuild manuscript source and figures

```sh
.venv/bin/python -B model/current_results/analysis_source/remove_panel_titles.py
.venv/bin/python -B model/current_results/analysis_source/update_current_paper.py
.venv/bin/python -B model/current_results/analysis_source/export_publication_workbook.py
.venv/bin/python -B model/current_results/analysis_source/finalize_publication_assets.py
```

These commands regenerate publication figures, editable Word documents and figure-collection PDFs. Complete manuscript PDFs are included. The optional full-document reading-copy renderer uses macOS Quick Look and Google Chrome; scientific model and numerical reproduction do not depend on these applications. Inline Word equations are editable Office Math. The original absolute paths stored in provenance files identify sources; current execution uses repository-relative snapshots.

Dataset attribution and reuse terms are listed in [DATA_SOURCES.md](DATA_SOURCES.md), and repository citation metadata are provided in [CITATION.cff](CITATION.cff).
