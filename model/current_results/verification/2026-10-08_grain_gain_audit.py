"""Independent arithmetic audit; only this audit's JSON/CSV outputs are written.

No crop simulation or calibration is run. Frozen source paths are resolved inside
current_results; original absolute paths in provenance metadata are never opened.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
from scipy.optimize import linprog

ROOT = Path(__file__).resolve().parents[1]
REG = ROOT / "regional"
CAL = ROOT / "calibration"
OUT = ROOT / "verification/2026-10-08_grain_gain_audit.json"
CSV = ROOT / "publication/tables/grain_gain_reconciliation.csv"
CHECKS = {}
PROVENANCE = {}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def record(path):
    path = Path(path)
    PROVENANCE[str(path.relative_to(ROOT))] = {
        "sha256": sha(path), "bytes": path.stat().st_size
    }
    return path


def read_csv(relative, root=REG):
    return pd.read_csv(record(root / relative))


def read_json(relative, root=REG):
    return json.loads(record(root / relative).read_text())


def check(name, passed, **evidence):
    CHECKS[name] = {"passed": bool(passed), **evidence}


def f(value):
    return float(value)


rotation = read_csv("predictions/rotation_summaries.csv")
seasons = read_csv("predictions/all_season_summaries.csv")
allocation = read_csv("parameters/frozen_training_allocation.csv")
annual = read_csv("tables/regional_policy_annual_results.csv")
mapping = read_csv("data/all_source_cell_mapping.csv")
reps = read_csv("data/representative_cells.csv")
excluded = read_csv("data/excluded_source_cells.csv")
decisions = read_csv("data/annual_policy_decisions.csv")
freeze = read_json("verification/allocation_freeze.json")
protocol = read_json("parameters/frozen_protocol.json")
selected = read_json("parameters/selected_model.json")
cal_selected = read_json("parameters/frozen_model.json", CAL)
transfer = read_json("verification/parameter_transfer.json")
cards = read_json("parameters/used_crop_parameters.json")
hydrology = read_json("parameters/used_hydrology_by_crop.json")
manifest = read_json("verification/file_manifest.json")
input_manifest = read_json("verification/input_manifest.json")
native_identity = read_json("verification/native_source_identity.json")
native_model_path = record(REG / "source_snapshots/native_process/research/ncp_irrigation/model.py")
native_model_text = native_model_path.read_text()
driver_path = record(REG / "source_snapshots/regional_recalculation.py")
driver_text = driver_path.read_text()

seal_failures = []
for relative, metadata in manifest.items():
    path = REG / relative
    if not path.exists() or path.stat().st_size != metadata["bytes"] or sha(path) != metadata["sha256"]:
        seal_failures.append(relative)
check("full_regional_seal", not seal_failures, n_files=len(manifest), failures=seal_failures,
      expected_bytes=sum(x["bytes"] for x in manifest.values()))
input_failures = [x["snapshot"] for x in input_manifest
                  if not (REG / x["snapshot"]).exists() or sha(REG / x["snapshot"]) != x["sha256"]]
fingerprint = hashlib.sha256(json.dumps(input_manifest, sort_keys=True).encode()).hexdigest()
check("frozen_source_snapshots", not input_failures and fingerprint == protocol["source_fingerprint"],
      n_snapshots=len(input_manifest), failures=input_failures, source_fingerprint=fingerprint,
      original_absolute_paths_accessed=False)
native_reg = REG / "source_snapshots/native_process"
native_cal = CAL / "source_snapshots/native"
reg_files = {str(p.relative_to(native_reg)): sha(p) for p in native_reg.rglob("*.py")}
cal_files = {str(p.relative_to(native_cal)): sha(p) for p in native_cal.rglob("*.py")}
check("full_native_engine_identity", reg_files == cal_files == native_identity["files"],
      regional_python_files=len(reg_files), calibration_python_files=len(cal_files),
      native_file_identity_sha256=hashlib.sha256(json.dumps(reg_files, sort_keys=True).encode()).hexdigest())
check("current_calibration_selection", selected == cal_selected and selected["selected_version"] == "management_refit"
      and not selected["testing_used_for_selection"], selected_version=selected["selected_version"],
      regional_selected_sha256=sha(REG / "parameters/selected_model.json"),
      calibration_selected_sha256=sha(CAL / "parameters/frozen_model.json"),
      field_calibration_years=selected["field_calibration_years"], field_testing_years=selected["field_testing_years"])
parameter_evidence = {}
for crop in ["wheat", "maize"]:
    filename = f"yang2024_{crop}_2015_2016_W0_crop.json"
    snapshot = read_json(f"source_snapshots/selected_resolved_field/{filename}")["parameters"]
    resolved_paths = sorted((CAL / "inputs/resolved/management_refit/field").glob(f"yang2024_{crop}_*_crop.json"))
    all_parameters = [json.loads(record(path).read_text())["parameters"] for path in resolved_paths]
    used = {"crop": cards[crop], "hydrology": hydrology[crop]}
    canonical = hashlib.sha256(json.dumps(used, sort_keys=True).encode()).hexdigest()
    exact = (used == snapshot == transfer["effective_parameters"][crop]
             and all(value == used for value in all_parameters)
             and canonical == transfer["canonical_effective_parameters_sha256"][crop])
    parameter_evidence[crop] = {"exact": exact, "n_field_resolved_cards": len(resolved_paths),
                                "canonical_effective_parameters_sha256": canonical}
check("effective_parameter_transfer", all(x["exact"] and x["n_field_resolved_cards"] == 16
                                          for x in parameter_evidence.values()),
      crop_evidence=parameter_evidence, multipliers_reapplied=False,
      diagnostic_parameter_version_label="unvalidated-engineering-priors",
      diagnostic_label_origin='model.py summary uses parameters.get("version", "unvalidated-engineering-priors"); '
                              'regional segment parameters supply crop and hydrology without a top-level version')
check("allocation_freeze_hashes", sha(REG / "parameters/frozen_training_allocation.csv") == freeze["allocation_sha256"]
      and sha(REG / "data/annual_policy_decisions.csv") == freeze["decisions_sha256"]
      and not freeze["selection_uses_testing_outcomes"],
      allocation_sha256=freeze["allocation_sha256"], decisions_sha256=freeze["decisions_sha256"],
      adaptive_decision_policies=sorted(decisions.policy.unique().tolist()),
      fixed_targeted_claim_uses_adaptive_decisions=False)

keys = ["representative_id", "fraction", "harvest_year"]
years = list(range(1997, 2026))
fractions = [0., .25, .5, .75, 1.]
check("rotation_grain_keys", not rotation.duplicated(keys).any() and len(rotation) == 32 * 5 * 29
      and sorted(rotation.harvest_year.unique()) == years,
      n_rows=len(rotation), unique_representatives=int(rotation.representative_id.nunique()),
      years=years, fractions=sorted(rotation.fraction.unique().tolist()))
crop_seasons = seasons[seasons.crop.isin(["wheat", "maize"])].copy()
pair_sizes = crop_seasons.groupby(keys).size()
crop_counts = crop_seasons.groupby(keys).crop.nunique()
pair_grain = crop_seasons.groupby(keys).yield_kg_ha.sum().rename("recomputed_crop_grain")
paired = rotation.merge(pair_grain, on=keys, validate="one_to_one")
crop_error = f((paired.yield_kg_ha - paired.recomputed_crop_grain).abs().max())
check("crop_and_year_counting", pair_sizes.eq(2).all() and crop_counts.eq(2).all() and crop_error < 1e-8
      and seasons.loc[seasons.crop.eq("fallow"), "yield_kg_ha"].eq(0).all()
      and not crop_seasons.duplicated(keys + ["crop"]).any(),
      n_crop_pairs=len(pair_sizes), crops_per_pair=sorted(crop_counts.unique().tolist()),
      crop_records_per_pair=sorted(pair_sizes.unique().tolist()),
      maximum_rotation_vs_crop_sum_error_kg_ha=crop_error,
      same_physical_hectare_supports_two_successive_crops=True,
      physical_area_multiplied_by_two=False, quota_options_summed_as_independent_areas=False)
AREA = f(mapping.used_area_ha.sum())
source_areas = mapping.groupby("representative_id").used_area_ha.sum()
rep_areas = reps.set_index("representative_id").represented_area_ha
mapping_error = f((source_areas - rep_areas).abs().max())
rotation_area_error = f((rotation.represented_area_ha - rotation.representative_id.map(source_areas)).abs().max())
check("source_cell_area_weights", not mapping.zone_id.duplicated().any() and mapping_error < 1e-7
      and rotation_area_error < 1e-7 and np.allclose(mapping.used_area_ha, mapping.mapped_rotation_area_ha)
      and mapping.used_area_ha.gt(0).all(),
      source_cells=len(mapping), representatives=len(reps), mapped_rotation_area_ha=AREA,
      represented_area_ha=f(reps.represented_area_ha.sum()),
      whole_classified_cell_area_ha=f(mapping.whole_classified_cell_area_ha.sum()),
      excluded_cells=len(excluded), excluded_mapped_area_ha=f(excluded.mapped_rotation_area_ha.sum()),
      maximum_representative_source_area_error_ha=mapping_error,
      maximum_rotation_source_area_error_ha=rotation_area_error,
      area_basis=mapping.area_basis.unique().tolist(), static_source_years=sorted(mapping.year.unique().tolist()))

# Reconstruct all policies independently from sealed response rows and shares.
records = []
allocation_checks = []
for cut, chosen in allocation.groupby("reduction_fraction"):
    share_sum = chosen.groupby("representative_id").area_share.sum()
    area_error = (chosen.allocated_area_ha - chosen.area_share * chosen.representative_id.map(source_areas)).abs().max()
    allocation_checks.append(share_sum.sub(1).abs().max() < 1e-9 and area_error < 1e-7
                             and chosen.area_share.between(-1e-12, 1 + 1e-12).all())
check("allocation_areas", all(allocation_checks), reduction_fractions=sorted(allocation.reduction_fraction.unique().tolist()))
for year, group in rotation.groupby("harvest_year"):
    for policy in annual.loc[annual.harvest_year.eq(year), "policy"]:
        if policy == "conventional":
            active = group[group.fraction.eq(1.)].copy()
            active["weight_ha"] = active.representative_id.map(source_areas)
        elif policy.startswith("uniform_"):
            cut = int(policy.split("_")[1].replace("pct", "")) / 100
            active = group[group.fraction.eq(1 - cut)].copy()
            active["weight_ha"] = active.representative_id.map(source_areas)
        else:
            cut = int(policy.split("_")[1].replace("pct", "")) / 100
            active = group.merge(allocation[allocation.reduction_fraction.eq(cut)],
                                 on=["representative_id", "fraction"], validate="one_to_one")
            active["weight_ha"] = active.representative_id.map(source_areas) * active.area_share
        records.append({"harvest_year": int(year), "policy": policy,
                        "grain_production_t": f((active.weight_ha * active.yield_kg_ha).sum() / 1000),
                        "field_irrigation_m3": f((active.weight_ha * active.irrigation_mm).sum() * 10),
                        "mapped_rotation_area_ha": f(active.weight_ha.sum())})
recomputed = pd.DataFrame(records)
compare = recomputed.merge(annual, on=["harvest_year", "policy"], suffixes=("_audit", "_saved"), validate="one_to_one")
differences = {col: f((compare[col + "_audit"] - compare[col + "_saved"]).abs().max())
               for col in ["grain_production_t", "field_irrigation_m3", "mapped_rotation_area_ha"]}
check("all_annual_policy_arithmetic", len(compare) == 203 and max(differences.values()) < 1e-4,
      n_policy_years=len(compare), maximum_absolute_errors=differences,
      grain_formula="sum(source_cell_mapped_area_ha * frozen_option_area_share * crop_pair_dry_grain_kg_ha) / 1000",
      water_formula="sum(source_cell_mapped_area_ha * frozen_option_area_share * irrigation_mm) * 10")
test = recomputed[recomputed.harvest_year.between(2014, 2025)].copy()
test_wide = test.pivot(index="harvest_year", columns="policy", values="grain_production_t")
test_water = test.pivot(index="harvest_year", columns="policy", values="field_irrigation_m3")
gains = test_wide.targeted_50pct - test_wide.uniform_50pct
means = test_wide.mean()
check("same_annual_field_water", (test_water.targeted_50pct - test_water.uniform_50pct).abs().max() < 1e-4
      and np.allclose(test_water.uniform_50pct, .5 * test_water.conventional, rtol=1e-12, atol=1e-4),
      maximum_annual_targeted_uniform_difference_m3=f((test_water.targeted_50pct - test_water.uniform_50pct).abs().max()),
      annual_50pct_field_irrigation_m3=f(test_water.uniform_50pct.mean()),
      field_irrigation_depth_mm=f(test_water.uniform_50pct.mean() / AREA / 10))

# Independently solve only the small allocation LP, using 1997–2013 responses.
training = rotation[rotation.harvest_year.between(1997, 2013)].groupby(["representative_id", "fraction"], as_index=False).yield_kg_ha.mean()
ids = sorted(source_areas.index)
production = training.pivot(index="representative_id", columns="fraction", values="yield_kg_ha").reindex(index=ids, columns=fractions).to_numpy()
weights = source_areas.reindex(ids).to_numpy()
n = len(ids)
eq = np.zeros((n + 1, n * 5))
for i in range(n):
    eq[i, i * 5:(i + 1) * 5] = 1.
eq[-1] = (weights[:, None] * np.asarray(fractions)[None, :] / AREA).ravel()
lp_evidence = []
for cut in [.25, .5, .75]:
    result = linprog(-(weights[:, None] * production / AREA).ravel(), A_eq=eq,
                     b_eq=np.r_[np.ones(n), 1 - cut], bounds=(0, 1), method="highs")
    shares = allocation[allocation.reduction_fraction.eq(cut)].pivot(index="representative_id", columns="fraction", values="area_share").reindex(index=ids, columns=fractions).to_numpy()
    frozen_objective = f(np.sum(weights[:, None] * production * shares) / AREA)
    gap = f(-result.fun - frozen_objective) if result.success else None
    lp_evidence.append({"reduction_fraction": cut, "solver_success": bool(result.success),
                        "frozen_training_yield_kg_ha": frozen_objective,
                        "training_optimum_minus_frozen_kg_ha": gap,
                        "area_weighted_quota_fraction": f(np.sum(weights[:, None] * shares * fractions) / AREA)})
check("allocation_matches_current_training_optimum", all(x["solver_success"] and abs(x["training_optimum_minus_frozen_kg_ha"]) < 1e-7 for x in lp_evidence),
      selection_years=list(range(1997, 2014)), testing_years_used=False, reductions=lp_evidence,
      crop_simulations_rerun=False)

crop_outputs = []
crop_conventional = {}
test_crops = crop_seasons[crop_seasons.harvest_year.between(2014, 2025)]
a50 = allocation[allocation.reduction_fraction.eq(.5)]
for crop, group in test_crops.groupby("crop"):
    full = group[group.fraction.eq(1.)]
    w = full.represented_area_ha
    hi = full.yield_kg_ha / full.biomass_kg_ha
    crop_conventional[crop] = {
        "dry_grain_t_ha": f(np.average(full.yield_kg_ha / 1000, weights=w)),
        "grain_13pct_moisture_t_ha": f(np.average(full.yield_kg_ha / 870, weights=w)),
        "aboveground_dry_biomass_t_ha": f(np.average(full.biomass_kg_ha / 1000, weights=w)),
        "mean_harvest_index_area_weighted": f(np.average(hi, weights=w)),
        "harvest_index_total_grain_over_total_biomass": f(np.sum(w * full.yield_kg_ha) / np.sum(w * full.biomass_kg_ha)),
        "grain_t_ha_min_max": [f(full.yield_kg_ha.min() / 1000), f(full.yield_kg_ha.max() / 1000)],
        "harvest_index_min_max": [f(hi.min()), f(hi.max())],
        "n_representative_years": len(full), "season_end_reasons": {str(k): int(v) for k, v in full.season_end_reason.value_counts().items()}}
    for policy in ["conventional", "uniform_50pct", "targeted_50pct"]:
        if policy == "targeted_50pct":
            g = group.merge(a50, on=["representative_id", "fraction"], validate="many_to_one")
            weight = g.representative_id.map(source_areas) * g.area_share
        else:
            g = group[group.fraction.eq(1. if policy == "conventional" else .5)]
            weight = g.representative_id.map(source_areas)
        production_t = f(np.sum(weight * g.yield_kg_ha) / 1000 / 12)
        crop_outputs.append({"crop": crop, "policy": policy, "mean_production_t_dry_per_year": production_t,
                             "mean_yield_t_ha_dry": production_t / AREA,
                             "mean_yield_t_ha_at_13pct_moisture": production_t / AREA / .87,
                             "mean_field_irrigation_mm": f(np.sum(weight * g.irrigation_field_mm) / AREA / 12)})
field = read_csv("predictions/field_comparisons.csv", CAL)
field = field[field.version.eq("management_refit")]
field_evidence = []
for crop, group in field[field.harvest_year.eq(2019)].groupby("crop"):
    obs = group.yield_13pct_kg_ha.to_numpy() / 1000
    pred = group.grain_13pct_kg_ha.to_numpy() / 1000
    field_evidence.append({"site": "Wuqiao", "crop": crop, "year": 2019, "n_treatment_means": len(group),
                           "unit": "t ha-1 at 13% moisture", "comparison": "retrospective testing",
                           "bias": f(np.mean(pred - obs)), "rmse": f(np.sqrt(np.mean((pred - obs) ** 2))),
                           "nse": f(1 - np.sum((pred - obs) ** 2) / np.sum((obs - obs.mean()) ** 2)),
                           "observed_mean": f(obs.mean()), "predicted_mean": f(pred.mean())})

GAIN = f(gains.mean())
summary = {
    "evaluation_years": list(range(2014, 2026)), "n_years": 12, "mapped_rotation_area_ha": AREA,
    "grain_mass_basis": "dry matter", "crop_basis": "successive wheat + maize harvests on the same mapped hectares",
    "conventional_mean_grain_Mt_per_year": f(means.conventional / 1e6),
    "uniform50_mean_grain_Mt_per_year": f(means.uniform_50pct / 1e6),
    "targeted50_mean_grain_Mt_per_year": f(means.targeted_50pct / 1e6),
    "targeted_minus_uniform50_Mt_per_year": GAIN / 1e6,
    "targeted_minus_uniform50_t_ha_per_rotation_year": GAIN / AREA,
    "targeted_minus_uniform50_percent_of_uniform50": 100 * GAIN / f(means.uniform_50pct),
    "uniform50_grain_retention_pct_of_conventional": 100 * f(means.uniform_50pct / means.conventional),
    "targeted50_grain_retention_pct_of_conventional": 100 * f(means.targeted_50pct / means.conventional),
    "targeted50_minus_conventional_Mt_per_year": f((means.targeted_50pct - means.conventional) / 1e6),
    "same_difference_at_13pct_moisture_Mt_per_year": GAIN / 1e6 / .87,
    "same_difference_at_13pct_moisture_t_ha_per_rotation_year": GAIN / AREA / .87,
    "minimum_maximum_annual_gain_Mt": [f(gains.min() / 1e6), f(gains.max() / 1e6)],
    "annual_gains_Mt": {str(year): f(value / 1e6) for year, value in gains.items()},
    "50pct_allocation_area_share_by_quota": {str(quota): f(area / AREA) for quota, area in a50.groupby("fraction").allocated_area_ha.sum().items()},
    "arithmetic_interpretation": "Targeted allocation retains more modeled dry grain than a uniform 50% irrigation cut at the same annual field-water budget; it still produces less grain than conventional full irrigation."
}
csv_rows = []
for row in crop_outputs:
    csv_rows.append({"period": "2014-2025", "quantity": "mean annual production and mapped-area yield",
                     "crop": row["crop"], "comparison": row["policy"], "mass_basis": "dry grain",
                     "area_ha": AREA, "production_Mt_per_year": row["mean_production_t_dry_per_year"] / 1e6,
                     "yield_t_ha_per_year": row["mean_yield_t_ha_dry"],
                     "equivalent_13pct_moisture_yield_t_ha": row["mean_yield_t_ha_at_13pct_moisture"],
                     "field_irrigation_mm": row["mean_field_irrigation_mm"], "relative_to_uniform50_pct": None})
for policy in ["conventional", "uniform_50pct", "targeted_50pct"]:
    p = f(means[policy])
    csv_rows.append({"period": "2014-2025", "quantity": "mean annual production and mapped-area yield",
                     "crop": "wheat+maize", "comparison": policy, "mass_basis": "dry grain",
                     "area_ha": AREA, "production_Mt_per_year": p / 1e6, "yield_t_ha_per_year": p / AREA,
                     "equivalent_13pct_moisture_yield_t_ha": p / AREA / .87,
                     "field_irrigation_mm": f(test_water[policy].mean() / AREA / 10),
                     "relative_to_uniform50_pct": None})
for crop in ["wheat", "maize", "wheat+maize"]:
    if crop == "wheat+maize":
        gain, uniform = GAIN, f(means.uniform_50pct)
    else:
        by_policy = {x["policy"]: x["mean_production_t_dry_per_year"] for x in crop_outputs if x["crop"] == crop}
        gain, uniform = by_policy["targeted_50pct"] - by_policy["uniform_50pct"], by_policy["uniform_50pct"]
    csv_rows.append({"period": "2014-2025", "quantity": "mean annual grain retained relative to equal-water uniform cut",
                     "crop": crop, "comparison": "targeted_50pct minus uniform_50pct", "mass_basis": "dry grain",
                     "area_ha": AREA, "production_Mt_per_year": gain / 1e6, "yield_t_ha_per_year": gain / AREA,
                     "equivalent_13pct_moisture_yield_t_ha": gain / AREA / .87,
                     "field_irrigation_mm": 0., "relative_to_uniform50_pct": 100 * gain / uniform})
CSV.parent.mkdir(parents=True, exist_ok=True)
pd.DataFrame(csv_rows).to_csv(CSV, index=False)
record(CSV)
record(Path(__file__))
status = all(x["passed"] for x in CHECKS.values())
audit = {
    "checked_at_utc": datetime.now(timezone.utc).isoformat(),
    "status": "arithmetic verified; model-transfer caveats remain" if status else "needs arithmetic or source revision",
    "all_arithmetic_and_source_checks_passed": status,
    "scope": {"full_regional_rerun": False, "recalibration": False, "sealed_sources_written": False,
               "historical_original_paths_opened": False, "new_artifacts": [str(OUT.relative_to(ROOT)), str(CSV.relative_to(ROOT))]},
    "checks": CHECKS, "numerical_reconciliation": summary,
    "crop_policy_reconciliation": crop_outputs, "conventional_crop_yields_and_harvest_indices": crop_conventional,
    "model_transfer_caveats": [
        {"finding": "Regional absolute production and allocation gains are conditional model scenarios, not independently validated regional outcomes.",
         "source": "regional/parameters/frozen_protocol.json", "independent_regional_validation": False},
        {"finding": "Allocation training uses 1997-2013 regional response years, whereas the crop-card field fit uses 2016-2018, within the 2014-2025 regional evaluation period. The allocation weather-year separation does not establish a wholly time-independent regional crop-model validation.",
         "source": "regional/parameters/frozen_protocol.json; regional/parameters/selected_model.json"},
        {"finding": "The model assumes the fertilized management class across all representative cells; the nutrition factor is 1 and no soil nitrogen cycle is simulated.",
         "source": "regional/source_snapshots/regional_recalculation.py; native_process/research/ncp_irrigation/growth.py",
         "nutrition_factor": {crop: cards[crop].get("nutrition_factor", 1.) * cards[crop].get("nutrition_by_management", {}).get("fertilized", 1.) for crop in ["wheat", "maize"]}},
        {"finding": "Mapped physical area uses static 2020 Class246/CPM correction across all scenario years and is not observed annual harvested area.",
         "source": "regional/data/all_source_cell_mapping.csv"},
        {"finding": "Uniform planting/harvest dates, fixed conventional irrigation of 300 mm wheat plus 80 mm maize, and a shared crop card are transferred throughout the region.",
         "source": "regional/source_snapshots/regional_recalculation.py"},
        {"finding": "The 2019 Wuqiao retrospective yield comparisons have positive mean biases and negative NSE for both crops; these directly document model error, without quantifying the regional bias or correcting allocation gains.",
         "source": "calibration/predictions/field_comparisons.csv", "recomputed_evidence": field_evidence},
        {"finding": "The default summary parameter-version label does not identify the effective selected calibration; the exact cards, native source, provenance and frozen protocol do.",
         "source": "regional/source_snapshots/native_process/research/ncp_irrigation/model.py"}
    ],
    "unit_reconciliation": {"kg_to_t": "divide kg by 1000 once", "grain_at_13pct_moisture": "dry grain divided by 0.87",
                            "water_mm_ha_to_m3": "multiply by 10", "moisture_conversion_in_regional_claim": False,
                            "region_total_is_not_per_hectare_yield": True,
                            "claimed_7Mt_is_not_7t_per_ha": True},
    "input_and_output_provenance": PROVENANCE,
    "unverified": ["Independent regional absolute-grain validation", "Realized implementation benefits", "Regional structural/model/area uncertainty"]
}
OUT.write_text(json.dumps(audit, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
print(json.dumps({"all_checks_passed": status, "n_checks": len(CHECKS),
                  "failed_checks": [name for name, value in CHECKS.items() if not value["passed"]],
                  "audit": str(OUT), "reconciliation_csv": str(CSV),
                  "summary": summary}, ensure_ascii=False, indent=2))
sys.exit(0 if status else 1)
