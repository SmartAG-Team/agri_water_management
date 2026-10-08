"""Publish selected current crop evidence without changing archived calibration.

Run with the project .venv/bin/python. The copied, exact calibration is the
controlling source. Publication exports contain only selected management_refit
predictions; reference and older model predictions are never mixed into scores.
"""
from pathlib import Path
import hashlib
import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CAL = ROOT / "calibration"
PUB = ROOT / "publication"
TABLES = PUB / "tables"
FIGURES = PUB / "figures"
BLUE, ORANGE = "#2166a5", "#c56d20"
DISPLAY = {"calibration": "Prior calibration partition",
           "validation": "Retrospective evaluation"}
FIELD_DISPLAY = {"calibration": "Calibration (2016–2018)",
                 "validation": "Retrospective testing (2019)"}
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8.5,
                    "axes.labelcolor": "black", "text.color": "black",
                    "xtick.color": "black", "ytick.color": "black",
                    "axes.edgecolor": "black", "axes.linewidth": .7,
                    "pdf.fonttype": 42, "ps.fonttype": 42,
                    "savefig.facecolor": "white"})


def native(value):
    return json.loads(json.dumps(value, default=lambda x: x.item()))


def write_json(path, value):
    path.write_text(json.dumps(native(value), ensure_ascii=False, indent=2,
                               allow_nan=False) + "\n")


def export(frame, name, alias=None):
    frame.to_csv(TABLES / ("current_" + name + ".csv"), index=False)
    if alias:
        frame.to_csv(TABLES / (alias + ".csv"), index=False)


def weights(frame):
    """Equal site, site-year and case weights, then equal within-case records."""
    counts = frame.groupby(["site", "source_group_id", "case_id"]).size()
    site_years = frame.groupby("site").source_group_id.nunique()
    year_cases = frame.groupby(["site", "source_group_id"]).case_id.nunique()
    ns = frame.site.nunique()
    result = np.array([1 / (ns * site_years.loc[r.site] *
                      year_cases.loc[(r.site, r.source_group_id)] *
                      counts.loc[(r.site, r.source_group_id, r.case_id)])
                      for r in frame.itertuples()])
    assert np.isclose(result.sum(), 1., atol=1e-12)
    return result


def score(observed, predicted, weight=None):
    o, p = np.asarray(observed, float), np.asarray(predicted, float)
    assert len(o) and len(o) == len(p) and np.isfinite(o).all() and np.isfinite(p).all()
    w = np.full(len(o), 1. / len(o)) if weight is None else weight
    om, pm = float(w @ o), float(w @ p)
    mse = float(w @ ((p - o) ** 2))
    ov, pv = float(w @ ((o - om) ** 2)), float(w @ ((p - pm) ** 2))
    covariance = float(w @ ((o - om) * (p - pm)))
    return dict(n=len(o), observed_mean=om, predicted_mean=pm, rmse=mse ** .5,
                bias=float(w @ (p - o)), nrmse_percent=100 * mse ** .5 / om,
                nse=1 - mse / ov if ov > 1e-12 else None,
                r_squared=covariance ** 2 / (ov * pv) if ov * pv > 1e-12 else None)


def save_figure(fig, name, caption):
    for extension in ["png", "pdf"]:
        fig.savefig(FIGURES / (name + "." + extension), dpi=300, bbox_inches="tight")
    (FIGURES / (name + "_caption.txt")).write_text(caption + "\n")
    plt.close(fig)


def style(ax, letter, title):
    ax.set_title(f"({letter}) {title}", loc="left", fontsize=9)
    for spine in ax.spines.values():
        spine.set_visible(True)
    ax.tick_params(width=.7, length=3)


