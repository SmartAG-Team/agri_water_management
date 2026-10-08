"""Download native-grid AgERA5 v2 monthly subsets from Google Earth Engine.

Run with the project .venv Python. Uses existing EE credentials; writes no tokens.
Each ZIP contains one multiband GeoTIFF, in date-major / variable-minor order.
"""
import argparse
import calendar
import concurrent.futures
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import zipfile

import ee
import numpy as np
import rasterio
import requests

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "ee-gangzhaomodel"
COLLECTION = "projects/climate-engine-pro/assets/ce-ag-era5-v2/daily"
# Pixel edges, aligned to native 0.1 degree centres. This rectangle encloses the
# six complete province map extents and Beijing, with a small exterior buffer.
TRANSFORM = [0.1, 0, 110.15, 0, -0.1, 42.75]
WIDTH, HEIGHT = 127, 135
VARIABLES = [
    ("tmax", "Temperature_Air_2m_Max_24h", "K", "degC", 1., -273.15),
    ("tmin", "Temperature_Air_2m_Min_24h", "K", "degC", 1., -273.15),
    ("tmean", "Temperature_Air_2m_Mean_24h", "K", "degC", 1., -273.15),
    ("precip", "Precipitation_Flux", "mm day-1", "mm day-1", 1., 0.),
    ("srad", "Solar_Radiation_Flux", "J m-2 day-1", "MJ m-2 day-1", 1e-6, 0.),
    ("vap", "Vapour_Pressure_Mean_24h", "hPa", "kPa", 0.1, 0.),
    ("wind10m", "Wind_Speed_10m_Mean_24h", "m s-1", "m s-1", 1., 0.),
    ("et0", "ReferenceET_PenmanMonteith_FAO56", "mm day-1", "mm day-1", 1., 0.),
    ("tdew", "Dew_Point_Temperature_2m_Mean_24h", "K", "degC", 1., -273.15),
    ("rhmin", "Derived_Relative_Humidity_2m_Min_24h", "%", "%", 1., 0.),
    ("rhmax", "Derived_Relative_Humidity_2m_Max_24h", "%", "%", 1., 0.),
    ("vpd_tmax", "Vapour_Pressure_Deficit_at_Maximum_Temperature", "hPa", "kPa", 0.1, 0.),
]
BANDS = [v[1] for v in VARIABLES]
LOCK = threading.Lock()
LOCAL = threading.local()


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for b in iter(lambda: f.read(2**20), b""):
            h.update(b)
    return h.hexdigest()


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


def month_dates(year, month):
    return [dt.date(year, month, d).isoformat()
            for d in range(1, calendar.monthrange(year, month)[1] + 1)]


def verify_zip(path, year, month):
    expected = [d.replace("-", "") + "_" + b
                for d in month_dates(year, month) for b in BANDS]
    with zipfile.ZipFile(path) as z:
        if z.testzip() is not None:
            raise ValueError("ZIP CRC check failed")
        members = [n for n in z.namelist() if n.endswith(".tif")]
        if len(members) != 1:
            raise ValueError(f"Expected one TIFF, found {len(members)}")
    with zipfile.ZipFile(path) as z:
        payload = z.read(members[0])
    with rasterio.MemoryFile(payload) as memory, memory.open() as src:
        if (src.width, src.height, src.count) != (WIDTH, HEIGHT, len(expected)):
            raise ValueError(f"Unexpected raster dimensions: {src.shape}, {src.count}")
        if str(src.crs) != "EPSG:4326":
            raise ValueError("Unexpected CRS")
        if not np.allclose(tuple(src.transform)[:6], TRANSFORM, atol=1e-10):
            raise ValueError(f"Unexpected affine: {src.transform}")
        if any(src.descriptions) and list(src.descriptions) != expected:
            raise ValueError("Band ordering/names do not match dates and variables")
        # Force decompression of every band and retain source missing-value facts.
        values = np.ma.masked_invalid(src.read(masked=True))
        missing = np.ma.getmaskarray(values).sum(axis=(1, 2)).reshape(-1, len(BANDS))
        nonfinite = (~np.isfinite(values.filled(np.nan))).sum(axis=(1, 2)).reshape(-1, len(BANDS))
        return dict(tiff_member=members[0], shape=[src.count, src.height, src.width],
                    dtype=list(set(src.dtypes)), nodata_repr=str(src.nodata),
                    band_descriptions_embedded=any(src.descriptions),
                    masked_values_by_variable=dict(zip([v[0] for v in VARIABLES], missing.sum(axis=0).tolist())),
                    nonfinite_values_by_variable=dict(zip([v[0] for v in VARIABLES], nonfinite.sum(axis=0).tolist())))


