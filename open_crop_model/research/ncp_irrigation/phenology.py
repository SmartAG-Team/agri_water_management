"""Optional developmental responses; legacy process defaults remain unchanged.

Stage-specific wheat separates early vegetative growth from the environmental
modifiers of reproductive development. Subdaily maize uses a sinusoidal
temperature proxy; it is not an observed hourly temperature series.
"""
from math import isfinite,pi,sin
from crops.maize.config import phen_config as MAIZE_CONFIG


def wheat_development_factor(stage,photo,vern,response='legacy'):
    if response not in {'legacy','stage_specific'}:
        raise ValueError('Unknown wheat development_response')
    if response=='stage_specific' and stage<21:return 1.0
    return photo*vern if stage<31 else photo if stage<61 else 1.0


def maize_thermal_time(tmin,tmax,options):
    allowed={'stage_thermal_time_c','temperature_response','temperature_method'}
    if set(options)-allowed:raise ValueError('Unknown maize phenology option')
    method=options.get('temperature_method','daily_mean')
    if method not in {'daily_mean','subdaily'}:raise ValueError('Unknown maize temperature_method')
    defaults=MAIZE_CONFIG['MAIZE_CARDINAL_TEMPERATURES']
    card=options.get('temperature_response',dict(base=defaults['base_temperature'],
        optimal=defaults['optimal_temperature'],maximum=defaults['maximum_temperature']))
    if not isinstance(card,dict) or set(card)!={'base','optimal','maximum'}:
        raise ValueError('Temperature response requires base, optimal and maximum')
    try:base,optimum,maximum=(float(card[k]) for k in ['base','optimal','maximum'])
    except (TypeError,ValueError) as exc:raise ValueError('Invalid cardinal temperatures') from exc
    if not all(isfinite(x) for x in [base,optimum,maximum]) or not base<optimum<maximum:
        raise ValueError('Cardinal temperatures must be finite and ordered')
    if not isfinite(tmin) or not isfinite(tmax) or tmin>tmax:
        raise ValueError('Invalid daily temperature bounds')
    mean=(tmin+tmax)/2
    temperatures=[mean] if method=='daily_mean' else [mean+(tmax-tmin)/2*sin(2*pi*(i+.5)/8) for i in range(8)]
    def response(temp):
        if temp<=base or temp>=maximum:return 0.
        return temp-base if temp<=optimum else (maximum-temp)*(optimum-base)/(maximum-optimum)
    return sum(response(temp) for temp in temperatures)/len(temperatures)
