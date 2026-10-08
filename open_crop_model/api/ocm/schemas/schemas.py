from __future__ import annotations

# Compatibility module for older imports.  The public contracts live in the
# split schema modules; keep this file as a thin re-export layer so definitions
# cannot drift between crops or API entry points.
from .common import (
    CombinedStrategy,
    CultivarPhenologyParameters,
    DailyWeatherEntry,
    GrowthStageEntry,
    HourlyWeatherEntry,
    VarietyCharacteristics,
    _Model,
)
from .disease_schema import (
    DiseaseRequest,
    DiseaseResponse,
    DiseaseWeatherEntry,
    FungicideApplication,
)
from .decision_date_actions_schema import DecisionDateActionsRequest, DecisionDateActionsResponse
from .fullseason_simulation_schema import FullSeasonSimulationRequest, FullSeasonSimulationResponse
from .fullseason_actions_schema import FullSeasonActionsRequest, FullSeasonActionsResponse
from .insect_schema import AppliedInsecticide, InsectRequest, InsectResponse, PesticideSpec
from .irrigation_schema import (
    EconomicParameters,
    IrrigationDailyWeatherEntry,
    IrrigationRequest,
    IrrigationResponse,
    SoilLayer,
)
from .management_schema import ManagementRequest, ManagementResponse
from .nutrition_schema import (
    FertilizerApplication,
    NutritionDailyWeatherEntry,
    NutritionRequest,
    NutritionResponse,
    SoilTest,
)
from .phenology_schema import PhenologyRequest, PhenologyResponse
from .spray_weather_schema import PesticideType, SprayWeatherRequest, SprayWeatherResponse

__all__ = [
    "_Model",
    "CombinedStrategy",
    "CultivarPhenologyParameters",
    "DailyWeatherEntry",
    "HourlyWeatherEntry",
    "GrowthStageEntry",
    "VarietyCharacteristics",
    "PhenologyRequest",
    "PhenologyResponse",
    "PesticideType",
    "SprayWeatherRequest",
    "SprayWeatherResponse",
    "DiseaseWeatherEntry",
    "FungicideApplication",
    "DiseaseRequest",
    "DiseaseResponse",
    "PesticideSpec",
    "AppliedInsecticide",
    "InsectRequest",
    "InsectResponse",
    "NutritionDailyWeatherEntry",
    "SoilTest",
    "FertilizerApplication",
    "NutritionRequest",
    "NutritionResponse",
    "IrrigationDailyWeatherEntry",
    "SoilLayer",
    "EconomicParameters",
    "IrrigationRequest",
    "IrrigationResponse",
    "ManagementRequest",
    "ManagementResponse",
    "DecisionDateActionsRequest",
    "DecisionDateActionsResponse",
    "FullSeasonSimulationRequest",
    "FullSeasonSimulationResponse",
    "FullSeasonActionsRequest",
    "FullSeasonActionsResponse",
]
