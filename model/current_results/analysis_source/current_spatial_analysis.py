"""Area-weighted distribution of sealed regional policy contrasts.

Run with the project environment:
    .venv/bin/python model/current_results/analysis_source/current_spatial_analysis.py

This is a descriptive analysis of existing model output. It does not execute a
model, estimate sampling uncertainty, or treat mapped cells as independent runs.
Exact source snapshots are retained alongside this script; source manifests,
joins, annual means and regional accounting are checked before export.
"""

from __future__ import annotations

import argparse
from collections import deque
import hashlib
import json
from pathlib import Path
import shutil

import numpy as np
import pandas as pd


ANALYSIS_DATE = "2026-10-08"
SIGN_EPS_KG_HA = 1e-6
YEARS = list(range(2014, 2026))
CUTS = (0.25, 0.50, 0.75)
QUANTILES = {"q05": .05, "q10": .10, "q25": .25, "q50": .50,
             "q75": .75, "q90": .90, "q95": .95}
INPUTS = {
    "cells": "tables/current_source_cell_map_values.csv",
    "units": "tables/current_spatial_unit_period_means.csv",
    "annual": "tables/spatial_unit_annual_results.csv",
    "mapping": "data/all_source_cell_mapping.csv",
    "contrasts": "tables/policy_contrasts.csv",
    "identifier_semantics": "verification/table_identifier_semantics.json",
    "sealed_manifest": "verification/file_manifest.json",
}
METRICS = ("yield_kg_ha", "irrigation_mm", "modeled_total_et_mm",
           "annual_bottom_drainage_mm")


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False,
                               allow_nan=False) + "\n", encoding="utf-8")


def sign_labels(values: pd.Series) -> np.ndarray:
    return np.select([values.lt(-SIGN_EPS_KG_HA), values.gt(SIGN_EPS_KG_HA)],
                     ["loss", "gain"], default="unchanged")


def weighted_quantiles(values: np.ndarray, weights: np.ndarray) -> dict:
    """Left-continuous inverse of the normalized empirical weighted CDF."""
    ordered = np.argsort(values, kind="stable")
    value_order = values[ordered]
    cdf = np.cumsum(weights[ordered]) / np.sum(weights)
    return {name: float(value_order[min(np.searchsorted(cdf, q, side="left"),
                                       len(values) - 1)])
            for name, q in QUANTILES.items()}


def loss_components(cells: pd.DataFrame, cut: float) -> tuple[list, dict]:
    """Rook connectivity of occupied loss cells, using actual grid indices."""
    loss = cells[cells.outcome_group.eq("loss")]
    coordinate_rows = {(int(r.agera5_lat_index), int(r.agera5_lon_index)): r.Index
                       for r in loss.itertuples()}
    remaining = set(coordinate_rows)
    raw = []
    while remaining:
        start = min(remaining)
        remaining.remove(start)
        queue = deque([start])
        rows = []
        while queue:
            coord = queue.popleft()
            rows.append(coordinate_rows[coord])
            for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                neighbour = (coord[0] + dr, coord[1] + dc)
                if neighbour in remaining:
                    remaining.remove(neighbour)
                    queue.append(neighbour)
        g = cells.loc[rows]
        area = float(g.used_area_ha.sum())
        raw.append((area, sorted(rows), g))
    raw.sort(key=lambda entry: (-entry[0], str(entry[2].zone_id.min())))
    records, assignments = [], {}
    for rank, (area, rows, g) in enumerate(raw, 1):
        records.append(dict(
            reduction_fraction=cut, component_rank=rank,
            mapped_source_cell_count=len(g),
            representative_unit_count=int(g.representative_id.nunique()),
            rotation_area_ha=area, rotation_area_Mha=area / 1e6,
            pct_of_all_loss_area=100 * area / float(loss.used_area_ha.sum()),
            pct_of_total_rotation_area=100 * area / float(cells.used_area_ha.sum()),
            latitude_cell_center_min_deg=float(g.latitude.min()),
            latitude_cell_center_max_deg=float(g.latitude.max()),
            longitude_cell_center_min_deg=float(g.longitude.min()),
            longitude_cell_center_max_deg=float(g.longitude.max()),
            mean_delta_grain_t_ha_yr=float(np.average(g.delta_yield_kg_ha,
                                                     weights=g.used_area_ha)) / 1000,
            regional_grain_contribution_Mt_yr=float(np.sum(
                g.used_area_ha * g.delta_yield_kg_ha)) / 1e9,
        ))
        assignments.update({index: rank for index in rows})
    return records, assignments