def parameters(frozen):
    cards, sources = {}, {}
    for crop in ["wheat", "maize"]:
        paths = sorted((CAL / "inputs/resolved/management_refit/field").glob("*" + crop + "*"))
        assert len(paths) == 16
        inputs = [json.loads(p.read_text()) for p in paths]
        card = inputs[0]["parameters"]
        assert all(p["parameters"] == card for p in inputs)
        assert card["crop"]["growth_process"]["version"] == "canopy_v5"
        assert card["hydrology"]["drainage_method"] == "matric_gradient"
        assert card["hydrology"]["matric_interface_method"] == "relative_arithmetic"
        cards[crop], sources[crop] = card, str(paths[0].relative_to(ROOT))
    rows = []
    def row(label, unit, getter):
        rows.append({"Parameter": label, "Unit": unit,
                     "Wheat": getter(cards["wheat"]), "Maize": getter(cards["maize"])})
    row("Radiation-use efficiency", "g MJ⁻¹ intercepted PAR", lambda c: c["crop"]["profile"]["rue_g_mj"])
    for dvs in [0., .5, 1.]:
        row(f"Specific leaf area at DVS {dvs:g}", "m² g⁻¹",
            lambda c, x=dvs: dict(c["crop"]["growth_process"]["sla_by_stage"])[x])
    for label, dvs in [("before flowering", 0.), ("at DVS 1.4", 1.4), ("at DVS 1.7", 1.7)]:
        row("Leaf senescence rate " + label, "(°C d)⁻¹",
            lambda c, x=dvs: dict(c["crop"]["growth_process"]["senescence_by_stage"])[x])
    row("Leaf area index at emergence", "m² m⁻²", lambda c: c["crop"]["profile"]["initial_lai"])
    row("Grain number per biomass at grain set", "grains g⁻¹", lambda c: c["crop"]["profile"]["kernels_per_g_flowering_biomass"])
    row("Maximum individual grain mass", "g grain⁻¹", lambda c: c["crop"]["profile"]["max_grain_size_g"])
    row("Transpiration coefficient", "dimensionless", lambda c: c["crop"]["transpiration_coefficient"])
    row("Soil evaporation coefficient", "dimensionless", lambda c: c["crop"]["soil_evaporation_coefficient"])
    row("Root water uptake coefficient", "d⁻¹", lambda c: c["hydrology"]["root_extraction_fraction_day"])
    for key, label in [("leaf_allocation_multiplier", "Leaf biomass partitioning multiplier"),
                       ("early_leaf_allocation_multiplier", "Early vegetative leaf partitioning multiplier"),
                       ("late_leaf_allocation_multiplier", "Late vegetative leaf partitioning multiplier")]:
        row(label, "dimensionless", lambda c, k=key: c["crop"]["growth_process"].get(k))
    for key, label in [("reserve_fraction", "Stem reserve fraction"),
                       ("release_rate", "Stem reserve release fraction")]:
        row(label, "dimensionless", lambda c, k=key: c["crop"].get("grain_process", {}).get(k))
    row("Assimilation water-stress exponent", "dimensionless", lambda c: c["crop"]["growth_process"]["assimilation_water_stress_exponent"])
    row("Readily available water fraction", "dimensionless", lambda c: c["hydrology"]["readily_available_water_fraction"])
    row("Root density decay", "m⁻¹", lambda c: c["hydrology"]["root_density_decay_m_inv"])
    row("Maximum rooting depth", "mm", lambda c: c["crop"]["profile"]["root_max_mm"])
    row("Root compensation fraction", "dimensionless", lambda c: c["hydrology"]["root_compensation_fraction"])
    row("Readily evaporable water fraction", "dimensionless", lambda c: c["hydrology"]["readily_evaporable_fraction"])
    export(pd.DataFrame(rows), "shared_crop_parameters", "shared_crop_parameters")
    write_json(TABLES / "current_shared_effective_cards.json",
               dict(selected_version=frozen["selected_version"], source_cards=sources,
                    cards=cards, source_is_identical_across_16_field_cases_per_crop=True,
                    frozen_fit_cards=frozen["candidates"]["management_refit"],
                    effective_values_are_not_fit_multipliers=True,
                    root_depth_rule="Minimum of fitted maximum and supplied soil-profile depth"))
    return rows


