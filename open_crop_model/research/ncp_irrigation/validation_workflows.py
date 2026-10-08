"""Explicit continuous-rotation and matched regional uncertainty workflows."""

from copy import deepcopy
from pathlib import Path
import json
import numpy as np
import pandas as pd
from core.hydrology.types import bounded
from .model import simulate_season, simulate_rotation, initial_water_state
from .evaluation import pair_case, field_scores
from .groundwater import delayed_recharge
from .economics import annual_cost, period_cost
from .metrics import interval_summary
from .regional import simulate_zones, aggregate_zones, ranking_robustness


def resolve_rotations(specs, cases):
    indexed = {case["case_id"]: case for case in cases}
    resolved = deepcopy(specs)
    for spec in resolved:
        seasons = []
        for reference in spec["seasons"]:
            if reference.get("inputs", {}).get("crop") == "fallow":
                seasons.append(deepcopy(reference))
                continue
            identifier = reference["case_id"]
            if identifier not in indexed:
                raise ValueError("Rotation requires registered case references")
            case = indexed[identifier]
            if case["split"] != "validation":
                raise ValueError(
                    "Rotation requires independent validation case references"
                )
            seasons.append(deepcopy(case))
        spec["seasons"] = seasons
    return resolved


def evaluate_rotations(specs, parameters, seed):
    rows, pairs, cases = [], [], []
    sensitivity_ok = bool(specs)
    for spec in specs:
        if (
            spec.get("management_complete") is not True
            or spec.get("plot_alignment_confirmed") is not True
        ):
            sensitivity_ok = False
            continue
        seasons = deepcopy(spec["seasons"])
        if any(
            c.get("split") != "validation"
            for c in seasons
            if c.get("inputs", {}).get("crop") != "fallow"
        ):
            raise ValueError(
                "Rotation validation requires independent validation seasons"
            )
        for case in seasons:
            if "inputs_path" in case:
                case["inputs"] = json.loads(Path(case["inputs_path"]).read_text())
        if not {"wheat", "maize"} <= {c["inputs"]["crop"] for c in seasons}:
            sensitivity_ok = False
            continue
        forcing = [c["inputs"] for c in seasons]
        result = simulate_rotation(forcing, parameters, initial_water_state(forcing[0]))
        for case, season_result in zip(seasons, result):
            if case["inputs"]["crop"] == "fallow":
                continue
            observations = pd.read_csv(case["observations_path"])
            pairs.extend(pair_case(case, season_result, observations))
            cases.append(case)
        variants = spec.get("initial_theta_variants", [])
        if len(variants) < 2 or not spec.get("initial_sensitivity_targets"):
            sensitivity_ok = False
        summaries = []
        for theta in variants:
            x = deepcopy(forcing)
            x[0]["initial_theta"] = theta
            variants_result = simulate_rotation(
                x, parameters, initial_water_state(x[0])
            )
            summary = dict(
                rotation_id=spec["rotation_id"],
                structure="initial_state_sensitivity",
                evidence="independent_rotation_input_sensitivity",
                et_mm=sum(r.summary["et_mm"] for r in variants_result),
                yield_kg_ha=sum(r.summary["yield_kg_ha"] for r in variants_result),
                final_storage_mm=variants_result[-1].summary["final_storage_mm"],
                detail=json.dumps({"initial_theta": theta}),
                status="diagnostic",
            )
            rows.append(summary)
            summaries.append(summary)
        if summaries and spec.get("initial_sensitivity_targets"):
            targets = spec["initial_sensitivity_targets"]
            for variable, name in [
                ("et_mm", "et_relative_range_max"),
                ("yield_kg_ha", "yield_relative_range_max"),
                ("final_storage_mm", "final_storage_range_mm_max"),
            ]:
                values = [s[variable] for s in summaries]
                spread = max(values) - min(values)
                if variable != "final_storage_mm":
                    spread /= max(abs(float(np.mean(values))), 1e-8)
                sensitivity_ok &= spread <= bounded(targets[name], name)
    scored, gate = field_scores(cases, pairs, seed)
    rows.extend(
        dict(
            structure="continuous_rotation",
            evidence="independent_continuous_rotation_metrics",
            **r,
        )
        for r in scored
    )
    if gate == "fail":
        return rows, "fail"
    return (
        rows,
        "pass" if gate == "pass" and sensitivity_ok else "insufficient_evidence",
    )


