# maize_insect.py
from __future__ import annotations

from datetime import date as _date
from typing import List, Dict, Optional, Any, Union, Tuple

import numpy as np
import pandas as pd
import datetime
from core.utils import get_disease_insect_weed_status_and_code
from core.insect import (
    Insect,
    MigrationResult,
    PhenologyState,
    PopulationState,
    SprayAdvice,
)
from core.management import InsecticideEffects
from core.utils import favorability_to_category
from crops.maize.insect.config import pest_config
from crops.maize.insect.config import IOWA_STAGES, VARIETY_SUSC_CORRECTION


class MaizeInsect(Insect):
    """
    Concrete single-pest model for maize insects (e.g., SPODEX/SPOFRU/OSTFUR/HELARM).
    Implements all abstract methods required by core.insect.Insect and provides:
      - predict_arrival_or_overwinter_date(...)
      - simulate_insect_daily_risk(...)
    """

    def __init__(self, eppo_code: str):
        if eppo_code not in pest_config:
            raise ValueError(f"Unknown insect code '{eppo_code}'. Available: {list(pest_config.keys())}")
        self.eppo_code = eppo_code
        cfg = pest_config[eppo_code]["Global"]
        super().__init__(eppo_code, cfg)

        # Required weather variables (aliasing handled in normalizers below)
        self.weather_variable_list = cfg.get(
            "weather_variables",
            ["temperature_2m", "relative_humidity_2m", "wind_speed_10m", "precipitation", "shortwave_radiation"],
        )

        # Pressure & UI bits
        self.pressure_categories = self.cfg.get(
            "pressure_categories",
            {"0-0.2": "LOW", "0.2-0.5": "MEDIUM", "0.5-999": "HIGH"},
        )
        self.short_agg_days = int(self.cfg.get("short_agg_days", 5))
        self.instar_susceptibility = self.cfg.get("instar_susceptibility", {})
        self.recommended_products = self.cfg.get("recommended_products", [])

        # Convenience thresholds from config
        self.T_min = float(self.cfg.get("T_min", 10.0))
        self.migration_cfg = self.cfg.get("migration", {"enabled": False})

        self.code_stress =get_disease_insect_weed_status_and_code(category='code_stress')
        self.stress_code=get_disease_insect_weed_status_and_code(category='stress_code')
    # -------------------------------
    # Normalizers / daily transforms
    # -------------------------------
    def _normalize_hourly(self, weather_hourly: pd.DataFrame) -> pd.DataFrame:
        if weather_hourly is None or weather_hourly.empty:
            raise ValueError("weather_hourly must be a non-empty DataFrame.")
        df = weather_hourly.copy()
        if "DateTime" not in df.columns:
            raise ValueError("weather_hourly must include 'DateTime'.")
        df["DateTime"] = pd.to_datetime(df["DateTime"])

        # alias normalization to match config names
        if "relative_humidity_2m" not in df.columns and "relativehumidity_2m" in df.columns:
            df["relative_humidity_2m"] = df["relativehumidity_2m"]
        if "wind_speed_10m" not in df.columns and "windspeed_10m" in df.columns:
            df["wind_speed_10m"] = df["windspeed_10m"]

        # sanity for required variables
        for wv in list(dict.fromkeys([*(self.weather_variable_list or []), "precipitation", "shortwave_radiation", "wind_speed_10m", "relative_humidity_2m"])):
            if wv not in df.columns:
                raise ValueError(f"Missing required weather variable: {wv}")
            df[wv] = pd.to_numeric(df[wv], errors="coerce")
            if df[wv].isna().any():
                raise ValueError(f"weather variable has missing or non-numeric values: {wv}")

        df["Date"] = df["DateTime"].dt.normalize()
        return df

    def _daily_agg(self, df_hourly: pd.DataFrame) -> pd.DataFrame:
        return (
            df_hourly.groupby("Date", as_index=False)
            .agg(
                tmean=("temperature_2m", "mean"),
                wind=("wind_speed_10m", "mean"),
                rain=("precipitation", "sum"),
            )
            .sort_values("Date")
            .reset_index(drop=True)
        )

    def _degree_day_daily(self, df_hourly: pd.DataFrame, t_base: float) -> pd.DataFrame:
        df = df_hourly.copy()
        Th = np.maximum(0.0, df["temperature_2m"].astype(float) - float(t_base))
        dd_by_day = (
            pd.DataFrame({"Date": df["Date"], "deg_h": Th})
            .groupby("Date", as_index=False)["deg_h"].sum()
            .rename(columns={"deg_h": "deg_d"})
        )
        dd_by_day["deg_d"] = dd_by_day["deg_d"] / 24.0
        dd_by_day = dd_by_day.sort_values("Date").reset_index(drop=True)
        dd_by_day["dd_cum"] = dd_by_day["deg_d"].cumsum()
        return dd_by_day

    # -------------------------------
    # Weather / stage / variety fits
    # -------------------------------
    def _tri_fit_scalar(self, t: float, Tmin: float, Topt: float, Tmax: float) -> float:
        if t is None or np.isnan(t):
            return 0.0
        if t <= Tmin or t >= Tmax:
            return 0.0
        if t <= Topt:
            return (t - Tmin) / max(Topt - Tmin, 1e-9)
        return (Tmax - t) / max(Tmax - Topt, 1e-9)

    def _weather_favorability_day(self, day_hours: pd.DataFrame) -> float:
        """
        Simple weather suitability for insects:
        - Temperature triangle fit using T_min/T_opt/T_max from cfg (mean temp of the day).
        - Soft rain penalty.
        """
        if day_hours is None or day_hours.empty:
            return 0.0
        Tmean = float(day_hours["temperature_2m"].mean())
        Tmin = float(self.cfg.get("T_min", 10.0))
        Topt = float(self.cfg.get("T_opt", Tmin + 10.0))
        Tmax = float(self.cfg.get("T_max", Topt + 7.0))
        temp_fit = self._tri_fit_scalar(Tmean, Tmin, Topt, Tmax)

        if "precipitation" not in day_hours.columns:
            raise ValueError("hourly weather missing required field: precipitation")
        rain_sum = float(day_hours["precipitation"].sum())
        rain_pen = 1.0
        if rain_sum >= 10.0:
            rain_pen = 0.8
        elif rain_sum >= 2.0:
            rain_pen = 0.9

        return float(np.clip(temp_fit * rain_pen, 0.0, 1.0))

    @staticmethod
    def _stage_to_index(stage: str) -> Optional[int]:
        if not isinstance(stage, str) or not stage.strip():
            return None
        s = stage.strip().upper()
        if s in IOWA_STAGES:
            return IOWA_STAGES.index(s)
        if s.startswith("V") and s != "VT":
            try:
                vnum = int(s[1:].replace("+", ""))
            except ValueError:
                return None
            if vnum <= 16:
                label = f"V{vnum}"
                return IOWA_STAGES.index(label) if label in IOWA_STAGES else None
            return IOWA_STAGES.index("V16+")
        return None

    def _growth_stage_favorability_day(self, stage: str) -> float:
        """
        Crop-stage multiplier for actionable insect damage potential.
        The configured curve is linearly interpolated by Iowa maize stage order.
        """
        stage_idx = self._stage_to_index(stage)
        if stage_idx is None:
            return 0.0

        curve = self.cfg.get("stage_favorability_curve_iowa") or {}
        stages = curve.get("stages") or []
        values = curve.get("favorability") or []
        if not stages or len(stages) != len(values):
            return 0.0

        pairs = []
        for curve_stage, value in zip(stages, values):
            idx = self._stage_to_index(curve_stage)
            if idx is None:
                continue
            pairs.append((idx, float(value)))
        if not pairs:
            return 0.0

        pairs = sorted(pairs, key=lambda item: item[0])
        if stage_idx <= pairs[0][0]:
            return float(max(0.0, min(pairs[0][1], 1.5)))
        if stage_idx >= pairs[-1][0]:
            return float(max(0.0, min(pairs[-1][1], 1.5)))

        for (left_idx, left_val), (right_idx, right_val) in zip(pairs, pairs[1:]):
            if left_idx <= stage_idx <= right_idx:
                span = max(1, right_idx - left_idx)
                frac = (stage_idx - left_idx) / span
                val = left_val + frac * (right_val - left_val)
                return float(max(0.0, min(val, 1.5)))
        return 0.0

    def _variety_factor(self, grp: int=5) -> float:
        """
        Map variety susceptibility group to a favorability multiplier in [0,1].
        Resistant (group ≤2) → 0.85; susceptible capped at 1.0.
        """
        return float(np.interp(grp, VARIETY_SUSC_CORRECTION['susceptibility'], VARIETY_SUSC_CORRECTION['impact']))

    def _categorize(self, x: float) -> str:
        if x is None or (isinstance(x, float) and np.isnan(x)) or x <= 0.2:
            return "UNFAVORABLE"
        if x < 0.5:
            return "FAVORABLE"
        return "OPTIMAL"

    # -------------------------------
    # Abstract method implementations
    # -------------------------------
    def simulate_migration_arrival(
        self, weather: pd.DataFrame, *, external_alerts: Optional[pd.DataFrame] = None
    ) -> List[MigrationResult]:
        """Simple score: favorable when wind >= threshold and mean T >= threshold."""
        df = self._normalize_hourly(weather)
        daily = self._daily_agg(df)

        enabled = bool(self.migration_cfg.get("enabled", False))
        wind_th = float(self.migration_cfg.get("windspeed_threshold", 4.0))
        t_th = float(self.migration_cfg.get("temperature_threshold", 15.0))

        out: List[MigrationResult] = []
        for _, r in daily.iterrows():
            if not enabled:
                out.append(MigrationResult(date=r["Date"], arrived=False, arrival_score=0.0))
                continue
            score_w = 0.0 if np.isnan(r["wind"]) else max(0.0, min(1.0, (r["wind"] - wind_th) / max(wind_th, 1e-9)))
            score_t = 0.0 if np.isnan(r["tmean"]) else max(0.0, min(1.0, (r["tmean"] - t_th) / max(t_th, 1e-9)))
            score = (score_w + score_t) / 2.0
            out.append(MigrationResult(date=r["Date"], arrived=bool(score >= 0.5), arrival_score=float(score)))
        return out

    def simulate_phenology(self, weather: pd.DataFrame) -> List[PhenologyState]:
        """Advance instars by degree-day accumulation (egg → L1..L6 → pupa → adult)."""
        df = self._normalize_hourly(weather)
        dd = self._degree_day_daily(df, t_base=self.T_min)
        # Build per-instar requirements
        per_instar = {"egg": float(self.cfg.get("degree_day_per_stage", {}).get("egg", 0.0))}
        per_instar.update({k: float(v) for k, v in (self.cfg.get("degree_day_per_instar", {}) or {}).items()})
        per_instar.update({
            "pupa": float(self.cfg.get("degree_day_per_stage", {}).get("pupa", 0.0)),
            "adult": float(self.cfg.get("degree_day_per_stage", {}).get("adult", 0.0)),
        })
        dd_series = dd.set_index("Date")["deg_d"]
        return self.advance_instar_by_degree_day(dd_series, per_instar)

    def simulate_population(
        self,
        weather: pd.DataFrame,
        *,
        phenology: List[PhenologyState],
        initial_population: Optional[Dict[str, float]] = None,
    ) -> List[PopulationState]:
        """Very simple daily growth × natural mortality, distributed by instar proportions."""
        if not phenology:
            return []
        df = self._normalize_hourly(weather)
        dates = df["Date"].drop_duplicates().sort_values().tolist()

        first_dist = phenology[0].instar_distribution
        if not initial_population:
            total0 = 1.0
            cur = {k: total0 * float(v) for k, v in first_dist.items()}
        else:
            cur = dict(initial_population)

        growth_r = float(self.cfg.get("pop_growth_rate_daily", 0.05))
        nat_mort = float(self.cfg.get("natural_mortality_daily", 0.01))

        out: List[PopulationState] = []
        total_prev = sum(cur.values())

        for dt, phe in zip(dates, phenology):
            total = max(0.0, total_prev * (1.0 + growth_r) * (1.0 - nat_mort))
            dist = phe.instar_distribution
            cur = {k: total * float(dist.get(k, 0.0)) for k in dist.keys()}
            out.append(
                PopulationState(
                    date=pd.to_datetime(dt),
                    densities=cur,
                    total=total,
                    origin="carryover",
                    intermediate={"growth_r": growth_r, "nat_mort": nat_mort},
                )
            )
            total_prev = total
        return out

    def recommend_spray_windows(
        self,
        *,
        weather: pd.DataFrame,
        phenology: List[PhenologyState],
        population: List[PopulationState],
        pesticide_params: Optional[Dict[str, Any]] = None,
    ) -> List[SprayAdvice]:
        """Recommend a 2-day window when L2 is present, population is high, and spray weather is OK."""
        if not phenology or not population:
            return []
        totals = np.array([p.total for p in population], dtype=float)
        if totals.size == 0:
            return []
        trigger = float(np.quantile(totals, 0.6))

        df = self._normalize_hourly(weather)
        daily = self._daily_agg(df)

        out: List[SprayAdvice] = []
        for phe, pop in zip(phenology, population):
            if not phe.window_flags.get("L2_window", False):
                continue
            if pop.total < trigger:
                continue
            row = daily[daily["Date"] == phe.date]
            if row.empty:
                continue
            wind_ok = bool(row["wind"].iloc[0] < 3.0) if not np.isnan(row["wind"].iloc[0]) else True
            rain_ok = bool(row["rain"].iloc[0] < 2.0) if not np.isnan(row["rain"].iloc[0]) else True
            if not (wind_ok and rain_ok):
                continue
            out.append(
                SprayAdvice(
                    target_stage="L2",
                    window=(phe.date, phe.date + pd.Timedelta(days=2)),
                    confidence=0.8,
                    rationale=f"L2 window & population≥{trigger:.2f} & spray weather ok",
                )
            )
        return out

    # -------------------------------
    # Arrival / overwinter predictor
    # -------------------------------
    def _first_migration_date(
        self, daily: pd.DataFrame, *, wind_th: float, t_th: float, sustain_days: int = 1
    ) -> Optional[_date]:
        cond = (daily["wind"] >= wind_th) & (daily["tmean"] >= t_th)
        if sustain_days <= 1:
            if not cond.any():
                return None
            idx = cond.idxmax()
            return pd.to_datetime(daily.loc[idx, "Date"]).date()
        hits = cond.astype(int).rolling(window=sustain_days, min_periods=sustain_days).sum() >= sustain_days
        if not hits.any():
            return None
        idx = hits.idxmax()
        return pd.to_datetime(daily.loc[idx, "Date"]).date()

    def _first_overwinter_emergence_date(
        self, dd_daily: pd.DataFrame, *, threshold_dd: float, planting_date: Optional[_date] = None
    ) -> Optional[_date]:
        df = dd_daily.copy()
        if planting_date is not None:
            df = df[df["Date"] >= pd.to_datetime(planting_date)]
            if df.empty:
                return None
        hit = df[df["dd_cum"] >= float(threshold_dd)]
        if hit.empty:
            return None
        return pd.to_datetime(hit.iloc[0]["Date"]).date()

    def predict_arrival_or_overwinter_date(
        self,
        weather_hourly: pd.DataFrame,
        planting_date: _date,
        *,
        sustain_days_for_migration: int = 1,
        default_overwinter_dd_fraction: float = 0.6,
    ) -> Dict[str, Any]:
        dfh = self._normalize_hourly(weather_hourly)
        daily = self._daily_agg(dfh)
        dd_daily = self._degree_day_daily(dfh, t_base=self.T_min)

        overwinter = bool(self.cfg.get("overwinter", False))
        migr = self.cfg.get("migration", {"enabled": False})
        migr_enabled = bool(migr.get("enabled", False))
        wind_th = float(migr.get("windspeed_threshold", 5.0))
        t_th = float(migr.get("temperature_threshold", 15.0))
        sustain_days_cfg = int(migr.get("sustain_days", sustain_days_for_migration))

        out: Dict[str, Any] = {
            "code": self.code,
            "mode": "overwinter" if overwinter else "migration",
            "arrival_date": None,
            "overwinter_emergence_date": None,
            "support": {},
            "trace_head": [],
        }

        if overwinter:
            stage_dd = self.cfg.get("degree_day_per_stage", {})
            threshold_dd = float(
                self.cfg.get(
                    "overwinter_release_dd",
                    stage_dd.get("pupa", stage_dd.get("larva_total", 200.0) * default_overwinter_dd_fraction),
                )
            )
            emer_date = self._first_overwinter_emergence_date(
                dd_daily, threshold_dd=threshold_dd, planting_date=planting_date
            )
            out["overwinter_emergence_date"] = emer_date
            out["support"] = {
                "T_min": self.T_min,
                "threshold_dd": threshold_dd,
                "dd_cum_on_emer": float(
                    dd_daily.loc[dd_daily["Date"] <= pd.to_datetime(emer_date), "dd_cum"].max()
                ) if emer_date else None,
            }
        else:
            arr_date = None
            if migr_enabled:
                arr_date = self._first_migration_date(
                    daily, wind_th=wind_th, t_th=t_th, sustain_days=sustain_days_cfg
                )
            out["arrival_date"] = arr_date
            out["support"] = {"wind_th": wind_th, "tmean_th": t_th, "sustain_days": sustain_days_cfg}

        trace = daily.merge(dd_daily[["Date", "deg_d", "dd_cum"]], on="Date", how="left")
        trace["Date"] = pd.to_datetime(trace["Date"]).dt.date
        out["trace_head"] = trace.head(10).to_dict(orient="records")
        return out

    # -------------------------------
    # Insecticide helpers (universal protection)
    # -------------------------------
    def _normalize_insecticide_events(self, events: Union[list, pd.DataFrame, None]) -> pd.DataFrame:
        """
        Accept list[dict] or DataFrame and normalize into a DataFrame with:
          - 'date' (datetime.date)
          - pass-through product fields, including nested 'pesticide' dict
        Assumes each application protects ALL insects (no 'target' filtering).
        """
        if events is None:
            return pd.DataFrame(columns=["date","product_key","physical_state","dose_value","dose_unit","pesticide","residual_control_days"])

        df = pd.DataFrame(events) if isinstance(events, list) else events.copy()

        # alias 'Date' -> 'date'
        if "date" not in df.columns and "Date" in df.columns:
            df = df.rename(columns={"Date": "date"})
        if "date" not in df.columns:
            return pd.DataFrame(columns=["date","product_key","physical_state","dose_value","dose_unit","pesticide","residual_control_days"])

        # normalize to date (not datetime) for calendar joins
        df["date"] = pd.to_datetime(df["date"]).dt.date

        # keep safe subset; allow extra columns
        keep = [c for c in ["date","product_key","physical_state","dose_value","dose_unit","pesticide","residual_control_days"] if c in df.columns]
        df = df[keep].sort_values("date").reset_index(drop=True)
        return df

    def _build_label_protection_map(self, df_hourly: pd.DataFrame, events_df: pd.DataFrame) -> Dict[datetime.date, float]:
        """
        Compute daily protection level (0..1) from label parameters only:
          - dose-response: Emax, ED50, hill
          - residual: half_life_days (exponential decay)
          - rainfast: daily rain penalty on spray day
        This assumes the provided dose_value matches the dose-response unit in 'pesticide'.
        """
        if events_df is None or events_df.empty:
            return {}

        # daily rain map (mm/day)
        h = df_hourly.copy()
        h["Date"] = h["DateTime"].dt.date
        daily_rain = h.groupby("Date", as_index=False)["precipitation"].sum().rename(columns={"precipitation": "rain"})
        rain_map = dict(zip(daily_rain["Date"], daily_rain["rain"]))

        def _dose_resp(dose, Emax, ED50, h):
            dose = float(max(dose, 0.0))
            Emax = float(Emax); ED50 = float(ED50); h = float(h)
            return float(Emax) * (dose ** h) / ((ED50 ** h) + (dose ** h) + 1e-12)

        last_day = h["Date"].max()
        prot: Dict[datetime.date, float] = {}

        for _, ev in events_df.iterrows():
            sday = ev["date"]
            pest = (ev.get("pesticide") or {}) if isinstance(ev.get("pesticide"), dict) else {}
            dr    = (pest.get("dose_response") or {})
            resid = (pest.get("residual") or {})
            rf    = (pest.get("rainfast") or {})

            Emax = float(dr.get("Emax", 0.9))
            ED50 = float(dr.get("ED50", max(1.0, float(ev.get("dose_value", 1.0)))))
            hill = float(dr.get("hill", 1.2))
            half_life = float(resid.get("half_life_days", 5.0))
            residual_control_days = int(float(ev.get("residual_control_days") or 0.0))

            rain_thr = float(rf.get("rainfast_threshold_mm", 5.0))
            rain_pen = float(rf.get("rain_penalty", 0.85))

            base = _dose_resp(float(ev.get("dose_value", 0.0)), Emax, ED50, hill)

            # if it rains on the spray day above threshold, apply penalty
            if float(rain_map.get(sday, 0.0)) >= rain_thr:
                base *= rain_pen

            # decay across days by half-life
            d = sday
            while d <= last_day and base > 1e-4:
                days_since = (d - sday).days
                eff_d = float(base * (0.5 ** (days_since / max(half_life, 1e-9))))
                if residual_control_days > 0 and days_since < residual_control_days:
                    eff_d = max(eff_d, 0.41)
                prot[d] = max(prot.get(d, 0.0), eff_d)
                d = d + datetime.timedelta(days=1)

        return prot

    # -------------------------------
    # Daily risk (requested API)
    # -------------------------------
    def simulate_insect_daily_risk(
        self,
        planting_date: datetime.date = None,
        weather_hourly: pd.DataFrame = None,
        growth_stage: pd.DataFrame = None,
        variety_susceptibility:int = None,
        applied_insecticides: Union[list, pd.DataFrame, None] = None,
    ) -> pd.DataFrame:
        """
        Simulate daily insect risk across the season:
        1) Gate by arrival (migration) or emergence (overwinter).
        2) Favorability = weather × crop stage × variety.
        3) Overlay insecticide protection (assume every application protects ALL targets):
           - label-based protection (dose/ED50/Emax × half-life × rainfast)
           - optional model-based kill from InsecticideEffects (if available)
           - population drop confirmation
           A day is PROTECTED if any protection signal is active.
        4) Output JSON-safe daily table.
        """
        # ---- 0) Normalize inputs ----
        if weather_hourly is None or len(weather_hourly) == 0:
            return pd.DataFrame()

        dfw = self._normalize_hourly(weather_hourly)
        dfw["Date"] = dfw["DateTime"].dt.date

        if growth_stage is None or growth_stage.empty:
            raise ValueError("growth_stage DataFrame must not be None or empty")
        gs = growth_stage.copy()
        if "Date" not in gs.columns or "Stage" not in gs.columns:
            raise ValueError("growth_stage must have columns ['Date','Stage']")
        gs["Date"] = pd.to_datetime(gs["Date"]).dt.date
        gs_map = gs.set_index("Date")["Stage"].to_dict()

        # normalize and assume universal protection
        events_df = self._normalize_insecticide_events(applied_insecticides)

        # ---- 1) Timeline ----
        start_date = planting_date or dfw["Date"].min()
        end_date = min(gs["Date"].max(), dfw["Date"].max())
        if pd.to_datetime(start_date) > pd.to_datetime(end_date):
            return pd.DataFrame()
        days = pd.date_range(start=start_date, end=end_date, freq="D").date
        missing = set(days) - set(gs_map.keys())
        if missing:
            raise ValueError(f"Missing growth stage entries for dates: {sorted(missing)}")

        # group hours for day metrics
        hgroups = dict(tuple(dfw.groupby("Date", sort=True)))

        # ---- 2) Arrival / overwinter gating ----
        gate_info = self.predict_arrival_or_overwinter_date(weather_hourly=dfw, planting_date=start_date)
        gate_mode = gate_info.get("mode")
        arrival_date = gate_info.get("arrival_date")
        emer_date = gate_info.get("overwinter_emergence_date")

        # ---- 3) Phenology + population ----
        phe = self.simulate_phenology(dfw)
        pop_before = self.simulate_population(dfw, phenology=phe, initial_population=None)
        pop_map = {pd.to_datetime(p.date).date(): p for p in pop_before}

        # apply modelled pesticide effects if your base class supports it
        pop_after = pop_before
        if events_df is not None and not events_df.empty:
            try:
                pop_after = self.apply_pesticide_effects(
                    population_series=pop_before,
                    control_events=events_df.to_dict("records"),  # pass list[dict]
                    weather=dfw,
                    pesticide_params=None,
                )
            except Exception:
                pop_after = pop_before
        pop_after_map = {pd.to_datetime(p.date).date(): p for p in pop_after}

        # ---- 4) Label-based daily protection (independent of population) ----
        label_prot = self._build_label_protection_map(dfw, events_df)  # {date: 0..1}

        # ---- 5) Build rows ----
        rows = []
        prev_total = None
        vfac = self._variety_factor(variety_susceptibility)

        for d in days:
            stage = gs_map[d]

            # Gate before arrival/emergence → UNFAVORABLE
            if gate_mode == "migration":
                gated_out = bool(arrival_date is None or d < arrival_date)
            else:
                gated_out = bool(emer_date is None or d < emer_date)

            # Weather × stage × variety
            wfac = self._weather_favorability_day(hgroups.get(d))
            gsfac = self._growth_stage_favorability_day(stage)
            favor = 0.0 if gated_out else float(np.clip(wfac * gsfac * vfac, 0.0, 1.0))
            # Pop metrics
            pb = pop_map.get(d)
            pa = pop_after_map.get(d)
            total_b = float(pb.total) if pb else 0.0
            total_a = float(pa.total) if pa else total_b
            struct = (pb.densities if pb else {})

            # daily growth (JSON-safe)
            if prev_total is None or prev_total <= 0.0 or total_b <= 0.0:
                growth_r = None
            else:
                growth_r = float((total_b - prev_total) / max(prev_total, 1e-9))
            prev_total = total_b

            # model-based kill (optional) — assumes universal protection
            max_kill, is_protected_mgmt = (0.0, False)

            max_kill, is_protected_mgmt = InsecticideEffects.effective_kill_on_date(
                applied_insecticides=events_df.to_dict("records"),
                reference_date=d,
                hourly_weather=dfw,
                protection_threshold=0.50,  # tune threshold
            )


            # label-based protection (threshold)
            label_prot_flag = bool(label_prot.get(d, 0.0) >= 0.40)

            # population drop confirmation (after vs before)
            pop_drop_flag = (total_b > 0.0) and (total_a < 0.8 * total_b)

            protected = bool(is_protected_mgmt or label_prot_flag or pop_drop_flag)
            
            rows.append(
                {
                    "Date": d,
                    "eppo_code": self.eppo_code,
                    'gate_mode':gate_info.get("mode"),
                    'gate_date':arrival_date if gate_mode=='migration' else emer_date,
                    "weather_favorability": float(wfac),
                    "variety_favorability": float(vfac),
                    'gstage': stage,
                    "growth_stage_favorability": float(gsfac),
                    "population_structure_by_insect_number_in_each_stage": dict(struct),
                    "population": float(total_b),
                    "population_daily_growth_rate": growth_r,
                    "insecticide_effects": {
                        "protected": protected,
                        "population_after": float(total_a),
                        "population_before": float(total_b),
                        "reduction_ratio": float(1.0 - (total_a / max(total_b, 1e-9))) if total_b > 0 else 0.0,
                        "max_effective_kill_today": float(max_kill),
                        "label_protection_today": float(label_prot.get(d, 0.0)),
                    },
                    'favorability':favor

                    
                }
            )
        
        out = pd.DataFrame(rows).sort_values("Date").reset_index(drop=True)

        out['favorability']= out.apply(lambda row: row['favorability']*(1-row['insecticide_effects']['max_effective_kill_today']), axis=1 )

        # ---- 6) Aggregates & categories (+ force PROTECTED) ----
        k = int(self.short_agg_days or 5)
        actionable_mask = out["growth_stage_favorability"] > 0.0
        out["shortterm_aggregate_favorability"] = 0.0
        out.loc[actionable_mask, "shortterm_aggregate_favorability"] = (
            out.loc[actionable_mask, "favorability"].rolling(k, min_periods=1).mean()
        )

        # base categories
        out["categorized_daily_favorability"] = out["favorability"].apply(
            lambda x: favorability_to_category(x, self.cfg["daily_infection_risk_categories"]))

        out["categorized_shortterm_aggregate_favorability"] = out["shortterm_aggregate_favorability"].apply(
            lambda x: favorability_to_category(x, self.cfg["short_agg_infection_risk_categories"]))

        # override to PROTECTED when any protection signal is active
        prot_mask = out["insecticide_effects"].apply(lambda x: bool(x.get("protected", False)))
        out.loc[prot_mask, "categorized_daily_favorability"] = "PROTECTED"
        out.loc[prot_mask, "categorized_shortterm_aggregate_favorability"] = "PROTECTED"

        # JSON-safe
        out = out.replace({np.nan: None, np.inf: None, -np.inf: None})
        return out[
            [
                "Date",
                "eppo_code",
                "gate_mode",
                "gate_date",
                "weather_favorability",
                "variety_favorability",
                'gstage',
                "growth_stage_favorability",
                "population_structure_by_insect_number_in_each_stage",
                "population",
                "population_daily_growth_rate",
                "insecticide_effects",
                'favorability',
                "categorized_daily_favorability",
                "shortterm_aggregate_favorability",
                "categorized_shortterm_aggregate_favorability",
            ]
        ]
    def estimate_stress_risk(self,insect_daily_risk:pd.DataFrame):

        insect_stress_risk=insect_daily_risk.copy()[['Date','eppo_code','insecticide_effects','categorized_shortterm_aggregate_favorability']]

        insect_stress_risk['stress_risk']=insect_stress_risk['categorized_shortterm_aggregate_favorability']

        insect_stress_risk.loc[insect_stress_risk['insecticide_effects'].apply(lambda x:x['protected']==True),'stress_risk']="PROTECTED"
        return insect_stress_risk[['Date','eppo_code','stress_risk']]
