"""Daily crop coupling with one authoritative hydrology update per day.

Crop profiles and pure growth/phenology functions come from existing OCM.
Nutrition is an explicit fixed scenario factor, not a simulated N cycle.
"""

from __future__ import annotations
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import date, timedelta
from math import exp,isfinite
import pandas as pd
from core.crop_model import daily_engine as engine
from crops.wheat.phenology.model import WheatPhenology
from core.hydrology.types import (
    SoilLayerParameters,
    SoilColumnState,
    DualDomainState,
    HydrologyParameters,
    WaterDayForcing,
    IrrigationEvent,
    bounded,
)
from core.hydrology.balance import step_water_day, validate_state, FLUX_NAMES
from core.hydrology.adapters import field_water_mm
from core.hydrology.irrigation import remap_domains
from core.hydrology.evapotranspiration import reference_et0
from .contracts import validate_inputs
from .irrigation_policy import policy_event
from .phenology import wheat_development_factor,maize_thermal_time
from .stage_canopy import initial_sla, radiation_fraction
from .establishment import configuration as establishment_configuration, seed_layer_available_water_mm, sowing_stage
from .tillage import event_schedule as tillage_schedule, mix_water
from .growth import (validate_growth_parameters,revised,has_emerged,leaf_update,resolve_nutrition_factor,
                     grain_fill_fraction,validate_grain_parameters,stem_reserve_transfer,phase_rue_multiplier,
                     grain_number_from_flowering_biomass,grain_set_started,
                     transpiration_efficiency,dual_resource_growth,critical_growth_increment,water_stressed_assimilation)


@dataclass
class SeasonResult:
    daily: list[dict]
    summary: dict
    final_state: DualDomainState


def hydrology_parameters(inputs, parameters):
    layers = tuple(SoilLayerParameters(**layer) for layer in inputs["soil_layers"])
    return HydrologyParameters(layers, **parameters.get("hydrology", {}))


def refine_surface_layer(inputs, surface_depth_mm=100.):
    """Resolve actual surface storage without changing soil or total water.

    Apply before starting a season/rotation, and to every subsequent segment.
    Carried states must already have this geometry; no state is reset here.
    """
    from copy import deepcopy
    depth=bounded(surface_depth_mm,'surface layer depth',1e-9)
    result=deepcopy(inputs)
    layers=result['soil_layers']
    thickness=layers[0]['thickness_mm']
    if thickness>depth:
        first=deepcopy(layers[0]);second=deepcopy(layers[0])
        first['thickness_mm']=depth;second['thickness_mm']=thickness-depth
        result['soil_layers']=[first,second]+layers[1:]
        if 'initial_theta' in result:
            theta=result['initial_theta']
            if len(theta)!=len(layers):raise ValueError('Initial profile length mismatch')
            result['initial_theta']=[theta[0],theta[0]]+theta[1:]
    return result


def initial_water_state(inputs):
    if "initial_theta" not in inputs:
        raise ValueError("Independent initial_theta required for first segment")
    water = [
        theta * layer["thickness_mm"]
        for theta, layer in zip(inputs["initial_theta"], inputs["soil_layers"])
    ]
    fw = inputs["technology"]["wetted_fraction"]
    return DualDomainState(
        fw, SoilColumnState(water), SoilColumnState(list(water)) if fw < 1 else None
    )


