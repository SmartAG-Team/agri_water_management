from __future__ import annotations

import math

from .bbch_mapping import interpolate_process_parameters
from .config import GROWTH_PARAMETERS


def initial_root_depth_mm() -> float:
    return 15.0


def advance_root_depth(
    previous_root_depth_mm: float,
    bbch: int,
    mean_air_temp_c: float,
    root_zone_relative_available_water: float,
) -> float:
    params = interpolate_process_parameters(bbch)
    if bbch >= 89:
        return round(previous_root_depth_mm, 3)

    temp_factor = max(0.10, min(1.25, (mean_air_temp_c + 2.0) / 11.0))
    if bbch <= 25:
        temp_factor *= max(0.25, min(1.0, (mean_air_temp_c + 1.0) / 7.0))
    water_factor = max(0.45, min(1.0, 0.55 + 0.45 * root_zone_relative_available_water))
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
