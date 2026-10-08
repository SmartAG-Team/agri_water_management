from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from typing import Any

import pandas as pd

from crops.cotton.config import COTTON_NUTRIENT_COEFFICIENTS

STRESS_SEVERITY = {"LOW": 1, "FERTILIZED": 1, "MEDIUM": 2, "HIGH": 3}
SEVERITY_LABEL = {1: "LOW", 2: "MEDIUM", 3: "HIGH"}


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    return pd.to_datetime(value).date()


def _stage_name(value: Any, bbch: Any = None) -> str:
    if isinstance(value, dict):
        bbch = value.get("BBCH", value.get("bbch", value.get("StageCode", bbch)))
        value = value.get("Stage") or value.get("StageName")
    if bbch is not None:
        code = int(float(bbch))
        if code >= 89:
            return "maturity"
        if code >= 81:
            return "boll_opening"
        if code >= 75:
            return "boll_setting"
        if code >= 61:
            return "flowering"
        if code >= 51:
            return "squaring"
        if code >= 13:
            return "seedling"
        if code >= 9:
            return "emergence"
        return "sowing"
    text = str(value or "").strip().lower()
    zh_map = {
        "播种期": "sowing",
        "出苗期": "emergence",
        "苗期": "seedling",
        "旺长期": "seedling",
        "现蕾期": "squaring",
        "初花期": "flowering",
        "盛花期": "flowering",
        "盛铃期": "boll_setting",
        "初吐絮期": "boll_opening",
        "吐絮期": "boll_opening",
        "完熟期": "maturity",
    }
    if text in zh_map:
        return zh_map[text]
    if text.isdigit():
        code = int(text)
        if code >= 89:
            return "maturity"
        if code >= 81:
            return "boll_opening"
        if code >= 75:
            return "boll_setting"
        if code >= 61:
            return "flowering"
        if code >= 51:
            return "squaring"
        if code >= 13:
            return "seedling"
        if code >= 9:
            return "emergence"
        return "sowing"
    return text


def _stage_rank(stage: str) -> int:
    return {
        "sowing": 0,
        "emergence": 9,
        "seedling": 13,
        "squaring": 51,
        "flowering": 61,
        "boll_setting": 75,
        "boll_opening": 81,
        "maturity": 89,
    }.get(stage, 51)


def _stage_rank_from_item(item: dict) -> int:
    if item.get("BBCH") is not None:
        return int(float(item["BBCH"]))
    return _stage_rank(_stage_name(item))


def _current_stage(growth_stage: list[dict], decision_date: date) -> tuple[str, int]:
    if not growth_stage:
        return "squaring", 51
    rows = sorted(growth_stage, key=lambda x: str(x.get("Date")))
    chosen = rows[0]
    for item in rows:
        if _as_date(item.get("Date")) <= decision_date:
            chosen = item
        else:
            break
    stage = _stage_name(chosen)
    return stage, _stage_rank_from_item(chosen)


def _stage_date(growth_stage: list[dict], stage_rank: int, fallback: date) -> date:
    for item in sorted(growth_stage, key=lambda x: str(x.get("Date"))):
        if _stage_rank_from_item(item) >= stage_rank:
            return _as_date(item.get("Date")) or fallback
    return fallback


def _nutrients_from_history(items: list[dict]) -> dict[str, float]:
    totals = {"N": 0.0, "P2O5": 0.0, "K2O": 0.0}
    for item in items or []:
        nutrients = item.get("nutrients_kg_ha") or {}
        if nutrients:
            for key in totals:
                totals[key] += float(nutrients.get(key, nutrients.get(key.lower(), 0.0)) or 0.0)
            continue
        amount = float(item.get("amount_kg_ha", 0.0) or 0.0)
        totals["N"] += amount * float(item.get("n_pct", 0.0) or 0.0) / 100.0
        totals["P2O5"] += amount * float(item.get("p2o5_pct", 0.0) or 0.0) / 100.0
        totals["K2O"] += amount * float(item.get("k2o_pct", 0.0) or 0.0) / 100.0
    return {k: round(v, 3) for k, v in totals.items()}


