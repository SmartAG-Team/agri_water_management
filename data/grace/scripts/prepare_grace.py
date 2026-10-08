"""Preserve native monthly solutions, extract regional cubes and audit coverage.

No temporal interpolation, groundwater inversion, gain scaling, density
rescaling, or blending of observations with reconstructions is performed.
"""
from pathlib import Path
import argparse
import hashlib
import json
import re
import netCDF4
import numpy as np
import pandas as pd
import xarray as xr
import rasterio
from rasterio.features import shapes
from shapely.geometry import shape, mapping, box
from shapely.geometry.polygon import orient
from shapely.ops import unary_union, transform
from pyproj import Geod, Transformer

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT.parent
GEOD=Geod(ellps='WGS84')
PERIOD=pd.period_range('1996-10','2026-09',freq='M')
CHINA=(73,18,135,54)
CONTEXT=(109,28,124,44)


def jsonable(value):
    if isinstance(value,np.ndarray):return value.tolist()
    if isinstance(value,np.generic):return value.item()
    return str(value)


def area(geometry):
    if geometry.is_empty:return 0.
    if geometry.geom_type=='Polygon':
        return abs(GEOD.geometry_area_perimeter(orient(geometry,sign=1))[0])
    if hasattr(geometry,'geoms'):return sum(area(g) for g in geometry.geoms)
    return 0.


def regions():
    province_file=DATA/'spatial/derived/ccd_full_provinces_2024/province_display_boundaries.geojson'
    union=unary_union([shape(f['geometry']) for f in json.loads(province_file.read_text())['features']])
    mask_file=next((DATA/'extracted/external/groundwater_zenodo_17799537').rglob('mask/ncp.tif'))
    with rasterio.open(mask_file) as src:
        a=src.read(1)
        geoms=[shape(g) for g,value in shapes(a,mask=a==1,transform=src.transform) if value==1]
        to_lonlat=Transformer.from_crs(src.crs,'EPSG:4326',always_xy=True).transform
        study=transform(to_lonlat,unary_union(geoms))
    result={
        'NCPbox':{'geometry':box(112,32,120,41),
            'definition':'112–120 E, 32–41 N; rectangular extraction domain, not an official NCP boundary',
            'source':'Existing project NCPbox definition'},
        'six_provinces':{'geometry':union,
            'definition':'Complete Hebei, Henan, Shandong, Tianjin, Anhui and Jiangsu union; not the NCP proper',
            'source':'Natural Earth Admin 1 v5.1.1 at 1:10,000,000 cartographic scale'},
        'NCP_2025_study':{'geometry':study,
            'definition':'Published groundwater study mask; exploratory GRACE average for a small domain',
            'source':'https://doi.org/10.5281/zenodo.17799537; mask/ncp.tif',
            'source_path':str(mask_file.relative_to(DATA.parent)),
            'source_sha256':hashlib.sha256(mask_file.read_bytes()).hexdigest()},
    }
    for name,r in result.items():
        r['area_km2']=area(r['geometry'])/1e6
        r['below_csr_200000_km2_guidance']=r['area_km2']<200000
    (ROOT/'derived').mkdir(parents=True,exist_ok=True)
    features=[{'type':'Feature','geometry':mapping(r['geometry']),
        'properties':{'region':name,**{k:v for k,v in r.items() if k!='geometry'}}} for name,r in result.items()]
    (ROOT/'derived/region_definitions.geojson').write_text(json.dumps(
        {'type':'FeatureCollection','features':features},indent=2))
    return result


