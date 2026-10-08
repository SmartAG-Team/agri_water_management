from __future__ import annotations

from numbers import Real

from crops.maize.config import IOWA_STAGE_BBCH, IOWA_Stage_INDEX, IOWA_Stage_ORDER
from crops.maize.growth_config import (
    CURVE_ANCHOR_STAGE,
    PHASE_PARAMETER_CURVES,
    PHASES,
    PROCESS_PARAMETER_CURVES,
    PROCESS_PHASES,
)


_BBCH_STAGE_ORDER = sorted(
    ((int(IOWA_STAGE_BBCH[stage]), stage) for stage in IOWA_Stage_ORDER),
    key=lambda item: (item[0], IOWA_Stage_INDEX[item[1]]),
)


def normalize_iowa_stage(stage_value: int | float | str) -> str:
    """Return a configured Iowa stage for an Iowa label or canonical BBCH value."""

    if isinstance(stage_value, str):
        stage = stage_value.strip().upper()
        if stage in IOWA_Stage_INDEX:
            return stage
        try:
            numeric_value = float(stage)
        except ValueError as exc:
            raise ValueError(f"unknown maize Iowa stage: {stage_value!r}") from exc
    elif isinstance(stage_value, Real):
        numeric_value = float(stage_value)
    else:
        raise ValueError(f"unknown maize Iowa stage: {stage_value!r}")

    selected = IOWA_Stage_ORDER[0]
    for bbch, stage in _BBCH_STAGE_ORDER:
        if numeric_value < bbch:
            break
        selected = stage
    return selected


def stage_rank(stage_value: int | float | str) -> int:
    return IOWA_Stage_INDEX[normalize_iowa_stage(stage_value)]


def stage_between(
    stage_value: int | float | str,
    minimum_stage: str,
    maximum_stage: str,
) -> bool:
    rank = stage_rank(stage_value)
    return IOWA_Stage_INDEX[minimum_stage] <= rank <= IOWA_Stage_INDEX[maximum_stage]


def stage_progress(stage_value: int | float | str) -> float:
    return stage_rank(stage_value) / max(len(IOWA_Stage_ORDER) - 1, 1)


def stage_to_bbch(stage_value: int | float | str) -> int:
    return int(IOWA_STAGE_BBCH[normalize_iowa_stage(stage_value)])


def stage_to_code(stage_value: int | float | str) -> int:
    """Deprecated compatibility alias for :func:`stage_to_bbch`."""

    return stage_to_bbch(stage_value)


def _phase_for_stage(stage_value: int | float | str, phases: list[tuple[str, str, str]]) -> str:
    rank = stage_rank(stage_value)
    for name, start, end in phases:
        if IOWA_Stage_INDEX[start] <= rank <= IOWA_Stage_INDEX[end]:
            return name
    raise ValueError(f"no maize phase configured for {stage_value!r}")


def get_phase(stage_value: int | float | str) -> str:
    return _phase_for_stage(stage_value, PHASES)


def get_phase_parameters(stage_value: int | float | str) -> dict[str, float]:
    return {
        "kc": _interp_curve(PHASE_PARAMETER_CURVES["kc"], stage_value),
        "stress_weight": _interp_curve(PHASE_PARAMETER_CURVES["stress_weight"], stage_value),
    }


def get_process_phase(stage_value: int | float | str) -> str:
    return _phase_for_stage(stage_value, PROCESS_PHASES)


def get_process_phase_progress(stage_value: int | float | str) -> float:
    rank = stage_rank(stage_value)
    for _, start, end in PROCESS_PHASES:
        start_rank = IOWA_Stage_INDEX[start]
        end_rank = IOWA_Stage_INDEX[end]
        if start_rank <= rank <= end_rank:
            return (rank - start_rank) / max(end_rank - start_rank, 1)
    raise ValueError(f"no maize process phase configured for {stage_value!r}")


def _curve_anchor_rank(anchor: int | str) -> int:
    stage = CURVE_ANCHOR_STAGE[int(anchor)] if isinstance(anchor, Real) else str(anchor)
    return IOWA_Stage_INDEX[stage]


def _interp_curve(
    points: list[tuple[int | str, float]],
    stage_value: int | float | str,
) -> float:
    rank = stage_rank(stage_value)
    ranked_points = [(_curve_anchor_rank(anchor), float(value)) for anchor, value in points]
    if rank <= ranked_points[0][0]:
        return ranked_points[0][1]
    if rank >= ranked_points[-1][0]:
        return ranked_points[-1][1]
    for (left_rank, left_value), (right_rank, right_value) in zip(ranked_points, ranked_points[1:]):
        if left_rank <= rank <= right_rank:
            fraction = (rank - left_rank) / max(right_rank - left_rank, 1)
            return left_value + (right_value - left_value) * fraction
    return ranked_points[-1][1]


def interpolate_process_parameters(stage_value: int | float | str) -> dict:
    interpolated = {}
    for key in (
        "rue_g_mj",
        "sla_m2_kg",
        "leaf_senescence_fraction",
        "stem_senescence_fraction",
        "ear_senescence_fraction",
        "root_extension_mm_per_day",
        "max_root_depth_mm",
        "dm_remobilization_fraction",
        "n_remobilization_fraction",
    ):
        interpolated[key] = _interp_curve(PROCESS_PARAMETER_CURVES[key], stage_value)
    interpolated["partition"] = {}
    for key in ("leaf", "stem", "ear", "grain", "root"):
        interpolated["partition"][key] = _interp_curve(
            PROCESS_PARAMETER_CURVES["partition"][key],
            stage_value,
        )
    return interpolated
