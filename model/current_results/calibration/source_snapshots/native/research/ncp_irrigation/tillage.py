"""Prescribed vertical water mixing without an external water flux.

The operation does not change soil hydraulic properties, simulate residue
incorporation or exchange water between wet and dry field domains. Mixing
depth is an independently supplied management input, not an inferred state.
"""
from copy import deepcopy
from datetime import date
from core.hydrology.types import bounded


def event_schedule(inputs):
    raw_events=inputs.get('tillage_events',[])
    if not isinstance(raw_events,list):
        raise ValueError('tillage_events must be a list')
    start,end=date.fromisoformat(inputs['start_date']),date.fromisoformat(inputs['end_date'])
    total_depth=sum(layer['thickness_mm'] for layer in inputs['soil_layers'])
    events,ids={},set()
    for event in raw_events:
        if not isinstance(event,dict) or set(event)!={'event_id','date','depth_mm'}:
            raise ValueError('tillage event requires exactly event_id, date and depth_mm')
        event_id=event['event_id']
        if not isinstance(event_id,str) or not event_id or event_id in ids:
            raise ValueError('Unique tillage event_id required')
        ids.add(event_id)
        try:
            day=date.fromisoformat(event['date'])
        except (TypeError,ValueError) as exc:
            raise ValueError('tillage date must be an ISO date') from exc
        if not start<=day<=end:
            raise ValueError('tillage outside season')
        bounded(event['depth_mm'],'tillage depth',1e-9,total_depth)
        events.setdefault(event['date'],[]).append(dict(event))
    return events


def mix_water(state,layers,depth_mm):
    """Equalize theta in the mixed portions, subject to each pore limit.

    An intersected layer retains its unmixed water. A bounded common theta
    conserves water even when mixed layers have different pore capacities.
    Returned states are independent of the supplied state.
    """
    depth=bounded(depth_mm,'tillage depth',1e-9,sum(layer.thickness_mm for layer in layers))
    overlap=[];top=0.
    for layer in layers:
        overlap.append(max(0.,min(layer.thickness_mm,depth-top)))
        top+=layer.thickness_mm
    result=deepcopy(state)
    for column in [result.wet]+([result.dry] if result.dry is not None else []):
        if len(column.water_mm)!=len(layers):
            raise ValueError('tillage water profile length mismatch')
        theta=[water/layer.thickness_mm for water,layer in zip(column.water_mm,layers)]
        for value,layer in zip(theta,layers):
            bounded(value,'tillage initial theta',layer.air_dry,layer.saturation)
        target=sum(value*dz for value,dz in zip(theta,overlap))
        low=min(layer.air_dry for layer,dz in zip(layers,overlap) if dz>0.)
        high=max(layer.saturation for layer,dz in zip(layers,overlap) if dz>0.)
        for _ in range(80):
            level=(low+high)/2.
            water=sum(min(layer.saturation,max(layer.air_dry,level))*dz for layer,dz in zip(layers,overlap))
            if water<target:low=level
            else:high=level
        level=(low+high)/2.
        column.water_mm=[water+(min(layer.saturation,max(layer.air_dry,level))-old)*dz
            for water,old,layer,dz in zip(column.water_mm,theta,layers,overlap)]
    if abs(result.storage_mm()-state.storage_mm())>1e-8:
        raise ArithmeticError('tillage failed to conserve water')
    return result
