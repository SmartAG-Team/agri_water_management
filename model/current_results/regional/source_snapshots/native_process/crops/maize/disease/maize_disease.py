from core.disease import Disease
import pandas as pd
import datetime
import numpy as np
from copy import deepcopy
from crops.maize.disease.config import disease_config,  LWD_RULES, LWD_STORM_OVERRIDES, IOWA_STAGES
from core.management import FungicideEffects
from typing import List
from api.ocm.schemas.disease_schema import FungicideApplication
from core.utils import favorability_to_category
class MaizeDisease(Disease):
    """
    Maize-specific disease model for simulating infection risk and spray timing.
    Inherits from the abstract Disease class.
    """

    def __init__(self, eppo_code: str):
        super().__init__(eppo_code)
        self.eppo_code = eppo_code
        self.cfg = disease_config.get(eppo_code, {})['Global']
        self.weather_variable_list = self.cfg.get("weather_variable_list", [])
    def cal_growth_stage_favorability(self, growth_stage: str) -> float:
        """
        Growth-stage favorability for maize (0..1), using the disease-specific
        Iowa stage weights from self.cfg['Global'].

        - Expects `growth_stage` to be one of the configured Iowa stages (e.g., 'V6','VT','R2').
        - Returns 0.0 if the stage is outside this disease's configured window.
        - Raises ValueError if the stage string is not recognized.
        """
        if not isinstance(growth_stage, str) or not growth_stage:
            raise ValueError("growth_stage must be a non-empty string (e.g., 'V6', 'VT', 'R2').")

        table = self.cfg.get("crop_growth_stage_correction_iowa", {})
        stages_cfg = table.get("stages", IOWA_STAGES)
        weights    = table.get("infection", [])

        if not stages_cfg or not weights or len(stages_cfg) != len(weights):
            raise ValueError("Invalid crop_growth_stage_correction_iowa configuration for this disease.")

        # strict: unknown stage is a data error
        try:
            idx = stages_cfg.index(growth_stage)
        except ValueError as e:
            raise ValueError(f"Unknown growth stage '{growth_stage}'. Expected one of: {stages_cfg}") from e

        # respect configured start/end window (outside → 0)
        limits = self.cfg.get("growth_stage_limits_iowa", None)
        if limits is not None:
            try:
                start_i = stages_cfg.index(limits["start_stage"])
                end_i   = stages_cfg.index(limits["end_stage"])
            except ValueError as e:
                raise ValueError("growth_stage_limits_iowa contains stage(s) not present in the stages table.") from e
            if not (start_i <= idx <= end_i):
                return 0.0

        # mapped weight already in [0,1]
        return float(weights[idx])
    def cal_variety_susceptibility(self, susceptibility_group: int | float = 5) -> float:
        """
        Variety susceptibility correction (maize, disease-specific).
        Returns a favorability multiplier ∈ [0,1].
        Default group 5 → baseline (≈1.0 before normalization).

        Uses self.cfg['Global']['variety_susceptibility_correction'].
        """
        import numpy as np

        vc = self.cfg.get("variety_susceptibility_correction")
        if vc is None:
            return 1.0  # no correction if not configured

        x = np.array(vc["susceptibility"], dtype=float)  # e.g., [1, 5, 9]
        y = np.array(vc["infection"], dtype=float)       # e.g., [0.85, 1.00, 1.15]

        s = float(susceptibility_group)
        if s <= x.min():
            raw = float(y[x.argmin()])
        elif s >= x.max():
            raw = float(y[x.argmax()])
        else:
            raw = float(np.interp(s, x, y))

        # normalize to [0,1] using min/max of table
        ymin, ymax = float(y.min()), float(y.max())
        if ymax > ymin:
            return (raw - ymin) / (ymax - ymin)
        else:
            return 1.0

    def fungicide_effect(self,applied_fungicides:List[FungicideApplication]=None,day:datetime.date=None):
        """Correction of infection favorability depending on protective effect of fungicide.
        Args:
            applied_fungicides: list of dicts like the request body. One example:"applied_fungicides": [
                    {
                    "method": "SEED"
                    "applied_date": "2020-07-20",
                    "stress": "PYRIOR",
                    "curative_protection_days": 7,
                    "preventive_protection_days": 0
                    },
                    {
                    "method": "SEED_BOX"
                    "applied_date": "2020-07-24",
                    "stress": "CORTSS",
                    "curative_protection_days": 7,
                    "preventive_protection_days": 10
                    },
                    {
                    "method": "SPRAYING"
                    "applied_date": "2020-07-25",
                    "stress": "PYRNOR",
                    "curative_protection_days": 7,
                    "preventive_protection_days": 10
                    },

                    {
                    "method": "SPRAYING"
                    "applied_date": "2020-08-25",
                    "stress": "PYRNOR",
                    "curative_protection_days": 7,
                    "preventive_protection_days": 10,

                    "preventive_protection_days":False
                    }
                    ]
        the effects are equal to the 1 - effcicacy during the protective days for infection. For preventive, it starts from the appliced date and last the protective days. For curative, curative_protection_days means the curable days.
        The curable days means the limit for the tissues are in latent period. The effects start from latent period minus the curable days when the curable days < latent period. When the curable days>= latent period, the effects start immediately.
        For eridicant fungicide, the effects only binary, true or false. When it is true, it reduce the infection risk be 1 - efficacy for a period. The period is specific by target. 
        Returns: overall fungicide effects, preventive effects, curative effects and eridicant effects
        
        """
        
        if len(applied_fungicides)==0:
            return 1,1,1,1
        else:
            return FungicideEffects.effect_from_applied_fungicide(applied_fungicides=applied_fungicides,target=self.eppo_code,latent_days=self.cfg['duration_of_latent_period'],reference_date=day)

    def cal_daily_weather_favorability(self, weather_hourly: pd.DataFrame) -> float:
        """
        Weather-only daily favorability (0..1) for this disease (self.cfg['Global']).
        Assumes inoculum is present. Uses *hourly data for one day*.
        Variable requirements are taken from self.weather_variable_list.
        Storm/primary-inoculum transport is NOT considered here.

        Returns
        -------
        float ∈ [0, 1]
        """

        if weather_hourly is None or weather_hourly.empty:
            return 0.0

        # --- Normalize column names & verify against self.weather_variable_list ---
        df = weather_hourly.copy()
        df["DateTime"] = pd.to_datetime(df["DateTime"])

        # Alias mapping to your DEFAULT_WEATHER_VARIABLES
        alias_map = {
            "relativehumidity_2m": "relative_humidity_2m",
            "windspeed_10m": "wind_speed_10m",
        }
        for src, dst in alias_map.items():
            if src in df.columns and dst not in df.columns:
                df[dst] = df[src]

        # Ensure all required variables exist.
        required = list(dict.fromkeys([*(self.weather_variable_list or []), "precipitation", "shortwave_radiation", "wind_speed_10m", "relative_humidity_2m"]))
        # Always need DateTime to ensure this is hourly in one day
        if "DateTime" not in df.columns:
            raise ValueError("Missing required column: DateTime")
        for col in required:
            if col not in df.columns:
                raise ValueError(f"Missing required weather variable: {col}")
            df[col] = pd.to_numeric(df[col], errors="coerce")
            if df[col].isna().any():
                raise ValueError(f"weather variable has missing or non-numeric values: {col}")

        # Sort rows and sanity: one day only (won't hard fail if multiple, but uses given rows)
        df = df.sort_values("DateTime").reset_index(drop=True)

        # Pull arrays (safe to .get for optional tolerances)
        T   = df.get("temperature_2m").astype(float).values
        RH  = df["relative_humidity_2m"].astype(float).values
        WND = df["wind_speed_10m"].astype(float).values
        PR  = df["precipitation"].astype(float).values
        RAD = df["shortwave_radiation"].astype(float).values

        use_lwd = bool(self.cfg.get("use_lwd", True))  # DIPDMA sets False

        # ---------------- helpers ----------------
        def _tri_fit(Tarr: np.ndarray, Tmin: float, Topt: float, Tmax: float) -> np.ndarray:
            fit = np.zeros_like(Tarr, dtype=float)
            left  = (Tarr > Tmin) & (Tarr <= Topt)
            right = (Tarr > Topt) & (Tarr < Tmax)
            if Topt > Tmin:
                fit[left]  = (Tarr[left] - Tmin) / (Topt - Tmin)
            if Tmax > Topt:
                fit[right] = (Tmax - Tarr[right]) / (Tmax - Topt)
            fit[(Tarr <= Tmin) | (Tarr >= Tmax)] = 0.0
            return np.clip(fit, 0.0, 1.0)

        def _infer_hourly_wet(rules: dict) -> np.ndarray:
            """Implements your LWD rules with allowed missing subsets."""
            use_precip   = rules.get("use_precip_as_wet", True)
            rh_wet       = rules.get("rh_wet_threshold", 90.0)
            rh_dry       = rules.get("rh_dry_threshold", 80.0)
            rad_dry      = rules.get("rad_dry_threshold", 400.0)
            wind_dry     = rules.get("wind_dry_threshold", 3.5)
            allow_m_rad  = rules.get("allow_missing_radiation", True)
            allow_m_wind = rules.get("allow_missing_wind", True)

            wet = np.zeros(len(df), dtype=bool)
            if use_precip and "precipitation" in df.columns:
                wet |= (PR > rules.get("precip_wet_threshold", 0.0))

            # RH-based wetness unless dry conditions
            if "relative_humidity_2m" in df.columns:
                rh_hi  = RH >= rh_wet
                rh_low = RH < rh_dry
            else:
                rh_hi  = np.zeros(len(df), dtype=bool)
                rh_low = np.zeros(len(df), dtype=bool)

            # Dry conditions
            if ("shortwave_radiation" in df.columns) and not (allow_m_rad and np.all(np.isnan(RAD))):
                dry_by_rad = (RAD >= rad_dry)
            else:
                dry_by_rad = np.zeros(len(df), dtype=bool)

            if ("wind_speed_10m" in df.columns) and not (allow_m_wind and np.all(np.isnan(WND))):
                dry_by_wind = (WND >= wind_dry)
            else:
                dry_by_wind = np.zeros(len(df), dtype=bool)

            dry_any = dry_by_rad | dry_by_wind | rh_low
            wet |= (rh_hi & (~dry_any))
            return wet

        # =========================================================
        # A) Stress-driven (e.g., DIPDMA: use_lwd == False)
        # =========================================================
        if not use_lwd and "stress_drivers" in self.cfg:
            sd = self.cfg["stress_drivers"]

            # Heat degree hours above base
            base = float(sd.get("heat_degree_hour_base", 30.0))
            hdh  = np.maximum(0.0, T - base).sum()          # °C·h for the day
            heat_norm = min(hdh / 60.0, 1.0)                # ~1 near 60 °C·h

            # High-radiation hours
            high_rad_thr  = float(sd.get("high_rad_threshold", 500.0))
            high_rad_need = int(sd.get("high_rad_hours_threshold", 6))
            if "shortwave_radiation" in df.columns and not np.all(np.isnan(RAD)):
                high_rad_h = int((RAD >= high_rad_thr).sum())
                rad_norm   = min(high_rad_h / max(high_rad_need, 1), 1.0)
            else:
                rad_norm = 0.0

            # Dry day by daily precip
            dry_day_thr = float(sd.get("dry_day_precip_threshold", 1.0))
            day_pr_sum  = float(np.nansum(PR)) if "precipitation" in df.columns else np.inf
            dry_norm    = 1.0 if day_pr_sum < dry_day_thr else 0.0

            favor = (heat_norm + rad_norm + dry_norm) / 3.0
            return float(np.clip(favor, 0.0, 1.0))

        # =========================================================
        # B) Foliar diseases (LWD-based, storm ignored)
        # =========================================================
        wet = _infer_hourly_wet(LWD_RULES)

        # Minimum RH for infection hours (if configured)
        rH_min = float(self.cfg.get("rH_min", 0.0))
        if rH_min > 0 and "relative_humidity_2m" in df.columns:
            wet &= (RH >= rH_min)

        wet_hours = int(wet.sum())
        W_min = int(self.cfg.get("W_min", 0))
        W_max = int(self.cfg.get("W_max", 24))

        if wet_hours < W_min:
            return 0.0

        # Temperature fitness (prefer wet hours; fallback to all hours if none)
        Tmin, Topt, Tmax = self.cfg["T_min"], self.cfg["T_opt"], self.cfg["T_max"]
        fit = _tri_fit(T, Tmin, Topt, Tmax)
        temp_fit = float(fit[wet].mean()) if wet.any() else float(fit.mean())

        # Wetness sufficiency scaled to [0,1] between W_min and W_max
        wet_norm = (wet_hours - W_min) / max(W_max - W_min, 1)
        wet_norm = float(np.clip(wet_norm, 0.0, 1.0))

        return float(np.clip(wet_norm * temp_fit, 0.0, 1.0))
    def simulate_disease_daily_risk(
    self,
    planting_date: datetime.date = None,
    weather_hourly: pd.DataFrame = None,
    growth_stage: pd.DataFrame = None,
    variety_susceptibility: dict = None,
    applied_fungicides: list = None
) -> pd.DataFrame:
        """
        Simulate daily disease favorability across the season:
        weather-only (from hourly) × growth stage × variety × fungicide.

        Inputs:
        - weather_hourly: hourly DataFrame with a 'DateTime' column + vars in self.weather_variable_list
        - growth_stage: daily DataFrame with columns ['Date','Stage'] (Date can be str/ts/date)
        - variety_susceptibility: dict like {eppo_code: group}, default 5 if missing
        - applied_fungicides: list of application dicts (see FungicideEffects)

        Output:
        Daily DataFrame with a 'Date' column (date), one row per day.
        """
        if weather_hourly is None or weather_hourly.empty:
            return pd.DataFrame()

        gcfg = self.cfg

        # ---------- normalize hourly weather ----------
        dfw = weather_hourly.copy()
        if "DateTime" not in dfw.columns:
            raise Exception("weather missing variable DateTime")
        dfw["DateTime"] = pd.to_datetime(dfw["DateTime"])

        # alias columns to match config names
        if "relative_humidity_2m" not in dfw.columns and "relativehumidity_2m" in dfw.columns:
            dfw["relative_humidity_2m"] = dfw["relativehumidity_2m"]
        if "wind_speed_10m" not in dfw.columns and "windspeed_10m" in dfw.columns:
            dfw["wind_speed_10m"] = dfw["windspeed_10m"]

        # validate required variables from config and shared weather contract
        for wv in list(dict.fromkeys([*(self.weather_variable_list or []), "precipitation", "shortwave_radiation", "wind_speed_10m", "relative_humidity_2m"])):
            if wv not in dfw.columns:
                raise Exception(f"weather missing variable {wv}")
            dfw[wv] = pd.to_numeric(dfw[wv], errors="coerce")
            if dfw[wv].isna().any():
                raise Exception(f"weather variable has missing or non-numeric values: {wv}")

        # add Date column for grouping and daily timeline
        dfw["Date"] = dfw["DateTime"].dt.date

        # ---------- normalize growth stage (must be present) ----------
        if growth_stage is None or growth_stage.empty:
            raise ValueError("growth_stage DataFrame must not be None or empty")
        gs = growth_stage.copy()
        if "Date" not in gs.columns or "Stage" not in gs.columns:
            raise ValueError("growth_stage must have columns ['Date','Stage']")
        gs["Date"] = pd.to_datetime(gs["Date"]).dt.date
        gs_map = gs.set_index("Date")["Stage"].to_dict()

        # ---------- date range ----------
        start_date = planting_date or dfw["Date"].min()
        end_date = min(gs["Date"].max(), dfw["Date"].max())
        if pd.to_datetime(start_date) > pd.to_datetime(end_date):
            return pd.DataFrame()

        # ---------- build daily frame (use Date, not DateTime) ----------
        daily_dates = pd.date_range(start=start_date, end=end_date, freq="D").date
        _ds_df = pd.DataFrame({"Date": daily_dates})
        _ds_df["eppo_code"] = self.eppo_code

        # ensure we have stages for all days
        missing = set(daily_dates) - set(gs_map.keys())
        if missing:
            raise ValueError(f"Missing growth stage entries for dates: {sorted(missing)}")

        # ---------- weather-only favorability per day ----------
        groups = dict(tuple(dfw.groupby("Date", sort=True)))
        def _calc_weather(d: datetime.date) -> float:
            day_hours = groups.get(d, pd.DataFrame(columns=dfw.columns))
            if day_hours.empty:
                return 0.0
            return float(self.cal_daily_weather_favorability(day_hours))

        _ds_df["weather_favorability"] = _ds_df["Date"].map(_calc_weather)

        # ---------- growth stage favorability ----------
        _ds_df["growth_stage_favorability"] = _ds_df["Date"].map(lambda d: self.cal_growth_stage_favorability(gs_map[d]))

        # ---------- variety susceptibility (returns [0,1]) ----------
        vlevel = 5
        if isinstance(variety_susceptibility, dict):
            vlevel = variety_susceptibility.get(self.eppo_code, 5)
        _ds_df["variety_favorability"] = float(self.cal_variety_susceptibility(vlevel))

        # ---------- fungicide effects (per day) ----------
        _ds_df["fungicide_effects"] = _ds_df["Date"].map(
            lambda d: self.fungicide_effect(applied_fungicides=applied_fungicides, day=d)
        )
        _ds_df["fungicide_overall_effect"]     = _ds_df["fungicide_effects"].apply(lambda x: x[0])
        _ds_df["fungicide_preventive_effect"]  = _ds_df["fungicide_effects"].apply(lambda x: x[1])
        _ds_df["fungicide_curative_effect"]    = _ds_df["fungicide_effects"].apply(lambda x: x[2])
        _ds_df["fungicide_eradicative_effect"] = _ds_df["fungicide_effects"].apply(lambda x: x[3])

        # ---------- final favorability and categories ----------
        _ds_df["favorability"] = (
            _ds_df["weather_favorability"]
            * _ds_df["growth_stage_favorability"]
            * _ds_df["variety_favorability"]
            * _ds_df["fungicide_overall_effect"]
        )

        _ds_df["shortterm_aggregate_favorability"] = (
            _ds_df["favorability"].rolling(int(gcfg["short_agg_days"]), min_periods=1).mean()
        )
        _ds_df["categorized_daily_favorability"] = _ds_df["favorability"].apply(
            lambda x: favorability_to_category(x, gcfg["daily_infection_risk_categories"])
        )
        _ds_df["categorized_shortterm_aggregate_favorability"] = _ds_df["shortterm_aggregate_favorability"].apply(
            lambda x: favorability_to_category(x, gcfg["short_agg_infection_risk_categories"])
        )

        # ---------- persist & return ----------
        self.daily_disease_development_favorability = pd.concat(
            [getattr(self, "daily_disease_development_favorability", pd.DataFrame()), _ds_df],
            ignore_index=True
        )

        return _ds_df[[
            "Date",
            "eppo_code",
            "variety_favorability",
            "growth_stage_favorability",
            "fungicide_overall_effect",
            "fungicide_preventive_effect",
            "fungicide_curative_effect",
            "fungicide_eradicative_effect",
            "weather_favorability",
            "favorability",
            "categorized_daily_favorability",
            "shortterm_aggregate_favorability",
            "categorized_shortterm_aggregate_favorability",
        ]]
    def predict_onset_date_with_machine_learning_model(self,disease_daily_risk:pd.DataFrame):
        '''
        temporal simple meathod
        '''
        disease_daily_risk=disease_daily_risk.loc[disease_daily_risk.categorized_shortterm_aggregate_favorability=='OPTIMAL']
        if len(disease_daily_risk)==0:
            return pd.DataFrame()
        else:
            return disease_daily_risk.loc[disease_daily_risk.DateTime==disease_daily_risk.DateTime.min()]
    def estimate_stress_risk(self,disease_daily_risk:pd.DataFrame):
        from core.utils import get_disease_insect_weed_status_and_code
        status_num=get_disease_insect_weed_status_and_code(category='code_stress')
        stress_code=get_disease_insect_weed_status_and_code(category='stress_code')

        disease_stress_risks=disease_daily_risk.copy()[['Date','eppo_code','fungicide_overall_effect','categorized_shortterm_aggregate_favorability']]
        disease_stress_risks['stress_risk']=disease_stress_risks['categorized_shortterm_aggregate_favorability']
        disease_stress_risks.loc[(disease_stress_risks.fungicide_overall_effect<1),'stress_risk']=stress_code[4]#PROTECTED
        for ind,row in disease_stress_risks.iloc[1:].iterrows():
            today = row.Date
            yesterday = row.Date + datetime.timedelta(days=-1)
            today_status = row.stress_risk
            yesterday_status = disease_stress_risks.loc[
                (disease_stress_risks.Date == yesterday),
                "stress_risk"].values[0]
            if (
                (status_num[today_status] < status_num[yesterday_status])
                and (yesterday_status != stress_code[4])
            ):
                disease_stress_risks.loc[
                    (disease_stress_risks.Date == today),
                    "stress_risk",
                ] = yesterday_status
        return disease_stress_risks[['Date','eppo_code','stress_risk']]
