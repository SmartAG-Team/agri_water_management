from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta

from core.nutrition import (
    diagnose_nutrient_status,
    estimate_effective_remaining_nutrients,
    estimate_soil_nutrient_supply,
    estimate_fertilizer_cost,
    nutrient_to_product_amount,
    normalize_nutrition_request,
    recommend_fertilization,
)

from .bbch_npk_partition import phase_for_bbch
from .local_defaults import DEFAULT_RECOVERY_ASSUMPTIONS, DEFAULT_WHEAT_NUTRIENT_COEFFICIENTS, DEFAULT_WHEAT_N_SPLIT
from crops.wheat.phenology.config import parse_wheat_bbch_stage
from .quality_rules import grain_quality_hook
from .stage_rules import stage_rules_for_bbch


REFERENCE_STAGE_BIOMASS = {
    "establishment": 300.0,
    "tillering": 1800.0,
    "recovery": 2600.0,
    "jointing": 6500.0,
    "booting": 10500.0,
    "heading_flowering": 14000.0,
    "grain_filling": 16000.0,
    "maturity": 16500.0,
}


def _has_canopy_state(payload: dict) -> bool:
    current_status = payload.get("current_status") or {}
    return (
        payload.get("lai", current_status.get("lai")) is not None
        and payload.get("aboveground_biomass_kg_ha", current_status.get("total_biomass_kg_ha")) is not None
        and payload.get("root_zone_relative_available_water", current_status.get("root_zone_relative_available_water")) is not None
    )


def _item_date(item: dict) -> date | None:
    raw = item.get("Date") or item.get("DateTime")
    if raw is None:
        return None
    return date.fromisoformat(str(raw)[:10])


def _risk_from_status(status: str | None) -> str:
    return {
        "adequate": "LOW",
        "slightly_deficient": "MEDIUM",
        "deficient": "HIGH",
        "excessive": "MEDIUM",
    }.get(str(status or "").lower(), "LOW")


def _missing_canopy_simulation_fields(payload: dict, *, require_decision_date: bool) -> list[str]:
    missing = []
    if require_decision_date and not payload.get("decision_date"):
        missing.append("decision_date")
    for key in ("latitude", "longitude", "soil_type", "irrigation_method", "economic_parameters", "growth_stage", "soil_profile"):
        if payload.get(key) in (None, [], {}):
            missing.append(key)
    if payload.get("target_yield_kg_ha") is None:
        missing.append("target_yield_kg_ha")
    if not (payload.get("weather_data") or payload.get("weather_history") or payload.get("weather_daily")):
        missing.append("weather_data/weather_history/weather_daily")
    return missing


def _prepare_payload_with_simulated_canopy(payload: dict) -> dict:
    prepared = deepcopy(payload)
    if not prepared.get("decision_date"):
        return prepared

    if prepared.get("weather_data") and not prepared.get("weather_forecast") and not prepared.get("forecast_weather_data"):
        decision_day = str(prepared.get("decision_date") or "")[:10]
        prepared["weather_forecast"] = [
            item
            for item in prepared.get("weather_data") or []
            if str(item.get("DateTime") or item.get("Date") or "")[:10] >= decision_day
        ]

    if _has_canopy_state(prepared):
        return prepared

    missing = _missing_canopy_simulation_fields(prepared, require_decision_date=True)
    if missing:
        raise ValueError(
            "cannot simulate wheat canopy state for nutrition; missing required field(s): "
            + ", ".join(missing)
        )

    irrigation_body = deepcopy(prepared)
    if irrigation_body.get("weather_data") is None:
        irrigation_body["weather_data"] = irrigation_body.get("weather_history") or irrigation_body.get("weather_daily") or []
    if irrigation_body.get("forecast_weather_data") is None and irrigation_body.get("weather_forecast") is not None:
        irrigation_body["forecast_weather_data"] = irrigation_body["weather_forecast"]
    if irrigation_body.get("target_yield_kg_ha") is None:
        return prepared
    irrigation_body["_force_nutrition_coupling"] = True
    irrigation_body["_skip_forward_projection"] = True

    from crops.wheat.irrigation import WheatIrrigationModel

    irrigation_state = WheatIrrigationModel(
        latitude=float(irrigation_body["latitude"]),
        longitude=float(irrigation_body["longitude"]),
        soil_type=irrigation_body["soil_type"],
        irrigation_method=irrigation_body["irrigation_method"],
    ).run(irrigation_body).get("current_status") or {}

    prepared.setdefault("current_status", irrigation_state)
    if prepared.get("lai") is None:
        prepared["lai"] = irrigation_state.get("lai")
    if prepared.get("aboveground_biomass_kg_ha") is None:
        prepared["aboveground_biomass_kg_ha"] = irrigation_state.get("total_biomass_kg_ha")
    if prepared.get("root_zone_relative_available_water") is None:
        prepared["root_zone_relative_available_water"] = irrigation_state.get("root_zone_relative_available_water")
    if prepared.get("layered_soil_water") is None:
        prepared["layered_soil_water"] = irrigation_state.get("soil_water_by_layer")
    prepared.setdefault("_nutrition_canopy_source", "wheat_growth_model")
    return prepared


