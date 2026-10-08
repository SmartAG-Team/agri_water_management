from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional, Literal, Any

from pydantic import Field, model_validator

from .common import _Model, GrowthStageEntry, VarietyCharacteristics


class IrrigationDailyWeatherEntry(_Model):
    DateTime: date | str
    temperature_2m_mean: float
    temperature_2m_min: float
    temperature_2m_max: float
    precipitation_sum: float
    shortwave_radiation_sum: float = Field(..., description="Daily shortwave radiation sum, MJ/m2/day")
    windspeed_10m_min: Optional[float] = None
    windspeed_10m_max: Optional[float] = None
    windspeed_10m_mean: float
    relative_humidity_2m_mean: float


class SoilLayer(_Model):
    depth_top_cm: float
    depth_bottom_cm: float
    field_capacity: float
    wilting_point: float
    air_dry: float
    saturation: float
    bulk_density: float
    ksat_mm_day: float
    gravel_fraction: Optional[float] = None
    initial_soil_water_mm: Optional[float] = None
    initial_relative_water: Optional[float] = None
    label: Optional[str] = None

    @model_validator(mode="after")
    def _validate_initial_water(self) -> "SoilLayer":
        if self.initial_soil_water_mm is None and self.initial_relative_water is None:
            raise ValueError("soil layer requires initial_soil_water_mm or initial_relative_water")
        return self


class EconomicParameters(_Model):
    grain_price_cny_per_kg: Optional[float] = None
    seed_cotton_price_cny_per_kg: Optional[float] = None
    water_cost_cny_per_mm_ha: Optional[float] = None
    electricity_cost_cny_per_kwh: Optional[float] = None
    pump_kwh_per_mm_ha: Optional[float] = None
    irrigation_event_labor_cost_cny_ha: Optional[float] = None
    fertilizer_event_labor_cost_cny_ha: Optional[float] = None


class IrrigationRequest(_Model):
    crop_uuid: str
    crop_season_uuid: Optional[str] = ""
    planting_date: date
    decision_date: date
    latitude: float
    longitude: float
    mulch_enabled: Optional[bool] = True
    region_code: Optional[str] = None
    variety_characteristics: VarietyCharacteristics
    target_yield_kg_ha: Optional[float] = Field(default=None, gt=0)
    growth_stage: Optional[List[GrowthStageEntry]] = None
    weather_data: List[IrrigationDailyWeatherEntry]
    forecast_weather_data: Optional[List[IrrigationDailyWeatherEntry]] = None
    soil_type: Literal["sand", "sandy_loam", "clay"]
    soil_texture: str
    soil_profile: List[SoilLayer]
    irrigation_method: Literal["flood", "sprinkler", "micro-sprinkler", "drip", "drip_under_mulch"]
    economic_parameters: EconomicParameters
    irrigation_history: Optional[List[Dict[str, Any]]] = None
    applied_irrigations: Optional[List[Dict[str, Any]]] = None
    management_mode: Optional[str] = None
    soil_test: Optional[Dict[str, Any]] = None
    fertilizer_history: Optional[List[Dict[str, Any]]] = None
    applied_fertilizers: Optional[List[Dict[str, Any]]] = None
    economics: Optional[Dict[str, Any]] = None
    protein_target_pct: Optional[float] = None
    custom_products: Optional[List[Dict[str, Any]]] = None
    current_root_zone_soil_water_mm: Optional[float] = None
    current_lai: Optional[float] = None
    current_biomass_kg_ha: Optional[float] = None
    current_grain_weight_kg_ha: Optional[float] = None
    current_rooting_depth_mm: Optional[float] = None
    max_irrigation_recommendations: Optional[int] = None

    @model_validator(mode="after")
    def _validate_layers(self) -> "IrrigationRequest":
        if self.target_yield_kg_ha is None:
            raise ValueError("target_yield_kg_ha is required")
        if self.applied_irrigations is not None and self.irrigation_history is None:
            self.irrigation_history = self.applied_irrigations
        if self.applied_fertilizers is not None and self.fertilizer_history is None:
            self.fertilizer_history = self.applied_fertilizers
        if self.forecast_weather_data:
            by_day = {str(item.DateTime)[:10]: item for item in self.weather_data}
            for item in self.forecast_weather_data:
                by_day[str(item.DateTime)[:10]] = item
            self.weather_data = [by_day[key] for key in sorted(by_day)]
        if len(self.soil_profile) < 3 or len(self.soil_profile) > 5:
            raise ValueError("soil_profile must have 3 to 5 layers")
        economics = self.economic_parameters
        common_required = [
            "water_cost_cny_per_mm_ha",
            "electricity_cost_cny_per_kwh",
            "pump_kwh_per_mm_ha",
            "irrigation_event_labor_cost_cny_ha",
        ]
        missing = [key for key in common_required if getattr(economics, key) is None]
        if self.crop_uuid == "69231650-8600-4000-8000-000000000002":
            if economics.seed_cotton_price_cny_per_kg is None:
                missing.append("seed_cotton_price_cny_per_kg")
        elif economics.grain_price_cny_per_kg is None:
            missing.append("grain_price_cny_per_kg")
        if missing:
            raise ValueError(f"economic_parameters missing required field(s): {', '.join(missing)}")
        return self


class IrrigationResponse(_Model):
    daily_drought_risk: Dict[str, List[Dict[str, Any]]]
    stress_risk: Dict[str, List[Dict[str, Any]]]
    field_risk: List[Dict[str, Any]]
    action_recommendations: List[Dict[str, Any]]
    action_recommendations_full_season: List[Dict[str, Any]] = Field(default_factory=list)
    integrated_daily_state: List[Dict[str, Any]] = Field(default_factory=list)


for _M in (IrrigationDailyWeatherEntry, SoilLayer, EconomicParameters, IrrigationRequest, IrrigationResponse):
    _M.model_rebuild()