def _soil_supply(soil_test: dict) -> dict[str, float]:
    mineral_n = soil_test.get("mineral_n_kg_ha")
    alkali_n = soil_test.get("alkali_hydrolyzable_n_mg_kg")
    if mineral_n is None and alkali_n is None:
        raise ValueError("soil_test.mineral_n_kg_ha or soil_test.alkali_hydrolyzable_n_mg_kg is required")
    if soil_test.get("organic_matter_g_kg") is None:
        raise ValueError("soil_test.organic_matter_g_kg is required")
    if soil_test.get("olsen_p_mg_kg") is None:
        raise ValueError("soil_test.olsen_p_mg_kg is required")
    available_k = soil_test.get("available_k_mg_kg", soil_test.get("exchangeable_k_mg_kg"))
    if available_k is None:
        raise ValueError("soil_test.available_k_mg_kg or soil_test.exchangeable_k_mg_kg is required")
    mineral_n = 0.0 if mineral_n is None else float(mineral_n)
    alkali_n = 0.0 if alkali_n is None else float(alkali_n)
    organic_matter = float(soil_test["organic_matter_g_kg"])
    olsen_p = float(soil_test["olsen_p_mg_kg"])
    available_k = float(available_k)
    n_supply = min(160.0, mineral_n * 0.65 + alkali_n * 0.10 + organic_matter * 2.4)
    p_supply = min(100.0, olsen_p * 4.8)
    k_supply = min(220.0, available_k * 1.15)
    return {"N": round(n_supply, 3), "P2O5": round(p_supply, 3), "K2O": round(k_supply, 3)}


def _status_from_gap(gap: float, demand: float) -> str:
    ratio = gap / max(demand, 1.0)
    if ratio > 0.22:
        return "deficient"
    if ratio > 0.08:
        return "watch"
    return "adequate"


def _risk_from_gap(gap: float, demand: float) -> str:
    return {"deficient": "HIGH", "watch": "MEDIUM", "adequate": "LOW"}[_status_from_gap(gap, demand)]


def _cap_risk(level: str, cap: str) -> str:
    capped = min(STRESS_SEVERITY[str(level).upper()], STRESS_SEVERITY[str(cap).upper()])
    return SEVERITY_LABEL[capped]


def _stage_risk_cap(stage_rank: int) -> str:
    if stage_rank < 51:
        return "LOW"
    if stage_rank >= 89:
        return "LOW"
    if stage_rank >= 81:
        return "MEDIUM"
    return "HIGH"


def _stage_constrained_risk(level: str, stage_rank: int) -> str:
    return _cap_risk(level, _stage_risk_cap(stage_rank))


def _aggregate_field_risk(levels: list[str]) -> str:
    if not levels:
        return "LOW"
    max_value = max(STRESS_SEVERITY.get(str(level).upper(), 1) for level in levels)
    return SEVERITY_LABEL[max_value]


def _first_high_risk_date(field_risk: list[dict]) -> date | None:
    for item in field_risk:
        if str(item.get("field_risk")).upper() == "HIGH":
            return _as_date(item.get("Date"))
    return None


def _demand_fraction(stage_rank: int) -> float:
    if stage_rank < 9:
        return 0.02
    if stage_rank < 13:
        return 0.04
    if stage_rank < 51:
        return 0.16
    if stage_rank < 61:
        return 0.36
    if stage_rank < 75:
        return 0.68
    if stage_rank < 81:
        return 0.88
    return 1.0


def _nutrient_application_by_day(items: list[dict], nutrient: str, day: date) -> float:
    total = 0.0
    pct_key = {"N": "n_pct", "P2O5": "p2o5_pct", "K2O": "k2o_pct"}[nutrient]
    for item in items or []:
        applied_day = _as_date(item.get("date") or item.get("Date"))
        if applied_day is None or applied_day > day:
            continue
        nutrients = item.get("nutrients_kg_ha") or {}
        if nutrients:
            total += float(nutrients.get(nutrient, nutrients.get(nutrient.lower(), 0.0)) or 0.0)
            continue
        amount = float(item.get("amount_kg_ha", 0.0) or 0.0)
        total += amount * float(item.get(pct_key, 0.0) or 0.0) / 100.0
    return round(total, 3)


