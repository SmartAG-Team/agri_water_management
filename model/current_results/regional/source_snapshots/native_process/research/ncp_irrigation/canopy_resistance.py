"""Conditional surface-resistance response with reference aerodynamic geometry.

This pathway tests LAI, light and VPD regulation, without root-zone water stress.
Active LAI = half green LAI is a declared reference-inspired approximation.
Reference: FAO-56 chapter 2, equations 3 and 5. The light/VPD response forms
are exploratory parameterizations, not measured stomatal conductance.
"""
from math import exp,isfinite
from core.hydrology.evapotranspiration import reference_et0_components,wind_to_2m
from core.hydrology.types import bounded
from .effective_et import daily_features

RESISTANCE_FEATURES=('green_radiation','green_aerodynamic','dry_soil','rain_soil')
SURFACE_BOUNDS={'leaf_resistance_s_m':(5.,1000.),'vpd_sensitivity_kpa_inv':(0.,10.),'light_half_mj_m2_day':(0.,30.)}


def validate_surface(surface):
    if not isinstance(surface,dict) or set(surface)!=set(SURFACE_BOUNDS):
        raise ValueError('Surface requires exactly leaf resistance, VPD sensitivity and light half response')
    return {key:bounded(surface[key],key,*bounds) for key,bounds in SURFACE_BOUNDS.items()}


def resistance_features(weather,canopy,surface,*,crop,extinction=.65,latitude_deg=None,elevation_m=None,
                        reference_et0_source='provided',wind_height_m=None,vapour_pressure_policy='reject'):
    s=validate_surface(surface)
    base=daily_features(weather,canopy,crop=crop,version='energy_weather_partition',extinction=extinction,
        latitude_deg=latitude_deg,elevation_m=elevation_m,reference_et0_source=reference_et0_source,
        wind_height_m=wind_height_m,vapour_pressure_policy=vapour_pressure_policy)
    rows=[]
    for w,row in zip(weather,base):
        speed=w['wind_speed_m_s'] if 'wind_speed_m_s' in w else w['wind_speed_10m_m_s']
        height=row['wind_height_m']
        drivers=reference_et0_components(dict(w,wind_speed_m_s=speed),latitude_deg=latitude_deg,elevation_m=elevation_m,
            wind_height_m=height,vapour_pressure_policy=vapour_pressure_policy)
        temp=(w['tmin_c']+w['tmax_c'])/2.;u2=wind_to_2m(speed,height)
        pressure=w.get('pressure_kpa',101.3*((293.-.0065*elevation_m)/293.)**5.26)
        gamma=.000665*pressure;vapour=.6108*exp(17.27*temp/(temp+237.3));delta=4098.*vapour/(temp+237.3)**2
        rs=w['solar_radiation_mj_m2'];half=s['light_half_mj_m2_day']
        light=1. if half==0. else rs/(rs+half)
        active=.5*row['lai'];vpd=drivers['vapour_pressure_deficit_kpa']
        resistance=None;response=0.
        if active>0. and light>0.:
            resistance=s['leaf_resistance_s_m']*(1.+s['vpd_sensitivity_kpa_inv']*vpd)/light/active
            # Retain the same rounded grass-reference denominator as native
            # FAO56; rs=70 therefore recovers its exact radiative/aero shares.
            response=(delta+gamma*(1.+.34*u2))/(delta+gamma*(1.+.34*u2*resistance/70.))
        radiation=sum(row[k] for k in ['green_rad_early','green_rad_peak','green_rad_late'])
        aerodynamic=sum(row[k] for k in ['green_aero_early','green_aero_peak','green_aero_late'])
        rows.append(dict(date=row['date'],lai=row['lai'],reference_et0_mm=row['reference_et0_mm'],reference_et0_source=row['reference_et0_source'],
            wind_height_m=height,net_radiation_mj_m2=row['net_radiation_mj_m2'],negative_daily_vpd=row['negative_daily_vpd'],
            vapour_pressure_deficit_kpa=vpd,light_response=light,active_lai=active,canopy_resistance_s_m=resistance,resistance_multiplier=response,
            green_radiation=radiation*response,green_aerodynamic=aerodynamic*response,dry_soil=row['dry_soil'],rain_soil=row['rain_soil']))
    return rows


def simulate_resistance_et(weather,canopy,parameters,*,crop,site=None,extinction=.65,latitude_deg=None,elevation_m=None,
                           reference_et0_source=None,wind_height_m=None,vapour_pressure_policy=None):
    if parameters.get('version')!='canopy_resistance':raise ValueError('Unknown resistance ET version')
    coefficients=parameters['coefficients']
    if len(coefficients)!=4 or any(not isfinite(float(x)) or not 0.<=x<=3. for x in coefficients):raise ValueError('Invalid resistance ET coefficients')
    multiplier=parameters.get('site_multipliers',{}).get(site,1.)
    if not isfinite(multiplier) or not .5<=multiplier<=1.5:raise ValueError('Invalid effective ET site multiplier')
    forcing=parameters.get('forcing_context',{})
    rows=resistance_features(weather,canopy,parameters['surface'],crop=crop,extinction=extinction,latitude_deg=latitude_deg,elevation_m=elevation_m,
        reference_et0_source=reference_et0_source if reference_et0_source is not None else forcing.get('reference_et0_source','provided'),
        wind_height_m=wind_height_m if wind_height_m is not None else forcing.get('wind_height_m'),
        vapour_pressure_policy=vapour_pressure_policy or forcing.get('vapour_pressure_policy','reject'))
    for row in rows:
        terms=[row[key]*float(coef)*multiplier for key,coef in zip(RESISTANCE_FEATURES,coefficients)]
        row['transpiration_mm']=sum(terms[:2]);row['soil_evaporation_mm']=sum(terms[2:]);row['et_mm']=sum(terms)
        row['basis']='conditional_canopy_surface_resistance_no_root_water_balance'
    return rows
