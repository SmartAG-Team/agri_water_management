from __future__ import annotations

import math
from copy import deepcopy
from datetime import date

from core.nutrition.fertilizer_material import product_to_nutrients

from crops.maize.growth_config import GROWTH_PARAMETERS, ORGAN_N_CONCENTRATIONS

from .bbch_mapping import (
    get_phase,
    get_process_phase,
    interpolate_process_parameters,
    normalize_iowa_stage,
    stage_between,
    stage_rank,
)
from .rooting import initial_root_depth_mm


PUBLIC_GROWTH_KEYS = (
    "lai",
    "leaf_biomass_kg_ha",
    "stem_biomass_kg_ha",
    "ear_biomass_kg_ha",
    "grain_biomass_kg_ha",
    "live_aboveground_biomass_kg_ha",
    "dead_aboveground_biomass_kg_ha",
    "total_biomass_kg_ha",
    "grain_weight_kg_ha",
    "harvest_index",
    "actual_growth_kg_ha",
    "grain_growth_kg_ha",
    "senesced_aboveground_biomass_kg_ha",
    "remobilized_dm_kg_ha",
    "leaf_n_kg_ha",
    "stem_n_kg_ha",
    "ear_n_kg_ha",
    "grain_n_kg_ha",
    "total_plant_n_kg_ha",
    "daily_n_demand_kg_ha",
    "daily_n_uptake_kg_ha",
    "daily_p2o5_demand_kg_ha",
    "daily_p2o5_uptake_kg_ha",
    "daily_k2o_demand_kg_ha",
    "daily_k2o_uptake_kg_ha",
    "soil_available_n_kg_ha",
    "soil_available_p2o5_kg_ha",
    "soil_available_k2o_kg_ha",
    "soil_n_supply_kg_ha",
    "soil_p2o5_supply_kg_ha",
    "soil_k2o_supply_kg_ha",
    "fertilizer_n_supply_kg_ha",
    "fertilizer_p2o5_supply_kg_ha",
    "fertilizer_k2o_supply_kg_ha",
    "remobilized_n_kg_ha",
    "root_biomass_kg_ha",
    "root_length_km_ha",
    "root_length_density_cm_cm3",
    "water_growth_factor",
    "n_growth_factor",
    "p_growth_factor",
    "k_growth_factor",
    "combined_growth_factor",
)

P_TO_P2O5 = 2.291
K_TO_K2O = 1.205
DEFAULT_OLSEN_P_MG_KG = 27.5
DEFAULT_AVAILABLE_K_MG_KG = 176.2
P2O5_DEMAND_PER_N = 0.32
K2O_DEMAND_PER_N = 0.68
SPECIFIC_ROOT_LENGTH_M_G = 85.0


def _organ_concentrations(bbch: int | str) -> dict[str, dict[str, float]]:
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
            p2o5_pct = float(item.get("p2o5_pct", 0.0) or 0.0)
            k2o_pct = float(item.get("k2o_pct", 0.0) or 0.0)
            amount = float(item.get("amount_kg_ha", 0.0) or 0.0)
            nutrients = {
                "N": round(amount * n_pct / 100.0, 3),
                "P2O5": round(amount * p2o5_pct / 100.0, 3),
                "K2O": round(amount * k2o_pct / 100.0, 3),
            }
        n_amount = float(nutrients.get("N", 0.0) or 0.0)
        p2o5_amount = float(nutrients.get("P2O5", 0.0) or 0.0)
        k2o_amount = float(nutrients.get("K2O", 0.0) or 0.0)
        if n_amount <= 0 and p2o5_amount <= 0 and k2o_amount <= 0:
            continue
        pools.append({
            "date": applied_date,
            "product_name": item.get("product_name", "custom_product"),
            "remaining_n_kg_ha": n_amount,
            "remaining_p2o5_kg_ha": p2o5_amount,
            "remaining_k2o_kg_ha": k2o_amount,
            "release_type": item.get("release_type"),
            "release_days": item.get("release_days"),
        })
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
        surface_bias = 1.6 if idx == 0 else (1.15 if idx == 1 else 0.80)
        weights.append(max(0.0, thickness * surface_bias))
    total = sum(weights) or 1.0
    return [round(mineral_n * weight / total, 4) for weight in weights]


