"""Conservative bounded Darcy-gradient transport in water-content volumes.

van Genuchten--Mualem functions follow HYDRUS technical manual equations
2.43--2.45. Residual water is an explicit air-dry prior here. Head is capped
at 10^12 mm near residual storage for finite arithmetic. Conductivity is
zero at residual storage and Ksat at saturation. This explicit, bounded
layer approximation does not solve saturated positive pressure or the
full implicit Richards system. Hydraulic curves require independent input.
"""

from math import exp, expm1, isfinite, log, log1p
from functools import lru_cache
from .types import bounded

HEAD_CAP_MM = 1e12


def _curve(layer):
    if layer.retention_alpha_mm_inv is None or layer.retention_n is None:
        raise ValueError("Independent retention curve required")
    return layer.retention_alpha_mm_inv, layer.retention_n, 1.-1./layer.retention_n


def hydraulic_properties(theta, layer):
    alpha,n,m=_curve(layer)
    bounded(theta,"soil theta",layer.air_dry-1e-12,layer.saturation+1e-12)
    saturation=(theta-layer.air_dry)/(layer.saturation-layer.air_dry)
    if saturation<=0.:
        return -HEAD_CAP_MM,0.
    if saturation>=1.:
        return 0.,layer.ksat_mm_day
    logarithm=-log(saturation)/m
    log_head=(log(expm1(logarithm)) if logarithm<700. else logarithm)/n-log(alpha)
    head=-exp(min(log(HEAD_CAP_MM),log_head))
    power=exp(log(saturation)/m)
    factor=-expm1(m*log1p(-power)) if power<1. else 1.
    conductivity=layer.ksat_mm_day*sqrt_saturation(saturation)*factor*factor
    return head,conductivity


def sqrt_saturation(value):
    return value**.5


def theta_from_head(head, layer):
    alpha,n,m=_curve(layer)
    if isinstance(head,bool) or not isinstance(head,(int,float)) or not isfinite(head):
        raise ValueError("Finite retention pressure head required")
    if head>=0.:
        return layer.saturation
    power_log=n*log(alpha*-head)
    denominator_log=log1p(exp(power_log)) if power_log<700. else power_log
    return layer.air_dry+(layer.saturation-layer.air_dry)*exp(-m*denominator_log)


def _relative_at_scaled_head(suction, n):
    """Mualem relative conductivity for dimensionless positive suction."""
    if suction <= 0.:
        return 1.
    m=1.-1./n
    power_log=n*log(suction)
    denominator_log=log1p(exp(power_log)) if power_log<700. else power_log
    log_saturation=-m*denominator_log
    power=exp(-denominator_log)
    factor=-expm1(m*log1p(-power)) if power<1. else 1.
    return exp(.5*log_saturation)*factor*factor


@lru_cache(maxsize=128)
def _pressure_integral_table(n):
    # Integrate in log suction, including the Jacobian u. The lower omitted
    # interval is <1e-13; the dry tail converges because Mualem K decays >u^-2.
    start=-30.;step=64./4096
    values=[];primitive=[0.]
    for i in range(4097):
        suction=exp(start+i*step)
        values.append(suction*_relative_at_scaled_head(suction,n))
        if i:primitive.append(primitive[-1]+.5*step*(values[-2]+values[-1]))
    return tuple(values),tuple(primitive)


def _pressure_primitive(head, layer):
    """Integral of relative conductivity from zero to pressure head (mm)."""
    if head >= 0.:
        return head
    alpha,n,_=_curve(layer)
    suction=alpha*-head;position=(log(suction)+30.)/(64./4096)
    values,primitive=_pressure_integral_table(n)
    if position<=0.:
        return -suction*_relative_at_scaled_head(.5*suction,n)/alpha
    if position>=4096:
        return -primitive[-1]/alpha
    index=int(position);width=(position-index)*(64./4096)
    local=suction*_relative_at_scaled_head(suction,n)
    return -(primitive[index]+.5*width*(values[index]+local))/alpha


def _pressure_mean_relative(head1, head2, layer):
    delta=head1-head2
    if abs(delta)<=1e-8*max(1.,abs(head1),abs(head2)):
        head=.5*(head1+head2)
        return _relative_at_scaled_head(layer.retention_alpha_mm_inv*max(0.,-head),layer.retention_n)
    mean=(_pressure_primitive(head1,layer)-_pressure_primitive(head2,layer))/delta
    return min(1.,max(0.,mean))


