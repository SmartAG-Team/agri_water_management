"""Source-only plot/date reconciliation; never infer water from crop outcomes."""
from datetime import date
from math import isfinite
import re

def retain_after_recorded_presowing(events):
    """Replace the explicitly assumed sowing event, retain later assumptions.

    A partial presowing log provides no evidence that spring irrigation was
    absent. Recorded/unlabeled events retain their provenance and cannot be
    removed by this source-only adapter.
    """
    from copy import deepcopy
    return [deepcopy(e) for e in events if e.get('event_id') != 'assumed_bbch0']

def normalize_plot(value):
    value=re.sub(r'\s*\(\d+\)\s*$','',str(value).strip())
    for suffix in ['冬小麦','夏玉米']:
        if value.endswith(suffix):value=value[:-len(suffix)]
    return value.strip()

def classify_row(row):
    result=dict(row)
    d=date.fromisoformat(result['date'])
    harvest=d.year+int(result['crop']=='wheat' and d.month>=7)
    result.update(plot=normalize_plot(result['plot']),harvest_year=harvest,status='eligible')
    if int(result['declared_year']) not in {d.year,harvest}:
        result['status']='year_date_conflict'
    try:amount=float(result['amount_mm'])
    except (ValueError,TypeError):amount=float('nan')
    if not isfinite(amount) or amount<0:result['status']='amount_unknown'
    return result

def resolve_events(rows, *, site, plot, crop, start, end):
    plot=normalize_plot(plot)
    matching=[r for r in rows if r['site']==site and normalize_plot(r['plot'])==plot
              and r['crop']==crop and start<=r['date']<=end]
    if not matching:return dict(status='unknown',events=None,source_rows=[])
    if any(r['status']!='eligible' for r in matching):
        return dict(status='conflicting',events=None,source_rows=matching)
    grouped={}
    for r in matching:grouped.setdefault(r['date'],[]).append(r)
    events=[]
    for day,group in sorted(grouped.items()):
        amounts={float(r['amount_mm']) for r in group}
        if len(amounts)!=1:return dict(status='conflicting',events=None,source_rows=matching)
        # Duplicated sources preserve provenance without doubling water input.
        methods={r['method'] for r in group}
        if len(methods)>1:return dict(status='conflicting',events=None,source_rows=matching)
        events.append(dict(event_id='recorded_'+day,date=day,amount_mm=amounts.pop(),
                           measurement_location='field'))
    return dict(status='recorded_partial_log',events=events,source_rows=matching)