def _simulate_lai_from_stage(normalized: dict, phase: dict) -> float:
    expected_lai = float(phase["expected_lai"])
    biomass = float(normalized["aboveground_biomass_kg_ha"])
    if biomass <= 0.0:
        return 0.0 if int(normalized["current_bbch"]) < 9 else round(max(0.1, expected_lai * 0.25), 3)
    reference_biomass = max(REFERENCE_STAGE_BIOMASS.get(phase["phase_name"], 3000.0), 1.0)
    biomass_ratio = max(0.15, min(1.35, biomass / reference_biomass))
    return round(expected_lai * (biomass_ratio ** 0.65), 3)


def _process_demand_snapshot(normalized: dict, phase: dict, crop_activity_factor: float) -> dict:
    current_status = normalized.get("current_status") or {}
    current_total_plant_n = float(current_status.get("total_plant_n_kg_ha", 0.0) or 0.0)
    daily_n_demand = float(current_status.get("daily_n_demand_kg_ha", 0.0) or 0.0)
    remaining_growth_days = sum(
        1
        for item in normalized.get("growth_stage", [])
        if str(item.get("Date")) > str(normalized["decision_date"])
        and parse_wheat_bbch_stage(item.get("Stage", 0)) < 89
    )
    stage_multiplier = {
        "establishment": 0.5,
        "tillering": 1.1,
        "recovery": 1.25,
        "jointing": 1.3,
        "booting": 1.1,
        "heading_flowering": 0.9,
        "grain_filling": 0.55,
    }.get(phase["phase_name"], 0.25)
    future_window_days = min(remaining_growth_days, {"establishment": 12, "tillering": 24, "recovery": 18, "jointing": 16, "booting": 12, "heading_flowering": 8, "grain_filling": 5}.get(phase["phase_name"], 4))
    projected_future_demand = daily_n_demand * future_window_days * stage_multiplier * max(0.25, crop_activity_factor)
    cumulative_demand_to_date = current_total_plant_n + daily_n_demand * 1.5
    near_term_demand = daily_n_demand * max(3.0, min(8.0, 5.0 * crop_activity_factor + 1.0))
    seasonal_total = cumulative_demand_to_date + max(0.0, projected_future_demand)
    return {
        "nutrient_key": "N",
        "seasonal_total_demand": round(seasonal_total, 3),
        "cumulative_demand_to_date": round(cumulative_demand_to_date, 3),
        "remaining_demand": round(max(0.0, seasonal_total - cumulative_demand_to_date), 3),
        "near_term_demand": round(max(0.0, near_term_demand), 3),
        "biomass_progress": round(0.0 if seasonal_total <= 0 else min(1.0, cumulative_demand_to_date / seasonal_total), 4),
    }


def _build_basal_recommendation(normalized: dict) -> dict | None:
    planting_date = normalized.get("planting_date")
    if planting_date is None:
        return None

    target_yield_t = float(normalized["target_yield_kg_ha"]) / 1000.0
    seasonal_n_demand = target_yield_t * DEFAULT_WHEAT_NUTRIENT_COEFFICIENTS["N"]
    soil_test = normalized["soil_test"]
    mineral_n = soil_test.get("mineral_n_kg_ha")
    if mineral_n is None and soil_test.get("alkali_hydrolyzable_n_mg_kg") is not None:
        mineral_n = float(soil_test["alkali_hydrolyzable_n_mg_kg"]) * 0.32
    if mineral_n is None:
        raise ValueError("soil_test.mineral_n_kg_ha or soil_test.alkali_hydrolyzable_n_mg_kg is required")
    if soil_test.get("organic_matter_g_kg") is None:
        raise ValueError("soil_test.organic_matter_g_kg is required")
    mineral_n = float(mineral_n)
    organic_matter = float(soil_test["organic_matter_g_kg"])

    expected_soil_contribution = min(120.0, mineral_n * 0.65 + organic_matter * 2.0)
    fertilizer_need = max(0.0, seasonal_n_demand - expected_soil_contribution)
    if fertilizer_need <= 0:
        return None

    split = DEFAULT_WHEAT_N_SPLIT
    basal_fraction = split["basal_fraction"]
    if mineral_n < 40.0:
        basal_fraction += split["basal_fraction_low_soil_n_bonus"]
    elif mineral_n > 80.0:
        basal_fraction -= split["basal_fraction_high_soil_n_penalty"]
    if organic_matter < 12.0:
        basal_fraction += split["basal_fraction_low_om_bonus"]
    if target_yield_t >= 9.0:
        basal_fraction += split["basal_fraction_high_yield_bonus"]
    basal_fraction = max(split["basal_fraction_min"], min(split["basal_fraction_max"], basal_fraction))

    basal_n = max(split["basal_n_min_kg_ha"], min(split["basal_n_max_kg_ha"], fertilizer_need * basal_fraction))
    basal_n = round(basal_n, 3)
    product_amount = round(basal_n / (split["default_basal_n_pct"] / 100.0), 3)
    action_date = str(planting_date)

    return {
        "Date": action_date,
        "recommendationCode": "BASE_FERT_PLAN",
        "treatmentWindowCode": "BASELINE",
        "application_window_days": 1,
        "method": "basal_incorporated",
        "nutrient_plan": {"n_kg_ha": basal_n, "p2o5_kg_ha": 0.0, "k2o_kg_ha": 0.0},
        "product_plan": [{"product_name": split["default_basal_product"], "amount_kg_ha": product_amount}],
        "reason": [
            f"recommended basal N is based on target yield {normalized['target_yield_kg_ha']:.0f} kg/ha",
            f"soil initial mineral N {mineral_n:.1f} kg/ha and organic matter {organic_matter:.1f} g/kg were considered",
        ],
    }


