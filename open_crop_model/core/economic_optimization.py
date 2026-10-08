from __future__ import annotations

from datetime import date

DEFAULT_PRODUCT_PRICES_CNY_PER_KG = {
    "urea": 2.6,
    "compound_fertilizer": 3.1,
}


def _normalize_event_dates(event_dates: list[date | str] | None) -> list[date]:
    normalized: list[date] = []
    for item in event_dates or []:
        if isinstance(item, date):
            normalized.append(item)
        elif item:
            normalized.append(date.fromisoformat(str(item)[:10]))
    normalized.sort()
    return normalized


def _facility_competition_cost(event_count: int, irrigation_event_labor_cost_cny_ha: float) -> float:
    if event_count <= 1:
        return 0.0
    return irrigation_event_labor_cost_cny_ha * 0.85 * sum(idx**1.35 for idx in range(1, max(0, event_count)))


def _travel_setup_cost(event_count: int, irrigation_event_labor_cost_cny_ha: float) -> float:
    if event_count <= 0:
        return 0.0
    return max(0, event_count) * irrigation_event_labor_cost_cny_ha * 1.45


def _manual_monitoring_control_cost(event_count: int, irrigation_event_labor_cost_cny_ha: float) -> float:
    if event_count <= 0:
        return 0.0
    # The provided labor input is treated as direct field labor only.
    # Low-automation irrigation usually requires additional time to monitor
    # progress and manually adjust/control water delivery during each event.
    return max(0, event_count) * irrigation_event_labor_cost_cny_ha * 1.75


def _clustered_event_cost(
    event_dates: list[date | str] | None,
    labor_cost_cny_ha: float,
    *,
    cluster_window_days: int,
    cluster_scale: float,
) -> float:
    dates = _normalize_event_dates(event_dates)
    if len(dates) <= 1 or labor_cost_cny_ha <= 0:
        return 0.0
    total = 0.0
    for earlier, later in zip(dates, dates[1:]):
        gap_days = max(0, (later - earlier).days)
        if gap_days > cluster_window_days:
            continue
        tightness = max(0.0, (cluster_window_days + 1 - gap_days) / max(cluster_window_days, 1))
        total += labor_cost_cny_ha * cluster_scale * tightness**1.45
    return total


def irrigation_activity_cost_breakdown(
    *,
    total_irrigation_mm: float,
    event_count: int,
    electricity_cost_cny_per_kwh: float,
    pump_kwh_per_mm_ha: float,
    irrigation_event_labor_cost_cny_ha: float,
    water_cost_cny_per_mm_ha: float = 0.0,
    event_dates: list[date | str] | None = None,
) -> dict:
    pumping_cost = total_irrigation_mm * pump_kwh_per_mm_ha * electricity_cost_cny_per_kwh
    water_cost = total_irrigation_mm * max(0.0, float(water_cost_cny_per_mm_ha or 0.0))
    irrigation_labor_cost = max(0, event_count) * irrigation_event_labor_cost_cny_ha
    manual_monitoring_cost = _manual_monitoring_control_cost(max(0, event_count), irrigation_event_labor_cost_cny_ha)
    travel_setup_cost = _travel_setup_cost(max(0, event_count), irrigation_event_labor_cost_cny_ha)
    facility_competition_cost = _facility_competition_cost(max(0, event_count), irrigation_event_labor_cost_cny_ha)
    clustered_operations_cost = _clustered_event_cost(
        event_dates,
        irrigation_event_labor_cost_cny_ha,
        cluster_window_days=16,
        cluster_scale=1.35,
    )
    derived_logistics_overhead = manual_monitoring_cost + travel_setup_cost + facility_competition_cost + clustered_operations_cost
    return {
        "pumping_cost_cny_ha": round(pumping_cost, 3),
        "water_cost_cny_ha": round(water_cost, 3),
        "direct_labor_cost_cny_ha": round(irrigation_labor_cost, 3),
        "irrigation_labor_cost_cny_ha": round(irrigation_labor_cost, 3),
        "manual_monitoring_control_cost_cny_ha": round(manual_monitoring_cost, 3),
        "travel_setup_cost_cny_ha": round(travel_setup_cost, 3),
        "facility_competition_cost_cny_ha": round(facility_competition_cost, 3),
        "clustered_operations_cost_cny_ha": round(clustered_operations_cost, 3),
        "derived_logistics_overhead_cny_ha": round(derived_logistics_overhead, 3),
        "irrigation_operating_cost_cny_ha": round(
            pumping_cost + water_cost + irrigation_labor_cost + derived_logistics_overhead,
            3,
        ),
    }