def narrative(statistics: dict) -> dict:
    s25, s, s75 = (statistics["by_reduction_fraction"][f"{cut:.2f}"]
                  for cut in CUTS)
    loss, unchanged, gain = (s["groups"][name]
                             for name in ("loss", "unchanged", "gain"))
    q = s["weighted_quantiles_delta_grain_t_ha_yr"]
    largest = s["largest_loss_component"]
    return {
        "results_section_title": "Spatial distribution and persistence of allocation gains and losses",
        "results_paragraphs": [
            f"At the 50% regional irrigation reduction, targeted allocation increased mean annual combined wheat–maize dry grain relative to uniform reduction on {gain['area_pct']:.2f}% of the mapped rotation area ({gain['rotation_area_Mha']:.3f} Mha), decreased it on {loss['area_pct']:.2f}% ({loss['rotation_area_Mha']:.3f} Mha), and left it unchanged on {unchanged['area_pct']:.2f}% ({unchanged['rotation_area_Mha']:.3f} Mha). Mean contrasts within the gain and loss areas were +{gain['mean_delta_grain_t_ha_yr']:.3f} and {loss['mean_delta_grain_t_ha_yr']:.3f} t ha⁻¹ yr⁻¹, respectively. Their regional contributions were +{gain['regional_grain_contribution_Mt_yr']:.3f} and {loss['regional_grain_contribution_Mt_yr']:.3f} Mt yr⁻¹, giving a net gain of {s['net_grain_contribution_Mt_yr']:.3f} Mt yr⁻¹ ({s['mean_delta_grain_t_ha_yr']:.3f} t ha⁻¹ yr⁻¹ across the full mapped area). Local losses offset {s['negative_to_positive_grain_contribution_pct']:.2f}% of the gross positive contribution.",
            f"The area-weighted median grain contrast was {q['q50']:.3f} t ha⁻¹ yr⁻¹, and the 10th and 90th percentiles were {q['q10']:.3f} and +{q['q90']:.3f} t ha⁻¹ yr⁻¹. Thus, the positive regional mean coexisted with a substantial lower tail of local losses. Relative to uniform reduction, mean annual field irrigation increased by {gain['mean_delta_irrigation_mm_yr']:.1f} mm in the gain area and decreased by {abs(loss['mean_delta_irrigation_mm_yr']):.1f} mm in the loss area. The corresponding modeled total evapotranspiration contrasts were +{gain['mean_delta_total_et_mm_yr']:.1f} and {loss['mean_delta_total_et_mm_yr']:.1f} mm yr⁻¹. The irrigation transfers balanced at regional scale, whereas the evapotranspiration contrasts summed to a net increase of {s['mean_delta_total_et_mm_yr']:.1f} mm yr⁻¹.",
            f"Local grain losses were persistent over 2014–2025: {s['area_pct_loss_all_12_years']:.2f}% of the mapped rotation area lost grain in all 12 years, and {s['area_pct_ever_loss']:.2f}% experienced a loss in at least one year. Every response unit with a negative period-mean contrast recorded losses in at least {s['minimum_loss_years_among_mean_loss_units']} years; all response units with a positive period-mean contrast recorded gains in all 12 years. Across years, the mean annual area exposed to a loss was {s['mean_annual_loss_area_pct']:.2f}%. Loss cells formed {s['loss_component_count']} connected grid-cell footprints under rook adjacency. The largest contained {largest['rotation_area_Mha']:.3f} Mha of mapped rotation area ({largest['pct_of_all_loss_area']:.2f}% of all loss area), with occupied cell centers extending from {largest['latitude_cell_center_min_deg']:.1f} to {largest['latitude_cell_center_max_deg']:.1f}°N and {largest['longitude_cell_center_min_deg']:.1f} to {largest['longitude_cell_center_max_deg']:.1f}°E.",
            f"The distributional tradeoff increased as the regional irrigation budget tightened. Mean grain losses occupied {s25['groups']['loss']['area_pct']:.2f}%, {loss['area_pct']:.2f}%, and {s75['groups']['loss']['area_pct']:.2f}% of mapped rotation area at reductions of 25%, 50%, and 75%, respectively. At the 75% reduction, the area-weighted median contrast became negative ({s75['weighted_quantiles_delta_grain_t_ha_yr']['q50']:.3f} t ha⁻¹ yr⁻¹), although gains on {s75['groups']['gain']['area_pct']:.2f}% of the area produced a positive regional balance. Gross positive and negative contributions at that reduction were +{s75['groups']['gain']['regional_grain_contribution_Mt_yr']:.3f} and {s75['groups']['loss']['regional_grain_contribution_Mt_yr']:.3f} Mt yr⁻¹, leaving a net gain of {s75['net_grain_contribution_Mt_yr']:.3f} Mt yr⁻¹, below the net gain at the 50% reduction.",
        ],
        "methods_paragraph": (
            "Spatial distribution statistics describe targeted-minus-uniform policy contrasts in mean annual combined wheat–maize dry grain and water fluxes over 2014–2025. The 3,641 mapped 0.1° source cells carry outcomes from 32 representative response units. Cell weights are the static 2020 mapped rotation areas after cropland-fraction correction; they represent mapped rotation area rather than observed harvested area. Gains and losses exceed +10⁻⁶ and fall below −10⁻⁶ kg ha⁻¹, respectively; contrasts within these bounds are classified as unchanged. Group means use rotation-area weights, regional contributions sum area multiplied by the corresponding contrast, and weighted quantiles use the inverse empirical cumulative area distribution. Annual loss frequencies use the same sign threshold across the 12 years. Connected loss footprints comprise occupied cells sharing a horizontal or vertical grid edge (rook adjacency). The statistics describe the conditional modeled allocation footprint; mapped cells do not provide independent replication or cell-specific validation."
        ),
        "figure_panel_d_source": "publication/tables/current_spatial_distribution.csv",
        "figure_panel_d_scope": "reduction_fraction == 0.5; groups loss, unchanged, gain",
        "supporting_table": "publication/tables/current_spatial_loss_components.csv",
    }


