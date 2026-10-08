from __future__ import annotations

from copy import deepcopy
from datetime import timedelta

from core.nutrition import (
    diagnose_nutrient_status,
    estimate_effective_remaining_nutrients,
    estimate_soil_nutrient_supply,
    normalize_nutrition_request,
    recommend_fertilization,
)
from core.nutrition.fertilizer_inventory import kg_ha_to_kg_mu, product_plan_nutrients
from core.nutrition.fertilizer_material import product_to_nutrients

from crops.wheat.management.nutrition import build_fertilizer_recommendation_block, build_season_fertilization_actions
from crops.maize.irrigation.bbch_mapping import (
    normalize_iowa_stage,
    stage_between,
    stage_rank,
    stage_to_bbch,
)
from crops.maize.nutrition.config import REGIONAL_SOIL_TEST_DEFAULTS


ZHENGDING_LAT = 38.1478
ZHENGDING_LON = 114.5704
ZHENGDING_OLSEN_P_MG_KG = REGIONAL_SOIL_TEST_DEFAULTS["zhengding_olsen_p_mg_kg"]
SHIJIAZHUANG_OLSEN_P_MG_KG = REGIONAL_SOIL_TEST_DEFAULTS["shijiazhuang_olsen_p_mg_kg"]
NORTH_CHINA_PLAIN_AVAILABLE_K_MG_KG = REGIONAL_SOIL_TEST_DEFAULTS["north_china_plain_available_k_mg_kg"]
PK_BASAL_PLACEMENT_GUIDANCE_CN = [
    "磷肥移动性极差，后期地表追施很难进入15-30厘米玉米根系层，建议播种时深施作底肥。",
    "玉米苗期和拔节期对磷敏感，后期补磷容易错过根系和叶片建成关键期。",
    "钾肥在土壤中移动性弱，优先底肥深施；后期明显缺钾时只建议少量叶面喷施磷酸二氢钾作应急补救。",
]


def _phase_for_stage(stage: int | str) -> dict:
    stage = normalize_iowa_stage(stage)
    if stage_between(stage, "VS", "VE"):
        return {"phase_name": "establishment", "uptake_fraction": 0.04, "stage_window_fraction": 0.10, "access_factor": 0.45}
    if stage_between(stage, "V1", "V5"):
        return {"phase_name": "early_vegetative", "uptake_fraction": 0.14, "stage_window_fraction": 0.18, "access_factor": 0.60}
    if stage_between(stage, "V6", "V12"):
        return {"phase_name": "rapid_vegetative", "uptake_fraction": 0.46, "stage_window_fraction": 0.30, "access_factor": 0.82}
    if stage_between(stage, "V13", "V16+"):
        return {"phase_name": "pre_tassel", "uptake_fraction": 0.60, "stage_window_fraction": 0.26, "access_factor": 0.88}
    if stage_between(stage, "VT", "R1"):
        return {"phase_name": "tasseling_silking", "uptake_fraction": 0.72, "stage_window_fraction": 0.22, "access_factor": 0.92}
    if stage_between(stage, "R2", "R4"):
        return {"phase_name": "kernel_development", "uptake_fraction": 0.92, "stage_window_fraction": 0.14, "access_factor": 0.95}
    if stage == "R5":
        return {"phase_name": "grain_filling", "uptake_fraction": 0.96, "stage_window_fraction": 0.08, "access_factor": 0.92}
    return {"phase_name": "maturity", "uptake_fraction": 1.00, "stage_window_fraction": 0.02, "access_factor": 0.80}