def irrigation_n_leaching_cost_breakdown(
    *,
    irrigation_attributable_n_leached_kg_ha: float,
    grain_price_cny_per_kg: float,
    product_prices: dict | None = None,
    yield_value_loss_cny_per_kg_n: float | None = None,
) -> dict:
    n_leached = max(0.0, float(irrigation_attributable_n_leached_kg_ha) or 0.0)
    prices = dict(DEFAULT_PRODUCT_PRICES_CNY_PER_KG)
    prices.update(product_prices or {})
    urea_price = float(prices.get("urea", DEFAULT_PRODUCT_PRICES_CNY_PER_KG["urea"]) or DEFAULT_PRODUCT_PRICES_CNY_PER_KG["urea"])
    replacement_cost = n_leached / 0.46 * urea_price if n_leached > 0 else 0.0
    if yield_value_loss_cny_per_kg_n is None:
        yield_value_loss_cny_per_kg_n = float(grain_price_cny_per_kg or 0.0) * 8.5
    yield_value_loss = n_leached * max(0.0, float(yield_value_loss_cny_per_kg_n) or 0.0)
    total = replacement_cost + yield_value_loss
    return {
        "irrigation_attributable_n_leached_kg_ha": round(n_leached, 3),
        "n_leaching_replacement_cost_cny_ha": round(replacement_cost, 3),
        "n_leaching_yield_value_cost_cny_ha": round(yield_value_loss, 3),
        "n_leaching_total_cost_cny_ha": round(total, 3),
    }


def score_scenario(
    expected_yield_kg_ha: float,
    grain_price_cny_per_kg: float,
    total_irrigation_mm: float,
    event_count: int,
    electricity_cost_cny_per_kwh: float,
    pump_kwh_per_mm_ha: float,
    irrigation_event_labor_cost_cny_ha: float,
    water_cost_cny_per_mm_ha: float = 0.0,
    event_dates: list[date | str] | None = None,
    irrigation_attributable_n_leached_kg_ha: float = 0.0,
    product_prices: dict | None = None,
    yield_value_loss_cny_per_kg_n: float | None = None,
) -> dict:
    revenue = expected_yield_kg_ha * grain_price_cny_per_kg
    cost_breakdown = irrigation_activity_cost_breakdown(
        total_irrigation_mm=total_irrigation_mm,
        event_count=event_count,
        electricity_cost_cny_per_kwh=electricity_cost_cny_per_kwh,
        pump_kwh_per_mm_ha=pump_kwh_per_mm_ha,
        irrigation_event_labor_cost_cny_ha=irrigation_event_labor_cost_cny_ha,
        water_cost_cny_per_mm_ha=water_cost_cny_per_mm_ha,
        event_dates=event_dates,
    )
    n_leaching_cost = irrigation_n_leaching_cost_breakdown(
        irrigation_attributable_n_leached_kg_ha=irrigation_attributable_n_leached_kg_ha,
        grain_price_cny_per_kg=grain_price_cny_per_kg,
        product_prices=product_prices,
        yield_value_loss_cny_per_kg_n=yield_value_loss_cny_per_kg_n,
    )
    net_return = revenue - cost_breakdown["irrigation_operating_cost_cny_ha"] - n_leaching_cost["n_leaching_total_cost_cny_ha"]
    return {
        "expected_yield_kg_ha": round(expected_yield_kg_ha, 3),
        "gross_revenue_cny_ha": round(revenue, 3),
        **cost_breakdown,
        **n_leaching_cost,
        "expected_net_return_cny_ha": round(net_return, 3),
    }
