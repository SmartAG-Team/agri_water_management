"""Dimensionless bounded fitting with feasible absolute difference steps."""
from dataclasses import dataclass
from functools import partial
import numpy as np
from scipy.optimize import least_squares


def bounds_arrays(low, high):
    low, high = np.asarray(low, float), np.asarray(high, float)
    if low.ndim != 1 or low.shape != high.shape or not len(low):
        raise ValueError('Matching nonempty one-dimensional bounds required')
    if not np.isfinite(low).all() or not np.isfinite(high).all() or np.any(high <= low):
        raise ValueError('Finite ordered bounds required')
    return low, high


def to_unit(x, low, high):
    low, high = bounds_arrays(low, high)
    x = np.asarray(x, float)
    if x.shape != low.shape or not np.isfinite(x).all() or np.any(x < low) or np.any(x > high):
        raise ValueError('Physical vector outside parameter bounds')
    return (x - low) / (high - low)


def from_unit(z, low, high):
    low, high = bounds_arrays(low, high)
    z = np.asarray(z, float)
    if z.shape != low.shape or not np.isfinite(z).all() or np.any(z < 0.) or np.any(z > 1.):
        raise ValueError('Normalized vector outside unit bounds')
    return low + z * (high - low)


def absolute_jacobian(fun, z, step=.002, workers=None):
    z = np.asarray(z, float)
    if z.ndim != 1 or not np.isfinite(z).all() or np.any(z < 0.) or np.any(z > 1.):
        raise ValueError('Finite unit vector required')
    if not np.isfinite(step) or not 0. < step < .5:
        raise ValueError('Difference step must be inside (0, 0.5)')
    points, widths = [], []
    for i in range(len(z)):
        a, b = z.copy(), z.copy()
        a[i], b[i] = max(0., z[i] - step), min(1., z[i] + step)
        points.extend([a, b])
        widths.append(b[i] - a[i])
    values = list(map(fun, points) if workers is None else workers(fun, points))
    jacobian = np.column_stack([(np.asarray(values[2*i+1]) - np.asarray(values[2*i])) / width
                               for i, width in enumerate(widths)])
    if not np.isfinite(jacobian).all():
        raise ValueError('Nonfinite finite-difference derivative')
    return jacobian


@dataclass
class UnitResidual:
    fun: object
    low: np.ndarray
    high: np.ndarray

    def __call__(self, z):
        return self.fun(from_unit(z, self.low, self.high))


def fit_bounded(fun, initial, low, high, *, workers=None, difference_step=.002,
                ftol=1e-6, xtol=1e-6, gtol=1e-6, max_nfev=40, callback=None, x_scale=1.):
    low, high = bounds_arrays(low, high)
    unit_fun = UnitResidual(fun, low, high)
    z0 = to_unit(initial, low, high)
    jacobian = partial(absolute_jacobian, unit_fun, step=difference_step, workers=workers)
    result = least_squares(unit_fun, z0, jac=jacobian, bounds=(np.zeros(len(low)), np.ones(len(low))),
                           x_scale=x_scale, ftol=ftol, xtol=xtol, gtol=gtol,
                           max_nfev=max_nfev, callback=callback)
    result.physical_x = from_unit(result.x, low, high)
    return result
