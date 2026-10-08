from __future__ import annotations

from datetime import datetime
from math import acos, pi, sin, tan
from typing import Dict, Optional

import numpy as np
import pandas as pd

from core.phenology import BasePhenology
from .config import WHEAT_BBCH_ORDER, WHEAT_STAGE_LABELS, parse_wheat_bbch_stage, phen_config as CONFIG


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


class WheatPhenology(BasePhenology):
    """
    Winter wheat phenology model for the North China Plain.

    The driver is daily photo-thermal-vernal time:
      daily_ptu = thermal_time * photoperiod_factor * vernalization_factor
    """

    def __init__(self, params: Optional[Dict] = None) -> None:
        params = dict(params or {})
        maturity_group = params.get("variety_maturation_group", "middle")

        cards = CONFIG["WHEAT_CARDINAL_TEMPERATURES"]
        photo = CONFIG["WHEAT_PHOTOPERIOD"]
        vern = CONFIG["WHEAT_VERNALIZATION"]
        cultivar = CONFIG["WHEAT_CULTIVAR_GROUPS"][maturity_group]

        defaults = {
            "base_temperature": cards["base_temperature"],
            "variety_maturation_group": maturity_group,
            "latitude": cultivar["latitude"],
            "required_vernalization_days": cultivar["required_vernalization_days"],
            "photoperiod_sensitivity": cultivar["photoperiod_sensitivity"],
            "critical_daylength_hours": photo["critical_daylength_hours"],
            "optimal_daylength_hours": photo["optimal_daylength_hours"],
            "minimum_photoperiod_factor": photo["minimum_factor"],
            "minimum_vernalization_factor": vern["minimum_factor"],
        }
        defaults.update(params)
        super().__init__(defaults)

        self.opt_temp = float(cards["optimal_temperature"])
        self.max_temp = float(cards["maximum_temperature"])
        self.min_vern_temp = float(vern["minimum_temperature"])
        self.opt_vern_temp_low = float(vern["optimal_low_temperature"])
        self.opt_vern_temp_high = float(vern["optimal_high_temperature"])
        self.max_vern_temp = float(vern["maximum_temperature"])
        self.devernalization_threshold = float(vern["devernalization_threshold"])
        self.devernalization_rate = float(vern["devernalization_rate"])

        self.critical_daylength = float(defaults["critical_daylength_hours"])
        self.optimal_daylength = float(defaults["optimal_daylength_hours"])
        self.minimum_photoperiod_factor = float(defaults["minimum_photoperiod_factor"])
        self.minimum_vernalization_factor = float(defaults["minimum_vernalization_factor"])
        self.required_vernalization_days = float(defaults["required_vernalization_days"])
        self.photoperiod_sensitivity = float(defaults["photoperiod_sensitivity"])
        self.latitude = float(defaults["latitude"])

        self.stage_thresholds: Dict[int, float] = dict(
            CONFIG["WHEAT_STAGE_THRESHOLDS"][maturity_group]
        )

    def compute_thermal_time(self, tmean: float) -> float:
        tmean = float(tmean)
        if tmean <= self.base_temp or tmean >= self.max_temp:
            return 0.0
        if tmean <= self.opt_temp:
            return tmean - self.base_temp
        return (self.max_temp - tmean) * (self.opt_temp - self.base_temp) / (self.max_temp - self.opt_temp)

    def compute_vernalization_rate(self, tmean: float) -> float:
        tmean = float(tmean)
        if tmean <= self.min_vern_temp or tmean >= self.max_vern_temp:
            return 0.0
        if tmean < self.opt_vern_temp_low:
            return (tmean - self.min_vern_temp) / (self.opt_vern_temp_low - self.min_vern_temp)
        if tmean <= self.opt_vern_temp_high:
            return 1.0
        return (self.max_vern_temp - tmean) / (self.max_vern_temp - self.opt_vern_temp_high)

    def estimate_daylength(self, date_value: pd.Timestamp, latitude: Optional[float] = None) -> float:
        latitude = self.latitude if latitude is None else float(latitude)
        latitude = _clip(latitude, -65.0, 65.0)
        doy = pd.Timestamp(date_value).dayofyear
        lat_rad = latitude * pi / 180.0
        decl = 0.409 * sin((2.0 * pi * doy / 365.0) - 1.39)
        sunset_hour_angle = acos(_clip(-tan(lat_rad) * tan(decl), -1.0, 1.0))
        return 24.0 * sunset_hour_angle / pi

    def compute_photoperiod_factor(self, daylength_hours: float) -> float:
        if daylength_hours <= self.critical_daylength:
            return self.minimum_photoperiod_factor
        if daylength_hours >= self.optimal_daylength:
            return 1.0
        span = self.optimal_daylength - self.critical_daylength
        normalized = (daylength_hours - self.critical_daylength) / span
        shaped = normalized ** self.photoperiod_sensitivity
        return self.minimum_photoperiod_factor + (1.0 - self.minimum_photoperiod_factor) * shaped

    def get_stage(self, accumulated_ptu: float) -> int:
        stage = WHEAT_BBCH_ORDER[0]
        for candidate in WHEAT_BBCH_ORDER:
            if accumulated_ptu >= self.stage_thresholds[candidate]:
                stage = candidate
            else:
                break
        return stage

    def _threshold_for_observed_stage(self, value: object) -> float | None:
        try:
            code = parse_wheat_bbch_stage(value)
        except ValueError:
            return None
        if code not in self.stage_thresholds:
            return None
        return float(self.stage_thresholds[code])

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
            anchors.append((0, float(self.stage_thresholds[WHEAT_BBCH_ORDER[0]])))
        anchors = _unique_sorted_pairs_idx(anchors)
        anchor_idx = np.array([index for index, _ in anchors], dtype=int)
        anchor_thresholds = np.array([threshold for _, threshold in anchors], dtype=float)
        adjusted = _map_accum_through_anchors(raw_acc, anchor_idx, anchor_thresholds)
        adjusted[anchor_idx] = anchor_thresholds + 1e-6
        _cummax_inplace(adjusted)

        out = df.copy()
        out["AdjAccumulated_GDD"] = adjusted
        for index, value in enumerate(adjusted):
            stage_code = self.get_stage(float(value))
            out.at[index, "Stage"] = WHEAT_STAGE_LABELS.get(stage_code, f"bbch_{stage_code}")
            if "StageCode" in out.columns:
                out.at[index, "StageCode"] = stage_code
        for item in used_obs:
            stage_code = self.get_stage(float(item["threshold"]))
            out.at[item["index"], "Stage"] = WHEAT_STAGE_LABELS.get(stage_code, f"bbch_{stage_code}")
            if "StageCode" in out.columns:
                out.at[item["index"], "StageCode"] = stage_code

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

        df = weather.copy()
        if date_col not in df.columns:
            raise ValueError(f"weather must contain '{date_col}'")

        df[date_col] = pd.to_datetime(df[date_col])
        plant_dt = pd.to_datetime(planting_date)
        df = df.loc[df[date_col] >= plant_dt].sort_values(date_col).reset_index(drop=True)
        if df.empty:
            raise ValueError("No weather rows on/after planting_date.")

        if tmean_col in df.columns:
            df["_tmean"] = pd.to_numeric(df[tmean_col], errors="coerce")
        elif tmin_col in df.columns and tmax_col in df.columns:
            df["_tmean"] = (
                pd.to_numeric(df[tmin_col], errors="coerce") + pd.to_numeric(df[tmax_col], errors="coerce")
            ) / 2.0
        else:
            raise ValueError(f"Need '{tmean_col}' or both '{tmin_col}' and '{tmax_col}'.")

        if df["_tmean"].isna().any():
            raise ValueError("Temperature series contains non-numeric or missing values.")

        latitude = self.latitude if latitude is None else float(latitude)

        records = []
        accum_ptu = 0.0
        vern_state = 0.0

        for row in df[[date_col, "_tmean"]].to_dict(orient="records"):
            date_value = row[date_col]
            tmean = float(row["_tmean"])
            current_stage = self.get_stage(accum_ptu)

            thermal_time = self.compute_thermal_time(tmean)
            daylength_hours = self.estimate_daylength(date_value, latitude=latitude)
            photoperiod_factor = self.compute_photoperiod_factor(daylength_hours)

            vern_rate = self.compute_vernalization_rate(tmean)
            if vern_state < self.required_vernalization_days:
                vern_state = min(self.required_vernalization_days, vern_state + vern_rate)

            if tmean >= self.devernalization_threshold and current_stage < 31 and vern_state > 10.0:
                vern_state = max(0.0, vern_state - self.devernalization_rate)

            vern_progress = vern_state / self.required_vernalization_days
            vernalization_factor = self.minimum_vernalization_factor + (
                1.0 - self.minimum_vernalization_factor
            ) * _clip(vern_progress, 0.0, 1.0)

            if current_stage < 31:
                development_factor = photoperiod_factor * vernalization_factor
            elif current_stage < 61:
                development_factor = photoperiod_factor
            else:
                development_factor = 1.0

            daily_ptu = thermal_time * development_factor
            accum_ptu += daily_ptu
            stage = self.get_stage(accum_ptu)

            record = {
                "Date": pd.Timestamp(date_value).date(),
                "GDD": round(daily_ptu, 3),
                "Accumulated_GDD": round(accum_ptu, 3),
                "AdjAccumulated_GDD": round(accum_ptu, 3),
                "Stage": WHEAT_STAGE_LABELS.get(stage, f"bbch_{stage}"),
            }
            if include_diagnostics:
                record.update(
                    {
                        "ThermalTime": round(thermal_time, 3),
                        "DayLengthHours": round(daylength_hours, 3),
                        "PhotoperiodFactor": round(photoperiod_factor, 4),
                        "VernalizationRate": round(vern_rate, 4),
                        "VernalizationState": round(vern_state, 3),
                        "VernalizationFactor": round(vernalization_factor, 4),
                        "DevelopmentFactor": round(development_factor, 4),
                        "DailyPTU": round(daily_ptu, 3),
                        "StageCode": stage,
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
