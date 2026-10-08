"""Conservative bucket transport, bounded uptake and optional water-table exchange.

The water-table closure is a prescribed equilibrium bucket boundary, not a
Richards solver. Conductance/length are independent priors until validated.
"""

from copy import deepcopy
from math import exp, expm1
from .types import WaterBalanceResult, bounded

FLUX_NAMES = (
    "precipitation_mm",
    "irrigation_field_mm",
    "capillary_rise_mm",
    "soil_evaporation_mm",
    "transpiration_mm",
    "canopy_evaporation_mm",
    "application_evaporation_mm",
    "runoff_mm",
    "off_field_drift_mm",
    "bottom_drainage_mm",
    "pump_input_mm",
    "conveyance_loss_mm",
)


def validate_state(state, parameters):
    bounded(state.wetted_fraction, "wetted_fraction", 1e-9, 1)
    bounded(state.canopy_water_mm, "canopy_water_mm")
    if state.wetted_fraction < 1 and state.dry is None:
        raise ValueError("Active dry domain missing")
    for column, weight in domains(state):
        if len(column.water_mm) != len(parameters.layers):
            raise ValueError("State/layer length mismatch")
        bounded(column.pond_mm, "pond_mm")
        for i, (water, layer) in enumerate(zip(column.water_mm, parameters.layers)):
            bounded(
                water,
                f"water_mm[{i}]",
                layer.air_dry * layer.thickness_mm - 1e-10,
                layer.saturation * layer.thickness_mm + 1e-10,
            )


def domains(state):
    active = [(state.wet, state.wetted_fraction)]
    if state.wetted_fraction < 1:
        active.append((state.dry, 1 - state.wetted_fraction))
    return active


def _exchange(state, parameters, dt):
    if state.wetted_fraction == 1 or parameters.exchange_mm_day == 0:
        return
    fw = state.wetted_fraction
    fd = 1 - fw
    for i, layer in enumerate(parameters.layers):
        delta = state.wet.water_mm[i] - state.dry.water_mm[i]
        if delta == 0:
            continue
        donor, receiver, dw, rw = (
            (state.wet, state.dry, fw, fd)
            if delta > 0
            else (state.dry, state.wet, fd, fw)
        )
        # Field-area transfer: bounded by equalization, available water, capacity.
        amount = min(
            abs(delta) / (1 / dw + 1 / rw),
            parameters.exchange_mm_day * dt,
            max(0.0, donor.water_mm[i] - layer.air_dry * layer.thickness_mm) * dw,
            max(0.0, layer.saturation * layer.thickness_mm - receiver.water_mm[i]) * rw,
        )
        donor.water_mm[i] -= amount / dw
        receiver.water_mm[i] += amount / rw


def _conductivity_transport(column, parameters, dt):
    """Conservative conductivity-scaled reservoir cascade (not Darcy flow).

    Each layer releases above-FC water with residence time
    (SAT-FC)*thickness/Ksat. Incoming water is routed within the step; maximum
    receiving capacity includes downstream throughput, so a saturated thin
    layer can transmit Ksat rather than only its initial empty pore volume.
    Exponential reservoir release and conductivity/storage bounds keep the
    scheme positive. Transient accuracy still requires timestep convergence.
    """
    layers = parameters.layers
    receiving = [0.0] * len(layers) + [float("inf")]
    for i in range(len(layers) - 1, -1, -1):
        layer = layers[i]
        room = max(0.0, layer.saturation * layer.thickness_mm - column.water_mm[i])
        receiving[i] = room + min(layer.ksat_mm_day * dt, receiving[i + 1])
    incoming = min(column.pond_mm, layers[0].ksat_mm_day * dt, receiving[0])
    column.pond_mm -= incoming
    for i, layer in enumerate(layers):
        water = column.water_mm[i] + incoming
        excess = max(0.0, water - layer.field_capacity * layer.thickness_mm)
        drainable = (layer.saturation - layer.field_capacity) * layer.thickness_mm
        release = excess * -expm1(-layer.ksat_mm_day * dt / drainable)
        outgoing = min(release, layer.ksat_mm_day * dt, receiving[i + 1])
        column.water_mm[i] = water - outgoing
        incoming = outgoing
    spill = max(0.0, column.pond_mm - parameters.pond_capacity_mm)
    column.pond_mm -= spill
    return incoming, 0.0, spill


