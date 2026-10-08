from __future__ import annotations

from datetime import date


def _normalize_dates(event_dates: list[date | str] | None) -> list[date]:
    normalized: list[date] = []
    for item in event_dates or []:
        if isinstance(item, date):
            normalized.append(item)
        elif item:
            normalized.append(date.fromisoformat(str(item)[:10]))
    normalized.sort()
    return normalized


def _fertilizer_facility_cost(event_count: int, fertilizer_event_labor_cost_cny_ha: float) -> float:
    if event_count <= 1 or fertilizer_event_labor_cost_cny_ha <= 0:
        return 0.0
    return fertilizer_event_labor_cost_cny_ha * 0.70 * sum(idx**1.22 for idx in range(1, max(0, event_count)))


def _fertilizer_cluster_cost(event_dates: list[date | str] | None, fertilizer_event_labor_cost_cny_ha: float) -> float:
    dates = _normalize_dates(event_dates)
    if len(dates) <= 1 or fertilizer_event_labor_cost_cny_ha <= 0:
        return 0.0
    total = 0.0
    for earlier, later in zip(dates, dates[1:]):
        gap_days = max(0, (later - earlier).days)
        if gap_days > 22:
            continue
        tightness = max(0.0, (23 - gap_days) / 22.0)
        total += fertilizer_event_labor_cost_cny_ha * 0.95 * tightness**1.40
    return total


def estimate_fertilizer_cost(
    product_plan: list[dict],
    product_prices: dict | None = None,
    fertilizer_event_labor_cost_cny_ha: float | None = None,
    event_count: int | None = None,
    event_dates: list[date | str] | None = None,
) -> dict:
    total_cost = 0.0
    details = []
    available = False

    for item in product_plan:
        product_name = item["product_name"]
        amount = float(item["amount_kg_ha"])
        price = None if not product_prices else product_prices.get(product_name)
        if price is None:
            details.append({"product_name": product_name, "cost_cny_ha": None})
            continue
        cost = amount * float(price)
        total_cost += cost
        available = True
        details.append({"product_name": product_name, "cost_cny_ha": round(cost, 3)})

    labor_cost = 0.0
    facility_cost = 0.0
    logistics_cost = 0.0
    if product_plan and fertilizer_event_labor_cost_cny_ha is not None:
        labor_cost = float(fertilizer_event_labor_cost_cny_ha)
        available = True
        effective_event_count = max(1, int(event_count or 1))
        facility_cost = _fertilizer_facility_cost(effective_event_count, labor_cost)
        logistics_cost = _fertilizer_cluster_cost(event_dates, labor_cost)

    return {
        "available": available,
        "material_cost_cny_ha": round(total_cost, 3) if available else None,
        "labor_cost_cny_ha": round(labor_cost, 3) if available else None,
        "facility_competition_cost_cny_ha": round(facility_cost, 3) if available else None,
        "clustered_operations_cost_cny_ha": round(logistics_cost, 3) if available else None,
        "total_cost_cny_ha": round(total_cost + labor_cost + facility_cost + logistics_cost, 3) if available else None,
        "details": details,
    }
