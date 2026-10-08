"""Equipment labels never impose an efficiency or yield ranking."""

from copy import deepcopy
from math import isclose
from .types import IrrigationBoundary, DualDomainState, SoilColumnState, bounded

METHODS = {"flood", "surface_drip", "micro_sprinkler", "sprinkler"}


def partition_irrigation(event, state, parameters):
    if event.method not in METHODS:
        raise ValueError("Unknown irrigation method")
    bounded(event.amount_mm, "amount_mm")
    bounded(event.wetted_fraction, "wetted_fraction", 1e-9, 1)
    if not isclose(event.wetted_fraction, state.wetted_fraction, abs_tol=1e-12):
        raise ValueError(
            "Event geometry differs from state; explicitly remap at a segment boundary"
        )
    fractions = [
        bounded(getattr(event, k), k, 0, 1)
        for k in (
            "application_evaporation_fraction",
            "drift_fraction",
            "canopy_fraction",
        )
    ]
    if sum(fractions) > 1:
        raise ValueError("Irrigation partitions exceed input")
    if event.application_depth_mm != 0:
        raise ValueError("Only surface application is supported")
    bounded(event.start_minute, "start_minute", 0, 1440)
    duration = (
        1440.0
        if event.duration_minutes is None
        else bounded(event.duration_minutes, "duration_minutes", 1e-9, 1440)
    )
    if event.start_minute + duration > 1440:
        raise ValueError("Split events that cross midnight")
    if event.measurement_location == "pump":
        fraction = bounded(event.conveyance_fraction, "conveyance_fraction", 0, 1)
        pump = event.amount_mm
        field = pump * fraction
    elif event.measurement_location == "field":
        field = event.amount_mm
        pump = (
            field
            if event.conveyance_fraction is None
            else field
            / bounded(event.conveyance_fraction, "conveyance_fraction", 1e-9, 1)
        )
    else:
        raise ValueError("Explicit metering boundary required")
    evap, drift, canopy = [field * x for x in fractions]
    soil = field - evap - drift - canopy
    return IrrigationBoundary(
        field,
        soil / state.wetted_fraction,
        0.0,
        canopy,
        evap,
        drift,
        pump,
        pump - field,
    )


def remap_domains(state, new_wetted_fraction):
    """Nested wetted areas; area overlaps conserve each layer and pond water."""
    b = bounded(new_wetted_fraction, "new_wetted_fraction", 1e-9, 1)
    a = state.wetted_fraction
    if a == b:
        return deepcopy(state)
    oldwet = state.wet
    olddry = state.dry if a < 1 else oldwet

    def mix(wetarea, dryarea, total):
        return SoilColumnState(
            [
                (wetarea * w + dryarea * d) / total
                for w, d in zip(oldwet.water_mm, olddry.water_mm)
            ],
            (wetarea * oldwet.pond_mm + dryarea * olddry.pond_mm) / total,
        )

    wet = mix(min(a, b), max(0.0, b - a), b)
    dry = mix(max(0.0, a - b), 1 - max(a, b), 1 - b) if b < 1 else None
    return DualDomainState(b, wet, dry, state.canopy_water_mm, state.last_date)