def _layer_weights(soil_profile: list[dict] | None, surface_bias: float = 1.0) -> list[float]:
    if not soil_profile:
        return []
    weights = []
    for idx, layer in enumerate(soil_profile):
        thickness = float(layer["thickness_mm"])
        bias = max(0.25, surface_bias ** idx)
        weights.append(max(0.0, thickness * bias))
    total = sum(weights) or 1.0
    return [weight / total for weight in weights]


def _mg_kg_to_kg_ha(value_mg_kg: float, soil_profile: list[dict] | None, converter: float) -> float:
    if not soil_profile:
        return 0.0
    total = 0.0
    for layer in soil_profile:
        thickness_cm = float(layer["thickness_mm"]) / 10.0
        bulk_density = float(layer.get("bulk_density", 1.35))
        total += float(value_mg_kg) * bulk_density * thickness_cm * 0.1 * converter
    return max(0.0, total)


def _initial_soil_p2o5_layers(soil_test: dict, soil_profile: list[dict] | None) -> list[float]:
    weights = _layer_weights(soil_profile, surface_bias=0.55)
    if not weights:
        return []
    olsen_p = float(soil_test.get("olsen_p_mg_kg", DEFAULT_OLSEN_P_MG_KG) or DEFAULT_OLSEN_P_MG_KG)
    # Olsen P is an extractable P index; only a fraction of the converted pool is seasonally accessible.
    pool = _mg_kg_to_kg_ha(olsen_p, soil_profile, P_TO_P2O5) * 0.22
    return [round(pool * weight, 4) for weight in weights]


def _initial_soil_k2o_layers(soil_test: dict, soil_profile: list[dict] | None) -> list[float]:
    weights = _layer_weights(soil_profile, surface_bias=0.72)
    if not weights:
        return []
    available_k = soil_test.get("available_k_mg_kg", soil_test.get("exchangeable_k_mg_kg", DEFAULT_AVAILABLE_K_MG_KG))
    available_k = float(available_k or DEFAULT_AVAILABLE_K_MG_KG)
    # Exchangeable/available K is more plant-accessible than Olsen P, but still not fully available in one season.
    pool = _mg_kg_to_kg_ha(available_k, soil_profile, K_TO_K2O) * 0.38
    return [round(pool * weight, 4) for weight in weights]


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
    return {
        "lai": 0.0,
        "leaf_biomass_kg_ha": 0.0,
        "stem_biomass_kg_ha": 0.0,
        "ear_biomass_kg_ha": 0.0,
        "grain_biomass_kg_ha": 0.0,
        "live_aboveground_biomass_kg_ha": 0.0,
        "dead_aboveground_biomass_kg_ha": 0.0,
        "total_biomass_kg_ha": 0.0,
        "grain_weight_kg_ha": 0.0,
        "harvest_index": 0.0,
        "actual_growth_kg_ha": 0.0,
        "grain_growth_kg_ha": 0.0,
        "senesced_aboveground_biomass_kg_ha": 0.0,
        "remobilized_dm_kg_ha": 0.0,
        "leaf_n_kg_ha": 0.0,
        "stem_n_kg_ha": 0.0,
        "ear_n_kg_ha": 0.0,
        "grain_n_kg_ha": 0.0,
        "total_plant_n_kg_ha": 0.0,
        "daily_n_demand_kg_ha": 0.0,
        "daily_n_uptake_kg_ha": 0.0,
        "daily_p2o5_demand_kg_ha": 0.0,
        "daily_p2o5_uptake_kg_ha": 0.0,
        "daily_k2o_demand_kg_ha": 0.0,
        "daily_k2o_uptake_kg_ha": 0.0,
        "soil_available_n_kg_ha": 0.0,
        "soil_available_p2o5_kg_ha": 0.0,
        "soil_available_k2o_kg_ha": 0.0,
        "soil_n_supply_kg_ha": 0.0,
        "soil_p2o5_supply_kg_ha": 0.0,
        "soil_k2o_supply_kg_ha": 0.0,
        "fertilizer_n_supply_kg_ha": 0.0,
        "fertilizer_p2o5_supply_kg_ha": 0.0,
        "fertilizer_k2o_supply_kg_ha": 0.0,
        "remobilized_n_kg_ha": 0.0,
        "root_biomass_kg_ha": 0.0,
        "root_length_km_ha": 0.0,
        "root_length_density_cm_cm3": 0.0,
        "water_growth_factor": 1.0,
        "n_growth_factor": 1.0,
        "p_growth_factor": 1.0,
        "k_growth_factor": 1.0,
        "combined_growth_factor": 1.0,
        "_nutrition_enabled": nutrition_enabled,
        "_soil_n_layers_kg_ha": _initial_soil_n_layers(soil_test, soil_profile) if nutrition_enabled else [],
        "_soil_p2o5_layers_kg_ha": _initial_soil_p2o5_layers(soil_test, soil_profile) if nutrition_enabled else [],
        "_soil_k2o_layers_kg_ha": _initial_soil_k2o_layers(soil_test, soil_profile) if nutrition_enabled else [],
        "_organic_matter_g_kg": float(soil_test["organic_matter_g_kg"]) if nutrition_enabled else 0.0,
        "_fertilizer_n_pools": _fertilizer_history_to_pools(fertilizer_history, custom_products),
        "_root_biomass_kg_ha": 0.0,
        "_root_n_kg_ha": 0.0,
        "_root_depth_mm": initial_root_depth_mm(),
        "_dead_aboveground_biomass_kg_ha": 0.0,
    }


