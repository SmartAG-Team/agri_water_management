# crops/maize/nutrition/config.py
from __future__ import annotations

"""
Maize nutrition model configuration (North China Plain / NCP)
- Target-specific params for N, P, K and key micros (S, Zn, B).
- Daily stress categories on a 0–100 index (stress = demand vs. availability gap).
- Stage multipliers and uptake shares by broad growth bands.
- Simple base & in-season recommendation heuristics (per yield target).
- FERTILIZER_PRODUCTS: product master-data for dose→nutrient conversion.
"""

# ----------------------------
# Global metadata
# ----------------------------
CONFIG_VERSION = "0.3.1-ncp"
REGION = "North_China_Plain"
TEMPORAL_RESOLUTION = "daily"

# Iowa-like stage bands we reference in corrections / uptake shares
STAGE_BANDS = ["VE_V4", "V5_V8", "V9_V12", "V13_VT", "R1_R3", "R4_R6"]

from core.utils import get_nutrition_status_and_code
stress_code=get_nutrition_status_and_code(category='stress_code')
code_stress=get_nutrition_status_and_code(category='code_stress')
# ----------------------------
# Stress/risk categories (0–100)
# ----------------------------
DEFAULT_DAILY_STRESS_CATEGORIES_0_100 = {
    "0-30":   stress_code[1],
    "30-60":  stress_code[2],
    "60-999": stress_code[3],
}
DEFAULT_SHORT_AGG_STRESS_CATEGORIES_0_100 = {
    "0-30":   stress_code[1],
    "30-60":  stress_code[2],
    "60-999": stress_code[3],
}
DEFAULT_DAY_WITH_FERTILIZED_STATUS_AFTER_FERTILIZATION = 7
# ----------------------------
# Stage correction templates (multiplier to demand or priority)
# ----------------------------
MAIZE_STAGE_CORRECTION_TEMPLATES = {
    "VE_V4":  0.90,
    "V5_V8":  1.00,
    "V9_V12": 1.05,
    "V13_VT": 1.10,
    "R1_R3":  1.10,
    "R4_R6":  0.95,
}

def _stage_correction_for(target: str) -> dict:
    base = MAIZE_STAGE_CORRECTION_TEMPLATES.copy()
    t = (target or "").upper()
    if t == "N":
        base.update({"V9_V12": 1.10, "V13_VT": 1.15, "R1_R3": 1.15})
    elif t in ("P", "P2O5"):
        base.update({"VE_V4": 1.10, "V5_V8": 1.05})
    elif t in ("K", "K2O"):
        base.update({"V9_V12": 1.10, "R1_R3": 1.15})
    elif t in ("S",):
        base.update({"V5_V8": 1.05, "V9_V12": 1.05})
    elif t in ("ZN",):
        base.update({"VE_V4": 1.10, "V5_V8": 1.05})
    elif t in ("B",):
        base.update({"VE_V4": 1.10})
    return base

# ----------------------------
# Default uptake shares by stage band (sum≈1.0)
# ----------------------------
DEFAULT_UPTAKE_SHARE_BY_BAND = {
    #   VE_V4  V5_V8  V9_V12  V13_VT  R1_R3  R4_R6
    "N":   [0.06, 0.18, 0.26, 0.22, 0.20, 0.08],
    "P2O5":[0.12, 0.24, 0.24, 0.18, 0.14, 0.08],
    "K2O": [0.08, 0.20, 0.26, 0.22, 0.18, 0.06],
    "S":   [0.08, 0.22, 0.26, 0.22, 0.16, 0.06],
    "Zn":  [0.16, 0.28, 0.22, 0.16, 0.12, 0.06],
    "B":   [0.18, 0.28, 0.22, 0.16, 0.10, 0.06],
}

# ----------------------------
# Soil → plant availability helpers (defaults)
# ----------------------------
DEFAULT_SOIL_LAYER = {
    "sampling_depth_cm": 0,       # 负数表示“0~abs(depth)”（rb 中 -20 = 0–20 cm）
    "default_depth_cm": 20,
    "bulk_density_g_cm3": 1.30,
}

SOIL_CRITICAL_LEVELS = {
    "olsen_p_mg_kg":          12,
    "exchangeable_k_mg_kg":  150,
    "zn_mg_kg":               1.0,
    "s_mg_kg":               10.0,
    "mineral_N_mg_kg":       25.0,  # nitrate+ammonium
}

REGIONAL_SOIL_TEST_DEFAULTS = {
    "zhengding_olsen_p_mg_kg": 27.5,
    "shijiazhuang_olsen_p_mg_kg": 28.54,
    "north_china_plain_available_k_mg_kg": 176.2,
}

