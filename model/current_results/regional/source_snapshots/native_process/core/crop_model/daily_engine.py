from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from math import exp, isfinite, sqrt
import re
from typing import Any

import pandas as pd

from crops.cotton.config import (
    COTTON_MANAGEMENT_POLICY,
    IRRIGATION_METHOD_SPECS as COTTON_IRRIGATION_METHOD_SPECS,
)
from crops.maize.config import IOWA_STAGE_BBCH, IOWA_Stage_ORDER, phen_config as MAIZE_PHENOLOGY_CONFIG
from crops.maize.growth_regulator import GROWTH_REGULATION_CONFIG
from crops.maize.phenology import (
    build_maize_stage_thresholds,
    compute_maize_season_gdd,
    has_maize_cultivar_phenology,
    resolve_maize_phenology_profile,
)


MAIZE_UUID = "0181d981-0649-4be8-bd90-5d377ff86849"
WHEAT_UUID = "69231650-8600-4000-8000-000000000001"
COTTON_UUID = "69231650-8600-4000-8000-000000000002"


MAIZE_IOWA_STAGE_ORDER = tuple(IOWA_Stage_ORDER)
MAIZE_IOWA_STAGE_RANK = {stage: index for index, stage in enumerate(MAIZE_IOWA_STAGE_ORDER)}


def _maize_phenology_definition(
    maturation_group: str,
    cultivar_phenology: dict[str, Any] | None = None,
    *,
    planting_date: Any = None,
    phenology_profile: str | None = None,
    season_gdd: float | None = None,
) -> tuple[float, tuple[tuple[str, float, int], ...]]:
    thresholds = build_maize_stage_thresholds(
        maturation_group,
        cultivar_phenology,
        planting_date=planting_date,
        phenology_profile=phenology_profile,
        season_gdd=season_gdd,
    )
    maturity_thermal_time = float(thresholds["R6"])
    phases = tuple(
        (
            stage,
            float(thresholds[stage]) / maturity_thermal_time,
            IOWA_STAGE_BBCH[stage],
        )
        for stage in MAIZE_IOWA_STAGE_ORDER
    )
    return maturity_thermal_time, phases


DEFAULT_MAIZE_MATURITY_THERMAL_TIME, DEFAULT_MAIZE_PHENOLOGY_PHASES = _maize_phenology_definition("middle")


@dataclass(frozen=True)
class CropProfile:
    name: str
    base_temp_c: float
    max_lai: float
    rue_g_mj: float
    initial_lai: float
    final_leaf_number: float
    sla_max_m2_g: float
    sla_min_m2_g: float
    extinction_coefficient: float
    dead_extinction_coefficient: float
    max_grain_size_g: float
    max_grains_per_plant: float
    default_plant_population_m2: float
    kernels_per_g_flowering_biomass: float
    maturity_thermal_time_c: float
    phenology_phases: tuple[tuple[str, float, int], ...]
    root_initial_mm: float
    root_max_mm: float
    root_growth_end_fraction: float
    water_threshold: float
    water_target: float
    nutrient_coeff: dict[str, float]
    disease_stage_start: int
    insect_stage_start: int


@dataclass(frozen=True)
class CropActionProfile:
    fertilization_bbch_min: int | None = None
    fertilization_bbch_max: int | None = None
    n_topdress_bbch_min: int | None = None
    n_topdress_bbch_max: int | None = None
    fertilization_stage_min: str | None = None
    fertilization_stage_max: str | None = None
    n_topdress_stage_min: str | None = None
    n_topdress_stage_max: str | None = None
    automatic_irrigation_stage_max: str | None = None
    automatic_insecticide_stage_max: str | None = None
    min_projected_n_gap_kg_ha: float = 20.0
    max_single_n_kg_ha: float = 120.0
    min_n_application_interval_days: int = 14
    min_fertilizer_application_interval_days: int = 14
    max_irrigation_mm: float | None = None
    default_n_kg_ha: float = 18.0
    default_pk_kg_ha: float = 8.0
    insecticide_residual_days: int = 14


PROFILES: dict[str, CropProfile] = {
    MAIZE_UUID: CropProfile(
        name="maize",
        base_temp_c=8.0,
        max_lai=5.4,
        rue_g_mj=2.0,
        initial_lai=0.05,
        final_leaf_number=15.0,
        sla_max_m2_g=0.030,
        sla_min_m2_g=0.015,
        extinction_coefficient=0.62,
        dead_extinction_coefficient=0.45,
        max_grain_size_g=0.34,
        max_grains_per_plant=850.0,
        default_plant_population_m2=5.2,
        kernels_per_g_flowering_biomass=5.2,
        maturity_thermal_time_c=DEFAULT_MAIZE_MATURITY_THERMAL_TIME,
        phenology_phases=DEFAULT_MAIZE_PHENOLOGY_PHASES,
        root_initial_mm=180.0,
        root_max_mm=1150.0,
        root_growth_end_fraction=0.60,
        water_threshold=0.46,
        water_target=0.78,
        nutrient_coeff={"N": 0.022, "P2O5": 0.008, "K2O": 0.012},
        disease_stage_start=30,
        insect_stage_start=18,
    ),
    WHEAT_UUID: CropProfile(
        name="wheat",
        base_temp_c=0.0,
        max_lai=6.0,
        rue_g_mj=2.9,
        initial_lai=0.04,
        final_leaf_number=9.0,
        sla_max_m2_g=0.027,
        sla_min_m2_g=0.014,
        extinction_coefficient=0.58,
        dead_extinction_coefficient=0.42,
        max_grain_size_g=0.045,
        max_grains_per_plant=45.0,
        default_plant_population_m2=260.0,
        kernels_per_g_flowering_biomass=22.0,
        maturity_thermal_time_c=1700.0,
        phenology_phases=(
            ("Sowing", 0.03, 5),
            ("Emergence", 0.08, 10),
            ("Tillering", 0.25, 25),
            ("Stem elongation", 0.45, 35),
            ("Booting", 0.58, 45),
            ("Anthesis", 0.67, 65),
            ("Milk", 0.78, 75),
            ("Dough", 0.90, 85),
            ("Maturity", 1.00, 95),
        ),
        root_initial_mm=160.0,
        root_max_mm=1200.0,
        root_growth_end_fraction=0.67,
        water_threshold=0.50,
        water_target=0.82,
        nutrient_coeff={"N": 0.025, "P2O5": 0.010, "K2O": 0.018},
        disease_stage_start=25,
        insect_stage_start=21,
    ),
    COTTON_UUID: CropProfile(
        name="cotton",
        base_temp_c=12.0,
        max_lai=4.2,
        rue_g_mj=2.2,
        initial_lai=0.04,
        final_leaf_number=18.0,
        sla_max_m2_g=0.026,
        sla_min_m2_g=0.013,
        extinction_coefficient=0.65,
        dead_extinction_coefficient=0.45,
        max_grain_size_g=0.12,
        max_grains_per_plant=32.0,
        default_plant_population_m2=9.0,
        kernels_per_g_flowering_biomass=8.0,
        maturity_thermal_time_c=1800.0,
        phenology_phases=(
            ("Emergence", 0.06, 9),
            ("Squaring", 0.26, 31),
            ("Flowering", 0.48, 61),
            ("Boll growth", 0.72, 75),
            ("Boll opening", 0.90, 85),
            ("Maturity", 1.00, 95),
        ),
        root_initial_mm=180.0,
        root_max_mm=1050.0,
        root_growth_end_fraction=0.72,
        water_threshold=0.52,
        water_target=0.84,
        nutrient_coeff={"N": 0.035, "P2O5": 0.014, "K2O": 0.030},
        disease_stage_start=30,
        insect_stage_start=20,
    ),
}


def _profile_for_payload(profile: CropProfile, payload: dict[str, Any]) -> CropProfile:
    if profile.name != "maize":
        return profile
    variety = _dump(payload.get("variety_characteristics") or {})
    maturation_group = variety.get("maturation_group") or "middle"
    cultivar_phenology = _dump(variety.get("phenology") or {})
    phenology_profile = variety.get("phenology_profile")
    resolved_profile, _ = resolve_maize_phenology_profile(
        payload.get("planting_date"),
        phenology_profile,
    )
    season_gdd = None
    if resolved_profile == "spring_maize" and not has_maize_cultivar_phenology(
        cultivar_phenology
    ):
        season_gdd = compute_maize_season_gdd(
            pd.DataFrame(payload.get("daily_weather_data") or []),
            payload.get("planting_date"),
            payload.get("season_end"),
        )
    maturity_thermal_time, phases = _maize_phenology_definition(
        str(maturation_group),
        cultivar_phenology,
        planting_date=payload.get("planting_date"),
        phenology_profile=phenology_profile,
        season_gdd=season_gdd,
    )
    return replace(
        profile,
        maturity_thermal_time_c=maturity_thermal_time,
        phenology_phases=phases,
    )


ACTION_PROFILES: dict[str, CropActionProfile] = {
    "maize": CropActionProfile(
        fertilization_stage_min="VE",
        fertilization_stage_max="R3",
        n_topdress_stage_min="V9",
        n_topdress_stage_max="R1",
        # Later operations need field confirmation; these are automatic-release
        # limits, not a claim that late grain-filling maize has no water need.
        automatic_irrigation_stage_max="R3",
        automatic_insecticide_stage_max="R3",
        max_single_n_kg_ha=180.0,
        min_n_application_interval_days=14,
        min_fertilizer_application_interval_days=14,
        max_irrigation_mm=None,
        default_n_kg_ha=18.0,
        default_pk_kg_ha=8.0,
        insecticide_residual_days=14,
    ),
    "wheat": CropActionProfile(
        fertilization_bbch_min=10,
        fertilization_bbch_max=79,
        n_topdress_bbch_min=31,
        n_topdress_bbch_max=65,
        max_single_n_kg_ha=90.0,
        min_n_application_interval_days=21,
        min_fertilizer_application_interval_days=21,
        max_irrigation_mm=90.0,
        default_n_kg_ha=45.0,
        default_pk_kg_ha=12.0,
        insecticide_residual_days=12,
    ),
    "cotton": CropActionProfile(
        fertilization_bbch_min=13,
        fertilization_bbch_max=80,
        n_topdress_bbch_min=51,
        n_topdress_bbch_max=75,
        max_single_n_kg_ha=30.0,
        min_n_application_interval_days=7,
        min_fertilizer_application_interval_days=7,
        max_irrigation_mm=75.0,
        default_n_kg_ha=24.0,
        default_pk_kg_ha=12.0,
        insecticide_residual_days=11,
    ),
}


RISK_ORDER = {
    "LOW": 0,
    "OUT_OF_SEASON": 0,
    "UNFAVORABLE": 0,
    "NOT_SEASONAL": 0,
    "IRRIGATED": 0,
    "FERTILIZED": 0,
    "PROTECTED": 0,
    "MEDIUM": 1,
    "WATCH": 1,
    "FAVORABLE": 1,
    "HIGH": 2,
    "OPTIMAL": 2,
    "DEFICIENT": 2,
}

MAIZE_POST_GRASS_WEED_CODES = {"DIGSA", "ECHCG", "SETVI", "TRZAS"}
MAIZE_POST_HARD_GRASS_WEED_CODES = {"SORHA", "CYNSS"}
MAIZE_POST_BROADLEAF_WEED_CODES = {"AMARE", "CHEAL", "POROL", "CALHE", "HUMSC", "COMCO"}
MAIZE_POST_WEED_CODES = (
    MAIZE_POST_GRASS_WEED_CODES
    | MAIZE_POST_HARD_GRASS_WEED_CODES
    | MAIZE_POST_BROADLEAF_WEED_CODES
    | {"ELEIN", "CYPRO"}
)
MAIZE_POST_WEED_NAMES = {
    "DIGSA": "马唐",
    "ECHCG": "稗草",
    "SETVI": "狗尾草",
    "ELEIN": "牛筋草",
    "TRZAS": "自生小麦",
    "SORHA": "野高粱",
    "CYNSS": "狗牙根",
    "AMARE": "反枝苋",
    "CHEAL": "藜",
    "POROL": "马齿苋",
    "CALHE": "打碗花",
    "HUMSC": "葎草",
    "COMCO": "鸭跖草",
    "CYPRO": "香附子",
}
MAIZE_POST_HERBICIDE_STAGES = {"V3", "V4", "V5"}
MAIZE_WATER_STAGE_THRESHOLDS = {
    "VE_V5": {"medium": 0.50, "high": 0.40, "target": 0.65, "label": "苗期"},
    "V6_V11": {"medium": 0.58, "high": 0.48, "target": 0.70, "label": "营养生长期"},
    "V12_VT": {"medium": 0.65, "high": 0.55, "target": 0.78, "label": "大喇叭口-抽雄前"},
    "R1_R2": {"medium": 0.70, "high": 0.60, "target": 0.80, "label": "抽雄吐丝-授粉"},
    "R3_R4": {"medium": 0.60, "high": 0.50, "target": 0.72, "label": "灌浆中期"},
    "R5_R6": {"medium": 0.50, "high": 0.40, "target": 0.62, "label": "成熟后期"},
}

MAIZE_IRRIGATION_DEPTH_LIMITS_MM = {
    "VE_V5": (20.0, 40.0),
    "V6_V11": (25.0, 50.0),
    "V12_VT": (35.0, 70.0),
    "R1_R2": (35.0, 75.0),
    "R3_R4": (25.0, 60.0),
    "R5_R6": (15.0, 40.0),
}

IRRIGATION_METHOD_DEPTH_LIMITS_MM = {
    "drip": (12.0, 32.0),
    "micro-sprinkler": (22.0, 50.0),
    "sprinkler": (30.0, 65.0),
    "flood": (45.0, 95.0),
}


DEFAULT_DAILY_OUTPUTS = [
    "date",
    "days_after_planting",
    "growth_stage",
    "thermal_time_c",
    "cumulative_thermal_time_c",
    "active_temperature_c",
    "cumulative_active_temperature_c",
    "effective_temperature_c",
    "cumulative_effective_temperature_c",
    "lai",
    "green_lai",
    "senesced_lai",
    "aboveground_biomass_kg_ha",
    "dead_biomass_kg_ha",
    "grain_yield_kg_ha",
    "harvest_index",
    "root_depth_m",
    "rain_mm",
    "irrigation_mm",
    "root_zone_available_water_mm",
    "root_zone_capacity_mm",
    "root_zone_relative_available_water",
    "available_water_mm",
    "soil_water_mm",
    "aet_mm",
    "pet_mm",
    "drainage_mm",
    "water_stress_index",
    "n_stress_index",
    "p_stress_index",
    "k_stress_index",
    "disease_stress_index",
    "insect_stress_index",
    "weed_stress_index",
    "disease_favorability",
    "insect_favorability",
    "disease_field_risk",
    "insect_field_risk",
    "weed_field_risk",
    "disease_risk",
    "insect_risk",
    "weed_risk",
    "grain_sink_kg_ha",
    "grain_number_m2",
    "nutrient_supported_grain_yield_kg_ha",
    "n_demand_kg_ha",
    "p2o5_demand_kg_ha",
    "k2o_demand_kg_ha",
    "n_uptake_kg_ha",
    "p2o5_uptake_kg_ha",
    "k2o_uptake_kg_ha",
    "cumulative_n_uptake_kg_ha",
    "cumulative_p2o5_uptake_kg_ha",
    "cumulative_k2o_uptake_kg_ha",
    "n_volatilization_loss_kg_ha",
    "n_leaching_loss_kg_ha",
    "cumulative_n_volatilization_loss_kg_ha",
    "cumulative_n_leaching_loss_kg_ha",
    "cumulative_n_loss_kg_ha",
    "foliar_absorbed_n_kg_ha",
    "foliar_absorbed_p2o5_kg_ha",
    "foliar_absorbed_k2o_kg_ha",
    "foliar_washoff_fraction",
    "canopy_vigor_index",
    "cotton_canopy_regulation_status",
    "topping_status",
    "estimated_open_bolls_pct",
    "estimated_defoliation_pct",
    "harvest_aid_status",
    "machine_harvest_status",
]

DEFAULT_SUMMARY_OUTPUTS = [
    "final_yield_kg_ha",
    "max_lai",
    "total_aboveground_biomass_kg_ha",
    "total_rain_mm",
    "total_irrigation_mm",
    "season_water_stress",
    "season_nutrition_stress",
    "season_disease_stress",
    "season_insect_stress",
    "final_harvest_index",
    "final_grain_sink_kg_ha",
    "total_n_uptake_kg_ha",
    "total_p2o5_uptake_kg_ha",
    "total_k2o_uptake_kg_ha",
    "total_n_loss_kg_ha",
]


