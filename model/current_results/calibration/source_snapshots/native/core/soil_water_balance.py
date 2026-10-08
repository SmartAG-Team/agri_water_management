from __future__ import annotations

from copy import deepcopy


def _require_layer_float(layer: dict, field: str) -> float:
    if field not in layer or layer[field] is None:
        label = layer.get("label", "unknown")
        raise ValueError(f"soil layer {label} missing required field: {field}")
    return float(layer[field])


def _layer_storage_capacity_mm(layer: dict) -> float:
    taw_mm = _require_layer_float(layer, "taw_mm")
    # The model state stores plant-available water above wilting point. At a
    # daily timestep, water above field capacity should drain onward instead of
    # being retained as plant-available storage in the layer.
    return max(0.0, taw_mm)


def _drainage_fraction(layer: dict) -> float:
    ksat_mm_day = max(1.0, _require_layer_float(layer, "ksat_mm_day"))
    field_capacity = _require_layer_float(layer, "field_capacity")
    saturation = max(field_capacity + 1e-6, _require_layer_float(layer, "saturation"))
    bulk_density = max(1.0, _require_layer_float(layer, "bulk_density"))

    conductivity_factor = min(1.0, (ksat_mm_day / 240.0) ** 0.55)
    pore_buffer = max(0.0, min(1.0, (saturation - field_capacity) / 0.22))
    density_factor = max(0.75, min(1.1, 1.7 - bulk_density))
    return max(0.08, min(0.95, (0.10 + 0.75 * conductivity_factor) * (0.70 + 0.30 * pore_buffer) * density_factor))


def _drainage_transfer_fraction(layer: dict) -> float:
    ksat_mm_day = max(1.0, _require_layer_float(layer, "ksat_mm_day"))
    field_capacity = _require_layer_float(layer, "field_capacity")
    saturation = max(field_capacity + 1e-6, _require_layer_float(layer, "saturation"))

    conductivity_factor = min(1.0, (ksat_mm_day / 240.0) ** 0.60)
    water_holding_factor = max(0.0, min(1.0, (field_capacity - 0.10) / 0.26))
    pore_buffer = max(0.0, min(1.0, (saturation - field_capacity) / 0.22))
    return max(0.18, min(0.88, 0.82 - 0.52 * conductivity_factor + 0.28 * water_holding_factor + 0.10 * pore_buffer))


def _irrigation_capture_fraction(layer: dict) -> float:
    field_capacity = _require_layer_float(layer, "field_capacity")
    ksat_mm_day = max(1.0, _require_layer_float(layer, "ksat_mm_day"))
    fc_factor = max(0.0, min(1.0, (field_capacity - 0.10) / 0.26))
    conductivity_penalty = max(0.0, min(1.0, (ksat_mm_day - 35.0) / 205.0))
    # Application efficiency already accounts for a large share of irrigation
    # loss. Keep the physical soil adjustment here, but make it a secondary
    # modifier rather than a second heavy haircut; otherwise medium soils
    # unrealistically re-trigger irrigation soon after a large event.
    return max(0.72, min(1.0, 0.74 + 0.26 * fc_factor - 0.10 * conductivity_penalty))


def _rooted_fraction(layer: dict, root_depth_mm: float) -> float:
    top_mm = float(layer["depth_top_cm"]) * 10.0
    bottom_mm = float(layer["depth_bottom_cm"]) * 10.0
    if root_depth_mm <= top_mm:
        return 0.0
    if root_depth_mm >= bottom_mm:
        return 1.0
    return max(0.0, min(1.0, (root_depth_mm - top_mm) / max(bottom_mm - top_mm, 1e-6)))


def _normalized_weights(profile: list[dict], weights: list[float] | None, root_depth_mm: float) -> list[float]:
    if weights and len(weights) == len(profile):
        usable = [max(0.0, float(w)) if _rooted_fraction(layer, root_depth_mm) > 0 else 0.0 for layer, w in zip(profile, weights)]
    else:
        usable = [_rooted_fraction(layer, root_depth_mm) for layer in profile]
    total = sum(usable)
    if total <= 0:
        return [0.0 for _ in usable]
    return [weight / total for weight in usable]