def download_month(year, month):
    tag = f"{year}{month:02d}"
    out = ROOT / "raw" / str(year) / f"AgERA5_v2_NCP_{tag}.zip"
    receipt = ROOT / "metadata" / "downloads" / f"{tag}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and receipt.exists():
        previous = json.loads(receipt.read_text())
        if out.stat().st_size == previous["bytes"] and sha256(out) == previous["sha256"]:
            return dict(month=tag, status="verified_cached", bytes=out.stat().st_size)
        raise ValueError(f"Existing file failed SHA256: {out}")
    start = dt.date(year, month, 1)
    end = dt.date(year + (month == 12), month % 12 + 1, 1)
    ic = ee.ImageCollection(COLLECTION).filterDate(str(start), str(end)).sort("system:time_start").select(BANDS)
    image = ic.toBands().toFloat()
    expected_names = [d.replace("-", "") + "_" + b
                      for d in month_dates(year, month) for b in BANDS]
    actual_names = image.bandNames().getInfo()
    if actual_names != expected_names:
        raise ValueError(f"GEE monthly band order mismatch: {tag}")
    params = {"name": f"AgERA5_v2_NCP_{tag}", "crs": "EPSG:4326",
              "crs_transform": TRANSFORM, "dimensions": [WIDTH, HEIGHT],
              "filePerBand": False, "format": "ZIPPED_GEO_TIFF"}
    part = out.with_suffix(".zip.part")
    began = time.monotonic()
    for attempt in range(1, 6):
        try:
            # Signed download URLs stay in memory and are never written to logs.
            if not part.exists():
                url = image.getDownloadURL(params)
                if not hasattr(LOCAL, "session"):
                    LOCAL.session = requests.Session()
                with LOCAL.session.get(url, stream=True, timeout=(30, 240)) as r:
                    if r.status_code != 200:
                        raise RuntimeError(f"GEE download HTTP {r.status_code}")
                    with part.open("wb") as f:
                        for block in r.iter_content(2**20):
                            f.write(block)
            checks = verify_zip(part, year, month)
            part.replace(out)
            info = dict(month=tag, collection=COLLECTION, project=PROJECT,
                        dates=month_dates(year, month), bands=BANDS,
                        local_path=str(out.relative_to(ROOT)), bytes=out.stat().st_size,
                        sha256=sha256(out), retrieved_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
                        source_units={v[1]: v[2] for v in VARIABLES}, **checks)
            info["gee_band_order_verified"] = True
            info["export_band_names"] = actual_names
            write_json(receipt, info)
            return dict(month=tag, status="downloaded", bytes=out.stat().st_size,
                        seconds=round(time.monotonic()-began, 1))
        except Exception as exc:
            # Avoid leaking signed URLs in requests exceptions.
            kind = type(exc).__name__
            message = str(exc).split("https://")[0][:300]
            with LOCK:
                print(json.dumps(dict(month=tag, attempt=attempt, error_type=kind, message=message)), flush=True)
            if attempt == 5:
                raise RuntimeError(f"{tag}: {kind}: {message}") from None
            part.unlink(missing_ok=True)
            time.sleep(min(5 * 2**(attempt-1), 45))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-year", type=int, default=1996)
    ap.add_argument("--end-year", type=int, default=2025)
    ap.add_argument("--month", type=int)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    ee.Initialize(project=PROJECT)
    ee.data.setDeadline(240000)
    inventory = json.loads((ROOT / "metadata/gee_collection_inventory.json").read_text())
    if not set(BANDS).issubset(inventory["bands"]):
        raise ValueError("Requested bands unavailable")
    times = [dt.datetime.fromtimestamp(t/1000, dt.timezone.utc).date().isoformat() for t in inventory["times"]]
    expected = [str(dt.date(1996,1,1)+dt.timedelta(days=k)) for k in range(10958)]
    if sorted(times) != expected:
        raise ValueError("Source calendar is incomplete or duplicated")
    config = dict(collection=COLLECTION, project=PROJECT, period=["1996-01-01", "2025-12-31"],
                  expected_days=10958, crs="EPSG:4326", transform=TRANSFORM,
                  width=WIDTH, height=HEIGHT, bounds=[110.15, 29.25, 122.85, 42.75],
                  time_zone="local (AgERA5 source aggregation)",
                  spatial_scope="Native-grid rectangle enclosing complete Hebei, Henan, Shandong, Tianjin, Anhui, Jiangsu and Beijing; not a formal NCP boundary",
                  boundary_reference="data/spatial/derived/ccd_full_provinces_2024/province_display_boundaries.geojson",
                  variables=[dict(name=v[0], source_band=v[1], source_units=v[2], units=v[3],
                                  multiplier=v[4], offset=v[5]) for v in VARIABLES],
                  raw_layout="Date-major then variable-minor bands in monthly ZIP/GeoTIFF",
                  source_doi="10.24381/cds.6c68c9bb")
    write_json(ROOT / "metadata/config.json", config)
    jobs = [(y,m) for y in range(args.start_year,args.end_year+1)
            for m in ([args.month] if args.month else range(1,13))]
    failures = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        tasks = {pool.submit(download_month,y,m):(y,m) for y,m in jobs}
        for n,f in enumerate(concurrent.futures.as_completed(tasks),1):
            try:
                result = f.result()
            except Exception as e:
                result = dict(month=f"{tasks[f][0]}{tasks[f][1]:02d}", status="failed", message=str(e))
                failures.append(result)
            result.update(completed=n,total=len(jobs))
            print(json.dumps(result),flush=True)
    if failures:
        write_json(ROOT / "metadata/download_failures.json", failures)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
