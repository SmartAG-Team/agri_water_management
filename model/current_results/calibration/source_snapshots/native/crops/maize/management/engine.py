from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta

from .nutrition import MaizeNutritionManagementModel, build_fertilizer_recommendation_block, build_season_fertilization_actions
from .shared_state import build_nutrition_payload, build_yield_outlook, combine_daily_management_state
from .water import MaizeIrrigationModel
from core.nutrition.fertilizer_inventory import product_plan_nutrients
from core.nutrition.fertilizer_material import product_to_nutrients


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
    body["fertilizer_history"] = [item for item in (body.get("fertilizer_history") or []) if date.fromisoformat(str(item["date"])[:10]) <= decision_date]
    body["irrigation_history"] = [item for item in (body.get("irrigation_history") or []) if date.fromisoformat(str(item["date"])[:10]) <= decision_date]
    return body


def _build_water_request(payload: dict, nutrition_coupling: bool = False) -> dict:
    body = deepcopy(payload)
    body["management_mode"] = "water_only"
    if nutrition_coupling:
        body["_force_nutrition_coupling"] = True
    else:
        body.pop("_force_nutrition_coupling", None)
    return body


def _append_applied_irrigation(body: dict, water_response: dict, decision_date: date) -> None:
    actions = water_response.get("action_recommendations") or []
    action = next((item for item in actions if str(item.get("recommendedIrrigationDate")) == decision_date.isoformat()), None)
    if not action:
        return
    body.setdefault("irrigation_history", [])
    amount_mm = float(action.get("recommendedGrossDepthMm", 0.0) or 0.0)
    if any(str(item.get("date")) == decision_date.isoformat() and abs(float(item.get("amount_mm", 0.0) or 0.0) - amount_mm) < 1e-6 for item in body["irrigation_history"]):
        return
    body["irrigation_history"].append({"date": decision_date.isoformat(), "amount_mm": amount_mm, "method": body.get("irrigation_method"), "notes": "applied from maize management replay"})


def _append_applied_fertilizer(body: dict, nutrition_result: dict | None, decision_date: date) -> None:
    if not nutrition_result:
        return
    recommendation = nutrition_result.get("recommendation") or {}
    candidates = []
    for item in recommendation.get("season_plan_recommendations", []) or []:
        if str(item.get("Date")) == decision_date.isoformat():
            candidates.append(item)
    body.setdefault("fertilizer_history", [])
    for candidate in candidates:
        product_plan = candidate.get("product_plan") or []
        if not product_plan:
            continue
        for product in product_plan:
            amount = float(product.get("amount_kg_ha", 0.0) or 0.0)
            product_name = product.get("product_name")
            if amount <= 0 or not product_name:
                continue
            if any(str(item.get("date")) == decision_date.isoformat() and item.get("product_name") == product_name for item in body["fertilizer_history"]):
                continue
            nutrients = product_plan_nutrients(product)
            if not any(float(value or 0.0) > 0.0 for value in nutrients.values()):
                nutrients = product_to_nutrients(str(product_name), amount, body.get("custom_products") or [])
            if not nutrients:
                continue
            body["fertilizer_history"].append({
                "date": decision_date.isoformat(),
                "product_name": product_name,
                "amount_kg_ha": amount,
                "n_pct": round(float(nutrients.get("N", 0.0) or 0.0) / max(amount, 1e-6) * 100.0, 3),
                "p2o5_pct": round(float(nutrients.get("P2O5", 0.0) or 0.0) / max(amount, 1e-6) * 100.0, 3),
                "k2o_pct": round(float(nutrients.get("K2O", 0.0) or 0.0) / max(amount, 1e-6) * 100.0, 3),
                "method": candidate.get("method"),
                "nutrients_kg_ha": nutrients,
                "release_type": product.get("release_type"),
                "release_days": product.get("release_days"),
                "notes": "applied from maize management replay",
            })


