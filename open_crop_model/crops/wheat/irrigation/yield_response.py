from __future__ import annotations

from .bbch_mapping import get_phase_parameters


def stress_weight_for_bbch(bbch: int) -> float:
    return float(get_phase_parameters(bbch)["stress_weight"])


def expected_yield_after_stress(target_yield_kg_ha: float, cumulative_penalty: float) -> float:
    return max(0.0, target_yield_kg_ha * max(0.25, 1.0 - cumulative_penalty))
