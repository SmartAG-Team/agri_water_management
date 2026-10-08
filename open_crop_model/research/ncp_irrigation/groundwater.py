"""Source accounting and delayed recharge; no historical causal attribution."""

from math import sqrt
import numpy as np
import pandas as pd
from core.hydrology.types import bounded


def source_ledger(
    *,
    irrigation_field_mm,
    area_ha,
    conveyance_fraction,
    groundwater_fraction,
    withdrawal_total_mm=None,
):
    bounded(irrigation_field_mm, "irrigation_field_mm")
    bounded(area_ha, "area_ha", 1e-9)
    bounded(conveyance_fraction, "conveyance_fraction", 1e-9, 1)
    bounded(groundwater_fraction, "groundwater_fraction", 0, 1)
    field = irrigation_field_mm * area_ha * 10
    withdrawal = (
        irrigation_field_mm / conveyance_fraction
        if withdrawal_total_mm is None
        else bounded(withdrawal_total_mm, "withdrawal_total_mm", irrigation_field_mm)
    )
    total = withdrawal * area_ha * 10
    return dict(
        field_volume_m3=field,
        withdrawal_total_m3=total,
        groundwater_withdrawal_m3=total * groundwater_fraction,
        surface_water_withdrawal_m3=total * (1 - groundwater_fraction),
        conveyance_loss_m3=total - field,
        groundwater_withdrawal_mm=withdrawal * groundwater_fraction,
        withdrawal_total_mm=withdrawal,
        conveyance_basis="explicit_event_ledger"
        if withdrawal_total_mm is not None
        else "zone_fraction_assumption",
    )


def delayed_recharge(drainage_mm, kernel, previous_tail_mm=()):
    """Daily convolution; kernel index 0 is same-day recharge, never assumed.

    The kernel sums to at most one. The remainder is explicitly unrecharged;
    tail fluxes remain pending after the reporting period and may be carried.
    """
    drainage = np.asarray(drainage_mm, dtype=float)
    weights = np.asarray(kernel, dtype=float)
    tail = np.asarray(previous_tail_mm, dtype=float)
    if (
        drainage.ndim != 1
        or len(drainage) == 0
        or weights.ndim != 1
        or len(weights) == 0
    ):
        raise ValueError("Nonempty daily drainage and lag kernel required")
    for value in (drainage, weights, tail):
        if value.ndim != 1 or not np.isfinite(value).all() or (value < 0).any():
            raise ValueError("Recharge inputs must be finite and nonnegative")
    if weights.sum() > 1 + 1e-12:
        raise ValueError("Recharge kernel creates water")
    recharge = np.convolve(drainage, weights)
    full = np.zeros(max(len(recharge), len(tail)))
    full[: len(recharge)] += recharge
    full[: len(tail)] += tail
    n = len(drainage)
    unrecharged = float(drainage.sum() * (1 - weights.sum()))
    delivered = float(full[:n].sum())
    pending = float(full[n:].sum())
    residual = float(drainage.sum() + tail.sum() - unrecharged - delivered - pending)
    return dict(
        recharge_mm=full[:n].tolist(),
        full_recharge_mm=full.tolist(),
        pending_tail_mm=full[n:].tolist(),
        pending_after_period_mm=pending,
        unrecharged_mm=unrecharged,
        balance_residual_mm=residual,
    )


def scenario_storage_effect(
    baseline_pumping_mm,
    scenario_pumping_mm,
    baseline_recharge_mm,
    scenario_recharge_mm,
    baseline_capillary_mm=0.0,
    scenario_capillary_mm=0.0,
):
    for value in (
        baseline_pumping_mm,
        scenario_pumping_mm,
        baseline_recharge_mm,
        scenario_recharge_mm,
        baseline_capillary_mm,
        scenario_capillary_mm,
    ):
        bounded(value, "groundwater flux")
    return (
        baseline_pumping_mm
        - scenario_pumping_mm
        + scenario_recharge_mm
        - baseline_recharge_mm
        + baseline_capillary_mm
        - scenario_capillary_mm
    )


