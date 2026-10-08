# config.py
# Maize insect model configuration (SPODEX, SPOFRU, OSTFUR, HELARM)
# All daily risk categories use a 0–100 scale.

from __future__ import annotations

# ----------------------------
# Global metadata
# ----------------------------
CONFIG_VERSION = "0.2.4-stage-handoff"
REGION = "North_China_Plain"
TEMPORAL_RESOLUTION = "hourly"

from crops.maize.config import IOWA_Stage_ORDER as IOWA_STAGES

# Variety/crop resistance correction – multiplier applied to favorability or kill
VARIETY_SUSC_CORRECTION = {
    "susceptibility": [1, 5, 9],   # resistant – medium – susceptible
    "impact":         [0.85, 1.00, 1.15],
}

DEFAULT_WEATHER_VARIABLES = [
    "temperature_2m",
    "relative_humidity_2m",
    "wind_speed_10m",
    "precipitation",
    "shortwave_radiation",
]

# -------------------------------------------------------------
# Default maize-stage correction bands (can be reused per pest)
# These multiply daily favorability (0..1) BEFORE scaling to 0..100.
# -------------------------------------------------------------
MAIZE_STAGE_CORRECTION_TEMPLATES = {
    "VE_V4":  0.80,
    "V5_V8":  0.95,
    "V9_V12": 1.05,
    "V13_VT": 1.10,
    "R1_R3":  1.10,
    "R4_R6":  0.90,
}

# ----------------------------
# Pesticides master data
# ----------------------------
# Dose–response: Emax model  efficacy = Emax * dose^h / (ED50^h + dose^h)
# Residual:      efficacy(t) *= 0.5 ** (t / half_life_days)
# Rainfast:      if rain_after_spray>=threshold within rainfast_hours -> *= rain_penalty
# Temp (Q10):    if enabled -> *= Q10 ** ((T - ref_T)/10)
pesticide_params = {
    "chlorantraniliprole_200SC": {
        "common_name": "氯虫苯甲酰胺 200SC",
        "formulation": "SC",
        "irac_moa": "28",
        "label_rate_g_ai_ha": [10, 60],
        "dose_response": {"Emax": 0.98, "ED50": 25.0, "hill": 1.2},
        "dose_unit": "g_ai_ha",
        "residual": {"half_life_days": 10.0},
        "rainfast": {"rainfast_hours": 2, "rainfast_threshold_mm": 5.0, "rain_penalty": 0.85},
        "temp_modifier": {"enabled": True, "ref_T": 25.0, "Q10": 1.15},
        "notes": "对鳞翅目幼虫较佳，二龄窗口效果最佳。",
    },
    "emamectin_benzoate_50WG": {
        "common_name": "甲氨基阿维菌素苯甲酸盐 50WG",
        "formulation": "WG",
        "irac_moa": "6",
        "label_rate_g_ai_ha": [5, 30],
        "dose_response": {"Emax": 0.95, "ED50": 10.0, "hill": 1.5},
        "dose_unit": "g_ai_ha",
        "residual": {"half_life_days": 5.0},
        "rainfast": {"rainfast_hours": 3, "rainfast_threshold_mm": 3.0, "rain_penalty": 0.80},
        "temp_modifier": {"enabled": True, "ref_T": 25.0, "Q10": 1.10},
        "notes": "对小龄幼虫高效，具胃毒活性。",
    },
    "indoxacarb_150SC": {
        "common_name": "茚虫威 150SC",
        "formulation": "SC",
        "irac_moa": "22A",
        "label_rate_g_ai_ha": [25, 100],
        "dose_response": {"Emax": 0.92, "ED50": 35.0, "hill": 1.3},
        "dose_unit": "g_ai_ha",
        "residual": {"half_life_days": 7.0},
        "rainfast": {"rainfast_hours": 2, "rainfast_threshold_mm": 5.0, "rain_penalty": 0.85},
        "temp_modifier": {"enabled": False},
        "notes": "对鳞翅目幼虫有效，摄食后致死。",
    },
    "lambda_cyhalothrin_25EC": {
        "common_name": "高效氯氟氰菊酯 25EC",
        "formulation": "EC",
        "irac_moa": "3A",
        "label_rate_g_ai_ha": [10, 40],
        "dose_response": {"Emax": 0.85, "ED50": 18.0, "hill": 1.1},
        "dose_unit": "g_ai_ha",
        "residual": {"half_life_days": 4.0},
        "rainfast": {"rainfast_hours": 1, "rainfast_threshold_mm": 2.0, "rain_penalty": 0.75},
        "temp_modifier": {"enabled": False},
        "notes": "触杀较强，残效较短，注意抗性管理。",
    },
    "spinetoram_120SC": {
        "common_name": "螺虫乙酯 120SC",
        "formulation": "SC",
        "irac_moa": "5",
        "label_rate_g_ai_ha": [30, 120],
        "dose_response": {"Emax": 0.93, "ED50": 40.0, "hill": 1.2},
        "dose_unit": "g_ai_ha",
        "residual": {"half_life_days": 6.0},
        "rainfast": {"rainfast_hours": 2, "rainfast_threshold_mm": 5.0, "rain_penalty": 0.85},
        "temp_modifier": {"enabled": True, "ref_T": 25.0, "Q10": 1.05},
        "notes": "胃毒/触杀，对多龄期有效。",
    },
}

