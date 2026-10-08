# api/ocm/schemas/common.py
from __future__ import annotations
from datetime import datetime, date
from typing import Dict, Literal, Optional
from enum import Enum
from pydantic import BaseModel, Field

class _Model(BaseModel):
    model_config = dict(
        populate_by_name=True,
        from_attributes=True,
        extra="forbid",
    )

# 通用枚举（可被各领域复用）
class CombinedStrategy(str, Enum):
    minimum = "min"
    mean = "mean"
    maximum = "max"

# 通用天气（小时/日）
class DailyWeatherEntry(_Model):
    DateTime: date | datetime = Field(..., description="日粒度（可 YYYY-MM-DD）")
    temperature_2m_mean: float
    temperature_2m_min: float
    temperature_2m_max: float
    precipitation_sum: float
    shortwave_radiation_sum: float = Field(..., description="Daily shortwave radiation sum, MJ/m2/day")
    windspeed_10m_min: Optional[float] = None
    windspeed_10m_max: Optional[float] = None
    windspeed_10m_mean: float
    relative_humidity_2m_mean: float

class HourlyWeatherEntry(_Model):
    DateTime: datetime
    temperature_2m: float = Field(..., alias="temperature_2m")
    relative_humidity_2m: float = Field(..., alias="relativehumidity_2m")
    wind_speed_10m: float = Field(..., alias="windspeed_10m")
    precipitation: float
    shortwave_radiation: float = Field(..., description="Hourly shortwave radiation energy, MJ/m2/hour")
    dew_point_2m: Optional[float] = Field(None, alias="dewpoint_2m")
    vapor_pressure_deficit: Optional[float] = None
    cloud_cover: Optional[float] = Field(None, alias="cloudcover")

# 通用生育期
class GrowthStageEntry(_Model):
    Date: date
    Stage: str
    BBCH: Optional[int] = None
    StageName: Optional[str] = None

# 可选：通用品种信息
class CultivarPhenologyParameters(_Model):
    gdd_to_r6: Optional[float] = Field(default=None, gt=0)
    gdd_to_emergence: Optional[float] = Field(default=None, gt=0)
    gdd_ve_to_r1: Optional[float] = Field(default=None, gt=0)
    gdd_r1_to_r6: Optional[float] = Field(default=None, gt=0)
    stage_gdd: Optional[Dict[str, float]] = None


class VarietyCharacteristics(_Model):
    name: str
    maturation_group: str
    phenology: Optional[CultivarPhenologyParameters] = None
    phenology_profile: Optional[Literal["spring_maize", "summer_maize"]] = None
