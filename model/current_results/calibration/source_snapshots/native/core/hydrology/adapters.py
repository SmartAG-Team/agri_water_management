"""Explicit legacy conversions. No inferred air-dry threshold or silent clipping."""

from .types import SoilLayerParameters, SoilColumnState, DualDomainState


def from_daily_engine(layers, *, air_dry_theta):
    if len(layers) != len(air_dry_theta):
        raise ValueError("Air-dry profile mismatch")
    parameters = []
    water = []
    for layer, air in zip(layers, air_dry_theta):
        depth = layer["depth_mm"]
        parameters.append(
            SoilLayerParameters(
                depth,
                air,
                layer["wilting_mm"] / depth,
                layer["field_capacity_mm"] / depth,
                layer["saturation_mm"] / depth,
                layer["ksat_mm_day"],
            )
        )
        water.append(layer["water_mm"])
    return tuple(parameters), DualDomainState(1.0, SoilColumnState(water), None)


def from_irrigation_profile(layers, *, air_dry_theta):
    if len(layers) != len(air_dry_theta):
        raise ValueError("Air-dry profile mismatch")
    parameters = []
    water = []
    for layer, air in zip(layers, air_dry_theta):
        if layer.get("gravel_fraction", 0.0) != 0.0:
            raise ValueError(
                "Nonzero gravel requires an explicit fine-earth capacity representation"
            )
        depth = (
            (layer["depth_bottom_cm"] - layer["depth_top_cm"])
            * 10
            * (1 - layer.get("gravel_fraction", 0.0))
        )
        parameters.append(
            SoilLayerParameters(
                depth,
                air,
                layer["wilting_point"],
                layer["field_capacity"],
                layer["saturation"],
                layer["ksat_mm_day"],
            )
        )
        water.append(layer["soil_water_mm"] + layer["wilting_point"] * depth)
    return tuple(parameters), DualDomainState(1.0, SoilColumnState(water), None)


def field_water_mm(state):
    if state.wetted_fraction == 1.0:
        return list(state.wet.water_mm)
    return [
        state.wetted_fraction * w
        + (1 - state.wetted_fraction) * (state.dry.water_mm[i] if state.dry else 0.0)
        for i, w in enumerate(state.wet.water_mm)
    ]