def _has_fertilizer_effect(items: list[dict], nutrient: str, day: date) -> bool:
    return _nutrient_application_by_day(items, nutrient, day) > 0.0


def _daily_dates(growth_stage: list[dict], weather_rows: list[dict]) -> list[date]:
    dates = {
        day
        for day in (_as_date(item.get("Date")) for item in growth_stage or [])
        if day is not None
    }
    if not dates:
        dates = {
            day
            for day in (_as_date(item.get("DateTime") or item.get("Date")) for item in weather_rows or [])
            if day is not None
        }
    return sorted(dates)


def _build_daily_nutrition_profiles(
    *,
    growth_stage: list[dict],
    weather_rows: list[dict],
    demand: dict[str, float],
    soil_supply: dict[str, float],
    fertilizer_history: list[dict],
) -> tuple[dict[str, list[dict]], dict[str, list[dict]], list[dict]]:
    dates = _daily_dates(growth_stage, weather_rows)
    daily_by_target: dict[str, list[dict]] = {key: [] for key in demand}
    stress_by_target: dict[str, list[dict]] = {key: [] for key in demand}
    by_date: dict[str, list[str]] = {}

    for day in dates:
        stage, rank = _current_stage(growth_stage, day)
        fraction = _demand_fraction(rank)
        for nutrient in demand:
            demand_to_date = round(float(demand[nutrient]) * fraction, 3)
            soil_available = round(float(soil_supply.get(nutrient, 0.0) or 0.0), 3)
            fertilizer_release = _nutrient_application_by_day(fertilizer_history, nutrient, day)
            fertilizer_effect = _has_fertilizer_effect(fertilizer_history, nutrient, day)
            available = round(soil_available + fertilizer_release, 3)
            gap = round(max(0.0, demand_to_date - available), 3)
            stress_index = round(gap / max(demand_to_date, 1.0), 4)
            daily_risk = _stage_constrained_risk(_risk_from_gap(gap, demand_to_date), rank)

            recent = daily_by_target[nutrient][-4:] + [{"stress_index": stress_index}]
            shortterm_index = round(
                sum(float(item["stress_index"]) for item in recent) / max(len(recent), 1),
                4,
            )
            shortterm_gap = shortterm_index * max(demand_to_date, 1.0)
            shortterm_risk = _stage_constrained_risk(_risk_from_gap(shortterm_gap, demand_to_date), rank)
            public_daily_risk = "FERTILIZED" if fertilizer_effect else daily_risk
            public_shortterm_risk = "FERTILIZED" if fertilizer_effect else shortterm_risk
            date_key = day.isoformat()
            daily_by_target[nutrient].append(
                {
                    "Date": date_key,
                    "target_code": nutrient,
                    "unit": "kg_ha",
                    "gstage": stage,
                    "demand": demand_to_date,
                    "soil_supply": soil_available,
                    "fertilizer_release": fertilizer_release,
                    "fertilizer_effects": fertilizer_effect,
                    "available": available,
                    "stress_index": stress_index,
                    "raw_categorized_daily_stress": daily_risk,
                    "categorized_daily_stress": public_daily_risk,
                    "shortterm_aggregate_stress": shortterm_index,
                    "raw_categorized_shortterm_aggregate_stress": shortterm_risk,
                    "categorized_shortterm_aggregate_stress": public_shortterm_risk,
                }
            )
            stress_by_target[nutrient].append(
                {
                    "Date": date_key,
                    "target_code": nutrient,
                    "stress_risk": public_shortterm_risk,
                    "raw_stress_risk": shortterm_risk,
                }
            )
            by_date.setdefault(date_key, []).append(public_shortterm_risk)

    field_risk = [
        {"Date": day, "field_risk": _aggregate_field_risk(levels)}
        for day, levels in sorted(by_date.items())
    ]
    return daily_by_target, stress_by_target, field_risk


