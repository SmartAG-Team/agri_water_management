from __future__ import annotations

import re
from typing import Any


KG_HA_PER_KG_MU = 15.0
DEFAULT_COATED_RELEASE_DAYS = 45


def kg_ha_to_kg_mu(value: float) -> float:
    return round(max(0.0, float(value or 0.0)) / KG_HA_PER_KG_MU, 2)


def kg_mu_to_kg_ha(value: float) -> float:
    return round(max(0.0, float(value or 0.0)) * KG_HA_PER_KG_MU, 3)


def _ratio_from_text(text: str) -> tuple[float, float, float] | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*[:：-]\s*(\d+(?:\.\d+)?)\s*[:：-]\s*(\d+(?:\.\d+)?)", text)
    if not match:
        return None
    return tuple(float(match.group(index)) for index in (1, 2, 3))  # type: ignore[return-value]


def _is_urea_text(text: str) -> bool:
    lowered = text.strip().lower()
    return lowered in {"urea", "尿素"} or "尿素" in text or "urea" in lowered


def normalize_fertilizer_inventory(raw: list[Any] | None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for index, item in enumerate(raw or []):
        payload: dict[str, Any]
        if isinstance(item, str):
            payload = {"raw": item}
        elif isinstance(item, dict):
            payload = dict(item)
        else:
            continue

        raw_text = str(payload.get("ratio") or payload.get("type") or payload.get("name") or payload.get("raw") or "").strip()
        if not raw_text:
            continue

        available_kg_mu = payload.get("available_amount_kg_mu")
        if available_kg_mu is None and payload.get("available_amount_kg_ha") is not None:
            available_kg_mu = kg_ha_to_kg_mu(float(payload["available_amount_kg_ha"]))
        available_kg_ha = None if available_kg_mu is None else kg_mu_to_kg_ha(float(available_kg_mu))

        if _is_urea_text(raw_text):
            items.append(
                {
                    "id": f"urea_{index}",
                    "kind": "urea",
                    "label": "尿素",
                    "display_name": "尿素",
                    "nutrients": {"N": 0.46, "P2O5": 0.0, "K2O": 0.0},
                    "available_amount_kg_ha": available_kg_ha,
                    "available_amount_kg_mu": None if available_kg_mu is None else round(float(available_kg_mu), 2),
                    "release_type": payload.get("release_type") or "quick_release",
                    "release_days": int(payload.get("release_days") or 1),
                    "source": "农民已有肥",
                }
            )
            continue

        ratio = _ratio_from_text(raw_text)
        if ratio is None:
            continue
        n, p2o5, k2o = ratio
        ratio_label = f"{n:g}:{p2o5:g}:{k2o:g}"
        release_days = int(payload.get("release_days") or DEFAULT_COATED_RELEASE_DAYS)
        items.append(
            {
                "id": f"compound_{ratio_label.replace(':', '_')}_{index}",
                "kind": "compound",
                "label": ratio_label,
                "display_name": f"{ratio_label}复合肥",
                "nutrients": {"N": n / 100.0, "P2O5": p2o5 / 100.0, "K2O": k2o / 100.0},
                "available_amount_kg_ha": available_kg_ha,
                "available_amount_kg_mu": None if available_kg_mu is None else round(float(available_kg_mu), 2),
                "release_type": payload.get("release_type") or "coated_slow_release",
                "release_days": release_days,
                "source": "农民已有肥",
            }
        )
    return items


def product_plan_nutrients(product: dict[str, Any]) -> dict[str, float]:
    amount = float(product.get("amount_kg_ha", 0.0) or 0.0)
    nutrients = dict(product.get("nutrient_fractions") or product.get("nutrients") or {})
    if product.get("nutrients_kg_ha"):
        return {key: round(float(value or 0.0), 3) for key, value in dict(product["nutrients_kg_ha"]).items()}
    return {
        key: round(amount * float(nutrients.get(key, 0.0) or 0.0), 3)
        for key in ("N", "P2O5", "K2O")
    }
