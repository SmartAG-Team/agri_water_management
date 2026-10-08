from __future__ import annotations

from datetime import datetime
from math import acos, pi, sin, tan
from typing import Optional

import numpy as np
import pandas as pd

from core.phenology import BasePhenology
from crops.cotton.config import (
    COTTON_BBCH_COMPAT_STAGE,
    COTTON_BBCH_STAGE_LABELS,
    COTTON_BBCH_THRESHOLDS,
    COTTON_CARDINAL_TEMPERATURES,
    DEFAULT_REGION_CODE,
    REGION_PARAMS,
    STAGE_ORDER,
)


def _clip(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _cummax_inplace(values: np.ndarray) -> None:
    current = -np.inf
    for index in range(values.shape[0]):
        if values[index] < current:
            values[index] = current
        else:
            current = values[index]


def _map_accum_through_anchors(raw_acc: np.ndarray, anchor_idx: np.ndarray, anchor_thresholds: np.ndarray) -> np.ndarray:
    x = raw_acc[anchor_idx].astype(float)
    y = anchor_thresholds.astype(float)
    x_to_y = {float(xv): float(yv) for xv, yv in zip(x, y)}
    xy = sorted(x_to_y.items(), key=lambda item: item[0])
    x = np.array([item[0] for item in xy], dtype=float)
    y = np.array([item[1] for item in xy], dtype=float)
    if len(x) == 1:
        return raw_acc * (y[0] / max(x[0], 1e-9))

    adjusted = np.interp(raw_acc, x, y)
    x0, x1 = x[0], x[1]
    y0, y1 = y[0], y[1]
    left_slope = (y1 - y0) / max(x1 - x0, 1e-9)
    left_mask = raw_acc < x0
    adjusted[left_mask] = y0 + left_slope * (raw_acc[left_mask] - x0)

    x_prev, x_last = x[-2], x[-1]
    y_prev, y_last = y[-2], y[-1]
    right_slope = (y_last - y_prev) / max(x_last - x_prev, 1e-9)
    right_mask = raw_acc > x_last
    adjusted[right_mask] = y_last + right_slope * (raw_acc[right_mask] - x_last)
    return adjusted


def _unique_sorted_pairs_idx(pairs: list[tuple[int, float]]) -> list[tuple[int, float]]:
    values = {int(index): float(threshold) for index, threshold in pairs}
    return sorted(values.items(), key=lambda item: item[0])


class CottonPhenology(BasePhenology):
    """
    Rule-based cotton phenology model.

    Development is driven by adjusted GDD. The adjustment is intentionally
    small and explicit: plastic mulch accelerates establishment, and regional
    latitude/daylength plus region_code calibration slightly adjust
    vegetative/reproductive development before boll opening.
    """

    def __init__(self, params: Optional[dict] = None) -> None:
        params = dict(params or {})
        maturity_group = params.get("variety_maturation_group", "middle")
        if maturity_group not in COTTON_BBCH_THRESHOLDS:
            maturity_group = "middle"
        region_code = params.get("region_code") or DEFAULT_REGION_CODE
        region = REGION_PARAMS.get(region_code, REGION_PARAMS[DEFAULT_REGION_CODE])
        cards = COTTON_CARDINAL_TEMPERATURES

        defaults = {
            "base_temperature": cards["base_temperature"],
            "variety_maturation_group": maturity_group,
            "region_code": region_code,
            "latitude": region["latitude"],
            "mulch_enabled": True,
        }
        defaults.update(params)
        super().__init__(defaults)
        self.opt_temp = float(cards["optimal_temperature"])
        self.max_temp = float(cards["maximum_temperature"])
        self.maturity_group = maturity_group
        self.region_code = str(defaults["region_code"])
        self.region = REGION_PARAMS.get(self.region_code, REGION_PARAMS[DEFAULT_REGION_CODE])
        self.latitude = float(defaults.get("latitude") or self.region["latitude"])
        self.mulch_enabled = bool(defaults.get("mulch_enabled", True))
        self.stage_thresholds = dict(COTTON_BBCH_THRESHOLDS[maturity_group])

    def compute_thermal_time(self, tmean: float) -> float:
        tmean = float(tmean)
        if tmean <= self.base_temp or tmean >= self.max_temp:
            return 0.0
        if tmean <= self.opt_temp:
            return tmean - self.base_temp
        return (self.max_temp - tmean) * (self.opt_temp - self.base_temp) / (self.max_temp - self.opt_temp)

    def estimate_daylength(self, date_value: pd.Timestamp, latitude: Optional[float] = None) -> float:
        latitude = _clip(self.latitude if latitude is None else float(latitude), -65.0, 65.0)
        doy = pd.Timestamp(date_value).dayofyear
        lat_rad = latitude * pi / 180.0
        decl = 0.409 * sin((2.0 * pi * doy / 365.0) - 1.39)
        sunset_hour_angle = acos(_clip(-tan(lat_rad) * tan(decl), -1.0, 1.0))
        return 24.0 * sunset_hour_angle / pi

    @staticmethod
    def _photoperiod_factor(daylength_hours: float) -> float:
        if daylength_hours <= 13.0:
            return 1.0
        return _clip(1.0 + (daylength_hours - 13.0) * 0.025, 1.0, 1.07)

    def _mulch_factor(self, stage_code: int) -> float:
        if not self.mulch_enabled:
            return 1.0
        if stage_code < 20:
            return 1.0
        if stage_code < 50:
            return 1.0
        return 1.0

    def _mulch_temperature_bonus_c(self, stage_code: int) -> float:
        if not self.mulch_enabled:
            return 0.0
        if stage_code < 13:
            return 2.0
        if stage_code < 51:
            return 1.0
        return 0.0

    def get_stage_code(self, accumulated_gdd: float) -> int:
        stage = STAGE_ORDER[0]
        for candidate in STAGE_ORDER:
            if accumulated_gdd >= self.stage_thresholds[candidate]:
                stage = candidate
            else:
                break
        return stage

    def _threshold_for_observed_stage(self, value: object) -> float | None:
        text = str(value or "").strip()
        if not text:
            return None
        if text.replace(".", "", 1).isdigit():
            code = int(float(text))
            return float(self.stage_thresholds.get(code)) if code in self.stage_thresholds else None

        normalized = text.lower()
        for code, label in COTTON_BBCH_STAGE_LABELS.items():
            if text == label:
                return float(self.stage_thresholds[code])
        for code in STAGE_ORDER:
            if normalized == str(COTTON_BBCH_COMPAT_STAGE[code]).lower():
                return float(self.stage_thresholds[code])
        return None

    def _apply_observation_calibration(
        self,
        df: pd.DataFrame,
        dfob: pd.DataFrame | None,
        *,
        date_col: str,
        ob_date_col: str,
        ob_stage_col: str,
        snap_to_nearest: bool,
    ) -> pd.DataFrame:
        raw_acc = df["Accumulated_GDD"].to_numpy(dtype=float)
        anchors: list[tuple[int, float]] = []
        used_obs: list[dict] = []
        out_dates = pd.to_datetime(df[date_col])
        if dfob is not None and not dfob.empty:
            observations = dfob.copy()
            observations[ob_date_col] = pd.to_datetime(observations[ob_date_col])
            observations = observations.loc[observations[ob_date_col] >= out_dates.min()].copy()
            date_numbers = out_dates.to_numpy().astype("datetime64[D]").astype("int64")
            for _, row in observations.iterrows():
                threshold = self._threshold_for_observed_stage(row.get(ob_stage_col))
                if threshold is None:
                    continue
                if snap_to_nearest:
                    observed_day = row[ob_date_col].normalize().to_datetime64().astype("datetime64[D]").astype("int64")
                    index = int(np.argmin(np.abs(date_numbers - observed_day)))
                else:
                    matches = np.where(out_dates.dt.normalize() == row[ob_date_col].normalize())[0]
                    if len(matches) == 0:
                        continue
                    index = int(matches[0])
                anchors.append((index, threshold))
                used_obs.append(
                    {
                        "date": str(row[ob_date_col].date()),
                        "index": index,
                        "stage": str(row.get(ob_stage_col)),
                        "threshold": float(threshold),
                    }
                )

        if not anchors:
            df.attrs["calibration"] = {"anchors_used": [], "method": "identity (no observations)"}
            return df

        if min(index for index, _ in anchors) != 0:
            anchors.append((0, float(self.stage_thresholds[STAGE_ORDER[0]])))
        anchors = _unique_sorted_pairs_idx(anchors)
        anchor_idx = np.array([index for index, _ in anchors], dtype=int)
        anchor_thresholds = np.array([threshold for _, threshold in anchors], dtype=float)
        adjusted = _map_accum_through_anchors(raw_acc, anchor_idx, anchor_thresholds)
        adjusted[anchor_idx] = anchor_thresholds + 1e-6
        _cummax_inplace(adjusted)

        out = df.copy()
        out["AdjAccumulated_GDD"] = adjusted
        for index, value in enumerate(adjusted):
            stage_code = self.get_stage_code(float(value))
            out.at[index, "BBCH"] = stage_code
            out.at[index, "StageCode"] = stage_code
            out.at[index, "Stage"] = COTTON_BBCH_COMPAT_STAGE[stage_code]
            out.at[index, "StageName"] = COTTON_BBCH_STAGE_LABELS[stage_code]
        for item in used_obs:
            stage_code = self.get_stage_code(float(item["threshold"]))
            out.at[item["index"], "BBCH"] = stage_code
            out.at[item["index"], "StageCode"] = stage_code
            out.at[item["index"], "Stage"] = COTTON_BBCH_COMPAT_STAGE[stage_code]
            out.at[item["index"], "StageName"] = COTTON_BBCH_STAGE_LABELS[stage_code]

        stage_dates: dict[str, object] = {}
        for index, row in out.iterrows():
            stage_dates.setdefault(str(row["StageName"]), pd.Timestamp(row["Date"]).date())
            if "StageDates" in out.columns:
                out.at[index, "StageDates"] = {key: str(value) for key, value in stage_dates.items()}
        out.attrs["calibration"] = {
            "anchors_used": [{"day_index": int(index), "AdjGDD": float(threshold)} for index, threshold in anchors],
            "observations_used": used_obs,
            "method": "piecewise-linear in GDD space + epsilon snap + monotone",
        }
        return out

    def compute_stages(
        self,
        planting_date: str | datetime,
        weather: pd.DataFrame,
        *,
        date_col: str = "DateTime",
        tmean_col: str = "temperature_2m_mean",
        tmin_col: str = "temperature_2m_min",
        tmax_col: str = "temperature_2m_max",
        latitude: Optional[float] = None,
        include_diagnostics: bool = False,
        dfob: Optional[pd.DataFrame] = None,
        ob_date_col: str = "DateTime",
        ob_stage_col: str = "ObservedStage",
        return_calibration: bool = True,
        snap_to_nearest: bool = True,
    ) -> pd.DataFrame:
        if weather is None or weather.empty:
            raise ValueError("weather dataframe is empty")
        if date_col not in weather.columns:
            raise ValueError(f"weather must contain '{date_col}'")

        df = weather.copy()
        df[date_col] = pd.to_datetime(df[date_col])
        plant_dt = pd.to_datetime(planting_date)
        df = df.loc[df[date_col] >= plant_dt].sort_values(date_col).reset_index(drop=True)
        if df.empty:
            raise ValueError("No weather rows on/after planting_date.")

        if tmean_col in df.columns:
            df["_tmean"] = pd.to_numeric(df[tmean_col], errors="coerce")
        elif tmin_col in df.columns and tmax_col in df.columns:
            df["_tmean"] = (
                pd.to_numeric(df[tmin_col], errors="coerce")
                + pd.to_numeric(df[tmax_col], errors="coerce")
            ) / 2.0
        else:
            raise ValueError(f"Need '{tmean_col}' or both '{tmin_col}' and '{tmax_col}'.")
        if df["_tmean"].isna().any():
            raise ValueError("Temperature series contains non-numeric or missing values.")

        latitude = self.latitude if latitude is None else float(latitude)
        accumulated = 0.0
        records: list[dict] = []
        stage_dates: dict[str, object] = {}

        for row in df[[date_col, "_tmean"]].to_dict(orient="records"):
            date_value = row[date_col]
            tmean = float(row["_tmean"])
            stage_before = self.get_stage_code(accumulated)
            mulch_temperature_bonus = self._mulch_temperature_bonus_c(stage_before)
            effective_temperature = tmean + mulch_temperature_bonus
            thermal_time = self.compute_thermal_time(effective_temperature)
            daylength = self.estimate_daylength(date_value, latitude=latitude)
            photoperiod_factor = self._photoperiod_factor(daylength) if stage_before < 81 else 1.0
            region_factor = float(self.region.get("thermal_time_factor", 1.0))
            development_factor = photoperiod_factor * self._mulch_factor(stage_before) * region_factor
            daily_gdd = thermal_time * development_factor
            accumulated += daily_gdd
            stage_code = self.get_stage_code(accumulated)
            stage = COTTON_BBCH_COMPAT_STAGE[stage_code]
            stage_name = COTTON_BBCH_STAGE_LABELS[stage_code]
            stage_dates.setdefault(stage_name, pd.Timestamp(date_value).date())
            record = {
                "Date": pd.Timestamp(date_value).date(),
                "GDD": round(daily_gdd, 3),
                "Accumulated_GDD": round(accumulated, 3),
                "AdjAccumulated_GDD": round(accumulated, 3),
                "Stage": stage,
                "BBCH": stage_code,
                "StageName": stage_name,
            }
            if include_diagnostics:
                record.update(
                    {
                        "StageCode": stage_code,
                        "ThermalTime": round(thermal_time, 3),
                        "AirTemperatureMeanC": round(tmean, 3),
                        "MulchTemperatureBonusC": round(mulch_temperature_bonus, 3),
                        "EffectiveSoilTemperatureC": round(effective_temperature, 3),
                        "DayLengthHours": round(daylength, 3),
                        "PhotoperiodFactor": round(photoperiod_factor, 4),
                        "MulchFactor": round(self._mulch_factor(stage_before), 4),
                        "RegionFactor": round(region_factor, 4),
                        "DevelopmentFactor": round(development_factor, 4),
                        "StageDates": {k: str(v) for k, v in stage_dates.items()},
                    }
                )
            records.append(record)

        out = pd.DataFrame.from_records(records)
        out = self._apply_observation_calibration(
            out,
            dfob,
            date_col="Date",
            ob_date_col=ob_date_col,
            ob_stage_col=ob_stage_col,
            snap_to_nearest=snap_to_nearest,
        )
        if not return_calibration:
            out.attrs.pop("calibration", None)
        return out
