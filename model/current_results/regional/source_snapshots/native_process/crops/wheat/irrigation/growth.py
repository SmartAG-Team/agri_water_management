from __future__ import annotations

import math
from copy import deepcopy
from datetime import date

from core.nutrition.fertilizer_material import product_to_nutrients

from .bbch_mapping import get_phase, get_process_phase, get_process_phase_progress, interpolate_process_parameters
from .config import GROWTH_PARAMETERS, ORGAN_N_CONCENTRATIONS
from .rooting import initial_root_depth_mm


PUBLIC_GROWTH_KEYS = (
    "lai",
    "leaf_biomass_kg_ha",
    "stem_biomass_kg_ha",
    "spike_biomass_kg_ha",
    "grain_biomass_kg_ha",
    "total_biomass_kg_ha",
    "grain_weight_kg_ha",
    "leaf_n_kg_ha",
    "stem_n_kg_ha",
    "spike_n_kg_ha",
    "grain_n_kg_ha",
    "total_plant_n_kg_ha",
    "daily_n_demand_kg_ha",
    "daily_n_uptake_kg_ha",
    "soil_n_supply_kg_ha",
    "fertilizer_n_supply_kg_ha",
    "remobilized_n_kg_ha",
    "water_growth_factor",
    "n_growth_factor",
    "combined_growth_factor",
)

def _phase_value(mapping: dict[str, float], phase: str) -> float:
    return float(mapping.get(phase, list(mapping.values())[-1]))


def _organ_concentrations(bbch: int) -> dict[str, dict[str, float]]:
    return ORGAN_N_CONCENTRATIONS[get_phase(bbch)]


def _fertilizer_history_to_pools(fertilizer_history: list[dict] | None, custom_products: list[dict] | None = None) -> list[dict]:
    pools = []
    for item in fertilizer_history or []:
        applied_date = item.get("date")
        if isinstance(applied_date, str):
            applied_date = date.fromisoformat(applied_date[:10])
        if not isinstance(applied_date, date):
            continue
        nutrients = dict(item.get("nutrients_kg_ha") or {})
        if not nutrients and item.get("product_name") and item.get("amount_kg_ha") is not None:
            nutrients = product_to_nutrients(str(item["product_name"]), float(item["amount_kg_ha"]), custom_products)
        if not nutrients:
            n_pct = float(item.get("n_pct", 0.0) or 0.0)
            amount = float(item.get("amount_kg_ha", 0.0) or 0.0)
            nutrients = {"N": round(amount * n_pct / 100.0, 3)}
        n_amount = float(nutrients.get("N", 0.0) or 0.0)
        if n_amount <= 0:
            continue
        pools.append(
            {
                "date": applied_date,
                "product_name": item.get("product_name", "custom_product"),
                "remaining_n_kg_ha": n_amount,
            }
        )
    return pools


def _required_state_float(state: dict, field: str) -> float:
    if field not in state or state[field] is None:
        raise ValueError(f"growth state missing required field: {field}")
    return float(state[field])


def _initial_soil_n_layers(soil_test: dict, soil_profile: list[dict] | None) -> list[float]:
    if not soil_profile:
        return []
    mineral_n = soil_test.get("mineral_n_kg_ha")
    if mineral_n is None and soil_test.get("alkali_hydrolyzable_n_mg_kg") is not None:
        mineral_n = float(soil_test["alkali_hydrolyzable_n_mg_kg"]) * 0.32
    if mineral_n is None:
        raise ValueError("soil_test.mineral_n_kg_ha or soil_test.alkali_hydrolyzable_n_mg_kg is required when nutrition coupling is enabled")
    mineral_n = float(mineral_n)
    weights = []
    for idx, layer in enumerate(soil_profile):
        thickness = float(layer["thickness_mm"])
        surface_bias = 1.8 if idx == 0 else (1.25 if idx == 1 else 0.75)
        weights.append(max(0.0, thickness * surface_bias))
    total = sum(weights) or 1.0
    return [round(mineral_n * weight / total, 4) for weight in weights]


