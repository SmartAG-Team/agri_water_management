"""Shared optional canopy processes for potential and water-coupled growth.

Capacity constrains new leaf allocation, conserving assimilated carbon. Existing
cards retain legacy behavior. Cold turnover is an effective wheat process and
is not a mechanistic frost-injury model.
"""
from math import exp, isclose
from copy import deepcopy
from core.hydrology.types import bounded
from core.crop_model import daily_engine as engine
from .stage_canopy import validate_curve, stage_sla, stage_senescence


def transpiration_efficiency(weather, coefficient_kpa, method='temperature_proxy'):
    """Explicit VPD driver and aboveground kg ha-1 per mm water.

    Coefficient units are kPa kg biomass per kg water; 1 mm is 10000
    kg water ha-1. Roots are outside this aboveground carbon pool. The
    humidity option uses FAO56 daily mean saturation pressure, not measured
    daytime leaf-to-air VPD. A declared 0.05-kPa floor regularizes near-zero
    daily deficits; the legacy temperature proxy retains its 0.01-kPa floor.
    """
    coefficient=bounded(coefficient_kpa,'normalized transpiration efficiency',1e-12,.1)
    tmin=bounded(weather['tmin_c'],'minimum temperature',-100.,100.)
    tmax=bounded(weather['tmax_c'],'maximum temperature',-100.,100.)
    if tmax<tmin:raise ValueError('Maximum temperature below minimum')
    saturation=lambda t:.61078*exp(17.269*t/(237.3+t))
    if method=='temperature_proxy':
        vpd=max(.01,.75*(saturation(tmax)-saturation(tmin)))
    elif method=='daily_vapour_pressure':
        pressure=bounded(weather.get('actual_vapour_pressure_kpa'),'actual vapour pressure',0.)
        if pressure>saturation(tmax)+1e-6:
            raise ValueError('Actual vapour pressure exceeds maximum-temperature saturation')
        vpd=max(.05,(saturation(tmax)+saturation(tmin))/2.-pressure)
    else:
        raise ValueError('Unknown transpiration VPD method')
    return vpd,10000.*coefficient/vpd


def dual_resource_growth(radiative_growth, transpiration, interception_evaporation,
                         potential_transpiration, efficiency_kg_ha_mm,
                         effective_remaining_demand=None):
    """Partition a day into wet-canopy and dry-canopy energy shares.

    Interception substitutes for transpiration in the water solver. Its wet
    share retains radiative production; dry production requires both carbon
    supply and actual transpiration. This daily partition is an OCM assumption.
    """
    radiation=bounded(radiative_growth,'radiative growth',0.)
    water=bounded(transpiration,'actual transpiration',0.)
    interception=bounded(interception_evaporation,'canopy evaporation',0.)
    demand=bounded(potential_transpiration,'potential transpiration',0.)
    efficiency=bounded(efficiency_kg_ha_mm,'transpiration efficiency',1e-12)
    if demand==0.:return radiation
    remaining=(max(0.,demand-interception) if effective_remaining_demand is None else
               bounded(effective_remaining_demand,'remaining transpiration demand',0.))
    # The water solver accumulates the remaining demand with substep clamping;
    # total interception can exceed the amount that displaced that demand.
    wet=max(0.,min(1.,1.-remaining/demand))
    return min(radiation,radiation*wet+min(radiation*(1.-wet),water*efficiency))


def water_stressed_assimilation(potential_growth, water_factor, nutrition_factor, exponent=1.):
    """Effective carbon response, distinct from leaf-expansion sensitivity.

    The default is the archived linear response. This is an empirical crop
    sensitivity rather than leaf gas exchange or a transpiration-efficiency
    model; zero realized supply cannot support dry-canopy production.
    """
    potential=bounded(potential_growth,'potential growth',0.)
    water=bounded(water_factor,'assimilation water factor',0.,1.)
    nutrient=bounded(nutrition_factor,'assimilation nutrition factor',0.,1.)
    exponent=bounded(exponent,'assimilation water stress exponent',.25,2.)
    return potential*(water**exponent)*nutrient


