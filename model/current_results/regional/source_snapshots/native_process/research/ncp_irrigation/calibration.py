"""Calibration-only selection, transparent parameter freezing and joint priors."""

from pathlib import Path
from copy import deepcopy
import hashlib, json
import numpy as np


def check_observation_independence(observations):
    groups = {}
    for record in observations:
        group = record.get("dependency_group")
        if group:
            groups.setdefault(group, set()).add(record["variable"])
    if any(
        bool(variables & {"et","et_mm","water_budget_residual_mm"})
        and variables
        & {"storage_mm", "soil_theta", "soil_storage_initial_mm", "soil_storage_final_mm",
           "drainage_mm", "precipitation_mm", "irrigation_mm", "irrigation_field_mm"}
        for variables in groups.values()
    ):
        raise ValueError(
            "Derived ET and its balance components are not independent objective groups"
        )


def calibrate_candidates(candidates, samples, objective):
    if samples.empty or not samples.split.eq("calibration").all():
        raise ValueError("Only nonempty calibration samples can select parameters")
    check_observation_independence(samples.to_dict("records"))
    scores = []
    for parameters in candidates:
        score = float(objective(deepcopy(parameters), samples.copy()))
        if not np.isfinite(score):
            raise ValueError("Calibration objective must be finite")
        scores.append((score, deepcopy(parameters)))
    if not scores:
        raise ValueError("At least one explicit candidate is required")
    score, selected = min(scores, key=lambda pair: pair[0])
    return selected, {
        "objective": score,
        "candidates": len(scores),
        "calibration_groups": sorted(samples.source_group_id.unique()),
    }


def grouped_objective(frame, scales):
    """Equal variable and source-group weights, with independent physical scales."""
    if frame.empty:
        raise ValueError("No matched observations")
    terms = []
    for variable, subset in frame.groupby("variable"):
        scale = scales[variable]
        if not np.isfinite(scale) or scale <= 0:
            raise ValueError("Positive independent normalization scale required")
        by_group = [
            np.mean(((g.predicted - g.observed) / scale) ** 2)
            for _, g in subset.groupby("source_group_id")
        ]
        terms.append(np.mean(by_group))
    return float(np.mean(terms))


def _digest(payload):
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def freeze_parameters(parameters, calibration_groups, path):
    payload = {
        "parameters": parameters,
        "calibration_groups": sorted(set(calibration_groups)),
        "status": "frozen; validation cannot select parameters",
    }
    payload["sha256"] = _digest(payload)
    Path(path).write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n")
    return payload["sha256"]


def load_frozen_parameters(path, *, with_provenance=False):
    payload = json.loads(Path(path).read_text())
    expected = payload.pop("sha256")
    if _digest(payload) != expected:
        raise ValueError("Frozen parameter hash mismatch")
    payload["sha256"] = expected
    return payload if with_provenance else payload["parameters"]


def parameter_profiles(parameters, bounds, objective, n_points=7):
    """One-at-a-time profiles; not a posterior or proof of identifiability."""
    rows = []
    for name, (low, high) in bounds.items():
        for value in np.linspace(low, high, n_points):
            candidate = deepcopy(parameters)
            candidate[name] = float(value)
            rows.append(
                {
                    "parameter": name,
                    "value": float(value),
                    "objective": float(objective(candidate)),
                    "status": "profile diagnostic; covariance requires joint data",
                }
            )
    return rows


def joint_samples(ranges, n_samples=200, seed=20261002):
    rng = np.random.default_rng(seed)
    rows = []

    def draw(bounds):
        values = {}
        for key, (low, high) in sorted(bounds.items()):
            if not np.isfinite([low, high]).all() or high < low:
                raise ValueError("Finite ordered prior bounds required")
            values[key] = float(rng.uniform(low, high))
        return values

    for _ in range(n_samples):
        shared = draw(ranges.get("shared", {}))
        rows.append(
            {
                name: {**shared, **draw(local)}
                for name, local in sorted(ranges.get("technology", {}).items())
            }
        )
    return rows
