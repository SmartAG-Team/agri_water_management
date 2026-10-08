import numpy as np
import pandas as pd
from calibration_core import balanced_weights, scales_from_calibration

def score(observed, predicted):
    o, p = np.asarray(observed, float), np.asarray(predicted, float)
    error = p-o
    mse = float(np.mean(error**2))
    var = float(np.var(o))
    return dict(n=len(o), rmse=mse**.5, bias=float(error.mean()), observed_mean=float(o.mean()),
        nrmse_percent=100*mse**.5/o.mean() if abs(o.mean()) > 1e-12 else None,
        nse=1-mse/var if var > 1e-12 else None,
        r_squared=float(np.corrcoef(o,p)[0,1]**2) if len(o) >= 3 and var*np.var(p) > 1e-12 else None)


def original_data_loss(station, field):
    rows = []
    for (version, crop), g in station.groupby(['version','crop']):
        f = field[field.version.eq(version) & field.crop.eq(crop)]
        scales = scales_from_calibration(g)
        station_loss = float(.5*np.sum(balanced_weights(g)*(g.predicted-g.value).to_numpy()**2/g.variable.map(scales).to_numpy()**2))
        targets = []
        for r in f.itertuples():
            for variable, o, p, denominator in [
                ('seasonal_et',r.observed_et_mm,r.predicted_et_mm,max(50.,.15*r.observed_et_mm)),
                ('yield',r.yield_13pct_kg_ha*.87,r.grain_13pct_kg_ha*.87,max(1000.,.2*r.yield_13pct_kg_ha*.87))]:
                targets.append(dict(site='Wuqiao',source_group_id=r.source_group_id,case_id=r.case_id,
                                    variable=variable,error=(p-o)/denominator))
        target = pd.DataFrame(targets)
        field_loss = float(.35*np.sum(balanced_weights(target)*target.error.to_numpy()**2))
        periods = g[g.variable.eq('et')].groupby(['site','source_group_id','case_id']).agg(value=('value','sum'),predicted=('predicted','sum')).reset_index()
        periods['variable'] = 'observed_day_ET'
        seasonal_loss = float(.05*np.sum(balanced_weights(periods)*((periods.predicted-periods.value).to_numpy()/(.15*np.maximum(20.,periods.value.to_numpy())))**2))
        contrasts = []
        for _, year in f.groupby('harvest_year'):
            a, b = year[year.treatment.eq('W0')].iloc[0], year[year.treatment.eq('W3')].iloc[0]
            contrasts.append((b.predicted_et_mm-a.predicted_et_mm)-(b.observed_et_mm-a.observed_et_mm))
        response_loss = .1*float(np.mean((np.asarray(contrasts)/50.)**2))
        rows.append(dict(version=version,crop=crop,station_loss=station_loss,field_loss=field_loss,
                         matched_day_ET_loss=seasonal_loss,response_loss=response_loss,
                         calibration_data_loss=station_loss+field_loss+seasonal_loss+response_loss))
    return pd.DataFrame(rows)



def data_loss(station,field):
    if not station.split.eq('calibration').all() or not field.split.eq('calibration').all():
        raise ValueError('Calibration-only data loss required.')
    result=original_data_loss(station,field)
    for index,row in result.iterrows():
        g=field[field.version.eq(row.version)&field.crop.eq(row.crop)].copy()
        g['variable']='harvest_biomass'
        error=(g.predicted_biomass_kg_ha-g.observed_biomass_kg_ha).to_numpy()/np.maximum(1000.,.2*g.observed_biomass_kg_ha.to_numpy())
        added=.15*float(balanced_weights(g)@error**2)
        result.loc[index,'field_biomass_loss']=added
        result.loc[index,'calibration_data_loss']+=added
    return result
