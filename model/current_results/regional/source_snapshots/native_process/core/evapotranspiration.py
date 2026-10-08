from __future__ import annotations

import math
from datetime import date


def extraterrestrial_radiation_mj_m2_day(day_of_year: int, latitude_deg: float) -> float:
    lat = math.radians(latitude_deg)
    dr = 1 + 0.033 * math.cos(2 * math.pi * day_of_year / 365)
    solar_decl = 0.409 * math.sin(2 * math.pi * day_of_year / 365 - 1.39)
    ws = math.acos(max(-1.0, min(1.0, -math.tan(lat) * math.tan(solar_decl))))
    gsc = 0.0820
    return (24 * 60 / math.pi) * gsc * dr * (
        ws * math.sin(lat) * math.sin(solar_decl) + math.cos(lat) * math.cos(solar_decl) * math.sin(ws)
    )


def reference_et_mm(
    dt: date,
    latitude_deg: float,
    tmean: float,
    tmin: float,
    tmax: float,
    radiation_mj_m2: float,
) -> float:
    for name, value in {
        "tmean": tmean,
        "tmin": tmin,
        "tmax": tmax,
        "radiation_mj_m2": radiation_mj_m2,
    }.items():
        if value is None or not math.isfinite(float(value)):
            raise ValueError(f"reference_et_mm requires finite {name}")
    ra = extraterrestrial_radiation_mj_m2_day(dt.timetuple().tm_yday, latitude_deg)
    rs = float(radiation_mj_m2)
    ra_evap_mm = 0.408 * max(ra, 0.1)
    et0 = 0.0023 * (tmean + 17.8) * math.sqrt(max(tmax - tmin, 0.1)) * ra_evap_mm
    rad_adj = max(0.45, min(1.15, rs / max(ra, 0.1)))
    return max(0.0, et0 * rad_adj)


def crop_et_components(et0: float, kc: float, canopy_cover: float, stress_factor: float | None = None) -> dict:
    canopy_cover = max(0.0, min(0.98, canopy_cover))
    kc = max(0.2, kc)
    etc_potential = et0 * kc
    potential_transpiration = etc_potential * canopy_cover
    soil_evaporation = max(0.0, etc_potential - potential_transpiration)
    transpiration_factor = 1.0 if stress_factor is None else max(0.0, min(1.0, stress_factor))
    actual_transpiration = potential_transpiration * transpiration_factor
    eta_actual = actual_transpiration + soil_evaporation
    return {
        "reference_et_mm": round(et0, 3),
        "crop_et_potential_mm": round(etc_potential, 3),
        "soil_evaporation_mm": round(soil_evaporation, 3),
        "potential_transpiration_mm": round(potential_transpiration, 3),
        "actual_transpiration_mm": round(actual_transpiration, 3),
        "eta_actual_mm": round(eta_actual, 3),
    }
