"""Conditional crop demand plus two-stage evaporation from exposed topsoil.

FAO-56 chapter 7 supplies the REW/TEW reduction and exposed-area ceiling.
This is a surface compartment, not a root-zone water balance. A calibrated
basal diffusive flux represents deeper supply and is outside that compartment.
Absent irrigation is explicitly a zero-irrigation scenario. Kcmax is fixed or
supplied, without an unmeasured crop-height climate correction.
https://www.fao.org/4/x0490e/x0490e0c.htm
"""
from math import exp,isfinite
import numpy as np
from .effective_et import daily_features

VERSIONS={'surface_energy':('energy_partition',2),'surface_phase_energy':('energy_weather_partition',6)}


def prepare_surface_forcing(weather,canopy,*,crop,version='surface_energy',extinction=.65,
                            latitude_deg=None,elevation_m=None,vapour_pressure_policy='reject'):
    if version not in VERSIONS:raise ValueError('Unknown surface ET version')
    legacy,n=VERSIONS[version]
    rows=daily_features(weather,canopy,crop=crop,version=legacy,extinction=extinction,
        latitude_deg=latitude_deg,elevation_m=elevation_m,vapour_pressure_policy=vapour_pressure_policy)
    from .effective_et import FEATURES
    names=FEATURES[legacy][:n];rain=[];irrigation=[]
    for w,c in zip(weather,canopy):
        for key,output in [('precipitation_mm',rain),('irrigation_mm',irrigation)]:
            value=float(w.get(key,0.) if key=='irrigation_mm' else w[key])
            if not isfinite(value) or not 0.<=value<=1000.:raise ValueError('Invalid surface wetting')
            output.append(value)
        bbch=float(c['bbch'])
        if not isfinite(bbch) or not 0.<=bbch<=100.:raise ValueError('Invalid canopy stage')
    return dict(version=version,crop=crop,dates=[r['date'] for r in rows],lai=[r['lai'] for r in rows],
        reference_et0_mm=np.array([r['reference_et0_mm'] for r in rows]),
        canopy_features=np.array([[r[k] for k in names] for r in rows],dtype=float).reshape(len(rows),n),
        exposed_fraction=np.array([exp(-extinction*r['lai']) for r in rows]),
        rain=np.array(rain),irrigation=np.array(irrigation),
        irrigation_assumed_zero=['irrigation_mm' not in w for w in weather])


def validate_parameters(parameters):
    version=parameters['version']
    if version not in VERSIONS:raise ValueError('Unknown surface ET version')
    coefficients=parameters['coefficients'];n=VERSIONS[version][1]
    if len(coefficients)!=n+1 or any(not isfinite(float(v)) or not 0.<=v<=3. for v in coefficients[:-1]):
        raise ValueError('Invalid canopy coefficients')
    if not isfinite(float(coefficients[-1])) or not 0.<=coefficients[-1]<=.3:raise ValueError('Invalid basal diffusion coefficient')
    s=parameters['surface']
    if set(s)!={'tew_mm','rew_mm','initial_depletion_fraction','kc_max'}:raise ValueError('Invalid surface keys')
    if any(not isfinite(float(v)) for v in s.values()):raise ValueError('Nonfinite surface parameters')
    if not 1.<=s['tew_mm']<=60. or not 0.<=s['rew_mm']<s['tew_mm']:raise ValueError('Invalid evaporation capacities')
    if not 0.<=s['initial_depletion_fraction']<=1. or not 1.<=s['kc_max']<=1.5:raise ValueError('Invalid surface initial state or demand ceiling')


def surface_fluxes(prepared,parameters,*,diagnostics=True):
    """Consume validated forcing; fast ET uses the identical state recurrence."""
    validate_parameters(parameters)
    if prepared['version']!=parameters['version']:raise ValueError('Prepared forcing version mismatch')
    s=parameters['surface'];tew=s['tew_mm'];rew=s['rew_mm'];maximum=s['kc_max']
    storage=tew*(1.-s['initial_depletion_fraction'])
    transp=prepared['canopy_features']@np.asarray(parameters['coefficients'][:-1])
    et0=prepared['reference_et0_mm'];few=prepared['exposed_fraction']
    basal=parameters['coefficients'][-1]*et0*few
    results=[]
    for i,(t,b,demand,area,rain,irrigation) in enumerate(zip(transp,basal,et0,few,prepared['rain'],prepared['irrigation'])):
        before=storage;wetting=rain+irrigation;drainage=max(0.,before+wetting-tew)
        storage=min(tew,before+wetting);depletion=tew-storage
        kr=1. if depletion<=rew else storage/(tew-rew)
        kcb=(t+b)/demand if demand>0. else 0.
        ke=min(kr*max(0.,maximum-kcb),maximum*area)
        event=min(storage*area,ke*demand) if area>0. else 0.
        local=event/area if area>0. else 0.;storage=max(0.,storage-local)
        total=t+b+event
        if not diagnostics:results.append(float(total));continue
        results.append(dict(date=prepared['dates'][i],lai=prepared['lai'][i],reference_et0_mm=float(demand),
            exposed_fraction=float(area),transpiration_mm=float(t),basal_diffusion_mm=float(b),
            event_evaporation_mm=float(event),soil_evaporation_mm=float(b+event),et_mm=float(total),
            kr=float(kr),ke=float(ke),kcb=float(kcb),depletion_start_mm=float(tew-before),
            depletion_after_wetting_mm=float(depletion),depletion_end_mm=float(tew-storage),
            precipitation_mm=float(rain),irrigation_mm=float(irrigation),wetting_mm=float(wetting),drainage_mm=float(drainage),
            surface_water_balance_error_mm=float(storage-before-wetting+drainage+local),
            irrigation_assumed_zero=prepared['irrigation_assumed_zero'][i],
            basis='conditional_surface_depletion_ET_no_root_zone_balance'))
    return results


def simulate_surface_et(weather,canopy,parameters,*,crop,extinction=.65,latitude_deg=None,elevation_m=None,
                        vapour_pressure_policy=None):
    forcing=parameters.get('forcing_context',{})
    prepared=prepare_surface_forcing(weather,canopy,crop=crop,version=parameters['version'],extinction=extinction,
        latitude_deg=latitude_deg,elevation_m=elevation_m,
        vapour_pressure_policy=vapour_pressure_policy or forcing.get('vapour_pressure_policy','reject'))
    return surface_fluxes(prepared,parameters)