def _maize_stage_rules(stage: int | str, irrigation_method: str) -> dict:
    stage = normalize_iowa_stage(stage)
    if stage_between(stage, "VS", "VE"):
        return {
            "max_single_n_rate_kg_ha": 110.0,
            "hard_max_single_n_rate_kg_ha": 150.0,
            "min_effective_n_rate_kg_ha": 35.0,
            "default_method": "basal_incorporated",
            "default_n_product": "urea",
            "coverage_multiplier": 1.5,
            "practical_gap_kg_ha": 25.0,
            "yield_gain_kg_grain_per_kg_n": 12.0,
            "rainfall_activation_mm": 6.0,
            "heavy_rain_risk_mm": 30.0,
            "minimum_surface_water_for_broadcast": 0.28,
            "application_window_days": 3,
            "base_reasons": ["pre-plant or sowing stage requires basal nitrogen placement for early stand establishment"],
        }
    if stage_between(stage, "V1", "V7"):
        return {
            "max_single_n_rate_kg_ha": 90.0,
            "hard_max_single_n_rate_kg_ha": 120.0,
            "min_effective_n_rate_kg_ha": 25.0,
            "default_method": "topdress_before_irrigation",
            "default_n_product": "urea",
            "coverage_multiplier": 1.5,
            "practical_gap_kg_ha": 20.0,
            "yield_gain_kg_grain_per_kg_n": 10.0,
            "rainfall_activation_mm": 8.0,
            "heavy_rain_risk_mm": 35.0,
            "minimum_surface_water_for_broadcast": 0.30,
            "application_window_days": 3,
            "base_reasons": ["rapid vegetative growth is beginning and nitrogen demand is rising"],
        }
    if stage_between(stage, "V8", "R1"):
        return {
            "max_single_n_rate_kg_ha": 75.0,
            "hard_max_single_n_rate_kg_ha": 95.0,
            "min_effective_n_rate_kg_ha": 20.0,
            "default_method": "topdress_before_irrigation",
            "default_n_product": "urea",
            "coverage_multiplier": 1.4,
            "practical_gap_kg_ha": 18.0,
            "yield_gain_kg_grain_per_kg_n": 9.0,
            "rainfall_activation_mm": 8.0,
            "heavy_rain_risk_mm": 35.0,
            "minimum_surface_water_for_broadcast": 0.32,
            "application_window_days": 3,
            "base_reasons": ["pre-tassel to silking is a critical nitrogen demand period for maize"],
        }
    return {
        "max_single_n_rate_kg_ha": 35.0,
        "hard_max_single_n_rate_kg_ha": 45.0,
        "min_effective_n_rate_kg_ha": 15.0,
        "default_method": "topdress_before_irrigation",
        "default_n_product": "urea",
        "coverage_multiplier": 1.2,
        "practical_gap_kg_ha": 15.0,
        "yield_gain_kg_grain_per_kg_n": 5.0,
        "rainfall_activation_mm": 8.0,
        "heavy_rain_risk_mm": 30.0,
        "minimum_surface_water_for_broadcast": 0.35,
        "application_window_days": 2,
        "base_reasons": ["late nitrogen can still support grain filling when demand remains and economics justify it"],
    }


def _is_zhengding_area(latitude: float | None, longitude: float | None) -> bool:
    if latitude is None or longitude is None:
        return False
    return abs(float(latitude) - ZHENGDING_LAT) <= 0.35 and abs(float(longitude) - ZHENGDING_LON) <= 0.35


def _is_hebei_or_ncp(latitude: float | None, longitude: float | None) -> bool:
    if latitude is None or longitude is None:
        return True
    lat = float(latitude)
    lon = float(longitude)
    return 36.0 <= lat <= 42.5 and 113.0 <= lon <= 119.5


def _available_k(soil_test: dict) -> float | None:
    value = soil_test.get("available_k_mg_kg")
    if value is None:
        value = soil_test.get("exchangeable_k_mg_kg")
    return None if value is None else float(value)


def _enrich_hebei_pk_defaults(normalized: dict) -> dict:
    soil_test = normalized["soil_test"]
    assumptions = normalized["assumptions"]
    latitude = normalized.get("latitude")
    longitude = normalized.get("longitude")
    if soil_test.get("olsen_p_mg_kg") is None:
        if _is_zhengding_area(latitude, longitude):
            soil_test["olsen_p_mg_kg"] = ZHENGDING_OLSEN_P_MG_KG
            assumptions.append("soil_test.olsen_p_mg_kg missing; Zhengding Qinjiazhuang 0-20 cm default 27.5 mg/kg used")
        elif _is_hebei_or_ncp(latitude, longitude):
            soil_test["olsen_p_mg_kg"] = SHIJIAZHUANG_OLSEN_P_MG_KG
            assumptions.append("soil_test.olsen_p_mg_kg missing; Shijiazhuang cultivated land mean 28.54 mg/kg used")
    if _available_k(soil_test) is None and _is_hebei_or_ncp(latitude, longitude):
        soil_test["available_k_mg_kg"] = NORTH_CHINA_PLAIN_AVAILABLE_K_MG_KG
        assumptions.append("soil_test.available_k_mg_kg missing; North China Plain default 176.2 mg/kg used")
    return soil_test


