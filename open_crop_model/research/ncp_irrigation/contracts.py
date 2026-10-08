"""Canonical daily inputs: mm water, volumetric theta, Celsius, MJ/m²/day."""

from datetime import date, timedelta
from math import isfinite
from .irrigation_policy import validate_policy
from .tillage import event_schedule

METHODS = {"flood", "surface_drip", "micro_sprinkler", "sprinkler"}


def finite(value, name, low=None, high=None):
    if isinstance(value, bool):
        raise ValueError(f"{name}: boolean is not a numeric measurement")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name}: numeric value required") from exc
    if (
        not isfinite(number)
        or (low is not None and number < low)
        or (high is not None and number > high)
    ):
        raise ValueError(f"{name}: non-finite or out of bounds")
    return number


def validate_inputs(inputs):
    """Reject gaps, untraceable irrigation, invalid states and outcome leakage."""
    if "observed_yield_kg_ha" in inputs or "yield_target_kg_ha" in inputs:
        raise ValueError(
            "Observed/target yield cannot initialize process growth or nutrients"
        )
    if inputs.get("crop") not in {"wheat", "maize", "fallow"}:
        raise ValueError("crop must be wheat, maize or fallow")
    start, end = (
        date.fromisoformat(inputs["start_date"]),
        date.fromisoformat(inputs["end_date"]),
    )
    if end < start:
        raise ValueError("season dates reversed")
    if inputs.get("cutting_date") is not None:
        try:
            cutting = date.fromisoformat(inputs["cutting_date"])
        except (TypeError, ValueError) as exc:
            raise ValueError("cutting_date must be an ISO date") from exc
        if cutting != end:
            raise ValueError("Daily forcing must terminate on cutting_date")
    finite(inputs["latitude_deg"], "latitude", -65, 65)
    finite(inputs["elevation_m"], "elevation", -400, 8000)
    if 'organic_mulch_cover_fraction' in inputs:
        finite(inputs['organic_mulch_cover_fraction'],'organic mulch cover fraction',0.,1.)
    method = inputs.get("et0_method")
    if method not in {"hargreaves", "fao56_pm", "provided"}:
        raise ValueError("Explicit et0_method required")
    if method == "fao56_pm":
        finite(inputs.get("wind_height_m"), "wind_height_m", 0.2, 100)
    expected = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    if [date.fromisoformat(w["date"]) for w in inputs["weather"]] != expected:
        raise ValueError("Daily weather must be ordered, complete and unique")
    for w in inputs["weather"]:
        lo = finite(w["tmin_c"], "tmin", -90, 65)
        hi = finite(w["tmax_c"], "tmax", -90, 65)
        if hi < lo:
            raise ValueError("tmax below tmin")
        finite(w["precipitation_mm"], "precipitation", 0)
        if method == 'provided':
            finite(w.get('reference_et0_mm'), 'supplied reference_et0_mm', 0)
        if method == "fao56_pm":
            finite(w.get("solar_radiation_mj_m2"), "solar radiation", 0)
            finite(w.get("wind_speed_m_s"), "wind speed", 0)
            if "actual_vapour_pressure_kpa" in w:
                finite(w["actual_vapour_pressure_kpa"], "vapour pressure", 0)
            else:
                finite(w.get("relative_humidity_pct"), "relative humidity", 0, 100)
    if not inputs["soil_layers"]:
        raise ValueError("Soil profile is empty")
    for layer in inputs["soil_layers"]:
        finite(layer["thickness_mm"], "thickness", 1e-6)
        thresholds = [
            finite(layer[k], k, 0, 1)
            for k in ("air_dry", "wilting_point", "field_capacity", "saturation")
        ]
        if not thresholds[0] <= thresholds[1] < thresholds[2] < thresholds[3]:
            raise ValueError("Require air_dry <= WP < FC < SAT")
        finite(layer["ksat_mm_day"], "ksat", 0)
    if "initial_theta" in inputs:
        if len(inputs["initial_theta"]) != len(inputs["soil_layers"]):
            raise ValueError("Initial profile length mismatch")
        for theta, layer in zip(inputs["initial_theta"], inputs["soil_layers"]):
            finite(theta, "initial theta", layer["air_dry"], layer["saturation"])
    tech = inputs["technology"]
    if tech.get("method") not in METHODS:
        raise ValueError("Unsupported irrigation method")
    fw = finite(tech.get("wetted_fraction"), "wetted_fraction", 1e-9, 1)
    fractions = [
        finite(tech.get(k), k, 0, 1)
        for k in (
            "application_evaporation_fraction",
            "drift_fraction",
            "canopy_fraction",
        )
    ]
    if sum(fractions) > 1:
        raise ValueError("Irrigation fractions exceed total supply")
    depth = finite(tech.get("application_depth_mm"), "application depth", 0)
    if depth != 0:
        raise ValueError("Four-method contract covers surface application only")
    ids = set()
    for event in inputs.get("irrigation_events", []):
        if not event.get("event_id") or event["event_id"] in ids:
            raise ValueError("Unique event_id required")
        ids.add(event["event_id"])
        day = date.fromisoformat(event["date"])
        if not start <= day <= end:
            raise ValueError("Irrigation outside season")
        finite(event["amount_mm"], "irrigation amount", 0)
        if event.get("measurement_location") not in {"field", "pump"}:
            raise ValueError("Unknown irrigation metering boundary")
        if event["measurement_location"] == "pump":
            finite(event.get("conveyance_fraction"), "conveyance_fraction", 0, 1)
        if "duration_minutes" in event:
            finite(event["duration_minutes"], "duration", 1e-9, 1440)
        if "start_minute" in event:
            finite(event["start_minute"], "start minute", 0, 1440)
        if event.get("start_minute", 0) + event.get("duration_minutes", 1440) > 1440:
            raise ValueError("Event crosses day boundary; split explicitly")
    if 'irrigation_policy' in inputs:
        policy = validate_policy(inputs['irrigation_policy'])
        fixed = inputs.get('irrigation_events', [])
        if any(event['measurement_location'] != 'field' for event in fixed):
            raise ValueError('Irrigation policy requires field measurement boundaries for fixed events')
        if sum(float(event['amount_mm']) for event in fixed) > policy['seasonal_cap_mm']:
            raise ValueError('Fixed irrigation total exceeds policy seasonal_cap_mm')
    event_schedule(inputs)
    return inputs
