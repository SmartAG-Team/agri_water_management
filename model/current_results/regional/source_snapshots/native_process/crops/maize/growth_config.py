from __future__ import annotations

from crops.maize.config import IOWA_Stage_ORDER

# Maize growth and management phases are defined directly on the complete Iowa
# stage sequence.  The phase groups do not replace the individual stages; they
# only select the appropriate parameter and management policy family.
PHASES = [
    ("establishment", "VS", "VE"),
    ("early_vegetative", "V1", "V5"),
    ("rapid_vegetative", "V6", "V12"),
    ("pre_tassel", "V13", "V16+"),
    ("tasseling_silking", "VT", "R1"),
    ("kernel_development", "R2", "R4"),
    ("grain_filling", "R5", "R5"),
    ("maturity", "R6", "R6"),
]

PROCESS_PHASES = [
    ("sowing_emergence", "VS", "VE"),
    ("leaf_development", "V1", "V5"),
    ("rapid_vegetative", "V6", "V12"),
    ("pre_tassel", "V13", "V16+"),
    ("tasseling_silking", "VT", "R1"),
    ("kernel_development", "R2", "R4"),
    ("grain_filling", "R5", "R5"),
    ("maturity", "R6", "R6"),
]

# Existing calibrated curve values are retained, but each former curve anchor
# is attached to a real Iowa stage.  Interpolation is performed by Iowa rank,
# so V11 through V16 no longer collapse into one internal value.
CURVE_ANCHOR_STAGE = {
    0: "VS",
    9: "VE",
    17: "V5",
    25: "V10",
    31: "V12",
    39: "V14",
    49: "V16+",
    55: "VT",
    61: "R1",
    71: "R2",
    81: "R4",
    87: "R5",
    92: "R6",
}

if set(CURVE_ANCHOR_STAGE.values()) - set(IOWA_Stage_ORDER):
    raise ValueError("maize growth curve contains an unknown Iowa stage anchor")

GROWTH_PARAMETERS = {
    "light_extinction_coefficient": 0.58,
    "par_fraction": 0.48,
    "maintenance_respiration_fraction": 0.07,
    "soil_evaporation_depth_cm": 15.0,
    "root_distribution_shape": 1.9,
    "max_root_depth_mm": 1800.0,
}

PHASE_PARAMETER_CURVES = {
    "kc": [
        (0, 0.28),
        (9, 0.40),
        (17, 0.55),
        (25, 0.80),
        (31, 0.98),
        (39, 1.10),
        (49, 1.18),
        (55, 1.20),
        (61, 1.18),
        (71, 1.10),
        (81, 0.95),
        (87, 0.78),
        (92, 0.45),
    ],
    "stress_weight": [
        (0, 0.65),
        (9, 0.75),
        (17, 0.85),
        (25, 0.95),
        (31, 1.15),
        (39, 1.28),
        (49, 1.42),
        (55, 1.50),
        (61, 1.50),
        (71, 1.35),
        (81, 1.10),
        (87, 0.85),
        (92, 0.55),
    ],
}

