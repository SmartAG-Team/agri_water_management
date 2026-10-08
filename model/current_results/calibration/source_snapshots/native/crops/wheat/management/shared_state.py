from __future__ import annotations

from copy import deepcopy
from datetime import date


def _item_date(item: dict) -> date | None:
    raw = item.get("Date") or item.get("DateTime")
    if raw is None:
        return None
    return date.fromisoformat(str(raw)[:10])


def build_nutrition_payload(management_payload: dict, water_response: dict) -> dict:
    current_status = water_response["current_status"]
    payload = deepcopy(management_payload)
    payload["target_yield_kg_ha"] = management_payload.get("target_yield_kg_ha")
    payload["lai"] = current_status["lai"]
    payload["aboveground_biomass_kg_ha"] = current_status["total_biomass_kg_ha"]
    payload["biomass_partition"] = {
        "leaf_biomass_kg_ha": current_status["leaf_biomass_kg_ha"],
        "stem_biomass_kg_ha": current_status["stem_biomass_kg_ha"],
        "spike_biomass_kg_ha": current_status["spike_biomass_kg_ha"],
        "grain_biomass_kg_ha": current_status["grain_biomass_kg_ha"],
    }
    payload["root_zone_relative_available_water"] = current_status["root_zone_relative_available_water"]
    payload["current_status"] = current_status
    payload["layered_soil_water"] = current_status.get("soil_water_by_layer") or payload.get("layered_soil_water")
    payload["irrigation"] = {
        "irrigation_method": management_payload.get("irrigation_method"),
    }
    payload["irrigation_recommendation"] = water_response.get("action_recommendations") or []
    economics = deepcopy(management_payload.get("economic_parameters") or {})
    economics.update(deepcopy(management_payload.get("economics") or {}))
    payload["economics"] = economics
    payload["fertilizer_history"] = deepcopy(management_payload.get("fertilizer_history") or management_payload.get("applied_fertilizers") or [])
    if not payload.get("weather_forecast") and not payload.get("forecast_weather_data"):
        decision = date.fromisoformat(str(payload.get("decision_date")))
        payload["weather_forecast"] = [
            deepcopy(item)
            for item in management_payload.get("weather_data") or []
            if (item_date := _item_date(item)) is not None and item_date >= decision
        ]
    payload["soil_test"] = deepcopy(management_payload.get("soil_test") or {})
    payload["custom_products"] = deepcopy(management_payload.get("custom_products") or [])
    payload["fertilizer_inventory"] = deepcopy(management_payload.get("fertilizer_inventory") or [])
    return payload


def _nitrogen_factor(status: str) -> float:
    return {
        "disabled": 1.0,
        "adequate": 1.0,
        "slightly_deficient": 0.94,
        "deficient": 0.86,
        "excessive": 0.99,
    }.get(status, 1.0)


def _diagnostic_n_factor(nutrition_result: dict | None, status: str) -> float:
    base_factor = _nitrogen_factor(status)
    if not nutrition_result:
        return base_factor

    diagnostics = nutrition_result.get("diagnostics") or {}
    diagnosis = diagnostics.get("diagnosis") or {}
    demand_snapshot = diagnostics.get("demand_snapshot") or {}
    available_supply = float((diagnostics.get("soil_supply") or {}).get("available_supply", 0.0) or 0.0)
    effective_remaining = float(
        ((diagnostics.get("fertilizer_effective_pool") or {}).get("effective_remaining_nutrients") or {}).get("N", 0.0) or 0.0
    )
    near_term_demand = float(demand_snapshot.get("near_term_demand", 0.0) or 0.0)
    cumulative_ratio = float(diagnosis.get("cumulative_supply_ratio", 1.0) or 1.0)
    lai_ratio = float(diagnosis.get("lai_ratio", 1.0) or 1.0)
    biomass_ratio = float(diagnosis.get("biomass_ratio", 1.0) or 1.0)
    near_term_gap = float(diagnosis.get("near_term_gap_kg_ha", 0.0) or 0.0)
    recent_effective = float(diagnosis.get("recent_effective_n_kg_ha", 0.0) or 0.0)

    if status == "adequate":
        return 1.0

    demand_cover = 1.0
    if near_term_demand > 0:
        demand_cover = min(1.0, (available_supply + effective_remaining) / near_term_demand)
    trajectory_factor = min(1.0, max(0.85, 0.45 * lai_ratio + 0.55 * biomass_ratio))
    cumulative_factor = min(1.0, max(0.7, cumulative_ratio))
    recovery_credit = 1.0 if near_term_demand <= 0 else min(1.0, recent_effective / max(near_term_demand * 0.75, 1e-6))
    gap_penalty = 1.0 if near_term_gap <= 0 else max(0.7, 1.0 - near_term_gap / max(near_term_demand * 1.6, 18.0))

    factor = min(trajectory_factor, max(demand_cover, cumulative_factor * recovery_credit, gap_penalty))
    return round(max(base_factor, min(1.0, factor)), 4)


