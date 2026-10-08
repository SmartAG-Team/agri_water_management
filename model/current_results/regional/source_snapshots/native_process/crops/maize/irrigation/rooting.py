from __future__ import annotations

import math

from .bbch_mapping import interpolate_process_parameters, normalize_iowa_stage, stage_between
from crops.maize.growth_config import GROWTH_PARAMETERS


def initial_root_depth_mm() -> float:
    return 25.0


def advance_root_depth(
    previous_root_depth_mm: float,
    bbch: int | str,
    mean_air_temp_c: float,
    root_zone_relative_available_water: float,
) -> float:
    stage = normalize_iowa_stage(bbch)
    params = interpolate_process_parameters(stage)
    if stage == "R6":
        return round(previous_root_depth_mm, 3)

    temp_factor = max(0.15, min(1.30, (mean_air_temp_c + 1.0) / 12.0))
    if stage_between(stage, "VS", "V10"):
        temp_factor *= max(0.35, min(1.0, (mean_air_temp_c + 2.0) / 10.0))
    water_factor = max(0.40, min(1.0, 0.50 + 0.50 * root_zone_relative_available_water))
    extension = float(params["root_extension_mm_per_day"]) * temp_factor * water_factor
    max_depth = min(float(params["max_root_depth_mm"]), float(GROWTH_PARAMETERS["max_root_depth_mm"]))
    return round(min(max_depth, previous_root_depth_mm + extension), 3)


def layer_root_activity(soil_profile: list[dict], root_depth_mm: float) -> list[float]:
    shape = float(GROWTH_PARAMETERS["root_distribution_shape"])
    weights = []
    for layer in soil_profile:
        top_mm = float(layer["depth_top_cm"]) * 10.0
        bottom_mm = float(layer["depth_bottom_cm"]) * 10.0
        if root_depth_mm <= top_mm:
            weights.append(0.0)
            continue
        effective_bottom = min(bottom_mm, root_depth_mm)
        if effective_bottom <= top_mm:
            weights.append(0.0)
            continue
        midpoint = (top_mm + effective_bottom) * 0.5
        rel_depth = midpoint / max(root_depth_mm, 1e-6)
        thickness_fraction = (effective_bottom - top_mm) / max(root_depth_mm, 1e-6)
        weight = math.exp(-shape * rel_depth) * thickness_fraction
        weights.append(max(0.0, weight))
    total = sum(weights)
    if total <= 0:
        return [0.0 for _ in weights]
    return [round(weight / total, 6) for weight in weights]
