from __future__ import annotations


def grain_quality_hook(bbch: int, protein_target: float | None = None) -> dict:
    enabled = protein_target is not None and bbch >= 69 and bbch <= 77
    return {
        "enabled": enabled,
        "extra_n_kg_ha": 0.0 if not enabled else 8.0,
        "reason": "quality strategy placeholder" if enabled else "quality strategy not enabled",
    }
