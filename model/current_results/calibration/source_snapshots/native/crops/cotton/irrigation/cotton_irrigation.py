from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from typing import Any

import pandas as pd

from core.soil_water_balance import apply_daily_water_balance
from core.soil_hydraulics import validate_and_prepare_profile
from core.weather_processing import merge_historical_and_forecast, normalize_daily_weather, require_weather_value
from crops.cotton.config import DEFAULT_REGION_CODE, IRRIGATION_METHOD_SPECS, REGION_PARAMS


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


def _phase_params(stage: str) -> dict[str, float]:
    return {
        "sowing": {"kc": 0.22, "root_mm": 180.0, "threshold": 0.32, "target": 0.72, "yield_weight": 0.05},
        "emergence": {"kc": 0.30, "root_mm": 250.0, "threshold": 0.38, "target": 0.76, "yield_weight": 0.08},
        "seedling": {"kc": 0.48, "root_mm": 380.0, "threshold": 0.44, "target": 0.78, "yield_weight": 0.18},
        "squaring": {"kc": 0.82, "root_mm": 650.0, "threshold": 0.58, "target": 0.86, "yield_weight": 0.75},
        "flowering": {"kc": 1.14, "root_mm": 900.0, "threshold": 0.66, "target": 0.90, "yield_weight": 1.00},
        "boll_setting": {"kc": 1.05, "root_mm": 1050.0, "threshold": 0.63, "target": 0.88, "yield_weight": 0.95},
        "boll_opening": {"kc": 0.72, "root_mm": 900.0, "threshold": 0.45, "target": 0.72, "yield_weight": 0.35},
        "maturity": {"kc": 0.38, "root_mm": 700.0, "threshold": 0.30, "target": 0.62, "yield_weight": 0.08},
    }.get(stage, {"kc": 0.65, "root_mm": 600.0, "threshold": 0.50, "target": 0.78, "yield_weight": 0.45})


def _date_from_item(item: dict) -> date:
    if item.get("Date"):
        return pd.to_datetime(item["Date"]).date()
    return pd.to_datetime(item["DateTime"]).date()


def _reference_et_mm(row: dict) -> float:
    context = f"daily weather[{row.get('Date', '?')}]"
    tmean = require_weather_value(row, "temperature_2m_mean", context=context)
    radiation = require_weather_value(row, "shortwave_radiation_sum", context=context)
    wind = require_weather_value(row, "windspeed_10m_mean", context=context)
    rh = require_weather_value(row, "relative_humidity_2m_mean", context=context)
    temp_term = max(0.0, (tmean - 5.0) * 0.12)
    radiation_term = max(0.0, radiation * 0.145)
    wind_term = max(0.0, (wind - 1.5) * 0.16)
    humidity_term = max(-0.45, min(0.75, (55.0 - rh) * 0.018))
    return round(max(1.0, temp_term + radiation_term + wind_term + humidity_term), 3)


def _bbch_code(value: Any) -> int:
    if isinstance(value, dict):
        value = value.get("BBCH", value.get("bbch", value.get("StageCode", 0)))
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


