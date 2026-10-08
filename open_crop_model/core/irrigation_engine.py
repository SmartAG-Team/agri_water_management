from __future__ import annotations

from typing import Any


def generate_irrigation_scenarios(
    decision_date,
    irrigation_specs: dict[str, Any],
    remaining_days: int,
    root_zone_relative_available_water: float,
    stress_risk: str,
) -> list[dict]:
    min_depth = irrigation_specs["min_depth_mm"]
    max_depth = irrigation_specs["max_depth_mm"]
    mid_depth = round((min_depth + max_depth) * 0.5, 1)
    scenarios = [{"name": "no_irrigation", "events": []}]

    day_offsets = [0, 1, 3, 5]
    if stress_risk == "LOW" and root_zone_relative_available_water > 0.7:
        day_offsets = [0, 3]
    for offset in day_offsets:
        if offset > remaining_days:
            continue
        for depth in (min_depth, mid_depth, max_depth):
            scenarios.append(
                {
                    "name": f"irrigate_d{offset}_{int(depth)}",
                    "events": [
                        {
                            "offset_days": offset,
                            "gross_depth_mm": depth,
                        }
                    ],
                }
            )
    return scenarios
