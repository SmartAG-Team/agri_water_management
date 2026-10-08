"""Conditional daily ET from green canopy and weather.

This diagnostic pathway does not estimate field irrigation or close a root-zone
water balance. Rain memory is a surface-wetness proxy, not measured depletion.
Grain maturity does not turn off transpiration from a remaining green canopy.
"""
from math import exp,isfinite
from datetime import date,timedelta
from core.hydrology.evapotranspiration import reference_et0_components

FEATURES={
    'live_canopy':['green_canopy','exposed_soil'],
    'weather_partition':['green_early','green_peak','green_late','dry_soil','rain_soil'],
    'energy_partition':['green_radiation','green_aerodynamic','soil_radiation','soil_aerodynamic'],
    'energy_weather_partition':['green_rad_early','green_rad_peak','green_rad_late',
                               'green_aero_early','green_aero_peak','green_aero_late','dry_soil','rain_soil'],
}


def daily_features(weather,canopy,*,crop,version='live_canopy',extinction=.65,
                   latitude_deg=None,elevation_m=None,vapour_pressure_policy='reject',
                   reference_et0_source='provided',wind_height_m=None,irrigation_events=None):
    if crop not in ('wheat','maize') or version not in FEATURES:
        raise ValueError('Unknown crop or effective ET version')
    if len(weather)!=len(canopy) or any(w['date']!=c['date'] for w,c in zip(weather,canopy)):
        raise ValueError('Weather and canopy calendar must match')
    try:
        dates=[date.fromisoformat(w['date']) for w in weather]
    except (TypeError,ValueError) as exc:
        raise ValueError('Effective ET calendar requires ISO dates') from exc
    if any(b-a!=timedelta(days=1) for a,b in zip(dates,dates[1:])):
        raise ValueError('Effective ET calendar must be consecutive and ordered')
    wetting={}
    if irrigation_events is not None:
        if version not in ('weather_partition','energy_weather_partition'):
            raise ValueError('Recorded wetting requires a surface-wetness ET structure')
        for event in irrigation_events:
            try:
                day=date.fromisoformat(event['date'])
                amount=float(event['surface_wetting_mm'])
            except (KeyError,TypeError,ValueError) as exc:
                raise ValueError('Explicit dated surface_wetting_mm required') from exc
            if day not in dates or day in wetting or not isfinite(amount) or amount<0.:
                raise ValueError('Unique in-season nonnegative surface wetting required')
            wetting[day]=amount
    if not isfinite(extinction) or extinction<=0.:
        raise ValueError('Invalid extinction coefficient')
    if reference_et0_source not in ('provided','fao56'):
        raise ValueError('Unknown reference ET0 source')
    energy=version.startswith('energy_');use_drivers=energy or reference_et0_source=='fao56'
    if use_drivers and (latitude_deg is None or elevation_m is None):
        raise ValueError('FAO drivers require explicit latitude and elevation')
    rows=[];rain_memory=0.
    for day,w,c in zip(dates,weather,canopy):
        drivers=None;resolved_height=None
        if use_drivers:
            if 'wind_speed_m_s' in w:
                if wind_height_m is None:raise ValueError('Generic wind requires explicit measurement height')
                speed=w['wind_speed_m_s'];resolved_height=wind_height_m
            else:
                speed=w.get('wind_speed_10m_m_s');resolved_height=10. if wind_height_m is None else wind_height_m
                if resolved_height!=10.:raise ValueError('Named 10m wind contradicts explicit measurement height')
            drivers=reference_et0_components(dict(w,wind_speed_m_s=speed),
                latitude_deg=latitude_deg,elevation_m=elevation_m,wind_height_m=resolved_height,
                vapour_pressure_policy=vapour_pressure_policy)
        try:
            et0=drivers['reference_et0_mm'] if reference_et0_source=='fao56' else float(w['reference_et0_mm'])
            lai=float(c['lai'])
        except (KeyError,TypeError,ValueError) as exc:raise ValueError('Finite reference ET0 and LAI required') from exc
        if not isfinite(et0) or not isfinite(lai) or et0<0. or lai<0.:
            raise ValueError('Nonnegative finite ET0 and LAI required')
        cover=1.-exp(-extinction*lai)
        transp=et0*cover;soil=et0*(1.-cover)
        if energy:
            total=drivers['reference_et0_mm']
            radiation=min(total,max(0.,drivers['radiation_mm']))
            aerodynamic=total-radiation
            # A nonzero supplied ET0 can coexist with a zero recomputed total
            # (different radiation/pressure products). Positive raw demand still
            # determines the share; no weather or ET magnitude is invented.
            if total<=1e-12:
                radiation=max(0.,drivers['radiation_mm'])
                aerodynamic=max(0.,drivers['aerodynamic_mm'])
                total=radiation+aerodynamic
            if total<=1e-12 and et0>0.:
                raise ValueError('Positive supplied ET0 without any physical energy driver')
            share=radiation/total if total>1e-12 else 0.
            rad=et0*share;aero=et0-rad
        if version=='live_canopy':
            features=[transp,soil]
        elif version=='energy_partition':
            features=[rad*cover,aero*cover,rad*(1.-cover),aero*(1.-cover)]
        else:
            rain=float(w['precipitation_mm']);temperature=(float(w['tmin_c'])+float(w['tmax_c']))/2.
            if not isfinite(rain) or rain<0. or not isfinite(temperature):
                raise ValueError('Invalid ET weather')
            rain_memory=rain_memory*exp(-1./3.)+rain+wetting.get(day,0.)
            wetness=1.-exp(-rain_memory/10.)
            cold=max(0.,min(1.,temperature/10.)) if crop=='wheat' else 1.
            bbch=float(c['bbch'])
            lo,peak,end=(9.,51.,89.) if crop=='wheat' else (9.,65.,99.)
            if bbch<=peak:
                peak_weight=max(0.,min(1.,(bbch-lo)/(peak-lo)))
                weights=[1.-peak_weight,peak_weight,0.]
            else:
                late_weight=max(0.,min(1.,(bbch-peak)/(end-peak)))
                weights=[0.,1.-late_weight,late_weight]
            if energy:
                # Temperature already enters reference demand and its energy
                # shares. Unity response must retain the supplied ET0.
                features=[rad*cover*a for a in weights]+[aero*cover*a for a in weights]
                features += [soil*(1.-wetness),soil*wetness]
            else:
                features=[transp*cold*a for a in weights]+[soil*(1.-wetness),soil*wetness]
        rows.append(dict(date=w['date'],lai=lai,reference_et0_mm=et0,
                         **dict(zip(FEATURES[version],features))))
        rows[-1]['reference_et0_source']=reference_et0_source
        if irrigation_events is not None:
            rows[-1]['recorded_surface_wetting_mm']=wetting.get(day,0.)
            rows[-1]['management_basis']='supplied_wetting_events; completeness_not_inferred'
        if use_drivers:
            rows[-1]['negative_daily_vpd']=drivers['negative_daily_vpd']
            rows[-1]['wind_height_m']=resolved_height
            rows[-1]['net_radiation_mj_m2']=drivers['net_radiation_mj_m2']
    return rows


