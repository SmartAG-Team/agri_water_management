from __future__ import annotations


def stage_rules_for_bbch(bbch: int, irrigation_method: str | None = None) -> dict:
    if bbch < 21:
        max_single = 20.0
        hard_max = 28.0
        min_effective = 12.0
        coverage_multiplier = 1.3
        practical_gap = 12.0
        method = "topdress_before_irrigation"
        yield_gain = 8.0
    elif bbch < 31:
        max_single = 35.0
        hard_max = 60.0
        min_effective = 15.0
        coverage_multiplier = 2.0
        practical_gap = 18.0
        method = "topdress_before_irrigation"
        yield_gain = 11.0
    elif bbch < 49:
        max_single = 60.0
        hard_max = 80.0
        min_effective = 20.0
        coverage_multiplier = 2.2
        practical_gap = 22.0
        method = "topdress_before_irrigation"
        yield_gain = 13.0
    elif bbch < 69:
        max_single = 35.0
        hard_max = 45.0
        min_effective = 12.0
        coverage_multiplier = 1.5
        practical_gap = 16.0
        method = "topdress_before_irrigation"
        yield_gain = 8.0
    else:
        max_single = 15.0
        hard_max = 20.0
        min_effective = 8.0
        coverage_multiplier = 1.0
        practical_gap = 10.0
        method = "small_topdress_only_if_deficient"
        yield_gain = 4.0

    if irrigation_method in {"drip", "micro-sprinkler"}:
        method = "fertigation_with_irrigation"

    return {
        "max_single_n_rate_kg_ha": max_single,
        "hard_max_single_n_rate_kg_ha": hard_max,
        "min_effective_n_rate_kg_ha": min_effective,
        "coverage_multiplier": coverage_multiplier,
        "practical_gap_kg_ha": practical_gap,
        "gap_to_rate_factor": 0.72,
        "application_window_days": 3,
        "minimum_surface_water_for_broadcast": 0.34,
        "rainfall_activation_mm": 6.0,
        "heavy_rain_risk_mm": 20.0,
        "default_method": method,
        "default_n_product": "urea",
        "yield_gain_kg_grain_per_kg_n": yield_gain,
        "base_reasons": [
            "recommendation follows wheat stage-specific split N logic",
            "spring recovery and jointing are prioritized for N supply",
        ],
    }
