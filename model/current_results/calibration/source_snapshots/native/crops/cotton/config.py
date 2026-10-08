from __future__ import annotations

COTTON_UUID = "69231650-8600-4000-8000-000000000002"

REGION_PARAMS = {
    "xinjiang_karamay": {
        "latitude": 45.58,
        "thermal_time_factor": 1.02,
        "heat_stress_threshold_c": 35.0,
        "irrigation_interval_shift_days": -1,
        "salinity_watch_ec_ds_m": 3.0,
    },
    "xinjiang_shihezi": {
        "latitude": 44.31,
        "thermal_time_factor": 1.00,
        "heat_stress_threshold_c": 34.5,
        "irrigation_interval_shift_days": 0,
        "salinity_watch_ec_ds_m": 3.2,
    },
    "xinjiang_aksu": {
        "latitude": 41.17,
        "thermal_time_factor": 1.05,
        "heat_stress_threshold_c": 36.0,
        "irrigation_interval_shift_days": -1,
        "salinity_watch_ec_ds_m": 3.5,
    },
}

DEFAULT_REGION_CODE = "xinjiang_karamay"
COTTON_STAGE_ORDER = [
    "sowing",
    "emergence",
    "seedling",
    "squaring",
    "flowering",
    "boll_setting",
    "boll_opening",
    "maturity",
]

COTTON_CARDINAL_TEMPERATURES = {
    "base_temperature": 12.0,
    "optimal_temperature": 30.0,
    "maximum_temperature": 42.0,
}

COTTON_BBCH_STAGE_LABELS = {
    0: "播种期",
    9: "出苗期",
    13: "苗期",
    16: "旺长期",
    51: "现蕾期",
    61: "初花期",
    65: "盛花期",
    75: "盛铃期",
    81: "初吐絮期",
    85: "吐絮期",
    89: "完熟期",
}

COTTON_BBCH_COMPAT_STAGE = {
    0: "sowing",
    9: "emergence",
    13: "seedling",
    16: "seedling",
    51: "squaring",
    61: "flowering",
    65: "flowering",
    75: "boll_setting",
    81: "boll_opening",
    85: "boll_opening",
    89: "maturity",
}

COTTON_MATURITY_GROUP_FACTORS = {
    "very_early": 0.88,
    "early": 0.94,
    "middle": 1.00,
    "late": 1.08,
    "very_late": 1.15,
}

_COTTON_BBCH_MIDDLE_THRESHOLDS = {
    0: 0.0,
    9: 150.0,
    13: 305.0,
    16: 425.0,
    51: 675.0,
    61: 1120.0,
    65: 1300.0,
    75: 1550.0,
    81: 1950.0,
    85: 2025.0,
    89: 2100.0,
}

def _scale_cotton_thresholds(factor: float) -> dict[int, float]:
    return {
        bbch: 0.0 if bbch == 0 else round(value * factor, 1)
        for bbch, value in _COTTON_BBCH_MIDDLE_THRESHOLDS.items()
    }


COTTON_BBCH_THRESHOLDS = {
    group: _scale_cotton_thresholds(factor)
    for group, factor in COTTON_MATURITY_GROUP_FACTORS.items()
}

# Backward-compatible aliases for older imports. The values are now BBCH-based.
COTTON_STAGE_THRESHOLDS = COTTON_BBCH_THRESHOLDS
COTTON_STAGE_LABELS = COTTON_BBCH_COMPAT_STAGE

STAGE_ORDER = tuple(COTTON_BBCH_STAGE_LABELS.keys())

IRRIGATION_METHOD_SPECS = {
    "drip_under_mulch": {
        "efficiency": 0.92,
        "evaporation_multiplier": 0.24,
        "preferred_interval_days": 6,
        "max_event_mm": 36.0,
        "min_event_mm": 14.0,
        "target_refill_bonus": 0.05,
    },
    "drip": {
        "efficiency": 0.88,
        "evaporation_multiplier": 0.68,
        "preferred_interval_days": 7,
        "max_event_mm": 42.0,
        "min_event_mm": 18.0,
        "target_refill_bonus": 0.02,
    },
    "flood": {
        "efficiency": 0.62,
        "evaporation_multiplier": 1.05,
        "preferred_interval_days": 14,
        "max_event_mm": 85.0,
        "min_event_mm": 45.0,
        "target_refill_bonus": -0.03,
    },
}

DEFAULT_ECONOMICS = {
    "seed_cotton_price_cny_per_kg": 7.2,
    "water_cost_cny_per_mm_ha": 8.0,
    "electricity_cost_cny_per_kwh": 0.62,
    "pump_kwh_per_mm_ha": 0.46,
    "irrigation_event_labor_cost_cny_ha": 24.0,
    "fertilizer_event_labor_cost_cny_ha": 20.0,
    "product_prices": {
        "urea": 2.6,
        "map": 4.2,
        "potassium_sulfate": 5.0,
    },
}

