# crops/maize/nutrition/maize_nutrition.py
from __future__ import annotations

from typing import Dict, List, Optional, Tuple, Any, Union, Sequence
import datetime as _dt

import numpy as np
import pandas as pd
import datetime

from api.ocm.schemas.nutrition_schema import FertilizerApplication
from core.weather_processing import DAILY_WEATHER_REQUIRED_COLUMNS, require_weather_columns
from .config import (
    FERTILIZER_PRODUCTS,
    DEFAULT_WEATHER_VARIABLES,
    DEFAULT_DAILY_STRESS_CATEGORIES_0_100,
    DEFAULT_SHORT_AGG_STRESS_CATEGORIES_0_100,
    nutrition_config,
)

# ----------------------------
# helpers
# ----------------------------
def _clamp(x: float, lo: float, hi: float) -> float:
    return float(max(lo, min(hi, x)))

def _category_from_0_100(x: float, mapping: Dict[str, str]) -> str:
    if x is None or (isinstance(x, float) and (np.isnan(x) or not np.isfinite(x))):
        return list(mapping.values())[0]
    v = float(x)
    for rng, lab in mapping.items():
        a, b = rng.split("-")
        if v >= float(a) and v < float(b):
            return lab
    if v >= 100.0 and "60-100" in mapping:
        return mapping["60-100"]
    return list(mapping.values())[-1]

def _stage_to_band(stage: str) -> str:
    if not isinstance(stage, str) or not stage:
        return "VE_V4"
    s = stage.strip().upper()
    if s in {"VS", "VE"}:
        return "VE_V4"
    if s.startswith("V"):
        num = s[1:].replace("+", "")
        try:
            vnum = int(num)
        except ValueError:
            return "VE_V4"
        if vnum <= 4:
            return "VE_V4"
        if vnum <= 8:
            return "V5_V8"
        if vnum <= 12:
            return "V9_V12"
        return "V13_VT"
    if s in {"VT", "R1", "R2", "R3"}:
        return "R1_R3"
    if s in {"R4", "R5", "R6"}:
        return "R4_R6"
    return "VE_V4"

def _rolling_mean(series: pd.Series, k: int) -> pd.Series:
    return series.rolling(int(max(1, k)), min_periods=1).mean()


