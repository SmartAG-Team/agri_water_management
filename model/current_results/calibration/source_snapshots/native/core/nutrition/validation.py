from __future__ import annotations

from datetime import date

import pandas as pd

from core.nutrition.fertilizer_inventory import normalize_fertilizer_inventory
from core.weather_processing import normalize_daily_weather


def _coerce_date(value) -> date | None:
    if value is None:
        return None
    return pd.to_datetime(value).date()


def _current_bbch_from_series(growth_stage: list[dict], decision_date: date) -> int | None:
    if not growth_stage:
        return None
    stage_to_bbch = {
        "sowing": 0,
        "emergence": 9,
        "seedling": 13,
        "squaring": 51,
        "flowering": 61,
        "boll_setting": 75,
        "boll_opening": 81,
        "maturity": 89,
        "播种期": 0,
        "出苗期": 9,
        "苗期": 13,
        "旺长期": 16,
        "现蕾期": 51,
        "初花期": 61,
        "盛花期": 65,
        "盛铃期": 75,
        "初吐絮期": 81,
        "吐絮期": 85,
        "完熟期": 89,
    }

    def _bbch(item: dict) -> int | None:
        raw = item.get("BBCH", item.get("bbch", item.get("StageCode")))
        if raw is not None:
            return int(float(raw))
        stage = item.get("Stage")
        if stage is None:
            return None
        text = str(stage).strip()
        if text.isdigit():
            return int(text)
        return stage_to_bbch.get(text, stage_to_bbch.get(text.lower()))

    staged = sorted(
        [
            {"Date": _coerce_date(item["Date"]), "BBCH": bbch}
            for item in growth_stage
            if item.get("Date") and (bbch := _bbch(item)) is not None
        ],
        key=lambda x: x["Date"],
    )
    valid = [item for item in staged if item["Date"] <= decision_date]
    return valid[-1]["BBCH"] if valid else staged[0]["BBCH"]


def _extract_layered_soil_water(payload: dict) -> list[dict]:
    if payload.get("layered_soil_water"):
        return list(payload["layered_soil_water"])
    current_status = payload.get("current_status") or {}
    if current_status.get("soil_water_by_layer"):
        return list(current_status["soil_water_by_layer"])
    layers = []
    for item in payload.get("soil_profile") or []:
        if item.get("soil_water_mm") is not None:
            soil_water = float(item["soil_water_mm"])
        elif item.get("initial_soil_water_mm") is not None:
            soil_water = float(item["initial_soil_water_mm"])
        else:
            thickness_mm = (float(item["depth_bottom_cm"]) - float(item["depth_top_cm"])) * 10.0
            taw_mm = max(0.0, (float(item["field_capacity"]) - float(item["wilting_point"])) * thickness_mm)
            if item.get("initial_relative_water") is None:
                raise ValueError("soil_profile layer requires soil_water_mm, initial_soil_water_mm, or initial_relative_water")
            rel = float(item["initial_relative_water"])
            soil_water = taw_mm * rel
            layers.append(
                {
                    "label": item.get("label") or f"{int(item['depth_top_cm'])}-{int(item['depth_bottom_cm'])}cm",
                    "depth_top_cm": item["depth_top_cm"],
                    "depth_bottom_cm": item["depth_bottom_cm"],
                    "soil_water_mm": round(soil_water, 3),
                    "relative_available_water": round(rel, 4),
                }
            )
            continue
        rel_value = item.get("relative_available_water", item.get("initial_relative_water"))
        if rel_value is None:
            raise ValueError("layered soil water requires relative_available_water or initial_relative_water")
        layers.append(
            {
                "label": item.get("label") or f"{int(item['depth_top_cm'])}-{int(item['depth_bottom_cm'])}cm",
                "depth_top_cm": item["depth_top_cm"],
                "depth_bottom_cm": item["depth_bottom_cm"],
                "soil_water_mm": round(soil_water, 3),
                "relative_available_water": round(float(rel_value), 4),
            }
        )
    return layers