def simulate_effective_et(weather,canopy,parameters,*,crop,site=None,extinction=.65,
                          latitude_deg=None,elevation_m=None,vapour_pressure_policy=None,
                          reference_et0_source=None,wind_height_m=None,irrigation_events=None):
    version=parameters['version'];coefficients=parameters['coefficients']
    if version not in FEATURES or len(coefficients)!=len(FEATURES[version]):
        raise ValueError('Invalid effective ET coefficient count')
    if any(not isfinite(float(x)) or x<0. or x>3. for x in coefficients):
        raise ValueError('Invalid effective ET coefficient')
    multiplier=parameters.get('site_multipliers',{}).get(site,1.)
    if not isfinite(multiplier) or not .5<=multiplier<=1.5:
        raise ValueError('Invalid effective ET site multiplier')
    forcing=parameters.get('forcing_context',{})
    features=daily_features(weather,canopy,crop=crop,version=version,extinction=extinction,
                            latitude_deg=latitude_deg,elevation_m=elevation_m,
                            vapour_pressure_policy=vapour_pressure_policy or forcing.get('vapour_pressure_policy','reject'),
                            reference_et0_source=reference_et0_source if reference_et0_source is not None else forcing.get('reference_et0_source','provided'),
                            wind_height_m=wind_height_m if wind_height_m is not None else forcing.get('wind_height_m'),
                            irrigation_events=irrigation_events)
    n_transp={'live_canopy':1,'weather_partition':3,'energy_partition':2,'energy_weather_partition':6}[version]
    for row in features:
        terms=[row[name]*coef*multiplier for name,coef in zip(FEATURES[version],coefficients)]
        row['transpiration_mm']=sum(terms[:n_transp]);row['soil_evaporation_mm']=sum(terms[n_transp:])
        row['et_mm']=row['transpiration_mm']+row['soil_evaporation_mm']
        row['basis']='conditional_green_canopy_weather_ET_no_root_zone_water_balance'
        if irrigation_events is not None:
            row['basis']='conditional_green_canopy_weather_recorded_irrigation_ET_no_root_zone_water_balance'
    return features
