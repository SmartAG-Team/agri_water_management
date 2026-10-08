from typing import List
IOWA_Stage_ORDER: List[str] = [
    "VS","VE",
    "V1","V2","V3","V4","V5","V6","V7","V8","V9","V10","V11","V12","V13","V14","V15","V16","V16+",
    "VT","R1","R2","R3","R4","R5","R6"
]
IOWA_Stage_INDEX = {s: i for i, s in enumerate(IOWA_Stage_ORDER)}

# Companion BBCH values used by integrated simulation outputs.  Iowa stages
# remain the source of truth for maize phenology and management windows.
IOWA_STAGE_BBCH = {
    "VS": 5,
    "VE": 9,
    "V1": 14,
    "V2": 18,
    "V3": 23,
    "V4": 27,
    "V5": 32,
    "V6": 36,
    "V7": 40,
    "V8": 45,
    "V9": 49,
    "V10": 50,
    "V11": 52,
    "V12": 53,
    "V13": 54,
    "V14": 56,
    "V15": 57,
    "V16": 58,
    "V16+": 60,
    "VT": 61,
    "R1": 65,
    "R2": 73,
    "R3": 79,
    "R4": 85,
    "R5": 89,
    "R6": 95,
}

MAIZE_PHENOLOGY_PARAMETER_VERSION = "maize_seasonal_gdd_v1"
SPRING_MAIZE_PLANTING_CUTOFF = (5, 25)
SPRING_MAIZE_MIDDLE_SEASON_FRACTION = 0.95

phen_config={
"MAIZE_CARDINAL_TEMPERATURES" : {
    "base_temperature": 8.0,  # Base temperature for maize growth
    "optimal_temperature": 30.0,  # Optimal temperature for growth
    "maximum_temperature": 44.0,  # Maximum temperature for maize growth
},

"MAIZE_STAGE_THRESHOLDS" : {
    "very_early": {
        "VS": 0,
        "VE": 68.0,
        "V1": 102.0,
        "V2": 153.0,
        "V3": 204.0,
        "V4": 255.0,
        "V5": 306.0,
        "V6": 357.0,
        "V7": 408.0,
        "V8": 459.0,
        "V9": 510.0,
        "V10": 561.0,
        "V11": 612.0,
        "V12": 663.0,
        "V13": 714.0,
        "V14": 765.0,
        "V15": 816.0,
        "V16": 867.0,
        "V16+": 892.5,
        "VT": 935.0,
        "R1": 1062.5,
        "R2": 1227.464,
        "R3": 1392.428,
        "R4": 1557.393,
        "R5": 1722.357,
        "R6": 1887.321
    },
    "early": {
        "VS": 0,
        "VE": 74.4,
        "V1": 111.6,
        "V2": 167.4,
        "V3": 223.2,
        "V4": 279.0,
        "V5": 334.8,
        "V6": 390.6,
        "V7": 446.4,
        "V8": 502.2,
        "V9": 558.0,
        "V10": 613.8,
        "V11": 669.6,
        "V12": 725.4,
        "V13": 781.2,
        "V14": 837.0,
        "V15": 892.8,
        "V16": 948.6,
        "V16+": 976.5,
        "VT": 1023.0,
        "R1": 1162.5,
        "R2": 1322.156,
        "R3": 1481.811,
        "R4": 1641.467,
        "R5": 1801.122,
        "R6": 1960.778
    },
    "middle": {
        "VS": 0,
        "VE": 80.8,
        "V1": 121.2,
        "V2": 181.8,
        "V3": 242.4,
        "V4": 303.0,
        "V5": 363.6,
        "V6": 424.2,
        "V7": 484.8,
        "V8": 545.4,
        "V9": 606.0,
        "V10": 666.6,
        "V11": 727.2,
        "V12": 787.8,
        "V13": 848.4,
        "V14": 909.0,
        "V15": 969.6,
        "V16": 1030.2,
        "V16+": 1060.5,
        "VT": 1111.0,
        "R1": 1262.5,
        "R2": 1410.000,
        "R3": 1557.500,
        "R4": 1705.000,
        "R5": 1852.500,
        "R6": 2000.000
    },
    "late": {
        "VS": 0,
        "VE": 84.0,
        "V1": 126.0,
        "V2": 189.0,
        "V3": 252.0,
        "V4": 315.0,
        "V5": 378.0,
        "V6": 441.0,
        "V7": 504.0,
        "V8": 567.0,
        "V9": 630.0,
        "V10": 693.0,
        "V11": 756.0,
        "V12": 819.0,
        "V13": 882.0,
        "V14": 945.0,
        "V15": 1008.0,
        "V16": 1071.0,
        "V16+": 1102.5,
        "VT": 1155.0,
        "R1": 1312.5,
        "R2": 1457.780,
        "R3": 1603.060,
        "R4": 1748.341,
        "R5": 1893.621,
        "R6": 2038.901
    },
    "very_late": {
        "VS": 0,
        "VE": 92.0,
        "V1": 138.0,
        "V2": 207.0,
        "V3": 276.0,
        "V4": 345.0,
        "V5": 414.0,
        "V6": 483.0,
        "V7": 552.0,
        "V8": 621.0,
        "V9": 690.0,
        "V10": 759.0,
        "V11": 828.0,
        "V12": 897.0,
        "V13": 966.0,
        "V14": 1035.0,
        "V15": 1104.0,
        "V16": 1173.0,
        "V16+": 1207.5,
        "VT": 1265.0,
        "R1": 1437.5,
        "R2": 1567.978,
        "R3": 1698.455,
        "R4": 1828.933,
        "R5": 1959.410,
        "R6": 2089.888
    }
}
}
# crops/maize/config.py

# ... phen_config remains unchanged ...

spray_weather_config={
    "PARAMETERS": {
        "foliar_nutrition": [
            {"key": "temperature_2m", "x0": 22, "k": 0.35},
            {"key": "relative_humidity_2m", "x0": 65, "k": 0.08},
            {"key": "wind_speed_10m", "x0": -2.2, "k": 2.0, "negate": True},
            {"key": "precipitation", "x0": -0.12, "k": 18.0, "negate": True},
            {"key": "shortwave_radiation", "x0": -1.62, "k": 2.50, "negate": True},
        ],
    },
}
