from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import pandas as pd

from core.drought_stress import diagnose_stress
from core.economic_optimization import irrigation_activity_cost_breakdown, irrigation_n_leaching_cost_breakdown, score_scenario
from core.evapotranspiration import crop_et_components, reference_et_mm
from core.irrigation_engine import generate_irrigation_scenarios
from core.scenario_simulation import rank_scenarios
from core.soil_hydraulics import SOIL_DEFAULTS, validate_and_prepare_profile
from core.soil_water_balance import apply_daily_water_balance
from core.weather_processing import merge_historical_and_forecast, normalize_daily_weather, require_weather_value

from .bbch_mapping import get_phase
from .config import IRRIGATION_METHOD_SPECS
from .crop_coefficients import canopy_cover_from_lai, kc_for_bbch
from .growth import extract_public_growth_state, initialize_growth_state, update_growth_state
from .rooting import advance_root_depth, initial_root_depth_mm, layer_root_activity
from crops.wheat.phenology.config import parse_wheat_bbch_stage


class WheatIrrigationModel:
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
        return max(0.0, min(1.0, (float(target_yield_kg_ha) - 7500.0) / 2500.0))

    def _soil_defaults(self) -> dict:
        return SOIL_DEFAULTS.get(self.soil_type, SOIL_DEFAULTS["sandy_loam"])

    def _soil_water_holding_index(self) -> float:
        defaults = self._soil_defaults()
        paw_per_m_mm = max(
            0.0,
            (float(defaults.get("field_capacity", 0.24) or 0.24) - float(defaults.get("wilting_point", 0.10) or 0.10))
            * 1000.0,
        )
        return max(0.0, min(1.0, (paw_per_m_mm - 90.0) / 80.0))

    def _soil_conductivity_index(self) -> float:
        defaults = self._soil_defaults()
        ksat_mm_day = float(defaults.get("ksat_mm_day", 120.0) or 120.0)
        return max(0.0, min(1.0, (ksat_mm_day - 35.0) / 205.0))

    def _soil_trigger_adjustment(self) -> float:
        holding = self._soil_water_holding_index()
        conductivity = self._soil_conductivity_index()
        # Low-holding / fast-draining soils should trigger before they fall into
        # a late rescue pattern, while heavier soils can wait longer.
        return 0.10 - 0.22 * holding + 0.04 * conductivity

    def _soil_target_adjustment(self) -> float:
        holding = self._soil_water_holding_index()
        conductivity = self._soil_conductivity_index()
        return -0.14 * holding + 0.015 * (conductivity - 0.5)

    def _phase_trigger_threshold_for_target(self, phase: str, target_yield_kg_ha: float) -> float:
        base_threshold = {
            "establishment": 0.24,
            "tillering": 0.24,
            "jointing": 0.54,
            "booting": 0.62,
            "heading_flowering": 0.66,
            "grain_filling": 0.42,
            "maturity": 0.30,
        }.get(phase, 0.40)
        intensity = self._target_intensity(target_yield_kg_ha)
        phase_bonus = 0.0
        if phase in {"jointing", "booting", "heading_flowering"}:
            phase_bonus = 0.08 * intensity
        elif phase == "grain_filling":
            phase_bonus = 0.05 * intensity
        return max(0.20, min(0.78, base_threshold + self.method_specs["stress_threshold_shift"] + phase_bonus + self._soil_trigger_adjustment()))

    def _phase_target_raw_for_target(self, phase: str, target_yield_kg_ha: float) -> float:
        base_target = {
            "jointing": 0.84,
            "booting": 0.89,
            "heading_flowering": 0.91,
            "grain_filling": 0.76,
        }.get(phase, 0.72)
        intensity = self._target_intensity(target_yield_kg_ha)
        phase_bonus = 0.06 * intensity if phase in {"jointing", "booting", "heading_flowering"} else 0.04 * intensity
        return max(0.55, min(0.95, base_target + self.method_specs["target_refill_bonus"] + phase_bonus + self._soil_target_adjustment()))

    def _first_event_establishment_credit(self, phase: str) -> float:
        holding = self._soil_water_holding_index()
        conductivity = self._soil_conductivity_index()
        phase_base = {
            "jointing": 0.74,
            "booting": 0.78,
            "heading_flowering": 0.84,
            "grain_filling": 0.90,
        }.get(phase, 0.82)
        physical_credit = 0.14 * (1.0 - holding) + 0.06 * conductivity
        return max(0.62, min(0.92, phase_base - physical_credit))

    def _method_evaporation_factor(self) -> float:
        return float(self.method_specs["evaporation_multiplier"])

    def _seasonal_soil_evaporation_factor(self, phase: str, bbch: int, mean_air_temp_c: float) -> float:
        if phase == "establishment":
            return 0.55
        if phase == "tillering":
            if bbch <= 25 and mean_air_temp_c < 8.0:
                return 0.28
            if mean_air_temp_c < 12.0:
                return 0.42
            return 0.58
        if phase == "jointing":
            return 0.72
        if phase == "booting":
            return 0.82
        if phase == "heading_flowering":
            return 0.88
        if phase == "grain_filling":
            return 0.78
        return 0.50

    def _method_interval_days(self, phase: str, target_yield_kg_ha: float) -> int:
        preferred = int(self.method_specs["preferred_interval_days"])
        preferred -= int(round(3.0 * self._target_intensity(target_yield_kg_ha)))
        if phase in {"booting", "heading_flowering"}:
            preferred = max(7, preferred - 3)
        if phase == "grain_filling":
            preferred = max(7, preferred - 2)
        return max(7, preferred)

    @staticmethod
    def _stage_value_weight(phase: str) -> float:
        return {
            "establishment": 0.15,
            "tillering": 0.20,
            "jointing": 0.65,
            "booting": 0.95,
            "heading_flowering": 0.98,
            "grain_filling": 0.24,
            "maturity": 0.10,
        }.get(phase, 0.35)

    @staticmethod
    def _soil_buffer_index(root_zone_soil_water_mm: float, raw: float, root_depth_mm: float) -> float:
        if raw <= 0:
            return 0.0
        root_zone_taw_mm = max(0.0, float(root_zone_soil_water_mm)) / max(float(raw), 0.05)
        root_depth_m = max(0.25, float(root_depth_mm) / 1000.0)
        taw_per_m = root_zone_taw_mm / root_depth_m
        return max(0.0, min(1.0, (taw_per_m - 90.0) / 110.0))

    def _soil_retention_factor(self) -> float:
        defaults = self._soil_defaults()
        field_capacity = float(defaults.get("field_capacity", 0.24) or 0.24)
        ksat_mm_day = float(defaults.get("ksat_mm_day", 120.0) or 120.0)
        fc_factor = max(0.0, min(1.0, (field_capacity - 0.10) / 0.26))
        conductivity_penalty = max(0.0, min(1.0, (ksat_mm_day - 35.0) / 205.0))
        return max(0.42, min(1.0, 0.48 + 0.58 * fc_factor - 0.22 * conductivity_penalty))

    @staticmethod
    def _current_mobile_n_kg_ha(growth_state: dict) -> float:
        return round(
            max(0.0, sum(float(x or 0.0) for x in (growth_state.get("_soil_n_layers_kg_ha") or [])))
            + max(0.0, sum(float(item.get("remaining_n_kg_ha", 0.0) or 0.0) for item in (growth_state.get("_fertilizer_n_pools") or []))),
            3,
        )

    def _estimate_irrigation_n_leaching_kg_ha(
        self,
        *,
        mobile_n_kg_ha: float,
        deep_percolation_mm: float,
        precipitation_mm: float,
        irrigation_mm: float,
        soil_retention: float,
    ) -> float:
        if mobile_n_kg_ha <= 0 or deep_percolation_mm <= 0 or irrigation_mm <= 0:
            return 0.0
        irrigation_share = irrigation_mm / max(1.0, precipitation_mm + irrigation_mm)
        hydraulic_push = min(0.85, deep_percolation_mm / max(12.0, 28.0 + 65.0 * soil_retention))
        mobility = 0.24 + 0.56 * (1.0 - soil_retention)
        return round(max(0.0, mobile_n_kg_ha * hydraulic_push * mobility * irrigation_share), 3)

    def _estimate_irrigation_n_leaching_cost_cny_ha(
        self,
        *,
        mobile_n_kg_ha: float,
        recommended_depth_mm: float,
        precipitation_mm: float,
        soil_retention: float,
        economics: dict,
        phase: str,
    ) -> float:
        estimated_n_leached = self._estimate_irrigation_n_leaching_kg_ha(
            mobile_n_kg_ha=mobile_n_kg_ha,
            deep_percolation_mm=max(0.0, recommended_depth_mm * (0.16 + 0.42 * (1.0 - soil_retention))),
            precipitation_mm=precipitation_mm,
            irrigation_mm=recommended_depth_mm,
            soil_retention=soil_retention,
        )
        phase_multiplier = {
            "establishment": 0.55,
            "tillering": 0.70,
            "jointing": 1.05,
            "booting": 1.10,
            "heading_flowering": 1.00,
            "grain_filling": 0.72,
            "maturity": 0.35,
        }.get(phase, 0.85)
        costs = irrigation_n_leaching_cost_breakdown(
            irrigation_attributable_n_leached_kg_ha=estimated_n_leached,
            grain_price_cny_per_kg=economics["grain_price_cny_per_kg"],
            product_prices=economics.get("product_prices"),
            yield_value_loss_cny_per_kg_n=economics["grain_price_cny_per_kg"] * (12.0 * phase_multiplier),
        )
        return float(costs["n_leaching_total_cost_cny_ha"])

    def _event_operating_cost_cny_ha(self, gross_depth_mm: float, economics: dict) -> float:
        labor = economics["irrigation_event_labor_cost_cny_ha"]
        electricity = (
            float(gross_depth_mm)
            * economics["pump_kwh_per_mm_ha"]
            * economics["electricity_cost_cny_per_kwh"]
        )
        return labor + electricity

    def _incremental_activity_cost_cny_ha(
        self,
        *,
        recommended_depth_mm: float,
        economics: dict,
        prior_depth_mm: float,
        prior_event_dates: list[Any],
        planned_date,
    ) -> float:
        before = irrigation_activity_cost_breakdown(
            total_irrigation_mm=prior_depth_mm,
            event_count=len(prior_event_dates),
            electricity_cost_cny_per_kwh=economics["electricity_cost_cny_per_kwh"],
            pump_kwh_per_mm_ha=economics["pump_kwh_per_mm_ha"],
            irrigation_event_labor_cost_cny_ha=economics["irrigation_event_labor_cost_cny_ha"],
            event_dates=prior_event_dates,
        )
        after_dates = list(prior_event_dates) + [planned_date]
        after = irrigation_activity_cost_breakdown(
            total_irrigation_mm=prior_depth_mm + recommended_depth_mm,
            event_count=len(after_dates),
            electricity_cost_cny_per_kwh=economics["electricity_cost_cny_per_kwh"],
            pump_kwh_per_mm_ha=economics["pump_kwh_per_mm_ha"],
            irrigation_event_labor_cost_cny_ha=economics["irrigation_event_labor_cost_cny_ha"],
            event_dates=after_dates,
        )
        return float(after["irrigation_operating_cost_cny_ha"]) - float(before["irrigation_operating_cost_cny_ha"])

    def _recent_irrigation_residual_fraction(
        self,
        *,
        prior_events: list[dict],
        planned_date,
        root_zone_soil_water_mm: float,
        raw: float,
        root_depth_mm: float,
    ) -> float:
        if not prior_events:
            return 0.0
        safe_raw = max(float(raw), 0.05)
        root_zone_capacity_mm = max(float(root_zone_soil_water_mm), 0.0) / safe_raw
        root_depth_factor = max(0.65, min(1.2, float(root_depth_mm) / 900.0))
        soil_retention = self._soil_retention_factor()
        holding = self._soil_water_holding_index()
        conductivity = self._soil_conductivity_index()
        residual_window_days = 10.0 + 12.0 * holding + 4.0 * (1.0 - conductivity)
        residual = 0.0
        for event in prior_events:
            event_date = event.get("date")
            if event_date is None or event_date >= planned_date:
                continue
            gap_days = max(0, (planned_date - event_date).days)
            if gap_days > residual_window_days * 1.6:
                continue
            gross_depth_mm = float(event.get("gross_depth_mm", 0.0) or 0.0)
            if gross_depth_mm <= 0.0:
                continue
            effective_depth_mm = gross_depth_mm * float(self.method_specs["efficiency"]) * max(0.62, soil_retention) * root_depth_factor
            event_share = min(1.15, effective_depth_mm / max(root_zone_capacity_mm, 1.0))
            residual += event_share * max(0.0, 1.0 - gap_days / max(residual_window_days, 1.0))
        return max(0.0, min(1.0, residual))

    def _is_economically_justified(
        self,
        *,
        phase: str,
        raw: float,
        root_zone_soil_water_mm: float,
        root_depth_mm: float,
        marginal_yield_gain_kg_ha: float,
        recommended_depth_mm: float,
        economics: dict,
        prior_event_dates: list[Any],
        prior_total_depth_mm: float,
        prior_events: list[dict],
        planned_date,
        estimated_n_leaching_cost_cny_ha: float,
    ) -> bool:
        if marginal_yield_gain_kg_ha <= 0.0:
            return False
        soil_buffer = self._soil_buffer_index(root_zone_soil_water_mm, raw, root_depth_mm)
        soil_retention = self._soil_retention_factor()
        marginal_value_cny_ha = marginal_yield_gain_kg_ha * economics["grain_price_cny_per_kg"]
        residual_carryover = max(0.0, min(1.0, soil_buffer * soil_retention))
        valid_prior_dates = [item for item in prior_event_dates if item is not None]
        gap_days = max(0, (planned_date - max(valid_prior_dates)).days) if valid_prior_dates else None
        phase_margin = {
            "establishment": 0.96,
            "tillering": 0.98,
            "jointing": 1.00,
            "booting": 1.04,
            "heading_flowering": 1.08,
            "grain_filling": 1.42,
            "maturity": 1.22,
        }.get(phase, 1.02)
        urgency_discount = 0.94 if raw < 0.16 else (0.97 if raw < 0.22 else 1.0)
        holding = self._soil_water_holding_index()
        conductivity = self._soil_conductivity_index()
        carryover_window_days = 10.0 + 12.0 * holding + 4.0 * (1.0 - conductivity)
        residual_margin = 1.0 + (0.22 + 0.20 * holding) * residual_carryover
        recent_event_carryover = 0.0
        if gap_days is not None and gap_days < carryover_window_days:
            recent_event_carryover = soil_retention * max(0.0, (carryover_window_days - gap_days) / max(carryover_window_days, 1.0))
            residual_margin += (0.24 + 0.18 * holding) * (carryover_window_days - gap_days) / max(carryover_window_days, 1.0)
            if gap_days <= 5:
                residual_margin += 0.22 + 0.12 * holding
        # The first strategic spring irrigation should not be over-penalized by
        # the stronger all-in cost assumptions; otherwise the model delays the
        # first event too much and compensates with clustered rescue irrigations.
        establishment_credit = 1.0
        if not valid_prior_dates:
            establishment_credit = self._first_event_establishment_credit(phase)
        recent_depth_carryover = self._recent_irrigation_residual_fraction(
            prior_events=prior_events,
            planned_date=planned_date,
            root_zone_soil_water_mm=root_zone_soil_water_mm,
            raw=raw,
            root_depth_mm=root_depth_mm,
        )
        required_return = self._incremental_activity_cost_cny_ha(
            recommended_depth_mm=recommended_depth_mm,
            economics=economics,
            prior_depth_mm=prior_total_depth_mm,
            prior_event_dates=prior_event_dates,
            planned_date=planned_date,
        ) * (1.06 + 0.28 * soil_buffer + 0.12 * (1.0 - soil_retention)) * phase_margin * urgency_discount * residual_margin * establishment_credit + estimated_n_leaching_cost_cny_ha
        if recent_event_carryover > 0.0:
            required_return *= 1.0 + recent_event_carryover * (0.62 + 0.36 * holding + 0.12 * (1.0 - conductivity))
        if recent_depth_carryover > 0.0:
            required_return *= 1.0 + recent_depth_carryover * (2.20 + 0.75 * holding + 0.18 * (1.0 - conductivity))
        if phase in {"heading_flowering", "grain_filling"} and gap_days is not None and gap_days <= 5 and raw > 0.30:
            return False
        return marginal_value_cny_ha >= required_return

    def _depth_cap_mm(self) -> float:
        base_cap = float(self.method_specs["max_depth_mm"])
        holding = self._soil_water_holding_index()
        conductivity = self._soil_conductivity_index()
        return base_cap + 8.0 + 28.0 * holding + 18.0 * (1.0 - conductivity)

    def _irrigation_trigger(
        self,
        bbch: int,
        stress_risk: str,
        raw: float,
        days_since_last: int,
        potential_transpiration_mm: float,
        et0: float,
        target_yield_kg_ha: float,
    ) -> bool:
        phase = get_phase(bbch)
        threshold = self._phase_trigger_threshold_for_target(phase, target_yield_kg_ha)
        if bbch >= 83:
            return False
        # Low atmospheric/crop demand periods should not trigger irrigation just because RAW is low.
        if potential_transpiration_mm < 1.05 and et0 < 2.4:
            return False
        if phase in {"establishment", "tillering"}:
            return (
                stress_risk == "HIGH"
                and raw < min(0.18, threshold - 0.06)
                and potential_transpiration_mm >= 1.20
                and bbch >= 24
            )
        if phase in {"jointing", "booting", "heading_flowering", "grain_filling"}:
            holding = self._soil_water_holding_index()
            conductivity = self._soil_conductivity_index()
            if phase == "jointing":
                medium_margin = 0.03 + 0.07 * holding
                high_margin = 0.01 + 0.10 * holding
            elif phase in {"booting", "heading_flowering"}:
                medium_margin = 0.04 + 0.10 * holding
                high_margin = 0.02 + 0.12 * holding
            else:
                medium_margin = 0.18 + 0.18 * holding
                high_margin = 0.22 + 0.12 * holding
            medium_stress_cutoff = 0.21 - 0.10 * holding + 0.05 * conductivity if phase == "grain_filling" else 0.30 - 0.18 * holding + 0.08 * conductivity
            if stress_risk == "HIGH":
                return raw < threshold - high_margin
            if stress_risk == "MEDIUM" and raw < medium_stress_cutoff:
                return raw < threshold - medium_margin
            return False
        return False

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

    @staticmethod
    def _yield_proxy_kg_ha(state: dict, bbch: int) -> float:
        total_biomass = float(state.get("total_biomass_kg_ha", 0.0) or 0.0)
        grain = float(state.get("grain_weight_kg_ha", 0.0) or 0.0)
        hi_factor = {
            "establishment": 0.06,
            "tillering": 0.12,
            "jointing": 0.22,
            "booting": 0.31,
            "heading_flowering": 0.42,
            "grain_filling": 0.58,
            "maturity": 1.0,
        }.get(get_phase(bbch), 0.30)
        if bbch >= 71:
            return grain
        return max(grain, total_biomass * hi_factor)

    def _marginal_horizon_days(self, phase: str) -> int:
        base = {
            "establishment": 8,
            "tillering": 10,
            "jointing": 16,
            "booting": 16,
            "heading_flowering": 14,
            "grain_filling": 10,
            "maturity": 7,
        }.get(phase, 12)
        holding = self._soil_water_holding_index()
        conductivity = self._soil_conductivity_index()
        return int(round(base + 3.0 + 5.0 * holding + 3.0 * (1.0 - conductivity)))

    def _preferred_refill_multiplier(self, phase: str) -> float:
        holding = self._soil_water_holding_index()
        conductivity = self._soil_conductivity_index()
        phase_bonus = {
            "jointing": 0.18,
            "booting": 0.20,
            "heading_flowering": 0.14,
            "grain_filling": 0.08,
        }.get(phase, 0.10)
        return 1.0 + phase_bonus * (0.90 + 1.00 * holding + 0.35 * (1.0 - conductivity))

    def _economic_refill_bias(self, economics: dict) -> float:
        labor = economics["irrigation_event_labor_cost_cny_ha"]
        pump_cost_per_mm = (
            economics["pump_kwh_per_mm_ha"]
            * economics["electricity_cost_cny_per_kwh"]
        )
        trip_cost_signal = labor + 45.0 * pump_cost_per_mm
        return max(0.0, min(0.35, (trip_cost_signal - 55.0) / 260.0))

    def _simulate_counterfactual_window(
        self,
        *,
        weather_df: pd.DataFrame,
        gs_map: dict,
        start_idx: int,
        soil_state: list[dict],
        growth_state: dict,
        target_yield_kg_ha: float,
        event_date,
        event_depth_mm: float,
    ) -> tuple[float, float]:
        horizon_end = min(len(weather_df), start_idx + self._marginal_horizon_days(get_phase(gs_map.get(weather_df.iloc[start_idx]["Date"], 0))))
        sim_soil = deepcopy(soil_state)
        sim_growth = deepcopy(growth_state)
        method_specs = self.method_specs
        last_state = extract_public_growth_state(sim_growth)
        last_bbch = gs_map.get(weather_df.iloc[start_idx]["Date"], 0)

        for idx in range(start_idx, horizon_end):
            row = weather_df.iloc[idx]
            current_date = row["Date"]
            bbch = gs_map.get(current_date, last_bbch)
            tmean = float(row["temperature_2m_mean"])
            prior_root_depth = float(sim_growth.get("_root_depth_mm", initial_root_depth_mm()))
            root_snapshot = apply_daily_water_balance(
                soil_profile=sim_soil,
                root_depth_mm=prior_root_depth,
                precipitation_mm=0.0,
                irrigation_gross_mm=0.0,
                potential_soil_evaporation_mm=0.0,
                potential_transpiration_mm=0.0,
                irrigation_efficiency=method_specs["efficiency"],
            )
            root_depth_mm = advance_root_depth(prior_root_depth, bbch, tmean, root_snapshot["root_zone_relative_available_water"])
            root_weights = layer_root_activity(sim_soil, root_depth_mm)
            et0 = reference_et_mm(
                current_date,
                self.latitude,
                tmean,
                require_weather_value(row, "temperature_2m_min", context=f"daily weather[{current_date}]"),
                require_weather_value(row, "temperature_2m_max", context=f"daily weather[{current_date}]"),
                require_weather_value(row, "shortwave_radiation_sum", context=f"daily weather[{current_date}]"),
            )
            canopy_cover = canopy_cover_from_lai(sim_growth["lai"])
            components = crop_et_components(et0, kc_for_bbch(bbch), canopy_cover, None)
            phase = get_phase(bbch)
            potential_soil_evap = round(
                components["soil_evaporation_mm"]
                * self._method_evaporation_factor()
                * self._seasonal_soil_evaporation_factor(phase, bbch, tmean),
                3,
            )
            irrigation_mm = float(event_depth_mm if event_date == current_date else 0.0)
            wb = apply_daily_water_balance(
                soil_profile=sim_soil,
                root_depth_mm=root_depth_mm,
                precipitation_mm=require_weather_value(row, "precipitation_sum", context=f"daily weather[{current_date}]"),
                irrigation_gross_mm=irrigation_mm,
                potential_soil_evaporation_mm=potential_soil_evap,
                potential_transpiration_mm=components["potential_transpiration_mm"],
                irrigation_efficiency=method_specs["efficiency"],
                root_activity_weights=root_weights,
            )
            sim_soil = wb["soil_profile"]
            sim_growth = update_growth_state(
                sim_growth,
                current_date=current_date,
                bbch=bbch,
                mean_air_temp_c=tmean,
                radiation_sum=require_weather_value(row, "shortwave_radiation_sum", context=f"daily weather[{current_date}]"),
                actual_transpiration_mm=wb["actual_transpiration_mm"],
                potential_transpiration_mm=components["potential_transpiration_mm"],
                reference_et_mm=components["reference_et_mm"],
                root_zone_relative_available_water=wb["root_zone_relative_available_water"],
                top_layer_relative_water=float(wb["soil_water_by_layer"][0]["relative_available_water"]) if wb["soil_water_by_layer"] else wb["root_zone_relative_available_water"],
                precipitation_mm=require_weather_value(row, "precipitation_sum", context=f"daily weather[{current_date}]"),
                irrigation_mm=irrigation_mm,
                deep_percolation_mm=wb["deep_percolation_mm"],
                soil_water_by_layer=wb["soil_water_by_layer"],
                root_depth_mm=root_depth_mm,
                root_activity_weights=root_weights,
                target_yield_kg_ha=target_yield_kg_ha,
            )
            last_state = extract_public_growth_state(sim_growth)
            last_bbch = bbch

        return self._yield_proxy_kg_ha(last_state, last_bbch), float(sim_growth.get("cumulative_irrigation_attributable_n_leached_kg_ha", 0.0) or 0.0)

    def _simulate(
        self,
        weather_df: pd.DataFrame,
        growth_stage_df: pd.DataFrame,
        soil_profile: list[dict],
        target_yield_kg_ha: float,
        economics: dict,
        scenario_events: list[dict] | None = None,
        soil_test: dict | None = None,
        fertilizer_history: list[dict] | None = None,
        custom_products: list[dict] | None = None,
    ) -> dict:
        gs_map = {pd.to_datetime(x["Date"]).date(): parse_wheat_bbch_stage(x.get("Stage")) for x in growth_stage_df.to_dict(orient="records")}
        soil_state = deepcopy(soil_profile)
        daily_records = []
        cumulative_penalty = 0.0
        cumulative_irrigation_n_leached = 0.0
        growth_state = initialize_growth_state(
            soil_test=soil_test,
            fertilizer_history=fertilizer_history,
            custom_products=custom_products,
            nutrition_enabled=soil_test is not None or bool(fertilizer_history),
            soil_profile=soil_profile,
        )
        events_by_date = {}
        for event in scenario_events or []:
            events_by_date[event["Date"]] = event["gross_depth_mm"]

        for _, row in weather_df.iterrows():
            current_date = row["Date"]
            bbch = gs_map.get(current_date, max(gs_map.values()) if gs_map else 0)
            tmean = float(row["temperature_2m_mean"])
            prior_root_depth = float(growth_state.get("_root_depth_mm", initial_root_depth_mm()))
            root_snapshot = apply_daily_water_balance(
                soil_profile=soil_state,
                root_depth_mm=prior_root_depth,
                precipitation_mm=0.0,
                irrigation_gross_mm=0.0,
                potential_soil_evaporation_mm=0.0,
                potential_transpiration_mm=0.0,
                irrigation_efficiency=IRRIGATION_METHOD_SPECS[self.irrigation_method]["efficiency"],
            )
            root_depth_mm = advance_root_depth(prior_root_depth, bbch, tmean, root_snapshot["root_zone_relative_available_water"])
            root_weights = layer_root_activity(soil_state, root_depth_mm)
            et0 = reference_et_mm(
                current_date,
                self.latitude,
                tmean,
                require_weather_value(row, "temperature_2m_min", context=f"daily weather[{current_date}]"),
                require_weather_value(row, "temperature_2m_max", context=f"daily weather[{current_date}]"),
                require_weather_value(row, "shortwave_radiation_sum", context=f"daily weather[{current_date}]"),
            )
            canopy_cover = canopy_cover_from_lai(growth_state["lai"])
            components = crop_et_components(et0, kc_for_bbch(bbch), canopy_cover, None)
            phase = get_phase(bbch)
            potential_soil_evap = round(
                components["soil_evaporation_mm"]
                * self._method_evaporation_factor()
                * self._seasonal_soil_evaporation_factor(phase, bbch, tmean),
                3,
            )
            irrigation_mm = float(events_by_date.get(current_date, 0.0))
            wb = apply_daily_water_balance(
                soil_profile=soil_state,
                root_depth_mm=root_depth_mm,
                precipitation_mm=require_weather_value(row, "precipitation_sum", context=f"daily weather[{current_date}]"),
                irrigation_gross_mm=irrigation_mm,
                potential_soil_evaporation_mm=potential_soil_evap,
                potential_transpiration_mm=components["potential_transpiration_mm"],
                irrigation_efficiency=IRRIGATION_METHOD_SPECS[self.irrigation_method]["efficiency"],
                root_activity_weights=root_weights,
            )
            soil_state = wb["soil_profile"]
            growth_state = update_growth_state(
                growth_state,
                current_date=current_date,
                bbch=bbch,
                mean_air_temp_c=tmean,
                radiation_sum=require_weather_value(row, "shortwave_radiation_sum", context=f"daily weather[{current_date}]"),
                actual_transpiration_mm=wb["actual_transpiration_mm"],
                potential_transpiration_mm=components["potential_transpiration_mm"],
                reference_et_mm=components["reference_et_mm"],
                root_zone_relative_available_water=wb["root_zone_relative_available_water"],
                top_layer_relative_water=float(wb["soil_water_by_layer"][0]["relative_available_water"]) if wb["soil_water_by_layer"] else wb["root_zone_relative_available_water"],
                precipitation_mm=require_weather_value(row, "precipitation_sum", context=f"daily weather[{current_date}]"),
                irrigation_mm=irrigation_mm,
                deep_percolation_mm=wb["deep_percolation_mm"],
                soil_water_by_layer=wb["soil_water_by_layer"],
                root_depth_mm=root_depth_mm,
                root_activity_weights=root_weights,
                target_yield_kg_ha=target_yield_kg_ha,
            )
            irrigation_n_leached = float(growth_state.get("irrigation_attributable_n_leached_kg_ha", 0.0) or 0.0)
            cumulative_irrigation_n_leached += irrigation_n_leached
            stress = diagnose_stress(
                wb["root_zone_relative_available_water"],
                wb["eta_actual_mm"],
                components["crop_et_potential_mm"],
                components["potential_transpiration_mm"],
                actual_transpiration_mm=wb["actual_transpiration_mm"],
            )
            daily_records.append(
                {
                    "Date": str(current_date),
                    "bbch": bbch,
                    **extract_public_growth_state(growth_state),
                    "root_depth_mm": round(root_depth_mm, 3),
                    "reference_et_mm": components["reference_et_mm"],
                    "crop_et_potential_mm": components["crop_et_potential_mm"],
                    "soil_evaporation_mm": wb["actual_soil_evaporation_mm"],
                    "potential_transpiration_mm": components["potential_transpiration_mm"],
                    "actual_transpiration_mm": wb["actual_transpiration_mm"],
                    "eta_actual_mm": wb["eta_actual_mm"],
                    "root_zone_soil_water_mm": wb["root_zone_soil_water_mm"],
                    "root_zone_relative_available_water": wb["root_zone_relative_available_water"],
                    "soil_water_by_layer": wb["soil_water_by_layer"],
                    "drainage_mm": wb["drainage_mm"],
                    "runoff_mm": wb["runoff_mm"],
                    "deep_percolation_mm": wb["deep_percolation_mm"],
                    "irrigation_attributable_n_leached_kg_ha": irrigation_n_leached,
                    "cumulative_irrigation_attributable_n_leached_kg_ha": round(cumulative_irrigation_n_leached, 3),
                    "capillary_rise_mm": wb["capillary_rise_mm"],
                    "stress_index_water": stress["stress_index_water"],
                    "stress_index_eta_ratio": stress["stress_index_eta_ratio"],
                    "stress_risk": stress["stress_risk"],
                    "irrigation_mm": round(irrigation_mm, 3),
                }
            )
        expected_yield = float(daily_records[-1]["grain_weight_kg_ha"]) if daily_records else 0.0
        economics_result = score_scenario(
            expected_yield_kg_ha=expected_yield,
            grain_price_cny_per_kg=economics["grain_price_cny_per_kg"],
            total_irrigation_mm=sum(e["gross_depth_mm"] for e in (scenario_events or [])),
            event_count=len(scenario_events or []),
            electricity_cost_cny_per_kwh=economics["electricity_cost_cny_per_kwh"],
            pump_kwh_per_mm_ha=economics["pump_kwh_per_mm_ha"],
            irrigation_event_labor_cost_cny_ha=economics["irrigation_event_labor_cost_cny_ha"],
            event_dates=[event["Date"] for event in (scenario_events or []) if event.get("Date")],
            irrigation_attributable_n_leached_kg_ha=cumulative_irrigation_n_leached,
            product_prices=economics.get("product_prices"),
        )
        return {
            "daily_records": daily_records,
            "economics": economics_result,
            "summary": {
                "final_stress_score": daily_records[-1]["stress_index_water"] if daily_records else 0.0,
                "total_irrigation_mm": round(sum(e["gross_depth_mm"] for e in (scenario_events or [])), 3),
            },
        }

    def _simulate_with_policy(
        self,
        weather_df: pd.DataFrame,
        growth_stage_df: pd.DataFrame,
        soil_profile: list[dict],
        target_yield_kg_ha: float,
        economics: dict,
        decision_date,
        applied_events: list[dict] | None = None,
        soil_test: dict | None = None,
        fertilizer_history: list[dict] | None = None,
        custom_products: list[dict] | None = None,
        full_season_recommendations: bool = True,
    ) -> dict:
        gs_map = {pd.to_datetime(x["Date"]).date(): parse_wheat_bbch_stage(x.get("Stage")) for x in growth_stage_df.to_dict(orient="records")}
        soil_state = deepcopy(soil_profile)
        daily_records = []
        actions = []
        cumulative_penalty = 0.0
        cumulative_irrigation_n_leached = 0.0
        growth_state = initialize_growth_state(
            soil_test=soil_test,
            fertilizer_history=fertilizer_history,
            custom_products=custom_products,
            nutrition_enabled=soil_test is not None or bool(fertilizer_history),
            soil_profile=soil_profile,
        )
        last_irrigation_date = None
        method_specs = self.method_specs
        applied_by_date = {event["Date"]: float(event["gross_depth_mm"]) for event in (applied_events or [])}
        scheduled_by_date: dict[Any, float] = {}
        pending_action_date = None

        for row_idx, row in weather_df.iterrows():
            current_date = row["Date"]
            bbch = gs_map.get(current_date, max(gs_map.values()) if gs_map else 0)
            tmean = float(row["temperature_2m_mean"])
            prior_root_depth = float(growth_state.get("_root_depth_mm", initial_root_depth_mm()))
            root_snapshot = apply_daily_water_balance(
                soil_profile=soil_state,
                root_depth_mm=prior_root_depth,
                precipitation_mm=0.0,
                irrigation_gross_mm=0.0,
                potential_soil_evaporation_mm=0.0,
                potential_transpiration_mm=0.0,
                irrigation_efficiency=method_specs["efficiency"],
            )
            root_depth_mm = advance_root_depth(prior_root_depth, bbch, tmean, root_snapshot["root_zone_relative_available_water"])
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
            canopy_cover = canopy_cover_from_lai(growth_state["lai"])
            components = crop_et_components(et0, kc_for_bbch(bbch), canopy_cover, None)
            phase = get_phase(bbch)
            potential_soil_evap = round(
                components["soil_evaporation_mm"]
                * self._method_evaporation_factor()
                * self._seasonal_soil_evaporation_factor(phase, bbch, tmean),
                3,
            )
            # Use the potential ET state for pre-decision screening; a zero actual ET placeholder
            # would otherwise force artificial HIGH stress before the day is even simulated.
            pre_stress = diagnose_stress(
                raw,
                components["crop_et_potential_mm"],
                components["crop_et_potential_mm"],
                components["potential_transpiration_mm"],
            )

            irrigation_mm = float(applied_by_date.get(current_date, scheduled_by_date.get(current_date, 0.0)))
            if irrigation_mm > 0.0:
                last_irrigation_date = current_date
                if pending_action_date == current_date:
                    pending_action_date = None
            if current_date >= decision_date and (full_season_recommendations or current_date == decision_date) and pending_action_date is None:
                days_since_last = 999 if last_irrigation_date is None else (current_date - last_irrigation_date).days
                if self._irrigation_trigger(
                    bbch,
                    pre_stress["stress_risk"],
                    raw,
                    days_since_last,
                    components["potential_transpiration_mm"],
                    et0,
                    target_yield_kg_ha,
                ):
                    phase = get_phase(bbch)
                    target_raw = self._phase_target_raw_for_target(phase, target_yield_kg_ha)
                    threshold = self._phase_trigger_threshold_for_target(phase, target_yield_kg_ha)
                    deficit = max(0.0, target_raw - raw)
                    refill_deficit_mm = self._estimate_refill_deficit_mm(raw, target_raw, root_snapshot["root_zone_soil_water_mm"])
                    refill_gross_mm = refill_deficit_mm / max(float(method_specs["efficiency"]), 1e-6)
                    economic_refill_bias = self._economic_refill_bias(economics)
                    preferred_refill_gross_mm = refill_gross_mm * (
                        self._preferred_refill_multiplier(phase) + economic_refill_bias
                    )
                    dynamic_depth_cap_mm = self._depth_cap_mm() * (1.0 + 0.35 * economic_refill_bias)
                    scaled_depth_mm = (
                        method_specs["min_depth_mm"]
                        + (dynamic_depth_cap_mm - method_specs["min_depth_mm"]) * min(1.0, deficit / 0.45)
                    )
                    recommended_depth_mm = round(
                        min(
                            dynamic_depth_cap_mm,
                            max(method_specs["min_depth_mm"], max(scaled_depth_mm, preferred_refill_gross_mm)),
                        ),
                        1,
                    )
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
                        baseline_proxy_yield, baseline_leached_n = self._simulate_counterfactual_window(
                            weather_df=weather_df,
                            gs_map=gs_map,
                            start_idx=row_idx,
                            soil_state=soil_state,
                            growth_state=growth_state,
                            target_yield_kg_ha=target_yield_kg_ha,
                            event_date=None,
                            event_depth_mm=0.0,
                        )
                        irrigated_proxy_yield, irrigated_leached_n = self._simulate_counterfactual_window(
                            weather_df=weather_df,
                            gs_map=gs_map,
                            start_idx=row_idx,
                            soil_state=soil_state,
                            growth_state=growth_state,
                            target_yield_kg_ha=target_yield_kg_ha,
                            event_date=window["recommended_date"],
                            event_depth_mm=recommended_depth_mm,
                        )
                        marginal_yield_gain_kg_ha = max(0.0, irrigated_proxy_yield - baseline_proxy_yield)
                        incremental_leached_n_kg_ha = max(0.0, irrigated_leached_n - baseline_leached_n)
                        leaching_costs = irrigation_n_leaching_cost_breakdown(
                            irrigation_attributable_n_leached_kg_ha=incremental_leached_n_kg_ha,
                            grain_price_cny_per_kg=economics["grain_price_cny_per_kg"],
                            product_prices=economics.get("product_prices"),
                            yield_value_loss_cny_per_kg_n=economics["grain_price_cny_per_kg"]
                            * {
                                "establishment": 6.6,
                                "tillering": 8.0,
                                "jointing": 12.6,
                                "booting": 13.0,
                                "heading_flowering": 12.0,
                                "grain_filling": 8.8,
                                "maturity": 4.8,
                            }.get(phase, 9.5),
                        )
                        estimated_n_leaching_cost = float(leaching_costs["n_leaching_total_cost_cny_ha"])
                        if not self._is_economically_justified(
                            phase=phase,
                            raw=raw,
                            root_zone_soil_water_mm=root_snapshot["root_zone_soil_water_mm"],
                            root_depth_mm=root_depth_mm,
                            marginal_yield_gain_kg_ha=marginal_yield_gain_kg_ha,
                            recommended_depth_mm=recommended_depth_mm,
                            economics=economics,
                            prior_event_dates=[
                                pd.to_datetime(event["Date"]).date()
                                for event in (applied_events or [])
                                if event.get("Date")
                            ] + [
                                pd.to_datetime(action["recommendedIrrigationDate"]).date()
                                for action in actions
                                if action.get("recommendedIrrigationDate")
                            ],
                            prior_total_depth_mm=sum(float(event.get("gross_depth_mm", 0.0) or 0.0) for event in (applied_events or []))
                            + sum(float(action.get("recommendedGrossDepthMm", 0.0) or 0.0) for action in actions),
                            prior_events=[
                                {
                                    "date": pd.to_datetime(event["Date"]).date(),
                                    "gross_depth_mm": float(event.get("gross_depth_mm", 0.0) or 0.0),
                                }
                                for event in (applied_events or [])
                                if event.get("Date")
                            ] + [
                                {
                                    "date": pd.to_datetime(action["recommendedIrrigationDate"]).date(),
                                    "gross_depth_mm": float(action.get("recommendedGrossDepthMm", 0.0) or 0.0),
                                }
                                for action in actions
                                if action.get("recommendedIrrigationDate")
                            ],
                            planned_date=window["recommended_date"],
                            estimated_n_leaching_cost_cny_ha=estimated_n_leaching_cost,
                        ):
                            continue
                        scheduled_date = window["recommended_date"]
                        scheduled_by_date[scheduled_date] = recommended_depth_mm
                        if scheduled_date == current_date:
                            irrigation_mm = recommended_depth_mm
                            last_irrigation_date = current_date
                        else:
                            pending_action_date = scheduled_date
                        reason = f"Sequential policy trigger at BBCH {bbch} with {pre_stress['stress_risk']} stress and root-zone RAW {raw:.2f}."
                        if window["policy_reasons"]:
                            reason = f"{reason} {'; '.join(window['policy_reasons'])}."
                        actions.append(
                            {
                                "Date": str(scheduled_date),
                                "recommendationCode": "IRRIGATE",
                                "actionTypeCode": "IRRIGATION",
                                "treatmentWindowCode": "IMMEDIATE",
                                "treatmentStartDate": str(window["treatment_start_date"]),
                                "treatmentEndDate": str(window["treatment_end_date"]),
                                "recommendedIrrigationDate": str(scheduled_date),
                                "recommendedGrossDepthMm": recommended_depth_mm,
                                "recommendedNetDepthMm": round(recommended_depth_mm * method_specs["efficiency"], 3),
                                "recommendedRemainingEventCount": 0,
                                "reason": reason,
                                "expectedYieldImpactKgHa": 0.0,
                                "expectedNetReturnCnyHa": 0.0,
                            }
                        )

            wb = apply_daily_water_balance(
                soil_profile=soil_state,
                root_depth_mm=root_depth_mm,
                precipitation_mm=require_weather_value(row, "precipitation_sum", context=f"daily weather[{current_date}]"),
                irrigation_gross_mm=irrigation_mm,
                potential_soil_evaporation_mm=potential_soil_evap,
                potential_transpiration_mm=components["potential_transpiration_mm"],
                irrigation_efficiency=method_specs["efficiency"],
                root_activity_weights=root_weights,
            )
            soil_state = wb["soil_profile"]
            growth_state = update_growth_state(
                growth_state,
                current_date=current_date,
                bbch=bbch,
                mean_air_temp_c=tmean,
                radiation_sum=require_weather_value(row, "shortwave_radiation_sum", context=f"daily weather[{current_date}]"),
                actual_transpiration_mm=wb["actual_transpiration_mm"],
                potential_transpiration_mm=components["potential_transpiration_mm"],
                reference_et_mm=components["reference_et_mm"],
                root_zone_relative_available_water=wb["root_zone_relative_available_water"],
                top_layer_relative_water=float(wb["soil_water_by_layer"][0]["relative_available_water"]) if wb["soil_water_by_layer"] else wb["root_zone_relative_available_water"],
                precipitation_mm=require_weather_value(row, "precipitation_sum", context=f"daily weather[{current_date}]"),
                irrigation_mm=irrigation_mm,
                deep_percolation_mm=wb["deep_percolation_mm"],
                soil_water_by_layer=wb["soil_water_by_layer"],
                root_depth_mm=root_depth_mm,
                root_activity_weights=root_weights,
                target_yield_kg_ha=target_yield_kg_ha,
            )
            irrigation_n_leached = float(growth_state.get("irrigation_attributable_n_leached_kg_ha", 0.0) or 0.0)
            cumulative_irrigation_n_leached += irrigation_n_leached
            stress = diagnose_stress(
                wb["root_zone_relative_available_water"],
                wb["eta_actual_mm"],
                components["crop_et_potential_mm"],
                components["potential_transpiration_mm"],
                actual_transpiration_mm=wb["actual_transpiration_mm"],
            )
            daily_records.append(
                {
                    "Date": str(current_date),
                    "bbch": bbch,
                    **extract_public_growth_state(growth_state),
                    "root_depth_mm": round(root_depth_mm, 3),
                    "reference_et_mm": components["reference_et_mm"],
                    "crop_et_potential_mm": components["crop_et_potential_mm"],
                    "soil_evaporation_mm": wb["actual_soil_evaporation_mm"],
                    "potential_transpiration_mm": components["potential_transpiration_mm"],
                    "actual_transpiration_mm": wb["actual_transpiration_mm"],
                    "eta_actual_mm": wb["eta_actual_mm"],
                    "root_zone_soil_water_mm": wb["root_zone_soil_water_mm"],
                    "root_zone_relative_available_water": wb["root_zone_relative_available_water"],
                    "soil_water_by_layer": wb["soil_water_by_layer"],
                    "drainage_mm": wb["drainage_mm"],
                    "runoff_mm": wb["runoff_mm"],
                    "deep_percolation_mm": wb["deep_percolation_mm"],
                    "irrigation_attributable_n_leached_kg_ha": irrigation_n_leached,
                    "cumulative_irrigation_attributable_n_leached_kg_ha": round(cumulative_irrigation_n_leached, 3),
                    "capillary_rise_mm": wb["capillary_rise_mm"],
                    "stress_index_water": stress["stress_index_water"],
                    "stress_index_eta_ratio": stress["stress_index_eta_ratio"],
                    "stress_risk": stress["stress_risk"],
                    "irrigation_mm": round(irrigation_mm, 3),
                }
            )
        expected_yield = float(daily_records[-1]["grain_weight_kg_ha"]) if daily_records else 0.0
        economics_result = score_scenario(
            expected_yield_kg_ha=expected_yield,
            grain_price_cny_per_kg=economics["grain_price_cny_per_kg"],
            total_irrigation_mm=sum(a["recommendedGrossDepthMm"] for a in actions),
            event_count=len(actions),
            electricity_cost_cny_per_kwh=economics["electricity_cost_cny_per_kwh"],
            pump_kwh_per_mm_ha=economics["pump_kwh_per_mm_ha"],
            irrigation_event_labor_cost_cny_ha=economics["irrigation_event_labor_cost_cny_ha"],
            event_dates=[item["recommendedIrrigationDate"] for item in actions if item.get("recommendedIrrigationDate")],
            irrigation_attributable_n_leached_kg_ha=cumulative_irrigation_n_leached,
            product_prices=economics.get("product_prices"),
        )
        for action in actions:
            action["expectedNetReturnCnyHa"] = economics_result["expected_net_return_cny_ha"]
        if not actions:
            actions.append(
                {
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
                }
            )
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
            bbch = int(record.get("bbch", 0) or 0)
            phase = get_phase(bbch)
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
            row["raw_stress_risk"] = row.get("stress_risk")
            row["stress_risk"] = "IRRIGATED"
            row["irrigation_replay_event_date"] = active_event["Date"].isoformat()
            row["irrigation_replay_effect_days"] = coverage_days
            adjusted.append(row)
        return adjusted

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
        if payload.get("_skip_forward_projection"):
            simulation_end = min(season_end, decision_date + timedelta(days=10))
            gs = gs.loc[gs["Date"] <= simulation_end].copy()
            season_end = simulation_end

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
        sim_df = weather_df.loc[weather_df["Date"] <= max(weather_df["Date"])].copy()
        season_weather_df = sim_df.copy()
        if season_end and season_weather_df["Date"].max() < season_end:
            raise ValueError("weather_data must cover the season through the final growth stage date")
        historical_result = self._simulate(
            weather_df=season_weather_df,
            growth_stage_df=gs,
            soil_profile=soil_profile,
            target_yield_kg_ha=target_yield_kg_ha,
            economics=economics,
            scenario_events=applied_events,
            soil_test=payload.get("soil_test") if use_nutrition_coupling else None,
            fertilizer_history=payload.get("fertilizer_history") if use_nutrition_coupling else None,
            custom_products=payload.get("custom_products") if use_nutrition_coupling else None,
        )

        decision_record = next((r for r in historical_result["daily_records"] if r["Date"] == str(decision_date)), historical_result["daily_records"][-1])
        future_days = max(0, (weather_df["Date"].max() - decision_date).days)
        scenarios = []
        scenario_defs = generate_irrigation_scenarios(
            decision_date,
            IRRIGATION_METHOD_SPECS[self.irrigation_method],
            future_days,
            decision_record["root_zone_relative_available_water"],
            decision_record["stress_risk"],
        )
        for scenario in scenario_defs:
            events = applied_events + [{"Date": decision_date + timedelta(days=e["offset_days"]), "gross_depth_mm": e["gross_depth_mm"]} for e in scenario["events"]]
            result = self._simulate(
                weather_df=season_weather_df,
                growth_stage_df=gs,
                soil_profile=soil_profile,
                target_yield_kg_ha=target_yield_kg_ha,
                economics=economics,
                scenario_events=events,
                soil_test=payload.get("soil_test") if use_nutrition_coupling else None,
                fertilizer_history=payload.get("fertilizer_history") if use_nutrition_coupling else None,
                custom_products=payload.get("custom_products") if use_nutrition_coupling else None,
            )
            scenarios.append(
                {
                    "scenario_name": scenario["name"],
                    "events": [
                        {"Date": str(e["Date"]), "gross_depth_mm": e["gross_depth_mm"]}
                        for e in events
                    ],
                    "economics": result["economics"],
                    "summary": result["summary"],
                    "daily_tail": result["daily_records"][-10:],
                }
            )
        ranked = rank_scenarios(scenarios)
        best = ranked[0]
        scheduled_result = self._simulate_with_policy(
            weather_df=season_weather_df,
            growth_stage_df=gs,
            soil_profile=soil_profile,
            target_yield_kg_ha=target_yield_kg_ha,
            economics=economics,
            decision_date=decision_date,
            applied_events=applied_events,
            soil_test=payload.get("soil_test") if use_nutrition_coupling else None,
            fertilizer_history=payload.get("fertilizer_history") if use_nutrition_coupling else None,
            custom_products=payload.get("custom_products") if use_nutrition_coupling else None,
            full_season_recommendations=not bool(payload.get("_skip_forward_projection")),
        )
        scheduled_result["daily_records"] = self._apply_irrigation_replay_status(
            scheduled_result["daily_records"],
            applied_events,
            target_yield_kg_ha,
        )
        action_recommendations = scheduled_result["action_recommendations"]
        if action_recommendations and action_recommendations[0]["recommendationCode"] == "IRRIGATE":
            detail = action_recommendations[0].get("reason")
            summary = f"Best near-term scenario is irrigation; then follow sequential policy for later actions. Current stress is {decision_record['stress_risk']}."
            action_recommendations[0]["reason"] = summary if not detail else f"{summary} {detail}"
            action_recommendations[0]["expectedYieldImpactKgHa"] = round(best["economics"]["expected_yield_kg_ha"] - historical_result["economics"]["expected_yield_kg_ha"], 3)
            action_recommendations[0]["expectedNetReturnCnyHa"] = best["economics"]["expected_net_return_cny_ha"]

        field_status = [
            {
                "Date": r["Date"],
                "field_status": r["stress_risk"],
                "root_zone_status": r["stress_risk"],
                "forecast_risk": "WATCH" if r["stress_risk"] in {"MEDIUM", "HIGH"} else "STABLE",
            }
            for r in scheduled_result["daily_records"]
        ]
        return {
            "current_status": next((r for r in scheduled_result["daily_records"] if r["Date"] == str(decision_date)), scheduled_result["daily_records"][-1]),
            "daily_stress_risk": scheduled_result["daily_records"],
            "field_status": field_status,
            "action_recommendations": action_recommendations,
            "scenario_comparison": ranked,
            "defaults_used": defaults_used,
            "input_adjustments": input_adjustments,
            "explanation": {
                "summary": action_recommendations[0]["reason"],
                "economic_basis": "profit = grain revenue - pumping cost - direct labor - derived logistics overhead - irrigation-attributable nitrogen leaching cost",
                "irrigation_method": self.irrigation_method,
            },
        }