PROCESS_PARAMETER_CURVES = {
    "rue_g_mj": [
        (0, 0.0),
        (9, 1.7),
        (17, 2.7),
        (25, 3.5),
        (31, 4.2),
        (39, 4.6),
        (49, 4.8),
        (55, 4.9),
        (61, 4.8),
        (71, 4.2),
        (81, 3.0),
        (87, 1.6),
        (92, 0.2),
    ],
    "sla_m2_kg": [
        (0, 10.0),
        (9, 8.0),
        (17, 7.0),
        (25, 5.8),
        (31, 5.0),
        (39, 4.4),
        (49, 4.0),
        (55, 3.8),
        (61, 3.5),
        (71, 3.1),
        (81, 2.8),
        (87, 2.5),
        (92, 2.2),
    ],
    "leaf_senescence_fraction": [
        (0, 0.0),
        (9, 0.001),
        (17, 0.0015),
        (25, 0.0020),
        (31, 0.0026),
        (39, 0.0034),
        (49, 0.0046),
        (55, 0.0060),
        (61, 0.0085),
        (71, 0.0160),
        (81, 0.0300),
        (87, 0.0540),
        (92, 0.0850),
    ],
    "stem_senescence_fraction": [
        (0, 0.0),
        (9, 0.0003),
        (17, 0.0005),
        (25, 0.0007),
        (31, 0.0009),
        (39, 0.0012),
        (49, 0.0018),
        (55, 0.0026),
        (61, 0.0040),
        (71, 0.0090),
        (81, 0.0160),
        (87, 0.0240),
        (92, 0.0360),
    ],
    "ear_senescence_fraction": [
        (0, 0.0),
        (9, 0.0),
        (17, 0.0),
        (25, 0.0),
        (31, 0.0),
        (39, 0.0),
        (49, 0.0010),
        (55, 0.0025),
        (61, 0.0040),
        (71, 0.0070),
        (81, 0.0110),
        (87, 0.0160),
        (92, 0.0200),
    ],
    "root_extension_mm_per_day": [
        (0, 6.0),
        (9, 18.0),
        (17, 24.0),
        (25, 28.0),
        (31, 34.0),
        (39, 36.0),
        (49, 32.0),
        (55, 24.0),
        (61, 16.0),
        (71, 9.0),
        (81, 3.0),
        (87, 0.6),
        (92, 0.0),
    ],
    "max_root_depth_mm": [
        (0, 50.0),
        (9, 120.0),
        (17, 350.0),
        (25, 650.0),
        (31, 950.0),
        (39, 1300.0),
        (49, 1600.0),
        (55, 1750.0),
        (61, 1800.0),
        (71, 1800.0),
        (81, 1800.0),
        (87, 1800.0),
        (92, 1800.0),
    ],
    "dm_remobilization_fraction": [
        (0, 0.0), (9, 0.0), (17, 0.0), (25, 0.0), (31, 0.0), (39, 0.0),
        (49, 0.02), (55, 0.08), (61, 0.18), (71, 0.36), (81, 0.48), (87, 0.22), (92, 0.10),
    ],
    "n_remobilization_fraction": [
        (0, 0.0), (9, 0.0), (17, 0.0), (25, 0.0), (31, 0.0), (39, 0.0),
        (49, 0.03), (55, 0.12), (61, 0.24), (71, 0.42), (81, 0.52), (87, 0.20), (92, 0.10),
    ],
    "partition": {
        "leaf": [
            (0, 0.00), (9, 0.24), (17, 0.20), (25, 0.16), (31, 0.12), (39, 0.08),
            (49, 0.05), (55, 0.02), (61, 0.005), (71, 0.0), (81, 0.0), (87, 0.0), (92, 0.0),
        ],
        "stem": [
            (0, 0.00), (9, 0.28), (17, 0.34), (25, 0.38), (31, 0.42), (39, 0.38),
            (49, 0.24), (55, 0.12), (61, 0.06), (71, 0.015), (81, 0.004), (87, 0.0), (92, 0.0),
        ],
        "ear": [
            (0, 0.00), (9, 0.00), (17, 0.00), (25, 0.00), (31, 0.06), (39, 0.14),
            (49, 0.24), (55, 0.18), (61, 0.08), (71, 0.02), (81, 0.004), (87, 0.0), (92, 0.0),
        ],
        "grain": [
            (0, 0.00), (9, 0.00), (17, 0.00), (25, 0.00), (31, 0.00), (39, 0.00),
            (49, 0.02), (55, 0.28), (61, 0.72), (71, 0.94), (81, 0.98), (87, 0.0), (92, 0.0),
        ],
        "root": [
            (0, 1.00), (9, 0.48), (17, 0.34), (25, 0.28), (31, 0.22), (39, 0.18),
            (49, 0.12), (55, 0.08), (61, 0.04), (71, 0.02), (81, 0.01), (87, 0.0), (92, 0.0),
        ],
    },
}

ORGAN_N_CONCENTRATIONS = {
    "establishment": {
        "critical": {"leaf": 0.050, "stem": 0.018, "ear": 0.000, "grain": 0.000},
        "minimum": {"leaf": 0.018, "stem": 0.007, "ear": 0.000, "grain": 0.000},
    },
    "early_vegetative": {
        "critical": {"leaf": 0.042, "stem": 0.015, "ear": 0.000, "grain": 0.000},
        "minimum": {"leaf": 0.016, "stem": 0.006, "ear": 0.000, "grain": 0.000},
    },
    "rapid_vegetative": {
        "critical": {"leaf": 0.034, "stem": 0.013, "ear": 0.010, "grain": 0.000},
        "minimum": {"leaf": 0.013, "stem": 0.005, "ear": 0.005, "grain": 0.000},
    },
    "pre_tassel": {
        "critical": {"leaf": 0.028, "stem": 0.011, "ear": 0.014, "grain": 0.000},
        "minimum": {"leaf": 0.010, "stem": 0.004, "ear": 0.006, "grain": 0.000},
    },
    "tasseling_silking": {
        "critical": {"leaf": 0.022, "stem": 0.009, "ear": 0.015, "grain": 0.016},
        "minimum": {"leaf": 0.008, "stem": 0.0035, "ear": 0.007, "grain": 0.013},
    },
    "kernel_development": {
        "critical": {"leaf": 0.016, "stem": 0.007, "ear": 0.011, "grain": 0.018},
        "minimum": {"leaf": 0.006, "stem": 0.003, "ear": 0.005, "grain": 0.015},
    },
    "grain_filling": {
        "critical": {"leaf": 0.016, "stem": 0.007, "ear": 0.011, "grain": 0.018},
        "minimum": {"leaf": 0.006, "stem": 0.003, "ear": 0.005, "grain": 0.015},
    },
    "maturity": {
        "critical": {"leaf": 0.010, "stem": 0.005, "ear": 0.008, "grain": 0.017},
        "minimum": {"leaf": 0.004, "stem": 0.002, "ear": 0.004, "grain": 0.015},
    },
}