def tables(station, field, inv):
    raw = []
    sites = []
    for (crop, split, variable), g in station.groupby(["crop", "split", "variable"]):
        raw.append(dict(crop=crop, split=split, variable=variable,
                        n_sites=g.site.nunique(), n_site_years=g.source_group_id.nunique(),
                        comparison="Conditional transfer with inherited growth calibration",
                        unit=g.unit.iloc[0], **score(g.value, g.predicted, weights(g))))
    for (site, crop, split, variable), g in station.groupby(["site", "crop", "split", "variable"]):
        sites.append(dict(site=site, crop=crop, split=split, variable=variable,
                          n_sites=1, n_site_years=g.source_group_id.nunique(),
                          unit=g.unit.iloc[0], **score(g.value, g.predicted, weights(g))))
    raw = pd.DataFrame(raw)
    saved = pd.read_csv(CAL / "tables/station_metrics.csv").query("version == 'management_refit'")
    merged = raw.merge(saved, on=["crop", "split", "variable"], validate="one_to_one", suffixes=("", "_saved"))
    for col in ["rmse", "bias", "nse", "r_squared"]:
        assert np.allclose(merged[col], merged[col + "_saved"], atol=1e-9, rtol=0, equal_nan=True)
    export(raw, "station_metrics_raw_units")
    export(pd.DataFrame(sites), "site_metrics_raw_units")
    publication = raw.copy()
    mass = publication.variable.isin(["biomass", "yield", "harvest_biomass"])
    publication.loc[mass, ["observed_mean", "predicted_mean", "rmse", "bias"]] *= .001
    publication.loc[mass, "unit"] = "t ha⁻¹ dry mass"
    publication["partition_label"] = publication.split.map(DISPLAY)
    export(publication, "station_metrics")
    export(pd.read_csv(CAL / "tables/case_data_availability.csv"), "case_data_availability")
    coverage = station.groupby(["site", "variable"]).size().unstack(fill_value=0)
    coverage = coverage.reindex(columns=["lai", "biomass", "et", "yield", "harvest_biomass"], fill_value=0)
    coverage.columns = ["LAI", "Biomass", "Daily ET", "Yield", "Harvest biomass"]
    export(coverage.rename_axis("Site").reset_index(), "observation_coverage", "main_observation_coverage")
    detailed = station.groupby(["site", "crop", "split", "variable"]).agg(
        records=("observation_id", "size"), cases=("case_id", "nunique"),
        site_years=("source_group_id", "nunique"), first_date=("window_start", "min"),
        last_date=("window_end", "max")).reset_index()
    export(detailed, "observation_coverage_by_partition")
    et = station[station.variable.eq("et")]
    assert et.window_start.eq(et.window_end).all()
    sums = et.groupby(["crop", "split", "site", "source_group_id", "case_id"]).agg(
        value=("value", "sum"), predicted=("predicted", "sum"),
        measurement_days=("observation_id", "nunique")).reset_index()
    export(sums, "matched_day_et_by_case")
    rows = []
    for (crop, split), g in sums.groupby(["crop", "split"]):
        s = score(g.value, g.predicted, weights(g))
        rows.append(dict(Crop=crop.capitalize(), Partition=DISPLAY[split], Seasons=len(g),
                         NSE=s["nse"], RMSE=s["rmse"], Bias=s["bias"]))
    matched = pd.DataFrame(rows)
    export(matched, "matched_day_et", "main_matched_day_et")
    field_rows, annual_rows, contrasts = [], [], []
    for (crop, split), g in field.groupby(["crop", "split"]):
        for target, observed, predicted, factor, unit in [
            ("Seasonal ET", "observed_et_mm", "predicted_et_mm", 1., "mm"),
            ("Annual grain", "yield_13pct_kg_ha", "grain_13pct_kg_ha", .001, "t ha⁻¹ at 13% moisture"),
            ("Aboveground biomass", "observed_biomass_kg_ha", "predicted_biomass_kg_ha", .001, "t ha⁻¹ dry mass")]:
            s = score(g[observed] * factor, g[predicted] * factor)
            field_rows.append(dict(Crop=crop.capitalize(), Partition=FIELD_DISPLAY[split], Target=target,
                                   n=s["n"], Unit=unit, RMSE=s["rmse"], Bias=s["bias"],
                                   nRMSE_pct=s["nrmse_percent"], NSE=s["nse"], **{"R²": s["r_squared"]}))
        s = score(g.yield_13pct_kg_ha * .001, g.grain_13pct_kg_ha * .001)
        annual_rows.append(dict(Crop=crop.capitalize(), Partition=FIELD_DISPLAY[split], Comparison="Annual grain yield",
                               N=s["n"], **{"RMSE (t/ha)": s["rmse"], "Bias (t/ha)": s["bias"],
                                           "NSE": s["nse"], "R²": s["r_squared"]}))
        cg = []
        for year, y in g.groupby("harvest_year"):
            base = y[y.treatment.eq("W0")].iloc[0]
            for r in y[y.treatment.ne("W0")].itertuples():
                cg.append(dict(crop=crop, split=split, year=int(year), treatment=r.treatment,
                               observed=(r.yield_13pct_kg_ha - base.yield_13pct_kg_ha) * .001,
                               predicted=(r.grain_13pct_kg_ha - base.grain_13pct_kg_ha) * .001))
        c = pd.DataFrame(cg)
        s = score(c.observed, c.predicted)
        annual_rows.append(dict(Crop=crop.capitalize(), Partition=FIELD_DISPLAY[split], Comparison="Within-year W1–W3 minus W0",
                               N=s["n"], **{"RMSE (t/ha)": s["rmse"], "Bias (t/ha)": s["bias"],
                                           "NSE": s["nse"], "R²": s["r_squared"]}))
        contrasts.extend(cg)
    field_summary = pd.DataFrame(field_rows)
    export(field_summary, "field_metrics", "main_benchmark_metrics")
    export(pd.DataFrame(annual_rows), "annual_yield_evaluation", "supplement_annual_yield_evaluation")
    export(pd.DataFrame(contrasts), "annual_yield_contrasts")
    dry = field.copy()
    dry["observed_dry_grain_kg_ha"] = dry.yield_13pct_kg_ha * .87
    dry["predicted_dry_grain_kg_ha"] = dry.grain_13pct_kg_ha * .87
    export(dry, "field_comparisons")
    export(pd.read_csv(CAL / "tables/field_ET_contrasts.csv").query("version == 'management_refit'"), "field_ET_contrasts")
    export(pd.read_csv(CAL / "tables/seasonal_ET_working_checks.csv").query("version == 'management_refit'"), "field_working_checks")
    inventory = inv.copy()
    inventory["year"] = inventory.source_group_id.str.rsplit("-", n=1).str[-1].astype(int)
    export(inventory, "case_partition")
    return publication, matched, field_summary, inventory


