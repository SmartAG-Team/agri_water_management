from __future__ import annotations

from typing import Iterable, Sequence

import pandas as pd


DAILY_WEATHER_REQUIRED_COLUMNS = [
    "temperature_2m_mean",
    "temperature_2m_min",
    "temperature_2m_max",
    "precipitation_sum",
    "shortwave_radiation_sum",
    "windspeed_10m_mean",
    "relative_humidity_2m_mean",
]

DAILY_WEATHER_NUMERIC_COLUMNS = [
    "temperature_2m_mean",
    "temperature_2m_min",
    "temperature_2m_max",
    "precipitation_sum",
    "shortwave_radiation_sum",
    "windspeed_10m_min",
    "windspeed_10m_max",
    "windspeed_10m_mean",
    "relative_humidity_2m_mean",
]

DAILY_WEATHER_RANGES = {
    "temperature_2m_mean": (-60.0, 60.0, "deg C"),
    "temperature_2m_min": (-60.0, 60.0, "deg C"),
    "temperature_2m_max": (-60.0, 60.0, "deg C"),
    "precipitation_sum": (0.0, 500.0, "mm/day"),
    "shortwave_radiation_sum": (0.0, 45.0, "MJ/m2/day"),
    "windspeed_10m_mean": (0.0, 75.0, "m/s"),
    "relative_humidity_2m_mean": (0.0, 100.0, "%"),
}

HOURLY_WEATHER_ALIASES = {
    "relativehumidity_2m": "relative_humidity_2m",
    "windspeed_10m": "wind_speed_10m",
}

HOURLY_WEATHER_REQUIRED_COLUMNS = [
    "temperature_2m",
    "relative_humidity_2m",
    "wind_speed_10m",
    "precipitation",
    "shortwave_radiation",
]


def _row_label(df: pd.DataFrame, idx: int) -> str:
    row = df.loc[idx]
    if "Date" in df.columns:
        return str(row["Date"])
    if "DateTime" in df.columns:
        return str(row["DateTime"])
    return f"row {idx}"


def require_weather_columns(df: pd.DataFrame, required: Sequence[str], *, label: str) -> None:
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"{label} missing required weather field(s): {', '.join(missing)}")
    for col in required:
        bad = df.index[df[col].isna()].tolist()
        if bad:
            where = ", ".join(_row_label(df, idx) for idx in bad[:5])
            suffix = "" if len(bad) <= 5 else f" and {len(bad) - 5} more"
            raise ValueError(f"{label}.{col} has missing or non-numeric value at {where}{suffix}")


def require_weather_value(row: dict | pd.Series, field: str, *, context: str = "weather") -> float:
    if field not in row:
        raise ValueError(f"{context} missing required weather field: {field}")
    value = row[field]
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric):
        raise ValueError(f"{context}.{field} has missing or non-numeric value")
    return float(numeric)


def _raise_daily_range_error(field: str, value: float, label: str, minimum: float, maximum: float, unit: str) -> None:
    message = (
        f"Invalid daily weather {field} on {label}: {value}; "
        f"expected {minimum:g}..{maximum:g} {unit}."
    )
    if field == "shortwave_radiation_sum" and value > maximum:
        message += " Value looks like Wh/m2/day; convert by multiplying 0.0036 before calling API."
    raise ValueError(message)


def validate_daily_weather_ranges(df: pd.DataFrame) -> None:
    for field, (minimum, maximum, unit) in DAILY_WEATHER_RANGES.items():
        if field not in df.columns:
            continue
        bad = df.index[(df[field] < minimum) | (df[field] > maximum)].tolist()
        if bad:
            idx = bad[0]
            _raise_daily_range_error(field, float(df.loc[idx, field]), _row_label(df, idx), minimum, maximum, unit)

    bad_temperature_order = df.index[
        (df["temperature_2m_min"] > df["temperature_2m_mean"])
        | (df["temperature_2m_mean"] > df["temperature_2m_max"])
    ].tolist()
    if bad_temperature_order:
        idx = bad_temperature_order[0]
        label = _row_label(df, idx)
        raise ValueError(
            "Invalid daily weather temperature order on "
            f"{label}: expected temperature_2m_min <= temperature_2m_mean <= temperature_2m_max."
        )


def normalize_hourly_weather(entries: Iterable[dict] | pd.DataFrame, *, allow_empty: bool = False) -> pd.DataFrame:
    if isinstance(entries, pd.DataFrame):
        df = entries.copy()
    else:
        df = pd.DataFrame(list(entries or []))
    if df.empty:
        if allow_empty:
            return df
        raise ValueError("hourly weather cannot be empty")

    if "DateTime" not in df.columns:
        raise ValueError("hourly records need 'DateTime'")
    df["DateTime"] = pd.to_datetime(df["DateTime"])

    for src, dst in HOURLY_WEATHER_ALIASES.items():
        if src in df.columns and dst not in df.columns:
            df[dst] = df[src]

    for col in HOURLY_WEATHER_REQUIRED_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    require_weather_columns(df, HOURLY_WEATHER_REQUIRED_COLUMNS, label="hourly weather")

    return df.sort_values("DateTime").reset_index(drop=True)


def normalize_daily_weather(entries: Iterable[dict] | pd.DataFrame, *, allow_empty: bool = False) -> pd.DataFrame:
    if isinstance(entries, pd.DataFrame):
        df = entries.copy()
    else:
        df = pd.DataFrame(list(entries or []))
    if df.empty:
        if allow_empty:
            return df
        raise ValueError("daily weather cannot be empty")

    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"]).dt.date
    elif "DateTime" in df.columns:
        df["Date"] = pd.to_datetime(df["DateTime"]).dt.date
    else:
        raise ValueError("daily weather needs 'Date' or 'DateTime'")

    for col in DAILY_WEATHER_NUMERIC_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    require_weather_columns(df, DAILY_WEATHER_REQUIRED_COLUMNS, label="daily weather")
    validate_daily_weather_ranges(df)

    return df.sort_values("Date").reset_index(drop=True)


def merge_historical_and_forecast(historical: pd.DataFrame, forecast: pd.DataFrame) -> pd.DataFrame:
    if historical.empty and forecast.empty:
        return pd.DataFrame()
    if historical.empty:
        return forecast.copy()
    if forecast.empty:
        return historical.copy()

    df = pd.concat([historical, forecast], ignore_index=True)
    df = df.sort_values("Date").drop_duplicates(subset=["Date"], keep="last").reset_index(drop=True)
    return df
