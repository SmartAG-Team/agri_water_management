import math
import numpy as np
from typing import List, Dict, Optional, Any

try:
    import pandas as pd  # 类型判断用，可选
except Exception:
    pd = None
from copy import deepcopy
DRONE_SPRAY_CFG = {
    "SPRAY_WEATHER_LABELS": [("Favorable", 0.65), ("Caution", 0.42), ("Unfavorable", 0.0)],
    "COMBINED_STRATEGY": "mean",  # or "min" if you want to be conservative
    "SAFETY_LIMITS": {
        "temperature_min": 8.0,
        "temperature_max": 32.0,
        "relative_humidity_min": 45.0,
        "vpd_max": 2.2,
        "wind_speed_max": 3.0,
        "precipitation_max": 0.05,
        "shortwave_radiation_max": 2.4,
        "severe_temperature_min": 5.0,
        "severe_temperature_max": 35.0,
        "severe_relative_humidity_min": 30.0,
        "severe_vpd_max": 3.0,
        "severe_wind_speed_max": 4.0,
        "severe_precipitation_max": 0.2,
        "severe_shortwave_radiation_max": 3.2,
    },
    "PARAMETERS": {
        "fungicide": [
            {"key": "temperature_2m",      "x0": 20,   "k": 0.40},              # slightly cooler preferred
            {"key": "relative_humidity_2m", "x0": 72,   "k": 0.08},              # higher RH helps low-volume/fine droplets
            {"key": "wind_speed_10m",       "x0": -2.2, "k": 2.0, "negate": True},# drone drift risk rises above ~2.2 m/s
            {"key": "precipitation",       "x0": -0.12, "k": 18.0, "negate": True},
            {"key": "shortwave_radiation", "x0": -1.44, "k": 2.78, "negate": True},# hourly MJ/m2; prefer low radiation (dawn/dusk)
        ],
        "herbicide": [
            {"key": "temperature_2m",      "x0": 21,   "k": 0.40},
            {"key": "relative_humidity_2m", "x0": 68,   "k": 0.08},
            {"key": "wind_speed_10m",       "x0": -2.2, "k": 2.0, "negate": True},
            {"key": "precipitation",       "x0": -0.12, "k": 18.0, "negate": True},
            {"key": "shortwave_radiation", "x0": -1.26, "k": 2.78, "negate": True},# hourly MJ/m2; stricter on sun for crop safety
        ],
        "insecticide": [
            {"key": "temperature_2m",      "x0": 23,   "k": 0.30},              # pests active; still favor cooler evening
            {"key": "relative_humidity_2m", "x0": 62,   "k": 0.07},
            {"key": "wind_speed_10m",       "x0": -2.2, "k": 1.8, "negate": True},
            {"key": "precipitation",       "x0": -0.12, "k": 18.0, "negate": True},
            {"key": "shortwave_radiation", "x0": -1.80, "k": 2.22,"negate": True},# hourly MJ/m2; stronger penalty for high UV
        ],
    }
}
GROUND_SPRAY_CFG = {
    "SPRAY_WEATHER_LABELS": [("Favorable", 0.65), ("Caution", 0.40), ("Unfavorable", 0.25)],
    "COMBINED_STRATEGY": "mean",  # or keep "mean" + add vetoes (see note)
    "SAFETY_LIMITS": {
        "temperature_min": 8.0,
        "temperature_max": 33.0,
        "relative_humidity_min": 40.0,
        "vpd_max": 2.5,
        "wind_speed_max": 4.0,
        "precipitation_max": 0.05,
        "shortwave_radiation_max": 3.0,
        "severe_temperature_min": 5.0,
        "severe_temperature_max": 36.0,
        "severe_relative_humidity_min": 28.0,
        "severe_vpd_max": 3.3,
        "severe_wind_speed_max": 5.0,
        "severe_precipitation_max": 0.2,
        "severe_shortwave_radiation_max": 3.6,
    },
    "PARAMETERS": {
        "fungicide": [
            {"key": "temperature_2m",      "x0": 22,   "k": 0.40},
            {"key": "relative_humidity_2m", "x0": 70,   "k": 0.10},              # moderate RH okay
            {"key": "wind_speed_10m",       "x0": -3.0, "k": 2.0, "negate": True},# tolerates ~3 m/s
            {"key": "precipitation",       "x0": -0.18, "k": 15.0, "negate": True},
            {"key": "shortwave_radiation", "x0": -1.80, "k": 2.78, "negate": True},# hourly MJ/m2; more tolerant to sun
        ],
        "herbicide": [
            {"key": "temperature_2m",      "x0": 22,   "k": 0.40},
            {"key": "relative_humidity_2m", "x0": 68,   "k": 0.10},              # keep no negate
            {"key": "wind_speed_10m",       "x0": -2.5, "k": 2.5, "negate": True},# stricter than fungicide (drift risk)
            {"key": "precipitation",       "x0": -0.18, "k": 15.0, "negate": True},
            {"key": "shortwave_radiation", "x0": -1.62, "k": 2.78, "negate": True},
        ],
        "insecticide": [
            {"key": "temperature_2m",      "x0": 24,   "k": 0.30},
            {"key": "relative_humidity_2m", "x0": 65,   "k": 0.08},
            {"key": "wind_speed_10m",       "x0": -3.0, "k": 2.0, "negate": True},
            {"key": "precipitation",       "x0": -0.18, "k": 15.0, "negate": True},
            {"key": "shortwave_radiation", "x0": -2.52, "k": 2.22,"negate": True},# hourly MJ/m2; most tolerant to sun of the three
        ],
    }
}

