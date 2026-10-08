from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from math import isfinite
from typing import Any, Dict, Mapping, Optional, List, Tuple

import numpy as np
import pandas as pd
from collections import OrderedDict
from core.phenology import BasePhenology
from .config import (
    IOWA_Stage_INDEX,
    IOWA_Stage_ORDER,
    MAIZE_PHENOLOGY_PARAMETER_VERSION,
    SPRING_MAIZE_MIDDLE_SEASON_FRACTION,
    SPRING_MAIZE_PLANTING_CUTOFF,
    phen_config as CONFIG,
)


_CULTIVAR_PHENOLOGY_KEYS = {
    "gdd_to_r6",
    "gdd_to_emergence",
    "gdd_ve_to_r1",
    "gdd_r1_to_r6",
    "stage_gdd",
}
_PHASE_GDD_KEYS = ("gdd_to_emergence", "gdd_ve_to_r1", "gdd_r1_to_r6")
_MAIZE_PHENOLOGY_PROFILES = {"spring_maize", "summer_maize"}


@dataclass(frozen=True)
class MaizePhenologyResolution:
    thresholds: dict[str, float]
    profile: str
    parameter_source: str
    r6_gdd: float
    parameter_version: str = MAIZE_PHENOLOGY_PARAMETER_VERSION


def _phenology_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return dict(value.model_dump(exclude_none=True))
    if isinstance(value, Mapping):
        return dict(value)
    raise ValueError("cultivar phenology must be an object")


def has_maize_cultivar_phenology(value: Mapping[str, Any] | Any | None) -> bool:
    phenology = _phenology_mapping(value)
    raw_stage_gdd = phenology.get("stage_gdd")
    if raw_stage_gdd is not None and (
        not isinstance(raw_stage_gdd, Mapping) or bool(raw_stage_gdd)
    ):
        return True
    return any(phenology.get(key) is not None for key in (*_PHASE_GDD_KEYS, "gdd_to_r6"))


