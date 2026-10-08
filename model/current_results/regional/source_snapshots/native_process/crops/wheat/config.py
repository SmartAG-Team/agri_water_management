from __future__ import annotations

WHEAT_STAGE_ORDER = [
    "sowing",
    "seedling",
    "tillering",
    "jointing",
    "booting",
    "heading",
    "flowering",
    "grain_filling",
    "maturity",
]

spray_weather_config = {
    "PROFILE": "drone",
    "SPRAY_WEATHER_LABELS": [("Favorable", 0.65), ("Caution", 0.45), ("Unfavorable", 0.0)],
    "COMBINED_STRATEGY": "mean",
    "PARAMETERS": {
        "foliar_nutrition": [
            {"key": "temperature_2m", "x0": 18, "k": 0.35},
            {"key": "relative_humidity_2m", "x0": 62, "k": 0.08},
            {"key": "wind_speed_10m", "x0": -2.0, "k": 2.5, "negate": True},
            {"key": "precipitation", "x0": 0.0, "k": 10.0, "negate": True},
            {"key": "shortwave_radiation", "x0": -1.44, "k": 2.50, "negate": True},
        ],
        "fungicide": [
            {"key": "temperature_2m", "x0": 18, "k": 0.38},
            {"key": "relative_humidity_2m", "x0": 75, "k": 0.10},
            {"key": "wind_speed_10m", "x0": -2.0, "k": 2.5, "negate": True},
            {"key": "precipitation", "x0": 0.0, "k": 10.0, "negate": True},
            {"key": "shortwave_radiation", "x0": -1.62, "k": 2.50, "negate": True},
        ],
        "herbicide": [
            {"key": "temperature_2m", "x0": 16, "k": 0.38},
            {"key": "relative_humidity_2m", "x0": 65, "k": 0.08},
            {"key": "wind_speed_10m", "x0": -2.0, "k": 2.5, "negate": True},
            {"key": "precipitation", "x0": 0.0, "k": 10.0, "negate": True},
            {"key": "shortwave_radiation", "x0": -1.80, "k": 2.22, "negate": True},
        ],
        "insecticide": [
            {"key": "temperature_2m", "x0": 20, "k": 0.32},
            {"key": "relative_humidity_2m", "x0": 60, "k": 0.07},
            {"key": "wind_speed_10m", "x0": -2.0, "k": 2.5, "negate": True},
            {"key": "precipitation", "x0": 0.0, "k": 10.0, "negate": True},
            {"key": "shortwave_radiation", "x0": -2.16, "k": 1.94, "negate": True},
        ],
    },
}

FOLIAR_NUTRITION_POLICY = {
    "bbch_min": 31,
    "bbch_max": 75,
    "default_missing_micronutrients": False,
    "micronutrient_soil_keys": {"Zn": "zn_mg_kg", "B": "b_mg_kg"},
    "default_micros_g_ha": {"Zn": 250.0, "B": 120.0},
    "min_interval_days": 10,
    "release_days": 1,
    "absorption_efficiency": 0.50,
    "rescue_n_kg_ha": 6.0,
    "rescue_k2o_kg_ha": 4.0,
}

DISEASE_TARGETS = {
    "PUCCSI": {
        "name": "wheat_stripe_rust",
        "name_cn": "小麦条锈病",
        "temp_optimum_c": 13.0,
        "temp_width_c": 9.0,
        "humidity_weight": 0.65,
        "rain_weight": 0.25,
        "stage_window": {"tillering", "jointing", "booting", "heading", "flowering"},
        "stage_favorability_curve": {
            "stages": WHEAT_STAGE_ORDER,
            "favorability": [0.0, 0.15, 0.8, 1.0, 1.0, 0.9, 0.6, 0.2, 0.0],
        },
    },
    "ERYSGT": {
        "name": "wheat_powdery_mildew",
        "name_cn": "小麦白粉病",
        "temp_optimum_c": 18.0,
        "temp_width_c": 8.0,
        "humidity_weight": 0.55,
        "rain_weight": 0.10,
        "stage_window": {"tillering", "jointing", "booting", "heading"},
        "stage_favorability_curve": {
            "stages": WHEAT_STAGE_ORDER,
            "favorability": [0.0, 0.1, 0.75, 1.0, 1.0, 0.8, 0.25, 0.05, 0.0],
        },
    },
    "GIBBZE": {
        "name": "fusarium_head_blight",
        "name_cn": "小麦赤霉病",
        "temp_optimum_c": 24.0,
        "temp_width_c": 8.0,
        "humidity_weight": 0.50,
        "rain_weight": 0.55,
        "stage_window": {"heading", "flowering", "grain_filling"},
        "stage_favorability_curve": {
            "stages": WHEAT_STAGE_ORDER,
            "favorability": [0.0, 0.0, 0.0, 0.0, 0.2, 0.85, 1.0, 0.45, 0.0],
        },
    },
}

INSECT_TARGETS = {
    "MACSAV": {
        "name": "english_grain_aphid",
        "name_cn": "麦长管蚜",
        "temp_optimum_c": 20.0,
        "temp_width_c": 9.0,
        "humidity_optimum_pct": 62.0,
        "stage_window": {"tillering", "jointing", "booting", "heading", "grain_filling"},
        "stage_favorability_curve": {
            "stages": WHEAT_STAGE_ORDER,
            "favorability": [0.0, 0.2, 0.8, 1.0, 1.0, 1.0, 0.8, 0.55, 0.05],
        },
    },
    "PSEDSE": {
        "name": "oriental_armyworm",
        "name_cn": "东方黏虫",
        "temp_optimum_c": 24.0,
        "temp_width_c": 8.0,
        "humidity_optimum_pct": 70.0,
        "stage_window": {"jointing", "booting", "heading", "grain_filling"},
        "stage_favorability_curve": {
            "stages": WHEAT_STAGE_ORDER,
            "favorability": [0.0, 0.05, 0.25, 0.8, 1.0, 1.0, 0.75, 0.45, 0.05],
        },
    },
}

INSECTICIDE_PARAMS = {
    "wheat_pyrethroid_50EC": {
        "common_name": "wheat pyrethroid 50EC",
        "formulation": "EC",
        "irac_moa": "3A",
        "dose_unit": "ml_ai_mu",
        "label_rate_ml_ai_mu": [18.0, 30.0],
        "ai_content_g_per_L": 50.0,
        "ai_density_g_per_ml": 1.0,
        "dose_response": {"max_mortality": 0.84, "ed50_g_ai_mu": 0.9, "hill": 1.4},
        "residual": {"half_life_days": 5.5, "duration_days": 12.0},
        "rainfast": {"rainfast_hours": 2.0, "washoff_fraction": 0.18},
        "temp_modifier": {"q10": 1.12, "reference_c": 25.0},
        "notes": "Model parameter set for wheat aphid and armyworm protection; not an observed field operation.",
    },
}

PLANT_PROTECTION_RECOMMENDATION_CONFIG = {
    "disease": {
        "early_alert_days": 4,
        "spray_window_days": 5,
        "minimum_recommendation_interval_days": 14,
        "max_recommendations_per_season": 2,
    },
    "insect": {
        "early_alert_days": 4,
        "spray_window_days": 5,
        "minimum_recommendation_interval_days": 14,
        "max_recommendations_per_season": 2,
    },
}