def nominal_months(ds,product):
    t=ds.time
    units=t.attrs.get('units',t.attrs.get('Units'))
    dates=netCDF4.num2date(t.values,units,calendar=t.attrs.get('calendar','standard'),
        only_use_cftime_datetimes=False)
    nominal=[str(d)[:7] for d in dates]
    adjustments=[]
    # Documented special months: gravity-toolkit time.adjust_months notes.
    fixes={
        'CSR_RL0603':{'2011-10-31':'2011-11','2015-04-27':'2015-05'},
        'GSFC_RL06v20':{'2018-11-01':'2018-10'},
    }.get(product,{})
    for i,d in enumerate(dates):
        key=str(d)[:10]
        if key in fixes:
            adjustments.append({'source_index':i,'source_center_time':str(d),
                'center_calendar_month':nominal[i],'nominal_solution_month':fixes[key]})
            nominal[i]=fixes[key]
    assert len(nominal)==len(set(nominal)),(product,'Unresolved duplicate solution month')
    assert nominal==sorted(nominal),(product,'Nonmonotonic nominal months')
    bounds=[]
    if 'time_bounds' in ds:
        bound_dates=netCDF4.num2date(ds.time_bounds.values,units,
            calendar=t.attrs.get('calendar','standard'),only_use_cftime_datetimes=False)
        bounds=[[str(v) for v in row] for row in bound_dates]
    else:
        bounds=[['',''] for _ in nominal]
    audit=pd.DataFrame({'source_time_index':np.arange(len(nominal)),
        'source_center_time':[str(v) for v in dates],'nominal_month':nominal,
        'source_interval_start':[b[0] for b in bounds],'source_interval_end':[b[1] for b in bounds]})
    return nominal,adjustments,audit


def subset(ds,bounds):
    x0,y0,x1,y1=bounds
    return ds.isel(lon=np.flatnonzero((ds.lon.values>=x0)&(ds.lon.values<=x1)),
        lat=np.flatnonzero((ds.lat.values>=y0)&(ds.lat.values<=y1))).sortby('lat').sortby('lon')


def write_cube(ds,path):
    path.parent.mkdir(parents=True,exist_ok=True)
    encoding={n:{'zlib':True,'complevel':4} for n,v in ds.data_vars.items() if v.dtype.kind in 'fiu'}
    ds.to_netcdf(path,engine='netcdf4',encoding=encoding)


def spatial_weights(lats,lons,geometry,land):
    dx=float(np.median(np.diff(lons)));dy=float(np.median(np.diff(lats)))
    weights=np.zeros((len(lats),len(lons)),dtype='float64')
    for iy,y in enumerate(lats):
        for ix,x in enumerate(lons):
            if land[iy,ix]:
                cell=box(x-dx/2,y-dy/2,x+dx/2,y+dy/2)
                if geometry.intersects(cell):weights[iy,ix]=area(geometry.intersection(cell))
    assert weights.sum()>0
    return weights


def export_gsfc_native(reference,months):
    path=ROOT/'raw/gsfc_rl06v20/gsfc.glb_.200204_202603_rl06v2.0_obp-ice6gd.h5'
    with netCDF4.Dataset(path) as source:
        assert source['solution'].Version=='RL06 v2.0'
        # HDF5 uses Jan 0, 2002; the gridded NetCDF uses Jan 1, 2002.
        assert np.allclose(source['time']['ref_days_middle'][:].ravel()-1,reference.time.values)
        g=source['mascon'];lat=g['lat_center'][:].ravel();lon=g['lon_center'][:].ravel()
        dx=g['lon_span'][:].ravel();dy=g['lat_span'][:].ravel()
        i=np.flatnonzero((lon+dx/2>=CONTEXT[0])&(lon-dx/2<=CONTEXT[2])&
            (lat+dy/2>=CONTEXT[1])&(lat-dy/2<=CONTEXT[3]))
        ids=g['labels'][:].ravel()[i].astype('int32')
        assert len(np.unique(ids))==len(ids)
        ds=xr.Dataset(coords={'mascon':ids,'time':reference.time})
        ds['nominal_month']=('time',np.asarray(months,dtype=str))
        for name in ['lat_center','lon_center','lat_span','lon_span','area_km2','area_deg','location','basin','elev_flag']:
            ds[name]=('mascon',g[name][:].ravel()[i])
            ds[name].attrs={k.lower() if k=='Units' else k:v for k,v in g[name].__dict__.items()}
        ds['lwe_thickness']=(('mascon','time'),source['solution']['cmwe'][i,:])
        ds.lwe_thickness.attrs={'units':'cm','long_name':'Native mascon equivalent water height anomaly'}
        for name in ['leakage_2sigma','leakage_trend','noise_2sigma']:
            values=source['uncertainty'][name][i,:]
            ds[name]=(('mascon','time'),values) if name=='noise_2sigma' else ('mascon',values[:,0])
            ds[name].attrs={k.lower() if k=='Units' else k:v for k,v in source['uncertainty'][name].__dict__.items()}
        ds.attrs={'source_file':str(path.relative_to(ROOT)),'product_version':source['solution'].Version,
            'subset':'Native mascon footprints intersecting 109–124 E, 28–44 N',
            'source_uncertainty_metadata':json.dumps(source['uncertainty'].__dict__),
            'note':'Native values and uncertainty components retained. No combined regional uncertainty is calculated.'}
        write_cube(ds,ROOT/'derived/ncp_context/GSFC_RL06v20_native_mascons_with_uncertainty.nc')
        return {'native_hdf_version':source['solution'].Version,'regional_native_mascons':len(ids),
            'hdf_netcdf_time_axis_matched':True,'hdf_time_epoch_offset_days':1}


