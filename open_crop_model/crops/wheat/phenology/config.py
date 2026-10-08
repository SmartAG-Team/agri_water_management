from typing import Any, Dict, List


WHEAT_BBCH_ORDER: List[int] = [
    0,
    9,
    10,
    11,
    12,
    13,
    21,
    22,
    23,
    24,
    25,
    31,
    32,
    33,
    34,
    37,
    39,
    43,
    45,
    47,
    49,
    51,
    55,
    61,
    65,
    69,
    71,
    73,
    75,
    77,
    83,
    85,
    87,
    89,
    92,
]

WHEAT_BBCH_INDEX: Dict[int, int] = {stage: idx for idx, stage in enumerate(WHEAT_BBCH_ORDER)}

WHEAT_STAGE_LABELS: Dict[int, str] = {
    0: "pre_emergence",
    9: "emergence",
    10: "first_leaf",
    11: "one_leaf",
    12: "two_leaves",
    13: "three_leaves",
    21: "begin_tillering",
    22: "two_tillers",
    23: "three_tillers",
    24: "four_tillers",
    25: "five_tillers",
    31: "first_node",
    32: "second_node",
    33: "third_node",
    34: "fourth_node",
    37: "flag_leaf_just_visible",
    39: "flag_leaf_fully_unrolled",
    43: "booting_early",
    45: "booting_mid",
    47: "flag_leaf_sheath_opening",
    49: "awns_visible",
    51: "heading_begin",
    55: "heading_mid",
    61: "flowering_begin",
    65: "flowering_full",
    69: "flowering_end",
    71: "grain_water_ripe",
    73: "grain_early_milk",
    75: "grain_mid_milk",
    77: "grain_late_milk",
    83: "grain_early_dough",
    85: "grain_soft_dough",
    87: "grain_hard_dough",
    89: "full_ripeness",
    92: "overripe",
}

WHEAT_STAGE_CODE_BY_LABEL: Dict[str, int] = {label: code for code, label in WHEAT_STAGE_LABELS.items()}
WHEAT_STAGE_CODE_BY_LABEL.update(
    {
        "tillering": 21,
        "jointing": 31,
        "booting": 43,
        "heading": 51,
        "flowering": 61,
        "heading_flowering": 51,
        "grain_filling": 71,
        "maturity": 89,
    }
)


def parse_wheat_bbch_stage(raw: Any) -> int:
    if raw is None:
        raise ValueError("growth_stage entry missing required Stage field")
    text = str(raw).strip()
    if not text:
        raise ValueError("growth_stage entry has empty Stage field")
    try:
        return int(float(text))
    except ValueError:
        normalized = text.lower().replace(" ", "_").replace("-", "_")
        if normalized in WHEAT_STAGE_CODE_BY_LABEL:
            return WHEAT_STAGE_CODE_BY_LABEL[normalized]
        raise ValueError(f"unsupported wheat growth stage: {raw!r}") from None


_MIDDLE_THRESHOLDS: Dict[int, float] = {
    0: 0.0,
    9: 48.0,
    10: 61.0,
    11: 78.0,
    12: 99.0,
    13: 122.0,
    21: 150.0,
    22: 182.0,
    23: 214.0,
    24: 246.0,
    25: 278.0,
    31: 441.0,
    32: 499.0,
    33: 557.0,
    34: 615.0,
    37: 673.0,
    39: 719.0,
    43: 766.0,
    45: 800.0,
    47: 835.0,
    49: 870.0,
    51: 905.0,
    55: 951.0,
    61: 992.0,
    65: 1032.0,
    69: 1067.0,
    71: 1119.0,
    73: 1172.0,
    75: 1218.0,
    77: 1264.0,
    83: 1328.0,
    85: 1375.0,
    87: 1421.0,
    89: 1462.0,
    92: 1508.0,
}


def _scale_thresholds(base: Dict[int, float], factor: float) -> Dict[int, float]:
    scaled: Dict[int, float] = {}
    for stage, value in base.items():
        if stage == 0:
            scaled[stage] = 0.0
        else:
            scaled[stage] = round(value * factor, 1)
    return scaled


phen_config = {
    "WHEAT_CARDINAL_TEMPERATURES": {
        "base_temperature": 0.0,
        "optimal_temperature": 22.0,
        "maximum_temperature": 35.0,
    },
    "WHEAT_PHOTOPERIOD": {
        "critical_daylength_hours": 8.0,
        "optimal_daylength_hours": 14.0,
        "minimum_factor": 0.35,
    },
    "WHEAT_VERNALIZATION": {
        "minimum_temperature": -4.0,
        "optimal_low_temperature": 3.0,
        "optimal_high_temperature": 10.0,
        "maximum_temperature": 18.0,
        "minimum_factor": 0.2,
        "devernalization_threshold": 30.0,
        "devernalization_rate": 0.5,
    },
    "WHEAT_STAGE_THRESHOLDS": {
        "very_early": _scale_thresholds(_MIDDLE_THRESHOLDS, 0.90),
        "early": _scale_thresholds(_MIDDLE_THRESHOLDS, 0.95),
        "middle": _MIDDLE_THRESHOLDS,
        "late": _scale_thresholds(_MIDDLE_THRESHOLDS, 1.06),
        "very_late": _scale_thresholds(_MIDDLE_THRESHOLDS, 1.12),
    },
    "WHEAT_CULTIVAR_GROUPS": {
        "very_early": {
            "required_vernalization_days": 35.0,
            "photoperiod_sensitivity": 0.70,
            "latitude": 35.0,
            "sowing_window": ("09-25", "10-10"),
            "harvest_window": ("05-25", "06-05"),
        },
        "early": {
            "required_vernalization_days": 40.0,
            "photoperiod_sensitivity": 0.78,
            "latitude": 35.0,
            "sowing_window": ("09-28", "10-12"),
            "harvest_window": ("05-28", "06-08"),
        },
        "middle": {
            "required_vernalization_days": 45.0,
            "photoperiod_sensitivity": 0.85,
            "latitude": 35.0,
            "sowing_window": ("10-01", "10-15"),
            "harvest_window": ("05-31", "06-10"),
        },
        "late": {
            "required_vernalization_days": 52.0,
            "photoperiod_sensitivity": 0.92,
            "latitude": 35.0,
            "sowing_window": ("10-03", "10-18"),
            "harvest_window": ("06-03", "06-13"),
        },
        "very_late": {
            "required_vernalization_days": 58.0,
            "photoperiod_sensitivity": 1.00,
            "latitude": 35.0,
            "sowing_window": ("10-05", "10-20"),
            "harvest_window": ("06-05", "06-15"),
        },
    },
    "NORTH_CHINA_PLAIN_CONTEXT": {
        "region": "North China Plain",
        "crop_type": "winter wheat",
        "typical_sowing_window": ("10-01", "10-20"),
        "typical_harvest_window": ("06-01", "06-15"),
        "notes": [
            "Model targets winter wheat grown under autumn sowing, winter dormancy, and early-summer harvest.",
            "Development is driven by photo-thermal-vernal time rather than temperature alone.",
        ],
    },
}
