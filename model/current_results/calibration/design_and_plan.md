# Documented-management water calibration

**Objective:** Seasonal ET and irrigation-response accuracy sufficient for management scenarios, with plausible yield and biomass and retained multisite growth evaluation.

**Scientific basis:** Station irrigation-log completeness is unconfirmed. Unknown water supply affects growth as well as daily ET, so those growth observations cannot identify water-response coefficients independently of their management assumptions. The retained growth coefficients originate from the prior calibration using 480 wheat and 433 maize observations across every eligible station. The new water calibration uses documented Wuqiao treatment management and source-confirmed harvest measurements.

**Calibration:** Eight existing coefficients per crop are estimated: transpiration multiplier, soil evaporation coefficient, readily available water fraction, assimilation water-stress exponent, root activity decay, maximum root depth, daily root extraction coefficient and root compensation. Existing bounds and coefficient prior centres and scales are retained. RUE, leaf allocation and grain-number coefficients retain their previous multisite estimates. No native equations or forcing are changed.

**Objective definition:** Original field weights are retained: seasonal ET 0.325, dry grain yield 0.175, dry aboveground biomass 0.15 and W3−W0 seasonal ET response 0.1. Total prior weight is 0.01 over the eight newly estimated coefficients. Conditional station comparisons contribute no residuals to the new water objective. The reference is recomputed under the same field objective; scores from the previous combined objective are not compared directly.

**Partition:** Field fitting uses 2016–2018, twelve treatment-seasons per crop. The 2019 field year is excluded from fitting and selection. Testing is retrospective. Every existing station and whole site-year partition is retained for conditional evaluation after the water refit. Station calibration-partition comparisons are transfer diagnostics of the water refit; their growth coefficients have an earlier calibration history. Neither the old growth fit nor the new conditional comparisons establishes independent management validation.

**Acceptance:** ET RMSE ≤50 mm, nRMSE ≤15%, absolute bias ≤10% of observed mean, positive NSE, irrigation-response error ≤50 mm and yield nRMSE ≤20%, alongside biomass and LAI plausibility. Numerical integrity alone does not establish accuracy.

**Execution and verification:**

- [ ] Verify parameter selection and exact preservation of the inherited growth coefficients.
- [ ] Fit both crops with documented field forcing and observations; verify the reference objective against saved comparisons.
- [ ] Freeze selection using only the equal-crop mean field calibration loss.
- [ ] Evaluate all 164 existing cases per parameter card; retain exact weather, observations, input snapshots and SHA-256 provenance.
- [ ] Independently verify budgets, observation windows, metrics, moisture bases and qualification flags.
- [ ] Export the data workbook and PNG/PDF figures, inspect figures and seal the run. Promotion requires supported irrigation accuracy.
