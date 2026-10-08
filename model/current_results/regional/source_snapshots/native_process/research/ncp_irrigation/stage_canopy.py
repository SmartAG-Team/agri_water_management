"""Development-stage canopy traits for the uncapped canopy_v5 formulation.

Development stage is zero at emergence, one at flowering and two at maturity.
SLA applies to newly formed leaf dry matter; existing cohorts retain their area.
Radiation-use efficiency has an explicit intercepted-PAR denominator.
"""
from math import isfinite, exp
from core.crop_model import daily_engine as engine
from core.hydrology.types import bounded


def validate_curve(curve, name, lower, upper):
    if not isinstance(curve, (list, tuple)) or len(curve) < 2:
        raise ValueError(f'{name} requires at least two development-stage knots')
    previous = -1.
    for point in curve:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError(f'{name} knots must contain stage and value')
        stage, value = point
        bounded(stage, name+' stage', 0., 2.)
        bounded(value, name+' value', lower, upper)
        if stage <= previous:
            raise ValueError(f'{name} stages must strictly increase')
        previous = stage
    if curve[0][0] != 0. or curve[-1][0] != 2.:
        raise ValueError(f'{name} must cover emergence through maturity (0–2)')
    return curve


def interpolate(curve, development_stage):
    if not isfinite(development_stage):
        raise ValueError('Development stage must be finite')
    return engine._linear_interp(development_stage, [p[0] for p in curve], [p[1] for p in curve])


def stage_sla(parameters, stage):
    return interpolate(parameters['sla_by_stage'], stage['development_stage'])


def stage_senescence(parameters, stage, growth_factor, temperature, base_temperature):
    """Stage-specific thermal mortality plus a separate stress hazard.

    The intrinsic rate is per degree day; no thermal ageing occurs below the
    crop base temperature. Water/nutrient stress remains a daily effective loss.
    The terminal grain stage selects the last thermal-rate knot; it does not
    imply simultaneous death of all leaves. Wheat full ripeness is handled
    separately in leaf_update.
    """
    rate = interpolate(parameters['senescence_by_stage'], stage['development_stage'])
    thermal_time = max(0., temperature-base_temperature)
    survival = exp(-rate*thermal_time)
    stress_rate = .025 * (1. - bounded(growth_factor, 'growth stress factor', 0., 1.))
    return 1. - survival * (1. - stress_rate)


def radiation_fraction(parameters):
    return parameters['par_fraction'] if parameters.get('version') == 'canopy_v5' else 1.


def initial_sla(profile, parameters):
    return parameters['sla_by_stage'][0][1] if parameters.get('version') == 'canopy_v5' else profile.sla_max_m2_g