def _product_plan(nutrient_plan: dict[str, float]) -> list[dict[str, float | str]]:
    products = []
    n = float(nutrient_plan.get("n_kg_ha", 0.0) or 0.0)
    p = float(nutrient_plan.get("p2o5_kg_ha", 0.0) or 0.0)
    k = float(nutrient_plan.get("k2o_kg_ha", 0.0) or 0.0)
    if n > 0:
        products.append({"product_name": "urea", "amount_kg_ha": round(n / 0.46, 3)})
    if p > 0:
        products.append({"product_name": "map", "amount_kg_ha": round(p / 0.52, 3)})
    if k > 0:
        products.append({"product_name": "potassium_sulfate", "amount_kg_ha": round(k / 0.50, 3)})
    return products


def _target_items(nutrient_plan: dict[str, float]) -> list[dict[str, float | str]]:
    product_keys = {
        "N": "urea_46N",
        "P2O5": "MAP_11_52_0",
        "K2O": "potassium_sulfate_0_0_50",
    }
    rates = {"N": 0.46, "P2O5": 0.52, "K2O": 0.50}
    nutrient_keys = {"N": "n_kg_ha", "P2O5": "p2o5_kg_ha", "K2O": "k2o_kg_ha"}
    targets = []
    for target, key in nutrient_keys.items():
        amount = float(nutrient_plan.get(key, 0.0) or 0.0)
        if amount <= 0.0:
            continue
        targets.append(
            {
                "target": target,
                "unit": f"kg_ha_as_{target}",
                "nutrient_amount": round(amount, 2),
                "product_key": product_keys[target],
                "product_amount_kg_ha": round(amount / rates[target], 2),
            }
        )
    return targets


def _maize_style_action(item: dict[str, Any], *, source: str) -> dict[str, Any]:
    date_value = str(item.get("Date"))
    window_days = int(item.get("application_window_days", 0) or 0)
    end_date = (_as_date(date_value) or date.today()) + timedelta(days=max(0, window_days))
    return {
        "Date": date_value,
        "recommendationCode": "RECOMMENDED",
        "actionTypeCode": "TREAT",
        "treatmentWindowCode": "BASE_AT_SOWING" if source == "BASE" else "CURRENT",
        "treatmentStartDate": date_value,
        "treatmentEndDate": str(end_date if source == "IN_SEASON" else date_value),
        "source": source,
        "targets": _target_items(item.get("nutrient_plan") or {}),
    }


def _merged_nutrient_plan(items: list[dict[str, Any]]) -> dict[str, float]:
    totals = {"n_kg_ha": 0.0, "p2o5_kg_ha": 0.0, "k2o_kg_ha": 0.0}
    for item in items:
        nutrient_plan = item.get("nutrient_plan") or {}
        for key in totals:
            totals[key] += float(nutrient_plan.get(key, 0.0) or 0.0)
    return {key: round(value, 3) for key, value in totals.items()}


def _fertilizer_cost(product_plan: list[dict], economics: dict, event_count: int) -> float:
    prices = economics.get("product_prices")
    if prices is None:
        raise ValueError("economics.product_prices is required")
    if economics.get("fertilizer_event_labor_cost_cny_ha") is None:
        raise ValueError("economics.fertilizer_event_labor_cost_cny_ha is required")
    total = 0.0
    for item in product_plan:
        product_name = item.get("product_name")
        if product_name not in prices:
            raise ValueError(f"economics.product_prices missing price for {product_name}")
        total += float(item.get("amount_kg_ha", 0.0) or 0.0) * float(prices[product_name])
    total += event_count * float(economics["fertilizer_event_labor_cost_cny_ha"])
    return round(total, 3)


