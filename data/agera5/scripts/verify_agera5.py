"""Independently verify the delivered local AgERA5 package and write catalogs."""
import csv
import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from download_agera5 import ROOT, VARIABLES, WIDTH, HEIGHT, sha256, write_json


def main():
    raw_records=[]
    annual_records=[]
    issues=[]
    expected_days=np.arange(np.datetime64('1996-01-01'),np.datetime64('2026-01-01'),dtype='datetime64[D]')
    actual_days=[]
    stats=[]
    consistency={}
    for year in range(1996,2026):
        for month in range(1,13):
            p=ROOT/'metadata/downloads'/f'{year}{month:02d}.json'
            if not p.exists():
                issues.append(f'Missing month receipt {year}{month:02d}')
                continue
            r=json.loads(p.read_text())
            f=ROOT/r['local_path']
            if not f.exists() or sha256(f)!=r['sha256']:
                issues.append(f'Raw file missing or checksum changed: {f.name}')
            if not r['gee_band_order_verified']:
                issues.append(f'Unverified band order: {f.name}')
            raw_records.append({'kind':'raw_GEE_ZIP', 'period':r['month'], 'path':r['local_path'],
                                'bytes':r['bytes'],'sha256':r['sha256']})
        p=ROOT/'metadata/annual'/f'{year}.json'
        if not p.exists():
            issues.append(f'Missing annual NetCDF receipt {year}')
            continue
        r=json.loads(p.read_text())
        f=ROOT/r['path']
        if not f.exists() or sha256(f)!=r['sha256']:
            issues.append(f'NetCDF missing or checksum changed: {f.name}')
            continue
        with xr.open_dataset(f,engine='netcdf4') as ds:
            expected=np.arange(np.datetime64(f'{year}-01-01'),np.datetime64(f'{year+1}-01-01'),dtype='datetime64[D]')
            if not np.array_equal(ds.time.values.astype('datetime64[D]'),expected): issues.append(f'{year}: date error')
            if ds.sizes['lat']!=HEIGHT or ds.sizes['lon']!=WIDTH: issues.append(f'{year}: grid shape error')
            if not np.allclose(ds.lon.values,np.arange(127)*0.1+110.2): issues.append(f'{year}: longitude error')
            if not np.allclose(ds.lat.values,42.7-np.arange(135)*0.1): issues.append(f'{year}: latitude error')
            for v in VARIABLES:
                if ds[v[0]].attrs['units']!=v[3]: issues.append(f'{year}: units error {v[0]}')
            actual_days.extend(ds.time.values.astype('datetime64[D]'))
        if not r['netcdf_roundtrip_exact']: issues.append(f'{year}: roundtrip failed')
        for var,q in r['quality']['variables'].items():
            stats.append({'year':year,'variable':var,**q})
            if q['days_with_no_valid_pixel']: issues.append(f'{year}: entirely missing days {var}')
        for k,v in r['quality']['consistency_counts'].items(): consistency[k]=consistency.get(k,0)+v
        annual_records.append({'kind':'annual_NetCDF','period':year,'path':r['path'],'bytes':r['bytes'],'sha256':r['sha256']})
    if not np.array_equal(np.asarray(actual_days),expected_days): issues.append('Whole-period calendar mismatch')
    station_summary=[]
    station_receipt=ROOT/'metadata/reference_station_extraction.json'
    if not station_receipt.exists():
        issues.append('Missing four-station extraction receipt')
    else:
        sr=json.loads(station_receipt.read_text())
        if len(sr['files'])!=4: issues.append('Expected four station weather files')
        for r in sr['files']:
            p=ROOT/r['path']
            if not p.exists() or sha256(p)!=r['sha256']:
                issues.append('Station CSV missing or checksum changed: '+p.name)
                continue
            df=pd.read_csv(p,parse_dates=['date'])
            if not np.array_equal(df.date.values.astype('datetime64[D]'),expected_days):
                issues.append('Station calendar mismatch: '+p.name)
            if not set(v[0] for v in VARIABLES).issubset(df.columns):
                issues.append('Station variable missing: '+p.name)
            if not df.qc_temperature.between(0,7).all(): issues.append('Invalid station QC flag: '+p.name)
            station_summary.append({'station':r['station'],'rows':len(df),'sha256_checked':True,
                                    'flagged_temperature_days':int((df.qc_temperature!=0).sum()),
                                    'missing_weather_values':int(df[[v[0] for v in VARIABLES]].isna().sum().sum())})
    pixel_check=json.loads((ROOT/'metadata/gee_pixel_crosscheck.json').read_text())
    if not pixel_check['all_exact_at_float32']: issues.append('GEE pixel cross-check failed')
    catalog=ROOT/'catalog';catalog.mkdir(exist_ok=True)
    for name,records in [('files.csv',raw_records+annual_records),('quality_by_year_variable.csv',stats)]:
        if records:
            with (catalog/name).open('w',newline='') as f:
                w=csv.DictWriter(f,fieldnames=list(records[0]));w.writeheader();w.writerows(records)
    summary=dict(verified_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
                 completed=len(raw_records)==360 and len(annual_records)==30 and not issues,
                 start_date='1996-01-01',end_date='2025-12-31',days=len(actual_days),expected_days=10958,
                 raw_months=len(raw_records),annual_netcdf_files=len(annual_records),meteorological_variables=len(VARIABLES),
                 latitude_cells=HEIGHT,longitude_cells=WIDTH,
                 raw_bytes=sum(r['bytes'] for r in raw_records),derived_bytes=sum(r['bytes'] for r in annual_records),
                 raw_sha256_checked=True,netcdf_sha256_checked=True,calendar_checked=True,grid_and_units_checked=True,
                 source_consistency_counts=consistency,delivery_issues=issues,
                 reference_stations=station_summary,gee_pixel_variable_checks=pixel_check['checks'],
                 gee_pixel_crosscheck_exact=pixel_check['all_exact_at_float32'],
                 source_quality_note='Original temperature consistency anomalies are retained and counted; delivery checks do not certify meteorological accuracy.')
    write_json(catalog/'verification.json',summary)
    print(json.dumps(summary,indent=2))
    if issues: raise SystemExit(1)


if __name__=='__main__': main()
