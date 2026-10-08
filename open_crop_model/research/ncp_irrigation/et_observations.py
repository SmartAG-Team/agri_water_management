"""Direct ET and unclosed soil-water loss are distinct observation variables."""
from datetime import date,timedelta
from math import isfinite,isclose
import numpy as np

DIRECT_METHODS={'direct_daily','lysimeter','weighing_lysimeter','ec_unfilled_daily'}
ET_VARIABLES={'et','et_mm'}
BUDGET_VARIABLE='water_budget_residual_mm'


def classify_et_targets(frame):
    if 'variable' not in frame:raise ValueError('ET classification requires variable identity')
    result=frame.copy(deep=True)
    targets=result.variable.isin(ET_VARIABLES)
    methods=result['measurement_method'] if 'measurement_method' in result else result.variable.map(lambda _:None)
    result['et_target_kind']=''
    result.loc[targets,'et_target_kind']='unverified_et_method'
    result.loc[targets&methods.isin(DIRECT_METHODS),'et_target_kind']='direct_et'
    budget=targets&methods.eq('water_budget_interval')
    result.loc[budget,'variable']=BUDGET_VARIABLE
    result.loc[result.variable.eq(BUDGET_VARIABLE),'et_target_kind']='unclosed_water_budget'
    return result


def require_direct_et(frame):
    classified=classify_et_targets(frame)
    if frame.empty or 'split' not in frame or not frame.split.eq('calibration').all():
        raise ValueError('Only nonempty direct ET calibration targets can fit ET')
    if not classified.et_target_kind.eq('direct_et').all():
        raise ValueError('ET fitting requires explicitly identified direct measurements')
    if 'value' not in frame or not np.isfinite(frame.value.to_numpy(float)).all():
        raise ValueError('Direct ET calibration targets must be finite')


def _finite(value,name,nonnegative=True):
    try:value=float(value)
    except (TypeError,ValueError) as exc:raise ValueError(f'Invalid {name}') from exc
    if not isfinite(value) or (nonnegative and value<0.):raise ValueError(f'Invalid {name}')
    return value


def water_budget_residual(daily,*,window_start,window_end,profile_depth_cm,observed_depth_cm):
    try:
        start=date.fromisoformat(window_start);end=date.fromisoformat(window_end)
        dates=[date.fromisoformat(r['date']) for r in daily]
    except (KeyError,TypeError,ValueError) as exc:raise ValueError('Budget interval requires explicit ISO dates') from exc
    if end<start or not dates or any(b<=a for a,b in zip(dates,dates[1:])):
        raise ValueError('Budget interval dates must be unique and ordered')
    profile=_finite(profile_depth_cm,'profile depth')
    try:top,bottom=observed_depth_cm
    except (TypeError,ValueError) as exc:raise ValueError('Budget observation requires matched full depth') from exc
    top=_finite(top,'observed top depth');bottom=_finite(bottom,'observed bottom depth')
    if profile<=0. or not isclose(top,0.,abs_tol=1e-9) or not isclose(bottom,profile,rel_tol=0.,abs_tol=1e-9):
        raise ValueError('Budget observation depth must match the full modeled soil column')
    selected=[r for r,d in zip(daily,dates) if start<=d<=end]
    expected=[(start+timedelta(days=n)).isoformat() for n in range((end-start).days+1)]
    if [r['date'] for r in selected]!=expected:raise ValueError('Missing exact modeled day in budget interval')
    loss=0.;previous=None
    for r in selected:
        try:
            initial=_finite(r['soil_storage_initial_mm'],'initial soil storage')
            final=_finite(r['soil_storage_final_mm'],'final soil storage')
            rain=_finite(r['precipitation_mm'],'field rainfall')
            irrigation=_finite(r['irrigation_field_mm'],'field irrigation')
        except KeyError as exc:raise ValueError('Budget operator requires coupled soil storage and field inputs') from exc
        if previous is not None and not isclose(initial,previous,rel_tol=0.,abs_tol=1e-8):
            raise ValueError('Discontinuous soil storage in budget interval')
        loss+=rain+irrigation-(final-initial);previous=final
    return loss/len(selected)