def _recommended_fertilizer_history(payload: dict, nutrition_result: dict | None, from_date: date) -> list[dict]:
    if not nutrition_result:
        return []
    recommendation = nutrition_result.get("recommendation") or {}
    history = []
    for candidate in recommendation.get("season_plan_recommendations", []) or []:
        action_date = date.fromisoformat(str(candidate.get("Date"))[:10])
        if action_date < from_date:
            continue
        for product in candidate.get("product_plan") or []:
            amount = float(product.get("amount_kg_ha", 0.0) or 0.0)
            product_name = product.get("product_name")
            if amount <= 0 or not product_name:
                continue
            nutrients = product_plan_nutrients(product)
            if not any(float(value or 0.0) > 0.0 for value in nutrients.values()):
                nutrients = product_to_nutrients(str(product_name), amount, payload.get("custom_products") or [])
            if not nutrients:
                continue
            history.append({
                "date": action_date.isoformat(),
                "product_name": product_name,
                "amount_kg_ha": amount,
                "n_pct": round(float(nutrients.get("N", 0.0) or 0.0) / max(amount, 1e-6) * 100.0, 3),
                "p2o5_pct": round(float(nutrients.get("P2O5", 0.0) or 0.0) / max(amount, 1e-6) * 100.0, 3),
                "k2o_pct": round(float(nutrients.get("K2O", 0.0) or 0.0) / max(amount, 1e-6) * 100.0, 3),
                "method": candidate.get("method"),
                "nutrients_kg_ha": nutrients,
                "release_type": product.get("release_type"),
                "release_days": product.get("release_days"),
                "notes": "scheduled from maize management recommendation",
            })
    return history