def _transport(column, parameters, dt, water_table_depth):
    if parameters.drainage_method == "matric_gradient":
        from .matric import transport
        return transport(column, parameters, dt)
    if parameters.drainage_method == "conductivity_reservoir":
        return _conductivity_transport(column, parameters, dt)
    layers = parameters.layers
    # Store overflow, never clip it away. Infiltration is bounded by Ksat.
    room = max(0.0, layers[0].saturation * layers[0].thickness_mm - column.water_mm[0])
    infiltration = min(column.pond_mm, room, layers[0].ksat_mm_day * dt)
    column.pond_mm -= infiltration
    column.water_mm[0] += infiltration
    drainage = capillary = 0.0
    for i, layer in enumerate(layers):
        if (
            i == len(layers) - 1
            and parameters.bottom_boundary == "prescribed_water_table"
        ):
            center = sum(x.thickness_mm for x in layers[:-1]) + layer.thickness_mm / 2
            height = max(0.0, water_table_depth - center)
            equilibrium = layer.thickness_mm * (
                layer.wilting_point
                + (layer.saturation - layer.wilting_point)
                * exp(-height / parameters.capillary_length_mm)
            )
            move = (equilibrium - column.water_mm[i]) * (
                1 - exp(-parameters.water_table_conductance_day * dt)
            )
            move = max(-layer.ksat_mm_day * dt, min(layer.ksat_mm_day * dt, move))
            column.water_mm[i] += move
            capillary += max(0.0, move)
            drainage += max(0.0, -move)
            continue
        excess = max(
            0.0, column.water_mm[i] - layer.field_capacity * layer.thickness_mm
        )
        flow = min(
            excess * (1 - exp(-parameters.drainage_rate_day * dt)),
            layer.ksat_mm_day * dt,
        )
        if i + 1 < len(layers):
            capacity = max(
                0.0,
                layers[i + 1].saturation * layers[i + 1].thickness_mm
                - column.water_mm[i + 1],
            )
            flow = min(flow, capacity)
            column.water_mm[i + 1] += flow
        else:
            drainage += flow
        column.water_mm[i] -= flow
    spill = max(0.0, column.pond_mm - parameters.pond_capacity_mm)
    column.pond_mm -= spill
    return drainage, capillary, spill


def _root_activity(parameters, root_depth):
    """Layer-integrated relative root activity per represented soil volume.

    The reference mode has mean one over represented rooted depth. The surface
    mode integrates a local exponential coefficient with surface amplitude one;
    deeper rooting does not increase the coefficient in existing shallow roots.
    A positive background fraction mixes the surface exponential with a
    depth-uniform density, each normalized over the declared reference depth.
    Neither mode adds water. Zero decay retains uniform rooted-layer fractions.
    """
    depth=min(root_depth,sum(layer.thickness_mm for layer in parameters.layers))
    decay=parameters.root_density_decay_m_inv/1000.
    background=parameters.root_activity_background_fraction
    if background>0. and decay>0. and depth>0.:
        reference=parameters.root_activity_reference_depth_mm
        def exponential_length(length):
            scaled=decay*length
            if scaled==0.:
                return length
            if scaled==float('inf'):
                return 1./decay
            return length*(-expm1(-scaled)/scaled)
        reference_length=exponential_length(reference)
        total=(1-background)*exponential_length(depth)/reference_length+background*depth/reference
        surface_density=(1-background)/reference_length+background/reference
        top=0.;weights=[]
        for layer in parameters.layers:
            length=max(0.,min(layer.thickness_mm,depth-top))
            integral=((1-background)*exp(-decay*top)*exponential_length(length)/reference_length
                      +background*length/reference)
            if parameters.root_activity_normalization=="surface":
                activity=integral/(surface_density*layer.thickness_mm)
            else:
                activity=depth*integral/(total*layer.thickness_mm)
            weights.append(activity);top+=layer.thickness_mm
        return weights
    denominator=-expm1(-decay*depth) if decay>0. and depth>0. else 0.
    top=0.;weights=[]
    for layer in parameters.layers:
        length=max(0.,min(layer.thickness_mm,depth-top))
        if denominator>0.:
            if parameters.root_activity_normalization == "surface":
                activity=exp(-decay*top)*-expm1(-decay*length)/(decay*layer.thickness_mm)
            else:
                activity=depth*exp(-decay*top)*-expm1(-decay*length)/(denominator*layer.thickness_mm)
        else:activity=length/layer.thickness_mm
        weights.append(activity);top+=layer.thickness_mm
    return weights