def initialize_growth_state(
    soil_test: dict | None = None,
    fertilizer_history: list[dict] | None = None,
    custom_products: list[dict] | None = None,
    nutrition_enabled: bool = True,
    soil_profile: list[dict] | None = None,
) -> dict:
    soil_test = soil_test or {}
    if nutrition_enabled and soil_test.get("organic_matter_g_kg") is None:
        raise ValueError("soil_test.organic_matter_g_kg is required when nutrition coupling is enabled")
    root_depth_mm = initial_root_depth_mm()
    return {
        "lai": 0.0,
        "leaf_biomass_kg_ha": 0.0,
        "stem_biomass_kg_ha": 0.0,
        "spike_biomass_kg_ha": 0.0,
        "grain_biomass_kg_ha": 0.0,
        "total_biomass_kg_ha": 0.0,
        "grain_weight_kg_ha": 0.0,
        "leaf_n_kg_ha": 0.0,
        "stem_n_kg_ha": 0.0,
        "spike_n_kg_ha": 0.0,
        "grain_n_kg_ha": 0.0,
        "total_plant_n_kg_ha": 0.0,
        "daily_n_demand_kg_ha": 0.0,
        "daily_n_uptake_kg_ha": 0.0,
        "soil_n_supply_kg_ha": 0.0,
        "fertilizer_n_supply_kg_ha": 0.0,
        "remobilized_n_kg_ha": 0.0,
        "water_growth_factor": 1.0,
        "n_growth_factor": 1.0,
        "combined_growth_factor": 1.0,
        "_nutrition_enabled": nutrition_enabled,
        "_soil_n_layers_kg_ha": _initial_soil_n_layers(soil_test, soil_profile) if nutrition_enabled else [],
        "_organic_matter_g_kg": float(soil_test["organic_matter_g_kg"]) if nutrition_enabled else 0.0,
        "_fertilizer_n_pools": _fertilizer_history_to_pools(fertilizer_history, custom_products),
        "_root_biomass_kg_ha": 0.0,
        "_root_n_kg_ha": 0.0,
        "_root_depth_mm": root_depth_mm,
        "_dead_aboveground_biomass_kg_ha": 0.0,
    }


def extract_public_growth_state(state: dict) -> dict:
    return {key: state[key] for key in PUBLIC_GROWTH_KEYS}


def _fertilizer_release_for_day(
    pools: list[dict],
    current_date: date,
    top_layer_relative_water: float,
    precipitation_mm: float,
    irrigation_mm: float,
) -> tuple[list[dict], float]:
    updated_pools = []
    released = 0.0
    for item in pools:
        pool = deepcopy(item)
        if current_date < pool["date"]:
            updated_pools.append(pool)
            continue
        days_since = max(0, (current_date - pool["date"]).days)
        if days_since <= 7:
            daily_fraction = 0.18
        elif days_since <= 21:
            daily_fraction = 0.07
        elif days_since <= 45:
            daily_fraction = 0.03
        else:
            daily_fraction = 0.008
        dissolution_factor = 0.75 if top_layer_relative_water < 0.25 and precipitation_mm + irrigation_mm < 5.0 else 1.0
        loss_factor = 0.85 if precipitation_mm + irrigation_mm >= 35.0 and days_since <= 3 else 1.0
        day_release = min(pool["remaining_n_kg_ha"], pool["remaining_n_kg_ha"] * daily_fraction * dissolution_factor * loss_factor)
        pool["remaining_n_kg_ha"] = max(0.0, pool["remaining_n_kg_ha"] - day_release)
        released += day_release
        updated_pools.append(pool)
    return updated_pools, round(released, 4)


def _leaf_area_from_biomass(leaf_biomass_kg_ha: float, leaf_n_kg_ha: float, base_sla_m2_kg: float, critical_leaf_n: float) -> float:
    if leaf_biomass_kg_ha <= 0:
        return 0.0
    actual_leaf_conc = leaf_n_kg_ha / max(leaf_biomass_kg_ha, 1e-6)
    n_ratio = max(0.0, actual_leaf_conc / max(critical_leaf_n, 1e-6))
    n_modifier = max(0.35, min(1.00, n_ratio**0.85))
    effective_sla = base_sla_m2_kg * n_modifier
    return max(0.0, leaf_biomass_kg_ha / 1000.0 * effective_sla)