class BaseSprayWeather:
    def __init__(self, config: Optional[Dict[str, Any]] = None, *, mode: str = "drone"):
        """
        mode: 'drone' (default) or 'ground'. You can also pass PROFILE in config.
        Precedence (lowest -> highest):
            PROFILE (drone/ground)  -->  user `config`
        """
        cfg_in = config or {}
        profile_name = (cfg_in.get("PROFILE") or mode or "drone").lower()
        if profile_name not in ("drone", "ground"):
            raise ValueError("spray weather mode/profile must be 'drone' or 'ground'")
        # select profile
        base_cfg = DRONE_SPRAY_CFG if profile_name == "drone" else GROUND_SPRAY_CFG


        # set attributes
        self.profile: str = profile_name
        self.labels: List = deepcopy(cfg_in.get("SPRAY_WEATHER_LABELS") or base_cfg["SPRAY_WEATHER_LABELS"])
        self.strategy: str = cfg_in.get("COMBINED_STRATEGY") or base_cfg["COMBINED_STRATEGY"]
        self.parameters_cfg: Dict[str, List[Dict[str, Any]]] = deepcopy(base_cfg["PARAMETERS"])
        self.parameters_cfg.update(deepcopy(cfg_in.get("PARAMETERS", {})))
        self.safety_limits: Dict[str, float] = deepcopy(base_cfg.get("SAFETY_LIMITS", {}))
        self.safety_limits.update(deepcopy(cfg_in.get("SAFETY_LIMITS", {})))
    def _ensure_scalar(self,val, key: str):
        """将 Series/ndarray/list 中长度为1的值取出成标量；长度>1时报错提示。"""
        # pandas Series
        if pd is not None and isinstance(val, pd.Series):
            if val.size == 1:
                return float(val.iloc[0])
            raise TypeError(f"key '{key}' expects a scalar per hour, got Series of length {val.size}. "
                            f"请按行调用 classify_hour(row.to_dict())。")
        # numpy array / list / tuple
        if isinstance(val, (np.ndarray, list, tuple)):
            arr = np.asarray(val)
            if arr.size == 1:
                return float(arr.ravel()[0])
            raise TypeError(f"key '{key}' expects a scalar per hour, got array of shape {arr.shape}. "
                            f"请按行调用 classify_hour(row.to_dict())。")
        # 其余类型：尝试转为 float
        return float(val)

    def sigmoid_score(self, x: float, x0: float, k: float) -> float:
        # 用 numpy 实现，兼容 float；上游已确保是标量
        return float(1.0 / (1.0 + np.exp(-k * (x - x0))))

    def get_parameters(self, pesticide_type: str) -> List[Dict]:
        return self.parameters_cfg.get(pesticide_type, [])

    def evaluate_score(self, hour_data: dict, pesticide_type: str) -> float:
        params = self.get_parameters(pesticide_type)
        if not params:
            return 0.0

        scores = []
        for p in params:
            if p["key"] not in hour_data or hour_data[p["key"]] is None:
                raise ValueError(f"hourly weather missing required field: {p['key']}")
            raw = hour_data[p["key"]]
            val = self._ensure_scalar(raw, p["key"])
            if p.get("negate", False):
                val = -val
            scores.append(self.sigmoid_score(val, p["x0"], p["k"]))

        if self.strategy == "min":
            agg = min(scores)
        elif self.strategy == "max":
            agg = max(scores)
        else:
            agg = sum(scores) / len(scores)

        return round(float(agg), 3)

    def score_to_label(self, score: float) -> str:
        for label, th in self.labels:
            if score >= th:
                return label
        return self.labels[-1][0]

    def classify_hour(self, hour_data: dict) -> Dict:
        f_score = self.evaluate_score(hour_data, 'fungicide')
        h_score = self.evaluate_score(hour_data, 'herbicide')
        i_score = self.evaluate_score(hour_data, 'insecticide')
        total_score = round((f_score + h_score + i_score) / 3, 3)
        return {
            "fungicide_score": f_score, "herbicide_score": h_score, "insecticide_score": i_score,
            "fungicide": self.score_to_label(f_score), "herbicide": self.score_to_label(h_score),
            "insecticide": self.score_to_label(i_score), "total_score": total_score,
            "overall": self.score_to_label(total_score)
        }

    # 可选：如果你确实想直接喂 DataFrame，这里提供一个安全入口
    def classify_day_df(self, df):  # df: pd.DataFrame，含所需列与 'datetime'
        out = []
        for _, row in df.iterrows():
            rec = self.classify_hour(row.to_dict())
            rec["datetime"] = row.get("datetime")
            out.append(rec)
        return out