def resolve_growth_parameters(parameters,site=None,cultivar=None):
    """Resolve effective partial-pooling cards before invoking either simulator.

    Unknown sites use the common card. Site adjustments describe conditional
    fits; they do not identify irrigation, cultivar or measurement mechanisms.
    """
    if 'shared' not in parameters:return deepcopy(parameters)
    if set(parameters)-{'shared','site_adjustments','cultivar_adjustments'}:raise ValueError('Unknown pooled growth key')
    result=deepcopy(parameters['shared'])
    cultivars=parameters.get('cultivar_adjustments',{})
    if not isinstance(cultivars,dict):raise ValueError('Cultivar adjustments must be a mapping')
    ranges={'rue_g_mj':(1e-9,10.),'grain_number_multiplier':(1e-9,10.),
            'leaf_allocation_multiplier':(.1,2.),'transpiration_coefficient':(.1,2.)}
    for name,traits in cultivars.items():
        if not isinstance(name,str) or not name.strip():raise ValueError('Cultivar names must be nonempty strings')
        if not isinstance(traits,dict) or set(traits)-set(ranges):raise ValueError('Unknown cultivar adjustment')
        for key,value in traits.items():bounded(value,key,*ranges[key])
    selected=cultivars.get(cultivar,{})
    if 'rue_g_mj' in selected:result['profile']['rue_g_mj']=selected['rue_g_mj']
    if 'transpiration_coefficient' in selected:
        result['transpiration_coefficient']=selected['transpiration_coefficient']
    if 'grain_number_multiplier' in selected:
        result['profile']['kernels_per_g_flowering_biomass']*=selected['grain_number_multiplier']
    if 'leaf_allocation_multiplier' in selected:
        process=result.setdefault('growth_process',{})
        if 'early_leaf_allocation_multiplier' in process or 'late_leaf_allocation_multiplier' in process:
            process['early_leaf_allocation_multiplier']=selected['leaf_allocation_multiplier']
            process['late_leaf_allocation_multiplier']=selected['leaf_allocation_multiplier']
        else:process['leaf_allocation_multiplier']=selected['leaf_allocation_multiplier']
    adjustments=parameters.get('site_adjustments',{}).get(site,{})
    if set(adjustments)-{'rue_multiplier','sla_multiplier','sla_by_stage'}:raise ValueError('Unknown site adjustment')
    for name,value in adjustments.items():
        if name=='sla_by_stage':
            validate_curve(value,'site-specific SLA',.001,.1)
            result['growth_process']['sla_by_stage']=deepcopy(value)
            continue
        bounded(value,name,1e-9,10.)
        fields=['rue_g_mj'] if name=='rue_multiplier' else ['sla_max_m2_g','sla_min_m2_g']
        if name=='sla_multiplier' and result.get('growth_process',{}).get('version')=='canopy_v5':
            for point in result['growth_process']['sla_by_stage']:point[1]*=value
        else:
            for field in fields:result['profile'][field]*=value
    return result


def resolve_nutrition_factor(inputs,crop_parameters):
    """Resolve explicit effective limitations; no soil N budget is inferred.

    Unmapped management classes retain the scalar assumption. All supplied
    mapping entries are validated even if the active class does not use them.
    """
    scalar=bounded(crop_parameters.get('nutrition_factor',1.),'nutrition_factor',0.,1.)
    mapping=crop_parameters.get('nutrition_by_management',{})
    if not isinstance(mapping,dict):raise ValueError('nutrition_by_management must be a mapping')
    for name,factor in mapping.items():
        if not isinstance(name,str) or not name.strip():raise ValueError('Nutrient management classes must be nonempty strings')
        bounded(factor,'management nutrient factor',0.,1.)
    label=inputs.get('management_class')
    if label is not None and (not isinstance(label,str) or not label.strip()):
        raise ValueError('management_class must be a nonempty string')
    return scalar*mapping.get(label,1.)


