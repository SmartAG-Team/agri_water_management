"""Prepare annual CF NetCDF files from verified monthly GEE downloads."""
import argparse
import calendar
import datetime as dt
import hashlib
import json
from pathlib import Path
import shutil
import time
import zipfile

import numpy as np
import rasterio
import xarray as xr

from download_agera5 import ROOT, VARIABLES, TRANSFORM, WIDTH, HEIGHT, sha256, write_json

LONG_NAMES = {
    "tmax": "Daily maximum 2 m air temperature",
    "tmin": "Daily minimum 2 m air temperature",
    "tmean": "Daily mean 2 m air temperature",
    "precip": "Daily precipitation (daily total expressed per day)",
    "srad": "Daily downward solar radiation energy expressed per day",
    "vap": "Daily mean vapour pressure",
    "wind10m": "Daily mean wind speed at 10 m",
    "et0": "FAO-56 Penman-Monteith grass reference evapotranspiration",
    "tdew": "Daily mean 2 m dewpoint temperature",
    "rhmin": "Daily minimum derived 2 m relative humidity",
    "rhmax": "Daily maximum derived 2 m relative humidity",
    "vpd_tmax": "Vapour pressure deficit at daily maximum temperature",
}


def month_array(year, month):
    record = json.loads((ROOT / "metadata/downloads" / f"{year}{month:02d}.json").read_text())
    p = ROOT / record["local_path"]
    if sha256(p) != record["sha256"]:
        raise ValueError(f"SHA256 mismatch: {p}")
    expected_dates = [dt.date(year, month, d).isoformat()
                      for d in range(1, calendar.monthrange(year, month)[1] + 1)]
    if record["dates"] != expected_dates or not record["gee_band_order_verified"]:
        raise ValueError("Monthly date/order verification failed")
    with zipfile.ZipFile(p) as z:
        with rasterio.MemoryFile(z.read(record["tiff_member"])) as mem, mem.open() as src:
            if src.shape != (HEIGHT, WIDTH) or src.count != len(expected_dates)*len(VARIABLES):
                raise ValueError("Wrong raster shape")
            arr = np.ma.masked_invalid(src.read(masked=True)).filled(np.nan)
    return arr.reshape(len(expected_dates),len(VARIABLES),HEIGHT,WIDTH), record


def quality(ds):
    stats = {}
    for name in [v[0] for v in VARIABLES]:
        a = ds[name].values
        valid = np.isfinite(a)
        stats[name] = {
            "units": ds[name].attrs["units"], "valid_values": int(valid.sum()),
            "missing_values": int((~valid).sum()),
            "min": float(np.nanmin(a)), "max": float(np.nanmax(a)),
            "days_with_no_valid_pixel": int((valid.sum(axis=(1,2)) == 0).sum()),
            "pixels_missing_every_day": int((valid.sum(axis=0) == 0).sum()),
            "pixels_with_partial_time_coverage": int(((valid.sum(axis=0)>0)&(valid.sum(axis=0)<a.shape[0])).sum()),
        }
    checks = {
        "tmin_gt_tmax": int((ds.tmin.values > ds.tmax.values + 0.01).sum()),
        "tmean_below_tmin": int((ds.tmean.values < ds.tmin.values - 0.01).sum()),
        "tmean_above_tmax": int((ds.tmean.values > ds.tmax.values + 0.01).sum()),
        "rhmin_gt_rhmax": int((ds.rhmin.values > ds.rhmax.values + 0.001).sum()),
    }
    for name in ["precip","srad","vap","wind10m","et0","vpd_tmax"]:
        checks[name+"_negative"] = int((ds[name].values < -1e-6).sum())
    for name in ["rhmin","rhmax"]:
        checks[name+"_outside_0_100"] = int(((ds[name].values < -0.001)|(ds[name].values > 100.001)).sum())
    return dict(variables=stats, consistency_counts=checks)


