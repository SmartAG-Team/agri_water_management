"""Identity-aware observation operators and independently gated field scores."""

from datetime import date
import numpy as np
import pandas as pd
from .data import quality_control_observations, depth_average, freeze_split
from .metrics import (
    paired_metrics,
    acceptance,
    bootstrap_by_season,
    seasonal_et_metrics,
    contrast_acceptance,
)

CASE_KEYS = ["site_id", "experiment_id", "season_id", "treatment_id", "source_group_id"]
UNITS = {
    "soil_theta": "m3/m3",
    "et_mm": "mm/day",
    "water_budget_residual_mm": "mm/day",
    "yield_kg_ha": "kg/ha",
    "lai": "m2/m2",
    "biomass_kg_ha": "kg/ha",
    "phenology_days": "days",
}
SUPPORT = {
    "field_mean": "soil_theta",
    "wet_domain_mean": "wet_theta",
    "dry_domain_mean": "dry_theta",
}


def pair_case(case, result, observations):
    required = CASE_KEYS + [
        "replicate_id",
        "date",
        "variable",
        "value",
        "unit",
        "qc_flag",
        "irrigation_method",
        "spatial_support",
    ]
    missing = set(required) - set(observations)
    if missing:
        raise ValueError(f"Canonical observation fields missing: {sorted(missing)}")
    selected = observations.copy()
    for key in CASE_KEYS:
        selected = selected[selected[key].astype(str).eq(str(case[key]))]
    if selected.empty:
        return []
    if selected["replicate_id"].isna().any():
        raise ValueError("Explicit replicate identity required")
    if (
        selected.replicate_id.nunique() > 1
        and case.get("observation_aggregation") != "mean_replicates"
    ):
        raise ValueError(
            "Multiple replicates require an explicit mean_replicates operator"
        )
    method = case["inputs"]["technology"]["method"]
    if not selected.irrigation_method.eq(method).all():
        raise ValueError("Observation irrigation method disagrees with case identity")
    valid = quality_control_observations(selected)
    by_date = {r["date"]: r for r in result.daily}
    records = []
    for obs in valid.to_dict("records"):
        if obs["qc_flag"] != "pass":
            continue
        variable = obs["variable"]
        if variable not in UNITS or obs["unit"] != UNITS[variable]:
            raise ValueError(
                f"Unknown or incompatible observation unit: {variable}/{obs['unit']}"
            )
        if obs["date"] not in by_date:
            raise ValueError("Observation date outside modeled season")
        row = by_date[obs["date"]]
        support = obs.get("spatial_support", "field_mean")
        if variable == "soil_theta":
            if support not in SUPPORT:
                raise ValueError(
                    "Point sampling needs a verified domain-to-field support operator"
                )
            theta = row[SUPPORT[support]]
            if not theta:
                raise ValueError("Observation support refers to an inactive domain")
            prediction = depth_average(
                theta,
                [l["thickness_mm"] for l in case["inputs"]["soil_layers"]],
                obs["depth_top_cm"],
                obs["depth_bottom_cm"],
            )
        elif variable == "water_budget_residual_mm":
            from .et_observations import water_budget_residual
            if support!='field_mean':raise ValueError('Budget residual requires field-mean support')
            if obs.get('window_end')!=obs['date'] or not isinstance(obs.get('window_start'),str):
                raise ValueError('Budget residual requires an explicit interval ending on observation date')
            prediction=water_budget_residual(result.daily,
                window_start=obs['window_start'],window_end=obs['window_end'],
                profile_depth_cm=sum(l['thickness_mm'] for l in case['inputs']['soil_layers'])/10.,
                observed_depth_cm=(obs.get('depth_top_cm'),obs.get('depth_bottom_cm')))
        elif variable == "phenology_days":
            event = obs.get("phenology_event")
            if event not in {"anthesis", "maturity"}:
                raise ValueError(
                    "Phenology observation requires anthesis or maturity event"
                )
            when = result.summary.get(event + "_date")
            prediction = (
                (
                    date.fromisoformat(when)
                    - date.fromisoformat(case["inputs"]["start_date"])
                ).days
                + 1
                if when
                else float("nan")
            )
        else:
            if variable == "yield_kg_ha" and obs["date"] != case["inputs"]["end_date"]:
                raise ValueError("Yield must align with declared harvest/end date")
            prediction = row.get(variable, float("nan"))
        records.append(
            {k: case[k] for k in CASE_KEYS}
            | dict(
                case_id=case["case_id"],
                date=obs["date"],
                crop=case["inputs"]["crop"],
                method=method,
                variable=variable,
                predicted=prediction,
                observed=obs["value"],
                unit=obs["unit"],
                replicate_id=str(obs["replicate_id"]),
                spatial_support=support,
                depth_top_cm=obs.get("depth_top_cm")
                if variable in {"soil_theta","water_budget_residual_mm"}
                else None,
                depth_bottom_cm=obs.get("depth_bottom_cm")
                if variable in {"soil_theta","water_budget_residual_mm"}
                else None,
                phenology_event=obs.get("phenology_event", "")
                if variable == "phenology_days"
                else "",
                window_start=obs.get("window_start") if variable == "water_budget_residual_mm" else None,
                window_end=obs.get("window_end") if variable == "water_budget_residual_mm" else None,
                dependency_group=obs.get("dependency_group"),
                qc_flag="pass",
                split=case["split"],
                evidence="verified_case_observation",
            )
        )
    return records