# -------------------------------------------------------------
# Risk categories on a 0–100 index (favorability × 100 before classifying)
# -------------------------------------------------------------
from core.utils import get_disease_insect_weed_status_and_code
stress_code=get_disease_insect_weed_status_and_code('stress_code')

DEFAULT_DAILY_RISK_CATEGORIES_0_100 = {
    "0-30":   stress_code[1],
    "30-80":  stress_code[2],
    "80-900": stress_code[3]
}
DEFAULT_SHORT_AGG_RISK_CATEGORIES_0_100 = {
    "0-30":   stress_code[1],
    "30-80":  stress_code[2],
    "80-900": stress_code[3]
}

# Helper: per-pest maize stage correction, emphasizing the right windows
def _stage_correction_for(pest: str) -> dict:
    """
    Returns a dict of maize stage bands → multiplier.
    Bands must be among: VE_V4, V5_V8, V9_V12, V13_VT, R1_R3, R4_R6
    """
    base = MAIZE_STAGE_CORRECTION_TEMPLATES.copy()

    if pest == "SPODEX":
        # Early migrant; peaks pre-tassel, declines after R1
        base.update({
            "VE_V4":  0.90,
            "V5_V8":  1.00,
            "V9_V12": 1.15,
            "V13_VT": 1.10,
            "R1_R3":  0.90,
            "R4_R6":  0.75,
        })
    elif pest == "SPOFRU":
        # Later migrant, strongest VT–R2
        base.update({
            "VE_V4":  0.60,
            "V5_V8":  0.90,
            "V9_V12": 1.05,
            "V13_VT": 1.15,
            "R1_R3":  1.20,
            "R4_R6":  0.95,
        })
    elif pest == "OSTFUR":
        # Asian corn borer: 1st gen in June (V5–V10), 2nd gen July–Aug (VT–R2)
        base.update({
            "VE_V4":  0.70,
            "V5_V8":  1.10,
            "V9_V12": 1.10,
            "V13_VT": 1.20,
            "R1_R3":  1.20,
            "R4_R6":  0.90,
        })
    elif pest == "HELARM":
        # Multiple pulses; strong around VT–R2, still relevant at R3
        base.update({
            "VE_V4":  0.80,
            "V5_V8":  1.00,
            "V9_V12": 1.05,
            "V13_VT": 1.15,
            "R1_R3":  1.15,
            "R4_R6":  0.95,
        })
    return base

def _stage_favorability_curve_for(pest: str) -> dict:
    """
    Per-pest maize stage multiplier curve for actionable crop damage potential.
    X values are Iowa maize stages and are linearly interpolated by stage order.
    """
    if pest == "SPODEX":
        return {
            "stages": ["VS", "VE", "V2", "V3", "V6", "V8", "V10", "V11", "V12", "VT", "R6"],
            "favorability": [0.0, 0.0, 0.20, 0.50, 0.95, 1.10, 0.85, 0.40, 0.0, 0.0, 0.0],
        }
    if pest == "SPOFRU":
        return {
            "stages": ["VS", "VE", "V2", "V3", "V6", "V10", "VT", "R2", "R3", "R4", "R5", "R6"],
            "favorability": [0.0, 0.0, 0.0, 0.4, 1.0, 1.2, 1.15, 0.8, 0.28, 0.08, 0.02, 0.0],
        }
    if pest == "OSTFUR":
        return {
            "stages": ["VS", "VE", "V5", "V8", "V10", "V11", "V12", "V13", "V14", "V16+", "VT", "R2", "R3", "R4", "R5", "R6"],
            "favorability": [0.0, 0.0, 0.0, 0.10, 0.25, 0.50, 1.10, 1.15, 1.20, 1.20, 1.20, 1.15, 0.35, 0.10, 0.03, 0.0],
        }
    if pest == "HELARM":
        return {
            "stages": ["VS", "VE", "VT", "R1", "R2", "R3", "R4", "R5", "R6"],
            "favorability": [0.0, 0.0, 0.0, 0.8, 1.2, 0.35, 0.10, 0.02, 0.0],
        }
    return {"stages": ["VS", "R6"], "favorability": [0.0, 0.0]}