def main(root: Path) -> None:
    root = root.resolve()
    regional = root / "regional"
    snapshot_dir = root / "analysis_source/spatial_analysis_inputs_20261008"
    tables = root / "publication/tables"
    tables.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((regional / INPUTS["sealed_manifest"]).read_text())
    snapshots, source_receipt, checks = {}, {}, {}
    for name, relative in INPUTS.items():
        source = regional / relative
        digest = sha256(source)
        if name != "sealed_manifest":
            assert digest == manifest[relative]["sha256"], f"Changed sealed input: {relative}"
        target = snapshot_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            assert sha256(target) == digest, f"Existing snapshot differs: {target}"
        else:
            shutil.copy2(source, target)
        assert sha256(target) == digest
        snapshots[name] = target
        source_receipt[name] = dict(source=str(source.relative_to(root)),
            snapshot=str(target.relative_to(root)), sha256=digest,
            bytes=source.stat().st_size, sealed_manifest_matched=name != "sealed_manifest")
    checks["sealed_sources_match_manifest_and_exact_snapshots"] = True

    cells, units, annual, mapping, contrasts = (pd.read_csv(snapshots[name])
        for name in ("cells", "units", "annual", "mapping", "contrasts"))
    assert len(cells) == 3641 and cells.zone_id.is_unique
    assert cells.representative_id.nunique() == 32
    assert set(units.parameter_set) == set(annual.parameter_set) == {"source_screened"}
    assert not units.duplicated(["reduction_fraction", "representative_id"]).any()
    assert not annual.duplicated(["reduction_fraction", "representative_id", "harvest_year"]).any()
    assert set(annual.harvest_year) == set(YEARS)
    assert set(units.reduction_fraction) == set(annual.reduction_fraction) == set(CUTS)
    assert annual.groupby(["reduction_fraction", "representative_id"]).size().eq(12).all()
    assert cells.used_area_ha.gt(0).all()
    assert not cells.isna().any().any() and not units.isna().any().any()
    assert not annual.isna().any().any()
    assert np.isfinite(cells.select_dtypes("number").to_numpy()).all()
    assert np.isfinite(units.select_dtypes("number").to_numpy()).all()
    assert np.isfinite(annual.select_dtypes("number").to_numpy()).all()
    checks["complete_unique_cells_units_12_years_and_three_cuts"] = True

    grid = mapping[["zone_id", "representative_id", "latitude", "longitude",
                    "used_area_ha", "agera5_lat_index", "agera5_lon_index"]]
    joined = cells.merge(grid, on="zone_id", suffixes=("", "_source"),
                         validate="one_to_one", indicator=True)
    assert joined._merge.eq("both").all() and len(joined) == len(grid)
    for column in ("representative_id", "latitude", "longitude", "used_area_ha"):
        np.testing.assert_allclose(joined[column], joined[column + "_source"],
                                   rtol=0, atol=1e-9)
    assert not grid.duplicated(["agera5_lat_index", "agera5_lon_index"]).any()
    np.testing.assert_allclose(grid.latitude + .1 * grid.agera5_lat_index, 42.7,
                               rtol=0, atol=1e-9)
    np.testing.assert_allclose(grid.longitude - .1 * grid.agera5_lon_index, 110.2,
                               rtol=0, atol=1e-9)
    cell_base = grid.copy()
    area_total = float(cells.used_area_ha.sum())
    area_by_unit = cells.groupby("representative_id").used_area_ha.sum()
    checks["one_to_one_source_grid_join_and_actual_0_1_degree_grid"] = True

    distributions, components, frequencies, annual_exposures = [], [], [], []
    by_cut, accounting_max_errors = {}, {}
    cell_output = None
    for cut in CUTS:
        u = units[units.reduction_fraction.eq(cut)].sort_values("representative_id")
        a = annual[annual.reduction_fraction.eq(cut)]
        assert len(u) == 32
        np.testing.assert_allclose(u.represented_area_ha,
            area_by_unit.loc[u.representative_id], rtol=0, atol=1e-8)
        mean = a.groupby("representative_id").mean(numeric_only=True)
        for metric in METRICS:
            for prefix in ("targeted_", "uniform_", "delta_"):
                column = prefix + metric
                np.testing.assert_allclose(u[column], mean.loc[u.representative_id, column],
                                           rtol=1e-12, atol=1e-8)
            np.testing.assert_allclose(a["delta_" + metric],
                a["targeted_" + metric] - a["uniform_" + metric], rtol=1e-12, atol=1e-8)
        count = a.assign(gain=a.delta_yield_kg_ha.gt(SIGN_EPS_KG_HA),
            loss=a.delta_yield_kg_ha.lt(-SIGN_EPS_KG_HA)).groupby("representative_id")
        for label in ("gain", "loss"):
            np.testing.assert_array_equal(u[f"grain_{label}_years"],
                count[label].sum().loc[u.representative_id])
        x = cell_base.merge(u, on="representative_id", validate="many_to_one")
        assert len(x) == len(cells)
        x["outcome_group"] = sign_labels(x.delta_yield_kg_ha)
        if cut == .5:
            compare = cells.merge(x, on="zone_id", suffixes=("_map", "_unit"),
                                  validate="one_to_one")
            for metric in METRICS:
                np.testing.assert_allclose(compare["source_screened_delta_" + metric],
                    compare["delta_" + metric], rtol=1e-12, atol=1e-8)
            for label in ("gain", "loss"):
                np.testing.assert_array_equal(compare[f"source_screened_grain_{label}_years"],
                                               compare[f"grain_{label}_years"])

        groups = {}
        for label in ("loss", "unchanged", "gain"):
            g = x[x.outcome_group.eq(label)]
            w = g.used_area_ha
            area = float(w.sum())
            row = dict(reduction_fraction=cut, outcome_group=label,
                mapped_source_cell_count=len(g),
                representative_unit_count=int(g.representative_id.nunique()),
                rotation_area_ha=area, rotation_area_Mha=area / 1e6,
                area_fraction=area / area_total, area_pct=100 * area / area_total,
                mean_delta_grain_kg_ha_yr=float(np.average(g.delta_yield_kg_ha, weights=w)),
                mean_delta_grain_t_ha_yr=float(np.average(g.delta_yield_kg_ha, weights=w)) / 1000,
                mean_delta_irrigation_mm_yr=float(np.average(g.delta_irrigation_mm, weights=w)),
                mean_delta_total_et_mm_yr=float(np.average(g.delta_modeled_total_et_mm, weights=w)),
                mean_delta_bottom_drainage_mm_yr=float(np.average(g.delta_annual_bottom_drainage_mm, weights=w)),
                regional_grain_contribution_t_yr=float(np.sum(w * g.delta_yield_kg_ha)) / 1000,
                regional_grain_contribution_Mt_yr=float(np.sum(w * g.delta_yield_kg_ha)) / 1e9,
                regional_irrigation_contribution_m3_yr=float(np.sum(w * g.delta_irrigation_mm)) * 10,
                regional_total_et_contribution_m3_yr=float(np.sum(w * g.delta_modeled_total_et_mm)) * 10,
                mean_grain_gain_years=float(np.average(g.grain_gain_years, weights=w)),
                mean_grain_loss_years=float(np.average(g.grain_loss_years, weights=w)),
                grain_gain_years_min=int(g.grain_gain_years.min()),
                grain_gain_years_max=int(g.grain_gain_years.max()),
                grain_loss_years_min=int(g.grain_loss_years.min()),
                grain_loss_years_max=int(g.grain_loss_years.max()))
            distributions.append(row)
            groups[label] = row
        np.testing.assert_allclose(sum(row["rotation_area_ha"] for row in groups.values()),
                                   area_total, rtol=0, atol=1e-7)
        comp, assignments = loss_components(x, cut)
        components.extend(comp)
        np.testing.assert_allclose(sum(row["rotation_area_ha"] for row in comp),
                                   groups["loss"]["rotation_area_ha"], rtol=0, atol=1e-7)
        if cut == .5:
            x["loss_component_rank"] = pd.Series(assignments).reindex(x.index).astype("Int64")
            cell_output = x[["zone_id", "representative_id", "latitude", "longitude",
                "agera5_lat_index", "agera5_lon_index", "used_area_ha", "outcome_group",
                "delta_yield_kg_ha", "delta_irrigation_mm", "delta_modeled_total_et_mm",
                "grain_gain_years", "grain_loss_years", "loss_component_rank"]]

        for loss_years, g in x.groupby("grain_loss_years"):
            frequencies.append(dict(reduction_fraction=cut, grain_loss_years=int(loss_years),
                mapped_source_cell_count=len(g), representative_unit_count=int(g.representative_id.nunique()),
                rotation_area_ha=float(g.used_area_ha.sum()),
                area_pct=100 * float(g.used_area_ha.sum()) / area_total))
        for year, g in a.groupby("harvest_year"):
            g = g.copy()
            g["outcome_group"] = sign_labels(g.delta_yield_kg_ha)
            for label in ("loss", "unchanged", "gain"):
                h = g[g.outcome_group.eq(label)]
                annual_exposures.append(dict(reduction_fraction=cut, harvest_year=int(year),
                    outcome_group=label, representative_unit_count=len(h),
                    rotation_area_ha=float(h.represented_area_ha.sum()),
                    area_pct=100 * float(h.represented_area_ha.sum()) / area_total))
        c = contrasts[contrasts.reduction_fraction.eq(cut) & contrasts.period.eq("testing")]
        assert set(c.harvest_year) == set(YEARS)
        check_annual = a.assign(grain_t=a.delta_yield_kg_ha * a.represented_area_ha / 1000,
            et_m3=a.delta_modeled_total_et_mm * a.represented_area_ha * 10,
            irrigation_m3=a.delta_irrigation_mm * a.represented_area_ha * 10).groupby("harvest_year").sum(numeric_only=True)
        for annual_column, regional_column in (("grain_t", "additional_grain_t"),
                ("et_m3", "additional_et_m3"), ("irrigation_m3", "field_water_difference_m3")):
            residual = check_annual[annual_column] - c.set_index("harvest_year")[regional_column]
            accounting_max_errors[f"{cut:.2f}_{annual_column}"] = float(residual.abs().max())
            np.testing.assert_allclose(check_annual[annual_column],
                c.set_index("harvest_year")[regional_column], rtol=1e-12, atol=1e-4)
        net_grain = float(np.sum(x.used_area_ha * x.delta_yield_kg_ha)) / 1e9
        np.testing.assert_allclose(net_grain, c.additional_grain_t.mean() / 1e6,
                                   rtol=1e-12, atol=1e-10)
        mean_delta_irrigation = float(np.average(x.delta_irrigation_mm, weights=x.used_area_ha))
        assert abs(mean_delta_irrigation) < 1e-10
        by_cut[f"{cut:.2f}"] = dict(groups=groups,
            mean_delta_grain_t_ha_yr=net_grain * 1e6 / area_total,
            net_grain_contribution_Mt_yr=net_grain,
            mean_delta_irrigation_mm_yr=mean_delta_irrigation,
            mean_delta_total_et_mm_yr=float(np.average(x.delta_modeled_total_et_mm, weights=x.used_area_ha)),
            negative_to_positive_grain_contribution_pct=100 * abs(groups["loss"]["regional_grain_contribution_Mt_yr"]) / groups["gain"]["regional_grain_contribution_Mt_yr"],
            weighted_quantiles_delta_grain_t_ha_yr={name: value / 1000 for name, value in
                weighted_quantiles(x.delta_yield_kg_ha.to_numpy(), x.used_area_ha.to_numpy()).items()},
            area_pct_loss_all_12_years=100 * float(x.loc[x.grain_loss_years.eq(12), "used_area_ha"].sum()) / area_total,
            area_pct_ever_loss=100 * float(x.loc[x.grain_loss_years.gt(0), "used_area_ha"].sum()) / area_total,
            area_pct_gain_all_12_years=100 * float(x.loc[x.grain_gain_years.eq(12), "used_area_ha"].sum()) / area_total,
            mean_annual_loss_area_pct=100 * float(np.average(x.grain_loss_years, weights=x.used_area_ha)) / 12,
            minimum_loss_years_among_mean_loss_units=groups["loss"]["grain_loss_years_min"],
            loss_component_count=len(comp), largest_loss_component=comp[0])
    checks["annual_means_counts_and_map_values_reproduced"] = True
    checks["source_cell_and_representative_area_weights_reconcile"] = True
    checks["annual_regional_grain_et_and_irrigation_accounting_reconcile"] = True
    checks["sign_groups_and_connected_components_partition_loss_area"] = True
    checks["targeted_and_uniform_regional_field_irrigation_equal"] = True

    statistics = dict(analysis_date=ANALYSIS_DATE, results_classification="descriptive conditional regional scenarios",
        parameter_identifier="source_screened", policy_contrast="refit_targeted_minus_uniform",
        period_years=YEARS, mapped_source_cell_count=len(cells), representative_response_unit_count=32,
        total_mapped_rotation_area_ha=area_total,
        total_mapped_rotation_area_Mha=area_total / 1e6,
        sign_threshold_kg_ha=SIGN_EPS_KG_HA,
        weighted_quantile_definition="inf{x: cumulative mapped rotation area at values <= x reaches q}",
        spatial_connectivity="Rook adjacency on occupied 0.1-degree AgERA5 source grid indices; no diagonal adjacency",
        area_basis=str(mapping.area_basis.iloc[0]),
        inferential_scope="32 representative response units mapped to 3,641 cells; no p values or independent cell-level replication",
        by_reduction_fraction=by_cut)
    outputs = {
        "distribution": tables / "current_spatial_distribution.csv",
        "statistics": tables / "current_spatial_statistics.json",
        "components": tables / "current_spatial_loss_components.csv",
        "loss_frequencies": tables / "current_spatial_loss_frequency.csv",
        "annual_exposure": tables / "current_spatial_annual_exposure.csv",
        "component_cells": tables / "current_spatial_component_cells_50pct.csv",
        "narrative": root / "analysis_source/spatial_analysis_narrative.json",
    }
    pd.DataFrame(distributions).to_csv(outputs["distribution"], index=False)
    write_json(outputs["statistics"], statistics)
    pd.DataFrame(components).to_csv(outputs["components"], index=False)
    pd.DataFrame(frequencies).to_csv(outputs["loss_frequencies"], index=False)
    pd.DataFrame(annual_exposures).to_csv(outputs["annual_exposure"], index=False)
    assert cell_output is not None
    cell_output.to_csv(outputs["component_cells"], index=False)
    write_json(outputs["narrative"], narrative(statistics))
    for name, relative in INPUTS.items():
        assert sha256(regional / relative) == source_receipt[name]["sha256"]
    checks["sealed_inputs_unchanged_after_analysis"] = True
    receipt = dict(analysis_date=ANALYSIS_DATE, all_checks_passed=all(checks.values()),
        checks=checks, sources=source_receipt,
        script=dict(path=str(Path(__file__).resolve().relative_to(root)), sha256=sha256(Path(__file__))),
        outputs={name: dict(path=str(path.relative_to(root)), sha256=sha256(path),
                            bytes=path.stat().st_size) for name, path in outputs.items()},
        maximum_annual_regional_accounting_residuals=accounting_max_errors,
        statistical_scope=statistics["inferential_scope"],
        model_executed=False, calibration_or_regional_inputs_modified=False)
    write_json(root / "verification/spatial_analysis_20261008.json", receipt)
    print(json.dumps(dict(all_checks_passed=receipt["all_checks_passed"],
        total_rotation_area_Mha=area_total / 1e6,
        net_grain_gain_50pct_Mt_yr=by_cut["0.50"]["net_grain_contribution_Mt_yr"],
        loss_area_50pct_pct=by_cut["0.50"]["groups"]["loss"]["area_pct"],
        largest_connected_loss_area_50pct_Mha=by_cut["0.50"]["largest_loss_component"]["rotation_area_Mha"])))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    main(parser.parse_args().root)