def apply_registered_split(cases):
    if not cases:
        return []
    rows = []
    for case in cases:
        rows.append(
            {k: case[k] for k in CASE_KEYS}
            | dict(
                crop=case["inputs"]["crop"],
                start_date=case["inputs"]["start_date"],
                eligible=case["split"] != "excluded",
                exclusion_reason="",
            )
        )
    frozen = freeze_split(pd.DataFrame(rows))
    for case, registered in zip(cases, frozen.to_dict("records")):
        if registered["split"] != "excluded" and registered["split"] != case["split"]:
            raise ValueError(
                "Case split contradicts predeclared chronological whole-source-group split"
            )
        case["split"] = registered["split"]
        case["exclusion_reason"] = registered["exclusion_reason"]
    return frozen.to_dict("records")


def _mean_replicates(frame):
    keys = [
        k
        for k in [
            "case_id",
            "site_id",
            "crop",
            "method",
            "season_id",
            "source_group_id",
            "treatment_id",
            "variable",
            "date",
            "depth_top_cm",
            "depth_bottom_cm",
            "spatial_support",
            "phenology_event",
            "window_start",
            "window_end",
        ]
        if k in frame
    ]
    return frame.groupby(keys, dropna=False, as_index=False, sort=False)[
        ["observed", "predicted"]
    ].agg(lambda values: float(np.mean(values.to_numpy())))


def _score(variable, group):
    if not np.isfinite(group.predicted.to_numpy(dtype=float)).all():
        return {"n": len(group), "status": "invalid_prediction", "acceptance": "fail"}
    metrics = paired_metrics(group.observed, group.predicted)
    return metrics | {"acceptance": acceptance(variable, metrics)}


def field_scores(cases, pairs, seed):
    validation = pd.DataFrame([p for p in pairs if p.get("split") == "validation"])
    if validation.empty:
        return [], "insufficient_evidence"
    frame = _mean_replicates(validation)
    case_by_id = {c["case_id"]: c for c in cases}
    rows = []
    # Every case and depth/support is scored separately before pooled summaries.
    keys = [
        "case_id",
        "site_id",
        "crop",
        "method",
        "season_id",
        "source_group_id",
        "variable",
        "depth_top_cm",
        "depth_bottom_cm",
        "spatial_support",
        "phenology_event",
    ]
    for values, group in frame.groupby(keys, dropna=False, sort=False):
        metadata = dict(zip(keys, values))
        metrics = _score(metadata["variable"], group)
        rows.append(
            metadata
            | metrics
            | {"split": "validation", "aggregation": "individual_season"}
        )
        if metadata["variable"] == "et_mm" and metadata["case_id"] in case_by_id:
            case = case_by_id[metadata["case_id"]]
            expected = len(case["inputs"]["weather"])
            sm = seasonal_et_metrics(group.observed, group.predicted, expected)
            if not np.isfinite(group.predicted.to_numpy()).all():
                sm = {
                    "status": "invalid_prediction",
                    "coverage": len(group) / expected,
                    "acceptance": "fail",
                }
            else:
                sm["acceptance"] = acceptance("season_et_mm", sm)
            rows.append(
                metadata
                | sm
                | {
                    "variable": "season_et_mm",
                    "split": "validation",
                    "aggregation": "individual_season",
                }
            )
    aggregate_keys = [
        "site_id",
        "crop",
        "method",
        "variable",
        "depth_top_cm",
        "depth_bottom_cm",
        "spatial_support",
        "phenology_event",
    ]
    for values, group in frame.groupby(aggregate_keys, dropna=False, sort=False):
        metadata = dict(zip(aggregate_keys, values))
        metrics = _score(metadata["variable"], group)
        ci = (
            bootstrap_by_season(group, seed)
            if metrics["acceptance"] != "fail"
            or np.isfinite(group.predicted.to_numpy()).all()
            else {"status": "invalid_prediction"}
        )
        metrics.update({k: v for k, v in ci.items() if k != "status"})
        metrics["confidence_status"] = ci["status"]
        rows.append(
            metadata
            | metrics
            | {"split": "validation", "aggregation": "independent_seasons"}
        )
    required = {"yield_kg_ha", "soil_theta", "et_mm", "season_et_mm", "phenology_days"}
    evidence = bool(cases)
    active = [c for c in cases if c["split"] == "validation"]
    if not {"wheat", "maize"} <= {c["inputs"]["crop"] for c in active}:
        evidence = False
    for case in active:
        scored = [r for r in rows if r.get("case_id") == case["case_id"]]
        if not required <= {r["variable"] for r in scored}:
            evidence = False
        cf = frame[frame.case_id.eq(case["case_id"])]
        if cf[cf.variable.eq("soil_theta")].date.nunique() < 3:
            evidence = False
        if not {"anthesis", "maturity"} <= set(
            cf[cf.variable.eq("phenology_days")].phenology_event
        ):
            evidence = False
    if not active or any(r["acceptance"] == "insufficient_evidence" for r in rows):
        evidence = False
    if any(r["acceptance"] == "fail" for r in rows):
        return rows, "fail"
    pooled = [r for r in rows if r["aggregation"] == "independent_seasons"]
    if any(r.get("confidence_status") != "scored" for r in pooled):
        evidence = False
    return rows, "pass" if evidence else "insufficient_evidence"


