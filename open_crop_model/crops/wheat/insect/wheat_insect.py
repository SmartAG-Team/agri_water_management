from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pandas as pd

from core.management import SprayRecommendation
from crops.wheat.config import INSECT_TARGETS, PLANT_PROTECTION_RECOMMENDATION_CONFIG, WHEAT_STAGE_ORDER


STRESS_SEVERITY = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "PROTECTED": 4}


@dataclass(frozen=True)
class WheatInsectTarget:
    eppo_code: str

    @property
    def name(self) -> str:
        return INSECT_TARGETS[self.eppo_code]["name"]

    @property
    def name_cn(self) -> str:
        return INSECT_TARGETS[self.eppo_code]["name_cn"]


def _stage_name(value, bbch=None) -> str:
    if bbch is not None:
        code = int(float(bbch))
        if code >= 89:
            return "maturity"
        if code >= 71:
            return "grain_filling"
        if code >= 61:
            return "flowering"
        if code >= 51:
            return "heading"
        if code >= 41:
            return "booting"
        if code >= 31:
            return "jointing"
        if code >= 21:
            return "tillering"
        if code >= 10:
            return "seedling"
        return "sowing"
    text = str(value or "").strip().lower()
    if text.isdigit():
        return _stage_name(None, text)
    return {
        "播种期": "sowing",
        "出苗期": "seedling",
        "分蘖期": "tillering",
        "拔节期": "jointing",
        "孕穗期": "booting",
        "抽穗期": "heading",
        "开花期": "flowering",
        "灌浆期": "grain_filling",
        "成熟期": "maturity",
    }.get(text, text)


def _stage_for_date(growth_stage: pd.DataFrame | None, day) -> str:
    if growth_stage is None or growth_stage.empty:
        raise ValueError("growth_stage is required for wheat insect risk")
    gs = growth_stage.copy()
    gs["Date"] = pd.to_datetime(gs["Date"]).dt.date
    rows = gs.loc[gs["Date"] <= day]
    if rows.empty:
        return ""
    row = rows.iloc[-1]
    return _stage_name(row.get("Stage"), row.get("BBCH"))


def _risk_level(score: float) -> str:
    if score >= 0.45:
        return "HIGH"
    if score >= 0.22:
        return "MEDIUM"
    return "LOW"


def _stage_index(stage: str) -> int | None:
    return WHEAT_STAGE_ORDER.index(stage) if stage in WHEAT_STAGE_ORDER else None


def _stage_factor(cfg: dict[str, Any], stage: str) -> float:
    stage_idx = _stage_index(stage)
    if stage_idx is None:
        return 0.0
    curve = cfg.get("stage_favorability_curve") or {}
    stages = curve.get("stages") or []
    values = curve.get("favorability") or []
    if not stages or len(stages) != len(values):
        return 0.0
    pairs = [
        (idx, float(value))
        for curve_stage, value in zip(stages, values)
        if (idx := _stage_index(curve_stage)) is not None
    ]
    if not pairs:
        return 0.0
    pairs = sorted(pairs)
    if stage_idx <= pairs[0][0]:
        return max(0.0, min(1.5, pairs[0][1]))
    if stage_idx >= pairs[-1][0]:
        return max(0.0, min(1.5, pairs[-1][1]))
    for (left_idx, left_val), (right_idx, right_val) in zip(pairs, pairs[1:]):
        if left_idx <= stage_idx <= right_idx:
            frac = (stage_idx - left_idx) / max(1, right_idx - left_idx)
            return max(0.0, min(1.5, left_val + frac * (right_val - left_val)))
    return 0.0


def _aggregate_field_risk(levels: list[str]) -> str:
    values = [STRESS_SEVERITY.get(str(level).upper(), 1) for level in levels]
    if not values:
        return "LOW"
    max_value = max(values)
    if max_value == STRESS_SEVERITY["PROTECTED"]:
        unprotected = [value for value in values if value < STRESS_SEVERITY["PROTECTED"]]
        if not unprotected:
            return "PROTECTED"
        risk_not_under_protection = max(unprotected)
        if risk_not_under_protection > STRESS_SEVERITY["LOW"]:
            return {value: key for key, value in STRESS_SEVERITY.items()}[risk_not_under_protection]
        return "PROTECTED"
    return {value: key for key, value in STRESS_SEVERITY.items()}[max_value]


def _applied_insecticide_summary(
    applied_insecticides: pd.DataFrame | None,
    decision_date=None,
) -> tuple[int, Any | None]:
    if applied_insecticides is None or applied_insecticides.empty or "Date" not in applied_insecticides:
        return 0, None
    decision_day = pd.to_datetime(decision_date).date() if decision_date is not None else None
    dates = pd.to_datetime(applied_insecticides["Date"], errors="coerce").dropna().dt.date
    if decision_day is not None:
        dates = dates[dates <= decision_day]
    return int(len(dates)), max(dates) if len(dates) else None


