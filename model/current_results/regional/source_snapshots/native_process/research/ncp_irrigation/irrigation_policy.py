"""Causal field-boundary irrigation from beginning-of-day root-zone water.

Relative availability is integrated water above WP divided by integrated
FC-WP capacity. Layer intersections use field-mean inherited water; neither
current-day rain nor forecasts or target observations enter the decision.
The requested amount is gross field-boundary water, without a guessed
application efficiency. The hydrology kernel partitions application losses.
"""

from datetime import date

from core.hydrology.adapters import field_water_mm
from core.hydrology.types import bounded


def validate_policy(policy: dict) -> dict:
    """Validate a fully explicit policy without modifying its input."""
    fields = {'kind', 'threshold_fraction', 'target_fraction', 'max_depth_mm',
              'seasonal_cap_mm', 'min_interval_days', 'start_bbch', 'end_bbch'}
    if not isinstance(policy, dict) or set(policy) != fields:
        raise ValueError('irrigation_policy requires exactly the documented policy fields')
    if policy['kind'] != 'root_zone_threshold':
        raise ValueError('Unknown irrigation policy kind')
    for key in ['threshold_fraction', 'target_fraction']:
        bounded(policy[key], key, 0., 1.)
    if policy['threshold_fraction'] >= policy['target_fraction']:
        raise ValueError('Require threshold_fraction < target_fraction')
    for key in ['max_depth_mm', 'seasonal_cap_mm']:
        bounded(policy[key], key, 0.)
    interval = policy['min_interval_days']
    if isinstance(interval, bool) or not isinstance(interval, int) or interval < 0:
        raise ValueError('min_interval_days must be a nonnegative integer')
    for key in ['start_bbch', 'end_bbch']:
        bounded(policy[key], key, 0., 99.)
    if policy['start_bbch'] > policy['end_bbch']:
        raise ValueError('Irrigation policy stage bounds reversed')
    return dict(policy)


def policy_event(policy, day, stage, root_depth_mm, state, layers,
                 applied_mm, last_event_date) -> dict | None:
    """Return a proposed event from inherited water, never update water state.

    ``applied_mm`` includes water already allocated against this season's cap.
    The simulator includes reserved, explicitly scheduled fixed events in this
    amount so automatic events cannot consume their quota. Stage bounds are
    inclusive; the availability trigger is strictly below the threshold.
    """
    policy = validate_policy(policy)
    current = date.fromisoformat(day)
    root_depth_mm = bounded(root_depth_mm, 'root_depth_mm', 0.)
    applied_mm = bounded(applied_mm, 'applied_mm', 0.)
    bbch = bounded(stage['bbch'], 'policy stage BBCH', 0., 99.)
    if last_event_date is not None:
        elapsed = (current - date.fromisoformat(last_event_date)).days
        if elapsed < 0:
            raise ValueError('last_event_date cannot be in the future')
        if elapsed < policy['min_interval_days']:
            return None
    if not policy['start_bbch'] <= bbch <= policy['end_bbch']:
        return None
    budget = max(0., policy['seasonal_cap_mm'] - applied_mm)
    if root_depth_mm == 0. or budget == 0. or policy['max_depth_mm'] == 0.:
        return None
    water = field_water_mm(state)
    if len(water) != len(layers):
        raise ValueError('Policy soil state and layer lengths differ')
    depth = available = capacity = 0.
    for stored, layer in zip(water, layers):
        overlap = max(0., min(layer.thickness_mm, root_depth_mm - depth))
        available += (stored / layer.thickness_mm - layer.wilting_point) * overlap
        capacity += (layer.field_capacity - layer.wilting_point) * overlap
        depth += layer.thickness_mm
    if capacity == 0. or available >= policy['threshold_fraction'] * capacity:
        return None
    amount = min(policy['target_fraction'] * capacity - available,
                 policy['max_depth_mm'], budget)
    return dict(event_id=f'policy-root_zone_threshold-{day}', date=day,
                amount_mm=amount, measurement_location='field')
