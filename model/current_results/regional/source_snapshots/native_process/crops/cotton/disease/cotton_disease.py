from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pandas as pd

from crops.cotton.config import COTTON_STAGE_ORDER, DISEASE_TARGETS, PLANT_PROTECTION_RECOMMENDATION_CONFIG
from core.management import SprayRecommendation


STRESS_SEVERITY = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "PROTECTED": 4}


@dataclass(frozen=True)
class CottonDiseaseTarget:
    eppo_code: str

    @property
    def name(self) -> str:
        return DISEASE_TARGETS[self.eppo_code]["name"]

    @property
    def name_cn(self) -> str:
        return DISEASE_TARGETS[self.eppo_code]["name_cn"]


def _stage_name(value, bbch=None) -> str:
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
        return _stage_name(None, text)
    return text


def _stage_for_date(growth_stage: pd.DataFrame, day) -> str:
    if growth_stage is None or growth_stage.empty:
        raise ValueError("growth_stage is required for cotton disease risk")
    gs = growth_stage.copy()
    gs["Date"] = pd.to_datetime(gs["Date"]).dt.date
    rows = gs.loc[gs["Date"] <= day]
    if rows.empty:
        return ""
    row = rows.iloc[-1]
    return _stage_name(row.get("Stage"), row.get("BBCH"))


def _risk_level(score: float) -> str:
    if score >= 0.46:
        return "HIGH"
    if score >= 0.25:
        return "MEDIUM"
    return "LOW"


def _target_key(value: Any) -> str:
    return str(value or "").strip().replace("-", "_").replace(" ", "_").upper()


def _disease_target_aliases(value: Any) -> set[str]:
    key = _target_key(value)
    aliases = {key} if key else set()
    for code, config in DISEASE_TARGETS.items():
        normalized = {
            _target_key(code),
            _target_key(config.get("name")),
            _target_key(config.get("name_cn")),
        }
        if key in normalized:
            aliases.update(normalized)
    return aliases


def _fungicide_covers_target(item: dict[str, Any], code: str) -> bool:
    target_aliases = _disease_target_aliases(code)
    targets = item.get("target_diseases") or []
    if isinstance(targets, str):
        targets = [targets]
    coverage_aliases: set[str] = set()
    for target in targets:
        coverage_aliases.update(_disease_target_aliases(target))
    if coverage_aliases:
        return bool(coverage_aliases & target_aliases)

    stress = item.get("stress")
    if not stress:
        return True
    stress_key = _target_key(stress)
    broad_spectrum = {"*", "ALL", "ANY", "BROAD", "BROAD_SPECTRUM", "TANK_MIX", "MIXED", "广谱", "广谱杀菌剂"}
    return stress_key in broad_spectrum or bool(_disease_target_aliases(stress) & target_aliases)


def _stage_index(stage: str) -> int | None:
    return COTTON_STAGE_ORDER.index(stage) if stage in COTTON_STAGE_ORDER else None


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


def _applied_fungicide_summary(
    applied_fungicides: list[dict] | None,
    decision_date=None,
) -> tuple[int, Any | None]:
    decision_day = pd.to_datetime(decision_date).date() if decision_date is not None else None
    dates = [
        pd.to_datetime(item.get("applied_date")).date()
        for item in applied_fungicides or []
        if item.get("applied_date")
    ]
    if decision_day is not None:
        dates = [date for date in dates if date <= decision_day]
    return len(dates), max(dates) if dates else None


