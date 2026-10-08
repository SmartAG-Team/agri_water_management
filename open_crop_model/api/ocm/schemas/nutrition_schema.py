from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from pydantic import Field, model_validator

from .common import _Model
from .phenology_schema import GrowthStageEntry


class NutritionDailyWeatherEntry(_Model):
    DateTime: datetime
    temperature_2m_mean: float
    temperature_2m_min: float
    temperature_2m_max: float
    precipitation_sum: float
    shortwave_radiation_sum: float = Field(..., description="Daily shortwave radiation sum, MJ/m2/day")
    windspeed_10m_min: Optional[float] = None
    windspeed_10m_max: Optional[float] = None
    windspeed_10m_mean: float
    relative_humidity_2m_mean: float


class SoilTest(_Model):
    organic_matter_g_kg: Optional[float] = None
    mineral_n_kg_ha: Optional[float] = None
    alkali_hydrolyzable_n_mg_kg: Optional[float] = None
    olsen_p_mg_kg: Optional[float] = None
    available_k_mg_kg: Optional[float] = None
    exchangeable_k_mg_kg: Optional[float] = None
    available_s_mg_kg: Optional[float] = None
    zn_mg_kg: Optional[float] = None
    b_mg_kg: Optional[float] = None
    ph: Optional[float] = None
    soil_ec_ds_m: Optional[float] = None
    ec_ds_m: Optional[float] = None
    electrical_conductivity_ds_m: Optional[float] = None


class FertilizerApplication(_Model):
    date: Optional[str] = None
    Date: Optional[str] = None
    product_uuid: Optional[str] = None
    product_name: Optional[str] = None
    product_key: Optional[str] = None
    amount_kg_ha: float = Field(default=0.0, ge=0.0)
    n_pct: Optional[float] = None
    p2o5_pct: Optional[float] = None
    k2o_pct: Optional[float] = None
    application_method: Optional[str] = None
    method: Optional[str] = "soil"
    notes: Optional[str] = None
    nutrients_kg_ha: Optional[Dict[str, float]] = None
    micros_g_ha: Optional[Dict[str, float]] = None
    target_nutrients: Optional[List[str]] = None
    release_type: Optional[str] = None
    release_days: Optional[int] = None


class NutritionRequest(_Model):
    crop_uuid: str
    crop_season_uuid: Optional[str] = ""
    planting_date: Optional[date] = None
    decision_date: date
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    mulch_enabled: Optional[bool] = True
    fertigation_enabled: Optional[bool] = False
    region_code: Optional[str] = None
    target_yield_kg_ha: Optional[float] = None
    variety_characteristics: Optional[Dict[str, Any]] = None
    management_mode: Optional[str] = None
    irrigation_method: Optional[str] = None

    growth_stage: Optional[List[GrowthStageEntry]] = None
    current_bbch: Optional[int] = None

    lai: Optional[float] = None
    aboveground_biomass_kg_ha: Optional[float] = None
    biomass_partition: Optional[Dict[str, float]] = None
    root_zone_relative_available_water: Optional[float] = None

    weather_daily: Optional[List[NutritionDailyWeatherEntry]] = None
    weather_data: Optional[List[NutritionDailyWeatherEntry]] = None
    weather_history: Optional[List[NutritionDailyWeatherEntry]] = None
    forecast_weather_data: Optional[List[NutritionDailyWeatherEntry]] = None
    weather_forecast: Optional[List[NutritionDailyWeatherEntry]] = None

    soil: Optional[Dict[str, Any]] = None
    soil_type: Optional[str] = None
    soil_texture: Optional[str] = None
    soil_profile: Optional[List[Dict[str, Any]]] = None
    layered_soil_water: Optional[List[Dict[str, Any]]] = None
    current_status: Optional[Dict[str, Any]] = None

    irrigation: Optional[Dict[str, Any]] = None
    applied_irrigations: Optional[List[Dict[str, Any]]] = None
    irrigation_history: Optional[List[Dict[str, Any]]] = None
    irrigation_recommendation: Optional[List[Dict[str, Any]]] = None
    action_recommendations: Optional[List[Dict[str, Any]]] = None

    soil_test: Optional[SoilTest] = None
    fertilizer_history: Optional[List[FertilizerApplication]] = None
    applied_fertilizers: Optional[List[FertilizerApplication]] = None
    economics: Optional[Dict[str, Any]] = None
    economic_parameters: Optional[Dict[str, Any]] = None
    custom_products: Optional[List[Dict[str, Any]]] = None
    fertilizer_inventory: Optional[List[Any]] = None
    protein_target_pct: Optional[float] = None

    @model_validator(mode="after")
    def _normalize_aliases(self) -> "NutritionRequest":
        if self.weather_history is None and self.weather_data:
            self.weather_history = self.weather_data
        if self.weather_history is None and self.weather_daily:
            self.weather_history = self.weather_daily
        if self.weather_forecast is None and self.forecast_weather_data:
            self.weather_forecast = self.forecast_weather_data
        if self.applied_irrigations is not None and self.irrigation_history is None:
            self.irrigation_history = self.applied_irrigations
        missing = []
        if self.target_yield_kg_ha is None:
            missing.append("target_yield_kg_ha")
        if not self.growth_stage:
            missing.append("growth_stage")
        if self.soil_test is None:
            missing.append("soil_test")
        else:
            if self.soil_test.organic_matter_g_kg is None:
                missing.append("soil_test.organic_matter_g_kg")
            if self.soil_test.ph is None:
                missing.append("soil_test.ph")
            if self.soil_test.mineral_n_kg_ha is None and self.soil_test.alkali_hydrolyzable_n_mg_kg is None:
                missing.append("soil_test.mineral_n_kg_ha or soil_test.alkali_hydrolyzable_n_mg_kg")
        if not (self.weather_history or self.weather_data or self.weather_daily):
            missing.append("weather_history/weather_data/weather_daily")
        if missing:
            raise ValueError(f"nutrition request missing required field(s): {', '.join(missing)}")
        return self


class NutritionResponse(_Model):
    daily_nutrition_risk: Dict[str, List[Dict[str, Any]]]
    stress_risk: Dict[str, List[Dict[str, Any]]]
    field_risk: List[Dict[str, Any]]
    action_recommendations: List[Dict[str, Any]]
    action_recommendations_full_season: List[Dict[str, Any]] = Field(default_factory=list)
    integrated_daily_state: List[Dict[str, Any]] = Field(default_factory=list)


for _M in (
    NutritionDailyWeatherEntry,
    SoilTest,
    FertilizerApplication,
    NutritionRequest,
    NutritionResponse,
):
    _M.model_rebuild()