class MaizeManagementModel:
    def __init__(self, latitude: float, longitude: float, soil_type: str, irrigation_method: str):
        self.water_model = MaizeIrrigationModel(latitude=latitude, longitude=longitude, soil_type=soil_type, irrigation_method=irrigation_method)
        self.nutrition_model = MaizeNutritionManagementModel()

    def _replay_histories_to_decision(self, payload: dict, management_mode: str) -> dict:
        if payload.get("_history_replayed"):
            return deepcopy(payload)
        if not payload.get("growth_stage"):
            enriched = deepcopy(payload)
            enriched["_history_replayed"] = True
            return enriched
        full_weather = _extend_weather_to_season(payload)
        rolling_request = deepcopy(payload)
        rolling_request.setdefault("irrigation_history", [])
        rolling_request.setdefault("fertilizer_history", [])
        start_date = date.fromisoformat(str(payload["planting_date"])) - timedelta(days=7)
        end_date = date.fromisoformat(str(payload["decision_date"])) - timedelta(days=1)
        current_date = start_date
        while current_date <= end_date:
            request_body = _build_projection_request(rolling_request, full_weather, current_date)
            water_response = self.water_model.run(_build_water_request(request_body, nutrition_coupling=management_mode == "water_nutrition"))
            _append_applied_irrigation(rolling_request, water_response, current_date)
            current_date += timedelta(days=1)
        rolling_request["_history_replayed"] = True
        return rolling_request

    def run(self, payload: dict) -> dict:
        management_mode = payload.get("management_mode", "water_only")
        payload = self._replay_histories_to_decision(payload, management_mode)
        decision_date = date.fromisoformat(str(payload.get("decision_date")))
        water_response = self.water_model.run(_build_water_request(payload, nutrition_coupling=management_mode == "water_nutrition"))
        water_ceiling_response = None
        nutrition_result = None
        if management_mode == "water_nutrition":
            nutrition_payload = build_nutrition_payload(payload, water_response)
            nutrition_result = self.nutrition_model.run(nutrition_payload)
            water_ceiling_response = self.water_model.run(_build_water_request(payload, nutrition_coupling=False))
        no_action_water_source = water_ceiling_response or water_response
        no_action_water_daily = no_action_water_source.get("no_action_daily_stress_risk") or no_action_water_source["daily_stress_risk"]
        no_action_daily_management_state = combine_daily_management_state(management_mode, no_action_water_daily, nutrition_result)
        after_action_water_response = water_response
        recommended_fertilizer_history = _recommended_fertilizer_history(payload, nutrition_result, decision_date)
        if management_mode == "water_nutrition" and recommended_fertilizer_history:
            after_action_payload = deepcopy(payload)
            after_action_payload.setdefault("fertilizer_history", [])
            existing = {
                (str(item.get("date") or item.get("Date"))[:10], item.get("product_name"))
                for item in after_action_payload["fertilizer_history"]
            }
            for item in recommended_fertilizer_history:
                key = (str(item.get("date"))[:10], item.get("product_name"))
                if key not in existing:
                    after_action_payload["fertilizer_history"].append(item)
                    existing.add(key)
            after_action_water_response = self.water_model.run(_build_water_request(after_action_payload, nutrition_coupling=True))
        daily_management_state = combine_daily_management_state(management_mode, after_action_water_response["daily_stress_risk"], nutrition_result)
        yield_outlook = build_yield_outlook(management_mode, water_response, nutrition_result, daily_management_state=no_action_daily_management_state)
        if management_mode == "water_nutrition":
            water_ceiling_daily = (water_ceiling_response or water_response).get("daily_stress_risk") or []
            water_ceiling = float(water_ceiling_daily[-1].get("grain_weight_kg_ha", 0.0) or 0.0) if water_ceiling_daily else 0.0
            current_yield = float(no_action_daily_management_state[-1].get("grain_weight_kg_ha", 0.0) or 0.0) if no_action_daily_management_state else 0.0
            expected_yield = float(daily_management_state[-1].get("grain_weight_kg_ha", 0.0) or 0.0) if daily_management_state else current_yield
            expected_yield = max(current_yield, expected_yield)
            yield_outlook.update({
                "water_limited_yield_kg_ha": round(water_ceiling, 3),
                "water_nutrition_limited_yield_kg_ha": round(current_yield, 3),
                "expected_yield_with_recommendation_kg_ha": round(expected_yield, 3),
                "expected_yield_recovery_kg_ha": round(expected_yield - current_yield, 3),
                "expected_yield_recovery_pct": round(0.0 if current_yield <= 0 else (expected_yield - current_yield) / current_yield * 100.0, 3),
            })
        if nutrition_result:
            nutrition_status = nutrition_result["nutrition_status"]
            fertilizer_recommendation = build_fertilizer_recommendation_block(payload, nutrition_result["recommendation"])
            nutrition_assumptions = nutrition_result.get("assumptions", [])
            nutrition_explanation = nutrition_result.get("reason", [])
        else:
            nutrition_status = {"status": "disabled", "n_status": "disabled", "p_status": "disabled", "k_status": "disabled"}
            fertilizer_recommendation = {"disabled": True, "apply_now": False, "application_window": None, "method": "disabled", "nutrient_plan": {"n_kg_ha": 0.0, "p2o5_kg_ha": 0.0, "k2o_kg_ha": 0.0}, "product_plan": [], "season_plan": []}
            nutrition_assumptions = ["management_mode is water_only, so fertilization recommendation is disabled"]
            nutrition_explanation = ["management_mode is water_only, so fertilization recommendation is disabled"]
        fertilizer_actions = build_season_fertilization_actions(payload, nutrition_result["recommendation"]) if nutrition_result else []
        action_recommendations = list(after_action_water_response.get("action_recommendations") or []) + fertilizer_actions
        action_recommendations.sort(key=lambda item: (str(item.get("Date") or item.get("recommendedIrrigationDate") or ""), str(item.get("actionTypeCode") or item.get("action_domain") or "")))
        decision_date_str = str(payload.get("decision_date"))
        current_status = next((item for item in no_action_daily_management_state if str(item.get("Date")) == decision_date_str), no_action_daily_management_state[-1] if no_action_daily_management_state else water_response["current_status"])
        after_recommendation_current_status = next((item for item in daily_management_state if str(item.get("Date")) == decision_date_str), daily_management_state[-1] if daily_management_state else after_action_water_response["current_status"])
        return {
            "management_mode": management_mode,
            "current_status": current_status,
            "after_recommendation_current_status": after_recommendation_current_status,
            "yield_outlook": yield_outlook,
            "water_status": water_response["current_status"],
            "nutrition_status": nutrition_status,
            "irrigation_recommendation": {"disabled": False, "actions": after_action_water_response.get("action_recommendations") or []},
            "fertilizer_recommendation": fertilizer_recommendation,
            "action_recommendations": action_recommendations,
            "no_action_daily_management_state": no_action_daily_management_state,
            "daily_management_state": daily_management_state,
            "assumptions": list(water_response.get("defaults_used") or []) + list(nutrition_assumptions),
            "explanation": {"water": water_response.get("explanation"), "nutrition": nutrition_explanation},
        }