# ----------------------------
# Yield → seasonal requirement heuristics (per t grain)
# ----------------------------
YIELD_REQ_COEFF = {
    "N":    20.0,   # kg N / t
    "P2O5": 10.0,   # kg P2O5 / t
    "K2O":  22.0,   # kg K2O / t
    "S":     4.0,   # kg S / t
    "Zn_g":  30.0,  # g Zn / t
    "B_g":   12.0,  # g B / t（可按需调整）
}
DEFAULT_WEATHER_VARIABLES_DAILY = [
    "temperature_2m_min",
    "temperature_2m_max",
    "precipitation_sum",
    "shortwave_radiation_sum",
    "windspeed_10m_min",
    "windspeed_10m_max",
    "windspeed_10m_mean",
]
DEFAULT_WEATHER_VARIABLES = DEFAULT_WEATHER_VARIABLES_DAILY
# ----------------------------
# In-season recommendation policy
# ----------------------------
INSEASON_POLICY = {
    "max_apps": 3,
    "allow_leaf_spray": True,
    "foliar_spray": {
        "stage_min": "VE",
        "stage_max": "R3",
        "default_micro_stage_min": "V9",
        "default_micro_stage_max": "VT",
        "default_missing_micronutrients": True,
        "min_interval_days": 10,
        "release_days": 1,
        "default_micros_g_ha": {"Zn": 500.0, "B": 250.0},
        "rescue_n_kg_ha": 8.0,
        "rescue_k2o_kg_ha": 6.0,
        "rescue_stage_min": "V5",
        "rescue_stage_max": "R3",
        "low_early_k2o_kg_ha": 45.0,
        "early_k_application_days": 35,
        "max_default_micro_apps": 1,
        "max_rescue_apps": 1,
    },
    "split_ratio": {
        "N":    [0.45, 0.35, 0.20],
        "P2O5": [0.50, 0.30, 0.20],
        "K2O":  [0.40, 0.40, 0.20],
        "S":    [0.50, 0.30, 0.20],
        "Zn":   [0.60, 0.30, 0.10],
        "B":    [0.60, 0.30, 0.10],
    },
    "timing_windows": {
        "N":    ["V5_V8", "V9_V12", "V13_VT"],
        "P2O5": ["VE_V4", "V5_V8", "V9_V12"],
        "K2O":  ["V5_V8", "V9_V12", "R1_R3"],
        "S":    ["V5_V8", "V9_V12", "R1_R3"],
        "Zn":   ["VE_V4", "V5_V8", "V13_VT"],   # 叶喷常见窗口
        "B":    ["VE_V4", "V5_V8", "R1_R3"],    # 叶喷/土施均可
    },
    "leaf_spray_targets": ["Zn", "B"],
}

