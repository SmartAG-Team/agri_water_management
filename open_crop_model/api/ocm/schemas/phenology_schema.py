# api/ocm/schemas/phenology_schema.py
from __future__ import annotations
from typing import Dict, List, Optional
from datetime import date
from pydantic import Field
from .common import _Model, DailyWeatherEntry, GrowthStageEntry, VarietyCharacteristics

class PhenologyRequest(_Model):
    crop_uuid: str
    crop_season_uuid: Optional[str] = ""
    planting_date: date
    season_end: Optional[date] = Field(
        default=None,
        description=(
            "Planned or actual harvest date; defaults to the last daily-weather date "
            "when omitted."
        ),
    )
    latitude: float
    longitude: float
    variety_characteristics: VarietyCharacteristics
    weather_data: List[DailyWeatherEntry]
    include_diagnostics: bool = Field(
        default=False,
        description="是否返回作物特异的额外诊断字段；小麦可显式开启。",
    )
    observations: Optional[List[Dict[str, str]]] = Field(
        default=None, description="可选观测：[{DateTime, ObservedStage}]"
    )

class PhenologyResponse(_Model):
    phenology: List[Dict]  # {Date, Stage, dd_cum, ...}

for _M in (PhenologyRequest, PhenologyResponse):
    _M.model_rebuild()