def _pk_status(value: float | None, nutrient_key: str) -> str:
    if value is None:
        return "unknown"
    if nutrient_key == "P2O5":
        if value < 15.0:
            return "deficient"
        if value < 26.9:
            return "slightly_deficient"
        if value <= 40.0:
            return "adequate"
        return "excessive"
    if value < 100.0:
        return "deficient"
    if value < 120.0:
        return "slightly_deficient"
    if value <= 220.0:
        return "adequate"
    return "excessive"


def _soil_factor_for_pk(value: float | None, nutrient_key: str) -> float:
    if value is None:
        return 1.0
    if nutrient_key == "P2O5":
        if value < 15.0:
            return 1.35
        if value < 26.9:
            return 1.10
        if value <= 30.0:
            return 0.75
        if value <= 40.0:
            return 0.55
        return 0.25
    if value < 100.0:
        return 1.25
    if value < 120.0:
        return 1.00
    if value <= 220.0:
        return 0.75
    return 0.45


def _estimate_maize_pk_plan(normalized: dict) -> dict:
    soil_test = _enrich_hebei_pk_defaults(normalized)
    target_yield_t = float(normalized["target_yield_kg_ha"]) / 1000.0
    olsen_p = None if soil_test.get("olsen_p_mg_kg") is None else float(soil_test["olsen_p_mg_kg"])
    available_k = _available_k(soil_test)
    p_status = _pk_status(olsen_p, "P2O5")
    k_status = _pk_status(available_k, "K2O")

    # North China summer maize guidance is close to 28-6-9 compound fertilizer.
    # Convert that regional band to target-yield-scaled nutrient rates, then
    # reduce or increase by local soil-test status.
    p2o5_base = target_yield_t * 5.0
    k2o_base = target_yield_t * 7.0
    p2o5_rate = round(max(0.0, min(90.0, p2o5_base * _soil_factor_for_pk(olsen_p, "P2O5"))), 3)
    k2o_rate = round(max(0.0, min(120.0, k2o_base * _soil_factor_for_pk(available_k, "K2O"))), 3)

    reasons = []
    if olsen_p is not None:
        reasons.append(f"Olsen P {olsen_p:.1f} mg/kg gives P status {p_status}")
    if available_k is not None:
        reasons.append(f"available K {available_k:.1f} mg/kg gives K status {k_status}")
    if p_status == "excessive":
        reasons.append("soil Olsen P is high; only a small starter/maintenance P rate is kept")
    elif p_status == "adequate":
        reasons.append("soil Olsen P is near the Zhengding/Shijiazhuang sufficiency range; P rate is reduced")
    if k_status == "adequate":
        reasons.append("soil available K is adequate; K rate is set as maintenance rather than deficiency correction")
    elif k_status == "excessive":
        reasons.append("soil available K is high; K rate is reduced to maintenance")
    if p2o5_rate > 0 or k2o_rate > 0:
        reasons.extend(PK_BASAL_PLACEMENT_GUIDANCE_CN)

    return {
        "p_status": p_status,
        "k_status": k_status,
        "olsen_p_mg_kg": olsen_p,
        "available_k_mg_kg": available_k,
        "p2o5_rate_kg_ha": p2o5_rate,
        "k2o_rate_kg_ha": k2o_rate,
        "reasons": reasons,
    }


def _nutrient_totals(product_plan: list[dict], custom_products: list[dict] | None = None) -> dict:
    totals = {"N": 0.0, "P2O5": 0.0, "K2O": 0.0}
    for item in product_plan:
        nutrients = product_plan_nutrients(item)
        if not any(float(value or 0.0) > 0.0 for value in nutrients.values()):
            nutrients = product_to_nutrients(str(item["product_name"]), float(item["amount_kg_ha"]), custom_products)
        for key in totals:
            totals[key] += float(nutrients.get(key, 0.0) or 0.0)
    return {key: round(value, 3) for key, value in totals.items()}