def _build_future_topdress_plan(
    normalized: dict,
    current_bbch: int,
    diagnosis: dict,
    demand_snapshot: dict,
    soil_supply: dict,
    fertilizer_pool: dict,
    stage_rules: dict,
    economics: dict,
    irrigation_method: str,
) -> list[dict]:
    if current_bbch >= 83:
        return []

    growth_stage = normalized.get("growth_stage") or []
    decision_date = normalized["decision_date"]
    jointing_date = None
    heading_date = None
    jointing_stage = 31
    heading_stage = 51
    for item in growth_stage:
        item_date = type(decision_date).fromisoformat(str(item.get("Date")))
        item_stage = parse_wheat_bbch_stage(item.get("Stage", 0))
        if item_date < decision_date:
            continue
        if jointing_date is None and item_stage >= 31:
            jointing_date = item_date
            jointing_stage = item_stage
        if heading_date is None and item_stage >= 51:
            heading_date = item_date
            heading_stage = item_stage
        if jointing_date is not None and heading_date is not None:
            break

    available_future_credit = (
        float(soil_supply.get("available_supply", 0.0) or 0.0) * 0.40
        + float((fertilizer_pool.get("effective_remaining_nutrients") or {}).get("N", 0.0) or 0.0) * 0.70
    )
    target_yield_t = float(normalized["target_yield_kg_ha"]) / 1000.0
    seasonal_total_need = target_yield_t * DEFAULT_WHEAT_NUTRIENT_COEFFICIENTS["N"]
    soil_test = normalized["soil_test"]
    mineral_n = soil_test.get("mineral_n_kg_ha")
    if mineral_n is None and soil_test.get("alkali_hydrolyzable_n_mg_kg") is not None:
        mineral_n = float(soil_test["alkali_hydrolyzable_n_mg_kg"]) * 0.32
    if mineral_n is None:
        raise ValueError("soil_test.mineral_n_kg_ha or soil_test.alkali_hydrolyzable_n_mg_kg is required")
    if soil_test.get("organic_matter_g_kg") is None:
        raise ValueError("soil_test.organic_matter_g_kg is required")
    mineral_n = float(mineral_n)
    organic_matter = float(soil_test["organic_matter_g_kg"])
    expected_soil_contribution = min(130.0, mineral_n * 0.70 + organic_matter * 2.2)
    applied_n = sum(float((item.get("nutrients_kg_ha") or {}).get("N", 0.0) or 0.0) for item in normalized.get("fertilizer_history", []))
    management_gap = max(0.0, seasonal_total_need - expected_soil_contribution - applied_n)
    process_gap = max(0.0, float(demand_snapshot["remaining_demand"]) - available_future_credit)
    seasonal_gap = max(process_gap, management_gap)
    if seasonal_gap < 20.0:
        return []

    grain_price = float(economics.get("grain_price_cny_per_kg", 0.0) or 0.0)
    product_prices = economics.get("product_prices") or {}
    management_intensity = max(0.0, min(1.0, (target_yield_t - 7.5) / 2.5))
    historical_event_dates = [item["date"] for item in normalized.get("fertilizer_history", []) if item.get("date")]
    recent_n_days = None
    for item in normalized.get("fertilizer_history", []):
        n_amount = float((item.get("nutrients_kg_ha") or {}).get("N", 0.0) or 0.0)
        if n_amount <= 0:
            continue
        days_since = (decision_date - item["date"]).days
        if days_since >= 0:
            recent_n_days = days_since if recent_n_days is None else min(recent_n_days, days_since)

    def _economically_viable(n_rate: float, planned_bbch: int, planned_date) -> bool:
        planned_rules = stage_rules_for_bbch(planned_bbch, irrigation_method=irrigation_method)
        gain_per_n = float(planned_rules.get("yield_gain_kg_grain_per_kg_n", 0.0) or 0.0)
        product_amount = nutrient_to_product_amount(planned_rules["default_n_product"], "N", n_rate) or 0.0
        expected_revenue = grain_price * gain_per_n * n_rate
        planned_dates = historical_event_dates + [planned_date]
        for existing in plan:
            if existing.get("Date"):
                planned_dates.append(type(decision_date).fromisoformat(str(existing["Date"])))
        economics_result = estimate_fertilizer_cost(
            [{"product_name": planned_rules["default_n_product"], "amount_kg_ha": round(product_amount, 3)}],
            product_prices,
            economics.get("fertilizer_event_labor_cost_cny_ha"),
            event_count=len(planned_dates),
            event_dates=planned_dates,
        )
        required_margin = float(economics_result.get("total_cost_cny_ha") or 0.0)
        intensity_multiplier = 1.18 + 0.16 * max(0, len(planned_dates) - 1)
        return expected_revenue >= required_margin * intensity_multiplier

    plan = []
    current_status = diagnosis.get("status")
    allow_follow_up_split = current_status == "deficient"

    if jointing_date is not None and current_bbch < 31:
        jointing_rules = stage_rules_for_bbch(jointing_stage, irrigation_method=irrigation_method)
        default_product = jointing_rules["default_n_product"]
        max_single = float(jointing_rules.get("hard_max_single_n_rate_kg_ha", jointing_rules["max_single_n_rate_kg_ha"]))
        min_effective = float(jointing_rules["min_effective_n_rate_kg_ha"])
        base_share = 0.62 + 0.12 * management_intensity
        if current_status == "deficient":
            base_share += 0.08
        elif current_status == "adequate":
            base_share -= 0.05
        main_n = min(max_single, max(min_effective, seasonal_gap * base_share))
        if current_status == "deficient":
            main_n = min(max_single, max(main_n, seasonal_gap * 0.50))
        main_n = round(main_n, 3)
        if _economically_viable(main_n, jointing_stage, jointing_date):
            product_amount = nutrient_to_product_amount(default_product, "N", main_n) or 0.0
            plan.append(
                {
                    "Date": str(jointing_date),
                    "recommendationCode": "TOPDRESS_PLAN",
                    "treatmentWindowCode": "JOINTING",
                    "application_window_days": int(jointing_rules.get("application_window_days", 3)),
                    "method": "topdress_before_irrigation",
                    "nutrient_plan": {"n_kg_ha": main_n, "p2o5_kg_ha": 0.0, "k2o_kg_ha": 0.0},
                    "product_plan": [{"product_name": default_product, "amount_kg_ha": round(product_amount, 3)}],
                    "reason": [
                        "main spring topdress is scheduled for jointing to support rapid biomass and spike growth",
                    ],
                }
            )
            seasonal_gap = max(0.0, seasonal_gap - main_n * 0.72)
    elif 31 <= current_bbch < 49 and (recent_n_days is None or recent_n_days > 10):
        if current_status == "adequate" and recent_n_days is not None:
            return plan
        jointing_rules = stage_rules_for_bbch(current_bbch, irrigation_method=irrigation_method)
        default_product = jointing_rules["default_n_product"]
        max_single = float(jointing_rules.get("hard_max_single_n_rate_kg_ha", jointing_rules["max_single_n_rate_kg_ha"]))
        min_effective = float(jointing_rules["min_effective_n_rate_kg_ha"])
        main_n = min(max_single, max(min_effective, seasonal_gap * (0.58 + 0.10 * management_intensity)))
        if current_status == "adequate":
            main_n = min(main_n, seasonal_gap * 0.42)
        main_n = round(main_n, 3)
        if main_n >= min_effective and _economically_viable(main_n, current_bbch, decision_date):
            product_amount = nutrient_to_product_amount(default_product, "N", main_n) or 0.0
            plan.append(
                {
                    "Date": str(decision_date),
                    "recommendationCode": "TOPDRESS_PLAN",
                    "treatmentWindowCode": "JOINTING",
                    "application_window_days": int(jointing_rules.get("application_window_days", 3)),
                    "method": "topdress_before_irrigation",
                    "nutrient_plan": {"n_kg_ha": main_n, "p2o5_kg_ha": 0.0, "k2o_kg_ha": 0.0},
                    "product_plan": [{"product_name": default_product, "amount_kg_ha": round(product_amount, 3)}],
                    "reason": [
                        "jointing-stage demand justifies an immediate main spring topdress",
                    ],
                }
            )
            seasonal_gap = max(0.0, seasonal_gap - main_n * 0.72)

    if heading_date is not None and seasonal_gap >= 18.0 and current_bbch < 51 and allow_follow_up_split:
        heading_rules = stage_rules_for_bbch(heading_stage, irrigation_method=irrigation_method)
        default_product = heading_rules["default_n_product"]
        late_n = min(float(heading_rules.get("hard_max_single_n_rate_kg_ha", 45.0)), max(float(heading_rules["min_effective_n_rate_kg_ha"]), seasonal_gap * (0.26 + 0.08 * management_intensity)))
        late_n = round(late_n, 3)
        if late_n >= float(heading_rules["min_effective_n_rate_kg_ha"]) and _economically_viable(late_n, heading_stage, heading_date):
            product_amount = nutrient_to_product_amount(default_product, "N", late_n) or 0.0
            plan.append(
                {
                    "Date": str(heading_date),
                    "recommendationCode": "TOPDRESS_PLAN",
                    "treatmentWindowCode": "HEADING",
                    "application_window_days": int(heading_rules.get("application_window_days", 3)),
                    "method": "topdress_before_irrigation",
                    "nutrient_plan": {"n_kg_ha": late_n, "p2o5_kg_ha": 0.0, "k2o_kg_ha": 0.0},
                    "product_plan": [{"product_name": default_product, "amount_kg_ha": round(product_amount, 3)}],
                    "reason": [
                        "a second topdress is reserved for reproductive demand and grain set support",
                    ],
                }
            )

    return plan


