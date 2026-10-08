from __future__ import annotations


def _combined_stress_index(row: dict) -> float:
    if str(row.get("stress_risk", "")).upper() == "NOT_SEASONAL":
        return 0.0
    if "plant_water_stress_index" in row or "water_uptake_ratio" in row:
        return float(row.get("instant_stress_index_water", row.get("stress_index_water", 0.0)) or 0.0)
    return max(
        float(row.get("instant_stress_index_water", row.get("stress_index_water", 0.0)) or 0.0),
        float(row.get("instant_stress_index_eta_ratio", row.get("stress_index_eta_ratio", 0.0)) or 0.0),
    )


def _window_status(scores: list[float], forecast_rain_mm: float) -> tuple[str, float]:
    if not scores:
        return "LOW", 0.0
    high_days = sum(score >= 0.60 for score in scores)
    mean_score = sum(scores) / len(scores)
    max_score = max(scores)
    rain_credit = min(0.22, max(0.0, forecast_rain_mm) / 240.0)
    adjusted_score = max(0.0, mean_score - rain_credit)
    if adjusted_score >= 0.60 or (high_days >= 4 and forecast_rain_mm < 25.0):
        return "HIGH", adjusted_score
    if adjusted_score >= 0.30 or (high_days >= 2 and forecast_rain_mm < 50.0) or (max_score >= 0.55 and forecast_rain_mm < 25.0):
        return "MEDIUM", adjusted_score
    return "LOW", adjusted_score


def apply_forecast_aggregated_drought_status(daily_records: list[dict], window_days: int = 7) -> list[dict]:
    """Expose drought status as a short forward-looking field condition.

    The physical water-balance stress remains available under ``instant_*`` fields.
    Public ``stress_risk`` is the 7-day risk summary, with actual irrigation days
    overlaid as ``IRRIGATED``.
    """
    if not daily_records:
        return daily_records

    records = daily_records
    for row in records:
        row["instant_stress_index_water"] = row.get("instant_stress_index_water", row.get("stress_index_water", 0.0))
        row["instant_stress_index_eta_ratio"] = row.get("instant_stress_index_eta_ratio", row.get("stress_index_eta_ratio", 0.0))
        row["instant_stress_risk"] = row.get("instant_stress_risk", row.get("stress_risk", "LOW"))

    scores = [_combined_stress_index(row) for row in records]
    for index, row in enumerate(records):
        if str(row.get("stress_risk", "")).upper() == "NOT_SEASONAL":
            row["instant_stress_index_water"] = 0.0
            row["instant_stress_index_eta_ratio"] = 0.0
            row["instant_stress_risk"] = "NOT_SEASONAL"
            row["water_uptake_ratio"] = 1.0
            row["plant_water_stress_index"] = 0.0
            row["drought_risk_window_days"] = window_days
            row["forecast_rain_7d_mm"] = 0.0
            row["mean_instant_stress_index_7d"] = 0.0
            row["max_instant_stress_index_7d"] = 0.0
            row["forecast_adjusted_stress_index_7d"] = 0.0
            row["stress_index_water"] = 0.0
            row["stress_index_eta_ratio"] = 0.0
            row["stress_risk"] = "NOT_SEASONAL"
            continue
        window = records[index : index + max(1, window_days)]
        window_scores = scores[index : index + max(1, window_days)]
        mean_score = sum(window_scores) / len(window_scores)
        max_score = max(window_scores) if window_scores else 0.0
        forecast_rain = round(sum(float(item.get("precipitation_mm", 0.0) or 0.0) for item in window), 3)
        public_status, adjusted_score = _window_status(window_scores, forecast_rain)
        row["drought_risk_window_days"] = window_days
        row["forecast_rain_7d_mm"] = forecast_rain
        row["mean_instant_stress_index_7d"] = round(mean_score, 4)
        row["max_instant_stress_index_7d"] = round(max_score, 4)
        row["forecast_adjusted_stress_index_7d"] = round(adjusted_score, 4)
        row["stress_index_water"] = round(adjusted_score, 4)
        row["stress_index_eta_ratio"] = row["instant_stress_index_eta_ratio"]
        row["stress_risk"] = "IRRIGATED" if float(row.get("irrigation_mm", 0.0) or 0.0) > 0.0 else public_status
    return records


def diagnose_stress(
    relative_available_water: float,
    eta_actual_mm: float,
    crop_et_potential_mm: float,
    potential_transpiration_mm: float | None = None,
    actual_transpiration_mm: float | None = None,
) -> dict:
    raw = max(0.0, min(1.0, relative_available_water))
    eta_ratio = 1.0 if crop_et_potential_mm <= 0 else max(0.0, min(1.0, eta_actual_mm / crop_et_potential_mm))
    transpiration_demand = max(0.0, float(potential_transpiration_mm or 0.0))
    if actual_transpiration_mm is not None and potential_transpiration_mm is not None:
        if transpiration_demand <= 0.25:
            water_uptake_ratio = 1.0
            water_stress_index = 0.0
        else:
            water_uptake_ratio = max(0.0, min(1.0, float(actual_transpiration_mm) / transpiration_demand))
            water_stress_index = 1.0 - water_uptake_ratio
        eta_stress_index = 1.0 - eta_ratio
    else:
        demand_factor = max(0.15, min(1.0, max(crop_et_potential_mm / 3.5, transpiration_demand / 1.8)))
        water_stress_index = (1.0 - raw) * demand_factor
        water_uptake_ratio = max(0.0, min(1.0, 1.0 - water_stress_index))
        eta_stress_index = 1.0 - eta_ratio
    if water_stress_index >= 0.60:
        level = "HIGH"
    elif water_stress_index >= 0.30:
        level = "MEDIUM"
    else:
        level = "LOW"
    return {
        "stress_index_water": round(water_stress_index, 4),
        "stress_index_eta_ratio": round(eta_stress_index, 4),
        "water_uptake_ratio": round(water_uptake_ratio, 4),
        "plant_water_stress_index": round(water_stress_index, 4),
        "stress_risk": level,
    }