def run_fullseason_simulation(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_simulation_payload(payload)
    return _run_daily_engine(normalized, decision_date=None, include_actions=False, filter_outputs=True)


def run_daily_decision_engine(payload: dict[str, Any]) -> dict[str, Any]:
    if "simulation" not in payload:
        raise ValueError("decision-date-actions requires a simulation request body")
    simulation_payload = _normalize_simulation_payload(payload["simulation"])
    simulation_payload["decision_date"] = payload["decision_date"]
    return _run_daily_engine(simulation_payload, decision_date=_as_date(payload["decision_date"]), include_actions=True, filter_outputs=False)


def _run_daily_engine(payload: dict[str, Any], *, decision_date: date | None, include_actions: bool, filter_outputs: bool) -> dict[str, Any]:
    profile = PROFILES.get(str(payload.get("crop_uuid")))
    if profile is None:
        raise ValueError(f"Unsupported crop_uuid for daily crop engine: {payload.get('crop_uuid')}")
    profile = _profile_for_payload(profile, payload)

    planting_date = _as_date(payload["planting_date"])
    decision_date = decision_date or _as_date(payload.get("decision_date") or payload.get("season_start") or planting_date)
    season_start = _as_date(payload.get("season_start") or planting_date)
    season_end = _as_date(payload.get("season_end") or _last_weather_date(payload) or decision_date)
    if season_end < season_start:
        raise ValueError("season_end must be on or after season_start")

    daily_weather = _daily_weather_map(payload.get("daily_weather_data") or payload.get("weather_data") or [])
    seasonal_max_wind_m_s = max(
        (
            float(weather.get("windspeed_10m_max", 0.0) or 0.0)
            for weather_day, weather in daily_weather.items()
            if season_start <= weather_day <= season_end
        ),
        default=0.0,
    )
    hourly_by_day = _hourly_weather_by_day(payload.get("hourly_weather_data") or [])
    soil_layers = _init_soil_layers(payload.get("soil_profile") or [])
    if not soil_layers:
        raise ValueError("soil_profile is required")
    maturity_thermal_time = float(payload.get("maturity_thermal_time_c") or profile.maturity_thermal_time_c)
    plant_population_m2 = _plant_population_m2(payload, profile)

    applied_irrigations = _events_by_day(payload.get("applied_irrigations") or [], ("date", "Date"))
    applied_fertilizers = _events_by_day(payload.get("applied_fertilizers") or [], ("Date", "date"))
    applied_fertigations = _events_by_day(payload.get("applied_fertigation") or [], ("Date", "date"))
    applied_water_events = _merge_events_by_day(applied_irrigations, applied_fertigations)
    applied_nutrient_events = _merge_events_by_day(applied_fertilizers, applied_fertigations)
    applied_fungicides = _events_by_day(payload.get("applied_fungicides") or [], ("applied_date", "Date", "date"))
    normalized_insecticides = _normalize_insecticide_applications(
        payload.get("applied_insecticides") or [],
        profile=profile,
    )
    applied_insecticides = _events_by_day(normalized_insecticides, ("Date", "date"))
    applied_herbicides = _events_by_day(payload.get("applied_herbicides") or [], ("Date", "date", "applied_date"))
    applied_growth_regulators = _events_by_day(payload.get("applied_growth_regulators") or [], ("Date", "date", "applied_date"))
    applied_toppings = _events_by_day(payload.get("applied_toppings") or [], ("Date", "date", "applied_date"))
    applied_harvest_aids = _events_by_day(payload.get("applied_harvest_aids") or [], ("Date", "date", "applied_date"))
    applied_harvests = _events_by_day(payload.get("applied_harvests") or [], ("Date", "date", "applied_date"))
    target_yield = float(payload.get("yield_target_kg_ha") or 0.0)
    if target_yield <= 0.0:
        raise ValueError("yield_target_kg_ha is required")

    enabled = _enabled_stresses(payload)
    cotton_management_settings = _dump(payload.get("cotton_management") or {})
    cotton_management_enabled = profile.name == "cotton" and bool(cotton_management_settings.get("enabled", False))
    growth_regulation_enabled = (
        profile.name == "maize" and bool(_dump(payload.get("growth_regulation") or {}).get("enabled", False))
    ) or (
        cotton_management_enabled and bool(cotton_management_settings.get("canopy_regulation", True))
    )
    enabled_nutrients = enabled["nutrition"]
    if (enabled["disease"] or enabled["insect"]) and not payload.get("hourly_weather_data"):
        raise ValueError("weather.hourly is required when disease or insect stresses are requested")
    growth_stage_observations = _normalize_growth_stage_observations(
        payload.get("observations") or {},
        profile=profile,
        season_start=season_start,
        season_end=season_end,
        decision_date=decision_date if include_actions else None,
    )
    if cotton_management_enabled:
        growth_stage_observations = [
            *_cotton_regional_phenology_observations(payload, planting_date, season_end),
            *growth_stage_observations,
        ]
    phenology_rows, profile, maturity_thermal_time, _ = _build_growth_stage_rows(
        profile=profile,
        daily_weather=daily_weather,
        hourly_by_day=hourly_by_day,
        season_start=season_start,
        season_end=season_end,
        maturity_thermal_time=maturity_thermal_time,
        observations=growth_stage_observations,
    )
    phenology_by_day = {row["Date"]: row for row in phenology_rows}
    _validate_applied_growth_regulator_stages(applied_growth_regulators, phenology_by_day, profile)
    biotic = _simulate_biotic_processes(
        payload=payload,
        profile=profile,
        phenology_rows=phenology_rows,
        enabled=enabled,
        season_start=season_start,
        season_end=season_end,
    )
    nutrient_demand_fraction_sum = _seasonal_demand_fraction_sum(phenology_rows)

    nutrient_supply = _initial_nutrient_supply(payload.get("soil_test") or {}, profile, target_yield)
    nutrient_demand_cum = {"N": 0.0, "P2O5": 0.0, "K2O": 0.0}
    nutrient_uptake_cum = {"N": 0.0, "P2O5": 0.0, "K2O": 0.0}
    nitrogen_loss_cum = {"volatilization": 0.0, "leaching": 0.0}
    nutrient_requirement_yield = max(target_yield, _potential_grain_sink_capacity(profile, plant_population_m2))
    seasonal_nutrient_need = {key: nutrient_requirement_yield * coeff for key, coeff in profile.nutrient_coeff.items()}

    daily_rows: list[dict[str, Any]] = []
    daily_risk = {"water": [], "nutrition": {"N": [], "P2O5": [], "K2O": []}, "disease": [], "insect": [], "weed": [], "lodging": []}
    actions: list[dict[str, Any]] = []
    action_ids: set[str] = set()
    open_episodes = {
        "irrigation": False,
        "fertilization": False,
        "fungicide": False,
        "insecticide": False,
        "herbicide": False,
        "growth_regulator": False,
        "topping": False,
        "harvest_aid": False,
        "harvest": False,
    }
    emitted_domains: set[str] = set()

    lai = profile.initial_lai
    dead_lai = 0.0
    initial_sla = profile.sla_max_m2_g
    live_leaf_biomass = lai / max(initial_sla * 0.1, 1e-6)
    dead_leaf_biomass = 0.0
    live_non_leaf_biomass = 0.0
    live_vegetative_biomass = live_leaf_biomass + live_non_leaf_biomass
    dead_biomass = 0.0
    total_biomass = live_vegetative_biomass
    grain_yield = 0.0
    grain_number_m2 = 0.0
    grain_sink_kg_ha = 0.0
    flowering_grain_number_set = False
    cumulative_gdd = 0.0
    cumulative_active_temperature = 0.0
    root_depth = profile.root_initial_mm

    for day in _date_range(season_start, season_end):
        weather = daily_weather.get(day) or _daily_from_hourly(day, hourly_by_day.get(day) or [])
        mean_temperature = float(weather["temperature_2m_mean"])
        active_temperature = mean_temperature if mean_temperature >= profile.base_temp_c else 0.0
        gdd = _daily_thermal_time(profile, weather)
        cumulative_gdd += gdd
        cumulative_active_temperature += active_temperature
        phenology = _phenology_for_thermal_time(profile, cumulative_gdd, maturity_thermal_time)
        stage = phenology
        bbch = int(phenology["BBCH"])
        bbch_continuous = float(phenology["BBCHContinuous"])
        thermal_progress = float(phenology["ThermalProgress"])
        stage_name = str(stage.get("Stage") or stage.get("StageName") or "")
        growth_regulation_stage = _maize_iowa_stage_for_progress(profile, thermal_progress)
        root_depth = _root_depth_for_progress(profile, thermal_progress)
        cotton_leaf_expansion_multiplier = _cotton_leaf_expansion_multiplier(
            profile=profile,
            day=day,
            applied_growth_regulators=applied_growth_regulators,
            applied_toppings=applied_toppings,
        )
        harvest_aid_daily_abscission = _cotton_harvest_aid_daily_abscission(
            profile=profile,
            day=day,
            applied_harvest_aids=applied_harvest_aids,
        )

        irrigation_mm = sum(_event_amount(event, ("amount_mm", "irrigation_mm", "recommendedGrossDepthMm")) for event in applied_water_events.get(day, []))
        fertilizer_release = _fertilizer_release(applied_nutrient_events, day)
        n_volatilization_loss = _nitrogen_volatilization_loss(
            fertilizer_release.get("N", 0.0),
            weather=weather,
            precipitation_mm=float(weather["precipitation_sum"]),
            irrigation_mm=irrigation_mm,
            irrigation_method=str(payload.get("irrigation_method") or ""),
            mulch_enabled=bool(payload.get("mulch_enabled")),
        )
        nitrogen_loss_cum["volatilization"] += n_volatilization_loss
        for nutrient, amount in fertilizer_release.items():
            if nutrient == "N":
                nutrient_supply[nutrient] += max(0.0, amount - n_volatilization_loss)
            else:
                nutrient_supply[nutrient] += amount
        foliar_absorption = _foliar_nutrient_absorption(applied_nutrient_events, day, weather)

        et0 = _reference_et(weather)
        canopy_cover = _green_cover(lai, profile)
        pet = et0 * _kc_from_progress(thermal_progress, profile)
        potential_transpiration = pet * canopy_cover
        soil_evaporation = max(0.0, pet - potential_transpiration)

        _add_infiltration(soil_layers, precipitation=float(weather["precipitation_sum"]), irrigation=irrigation_mm)
        water_before = _root_zone_relative_water(soil_layers, root_depth)
        water_supply = _root_zone_water_supply(soil_layers, root_depth)
        if enabled["water"]:
            actual_transpiration = min(potential_transpiration, water_supply)
            water_factor = actual_transpiration / max(potential_transpiration, 1e-6)
        else:
            actual_transpiration = potential_transpiration
            water_factor = 1.0
        actual_et = soil_evaporation + actual_transpiration
        drainage = _advance_soil_water(
            soil_layers,
            soil_evaporation=soil_evaporation,
            transpiration=actual_transpiration,
            root_depth_mm=root_depth,
        )
        root_zone_water = _root_zone_water_state(soil_layers, root_depth)
        water_after = root_zone_water["relative_available_water"]
        top_layer_relative_water = _top_layer_relative_water(soil_layers)
        forecast_rain_3d_mm = _forecast_precipitation_mm(daily_weather, day, 3)
        water_diagnosis = (
            _water_diagnosis(
                water_after,
                profile,
                potential_transpiration,
                actual_transpiration,
                stage_name=stage_name,
                thermal_progress=thermal_progress,
                bbch=bbch,
                root_depth_m=root_depth / 1000.0,
                top_layer_relative_water=top_layer_relative_water,
                forecast_rain_3d_mm=forecast_rain_3d_mm,
            )
            if enabled["water"]
            else {
                "stress_risk": "LOW",
                "stress_index": 0.0,
                "stage_band": "",
                "stage_band_label": "",
                "medium_threshold": None,
                "high_threshold": None,
                "target_threshold": None,
                "basis": [],
                "irrigation_recommended": False,
            }
        )
        water_risk = str(water_diagnosis["stress_risk"])
        n_leaching_loss = _nitrogen_leaching_loss(
            nutrient_supply["N"],
            drainage_mm=drainage,
            relative_water=water_after,
            irrigation_method=str(payload.get("irrigation_method") or ""),
        )
        nutrient_supply["N"] = max(0.0, nutrient_supply["N"] - n_leaching_loss)
        nitrogen_loss_cum["leaching"] += n_leaching_loss

        daily_fraction = _daily_demand_fraction(thermal_progress) / nutrient_demand_fraction_sum
        nutrient_status: dict[str, str] = {}
        nutrient_factors: dict[str, float] = {}
        nutrient_demand_daily = {"N": 0.0, "P2O5": 0.0, "K2O": 0.0}
        nutrient_uptake_daily = {"N": 0.0, "P2O5": 0.0, "K2O": 0.0}
        for nutrient in ("N", "P2O5", "K2O"):
            if nutrient not in enabled_nutrients:
                nutrient_status[nutrient] = "LOW"
                nutrient_factors[nutrient] = 1.0
                continue
            demand = seasonal_nutrient_need[nutrient] * daily_fraction
            nutrient_demand_daily[nutrient] = demand
            nutrient_demand_cum[nutrient] += demand
            uptake = min(nutrient_supply[nutrient], demand * max(0.2, water_factor))
            nutrient_supply[nutrient] = max(0.0, nutrient_supply[nutrient] - uptake)
            foliar_uptake = foliar_absorption["absorbed"].get(nutrient, 0.0)
            total_uptake = uptake + foliar_uptake
            nutrient_uptake_daily[nutrient] = total_uptake
            nutrient_uptake_cum[nutrient] += total_uptake
            uptake_ratio = nutrient_uptake_cum[nutrient] / max(nutrient_demand_cum[nutrient], 1e-6)
            status = _nutrient_risk_status(nutrient, uptake_ratio)
            nutrient_status[nutrient] = status
            nutrient_factors[nutrient] = _nutrient_growth_factor(uptake_ratio)

        pending_release = _pending_fertilizer_release(applied_nutrient_events, day)
        target_n_need = target_yield * float(profile.nutrient_coeff.get("N", 0.0) or 0.0)
        projected_n_gap = max(
            0.0,
            target_n_need
            - nutrient_uptake_cum["N"]
            - nutrient_supply["N"]
            - pending_release.get("N", 0.0),
        )
        if "N" in enabled_nutrients:
            nutrient_status["N"] = _crop_n_management_risk_status(
                profile=profile,
                stage_name=stage_name,
                thermal_progress=thermal_progress,
                bbch=bbch,
                uptake_status=nutrient_status["N"],
                projected_gap_kg_ha=projected_n_gap,
            )

        disease_state = biotic["by_day"].get(day, {}).get("disease", {})
        insect_state = biotic["by_day"].get(day, {}).get("insect", {})
        disease_risk = disease_state.get("stress_risk", "LOW")
        insect_risk = insect_state.get("stress_risk", "LOW")
        disease_stress_index = float(disease_state.get("stress_index", 0.0) or 0.0)
        insect_stress_index = float(insect_state.get("stress_index", 0.0) or 0.0)
        weed_state = _weed_state_for_day(
            profile=profile,
            payload=payload,
            day=day,
            stage_name=stage.get("Stage") or stage.get("StageName"),
            applied_herbicides=applied_herbicides,
        )
        weed_risk = weed_state.get("stress_risk", "LOW")
        weed_stress_index = float(weed_state.get("stress_index", 0.0) or 0.0)

        nutrition_factor = min(nutrient_factors.values())
        biotic_growth_factor = _biotic_growth_factor(
            disease_stress_index,
            insect_stress_index,
            profile=profile,
            stage_name=stage_name,
            thermal_progress=thermal_progress,
            bbch=bbch,
        )
        combined_factor = max(0.0, min(1.0, water_factor * nutrition_factor * biotic_growth_factor))
        radiation = float(weather["shortwave_radiation_sum"])
        potential_growth = max(0.0, radiation * canopy_cover * profile.rue_g_mj * _temperature_growth_factor(weather, profile) * 10.0)
        actual_growth = potential_growth * combined_factor

        if profile.name == "cotton" and bbch >= 61:
            grain_set_factor = _grain_set_stress_factor(water_factor, nutrition_factor)
            grain_sink_kg_ha = max(
                grain_sink_kg_ha,
                _cotton_seed_cotton_sink_for_stage(
                    plant_population_m2=plant_population_m2,
                    bbch=bbch_continuous,
                    stress_factor=grain_set_factor,
                ),
            )
            grain_number_m2 = _cotton_boll_number_from_sink(grain_sink_kg_ha, plant_population_m2)
        elif not flowering_grain_number_set and _grain_set_window_started(
            profile=profile,
            stage_name=stage_name,
            thermal_progress=thermal_progress,
            bbch=bbch,
        ):
            flowering_biomass_g_m2 = total_biomass * 0.1
            grain_set_factor = _grain_set_stress_factor(water_factor, nutrition_factor)
            source_limited_grains = max(0.0, flowering_biomass_g_m2 * profile.kernels_per_g_flowering_biomass * grain_set_factor)
            population_limited_grains = max(0.0, plant_population_m2 * profile.max_grains_per_plant * grain_set_factor)
            grain_number_m2 = min(source_limited_grains, population_limited_grains)
            grain_sink_kg_ha = grain_number_m2 * profile.max_grain_size_g * 10.0
            flowering_grain_number_set = True

        grain_allocation = _grain_allocation_fraction_for_profile(profile, thermal_progress, bbch_continuous)
        nutrient_supported_grain_yield = _nutrient_supported_grain_yield(nutrient_uptake_cum, profile, enabled_nutrients)
        grain_fill_ceiling = grain_sink_kg_ha * _grain_fill_fraction_for_profile(profile, thermal_progress, bbch_continuous)
        nutrient_fill_ceiling = min(grain_fill_ceiling, nutrient_supported_grain_yield)
        grain_fill_demand = min(max(0.0, grain_sink_kg_ha - grain_yield), max(0.0, nutrient_fill_ceiling - grain_yield))
        grain_growth = min(actual_growth * grain_allocation, grain_fill_demand)
        vegetative_growth = max(0.0, actual_growth - grain_growth)
        grain_yield += grain_growth

        leaf_growth = vegetative_growth * _leaf_partition_fraction_for_profile(profile, thermal_progress, bbch_continuous)
        non_leaf_growth = max(0.0, vegetative_growth - leaf_growth)
        expansion_factor = _leaf_expansion_stress(water_factor, nutrition_factor, weather)
        new_leaf_area = (
            leaf_growth
            * 0.1
            * _specific_leaf_area_for_profile(profile, thermal_progress, bbch_continuous)
            * expansion_factor
            * cotton_leaf_expansion_multiplier
        )
        biotic_leaf_damage = _biotic_leaf_damage_fraction(
            disease_stress_index,
            insect_stress_index,
            profile=profile,
            stage_name=stage_name,
            thermal_progress=thermal_progress,
            bbch=bbch,
        )
        leaf_senescence_fraction = (
            _leaf_senescence_fraction_for_profile(profile, thermal_progress, bbch_continuous, combined_factor)
            + biotic_leaf_damage
            + harvest_aid_daily_abscission
        )
        senesced_leaf_area = min(lai + new_leaf_area, lai * leaf_senescence_fraction)
        senesced_leaf_biomass = min(live_leaf_biomass + leaf_growth, live_leaf_biomass * leaf_senescence_fraction)
        lai = max(0.0, lai + new_leaf_area - senesced_leaf_area)
        dead_lai += senesced_leaf_area
        live_leaf_biomass = max(0.0, live_leaf_biomass + leaf_growth - senesced_leaf_biomass)
        dead_leaf_biomass += senesced_leaf_biomass

        live_non_leaf_biomass += non_leaf_growth
        vegetative_senescence = _vegetative_senescence_fraction_for_profile(profile, thermal_progress, bbch)
        grain_fill_shortfall = min(max(0.0, grain_sink_kg_ha - grain_yield), max(0.0, nutrient_fill_ceiling - grain_yield))
        grain_remobilization = min(grain_fill_shortfall, live_non_leaf_biomass * _grain_remobilization_fraction_for_profile(profile, thermal_progress, bbch_continuous))
        grain_yield += grain_remobilization
        live_non_leaf_biomass = max(0.0, live_non_leaf_biomass - grain_remobilization)
        senesced_non_leaf = live_non_leaf_biomass * vegetative_senescence
        live_non_leaf_biomass = max(0.0, live_non_leaf_biomass - senesced_non_leaf)
        live_vegetative_biomass = live_leaf_biomass + live_non_leaf_biomass
        dead_biomass += max(0.0, senesced_leaf_biomass + senesced_non_leaf)
        total_biomass = live_vegetative_biomass + dead_biomass + grain_yield
        calculated_harvest_index = grain_yield / total_biomass if total_biomass > 0.0 else 0.0

        soil_water = _public_soil_layers(soil_layers)
        row = {
            "Date": day.isoformat(),
            "days_after_planting": (day - planting_date).days,
            "Stage": stage.get("Stage") or "",
            "StageName": stage.get("StageName"),
            "BBCH": bbch,
            "thermal_time_c": round(gdd, 3),
            "cumulative_thermal_time_c": round(cumulative_gdd, 3),
            "active_temperature_c": round(active_temperature, 3),
            "cumulative_active_temperature_c": round(cumulative_active_temperature, 3),
            "effective_temperature_c": round(gdd, 3),
            "cumulative_effective_temperature_c": round(cumulative_gdd, 3),
            "phenology_stage_index": round(float(stage.get("PhenologyStageIndex", 0.0)), 3),
            "phenology_phase_fraction": round(float(stage.get("PhaseFraction", 0.0)), 4),
            "temperature_2m_mean": round(float(weather["temperature_2m_mean"]), 3),
            "temperature_2m_max": round(float(weather["temperature_2m_max"]), 3),
            "windspeed_10m_mean": round(float(weather.get("windspeed_10m_mean", 0.0)), 3),
            "windspeed_10m_max": round(float(weather.get("windspeed_10m_max", 0.0)), 3),
            "precipitation_mm": round(float(weather["precipitation_sum"]), 3),
            "irrigation_mm": round(irrigation_mm, 3),
            "reference_et_mm": round(et0, 3),
            "potential_et_mm": round(pet, 3),
            "actual_et_mm": round(actual_et, 3),
            "aet_mm": round(actual_et, 3),
            "potential_transpiration_mm": round(potential_transpiration, 3),
            "actual_transpiration_mm": round(actual_transpiration, 3),
            "drainage_mm": round(drainage, 3),
            "root_depth_mm": round(root_depth, 3),
            "root_depth_m": round(root_depth / 1000.0, 4),
            "root_zone_relative_available_water": round(water_after, 4),
            "root_zone_available_water_mm": round(root_zone_water["available_water_mm"], 3),
            "root_zone_capacity_mm": round(root_zone_water["capacity_mm"], 3),
            "top_layer_relative_available_water": round(top_layer_relative_water, 4) if top_layer_relative_water is not None else None,
            "forecast_rain_3d_mm": round(forecast_rain_3d_mm, 3),
            "water_stress_stage_band": water_diagnosis.get("stage_band") or "",
            "water_stress_stage_band_label": water_diagnosis.get("stage_band_label") or "",
            "water_medium_threshold": water_diagnosis.get("medium_threshold"),
            "water_high_threshold": water_diagnosis.get("high_threshold"),
            "water_target_threshold": water_diagnosis.get("target_threshold"),
            "water_stress_basis": water_diagnosis.get("basis") or [],
            "irrigation_recommended": bool(water_diagnosis.get("irrigation_recommended")),
            "available_water_mm": round(sum(layer["water_mm"] - layer["wilting_mm"] for layer in soil_layers), 3),
            "soil_water_mm": round(sum(layer["water_mm"] for layer in soil_layers), 3),
            "lai": round(lai, 3),
            "live_lai": round(lai, 3),
            "dead_lai": round(dead_lai, 3),
            "leaf_live_biomass_kg_ha": round(live_leaf_biomass, 3),
            "leaf_dead_biomass_kg_ha": round(dead_leaf_biomass, 3),
            "live_non_leaf_biomass_kg_ha": round(live_non_leaf_biomass, 3),
            "live_aboveground_biomass_kg_ha": round(live_vegetative_biomass + grain_yield, 3),
            "dead_aboveground_biomass_kg_ha": round(dead_biomass, 3),
            "total_biomass_kg_ha": round(total_biomass, 3),
            "grain_weight_kg_ha": round(grain_yield, 3),
            "harvest_index": round(calculated_harvest_index, 4),
            "grain_number_m2": round(grain_number_m2, 3),
            "grain_sink_kg_ha": round(grain_sink_kg_ha, 3),
            "grain_remobilization_kg_ha": round(grain_remobilization, 3),
            "nutrient_supported_grain_yield_kg_ha": round(nutrient_supported_grain_yield, 3) if nutrient_supported_grain_yield < 1e8 else None,
            "yield_target_kg_ha": round(target_yield, 3),
            "nutrient_requirement_yield_kg_ha": round(nutrient_requirement_yield, 3),
            "management_target_n_need_kg_ha": round(target_n_need, 3),
            "projected_n_gap_kg_ha": round(projected_n_gap, 3),
            "pending_n_release_kg_ha": round(pending_release.get("N", 0.0), 3),
            "n_demand_kg_ha": round(nutrient_demand_daily["N"], 3),
            "p2o5_demand_kg_ha": round(nutrient_demand_daily["P2O5"], 3),
            "k2o_demand_kg_ha": round(nutrient_demand_daily["K2O"], 3),
            "cumulative_n_demand_kg_ha": round(nutrient_demand_cum["N"], 3),
            "cumulative_p2o5_demand_kg_ha": round(nutrient_demand_cum["P2O5"], 3),
            "cumulative_k2o_demand_kg_ha": round(nutrient_demand_cum["K2O"], 3),
            "n_uptake_kg_ha": round(nutrient_uptake_daily["N"], 3),
            "p2o5_uptake_kg_ha": round(nutrient_uptake_daily["P2O5"], 3),
            "k2o_uptake_kg_ha": round(nutrient_uptake_daily["K2O"], 3),
            "cumulative_n_uptake_kg_ha": round(nutrient_uptake_cum["N"], 3),
            "cumulative_p2o5_uptake_kg_ha": round(nutrient_uptake_cum["P2O5"], 3),
            "cumulative_k2o_uptake_kg_ha": round(nutrient_uptake_cum["K2O"], 3),
            "n_volatilization_loss_kg_ha": round(n_volatilization_loss, 3),
            "n_leaching_loss_kg_ha": round(n_leaching_loss, 3),
            "cumulative_n_volatilization_loss_kg_ha": round(nitrogen_loss_cum["volatilization"], 3),
            "cumulative_n_leaching_loss_kg_ha": round(nitrogen_loss_cum["leaching"], 3),
            "cumulative_n_loss_kg_ha": round(nitrogen_loss_cum["volatilization"] + nitrogen_loss_cum["leaching"], 3),
            "foliar_absorbed_n_kg_ha": round(foliar_absorption["absorbed"].get("N", 0.0), 3),
            "foliar_absorbed_p2o5_kg_ha": round(foliar_absorption["absorbed"].get("P2O5", 0.0), 3),
            "foliar_absorbed_k2o_kg_ha": round(foliar_absorption["absorbed"].get("K2O", 0.0), 3),
            "foliar_washoff_fraction": round(foliar_absorption["washoff_fraction"], 4),
            "available_n_kg_ha": round(nutrient_supply["N"], 3),
            "available_p2o5_kg_ha": round(nutrient_supply["P2O5"], 3),
            "available_k2o_kg_ha": round(nutrient_supply["K2O"], 3),
            "water_stress_risk": water_risk,
            "n_stress_risk": nutrient_status["N"],
            "p2o5_stress_risk": nutrient_status["P2O5"],
            "k2o_stress_risk": nutrient_status["K2O"],
            "nutrition_stress_risk": _worst_status(nutrient_status.values()),
            "disease_stress_risk": disease_risk,
            "insect_stress_risk": insect_risk,
            "weed_stress_risk": weed_risk,
            "disease_field_risk": disease_state.get("field_risk", disease_risk),
            "insect_field_risk": insect_state.get("field_risk", insect_risk),
            "weed_field_risk": weed_state.get("field_risk", weed_risk),
            "dominant_disease_target": disease_state.get("target_code"),
            "dominant_insect_target": insect_state.get("target_code"),
            "dominant_weed_target": weed_state.get("target_code"),
            "weed_target_codes": weed_state.get("target_codes") or [],
            "water_growth_factor": round(water_factor, 4),
            "transpiration_water_stress_index": round(1.0 - water_factor, 4),
            "nutrition_growth_factor": round(nutrition_factor, 4),
            "biotic_growth_factor": round(biotic_growth_factor, 4),
            "water_stress_index": round(float(water_diagnosis.get("stress_index", 1.0 - water_factor) or 0.0), 4),
            "n_stress_index": round(1.0 - nutrient_factors["N"], 4),
            "p_stress_index": round(1.0 - nutrient_factors["P2O5"], 4),
            "k_stress_index": round(1.0 - nutrient_factors["K2O"], 4),
            "disease_stress_index": round(disease_stress_index, 4),
            "insect_stress_index": round(insect_stress_index, 4),
            "weed_stress_index": round(weed_stress_index, 4),
            "disease_favorability": round(float(disease_state.get("favorability", 0.0) or 0.0), 4),
            "insect_favorability": round(float(insect_state.get("favorability", 0.0) or 0.0), 4),
            "disease_growth_factor": round(
                _biotic_growth_factor(
                    disease_stress_index,
                    0.0,
                    profile=profile,
                    stage_name=stage_name,
                    thermal_progress=thermal_progress,
                    bbch=bbch,
                ),
                4,
            ),
            "insect_growth_factor": round(
                _biotic_growth_factor(
                    0.0,
                    insect_stress_index,
                    profile=profile,
                    stage_name=stage_name,
                    thermal_progress=thermal_progress,
                    bbch=bbch,
                ),
                4,
            ),
            "biotic_leaf_damage_fraction": round(biotic_leaf_damage, 4),
            "combined_growth_factor": round(combined_factor, 4),
            "potential_growth_kg_ha": round(potential_growth, 3),
            "integrated_actual_growth_kg_ha": round(actual_growth, 3),
            "layered_soil_water": soil_water,
        }
        growth_regulation = _growth_regulation_state(
            payload=payload,
            profile=profile,
            day=day,
            iowa_stage=growth_regulation_stage,
            plant_population_m2=plant_population_m2,
            cumulative_applied_nutrients=_cumulative_applied_nutrients(applied_nutrient_events, day),
            seasonal_max_wind_m_s=seasonal_max_wind_m_s,
            applied_growth_regulators=applied_growth_regulators,
        )
        row.update(growth_regulation)
        row.update(
            _cotton_management_state(
                payload=payload,
                profile=profile,
                day=day,
                bbch=bbch,
                live_lai=lai,
                plant_population_m2=plant_population_m2,
                water_factor=water_factor,
                nutrition_factor=nutrition_factor,
                weather=weather,
                daily_weather=daily_weather,
                applied_growth_regulators=applied_growth_regulators,
                applied_toppings=applied_toppings,
                applied_harvest_aids=applied_harvest_aids,
                applied_harvests=applied_harvests,
            )
        )
        daily_rows.append(row)
        _append_daily_risk(daily_risk, row, nutrient_status, nutrient_demand_cum, nutrient_supply)

        if include_actions and day >= decision_date:
            _maybe_add_actions(
                actions=actions,
                action_ids=action_ids,
                open_episodes=open_episodes,
                emitted_domains=emitted_domains,
                payload=payload,
                profile=profile,
                day=day,
                row=row,
                nutrient_status=nutrient_status,
                soil_layers=soil_layers,
                root_depth=root_depth,
                applied_by_day={
                    "irrigation": applied_water_events,
                    "fertilization": applied_nutrient_events,
                    "fungicide": applied_fungicides,
                    "insecticide": applied_insecticides,
                    "herbicide": applied_herbicides,
                    "growth_regulator": applied_growth_regulators,
                    "topping": applied_toppings,
                    "harvest_aid": applied_harvest_aids,
                    "harvest": applied_harvests,
                },
            )
        _close_resolved_episodes(open_episodes, water_risk, nutrient_status, disease_risk, insect_risk, weed_risk)

    histories = {
        "fungicide": _history_before(payload.get("applied_fungicides") or [], decision_date, ("applied_date", "Date", "date")),
        "insecticide": _history_before(payload.get("applied_insecticides") or [], decision_date, ("Date", "date")),
        "herbicide": _history_before(payload.get("applied_herbicides") or [], decision_date, ("Date", "date", "applied_date")),
        "irrigation": _history_before(payload.get("applied_irrigations") or [], decision_date, ("date", "Date")),
        "fertilization": _history_before(payload.get("applied_fertilizers") or [], decision_date, ("Date", "date")),
        "fertigation": _history_before(payload.get("applied_fertigation") or [], decision_date, ("Date", "date")),
    }
    if growth_regulation_enabled:
        histories["growth_regulator"] = _history_before(payload.get("applied_growth_regulators") or [], decision_date, ("Date", "date", "applied_date"))
    if cotton_management_enabled:
        histories["topping"] = _history_before(payload.get("applied_toppings") or [], decision_date, ("Date", "date", "applied_date"))
        histories["harvest_aid"] = _history_before(payload.get("applied_harvest_aids") or [], decision_date, ("Date", "date", "applied_date"))
        histories["harvest"] = _history_before(payload.get("applied_harvests") or [], decision_date, ("Date", "date", "applied_date"))
    actions.sort(key=lambda item: (item["action_date"], item["domain"], item["id"]))
    counts = {
        domain: sum(1 for item in actions if item["domain"] == domain)
        for domain in (
            "irrigation",
            "fertilization",
            "fungicide",
            "insecticide",
            "herbicide",
            "growth_regulator",
            "topping",
            "harvest_aid",
            "harvest",
        )
    }
    summary = _summary_from_daily_rows(daily_rows, payload.get("_summary_outputs"))
    response_rows = _filter_daily_outputs(daily_rows, payload.get("_daily_outputs")) if filter_outputs else daily_rows
    hourly_outputs = _simulate_hourly_outputs(payload=payload, profile=profile)
    daily_risk["disease"] = biotic["daily_risk"]["disease"]
    daily_risk["insect"] = biotic["daily_risk"]["insect"]
    nutrition_stress_risk = _carried_nutrition_stress_risk(daily_risk["nutrition"], applied_nutrient_events)
    stress_risk = {
        "water": {"DROUGHT": daily_risk["water"]},
        "nutrition": nutrition_stress_risk,
        "disease": biotic["stress_risk"]["disease"],
        "insect": biotic["stress_risk"]["insect"],
        "weed": _weed_stress_risk(daily_risk["weed"]),
    }
    if profile.name == "maize" and growth_regulation_enabled:
        stress_risk["lodging"] = {"LODGING": daily_risk["lodging"]}
    else:
        daily_risk.pop("lodging", None)
    field_risk = _overall_field_risk(
        stress_risk,
        season_start=season_start,
        season_end=season_end,
    )
    response = {
        "crop_uuid": payload["crop_uuid"],
        "crop_season_uuid": payload.get("crop_season_uuid") or "",
        "planting_date": planting_date,
        "decision_date": decision_date,
        "season_start": season_start,
        "season_end": season_end,
        "histories": histories,
        "action_counts": counts,
        "management_actions": actions,
        "daily_risk": daily_risk,
        "stress_risk": stress_risk,
        "field_risk": field_risk,
        "integrated_daily_state": response_rows,
        "daily_state": response_rows,
        "summary": summary,
    }
    if hourly_outputs:
        response["hourly"] = hourly_outputs
    return response


def _normalize_simulation_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if "cropseason" not in payload:
        raise ValueError("fullseason simulation request requires cropseason/soil/weather/management/output")

    cropseason = _dump(payload["cropseason"])
    soil = _dump(payload["soil"])
    weather = _dump(payload["weather"])
    management = _dump(payload["management"])
    observations = _dump(payload.get("observations") or {})
    output = _dump(payload.get("output") or {})
    inventory = _normalize_inventory(_dump(management.get("inventory") or {}))
    applied = _normalize_applied_management(_dump(management.get("applied") or {}), inventory, management)
    variety = _dump(cropseason.get("variety") or {})
    crop_uuid = cropseason.get("crop_uuid") or _crop_uuid_for_name(cropseason.get("crop"))
    if crop_uuid == MAIZE_UUID and not str(variety.get("maturation_group") or "").strip():
        raise ValueError("cropseason.variety.maturation_group is required for maize")
    daily_weather = weather.get("daily") or []
    season_end = cropseason.get("season_end") or _last_weather_date(
        {"daily_weather_data": daily_weather}
    )
    if season_end is None:
        raise ValueError("cropseason.season_end or dated weather.daily data is required")
    stresses = _dump(management.get("stresses") or {})
    daily_outputs = output.get("daily") or DEFAULT_DAILY_OUTPUTS
    summary_outputs = output.get("summary") or DEFAULT_SUMMARY_OUTPUTS
    hourly_outputs = _normalize_hourly_outputs(output.get("hourly") or [])
    _validate_requested_outputs(daily_outputs, summary_outputs, hourly_outputs)

    return {
        "crop_uuid": crop_uuid,
        "crop_season_uuid": cropseason.get("crop_season_uuid") or "",
        "planting_date": cropseason["planting_date"],
        "season_start": cropseason.get("season_start") or cropseason["planting_date"],
        "season_end": season_end,
        "latitude": cropseason["latitude"],
        "longitude": cropseason["longitude"],
        "region_code": cropseason.get("region_code"),
        "yield_target_kg_ha": cropseason["yield_target_kg_ha"],
        "variety_characteristics": {
            "name": variety.get("name") or cropseason.get("crop") or "",
            "maturation_group": variety.get("maturation_group"),
            "phenology_profile": variety.get("phenology_profile"),
            "phenology": _dump(variety.get("phenology") or {}),
        },
        "variety_susceptibility": variety.get("susceptibility") or {},
        "daily_weather_data": daily_weather,
        "hourly_weather_data": weather.get("hourly") or [],
        "observations": observations,
        "soil_type": soil.get("texture") or "sandy_loam",
        "soil_texture": soil.get("texture") or "sandy_loam",
        "soil_profile": soil.get("layers") or [],
        "soil_test": soil.get("analysis") or {},
        "management_mode": management.get("mode"),
        "irrigation_method": management.get("irrigation_method") or "flood",
        "fertigation_enabled": bool(management.get("fertigation_enabled", False)),
        "mulch_enabled": bool(management.get("mulch_enabled", False)),
        "economic_parameters": management.get("economics") or {},
        "economics": management.get("economics") or {},
        "custom_products": inventory.get("custom_products") or [],
        "fertilizer_inventory": inventory.get("fertilizers") or [],
        "seed_inventory": inventory.get("seeds") or [],
        "fungicide_inventory": inventory.get("fungicides") or [],
        "insecticide_inventory": inventory.get("insecticides") or [],
        "herbicide_inventory": inventory.get("herbicides") or [],
        "growth_regulator_inventory": inventory.get("growth_regulators") or [],
        "harvest_aid_inventory": inventory.get("harvest_aids") or [],
        "product_inventory": inventory,
        "applied_sowings": applied.get("sowings") or applied.get("seedings") or [],
        "applied_fertilizers": applied.get("fertilizers") or [],
        "applied_fertigation": applied.get("fertigation") or [],
        "applied_irrigations": applied.get("irrigations") or [],
        "applied_fungicides": applied.get("fungicides") or [],
        "applied_insecticides": applied.get("insecticides") or [],
        "applied_herbicides": applied.get("herbicides") or [],
        "applied_growth_regulators": applied.get("growth_regulators") or [],
        "applied_toppings": applied.get("toppings") or [],
        "applied_harvest_aids": applied.get("harvest_aids") or [],
        "applied_harvests": applied.get("harvests") or [],
        "growth_regulation": management.get("growth_regulation") or {},
        "cotton_management": management.get("cotton_management") or {},
        "stress_eppo_codes": stresses.get("diseases") or [],
        "insect_eppo_codes": stresses.get("insects") or [],
        "weed_eppo_codes": _normalize_weed_codes(stresses.get("weeds") or []),
        "_enabled_stresses": _normalize_stresses(stresses),
        "_daily_outputs": daily_outputs,
        "_summary_outputs": summary_outputs,
        "_hourly_outputs": hourly_outputs,
    }


def _crop_uuid_for_name(name: Any) -> str:
    normalized = str(name or "maize").strip().lower()
    if normalized == "maize":
        return MAIZE_UUID
    if normalized == "wheat":
        return WHEAT_UUID
    if normalized == "cotton":
        return COTTON_UUID
    raise ValueError(f"Unsupported crop name: {name}")


def _normalize_inventory(raw: dict[str, Any]) -> dict[str, Any]:
    categories = ("fertilizers", "seeds", "fungicides", "insecticides", "herbicides", "growth_regulators", "harvest_aids")
    inventory = {key: [_normalize_inventory_product(item, key) for item in raw.get(key) or []] for key in categories}
    inventory["custom_products"] = list(raw.get("custom_products") or [])
    products_by_uuid = {}
    for items in inventory.values():
        if not isinstance(items, list):
            continue
        for item in items:
            uuid = item.get("uuid")
            if uuid:
                products_by_uuid[str(uuid)] = item
    inventory["_products_by_uuid"] = products_by_uuid
    return inventory


def _normalize_inventory_product(raw: Any, category: str) -> dict[str, Any]:
    if isinstance(raw, str):
        item: dict[str, Any] = {"name": raw, "display_name": raw, "product_key": raw}
    else:
        item = _dump(raw)
    item.setdefault("product_category", category[:-1] if category.endswith("s") else category)
    item.setdefault("product_key", item.get("name") or item.get("display_name") or item.get("ratio") or item.get("uuid"))
    item.setdefault("display_name", item.get("product_key"))
    if category == "fertilizers":
        ratio = _normalize_npk_ratio(item.get("npk_ratio") or item.get("ratio") or item)
        item["npk_ratio"] = {"n": ratio["N"] * 100.0, "p2o5": ratio["P2O5"] * 100.0, "k2o": ratio["K2O"] * 100.0}
        item["n_pct"] = item["npk_ratio"]["n"]
        item["p2o5_pct"] = item["npk_ratio"]["p2o5"]
        item["k2o_pct"] = item["npk_ratio"]["k2o"]
        if not item.get("product_key") or str(item.get("product_key")).count(":") == 2:
            item["product_key"] = item.get("display_name") or _display_fertilizer_product(str(item.get("ratio")))
    if category == "insecticides":
        item["product_key"] = _insecticide_product_key(item)
    if category == "growth_regulators":
        _validate_growth_regulator_product(item)
    if category == "harvest_aids":
        _validate_harvest_aid_product(item)
    return item


def _normalize_npk_ratio(raw: Any) -> dict[str, float]:
    if isinstance(raw, str) and raw.count(":") == 2:
        try:
            n, p, k = [float(part) / 100.0 for part in raw.replace("复合肥", "").split(":")]
            return {"N": n, "P2O5": p, "K2O": k}
        except ValueError:
            pass
    data = _dump(raw) if not isinstance(raw, str) else {}
    return {
        "N": max(0.0, float(data.get("N", data.get("n", data.get("n_pct", 0.0))) or 0.0) / 100.0),
        "P2O5": max(0.0, float(data.get("P2O5", data.get("p2o5", data.get("p2o5_pct", 0.0))) or 0.0) / 100.0),
        "K2O": max(0.0, float(data.get("K2O", data.get("k2o", data.get("k2o_pct", 0.0))) or 0.0) / 100.0),
    }


def _normalize_applied_management(applied: dict[str, Any], inventory: dict[str, Any], management: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return {
        "sowings": [_normalize_sowing_event(event, inventory) for event in applied.get("sowings") or applied.get("seedings") or []],
        "fertilizers": [_normalize_fertilizer_event(event, inventory) for event in applied.get("fertilizers") or []],
        "fertigation": [_normalize_fertigation_event(event, inventory, management) for event in applied.get("fertigation") or []],
        "irrigations": [_normalize_irrigation_event(event, management) for event in applied.get("irrigations") or []],
        "fungicides": [_normalize_fungicide_event(event, inventory) for event in applied.get("fungicides") or []],
        "insecticides": [_normalize_insecticide_event(event, inventory) for event in applied.get("insecticides") or []],
        "herbicides": [_normalize_herbicide_event(event, inventory) for event in applied.get("herbicides") or []],
        "growth_regulators": [_normalize_growth_regulator_event(event, inventory) for event in applied.get("growth_regulators") or []],
        "toppings": [_normalize_field_operation_event(event, "topping") for event in applied.get("toppings") or []],
        "harvest_aids": [_normalize_harvest_aid_event(event, inventory) for event in applied.get("harvest_aids") or []],
        "harvests": [_normalize_field_operation_event(event, "machine_harvest") for event in applied.get("harvests") or []],
    }


def _inventory_product(inventory: dict[str, Any], product_uuid: Any) -> dict[str, Any]:
    if not product_uuid:
        return {}
    return dict((inventory.get("_products_by_uuid") or {}).get(str(product_uuid)) or {})


def _merge_product(event: dict[str, Any], product: dict[str, Any]) -> dict[str, Any]:
    out = dict(event)
    if product:
        out.setdefault("product_uuid", product.get("uuid"))
        out.setdefault("product_key", product.get("product_key") or product.get("name"))
        out.setdefault("product_name", product.get("name"))
        out.setdefault("display_name", product.get("display_name"))
    return out


def _normalize_sowing_event(raw: Any, inventory: dict[str, Any]) -> dict[str, Any]:
    event = _dump(raw)
    return _merge_product(event, _inventory_product(inventory, event.get("product_uuid")))


def _normalize_fertilizer_event(raw: Any, inventory: dict[str, Any]) -> dict[str, Any]:
    event = _merge_product(_dump(raw), _inventory_product(inventory, _dump(raw).get("product_uuid")))
    event.setdefault("Date", event.get("date"))
    event.setdefault("date", event.get("Date"))
    if event.get("application_method") and not event.get("method"):
        event["method"] = event["application_method"]
    event["method"] = _normalize_fertilizer_method(event.get("method"))
    if event["method"] == "foliar":
        event.setdefault("application_method", "foliar_spray")
        event.setdefault("release_type", "quick_release")
        event.setdefault("release_days", 1)
    product = _inventory_product(inventory, event.get("product_uuid"))
    if product:
        event.setdefault("n_pct", product.get("n_pct"))
        event.setdefault("p2o5_pct", product.get("p2o5_pct"))
        event.setdefault("k2o_pct", product.get("k2o_pct"))
    event["nutrients_kg_ha"] = _canonical_nutrients(event.get("nutrients_kg_ha") or {})
    if event.get("micros_g_ha") and isinstance(event["micros_g_ha"], dict):
        event["micros_g_ha"] = {
            str(key): round(max(0.0, float(value or 0.0)), 3)
            for key, value in event["micros_g_ha"].items()
        }
    event.setdefault("target_nutrients", _event_target_nutrients(event))
    return event


def _normalize_fertigation_event(raw: Any, inventory: dict[str, Any], management: dict[str, Any]) -> dict[str, Any]:
    event = _normalize_fertilizer_event(raw, inventory)
    event["method"] = "fertigation"
    event["application_method"] = "fertigation"
    if event.get("irrigation_method") and not event.get("water_method"):
        event["water_method"] = event["irrigation_method"]
    event.setdefault("water_method", management.get("irrigation_method"))
    if event.get("amount_mm") is None and event.get("irrigation_mm") is not None:
        event["amount_mm"] = event.get("irrigation_mm")
    if event.get("irrigation_mm") is None and event.get("amount_mm") is not None:
        event["irrigation_mm"] = event.get("amount_mm")
    return event


def _normalize_irrigation_event(raw: Any, management: dict[str, Any]) -> dict[str, Any]:
    event = _dump(raw)
    if event.get("irrigation_method") and not event.get("method"):
        event["method"] = event["irrigation_method"]
    event.setdefault("method", management.get("irrigation_method"))
    return event


def _normalize_fungicide_event(raw: Any, inventory: dict[str, Any]) -> dict[str, Any]:
    event = _merge_product(_dump(raw), _inventory_product(inventory, _dump(raw).get("product_uuid")))
    app_day = _date_key(event.get("applied_date") or event.get("date") or event.get("Date"))
    if app_day is not None:
        event["applied_date"] = app_day
        event.setdefault("date", app_day.isoformat())
    targets = event.get("target_diseases") or []
    if targets and not event.get("stress"):
        event["stress"] = str(targets[0]).upper()
    window = (_inventory_product(inventory, event.get("product_uuid")).get("efficacy_window_days") or {})
    event.setdefault("preventive_protection_days", window.get("protective_days", 10))
    event.setdefault("curative_protection_days", window.get("curative_days", 0))
    event.setdefault("eradicative_protection_days", window.get("eradicative_days", 0))
    event.setdefault("preventive_efficacy", 0.82)
    event.setdefault("curative_efficacy", 0.55 if float(event.get("curative_protection_days") or 0.0) > 0 else 0.0)
    return event


def _normalize_insecticide_event(raw: Any, inventory: dict[str, Any]) -> dict[str, Any]:
    product = _inventory_product(inventory, _dump(raw).get("product_uuid"))
    event = _merge_product(_dump(raw), product)
    event["product_key"] = _insecticide_product_key(event)
    event.setdefault("Date", event.get("date") or event.get("applied_date"))
    event.setdefault("date", event.get("Date"))
    if event.get("application_method") and not event.get("method"):
        event["method"] = event["application_method"]
    window = product.get("efficacy_window_days") or {}
    event.setdefault("residual_control_days", window.get("residual_control_days", 14))
    if product.get("pesticide") and not event.get("pesticide"):
        event["pesticide"] = deepcopy(product["pesticide"])
    if not event.get("pesticide"):
        event["pesticide"] = deepcopy(_insecticide_params(PROFILES[MAIZE_UUID]).get(event["product_key"]) or {})
    if event.get("pesticide"):
        pesticide = event["pesticide"]
        event.setdefault("dose_unit", pesticide.get("dose_unit") or "g_ai_ha")
        label_rate = pesticide.get("label_rate_g_ai_ha") or pesticide.get("label_rate_ml_ai_mu") or pesticide.get("label_rate_g_ai_mu") or []
        if event.get("dose_value") is None and label_rate:
            if isinstance(label_rate, list) and len(label_rate) >= 2:
                event["dose_value"] = (float(label_rate[0]) + float(label_rate[1])) / 2.0
            else:
                event["dose_value"] = float(label_rate[0] if isinstance(label_rate, list) else label_rate)
    return event


def _normalize_herbicide_event(raw: Any, inventory: dict[str, Any]) -> dict[str, Any]:
    product = _inventory_product(inventory, _dump(raw).get("product_uuid"))
    event = _merge_product(_dump(raw), product)
    day = _date_key(event.get("Date") or event.get("date") or event.get("applied_date"))
    if day is not None:
        event["Date"] = day.isoformat()
        event.setdefault("date", day.isoformat())
        event.setdefault("applied_date", day.isoformat())
    if event.get("application_method") and not event.get("method"):
        event["method"] = event["application_method"]
    event.setdefault("application_method", event.get("method") or "foliar_spray")
    event.setdefault("application_timing", product.get("application_timing") or "post_emergence")
    event.setdefault("target_weeds", product.get("target_weeds") or [])
    window = product.get("efficacy_window_days") or {}
    event.setdefault("residual_control_days", window.get("residual_control_days", 21))
    event.setdefault("allow_tank_mix", product.get("allow_tank_mix", True))
    return event


def _validate_growth_regulator_product(item: dict[str, Any]) -> None:
    key = item.get("product_key") or item.get("uuid")
    if not item.get("uuid") or not key:
        raise ValueError("inventory.growth_regulators products require uuid and product_key")
    crops = {str(value).strip().lower() for value in item.get("registered_crops") or []}
    if not crops.intersection({"maize", "cotton"}):
        raise ValueError(f"growth regulator {key} must be registered for maize or cotton")
    if not item.get("active_ingredients") or not item.get("formulation"):
        raise ValueError(f"growth regulator {key} requires active_ingredients and formulation")
    if item.get("rainfast_hours") is None or float(item.get("rainfast_hours")) < 0.0:
        raise ValueError(f"growth regulator {key} requires non-negative rainfast_hours")
    if "allow_tank_mix" not in item:
        raise ValueError(f"growth regulator {key} requires allow_tank_mix")
    if str(item.get("application_method") or "") != "foliar_spray":
        raise ValueError(f"growth regulator {key} requires application_method=foliar_spray")
    if "cotton" in crops and "maize" not in crops:
        window = _dump(item.get("label_bbch_window") or item.get("label_stage_window") or {})
        bbch_min = int(window.get("min", 51))
        bbch_max = int(window.get("max", 79))
        if not 0 <= bbch_min <= bbch_max <= 99:
            raise ValueError(f"growth regulator {key} has an invalid cotton BBCH window")
        schedule = list(item.get("dose_schedule") or [])
        if not schedule:
            schedule = [{"bbch_min": bbch_min, "bbch_max": bbch_max, **_dump(item.get("label_dose") or {})}]
        normalized_schedule = []
        for entry in schedule:
            raw = _dump(entry)
            unit = str(raw.get("unit") or "")
            recommended = float(raw.get("recommended") or 0.0)
            low = float(raw.get("min", recommended) or 0.0)
            high = float(raw.get("max", recommended) or 0.0)
            entry_min = int(raw.get("bbch_min", bbch_min))
            entry_max = int(raw.get("bbch_max", bbch_max))
            if not unit or recommended <= 0.0 or low <= 0.0 or high < low or not low <= recommended <= high:
                raise ValueError(f"growth regulator {key} has an invalid cotton dose schedule")
            if not bbch_min <= entry_min <= entry_max <= bbch_max:
                raise ValueError(f"growth regulator {key} dose schedule is outside its cotton BBCH window")
            normalized_schedule.append({
                "bbch_min": entry_min,
                "bbch_max": entry_max,
                "min": low,
                "max": high,
                "recommended": recommended,
                "unit": unit,
            })
        item["label_bbch_window"] = {"min": bbch_min, "max": bbch_max}
        item["dose_schedule"] = normalized_schedule
        item["max_applications"] = max(1, int(item.get("max_applications") or len(normalized_schedule)))
        item["min_interval_days"] = max(1, int(item.get("min_interval_days") or 10))
        item.setdefault("effect_days", 10)
        item.setdefault("leaf_expansion_multiplier", 0.82)
    else:
        dose = _dump(item.get("label_dose") or {})
        if not dose.get("unit") or dose.get("recommended") is None:
            raise ValueError(f"growth regulator {key} requires label_dose.unit and label_dose.recommended")
        recommended = float(dose["recommended"])
        low = float(dose.get("min", recommended))
        high = float(dose.get("max", recommended))
        if recommended <= 0.0 or low <= 0.0 or high < low or not low <= recommended <= high:
            raise ValueError(f"growth regulator {key} has an invalid label dose")
        window = _dump(item.get("label_stage_window") or {})
        if _maize_stage_rank(window.get("min")) is None or _maize_stage_rank(window.get("max")) is None:
            raise ValueError(f"growth regulator {key} requires a valid Iowa label_stage_window")
        if _maize_stage_rank(window.get("min")) > _maize_stage_rank(window.get("max")):
            raise ValueError(f"growth regulator {key} has an invalid label_stage_window")
        item["label_dose"] = {"min": low, "max": high, "recommended": recommended, "unit": str(dose["unit"])}
        item["label_stage_window"] = {"min": str(window["min"]).upper(), "max": str(window["max"]).upper()}
    item.setdefault("allow_tank_mix", False)
    item.setdefault("priority", 100)


def _validate_harvest_aid_product(item: dict[str, Any]) -> None:
    key = item.get("product_key") or item.get("uuid")
    if not item.get("uuid") or not key:
        raise ValueError("inventory.harvest_aids products require uuid and product_key")
    crops = {str(value).strip().lower() for value in item.get("registered_crops") or []}
    if "cotton" not in crops:
        raise ValueError(f"harvest aid {key} must be registered for cotton")
    if not item.get("active_ingredients") or not item.get("formulation"):
        raise ValueError(f"harvest aid {key} requires active_ingredients and formulation")
    dose = _dump(item.get("label_dose") or {})
    unit = str(dose.get("unit") or "")
    recommended = float(dose.get("recommended") or 0.0)
    low = float(dose.get("min", recommended) or 0.0)
    high = float(dose.get("max", recommended) or 0.0)
    if not unit or recommended <= 0.0 or low <= 0.0 or high < low or not low <= recommended <= high:
        raise ValueError(f"harvest aid {key} has an invalid label dose")
    item["label_dose"] = {"min": low, "max": high, "recommended": recommended, "unit": unit}
    item["minimum_open_bolls_pct"] = max(0.0, min(100.0, float(item.get("minimum_open_bolls_pct", 30.0))))
    item["minimum_mean_temperature_c"] = float(item.get("minimum_mean_temperature_c", 12.0))
    item["max_wind_speed_m_s"] = max(0.0, float(item.get("max_wind_speed_m_s", 4.0)))
    item["rainfast_hours"] = max(0.0, float(item.get("rainfast_hours", 24.0)))
    item["response_days"] = max(1, int(item.get("response_days", 10)))
    item["preharvest_interval_days"] = max(item["response_days"], int(item.get("preharvest_interval_days", item["response_days"])))
    item["target_defoliation_pct"] = max(1.0, min(100.0, float(item.get("target_defoliation_pct", 90.0))))
    item.setdefault("application_method", "foliar_spray")
    item.setdefault("priority", 100)


def _normalize_growth_regulator_event(raw: Any, inventory: dict[str, Any]) -> dict[str, Any]:
    source = _dump(raw)
    product = _inventory_product(inventory, source.get("product_uuid"))
    if not product:
        raise ValueError("management.applied.growth_regulators references an unknown product_uuid")
    event = _merge_product(source, product)
    day = _date_key(event.get("Date") or event.get("date") or event.get("applied_date"))
    if day is None:
        raise ValueError("management.applied.growth_regulators event requires date")
    crops = {str(value).strip().lower() for value in product.get("registered_crops") or []}
    cotton_only = "cotton" in crops and "maize" not in crops
    if cotton_only:
        schedules = product.get("dose_schedule") or []
        units = {str(entry["unit"]) for entry in schedules}
        if len(units) != 1:
            raise ValueError(f"growth regulator {product['product_key']} cotton dose schedule must use one unit")
        unit = next(iter(units))
        low = min(float(entry["min"]) for entry in schedules)
        high = max(float(entry["max"]) for entry in schedules)
        default = float(schedules[0]["recommended"])
        dose = {"min": low, "max": high, "recommended": default, "unit": unit}
    else:
        dose = product["label_dose"]
    value = float(event.get("dose_value", dose["recommended"]))
    unit = str(event.get("dose_unit") or dose["unit"])
    if not isfinite(value) or value <= 0.0 or not unit:
        raise ValueError(f"growth regulator {product['product_key']} applied dose must be positive and have a unit")
    if cotton_only and (unit != dose["unit"] or not dose["min"] <= value <= dose["max"]):
        raise ValueError(f"growth regulator {product['product_key']} dose must be within its label range and unit")
    event.update({
        "Date": day.isoformat(),
        "date": day.isoformat(),
        "applied_date": day.isoformat(),
        "dose_value": value,
        "dose_unit": unit,
        "application_method": event.get("application_method") or product.get("application_method") or "foliar_spray",
        "method": "foliar_spray",
        "target": "CANOPY_VIGOR" if cotton_only else "LODGING",
        "allow_tank_mix": bool(product.get("allow_tank_mix", False)),
        "label_stage_window": deepcopy(product.get("label_stage_window") or product.get("label_bbch_window") or {}),
    })
    return event


def _normalize_harvest_aid_event(raw: Any, inventory: dict[str, Any]) -> dict[str, Any]:
    source = _dump(raw)
    product = _inventory_product(inventory, source.get("product_uuid"))
    if not product:
        raise ValueError("management.applied.harvest_aids references an unknown product_uuid")
    event = _merge_product(source, product)
    day = _date_key(event.get("Date") or event.get("date") or event.get("applied_date"))
    if day is None:
        raise ValueError("management.applied.harvest_aids event requires date")
    dose = product["label_dose"]
    value = float(event.get("dose_value", dose["recommended"]))
    unit = str(event.get("dose_unit") or dose["unit"])
    if unit != dose["unit"] or not dose["min"] <= value <= dose["max"]:
        raise ValueError(f"harvest aid {product['product_key']} dose must be within its label range and unit")
    event.update({
        "Date": day.isoformat(),
        "date": day.isoformat(),
        "applied_date": day.isoformat(),
        "dose_value": value,
        "dose_unit": unit,
        "application_method": product.get("application_method") or "foliar_spray",
        "method": "foliar_spray",
        "target": "DEFOLIATION_RIPENING",
    })
    return event


def _normalize_field_operation_event(raw: Any, operation: str) -> dict[str, Any]:
    event = _dump(raw)
    day = _date_key(event.get("Date") or event.get("date") or event.get("applied_date"))
    if day is None:
        raise ValueError(f"management.applied.{operation} event requires date")
    event.update({"Date": day.isoformat(), "date": day.isoformat(), "method": operation, "operation": operation})
    return event


def _validate_applied_growth_regulator_stages(
    events_by_day: dict[date, list[dict[str, Any]]],
    phenology_by_day: dict[date, dict[str, Any]],
    profile: CropProfile,
) -> None:
    if profile.name == "cotton":
        ordered_events = [(event_day, event) for event_day, events in sorted(events_by_day.items()) for event in events]
        if not ordered_events:
            return
        max_applications = min(int(event.get("max_applications") or 1) for _day, event in ordered_events)
        if len(ordered_events) > max_applications:
            raise ValueError(f"cotton growth regulation permits at most {max_applications} applications for the selected product")
        previous_day: date | None = None
        for event_day, event in ordered_events:
            bbch = int((phenology_by_day.get(event_day) or {}).get("BBCH") or 0)
            window = event.get("label_bbch_window") or event.get("label_stage_window") or {}
            if not int(window.get("min", 0)) <= bbch <= int(window.get("max", 99)):
                raise ValueError(f"growth regulator {event.get('product_key')} application on {event_day} is outside its cotton BBCH label window")
            interval = int(event.get("min_interval_days") or 1)
            if previous_day is not None and (event_day - previous_day).days < interval:
                raise ValueError(f"growth regulator {event.get('product_key')} applications must be at least {interval} days apart")
            previous_day = event_day
        return

    # Maize applications in ``management.applied`` are field history, not new
    # prescriptions. Preserve those observations even when they differ from
    # the current inventory label or the model's recommendation window. The
    # inventory and recommendation paths remain label- and stage-constrained.
    return


def _canonical_nutrients(raw: dict[str, Any]) -> dict[str, float]:
    return {
        "N": float(raw.get("N", raw.get("n", 0.0)) or 0.0),
        "P2O5": float(raw.get("P2O5", raw.get("p2o5", 0.0)) or 0.0),
        "K2O": float(raw.get("K2O", raw.get("k2o", 0.0)) or 0.0),
    }


def _insecticide_product_key(item: dict[str, Any]) -> str:
    key = str(item.get("product_key") or item.get("name") or item.get("active_ingredient") or "")
    if key == "chlorantraniliprole":
        return "chlorantraniliprole_200SC"
    return key or "chlorantraniliprole_200SC"


def _normalize_stresses(stresses: dict[str, Any]) -> dict[str, Any]:
    nutrition_raw = stresses.get("nutrition")
    nutrient_map = {"N": "N", "P": "P2O5", "P2O5": "P2O5", "K": "K2O", "K2O": "K2O"}
    nutrients: set[str] = set()
    for item in nutrition_raw or []:
        key = str(item).upper()
        if key not in nutrient_map:
            raise ValueError(f"Unsupported nutrition stress: {item}")
        nutrients.add(nutrient_map[key])
    water_raw = stresses.get("water")
    water = bool(water_raw) and str(water_raw).lower() not in {"false", "none", "no", "0"}
    return {
        "water": water,
        "nutrition": nutrients,
        "disease": bool(stresses.get("diseases")),
        "insect": bool(stresses.get("insects")),
        "weed": bool(_normalize_weed_codes(stresses.get("weeds") or [])),
    }


def _enabled_stresses(payload: dict[str, Any]) -> dict[str, Any]:
    return payload.get("_enabled_stresses") or {"water": False, "nutrition": set(), "disease": False, "insect": False, "weed": False}


def _normalize_weed_codes(raw: Any) -> list[str]:
    if raw is None:
        return []
    values = raw if isinstance(raw, list) else [raw]
    codes: list[str] = []
    for item in values:
        code = str(item or "").strip().upper()
        if not code:
            continue
        if code not in MAIZE_POST_WEED_CODES:
            raise ValueError(f"Unsupported weed target_code: {item}")
        if code not in codes:
            codes.append(code)
    return codes


DAILY_OUTPUT_MAP = {
    "date": "Date",
    "days_after_planting": "days_after_planting",
    "growth_stage": "Stage",
    "thermal_time_c": "thermal_time_c",
    "cumulative_thermal_time_c": "cumulative_thermal_time_c",
    "active_temperature_c": "active_temperature_c",
    "cumulative_active_temperature_c": "cumulative_active_temperature_c",
    "effective_temperature_c": "effective_temperature_c",
    "cumulative_effective_temperature_c": "cumulative_effective_temperature_c",
    "lai": "lai",
    "green_lai": "live_lai",
    "senesced_lai": "dead_lai",
    "aboveground_biomass_kg_ha": "total_biomass_kg_ha",
    "dead_biomass_kg_ha": "dead_aboveground_biomass_kg_ha",
    "grain_yield_kg_ha": "grain_weight_kg_ha",
    "harvest_index": "harvest_index",
    "root_depth_m": "root_depth_m",
    "rain_mm": "precipitation_mm",
    "irrigation_mm": "irrigation_mm",
    "root_zone_available_water_mm": "root_zone_available_water_mm",
    "root_zone_capacity_mm": "root_zone_capacity_mm",
    "root_zone_relative_available_water": "root_zone_relative_available_water",
    "available_water_mm": "available_water_mm",
    "soil_water_mm": "soil_water_mm",
    "aet_mm": "actual_et_mm",
    "pet_mm": "potential_et_mm",
    "drainage_mm": "drainage_mm",
    "water_stress_index": "water_stress_index",
    "n_stress_index": "n_stress_index",
    "p_stress_index": "p_stress_index",
    "k_stress_index": "k_stress_index",
    "disease_stress_index": "disease_stress_index",
    "insect_stress_index": "insect_stress_index",
    "weed_stress_index": "weed_stress_index",
    "disease_favorability": "disease_favorability",
    "insect_favorability": "insect_favorability",
    "disease_field_risk": "disease_field_risk",
    "insect_field_risk": "insect_field_risk",
    "weed_field_risk": "weed_field_risk",
    "disease_risk": "disease_stress_risk",
    "insect_risk": "insect_stress_risk",
    "weed_risk": "weed_stress_risk",
    "lodging_risk_index_raw": "lodging_risk_index_raw",
    "lodging_risk_index": "lodging_risk_index",
    "lodging_risk": "lodging_risk",
    "growth_regulation_status": "growth_regulation_status",
    "growth_regulation_basis": "growth_regulation_basis",
    "grain_sink_kg_ha": "grain_sink_kg_ha",
    "grain_number_m2": "grain_number_m2",
    "nutrient_supported_grain_yield_kg_ha": "nutrient_supported_grain_yield_kg_ha",
    "n_demand_kg_ha": "n_demand_kg_ha",
    "p2o5_demand_kg_ha": "p2o5_demand_kg_ha",
    "k2o_demand_kg_ha": "k2o_demand_kg_ha",
    "cumulative_n_demand_kg_ha": "cumulative_n_demand_kg_ha",
    "cumulative_p2o5_demand_kg_ha": "cumulative_p2o5_demand_kg_ha",
    "cumulative_k2o_demand_kg_ha": "cumulative_k2o_demand_kg_ha",
    "n_uptake_kg_ha": "n_uptake_kg_ha",
    "p2o5_uptake_kg_ha": "p2o5_uptake_kg_ha",
    "k2o_uptake_kg_ha": "k2o_uptake_kg_ha",
    "cumulative_n_uptake_kg_ha": "cumulative_n_uptake_kg_ha",
    "cumulative_p2o5_uptake_kg_ha": "cumulative_p2o5_uptake_kg_ha",
    "cumulative_k2o_uptake_kg_ha": "cumulative_k2o_uptake_kg_ha",
    "n_volatilization_loss_kg_ha": "n_volatilization_loss_kg_ha",
    "n_leaching_loss_kg_ha": "n_leaching_loss_kg_ha",
    "cumulative_n_volatilization_loss_kg_ha": "cumulative_n_volatilization_loss_kg_ha",
    "cumulative_n_leaching_loss_kg_ha": "cumulative_n_leaching_loss_kg_ha",
    "cumulative_n_loss_kg_ha": "cumulative_n_loss_kg_ha",
    "available_n_kg_ha": "available_n_kg_ha",
    "available_p2o5_kg_ha": "available_p2o5_kg_ha",
    "available_k2o_kg_ha": "available_k2o_kg_ha",
    "canopy_vigor_index": "canopy_vigor_index",
    "cotton_canopy_regulation_status": "cotton_canopy_regulation_status",
    "topping_status": "topping_status",
    "estimated_open_bolls_pct": "estimated_open_bolls_pct",
    "estimated_defoliation_pct": "estimated_defoliation_pct",
    "harvest_aid_status": "harvest_aid_status",
    "machine_harvest_status": "machine_harvest_status",
}

SUMMARY_OUTPUTS = set(DEFAULT_SUMMARY_OUTPUTS)
HOURLY_OUTPUTS = {"spray_weather"}


def _validate_requested_outputs(daily_outputs: list[str], summary_outputs: list[str], hourly_outputs: list[dict[str, Any]] | None = None) -> None:
    unknown_daily = sorted(set(daily_outputs) - set(DAILY_OUTPUT_MAP))
    unknown_summary = sorted(set(summary_outputs) - SUMMARY_OUTPUTS)
    hourly_names = {name for item in hourly_outputs or [] for name in item}
    unknown_hourly = sorted(hourly_names - HOURLY_OUTPUTS)
    if unknown_daily:
        raise ValueError(f"Unknown output.daily variable(s): {', '.join(unknown_daily)}")
    if unknown_summary:
        raise ValueError(f"Unknown output.summary variable(s): {', '.join(unknown_summary)}")
    if unknown_hourly:
        raise ValueError(f"Unknown output.hourly variable(s): {', '.join(unknown_hourly)}")


def _normalize_hourly_outputs(raw_outputs: list[Any]) -> list[dict[str, Any]]:
    outputs: list[dict[str, Any]] = []
    for raw in raw_outputs or []:
        item = _dump(raw)
        if not item:
            continue
        normalized_item: dict[str, Any] = {}
        for name, config in item.items():
            normalized_item[str(name)] = _dump(config) if isinstance(config, dict) else {}
        outputs.append(normalized_item)
    return outputs


def _hourly_output_config(payload: dict[str, Any], name: str) -> dict[str, Any] | None:
    for item in payload.get("_hourly_outputs") or []:
        if name in item:
            return item.get(name) or {}
    return None


def _simulate_hourly_outputs(*, payload: dict[str, Any], profile: CropProfile) -> dict[str, Any]:
    outputs: dict[str, Any] = {}
    spray_config = _hourly_output_config(payload, "spray_weather")
    if spray_config is not None:
        outputs["spray_weather"] = _simulate_spray_weather_output(payload=payload, profile=profile, config=spray_config)
    return outputs


def _simulate_spray_weather_output(*, payload: dict[str, Any], profile: CropProfile, config: dict[str, Any]) -> list[dict[str, Any]]:
    hourly_rows = payload.get("hourly_weather_data") or []
    if not hourly_rows:
        raise ValueError("weather.hourly is required when output.hourly.spray_weather is requested")

    from crops.maize.spray_weather import MaizeSprayWeather

    spray_weather_config = _spray_weather_config(profile)

    mode = str(config.get("mode") or "drone").lower()
    weather_df = _biotic_hourly_dataframe(hourly_rows)
    result = MaizeSprayWeather(config=spray_weather_config, mode=mode).evaluate(weather_df)
    if "overall_score" not in result.columns and "total_score" in result.columns:
        result["overall_score"] = result["total_score"]
    return _hourly_records_safe(result)


def _spray_weather_config(profile: CropProfile) -> dict[str, Any]:
    if profile.name == "maize":
        from crops.maize.config import spray_weather_config
    elif profile.name == "wheat":
        from crops.wheat.config import spray_weather_config
    elif profile.name == "cotton":
        from crops.cotton.config import spray_weather_config
    else:
        raise ValueError(f"{profile.name} spray_weather output is not supported")
    return spray_weather_config


def _disease_model_class(profile: CropProfile):
    if profile.name == "wheat":
        from crops.wheat.disease import WheatDiseaseModel

        return WheatDiseaseModel
    if profile.name == "cotton":
        from crops.cotton.disease import CottonDiseaseModel

        return CottonDiseaseModel
    raise ValueError(f"{profile.name} disease model is not configured")


def _insect_model_class(profile: CropProfile):
    if profile.name == "wheat":
        from crops.wheat.insect import WheatInsectModel

        return WheatInsectModel
    if profile.name == "cotton":
        from crops.cotton.insect import CottonInsectModel

        return CottonInsectModel
    raise ValueError(f"{profile.name} insect model is not configured")


def _hourly_records_safe(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    safe = df.replace({float("inf"): None, float("-inf"): None}).where(pd.notna(df), None)
    records = safe.to_dict(orient="records")
    for row in records:
        for key, value in list(row.items()):
            if isinstance(value, pd.Timestamp):
                row[key] = value.isoformat()
            elif isinstance(value, datetime):
                row[key] = value.isoformat()
            elif isinstance(value, date):
                row[key] = value.isoformat()
    return records


def _filter_daily_outputs(rows: list[dict[str, Any]], requested: list[str] | None) -> list[dict[str, Any]]:
    requested = requested or DEFAULT_DAILY_OUTPUTS
    _validate_requested_outputs(requested, [])
    filtered = []
    for row in rows:
        item = {}
        for public_name in requested:
            item[public_name] = row.get(DAILY_OUTPUT_MAP[public_name])
        filtered.append(item)
    return filtered


def _summary_from_daily_rows(rows: list[dict[str, Any]], requested: list[str] | None) -> dict[str, Any]:
    requested = requested or DEFAULT_SUMMARY_OUTPUTS
    _validate_requested_outputs([], requested)
    if not rows:
        return {key: None for key in requested}
    values = {
        "final_yield_kg_ha": rows[-1].get("grain_weight_kg_ha"),
        "max_lai": round(max(float(row.get("lai", 0.0) or 0.0) for row in rows), 3),
        "total_aboveground_biomass_kg_ha": rows[-1].get("total_biomass_kg_ha"),
        "total_rain_mm": round(sum(float(row.get("precipitation_mm", 0.0) or 0.0) for row in rows), 3),
        "total_irrigation_mm": round(sum(float(row.get("irrigation_mm", 0.0) or 0.0) for row in rows), 3),
        "season_water_stress": round(sum(float(row.get("water_stress_index", 0.0) or 0.0) for row in rows) / len(rows), 4),
        "season_nutrition_stress": round(sum(1.0 - float(row.get("nutrition_growth_factor", 1.0) or 1.0) for row in rows) / len(rows), 4),
        "season_disease_stress": round(sum(float(row.get("disease_stress_index", 0.0) or 0.0) for row in rows) / len(rows), 4),
        "season_insect_stress": round(sum(float(row.get("insect_stress_index", 0.0) or 0.0) for row in rows) / len(rows), 4),
        "final_harvest_index": rows[-1].get("harvest_index"),
        "final_grain_sink_kg_ha": rows[-1].get("grain_sink_kg_ha"),
        "total_n_uptake_kg_ha": rows[-1].get("cumulative_n_uptake_kg_ha"),
        "total_p2o5_uptake_kg_ha": rows[-1].get("cumulative_p2o5_uptake_kg_ha"),
        "total_k2o_uptake_kg_ha": rows[-1].get("cumulative_k2o_uptake_kg_ha"),
        "total_n_loss_kg_ha": rows[-1].get("cumulative_n_loss_kg_ha"),
    }
    return {key: values.get(key) for key in requested}


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)[:10]).date()


def _date_key(value: Any) -> date | None:
    if value is None:
        return None
    try:
        return _as_date(value)
    except Exception:
        return None


def _date_range(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _dump(item: Any) -> dict[str, Any]:
    if hasattr(item, "model_dump"):
        return item.model_dump(mode="json")
    if isinstance(item, dict):
        return dict(item)
    return dict(item)


def _last_weather_date(payload: dict[str, Any]) -> date | None:
    dates = [_date_key((_dump(row)).get("DateTime") or (_dump(row)).get("Date")) for row in payload.get("daily_weather_data") or []]
    dates = [item for item in dates if item is not None]
    return max(dates) if dates else None


def _daily_weather_map(rows: list[Any]) -> dict[date, dict[str, float]]:
    out = {}
    for raw in rows:
        item = _dump(raw)
        day = _date_key(item.get("DateTime") or item.get("Date"))
        if day is None:
            continue
        out[day] = {
            "temperature_2m_mean": float(item.get("temperature_2m_mean", item.get("temperature_2m", 20.0)) or 20.0),
            "temperature_2m_min": float(item.get("temperature_2m_min", item.get("temperature_2m_mean", 20.0)) or 20.0),
            "temperature_2m_max": float(item.get("temperature_2m_max", item.get("temperature_2m_mean", 20.0)) or 20.0),
            "precipitation_sum": float(item.get("precipitation_sum", item.get("precipitation", 0.0)) or 0.0),
            "shortwave_radiation_sum": float(item.get("shortwave_radiation_sum", item.get("shortwave_radiation", 18.0)) or 18.0),
            "windspeed_10m_mean": float(item.get("windspeed_10m_mean", item.get("wind_speed_10m", item.get("windspeed_10m", 2.0))) or 2.0),
            "windspeed_10m_min": float(item.get("windspeed_10m_min", item.get("windspeed_10m_mean", 0.0)) or 0.0),
            "windspeed_10m_max": float(item.get("windspeed_10m_max", item.get("windspeed_10m_mean", item.get("wind_speed_10m", 2.0))) or 2.0),
            "relative_humidity_2m_mean": float(item.get("relative_humidity_2m_mean", item.get("relativehumidity_2m", 65.0)) or 65.0),
        }
    return out


def _hourly_weather_by_day(rows: list[Any]) -> dict[date, list[dict[str, Any]]]:
    out: dict[date, list[dict[str, Any]]] = {}
    for raw in rows:
        item = _dump(raw)
        day = _date_key(item.get("DateTime"))
        if day is not None:
            out.setdefault(day, []).append(item)
    return out


def _daily_from_hourly(day: date, rows: list[dict[str, Any]]) -> dict[str, float]:
    if not rows:
        return {
            "temperature_2m_mean": 20.0,
            "temperature_2m_min": 16.0,
            "temperature_2m_max": 26.0,
            "precipitation_sum": 0.0,
            "shortwave_radiation_sum": 18.0,
            "windspeed_10m_mean": 2.0,
            "relative_humidity_2m_mean": 65.0,
        }
    temps = [float(row.get("temperature_2m", row.get("temperature_2m_mean", 20.0)) or 20.0) for row in rows]
    rhs = [float(row.get("relativehumidity_2m", row.get("relative_humidity_2m", 65.0)) or 65.0) for row in rows]
    winds = [float(row.get("windspeed_10m", row.get("wind_speed_10m", 2.0)) or 2.0) for row in rows]
    return {
        "temperature_2m_mean": sum(temps) / len(temps),
        "temperature_2m_min": min(temps),
        "temperature_2m_max": max(temps),
        "precipitation_sum": sum(float(row.get("precipitation", 0.0) or 0.0) for row in rows),
        "shortwave_radiation_sum": sum(float(row.get("shortwave_radiation", 0.0) or 0.0) for row in rows) or 18.0,
        "windspeed_10m_mean": sum(winds) / len(winds),
        "relative_humidity_2m_mean": sum(rhs) / len(rhs),
    }


def _growth_stage_observation_error(record: dict[str, Any], reason: str) -> ValueError:
    index = record.get("record_index", "?")
    day = record.get("date") or record.get("Date") or record.get("DateTime") or "?"
    value = record.get("observed_value")
    return ValueError(
        f"observations.growth_stage.records[{index}] date {day} "
        f"observed_value {value!r}: {reason}"
    )


def _normalize_growth_stage_observations(
    observations: dict[str, Any],
    *,
    profile: CropProfile,
    season_start: date,
    season_end: date,
    decision_date: date | None = None,
) -> list[dict[str, Any]]:
    raw = _dump(observations or {}).get("growth_stage") or {}
    growth_stage = _dump(raw)
    standard = str(growth_stage.get("standard") or "").strip().lower()
    records = growth_stage.get("records") or []
    if not records:
        return []
    if not standard:
        raise ValueError("observations.growth_stage.standard is required when records are present")
    if standard == "zadocs":
        standard = "zadoks"
    if standard not in {"iowa", "bbch", "zadoks"}:
        raise ValueError(
            f"observations.growth_stage.standard {standard!r} is not supported; "
            "expected one of: bbch, iowa, zadoks"
        )
    if standard == "iowa" and profile.name != "maize":
        raise ValueError(f"observations.growth_stage.standard 'iowa' is not supported for crop {profile.name!r}")
    if standard == "zadoks" and profile.name != "wheat":
        raise ValueError(f"observations.growth_stage.standard 'zadoks' is not supported for crop {profile.name!r}")

    normalized: list[dict[str, Any]] = []
    for record_index, record in enumerate(records):
        item = _dump(record)
        item["record_index"] = record_index
        day = _date_key(item.get("date") or item.get("Date") or item.get("DateTime"))
        if day is None:
            raise _growth_stage_observation_error(item, "date is invalid")
        item["date"] = day
        if day < season_start or day > season_end:
            raise _growth_stage_observation_error(
                item,
                f"date is outside simulation season {season_start.isoformat()}..{season_end.isoformat()}",
            )
        if decision_date is not None and day > decision_date:
            raise _growth_stage_observation_error(
                item,
                f"date is after decision_date {decision_date.isoformat()}",
            )
        target_progress = _growth_stage_observation_progress(profile, standard, item.get("observed_value"))
        if target_progress is None:
            raise _growth_stage_observation_error(
                item,
                f"value is not valid for standard {standard!r} and crop {profile.name!r}",
            )
        normalized.append(
            {
                "date": day,
                "target_progress": float(target_progress),
                "observed_value": item.get("observed_value"),
                "record_index": record_index,
                "source": "field",
            }
        )

    by_date: dict[date, dict[str, Any]] = {}
    for item in normalized:
        previous = by_date.get(item["date"])
        if previous is None:
            by_date[item["date"]] = item
            continue
        if abs(float(previous["target_progress"]) - float(item["target_progress"])) > 1e-9:
            raise _growth_stage_observation_error(
                item,
                f"conflicts with records[{previous['record_index']}] on the same date",
            )

    latest_by_stage: dict[float, dict[str, Any]] = {}
    for item in sorted(by_date.values(), key=lambda record: (record["date"], record["record_index"])):
        stage_key = round(float(item["target_progress"]), 12)
        latest_by_stage[stage_key] = item

    cleaned = sorted(latest_by_stage.values(), key=lambda record: (record["date"], record["record_index"]))
    previous: dict[str, Any] | None = None
    for item in cleaned:
        if previous is not None and float(item["target_progress"]) <= float(previous["target_progress"]) + 1e-12:
            raise _growth_stage_observation_error(
                item,
                f"stage regresses from records[{previous['record_index']}] "
                f"date {previous['date']} observed_value {previous['observed_value']!r}",
            )
        previous = item
    return cleaned


def _growth_stage_observation_progress(profile: CropProfile, standard: str, value: Any) -> float | None:
    if standard == "iowa":
        return _maize_iowa_stage_progress(profile, value) if profile.name == "maize" else None
    if standard == "bbch":
        progress = _profile_bbch_progress(profile, value)
        return progress if progress is not None else _profile_stage_midpoint_progress(profile, value)
    if standard == "zadoks":
        if profile.name != "wheat":
            return None
        progress = _profile_bbch_progress(profile, value)
        return progress if progress is not None else _profile_stage_midpoint_progress(profile, value)
    return None


def _profile_stage_midpoint_progress(profile: CropProfile, value: Any) -> float | None:
    label = str(value or "").strip()
    normalized_label = _stage_label_key(label)
    phases = list(profile.phenology_phases)
    labels = [_stage_label_key(stage) for stage, _, _ in phases]
    if normalized_label not in labels:
        return None

    index = labels.index(normalized_label)
    start = 0.0 if index == 0 else float(phases[index][1])
    if index + 1 < len(phases):
        end = float(phases[index + 1][1])
        return max(0.0, min(1.0, (start + end) / 2.0))
    return 1.0


def _maize_iowa_stage_progress(profile: CropProfile, value: Any) -> float | None:
    label = _maize_stage_label(value)
    if not label:
        return None
    return _maize_iowa_stage_threshold(profile, MAIZE_IOWA_STAGE_RANK[label])


def _maize_iowa_stage_threshold(profile: CropProfile, rank: int) -> float:
    wanted = MAIZE_IOWA_STAGE_ORDER[rank]
    for stage, threshold, _ in profile.phenology_phases:
        if _maize_stage_label(stage) == wanted:
            return float(threshold)
    raise ValueError(f"maize phenology profile is missing Iowa stage {wanted}")


def _maize_iowa_stage_for_progress(profile: CropProfile, progress: float) -> str:
    if profile.name != "maize":
        return ""
    bounded = max(0.0, min(1.0, float(progress)))
    current = MAIZE_IOWA_STAGE_ORDER[0]
    for rank, label in enumerate(MAIZE_IOWA_STAGE_ORDER):
        if bounded + 1e-12 < _maize_iowa_stage_threshold(profile, rank):
            break
        current = label
    return current


def _stage_label_key(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def _profile_bbch_progress(profile: CropProfile, value: Any) -> float | None:
    try:
        observed_bbch = float(value)
    except (TypeError, ValueError):
        return None
    if observed_bbch < 0.0:
        return None

    previous_threshold = 0.0
    previous_bbch = 0.0
    phases = list(profile.phenology_phases)
    final_bbch = float(phases[-1][2])
    if observed_bbch > final_bbch:
        return None

    for _, threshold, bbch in phases:
        threshold = float(threshold)
        bbch = float(bbch)
        if observed_bbch <= bbch:
            span = max(bbch - previous_bbch, 1e-9)
            fraction = max(0.0, min(1.0, (observed_bbch - previous_bbch) / span))
            return max(0.0, min(1.0, previous_threshold + fraction * (threshold - previous_threshold)))
        previous_threshold = threshold
        previous_bbch = bbch
    return 1.0


def _calibrate_phenology_requirements(
    *,
    profile: CropProfile,
    maturity_thermal_time: float,
    raw_cumulative_by_date: dict[date, float],
    observations: list[dict[str, Any]],
) -> tuple[float, tuple[tuple[str, float, int], ...], list[dict[str, Any]]]:
    configured = [
        {
            "stage": stage,
            "configured_cumulative_gdd": float(threshold) * maturity_thermal_time,
            "bbch": int(bbch),
        }
        for stage, threshold, bbch in profile.phenology_phases
    ]
    if not observations:
        diagnostics = []
        previous = 0.0
        for item in configured:
            cumulative = float(item["configured_cumulative_gdd"])
            diagnostics.append(
                {
                    **item,
                    "calibrated_cumulative_gdd": cumulative,
                    "configured_delta_gdd": cumulative - previous,
                    "calibrated_delta_gdd": cumulative - previous,
                    "after_last_observation": True,
                }
            )
            previous = cumulative
        return maturity_thermal_time, profile.phenology_phases, diagnostics

    anchors: list[dict[str, Any]] = [
        {
            "date": None,
            "base_requirement_gdd": 0.0,
            "observed_requirement_gdd": 0.0,
            "record_index": "origin",
            "observed_value": "season_start",
        }
    ]
    for observation in sorted(observations, key=lambda item: item["date"]):
        raw_requirement = raw_cumulative_by_date.get(observation["date"])
        if raw_requirement is None:
            raise _growth_stage_observation_error(observation, "date has no weather-derived cumulative GDD")
        anchors.append(
            {
                **observation,
                "base_requirement_gdd": float(observation["target_progress"]) * maturity_thermal_time,
                "observed_requirement_gdd": float(raw_requirement),
            }
        )

    for previous, current in zip(anchors, anchors[1:]):
        base_delta = float(current["base_requirement_gdd"]) - float(previous["base_requirement_gdd"])
        observed_delta = float(current["observed_requirement_gdd"]) - float(previous["observed_requirement_gdd"])
        if base_delta <= 1e-9:
            raise _growth_stage_observation_error(
                current,
                "stage requirement must be greater than the preceding retained observation",
            )
        if observed_delta <= 1e-9:
            raise _growth_stage_observation_error(
                current,
                "weather-derived GDD interval must be positive",
            )

    last_anchor = anchors[-1]

    def calibrated_requirement(configured_requirement: float) -> float:
        previous_anchor = anchors[0]
        for current_anchor in anchors[1:]:
            if configured_requirement <= float(current_anchor["base_requirement_gdd"]) + 1e-9:
                configured_interval = (
                    float(current_anchor["base_requirement_gdd"])
                    - float(previous_anchor["base_requirement_gdd"])
                )
                observed_interval = (
                    float(current_anchor["observed_requirement_gdd"])
                    - float(previous_anchor["observed_requirement_gdd"])
                )
                relative_delta = configured_requirement - float(previous_anchor["base_requirement_gdd"])
                return float(previous_anchor["observed_requirement_gdd"]) + (
                    relative_delta / configured_interval
                ) * observed_interval
            previous_anchor = current_anchor
        return float(last_anchor["observed_requirement_gdd"]) + (
            configured_requirement - float(last_anchor["base_requirement_gdd"])
        )

    calibrated_absolute = [
        calibrated_requirement(float(item["configured_cumulative_gdd"]))
        for item in configured
    ]
    calibrated_maturity = calibrated_absolute[-1]
    if calibrated_maturity <= 0.0:
        raise ValueError("growth-stage calibration produced a non-positive maturity GDD requirement")

    calibrated_phases = tuple(
        (
            str(item["stage"]),
            calibrated / calibrated_maturity,
            int(item["bbch"]),
        )
        for item, calibrated in zip(configured, calibrated_absolute)
    )
    diagnostics: list[dict[str, Any]] = []
    previous_configured = 0.0
    previous_calibrated = 0.0
    last_base_requirement = float(last_anchor["base_requirement_gdd"])
    for item, calibrated in zip(configured, calibrated_absolute):
        configured_cumulative = float(item["configured_cumulative_gdd"])
        diagnostics.append(
            {
                **item,
                "calibrated_cumulative_gdd": calibrated,
                "configured_delta_gdd": configured_cumulative - previous_configured,
                "calibrated_delta_gdd": calibrated - previous_calibrated,
                "after_last_observation": configured_cumulative > last_base_requirement + 1e-9,
            }
        )
        previous_configured = configured_cumulative
        previous_calibrated = calibrated
    return calibrated_maturity, calibrated_phases, diagnostics


def _cotton_regional_phenology_observations(
    payload: dict[str, Any],
    planting_date: date,
    season_end: date,
) -> list[dict[str, Any]]:
    region_code = str(payload.get("region_code") or "")
    anchors = COTTON_MANAGEMENT_POLICY["regional_phenology_anchors"].get(region_code) or ()
    observations = []
    for anchor in anchors:
        day = planting_date + timedelta(days=int(anchor["days_after_planting"]))
        if day <= season_end:
            observations.append(
                {
                    "date": day,
                    "target_progress": float(anchor["target_progress"]),
                    "observed_value": f"regional:{anchor['target_progress']}",
                    "record_index": f"regional:{anchor['days_after_planting']}",
                    "source": "regional",
                }
            )
    return observations


def _build_growth_stage_rows(
    *,
    profile: CropProfile,
    daily_weather: dict[date, dict[str, float]],
    hourly_by_day: dict[date, list[dict[str, Any]]],
    season_start: date,
    season_end: date,
    maturity_thermal_time: float,
    observations: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], CropProfile, float, list[dict[str, Any]]]:
    cumulative_gdd = 0.0
    raw_values: list[float] = []
    dates: list[date] = []
    for day in _date_range(season_start, season_end):
        weather = daily_weather.get(day) or _daily_from_hourly(day, hourly_by_day.get(day) or [])
        cumulative_gdd += _daily_thermal_time(profile, weather)
        raw_values.append(cumulative_gdd)
        dates.append(day)

    calibrated_maturity, calibrated_phases, diagnostics = _calibrate_phenology_requirements(
        profile=profile,
        maturity_thermal_time=maturity_thermal_time,
        raw_cumulative_by_date=dict(zip(dates, raw_values)),
        observations=observations or [],
    )
    calibrated_profile = replace(
        profile,
        maturity_thermal_time_c=calibrated_maturity,
        phenology_phases=calibrated_phases,
    )

    rows: list[dict[str, Any]] = []
    for day, raw_value in zip(dates, raw_values):
        phenology = _phenology_for_thermal_time(calibrated_profile, raw_value, calibrated_maturity)
        rows.append(
            {
                "Date": day,
                "Stage": phenology.get("Stage") or "",
                "IowaStage": _maize_iowa_stage_for_progress(
                    calibrated_profile,
                    float(phenology.get("ThermalProgress") or 0.0),
                ),
                "BBCH": int(phenology.get("BBCH") or 0),
                "ThermalProgress": float(phenology.get("ThermalProgress") or 0.0),
                "RawCumulativeThermalTime": raw_value,
                "AdjustedCumulativeThermalTime": raw_value,
            }
        )
    return rows, calibrated_profile, calibrated_maturity, diagnostics


def _simulate_biotic_processes(
    *,
    payload: dict[str, Any],
    profile: CropProfile,
    phenology_rows: list[dict[str, Any]],
    enabled: dict[str, Any],
    season_start: date,
    season_end: date,
) -> dict[str, Any]:
    empty = {
        "by_day": {day: {"disease": _empty_biotic_day(), "insect": _empty_biotic_day()} for day in _date_range(season_start, season_end)},
        "daily_risk": {"disease": [], "insect": []},
        "stress_risk": {"disease": {}, "insect": {}},
        "field_risk": {"disease": [], "insect": []},
    }
    if not enabled["disease"] and not enabled["insect"]:
        return empty

    hourly_rows = payload.get("hourly_weather_data") or []
    if not hourly_rows:
        raise ValueError("weather.hourly is required when disease or insect stresses are requested")
    weather_df = _biotic_hourly_dataframe(hourly_rows)
    phenology_df = pd.DataFrame(phenology_rows)
    stage_source_cols = ("Date", "Stage") if profile.name == "maize" else ("Date", "Stage", "BBCH")
    stage_cols = [col for col in stage_source_cols if col in phenology_df.columns]
    growth_stage_df = phenology_df[stage_cols]
    variety_susceptibility = payload.get("variety_susceptibility") or {}

    if enabled["disease"]:
        disease = _simulate_disease_process(
            profile=profile,
            targets=payload.get("stress_eppo_codes") or [],
            planting_date=_as_date(payload["planting_date"]),
            weather_df=weather_df,
            growth_stage_df=growth_stage_df,
            variety_susceptibility=variety_susceptibility,
            applied_fungicides=payload.get("applied_fungicides") or [],
            season_start=season_start,
            season_end=season_end,
        )
        _merge_biotic_domain(empty, "disease", disease, season_start, season_end)

    if enabled["insect"]:
        normalized_insecticides = _normalize_insecticide_applications(
            payload.get("applied_insecticides") or [],
            profile=profile,
        )
        insect = _simulate_insect_process(
            profile=profile,
            targets=payload.get("insect_eppo_codes") or [],
            planting_date=_as_date(payload["planting_date"]),
            weather_df=weather_df,
            growth_stage_df=growth_stage_df,
            variety_susceptibility=variety_susceptibility,
            applied_insecticides=normalized_insecticides,
            season_start=season_start,
            season_end=season_end,
        )
        _merge_biotic_domain(empty, "insect", insect, season_start, season_end)

    return empty


def _simulate_disease_process(
    *,
    profile: CropProfile,
    targets: list[str],
    planting_date: date,
    weather_df: pd.DataFrame,
    growth_stage_df: pd.DataFrame,
    variety_susceptibility: dict[str, Any],
    applied_fungicides: list[dict[str, Any]],
    season_start: date,
    season_end: date,
) -> dict[str, Any]:
    target_codes = [str(code).upper() for code in targets or []]
    if profile.name == "maize":
        from crops.maize.disease.maize_disease import MaizeDisease

        daily_by_code: dict[str, list[dict[str, Any]]] = {}
        stress_by_code: dict[str, list[dict[str, Any]]] = {}
        target_rows: list[dict[str, Any]] = []

        for code in target_codes:
            model = MaizeDisease(code)
            daily_df = model.simulate_disease_daily_risk(
                planting_date=planting_date,
                weather_hourly=weather_df,
                growth_stage=growth_stage_df,
                variety_susceptibility=variety_susceptibility,
                applied_fungicides=applied_fungicides,
            )
            stress_df = model.estimate_stress_risk(daily_df)
            daily_records = _records_safe(daily_df)
            stress_records = _biotic_stress_records(stress_df, daily_records)
            daily_by_code[code] = daily_records
            stress_by_code[code] = stress_records
            target_rows.extend(stress_records)
    else:
        model_class = _disease_model_class(profile)
        result = model_class(target_codes).run(
            weather_hourly=weather_df,
            growth_stage=growth_stage_df,
            variety_susceptibility=variety_susceptibility,
            applied_fungicides=applied_fungicides,
            decision_date=season_start,
        )
        daily_by_code = {
            str(code).upper(): _records_safe(pd.DataFrame(rows))
            for code, rows in (result.get("daily_disease_risk") or {}).items()
        }
        stress_by_code = {
            str(code).upper(): _biotic_stress_records(pd.DataFrame(rows), daily_by_code.get(str(code).upper(), []))
            for code, rows in (result.get("stress_risk") or {}).items()
        }
        target_rows = [row for rows in stress_by_code.values() for row in rows]

    return _biotic_domain_result(
        target_rows=target_rows,
        daily_by_code=daily_by_code,
        stress_by_code=stress_by_code,
        season_start=season_start,
        season_end=season_end,
    )


def _simulate_insect_process(
    *,
    profile: CropProfile,
    targets: list[str],
    planting_date: date,
    weather_df: pd.DataFrame,
    growth_stage_df: pd.DataFrame,
    variety_susceptibility: dict[str, Any],
    applied_insecticides: list[dict[str, Any]],
    season_start: date,
    season_end: date,
) -> dict[str, Any]:
    target_codes = [str(code).upper() for code in targets or []]
    if profile.name == "maize":
        from crops.maize.insect.maize_insect import MaizeInsect

        daily_by_code: dict[str, list[dict[str, Any]]] = {}
        stress_by_code: dict[str, list[dict[str, Any]]] = {}
        target_rows: list[dict[str, Any]] = []

        for code in target_codes:
            model = MaizeInsect(code)
            daily_df = model.simulate_insect_daily_risk(
                planting_date=planting_date,
                weather_hourly=weather_df,
                growth_stage=growth_stage_df,
                variety_susceptibility=variety_susceptibility.get(code) if isinstance(variety_susceptibility, dict) else None,
                applied_insecticides=applied_insecticides,
            )
            stress_df = model.estimate_stress_risk(daily_df)
            daily_records = _records_safe(daily_df)
            stress_records = _biotic_stress_records(stress_df, daily_records)
            daily_by_code[code] = daily_records
            stress_by_code[code] = stress_records
            target_rows.extend(stress_records)
    else:
        model_class = _insect_model_class(profile)
        result = model_class(target_codes).run(
            weather_hourly=weather_df,
            growth_stage=growth_stage_df,
            variety_susceptibility=variety_susceptibility,
            applied_insecticides=pd.DataFrame(applied_insecticides or []),
            decision_date=season_start,
        )
        daily_by_code = {
            str(code).upper(): _records_safe(pd.DataFrame(rows))
            for code, rows in (result.get("daily_insect_risk") or {}).items()
        }
        stress_by_code = {
            str(code).upper(): _biotic_stress_records(pd.DataFrame(rows), daily_by_code.get(str(code).upper(), []))
            for code, rows in (result.get("stress_risk") or {}).items()
        }
        target_rows = [row for rows in stress_by_code.values() for row in rows]

    return _biotic_domain_result(
        target_rows=target_rows,
        daily_by_code=daily_by_code,
        stress_by_code=stress_by_code,
        season_start=season_start,
        season_end=season_end,
    )


def _merge_biotic_domain(target: dict[str, Any], domain: str, result: dict[str, Any], season_start: date, season_end: date) -> None:
    target["daily_risk"][domain] = result["daily_risk"]
    target["stress_risk"][domain] = result["stress_risk"]
    target["field_risk"][domain] = result["field_risk"]
    by_day = result["by_day"]
    for day in _date_range(season_start, season_end):
        target["by_day"].setdefault(day, {})[domain] = by_day.get(day, _empty_biotic_day())


def _biotic_domain_result(
    *,
    target_rows: list[dict[str, Any]],
    daily_by_code: dict[str, list[dict[str, Any]]],
    stress_by_code: dict[str, list[dict[str, Any]]],
    season_start: date,
    season_end: date,
) -> dict[str, Any]:
    stress_by_code = {code: _carry_forward_stress_rows(rows) for code, rows in stress_by_code.items()}
    target_rows = [row for rows in stress_by_code.values() for row in rows]
    rows_by_day: dict[date, list[dict[str, Any]]] = {}
    for row in target_rows:
        day = _date_key(row.get("Date"))
        if day is not None:
            rows_by_day.setdefault(day, []).append(row)

    by_day: dict[date, dict[str, Any]] = {}
    field_rows: list[dict[str, Any]] = []
    for day in _date_range(season_start, season_end):
        rows = rows_by_day.get(day) or []
        selected = _dominant_biotic_row(rows)
        state = _empty_biotic_day() if selected is None else {
            "stress_risk": selected["stress_risk"],
            "field_risk": _field_status_from_rows(rows),
            "target_code": selected["target_code"],
            "stress_index": selected["stress_index"],
            "favorability": selected["favorability"],
        }
        by_day[day] = state
        field_rows.append({"Date": day.isoformat(), "field_risk": state["field_risk"]})

    daily_risk = []
    for code, rows in daily_by_code.items():
        stress_lookup = {_date_key(row.get("Date")): row for row in stress_by_code.get(code, [])}
        for row in rows:
            day = _date_key(row.get("Date"))
            stress = stress_lookup.get(day, {})
            raw_status = stress.get("raw_stress_risk") or row.get("categorized_shortterm_aggregate_favorability")
            daily_risk.append(
                {
                    "Date": day.isoformat() if day else str(row.get("Date")),
                    "target_code": code,
                    "stress_risk": _normalize_biotic_status(raw_status),
                    "raw_stress_risk": raw_status,
                    "favorability": round(float(row.get("favorability", 0.0) or 0.0), 4),
                    "shortterm_aggregate_favorability": round(float(row.get("shortterm_aggregate_favorability", 0.0) or 0.0), 4),
                    "weather_favorability": round(float(row.get("weather_favorability", 0.0) or 0.0), 4),
                    "growth_stage_favorability": round(float(row.get("growth_stage_favorability", 0.0) or 0.0), 4),
                }
            )

    return {
        "by_day": by_day,
        "daily_risk": sorted(daily_risk, key=lambda item: (item["Date"], item["target_code"])),
        "stress_risk": stress_by_code,
        "field_risk": field_rows,
    }


def _biotic_stress_records(stress_df: pd.DataFrame, daily_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    daily_lookup = {_date_key(row.get("Date")): row for row in daily_records}
    out = []
    for row in _records_safe(stress_df):
        day = _date_key(row.get("Date"))
        daily = daily_lookup.get(day, {})
        raw_status = row.get("stress_risk")
        stress_status = _normalize_biotic_status(raw_status)
        if stress_status != "PROTECTED" and float(daily.get("growth_stage_favorability", 0.0) or 0.0) <= 0.0:
            stress_status = "OUT_OF_SEASON"
        out.append(
            {
                "Date": day.isoformat() if day else str(row.get("Date")),
                "target_code": row.get("target_code") or row.get("eppo_code"),
                "eppo_code": row.get("eppo_code") or row.get("target_code"),
                "stress_risk": stress_status,
                "raw_stress_risk": raw_status,
                "stress_index": _biotic_index(raw_status, daily),
                "favorability": float(daily.get("shortterm_aggregate_favorability", daily.get("favorability", 0.0)) or 0.0),
            }
        )
    return out


def _dominant_biotic_row(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    return max(rows, key=lambda row: (RISK_ORDER.get(str(row.get("stress_risk") or "LOW").upper(), 0), float(row.get("stress_index", 0.0) or 0.0)))


def _field_status_from_rows(rows: list[dict[str, Any]]) -> str:
    return _worst_status([row.get("stress_risk") for row in rows])


def _empty_biotic_day() -> dict[str, Any]:
    return {"stress_risk": "LOW", "field_risk": "LOW", "target_code": None, "stress_index": 0.0, "favorability": 0.0}


def _biotic_index(raw_status: Any, daily: dict[str, Any]) -> float:
    if str(raw_status or "").upper() == "PROTECTED":
        return 0.0
    value = daily.get("shortterm_aggregate_favorability", daily.get("favorability", 0.0))
    try:
        return max(0.0, min(1.0, float(value or 0.0)))
    except (TypeError, ValueError):
        return {"OPTIMAL": 0.85, "HIGH": 0.85, "FAVORABLE": 0.45, "MEDIUM": 0.45}.get(str(raw_status or "LOW").upper(), 0.0)


def _normalize_biotic_status(status: Any) -> str:
    value = str(status or "LOW").upper()
    return {
        "UNFAVORABLE": "OUT_OF_SEASON",
        "NOT_SEASONAL": "OUT_OF_SEASON",
        "FAVORABLE": "MEDIUM",
        "WATCH": "MEDIUM",
        "OPTIMAL": "HIGH",
        "NECESSARY": "HIGH",
        "PROTECTED": "PROTECTED",
    }.get(value, value if value in {"OUT_OF_SEASON", "LOW", "MEDIUM", "HIGH", "PROTECTED"} else "LOW")


def _biotic_hourly_dataframe(rows: list[Any]) -> pd.DataFrame:
    normalized = []
    for raw in rows:
        item = _dump(raw)
        if not item.get("DateTime"):
            raise ValueError("weather.hourly rows must include DateTime")
        row = dict(item)
        if "relative_humidity_2m" not in row and "relativehumidity_2m" in row:
            row["relative_humidity_2m"] = row["relativehumidity_2m"]
        if "relativehumidity_2m" not in row and "relative_humidity_2m" in row:
            row["relativehumidity_2m"] = row["relative_humidity_2m"]
        if "wind_speed_10m" not in row and "windspeed_10m" in row:
            row["wind_speed_10m"] = row["windspeed_10m"]
        if "windspeed_10m" not in row and "wind_speed_10m" in row:
            row["windspeed_10m"] = row["wind_speed_10m"]
        normalized.append(row)
    df = pd.DataFrame(normalized)
    df["DateTime"] = pd.to_datetime(df["DateTime"])
    required = ["temperature_2m", "relative_humidity_2m", "wind_speed_10m", "precipitation", "shortwave_radiation"]
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(f"weather.hourly missing required variable(s): {', '.join(missing)}")
    for column in required:
        df[column] = pd.to_numeric(df[column], errors="coerce")
        if df[column].isna().any():
            raise ValueError(f"weather.hourly.{column} has missing or non-numeric values")
    return df.sort_values("DateTime").reset_index(drop=True)


def _records_safe(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    safe = df.replace({float("inf"): None, float("-inf"): None}).where(pd.notna(df), None)
    records = safe.to_dict(orient="records")
    for row in records:
        for key, value in list(row.items()):
            if isinstance(value, pd.Timestamp):
                row[key] = value.date().isoformat()
            elif isinstance(value, datetime):
                row[key] = value.date().isoformat()
            elif isinstance(value, date):
                row[key] = value.isoformat()
    return records


def _normalize_insecticide_applications(events: list[Any], *, profile: CropProfile) -> list[dict[str, Any]]:
    if not events:
        return []

    normalized = []
    for index, raw in enumerate(events):
        event = _dump(raw)
        day = _date_key(event.get("date") or event.get("Date") or event.get("applied_date"))
        if day is None:
            raise ValueError(f"management.applied.insecticides[{index}] missing date")
        product_key = event.get("product_key")
        if not product_key:
            raise ValueError(f"management.applied.insecticides[{index}] missing product_key")
        pesticide = deepcopy(event.get("pesticide") or _insecticide_params(profile).get(str(product_key)))
        if not pesticide:
            raise ValueError(f"management.applied.insecticides[{index}] missing pesticide master data for {product_key}")
        if event.get("dose_value") is None or event.get("dose_unit") is None:
            raise ValueError(f"management.applied.insecticides[{index}] must include dose_value and dose_unit")
        normalized_event = dict(event)
        normalized_event["date"] = day
        normalized_event["Date"] = day
        normalized_event["product_key"] = product_key
        normalized_event["pesticide"] = pesticide
        normalized.append(normalized_event)
    return normalized


def _insecticide_params(profile: CropProfile) -> dict[str, Any]:
    if profile.name == "maize":
        from crops.maize.insect.config import pesticide_params

        return pesticide_params
    if profile.name == "wheat":
        from crops.wheat.config import INSECTICIDE_PARAMS

        return INSECTICIDE_PARAMS
    if profile.name == "cotton":
        from crops.cotton.config import COTTON_INSECTICIDE_PARAMS

        return COTTON_INSECTICIDE_PARAMS
    return {}


def _season_thermal_time(profile: CropProfile, daily_weather: dict[date, dict[str, float]], hourly_by_day: dict[date, list[dict[str, Any]]], season_start: date, season_end: date) -> float:
    total = 0.0
    for day in _date_range(season_start, season_end):
        weather = daily_weather.get(day) or _daily_from_hourly(day, hourly_by_day.get(day) or [])
        total += _daily_thermal_time(profile, weather)
    return max(total, 1.0)


def _daily_thermal_time(profile: CropProfile, weather: dict[str, float]) -> float:
    mean_temperature = float(weather["temperature_2m_mean"])
    if profile.name != "maize":
        return max(0.0, mean_temperature - profile.base_temp_c)

    cardinal = MAIZE_PHENOLOGY_CONFIG["MAIZE_CARDINAL_TEMPERATURES"]
    base = float(cardinal["base_temperature"])
    optimum = float(cardinal["optimal_temperature"])
    maximum = float(cardinal["maximum_temperature"])
    if mean_temperature < base or mean_temperature > maximum:
        return 0.0
    if mean_temperature <= optimum:
        return mean_temperature - base
    return (maximum - mean_temperature) * (optimum - base) / max(maximum - optimum, 1e-9)


def _phenology_for_thermal_time(profile: CropProfile, cumulative_tt: float, maturity_tt: float) -> dict[str, Any]:
    progress = max(0.0, min(1.0, cumulative_tt / max(maturity_tt, 1.0)))
    previous_stage = ("Sowing", 0.0, 0)
    stage_index = 1
    phase_count = len(profile.phenology_phases)
    stage_index_offset = 1 if profile.phenology_phases and float(profile.phenology_phases[0][1]) <= 0.0 else 0
    for index, phase in enumerate(profile.phenology_phases, start=1):
        stage_name, threshold, bbch = phase
        if progress < threshold or index == phase_count:
            start_name, start_threshold, start_bbch = previous_stage
            span = max(threshold - start_threshold, 1e-9)
            fraction = max(0.0, min(1.0, (progress - start_threshold) / span))
            bbch_continuous = start_bbch + fraction * (bbch - start_bbch)
            display_stage = stage_name if index == 1 or fraction >= 1.0 else start_name
            return {
                "Date": None,
                "Stage": display_stage,
                "StageName": display_stage,
                "BBCH": int(round(bbch_continuous)),
                "BBCHContinuous": bbch_continuous,
                "ThermalProgress": progress,
                "PhaseFraction": fraction,
                "PhenologyStageIndex": index - 1 - stage_index_offset + fraction,
            }
        previous_stage = phase
        stage_index = index
    final_name, _, final_bbch = profile.phenology_phases[-1]
    return {
        "Date": None,
        "Stage": final_name,
        "StageName": final_name,
        "BBCH": final_bbch,
        "BBCHContinuous": float(final_bbch),
        "ThermalProgress": progress,
        "PhaseFraction": 1.0,
        "PhenologyStageIndex": float(stage_index),
    }


def _root_depth_for_progress(profile: CropProfile, thermal_progress: float) -> float:
    root_progress = max(0.0, min(1.0, thermal_progress / max(profile.root_growth_end_fraction, 1e-6)))
    return min(
        profile.root_max_mm,
        max(profile.root_initial_mm, profile.root_initial_mm + root_progress * (profile.root_max_mm - profile.root_initial_mm)),
    )


def _season_progress(day: date, planting: date, end: date) -> float:
    return max(0.0, min(1.0, (day - planting).days / max((end - planting).days, 1)))


def _init_soil_layers(rows: list[Any]) -> list[dict[str, float]]:
    layers = []
    cumulative_depth_mm = 0.0
    for index, raw in enumerate(rows):
        item = _dump(raw)
        if item.get("thickness_mm") is not None:
            depth_mm = float(item["thickness_mm"])
            top_cm = cumulative_depth_mm / 10.0
            bottom_cm = (cumulative_depth_mm + depth_mm) / 10.0
            wilting_mm = float(item["ll15_mm"])
            fc_mm = float(item["dul_mm"])
            sat_mm = float(item["sat_mm"])
            water = float(item["initial_water_mm"])
            taw = max(1.0, fc_mm - wilting_mm)
            kl = float(item.get("kl", 0.06 if index == 0 else 0.04) or (0.06 if index == 0 else 0.04))
            xf = float(item.get("xf", 1.0) or 1.0)
            ksat = float(item.get("ks_mm_day", item.get("ksat_mm_day", 20.0)) or 20.0)
            cumulative_depth_mm += depth_mm
        else:
            top_cm = float(item.get("depth_top_cm", 0.0) or 0.0)
            bottom_cm = float(item.get("depth_bottom_cm", top_cm) or top_cm)
            depth_mm = max(0.0, (bottom_cm - top_cm) * 10.0)
            gravel = float(item.get("gravel_fraction", 0.0) or 0.0)
            fc = float(item.get("field_capacity", 0.28) or 0.28)
            wp = float(item.get("wilting_point", 0.12) or 0.12)
            sat = float(item.get("saturation", max(fc, 0.42)) or max(fc, 0.42))
            taw = max(1.0, (fc - wp) * depth_mm * (1.0 - gravel))
            wilting_mm = max(0.0, wp * depth_mm * (1.0 - gravel))
            fc_mm = wilting_mm + taw
            sat_mm = max(fc_mm, sat * depth_mm * (1.0 - gravel))
            if item.get("initial_soil_water_mm") is not None:
                water = float(item["initial_soil_water_mm"])
            else:
                rel = float(item.get("initial_relative_water", 0.65) or 0.65)
                water = wilting_mm + rel * taw
            kl = float(item.get("kl", 0.06 if index == 0 else 0.04) or (0.06 if index == 0 else 0.04))
            xf = float(item.get("xf", 1.0) or 1.0)
            ksat = float(item.get("ksat_mm_day", 20.0) or 20.0)
        layers.append(
            {
                "index": float(index),
                "depth_top_cm": top_cm,
                "depth_bottom_cm": bottom_cm,
                "depth_mm": depth_mm,
                "water_mm": max(wilting_mm, min(sat_mm, water)),
                "wilting_mm": wilting_mm,
                "field_capacity_mm": fc_mm,
                "saturation_mm": sat_mm,
                "taw_mm": taw,
                "kl": kl * max(0.0, xf),
                "ksat_mm_day": ksat,
            }
        )
    return layers


def _events_by_day(events: list[Any], date_keys: tuple[str, ...]) -> dict[date, list[dict[str, Any]]]:
    out: dict[date, list[dict[str, Any]]] = {}
    for raw in events:
        event = _dump(raw)
        day = next((_date_key(event.get(key)) for key in date_keys if event.get(key) is not None), None)
        if day is not None:
            out.setdefault(day, []).append(event)
    return out


def _merge_events_by_day(*groups: dict[date, list[dict[str, Any]]]) -> dict[date, list[dict[str, Any]]]:
    merged: dict[date, list[dict[str, Any]]] = {}
    for group in groups:
        for day, events in group.items():
            merged.setdefault(day, []).extend(events)
    return merged


def _history_before(events: list[Any], cutoff: date, date_keys: tuple[str, ...]) -> list[dict[str, Any]]:
    rows = []
    for raw in events:
        event = _dump(raw)
        day = next((_date_key(event.get(key)) for key in date_keys if event.get(key) is not None), None)
        if day is not None and day <= cutoff:
            rows.append(event)
    return rows


def _event_amount(event: dict[str, Any], keys: tuple[str, ...]) -> float:
    for key in keys:
        if event.get(key) is not None:
            return max(0.0, float(event[key] or 0.0))
    return 0.0


def _initial_nutrient_supply(soil_test: dict[str, Any], profile: CropProfile, target_yield: float) -> dict[str, float]:
    mineral_n = soil_test.get("mineral_n_kg_ha")
    if mineral_n is None:
        mineral_n = float(soil_test.get("alkali_hydrolyzable_n_mg_kg", 80.0) or 80.0) * 0.9
    p = float(soil_test.get("olsen_p_mg_kg", 15.0) or 15.0) * 4.0
    k = float(soil_test.get("available_k_mg_kg", soil_test.get("exchangeable_k_mg_kg", 140.0)) or 140.0) * 1.1
    om_bonus = float(soil_test.get("organic_matter_g_kg", 15.0) or 15.0) * 1.2
    return {
        "N": max(0.0, float(mineral_n) + om_bonus),
        "P2O5": max(0.0, p),
        "K2O": max(0.0, k),
    }


def _fertilizer_release(events_by_day: dict[date, list[dict[str, Any]]], day: date) -> dict[str, float]:
    release = {"N": 0.0, "P2O5": 0.0, "K2O": 0.0}
    for event_day, events in events_by_day.items():
        if event_day > day:
            continue
        days_since = (day - event_day).days
        for event in events:
            if _normalize_fertilizer_method(event.get("method") or event.get("application_method")) == "foliar":
                continue
            release_days = int(event.get("release_days") or (21 if str(event.get("release_type") or "").lower().startswith("coated") else 3))
            if days_since >= max(1, release_days):
                continue
            nutrients = _event_nutrients(event)
            daily_fraction = 1.0 / max(1, release_days)
            for nutrient in release:
                release[nutrient] += nutrients.get(nutrient, 0.0) * daily_fraction
    return release


def _foliar_nutrient_absorption(
    events_by_day: dict[date, list[dict[str, Any]]],
    day: date,
    weather: dict[str, Any],
) -> dict[str, Any]:
    absorbed = {"N": 0.0, "P2O5": 0.0, "K2O": 0.0}
    max_washoff = 0.0
    rain_mm = max(0.0, float(weather.get("precipitation_sum") or 0.0))
    same_day_washoff = min(0.85, rain_mm / 12.0) if rain_mm > 0.0 else 0.0
    for event_day, events in events_by_day.items():
        if event_day > day:
            continue
        days_since = (day - event_day).days
        for event in events:
            if _normalize_fertilizer_method(event.get("method") or event.get("application_method")) != "foliar":
                continue
            release_days = max(1, int(event.get("release_days") or 1))
            if days_since >= release_days:
                continue
            washoff = same_day_washoff if days_since == 0 else 0.0
            max_washoff = max(max_washoff, washoff)
            efficiency = _foliar_absorption_efficiency(event)
            nutrients = _event_nutrients(event)
            for nutrient in absorbed:
                absorbed[nutrient] += nutrients.get(nutrient, 0.0) / release_days * efficiency * (1.0 - washoff)
    return {"absorbed": absorbed, "washoff_fraction": max_washoff}


def _foliar_absorption_efficiency(event: dict[str, Any]) -> float:
    if event.get("foliar_absorption_efficiency") is not None:
        return max(0.0, min(0.95, float(event.get("foliar_absorption_efficiency") or 0.0)))
    if event.get("absorption_efficiency") is not None:
        return max(0.0, min(0.95, float(event.get("absorption_efficiency") or 0.0)))
    return 0.55


def _normalize_fertilizer_method(value: Any) -> str:
    method = str(value or "soil").strip().lower()
    if method in {"foliar_spray", "leaf_spray", "leaf", "foliar"}:
        return "foliar"
    return method


def _event_nutrients(event: dict[str, Any]) -> dict[str, float]:
    nutrients = event.get("nutrients_kg_ha") or {}
    if nutrients:
        return _canonical_nutrients(nutrients)
    amount = float(event.get("amount_kg_ha", 0.0) or 0.0)
    return {
        "N": amount * float(event.get("n_pct", 0.0) or 0.0) / 100.0,
        "P2O5": amount * float(event.get("p2o5_pct", 0.0) or 0.0) / 100.0,
        "K2O": amount * float(event.get("k2o_pct", 0.0) or 0.0) / 100.0,
    }


def _event_target_nutrients(event: dict[str, Any]) -> list[str]:
    targets = [str(value) for value in event.get("target_nutrients") or [] if value]
    if targets:
        return targets
    nutrients = _event_nutrients(event)
    targets.extend(key for key, value in nutrients.items() if float(value or 0.0) > 0.0)
    micros = event.get("micros_g_ha") or {}
    if isinstance(micros, dict):
        targets.extend(str(key) for key, value in micros.items() if float(value or 0.0) > 0.0)
    return sorted(set(targets))


def _reference_et(weather: dict[str, float]) -> float:
    tmean = float(weather["temperature_2m_mean"])
    tmin = float(weather["temperature_2m_min"])
    tmax = float(weather["temperature_2m_max"])
    radiation = float(weather["shortwave_radiation_sum"])
    wind = float(weather.get("windspeed_10m_mean", 2.0))
    rh = float(weather.get("relative_humidity_2m_mean", 65.0))
    temp_range = max(0.1, tmax - tmin)
    return max(0.2, 0.13 * radiation + 0.07 * sqrt(temp_range) + 0.08 * wind + 0.015 * max(0.0, 75.0 - rh))


def _kc_from_progress(progress: float, profile: CropProfile) -> float:
    if progress < 0.15:
        return 0.35
    if progress < 0.45:
        return 0.35 + (progress - 0.15) / 0.30 * 0.75
    if progress < 0.75:
        return 1.10
    return max(0.55, 1.10 - (progress - 0.75) / 0.25 * 0.55)


def _continuous_bbch(progress: float) -> float:
    if progress < 0.08:
        return 5.0 + progress / 0.08 * 5.0
    if progress < 0.35:
        return 10.0 + (progress - 0.08) / 0.27 * 25.0
    if progress < 0.58:
        return 35.0 + (progress - 0.35) / 0.23 * 30.0
    if progress < 0.82:
        return 65.0 + (progress - 0.58) / 0.24 * 15.0
    return 80.0 + (progress - 0.82) / 0.18 * 15.0


def _maize_stage_label(value: Any) -> str:
    label = str(value or "").strip().upper().replace(" ", "")
    if label in MAIZE_IOWA_STAGE_RANK:
        return label
    return ""


def _row_stage_label(row: dict[str, Any]) -> str:
    return _maize_stage_label(row.get("Stage") or row.get("StageName"))


def _maize_row_stage_between(row: dict[str, Any], start: str, end: str) -> bool:
    return _maize_stage_between(_row_stage_label(row), start, end)


def _maize_stage_rank(value: Any) -> int | None:
    label = _maize_stage_label(value)
    return MAIZE_IOWA_STAGE_RANK.get(label) if label else None


def _maize_stage_between(value: Any, start: str, end: str) -> bool:
    rank = _maize_stage_rank(value)
    start_rank = MAIZE_IOWA_STAGE_RANK[start]
    end_rank = MAIZE_IOWA_STAGE_RANK[end]
    return rank is not None and start_rank <= rank <= end_rank


def _maize_stage_at_or_after(value: Any, start: str) -> bool:
    rank = _maize_stage_rank(value)
    return rank is not None and rank >= MAIZE_IOWA_STAGE_RANK[start]


def _maize_stage_before(value: Any, stage: str) -> bool:
    rank = _maize_stage_rank(value)
    return rank is not None and rank < MAIZE_IOWA_STAGE_RANK[stage]


def _stage_progress_threshold(profile: CropProfile, stage_name: str) -> float:
    if profile.name == "maize":
        rank = _maize_stage_rank(stage_name)
        if rank is not None:
            return _maize_iowa_stage_threshold(profile, rank)
    wanted = _stage_label_key(stage_name)
    for stage, threshold, _ in profile.phenology_phases:
        if _stage_label_key(stage) == wanted:
            return float(threshold)
    return 1.0


def _maize_progress_between(profile: CropProfile, progress: float, start_stage: str, end_stage: str) -> bool:
    start = _stage_progress_threshold(profile, start_stage)
    end = _stage_progress_threshold(profile, end_stage)
    return start <= float(progress or 0.0) <= end


def _maize_progress_at_or_after(profile: CropProfile, progress: float, stage_name: str) -> bool:
    return float(progress or 0.0) >= _stage_progress_threshold(profile, stage_name)


def _green_cover(lai: float, profile: CropProfile) -> float:
    return max(0.0, min(0.999999, 1.0 - exp(-profile.extinction_coefficient * max(0.0, lai))))


def _specific_leaf_area_for_profile(profile: CropProfile, thermal_progress: float, bbch: float) -> float:
    if profile.name != "maize":
        return _specific_leaf_area(profile, bbch)
    leaf_tips = _maize_leaf_tips_appeared(profile, thermal_progress)
    return _linear_interp(leaf_tips, [1.0, 9.0], [profile.sla_max_m2_g, profile.sla_min_m2_g])


def _maize_leaf_tips_appeared(profile: CropProfile, thermal_progress: float) -> float:
    progress = max(0.0, min(1.0, float(thermal_progress or 0.0)))
    emergence = _stage_progress_threshold(profile, "VE")
    tasseling = _stage_progress_threshold(profile, "VT")
    if progress < emergence:
        return 0.5
    if progress < tasseling:
        fraction = (progress - emergence) / max(tasseling - emergence, 1e-9)
        return 1.0 + fraction * max(1.0, profile.final_leaf_number - 1.0)
    return profile.final_leaf_number


def _specific_leaf_area(profile: CropProfile, bbch: float) -> float:
    leaf_tips = _leaf_tips_appeared(profile, bbch)
    return _linear_interp(leaf_tips, [1.0, 9.0], [profile.sla_max_m2_g, profile.sla_min_m2_g])


def _leaf_tips_appeared(profile: CropProfile, bbch: float) -> float:
    if bbch < 10.0:
        return 0.5
    if bbch < 65.0:
        return 1.0 + (bbch - 10.0) / 55.0 * max(1.0, profile.final_leaf_number - 1.0)
    return profile.final_leaf_number


def _leaf_partition_fraction(profile: CropProfile, bbch: float) -> float:
    if profile.name == "wheat":
        return _linear_interp(bbch, [5.0, 21.0, 37.0, 60.0, 75.0], [0.24, 0.38, 0.30, 0.04, 0.0])
    return _linear_interp(bbch, [5.0, 20.0, 45.0, 70.0, 85.0], [0.22, 0.36, 0.28, 0.08, 0.01])


def _leaf_partition_fraction_for_profile(profile: CropProfile, thermal_progress: float, bbch: float) -> float:
    if profile.name != "maize":
        return _leaf_partition_fraction(profile, bbch)
    progress = float(thermal_progress or 0.0)
    ve = _stage_progress_threshold(profile, "VE")
    v6 = _stage_progress_threshold(profile, "V6")
    v9 = _stage_progress_threshold(profile, "V9")
    vt = _stage_progress_threshold(profile, "VT")
    r1 = _stage_progress_threshold(profile, "R1")
    r2 = _stage_progress_threshold(profile, "R2")
    return _linear_interp(progress, [0.0, ve, v6, v9, vt, r1, r2], [0.20, 0.42, 0.36, 0.18, 0.04, 0.02, 0.0])


def _leaf_expansion_stress(water_factor: float, nutrition_factor: float, weather: dict[str, float]) -> float:
    temperature_factor = _linear_interp(float(weather["temperature_2m_mean"]), [0.0, 12.0, 14.0], [0.0, 1.0, 1.0])
    nitrogen_factor = _linear_interp(nutrition_factor, [0.0, 0.5, 1.0], [0.1, 0.1, 1.0])
    water_expansion_factor = _linear_interp(water_factor, [0.0, 0.7, 1.0], [0.0, 1.0, 1.0])
    return max(0.0, min(1.0, water_expansion_factor, nitrogen_factor, temperature_factor))


def _leaf_senescence_fraction(bbch: float, combined_growth_factor: float) -> float:
    age = _linear_interp(bbch, [60.0, 75.0, 85.0, 95.0], [0.0, 0.004, 0.018, 0.065])
    stress = max(0.0, 1.0 - combined_growth_factor) * 0.025
    return max(0.0, min(0.12, age + stress))


def _leaf_senescence_fraction_for_profile(profile: CropProfile, thermal_progress: float, bbch: float, combined_growth_factor: float) -> float:
    if profile.name != "maize":
        return _leaf_senescence_fraction(bbch, combined_growth_factor)
    progress = float(thermal_progress or 0.0)
    r1 = _stage_progress_threshold(profile, "R1")
    r3 = _stage_progress_threshold(profile, "R3")
    r4 = _stage_progress_threshold(profile, "R4")
    r6 = _stage_progress_threshold(profile, "R6")
    age = _linear_interp(progress, [r1, r3, r4, r6], [0.0, 0.004, 0.018, 0.065])
    stress = max(0.0, 1.0 - combined_growth_factor) * 0.025
    return max(0.0, min(0.12, age + stress))


def _biotic_growth_factor(
    disease_stress_index: float,
    insect_stress_index: float,
    *,
    profile: CropProfile,
    stage_name: str,
    thermal_progress: float,
    bbch: int,
) -> float:
    if profile.name == "maize":
        if not _maize_stage_at_or_after(stage_name, "VE") or _maize_progress_at_or_after(profile, thermal_progress, "R6"):
            return 1.0
    else:
        if bbch < 18 or bbch >= 92:
            return 1.0
    disease_loss = max(0.0, min(1.0, float(disease_stress_index or 0.0))) * 0.18
    insect_loss = max(0.0, min(1.0, float(insect_stress_index or 0.0))) * 0.16
    return max(0.55, min(1.0, 1.0 - disease_loss - insect_loss))


def _biotic_growth_factor_by_bbch(disease_stress_index: float, insect_stress_index: float, bbch: int) -> float:
    if bbch < 18 or bbch >= 92:
        return 1.0
    disease_loss = max(0.0, min(1.0, float(disease_stress_index or 0.0))) * 0.18
    insect_loss = max(0.0, min(1.0, float(insect_stress_index or 0.0))) * 0.16
    return max(0.55, min(1.0, 1.0 - disease_loss - insect_loss))


def _biotic_leaf_damage_fraction(
    disease_stress_index: float,
    insect_stress_index: float,
    *,
    profile: CropProfile,
    stage_name: str,
    thermal_progress: float,
    bbch: int,
) -> float:
    if profile.name == "maize":
        if not _maize_stage_at_or_after(stage_name, "VE") or _maize_progress_at_or_after(profile, thermal_progress, "R6"):
            return 0.0
    else:
        if bbch < 18 or bbch >= 92:
            return 0.0
    disease = max(0.0, min(1.0, float(disease_stress_index or 0.0))) * 0.008
    insect = max(0.0, min(1.0, float(insect_stress_index or 0.0))) * 0.006
    return min(0.02, disease + insect)


def _biotic_leaf_damage_fraction_by_bbch(disease_stress_index: float, insect_stress_index: float, bbch: int) -> float:
    if bbch < 18 or bbch >= 92:
        return 0.0
    disease = max(0.0, min(1.0, float(disease_stress_index or 0.0))) * 0.008
    insect = max(0.0, min(1.0, float(insect_stress_index or 0.0))) * 0.006
    return min(0.02, disease + insect)


def _vegetative_senescence_fraction(bbch: int) -> float:
    if bbch < 80:
        return 0.0
    return min(0.025, (bbch - 79) * 0.0018)


def _vegetative_senescence_fraction_for_profile(profile: CropProfile, thermal_progress: float, bbch: int) -> float:
    if profile.name != "maize":
        return _vegetative_senescence_fraction(bbch)
    progress = float(thermal_progress or 0.0)
    r3 = _stage_progress_threshold(profile, "R3")
    r6 = _stage_progress_threshold(profile, "R6")
    if progress < r3:
        return 0.0
    return min(0.025, (progress - r3) / max(r6 - r3, 1e-9) * 0.025)


def _temperature_growth_factor(weather: dict[str, float], profile: CropProfile) -> float:
    tmean = float(weather["temperature_2m_mean"])
    if profile.name == "wheat":
        return _linear_interp(tmean, [-5.0, 0.0, 15.0, 30.0, 42.0], [0.0, 0.2, 1.0, 1.0, 0.0])
    if profile.name == "cotton":
        return _linear_interp(tmean, [0.0, 12.0, 24.0, 36.0, 50.0], [0.0, 0.0, 1.0, 1.0, 0.0])
    return _linear_interp(tmean, [0.0, 8.0, 15.0, 35.0, 50.0], [0.0, 0.0, 1.0, 1.0, 0.0])


def _linear_interp(x: float, xs: list[float], ys: list[float]) -> float:
    if x <= xs[0]:
        return ys[0]
    for index in range(1, len(xs)):
        if x <= xs[index]:
            span = max(xs[index] - xs[index - 1], 1e-9)
            frac = (x - xs[index - 1]) / span
            return ys[index - 1] + frac * (ys[index] - ys[index - 1])
    return ys[-1]


def _root_zone_relative_water(layers: list[dict[str, float]], root_depth_mm: float) -> float:
    return _root_zone_water_state(layers, root_depth_mm)["relative_available_water"]


def _top_layer_relative_water(layers: list[dict[str, float]]) -> float | None:
    if not layers:
        return None
    layer = layers[0]
    taw = max(float(layer.get("taw_mm", 0.0) or 0.0), 1e-6)
    available = max(0.0, float(layer.get("water_mm", 0.0) or 0.0) - float(layer.get("wilting_mm", 0.0) or 0.0))
    return max(0.0, min(1.2, available / taw))


def _forecast_precipitation_mm(daily_weather: dict[date, dict[str, float]], day: date, days: int) -> float:
    total = 0.0
    for offset in range(1, max(0, int(days)) + 1):
        row = daily_weather.get(day + timedelta(days=offset)) or {}
        total += float(row.get("precipitation_sum", 0.0) or 0.0)
    return max(0.0, total)


def _root_zone_water_state(layers: list[dict[str, float]], root_depth_mm: float) -> dict[str, float]:
    taw = 0.0
    available = 0.0
    for layer in layers:
        overlap = _root_overlap(layer, root_depth_mm)
        if overlap <= 0.0:
            continue
        frac = overlap / max(layer["depth_mm"], 1.0)
        taw += layer["taw_mm"] * frac
        available += max(0.0, layer["water_mm"] - layer["wilting_mm"]) * frac
    return {
        "available_water_mm": max(0.0, available),
        "capacity_mm": max(taw, 1.0),
        "relative_available_water": max(0.0, min(1.2, available / max(taw, 1.0))),
    }


def _root_overlap(layer: dict[str, float], root_depth_mm: float) -> float:
    top = layer["depth_top_cm"] * 10.0
    bottom = layer["depth_bottom_cm"] * 10.0
    return max(0.0, min(bottom, root_depth_mm) - top)


def _root_zone_water_supply(layers: list[dict[str, float]], root_depth_mm: float) -> float:
    supply = 0.0
    for layer in layers:
        overlap = _root_overlap(layer, root_depth_mm)
        if overlap <= 0.0:
            continue
        frac = overlap / max(layer["depth_mm"], 1.0)
        extractable = max(0.0, layer["water_mm"] - layer["wilting_mm"]) * frac
        supply += extractable * max(0.0, layer.get("kl", 0.04))
    return max(0.0, supply)


def _add_infiltration(layers: list[dict[str, float]], *, precipitation: float, irrigation: float) -> None:
    infiltration = max(0.0, precipitation + irrigation)
    if layers:
        layers[0]["water_mm"] = min(layers[0]["saturation_mm"], layers[0]["water_mm"] + infiltration)


def _advance_soil_water(layers: list[dict[str, float]], *, soil_evaporation: float, transpiration: float, root_depth_mm: float) -> float:
    if layers and soil_evaporation > 0.0:
        removable = max(0.0, layers[0]["water_mm"] - layers[0]["wilting_mm"])
        layers[0]["water_mm"] -= min(removable, soil_evaporation)

    root_weights = []
    for layer in layers:
        overlap = _root_overlap(layer, root_depth_mm)
        frac = overlap / max(layer["depth_mm"], 1.0)
        extractable = max(0.0, layer["water_mm"] - layer["wilting_mm"]) * frac
        root_weights.append(extractable * max(0.0, layer.get("kl", 0.04)))
    total_root = sum(root_weights) or 1.0
    for layer, weight in zip(layers, root_weights):
        demand = transpiration * weight / total_root
        removable = max(0.0, layer["water_mm"] - layer["wilting_mm"])
        layer["water_mm"] -= min(removable, demand)
    drainage = 0.0
    for index, layer in enumerate(layers):
        excess = max(0.0, layer["water_mm"] - layer["field_capacity_mm"])
        move = min(excess, layer["ksat_mm_day"])
        layer["water_mm"] -= move
        if index + 1 < len(layers):
            layers[index + 1]["water_mm"] = min(layers[index + 1]["saturation_mm"], layers[index + 1]["water_mm"] + move)
        else:
            drainage += move
    return drainage


def _public_soil_layers(layers: list[dict[str, float]]) -> list[dict[str, float]]:
    out = []
    for layer in layers:
        rel = max(0.0, min(1.2, (layer["water_mm"] - layer["wilting_mm"]) / max(layer["taw_mm"], 1.0)))
        out.append(
            {
                "depth_top_cm": layer["depth_top_cm"],
                "depth_bottom_cm": layer["depth_bottom_cm"],
                "soil_water_mm": round(layer["water_mm"], 3),
                "relative_available_water": round(rel, 4),
            }
        )
    return out


def _water_risk(
    relative_water: float,
    profile: CropProfile,
    potential_t: float,
    actual_t: float,
    *,
    stage_name: str,
    thermal_progress: float,
    bbch: int,
) -> str:
    return str(
        _water_diagnosis(
            relative_water,
            profile,
            potential_t,
            actual_t,
            stage_name=stage_name,
            thermal_progress=thermal_progress,
            bbch=bbch,
            root_depth_m=1.0,
            top_layer_relative_water=None,
            forecast_rain_3d_mm=0.0,
        )["stress_risk"]
    )


def _water_diagnosis(
    relative_water: float,
    profile: CropProfile,
    potential_t: float,
    actual_t: float,
    *,
    stage_name: str,
    thermal_progress: float,
    bbch: int,
    root_depth_m: float,
    top_layer_relative_water: float | None,
    forecast_rain_3d_mm: float,
) -> dict[str, Any]:
    if profile.name == "maize":
        if not _maize_stage_at_or_after(stage_name, "VE") or _maize_progress_at_or_after(profile, thermal_progress, "R6") or potential_t < 0.2:
            return _water_diagnosis_result("LOW")
        return _maize_water_diagnosis(
            relative_water=relative_water,
            potential_t=potential_t,
            actual_t=actual_t,
            stage_name=stage_name,
            root_depth_m=root_depth_m,
            top_layer_relative_water=top_layer_relative_water,
            forecast_rain_3d_mm=forecast_rain_3d_mm,
        )
    elif bbch < 8 or bbch >= 90 or potential_t < 0.2:
        return _water_diagnosis_result("LOW")
    if profile.name == "cotton":
        return _cotton_water_diagnosis(
            relative_water=relative_water,
            profile=profile,
            potential_t=potential_t,
            actual_t=actual_t,
            forecast_rain_3d_mm=forecast_rain_3d_mm,
        )
    uptake_ratio = actual_t / max(potential_t, 1e-6)
    if relative_water < profile.water_threshold * 0.72 or uptake_ratio < 0.62:
        return _water_diagnosis_result("HIGH", stress_index=max(0.6, 1.0 - uptake_ratio))
    if relative_water < profile.water_threshold or uptake_ratio < 0.82:
        return _water_diagnosis_result("MEDIUM", stress_index=max(0.3, 1.0 - uptake_ratio))
    return _water_diagnosis_result("LOW", stress_index=max(0.0, 1.0 - uptake_ratio))


def _cotton_water_diagnosis(
    *,
    relative_water: float,
    profile: CropProfile,
    potential_t: float,
    actual_t: float,
    forecast_rain_3d_mm: float,
) -> dict[str, Any]:
    medium_threshold = profile.water_threshold
    high_threshold = profile.water_threshold * 0.72
    uptake_ratio = actual_t / max(potential_t, 1e-6)
    common = {
        "medium_threshold": medium_threshold,
        "high_threshold": high_threshold,
        "target_threshold": profile.water_target,
    }
    if relative_water < high_threshold or uptake_ratio < 0.62:
        basis = ["LOW_ROOT_WATER" if relative_water < high_threshold else "LOW_TRANSPIRATION"]
        low_rain_3d = forecast_rain_3d_mm < 8.0
        if low_rain_3d:
            basis.append("LOW_3D_RAIN")
        return _water_diagnosis_result(
            "HIGH",
            stress_index=max(0.6, 1.0 - uptake_ratio),
            basis=basis,
            irrigation_recommended=low_rain_3d,
            **common,
        )
    if relative_water < medium_threshold or uptake_ratio < 0.82:
        return _water_diagnosis_result("MEDIUM", stress_index=max(0.3, 1.0 - uptake_ratio), **common)
    return _water_diagnosis_result("LOW", stress_index=max(0.0, 1.0 - uptake_ratio), **common)


def _water_diagnosis_result(
    stress_risk: str,
    *,
    stress_index: float = 0.0,
    stage_band: str = "",
    stage_band_label: str = "",
    medium_threshold: float | None = None,
    high_threshold: float | None = None,
    target_threshold: float | None = None,
    basis: list[str] | None = None,
    irrigation_recommended: bool = False,
) -> dict[str, Any]:
    return {
        "stress_risk": stress_risk,
        "stress_index": round(max(0.0, min(1.0, float(stress_index or 0.0))), 4),
        "stage_band": stage_band,
        "stage_band_label": stage_band_label,
        "medium_threshold": medium_threshold,
        "high_threshold": high_threshold,
        "target_threshold": target_threshold,
        "basis": basis or [],
        "irrigation_recommended": bool(irrigation_recommended),
    }


def _maize_water_diagnosis(
    *,
    relative_water: float,
    potential_t: float,
    actual_t: float,
    stage_name: str,
    root_depth_m: float,
    top_layer_relative_water: float | None,
    forecast_rain_3d_mm: float,
) -> dict[str, Any]:
    band = _maize_water_stage_band(stage_name)
    thresholds = MAIZE_WATER_STAGE_THRESHOLDS[band]
    medium_threshold = float(thresholds["medium"])
    high_threshold = float(thresholds["high"])
    target_threshold = float(thresholds["target"])
    stage_label = str(thresholds["label"])
    uptake_ratio = actual_t / max(potential_t, 1e-6)
    basis: list[str] = []

    status = "LOW"
    stress_index = max(0.0, 1.0 - uptake_ratio)
    if relative_water < high_threshold or uptake_ratio < 0.70:
        status = "HIGH"
        basis.append("LOW_ROOT_WATER" if relative_water < high_threshold else "LOW_TRANSPIRATION")
        stress_index = max(stress_index, (high_threshold - relative_water) / max(high_threshold, 1e-6) + 0.60)
    elif relative_water < medium_threshold or uptake_ratio < 0.88:
        status = "MEDIUM"
        basis.append("LOW_ROOT_WATER" if relative_water < medium_threshold else "LOW_TRANSPIRATION")
        stress_index = max(stress_index, (medium_threshold - relative_water) / max(medium_threshold, 1e-6) + 0.30)

    shallow_root = root_depth_m < 0.60
    surface_dry = top_layer_relative_water is not None and top_layer_relative_water < 0.55
    low_rain_3d = forecast_rain_3d_mm < 8.0
    critical_stage = band in {"V12_VT", "R1_R2"}

    if shallow_root and surface_dry:
        basis.extend(["SURFACE_DRY", "SHALLOW_ROOT"])
    if critical_stage:
        basis.append("CRITICAL_STAGE")
    if low_rain_3d:
        basis.append("LOW_3D_RAIN")

    severe_deficit = relative_water < max(0.0, high_threshold - 0.05) or uptake_ratio < 0.65
    irrigation_recommended = False
    if status == "HIGH" and (low_rain_3d or severe_deficit):
        irrigation_recommended = True
    elif critical_stage and status == "MEDIUM" and low_rain_3d:
        irrigation_recommended = True

    return _water_diagnosis_result(
        status,
        stress_index=stress_index,
        stage_band=band,
        stage_band_label=stage_label,
        medium_threshold=medium_threshold,
        high_threshold=high_threshold,
        target_threshold=target_threshold,
        basis=sorted(set(basis), key=basis.index),
        irrigation_recommended=irrigation_recommended,
    )


def _maize_water_stage_band(stage_name: str) -> str:
    rank = _maize_stage_rank(stage_name)
    if rank is None or rank <= MAIZE_IOWA_STAGE_RANK["V5"]:
        return "VE_V5"
    if rank <= MAIZE_IOWA_STAGE_RANK["V11"]:
        return "V6_V11"
    if rank <= MAIZE_IOWA_STAGE_RANK["VT"]:
        return "V12_VT"
    if rank <= MAIZE_IOWA_STAGE_RANK["R2"]:
        return "R1_R2"
    if rank <= MAIZE_IOWA_STAGE_RANK["R4"]:
        return "R3_R4"
    return "R5_R6"


def _plant_population_m2(payload: dict[str, Any], profile: CropProfile) -> float:
    direct_keys = ("plant_population_m2", "sowing_density_plants_m2", "plant_density_plants_m2")
    for key in direct_keys:
        value = _positive_float(payload.get(key))
        if value is not None:
            return value

    for event in payload.get("applied_sowings") or []:
        for key in direct_keys:
            value = _positive_float(event.get(key))
            if value is not None:
                return value
        for key in ("plant_density_plants_ha", "sowing_density_plants_ha", "population_plants_ha"):
            value = _positive_float(event.get(key))
            if value is not None:
                return value / 10000.0

    return profile.default_plant_population_m2


def _positive_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number > 0.0 and number < 1e9:
        return number
    return None


def _potential_grain_sink_capacity(profile: CropProfile, plant_population_m2: float) -> float:
    if profile.name == "cotton":
        return _cotton_seed_cotton_sink_capacity(plant_population_m2)
    return max(0.0, plant_population_m2 * profile.max_grains_per_plant * profile.max_grain_size_g * 10.0)


def _cotton_retained_bolls_per_plant(plant_population_m2: float) -> float:
    return _linear_interp(
        plant_population_m2,
        [4.0, 8.0, 12.0, 18.0, 26.0, 34.0],
        [14.5, 12.0, 9.5, 7.5, 5.8, 4.6],
    )


def _cotton_seed_cotton_mass_per_boll_g(plant_population_m2: float) -> float:
    return _linear_interp(
        plant_population_m2,
        [4.0, 8.0, 12.0, 18.0, 26.0, 34.0],
        [6.2, 5.8, 5.4, 4.8, 4.3, 3.9],
    )


def _cotton_seed_cotton_sink_capacity(plant_population_m2: float) -> float:
    retained_bolls_m2 = plant_population_m2 * _cotton_retained_bolls_per_plant(plant_population_m2)
    return max(0.0, retained_bolls_m2 * _cotton_seed_cotton_mass_per_boll_g(plant_population_m2) * 10.0)


def _cotton_seed_cotton_sink_for_stage(*, plant_population_m2: float, bbch: float, stress_factor: float) -> float:
    if bbch < 61.0:
        return 0.0
    capacity = _cotton_seed_cotton_sink_capacity(plant_population_m2)
    progress = max(0.0, min(1.0, (bbch - 61.0) / 24.0))
    stage_fraction = progress * progress * (3.0 - 2.0 * progress)
    retained_fraction = max(0.55, min(1.0, stress_factor))
    return capacity * stage_fraction * retained_fraction


def _cotton_boll_number_from_sink(grain_sink_kg_ha: float, plant_population_m2: float) -> float:
    return max(0.0, grain_sink_kg_ha / max(_cotton_seed_cotton_mass_per_boll_g(plant_population_m2) * 10.0, 1e-6))


def _nutrient_growth_factor(uptake_ratio: float) -> float:
    ratio = max(0.0, min(1.2, uptake_ratio))
    if ratio >= 1.0:
        return 1.0
    if ratio >= 0.80:
        return 0.90 + (ratio - 0.80) / 0.20 * 0.10
    if ratio >= 0.55:
        return 0.65 + (ratio - 0.55) / 0.25 * 0.25
    return max(0.30, ratio / 0.55 * 0.65)


def _nutrient_risk_status(nutrient: str, uptake_ratio: float) -> str:
    ratio = max(0.0, min(1.2, uptake_ratio))
    if nutrient == "N":
        if ratio < 0.55:
            return "HIGH"
        if ratio < 0.78:
            return "MEDIUM"
        return "LOW"
    if ratio < 0.65:
        return "HIGH"
    if ratio < 0.90:
        return "MEDIUM"
    return "LOW"


def _crop_n_management_risk_status(
    *,
    profile: CropProfile,
    stage_name: str,
    thermal_progress: float,
    bbch: int,
    uptake_status: str,
    projected_gap_kg_ha: float,
) -> str:
    action_profile = ACTION_PROFILES.get(profile.name)
    if action_profile is None:
        return uptake_status
    status = str(uptake_status or "LOW").upper()
    has_target_gap = projected_gap_kg_ha >= action_profile.min_projected_n_gap_kg_ha
    has_current_stress = status in {"HIGH", "MEDIUM", "DEFICIENT", "WATCH"}
    if not has_target_gap and not has_current_stress:
        return "LOW"
    if profile.name == "maize":
        min_stage = action_profile.n_topdress_stage_min or "V9"
        max_stage = action_profile.n_topdress_stage_max or "R1"
        if _maize_stage_between(stage_name, min_stage, max_stage) and (has_target_gap or status == "HIGH"):
            return "HIGH"
        if _maize_stage_before(stage_name, min_stage):
            return "MEDIUM" if status == "HIGH" else "LOW"
        return "MEDIUM"
    if action_profile.n_topdress_bbch_min is None or action_profile.n_topdress_bbch_max is None:
        return uptake_status
    if action_profile.n_topdress_bbch_min <= int(bbch or 0) <= action_profile.n_topdress_bbch_max and (has_target_gap or status == "HIGH"):
        return "HIGH"
    if int(bbch or 0) < action_profile.n_topdress_bbch_min:
        return "MEDIUM" if status == "HIGH" else "LOW"
    return "MEDIUM"


def _nutrient_supported_grain_yield(
    nutrient_uptake_cum: dict[str, float],
    profile: CropProfile,
    enabled_nutrients: set[str],
) -> float:
    ceilings = []
    for nutrient, coeff in profile.nutrient_coeff.items():
        if nutrient in enabled_nutrients and coeff > 0.0:
            ceilings.append(nutrient_uptake_cum[nutrient] / coeff)
    return min(ceilings) if ceilings else 1e9


def _nitrogen_volatilization_loss(
    nitrogen_release_kg_ha: float,
    *,
    weather: dict[str, float],
    precipitation_mm: float,
    irrigation_mm: float,
    irrigation_method: str,
    mulch_enabled: bool,
) -> float:
    release = max(0.0, float(nitrogen_release_kg_ha or 0.0))
    if release <= 0.0:
        return 0.0
    mean_temperature = float(weather.get("temperature_2m_mean", 20.0) or 20.0)
    temp_factor = _linear_interp(mean_temperature, [8.0, 25.0, 36.0], [0.45, 1.0, 1.35])
    wetting_mm = max(0.0, precipitation_mm + irrigation_mm)
    if wetting_mm >= 15.0:
        incorporation_factor = 0.35
    elif wetting_mm >= 5.0:
        incorporation_factor = 0.65
    else:
        incorporation_factor = 1.0
    method = irrigation_method.lower()
    method_factor = 0.35 if "drip" in method or "fertigation" in method else 1.0
    mulch_factor = 0.75 if mulch_enabled else 1.0
    loss_fraction = min(0.18, 0.055 * temp_factor * incorporation_factor * method_factor * mulch_factor)
    return min(release, release * loss_fraction)


def _nitrogen_leaching_loss(
    available_n_kg_ha: float,
    *,
    drainage_mm: float,
    relative_water: float,
    irrigation_method: str,
) -> float:
    available = max(0.0, float(available_n_kg_ha or 0.0))
    drainage = max(0.0, float(drainage_mm or 0.0))
    if available <= 0.0 or drainage <= 0.0:
        return 0.0
    method = irrigation_method.lower()
    if "flood" in method:
        method_factor = 1.10
    elif "sprinkler" in method:
        method_factor = 0.85
    elif "drip" in method:
        method_factor = 0.45
    else:
        method_factor = 1.0
    wetness_factor = _linear_interp(relative_water, [0.45, 0.80, 1.10], [0.40, 0.85, 1.15])
    mobile_fraction = _linear_interp(relative_water, [0.45, 0.80, 1.10], [0.20, 0.45, 0.60])
    hydraulic_loss_fraction = min(0.045, drainage / 360.0 * method_factor * wetness_factor)
    return min(available, available * mobile_fraction * hydraulic_loss_fraction)


def _daily_demand_fraction(progress: float) -> float:
    if progress < 0.10 or progress > 0.95:
        return 0.002
    peak = exp(-((progress - 0.55) ** 2) / 0.045)
    return 0.002 + 0.018 * peak


def _seasonal_demand_fraction_sum(phenology_rows: list[dict[str, Any]]) -> float:
    total = sum(_daily_demand_fraction(float(row.get("ThermalProgress", 0.0) or 0.0)) for row in phenology_rows)
    return max(total, 1e-6)


def _protection_or_weather_risk(
    *,
    day: date,
    bbch: int,
    stage_start: int,
    hourly: list[dict[str, Any]],
    applied: dict[date, list[dict[str, Any]]],
    protection_days_key: str,
    default_protection_days: int,
    decision_kind: str,
) -> str:
    for event_day, events in applied.items():
        if event_day > day:
            continue
        for event in events:
            days = int(event.get(protection_days_key) or event.get("preventive_protection_days") or default_protection_days)
            if 0 <= (day - event_day).days <= days:
                return "PROTECTED"
    if bbch < stage_start or not hourly:
        return "LOW"
    wet_hours = sum(1 for row in hourly if float(row.get("relativehumidity_2m", row.get("relative_humidity_2m", 0.0)) or 0.0) >= 88.0 or float(row.get("precipitation", 0.0) or 0.0) > 0.0)
    temps = [float(row.get("temperature_2m", 20.0) or 20.0) for row in hourly]
    mean_temp = sum(temps) / max(len(temps), 1)
    if decision_kind == "insect":
        score = max(0.0, min(1.0, (mean_temp - 12.0) / 18.0)) * 0.65 + max(0.0, min(1.0, (12.0 - wet_hours) / 12.0)) * 0.35
    else:
        temp_fit = max(0.0, 1.0 - abs(mean_temp - 24.0) / 15.0)
        score = min(1.0, wet_hours / 12.0) * 0.65 + temp_fit * 0.35
    if score >= 0.72:
        return "HIGH"
    if score >= 0.48:
        return "MEDIUM"
    return "LOW"


def _risk_factor(status: Any) -> float:
    status = str(status or "LOW").upper()
    return {"HIGH": 0.68, "DEFICIENT": 0.72, "MEDIUM": 0.88, "WATCH": 0.92}.get(status, 1.0)


def _grain_set_stress_factor(water_factor: float, nutrition_factor: float) -> float:
    source_factor = max(0.0, min(1.0, water_factor * nutrition_factor))
    return max(0.72, min(1.0, 0.72 + 0.28 * source_factor))


def _worst_status(values: Any) -> str:
    statuses = [str(value or "LOW").upper() for value in values or []]
    return max(statuses, key=lambda value: RISK_ORDER.get(value, 0)) if statuses else "LOW"


def _senescence_fraction(bbch: int) -> float:
    if bbch < 75:
        return 0.0
    return min(0.04, (bbch - 74) * 0.003)


def _grain_allocation_fraction(bbch: float) -> float:
    if bbch < 61:
        return 0.0
    if bbch < 70:
        return _linear_interp(bbch, [61.0, 70.0], [0.55, 0.85])
    if bbch < 85:
        return _linear_interp(bbch, [70.0, 85.0], [0.85, 0.98])
    if bbch < 95:
        return _linear_interp(bbch, [85.0, 95.0], [0.98, 0.85])
    return 0.0


def _grain_set_window_started(*, profile: CropProfile, stage_name: str, thermal_progress: float, bbch: int) -> bool:
    if profile.name == "maize":
        return _maize_stage_at_or_after(stage_name, "VT")
    return bbch >= 61


def _grain_allocation_fraction_for_profile(profile: CropProfile, thermal_progress: float, bbch: float) -> float:
    if profile.name != "maize":
        return _grain_allocation_fraction(bbch)
    progress = float(thermal_progress or 0.0)
    vt = _stage_progress_threshold(profile, "VT")
    r1 = _stage_progress_threshold(profile, "R1")
    r3 = _stage_progress_threshold(profile, "R3")
    r5 = _stage_progress_threshold(profile, "R5")
    r6 = _stage_progress_threshold(profile, "R6")
    if progress < vt:
        return 0.0
    if progress < r1:
        return _linear_interp(progress, [vt, r1], [0.55, 0.85])
    if progress < r3:
        return _linear_interp(progress, [r1, r3], [0.85, 0.98])
    if progress < r5:
        return 0.98
    if progress < r6:
        return _linear_interp(progress, [r5, r6], [0.98, 0.85])
    return 0.0


def _grain_fill_fraction(bbch: float) -> float:
    if bbch < 61:
        return 0.0
    if bbch >= 95:
        return 1.0
    progress = (bbch - 61.0) / 34.0
    return max(0.0, min(1.0, progress * progress * (3.0 - 2.0 * progress)))


def _grain_fill_fraction_for_profile(profile: CropProfile, thermal_progress: float, bbch: float) -> float:
    if profile.name != "maize":
        return _grain_fill_fraction(bbch)
    progress = float(thermal_progress or 0.0)
    vt = _stage_progress_threshold(profile, "VT")
    r6 = _stage_progress_threshold(profile, "R6")
    if progress < vt:
        return 0.0
    if progress >= r6:
        return 1.0
    fill_progress = (progress - vt) / max(r6 - vt, 1e-9)
    return max(0.0, min(1.0, fill_progress * fill_progress * (3.0 - 2.0 * fill_progress)))


def _grain_remobilization_fraction(bbch: float) -> float:
    if bbch < 75:
        return 0.0
    if bbch < 90:
        return _linear_interp(bbch, [75.0, 90.0], [0.0015, 0.005])
    if bbch < 95:
        return _linear_interp(bbch, [90.0, 95.0], [0.005, 0.002])
    return 0.0


def _grain_remobilization_fraction_for_profile(profile: CropProfile, thermal_progress: float, bbch: float) -> float:
    if profile.name != "maize":
        return _grain_remobilization_fraction(bbch)
    progress = float(thermal_progress or 0.0)
    r3 = _stage_progress_threshold(profile, "R3")
    r5 = _stage_progress_threshold(profile, "R5")
    r6 = _stage_progress_threshold(profile, "R6")
    if progress < r3:
        return 0.0
    if progress < r5:
        return _linear_interp(progress, [r3, r5], [0.0015, 0.005])
    if progress < r6:
        return _linear_interp(progress, [r5, r6], [0.005, 0.002])
    return 0.0


def _append_daily_risk(
    daily_risk: dict[str, Any],
    row: dict[str, Any],
    nutrient_status: dict[str, str],
    nutrient_demand_cum: dict[str, float],
    nutrient_supply: dict[str, float],
) -> None:
    day = row["Date"]
    daily_risk["water"].append(
        {
            "Date": day,
            "target_code": "DROUGHT",
            "stress_risk": row["water_stress_risk"],
            "stress_index": row["water_stress_index"],
            "root_zone_relative_available_water": row["root_zone_relative_available_water"],
            "top_layer_relative_available_water": row.get("top_layer_relative_available_water"),
            "forecast_rain_3d_mm": row.get("forecast_rain_3d_mm"),
            "water_stress_stage_band": row.get("water_stress_stage_band"),
            "water_stress_stage_band_label": row.get("water_stress_stage_band_label"),
            "water_medium_threshold": row.get("water_medium_threshold"),
            "water_high_threshold": row.get("water_high_threshold"),
            "water_target_threshold": row.get("water_target_threshold"),
            "water_stress_basis": row.get("water_stress_basis") or [],
            "irrigation_recommended": row.get("irrigation_recommended"),
            "potential_transpiration_mm": row["potential_transpiration_mm"],
            "actual_transpiration_mm": row["actual_transpiration_mm"],
        }
    )
    stress_index_by_nutrient = {"N": row["n_stress_index"], "P2O5": row["p_stress_index"], "K2O": row["k_stress_index"]}
    for nutrient in ("N", "P2O5", "K2O"):
        daily_risk["nutrition"][nutrient].append(
            {
                "Date": day,
                "target_code": nutrient,
                "stress_risk": nutrient_status[nutrient],
                "stress_index": stress_index_by_nutrient[nutrient],
                "cumulative_demand_kg_ha": round(nutrient_demand_cum[nutrient], 3),
                "available_kg_ha": round(nutrient_supply[nutrient], 3),
            }
        )
    weed_targets = row.get("weed_target_codes") or []
    for code in weed_targets:
        daily_risk["weed"].append(
            {
                "Date": day,
                "target_code": code,
                "eppo_code": code,
                "name_cn": MAIZE_POST_WEED_NAMES.get(code),
                "stress_risk": row["weed_stress_risk"],
                "stress_index": row["weed_stress_index"],
            }
        )
    if row.get("lodging_risk") is not None:
        daily_risk["lodging"].append(
            {
                "Date": day,
                "target_code": "LODGING",
                "stress_risk": row["lodging_risk"],
                "stress_index": row["lodging_risk_index"],
                "raw_stress_index": row["lodging_risk_index_raw"],
                "status": row["growth_regulation_status"],
                "basis": row["growth_regulation_basis"],
            }
        )


def _weed_state_for_day(
    *,
    profile: CropProfile,
    payload: dict[str, Any],
    day: date,
    stage_name: Any,
    applied_herbicides: dict[date, list[dict[str, Any]]],
) -> dict[str, Any]:
    codes = _weed_target_codes_for_payload(profile, payload)
    if not codes:
        return {
            "stress_risk": "LOW",
            "field_risk": "LOW",
            "target_code": None,
            "target_codes": [],
            "stress_index": 0.0,
        }
    if _active_herbicide_coverage(applied_herbicides, day, codes):
        status = "PROTECTED"
        index = 0.0
    elif str(stage_name or "").upper() in MAIZE_POST_HERBICIDE_STAGES:
        status = "HIGH"
        index = 1.0
    else:
        status = "LOW"
        index = 0.0
    return {
        "stress_risk": status,
        "field_risk": status,
        "target_code": ",".join(codes),
        "target_codes": codes,
        "stress_index": index,
    }


def _weed_target_codes_for_payload(profile: CropProfile, payload: dict[str, Any]) -> list[str]:
    if profile.name != "maize":
        return []
    return _normalize_weed_codes(payload.get("weed_eppo_codes") or [])


def _weed_stress_risk(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_code: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        code = str(row.get("target_code") or "")
        if not code:
            continue
        by_code.setdefault(code, []).append(dict(row))
    return {code: sorted(items, key=lambda item: str(item.get("Date") or "")) for code, items in sorted(by_code.items())}


def _carried_nutrition_stress_risk(
    daily_nutrition: dict[str, list[dict[str, Any]]],
    applied_fertilizers: dict[date, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    carried: dict[str, list[dict[str, Any]]] = {}
    for nutrient, rows in daily_nutrition.items():
        sorted_rows = sorted(rows, key=lambda item: str(item.get("Date") or ""))
        if nutrient == "N":
            # N management risk is recomputed daily and may only be raised to HIGH
            # inside the configured topdress window. Do not carry that HIGH beyond it.
            carried[nutrient] = [dict(row) for row in sorted_rows]
            continue
        carry_status = "LOW"
        managed_since_fertilizer = False
        target_rows: list[dict[str, Any]] = []
        for row in sorted_rows:
            item = dict(row)
            day = _date_key(item.get("Date"))
            if day is not None and _fertilizer_supplies_nutrient(applied_fertilizers.get(day, []), nutrient):
                carry_status = "LOW"
                managed_since_fertilizer = True
                item["stress_risk"] = "LOW"
            else:
                if managed_since_fertilizer:
                    current_status = _current_nutrition_stress_status(item)
                    item["stress_risk"] = current_status
                    if current_status == "HIGH":
                        carry_status = "HIGH"
                        managed_since_fertilizer = False
                    else:
                        carry_status = current_status
                else:
                    carry_status = _worse_risk_status(carry_status, item.get("stress_risk"))
                    item["stress_risk"] = carry_status
            target_rows.append(item)
        carried[nutrient] = target_rows
    return carried


def _current_nutrition_stress_status(row: dict[str, Any]) -> str:
    stress_index = float(row.get("stress_index") or 0.0)
    available = float(row.get("available_kg_ha") or 0.0)
    if stress_index >= 0.35:
        return "HIGH"
    if stress_index >= 0.12:
        return "MEDIUM"
    if available <= 0.5 and stress_index >= 0.05:
        return "MEDIUM"
    return "LOW"


def _overall_field_risk(
    stress_risk: dict[str, Any],
    *,
    season_start: date,
    season_end: date,
) -> list[dict[str, Any]]:
    statuses_by_day: dict[date, list[Any]] = {day: [] for day in _date_range(season_start, season_end)}
    for domain_targets in stress_risk.values():
        target_groups = domain_targets.values() if isinstance(domain_targets, dict) else [domain_targets]
        for rows in target_groups:
            for row in rows or []:
                day = _date_key(row.get("Date") or row.get("date"))
                if day in statuses_by_day:
                    statuses_by_day[day].append(row.get("stress_risk") or row.get("field_risk"))
    return [
        {"Date": day.isoformat(), "field_risk": _worst_status(statuses)}
        for day, statuses in statuses_by_day.items()
    ]


def _fertilizer_supplies_nutrient(events: list[dict[str, Any]], nutrient: str) -> bool:
    wanted = nutrient.upper()
    for event in events:
        nutrients = event.get("nutrients_kg_ha") or {}
        for key, value in nutrients.items():
            if str(key).upper() == wanted and float(value or 0.0) > 0.0:
                return True
    return False


def _worse_risk_status(left: Any, right: Any) -> str:
    left_status = str(left or "LOW").upper()
    right_status = str(right or "LOW").upper()
    return left_status if RISK_ORDER.get(left_status, 0) >= RISK_ORDER.get(right_status, 0) else right_status


def _carry_forward_stress_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    carry_status = "LOW"
    carry_index = 0.0
    carried: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: str(item.get("Date") or "")):
        item = dict(row)
        status = str(item.get("stress_risk") or "LOW").upper()
        if status in {"OUT_OF_SEASON", "PROTECTED"}:
            item["stress_risk"] = status
            carry_status = "LOW"
            carry_index = 0.0
        else:
            item_index = float(item.get("stress_index", 0.0) or 0.0)
            output_status = _worse_risk_status(carry_status, status)
            if RISK_ORDER.get(output_status, 0) > RISK_ORDER.get(status, 0):
                item["stress_index"] = round(max(carry_index, item_index), 4)
            else:
                item["stress_index"] = round(item_index, 4)
            item["stress_risk"] = output_status
            carry_status = output_status
            carry_index = float(item.get("stress_index", 0.0) or 0.0)
        carried.append(item)
    return carried


def _unit_interval(value: float, low: float, high: float) -> float:
    return max(0.0, min(1.0, (value - low) / max(high - low, 1e-9)))


def _cumulative_applied_nutrients(
    events_by_day: dict[date, list[dict[str, Any]]],
    day: date,
) -> dict[str, float]:
    totals = {"N": 0.0, "P2O5": 0.0, "K2O": 0.0}
    for event_day, events in events_by_day.items():
        if event_day > day:
            continue
        for event in events:
            for nutrient, amount in _event_nutrients(event).items():
                if nutrient in totals:
                    totals[nutrient] += max(0.0, float(amount or 0.0))
    return totals


def _cotton_leaf_expansion_multiplier(
    *,
    profile: CropProfile,
    day: date,
    applied_growth_regulators: dict[date, list[dict[str, Any]]],
    applied_toppings: dict[date, list[dict[str, Any]]],
) -> float:
    if profile.name != "cotton":
        return 1.0
    multiplier = 1.0
    for event_day, events in applied_growth_regulators.items():
        for event in events:
            effect_days = max(1, int(event.get("effect_days") or 10))
            if 0 <= (day - event_day).days < effect_days:
                multiplier *= float(event.get("leaf_expansion_multiplier") or 0.82)
    if any(event_day <= day and events for event_day, events in applied_toppings.items()):
        multiplier *= float(COTTON_MANAGEMENT_POLICY["topping"]["post_topping_leaf_expansion_multiplier"])
    return max(0.05, min(1.0, multiplier))


def _cotton_harvest_aid_daily_abscission(
    *,
    profile: CropProfile,
    day: date,
    applied_harvest_aids: dict[date, list[dict[str, Any]]],
) -> float:
    if profile.name != "cotton":
        return 0.0
    daily_fraction = 0.0
    for event_day, events in applied_harvest_aids.items():
        for event in events:
            response_days = max(1, int(event.get("response_days") or COTTON_MANAGEMENT_POLICY["harvest_aid"]["default_response_days"]))
            elapsed = (day - event_day).days
            if 0 <= elapsed < response_days:
                target = max(0.01, min(0.99, float(event.get("target_defoliation_pct") or 90.0) / 100.0))
                daily_fraction = max(daily_fraction, 1.0 - (1.0 - target) ** (1.0 / response_days))
    return daily_fraction


def _cotton_open_bolls_pct(bbch: int) -> float:
    if bbch < 80:
        return 0.0
    return round(max(0.0, min(100.0, (float(bbch) - 80.0) / 15.0 * 100.0)), 1)


def _cotton_harvest_aid_progress(
    day: date,
    applied_harvest_aids: dict[date, list[dict[str, Any]]],
) -> tuple[float, date | None, dict[str, Any] | None]:
    candidates = [
        (event_day, event)
        for event_day, events in applied_harvest_aids.items()
        if event_day <= day
        for event in events
    ]
    if not candidates:
        return 0.0, None, None
    event_day, event = min(candidates, key=lambda item: item[0])
    response_days = max(1, int(event.get("response_days") or COTTON_MANAGEMENT_POLICY["harvest_aid"]["default_response_days"]))
    target = float(event.get("target_defoliation_pct") or COTTON_MANAGEMENT_POLICY["harvest_aid"]["default_target_defoliation_pct"])
    progress = min(1.0, max(0.0, (day - event_day).days / response_days))
    return round(target * progress, 1), event_day, event


def _cotton_calendar_window(day: date, start: tuple[int, int], end: tuple[int, int]) -> bool:
    return date(day.year, *start) <= day <= date(day.year, *end)


def _eligible_cotton_growth_regulator(
    payload: dict[str, Any],
    bbch: int,
    application_index: int,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for product in payload.get("growth_regulator_inventory") or []:
        crops = {str(value).lower() for value in product.get("registered_crops") or []}
        if "cotton" not in crops or application_index >= int(product.get("max_applications") or 1):
            continue
        window = product.get("label_bbch_window") or {}
        if not int(window.get("min", 0)) <= bbch <= int(window.get("max", 99)):
            continue
        schedules = [
            entry
            for entry in product.get("dose_schedule") or []
            if int(entry.get("bbch_min", 0)) <= bbch <= int(entry.get("bbch_max", 99))
        ]
        if schedules:
            candidates.append((product, schedules[0]))
    if not candidates:
        return None
    return min(candidates, key=lambda item: (int(item[0].get("priority", 100)), str(item[0].get("product_key") or "")))


def _eligible_harvest_aid(payload: dict[str, Any]) -> dict[str, Any] | None:
    candidates = [
        product
        for product in payload.get("harvest_aid_inventory") or []
        if "cotton" in {str(value).lower() for value in product.get("registered_crops") or []}
    ]
    return min(candidates, key=lambda item: (int(item.get("priority", 100)), str(item.get("product_key") or ""))) if candidates else None


def _cotton_management_state(
    *,
    payload: dict[str, Any],
    profile: CropProfile,
    day: date,
    bbch: int,
    live_lai: float,
    plant_population_m2: float,
    water_factor: float,
    nutrition_factor: float,
    weather: dict[str, Any],
    daily_weather: dict[date, dict[str, Any]],
    applied_growth_regulators: dict[date, list[dict[str, Any]]],
    applied_toppings: dict[date, list[dict[str, Any]]],
    applied_harvest_aids: dict[date, list[dict[str, Any]]],
    applied_harvests: dict[date, list[dict[str, Any]]],
) -> dict[str, Any]:
    settings = _dump(payload.get("cotton_management") or {})
    if profile.name != "cotton" or not bool(settings.get("enabled", False)):
        return {}

    canopy = COTTON_MANAGEMENT_POLICY["canopy"]
    density = plant_population_m2 * 10000.0
    components = {
        "lai": _unit_interval(live_lai, *canopy["lai_range"]),
        "density": _unit_interval(density, *canopy["density_plants_ha_range"]),
        "yield_target": _unit_interval(float(payload.get("yield_target_kg_ha") or 0.0), *canopy["yield_target_kg_ha_range"]),
        "resources": max(0.0, min(1.0, (water_factor + nutrition_factor) / 2.0)),
    }
    vigor = round(sum(float(canopy["weights"][key]) * value for key, value in components.items()), 4)
    prior_regulator_events = [
        (event_day, event)
        for event_day, events in applied_growth_regulators.items()
        if event_day <= day
        for event in events
    ]
    regulator_match = _eligible_cotton_growth_regulator(payload, bbch, len(prior_regulator_events))
    last_regulator_day = max((event_day for event_day, _event in prior_regulator_events), default=None)
    interval_ok = True
    if regulator_match and last_regulator_day is not None:
        interval_ok = (day - last_regulator_day).days >= int(regulator_match[0].get("min_interval_days") or 10)
    regulator_in_window = 51 <= bbch <= 79
    regulator_status = (
        "DISABLED"
        if not bool(settings.get("canopy_regulation", True))
        else "ACTIONABLE"
        if regulator_in_window and vigor >= float(canopy["vigor_threshold"]) and regulator_match and interval_ok
        else "NO_ELIGIBLE_PRODUCT"
        if regulator_in_window and vigor >= float(canopy["vigor_threshold"]) and not regulator_match
        else "WAIT_INTERVAL"
        if regulator_in_window and vigor >= float(canopy["vigor_threshold"]) and not interval_ok
        else "WATCH"
        if regulator_in_window
        else "OUT_OF_WINDOW"
    )

    topping = COTTON_MANAGEMENT_POLICY["topping"]
    topped = any(event_day <= day and events for event_day, events in applied_toppings.items())
    topping_window = (
        int(topping["bbch_min"]) <= bbch <= int(topping["bbch_max"])
        and _cotton_calendar_window(day, topping["calendar_start"], topping["calendar_end"])
    )
    topping_status = (
        "DISABLED"
        if not bool(settings.get("topping", True))
        else "MANAGED"
        if topped
        else "ACTIONABLE"
        if topping_window and len(prior_regulator_events) >= int(topping["minimum_prior_regulator_applications"])
        else "WAIT_PRIOR_CONTROL"
        if topping_window
        else "OUT_OF_WINDOW"
    )

    open_bolls = _cotton_open_bolls_pct(bbch)
    defoliation, harvest_aid_day, harvest_aid_event = _cotton_harvest_aid_progress(day, applied_harvest_aids)
    harvest_aid_policy = COTTON_MANAGEMENT_POLICY["harvest_aid"]
    aid_product = _eligible_harvest_aid(payload)
    aid_applied = harvest_aid_day is not None
    rainfast_days = max(1, int((float((aid_product or {}).get("rainfast_hours") or 24.0) + 23.0) // 24.0))
    rain_during_rainfast = sum(
        float((daily_weather.get(day + timedelta(days=offset)) or {}).get("precipitation_sum", 0.0) or 0.0)
        for offset in range(rainfast_days + 1)
    )
    aid_weather_ok = bool(aid_product) and (
        float(weather.get("temperature_2m_mean") or 0.0) >= float(aid_product.get("minimum_mean_temperature_c") or 12.0)
        and float(weather.get("windspeed_10m_mean") or 0.0) <= float(aid_product.get("max_wind_speed_m_s") or 4.0)
        and rain_during_rainfast <= 0.1
    )
    aid_window = (
        bbch >= int(harvest_aid_policy["bbch_min"])
        and open_bolls >= float((aid_product or {}).get("minimum_open_bolls_pct") or 30.0)
        and live_lai >= float(harvest_aid_policy["minimum_live_lai"])
        and _cotton_calendar_window(day, harvest_aid_policy["calendar_start"], harvest_aid_policy["calendar_end"])
    )
    harvest_aid_status = (
        "DISABLED"
        if not bool(settings.get("harvest_aid", True))
        else "MANAGED"
        if aid_applied
        else "NO_ELIGIBLE_PRODUCT"
        if aid_window and aid_product is None
        else "WAIT_WEATHER"
        if aid_window and not aid_weather_ok
        else "ACTIONABLE"
        if aid_window
        else "OUT_OF_WINDOW"
    )

    harvest_policy = COTTON_MANAGEMENT_POLICY["harvest"]
    harvested = any(event_day <= day and events for event_day, events in applied_harvests.items())
    preharvest_days = int((harvest_aid_event or {}).get("preharvest_interval_days") or 10)
    preharvest_ok = harvest_aid_day is not None and (day - harvest_aid_day).days >= preharvest_days
    harvest_weather_ok = float(weather.get("precipitation_sum") or 0.0) <= 0.1
    harvest_ready = (
        preharvest_ok
        and defoliation >= float(harvest_policy["minimum_defoliation_pct"])
        and open_bolls >= float(harvest_policy["minimum_open_bolls_pct"])
        and harvest_weather_ok
    )
    harvest_status = (
        "DISABLED"
        if not bool(settings.get("machine_harvest", True))
        else "HARVESTED"
        if harvested
        else "ACTIONABLE"
        if harvest_ready
        else "WAITING"
    )
    return {
        "canopy_vigor_index": vigor,
        "cotton_canopy_regulation_status": regulator_status,
        "cotton_canopy_regulation_basis": {
            "components": {key: round(value, 4) for key, value in components.items()},
            "weights": deepcopy(canopy["weights"]),
            "plant_density_plants_ha": round(density, 1),
            "bbch": bbch,
            "prior_applications": len(prior_regulator_events),
        },
        "topping_status": topping_status,
        "estimated_open_bolls_pct": open_bolls,
        "estimated_defoliation_pct": defoliation,
        "harvest_aid_status": harvest_aid_status,
        "harvest_aid_basis": {
            "bbch": bbch,
            "live_lai": round(live_lai, 3),
            "open_bolls_pct": open_bolls,
            "mean_temperature_c": round(float(weather.get("temperature_2m_mean") or 0.0), 2),
            "wind_speed_m_s": round(float(weather.get("windspeed_10m_mean") or 0.0), 2),
            "rain_during_rainfast_mm": round(rain_during_rainfast, 2),
        },
        "machine_harvest_status": harvest_status,
        "machine_harvest_basis": {
            "estimated_defoliation_pct": defoliation,
            "estimated_open_bolls_pct": open_bolls,
            "preharvest_interval_satisfied": preharvest_ok,
            "dry_weather": harvest_weather_ok,
        },
    }


def _growth_regulation_state(
    *,
    payload: dict[str, Any],
    profile: CropProfile,
    day: date,
    iowa_stage: str,
    plant_population_m2: float,
    cumulative_applied_nutrients: dict[str, float],
    seasonal_max_wind_m_s: float,
    applied_growth_regulators: dict[date, list[dict[str, Any]]],
) -> dict[str, Any]:
    settings = _dump(payload.get("growth_regulation") or {})
    enabled = profile.name == "maize" and bool(settings.get("enabled", False))
    if not enabled:
        return {}

    config = GROWTH_REGULATION_CONFIG
    density_config = config["density_plants_ha"]
    density = plant_population_m2 * 10000.0
    density_score = _unit_interval(density, density_config["low"], density_config["high"])

    yield_config = config["yield_target_kg_ha"]
    yield_target = float(payload.get("yield_target_kg_ha") or 0.0)
    yield_score = _unit_interval(yield_target, yield_config["low"], yield_config["high"])

    n_config = config["fertilizer_n_kg_ha"]
    k_config = config["fertilizer_k2o_kg_ha"]
    cumulative_n = float(cumulative_applied_nutrients.get("N", 0.0) or 0.0)
    cumulative_k2o = float(cumulative_applied_nutrients.get("K2O", 0.0) or 0.0)
    n_score = _unit_interval(cumulative_n, n_config["low"], n_config["high"])
    k_score = _unit_interval(cumulative_k2o, k_config["low"], k_config["high"])
    fertilizer_score = max(0.0, min(1.0, n_score - float(config["potassium_credit"]) * k_score))

    wind_config = config["seasonal_max_wind_m_s"]
    wind_score = _unit_interval(seasonal_max_wind_m_s, wind_config["low"], wind_config["high"])
    stage = _maize_stage_label(iowa_stage)

    components = {
        "density": density_score,
        "yield_target": yield_score,
        "fertilizer": fertilizer_score,
        "wind": wind_score,
    }
    raw_index = round(sum(float(config["weights"][key]) * value for key, value in components.items()), 4)
    managed = any(event_day <= day and events for event_day, events in applied_growth_regulators.items())
    adjusted_index = round(raw_index * (1.0 - float(config["managed_risk_reduction_fraction"])), 4) if managed else raw_index
    thresholds = config["risk_thresholds"]
    modeled_risk = "HIGH" if adjusted_index >= thresholds["high"] else "MEDIUM" if adjusted_index >= thresholds["medium"] else "LOW"
    risk = "PROTECTED" if managed else modeled_risk
    in_window = _maize_stage_between(stage, config["stage_min"], config["stage_max"])
    eligible_product = _eligible_growth_regulator(payload, stage) if in_window else None
    status = (
        "MANAGED"
        if managed
        else "NO_ELIGIBLE_PRODUCT"
        if in_window and risk in {"MEDIUM", "HIGH"} and eligible_product is None
        else "ACTIONABLE"
        if in_window and risk in {"MEDIUM", "HIGH"}
        else "WATCH"
        if in_window
        else "OUT_OF_WINDOW"
    )
    return {
        "lodging_risk_index_raw": raw_index,
        "lodging_risk_index": adjusted_index,
        "lodging_risk": risk,
        "growth_regulation_status": status,
        "growth_regulation_basis": {
            "components": {key: round(value, 4) for key, value in components.items()},
            "weights": {key: float(value) for key, value in config["weights"].items()},
            "plant_density_plants_ha": round(density, 1),
            "yield_target_kg_ha": round(yield_target, 3),
            "cumulative_applied_n_kg_ha": round(cumulative_n, 3),
            "cumulative_applied_k2o_kg_ha": round(cumulative_k2o, 3),
            "seasonal_max_wind_m_s": round(seasonal_max_wind_m_s, 3),
            "density_range_plants_ha": [density_config["low"], density_config["high"]],
            "yield_target_range_kg_ha": [yield_config["low"], yield_config["high"]],
            "fertilizer_n_range_kg_ha": [n_config["low"], n_config["high"]],
            "fertilizer_k2o_range_kg_ha": [k_config["low"], k_config["high"]],
            "potassium_credit": float(config["potassium_credit"]),
            "seasonal_max_wind_range_m_s": [wind_config["low"], wind_config["high"]],
            "iowa_stage": stage,
            "model_stage_window": [config["stage_min"], config["stage_max"]],
        },
    }


def _eligible_growth_regulator(payload: dict[str, Any], stage: Any) -> dict[str, Any] | None:
    candidates = []
    for product in payload.get("growth_regulator_inventory") or []:
        window = product.get("label_stage_window") or {}
        if _maize_stage_between(stage, window.get("min"), window.get("max")):
            candidates.append(product)
    return min(candidates, key=lambda item: (int(item.get("priority", 100)), str(item.get("product_key") or ""))) if candidates else None


def _maybe_add_actions(
    *,
    actions: list[dict[str, Any]],
    action_ids: set[str],
    open_episodes: dict[str, bool],
    emitted_domains: set[str],
    payload: dict[str, Any],
    profile: CropProfile,
    day: date,
    row: dict[str, Any],
    nutrient_status: dict[str, str],
    soil_layers: list[dict[str, float]],
    root_depth: float,
    applied_by_day: dict[str, dict[date, list[dict[str, Any]]]],
) -> None:
    if (
        _irrigation_actionable(row)
        and automatic_action_stage_allowed(row, profile, "irrigation")
        and "irrigation" not in emitted_domains
        and not open_episodes["irrigation"]
        and not applied_by_day["irrigation"].get(day)
    ):
        root_capacity = _root_zone_capacity(soil_layers, root_depth)
        depth = _irrigation_depth_mm(
            profile=profile,
            row=row,
            root_capacity=root_capacity,
            irrigation_method=payload.get("irrigation_method"),
        )
        event = {"date": day.isoformat(), "amount_mm": round(depth, 1), "method": payload.get("irrigation_method"), "notes": "decision-date daily engine irrigation recommendation"}
        _add_action(
            actions,
            action_ids,
            "irrigation",
            day,
            "DROUGHT",
            event,
            {
                "recommendationCode": "IRRIGATE",
                "recommendedGrossDepthMm": event["amount_mm"],
                "recommendedIrrigationDate": day.isoformat(),
                "triggerDomain": "water",
                "triggerTarget": "DROUGHT",
                "triggerRisk": row.get("water_stress_risk"),
                "triggerStressIndex": row.get("water_stress_index"),
                "triggerBasis": ",".join(str(item) for item in row.get("water_stress_basis") or []),
                "waterStressStageBand": row.get("water_stress_stage_band"),
                "waterStressStageBandLabel": row.get("water_stress_stage_band_label"),
                "waterMediumThreshold": row.get("water_medium_threshold"),
                "waterHighThreshold": row.get("water_high_threshold"),
                "rootZoneRelativeAvailableWater": row.get("root_zone_relative_available_water"),
                "topLayerRelativeAvailableWater": row.get("top_layer_relative_available_water"),
                "forecastRain3dMm": row.get("forecast_rain_3d_mm"),
                "targetRelativeAvailableWater": row.get("water_target_threshold"),
            },
        )
        open_episodes["irrigation"] = True
        emitted_domains.add("irrigation")
    fertilization_allowed = _fertilization_actionable(row, profile)
    n_topdress_window = _crop_n_topdress_window(row, profile)
    foliar_candidate = None
    if (
        "fertilization" not in emitted_domains
        and not open_episodes["fertilization"]
        and not applied_by_day["fertilization"].get(day)
    ):
        foliar_candidate = _foliar_nutrition_event(
            payload=payload,
            profile=profile,
            day=day,
            row=row,
            applied_fertilizers=applied_by_day["fertilization"],
        )
    foliar_targets = set(
        ((foliar_candidate or {}).get("event") or {}).get("target_nutrients") or []
    )
    deficient = []
    pk_topdress_amounts: dict[str, float] = {}
    for nutrient, status in nutrient_status.items():
        if not fertilization_allowed or not _high_action(status) or (nutrient == "N" and n_topdress_window):
            continue
        if profile.name == "maize" and nutrient == "K2O" and "K2O" in foliar_targets:
            continue
        if profile.name == "maize" and nutrient in {"P2O5", "K2O"}:
            amount = _crop_pk_topdress(row, profile, applied_by_day["fertilization"], day, nutrient)
            if amount <= 0.0:
                continue
            pk_topdress_amounts[nutrient] = amount
        deficient.append(nutrient)
    topdress_n = (
        _crop_n_topdress(row, profile, applied_by_day["fertilization"], day)
        if fertilization_allowed and _high_action(nutrient_status.get("N"))
        else 0.0
    )
    fertilization_targets = ["N", *deficient] if topdress_n > 0.0 else deficient
    action_profile = ACTION_PROFILES.get(profile.name) or CropActionProfile(0, 99)
    recent_fertilizer_day = _recent_fertilizer_application_day(
        applied_by_day["fertilization"],
        day,
    )
    if (
        fertilization_targets
        and "fertilization" not in emitted_domains
        and not open_episodes["fertilization"]
        and not applied_by_day["fertilization"].get(day)
        and (
            recent_fertilizer_day is None
            or (day - recent_fertilizer_day).days >= action_profile.min_fertilizer_application_interval_days
        )
    ):
        nutrients = {
            nutrient: round(
                action_profile.default_n_kg_ha
                if nutrient == "N"
                else pk_topdress_amounts.get(nutrient, action_profile.default_pk_kg_ha),
                3,
            )
            for nutrient in fertilization_targets
        }
        notes = "decision-date daily engine fertilizer recommendation"
        if topdress_n > 0.0:
            nutrients["N"] = round(topdress_n, 3)
            notes = f"{profile.name} N topdress at configured in-season N window"
        product = _select_fertilizer_product(payload, nutrients)
        product_key = product.get("product_key") or product.get("display_name") or "fertilizer"
        amount_kg_ha, supplied_nutrients = _fertilizer_amount_and_supplied_nutrients(product, nutrients)
        method = "fertigation" if payload.get("fertigation_enabled") else "topdress_before_irrigation"
        event = {
            "Date": day.isoformat(),
            "date": day.isoformat(),
            "product_uuid": product.get("uuid"),
            "product_key": product_key,
            "product_name": product.get("name"),
            "display_name": product.get("display_name"),
            "amount_kg_ha": amount_kg_ha,
            "application_method": method,
            "method": method,
            "nutrients_kg_ha": supplied_nutrients,
            "target_nutrients": fertilization_targets,
            "release_type": "quick_release",
            "release_days": 3,
            "notes": notes,
        }
        source = {
            "recommendationCode": "FERTILIZE",
            "targets": [{"target": nutrient, "nutrient_amount": amount} for nutrient, amount in nutrients.items()],
            "triggerDomain": "nutrition",
            "triggerTarget": ",".join(fertilization_targets),
            "triggerRisk": row.get("n_stress_risk") if fertilization_targets == ["N"] else "HIGH",
        }
        if topdress_n > 0.0:
            source.update(
                {
                    "triggerBasis": "PROJECTED_N_GAP",
                    "targetYieldKgHa": row.get("yield_target_kg_ha"),
                    "managementTargetNNeedKgHa": row.get("management_target_n_need_kg_ha"),
                    "absorbedNkgHa": row.get("cumulative_n_uptake_kg_ha"),
                    "availableNkgHa": row.get("available_n_kg_ha"),
                    "pendingReleaseNkgHa": row.get("pending_n_release_kg_ha"),
                    "projectedNGapKgHa": row.get("projected_n_gap_kg_ha"),
                }
            )
        if pk_topdress_amounts:
            source.update(
                {
                    "triggerBasis": "TARGET_YIELD_PK_GAP",
                    "targetYieldKgHa": row.get("yield_target_kg_ha"),
                    "projectedPKGapKgHa": {
                        nutrient: _crop_pk_remaining_gap(row, profile, applied_by_day["fertilization"], day, nutrient)
                        for nutrient in pk_topdress_amounts
                    },
                    "availableP2O5kgHa": row.get("available_p2o5_kg_ha"),
                    "availableK2OkgHa": row.get("available_k2o_kg_ha"),
                    "pendingReleaseP2O5kgHa": row.get("pending_p2o5_release_kg_ha"),
                    "pendingReleaseK2OkgHa": row.get("pending_k2o_release_kg_ha"),
                }
            )
        _add_action(
            actions,
            action_ids,
            "fertilization",
            day,
            ",".join(fertilization_targets),
            event,
            source,
        )
        open_episodes["fertilization"] = True
        emitted_domains.add("fertilization")
    if (
        "fertilization" not in emitted_domains
        and not open_episodes["fertilization"]
        and not applied_by_day["fertilization"].get(day)
    ):
        if foliar_candidate is not None:
            target = ",".join(foliar_candidate["event"].get("target_nutrients") or ["FOLIAR"])
            _add_action(
                actions,
                action_ids,
                "fertilization",
                day,
                target,
                foliar_candidate["event"],
                foliar_candidate["source"],
            )
            open_episodes["fertilization"] = True
            emitted_domains.add("fertilization")
    if _high_action(row["disease_stress_risk"]) and "fungicide" not in emitted_domains and not open_episodes["fungicide"] and not applied_by_day["fungicide"].get(day):
        target = row.get("dominant_disease_target") or "DISEASE"
        if not _active_fungicide_coverage(applied_by_day["fungicide"], day, target, profile):
            product = _select_fungicide_product(payload, target, profile)
            window = product.get("efficacy_window_days") or {}
            target_diseases = list(product.get("target_diseases") or [])
            if not target_diseases:
                target_diseases = [target]
            event = {
                "date": day.isoformat(),
                "applied_date": day.isoformat(),
                "product_uuid": product.get("uuid"),
                "product_key": product.get("product_key") or product.get("name") or "fungicide",
                "product_name": product.get("name"),
                "display_name": product.get("display_name"),
                "application_method": "foliar_spray",
                "curative_efficacy": 0.72,
                "curative_protection_days": window.get("curative_days", 5),
                "preventive_efficacy": 0.82,
                "preventive_protection_days": window.get("protective_days", 14),
                "stress": target,
                "target_diseases": target_diseases,
            }
            _add_action(
                actions,
                action_ids,
                "fungicide",
                day,
                target,
                event,
                {
                    "recommendationCode": "SPRAY",
                    "treatmentStartDate": day.isoformat(),
                    "treatmentEndDate": (day + timedelta(days=2)).isoformat(),
                    "triggerDomain": "disease",
                    "triggerTarget": target,
                    "triggerRisk": row.get("disease_stress_risk"),
                    "triggerStressIndex": row.get("disease_stress_index"),
                },
            )
            open_episodes["fungicide"] = True
            emitted_domains.add("fungicide")
    if (
        _insecticide_actionable(row, profile)
        and "insecticide" not in emitted_domains
        and not open_episodes["insecticide"]
        and not applied_by_day["insecticide"].get(day)
        and not _active_insecticide_residual(applied_by_day["insecticide"], day)
    ):
        target = row.get("dominant_insect_target") or "INSECT"
        event = _default_insecticide_event(day, payload, profile)
        source = {
            "recommendationCode": "SPRAY",
            "treatmentStartDate": day.isoformat(),
            "treatmentEndDate": (day + timedelta(days=2)).isoformat(),
            "triggerDomain": "insect",
            "triggerTarget": target,
            "triggerRisk": row.get("insect_stress_risk"),
            "triggerStressIndex": row.get("insect_stress_index"),
        }
        if _spodex_low_instar_iowa_actionable(row, profile) and not _high_action(row.get("insect_stress_risk")):
            source["triggerBasis"] = "SPODEX_LOW_INSTAR_IOWA_V6_V8"
        _add_action(
            actions,
            action_ids,
            "insecticide",
            day,
            target,
            event,
            source,
        )
        open_episodes["insecticide"] = True
        emitted_domains.add("insecticide")
    if (
        _high_action(row.get("weed_stress_risk"))
        and "herbicide" not in emitted_domains
        and not open_episodes["herbicide"]
        and not applied_by_day["herbicide"].get(day)
    ):
        weed_codes = list(row.get("weed_target_codes") or _weed_target_codes_for_payload(profile, payload))
        if weed_codes and not _active_herbicide_coverage(applied_by_day["herbicide"], day, weed_codes):
            prescription = _herbicide_prescription_for_targets(weed_codes, payload)
            event = _herbicide_event_for_prescription(day, payload, prescription)
            target = ",".join(prescription["matched_target_codes"])
            _add_action(
                actions,
                action_ids,
                "herbicide",
                day,
                target,
                event,
                {
                    "recommendationCode": "SPRAY",
                    "treatmentStartDate": day.isoformat(),
                    "treatmentEndDate": (day + timedelta(days=2)).isoformat(),
                    "triggerDomain": "weed",
                    "triggerTarget": target,
                    "triggerRisk": row.get("weed_stress_risk"),
                    "triggerStressIndex": row.get("weed_stress_index"),
                    "prescription": prescription,
                },
            )
            open_episodes["herbicide"] = True
            emitted_domains.add("herbicide")
    if (
        profile.name == "cotton"
        and row.get("cotton_canopy_regulation_status") == "ACTIONABLE"
        and "growth_regulator" not in emitted_domains
        and not open_episodes["growth_regulator"]
        and not applied_by_day["growth_regulator"].get(day)
    ):
        application_index = sum(
            len(events)
            for event_day, events in applied_by_day["growth_regulator"].items()
            if event_day <= day
        )
        match = _eligible_cotton_growth_regulator(payload, int(row.get("BBCH") or 0), application_index)
        if match is not None:
            product, dose = match
            event = {
                "Date": day.isoformat(),
                "date": day.isoformat(),
                "applied_date": day.isoformat(),
                "product_uuid": product["uuid"],
                "product_key": product["product_key"],
                "product_name": product.get("name"),
                "display_name": product.get("display_name") or product.get("name") or product["product_key"],
                "active_ingredients": product.get("active_ingredients"),
                "formulation": product.get("formulation"),
                "dose_value": dose["recommended"],
                "dose_unit": dose["unit"],
                "application_method": product.get("application_method") or "foliar_spray",
                "method": "foliar_spray",
                "target": "CANOPY_VIGOR",
                "rainfast_hours": product.get("rainfast_hours"),
                "allow_tank_mix": bool(product.get("allow_tank_mix", False)),
                "label_bbch_window": deepcopy(product["label_bbch_window"]),
                "dose_schedule": deepcopy(product["dose_schedule"]),
                "max_applications": product.get("max_applications"),
                "min_interval_days": product.get("min_interval_days"),
                "effect_days": product.get("effect_days"),
                "leaf_expansion_multiplier": product.get("leaf_expansion_multiplier"),
                "scenario_fixture": bool(product.get("scenario_fixture", False)),
                "notes": product.get("notes"),
            }
            _add_action(
                actions,
                action_ids,
                "growth_regulator",
                day,
                "CANOPY_VIGOR",
                event,
                {
                    "recommendationCode": "GROWTH_REGULATE",
                    "treatmentStartDate": day.isoformat(),
                    "treatmentEndDate": day.isoformat(),
                    "triggerDomain": "canopy",
                    "triggerTarget": "CANOPY_VIGOR",
                    "triggerRisk": "HIGH",
                    "triggerStressIndex": row.get("canopy_vigor_index"),
                    "triggerBasis": row.get("cotton_canopy_regulation_basis"),
                    "recommendedDose": {"value": dose["recommended"], "unit": dose["unit"]},
                },
            )
            open_episodes["growth_regulator"] = True
            emitted_domains.add("growth_regulator")
    if (
        profile.name == "cotton"
        and row.get("topping_status") == "ACTIONABLE"
        and "topping" not in emitted_domains
        and not open_episodes["topping"]
        and not applied_by_day["topping"].get(day)
    ):
        event = {
            "Date": day.isoformat(),
            "date": day.isoformat(),
            "operation": "topping",
            "method": "mechanical_or_manual",
            "target": "REMOVE_APICAL_GROWING_POINT",
            "notes": "棉花打顶；具体机械或人工方式由田间条件确定",
        }
        _add_action(
            actions,
            action_ids,
            "topping",
            day,
            "REMOVE_APICAL_GROWING_POINT",
            event,
            {
                "recommendationCode": "TOP_COTTON",
                "triggerDomain": "canopy",
                "triggerTarget": "REMOVE_APICAL_GROWING_POINT",
                "triggerRisk": "ACTIONABLE",
                "triggerBasis": {"bbch": row.get("BBCH"), "prior_growth_regulator_applications": sum(len(events) for event_day, events in applied_by_day["growth_regulator"].items() if event_day <= day)},
            },
        )
        open_episodes["topping"] = True
        emitted_domains.add("topping")
    if (
        profile.name == "cotton"
        and row.get("harvest_aid_status") == "ACTIONABLE"
        and "harvest_aid" not in emitted_domains
        and not open_episodes["harvest_aid"]
        and not applied_by_day["harvest_aid"].get(day)
    ):
        product = _eligible_harvest_aid(payload)
        if product is not None:
            dose = product["label_dose"]
            event = {
                "Date": day.isoformat(),
                "date": day.isoformat(),
                "applied_date": day.isoformat(),
                "product_uuid": product["uuid"],
                "product_key": product["product_key"],
                "product_name": product.get("name"),
                "display_name": product.get("display_name") or product.get("name") or product["product_key"],
                "active_ingredients": product.get("active_ingredients"),
                "formulation": product.get("formulation"),
                "dose_value": dose["recommended"],
                "dose_unit": dose["unit"],
                "application_method": product.get("application_method") or "foliar_spray",
                "method": "foliar_spray",
                "target": "DEFOLIATION_RIPENING",
                "rainfast_hours": product.get("rainfast_hours"),
                "response_days": product.get("response_days"),
                "preharvest_interval_days": product.get("preharvest_interval_days"),
                "target_defoliation_pct": product.get("target_defoliation_pct"),
                "scenario_fixture": bool(product.get("scenario_fixture", False)),
                "notes": product.get("notes"),
            }
            _add_action(
                actions,
                action_ids,
                "harvest_aid",
                day,
                "DEFOLIATION_RIPENING",
                event,
                {
                    "recommendationCode": "APPLY_HARVEST_AID",
                    "treatmentStartDate": day.isoformat(),
                    "treatmentEndDate": day.isoformat(),
                    "triggerDomain": "harvest_aid",
                    "triggerTarget": "DEFOLIATION_RIPENING",
                    "triggerRisk": "ACTIONABLE",
                    "triggerBasis": row.get("harvest_aid_basis"),
                    "recommendedDose": {"value": dose["recommended"], "unit": dose["unit"]},
                },
            )
            open_episodes["harvest_aid"] = True
            emitted_domains.add("harvest_aid")
    if (
        profile.name == "cotton"
        and row.get("machine_harvest_status") == "ACTIONABLE"
        and "harvest" not in emitted_domains
        and not open_episodes["harvest"]
        and not applied_by_day["harvest"].get(day)
    ):
        event = {
            "Date": day.isoformat(),
            "date": day.isoformat(),
            "operation": "machine_harvest",
            "method": "machine_harvest",
            "target": "SEED_COTTON",
        }
        _add_action(
            actions,
            action_ids,
            "harvest",
            day,
            "SEED_COTTON",
            event,
            {
                "recommendationCode": "MACHINE_HARVEST",
                "triggerDomain": "harvest",
                "triggerTarget": "SEED_COTTON",
                "triggerRisk": "ACTIONABLE",
                "triggerBasis": row.get("machine_harvest_basis"),
            },
        )
        open_episodes["harvest"] = True
        emitted_domains.add("harvest")
    if (
        profile.name == "maize"
        and
        row.get("growth_regulation_status") == "ACTIONABLE"
        and "growth_regulator" not in emitted_domains
        and not open_episodes["growth_regulator"]
        and not any(events for event_day, events in applied_by_day["growth_regulator"].items() if event_day <= day)
    ):
        growth_basis = row.get("growth_regulation_basis") or {}
        product = _eligible_growth_regulator(payload, growth_basis.get("iowa_stage"))
        if product is not None:
            dose = product["label_dose"]
            event = {
                "Date": day.isoformat(),
                "date": day.isoformat(),
                "applied_date": day.isoformat(),
                "product_uuid": product["uuid"],
                "product_key": product["product_key"],
                "product_name": product.get("name"),
                "display_name": product.get("display_name") or product.get("name") or product["product_key"],
                "active_ingredients": product.get("active_ingredients"),
                "formulation": product.get("formulation"),
                "dose_value": dose["recommended"],
                "dose_unit": dose["unit"],
                "dealer_recommended_dose": deepcopy(product.get("dealer_recommended_dose") or {}),
                "application_method": product.get("application_method") or "foliar_spray",
                "method": "foliar_spray",
                "target": "LODGING",
                "rainfast_hours": product.get("rainfast_hours"),
                "allow_tank_mix": bool(product.get("allow_tank_mix", False)),
                "label_stage_window": deepcopy(product["label_stage_window"]),
            }
            _add_action(
                actions,
                action_ids,
                "growth_regulator",
                day,
                "LODGING",
                event,
                {
                    "recommendationCode": "GROWTH_REGULATE",
                    "treatmentStartDate": day.isoformat(),
                    "treatmentEndDate": (day + timedelta(days=2)).isoformat(),
                    "triggerDomain": "lodging",
                    "triggerTarget": "LODGING",
                    "triggerRisk": row.get("lodging_risk"),
                    "triggerStressIndex": row.get("lodging_risk_index"),
                    "triggerBasis": row.get("growth_regulation_basis"),
                    "recommendedDose": {"value": dose["recommended"], "unit": dose["unit"]},
                },
            )
            open_episodes["growth_regulator"] = True
            emitted_domains.add("growth_regulator")


def _needs_action(status: Any) -> bool:
    return str(status or "LOW").upper() in {"MEDIUM", "HIGH", "DEFICIENT", "WATCH"}


def _high_action(status: Any) -> bool:
    return str(status or "LOW").upper() == "HIGH"


def _insecticide_actionable(row: dict[str, Any], profile: CropProfile) -> bool:
    if not automatic_action_stage_allowed(row, profile, "insecticide"):
        return False
    return _high_action(row.get("insect_stress_risk")) or _spodex_low_instar_iowa_actionable(row, profile)


def automatic_action_stage_allowed(
    row: dict[str, Any], profile: CropProfile, domain: str,
) -> bool:
    """Separate a modeled risk from permission to issue a routine operation."""
    if profile.name != "maize":
        return True
    policy = ACTION_PROFILES["maize"]
    stage_max = getattr(policy, f"automatic_{domain}_stage_max", None)
    stage_rank = _maize_stage_rank(_row_stage_label(row))
    maximum_rank = _maize_stage_rank(stage_max) if stage_max is not None else None
    return stage_rank is None or maximum_rank is None or stage_rank <= maximum_rank


def _spodex_low_instar_iowa_actionable(row: dict[str, Any], profile: CropProfile) -> bool:
    if profile.name != "maize":
        return False
    target = str(row.get("dominant_insect_target") or "").upper()
    risk = str(row.get("insect_stress_risk") or "").upper()
    stress_index = float(row.get("insect_stress_index") or 0.0)
    return (
        target == "SPODEX"
        and _maize_stage_between(_row_stage_label(row), "V6", "V8")
        and stress_index >= 0.70
        and risk in {"MEDIUM", "HIGH"}
    )


def _foliar_nutrition_event(
    *,
    payload: dict[str, Any],
    profile: CropProfile,
    day: date,
    row: dict[str, Any],
    applied_fertilizers: dict[date, list[dict[str, Any]]],
) -> dict[str, dict[str, Any]] | None:
    policy = _foliar_nutrition_policy(profile)
    if not policy:
        return None
    if profile.name == "maize":
        if not _maize_row_stage_between(row, str(policy.get("stage_min") or "VE"), str(policy.get("stage_max") or "R3")):
            return None
        bbch = None
    else:
        bbch = int(row.get("BBCH") or 0)
        if bbch < int(policy.get("bbch_min", 15)) or bbch > int(policy.get("bbch_max", 85)):
            return None

    min_interval_days = int(policy.get("min_interval_days", 10))
    soil_test = payload.get("soil_test") or {}
    target_nutrients: list[str] = []
    nutrients_kg_ha: dict[str, float] = {}
    micros_g_ha: dict[str, float] = {}
    reasons: list[str] = []
    trigger_bases: list[str] = []

    if profile.name == "maize":
        default_micro_allowed = _maize_row_stage_between(
            row,
            str(policy.get("default_micro_stage_min") or "V9"),
            str(policy.get("default_micro_stage_max") or "VT"),
        )
    else:
        default_micro_allowed = (
            int(policy.get("default_micro_bbch_min", policy.get("bbch_min", 15)))
            <= bbch
            <= int(policy.get("default_micro_bbch_max", policy.get("bbch_max", 85)))
        )
    if bool(policy.get("default_missing_micronutrients", True)) and default_micro_allowed:
        default_micros = policy.get("default_micros_g_ha") or {}
        soil_keys = policy.get("micronutrient_soil_keys") or {"Zn": "zn_mg_kg", "B": "b_mg_kg"}
        for target, soil_key in soil_keys.items():
            if soil_test.get(soil_key) is None and not _foliar_target_already_applied(applied_fertilizers, day, target):
                amount = float(default_micros.get(target, 0.0) or 0.0)
                if amount > 0.0:
                    target_nutrients.append(target)
                    micros_g_ha[target] = round(amount, 3)
                    reasons.append(f"soil_test.{soil_key} missing; default {profile.name} foliar {target} supplement")
                    trigger_bases.append(f"MISSING_{target.upper()}_SOIL_TEST")

    if profile.name == "maize":
        rescue_allowed = _maize_row_stage_between(
            row,
            str(policy.get("rescue_stage_min") or "V5"),
            str(policy.get("rescue_stage_max") or "R3"),
        )
    else:
        rescue_allowed = (
            int(policy.get("rescue_bbch_min", policy.get("bbch_min", 15)))
            <= bbch
            <= int(policy.get("rescue_bbch_max", policy.get("bbch_max", 85)))
        )
    rescue_n_enabled = not (
        bool(policy.get("disable_rescue_n_when_fertigation_enabled", False))
        and bool(payload.get("fertigation_enabled", False))
    )
    if (
        rescue_n_enabled
        and rescue_allowed
        and _high_action(row.get("n_stress_risk"))
        and not _foliar_target_recently_applied(applied_fertilizers, day, "N", min_interval_days)
    ):
        amount = float(policy.get("rescue_n_kg_ha", 0.0) or 0.0)
        if amount > 0.0:
            target_nutrients.append("N")
            nutrients_kg_ha["N"] = round(amount, 3)
            reasons.append(f"{profile.name} N stress is HIGH; foliar N rescue is allowed when regular fertilization is not emitted")
            trigger_bases.append("N_DEFICIENCY_FOLIAR_RESCUE")

    early_k2o = _early_soil_applied_k2o_kg_ha(payload, applied_fertilizers, day, int(policy.get("early_k_application_days", 35)))
    low_early_k2o = early_k2o <= float(policy.get("low_early_k2o_kg_ha", float("inf")))
    prior_k_rescues = _foliar_target_application_count(applied_fertilizers, day, "K2O")
    max_rescue_apps = int(policy.get("max_rescue_apps", 2) or 2)
    if (
        rescue_allowed
        and _high_action(row.get("k2o_stress_risk"))
        and low_early_k2o
        and prior_k_rescues < max_rescue_apps
        and not _foliar_target_recently_applied(applied_fertilizers, day, "K2O", min_interval_days)
    ):
        amount = float(policy.get("rescue_k2o_kg_ha", 0.0) or 0.0)
        if amount > 0.0:
            target_nutrients.append("K2O")
            nutrients_kg_ha["K2O"] = round(amount, 3)
            reasons.append(f"{profile.name} K stress is HIGH and early soil-applied K2O is {early_k2o:.1f} kg/ha; add foliar K rescue")
            trigger_bases.append("K_DEFICIENCY_LOW_EARLY_K")

    target_nutrients = _sort_foliar_targets(target_nutrients)
    if not target_nutrients:
        return None

    product = _select_foliar_fertilizer_product(payload, target_nutrients)
    product_key = product.get("product_key") or _foliar_product_key(profile.name, target_nutrients)
    display_name = product.get("display_name") or _foliar_display_name(target_nutrients)

    event = {
        "Date": day.isoformat(),
        "date": day.isoformat(),
        "product_uuid": product.get("uuid"),
        "product_key": product_key,
        "product_name": product.get("name") or product_key,
        "display_name": display_name,
        "amount_kg_ha": round(sum(nutrients_kg_ha.values()) + sum(micros_g_ha.values()) / 1000.0, 3),
        "application_method": "foliar_spray",
        "method": "foliar",
        "nutrients_kg_ha": _canonical_nutrients(nutrients_kg_ha),
        "micros_g_ha": micros_g_ha,
        "target_nutrients": target_nutrients,
        "release_type": "quick_release",
        "release_days": int(policy.get("release_days", 1) or 1),
        "foliar_absorption_efficiency": float(policy.get("absorption_efficiency", 0.55) or 0.55),
        "allow_tank_mix": bool(product.get("allow_tank_mix", True)),
        "notes": "; ".join(reasons),
    }
    high_targets = [target for target in ("N", "K2O") if target in target_nutrients]
    source = {
        "recommendationCode": "FOLIAR_FERTILIZE",
        "targets": [
            {"target": target, "nutrient_amount": nutrients_kg_ha[target], "unit": "kg_ha"}
            for target in ("N", "K2O")
            if target in nutrients_kg_ha
        ]
        + [
            {"target": target, "nutrient_amount": micros_g_ha[target], "unit": "g_ha"}
            for target in ("Zn", "B")
            if target in micros_g_ha
        ],
        "triggerDomain": "nutrition",
        "triggerTarget": ",".join(target_nutrients),
        "triggerRisk": "HIGH" if high_targets else "DEFAULT_SUPPLEMENT",
        "triggerBasis": ",".join(sorted(set(trigger_bases))),
        "triggerStressIndex": max(float(row.get("n_stress_index") or 0.0), float(row.get("k_stress_index") or 0.0)),
        "availableNkgHa": row.get("available_n_kg_ha"),
        "availableK2OkgHa": row.get("available_k2o_kg_ha"),
        "nDemandKgHa": row.get("n_demand_kg_ha"),
        "k2oDemandKgHa": row.get("k2o_demand_kg_ha"),
    }
    if not high_targets:
        source["triggerException"] = "MISSING_MICRONUTRIENT_SOIL_TEST_DEFAULT_SUPPLEMENT"
    return {"event": event, "source": source}


def _foliar_nutrition_policy(profile: CropProfile) -> dict[str, Any]:
    if profile.name == "maize":
        from crops.maize.nutrition.config import INSEASON_POLICY

        return dict(INSEASON_POLICY.get("foliar_spray") or {})
    if profile.name == "cotton":
        from crops.cotton.config import FOLIAR_NUTRITION_POLICY

        return dict(FOLIAR_NUTRITION_POLICY)
    if profile.name == "wheat":
        from crops.wheat.config import FOLIAR_NUTRITION_POLICY

        return dict(FOLIAR_NUTRITION_POLICY)
    return {}


def _sort_foliar_targets(targets: list[str]) -> list[str]:
    priority = {"N": 0, "P2O5": 1, "K2O": 2, "Zn": 3, "B": 4, "Mg": 5, "S": 6}
    return sorted(set(targets), key=lambda target: (priority.get(target, 99), target))


def _select_foliar_fertilizer_product(payload: dict[str, Any], targets: list[str]) -> dict[str, Any]:
    inventory = [item for item in payload.get("fertilizer_inventory") or [] if isinstance(item, dict)]
    target_set = {_target_key(target) for target in targets}
    for item in inventory:
        methods = {_target_key(method) for method in item.get("application_methods") or item.get("allowed_application_methods") or []}
        if "FOLIAR_SPRAY" not in methods and "FOLIAR" not in methods:
            continue
        product_targets = {_target_key(target) for target in item.get("target_nutrients") or []}
        nutrients = item.get("nutrients") or item.get("npk_ratio") or {}
        if isinstance(nutrients, dict):
            product_targets.update(_target_key(key) for key, value in nutrients.items() if float(value or 0.0) > 0.0)
        micros = item.get("micros_g_kg") or item.get("micronutrients") or {}
        if isinstance(micros, dict):
            product_targets.update(_target_key(key) for key, value in micros.items() if float(value or 0.0) > 0.0)
        if product_targets and target_set <= product_targets:
            return item
    return {}


def _foliar_product_key(crop_name: str, targets: list[str]) -> str:
    suffix = "_".join(str(target).lower().replace("2o", "2o") for target in targets)
    return f"{crop_name}_foliar_{suffix}"


def _foliar_display_name(targets: list[str]) -> str:
    names = {"N": "叶面氮肥", "K2O": "叶面钾肥", "Zn": "叶面锌肥", "B": "叶面硼肥"}
    return "+".join(names.get(target, target) for target in targets)


def _foliar_target_already_applied(
    events_by_day: dict[date, list[dict[str, Any]]],
    day: date,
    target: str,
) -> bool:
    return _foliar_target_recently_applied(events_by_day, day, target, 10_000)


def _foliar_target_application_count(
    events_by_day: dict[date, list[dict[str, Any]]],
    day: date,
    target: str,
) -> int:
    count = 0
    for event_day, events in events_by_day.items():
        if event_day > day:
            continue
        for event in events:
            if _normalize_fertilizer_method(event.get("method") or event.get("application_method")) != "foliar":
                continue
            if target in _event_target_nutrients(event):
                count += 1
    return count


def _early_soil_applied_k2o_kg_ha(
    payload: dict[str, Any],
    events_by_day: dict[date, list[dict[str, Any]]],
    day: date,
    early_days: int,
) -> float:
    planting_day = _date_key(payload.get("planting_date")) or day
    cutoff = min(day, planting_day + timedelta(days=max(0, early_days)))
    total = 0.0
    for event_day, events in events_by_day.items():
        if event_day > cutoff:
            continue
        for event in events:
            if _normalize_fertilizer_method(event.get("method") or event.get("application_method")) == "foliar":
                continue
            total += _event_nutrients(event).get("K2O", 0.0)
    return round(total, 3)


def _foliar_target_recently_applied(
    events_by_day: dict[date, list[dict[str, Any]]],
    day: date,
    target: str,
    interval_days: int,
) -> bool:
    for event_day, events in events_by_day.items():
        if event_day > day or (day - event_day).days >= interval_days:
            continue
        for event in events:
            if _normalize_fertilizer_method(event.get("method") or event.get("application_method")) != "foliar":
                continue
            if target in _event_target_nutrients(event):
                return True
    return False


def _fertilization_actionable(row: dict[str, Any], profile: CropProfile) -> bool:
    action_profile = ACTION_PROFILES.get(profile.name)
    if profile.name == "maize":
        if action_profile is None:
            return not _maize_stage_at_or_after(_row_stage_label(row), "R4")
        return _maize_row_stage_between(
            row,
            action_profile.fertilization_stage_min or "VE",
            action_profile.fertilization_stage_max or "R3",
        )
    bbch = int(row.get("BBCH") or 0)
    if action_profile is None:
        return bbch < 80
    if action_profile.fertilization_bbch_min is None or action_profile.fertilization_bbch_max is None:
        return bbch < 80
    return action_profile.fertilization_bbch_min <= bbch <= action_profile.fertilization_bbch_max


def _active_insecticide_residual(events_by_day: dict[date, list[dict[str, Any]]], day: date) -> bool:
    for event_day, events in events_by_day.items():
        if event_day > day:
            continue
        for event in events:
            residual_days = _insecticide_residual_days(event)
            if residual_days > 0 and 0 <= (day - event_day).days < residual_days:
                return True
    return False


def _active_herbicide_coverage(
    events_by_day: dict[date, list[dict[str, Any]]],
    day: date,
    target_codes: list[str],
) -> bool:
    target_set = {_target_key(code) for code in target_codes}
    for event_day, events in events_by_day.items():
        if event_day > day:
            continue
        for event in events:
            residual_days = _herbicide_residual_days(event)
            if residual_days <= 0 or not (0 <= (day - event_day).days < residual_days):
                continue
            covered = _herbicide_covered_weed_keys(event.get("target_weeds") or [])
            if not covered or covered & target_set:
                return True
    return False


def _herbicide_covered_weed_keys(target_weeds: list[Any]) -> set[str]:
    covered: set[str] = set()
    for target in target_weeds:
        key = _target_key(target)
        if not key:
            continue
        covered.add(key)
        if key in _target_key_set({"annual_grasses", "grass_weeds"}):
            covered.update(_target_key_set(MAIZE_POST_GRASS_WEED_CODES | MAIZE_POST_HARD_GRASS_WEED_CODES | {"ELEIN"}))
        elif key == _target_key("volunteer_wheat"):
            covered.add(_target_key("TRZAS"))
        elif key in _target_key_set({"broadleaf_weeds", "broadleaf"}):
            covered.update(_target_key_set(MAIZE_POST_BROADLEAF_WEED_CODES))
    return covered


def _herbicide_residual_days(event: dict[str, Any]) -> int:
    if event.get("residual_control_days") is not None:
        return max(0, int(float(event.get("residual_control_days") or 0)))
    window = event.get("efficacy_window_days") or {}
    if isinstance(window, dict) and window.get("residual_control_days") is not None:
        return max(0, int(float(window.get("residual_control_days") or 0)))
    return 21


def _herbicide_prescription_for_targets(target_codes: list[str], payload: dict[str, Any]) -> dict[str, Any]:
    codes = [code for code in _normalize_weed_codes(target_codes) if code in MAIZE_POST_WEED_CODES]
    code_set = set(codes)
    if not codes:
        codes = ["DIGSA"]
        code_set = set(codes)

    alternative = None
    separate = False
    if "CYPRO" in code_set:
        matched = ["CYPRO"]
        direction = "氯吡嘧磺隆方向"
        active_ingredients = ["halosulfuron-methyl"]
        separate = True
    elif {"ELEIN", "DIGSA"} <= code_set:
        matched = _ordered_present(codes, {"ELEIN", "DIGSA"} | MAIZE_POST_GRASS_WEED_CODES | MAIZE_POST_HARD_GRASS_WEED_CODES)
        preferred = ["topramezone", "terbuthylazine"]
        fallback = ["topramezone", "nicosulfuron", "atrazine"]
        if _herbicide_inventory_can_cover(payload, preferred):
            direction = "苯唑草酮 + 特丁津"
            active_ingredients = preferred
        elif _has_business_preferred_tembotrione_package(payload, code_set):
            direction = "烟嘧磺隆 + 莠去津 + 环磺酮"
            active_ingredients = ["nicosulfuron", "atrazine", "tembotrione"]
            alternative = "业务方案明确推荐环磺酮方向时使用"
        else:
            direction = "苯唑草酮 + 烟嘧磺隆 + 莠去津"
            active_ingredients = fallback
            alternative = "库存缺少苯唑草酮+特丁津完整组合时使用"
    elif (
        code_set & (MAIZE_POST_GRASS_WEED_CODES | MAIZE_POST_HARD_GRASS_WEED_CODES)
        and code_set & MAIZE_POST_BROADLEAF_WEED_CODES
    ):
        matched = _ordered_present(
            codes,
            MAIZE_POST_GRASS_WEED_CODES | MAIZE_POST_HARD_GRASS_WEED_CODES | MAIZE_POST_BROADLEAF_WEED_CODES,
        )
        direction = "烟嘧磺隆 + 硝磺草酮 + 莠去津"
        active_ingredients = ["nicosulfuron", "mesotrione", "atrazine"]
    elif code_set & MAIZE_POST_HARD_GRASS_WEED_CODES:
        matched = _ordered_present(codes, MAIZE_POST_GRASS_WEED_CODES | MAIZE_POST_HARD_GRASS_WEED_CODES)
        direction = "烟嘧磺隆 + 莠去津 + 环磺酮"
        active_ingredients = ["nicosulfuron", "atrazine", "tembotrione"]
    elif code_set & MAIZE_POST_BROADLEAF_WEED_CODES:
        matched = _ordered_present(codes, MAIZE_POST_BROADLEAF_WEED_CODES)
        second = "atrazine" if _herbicide_inventory_can_cover(payload, ["atrazine"]) else "terbuthylazine"
        direction = "硝磺草酮 + 莠去津/特丁津"
        active_ingredients = ["mesotrione", second]
        if {"AMARE", "CHEAL", "POROL"} <= code_set:
            alternative = "阔叶严重可考虑氯氟吡氧乙酸方向"
    elif code_set & MAIZE_POST_GRASS_WEED_CODES:
        matched = _ordered_present(codes, MAIZE_POST_GRASS_WEED_CODES)
        direction = "烟嘧磺隆 + 莠去津"
        active_ingredients = ["nicosulfuron", "atrazine"]
    else:
        matched = codes
        direction = "烟嘧磺隆 + 莠去津"
        active_ingredients = ["nicosulfuron", "atrazine"]

    return {
        "direction": direction,
        "active_ingredients": active_ingredients,
        "matched_target_codes": matched,
        "matched_target_names": [MAIZE_POST_WEED_NAMES.get(code, code) for code in matched],
        "separate_application_required": separate,
        "alternative_direction": alternative,
    }


def _ordered_present(codes: list[str], wanted: set[str]) -> list[str]:
    return [code for code in codes if code in wanted]


def _herbicide_inventory_can_cover(payload: dict[str, Any], active_ingredients: list[str]) -> bool:
    inventory = [item for item in payload.get("herbicide_inventory") or [] if isinstance(item, dict)]
    if not inventory:
        return False
    required = {_target_key(item) for item in active_ingredients}
    covered: set[str] = set()
    for item in inventory:
        covered.update(_herbicide_product_active_keys(item))
    return required <= covered


def _has_business_preferred_tembotrione_package(payload: dict[str, Any], code_set: set[str]) -> bool:
    target_set = {_target_key(code) for code in code_set}
    required = _target_key_set({"nicosulfuron", "atrazine", "tembotrione"})
    for item in payload.get("herbicide_inventory") or []:
        if not isinstance(item, dict):
            continue
        business_score, _ = _herbicide_business_priority(item, target_set)
        if business_score > 0 and required <= _herbicide_product_active_keys(item):
            return True
    return False


def _herbicide_event_for_prescription(day: date, payload: dict[str, Any], prescription: dict[str, Any]) -> dict[str, Any]:
    product, components = _select_herbicide_product(payload, prescription)
    target_codes = list(prescription.get("matched_target_codes") or [])
    product_ranking = product.get("effect_ranking") or []
    event = {
        "Date": day.isoformat(),
        "date": day.isoformat(),
        "applied_date": day.isoformat(),
        "product_uuid": product.get("uuid"),
        "product_key": product.get("product_key") or product.get("name") or "generic_herbicide_prescription",
        "product_name": product.get("name") or product.get("product_key") or "generic_herbicide_prescription",
        "display_name": product.get("display_name") or product.get("name") or prescription.get("direction"),
        "product_category": "herbicide",
        "application_method": "foliar_spray",
        "method": "foliar_spray",
        "application_timing": "post_emergence",
        "target_weeds": target_codes,
        "target_weed_names": [MAIZE_POST_WEED_NAMES.get(code, code) for code in target_codes],
        "active_ingredients": list(prescription.get("active_ingredients") or []),
        "prescription_direction": prescription.get("direction"),
        "components": components,
        "residual_control_days": _herbicide_residual_days(product),
        "allow_tank_mix": bool(product.get("allow_tank_mix", True)),
        "effect_score": product.get("effect_score"),
        "effect_reasons": product.get("effect_reasons") or [],
        "effect_ranking": product_ranking,
        "separate_application_required": bool(prescription.get("separate_application_required")),
        "notes": "decision-date daily engine herbicide recommendation",
    }
    if len(components) > 1:
        event["product_key"] = "tank_mix:" + "+".join(str(item.get("active_ingredient") or item.get("product_key")) for item in components)
        event["display_name"] = prescription.get("direction")
    return event


def _select_herbicide_product(payload: dict[str, Any], prescription: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    inventory = [item for item in payload.get("herbicide_inventory") or [] if isinstance(item, dict)]
    required = {_target_key(item) for item in prescription.get("active_ingredients") or []}
    full_coverage_candidates = [
        item
        for item in inventory
        if required <= _herbicide_product_active_keys(item) and _herbicide_product_supports_post_emergence(item)
    ]
    target_set = {_target_key(code) for code in prescription.get("matched_target_codes") or []}
    business_candidates = [
        item
        for item in inventory
        if item not in full_coverage_candidates
        and _herbicide_product_supports_post_emergence(item)
        and _herbicide_business_priority(item, target_set)[0] > 0
    ]
    full_coverage_candidates.extend(business_candidates)
    ranked = _rank_herbicide_product_candidates(full_coverage_candidates, prescription)
    if ranked:
        selected = dict(ranked[0]["product"])
        selected["effect_score"] = ranked[0]["effect_score"]
        selected["effect_reasons"] = ranked[0]["effect_reasons"]
        selected["effect_ranking"] = [
            {
                "rank": index + 1,
                "product_key": item["product"].get("product_key") or item["product"].get("name"),
                "display_name": item["product"].get("display_name") or item["product"].get("name"),
                "effect_score": item["effect_score"],
                "effect_reasons": item["effect_reasons"],
                "penalties": item["penalties"],
                "business_priority": item["business_priority"],
            }
            for index, item in enumerate(ranked[:5])
        ]
        return selected, [_herbicide_component(selected)]

    components: list[dict[str, Any]] = []
    covered: set[str] = set()
    for active in prescription.get("active_ingredients") or []:
        active_key = _target_key(active)
        matches = [
            item
            for item in inventory
            if active_key in _herbicide_product_active_keys(item)
            and _herbicide_product_supports_post_emergence(item)
        ]
        ranked_matches = _rank_herbicide_product_candidates(
            matches,
            {**prescription, "active_ingredients": [active]},
        )
        match = ranked_matches[0]["product"] if ranked_matches else None
        if match:
            components.append(_herbicide_component(match))
            covered.add(active_key)
        else:
            components.append({"active_ingredient": active, "product_key": active, "display_name": active, "generic": True})
    if components and required <= (covered | {_target_key(item.get("active_ingredient")) for item in components}):
        return {"product_key": "generic_herbicide_prescription", "display_name": prescription.get("direction"), "allow_tank_mix": True}, components

    fallback = next((item for item in inventory if _herbicide_product_supports_post_emergence(item)), None) or (inventory[0] if inventory else {})
    if fallback:
        return fallback, [_herbicide_component(fallback)]
    return {"product_key": "generic_herbicide_prescription", "display_name": prescription.get("direction"), "allow_tank_mix": True}, [
        {"active_ingredient": active, "product_key": active, "display_name": active, "generic": True}
        for active in prescription.get("active_ingredients") or []
    ]


def _rank_herbicide_product_candidates(
    candidates: list[dict[str, Any]],
    prescription: dict[str, Any],
) -> list[dict[str, Any]]:
    scored = []
    for index, product in enumerate(candidates):
        score, reasons, penalties, business_priority = _herbicide_product_effect_score(product, prescription)
        scored.append(
            {
                "product": product,
                "effect_score": round(score, 3),
                "effect_reasons": reasons,
                "penalties": penalties,
                "business_priority": business_priority,
                "inventory_index": index,
            }
        )
    return sorted(
        scored,
        key=lambda item: (
            -float(item["effect_score"]),
            _herbicide_price_sort_value(item["product"]),
            item["inventory_index"],
        ),
    )


def _herbicide_product_effect_score(product: dict[str, Any], prescription: dict[str, Any]) -> tuple[float, list[str], list[dict[str, Any]], dict[str, Any]]:
    target_codes = list(prescription.get("matched_target_codes") or [])
    target_set = {_target_key(code) for code in target_codes}
    active_keys = _herbicide_product_active_keys(product)
    required = {_target_key(item) for item in prescription.get("active_ingredients") or []}
    score = 100.0
    reasons: list[str] = []
    penalties: list[dict[str, Any]] = []
    missing = sorted(required - active_keys)
    if required and not missing:
        score += len(required) * 12.0
        reasons.append("覆盖处方活性成分")
    elif missing:
        penalty = 18.0 * len(missing)
        score -= penalty
        penalties.append({"type": "missing_prescription_active", "score": -penalty, "items": missing})
        reasons.append("未完全覆盖机理处方活性成分")

    target_score, target_reasons = _herbicide_product_target_score(product, target_set)
    score += target_score
    reasons.extend(target_reasons)

    active_score, active_reasons = _herbicide_product_active_effect_score(product, active_keys & required)
    score += active_score
    reasons.extend(active_reasons)

    intensity_score, intensity_reasons = _herbicide_product_intensity_score(product, required)
    score += intensity_score
    reasons.extend(intensity_reasons)

    extra_actives = sorted(active_keys - required)
    if extra_actives:
        penalty = _herbicide_extra_active_penalty(extra_actives, target_set, required)
        score -= penalty
        penalties.append({"type": "extra_prescription_active", "score": -penalty, "items": extra_actives})
        reasons.append("处方外有效成分降级:" + ",".join(extra_actives))

    business_score, business_reasons = _herbicide_business_priority(product, target_set)
    score += business_score
    business_priority = {"score": business_score, "reasons": business_reasons}
    reasons.extend(business_reasons)

    if product.get("allow_tank_mix", True):
        score += 1.0
    return score, reasons[:8], penalties, business_priority


def _herbicide_product_target_score(product: dict[str, Any], target_set: set[str]) -> tuple[float, list[str]]:
    product_targets = {_target_key(item) for item in product.get("target_weeds") or []}
    score = 0.0
    reasons: list[str] = []
    direct_matches = sorted(product_targets & target_set)
    if direct_matches:
        score += 8.0 * len(direct_matches)
        reasons.append("目标杂草标签匹配:" + ",".join(direct_matches))
    if product_targets & _target_key_set({"annual_grasses", "grass_weeds"}) and target_set & _target_key_set(MAIZE_POST_GRASS_WEED_CODES | MAIZE_POST_HARD_GRASS_WEED_CODES | {"ELEIN"}):
        score += 8.0
        reasons.append("覆盖禾本科草相")
    if _target_key("volunteer_wheat") in product_targets and "TRZAS" in target_set:
        score += 6.0
        reasons.append("覆盖自生小麦")
    if product_targets & _target_key_set({"broadleaf_weeds", "broadleaf"}) and target_set & _target_key_set(MAIZE_POST_BROADLEAF_WEED_CODES):
        score += 8.0
        reasons.append("覆盖阔叶草相")
    return score, reasons


def _herbicide_product_active_effect_score(product: dict[str, Any], active_keys: set[str]) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    score += _active_bonus(
        active_keys,
        {
            "nicosulfuron": 14,
            "atrazine": 5,
            "mesotrione": 16,
            "tembotrione": 18,
            "topramezone": 18,
            "terbuthylazine": 6,
            "halosulfuron_methyl": 18,
        },
    )
    if active_keys:
        reasons.append("处方内关键成分匹配")
    return score, reasons


def _herbicide_product_intensity_score(product: dict[str, Any], required: set[str]) -> tuple[float, list[str]]:
    wanted: dict[str, float] = {}
    if _target_key("nicosulfuron") in required:
        wanted.update({"nicosulfuron": 0.012, "atrazine": 0.002})
    if _target_key("tembotrione") in required:
        wanted.update({"tembotrione": 0.018, "topramezone": 0.012})
    if _target_key("topramezone") in required:
        wanted.update({"topramezone": 0.012})
    if _target_key("mesotrione") in required:
        wanted.update({"mesotrione": 0.014, "topramezone": 0.010, "atrazine": 0.002})
    intensities = _herbicide_active_intensities(product)
    score = 0.0
    scored_actives = []
    for active, weight in wanted.items():
        intensity = intensities.get(active, 0.0)
        if intensity > 0.0:
            score += min(18.0, intensity * weight)
            scored_actives.append(active)
    if not scored_actives:
        return 0.0, []
    return score, ["剂量强度:" + ",".join(scored_actives)]


def _herbicide_extra_active_penalty(extra_actives: list[str], target_set: set[str], required: set[str]) -> float:
    if not extra_actives:
        return 0.0
    simple_grass = bool(target_set & _target_key_set(MAIZE_POST_GRASS_WEED_CODES)) and not (
        target_set & (_target_key_set(MAIZE_POST_HARD_GRASS_WEED_CODES | MAIZE_POST_BROADLEAF_WEED_CODES) | {_target_key("ELEIN")})
    )
    if simple_grass and required <= _target_key_set({"nicosulfuron", "atrazine"}):
        return 14.0 * len(extra_actives)
    return 8.0 * len(extra_actives)


def _herbicide_business_priority(product: dict[str, Any], target_set: set[str]) -> tuple[float, list[str]]:
    text = _herbicide_business_text(product)
    if not text:
        return 0.0, []
    scoped_to_009 = "009" in text
    if scoped_to_009 and not _target_key_set({"TRZAS", "ELEIN", "DIGSA"}) <= target_set:
        return 0.0, []
    score = 0.0
    reasons: list[str] = []
    if any(token in text for token in ("最终选择", "009选用", "推荐药剂", "推荐执行", "选用套餐")):
        score += 90.0
        reasons.append("业务方案明确推荐")
    if "不选用" in text or "不作为默认采购项" in text:
        score -= 90.0
        reasons.append("业务方案标注不选用")
    return score, reasons


def _herbicide_business_text(product: dict[str, Any]) -> str:
    fields: list[str] = []
    for key in ("recommendation_note", "recommendation_note_009", "notes", "business_note", "selection_note"):
        value = product.get(key)
        if value:
            fields.append(str(value))
    for component in product.get("package_components") or []:
        if not isinstance(component, dict):
            continue
        for key in ("recommendation_note", "recommendation_note_009", "notes", "business_note", "selection_note"):
            value = component.get(key)
            if value:
                fields.append(str(value))
    return " ".join(fields)


def _herbicide_active_intensities(product: dict[str, Any]) -> dict[str, float]:
    intensities: dict[str, float] = {}
    for component in product.get("package_components") or []:
        if not isinstance(component, dict):
            continue
        if "助剂" in str(component.get("component_type") or component.get("component_name") or ""):
            continue
        dose = _extract_first_float(component.get("dose_per_mu"))
        if dose <= 0.0:
            continue
        active_values = [str(active or "").strip().lower() for active in component.get("active_ingredients") or []]
        concentration_text = str(component.get("concentration") or "")
        ingredient_text = " ".join(str(component.get(key) or "") for key in ("ingredient_text", "component_name", "display_name", "product_name"))
        for active in active_values:
            pct = _component_active_percent(active, concentration_text, ingredient_text)
            if pct > 0.0:
                intensities[active] = intensities.get(active, 0.0) + dose * pct
    return intensities


def _component_active_percent(active: str, concentration_text: str, ingredient_text: str) -> float:
    display_names = {
        "nicosulfuron": ("烟嘧磺隆", "烟磺隆", "烟嘧"),
        "atrazine": ("莠去津",),
        "mesotrione": ("硝磺草酮",),
        "topramezone": ("苯唑草酮", "苯唑酮"),
        "tembotrione": ("环磺酮",),
        "halosulfuron-methyl": ("氯吡嘧磺隆",),
    }.get(active, ())
    for name in display_names:
        match = re.search(rf"{re.escape(name)}\s*([0-9]+(?:\.[0-9]+)?)\s*%", concentration_text)
        if match:
            return float(match.group(1))
    if len(re.findall(r"[0-9]+(?:\.[0-9]+)?\s*%", concentration_text)) == 1 and any(name in ingredient_text for name in display_names):
        return _extract_first_float(concentration_text)
    return 0.0


def _extract_first_float(value: Any) -> float:
    match = re.search(r"[0-9]+(?:\.[0-9]+)?", str(value or ""))
    return float(match.group(0)) if match else 0.0


def _active_bonus(active_keys: set[str], weights: dict[str, float]) -> float:
    return sum(weight for active, weight in weights.items() if _target_key(active) in active_keys)


def _target_key_set(values: set[str]) -> set[str]:
    return {_target_key(value) for value in values}


def _herbicide_price_sort_value(product: dict[str, Any]) -> float:
    value = product.get("price_yuan_per_mu")
    try:
        return float(value)
    except (TypeError, ValueError):
        return 1e9


def _herbicide_component(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "product_uuid": item.get("uuid"),
        "product_key": item.get("product_key") or item.get("name"),
        "display_name": item.get("display_name") or item.get("name") or item.get("product_key"),
        "active_ingredient": item.get("active_ingredient") or item.get("active_ingredients"),
    }


def _herbicide_product_active_keys(item: dict[str, Any]) -> set[str]:
    values: list[Any] = []
    for key in ("active_ingredient", "active_ingredients", "components"):
        value = item.get(key)
        if isinstance(value, list):
            values.extend(value)
        elif isinstance(value, dict):
            values.extend(value.values())
        elif value:
            values.append(value)
    return {_target_key(value) for value in values if value}


def _herbicide_product_supports_post_emergence(item: dict[str, Any]) -> bool:
    values: list[str] = []
    for key in ("application_timing", "application_method", "method", "timing", "notes"):
        value = item.get(key)
        if value:
            values.append(str(value).lower())
    for key in ("application_timings", "application_methods", "allowed_application_methods"):
        for value in item.get(key) or []:
            values.append(str(value).lower())
    if not values:
        return True
    text = " ".join(values)
    return any(token in text for token in ("post", "foliar", "stem_leaf", "茎叶", "苗后"))


def _active_fungicide_coverage(
    events_by_day: dict[date, list[dict[str, Any]]],
    day: date,
    target: str,
    profile: CropProfile,
) -> bool:
    for event_day, events in events_by_day.items():
        if event_day > day:
            continue
        for event in events:
            window = _fungicide_protection_days(event)
            if window <= 0:
                continue
            if 0 <= (day - event_day).days <= window and _fungicide_event_covers_target(event, target, profile):
                return True
    return False


def _fungicide_protection_days(event: dict[str, Any]) -> int:
    if event.get("preventive_protection_days") is not None:
        return max(0, int(float(event.get("preventive_protection_days") or 0)))
    window = event.get("efficacy_window_days") or {}
    if isinstance(window, dict) and window.get("protective_days") is not None:
        return max(0, int(float(window.get("protective_days") or 0)))
    return 0


def _fungicide_event_covers_target(event: dict[str, Any], target: str, profile: CropProfile) -> bool:
    target_aliases = _disease_target_aliases(profile, target)
    target_values = event.get("target_diseases") or []
    if isinstance(target_values, str):
        target_values = [target_values]
    coverage_aliases: set[str] = set()
    for value in target_values:
        coverage_aliases.update(_disease_target_aliases(profile, value))
    if coverage_aliases:
        return bool(coverage_aliases & target_aliases)

    stress = event.get("stress")
    if not stress:
        return True
    stress_key = _target_key(stress)
    broad_spectrum = {"*", "ALL", "ANY", "BROAD", "BROAD_SPECTRUM", "TANK_MIX", "MIXED", "广谱", "广谱杀菌剂"}
    return stress_key in broad_spectrum or bool(_disease_target_aliases(profile, stress) & target_aliases)


def _disease_target_aliases(profile: CropProfile, value: Any) -> set[str]:
    key = _target_key(value)
    aliases = {key} if key else set()
    target_map: dict[str, dict[str, Any]] = {}
    if profile.name == "wheat":
        from crops.wheat.config import DISEASE_TARGETS as target_map
    elif profile.name == "cotton":
        from crops.cotton.config import DISEASE_TARGETS as target_map
    elif profile.name == "maize":
        target_map = {
            "SETOTU": {"name": "northern_corn_leaf_blight", "aliases": ["leaf_spot", "exserohilum_turcicum"]},
            "COCHHE": {"name": "southern_corn_leaf_blight", "aliases": ["leaf_spot", "bipolaris_maydis"]},
            "PUCCSO": {"name": "common_rust", "aliases": ["rust", "puccinia_sorghi"]},
            "PUCCPY": {"name": "southern_rust", "aliases": ["rust", "puccinia_polysora"]},
            "DIPDMA": {"name": "diplodia_ear_rot", "aliases": ["ear_rot", "stalk_rot"]},
        }
    for code, config in target_map.items():
        values = {code, config.get("name"), config.get("name_cn")}
        values.update(config.get("aliases") or [])
        normalized = {_target_key(item) for item in values if item}
        if key in normalized:
            aliases.update(normalized)
    return aliases


def _target_key(value: Any) -> str:
    return str(value or "").strip().replace("-", "_").replace(" ", "_").upper()


def _insecticide_residual_days(event: dict[str, Any]) -> int:
    if event.get("residual_control_days") is not None:
        return max(0, int(float(event.get("residual_control_days") or 0)))
    window = event.get("efficacy_window_days") or {}
    if isinstance(window, dict) and window.get("residual_control_days") is not None:
        return max(0, int(float(window.get("residual_control_days") or 0)))
    pesticide = event.get("pesticide") or {}
    residual = pesticide.get("residual") if isinstance(pesticide, dict) else {}
    half_life = float((residual or {}).get("half_life_days") or 0.0)
    if half_life > 0.0:
        return max(1, int(round(half_life * 1.4)))
    return int(event.get("_default_residual_days") or 14)


def _crop_n_topdress(row: dict[str, Any], profile: CropProfile, applied_fertilizers: dict[date, list[dict[str, Any]]], day: date) -> float:
    action_profile = ACTION_PROFILES.get(profile.name)
    if action_profile is None:
        return 0.0
    if not _crop_n_topdress_window(row, profile):
        return 0.0
    recent_n_day = _recent_n_application_day(applied_fertilizers, day)
    if recent_n_day is not None and (day - recent_n_day).days < action_profile.min_n_application_interval_days:
        return 0.0
    remaining_gap = float(row.get("projected_n_gap_kg_ha") or 0.0)
    if remaining_gap <= 0.0:
        target_n_need = float(row.get("management_target_n_need_kg_ha") or 0.0)
        absorbed_n = float(row.get("cumulative_n_uptake_kg_ha") or 0.0)
        available_n = float(row.get("available_n_kg_ha") or 0.0)
        pending_release_n = _pending_fertilizer_release(applied_fertilizers, day).get("N", 0.0)
        remaining_gap = target_n_need - absorbed_n - available_n - pending_release_n
    if remaining_gap < action_profile.min_projected_n_gap_kg_ha:
        return 0.0
    return round(min(action_profile.max_single_n_kg_ha, remaining_gap / 0.50), 3)


def _crop_pk_topdress(
    row: dict[str, Any],
    profile: CropProfile,
    applied_fertilizers: dict[date, list[dict[str, Any]]],
    day: date,
    nutrient: str,
) -> float:
    action_profile = ACTION_PROFILES.get(profile.name)
    if action_profile is None or nutrient not in {"P2O5", "K2O"}:
        return 0.0
    remaining_gap = _crop_pk_remaining_gap(row, profile, applied_fertilizers, day, nutrient)
    if remaining_gap <= 0.0:
        return 0.0
    return round(max(action_profile.default_pk_kg_ha, remaining_gap / 0.75), 3)


def _crop_pk_remaining_gap(
    row: dict[str, Any],
    profile: CropProfile,
    applied_fertilizers: dict[date, list[dict[str, Any]]],
    day: date,
    nutrient: str,
) -> float:
    target_yield = float(row.get("yield_target_kg_ha") or 0.0)
    coeff = float(profile.nutrient_coeff.get(nutrient, 0.0) or 0.0)
    target_need = target_yield * coeff
    field_prefix = "p2o5" if nutrient == "P2O5" else "k2o"
    absorbed = float(row.get(f"cumulative_{field_prefix}_uptake_kg_ha") or 0.0)
    available = float(row.get(f"available_{field_prefix}_kg_ha") or 0.0)
    pending_release = _pending_fertilizer_release(applied_fertilizers, day).get(nutrient, 0.0)
    return round(target_need - absorbed - available - pending_release, 3)


def _crop_n_topdress_window(row: dict[str, Any], profile: CropProfile) -> bool:
    action_profile = ACTION_PROFILES.get(profile.name)
    if action_profile is None:
        return False
    if profile.name == "maize":
        return _maize_row_stage_between(
            row,
            action_profile.n_topdress_stage_min or "V9",
            action_profile.n_topdress_stage_max or "R1",
        )
    if action_profile.n_topdress_bbch_min is None or action_profile.n_topdress_bbch_max is None:
        return False
    bbch = int(row.get("BBCH") or 0)
    return action_profile.n_topdress_bbch_min <= bbch <= action_profile.n_topdress_bbch_max


def _recent_n_application_day(events_by_day: dict[date, list[dict[str, Any]]], day: date) -> date | None:
    days: list[date] = []
    for event_day, events in events_by_day.items():
        if event_day > day:
            continue
        for event in events:
            if _event_nutrients(event).get("N", 0.0) > 0.0:
                days.append(event_day)
                break
    return max(days) if days else None


def _recent_fertilizer_application_day(
    events_by_day: dict[date, list[dict[str, Any]]],
    day: date,
) -> date | None:
    days: list[date] = []
    for event_day, events in events_by_day.items():
        if event_day > day:
            continue
        for event in events:
            nutrients = _event_nutrients(event)
            if any(nutrients.get(nutrient, 0.0) > 0.0 for nutrient in ("N", "P2O5", "K2O")):
                days.append(event_day)
                break
    return max(days) if days else None


def _pending_fertilizer_release(events_by_day: dict[date, list[dict[str, Any]]], day: date) -> dict[str, float]:
    pending = {"N": 0.0, "P2O5": 0.0, "K2O": 0.0}
    for event_day, events in events_by_day.items():
        if event_day > day:
            continue
        days_since = (day - event_day).days
        for event in events:
            if _normalize_fertilizer_method(event.get("method") or event.get("application_method")) == "foliar":
                continue
            release_days = int(event.get("release_days") or (21 if str(event.get("release_type") or "").lower().startswith("coated") else 3))
            release_days = max(1, release_days)
            remaining_release_days = max(0, release_days - days_since - 1)
            if remaining_release_days <= 0:
                continue
            nutrients = _event_nutrients(event)
            for nutrient in pending:
                pending[nutrient] += nutrients.get(nutrient, 0.0) * remaining_release_days / release_days
    return pending


def _preferred_fertilizer_product(payload: dict[str, Any]) -> str:
    product = _select_fertilizer_product(payload, {"N": 1.0, "P2O5": 1.0, "K2O": 1.0})
    return str(product.get("product_key") or product.get("display_name") or "balanced_fertilizer")


def _select_fertilizer_product(payload: dict[str, Any], nutrients: dict[str, float]) -> dict[str, Any]:
    inventory = [item if isinstance(item, dict) else {"product_key": item, "display_name": item} for item in payload.get("fertilizer_inventory") or []]
    n_only = set(key for key, value in nutrients.items() if float(value or 0.0) > 0.0) == {"N"}
    if n_only:
        for item in inventory:
            if float(item.get("n_pct") or 0.0) > 0.0 and float(item.get("p2o5_pct") or 0.0) == 0.0 and float(item.get("k2o_pct") or 0.0) == 0.0:
                return item
    for item in inventory:
        if float(item.get("n_pct") or 0.0) > 0.0 and (float(item.get("p2o5_pct") or 0.0) > 0.0 or float(item.get("k2o_pct") or 0.0) > 0.0):
            return item
    return inventory[0] if inventory else {"product_key": "balanced_fertilizer", "display_name": "balanced_fertilizer", "n_pct": 0.0}


def _fertilizer_amount_and_supplied_nutrients(product: dict[str, Any], requested: dict[str, float]) -> tuple[float, dict[str, float]]:
    fractions = {
        "N": max(float(product.get("n_pct") or 0.0) / 100.0, 0.0),
        "P2O5": max(float(product.get("p2o5_pct") or 0.0) / 100.0, 0.0),
        "K2O": max(float(product.get("k2o_pct") or 0.0) / 100.0, 0.0),
    }
    positive = {key: float(value or 0.0) for key, value in requested.items() if float(value or 0.0) > 0.0}
    rate_candidates = [amount / fractions[nutrient] for nutrient, amount in positive.items() if fractions.get(nutrient, 0.0) > 0.0]
    if rate_candidates:
        amount_kg_ha = round(max(rate_candidates), 3)
        supplied = {
            nutrient: round(amount_kg_ha * fraction, 3)
            for nutrient, fraction in fractions.items()
            if nutrient in positive or fraction > 0.0
        }
        return amount_kg_ha, supplied
    amount_kg_ha = round(sum(positive.values()) * 2.2, 3)
    return amount_kg_ha, {nutrient: round(amount, 3) for nutrient, amount in positive.items()}


def _select_fungicide_product(payload: dict[str, Any], target: str, profile: CropProfile) -> dict[str, Any]:
    inventory = payload.get("fungicide_inventory") or []
    target_aliases = _disease_target_aliases(profile, target)
    for item in inventory:
        coverage_aliases: set[str] = set()
        for value in item.get("target_diseases") or []:
            coverage_aliases.update(_disease_target_aliases(profile, value))
        if coverage_aliases & target_aliases:
            return item
    return inventory[0] if inventory else {"product_key": "fungicide", "display_name": "fungicide", "efficacy_window_days": {}}


def _select_insecticide_product(payload: dict[str, Any], target: str) -> dict[str, Any]:
    inventory = payload.get("insecticide_inventory") or []
    target_lower = str(target or "").lower()
    for item in inventory:
        targets = [str(value).lower() for value in item.get("target_pests") or []]
        if target_lower in targets:
            return item
    return inventory[0] if inventory else {"product_key": "chlorantraniliprole_200SC", "display_name": "chlorantraniliprole_200SC"}


def _display_fertilizer_product(value: str) -> str:
    text = value.strip()
    if text.count(":") == 2 and "复合肥" not in text:
        return f"{text}复合肥"
    return text


def _default_insecticide_event(day: date, payload: dict[str, Any] | None = None, profile: CropProfile | None = None) -> dict[str, Any]:
    payload = payload or {}
    product = _select_insecticide_product(payload, "")
    profile = profile or PROFILES.get(str(payload.get("crop_uuid"))) or PROFILES[MAIZE_UUID]
    pesticide_params = _insecticide_params(profile)
    product_key = _insecticide_product_key(product)
    pesticide = deepcopy(pesticide_params.get(product_key) or next(iter(pesticide_params.values()), None) or {})
    if not pesticide:
        product_key = str(product.get("product_key") or "insecticide")
        pesticide = {
            "common_name": product_key,
            "formulation": None,
            "dose_unit": "ml_ai_mu",
            "label_rate_ml_ai_mu": [4.0, 6.0],
            "dose_response": {"EC50": 1.0, "slope": 1.2},
            "residual": {"half_life_days": 7.0},
        }
    if "label_rate_g_ai_ha" in pesticide and "label_rate_g_ai_mu" not in pesticide:
        pesticide["label_rate_g_ai_mu"] = [round(float(value) / 15.0, 6) for value in pesticide.pop("label_rate_g_ai_ha")]
        dose_response = pesticide.get("dose_response") or {}
        if "ED50" in dose_response:
            dose_response["ED50"] = round(float(dose_response["ED50"]) / 15.0, 6)
        pesticide["dose_unit"] = "g_ai_mu"
    pesticide["dose_unit"] = "ml_ai_mu" if pesticide.get("dose_unit") not in {"g_ai_mu", "ml_ai_mu"} else pesticide["dose_unit"]
    label_rate = pesticide.get("label_rate_g_ai_mu") or pesticide.get("label_rate_ml_ai_mu") or pesticide.get("label_rate_g_ai_ha") or []
    if isinstance(label_rate, list) and label_rate:
        dose_value = round(sum(float(value) for value in label_rate) / len(label_rate), 6)
    elif label_rate:
        dose_value = float(label_rate)
    else:
        dose_value = 4.0
    window = product.get("efficacy_window_days") or {}
    action_profile = ACTION_PROFILES.get(profile.name) or CropActionProfile(0, 99)
    return {
        "Date": day.isoformat(),
        "date": day.isoformat(),
        "product_uuid": product.get("uuid"),
        "product_key": product_key,
        "product_name": product.get("name"),
        "display_name": product.get("display_name"),
        "physical_state": "liquid",
        "dose_value": dose_value,
        "dose_unit": pesticide["dose_unit"],
        "pesticide": pesticide,
        "residual_control_days": int(window.get("residual_control_days") or action_profile.insecticide_residual_days),
        "_default_residual_days": action_profile.insecticide_residual_days,
        "application_method": "foliar_spray",
        "target_pests": product.get("target_pests") or [],
        "notes": "decision-date daily engine insecticide recommendation",
    }


def _root_zone_capacity(layers: list[dict[str, float]], root_depth: float) -> float:
    capacity = 0.0
    for layer in layers:
        overlap = _root_overlap(layer, root_depth)
        capacity += layer["taw_mm"] * overlap / max(layer["depth_mm"], 1.0)
    return max(1.0, capacity)


def _irrigation_actionable(row: dict[str, Any]) -> bool:
    if "irrigation_recommended" in row:
        return bool(row.get("irrigation_recommended"))
    return _high_action(row.get("water_stress_risk"))


def _irrigation_depth_mm(
    *,
    profile: CropProfile,
    row: dict[str, Any],
    root_capacity: float,
    irrigation_method: Any,
) -> float:
    relative_water = float(row.get("root_zone_relative_available_water", 0.0) or 0.0)
    target = float(row.get("water_target_threshold") or profile.water_target)
    raw_depth = max(0.0, (target - relative_water) * root_capacity)

    min_depth, max_depth = _irrigation_depth_limits_mm(profile, row, irrigation_method)
    action_profile = ACTION_PROFILES.get(profile.name)
    if action_profile is not None and action_profile.max_irrigation_mm is not None:
        max_depth = min(max_depth, float(action_profile.max_irrigation_mm))
    if min_depth > max_depth:
        max_depth = min_depth
    return round(max(min_depth, min(max_depth, raw_depth)), 1)


def _irrigation_depth_limits_mm(
    profile: CropProfile,
    row: dict[str, Any],
    irrigation_method: Any,
) -> tuple[float, float]:
    if profile.name == "cotton":
        method = str(irrigation_method or "").strip().lower()
        method_specs = COTTON_IRRIGATION_METHOD_SPECS.get(method)
        if method_specs is not None:
            return float(method_specs["min_event_mm"]), float(method_specs["max_event_mm"])
    if profile.name == "maize":
        stage_limits = MAIZE_IRRIGATION_DEPTH_LIMITS_MM.get(str(row.get("water_stress_stage_band") or ""), (25.0, 60.0))
    else:
        stage_limits = (15.0, 75.0)
    method_limits = IRRIGATION_METHOD_DEPTH_LIMITS_MM.get(_normalized_irrigation_method(irrigation_method), stage_limits)
    min_depth = max(float(stage_limits[0]), float(method_limits[0]))
    max_depth = min(float(stage_limits[1]), float(method_limits[1]))
    return min_depth, max_depth


def _normalized_irrigation_method(value: Any) -> str:
    method = str(value or "").strip().lower().replace("_", "-")
    if "drip" in method:
        return "drip"
    if "micro" in method:
        return "micro-sprinkler"
    if "sprinkler" in method or "喷" in method:
        return "sprinkler"
    if "flood" in method or "furrow" in method or "border" in method or "漫" in method or "沟" in method:
        return "flood"
    return method


def _add_action(actions: list[dict[str, Any]], action_ids: set[str], domain: str, day: date, target: str, event: dict[str, Any], source: dict[str, Any]) -> None:
    identity = f"{domain}:{target}:{day.isoformat()}"
    if identity in action_ids:
        return
    action_ids.add(identity)
    source = dict(source)
    source.setdefault("Date", day.isoformat())
    source.setdefault("action_domain", domain.upper())
    source.setdefault("target_code", target)
    actions.append(
        {
            "id": identity,
            "domain": domain,
            "model_run_date": day.isoformat(),
            "action_date": day.isoformat(),
            "target": target,
            "source_recommendation": source,
            "applied_event": event,
        }
    )


def _close_resolved_episodes(
    open_episodes: dict[str, bool],
    water_risk: str,
    nutrient_status: dict[str, str],
    disease_risk: str,
    insect_risk: str,
    weed_risk: str,
) -> None:
    if not _high_action(water_risk):
        open_episodes["irrigation"] = False
    if not any(_high_action(status) for status in nutrient_status.values()):
        open_episodes["fertilization"] = False
    if not _high_action(disease_risk):
        open_episodes["fungicide"] = False
    if not _high_action(insect_risk):
        open_episodes["insecticide"] = False
    if not _high_action(weed_risk):
        open_episodes["herbicide"] = False
