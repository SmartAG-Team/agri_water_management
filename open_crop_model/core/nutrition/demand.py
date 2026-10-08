from __future__ import annotations


def estimate_nutrient_demand(
    nutrient_key: str,
    target_yield_kg_ha: float,
    current_biomass_kg_ha: float,
    expected_total_biomass_kg_ha: float,
    current_uptake_fraction: float,
    stage_window_fraction: float,
    crop_coefficients: dict[str, float],
    activity_factor: float = 1.0,
) -> dict:
    total_per_t = float(crop_coefficients.get(nutrient_key, 0.0))
    seasonal_total = max(0.0, target_yield_kg_ha / 1000.0 * total_per_t)
    biomass_progress = 0.0 if expected_total_biomass_kg_ha <= 0 else max(0.0, min(1.0, current_biomass_kg_ha / expected_total_biomass_kg_ha))
    cumulative_target = seasonal_total * max(current_uptake_fraction, biomass_progress * 0.92)
    remaining_demand = max(0.0, seasonal_total - cumulative_target)
    near_term_demand = remaining_demand * max(0.08, min(0.45, stage_window_fraction)) * max(0.2, min(1.0, activity_factor))
    return {
        "nutrient_key": nutrient_key,
        "seasonal_total_demand": round(seasonal_total, 3),
        "cumulative_demand_to_date": round(cumulative_target, 3),
        "remaining_demand": round(remaining_demand, 3),
        "near_term_demand": round(near_term_demand, 3),
        "biomass_progress": round(biomass_progress, 4),
    }
