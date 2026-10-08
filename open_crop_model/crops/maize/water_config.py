from __future__ import annotations


IRRIGATION_METHOD_SPECS = {
    "flood": {
        "efficiency": 0.60,
        "min_depth_mm": 55.0,
        "max_depth_mm": 95.0,
        "preferred_interval_days": 16,
        "evaporation_multiplier": 1.14,
        "stress_threshold_shift": -0.03,
        "target_refill_bonus": 0.06,
        "max_event_bonus": 0,
    },
    "sprinkler": {
        "efficiency": 0.75,
        "min_depth_mm": 35.0,
        "max_depth_mm": 65.0,
        "preferred_interval_days": 12,
        "evaporation_multiplier": 1.00,
        "stress_threshold_shift": 0.00,
        "target_refill_bonus": 0.00,
        "max_event_bonus": 0,
    },
    "micro-sprinkler": {
        "efficiency": 0.82,
        "min_depth_mm": 22.0,
        "max_depth_mm": 50.0,
        "preferred_interval_days": 9,
        "evaporation_multiplier": 0.90,
        "stress_threshold_shift": 0.05,
        "target_refill_bonus": -0.03,
        "max_event_bonus": 1,
    },
    "drip": {
        "efficiency": 0.90,
        "min_depth_mm": 12.0,
        "max_depth_mm": 32.0,
        "preferred_interval_days": 6,
        "evaporation_multiplier": 0.76,
        "stress_threshold_shift": 0.08,
        "target_refill_bonus": -0.06,
        "max_event_bonus": 2,
    },
}

SOIL_REALISM = {
    "sand": {"max_recommended_events": 7, "interval_adjust_days": -2, "max_depth_bonus_mm": 0.0},
    "sandy_loam": {"max_recommended_events": 5, "interval_adjust_days": 1, "max_depth_bonus_mm": 5.0},
    "clay": {"max_recommended_events": 4, "interval_adjust_days": 5, "max_depth_bonus_mm": 15.0},
}
