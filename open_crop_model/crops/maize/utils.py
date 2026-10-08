from typing import Dict, Any, List, Optional
from .config import IOWA_Stage_ORDER, IOWA_Stage_INDEX
import bisect
def _stage_to_index(stage: str) -> Optional[int]:
    if not stage:
        return None
    s = stage.strip().upper()
    if s in IOWA_Stage_INDEX:
        return IOWA_Stage_INDEX[s]
    # Clamp V17+ to V16+
    if s.startswith("V"):
        try:
            n = int(s[1:])
            if n <= 16:
                return IOWA_Stage_INDEX.get(f"V{n}")
            return IOWA_Stage_INDEX["V16+"]
        except Exception:
            return None
    return None

def _stage_in_range(stage: str, start_stage: str, end_stage: str) -> bool:
    si = _stage_to_index(stage)
    a = _stage_to_index(start_stage)
    b = _stage_to_index(end_stage)
    if si is None or a is None or b is None:
        return False
    return a <= si <= b

def _interp_piecewise_linear(xs: List[float], ys: List[float], x: float) -> float:
    if not xs or not ys or len(xs) != len(ys):
        raise ValueError("Invalid interpolation pairs.")
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    i = bisect.bisect_left(xs, x)
    x0, x1 = xs[i-1], xs[i]
    y0, y1 = ys[i-1], ys[i]
    t = (x - x0) / (x1 - x0) if x1 != x0 else 0.0
    return y0 + t * (y1 - y0)

def _get_stage_multiplier(cfg_global: Dict[str, Any], stage: str) -> float:
    cg = cfg_global.get("crop_growth_stage_correction_iowa", {})
    stages = cg.get("stages", [])
    infect = cg.get("infection", [])
    if not stages or not infect or len(stages) != len(infect):
        return 1.0
    s = stage.strip().upper()
    if s in stages:
        return float(infect[stages.index(s)])
    idx = _stage_to_index(s)
    if idx is None:
        return 0.0
    defined_idx = [(_stage_to_index(x), i) for i, x in enumerate(stages) if _stage_to_index(x) is not None]
    if not defined_idx:
        return 1.0
    closest = min(defined_idx, key=lambda t: abs(t[0] - idx))
    return float(infect[closest[1]])

def _get_variety_multiplier(cfg_global: Dict[str, Any], susceptibility_index: int) -> float:
    vs = cfg_global.get("variety_susceptibility_correction", {})
    xs = [float(x) for x in vs.get("susceptibility", [1, 5, 9])]
    ys = [float(y) for y in vs.get("infection", [0.8, 1.0, 1.2])]
    si = max(1, min(9, int(susceptibility_index)))
    return float(_interp_piecewise_linear(xs, ys, si))