def process(path,product,kind,region_defs):
    print('Processing',product,flush=True)
    with xr.open_dataset(path,decode_times=False,engine='netcdf4') as source:
        raw_attrs=dict(source.attrs)
        ds=source.copy(deep=False)
        for n in ds.variables:
            if 'Units' in ds[n].attrs:
                ds[n].attrs['units']=ds[n].attrs.pop('Units')
        months,adjustments,time_audit=nominal_months(ds,product)
        time_audit.to_csv(ROOT/'metadata'/f'{product}_time_axis.csv',index=False)
        variable='TWSA' if kind=='reconstruction' else 'lwe_thickness'
        assert ds[variable].attrs['units'].startswith('cm')
        ds['nominal_month']=('time',np.asarray(months,dtype=str))
        ds.nominal_month.attrs={'long_name':'Nominal GRACE solution month or reconstruction calendar month',
            'comment':'Source time and averaging bounds are retained; documented special months are mapped separately.'}
        china=subset(ds,CHINA).load()
        china.attrs.update({'subset_scope':'China context bounding box, 73–135 E and 18–54 N; not a national boundary mask',
            'source_file':str(path.relative_to(ROOT)),'local_product_id':product,'data_kind':kind,
            'processing_note':'Native values/units unchanged. No interpolation, smoothing, gain factors or density conversion.'})
        write_cube(china,ROOT/'derived/china_context'/f'{product}_China_bbox.nc')
        context=subset(china,CONTEXT)
        context.attrs=dict(china.attrs)
        context.attrs['subset_scope']='NCP and six-province context bounding box, 109–124 E and 28–44 N; not the NCP boundary'
        write_cube(context,ROOT/'derived/ncp_context'/f'{product}_NCP_context.nc')
        a=context[variable].transpose('time','lat','lon').values.astype('float64')
        finite=np.isfinite(a)
        if product=='CSR_RL0603':
            mask_file=ROOT/'raw/csr_rl0603/CSR_GRACE_GRACE-FO_RL06_Mascons_v02_LandMask.nc'
            with xr.open_dataset(mask_file) as lm:
                land=lm.LO_val.sel(lat=context.lat,lon=context.lon).values==1
        elif 'land_mask' in context:
            land=context.land_mask.values==1
        else:
            land=finite.any(axis=0)
        all_series=[];spatial=[];weight_vars={}
        for name,r in region_defs.items():
            weights=spatial_weights(context.lat.values,context.lon.values,r['geometry'],land)
            valid_area=np.sum(finite*weights[None,:,:],axis=(1,2))
            totals=np.nansum(a*weights[None,:,:],axis=(1,2))
            assert (valid_area>0).all()
            values=10*totals/valid_area
            valid_fraction=np.clip(valid_area/weights.sum(),0,1)
            weight_vars[name]=(('lat','lon'),weights)
            all_series.append(pd.DataFrame({'product':product,'data_kind':kind,'region':name,
                'month':months,'twsa_mm_as_released':values,
                'valid_area_fraction':valid_fraction,
                'represented_land_area_km2':weights.sum()/1e6}))
            spatial.append({'region':name,'selected_cells':int((weights>0).sum()),
                'weighted_land_area_km2':weights.sum()/1e6,'region_area_km2':r['area_km2'],
                'below_csr_200000_km2_guidance':r['below_csr_200000_km2_guidance'],
                'minimum_valid_area_fraction':float(valid_fraction.min())})
        weights_ds=xr.Dataset(weight_vars,coords={'lat':context.lat,'lon':context.lon},
            attrs={'units':'m2','method':'Geodesic area of region/cell intersections on WGS84; source land mask applied',
                'note':'Area weights describe grid sampling; neighbouring GRACE grid cells are not independent measurements.'})
        write_cube(weights_ds,ROOT/'derived/weights'/f'{product}_regional_weights.nc')
        pd.concat(all_series,ignore_index=True).to_csv(ROOT/'series'/f'{product}_regional_monthly.csv',index=False)
        expected=pd.period_range(months[0],months[-1],freq='M')
        gaps=[str(p) for p in expected if str(p) not in months]
        warnings=[];native_check={}
        if product=='CSR_RL0603':
            warnings+=['Source declares water density 1025 kg/m3. Only cm to mm conversion is applied; no freshwater-density rescaling.',
                'Source months_missing attribute contains a duplicate 2017-02. Coverage derives from solution times and documented special-month mapping.']
        if product=='GSFC_RL06v20':
            native_check=export_gsfc_native(ds,months)
            warnings+=['Official page, filename and native HDF5 solution group identify RL06 v2.0; gridded NetCDF retains legacy v1.0 labels and date_created=date_stamp. Raw attributes are preserved; HDF5 and NetCDF time axes match.']
        if kind=='reconstruction':
            warnings+=['Model estimates trained on JPL GRACE; not independent satellite observations.',
                'Published evaluation identifies poor reconstruction of anthropogenic depletion in the North China Plain; unsuitable as direct pumping-trend validation.',
                'Paper describes the original 1984–2021 release; downloaded v2 repository extends through 2023.']
        report={'product':product,'kind':kind,'raw_path':str(path.relative_to(ROOT)),
            'solution_count':len(months),'first_month':months[0],'last_month':months[-1],
            'missing_months_within_span':gaps,'time_adjustments':adjustments,
            'source_time_grain':'Monthly gravity solutions with varying observation windows' if kind=='satellite_solution' else 'Calendar-month model estimates',
            'grid_spacing_degrees':float(np.median(np.diff(context.lon.values))),
            'native_variable':variable,'native_units':context[variable].attrs['units'],
            'csv_conversion':'Native cm multiplied by 10; product-specific density conventions retained',
            'native_global_attributes':raw_attrs,'regional_profiles':spatial,'warnings':warnings}
        report.update(native_check)
        (ROOT/'metadata'/f'{product}_quality.json').write_text(json.dumps(report,indent=2,default=jsonable))
        print('Saved',product,len(months),'solutions;',len(gaps),'missing nominal months',flush=True)