def _carry_forward_unprotected_risk(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["stress_risk"] = out["categorized_shortterm_aggregate_favorability"]
    out.loc[out["fungicide_overall_effect"] < 1.0, "stress_risk"] = "PROTECTED"
    for idx in range(1, len(out)):
        today_status = str(out.at[idx, "stress_risk"])
        yesterday_status = str(out.at[idx - 1, "stress_risk"])
        if (
            STRESS_SEVERITY.get(today_status, 1) < STRESS_SEVERITY.get(yesterday_status, 1)
            and yesterday_status != "PROTECTED"
        ):
            out.at[idx, "stress_risk"] = yesterday_status
    return out


class CottonDiseaseModel:
    def __init__(self, target_codes: list[str] | None = None) -> None:
        self.targets = [CottonDiseaseTarget(code) for code in (target_codes or list(DISEASE_TARGETS))]

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
        agg = df.groupby("Date").agg(
            temperature_mean=("temperature_2m", "mean"),
            relative_humidity_mean=("relative_humidity_2m", "mean"),
            precipitation_sum=("precipitation", "sum"),
        )
        return agg.reset_index()

    @staticmethod
    def _fungicide_protection(applied_fungicides: list[dict] | None, day, code: str) -> float:
        protection = 0.0
        for item in applied_fungicides or []:
            if not _fungicide_covers_target(item, code):
                continue
            applied = pd.to_datetime(item.get("applied_date")).date()
            days = (day - applied).days
            if days < 0:
                continue
            window = int(item.get("preventive_protection_days", 0) or 0)
            if days <= window:
                efficacy = float(item.get("preventive_efficacy", 0.0) or 0.0)
                protection = max(protection, efficacy * (1.0 - days / max(window + 1, 1)))
        return max(0.0, min(0.85, protection))

    def run(
        self,
        *,
        weather_hourly: pd.DataFrame,
        growth_stage: pd.DataFrame,
        variety_susceptibility: dict[str, int] | None,
        applied_fungicides: list[dict] | None,
        decision_date,
    ) -> dict[str, Any]:
        daily_weather = self._daily_weather(weather_hourly)
        daily_disease_risk: dict[str, list[dict]] = {}
        stress_risk: dict[str, list[dict]] = {}
        stress_frames: list[pd.DataFrame] = []

        for target in self.targets:
            cfg = DISEASE_TARGETS[target.eppo_code]
            raw_records = []
            susceptibility = float((variety_susceptibility or {}).get(target.eppo_code, 5) or 5)
            susceptibility_factor = 0.75 + max(0.0, min(10.0, susceptibility)) / 20.0
            for row in daily_weather.to_dict(orient="records"):
                day = row["Date"]
                stage = _stage_for_date(growth_stage, day)
                temp = float(row["temperature_mean"])
                rh = float(row["relative_humidity_mean"])
                rain = float(row["precipitation_sum"])
                temp_score = max(0.0, 1.0 - abs(temp - float(cfg["temp_optimum_c"])) / float(cfg["temp_width_c"]))
                humidity_score = max(0.0, min(1.0, (rh - 58.0) / 32.0))
                rain_score = max(0.0, min(1.0, rain / 12.0))
                stage_factor = _stage_factor(cfg, stage)
                protection = self._fungicide_protection(applied_fungicides, day, target.eppo_code)
                weather_score = max(
                    0.0,
                    min(
                        1.0,
                        0.45 * temp_score
                        + float(cfg["humidity_weight"]) * humidity_score
                        + float(cfg["rain_weight"]) * rain_score,
                    ),
                )
                score = max(0.0, min(1.0, weather_score * stage_factor * susceptibility_factor * (1.0 - protection)))
                fungicide_effect = max(0.0, min(1.0, 1.0 - protection))
                raw_records.append(
                    {
                        "Date": day,
                        "eppo_code": target.eppo_code,
                        "variety_favorability": round(susceptibility_factor, 4),
                        "growth_stage_favorability": round(stage_factor, 4),
                        "fungicide_overall_effect": round(fungicide_effect, 4),
                        "fungicide_preventive_effect": round(fungicide_effect, 4),
                        "fungicide_curative_effect": 1.0,
                        "fungicide_eradicative_effect": 1,
                        "weather_favorability": round(weather_score, 4),
                        "favorability": round(score, 4),
                    }
                )
            df_target = pd.DataFrame(raw_records).sort_values("Date").reset_index(drop=True)
            actionable_mask = df_target["growth_stage_favorability"] > 0.0
            df_target["shortterm_aggregate_favorability"] = 0.0
            df_target.loc[actionable_mask, "shortterm_aggregate_favorability"] = (
                df_target.loc[actionable_mask, "favorability"].rolling(5, min_periods=1).mean()
            )
            df_target["categorized_daily_favorability"] = df_target["favorability"].apply(_risk_level)
            df_target["categorized_shortterm_aggregate_favorability"] = df_target["shortterm_aggregate_favorability"].apply(_risk_level)
            df_target = _carry_forward_unprotected_risk(df_target)
            df_target["Date"] = pd.to_datetime(df_target["Date"]).dt.date

            daily_columns = [
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
            ]
            stress_frame = df_target[
                [
                    "Date",
                    "eppo_code",
                    "stress_risk",
                ]
            ].copy()
            daily_disease_risk[target.eppo_code] = df_target[daily_columns].to_dict(orient="records")
            stress_records = stress_frame.to_dict(orient="records")
            stress_risk[target.eppo_code] = stress_records
            stress_frames.append(stress_frame)

        stress_df = pd.concat(stress_frames, ignore_index=True) if stress_frames else pd.DataFrame()
        field_rows: list[dict[str, Any]] = []
        by_date: dict[str, list[str]] = {}
        for records in stress_risk.values():
            for row in records:
                by_date.setdefault(row["Date"], []).append(row["stress_risk"])
        for day, levels in sorted(by_date.items()):
            field_rows.append({"Date": day, "field_risk": _aggregate_field_risk(levels)})

        field_df = pd.DataFrame(field_rows, columns=["Date", "field_risk"])
        if stress_df.empty or field_df.empty:
            actions = []
        else:
            recommendation_config = PLANT_PROTECTION_RECOMMENDATION_CONFIG["disease"]
            applied_count, last_applied = _applied_fungicide_summary(applied_fungicides, decision_date)
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
            "daily_disease_risk": daily_disease_risk,
            "stress_risk": stress_risk,
            "field_risk": field_rows,
            "action_recommendations": actions,
        }