def evaluate_contrasts(specs, cases, pairs):
    indexed = {c["case_id"]: c for c in cases}
    rows = []
    for spec in specs:
        a, b = indexed[spec["case_a"]], indexed[spec["case_b"]]
        if a["split"] != "validation" or b["split"] != "validation":
            continue
        if spec.get("comparability_confirmed") is not True:
            continue
        for key in ["site_id", "experiment_id", "season_id", "source_group_id"]:
            if a[key] != b[key]:
                raise ValueError(
                    "Technology contrast must compare the same trial and source group"
                )
        for key in ["crop", "start_date", "end_date"]:
            if a["inputs"][key] != b["inputs"][key]:
                raise ValueError("Contrast crop and time supports differ")
        if spec["comparison"] not in {"same_field_water", "technology_package"}:
            raise ValueError("Explicit contrast comparison required")
        if spec["comparison"] == "same_field_water":
            # Paired daily field totals are supplied by the runner, not device labels.
            if abs(spec["field_input_a_mm"] - spec["field_input_b_mm"]) > 1e-6:
                raise ValueError("Device contrast requires equal net field water")
        variable = spec["variable"]
        if variable not in {"yield_kg_ha", "season_et_mm"}:
            raise ValueError("Contrast requires yield or observed seasonal ET")
        summaries = []
        for case in [a, b]:
            selected = [
                p
                for p in pairs
                if p["case_id"] == case["case_id"]
                and p["variable"]
                == ("et_mm" if variable == "season_et_mm" else variable)
            ]
            if not selected:
                summaries.append(None)
                continue
            frame = pd.DataFrame(selected)
            if variable == "season_et_mm":
                expected = len(case["inputs"]["weather"])
                counts = frame.groupby("replicate_id").date.nunique()
                if counts.min() / expected < 0.9:
                    summaries.append(None)
                    continue
                frame = frame.groupby("replicate_id")[["observed", "predicted"]].sum()
            else:
                frame = frame.groupby("replicate_id")[["observed", "predicted"]].mean()
            summaries.append(frame)
        if any(s is None or len(s) < 2 for s in summaries):
            continue
        va, vb = summaries
        difference = float(va.observed.mean() - vb.observed.mean())
        # Independent replicate bootstrap; complete trial remains the outer unit.
        rng = np.random.default_rng(20261002)
        diffs = [
            float(
                rng.choice(va.observed, len(va), replace=True).mean()
                - rng.choice(vb.observed, len(vb), replace=True).mean()
            )
            for _ in range(1000)
        ]
        rows.append(
            dict(
                source_group_id=a["source_group_id"],
                case_a=a["case_id"],
                case_b=b["case_id"],
                method_a=a["inputs"]["technology"]["method"],
                method_b=b["inputs"]["technology"]["method"],
                variable=variable,
                observed_difference=difference,
                observed_ci_low=float(np.quantile(diffs, 0.025)),
                observed_ci_high=float(np.quantile(diffs, 0.975)),
                predicted_difference=float(va.predicted.mean() - vb.predicted.mean()),
                status="independent_validation_contrast",
                interval_method="independent replicate bootstrap; outer gate weights trial groups equally",
            )
        )
    return rows, contrast_acceptance(rows)["status"]
