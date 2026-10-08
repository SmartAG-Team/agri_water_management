# api/ocm/schemas/spray_weather_schema.py
from __future__ import annotations
from typing import Optional, List
from enum import Enum
from pydantic import Field
from .common import _Model, HourlyWeatherEntry, CombinedStrategy

class PesticideType(str, Enum):
    fungicide = "fungicide"
    herbicide = "herbicide"
    insecticide = "insecticide"

class SprayWeatherRequest(_Model):
    crop_uuid: str
    pesticide_type: PesticideType
    mode: str                                    # "hourly"/"daily" 等
    combined_strategy: Optional[CombinedStrategy] = None
    crop_season_uuid: Optional[str] = ""
    weather_data: List[HourlyWeatherEntry]

class SprayWeatherResponse(_Model):
    spray_weather: List[dict]  # {DateTime, suitability, reason, ...}

for _M in (SprayWeatherRequest, SprayWeatherResponse):
    _M.model_rebuild()