"""Apply source-grounded current crop sections to article and supplement blocks."""
from copy import deepcopy
from pathlib import Path
import json
import re

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PUBLICATION = ROOT / "publication"
TABLES = PUBLICATION / "tables"


def _section(document, prefix):
    matches = [b for b in document["blocks"] if b.get("heading", "").startswith(prefix)]
    if len(matches) != 1:
        raise ValueError(f"Expected one section starting with {prefix!r}; found {len(matches)}")
    return matches[0]


def _table(document, number):
    prefix = f"Table {number}."
    matches = [b for b in document["blocks"] if b.get("table_caption", "").startswith(prefix)]
    if len(matches) != 1:
        raise ValueError(f"Expected one {prefix}")
    return matches[0]


def _figure(document, number):
    prefix = f"Figure {number}."
    matches = [b for b in document["blocks"] if b.get("caption", "").startswith(prefix)]
    if len(matches) != 1:
        raise ValueError(f"Expected one {prefix}")
    return matches[0]


def _bind_figure(document, number, stem):
    image = PUBLICATION / "figures" / (stem + ".png")
    caption = image.with_name(stem + "_caption.txt")
    if not image.exists() or not caption.exists():
        raise FileNotFoundError(image)
    block = _figure(document, number)
    block["figure"] = "figures/" + image.name
    block["caption"] = re.sub(r'^Figure S?\d+\.', f'Figure {number}.', caption.read_text().strip())


def _metric(frame, crop, split, variable):
    result = frame[frame.crop.eq(crop) & frame.split.eq(split) & frame.variable.eq(variable)]
    if len(result) != 1:
        raise ValueError((crop, split, variable))
    return result.iloc[0]


def _sci(value):
    mantissa, exponent = f"{value:.2e}".split("e")
    return mantissa + " × 10" + str(int(exponent)).translate(str.maketrans("-0123456789", "⁻⁰¹²³⁴⁵⁶⁷⁸⁹"))