def _recommended_n_factor(nutrition_result: dict | None, current_n_factor: float) -> float:
    if not nutrition_result:
        return current_n_factor

    recommendation = nutrition_result.get("recommendation") or {}
    diagnostics = nutrition_result.get("diagnostics") or {}
    recommendation_n = float(recommendation.get("nutrient_plan", {}).get("n_kg_ha", 0.0) or 0.0)
    if recommendation_n <= 0:
        return current_n_factor

    recovery_assumptions = diagnostics.get("recovery_assumptions") or {}
    recovery_fraction = float(recovery_assumptions.get("topdress_recovery_fraction", 0.62))
    method = str(recommendation.get("method", ""))
    if "fertigation" in method:
        recovery_fraction = float(recovery_assumptions.get("fertigation_recovery_fraction", recovery_fraction))

    demand_snapshot = diagnostics.get("demand_snapshot") or {}
    near_term_demand = float(demand_snapshot.get("near_term_demand", 0.0) or 0.0)
    remaining_demand = float(demand_snapshot.get("remaining_demand", 0.0) or 0.0)
    actionable_gap = max(18.0, near_term_demand, remaining_demand * 0.22)
    effective_recommended_n = recommendation_n * recovery_fraction
    restored_fraction = max(0.0, min(1.0, effective_recommended_n / actionable_gap))
    return round(current_n_factor + (1.0 - current_n_factor) * restored_fraction, 4)


def season_plan_n_factors(nutrition_result: dict | None, current_n_factor: float) -> tuple[float, float]:
    if not nutrition_result:
        return current_n_factor, current_n_factor

    recommendation = nutrition_result.get("recommendation") or {}
    decision_date_str = str(nutrition_result.get("decision_date") or "")
    future_n = 0.0
    for item in recommendation.get("season_plan_recommendations", []) or []:
        action_date = str(item.get("Date") or "")
        if action_date and decision_date_str and action_date <= decision_date_str:
            continue
        future_n += float((item.get("nutrient_plan") or {}).get("n_kg_ha", 0.0) or 0.0)

    if future_n <= 0:
        return current_n_factor, _recommended_n_factor(nutrition_result, current_n_factor)

    diagnostics = nutrition_result.get("diagnostics") or {}
    demand_snapshot = diagnostics.get("demand_snapshot") or {}
    recovery_assumptions = diagnostics.get("recovery_assumptions") or {}
    recovery_fraction = float(recovery_assumptions.get("topdress_recovery_fraction", 0.62))
    remaining_demand = float(demand_snapshot.get("remaining_demand", 0.0) or 0.0)
    if remaining_demand <= 0:
        return current_n_factor, current_n_factor

    plan_importance = min(1.0, future_n * recovery_fraction / max(remaining_demand, 1e-6))
    limited_without_future = max(0.78, min(current_n_factor, 1.0 - 0.20 * plan_importance))
    recovered_with_plan = min(1.0, limited_without_future + (1.0 - limited_without_future) * 0.75)
    return round(limited_without_future, 4), round(recovered_with_plan, 4)