def interface_conductivity(upper, lower, k_upper, k_lower, method, *, head_upper=None, head_lower=None):
    """Combine saturated resistances and positive face relative mobility.

    Relative arithmetic interpolation allows a wet node to supply an air-dry
    neighbor. Saturated conductivity remains the series-resistance value;
    a genuinely impermeable layer is never bypassed. This is an explicit
    interface approximation, not a universal Darcian mean. Grid and timestep
    convergence are required for transient infiltration.
    """
    distance=.5*(upper.thickness_mm+lower.thickness_mm)
    if method == "harmonic_nodal":
        if k_upper==0. or k_lower==0.:return 0.
        return distance/(.5*upper.thickness_mm/k_upper+.5*lower.thickness_mm/k_lower)
    if method == "pressure_integrated":
        if any(isinstance(h,bool) or not isinstance(h,(int,float)) or not isfinite(h) for h in [head_upper,head_lower]):
            raise ValueError("Pressure-integrated conductivity requires finite numeric heads")
    if upper.ksat_mm_day==0. or lower.ksat_mm_day==0.:return 0.
    saturated=distance/(.5*upper.thickness_mm/upper.ksat_mm_day+.5*lower.thickness_mm/lower.ksat_mm_day)
    if method == "pressure_integrated":
        # Kirchhoff head-interval means are exact for homogeneous horizontal
        # pressure-driven flow. The existing gravity and heterogeneous-material
        # closures remain approximate; saturated series resistance is preserved.
        relative_upper=_pressure_mean_relative(head_upper,head_lower,upper)
        relative_lower=_pressure_mean_relative(head_upper,head_lower,lower)
    else:
        relative_upper=min(1.,max(0.,k_upper/upper.ksat_mm_day))
        relative_lower=min(1.,max(0.,k_lower/lower.ksat_mm_day))
    relative=(lower.thickness_mm*relative_upper+upper.thickness_mm*relative_lower)/(upper.thickness_mm+lower.thickness_mm)
    return saturated*relative


def transport(column, parameters, dt):
    """Integrate signed interfaces with exact opposite layer-area transfers.

    Receiving-capacity restrictions propagate upward through saturated
    layers and allow saturated throughflow. Adaptive internal steps limit
    each net theta change, independently of the crop fitting parameters.
    Precipitation/event timing, evaporation and uptake retain the outer
    hydrology step. Upward redistribution is an internal flux, not supply.
    """
    layers=parameters.layers
    remaining=dt
    drainage=0.
    iterations=0
    while remaining>1e-12:
        iterations+=1
        if iterations>100000:
            raise ArithmeticError("Matric transport did not finish its time interval")
        properties=[]
        for water,soil in zip(column.water_mm,layers):
            # Use the same storage bounds as flux eligibility. Division can
            # round an exact residual storage to theta slightly above AD.
            if water<=soil.air_dry*soil.thickness_mm:
                theta=soil.air_dry
            else:
                theta=water/soil.thickness_mm
            properties.append(hydraulic_properties(theta,soil))
        flux=[]
        for i in range(len(layers)-1):
            head1,k1=properties[i];head2,k2=properties[i+1]
            distance=.5*(layers[i].thickness_mm+layers[i+1].thickness_mm)
            conductivity=interface_conductivity(layers[i],layers[i+1],k1,k2,parameters.matric_interface_method,
                head_upper=head1,head_lower=head2)
            flux.append(conductivity*(1.+(head1-head2)/distance))
        # Free drainage is a unit hydraulic gradient, not removal to FC.
        flux.append(properties[-1][1])
        # Receiving capacity can include same-interval downstream throughput.
        for i in range(len(layers)-2,-1,-1):
            if flux[i]>0.:
                room=max(0.,layers[i+1].saturation*layers[i+1].thickness_mm-column.water_mm[i+1])
                flux[i]=min(flux[i],max(0.,flux[i+1])+room/remaining)
        room=max(0.,layers[0].saturation*layers[0].thickness_mm-column.water_mm[0])
        infiltration=min(column.pond_mm/remaining,layers[0].ksat_mm_day,
                         max(0.,flux[0])+room/remaining)
        rates=[infiltration]+flux
        step=remaining
        for i,soil in enumerate(layers):
            change=rates[i]-rates[i+1]
            if change==0.:
                continue
            step=min(step,parameters.matric_max_theta_step*soil.thickness_mm/abs(change))
            if change>0.:
                available=max(0.,soil.saturation*soil.thickness_mm-column.water_mm[i])
            else:
                available=max(0.,column.water_mm[i]-soil.air_dry*soil.thickness_mm)
            step=min(step,available/abs(change))
        if step<=0.:
            raise ArithmeticError("Matric flux cannot advance within pore bounds")
        column.pond_mm-=infiltration*step
        for i in range(len(layers)):
            column.water_mm[i]+=(rates[i]-rates[i+1])*step
        drainage+=flux[-1]*step
        remaining-=step
    spill=max(0.,column.pond_mm-parameters.pond_capacity_mm)
    column.pond_mm-=spill
    return drainage,0.,spill
