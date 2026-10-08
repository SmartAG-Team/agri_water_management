from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
from typing import Any

from crops.cotton.config import REGION_PARAMS
from crops.cotton.irrigation import CottonIrrigationModel
from crops.cotton.nutrition import CottonNutritionModel


PHASE_WEIGHTS = {
    "seedling": {"N": 0.08, "P2O5": 0.10, "K2O": 0.07},
    "squaring": {"N": 0.20, "P2O5": 0.18, "K2O": 0.22},
    "initial_flowering": {"N": 0.24, "P2O5": 0.20, "K2O": 0.24},
    "full_flowering": {"N": 0.22, "P2O5": 0.20, "K2O": 0.22},
    "boll_setting": {"N": 0.26, "P2O5": 0.24, "K2O": 0.25},
    "initial_boll_opening": {"N": 0.0, "P2O5": 0.05, "K2O": 0.0},
    "boll_opening": {"N": 0.0, "P2O5": 0.03, "K2O": 0.0},
}

PHASE_RANKS = {
    "seedling": 13,
    "squaring": 51,
    "initial_flowering": 61,
    "full_flowering": 65,
    "boll_setting": 75,
    "initial_boll_opening": 81,
    "boll_opening": 85,
}

NUTRIENT_KEYS = {"N": "n_kg_ha", "P2O5": "p2o5_kg_ha", "K2O": "k2o_kg_ha"}
PUBLIC_NUTRIENT_TARGETS = {"N": "N", "P2O5": "P", "K2O": "K"}
STRESS_ORDER = {"LOW": 0, "IRRIGATED": 0, "FERTILIZED": 0, "FERTIGATED": 0, "MEDIUM": 1, "WATCH": 1, "HIGH": 2, "DEFICIENT": 2}
STRESS_NORMALIZE = {"WATCH": "MEDIUM", "DEFICIENT": "HIGH", "ADEQUATE": "LOW", "DISABLED": "LOW"}
MIN_FERTIWATER_NPK_KG_HA = 8.0
MAX_FERTIWATER_EVENTS_PER_PHASE = 2


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)[:10]).date()


def _stage_rank(item: dict[str, Any]) -> int:
    if item.get("BBCH") is not None:
        return int(float(item["BBCH"]))
    stage = str(item.get("Stage") or item.get("StageName") or "").strip().lower()
    return {
        "苗期": 13,
        "seedling": 13,
        "现蕾期": 51,
        "squaring": 51,
        "初花期": 61,
        "flowering": 61,
        "盛花期": 65,
        "盛铃期": 75,
        "boll_setting": 75,
        "初吐絮期": 81,
        "吐絮期": 85,
        "boll_opening": 81,
        "maturity": 89,
        "完熟期": 89,
    }.get(stage, 51)


def _phase_from_rank(rank: int) -> str:
    if rank >= 85:
        return "boll_opening"
    if rank >= 81:
        return "initial_boll_opening"
    if rank >= 75:
        return "boll_setting"
    if rank >= 65:
        return "full_flowering"
    if rank >= 61:
        return "initial_flowering"
    if rank >= 51:
        return "squaring"
    return "seedling"


def _phase_for_day(growth_stage: list[dict[str, Any]], day: date) -> tuple[str, int, date | None]:
    rows = sorted(growth_stage or [], key=lambda item: str(item.get("Date")))
    chosen = rows[0] if rows else {"BBCH": 51, "Date": day}
    for item in rows:
        item_day = _as_date(item.get("Date"))
        if item_day is not None and item_day <= day:
            chosen = item
        else:
            break
    rank = _stage_rank(chosen)
    next_start = None
    current_phase = _phase_from_rank(rank)
    current_phase_rank = PHASE_RANKS[current_phase]
    for item in rows:
        item_day = _as_date(item.get("Date"))
        item_rank = _stage_rank(item)
        if item_day is not None and item_day > day and item_rank > current_phase_rank:
            next_start = item_day
            break
    return current_phase, rank, next_start


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


def _normalize_stress(value: Any) -> str:
    raw = str(value or "LOW").upper()
    return STRESS_NORMALIZE.get(raw, raw if raw in {"LOW", "MEDIUM", "HIGH", "IRRIGATED", "FERTILIZED", "FERTIGATED"} else "LOW")


def _worst_stress(levels: list[str]) -> str:
    if not levels:
        return "LOW"
    value = max(STRESS_ORDER.get(_normalize_stress(level), 0) for level in levels)
    return {0: "LOW", 1: "MEDIUM", 2: "HIGH"}[value]


def _nutrient_plan_from_event(item: dict[str, Any]) -> dict[str, float]:
    nutrients = item.get("nutrients_kg_ha") or {}
    return {
        "n_kg_ha": round(float(nutrients.get("N", nutrients.get("n", 0.0)) or 0.0), 3),
        "p2o5_kg_ha": round(float(nutrients.get("P2O5", nutrients.get("p2o5", 0.0)) or 0.0), 3),
        "k2o_kg_ha": round(float(nutrients.get("K2O", nutrients.get("k2o", 0.0)) or 0.0), 3),
    }


def _operation_code(item: dict[str, Any]) -> str:
    op = str(item.get("operation_code") or item.get("operation") or "").upper()
    if op in {"W", "F", "FW"}:
        return op
    water_mm = float(item.get("water_mm", item.get("amount_mm", item.get("gross_depth_mm", 0.0))) or 0.0)
    nutrient_plan = _nutrient_plan_from_event(item)
    has_nutrient = any(float(value or 0.0) > 0.0 for value in nutrient_plan.values())
    if water_mm > 0.0 and has_nutrient:
        return "FW"
    if water_mm > 0.0:
        return "W"
    return "F"


def _applied_water_fertilizer_actions(payload: dict[str, Any]) -> list[dict[str, Any]]:
    actions = []
    source = payload.get("applied_water_fertilizer") or []
    for item in source:
        day = _as_date(item.get("date") or item.get("Date"))
        if day is None:
            continue
        op = _operation_code(item)
        water_mm = round(float(item.get("water_mm", item.get("amount_mm", item.get("gross_depth_mm", 0.0))) or 0.0), 3)
        nutrient_plan = _nutrient_plan_from_event(item)
        actions.append(
            {
                "Date": str(day),
                "recommendationCode": {
                    "W": "COTTON_APPLIED_WATER",
                    "F": "COTTON_APPLIED_FERTILIZER",
                    "FW": "COTTON_APPLIED_FERTIWATER",
                }[op],
                "actionTypeCode": {
                    "W": "IRRIGATION",
                    "F": "FERTILIZATION",
                    "FW": "IRRIGATION_FERTIGATION",
                }[op],
                "action_domain": "FERTIWATER",
                "operation_code": op,
                "treatmentWindowCode": str(item.get("treatmentWindowCode") or item.get("stage") or "APPLIED").upper(),
                "treatmentStartDate": str(day),
                "treatmentEndDate": str(day),
                "recommendedIrrigationDate": str(day),
                "recommendedGrossDepthMm": water_mm,
                "irrigation_method": item.get("method") or payload.get("irrigation_method"),
                "irrigation_coupled": op in {"W", "FW"},
                "nutrient_plan": nutrient_plan,
                "product_plan": item.get("product_plan") or [],
                "notes": item.get("notes"),
                "reason": [item.get("notes") or "applied water-fertilizer operation from request body"],
            }
        )
    return sorted(actions, key=lambda action: str(action.get("Date")))


def _event_water_mm(item: dict[str, Any]) -> float:
    return round(float(item.get("water_mm", item.get("amount_mm", item.get("gross_depth_mm", 0.0))) or 0.0), 3)


def _event_date_text(item: dict[str, Any]) -> str | None:
    day = _as_date(item.get("date") or item.get("Date") or item.get("recommendedIrrigationDate"))
    return None if day is None else str(day)


def _normalize_farmer_event(item: dict[str, Any], *, default_method: str | None = None) -> dict[str, Any] | None:
    day = _event_date_text(item)
    if day is None:
        return None
    water_mm = _event_water_mm(item)
    nutrients = _nutrient_plan_from_event(item)
    nutrient_source = {
        "N": nutrients["n_kg_ha"],
        "P2O5": nutrients["p2o5_kg_ha"],
        "K2O": nutrients["k2o_kg_ha"],
    }
    op = _operation_code({**item, "water_mm": water_mm, "nutrients_kg_ha": nutrient_source})
    return {
        "date": day,
        "operation_code": op,
        "water_mm": water_mm,
        "nutrients_kg_ha": nutrient_source,
        "method": item.get("method") or item.get("irrigation_method") or default_method,
        "product_name": item.get("product_name"),
        "amount_kg_ha": float(item.get("amount_kg_ha", 0.0) or 0.0),
        "notes": item.get("notes"),
    }