def apply_daily_water_balance(
    soil_profile: list[dict],
    root_depth_mm: float,
    precipitation_mm: float,
    irrigation_gross_mm: float,
    potential_soil_evaporation_mm: float,
    potential_transpiration_mm: float,
    irrigation_efficiency: float,
    root_activity_weights: list[float] | None = None,
) -> dict:
    profile = deepcopy(soil_profile)
    irrigation_capture = _irrigation_capture_fraction(profile[0]) if profile else 1.0
    gross_in = max(0.0, precipitation_mm) + max(0.0, irrigation_gross_mm * irrigation_efficiency * irrigation_capture)
    top_layer = profile[0] if profile else {}
    top_ksat = max(1.0, _require_layer_float(top_layer, "ksat_mm_day")) if profile else 1.0
    runoff_fraction = max(0.01, min(0.08, 0.01 + 0.05 * max(0.0, 1.0 - top_ksat / 120.0)))
    runoff_mm = max(0.0, gross_in * runoff_fraction)
    infiltrated = gross_in - runoff_mm

    for layer in profile:
        capacity_left = max(0.0, _layer_storage_capacity_mm(layer) - layer["soil_water_mm"])
        add = min(capacity_left, infiltrated)
        layer["soil_water_mm"] += add
        infiltrated -= add
    deep_percolation_mm = max(0.0, infiltrated)

    evaporation_demand = max(0.0, potential_soil_evaporation_mm)
    actual_soil_evaporation = 0.0
    for idx, layer in enumerate(profile):
        if idx > 1 or evaporation_demand <= 0:
            break
        evaporation_share = 0.75 if idx == 0 else 0.25
        demand = potential_soil_evaporation_mm * evaporation_share
        extractable = min(demand, layer["soil_water_mm"])
        layer["soil_water_mm"] -= extractable
        actual_soil_evaporation += extractable
        evaporation_demand -= extractable

    transpiration_demand = max(0.0, potential_transpiration_mm)
    layer_weights = _normalized_weights(profile, root_activity_weights, root_depth_mm)
    actual_transpiration = 0.0
    remaining_demand = transpiration_demand

    for _ in range(3):
        if remaining_demand <= 1e-6:
            break
        capacities = []
        for layer, weight in zip(profile, layer_weights):
            rel_water = layer["soil_water_mm"] / max(layer["taw_mm"], 1e-6)
            capacity = min(layer["soil_water_mm"], remaining_demand * weight * (0.25 + 0.75 * rel_water) * 1.8)
            capacities.append(max(0.0, capacity))
        total_capacity = sum(capacities)
        if total_capacity <= 1e-6:
            break
        demand_fraction = min(1.0, remaining_demand / total_capacity)
        for layer, capacity in zip(profile, capacities):
            uptake = min(layer["soil_water_mm"], capacity * demand_fraction)
            layer["soil_water_mm"] -= uptake
            actual_transpiration += uptake
            remaining_demand -= uptake

    drainage_mm = 0.0
    for idx, layer in enumerate(profile):
        excess = max(0.0, layer["soil_water_mm"] - layer["taw_mm"])
        if excess <= 0:
            continue
        drained = excess * _drainage_fraction(layer)
        layer["soil_water_mm"] -= drained
        drainage_mm += drained
        transfer_fraction = _drainage_transfer_fraction(layer)
        deep_percolation_mm += drained * (1.0 - transfer_fraction)
        remaining_drainage = drained * transfer_fraction
        next_idx = idx + 1
        while remaining_drainage > 1e-9 and next_idx < len(profile):
            next_layer = profile[next_idx]
            storage_left = max(0.0, _layer_storage_capacity_mm(next_layer) - next_layer["soil_water_mm"])
            transfer = min(storage_left, remaining_drainage)
            next_layer["soil_water_mm"] += transfer
            remaining_drainage -= transfer
            next_idx += 1
        deep_percolation_mm += remaining_drainage

    root_zone_soil_water = 0.0
    root_zone_taw = 0.0
    active_root_relative = 0.0
    final_activity_weights = _normalized_weights(profile, root_activity_weights, root_depth_mm) if root_activity_weights else None
    soil_water_by_layer = []
    for index, layer in enumerate(profile):
        rooted_frac = _rooted_fraction(layer, root_depth_mm)
        root_zone_soil_water += layer["soil_water_mm"] * rooted_frac
        root_zone_taw += layer["taw_mm"] * rooted_frac
        rel_available = layer["soil_water_mm"] / max(layer["taw_mm"], 1e-6)
        if final_activity_weights:
            active_root_relative += min(1.0, rel_available) * final_activity_weights[index]
        soil_water_by_layer.append(
            {
                "label": layer["label"],
                "depth_top_cm": layer["depth_top_cm"],
                "depth_bottom_cm": layer["depth_bottom_cm"],
                "soil_water_mm": round(layer["soil_water_mm"], 3),
                "relative_available_water": round(min(1.0, rel_available), 4),
            }
        )

    storage_root_relative = min(1.0, root_zone_soil_water / max(root_zone_taw, 1e-6))
    stress_root_relative = active_root_relative if final_activity_weights else storage_root_relative
    return {
        "soil_profile": profile,
        "root_zone_soil_water_mm": round(root_zone_soil_water, 3),
        "root_zone_relative_available_water": round(stress_root_relative, 4),
        "storage_root_zone_relative_available_water": round(storage_root_relative, 4),
        "active_root_zone_relative_available_water": round(stress_root_relative, 4),
        "soil_water_by_layer": soil_water_by_layer,
        "runoff_mm": round(runoff_mm, 3),
        "drainage_mm": round(drainage_mm, 3),
        "deep_percolation_mm": round(deep_percolation_mm, 3),
        "capillary_rise_mm": 0.0,
        "actual_soil_evaporation_mm": round(actual_soil_evaporation, 3),
        "actual_transpiration_mm": round(actual_transpiration, 3),
        "eta_actual_mm": round(actual_soil_evaporation + actual_transpiration, 3),
    }