def _finite_gdd(value: Any, *, field: str, allow_zero: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite number") from exc
    if not isfinite(number):
        raise ValueError(f"{field} must be a finite number")
    if number < 0.0 or (not allow_zero and number <= 0.0):
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{field} must be {qualifier}")
    return number


def _validate_stage_thresholds(thresholds: Mapping[str, float]) -> None:
    if list(thresholds) != IOWA_Stage_ORDER:
        raise ValueError("maize stage thresholds must contain every Iowa stage in canonical order")
    values = [float(thresholds[stage]) for stage in IOWA_Stage_ORDER]
    if values[0] != 0.0:
        raise ValueError("maize stage threshold VS must equal 0 GDD")
    for current_stage, following_stage, current, following in zip(
        IOWA_Stage_ORDER,
        IOWA_Stage_ORDER[1:],
        values,
        values[1:],
    ):
        if not isfinite(current) or not isfinite(following) or current >= following:
            raise ValueError(
                "maize stage thresholds must be strictly increasing; "
                f"expected {current_stage} < {following_stage}"
            )


def _build_from_stage_anchors(
    template: Mapping[str, float],
    raw_stage_gdd: Any,
) -> dict[str, float]:
    if not isinstance(raw_stage_gdd, Mapping):
        raise ValueError("cultivar phenology.stage_gdd must be an object")

    supplied: dict[str, float] = {}
    for raw_stage, raw_gdd in raw_stage_gdd.items():
        stage = str(raw_stage).strip().upper()
        if stage not in IOWA_Stage_INDEX:
            supported = ", ".join(IOWA_Stage_ORDER)
            raise ValueError(f"unknown maize Iowa stage {raw_stage!r}; expected one of: {supported}")
        if stage in supplied:
            raise ValueError(f"duplicate maize Iowa stage anchor {stage!r}")
        supplied[stage] = _finite_gdd(
            raw_gdd,
            field=f"cultivar phenology.stage_gdd.{stage}",
            allow_zero=stage == "VS",
        )

    anchors = {
        "VS": float(template["VS"]),
        "VE": float(template["VE"]),
        "R6": float(template["R6"]),
        **supplied,
    }
    if float(anchors["VS"]) != 0.0:
        raise ValueError("cultivar phenology.stage_gdd.VS must equal 0 GDD")

    ordered_anchors = [stage for stage in IOWA_Stage_ORDER if stage in anchors]
    for current_stage, following_stage in zip(ordered_anchors, ordered_anchors[1:]):
        if anchors[current_stage] >= anchors[following_stage]:
            raise ValueError(
                "cultivar phenology stage anchors must be strictly increasing; "
                f"expected {current_stage} < {following_stage}"
            )

    thresholds: dict[str, float] = {}
    for left_stage, right_stage in zip(ordered_anchors, ordered_anchors[1:]):
        left_index = IOWA_Stage_INDEX[left_stage]
        right_index = IOWA_Stage_INDEX[right_stage]
        template_left = float(template[left_stage])
        template_span = float(template[right_stage]) - template_left
        anchor_left = float(anchors[left_stage])
        anchor_span = float(anchors[right_stage]) - anchor_left
        for index in range(left_index, right_index):
            stage = IOWA_Stage_ORDER[index]
            fraction = (float(template[stage]) - template_left) / template_span
            thresholds[stage] = anchor_left + fraction * anchor_span
    thresholds[ordered_anchors[-1]] = float(anchors[ordered_anchors[-1]])
    return thresholds


def _build_from_phase_gdd(
    template: Mapping[str, float],
    phenology: Mapping[str, Any],
) -> dict[str, float]:
    emergence = (
        _finite_gdd(phenology["gdd_to_emergence"], field="cultivar phenology.gdd_to_emergence")
        if phenology.get("gdd_to_emergence") is not None
        else float(template["VE"])
    )
    ve_to_r1 = (
        _finite_gdd(phenology["gdd_ve_to_r1"], field="cultivar phenology.gdd_ve_to_r1")
        if phenology.get("gdd_ve_to_r1") is not None
        else float(template["R1"]) - float(template["VE"])
    )
    r1_to_r6 = (
        _finite_gdd(phenology["gdd_r1_to_r6"], field="cultivar phenology.gdd_r1_to_r6")
        if phenology.get("gdd_r1_to_r6") is not None
        else float(template["R6"]) - float(template["R1"])
    )
    r1 = emergence + ve_to_r1
    r6 = r1 + r1_to_r6

    thresholds: dict[str, float] = {"VS": 0.0}
    for stage in IOWA_Stage_ORDER[1 : IOWA_Stage_INDEX["R1"] + 1]:
        fraction = (
            (float(template[stage]) - float(template["VE"]))
            / (float(template["R1"]) - float(template["VE"]))
        )
        thresholds[stage] = emergence + fraction * ve_to_r1
    for stage in IOWA_Stage_ORDER[IOWA_Stage_INDEX["R1"] + 1 :]:
        fraction = (
            (float(template[stage]) - float(template["R1"]))
            / (float(template["R6"]) - float(template["R1"]))
        )
        thresholds[stage] = r1 + fraction * r1_to_r6
    thresholds["VE"] = emergence
    thresholds["R1"] = r1
    thresholds["R6"] = r6
    return thresholds


def _build_from_total_gdd(
    template: Mapping[str, float],
    raw_gdd_to_r6: Any,
) -> dict[str, float]:
    target_r6 = _finite_gdd(raw_gdd_to_r6, field="cultivar phenology.gdd_to_r6")
    emergence = float(template["VE"])
    if target_r6 <= emergence:
        raise ValueError(
            "cultivar phenology.gdd_to_r6 must be greater than the template VE threshold "
            f"({emergence:g} GDD)"
        )
    scale = (target_r6 - emergence) / (float(template["R6"]) - emergence)
    thresholds: dict[str, float] = {
        "VS": template["VS"],
        "VE": template["VE"],
    }
    for stage in IOWA_Stage_ORDER[2:]:
        thresholds[stage] = emergence + (float(template[stage]) - emergence) * scale
    thresholds["R6"] = target_r6
    return thresholds


def _maturity_template(maturation_group: str) -> tuple[str, dict[str, float]]:
    group = str(maturation_group or "middle").strip().lower()
    templates = CONFIG["MAIZE_STAGE_THRESHOLDS"]
    if group not in templates:
        supported = ", ".join(sorted(templates))
        raise ValueError(f"unsupported maize maturation_group {maturation_group!r}; expected one of: {supported}")
    return group, dict(templates[group])


def _build_from_cultivar_phenology(
    template: Mapping[str, float],
    cultivar_phenology: Mapping[str, Any] | Any | None,
) -> tuple[dict[str, float] | None, str | None]:
    phenology = _phenology_mapping(cultivar_phenology)
    unknown = sorted(set(phenology) - _CULTIVAR_PHENOLOGY_KEYS)
    if unknown:
        raise ValueError(f"unsupported cultivar phenology parameter(s): {', '.join(unknown)}")

    raw_stage_gdd = phenology.get("stage_gdd")
    if raw_stage_gdd is not None and not isinstance(raw_stage_gdd, Mapping):
        raise ValueError("cultivar phenology.stage_gdd must be an object")
    if raw_stage_gdd:
        thresholds = _build_from_stage_anchors(template, raw_stage_gdd)
        source = "cultivar_stage_gdd"
    elif any(phenology.get(key) is not None for key in _PHASE_GDD_KEYS):
        thresholds = _build_from_phase_gdd(template, phenology)
        source = "cultivar_phase_gdd"
    elif phenology.get("gdd_to_r6") is not None:
        thresholds = _build_from_total_gdd(template, phenology["gdd_to_r6"])
        source = "cultivar_gdd_to_r6"
    else:
        return None, None

    ordered = {stage: thresholds[stage] for stage in IOWA_Stage_ORDER}
    _validate_stage_thresholds(ordered)
    return ordered, source


def _planting_day(value: str | date | datetime | None) -> date | None:
    if value is None:
        return None
    try:
        return pd.to_datetime(value).date()
    except Exception as exc:
        raise ValueError(f"invalid maize planting_date {value!r}") from exc


def resolve_maize_phenology_profile(
    planting_date: str | date | datetime | None,
    phenology_profile: str | None = None,
) -> tuple[str, str]:
    if phenology_profile is not None:
        profile = str(phenology_profile).strip().lower()
        if profile not in _MAIZE_PHENOLOGY_PROFILES:
            supported = ", ".join(sorted(_MAIZE_PHENOLOGY_PROFILES))
            raise ValueError(
                f"unsupported maize phenology_profile {phenology_profile!r}; "
                f"expected one of: {supported}"
            )
        return profile, "explicit_profile"

    day = _planting_day(planting_date)
    if day is None:
        return "summer_maize", "legacy_default"
    if (day.month, day.day) < SPRING_MAIZE_PLANTING_CUTOFF:
        return "spring_maize", "planting_date"
    return "summer_maize", "planting_date"


def resolve_maize_stage_thresholds(
    maturation_group: str,
    cultivar_phenology: Mapping[str, Any] | Any | None = None,
    *,
    planting_date: str | date | datetime | None = None,
    phenology_profile: str | None = None,
    season_gdd: float | None = None,
) -> MaizePhenologyResolution:
    _, template = _maturity_template(maturation_group)
    profile, profile_source = resolve_maize_phenology_profile(
        planting_date,
        phenology_profile,
    )
    cultivar_thresholds, cultivar_source = _build_from_cultivar_phenology(
        template,
        cultivar_phenology,
    )

    if cultivar_thresholds is not None:
        thresholds = cultivar_thresholds
        source = str(cultivar_source)
    elif profile == "summer_maize":
        thresholds = template
        source = "summer_table" if profile_source != "legacy_default" else "legacy_default"
    else:
        if season_gdd is None:
            raise ValueError("season GDD is required for unparameterized spring maize")
        available_gdd = _finite_gdd(season_gdd, field="spring maize season GDD")
        middle_r6 = float(CONFIG["MAIZE_STAGE_THRESHOLDS"]["middle"]["R6"])
        maturity_ratio = float(template["R6"]) / middle_r6
        r6_fraction = min(
            SPRING_MAIZE_MIDDLE_SEASON_FRACTION,
            SPRING_MAIZE_MIDDLE_SEASON_FRACTION * maturity_ratio,
        )
        thresholds = _build_from_total_gdd(template, available_gdd * r6_fraction)
        source = "seasonal_gdd"

    ordered = {stage: float(thresholds[stage]) for stage in IOWA_Stage_ORDER}
    _validate_stage_thresholds(ordered)
    return MaizePhenologyResolution(
        thresholds=ordered,
        profile=profile,
        parameter_source=source,
        r6_gdd=float(ordered["R6"]),
    )


def build_maize_stage_thresholds(
    maturation_group: str,
    cultivar_phenology: Mapping[str, Any] | Any | None = None,
    *,
    planting_date: str | date | datetime | None = None,
    phenology_profile: str | None = None,
    season_gdd: float | None = None,
) -> dict[str, float]:
    """Resolve effective Iowa-stage GDD thresholds for one maize cultivar."""
    return resolve_maize_stage_thresholds(
        maturation_group,
        cultivar_phenology,
        planting_date=planting_date,
        phenology_profile=phenology_profile,
        season_gdd=season_gdd,
    ).thresholds


def compute_maize_season_gdd(
    weather: pd.DataFrame,
    planting_date: str | date | datetime,
    season_end: str | date | datetime,
    *,
    date_col: str = "DateTime",
    tmean_col: str = "temperature_2m_mean",
    tmin_col: str = "temperature_2m_min",
    tmax_col: str = "temperature_2m_max",
    base_temp: float | None = None,
    opt_temp: float | None = None,
    max_temp: float | None = None,
) -> float:
    start = _planting_day(planting_date)
    end = _planting_day(season_end)
    if start is None or end is None or end <= start:
        raise ValueError("spring maize season_end must be after planting_date")
    if weather is None or weather.empty or date_col not in weather:
        raise ValueError("spring maize seasonal calibration requires continuous daily weather")

    frame = weather.copy()
    frame[date_col] = pd.to_datetime(frame[date_col], errors="coerce").dt.date
    frame = frame.loc[(frame[date_col] >= start) & (frame[date_col] < end)].copy()
    expected_dates = list(pd.date_range(start=start, end=end, inclusive="left").date)
    actual_dates = frame[date_col].tolist()
    if actual_dates != expected_dates or len(set(actual_dates)) != len(actual_dates):
        raise ValueError(
            "spring maize seasonal calibration requires continuous daily weather "
            "for [planting_date, season_end)"
        )

    if tmean_col in frame:
        temperatures = frame[tmean_col].to_numpy(dtype="float64")
    elif {tmin_col, tmax_col}.issubset(frame.columns):
        temperatures = (
            frame[tmin_col].to_numpy(dtype="float64")
            + frame[tmax_col].to_numpy(dtype="float64")
        ) / 2.0
    else:
        raise ValueError(f"Need '{tmean_col}' or both '{tmin_col}' & '{tmax_col}'.")
    return float(
        np.sum(
            _compute_maize_gdd(
                temperatures,
                base_temp=base_temp,
                opt_temp=opt_temp,
                max_temp=max_temp,
            )
        )
    )


def _compute_maize_gdd(
    tmean: np.ndarray | float,
    *,
    base_temp: float | None = None,
    opt_temp: float | None = None,
    max_temp: float | None = None,
) -> np.ndarray | float:
    cards = CONFIG["MAIZE_CARDINAL_TEMPERATURES"]
    base_temp = float(cards["base_temperature"] if base_temp is None else base_temp)
    opt_temp = float(cards["optimal_temperature"] if opt_temp is None else opt_temp)
    max_temp = float(cards["maximum_temperature"] if max_temp is None else max_temp)
    t = np.asarray(tmean, dtype="float64")
    t_clip = np.clip(t, base_temp, max_temp)
    below_opt = t_clip <= opt_temp
    gdd = np.where(
        below_opt,
        t_clip - base_temp,
        (max_temp - t_clip) * (opt_temp - base_temp) / (max_temp - opt_temp),
    )
    gdd[(t < base_temp) | (t > max_temp)] = 0.0
    return gdd.item() if np.isscalar(tmean) else gdd

# ---------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------
def _cummax_inplace(a: np.ndarray) -> None:
    mx = -np.inf
    for i in range(a.shape[0]):
        if a[i] < mx:
            a[i] = mx
        else:
            mx = a[i]

def _threshold_for_label(stage_thresholds: Dict[str, float], label: str) -> Optional[float]:
    return {k.lower(): float(v) for k, v in stage_thresholds.items()}.get(str(label).strip().lower())

def _unique_sorted_pairs_idx(pairs: List[Tuple[int, float]]) -> List[Tuple[int, float]]:
    # de-dup by index, keep last
    d = {}
    for i, v in pairs:
        d[int(i)] = float(v)
    return sorted(d.items(), key=lambda kv: kv[0])

def _map_accum_through_anchors(
    raw_acc: np.ndarray,       # Accumulated_GDD over all days
    anch_idx: np.ndarray,      # indices of anchors (ascending)
    anch_thr: np.ndarray       # target thresholds at anchors (same length)
) -> np.ndarray:
    """
    Build adj = f(raw_acc) using piecewise-linear mapping in GDD space.
    Ensures natural growth after the last observation (right extrapolation uses
    the last segment slope in GDD space), and preserves daily variability.
    """
    # x = accumulated GDD at anchors; y = target thresholds
    x = raw_acc[anch_idx].astype(float)
    y = anch_thr.astype(float)

    # De-dup by x (keep last), enforce strictly increasing x for interpolation
    # (handles zero-GDD or identical acc on different days)
    x_to_y = {}
    for xv, yv in zip(x, y):
        x_to_y[float(xv)] = float(yv)
    xy = sorted(x_to_y.items(), key=lambda kv: kv[0])
    x = np.array([kv[0] for kv in xy], dtype=float)
    y = np.array([kv[1] for kv in xy], dtype=float)

    # If only one anchor remains, scale proportionally to raw_acc
    if len(x) == 1:
        ratio = y[0] / max(x[0], 1e-9)
        adj = raw_acc * ratio
        return adj

    # Interior: linear interpolation on x
    adj = np.interp(raw_acc, x, y)

    # Left extrapolation: slope of first segment
    x0, x1 = x[0], x[1]
    y0, y1 = y[0], y[1]
    slope_left = (y1 - y0) / max(x1 - x0, 1e-9)
    left_mask = raw_acc < x0
    adj[left_mask] = y0 + slope_left * (raw_acc[left_mask] - x0)

    # Right extrapolation: slope of last segment
    xL_1, xL = x[-2], x[-1]
    yL_1, yL = y[-2], y[-1]
    slope_right = (yL - yL_1) / max(xL - xL_1, 1e-9)
    right_mask = raw_acc > xL
    adj[right_mask] = yL + slope_right * (raw_acc[right_mask] - xL)

    return adj

class MaizePhenology(BasePhenology):
    def __init__(self, params: Optional[Dict] = None) -> None:
        defaults = {
            "base_temperature": CONFIG["MAIZE_CARDINAL_TEMPERATURES"]["base_temperature"],
            "variety_maturation_group": "middle",
        }
        if params:
            defaults.update(params)
        super().__init__(defaults)
        self.phenology_params = defaults
        cards = CONFIG["MAIZE_CARDINAL_TEMPERATURES"]
        self.opt_temp = float(cards["optimal_temperature"])
        self.max_temp = float(cards["maximum_temperature"])
        self.cultivar_phenology = {
            key: defaults[key]
            for key in _CULTIVAR_PHENOLOGY_KEYS
            if key in defaults and defaults[key] is not None
        }
        self.stage_thresholds: Dict[str, float] = build_maize_stage_thresholds(
            defaults["variety_maturation_group"],
            self.cultivar_phenology,
        )
        self.phenology_resolution = resolve_maize_stage_thresholds(
            defaults["variety_maturation_group"],
            self.cultivar_phenology,
        )

    def compute_gdd(self, tmean: np.ndarray | float) -> np.ndarray | float:
        return _compute_maize_gdd(
            tmean,
            base_temp=self.base_temp,
            opt_temp=self.opt_temp,
            max_temp=self.max_temp,
        )

    def compute_stages(
        self,
        planting_date: str | datetime,
        weather: pd.DataFrame,
        *,
        date_col: str = "DateTime",
        tmean_col: str = "temperature_2m_mean",
        tmin_col: str = "temperature_2m_min",
        tmax_col: str = "temperature_2m_max",
        dfob: Optional[pd.DataFrame] = None,
        ob_date_col: str = "DateTime",
        ob_stage_col: str = "ObservedStage",
        return_calibration: bool = True,
        snap_to_nearest: bool = True,
        include_diagnostics: bool = False,
    ) -> pd.DataFrame:
        """
        Compute maize phenology timeline.

        - If observations (dfob) are provided: piecewise-linear mapping in GDD space
        to align model to observed stage thresholds (with epsilon snap + monotone).
        - If no observations: identity mapping (AdjAccumulated_GDD = Accumulated_GDD),
        so stages follow the raw thermal time.

        Parameters
        ----------
        planting_date : str | datetime
        weather : DataFrame
            Must contain `date_col` and either:
            - `tmean_col`, or
            - both `tmin_col` and `tmax_col`.
        dfob : Optional[DataFrame]
            Optional observations with date and observed stage label.

        Returns
        -------
        DataFrame with columns:
            Date | GDD | Accumulated_GDD | AdjAccumulated_GDD | Stage
        """
        if weather is None or weather.empty:
            raise ValueError("weather dataframe is empty")

        profile, _ = resolve_maize_phenology_profile(
            planting_date,
            self.phenology_params.get("phenology_profile"),
        )
        season_gdd = None
        if profile == "spring_maize" and not has_maize_cultivar_phenology(
            self.cultivar_phenology
        ):
            season_end = self.phenology_params.get("season_end")
            if season_end is None:
                if date_col not in weather:
                    raise ValueError(
                        "season_end or dated weather data is required for "
                        "unparameterized spring maize"
                    )
                weather_dates = pd.to_datetime(weather[date_col], errors="coerce")
                if weather_dates.notna().sum() == 0:
                    raise ValueError(
                        "season_end or dated weather data is required for "
                        "unparameterized spring maize"
                    )
                season_end = weather_dates.max().date()
            season_gdd = compute_maize_season_gdd(
                weather,
                planting_date,
                season_end,
                base_temp=self.base_temp,
                opt_temp=self.opt_temp,
                max_temp=self.max_temp,
            )
        self.phenology_resolution = resolve_maize_stage_thresholds(
            self.phenology_params["variety_maturation_group"],
            self.cultivar_phenology,
            planting_date=planting_date,
            phenology_profile=self.phenology_params.get("phenology_profile"),
            season_gdd=season_gdd,
        )
        self.stage_thresholds = self.phenology_resolution.thresholds

        # 1) Prep & slice since planting date
        df = weather.copy()
        df[date_col] = pd.to_datetime(df[date_col])
        plant_dt = pd.to_datetime(planting_date)
        df = df.loc[df[date_col] >= plant_dt].sort_values(date_col).reset_index(drop=True)
        if df.empty:
            raise ValueError("No weather rows on/after planting_date.")

        # 2) Daily GDD and cumulative GDD
        if tmean_col in df:
            df["GDD"] = self.compute_gdd(df[tmean_col].to_numpy())
        elif {tmin_col, tmax_col}.issubset(df.columns):
            # Use average of min/max as tmean and pass to compute_gdd (which clips to [base, max])
            tmean_arr = (df[tmin_col].to_numpy(dtype="float64") + df[tmax_col].to_numpy(dtype="float64")) / 2.0
            df["GDD"] = self.compute_gdd(tmean_arr)
        else:
            raise ValueError(f"Need '{tmean_col}' or both '{tmin_col}' & '{tmax_col}'.")

        df["Accumulated_GDD"] = df["GDD"].cumsum()
        raw_acc = df["Accumulated_GDD"].to_numpy(dtype=float)

        # 3) Build anchors from observations (index -> target threshold)
        anchors: List[Tuple[int, float]] = []
        used_obs: List[dict] = []
        has_obs = (dfob is not None) and (not dfob.empty)

        if has_obs:
            ob = dfob.copy()
            ob[ob_date_col] = pd.to_datetime(ob[ob_date_col])
            ob = ob.loc[ob[ob_date_col] >= plant_dt].copy()

            dates_np = df[date_col].to_numpy()
            if not ob.empty:
                dnums = dates_np.astype("datetime64[D]").astype("int64")
                for _, r in ob.iterrows():
                    t = r[ob_date_col]
                    lbl = str(r[ob_stage_col]).strip()
                    thr = _threshold_for_label(self.stage_thresholds, lbl)
                    if thr is None:
                        continue

                    if snap_to_nearest:
                        tnum = t.normalize().to_datetime64().astype("datetime64[D]").astype("int64")
                        idx = int(np.argmin(np.abs(dnums - tnum)))
                    else:
                        m = np.where(df[date_col].dt.normalize() == t.normalize())[0]
                        idx = int(m[0]) if len(m) else None
                    if idx is None:
                        continue

                    anchors.append((idx, float(thr)))
                    used_obs.append({"date": str(t.date()), "index": idx, "stage": lbl, "threshold": float(thr)})

        # 4) If NO observations: identity mapping and return
        if not anchors:
            df["AdjAccumulated_GDD"] = raw_acc
            out = self._assign_stages(df, date_col)
            if return_calibration:
                out.attrs["calibration"] = {
                    "anchors_used": [],
                    "method": "identity (no observations)",
                }
            if include_diagnostics:
                out = self._with_resolution_diagnostics(out)
            return out

        # 5) If there are anchors, ensure day-0 stabilizing anchor exists
        if min(i for i, _ in anchors) != 0:
            vs_thr = float(self.stage_thresholds.get("VS", 0.0))
            anchors.append((0, vs_thr))

        anchors = _unique_sorted_pairs_idx(anchors)
        anch_idx = np.array([i for i, _ in anchors], dtype=int)
        anch_thr = np.array([v for _, v in anchors], dtype=float)

        # 6) Map in GDD space via piecewise-linear function
        adj = _map_accum_through_anchors(raw_acc, anch_idx, anch_thr)

        # Snap exact at anchors (epsilon above) and enforce monotone
        if len(anch_idx):
            eps = 1e-6
            adj[anch_idx] = anch_thr + eps
        _cummax_inplace(adj)

        df["AdjAccumulated_GDD"] = adj

        # 7) Stage binning and finalize
        out = self._assign_stages(df, date_col)

        # Ensure observed indices carry observed labels
        if used_obs:
            for u in used_obs:
                out.loc[u["index"], "Stage"] = u["stage"]

        if return_calibration:
            out.attrs["calibration"] = {
                "anchors_used": [{"day_index": int(i), "AdjGDD": float(v)} for i, v in anchors],
                "method": "piecewise-linear in GDD space + epsilon snap + monotone",
            }
        if include_diagnostics:
            out = self._with_resolution_diagnostics(out)
        return out

    def _with_resolution_diagnostics(self, frame: pd.DataFrame) -> pd.DataFrame:
        frame = frame.copy()
        frame["PhenologyProfile"] = self.phenology_resolution.profile
        frame["PhenologyParameterSource"] = self.phenology_resolution.parameter_source
        frame["PhenologyR6GDD"] = self.phenology_resolution.r6_gdd
        frame["PhenologyParameterVersion"] = self.phenology_resolution.parameter_version
        return frame

    def _assign_stages(self, df: pd.DataFrame, date_col: str) -> pd.DataFrame:
        items = sorted(self.stage_thresholds.items(), key=lambda kv: float(kv[1]))
        dedup = OrderedDict()
        for label, lim in items:
            dedup[float(lim)] = str(label)

        limits = list(dedup.keys())
        labels = list(dedup.values())

        edges: List[float] = []
        if not limits or limits[0] > 0.0:
            edges.append(0.0)
        edges.extend(limits)
        edges.append(np.inf)

        intervals = len(edges) - 1
        if len(labels) < intervals:
            labels = labels + [labels[-1]] * (intervals - len(labels))
        elif len(labels) > intervals:
            labels = labels[:intervals]

        out = df[[date_col, "GDD", "Accumulated_GDD", "AdjAccumulated_GDD"]].copy()
        out = out.rename(columns={date_col: "Date"})
        out["Date"] = pd.to_datetime(out["Date"]).dt.date

        out["Stage"] = pd.cut(
            out["AdjAccumulated_GDD"].to_numpy(dtype=float),
            bins=np.array(edges, dtype=float),
            labels=labels,
            right=True,
            include_lowest=True,
            duplicates="drop",
        )
        if out["Stage"].isna().any():
            out["Stage"] = out["Stage"].ffill().bfill()
        return out