def _baseline_farmer_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    default_method = payload.get("irrigation_method")
    for item in payload.get("applied_water_fertilizer") or []:
        event = _normalize_farmer_event(item, default_method=default_method)
        if event is not None:
            events.append(event)
    if events:
        return sorted(events, key=lambda item: str(item.get("date")))

    for item in payload.get("irrigation_history") or payload.get("applied_irrigations") or []:
        day = _event_date_text(item)
        if day is None:
            continue
        events.append(
            {
                "date": day,
                "operation_code": "W",
                "water_mm": _event_water_mm(item),
                "nutrients_kg_ha": {"N": 0.0, "P2O5": 0.0, "K2O": 0.0},
                "method": item.get("method") or default_method,
                "notes": item.get("notes"),
            }
        )
    for item in payload.get("fertilizer_history") or payload.get("applied_fertilizers") or []:
        event = _normalize_farmer_event({**item, "water_mm": 0.0}, default_method=item.get("method"))
        if event is not None and any(float(value or 0.0) > 0.0 for value in (event.get("nutrients_kg_ha") or {}).values()):
            event["operation_code"] = "F"
            events.append(event)
    return sorted(events, key=lambda item: str(item.get("date")))


def _event_totals(events: list[dict[str, Any]]) -> dict[str, float | int]:
    totals = {"water_mm": 0.0, "n_kg_ha": 0.0, "p2o5_kg_ha": 0.0, "k2o_kg_ha": 0.0}
    event_count = 0
    water_event_count = 0
    for event in events:
        water = _event_water_mm(event)
        nutrients = _nutrient_plan_from_event(event)
        if water > 0.0:
            water_event_count += 1
        if water > 0.0 or any(float(value or 0.0) > 0.0 for value in nutrients.values()):
            event_count += 1
        totals["water_mm"] += water
        totals["n_kg_ha"] += nutrients["n_kg_ha"]
        totals["p2o5_kg_ha"] += nutrients["p2o5_kg_ha"]
        totals["k2o_kg_ha"] += nutrients["k2o_kg_ha"]
    return {
        "total_irrigation_mm": round(totals["water_mm"], 3),
        "total_n_kg_ha": round(totals["n_kg_ha"], 3),
        "total_p2o5_kg_ha": round(totals["p2o5_kg_ha"], 3),
        "total_k2o_kg_ha": round(totals["k2o_kg_ha"], 3),
        "event_count": event_count,
        "water_event_count": water_event_count,
    }


def _action_effect_targets(action: dict[str, Any]) -> set[str]:
    op = str(action.get("operation_code") or "").upper()
    nutrient_plan = action.get("nutrient_plan") or {}
    targets: set[str] = set()
    water_mm = float(action.get("recommendedGrossDepthMm", 0.0) or 0.0)
    if op in {"W", "FW"} and water_mm > 0.0:
        targets.add("DROUGHT")
    if op in {"F", "FW"}:
        if float(nutrient_plan.get("n_kg_ha", 0.0) or 0.0) > 0.0:
            targets.add("N")
        if float(nutrient_plan.get("p2o5_kg_ha", 0.0) or 0.0) > 0.0:
            targets.add("P")
        if float(nutrient_plan.get("k2o_kg_ha", 0.0) or 0.0) > 0.0:
            targets.add("K")
    return targets


