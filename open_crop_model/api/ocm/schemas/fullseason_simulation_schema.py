from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, ConfigDict, model_validator

from .common import _Model


class _OpenModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, from_attributes=True, extra="allow")


class CropSeason(_Model):
    crop_uuid: Optional[str] = None
    crop_season_uuid: Optional[str] = ""
    crop: str = "maize"
    planting_date: date
    season_start: Optional[date] = None
    season_end: Optional[date] = None
    latitude: float
    longitude: float
    region_code: Optional[str] = None
    yield_target_kg_ha: float = Field(..., gt=0)
    variety: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _default_start(self) -> "CropSeason":
        if self.season_start is None:
            self.season_start = self.planting_date
        if self.season_end is not None and self.season_end < self.season_start:
            raise ValueError("cropseason.season_end must be on or after cropseason.season_start")
        is_maize = (
            str(self.crop).strip().lower() == "maize"
            or self.crop_uuid == "0181d981-0649-4be8-bd90-5d377ff86849"
        )
        if is_maize:
            maturation_group = self.variety.get("maturation_group")
            if not str(maturation_group or "").strip():
                raise ValueError("cropseason.variety.maturation_group is required for maize")
            phenology_profile = self.variety.get("phenology_profile")
            if phenology_profile not in (None, "spring_maize", "summer_maize"):
                raise ValueError(
                    "cropseason.variety.phenology_profile must be spring_maize or summer_maize"
                )
        return self


class ApsimSoilLayer(_Model):
    thickness_mm: float = Field(..., gt=0)
    bd_g_cm3: Optional[float] = Field(default=None, gt=0)
    air_dry_mm: float = Field(default=0.0, ge=0)
    ll15_mm: float = Field(..., ge=0)
    dul_mm: float = Field(..., ge=0)
    sat_mm: float = Field(..., ge=0)
    ks_mm_day: float = Field(..., ge=0)
    kl: float = Field(default=0.04, ge=0)
    xf: float = Field(default=1.0, ge=0)
    initial_water_mm: float = Field(..., ge=0)

    @model_validator(mode="after")
    def _validate_water_limits(self) -> "ApsimSoilLayer":
        if not (self.air_dry_mm <= self.ll15_mm <= self.dul_mm <= self.sat_mm):
            raise ValueError("soil layer must satisfy air_dry_mm <= ll15_mm <= dul_mm <= sat_mm")
        if self.initial_water_mm > self.sat_mm:
            raise ValueError("soil layer initial_water_mm must be <= sat_mm")
        return self


class SoilInput(_Model):
    texture: str
    layers: List[ApsimSoilLayer]
    analysis: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_layers(self) -> "SoilInput":
        if not self.layers:
            raise ValueError("soil.layers is required")
        return self


class WeatherInput(_Model):
    daily: List[_OpenModel] = Field(default_factory=list)
    hourly: List[_OpenModel] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_daily(self) -> "WeatherInput":
        if not self.daily:
            raise ValueError("weather.daily is required")
        return self


class GrowthRegulationSettings(_Model):
    enabled: bool = False


class CottonManagementSettings(_Model):
    enabled: bool = False
    canopy_regulation: bool = True
    topping: bool = True
    harvest_aid: bool = True
    machine_harvest: bool = True


class ManagementInput(_Model):
    mode: Optional[str] = None
    irrigation_method: str = "flood"
    fertigation_enabled: bool = False
    mulch_enabled: bool = False
    inventory: Dict[str, Any] = Field(default_factory=dict)
    applied: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict)
    economics: Dict[str, Any] = Field(default_factory=dict)
    stresses: Dict[str, Any] = Field(default_factory=dict)
    growth_regulation: GrowthRegulationSettings = Field(default_factory=GrowthRegulationSettings)
    cotton_management: CottonManagementSettings = Field(default_factory=CottonManagementSettings)


class OutputRequest(_Model):
    daily: List[str] = Field(default_factory=list)
    hourly: List[Dict[str, Any]] = Field(default_factory=list)
    summary: List[str] = Field(default_factory=list)


class GrowthStageObservationRecord(_Model):
    date: date
    observed_value: Any


class GrowthStageObservations(_Model):
    standard: Optional[str] = None
    records: List[GrowthStageObservationRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_standard(self) -> "GrowthStageObservations":
        if self.records and not self.standard:
            raise ValueError("observations.growth_stage.standard is required when records are present")
        return self


class ObservationsInput(_Model):
    growth_stage: Optional[GrowthStageObservations] = None


class FullSeasonSimulationRequest(_Model):
    cropseason: CropSeason
    soil: SoilInput
    weather: WeatherInput
    management: ManagementInput
    observations: ObservationsInput = Field(default_factory=ObservationsInput)
    output: OutputRequest = Field(default_factory=OutputRequest)

    @model_validator(mode="after")
    def _default_season_end(self) -> "FullSeasonSimulationRequest":
        if self.cropseason.season_end is None:
            weather_dates: list[date] = []
            for record in self.weather.daily:
                item = record.model_dump()
                raw_date = item.get("DateTime") or item.get("Date")
                if isinstance(raw_date, datetime):
                    weather_dates.append(raw_date.date())
                elif isinstance(raw_date, date):
                    weather_dates.append(raw_date)
                elif raw_date is not None:
                    weather_dates.append(date.fromisoformat(str(raw_date)[:10]))
            if not weather_dates:
                raise ValueError(
                    "cropseason.season_end or a dated weather.daily record is required"
                )
            self.cropseason.season_end = max(weather_dates)
        if self.cropseason.season_end < self.cropseason.season_start:
            raise ValueError("cropseason.season_end must be on or after cropseason.season_start")
        return self


class FullSeasonSimulationResponse(_Model):
    crop_uuid: str
    crop_season_uuid: Optional[str] = ""
    planting_date: date
    season_start: date
    season_end: date
    daily_state: List[Dict[str, Any]]
    summary: Dict[str, Any]
    daily_risk: Dict[str, Any] = Field(default_factory=dict)
    stress_risk: Dict[str, Any] = Field(default_factory=dict)
    field_risk: List[Dict[str, Any]] = Field(default_factory=list)
    hourly: Dict[str, Any] = Field(default_factory=dict)


for _M in (
    CropSeason,
    ApsimSoilLayer,
    SoilInput,
    WeatherInput,
    GrowthRegulationSettings,
    ManagementInput,
    OutputRequest,
    GrowthStageObservationRecord,
    GrowthStageObservations,
    ObservationsInput,
    FullSeasonSimulationRequest,
    FullSeasonSimulationResponse,
):
    _M.model_rebuild()
