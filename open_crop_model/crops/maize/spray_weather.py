# maize/spray_weather.py
import numpy as np
import pandas as pd
from core.spray_weather import BaseSprayWeather  # 你的基类

class MaizeSprayWeather(BaseSprayWeather):
    def __init__(self, config=None,mode=None):
        super().__init__(config,mode=mode)
        # 读取参数（仍用通用key：temperature/humidity/wind_speed/precipitation/solar_radiation 等）
        self.parameters = {
            "fungicide": self.get_parameters("fungicide"),
            "herbicide": self.get_parameters("herbicide"),
            "insecticide": self.get_parameters("insecticide"),
            "foliar_nutrition": self.get_parameters("foliar_nutrition") or self.get_parameters("fungicide"),
        }
        # ✅ 用你提供的列名作为默认映射（可被 config["COLUMN_MAP"] 覆盖）
        default_map = {
            "temperature": "temperature_2m",
            "humidity": "relative_humidity_2m",
            "wind_speed": "wind_speed_10m",
            "precipitation": "precipitation",
            "solar_radiation": "shortwave_radiation",
            # 可选：如参数用到这些key时也能映射
            "vpd": "vapor_pressure_deficit",
            "dew_point": "dewpoint_2m",
            "cloud_cover": "cloudcover",
        }
        self.column_map: dict = (config or {}).get("COLUMN_MAP", default_map)
        self.dt_col = (config or {}).get("DATETIME_COL", "DateTime")

    # ---------------- helper ----------------
    def _resolve_col(self, key: str, available_cols: set) -> str:
        # 明确映射
        if key in self.column_map:
            col = self.column_map[key]
            if col in available_cols:
                return col
        # 容错：如果 key 本身就是原始列名，也允许直接使用
        if key in available_cols:
            # 写回映射，后续就不再查
            self.column_map[key] = key
            return key

        raise KeyError(
            f"Cannot find column for key '{key}'. "
            f"Set COLUMN_MAP for it. Available: {sorted(list(available_cols))}"
        )

    @staticmethod
    def _sigmoid_vec(x: pd.Series, x0: float, k: float) -> pd.Series:
        # 避免溢出
        z = -k * (x - x0)
        # 限制范围以避免exp爆炸
        z = np.clip(z, -60, 60)
        return 1.0 / (1.0 + np.exp(z))

    def _score_block(self, df: pd.DataFrame, params: list[dict], available_cols: set) -> pd.Series:
        if not params:
            return pd.Series(0.0, index=df.index, dtype=float)

        comps = []
        for p in params:
            key = p["key"]              # 例如 "temperature"
            x0 = float(p["x0"])
            k  = float(p["k"])
            negate = bool(p.get("negate", False))

            col = self._resolve_col(key, available_cols)  # 映射到你给的列名
            v = pd.to_numeric(df[col], errors="coerce")
            if negate:
                v = -v
            s = self._sigmoid_vec(v, x0, k)
            comps.append(s)

        return pd.concat(comps, axis=1).mean(axis=1)

    def _optional_numeric(self, df: pd.DataFrame, key: str, available_cols: set) -> pd.Series | None:
        try:
            col = self._resolve_col(key, available_cols)
        except KeyError:
            return None
        return pd.to_numeric(df[col], errors="coerce")

    def _spray_safety_flags(self, df: pd.DataFrame, available_cols: set) -> tuple[pd.Series, pd.Series]:
        flags: list[list[str]] = [[] for _ in range(len(df))]
        severe_flags: list[list[str]] = [[] for _ in range(len(df))]
        limits = self.safety_limits

        def add_flag(mask: pd.Series, name: str, *, severe: bool = False) -> None:
            mask = mask.fillna(False)
            target = severe_flags if severe else flags
            for idx in df.index[mask]:
                target[int(idx)].append(name)

        temp = self._optional_numeric(df, "temperature_2m", available_cols)
        if temp is not None:
            add_flag(temp < float(limits.get("temperature_min", -999.0)), "temperature_low")
            add_flag(temp > float(limits.get("temperature_max", 999.0)), "temperature_high")
            add_flag(temp < float(limits.get("severe_temperature_min", -999.0)), "temperature_low", severe=True)
            add_flag(temp > float(limits.get("severe_temperature_max", 999.0)), "temperature_high", severe=True)

        rh = self._optional_numeric(df, "relative_humidity_2m", available_cols)
        if rh is not None:
            add_flag(rh < float(limits.get("relative_humidity_min", -999.0)), "humidity_low")
            add_flag(rh < float(limits.get("severe_relative_humidity_min", -999.0)), "humidity_low", severe=True)

        vpd = self._optional_numeric(df, "vpd", available_cols)
        if vpd is not None:
            add_flag(vpd > float(limits.get("vpd_max", 999.0)), "vpd_high")
            add_flag(vpd > float(limits.get("severe_vpd_max", 999.0)), "vpd_high", severe=True)

        wind = self._optional_numeric(df, "wind_speed_10m", available_cols)
        if wind is not None:
            add_flag(wind > float(limits.get("wind_speed_max", 999.0)), "wind_high")
            add_flag(wind > float(limits.get("severe_wind_speed_max", 999.0)), "wind_high", severe=True)

        precip = self._optional_numeric(df, "precipitation", available_cols)
        if precip is not None:
            add_flag(precip > float(limits.get("precipitation_max", 999.0)), "rain")
            add_flag(precip > float(limits.get("severe_precipitation_max", 999.0)), "rain", severe=True)

        radiation = self._optional_numeric(df, "shortwave_radiation", available_cols)
        if radiation is not None:
            add_flag(radiation > float(limits.get("shortwave_radiation_max", 999.0)), "radiation_high")
            add_flag(radiation > float(limits.get("severe_shortwave_radiation_max", 999.0)), "radiation_high", severe=True)

        flag_text = pd.Series([",".join(sorted(set(item))) for item in flags], index=df.index, dtype=object)
        severe_text = pd.Series([",".join(sorted(set(item))) for item in severe_flags], index=df.index, dtype=object)
        return flag_text, severe_text

    @staticmethod
    def _apply_safety_caps(score: pd.Series, flags: pd.Series, severe_flags: pd.Series) -> pd.Series:
        capped = score.copy()
        has_flag = flags.astype(str) != ""
        has_severe = severe_flags.astype(str) != ""
        capped = capped.where(~has_flag, np.minimum(capped, 0.64))
        capped = capped.where(~has_severe, np.minimum(capped, 0.24))
        return capped.round(3)

    # ---------------- main ----------------
    def evaluate(self, hour_data: pd.DataFrame) -> pd.DataFrame:
        """
        输入: 列包含
        DateTime, temperature_2m, relative_humidity_2m, vapor_pressure_deficit,
        precipitation, dewpoint_2m, windspeed_10m, cloudcover, shortwave_radiation
        """
        if hour_data is None or hour_data.empty:
            raise ValueError("hour_data is empty.")

        df = hour_data.copy().reset_index(drop=True)
        # 确保时间列
        if self.dt_col not in df.columns:
            raise KeyError(f"Missing datetime column '{self.dt_col}' in hour_data.")

        df[self.dt_col] = pd.to_datetime(df[self.dt_col], errors="coerce")
        available_cols = set(df.columns)
        radiation_col = self._resolve_col("shortwave_radiation", available_cols)
        radiation = pd.to_numeric(df[radiation_col], errors="coerce")
        if radiation.max(skipna=True) > 20.0:
            raise ValueError("shortwave_radiation must be MJ/m2/hour, not W/m2")
        # 三类打分
        f = self._score_block(df, self.parameters["fungicide"], available_cols).round(3)
        h = self._score_block(df, self.parameters["herbicide"], available_cols).round(3)
        i = self._score_block(df, self.parameters["insecticide"], available_cols).round(3)
        n = self._score_block(df, self.parameters["foliar_nutrition"], available_cols).round(3)
        safety_flags, severe_safety_flags = self._spray_safety_flags(df, available_cols)
        f = self._apply_safety_caps(f, safety_flags, severe_safety_flags)
        h = self._apply_safety_caps(h, safety_flags, severe_safety_flags)
        i = self._apply_safety_caps(i, safety_flags, severe_safety_flags)
        n = self._apply_safety_caps(n, safety_flags, severe_safety_flags)
        total = ((f + h + i + n) / 4.0).round(3)

        out = pd.DataFrame({
            "DateTime": df[self.dt_col],
            "fungicide_score": f,
            "herbicide_score": h,
            "insecticide_score": i,
            "foliar_nutrition_score": n,
            "fungicide": f.map(self.score_to_label),
            "herbicide": h.map(self.score_to_label),
            "insecticide": i.map(self.score_to_label),
            "foliar_nutrition": n.map(self.score_to_label),
            "total_score": total,
            "overall_score": total,
            "overall": total.map(self.score_to_label),
            "safety_flags": safety_flags,
            "severe_safety_flags": severe_safety_flags,
        })
        out["DateTime"] = out["DateTime"].dt.strftime("%Y-%m-%d %H:%M:%S")
        return out
