from __future__ import annotations

from datetime import date
from datetime import timedelta

from .economics import estimate_fertilizer_cost
from .fertilizer_material import nutrient_to_product_amount


def recommend_fertilization(
    nutrient_key: str,
    decision_date,
    diagnosis: dict,
    demand_snapshot: dict,
    soil_supply: dict,
    fertilizer_pool: dict,
    stage_rules: dict,
    irrigation_signal: dict,
    rainfall_signal: dict,
    economics: dict,
    custom_products: list[dict] | None = None,
    fertilizer_history: list[dict] | None = None,
) -> dict:
    available_now = soil_supply["available_supply"] + fertilizer_pool["effective_remaining_nutrients"].get(nutrient_key, 0.0)
    demand_gap = max(0.0, demand_snapshot["remaining_demand"] - available_now)
    stage_cap = float(stage_rules["max_single_n_rate_kg_ha"])
    hard_stage_cap = float(stage_rules.get("hard_max_single_n_rate_kg_ha", stage_cap))
    stage_floor = float(stage_rules["min_effective_n_rate_kg_ha"])
    seasonal_total = float(demand_snapshot.get("seasonal_total_demand", 0.0))
    near_term_demand = float(demand_snapshot.get("near_term_demand", 0.0))
    cumulative_demand_to_date = float(diagnosis.get("cumulative_demand_to_date_kg_ha", 0.0))
    cumulative_supply_to_date = float(diagnosis.get("cumulative_supply_to_date_kg_ha", 0.0))
    trajectory_gap = max(0.0, 1.0 - min(float(diagnosis.get("lai_ratio", 1.0)), float(diagnosis.get("biomass_ratio", 1.0))))
    supply_ratio = float(diagnosis.get("supply_ratio", 1.0))
    cumulative_shortfall = max(0.0, cumulative_demand_to_date - cumulative_supply_to_date)

    # Allow the single-event cap to expand when high target yield and current crop demand justify it.
    adaptive_stage_cap = min(
        hard_stage_cap,
        stage_cap
        + max(0.0, seasonal_total - 220.0) * 0.06
        + cumulative_shortfall * 0.10
        + near_term_demand * 0.18
        + trajectory_gap * 20.0,
    )
    adaptive_stage_cap = max(stage_cap, adaptive_stage_cap)

    coverage_multiplier = float(stage_rules.get("coverage_multiplier", 1.6))
    practical_gap = float(stage_rules.get("practical_gap_kg_ha", stage_floor))
    buffered_demand = min(demand_snapshot["remaining_demand"], near_term_demand * coverage_multiplier)
    practical_trigger_gap = max(practical_gap, near_term_demand * 0.85)

    demand_driven_rate = max(
        cumulative_shortfall * 0.58 + buffered_demand * 0.88,
        buffered_demand * 0.78,
    )
    if diagnosis["status"] == "deficient":
        demand_driven_rate = max(
            demand_driven_rate,
            stage_floor + cumulative_shortfall * 0.62 + buffered_demand * 0.78 + trajectory_gap * 12.0,
        )
    elif diagnosis["status"] == "slightly_deficient":
        demand_driven_rate = max(
            demand_driven_rate,
            stage_floor + cumulative_shortfall * 0.42 + buffered_demand * 0.52 + trajectory_gap * 8.0,
        )

    proposed_n = max(
        0.0,
        min(
            adaptive_stage_cap,
            max(stage_floor if diagnosis["status"] in {"deficient", "slightly_deficient"} else 0.0, demand_driven_rate),
        ),
    )

    next_irrigation_date = irrigation_signal.get("next_irrigation_date")
    next_irrigation_depth = irrigation_signal.get("next_irrigation_depth_mm", 0.0)
    irrigation_soon = next_irrigation_date is not None and 0 <= (next_irrigation_date - decision_date).days <= 5
    rain_soon = rainfall_signal["rainfall_next_3d_mm"] >= stage_rules["rainfall_activation_mm"]
    heavy_rain_risk = rainfall_signal["rainfall_next_3d_mm"] >= stage_rules["heavy_rain_risk_mm"]
    too_dry = irrigation_signal["top_layer_relative_water"] < stage_rules["minimum_surface_water_for_broadcast"] and not irrigation_soon and not rain_soon
    recent_effective_n = float(diagnosis.get("recent_effective_n_kg_ha", 0.0))
    cumulative_supply_ratio = float(diagnosis.get("cumulative_supply_ratio", diagnosis.get("supply_ratio", 1.0)))

    candidates = [{"name": "no_fertilization", "score": 0.0, "apply_now": False, "method": "wait"}]

    if proposed_n > 0 and not too_dry and not heavy_rain_risk:
        candidates.append({"name": "fertilize_now", "score": 0.75, "apply_now": True, "method": stage_rules["default_method"]})
    if proposed_n > 0 and irrigation_soon:
        candidates.append({"name": "before_next_irrigation", "score": 0.90, "apply_now": True, "method": "topdress_before_irrigation"})
        if irrigation_signal.get("fertigation_supported"):
            candidates.append({"name": "with_next_irrigation", "score": 0.95, "apply_now": True, "method": "fertigation_with_irrigation"})
    if proposed_n > 0 and too_dry and (rain_soon or irrigation_soon):
        candidates.append({"name": "delay_to_moisture_window", "score": 0.80, "apply_now": False, "method": "delay_until_rain_or_irrigation"})
    if diagnosis["status"] == "adequate":
        candidates.append({"name": "reduce_dose", "score": 0.55, "apply_now": proposed_n >= stage_floor, "method": stage_rules["default_method"], "rate_multiplier": 0.6})

    if diagnosis["status"] == "adequate" and (recent_effective_n >= near_term_demand * 0.75 or cumulative_supply_ratio >= 0.98):
        best = {"name": "no_fertilization", "apply_now": False, "method": "not_needed", "score": 1.0, "rate_multiplier": 0.0}
    elif (
        diagnosis["status"] == "slightly_deficient"
        and cumulative_shortfall < practical_trigger_gap
        and (
            recent_effective_n >= near_term_demand * 0.8
            or cumulative_supply_ratio >= 0.92
            or (trajectory_gap < 0.10 and supply_ratio >= 0.75)
        )
    ):
        best = {"name": "no_fertilization", "apply_now": False, "method": "not_needed", "score": 1.0, "rate_multiplier": 0.0}
    elif diagnosis["status"] == "excessive" or proposed_n <= 0:
        best = {"name": "no_fertilization", "apply_now": False, "method": "not_needed", "score": 1.0, "rate_multiplier": 0.0}
    else:
        best = max(candidates, key=lambda item: item["score"])

    rate_multiplier = float(best.get("rate_multiplier", 1.0))
    final_n = round(proposed_n * rate_multiplier, 3)
    if diagnosis["status"] == "slightly_deficient" and supply_ratio > 0.65 and trajectory_gap < 0.08:
        final_n = round(min(final_n, adaptive_stage_cap * 0.9), 3)

    if not best["apply_now"] and best["name"] == "delay_to_moisture_window":
        window_start = next_irrigation_date or (decision_date + timedelta(days=1))
    else:
        window_start = decision_date if best["apply_now"] else (next_irrigation_date or decision_date)
    window_end = window_start + timedelta(days=max(1, int(stage_rules["application_window_days"]) - 1))

    product_name = stage_rules["default_n_product"]
    product_amount = nutrient_to_product_amount(product_name, nutrient_key, final_n, custom_products) if final_n > 0 else 0.0
    product_plan = []
    if final_n > 0 and product_amount:
        product_plan.append({"product_name": product_name, "amount_kg_ha": round(product_amount, 3)})

    history_dates = [
        date.fromisoformat(str(item.get("date"))[:10])
        for item in (fertilizer_history or [])
        if item.get("date")
    ]
    planned_event_dates = history_dates + ([window_start] if final_n > 0 else [])
    planned_event_count = len(history_dates) + (1 if final_n > 0 else 0)
    economics_result = estimate_fertilizer_cost(
        product_plan,
        economics.get("product_prices"),
        economics.get("fertilizer_event_labor_cost_cny_ha"),
        event_count=planned_event_count,
        event_dates=planned_event_dates,
    )
    grain_price = float(economics.get("grain_price_cny_per_kg", 0.0) or 0.0)
    stage_yield_gain_per_kg_n = float(stage_rules.get("yield_gain_kg_grain_per_kg_n", 0.0) or 0.0)
    expected_yield_gain = final_n * stage_yield_gain_per_kg_n
    expected_revenue_gain = expected_yield_gain * grain_price
    estimated_total_cost = float(economics_result.get("total_cost_cny_ha") or 0.0)
    expected_margin = expected_revenue_gain - estimated_total_cost
    if final_n > 0 and grain_price > 0 and stage_yield_gain_per_kg_n > 0 and expected_margin <= 0:
        final_n = 0.0
        product_plan = []
        economics_result = estimate_fertilizer_cost(
            product_plan,
            economics.get("product_prices"),
            economics.get("fertilizer_event_labor_cost_cny_ha"),
            event_count=len(history_dates),
            event_dates=history_dates,
        )
        best = {"name": "no_fertilization", "apply_now": False, "method": "not_economic", "score": 1.0, "rate_multiplier": 0.0}
        expected_yield_gain = 0.0
        expected_revenue_gain = 0.0
        expected_margin = 0.0
    reasons = list(stage_rules["base_reasons"])
    if irrigation_soon:
        reasons.append("irrigation opportunity is available soon, improving nitrogen uptake")
    if too_dry:
        reasons.append("surface soil is too dry for an immediate broadcast N application")
    if heavy_rain_risk:
        reasons.append("heavy rainfall risk raises near-term nitrogen loss potential")

    return {
        "apply_now": bool(best["apply_now"] and final_n > 0),
        "application_window": {"start_date": str(window_start), "end_date": str(window_end)},
        "method": best["method"] if final_n > 0 else "not_needed",
        "nutrient_plan": {"n_kg_ha": final_n, "p2o5_kg_ha": 0.0, "k2o_kg_ha": 0.0},
        "product_plan": product_plan,
        "economics": economics_result,
        "expected_yield_gain_kg_ha": round(expected_yield_gain, 3),
        "expected_revenue_gain_cny_ha": round(expected_revenue_gain, 3),
        "expected_margin_cny_ha": round(expected_margin, 3),
        "reason": reasons,
    }
