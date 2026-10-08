"""Conditional VG curves reconstructed from supplied FC/WP endpoints."""
from copy import deepcopy
from math import exp,expm1,isfinite,log
from scipy.optimize import brentq


def reconstruct(row):
    p=deepcopy(row)
    dry,lower,upper,saturation=[float(p[key]) for key in ['air_dry','wilting_point','field_capacity','saturation']]
    if not all(isfinite(v) for v in [dry,lower,upper,saturation]) or not dry<lower<upper<saturation:
        raise ValueError('Retention reconstruction requires ordered AD < WP < FC < SAT.')
    if p.get('retention_alpha_mm_inv') is not None and p.get('retention_n') is not None:return p
    se_fc=(upper-dry)/(saturation-dry);se_wp=(lower-dry)/(saturation-dry)
    def logarithm(value):return log(expm1(value)) if value<700 else value
    def terms(n):
        m=1-1/n
        return logarithm(-log(se_fc)/m),logarithm(-log(se_wp)/m)
    def difference(n):
        fc,wp=terms(n)
        return (fc-wp)/n-log(3300./150000.)
    n=brentq(difference,1.000001,20.,xtol=1e-12)
    fc,_=terms(n);alpha=exp(fc/n-log(3300.))
    if not isfinite(alpha) or alpha<=0:raise ValueError('Retention reconstruction is not finite.')
    p.update(retention_alpha_mm_inv=alpha,retention_n=n)
    return p