def _carry_forward_unprotected_risk(df: pd.DataFrame, protection_col: str) -> pd.DataFrame:
    out = df.copy()
    out["stress_risk"] = out["categorized_shortterm_aggregate_favorability"]
    out.loc[out[protection_col] > 0, "stress_risk"] = "PROTECTED"
    for idx in range(1, len(out)):
        today = str(out.at[idx, "stress_risk"])
        yesterday = str(out.at[idx - 1, "stress_risk"])
        if STRESS_SEVERITY.get(today, 1) < STRESS_SEVERITY.get(yesterday, 1) and yesterday != "PROTECTED":
            out.at[idx, "stress_risk"] = yesterday
    return out


class WheatInsectModel:
    def __init__(self, target_codes: list[str] | None = None) -> None:
        self.targets = [WheatInsectTarget(code) for code in (target_codes or list(INSECT_TARGETS))]

    @staticmethod
    def _daily_weather(weather_hourly: pd.DataFrame) -> pd.DataFrame:
        df = weather_hourly.copy()
        df["DateTime"] = pd.to_datetime(df["DateTime"])
        df["Date"] = df["DateTime"].dt.date
        if "relative_humidity_2m" not in df.columns and "relativehumidity_2m" in df.columns:
            df["relative_humidity_2m"] = df["relativehumidity_2m"]
        required = ["temperature_2m", "relative_humidity_2m", "precipitation"]
        missing = [col for col in required if col not in df.columns]
        if missing:
            raise ValueError(f"hourly weather missing required field(s): {', '.join(missing)}")
        for col in required:
            df[col] = pd.to_numeric(df[col], errors="coerce")
            if df[col].isna().any():
                raise ValueError(f"hourly weather.{col} has missing or non-numeric values")
        return df.groupby("Date").agg(
            temperature_mean=("temperature_2m", "mean"),
            relative_humidity_mean=("relative_humidity_2m", "mean"),
            precipitation_sum=("precipitation", "sum"),
        ).reset_index()

    @staticmethod
    def _insecticide_protection(applied_insecticides: pd.DataFrame | None, day) -> float:
        if applied_insecticides is None or applied_insecticides.empty:
            return 0.0
        protection = 0.0
        for item in applied_insecticides.to_dict(orient="records"):
            applied = pd.to_datetime(item.get("Date")).date()
            days = (day - applied).days
            if days < 0:
                continue
            residual = (item.get("pesticide") or {}).get("residual") or {}
            half_life = float(residual.get("half_life_days", residual.get("duration_days", 5.0)) or 5.0)
            if days <= half_life * 2.2:
                dose_response = (item.get("pesticide") or {}).get("dose_response") or {}
                max_mortality = float(dose_response.get("max_mortality", dose_response.get("Emax", 0.70)) or 0.70)
                protection = max(protection, max_mortality * max(0.0, 1.0 - days / max(half_life * 2.2, 1.0)))
        return max(0.0, min(0.85, protection))

    @staticmethod
    def _population_recovery_factor(applied_insecticides: pd.DataFrame | None, day) -> float:
        if applied_insecticides is None or applied_insecticides.empty:
            return 1.0
        recovery_factor = 1.0
        for item in applied_insecticides.to_dict(orient="records"):
            applied = pd.to_datetime(item.get("Date")).date()
            days = (day - applied).days
            if days < 0:
                continue
            pesticide = item.get("pesticide") or {}
            residual = pesticide.get("residual") or {}
            duration = float(residual.get("duration_days", residual.get("half_life_days", 10.0) * 2.2) or 10.0)
            recovery_days = max(35.0, min(56.0, duration * 4.5))
            if days > recovery_days:
                continue
            dose_response = pesticide.get("dose_response") or {}
            max_mortality = float(dose_response.get("max_mortality", dose_response.get("Emax", 0.70)) or 0.70)
            remaining_effect = max(0.0, 1.0 - days / recovery_days)
            recovery_factor = min(recovery_factor, 1.0 - max_mortality * remaining_effect)
        return max(0.18, min(1.0, recovery_factor))

    def run(
        self,
        *,
        weather_hourly: pd.DataFrame,
        growth_stage: pd.DataFrame | None,
        variety_susceptibility: dict[str, int] | None,
        applied_insecticides: pd.DataFrame | None,
        decision_date,
    ) -> dict[str, Any]:
        daily_weather = self._daily_weather(weather_hourly)
        daily_risk: dict[str, list[dict]] = {}
        stress_risk: dict[str, list[dict]] = {}

        for target in self.targets:
            cfg = INSECT_TARGETS[target.eppo_code]
            susceptibility = float((variety_susceptibility or {}).get(target.eppo_code, 5) or 5)
            susceptibility_factor = 0.75 + max(0.0, min(10.0, susceptibility)) / 20.0
            population_index = 0.16
            records = []
            for row in daily_weather.to_dict(orient="records"):
                day = row["Date"]
                stage = _stage_for_date(growth_stage, day)
                temp = float(row["temperature_mean"])
                rh = float(row["relative_humidity_mean"])
                rain = float(row["precipitation_sum"])
                temp_score = max(0.0, 1.0 - abs(temp - float(cfg["temp_optimum_c"])) / float(cfg["temp_width_c"]))
                humidity_score = max(0.0, 1.0 - abs(rh - float(cfg["humidity_optimum_pct"])) / 45.0)
                rain_penalty = max(0.62, 1.0 - min(0.38, rain / 32.0))
                stage_factor = _stage_factor(cfg, stage)
                protection = self._insecticide_protection(applied_insecticides, day)
                population_recovery = self._population_recovery_factor(applied_insecticides, day)
                weather_score = max(0.0, min(1.0, (0.58 * temp_score + 0.32 * humidity_score) * rain_penalty))
                growth = weather_score * stage_factor * susceptibility_factor * population_recovery
                population_index = max(0.04, min(1.0, population_index * 0.84 + growth * 0.24))
                effective_population_index = max(0.04, min(1.0, population_index * (1.0 - protection)))
                if protection > 0.0:
                    population_index = effective_population_index
                score = effective_population_index * stage_factor * population_recovery
                records.append({
                    "Date": day,
                    "eppo_code": target.eppo_code,
                    "stress": target.eppo_code,
                    "insect": target.eppo_code,
                    "insect_name": cfg["name"],
                    "insect_name_cn": cfg["name_cn"],
                    "stress_name": cfg["name"],
                    "stress_name_cn": cfg["name_cn"],
                    "Stage": stage,
                    "weather_favorability": round(weather_score, 4),
                    "growth_stage_favorability": round(stage_factor, 4),
                    "variety_favorability": round(susceptibility_factor, 4),
                    "insecticide_protection": round(protection, 4),
                    "population_recovery_factor": round(population_recovery, 4),
                    "population_index": round(population_index, 4),
                    "favorability": round(score, 4),
                    "risk_score": round(score, 4),
                    "temperature_mean_c": round(temp, 3),
                    "relative_humidity_mean_pct": round(rh, 3),
                    "precipitation_mm": round(rain, 3),
                })
            df_target = pd.DataFrame(records).sort_values("Date").reset_index(drop=True)
            actionable_mask = df_target["growth_stage_favorability"] > 0.0
            df_target["shortterm_aggregate_favorability"] = 0.0
            df_target.loc[actionable_mask, "shortterm_aggregate_favorability"] = (
                df_target.loc[actionable_mask, "favorability"].rolling(5, min_periods=1).mean()
            )
            df_target["categorized_daily_favorability"] = df_target["favorability"].apply(_risk_level)
            df_target["categorized_shortterm_aggregate_favorability"] = df_target["shortterm_aggregate_favorability"].apply(_risk_level)
            df_target = _carry_forward_unprotected_risk(df_target, "insecticide_protection")
            df_target["Date"] = pd.to_datetime(df_target["Date"]).dt.date
            daily_columns = [column for column in df_target.columns if column != "stress_risk"]
            daily_risk[target.eppo_code] = df_target[daily_columns].to_dict(orient="records")
            stress_frame = df_target[[
                "Date",
                "eppo_code",
                "stress_risk",
            ]].copy()
            stress_risk[target.eppo_code] = stress_frame.to_dict(orient="records")

        by_date: dict[Any, list[str]] = {}
        for records in stress_risk.values():
            for row in records:
                by_date.setdefault(row["Date"], []).append(row["stress_risk"])
        field_risk = [
            {"Date": day, "field_risk": _aggregate_field_risk(levels)}
            for day, levels in sorted(by_date.items())
        ]
        stress_df = pd.concat(
            [pd.DataFrame(records) for records in stress_risk.values()],
            ignore_index=True,
        ) if stress_risk else pd.DataFrame()
        field_df = pd.DataFrame(field_risk, columns=["Date", "field_risk"])
        if stress_df.empty or field_df.empty:
            actions = []
        else:
            recommendation_config = PLANT_PROTECTION_RECOMMENDATION_CONFIG["insect"]
            applied_count, last_applied = _applied_insecticide_summary(applied_insecticides, decision_date)
            min_interval_days = recommendation_config["minimum_recommendation_interval_days"]
            max_recommendations = max(
                0,
                int(recommendation_config["max_recommendations_per_season"])
                - applied_count,
            )
            action_df = SprayRecommendation.spray_recommendation(
                disease_status=stress_df,
                field_status=field_df,
                early_alert_days=recommendation_config["early_alert_days"],
                spray_window_wide_define_by_curative_products=recommendation_config["spray_window_days"],
                minimum_recommendation_interval_days=min_interval_days,
                max_recommendations_per_season=None,
            )
            earliest_start = (
                last_applied + timedelta(days=min_interval_days)
                if last_applied is not None
                else None
            )
            action_df = SprayRecommendation._suppress_recommendations_before(action_df, earliest_start)
            action_df = SprayRecommendation.apply_decision_date(action_df, decision_date)
            action_df = SprayRecommendation._limit_recommendation_windows(
                action_df,
                minimum_recommendation_interval_days=min_interval_days,
                max_recommendations_per_season=max_recommendations,
            )
            action_df = SprayRecommendation.normalize_plant_protection_actions(action_df)
            actions = action_df.to_dict(orient="records")
        return {
            "daily_insect_risk": daily_risk,
            "stress_risk": stress_risk,
            "field_risk": field_risk,
            "action_recommendations": actions,
        }