def figure3(station, field, inventory):
    fig = plt.figure(figsize=(11, 5.1), layout="constrained")
    gs = fig.add_gridspec(1, 3, width_ratios=[1.6, 1., 1.])
    ax = fig.add_subplot(gs[0, 0])
    rows = sorted(set(zip(inventory.site, inventory.crop)))
    for y, (site, crop) in enumerate(rows):
        g = inventory[inventory.site.eq(site) & inventory.crop.eq(crop)]
        for split, marker, color in [("calibration", "o", BLUE), ("validation", "s", ORANGE)]:
            years = sorted(g[g.split.eq(split)].year.unique())
            ax.scatter(years, np.full(len(years), y), marker=marker, color=color, s=28)
    ax.set_yticks(range(len(rows)), [f"{site} · {crop}" for site, crop in rows])
    ax.invert_yaxis()
    ax.set_xlim(1997, 2023)
    ax.set_xticks([1998, 2004, 2010, 2016, 2022])
    ax.set_xlabel("Archived crop-season group year")
    style(ax, "a", "Station partitions")
    ax = fig.add_subplot(gs[0, 1])
    variables = ["lai", "biomass", "yield", "harvest_biomass"]
    labels = ["LAI", "In-season biomass", "Grain", "Harvest biomass"]
    for offset, split, color in [(-.17, "calibration", BLUE), (.17, "validation", ORANGE)]:
        counts = [int((station.variable.eq(v) & station.split.eq(split)).sum()) for v in variables]
        bars = ax.barh(np.arange(4) + offset, counts, height=.3, color=color)
        for bar, n in zip(bars, counts):
            ax.text(n + 5, bar.get_y() + bar.get_height()/2, str(n), va="center", fontsize=8)
    ax.set_yticks(range(4), labels)
    ax.invert_yaxis(); ax.set_xlim(0, 520)
    ax.set_xlabel("Scored observations")
    style(ax, "b", "Station growth evidence")
    ax = fig.add_subplot(gs[0, 2])
    for i, crop in enumerate(["wheat", "maize"]):
        for split, marker, color in [("calibration", "o", BLUE), ("validation", "s", ORANGE)]:
            g = field[field.crop.eq(crop) & field.split.eq(split)]
            ax.scatter(g.harvest_year, [i + int(t[-1])*.14 for t in g.treatment],
                       color=color, marker=marker, s=30)
    ax.set_yticks([.21, 1.21], ["Wheat W0–W3", "Maize W0–W3"])
    ax.set_xlim(2015.6, 2019.4); ax.set_ylim(-.12, 1.65)
    ax.set_xticks([2016, 2017, 2018, 2019]); ax.set_xlabel("Harvest year")
    style(ax, "c", "Documented Wuqiao management")
    ax.text(.03, .96, "12 fitting + 4 testing seasons\nper crop; ET, grain, biomass",
            transform=ax.transAxes, ha="left", va="top", fontsize=8)
    fig.legend(handles=[Line2D([], [], marker="o", color=BLUE, ls="none", label="Calibration partition"),
                        Line2D([], [], marker="s", color=ORANGE, ls="none", label="Retrospective evaluation/testing")],
               loc="outside lower center", ncol=2, frameon=False)
    save_figure(fig, "current_Figure_3_observation_partitions",
        "Figure 3. Observation coverage and evidence partitions. (a) Whole station site-years "
        "remain in their original calibration and retrospective-evaluation partitions. Years "
        "refer to the archived crop-season grouping; coverage is intermittent. (b) Quality-screened "
        "station growth and harvest observations. All five eligible sites are retained; inherited "
        "growth coefficients were supported by 480 wheat and 433 maize calibration observations. "
        "Station management completeness is unconfirmed and these observations do not enter the "
        "current water-parameter fit. The additional 5,675 daily ET observations remain conditional "
        "comparisons. (c) Wuqiao contains four treatment means per crop-year: 2016–2018 is used to "
        "fit eight water coefficients per crop, and 2019 is excluded from fitting and selection. "
        "The 2019 outcomes had been inspected during earlier development, so testing is retrospective. "
        "Maize treatments identify preceding wheat management; maize irrigation is identical within each year.")


