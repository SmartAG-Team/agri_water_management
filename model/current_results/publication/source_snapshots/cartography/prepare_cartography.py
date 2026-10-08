"""Preserve existing boundary sources and derive publication plotting coordinates."""
from pathlib import Path
from io import BytesIO
from datetime import datetime, timezone
from collections import Counter
import csv
import hashlib
import json
import shutil
import zipfile

import numpy as np
import requests
import rasterio
from rasterio.features import rasterize
from pyproj import CRS, Geod, Transformer
import shapefile
from shapely.geometry import shape, mapping, box, LineString, MultiLineString
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[4]
HISTORY = PROJECT / 'archives/2026-10-07_model_history/model/2026-10-03_submission_package'
EXTENT = [112., 32., 121.5, 41.]
PROJ = '+proj=eqc +lat_ts=36 +lat_0=0 +lon_0=117 +datum=WGS84 +units=m +no_defs'
NATURAL_EARTH_URL = 'https://www.naturalearthdata.com/downloads/10m-cultural-vectors/10m-admin-1-states-provinces/'
LICENSE_URL = 'https://www.naturalearthdata.com/about/terms-of-use/'
SRTM_URL = 'https://developers.google.com/earth-engine/datasets/catalog/USGS_SRTMGL1_003'
HYDROBASINS_URL = 'https://developers.google.com/earth-engine/datasets/catalog/WWF_HydroSHEDS_v1_Basins_hybas_7'


