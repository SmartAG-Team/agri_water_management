from __future__ import annotations

from copy import deepcopy


def rank_scenarios(scenarios: list[dict]) -> list[dict]:
    ordered = sorted(
        scenarios,
        key=lambda x: (
            x["economics"]["expected_net_return_cny_ha"],
            -x["summary"]["final_stress_score"],
            x["summary"]["total_irrigation_mm"],
        ),
        reverse=True,
    )
    for idx, item in enumerate(ordered, start=1):
        item["rank"] = idx
    return ordered