def validate_growth_parameters(parameters):
    if not isinstance(parameters,dict):raise ValueError('Growth process parameters must be a mapping')
    if parameters.get('version')=='canopy_v5':
        allowed={'version','radiation_basis','par_fraction','sla_by_stage','senescence_by_stage',
                 'leaf_allocation_multiplier','early_leaf_allocation_multiplier','late_leaf_allocation_multiplier',
                 'leaf_expansion_stress_policy','grain_fill_completion_bbch','cold_turnover_rate',
                 'transpiration_efficiency_kpa','transpiration_vpd_method','transpiration_demand_method',
                 'assimilation_water_stress_exponent','grain_number_basis','grain_set_window_dvs'}
        if set(parameters)-allowed:raise ValueError('canopy_v5 excludes maximum LAI and legacy canopy coefficients')
        if 'transpiration_efficiency_kpa' in parameters:
            bounded(parameters['transpiration_efficiency_kpa'],'normalized transpiration efficiency',1e-12,.1)
        if 'assimilation_water_stress_exponent' in parameters:
            bounded(parameters['assimilation_water_stress_exponent'],'assimilation water stress exponent',.25,2.)
            if 'transpiration_efficiency_kpa' in parameters:
                raise ValueError('Assimilation exponent and transpiration efficiency are mutually exclusive')
        if parameters.get('transpiration_vpd_method','temperature_proxy') not in ('temperature_proxy','daily_vapour_pressure'):
            raise ValueError('Unknown transpiration VPD method')
        if 'transpiration_vpd_method' in parameters and 'transpiration_efficiency_kpa' not in parameters:
            raise ValueError('Transpiration VPD method requires an efficiency coefficient')
        if parameters.get('transpiration_demand_method','carbon_requirement') not in ('carbon_requirement','atmospheric'):
            raise ValueError('Unknown transpiration demand method')
        if 'transpiration_demand_method' in parameters and 'transpiration_efficiency_kpa' not in parameters:
            raise ValueError('Transpiration demand method requires an efficiency coefficient')
        basis=parameters.get('grain_number_basis','flowering_biomass')
        if basis not in ('flowering_biomass','critical_growth'):
            raise ValueError('Unknown grain number basis')
        if basis=='critical_growth':
            window=parameters.get('grain_set_window_dvs')
            if not isinstance(window,(list,tuple)) or len(window)!=2:
                raise ValueError('Critical grain set needs two development bounds')
            start=bounded(window[0],'kernel set window start',0.,2.)
            end=bounded(window[1],'kernel set window end',0.,2.)
            if not start<1.<end:
                raise ValueError('Kernel set window must bracket flowering')
        elif 'grain_set_window_dvs' in parameters:
            raise ValueError('Kernel set window requires critical growth basis')
        if parameters.get('radiation_basis')!='intercepted_par':raise ValueError('canopy_v5 RUE requires intercepted PAR')
        bounded(parameters.get('par_fraction'),'PAR fraction',.3,.6)
        validate_curve(parameters.get('sla_by_stage'),'specific leaf area',.001,.1)
        validate_curve(parameters.get('senescence_by_stage'),'thermal senescence',0.,.02)
        limits={'leaf_allocation_multiplier':(.1,2.),'early_leaf_allocation_multiplier':(.1,2.),
                'late_leaf_allocation_multiplier':(.1,2.),'grain_fill_completion_bbch':(75.,95.),
                'cold_turnover_rate':(0.,.15)}
        for key, bounds in limits.items():
            if key in parameters:bounded(parameters[key],key,*bounds)
        if parameters.get('leaf_expansion_stress_policy','water_nutrition') not in ['temperature_only','water_nutrition']:
            raise ValueError('Unknown leaf expansion stress policy')
        return parameters
    allowed={'version','max_lai','leaf_allocation_multiplier','senescence_multiplier','cold_turnover_rate','leaf_lifespan_c_days','early_leaf_allocation_multiplier','late_leaf_allocation_multiplier','leaf_expansion_stress_policy','grain_fill_completion_bbch','reproductive_rue_multiplier'}
    if set(parameters)-allowed:raise ValueError('Unknown growth process coefficient')
    if parameters.get('version','legacy') not in ['legacy','canopy_v2','canopy_v3','canopy_v4']:raise ValueError('Unknown growth process version')
    if 'leaf_lifespan_c_days' in parameters and parameters.get('version') not in ['canopy_v3','canopy_v4']:
        raise ValueError('Thermal leaf lifespan requires canopy_v3 or canopy_v4')
    if any(k in parameters for k in ['early_leaf_allocation_multiplier','late_leaf_allocation_multiplier']) and parameters.get('version')!='canopy_v4':
        raise ValueError('Phase allocation requires canopy_v4')
    if parameters.get('leaf_expansion_stress_policy','temperature_only') not in ['temperature_only','water_nutrition']:
        raise ValueError('Unknown leaf expansion stress policy')
    if parameters.get('leaf_expansion_stress_policy')=='water_nutrition' and parameters.get('version','legacy')=='legacy':
        raise ValueError('Stress expansion requires a revised canopy version')
    ranges={'max_lai':(1e-9,20.),'leaf_allocation_multiplier':(1e-9,4.),'senescence_multiplier':(1e-9,10.),'cold_turnover_rate':(0.,.15),'leaf_lifespan_c_days':(100.,2000.),'early_leaf_allocation_multiplier':(.1,3.),'late_leaf_allocation_multiplier':(.1,3.),'grain_fill_completion_bbch':(75.,95.),'reproductive_rue_multiplier':(.3,2.5)}
    for key,value in parameters.items():
        if key not in ['version','leaf_expansion_stress_policy']:bounded(value,key,*ranges[key])
    return parameters


