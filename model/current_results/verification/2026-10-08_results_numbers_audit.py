"""Read-only audit of every quantitative Results/Conclusions claim and table.

Uses raw selected comparisons and sealed regional responses; no model is run.
Stores manuscript snapshots by content hash and writes only verification files.
"""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import io
import json
import re
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
VER = ROOT / "verification"
PAPER = ROOT / "publication/analysis_source/manuscript_blocks.json"
OUT = VER / "2026-10-08_results_numbers_audit.json"
SOURCES, EXPECTED, CLAIMS, LABELS, UNMAPPED, CROSSCHECKS = {}, {}, [], [], [], []


def read(path, kind="csv"):
    path = ROOT / path
    content = path.read_bytes()
    SOURCES[str(path.relative_to(ROOT))] = {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
    if kind == "json": return json.loads(content)
    if kind == "text": return content.decode()
    return pd.read_csv(io.BytesIO(content))


def e(name, value, unit, source, calculation="", selector=None):
    EXPECTED[name] = {"value": float(value), "unit": unit, "source": source,
                      "calculation": calculation, "selector": selector}
    return name


def score(o, p, weights=None):
    o, p = np.asarray(o, float), np.asarray(p, float)
    w = np.ones(len(o))/len(o) if weights is None else np.asarray(weights, float)
    om, pm = np.sum(w*o), np.sum(w*p)
    mse = np.sum(w*(p-o)**2)
    ov, pv = np.sum(w*(o-om)**2), np.sum(w*(p-pm)**2)
    return dict(n=len(o), rmse=np.sqrt(mse), bias=np.sum(w*(p-o)), nrmse_percent=100*np.sqrt(mse)/om,
                nse=1-mse/ov, r_squared=np.sum(w*(o-om)*(p-pm))**2/(ov*pv),
                observed_mean=om, predicted_mean=pm)


def station_weights(g):
    # Construct hierarchical weights without importing the publication helper.
    result = np.zeros(len(g))
    g = g.reset_index(drop=True)
    for _, site in g.groupby("site"):
        years = list(site.groupby("source_group_id"))
        for _, year in years:
            cases = list(year.groupby("case_id"))
            for _, case in cases:
                result[case.index] = 1/g.site.nunique()/len(years)/len(cases)/len(case)
    assert abs(result.sum()-1) < 1e-12
    return result


def claim(loc, text, token, name, tolerance=None, category="numeric claim", span=None):
    numeric_words = dict(one=1, once=1, single=1, two=2, both=2, three=3, four=4, five=5,
                         eight=8, nine=9, twelve=12, sixteen=16)
    if token.lower() in numeric_words:
        actual, default_tol = float(numeric_words[token.lower()]), 0.
    else:
        t = token.replace(",", "").replace("−", "-")
        actual = float(t)
        default_tol = .5 * 10**(-len(t.split(".")[1])) if "." in t else 0.
    tolerance = default_tol if tolerance is None else tolerance
    source = EXPECTED[name]
    error = actual-source["value"]
    CLAIMS.append(dict(location=loc, text=text, claimed_token=token, claimed_value=actual,
                       expected_key=name, expected_value=source["value"], tolerance=float(tolerance),
                       absolute_error=abs(error), passed=bool(abs(error) <= tolerance+1e-10),
                       unit=source["unit"], source=source["source"], source_selector=source["selector"],
                       calculation=source["calculation"], category=category, span=span))


station_path = "calibration/predictions/station_comparisons.csv"
station = read(station_path).query("version == 'management_refit'").copy()
field_path = "calibration/predictions/field_comparisons.csv"
field = read(field_path).query("version == 'management_refit'").copy()
frozen_path = "calibration/parameters/frozen_model.json"
frozen = read(frozen_path, "json")
protocol_path = "regional/parameters/frozen_protocol.json"
protocol = read(protocol_path, "json")
rotation_path = "regional/predictions/rotation_summaries.csv"
rotation = read(rotation_path)
allocation_path = "regional/parameters/frozen_training_allocation.csv"
allocation = read(allocation_path)
mapping_path = "regional/data/all_source_cell_mapping.csv"
mapping = read(mapping_path)
membership_path = "regional/data/class_memberships.csv"
membership = read(membership_path)
adaptive_path = "regional/adaptive/rotation_predictions.csv"
adaptive = read(adaptive_path)
decision_path = "regional/data/annual_policy_decisions.csv"
decision = read(decision_path)
rule_path = "regional/parameters/frozen_class_rules.json"
rules = read(rule_path, "json")
fixed_class_path = "regional/data/class_fixed_quota_predictions.csv"
fixed_class = read(fixed_class_path)
read("analysis_source/current_crop_publication.py", "text")
read("publication/analysis_source/word_documents.py", "text")
profiles_path = "regional/data/used_hydraulic_profiles.json"
profiles = read(profiles_path, "json")
transcription_path = "publication/literature/treatment_sources/published_numeric_transcriptions.json"
transcription = read(transcription_path, "json")
grain_definition_path = "publication/literature/treatment_sources/yang2024_supplement_extracted.json"
grain_definition = read(grain_definition_path, "json")
grain_definition_text = json.dumps(grain_definition, ensure_ascii=False)
assert "Moisture of wheat and maize grain is 13%" in grain_definition_text
CROSSCHECKS.append(dict(name="all five sites and intact whole site-year partitions",
                       passed=bool(station.site.nunique() == 5 and not station.observation_id.duplicated().any()
                                   and station.groupby(["site", "source_group_id"]).split.nunique().eq(1).all()),
                       source=station_path))
CROSSCHECKS.append(dict(name="per-crop counts quantified jointly in captions and narrative",
                       passed=all(len(field[field.crop.eq(c)]) == 16
                                  and len(field[field.crop.eq(c)&field.split.eq("calibration")]) == 12
                                  and len(field[field.crop.eq(c)&field.split.eq("validation")]) == 4
                                  and frozen["candidates"]["management_refit"][c]["free_parameters"] == 8
                                  for c in ["wheat", "maize"]),
                       sources=[field_path, frozen_path], counts_per_crop=dict(total=16,calibration=12,testing=4,free_water_parameters=8)))

e("count.station", len(station), "observations", station_path, "selected version row count")
e("count.sites", station.site.nunique(), "sites", station_path, "distinct eligible site count")
for split in ["calibration", "validation"]:
    e("count.nonET."+split, len(station[(station.split == split)&(station.variable != "et")]), "observations", station_path)
e("count.ET", len(station[station.variable == "et"]), "observations", station_path)
for (crop, split, var), g in station.groupby(["crop", "split", "variable"]):
    scale = .001 if var in ["yield", "biomass", "harvest_biomass"] else 1.
    unit = "t ha-1 dry matter" if scale == .001 else "mm d-1" if var == "et" else "m2 m-2"
    metrics = score(g.value*scale, g.predicted*scale, station_weights(g))
    for metric, value in metrics.items():
        e(f"station.{crop}.{split}.{var}.{metric}", value, "count" if metric == "n" else "dimensionless" if metric in ["nse", "r_squared"] else "%" if metric == "nrmse_percent" else unit,
          station_path, "equal sites, site-years within sites, cases within site-years, records within cases",
          dict(crop=crop, split=split, variable=var, version="management_refit"))
    e(f"station.{crop}.{split}.{var}.years", g.source_group_id.nunique(), "site-years", station_path,
      "distinct source_group_id", dict(crop=crop, split=split, variable=var))
et = station[station.variable == "et"]
matched = et.groupby(["crop", "split", "site", "source_group_id", "case_id"])[["value", "predicted"]].sum().reset_index()
for (crop, split), g in matched.groupby(["crop", "split"]):
    for metric, value in score(g.value, g.predicted, station_weights(g)).items():
        e(f"matched.{crop}.{split}.{metric}", value, "seasons" if metric == "n" else "dimensionless" if metric in ["nse", "r_squared"] else "mm",
          station_path, "sum exact matched observed days within cases, then hierarchical site/year/case weights", dict(crop=crop, split=split))

field_variables = {"et": ("observed_et_mm", "predicted_et_mm", 1., "mm"),
                   "grain": ("yield_13pct_kg_ha", "grain_13pct_kg_ha", .001, "t ha-1 at 13% grain moisture"),
                   "biomass": ("observed_biomass_kg_ha", "predicted_biomass_kg_ha", .001, "t ha-1 dry matter")}
for (crop, split), g in field.groupby(["crop", "split"]):
    for variable, (obs, pred, scale, unit) in field_variables.items():
        for metric, value in score(g[obs]*scale, g[pred]*scale).items():
            e(f"field.{crop}.{split}.{variable}.{metric}", value, "treatment means" if metric == "n" else "dimensionless" if metric in ["nse", "r_squared"] else "%" if metric == "nrmse_percent" else unit,
              field_path, "equal treatment-year means; source/model grain columns are already at 13% moisture", dict(crop=crop, split=split, version="management_refit"))
    e(f"field.{crop}.{split}.years", g.harvest_year.nunique(), "years", field_path)
for crop, g in field.groupby("crop"):
    for variable, (obs, pred, scale, unit) in field_variables.items():
        if variable == "biomass": continue
        pivot = g.pivot(index=["split", "harvest_year"], columns="treatment", values=[obs, pred])
        treatment_options = ["W3"] if variable == "et" else ["W1", "W2", "W3"]
        for split in ["calibration", "validation"]:
            errors = np.concatenate([((pivot[pred][t]-pivot[pred].W0)-(pivot[obs][t]-pivot[obs].W0)).loc[split].to_numpy()*scale for t in treatment_options])
            e(f"contrast.{crop}.{split}.{variable}.rmse", np.sqrt(np.mean(errors**2)), unit, field_path,
              "within-year W3-W0 ET or W1/W2/W3-W0 grain contrast errors", dict(crop=crop, split=split, treatments=treatment_options))
    e("count.field."+crop, len(g), "treatment seasons", field_path)
    e("count.treatments."+crop, g.treatment.nunique(), "treatments per crop-year", field_path)
    e("count.water."+crop, frozen["candidates"]["management_refit"][crop]["free_parameters"], "estimated water coefficients", frozen_path)
    e("count.inherited."+crop, frozen["candidates"]["management_refit"][crop]["inherited_growth_records"], "observations", frozen_path)

areas = mapping.groupby("representative_id").used_area_ha.sum()
AREA = float(areas.sum())
e("region.area", AREA, "ha mapped physical rotation area", mapping_path, "sum static 2020 CPM-corrected Class246 source-cell areas")
regional_records = []
for year, g in rotation.groupby("harvest_year"):
    for policy in ["conventional", "uniform_25pct", "targeted_25pct", "uniform_50pct", "targeted_50pct", "uniform_75pct", "targeted_75pct"]:
        cut = 0. if policy == "conventional" else int(policy.split("_")[1][:-3])/100
        if policy.startswith("targeted"):
            q = g.merge(allocation[allocation.reduction_fraction == cut], on=["representative_id", "fraction"], validate="one_to_one")
            weights = q.representative_id.map(areas)*q.area_share
        else:
            q = g[g.fraction == 1-cut]
            weights = q.representative_id.map(areas)
        record = dict(harvest_year=year, policy=policy, grain=float(np.sum(weights*q.yield_kg_ha)/1e9))
        for label, col in [("irrigation", "irrigation_mm"), ("et", "modeled_total_et_mm"), ("drainage", "annual_bottom_drainage_mm"), ("runoff", "annual_runoff_mm")]:
            record[label] = float(np.sum(weights*q[col])*10/1e9)
        regional_records.append(record)
regional = pd.DataFrame(regional_records)
means = regional[regional.harvest_year.between(2014, 2025)].groupby("policy")[["grain", "irrigation", "et", "drainage", "runoff"]].mean()
for policy, row in means.iterrows():
    for label, value in row.items():
        e(f"region.{policy}.{label}", value, "Mt yr-1 dry grain" if label == "grain" else "km3 yr-1",
          [rotation_path, allocation_path, mapping_path], "source-cell mapped area times frozen allocation share times response, then equal means over 12 harvest years",
          dict(policy=policy, harvest_years="2014-2025", variable=label))
gain = float(means.loc["targeted_50pct", "grain"]-means.loc["uniform_50pct", "grain"])
e("region.gain", gain, "Mt yr-1 dry grain", [rotation_path, allocation_path, mapping_path], "targeted50 minus uniform50")
e("region.gain_ha", gain*1e6/AREA, "t ha-1 dry grain per wheat-maize rotation year", [rotation_path, allocation_path, mapping_path])
e("region.gain_pct", 100*gain/means.loc["uniform_50pct", "grain"], "% of equal-water uniform50 production", [rotation_path, allocation_path, mapping_path])
e("region.loss_conventional", means.loc["conventional", "grain"]-means.loc["targeted_50pct", "grain"], "Mt yr-1 dry grain", [rotation_path, allocation_path, mapping_path])
e("region.extra_et", means.loc["targeted_50pct", "et"]-means.loc["uniform_50pct", "et"], "km3 yr-1 crop-plus-fallow ET", [rotation_path, allocation_path, mapping_path])
wide = regional[regional.harvest_year.between(2014, 2025)].pivot(index="harvest_year", columns="policy", values="grain")
e("region.positive_years", (wide.targeted_50pct > wide.uniform_50pct).sum(), "years", rotation_path)
e("region.total_years", len(wide), "years", rotation_path)
responses = rotation[rotation.harvest_year.between(2014, 2025)].groupby(["representative_id", "fraction"]).yield_kg_ha.mean().reset_index()
g = responses.merge(allocation[allocation.reduction_fraction == .5], on=["representative_id", "fraction"], validate="one_to_one")
targeted = (g.yield_kg_ha*g.area_share).groupby(g.representative_id).sum()
uniform = responses[responses.fraction == .5].set_index("representative_id").yield_kg_ha
cells = mapping.copy();cells["delta_grain"] = cells.representative_id.map(targeted-uniform)
for name, low, high in [("southern", 32, 36), ("central", 36, 38), ("northern", 38, 41)]:
    g = cells[cells.latitude.ge(low)&cells.latitude.lt(high)]
    e("spatial."+name+".grain", np.sum(g.used_area_ha*g.delta_grain)/1e9, "Mt yr-1 dry grain", [rotation_path, allocation_path, mapping_path], f"source-cell latitude in [{low},{high}); source-cell area times inherited representative grain contrast")
    e("spatial."+name+".area_pct", 100*g.used_area_ha.sum()/AREA, "% of mapped physical rotation area", mapping_path)

available = membership[membership.harvest_year.between(2014, 2025)].groupby("harvest_year").class_available.all()
available_years = available[available].index.tolist()
e("count.available", len(available_years), "GRACE-available harvest years", membership_path)
e("count.missing", (~available).sum(), "missing-GRACE harvest years", membership_path)
train_class = membership[membership.harvest_year.between(2003, 2013)]
e("count.classes", train_class.relative_class.nunique(), "antecedent storage-rainfall classes", membership_path)
e("count.strata", train_class.relative_storage.nunique(), "storage strata", membership_path)
e("count.targets", len(rules), "selection targets", rule_path)
ad_records = []
for (policy, year), g in adaptive.groupby(["policy", "harvest_year"]):
    w = g.representative_id.map(areas)
    ad_records.append(dict(policy=policy, year=year, grain=float(np.sum(w*g.yield_kg_ha)/1000),
                           irrigation=float(np.sum(w*g.irrigation_mm)/AREA), et=float(np.sum(w*g.modeled_total_et_mm)/AREA),
                           drainage=float(np.sum(w*g.annual_bottom_drainage_mm)/AREA), runoff=float(np.sum(w*g.annual_runoff_mm)/AREA)))
ad = pd.DataFrame(ad_records)
full = ad[ad.policy == "conventional"].set_index("year")
for target in [95, 98]:
    policy = f"adaptive_{target}"
    q = ad[(ad.policy == policy)&ad.year.isin(available_years)].set_index("year").sort_index()
    b = full.loc[q.index]
    ratio = 100*q.grain/b.grain
    for metric, value in [("retention", 100*q.grain.sum()/b.grain.sum()), ("irrigation_reduction", (b.irrigation-q.irrigation).mean()),
                          ("et_reduction", (b.et-q.et).mean()), ("minimum_retention", ratio.min()), ("years_at_least95", ratio.ge(95).sum())]:
        e(f"adaptive.{target}.{metric}", value, "years" if metric == "years_at_least95" else "mm per rotation" if "reduction" in metric else "% of same-year conventional dry grain",
          [adaptive_path, membership_path, mapping_path], "available-year period retention = ratio of summed dry production; reductions = mean of paired same-year differences",
          dict(policy=policy, eligible_years=available_years, denominator="conventional on identical years"))
    e(f"target.{target}", target, "% selection grain target", rule_path)
    for weather in ["dry", "wet"]:
        assert rules[policy]["higher_storage_"+weather] == rules[policy]["lower_storage_"+weather]
        e(f"quota.{target}.{weather}", rules[policy]["lower_storage_"+weather], "fraction of conventional irrigation", rule_path,
          "identical in higher and lower storage strata")
    a = decision[decision.policy == policy].sort_values(["representative_id", "harvest_year"])
    b = decision[decision.policy == f"rain_{target}_matched"].sort_values(["representative_id", "harvest_year"])
    assert np.array_equal(a.quota_fraction.to_numpy(), b.quota_fraction.to_numpy())
    ar = ad[ad.policy == policy].set_index("year").sort_index();br = ad[ad.policy == f"rain_{target}_matched"].set_index("year").sort_index()
    max_delta = max(float((ar[k]-br[k]).abs().max()) for k in ["grain", "irrigation", "et", "drainage", "runoff"])
    CROSSCHECKS.append(dict(name=f"matched rainfall {target} identical actions/outcomes", passed=max_delta < 1e-9,
                            maximum_absolute_output_difference=max_delta, n_decisions=len(a), sources=[decision_path, adaptive_path]))

for name, value, source in [("year.field_start", min(frozen["field_calibration_years"]), frozen_path),
                            ("year.field_end", max(frozen["field_calibration_years"]), frozen_path),
                            ("year.field_test", frozen["field_testing_years"][0], frozen_path),
                            ("year.eval_start", min(protocol["evaluation_years"]), protocol_path),
                            ("year.eval_end", max(protocol["evaluation_years"]), protocol_path),
                            ("year.alloc_start", min(protocol["training_allocation_years"]), protocol_path),
                            ("year.alloc_end", max(protocol["training_allocation_years"]), protocol_path),
                            ("year.class_start", min(protocol["class_selection_years"]), protocol_path),
                            ("year.class_end", max(protocol["class_selection_years"]), protocol_path)]:
    e(name, value, "calendar harvest year", source)
e("meta.moisture", 13, "% wet-basis grain moisture", grain_definition_path, "archived primary supplementary note: Moisture of wheat and maize grain is 13%")
e("meta.dry_fraction", .87, "dry fraction of grain at 13% moisture", grain_definition_path, "1-0.13")
e("meta.depth0", 0, "m below ground surface", profiles_path)
assert all(sum(x["thickness_mm"] for x in p["layers"]) == 2000 for p in profiles)
e("meta.depth2", 2, "m modeled soil-profile depth", profiles_path, "sum layer thickness / 1000")
e("meta.bin_wheat", 15, "days per wheat plotting bin", "analysis_source/current_crop_publication.py", 'q.das divided by q.crop.map({"wheat":15,"maize":7})')
e("meta.bin_maize", 7, "days per maize plotting bin", "analysis_source/current_crop_publication.py")
replicates = {x.get("yield_replicates") for x in transcription["yang2024"] if x.get("yield_replicates") is not None}
assert replicates == {3}
e("meta.field_replicates", 3, "field replicates underlying treatment mean/SE", transcription_path)
e("meta.conversions", 1, "dry-to-13%-grain moisture conversions", "analysis_source/current_crop_publication.py", "dry grain divided by .87 once")
e("meta.cut50", 50, "% field-irrigation reduction", allocation_path)
e("meta.threshold95", 95, "% same-year conventional grain", adaptive_path)
e("count.field_test_years", len(frozen["field_testing_years"]), "retrospective field-test years", frozen_path)

def st(crop, split, var, metric): return f"station.{crop}.{split}.{var}.{metric}"
def fi(crop, split, var, metric): return f"field.{crop}.{split}.{var}.{metric}"
def pair(fn, split, var, metric): return [fn(c, split, var, metric) for c in ["wheat", "maize"]]

# Numeric tokens in their actual order, excluding figure/table/citation labels.
MAP = {
 (23,"paragraphs",0): ["count.station", "count.nonET.calibration", "count.nonET.validation"],
 (23,"paragraphs",1): pair(st,"validation","lai","rmse")+pair(st,"validation","lai","nse")+pair(st,"calibration","lai","rmse")+pair(st,"validation","biomass","rmse")+pair(st,"validation","biomass","bias")+pair(st,"validation","biomass","nse"),
 (23,"paragraphs",2): pair(st,"calibration","yield","rmse")+pair(st,"validation","yield","rmse")+pair(st,"validation","yield","bias")+pair(st,"validation","yield","nse")+pair(st,"validation","harvest_biomass","rmse")+pair(st,"validation","harvest_biomass","n"),
 (23,"paragraphs",3): pair(st,"validation","et","rmse")+pair(st,"validation","et","bias")+pair(st,"validation","et","nse")+[f"matched.{c}.validation.{m}" for m in ["rmse","bias","nse"] for c in ["wheat","maize"]],
 (24,"caption",0): ["count.inherited.wheat","count.inherited.maize","count.ET","year.field_start","year.field_end","year.field_test","year.field_test"],
 (25,"caption",0): ["meta.bin_wheat","meta.bin_maize"],
 (27,"paragraphs",0): pair(fi,"calibration","et","rmse")+[fi("wheat","calibration","et","n")]+pair(fi,"calibration","et","bias")+pair(fi,"calibration","et","nrmse_percent")+["year.field_test"]+pair(fi,"validation","et","rmse")+pair(fi,"validation","et","bias")+pair(fi,"validation","et","nrmse_percent")+pair(fi,"validation","et","nse"),
 (27,"paragraphs",1): ["meta.moisture"]+pair(fi,"calibration","grain","rmse")+pair(fi,"validation","grain","rmse")+pair(fi,"validation","grain","bias")+pair(fi,"validation","grain","nrmse_percent")+pair(fi,"validation","grain","nse")+pair(fi,"calibration","biomass","rmse")+pair(fi,"validation","biomass","rmse")+pair(fi,"validation","biomass","nrmse_percent"),
 (27,"paragraphs",2): ["contrast.wheat.calibration.et.rmse","contrast.maize.calibration.et.rmse","year.field_test","contrast.wheat.validation.et.rmse","contrast.maize.validation.et.rmse","contrast.wheat.calibration.grain.rmse","contrast.maize.calibration.grain.rmse","contrast.wheat.validation.grain.rmse","contrast.maize.validation.grain.rmse","meta.moisture"],
 (28,"table_note",0): [fi("wheat","calibration","et","n"),"year.field_start","year.field_end","year.field_test","meta.depth0","meta.depth2","meta.moisture"],
 (29,"caption",0): ["meta.moisture","year.field_start","year.field_end","year.field_test","meta.dry_fraction","count.field.wheat","meta.depth0","meta.depth2"],
 (30,"paragraphs",0): ["region.conventional.irrigation","region.conventional.grain","year.eval_start","year.eval_end","meta.cut50","region.targeted_50pct.irrigation","region.targeted_50pct.grain","region.uniform_50pct.grain","region.gain","region.gain_pct"],
 (30,"paragraphs",1): ["region.targeted_50pct.et","region.uniform_50pct.et","region.conventional.et","region.extra_et","region.targeted_50pct.drainage","region.uniform_50pct.drainage","region.targeted_50pct.runoff","region.uniform_50pct.runoff"],
 (30,"paragraphs",2): ["region.positive_years","region.total_years","spatial.central.grain","spatial.northern.grain","spatial.southern.grain","spatial.southern.area_pct"],
 (31,"table_note",0): ["year.eval_start","year.eval_end","meta.depth2"],
 (31,"table_caption",0): ["year.eval_start","year.eval_end"],
 (32,"caption",0): ["year.eval_start","year.eval_end","year.alloc_start","year.alloc_end"],
 (33,"caption",0): ["meta.cut50","year.eval_start","year.eval_end"],
 (34,"paragraphs",0): ["year.class_start","year.class_end","target.95","quota.95.dry","quota.95.wet","target.98","quota.98.dry","quota.98.wet"],
 (34,"paragraphs",1): ["target.95","target.98","adaptive.95.retention","adaptive.98.retention","adaptive.95.irrigation_reduction","adaptive.98.irrigation_reduction","adaptive.95.et_reduction","adaptive.98.et_reduction","target.95","meta.threshold95","adaptive.95.years_at_least95","adaptive.95.minimum_retention"],
 (35,"table_note",0): ["target.95","year.class_start","year.class_end"],
 (36,"caption",0): ["year.eval_start","year.eval_end","target.95","target.98"],
 (43,"paragraphs",0): ["meta.cut50","region.gain","region.extra_et"],
 (43,"paragraphs",1): ["target.95","target.98","adaptive.95.retention","adaptive.98.retention","target.95","adaptive.95.irrigation_reduction","adaptive.95.et_reduction"],
}
WORDMAP = {
 (23,"paragraphs",0): ["count.sites"], (23,"paragraphs",2): [st("wheat","validation","harvest_biomass","years"),st("maize","validation","harvest_biomass","years")],
 (24,"caption",0): ["count.sites","count.treatments.wheat","count.water.wheat"],
 (27,"paragraphs",0): [fi("wheat","validation","et","n")], (27,"paragraphs",2): ["field.wheat.calibration.years"],
 (28,"table_note",0): [fi("wheat","validation","et","n")], (29,"caption",0): ["meta.field_replicates","meta.conversions"],
 (34,"paragraphs",0): ["count.classes","count.available","count.missing","count.strata"],
 (34,"paragraphs",1): ["count.available","count.available"], (34,"paragraphs",2): ["count.targets"],
 (35,"table_note",0): ["count.strata"], (43,"paragraphs",1): ["count.available"], (43,"paragraphs",2): ["count.field_test_years"],
}
paper_bytes = PAPER.read_bytes()
paper_sha = hashlib.sha256(paper_bytes).hexdigest()
snapshot = VER/f"2026-10-08_results_numbers_source_{paper_sha[:12]}.json"
if not snapshot.exists(): snapshot.write_bytes(paper_bytes)
document = json.loads(paper_bytes)
SOURCES[str(PAPER.relative_to(ROOT))] = dict(sha256=paper_sha, bytes=len(paper_bytes), snapshot=str(snapshot.relative_to(ROOT)))
number_pattern = re.compile(r"(?<![\w.])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
word_pattern = re.compile(r"\b(?:five|four|three|nine|eight|sixteen|twelve|single|both|one|once|two)\b", re.I)
section = ""
for bi, block in enumerate(document["blocks"]):
    if block.get("heading"): section = block["heading"]
    if not section.startswith(("3.", "5.")): continue
    for field_name in ["paragraphs", "caption", "table_note", "table_caption"]:
        texts = block.get(field_name, [])
        if isinstance(texts, str): texts = [texts]
        for pi, text in enumerate(texts):
            normalized = text.replace("−", "-")
            key = (bi, field_name, pi)
            excluded = [m.span() for p in [r"(?:Figures?|Tables?)\s+S?\d+(?:[–-]S?\d+)?", r"\[CITE:[^\]]+\]", r"\b1:1\b"] for m in re.finditer(p, normalized)]
            matches = []
            for m in number_pattern.finditer(normalized):
                if any(a <= m.start() < b for a,b in excluded):
                    LABELS.append(dict(location=dict(block=bi,field=field_name,index=pi,section=section), token=m.group(), span=m.span(), category="figure/table/citation/reference-line label"))
                else: matches.append(m)
            expected = MAP.get(key, [])
            if len(matches) != len(expected):
                UNMAPPED.append(dict(location=key, section=section, text=text, tokens=[m.group() for m in matches], expected_keys=expected, reason="numeric-token map requires review after manuscript changes"))
            else:
                for match,name in zip(matches,expected):
                    claim(dict(block=bi,field=field_name,index=pi,section=section),text,match.group(),name,span=match.span())
            words = list(word_pattern.finditer(normalized))
            expected_words = WORDMAP.get(key, [])
            if len(words) != len(expected_words):
                UNMAPPED.append(dict(location=key, section=section, text=text, tokens=[m.group() for m in words], reason="word-number map requires review"))
            else:
                for match,name in zip(words,expected_words):
                    claim(dict(block=bi,field=field_name,index=pi,section=section),text,match.group(),name,category="count written as word",span=match.span())

# Check every numeric cell in each Results table against raw-data calculations.
for bi in [26,28,31,35]:
    block = document["blocks"][bi]
    table_path = "publication/"+block["table"]
    table = read(table_path)
    for ri,row in table.iterrows():
        if bi == 26:
            crop = row.Crop.lower();split = "calibration" if row.Partition == "Prior calibration partition" else "validation"
            mapping_keys = {"Seasons":f"matched.{crop}.{split}.n","NSE":f"matched.{crop}.{split}.nse","RMSE":f"matched.{crop}.{split}.rmse","Bias":f"matched.{crop}.{split}.bias"}
        elif bi == 28:
            crop = row.Crop.lower();split = "calibration" if "2016" in row.Partition else "validation"
            var = {"Seasonal ET":"et","Grain":"grain","Annual grain":"grain","Biomass":"biomass","Aboveground biomass":"biomass"}[row.Target]
            mapping_keys = {col:fi(crop,split,var,m) for col,m in [("n","n"),("RMSE","rmse"),("Bias","bias"),("nRMSE_pct","nrmse_percent"),("NSE","nse"),("R²","r_squared")]}
        elif bi == 31:
            policy = row.Policy.lower().replace(" ","_")
            mapping_keys = {col:f"region.{policy}.{m}" for col,m in [("Grain (Mt yr⁻¹)","grain"),("Irrigation (km³ yr⁻¹)","irrigation"),("ET (km³ yr⁻¹)","et"),("Drainage (km³ yr⁻¹)","drainage"),("Runoff (km³ yr⁻¹)","runoff")]}
        else:
            label = row.Class.lower().replace(" ","_")
            quota = rules["adaptive_95"][label]
            group = fixed_class[(fixed_class.period == "training")&(fixed_class.relative_class == label)&(fixed_class.fraction == quota)]
            full = fixed_class[(fixed_class.period == "training")&(fixed_class.relative_class == label)&(fixed_class.fraction == 1)]
            retention = 100*np.sum(group.represented_area_ha*group.yield_kg_ha)/np.sum(full.represented_area_ha*full.yield_kg_ha)
            mapping_keys = {}
            for col,value,unit in [("Irrigation fraction",quota,"fraction"),("Wheat irrigation (mm)",300*quota,"mm"),("Maize irrigation (mm)",80*quota,"mm"),("Selection grain retention (%)",retention,"% of class-mean conventional grain")]:
                name = f"class.{label}.{col}";e(name,value,unit,[rule_path,fixed_class_path],"area-weighted training class mean relative to equal-cohort full irrigation")
                mapping_keys[col] = name
        numeric_columns = [col for col in table.columns if isinstance(row[col],(int,float,np.integer,np.floating)) and pd.notna(row[col])]
        assert set(numeric_columns) == set(mapping_keys), (bi,numeric_columns,mapping_keys)
        for col,name in mapping_keys.items():
            value = row[col]
            claim(dict(block=bi,field="table_cell",row=int(ri),column=col,table=table_path),str(row.to_dict()),str(value),name,
                  tolerance=1e-9 if EXPECTED[name]["unit"] not in ["seasons","treatment means"] else 0.,category="Results table numeric cell")

failed = [x for x in CLAIMS if not x["passed"]]
changed_sources = []
for relative,receipt in SOURCES.items():
    if hashlib.sha256((ROOT/relative).read_bytes()).hexdigest() != receipt["sha256"]:
        changed_sources.append(relative)
passed = not failed and not UNMAPPED and not changed_sources and all(x["passed"] for x in CROSSCHECKS)
audit = dict(checked_at_utc=datetime.now(timezone.utc).isoformat(),
             all_quantitative_claims_passed=passed, manuscript_sha256=paper_sha,
             manuscript_source_snapshot=str(snapshot.relative_to(ROOT)),
             scope="All Results/Conclusions paragraph numeric tokens, written-number counts, figure/table captions and notes, plus every numeric Results table cell; reference labels retained separately",
             claim_count=len(CLAIMS), passed_claim_count=sum(x["passed"] for x in CLAIMS), failed_claim_count=len(failed),
             reference_label_count=len(LABELS), claims=CLAIMS, failed_claims=failed, unmapped_claims=UNMAPPED,
             excluded_reference_labels=LABELS, crosschecks=CROSSCHECKS, expected_raw_statistics=EXPECTED,
             source_provenance=SOURCES, sources_changed_during_audit=changed_sources,
             audit_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
             crop_order="Narrative pairs refer to wheat then maize unless the sentence explicitly says otherwise",
             mass_basis="Station and regional grain are dry mass; Wuqiao grain is at 13% moisture; biomass is dry mass",
             statistical_denominators="Site-balanced station scores; equal Wuqiao treatment-year means; equal regional year means; adaptive grain retention is summed-production ratio on identical GRACE-available years",
             scenario_classification="Conditional regional scenarios and retrospective field/station comparisons; numeric agreement does not establish independent regional validation")
OUT.write_text(json.dumps(audit,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
print(json.dumps(dict(all_quantitative_claims_passed=passed,claim_count=len(CLAIMS),failed_claims=failed,
                      unmapped_claims=UNMAPPED,sources_changed_during_audit=changed_sources,audit=str(OUT)),ensure_ascii=False,indent=2))
sys.exit(0 if passed else 1)
