"""Source-backed regional inputs; operational plain domain, never an official NCP boundary.

Run from the project with .venv/bin/python PATH --stage all. Existing archives are
read only. Outputs remain inside this model run. Earth Engine uses existing local
OAuth through its client; no credentials are serialized.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import ee
import numpy as np
import pandas as pd
from pyproj import Geod, Proj, Transformer
import rasterio
from rasterio.features import rasterize, shapes
from rasterio.transform import from_origin
from rasterio.warp import transform_bounds
from rasterio.windows import Window, from_bounds
import requests
from scipy import ndimage
from shapely.geometry import box, mapping, shape
from shapely.ops import unary_union
import xarray as xr

RUN = Path(__file__).resolve().parents[1]
ROOT = RUN.parents[1]
OUT = RUN / "data/regional"
RAW = OUT / "raw_gee"
ENVELOPE = [112.0, 32.0, 121.5, 41.0]
DX = 0.01
HEIGHT, WIDTH = 900, 950
TRANSFORM = from_origin(112, 41, DX, DX)
DEPTHS = ["0-5cm", "5-15cm", "15-30cm", "30-60cm", "60-100cm", "100-200cm"]
PROPERTIES = ["sand", "silt", "clay", "bdod", "soc", "cfvo"]
BASIN_ASSET = "WWF/HydroSHEDS/v1/Basins/hybas_7"
SEEDS = {"Hai_Ziya": [116.0, 38.0], "Yellow_lower": [115.0, 34.9],
         "Huai_lower": [116.9, 33.1]}
PLAIN_SEEDS = [[115.5, 37.5], [116, 35], [117, 33.5]]
GEOD = Geod(ellps="WGS84")


def utc():
    return datetime.now(timezone.utc).isoformat()


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def cell_index(lon, lat):
    return np.floor((41 - np.asarray(lat)) / DX).astype(int), np.floor((np.asarray(lon) - 112) / DX).astype(int)


def download_property(name):
    path = RAW / f"{name}_mean_0p01deg.tif"
    record = path.with_suffix(".json")
    if record.exists() and path.exists():
        meta = json.loads(record.read_text())
        if meta["sha256"] == sha256(path):
            return meta
        raise ValueError(f"Changed completed export: {path}")
    if name == "srtm":
        asset = "USGS/SRTMGL1_003"
        native = ee.Image(asset).select("elevation")
        support = native.resample("bilinear").reproject(crs="EPSG:4326", crsTransform=[0.001, 0, 112, 0, -0.001, 41])
        image = support.rename("elevation_m").addBands(ee.Terrain.slope(support).rename("support_slope_deg"))
        bands = ["elevation_m", "support_slope_deg"]
    else:
        asset = f"projects/soilgrids-isric/{name}_mean"
        native = image = ee.Image(asset)
        bands = native.bandNames().getInfo()
    native_projection = native.select(0).projection().getInfo()
    # Aggregation is explicit. Slope is derived at0.001degree support because
    # native30m slope aggregation exceeds Earth Engine memory for this domain.
    aggregated = image.reduceResolution(reducer=ee.Reducer.mean(), maxPixels=256 if name == "srtm" else 4096).reproject(
        crs="EPSG:4326", crsTransform=list(TRANSFORM)[:6])
    params = dict(name=f"regional_{name}", crs="EPSG:4326",
                  crs_transform=list(TRANSFORM)[:6], dimensions=[WIDTH, HEIGHT],
                  format="GEO_TIFF", filePerBand=False)
    partial = path.with_suffix(".part")
    def retrieve(target, export_parameters):
        url = aggregated.toFloat().getDownloadURL(export_parameters)
        with requests.get(url, timeout=(30, 600), stream=True) as response:
            if not response.ok:
                try:
                    message = response.json().get("error", {}).get("message", "unknown download failure")
                except ValueError:
                    message = response.text[:500]
                raise RuntimeError(f"Earth Engine export HTTP{response.status_code}: {message}")
            with target.open("wb") as stream:
                for chunk in response.iter_content(1024 * 1024):
                    stream.write(chunk)
    tile_records = []
    if name == "srtm":
        # Whole-envelope computations exceed the GEE interactive memory limit.
        # Keep25 actual source tiles and join only after all have succeeded.
        tiles_dir = RAW / "srtm_tiles"
        tiles_dir.mkdir(exist_ok=True)
        def tile_download(item):
            iy, ix = item
            tile = tiles_dir / f"srtm_r{iy}_c{ix}.tif"
            tile_params = dict(params, name=f"srtm_r{iy}_c{ix}",
                crs_transform=[DX, 0, 112 + ix * 190 * DX, 0, -DX, 41 - iy * 180 * DX], dimensions=[190, 180])
            if not tile.exists():
                tile_partial = tile.with_suffix(".part")
                retrieve(tile_partial, tile_params)
                with rasterio.open(tile_partial) as source:
                    if source.shape != (180, 190) or source.count !=2:
                        raise ValueError("Unexpected terrain tile")
                tile_partial.rename(tile)
            return dict(path=str(tile), row=iy, col=ix, sha256=sha256(tile))
        with ThreadPoolExecutor(max_workers=2) as pool:
            tile_records = list(pool.map(tile_download, [(iy, ix) for iy in range(5) for ix in range(5)]))
        mosaic = np.full((2, HEIGHT, WIDTH), np.nan, dtype="float32")
        for tile in tile_records:
            with rasterio.open(tile["path"]) as source:
                mosaic[:, tile["row"]*180:(tile["row"]+1)*180, tile["col"]*190:(tile["col"]+1)*190] = source.read(masked=True).filled(np.nan)
        with rasterio.open(partial, "w", driver="GTiff", count=2, height=HEIGHT, width=WIDTH,
                           dtype="float32", crs="EPSG:4326", transform=TRANSFORM, nodata=np.nan, compress="deflate") as destination:
            destination.write(mosaic)
    else:
        retrieve(partial, params)
    with rasterio.open(partial) as source:
        if source.shape != (HEIGHT, WIDTH) or source.count != len(bands):
            raise ValueError(f"Unexpected export grid/bands: {name}")
        values = source.read(masked=True)
        coverage = [dict(band=b, valid=int(np.ma.count(values[i])),
                         minimum=float(values[i].min()), maximum=float(values[i].max()))
                    for i, b in enumerate(bands)]
    partial.rename(path)
    meta = dict(asset=asset, accessed_utc=utc(), path=str(path), bytes=path.stat().st_size,
                sha256=sha256(path), bands=bands, native_projection=native_projection,
                export_crs="EPSG:4326", export_transform=list(TRANSFORM)[:6],
                export_shape=[HEIGHT, WIDTH], envelope=ENVELOPE,
                operator="SRTM bilinear resampling to0.001degree support; slope computed atthat support; aligned10x10 mean; not mean native30m slope" if name == "srtm" else "native pixel mean using Earth Engine reduceResolution; mask preserved",
                support="0.01 degree aggregation; not native 1km measurements", coverage=coverage, source_tiles=tile_records)
    dump(record, meta)
    print(f"Exported {name}: {path.stat().st_size / 1e6:.1f} MB", flush=True)
    return meta


def fetch_basins():
    path = RAW / "hydrobasins_level7_source_v2.geojson"
    meta_path = RAW / "hydrobasins_level7_source_v2.json"
    if path.exists() and meta_path.exists():
        meta = json.loads(meta_path.read_text())
        if sha256(path) != meta["sha256"]:
            raise ValueError("Completed basin snapshot changed")
        return meta
    collection = ee.FeatureCollection(BASIN_ASSET)
    region = ee.Geometry.Rectangle(ENVELOPE, geodesic=False)
    seed_records = {}
    for label, coordinates in SEEDS.items():
        feature = collection.filterBounds(ee.Geometry.Point(coordinates)).first()
        seed_records[label] = dict(seed=coordinates, properties=feature.toDictionary().getInfo())
    # Hai-system rivers have multiple outlets; include outlet/coastal subbasins,
    # rather than assuming one seed's MAIN_BAS covers the entire Hai River system.
    nearby = collection.filterBounds(region)
    outlets = nearby.filter(ee.Filter.Or(ee.Filter.eq("COAST", 1), ee.Filter.eq("ORDER", 1)))
    main_ids = sorted(set(outlets.aggregate_array("MAIN_BAS").getInfo()) |
                      {v["properties"]["MAIN_BAS"] for v in seed_records.values()})
    # Huai drains into the Yangtze in this topology. Exclude the Yangtze main
    # basin, then retain its explicitly identified Huai Pfafstetter subtree.
    # The Huai seeds havePFAF4342203/4342500/4342603; the lower Yangtze
    # control has4343030. Preserve both the selection and these controls.
    yangtze = collection.filterBounds(ee.Geometry.Point([118.5, 31.8])).first().toDictionary().getInfo()
    main_ids = [value for value in main_ids if value != yangtze["MAIN_BAS"]]
    selected = nearby.filter(ee.Filter.Or(ee.Filter.inList("MAIN_BAS", main_ids),
                             ee.Filter.And(ee.Filter.gte("PFAF_ID", 4342000), ee.Filter.lt("PFAF_ID", 4343000))))
    geojson = selected.getInfo()
    dump(path, geojson)
    meta = dict(asset=BASIN_ASSET, accessed_utc=utc(), path=str(path), sha256=sha256(path),
                envelope=ENVELOPE, seed_records=seed_records, selected_main_bas_ids=main_ids,
                excluded_yangtze_main_bas=yangtze["MAIN_BAS"], features=len(geojson["features"]),
                huai_pfaf_range=[4342000, 4342999], lower_yangtze_control=yangtze,
                selection="Named lower Hai/Ziya and Yellow main basins plus coastal/order1 outlets; exclude Yangtze MAIN_BAS except identified Huai Pfafstetter4342 subtree; retain actual HYBAS_IDs",
                limitation="Operational drainage coverage; not an official physiographic NCP boundary")
    dump(meta_path, meta)
    print(f"Fetched {meta['features']} basin polygons", flush=True)
    return meta


def row_areas():
    # Exact ellipsoidal area of each regular geographic aggregation cell.
    result = []
    for row in range(HEIGHT):
        top = 41 - row * DX
        a, _ = GEOD.polygon_area_perimeter([112, 112 + DX, 112 + DX, 112],
                                           [top, top, top - DX, top - DX])
        result.append(abs(a))
    return np.asarray(result)


def raster_write(path, values, nodata=255):
    with rasterio.open(path, "w", driver="GTiff", height=HEIGHT, width=WIDTH,
                       count=1, dtype=values.dtype, crs="EPSG:4326", transform=TRANSFORM,
                       nodata=nodata, compress="deflate") as destination:
        destination.write(values, 1)


def construct_domains():
    with rasterio.open(RAW / "srtm_mean_0p01deg.tif") as source:
        terrain = source.read(masked=True).filled(np.nan)
    source_basins = json.loads((RAW / "hydrobasins_level7_source_v2.geojson").read_text())
    envelope = box(*ENVELOPE)
    geometries = [(mapping(shape(f["geometry"]).intersection(envelope)), 1)
                  for f in source_basins["features"] if shape(f["geometry"]).intersects(envelope)]
    coverage = rasterize(geometries, out_shape=(HEIGHT, WIDTH), transform=TRANSFORM,
                         fill=0, all_touched=False).astype(bool)
    raster_write(OUT / "selected_basin_coverage_0p01deg.tif", coverage.astype("uint8"))
    areas = row_areas()[:, None]
    stations = json.loads((ROOT / "data/spatial/metadata/stations.json").read_text())
    domains = []
    for elevation in [100, 200, 300]:
        for slope in [1, 2, 3]:
            eligible = coverage & np.isfinite(terrain).all(axis=0) & (terrain[0] <= elevation) & (terrain[1] <= slope)
            labels, n = ndimage.label(eligible, structure=np.ones((3, 3)))
            selected = set()
            seed_details = []
            for x, y in PLAIN_SEEDS:
                row, col = cell_index(x, y)
                # A nearby 0.15degree neighbourhood accommodates seed placement
                # on towns/rivers; select its largest local lowland component.
                window = labels[max(0, row - 15):row + 16, max(0, col - 15):col + 16]
                ids, counts = np.unique(window[window > 0], return_counts=True)
                if len(ids):
                    label = int(ids[np.argmax(counts)])
                    selected.add(label)
                    seed_details.append(dict(seed=[x, y], selected_component=label))
                else:
                    seed_details.append(dict(seed=[x, y], selected_component=None))
            mask = np.isin(labels, list(selected)) & eligible
            name = f"operational_plain_e{elevation}_s{slope}"
            path = OUT / f"{name}_0p01deg.tif"
            raster_write(path, mask.astype("uint8"))
            station_rows = []
            for station in stations:
                row, col = cell_index(station["lon"], station["lat"])
                station_rows.append(dict(station=station["name"], lon=station["lon"], lat=station["lat"],
                                         included=bool(mask[row, col]),
                                         elevation_m=float(terrain[0, row, col]),
                                         mean_support_slope_deg=float(terrain[1, row, col])))
            domains.append(dict(name=name, path=str(path), sha256=sha256(path),
                                elevation_max_m=elevation, mean_support_slope_max_deg=slope,
                                area_km2=float(np.sum(mask * areas) / 1e6), pixels=int(mask.sum()),
                                component_seed_selection=seed_details, stations=station_rows,
                                connected_components_before_selection=int(n),
                                definition="Drainage-constrained, connected lowland candidate; no official full-NCP certification"))
            if elevation == 200 and slope == 2:
                polygons = [shape(g) for g, value in shapes(mask.astype("uint8"), mask=mask, transform=TRANSFORM) if value == 1]
                dump(OUT / "operational_plain_base_exact_geometry.geojson",
                     dict(type="FeatureCollection", features=[dict(type="Feature", geometry=mapping(unary_union(polygons)),
                          properties=dict(definition=name, official_ncp_boundary=False, elevation_max_m=200, mean_support_slope_max_deg=2))]))
    dump(OUT / "operational_domains.json", dict(created_utc=utc(), envelope=ENVELOPE,
         base="operational_plain_e200_s2", transform=list(TRANSFORM)[:6],
         connectivity="8-neighbour; largest local component within0.15degree of each three lowland seeds; no artificial bridges",
         domains=domains, limitation="Operational physiographic candidate. Boundary sensitivity is a design sensitivity, not proof of official whole-NCP coverage."))
    return domains


def aggregate_rotation(year, longitude, latitude, weights, sr, sc, soil, basis, quality):
    weather_col = np.floor((longitude - 110.15) / 0.1).astype(int)
    weather_row = np.floor((42.75 - latitude) / 0.1).astype(int)
    bins = weather_row * 127 + weather_col
    size = 135 * 127
    area = np.bincount(bins, weights=weights, minlength=size)
    ix = np.flatnonzero(area > 0)
    table = pd.DataFrame(dict(year=year, zone_id=[f"ag_{i // 127:03d}_{i % 127:03d}" for i in ix],
                 agera5_lat_index=ix // 127, agera5_lon_index=ix % 127,
                 latitude=42.7 - (ix // 127) * 0.1, longitude=110.2 + (ix % 127) * 0.1,
                 mapped_rotation_area_ha=area[ix], native_rotation_cells=np.bincount(bins, minlength=size)[ix]))
    table["area_basis"] = basis
    for key, grid in soil.items():
        values = grid[sr, sc]
        finite = np.isfinite(values)
        available = np.bincount(bins[finite], weights=weights[finite], minlength=size)
        total = np.bincount(bins[finite], weights=weights[finite] * values[finite], minlength=size)
        table[key] = np.divide(total[ix], available[ix], out=np.full(len(ix), np.nan), where=available[ix] > 0)
        table[key + "_coverage_fraction"] = available[ix] / area[ix]
    for code, name in [(1, "good"), (2, "moderate"), (3, "low")]:
        qarea = np.bincount(bins[quality == code], weights=weights[quality == code], minlength=size)
        table[f"chinacp_da_{name}_area_fraction"] = qarea[ix] / area[ix]
    known = np.isin(quality, [1, 2, 3])
    qarea = np.bincount(bins[~known], weights=weights[~known], minlength=size)
    table["chinacp_da_unknown_area_fraction"] = qarea[ix] / area[ix]
    return table


def sample_projected_grid(path, px, py, source_crs):
    with rasterio.open(path) as source:
        if source.crs != source_crs:
            px, py = Transformer.from_crs(source_crs, source.crs, always_xy=True).transform(px, py)
        pad = 2 * max(abs(source.transform.a), abs(source.transform.e))
        bounds = [float(np.min(px)) - pad, float(np.min(py)) - pad,
                  float(np.max(px)) + pad, float(np.max(py)) + pad]
        window = from_bounds(*bounds, transform=source.transform).round_offsets().round_lengths()
        values = source.read(1, window=window, boundless=True, masked=True, out_dtype="float64").filled(np.nan)
        transform = source.window_transform(window)
        row = np.floor((np.asarray(py) - transform.f) / transform.e).astype(int)
        col = np.floor((np.asarray(px) - transform.c) / transform.a).astype(int)
        return values[row, col]


def cartographic_province_grid():
    path = ROOT / "data/spatial/derived/context_provinces.geojson"
    features = json.loads(path.read_text())["features"]
    names = {i + 1: feature["properties"]["name"] for i, feature in enumerate(features)}
    grid = rasterize([(feature["geometry"], i + 1) for i, feature in enumerate(features)],
                     out_shape=(HEIGHT, WIDTH), transform=TRANSFORM, fill=0, all_touched=False)
    return grid, names, path


def crop_and_soil(domains):
    with rasterio.open(OUT / "operational_plain_e200_s2_0p01deg.tif") as source:
        base = source.read(1) == 1
    domain_masks = {}
    for domain in domains:
        with rasterio.open(domain["path"]) as source:
            domain_masks[domain["name"]] = source.read(1) == 1
    province_grid, province_names, province_source = cartographic_province_grid()
    soil = {}
    for prop in PROPERTIES:
        with rasterio.open(RAW / f"{prop}_mean_0p01deg.tif") as source:
            values = source.read(masked=True).filled(np.nan)
        metadata = json.loads((RAW / f"{prop}_mean_0p01deg.json").read_text())
        if metadata["bands"] != [f"{prop}_{depth}_mean" for depth in DEPTHS]:
            raise ValueError(f"Unexpected SoilGrids depth-band order for {prop}")
        for index, depth in enumerate(DEPTHS):
            soil[f"{prop}_{depth}"] = values[index]
    with rasterio.open(RAW / "srtm_mean_0p01deg.tif") as source:
        soil["elevation_m"] = source.read(1, masked=True).filled(np.nan)
    rr, cc = np.nonzero(base)
    soil_rows = dict(row=rr, col=cc, longitude=112 + (cc + 0.5) * DX,
                     latitude=41 - (rr + 0.5) * DX, cell_area_ha=row_areas()[rr] / 10000)
    soil_rows.update({key: value[rr, cc] for key, value in soil.items()})
    pd.DataFrame(soil_rows).to_csv(OUT / "soil_raw_properties_operational_base.csv.gz", index=False)
    tables, cpm_tables, sensitivities, inputs, province_rows = [], [], [], [], []
    common_chinacp = None
    cpm_path = ROOT / "data/spatial/extracted/chinacp_500m_2015_2021/CPM/CPM.tif"
    for year in range(2015, 2022):
        path = ROOT / f"data/spatial/extracted/chinacp_500m_2015_2021/ChinaCP/ChinaCP_{year}.tif"
        da_path = ROOT / f"data/spatial/extracted/chinacp_500m_2015_2021/ChinaCP-DA/ChinaCP-DA{year}.tif"
        with rasterio.open(path) as source:
            bounds = transform_bounds("EPSG:4326", source.crs, *ENVELOPE, densify_pts=21)
            window = from_bounds(*bounds, transform=source.transform).round_offsets().round_lengths()
            classes = source.read(1, window=window)
            local_transform = source.window_transform(window)
            r, c = np.nonzero(classes == 246)
            px, py = rasterio.transform.xy(local_transform, r, c)
            px, py = np.asarray(px), np.asarray(py)
            longitude, latitude = Transformer.from_crs(source.crs, "EPSG:4326", always_xy=True).transform(px, py)
            longitude, latitude = np.asarray(longitude), np.asarray(latitude)
            sr, sc = cell_index(longitude, latitude)
            valid = (sr >= 0) & (sr < HEIGHT) & (sc >= 0) & (sc < WIDTH)
            longitude, latitude, sr, sc, px, py = [a[valid] for a in [longitude, latitude, sr, sc, px, py]]
            # Preserve physical whole-cell and fractional cropland estimates separately.
            scale = np.asarray(Proj(source.crs).get_factors(longitude, latitude).areal_scale)
            area_ha = abs(source.transform.a * source.transform.e) / scale / 10000
            cpm_percent = sample_projected_grid(cpm_path, px, py, source.crs)
            if not (np.isfinite(cpm_percent).all() and (cpm_percent >= 0).all() and (cpm_percent <= 100).all()):
                raise ValueError("CPM source percentages missing or outside 0--100")
            with rasterio.open(da_path) as da:
                aligned_quality = da.crs == source.crs and da.transform.almost_equals(source.transform, precision=1e-6) and da.shape == source.shape
                quality_crs = str(da.crs)
                quality_transform = list(da.transform)[:6]
                if aligned_quality:
                    quality = da.read(1, window=window)[r, c][valid]
                else:
                    # The2021 archive's DA is EPSG4326, unlike its class map.
                    # Preserve it and sample actual geographic source cells.
                    quality = sample_projected_grid(da_path, px, py, source.crs)
            included = base[sr, sc]
            for name, mask in domain_masks.items():
                flag = mask[sr, sc]
                sensitivities.append(dict(year=year, domain=name, mapped_rotation_area_ha=float(area_ha[flag].sum()),
                    cpm_scaled_rotation_area_ha=float((area_ha * cpm_percent / 100)[flag].sum()),
                    native_rotation_cells=int(flag.sum()),
                    area_basis="Native 500m class246 centre assignment; source projection-scale-corrected physical area; CPM separately applies static2020 cropland percentage"))
            x, y, sr, sc, weights, cp, qa = [a[included] for a in [longitude, latitude, sr, sc, area_ha, cpm_percent, quality]]
            table = aggregate_rotation(year, x, y, weights, sr, sc, soil,
                "Whole classified 500m rotation-cell physical area; centre-assigned domain/weather cell; not observed harvested area", qa)
            cpm_table = aggregate_rotation(year, x, y, weights * cp / 100, sr, sc, soil,
                "Class246 physical cell area multiplied by nearest-source-cell CPM2020 cropland percentage; static2020 mixed-pixel correction; not observed harvested area", qa)
            cpm_area = cpm_table.set_index("zone_id").mapped_rotation_area_ha
            table["cpm_scaled_rotation_area_ha"] = table.zone_id.map(cpm_area).fillna(0)
            whole_area = table.set_index("zone_id").mapped_rotation_area_ha
            cpm_table["whole_classified_cell_area_ha"] = cpm_table.zone_id.map(whole_area)
            cpm_table["cpm_source_year"] = 2020
            tables.append(table)
            cpm_tables.append(cpm_table)
            province_ids = province_grid[sr, sc]
            for pid in np.unique(province_ids):
                flag = province_ids == pid
                province_rows.append(dict(year=year, province_context=province_names.get(int(pid), "Unassigned cartographic context"),
                    mapped_rotation_area_ha=float(weights[flag].sum()), cpm_scaled_rotation_area_ha=float((weights * cp / 100)[flag].sum()),
                    crop_pixel_latitude_min=float(y[flag].min()), crop_pixel_latitude_max=float(y[flag].max()),
                    native_rotation_cells=int(flag.sum()), boundary_source=str(province_source),
                    boundary_use="Cartographic coverage diagnostic; province union does not define simulation domain"))
            if year == 2020:
                common = x < 120
                common_chinacp = aggregate_rotation(year, x[common], y[common], weights[common], sr[common], sc[common], {},
                    "Whole class246 cell area within CCD common envelope112--120E32--41N and operational base", qa[common])
                cp_common = aggregate_rotation(year, x[common], y[common], (weights * cp / 100)[common], sr[common], sc[common], {},
                    "CPM2020-scaled class246 cell area within CCD common envelope", qa[common])
                common_chinacp["chinacp_cpm_scaled_area_ha"] = common_chinacp.zone_id.map(cp_common.set_index("zone_id").mapped_rotation_area_ha).fillna(0)
                common_chinacp.to_csv(OUT / "chinacp_2020_ccd_common_extent.csv", index=False)
            inputs.append(dict(path=str(path), sha256=sha256(path), year=year, source_crs=str(source.crs),
                classification_value=246, source_window=[window.col_off, window.row_off, window.width, window.height],
                data_availability_path=str(da_path), data_availability_sha256=sha256(da_path),
                data_availability_crs=quality_crs, data_availability_transform=quality_transform,
                data_availability_operator="Same cell grid within1e-6map units" if aligned_quality else "Nearest actual source native cell at ChinaCP pixel centre after CRS transform",
                provenance_archive=str(ROOT / "data/spatial/raw/chinacp_500m_2015_2021/ChinaCP.zip"),
                provenance_manifest=str(ROOT / "data/spatial/catalog/files.csv")))
            print(f"ChinaCP {year}: {len(table)} weather zones; {weights.sum() / 1e6:.3f} million whole-cell ha; {(weights * cp / 100).sum() / 1e6:.3f} million CPM-scaled ha", flush=True)
    combined = pd.concat(tables, ignore_index=True)
    cp_combined = pd.concat(cpm_tables, ignore_index=True)
    combined.to_csv(OUT / "rotation_area_soil_by_agera5_cell_2015_2021.csv.gz", index=False)
    cp_combined.to_csv(OUT / "rotation_area_soil_by_agera5_cell_2015_2021_cpm_scaled.csv.gz", index=False)
    combined[combined.year == 2020].to_csv(OUT / "regional_drivers_agera5_2020.csv", index=False)
    cp_combined[cp_combined.year == 2020].to_csv(OUT / "regional_drivers_agera5_2020_cpm_scaled.csv", index=False)
    pd.DataFrame(sensitivities).to_csv(OUT / "rotation_area_boundary_sensitivity_2015_2021.csv", index=False)
    pd.DataFrame(province_rows).to_csv(OUT / "rotation_area_cartographic_province_coverage_2015_2021.csv", index=False)
    dump(OUT / "regional_driver_metadata.json", dict(created_utc=utc(), official_ncp_boundary=False,
         base_domain=str(OUT / "operational_plain_e200_s2_0p01deg.tif"), envelope=ENVELOPE,
         crop_inputs=inputs, crop_years=list(range(2015, 2022)), soil_raw_mapped_units=True,
         soil_units=dict(sand="g/kg", silt="g/kg", clay="g/kg", bdod="cg/cm3", soc="dg/kg", cfvo="volume permille"),
         pedotransfer_status="Raw mapped properties retained; hydraulic PTF belongs to the separate scenario adapter",
         table=str(OUT / "regional_drivers_agera5_2020.csv"),
         cpm_scaled_table=str(OUT / "regional_drivers_agera5_2020_cpm_scaled.csv"),
         all_years=str(OUT / "rotation_area_soil_by_agera5_cell_2015_2021.csv.gz"),
         all_years_cpm_scaled=str(OUT / "rotation_area_soil_by_agera5_cell_2015_2021_cpm_scaled.csv.gz"),
         cpm_source=dict(path=str(cpm_path), sha256=sha256(cpm_path), year=2020,
             units="percent0--100", operator="Nearest source native500m CPM cell at each ChinaCP native500m pixel centre; grids have different origins, no forced identical-grid assumption"),
         weather_paths=[str(ROOT / f"data/agera5/derived/netcdf/AgERA5_v2_NCP_daily_{year}.nc") for year in range(1996, 2026)],
         method="Regional soil means weighted by mapped rotation-cell physical areas; native UTM areal-scale correction; class500m centres assign0.01degree domain and0.1degree weather cells; CPM-scaled variant uses its own weights",
         limitations=["Operational domain not certified official whole NCP", "Static2020 area with30yr forcing is a fixed cropping-footprint scenario, not observed historical area", "Whole500m classified-cell area and CPM2020-scaled crop-area variant retained separately", "CPM2020 fraction is static for2015--2021 and nearest-centre sampled because source grid origins differ", "500m cell-centre boundary approximation", "SoilGrids modeled properties are not independent field hydraulics", "PTF of average soil properties differs from average of spatially distributed PTF outputs", "CCD source agreement is a sensitivity check, not independent field validation"] ))
    return combined


def compare_ccd():
    path = ROOT / "data/spatial/derived/ccd_30m_2020_2024/NCPbox_wheat_maize_2020_nativegrid.tif"
    with rasterio.open(OUT / "operational_plain_e200_s2_0p01deg.tif") as source:
        base = source.read(1) == 1
    size = 135 * 127
    rotation = np.zeros(size)
    represented = np.zeros(size)
    conflict = np.zeros(size)
    with rasterio.open(path) as source:
        if source.crs.to_epsg() != 4326:
            raise ValueError("CCD comparator must retain its geographic native grid")
        step_x, step_y = source.transform.a, -source.transform.e
        lat_top = source.transform.f - np.arange(source.height) * step_y
        # Each CCD row's physical pixel area is evaluated on WGS84.
        areas = np.asarray([abs(GEOD.polygon_area_perimeter([112, 112 + step_x, 112 + step_x, 112],
                            [t, t, t - step_y, t - step_y])[0]) / 10000 for t in lat_top])
        for start in range(0, source.height, 256):
            values = source.read(1, window=Window(0, start, source.width, min(256, source.height-start)))
            for flag, target in [(values == 1, rotation), (np.isin(values, [0, 1]), represented), (values == 254, conflict)]:
                row, col = np.nonzero(flag)
                lon = source.transform.c + (col + 0.5) * step_x
                lat = source.transform.f - (row + start + 0.5) * step_y
                sr, sc = cell_index(lon, lat)
                valid = (sr >= 0) & (sr < HEIGHT) & (sc >= 0) & (sc < WIDTH) & (lon < 120)
                lon, lat, sr, sc, row = [a[valid] for a in [lon, lat, sr, sc, row]]
                inside = base[sr, sc]
                weather_col = np.floor((lon[inside] - 110.15) / 0.1).astype(int)
                weather_row = np.floor((42.75 - lat[inside]) / 0.1).astype(int)
                bins = weather_row * 127 + weather_col
                target += np.bincount(bins, weights=areas[row[inside] + start], minlength=size)
            if start % 8192 == 0:
                print(f"CCD2020 native rows {start}/{source.height}", flush=True)
        source_meta = dict(path=str(path), sha256=sha256(path), native_shape=list(source.shape),
             native_transform=list(source.transform)[:6], native_crs=str(source.crs),
             derived_class_1="Original CCD class9 wheat-maize rotation", class_0="Other nonzero CCD crop codes", class_254="Overlapping province conflict, excluded", class_255="Source background/noncrop/NoData/outsideselectedcoverage, excluded", source_tags=source.tags())
    ix = np.flatnonzero(represented + rotation + conflict > 0)
    table = pd.DataFrame(dict(zone_id=[f"ag_{i // 127:03d}_{i % 127:03d}" for i in ix],
        ccd2020_rotation_area_ha=rotation[ix], ccd2020_known_crop_area_ha=represented[ix], ccd2020_conflict_area_ha=conflict[ix]))
    chinacp = pd.read_csv(OUT / "chinacp_2020_ccd_common_extent.csv")
    merged = chinacp.rename(columns={"mapped_rotation_area_ha": "chinacp_whole_class_area_ha"}).merge(table, on="zone_id", how="outer")
    area_fields = ["chinacp_whole_class_area_ha", "chinacp_cpm_scaled_area_ha", "ccd2020_rotation_area_ha", "ccd2020_known_crop_area_ha", "ccd2020_conflict_area_ha"]
    merged[area_fields] = merged[area_fields].fillna(0)
    merged.to_csv(OUT / "rotation_area_chinacp_ccd2020_common_extent.csv", index=False)
    dump(OUT / "rotation_area_chinacp_ccd2020_comparison.json", dict(created_utc=utc(), source=source_meta,
         common_envelope=[112, 32, 120, 41], operational_base_domain=str(OUT / "operational_plain_e200_s2_0p01deg.tif"),
         table=str(OUT / "rotation_area_chinacp_ccd2020_common_extent.csv"),
         physical_areas_ha={key: float(merged[key].sum()) for key in area_fields},
         areas_operator="CCD native pixel area from WGS84 geodesic row-cell polygons; ChinaCP native UTM cell area corrected by local areal scale; each native cell centre assigns operational domain/weather grid",
         limitations=["Common envelope extends only to120E because existing CCD merge ends there; cannot represent the complete121.5E extraction envelope", "CCD province sources omit Beijing standalone and use derived255 for multiple unconfirmed background states", "Source agreement is not independent field validation", "Different native resolutions and mixed-pixel semantics affect totals", "Only2020 compared; annualCCD2021--2024 remain preserved archives"] ))
    print(f"CCD2020 common-domain rotation area {rotation.sum() / 1e6:.3f} million ha", flush=True)


def weather_quality():
    drivers = pd.read_csv(OUT / "regional_drivers_agera5_2020.csv")
    rows = drivers.agera5_lat_index.to_numpy()
    cols = drivers.agera5_lon_index.to_numpy()
    weights = drivers.mapped_rotation_area_ha.to_numpy()
    catalog = pd.read_csv(ROOT / "data/agera5/catalog/files.csv")
    annual, flagged, sources = [], [], []
    variables = ["tmin", "tmax", "tmean", "precip", "et0"]
    for year in range(1996, 2026):
        path = ROOT / f"data/agera5/derived/netcdf/AgERA5_v2_NCP_daily_{year}.nc"
        digest = sha256(path)
        catalog_entry = catalog[catalog.path.str.endswith(path.name)]
        if len(catalog_entry) != 1 or catalog_entry.iloc[0].sha256 != digest:
            raise ValueError(f"Weather source hash does not match existing archive catalog: {year}")
        with xr.open_dataset(path) as source:
            if not (np.allclose(source.lat.values[rows], drivers.latitude) and
                    np.allclose(source.lon.values[cols], drivers.longitude)):
                raise ValueError("Weather indices do not match source coordinate centres")
            dates = pd.to_datetime(source.time.values)
            expected = pd.date_range(f"{year}-01-01", f"{year}-12-31")
            if not dates.equals(expected):
                raise ValueError(f"Incomplete or unordered daily weather dates: {year}")
            values = {key: source[key].values[:, rows, cols] for key in variables}
            attrs = {key: dict(source[key].attrs) for key in variables}
        masks = {"missing_native_required": ~np.isfinite(np.stack([values[v] for v in ["tmin", "tmax", "precip", "et0"]])).all(axis=0),
                 "tmin_above_tmax": values["tmin"] > values["tmax"],
                 "tmean_below_tmin": values["tmean"] < values["tmin"],
                 "tmean_above_tmax": values["tmean"] > values["tmax"],
                 "negative_precipitation": values["precip"] < 0,
                 "negative_et0": values["et0"] < 0}
        for name, flag in masks.items():
            annual.append(dict(year=year, quality_flag=name, flagged_zone_days=int(flag.sum()),
                flagged_zones=int(flag.any(axis=0).sum()), total_zone_days=int(flag.size),
                flagged_area_day_fraction=float(np.sum(flag * weights[None, :]) / (weights.sum() * len(dates))),
                native_required=name not in {"tmean_below_tmin", "tmean_above_tmax"}, source_path=str(path)))
        any_flag = np.logical_or.reduce(list(masks.values()))
        day, zone = np.nonzero(any_flag)
        frame = pd.DataFrame(dict(date=dates[day], zone_id=drivers.zone_id.to_numpy()[zone],
                                 latitude=drivers.latitude.to_numpy()[zone], longitude=drivers.longitude.to_numpy()[zone]))
        for key in variables:
            frame[key] = values[key][day, zone]
        for key, flag in masks.items():
            frame[key] = flag[day, zone]
        flagged.append(frame)
        sources.append(dict(path=str(path), year=year, sha256=digest, bytes=path.stat().st_size,
                            days=len(dates), variable_attributes=attrs, catalog_sha_verified=True))
        print(f"Weather quality {year}: {len(frame)} source-flagged zone days", flush=True)
    pd.DataFrame(annual).to_csv(OUT / "weather_quality_active_rotation_1996_2025.csv", index=False)
    pd.concat(flagged, ignore_index=True).to_csv(OUT / "weather_quality_flagged_days_1996_2025.csv.gz", index=False)
    dump(OUT / "weather_source_provenance_1996_2025.json", dict(created_utc=utc(),
        unit=f"{len(drivers)} nonzero ChinaCP2020 whole-class-area AgERA5 weather cells; fixed footprint", sources=sources,
        quality_table=str(OUT / "weather_quality_active_rotation_1996_2025.csv"),
        flagged_source_values=str(OUT / "weather_quality_flagged_days_1996_2025.csv.gz"),
        source_unchanged=True, quality_policy="Only detection; no correction, imputation or unit changes",
        interpretation="tmean is a source diagnostic; native provided-et0 driver consumes tmin,tmax,precip,et0. Invalid native-required inputs need an explicit scenario exclusion or documented derived repair."))


def domain_review():
    with rasterio.open(OUT / "operational_plain_e200_s2_0p01deg.tif") as source:
        base = source.read(1) == 1
    with rasterio.open(OUT / "selected_basin_coverage_0p01deg.tif") as source:
        basin = source.read(1) == 1
    with rasterio.open(RAW / "srtm_mean_0p01deg.tif") as source:
        terrain = source.read(masked=True).filled(np.nan)
    eligible = basin & np.isfinite(terrain).all(axis=0) & (terrain[0] <= 200) & (terrain[1] <= 2)
    labels, count = ndimage.label(eligible, structure=np.ones((3, 3)))
    province_grid, province_names, province_source = cartographic_province_grid()
    cell_area = np.broadcast_to(row_areas()[:, None] / 1e6, labels.shape)
    land_area = np.bincount(labels.ravel(), weights=cell_area.ravel(), minlength=count+1)
    selected_labels = set(np.unique(labels[base]).tolist())
    pixel_count = np.bincount(labels.ravel(), minlength=count+1)
    crop_file = ROOT / "data/spatial/extracted/chinacp_500m_2015_2021/ChinaCP/ChinaCP_2020.tif"
    cpm_file = ROOT / "data/spatial/extracted/chinacp_500m_2015_2021/CPM/CPM.tif"
    with rasterio.open(crop_file) as source:
        window = from_bounds(*transform_bounds("EPSG:4326", source.crs, *ENVELOPE, densify_pts=21),
                             transform=source.transform).round_offsets().round_lengths()
        crop_r, crop_c = np.nonzero(source.read(1, window=window) == 246)
        px, py = rasterio.transform.xy(source.window_transform(window), crop_r, crop_c)
        px, py = np.asarray(px), np.asarray(py)
        lon, lat = Transformer.from_crs(source.crs, "EPSG:4326", always_xy=True).transform(px, py)
        lon, lat = np.asarray(lon), np.asarray(lat)
        rr, cc = cell_index(lon, lat)
        valid = (rr >= 0) & (rr < HEIGHT) & (cc >= 0) & (cc < WIDTH)
        rr, cc, lon, lat, px, py = [a[valid] for a in [rr, cc, lon, lat, px, py]]
        physical_ha = abs(source.transform.a * source.transform.e) / np.asarray(Proj(source.crs).get_factors(lon, lat).areal_scale) / 10000
        fraction = sample_projected_grid(cpm_file, px, py, source.crs) / 100
    component_crop = np.bincount(labels[rr, cc], weights=physical_ha, minlength=count+1)
    component_cpm = np.bincount(labels[rr, cc], weights=physical_ha * fraction, minlength=count+1)
    slices = ndimage.find_objects(labels)
    component_rows = []
    for label, slices_pair in enumerate(slices, start=1):
        if slices_pair is None:
            continue
        r, c = slices_pair
        flag = labels[r, c] == label
        ids, sizes = np.unique(province_grid[r, c][flag], return_counts=True)
        province = int(ids[np.argmax(sizes)])
        component_rows.append(dict(component_id=label, retained=label in selected_labels,
            eligible_lowland_area_km2=float(land_area[label]), pixels=int(pixel_count[label]),
            lon_min=112+c.start*DX, lat_min=41-r.stop*DX, lon_max=112+c.stop*DX, lat_max=41-r.start*DX,
            dominant_cartographic_province=province_names.get(province, "Unassigned context"),
            chinacp2020_whole_cell_rotation_area_ha=float(component_crop[label]),
            chinacp2020_cpm_scaled_rotation_area_ha=float(component_cpm[label]),
            retention_reason="Connected to all three retained lowland seed neighbourhoods" if label in selected_labels else "Disconnected at0.01degree and8-neighbour support from the retained seed component; operational criterion, no field validation"))
    table = pd.DataFrame(component_rows).sort_values("eligible_lowland_area_km2", ascending=False)
    table.to_csv(OUT / "domain_connected_component_review.csv", index=False)
    categories = {"outside_selected_drainage": ~basin[rr, cc],
        "selected_drainage_missing_terrain": basin[rr, cc] & ~np.isfinite(terrain[:, rr, cc]).all(axis=0),
        "elevation_above200m": basin[rr, cc] & np.isfinite(terrain[:, rr, cc]).all(axis=0) & (terrain[0, rr, cc] > 200),
        "slope_above2deg_within200m": basin[rr, cc] & np.isfinite(terrain[:, rr, cc]).all(axis=0) & (terrain[0, rr, cc] <= 200) & (terrain[1, rr, cc] > 2),
        "eligible_lowland_disconnected": eligible[rr, cc] & ~base[rr, cc], "retained_operational_base": base[rr, cc]}
    category_rows = [dict(category=name, chinacp2020_whole_cell_rotation_area_ha=float(physical_ha[flag].sum()),
                     chinacp2020_cpm_scaled_rotation_area_ha=float((physical_ha * fraction)[flag].sum()),
                     native_rotation_cells=int(flag.sum())) for name, flag in categories.items()]
    pd.DataFrame(category_rows).to_csv(OUT / "domain_rotation_area_exclusion_categories_2020.csv", index=False)
    checkpoints = {"Beijing_southern_plain": [116.4,39.8], "Tianjin_plain": [117.3,39.1],
        "Anhui_Fuyang_plain": [115.8,32.9], "Jiangsu_Suqian_plain": [118.3,33.95]}
    checks = []
    for name, (x,y) in checkpoints.items():
        row, col = cell_index(x,y)
        checks.append(dict(checkpoint=name, lon=x, lat=y, included=bool(base[row,col]),
            selected_drainage=bool(basin[row,col]), elevation_m=float(terrain[0,row,col]),
            support_slope_deg=float(terrain[1,row,col]),
            meaning="Approximate geographic coverage checkpoint, not a field station or validation observation"))
    for code in np.unique(province_grid[base]):
        flag = base & (province_grid == code)
        cr, _ = np.nonzero(flag)
        checks.append(dict(province_context=province_names.get(int(code),"Unassigned context"),
            included_land_area_km2=float(cell_area[flag].sum()), pixel_lat_min=float(41-(cr.max()+.5)*DX),
            pixel_lat_max=float(41-(cr.min()+.5)*DX),
            meaning="Cartographic province-centre assignment for coverage only; not a domain constraint"))
    r, c = np.nonzero(base)
    excluded = table[~table.retained]
    dump(OUT / "domain_geographic_checks.json", dict(created_utc=utc(), official_ncp_boundary=False,
         retained_component_ids=sorted(selected_labels), eligible_components=count,
         eligible_lowland_area_km2=float((eligible*cell_area).sum()),
         retained_lowland_area_km2=float((base*cell_area).sum()),
         excluded_eligible_lowland_area_km2=float(((eligible & ~base)*cell_area).sum()),
         disconnected_rotation_area_ha=float(component_crop[[i for i in range(1,count+1) if i not in selected_labels]].sum()),
         disconnected_rotation_cpm_scaled_area_ha=float(component_cpm[[i for i in range(1,count+1) if i not in selected_labels]].sum()),
         largest_disconnected_components=excluded.head(20).to_dict("records"), checkpoints=checks,
         domain_bounds=[112+c.min()*DX,41-(r.max()+1)*DX,112+(c.max()+1)*DX,41-r.min()*DX],
         touches_envelope=dict(west=bool(base[:,0].any()),east=bool(base[:,-1].any()),south=bool(base[-1].any()),north=bool(base[0].any())),
         cartographic_province_source=dict(path=str(province_source),sha256=sha256(province_source)),
         interpretation="Disconnected lowlands are explicitly inventoried; no assumption that all are outside an official NCP boundary. Differences need geographic interpretation and boundary sensitivity before a certified whole-NCP claim."))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=["all", "download", "prepare", "compare", "quality", "review"], default="all")
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    if args.stage in {"all", "download"}:
        ee.Initialize(project="ee-gangzhaomodel")
        fetch_basins()
        errors = []
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(download_property, name): name for name in ["srtm", *PROPERTIES]}
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as error:
                    errors.append(dict(source=futures[future], error=str(error)[:500], time_utc=utc()))
                    print(f"Source failed: {futures[future]} {str(error)[:180]}", flush=True)
        dump(OUT / "download_status.json", dict(checked_utc=utc(), errors=errors,
             successful=[str(p) for p in RAW.glob("*.tif")], source_metadata=[str(p) for p in RAW.glob("*.json")]))
        if errors:
            raise RuntimeError("Some regional source exports failed; preserved successful source snapshots")
    if args.stage in {"all", "prepare"}:
        domains = construct_domains()
        crop_and_soil(domains)
    if args.stage in {"all", "prepare", "compare"}:
        compare_ccd()
    if args.stage in {"all", "quality"}:
        weather_quality()
    if args.stage in {"all", "prepare", "review"}:
        domain_review()
    outputs = [dict(path=str(p), bytes=p.stat().st_size, sha256=sha256(p))
               for p in OUT.rglob("*") if p.is_file() and p.name != "file_manifest.csv"]
    pd.DataFrame(outputs).to_csv(OUT / "file_manifest.csv", index=False)
    print(f"Completed regional driver stage {args.stage}: {OUT}", flush=True)


if __name__ == "__main__":
    main()
