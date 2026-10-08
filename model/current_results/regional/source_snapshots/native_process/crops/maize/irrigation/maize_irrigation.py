from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from typing import Any

import pandas as pd

from core.drought_stress import apply_forecast_aggregated_drought_status, diagnose_stress
from core.economic_optimization import irrigation_activity_cost_breakdown, score_scenario
from core.evapotranspiration import crop_et_components, reference_et_mm
from core.irrigation_engine import generate_irrigation_scenarios
from core.scenario_simulation import rank_scenarios
from core.soil_hydraulics import SOIL_DEFAULTS, validate_and_prepare_profile
from core.soil_water_balance import apply_daily_water_balance
from core.weather_processing import merge_historical_and_forecast, normalize_daily_weather, require_weather_value
from crops.maize.water_config import IRRIGATION_METHOD_SPECS, SOIL_REALISM

from .bbch_mapping import (
    get_phase,
    get_phase_parameters,
    get_process_phase_progress,
    interpolate_process_parameters,
    normalize_iowa_stage,
    stage_between,
    stage_progress,
    stage_rank,
    stage_to_bbch,
)
from .crop_coefficients import canopy_cover_from_lai, kc_for_bbch
from .growth import extract_public_growth_state, initialize_growth_state, update_growth_state
from .rooting import advance_root_depth, initial_root_depth_mm, layer_root_activity


