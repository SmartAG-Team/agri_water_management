from __future__ import annotations


def diagnose_nutrient_status(
    nutrient_key: str,
    lai_ratio: float,
    biomass_ratio: float,
    available_nutrient_kg_ha: float,
    cumulative_supply_to_date_kg_ha: float,
    cumulative_demand_to_date_kg_ha: float,
    remaining_demand_kg_ha: float,
    near_term_demand_kg_ha: float,
    recent_effective_n_kg_ha: float,
    phase_name: str,
) -> dict:
    supply_ratio = 1.2 if remaining_demand_kg_ha <= 0 else available_nutrient_kg_ha / max(remaining_demand_kg_ha, 1e-6)
    cumulative_ratio = 1.2 if cumulative_demand_to_date_kg_ha <= 0 else cumulative_supply_to_date_kg_ha / max(cumulative_demand_to_date_kg_ha, 1e-6)
    near_term_gap = near_term_demand_kg_ha - available_nutrient_kg_ha * 0.45
    reasons = []

    if recent_effective_n_kg_ha >= near_term_demand_kg_ha * 0.8 and cumulative_ratio >= 0.9:
        status = "adequate"
        reasons.append({"code": "recent_application_covers_near_term_demand", "value": round(recent_effective_n_kg_ha, 3)})
    elif cumulative_ratio >= 1.0 and near_term_gap <= 6.0 and lai_ratio >= 0.9 and biomass_ratio >= 0.95:
        status = "adequate"
        reasons.append({"code": "cumulative_supply_meets_crop_demand", "value": round(cumulative_ratio, 3)})
    elif near_term_gap > 10.0 and (supply_ratio < 0.55 or cumulative_ratio < 0.72 or (lai_ratio < 0.80 and biomass_ratio < 0.82)):
        status = "deficient"
        reasons.append({"code": "low_supply_gap", "value": round(near_term_gap, 3)})
    elif supply_ratio < 0.75 or cumulative_ratio < 0.9 or lai_ratio < 0.9 or biomass_ratio < 0.95:
        status = "slightly_deficient"
        reasons.append({"code": "suboptimal_trajectory", "value": round(min(lai_ratio, biomass_ratio), 3)})
    elif supply_ratio > 1.40 and cumulative_ratio > 1.25 and lai_ratio > 1.05 and biomass_ratio > 1.05 and phase_name not in {"grain_filling", "maturity"}:
        status = "excessive"
        reasons.append({"code": "surplus_supply", "value": round(supply_ratio, 3)})
    else:
        status = "adequate"
        reasons.append({"code": "supply_matches_demand", "value": round(cumulative_ratio, 3)})

    return {
        "nutrient_key": nutrient_key,
        "status": status,
        "lai_ratio": round(lai_ratio, 3),
        "biomass_ratio": round(biomass_ratio, 3),
        "supply_ratio": round(supply_ratio, 3),
        "cumulative_supply_ratio": round(cumulative_ratio, 3),
        "cumulative_supply_to_date_kg_ha": round(cumulative_supply_to_date_kg_ha, 3),
        "cumulative_demand_to_date_kg_ha": round(cumulative_demand_to_date_kg_ha, 3),
        "near_term_gap_kg_ha": round(near_term_gap, 3),
        "recent_effective_n_kg_ha": round(recent_effective_n_kg_ha, 3),
        "reasons": reasons,
    }