def _profile(inputs, crop_parameters):
    uuid = {"wheat": engine.WHEAT_UUID, "maize": engine.MAIZE_UUID}[inputs["crop"]]
    profile = engine.PROFILES[uuid]
    if inputs["crop"] == "maize":
        maturity, phases = engine._maize_phenology_definition(
            inputs.get("maturity_group", "middle")
        )
        profile = replace(
            profile, maturity_thermal_time_c=maturity, phenology_phases=phases
        )
        thresholds = crop_parameters.get("maize_phenology", {}).get(
            "stage_thermal_time_c"
        )
        if thresholds is not None:
            names = [name for name, _, _ in profile.phenology_phases]
            values = _ordered_thresholds(thresholds, names)
            maturity = values["R6"]
            profile = replace(
                profile,
                maturity_thermal_time_c=maturity,
                phenology_phases=tuple(
                    (name, values[name] / maturity, bbch)
                    for name, _, bbch in profile.phenology_phases
                ),
            )
    # Physical crop coefficients are shared across irrigation technologies.
    allowed = {
        "rue_g_mj",
        "initial_lai",
        "max_lai",
        "sla_max_m2_g",
        "sla_min_m2_g",
        "extinction_coefficient",
        "root_initial_mm",
        "root_max_mm",
        "kernels_per_g_flowering_biomass",
        "max_grain_size_g",
    }
    overrides = crop_parameters.get("profile", {})
    if crop_parameters.get('growth_process',{}).get('version')=='canopy_v5' and set(overrides)&{'max_lai','sla_max_m2_g','sla_min_m2_g'}:
        raise ValueError('canopy_v5 excludes maximum LAI and uses development-stage SLA')
    if set(overrides) - allowed:
        raise ValueError("Unsupported crop profile override")
    for name, value in overrides.items():
        bounded(value, name, 1e-9)
    result = replace(profile, **overrides)
    if result.sla_min_m2_g > result.sla_max_m2_g:
        raise ValueError("Specific leaf area minimum exceeds maximum")
    process=validate_growth_parameters(crop_parameters.get('growth_process',{}))
    if revised(process) and process.get('version')!='canopy_v5' and result.initial_lai>process.get('max_lai',result.max_lai):
        raise ValueError('Crop initial LAI exceeds canopy capacity')
    return result


def _ordered_thresholds(thresholds, stages):
    """Validate a complete frozen stage card without changing global defaults."""
    if not isinstance(thresholds, dict):
        raise ValueError("Stage thresholds must be a mapping")
    try:
        normalized = {
            int(key) if isinstance(stages[0], int) else str(key): value
            for key, value in thresholds.items()
        }
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid stage threshold keys") from exc
    if set(normalized) != set(stages) or len(normalized) != len(thresholds):
        raise ValueError("Stage thresholds must include exactly the model stages")
    values = {key: bounded(normalized[key], "stage threshold", 0) for key in stages}
    ordered = list(values.values())
    if ordered[0] != 0 or any(b <= a for a, b in zip(ordered, ordered[1:])):
        raise ValueError("Stage thresholds must start at zero and strictly increase")
    return values


def _phenology(inputs, profile, parameters):
    scale = bounded(
        parameters.get("thermal_time_scale", 1.0), "thermal_time_scale", 1e-9
    )
    result = []
    cumulative = 0.0
    vern_state = 0.0
    wheat_parameters = dict(parameters.get("wheat_phenology", {}))
    wheat_thresholds = wheat_parameters.pop("stage_thresholds", None)
    wheat_response = wheat_parameters.pop("development_response", "legacy")
    if inputs["crop"] == "wheat":
        wheat_development_factor(0,1.,1.,wheat_response)
    maize_parameters = dict(parameters.get("maize_phenology", {}))
    wheat = (
        WheatPhenology(
            {
                **wheat_parameters,
                "latitude": inputs["latitude_deg"],
                "variety_maturation_group": inputs.get("maturity_group", "middle"),
            }
        )
        if inputs["crop"] == "wheat"
        else None
    )
    if wheat:
        if wheat_thresholds is not None:
            wheat.stage_thresholds = _ordered_thresholds(
                wheat_thresholds, list(wheat.stage_thresholds)
            )
        bounded(wheat.required_vernalization_days, "required_vernalization_days", 1e-9)
        if not isfinite(wheat.base_temp) or not wheat.base_temp < wheat.opt_temp < wheat.max_temp:
            raise ValueError("Wheat cardinal temperatures must be finite and ordered")
        bounded(wheat.photoperiod_sensitivity, "photoperiod_sensitivity", 0)
    for weather in inputs["weather"]:
        temp = (weather["tmin_c"] + weather["tmax_c"]) / 2
        if wheat:
            previous = wheat.get_stage(cumulative)
            thermal = wheat.compute_thermal_time(temp)
            photo = wheat.compute_photoperiod_factor(
                wheat.estimate_daylength(pd.Timestamp(weather["date"]))
            )
            vern_state = min(
                wheat.required_vernalization_days,
                vern_state + wheat.compute_vernalization_rate(temp),
            )
            if (
                temp >= wheat.devernalization_threshold
                and previous < 31
                and vern_state > 10.0
            ):
                vern_state = max(0.0, vern_state - wheat.devernalization_rate)
            vern = wheat.minimum_vernalization_factor + (
                1 - wheat.minimum_vernalization_factor
            ) * min(1.0, vern_state / wheat.required_vernalization_days)
            development = wheat_development_factor(previous,photo,vern,wheat_response)
            cumulative += thermal * development * scale
            bbch = wheat.get_stage(cumulative)
            progress = min(1.0, cumulative / max(wheat.stage_thresholds.values()))
            row = {
                "bbch": bbch,
                "stage": f"BBCH{bbch}",
                "thermal_progress": progress,
                "vernalization_factor": vern,
                "photoperiod_factor": photo,
            }
        else:
            cumulative += (maize_thermal_time(weather["tmin_c"],weather["tmax_c"],maize_parameters)
                if maize_parameters else engine._daily_thermal_time(profile, {"temperature_2m_mean": temp})) * scale
            stage = engine._phenology_for_thermal_time(
                profile, cumulative, profile.maturity_thermal_time_c
            )
            row = {
                "bbch": stage["BBCH"],
                "stage": stage["StageName"],
                "thermal_progress": stage["ThermalProgress"],
                "vernalization_factor": 1.0,
                "photoperiod_factor": 1.0,
            }
        row["cumulative_thermal_time_c"] = cumulative
        anchors = ([wheat.stage_thresholds[k] for k in [9,61,89]] if wheat else
                   [engine._stage_progress_threshold(profile,k)*profile.maturity_thermal_time_c for k in ['VE','R1','R6']])
        row['development_stage'] = engine._linear_interp(cumulative,anchors,[0.,1.,2.])
        result.append(row)
    return result


