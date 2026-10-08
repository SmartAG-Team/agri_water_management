GROWTH_REGULATION_CONFIG = {
    "stage_min": "V6",
    "stage_max": "V10",
    "risk_thresholds": {"medium": 0.25, "high": 0.50},
    "weights": {
        "density": 0.50,
        "yield_target": 0.15,
        "fertilizer": 0.20,
        "wind": 0.15,
    },
    "density_plants_ha": {"low": 60000.0, "high": 75000.0},
    "yield_target_kg_ha": {"low": 7500.0, "high": 15000.0},
    "fertilizer_n_kg_ha": {"low": 0.0, "high": 240.0},
    "fertilizer_k2o_kg_ha": {"low": 0.0, "high": 120.0},
    "potassium_credit": 0.25,
    "seasonal_max_wind_m_s": {"low": 4.0, "high": 12.0},
    "managed_risk_reduction_fraction": 0.25,
}
