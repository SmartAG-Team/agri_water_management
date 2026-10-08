from __future__ import annotations

from .decision_date_actions_schema import DecisionDateActionsRequest, DecisionDateActionsResponse


class FullSeasonActionsRequest(DecisionDateActionsRequest):
    pass


class FullSeasonActionsResponse(DecisionDateActionsResponse):
    pass


for _M in (FullSeasonActionsRequest, FullSeasonActionsResponse):
    _M.model_rebuild()
