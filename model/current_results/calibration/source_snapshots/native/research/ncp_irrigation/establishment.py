"""Seed-layer water availability for the start of crop development.

Sowing depth selects a represented soil layer. Positive extractable water above
its lower limit permits establishment. Multiple domains use field-area mean
water, consistent with the single representative crop population. Layer-average
water is not a measured seed-scale water potential.
"""
from core.hydrology.types import bounded
from core.hydrology.balance import domains


def configuration(inputs,crop_parameters,layers):
    method=crop_parameters.get('germination_water_response','seed_layer_available_water')
    if method not in {'seed_layer_available_water','temperature_only'}:
        raise ValueError('Unknown germination water response')
    depth=bounded(inputs.get('sowing_depth_mm',50.),'sowing_depth_mm',1e-9,
                  sum(layer.thickness_mm for layer in layers))
    total=0.
    for index,layer in enumerate(layers):
        total+=layer.thickness_mm
        if depth<=total:return method,depth,index
    raise ValueError('Sowing depth outside represented soil profile')


def seed_layer_available_water_mm(state,layers,index):
    layer=layers[index]
    return sum(weight*(column.water_mm[index]-layer.wilting_point*layer.thickness_mm)
               for column,weight in domains(state))


def sowing_stage(crop,calendar_stage):
    return dict(bbch=0,stage='BBCH0' if crop=='wheat' else 'VS',thermal_progress=0.,
                cumulative_thermal_time_c=0.,development_stage=0.,
                vernalization_factor=0. if crop=='wheat' else 1.,
                photoperiod_factor=calendar_stage['photoperiod_factor'])
