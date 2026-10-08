from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta

from .nutrition import WheatNutritionModel, build_fertilizer_recommendation_block, build_season_fertilization_actions
from .shared_state import build_nutrition_payload, build_yield_outlook, combine_daily_management_state, season_plan_n_factors
from .water import WheatIrrigationModel


def _weather_item_date(item: dict) -> date:
    if item.get("Date"):
        return date.fromisoformat(str(item["Date"])[:10])
    return date.fromisoformat(str(item["DateTime"])[:10])


def _extend_weather_to_season(payload: dict) -> list[dict]:
    merged = []
    seen = set()
    for item in (payload.get("weather_data") or []) + (payload.get("forecast_weather_data") or []):
        key = _weather_item_date(item)
        if key in seen:
            continue
        merged.append(deepcopy(item))
        seen.add(key)
    return merged


def _build_projection_request(base_request: dict, full_weather: list[dict], decision_date: date) -> dict:
    body = deepcopy(base_request)
    body["decision_date"] = decision_date.isoformat()
    body["weather_data"] = deepcopy(full_weather)
    body.pop("forecast_weather_data", None)
    body["fertilizer_history"] = [
        item for item in (body.get("fertilizer_history") or []) if date.fromisoformat(str(item["date"])[:10]) <= decision_date
    ]
    body["irrigation_history"] = [
        item for item in (body.get("irrigation_history") or []) if date.fromisoformat(str(item["date"])[:10]) <= decision_date
    ]
    return body


def _build_water_request(payload: dict) -> dict:
    body = deepcopy(payload)
    management_mode = payload.get("management_mode", "water_only")
    body["management_mode"] = management_mode
    if management_mode == "water_nutrition":
        body["_force_nutrition_coupling"] = True
    else:
        body.pop("_force_nutrition_coupling", None)
    return body


def _append_applied_irrigation(body: dict, water_response: dict, decision_date: date) -> None:
    actions = water_response.get("action_recommendations") or []
    action = next((item for item in actions if str(item.get("recommendedIrrigationDate")) == decision_date.isoformat()), None)
    if not action:
        return
    if body.get("irrigation_history") is None:
        body["irrigation_history"] = []
    amount_mm = float(action.get("recommendedGrossDepthMm", 0.0) or 0.0)
    history = body["irrigation_history"]
    if any(
        str(item.get("date")) == decision_date.isoformat()
        and abs(float(item.get("amount_mm", 0.0) or 0.0) - amount_mm) < 1e-6
        and str(item.get("method") or body.get("irrigation_method")) == str(body.get("irrigation_method"))
        for item in history
    ):
        return
    history.append(
        {
            "date": decision_date.isoformat(),
            "amount_mm": amount_mm,
            "method": body.get("irrigation_method"),
            "notes": "applied from management yield projection",
        }
    )


def _append_applied_fertilizer(body: dict, nutrition_result: dict | None, decision_date: date) -> None:
    if not nutrition_result:
        return
    recommendation = nutrition_result.get("recommendation") or {}
    candidates = []
    if recommendation.get("apply_now"):
        candidates.append(
            {
                "method": recommendation.get("method"),
                "nutrient_plan": recommendation.get("nutrient_plan"),
                "product_plan": recommendation.get("product_plan"),
            }
        )
    for item in recommendation.get("season_plan_recommendations", []) or []:
        if str(item.get("Date")) == decision_date.isoformat():
            candidates.append(item)

    if body.get("fertilizer_history") is None:
        body["fertilizer_history"] = []
    history = body["fertilizer_history"]
    for candidate in candidates:
        product_plan = candidate.get("product_plan") or []
        if not product_plan:
            continue
        product = product_plan[0]
        amount = float(product.get("amount_kg_ha", 0.0) or 0.0)
        n_amount = float((candidate.get("nutrient_plan") or {}).get("n_kg_ha", 0.0) or 0.0)
        if amount <= 0 or n_amount <= 0:
            continue
        if any(str(item.get("date")) == decision_date.isoformat() and item.get("product_name") == product.get("product_name") for item in history):
            continue
        history.append(
            {
                "date": decision_date.isoformat(),
                "product_name": product.get("product_name"),
                "amount_kg_ha": amount,
                "n_pct": round(n_amount / max(amount, 1e-6) * 100.0, 3),
                "p2o5_pct": 0.0,
                "k2o_pct": 0.0,
                "method": candidate.get("method"),
                "notes": "applied from management yield projection",
            }
        )