def apply(article, supplement):
    """Return copied block documents with current crop narrative and bindings.

    Regional sections and the final strategy paragraph of Discussion 4.5 are
    left to the main publication integration. No document or table is written.
    """
    article, supplement = deepcopy(article), deepcopy(supplement)
    frozen = json.loads((ROOT / "calibration/parameters/frozen_model.json").read_text())
    if frozen["selected_version"] != "management_refit":
        raise ValueError("Current narrative requires selected management_refit")
    station = pd.read_csv(TABLES / "current_station_metrics.csv")
    field = pd.read_csv(ROOT / "calibration/tables/field_metrics.csv")
    field = field[field.version.eq("management_refit")]
    summaries = pd.read_csv(ROOT / "calibration/predictions/case_summaries.csv")
    summaries = summaries[summaries.version.eq("management_refit")]
    water_residual = _sci(summaries.water_residual_max_mm.max())
    carbon_residual = _sci(summaries.carbon_residual_max_kg_ha.max())

    _section(article, "2.3.1.")["paragraphs"] = [
        "Winter-wheat and summer-maize observations were assembled from the National Ecosystem Science Data Center and linked archives at Fengqiu, Gucheng, Luancheng, Shangqiu and Yucheng. The sources contain long-term crop monitoring and the Gucheng maize sowing-date experiment. Archived crop-season groups span 2004–2008 at Fengqiu and Luancheng, 2018–2020 at Gucheng, 2001–2007 at Shangqiu and 1998–2022 at Yucheng. Coverage is intermittent; exact crop-by-site years and dataset identifiers are listed in Tables S1 and S2.",
        "The archive retains 132 crop–plot–season trajectories in 71 original calibration and 61 retrospective-evaluation cases (Figure 3). Quality-controlled targets occur in 127 cases. The 7,397 scored records comprise 795 LAI measurements, 543 in-season aboveground-biomass measurements, 5,675 daily ET measurements, 192 grain-yield samples and 192 harvest-biomass samples (Table 1). Daily ET is available at Fengqiu and Yucheng; grain and harvest-biomass samples are confined to Yucheng. Six quadrats within a crop-year characterize sampling variation rather than six independent growing seasons.",
        "Observations and predictions share recorded sampling dates or documented averaging windows. Stage-indexed Gucheng LAI measurements use the corresponding observed stage dates as sampling coordinates; those dates do not prescribe simulated development. Fengqiu ET identifies the lysimeter observation plot. Yucheng ET retains the measurement definitions of its source archive. Provider grain and aboveground dry masses in g m⁻² are multiplied by 10 to obtain kg ha⁻¹.",
        "Eligibility requires valid dates, compatible crop and plot identities, consistent stage sequences and documented quantity units. Wheat LAI above 7 m² m⁻² is excluded under the study-specific screening rule, with raw values and reasons preserved. The six-replicate maize harvest vectors in Yucheng 2012 and 2017 are identical despite different dates and cultivar labels. Both groups remain excluded because their source authenticity is unresolved; 12 samples account for 24 grain and harvest-biomass records. Other observations and all eligible sites remain available.",
        "Complete irrigation histories matched to station plots remain unconfirmed. Station growth and ET consequently supply conditional transfer comparisons; no station observation enters the current water-parameter objective. Twenty-six Fengqiu cases retain complete 0–1.5 m moisture profiles from the preceding calendar month: 11 in the original calibration partition and 15 in retrospective evaluation. Volumetric percentages from fifteen 100-mm source layers are conservatively remapped to the model layers without depth extrapolation or clipping. Their monthly timing and subplot support do not establish a measured sowing-day state. Cases without an eligible profile retain their archived initialization."
    ]
    _section(article, "2.3.2.")["paragraphs"] = [
        "The Wuqiao experiment at 37.630°N, 116.444°E contains four wheat irrigation schedules, W0–W3, during the 2015–2019 rotation [CITE:yang2024_precipitation]. Dated 75-mm events, sowing and harvest dates, nitrogen input of 158.4 kg ha⁻¹ per crop and treatment-specific initial total water storage in 2 m are retained. Maize irrigation is identical among treatments within each year: zero in 2016 and 2018, and 75 mm on 18 June in 2017 and 2019. Its W0–W3 labels therefore identify preceding wheat management. Seasonal initial water distribution among layers is assumed. Field capacity, wilting point and bulk density are transferred from the published Luancheng profile, with its bottom 10 cm extrapolated [CITE:sun2006_water_balance]. Conductivity follows a separate SoilGrids–Saxton–Rawls prior [CITE:soilgrids2020_dataset|saxton2006_hydraulics]; the particle-density prior is 2.75 g cm⁻³.",
        "Published seasonal ET equals rainfall plus irrigation plus depletion of 0–2 m storage, with runoff and drainage treated as negligible [CITE:yang2024_precipitation]. The model represents both outflows. The 2016 maize balance uses 499.1 mm of maize-season rainfall; the 599.6-mm rotation total also contains 100.5 mm during wheat. This period reconciliation reproduces the four printed maize ET values within 0.9 mm of rounding, and all 32 crop–treatment–season ET targets remain eligible. Simulations retain their exact archived weather forcing. Wheat crop-start storage follows the pre-sowing simulation, whereas maize is independently initialized from observed treatment-specific total storage. Reported wheat-straw mulch has no measured cover fraction, so the maize simulations retain the exposed-soil evaporation boundary [CITE:yang2024_precipitation].",
        "Harvest aboveground biomass includes grain and residues cut at ground level and oven-dried to constant mass. Its dry-matter basis requires no moisture adjustment. Published grain yields are standardized to 13% moisture [CITE:yang2024_precipitation]. Annual 2016–2019 means and standard errors are digitized from the source Supplementary Figure S2 and reconciled with its eight printed four-year means. Pixel tolerances are approximately 0.043 t ha⁻¹ for wheat and 0.056 t ha⁻¹ for maize. Observed grain is multiplied by 0.87 for dry-matter fitting; model dry grain is divided by 0.87 for comparisons reported at 13% moisture. Annual and four-year means overlap and do not provide separate independent observations.",
        "The 2016–2018 harvest years contribute 12 treatment seasons per crop to the water fit, with seasonal ET, dry grain, dry aboveground biomass and annual W3−W0 ET contrasts as targets. The four 2019 treatment means per crop are excluded from fitting and parameter selection. Their prior inspection during model development makes this test retrospective (Figure 3)."
    ]
    _section(article, "2.4.")["paragraphs"] = [
        "Open Crop Model (OCM) couples crop growth and a layered soil-water balance at a daily time step. Wheat development combines temperature, vernalization and photoperiod responses; maize development follows thermal time. Development stage (DVS) is anchored at emergence, flowering and maturity. Germination begins when water above the wilting-point storage is available in the represented layer containing the seed; subsequent thermal development starts from that date. Stage coefficients remain fixed, while the soil-water state can alter establishment timing. Recorded or prescribed harvest dates define the management endpoint separately. Soil-water storage passes continuously through crops and fallows.",
        "Canopy interception of radiation follows Beer–Lambert attenuation. Aboveground production depends on intercepted PAR, crop-specific RUE, temperature, prescribed nutrition and an empirical power response to realized transpiration supply. PAR is 0.45 of incident shortwave radiation [CITE:kiniry1989_rue]. Stage-specific SLA determines new leaf area, while established cohorts retain their birth area-to-mass ratio [CITE:pcse_leaf_dynamics]. Stage-dependent thermal senescence and a separate daily stress-loss fraction determine leaf survival [CITE:tan2020_stage_senescence]. LAI emerges from these carbon and area balances without a maximum-LAI parameter. Simulated biomass at wheat flowering or maize silking determines grain number; fixed individual grain mass defines sink capacity, and assimilate supply and wheat stem reserves constrain grain filling.",
        "The water balance represents interception, ponding, infiltration, evaporation, root uptake, runoff and bottom drainage. Atmospheric demands are partitioned from reference ET using canopy cover and effective transpiration and evaporation coefficients. Soil evaporation follows a storage-based two-stage reduction [CITE:allen1998_fao56]. Root uptake depends on rooted-layer water, local depletion stress and a bounded compensation fraction that redirects unmet demand toward better-supplied rooted layers. Vertical transport uses van Genuchten–Mualem hydraulic functions [CITE:vangenuchten1980|mualem1976], signed gravity–matric gradients and conservative storage bounds. Relative-arithmetic interface mobility and adaptive internal steps permit wetting and redistribution within 24 daily outer steps. The lower boundary is free drainage with no capillary-rise supply. This explicit layered approximation differs from a full implicit Richards-equation solution. Profile depths follow the supplied inputs, including 2 m for Wuqiao and regional simulations."
    ]
    _section(article, "2.5.")["paragraphs"] = [
        "Phenological stage coefficients, stage-specific SLA and thermal senescence, initial LAI, stem-reserve coefficients and prescribed nutrition modifiers retain their earlier estimates. RUE, leaf allocation and grain-number coefficients retain the inherited multisite estimates supported by 480 wheat and 433 maize growth and harvest observations across four eligible wheat sites and five maize sites. Whole site-years remain in their original partitions, and inherited growth estimation balances available variables, sites, site-years and crop cases. Station simulations retain their archived site and cultivar parameterization; Wuqiao and regional simulations transfer the shared effective crop parameters (Table S3). Fixed individual grain masses are 0.045 g for wheat and 0.300 g for maize, conditional model priors documented in APSIM [CITE:apsim_ng_source_2026].",
        "Eight existing water-response coefficients per crop are estimated from documented Wuqiao management in 2016–2018: the transpiration multiplier, soil evaporation coefficient, readily available water fraction, assimilation water-stress exponent, root-density decay, maximum rooting depth, log₁₀ daily root extraction coefficient and root compensation fraction. RUE, leaf allocation and grain-number multipliers remain fixed. Each crop contributes 12 seasonal ET, 12 dry-grain and 12 dry-biomass targets, together with three W3−W0 ET contrasts derived from the same seasonal targets. The criterion retains weights of 0.325, 0.175, 0.15 and 0.10 for those components. ET residual scales are max(50 mm, 0.15 × observed ET); grain and biomass scales are max(1,000 kg ha⁻¹, 0.20 × observed dry mass). Contrast residuals use 50 mm. Eight coefficient-prior terms have total weight 0.01, with inherited bounds, centres and scales.",
        "Bounded least squares starts from the inherited water vector, with coefficients scaled to their prescribed intervals, a 40-evaluation limit and convergence tolerances of 8 × 10⁻⁵. Reference and refitted coefficients use the identical field-only criterion; the equal-crop mean calibration data loss is 0.592580 for the reference and 0.577934 for the selected refit. Selection excludes prior penalties from this comparison and precedes 2019 testing. Station observations, daily ET and phase ET with unresolved sampling windows have zero weight in the current water fit. Source observations, weather, soil priors, initialization rules and native process equations remain fixed during this refit.",
        "The selected parameters are frozen before retrospective Wuqiao testing and current station comparisons. Earlier inspection of evaluation observations prevents prospective validation claims. Station scores characterize transfer under unconfirmed complete management and inherited growth-calibration history. Regional management comparisons in 2014–2025 are retrospective conditional scenarios because the current crop-calibration years overlap that calendar. Observation windows and grain-moisture bases remain matched, and ET summed over measured days remains separate from full-season simulated ET."
    ]
    _section(article, "2.8.")["paragraphs"] = [
        "Performance is summarized by RMSE, mean bias error, nRMSE relative to observed mean, Nash–Sutcliffe efficiency (NSE) [CITE:nash1970_efficiency] and squared Pearson correlation (R²). Station scores give equal weight to available sites, site-years within sites, crop cases within site-years and records within cases. Wuqiao treatment means receive equal weights within each crop and partition. Field calibration and retrospective testing remain separate. Grain and harvest biomass in station comparisons are dry matter; Wuqiao grain results retain the published 13% moisture basis, with a single 0.87 conversion between dry and reported grain. Biomass remains dry matter throughout.",
        "Seasonal curves group matching observations and predictions into 15-day wheat and 7-day maize bins at observation-window midpoints in days after sowing. Case means receive equal weight within site-years, site-year means within sites and available sites within bins. Standard errors describe variation among contributing site-year means rather than model uncertainty. Site-specific curves use equal site-year weights. Coverage varies among bins, and gaps remain uninterpolated. Accuracy scores use unbinned records. Yucheng harvest error bars describe quadrat sampling variation within crop-years.",
        "ET sums use identical observed and simulated measurement days within each case and retain the station balancing weights. They exclude unmeasured days and do not represent complete-season observed totals. Regional grain production uses dry mass and rotation-area weights; actual ET includes crops and fallows. Paired policy differences cover 2014–2025. Daily water and carbon residuals and continuity of carried soil-water storage assess numerical conservation. Field irrigation, ET, drainage below 2 m and groundwater storage have distinct accounting boundaries."
    ]

    _bind_figure(article, "3", "current_Figure_3_observation_partitions")
    _bind_figure(article, "4", "current_Figure_4_multisite_seasonal_curves")
    _bind_figure(article, "5", "current_Figure_5_Wuqiao_calibration_testing")
    table = _table(article, "1")
    table.update(table="tables/main_observation_coverage.csv",
        table_caption="Table 1. Eligible station-observation records by site and variable.",
        table_note="Counts denote individual observations, not independent experiments. Station comparisons retain inherited growth-fitting history and unconfirmed complete management. Yucheng grain and harvest biomass contain six dry-matter samples per crop-year; the duplicated 2012 and 2017 maize groups remain excluded.")
    table = _table(article, "2")
    table.update(table="tables/main_matched_day_et.csv",
        table_caption="Table 2. Conditional station ET sums on matched measurement days.",
        table_note="Observed and simulated sums use identical measurement days within cases, with balanced site, site-year and case weights. Unmeasured days are excluded. Original calibration partitions retain an earlier growth-fitting history; evaluation is retrospective. Complete irrigation-log matching remains unconfirmed.",
        header_labels={"RMSE":"RMSE (mm)", "Bias":"MBE (mm)"},
        precision={"RMSE":2,"Bias":2,"NSE":3})
    table = _table(article, "3")
    table.update(table="tables/main_benchmark_metrics.csv",
        table_caption="Table 3. Wuqiao water calibration and retrospective testing.",
        table_note="Calibration uses 12 treatment seasons per crop in 2016–2018; retrospective testing uses four in 2019. Counts are treatment means rather than independent replicate seasons. ET is a 0–2 m water-balance estimate under negligible runoff and drainage assumptions. Grain is at 13% moisture and aboveground biomass is dry matter. Annual grain means and SE are digitized from source Supplementary Figure S2 [CITE:yang2024_precipitation]. Initial total storage is observed; layer distribution and transferred hydraulics remain conditional.",
        precision={"RMSE":3,"Bias":3,"nRMSE_pct":2,"NSE":3,"R²":3})

    wc = _metric(station,"wheat","calibration","lai")
    we = _metric(station,"wheat","validation","lai")
    mc = _metric(station,"maize","calibration","lai")
    me = _metric(station,"maize","validation","lai")
    wb = _metric(station,"wheat","validation","biomass")
    mb = _metric(station,"maize","validation","biomass")
    wy = _metric(station,"wheat","validation","yield")
    my = _metric(station,"maize","validation","yield")
    wet = _metric(station,"wheat","validation","et")
    met = _metric(station,"maize","validation","et")
    b = _section(article,"3.1.")
    b["heading"]="3.1. Conditional multisite crop comparisons"
    b["paragraphs"]=[
        "The original station partitions retain all five eligible sites and whole site-years (Figure 3). Current comparisons contain 7,397 scored observations, including 913 non-ET observations in the original calibration partition and 809 in retrospective evaluation. Availability differs by variable and site, with annual harvest outcomes confined to Yucheng (Table 1).",
        f"Retrospective-evaluation LAI RMSE was {we.rmse:.3f} m² m⁻² for wheat and {me.rmse:.3f} m² m⁻² for maize, with NSE of {we.nse:.3f} and {me.nse:.3f}, respectively (Figure 4). Corresponding RMSE in the prior calibration partition was {wc.rmse:.3f} and {mc.rmse:.3f} m² m⁻². Evaluation in-season biomass RMSE was {wb.rmse:.3f} and {mb.rmse:.3f} t ha⁻¹, with biases of {wb.bias:+.3f} and {mb.bias:+.3f} t ha⁻¹ and NSE of {wb.nse:.3f} and {mb.nse:.3f}. Site-specific seasonal comparisons retain their available observations and original partitions (Figures S7–S12).",
        f"Dry-grain RMSE was 1.256 t ha⁻¹ for wheat and 1.402 t ha⁻¹ for maize in the prior calibration partition, compared with {wy.rmse:.3f} and {my.rmse:.3f} t ha⁻¹ in retrospective evaluation (Figure S6). Evaluation grain biases were {wy.bias:+.3f} and {my.bias:+.3f} t ha⁻¹, and NSE was {wy.nse:.3f} and {my.nse:.3f}. Evaluation harvest-biomass RMSE was 2.736 t ha⁻¹ for wheat and 3.719 t ha⁻¹ for maize; the corresponding sample counts were 54 in nine wheat years and 48 in eight maize years.",
        f"Daily ET evaluation RMSE was {wet.rmse:.3f} mm d⁻¹ for wheat and {met.rmse:.3f} mm d⁻¹ for maize, with biases of {wet.bias:+.3f} and {met.bias:+.3f} mm d⁻¹ and NSE of {wet.nse:.3f} and {met.nse:.3f} (Figure 4). ET summed over matched measurement days had evaluation RMSE of 51.29 mm for wheat and 74.17 mm for maize, biases of +25.94 and −55.58 mm, and NSE of 0.826 and 0.118 (Table 2). These sums cover only observed days."
    ]
    b = _section(article,"3.2.")
    b["heading"]="3.2. Wuqiao calibration and retrospective testing"
    b["paragraphs"]=[
        "Seasonal ET calibration RMSE was 40.19 mm for wheat and 37.44 mm for maize across 12 treatment seasons per crop, with biases of −12.65 and +3.60 mm and nRMSE of 8.97% and 12.69% (Table 3; Figure 5). Across the four 2019 testing means per crop, ET RMSE was 73.80 and 55.64 mm, bias was +48.93 and −35.90 mm, nRMSE was 21.98% and 14.13%, and NSE was 0.503 and −0.346, respectively.",
        "At 13% grain moisture, calibration RMSE was 1.249 t ha⁻¹ for wheat and 2.267 t ha⁻¹ for maize; retrospective-testing RMSE was 3.161 and 1.700 t ha⁻¹. Testing grain biases were +3.108 and +1.403 t ha⁻¹, with nRMSE of 83.27% and 15.97% and NSE of −4.654 and −3.641 (Table 3; Figure 5). Dry aboveground-biomass RMSE was 4.222 and 4.688 t ha⁻¹ in calibration and 5.427 and 2.038 t ha⁻¹ in testing, respectively. Testing biomass nRMSE was 55.66% for wheat and 10.44% for maize.",
        "W3−W0 seasonal ET-contrast RMSE was 45.79 mm for wheat and 66.84 mm for maize across three calibration years; the absolute 2019 contrast errors were 116.37 and 52.74 mm. For grain contrasts of W1–W3 relative to same-year W0, calibration RMSE was 0.519 and 1.669 t ha⁻¹ and retrospective-testing RMSE was 1.316 and 1.323 t ha⁻¹, respectively, at 13% moisture (Table S5; Figure S2). Maize treatments retain preceding wheat histories with identical current-year maize irrigation [CITE:yang2024_precipitation]."
    ]

    _section(supplement,"S1.")["paragraphs"]=[
        "The station archive preserves 132 crop–plot–season trajectories, whole site-year partitions and source metadata. The 7,397 eligible records occur in 127 cases and retain all five observation sites for their available variables. Calibration and retrospective-evaluation trajectory counts remain 71 and 61. Variable-specific availability and missing combinations are retained in the accompanying case and site tables; missing measurements do not become zero observations.",
        "Wheat LAI above 7 m² m⁻² remains excluded under the study-specific rule. Duplicated six-replicate maize harvest vectors in Yucheng 2012 and 2017 account for 24 excluded grain and biomass records, divided equally between the original calibration and evaluation partitions. These source-based exclusions retain raw values and do not depend on prediction residuals.",
        "Earlier multisite growth estimates retain 480 wheat and 433 maize calibration observations. Current station growth and ET comparisons are conditional on archived management whose full irrigation-log completeness is unresolved. They have zero weight in the current water fit. Wuqiao 2016–2018 supplies 12 documented treatment seasons per crop for that fit; 2019 supplies four retrospective testing seasons per crop. Seasonal ET, grain and biomass targets refer to the same treatment-season population, while ET contrasts are derived from those seasonal targets.",
        "Twenty-six Fengqiu cases retain antecedent moisture initialization from the preceding calendar month. Complete fifteen-layer 0–1.5 m volumetric profiles map conservatively to the represented model layers, preserving depth-integrated storage. The identical eligibility rule applies to 11 original calibration and 15 evaluation cases. Monthly timing, aggregation and subplot support remain unresolved relative to the exact sowing-day state. Original cases remain available when a measured antecedent profile is absent."
    ]
    _section(supplement,"S2.1.")["paragraphs"]=[
        "Open Crop Model simulates phenology, green leaf area, live and dead aboveground biomass, grain dry matter, rooting depth and layered soil-water storage. Inputs comprise weather, soil profiles, crop parameters, dated sowing and harvest, and irrigation events. Soil water carries continuously between crop and fallow segments in regional histories; Wuqiao crop starts retain their separately specified storage conditioning.",
        "Wheat stage development combines temperature, vernalization and photoperiod modifiers with inherited stage requirements. Maize uses a subdaily temperature response reconstructed from daily extrema, with base, optimum and maximum temperatures of 6, 30 and 44 °C. The shared medium-maturity maize card requires approximately 1,909 °C d to reach R6. DVS anchors are wheat BBCH 09, 61 and 89 and maize VE, R1 and R6 [CITE:meier2018_bbch]. Wheat dough stage and full ripeness retain their distinct definitions.",
        "Seed-layer water above wilting-point storage permits germination. The represented layer is selected by sowing depth, with a 50-mm prior when no depth is supplied. Thermal development and wheat vernalization begin from the first eligible germination day rather than accumulating during dry-seed days. This layer-average availability is an effective establishment condition, not a measured seed-scale water potential. Fixed stage coefficients therefore do not imply fixed calendar-stage predictions across water states. New biomass production ends at wheat full ripeness or maize physiological maturity; green maize area can persist under the terminal senescence rate until harvest. Harvest is a separate management endpoint and does not substitute for an observed maturity date."
    ]
    _section(supplement,"S2.2.")["paragraphs"]=[
        "Green cover is f_c = 1−exp(−k_L LAI). Daily aboveground dry-matter production is ΔB = 10 R_s p_PAR f_c ε f_T f_W^γ f_N, where R_s is incident shortwave radiation, p_PAR = 0.45, ε is RUE per MJ of intercepted PAR, f_T is the temperature modifier, f_N is the prescribed nutrition factor and f_W is realized transpiration divided by remaining potential transpiration demand after wet-canopy evaporation. The factor 10 converts g m⁻² to kg ha⁻¹. PAR enters once [CITE:kiniry1989_rue]. The fitted empirical assimilation exponents are 1.85985 for wheat and 0.25000 for maize. They govern carbon response separately from leaf-expansion and leaf-survival sensitivity.",
        "SLA is linearly interpolated through DVS 0, 0.5, 1 and 2, with the DVS-2 value equal to DVS 1. New leaf dry matter acquires area using current SLA; established cohorts retain their birth area-to-mass ratio [CITE:pcse_leaf_dynamics]. Thermal senescence hazard h(DVS) is interpolated through DVS 0, 1, 1.4, 1.7 and 2, with a constant preflowering rate and increasing grain-filling rates [CITE:tan2020_stage_senescence]. Daily survival is exp[−h(DVS) max(T_mean−T_base,0)](1−q_s), with q_s = 0.025(1−f_W f_N). The terminal hazard continues at maize R6, whereas wheat BBCH 89 senesces remaining green leaves. LAI emerges from the cohort area balance; no maximum-LAI parameter or cap enters this formulation.",
        "Leaf allocation follows crop development, with separate early and late vegetative multipliers for maize and additional temperature, water-supply and nutrition limits on leaf expansion. Initial LAI is an emergence condition. Stage-specific SLA and senescence coefficients retain their inherited estimates and conditional trait priors [CITE:wofost_wheat_parameters|wofost_maize_parameters|apsim_ng_source_2026]; their values do not represent universal physiological bounds.",
        "Grain number is fixed at the first grain-set day as N_g = 0.1 B_f c_g, where B_f is simulated aboveground dry mass immediately before grain set and c_g is the grain-number coefficient. Grain set starts at wheat BBCH 61 and maize R1. Maximum grain dry mass is Q_g = 10 N_g m_g, with fixed m_g of 0.045 g grain⁻¹ for wheat and 0.300 g grain⁻¹ for maize [CITE:apsim_ng_source_2026]. Assimilate supply and development-dependent filling capacity constrain grain accumulation. Wheat remobilization transfers an explicit subset of stem mass to grain while conserving total aboveground mass. Live and dead leaves, stems and grain remain in the aboveground carbon pool; observed yield or harvest index does not initialize grain.",
        "Prescribed nutrition factors distinguish archived fertilized and unfertilized management classes. Wuqiao and regional simulations retain the fertilized class. These factors describe effective growth limitations; the model does not resolve a mechanistic nitrogen balance."
    ]
    _section(supplement,"S2.3.")["paragraphs"]=[
        "Potential transpiration and exposed-soil evaporation are T_p = K_T ET₀ f_c and E_p = K_E ET₀(1−f_c), with ET₀ supplied by AgERA5. Rainfall interception fills a canopy store of capacity 1 mm × f_c. Wet-canopy evaporation reduces the transpiration demand remaining within each outer water step. Actual ET is T_a + E_s + E_c, the sum of transpiration, soil evaporation and canopy evaporation. Wuqiao residue cover is unmeasured and its exposed-soil boundary is retained; prescribed organic mulch, where supplied, reduces potential soil evaporation using the FAO-56 cover approximation [CITE:allen1998_fao56].",
        "Two-stage evaporation depends on actual surface-layer storage. Total evaporable water is TEW = (θ_FC−θ_AD) Δz₁ and readily evaporable water is REW = 0.30 TEW. Surface depletion is D_e = (θ_FC−θ₁) Δz₁. The evaporation reduction is K_r = min[1,max(0,(TEW−D_e)/(TEW−REW))], with actual extraction bounded by storage above air dry. Ponded water evaporates before soil storage. This storage closure follows the two-stage drying principle [CITE:allen1998_fao56] without resolving a complete dual-crop-coefficient surface balance.",
        "Root activity integrates an exponential depth distribution over rooted parts of each layer and is normalized over the represented rooted volume. Local depletion stress is s_i = min[1,max(0,W_i−θ_WP,i Δz_i)/((1−p)(θ_FC,i−θ_WP,i) Δz_i)], where p is the fitted readily available water fraction. Base uptake combines root activity, local stress, atmospheric demand and the daily extraction coefficient; accessible storage and extraction capacity bound each contribution. The compensation fraction allocates a bounded part of unmet demand to rooted layers whose stress factors equal or exceed the activity-weighted mean. Total uptake remains bounded by demand times the best rooted-layer stress factor and by available extraction capacity. Compensation redistributes demand and does not eliminate uniformly dry-profile stress.",
        "Effective saturation is S_e = (θ−θ_AD)/(θ_SAT−θ_AD). With m = 1−1/n, the retention relation is S_e = [1+(α|h|)^n]^−m [CITE:vangenuchten1980], and conductivity is K = K_sat S_e^(1/2)[1−(1−S_e^(1/m))^m]^2 [CITE:mualem1976|vangenuchten1980]. Adjacent half-layer saturated resistances define K_sat,face = d/[Δz_i/(2K_sat,i)+Δz_(i+1)/(2K_sat,i+1)], where d = (Δz_i+Δz_(i+1))/2. Relative-arithmetic face mobility is r_face = [Δz_(i+1) r_i + Δz_i r_(i+1)]/[Δz_i+Δz_(i+1)], with r_i = K_i/K_sat,i. Thus K_face = K_sat,face r_face. This interface approximation permits a wet layer to supply a dry neighbor while retaining the resistance of low-conductivity material.",
        "Signed interface flux is q_i = K_face[1+(h_i−h_(i+1))/d], positive downward. Transfers have equal and opposite contributions in neighboring layer balances and are bounded by donor storage and receiving pore capacity. Twenty-four outer steps partition daily forcing, evaporation and uptake; adaptive internal transport steps limit net θ increments to 0.005. The free-drainage bottom flux equals the current unsaturated conductivity under unit hydraulic gradient. Upward redistribution is an internal profile flux, with no external capillary-rise supply. This bounded explicit layered approximation does not resolve the full implicit Richards system or saturated positive pressure.",
        "For a field of profile storage S, the accounting identity is ΔS = P + I − ET_a − D − Q. Applied irrigation I is a field-boundary input. Flood-irrigation scenarios wet the entire field, with application, drift and conveyance losses set to zero at that boundary. Drainage D leaves the modeled profile and is distinct from water reaching an aquifer after deeper vadose-zone transport [CITE:wu2023_vadose_zone]."
    ]
    _section(supplement,"S2.4.")["paragraphs"]=[
        "Saxton–Rawls equations use normalized sand and clay fractions and organic matter percent [CITE:saxton2006_hydraulics]. SoilGrids carbon in dg kg⁻¹ is converted to organic matter percent by 1.724 × SOC/100; bulk density in cg cm⁻³ is divided by 100; coarse fragments in permille are divided by 1,000 [CITE:soilgrids2020_dataset]. Regional porosity uses 1−BD/2.65, with prescribed density and gravel adjustments. Air-dry water content retains its declared prior. Field-capacity, wilting-point and saturation ordering is checked before simulation; unavailable complete profiles remain excluded under the spatial-source rule.",
        "The van Genuchten α and n values are conditional hydraulic priors reconstructed from supplied field-capacity and wilting-point endpoints at pressure heads of −3,300 and −150,000 mm, with θ_AD as residual water and θ_SAT as saturated content. Endpoint reconstruction preserves those storage thresholds rather than fitting curves to crop outcomes. Neither depth-resolved Wuqiao retention measurements nor exact seasonal initial layer distributions are available. The separate Wuqiao particle-density prior is 2.75 g cm⁻³.",
        "Table S3 contains effective shared crop values after applying the frozen fit multipliers. Regional and Wuqiao inputs use identical shared cards within each crop. Maximum root depth is limited by the supplied profile depth. Stage-specific SLA and senescence, fixed grain mass and inherited growth coefficients remain distinct from the eight water coefficients fitted in the current run. Near-zero root-density decay yields approximately uniform activity over represented rooted volume; it does not identify an observed root-mass profile.",
        f"The 164 selected station and field trajectories conserve daily water and aboveground carbon, with maximum absolute residuals of {water_residual} mm and {carbon_residual} kg ha⁻¹. Exact inputs, observation identities, frozen parameters, source snapshots and SHA-256 provenance accompany the predictions. Conservation establishes arithmetic consistency; hydraulic and crop-response accuracy depend on the conditional observations and retrospective field test."
    ]
    table = _table(supplement,"S3")
    table.update(table="tables/shared_crop_parameters.csv",
        table_note="Effective shared crop parameters from the selected current cards. RUE uses intercepted PAR with PAR fraction 0.45. DVS-2 SLA equals its DVS-1 value; senescence is a thermal hazard with a separate daily stress loss. Wheat BBCH 89 terminates remaining green leaf area; maize retains its terminal hazard at R6. Grain mass is fixed. Water coefficients are estimated from Wuqiao 2016–2018; growth coefficients retain inherited multisite estimates. Root depth is limited by profile depth. Dashes denote crop-specific processes. No maximum-LAI parameter is fitted.",
        precision={"Wheat":6,"Maize":6})
    _section(supplement,"S4.")["paragraphs"]=[
        "Annual Wuqiao grain targets comprise sixteen treatment-year means per crop in 2016–2019, digitized with standard errors from the primary Supplementary Figure S2 [CITE:yang2024_precipitation]. Grain retains its 13% moisture basis in comparisons. The twelve 2016–2018 means per crop enter calibration after a single multiplication by 0.87 to dry matter. The four 2019 means remain outside fitting and selection but had been inspected during earlier development. Four-year means summarize the same years and supply no additional independent targets.",
        "Annual grain calibration RMSE is 1.249 t ha⁻¹ for wheat and 2.267 t ha⁻¹ for maize; retrospective-testing RMSE is 3.161 and 1.700 t ha⁻¹ at 13% moisture. Within-year W1–W3 minus W0 grain-contrast RMSE is 0.519 and 1.669 t ha⁻¹ in calibration and 1.316 and 1.323 t ha⁻¹ in 2019 (Table S5; Figure S2). Maize treatment labels retain preceding wheat management because maize irrigation is identical within each year [CITE:yang2024_precipitation].",
        "All printed seasonal ET targets remain eligible after matching the 2016 maize rainfall period. Maize-season rainfall of 499.1 mm excludes the 100.5 mm during wheat from the 599.6-mm rotation total. Reconstructed balance residuals for W0–W3 are +0.1, +0.1, −0.9 and +0.1 mm. Source ET and depletion values remain unchanged. Storage-depletion diagnostics share the source ET balance and its unconfirmed exact sampling dates; they are conditional accounting comparisons rather than independent soil-water validation."
    ]
    table = _table(supplement,"S5")
    table.update(table="tables/supplement_annual_yield_evaluation.csv",
        table_caption="Table S5. Wuqiao annual grain and within-year treatment contrasts by fitting and testing partition.",
        table_note="Grain is at 13% moisture. Calibration comprises 2016–2018 and retrospective testing comprises 2019. Source means and standard errors are digitized from Supplementary Figure S2 [CITE:yang2024_precipitation], with pixel tolerances near 0.043 t ha⁻¹ for wheat and 0.056 for maize. Contrasts compare W1–W3 with same-year W0. Four-year means overlap these annual targets; maize contrasts retain preceding wheat histories.")
    figure = _figure(supplement,"S2")
    figure["figure"]="../calibration/figures/Field_grain_yield.png"
    figure["caption"]=("Figure S2. Annual Wuqiao grain at 13% moisture under the selected current parameters. "
        "Panels (a–d) show wheat and (e–h) maize in 2016–2019. Black points and error bars are published "
        "means ± standard error of three replicates; blue squares show current predictions. The 2016–2018 "
        "treatment means contribute to water calibration after conversion to dry grain; 2019 is retrospective "
        "testing, excluded from fitting and selection. Maize irrigation is identical within each year and "
        "labels retain preceding wheat management. Source means and SE are digitized from Supplementary "
        "Figure S2 of the primary experiment [CITE:yang2024_precipitation].")
    _section(supplement,"S9.")["paragraphs"]=[
        "Current seasonal curves use the selected frozen parameters and all sites with eligible observations for each variable. Observation and prediction channels share windows, case identities and aggregation weights. Bins span 15 days for wheat and 7 days for maize; site-year contributions and populations can change among bins. Standard errors describe variation across site-year means and are omitted for single-year bins. Missing bins remain gaps. Unbinned records determine performance scores (Figures S7–S12).",
        "The original station calibration partitions retain earlier growth-fitting history; all current station comparisons are conditional on unconfirmed complete irrigation management. Station observations have no weight in the latest water fit. Yucheng has no eligible in-season biomass evaluation observations, while its harvest-biomass endpoints remain separately available. Figure S6 shows dry-grain and aboveground harvest means with quadrat SE. Six samples within a crop-year describe sampling variation, and the duplicated 2012 and 2017 maize groups remain excluded. Exact case availability, site-specific metrics, bin contributions and harvest source rows accompany the figures."
    ]
    for number, stem in [("S6","current_Figure_S6_Yucheng_harvest_comparisons"),
                         ("S7","current_Figure_S7_wheat_lai_seasonal_curves"),
                         ("S8","current_Figure_S8_wheat_biomass_seasonal_curves"),
                         ("S9","current_Figure_S9_wheat_et_seasonal_curves"),
                         ("S10","current_Figure_S10_maize_lai_seasonal_curves"),
                         ("S11","current_Figure_S11_maize_biomass_seasonal_curves"),
                         ("S12","current_Figure_S12_maize_et_seasonal_curves")]:
        _bind_figure(supplement,number,stem)

    discussion = _section(article,"4.5.")
    remainder = discussion["paragraphs"][2:]
    discussion["paragraphs"]=[
        "The crop formulation links stage-specific leaf traits, radiation interception, carbon supply and grain formation to water availability. Intercepted-PAR RUE [CITE:kiniry1989_rue], cohort-specific SLA and thermal senescence constrain canopy carbon–area relationships, while fixed individual grain mass separates grain-number estimation from a second multiplicative sink coefficient (Table S3). The assimilation stress exponent and root uptake closure determine how water shortage affects production. Wheat transpiration formulations can differ substantially among crop models [CITE:cammarano2016_water_use]; absolute canopy or yield agreement alone therefore supplies incomplete evidence about the marginal grain response to irrigation. Near-bound root decay, compensation and maize evaporation and assimilation coefficients remain empirical conditional estimates rather than uniquely identified physiological traits.",
        "Whole site-years and balanced site contributions preserve the multisite basis of inherited growth estimates, but incomplete irrigation histories limit current station growth and ET comparisons. Wuqiao 2016–2018 constrains the fitted water response, whereas 2019 provides a single retrospective test year under observed total storage and assumed layer distribution and hydraulics (Figures 4–5). Wheat testing grain nRMSE of 83.27% and ET-response error of 116.37 mm, together with negative maize ET NSE and a 52.74-mm response error, limit confidence in regional irrigation-response transfer. High grain correlations coexist with negative testing NSE because correlation does not measure absolute agreement. Numerical closure verifies conservation without resolving those response errors. Depth-resolved soil water and complete dated management would strengthen identification of redistribution and uptake; assimilation of canopy and soil-moisture observations additionally requires retrieval-error and observation-operator treatment [CITE:ines2013_soil_lai_assimilation]."
    ] + remainder
    return article, supplement