def revised(parameters):return parameters.get('version','legacy') in ['canopy_v2','canopy_v3','canopy_v4','canopy_v5']


def validate_grain_parameters(parameters):
    """Validate an optional, explicit nonstructural stem reserve process."""
    if not isinstance(parameters,dict):raise ValueError('Grain process must be a mapping')
    if set(parameters)-{'version','reserve_fraction','release_rate'}:raise ValueError('Unknown grain process coefficient')
    version=parameters.get('version','legacy')
    if version not in ['legacy','stem_reserve_v1']:raise ValueError('Unknown grain process version')
    if version=='legacy' and set(parameters)-{'version'}:raise ValueError('Reserve coefficients require stem_reserve_v1')
    for key,high in [('reserve_fraction',.6),('release_rate',1.)]:
        if key in parameters:bounded(parameters[key],key,0.,high)
    return parameters


def stem_reserve_transfer(*,stem,reserve,stem_growth,demand,parameters,active):
    """Grow stem, then meet grain shortfall from its reserve subset only.

    Stem includes reserves. Transfers conserve total aboveground dry matter;
    structural stem is never available for grain filling. Parameters are
    effective fractions, without a measured soluble-carbohydrate calibration.
    """
    validate_grain_parameters(parameters)
    if parameters.get('version')!='stem_reserve_v1':raise ValueError('Explicit reserve process required')
    stem=bounded(stem,'stem mass',0.)
    reserve=bounded(reserve,'stem reserve',0.,stem)
    stem_growth=bounded(stem_growth,'new stem growth',0.)
    demand=bounded(demand,'unmet grain demand',0.)
    new_stem=stem+stem_growth
    new_reserve=reserve+stem_growth*parameters.get('reserve_fraction',.25)
    transfer=min(demand,new_reserve*parameters.get('release_rate',.1)) if active else 0.
    return dict(stem_kg_ha=new_stem-transfer,reserve_kg_ha=new_reserve-transfer,transfer_kg_ha=transfer)


def phase_rue_multiplier(profile,stage,parameters):
    """An effective reproductive RUE multiplier starts at maize R1/wheat61."""
    reproductive=engine._maize_stage_at_or_after(stage['stage'],'R1') if profile.name=='maize' else stage['bbch']>=61
    return parameters.get('reproductive_rue_multiplier',1.) if reproductive else 1.


def grain_number_from_flowering_biomass(profile, biomass_kg_ha, parameters):
    """Grains per square metre from simulated aboveground flowering biomass.

    The v5 sink uses the biomass coefficient directly. A fixed plant-density
    cap is not a measured ear-density constraint, particularly in tillering
    wheat. Existing cards retain their original stand-based ceiling.
    """
    biomass=bounded(biomass_kg_ha,'flowering aboveground biomass',0.)
    number=biomass*.1*profile.kernels_per_g_flowering_biomass
    if parameters.get('version')=='canopy_v5':return number
    return min(number,profile.default_plant_population_m2*profile.max_grains_per_plant)


def critical_growth_increment(growth_kg_ha,previous_dvs,current_dvs,parameters):
    """Aboveground production allocated to the kernel-set observation window.

    Boundary days use the overlap fraction of simulated development. The
    accumulator describes carbon produced, not an additional carbon pool;
    assimilate allocation and mass conservation remain in the crop solver.
    """
    production=bounded(growth_kg_ha,'critical period growth',0.)
    previous=bounded(previous_dvs,'previous development stage',0.,2.)
    current=bounded(current_dvs,'current development stage',previous,2.)
    if parameters.get('grain_number_basis')!='critical_growth':return 0.
    start,end=parameters['grain_set_window_dvs']
    if current==previous:return production if start<=current<end else 0.
    overlap=max(0.,min(current,end)-max(previous,start))
    return production*overlap/(current-previous)


