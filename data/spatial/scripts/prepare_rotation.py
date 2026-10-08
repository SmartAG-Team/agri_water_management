"""Prepare regional rotation layers without turning missing data into absence.

ROI is a display/extraction box (112–120 E, 32–41 N), not an NCP boundary.
Original rasters and their native georeferencing remain unchanged.
"""
from contextlib import ExitStack
from pathlib import Path
import argparse
import json
import math
from collections import Counter
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.windows import Window, from_bounds
from rasterio.warp import transform_bounds
from rasterio.vrt import WarpedVRT

ROOT = Path(__file__).resolve().parents[1]
ROI = (112.0, 32.0, 120.0, 41.0)

def write_small(path, a, transform, crs, nodata, **tags):
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, 'w', driver='GTiff', width=a.shape[1], height=a.shape[0],
            count=1, dtype=a.dtype, crs=crs, transform=transform, nodata=nodata,
            compress='deflate', tiled=True, blockxsize=256, blockysize=256) as d:
        d.write(a, 1)
        d.update_tags(**tags)

def integer_window(bounds, src):
    w = from_bounds(*bounds, transform=src.transform)
    x, y = math.floor(w.col_off), math.floor(w.row_off)
    right = min(src.width, math.ceil(w.col_off + w.width))
    bottom = min(src.height, math.ceil(w.row_off + w.height))
    x, y = max(0, x), max(0, y)
    return Window(x, y, right-x, bottom-y)

def chinacp():
    counts = None
    valid_years = None
    first = None
    out = ROOT / 'derived' / 'chinacp_500m_2015_2021'
    for p in sorted((ROOT / 'extracted/chinacp_500m_2015_2021/ChinaCP').glob('*.tif')):
        year = p.stem.split('_')[-1]
        with rasterio.open(p) as s:
            b = transform_bounds('EPSG:4326', s.crs, *ROI, densify_pts=41)
            w = integer_window(b, s)
            a = s.read(1, window=w)
            tr = s.window_transform(w)
            # Exact rectangular ROI cut in source CRS; extent corners can extend
            # beyond geographic ROI because of the UTM projection.
            valid = a != s.nodata
            mask = np.full(a.shape, 255, dtype='uint8')
            mask[valid] = (a[valid] == 246).astype('uint8')
            write_small(out / f'NCPbox_ChinaCP_classes_{year}_500m.tif', a, tr, s.crs, s.nodata,
                source_doi='10.6084/m9.figshare.14936052.v12', rotation_class=246,
                roi_wgs84=str(ROI), note='Native UTM 48N; includes projected bounding-envelope margins')
            write_small(out / f'NCPbox_wheat_maize_{year}_500m.tif', mask, tr, s.crs, 255,
                classes='1=wheat-maize;0=other valid original class;255=source NoData', source_year=year)
            spec = (a.shape, tr, s.crs)
            if first is None:
                first = spec
                counts = np.zeros(a.shape, dtype='uint8')
                valid_years = np.zeros(a.shape, dtype='uint8')
            assert spec[0] == first[0] and spec[2] == first[2] and np.allclose(
                tuple(spec[1]), tuple(first[1]), atol=1e-6, rtol=0), 'Annual native grids do not match'
            counts += (a == 246)
            valid_years += valid
            print('ChinaCP processed', year, 'rotation pixels', np.sum(a == 246), flush=True)
    counts[valid_years == 0] = 255
    write_small(out / 'NCPbox_rotation_year_count_2015_2021.tif', counts, first[1], first[2], 255,
        meaning='Number of annual maps classed 246 (0–7); use valid_year_count as denominator')
    write_small(out / 'NCPbox_valid_year_count_2015_2021.tif', valid_years, first[1], first[2], None,
        meaning='Number of annual maps with a valid original class (0–7)')

