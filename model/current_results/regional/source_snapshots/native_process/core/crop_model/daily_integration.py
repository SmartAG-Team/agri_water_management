from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from typing import Any


_STATUS_FACTOR = {
    "LOW": 1.0,
    "NOT_SEASONAL": 1.0,
    "IRRIGATED": 1.0,
    "PROTECTED": 1.0,
    "ADEQUATE": 1.0,
    "WATCH": 0.92,
    "MEDIUM": 0.88,
    "DEFICIENT": 0.72,
    "HIGH": 0.68,
}


def _date_key(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value)
    return text[:10] if text else None


def _rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    return []


def _daily_blocks(result: dict[str, Any] | None, keys: tuple[str, ...]) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(result, dict):
        return {}
    for key in keys:
        value = result.get(key)
        if isinstance(value, dict):
            return {str(target): _rows(rows) for target, rows in value.items()}
    return {}


def _status(value: Any) -> str:
    if value is None:
        return "LOW"
    return str(value).upper()


def _factor(value: Any) -> float:
    return float(_STATUS_FACTOR.get(_status(value), 1.0))


def _stress_by_day(result: dict[str, Any] | None) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    if not isinstance(result, dict):
        return out
    stress = result.get("stress_risk") or {}
    if not isinstance(stress, dict):
        return out
    for target, rows in stress.items():
        for row in _rows(rows):
            day = _date_key(row.get("Date"))
            if not day:
                continue
            out.setdefault(day, {})[str(target)] = _status(row.get("stress_risk") or row.get("field_risk"))
    return out


def _field_by_day(result: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(result, dict):
        return {}
    return {
        day: _status(row.get("field_risk") or row.get("field_status"))
        for row in _rows(result.get("field_risk"))
        if (day := _date_key(row.get("Date")))
    }


def _base_daily_rows(*results: dict[str, Any] | None) -> list[dict[str, Any]]:
    for result in results:
        if not isinstance(result, dict):
            continue
        for key in (
            "integrated_daily_state",
            "daily_management_state",
            "no_action_daily_management_state",
            "display_daily_stress_risk",
            "daily_stress_risk",
            "no_action_daily_stress_risk",
        ):
            rows = _rows(result.get(key))
            if rows:
                return rows
        blocks = _daily_blocks(result, ("daily_drought_risk", "daily_fertiwater_risk", "daily_nutrition_risk", "daily_disease_risk", "daily_insect_risk"))
        for rows in blocks.values():
            if rows:
                return rows
    return []


def _float(row: dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float(row.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def _potential_growth(row: dict[str, Any]) -> float:
    actual = _float(row, "actual_growth_kg_ha")
    existing_factor = _float(row, "combined_growth_factor", 0.0)
    if actual > 0.0 and existing_factor > 1e-6:
        return actual / existing_factor
    return actual


def build_integrated_daily_state(
    *,
    water_result: dict[str, Any] | None = None,
    nutrition_result: dict[str, Any] | None = None,
    disease_result: dict[str, Any] | None = None,
    insect_result: dict[str, Any] | None = None,
    management_result: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build one daily state table from existing daily domain processes.

    The water or management daily rows are the biophysical base because they
    already contain daily soil-water dynamics, crop growth, LAI, biomass, and
    yield. Nutrition, disease, and insect blocks are joined by date and exposed
    as daily stress factors so all legacy endpoints share one daily state.
    """

    base_rows = _base_daily_rows(management_result, water_result, nutrition_result, disease_result, insect_result)
    if not base_rows:
        return []

    nutrition_by_day = _stress_by_day(nutrition_result)
    disease_by_day = _stress_by_day(disease_result)
    insect_by_day = _stress_by_day(insect_result)
    nutrition_field = _field_by_day(nutrition_result)
    disease_field = _field_by_day(disease_result)
    insect_field = _field_by_day(insect_result)

    integrated: list[dict[str, Any]] = []
    for base in base_rows:
        day = _date_key(base.get("Date"))
        if not day:
            continue
        row = deepcopy(base)
        row["Date"] = day

        water_status = _status(
            row.get("water_stress")
            or row.get("stress_risk")
            or row.get("field_status")
            or row.get("field_risk")
        )
        nutrient_statuses = nutrition_by_day.get(day, {})
        n_status = nutrient_statuses.get("N", row.get("n_stress_risk") or row.get("n_status"))
        p_status = nutrient_statuses.get("P2O5", row.get("p_stress_risk") or row.get("p_status") or row.get("p2o5_status"))
        k_status = nutrient_statuses.get("K2O", row.get("k_stress_risk") or row.get("k_status") or row.get("k2o_status"))
        disease_status = disease_field.get(day) or _worst_status(disease_by_day.get(day, {}).values())
        insect_status = insect_field.get(day) or _worst_status(insect_by_day.get(day, {}).values())

        water_factor = _float(row, "water_growth_factor", _factor(water_status))
        n_factor = _float(row, "n_growth_factor", _factor(n_status))
        p_factor = _float(row, "p_growth_factor", _factor(p_status))
        k_factor = _float(row, "k_growth_factor", _factor(k_status))
        nutrition_factor = min(n_factor, p_factor, k_factor)
        disease_factor = _factor(disease_status)
        insect_factor = _factor(insect_status)
        combined_factor = max(0.0, min(1.0, water_factor * nutrition_factor * disease_factor * insect_factor))
        potential_growth = _potential_growth(row)

        row.update(
            {
                "water_stress_risk": water_status,
                "n_stress_risk": _status(n_status),
                "p2o5_stress_risk": _status(p_status),
                "k2o_stress_risk": _status(k_status),
                "nutrition_stress_risk": _worst_status([n_status, p_status, k_status]),
                "disease_stress_risk": _status(disease_status),
                "insect_stress_risk": _status(insect_status),
                "water_growth_factor": round(water_factor, 4),
                "n_growth_factor": round(n_factor, 4),
                "p_growth_factor": round(p_factor, 4),
                "k_growth_factor": round(k_factor, 4),
                "nutrition_growth_factor": round(nutrition_factor, 4),
                "disease_growth_factor": round(disease_factor, 4),
                "insect_growth_factor": round(insect_factor, 4),
                "combined_growth_factor": round(combined_factor, 4),
                "potential_growth_kg_ha": round(potential_growth, 3),
                "integrated_actual_growth_kg_ha": round(potential_growth * combined_factor, 3),
                "layered_soil_water": row.get("soil_water_by_layer") or row.get("layered_soil_water") or [],
            }
        )
        integrated.append(row)
    return integrated


def _worst_status(values: Any) -> str:
    order = {"LOW": 0, "NOT_SEASONAL": 0, "IRRIGATED": 0, "PROTECTED": 0, "MEDIUM": 1, "WATCH": 1, "HIGH": 2, "DEFICIENT": 2}
    statuses = [_status(value) for value in values or []]
    if not statuses:
        return "LOW"
    return max(statuses, key=lambda value: order.get(value, 0))