def grace_groundwater_anomaly(
    *,
    tws_mm,
    soil_mm,
    surface_mm,
    snow_mm,
    canopy_mm,
    sigma_components,
    covariance=None,
    soil_store_scope="full_column",
):
    """All anomalies use common dates, area, baseline and mm equivalent water.

    Root-zone water alone cannot replace the complete soil water store.
    Supplied covariance allows correlated component errors; diagonal errors
    otherwise mean an explicit independence assumption.
    """
    if soil_store_scope != "full_column":
        raise ValueError("GRACE subtraction requires full soil-column storage")
    values = np.asarray([tws_mm, soil_mm, surface_mm, snow_mm, canopy_mm], dtype=float)
    sigma = np.asarray(sigma_components, dtype=float)
    if (
        values.shape != (5,)
        or sigma.shape != (5,)
        or not np.isfinite(values).all()
        or not np.isfinite(sigma).all()
        or (sigma < 0).any()
    ):
        raise ValueError("Five finite storage anomalies and uncertainties required")
    matrix = (
        np.diag(sigma**2) if covariance is None else np.asarray(covariance, dtype=float)
    )
    if (
        matrix.shape != (5, 5)
        or not np.isfinite(matrix).all()
        or not np.allclose(matrix, matrix.T)
        or np.linalg.eigvalsh(matrix).min() < -1e-9
    ):
        raise ValueError("Storage covariance must be symmetric positive semidefinite")
    signs = np.array([1.0, -1.0, -1.0, -1.0, -1.0])
    variance = float(signs @ matrix @ signs)
    return dict(
        groundwater_mm=float(signs @ values),
        sigma_mm=sqrt(max(0.0, variance)),
        error_assumption="independent components"
        if covariance is None
        else "supplied joint covariance",
        interpretation="regional storage anomaly; no pixel technology attribution",
    )


def well_panel_trends(frame, min_coverage=0.8, min_months=24):
    """Per-well trend and explicit aquifer class, without changing-panel pooling."""
    bounded(min_coverage, "min_coverage", 0, 1)
    required = {"well_id", "date", "groundwater_depth_m", "aquifer_class"}
    if not required <= set(frame):
        raise ValueError("Canonical well columns missing")
    data = frame.copy()
    data["date"] = pd.to_datetime(data.date, errors="coerce")
    data["groundwater_depth_m"] = pd.to_numeric(
        data.groundwater_depth_m, errors="coerce"
    )
    for flag in (
        "missing_or_nonnumeric_value",
        "possible_sentinel_value",
        "conflicting_values_same_well_date",
        "invalid_date",
        "station_code_mismatch",
    ):
        if flag in data:
            data = data[~data[flag].astype(str).str.lower().eq("true")]
    data = data[
        data.aquifer_class.isin(["unconfined", "confined"])
        & data.date.notna()
        & np.isfinite(data.groundwater_depth_m)
    ]
    columns = [
        "well_id",
        "aquifer_class",
        "n_months",
        "coverage",
        "depth_slope_m_year",
        "level_slope_m_year",
        "first_month",
        "last_month",
        "interpretation",
    ]
    if data.empty:
        return pd.DataFrame(columns=columns)
    data["month"] = data.date.dt.to_period("M")
    expected = len(pd.period_range(data.month.min(), data.month.max(), freq="M"))
    rows = []
    for well, group in data.groupby("well_id", sort=True):
        if group.aquifer_class.nunique() != 1:
            continue
        counts = group.groupby("month").groundwater_depth_m.nunique()
        if counts.gt(1).any():
            continue
        group = group.drop_duplicates("month").sort_values("month")
        coverage = len(group) / expected
        if coverage < min_coverage or len(group) < min_months:
            continue
        x = np.array([m.ordinal for m in group.month], dtype=float) / 12
        slope = float(np.polyfit(x - x[0], group.groundwater_depth_m, 1)[0])
        rows.append(
            dict(
                well_id=well,
                aquifer_class=group.aquifer_class.iloc[0],
                n_months=len(group),
                coverage=coverage,
                depth_slope_m_year=slope,
                level_slope_m_year=-slope,
                first_month=str(group.month.min()),
                last_month=str(group.month.max()),
                interpretation="depth increase means level decline; storage change needs independent aquifer parameters",
            )
        )
    return pd.DataFrame(rows, columns=columns)
