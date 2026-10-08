from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter


@lru_cache(maxsize=1)
def get_router() -> APIRouter:
    from .routers import (
        decision_date_actions,
        disease_model,
        fullseason_actions,
        fullseason_simulation,
        insect_model,
        irrigation_model,
        management_model,
        nutrition_model,
        phenology,
        spray_weather,
        task,
    )

    router = APIRouter()
    router.include_router(task.router, tags=["OCM Task"])
    router.include_router(phenology.router, tags=["OCM"])
    router.include_router(spray_weather.router, tags=["OCM"])
    router.include_router(decision_date_actions.router, tags=["OCM"])
    router.include_router(disease_model.router, tags=["OCM"])
    router.include_router(fullseason_simulation.router, tags=["OCM"])
    router.include_router(fullseason_actions.router, tags=["OCM"])
    router.include_router(insect_model.router, tags=["OCM"])
    router.include_router(nutrition_model.router, tags=["OCM"])
    router.include_router(irrigation_model.router, tags=["OCM"])
    router.include_router(management_model.router, tags=["OCM"])
    return router


def __getattr__(name: str):
    if name == "router":
        return get_router()
    raise AttributeError(name)


__all__ = ["get_router", "router"]