def figure4(station, inventory, metrics):
    q = station[station.variable.isin(["lai", "biomass", "et"])].copy()
    starts = pd.to_datetime(q.case_id.map(inventory.set_index("case_id").start_date))
    q["das"] = ((pd.to_datetime(q.window_start) - starts).dt.days +
                (pd.to_datetime(q.window_end) - starts).dt.days) / 2
    q["bin"] = np.floor(q.das / q.crop.map({"wheat": 15, "maize": 7})).astype(int)
    keys = ["crop", "split", "variable", "site", "source_group_id", "case_id", "bin"]
    case = q.groupby(keys)[["value", "predicted", "das"]].mean().reset_index()
    year = case.groupby([k for k in keys if k != "case_id"])[["value", "predicted", "das"]].mean().reset_index()
    rows = []
    for (crop, split, variable, bin_index), g in year.groupby(["crop", "split", "variable", "bin"]):
        sm = g.groupby("site")[["value", "predicted", "das"]].mean()
        w = np.array([1 / (g.site.nunique() * g.site.eq(r.site).sum()) for r in g.itertuples()])
        o = float(sm.value.mean()); neff = float(1 / (w @ w))
        se = float(np.sqrt((w @ ((g.value - o)**2)) / (1 - 1 / neff) / neff)) if neff > 1 else np.nan
        rows.append(dict(crop=crop, split=split, variable=variable, bin=bin_index,
                         das=sm.das.mean(), observed=o, predicted=sm.predicted.mean(),
                         observed_SE=se, sites=len(sm), site_years=len(g), effective_site_years=neff))
    curves = pd.DataFrame(rows)
    export(curves, "balanced_seasonal_curves")
    export(year, "seasonal_curve_site_year_sources")
    fig, axes = plt.subplots(4, 3, figsize=(10.3, 10.8), layout="constrained")
    variables = [("lai", "LAI (m² m⁻²)", 1.), ("biomass", "Aboveground dry biomass (t ha⁻¹)", .001),
                 ("et", "Daily actual ET (mm d⁻¹)", 1.)]
    for row, (crop, split) in enumerate([(c, s) for c in ["wheat", "maize"] for s in ["calibration", "validation"]]):
        for col, (variable, label, factor) in enumerate(variables):
            ax = axes[row, col]
            g = curves[curves.crop.eq(crop) & curves.split.eq(split) & curves.variable.eq(variable)].sort_values("bin")
            ax.errorbar(g.das, g.observed*factor, yerr=g.observed_SE*factor, color="black", marker="o",
                        ms=3, ls="none", capsize=2, lw=.75)
            for _, segment in g.groupby(g.bin.diff().gt(1).cumsum()):
                ax.plot(segment.das, segment.predicted*factor, color=BLUE, lw=1.3)
            all_crop = curves[curves.crop.eq(crop) & curves.variable.eq(variable)]
            upper = max((all_crop.observed + all_crop.observed_SE.fillna(0)).max(), all_crop.predicted.max()) * factor
            ax.set_ylim(0, upper*1.1)
            das = curves[curves.crop.eq(crop)].das
            ax.set_xlim(min(0, das.min()-5), das.max()+5)
            ax.set_xlabel("Days after sowing"); ax.set_ylabel(label)
            partition = "prior calibration years" if split == "calibration" else "retrospective evaluation"
            style(ax, chr(97 + row*3 + col), f"{crop.capitalize()} · {partition}")
            m = metrics[metrics.crop.eq(crop) & metrics.split.eq(split) & metrics.variable.eq(variable)].iloc[0]
            ax.text(.03, .94, f"RMSE = {m.rmse:.2f}\nNSE = {m.nse:.2f}", transform=ax.transAxes,
                    fontsize=8, va="top", bbox=dict(facecolor="white", edgecolor="none", alpha=.9, pad=1))
    fig.legend(handles=[Line2D([], [], color="black", marker="o", ls="none", label="Observed mean ± descriptive SE"),
                        Line2D([], [], color=BLUE, label="Current coupled prediction")],
               loc="outside lower center", ncol=2, frameon=False)
    save_figure(fig, "current_Figure_4_multisite_seasonal_curves",
        "Figure 4. Conditional station comparisons under the selected current crop parameters. "
        "Rows show wheat and maize in original calibration and retrospective-evaluation partitions; "
        "columns show LAI, in-season aboveground dry biomass and actual ET. Black points describe "
        "matched observations and blue lines the corresponding current predictions in 15-day wheat "
        "and 7-day maize bins. Cases receive equal weight within site-years, site-years within sites, "
        "and available sites within bins. Error bars are descriptive standard errors of site-year "
        "means; they are not measurement or model uncertainty. Bin populations can vary and missing "
        "bins remain gaps. Scores use the original unbinned records in displayed units. Growth "
        "coefficients retain their earlier multisite fitting history; station observations are "
        "excluded from the current water fit. Unconfirmed irrigation-log completeness makes station "
        "growth and ET conditional transfer evidence, without independent management validation.")