def _layer_depletion_stress(column, index, layer, fraction):
    available=max(0.,column.water_mm[index]-layer.wilting_point*layer.thickness_mm)
    capacity=(layer.field_capacity-layer.wilting_point)*layer.thickness_mm
    return min(1.,available/((1.-fraction)*capacity)) if capacity>0. else 0.


def _compensated_layer_uptake(state, parameters, root_depth, demand, dt,
                              activity_weights, depletion_fraction):
    """Redistribute a bounded unmet demand to better supplied rooted volume.

    Base uptake matches the noncompensatory layer closure. Additional uptake
    is restricted to roots at or above the rooted mean local stress factor,
    to their remaining extraction capacity, and to the best local stress.
    This empirical closure does not resolve root hydraulic potentials.
    """
    total_activity=sum(a*l.thickness_mm for a,l in zip(activity_weights,parameters.layers))
    if demand<=0. or total_activity<=0.:
        return 0.
    entries=[];top=0.
    for i,layer in enumerate(parameters.layers):
        rooted=max(0.,min(1.,(root_depth-top)/layer.thickness_mm))
        top+=layer.thickness_mm
        share=activity_weights[i]*layer.thickness_mm/total_activity
        for column,weight in domains(state):
            if weight<=0. or share<=0.:
                continue
            available=max(0.,column.water_mm[i]-layer.wilting_point*layer.thickness_mm)
            capacity=min(available*rooted,
                available*activity_weights[i]*parameters.root_extraction_fraction_day*dt)*weight
            stress=_layer_depletion_stress(column,i,layer,depletion_fraction)
            base=min(capacity,demand*share*weight*stress)
            entries.append([column,i,weight,capacity,share*weight,stress,base])
    if not entries:
        return 0.
    mean_stress=sum(e[4]*e[5] for e in entries)
    best_stress=max(e[5] for e in entries)
    base_total=sum((e[6] for e in entries),0.)
    budget=parameters.root_compensation_fraction*max(0.,demand*best_stress-base_total)
    # Each capacity-limited pass either allocates the remaining budget or
    # exhausts at least one eligible entry. No uptake is taken from dry roots.
    while budget>1e-12:
        eligible=[e for e in entries if e[5]>=mean_stress-1e-12 and
                  e[4]*e[5]>0. and e[3]-e[6]>1e-12]
        total_priority=sum(e[4]*e[5] for e in eligible)
        if total_priority<=0.:
            break
        taken=0.
        for entry in eligible:
            added=min(entry[3]-entry[6],budget*entry[4]*entry[5]/total_priority)
            entry[6]+=added;taken+=added
        if taken<=1e-12:
            break
        budget=max(0.,budget-taken)
    for column,i,weight,capacity,share,stress,actual in entries:
        column.water_mm[i]-=actual/weight
    return sum((e[6] for e in entries),0.)


