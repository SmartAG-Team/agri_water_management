from __future__ import annotations

import datetime
from typing import Any, Optional

import pandas as pd

from core.crop import Crop
from crops.cotton.config import DEFAULT_REGION_CODE, DISEASE_TARGETS, INSECT_TARGETS, spray_weather_config
from crops.cotton.disease import CottonDiseaseModel, CottonDiseaseTarget
from crops.cotton.insect import CottonInsectModel, CottonInsectTarget
from crops.cotton.irrigation import CottonIrrigationModel
from crops.cotton.management import CottonManagementModel
from crops.cotton.nutrition import CottonNutritionModel
from crops.cotton.phenology import CottonPhenology
from crops.maize.spray_weather import MaizeSprayWeather


class Cotton(Crop):
    """
    Cotton crop facade for Xinjiang plastic-mulch drip systems.

    The class follows the maize/wheat facade pattern: API services instantiate
    the crop by crop_uuid and call domain-specific simulate_* methods.
    """

    def __init__(
        self,
        planting_date: Optional[str] = None,
        params: Optional[dict[str, Any]] = None,
        variety_maturation_group: str = "middle",
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        **_,
    ) -> None:
        params = dict(params or {})
        phenology_params = params.get("phenology", {}).copy()
        if "variety_maturation_group" in params:
            phenology_params.setdefault("variety_maturation_group", params.pop("variety_maturation_group"))
        phenology_params.setdefault("variety_maturation_group", variety_maturation_group)
        phenology_params.setdefault("region_code", params.get("region_code") or DEFAULT_REGION_CODE)
        phenology_params.setdefault("mulch_enabled", params.get("mulch_enabled", True))
        param_latitude = latitude if latitude is not None else params.get("latitude")
        param_longitude = longitude if longitude is not None else params.get("longitude")
        if param_latitude is not None:
            phenology_params.setdefault("latitude", param_latitude)
        if param_longitude is not None:
            phenology_params.setdefault("longitude", param_longitude)
        params["phenology"] = phenology_params

        self.latitude = param_latitude
        self.longitude = param_longitude
        self.region_code = params.get("region_code") or phenology_params.get("region_code") or DEFAULT_REGION_CODE
        self.mulch_enabled = bool(params.get("mulch_enabled", True))
        self.fertigation_enabled = bool(params.get("fertigation_enabled", False))
        self.planting_date: Optional[datetime.date] = pd.to_datetime(planting_date).date() if planting_date else None

        super().__init__(params=params)
        self.phenology_model = CottonPhenology(params=phenology_params)
        self.nutrition_model = CottonNutritionModel()
        self.diseases = [CottonDiseaseTarget(code) for code in DISEASE_TARGETS]
        self.insects = [CottonInsectTarget(code) for code in INSECT_TARGETS]

    def simulate_growth(self, weather_data: pd.DataFrame | None = None):
        return self.simulate_growth_stage(weather_data=weather_data)

    def calculate_gdd_from_temperature_mean_2m(self, temperature_mean_2m: float):
        return self.phenology_model.compute_thermal_time(temperature_mean_2m)

    def simulate_growth_stage(
        self,
        weather_data: pd.DataFrame | None = None,
        dfob: pd.DataFrame | None = None,
        include_diagnostics: bool = False,
    ) -> pd.DataFrame:
        return self.phenology_model.compute_stages(
            self.planting_date,
            weather_data,
            dfob=dfob,
            include_diagnostics=include_diagnostics,
        )

    def evaluate_spray_weather(self, dfwd: pd.DataFrame, mode: str | None = None) -> pd.DataFrame:
        return MaizeSprayWeather(config=spray_weather_config, mode=mode).evaluate(hour_data=dfwd)

    def simulate_irrigation_progress(self, payload: dict) -> dict:
        model = CottonIrrigationModel(
            latitude=payload["latitude"],
            longitude=payload["longitude"],
            soil_type=payload["soil_type"],
            irrigation_method=payload["irrigation_method"],
            mulch_enabled=bool(payload.get("mulch_enabled", self.mulch_enabled)),
            region_code=payload.get("region_code") or self.region_code,
        )
        return model.run(payload)

    def simulate_nutrition_progress(self, payload: dict) -> dict:
        return self.nutrition_model.run(payload)

    def simulate_management_progress(self, payload: dict) -> dict:
        model = CottonManagementModel(
            latitude=payload["latitude"],
            longitude=payload["longitude"],
            soil_type=payload["soil_type"],
            irrigation_method=payload["irrigation_method"],
            mulch_enabled=bool(payload.get("mulch_enabled", self.mulch_enabled)),
            fertigation_enabled=bool(payload.get("fertigation_enabled", self.fertigation_enabled)),
            region_code=payload.get("region_code") or self.region_code,
        )
        return model.run(payload)

    def simulate_disease_progress(
        self,
        *,
        weather_hourly: pd.DataFrame,
        growth_stage: pd.DataFrame,
        variety_susceptibility: dict[str, int] | None,
        applied_fungicides: list[dict] | None,
        decision_date: datetime.date | str,
    ) -> dict:
        codes = [item.eppo_code for item in self.diseases]
        return CottonDiseaseModel(codes).run(
            weather_hourly=weather_hourly,
            growth_stage=growth_stage,
            variety_susceptibility=variety_susceptibility,
            applied_fungicides=applied_fungicides,
            decision_date=decision_date,
        )

    def simulate_insect_progress(
        self,
        *,
        weather_hourly: pd.DataFrame,
        growth_stage: pd.DataFrame | None,
        variety_susceptibility: dict[str, int] | None,
        applied_insecticides: pd.DataFrame | None,
        decision_date: datetime.date | str,
    ) -> dict:
        codes = [item.eppo_code for item in self.insects]
        return CottonInsectModel(codes).run(
            weather_hourly=weather_hourly,
            growth_stage=growth_stage,
            variety_susceptibility=variety_susceptibility,
            applied_insecticides=applied_insecticides,
            decision_date=decision_date,
        )