def _smooth_lai(previous_lai: float, target_lai: float, phase: str, mean_air_temp_c: float, water_factor: float, n_factor: float) -> float:
    if target_lai <= previous_lai:
        max_decline = {
            "vegetative": 0.03,
            "stem_elongation": 0.05,
            "booting": 0.07,
            "flowering": 0.11,
            "grain_filling": 0.16,
            "maturity": 0.12,
        }.get(phase, 0.08)
        stress_modifier = min(1.35, 1.0 + 0.8 * (1.0 - min(water_factor, n_factor)))
        return max(target_lai, previous_lai - max_decline * stress_modifier)

    max_increase = {
        "vegetative": 0.10,
        "stem_elongation": 0.20,
        "booting": 0.14,
        "flowering": 0.06,
        "grain_filling": 0.02,
        "maturity": 0.0,
    }.get(phase, 0.08)
    temp_modifier = max(0.35, min(1.05, (mean_air_temp_c - 2.0) / 14.0))
    stress_modifier = max(0.45, min(1.0, 0.55 * water_factor + 0.45 * n_factor))
    return min(target_lai, previous_lai + max_increase * temp_modifier * stress_modifier)


def update_growth_state(
    previous: dict,
    current_date: date,
    bbch: int,
    mean_air_temp_c: float,
    radiation_sum: float,
    actual_transpiration_mm: float,
    potential_transpiration_mm: float,
    reference_et_mm: float,
    root_zone_relative_available_water: float,
    top_layer_relative_water: float,
    precipitation_mm: float,
    irrigation_mm: float,
    soil_water_by_layer: list[dict],
    root_depth_mm: float,
    root_activity_weights: list[float],
    deep_percolation_mm: float = 0.0,
    target_yield_kg_ha: float | None = None,
) -> dict:
    state = deepcopy(previous)
    phase = get_process_phase(bbch)
    phase_params = interpolate_process_parameters(bbch)
    n_conc = _organ_concentrations(bbch)

    leaf = float(state["leaf_biomass_kg_ha"])
    stem = float(state["stem_biomass_kg_ha"])
    spike = float(state["spike_biomass_kg_ha"])
    grain = float(state["grain_biomass_kg_ha"])
    root = float(state.get("_root_biomass_kg_ha", 0.0))
    dead_aboveground = float(state.get("_dead_aboveground_biomass_kg_ha", 0.0))

    leaf_n = float(state["leaf_n_kg_ha"])
    stem_n = float(state["stem_n_kg_ha"])
    spike_n = float(state["spike_n_kg_ha"])
    grain_n = float(state["grain_n_kg_ha"])
    root_n = float(state.get("_root_n_kg_ha", 0.0))

    nutrition_enabled = bool(state.get("_nutrition_enabled", True))
    soil_n_layers = list(state.get("_soil_n_layers_kg_ha", []))
    fert_pools = list(state.get("_fertilizer_n_pools", []))

    state["_root_depth_mm"] = round(root_depth_mm, 3)

    if bbch < 10:
        if nutrition_enabled and soil_n_layers:
            om = _required_state_float(state, "_organic_matter_g_kg")
            mineralization = om * 0.004 * max(0.25, top_layer_relative_water)
            soil_n_layers[0] += mineralization
            fert_pools, released = _fertilizer_release_for_day(
                fert_pools,
                current_date=current_date,
                top_layer_relative_water=top_layer_relative_water,
                precipitation_mm=precipitation_mm,
                irrigation_mm=irrigation_mm,
            )
            if soil_n_layers:
                soil_n_layers[0] += released
        state.update(
            {
                "lai": 0.0,
                "total_biomass_kg_ha": 0.0,
                "grain_weight_kg_ha": 0.0,
                "daily_n_demand_kg_ha": 0.0,
                "daily_n_uptake_kg_ha": 0.0,
                "soil_n_supply_kg_ha": 0.0,
                "fertilizer_n_supply_kg_ha": 0.0,
                "remobilized_n_kg_ha": 0.0,
                "water_growth_factor": 1.0,
                "n_growth_factor": 1.0,
                "combined_growth_factor": 1.0,
                "_soil_n_layers_kg_ha": [round(x, 4) for x in soil_n_layers],
                "_fertilizer_n_pools": fert_pools,
            }
        )
        return state

    base_sla = float(phase_params["sla_m2_kg"])
    partition = dict(phase_params["partition"])
    partition_sum = sum(max(0.0, float(v)) for v in partition.values()) or 1.0
    partition = {k: max(0.0, float(v)) / partition_sum for k, v in partition.items()}
    current_lai = _leaf_area_from_biomass(leaf, leaf_n, base_sla, n_conc["critical"]["leaf"])
    k = float(GROWTH_PARAMETERS["light_extinction_coefficient"])
    par_fraction = float(GROWTH_PARAMETERS["par_fraction"])
    intercepted_par = max(0.0, radiation_sum) * par_fraction * (1.0 - math.exp(-k * max(0.0, current_lai)))
    rue = float(phase_params["rue_g_mj"])
    temp_factor = max(0.0, min(1.08, (mean_air_temp_c - 0.0) / 18.0))
    if bbch <= 30:
        # Overwinter and regreening response should follow thermal conditions,
        # without introducing additional phase-specific parameter jumps.
        dormancy_factor = max(0.03, min(1.0, (mean_air_temp_c - 3.0) / 10.0))
        temp_factor *= dormancy_factor
    maintenance_fraction = float(GROWTH_PARAMETERS["maintenance_respiration_fraction"])
    potential_growth = max(0.0, intercepted_par * rue * 10.0 * temp_factor * (1.0 - maintenance_fraction))
    if bbch <= 15 and leaf + stem + spike + grain < 180.0:
        seed_reserve_growth = 45.0 * temp_factor * max(0.0, 1.0 - (leaf + stem + spike + grain) / 220.0)
        potential_growth += seed_reserve_growth

    potential_leaf_growth = potential_growth * float(partition["leaf"])
    potential_stem_growth = potential_growth * float(partition["stem"])
    potential_spike_growth = potential_growth * float(partition["spike"])
    potential_grain_growth = potential_growth * float(partition["grain"])
    potential_root_growth = potential_growth * float(partition["root"])

    organ_daily_n_demand = {
        "leaf": potential_leaf_growth * n_conc["critical"]["leaf"],
        "stem": potential_stem_growth * n_conc["critical"]["stem"],
        "spike": potential_spike_growth * n_conc["critical"]["spike"],
        "grain": potential_grain_growth * n_conc["critical"]["grain"],
        "root": potential_root_growth * 0.012,
    }
    total_n_demand = sum(organ_daily_n_demand.values())

    eta_ratio = 1.0 if potential_transpiration_mm <= 0.25 else max(0.0, min(1.0, actual_transpiration_mm / potential_transpiration_mm))
    raw_buffer = max(0.0, min(1.0, root_zone_relative_available_water))
    buffer_floor = {
        "vegetative": 0.22,
        "stem_elongation": 0.26,
        "booting": 0.30,
        "flowering": 0.34,
        "grain_filling": 0.22,
        "maturity": 0.18,
    }.get(phase, 0.24)
    buffered_water_factor = min(1.0, buffer_floor + (1.0 - buffer_floor) * raw_buffer)
    water_factor = max(0.0, min(1.0, (eta_ratio * buffered_water_factor) ** 0.5))

    soil_supply = 0.0
    fertilizer_supply = 0.0
    remobilized_n = 0.0
    total_uptake = 0.0
    irrigation_n_leached = 0.0
    if nutrition_enabled and soil_n_layers:
        om = _required_state_float(state, "_organic_matter_g_kg")
        mineralization = om * 0.0055 * max(0.16, root_zone_relative_available_water)
        if len(soil_n_layers) == 1:
            soil_n_layers[0] += mineralization
        else:
            soil_n_layers[0] += mineralization * 0.65
            soil_n_layers[1] += mineralization * 0.35

        fert_pools, released = _fertilizer_release_for_day(
            fert_pools,
            current_date=current_date,
            top_layer_relative_water=top_layer_relative_water,
            precipitation_mm=precipitation_mm,
            irrigation_mm=irrigation_mm,
        )
        fertilizer_supply = released
        soil_n_layers[0] += released

        if irrigation_mm > 0.0 and deep_percolation_mm > 0.0:
            irrigation_share = irrigation_mm / max(1.0, precipitation_mm + irrigation_mm)
            hydraulic_push = min(0.9, deep_percolation_mm / max(10.0, 24.0 + 52.0 * raw_buffer))
            rooted_mobile_pool = 0.0
            for idx, layer_n in enumerate(soil_n_layers):
                root_weight = float(root_activity_weights[idx]) if idx < len(root_activity_weights) else 0.0
                rooted_mobile_pool += max(0.0, layer_n) * min(1.0, 0.30 + 1.55 * root_weight)
            fert_mobile_pool = max(
                0.0,
                sum(float(pool.get("remaining_n_kg_ha", 0.0) or 0.0) for pool in fert_pools),
            )
            mobile_pool = rooted_mobile_pool + fert_mobile_pool
            mobility = 0.24 + 0.48 * (1.0 - raw_buffer)
            irrigation_n_leached = max(0.0, mobile_pool * hydraulic_push * irrigation_share * mobility)
            remaining_loss = irrigation_n_leached
            for pool in fert_pools:
                if remaining_loss <= 1e-6:
                    break
                removable = min(float(pool.get("remaining_n_kg_ha", 0.0) or 0.0), remaining_loss * 0.65)
                pool["remaining_n_kg_ha"] = max(0.0, float(pool.get("remaining_n_kg_ha", 0.0) or 0.0) - removable)
                remaining_loss -= removable
            layer_weights = [0.45, 0.30, 0.17, 0.08]
            for idx, available in enumerate(soil_n_layers):
                if remaining_loss <= 1e-6:
                    break
                weight = layer_weights[idx] if idx < len(layer_weights) else 0.04
                removable = min(available, max(0.0, irrigation_n_leached * weight), remaining_loss)
                soil_n_layers[idx] = max(0.0, available - removable)
                remaining_loss -= removable

        layer_supply_caps = []
        for idx, layer_water in enumerate(soil_water_by_layer):
            rel_water = float(layer_water.get("relative_available_water", 0.0) or 0.0)
            root_weight = float(root_activity_weights[idx]) if idx < len(root_activity_weights) else 0.0
            uptake_access = min(1.0, 0.30 + 1.70 * root_weight)
            moisture_access = 0.35 + 0.65 * rel_water
            supply_cap = soil_n_layers[idx] * uptake_access * moisture_access
            layer_supply_caps.append(max(0.0, supply_cap))
        total_layer_supply = sum(layer_supply_caps)
        if total_layer_supply > 0 and total_n_demand > 0:
            uptake_target = min(total_n_demand, total_layer_supply)
            for idx, cap in enumerate(layer_supply_caps):
                uptake = uptake_target * cap / total_layer_supply
                uptake = min(uptake, soil_n_layers[idx])
                soil_n_layers[idx] -= uptake
                soil_supply += uptake

        if phase in {"flowering", "grain_filling", "maturity"}:
            mobilizable_leaf_n = max(0.0, leaf_n - leaf * n_conc["minimum"]["leaf"])
            mobilizable_stem_n = max(0.0, stem_n - stem * n_conc["minimum"]["stem"])
            mobilizable_spike_n = max(0.0, spike_n - spike * n_conc["minimum"]["spike"])
            remobilizable_total = (
                mobilizable_leaf_n * float(phase_params["n_remobilization_fraction"])
                + mobilizable_stem_n * float(phase_params["n_remobilization_fraction"])
                + mobilizable_spike_n * float(phase_params["n_remobilization_fraction"])
            )
            remobilized_n = min(max(0.0, total_n_demand - soil_supply), remobilizable_total)

        total_uptake = soil_supply + remobilized_n
        n_supply_ratio = 1.0 if total_n_demand <= 1e-6 else max(0.0, min(1.0, total_uptake / total_n_demand))
        leaf_n_status = 1.0
        stem_n_status = 1.0
        if leaf > 1e-6:
            leaf_n_status = max(0.0, min(1.0, (leaf_n / leaf) / max(n_conc["critical"]["leaf"], 1e-6)))
        if stem > 1e-6:
            stem_n_status = max(0.0, min(1.0, (stem_n / stem) / max(n_conc["critical"]["stem"], 1e-6)))
        structural_n_status = 0.65 * leaf_n_status + 0.35 * stem_n_status
        n_factor = max(0.0, min(1.0, math.sqrt(max(0.0, n_supply_ratio) * max(0.0, structural_n_status))))
    else:
        n_factor = 1.0
        soil_supply = total_n_demand
        total_uptake = total_n_demand

    combined_growth_factor = max(0.0, min(1.0, water_factor * n_factor))

    actual_leaf_growth = potential_leaf_growth * combined_growth_factor
    actual_stem_growth = potential_stem_growth * combined_growth_factor
    actual_spike_growth = potential_spike_growth * combined_growth_factor
    actual_grain_growth = potential_grain_growth * combined_growth_factor
    actual_root_growth = potential_root_growth * combined_growth_factor

    leaf += actual_leaf_growth
    stem += actual_stem_growth
    spike += actual_spike_growth
    grain += actual_grain_growth
    root += actual_root_growth

    leaf_senescence = float(phase_params["leaf_senescence_fraction"])
    stem_senescence = float(phase_params["stem_senescence_fraction"])
    spike_senescence = float(phase_params["spike_senescence_fraction"])
    leaf_turnover = leaf * leaf_senescence * max(0.90, 1.10 - 0.5 * water_factor)
    stem_turnover = stem * stem_senescence
    spike_turnover = spike * spike_senescence
    projected_leaf_lai = _leaf_area_from_biomass(max(0.0, leaf - leaf_turnover), leaf_n, base_sla, n_conc["critical"]["leaf"])
    shading_threshold = 4.4 + max(0.0, min(1.4, (bbch - 31.0) / 18.0 * 1.4))
    if shading_threshold is not None and projected_leaf_lai > shading_threshold:
        self_shading_fraction = min(0.28, 0.06 * (projected_leaf_lai - shading_threshold))
        extra_turnover = leaf * self_shading_fraction
        leaf_turnover += extra_turnover

    if phase in {"flowering", "grain_filling", "maturity"}:
        remobilization_fraction = float(phase_params["dm_remobilization_fraction"])
        senesced_dm = leaf_turnover + stem_turnover + spike_turnover
        remobilized_dm = senesced_dm * remobilization_fraction * (0.45 + 0.55 * min(water_factor, n_factor))
        grain += remobilized_dm
        dead_aboveground += max(0.0, senesced_dm - remobilized_dm)
        leaf = max(0.0, leaf - leaf_turnover)
        stem = max(0.0, stem - stem_turnover)
        spike = max(0.0, spike - spike_turnover)
    else:
        dead_aboveground += leaf_turnover + stem_turnover + spike_turnover
        leaf = max(0.0, leaf - leaf_turnover)
        stem = max(0.0, stem - stem_turnover)
        spike = max(0.0, spike - spike_turnover)

    actual_n_demand = {
        "leaf": actual_leaf_growth * n_conc["critical"]["leaf"],
        "stem": actual_stem_growth * n_conc["critical"]["stem"],
        "spike": actual_spike_growth * n_conc["critical"]["spike"],
        "grain": actual_grain_growth * n_conc["critical"]["grain"],
        "root": actual_root_growth * 0.012,
    }
    total_actual_n = sum(actual_n_demand.values())
    external_uptake = min(total_actual_n, soil_supply)
    remobilization_used = min(max(0.0, total_actual_n - external_uptake), remobilized_n)

    # Allocate external uptake by demand share.
    total_for_allocation = max(total_actual_n, 1e-6)
    for organ_key, organ_demand in actual_n_demand.items():
        allocated = external_uptake * organ_demand / total_for_allocation
        if organ_key == "leaf":
            leaf_n += allocated
        elif organ_key == "stem":
            stem_n += allocated
        elif organ_key == "spike":
            spike_n += allocated
        elif organ_key == "grain":
            grain_n += allocated
        else:
            root_n += allocated

    if remobilization_used > 0:
        leaf_take = min(max(0.0, leaf_n - leaf * n_conc["minimum"]["leaf"]), remobilization_used * 0.35)
        stem_take = min(max(0.0, stem_n - stem * n_conc["minimum"]["stem"]), remobilization_used * 0.45)
        spike_take = min(max(0.0, spike_n - spike * n_conc["minimum"]["spike"]), remobilization_used * 0.20)
        leaf_n -= leaf_take
        stem_n -= stem_take
        spike_n -= spike_take
        grain_n += leaf_take + stem_take + spike_take

    target_leaf_lai = _leaf_area_from_biomass(leaf, leaf_n, base_sla, n_conc["critical"]["leaf"])
    previous_lai = float(state.get("lai", 0.0) or 0.0)
    leaf_lai = _smooth_lai(previous_lai, target_leaf_lai, phase, mean_air_temp_c, water_factor, n_factor)
    total_biomass = max(0.0, leaf + stem + spike + grain + dead_aboveground)

    state.update(
        {
            "lai": round(leaf_lai, 3),
            "leaf_biomass_kg_ha": round(max(0.0, leaf), 3),
            "stem_biomass_kg_ha": round(max(0.0, stem), 3),
            "spike_biomass_kg_ha": round(max(0.0, spike), 3),
            "grain_biomass_kg_ha": round(max(0.0, grain), 3),
            "total_biomass_kg_ha": round(total_biomass, 3),
            "grain_weight_kg_ha": round(max(0.0, grain), 3),
            "leaf_n_kg_ha": round(max(0.0, leaf_n), 3),
            "stem_n_kg_ha": round(max(0.0, stem_n), 3),
            "spike_n_kg_ha": round(max(0.0, spike_n), 3),
            "grain_n_kg_ha": round(max(0.0, grain_n), 3),
            "total_plant_n_kg_ha": round(max(0.0, leaf_n + stem_n + spike_n + grain_n + root_n), 3),
            "daily_n_demand_kg_ha": round(total_n_demand, 3),
            "daily_n_uptake_kg_ha": round(external_uptake + remobilization_used, 3),
            "soil_n_supply_kg_ha": round(soil_supply, 3),
            "fertilizer_n_supply_kg_ha": round(fertilizer_supply, 3),
            "remobilized_n_kg_ha": round(remobilization_used, 3),
            "water_growth_factor": round(water_factor, 4),
            "n_growth_factor": round(n_factor, 4),
            "combined_growth_factor": round(combined_growth_factor, 4),
            "irrigation_attributable_n_leached_kg_ha": round(irrigation_n_leached, 4),
            "cumulative_irrigation_attributable_n_leached_kg_ha": round(
                float(state.get("cumulative_irrigation_attributable_n_leached_kg_ha", 0.0) or 0.0) + irrigation_n_leached,
                4,
            ),
            "_soil_n_layers_kg_ha": [round(x, 4) for x in soil_n_layers],
            "_fertilizer_n_pools": fert_pools,
            "_root_biomass_kg_ha": round(root, 4),
            "_root_n_kg_ha": round(root_n, 4),
            "_dead_aboveground_biomass_kg_ha": round(dead_aboveground, 4),
        }
    )
    return state