# ----------------------------
# Target-specific configuration
# ----------------------------
nutrition_config = {
    "N": {
        "unit": "kg_ha",
        "soil_availability_keys": ["nitrate_mg_kg", "ammonium_mg_kg"],
        "uptake_efficiency_default": 0.55,
        'fertilized_status_days_after_fertilization': DEFAULT_DAY_WITH_FERTILIZED_STATUS_AFTER_FERTILIZATION,
        "daily_category": DEFAULT_DAILY_STRESS_CATEGORIES_0_100,
        "short_agg_category": DEFAULT_SHORT_AGG_STRESS_CATEGORIES_0_100,
        "short_agg_days": 5,
        "stage_correction": _stage_correction_for("N"),
        "uptake_share_by_band": DEFAULT_UPTAKE_SHARE_BY_BAND["N"],
        "yield_coeff": YIELD_REQ_COEFF["N"],
        "policy": INSEASON_POLICY,
        # --- new soil supply defaults ---
        "soil_supply": { "base_kg_ha_day": 1.8, "temp_ref": 20.0, "Q10": 2.0 },
    },
    "P2O5": {
        "unit": "kg_ha_as_P2O5",
        "soil_availability_keys": ["olsen_p_mg_kg"],
        "uptake_efficiency_default": 0.35,
        'fertilized_status_days_after_fertilization': DEFAULT_DAY_WITH_FERTILIZED_STATUS_AFTER_FERTILIZATION,
        "daily_category": DEFAULT_DAILY_STRESS_CATEGORIES_0_100,
        "short_agg_category": DEFAULT_SHORT_AGG_STRESS_CATEGORIES_0_100,
        "short_agg_days": 7,
        "stage_correction": _stage_correction_for("P2O5"),
        "uptake_share_by_band": DEFAULT_UPTAKE_SHARE_BY_BAND["P2O5"],
        "yield_coeff": YIELD_REQ_COEFF["P2O5"],
        "policy": INSEASON_POLICY,
        "soil_supply": { "base_kg_ha_day": 0.40 },
        "soil_applied_fertilizer_model": "residual_pool",
        "residual_pool_loss": {
            "runoff_weight": 0.10,
            "hydraulic_loss_scale_mm": 300.0,
            "max_daily_loss_fraction": 0.03,
        },
    },
    "K2O": {
        "unit": "kg_ha_as_K2O",
        "soil_availability_keys": ["exchangeable_k_mg_kg"],
        "uptake_efficiency_default": 0.50,
        'fertilized_status_days_after_fertilization': DEFAULT_DAY_WITH_FERTILIZED_STATUS_AFTER_FERTILIZATION,
        "daily_category": DEFAULT_DAILY_STRESS_CATEGORIES_0_100,
        "short_agg_category": DEFAULT_SHORT_AGG_STRESS_CATEGORIES_0_100,
        "short_agg_days": 5,
        "stage_correction": _stage_correction_for("K2O"),
        "uptake_share_by_band": DEFAULT_UPTAKE_SHARE_BY_BAND["K2O"],
        "yield_coeff": YIELD_REQ_COEFF["K2O"],
        "policy": INSEASON_POLICY,
        "soil_supply": { "base_kg_ha_day": 0.80 },
        "soil_applied_fertilizer_model": "residual_pool",
        "residual_pool_loss": {
            "runoff_weight": 0.10,
            "hydraulic_loss_scale_mm": 220.0,
            "max_daily_loss_fraction": 0.12,
        },
    },
    "S": {
        "unit": "kg_ha",
        "soil_availability_keys": ["s_mg_kg"],
        "uptake_efficiency_default": 0.50,
        'fertilized_status_days_after_fertilization': DEFAULT_DAY_WITH_FERTILIZED_STATUS_AFTER_FERTILIZATION,
        "daily_category": DEFAULT_DAILY_STRESS_CATEGORIES_0_100,
        "short_agg_category": DEFAULT_SHORT_AGG_STRESS_CATEGORIES_0_100,
        "short_agg_days": 7,
        "stage_correction": _stage_correction_for("S"),
        "uptake_share_by_band": DEFAULT_UPTAKE_SHARE_BY_BAND["S"],
        "yield_coeff": YIELD_REQ_COEFF["S"],
        "policy": INSEASON_POLICY,
        "soil_supply": { "base_kg_ha_day": 0.20 },
    },
    "Zn": {
        "unit": "g_ha",  # 注意单位
        "soil_availability_keys": ["zn_mg_kg"],
        "uptake_efficiency_default": 0.15,
        'fertilized_status_days_after_fertilization': DEFAULT_DAY_WITH_FERTILIZED_STATUS_AFTER_FERTILIZATION,
        "daily_category": DEFAULT_DAILY_STRESS_CATEGORIES_0_100,
        "short_agg_category": DEFAULT_SHORT_AGG_STRESS_CATEGORIES_0_100,
        "short_agg_days": 10,
        "stage_correction": _stage_correction_for("Zn"),
        "uptake_share_by_band": DEFAULT_UPTAKE_SHARE_BY_BAND["Zn"],
        "yield_coeff_g_per_t": YIELD_REQ_COEFF["Zn_g"],
        "policy": INSEASON_POLICY,
        "soil_supply": { "base_g_ha_day": 8.0 },
    },
    "B": {
        "unit": "g_ha",
        "soil_availability_keys": ["b_mg_kg"],
        "uptake_efficiency_default": 0.10,
        'fertilized_status_days_after_fertilization': DEFAULT_DAY_WITH_FERTILIZED_STATUS_AFTER_FERTILIZATION,
        "daily_category": DEFAULT_DAILY_STRESS_CATEGORIES_0_100,
        "short_agg_category": DEFAULT_SHORT_AGG_STRESS_CATEGORIES_0_100,
        "short_agg_days": 10,
        "stage_correction": _stage_correction_for("B"),
        "uptake_share_by_band": DEFAULT_UPTAKE_SHARE_BY_BAND["B"],
        "yield_coeff_g_per_t": YIELD_REQ_COEFF["B_g"],
        "policy": INSEASON_POLICY,
        "soil_supply": { "base_g_ha_day": 4.0 },
    },
}