def process_year(year):
    dest = ROOT / "derived/netcdf" / f"AgERA5_v2_NCP_daily_{year}.nc"
    receipt = ROOT / "metadata/annual" / f"{year}.json"
    if dest.exists() and receipt.exists():
        old=json.loads(receipt.read_text())
        if sha256(dest)==old["sha256"]:
            return dict(year=year,status="verified_cached",bytes=dest.stat().st_size)
        raise ValueError(f"Changed existing derived file: {dest}")
    arrays, inputs = [], []
    for month in range(1,13):
        a,r = month_array(year,month)
        arrays.append(a)
        inputs.append({k:r[k] for k in ["month","local_path","sha256","bytes"]})
    raw = np.concatenate(arrays,axis=0)
    del arrays
    days = np.arange(np.datetime64(f"{year}-01-01"),np.datetime64(f"{year+1}-01-01"),dtype="datetime64[D]")
    lon = np.round(TRANSFORM[2] + (np.arange(WIDTH)+0.5)*TRANSFORM[0],8)
    lat = np.round(TRANSFORM[5] + (np.arange(HEIGHT)+0.5)*TRANSFORM[4],8)
    ds = xr.Dataset(coords={"time":days,"lat":lat,"lon":lon})
    for i,v in enumerate(VARIABLES):
        name,band,source_units,units,mult,offset = v
        data = (raw[:,i].astype("float64")*mult+offset).astype("float32")
        ds[name] = (("time","lat","lon"),data)
        ds[name].attrs.update(long_name=LONG_NAMES[name],units=units,source_band=band,
                              source_units=source_units,grid_mapping="crs",
                              conversion=f"source * {mult:g} + {offset:g}")
    del raw
    ds["crs"] = xr.DataArray(np.int32(0), attrs={"grid_mapping_name":"latitude_longitude", "epsg_code":"EPSG:4326",
                                                "semi_major_axis":6378137., "inverse_flattening":298.257223563})
    ds.lon.attrs.update(standard_name="longitude",units="degrees_east",axis="X")
    ds.lat.attrs.update(standard_name="latitude",units="degrees_north",axis="Y")
    ds.time.attrs.update(standard_name="time",axis="T",comment="Calendar date of the original AgERA5 local-day aggregation; no UTC reaggregation applied.")
    ds.attrs.update(Conventions="CF-1.8",title="AgERA5 v2 daily meteorological data for NCP and complete surrounding provinces",
                    source="Copernicus Climate Change Service AgERA5 v2, mirrored in Google Earth Engine by Climate Engine",
                    source_doi="10.24381/cds.6c68c9bb",
                    earth_engine_collection="projects/climate-engine-pro/assets/ce-ag-era5-v2/daily",
                    geospatial_lon_min=110.15,geospatial_lon_max=122.85,geospatial_lat_min=29.25,geospatial_lat_max=42.75,
                    native_grid_spacing_degrees=0.1, temporal_resolution="daily",
                    spatial_scope="Rectangle enclosing complete Hebei, Henan, Shandong, Tianjin, Anhui, Jiangsu and Beijing; not a formal NCP boundary",
                    missing_data="Nonfinite source cells retained as missing; no spatial or temporal gap filling",
                    processing="Native EPSG:4326 pixel centres and original calendar dates retained; unit conversion only; no spatial interpolation",
                    reference_et_note="ET0 is grass reference evapotranspiration, not actual crop evapotranspiration or irrigation requirement",
                    history=dt.datetime.now(dt.timezone.utc).isoformat()+": downloaded from GEE and converted to annual NetCDF")
    qa=quality(ds)
    dest.parent.mkdir(parents=True,exist_ok=True)
    tmp=dest.with_suffix(".nc.part")
    enc={v[0]:{"dtype":"float32","zlib":True,"complevel":4,"shuffle":True,
                "chunksizes":(min(31,len(days)),HEIGHT,WIDTH),"_FillValue":np.float32(-9999.)} for v in VARIABLES}
    enc["time"]={"units":"days since 1996-01-01 00:00:00","calendar":"proleptic_gregorian","dtype":"int32"}
    ds.to_netcdf(tmp,engine="netcdf4",encoding=enc)
    with xr.open_dataset(tmp,engine="netcdf4") as check:
        if not np.array_equal(check.time.values,ds.time.values): raise ValueError("NetCDF time changed")
        for name in [v[0] for v in VARIABLES]:
            if not np.array_equal(check[name].values,ds[name].values,equal_nan=True):
                raise ValueError(f"NetCDF roundtrip changed {name}")
    tmp.replace(dest)
    record=dict(year=year,days=len(days),shape=[len(days),HEIGHT,WIDTH],variables=len(VARIABLES),
                path=str(dest.relative_to(ROOT)),bytes=dest.stat().st_size,sha256=sha256(dest),
                source_files=inputs,netcdf_roundtrip_exact=True,quality=qa)
    write_json(receipt,record)
    ds.close()
    return dict(year=year,status="prepared",bytes=record["bytes"],checks=qa["consistency_counts"])


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--start-year",type=int,default=1996)
    ap.add_argument("--end-year",type=int,default=2025)
    ap.add_argument("--available-only",action="store_true")
    ap.add_argument("--follow",action="store_true",help="Prepare each year as its 12 monthly downloads finish")
    args=ap.parse_args()
    done=set()
    while True:
        for year in range(args.start_year,args.end_year+1):
            if year in done: continue
            complete=all((ROOT/"metadata/downloads"/f"{year}{m:02d}.json").exists() for m in range(1,13))
            if not complete and (args.available_only or args.follow): continue
            print(json.dumps(process_year(year)),flush=True)
            done.add(year)
        if not args.follow or len(done)==args.end_year-args.start_year+1: break
        time.sleep(30)


if __name__=="__main__": main()