COTTON_NUTRIENT_COEFFICIENTS = {
    "N": 0.040,
    "P2O5": 0.015,
    "K2O": 0.036,
}

COTTON_SPLIT_PLAN = {
    "basal": {"fraction": 0.20, "stage_code": 0},
    "squaring": {"fraction": 0.25, "stage_code": 51},
    "flowering": {"fraction": 0.32, "stage_code": 61},
    "boll_setting": {"fraction": 0.23, "stage_code": 75},
}

DISEASE_TARGETS = {
    "VERTDA": {
        "name": "cotton_verticillium_wilt",
        "name_cn": "棉花黄萎病",
        "temp_optimum_c": 24.0,
        "temp_width_c": 8.0,
        "humidity_weight": 0.55,
        "rain_weight": 0.25,
        "stage_window": {"seedling", "squaring", "flowering", "boll_setting"},
        "stage_favorability_curve": {
            "stages": COTTON_STAGE_ORDER,
            "favorability": [0.0, 0.0, 0.8, 1.0, 1.0, 0.7, 0.2, 0.0],
        },
    },
    "FUSOXY": {
        "name": "cotton_fusarium_wilt",
        "name_cn": "棉花枯萎病",
        "temp_optimum_c": 28.0,
        "temp_width_c": 8.0,
        "humidity_weight": 0.40,
        "rain_weight": 0.35,
        "stage_window": {"seedling", "squaring", "flowering"},
        "stage_favorability_curve": {
            "stages": COTTON_STAGE_ORDER,
            "favorability": [0.0, 0.0, 1.0, 0.85, 0.55, 0.2, 0.05, 0.0],
        },
    },
    "BOLLROT": {
        "name": "cotton_boll_rot",
        "name_cn": "棉铃腐病",
        "temp_optimum_c": 27.0,
        "temp_width_c": 7.0,
        "humidity_weight": 0.70,
        "rain_weight": 0.45,
        "stage_window": {"flowering", "boll_setting", "boll_opening"},
        "stage_favorability_curve": {
            "stages": COTTON_STAGE_ORDER,
            "favorability": [0.0, 0.0, 0.0, 0.15, 0.85, 1.0, 0.75, 0.1],
        },
    },
}

INSECT_TARGETS = {
    "HELIAR": {
        "name": "cotton_bollworm",
        "name_cn": "棉铃虫",
        "temp_optimum_c": 29.0,
        "temp_width_c": 8.0,
        "humidity_optimum_pct": 65.0,
        "stage_window": {"squaring", "flowering", "boll_setting"},
        "stage_favorability_curve": {
            "stages": COTTON_STAGE_ORDER,
            "favorability": [0.0, 0.0, 0.1, 1.0, 1.0, 0.72, 0.12, 0.0],
        },
    },
    "APHIGO": {
        "name": "cotton_aphid",
        "name_cn": "棉蚜",
        "temp_optimum_c": 24.0,
        "temp_width_c": 7.0,
        "humidity_optimum_pct": 55.0,
        "stage_window": {"seedling", "squaring", "flowering"},
        "stage_favorability_curve": {
            "stages": COTTON_STAGE_ORDER,
            "favorability": [0.0, 0.05, 1.0, 0.95, 0.62, 0.16, 0.05, 0.0],
        },
    },
    "TETRUR": {
        "name": "two_spotted_spider_mite",
        "name_cn": "二斑叶螨",
        "temp_optimum_c": 33.0,
        "temp_width_c": 7.0,
        "humidity_optimum_pct": 35.0,
        "stage_window": {"squaring", "flowering", "boll_setting", "boll_opening"},
        "stage_favorability_curve": {
            "stages": COTTON_STAGE_ORDER,
            "favorability": [0.0, 0.0, 0.2, 0.85, 1.0, 0.62, 0.18, 0.0],
        },
    },
}

