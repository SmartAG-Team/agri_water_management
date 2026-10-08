# config.py
# 玉米病害参数（华北平原；Iowa 生育期；与 HourlyWeatherEntry 字段一致）
# HourlyWeatherEntry:
#   DateTime: datetime
#   temperature_2m: float
#   relativehumidity_2m: Optional[float]
#   windspeed_10m: Optional[float]
#   precipitation: Optional[float]
#   shortwave_radiation: Optional[float]  # hourly MJ/m2

CONFIG_VERSION = "0.1.0"
REGION = "North_China_Plain"
PHENOLOGY_SYSTEM = "Iowa_VS_to_R6"
TEMPORAL_RESOLUTION = "hourly"

from crops.maize.config import IOWA_Stage_ORDER as IOWA_STAGES

# ---------------------------------------------------------------------
# 叶面湿润（LWD）推断规则（逐小时）
# 判湿： (precipitation > 0) 或 (RH >= rh_wet_threshold 且非干燥条件)
# 判干： (shortwave_radiation >= rad_dry_threshold) 或 (windspeed_10m >= wind_dry_threshold) 或 (RH < rh_dry_threshold)
# 缺测：允许仅用 RH + (风/辐射) 的子集判断
# ---------------------------------------------------------------------
LWD_RULES = {
    "use_precip_as_wet": True,
    "precip_wet_threshold": 0.0,     # mm/h
    "rh_wet_threshold": 90.0,        # %
    "rh_dry_threshold": 80.0,        # %
    "rad_dry_threshold": 1.44,       # hourly MJ/m2, equivalent to about 400 W/m2
    "wind_dry_threshold": 3.5,       # m/s
    "allow_missing_radiation": True,
    "allow_missing_wind": True,
    "daily_W_max": 24,               # h/day
}

# ——风暴期 LWD 覆写（在 storm_active_or_tail = True 时生效）——
LWD_STORM_OVERRIDES = {
    "enabled": True,
    "rh_wet_threshold": 88.0,
    "rh_dry_threshold": 78.0,
    "rad_dry_threshold": 2.16,       # hourly MJ/m2, equivalent to about 600 W/m2
    "wind_dry_threshold": 5.0,
    "allow_wet_carry_over": True,    # 允许湿润跨日延续
    "daily_W_max": 24,
}

# 通用品种抗感修正（保守）
VARIETY_SUSC_CORRECTION = {
    "susceptibility": [1, 5, 9],    # 抗-中-感
    "infection":      [0.85, 1.00, 1.15]
}

# 与 HourlyWeatherEntry 对齐的统一气象字段
DEFAULT_WEATHER_VARIABLES = [
    "temperature_2m", "relative_humidity_2m", "wind_speed_10m", "precipitation", "shortwave_radiation"
]

# 风险分数说明
RISK_SCORE_DOC = "daily_score = wet_hours_eff × temp_fitness × stage_weight × optional_modifiers"

from core.utils import get_disease_insect_weed_status_and_code
disease_status_code=get_disease_insect_weed_status_and_code(category='stress_code')
DEFAULT_DAILY_RISK_CATEGORIES_0_100 = {
        "0-6":   disease_status_code[1],
        "6-12":  disease_status_code[2],
        "12-999":disease_status_code[3]
}
DEFAULT_SHORT_AGG_RISK_CATEGORIES_0_100 = {
        "0-6":   disease_status_code[1],
        "6-12":  disease_status_code[2],
        "12-999":disease_status_code[3]
}

