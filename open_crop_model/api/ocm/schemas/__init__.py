# api/ocm/schemas/__init__.py
from .common import (
    _Model, CombinedStrategy,
    DailyWeatherEntry, HourlyWeatherEntry, GrowthStageEntry,
    CultivarPhenologyParameters, VarietyCharacteristics,
)

from .phenology_schema import PhenologyRequest, PhenologyResponse
from .spray_weather_schema import PesticideType, SprayWeatherRequest, SprayWeatherResponse
from .nutrition_schema import (
    FertilizerApplication, NutritionRequest, NutritionResponse
)
from .disease_schema import (
    DiseaseWeatherEntry, FungicideApplication, DiseaseRequest, DiseaseResponse
)
from .insect_schema import (
    PesticideSpec, AppliedInsecticide, InsectRequest, InsectResponse
)
from .irrigation_schema import IrrigationRequest, IrrigationResponse
from .management_schema import ManagementRequest, ManagementResponse
from .decision_date_actions_schema import DecisionDateActionsRequest, DecisionDateActionsResponse
from .fullseason_simulation_schema import FullSeasonSimulationRequest, FullSeasonSimulationResponse
from .fullseason_actions_schema import FullSeasonActionsRequest, FullSeasonActionsResponse

__all__ = [
    # common
    "_Model", "CombinedStrategy",
    "DailyWeatherEntry", "HourlyWeatherEntry", "GrowthStageEntry",
    "CultivarPhenologyParameters", "VarietyCharacteristics",
    # phenology
    "PhenologyRequest", "PhenologyResponse",
    # spray weather
    "PesticideType", "SprayWeatherRequest", "SprayWeatherResponse",
    # nutrition
    "FertilizerApplication", "NutritionRequest", "NutritionResponse",
    # disease
    "DiseaseWeatherEntry", "FungicideApplication", "DiseaseRequest", "DiseaseResponse",
    # insect
    "PesticideSpec", "AppliedInsecticide", "InsectRequest", "InsectResponse",
    # irrigation
    "IrrigationRequest", "IrrigationResponse",
    # management
    "ManagementRequest", "ManagementResponse",
    # decision date actions
    "DecisionDateActionsRequest", "DecisionDateActionsResponse",
    "FullSeasonSimulationRequest", "FullSeasonSimulationResponse",
    "FullSeasonActionsRequest", "FullSeasonActionsResponse",
]