def _fertilizer_product_item(
    *,
    product_name: str,
    display_name: str,
    amount_kg_ha: float,
    nutrient_fractions: dict,
    source: str,
    release_type: str = "quick_release",
    release_days: int = 1,
    inventory_limited: bool = False,
) -> dict:
    amount = round(max(0.0, float(amount_kg_ha or 0.0)), 3)
    return {
        "product_name": product_name,
        "display_name": display_name,
        "amount_kg_ha": amount,
        "amount_kg_mu": kg_ha_to_kg_mu(amount),
        "nutrient_fractions": {key: float(nutrient_fractions.get(key, 0.0) or 0.0) for key in ("N", "P2O5", "K2O")},
        "nutrients_kg_ha": {
            key: round(amount * float(nutrient_fractions.get(key, 0.0) or 0.0), 3)
            for key in ("N", "P2O5", "K2O")
        },
        "source": source,
        "release_type": release_type,
        "release_days": int(release_days),
        "inventory_limited": inventory_limited,
    }


def _scientific_p_product(ph: float) -> tuple[str, str, dict]:
    if ph >= 7.8:
        return "monoammonium_phosphate", "磷酸一铵", {"N": 0.11, "P2O5": 0.52, "K2O": 0.0}
    return "diammonium_phosphate", "磷酸二铵", {"N": 0.18, "P2O5": 0.46, "K2O": 0.0}


def _select_inventory_compound_plan(p2o5_kg_ha: float, k2o_kg_ha: float, normalized: dict) -> tuple[list[dict], dict]:
    inventory = [item for item in normalized.get("fertilizer_inventory") or [] if item.get("kind") == "compound"]
    product_plan: list[dict] = []
    remaining_p = max(0.0, float(p2o5_kg_ha or 0.0))
    remaining_k = max(0.0, float(k2o_kg_ha or 0.0))
    tolerance = 0.5
    used_ids: set[str] = set()

    while inventory and (remaining_p > tolerance or remaining_k > tolerance):
        best: tuple[float, dict, float, bool] | None = None
        for item in inventory:
            if item["id"] in used_ids:
                continue
            nutrients = item["nutrients"]
            p_fraction = float(nutrients.get("P2O5", 0.0) or 0.0)
            k_fraction = float(nutrients.get("K2O", 0.0) or 0.0)
            required_amounts = []
            if remaining_p > tolerance and p_fraction > 0:
                required_amounts.append(remaining_p / p_fraction)
            if remaining_k > tolerance and k_fraction > 0:
                required_amounts.append(remaining_k / k_fraction)
            if not required_amounts:
                continue
            requested_amount = max(required_amounts)
            available = item.get("available_amount_kg_ha")
            amount = requested_amount if available is None else min(requested_amount, float(available))
            if amount <= 0:
                continue
            supplied_p = amount * p_fraction
            supplied_k = amount * k_fraction
            coverage = min(remaining_p, supplied_p) + min(remaining_k, supplied_k)
            overage = max(0.0, supplied_p - remaining_p) + max(0.0, supplied_k - remaining_k)
            supplied_n = amount * float(nutrients.get("N", 0.0) or 0.0)
            score = coverage * 10.0 - overage * 1.5 - supplied_n * 0.08 - amount * 0.01
            limited = available is not None and amount < requested_amount - 1e-6
            if best is None or score > best[0]:
                best = (score, item, amount, limited)
        if best is None:
            break
        _, item, amount, limited = best
        nutrients = item["nutrients"]
        product_plan.append(
            _fertilizer_product_item(
                product_name=item["id"],
                display_name=item["display_name"],
                amount_kg_ha=amount,
                nutrient_fractions=nutrients,
                source="农民已有肥",
                release_type=item["release_type"],
                release_days=int(item["release_days"]),
                inventory_limited=limited,
            )
        )
        remaining_p = max(0.0, remaining_p - amount * float(nutrients.get("P2O5", 0.0) or 0.0))
        remaining_k = max(0.0, remaining_k - amount * float(nutrients.get("K2O", 0.0) or 0.0))
        used_ids.add(item["id"])

    return product_plan, {"P2O5": round(remaining_p, 3), "K2O": round(remaining_k, 3)}