def withdrawal_from_events(inputs, zone_fraction):
    """Use pump meters/event conversion first; explicit zone fallback second."""
    pump = 0.0
    for event in inputs.get("irrigation_events", []):
        if event["measurement_location"] == "pump":
            pump += event["amount_mm"]
        else:
            fraction = event.get("conveyance_fraction")
            if fraction is None:
                fraction = zone_fraction
            pump += event["amount_mm"] / bounded(
                fraction, "conveyance_fraction", 1e-9, 1
            )
    return pump


def observed_uncertainty(specs, cases, parameters, seed):
    indexed = {c["case_id"]: c for c in cases}
    rows = []
    for spec in specs:
        case = indexed[spec["case_id"]]
        if case["split"] == "excluded":
            continue
        draws = spec["joint_draws"]
        if len(draws) < 200:
            raise ValueError(
                "At least 200 declared matched joint uncertainty draws required"
            )
        if spec.get("distribution_source") not in {
            "registered_independent_prior",
            "frozen_calibration_distribution",
        }:
            raise ValueError("Uncertainty distribution provenance required")
        if spec[
            "distribution_source"
        ] == "frozen_calibration_distribution" and not spec.get(
            "calibration_source_groups"
        ):
            raise ValueError("Calibration distribution requires training provenance")
        if set(spec.get("calibration_source_groups", [])) & {
            c["source_group_id"] for c in cases if c["split"] == "validation"
        }:
            raise ValueError("Uncertainty calibration provenance overlaps validation")
        values = {name: {} for name in spec["technologies"]}
        for draw in draws:
            common = deepcopy(case["inputs"])
            allowed = {
                "weather",
                "soil_layers",
                "initial_theta",
                "irrigation_events",
                "wind_height_m",
                "et0_method",
            }
            if set(draw.get("shared_inputs", {})) - allowed:
                raise ValueError("Unregistered shared uncertainty input")
            common.update(deepcopy(draw.get("shared_inputs", {})))
            for name, technology in sorted(spec["technologies"].items()):
                x = deepcopy(common)
                x["technology"] = deepcopy(technology)
                x["technology"].update(
                    deepcopy(draw.get("technology_overrides", {}).get(name, {}))
                )
                p = deepcopy(draw.get("parameters", parameters))
                r = simulate_season(x, p)
                output = {
                    key: r.summary[key]
                    for key in ["yield_kg_ha", "et_mm", "bottom_drainage_mm"]
                }
                boundary = draw["source"]
                pump = withdrawal_from_events(x, boundary["conveyance_fraction"])
                field = r.summary["irrigation_field_mm"]
                gw_fraction = bounded(
                    boundary["groundwater_fraction"], "groundwater_fraction", 0, 1
                )
                recharge = delayed_recharge(
                    [d["bottom_drainage_mm"] for d in r.daily],
                    boundary["recharge_kernel"],
                    boundary.get("previous_recharge_tail_mm", ()),
                )
                output["groundwater_withdrawal_mm"] = pump * gw_fraction
                output["net_pumping_mm"] = pump * gw_fraction - sum(
                    recharge["recharge_mm"]
                )
                output["agricultural_gw_balance_mm"] = (
                    sum(recharge["recharge_mm"])
                    - pump * gw_fraction
                    - r.summary["capillary_rise_mm"]
                )
                output["pending_recharge_mm"] = recharge["pending_after_period_mm"]
                if draw.get("cost_by_method"):
                    costs = annual_cost(
                        withdrawal_m3_ha=pump * 10, **draw["cost_by_method"][name]
                    )
                    price = bounded(draw["crop_price_cny_kg"], "crop_price_cny_kg")
                    allocation = bounded(
                        draw["annual_cost_allocation_fraction"],
                        "annual_cost_allocation_fraction",
                        0,
                        1,
                    )
                    other = bounded(draw["other_cost_cny_ha"], "other_cost_cny_ha")
                    output["net_return_cny_ha"] = (
                        r.summary["yield_kg_ha"] * price
                        - period_cost(costs, allocation)
                        - other
                    )
                for key, value in output.items():
                    values[name].setdefault(key, []).append(value)
        for name, variables in values.items():
            for variable, array in variables.items():
                ranking = ranking_robustness(
                    {method: vs[variable] for method, vs in values.items()},
                    higher_is_better=variable
                    in {
                        "yield_kg_ha",
                        "net_return_cny_ha",
                        "agricultural_gw_balance_mm",
                    },
                )
                rows.append(
                    dict(
                        case_id=case["case_id"],
                        method=name,
                        variable=variable,
                        n=len(array),
                        **interval_summary(array),
                        ranking_probability=ranking[name],
                        evidence="conditional_matched_input_uncertainty",
                        distribution_source=spec["distribution_source"],
                        status="conditional_scenario_not_observed_accuracy",
                    )
                )
    return rows