def regional_mask(kind, year=2024):
    out = ROOT / 'derived' / kind
    out.mkdir(parents=True, exist_ok=True)
    report = {'roi': ROI, 'kind': kind, 'year': year}
    with ExitStack() as stack:
        if kind == 'chinacp_wheat10m_2020':
            p = ROOT / 'extracted' / kind / 'wheat_maize_china_2020_mosaic.tif'
            s = stack.enter_context(rasterio.open(p))
            crop = integer_window(ROI, s)
            width, height = int(crop.width), int(crop.height)
            transform = s.window_transform(crop)
            readers = [s]
            step = 50  # ~0.009 degrees; full-resolution counts, no overviews
            def read(w):
                a = s.read(1, window=Window(crop.col_off+w.col_off, crop.row_off+w.row_off, w.width, w.height))
                values, n = np.unique(a, return_counts=True)
                class_counts.update(dict(zip(map(int, values), map(int, n))))
                result = np.full(a.shape, 255, dtype='uint8')
                result[a == 1] = 1
                # Only class 1 and declared NoData=16 are expected.
                assert np.all((a == 1) | (a == s.nodata)), 'Unexpected source class'
                return result
            report['source_pixel_spacing_degrees'] = s.res
            report['source_nodata'] = s.nodata
            report['nominal_product_name'] = 'ChinaCP-Wheat10m (actual file grid ~0.000179663 degrees)'
        else:
            paths = sorted((ROOT / 'raw/ccd_30m_2020_2024').glob(f'*/China-*-crops-{year}-WGS84.tif'))
            assert len(paths) == 6, f'Expected six CCD provinces, found {len(paths)}'
            resolution = 0.000269494585236
            width = math.ceil((ROI[2]-ROI[0])/resolution)
            height = math.ceil((ROI[3]-ROI[1])/resolution)
            transform = from_origin(ROI[0], ROI[3], resolution, resolution)
            readers = []
            for p in paths:
                s = stack.enter_context(rasterio.open(p))
                readers.append(stack.enter_context(WarpedVRT(s, crs='EPSG:4326', transform=transform,
                    width=width, height=height, src_nodata=0, nodata=0,
                    resampling=Resampling.nearest, warp_mem_limit=64)))
            step = 32
            conflicts = [0]
            conflict_examples = []
            def read(w):
                combined = np.zeros((int(w.height), int(w.width)), dtype='uint8')
                conflict = np.zeros(combined.shape, dtype=bool)
                for p, s in zip(paths, readers):
                    a = s.read(1, window=w).astype('uint8')
                    new_conflict = (combined > 0) & (a > 0) & (combined != a)
                    conflict |= new_conflict
                    if len(conflict_examples) < 20 and np.any(new_conflict):
                        rr, cc = np.where(new_conflict)
                        for r, c in zip(rr[:20-len(conflict_examples)], cc[:20-len(conflict_examples)]):
                            x, y = transform * (w.col_off+c+.5, w.row_off+r+.5)
                            conflict_examples.append({'lon':x, 'lat':y, 'previous_class':int(combined[r,c]),
                                'new_class':int(a[r,c]), 'new_source':p.name})
                    # Keep nonzero mapped classes; class 0 includes background
                    # beyond each province, so it must not erase adjacent data.
                    combined = np.maximum(combined, a)
                assert set(np.unique(combined)).issubset({0,1,2,3,4,5,6,9})
                vals, n = np.unique(combined, return_counts=True)
                class_counts.update(dict(zip(map(int, vals), map(int, n))))
                result = np.full(combined.shape, 255, dtype='uint8')
                result[(combined > 0) & (combined != 9)] = 0
                result[combined == 9] = 1
                # Retain disagreements as an explicit review class; never let
                # numeric class ordering decide the final rotation status.
                result[conflict] = 254
                conflicts[0] += int(np.count_nonzero(conflict))
                return result
            report['sources'] = [str(p.relative_to(ROOT)) for p in paths]
            report['resampling'] = 'nearest to a common grid; zeros treated as background; nonzero union'

        dest = out / f'NCPbox_wheat_maize_{year}_nativegrid.tif'
        dh, dw = math.ceil(height/step), math.ceil(width/step)
        density = np.zeros((dh, dw), dtype='float32')
        class_counts = Counter()
        total = Counter()
        with rasterio.Env(GDAL_CACHEMAX=256*1024*1024):
            with rasterio.open(dest, 'w', driver='GTiff', width=width, height=height,
                    count=1, dtype='uint8', crs='EPSG:4326', transform=transform,
                    nodata=255, tiled=True, blockxsize=512, blockysize=512,
                    compress='deflate', NUM_THREADS='2', BIGTIFF='IF_SAFER') as d:
                stride = step * 10
                for row in range(0, height, stride):
                    h = min(stride, height-row)
                    w = Window(0, row, width, h)
                    a = read(w)
                    d.write(a, 1, window=w)
                    v, n = np.unique(a, return_counts=True)
                    total.update(dict(zip(map(int, v), map(int, n))))
                    # Display fraction = mapped rotation pixels / ALL source-grid
                    # positions, including other uses/background; NOT a probability
                    # or a fraction conditional on cropland/valid observations.
                    binary = (a == 1).astype('uint8')
                    sums = np.add.reduceat(np.add.reduceat(binary, np.arange(0,h,step), axis=0),
                        np.arange(0,width,step), axis=1)
                    row_n = np.minimum(step, h-np.arange(0,h,step))
                    col_n = np.minimum(step, width-np.arange(0,width,step))
                    fraction = sums / (row_n[:,None]*col_n[None,:])
                    density[row//step:row//step+fraction.shape[0]] = fraction
                    if row % (stride*20) == 0:
                        print(kind, year, f'{100*row/height:.0f}%', flush=True)
                d.update_tags(source_year=year, roi_wgs84=str(ROI),
                    class_1='mapped wheat-maize rotation',
                    class_0='other nonzero crop category (CCD only)',
                    class_254='disagreement between overlapping province rasters (CCD only)',
                    class_255='source background/non-crop/NoData or outside selected source coverage',
                    warning='Regional display box, not a formal NCP boundary; a mapped class is not field truth')
                d.build_overviews([2,4,8,16,32,64], Resampling.nearest)
                d.update_tags(ns='rio_overview', resampling='nearest')
        density_transform = transform * rasterio.Affine.scale(step, step)
        write_small(out / f'NCPbox_mapped_rotation_fraction_{year}_overview.tif', density,
            density_transform, 'EPSG:4326', None,
            definition='Mapped rotation pixel count / all native-grid positions in display cell',
            warning='Zero means no mapped rotation; does not prove field absence. Edge cells may be partial.',
            aggregation_factor=step)
        report['output_shape'] = [height,width]
        report['source_or_mosaic_class_counts_in_roi'] = dict(class_counts)
        report['output_class_counts'] = dict(total)
        report['overview_factor'] = step
        if kind != 'chinacp_wheat10m_2020':
            report['conflicting_nonzero_pixels_in_overlap'] = conflicts[0]
            report['conflict_policy'] = 'Output class 254; excluded from mapped rotation fraction numerator'
            report['conflict_examples'] = conflict_examples
        (ROOT/'metadata'/f'processing_{kind}_{year}.json').write_text(json.dumps(report,indent=2))
        print('Saved',dest,flush=True)

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('kind', choices=['chinacp','wheat10m','ccd'])
    p.add_argument('--year',type=int,default=2024)
    args = p.parse_args()
    if args.kind == 'chinacp': chinacp()
    elif args.kind == 'wheat10m': regional_mask('chinacp_wheat10m_2020',2020)
    else: regional_mask('ccd_30m_2020_2024',args.year)