def grain_set_started(profile, stage, parameters):
    """Align v5 maize grain formation with R1, the flowering DVS anchor."""
    if profile.name=='maize' and parameters.get('version')=='canopy_v5':
        return engine._maize_stage_at_or_after(stage['stage'],'R1')
    return engine._grain_set_window_started(profile=profile,stage_name=stage['stage'],
        thermal_progress=stage['thermal_progress'],bbch=stage['bbch'])


def grain_fill_fraction(profile, progress, bbch, parameters):
    """Optional wheat sink completion at its explicit ripeness boundary.

    The original OCM curve completes at BBCH95, after the research wheat
    simulator stops assimilation at BBCH89. The explicit option aligns these
    boundaries without changing maize, old cards, carbon supply or sink size.
    """
    if profile.name=='maize' and parameters.get('version')=='canopy_v5':
        start=engine._stage_progress_threshold(profile,'R1')
        end=engine._stage_progress_threshold(profile,'R6')
        fraction=max(0.,min(1.,(progress-start)/(end-start)))
        return fraction*fraction*(3.-2.*fraction)
    if profile.name != 'wheat' or 'grain_fill_completion_bbch' not in parameters:
        return engine._grain_fill_fraction_for_profile(profile, progress, bbch)
    end = bounded(parameters['grain_fill_completion_bbch'], 'grain fill completion', 75., 95.)
    fraction = max(0., min(1., (bbch-61.)/(end-61.)))
    return fraction*fraction*(3.-2.*fraction)


def has_emerged(stage):
    # Maize displays rounded BBCH9 before its categorical VE threshold.
    # VS remains pre-emergence even when that rounded display value is nine.
    return stage.get('stage')!='VS' and stage['bbch']>=9


def leaf_update(*,lai,leaf,vegetative_growth,profile,stage,temperature,stress,parameters,leaf_age_c_days=0.,leaf_cohorts=None,water_factor=1.,nutrition_factor=1.):
    progress=stage['thermal_progress'];bbch=stage['bbch']
    allocation=engine._leaf_partition_fraction_for_profile(profile,progress,bbch)
    senescence=engine._leaf_senescence_fraction_for_profile(profile,progress,bbch,stress)
    sla=engine._specific_leaf_area_for_profile(profile,progress,bbch)
    if parameters.get('version')=='canopy_v5':
        sla=stage_sla(parameters,stage)
        senescence=stage_senescence(parameters,stage,stress,temperature,profile.base_temp_c)
    if parameters.get('version') in ['canopy_v4','canopy_v5'] and profile.name=='maize':
        v6=engine._stage_progress_threshold(profile,'V6');vt=engine._stage_progress_threshold(profile,'VT')
        fraction=min(1.,max(0.,(progress-v6)/(vt-v6)))
        early=parameters.get('early_leaf_allocation_multiplier',1.)
        late=parameters.get('late_leaf_allocation_multiplier',1.)
        allocation*=early+(late-early)*fraction
    if revised(parameters):
        allocation=min(1.,allocation*parameters.get('leaf_allocation_multiplier',1.))
        # Existing OCM leaf-expansion temperature response; excess growth goes
        # to non-leaf tissue rather than disappearing from the carbon balance.
        water=bounded(water_factor,'leaf expansion water factor',0.,1.)
        nutrient=bounded(nutrition_factor,'leaf expansion nutrient factor',0.,1.)
        default='water_nutrition' if parameters.get('version')=='canopy_v5' else 'temperature_only'
        if parameters.get('leaf_expansion_stress_policy',default)=='temperature_only':water=nutrient=1.
        expansion=engine._leaf_expansion_stress(water,nutrient,{'temperature_2m_mean':temperature})
        allocation*=expansion
        senescence*=parameters.get('senescence_multiplier',1.)
        if profile.name=='wheat' and bbch<31:
            senescence+=parameters.get('cold_turnover_rate',0.)*min(1.,max(0.,(5.-temperature)/10.))
        if profile.name=='wheat' and bbch>=89:senescence=1.
        senescence=min(1.,max(0.,senescence))
    aged=leaf_age_c_days+max(temperature,0.)
    if parameters.get('version')=='canopy_v3':
        # Effective area-weighted mean age, not a resolved leaf-cohort model.
        # Thermal turnover precedes reproductive BBCH and slows in cold weather.
        lifespan=parameters.get('leaf_lifespan_c_days',800.)
        age_loss=1.-exp(-(aged/lifespan)**4*max(temperature,0.)/lifespan)
        senescence=1.-(1.-senescence)*(1.-age_loss)
    if parameters.get('version') in ['canopy_v4','canopy_v5']:
        return _cohort_update(lai=lai,leaf=leaf,vegetative_growth=vegetative_growth,allocation=allocation,
            sla=sla,temperature=temperature,background_senescence=senescence,profile=profile,
            parameters=parameters,leaf_cohorts=leaf_cohorts,initial_age=leaf_age_c_days)
    remaining_lai=lai*(1-senescence)
    leaf_growth=vegetative_growth*allocation
    if revised(parameters):
        capacity=parameters.get('max_lai',profile.max_lai)
        leaf_growth=min(leaf_growth,max(0.,capacity-remaining_lai)/(.1*sla))
    loss=leaf*senescence
    new_lai=max(0.,remaining_lai+leaf_growth*.1*sla)
    next_age=aged*remaining_lai/new_lai if new_lai>0. else 0.
    return dict(lai=new_lai,leaf_kg_ha=leaf-loss+leaf_growth,leaf_age_c_days=next_age,
        leaf_growth_kg_ha=leaf_growth,non_leaf_growth_kg_ha=vegetative_growth-leaf_growth,
        dead_leaf_increment_kg_ha=loss,senescence_fraction=senescence)


