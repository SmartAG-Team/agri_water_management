"""Independent management-qualified loss calculation from saved predictions."""
import numpy as np
import pandas as pd
from audit_math import weights


def losses(station, field):
    rows = []
    for (version, crop), group in station[station.split.eq('calibration')].groupby(['version', 'crop']):
        scales = {}
        for variable, part in group.groupby('variable'):
            w = weights(part)
            mean = w @ part.value.to_numpy()
            scales[variable] = max(.5 if variable in ['lai', 'et'] else 500.,
                                  np.sqrt(w @ (part.value.to_numpy()-mean)**2))
        retained = group.variable.ne('et').to_numpy()
        normalized = (group.predicted-group.value).to_numpy()/group.variable.map(scales).to_numpy()
        original_weights = weights(group)
        station_loss = .5 * float(original_weights[retained] @ normalized[retained]**2)
        f = field[field.version.eq(version) & field.crop.eq(crop) & field.split.eq('calibration')]
        seasonal = f.assign(variable='seasonal_et')
        z = (f.predicted_et_mm-f.observed_et_mm).to_numpy()/np.maximum(50., .15*f.observed_et_mm.to_numpy())
        ET_loss = .325 * float(weights(seasonal) @ z**2)
        grain = f.assign(variable='yield')
        z = ((f.grain_13pct_kg_ha-f.yield_13pct_kg_ha)*.87).to_numpy()/np.maximum(1000., .2*.87*f.yield_13pct_kg_ha.to_numpy())
        grain_loss = .175 * float(weights(grain) @ z**2)
        biomass = f.assign(variable='harvest_biomass')
        z = (f.predicted_biomass_kg_ha-f.observed_biomass_kg_ha).to_numpy()/np.maximum(1000., .2*f.observed_biomass_kg_ha.to_numpy())
        biomass_loss = .15 * float(weights(biomass) @ z**2)
        responses = []
        for _, year in f.groupby('harvest_year'):
            lo, hi = year.set_index('treatment').loc[['W0', 'W3']].itertuples()
            responses.append((hi.predicted_et_mm-lo.predicted_et_mm)-(hi.observed_et_mm-lo.observed_et_mm))
        response_loss = .1 * float(np.mean((np.array(responses)/50.)**2))
        rows.append(dict(version=version, crop=crop, station_growth_yield_loss=station_loss,
            field_seasonal_ET_loss=ET_loss, field_grain_yield_loss=grain_loss,
            field_biomass_loss=biomass_loss, response_loss=response_loss,
            conditional_daily_ET_loss=0., matched_day_ET_loss=0.,
            calibration_data_loss=station_loss+ET_loss+grain_loss+biomass_loss+response_loss))
    return pd.DataFrame(rows)
