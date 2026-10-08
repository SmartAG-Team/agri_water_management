"""FAO-56 daily reference ET. Temperature-only estimation is an explicit option.

Primary methods: https://www.fao.org/4/x0490e/x0490e07.htm
https://www.fao.org/4/x0490e/x0490e08.htm
No missing meteorological driver is silently synthesized.
"""

from datetime import date
from math import acos, cos, exp, log, pi, sin, sqrt, tan
from .types import bounded


def radiation_to_mj(value, unit):
    bounded(value, "radiation")
    if unit == "J/m2/day":
        return value / 1e6
    if unit == "MJ/m2/day":
        return value
    raise ValueError("Radiation unit must be J/m2/day or MJ/m2/day")


def wind_to_2m(speed, height):
    bounded(speed, "wind_speed_m_s")
    bounded(height, "wind_height_m", 0.2, 100)
    return speed * 4.87 / log(67.8 * height - 5.42)


def extraterrestrial_radiation(day, latitude):
    bounded(latitude, "latitude_deg", -65, 65)
    j = date.fromisoformat(day).timetuple().tm_yday
    phi = latitude * pi / 180
    dr = 1 + 0.033 * cos(2 * pi * j / 365)
    declination = 0.409 * sin(2 * pi * j / 365 - 1.39)
    omega = acos(max(-1.0, min(1.0, -tan(phi) * tan(declination))))
    return (
        24
        * 60
        / pi
        * 0.0820
        * dr
        * (
            omega * sin(phi) * sin(declination)
            + cos(phi) * cos(declination) * sin(omega)
        )
    )


def _vapour(temp):
    return 0.6108 * exp(17.27 * temp / (temp + 237.3))


def reference_et0(weather, *, latitude_deg, elevation_m, wind_height_m, method):
    if method == 'provided':
        return bounded(weather.get('reference_et0_mm'), 'supplied reference_et0_mm', 0)
    if method == "fao56_pm":
        return reference_et0_components(
            weather, latitude_deg=latitude_deg, elevation_m=elevation_m,
            wind_height_m=wind_height_m,
        )["reference_et0_mm"]
    if method != "hargreaves":
        raise ValueError("Unknown ET0 method")
    lo = bounded(weather.get("tmin_c"), "tmin_c", -90, 65)
    hi = bounded(weather.get("tmax_c"), "tmax_c", -90, 65)
    if hi < lo:
        raise ValueError("tmax below tmin")
    ra = extraterrestrial_radiation(weather["date"], latitude_deg)
    bounded(elevation_m, "elevation_m", -400, 8000)
    return max(0.0, 0.0023 * ((lo + hi) / 2 + 17.8) * sqrt(hi - lo) * 0.408 * ra)


def reference_et0_components(weather, *, latitude_deg, elevation_m, wind_height_m,
                            vapour_pressure_policy='reject'):
    """Signed FAO-56 radiative/aerodynamic demand in mm/day, with G=0.

    Net radiation may be supplied explicitly in MJ/m²/day. Otherwise the
    existing shortwave/longwave estimate is used. Only total ET is clipped;
    negative net radiation remains visible in the radiative component.
    """
    if vapour_pressure_policy not in ('reject', 'zero_negative_daily_vpd'):
        raise ValueError('Unknown vapour pressure policy')
    lo = bounded(weather.get("tmin_c"), "tmin_c", -90, 65)
    hi = bounded(weather.get("tmax_c"), "tmax_c", -90, 65)
    if hi < lo:
        raise ValueError("tmax below tmin")
    temp = (lo + hi) / 2
    ra = extraterrestrial_radiation(weather["date"], latitude_deg)
    bounded(elevation_m, "elevation_m", -400, 8000)
    rs = bounded(weather.get("solar_radiation_mj_m2"), "solar_radiation_mj_m2")
    u2 = wind_to_2m(weather.get("wind_speed_m_s"), wind_height_m)
    es = (_vapour(lo) + _vapour(hi)) / 2
    if "actual_vapour_pressure_kpa" in weather:
        ea = bounded(weather["actual_vapour_pressure_kpa"], "vapour pressure")
    else:
        ea = (
            es
            * bounded(
                weather.get("relative_humidity_pct"), "relative_humidity_pct", 0, 100
            )
            / 100
        )
    negative_vpd = ea > es + 1e-6
    if negative_vpd and (vapour_pressure_policy == 'reject' or ea > _vapour(hi) + 1e-6):
        raise ValueError("Daily vapour pressure exceeds saturation")
    pressure = (
        bounded(weather["pressure_kpa"], "pressure_kpa", 1)
        if "pressure_kpa" in weather
        else 101.3 * ((293 - 0.0065 * elevation_m) / 293) ** 5.26
    )
    gamma = 0.000665 * pressure
    delta = 4098 * _vapour(temp) / (temp + 237.3) ** 2
    clear = (0.75 + 2e-5 * elevation_m) * ra
    cloud = 1.35 * min(1.0, rs / max(clear, 1e-9)) - 0.35
    # Bound cloud factor to zero under near-dark daily conditions.
    longwave = (
        4.903e-9
        * ((lo + 273.16) ** 4 + (hi + 273.16) ** 4)
        / 2
        * (0.34 - 0.14 * sqrt(ea))
        * max(0.0, cloud)
    )
    rn = (bounded(weather["net_radiation_mj_m2"], "net_radiation_mj_m2", -50, 80)
          if "net_radiation_mj_m2" in weather else 0.77 * rs - longwave)
    denominator = delta + gamma * (1 + 0.34 * u2)
    radiation = 0.408 * delta * rn / denominator
    aerodynamic = gamma * 900 / (temp + 273) * u2 * max(0., es - ea) / denominator
    return dict(radiation_mm=radiation, aerodynamic_mm=aerodynamic,
                reference_et0_mm=max(0., radiation + aerodynamic),
                net_radiation_mj_m2=rn, negative_daily_vpd=negative_vpd,
                vapour_pressure_deficit_kpa=max(0., es - ea))
