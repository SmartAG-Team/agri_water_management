from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta

from crops.wheat.nutrition.wheat_nutrition import WheatNutritionModel


def _nutrient_rate_from_history(item: dict, nutrient_key: str, default_pct: float = 0.0) -> float:
    pct_key = {"N": "n_pct", "P2O5": "p2o5_pct", "K2O": "k2o_pct"}.get(nutrient_key, "")
    pct = float(item.get(pct_key, default_pct) or 0.0)
    amount = float(item.get("amount_kg_ha", 0.0) or 0.0)
    return round(amount * pct / 100.0, 3)


def _bounded_window(start_date: str | None, end_date: str | None, default_days: int = 3) -> tuple[str | None, str | None]:
    if not start_date:
        return None, None
    start = date.fromisoformat(str(start_date)[:10])
    raw_end = date.fromisoformat(str(end_date)[:10]) if end_date else start + timedelta(days=max(0, default_days - 1))
    if raw_end < start:
        raw_end = start
    max_end = start + timedelta(days=2)
    return str(start), str(min(raw_end, max_end))


def _window_from_item(action_date: str, item: dict | None = None, recommendation: dict | None = None, default_days: int = 3) -> tuple[str, str]:
    window = (item or {}).get("application_window") or (recommendation or {}).get("application_window") or {}
    start_date = str(window.get("start_date") or action_date)
    end_date = window.get("end_date")
    if end_date is None:
        window_days = int((item or {}).get("application_window_days") or default_days)
        end_date = str(date.fromisoformat(start_date[:10]) + timedelta(days=max(0, min(window_days, 3) - 1)))
    start_date, end_date = _bounded_window(start_date, str(end_date), default_days=default_days)
    return start_date or action_date, end_date or action_date


def build_fertilization_action(
    action_date: str,
    nutrient_plan: dict,
    product_plan: list[dict],
    method: str,
    recommendation_code: str,
    treatment_window_code: str,
    reason: str,
    treatment_start_date: str | None = None,
    treatment_end_date: str | None = None,
    completed: bool = False,
) -> dict:
    return {
        "Date": action_date,
        "recommendationCode": recommendation_code,
        "actionTypeCode": "FERTILIZATION",
        "action_domain": "FERTILIZATION",
        "treatmentWindowCode": treatment_window_code,
        "treatmentStartDate": treatment_start_date or action_date,
        "treatmentEndDate": treatment_end_date or action_date,
        "method": method,
        "nutrient_plan": deepcopy(nutrient_plan),
        "product_plan": deepcopy(product_plan),
        "reason": reason,
        "completed": completed,
    }


