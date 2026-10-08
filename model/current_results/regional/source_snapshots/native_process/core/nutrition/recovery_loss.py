from __future__ import annotations

from datetime import date


def estimate_effective_remaining_nutrients(
    fertilizer_history: list[dict],
    decision_date: date,
    top_layer_relative_water: float,
    rainfall_next_3d_mm: float,
    irrigation_next_5d_mm: float,
) -> dict:
    effective = {"N": 0.0, "P2O5": 0.0, "K2O": 0.0, "S": 0.0}
    cumulative_available = {"N": 0.0, "P2O5": 0.0, "K2O": 0.0, "S": 0.0}
    recent_available = {"N": 0.0, "P2O5": 0.0, "K2O": 0.0, "S": 0.0}
    details = []
    for item in fertilizer_history:
        applied_date = item["date"]
        days_since = max(0, (decision_date - applied_date).days)
        nutrient_map = dict(item.get("nutrients_kg_ha") or {})
        if not nutrient_map and item.get("product_name"):
            nutrient_map = item.get("computed_nutrients_kg_ha") or {}

        if days_since <= 7:
            release_fraction = 0.78
            cumulative_fraction = 0.42
        elif days_since <= 21:
            release_fraction = 0.58
            cumulative_fraction = 0.76
        elif days_since <= 45:
            release_fraction = 0.22
            cumulative_fraction = 0.92
        else:
            release_fraction = 0.05
            cumulative_fraction = 0.95
        release_days_raw = item.get("release_days")
        if release_days_raw:
            release_days = max(1, int(release_days_raw))
            progress = max(0.0, min(1.0, days_since / release_days))
            if item.get("release_type") == "coated_slow_release" or release_days > 30:
                cumulative_fraction = progress
                release_fraction = max(0.0, 1.0 - progress)

        dryness_factor = 0.65 if top_layer_relative_water < 0.30 and days_since <= 10 else 1.0
        loss_factor = 0.82 if (rainfall_next_3d_mm >= 20.0 or irrigation_next_5d_mm >= 35.0) and days_since <= 5 else 1.0
        effective_fraction = max(0.0, min(1.0, release_fraction * dryness_factor * loss_factor))
        cumulative_fraction = max(0.0, min(1.0, cumulative_fraction * max(0.75, dryness_factor) * loss_factor))

        detail_item = {
            "date": str(applied_date),
            "product_name": item.get("product_name") or item.get("product_key"),
            "effective_fraction": round(effective_fraction, 4),
            "cumulative_fraction": round(cumulative_fraction, 4),
            "days_since": days_since,
        }
        for nutrient_key, amount in nutrient_map.items():
            if nutrient_key in effective:
                remaining = float(amount) * effective_fraction
                effective[nutrient_key] += remaining
                cumulative_available[nutrient_key] += float(amount) * cumulative_fraction
                if days_since <= 28:
                    recent_available[nutrient_key] += float(amount) * effective_fraction
        details.append(detail_item)

    return {
        "effective_remaining_nutrients": {k: round(v, 3) for k, v in effective.items()},
        "cumulative_available_nutrients": {k: round(v, 3) for k, v in cumulative_available.items()},
        "recent_available_nutrients": {k: round(v, 3) for k, v in recent_available.items()},
        "details": details,
    }