def _uptake(state, parameters, root_depth, demand, dt, activity_weights=None,
            depletion_fraction=None):
    entries = []
    top = 0.0
    activity_weights=activity_weights if activity_weights is not None else _root_activity(parameters,root_depth)
    local=parameters.plant_water_stress_method == "layer_root_depletion"
    profile_activity=sum(a*l.thickness_mm for a,l in zip(activity_weights,parameters.layers))
    fraction=parameters.readily_available_water_fraction if depletion_fraction is None else depletion_fraction
    if parameters.plant_water_stress_method == "compensated_layer_depletion":
        return _compensated_layer_uptake(state,parameters,root_depth,demand,dt,
                                         activity_weights,fraction)
    local_total=0.
    for i, layer in enumerate(parameters.layers):
        rooted = max(0.0, min(1.0, (root_depth - top) / layer.thickness_mm))
        top += layer.thickness_mm
        for column, weight in domains(state):
            available = max(
                0.0, column.water_mm[i] - layer.wilting_point * layer.thickness_mm
            )
            capacity=min(available*rooted,
                available*activity_weights[i]*parameters.root_extraction_fraction_day*dt)*weight
            if local:
                share=activity_weights[i]*layer.thickness_mm/profile_activity if profile_activity>0. else 0.
                stress=_layer_depletion_stress(column,i,layer,fraction)
                actual=min(capacity,demand*share*weight*stress)
                column.water_mm[i]-=actual/weight
                local_total+=actual
                continue
            entries.append((column, i, weight, capacity))
    if local:return local_total
    total = sum(x[3] for x in entries)
    if total == 0:
        return 0.0
    ratio = min(1.0, demand / total)
    for column, i, weight, capacity in entries:
        column.water_mm[i] -= capacity * ratio / weight
    return total * ratio


def _root_zone_stress(state, parameters, root_depth, depletion_fraction=None):
    """FAO56 depletion stress from represented rooted field-area storage.

    The supplied readily available fraction may include a daily demand adjustment.
    Excess water above FC cannot remove more than all of the drought stress.
    This bucket closure does not simulate root/leaf water potentials.
    """
    if parameters.plant_water_stress_method == "none":
        return 1.0
    fraction=parameters.readily_available_water_fraction if depletion_fraction is None else depletion_fraction
    if parameters.plant_water_stress_method in {"layer_root_depletion", "compensated_layer_depletion"}:
        activity=_root_activity(parameters,root_depth)
        total=sum(a*l.thickness_mm for a,l in zip(activity,parameters.layers))
        if total<=0.:return 0.
        terms=[(a*layer.thickness_mm/total*weight,_layer_depletion_stress(column,i,layer,fraction))
            for i,(a,layer) in enumerate(zip(activity,parameters.layers)) for column,weight in domains(state)]
        mean=sum(weight*stress for weight,stress in terms)
        if parameters.plant_water_stress_method == "compensated_layer_depletion":
            best=max(stress for weight,stress in terms if weight>0.)
            return min(best,mean+parameters.root_compensation_fraction*(best-mean))
        return mean
    top=capacity=available=0.0
    for i,layer in enumerate(parameters.layers):
        rooted=max(0.,min(1.,(root_depth-top)/layer.thickness_mm))
        top+=layer.thickness_mm
        layer_capacity=(layer.field_capacity-layer.wilting_point)*layer.thickness_mm
        capacity+=layer_capacity*rooted
        for column,weight in domains(state):
            water=max(0.,column.water_mm[i]-layer.wilting_point*layer.thickness_mm)
            available+=min(layer_capacity,water)*rooted*weight
    return min(1.,available/((1.-fraction)*capacity)) if capacity>0. else 0.