class CottonIrrigationModel:
    def __init__(
        self,
        latitude: float,
        longitude: float,
        soil_type: str,
        irrigation_method: str,
        *,
        mulch_enabled: bool = True,
        region_code: str | None = None,
    ) -> None:
        self.latitude = latitude
        self.longitude = longitude
        self.soil_type = (soil_type or "sandy_loam").strip().lower().replace(" ", "_")
        if not soil_type:
            raise ValueError("soil_type is required")
        self.irrigation_method = "drip_under_mulch" if irrigation_method == "drip" and mulch_enabled else irrigation_method
        if self.irrigation_method not in IRRIGATION_METHOD_SPECS:
            raise ValueError(f"Unknown cotton irrigation_method: {irrigation_method}")
        self.method_specs = IRRIGATION_METHOD_SPECS[self.irrigation_method]
        self.mulch_enabled = bool(mulch_enabled)
        self.region_code = region_code or DEFAULT_REGION_CODE
        self.region = REGION_PARAMS.get(self.region_code, REGION_PARAMS[DEFAULT_REGION_CODE])

    def _economics(self, payload: dict) -> tuple[dict, list[dict]]:
        raw = {}
        raw.update(payload.get("economic_parameters") or {})
        raw.update(payload.get("economics") or {})
        if not raw:
            raise ValueError("economic_parameters is required")
        economics = {
            key: value if key == "product_prices" else float(value)
            for key, value in raw.items()
            if value is not None
        }
        required = [
            "seed_cotton_price_cny_per_kg",
            "water_cost_cny_per_mm_ha",
            "electricity_cost_cny_per_kwh",
            "pump_kwh_per_mm_ha",
            "irrigation_event_labor_cost_cny_ha",
        ]
        missing = [key for key in required if key not in economics]
        if missing:
            raise ValueError(f"economic_parameters missing required field(s): {', '.join(missing)}")
        return economics, []

    @staticmethod
    def _root_zone_capacity_mm(soil_profile: list[dict], root_depth_mm: float) -> float:
        remaining_cm = max(0.0, root_depth_mm / 10.0)
        capacity = 0.0
        for layer in soil_profile:
            top = float(layer["depth_top_cm"])
            bottom = float(layer["depth_bottom_cm"])
            thickness = max(0.0, min(bottom, remaining_cm) - top)
            if thickness <= 0:
                continue
            paw = max(0.02, float(layer["field_capacity"]) - float(layer["wilting_point"]))
            capacity += paw * thickness * 10.0
        return max(35.0, capacity)

    @staticmethod
    def _initial_relative_water(soil_profile: list[dict], root_depth_mm: float) -> float:
        weighted = 0.0
        total = 0.0
        limit_cm = root_depth_mm / 10.0
        for layer in soil_profile:
            top = float(layer["depth_top_cm"])
            bottom = float(layer["depth_bottom_cm"])
            thickness = max(0.0, min(bottom, limit_cm) - top)
            if thickness <= 0:
                continue
            raw = layer.get("initial_relative_water")
            if raw is None and layer.get("initial_soil_water_mm") is not None:
                capacity = max(1e-6, (float(layer["field_capacity"]) - float(layer["wilting_point"])) * thickness * 10.0)
                raw = float(layer["initial_soil_water_mm"]) / capacity
            if raw is None:
                raise ValueError("soil_profile layer requires initial_soil_water_mm or initial_relative_water")
            raw = float(raw)
            weighted += max(0.0, min(1.0, raw)) * thickness
            total += thickness
        if not total:
            raise ValueError("soil_profile does not overlap cotton root zone")
        return round(weighted / total, 4)

    @staticmethod
    def _root_zone_water_mm(soil_profile: list[dict], root_depth_mm: float) -> float:
        total = 0.0
        limit_cm = root_depth_mm / 10.0
        for layer in soil_profile:
            top = float(layer["depth_top_cm"])
            bottom = float(layer["depth_bottom_cm"])
            thickness = max(0.0, min(bottom, limit_cm) - top)
            if thickness <= 0:
                continue
            fraction = thickness / max(bottom - top, 1e-6)
            total += float(layer.get("soil_water_mm", 0.0) or 0.0) * fraction
        return round(total, 3)

    def _root_activity_weights(self, soil_profile: list[dict], root_depth_mm: float) -> list[float] | None:
        if self.irrigation_method != "drip_under_mulch":
            return None
        weights = []
        for layer in soil_profile:
            top_mm = float(layer["depth_top_cm"]) * 10.0
            bottom_mm = float(layer["depth_bottom_cm"]) * 10.0
            if root_depth_mm <= top_mm:
                rooted_fraction = 0.0
            elif root_depth_mm >= bottom_mm:
                rooted_fraction = 1.0
            else:
                rooted_fraction = max(0.0, min(1.0, (root_depth_mm - top_mm) / max(bottom_mm - top_mm, 1e-6)))
            mid_cm = (float(layer["depth_top_cm"]) + float(layer["depth_bottom_cm"])) / 2.0
            if mid_cm <= 20.0:
                activity = 0.36
            elif mid_cm <= 40.0:
                activity = 0.30
            elif mid_cm <= 80.0:
                activity = 0.22
            elif mid_cm <= 120.0:
                activity = 0.09
            else:
                activity = 0.03
            weights.append(activity * rooted_fraction)
        return weights

    def _stress(self, raw: float, threshold: float, stage: str, *, terminal_drydown: bool = False) -> str:
        if terminal_drydown:
            return "MEDIUM" if raw < threshold * 0.65 else "LOW"
        if stage == "maturity":
            return "LOW" if raw >= threshold * 0.45 else "MEDIUM"
        if stage == "boll_opening":
            return "MEDIUM" if raw < threshold * 0.65 else "LOW"
        high_margin = 0.24 if stage in {"flowering", "boll_setting"} else 0.20
        medium_ratio = 0.90 if stage in {"flowering", "boll_setting"} else 0.94
        if raw < threshold - high_margin:
            return "HIGH"
        if raw < threshold * medium_ratio:
            return "MEDIUM"
        if raw > 0.92:
            return "WATERLOGGING_WATCH"
        return "LOW"

    def _target_interval_days(self, stage: str) -> int:
        if self.irrigation_method == "drip_under_mulch":
            if stage in {"flowering", "boll_setting"}:
                return 5
            if stage == "squaring":
                return 6
            if stage in {"boll_opening", "maturity"}:
                return 10
            return 8
        base = int(self.method_specs["preferred_interval_days"])
        base += int(self.region.get("irrigation_interval_shift_days", 0))
        if stage in {"flowering", "boll_setting"}:
            base -= 1
        if stage in {"boll_opening", "maturity"}:
            base += 3
        return max(4, base)

    def _parse_irrigation_history(self, payload: dict) -> list[dict]:
        events = []
        for item in payload.get("irrigation_history") or []:
            dt = item.get("Date") or item.get("date") or item.get("recommendedIrrigationDate")
            if not dt:
                raise ValueError("irrigation_history event missing required date field")
            amount = next(
                (
                    item[key]
                    for key in ("amount_mm", "gross_depth_mm", "recommendedGrossDepthMm")
                    if key in item and item[key] is not None
                ),
                None,
            )
            if amount is None:
                raise ValueError("irrigation_history event missing required depth field")
            events.append({"Date": pd.to_datetime(dt).date(), "gross_depth_mm": float(amount)})
        return sorted(events, key=lambda x: x["Date"])

    def _simulate_daily(
        self,
        payload: dict,
        weather_df: pd.DataFrame,
        growth_stage_df: pd.DataFrame,
        soil_profile: list[dict],
        irrigation_events: list[dict],
        economics: dict,
    ) -> list[dict]:
        stage_by_date = {
            row["Date"]: row
            for row in growth_stage_df.to_dict(orient="records")
        }
        stage_rows = sorted(growth_stage_df.to_dict(orient="records"), key=lambda item: item["Date"])
        boll_opening_dates = [
            item["Date"]
            for item in stage_rows
            if _bbch_code(item) >= 81 or _stage_name(item) in {"boll_opening", "maturity"}
        ]
        first_boll_opening = min(boll_opening_dates) if boll_opening_dates else None

        def stage_for_day(day: date) -> dict:
            chosen = stage_rows[0]
            for item in stage_rows:
                if item["Date"] <= day:
                    chosen = item
                else:
                    break
            return stage_by_date.get(day, chosen)

        event_by_date: dict[date, float] = {}
        for event in irrigation_events:
            event_by_date[event["Date"]] = event_by_date.get(event["Date"], 0.0) + float(event["gross_depth_mm"])

        profile_state = deepcopy(soil_profile)
        weighted_stress = 0.0
        weight_sum = 0.0
        target_yield_raw = payload.get("target_yield_kg_ha")
        if target_yield_raw is None:
            raise ValueError("target_yield_kg_ha is required")
        target_yield = float(target_yield_raw)
        records = []

        for row in weather_df.to_dict(orient="records"):
            day = row["Date"]
            stage_record = stage_for_day(day)
            stage = _stage_name(stage_record)
            bbch = _bbch_code(stage_record)
            params = _phase_params(stage)
            root_depth = params["root_mm"]
            capacity = self._root_zone_capacity_mm(soil_profile, root_depth)
            soil_water = self._root_zone_water_mm(profile_state, root_depth)
            raw = max(0.0, min(1.2, soil_water / capacity))
            threshold = params["threshold"]
            if self.irrigation_method == "drip_under_mulch" and self.mulch_enabled:
                threshold *= 0.86
            target = max(threshold + 0.10, min(0.90, params["target"] + float(self.method_specs["target_refill_bonus"]) - 0.03))
            et0 = _reference_et_mm(row)
            evaporation_multiplier = float(self.method_specs["evaporation_multiplier"])
            kc = params["kc"]
            potential_et = et0 * kc
            soil_evaporation_share = 0.12 if self.irrigation_method == "drip_under_mulch" and self.mulch_enabled else 0.28
            soil_evaporation = potential_et * soil_evaporation_share * evaporation_multiplier
            transpiration = max(0.0, potential_et - soil_evaporation)
            water_factor = max(0.35, min(1.0, raw / max(threshold, 0.2)))
            actual_transpiration = transpiration * water_factor
            actual_et = soil_evaporation + actual_transpiration
            rain = require_weather_value(row, "precipitation_sum", context=f"daily weather[{day}]")
            effective_rain_factor = 0.78 if self.mulch_enabled else 0.70
            effective_rain = rain * effective_rain_factor
            irrigation = event_by_date.get(day, 0.0)
            water_balance = apply_daily_water_balance(
                profile_state,
                root_depth,
                precipitation_mm=effective_rain,
                irrigation_gross_mm=irrigation,
                potential_soil_evaporation_mm=soil_evaporation,
                potential_transpiration_mm=transpiration,
                irrigation_efficiency=float(self.method_specs["efficiency"]),
                root_activity_weights=self._root_activity_weights(profile_state, root_depth),
            )
            profile_state = water_balance["soil_profile"]
            soil_water = float(water_balance["root_zone_soil_water_mm"])
            raw_after = max(0.0, min(1.15, float(water_balance["root_zone_relative_available_water"])))
            actual_transpiration = float(water_balance["actual_transpiration_mm"])
            actual_et = float(water_balance["eta_actual_mm"])
            days_to_boll_opening = (
                (first_boll_opening - day).days
                if first_boll_opening is not None and day <= first_boll_opening
                else None
            )
            terminal_drydown = bbch >= 81 or (
                stage == "boll_setting"
                and bbch >= 75
                and days_to_boll_opening is not None
                and 0 <= days_to_boll_opening <= 20
            )
            stress = self._stress(raw_after, threshold, stage, terminal_drydown=terminal_drydown)
            heat_stress = 1.0
            if require_weather_value(row, "temperature_2m_max", context=f"daily weather[{day}]") >= float(self.region["heat_stress_threshold_c"]):
                heat_stress = 0.96 if raw_after >= threshold else 0.90
            growth_factor = max(0.20, min(1.0, water_factor * heat_stress))
            weighted_stress += (1.0 - growth_factor) * params["yield_weight"]
            weight_sum += params["yield_weight"]
            stress_penalty = min(0.38, weighted_stress / max(weight_sum, 1e-6) * 0.52)
            seed_cotton_yield = target_yield * (1.0 - stress_penalty)
            lint_yield = seed_cotton_yield * 0.40
            records.append(
                {
                    "Date": str(day),
                    "Stage": stage,
                    "BBCH": bbch,
                    "terminal_drydown": terminal_drydown,
                    "days_to_boll_opening": days_to_boll_opening,
                    "reference_et_mm": round(et0, 3),
                    "crop_et_potential_mm": round(potential_et, 3),
                    "soil_evaporation_mm": round(soil_evaporation, 3),
                    "mulch_enabled": self.mulch_enabled,
                    "mulch_evaporation_multiplier": round(evaporation_multiplier, 3),
                    "soil_evaporation_share": round(soil_evaporation_share, 3),
                    "mulch_effective_rain_factor": round(effective_rain_factor, 3),
                    "mulch_soil_temperature_bonus_c": 2.0 if self.mulch_enabled and stage in {"sowing", "emergence"} else (1.0 if self.mulch_enabled and stage == "seedling" else 0.0),
                    "actual_transpiration_mm": round(actual_transpiration, 3),
                    "eta_actual_mm": round(actual_et, 3),
                    "precipitation_mm": round(rain, 3),
                    "effective_precipitation_mm": round(effective_rain, 3),
                    "irrigation_applied_mm": round(irrigation, 3),
                    "runoff_mm": water_balance["runoff_mm"],
                    "drainage_mm": water_balance["drainage_mm"],
                    "deep_percolation_mm": water_balance["deep_percolation_mm"],
                    "soil_water_by_layer": water_balance["soil_water_by_layer"],
                    "root_depth_mm": round(root_depth, 3),
                    "root_zone_soil_water_mm": round(soil_water, 3),
                    "root_zone_capacity_mm": round(capacity, 3),
                    "root_zone_relative_available_water": round(raw_after, 4),
                    "storage_root_zone_relative_available_water": water_balance.get("storage_root_zone_relative_available_water"),
                    "active_root_zone_relative_available_water": water_balance.get("active_root_zone_relative_available_water"),
                    "stress_threshold": round(threshold, 3),
                    "target_relative_available_water": round(target, 3),
                    "stress_risk": stress,
                    "water_growth_factor": round(growth_factor, 4),
                    "seed_cotton_yield_kg_ha": round(seed_cotton_yield, 3),
                    "lint_yield_kg_ha": round(lint_yield, 3),
                }
            )
        return records

    def _build_recommendations(self, payload: dict, daily_records: list[dict], applied_events: list[dict], economics: dict) -> list[dict]:
        decision_date = pd.to_datetime(payload["decision_date"]).date()
        future = [r for r in daily_records if pd.to_datetime(r["Date"]).date() >= decision_date]
        last_event_date = max((e["Date"] for e in applied_events if e["Date"] <= decision_date), default=None)
        recommendations = []
        max_recommendations = int(payload.get("max_irrigation_recommendations", 4) or 4)
        high_run_start_by_date: dict[date, date] = {}
        active_high_start: date | None = None
        for record in future:
            day = pd.to_datetime(record["Date"]).date()
            risk = str(record.get("stress_risk", "LOW")).upper()
            if risk == "HIGH":
                if active_high_start is None:
                    active_high_start = day
                high_run_start_by_date[day] = active_high_start
            else:
                active_high_start = None
        for record in future:
            day = pd.to_datetime(record["Date"]).date()
            stage = record["Stage"]
            risk = str(record.get("stress_risk", "LOW")).upper()
            triggered = risk == "HIGH"
            current_high_run_start = high_run_start_by_date.get(day)
            if current_high_run_start is not None and (day - current_high_run_start).days > 1:
                triggered = True
            trigger_high_run_start = high_run_start_by_date.get(day) if risk == "HIGH" else None
            interval = self._target_interval_days(stage)
            gap = 999 if last_event_date is None else (day - last_event_date).days
            required_gap = max(4, interval - 2)
            if triggered and gap >= required_gap:
                deficit_record = record
                root_capacity = float(
                    deficit_record.get("root_zone_capacity_mm")
                    or (float(deficit_record["root_zone_soil_water_mm"]) / max(float(deficit_record["root_zone_relative_available_water"]), 0.05))
                )
                target = float(deficit_record["target_relative_available_water"])
                deficit = max(0.0, (target - float(deficit_record["root_zone_relative_available_water"])) * root_capacity)
                gross = deficit / max(float(self.method_specs["efficiency"]), 0.5)
                gross = max(float(self.method_specs["min_event_mm"]), min(float(self.method_specs["max_event_mm"]), gross))
                operating_cost = gross * (
                    economics["water_cost_cny_per_mm_ha"]
                    + economics["electricity_cost_cny_per_kwh"]
                    * economics["pump_kwh_per_mm_ha"]
                ) + economics["irrigation_event_labor_cost_cny_ha"]
                reason = (
                    f"Cotton {stage} water status is {risk} "
                    f"with root-zone available water {record['root_zone_relative_available_water']:.2f}."
                )
                recommendations.append(
                    {
                        "Date": str(day),
                        "recommendationCode": "IRRIGATE",
                        "actionTypeCode": "IRRIGATION",
                        "treatmentWindowCode": stage.upper(),
                        "recommendedIrrigationDate": str(day),
                        "recommendedGrossDepthMm": round(gross, 3),
                        "irrigation_method": self.irrigation_method,
                        "application_window_days": 2 if self.irrigation_method != "flood" else 4,
                        "expectedCostCnyHa": round(operating_cost, 3),
                        "trigger_stress_risk": risk,
                        "forecast_high_risk_run_start": str(trigger_high_run_start) if trigger_high_run_start is not None else None,
                        "minimum_interval_days": interval,
                        "actual_gap_since_previous_water_days": gap if gap != 999 else None,
                        "reason": reason,
                    }
                )
                if len(recommendations) >= max_recommendations:
                    break
                last_event_date = day
        return recommendations

    def _apply_irrigation_replay_status(self, daily_records: list[dict], applied_events: list[dict]) -> list[dict]:
        if not applied_events:
            return daily_records
        event_dates = sorted(event["Date"] for event in applied_events)
        adjusted: list[dict] = []
        for record in daily_records:
            day = pd.to_datetime(record["Date"]).date()
            stage = str(record.get("Stage") or "")
            coverage_days = max(7, self._target_interval_days(stage) + 3)
            active_event = next(
                (
                    event_day
                    for event_day in reversed(event_dates)
                    if event_day <= day and (day - event_day).days <= coverage_days
                ),
                None,
            )
            if active_event is None:
                adjusted.append(record)
                continue
            row = dict(record)
            row["raw_stress_risk"] = row.get("stress_risk")
            row["stress_risk"] = "IRRIGATED"
            row["irrigation_replay_event_date"] = active_event.isoformat()
            row["irrigation_replay_effect_days"] = coverage_days
            adjusted.append(row)
        return adjusted

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        economics, defaults_used = self._economics(payload)
        weather_history = normalize_daily_weather(payload.get("weather_data") or [], allow_empty=True)
        weather_forecast = normalize_daily_weather(payload.get("forecast_weather_data") or [], allow_empty=True)
        weather_df = merge_historical_and_forecast(weather_history, weather_forecast)
        if weather_df.empty:
            raise ValueError("weather_data cannot be empty")
        growth_stage_df = pd.DataFrame(payload.get("growth_stage") or [])
        if growth_stage_df.empty:
            raise ValueError("growth_stage is required for cotton irrigation")
        growth_stage_df["Date"] = pd.to_datetime(growth_stage_df["Date"]).dt.date
        season_end = growth_stage_df["Date"].max()
        if season_end and weather_df["Date"].max() < season_end:
            raise ValueError("weather_data must cover the season through the final growth stage date")
        weather_df = weather_df.loc[weather_df["Date"] <= season_end].copy()
        if weather_df.empty:
            raise ValueError("weather_data does not overlap growth_stage")

        soil_profile, soil_adjustments, soil_defaults = validate_and_prepare_profile(self.soil_type, payload["soil_profile"])
        applied_events = self._parse_irrigation_history(payload)
        raw_daily = self._simulate_daily(payload, weather_df, growth_stage_df, soil_profile, applied_events, economics)
        daily = self._apply_irrigation_replay_status(raw_daily, applied_events)
        actions = self._build_recommendations(payload, daily, applied_events, economics)
        decision_date = str(payload["decision_date"])
        current = next((r for r in daily if r["Date"] == decision_date), daily[-1])
        field_status = [
            {
                "Date": r["Date"],
                "field_status": r["stress_risk"],
                "root_zone_status": r["stress_risk"],
                "forecast_risk": "WATCH" if r["stress_risk"] in {"MEDIUM", "HIGH"} else "STABLE",
            }
            for r in daily
        ]
        no_action_yield = float(current.get("seed_cotton_yield_kg_ha", 0.0) or 0.0)
        best_depth = float(actions[0]["recommendedGrossDepthMm"]) if actions else 0.0
        expected_gain = 0.0 if not actions else min(320.0, best_depth * 5.2)
        scenario_comparison = [
            {
                "scenario_name": "current_schedule",
                "summary": {"expected_yield_kg_ha": round(no_action_yield, 3), "irrigation_mm": 0.0},
                "economics": {"expected_net_return_cny_ha": round(no_action_yield * economics["seed_cotton_price_cny_per_kg"], 3)},
            },
            {
                "scenario_name": "recommended_cotton_drip",
                "summary": {"expected_yield_kg_ha": round(no_action_yield + expected_gain, 3), "irrigation_mm": round(best_depth, 3)},
                "economics": {
                    "expected_net_return_cny_ha": round((no_action_yield + expected_gain) * economics["seed_cotton_price_cny_per_kg"], 3)
                },
            },
        ]
        return {
            "current_status": current,
            "daily_stress_risk": daily,
            "field_status": field_status,
            "action_recommendations": actions,
            "scenario_comparison": scenario_comparison,
            "defaults_used": defaults_used + soil_defaults,
            "input_adjustments": soil_adjustments,
            "explanation": {
                "summary": actions[0]["reason"] if actions else "Cotton root-zone water is within the current trigger band.",
                "irrigation_method": self.irrigation_method,
                "mulch_effect": "mulch lowers soil evaporation and improves effective rainfall storage" if self.mulch_enabled else "mulch disabled",
                "regional_basis": self.region_code,
            },
        }