def build_season_fertilization_actions(payload: dict, recommendation: dict | None) -> list[dict]:
    planned_actions: list[dict] = []
    historical_signatures = {
        (
            str(item.get("date")),
            str(item.get("product_name", "custom_product")),
            round(float(item.get("amount_kg_ha", 0.0) or 0.0), 3),
        )
        for item in (payload.get("fertilizer_history") or [])
    }
    for item in recommendation.get("season_plan_recommendations", []) if recommendation else []:
        action_date = str(item.get("Date"))
        nutrient_plan = item.get("nutrient_plan") or {}
        total_npk_rate = sum(
            float(nutrient_plan.get(key, 0.0) or 0.0)
            for key in ("n_kg_ha", "p2o5_kg_ha", "k2o_kg_ha")
        )
        if not action_date:
            continue
        if total_npk_rate <= 0:
            continue
        product_plan = item.get("product_plan", [])
        if product_plan:
            product = product_plan[0]
            signature = (
                action_date,
                str(product.get("product_name", "custom_product")),
                round(float(product.get("amount_kg_ha", 0.0) or 0.0), 3),
            )
            if signature in historical_signatures:
                continue
        treatment_start_date, treatment_end_date = _window_from_item(action_date, item=item, default_days=int(item.get("application_window_days", 3) or 3))
        planned_actions.append(
            build_fertilization_action(
                action_date=action_date,
                nutrient_plan=item.get("nutrient_plan", {"n_kg_ha": 0.0, "p2o5_kg_ha": 0.0, "k2o_kg_ha": 0.0}),
                product_plan=product_plan,
                method=str(item.get("method", "soil")),
                recommendation_code=str(item.get("recommendationCode", "PLAN")),
                treatment_window_code=str(item.get("treatmentWindowCode", "BASELINE")),
                reason="; ".join(item.get("reason", [])) if isinstance(item.get("reason"), list) else str(item.get("reason", "season plan recommendation")),
                treatment_start_date=treatment_start_date,
                treatment_end_date=treatment_end_date,
                completed=False,
            )
        )

    history_actions: list[dict] = []
    for item in sorted(payload.get("fertilizer_history") or [], key=lambda row: row.get("date", "")):
        action_date = str(item.get("date"))
        nutrient_plan = {
            "n_kg_ha": _nutrient_rate_from_history(item, "N"),
            "p2o5_kg_ha": _nutrient_rate_from_history(item, "P2O5"),
            "k2o_kg_ha": _nutrient_rate_from_history(item, "K2O"),
        }
        product_name = item.get("product_name", "custom_product")
        product_amount = round(float(item.get("amount_kg_ha", 0.0) or 0.0), 3)
        history_actions.append(
            build_fertilization_action(
                action_date=action_date,
                nutrient_plan=nutrient_plan,
                product_plan=[{"product_name": product_name, "amount_kg_ha": product_amount}],
                method=str(item.get("method", "soil")),
                recommendation_code="APPLIED",
                treatment_window_code="COMPLETED",
                reason=str(item.get("notes", "historical fertilizer application")),
                completed=True,
            )
        )

    future_actions: list[dict] = []
    if recommendation and not recommendation.get("disabled"):
        window = recommendation.get("application_window") or {}
        start_date = str(window.get("start_date"))
        current_npk_rate = sum(
            float((recommendation.get("nutrient_plan") or {}).get(key, 0.0) or 0.0)
            for key in ("n_kg_ha", "p2o5_kg_ha", "k2o_kg_ha")
        )
        if start_date and current_npk_rate > 0:
            treatment_start_date, treatment_end_date = _window_from_item(start_date, recommendation=recommendation, default_days=3)
            future_actions.append(
                build_fertilization_action(
                    action_date=start_date,
                    nutrient_plan=recommendation.get("nutrient_plan", {"n_kg_ha": 0.0, "p2o5_kg_ha": 0.0, "k2o_kg_ha": 0.0}),
                    product_plan=recommendation.get("product_plan", []),
                    method=str(recommendation.get("method", "topdress_before_irrigation")),
                    recommendation_code="APPLY" if recommendation.get("apply_now") else "PLAN",
                    treatment_window_code="CURRENT" if recommendation.get("apply_now") else "FUTURE",
                    reason="; ".join(recommendation.get("reason", [])),
                    treatment_start_date=treatment_start_date,
                    treatment_end_date=treatment_end_date,
                    completed=False,
                )
            )

    actions = planned_actions + history_actions + future_actions
    actions.sort(key=lambda item: (item.get("Date", ""), 0 if item.get("completed") else 1))
    deduped = []
    seen = set()
    for item in actions:
        product_plan = item.get("product_plan") or []
        if product_plan:
            product = product_plan[0]
            signature = (
                str(item.get("Date")),
                str(product.get("product_name", "custom_product")),
                round(float(product.get("amount_kg_ha", 0.0) or 0.0), 3),
            )
        else:
            signature = (
                str(item.get("Date")),
                str(item.get("recommendationCode")),
                round(float((item.get("nutrient_plan") or {}).get("n_kg_ha", 0.0) or 0.0), 3),
            )
        if signature in seen:
            continue
        seen.add(signature)
        deduped.append(item)
    return deduped


def build_fertilizer_recommendation_block(payload: dict, recommendation: dict | None) -> dict:
    if not recommendation or recommendation.get("disabled"):
        return {
            "disabled": True,
            "apply_now": False,
            "application_window": None,
            "method": "disabled",
            "nutrient_plan": {"n_kg_ha": 0.0, "p2o5_kg_ha": 0.0, "k2o_kg_ha": 0.0},
            "product_plan": [],
            "season_plan": [],
        }

    block = deepcopy(recommendation)
    block["season_plan"] = build_season_fertilization_actions(payload, recommendation)
    return block


__all__ = [
    "WheatNutritionModel",
    "build_fertilizer_recommendation_block",
    "build_season_fertilization_actions",
]