def _build_basal_product_plan(n_kg_ha: float, p2o5_kg_ha: float, k2o_kg_ha: float, normalized: dict) -> tuple[list[dict], dict]:
    if normalized["soil_test"].get("ph") is None:
        raise ValueError("soil_test.ph is required")
    ph = float(normalized["soil_test"]["ph"])
    custom_products = normalized.get("custom_products") or []
    product_plan: list[dict] = []

    if normalized.get("fertilizer_inventory"):
        inventory_plan, remaining_pk = _select_inventory_compound_plan(p2o5_kg_ha, k2o_kg_ha, normalized)
        product_plan.extend(inventory_plan)
        p2o5_kg_ha = float(remaining_pk["P2O5"])
        k2o_kg_ha = float(remaining_pk["K2O"])

    if p2o5_kg_ha > 0.5:
        p_product, p_display, p_fractions = _scientific_p_product(ph)
        product_plan.append(
            _fertilizer_product_item(
                product_name=p_product,
                display_name=p_display,
                amount_kg_ha=round(p2o5_kg_ha / p_fractions["P2O5"], 3),
                nutrient_fractions=p_fractions,
                source="科学补充推荐",
            )
        )
    if k2o_kg_ha > 0.5:
        product_plan.append(
            _fertilizer_product_item(
                product_name="potassium_chloride",
                display_name="氯化钾",
                amount_kg_ha=round(k2o_kg_ha / 0.60, 3),
                nutrient_fractions={"N": 0.0, "P2O5": 0.0, "K2O": 0.60},
                source="科学补充推荐",
            )
        )
    supplied = _nutrient_totals(product_plan, custom_products)
    urea_n = max(0.0, n_kg_ha - supplied["N"])
    if urea_n > 0:
        product_plan.append(
            _fertilizer_product_item(
                product_name="urea",
                display_name="尿素",
                amount_kg_ha=round(urea_n / 0.46, 3),
                nutrient_fractions={"N": 0.46, "P2O5": 0.0, "K2O": 0.0},
                source="默认常用氮肥",
            )
        )
    supplied = _nutrient_totals(product_plan, custom_products)
    nutrient_plan = {
        "n_kg_ha": round(supplied["N"], 3),
        "p2o5_kg_ha": round(supplied["P2O5"], 3),
        "k2o_kg_ha": round(supplied["K2O"], 3),
    }
    return product_plan, nutrient_plan


def _build_basal_recommendation(normalized: dict, pk_plan: dict) -> dict | None:
    planting_date = normalized.get("planting_date")
    if planting_date is None:
        return None
    target_yield_t = float(normalized["target_yield_kg_ha"]) / 1000.0
    soil_test = normalized["soil_test"]
    mineral_n = soil_test.get("mineral_n_kg_ha")
    if mineral_n is None and soil_test.get("alkali_hydrolyzable_n_mg_kg") is not None:
        mineral_n = float(soil_test["alkali_hydrolyzable_n_mg_kg"]) * 0.32
    if mineral_n is None:
        raise ValueError("soil_test.mineral_n_kg_ha or soil_test.alkali_hydrolyzable_n_mg_kg is required")
    if soil_test.get("organic_matter_g_kg") is None:
        raise ValueError("soil_test.organic_matter_g_kg is required")
    mineral_n = float(mineral_n)
    organic_matter = float(soil_test["organic_matter_g_kg"])
    expected_soil_contribution = min(150.0, mineral_n * 0.72 + organic_matter * 2.3)
    seasonal_n_need = target_yield_t * 22.0
    fertilizer_need = max(0.0, seasonal_n_need - expected_soil_contribution)
    p2o5_rate = float(pk_plan.get("p2o5_rate_kg_ha", 0.0) or 0.0)
    k2o_rate = float(pk_plan.get("k2o_rate_kg_ha", 0.0) or 0.0)
    if fertilizer_need <= 0 and p2o5_rate <= 0 and k2o_rate <= 0:
        return None
    basal_n = round(0.0 if fertilizer_need <= 0 else max(60.0, min(110.0, fertilizer_need * 0.42)), 3)
    product_plan, nutrient_plan = _build_basal_product_plan(
        basal_n,
        p2o5_rate,
        k2o_rate,
        normalized,
    )
    return {
        "Date": str(planting_date),
        "recommendationCode": "BASE_FERT_PLAN",
        "treatmentWindowCode": "BASELINE",
        "application_window_days": 1,
        "method": "basal_incorporated",
        "nutrient_plan": nutrient_plan,
        "product_plan": product_plan,
        "reason": [
            f"recommended maize basal N is based on target yield {normalized['target_yield_kg_ha']:.0f} kg/ha",
            f"soil initial mineral N {mineral_n:.1f} kg/ha and organic matter {organic_matter:.1f} g/kg were considered",
            *pk_plan.get("reasons", []),
        ],
        "placement_guidance_cn": PK_BASAL_PLACEMENT_GUIDANCE_CN,
    }


