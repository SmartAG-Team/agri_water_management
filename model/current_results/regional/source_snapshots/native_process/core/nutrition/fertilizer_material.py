from __future__ import annotations

from copy import deepcopy


DEFAULT_FERTILIZER_MATERIALS = {
    "urea": {"product_name": "urea", "nutrients": {"N": 0.46}},
    "compound_fertilizer": {"product_name": "compound_fertilizer", "nutrients": {"N": 0.15, "P2O5": 0.15, "K2O": 0.15}},
    "diammonium_phosphate": {"product_name": "diammonium_phosphate", "nutrients": {"N": 0.18, "P2O5": 0.46}},
    "monoammonium_phosphate": {"product_name": "monoammonium_phosphate", "nutrients": {"N": 0.11, "P2O5": 0.52}},
    "potassium_chloride": {"product_name": "potassium_chloride", "nutrients": {"K2O": 0.60}},
    "potassium_sulfate": {"product_name": "potassium_sulfate", "nutrients": {"K2O": 0.50, "S": 0.18}},
    "ammonium_sulfate": {"product_name": "ammonium_sulfate", "nutrients": {"N": 0.21, "S": 0.24}},
    "foliar_kh2po4": {"product_name": "foliar_kh2po4", "nutrients": {"P2O5": 0.52, "K2O": 0.34}},
}


def get_fertilizer_material(product_name: str, custom_products: list[dict] | None = None) -> dict | None:
    materials = deepcopy(DEFAULT_FERTILIZER_MATERIALS)
    for item in custom_products or []:
        name = item.get("product_name")
        if name:
            materials[name] = {"product_name": name, "nutrients": dict(item.get("nutrients") or {})}
    return materials.get(product_name)


def product_to_nutrients(product_name: str, amount_kg_ha: float, custom_products: list[dict] | None = None) -> dict:
    material = get_fertilizer_material(product_name, custom_products)
    if not material:
        return {}
    return {k: round(amount_kg_ha * float(v), 3) for k, v in (material.get("nutrients") or {}).items()}


def nutrient_to_product_amount(product_name: str, nutrient_key: str, nutrient_amount: float, custom_products: list[dict] | None = None) -> float | None:
    material = get_fertilizer_material(product_name, custom_products)
    if not material:
        return None
    fraction = float((material.get("nutrients") or {}).get(nutrient_key, 0.0))
    if fraction <= 0:
        return None
    return round(nutrient_amount / fraction, 3)