def catalog(region_defs):
    profiles=[json.loads(p.read_text()) for p in sorted((ROOT/'metadata').glob('*_quality.json'))]
    sources=[json.loads(p.read_text()) for p in sorted((ROOT/'metadata/downloads').glob('*.json'))]
    pd.DataFrame(sources).to_csv(ROOT/'catalog/files.csv',index=False)
    overview=pd.DataFrame([{k:r[k] for k in ['product','kind','raw_path','solution_count','first_month',
        'last_month','grid_spacing_degrees','native_units']} for r in profiles])
    overview.to_csv(ROOT/'catalog/products.csv',index=False)
    if not profiles:return
    series=pd.concat([pd.read_csv(ROOT/'series'/f"{r['product']}_regional_monthly.csv") for r in profiles],ignore_index=True)
    series.to_csv(ROOT/'series/all_products_all_available_months.csv',index=False)
    expanded=[];coverage=[]
    for r in profiles:
        observed=set(series.loc[series['product']==r['product'],'month'])
        states={}
        for m in PERIOD:
            month=str(m)
            if month in observed:
                state='satellite_solution' if r['kind']=='satellite_solution' else 'model_reconstruction'
            elif r['kind']=='reconstruction':state='outside_reconstruction_period'
            elif month<r['first_month']:state='before_satellite_record'
            elif month>r['last_month']:state='not_yet_available_in_product'
            elif '2017-07'<=month<='2018-05':state='inter_mission_gap'
            else:state='missing_monthly_solution'
            states[month]=state
            coverage.append({'product':r['product'],'month':month,'status':state})
        for region in region_defs:
            s=series.loc[(series['product']==r['product'])&(series.region==region)].set_index('month')
            s=s.reindex(PERIOD.astype(str));s.index.name='month'
            s['product']=r['product'];s['data_kind']=r['kind'];s['region']=region
            s['availability_status']=[states[m] for m in s.index]
            expanded.append(s.reset_index())
    full=pd.concat(expanded,ignore_index=True)
    full.to_csv(ROOT/'series/monthly_1996-10_2026-09_by_product_region.csv',index=False)
    coverage=pd.DataFrame(coverage)
    coverage.to_csv(ROOT/'catalog/monthly_coverage_30years.csv',index=False)
    with pd.ExcelWriter(ROOT/'GRACE_monthly_NCP.xlsx',engine='openpyxl') as w:
        pd.DataFrame([
            ('Quantity','Terrestrial water storage anomaly (TWSA); not groundwater level or groundwater storage alone.'),
            ('Storage components','TWSA = GWSA + SMA + SWEA + SWA + OtherA. All terms are anomalies on consistent space, time, units and reference period.'),
            ('Groundwater residual','GWSA ≈ TWSA − SMA − SWEA − SWA only when remaining storage is accounted for or negligible. No GWSA is produced in this collection.'),
            ('Table units','mm of equivalent water thickness as released; native cm multiplied by 10.'),
            ('Requested window','1996-10 through 2026-09: 360 calendar months; satellite solutions begin in 2002-04.'),
            ('Missing values','Blank cells are unavailable values. No temporal interpolation or observation/reconstruction splice.'),
            ('Reconstruction','GRAiCE v2 is a model reconstruction trained on JPL GRACE, not independent observations; poor capture of NCP pumping trends is documented.'),
            ('Density conventions','CSR file states 1025 kg/m3; GSFC states 1000 kg/m3. Values retain native conventions; no density adjustment or inter-product mean is applied.'),
            ('Spatial scale','Grid spacing is not effective GRACE resolution. Regional averages use fractional cell areas and each product land support.'),
            ('Small study mask','NCP_2025_study is about 132,000 km2, below CSR guidance of about 200,000 km2; exploratory only.'),
            ('Anomaly reference','CSR and GSFC use the 2004–2009 mean. GRAiCE is trained on JPL anomalies referenced to 2004–2009; no additional rebaselining.'),
            ('Version metadata','GSFC official page, file name and native HDF5 confirm RL06v2.0; gridded NetCDF retains legacy v1.0 metadata. Originals are preserved.'),
        ],columns=['item','definition']).to_excel(w,sheet_name='README',index=False)
        overview.to_excel(w,sheet_name='products',index=False)
        for region in region_defs:
            part=full.loc[full.region==region]
            table=part.pivot(index='month',columns='product',values='twsa_mm_as_released')
            table.to_excel(w,sheet_name=region+'_TWSA_mm')
        coverage.to_excel(w,sheet_name='monthly_coverage',index=False)
        pd.DataFrame([{'region':n,**{k:v for k,v in r.items() if k!='geometry'}} for n,r in region_defs.items()]).to_excel(w,sheet_name='regions',index=False)
        pd.DataFrame([{'product':r['product'],'note':note} for r in profiles for note in r['warnings']]).to_excel(w,sheet_name='quality_notes',index=False)
        pd.DataFrame(sources).to_excel(w,sheet_name='source_files',index=False)
        for ws in w.book.worksheets:
            ws.freeze_panes='B2';ws.auto_filter.ref=ws.dimensions
            for cells in ws.columns:
                ws.column_dimensions[cells[0].column_letter].width=min(55,max(18,len(str(cells[0].value))+3))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--product',choices=['csr','gsfc','graice','all'],default='all')
    parser.add_argument('--refresh',action='store_true');args=parser.parse_args()
    for folder in ['metadata','series','catalog','derived']:(ROOT/folder).mkdir(parents=True,exist_ok=True)
    definitions=regions()
    tasks=[]
    if args.product in ['csr','all']:
        tasks.append((ROOT/'raw/csr_rl0603/CSR_GRACE_GRACE-FO_RL0603_Mascons_all-corrections.nc','CSR_RL0603','satellite_solution'))
    if args.product in ['gsfc','all']:
        tasks.append((ROOT/'raw/gsfc_rl06v20/gsfc.glb_.200204_202603_rl06v2.0_obp-ice6gd_halfdegree.nc','GSFC_RL06v20','satellite_solution'))
    if args.product in ['graice','all']:
        tasks.extend((p,p.stem+'_v2','reconstruction') for p in sorted((ROOT/'raw/graice_v2').glob('*.nc')))
    for path,product,kind in tasks:
        if path.exists() and (args.refresh or not (ROOT/'metadata'/f'{product}_quality.json').exists()):
            process(path,product,kind,definitions)
    catalog(definitions)


if __name__=='__main__':main()