def _build_season_topdress_plan(normalized: dict, diagnosis: dict, demand_snapshot: dict, soil_supply: dict, fertilizer_pool: dict, economics: dict) -> list[dict]:
    growth_stage = normalized.get("growth_stage") or []
    decision_date = normalized["decision_date"]
    current_stage = normalize_iowa_stage(normalized["current_bbch"])
    v8_date = None
    tassel_date = None
    for item in growth_stage:
        item_date = type(decision_date).fromisoformat(str(item.get("Date")))
        stage = normalize_iowa_stage(item.get("Stage"))
        if item_date < decision_date:
            continue
        if v8_date is None and stage_rank(stage) >= stage_rank("V8"):
            v8_date = item_date
        if tassel_date is None and stage_rank(stage) >= stage_rank("VT"):
            tassel_date = item_date
        if v8_date is not None and tassel_date is not None:
            break
    remaining_demand = float(demand_snapshot.get("remaining_demand", 0.0) or 0.0)
    future_credit = float(soil_supply.get("available_supply", 0.0) or 0.0) * 0.35 + float((fertilizer_pool.get("effective_remaining_nutrients") or {}).get("N", 0.0) or 0.0) * 0.75
    gap = max(0.0, remaining_demand - future_credit)
    if gap < 20.0:
        return []
    plans = []
    if stage_between(current_stage, "V8", "V16+"):
        rules = _maize_stage_rules(current_stage, normalized.get("irrigation_method", "sprinkler"))
        rate = round(min(float(rules["max_single_n_rate_kg_ha"]), max(float(rules["min_effective_n_rate_kg_ha"]), gap * 0.72)), 3)
        plans.append({
            "Date": str(decision_date),
            "recommendationCode": "TOPDRESS_PLAN",
            "treatmentWindowCode": "PRE_TASSEL_CURRENT",
            "application_window_days": int(rules.get("application_window_days", 3)),
            "method": "topdress_before_irrigation",
            "nutrient_plan": {"n_kg_ha": rate, "p2o5_kg_ha": 0.0, "k2o_kg_ha": 0.0},
            "product_plan": [
                _fertilizer_product_item(
                    product_name="urea",
                    display_name="尿素",
                    amount_kg_ha=round(rate / 0.46, 3),
                    nutrient_fractions={"N": 0.46, "P2O5": 0.0, "K2O": 0.0},
                    source="默认常用追肥",
                )
            ],
            "reason": ["active pre-tassel nitrogen limitation requires immediate topdress rather than waiting until tasseling"],
        })
        gap = max(0.0, gap - rate * 0.72)
    elif v8_date is not None:
        rate = round(min(80.0, max(30.0, gap * 0.55)), 3)
        plans.append({
            "Date": str(v8_date),
            "recommendationCode": "TOPDRESS_PLAN",
            "treatmentWindowCode": "V8_V12",
            "application_window_days": int(_maize_stage_rules("V8", normalized.get("irrigation_method", "sprinkler")).get("application_window_days", 3)),
            "method": "topdress_before_irrigation",
            "nutrient_plan": {"n_kg_ha": rate, "p2o5_kg_ha": 0.0, "k2o_kg_ha": 0.0},
            "product_plan": [
                _fertilizer_product_item(
                    product_name="urea",
                    display_name="尿素",
                    amount_kg_ha=round(rate / 0.46, 3),
                    nutrient_fractions={"N": 0.46, "P2O5": 0.0, "K2O": 0.0},
                    source="默认常用追肥",
                )
            ],
            "reason": ["main maize topdress is reserved for rapid canopy and stem growth before tasseling"],
        })
        gap = max(0.0, gap - rate * 0.72)
    if tassel_date is not None and gap >= 18.0:
        rate = round(min(45.0, max(18.0, gap * 0.40)), 3)
        plans.append({
            "Date": str(tassel_date),
            "recommendationCode": "TOPDRESS_PLAN",
            "treatmentWindowCode": "VT_R1",
            "application_window_days": int(_maize_stage_rules("VT", normalized.get("irrigation_method", "sprinkler")).get("application_window_days", 2)),
            "method": "topdress_before_irrigation",
            "nutrient_plan": {"n_kg_ha": rate, "p2o5_kg_ha": 0.0, "k2o_kg_ha": 0.0},
            "product_plan": [
                _fertilizer_product_item(
                    product_name="urea",
                    display_name="尿素",
                    amount_kg_ha=round(rate / 0.46, 3),
                    nutrient_fractions={"N": 0.46, "P2O5": 0.0, "K2O": 0.0},
                    source="默认常用追肥",
                )
            ],
            "reason": ["a second maize topdress can support tasseling, silking and early kernel set when justified"],
        })
    return plans