def _split_plan(fertigation_supported: bool, soil_test: dict) -> dict[str, dict[str, Any]]:
    if soil_test.get("olsen_p_mg_kg") is None:
        raise ValueError("soil_test.olsen_p_mg_kg is required")
    available_k_raw = soil_test.get("available_k_mg_kg", soil_test.get("exchangeable_k_mg_kg"))
    if available_k_raw is None:
        raise ValueError("soil_test.available_k_mg_kg or soil_test.exchangeable_k_mg_kg is required")
    olsen_p = float(soil_test["olsen_p_mg_kg"])
    available_k = float(available_k_raw)
    low_p = olsen_p < 12.0
    low_k = available_k < 130.0
    if fertigation_supported:
        basal_p = 0.45 if low_p else 0.25
        basal_k = 0.18 if low_k else 0.08
        return {
            "basal": {"stage_code": 0, "fractions": {"N": 0.05, "P2O5": basal_p, "K2O": basal_k}},
            "squaring": {"stage_code": 51, "fractions": {"N": 0.25, "P2O5": 0.20, "K2O": 0.25}},
            "flowering": {"stage_code": 61, "fractions": {"N": 0.38, "P2O5": 0.20, "K2O": 0.35}},
            "boll_setting": {"stage_code": 75, "fractions": {"N": 0.32, "P2O5": max(0.0, 1.0 - basal_p - 0.40), "K2O": max(0.0, 1.0 - basal_k - 0.60)}},
        }
    return {
        "basal": {"stage_code": 0, "fractions": {"N": 0.20, "P2O5": 0.55 if low_p else 0.35, "K2O": 0.30 if low_k else 0.18}},
        "squaring": {"stage_code": 51, "fractions": {"N": 0.30, "P2O5": 0.20, "K2O": 0.30}},
        "flowering": {"stage_code": 61, "fractions": {"N": 0.30, "P2O5": 0.15, "K2O": 0.28}},
        "boll_setting": {"stage_code": 75, "fractions": {"N": 0.20, "P2O5": 0.10, "K2O": 0.24}},
    }


def _irrigation_dates(payload: dict) -> list[date]:
    dates = []
    for item in (payload.get("irrigation_recommendation") or []) + (payload.get("action_recommendations") or []):
        dt = item.get("recommendedIrrigationDate") or item.get("Date")
        if dt:
            dates.append(_as_date(dt))
    for item in (payload.get("irrigation_history") or []) + (payload.get("applied_irrigations") or []):
        dt = item.get("date") or item.get("Date")
        if dt:
            dates.append(_as_date(dt))
    return sorted({d for d in dates if d is not None})


def _nearest_irrigation_date(target: date, irrigation_dates: list[date]) -> date:
    if not irrigation_dates:
        return target
    future = [d for d in irrigation_dates if d >= target]
    if future:
        candidate = min(future, key=lambda d: abs((d - target).days))
        if (candidate - target).days <= 10:
            return candidate
    candidate = min(irrigation_dates, key=lambda d: abs((d - target).days))
    if abs((candidate - target).days) <= 3:
        return candidate
    return target


def _is_irrigation_coupled(day: date | None, irrigation_dates: list[date]) -> bool:
    return day is not None and day in set(irrigation_dates)