class CottonManagementModel:
    def __init__(
        self,
        latitude: float,
        longitude: float,
        soil_type: str,
        irrigation_method: str,
        *,
        mulch_enabled: bool = True,
        fertigation_enabled: bool = False,
        region_code: str | None = None,
    ) -> None:
        self.water_model = CottonIrrigationModel(
            latitude=latitude,
            longitude=longitude,
            soil_type=soil_type,
            irrigation_method=irrigation_method,
            mulch_enabled=mulch_enabled,
            region_code=region_code,
        )
        self.nutrition_model = CottonNutritionModel()
        self.fertigation_enabled = fertigation_enabled

    @staticmethod
    def _economic_value(payload: dict, key: str) -> float:
        economics = {}
        economics.update(payload.get("economic_parameters") or {})
        economics.update(payload.get("economics") or {})
        if key not in economics or economics[key] is None:
            raise ValueError(f"economic_parameters missing required field: {key}")
        return float(economics[key])

    @staticmethod
    def _build_nutrition_payload(payload: dict, water_response: dict) -> dict:
        body = deepcopy(payload)
        body["irrigation"] = {
            "irrigation_method": payload.get("irrigation_method"),
            "current_status": water_response.get("current_status"),
        }
        body["irrigation_recommendation"] = water_response.get("action_recommendations") or []
        current = water_response.get("current_status") or {}
        body["root_zone_relative_available_water"] = current.get("root_zone_relative_available_water")
        return body

    @staticmethod
    def _combine_daily(water_daily: list[dict], nutrition_result: dict | None) -> list[dict]:
        nutrition_factor = 1.0
        nutrition_field_by_date = {}
        if nutrition_result:
            status = (nutrition_result.get("nutrition_status") or {}).get("status")
            nutrition_factor = {"deficient": 0.88, "watch": 0.95, "adequate": 1.0}.get(status, 0.96)
            nutrition_field_by_date = {
                str(item.get("Date")): str(item.get("field_risk", "")).upper()
                for item in nutrition_result.get("field_risk") or []
            }
        combined = []
        for row in water_daily:
            item = deepcopy(row)
            nutrition_status = nutrition_field_by_date.get(str(row.get("Date")))
            if nutrition_status:
                item["nutrition_status"] = nutrition_status
            item["n_growth_factor"] = nutrition_factor
            item["combined_growth_factor"] = round(float(row.get("water_growth_factor", 1.0) or 1.0) * nutrition_factor, 4)
            item["expected_yield_kg_ha"] = round(float(row.get("seed_cotton_yield_kg_ha", 0.0) or 0.0) * nutrition_factor, 3)
            item["fertigation_n_kg_ha"] = 0.0
            item["fertigation_p2o5_kg_ha"] = 0.0
            item["fertigation_k2o_kg_ha"] = 0.0
            combined.append(item)
        return combined

    @staticmethod
    def _build_fertilizer_block(nutrition_result: dict | None, fertiwater_plan: list[dict] | None = None) -> dict:
        fertiwater_plan = fertiwater_plan or []
        if not nutrition_result:
            return {
                "disabled": True,
                "apply_now": False,
                "method": "disabled",
                "nutrient_plan": {"n_kg_ha": 0.0, "p2o5_kg_ha": 0.0, "k2o_kg_ha": 0.0},
                "product_plan": [],
                "season_plan": [],
                "fertiwater_plan": fertiwater_plan,
            }
        rec = nutrition_result.get("recommendation") or {}
        active_plan = fertiwater_plan or rec.get("fertigation_plan", [])
        return {
            "disabled": False,
            "apply_now": bool(fertiwater_plan) or rec.get("apply_now", False),
            "application_window": rec.get("application_window"),
            "method": "fertiwater" if fertiwater_plan else rec.get("method"),
            "nutrient_plan": rec.get("nutrient_plan"),
            "product_plan": rec.get("product_plan"),
            "season_plan": rec.get("season_plan_recommendations", []),
            "fertigation_plan": active_plan,
            "fertiwater_plan": fertiwater_plan,
            "estimated_cost_cny_ha": rec.get("estimated_cost_cny_ha", 0.0),
        }

    @staticmethod
    def _remaining_deficit(nutrition_result: dict | None) -> dict[str, float]:
        status = (nutrition_result or {}).get("nutrition_status") or {}
        remaining = status.get("remaining_deficit_kg_ha") or {}
        return {key: float(remaining.get(key, 0.0) or 0.0) for key in ("N", "P2O5", "K2O")}

    @staticmethod
    def _phase_budget(remaining_deficit: dict[str, float], phase: str, allocated: dict[tuple[str, str], float]) -> dict[str, float]:
        budget = {}
        phases = list(PHASE_WEIGHTS)
        start_index = phases.index(phase) if phase in PHASE_WEIGHTS else 0
        remaining_phases = phases[start_index:]
        for nutrient in ("N", "P2O5", "K2O"):
            denominator = sum(float(PHASE_WEIGHTS[item][nutrient]) for item in remaining_phases)
            share = 0.0 if denominator <= 0 else float(PHASE_WEIGHTS[phase][nutrient]) / denominator
            value = float(remaining_deficit.get(nutrient, 0.0) or 0.0) * share
            value -= float(allocated.get((phase, nutrient), 0.0) or 0.0)
            budget[nutrient] = max(0.0, value)
        return budget

    @staticmethod
    def _remaining_phase_irrigation_mm(day: date, next_phase_start: date | None, daily: list[dict], recommended_depth: float, efficiency: float) -> float:
        end_day = next_phase_start or (day + timedelta(days=18))
        total = 0.0
        for row in daily:
            row_day = _as_date(row.get("Date"))
            if row_day is None or row_day < day or row_day >= end_day:
                continue
            etc = float(row.get("crop_et_potential_mm", 0.0) or 0.0)
            rain = float(row.get("effective_precipitation_mm", row.get("precipitation_mm", 0.0)) or 0.0)
            total += max(0.0, etc - rain) / max(efficiency, 0.5)
        return round(max(recommended_depth, total), 3)

    @staticmethod
    def _soil_ec(payload: dict) -> float | None:
        soil_test = payload.get("soil_test") or {}
        for key in ("soil_ec_ds_m", "ec_ds_m", "electrical_conductivity_ds_m", "EC"):
            if soil_test.get(key) is not None:
                return float(soil_test[key])
        for key in ("soil_ec_ds_m", "ec_ds_m", "electrical_conductivity_ds_m", "EC"):
            if payload.get(key) is not None:
                return float(payload[key])
        return None

    def _ec_constraint(self, payload: dict) -> dict[str, Any]:
        ec = self._soil_ec(payload)
        watch = float(REGION_PARAMS.get(self.water_model.region_code, {}).get("salinity_watch_ec_ds_m", 3.0))
        if ec is None or ec < watch:
            return {"soil_ec_ds_m": ec, "threshold_ds_m": watch, "load_factor": 1.0, "limited": False}
        load_factor = 0.65 if ec < watch + 1.0 else 0.45
        return {
            "soil_ec_ds_m": round(ec, 3),
            "threshold_ds_m": watch,
            "load_factor": load_factor,
            "limited": True,
            "reason": "soil EC is above the regional watch threshold; nutrient load is diluted while irrigation depth is unchanged",
        }

    @staticmethod
    def _payload_with_farmer_events(payload: dict, events: list[dict[str, Any]]) -> dict:
        body = deepcopy(payload)
        body["applied_water_fertilizer"] = deepcopy(events)
        irrigation_history = []
        fertilizer_history = []
        for event in events:
            day = event.get("date") or event.get("Date")
            water = _event_water_mm(event)
            nutrients = _nutrient_plan_from_event(event)
            if water > 0.0:
                irrigation_history.append(
                    {
                        "date": day,
                        "amount_mm": water,
                        "method": event.get("method") or body.get("irrigation_method"),
                        "notes": event.get("notes"),
                    }
                )
            if any(float(value or 0.0) > 0.0 for value in nutrients.values()):
                fertilizer_history.append(
                    {
                        "date": day,
                        "product_name": event.get("product_name") or "water_soluble_npk",
                        "amount_kg_ha": float(event.get("amount_kg_ha", 0.0) or 0.0),
                        "nutrients_kg_ha": {
                            "N": nutrients["n_kg_ha"],
                            "P2O5": nutrients["p2o5_kg_ha"],
                            "K2O": nutrients["k2o_kg_ha"],
                        },
                        "method": event.get("method") or "fertigation",
                        "notes": event.get("notes"),
                    }
                )
        body["irrigation_history"] = irrigation_history
        body["fertilizer_history"] = fertilizer_history
        body["applied_irrigations"] = irrigation_history
        body["applied_fertilizers"] = fertilizer_history
        return body

    @staticmethod
    def _is_salinity_or_establishment_water(event: dict[str, Any], planting_date: date | None) -> bool:
        day = _as_date(event.get("date") or event.get("Date"))
        water = _event_water_mm(event)
        notes = str(event.get("notes") or "")
        method = str(event.get("method") or "").lower()
        if "压盐" in notes or "造墒" in notes or "salt" in notes.lower():
            return True
        return bool(day and planting_date and day <= planting_date and (method == "flood" or water >= 100.0))

    @staticmethod
    def _phase_min_interval_days(rank: int) -> int:
        if rank >= 81:
            return 10
        if rank >= 61:
            return 5
        if rank >= 51:
            return 6
        return 8

    @staticmethod
    def _event_rank(growth_stage: list[dict[str, Any]], day: date) -> int:
        _, rank, _ = _phase_for_day(growth_stage, day)
        return rank

    @staticmethod
    def _water_event_count(events: list[dict[str, Any]]) -> int:
        return sum(1 for event in events if _event_water_mm(event) > 0.0)

    def _max_prescription_water_events(self, payload: dict, baseline_events: list[dict[str, Any]] | None = None) -> int:
        raw = payload.get("max_prescription_irrigation_events")
        if raw is not None:
            return max(1, int(raw))
        if baseline_events:
            farmer_water_events = self._water_event_count(baseline_events)
            if farmer_water_events > 0:
                return min(15, max(1, farmer_water_events - 1))
        return 15

    def _candidate_events(
        self,
        payload: dict,
        baseline_events: list[dict[str, Any]],
        *,
        water_factor: float,
        nutrient_factor: float,
        rebalance_peak_gaps: bool,
    ) -> list[dict[str, Any]]:
        planting_date = _as_date(payload.get("planting_date"))
        growth_stage = payload.get("growth_stage") or []
        ec_load_factor = float(self._ec_constraint(payload).get("load_factor", 1.0))
        fertigation_enabled = bool(payload.get("fertigation_enabled", self.fertigation_enabled))
        candidates: list[dict[str, Any]] = []
        for event in baseline_events:
            item = deepcopy(event)
            day = _as_date(item.get("date") or item.get("Date"))
            rank = self._event_rank(growth_stage, day) if day else 0
            water = _event_water_mm(item)
            nutrients = _nutrient_plan_from_event(item)
            salinity_event = self._is_salinity_or_establishment_water(item, planting_date)

            if water > 0.0 and not salinity_event:
                method = str(item.get("method") or payload.get("irrigation_method") or "").lower()
                water = round(water * water_factor, 3)
                if method in {"drip", "drip_under_mulch", "micro-sprinkler"}:
                    water = max(20.0, water)
            for nutrient, out_key in (("N", "n_kg_ha"), ("P2O5", "p2o5_kg_ha"), ("K2O", "k2o_kg_ha")):
                value = float(nutrients[out_key])
                if value <= 0.0:
                    continue
                if nutrient == "N" and rank >= 81:
                    value = 0.0
                elif not salinity_event:
                    value *= nutrient_factor * ec_load_factor
                nutrients[out_key] = round(max(0.0, value), 3)

            nutrient_source = {"N": nutrients["n_kg_ha"], "P2O5": nutrients["p2o5_kg_ha"], "K2O": nutrients["k2o_kg_ha"]}
            item["water_mm"] = round(water, 3)
            item["nutrients_kg_ha"] = nutrient_source
            item["operation_code"] = _operation_code(item)
            if fertigation_enabled and item["operation_code"] == "F" and day and planting_date and day > planting_date:
                # First version avoids standalone in-season fertilizer in fertigation systems.
                item["nutrients_kg_ha"] = {"N": 0.0, "P2O5": 0.0, "K2O": 0.0}
                item["operation_code"] = "W" if water > 0.0 else "F"
            candidates.append(item)

        # In prescription mode, in-season water/FW events are not kept on the
        # farmer calendar. They are templates: each event is scheduled only
        # after the current model run reaches HIGH drought stress, then the
        # event is added to applied_water_fertilizer and the model is rerun for
        # the next trigger.
        return self._rescue_supplement_events(payload, sorted(candidates, key=lambda item: str(item.get("date") or item.get("Date"))))

    def _rescue_supplement_events(self, payload: dict, baseline_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        planting_date = _as_date(payload.get("planting_date"))
        growth_stage = payload.get("growth_stage") or []
        max_water_events = self._max_prescription_water_events(payload, baseline_events)
        method = str(payload.get("irrigation_method") or "").lower()
        limited_water_count_mode = method in {"drip", "drip_under_mulch", "micro-sprinkler"} and max_water_events <= 15
        drip_depth_cap_mm = 80.0 if limited_water_count_mode else 45.0
        retained: list[dict[str, Any]] = []
        templates: list[dict[str, Any]] = []
        for event in baseline_events:
            item = deepcopy(event)
            item["source"] = "farmer_baseline_retained"
            day = _as_date(item.get("date") or item.get("Date"))
            water = _event_water_mm(item)
            if (
                water > 0.0
                and day is not None
                and planting_date is not None
                and day > planting_date
                and not self._is_salinity_or_establishment_water(item, planting_date)
            ):
                item["source"] = "water_balance_template"
                templates.append(item)
            else:
                retained.append(item)

        def last_water_day(events: list[dict[str, Any]]) -> date:
            dates = [
                _as_date(event.get("date") or event.get("Date"))
                for event in events
                if _event_water_mm(event) > 0.0 and _as_date(event.get("date") or event.get("Date")) is not None
            ]
            return max(dates) if dates else (planting_date or date.min)

        def scheduling_interval(previous_water: date) -> int:
            base = self._phase_min_interval_days(self._event_rank(growth_stage, previous_water))
            if limited_water_count_mode:
                base = max(base, 10 if self._event_rank(growth_stage, previous_water) >= 81 else 8)
            return base

        def trigger_day(events: list[dict[str, Any]], earliest: date, early_earliest: date | None = None) -> tuple[date, dict[str, Any], str] | None:
            early_earliest = early_earliest or earliest
            water_response = self.water_model.run(self._payload_with_farmer_events(payload, events))
            rows = water_response.get("daily_stress_risk") or []
            for index, row in enumerate(rows):
                day = _as_date(row.get("Date"))
                if day is None or day < early_earliest:
                    continue
                stress = str(row.get("stress_risk", "LOW")).upper()
                next_stress = str((rows[index + 1] if index + 1 < len(rows) else {}).get("stress_risk", "LOW")).upper()
                if day < earliest:
                    if stress == "HIGH":
                        return day, row, "current_high"
                    if stress == "MEDIUM" and next_stress == "HIGH":
                        return day, row, "forecast_next_day_high"
                    continue
                if stress == "HIGH":
                    return day, row, "current_high"
                if stress == "MEDIUM" and next_stress == "HIGH":
                    return day, row, "forecast_next_day_high"
            return None

        def bounded_drip_depth(template_water: float, trigger_record: dict[str, Any]) -> float:
            if method not in {"drip", "drip_under_mulch", "micro-sprinkler"}:
                return round(template_water, 3)
            capacity = float(trigger_record.get("root_zone_capacity_mm", 0.0) or 0.0)
            target = float(trigger_record.get("target_relative_available_water", 0.0) or 0.0)
            raw = float(trigger_record.get("root_zone_relative_available_water", 0.0) or 0.0)
            efficiency = float(self.water_model.method_specs.get("efficiency", 0.9) or 0.9)
            deficit = max(0.0, (target - raw) * capacity)
            refill_depth = deficit / max(efficiency, 0.5)
            if template_water > 0.0:
                regular_depth = min(drip_depth_cap_mm, template_water)
                refill_depth = max(refill_depth, regular_depth)
            return round(max(20.0, min(drip_depth_cap_mm, refill_depth)), 3)

        scheduled = retained[:]
        for template in templates:
            if self._water_event_count(scheduled) >= max_water_events:
                break
            previous_water = last_water_day(scheduled)
            interval = scheduling_interval(previous_water)
            earliest = previous_water + timedelta(days=interval)
            early_earliest = previous_water + timedelta(days=max(3, interval - 2))
            trigger = trigger_day(scheduled, earliest, early_earliest)
            if trigger is None:
                continue
            day, trigger_record, trigger_condition = trigger
            item = deepcopy(template)
            template_water = _event_water_mm(item)
            prescription_water = bounded_drip_depth(template_water, trigger_record)
            item["date"] = str(day)
            item["water_mm"] = prescription_water
            item["source"] = "water_balance_triggered"
            item["notes"] = "处方触发：根区有效水进入高风险，按水分平衡和灌溉次数上限安排灌水"
            item["trigger_risk_before_action"] = str(trigger_record.get("stress_risk", "LOW")).upper()
            item["trigger_condition"] = trigger_condition
            item["trigger_root_zone_relative_available_water"] = trigger_record.get("root_zone_relative_available_water")
            nutrients = _nutrient_plan_from_event(item)
            if self._event_rank(growth_stage, day) >= 81:
                nutrients["n_kg_ha"] = 0.0
            item["nutrients_kg_ha"] = {"N": nutrients["n_kg_ha"], "P2O5": nutrients["p2o5_kg_ha"], "K2O": nutrients["k2o_kg_ha"]}
            item["operation_code"] = _operation_code(item)
            scheduled.append(item)

        raw_supplements = payload.get("max_rescue_supplement_irrigations")
        max_supplements = 10 if raw_supplements is None else int(raw_supplements)
        max_supplements = max(0, min(10, max_supplements))
        max_supplements = min(max_supplements, max(0, max_water_events - self._water_event_count(scheduled)))
        for _ in range(max_supplements):
            if self._water_event_count(scheduled) >= max_water_events:
                break
            previous_water = last_water_day(scheduled)
            interval = scheduling_interval(previous_water)
            earliest = previous_water + timedelta(days=interval)
            early_earliest = previous_water + timedelta(days=max(3, interval - 2))
            trigger = trigger_day(scheduled, earliest, early_earliest)
            if trigger is None:
                break
            day, trigger_record, trigger_condition = trigger
            water_response = self.water_model.run(self._payload_with_farmer_events(payload, scheduled))
            if not any(str(row.get("stress_risk", "LOW")).upper() == "HIGH" for row in water_response.get("daily_stress_risk") or []):
                break
            record = next((row for row in water_response.get("daily_stress_risk") or [] if str(row.get("Date"))[:10] == str(day)), {})
            capacity = float(record.get("root_zone_capacity_mm", 0.0) or 0.0)
            target = float(record.get("target_relative_available_water", 0.0) or 0.0)
            raw = float(record.get("root_zone_relative_available_water", 0.0) or 0.0)
            deficit = max(0.0, (target - raw) * capacity)
            efficiency = float(self.water_model.method_specs.get("efficiency", 0.9) or 0.9)
            depth = max(20.0, min(drip_depth_cap_mm, deficit / max(efficiency, 0.5)))
            scheduled.append(
                {
                    "date": str(day),
                    "operation_code": "W",
                    "water_mm": round(depth, 3),
                    "nutrients_kg_ha": {"N": 0.0, "P2O5": 0.0, "K2O": 0.0},
                    "method": payload.get("irrigation_method"),
                    "notes": "保产补水处方：根区有效水进入高风险后按灌溉次数上限补灌",
                    "source": "prescription_supplement",
                    "trigger_risk_before_action": str(trigger_record.get("stress_risk", "LOW")).upper(),
                    "trigger_condition": trigger_condition,
                    "trigger_root_zone_relative_available_water": trigger_record.get("root_zone_relative_available_water"),
                }
            )
        return sorted(scheduled, key=lambda item: str(item.get("date") or item.get("Date")))

    def _split_large_drip_events(self, payload: dict, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        growth_stage = payload.get("growth_stage") or []
        planting_date = _as_date(payload.get("planting_date"))
        out: list[dict[str, Any]] = []
        for event in events:
            day = _as_date(event.get("date") or event.get("Date"))
            water = _event_water_mm(event)
            method = str(event.get("method") or payload.get("irrigation_method") or "").lower()
            if (
                day is None
                or water <= 45.0
                or method not in {"drip", "drip_under_mulch", "micro-sprinkler"}
                or self._is_salinity_or_establishment_water(event, planting_date)
            ):
                out.append(event)
                continue
            rank = self._event_rank(growth_stage, day)
            interval = self._phase_min_interval_days(rank)
            lead_days = 3 if rank >= 61 or "高干旱风险" in str(event.get("notes") or "") else 1
            chunk_count = max(2, int((water + 44.999) // 45.0))
            chunk_water = round(water / chunk_count, 3)
            nutrients = _nutrient_plan_from_event(event)
            for index in range(chunk_count):
                chunk_day = day - timedelta(days=lead_days + interval * (chunk_count - index - 1))
                if planting_date and chunk_day < planting_date:
                    chunk_day = planting_date
                ratio = chunk_water / max(water, 1e-6)
                chunk_nutrients = {
                    "N": round(nutrients["n_kg_ha"] * ratio, 3),
                    "P2O5": round(nutrients["p2o5_kg_ha"] * ratio, 3),
                    "K2O": round(nutrients["k2o_kg_ha"] * ratio, 3),
                }
                if self._event_rank(growth_stage, chunk_day) >= 81:
                    chunk_nutrients["N"] = 0.0
                chunk = deepcopy(event)
                chunk["date"] = str(chunk_day)
                chunk["water_mm"] = chunk_water
                chunk["nutrients_kg_ha"] = chunk_nutrients
                chunk["operation_code"] = _operation_code(chunk)
                chunk["notes"] = "处方重排：单次滴灌控制在45mm以内，并提前避开预测高风险"
                out.append(chunk)
        return sorted(out, key=lambda item: str(item.get("date") or item.get("Date")))

    def _rebalance_peak_water_gaps(self, payload: dict, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        growth_stage = payload.get("growth_stage") or []
        water_indexes = [idx for idx, event in enumerate(events) if _event_water_mm(event) > 0.0]
        additions: list[dict[str, Any]] = []
        for prev_idx, next_idx in zip(water_indexes, water_indexes[1:]):
            prev_event = events[prev_idx]
            next_event = events[next_idx]
            prev_day = _as_date(prev_event.get("date") or prev_event.get("Date"))
            next_day = _as_date(next_event.get("date") or next_event.get("Date"))
            if prev_day is None or next_day is None:
                continue
            gap = (next_day - prev_day).days
            rank = self._event_rank(growth_stage, prev_day)
            min_interval = self._phase_min_interval_days(rank)
            if gap <= min_interval * 2:
                continue
            if rank < 51 or rank >= 89:
                continue
            next_water = _event_water_mm(next_event)
            if next_water < 35.0:
                continue
            split_water = round(max(20.0, min(45.0, next_water * 0.45)), 3)
            remaining_water = round(max(20.0, next_water - split_water), 3)
            if remaining_water + split_water > next_water:
                split_water = round(max(0.0, next_water - remaining_water), 3)
            if split_water <= 0.0:
                continue
            split_day = prev_day + timedelta(days=max(min_interval, gap // 2))
            if split_day >= next_day:
                continue

            nutrients = _nutrient_plan_from_event(next_event)
            ratio = split_water / max(next_water, 1e-6)
            split_nutrients = {
                "N": round(nutrients["n_kg_ha"] * ratio, 3),
                "P2O5": round(nutrients["p2o5_kg_ha"] * ratio, 3),
                "K2O": round(nutrients["k2o_kg_ha"] * ratio, 3),
            }
            rank_at_split = self._event_rank(growth_stage, split_day)
            if rank_at_split >= 81:
                split_nutrients["N"] = 0.0
            next_event["water_mm"] = remaining_water
            next_event["nutrients_kg_ha"] = {
                "N": round(max(0.0, nutrients["n_kg_ha"] - split_nutrients["N"]), 3),
                "P2O5": round(max(0.0, nutrients["p2o5_kg_ha"] - split_nutrients["P2O5"]), 3),
                "K2O": round(max(0.0, nutrients["k2o_kg_ha"] - split_nutrients["K2O"]), 3),
            }
            next_event["operation_code"] = _operation_code(next_event)
            additions.append(
                {
                    "date": str(split_day),
                    "operation_code": "FW" if any(value > 0.0 for value in split_nutrients.values()) else "W",
                    "water_mm": split_water,
                    "nutrients_kg_ha": split_nutrients,
                    "method": next_event.get("method") or payload.get("irrigation_method"),
                    "notes": "处方重排：高耗水期长间隔拆分为小水勤灌",
                }
            )
        return sorted(events + additions, key=lambda item: str(item.get("date") or item.get("Date")))

    def _evaluate_prescription_events(
        self,
        payload: dict,
        events: list[dict[str, Any]],
        *,
        scenario_name: str,
        scenario_label: str,
    ) -> dict[str, Any]:
        scenario_payload = self._payload_with_farmer_events(payload, events)
        water_response = self.water_model.run(scenario_payload)
        nutrition_result = None
        if scenario_payload.get("management_mode") == "water_nutrition" or bool(scenario_payload.get("fertigation_enabled", self.fertigation_enabled)):
            nutrition_result = self.nutrition_model.run(self._build_nutrition_payload(scenario_payload, water_response))
        daily_management_state = self._combine_daily(water_response.get("daily_stress_risk") or [], nutrition_result)
        daily_fertiwater_risk, stress_risk, field_risk = self._normalized_management_blocks(daily_management_state, nutrition_result, scenario_payload)
        final_state = daily_management_state[-1] if daily_management_state else (water_response.get("current_status") or {})
        expected_yield = float(final_state.get("expected_yield_kg_ha", final_state.get("seed_cotton_yield_kg_ha", 0.0)) or 0.0)
        totals = _event_totals(events)
        economics = {}
        economics.update(payload.get("economic_parameters") or {})
        economics.update(payload.get("economics") or {})
        seed_price = float(economics.get("seed_cotton_price_cny_per_kg", 0.0) or 0.0)
        water_unit_cost = float(economics.get("water_cost_cny_per_mm_ha", 0.0) or 0.0) + (
            float(economics.get("electricity_cost_cny_per_kwh", 0.0) or 0.0)
            * float(economics.get("pump_kwh_per_mm_ha", 0.0) or 0.0)
        )
        irrigation_cost = float(totals["total_irrigation_mm"]) * water_unit_cost
        irrigation_cost += int(totals["event_count"]) * float(economics.get("irrigation_event_labor_cost_cny_ha", 0.0) or 0.0)
        product_prices = economics.get("product_prices") or {}
        fertilizer_cost = (
            float(totals["total_n_kg_ha"]) / 0.46 * float(product_prices.get("urea", 0.0) or 0.0)
            + float(totals["total_p2o5_kg_ha"]) / 0.52 * float(product_prices.get("map", 0.0) or 0.0)
            + float(totals["total_k2o_kg_ha"]) / 0.50 * float(product_prices.get("potassium_sulfate", 0.0) or 0.0)
        )
        fertilizer_event_count = sum(
            1 for event in events if any(value > 0.0 for value in (_nutrient_plan_from_event(event)).values())
        )
        fertilizer_cost += fertilizer_event_count * float(economics.get("fertilizer_event_labor_cost_cny_ha", 0.0) or 0.0)
        high_days = sum(1 for row in field_risk if row.get("field_risk") == "HIGH")
        medium_days = sum(1 for row in field_risk if row.get("field_risk") == "MEDIUM")
        gross_revenue = expected_yield * seed_price
        net_return = gross_revenue - irrigation_cost - fertilizer_cost
        return {
            "scenario_name": scenario_name,
            "scenario_label": scenario_label,
            "events": events,
            "totals": totals,
            "expected_yield_kg_ha": round(expected_yield, 3),
            "high_risk_days": high_days,
            "medium_risk_days": medium_days,
            "daily_fertiwater_risk": daily_fertiwater_risk,
            "stress_risk": stress_risk,
            "field_risk": field_risk,
            "daily_management_state": daily_management_state,
            "economic_summary": {
                "gross_revenue_cny_ha": round(gross_revenue, 3),
                "irrigation_cost_cny_ha": round(irrigation_cost, 3),
                "fertilizer_cost_cny_ha": round(fertilizer_cost, 3),
                "expected_net_return_cny_ha": round(net_return, 3),
            },
        }

    @staticmethod
    def _prescription_actions(events: list[dict[str, Any]], scenario_name: str) -> list[dict[str, Any]]:
        actions = []
        for event in events:
            day = _event_date_text(event)
            if day is None:
                continue
            water = _event_water_mm(event)
            nutrients = _nutrient_plan_from_event(event)
            nutrient_source = {"n_kg_ha": nutrients["n_kg_ha"], "p2o5_kg_ha": nutrients["p2o5_kg_ha"], "k2o_kg_ha": nutrients["k2o_kg_ha"]}
            op = _operation_code(event)
            if water <= 0.0 and not any(value > 0.0 for value in nutrient_source.values()):
                continue
            actions.append(
                {
                    "Date": day,
                    "recommendationCode": {
                        "W": "COTTON_PRESCRIPTION_WATER",
                        "F": "COTTON_PRESCRIPTION_FERTILIZER",
                        "FW": "COTTON_PRESCRIPTION_FERTIWATER",
                    }[op],
                    "actionTypeCode": {
                        "W": "IRRIGATION",
                        "F": "FERTILIZATION",
                        "FW": "IRRIGATION_FERTIGATION",
                    }[op],
                    "action_domain": "FERTIWATER",
                    "operation_code": op,
                    "scenario_name": scenario_name,
                    "bbch": event.get("bbch"),
                    "treatmentStartDate": day,
                    "treatmentEndDate": day,
                    "recommendedIrrigationDate": day,
                    "recommendedGrossDepthMm": water,
                    "irrigation_method": event.get("method"),
                    "source": event.get("source", "prescription_candidate"),
                    "trigger_risk_before_action": event.get("trigger_risk_before_action"),
                    "trigger_condition": event.get("trigger_condition"),
                    "trigger_root_zone_relative_available_water": event.get("trigger_root_zone_relative_available_water"),
                    "nutrient_plan": nutrient_source,
                    "product_plan": _product_plan(nutrient_source),
                    "reason": [event.get("notes") or "yield-guarded water/fertilizer saving prescription"],
                }
            )
        return sorted(actions, key=lambda item: str(item.get("Date")))

    @staticmethod
    def _irrigation_recommendation_actions(water_response: dict) -> list[dict[str, Any]]:
        actions = []
        for item in water_response.get("action_recommendations") or []:
            if item.get("recommendationCode") != "IRRIGATE":
                continue
            action = deepcopy(item)
            action.setdefault("Date", item.get("recommendedIrrigationDate"))
            action.setdefault("actionTypeCode", "IRRIGATION")
            action["action_domain"] = "IRRIGATION"
            action["operation_code"] = "W"
            action["nutrient_plan"] = {"n_kg_ha": 0.0, "p2o5_kg_ha": 0.0, "k2o_kg_ha": 0.0}
            actions.append(action)
        return actions

    @staticmethod
    def _resource_savings(candidate: dict[str, Any], baseline: dict[str, Any]) -> dict[str, float]:
        out = {}
        for field, label in (
            ("total_irrigation_mm", "water_mm"),
            ("total_n_kg_ha", "n_kg_ha"),
            ("total_p2o5_kg_ha", "p2o5_kg_ha"),
            ("total_k2o_kg_ha", "k2o_kg_ha"),
        ):
            base_value = float(baseline["totals"].get(field, 0.0) or 0.0)
            candidate_value = float(candidate["totals"].get(field, 0.0) or 0.0)
            saved = round(base_value - candidate_value, 3)
            out[f"{label}_saved"] = saved
            out[f"{label}_saved_pct"] = round(saved / base_value * 100.0, 3) if base_value > 0.0 else 0.0
        return out

    def _build_prescription(
        self,
        payload: dict,
        baseline_evaluation: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
        baseline_events = _baseline_farmer_events(payload)
        if not baseline_events:
            raise ValueError("farmer baseline is required for water/fertilizer saving prescription")

        scenarios = [
            baseline_evaluation,
            self._evaluate_prescription_events(
                payload,
                self._candidate_events(payload, baseline_events, water_factor=1.00, nutrient_factor=1.00, rebalance_peak_gaps=True),
                scenario_name="timing_optimization",
                scenario_label="关键期重排方案",
            ),
            self._evaluate_prescription_events(
                payload,
                self._candidate_events(payload, baseline_events, water_factor=0.98, nutrient_factor=0.96, rebalance_peak_gaps=True),
                scenario_name="water_saving",
                scenario_label="节水方案",
            ),
            self._evaluate_prescription_events(
                payload,
                self._candidate_events(payload, baseline_events, water_factor=1.00, nutrient_factor=0.88, rebalance_peak_gaps=True),
                scenario_name="fertilizer_saving",
                scenario_label="节肥方案",
            ),
            self._evaluate_prescription_events(
                payload,
                self._candidate_events(payload, baseline_events, water_factor=0.98, nutrient_factor=0.94, rebalance_peak_gaps=True),
                scenario_name="balanced_saving",
                scenario_label="平衡节水节肥方案",
            ),
            self._evaluate_prescription_events(
                payload,
                self._candidate_events(payload, baseline_events, water_factor=0.97, nutrient_factor=0.94, rebalance_peak_gaps=True),
                scenario_name="net_return_priority",
                scenario_label="净收益优先方案",
            ),
        ]
        if int(baseline_evaluation.get("high_risk_days", 0) or 0) > 0:
            scenarios.append(
                self._evaluate_prescription_events(
                    payload,
                    self._rescue_supplement_events(payload, baseline_events),
                    scenario_name="yield_rescue_supplement",
                    scenario_label="保产补水方案",
                )
            )
        baseline_yield = float(baseline_evaluation["expected_yield_kg_ha"])
        yield_floor = baseline_yield * 0.95
        baseline_high_days = int(baseline_evaluation["high_risk_days"])
        max_water_events = self._max_prescription_water_events(payload, baseline_events)
        high_risk_allowance = max(baseline_high_days + 14, 14)
        for scenario in scenarios:
            scenario["resource_savings_vs_farmer"] = self._resource_savings(scenario, baseline_evaluation)
            scenario["feasible"] = (
                float(scenario["expected_yield_kg_ha"]) >= yield_floor
                and int(scenario["high_risk_days"]) <= high_risk_allowance
                and int((scenario.get("totals") or {}).get("water_event_count", 0) or 0) <= max_water_events
            )

        feasible = [scenario for scenario in scenarios if scenario.get("feasible")]
        selected = max(
            feasible or [baseline_evaluation],
            key=lambda scenario: (
                float((scenario.get("economic_summary") or {}).get("expected_net_return_cny_ha", 0.0) or 0.0),
                float((scenario.get("resource_savings_vs_farmer") or {}).get("water_mm_saved", 0.0) or 0.0),
                float((scenario.get("resource_savings_vs_farmer") or {}).get("n_kg_ha_saved", 0.0) or 0.0)
                + float((scenario.get("resource_savings_vs_farmer") or {}).get("p2o5_kg_ha_saved", 0.0) or 0.0)
                + float((scenario.get("resource_savings_vs_farmer") or {}).get("k2o_kg_ha_saved", 0.0) or 0.0),
                -int((scenario.get("totals") or {}).get("event_count", 0) or 0),
            ),
        )
        for event in selected.get("events") or []:
            day = _as_date(event.get("date") or event.get("Date"))
            if day is not None:
                event["bbch"] = self._event_rank(payload.get("growth_stage") or [], day)
        prescription_actions = self._prescription_actions(selected["events"], selected["scenario_name"])
        drought_by_day = {
            str(row.get("Date"))[:10]: row
            for row in ((selected.get("daily_fertiwater_risk") or {}).get("DROUGHT") or [])
        }
        for action in prescription_actions:
            day = _as_date(action.get("Date") or action.get("recommendedIrrigationDate"))
            if day is None or float(action.get("recommendedGrossDepthMm", 0.0) or 0.0) <= 0.0:
                continue
            current = drought_by_day.get(str(day)) or {}
            following = drought_by_day.get(str(day + timedelta(days=1))) or {}
            action["current_risk_after_action"] = current.get("stress_risk")
            action["next_day_risk_after_action"] = following.get("stress_risk")
            action["root_zone_relative_available_water_after_action"] = current.get("root_zone_relative_available_water")
        action_by_day: dict[str, list[str]] = {}
        for action in prescription_actions:
            action_by_day.setdefault(str(action["Date"]), []).append(str(action["recommendationCode"]))
        field_by_day = {str(row.get("Date")): row for row in selected.get("field_risk") or []}
        daily_prescription = []
        for row in selected.get("daily_management_state") or []:
            day = str(row.get("Date"))
            daily_prescription.append(
                {
                    "Date": day,
                    "bbch_phase": row.get("Stage"),
                    "bbch": row.get("BBCH"),
                    "terminal_drydown": row.get("terminal_drydown"),
                    "days_to_boll_opening": row.get("days_to_boll_opening"),
                    "root_zone_relative_available_water": row.get("root_zone_relative_available_water"),
                    "storage_root_zone_relative_available_water": row.get("storage_root_zone_relative_available_water"),
                    "active_root_zone_relative_available_water": row.get("active_root_zone_relative_available_water"),
                    "drought_raw_risk": row.get("raw_water_stress_risk"),
                    "drought_managed_risk": row.get("water_stress_risk"),
                    "field_risk": (field_by_day.get(day) or {}).get("field_risk"),
                    "action_codes": action_by_day.get(day, []),
                }
            )
        no_saving_reason = None
        if selected["scenario_name"] == "farmer_practice":
            if int(selected.get("high_risk_days", 0) or 0) > 0:
                no_saving_reason = "当地农户方案仍存在高干旱风险，不能继续节水；当前处方只能提示优化分布或保产补水。"
            else:
                no_saving_reason = "当地农户方案已经是保产约束下的可行基线，当前模型未找到同时保产并节水节肥的更优方案。"
        elif selected["resource_savings_vs_farmer"].get("water_mm_saved", 0.0) < 0:
            if int(selected.get("high_risk_days", 0) or 0) > 0:
                no_saving_reason = "农户方案存在欠灌风险；处方优先提前邻近灌水并少量补水，但在操作次数约束下仍有残余高干旱风险，不能继续节水。"
            else:
                no_saving_reason = "农户方案存在欠灌风险，处方优先保产并优化分布，不能继续节水。"
        elif (
            selected["resource_savings_vs_farmer"].get("water_mm_saved", 0.0) <= 0.0
            and selected["resource_savings_vs_farmer"].get("n_kg_ha_saved", 0.0) <= 0.0
            and selected["resource_savings_vs_farmer"].get("p2o5_kg_ha_saved", 0.0) <= 0.0
            and selected["resource_savings_vs_farmer"].get("k2o_kg_ha_saved", 0.0) <= 0.0
            and int(selected.get("high_risk_days", 0) or 0) > 0
        ):
            no_saving_reason = "当地农户方案存在欠灌或时序风险，不能继续节水节肥；处方仅重排关键期水肥以保产。"

        prescription = {
            "selected_scenario": selected["scenario_name"],
            "selected_scenario_label": selected["scenario_label"],
            "baseline_type": payload.get("baseline_type", "local_farmer_practice"),
            "objective": payload.get("prescription_objective", "yield_guarded_saving"),
            "yield_guardrail": {
                "basis": "farmer_baseline_yield",
                "minimum_relative_yield": 0.95,
                "minimum_expected_yield_kg_ha": round(yield_floor, 3),
            },
            "irrigation_event_limit": {
                "max_water_events": max_water_events,
                "selected_water_events": int((selected.get("totals") or {}).get("water_event_count", 0) or 0),
                "limit_reached": int((selected.get("totals") or {}).get("water_event_count", 0) or 0) >= max_water_events,
            },
            "daily_prescription": daily_prescription,
            "actions": prescription_actions,
            "resource_savings_vs_farmer": selected["resource_savings_vs_farmer"],
            "risk_after_prescription": {
                "high_risk_days": selected["high_risk_days"],
                "medium_risk_days": selected["medium_risk_days"],
                "max_field_risk": _worst_stress([row.get("field_risk") for row in selected.get("field_risk") or []]),
            },
            "yield_outlook": {
                "farmer_baseline_yield_kg_ha": round(baseline_yield, 3),
                "expected_yield_kg_ha": selected["expected_yield_kg_ha"],
                "yield_change_vs_farmer_kg_ha": round(float(selected["expected_yield_kg_ha"]) - baseline_yield, 3),
            },
            "economic_summary": selected["economic_summary"],
            "no_saving_reason": no_saving_reason,
        }
        comparison = [
            {
                "scenario_name": scenario["scenario_name"],
                "scenario_label": scenario["scenario_label"],
                "feasible": bool(scenario.get("feasible")),
                "totals": scenario["totals"],
                "expected_yield_kg_ha": scenario["expected_yield_kg_ha"],
                "high_risk_days": scenario["high_risk_days"],
                "resource_savings_vs_farmer": scenario.get("resource_savings_vs_farmer", {}),
                "economic_summary": scenario["economic_summary"],
            }
            for scenario in scenarios
        ]
        farmer_baseline_summary = {
            **baseline_evaluation["totals"],
            "expected_yield_kg_ha": baseline_evaluation["expected_yield_kg_ha"],
            "high_risk_days": baseline_evaluation["high_risk_days"],
            "medium_risk_days": baseline_evaluation["medium_risk_days"],
            "economic_summary": baseline_evaluation["economic_summary"],
        }
        return prescription, comparison, farmer_baseline_summary, selected

    def _build_fertiwater_plan(
        self,
        payload: dict,
        water_response: dict,
        nutrition_result: dict | None,
    ) -> list[dict]:
        if not nutrition_result:
            return []
        fertigation_enabled = bool(payload.get("fertigation_enabled", self.fertigation_enabled))
        method = str(payload.get("irrigation_method") or "").lower()
        if not fertigation_enabled or method not in {"drip", "drip_under_mulch", "micro-sprinkler"}:
            return []

        daily = water_response.get("daily_stress_risk") or []
        daily_by_date = {str(item.get("Date")): item for item in daily}
        growth_stage = payload.get("growth_stage") or []
        remaining_deficit = self._remaining_deficit(nutrition_result)
        if max(remaining_deficit.values()) <= 0.1:
            return []

        ec_constraint = self._ec_constraint(payload)
        allocated: dict[tuple[str, str], float] = {}
        events = [
            deepcopy(item)
            for item in water_response.get("action_recommendations") or []
            if item.get("recommendationCode") == "IRRIGATE"
        ]

        plan = []
        efficiency = float(self.water_model.method_specs["efficiency"])
        phase_event_counts: dict[str, int] = {}
        for event in events:
            day = _as_date(event.get("recommendedIrrigationDate") or event.get("Date"))
            if day is None:
                continue
            phase, rank, next_phase_start = _phase_for_day(growth_stage, day)
            if rank < 13:
                continue
            recommended_depth = float(event.get("recommendedGrossDepthMm", 0.0) or 0.0)
            if recommended_depth <= 0.0:
                continue
            remaining_irrigation = self._remaining_phase_irrigation_mm(day, next_phase_start, daily, recommended_depth, efficiency)
            fert_ratio = min(1.0, recommended_depth / max(remaining_irrigation, 1e-6))
            phase_budget = self._phase_budget(remaining_deficit, phase, allocated)
            nutrient_plan = {}
            for nutrient, out_key in NUTRIENT_KEYS.items():
                amount = phase_budget[nutrient] * fert_ratio
                if nutrient == "N" and rank >= 81:
                    amount = 0.0
                amount *= float(ec_constraint["load_factor"])
                nutrient_plan[out_key] = round(max(0.0, amount), 3)
                allocated[(phase, nutrient)] = allocated.get((phase, nutrient), 0.0) + nutrient_plan[out_key]
            if max(nutrient_plan.values()) <= 0.05:
                continue
            total_npk = sum(float(value or 0.0) for value in nutrient_plan.values())
            if total_npk < MIN_FERTIWATER_NPK_KG_HA:
                continue
            if phase_event_counts.get(phase, 0) >= MAX_FERTIWATER_EVENTS_PER_PHASE:
                continue
            phase_event_counts[phase] = phase_event_counts.get(phase, 0) + 1
            water_record = daily_by_date.get(str(day), {})
            plan.append(
                {
                    "Date": str(day),
                    "recommendationCode": "COTTON_FERTIWATER",
                    "actionTypeCode": "IRRIGATION_FERTIGATION",
                    "action_domain": "FERTIWATER",
                    "treatmentWindowCode": phase.upper(),
                    "treatmentStartDate": str(day),
                    "treatmentEndDate": str(day),
                    "recommendedIrrigationDate": str(day),
                    "recommendedGrossDepthMm": round(recommended_depth, 3),
                    "irrigation_method": self.water_model.irrigation_method,
                    "irrigation_coupled": True,
                    "small_water_exception": bool(event.get("small_water_exception", False)),
                    "bbch": PHASE_RANKS[phase],
                    "bbch_phase": phase,
                    "fert_ratio": round(fert_ratio, 4),
                    "remaining_phase_irrigation_mm": remaining_irrigation,
                    "phase_remaining_nutrient_budget_kg_ha": {key: round(value, 3) for key, value in phase_budget.items()},
                    "nutrient_plan": nutrient_plan,
                    "product_plan": _product_plan(nutrient_plan),
                    "cost_effective": True,
                    "cost_effective_rule": {
                        "min_total_npk_kg_ha": MIN_FERTIWATER_NPK_KG_HA,
                        "max_events_per_phase": MAX_FERTIWATER_EVENTS_PER_PHASE,
                    },
                    "soil_ec_constraint": ec_constraint,
                    "root_zone_relative_available_water": water_record.get("root_zone_relative_available_water"),
                    "reason": [
                        event.get("reason"),
                        "fertigation is coupled to the irrigation depth for the current BBCH phase",
                        "event passes the minimum nutrient-load and per-phase frequency checks",
                        "N fertigation is stopped after BBCH81" if rank >= 81 else "N fertigation is allowed before BBCH81",
                    ],
                }
            )
        return plan

    @staticmethod
    def _apply_fertiwater_to_daily(daily_management_state: list[dict], fertiwater_plan: list[dict]) -> list[dict]:
        by_date = {str(item.get("Date")): item for item in daily_management_state}
        for event in fertiwater_plan:
            row = by_date.get(str(event.get("Date")))
            if not row:
                continue
            nutrient_plan = event.get("nutrient_plan") or {}
            row["fertigation_n_kg_ha"] = round(float(row.get("fertigation_n_kg_ha", 0.0) or 0.0) + float(nutrient_plan.get("n_kg_ha", 0.0) or 0.0), 3)
            row["fertigation_p2o5_kg_ha"] = round(float(row.get("fertigation_p2o5_kg_ha", 0.0) or 0.0) + float(nutrient_plan.get("p2o5_kg_ha", 0.0) or 0.0), 3)
            row["fertigation_k2o_kg_ha"] = round(float(row.get("fertigation_k2o_kg_ha", 0.0) or 0.0) + float(nutrient_plan.get("k2o_kg_ha", 0.0) or 0.0), 3)
            row["fertiwater_coupled"] = True
            row["fertiwater_fert_ratio"] = event.get("fert_ratio")
        return daily_management_state

    @staticmethod
    def _applied_effect_dates(payload: dict) -> dict[str, set[str]]:
        effects = {target: set() for target in ("DROUGHT", "N", "P", "K")}
        for action in _applied_water_fertilizer_actions(payload):
            day = _as_date(action.get("Date"))
            if day is None:
                continue
            for target in _action_effect_targets(action):
                water_mm = float(action.get("recommendedGrossDepthMm", 0.0) or 0.0)
                if target == "DROUGHT":
                    duration = max(4, min(10, int(round(water_mm / 10.0))))
                else:
                    duration = 7 if action.get("operation_code") == "FW" else 5
                for offset in range(duration):
                    effects[target].add(str(day + timedelta(days=offset)))
        return effects

    @staticmethod
    def _nutrition_profiles_by_date(nutrition_result: dict | None) -> dict[str, dict[str, dict[str, Any]]]:
        profiles = {target: {} for target in ("N", "P", "K")}
        if not nutrition_result:
            return profiles
        for source_target, public_target in PUBLIC_NUTRIENT_TARGETS.items():
            rows = sorted(
                (nutrition_result.get("daily_nutrition_risk") or {}).get(source_target) or [],
                key=lambda item: str(item.get("Date")),
            )
            latest: dict[str, Any] | None = None
            for row in rows:
                latest = row
                profiles[public_target][str(row.get("Date"))[:10]] = row
            if latest and not profiles[public_target]:
                profiles[public_target][str(latest.get("Date"))[:10]] = latest
        return profiles

    @staticmethod
    def _latest_profile(rows_by_date: dict[str, dict[str, Any]], day: str, previous: dict[str, Any] | None) -> dict[str, Any] | None:
        if day in rows_by_date:
            return rows_by_date[day]
        return previous

    @staticmethod
    def _normalized_management_blocks(
        daily_management_state: list[dict],
        nutrition_result: dict | None,
        payload: dict,
    ) -> tuple[dict, dict, list[dict]]:
        daily_by_target = {target: [] for target in ("DROUGHT", "N", "P", "K")}
        stress_by_target = {target: [] for target in ("DROUGHT", "N", "P", "K")}
        field = []
        persistent_status = {target: "LOW" for target in daily_by_target}
        lower_streak = {target: 0 for target in daily_by_target}
        effects_by_target = CottonManagementModel._applied_effect_dates(payload)
        nutrition_profiles = CottonManagementModel._nutrition_profiles_by_date(nutrition_result)
        latest_nutrition = {target: None for target in ("N", "P", "K")}
        for row in daily_management_state:
            day = str(row.get("Date"))
            target_daily: dict[str, str] = {"DROUGHT": _normalize_stress(row.get("stress_risk", "LOW"))}
            for target in ("N", "P", "K"):
                latest_nutrition[target] = CottonManagementModel._latest_profile(nutrition_profiles[target], day, latest_nutrition[target])
                profile = latest_nutrition[target] or {}
                target_daily[target] = _normalize_stress(
                    profile.get("categorized_shortterm_aggregate_stress")
                    or profile.get("categorized_daily_stress")
                    or "LOW"
                )

            field_levels = []
            for target in ("DROUGHT", "N", "P", "K"):
                raw_daily_status = target_daily[target]
                action_effect_active = day in effects_by_target[target]
                managed_daily_status = raw_daily_status
                if target == "DROUGHT":
                    if action_effect_active:
                        managed_daily_status = "IRRIGATED"
                        persistent_status[target] = "IRRIGATED"
                    else:
                        persistent_status[target] = raw_daily_status
                    lower_streak[target] = 0
                elif action_effect_active:
                    managed_daily_status = "FERTIGATED"
                    persistent_status[target] = "FERTIGATED"
                    lower_streak[target] = 0
                elif STRESS_ORDER.get(raw_daily_status, 0) > STRESS_ORDER.get(persistent_status[target], 0):
                    persistent_status[target] = raw_daily_status
                    lower_streak[target] = 0
                elif STRESS_ORDER.get(raw_daily_status, 0) < STRESS_ORDER.get(persistent_status[target], 0):
                    lower_streak[target] += 1
                    if lower_streak[target] >= 2:
                        persistent_status[target] = raw_daily_status
                        lower_streak[target] = 0
                else:
                    lower_streak[target] = 0

                public_row = {
                    "Date": day,
                    "target_code": target,
                    "stress_risk": raw_daily_status,
                    "raw_stress_risk": raw_daily_status,
                    "managed_stress_risk": persistent_status[target],
                    "categorized_daily_fertiwater_risk": raw_daily_status,
                    "managed_daily_fertiwater_risk": managed_daily_status,
                    "categorized_shortterm_aggregate_fertiwater_risk": persistent_status[target],
                    "action_effect_active": action_effect_active,
                }
                if target == "DROUGHT":
                    public_row.update(
                        {
                            "bbch_phase": row.get("Stage"),
                            "bbch": row.get("BBCH"),
                            "terminal_drydown": row.get("terminal_drydown"),
                            "days_to_boll_opening": row.get("days_to_boll_opening"),
                            "root_depth_mm": row.get("root_depth_mm"),
                            "root_length_index": round(float(row.get("root_depth_mm", 0.0) or 0.0) / 1050.0 * 100.0, 3),
                            "root_zone_relative_available_water": row.get("root_zone_relative_available_water"),
                            "storage_root_zone_relative_available_water": row.get("storage_root_zone_relative_available_water"),
                            "active_root_zone_relative_available_water": row.get("active_root_zone_relative_available_water"),
                            "irrigation_applied_mm": row.get("irrigation_applied_mm", 0.0),
                            "reference_et_mm": row.get("reference_et_mm"),
                            "crop_et_potential_mm": row.get("crop_et_potential_mm"),
                        }
                    )
                    row["raw_water_stress_risk"] = raw_daily_status
                    row["water_stress_risk"] = persistent_status[target]
                else:
                    profile = latest_nutrition[target] or {}
                    public_row.update(
                        {
                            "bbch_phase": profile.get("gstage") or row.get("Stage"),
                            "unit": profile.get("unit", "kg_ha"),
                            "demand": profile.get("demand"),
                            "soil_supply": profile.get("soil_supply"),
                            "fertilizer_release": profile.get("fertilizer_release"),
                            "available": profile.get("available"),
                            "stress_index": profile.get("stress_index"),
                        }
                    )
                    row[f"raw_{target.lower()}_stress_risk"] = raw_daily_status
                    row[f"{target.lower()}_stress_risk"] = persistent_status[target]
                daily_by_target[target].append(public_row)
                stress_by_target[target].append(
                    {
                        "Date": day,
                        "target_code": target,
                        "stress_risk": persistent_status[target],
                        "raw_stress_risk": raw_daily_status,
                        "action_effect_active": action_effect_active,
                    }
                )
                field_levels.append(persistent_status[target])
            row["categorized_shortterm_aggregate_fertiwater_risk"] = _worst_stress(field_levels)
            field.append({"Date": day, "field_risk": row["categorized_shortterm_aggregate_fertiwater_risk"]})
        return daily_by_target, stress_by_target, field

    def run(self, payload: dict) -> dict:
        baseline_events = _baseline_farmer_events(payload)
        if bool(payload.get("prescription_enabled", False)) and not baseline_events:
            raise ValueError("farmer baseline is required for water/fertilizer saving prescription")

        water_response = self.water_model.run(payload)
        management_mode = payload.get("management_mode", "water_only")
        nutrition_result = None
        if management_mode == "water_nutrition" or bool(payload.get("fertigation_enabled", self.fertigation_enabled)):
            nutrition_payload = self._build_nutrition_payload(payload, water_response)
            nutrition_result = self.nutrition_model.run(nutrition_payload)

        fertiwater_plan = self._build_fertiwater_plan(payload, water_response, nutrition_result)
        daily_management_state = self._combine_daily(water_response.get("daily_stress_risk") or [], nutrition_result)
        current_status = water_response.get("current_status") or {}
        current_yield = float((daily_management_state[-1] if daily_management_state else current_status).get("expected_yield_kg_ha", current_status.get("seed_cotton_yield_kg_ha", 0.0)) or 0.0)
        first_irrigation = next((item for item in water_response.get("action_recommendations", []) if item.get("recommendationCode") == "IRRIGATE"), None)
        water_gain = 0.0 if not first_irrigation else min(320.0, float(first_irrigation.get("recommendedGrossDepthMm", 0.0) or 0.0) * 5.2)
        nutrient_status = nutrition_result.get("nutrition_status") if nutrition_result else {"status": "disabled", "n_status": "disabled", "p_status": "disabled", "k_status": "disabled"}
        nutrient_gain = 0.0
        if nutrition_result and nutrient_status.get("status") == "deficient":
            nutrient_gain = min(420.0, current_yield * 0.07)
        elif nutrition_result and nutrient_status.get("status") == "watch":
            nutrient_gain = min(180.0, current_yield * 0.03)
        expected_with_recommendation = round(current_yield + water_gain + nutrient_gain, 3)
        seed_price = self._economic_value(payload, "seed_cotton_price_cny_per_kg")
        fertilizer_block = self._build_fertilizer_block(nutrition_result, fertiwater_plan)
        fertilizer_cost = float((fertilizer_block.get("estimated_cost_cny_ha") or 0.0))
        irrigation_cost = float(first_irrigation.get("expectedCostCnyHa", 0.0) if first_irrigation else 0.0)
        expected_cost_saving = max(0.0, (water_gain + nutrient_gain) * seed_price - fertilizer_cost - irrigation_cost)

        actions = _applied_water_fertilizer_actions(payload)
        actions.extend(self._irrigation_recommendation_actions(water_response))
        actions.extend(deepcopy(fertiwater_plan))
        prescription = None
        scenario_comparison = None
        farmer_baseline_summary = None
        selected_prescription_evaluation = None
        if bool(payload.get("prescription_enabled", False)):
            baseline_evaluation = self._evaluate_prescription_events(
                payload,
                baseline_events,
                scenario_name="farmer_practice",
                scenario_label="当地农户方案",
            )
            prescription, scenario_comparison, farmer_baseline_summary, selected_prescription_evaluation = self._build_prescription(
                payload,
                baseline_evaluation,
            )
        actions.sort(key=lambda x: (str(x.get("Date", "")), str(x.get("action_domain", ""))))
        daily_fertiwater_risk, stress_risk, field_risk = self._normalized_management_blocks(daily_management_state, nutrition_result, payload)
        if selected_prescription_evaluation is not None and prescription is not None:
            daily_management_state = selected_prescription_evaluation.get("daily_management_state") or daily_management_state
            daily_fertiwater_risk = selected_prescription_evaluation.get("daily_fertiwater_risk") or daily_fertiwater_risk
            stress_risk = selected_prescription_evaluation.get("stress_risk") or stress_risk
            field_risk = selected_prescription_evaluation.get("field_risk") or field_risk
            actions = deepcopy(prescription.get("actions") or [])
            actions.sort(key=lambda x: (str(x.get("Date", "")), str(x.get("action_domain", ""))))

        result = {
            "management_mode": management_mode,
            "daily_fertiwater_risk": daily_fertiwater_risk,
            "stress_risk": stress_risk,
            "field_risk": field_risk,
            "current_status": current_status,
            "yield_outlook": {
                "water_limited_yield_kg_ha": round(float(current_status.get("seed_cotton_yield_kg_ha", 0.0) or 0.0), 3),
                "water_nutrition_limited_yield_kg_ha": round(current_yield, 3),
                "expected_yield_with_recommendation_kg_ha": expected_with_recommendation,
                "expected_yield_recovery_kg_ha": round(expected_with_recommendation - current_yield, 3),
                "expected_cost_saving_cny_ha": round(expected_cost_saving, 3),
                "yield_basis": "seed_cotton_kg_ha",
            },
            "water_status": {
                "current_stress": current_status.get("stress_risk"),
                "root_zone_relative_available_water": current_status.get("root_zone_relative_available_water"),
                "field_status": water_response.get("field_status", []),
            },
            "nutrition_status": nutrient_status,
            "irrigation_recommendation": {
                "apply_now": bool(first_irrigation and first_irrigation.get("Date") == str(payload.get("decision_date"))),
                "recommendations": water_response.get("action_recommendations", []),
            },
            "fertilizer_recommendation": fertilizer_block,
            "action_recommendations": actions,
            "daily_management_state": daily_management_state,
            "assumptions": (water_response.get("defaults_used") or []) + (nutrition_result.get("assumptions", []) if nutrition_result else ["nutrition module disabled in water_only mode"]),
            "explanation": {
                "water": water_response.get("explanation"),
                "nutrition": nutrition_result.get("reason", []) if nutrition_result else ["management_mode is water_only"],
                "fertiwater": "fertilizer rates are coupled to irrigation depth by BBCH phase" if fertiwater_plan else "no coupled fertigation event met the water-trigger rules",
                "pest": "disease and insect endpoints remain separate; management output keeps agronomy actions focused on water and fertilizer.",
            },
        }
        if prescription is not None:
            result["prescription"] = prescription
            result["scenario_comparison"] = scenario_comparison
            result["farmer_baseline_summary"] = farmer_baseline_summary
        return result