class MaizeNutritionManagementModel:
    def run(self, payload: dict) -> dict:
        converted = deepcopy(payload)
        converted["growth_stage"] = [{"Date": item["Date"], "Stage": stage_to_bbch(item["Stage"])} for item in (payload.get("growth_stage") or [])]
        normalized = normalize_nutrition_request(converted)
        pk_plan = _estimate_maize_pk_plan(normalized)
        stage = normalize_iowa_stage(normalized["current_bbch"])
        phase = _phase_for_stage(stage)
        demand_snapshot = {
            "nutrient_key": "N",
            "seasonal_total_demand": round(max(0.0, normalized["target_yield_kg_ha"] / 1000.0 * 22.0), 3),
            "cumulative_demand_to_date": round(float((normalized.get("current_status") or {}).get("total_plant_n_kg_ha", 0.0) or 0.0) + float((normalized.get("current_status") or {}).get("daily_n_demand_kg_ha", 0.0) or 0.0), 3),
            "remaining_demand": 0.0,
            "near_term_demand": round(max(6.0, float((normalized.get("current_status") or {}).get("daily_n_demand_kg_ha", 0.0) or 0.0) * 6.0), 3),
            "biomass_progress": min(1.0, float(normalized["aboveground_biomass_kg_ha"]) / max(normalized["target_yield_kg_ha"] * 2.0, 1.0)),
        }
        demand_snapshot["remaining_demand"] = round(max(0.0, demand_snapshot["seasonal_total_demand"] - demand_snapshot["cumulative_demand_to_date"]), 3)
        soil_supply = estimate_soil_nutrient_supply("N", normalized["soil_test"], normalized["layered_soil_water"], normalized["root_zone_relative_available_water"], phase["access_factor"])
        forecast = normalized.get("weather_forecast") or normalized.get("weather_history")[-10:]
        rainfall_next_3d = sum(float(item["precipitation_sum"]) for item in forecast[:3])
        irrigation_actions = normalized.get("irrigation_recommendation") or []
        next_irrigation = next((item for item in irrigation_actions if item.get("actionTypeCode") == "IRRIGATION" and item.get("recommendedIrrigationDate")), None)
        irrigation_signal = {
            "next_irrigation_date": type(normalized["decision_date"]).fromisoformat(str(next_irrigation["recommendedIrrigationDate"])) if next_irrigation else None,
            "next_irrigation_depth_mm": float(next_irrigation.get("recommendedGrossDepthMm", 0.0) or 0.0) if next_irrigation else 0.0,
            "top_layer_relative_water": float((normalized["layered_soil_water"][0].get("relative_available_water", normalized["root_zone_relative_available_water"])) if normalized["layered_soil_water"] else normalized["root_zone_relative_available_water"]),
            "fertigation_supported": payload.get("irrigation_method") in {"drip", "micro-sprinkler"},
        }
        fertilizer_pool = estimate_effective_remaining_nutrients(
            normalized["fertilizer_history"],
            normalized["decision_date"],
            irrigation_signal["top_layer_relative_water"],
            rainfall_next_3d,
            float(irrigation_signal["next_irrigation_depth_mm"]),
        )
        available_now = float(soil_supply["available_supply"]) + float((fertilizer_pool["effective_remaining_nutrients"] or {}).get("N", 0.0) or 0.0)
        expected_lai = 2.4 if stage_between(stage, "VS", "V7") else 4.8 if stage_between(stage, "V8", "R1") else 3.2
        if normalized["lai"] is None:
            biomass_reference = max(normalized["target_yield_kg_ha"] * 1.4 * phase["uptake_fraction"], 1.0)
            biomass_ratio_for_lai = max(0.15, min(1.25, normalized["aboveground_biomass_kg_ha"] / biomass_reference))
            normalized["lai"] = round(expected_lai * (biomass_ratio_for_lai ** 0.65), 3)
            normalized["assumptions"].append("lai missing; simulated from maize stage, biomass, and target canopy trajectory")
        diagnosis = diagnose_nutrient_status(
            "N",
            lai_ratio=min(1.15, max(0.55, normalized["lai"] / max(expected_lai, 0.5))),
            biomass_ratio=min(1.10, max(0.55, normalized["aboveground_biomass_kg_ha"] / max(normalized["target_yield_kg_ha"] * 1.4 * phase["uptake_fraction"], 1.0))),
            available_nutrient_kg_ha=available_now,
            cumulative_supply_to_date_kg_ha=float(soil_supply["available_supply"]) + float((fertilizer_pool["cumulative_available_nutrients"] or {}).get("N", 0.0) or 0.0),
            cumulative_demand_to_date_kg_ha=float(demand_snapshot["cumulative_demand_to_date"]),
            remaining_demand_kg_ha=float(demand_snapshot["remaining_demand"]),
            near_term_demand_kg_ha=float(demand_snapshot["near_term_demand"]),
            recent_effective_n_kg_ha=float((fertilizer_pool["recent_available_nutrients"] or {}).get("N", 0.0) or 0.0),
            phase_name=phase["phase_name"],
        )
        recommendation = recommend_fertilization(
            nutrient_key="N",
            decision_date=normalized["decision_date"],
            diagnosis=diagnosis,
            demand_snapshot=demand_snapshot,
            soil_supply=soil_supply,
            fertilizer_pool=fertilizer_pool,
            stage_rules=_maize_stage_rules(stage, payload.get("irrigation_method", "sprinkler")),
            irrigation_signal=irrigation_signal,
            rainfall_signal={"rainfall_next_3d_mm": rainfall_next_3d},
            economics=normalized["economics"],
            custom_products=normalized["custom_products"],
        )
        season_plan = []
        basal = _build_basal_recommendation(normalized, pk_plan)
        if basal:
            season_plan.append(basal)
        season_plan.extend(_build_season_topdress_plan(normalized, diagnosis, demand_snapshot, soil_supply, fertilizer_pool, normalized["economics"]))
        recommendation["season_plan_recommendations"] = season_plan
        recommendation["disabled"] = False
        return {
            "decision_date": str(normalized["decision_date"]),
            "nutrition_status": {"n_status": diagnosis["status"], "p_status": pk_plan["p_status"], "k_status": pk_plan["k_status"]},
            "recommendation": recommendation,
            "reason": recommendation.get("reason", []),
            "assumptions": normalized["assumptions"],
            "diagnostics": {
                "diagnosis": diagnosis,
                "pk_diagnosis": pk_plan,
                "demand_snapshot": demand_snapshot,
                "soil_supply": soil_supply,
                "fertilizer_effective_pool": fertilizer_pool,
                "recovery_assumptions": {"topdress_recovery_fraction": 0.66, "fertigation_recovery_fraction": 0.78},
            },
        }


__all__ = [
    "MaizeNutritionManagementModel",
    "build_fertilizer_recommendation_block",
    "build_season_fertilization_actions",
]