def is_crop_mature(crop, stage):
    """Configured terminal maturity: maize R6, wheat full ripeness BBCH89."""
    return stage['stage'] == 'R6' if crop == 'maize' else stage['bbch'] >= 89


def simulate_season(inputs, parameters, initial_state=None):
    validate_inputs(inputs)
    p = hydrology_parameters(inputs, parameters)
    state = (
        initial_water_state(inputs)
        if initial_state is None
        else remap_domains(initial_state, inputs["technology"]["wetted_fraction"])
    )
    validate_state(state, p)
    if state.last_date and date.fromisoformat(
        inputs["start_date"]
    ) != date.fromisoformat(state.last_date) + timedelta(days=1):
        raise ValueError(
            "Carried state requires the immediately following daily weather"
        )
    initial_storage = state.storage_mm()
    fallow = inputs["crop"] == "fallow"
    crop_parameters = parameters.get("crop", {})
    unknown = set(crop_parameters) - {
        "profile",
        "thermal_time_scale",
        "wheat_phenology",
        "maize_phenology",
        "nutrition_factor",
        "nutrition_by_management",
        "transpiration_coefficient",
        "soil_evaporation_coefficient",
        "growth_process",
        "grain_process",
        "germination_water_response",
    }
    if unknown:
        raise ValueError(f"Unknown crop parameter keys: {sorted(unknown)}")
    process=validate_growth_parameters(crop_parameters.get('growth_process',{}))
    grain_process=validate_grain_parameters(crop_parameters.get('grain_process',{}))
    nutrition = resolve_nutrition_factor(inputs,crop_parameters)
    kt = bounded(
        crop_parameters.get("transpiration_coefficient", 1.15),
        "transpiration_coefficient",
        0,
        3,
    )
    ke = bounded(
        crop_parameters.get("soil_evaporation_coefficient", 1.0),
        "soil_evaporation_coefficient",
        0,
        3,
    )
    profile = None if fallow else _profile(inputs, crop_parameters)
    stages = (
        [
            {
                "bbch": 0,
                "stage": "fallow",
                "thermal_progress": 0.0,
                "vernalization_factor": 1.0,
                "photoperiod_factor": 1.0,
                "cumulative_thermal_time_c": 0.0,
            }
            for _ in inputs["weather"]
        ]
        if fallow
        else _phenology(inputs, profile, crop_parameters)
    )
    lai = 0.0 if fallow else profile.initial_lai
    leaf = 0.0 if fallow else lai / (initial_sla(profile,process) * 0.1)
    stem = dead = grain = sink = leaf_age = 0.0
    reserve=0.
    leaf_cohorts=None
    grain_set = False
    critical_growth=previous_dvs=0.
    emerged=not revised(process)
    if not emerged:lai=0.
    germination_method,seed_depth,seed_layer=(establishment_configuration(inputs,crop_parameters,p.layers)
        if not fallow else ('temperature_only',0.,0))
    water_germination=germination_method=='seed_layer_available_water'
    germinated=not water_germination
    germination_index=None
    germination_date=None
    active_stages=stages
    if water_germination:
        emerged=False;lai=0.
    daily = []
    events = {}
    tech = inputs["technology"]
    # FAO-56 ch.10: exposed-soil evaporation decreases by approximately
    # 5% per 10% organic mulch cover. Cover is a prescribed management input;
    # this does not model residue decomposition or the nutrient cycle.
    mulch=bounded(inputs.get('organic_mulch_cover_fraction',0.),'organic mulch cover fraction',0.,1.)
    for raw in inputs.get("irrigation_events", []):
        values = {k: v for k, v in raw.items() if k != "date"}
        event = IrrigationEvent(**tech, **values)
        events.setdefault(raw["date"], []).append(event)
    policy = inputs.get('irrigation_policy')
    policy_applied = 0.
    last_irrigation_date = None
    reserved_fixed = sum(event.amount_mm for day_events in events.values() for event in day_events)
    event_ids = {event.event_id for day_events in events.values() for event in day_events}
    fixed_dates = [date.fromisoformat(day) for day in events]
    tillage_events=tillage_schedule(inputs)
    for day_index,(weather, stage) in enumerate(zip(inputs["weather"], stages)):
        soil_storage_initial=float(sum(field_water_mm(state)))
        operations=tillage_events.get(weather['date'],[])
        for operation in operations:
            state=mix_water(state,p.layers,operation['depth_mm'])
        seed_esw=None
        if water_germination:
            seed_esw=seed_layer_available_water_mm(state,p.layers,seed_layer)
            if not germinated and seed_esw>0.:
                germinated=True;germination_index=day_index;germination_date=weather['date']
                # Dry-seed days add neither thermal time nor vernalization.
                active_stages=stages if day_index==0 else _phenology(
                    {**inputs,'weather':inputs['weather'][day_index:]},profile,crop_parameters)
                if not revised(process):emerged=True;lai=profile.initial_lai
            stage=(active_stages[day_index-germination_index] if germinated
                   else sowing_stage(inputs['crop'],stage))
        progress = stage["thermal_progress"]
        if not fallow and revised(process) and not emerged and has_emerged(stage):
            emerged=True;lai=profile.initial_lai
        bbch = stage["bbch"]
        cover = 0.0 if fallow else engine._green_cover(lai, profile)
        root = 0.0 if fallow or not germinated else engine._root_depth_for_progress(profile, progress)
        day_events = list(events.get(weather['date'], []))
        generated = None
        if policy is not None:
            # Only inherited water and known management enter the policy.
            # Reserve all scheduled fixed supply, including future dates.
            fixed_today = sum(event.amount_mm for event in day_events)
            reserved_fixed = max(0., reserved_fixed - fixed_today)
            current_date = date.fromisoformat(weather['date'])
            near_scheduled = any(0 < (day - current_date).days < policy['min_interval_days']
                                 for day in fixed_dates)
            if not day_events and not near_scheduled:
                policy_root=seed_depth if not germinated else root
                generated = policy_event(policy, weather['date'], stage, policy_root, state, p.layers,
                                         policy_applied + reserved_fixed, last_irrigation_date)
                if generated is not None:
                    original_id = generated['event_id']
                    suffix = 1
                    while generated['event_id'] in event_ids:
                        generated['event_id'] = f'{original_id}-{suffix}'
                        suffix += 1
                    event_ids.add(generated['event_id'])
                    day_events.append(IrrigationEvent(**tech, **{
                        k: v for k, v in generated.items() if k != 'date'}))
            if day_events:
                policy_applied += sum(event.amount_mm for event in day_events)
                last_irrigation_date = weather['date']
        et0 = reference_et0(
            weather,
            latitude_deg=inputs["latitude_deg"],
            elevation_m=inputs["elevation_m"],
            wind_height_m=inputs.get("wind_height_m"),
            method=inputs["et0_method"],
        )
        mature = not fallow and is_crop_mature(inputs["crop"], stage)
        # Grain maturity ends production, while remaining green leaves can
        # still use water. Soil uptake provides the actual limitation.
        pt = et0 * kt * cover
        atmospheric_pt = pt
        pe = et0 * ke * (1 - cover) * (1-.5*mulch)
        potential=0.
        efficiency=vpd=None
        if not fallow:
            radiation=bounded(weather.get('solar_radiation_mj_m2'),'growth solar radiation',0)
            temp={'temperature_2m_mean':(weather['tmin_c']+weather['tmax_c'])/2}
            if not mature and emerged:
                potential=(radiation*radiation_fraction(process)*cover*profile.rue_g_mj
                           *phase_rue_multiplier(profile,stage,process)
                           *engine._temperature_growth_factor(temp,profile)*10)
            if 'transpiration_efficiency_kpa' in process:
                vpd,efficiency=transpiration_efficiency(weather,process['transpiration_efficiency_kpa'],
                    method=process.get('transpiration_vpd_method','temperature_proxy'))
                # Green leaves retain water use after maturity even when new
                # carbon production has ceased.
                if not mature and process.get('transpiration_demand_method','carbon_requirement')=='carbon_requirement':
                    pt=min(pt,potential*nutrition/efficiency)
        forcing = WaterDayForcing(
            weather["date"],
            weather["precipitation_mm"],
            tuple(day_events),
            cover,
            root,
            pt,
            pe,
            weather.get("water_table_depth_mm"),
            et0 * max(kt, 1.0) * cover,
            weather.get("precipitation_hourly_mm"),
        )
        balance = step_water_day(state, forcing, p)
        state = balance.state
        effective_pt = balance.fluxes["potential_transpiration_after_interception_mm"]
        water_factor = (
            min(1.0, balance.fluxes["transpiration_mm"] / effective_pt)
            if effective_pt > 0
            else 1.0
        )
        transpiration_supply_factor=water_factor
        actual = 0.0
        biomass_before=leaf+stem+dead+grain
        growth_grain=remobilization=stem_growth=ceiling=0.
        if not fallow:
            actual = water_stressed_assimilation(potential,water_factor,nutrition,
                process.get('assimilation_water_stress_exponent',1.))
            if efficiency is not None:
                actual=dual_resource_growth(potential*nutrition,balance.fluxes['transpiration_mm'],
                    balance.fluxes['canopy_evaporation_mm'],pt,efficiency,
                    effective_remaining_demand=effective_pt)
                water_factor=min(1.,max(0.,actual/(potential*nutrition))) if potential*nutrition>0. else water_factor
            biomass_before = leaf + stem + dead + grain
            current_dvs=stage.get('development_stage',0.)
            critical_growth+=critical_growth_increment(actual,previous_dvs,current_dvs,process)
            previous_dvs=current_dvs
            if not grain_set and grain_set_started(profile,stage,process):
                # Sink depends on independent cultivar and simulated biomass.
                number = grain_number_from_flowering_biomass(profile,biomass_before,process)
                sink = number * profile.max_grain_size_g * 10
                grain_set = True
            if grain_set and process.get('grain_number_basis')=='critical_growth':
                number=grain_number_from_flowering_biomass(profile,critical_growth,process)
                sink=number*profile.max_grain_size_g*10
            ceiling = sink * grain_fill_fraction(profile, progress, bbch, process)
            growth_grain = min(
                actual
                * engine._grain_allocation_fraction_for_profile(
                    profile, progress, bbch
                ),
                max(0.0, ceiling - grain),
            )
            grain += growth_grain
            vegetative = actual - growth_grain
            if emerged:
                # Carbon is already constrained by radiation and transpiration.
                # Leaf water responses depend on soil supply relative to demand,
                # rather than carbon realization under an atmospheric ET cap.
                update=leaf_update(lai=lai,leaf=leaf,vegetative_growth=vegetative,profile=profile,stage=stage,
                    temperature=temp['temperature_2m_mean'],stress=transpiration_supply_factor*nutrition,parameters=process,leaf_age_c_days=leaf_age,leaf_cohorts=leaf_cohorts,
                    water_factor=transpiration_supply_factor,nutrition_factor=nutrition)
                leaf_cohorts=update.get('leaf_cohorts')
                leaf_age=update['leaf_age_c_days']
                dead+=update['dead_leaf_increment_kg_ha'];leaf=update['leaf_kg_ha'];stem_growth=update['non_leaf_growth_kg_ha'];stem+=stem_growth;lai=update['lai']
            if grain_process.get('version')=='stem_reserve_v1':
                transfer=stem_reserve_transfer(stem=stem-stem_growth,reserve=reserve,stem_growth=stem_growth,
                    demand=max(0.,ceiling-grain),parameters=grain_process,active=grain_set and not mature)
                stem=transfer['stem_kg_ha'];reserve=transfer['reserve_kg_ha'];remobilization=transfer['transfer_kg_ha']
            else:
                remobilization = min(
                    max(0.0, ceiling - grain),
                    stem * engine._grain_remobilization_fraction_for_profile(profile, progress, bbch),
                )
                stem -= remobilization
            grain += remobilization
        weighted_water = field_water_mm(state)
        row = {
            "date": weather["date"],
            "crop": inputs["crop"],
            **stage,
            **balance.fluxes,
            "balance_residual_mm": balance.balance_residual_mm,
            "et0_mm": et0,
            "et0_method": inputs["et0_method"],
            "precipitation_timing": balance.diagnostics["precipitation_timing"],
            "potential_transpiration_mm": pt,
            "potential_soil_evaporation_mm":pe,
            "organic_mulch_cover_fraction":mulch,
            "soil_storage_initial_mm": soil_storage_initial,
            "soil_storage_final_mm": float(sum(weighted_water)),
            "water_factor": water_factor,
            "root_zone_stress_factor":balance.diagnostics['root_zone_stress_factor'],
            "root_zone_depletion_fraction":balance.diagnostics['root_zone_depletion_fraction'],
            "potential_et_demand_mm":balance.diagnostics['potential_et_demand_mm'],
            "transpiration_supply_factor":transpiration_supply_factor,
            "atmospheric_potential_transpiration_mm":atmospheric_pt,
            "transpiration_demand_method":process.get('transpiration_demand_method','carbon_requirement') if efficiency is not None else 'atmospheric',
            "daytime_vpd_kpa":vpd if process.get('transpiration_vpd_method','temperature_proxy')=='temperature_proxy' else None,
            "transpiration_vpd_kpa":vpd,
            "transpiration_vpd_method":process.get('transpiration_vpd_method','temperature_proxy') if efficiency is not None else None,
            "transpiration_efficiency_kg_ha_mm":efficiency,
            "wet_canopy_energy_fraction":max(0.,min(1.,1.-effective_pt/pt)) if pt>0. else 0.,
            "nutrition_factor": nutrition,
            "potential_growth_kg_ha": potential,
            "growth_kg_ha": actual,
            "lai": lai,
            "leaf_age_c_days": leaf_age,
            "biomass_kg_ha": leaf + stem + dead + grain,
            "yield_kg_ha": grain,
            "leaf_kg_ha":leaf,"stem_kg_ha":stem,"dead_leaf_kg_ha":dead,"stem_reserve_kg_ha":reserve,
            "direct_grain_growth_kg_ha":growth_grain,"stem_remobilization_kg_ha":remobilization,
            "grain_sink_kg_ha":sink,"grain_fill_ceiling_kg_ha":ceiling,
            "critical_growth_kg_ha":critical_growth,
            "crop_carbon_residual_kg_ha":leaf+stem+dead+grain-biomass_before-actual,
            "root_depth_mm": root,
            "soil_theta": [
                w / l.thickness_mm for w, l in zip(weighted_water, p.layers)
            ],
            "wet_theta": [
                w / l.thickness_mm for w, l in zip(state.wet.water_mm, p.layers)
            ],
            "dry_theta": [
                w / l.thickness_mm for w, l in zip(state.dry.water_mm, p.layers)
            ]
            if state.wetted_fraction < 1
            else [],
            "wetted_fraction": state.wetted_fraction,
            "diagnostics": balance.diagnostics,
        }
        row["et_mm"] = (
            row["soil_evaporation_mm"]
            + row["transpiration_mm"]
            + row["canopy_evaporation_mm"]
        )
        if water_germination:
            row.update(germination_start_date=germination_date,seed_water_ready=germinated,
                       seed_layer_available_water_initial_mm=seed_esw,sowing_depth_mm=seed_depth)
        if tillage_events:
            row.update(tillage_event_ids=[operation['event_id'] for operation in operations],
                       tillage_mixing_depths_mm=[operation['depth_mm'] for operation in operations])
        if policy is not None:
            row.update(irrigation_policy_event_id=generated['event_id'] if generated else None,
                       irrigation_policy_amount_mm=generated['amount_mm'] if generated else 0.,
                       irrigation_event_ids=[event.event_id for event in day_events],
                       irrigation_policy_cumulative_mm=policy_applied)
        daily.append(row)
    summary = {name: sum(row[name] for row in daily) for name in FLUX_NAMES}
    summary.update(
        {
            "et_mm": sum(row["et_mm"] for row in daily),
            "yield_kg_ha": grain,
            "biomass_kg_ha": leaf + stem + dead + grain,
            "initial_storage_mm": initial_storage,
            "final_storage_mm": state.storage_mm(),
            "balance_residual_mm": sum(row["balance_residual_mm"] for row in daily),
            "parameter_version": parameters.get(
                "version", "unvalidated-engineering-priors"
            ),
            "nutrient_process": "fixed independent scenario factor; no N cycle",
            "anthesis_date": next(
                (
                    r["date"]
                    for r in daily
                    if (
                        r["stage"] == "R1"
                        if inputs["crop"] == "maize"
                        else r["bbch"] >= 61
                    )
                ),
                None,
            ),
            "maturity_date": next(
                (
                    r["date"]
                    for r in daily
                    if (
                        r["stage"] == "R6"
                        if inputs["crop"] == "maize"
                        else r["bbch"] >= 89
                    )
                ),
                None,
            ),
        }
    )
    maturity_date = summary.get("maturity_date")
    summary["season_end_date"] = maturity_date or inputs.get("cutting_date")
    summary["season_end_reason"] = (
        ("physiological_maturity" if inputs["crop"] == "maize" else "full_ripeness") if maturity_date
        else "cut_before_maturity" if inputs.get("cutting_date")
        else "right_censored"
    )
    summary["maturity_definition"] = "R6_physiological_maturity" if inputs["crop"] == "maize" else "BBCH89_full_ripeness"
    if water_germination:
        summary.update(germination_start_date=germination_date,germination_water_response=germination_method,
                       sowing_depth_mm=seed_depth)
    return SeasonResult(daily, summary, deepcopy(state))


def simulate_rotation(seasons, parameters, initial_state):
    """Continuous dated segments, including explicit fallow weather segments.

    No seasonal observed theta reset; same soil profile throughout a rotation.
    Geometry switches redistribute inherited water by overlap areas.
    """
    if not seasons:
        raise ValueError("At least one dated segment is required")
    profile = seasons[0]["soil_layers"]
    state = deepcopy(initial_state)
    results = []
    for inputs in seasons:
        if inputs["soil_layers"] != profile:
            raise ValueError(
                "Soil profile changes require an explicit conservative regridding operator"
            )
        result = simulate_season(inputs, parameters, state)
        results.append(result)
        state = result.final_state
    return results


def spin_up(historical_segments, parameters, initial_state):
    """Run independent pre-period forcing; does not assume equilibrium."""
    result = simulate_rotation(historical_segments, parameters, initial_state)
    return result[-1].final_state, {
        "days": sum(len(r.daily) for r in result),
        "initial_storage_mm": initial_state.storage_mm(),
        "final_storage_mm": result[-1].final_state.storage_mm(),
        "balance_residual_mm": sum(r.summary["balance_residual_mm"] for r in result),
        "status": "historical initialization; equilibrium and initial-state sensitivity require separate evaluation",
    }
