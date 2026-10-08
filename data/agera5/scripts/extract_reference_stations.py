"""Extract nearest native-grid weather for the four existing reference sites.

These are gridded reanalysis series, not station observations. Coordinates retain
the limitations documented in the project's reference_stations.geojson.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from download_agera5 import ROOT,VARIABLES,sha256,write_json


def main():
    source=ROOT.parent/'spatial/derived/reference_stations.geojson'
    features=json.loads(source.read_text())['features']
    tables={f['properties']['name']:[] for f in features}
    mappings=[]
    paths=[]
    for year in range(1996,2026):
        path=ROOT/'derived/netcdf'/f'AgERA5_v2_NCP_daily_{year}.nc'
        with xr.open_dataset(path) as ds:
            for f in features:
                p=f['properties'];lon,lat=f['geometry']['coordinates'];name=p['name']
                point=ds[[v[0] for v in VARIABLES]].sel(lon=lon,lat=lat,method='nearest').load()
                t=point.to_dataframe().reset_index().rename(columns={'time':'date','lat':'grid_lat','lon':'grid_lon'})
                t['qc_temperature']=(
                    (t.tmin>t.tmax+0.01).astype('uint8')+
                    (t.tmean<t.tmin-0.01).astype('uint8')*2+
                    (t.tmean>t.tmax+0.01).astype('uint8')*4)
                tables[name].append(t[['date']+[v[0] for v in VARIABLES]+['qc_temperature']])
                if year==1996:
                    grid_lon=float(point.lon);grid_lat=float(point.lat)
                    a1,a2=np.deg2rad([lat,grid_lat]);dl=np.deg2rad(grid_lon-lon)
                    hav=np.sin((a2-a1)/2)**2+np.cos(a1)*np.cos(a2)*np.sin(dl/2)**2
                    distance=2*6371.0088*np.arcsin(np.sqrt(hav))
                    mappings.append(dict(station=name,name_zh=p['name_zh'],reference_lon=lon,reference_lat=lat,
                                         grid_lon=grid_lon,grid_lat=grid_lat,distance_km=float(distance),
                                         coordinate_source=p['source'],coordinate_note=p['note']))
    dest=ROOT/'derived/reference_stations';dest.mkdir(parents=True,exist_ok=True)
    for name,parts in tables.items():
        df=pd.concat(parts,ignore_index=True)
        expected=pd.date_range('1996-01-01','2025-12-31',freq='D')
        assert np.array_equal(df.date.values,expected.values)
        path=dest/f'{name}_AgERA5_v2_daily_1996_2025.csv'
        df.to_csv(path,index=False,float_format='%.6f',date_format='%Y-%m-%d')
        paths.append(dict(station=name,path=str(path.relative_to(ROOT)),rows=len(df),sha256=sha256(path),
                          flagged_temperature_days=int((df.qc_temperature!=0).sum()),
                          missing_weather_values=int(df[[v[0] for v in VARIABLES]].isna().sum().sum())))
    pd.DataFrame(mappings).to_csv(dest/'grid_mapping.csv',index=False)
    write_json(ROOT/'metadata/reference_station_extraction.json',
               dict(coordinates_source=str(source.relative_to(ROOT.parent.parent)),coordinates_sha256=sha256(source),
                    data_type='Nearest-grid AgERA5 reanalysis, not station observations',
                    qc_temperature_bits={'1':'tmin > tmax + 0.01 degC','2':'tmean < tmin - 0.01 degC','4':'tmean > tmax + 0.01 degC'},
                    units={v[0]:v[3] for v in VARIABLES},files=paths))
    print(json.dumps(paths,indent=2))


if __name__=='__main__': main()