# ----------------------------
# Fertilizer master-data
# 说明：
# - nutrients 字段给出各养分氧化物/元素的质量分数（按产品净重）。
# - rate_unit: 默认 kg_ha（叶喷产品可用 g_ha）。
# - leaf_spray: bool 标注是否常用于叶喷（便于生成叶面推荐）。
# ----------------------------
FERTILIZER_PRODUCTS = {
    # —— 含氮 —— #
    "urea_46N": {
        "name": "尿素 46-0-0",
        "form": "granular",
        "rate_unit": "kg_ha",
        "nutrients": {"N": 0.46},
        "typical_base_rate_kg_ha": [80, 200],
        "typical_topdress_rate_kg_ha": [30, 150],
        "notes": "常规氮肥，易挥发，表施建议覆土/雨前施用。",
    },
    "ammonium_sulfate_21N_24S": {
        "name": "硫酸铵 21-0-0+24S",
        "form": "granular",
        "rate_unit": "kg_ha",
        "nutrients": {"N": 0.21, "S": 0.24},
        "typical_base_rate_kg_ha": [60, 150],
        "typical_topdress_rate_kg_ha": [30, 120],
        "notes": "供氮并补硫，适合低硫土壤。",
    },

    # —— 含磷 —— #
    "DAP_18_46_0": {
        "name": "磷酸二铵 18-46-0",
        "form": "granular",
        "rate_unit": "kg_ha",
        "nutrients": {"N": 0.18, "P2O5": 0.46},
        "typical_base_rate_kg_ha": [80, 200],
        "notes": "底肥常用，注意与碱性土壤中锌拮抗。",
    },
    "MAP_11_52_0": {
        "name": "磷酸一铵 11-52-0",
        "form": "granular",
        "rate_unit": "kg_ha",
        "nutrients": {"N": 0.11, "P2O5": 0.52},
        "typical_base_rate_kg_ha": [60, 180],
        "notes": "较 DAP 更不易挥发，适合滴灌/条施。",
    },

    # —— 含钾 —— #
    "MOP_0_0_60": {
        "name": "氯化钾 0-0-60",
        "form": "granular",
        "rate_unit": "kg_ha",
        "nutrients": {"K2O": 0.60},
        "typical_base_rate_kg_ha": [80, 220],
        "notes": "对氯敏感作物谨慎；玉米一般可用。",
    },
    "SOP_0_0_50": {
        "name": "硫酸钾 0-0-50",
        "form": "granular",
        "rate_unit": "kg_ha",
        "nutrients": {"K2O": 0.50, "S": 0.18},
        "typical_base_rate_kg_ha": [80, 220],
        "notes": "供钾并补硫，适于盐害风险地块。",
    },

    # —— 微量 —— #
    "zinc_sulfate_mono_33Zn": {
        "name": "硫酸锌（单水）≈33% Zn",
        "form": "granular",
        "rate_unit": "kg_ha",
        "nutrients": {"Zn": 0.33},   # 元素锌质量分数
        "typical_soil_rate_kg_ha": [10, 30],
        "leaf_spray": False,
        "notes": "土壤施用改良缺锌土；与 P 过量会拮抗。",
    },
    "zinc_sulfate_hedpta_12Zn_foliar": {
        "name": "螯合锌液剂 ≈12% Zn（叶面）",
        "form": "liquid",
        "rate_unit": "g_ha",          # 叶面按 g Zn/ha 口径更直观
        "nutrients": {"Zn": 0.12},    # 若按制剂量则需同时给出制剂→Zn 的浓度
        "typical_leaf_rate_g_ha": [300, 800],  # 供参考（以元素 Zn 计）
        "leaf_spray": True,
        "notes": "苗期/拔节期/抽雄前叶喷 1–2 次。",
    },
    "borax_11B": {
        "name": "硼砂 ≈11% B",
        "form": "granular",
        "rate_unit": "kg_ha",
        "nutrients": {"B": 0.11},
        "typical_soil_rate_kg_ha": [8, 15],
        "leaf_spray": False,
    },
    "boric_acid_17B_foliar": {
        "name": "硼酸 ≈17% B（叶面）",
        "form": "powder",
        "rate_unit": "g_ha",
        "nutrients": {"B": 0.17},
        "typical_leaf_rate_g_ha": [200, 400],
        "leaf_spray": True,
    },
}

# ----------------------------
# Public exports
# ----------------------------
__all__ = [
    "CONFIG_VERSION",
    "REGION",
    "TEMPORAL_RESOLUTION",
    "STAGE_BANDS",
    "DEFAULT_DAILY_STRESS_CATEGORIES_0_100",
    "DEFAULT_SHORT_AGG_STRESS_CATEGORIES_0_100",
    "MAIZE_STAGE_CORRECTION_TEMPLATES",
    "DEFAULT_UPTAKE_SHARE_BY_BAND",
    "DEFAULT_SOIL_LAYER",
    "SOIL_CRITICAL_LEVELS",
    "REGIONAL_SOIL_TEST_DEFAULTS",
    "YIELD_REQ_COEFF",
    "INSEASON_POLICY",
    "nutrition_config",
    "FERTILIZER_PRODUCTS",
    "DEFAULT_WEATHER_VARIABLES",
]
