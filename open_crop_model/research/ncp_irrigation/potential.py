"""Potential crop growth using OCM kernels, with optional explicit nutrient limitation.

This trajectory has no hydrological state and cannot validate irrigation or
actual field ET. It provides conditional canopy/biomass calibration diagnostics.
"""
from core.hydrology.types import bounded
from core.crop_model import daily_engine as engine
from .stage_canopy import initial_sla, radiation_fraction
from .model import _profile, _phenology, is_crop_mature
from .growth import (validate_growth_parameters,revised,has_emerged,leaf_update,resolve_nutrition_factor,
                     grain_fill_fraction,validate_grain_parameters,stem_reserve_transfer,phase_rue_multiplier,
                     grain_number_from_flowering_biomass,grain_set_started,critical_growth_increment)


def simulate_potential_season(inputs, crop_parameters, phenology_rows=None):
    profile=_profile(inputs,crop_parameters)
    nutrition=resolve_nutrition_factor(inputs,crop_parameters)
    process=validate_growth_parameters(crop_parameters.get('growth_process',{}))
    grain_process=validate_grain_parameters(crop_parameters.get('grain_process',{}))
    stages=_phenology(inputs,profile,crop_parameters) if phenology_rows is None else phenology_rows
    if len(stages)!=len(inputs['weather']):
        raise ValueError('Frozen phenology must contain one row per weather day')
    lai=profile.initial_lai
    leaf=lai/(initial_sla(profile,process)*.1)
    stem=dead=grain=sink=leaf_age=0.
    reserve=0.
    leaf_cohorts=None
    grain_set=False
    critical_growth=previous_dvs=0.
    emerged=not revised(process)
    if not emerged:lai=0.
    rows=[]
    for weather,stage in zip(inputs['weather'],stages):
        progress=stage['thermal_progress'];bbch=stage['bbch']
        if revised(process) and not emerged and has_emerged(stage):
            emerged=True;lai=profile.initial_lai
        cover=engine._green_cover(lai,profile)
        mature=is_crop_mature(inputs['crop'],stage)
        radiation=bounded(weather.get('solar_radiation_mj_m2'),'growth solar radiation',0)
        temp={'temperature_2m_mean':(weather['tmin_c']+weather['tmax_c'])/2}
        actual=0. if mature or not emerged else radiation*radiation_fraction(process)*cover*profile.rue_g_mj*phase_rue_multiplier(profile,stage,process)*engine._temperature_growth_factor(temp,profile)*10*nutrition
        biomass_before=leaf+stem+dead+grain
        current_dvs=stage.get('development_stage',0.)
        critical_growth+=critical_growth_increment(actual,previous_dvs,current_dvs,process)
        previous_dvs=current_dvs
        if not grain_set and grain_set_started(profile,stage,process):
            number=grain_number_from_flowering_biomass(profile,biomass_before,process)
            sink=number*profile.max_grain_size_g*10;grain_set=True
        if grain_set and process.get('grain_number_basis')=='critical_growth':
            number=grain_number_from_flowering_biomass(profile,critical_growth,process)
            sink=number*profile.max_grain_size_g*10
        ceiling=sink*grain_fill_fraction(profile,progress,bbch,process)
        growth_grain=min(actual*engine._grain_allocation_fraction_for_profile(profile,progress,bbch),max(0.,ceiling-grain))
        grain+=growth_grain;vegetative=actual-growth_grain
        stem_growth=0.
        if emerged:
            update=leaf_update(lai=lai,leaf=leaf,vegetative_growth=vegetative,profile=profile,stage=stage,
                temperature=temp['temperature_2m_mean'],stress=nutrition,parameters=process,leaf_age_c_days=leaf_age,leaf_cohorts=leaf_cohorts,
                water_factor=1.,nutrition_factor=nutrition)
            leaf_cohorts=update.get('leaf_cohorts')
            leaf_age=update['leaf_age_c_days']
            dead+=update['dead_leaf_increment_kg_ha'];leaf=update['leaf_kg_ha'];stem_growth=update['non_leaf_growth_kg_ha'];stem+=stem_growth;lai=update['lai']
        if grain_process.get('version')=='stem_reserve_v1':
            transfer=stem_reserve_transfer(stem=stem-stem_growth,reserve=reserve,stem_growth=stem_growth,
                demand=max(0.,ceiling-grain),parameters=grain_process,active=grain_set and not mature)
            stem=transfer['stem_kg_ha'];reserve=transfer['reserve_kg_ha'];remobilization=transfer['transfer_kg_ha']
        else:
            remobilization=min(max(0.,ceiling-grain),stem*engine._grain_remobilization_fraction_for_profile(profile,progress,bbch))
            stem-=remobilization
        grain+=remobilization
        rows.append(dict(date=weather['date'],crop=inputs['crop'],**stage,lai=lai,biomass_kg_ha=leaf+stem+dead+grain,
            yield_kg_ha=grain,growth_kg_ha=actual,water_factor=1.,nutrition_factor=nutrition,leaf_age_c_days=leaf_age,
            leaf_kg_ha=leaf,stem_kg_ha=stem,dead_leaf_kg_ha=dead,stem_reserve_kg_ha=reserve,
            direct_grain_growth_kg_ha=growth_grain,stem_remobilization_kg_ha=remobilization,
            grain_sink_kg_ha=sink,grain_fill_ceiling_kg_ha=ceiling,
            critical_growth_kg_ha=critical_growth,
            crop_carbon_residual_kg_ha=leaf+stem+dead+grain-biomass_before-actual,
            simulation_basis='potential_growth_no_water_or_nutrient_limitation' if nutrition==1. else 'conditional_growth_with_effective_nutrient_limitation_no_water_state'))
    return rows
