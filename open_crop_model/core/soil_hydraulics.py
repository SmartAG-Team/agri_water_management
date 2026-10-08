from __future__ import annotations

from typing import Any


SOIL_DEFAULTS = {
    "sand": {"field_capacity": 0.16, "wilting_point": 0.07, "air_dry": 0.03, "saturation": 0.36, "bulk_density": 1.55, "ksat_mm_day": 240},
    "sandy_loam": {"field_capacity": 0.24, "wilting_point": 0.10, "air_dry": 0.04, "saturation": 0.42, "bulk_density": 1.45, "ksat_mm_day": 120},
    "clay": {"field_capacity": 0.36, "wilting_point": 0.20, "air_dry": 0.08, "saturation": 0.50, "bulk_density": 1.30, "ksat_mm_day": 35},
}


def validate_and_prepare_profile(soil_type: str, soil_profile: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    if len(soil_profile or []) < 3 or len(soil_profile or []) > 5:
        raise ValueError("soil_profile must have 3 to 5 layers")
    soil_key = (soil_type or "sandy_loam").strip().lower().replace(" ", "_")
    if soil_key not in SOIL_DEFAULTS:
        raise ValueError(f"Unknown soil_type: {soil_type}")
    defaults = SOIL_DEFAULTS[soil_key]

    prepared: list[dict] = []
    adjustments: list[dict] = []
    defaults_used: list[dict] = []
    prev_bottom = 0.0

    for idx, layer in enumerate(soil_profile):
        top = float(layer["depth_top_cm"])
        bottom = float(layer["depth_bottom_cm"])
        if idx == 0 and top != 0:
            raise ValueError("top layer must start at 0 cm")
        if abs(top - prev_bottom) > 1e-6:
            raise ValueError("soil layers must be contiguous")
        if bottom <= top:
            raise ValueError("each soil layer must have positive thickness")
        prev_bottom = bottom

        out = dict(layer)
        for key in ("field_capacity", "wilting_point", "air_dry", "saturation", "bulk_density", "ksat_mm_day"):
            if out.get(key) is None:
                raise ValueError(f"soil_profile[{idx}].{key} is required")
            out[key] = float(out[key])

        if out["field_capacity"] < defaults["field_capacity"] * 0.65 or out["field_capacity"] > defaults["field_capacity"] * 1.35:
            adjustments.append({"target": f"soil_profile[{idx}].field_capacity", "from": out["field_capacity"], "to": defaults["field_capacity"], "reason": f"adjusted_to_{soil_key}"})
            out["field_capacity"] = defaults["field_capacity"]
        if out["wilting_point"] < defaults["wilting_point"] * 0.6 or out["wilting_point"] > defaults["wilting_point"] * 1.4:
            adjustments.append({"target": f"soil_profile[{idx}].wilting_point", "from": out["wilting_point"], "to": defaults["wilting_point"], "reason": f"adjusted_to_{soil_key}"})
            out["wilting_point"] = defaults["wilting_point"]
        if out["air_dry"] > out["wilting_point"]:
            adjustments.append({"target": f"soil_profile[{idx}].air_dry", "from": out["air_dry"], "to": min(defaults["air_dry"], out["wilting_point"] * 0.8), "reason": "air_dry_limited_by_wilting_point"})
            out["air_dry"] = min(defaults["air_dry"], out["wilting_point"] * 0.8)
        if out["saturation"] <= out["field_capacity"]:
            adjustments.append({"target": f"soil_profile[{idx}].saturation", "from": out["saturation"], "to": max(defaults["saturation"], out["field_capacity"] + 0.08), "reason": "saturation_must_exceed_field_capacity"})
            out["saturation"] = max(defaults["saturation"], out["field_capacity"] + 0.08)

        thickness_mm = (bottom - top) * 10.0
        out["thickness_mm"] = thickness_mm
        out["taw_mm"] = max(0.0, (out["field_capacity"] - out["wilting_point"]) * thickness_mm)
        out["raw_mm"] = out["taw_mm"] * 0.5
        out["label"] = out.get("label") or f"{int(top)}-{int(bottom)}cm"
        if out.get("initial_soil_water_mm") is not None:
            out["soil_water_mm"] = float(out["initial_soil_water_mm"])
        else:
            rel = out.get("initial_relative_water")
            if rel is not None:
                out["soil_water_mm"] = out["taw_mm"] * max(0.0, min(1.0, float(rel)))
            else:
                raise ValueError(
                    f"soil_profile[{idx}] requires initial_soil_water_mm or initial_relative_water"
                )
        prepared.append(out)

    if prev_bottom < 90:
        raise ValueError("soil_profile final depth must be at least 90 cm")
    return prepared, adjustments, defaults_used
