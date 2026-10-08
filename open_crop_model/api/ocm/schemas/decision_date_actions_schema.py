from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from pydantic import Field, model_validator

from .common import _Model
from .fullseason_simulation_schema import FullSeasonSimulationRequest


class DecisionDateActionsRequest(_Model):
    decision_date: date
    simulation: FullSeasonSimulationRequest

    @model_validator(mode="after")
    def _validate_decision_date(self) -> "DecisionDateActionsRequest":
        end = self.simulation.cropseason.season_end
        if self.decision_date > end:
            raise ValueError("decision_date must be on or before simulation season_end")
        return self


class DecisionDateActionsResponse(_Model):
    crop_uuid: str
    crop_season_uuid: Optional[str] = ""
    planting_date: date
    decision_date: date
    season_start: date
    season_end: date
    applied: Dict[str, List[Dict[str, Any]]]
    action_recommendation_counts: Dict[str, int]
    action_recommendations: Dict[str, Optional[Dict[str, Any]]]
    spray_passes: List[Dict[str, Any]] = Field(default_factory=list)
    daily_risk: Dict[str, Any]
    stress_risk: Dict[str, Any] = Field(default_factory=dict)
    field_risk: List[Dict[str, Any]] = Field(default_factory=list)
    hourly: Dict[str, Any] = Field(default_factory=dict)
    integrated_daily_state: List[Dict[str, Any]]
    daily_state: List[Dict[str, Any]] = Field(default_factory=list)
    summary: Dict[str, Any] = Field(default_factory=dict)


for _M in (DecisionDateActionsRequest, DecisionDateActionsResponse):
    _M.model_rebuild()