def _cohort_update(*,lai,leaf,vegetative_growth,allocation,sla,temperature,background_senescence,
        profile,parameters,leaf_cohorts,initial_age):
    """Age each birth cohort without diluting old-leaf hazards by new growth.

    State tuples hold LAI, dry mass (kg/ha), and accumulated positive degree
    days. Age is diagnostic after aggregation; mortality uses cohort ages.
    """
    cohorts=[(lai,leaf,initial_age)] if leaf_cohorts is None else leaf_cohorts
    for cohort in cohorts:
        if len(cohort)!=3:raise ValueError('Leaf cohort must contain area, mass and age')
        for value,name in zip(cohort,['cohort area','cohort mass','cohort age']):bounded(value,name,0.)
    if not isclose(sum(c[0] for c in cohorts),lai,rel_tol=1e-9,abs_tol=1e-9) or not isclose(sum(c[1] for c in cohorts),leaf,rel_tol=1e-9,abs_tol=1e-9):
        raise ValueError('Leaf cohort totals disagree with canopy state')
    increment=max(temperature,0.)
    lifespan=parameters.get('leaf_lifespan_c_days',800. if profile.name=='wheat' and parameters.get('version')!='canopy_v5' else None)
    remaining=[]
    for area,mass,age in cohorts:
        aged=age+increment
        survival=1.-background_senescence
        if lifespan is not None:survival*=exp(-(aged/lifespan)**4*increment/lifespan)
        # Exact exhausted cohorts can be removed; small surviving cohorts are
        # retained, preserving mass rather than imposing a numerical cutoff.
        if survival>0. and (area>0. or mass>0.):remaining.append((area*survival,mass*survival,aged))
    remaining_area=sum(c[0] for c in remaining);remaining_mass=sum(c[1] for c in remaining)
    growth=vegetative_growth*allocation
    if parameters.get('version')!='canopy_v5':
        capacity=parameters.get('max_lai',profile.max_lai)
        growth=min(growth,max(0.,capacity-remaining_area)/(.1*sla))
    if growth>0.:remaining.append((growth*.1*sla,growth,0.))
    new_area=remaining_area+growth*.1*sla
    mean_age=sum(c[0]*c[2] for c in remaining)/new_area if new_area>0. else 0.
    return dict(lai=new_area,leaf_kg_ha=remaining_mass+growth,leaf_age_c_days=mean_age,leaf_cohorts=remaining,
        leaf_growth_kg_ha=growth,non_leaf_growth_kg_ha=vegetative_growth-growth,
        dead_leaf_increment_kg_ha=leaf-remaining_mass,
        senescence_fraction=1.-remaining_area/lai if lai>0. else 0.)
