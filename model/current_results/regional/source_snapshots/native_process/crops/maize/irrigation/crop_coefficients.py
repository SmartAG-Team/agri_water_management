from __future__ import annotations

from .bbch_mapping import get_phase_parameters


def kc_for_bbch(bbch: int | str) -> float:
    return float(get_phase_parameters(bbch)["kc"])


def canopy_cover_from_lai(lai: float) -> float:
    return max(0.03, min(0.98, 1.0 - pow(2.718281828, -0.50 * max(0.0, lai))))
