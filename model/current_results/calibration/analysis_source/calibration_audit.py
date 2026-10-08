"""Independent calibration loss calculation from archived observations and predictions."""
import numpy as np
import pandas as pd
from audit_math import weights

def independent_losses(station,field):
    rows=[]
    for (version,crop),g in station[station.split.eq('calibration')].groupby(['version','crop']):
        scales={}
        for variable,v in g.groupby('variable'):
            w=weights(v);mean=float(w@v.value.to_numpy(float))
            spread=float(np.sqrt(w@(v.value.to_numpy(float)-mean)**2))
            scales[variable]=max(.5 if variable in ['lai','et'] else 500.,spread)
        error=(g.predicted-g.value).to_numpy()/g.variable.map(scales).to_numpy()
        station_loss=.5*float(weights(g)@error**2)
        f=field[field.version.eq(version)&field.crop.eq(crop)&field.split.eq('calibration')]
        targets=[]
        for r in f.itertuples():
            for variable,o,p,scale in [
                ('seasonal_et',r.observed_et_mm,r.predicted_et_mm,max(50.,.15*r.observed_et_mm)),
                ('yield',r.yield_13pct_kg_ha*.87,r.grain_13pct_kg_ha*.87,max(1000.,.2*r.yield_13pct_kg_ha*.87))]:
                targets.append(dict(site=r.site,source_group_id=r.source_group_id,case_id=r.case_id,
                    variable=variable,error=(p-o)/scale))
        targets=pd.DataFrame(targets);field_loss=.35*float(weights(targets)@targets.error.to_numpy()**2)
        biomass=f.assign(variable='harvest_biomass')
        z=(biomass.predicted_biomass_kg_ha-biomass.observed_biomass_kg_ha).to_numpy()/np.maximum(1000.,.2*biomass.observed_biomass_kg_ha.to_numpy())
        biomass_loss=.15*float(weights(biomass)@z**2)
        p=g[g.variable.eq('et')].groupby(['site','source_group_id','case_id']).agg(
            observed=('value','sum'),simulated=('predicted','sum')).reset_index()
        p['variable']='observed_day_ET'
        z=(p.simulated-p.observed).to_numpy()/(.15*np.maximum(20.,p.observed.to_numpy()))
        matching_loss=.05*float(weights(p)@z**2)
        differences=[]
        for _,year in f.groupby('harvest_year'):
            lo,hi=year.set_index('treatment').loc[['W0','W3']].itertuples()
            differences.append((hi.predicted_et_mm-lo.predicted_et_mm)-(hi.observed_et_mm-lo.observed_et_mm))
        response_loss=.1*float(np.mean((np.array(differences)/50.)**2))
        rows.append(dict(version=version,crop=crop,station_loss=station_loss,field_loss=field_loss,
            matched_day_ET_loss=matching_loss,response_loss=response_loss,field_biomass_loss=biomass_loss,
            calibration_data_loss=station_loss+field_loss+matching_loss+response_loss+biomass_loss))
    return pd.DataFrame(rows)


