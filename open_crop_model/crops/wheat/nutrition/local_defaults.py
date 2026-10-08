from __future__ import annotations


DEFAULT_WHEAT_NUTRIENT_COEFFICIENTS = {
    "N": 31.0,
    "P2O5": 12.0,
    "K2O": 20.0,
    "S": 4.0,
    "Zn": 45.0,
    "B": 18.0,
}

DEFAULT_SOIL_TEST = {
    "organic_matter_g_kg": 15.0,
    "mineral_n_kg_ha": 45.0,
    "ph": 7.8,
}

DEFAULT_RECOVERY_ASSUMPTIONS = {
    "split_n_strategy": "recovery_jointing_priority",
    "topdress_recovery_fraction": 0.62,
    "fertigation_recovery_fraction": 0.72,
}

DEFAULT_WHEAT_N_SPLIT = {
    "basal_fraction": 0.34,
    "basal_fraction_low_soil_n_bonus": 0.08,
    "basal_fraction_high_yield_bonus": 0.06,
    "basal_fraction_high_soil_n_penalty": 0.08,
    "basal_fraction_low_om_bonus": 0.04,
    "basal_fraction_min": 0.22,
    "basal_fraction_max": 0.48,
    "basal_n_min_kg_ha": 30.0,
    "basal_n_max_kg_ha": 90.0,
    "default_basal_product": "compound_fertilizer",
    "default_basal_n_pct": 15.0,
}
