"""Deterministic climate-soil zone batches and scale-aware conditional accounting."""

from copy import deepcopy
import numpy as np
from core.hydrology.types import bounded
from .model import simulate_rotation, initial_water_state
from .groundwater import source_ledger, delayed_recharge


def aggregate_zones(zones):
    if not zones:
        raise ValueError("At least one zone required")
    if len({z["zone_id"] for z in zones}) != len(zones):
        raise ValueError("Duplicate zone IDs double-count area")
    area = sum(bounded(z["area_ha"], "area_ha", 1e-9) for z in zones)
    variables = set.union(*(set(z) for z in zones)) - {
        "zone_id",
        "area_ha",
        "pending_recharge_tail_mm",
    }
    out = {"area_ha": area}
    for variable in variables:
        if variable.endswith(("_mm", "_cny_ha", "_cny_ha_year")):
            if any(variable not in zone for zone in zones):
                raise ValueError("Missing zone flux prevents aggregation")
            out[variable] = (
                sum(
                    bounded(zone[variable], variable, -float("inf")) * zone["area_ha"]
                    for zone in zones
                )
                / area
            )
    if any("pending_recharge_tail_mm" in z for z in zones):
        if (
            any("pending_recharge_tail_mm" not in z for z in zones)
            or len({z.get("period_end_date") for z in zones}) != 1
        ):
            raise ValueError(
                "Recharge tails require a common end date and complete coverage"
            )
        tails = [z["pending_recharge_tail_mm"] for z in zones]
        if len({len(t) for t in tails}) != 1:
            raise ValueError("Recharge tail dates are not aligned")
        out["pending_recharge_tail_mm"] = [
            sum(
                bounded(z["pending_recharge_tail_mm"][i], "recharge_tail")
                * z["area_ha"]
                for z in zones
            )
            / area
            for i in range(len(tails[0]))
        ]
    if "irrigation_field_mm" in out:
        out["irrigation_volume_m3"] = out["irrigation_field_mm"] * area * 10
    return out


def simulate_zones(zones, parameter_zones, validation_status, *, purpose="policy"):
    if purpose not in {"policy", "software_test"}:
        raise ValueError("Purpose must be policy or software_test")
    if purpose == "policy":
        if (
            validation_status.get("V0") != "pass"
            or validation_status.get("V1") != "pass"
        ):
            raise ValueError(
                "Regional policy simulation requires V0 and independent V1 validation"
            )
        methods = {
            s["technology"]["method"]
            for z in zones
            for s in z["seasons"]
            if s["crop"] != "fallow"
        }
        if not methods <= set(validation_status.get("validated_methods", [])):
            raise ValueError(
                "Regional technology scope exceeds independently validated irrigation methods"
            )
    if len({z["zone_id"] for z in zones}) != len(zones):
        raise ValueError("Duplicate zone IDs")
    rows = []
    for zone in sorted(zones, key=lambda z: z["zone_id"]):
        area = bounded(zone["area_ha"], "area_ha", 1e-9)
        parameters = parameter_zones[zone["climate_soil_zone"]]
        results = simulate_rotation(
            zone["seasons"], parameters, initial_water_state(zone["seasons"][0])
        )
        daily = [day for result in results for day in result.daily]
        field = sum(r.summary["irrigation_field_mm"] for r in results)
        # Metered event withdrawals are authoritative. Zone conveyance is a
        # fallback only for field measurements with no event-level conversion.
        pump = 0.0
        zone_fraction = bounded(
            zone["conveyance_fraction"], "conveyance_fraction", 1e-9, 1
        )
        for season in zone["seasons"]:
            for event in season.get("irrigation_events", []):
                if event["measurement_location"] == "pump":
                    pump += event["amount_mm"]
                else:
                    fraction = event.get("conveyance_fraction")
                    pump += event["amount_mm"] / (
                        zone_fraction if fraction is None else fraction
                    )
        sources = source_ledger(
            irrigation_field_mm=field,
            area_ha=area,
            conveyance_fraction=zone_fraction,
            withdrawal_total_mm=pump,
            groundwater_fraction=zone["groundwater_fraction"],
        )
        recharge = delayed_recharge(
            [day["bottom_drainage_mm"] for day in daily],
            zone["recharge_kernel"],
            zone.get("previous_recharge_tail_mm", ()),
        )
        capillary = sum(day["capillary_rise_mm"] for day in daily)
        economic_values = {}
        if zone.get("economics"):
            from .economics import annual_cost, period_cost

            economics = zone["economics"]
            cost = annual_cost(withdrawal_m3_ha=pump * 10, **economics["cost"])
            cost_interpretation = cost.pop("interpretation")
            revenue = sum(
                r.summary["yield_kg_ha"]
                * bounded(economics["crop_prices_cny_kg"][s["crop"]], "crop_price")
                for s, r in zip(zone["seasons"], results)
                if s["crop"] != "fallow"
            )
            allocation = bounded(
                economics["annual_cost_allocation_fraction"],
                "annual_cost_allocation_fraction",
                0,
                1,
            )
            other = bounded(economics["other_cost_cny_ha"], "other_cost_cny_ha")
            economic_values = dict(
                net_return_cny_ha=revenue - period_cost(cost, allocation) - other,
                revenue_cny_ha=revenue,
                cost_interpretation=cost_interpretation,
                **cost,
            )
        rows.append(
            dict(
                zone_id=zone["zone_id"],
                area_ha=area,
                climate_soil_zone=zone["climate_soil_zone"],
                period_end_date=daily[-1]["date"],
                irrigation_field_mm=field,
                et_mm=sum(r.summary["et_mm"] for r in results),
                bottom_drainage_mm=sum(day["bottom_drainage_mm"] for day in daily),
                recharge_in_period_mm=sum(recharge["recharge_mm"]),
                pending_recharge_mm=recharge["pending_after_period_mm"],
                pending_recharge_tail_mm=recharge["pending_tail_mm"],
                capillary_rise_mm=capillary,
                agricultural_gw_balance_mm=sum(recharge["recharge_mm"])
                - sources["groundwater_withdrawal_mm"]
                - capillary,
                initial_storage_mm=results[0].summary["initial_storage_mm"],
                final_storage_mm=results[-1].summary["final_storage_mm"],
                crop_yield_kg_ha=[
                    {
                        "crop": s["crop"],
                        "end_date": s["end_date"],
                        "yield_kg_ha": r.summary["yield_kg_ha"],
                    }
                    for s, r in zip(zone["seasons"], results)
                    if s["crop"] != "fallow"
                ],
                **sources,
                **economic_values,
                evidence="validated_scope_conditional_scenario"
                if purpose == "policy"
                else "synthetic_or_unvalidated_software_scenario",
                interpretation="agricultural flux contribution with fixed external background; not total groundwater recovery or a causal policy effect",
            )
        )
    return rows