def figure5(field):
    annual = pd.read_csv(CAL / "data/wuqiao_digitized_annual_yields.csv")
    annual = annual[annual.crop.isin(["wheat", "maize"])]
    assert len(annual) == 32 and not annual.duplicated(["crop", "harvest_year", "treatment"]).any()
    values = field.merge(annual[["crop", "harvest_year", "treatment", "standard_error_kg_ha",
                                  "digitization_tolerance_kg_ha"]],
                         on=["crop", "harvest_year", "treatment"], validate="one_to_one")
    export(values, "field_figure_sources")
    fig, axes = plt.subplots(2, 2, figsize=(8.3, 7.6), layout="constrained")
    for row, (target, observed, predicted, factor, label) in enumerate([
            ("Seasonal ET", "observed_et_mm", "predicted_et_mm", 1., "Seasonal ET (mm)"),
            ("Annual grain", "yield_13pct_kg_ha", "grain_13pct_kg_ha", .001, "Grain at 13% moisture (t ha⁻¹)")]):
        for col, crop in enumerate(["wheat", "maize"]):
            ax = axes[row, col]; g = values[values.crop.eq(crop)]
            for split, marker, color in [("calibration", "o", BLUE), ("validation", "s", ORANGE)]:
                part = g[g.split.eq(split)]
                xerr = part.standard_error_kg_ha*factor if row == 1 else None
                ax.errorbar(part[observed]*factor, part[predicted]*factor, xerr=xerr,
                            color=color, fmt=marker, ms=5, ls="none", capsize=2,
                            markeredgecolor="black", markeredgewidth=.5, lw=.7)
            maximum = max(values[observed].max(), values[predicted].max()) * factor * 1.1
            ax.plot([0, maximum], [0, maximum], color="black", ls="--", lw=.8)
            ax.set_xlim(0, maximum); ax.set_ylim(0, maximum); ax.set_aspect("equal", adjustable="box")
            ax.set_xlabel("Observed " + label[0].lower() + label[1:])
            ax.set_ylabel("Simulated " + label[0].lower() + label[1:])
            title = "seasonal ET" if row == 0 else "annual grain"
            style(ax, chr(97+row*2+col), f"{crop.capitalize()} · {title}")
    fig.legend(handles=[Line2D([], [], marker="o", color=BLUE, ls="none", markeredgecolor="black",
                              label="Calibration (2016–2018; n = 12 per crop)"),
                        Line2D([], [], marker="s", color=ORANGE, ls="none", markeredgecolor="black",
                              label="Retrospective testing (2019; n = 4 per crop)")],
               loc="outside lower center", ncol=1, frameon=False)
    save_figure(fig, "current_Figure_5_Wuqiao_calibration_testing",
        "Figure 5. Wuqiao treatment-season calibration and retrospective testing of the selected "
        "current parameters. (a,b) Seasonal ET; (c,d) annual treatment-mean grain yield at 13% "
        "moisture. Circles show 2016–2018 observations used to fit eight water coefficients per "
        "crop; squares show 2019, excluded from fitting and selection but inspected during earlier "
        "development. Dashed lines denote 1:1 agreement. All 16 treatment seasons per crop are "
        "retained. Published ET is inferred from the 0–2 m water balance assuming negligible runoff "
        "and drainage; numerical ET dispersion is unavailable. Horizontal grain error bars are "
        "published standard errors (three field replicates), digitized with annual means from "
        "Supplementary Figure S2; pixel tolerances are approximately 0.043 t ha⁻¹ for wheat and "
        "0.056 t ha⁻¹ for maize. Model dry grain is divided by 0.87 for this comparison. Observed "
        "total 2-m storage conditions initialization; layer distribution and transferred hydraulics "
        "remain assumptions. Maize receives identical irrigation within each year, and treatment "
        "labels retain preceding wheat management. Four-year grain means overlap these annual "
        "targets and provide no additional independent evaluation observations.")