class WheatNutritionModel:
    def run(self, payload: dict) -> dict:
        if not payload.get("decision_date"):
            return self._run_daily_decisions(payload)
        payload = _prepare_payload_with_simulated_canopy(payload)
        normalized = normalize_nutrition_request(payload)
        bbch = normalized["current_bbch"]
        phase = phase_for_bbch(bbch)
        forecast_temps = [float(item["temperature_2m_mean"]) for item in normalized["weather_forecast"][:5]]
        mean_temp_next_5d = sum(forecast_temps) / max(len(forecast_temps), 1)
        crop_activity_factor = 1.0
        if phase["phase_name"] in {"establishment", "tillering"}:
            if mean_temp_next_5d < 4.5:
                crop_activity_factor = 0.22
            elif mean_temp_next_5d < 7.0:
                crop_activity_factor = 0.42
            elif mean_temp_next_5d < 10.0:
                crop_activity_factor = 0.65
        elif phase["phase_name"] == "recovery":
            if mean_temp_next_5d < 6.0:
                crop_activity_factor = 0.45
            elif mean_temp_next_5d < 9.0:
                crop_activity_factor = 0.68
        elif phase["phase_name"] == "grain_filling":
            crop_activity_factor = 0.65
            if bbch >= 77:
                crop_activity_factor = 0.38
            if bbch >= 83:
                crop_activity_factor = 0.25

        if normalized["lai"] is None:
            normalized["lai"] = _simulate_lai_from_stage(normalized, phase)
            normalized["assumptions"].append(
                "lai missing; simulated from wheat stage, biomass, and target canopy trajectory"
            )
        elif payload.get("_nutrition_canopy_source") == "wheat_growth_model":
            normalized["assumptions"].append("lai simulated from wheat growth model")
        lai_ratio = normalized["lai"] / max(phase["expected_lai"], 0.2)
        biomass_ratio = normalized["aboveground_biomass_kg_ha"] / max(REFERENCE_STAGE_BIOMASS.get(phase["phase_name"], 3000.0), 1.0)

        demand_snapshot = _process_demand_snapshot(normalized, phase, crop_activity_factor)

        soil_supply = estimate_soil_nutrient_supply(
            nutrient_key="N",
            soil_test=normalized["soil_test"],
            layered_soil_water=normalized["layered_soil_water"],
            root_zone_relative_available_water=normalized["root_zone_relative_available_water"],
            stage_access_factor=phase["window_fraction"] + 0.35,
        )

        top_layer_raw = (
            float(normalized["layered_soil_water"][0].get("relative_available_water", normalized["root_zone_relative_available_water"]))
            if normalized["layered_soil_water"]
            else normalized["root_zone_relative_available_water"]
        )
        rainfall_next_3d = sum(float(item["precipitation_sum"]) for item in normalized["weather_forecast"][:3])
        next_irrigation = None
        next_irrigation_depth = 0.0
        fertigation_supported = False
        for item in normalized["irrigation_recommendation"]:
            dt = item.get("recommendedIrrigationDate") or item.get("Date")
            if dt:
                next_irrigation = normalized["decision_date"] if str(dt) == str(normalized["decision_date"]) else type(normalized["decision_date"]).fromisoformat(str(dt))
                next_irrigation_depth = float(item.get("recommendedGrossDepthMm", item.get("recommended_gross_depth_mm", 0.0)) or 0.0)
                break
        irrigation_method = (normalized["irrigation"].get("irrigation_method") or payload.get("irrigation_method") or "").lower()
        fertigation_supported = irrigation_method in {"drip", "micro-sprinkler"}

        fertilizer_pool = estimate_effective_remaining_nutrients(
            fertilizer_history=normalized["fertilizer_history"],
            decision_date=normalized["decision_date"],
            top_layer_relative_water=top_layer_raw,
            rainfall_next_3d_mm=rainfall_next_3d,
            irrigation_next_5d_mm=next_irrigation_depth,
        )

        soil_details = soil_supply["details"]
        biomass_progress = float(demand_snapshot["biomass_progress"])
        cumulative_soil_supply = (
            float(soil_details.get("initial_available_n_kg_ha", 0.0)) * min(1.0, 0.42 + 0.58 * biomass_progress)
            + float(soil_details.get("mineralization_n_kg_ha", 0.0)) * max(0.2, phase["uptake_fraction"])
        )
        cumulative_supply_to_date = cumulative_soil_supply + fertilizer_pool["cumulative_available_nutrients"]["N"]

        diagnosis = diagnose_nutrient_status(
            nutrient_key="N",
            lai_ratio=lai_ratio,
            biomass_ratio=biomass_ratio,
            available_nutrient_kg_ha=soil_supply["available_supply"] + fertilizer_pool["effective_remaining_nutrients"]["N"],
            cumulative_supply_to_date_kg_ha=cumulative_supply_to_date,
            cumulative_demand_to_date_kg_ha=demand_snapshot["cumulative_demand_to_date"],
            remaining_demand_kg_ha=demand_snapshot["remaining_demand"],
            near_term_demand_kg_ha=demand_snapshot["near_term_demand"],
            recent_effective_n_kg_ha=fertilizer_pool["recent_available_nutrients"]["N"],
            phase_name=phase["phase_name"],
        )

        stage_rules = stage_rules_for_bbch(bbch, irrigation_method=irrigation_method)
        quality_hook = grain_quality_hook(bbch, protein_target=payload.get("protein_target_pct"))
        if quality_hook["enabled"]:
            stage_rules["max_single_n_rate_kg_ha"] += quality_hook["extra_n_kg_ha"]

        recommendation = recommend_fertilization(
            nutrient_key="N",
            decision_date=normalized["decision_date"],
            diagnosis=diagnosis,
            demand_snapshot=demand_snapshot,
            soil_supply=soil_supply,
            fertilizer_pool=fertilizer_pool,
            stage_rules=stage_rules,
            irrigation_signal={
                "top_layer_relative_water": top_layer_raw,
                "next_irrigation_date": next_irrigation,
                "next_irrigation_depth_mm": next_irrigation_depth,
                "fertigation_supported": fertigation_supported,
            },
            rainfall_signal={"rainfall_next_3d_mm": rainfall_next_3d},
            economics=normalized["economics"],
            custom_products=normalized["custom_products"],
            fertilizer_history=normalized["fertilizer_history"],
        )

        growth_stage_lookup = {
            str(item.get("Date")): parse_wheat_bbch_stage(item.get("Stage"))
            for item in normalized.get("growth_stage", [])
            if item.get("Date") and item.get("Stage") is not None
        }
        recent_n_application = False
        min_days_since_n_application = None
        for item in normalized.get("fertilizer_history", []):
            n_amount = float((item.get("nutrients_kg_ha") or {}).get("N", 0.0) or 0.0)
            if n_amount <= 0:
                continue
            days_since_n = (normalized["decision_date"] - item["date"]).days
            if 0 <= days_since_n <= 30:
                recent_n_application = True
            if days_since_n >= 0:
                min_days_since_n_application = days_since_n if min_days_since_n_application is None else min(min_days_since_n_application, days_since_n)
        jointing_date = None
        heading_date = None
        for item in normalized.get("growth_stage", []):
            item_date = str(item.get("Date"))
            item_stage = parse_wheat_bbch_stage(item.get("Stage")) if item.get("Stage") is not None else None
            if item_stage is None:
                continue
            stage_date = type(normalized["decision_date"]).fromisoformat(item_date)
            if not (normalized["decision_date"] < stage_date <= normalized["decision_date"] + timedelta(days=10)):
                continue
            if jointing_date is None and item_stage >= 31:
                jointing_date = stage_date
            if heading_date is None and item_stage >= 51:
                heading_date = stage_date
            if jointing_date is not None and heading_date is not None:
                break

        if (
            recommendation.get("apply_now")
            and phase["phase_name"] in {"tillering", "recovery"}
            and diagnosis["status"] in {"slightly_deficient", "deficient"}
            and jointing_date is not None
            and recent_n_application
        ):
            recommendation = {
                "apply_now": False,
                "application_window": {
                    "start_date": str(jointing_date),
                    "end_date": str(jointing_date + timedelta(days=2)),
                },
                "method": "topdress_before_irrigation",
                "nutrient_plan": recommendation.get("nutrient_plan", {"n_kg_ha": 0.0, "p2o5_kg_ha": 0.0, "k2o_kg_ha": 0.0}),
                "product_plan": recommendation.get("product_plan", []),
                "economics": recommendation.get("economics", {"estimated_cost_cny_ha": 0.0}),
                "reason": [
                    "current deficiency is mild and the crop is close to jointing, so N is consolidated into the main jointing topdress",
                ],
            }

        if (
            recommendation.get("apply_now")
            and phase["phase_name"] == "booting"
            and diagnosis["status"] == "slightly_deficient"
            and heading_date is not None
            and recent_n_application
            and not payload.get("protein_target_pct")
        ):
            recommendation = {
                "apply_now": False,
                "application_window": {
                    "start_date": str(heading_date),
                    "end_date": str(heading_date + timedelta(days=2)),
                },
                "method": "topdress_before_irrigation",
                "nutrient_plan": recommendation.get("nutrient_plan", {"n_kg_ha": 0.0, "p2o5_kg_ha": 0.0, "k2o_kg_ha": 0.0}),
                "product_plan": recommendation.get("product_plan", []),
                "economics": recommendation.get("economics", {"estimated_cost_cny_ha": 0.0}),
                "reason": [
                    "booting-stage deficiency is mild and heading is imminent, so the topdress is consolidated into one later reproductive application",
                ],
            }

        if (
            bbch >= 61
            and not payload.get("protein_target_pct")
            and recommendation.get("apply_now")
            and diagnosis["status"] != "deficient"
        ):
            recommendation = {
                "apply_now": False,
                "application_window": {"start_date": str(normalized["decision_date"]), "end_date": str(normalized["decision_date"] + timedelta(days=2))},
                "method": "not_needed",
                "nutrient_plan": {"n_kg_ha": 0.0, "p2o5_kg_ha": 0.0, "k2o_kg_ha": 0.0},
                "product_plan": [],
                "economics": {"estimated_cost_cny_ha": 0.0},
                "reason": ["late grain-filling nitrogen is skipped in yield-first mode without a grain protein target"],
            }

        if (
            recommendation.get("apply_now")
            and min_days_since_n_application is not None
            and min_days_since_n_application <= 7
            and diagnosis["status"] != "deficient"
        ):
            recommendation = {
                "apply_now": False,
                "application_window": {
                    "start_date": str(normalized["decision_date"]),
                    "end_date": str(normalized["decision_date"] + timedelta(days=2)),
                },
                "method": "not_needed",
                "nutrient_plan": {"n_kg_ha": 0.0, "p2o5_kg_ha": 0.0, "k2o_kg_ha": 0.0},
                "product_plan": [],
                "economics": {"estimated_cost_cny_ha": 0.0},
                "reason": ["a recent N application is still in its effective uptake window, so an additional immediate topdress is not justified"],
            }

        if recommendation["apply_now"]:
            window = recommendation["application_window"]
        else:
            start = next_irrigation or (normalized["decision_date"] + timedelta(days=1))
            window = {"start_date": str(start), "end_date": str(start + timedelta(days=2))}
            recommendation["application_window"] = window

        reasons = [
            f"current crop stage is {phase['phase_name']} (BBCH {bbch})",
            f"current LAI to expected ratio is {diagnosis['lai_ratio']}",
            f"current biomass to expected ratio is {diagnosis['biomass_ratio']}",
            f"near-term crop activity factor is {round(crop_activity_factor, 2)}",
        ]
        if recommendation["reason"]:
            reasons.extend(recommendation["reason"])

        assumptions = list(normalized["assumptions"])
        assumptions.append("grain quality target not provided; yield-first default used" if not payload.get("protein_target_pct") else "protein target provided; quality hook active")
        assumptions.append("soil test sulfur missing; sulfur recommendation skipped" if normalized["soil_test"].get("available_s_mg_kg") is None else "soil sulfur test available")
        season_plan_recommendations = []
        basal_recommendation = _build_basal_recommendation(normalized)
        if basal_recommendation:
            season_plan_recommendations.append(basal_recommendation)
        season_plan_recommendations.extend(
            _build_future_topdress_plan(
                normalized=normalized,
                current_bbch=bbch,
                diagnosis=diagnosis,
                demand_snapshot=demand_snapshot,
                soil_supply=soil_supply,
                fertilizer_pool=fertilizer_pool,
                stage_rules=stage_rules,
                economics=normalized["economics"],
                irrigation_method=irrigation_method,
            )
        )
        if season_plan_recommendations:
            recommendation["season_plan_recommendations"] = season_plan_recommendations

        return {
            "decision_date": str(normalized["decision_date"]),
            "nutrition_status": {
                "n_status": diagnosis["status"],
                "p_status": "unknown",
                "k_status": "unknown",
            },
            "recommendation": recommendation,
            "reason": reasons,
            "assumptions": assumptions,
            "current_status": {
                "bbch": bbch,
                "phase": phase["phase_name"],
                "lai": round(normalized["lai"], 3),
                "aboveground_biomass_kg_ha": round(normalized["aboveground_biomass_kg_ha"], 3),
                "root_zone_relative_available_water": round(normalized["root_zone_relative_available_water"], 4),
            },
            "diagnostics": {
                "demand_snapshot": demand_snapshot,
                "soil_supply": soil_supply,
                "fertilizer_effective_pool": fertilizer_pool,
                "diagnosis": diagnosis,
                "recovery_assumptions": DEFAULT_RECOVERY_ASSUMPTIONS,
                "quality_hook": quality_hook,
            },
        }

    def _run_daily_decisions(self, payload: dict) -> dict:
        missing = _missing_canopy_simulation_fields(payload, require_decision_date=False)
        if missing:
            raise ValueError(
                "decision_date is optional for wheat nutrition only when daily canopy state can be simulated; "
                "missing required field(s): " + ", ".join(missing)
            )

        growth_stage_dates = [_item_date(item) for item in payload.get("growth_stage") or []]
        growth_stage_dates = [item for item in growth_stage_dates if item is not None]
        if not growth_stage_dates:
            raise ValueError("growth_stage must contain dated entries for daily wheat nutrition decisions")
        first_decision_date = min(growth_stage_dates)

        irrigation_body = deepcopy(payload)
        irrigation_body["decision_date"] = str(first_decision_date)
        if irrigation_body.get("weather_data") is None:
            irrigation_body["weather_data"] = irrigation_body.get("weather_history") or irrigation_body.get("weather_daily") or []
        if irrigation_body.get("forecast_weather_data") is None and irrigation_body.get("weather_forecast") is not None:
            irrigation_body["forecast_weather_data"] = irrigation_body["weather_forecast"]
        irrigation_body["_force_nutrition_coupling"] = True

        from crops.wheat.irrigation import WheatIrrigationModel

        water_response = WheatIrrigationModel(
            latitude=float(irrigation_body["latitude"]),
            longitude=float(irrigation_body["longitude"]),
            soil_type=irrigation_body["soil_type"],
            irrigation_method=irrigation_body["irrigation_method"],
        ).run(irrigation_body)

        weather_rows = (irrigation_body.get("weather_data") or []) + (irrigation_body.get("forecast_weather_data") or [])
        daily_nutrition = []
        stress_rows = []
        field_rows = []
        action_recommendations = []
        seen_actions = set()

        for state in water_response.get("daily_stress_risk") or []:
            decision = str(state.get("Date") or "")[:10]
            if not decision:
                continue
            daily_payload = deepcopy(payload)
            daily_payload["decision_date"] = decision
            daily_payload["target_yield_kg_ha"] = payload.get("target_yield_kg_ha")
            daily_payload["current_status"] = state
            daily_payload["lai"] = state.get("lai")
            daily_payload["aboveground_biomass_kg_ha"] = state.get("total_biomass_kg_ha")
            daily_payload["biomass_partition"] = {
                "leaf_biomass_kg_ha": state.get("leaf_biomass_kg_ha"),
                "stem_biomass_kg_ha": state.get("stem_biomass_kg_ha"),
                "spike_biomass_kg_ha": state.get("spike_biomass_kg_ha"),
                "grain_biomass_kg_ha": state.get("grain_biomass_kg_ha"),
            }
            daily_payload["root_zone_relative_available_water"] = state.get("root_zone_relative_available_water")
            daily_payload["layered_soil_water"] = state.get("soil_water_by_layer") or payload.get("layered_soil_water")
            daily_payload["irrigation"] = {"irrigation_method": payload.get("irrigation_method")}
            daily_payload["irrigation_recommendation"] = water_response.get("action_recommendations") or []
            daily_payload["weather_forecast"] = [
                deepcopy(item)
                for item in weather_rows
                if (item_date := _item_date(item)) is not None and item_date >= date.fromisoformat(decision)
            ]
            daily_payload["_nutrition_canopy_source"] = "wheat_growth_model"

            daily_result = self.run(daily_payload)
            status = (daily_result.get("nutrition_status") or {}).get("n_status")
            risk = _risk_from_status(status)
            daily_nutrition.append(
                {
                    "Date": decision,
                    "target_code": "N",
                    "stress_risk": risk,
                    "n_status": status,
                    "bbch": (daily_result.get("current_status") or {}).get("bbch"),
                    "lai": (daily_result.get("current_status") or {}).get("lai"),
                    "aboveground_biomass_kg_ha": (daily_result.get("current_status") or {}).get("aboveground_biomass_kg_ha"),
                    "root_zone_relative_available_water": (daily_result.get("current_status") or {}).get("root_zone_relative_available_water"),
                }
            )
            stress_rows.append({"Date": decision, "target_code": "N", "stress_risk": risk})
            field_rows.append({"Date": decision, "field_risk": risk})

            recommendation = daily_result.get("recommendation") or {}
            actions = list(recommendation.get("season_plan_recommendations") or [])
            if recommendation.get("apply_now"):
                action = {
                    "Date": decision,
                    "recommendationCode": "N_TOPDRESS",
                    "treatmentWindowCode": "IMMEDIATE",
                    "method": recommendation.get("method"),
                    "application_window": recommendation.get("application_window"),
                    "nutrient_plan": recommendation.get("nutrient_plan"),
                    "product_plan": recommendation.get("product_plan"),
                    "reason": recommendation.get("reason") or daily_result.get("reason") or [],
                }
                actions.insert(0, action)
            for action in actions:
                action_date = str(action.get("Date") or (action.get("application_window") or {}).get("start_date") or decision)[:10]
                code = action.get("recommendationCode") or action.get("method") or "NUTRITION_ACTION"
                key = (action_date, code)
                if key in seen_actions:
                    continue
                seen_actions.add(key)
                action_recommendations.append(action)

        return {
            "daily_nutrition_risk": {"N": daily_nutrition},
            "stress_risk": {"N": stress_rows},
            "field_risk": field_rows,
            "action_recommendations": action_recommendations,
        }