def compare_technologies(
    zones, technologies, parameter_zones, validation_status, *, purpose="policy"
):
    """Matched forcing and crop parameters; device boundary varies explicitly."""
    outputs = {}
    for name, technology in sorted(technologies.items()):
        inputs = deepcopy(zones)
        for zone in inputs:
            for season in zone["seasons"]:
                season["technology"] = deepcopy(technology)
        outputs[name] = simulate_zones(
            inputs, parameter_zones, validation_status, purpose=purpose
        )
    return outputs


def ranking_robustness(samples_by_method, *, higher_is_better):
    methods = sorted(samples_by_method)
    values = np.array([samples_by_method[m] for m in methods], dtype=float)
    if values.ndim != 2 or values.shape[1] == 0 or not np.isfinite(values).all():
        raise ValueError("Aligned finite joint samples required")
    best = values.max(axis=0) if higher_is_better else values.min(axis=0)
    # Ties share the probability; no hard-coded technology priority.
    win = np.isclose(values, best, rtol=1e-10, atol=1e-10)
    weights = win / win.sum(axis=0)
    return {name: float(weights[i].mean()) for i, name in enumerate(methods)}


def drought_goal_coverage(
    annual_rows, minimum_crop_yield, minimum_agricultural_gw_balance_mm
):
    results = {}
    for method in sorted({r["method"] for r in annual_rows}):
        rows = [
            r
            for r in annual_rows
            if r["method"] == method and r["drought_class"] == "dry"
        ]
        eligible = [
            r for r in rows if set(minimum_crop_yield) <= set(r["crop_yields_kg_ha"])
        ]
        passes = [
            all(
                r["crop_yields_kg_ha"][crop] >= target
                for crop, target in minimum_crop_yield.items()
            )
            and r["agricultural_gw_balance_mm"] >= minimum_agricultural_gw_balance_mm
            for r in eligible
        ]
        results[method] = {
            "n_dry_years": len(eligible),
            "goal_fraction": float(np.mean(passes)) if passes else None,
            "status": "conditional_scenario" if passes else "insufficient_evidence",
        }
    return results