def combine_daily_management_state(
    management_mode: str,
    water_daily_records: list[dict],
    nutrition_result: dict | None,
) -> list[dict]:
    n_status = "disabled"
    if nutrition_result:
        n_status = nutrition_result.get("nutrition_status", {}).get("n_status", "adequate")
    n_factor = _diagnostic_n_factor(nutrition_result, n_status) if management_mode == "water_nutrition" else 1.0

    decision_date = None
    if nutrition_result and nutrition_result.get("decision_date"):
        decision_date = date.fromisoformat(str(nutrition_result["decision_date"]))

    baseline = None
    combined = []
    embedded_n = any("n_growth_factor" in item for item in water_daily_records)
    for item in water_daily_records:
        combined_item = deepcopy(item)
        actual_water_factor = float(item.get("water_growth_factor", {"LOW": 1.0, "MEDIUM": 0.88, "HIGH": 0.74}.get(item["stress_risk"], 1.0)) or 1.0)
        combined_item["water_stress_factor"] = round(actual_water_factor, 3)
        combined_item["n_status"] = n_status
        item_n_factor = float(item.get("n_growth_factor", n_factor) or n_factor) if management_mode == "water_nutrition" else 1.0
        combined_item["n_stress_factor"] = round(item_n_factor, 3)
        combined_item["n_stress_index"] = round(1.0 - item_n_factor, 3)
        actual_combined = float(item.get("combined_growth_factor", actual_water_factor * item_n_factor) or (actual_water_factor * item_n_factor))
        combined_item["combined_growth_factor"] = round(actual_combined, 3)

        item_date = date.fromisoformat(str(item["Date"]))
        if decision_date and item_date >= decision_date and not embedded_n:
            if baseline is None:
                baseline = deepcopy(combined_item)
            growth_delta_factor = n_factor
            lai_delta_factor = 1.0 - 0.45 * (1.0 - n_factor)
            for key in ("leaf_biomass_kg_ha", "stem_biomass_kg_ha", "spike_biomass_kg_ha", "grain_biomass_kg_ha", "grain_weight_kg_ha"):
                start_value = float(baseline.get(key, 0.0) or 0.0)
                raw_value = float(combined_item.get(key, 0.0) or 0.0)
                combined_item[key] = round(start_value + max(0.0, raw_value - start_value) * growth_delta_factor, 3)
            lai_start = float(baseline.get("lai", 0.0) or 0.0)
            lai_raw = float(combined_item.get("lai", 0.0) or 0.0)
            combined_item["lai"] = round(lai_start + (lai_raw - lai_start) * lai_delta_factor, 3)
            combined_item["total_biomass_kg_ha"] = round(
                combined_item["leaf_biomass_kg_ha"]
                + combined_item["stem_biomass_kg_ha"]
                + combined_item["spike_biomass_kg_ha"]
                + combined_item["grain_biomass_kg_ha"],
                3,
            )
        combined.append(combined_item)
    return combined


def build_yield_outlook(
    management_mode: str,
    water_response: dict,
    nutrition_result: dict | None,
    daily_management_state: list[dict] | None = None,
) -> dict:
    if daily_management_state:
        water_limited = float(daily_management_state[-1].get("grain_weight_kg_ha", 0.0) or 0.0)
    else:
        daily_records = water_response.get("daily_stress_risk") or []
        if daily_records:
            water_limited = float(daily_records[-1].get("grain_weight_kg_ha", 0.0))
        else:
            ranked = water_response.get("scenario_comparison") or []
            if ranked:
                water_limited = float(ranked[0].get("economics", {}).get("expected_yield_kg_ha", 0.0))
            else:
                water_limited = float(water_response["current_status"].get("grain_weight_kg_ha", 0.0))
    current_combined = None
    expected_with_recommendation = None
    recovery_kg_ha = None
    recovery_pct = None
    if management_mode == "water_nutrition":
        n_status = "disabled"
        if nutrition_result:
            n_status = nutrition_result.get("nutrition_status", {}).get("n_status", "adequate")
        current_n_factor = _diagnostic_n_factor(nutrition_result, n_status)
        planned_n_factor, recovered_planned_factor = season_plan_n_factors(nutrition_result, current_n_factor)
        if daily_management_state:
            current_combined = round(float(daily_management_state[-1].get("grain_weight_kg_ha", 0.0)), 3)
        else:
            current_combined = round(water_limited * planned_n_factor, 3)

        current_combined = min(current_combined, round(water_limited * planned_n_factor, 3))
        recovered_n_factor = max(_recommended_n_factor(nutrition_result, current_n_factor), recovered_planned_factor)
        expected_with_recommendation = round(water_limited * recovered_n_factor, 3)
        if current_combined is not None:
            expected_with_recommendation = max(current_combined, expected_with_recommendation)
            recovery_kg_ha = round(expected_with_recommendation - current_combined, 3)
            recovery_pct = round(0.0 if current_combined <= 0 else recovery_kg_ha / current_combined * 100.0, 3)
    return {
        "water_limited_yield_kg_ha": round(water_limited, 3),
        "water_nutrition_limited_yield_kg_ha": current_combined,
        "expected_yield_with_recommendation_kg_ha": expected_with_recommendation,
        "expected_yield_recovery_kg_ha": recovery_kg_ha,
        "expected_yield_recovery_pct": recovery_pct,
        "active_limitation": "water_nitrogen" if management_mode == "water_nutrition" else "water",
    }
