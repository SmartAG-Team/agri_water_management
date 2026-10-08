Documented-management water calibration

Eight existing water-response coefficients per crop are calibrated against documented Wuqiao treatment management in 2016–2018. RUE, leaf allocation and grain-number coefficients retain their earlier multisite estimates, supported by 480 wheat and 433 maize growth/yield observations across all four eligible wheat sites and five maize sites. Source station irrigation-log completeness remains unconfirmed, so station outcomes do not identify water-response coefficients in this refit. All station observations and whole site-year partitions remain available for conditional transfer evaluation. No source value or native equation is changed.

The field objective retains weights of 0.325 for seasonal ET, 0.175 for dry grain yield, 0.15 for confirmed dry aboveground biomass and 0.1 for W3−W0 ET response. Total prior weight is 0.01 over the eight estimated coefficients. Bounds and prior centres/scales retain their inherited values. Reference and candidate scores use this same field-only objective; scores from the former combined station/field objective are not directly comparable.

The 2019 field year remains outside fitting and selection. Testing is retrospective because its outcomes were inspected during earlier development. Station calibration-partition curves are transfer diagnostics after the water refit, with an earlier growth-calibration history; they are not direct new water calibration or independent management validation.

The selected version is management_refit. Reference field calibration loss is 0.592580; candidate loss is 0.577934. Selection precedes testing.

| Version | Crop | Partition | ET RMSE (mm) | ET nRMSE (%) | ET R² | ET NSE | ET response error (mm) | Yield nRMSE (%) | Biomass nRMSE (%) |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| management_refit | Maize | Calibration | 37.44 | 12.69 | 0.619 | 0.533 | 66.84 | 18.78 | 21.79 |
| management_refit | Maize | Retrospective testing | 55.64 | 14.13 | 0.343 | -0.346 | 52.74 | 15.97 | 10.44 |
| management_refit | Wheat | Calibration | 40.19 | 8.97 | 0.758 | 0.689 | 45.79 | 16.74 | 25.48 |
| management_refit | Wheat | Retrospective testing | 73.80 | 21.98 | 0.915 | 0.503 | 116.37 | 83.27 | 55.66 |
| reference | Maize | Calibration | 36.78 | 12.46 | 0.643 | 0.549 | 64.45 | 19.64 | 22.67 |
| reference | Maize | Retrospective testing | 52.39 | 13.30 | 0.404 | -0.193 | 52.49 | 15.62 | 10.50 |
| reference | Wheat | Calibration | 41.29 | 9.22 | 0.744 | 0.672 | 43.63 | 17.93 | 27.02 |
| reference | Wheat | Retrospective testing | 71.94 | 21.43 | 0.907 | 0.528 | 111.79 | 81.30 | 53.47 |

Working criteria are ET RMSE ≤50 mm, nRMSE ≤15%, absolute bias ≤10% of observed mean, positive NSE, ET-response error ≤50 mm and field-yield nRMSE ≤20%, alongside biomass and LAI plausibility. Four testing means per crop and one annual contrast provide limited evidence of transfer across years. These are study criteria, not universal publication thresholds.

Published field ET is a 0–2 m water-balance estimate under negligible runoff and drainage assumptions. Vertical initial water profiles and exact storage sampling order remain unconfirmed. Station management uncertainty also affects growth and soil-water comparisons. No phase ET estimate with unconfirmed sampling windows enters this refit.

The selected version does not meet all field testing criteria. No manuscript or regional-policy promotion follows.