COTTON_INSECTICIDE_PARAMS = {
    "cotton_pyrethroid_50EC": {
        "common_name": "cotton pyrethroid 50EC",
        "formulation": "EC",
        "irac_moa": "3A",
        "dose_unit": "ml_ai_mu",
        "label_rate_ml_ai_mu": [20.0, 30.0],
        "ai_content_g_per_L": 50.0,
        "ai_density_g_per_ml": 1.0,
        "dose_response": {"max_mortality": 0.82, "ed50_g_ai_mu": 0.9, "hill": 1.4},
        "residual": {"half_life_days": 5.0, "duration_days": 11.0},
        "rainfast": {"rainfast_hours": 2.0, "washoff_fraction": 0.18},
        "temp_modifier": {"q10": 1.15, "reference_c": 25.0},
        "notes": "Model parameter set for cotton insect protection; not an observed field operation.",
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

spray_weather_config = {
    "PROFILE": "drone",
    "SPRAY_WEATHER_LABELS": [("Favorable", 0.65), ("Caution", 0.45), ("Unfavorable", 0.0)],
    "COMBINED_STRATEGY": "mean",
    "PARAMETERS": {
        "foliar_nutrition": [
            {"key": "temperature_2m", "x0": 25, "k": 0.32},
            {"key": "relative_humidity_2m", "x0": 60, "k": 0.08},
            {"key": "wind_speed_10m", "x0": -2.0, "k": 2.5, "negate": True},
            {"key": "precipitation", "x0": 0.0, "k": 10.0, "negate": True},
            {"key": "shortwave_radiation", "x0": -1.62, "k": 2.50, "negate": True},
        ],
        "fungicide": [
            {"key": "temperature_2m", "x0": 24, "k": 0.35},
            {"key": "relative_humidity_2m", "x0": 72, "k": 0.09},
            {"key": "wind_speed_10m", "x0": -2.0, "k": 2.5, "negate": True},
            {"key": "precipitation", "x0": 0.0, "k": 10.0, "negate": True},
            {"key": "shortwave_radiation", "x0": -1.80, "k": 2.50, "negate": True},
        ],
        "herbicide": [
            {"key": "temperature_2m", "x0": 23, "k": 0.35},
            {"key": "relative_humidity_2m", "x0": 65, "k": 0.08},
            {"key": "wind_speed_10m", "x0": -2.0, "k": 2.5, "negate": True},
            {"key": "precipitation", "x0": 0.0, "k": 10.0, "negate": True},
            {"key": "shortwave_radiation", "x0": -1.98, "k": 2.22, "negate": True},
        ],
        "insecticide": [
            {"key": "temperature_2m", "x0": 26, "k": 0.30},
            {"key": "relative_humidity_2m", "x0": 60, "k": 0.08},
            {"key": "wind_speed_10m", "x0": -2.0, "k": 2.5, "negate": True},
            {"key": "precipitation", "x0": 0.0, "k": 10.0, "negate": True},
            {"key": "shortwave_radiation", "x0": -2.34, "k": 1.94, "negate": True},
        ],
    },
}

FOLIAR_NUTRITION_POLICY = {
    "bbch_min": 51,
    "bbch_max": 85,
    "default_missing_micronutrients": True,
    "micronutrient_soil_keys": {"B": "b_mg_kg", "Zn": "zn_mg_kg"},
    "default_micros_g_ha": {"B": 180.0, "Zn": 300.0},
    "min_interval_days": 10,
    "release_days": 1,
    "absorption_efficiency": 0.50,
    "disable_rescue_n_when_fertigation_enabled": True,
    "rescue_n_kg_ha": 4.0,
    "rescue_k2o_kg_ha": 5.0,
}

# Regional defaults are agronomic model parameters, not pesticide-label defaults.
# Product, dose, interval, and weather restrictions remain inventory driven.
COTTON_MANAGEMENT_POLICY = {
    "regional_phenology_anchors": {
        "xinjiang_karamay": (
            {"days_after_planting": 60, "target_progress": 0.48},
            {"days_after_planting": 125, "target_progress": 0.90},
            {"days_after_planting": 145, "target_progress": 1.00},
        ),
    },
    "canopy": {
        "vigor_threshold": 0.65,
        "lai_range": (3.5, 6.0),
        "density_plants_ha_range": (70000.0, 110000.0),
        "yield_target_kg_ha_range": (4500.0, 6500.0),
        "weights": {"lai": 0.45, "density": 0.20, "yield_target": 0.15, "resources": 0.20},
    },
    "topping": {
        "bbch_min": 65,
        "bbch_max": 75,
        "calendar_start": (7, 5),
        "calendar_end": (7, 15),
        "minimum_prior_regulator_applications": 2,
        "post_topping_leaf_expansion_multiplier": 0.20,
    },
    "harvest_aid": {
        "bbch_min": 85,
        "calendar_start": (9, 5),
        "calendar_end": (9, 15),
        "minimum_live_lai": 0.8,
        "default_response_days": 10,
        "default_target_defoliation_pct": 90.0,
    },
    "harvest": {
        "minimum_defoliation_pct": 90.0,
        "minimum_open_bolls_pct": 90.0,
    },
}