class WheatManagementModel:
    def __init__(self, latitude: float, longitude: float, soil_type: str, irrigation_method: str):
        self.water_model = WheatIrrigationModel(
            latitude=latitude,
            longitude=longitude,
            soil_type=soil_type,
            irrigation_method=irrigation_method,
        )
        self.nutrition_model = WheatNutritionModel()

    def _project_managed_yield(self, payload: dict, management_mode: str, apply_fertilizer: bool) -> float | None:
        if not payload.get("growth_stage"):
            return None

        full_weather = _extend_weather_to_season(payload)
        if not full_weather:
            return None

        rolling_request = deepcopy(payload)
        rolling_request["management_mode"] = management_mode
        rolling_request.setdefault("irrigation_history", [])

        current_date = date.fromisoformat(str(payload["decision_date"]))
        final_date = date.fromisoformat(str(payload["growth_stage"][-1]["Date"]))
        final_yield = None

        while current_date <= final_date:
            request_body = _build_projection_request(rolling_request, full_weather, current_date)
            water_response = self.water_model.run(_build_water_request(request_body))
            nutrition_result = None
            if management_mode == "water_nutrition":
                nutrition_payload = build_nutrition_payload(request_body, water_response)
                nutrition_result = self.nutrition_model.run(nutrition_payload)
            daily_state = combine_daily_management_state(management_mode, water_response["daily_stress_risk"], nutrition_result)
            if daily_state:
                final_yield = float(daily_state[-1].get("grain_weight_kg_ha", 0.0) or 0.0)
            _append_applied_irrigation(rolling_request, water_response, current_date)
            if apply_fertilizer:
                _append_applied_fertilizer(rolling_request, nutrition_result, current_date)
            current_date += timedelta(days=1)

        return round(final_yield, 3) if final_yield is not None else None

    def _replay_histories_to_decision(self, payload: dict, management_mode: str) -> dict:
        if payload.get("_history_replayed"):
            return deepcopy(payload)
        if not payload.get("growth_stage"):
            enriched = deepcopy(payload)
            enriched["_history_replayed"] = True
            return enriched

        full_weather = _extend_weather_to_season(payload)
        if not full_weather:
            enriched = deepcopy(payload)
            enriched["_history_replayed"] = True
            return enriched

        rolling_request = deepcopy(payload)
        rolling_request.setdefault("irrigation_history", [])
        rolling_request.setdefault("fertilizer_history", [])
        start_date = date.fromisoformat(str(payload["planting_date"])) - timedelta(days=7)
        end_date = date.fromisoformat(str(payload["decision_date"])) - timedelta(days=1)
        current_date = start_date
        while current_date <= end_date:
            request_body = _build_projection_request(rolling_request, full_weather, current_date)
            request_body["_skip_forward_projection"] = True
            water_response = self.water_model.run(_build_water_request(request_body))
            nutrition_result = None
            if management_mode == "water_nutrition":
                nutrition_payload = build_nutrition_payload(request_body, water_response)
                nutrition_result = self.nutrition_model.run(nutrition_payload)
            _append_applied_irrigation(rolling_request, water_response, current_date)
            _append_applied_fertilizer(rolling_request, nutrition_result, current_date)
            current_date += timedelta(days=1)

        rolling_request["_history_replayed"] = True
        return rolling_request

    def run(self, payload: dict) -> dict:
        management_mode = payload.get("management_mode", "water_only")
        payload = self._replay_histories_to_decision(payload, management_mode)
        skip_forward_projection = bool(payload.get("_skip_forward_projection"))
        water_response = self.water_model.run(_build_water_request(payload))

        nutrition_result = None
        if management_mode == "water_nutrition":
            nutrition_payload = build_nutrition_payload(payload, water_response)
            nutrition_result = self.nutrition_model.run(nutrition_payload)

        daily_management_state = combine_daily_management_state(
            management_mode=management_mode,
            water_daily_records=water_response["daily_stress_risk"],
            nutrition_result=nutrition_result,
        )
        yield_outlook = build_yield_outlook(
            management_mode,
            water_response,
            nutrition_result,
            daily_management_state=daily_management_state,
        )
        if management_mode == "water_nutrition" and nutrition_result:
            current_n_factor = float(daily_management_state[-1].get("n_stress_factor", 1.0) if daily_management_state else 1.0)
            limited_factor, recovered_factor = season_plan_n_factors(nutrition_result, current_n_factor)
            water_ceiling = float(yield_outlook.get("water_limited_yield_kg_ha") or 0.0)
            current_yield = float(yield_outlook.get("water_nutrition_limited_yield_kg_ha") or 0.0)
            if water_ceiling > 0 and current_yield > 0:
                limited_yield = min(current_yield, round(water_ceiling * limited_factor, 3))
                recovered_yield = min(water_ceiling, round(water_ceiling * recovered_factor, 3))
                recovered_yield = max(limited_yield, recovered_yield)
                yield_outlook["water_nutrition_limited_yield_kg_ha"] = round(limited_yield, 3)
                yield_outlook["expected_yield_with_recommendation_kg_ha"] = round(recovered_yield, 3)
                yield_outlook["expected_yield_recovery_kg_ha"] = round(recovered_yield - limited_yield, 3)
                yield_outlook["expected_yield_recovery_pct"] = round(
                    0.0 if limited_yield <= 0 else (recovered_yield - limited_yield) / limited_yield * 100.0,
                    3,
                )

        if nutrition_result:
            nutrition_status = nutrition_result["nutrition_status"]
            fertilizer_recommendation = build_fertilizer_recommendation_block(payload, nutrition_result["recommendation"])
            nutrition_assumptions = nutrition_result.get("assumptions", [])
            nutrition_explanation = nutrition_result.get("reason", [])
        else:
            nutrition_status = {"status": "disabled", "n_status": "disabled", "p_status": "disabled", "k_status": "disabled"}
            fertilizer_recommendation = {
                "disabled": True,
                "apply_now": False,
                "application_window": None,
                "method": "disabled",
                "nutrient_plan": {"n_kg_ha": 0.0, "p2o5_kg_ha": 0.0, "k2o_kg_ha": 0.0},
                "product_plan": [],
                "season_plan": [],
            }
            nutrition_assumptions = ["nutrition module disabled in water_only mode"]
            nutrition_explanation = ["management_mode is water_only, so fertilization recommendation is disabled"]

        first_irrigation = next(
            (item for item in water_response["action_recommendations"] if item.get("recommendationCode") == "IRRIGATE"),
            None,
        )
        decision_date = str(payload.get("decision_date"))
        irrigation_recommendation = {
            "apply_now": bool(first_irrigation and str(first_irrigation.get("recommendedIrrigationDate")) == decision_date),
            "recommendations": water_response["action_recommendations"],
        }
        water_status = {
            "current_stress": water_response["current_status"]["stress_risk"],
            "root_zone_relative_available_water": water_response["current_status"]["root_zone_relative_available_water"],
            "field_status": water_response["field_status"],
        }

        action_recommendations = []
        for item in water_response["action_recommendations"]:
            item2 = deepcopy(item)
            item2["action_domain"] = "IRRIGATION"
            action_recommendations.append(item2)
        if not fertilizer_recommendation.get("disabled"):
            action_recommendations.extend(build_season_fertilization_actions(payload, nutrition_result["recommendation"]))
        action_recommendations.sort(key=lambda item: (item.get("Date", ""), item.get("action_domain", ""), item.get("treatmentWindowCode", "")))

        assumptions = []
        for item in (water_response.get("defaults_used") or []) + nutrition_assumptions:
            if item not in assumptions:
                assumptions.append(item)
        return {
            "management_mode": management_mode,
            "current_status": water_response["current_status"],
            "yield_outlook": yield_outlook,
            "water_status": water_status,
            "nutrition_status": nutrition_status,
            "irrigation_recommendation": irrigation_recommendation,
            "fertilizer_recommendation": fertilizer_recommendation,
            "action_recommendations": action_recommendations,
            "daily_management_state": daily_management_state,
            "assumptions": assumptions,
            "explanation": {
                "water": water_response.get("explanation"),
                "nutrition": nutrition_explanation,
            },
        }
