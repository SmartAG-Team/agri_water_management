from __future__ import annotations


def estimate_soil_nutrient_supply(
    nutrient_key: str,
    soil_test: dict,
    layered_soil_water: list[dict],
    root_zone_relative_available_water: float,
    stage_access_factor: float,
) -> dict:
    if nutrient_key != "N":
        return {"available_supply": 0.0, "details": {"supported": False}}

    mineral_n = soil_test.get("mineral_n_kg_ha")
    if mineral_n is None and soil_test.get("alkali_hydrolyzable_n_mg_kg") is not None:
        mineral_n = float(soil_test["alkali_hydrolyzable_n_mg_kg"]) * 0.32
    if mineral_n is None:
        raise ValueError("soil_test.mineral_n_kg_ha or soil_test.alkali_hydrolyzable_n_mg_kg is required")

    if soil_test.get("organic_matter_g_kg") is None:
        raise ValueError("soil_test.organic_matter_g_kg is required")
    organic_matter = float(soil_test["organic_matter_g_kg"])
    mineralization = max(8.0, organic_matter * 1.35 * max(0.45, stage_access_factor))

    if layered_soil_water:
        top_layers = layered_soil_water[:2]
        missing = [idx for idx, item in enumerate(top_layers) if item.get("relative_available_water") is None]
        if missing:
            raise ValueError("layered_soil_water requires relative_available_water")
        layer_access = sum(float(item["relative_available_water"]) for item in top_layers) / max(len(top_layers), 1)
    else:
        layer_access = max(0.55, root_zone_relative_available_water)

    accessibility = max(0.35, min(1.0, 0.55 * root_zone_relative_available_water + 0.45 * layer_access))
    available_supply = (float(mineral_n) + mineralization) * accessibility
    return {
        "available_supply": round(available_supply, 3),
        "details": {
            "initial_available_n_kg_ha": round(float(mineral_n), 3),
            "mineralization_n_kg_ha": round(mineralization, 3),
            "accessibility_factor": round(accessibility, 4),
        },
    }