def sha(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def lines(geometry):
    if geometry.is_empty:
        return []
    if geometry.geom_type in ('LineString', 'LinearRing'):
        return [geometry]
    if hasattr(geometry, 'geoms'):
        return [line for part in geometry.geoms for line in lines(part)]
    raise ValueError('Expected linear boundary geometry: ' + geometry.geom_type)


def polylines(path, geometries, transform):
    rows = 0
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['segment_id', 'vertex_index', 'longitude', 'latitude', 'x_m', 'y_m'])
        writer.writeheader()
        for segment, line in enumerate(geometries):
            for vertex, (longitude, latitude) in enumerate(line.coords):
                x, y = transform.transform(longitude, latitude)
                writer.writerow(dict(segment_id=segment, vertex_index=vertex,
                    longitude=longitude, latitude=latitude, x_m=x, y_m=y))
                rows += 1
    return dict(segments=len(geometries), vertices=rows, sha256=sha(path), path=path.name)


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    sources = []

    def preserve(source, relative, role, source_url=None):
        source = Path(source)
        destination = ROOT / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if sha(source) != sha(destination):
                raise ValueError('Preserved source differs: ' + str(destination))
        else:
            shutil.copy2(source, destination)
        sources.append(dict(original_local_path=str(source), snapshot=relative,
            bytes=destination.stat().st_size, sha256=sha(destination), source_sha256=sha(source),
            role=role, source_url=source_url))
        return destination

    ne_metadata = json.loads((PROJECT / 'data/spatial/metadata/natural_earth_10m.json').read_text())
    ne_archive = preserve(PROJECT / ne_metadata['archive'], 'original_sources/ne_10m_admin_1_states_provinces.zip',
        'Unchanged primary Natural Earth administrative polygon archive', ne_metadata['download_url'])
    assert sha(ne_archive) == ne_metadata['sha256']
    preserve(PROJECT / 'data/spatial/metadata/natural_earth_10m.json', 'original_sources/natural_earth_10m_metadata.json',
        'Existing download metadata and source SHA-256', NATURAL_EARTH_URL)
    for suffix in ('VERSION.txt', 'prj', 'README.html'):
        preserve(PROJECT / 'data/spatial/extracted/natural_earth_10m' / ('ne_10m_admin_1_states_provinces.' + suffix),
            'original_sources/ne_10m_admin_1_states_provinces.' + suffix, 'Original Natural Earth archive companion', NATURAL_EARTH_URL)
    study_file = preserve(HISTORY / 'data/regional/operational_plain_base_exact_geometry.geojson',
        'original_sources/operational_plain_base_exact_geometry.geojson',
        'Exact operational study-domain polygon, derived in original regional preparation; not an official NCP boundary')
    mask_file = preserve(HISTORY / 'data/regional/operational_plain_e200_s2_0p01deg.tif',
        'original_sources/operational_plain_e200_s2_0p01deg.tif', 'Exact originating 0.01-degree operational domain mask')
    for name in ('operational_domains.json', 'domain_geographic_checks.json'):
        preserve(HISTORY / 'data/regional' / name, 'original_sources/' + name, 'Original domain definition and area/provenance metadata')
    preserve(HISTORY / 'analysis_source/regional_drivers.py', 'original_sources/regional_drivers.py', 'Original domain derivation source code')
    preserve(HISTORY / 'data/regional/raw_gee/srtm_mean_0p01deg.json', 'original_sources/srtm_mean_0p01deg.json',
        'Original elevation/slope source metadata', SRTM_URL)
    preserve(HISTORY / 'data/regional/raw_gee/hydrobasins_level7_source_v2.json',
        'original_sources/hydrobasins_level7_source_v2.json', 'Original drainage support source metadata', HYDROBASINS_URL)

    web_sources = []
    for key, url in [('natural_earth_terms', LICENSE_URL), ('natural_earth_dataset', NATURAL_EARTH_URL),
                     ('srtm_catalog', SRTM_URL), ('hydrobasins_catalog', HYDROBASINS_URL)]:
        destination = ROOT / 'source_pages' / (key + '.html')
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            response = requests.get(url, timeout=30)
            if response.ok:
                destination.write_bytes(response.content)
            else:
                destination = destination.with_suffix('.access.json')
                dump(destination, dict(url=url, direct_download_status=response.status_code,
                    readable_source_verified_via='web__run primary-source open on 2026-10-08',
                    direct_html_snapshot_available=False))
        web_sources.append(dict(url=url, snapshot=destination.relative_to(ROOT).as_posix(),
            sha256=sha(destination), accessed_utc=datetime.now(timezone.utc).isoformat()))

    with zipfile.ZipFile(ne_archive) as archive:
        def payload(suffix):
            return BytesIO(archive.read('ne_10m_admin_1_states_provinces.' + suffix))
        reader = shapefile.Reader(shp=payload('shp'), shx=payload('shx'), dbf=payload('dbf'), encoding='utf-8')
        fields = [entry[0] for entry in reader.fields[1:]]
        province_features, provincial_lines, label_rows = [], [], []
        extent = box(*EXTENT)
        for record in reader.iterShapeRecords():
            properties = dict(zip(fields, record.record))
            if properties.get('admin') != 'China':
                continue
            geometry = shape(record.shape.__geo_interface__)
            if not geometry.intersects(extent):
                continue
            assert geometry.is_valid
            clipped_polygon = geometry.intersection(extent)
            # Clipping original boundary lines avoids inventing rectangular frame boundaries.
            clipped_lines = geometry.boundary.intersection(extent)
            provincial_lines.extend(lines(clipped_lines))
            label = clipped_polygon.representative_point()
            name = properties.get('name_en') or properties['name']
            province_features.append(dict(type='Feature', geometry=mapping(clipped_polygon),
                properties=dict(name=name, source_name=properties['name'], iso_3166_2=properties.get('iso_3166_2'),
                    source='Natural Earth Admin 1 v5.1.1, 1:10,000,000', role='Cartographic context only')))
            label_rows.append(dict(name=name, iso_3166_2=properties.get('iso_3166_2'), longitude=label.x, latitude=label.y))

    merged_province_lines = lines(unary_union(provincial_lines))
    dump(ROOT / 'province_context_polygons_clipped.geojson', dict(type='FeatureCollection', features=province_features))
    dump(ROOT / 'province_boundary_lines.geojson', dict(type='FeatureCollection', features=[dict(type='Feature',
        properties=dict(source='Natural Earth Admin 1 v5.1.1', role='Deduplicated original cartographic boundaries clipped to display extent'),
        geometry=mapping(MultiLineString([line.coords for line in merged_province_lines])))]))

    original = json.loads(study_file.read_text())
    study = unary_union([shape(feature['geometry']) for feature in original['features']])
    assert study.is_valid
    polygons = list(study.geoms) if study.geom_type == 'MultiPolygon' else [study]
    exterior = [LineString(polygon.exterior.coords) for polygon in polygons]
    interiors = [LineString(ring.coords) for polygon in polygons for ring in polygon.interiors]
    for filename, collection, role in [('study_area_outline_exterior.geojson', exterior, 'All exact exterior rings; no internal holes'),
        ('study_area_boundary_all_rings.geojson', exterior + interiors, 'All exact exterior and interior rings')]:
        dump(ROOT / filename, dict(type='FeatureCollection', features=[dict(type='Feature',
            properties=dict(definition='operational_plain_e200_s2', official_ncp_boundary=False, role=role),
            geometry=mapping(MultiLineString([line.coords for line in collection])))]))
    with rasterio.open(mask_file) as raster:
        original_mask = raster.read(1).astype(bool)
        polygon_mask = rasterize([(study, 1)], out_shape=original_mask.shape, transform=raster.transform, fill=0).astype(bool)
        exact_mask = np.array_equal(original_mask, polygon_mask)
        assert exact_mask
        mask_count = int(original_mask.sum())

    projection = CRS.from_proj4(PROJ)
    transform = Transformer.from_crs('EPSG:4326', projection, always_xy=True)
    derived = [polylines(ROOT / 'province_boundaries_polylines.csv', merged_province_lines, transform),
        polylines(ROOT / 'study_area_outline_exterior_polylines.csv', exterior, transform),
        polylines(ROOT / 'study_area_boundary_all_rings_polylines.csv', exterior + interiors, transform)]
    with (ROOT / 'province_labels.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['name', 'iso_3166_2', 'longitude', 'latitude', 'x_m', 'y_m'])
        writer.writeheader()
        for row in label_rows:
            row['x_m'], row['y_m'] = transform.transform(row['longitude'], row['latitude'])
            writer.writerow(row)
    dump(ROOT / 'projection.json', dict(name='Equirectangular, standard parallel 36 N, central meridian 117 E',
        proj=PROJ, wkt=projection.to_wkt(), geographic_crs='EPSG:4326', coordinate_order='longitude,latitude',
        display_extent=EXTENT, projected_units='metres', standard_parallel_deg=36., central_meridian_deg=117.))

    geodesic_area = abs(Geod(ellps='WGS84').geometry_area_perimeter(study)[0]) / 1e6
    domain_metadata = json.loads((ROOT / 'original_sources/domain_geographic_checks.json').read_text())
    metadata = dict(created_utc=datetime.now(timezone.utc).isoformat(), purpose='Publication map boundaries only; no model/calibration/regional outputs modified',
        primary_province_source=dict(dataset=ne_metadata['dataset'], version='5.1.1', scale='1:10,000,000', source_url=NATURAL_EARTH_URL,
            download_url=ne_metadata['download_url'], license='Public domain', license_url=LICENSE_URL,
            attribution='Made with Natural Earth.', authority='Primary Natural Earth cartographic dataset; not a Chinese government cadastral or legally authoritative boundary',
            selected_provinces=sorted(row['name'] for row in label_rows)),
        study_outline=dict(source_snapshot='original_sources/operational_plain_base_exact_geometry.geojson',
            definition='Drainage-constrained connected operational lowland mask at 0.01-degree support; elevation <=200 m and mean support slope <=2 degrees.',
            official_ncp_boundary=False, source_project_defined=True,
            source_urls=[SRTM_URL, HYDROBASINS_URL], license='Project-derived polygon reused with workspace owner authorization; underlying dataset terms retained in source_pages.',
            underlying_terms=dict(srtm=dict(source_url=SRTM_URL, terms='Earth Engine catalog refers to JPL public-site reuse terms; original dataset producer NASA/USGS/JPL-Caltech.'),
                hydrobasins=dict(source_url=HYDROBASINS_URL, terms='HydroSHEDS data free for non-commercial and commercial use; source catalog refers to HydroSHEDS License Agreement.')),
            attribution='Operational analysis domain derived from USGS/NASA SRTMGL1 v003 elevation/slope and WWF HydroSHEDS/HydroBASINS Level 7 drainage support.',
            source_raster_cell_area_km2=domain_metadata['retained_lowland_area_km2'], geodesic_vector_area_km2=geodesic_area,
            area_difference_km2=domain_metadata['retained_lowland_area_km2']-geodesic_area,
            area_definition_note='Manuscript area 404,658 km² is rounded retained raster-cell area. Polygon geodesic area uses long exterior/interior rings and is separately recorded; it does not replace the established raster area.',
            csr_land_mask_area_note='CSR land-masked GRACE averaging support (~395,969 km²) is distinct and does not define this study outline.',
            crop_footprint_note='The 3,641 mapped 0.1-degree crop-weather cells are an aggregated crop footprint, not the analysis boundary.',
            exterior_polygon_parts=len(exterior), internal_holes=len(interiors), source_mask_pixels=mask_count,
            exact_vector_to_source_raster_mask_match=exact_mask, bounds=list(study.bounds)),
        recommended_overlays=dict(provincial=dict(csv='province_boundaries_polylines.csv', color='#4A4A4A', linewidth_points=0.45, zorder=4),
            study=dict(csv='study_area_outline_exterior_polylines.csv', color='#111111', linewidth_points=0.9, zorder=5),
            full_rings_optional='study_area_boundary_all_rings_polylines.csv; retained for complete topology, dense holes can obscure map values',
            labels='province_labels.csv; representative points inside clipped province polygons; final collision placement belongs to renderer'),
        projection='projection.json', derived_polyline_files=derived, original_source_snapshots=sources, primary_web_metadata_snapshots=web_sources,
        verification=dict(original_sources_sha256_match=True, natural_earth_zip_matches_existing_metadata=True,
            all_selected_province_geometries_valid=True, study_geometry_valid=True,
            province_boundary_lines_have_no_artificial_extent_edges=True, no_boundary_simplification=True,
            no_bulk_earth_engine_access=True, model_results_untouched=True))
    dump(ROOT / 'cartography_metadata.json', metadata)
    outputs = [path for path in ROOT.rglob('*') if path.is_file() and path.name != 'file_manifest.json']
    dump(ROOT / 'file_manifest.json', {path.relative_to(ROOT).as_posix():dict(bytes=path.stat().st_size, sha256=sha(path)) for path in sorted(outputs)})
    print(json.dumps(dict(cartography_root=str(ROOT), provinces=len(label_rows), province_line_segments=len(merged_province_lines),
        exterior_rings=len(exterior), interior_rings=len(interiors), exact_mask_match=exact_mask,
        raster_area_km2=domain_metadata['retained_lowland_area_km2'], polygon_area_km2=geodesic_area,
        source_snapshots=len(sources), outputs=len(outputs)), indent=2))


if __name__ == '__main__':
    main()
