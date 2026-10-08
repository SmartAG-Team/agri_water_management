# crops/maize/maize.py
"""
Maize crop model: growth stage simulation, spray-weather evaluation,
and disease risk pipeline (daily risk, stress risk, field risk, and actions).

Public API (stable):
- Maize.simulate_growth_stage(...)
- Maize.evaluate_spray_weather(...)
- Maize.simulate_disease_progress(...)
- Maize.field_risks_estimation(...)
- Maize.action_recommendations(...)

Conventions
-----------
* Daily outputs always use a "Date" (python datetime.date) column.
* Hourly inputs must contain "DateTime" and variables required by each disease.
* Fungicide applications may be dicts or Pydantic objects; both are supported.
"""

from __future__ import annotations

from copy import deepcopy
import datetime
import warnings
from typing import Optional, Dict, Any, List, Union, Iterable

import numpy as np
import pandas as pd

from core.crop import Crop
from crops.maize.management import MaizeManagementModel
from crops.maize.phenology import MaizePhenology
from crops.maize.spray_weather import MaizeSprayWeather
from crops.maize.disease.maize_disease import MaizeDisease
from crops.maize.insect.maize_insect import MaizeInsect
from crops.maize.irrigation.maize_irrigation import MaizeIrrigationModel
from crops.maize.nutrition.maize_nutrition import MaizeNutrition
from core.nutrition.fertilizer_inventory import normalize_fertilizer_inventory

from crops.maize.config import spray_weather_config as spconfig
from api.ocm.schemas.disease_schema import FungicideApplication  # typing-compatible
from core.management import SprayRecommendation
from core.utils import get_nutrition_status_and_code,get_disease_insect_weed_status_and_code,get_water_status_and_code



