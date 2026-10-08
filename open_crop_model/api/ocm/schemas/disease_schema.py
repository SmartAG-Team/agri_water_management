# api/ocm/schemas/disease_schema.py
from __future__ import annotations
from typing import Any, List, Dict, Optional
from datetime import datetime, date
from pydantic import Field
from .common import _Model, GrowthStageEntry

class DiseaseWeatherEntry(_Model):
    DateTime: datetime
    temperature_2m: float
    relative_humidity_2m: float = Field(..., alias="relativehumidity_2m")
    vapor_pressure_deficit: float
    precipitation: float
    dew_point_2m: float = Field(..., alias="dewpoint_2m")
    wind_speed_10m: float = Field(..., alias="windspeed_10m")
    cloud_cover: float = Field(..., alias="cloudcover")
    shortwave_radiation: float = Field(..., description="Hourly shortwave radiation energy, MJ/m2/hour")

class FungicideApplication(_Model):
    applied_date: date
    curative_efficacy: float
    curative_protection_days: int
    preventive_efficacy: float
    preventive_protection_days: int
    stress: str

class DiseaseRequest(_Model):
    applied_fungicides: List[FungicideApplication]
    crop_uuid: str
    planting_date: date
    decision_date: date
    crop_season_uuid: Optional[str] = ""
    growth_stage: List[GrowthStageEntry]
    latitude: float
    longitude: float
    stress_eppo_codes: List[str]
    variety_susceptibility: Dict[str, int]
    weather_data: List[DiseaseWeatherEntry]

class DiseaseResponse(_Model):
    daily_disease_risk: Dict[str, List[Dict[str, Any]]]
    stress_risk: Dict[str, List[Dict[str, Any]]]
    field_risk: List[Dict[str, Any]]
    action_recommendations: List[Dict[str, Any]]
    action_recommendations_full_season: List[Dict[str, Any]] = Field(default_factory=list)
    integrated_daily_state: List[Dict[str, Any]] = Field(default_factory=list)

for _M in (
    DiseaseWeatherEntry, FungicideApplication, DiseaseRequest, DiseaseResponse
):
    _M.model_rebuild()