def step_water_day(state, forcing, parameters):
    from datetime import date, timedelta

    try:
        current_date = date.fromisoformat(forcing.date)
        if forcing.date != current_date.isoformat():
            raise ValueError("Canonical ISO date required")
        if state.last_date is not None and current_date != date.fromisoformat(
            state.last_date
        ) + timedelta(days=1):
            raise ValueError("Carried state requires immediately following date")
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Invalid or nonfollowing date: {forcing.date}") from exc
    identifiers = [event.event_id for event in forcing.irrigation_events]
    if any(not isinstance(key, str) or not key.strip() for key in identifiers) or len(
        set(identifiers)
    ) != len(identifiers):
        raise ValueError(
            "Irrigation event IDs must be nonempty and unique within the day"
        )
    validate_state(state, parameters)
    for name in (
        "precipitation_mm",
        "root_depth_mm",
        "potential_transpiration_mm",
        "potential_soil_evaporation_mm",
        "potential_canopy_evaporation_mm",
    ):
        bounded(getattr(forcing, name), name)
    bounded(forcing.canopy_cover, "canopy_cover", 0, 1)
    hourly = forcing.precipitation_hourly_mm
    if hourly is not None:
        if not isinstance(hourly, (tuple, list)) or len(hourly) != 24:
            raise ValueError("precipitation_hourly_mm requires 24 hourly amounts")
        for amount in hourly:
            bounded(amount, "precipitation_hourly_mm")
        if abs(sum(hourly)-forcing.precipitation_mm) > max(1e-8, forcing.precipitation_mm*1e-10):
            raise ValueError("Hourly amounts must equal daily precipitation")
        if parameters.substeps == 24:
            rain_steps = hourly
        else:
            rain_steps = [0.] * parameters.substeps
            for h, amount in enumerate(hourly):
                if amount == 0:
                    continue
                for j in range(int(h*parameters.substeps/24), min(parameters.substeps, int((h+1)*parameters.substeps/24)+1)):
                    overlap=max(0.,min((j+1)*24/parameters.substeps,h+1)-max(j*24/parameters.substeps,h))
                    rain_steps[j] += amount * overlap
    if parameters.bottom_boundary == "prescribed_water_table":
        bounded(forcing.water_table_depth_mm, "water_table_depth_mm")
    s = deepcopy(state)
    before = s.storage_mm()
    flux = {key: 0.0 for key in FLUX_NAMES}
    flux["precipitation_mm"] = forcing.precipitation_mm
    boundaries = []
    assumptions = []
    effective_transpiration_demand = 0.0
    root_zone_stress_factor = 0.0
    activity_weights=_root_activity(parameters,forcing.root_depth_mm)
    # FAO56 Chapter 8: p=p_table+0.04*(5-ETc), bounded to [0.1,0.8].
    # The driver is full daily potential transpiration + soil evaporation.
    # Wet-canopy evaporation competes with transpiration and is not added again.
    depletion_fraction=parameters.readily_available_water_fraction
    potential_et_demand=forcing.potential_transpiration_mm+forcing.potential_soil_evaporation_mm
    if parameters.readily_available_water_adjustment == "fao56_daily_demand":
        depletion_fraction=min(.8,max(.1,depletion_fraction+.04*(5.-potential_et_demand)))
    if forcing.irrigation_events:
        from .irrigation import partition_irrigation

        for event in forcing.irrigation_events:
            boundary = partition_irrigation(event, s, parameters)
            boundaries.append((event, boundary))
            for key, attr in [
                ("irrigation_field_mm", "field_input_mm"),
                ("pump_input_mm", "pump_input_mm"),
                ("conveyance_loss_mm", "conveyance_loss_mm"),
                ("application_evaporation_mm", "application_evaporation_mm"),
                ("off_field_drift_mm", "off_field_drift_mm"),
            ]:
                flux[key] += getattr(boundary, attr)
            if event.duration_minutes is None:
                assumptions.append(f"{event.event_id}: uniform_24h_duration_assumption")
    for j in range(parameters.substeps):
        dt = 1.0 / parameters.substeps
        if hourly is None:
            rain = forcing.precipitation_mm * dt
        else:
            rain = rain_steps[j]
        for event, boundary in boundaries:
            start = event.start_minute
            end = start + (event.duration_minutes or 1440.0)
            overlap = max(
                0.0, min((j + 1) * dt * 1440, end) - max(j * dt * 1440, start)
            )
            part = overlap / (end - start)
            s.wet.pond_mm += boundary.wet_local_mm * part
            if s.wetted_fraction < 1:
                s.dry.pond_mm += boundary.dry_local_mm * part
            s.canopy_water_mm += boundary.canopy_input_mm * part
        capacity = parameters.canopy_capacity_mm * forcing.canopy_cover
        intercepted = min(
            rain * forcing.canopy_cover, max(0.0, capacity - s.canopy_water_mm)
        )
        s.canopy_water_mm += intercepted
        overflow = max(0.0, s.canopy_water_mm - capacity)
        s.canopy_water_mm -= overflow
        for column, weight in domains(s):
            column.pond_mm += rain - intercepted + overflow
        for column, weight in domains(s):
            drain, cap, runoff = _transport(
                column, parameters, dt, forcing.water_table_depth_mm
            )
            flux["bottom_drainage_mm"] += weight * drain
            flux["capillary_rise_mm"] += weight * cap
            flux["runoff_mm"] += weight * runoff
        _exchange(s, parameters, dt)
        evaporation_budget = forcing.potential_soil_evaporation_mm * dt
        ec = min(s.canopy_water_mm, forcing.potential_canopy_evaporation_mm * dt)
        s.canopy_water_mm -= ec
        flux["canopy_evaporation_mm"] += ec
        effective_transpiration_demand += max(
            0.0, forcing.potential_transpiration_mm * dt - ec
        )
        local_demand = evaporation_budget
        for column, weight in domains(s):
            pond_evap = min(column.pond_mm, local_demand)
            column.pond_mm -= pond_evap
            layer = parameters.layers[0]
            air = layer.air_dry * layer.thickness_mm
            fc = layer.field_capacity * layer.thickness_mm
            relative = max(0.0, min(1.0, (column.water_mm[0] - air) / (fc - air)))
            if not parameters.dynamic_evaporation:
                drying = 1.0
            elif parameters.soil_evaporation_method == "two_stage_storage":
                # Generalized FAO-56 two-stage reduction from actual storage:
                # TEW=(FC-air_dry)*Z, REW=fraction*TEW, De=(FC-theta)*Z.
                # Kr=min(1,(TEW-De)/(TEW-REW)). No separate water is created.
                drying = min(1.0, relative/(1.0-parameters.readily_evaporable_fraction))
            else:
                drying = relative
            soil_evap = min(
                max(0.0, column.water_mm[0] - air), (local_demand - pond_evap) * drying
            )
            column.water_mm[0] -= soil_evap
            flux["soil_evaporation_mm"] += weight * (soil_evap + pond_evap)
        stress = _root_zone_stress(s, parameters, forcing.root_depth_mm, depletion_fraction)
        root_zone_stress_factor += stress * dt
        flux["transpiration_mm"] += _uptake(
            s,
            parameters,
            forcing.root_depth_mm,
            max(0.0, forcing.potential_transpiration_mm * dt - ec) *
                (1. if parameters.plant_water_stress_method in {
                    "layer_root_depletion", "compensated_layer_depletion"
                } else stress),
            dt,
            activity_weights,
            depletion_fraction,
        )
    s.last_date = forcing.date
    delta = s.storage_mm() - before
    residual = (
        flux["precipitation_mm"]
        + flux["irrigation_field_mm"]
        + flux["capillary_rise_mm"]
        - sum(
            flux[k]
            for k in (
                "soil_evaporation_mm",
                "transpiration_mm",
                "canopy_evaporation_mm",
                "application_evaporation_mm",
                "runoff_mm",
                "off_field_drift_mm",
                "bottom_drainage_mm",
            )
        )
        - delta
    )
    flux["storage_initial_mm"] = before
    flux["potential_transpiration_after_interception_mm"] = (
        effective_transpiration_demand
    )
    flux["storage_final_mm"] = s.storage_mm()
    flux["storage_change_mm"] = delta
    validate_state(s, parameters)
    if abs(residual) > 1e-6:
        raise ArithmeticError(f"Water balance residual {residual} exceeds tolerance")
    return WaterBalanceResult(
        s,
        flux,
        residual,
        {
            "input_timing_assumptions": assumptions,
            "bottom_boundary": parameters.bottom_boundary,
            "substeps": parameters.substeps,
            "precipitation_timing": "uniform_daily_assumption" if hourly is None else "explicit_hourly",
            "plant_water_stress_method": parameters.plant_water_stress_method,
            "root_zone_stress_factor": root_zone_stress_factor,
            "root_zone_depletion_fraction": depletion_fraction,
            "readily_available_water_adjustment": parameters.readily_available_water_adjustment,
            "potential_et_demand_mm": potential_et_demand,
            "surface_wetness": [
                (
                    c.water_mm[0]
                    - parameters.layers[0].air_dry * parameters.layers[0].thickness_mm
                )
                / (
                    parameters.layers[0].thickness_mm
                    * (
                        parameters.layers[0].field_capacity
                        - parameters.layers[0].air_dry
                    )
                )
                for c, w in domains(s)
            ],
        },
    )
