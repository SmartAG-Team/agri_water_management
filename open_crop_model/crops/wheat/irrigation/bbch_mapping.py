from __future__ import annotations

from .config import PHASES, PHASE_PARAMETER_CURVES, PROCESS_PHASES, PROCESS_PARAMETER_CURVES


def get_phase(bbch: int) -> str:
    for name, start, end in PHASES:
        if start <= bbch <= end:
            return name
    return "maturity"


def get_phase_parameters(bbch: int) -> dict:
    return {
        "kc": _interp_curve(PHASE_PARAMETER_CURVES["kc"], bbch),
        "stress_weight": _interp_curve(PHASE_PARAMETER_CURVES["stress_weight"], bbch),
    }


def get_process_phase(bbch: int) -> str:
    for name, start, end in PROCESS_PHASES:
        if start <= bbch <= end:
            return name
    return "maturity"


def get_process_phase_progress(bbch: int) -> float:
    for _, start, end in PROCESS_PHASES:
        if start <= bbch <= end:
            span = max(1, end - start)
            return max(0.0, min(1.0, (bbch - start) / span))
    return 1.0


def _interp_curve(points: list[tuple[int, float]], bbch: int) -> float:
    if bbch <= points[0][0]:
        return float(points[0][1])
    if bbch >= points[-1][0]:
        return float(points[-1][1])
    for idx in range(len(points) - 1):
        left_x, left_y = points[idx]
        right_x, right_y = points[idx + 1]
        if left_x <= bbch <= right_x:
            frac = (bbch - left_x) / max(right_x - left_x, 1)
            return float(left_y) + (float(right_y) - float(left_y)) * frac
    return float(points[-1][1])


def interpolate_process_parameters(bbch: int) -> dict:
    interpolated = {}
    for key in (
        "rue_g_mj",
        "sla_m2_kg",
        "leaf_senescence_fraction",
        "stem_senescence_fraction",
        "spike_senescence_fraction",
        "root_extension_mm_per_day",
        "max_root_depth_mm",
        "dm_remobilization_fraction",
        "n_remobilization_fraction",
    ):
        interpolated[key] = _interp_curve(PROCESS_PARAMETER_CURVES[key], bbch)
    interpolated["partition"] = {}
    for key in ("leaf", "stem", "spike", "grain", "root"):
        interpolated["partition"][key] = _interp_curve(PROCESS_PARAMETER_CURVES["partition"][key], bbch)
    return interpolated