def extract_public_growth_state(state: dict) -> dict:
    public = {key: state[key] for key in PUBLIC_GROWTH_KEYS}
    public["spike_biomass_kg_ha"] = public["ear_biomass_kg_ha"]
    public["spike_n_kg_ha"] = public["ear_n_kg_ha"]
    return public


def _fertilizer_release_for_day(pools: list[dict], current_date: date, top_layer_relative_water: float, precipitation_mm: float, irrigation_mm: float) -> tuple[list[dict], dict[str, float]]:
    updated = []
    released = {"N": 0.0, "P2O5": 0.0, "K2O": 0.0}
    for item in pools:
        pool = deepcopy(item)
        if current_date < pool["date"]:
            updated.append(pool)
            continue
        days_since = max(0, (current_date - pool["date"]).days)
        if days_since <= 5:
            daily_fraction = 0.20
        elif days_since <= 18:
            daily_fraction = 0.08
        elif days_since <= 40:
            daily_fraction = 0.03
        else:
            daily_fraction = 0.008
        if pool.get("release_days") and (pool.get("release_type") == "coated_slow_release" or int(pool.get("release_days") or 0) > 30):
            daily_fraction = min(daily_fraction, 1.0 / max(1, int(pool["release_days"])))
        dissolution_factor = 0.70 if top_layer_relative_water < 0.25 and precipitation_mm + irrigation_mm < 5.0 else 1.0
        loss_factor = 0.86 if precipitation_mm + irrigation_mm >= 45.0 and days_since <= 3 else 1.0
        for nutrient, field in (("N", "remaining_n_kg_ha"), ("P2O5", "remaining_p2o5_kg_ha"), ("K2O", "remaining_k2o_kg_ha")):
            remaining = float(pool.get(field, 0.0) or 0.0)
            mobility = {"N": 1.0, "P2O5": 0.45, "K2O": 0.70}[nutrient]
            day_release = min(remaining, remaining * daily_fraction * dissolution_factor * loss_factor * mobility)
            pool[field] = max(0.0, remaining - day_release)
            released[nutrient] += day_release
        updated.append(pool)
    return updated, {key: round(value, 4) for key, value in released.items()}


