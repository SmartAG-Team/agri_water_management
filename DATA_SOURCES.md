# Data sources and terms

The exact observation subsets, quality exclusions, management, weather, soil inputs and parameter sets used by the study are included under `model/current_results/calibration/` and `model/current_results/regional/`. Reproduction uses these local snapshots and does not require an Earth Engine account, provider login, another model checkout or the historical workspace archives.

Original datasets retain their provider terms. The observation-source catalogue, with dataset identifiers, DOI links and licences, is included at `data/catalog/datasets.json`. NESDC station observations are attributed under the **Creative Commons Attribution–NonCommercial 4.0** terms recorded there. The study's curated observation files retain source identifiers and quality flags; their organization and exclusions differ from the original provider files.

AgERA5 forcing is attributed to the Copernicus Climate Data Store/ECMWF. GRACE storage anomalies are attributed to the CSR mascon products. The study uses frozen regional storage series and weather subsets, rather than requiring a new remote download. ChinaCP, SoilGrids and SRTM supply the mapped rotation, soil and terrain inputs described in the manuscript. Source definitions and references are retained in `model/current_results/publication/tables/trial_dataset_sources.csv` and `model/current_results/publication/literature/manuscript_references.csl.json`.

Wuqiao field observations include treatment means and standard errors digitized from the primary irrigation experiment, with the original moisture basis, sampling units and provenance retained. The experiment is identified in the manuscript Methods and reference records. Downloaded journal articles and publisher supplements are not redistributed in this repository.

Natural Earth provincial boundary data are public domain. Source versions, licence records and the study-domain construction are included in `model/current_results/publication/source_snapshots/cartography/`. The operational study outline is a physiographic analysis domain.

Public repository access does not replace source attribution or change the original providers' licences. Existing software ownership and licence notices are retained; no blanket licence is applied to third-party datasets or source material.