class Maize(Crop):
    """
    Maize crop class inheriting from the Crop base class.

    Initialization supports:
      - params={"variety_maturation_group": "..."}  (flat)
      - params={"phenology": {"variety_maturation_group": "..."}} (nested)
      - explicit `variety_maturation_group` argument (fallback)

    Parameters
    ----------
    planting_date : Optional[str]
        Planting date (any pandas-parseable string). Stored as `datetime.date`.
        If omitted, disease routines infer a start date from earliest hourly weather.
    params : Optional[Dict[str, Any]]
        Generic crop configuration; phenology block is normalized.
    variety_maturation_group : str, default "middle"
        Fallback maturity group if not provided in params.
    latitude, longitude : Optional[float]
        Optional geolocation.
    name : str, default "maize"
        Crop name (not passed to parent; adapt if your base class needs it).
    """

    # Field risk code ordering (low → high → protected)

    def __init__(
        self,
        planting_date: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        variety_maturation_group: str = "middle",
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        name: str = "maize",
        **_,
    ) -> None:
        # --- normalize params & phenology ---
        params = dict(params or {})
        phenology_params = params.get("phenology", {}).copy()

        # promote flat key -> phenology if present
        if "variety_maturation_group" in params:
            phenology_params.setdefault(
                "variety_maturation_group", params.pop("variety_maturation_group")
            )
        # fallback to explicit arg
        phenology_params.setdefault("variety_maturation_group", variety_maturation_group)
        if params.get("phenology_profile") is not None:
            phenology_params.setdefault("phenology_profile", params["phenology_profile"])
        if params.get("season_end") is not None:
            phenology_params.setdefault("season_end", params["season_end"])
        params["phenology"] = phenology_params

        # Store request geolocation. Services pass it both top-level and inside
        # params so direct crop construction and API calls behave the same.
        self.latitude = latitude if latitude is not None else params.get("latitude")
        self.longitude = longitude if longitude is not None else params.get("longitude")

        # planting date (store as datetime.date)
        self.planting_date: Optional[datetime.date]
        planting_date_value = planting_date if planting_date is not None else params.get("planting_date")
        if planting_date_value is not None:
            try:
                self.planting_date = pd.to_datetime(planting_date_value).date()
            except Exception as e:
                raise ValueError(f"Invalid planting_date '{planting_date_value}': {e}") from e
        else:
            self.planting_date = None

        # Parent init (adapt if your base needs different args)
        super().__init__(params=params)

        # models
        self.phenology_model = MaizePhenology(params=phenology_params)

        # configured diseases (adjust list as necessary)
        self.diseases: List[MaizeDisease] = [
            MaizeDisease(eppo_code=eppo) for eppo in ["SETOTU", "COCHHE", "PUCCPY"]
        ]
        self.insects: List[MaizeInsect] =[MaizeInsect(eppo_code=eppo) for eppo in ["SPODEX","SPOFRU","OSTFUR","HELARM"]]
        self.nutritions:List[MaizeNutrition] =[MaizeNutrition(target_code=tc) for tc in ["N","P2O5","K2O"]]
        self.management_model = MaizeManagementModel(
            latitude=float(self.latitude) if self.latitude is not None else 35.0,
            longitude=float(self.longitude) if self.longitude is not None else 114.5,
            soil_type="sandy_loam",
            irrigation_method="sprinkler",
        )
        self.nutition_stress_code=get_nutrition_status_and_code(category='stress_code')
        self.nutition_code_stress=get_nutrition_status_and_code(category='code_stress')
        self.disease_insect_weed_stress_code=get_disease_insect_weed_status_and_code(category='stress_code')
        self.disease_insect_weed_code_stress=get_disease_insect_weed_status_and_code(category='code_stress')
        # ensure unique by eppo_code and type
        self._dedupe_diseases()

    def simulate_management_progress(self, payload: dict) -> dict:
        management_model = MaizeManagementModel(
            latitude=payload["latitude"],
            longitude=payload["longitude"],
            soil_type=payload["soil_type"],
            irrigation_method=payload["irrigation_method"],
        )
        result = management_model.run(payload)
        if payload.get("management_mode") == "water_nutrition":
            nutrition_payload = self._management_payload_to_nutrition_payload(payload)
            result["nutrition_season_response"] = self.simulate_nutrition_progress(nutrition_payload)
        return result

    def _management_payload_to_nutrition_payload(self, payload: dict) -> dict:
        body = deepcopy(payload)
        fertilizer_events = []
        for event in deepcopy(body.get("applied_fertilizers") or body.get("fertilizer_history") or []):
            raw_date = event.get("Date") or event.get("date")
            if not raw_date:
                continue
            nutrients = event.get("nutrients_kg_ha") or {}
            amount = float(event.get("amount_kg_ha") or sum(float(value or 0.0) for value in nutrients.values()) or 0.0)
            if amount <= 0.0:
                continue
            fertilizer_events.append({
                "Date": str(raw_date)[:10],
                "product_key": event.get("product_key") or event.get("product_name"),
                "amount_kg_ha": amount,
                "method": event.get("method") or "soil",
                "nutrients_kg_ha": nutrients,
                "release_type": event.get("release_type"),
                "release_days": event.get("release_days"),
                "notes": event.get("notes"),
            })

        irrigation_events = []
        for event in deepcopy(body.get("applied_irrigations") or body.get("irrigation_history") or []):
            raw_date = event.get("Date") or event.get("date")
            amount = event.get("amount_mm") or event.get("water_mm") or event.get("irrigation_mm") or event.get("gross_depth_mm")
            if raw_date and float(amount or 0.0) > 0.0:
                irrigation_events.append({
                    "date": str(raw_date)[:10],
                    "amount_mm": float(amount),
                    "method": event.get("method") or body.get("irrigation_method"),
                    "notes": event.get("notes"),
                })

        for event in body.get("applied_water_fertilizer") or []:
            raw_date = event.get("date") or event.get("Date")
            if not raw_date:
                continue
            day = str(raw_date)[:10]
            water_mm = float(event.get("water_mm") or event.get("amount_mm") or 0.0)
            if water_mm > 0.0:
                irrigation_events.append({
                    "date": day,
                    "amount_mm": water_mm,
                    "method": event.get("method") or body.get("irrigation_method"),
                    "notes": event.get("notes") or "converted from applied_water_fertilizer",
                })

            nutrients = event.get("nutrients_kg_ha") or {}
            nutrient_total = sum(float(value or 0.0) for value in nutrients.values())
            product_amount = float(event.get("amount_kg_ha") or 0.0)
            if product_amount <= 0.0 and nutrient_total <= 0.0:
                continue
            fertilizer_events.append({
                "Date": day,
                "product_key": event.get("product_key") or event.get("product_name") or "water_soluble_npk",
                "amount_kg_ha": max(product_amount, nutrient_total),
                "method": event.get("method") or "fertigation",
                "nutrients_kg_ha": nutrients,
                "release_type": event.get("release_type"),
                "release_days": event.get("release_days"),
                "notes": event.get("notes") or "converted from applied_water_fertilizer",
            })

        body["applied_fertilizers"] = fertilizer_events
        body["fertilizer_history"] = fertilizer_events
        body["applied_irrigations"] = irrigation_events
        body["irrigation_history"] = irrigation_events
        return body

    def simulate_irrigation_progress(self, payload: dict) -> dict:
        irrigation_model = MaizeIrrigationModel(
            latitude=payload["latitude"],
            longitude=payload["longitude"],
            soil_type=payload["soil_type"],
            irrigation_method=payload["irrigation_method"],
        )
        return irrigation_model.run(payload)

    # -------------------------------------------------------------------------
    # Helpers & validators
    # -------------------------------------------------------------------------
    def _dedupe_diseases(self) -> None:
        """Remove duplicates by eppo_code (preserves first occurrence)."""
        seen = set()
        unique = []
        for d in self.diseases:
            if d.eppo_code not in seen:
                unique.append(d)
                seen.add(d.eppo_code)
        self.diseases = unique

    @staticmethod
    def _safe_get(obj: Union[dict, Any], key: str, default=None):
        """Return `obj[key]` if dict, else `getattr(obj, key, default)`."""
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    @classmethod
    def _fungicide_applies_to_disease(cls, af: Union[dict, FungicideApplication], eppo_code: str) -> bool:
        target_aliases = cls._maize_disease_aliases(eppo_code)
        target_values = cls._safe_get(af, "target_diseases", []) or []
        if isinstance(target_values, str):
            target_values = [target_values]
        coverage_aliases = set()
        for value in target_values:
            coverage_aliases.update(cls._maize_disease_aliases(value))
        if coverage_aliases:
            return bool(coverage_aliases & target_aliases)

        stress = cls._target_key(cls._safe_get(af, "stress", "") or "")
        broad_spectrum = {"", "*", "ALL", "ANY", "BROAD", "BROAD_SPECTRUM", "TANK_MIX", "MIXED", "广谱", "广谱杀菌剂"}
        return stress in broad_spectrum or bool(cls._maize_disease_aliases(stress) & target_aliases)

    @staticmethod
    def _target_key(value: Any) -> str:
        return str(value or "").strip().replace("-", "_").replace(" ", "_").upper()

    @classmethod
    def _maize_disease_aliases(cls, value: Any) -> set[str]:
        key = cls._target_key(value)
        aliases = {key} if key else set()
        disease_aliases = {
            "SETOTU": {"NORTHERN_CORN_LEAF_BLIGHT", "LEAF_SPOT", "EXSEROHILUM_TURCICUM"},
            "COCHHE": {"SOUTHERN_CORN_LEAF_BLIGHT", "LEAF_SPOT", "BIPOLARIS_MAYDIS"},
            "PUCCSO": {"COMMON_RUST", "RUST", "PUCCINIA_SORGHI"},
            "PUCCPY": {"SOUTHERN_RUST", "RUST", "PUCCINIA_POLYSORA"},
            "DIPDMA": {"DIPLODIA_EAR_ROT", "EAR_ROT", "STALK_ROT"},
        }
        for code, values in disease_aliases.items():
            normalized = {code, *values}
            if key in normalized:
                aliases.update(normalized)
        return aliases

    @staticmethod
    def _ensure_hourly_schema(df: pd.DataFrame, need_cols: Iterable[str] = ()) -> pd.DataFrame:
        """
        Validate & normalize hourly weather:
         - must contain 'DateTime'
         - ensure DateTime is parseable
         - ensure needed columns exist
         - sort by DateTime; add 'Date' column (date)
        """
        if df is None or df.empty:
            raise ValueError("weather_hourly must be a non-empty DataFrame.")

        df = df.copy()
        if "DateTime" not in df.columns:
            raise ValueError("weather_hourly must include 'DateTime' column.")
        try:
            df["DateTime"] = pd.to_datetime(df["DateTime"])
        except Exception as e:
            raise ValueError(f"Failed to parse 'DateTime': {e}") from e

        # Column aliasing commonly seen in sources
        if "relative_humidity_2m" not in df.columns and "relativehumidity_2m" in df.columns:
            df["relative_humidity_2m"] = df["relativehumidity_2m"]
        if "wind_speed_10m" not in df.columns and "windspeed_10m" in df.columns:
            df["wind_speed_10m"] = df["windspeed_10m"]

        missing = [c for c in need_cols if c not in df.columns]
        if missing:
            raise ValueError(f"weather_hourly missing required variables: {missing}")

        df = df.sort_values("DateTime").reset_index(drop=True)
        df["Date"] = df["DateTime"].dt.date
        return df

    @staticmethod
    def _ensure_growth_stage_schema(growth_stage: pd.DataFrame) -> pd.DataFrame:
        """
        Ensure growth stage DataFrame has columns ['Date','Stage'] and valid dates.
        """
        if growth_stage is None or growth_stage.empty:
            raise ValueError("growth_stage must be a non-empty DataFrame with ['Date','Stage'].")

        req = {"Date", "Stage"}
        missing = req - set(growth_stage.columns)
        if missing:
            raise ValueError(f"growth_stage missing required columns: {sorted(missing)}")

        gs = growth_stage.copy()
        try:
            gs["Date"] = pd.to_datetime(gs["Date"]).dt.date
        except Exception as e:
            raise ValueError(f"Failed to parse growth_stage['Date']: {e}") from e

        # Drop exact duplicate Date rows but warn
        dup_mask = gs.duplicated(subset=["Date"], keep="first")
        if dup_mask.any():
            warnings.warn(
                "growth_stage contains duplicate dates; "
                f"keeping first for: {sorted(gs.loc[dup_mask, 'Date'].unique())}"
            )
            gs = gs.loc[~dup_mask].copy()

        return gs

    @staticmethod
    def _normalize_fungicides(
        applied_fungicides: Optional[List[Union[FungicideApplication, dict]]]
    ) -> List[Union[FungicideApplication, dict]]:
        """Return a list (possibly empty). Drop Nones and non-dicts/objects with a warning."""
        if not applied_fungicides:
            return []
        out = []
        for i, af in enumerate(applied_fungicides):
            if af is None:
                continue
            if isinstance(af, (dict, FungicideApplication)):
                out.append(af)
            else:
                warnings.warn(f"Ignoring fungicide at index {i}: unsupported type {type(af).__name__}")
        return out

    @staticmethod
    def _application_dates(
        applied_treatments: Any | None,
        decision_date: datetime.date | str | None = None,
    ) -> list[datetime.date]:
        if applied_treatments is None:
            return []
        decision_day = pd.to_datetime(decision_date).date() if decision_date is not None else None
        values: list[Any] = []
        if isinstance(applied_treatments, pd.DataFrame):
            for column in ("applied_date", "Date", "date"):
                if column in applied_treatments:
                    values.extend(applied_treatments[column].tolist())
                    break
        elif isinstance(applied_treatments, list):
            for item in applied_treatments:
                if isinstance(item, dict):
                    values.extend(item.get(key) for key in ("applied_date", "Date", "date") if item.get(key) is not None)
                else:
                    values.extend(
                        getattr(item, key)
                        for key in ("applied_date", "Date", "date")
                        if getattr(item, key, None) is not None
                    )
        dates = pd.to_datetime(pd.Series(values), errors="coerce").dropna()
        applied_dates = sorted(date.date() for date in dates)
        if decision_day is not None:
            applied_dates = [date for date in applied_dates if date <= decision_day]
        return applied_dates

    # -------------------------------------------------------------------------
    # Growth & spray weather
    # -------------------------------------------------------------------------
    def simulate_growth_stage(
        self,
        weather_data: pd.DataFrame | None = None,
        dfob: Optional[pd.DataFrame] = None,
        include_diagnostics: bool = False,
    ) -> pd.DataFrame:
        """
        Compute growth stages over time.

        Parameters
        ----------
        weather_data : DataFrame
            Weather inputs for the phenology model (schema defined by MaizePhenology).
        dfob : DataFrame, optional
            Observation overrides / checkpoints.

        Returns
        -------
        DataFrame
            Daily growth stages with columns ["Date", "Stage"].
        """
        return self.phenology_model.compute_stages(
            self.planting_date,
            weather_data,
            dfob=dfob,
            include_diagnostics=include_diagnostics,
        )

    def evaluate_spray_weather(self, dfwd: pd.DataFrame, mode: str | None = None) -> pd.DataFrame:
        """
        Evaluate spray window suitability from **hourly** weather.

        Parameters
        ----------
        dfwd : DataFrame
            Hourly weather (must contain "DateTime" and variables required by MaizeSprayWeather).
        mode : {"strict","relaxed",...}, optional
            Behavior mode, forwarded to MaizeSprayWeather.

        Returns
        -------
        DataFrame
            Per-hour or per-window suitability assessment (model-specific schema).
        """
        if dfwd is None or dfwd.empty:
            raise ValueError("dfwd must be a non-empty hourly DataFrame.")
        if "DateTime" not in dfwd.columns:
            raise ValueError("dfwd must contain 'DateTime' column.")

        msw = MaizeSprayWeather(config=spconfig, mode=mode)
        return msw.evaluate(hour_data=dfwd)

    # -------------------------------------------------------------------------
    # Disease pipeline (per season)
    # -------------------------------------------------------------------------
    def simulate_disease_progress(
        self,
        weather_hourly: pd.DataFrame,
        growth_stage: pd.DataFrame,
        variety_susceptibility: Dict[str, int],
        applied_fungicides: Optional[List[Union[FungicideApplication, dict]]] = None,
        decision_date: Optional[Union[datetime.date, str]] = None,
        strict: bool = True,
    ) -> Dict[str, Any]:
        """
        Simulate disease risk & actions for each configured maize disease.

        Pipeline per disease
        --------------------
        1) Daily disease favorability from hourly weather (0..1), corrected by:
           growth stage (0..1), variety susceptibility (0..1), and fungicides.
        2) Stress risk categorization per day ("UNFAVORABLE" → "PROTECTED").
        3) Aggregate to field-level risk per day.
        4) Generate action recommendations.

        Parameters
        ----------
        weather_hourly : DataFrame
            Hourly weather with 'DateTime' and variables required by each disease.
        growth_stage : DataFrame
            Daily growth stages with columns ['Date','Stage'].
        variety_susceptibility : dict
            Mapping {eppo_code: susceptibility_group_int}. If missing, group 5 is used.
        applied_fungicides : list of dict or FungicideApplication, optional
            Season applications. Entries with `stress == eppo_code` apply to one disease; blank,
            ALL, BROAD_SPECTRUM, or TANK_MIX entries apply to all disease targets.
        strict : bool, default True
            If True, raise on per-disease errors; if False, collect errors and continue others.

        Returns
        -------
        dict
            {
              "daily_disease_risk": {eppo_code: [records...]},
              "stress_risk":        {eppo_code: [records...]},
              "field_risk":         [records...],
              "action_recommendations": [records...],
              "errors":             [{eppo_code, message}]  # only when strict=False and a disease fails
            }
        """
        # Normalize/validate frames
        if weather_hourly is None or weather_hourly.empty:
            raise ValueError("weather_hourly must be a non-empty hourly DataFrame with 'DateTime'.")

        gs = self._ensure_growth_stage_schema(growth_stage)
        applied_fungicides = self._normalize_fungicides(applied_fungicides)

        disease_risk_and_recommendations: Dict[str, Any] = {}
        daily_disease_risk: Dict[str, Any] = {}
        stress_risk: Dict[str, Any] = {}
        stress_risk_df = pd.DataFrame()


        # earliest weather date for fallback planting_date
        try:
            inferred_planting = pd.to_datetime(weather_hourly["DateTime"]).dt.date.min()
        except Exception as e:
            raise ValueError(f"weather_hourly['DateTime'] parse failed: {e}") from e

        for disease in self.diseases:
            # Preflight: ensure weather has required variables for this disease
            required_vars = list(disease.weather_variable_list or [])
            dfw = self._ensure_hourly_schema(weather_hourly, need_cols=required_vars)

            # filter fungicides intended for this eppo_code, including broad-spectrum sprays
            apfs = [af for af in applied_fungicides if self._fungicide_applies_to_disease(af, disease.eppo_code)]

            # use provided planting date or fallback
            planting_date = self.planting_date or inferred_planting

            # 1) daily favorability (returns daily DataFrame with 'Date')
            disease_daily_risk = disease.simulate_disease_daily_risk(
                planting_date=planting_date,
                weather_hourly=dfw,
                growth_stage=gs,
                variety_susceptibility=variety_susceptibility,
                applied_fungicides=apfs
            )
            if not isinstance(disease_daily_risk, pd.DataFrame) or "Date" not in disease_daily_risk.columns:
                raise ValueError(
                    f"{disease.eppo_code}: simulate_disease_daily_risk must return a DataFrame containing 'Date'."
                )

            # sanitize daily df
            ddf = disease_daily_risk.copy()
            ddf["Date"] = pd.to_datetime(ddf["Date"]).dt.date
            ddf = (
                ddf.sort_values("Date")
                .drop_duplicates(subset=["Date"], keep="last")
                .reset_index(drop=True)
            )

            # 2) stress risk per disease
            dsr = disease.estimate_stress_risk(ddf)
            required_cols = {"Date", "eppo_code", "stress_risk"}
            if not isinstance(dsr, pd.DataFrame) or required_cols - set(dsr.columns):
                raise ValueError(
                    f"{disease.eppo_code}: estimate_stress_risk must return DataFrame with {sorted(required_cols)}."
                )

            # sanitize stress df
            dsr["Date"] = pd.to_datetime(dsr["Date"]).dt.date
            dsr = (
                dsr.sort_values("Date")
                .drop_duplicates(subset=["Date"], keep="last")
                .reset_index(drop=True)
            )

            daily_disease_risk[disease.eppo_code] = ddf.to_dict(orient="records")
            stress_risk[disease.eppo_code] = dsr.to_dict(orient="records")
            stress_risk_df = pd.concat([stress_risk_df, dsr], ignore_index=True)


        disease_risk_and_recommendations["daily_disease_risk"] = daily_disease_risk
        disease_risk_and_recommendations["stress_risk"] = stress_risk

        # 3) field-level risk
        field_status = self.field_risks_estimation(stress_risk_df,stress_type='Disease_Insect_Weed')
        disease_risk_and_recommendations["field_risk"] = field_status.to_dict(orient="records")

        # 4) recommendations (guard against empty inputs)
        if stress_risk_df.empty or field_status.empty:
            recommendations = pd.DataFrame(columns=["Date", "recommendation"])
        else:
            recommendations = self.action_recommendations(
                stress_risks=stress_risk_df,
                field_status=field_status,
                applied_treatments=applied_fungicides,
                decision_date=decision_date,
            )
            if not isinstance(recommendations, pd.DataFrame):
                raise RuntimeError("action_recommendations must return a pandas DataFrame.")

        disease_risk_and_recommendations["action_recommendations"] = recommendations.to_dict(orient="records")



        return disease_risk_and_recommendations

    def field_risks_estimation(self, stress_risks: pd.DataFrame,stress_type:str) -> pd.DataFrame:
        """
        Aggregate per-disease stress risks to a single **field risk** per day.

        Parameters
        ----------
        stress_risks : DataFrame
            Concatenation of all diseases' stress risk rows; must include
            columns ["Date","eppo_code","stress_risk"].

        Returns
        -------
        DataFrame
            Columns: ["Date","risk_code"], where risk_code is an integer priority
            (UNFAVORABLE=1 < FAVORABLE=2 < OPTIMAL=3 < PROTECTED=4).

        Notes
        -----
        This function accepts that `Crop.get_field_risk_for_a_day` may return either
        an integer or a string category. Both are normalized to an integer code.
        """
        pd.set_option("display.max_columns", 200)
        if stress_type=='Nutrition':
            STATUS_TO_NUM=self.nutition_code_stress
        elif stress_type=='Disease_Insect_Weed':
            STATUS_TO_NUM=self.disease_insect_weed_code_stress
        elif stress_type=='Water':
            STATUS_TO_NUM=self.water_code_stress
        else:
            raise ValueError(f"Unknown stress_type '{stress_type}'; expected one of 'Nutrition','Disease_Insect_Weed','Water'.")

        if stress_risks is None or stress_risks.empty:
            return pd.DataFrame(columns=["Date", "stress_risk"])
        if not 'target_code' in stress_risks:
            stress_risks['target_code']=stress_risks['eppo_code']
        req = {"Date", "target_code", "stress_risk"}
        missing_cols = req - set(stress_risks.columns)
        if missing_cols:
            raise ValueError(f"stress_risks missing required columns: {sorted(missing_cols)}")

        lrsr = stress_risks.loc[:, ["Date", "target_code", "stress_risk"]].copy()
        try:
            lrsr["Date"] = pd.to_datetime(lrsr["Date"]).dt.date
        except Exception as e:
            raise ValueError(f"Failed to parse stress_risks['Date']: {e}") from e

        # Map categories; unknown categories -> warn & drop
        def _map_status(s: Any) -> Optional[int]:
            if pd.isna(s):
                return None
            s_str = str(s).upper()
            if s_str not in STATUS_TO_NUM:
                warnings.warn(f"Unknown stress_risk category '{s}'; dropping row.")
                return None
            return STATUS_TO_NUM[s_str]

        lrsr["riskCode_number"] = lrsr["stress_risk"].map(_map_status)
        lrsr = lrsr.dropna(subset=["riskCode_number"]).copy()
        if lrsr.empty:
            return pd.DataFrame(columns=["Date", "stress_risk"])

        out: List[Dict[str, Any]] = []
        for rd in sorted(lrsr["Date"].unique()):
            values = lrsr.loc[lrsr["Date"] == rd, "riskCode_number"].astype(int).values
            if len(values) == 0:
                continue
            try:
                risk_code_raw = Crop.get_field_risk_for_a_day(values,STATUS_TO_NUM)
            except Exception as e:
                raise RuntimeError(f"Crop.get_field_risk_for_a_day failed for {rd}: {e}") from e
            out.append({"Date": rd, "field_risk": risk_code_raw})
        return pd.DataFrame(out, columns=["Date", "field_risk"]).sort_values("Date").reset_index(drop=True)

    def simulate_growth(self, weather_data: list) -> None:
        """Placeholder: hook for biomass or yield simulation (not implemented)."""
        pass

    def calculate_gdd_from_temperature_mean_2m(self, temperature_mean_2m: float) -> float:
        """
        Compute daily Growing Degree Days (GDD) from mean 2 m temperature.

        Parameters
        ----------
        temperature_mean_2m : float

        Returns
        -------
        float
            Daily GDD as defined by MaizePhenology.compute_daily_gdd.
        """
        return self.phenology_model.compute_daily_gdd(temperature_mean_2m=temperature_mean_2m)

    def action_recommendations(
        self,
        stress_risks: pd.DataFrame | None = None,
        field_status: pd.DataFrame | None = None,
        early_alert_days: int = 4,
        spray_window_wide_define_by_curative_products: int = 5,
        minimum_recommendation_interval_days: int | None = 14,
        max_recommendations_per_season: int | None = 2,
        applied_treatments: Any | None = None,
        decision_date: datetime.date | str | None = None,
        stress_type:str='Disease_Insect_Weed'
    ) -> pd.DataFrame:
        """
        Generate fungicide spray recommendations from disease and field risks.

        Parameters
        ----------
        stress_risks : DataFrame
            Per-disease stress risk rows.
        field_status : DataFrame
            Field risk per day (output of field_risks_estimation).
            May contain integers (1..4) or category strings; this function
            normalizes to category strings expected by the recommender.
        early_alert_days : int, default 4
            Look-ahead window for alerts.
        spray_window_wide_define_by_curative_products : int, default 5
            Defines spray window width when curative products are present.

        Returns
        -------
        DataFrame
            Action recommendations (schema defined by Fungicide.fungicide_spray_recommendation).
        """
        if stress_type=='Nutrition':
            STATUS_TO_NUM=self.nutition_code_stress
        elif stress_type=='Disease_Insect_Weed':
            STATUS_TO_NUM=self.disease_insect_weed_code_stress
        elif stress_type=='Water':
            STATUS_TO_NUM=self.water_code_stress
        else:
            raise ValueError(f"Unknown stress_type '{stress_type}'; expected one of 'Nutrition','Disease_Insect_Weed','Water'.")
        stress_risks = stress_risks if isinstance(stress_risks, pd.DataFrame) else pd.DataFrame()
        field_status = field_status if isinstance(field_status, pd.DataFrame) else pd.DataFrame()

        if stress_risks.empty or field_status.empty:
            warnings.warn("action_recommendations called with empty inputs; returning empty DataFrame.")
            return pd.DataFrame(columns=["Date", "recommendation"])

        # --- normalize field_status.stress_risk to category strings ---
        num_to_status = {v: k for k, v in STATUS_TO_NUM.items()}  # {1:'UNFAVORABLE', ...}
        fs = field_status.copy()

        if "field_risk" not in fs.columns:
            raise ValueError("field_status must contain 'field_risk' column.")

        # If column is numeric (or looks numeric), map to string categories
        if pd.api.types.is_numeric_dtype(fs["field_risk"]):
            fs["field_risk"] = fs["field_risk"].map(num_to_status)
        else:
            # ensure upper-case strings and validate
            fs["field_risk"] = fs["field_risk"].astype(str).str.upper()

        # Validate no unknown categories remain
        unknown = set(fs["field_risk"].unique()) - set(STATUS_TO_NUM.keys())
        if unknown:
            raise ValueError(
                f"field_status.field_risk has unknown categories: {sorted(unknown)}; "
                f"expected one of {list(self.STATUS_TO_NUM.keys())}."
            )

        # Ensure Date is datetime.date
        if "Date" not in fs.columns:
            raise ValueError("field_status must contain 'Date' column.")
        fs["Date"] = pd.to_datetime(fs["Date"]).dt.date

        applied_dates = self._application_dates(applied_treatments, decision_date)
        remaining_recommendations = (
            None
            if max_recommendations_per_season is None
            else max(0, int(max_recommendations_per_season) - len(applied_dates))
        )
        action_df = SprayRecommendation.spray_recommendation(
            disease_status=stress_risks,
            field_status=fs,
            early_alert_days=early_alert_days,
            spray_window_wide_define_by_curative_products=spray_window_wide_define_by_curative_products,
            minimum_recommendation_interval_days=minimum_recommendation_interval_days,
            max_recommendations_per_season=None,
        )
        earliest_start = None
        if applied_dates and minimum_recommendation_interval_days:
            earliest_start = max(applied_dates) + datetime.timedelta(days=minimum_recommendation_interval_days)
        action_df = SprayRecommendation._suppress_recommendations_before(action_df, earliest_start)
        action_df = SprayRecommendation.apply_decision_date(action_df, decision_date)
        action_df = SprayRecommendation._limit_recommendation_windows(
            action_df,
            minimum_recommendation_interval_days=minimum_recommendation_interval_days,
            max_recommendations_per_season=remaining_recommendations,
        )
        return SprayRecommendation.normalize_plant_protection_actions(action_df)
    def simulate_insect_progress(
    self,
    weather_hourly: pd.DataFrame,
    growth_stage: Optional[pd.DataFrame] = None,
    variety_susceptibility: Optional[Dict[str, int]] = None,
    applied_insecticides: Optional[Union[pd.DataFrame, List[dict]]] = None,
    decision_date: Optional[Union[datetime.date, str]] = None,
    strict: bool = True,
) -> Dict[str, Any]:
        """
        Simulate insect population dynamics & spray timing for the configured pests.

        Pipeline per pest
        -----------------
        1) Drive development by hourly weather → daily degree-days → life-stage timeline (egg→L1…→L6→pupa→adult).
        2) From pest_config: pull initial instar distribution, instar susceptibilities, recommended products, L2 window.
        3) Apply season insecticides (亩制单位：固体 g_ai_mu；液体 ml_ai_mu):
        - Resolve effective pesticide spec = request.pesticide (master data)
            + pest_config['pesticide_adjustments'][product_key] multipliers.
        - Compute per-instar kill (Emax-ED50-hill) × 温度修正 × 雨洗 × 残效 × 龄期权重。
        4) Produce daily profile (population_before/after, dominant stage, L2_window, spray_ok).
        5) Categorize daily pressure → LOW/MEDIUM/HIGH/PROTECTED.
        6) Aggregate to field-level risk per day.
        7) Generate action recommendations.

        Parameters
        ----------
        weather_hourly : DataFrame
            Hourly weather with 'DateTime' and (temperature_2m, relative_humidity_2m, wind_speed_10m, precipitation, shortwave_radiation).
        growth_stage : DataFrame, optional
            Daily crop stages ['Date','Stage']. If None, crop-stage coupling is skipped.
        insect_eppo_codes : list of str, optional
            Subset of pests to simulate; if None, use all configured in self.insect_eppo_codes.
        applied_insecticides : DataFrame or list of dict, optional
            Season applications; each row must carry an embedded `pesticide` spec (master data).
            Units must be mu-based: g_ai_mu (solid) or ml_ai_mu (liquid).
        strict : bool, default True
            If True, raise on per-pest errors; if False, collect errors and continue others.

        Returns
        -------
        dict
            {
            "daily_insect_risk":    {code: [records...]},
            "stress_risk":          {code: [records...]},
            "field_risk":           [records...],
            "action_recommendations":[records...],
            "errors":               [{code, message}]      # when strict=False and a pest fails
            }
        """
        # ---------- 0) Validate / normalize inputs ----------
        if weather_hourly is None or weather_hourly.empty:
            raise ValueError("weather_hourly must be a non-empty hourly DataFrame with 'DateTime'.")

        # Ensure DateTime is ts and minimal columns exist; reuse your internal normalizer if present
        dfw = self._ensure_hourly_schema(
            weather_hourly,
            need_cols=["temperature_2m", "relative_humidity_2m", "wind_speed_10m", "precipitation", "shortwave_radiation"],
        )
        dfw["Date"] = pd.to_datetime(dfw["DateTime"]).dt.date

        gs = None
        if growth_stage is not None and not growth_stage.empty:
            gs = self._ensure_growth_stage_schema(growth_stage)  # expects ['Date','Stage'] with Date=dt.date
        # Normalize applied insecticides:
        # - accept DataFrame already normalized by service layer
        # - or list[dict] → DataFrame via helper
        if isinstance(applied_insecticides, list):
            ap_insect = self._normalize_insecticides(applied_insecticides)
        elif isinstance(applied_insecticides, pd.DataFrame):
            ap_insect = applied_insecticides.copy()
        else:
            ap_insect = pd.DataFrame(columns=[
                "date","product_key","physical_state","dose_value","dose_unit","ai_density_g_per_ml","pesticide","notes"
            ])
        if not ap_insect.empty:
            ap_insect["Date"] = pd.to_datetime(ap_insect["Date"]).dt.date
            ap_insect = ap_insect.sort_values("Date").reset_index(drop=True)


        planting_date = self.planting_date

        # ---------- 1) Iterate pests ----------
        insect_risk_and_recommendations: Dict[str, Any] = {}
        insects_daily_infestation_risk: Dict[str, Any] = {}
        stress_risk: Dict[str, Any] = {}
        stress_risk_df = pd.DataFrame()

        for pest in self.insects:
            code=pest.eppo_code
            # 1.1) Ensure weather has everything this pest needs (usually the DEFAULT_WEATHER_VARIABLES)
            need_vars = list(pest.weather_variable_list or [])
            dfw_pest = self._ensure_hourly_schema(dfw, need_cols=need_vars)

            # 1.2) Filter applications relevant to this pest (all are relevant; target-specific tweaks live in pest_config)
            ap_this = ap_insect.copy()

            # 1.3) initial distribution from pest_config (handled inside pest when not provided)
            # 1.4) simulate daily population (returns daily df with Date)
            insect_daily_infestation_risk=pest.simulate_insect_daily_risk(
                planting_date=planting_date,
                weather_hourly=dfw_pest,
                growth_stage=gs,
                variety_susceptibility=variety_susceptibility.get(code) if variety_susceptibility else None,
                applied_insecticides=ap_this
            )

            insects_daily_infestation_risk[code]=self.df_records_safe(insect_daily_infestation_risk)

            # 2) stress risk per insect
            isr = pest.estimate_stress_risk(insect_daily_infestation_risk)
            required_cols = {"Date", "eppo_code", "stress_risk"}
            if not isinstance(isr, pd.DataFrame) or required_cols - set(isr.columns):
                raise ValueError(
                    f"{pest.eppo_code}: estimate_stress_risk must return DataFrame with {sorted(required_cols)}."
                )

            # sanitize stress df
            isr["Date"] = pd.to_datetime(isr["Date"]).dt.date
            isr = (
                isr.sort_values("Date")
                .drop_duplicates(subset=["Date"], keep="last")
                .reset_index(drop=True)
            )
            stress_risk[pest.eppo_code] = isr.to_dict(orient="records")
            stress_risk_df = pd.concat([stress_risk_df, isr], ignore_index=True)
        insect_risk_and_recommendations={}
        insect_risk_and_recommendations["daily_insect_risk"] = insects_daily_infestation_risk
        insect_risk_and_recommendations["stress_risk"] = stress_risk
        field_status = self.field_risks_estimation(stress_risk_df,stress_type='Disease_Insect_Weed')
        insect_risk_and_recommendations["field_risk"] = self.df_records_safe(field_status)

        if stress_risk_df.empty or field_status.empty:
            recommendations = pd.DataFrame(columns=["Date", "recommendation"])
        else:
            recommendations = self.action_recommendations(
                stress_risks=stress_risk_df,
                field_status=field_status,
                applied_treatments=ap_insect,
                decision_date=decision_date,
            )
            if not isinstance(recommendations, pd.DataFrame):
                raise RuntimeError("action_recommendations must return a pandas DataFrame.")

        insect_risk_and_recommendations["action_recommendations"] = recommendations.to_dict(orient="records")
        
        return insect_risk_and_recommendations
    def df_records_safe(self,df: pd.DataFrame) -> List[Dict[str, Any]]:
        """Replace NaN/inf in a DataFrame and return JSON-serializable records."""
        if df is None or df.empty:
            return []
        df2 = df.replace({np.nan: None, np.inf: None, -np.inf: None})
        # ensure datetimes/dates are serializable
        for c in df2.columns:
            if pd.api.types.is_datetime64_any_dtype(df2[c]):
                df2[c] = df2[c].dt.strftime("%Y-%m-%dT%H:%M:%S")
            elif pd.api.types.is_datetime64_dtype(df2[c]):  # redundant safety
                df2[c] = df2[c].astype(str)
        return df2.to_dict(orient="records")
    def simulate_nutrition_progress(
        self,
        payload: Optional[dict] = None,
        *,
        weather_daily: pd.DataFrame | List[dict] | None = None,
        growth_stage: Optional[pd.DataFrame | List[dict]] = None,
        soil_status: Optional[dict] = None,
        soil_profile: Optional[List[dict]] = None,
        soil_type: Optional[str] = None,
        base_fertiliser_plan: Optional[dict] = None,
        expected_yield_kg_ha: Optional[float] = None,
        applied_fertilizers: Optional[pd.DataFrame | List[dict]] = None,
        irrigation_events: Optional[pd.DataFrame | List[dict]] = None,
        irrigation_method: Optional[str] = None,
        max_inseason_apps: int = 3,
        allow_leaf_spray: bool = True,
        strict: bool = True,
    ) -> Dict[str, Any]:
        """
        Nutrition season pipeline (daily):
        1) For each nutrient target in self.nutritions: compute daily demand/supply/stress & categories.
        2) Aggregate to field-level daily category.
        3) Produce fertilizer recommendations (base + in-season).
        """
        fertilizer_inventory = []
        if payload is not None:
            weather_daily = payload.get("weather_daily") or payload.get("weather_data")
            growth_stage = payload.get("growth_stage")
            soil_status = payload.get("soil_status") or payload.get("soil_test")
            soil_profile = payload.get("soil_profile")
            soil_type = payload.get("soil_type")
            irrigation_method = payload.get("irrigation_method")
            base_fertiliser_plan = payload.get("base_fertiliser_plan")
            expected_yield_kg_ha = payload.get("expected_yield_kg_ha") or payload.get("target_yield_kg_ha")
            applied_fertilizers = []
            for item in payload.get("applied_fertilizers") or payload.get("fertilizer_history") or []:
                normalized_item = dict(item)
                for key in ("Date", "date"):
                    if normalized_item.get(key) is not None and not isinstance(normalized_item.get(key), str):
                        normalized_item[key] = pd.to_datetime(normalized_item[key]).date().isoformat()
                applied_fertilizers.append(normalized_item)
            irrigation_events = []
            for item in payload.get("applied_irrigations") or payload.get("irrigation_history") or []:
                normalized_item = dict(item)
                for key in ("Date", "date"):
                    if normalized_item.get(key) is not None and not isinstance(normalized_item.get(key), str):
                        normalized_item[key] = pd.to_datetime(normalized_item[key]).date().isoformat()
                irrigation_events.append(normalized_item)
            max_inseason_apps = int(payload.get("max_inseason_apps", max_inseason_apps))
            allow_leaf_spray = bool(payload.get("allow_leaf_spray", allow_leaf_spray))
            fertilizer_inventory = payload.get("fertilizer_inventory") or []
        elif irrigation_events is None:
            irrigation_events = []

        # ---------- helpers ----------
        def _df_from_any_daily(entries) -> pd.DataFrame:
            if entries is None:
                return pd.DataFrame()
            if isinstance(entries, pd.DataFrame):
                df = entries.copy()
            else:
                if len(entries) == 0:
                    return pd.DataFrame()
                rows = [e.model_dump() for e in entries] if hasattr(entries[0], "model_dump") else list(entries)
                df = pd.DataFrame(rows)
            if "Date" in df.columns:
                df["Date"] = pd.to_datetime(df["Date"]).dt.date
            elif "DateTime" in df.columns:
                df["Date"] = pd.to_datetime(df["DateTime"]).dt.date
            else:
                raise ValueError("Daily weather requires 'Date' or 'DateTime'.")
            return df.sort_values("Date").reset_index(drop=True)

        def _df_from_growth_stage(gs) -> pd.DataFrame:
            if gs is None:
                return pd.DataFrame(columns=["Date", "Stage"])
            if isinstance(gs, pd.DataFrame):
                df = gs.copy()
            else:
                if len(gs) == 0:
                    return pd.DataFrame(columns=["Date", "Stage"])
                rows = [e.model_dump() for e in gs] if hasattr(gs[0], "model_dump") else list(gs)
                df = pd.DataFrame(rows)
            if {"Date", "Stage"} - set(df.columns):
                raise ValueError("growth_stage must have columns ['Date','Stage'].")
            df["Date"] = pd.to_datetime(df["Date"]).dt.date
            return df.sort_values("Date").reset_index(drop=True)
        fert_df=pd.DataFrame() if applied_fertilizers is None else (
            pd.DataFrame(applied_fertilizers) if isinstance(applied_fertilizers, list) else applied_fertilizers.copy()
        )


        # ---------- 0) inputs ----------
        dfw = _df_from_any_daily(weather_daily)
        if dfw.empty:
            raise ValueError("weather_daily must be provided and non-empty for nutrition modeling.")

        gs_df = _df_from_growth_stage(growth_stage)
        if fert_df is None:
            fert_df=pd.DataFrame()

        if expected_yield_kg_ha is None:
            expected_yield_kg_ha = getattr(self, "target_yield_kg_ha", None) or getattr(self, "expected_yield_kg_ha", None)

        # ---------- 1) ensure nutrition targets from self.nutritions ----------
        nutr_objs: List[MaizeNutrition] = []
        if hasattr(self, "nutritions") and isinstance(self.nutritions, list) and self.nutritions:
            # use as-is (filters only valid MaizeNutrition instances)
            nutr_objs = [n for n in self.nutritions if hasattr(n, "simulate_nutrition_daily_stress")]
        else:
            # lazily construct a default set
            try:
                from crops.maize.nutrition.maize_nutrition import MaizeNutrition as _MN
            except Exception as e:
                raise RuntimeError("MaizeNutrition class not found. Please implement crops.maize.nutrition.maize_nutrition.MaizeNutrition.") from e
            for code in ["N", "P2O5", "K2O", "S", "Zn"]:
                nutr_objs.append(_MN(target_code=code))

        if not nutr_objs:
            raise ValueError("No nutrition targets resolved from self.nutritions.")

        # ---------- 2) per-target daily ----------
        daily_by_target: Dict[str, List[dict]] = {}
        stress_risk: Dict[str, List[dict]] = {}
        errors: List[Dict[str, str]] = []
        stress_risk_df=pd.DataFrame()
        for nobj in nutr_objs:
            # prefer attribute name 'target' (matches maize_nutrition.py)
            code = getattr(nobj, "target", getattr(nobj, "code", "N/A"))
            try:
                # NOTE: maize_nutrition.simulate_nutrition_daily_stress expects daily weather in newest version.
                dft = nobj.simulate_nutrition_daily_stress(
                    planting_date=self.planting_date,
                    weather_daily=dfw,             # if your class wants daily, it will accept 'Date'
                    growth_stage=gs_df if not gs_df.empty else None,
                    yield_target_t_ha=(expected_yield_kg_ha or 0.0) / 1000.0,  # kg/ha -> t/ha
                    applied_fertlizers=fert_df,
                    soil_status=soil_status,
                    irrigation_events=irrigation_events,
                    soil_profile=soil_profile,
                    soil_type=soil_type,
                    irrigation_method=irrigation_method,
                    latitude=self.latitude,
                    longitude=self.longitude,
                )
                need = {
                    "Date", "target_code", "unit", "gstage",
                    "demand", "soil_supply", "fertilizer_release", "available",
                    "stress_index", "categorized_daily_stress",
                    "shortterm_aggregate_stress", "categorized_shortterm_aggregate_stress",
                }
                if not isinstance(dft, pd.DataFrame) or need - set(dft.columns):
                    raise ValueError(f"{code}: simulate_nutrition_daily_stress must return DataFrame with {sorted(need)}.")

                dft = (
                    dft.copy()
                    .sort_values("Date")
                    .drop_duplicates(subset=["Date"], keep="last")
                    .reset_index(drop=True)
                )
                dft = dft.replace({np.nan: None, np.inf: None, -np.inf: None})
                daily_by_target[str(code)] = dft.to_dict(orient="records")
        # 2) stress risk per insect
                isr = nobj.estimate_stress_risk(dft)
                required_cols = {"Date", "target_code", "stress_risk"}
                if not isinstance(isr, pd.DataFrame) or required_cols - set(isr.columns):
                    raise ValueError(
                        f"{nobj.target_code}: estimate_stress_risk must return DataFrame with {sorted(required_cols)}."
                    )

                # sanitize stress df
                isr["Date"] = pd.to_datetime(isr["Date"]).dt.date
                isr = (
                    isr.sort_values("Date")
                    .drop_duplicates(subset=["Date"], keep="last")
                    .reset_index(drop=True)
                )
                stress_risk[code] = isr.to_dict(orient="records")
                stress_risk_df = pd.concat([stress_risk_df, isr], ignore_index=True)
            except Exception as exc:
                if strict:
                    raise
                errors.append({"target": str(code), "message": str(exc)})


        # ---------- 3) field-level aggregation ----------
        # Map categories to numeric stress priority (UNFAVORABLE < FAVORABLE < OPTIMAL < PROTECTED)

        # flatten per-target rows to compute field category per day
        flat_rows = []
        for code, recs in daily_by_target.items():
            for r in recs:
                rr = dict(r)
                rr["target"] = code
                # align names for aggregation
                rr["category"] = rr.get("categorized_daily_stress")
                rr["demand_kg_ha"] = rr.get("demand")
                rr["availability_kg_ha"] = rr.get("available")
                flat_rows.append(rr)
        field_df = pd.DataFrame(flat_rows)

        field_risk = self.field_risks_estimation(stress_risk_df,stress_type='Nutrition')
        #---------- 4) recommendations ----------
        action_recs=self.fertilizer_recommendation_nutrientwise(
            planting_date=self.planting_date,
            stress_status_by_target={k: pd.DataFrame(v) for k, v in stress_risk.items()},
            daily_by_target={k: pd.DataFrame(v) for k, v in daily_by_target.items()} if daily_by_target else None,
            early_alert_days=4,
            treatment_window_days=5,
            base_split=0.6,
            applied_fertilizers=applied_fertilizers,
            soil_status=soil_status,
            expected_yield_kg_ha=expected_yield_kg_ha,
            fertilizer_inventory=fertilizer_inventory,
        )
        # ---------- 5) assemble public NutritionResponse shape ----------
        recommendation_actions = action_recs.to_dict(orient="records")
        field_rows = field_risk.to_dict(orient="records")
        out: Dict[str, Any] = {
            "daily_nutrition_risk": daily_by_target,
            "stress_risk": stress_risk,
            "field_risk": field_rows,
            "action_recommendations": recommendation_actions,
        }
        if not strict and errors:
            out["errors"] = errors
        return out
    def fertilizer_recommendation_nutrientwise(
    self,
    *,
    planting_date: datetime.date,                              # REQUIRED: base fertilizer date
    stress_status_by_target: Dict[str, pd.DataFrame],          # {'N': df, 'P2O5': df, ...} each with ['Date','stress_risk']
    daily_by_target: Optional[Dict[str, pd.DataFrame]] = None, # per-target daily rows used to size doses (['Date','demand','available','fertilizer_release'])
    early_alert_days: int = 4,
    treatment_window_days: int = 5,
    base_split: float = 0.60,                                  # share of remaining seasonal deficit assigned to BASE
    applied_fertilizers: Optional[pd.DataFrame | List[dict]] = None,  # season applications
    soil_status: Optional[dict] = None,
    expected_yield_kg_ha: Optional[float] = None,
    fertilizer_inventory: Optional[list] = None,
) -> pd.DataFrame:
        """
        Output: one row per operation date (BASE + merged IN_SEASON windows), with a 'targets' list:
        [
        {"target": "N|P2O5|K2O|S|Zn|B", "unit": "kg_ha_as_<target>|g_ha_as_<target>",
        "nutrient_amount": <float>, "product_key": <str>, "product_amount_kg_ha": <float>}, ...
        ]
        Columns: ['Date','recommendationCode','actionTypeCode','treatmentWindowCode',
                'treatmentStartDate','treatmentEndDate','source','targets']
        """
        import numpy as np
        import pandas as pd
        import datetime as _dt
        from core.utils import get_nutrition_status_and_code
        from crops.maize.nutrition.config import FERTILIZER_PRODUCTS, nutrition_config

        if not planting_date:
            raise ValueError("planting_date must be provided (base fertilizer is applied on planting_date).")

        # Only include configured targets if self.nutritions is set
        configured_targets = None
        if hasattr(self, "nutritions") and isinstance(self.nutritions, list) and self.nutritions:
            configured_targets = {getattr(n, "target", None) for n in self.nutritions}

        DEFAULT_PRODUCT = {
            "N":    "urea_46N",
            "P2O5": "DAP_18_46_0",
            "K2O":  "MOP_0_0_60",
            "S":    "ammonium_sulfate_21N_24S",
            "Zn":   "zinc_sulfate_mono_33Zn",
            "B":    "borax_11B",
        }
        PK_BASAL_PLACEMENT_GUIDANCE_CN = [
            "磷肥移动性极差，后期地表追施很难进入15-30厘米玉米根系层，建议播种时深施作底肥。",
            "玉米苗期和拔节期对磷敏感，后期补磷容易错过根系和叶片建成关键期。",
            "钾肥在土壤中移动性弱，优先底肥深施；后期明显缺钾时只建议少量叶面喷施磷酸二氢钾作应急补救。",
        ]
        stage_order = [
            "VS", "VE", "V1", "V2", "V3", "V4", "V5", "V6", "V7", "V8", "V9", "V10",
            "V11", "V12", "V13", "V14", "V15", "V16", "V16+", "VT", "R1", "R2", "R3",
            "R4", "R5", "R6",
        ]
        stage_rank = {stage: idx for idx, stage in enumerate(stage_order)}

        def _stage_rank(stage: object) -> int:
            return stage_rank.get(str(stage or "").upper(), -1)

        def _unit_for(target: str) -> str:
            return f"g_ha_as_{target}" if target in ("Zn","B") else f"kg_ha_as_{target}"

        def _product_amount_for(target: str, unit_label: str, product_key: str, nutrient_amount: float) -> float:
            prod = FERTILIZER_PRODUCTS.get(product_key, {})
            frac = float((prod.get("nutrients") or {}).get(target, 0.0))
            if frac <= 0.0 or nutrient_amount <= 0.0:
                return 0.0
            nutrient_kg = (nutrient_amount / 1000.0) if unit_label.startswith("g_ha") else nutrient_amount
            return nutrient_kg / frac

        def _product_item(product_name: str, display_name: str, amount_kg_ha: float, fractions: dict, source: str, release_type: str = "quick_release", release_days: int = 1, inventory_limited: bool = False) -> dict:
            amount = round(max(0.0, float(amount_kg_ha or 0.0)), 3)
            nutrient_fractions = {key: float(fractions.get(key, 0.0) or 0.0) for key in ("N", "P2O5", "K2O")}
            return {
                "product_name": product_name,
                "display_name": display_name,
                "amount_kg_ha": amount,
                "nutrient_fractions": nutrient_fractions,
                "nutrients_kg_ha": {key: round(amount * value, 3) for key, value in nutrient_fractions.items()},
                "source": source,
                "release_type": release_type,
                "release_days": int(release_days),
                "inventory_limited": inventory_limited,
            }

        def _sum_product_plan(product_plan: list[dict]) -> dict:
            totals = {"N": 0.0, "P2O5": 0.0, "K2O": 0.0}
            for product in product_plan:
                nutrients = product.get("nutrients_kg_ha") or {}
                for key in totals:
                    totals[key] += float(nutrients.get(key, 0.0) or 0.0)
            return {
                "n_kg_ha": round(totals["N"], 3),
                "p2o5_kg_ha": round(totals["P2O5"], 3),
                "k2o_kg_ha": round(totals["K2O"], 3),
            }

        def _scientific_product(target: str, amount_kg_ha: float) -> dict | None:
            if target == "N" and amount_kg_ha > 0:
                return _product_item("urea", "尿素", amount_kg_ha / 0.46, {"N": 0.46}, "默认常用氮肥")
            if target == "P2O5" and amount_kg_ha > 0:
                return _product_item("DAP_18_46_0", "磷酸二铵", amount_kg_ha / 0.46, {"N": 0.18, "P2O5": 0.46}, "科学补充推荐")
            if target == "K2O" and amount_kg_ha > 0:
                return _product_item("MOP_0_0_60", "氯化钾", amount_kg_ha / 0.60, {"K2O": 0.60}, "科学补充推荐")
            return None

        def _base_product_plan_from_targets(base_targets: list[dict]) -> tuple[list[dict], dict]:
            need = {"N": 0.0, "P2O5": 0.0, "K2O": 0.0}
            for target_item in base_targets:
                target = str(target_item.get("target"))
                if target in need:
                    need[target] += float(target_item.get("nutrient_amount", 0.0) or 0.0)

            inventory = normalize_fertilizer_inventory(fertilizer_inventory)
            compounds = [item for item in inventory if item.get("kind") == "compound"]
            product_plan: list[dict] = []
            remaining_p = need["P2O5"]
            remaining_k = need["K2O"]
            if compounds:
                best = None
                for item in compounds:
                    nutrients = item["nutrients"]
                    required = []
                    if remaining_p > 0 and nutrients.get("P2O5", 0.0) > 0:
                        required.append(remaining_p / float(nutrients["P2O5"]))
                    if remaining_k > 0 and nutrients.get("K2O", 0.0) > 0:
                        required.append(remaining_k / float(nutrients["K2O"]))
                    if not required:
                        continue
                    requested = max(required)
                    available = item.get("available_amount_kg_ha")
                    amount = requested if available is None else min(requested, float(available))
                    supplied_p = amount * float(nutrients.get("P2O5", 0.0) or 0.0)
                    supplied_k = amount * float(nutrients.get("K2O", 0.0) or 0.0)
                    supplied_n = amount * float(nutrients.get("N", 0.0) or 0.0)
                    pk_gap = max(0.0, remaining_p - supplied_p) + max(0.0, remaining_k - supplied_k)
                    score = -pk_gap * 20.0 - supplied_n * 0.05 - amount * 0.01
                    limited = available is not None and amount < requested - 1e-6
                    if best is None or score > best[0]:
                        best = (score, item, amount, limited)
                if best is not None:
                    _, item, amount, limited = best
                    nutrients = item["nutrients"]
                    product_plan.append(
                        _product_item(
                            item["id"],
                            item["display_name"],
                            amount,
                            nutrients,
                            "农民已有肥",
                            release_type=item["release_type"],
                            release_days=int(item["release_days"]),
                            inventory_limited=limited,
                        )
                    )
                    remaining_p = max(0.0, remaining_p - amount * float(nutrients.get("P2O5", 0.0) or 0.0))
                    remaining_k = max(0.0, remaining_k - amount * float(nutrients.get("K2O", 0.0) or 0.0))

            for target, amount in (("P2O5", remaining_p), ("K2O", remaining_k)):
                product = _scientific_product(target, amount)
                if product:
                    product_plan.append(product)

            supplied = _sum_product_plan(product_plan)
            remaining_n = max(0.0, need["N"] - supplied["n_kg_ha"])
            product = _scientific_product("N", remaining_n)
            if product:
                product_plan.append(product)

            if not product_plan:
                for target_item in base_targets:
                    target = str(target_item.get("target"))
                    product = _scientific_product(target, float(target_item.get("nutrient_amount", 0.0) or 0.0))
                    if product:
                        product_plan.append(product)
            return product_plan, _sum_product_plan(product_plan)

        def _basal_nutrient_amount(target: str, cfg: dict, dfN: pd.DataFrame, remaining_deficit: float) -> float:
            if cfg.get("soil_applied_fertilizer_model") != "residual_pool" or dfN.empty:
                return base_split * remaining_deficit
            if not {"demand", "soil_supply"}.issubset(dfN.columns):
                return remaining_deficit
            balance = (
                pd.to_numeric(dfN["demand"], errors="coerce").fillna(0.0)
                - pd.to_numeric(dfN["soil_supply"], errors="coerce").fillna(0.0)
            )
            cumulative_deficit = balance.cumsum().clip(lower=0.0)
            return max(0.0, float(cumulative_deficit.max() if len(cumulative_deficit) else remaining_deficit))

        def _soil_factor_for_p(olsen_p: float | None) -> float:
            if olsen_p is None:
                return 1.0
            if olsen_p < 15.0:
                return 1.35
            if olsen_p < 26.9:
                return 1.10
            if olsen_p <= 30.0:
                return 0.75
            if olsen_p <= 40.0:
                return 0.55
            return 0.25

        def _soil_factor_for_k(available_k: float | None) -> float:
            if available_k is None:
                return 1.0
            if available_k < 100.0:
                return 1.25
            if available_k < 120.0:
                return 1.00
            if available_k <= 220.0:
                return 0.65
            return 0.35

        def _target_yield_t_ha(target: str, cfg: dict, seasonal_demand: float) -> float:
            if expected_yield_kg_ha:
                return max(0.0, float(expected_yield_kg_ha) / 1000.0)
            coeff = float(cfg.get("yield_coeff", 0.0) or 0.0)
            if coeff > 0.0 and seasonal_demand > 0.0:
                return max(0.0, seasonal_demand / coeff)
            return 0.0

        def _basal_pk_balance_amount(target: str, cfg: dict, seasonal_demand: float) -> float:
            soil = soil_status or {}
            target_yield_t = _target_yield_t_ha(target, cfg, seasonal_demand)
            if target_yield_t <= 0.0:
                return 0.0
            if target == "P2O5":
                olsen_p_raw = soil.get("olsen_p_mg_kg")
                olsen_p = None if olsen_p_raw is None else float(olsen_p_raw)
                return max(0.0, target_yield_t * 5.0 * _soil_factor_for_p(olsen_p))
            if target == "K2O":
                available_k_raw = soil.get("available_k_mg_kg", soil.get("exchangeable_k_mg_kg"))
                available_k = None if available_k_raw is None else float(available_k_raw)
                total_uptake_coeff = 19.2
                grain_removal_coeff = 4.5
                stover_return_fraction = 0.65
                stover_k_recycling_efficiency = 0.75
                recycled_stover_fraction = stover_return_fraction * stover_k_recycling_efficiency
                effective_k_coeff = grain_removal_coeff + (1.0 - recycled_stover_fraction) * (
                    total_uptake_coeff - grain_removal_coeff
                )
                return max(0.0, target_yield_t * effective_k_coeff * _soil_factor_for_k(available_k))
            return 0.0

        # discover HIGH label (top risky status except PROTECTED)
        code_stress = get_nutrition_status_and_code(category="code_stress")
        HIGH_LABEL = "HIGH" if "HIGH" in code_stress else max(
            (k for k in code_stress if k.upper() != "PROTECTED"),
            key=lambda k: code_stress[k]
        )

        # -------------------- detect if BASE already applied on planting_date --------------------
        base_already_applied = False
        if applied_fertilizers is not None and len(applied_fertilizers) > 0:
            if isinstance(applied_fertilizers, pd.DataFrame):
                ap = applied_fertilizers.copy()
            else:
                ap = pd.DataFrame(applied_fertilizers).copy()

            # normalize Date column name + dtype
            if not ap.empty and "Date" not in ap.columns and "date" in ap.columns:
                ap = ap.rename(columns={"date": "Date"})
            if not ap.empty and "Date" in ap.columns:
                ap["Date"] = pd.to_datetime(ap["Date"], errors="coerce").dt.date
                base_already_applied = (ap["Date"] == planting_date).any()
        base_row = None
        if not base_already_applied:
            base_targets: list[dict] = []
            for target, cfg in nutrition_config.items():
                if configured_targets and target not in configured_targets:
                    continue
                unit_label = _unit_for(target)
                product_key = DEFAULT_PRODUCT.get(target)

                seasonal_demand = soil_supply = released = 0.0
                dfN = pd.DataFrame()
                if daily_by_target and target in daily_by_target and daily_by_target[target] is not None:
                    dfN = pd.DataFrame(daily_by_target[target]).copy()
                    if not dfN.empty:
                        dfN["Date"] = pd.to_datetime(dfN["Date"]).dt.date
                        for c in ["demand", "soil_supply", "fertilizer_release"]:
                            if c in dfN:
                                dfN[c] = pd.to_numeric(dfN[c], errors="coerce").fillna(0.0)
                        seasonal_demand = float(dfN.get("demand", pd.Series(dtype=float)).sum())
                        soil_supply     = float(dfN.get("soil_supply", pd.Series(dtype=float)).sum())
                        released        = float(dfN.get("fertilizer_release", pd.Series(dtype=float)).sum())

                deficit_total     = max(0.0, seasonal_demand - soil_supply)
                remaining_deficit = max(0.0, deficit_total - released)
                if target in {"P2O5", "K2O"}:
                    base_amt      = _basal_pk_balance_amount(target, cfg, seasonal_demand)
                else:
                    base_amt      = _basal_nutrient_amount(target, cfg, dfN, remaining_deficit)
                prod_kg_ha        = _product_amount_for(target, unit_label, product_key, base_amt)

                if base_amt > 0 or prod_kg_ha > 0:
                    base_targets.append({
                        "target": target,
                        "unit": unit_label,
                        "nutrient_amount": round(base_amt, 1 if unit_label.startswith("g_ha") else 2),
                        "product_key": product_key,
                        "product_amount_kg_ha": round(prod_kg_ha, 2),
                    })

            if base_targets:
                product_plan, nutrient_plan = _base_product_plan_from_targets(base_targets)
                base_row = {
                    "Date": planting_date,
                    "recommendationCode": "RECOMMENDED",
                    "actionTypeCode":     "TREAT",
                    "treatmentWindowCode": "BASE_AT_SOWING",
                    "treatmentStartDate": planting_date,
                    "treatmentEndDate":   planting_date,
                    "source": "BASE",
                    "targets": base_targets,
                    "nutrient_plan": nutrient_plan,
                    "product_plan": product_plan,
                    "reason": [
                        "播种底肥包含磷钾养分；磷钾土壤移动性弱，后期地表追施利用率低。",
                        *PK_BASAL_PLACEMENT_GUIDANCE_CN,
                    ],
                }

        # -------------------- IN-SEASON (HIGH-only → merged ops) --------------------
        raw_ops: list[dict] = []
        for target, dfr in (stress_status_by_target or {}).items():
            if configured_targets and target not in configured_targets:
                continue
            if dfr is None or len(dfr) == 0:
                continue
            if target in {"P2O5", "K2O"}:
                continue

            unit_label = _unit_for(target)
            product_key = DEFAULT_PRODUCT.get(target)

            df = dfr.copy()
            df["Date"] = pd.to_datetime(df["Date"]).dt.date
            df["cat"] = df["stress_risk"].astype(str).str.upper()

            # consecutive HIGH segments
            high_days = sorted([d for d, c in zip(df["Date"], df["cat"]) if c == HIGH_LABEL])
            if not high_days:
                continue
            segs = []
            a = high_days[0]; b = a
            for d in high_days[1:]:
                if (d - b).days == 1:
                    b = d
                else:
                    segs.append((a, b))
                    a, b = d, d
            segs.append((a, b))

            # dose sizing from daily_by_target[target]
            dfN = None
            if daily_by_target and target in daily_by_target and daily_by_target[target] is not None:
                dfN = pd.DataFrame(daily_by_target[target]).copy()
                if not dfN.empty:
                    dfN["Date"] = pd.to_datetime(dfN["Date"]).dt.date
                    for c in ["demand", "available"]:
                        if c in dfN:
                            dfN[c] = pd.to_numeric(dfN[c], errors="coerce")

            for s0, s1 in segs:
                cur_start = s0
                cur_end   = s0 + _dt.timedelta(days=treatment_window_days)
                coverage_end = s1 + _dt.timedelta(days=1)

                if target == "N" and dfN is not None and {"Date", "gstage"}.issubset(dfN.columns):
                    start_stage = next((row.get("gstage") for _, row in dfN.iterrows() if row.get("Date") == cur_start), None)
                    if _stage_rank(start_stage) >= _stage_rank("R1"):
                        pre_tassel_days = [
                            row.get("Date")
                            for _, row in dfN.iterrows()
                            if row.get("Date") <= cur_start and _stage_rank("V10") <= _stage_rank(row.get("gstage")) <= _stage_rank("V14")
                        ]
                        if pre_tassel_days:
                            cur_start = max(pre_tassel_days)
                            cur_end = cur_start + _dt.timedelta(days=treatment_window_days)

                nutrient_amt = 0.0
                if dfN is not None and {"demand","available","Date"}.issubset(dfN.columns):
                    sub = dfN[(dfN["Date"] >= cur_start) & (dfN["Date"] < coverage_end)].copy()
                    if not sub.empty:
                        sub["shortfall"] = (sub["demand"] - sub["available"]).clip(lower=0.0)
                        nutrient_amt = float(np.nansum(sub["shortfall"].values))

                prod_kg_ha = _product_amount_for(target, unit_label, product_key, nutrient_amt)
                if nutrient_amt <= 0 and prod_kg_ha <= 0:
                    continue

                raw_ops.append({
                    "start": cur_start,
                    "end":   cur_end,
                    "target_item": {
                        "target": target,
                        "unit": unit_label,
                        "nutrient_amount": round(nutrient_amt, 1 if unit_label.startswith("g_ha") else 2),
                        "product_key": product_key,
                        "product_amount_kg_ha": round(prod_kg_ha, 2),
                    }
                })

        # merge overlapping/adjacent windows across targets into single earlier operations
        merged_ops: list[dict] = []
        if raw_ops:
            raw_ops.sort(key=lambda x: x["start"])
            group = [raw_ops[0]]
            g_start, g_end = raw_ops[0]["start"], raw_ops[0]["end"]

            for r in raw_ops[1:]:
                rs, re = r["start"], r["end"]
                if rs <= g_end:        # overlap/touch
                    group.append(r)
                    if re > g_end:
                        g_end = re
                else:
                    merged_ops.append({"start": g_start, "end": g_end, "items": [x["target_item"] for x in group]})
                    group = [r]
                    g_start, g_end = rs, re
            merged_ops.append({"start": g_start, "end": g_end, "items": [x["target_item"] for x in group]})

        inseason_rows = []
        for op in merged_ops:
            nutrient_plan = {"n_kg_ha": 0.0, "p2o5_kg_ha": 0.0, "k2o_kg_ha": 0.0}
            for item in op["items"]:
                if item.get("target") == "N":
                    nutrient_plan["n_kg_ha"] += float(item.get("nutrient_amount", 0.0) or 0.0)
            nutrient_plan["n_kg_ha"] = round(nutrient_plan["n_kg_ha"], 3)
            product_plan = []
            if nutrient_plan["n_kg_ha"] > 0:
                product_plan.append(_product_item("urea", "尿素", nutrient_plan["n_kg_ha"] / 0.46, {"N": 0.46}, "默认常用追肥"))
            inseason_rows.append({
                "Date": op["start"],                        # single operation on earliest start
                "recommendationCode": "RECOMMENDED",
                "actionTypeCode": "TREAT",
                "treatmentWindowCode": "CURRENT",
                "treatmentStartDate": op["start"],
                "treatmentEndDate": op["end"],
                "source": "IN_SEASON",
                "targets": op["items"],                     # list of per-target dose/product
                "nutrient_plan": nutrient_plan,
                "product_plan": product_plan,
                "reason": ["玉米季内土壤追肥仅推荐氮肥；磷钾已按低移动性原则安排在播种底肥。"],
            })

        # -------------------- assemble (one row per operation date) --------------------
        rows = ([] if base_row is None else [base_row]) + inseason_rows
        rows = [r for r in rows if len(r.get("targets", [])) > 0]

        out = pd.DataFrame(rows, columns=[
            "Date","recommendationCode","actionTypeCode","treatmentWindowCode",
            "treatmentStartDate","treatmentEndDate","source","targets","nutrient_plan","product_plan","reason"
        ])
        if out.empty:
            return pd.DataFrame(columns=[
                "Date","recommendationCode","actionTypeCode","treatmentWindowCode",
                "treatmentStartDate","treatmentEndDate","source","targets","nutrient_plan","product_plan","reason"
            ])
        for c in ["Date","treatmentStartDate","treatmentEndDate"]:
            out[c] = pd.to_datetime(out[c]).dt.date

        return out.sort_values(["Date","source"]).reset_index(drop=True)


           

        