class CottonNutritionModel:
    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        decision_date = _as_date(payload.get("decision_date") or payload.get("planting_date"))
        planting_date = _as_date(payload.get("planting_date"))
        target_yield_raw = payload.get("target_yield_kg_ha")
        if target_yield_raw is None:
            raise ValueError("target_yield_kg_ha is required")
        target_yield = float(target_yield_raw)
        growth_stage = payload.get("growth_stage") or []
        if decision_date is None and growth_stage:
            decision_date = _as_date(growth_stage[0].get("Date"))
        if decision_date is None:
            raise ValueError("planting_date or growth_stage is required")
        current_stage, current_rank = _current_stage(growth_stage, decision_date)
        soil_test = payload.get("soil_test")
        if not soil_test:
            raise ValueError("soil_test is required")
        fertilizer_history = payload.get("fertilizer_history") or payload.get("applied_fertilizers") or []
        economics = {}
        economics.update(payload.get("economic_parameters") or {})
        economics.update(payload.get("economics") or {})

        fertigation_enabled = bool(payload.get("fertigation_enabled"))
        irrigation_method = str(payload.get("irrigation_method") or (payload.get("irrigation") or {}).get("irrigation_method") or "").lower()
        fertigation_supported = fertigation_enabled and irrigation_method in {"drip", "drip_under_mulch", "micro-sprinkler"}
        irrigation_dates = _irrigation_dates(payload)

        demand = {
            key: round(target_yield * coeff, 3)
            for key, coeff in COTTON_NUTRIENT_COEFFICIENTS.items()
        }
        soil_supply = _soil_supply(soil_test)
        applied = _nutrients_from_history(fertilizer_history)
        deficit = {
            key: round(max(0.0, demand[key] - soil_supply.get(key, 0.0) - applied.get(key, 0.0)), 3)
            for key in demand
        }
        status = {f"{key.lower()}_status": _status_from_gap(deficit[key], demand[key]) for key in demand}
        field_status = "deficient" if any(v == "deficient" for v in status.values()) else ("watch" if any(v == "watch" for v in status.values()) else "adequate")
        daily_nutrition_risk, stress_risk, field_risk = _build_daily_nutrition_profiles(
            growth_stage=growth_stage,
            weather_rows=payload.get("weather_data") or payload.get("weather_daily") or payload.get("weather_history") or [],
            demand=demand,
            soil_supply=soil_supply,
            fertilizer_history=fertilizer_history,
        )

        season_plan = []
        split_plan = _split_plan(fertigation_supported, soil_test)
        for split_name, split in split_plan.items():
            stage_code = int(split["stage_code"])
            if stage_code < current_rank:
                continue
            action_date = planting_date or decision_date
            if stage_code > 0:
                action_date = _stage_date(growth_stage, stage_code, decision_date + timedelta(days=10))
            if action_date < decision_date:
                action_date = decision_date
            method = "fertigation" if fertigation_supported and split_name != "basal" else ("starter_basal" if fertigation_supported and split_name == "basal" else ("basal_incorporated" if split_name == "basal" else "banded_topdress"))
            original_action_date = action_date
            if method == "fertigation":
                action_date = _nearest_irrigation_date(action_date, irrigation_dates)
            fractions = split["fractions"]
            nutrient_plan = {
                "n_kg_ha": round(deficit["N"] * float(fractions["N"]), 3),
                "p2o5_kg_ha": round(deficit["P2O5"] * float(fractions["P2O5"]), 3),
                "k2o_kg_ha": round(deficit["K2O"] * float(fractions["K2O"]), 3),
            }
            if max(nutrient_plan.values()) <= 0.5:
                continue
            product_plan = _product_plan(nutrient_plan)
            season_plan.append(
                {
                    "Date": str(action_date),
                    "recommendationCode": "COTTON_FERTIGATION_SPLIT" if method == "fertigation" else "COTTON_FERTILIZER_SPLIT",
                    "treatmentWindowCode": split_name.upper(),
                    "application_window_days": 3 if method == "fertigation" else 5,
                    "method": method,
                    "nutrient_plan": nutrient_plan,
                    "product_plan": product_plan,
                    "water_coupled": method == "fertigation",
                    "target_stage_date": str(original_action_date),
                    "irrigation_coupled_date": str(action_date) if method == "fertigation" else None,
                    "irrigation_coupled": method == "fertigation" and _is_irrigation_coupled(action_date, irrigation_dates),
                    "cost_estimate_cny_ha": _fertilizer_cost(product_plan, economics, 1),
                    "reason": [
                        f"{split_name} split follows cotton demand timing under Xinjiang drip management",
                        "fertigation date is coupled to an irrigation event" if method == "fertigation" and _is_irrigation_coupled(action_date, irrigation_dates) else "basal/starter nutrients are kept limited under drip fertigation",
                    ],
                }
            )

        apply_now_plan = next((item for item in season_plan if item["Date"] == str(decision_date)), None)
        if apply_now_plan is None and field_status != "adequate" and season_plan:
            apply_now_plan = deepcopy(season_plan[0])
            apply_now_plan["Date"] = str(decision_date)
            apply_now_plan["recommendationCode"] = "COTTON_CORRECTIVE_NPK"
            apply_now_plan["application_window_days"] = 2

        fertigation_plan = []
        if fertigation_supported:
            for item in season_plan:
                if item["method"] != "fertigation":
                    continue
                base_date = _as_date(item["Date"]) or decision_date
                nutrient_plan = item["nutrient_plan"]
                for idx, fraction in enumerate((0.45, 0.35, 0.20)):
                    raw_date = base_date + timedelta(days=idx * 7)
                    coupled_date = _nearest_irrigation_date(raw_date, irrigation_dates)
                    is_coupled = _is_irrigation_coupled(coupled_date, irrigation_dates)
                    fertigation_plan.append(
                        {
                            "Date": str(coupled_date),
                            "method": "fertigation",
                            "nutrient_plan": {
                                "n_kg_ha": round(nutrient_plan["n_kg_ha"] * fraction, 3),
                                "p2o5_kg_ha": round(nutrient_plan["p2o5_kg_ha"] * fraction, 3),
                                "k2o_kg_ha": round(nutrient_plan["k2o_kg_ha"] * fraction, 3),
                            },
                            "irrigation_coupled": is_coupled,
                            "target_date_before_coupling": str(raw_date),
                            "irrigation_coupling": "inject during the middle third of the drip event, then flush lines with clean water",
                        }
                    )

        recommendation = {
            "apply_now": apply_now_plan is not None,
            "application_window": None if apply_now_plan is None else {"start": apply_now_plan["Date"], "days": apply_now_plan["application_window_days"]},
            "method": "none" if apply_now_plan is None else apply_now_plan["method"],
            "nutrient_plan": {"n_kg_ha": 0.0, "p2o5_kg_ha": 0.0, "k2o_kg_ha": 0.0} if apply_now_plan is None else apply_now_plan["nutrient_plan"],
            "product_plan": [] if apply_now_plan is None else apply_now_plan["product_plan"],
            "season_plan_recommendations": season_plan,
            "fertigation_plan": fertigation_plan,
            "estimated_cost_cny_ha": round(sum(float(item.get("cost_estimate_cny_ha", 0.0) or 0.0) for item in season_plan), 3),
            "fertigation_enabled": fertigation_supported,
        }
        if fertigation_supported:
            public_plan = [
                item
                for item in season_plan
                if item.get("method") == "starter_basal"
                or (item.get("method") == "fertigation" and item.get("irrigation_coupled"))
            ]
        else:
            basal_plan = [item for item in season_plan if item.get("method") == "basal_incorporated"]
            foliar_plan = [item for item in season_plan if item.get("method") != "basal_incorporated"][:2]
            public_plan = basal_plan + foliar_plan
        first_high_date = _first_high_risk_date(field_risk)
        if fertigation_supported:
            actions = [
                _maize_style_action(item, source="BASE" if item.get("method") == "starter_basal" else "IN_SEASON")
                for item in public_plan
            ]
        elif first_high_date is not None and public_plan:
            nutrient_plan = _merged_nutrient_plan(public_plan)
            trigger_plan = {
                "Date": str(first_high_date),
                "application_window_days": 3 if fertigation_supported else 5,
                "method": "fertigation" if fertigation_supported else "banded_topdress",
                "nutrient_plan": nutrient_plan,
            }
            actions = [_maize_style_action(trigger_plan, source="IN_SEASON")]
        else:
            actions = []
        actions = [item for item in actions if item.get("targets")]

        return {
            "daily_nutrition_risk": daily_nutrition_risk,
            "stress_risk": stress_risk,
            "field_risk": field_risk,
            "action_recommendations": actions,
            "decision_date": str(decision_date),
            "nutrition_status": {
                "status": field_status,
                **status,
                "demand_kg_ha": demand,
                "soil_supply_kg_ha": soil_supply,
                "applied_kg_ha": applied,
                "remaining_deficit_kg_ha": deficit,
            },
            "recommendation": recommendation,
            "reason": [
                "cotton N/P/K demand is estimated from target seed-cotton yield and adjusted by measured soil supply and fertilizer history",
                "fertigation splits are only enabled for drip-compatible systems when fertigation_enabled is true",
            ],
            "assumptions": [
                "rules are calibrated for Xinjiang machine-picked cotton management ranges",
                "soil_test missing values use conservative regional defaults",
                "under drip fertigation, basal fertilizer is not forced; N is mainly split through irrigation, while P/K starter or basal use depends on soil test",
            ],
            "current_status": {
                "current_stage": current_stage,
                "current_stage_rank": current_rank,
                "target_yield_kg_ha": target_yield,
            },
            "diagnostics": {
                "fertigation_supported": fertigation_supported,
                "irrigation_method": irrigation_method,
                "irrigation_coupled_dates": [str(d) for d in irrigation_dates],
                "split_plan_basis": "drip_fertigation_reduced_basal" if fertigation_supported else "conventional_basal_plus_topdress",
                "fertilizer_history_count": len(fertilizer_history),
            },
        }