def run_regional_workflow(spec, parameters, status):
    if not spec:
        return [], "insufficient_evidence"
    if any(status.get(stage) != "pass" for stage in ["V0", "V1", "V2"]):
        return [
            dict(
                domain="North China Plain",
                status="insufficient_evidence",
                detail="V0, V1 and V2 prerequisite evidence incomplete",
            )
        ], "insufficient_evidence"
    if spec.get("coverage_confirmed") is not True or not spec.get(
        "validation_observations"
    ):
        return [
            dict(
                domain="North China Plain",
                status="insufficient_evidence",
                detail="Regional driving and independent observation coverage unconfirmed",
            )
        ], "insufficient_evidence"
    if set(spec["validated_methods"]) - set(status.get("validated_methods", [])):
        raise ValueError(
            "Regional method scope exceeds actual independent contrast evidence"
        )
    rows = simulate_zones(
        spec["zones"], spec["parameter_zones"], status, purpose="policy"
    )
    aggregate = aggregate_zones(rows)
    output, results = [], []
    for obs in spec["validation_observations"]:
        if (
            obs.get("independent") is not True
            or obs.get("scope") != spec.get("aggregation_scope")
            or obs.get("period_end_date") != rows[0]["period_end_date"]
        ):
            raise ValueError(
                "Independent regional observation requires matching scope and time"
            )
        variable = obs["variable"]
        if variable not in aggregate or not variable.endswith("_mm"):
            raise ValueError(
                "Region observation operator is unavailable; wells/SIF/GRACE require explicit full-column operators"
            )
        if obs.get("unit") != "mm":
            raise ValueError("Regional water unit must be mm")
        predicted = aggregate[variable]
        error = abs(predicted - obs["value"])
        threshold = bounded(obs["absolute_error_target_mm"], "absolute_error_target_mm")
        result = "pass" if error <= threshold else "fail"
        results.append(result)
        output.append(
            dict(
                domain=obs["scope"],
                variable=variable,
                status=result,
                detail=json.dumps(
                    dict(
                        predicted=predicted,
                        observed=obs["value"],
                        absolute_error_mm=error,
                        target_mm=threshold,
                    )
                ),
            )
        )
    output.append(
        dict(
            domain=spec["aggregation_scope"],
            status="insufficient_evidence",
            detail="Flux checks cover declared agricultural outputs only; independent canopy/yield products, full-column GRACE components and groundwater background remain required for V4",
        )
    )
    return output, "fail" if "fail" in results else "insufficient_evidence"