def supplementary_figures(station):
    year = pd.read_csv(TABLES / "current_seasonal_curve_site_year_sources.csv")
    series = []
    for (crop, site, split, variable, bin_index), g in year.groupby(["crop", "site", "split", "variable", "bin"]):
        series.append(dict(crop=crop, site=site, split=split, variable=variable,
                           bin=bin_index, das=g.das.mean(), observed=g.value.mean(),
                           predicted=g.predicted.mean(), site_years=len(g),
                           observed_SE=g.value.std(ddof=1)/len(g)**.5 if len(g)>1 else np.nan))
    series = pd.DataFrame(series)
    export(series, "site_seasonal_curves")
    number = 7
    for crop in ["wheat", "maize"]:
        for variable, label, factor in [("lai", "LAI (m² m⁻²)", 1.),
                                        ("biomass", "Aboveground dry biomass (t ha⁻¹)", .001),
                                        ("et", "Daily actual ET (mm d⁻¹)", 1.)]:
            part = series[series.crop.eq(crop) & series.variable.eq(variable)]
            sites = sorted(part.site.unique())
            fig, axes = plt.subplots(len(sites), 2, figsize=(8.5, 2.45*len(sites)),
                                     layout="constrained", squeeze=False)
            maximum = max((part.observed + part.observed_SE.fillna(0)).max(), part.predicted.max()) * factor * 1.1
            for row, site in enumerate(sites):
                for col, split in enumerate(["calibration", "validation"]):
                    ax = axes[row, col]
                    g = part[part.site.eq(site) & part.split.eq(split)].sort_values("bin")
                    ax.errorbar(g.das, g.observed*factor, yerr=g.observed_SE*factor,
                                fmt="o", ms=3, color="black", capsize=2, lw=.7)
                    for _, segment in g.groupby(g.bin.diff().gt(1).cumsum()):
                        ax.plot(segment.das, segment.predicted*factor, color=BLUE, lw=1.3)
                    ax.set_ylim(0, maximum); ax.set_xlim(min(0, part.das.min()-5), part.das.max()+5)
                    ax.set_xlabel("Days after sowing"); ax.set_ylabel(label)
                    period = "prior calibration years" if split == "calibration" else "retrospective evaluation"
                    style(ax, chr(97+row*2+col), f"{site} · {period}")
                    if g.empty:
                        ax.text(.5, .5, "No matched observations", transform=ax.transAxes, ha="center")
            fig.legend(handles=[Line2D([], [], color="black", marker="o", ls="none", label="Observed mean ± descriptive SE"),
                                Line2D([], [], color=BLUE, label="Current coupled prediction")],
                       loc="outside lower center", ncol=2, frameon=False)
            save_figure(fig, f"current_Figure_S{number}_{crop}_{variable}_seasonal_curves",
                f"Figure S{number}. Current conditional {crop} {label.lower()} comparisons by site. "
                "Observations and selected-model predictions use matching windows and the original "
                "whole site-year partitions. Cases receive equal weights within site-years, and "
                "site-years receive equal weights within bins. Error bars are descriptive standard "
                "errors across site-year means; single-year bins have no interval. Missing bins remain "
                "gaps. Exact available counts and scores accompany the figures. Unconfirmed matched "
                "irrigation logs limit management interpretation; station observations do not enter "
                "the current water fit and growth coefficients retain their earlier multisite history.")
            number += 1
    harvest = station[station.variable.isin(["yield", "harvest_biomass"])].copy()
    harvest["year"] = harvest.source_group_id.str.rsplit("-", n=1).str[-1].astype(int)
    grouped = harvest.groupby(["site", "crop", "split", "year", "variable"]).agg(
        observed=("value", "mean"), predicted=("predicted", "mean"),
        replicates=("observation_id", "size"), observed_sd=("value", "std")).reset_index()
    grouped["observed_SE"] = grouped.observed_sd / np.sqrt(grouped.replicates)
    assert grouped.site.eq("Yucheng").all() and grouped.replicates.eq(6).all()
    assert not grouped[grouped.crop.eq("maize")].year.isin([2012, 2017]).any()
    export(grouped, "annual_station_harvest_sources")
    fig, axes = plt.subplots(2, 2, figsize=(9., 6.7), layout="constrained")
    for row, (variable, label) in enumerate([("harvest_biomass", "Aboveground dry biomass (t ha⁻¹)"),
                                           ("yield", "Dry grain (t ha⁻¹)")]):
        for col, crop in enumerate(["wheat", "maize"]):
            ax = axes[row, col]; g = grouped[grouped.crop.eq(crop) & grouped.variable.eq(variable)].sort_values("year")
            for split, marker in [("calibration", "o"), ("validation", "s")]:
                p = g[g.split.eq(split)]
                ax.errorbar(p.year, p.observed*.001, yerr=p.observed_SE*.001, fmt=marker,
                            color="black", ms=4, capsize=2, lw=.8)
                ax.plot(p.year, p.predicted*.001, marker=marker, ls="none", color=BLUE, ms=4)
            ax.set_ylim(0, max((g.observed+g.observed_SE).max(), g.predicted.max())*.001*1.1)
            ax.set_xlabel("Archived crop-season group year"); ax.set_ylabel(label)
            ax.set_xticks([int(g.year.min()), 2010, 2016, int(g.year.max())])
            style(ax, chr(97+row*2+col), crop.capitalize()+" · Yucheng")
    fig.legend(handles=[Line2D([], [], color="black", marker="o", ls="none", label="Observed mean ± replicate SE"),
                        Line2D([], [], color=BLUE, marker="o", ls="none", label="Current prediction"),
                        Line2D([], [], color="grey", marker="o", ls="none", label="Prior calibration partition"),
                        Line2D([], [], color="grey", marker="s", ls="none", label="Retrospective evaluation")],
               loc="outside lower center", ncol=2, frameon=False)
    save_figure(fig, "current_Figure_S6_Yucheng_harvest_comparisons",
        "Figure S6. Current conditional Yucheng harvest comparisons. Black points show observed "
        "means ± standard errors among six quadrat samples per crop-year; blue points show current "
        "coupled-model predictions at the same sampling windows. Circles and squares distinguish "
        "the original calibration and retrospective-evaluation partitions. Grain and total aboveground "
        "biomass are dry matter. The duplicated maize 2012 and 2017 source vectors remain excluded. "
        "Within-year replicates describe spatial sampling variation and are not independent crop seasons. "
        "Management completeness remains unconfirmed and current water coefficients use Wuqiao fitting "
        "targets, while growth coefficients retain their earlier multisite fitting history.")


