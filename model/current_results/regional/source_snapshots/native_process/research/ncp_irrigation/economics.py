"""Engineering cost scenarios; all costs and equipment properties are explicit."""

from core.hydrology.types import bounded


def capital_recovery_factor(life_years, discount_rate):
    bounded(life_years, "life_years", 1e-9)
    bounded(discount_rate, "discount_rate", 0, 1)
    if discount_rate == 0:
        return 1 / life_years
    # Stable for small positive rates without overflow for long lifetimes.
    from math import expm1, log1p

    return discount_rate / (-expm1(-life_years * log1p(discount_rate)))


def annualized_capital(capital_cny_ha, life_years, discount_rate):
    bounded(capital_cny_ha, "capital_cny_ha")
    return capital_cny_ha * capital_recovery_factor(life_years, discount_rate)


def pumping_energy_kwh(volume_m3, lift_m, pressure_head_m, pump_efficiency):
    bounded(volume_m3, "volume_m3")
    bounded(lift_m, "lift_m")
    bounded(pressure_head_m, "pressure_head_m")
    bounded(pump_efficiency, "pump_efficiency", 1e-9, 1)
    return (
        1000 * 9.81 * volume_m3 * (lift_m + pressure_head_m) / (pump_efficiency * 3.6e6)
    )


def annual_cost(
    *,
    capital_cny_ha,
    life_years,
    discount_rate,
    maintenance_cny_ha,
    labor_cny_ha,
    withdrawal_m3_ha,
    lift_m,
    pressure_head_m,
    pump_efficiency,
    water_price_cny_m3,
    electricity_price_cny_kwh,
    water_price_includes_energy=False,
):
    for name, value in [
        ("maintenance", maintenance_cny_ha),
        ("labor", labor_cny_ha),
        ("water price", water_price_cny_m3),
        ("electricity price", electricity_price_cny_kwh),
    ]:
        bounded(value, name)
    if water_price_includes_energy and electricity_price_cny_kwh != 0:
        raise ValueError("Water price includes energy: electricity charge must be zero")
    capital = annualized_capital(capital_cny_ha, life_years, discount_rate)
    energy = pumping_energy_kwh(
        withdrawal_m3_ha, lift_m, pressure_head_m, pump_efficiency
    )
    water = withdrawal_m3_ha * water_price_cny_m3
    electricity = energy * electricity_price_cny_kwh
    return dict(
        capital_cny_ha_year=capital,
        maintenance_cny_ha_year=maintenance_cny_ha,
        labor_cny_ha_year=labor_cny_ha,
        water_cny_ha_year=water,
        electricity_cny_ha_year=electricity,
        energy_kwh_ha_year=energy,
        total_cny_ha_year=capital
        + maintenance_cny_ha
        + labor_cny_ha
        + water
        + electricity,
        interpretation="conditional explicit-price scenario, not verified technology cost",
    )


def break_even_capital(
    annual_incremental_revenue_cny_ha,
    annual_incremental_running_cost_cny_ha,
    life_years,
    discount_rate,
):
    from math import isfinite

    if not isfinite(annual_incremental_revenue_cny_ha) or not isfinite(
        annual_incremental_running_cost_cny_ha
    ):
        raise ValueError("Finite incremental cost/revenue required")
    surplus = annual_incremental_revenue_cny_ha - annual_incremental_running_cost_cny_ha
    return dict(
        max_viable_capital_cny_ha=max(0.0, surplus)
        / capital_recovery_factor(life_years, discount_rate),
        annual_surplus_cny_ha=surplus,
        status="viable_below_threshold"
        if surplus >= 0
        else "annual_operating_subsidy_required",
    )


def period_cost(cost, annual_cost_allocation_fraction):
    """Allocate annual fixed costs; metered period water/energy are already dated."""
    fraction = bounded(
        annual_cost_allocation_fraction, "annual_cost_allocation_fraction", 0, 1
    )
    fixed = sum(
        cost[key]
        for key in [
            "capital_cny_ha_year",
            "maintenance_cny_ha_year",
            "labor_cny_ha_year",
        ]
    )
    return (
        fixed * fraction + cost["water_cny_ha_year"] + cost["electricity_cny_ha_year"]
    )
