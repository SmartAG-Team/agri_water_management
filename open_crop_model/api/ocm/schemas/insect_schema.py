# api/ocm/schemas/insect_schema.py
from __future__ import annotations
from typing import Any, Dict, List, Optional, Literal
from datetime import date
from pydantic import Field, model_validator
from .common import _Model, HourlyWeatherEntry, GrowthStageEntry

class PesticideSpec(_Model):
    common_name: str
    formulation: str
    irac_moa: Optional[str] = None
    dose_unit: Literal["g_ai_mu", "ml_ai_mu"]
    label_rate_g_ai_mu: Optional[List[float]] = None
    label_rate_ml_ai_mu: Optional[List[float]] = None
    ai_content_g_per_L: Optional[float] = None
    ai_density_g_per_ml: Optional[float] = None
    dose_response: Dict[str, float]
    residual: Dict[str, float]
    rainfast: Optional[Dict[str, float]] = None
    temp_modifier: Optional[Dict[str, float]] = None
    notes: Optional[str] = None

class AppliedInsecticide(_Model):
    Date: date
    product_key: str
    physical_state: Optional[Literal["liquid", "solid"]] = None
    dose_value: float
    dose_unit: Literal["g_ai_mu", "ml_ai_mu"]
    ai_density_g_per_ml: Optional[float] = None
    pesticide: PesticideSpec
    notes: Optional[str] = None

    @model_validator(mode="after")
    def _check_unit_consistency(self) -> "AppliedInsecticide":
        if self.physical_state == "solid" and self.dose_unit != "g_ai_mu":
            raise ValueError("固体剂型必须使用 g_ai_mu")
        if self.pesticide and self.pesticide.dose_unit not in ("g_ai_mu", "ml_ai_mu"):
            raise ValueError("pesticide.dose_unit 必须是 g_ai_mu 或 ml_ai_mu")
        return self

class InsectRequest(_Model):
    crop_uuid: str
    crop_season_uuid: Optional[str] = ""
    planting_date: date
    decision_date: date
    latitude: float
    longitude: float
    variety_susceptibility: Dict[str, int]
    insect_eppo_codes: List[str]
    weather_data: List[HourlyWeatherEntry]
    applied_insecticides: Optional[List[AppliedInsecticide]] = Field(default_factory=list)
    growth_stage: Optional[List[GrowthStageEntry]] = Field(default_factory=list)


class InsectResponse(_Model):
    daily_insect_risk: Dict[str, List[Dict[str, Any]]]
    stress_risk: Dict[str, List[Dict[str, Any]]]
    field_risk: List[Dict[str, Any]]
    action_recommendations: List[Dict[str, Any]]
    action_recommendations_full_season: List[Dict[str, Any]] = Field(default_factory=list)
    integrated_daily_state: List[Dict[str, Any]] = Field(default_factory=list)


for _M in (PesticideSpec, AppliedInsecticide, InsectRequest, InsectResponse):
    _M.model_rebuild()