class MaizeIrrigationModel:
    RAIN_VETO_MM = 15.0
    RAIN_COVER_RATIO = 0.80
    MAX_TREATMENT_WINDOW_DAYS = 3

    def __init__(self, latitude: float, longitude: float, soil_type: str, irrigation_method: str):
        self.latitude = latitude
        self.longitude = longitude
        self.soil_type = (soil_type or "sandy_loam").strip().lower().replace(" ", "_")
        if self.soil_type not in SOIL_DEFAULTS:
            raise ValueError(f"Unknown soil_type: {soil_type}")
        self.irrigation_method = irrigation_method
        if irrigation_method not in IRRIGATION_METHOD_SPECS:
            raise ValueError(f"Unknown irrigation_method: {irrigation_method}")
        self.method_specs = IRRIGATION_METHOD_SPECS[self.irrigation_method]

    def _target_intensity(self, target_yield_kg_ha: float) -> float:
        return max(0.0, min(1.0, (float(target_yield_kg_ha) - 8500.0) / 2500.0))

    def _phase_trigger_threshold(self, phase: str, target_yield_kg_ha: float) -> float:
        base = {
            "establishment": 0.28,
            "early_vegetative": 0.34,
            "rapid_vegetative": 0.52,
            "pre_tassel": 0.62,
            "tasseling_silking": 0.68,
            "kernel_development": 0.58,
            "grain_filling": 0.58,
            "maturity": 0.32,
        }.get(phase, 0.42)
        intensity = self._target_intensity(target_yield_kg_ha)
        return max(0.22, min(0.80, base + self.method_specs["stress_threshold_shift"] + 0.08 * intensity))

    def _phase_target_raw(self, phase: str, target_yield_kg_ha: float) -> float:
        base = {
            "rapid_vegetative": 0.84,
            "pre_tassel": 0.88,
            "tasseling_silking": 0.92,
            "kernel_development": 0.86,
            "grain_filling": 0.86,
        }.get(phase, 0.74)
        intensity = self._target_intensity(target_yield_kg_ha)
        return max(0.62, min(0.96, base + self.method_specs["target_refill_bonus"] + 0.05 * intensity))

    def _method_interval_days(self, phase: str, target_yield_kg_ha: float) -> int:
        preferred = int(self.method_specs["preferred_interval_days"])
        preferred -= int(round(3.0 * self._target_intensity(target_yield_kg_ha)))
        if phase in {"pre_tassel", "tasseling_silking"}:
            preferred = max(6, preferred - 2)
        if phase == "grain_filling":
            preferred = max(7, preferred - 1)
        preferred += int(SOIL_REALISM[self.soil_type].get("interval_adjust_days", 0))
        return max(6, preferred)

    def _remaining_interval_slots(
        self,
        weather_df: pd.DataFrame,
        start_idx: int,
        gs_map: dict,
        target_yield_kg_ha: float,
    ) -> int:
        if start_idx >= len(weather_df):
            return 0
        current_date = weather_df.iloc[start_idx]["Date"]
        slots = 0
        next_slot_date = current_date
        for idx in range(start_idx + 1, len(weather_df)):
            date = weather_df.iloc[idx]["Date"]
            stage = gs_map.get(date, "VS")
            if not self._is_water_sensitive_stage(stage):
                continue
            interval_days = self._method_interval_days(get_phase(stage), target_yield_kg_ha)
            if date >= next_slot_date + timedelta(days=interval_days):
                slots += 1
                next_slot_date = date
        return slots

    def _method_max_depth_mm(self) -> float:
        return float(self.method_specs["max_depth_mm"]) + float(SOIL_REALISM[self.soil_type].get("max_depth_bonus_mm", 0.0))

    @staticmethod
    def _is_water_sensitive_stage(stage: int | str) -> bool:
        return stage_between(stage, "VS", "R5")

    @staticmethod
    def _simulated_yield_kg_ha(daily_records: list[dict]) -> float:
        return round(float((daily_records[-1] if daily_records else {}).get("grain_weight_kg_ha", 0.0) or 0.0), 3)

    def _irrigation_trigger(self, stage: int | str, stress_risk: str, raw: float, days_since_last: int, potential_transpiration_mm: float, et0: float, target_yield_kg_ha: float) -> bool:
        phase = get_phase(stage)
        if normalize_iowa_stage(stage) == "R6":
            return False
        if potential_transpiration_mm < 1.2 and et0 < 2.8:
            return False
        threshold = self._phase_trigger_threshold(phase, target_yield_kg_ha)
        preferred_interval = self._method_interval_days(phase, target_yield_kg_ha)
        if days_since_last < preferred_interval:
            urgent_interval = max(6, int(round(preferred_interval * 0.65)))
            urgent_depletion = stress_risk == "HIGH" and raw < max(0.0, threshold - 0.12)
            if days_since_last < urgent_interval or not urgent_depletion:
                return False
        return stress_risk == "HIGH" or raw < threshold or (stress_risk == "MEDIUM" and raw < threshold + 0.04)

    @staticmethod
    def _normalized_partition(stage: int | str) -> dict[str, float]:
        partition = dict(interpolate_process_parameters(stage)["partition"])
        total = sum(max(0.0, float(value)) for value in partition.values()) or 1.0
        return {key: max(0.0, float(value)) / total for key, value in partition.items()}

    def _phenology_water_response(
        self,
        stage: int | str,
        growth_state: dict[str, Any],
        potential_transpiration_mm: float,
    ) -> dict[str, float]:
        stage = normalize_iowa_stage(stage)
        phase_params = get_phase_parameters(stage)
        process_params = interpolate_process_parameters(stage)
        partition = self._normalized_partition(stage)

        kc_activity = max(0.0, min(1.0, float(phase_params["kc"]) / 1.20))
        transpiration_activity = max(0.0, min(1.0, float(potential_transpiration_mm or 0.0) / 4.5))
        water_demand_weight = max(kc_activity * 0.55, kc_activity * transpiration_activity)

        establishment_activity = (
            max(0.0, 1.0 - get_process_phase_progress(stage))
            if stage_between(stage, "VS", "VE")
            else 0.0
        )
        canopy_building_activity = 0.0
        if stage_rank(stage) < stage_rank("VT"):
            vegetative_partition = partition.get("leaf", 0.0) + partition.get("stem", 0.0) + partition.get("root", 0.0)
            canopy_building_activity = vegetative_partition * max(0.0, 1.0 - stage_progress(stage) / 0.80) * 0.42

        direct_grain_activity = partition.get("grain", 0.0)
        reproductive_span = max(stage_rank("R5") - stage_rank("VT"), 1)
        reproductive_remaining = max(0.0, min(1.0, (stage_rank("R5") - stage_rank(stage)) / reproductive_span))
        remobilization_activity = float(process_params["dm_remobilization_fraction"]) * reproductive_remaining
        grain_sink_activity = max(0.0, min(1.0, direct_grain_activity + 0.35 * remobilization_activity))

        stress_weight = max(0.0, float(phase_params["stress_weight"]) / 1.50)
        yield_sensitivity = (
            0.48 * establishment_activity
            + canopy_building_activity
            + grain_sink_activity
        ) * stress_weight

        lai = float(growth_state.get("lai", 0.0) or 0.0)
        if stage_rank(stage) >= stage_rank("VT"):
            green_canopy_factor = max(0.0, min(1.0, lai / 3.0))
            yield_sensitivity *= 0.55 + 0.45 * green_canopy_factor

        return {
            "water_demand_weight": round(max(0.0, min(1.0, water_demand_weight)), 6),
            "yield_sensitivity": round(max(0.0, min(1.25, yield_sensitivity)), 6),
            "grain_sink_activity": round(grain_sink_activity, 6),
            "establishment_activity": round(establishment_activity, 6),
        }

    def _estimate_marginal_irrigation_yield_gain(
        self,
        *,
        stage: int | str,
        growth_state: dict[str, Any],
        raw: float,
        target_raw: float,
        refill_deficit_mm: float,
        recommended_depth_mm: float,
        potential_transpiration_mm: float,
    ) -> dict[str, float]:
        response = self._phenology_water_response(stage, growth_state, potential_transpiration_mm)
        if response["yield_sensitivity"] <= 0.0 or response["water_demand_weight"] <= 0.0:
            return {**response, "expected_yield_gain_kg_ha": 0.0}

        depletion_signal = max(0.0, float(target_raw) - float(raw)) / max(float(target_raw), 1e-6)
        if depletion_signal <= 0.0:
            return {**response, "expected_yield_gain_kg_ha": 0.0}

        net_depth_mm = max(0.0, float(recommended_depth_mm) * float(self.method_specs["efficiency"]))
        refill_ratio = min(1.0, net_depth_mm / max(float(refill_deficit_mm), 1.0))
        gain_fraction = (
            0.24
            * depletion_signal
            * refill_ratio
            * response["water_demand_weight"]
            * response["yield_sensitivity"]
        )
        source_capacity = max(
            0.0,
            float(growth_state.get("live_aboveground_biomass_kg_ha", growth_state.get("total_biomass_kg_ha", 0.0)) or 0.0)
            * 0.52
            - float(growth_state.get("grain_weight_kg_ha", 0.0) or 0.0),
        )
        return {
            **response,
            "expected_yield_gain_kg_ha": round(source_capacity * gain_fraction, 3),
        }

    def _simulated_event_yield_gain(
        self,
        *,
        weather_df: pd.DataFrame,
        growth_stage_df: pd.DataFrame,
        soil_profile: list[dict],
        target_yield_kg_ha: float,
        economics: dict,
        baseline_events: list[dict],
        candidate_event: dict,
        soil_test: dict | None,
        fertilizer_history: list[dict] | None,
        custom_products: list[dict] | None,
    ) -> float:
        baseline = self._simulate(
            weather_df=weather_df,
            growth_stage_df=growth_stage_df,
            soil_profile=soil_profile,
            target_yield_kg_ha=target_yield_kg_ha,
            economics=economics,
            scenario_events=baseline_events,
            soil_test=soil_test,
            fertilizer_history=fertilizer_history,
            custom_products=custom_products,
        )
        candidate = self._simulate(
            weather_df=weather_df,
            growth_stage_df=growth_stage_df,
            soil_profile=soil_profile,
            target_yield_kg_ha=target_yield_kg_ha,
            economics=economics,
            scenario_events=[*baseline_events, candidate_event],
            soil_test=soil_test,
            fertilizer_history=fertilizer_history,
            custom_products=custom_products,
        )
        return round(
            max(
                0.0,
                self._simulated_yield_kg_ha(candidate["daily_records"])
                - self._simulated_yield_kg_ha(baseline["daily_records"]),
            ),
            3,
        )

    def _minimum_economic_yield_gain_kg_ha(self, economics: dict[str, Any], recommended_depth_mm: float) -> float:
        costs = irrigation_activity_cost_breakdown(
            total_irrigation_mm=float(recommended_depth_mm),
            event_count=1,
            electricity_cost_cny_per_kwh=float(economics["electricity_cost_cny_per_kwh"]),
            pump_kwh_per_mm_ha=float(economics["pump_kwh_per_mm_ha"]),
            irrigation_event_labor_cost_cny_ha=float(economics["irrigation_event_labor_cost_cny_ha"]),
            water_cost_cny_per_mm_ha=float(economics.get("water_cost_cny_per_mm_ha", 0.0) or 0.0),
        )
        grain_price = max(float(economics["grain_price_cny_per_kg"]), 1e-6)
        return round(costs["irrigation_operating_cost_cny_ha"] / grain_price, 3)

    @staticmethod
    def _estimate_refill_deficit_mm(raw: float, target_raw: float, root_zone_soil_water_mm: float) -> float:
        if target_raw <= raw:
            return 0.0
        safe_raw = max(float(raw), 0.05)
        root_zone_capacity_mm = max(float(root_zone_soil_water_mm), 0.0) / safe_raw
        return round(max(0.0, target_raw - raw) * root_zone_capacity_mm, 3)

    @staticmethod
    def _forecast_rain_mm(weather_df: pd.DataFrame, start_idx: int, days: int = 3) -> float:
        end_idx = min(len(weather_df), start_idx + max(days, 0))
        if start_idx >= end_idx:
            return 0.0
        return round(
            sum(require_weather_value(weather_df.iloc[idx], "precipitation_sum", context=f"daily weather[{weather_df.iloc[idx]['Date']}]") for idx in range(start_idx, end_idx)),
            3,
        )

    def _select_irrigation_window(
        self,
        weather_df: pd.DataFrame,
        start_idx: int,
        raw: float,
        target_raw: float,
        threshold: float,
        stress_risk: str,
        root_zone_soil_water_mm: float,
    ) -> dict[str, Any] | None:
        refill_deficit_mm = self._estimate_refill_deficit_mm(raw, target_raw, root_zone_soil_water_mm)
        search_end = min(len(weather_df), start_idx + self.MAX_TREATMENT_WINDOW_DAYS)
        blocked_today = False
        postponed_today = False
        candidate_idx = None

        for idx in range(start_idx, search_end):
            same_day_rain = require_weather_value(weather_df.iloc[idx], "precipitation_sum", context=f"daily weather[{weather_df.iloc[idx]['Date']}]")
            if same_day_rain >= self.RAIN_VETO_MM:
                if idx == start_idx:
                    blocked_today = True
                continue
            if refill_deficit_mm > 0 and self._forecast_rain_mm(weather_df, idx, 3) >= refill_deficit_mm * self.RAIN_COVER_RATIO:
                if idx == start_idx:
                    postponed_today = True
                continue
            candidate_idx = idx
            break

        if candidate_idx is None:
            return None

        urgent_window = stress_risk == "HIGH" and raw < max(0.0, threshold - 0.05)
        max_end = min(len(weather_df), candidate_idx + (1 if urgent_window else self.MAX_TREATMENT_WINDOW_DAYS))
        end_idx = candidate_idx
        for idx in range(candidate_idx + 1, max_end):
            same_day_rain = require_weather_value(weather_df.iloc[idx], "precipitation_sum", context=f"daily weather[{weather_df.iloc[idx]['Date']}]")
            if same_day_rain >= self.RAIN_VETO_MM:
                break
            if refill_deficit_mm > 0 and self._forecast_rain_mm(weather_df, idx, 3) >= refill_deficit_mm * self.RAIN_COVER_RATIO:
                break
            end_idx = idx

        start_date = weather_df.iloc[candidate_idx]["Date"]
        end_date = weather_df.iloc[end_idx]["Date"]
        reasons = []
        if blocked_today:
            _date = weather_df.iloc[start_idx]['Date']
            reasons.append(
                f"same-day rainfall forecast {require_weather_value(weather_df.iloc[start_idx], 'precipitation_sum', context=f'daily weather[{_date}]'):.1f} mm hit the {self.RAIN_VETO_MM:.0f} mm irrigation veto"
            )
        elif postponed_today and candidate_idx > start_idx:
            reasons.append("near-term rainfall is expected to cover most of the refill deficit, so irrigation is postponed")
        elif candidate_idx > start_idx:
            reasons.append("earliest eligible irrigation day is shifted within the treatment window")

        return {
            "recommended_date": start_date,
            "treatment_start_date": start_date,
            "treatment_end_date": end_date,
            "refill_deficit_mm": refill_deficit_mm,
            "policy_reasons": reasons,
        }

    def _seasonal_soil_evaporation_factor(self, phase: str) -> float:
        return {
            "establishment": 0.70,
            "early_vegetative": 0.72,
            "rapid_vegetative": 0.76,
            "pre_tassel": 0.82,
            "tasseling_silking": 0.86,
            "kernel_development": 0.80,
            "grain_filling": 0.76,
            "maturity": 0.55,
        }.get(phase, 0.70)

    def _simulate(self, weather_df: pd.DataFrame, growth_stage_df: pd.DataFrame, soil_profile: list[dict], target_yield_kg_ha: float, economics: dict, scenario_events: list[dict] | None = None, soil_test: dict | None = None, fertilizer_history: list[dict] | None = None, custom_products: list[dict] | None = None) -> dict:
        gs_map = {
            pd.to_datetime(item["Date"]).date(): normalize_iowa_stage(item["Stage"])
            for item in growth_stage_df.to_dict(orient="records")
        }
        season_start = min(gs_map) if gs_map else None
        soil_state = deepcopy(soil_profile)
        daily_records = []
        growth_state = initialize_growth_state(
            soil_test=soil_test,
            fertilizer_history=fertilizer_history,
            custom_products=custom_products,
            nutrition_enabled=soil_test is not None or bool(fertilizer_history),
            soil_profile=soil_profile,
        )
        events_by_date = {event["Date"]: event["gross_depth_mm"] for event in (scenario_events or [])}
        for _, row in weather_df.iterrows():
            current_date = row["Date"]
            stage = gs_map.get(current_date, "VS")
            in_water_season = current_date >= season_start
            tmean = float(row["temperature_2m_mean"])
            prior_root_depth = float(growth_state.get("_root_depth_mm", initial_root_depth_mm()))
            root_snapshot = apply_daily_water_balance(soil_profile=soil_state, root_depth_mm=prior_root_depth, precipitation_mm=0.0, irrigation_gross_mm=0.0, potential_soil_evaporation_mm=0.0, potential_transpiration_mm=0.0, irrigation_efficiency=self.method_specs["efficiency"])
            root_depth_mm = advance_root_depth(prior_root_depth, stage, tmean, root_snapshot["root_zone_relative_available_water"])
            root_weights = layer_root_activity(soil_state, root_depth_mm)
            et0 = reference_et_mm(
                current_date,
                self.latitude,
                tmean,
                require_weather_value(row, "temperature_2m_min", context=f"daily weather[{current_date}]"),
                require_weather_value(row, "temperature_2m_max", context=f"daily weather[{current_date}]"),
                require_weather_value(row, "shortwave_radiation_sum", context=f"daily weather[{current_date}]"),
            )
            components = crop_et_components(et0, kc_for_bbch(stage), canopy_cover_from_lai(growth_state["lai"]), None)
            phase = get_phase(stage)
            potential_soil_evap = round(components["soil_evaporation_mm"] * self.method_specs["evaporation_multiplier"] * self._seasonal_soil_evaporation_factor(phase), 3)
            irrigation_mm = float(events_by_date.get(current_date, 0.0))
            precipitation_mm = require_weather_value(row, "precipitation_sum", context=f"daily weather[{current_date}]")
            wb = apply_daily_water_balance(
                soil_profile=soil_state,
                root_depth_mm=root_depth_mm,
                precipitation_mm=precipitation_mm,
                irrigation_gross_mm=irrigation_mm,
                potential_soil_evaporation_mm=potential_soil_evap,
                potential_transpiration_mm=components["potential_transpiration_mm"],
                irrigation_efficiency=self.method_specs["efficiency"],
                root_activity_weights=root_weights,
            )
            soil_state = wb["soil_profile"]
            stress = diagnose_stress(
                wb["root_zone_relative_available_water"],
                wb["eta_actual_mm"],
                components["crop_et_potential_mm"],
                components["potential_transpiration_mm"],
                actual_transpiration_mm=wb["actual_transpiration_mm"],
            )
            growth_state = update_growth_state(
                growth_state,
                current_date=current_date,
                bbch=stage,
                mean_air_temp_c=tmean,
                radiation_sum=require_weather_value(row, "shortwave_radiation_sum", context=f"daily weather[{current_date}]"),
                actual_transpiration_mm=wb["actual_transpiration_mm"],
                potential_transpiration_mm=components["potential_transpiration_mm"],
                reference_et_mm=components["reference_et_mm"],
                root_zone_relative_available_water=wb["root_zone_relative_available_water"],
                top_layer_relative_water=float(wb["soil_water_by_layer"][0]["relative_available_water"]) if wb["soil_water_by_layer"] else wb["root_zone_relative_available_water"],
                precipitation_mm=precipitation_mm,
                irrigation_mm=irrigation_mm,
                soil_water_by_layer=wb["soil_water_by_layer"],
                root_depth_mm=root_depth_mm,
                root_activity_weights=root_weights,
            )
            public_stress_risk = stress["stress_risk"] if in_water_season and self._is_water_sensitive_stage(stage) else "NOT_SEASONAL"
            daily_records.append({
                "Date": str(current_date),
                "Stage": stage,
                "BBCH": stage_to_bbch(stage),
                "bbch": stage_to_bbch(stage),
                **extract_public_growth_state(growth_state),
                "root_depth_mm": round(root_depth_mm, 3),
                "reference_et_mm": components["reference_et_mm"],
                "crop_et_potential_mm": components["crop_et_potential_mm"],
                "soil_evaporation_mm": wb["actual_soil_evaporation_mm"],
                "potential_transpiration_mm": components["potential_transpiration_mm"],
                "actual_transpiration_mm": wb["actual_transpiration_mm"],
                "water_uptake_ratio": stress["water_uptake_ratio"],
                "plant_water_stress_index": stress["plant_water_stress_index"],
                "eta_actual_mm": wb["eta_actual_mm"],
                "root_zone_soil_water_mm": wb["root_zone_soil_water_mm"],
                "root_zone_relative_available_water": wb["root_zone_relative_available_water"],
                "storage_root_zone_relative_available_water": wb["storage_root_zone_relative_available_water"],
                "active_root_zone_relative_available_water": wb["active_root_zone_relative_available_water"],
                "soil_water_by_layer": wb["soil_water_by_layer"],
                "drainage_mm": wb["drainage_mm"],
                "runoff_mm": wb["runoff_mm"],
                "deep_percolation_mm": wb["deep_percolation_mm"],
                "capillary_rise_mm": wb["capillary_rise_mm"],
                "precipitation_mm": round(precipitation_mm, 3),
                "stress_index_water": stress["stress_index_water"],
                "stress_index_eta_ratio": stress["stress_index_eta_ratio"],
                "stress_risk": public_stress_risk,
                "raw_stress_risk": stress["stress_risk"],
                "irrigation_mm": round(irrigation_mm, 3),
            })
        daily_records = apply_forecast_aggregated_drought_status(daily_records)
        expected_yield = self._simulated_yield_kg_ha(daily_records)
        event_dates = [event["Date"] for event in (scenario_events or [])]
        economics_result = score_scenario(
            expected_yield_kg_ha=expected_yield,
            grain_price_cny_per_kg=economics["grain_price_cny_per_kg"],
            total_irrigation_mm=sum(e["gross_depth_mm"] for e in (scenario_events or [])),
            event_count=len(scenario_events or []),
            electricity_cost_cny_per_kwh=economics["electricity_cost_cny_per_kwh"],
            pump_kwh_per_mm_ha=economics["pump_kwh_per_mm_ha"],
            irrigation_event_labor_cost_cny_ha=economics["irrigation_event_labor_cost_cny_ha"],
            water_cost_cny_per_mm_ha=economics.get("water_cost_cny_per_mm_ha", 0.0),
            event_dates=event_dates,
            product_prices=economics.get("product_prices"),
        )
        return {"daily_records": daily_records, "economics": economics_result, "summary": {"final_stress_score": daily_records[-1]["stress_index_water"] if daily_records else 0.0, "total_irrigation_mm": round(sum(e["gross_depth_mm"] for e in (scenario_events or [])), 3)}}

    def _simulate_with_policy(self, weather_df: pd.DataFrame, growth_stage_df: pd.DataFrame, soil_profile: list[dict], target_yield_kg_ha: float, economics: dict, decision_date, applied_events: list[dict] | None = None, soil_test: dict | None = None, fertilizer_history: list[dict] | None = None, custom_products: list[dict] | None = None) -> dict:
        gs_map = {
            pd.to_datetime(item["Date"]).date(): normalize_iowa_stage(item["Stage"])
            for item in growth_stage_df.to_dict(orient="records")
        }
        season_start = min(gs_map) if gs_map else decision_date
        recommendation_start = max(decision_date, season_start)
        soil_state = deepcopy(soil_profile)
        daily_records = []
        actions = []
        growth_state = initialize_growth_state(
            soil_test=soil_test,
            fertilizer_history=fertilizer_history,
            custom_products=custom_products,
            nutrition_enabled=soil_test is not None or bool(fertilizer_history),
            soil_profile=soil_profile,
        )
        last_irrigation_date = None
        applied_by_date = {event["Date"]: float(event["gross_depth_mm"]) for event in (applied_events or [])}
        scheduled_by_date: dict[Any, float] = {}
        pending_action_date = None
        for row_idx, row in weather_df.iterrows():
            current_date = row["Date"]
            stage = gs_map.get(current_date, "VS")
            in_water_season = current_date >= season_start
            tmean = float(row["temperature_2m_mean"])
            prior_root_depth = float(growth_state.get("_root_depth_mm", initial_root_depth_mm()))
            root_snapshot = apply_daily_water_balance(soil_profile=soil_state, root_depth_mm=prior_root_depth, precipitation_mm=0.0, irrigation_gross_mm=0.0, potential_soil_evaporation_mm=0.0, potential_transpiration_mm=0.0, irrigation_efficiency=self.method_specs["efficiency"])
            root_depth_mm = advance_root_depth(prior_root_depth, stage, tmean, root_snapshot["root_zone_relative_available_water"])
            root_weights = layer_root_activity(soil_state, root_depth_mm)
            et0 = reference_et_mm(
                current_date,
                self.latitude,
                tmean,
                require_weather_value(row, "temperature_2m_min", context=f"daily weather[{current_date}]"),
                require_weather_value(row, "temperature_2m_max", context=f"daily weather[{current_date}]"),
                require_weather_value(row, "shortwave_radiation_sum", context=f"daily weather[{current_date}]"),
            )
            raw = root_snapshot["root_zone_relative_available_water"]
            components = crop_et_components(et0, kc_for_bbch(stage), canopy_cover_from_lai(growth_state["lai"]), None)
            phase = get_phase(stage)
            potential_soil_evap = round(components["soil_evaporation_mm"] * self.method_specs["evaporation_multiplier"] * self._seasonal_soil_evaporation_factor(phase), 3)
            precipitation_mm = require_weather_value(row, "precipitation_sum", context=f"daily weather[{current_date}]")
            # Use the potential ET state for pre-decision screening; a zero actual ET placeholder
            # would otherwise force artificial HIGH stress before the day is even simulated.
            pre_stress = diagnose_stress(
                raw,
                components["crop_et_potential_mm"],
                components["crop_et_potential_mm"],
                components["potential_transpiration_mm"],
            )
            irrigation_mm = float(applied_by_date.get(current_date, scheduled_by_date.get(current_date, 0.0)))
            if irrigation_mm > 0:
                last_irrigation_date = current_date
                if pending_action_date == current_date:
                    pending_action_date = None
            if current_date >= recommendation_start and pending_action_date is None:
                days_since_last = 999 if last_irrigation_date is None else (current_date - last_irrigation_date).days
                if self._irrigation_trigger(stage, pre_stress["stress_risk"], raw, days_since_last, components["potential_transpiration_mm"], et0, target_yield_kg_ha):
                    target_raw = self._phase_target_raw(phase, target_yield_kg_ha)
                    threshold = self._phase_trigger_threshold(phase, target_yield_kg_ha)
                    refill_deficit_mm = self._estimate_refill_deficit_mm(raw, target_raw, root_snapshot["root_zone_soil_water_mm"])
                    max_depth_mm = self._method_max_depth_mm()
                    recommended_depth_mm = round(
                        min(
                            max_depth_mm,
                            max(
                                self.method_specs["min_depth_mm"],
                                refill_deficit_mm / max(float(self.method_specs["efficiency"]), 0.01),
                            ),
                        ),
                        1,
                    )
                    marginal = self._estimate_marginal_irrigation_yield_gain(
                        stage=stage,
                        growth_state=growth_state,
                        raw=raw,
                        target_raw=target_raw,
                        refill_deficit_mm=refill_deficit_mm,
                        recommended_depth_mm=recommended_depth_mm,
                        potential_transpiration_mm=components["potential_transpiration_mm"],
                    )
                    minimum_gain = self._minimum_economic_yield_gain_kg_ha(economics, recommended_depth_mm)
                    window = self._select_irrigation_window(
                        weather_df=weather_df,
                        start_idx=row_idx,
                        raw=raw,
                        target_raw=target_raw,
                        threshold=threshold,
                        stress_risk=pre_stress["stress_risk"],
                        root_zone_soil_water_mm=root_snapshot["root_zone_soil_water_mm"],
                    )
                    if window is not None:
                        scheduled_date = window["recommended_date"]
                        if scheduled_date in applied_by_date:
                            pending_action_date = scheduled_date
                            continue
                        baseline_events = [
                            {"Date": day, "gross_depth_mm": depth}
                            for day, depth in sorted(scheduled_by_date.items())
                        ]
                        baseline_events.extend(applied_events or [])
                        candidate_event = {"Date": scheduled_date, "gross_depth_mm": recommended_depth_mm}
                        marginal["expected_yield_gain_kg_ha"] = self._simulated_event_yield_gain(
                            weather_df=weather_df,
                            growth_stage_df=growth_stage_df,
                            soil_profile=soil_profile,
                            target_yield_kg_ha=target_yield_kg_ha,
                            economics=economics,
                            baseline_events=baseline_events,
                            candidate_event=candidate_event,
                            soil_test=soil_test,
                            fertilizer_history=fertilizer_history,
                            custom_products=custom_products,
                        )
                        if marginal["expected_yield_gain_kg_ha"] <= minimum_gain:
                            continue
                        scheduled_by_date[scheduled_date] = recommended_depth_mm
                        if scheduled_date == current_date:
                            irrigation_mm = recommended_depth_mm
                            last_irrigation_date = current_date
                        else:
                            pending_action_date = scheduled_date
                        reason = (
                            f"Sequential policy trigger at stage {stage} with {pre_stress['stress_risk']} stress and RAW {raw:.2f}; "
                            f"phenology water response {marginal['yield_sensitivity']:.2f}, "
                            f"marginal yield gain {marginal['expected_yield_gain_kg_ha']:.1f} kg/ha "
                            f"above economic threshold {minimum_gain:.1f} kg/ha."
                        )
                        if window["policy_reasons"]:
                            reason = f"{reason} {'; '.join(window['policy_reasons'])}."
                        actions.append({
                            "Date": str(scheduled_date),
                            "recommendationCode": "IRRIGATE",
                            "actionTypeCode": "IRRIGATION",
                            "treatmentWindowCode": "IMMEDIATE",
                            "treatmentStartDate": str(window["treatment_start_date"]),
                            "treatmentEndDate": str(window["treatment_end_date"]),
                            "recommendedIrrigationDate": str(scheduled_date),
                            "recommendedGrossDepthMm": recommended_depth_mm,
                            "recommendedNetDepthMm": round(recommended_depth_mm * self.method_specs["efficiency"], 3),
                            "recommendedRemainingEventCount": self._remaining_interval_slots(
                                weather_df,
                                row_idx,
                                gs_map,
                                target_yield_kg_ha,
                            ),
                            "method": self.irrigation_method,
                            "reason": reason,
                            "expectedYieldImpactKgHa": marginal["expected_yield_gain_kg_ha"],
                            "expectedNetReturnCnyHa": 0.0,
                        })
            wb = apply_daily_water_balance(
                soil_profile=soil_state,
                root_depth_mm=root_depth_mm,
                precipitation_mm=precipitation_mm,
                irrigation_gross_mm=irrigation_mm,
                potential_soil_evaporation_mm=potential_soil_evap,
                potential_transpiration_mm=components["potential_transpiration_mm"],
                irrigation_efficiency=self.method_specs["efficiency"],
                root_activity_weights=root_weights,
            )
            soil_state = wb["soil_profile"]
            stress = diagnose_stress(
                wb["root_zone_relative_available_water"],
                wb["eta_actual_mm"],
                components["crop_et_potential_mm"],
                components["potential_transpiration_mm"],
                actual_transpiration_mm=wb["actual_transpiration_mm"],
            )
            growth_state = update_growth_state(
                growth_state,
                current_date=current_date,
                bbch=stage,
                mean_air_temp_c=tmean,
                radiation_sum=require_weather_value(row, "shortwave_radiation_sum", context=f"daily weather[{current_date}]"),
                actual_transpiration_mm=wb["actual_transpiration_mm"],
                potential_transpiration_mm=components["potential_transpiration_mm"],
                reference_et_mm=components["reference_et_mm"],
                root_zone_relative_available_water=wb["root_zone_relative_available_water"],
                top_layer_relative_water=float(wb["soil_water_by_layer"][0]["relative_available_water"]) if wb["soil_water_by_layer"] else wb["root_zone_relative_available_water"],
                precipitation_mm=precipitation_mm,
                irrigation_mm=irrigation_mm,
                soil_water_by_layer=wb["soil_water_by_layer"],
                root_depth_mm=root_depth_mm,
                root_activity_weights=root_weights,
            )
            public_stress_risk = stress["stress_risk"] if in_water_season and self._is_water_sensitive_stage(stage) else "NOT_SEASONAL"
            daily_records.append({
                "Date": str(current_date),
                "Stage": stage,
                "BBCH": stage_to_bbch(stage),
                "bbch": stage_to_bbch(stage),
                **extract_public_growth_state(growth_state),
                "root_depth_mm": round(root_depth_mm, 3),
                "reference_et_mm": components["reference_et_mm"],
                "crop_et_potential_mm": components["crop_et_potential_mm"],
                "soil_evaporation_mm": wb["actual_soil_evaporation_mm"],
                "potential_transpiration_mm": components["potential_transpiration_mm"],
                "actual_transpiration_mm": wb["actual_transpiration_mm"],
                "water_uptake_ratio": stress["water_uptake_ratio"],
                "plant_water_stress_index": stress["plant_water_stress_index"],
                "eta_actual_mm": wb["eta_actual_mm"],
                "root_zone_soil_water_mm": wb["root_zone_soil_water_mm"],
                "root_zone_relative_available_water": wb["root_zone_relative_available_water"],
                "storage_root_zone_relative_available_water": wb["storage_root_zone_relative_available_water"],
                "active_root_zone_relative_available_water": wb["active_root_zone_relative_available_water"],
                "soil_water_by_layer": wb["soil_water_by_layer"],
                "drainage_mm": wb["drainage_mm"],
                "runoff_mm": wb["runoff_mm"],
                "deep_percolation_mm": wb["deep_percolation_mm"],
                "capillary_rise_mm": wb["capillary_rise_mm"],
                "precipitation_mm": round(precipitation_mm, 3),
                "stress_index_water": stress["stress_index_water"],
                "stress_index_eta_ratio": stress["stress_index_eta_ratio"],
                "stress_risk": public_stress_risk,
                "raw_stress_risk": stress["stress_risk"],
                "irrigation_mm": round(irrigation_mm, 3),
            })
        daily_records = apply_forecast_aggregated_drought_status(daily_records)
        expected_yield = self._simulated_yield_kg_ha(daily_records)
        economics_result = score_scenario(
            expected_yield_kg_ha=expected_yield,
            grain_price_cny_per_kg=economics["grain_price_cny_per_kg"],
            total_irrigation_mm=sum(a["recommendedGrossDepthMm"] for a in actions),
            event_count=len(actions),
            electricity_cost_cny_per_kwh=economics["electricity_cost_cny_per_kwh"],
            pump_kwh_per_mm_ha=economics["pump_kwh_per_mm_ha"],
            irrigation_event_labor_cost_cny_ha=economics["irrigation_event_labor_cost_cny_ha"],
            water_cost_cny_per_mm_ha=economics.get("water_cost_cny_per_mm_ha", 0.0),
            event_dates=[item["recommendedIrrigationDate"] for item in actions if item.get("recommendedIrrigationDate")],
            product_prices=economics.get("product_prices"),
        )
        for action in actions:
            action["expectedNetReturnCnyHa"] = economics_result["expected_net_return_cny_ha"]
        if not actions:
            actions.append({
                "Date": str(decision_date),
                "recommendationCode": "NOT_NEEDED",
                "actionTypeCode": "NOT_NEEDED",
                "treatmentWindowCode": "SEASON",
                "treatmentStartDate": None,
                "treatmentEndDate": None,
                "recommendedIrrigationDate": None,
                "recommendedGrossDepthMm": 0.0,
                "recommendedNetDepthMm": 0.0,
                "recommendedRemainingEventCount": 0,
                "reason": "No irrigation trigger occurred in the remaining season.",
                "expectedYieldImpactKgHa": 0.0,
                "expectedNetReturnCnyHa": economics_result["expected_net_return_cny_ha"],
            })
        return {"daily_records": daily_records, "action_recommendations": actions, "economics": economics_result}

    def _apply_irrigation_replay_status(
        self,
        daily_records: list[dict],
        applied_events: list[dict],
        target_yield_kg_ha: float,
    ) -> list[dict]:
        if not applied_events:
            return daily_records
        events = sorted(applied_events, key=lambda event: event["Date"])
        adjusted: list[dict] = []
        for record in daily_records:
            day = pd.to_datetime(record["Date"]).date()
            if str(record.get("stress_risk", "")).upper() == "NOT_SEASONAL":
                adjusted.append(record)
                continue
            stage = record.get("Stage") or record.get("BBCH") or record.get("bbch") or "VS"
            phase = get_phase(stage)
            coverage_days = max(16, self._method_interval_days(phase, target_yield_kg_ha) + 7)
            active_event = next(
                (
                    event
                    for event in reversed(events)
                    if event["Date"] <= day and (day - event["Date"]).days <= coverage_days
                ),
                None,
            )
            if active_event is None:
                adjusted.append(record)
                continue
            row = dict(record)
            row["raw_stress_risk"] = row.get("raw_stress_risk", row.get("stress_risk"))
            row["stress_risk"] = "IRRIGATED"
            row["irrigation_replay_event_date"] = active_event["Date"].isoformat()
            row["irrigation_replay_effect_days"] = coverage_days
            adjusted.append(row)
        return adjusted

    @staticmethod
    def _visible_drought_supports_irrigation(action: dict[str, Any], daily_records: list[dict]) -> bool:
        day_value = action.get("recommendedIrrigationDate") or action.get("Date")
        if not day_value:
            return False
        action_day = pd.to_datetime(day_value).date()
        for record in daily_records:
            record_day = pd.to_datetime(record["Date"]).date()
            if abs((record_day - action_day).days) > 3:
                continue
            if str(record.get("stress_risk") or "").upper() in {"MEDIUM", "HIGH"}:
                return True
        return False

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        defaults_used = []
        input_adjustments = []
        use_nutrition_coupling = bool(payload.get("_force_nutrition_coupling")) or payload.get("management_mode") == "water_nutrition"
        raw_economics = payload.get("economic_parameters")
        if not raw_economics:
            raise ValueError("economic_parameters is required")
        economics = {
            key: value if key == "product_prices" else float(value)
            for key, value in raw_economics.items()
            if value is not None
        }
        required_economics = [
            "grain_price_cny_per_kg",
            "electricity_cost_cny_per_kwh",
            "pump_kwh_per_mm_ha",
            "irrigation_event_labor_cost_cny_ha",
        ]
        missing_economics = [key for key in required_economics if key not in economics]
        if missing_economics:
            raise ValueError(f"economic_parameters missing required field(s): {', '.join(missing_economics)}")
        target_yield_raw = payload.get("target_yield_kg_ha")
        if target_yield_raw is None:
            raise ValueError("target_yield_kg_ha is required")
        target_yield_kg_ha = float(target_yield_raw)

        decision_date = pd.to_datetime(payload["decision_date"]).date()
        weather_history = normalize_daily_weather(payload.get("weather_data") or [], allow_empty=True)
        weather_forecast = normalize_daily_weather(payload.get("forecast_weather_data") or [], allow_empty=True)
        weather_df = merge_historical_and_forecast(weather_history, weather_forecast)
        if weather_df.empty:
            raise ValueError("weather_data cannot be empty")
        gs = pd.DataFrame(payload.get("growth_stage") or [])
        if gs.empty:
            raise ValueError("growth_stage is required for irrigation service")
        gs["Date"] = pd.to_datetime(gs["Date"]).dt.date
        season_end = gs["Date"].max()
        soil_profile, soil_adjustments, soil_defaults = validate_and_prepare_profile(self.soil_type, payload["soil_profile"])
        input_adjustments.extend(soil_adjustments)
        defaults_used.extend(soil_defaults)
        applied_events = []
        for event in payload.get("irrigation_history") or payload.get("applied_irrigations") or []:
            dt = event.get("Date") or event.get("date") or event.get("recommendedIrrigationDate")
            if not dt:
                raise ValueError("irrigation_history event missing required date field")
            gross_depth = next(
                (
                    event[key]
                    for key in ("gross_depth_mm", "amount_mm", "recommendedGrossDepthMm", "irrigation_mm")
                    if key in event and event[key] is not None
                ),
                None,
            )
            if gross_depth is None:
                raise ValueError("irrigation_history event missing required depth field")
            applied_events.append({"Date": pd.to_datetime(dt).date(), "gross_depth_mm": float(gross_depth)})
        if season_end and weather_df["Date"].max() < season_end:
            raise ValueError("weather_data must cover the season through the final growth stage date")
        historical = self._simulate(
            weather_df=weather_df,
            growth_stage_df=gs,
            soil_profile=soil_profile,
            target_yield_kg_ha=target_yield_kg_ha,
            economics=economics,
            scenario_events=applied_events,
            soil_test=payload.get("soil_test") if use_nutrition_coupling else None,
            fertilizer_history=payload.get("fertilizer_history") if use_nutrition_coupling else None,
            custom_products=payload.get("custom_products") if use_nutrition_coupling else None,
        )
        decision_record = next((r for r in historical["daily_records"] if r["Date"] == str(decision_date)), historical["daily_records"][-1])
        future_days = max(0, (weather_df["Date"].max() - decision_date).days)
        scenarios = []
        decision_instant_stress = decision_record.get("instant_stress_risk", decision_record["stress_risk"])
        for scenario in generate_irrigation_scenarios(decision_date, self.method_specs, future_days, decision_record["root_zone_relative_available_water"], decision_instant_stress):
            events = applied_events + [{"Date": decision_date + timedelta(days=e["offset_days"]), "gross_depth_mm": e["gross_depth_mm"]} for e in scenario["events"]]
            result = self._simulate(
                weather_df=weather_df,
                growth_stage_df=gs,
                soil_profile=soil_profile,
                target_yield_kg_ha=target_yield_kg_ha,
                economics=economics,
                scenario_events=events,
                soil_test=payload.get("soil_test") if use_nutrition_coupling else None,
                fertilizer_history=payload.get("fertilizer_history") if use_nutrition_coupling else None,
                custom_products=payload.get("custom_products") if use_nutrition_coupling else None,
            )
            scenarios.append({"scenario_name": scenario["name"], "events": [{"Date": str(e["Date"]), "gross_depth_mm": e["gross_depth_mm"]} for e in events], "economics": result["economics"], "summary": result["summary"], "daily_tail": result["daily_records"][-10:]})
        ranked = rank_scenarios(scenarios)
        best = ranked[0]
        scheduled = self._simulate_with_policy(
            weather_df=weather_df,
            growth_stage_df=gs,
            soil_profile=soil_profile,
            target_yield_kg_ha=target_yield_kg_ha,
            economics=economics,
            decision_date=decision_date,
            applied_events=applied_events,
            soil_test=payload.get("soil_test") if use_nutrition_coupling else None,
            fertilizer_history=payload.get("fertilizer_history") if use_nutrition_coupling else None,
            custom_products=payload.get("custom_products") if use_nutrition_coupling else None,
        )
        scheduled["daily_records"] = self._apply_irrigation_replay_status(
            scheduled["daily_records"],
            applied_events,
            target_yield_kg_ha,
        )
        action_recommendations = [
            action
            for action in scheduled["action_recommendations"]
            if self._visible_drought_supports_irrigation(action, historical["daily_records"])
        ]
        if action_recommendations and action_recommendations[0]["recommendationCode"] == "IRRIGATE":
            detail = action_recommendations[0].get("reason")
            summary = f"Best near-term scenario is irrigation; then follow sequential policy for later actions. Current stress is {decision_record['stress_risk']}."
            action_recommendations[0]["reason"] = summary if not detail else f"{summary} {detail}"
            scenario_yield_gain = round(best["economics"]["expected_yield_kg_ha"] - historical["economics"]["expected_yield_kg_ha"], 3)
            policy_yield_gain = float(action_recommendations[0].get("expectedYieldImpactKgHa", 0.0) or 0.0)
            action_recommendations[0]["expectedYieldImpactKgHa"] = round(max(policy_yield_gain, scenario_yield_gain), 3)
            action_recommendations[0]["expectedNetReturnCnyHa"] = best["economics"]["expected_net_return_cny_ha"]
        explanation_summary = (
            action_recommendations[0]["reason"]
            if action_recommendations
            else "No irrigation recommendation because visible drought stress risk is below MEDIUM."
        )
        display_daily_records = scheduled["daily_records"] if applied_events else historical["daily_records"]
        field_status = [{"Date": r["Date"], "field_status": r["stress_risk"], "root_zone_status": r["stress_risk"], "forecast_risk": "WATCH" if r["stress_risk"] in {"MEDIUM", "HIGH"} else "STABLE"} for r in scheduled["daily_records"]]
        return {
            "current_status": next((r for r in historical["daily_records"] if r["Date"] == str(decision_date)), historical["daily_records"][-1]),
            "after_recommendation_current_status": next((r for r in scheduled["daily_records"] if r["Date"] == str(decision_date)), scheduled["daily_records"][-1]),
            "no_action_daily_stress_risk": historical["daily_records"],
            "daily_stress_risk": scheduled["daily_records"],
            "display_daily_stress_risk": display_daily_records,
            "field_status": field_status,
            "action_recommendations": action_recommendations,
            "scenario_comparison": ranked,
            "defaults_used": defaults_used,
            "input_adjustments": input_adjustments,
            "explanation": {
                "summary": explanation_summary,
                "economic_basis": "profit = grain revenue - pumping electricity cost - irrigation event labor cost",
                "irrigation_method": self.irrigation_method,
            },
        }
