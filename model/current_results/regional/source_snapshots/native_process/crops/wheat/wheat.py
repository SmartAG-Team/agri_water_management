from __future__ import annotations

import datetime
from typing import Any, Dict, Optional

import pandas as pd

from core.crop import Crop
from crops.wheat.management import WheatManagementModel
from crops.wheat.irrigation import WheatIrrigationModel
from crops.wheat.nutrition import WheatNutritionModel
from crops.wheat.phenology import WheatPhenology
from crops.maize.spray_weather import MaizeSprayWeather
from crops.wheat.config import DISEASE_TARGETS, INSECT_TARGETS, spray_weather_config
from crops.wheat.disease import WheatDiseaseModel, WheatDiseaseTarget
from crops.wheat.insect import WheatInsectModel, WheatInsectTarget


class Wheat(Crop):
    """
    Minimal wheat crop facade for phenology simulation.

    The broader disease/insect/nutrition pipeline is not wired yet; this class
    currently exposes the methods needed by the phenology API.
    """

    def __init__(
        self,
        planting_date: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        variety_maturation_group: str = "middle",
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        **_,
    ) -> None:
        params = dict(params or {})
        phenology_params = params.get("phenology", {}).copy()

        if "variety_maturation_group" in params:
            phenology_params.setdefault(
                "variety_maturation_group", params.pop("variety_maturation_group")
            )
        phenology_params.setdefault("variety_maturation_group", variety_maturation_group)

        param_latitude = latitude if latitude is not None else params.get("latitude")
        param_longitude = longitude if longitude is not None else params.get("longitude")
        if param_latitude is not None:
            phenology_params.setdefault("latitude", param_latitude)
        if param_longitude is not None:
            phenology_params.setdefault("longitude", param_longitude)
        params["phenology"] = phenology_params

        self.latitude = param_latitude
        self.longitude = param_longitude
        self.planting_date: Optional[datetime.date]
        if planting_date is not None:
            self.planting_date = pd.to_datetime(planting_date).date()
        else:
            self.planting_date = None

        super().__init__(params=params)
        self.phenology_model = WheatPhenology(params=phenology_params)
        self.diseases = [WheatDiseaseTarget(code) for code in DISEASE_TARGETS]
        self.insects = [WheatInsectTarget(code) for code in INSECT_TARGETS]
        self.irrigation_model = WheatIrrigationModel(
            latitude=float(self.latitude) if self.latitude is not None else float(phenology_params.get("latitude", 35.0)),
            longitude=float(self.longitude) if self.longitude is not None else float(phenology_params.get("longitude", 114.5)),
            soil_type="sandy_loam",
            irrigation_method="sprinkler",
        )
        self.nutrition_model = WheatNutritionModel()
        self.management_model = WheatManagementModel(
            latitude=float(self.latitude) if self.latitude is not None else float(phenology_params.get("latitude", 35.0)),
            longitude=float(self.longitude) if self.longitude is not None else float(phenology_params.get("longitude", 114.5)),
            soil_type="sandy_loam",
            irrigation_method="sprinkler",
        )

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
        return WheatDiseaseModel(codes).run(
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
        return WheatInsectModel(codes).run(
            weather_hourly=weather_hourly,
            growth_stage=growth_stage,
            variety_susceptibility=variety_susceptibility,
            applied_insecticides=applied_insecticides,
            decision_date=decision_date,
        )

    def simulate_irrigation_progress(self, payload: dict) -> dict:
        irrigation_model = WheatIrrigationModel(
            latitude=payload["latitude"],
            longitude=payload["longitude"],
            soil_type=payload["soil_type"],
            irrigation_method=payload["irrigation_method"],
        )
        return irrigation_model.run(payload)

    def simulate_nutrition_progress(self, payload: dict) -> dict:
        return self.nutrition_model.run(payload)

    def simulate_management_progress(self, payload: dict) -> dict:
        management_model = WheatManagementModel(
            latitude=payload["latitude"],
            longitude=payload["longitude"],
            soil_type=payload["soil_type"],
            irrigation_method=payload["irrigation_method"],
        )
        return management_model.run(payload)