def main():
    TABLES.mkdir(parents=True, exist_ok=True); FIGURES.mkdir(parents=True, exist_ok=True)
    frozen = json.loads((CAL / "parameters/frozen_model.json").read_text())
    assert frozen["selected_version"] == "management_refit"
    assert frozen["testing_used_for_selection"] is False and frozen["testing_retrospective"]
    station = pd.read_csv(CAL / "predictions/station_comparisons.csv", low_memory=False)
    station = station[station.version.eq("management_refit")].copy()
    field = pd.read_csv(CAL / "predictions/field_comparisons.csv")
    field = field[field.version.eq("management_refit")].copy()
    inv = pd.read_csv(CAL / "data/case_inventory.csv")
    assert len(station) == station.observation_id.nunique() == 7397
    assert len(field) == field.case_id.nunique() == 32 and field.et_eligible.all()
    assert station.site.nunique() == 5 and len(inv) == 132
    assert station.groupby("source_group_id").split.nunique().eq(1).all()
    parameter_rows = parameters(frozen)
    metrics, matched, field_summary, inventory = tables(station, field, inv)
    figure3(station, field, inventory)
    figure4(station, inventory, metrics)
    figure5(field)
    supplementary_figures(station)
    sources = [CAL / "parameters/frozen_model.json", CAL / "predictions/station_comparisons.csv",
               CAL / "predictions/field_comparisons.csv", CAL / "data/wuqiao_digitized_annual_yields.csv",
               Path(__file__)]
    write_json(TABLES / "current_crop_publication_receipt.json", dict(
        selected_version="management_refit", source_sha256={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
        station_observations=7397, station_cases=len(inv), scored_station_cases=station.case_id.nunique(),
        station_sites=sorted(station.site.unique()), field_cases=32,
        field_fit_years=[2016, 2017, 2018], field_retrospective_testing_years=[2019],
        free_water_parameters_per_crop=8, current_station_metrics_independently_reproduced=True,
        dry_grain_to_13pct_conversion="divide by 0.87 once", biomass_moisture_conversion=False,
        shared_effective_parameter_rows=len(parameter_rows), figure_png_pdf_pairs=10,
        current_phenology_prediction_claims=False,
        numerical_conservation_is_not_field_accuracy=True,
        regional_policy_evaluation_is_retrospective_scenario_comparison=True))
    print(json.dumps(dict(selected_version="management_refit", station_records=len(station),
                         field_cases=len(field), shared_parameter_rows=len(parameter_rows),
                         figure_pairs=10, independent_station_metric_check=True)))


if __name__ == "__main__":
    main()