def _extract_from_layers(
    pools: list[float],
    soil_water_by_layer: list[dict],
    root_activity_weights: list[float],
    root_zone_relative_available_water: float,
    mobility: float,
) -> tuple[list[float], float]:
    extracted = 0.0
    updated = []
    for idx, amount in enumerate(pools):
        weight = root_activity_weights[idx] if idx < len(root_activity_weights) else 0.0
        layer_rel = float(soil_water_by_layer[idx].get("relative_available_water", root_zone_relative_available_water)) if idx < len(soil_water_by_layer) else root_zone_relative_available_water
        extractable = float(amount) * weight * max(0.20, layer_rel) * mobility
        extracted += extractable
        updated.append(max(0.0, float(amount) - extractable))
    return updated, extracted


def _root_length_metrics(root_biomass_kg_ha: float, root_depth_mm: float) -> tuple[float, float]:
    root_length_km_ha = max(0.0, root_biomass_kg_ha * 1000.0 * SPECIFIC_ROOT_LENGTH_M_G / 1000.0)
    explored_volume_cm3_m2 = max(root_depth_mm / 10.0 * 10000.0, 1.0)
    length_cm_m2 = root_length_km_ha * 100000.0 / 10000.0
    root_length_density = length_cm_m2 / explored_volume_cm3_m2
    return round(root_length_km_ha, 3), round(root_length_density, 5)


def _leaf_area_from_biomass(leaf_biomass_kg_ha: float, leaf_n_kg_ha: float, base_sla_m2_kg: float, critical_leaf_n: float) -> float:
    if leaf_biomass_kg_ha <= 0:
        return 0.0
    actual_leaf_conc = leaf_n_kg_ha / max(leaf_biomass_kg_ha, 1e-6)
    n_modifier = max(0.60, min(1.00, actual_leaf_conc / max(critical_leaf_n, 1e-6)))
    effective_sla = base_sla_m2_kg * n_modifier
    return max(0.0, leaf_biomass_kg_ha / 1000.0 * effective_sla)


def _smooth_lai(previous_lai: float, target_lai: float, phase: str, mean_air_temp_c: float, water_factor: float, n_factor: float) -> float:
    if target_lai <= previous_lai:
        max_decline = {
            "leaf_development": 0.02,
            "rapid_vegetative": 0.04,
            "pre_tassel": 0.05,
            "tasseling_silking": 0.07,
            "kernel_development": 0.09,
            "grain_filling": 0.12,
            "maturity": 0.10,
        }.get(phase, 0.06)
        stress_modifier = min(1.35, 1.0 + 0.8 * (1.0 - min(water_factor, n_factor)))
        return max(target_lai, previous_lai - max_decline * stress_modifier)
    max_increase = {
        "leaf_development": 0.18,
        "rapid_vegetative": 0.28,
        "pre_tassel": 0.18,
        "tasseling_silking": 0.06,
        "kernel_development": 0.02,
        "grain_filling": 0.01,
        "maturity": 0.0,
    }.get(phase, 0.10)
    temp_modifier = max(0.35, min(1.10, (mean_air_temp_c - 5.0) / 18.0))
    stress_modifier = max(0.45, min(1.0, 0.55 * water_factor + 0.45 * n_factor))
    return min(target_lai, previous_lai + max_increase * temp_modifier * stress_modifier)


