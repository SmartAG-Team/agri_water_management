"""Predeclared engineering targets; missing evidence never receives a pass."""

import numpy as np

TARGETS = {
    "yield_kg_ha": {"nrmse": 0.15, "relative_bias": 0.10},
    "soil_theta": {"rmse": 0.04, "bias": 0.02},
    "et_mm": {"rmse": 1.2, "bias": 0.5},
    "season_et_mm": {"nrmse": 0.15, "relative_bias": 0.10},
    "lai": {"rmse": 1.0},
    "biomass_kg_ha": {"nrmse": 0.20},
    "phenology_days": {"mae": 7.0, "bias": 5.0},
}


def paired_metrics(observed, predicted):
    obs = np.asarray(observed, dtype=float)
    pred = np.asarray(predicted, dtype=float)
    if obs.shape != pred.shape:
        raise ValueError("Paired arrays must have identical shape")
    mask = np.isfinite(obs) & np.isfinite(pred)
    obs = obs[mask]
    pred = pred[mask]
    if len(obs) == 0:
        return {"n": 0, "status": "insufficient_evidence"}
    error = pred - obs
    mean = float(np.mean(obs))
    rmse = float(np.sqrt(np.mean(error**2)))
    bias = float(np.mean(error))
    return dict(
        n=len(obs),
        rmse=rmse,
        mae=float(np.mean(np.abs(error))),
        bias=bias,
        nrmse=rmse / abs(mean) if mean != 0 else None,
        relative_bias=bias / abs(mean) if mean != 0 else None,
        status="scored",
    )


def acceptance(variable, metrics):
    if metrics.get("status") == "insufficient_evidence":
        return "insufficient_evidence"
    if variable not in TARGETS:
        return "insufficient_evidence"
    for key, target in TARGETS[variable].items():
        if metrics.get(key) is None:
            return "insufficient_evidence"
        if abs(metrics[key]) > target:
            return "fail"
    return "pass"


def seasonal_et_metrics(observed, predicted, expected_days, min_coverage=0.9):
    if expected_days <= 0:
        raise ValueError("Expected day count must be positive")
    obs = np.asarray(observed, dtype=float)
    pred = np.asarray(predicted, dtype=float)
    if obs.shape != pred.shape:
        raise ValueError("Paired shape mismatch")
    mask = np.isfinite(obs) & np.isfinite(pred)
    coverage = float(mask.sum() / expected_days)
    if coverage < min_coverage:
        return {"status": "insufficient_evidence", "coverage": coverage}
    result = paired_metrics([obs[mask].sum()], [pred[mask].sum()])
    result["coverage"] = coverage
    # Missing days remain a reported limitation; not silently scaled to a full season.
    result["aggregation"] = "paired observed days; omitted days explicitly reported"
    return result


def bootstrap_by_season(frame, seed=20261002, n_bootstrap=500):
    groups = list(frame.source_group_id.unique())
    if len(groups) < 2:
        return {"status": "insufficient_evidence"}
    rng = np.random.default_rng(seed)
    values = []
    for _ in range(n_bootstrap):
        pieces = [
            frame[frame.source_group_id.eq(g)]
            for g in rng.choice(groups, len(groups), replace=True)
        ]
        obs = np.concatenate([p.observed.to_numpy() for p in pieces])
        pred = np.concatenate([p.predicted.to_numpy() for p in pieces])
        values.append(paired_metrics(obs, pred)["rmse"])
    return dict(
        rmse_ci_low=float(np.quantile(values, 0.025)),
        rmse_ci_high=float(np.quantile(values, 0.975)),
        independent_groups=len(groups),
        status="scored",
    )


def contrast_acceptance(contrasts):
    selected = [
        c for c in contrasts if c["observed_ci_low"] > 0 or c["observed_ci_high"] < 0
    ]
    groups = {c["source_group_id"] for c in selected}
    if len(groups) < 5:
        return dict(status="insufficient_evidence", independent_groups=len(groups))
    # Weight independent groups equally, not repeated contrasts within a trial.
    agreement = np.mean(
        [
            np.mean(
                [
                    np.sign(c["observed_difference"])
                    == np.sign(c["predicted_difference"])
                    for c in selected
                    if c["source_group_id"] == g
                ]
            )
            for g in groups
        ]
    )
    return dict(
        status="pass" if agreement >= 0.8 else "fail",
        direction_agreement=float(agreement),
        independent_groups=len(groups),
    )


def interval_summary(values):
    x = np.asarray(values, dtype=float)
    if x.size == 0 or not np.isfinite(x).all():
        raise ValueError("Finite uncertainty sample required")
    return dict(
        median=float(np.median(x)),
        p05=float(np.quantile(x, 0.05)),
        p95=float(np.quantile(x, 0.95)),
    )