def _normalize_fertilizer_history(payload: dict) -> list[dict]:
    raw = payload.get("fertilizer_history") or payload.get("applied_fertilizers") or []
    history = []
    for item in raw:
        nutrients = dict(item.get("nutrients_kg_ha") or {})
        n_pct = item.get("n_pct")
        p2o5_pct = item.get("p2o5_pct")
        k2o_pct = item.get("k2o_pct")
        amount = float(item.get("amount_kg_ha", 0.0))
        if "N" not in nutrients and n_pct is not None:
            nutrients["N"] = amount * float(n_pct) / 100.0
        if "P2O5" not in nutrients and p2o5_pct is not None:
            nutrients["P2O5"] = amount * float(p2o5_pct) / 100.0
        if "K2O" not in nutrients and k2o_pct is not None:
            nutrients["K2O"] = amount * float(k2o_pct) / 100.0
        history.append(
            {
                "date": _coerce_date(item.get("date") or item.get("Date")),
                "product_name": item.get("product_name") or item.get("product_key"),
                "amount_kg_ha": amount,
                "method": item.get("method", "soil"),
                "notes": item.get("notes"),
                "nutrients_kg_ha": nutrients,
                "release_type": item.get("release_type"),
                "release_days": item.get("release_days"),
            }
        )
    return [item for item in history if item["date"] is not None]


def normalize_nutrition_request(payload: dict) -> dict:
    assumptions = []
    decision_date = _coerce_date(payload.get("decision_date") or payload.get("Date"))
    if decision_date is None:
        raise ValueError("decision_date is required")

    target_yield = payload.get("target_yield_kg_ha")
    if target_yield is None:
        raise ValueError("target_yield_kg_ha is required")

    weather_history = normalize_daily_weather(payload.get("weather_history") or payload.get("weather_data") or payload.get("weather_daily") or [])
    weather_forecast = normalize_daily_weather(payload.get("weather_forecast") or payload.get("forecast_weather_data") or [])
    growth_stage = list(payload.get("growth_stage") or payload.get("phenology") or [])
    current_bbch = payload.get("current_bbch")
    if current_bbch is None:
        current_bbch = _current_bbch_from_series(growth_stage, decision_date)
    if current_bbch is None:
        raise ValueError("current_bbch or growth_stage is required")

    current_status = payload.get("current_status") or {}
    lai = payload.get("lai", current_status.get("lai"))
    biomass = payload.get("aboveground_biomass_kg_ha", current_status.get("total_biomass_kg_ha"))
    if biomass is None:
        raise ValueError("aboveground_biomass_kg_ha or current_status.total_biomass_kg_ha is required")

    layered_soil_water = _extract_layered_soil_water(payload)
    if not layered_soil_water:
        assumptions.append("layered_soil_water missing; no layer-specific adjustment applied")

    root_zone_raw = payload.get("root_zone_relative_available_water", current_status.get("root_zone_relative_available_water"))
    if root_zone_raw is None:
        raise ValueError("root_zone_relative_available_water or current_status.root_zone_relative_available_water is required")

    soil_test = dict(payload.get("soil_test") or {})
    if soil_test.get("organic_matter_g_kg") is None:
        raise ValueError("soil_test.organic_matter_g_kg is required")
    if soil_test.get("mineral_n_kg_ha") is None and soil_test.get("alkali_hydrolyzable_n_mg_kg") is None:
        raise ValueError("soil_test.mineral_n_kg_ha or soil_test.alkali_hydrolyzable_n_mg_kg is required")
    if soil_test.get("ph") is None:
        raise ValueError("soil_test.ph is required")

    irrigation_recommendation = payload.get("irrigation_recommendation") or payload.get("action_recommendations") or []
    irrigation = payload.get("irrigation") or {}
    economics = dict(payload.get("economic_parameters") or {})
    economics.update(dict(payload.get("economics") or {}))

    return {
        "crop_uuid": payload.get("crop_uuid"),
        "decision_date": decision_date,
        "planting_date": _coerce_date(payload.get("planting_date")),
        "latitude": payload.get("latitude"),
        "longitude": payload.get("longitude"),
        "target_yield_kg_ha": float(target_yield),
        "growth_stage": growth_stage,
        "current_bbch": int(current_bbch),
        "lai": float(lai) if lai is not None else None,
        "aboveground_biomass_kg_ha": float(biomass),
        "biomass_partition": dict(payload.get("biomass_partition") or {}),
        "layered_soil_water": layered_soil_water,
        "root_zone_relative_available_water": float(root_zone_raw),
        "weather_history": weather_history.to_dict(orient="records"),
        "weather_forecast": weather_forecast.to_dict(orient="records"),
        "soil_test": soil_test,
        "fertilizer_history": _normalize_fertilizer_history(payload),
        "irrigation": irrigation,
        "irrigation_recommendation": irrigation_recommendation,
        "economics": economics,
        "custom_products": list(payload.get("custom_products") or []),
        "fertilizer_inventory": normalize_fertilizer_inventory(payload.get("fertilizer_inventory") or []),
        "assumptions": assumptions,
    }