# ----------------------------
# main class
# ----------------------------
class MaizeNutrition:
    """
    Single-nutrient (target) daily nutrition stress model.
    target: 'N' / 'P2O5' / 'K2O' / 'S' / 'Zn' / 'B'
    """

    def __init__(self, target_code: str):
        if target_code not in nutrition_config:
            raise ValueError(f"Unknown nutrient target '{target_code}'. Available: {list(nutrition_config.keys())}")
        self.target = target_code
        self.cfg: Dict[str, Any] = nutrition_config[target_code]

        self.daily_cats = self.cfg.get("daily_category", DEFAULT_DAILY_STRESS_CATEGORIES_0_100)
        self.short_cats = self.cfg.get("short_agg_category", DEFAULT_SHORT_AGG_STRESS_CATEGORIES_0_100)
        self.short_k = int(self.cfg.get("short_agg_days", 5))
        self.weather_variable_list = DEFAULT_WEATHER_VARIABLES

    # ----------------------------
    # normalizers
    # ----------------------------
    def _normalize_daily(self, weather_daily: pd.DataFrame) -> pd.DataFrame:
        if weather_daily is None or weather_daily.empty:
            raise ValueError("weather_daily must be a non-empty DataFrame.")
        df = weather_daily.copy()

        if "Date" not in df.columns:
            if "DateTime" in df.columns:
                df["Date"] = pd.to_datetime(df["DateTime"]).dt.date
            elif "time" in df.columns:
                df["Date"] = pd.to_datetime(df["time"]).dt.date
            else:
                raise ValueError("weather_daily must contain 'Date' or 'DateTime' or 'time'.")
        else:
            df["Date"] = pd.to_datetime(df["Date"]).dt.date

        for col in DAILY_WEATHER_REQUIRED_COLUMNS:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        require_weather_columns(df, DAILY_WEATHER_REQUIRED_COLUMNS, label="daily weather")

        return df.sort_values("Date").reset_index(drop=True)

    def _normalize_growth_stage(self, growth_stage: pd.DataFrame) -> Dict[_dt.date, str]:
        if growth_stage is None or growth_stage.empty:
            raise ValueError("growth_stage DataFrame must not be None or empty")
        gs = growth_stage.copy()
        if "Date" not in gs.columns or "Stage" not in gs.columns:
            raise ValueError("growth_stage must have columns ['Date','Stage']")
        gs["Date"] = pd.to_datetime(gs["Date"]).dt.date
        return gs.set_index("Date")["Stage"].to_dict()

    def _normalize_events(
        self,
        fert_events: Union[Sequence[FertilizerApplication], List[Dict], pd.DataFrame, None]
    ) -> List[FertilizerApplication]:
        """
        Normalize to List[FertilizerApplication].

        Accepts:
        - already-validated List[FertilizerApplication]
        - list[dict] in older or current shapes
        - pd.DataFrame

        Older keys handled:
        - 'date' → 'Date'
        - 'dose_value' + 'dose_unit' → 'amount_kg_ha' (kg_ha direct; g_ha ÷1000)
        - 'amount' → 'amount_kg_ha'
        - unknown keys dropped before validation
        """
        if fert_events is None:
            return []

        # already schema objects?
        if isinstance(fert_events, list) and fert_events and isinstance(fert_events[0], FertilizerApplication):
            return fert_events

        # materialize records
        if isinstance(fert_events, pd.DataFrame):
            records = fert_events.to_dict(orient="records")
        else:
            records = list(fert_events)

        allowed = {
            "Date",
            "product_uuid",
            "product_key",
            "product_name",
            "amount_kg_ha",
            "application_method",
            "method",
            "nutrients_kg_ha",
            "micros_g_ha",
            "target_nutrients",
            "release_type",
            "release_days",
            "notes",
        }

        def _coerce_amount_kg_ha(r: Dict[str, Any]) -> float:
            # new field already present
            if "amount_kg_ha" in r and r["amount_kg_ha"] is not None:
                try:
                    return float(r["amount_kg_ha"])
                except Exception:
                    return 0.0
            # older payload: amount
            if "amount" in r and r["amount"] is not None:
                try:
                    return float(r["amount"])
                except Exception:
                    return 0.0
            # older payload: dose_value + dose_unit
            dv = r.get("dose_value", None)
            du = (r.get("dose_unit") or "").lower() if r.get("dose_unit") is not None else ""
            try:
                val = float(dv) if dv is not None else None
            except Exception:
                val = None
            if val is None:
                return 0.0
            if du in ("kg_ha", "kg/ha"):
                return val
            if du in ("g_ha", "g/ha"):
                return val / 1000.0  # g/ha → kg/ha
            # other units (ml/L_ha etc.) are not supported by this schema → ignore gracefully
            return 0.0

        out: List[FertilizerApplication] = []
        for raw in records:
            # work on a copy
            r = dict(raw) if isinstance(raw, dict) else dict(raw or {})

            # normalize Date key
            if "Date" not in r and "date" in r:
                r["Date"] = r.pop("date")

            # parse date into python date if it’s a string/ts
            if "Date" in r:
                try:
                    r["Date"] = pd.to_datetime(r["Date"]).date().isoformat()
                except Exception:
                    # let pydantic deal with validation errors later
                    pass

            # default method
            method = str(r.get("method") or r.get("application_method") or "soil").lower()
            if method in {"foliar_spray", "leaf_spray", "leaf", "foliar"}:
                method = "foliar"
                r.setdefault("application_method", "foliar_spray")
            r["method"] = method

            # compute amount_kg_ha from alternate shapes when missing
            r["amount_kg_ha"] = _coerce_amount_kg_ha(r)

            # keep only schema fields
            cleaned = {k: r[k] for k in allowed if k in r}

            # pass through explicit per-nutrient dicts if present & valid
            if "nutrients_kg_ha" in cleaned and not isinstance(cleaned["nutrients_kg_ha"], dict):
                cleaned["nutrients_kg_ha"] = None
            if "micros_g_ha" in cleaned and not isinstance(cleaned["micros_g_ha"], dict):
                cleaned["micros_g_ha"] = None

            # finally validate to FertilizerApplication
            try:
                fa = FertilizerApplication.model_validate(cleaned)
            except Exception as e:
                # If amount is zero (e.g., dose_value was None), skip silently
                if cleaned.get("amount_kg_ha", 0) <= 0:
                    continue
                raise ValueError(f"Invalid fertilizer application record {cleaned}: {e}") from e

            has_micros = bool(fa.micros_g_ha and any(float(value or 0.0) > 0.0 for value in fa.micros_g_ha.values()))
            # drop zero-dose events after validation (safe guard)
            if (fa.amount_kg_ha and fa.amount_kg_ha > 0) or has_micros:
                out.append(fa)

        return out

    def _events_for_target(
        self,
        applied_fertilizers: Union[Sequence[FertilizerApplication], List[Dict], pd.DataFrame, None],
    ) -> List[FertilizerApplication]:
        """
        Return ONLY the fertilizer applications that deliver THIS target.
        Relevance test:
          - If event provides explicit mass for THIS target (nutrients_kg_ha or micros_g_ha) and >0 → keep.
          - Else, keep if product's nutrient fractions contain THIS target (frac > 0).
        """
        events = self._normalize_events(applied_fertilizers)
        if not events:
            return []
        out: List[FertilizerApplication] = []
        for ev in events:
            if self.target in ev.nutrients_kg_ha and ev.nutrients_kg_ha[self.target] > 0.0:
                out.append(ev)
        return out

    def _normalize_irrigation_events(self, irrigation_events: Union[List[Dict], pd.DataFrame, None]) -> List[Dict[str, Any]]:
        if irrigation_events is None:
            return []
        if isinstance(irrigation_events, pd.DataFrame):
            records = irrigation_events.to_dict(orient="records")
        else:
            records = list(irrigation_events)

        out: List[Dict[str, Any]] = []
        for raw in records:
            r = dict(raw or {})
            raw_date = r.get("date", r.get("Date"))
            if raw_date is None:
                continue
            try:
                event_date = pd.to_datetime(raw_date).date()
            except Exception:
                continue
            amount = (
                r.get("amount_mm")
                if r.get("amount_mm") is not None
                else r.get("water_mm", r.get("irrigation_mm", r.get("gross_depth_mm", r.get("net_depth_mm", 0.0))))
            )
            try:
                amount_mm = float(amount or 0.0)
            except (TypeError, ValueError):
                amount_mm = 0.0
            if amount_mm <= 0.0:
                continue
            out.append({
                "date": event_date,
                "amount_mm": amount_mm,
                "method": str(r.get("method") or r.get("irrigation_method") or "").lower(),
            })
        return out

    def _irrigation_by_day(
        self,
        days: List[_dt.date],
        irrigation_events: Union[List[Dict], pd.DataFrame, None],
    ) -> Dict[_dt.date, Dict[str, Any]]:
        day_set = set(days)
        out = {d: {"amount_mm": 0.0, "methods": set()} for d in days}
        for ev in self._normalize_irrigation_events(irrigation_events):
            d = ev["date"]
            if d not in day_set:
                continue
            out[d]["amount_mm"] += float(ev["amount_mm"])
            if ev.get("method"):
                out[d]["methods"].add(ev["method"])
        return out

    # ----------------------------
    # demand / timeline
    # ----------------------------
    def _build_daily_timeline(
        self,
        start_date: _dt.date,
        end_date: _dt.date,
        gs_map: Dict[_dt.date, str],
        dfd: pd.DataFrame,
    ) -> Tuple[List[_dt.date], Dict[_dt.date, pd.Series]]:
        if start_date > end_date:
            return [], {}
        days = pd.date_range(start=start_date, end=end_date, freq="D").date
        missing = set(days) - set(gs_map.keys())
        if missing:
            raise ValueError(f"Missing growth stage entries for dates: {sorted(missing)}")
        day_rows = {d: dfd.loc[dfd["Date"] == d].squeeze() for d in days}
        return list(days), day_rows

    def _seasonal_demand_total(self, yield_target_t_ha: float) -> float:
        # N/P2O5/K2O/S: kg/ha ; Zn/B: g/ha
        if self.target in ("Zn", "B"):
            coeff = float(self.cfg.get("yield_coeff_g_per_t", 0.0))
        else:
            coeff = float(self.cfg.get("yield_coeff", 0.0))
        return max(0.0, float(yield_target_t_ha) * coeff)

    def _daily_demand_curve(
        self,
        days: List[_dt.date],
        gs_map: Dict[_dt.date, str],
        seasonal_total: float,
    ) -> Dict[_dt.date, float]:
        shares = (self.cfg.get("uptake_share_by_band") or [])
        band_order = ["VE_V4", "V5_V8", "V9_V12", "V13_VT", "R1_R3", "R4_R6"]
        if not shares or len(shares) != len(band_order):
            return {d: seasonal_total / max(len(days), 1) for d in days}

        share_map = dict(zip(band_order, shares))
        band_days: Dict[str, int] = {}
        day_band = {}
        for d in days:
            b = _stage_to_band(gs_map[d])
            day_band[d] = b
            band_days[b] = band_days.get(b, 0) + 1

        day_dem: Dict[_dt.date, float] = {}
        for b in band_order:
            if band_days.get(b, 0) <= 0:
                continue
            band_total = seasonal_total * float(share_map.get(b, 0.0))
            per_day = band_total / max(band_days[b], 1)
            for d in days:
                if day_band[d] == b:
                    day_dem[d] = float(per_day)

        if day_dem:
            scale = seasonal_total / max(1e-9, sum(day_dem.values()))
            for d in day_dem:
                day_dem[d] *= scale
        return day_dem if day_dem else {d: seasonal_total / max(len(days), 1) for d in days}

    # ----------------------------
    # soil supply (daily)
    # ----------------------------
    @staticmethod
    def _soil_value(soil_status: Optional[dict], *keys: str) -> Optional[float]:
        if not soil_status:
            return None
        for key in keys:
            value = soil_status.get(key)
            if value is None:
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
        return None

    def _seasonal_soil_n_supply(self, soil_status: Optional[dict]) -> Optional[float]:
        if not soil_status:
            return None

        mineral_n = self._soil_value(soil_status, "mineral_n_kg_ha")
        if mineral_n is None:
            alkali_n = self._soil_value(soil_status, "alkali_hydrolyzable_n_mg_kg")
            if alkali_n is not None:
                mineral_n = alkali_n * 0.32

        organic_matter = self._soil_value(soil_status, "organic_matter_g_kg")
        if mineral_n is None or organic_matter is None:
            return None

        return max(0.0, min(150.0, mineral_n * 0.72 + organic_matter * 2.3))

    def _soil_supply_curve(
        self,
        days: List[_dt.date],
        demand_curve: Dict[_dt.date, float],
        day_rows: Dict[_dt.date, pd.Series],
        soil_status: Optional[dict],
    ) -> Dict[_dt.date, float]:
        if self.target != "N":
            return {d: float(self._soil_supply_day(d, day_rows.get(d))) for d in days}

        seasonal_supply = self._seasonal_soil_n_supply(soil_status)
        if seasonal_supply is None:
            return {d: float(self._soil_supply_day(d, day_rows.get(d))) for d in days}

        demand_total = sum(float(demand_curve.get(d, 0.0)) for d in days)
        if demand_total <= 0.0:
            return {d: 0.0 for d in days}

        seasonal_supply = min(seasonal_supply, demand_total)
        return {
            d: seasonal_supply * float(demand_curve.get(d, 0.0)) / demand_total
            for d in days
        }

    def _n_balance_curve(
        self,
        days: List[_dt.date],
        demand_curve: Dict[_dt.date, float],
        day_rows: Dict[_dt.date, pd.Series],
        soil_status: Optional[dict],
        rel_map: Dict[_dt.date, float],
        irrigation_events: Union[List[Dict], pd.DataFrame, None],
        water_records: Optional[Dict[_dt.date, Dict[str, Any]]] = None,
    ) -> Optional[Dict[_dt.date, Dict[str, float]]]:
        if self.target != "N":
            return None
        if not soil_status:
            return None

        mineral_n = self._soil_value(soil_status, "mineral_n_kg_ha")
        if mineral_n is None:
            alkali_n = self._soil_value(soil_status, "alkali_hydrolyzable_n_mg_kg")
            if alkali_n is not None:
                mineral_n = alkali_n * 0.32
        organic_matter = self._soil_value(soil_status, "organic_matter_g_kg")
        if mineral_n is None or organic_matter is None:
            return None

        initial_soil_pool = max(0.0, float(mineral_n) * 0.72)
        seasonal_mineralization = max(0.0, min(95.0, float(organic_matter) * 2.3))
        demand_total = max(1e-9, sum(float(demand_curve.get(d, 0.0)) for d in days))
        irrigation_by_day = self._irrigation_by_day(days, irrigation_events)

        soil_pool = initial_soil_pool
        fertilizer_pool = 0.0
        out: Dict[_dt.date, Dict[str, float]] = {}

        for d in days:
            drow = day_rows.get(d)
            if drow is None or drow.empty:
                raise ValueError(f"daily weather missing for {d}")

            demand = max(0.0, float(demand_curve.get(d, 0.0)))
            temp = float(drow["temperature_2m_mean"])
            temp_factor = _clamp((temp - 5.0) / 25.0, 0.0, 1.0)
            demand_share = max(0.0, float(demand_curve.get(d, 0.0)) / demand_total)
            mineralization = seasonal_mineralization * demand_share * (0.45 + 0.55 * temp_factor)
            soil_pool += mineralization

            fertilizer_release = max(0.0, float(rel_map.get(d, 0.0)))
            fertilizer_pool += fertilizer_release

            precip = float(drow.get("precipitation_sum", 0.0) or 0.0)
            irrigation = irrigation_by_day.get(d, {"amount_mm": 0.0, "methods": set()})
            irrigation_mm = float(irrigation.get("amount_mm", 0.0) or 0.0)
            methods = {str(m).lower() for m in irrigation.get("methods", set())}
            flood_like = any(m in {"flood", "border", "basin", "surface", "furrow"} for m in methods)
            water_mm = max(0.0, precip + irrigation_mm)

            wb = (water_records or {}).get(d, {})
            if wb:
                irrigation_mm = float(wb.get("irrigation_mm", irrigation_mm) or 0.0)
                hydraulic_loss_mm = max(0.0, float(wb.get("deep_percolation_mm", 0.0) or 0.0) + 0.25 * float(wb.get("runoff_mm", 0.0) or 0.0))
                irrigation_share = 0.0 if water_mm <= 0.0 else irrigation_mm / max(water_mm, 1e-9)
                method_multiplier = 1.15 if flood_like else 1.0
                leaching_fraction = _clamp((hydraulic_loss_mm / 120.0) * (0.75 + 0.25 * irrigation_share) * method_multiplier, 0.0, 0.42)
            else:
                drainage_mm = max(0.0, water_mm - 25.0)
                irrigation_share = 0.0 if water_mm <= 0.0 else irrigation_mm / water_mm
                method_multiplier = 1.45 if flood_like else 1.0
                leaching_fraction = _clamp((drainage_mm / 100.0) * (0.55 + 0.35 * irrigation_share) * method_multiplier, 0.0, 0.42)
            mobile_pool = soil_pool + fertilizer_pool
            leaching_loss = mobile_pool * leaching_fraction
            if leaching_loss > 0.0 and mobile_pool > 0.0:
                soil_loss = leaching_loss * soil_pool / mobile_pool
                fert_loss = leaching_loss - soil_loss
                soil_pool = max(0.0, soil_pool - soil_loss)
                fertilizer_pool = max(0.0, fertilizer_pool - fert_loss)

            available_pool = soil_pool + fertilizer_pool
            uptake = min(demand, available_pool)
            fertilizer_uptake = min(fertilizer_pool, uptake)
            soil_uptake = uptake - fertilizer_uptake
            fertilizer_pool = max(0.0, fertilizer_pool - fertilizer_uptake)
            soil_pool = max(0.0, soil_pool - soil_uptake)

            out[d] = {
                "soil_supply": soil_uptake,
                "fertilizer_release": fertilizer_release,
                "fertilizer_uptake": fertilizer_uptake,
                "available": uptake,
                "mineralization": mineralization,
                "leaching_loss": leaching_loss,
                "precipitation_mm": precip,
                "irrigation_mm": irrigation_mm,
                "runoff_mm": float(wb.get("runoff_mm", 0.0) or 0.0) if wb else 0.0,
                "drainage_mm": float(wb.get("drainage_mm", 0.0) or 0.0) if wb else max(0.0, water_mm - 25.0),
                "deep_percolation_mm": float(wb.get("deep_percolation_mm", 0.0) or 0.0) if wb else max(0.0, water_mm - 25.0),
                "root_zone_relative_available_water": float(wb.get("root_zone_relative_available_water", 0.0) or 0.0) if wb else 0.0,
                "soil_pool_end": soil_pool,
                "fertilizer_pool_end": fertilizer_pool,
            }
        return out

    def _residual_pool_balance_curve(
        self,
        days: List[_dt.date],
        demand_curve: Dict[_dt.date, float],
        day_rows: Dict[_dt.date, pd.Series],
        rel_map: Dict[_dt.date, float],
        water_records: Optional[Dict[_dt.date, Dict[str, Any]]] = None,
    ) -> Optional[Dict[_dt.date, Dict[str, float]]]:
        if self.cfg.get("soil_applied_fertilizer_model") != "residual_pool":
            return None

        soil_pool = 0.0
        fertilizer_pool = 0.0
        out: Dict[_dt.date, Dict[str, float]] = {}

        for d in days:
            drow = day_rows.get(d)
            if drow is None or drow.empty:
                raise ValueError(f"daily weather missing for {d}")

            demand = max(0.0, float(demand_curve.get(d, 0.0)))
            soil_pool += max(0.0, float(self._soil_supply_day(d, drow)))
            fertilizer_release = max(0.0, float(rel_map.get(d, 0.0)))
            fertilizer_pool += fertilizer_release

            wb = (water_records or {}).get(d, {})
            deep_percolation = float(wb.get("deep_percolation_mm", 0.0) or 0.0) if wb else 0.0
            runoff = float(wb.get("runoff_mm", 0.0) or 0.0) if wb else 0.0
            loss_cfg = self.cfg.get("residual_pool_loss") or {}
            runoff_weight = float(loss_cfg.get("runoff_weight", 0.10) or 0.0)
            scale_mm = max(1e-6, float(loss_cfg.get("hydraulic_loss_scale_mm", 250.0) or 250.0))
            max_loss_fraction = max(0.0, float(loss_cfg.get("max_daily_loss_fraction", 0.08) or 0.0))
            hydraulic_loss_mm = max(0.0, deep_percolation + runoff_weight * runoff)
            leaching_fraction = _clamp(hydraulic_loss_mm / scale_mm, 0.0, max_loss_fraction)

            mobile_pool = soil_pool + fertilizer_pool
            leaching_loss = mobile_pool * leaching_fraction
            if leaching_loss > 0.0 and mobile_pool > 0.0:
                soil_loss = leaching_loss * soil_pool / mobile_pool
                fert_loss = leaching_loss - soil_loss
                soil_pool = max(0.0, soil_pool - soil_loss)
                fertilizer_pool = max(0.0, fertilizer_pool - fert_loss)

            available_pool = soil_pool + fertilizer_pool
            uptake = min(demand, available_pool)
            fertilizer_uptake = min(fertilizer_pool, uptake)
            soil_uptake = uptake - fertilizer_uptake
            fertilizer_pool = max(0.0, fertilizer_pool - fertilizer_uptake)
            soil_pool = max(0.0, soil_pool - soil_uptake)

            out[d] = {
                "soil_supply": soil_uptake,
                "fertilizer_release": fertilizer_release,
                "fertilizer_uptake": fertilizer_uptake,
                "available": uptake,
                "mineralization": 0.0,
                "leaching_loss": leaching_loss,
                "precipitation_mm": float(drow.get("precipitation_sum", 0.0) or 0.0),
                "irrigation_mm": float(wb.get("irrigation_mm", 0.0) or 0.0) if wb else 0.0,
                "runoff_mm": runoff,
                "drainage_mm": float(wb.get("drainage_mm", 0.0) or 0.0) if wb else 0.0,
                "deep_percolation_mm": deep_percolation,
                "root_zone_relative_available_water": float(wb.get("root_zone_relative_available_water", 0.0) or 0.0) if wb else 0.0,
                "soil_pool_end": soil_pool,
                "fertilizer_pool_end": fertilizer_pool,
            }
        return out

    def _shared_water_records(
        self,
        *,
        weather_daily: pd.DataFrame,
        growth_stage: pd.DataFrame,
        soil_profile: Optional[List[Dict[str, Any]]],
        irrigation_events: Union[List[Dict], pd.DataFrame, None],
        yield_target_t_ha: float,
        latitude: Optional[float],
        longitude: Optional[float],
        soil_type: Optional[str],
        irrigation_method: Optional[str],
    ) -> Optional[Dict[_dt.date, Dict[str, Any]]]:
        if not soil_profile:
            return None
        try:
            from core.soil_hydraulics import validate_and_prepare_profile
            from crops.maize.water_config import IRRIGATION_METHOD_SPECS
            from crops.maize.irrigation.maize_irrigation import MaizeIrrigationModel
        except Exception:
            return None

        method = str(irrigation_method or "sprinkler").strip().lower()
        if method not in IRRIGATION_METHOD_SPECS:
            method = "sprinkler"
        soil_name = str(soil_type or "sandy_loam").strip().lower().replace(" ", "_")
        try:
            prepared_profile, _, _ = validate_and_prepare_profile(soil_name, soil_profile)
            scenario_events = [
                {"Date": item["date"], "gross_depth_mm": item["amount_mm"]}
                for item in self._normalize_irrigation_events(irrigation_events)
            ]
            model = MaizeIrrigationModel(
                latitude=float(latitude if latitude is not None else 38.15),
                longitude=float(longitude if longitude is not None else 114.57),
                soil_type=soil_name,
                irrigation_method=method,
            )
            result = model._simulate(
                weather_df=weather_daily,
                growth_stage_df=growth_stage,
                soil_profile=prepared_profile,
                target_yield_kg_ha=float(yield_target_t_ha) * 1000.0,
                economics={
                    "grain_price_cny_per_kg": 2.2,
                    "electricity_cost_cny_per_kwh": 0.65,
                    "pump_kwh_per_mm_ha": 1.1,
                    "irrigation_event_labor_cost_cny_ha": 80.0,
                },
                scenario_events=scenario_events,
                soil_test={
                    "mineral_n_kg_ha": 500.0,
                    "organic_matter_g_kg": 25.0,
                    "olsen_p_mg_kg": 60.0,
                    "available_k_mg_kg": 320.0,
                    "ph": 7.5,
                },
                fertilizer_history=None,
                custom_products=None,
            )
        except Exception:
            return None
        records = {}
        for row in result.get("daily_records") or []:
            try:
                records[pd.to_datetime(row["Date"]).date()] = row
            except Exception:
                continue
        return records or None

    def _growth_demand_curve(
        self,
        days: List[_dt.date],
        water_records: Optional[Dict[_dt.date, Dict[str, Any]]],
        seasonal_total: float,
    ) -> Optional[Dict[_dt.date, float]]:
        if not water_records or seasonal_total <= 0.0:
            return None
        field_by_target = {
            "N": "daily_n_demand_kg_ha",
            "P2O5": "daily_p2o5_demand_kg_ha",
            "K2O": "daily_k2o_demand_kg_ha",
        }
        field = field_by_target.get(self.target)
        if not field:
            return None
        raw = {d: max(0.0, float((water_records.get(d) or {}).get(field, 0.0) or 0.0)) for d in days}
        raw_total = sum(raw.values())
        if raw_total <= 0.0:
            return None
        return {d: seasonal_total * raw[d] / raw_total for d in days}

    def _soil_supply_day(self, d: _dt.date, drow: Optional[pd.Series]) -> float:
        ss = (self.cfg.get("soil_supply") or {})
        if self.target == "N":
            base = float(ss.get("base_kg_ha_day", 0.0))
            tref = float(ss.get("temp_ref", 20.0))
            q10 = float(ss.get("Q10", 2.0))
            if drow is None or drow.empty:
                raise ValueError(f"daily weather missing for {d}")
            else:
                Tmean = drow["temperature_2m_mean"]
                if pd.isna(Tmean):
                    raise ValueError(f"daily weather[{d}].temperature_2m_mean is required")
                Tmean = float(Tmean)
            return base * (q10 ** ((Tmean - tref) / 10.0))
        elif self.target in {"P2O5", "K2O", "S"}:
            return float(ss.get("base_kg_ha_day", 0.0))
        else:  # Zn / B → g/ha/day
            return float(ss.get("base_g_ha_day", 0.0))

    # ----------------------------
    # fertilizer release (daily)
    # ----------------------------
    def _release_from_events(self, days: List[_dt.date], events: List[FertilizerApplication]) -> Dict[_dt.date, float]:
        """
        Events are already filtered for THIS target.
        Returns a {date: released_amount_in_target_unit}.
        """
        if not events:
            return {d: 0.0 for d in days}
        rel = {d: 0.0 for d in days}
        day_set = set(days)

        for ev in events:
            prod = FERTILIZER_PRODUCTS.get(ev.product_key, {})
            ev_date = pd.to_datetime(ev.Date).date()
            # 1) total nutrient mass for THIS target from this event
            total_nutrient: float
            if self.target in {"N", "P2O5", "K2O"}:
                total_nutrient = ev.nutrients_kg_ha[self.target]  # kg/ha
            else:  # Zn / B
                # explicit g/ha override?
                if ev.micros_g_ha and self.target in ev.micros_g_ha:
                    total_nutrient = max(0.0, float(ev.micros_g_ha[self.target]))  # g/ha
                else:
                    frac = float((prod.get("nutrients") or {}).get(self.target, 0.0))
                    if frac <= 0.0 or ev.amount_kg_ha <= 0.0:
                        continue
                    total_nutrient = ev.amount_kg_ha * frac * 1000.0  # kg→g
            # 2) release duration
            dur_key = "release_days_foliar" if ev.method == "foliar" else "release_days_soil"
            default_dur = 1 if ev.method == "foliar" else 30
            if ev.method != "foliar" and self.cfg.get("soil_applied_fertilizer_model") == "residual_pool":
                # The daily balance model controls availability from this pool.
                default_dur = 1
            dur = int(prod.get(dur_key, default_dur))
            dur = max(1, dur)

            per_day = total_nutrient / dur
            for i in range(dur):
                d = ev_date + _dt.timedelta(days=i)
                if d in day_set:
                    rel[d] += per_day
        return rel

    # ----------------------------
    # stress index
    # ----------------------------
    def _stress_index_0_100(self, demand: float, available: float) -> float:
        if demand <= 0:
            return 0.0
        r = available / max(1e-9, demand)
        return _clamp(100.0 * (1.0 - _clamp(r, 0.0, 1.0)), 0.0, 100.0)

    def _fertilizer_effects_on_day(
        self,
        day: _dt.date,
        applied_fertilizers: Union[Sequence[FertilizerApplication], List[Dict], pd.DataFrame, None],
    ) -> bool:
        """
        True if `day` ∈ [application_date, application_date + window] for ANY target-relevant event.
        Window (days) from product['fertilized_status_days_after_fertilization']; fallback: soil=3, foliar=1.
        """
        events = self._events_for_target(applied_fertilizers)
        if not events:
            return False

        for ev in events:
            prod = FERTILIZER_PRODUCTS.get(ev.product_key, {})
            win = int(prod.get("fertilized_status_days_after_fertilization", 1 if ev.method == "foliar" else 3))
            ev_date = pd.to_datetime(ev.Date).date()
            if ev_date <= day <= (ev_date + _dt.timedelta(days=win)):
                return True
        return False

    # ----------------------------
    # public: daily simulation
    # ----------------------------
    def simulate_nutrition_daily_stress(
        self,
        *,
        planting_date: Optional[_dt.date],
        weather_daily: pd.DataFrame,
        growth_stage: pd.DataFrame,
        yield_target_t_ha: float,
        applied_fertlizers: Union[Sequence[FertilizerApplication], List[Dict], pd.DataFrame, None] = None,
        soil_status: Optional[dict] = None,
        irrigation_events: Union[List[Dict], pd.DataFrame, None] = None,
        soil_profile: Optional[List[Dict[str, Any]]] = None,
        soil_type: Optional[str] = None,
        irrigation_method: Optional[str] = None,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
    ) -> pd.DataFrame:
        """
        Output columns:
        Date,target_code,unit,gstage,demand,soil_supply,fertilizer_release,available,
        stress_index,categorized_daily_stress,shortterm_aggregate_stress,categorized_shortterm_aggregate_stress
        """
        dfd = self._normalize_daily(weather_daily)
        gs_map = self._normalize_growth_stage(growth_stage)
        gs_df = growth_stage.copy()
        gs_df["Date"] = pd.to_datetime(gs_df["Date"]).dt.date

        start_date = planting_date or dfd["Date"].min()
        end_date = min(max(gs_map.keys()), dfd["Date"].max())

        days, day_rows = self._build_daily_timeline(start_date, end_date, gs_map, dfd)
        if not days:
            return pd.DataFrame()

        seasonal_total = self._seasonal_demand_total(yield_target_t_ha)
        demand_curve = self._daily_demand_curve(days, gs_map, seasonal_total)

        # events only for THIS target
        target_events = self._events_for_target(applied_fertlizers)
        rel_map = self._release_from_events(days, target_events)
        water_records = self._shared_water_records(
            weather_daily=dfd,
            growth_stage=gs_df,
            soil_profile=soil_profile,
            irrigation_events=irrigation_events,
            yield_target_t_ha=yield_target_t_ha,
            latitude=latitude,
            longitude=longitude,
            soil_type=soil_type,
            irrigation_method=irrigation_method,
        ) if self.target in {"N", "P2O5", "K2O"} else None
        growth_demand_curve = self._growth_demand_curve(days, water_records, seasonal_total)
        if growth_demand_curve:
            demand_curve = growth_demand_curve
        balance_curve = self._n_balance_curve(
            days,
            demand_curve,
            day_rows,
            soil_status,
            rel_map,
            irrigation_events,
            water_records,
        )
        if balance_curve is None:
            balance_curve = self._residual_pool_balance_curve(
                days,
                demand_curve,
                day_rows,
                rel_map,
                water_records,
            )
        soil_curve = {} if balance_curve else self._soil_supply_curve(days, demand_curve, day_rows, soil_status)

        rows = []
        for d in days:
            gstage = gs_map[d]
            demand = float(demand_curve.get(d, 0.0))
            if balance_curve:
                balance = balance_curve[d]
                soil = float(balance["soil_supply"])
                fert = float(balance["fertilizer_release"])
                available = float(balance["available"])
            else:
                balance = {}
                soil = float(soil_curve.get(d, 0.0))
                fert = float(rel_map.get(d, 0.0))
                available = soil + fert
            stress = self._stress_index_0_100(demand, available)
            fertilizer_effects = self._fertilizer_effects_on_day(d, target_events)

            rows.append({
                "Date": d,
                "target_code": self.target,
                "unit": self.cfg.get("unit", "kg_ha"),
                "gstage": gstage,
                "demand": round(demand, 3),
                "soil_supply": round(soil, 3),
                "fertilizer_release": round(fert, 3),
                "fertilizer_effects": fertilizer_effects,
                "available": round(available, 3),
                "stress_index": round(stress, 2),
                "crop_uptake": round(available, 3),
                "fertilizer_uptake": round(float(balance.get("fertilizer_uptake", 0.0)), 3),
                "mineralization": round(float(balance.get("mineralization", 0.0)), 3),
                "leaching_loss": round(float(balance.get("leaching_loss", 0.0)), 3),
                "precipitation_mm": round(float(balance.get("precipitation_mm", 0.0)), 3),
                "irrigation_mm": round(float(balance.get("irrigation_mm", 0.0)), 3),
                "runoff_mm": round(float(balance.get("runoff_mm", 0.0)), 3),
                "drainage_mm": round(float(balance.get("drainage_mm", 0.0)), 3),
                "deep_percolation_mm": round(float(balance.get("deep_percolation_mm", 0.0)), 3),
                "root_zone_relative_available_water": round(float(balance.get("root_zone_relative_available_water", 0.0)), 4),
                "soil_pool_end": round(float(balance.get("soil_pool_end", 0.0)), 3),
                "fertilizer_pool_end": round(float(balance.get("fertilizer_pool_end", 0.0)), 3),
            })

        out = pd.DataFrame(rows).sort_values("Date").reset_index(drop=True)
        out["shortterm_aggregate_stress"] = _rolling_mean(out["stress_index"], self.short_k)
        out["categorized_daily_stress"] = out["stress_index"].apply(lambda x: _category_from_0_100(x, self.daily_cats))
        out["categorized_shortterm_aggregate_stress"] = out["shortterm_aggregate_stress"].apply(
            lambda x: _category_from_0_100(x, self.short_cats)
        )

        return out[
            [
                "Date", "target_code", "unit", "gstage",
                "demand", "soil_supply", "fertilizer_release", "fertilizer_effects", "available",
                "stress_index", "categorized_daily_stress",
                "crop_uptake", "fertilizer_uptake", "mineralization", "leaching_loss",
                "precipitation_mm", "irrigation_mm", "runoff_mm", "drainage_mm",
                "deep_percolation_mm", "root_zone_relative_available_water",
                "soil_pool_end", "fertilizer_pool_end",
                "shortterm_aggregate_stress", "categorized_shortterm_aggregate_stress",
            ]
        ].replace({np.nan: None})

    # ----------------------------
    # public: stress risk (categorical)
    # ----------------------------
    def estimate_stress_risk(self, nutition_daily_risk: pd.DataFrame):
        from core.utils import get_nutrition_status_and_code
        stress_code = get_nutrition_status_and_code(category='stress_code')   # {1:'UNFAVORABLE',...,4:'PROTECTED'}
        code_stress = get_nutrition_status_and_code(category='code_stress')   # {'UNFAVORABLE':1,...}

        df = nutition_daily_risk.copy()
        need = {'Date','target_code','fertilizer_effects','categorized_shortterm_aggregate_stress'}
        for c in need - set(df.columns):
            df[c] = None

        out = df[['Date','target_code','fertilizer_effects','categorized_shortterm_aggregate_stress']].copy()
        out['stress_risk'] = out['categorized_shortterm_aggregate_stress']

        # mark protected during fertilizer-effect window
        out.loc[out['fertilizer_effects'] == True, 'stress_risk'] = stress_code[4]

        # no sudden downgrade unless protected yesterday
        out = out.sort_values('Date').reset_index(drop=True)
        for i in range(1, len(out)):
            today = out.loc[i, 'Date']
            yest = today + datetime.timedelta(days=-1)
            today_status = out.loc[i, 'stress_risk']
            try:
                yesterday_status = out.loc[out['Date'] == yest, 'stress_risk'].values[0]
            except Exception:
                continue
            if (
                code_stress.get(str(today_status), 0) < code_stress.get(str(yesterday_status), 0)
                and yesterday_status != stress_code[4]
                and today_status != "NOT_SEASONAL"
            ):
                out.loc[i, 'stress_risk'] = yesterday_status
        return out[['Date','target_code','stress_risk']]


    # ----------------------------
    # field summary
    # ----------------------------
    def summarize_field_status(self, daily_stress_df: pd.DataFrame) -> Dict[str, Any]:
        if daily_stress_df is None or daily_stress_df.empty:
            return {"target": self.target, "status": "NO_DATA"}

        df = daily_stress_df.copy()
        df["Date"] = pd.to_datetime(df["Date"]).dt.date
        df = df.sort_values("Date")

        last = df["Date"].max()
        last7 = df[df["Date"] >= (last - _dt.timedelta(days=6))]

        cur = float(df.loc[df["Date"] == last, "stress_index"].iloc[0])
        cur_cat = str(df.loc[df["Date"] == last, "categorized_daily_stress"].iloc[0])
        last7_avg = float(last7["stress_index"].mean()) if not last7.empty else cur
        season_avg = float(df["stress_index"].mean())
        season_max = float(df["stress_index"].max())

        return {
            "target": self.target,
            "current_stress_index": round(cur, 1),
            "current_category": cur_cat,
            "last7_avg": round(last7_avg, 1),
            "season_avg": round(season_avg, 1),
            "season_max": round(season_max, 1),
        }