# ----------------------------
# 病害配置
# ----------------------------
disease_config = {

    # 1) 玉米大斑病（Exserohilum turcicum）—— SETOTU
    "SETOTU": {
        "Global": {
            "pathogen": "Exserohilum turcicum",
            "eppo_code": "SETOTU",
            "host_name": "Zea mays",
            "plant_organ_affected": ["leaf"],
            "validation": "none",
            "version": CONFIG_VERSION,
            "development_status": "in_progress",
            "weather_temporal_resolution": TEMPORAL_RESOLUTION,
            "weather_variable_list": DEFAULT_WEATHER_VARIABLES,

            "crop_growth_stage_correction_iowa": {
                "stages": IOWA_STAGES,
                # V6↑，VT~R2 高，R5 后低
                "infection": [
                    0.0,0.0,0.0,0.0,
                    0.2,0.2,0.4,0.6,0.7,0.8,0.9,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,
                    1.0,1.0,0.9,0.8,0.6,0.2,0.0
                ],
            },
            "growth_stage_limits_iowa": {"start_stage": "V6", "end_stage": "R5"},
            "variety_susceptibility_correction": VARIETY_SUSC_CORRECTION,

            "T_min": 15.0, "T_opt": 20.0, "T_max": 30.0,
            "rH_min": 90.0,
            "W_min": 6, "W_max": 24,

            "use_rain_for_release": False,

            "inter_cropping_period": [11,12,1,2,3],
            "maturation_period":    [6,7,8,9,10],

            "cold_day_correction": {
                "enabled": True,
                "minimal_temp_threshold": -1.0,
                "number_of_cold_days":      [0,10,20,30,50,100],
                "inoc_correction_cold_days":[1.0,1.0,0.9,0.85,0.8,0.7],
            },

            "daily_infection_risk_categories": DEFAULT_DAILY_RISK_CATEGORIES_0_100,
            "short_agg_infection_risk_categories": DEFAULT_SHORT_AGG_RISK_CATEGORIES_0_100,
            "short_agg_days": 5,
            "duration_of_latent_period": 10
        }
    },

    # 2) 玉米小斑病（Bipolaris maydis）—— EPPO 码请上线前复核
    "COCHHE": {
        "Global": {
            "pathogen": "Bipolaris maydis",
            "eppo_code": "COCHHE",  # TODO: 上线前在 EPPO DB 复核
            "host_name": "Zea mays",
            "plant_organ_affected": ["leaf"],
            "validation": "none",
            "version": CONFIG_VERSION,
            "development_status": "in_progress",
            "weather_temporal_resolution": TEMPORAL_RESOLUTION,
            "weather_variable_list": DEFAULT_WEATHER_VARIABLES,

            "crop_growth_stage_correction_iowa": {
                "stages": IOWA_STAGES,
                "infection": [
                    0.0,0.0,0.0,0.1,
                    0.4,0.6,0.7,0.9,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,
                    1.0,1.0,0.9,0.8,0.6,0.2,0.0
                ],
            },
            "growth_stage_limits_iowa": {"start_stage": "V3", "end_stage": "R5"},
            "variety_susceptibility_correction": VARIETY_SUSC_CORRECTION,

            "T_min": 15.0, "T_opt": 28.0, "T_max": 35.0,
            "rH_min": 90.0,
            "W_min": 4, "W_max": 24,

            "use_rain_for_release": False,

            "inter_cropping_period": [11,12,1,2,3],
            "maturation_period":    [6,7,8,9,10],

            "cold_day_correction": {
                "enabled": True,
                "minimal_temp_threshold": -1.0,
                "number_of_cold_days":      [0,10,20,30,50,100],
                "inoc_correction_cold_days":[1.0,1.0,0.9,0.85,0.8,0.7],
            },

            "daily_infection_risk_categories": DEFAULT_DAILY_RISK_CATEGORIES_0_100,
            "short_agg_infection_risk_categories": DEFAULT_SHORT_AGG_RISK_CATEGORIES_0_100,
            "short_agg_days": 5,
            "duration_of_latent_period": 5
        }
    },

    # 3) 玉米普通锈（Puccinia sorghi）—— PUCCSO
    "PUCCSO": {
        "Global": {
            "pathogen": "Puccinia sorghi",
            "eppo_code": "PUCCSO",
            "host_name": "Zea mays",
            "plant_organ_affected": ["leaf", "leaf_sheath"],
            "validation": "none",
            "version": CONFIG_VERSION,
            "development_status": "in_progress",
            "weather_temporal_resolution": TEMPORAL_RESOLUTION,
            "weather_variable_list": DEFAULT_WEATHER_VARIABLES,

            "crop_growth_stage_correction_iowa": {
                "stages": IOWA_STAGES,
                # 早期低；V6~VT 升；R5 后趋零
                "infection": [
                    0.0,0.0,0.0,0.0,
                    0.05,0.05,0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.85,0.9,0.9,0.9,0.8,
                    0.9,0.9,0.7,0.5,0.3,0.1,0.0
                ],
            },
            "growth_stage_limits_iowa": {"start_stage": "V5", "end_stage": "R5"},
            "variety_susceptibility_correction": VARIETY_SUSC_CORRECTION,

            "T_min": 10.0, "T_opt": 20.0, "T_max": 28.0,
            "rH_min": 95.0,
            "W_min": 4, "W_max": 24,

            "use_rain_for_release": False,

            "inter_cropping_period": [11,12,1,2,3],
            "maturation_period":    [6,7,8,9,10],

            "cold_day_correction": {
                "enabled": True,
                "minimal_temp_threshold": -1.0,
                "number_of_cold_days":      [0,10,20,30,50,100],
                "inoc_correction_cold_days":[1.0,0.95,0.9,0.85,0.8,0.7],
            },

            "daily_infection_risk_categories": DEFAULT_DAILY_RISK_CATEGORIES_0_100,
            "short_agg_infection_risk_categories": DEFAULT_SHORT_AGG_RISK_CATEGORIES_0_100,
            "short_agg_days": 5,
            "duration_of_latent_period": 7
        }
    },

    # 4) 玉米南方锈（Puccinia polysora）—— PUCCPY（含台风/风暴输送）
    "PUCCPY": {
        "Global": {
            "pathogen": "Puccinia polysora",
            "eppo_code": "PUCCPY",
            "host_name": "Zea mays",
            "plant_organ_affected": ["leaf", "leaf_sheath"],
            "validation": "none",
            "version": CONFIG_VERSION,
            "development_status": "in_progress",
            "weather_temporal_resolution": TEMPORAL_RESOLUTION,
            "weather_variable_list": DEFAULT_WEATHER_VARIABLES,

            "crop_growth_stage_correction_iowa": {
                "stages": IOWA_STAGES,
                # 晚发；VT~R3 高，R5 后快速下降
                "infection": [
                    0.0,0.0,0.0,0.0, 0.0,0.0,0.0,0.05,0.05,0.05,0.1,0.1,0.15,0.2,0.3,0.4,0.5,0.6,0.6,
                    0.7,1.0,1.0,0.9,0.6,0.2,0.0
                ],
            },
            "growth_stage_limits_iowa": {"start_stage": "VT", "end_stage": "R5"},
            "variety_susceptibility_correction": VARIETY_SUSC_CORRECTION,

            "T_min": 15.0, "T_opt": 28.0, "T_max": 35.0,
            "rH_min": 95.0,
            "W_min": 4, "W_max": 24,

            "use_rain_for_release": False,

            "inter_cropping_period": [11,12,1,2,3],
            "maturation_period":    [6,7,8,9,10],

            # ——关闭越冬/冷日修正（华北多为远距离输送）——
            "cold_day_correction": {"enabled": False},

            # ——台风/风暴期外来孢子输送因子——
            "storm_transport": {
                "enabled": True,
                "trigger": {                 # 任一满足即判为 storm_active
                    "windspeed_10m_threshold": 10.0,  # m/s
                    "precip_rate_threshold":   2.0,   # mm/h
                    "duration_hours_min":      6
                },
                "influx_mapping": {
                    "windspeed_scale_max": 20.0,      # m/s → influx≈1
                    "precip_24h_scale_max": 150.0     # mm/24h → influx≈1
                },
                "influx_multiplier": 3.0,             # 风暴期额外放大倍数系数
                "post_storm_decay": {
                    "enabled": True,
                    "half_life_days": 2.0,            # 尾流半衰期
                    "max_tail_days": 5
                },
                "external_alert_weight": 0.7          # 若有外部台风告警指数（0–1）可融合
            },

            # ——风暴期内的病程覆写——
            "storm_period_overrides": {
                "latent_period_days": 5,              # 风暴/尾流窗口内潜育期缩短
                "stage_weight_scale": 1.1,            # 阶段权重整体×1.1
                "temp_fitness_high_humidity_bonus": {
                    "enabled": True,
                    "rh_threshold": 95.0,
                    "upper_t_opt_extension": 2.0      # 高湿下 T_opt 上沿 +2℃
                }
            },

            # 风险分级（常规 & 风暴期左移）
            "daily_infection_risk_categories": DEFAULT_DAILY_RISK_CATEGORIES_0_100,
            "short_agg_infection_risk_categories": DEFAULT_SHORT_AGG_RISK_CATEGORIES_0_100,
            "storm_risk_categories": {  # storm_active_or_tail=True 时使用
                "daily": {
                    "0-3":   "UNFAVORABLE",
                    "3-6":   "FAVORABLE",
                    "6-999": "OPTIMAL"
                },
                "short_agg": {
                    "0-3":   "UNFAVORABLE",
                    "3-6":   "FAVORABLE",
                    "6-999": "OPTIMAL"
                }
            },

            "short_agg_days": 5,
            "duration_of_latent_period": 6          # 常规；风暴期覆写为 5
        }
    },

    # 5) 茎腐病复合（Stenocarpella maydis & Fusarium spp.）—— DIPDMA（应激驱动）
    "DIPDMA": {
        "Global": {
            "pathogen": "Stenocarpella maydis (and Fusarium spp.)",
            "eppo_code": "DIPDMA",
            "host_name": "Zea mays",
            "plant_organ_affected": ["stem"],
            "validation": "none",
            "version": CONFIG_VERSION,
            "development_status": "in_progress",
            "weather_temporal_resolution": TEMPORAL_RESOLUTION,
            "weather_variable_list": DEFAULT_WEATHER_VARIABLES,

            "crop_growth_stage_correction_iowa": {
                "stages": IOWA_STAGES,
                "infection": [
                    0.0,0.0,0.0,0.0, 0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,
                    0.1,0.3,0.6,0.9,1.0,1.0,0.2
                ],
            },
            "growth_stage_limits_iowa": {"start_stage": "R1", "end_stage": "R6"},
            "variety_susceptibility_correction": VARIETY_SUSC_CORRECTION,

            "T_min": 10.0, "T_opt": 28.0, "T_max": 35.0,
            "use_lwd": False,  # 弱化叶面湿润作用

            # ——应激驱动阈值（实现层组合为日分数加权）——
            "stress_drivers": {
                "heat_degree_hour_base": 30.0,     # ℃，temperature_2m - base (>0计入)
                "heat_degree_hour_scale": 1.0,     # 线性比例

                "dry_spell_days_window": 10,       # 近10天
                "dry_day_precip_threshold": 1.0,   # mm/d
                "dry_spell_days_threshold": 5,     # 干旱日阈值

                "high_rad_threshold": 1.80,        # hourly MJ/m2, equivalent to about 500 W/m2
                "high_rad_hours_threshold": 6,     # h/d

                "rainy_days_window": 10,
                "rainy_day_threshold": 5.0,        # mm/d
                "rainy_days_high_freq": 4
            },

            "daily_infection_risk_categories": DEFAULT_DAILY_RISK_CATEGORIES_0_100,
            "short_agg_infection_risk_categories": DEFAULT_SHORT_AGG_RISK_CATEGORIES_0_100,
            "short_agg_days": 5,
            "duration_of_latent_period": 14
        }
    },
}

# 统一导出
__all__ = [
    "CONFIG_VERSION",
    "REGION",
    "PHENOLOGY_SYSTEM",
    "TEMPORAL_RESOLUTION",
    "LWD_RULES",
    "LWD_STORM_OVERRIDES",
    "IOWA_STAGES",
    "VARIETY_SUSC_CORRECTION",
    "RISK_SCORE_DOC",
    "disease_config",
]