def update_growth_state(
    previous: dict,
    current_date: date,
    bbch: int | str,
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
) -> dict:
    state = deepcopy(previous)
    stage = normalize_iowa_stage(bbch)
    phase = get_process_phase(stage)
    phase_params = interpolate_process_parameters(stage)
    n_conc = _organ_concentrations(stage)
    state["_root_depth_mm"] = round(root_depth_mm, 3)

    if stage_between(stage, "VS", "VE"):
        state.update({
            "lai": 0.0,
            "live_aboveground_biomass_kg_ha": 0.0,
            "dead_aboveground_biomass_kg_ha": round(float(state.get("_dead_aboveground_biomass_kg_ha", 0.0) or 0.0), 3),
            "total_biomass_kg_ha": 0.0,
            "grain_weight_kg_ha": 0.0,
            "harvest_index": 0.0,
            "actual_growth_kg_ha": 0.0,
            "grain_growth_kg_ha": 0.0,
            "senesced_aboveground_biomass_kg_ha": 0.0,
            "remobilized_dm_kg_ha": 0.0,
            "daily_n_demand_kg_ha": 0.0,
            "daily_n_uptake_kg_ha": 0.0,
            "daily_p2o5_demand_kg_ha": 0.0,
            "daily_p2o5_uptake_kg_ha": 0.0,
            "daily_k2o_demand_kg_ha": 0.0,
            "daily_k2o_uptake_kg_ha": 0.0,
            "soil_available_n_kg_ha": round(sum(state.get("_soil_n_layers_kg_ha", [])), 3),
            "soil_available_p2o5_kg_ha": round(sum(state.get("_soil_p2o5_layers_kg_ha", [])), 3),
            "soil_available_k2o_kg_ha": round(sum(state.get("_soil_k2o_layers_kg_ha", [])), 3),
            "soil_n_supply_kg_ha": 0.0,
            "soil_p2o5_supply_kg_ha": 0.0,
            "soil_k2o_supply_kg_ha": 0.0,
            "fertilizer_n_supply_kg_ha": 0.0,
            "fertilizer_p2o5_supply_kg_ha": 0.0,
            "fertilizer_k2o_supply_kg_ha": 0.0,
            "remobilized_n_kg_ha": 0.0,
            "water_growth_factor": 1.0,
            "n_growth_factor": 1.0,
            "p_growth_factor": 1.0,
            "k_growth_factor": 1.0,
            "combined_growth_factor": 1.0,
        })
        return state

    leaf = float(state["leaf_biomass_kg_ha"])
    stem = float(state["stem_biomass_kg_ha"])
    ear = float(state["ear_biomass_kg_ha"])
    grain = float(state["grain_biomass_kg_ha"])
    root = float(state.get("_root_biomass_kg_ha", 0.0))
    dead_aboveground = float(state.get("_dead_aboveground_biomass_kg_ha", 0.0) or 0.0)
    leaf_n = float(state["leaf_n_kg_ha"])
    stem_n = float(state["stem_n_kg_ha"])
    ear_n = float(state["ear_n_kg_ha"])
    grain_n = float(state["grain_n_kg_ha"])
    root_n = float(state.get("_root_n_kg_ha", 0.0))
    nutrition_enabled = bool(state.get("_nutrition_enabled", True))
    soil_n_layers = list(state.get("_soil_n_layers_kg_ha", []))
    soil_p2o5_layers = list(state.get("_soil_p2o5_layers_kg_ha", []))
    soil_k2o_layers = list(state.get("_soil_k2o_layers_kg_ha", []))
    fert_pools = list(state.get("_fertilizer_n_pools", []))

    base_sla = float(phase_params["sla_m2_kg"])
    partition = dict(phase_params["partition"])
    partition_sum = sum(max(0.0, float(v)) for v in partition.values()) or 1.0
    partition = {k: max(0.0, float(v)) / partition_sum for k, v in partition.items()}
    current_lai = _leaf_area_from_biomass(leaf, leaf_n, base_sla, n_conc["critical"]["leaf"])
    intercepted_par = max(0.0, radiation_sum) * float(GROWTH_PARAMETERS["par_fraction"]) * (
        1.0 - math.exp(-float(GROWTH_PARAMETERS["light_extinction_coefficient"]) * max(0.0, current_lai))
    )
    temp_factor = max(0.0, min(1.10, (mean_air_temp_c - 6.0) / 20.0))
    maintenance_fraction = float(GROWTH_PARAMETERS["maintenance_respiration_fraction"])
    potential_growth = max(0.0, intercepted_par * float(phase_params["rue_g_mj"]) * 10.0 * temp_factor * (1.0 - maintenance_fraction))
    if stage_between(stage, "V1", "V5") and leaf + stem + ear + grain < 220.0:
        seed_reserve_growth = 55.0 * max(0.2, temp_factor) * max(0.0, 1.0 - (leaf + stem + ear + grain) / 260.0)
        potential_growth += seed_reserve_growth

    water_factor = 1.0 if potential_transpiration_mm <= 0.25 else max(0.0, min(1.0, actual_transpiration_mm / max(potential_transpiration_mm, 1e-6)))
    water_factor = min(water_factor, max(0.50, min(1.0, 0.45 + 0.55 * root_zone_relative_available_water)))

    soil_n_supply = 0.0
    soil_p2o5_supply = 0.0
    soil_k2o_supply = 0.0
    fertilizer_n_supply = 0.0
    fertilizer_p2o5_supply = 0.0
    fertilizer_k2o_supply = 0.0
    n_uptake = 0.0
    p2o5_uptake = 0.0
    k2o_uptake = 0.0
    remobilized_n = 0.0
    n_factor = 1.0
    p_factor = 1.0
    k_factor = 1.0

    potential_organs = {k: potential_growth * float(partition[k]) for k in ("leaf", "stem", "ear", "grain", "root")}

    if nutrition_enabled:
        om = _required_state_float(state, "_organic_matter_g_kg")
        if soil_n_layers:
            mineralization = om * 0.005 * max(0.30, root_zone_relative_available_water)
            soil_n_layers[0] += mineralization
        fert_pools, fertilizer_release = _fertilizer_release_for_day(
            fert_pools,
            current_date=current_date,
            top_layer_relative_water=top_layer_relative_water,
            precipitation_mm=precipitation_mm,
            irrigation_mm=irrigation_mm,
        )
        fertilizer_n_supply = fertilizer_release["N"]
        fertilizer_p2o5_supply = fertilizer_release["P2O5"]
        fertilizer_k2o_supply = fertilizer_release["K2O"]
        demand_n = (
            potential_organs["leaf"] * n_conc["critical"]["leaf"]
            + potential_organs["stem"] * n_conc["critical"]["stem"]
            + potential_organs["ear"] * n_conc["critical"]["ear"]
            + potential_organs["grain"] * n_conc["critical"]["grain"]
        )
        demand_p2o5 = demand_n * P2O5_DEMAND_PER_N
        demand_k2o = demand_n * K2O_DEMAND_PER_N
        soil_n_layers, soil_n_supply = _extract_from_layers(
            soil_n_layers,
            soil_water_by_layer,
            root_activity_weights,
            root_zone_relative_available_water,
            mobility=1.0,
        )
        soil_p2o5_layers, soil_p2o5_supply = _extract_from_layers(
            soil_p2o5_layers,
            soil_water_by_layer,
            root_activity_weights,
            root_zone_relative_available_water,
            mobility=0.18,
        )
        soil_k2o_layers, soil_k2o_supply = _extract_from_layers(
            soil_k2o_layers,
            soil_water_by_layer,
            root_activity_weights,
            root_zone_relative_available_water,
            mobility=0.42,
        )

        if stage_rank(stage) >= stage_rank("VT"):
            leaf_surplus = max(0.0, leaf_n - leaf * n_conc["minimum"]["leaf"])
            stem_surplus = max(0.0, stem_n - stem * n_conc["minimum"]["stem"])
            ear_surplus = max(0.0, ear_n - ear * n_conc["minimum"]["ear"])
            remobilized_n = (leaf_surplus + stem_surplus + ear_surplus) * phase_params["n_remobilization_fraction"]

        available_n = soil_n_supply + fertilizer_n_supply + remobilized_n
        available_p2o5 = soil_p2o5_supply + fertilizer_p2o5_supply
        available_k2o = soil_k2o_supply + fertilizer_k2o_supply
        if demand_n > 0:
            n_factor = max(0.45, min(1.0, available_n / demand_n))
            n_uptake = min(available_n, demand_n)
            p_factor = max(0.55, min(1.0, available_p2o5 / max(demand_p2o5, 1e-6)))
            k_factor = max(0.60, min(1.0, available_k2o / max(demand_k2o, 1e-6)))
            p2o5_uptake = min(available_p2o5, demand_p2o5)
            k2o_uptake = min(available_k2o, demand_k2o)
        else:
            n_factor = 1.0
            n_uptake = 0.0
            p_factor = 1.0
            k_factor = 1.0
            p2o5_uptake = 0.0
            k2o_uptake = 0.0
    else:
        demand_n = 0.0
        demand_p2o5 = 0.0
        demand_k2o = 0.0

    nutrient_factor = min(n_factor, p_factor, k_factor)
    combined_factor = max(0.35, min(1.0, water_factor * nutrient_factor))
    actual_growth = potential_growth * combined_factor
    actual_organs = {k: actual_growth * float(partition[k]) for k in ("leaf", "stem", "ear", "grain", "root")}
    direct_grain_growth = actual_organs["grain"]

    leaf += actual_organs["leaf"]
    stem += actual_organs["stem"]
    ear += actual_organs["ear"]
    grain += actual_organs["grain"]
    root += actual_organs["root"]

    leaf_loss = leaf * phase_params["leaf_senescence_fraction"]
    stem_loss = stem * phase_params["stem_senescence_fraction"]
    ear_loss = ear * phase_params["ear_senescence_fraction"]
    leaf = max(0.0, leaf - leaf_loss)
    stem = max(0.0, stem - stem_loss)
    ear = max(0.0, ear - ear_loss)

    senesced_aboveground = leaf_loss + stem_loss + ear_loss
    reproductive_stage = stage_rank(stage) >= stage_rank("VT")
    remobilized_dm = senesced_aboveground * phase_params["dm_remobilization_fraction"] if reproductive_stage else 0.0
    if reproductive_stage:
        grain += remobilized_dm
    dead_aboveground += max(0.0, senesced_aboveground - remobilized_dm)

    if nutrition_enabled and demand_n > 0:
        actual_leaf_n_demand = actual_organs["leaf"] * n_conc["critical"]["leaf"]
        actual_stem_n_demand = actual_organs["stem"] * n_conc["critical"]["stem"]
        actual_ear_n_demand = actual_organs["ear"] * n_conc["critical"]["ear"]
        actual_grain_n_demand = actual_organs["grain"] * n_conc["critical"]["grain"]
        total_actual_n_demand = actual_leaf_n_demand + actual_stem_n_demand + actual_ear_n_demand + actual_grain_n_demand
        if total_actual_n_demand > 0 and n_uptake > 0:
            scale = min(1.0, n_uptake / total_actual_n_demand)
            leaf_n += actual_leaf_n_demand * scale
            stem_n += actual_stem_n_demand * scale
            ear_n += actual_ear_n_demand * scale
            grain_n += actual_grain_n_demand * scale
        if remobilized_n > 0 and reproductive_stage:
            leaf_n = max(leaf * n_conc["minimum"]["leaf"], leaf_n - remobilized_n * 0.45)
            stem_n = max(stem * n_conc["minimum"]["stem"], stem_n - remobilized_n * 0.35)
            ear_n = max(ear * n_conc["minimum"]["ear"], ear_n - remobilized_n * 0.20)
            grain_n += remobilized_n

    target_lai = _leaf_area_from_biomass(leaf, leaf_n, phase_params["sla_m2_kg"], n_conc["critical"]["leaf"])
    smoothed_lai = _smooth_lai(float(state["lai"]), target_lai, phase, mean_air_temp_c, water_factor, n_factor)

    live_aboveground = leaf + stem + ear + grain
    total_biomass = live_aboveground + dead_aboveground
    harvest_index = grain / total_biomass if total_biomass > 0 else 0.0
    total_plant_n = leaf_n + stem_n + ear_n + grain_n + root_n
    root_length_km_ha, root_length_density = _root_length_metrics(root, root_depth_mm)

    state.update(
        {
            "lai": round(smoothed_lai, 3),
            "leaf_biomass_kg_ha": round(leaf, 3),
            "stem_biomass_kg_ha": round(stem, 3),
            "ear_biomass_kg_ha": round(ear, 3),
            "grain_biomass_kg_ha": round(grain, 3),
            "live_aboveground_biomass_kg_ha": round(live_aboveground, 3),
            "dead_aboveground_biomass_kg_ha": round(dead_aboveground, 3),
            "total_biomass_kg_ha": round(total_biomass, 3),
            "grain_weight_kg_ha": round(grain, 3),
            "harvest_index": round(harvest_index, 4),
            "actual_growth_kg_ha": round(actual_growth, 3),
            "grain_growth_kg_ha": round(direct_grain_growth + remobilized_dm, 3),
            "senesced_aboveground_biomass_kg_ha": round(senesced_aboveground, 3),
            "remobilized_dm_kg_ha": round(remobilized_dm, 3),
            "leaf_n_kg_ha": round(leaf_n, 3),
            "stem_n_kg_ha": round(stem_n, 3),
            "ear_n_kg_ha": round(ear_n, 3),
            "grain_n_kg_ha": round(grain_n, 3),
            "total_plant_n_kg_ha": round(total_plant_n, 3),
            "daily_n_demand_kg_ha": round(demand_n if nutrition_enabled else 0.0, 3),
            "daily_n_uptake_kg_ha": round(n_uptake, 3),
            "daily_p2o5_demand_kg_ha": round(demand_p2o5 if nutrition_enabled else 0.0, 3),
            "daily_p2o5_uptake_kg_ha": round(p2o5_uptake, 3),
            "daily_k2o_demand_kg_ha": round(demand_k2o if nutrition_enabled else 0.0, 3),
            "daily_k2o_uptake_kg_ha": round(k2o_uptake, 3),
            "soil_available_n_kg_ha": round(sum(soil_n_layers), 3),
            "soil_available_p2o5_kg_ha": round(sum(soil_p2o5_layers), 3),
            "soil_available_k2o_kg_ha": round(sum(soil_k2o_layers), 3),
            "soil_n_supply_kg_ha": round(soil_n_supply, 3),
            "soil_p2o5_supply_kg_ha": round(soil_p2o5_supply, 3),
            "soil_k2o_supply_kg_ha": round(soil_k2o_supply, 3),
            "fertilizer_n_supply_kg_ha": round(fertilizer_n_supply, 3),
            "fertilizer_p2o5_supply_kg_ha": round(fertilizer_p2o5_supply, 3),
            "fertilizer_k2o_supply_kg_ha": round(fertilizer_k2o_supply, 3),
            "remobilized_n_kg_ha": round(remobilized_n, 3),
            "root_biomass_kg_ha": round(root, 3),
            "root_length_km_ha": root_length_km_ha,
            "root_length_density_cm_cm3": root_length_density,
            "water_growth_factor": round(water_factor, 3),
            "n_growth_factor": round(n_factor, 3),
            "p_growth_factor": round(p_factor, 3),
            "k_growth_factor": round(k_factor, 3),
            "combined_growth_factor": round(combined_factor, 3),
            "_soil_n_layers_kg_ha": soil_n_layers,
            "_soil_p2o5_layers_kg_ha": soil_p2o5_layers,
            "_soil_k2o_layers_kg_ha": soil_k2o_layers,
            "_fertilizer_n_pools": fert_pools,
            "_root_biomass_kg_ha": round(root, 3),
            "_root_n_kg_ha": round(root_n, 3),
            "_dead_aboveground_biomass_kg_ha": round(dead_aboveground, 3),
        }
    )
    return state
