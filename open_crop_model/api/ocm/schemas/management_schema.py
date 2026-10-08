from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Literal, Optional

from pydantic import ConfigDict, Field, model_validator

from .common import _Model, GrowthStageEntry, VarietyCharacteristics
from .irrigation_schema import EconomicParameters, IrrigationDailyWeatherEntry, SoilLayer
from .nutrition_schema import FertilizerApplication, SoilTest


class ManagementRequest(_Model):
    crop_uuid: str
    crop_season_uuid: Optional[str] = ""
    planting_date: date
    decision_date: date
    latitude: float
    longitude: float
    mulch_enabled: Optional[bool] = True
    fertigation_enabled: Optional[bool] = False
    region_code: Optional[str] = None
    variety_characteristics: VarietyCharacteristics
    target_yield_kg_ha: Optional[float] = Field(default=None, gt=0)
    management_mode: Literal["water_only", "water_nutrition"] = "water_only"
    growth_stage: List[GrowthStageEntry]
    weather_data: List[IrrigationDailyWeatherEntry]
    forecast_weather_data: Optional[List[IrrigationDailyWeatherEntry]] = None
    soil_type: Literal["sand", "sandy_loam", "clay"]
    soil_texture: str
    soil_profile: List[SoilLayer]
    irrigation_method: Literal["flood", "sprinkler", "micro-sprinkler", "drip", "drip_under_mulch"]
    economic_parameters: EconomicParameters
    applied_water_fertilizer: Optional[List[Dict[str, Any]]] = None
    irrigation_history: Optional[List[Dict[str, Any]]] = None
    applied_irrigations: Optional[List[Dict[str, Any]]] = None

    soil_test: Optional[SoilTest] = None
    fertilizer_history: Optional[List[FertilizerApplication]] = None
    applied_fertilizers: Optional[List[FertilizerApplication]] = None
    economics: Optional[Dict[str, Any]] = None
    protein_target_pct: Optional[float] = None
    custom_products: Optional[List[Dict[str, Any]]] = None
    fertilizer_inventory: Optional[List[Any]] = None

    current_status: Optional[Dict[str, Any]] = None
    lai: Optional[float] = None
    aboveground_biomass_kg_ha: Optional[float] = None
    biomass_partition: Optional[Dict[str, float]] = None
    layered_soil_water: Optional[List[Dict[str, Any]]] = None
    root_zone_relative_available_water: Optional[float] = None
    max_irrigation_recommendations: Optional[int] = None
    max_prescription_irrigation_events: Optional[int] = None
    prescription_enabled: Optional[bool] = False
    prescription_objective: Literal["yield_guarded_saving"] = "yield_guarded_saving"
    baseline_type: Literal["local_farmer_practice"] = "local_farmer_practice"

    @model_validator(mode="after")
    def _validate_layers(self) -> "ManagementRequest":
        if self.target_yield_kg_ha is None:
            raise ValueError("target_yield_kg_ha is required")
        if self.applied_water_fertilizer is not None:
            irrigation_events: List[Dict[str, Any]] = []
            fertilizer_events: List[Dict[str, Any]] = []
            for item in self.applied_water_fertilizer:
                day = item.get("date") or item.get("Date")
                if not day:
                    raise ValueError("applied_water_fertilizer event missing required date field")
                water_mm = item.get("water_mm", item.get("amount_mm", item.get("gross_depth_mm", 0.0))) or 0.0
                nutrients = item.get("nutrients_kg_ha") or {}
                if float(water_mm) > 0.0:
                    irrigation_events.append(
                        {
                            "date": day,
                            "amount_mm": float(water_mm),
                            "method": item.get("method") or self.irrigation_method,
                            "notes": item.get("notes"),
                        }
                    )
                if any(float(nutrients.get(key, nutrients.get(key.lower(), 0.0)) or 0.0) > 0.0 for key in ("N", "P2O5", "K2O")):
                    fertilizer_events.append(
                        FertilizerApplication.model_validate(
                            {
                                "date": day,
                                "product_name": item.get("product_name") or "water_soluble_npk",
                                "amount_kg_ha": float(item.get("amount_kg_ha", 0.0) or 0.0),
                                "nutrients_kg_ha": {
                                    "N": float(nutrients.get("N", nutrients.get("n", 0.0)) or 0.0),
                                    "P2O5": float(nutrients.get("P2O5", nutrients.get("p2o5", 0.0)) or 0.0),
                                    "K2O": float(nutrients.get("K2O", nutrients.get("k2o", 0.0)) or 0.0),
                                },
                                "method": item.get("method") or "fertigation",
                                "notes": item.get("notes"),
                            }
                        )
                    )
            if not self.irrigation_history and not self.applied_irrigations:
                self.irrigation_history = irrigation_events
            if not self.fertilizer_history and not self.applied_fertilizers:
                self.fertilizer_history = fertilizer_events
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


class ManagementResponse(_Model):
    model_config = ConfigDict(extra='ignore')
    daily_fertiwater_risk: Dict[str, List[Dict[str, Any]]]
    stress_risk: Dict[str, List[Dict[str, Any]]]
    field_risk: List[Dict[str, Any]]
    action_recommendations: List[Dict[str, Any]]
    action_recommendations_full_season: List[Dict[str, Any]] = Field(default_factory=list)
    integrated_daily_state: List[Dict[str, Any]] = Field(default_factory=list)
    prescription: Optional[Dict[str, Any]] = None
    scenario_comparison: Optional[List[Dict[str, Any]]] = None
    farmer_baseline_summary: Optional[Dict[str, Any]] = None


for _M in (ManagementRequest, ManagementResponse):
    _M.model_rebuild()