# ----------------------------
# Pest configuration
# ----------------------------
# Notes:
# - risk_index_scale boosts/attenuates the 0–100 risk index for each pest
# - daily/short_agg categories use 0–100 thresholds
# - short_agg_days controls rolling window length for the short-term aggregate
pest_config = {

    # 1) Spodoptera exigua (SPODEX) – early migrant
    "SPODEX": {
        "Global": {
            "common_name": "甜菜夜蛾",
            "scientific_name": "Spodoptera exigua",
            "host_crop": ["maize", "cotton", "vegetables"],
            "version": CONFIG_VERSION,
            "weather_variables": DEFAULT_WEATHER_VARIABLES,

            "T_min": 10.0, "T_opt": 28.0, "T_max": 35.0,
            "degree_day_per_stage": {"egg": 50, "larva_total": 260, "pupa": 100, "adult": 80},
            "degree_day_per_instar": {"L1": 35, "L2": 45, "L3": 55, "L4": 55, "L5": 45, "L6": 25},

            "fecundity": 800, "survival_rate": 0.60,
            "overwinter": False,
            # Calibrated for daily mean wind: field scouting can find early populations under moderate winds.
            "migration": {"enabled": True, "windspeed_threshold": 2.5, "temperature_threshold": 13.0, "sustain_days": 1.0},

            "instar_susceptibility": {"L1": 1.00, "L2": 0.95, "L3": 0.80, "L4": 0.65, "L5": 0.50, "L6": 0.40},
            "control_stage_window": {"preferred": ["L2"], "alternatives": ["L1", "L3"]},

            "recommended_products": [
                "chlorantraniliprole_200SC",
                "emamectin_benzoate_50WG",
                "spinetoram_120SC",
            ],

            "risk_index_scale": 1.05,
            "daily_infection_risk_categories": DEFAULT_DAILY_RISK_CATEGORIES_0_100,
            "short_agg_infection_risk_categories": DEFAULT_SHORT_AGG_RISK_CATEGORIES_0_100,
            "short_agg_days": 4,

            "stage_favorability_curve_iowa": _stage_favorability_curve_for("SPODEX"),
            "stage_correction": _stage_correction_for("SPODEX"),
        }
    },

    # 2) Spodoptera frugiperda (SPOFRU) – later migrant
    "SPOFRU": {
        "Global": {
            "common_name": "草地贪夜蛾",
            "scientific_name": "Spodoptera frugiperda",
            "host_crop": ["maize", "sorghum", "rice"],
            "version": CONFIG_VERSION,
            "weather_variables": DEFAULT_WEATHER_VARIABLES,

            "T_min": 12.0, "T_opt": 26.0, "T_max": 34.0,
            "degree_day_per_stage": {"egg": 45, "larva_total": 285, "pupa": 100, "adult": 60},
            "degree_day_per_instar": {"L1": 35, "L2": 50, "L3": 60, "L4": 60, "L5": 50, "L6": 30},

            "fecundity": 1200, "survival_rate": 0.50,
            "overwinter": False,
            # Slightly stricter migration; arrives later than SPODEX
            "migration": {"enabled": True, "windspeed_threshold": 5.5, "temperature_threshold": 18.0, "sustain_days": 1.5},

            "instar_susceptibility": {"L1": 1.00, "L2": 0.95, "L3": 0.75, "L4": 0.60, "L5": 0.45, "L6": 0.35},
            "control_stage_window": {"preferred": ["L2"], "alternatives": ["L1", "L3"]},

            "recommended_products": [
                "chlorantraniliprole_200SC",
                "emamectin_benzoate_50WG",
                "indoxacarb_150SC",
                "spinetoram_120SC",
            ],

            "risk_index_scale": 1.00,
            "daily_infection_risk_categories": DEFAULT_DAILY_RISK_CATEGORIES_0_100,
            "short_agg_infection_risk_categories": DEFAULT_SHORT_AGG_RISK_CATEGORIES_0_100,
            "short_agg_days": 3,

            "stage_favorability_curve_iowa": _stage_favorability_curve_for("SPOFRU"),
            "stage_correction": _stage_correction_for("SPOFRU"),
        }
    },

    # 3) Ostrinia furnacalis (OSTFUR) – resident overwinter
    "OSTFUR": {
        "Global": {
            "common_name": "玉米螟",
            "scientific_name": "Ostrinia furnacalis",
            "host_crop": ["maize"],
            "version": CONFIG_VERSION,
            "weather_variables": DEFAULT_WEATHER_VARIABLES,

            "T_min": 10.0, "T_opt": 28.0, "T_max": 33.0,
            "degree_day_per_stage": {"egg": 70, "larva_total": 300, "pupa": 120, "adult": 50},
            "degree_day_per_instar": {"L1": 40, "L2": 55, "L3": 60, "L4": 60, "L5": 55, "L6": 30},

            "fecundity": 600, "survival_rate": 0.65,
            "overwinter": True,
            "migration": {"enabled": False},
            # Lower DD threshold to release emergence earlier (June)
            "overwinter_release_dd": 120.0,

            "instar_susceptibility": {"L1": 1.00, "L2": 0.90, "L3": 0.75, "L4": 0.60, "L5": 0.45, "L6": 0.35},
            "control_stage_window": {"preferred": ["L2"], "alternatives": ["L1", "L3"]},

            "recommended_products": [
                "chlorantraniliprole_200SC",
                "emamectin_benzoate_50WG",
                "lambda_cyhalothrin_25EC",
            ],

            "risk_index_scale": 0.95,
            "daily_infection_risk_categories": DEFAULT_DAILY_RISK_CATEGORIES_0_100,
            "short_agg_infection_risk_categories": DEFAULT_SHORT_AGG_RISK_CATEGORIES_0_100,
            "short_agg_days": 5,

            "stage_favorability_curve_iowa": _stage_favorability_curve_for("OSTFUR"),
            "stage_correction": _stage_correction_for("OSTFUR"),
        }
    },

    # 4) Helicoverpa armigera (HELARM) – mixed (overwinter + migration pulses)
    "HELARM": {
        "Global": {
            "common_name": "棉铃虫",
            "scientific_name": "Helicoverpa armigera",
            "host_crop": ["cotton", "maize", "tomato"],
            "version": CONFIG_VERSION,
            "weather_variables": DEFAULT_WEATHER_VARIABLES,

            "T_min": 11.0, "T_opt": 27.0, "T_max": 36.0,
            "degree_day_per_stage": {"egg": 55, "larva_total": 265, "pupa": 110, "adult": 70},
            "degree_day_per_instar": {"L1": 35, "L2": 45, "L3": 55, "L4": 55, "L5": 50, "L6": 25},

            "fecundity": 1000, "survival_rate": 0.55,
            "overwinter": True,
            "migration": {"enabled": True, "windspeed_threshold": 6.5, "temperature_threshold": 17.0, "sustain_days": 3},
            "overwinter_release_dd": 140.0,  # first local emergence

            "instar_susceptibility": {"L1": 1.00, "L2": 0.90, "L3": 0.75, "L4": 0.65, "L5": 0.50, "L6": 0.40},
            "control_stage_window": {"preferred": ["L2"], "alternatives": ["L1", "L3"]},

            "recommended_products": [
                "chlorantraniliprole_200SC",
                "indoxacarb_150SC",
                "spinetoram_120SC",
            ],

            "risk_index_scale": 1.00,
            "daily_infection_risk_categories": DEFAULT_DAILY_RISK_CATEGORIES_0_100,
            "short_agg_infection_risk_categories": DEFAULT_SHORT_AGG_RISK_CATEGORIES_0_100,
            "short_agg_days": 4,

            "stage_favorability_curve_iowa": _stage_favorability_curve_for("HELARM"),
            "stage_correction": _stage_correction_for("HELARM"),
        }
    },
}

# ----------------------------
# Public exports
# ----------------------------
__all__ = [
    "CONFIG_VERSION",
    "REGION",
    "TEMPORAL_RESOLUTION",
    "IOWA_STAGES",
    "VARIETY_SUSC_CORRECTION",
    "DEFAULT_WEATHER_VARIABLES",
    "MAIZE_STAGE_CORRECTION_TEMPLATES",
    "pesticide_params",
    "pest_config",
]
