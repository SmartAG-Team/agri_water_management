"""Recheck downloads and spatial transformations; create a local inventory."""
from pathlib import Path
import csv
import json
import zipfile
import numpy as np
import pandas as pd
import rasterio
from download_figshare import digest

ROOT = Path(__file__).resolve().parents[1]

def main():
    cat = ROOT / 'catalog'
    cat.mkdir(exist_ok=True)
    records = json.loads((ROOT/'metadata/figshare_downloads.json').read_text())
    records += json.loads((ROOT/'metadata/ccd_downloads.json').read_text())
    assert len(records) == 45
    for item in records:
        p = ROOT/item['path']
        assert p.stat().st_size == item['bytes'], p
        assert digest(p,'md5') == item['md5'], p
        assert digest(p,'sha256') == item['sha256'], p
        if isinstance(item['license'],dict): item['license']=item['license']['name']
    boundary=ROOT/'raw/natural_earth/ne_50m_admin_1_states_provinces.zip'
    records.append({'dataset':'natural_earth_context','path':str(boundary.relative_to(ROOT)),
        'bytes':boundary.stat().st_size,'md5':digest(boundary,'md5'),'sha256':digest(boundary,'sha256'),
        'md5_verified':False,'source_url':'https://naturalearth.s3.amazonaws.com/50m_cultural/ne_50m_admin_1_states_provinces.zip',
        'license':'Public domain; see bundled README','accessed':'2026-10-02'})
    zip_checks=[]
    for item in records:
        p=ROOT/item['path']
        if p.suffix=='.zip':
            with zipfile.ZipFile(p) as z:
                bad=z.testzip(); assert bad is None,p
                zip_checks.append({'path':str(p.relative_to(ROOT)),'crc_pass':True,'members':len(z.infolist())})
    print('Original file checks passed:',len(records),'ZIP CRC:',len(zip_checks),flush=True)
    inventories=[]
    paths=list((ROOT/'raw/ccd_30m_2020_2024').glob('*/*.tif'))+list((ROOT/'extracted').rglob('*.tif'))
    for p in sorted(paths):
        with rasterio.open(p) as s:
            a=s.read(1,out_shape=(min(300,s.height),min(300,s.width)))
            inventories.append({'path':str(p.relative_to(ROOT)),'width':s.width,'height':s.height,
                'crs':s.crs.to_string() if s.crs else None,'pixel_size_x':s.res[0],'pixel_size_y':s.res[1],
                'nodata':s.nodata,'dtype':s.dtypes[0],'bounds':list(s.bounds),
                'sampled_unique_values_only':np.unique(a).tolist(),
                'note':'Values from a read sample, not an exhaustive class census'})
    assert len(paths)==48
    # Meaningful source-to-derived point checks at destination cell centres.
    rng=np.random.default_rng(20261002)
    pixel_checks=[]
    for year in [2020,2024]:
        p=ROOT/'derived/ccd_30m_2020_2024'/f'NCPbox_wheat_maize_{year}_nativegrid.tif'
        with rasterio.open(p) as d:
            rows=rng.integers(0,d.height,150);cols=rng.integers(0,d.width,150)
            coords=[d.xy(int(r),int(c)) for r,c in zip(rows,cols)]
            actual=np.array([v[0] for v in d.sample(coords)])
        source_values=[]
        for src in sorted((ROOT/'raw/ccd_30m_2020_2024').glob(f'*/China-*-crops-{year}-WGS84.tif')):
            with rasterio.open(src) as s: source_values.append(np.array([v[0] for v in s.sample(coords)]))
        source_values=np.array(source_values)
        expected=[]
        for values in source_values.T:
            unique=set(values[values>0].tolist())
            expected.append(254 if len(unique)>1 else (1 if 9 in unique else (0 if unique else 255)))
        assert np.array_equal(actual,expected), f'CCD source samples mismatch {year}'
        pixel_checks.append({'dataset':'CCD','year':year,'sampled_pixels':len(coords),'source_match':True})
    src=ROOT/'extracted/chinacp_wheat10m_2020/wheat_maize_china_2020_mosaic.tif'
    dst=ROOT/'derived/chinacp_wheat10m_2020/NCPbox_wheat_maize_2020_nativegrid.tif'
    with rasterio.open(src) as s,rasterio.open(dst) as d:
        coords=[d.xy(int(r),int(c)) for r,c in zip(rng.integers(0,d.height,150),rng.integers(0,d.width,150))]
        a=np.array([v[0] for v in d.sample(coords)])
        b=np.array([v[0] for v in s.sample(coords)])
        assert np.array_equal(a,np.where(b==1,1,255))
        pixel_checks.append({'dataset':'ChinaCP-Wheat10m','year':2020,'sampled_pixels':len(coords),'source_match':True})
    folder=ROOT/'derived/chinacp_500m_2015_2021'
    nums=None;dens=None
    for year in range(2015,2022):
        with rasterio.open(folder/f'NCPbox_wheat_maize_{year}_500m.tif') as s:
            a=s.read(1)
            if nums is None: nums=np.zeros(a.shape,dtype='uint8');dens=np.zeros(a.shape,dtype='uint8')
            nums+=a==1;dens+=a!=255
    nums[dens==0]=255
    with rasterio.open(folder/'NCPbox_rotation_year_count_2015_2021.tif') as s:assert np.array_equal(nums,s.read(1))
    with rasterio.open(folder/'NCPbox_valid_year_count_2015_2021.tif') as s:assert np.array_equal(dens,s.read(1))
    assert np.all(nums[dens>0]<=dens[dens>0])
    print('Spatial checks passed: 450 source-to-mask samples; full annual-count reconciliation',flush=True)

    datasets=[
        {'dataset':'CCD','published_years':'2001–2024','downloaded_years':'2020–2024',
         'downloaded_scope':'Hebei, Henan, Shandong, Tianjin, Anhui, Jiangsu; 30 province/year rasters',
         'nominal_resolution':'30 m','actual_grid':'EPSG:4326; ~0.000269494585236°',
         'rotation_class':'9','doi':'10.57760/sciencedb.32361','license':'CC BY 4.0',
         'local_folder':'raw/ccd_30m_2020_2024','article_doi':'10.1038/s41597-026-07370-5'},
        {'dataset':'ChinaCP','published_years':'2015–2021','downloaded_years':'2015–2021',
         'downloaded_scope':'China; 7 annual maps and all published auxiliary packages',
         'nominal_resolution':'500 m','actual_grid':'EPSG:32648; 500 × 500 m',
         'rotation_class':'246','doi':'10.6084/m9.figshare.14936052.v12','license':'CC BY 4.0',
         'local_folder':'extracted/chinacp_500m_2015_2021','article_doi':'10.1038/s41597-022-01589-8'},
        {'dataset':'ChinaCP-Wheat10m','published_years':'2020','downloaded_years':'2020',
         'downloaded_scope':'China wheat–maize layer only, plus code and README',
         'nominal_resolution':'10 m in product name','actual_grid':'EPSG:4326; 0.000179663056824° (~16.1 × 19.9 m at 36.5N)',
         'rotation_class':'1; source NoData=16','doi':'10.6084/m9.figshare.28646687.v3','license':'CC BY 4.0',
         'local_folder':'extracted/chinacp_wheat10m_2020','article_doi':'10.1016/j.agsy.2025.104338'},
    ]
    legends=[]
    for ds,values in {
        'CCD raw':{0:'non-crop/background (also outside province footprint)',1:'single-season rice',2:'double-season rice',3:'winter wheat',4:'winter wheat–single-season rice',5:'sugarcane',6:'maize',9:'winter wheat–maize'},
        'ChinaCP raw':{0:'fallow',14:'single maize',15:'single rice',16:'single wheat',17:'single others',245:'rice plus maize',246:'wheat plus maize',255:'double rice',256:'wheat plus rice',27:'other double cropping',3:'triple cropping',-2147483648:'NoData'},
        'ChinaCP-Wheat10m raw':{1:'mapped wheat–maize rotation',16:'NoData'},
        'CCD derived mask':{0:'other mapped nonzero crop class',1:'wheat–maize rotation',254:'source class disagreement',255:'source background/non-crop/NoData/outside selected coverage'},
        'ChinaCP derived mask':{0:'other valid source category',1:'wheat–maize rotation',255:'source NoData'},
        'ChinaCP-Wheat10m derived mask':{1:'mapped wheat–maize rotation',255:'source NoData'},
    }.items():
        legends.extend({'dataset':ds,'value':k,'meaning':v} for k,v in values.items())
    quality=[
        {'topic':'ChinaCP CRS','finding':'Paper says EPSG:4326; all seven downloaded class rasters report EPSG:32648. File georeferencing used.'},
        {'topic':'ChinaCP grid precision','finding':'2021 origin differs by 9.3e-10 metres; equality checked to absolute 1e-6 m. No resampling for annual counts.'},
        {'topic':'ChinaCP-Wheat10m grid','finding':'Downloaded file has 0.000179663-degree spacing, not a 10 x 10 m native grid.'},
        {'topic':'ChinaCP-Wheat10m background','finding':'Raw 16 is declared NoData, not confirmed non-rotation.'},
        {'topic':'CCD background','finding':'Raw 0 also appears outside individual province footprints; zeros cannot erase neighbouring nonzero classes.'},
        {'topic':'CCD province conflicts','finding':'Conflicting nonzero classes are retained as mask value 254; 2024 has 1071 pixels. See processing JSON for each year.'},
        {'topic':'Reference samples','finding':'Public Refer file has 2736 crop-type points; it is not the full 18379-sample paper validation dataset or a full rotation ground-truth set.'},
        {'topic':'Irrigation and groundwater','finding':'Crop rotation classes do not identify irrigation amounts, irrigation water source, or groundwater depth.'},
        {'topic':'ROI','finding':'112–120E,32–41N is a display/extraction box, not a formal North China Plain boundary.'},
        {'topic':'Overview fraction','finding':'Native-grid positions mapped rotation / all positions; not cropland-conditional fraction or probability.'},
        {'topic':'Station locations','finding':'Reference points have heterogeneous coordinate precision and plot meaning; not automatic footprint matching.'},
    ]
    derived=[]
    for p in sorted((ROOT/'derived').rglob('*')):
        if p.is_file():derived.append({'path':str(p.relative_to(ROOT)),'bytes':p.stat().st_size,'sha256':digest(p,'sha256')})
    frames={'数据集':pd.DataFrame(datasets),'原始文件':pd.DataFrame(records),'类别编码':pd.DataFrame(legends),
        '原始栅格属性':pd.DataFrame(inventories),'派生文件':pd.DataFrame(derived),'质量说明':pd.DataFrame(quality),
        'ZIP校验':pd.DataFrame(zip_checks),'空间抽检':pd.DataFrame(pixel_checks)}
    for name,df in [('datasets',frames['数据集']),('files',frames['原始文件']),('class_legend',frames['类别编码']),
            ('raster_inventory',frames['原始栅格属性']),('derived_files',frames['派生文件']),('quality_notes',frames['质量说明'])]:
        df.to_csv(cat/f'{name}.csv',index=False,encoding='utf-8-sig')
    with pd.ExcelWriter(cat/'轮作空间数据目录.xlsx',engine='openpyxl') as writer:
        for name,df in frames.items():
            df.to_excel(writer,sheet_name=name,index=False)
            sheet=writer.sheets[name];sheet.freeze_panes='A2';sheet.auto_filter.ref=sheet.dimensions
            for cells in sheet.columns:
                letter=cells[0].column_letter
                sheet.column_dimensions[letter].width=min(65,max(14,max(len(str(c.value or '')) for c in cells[:30])+2))
    checks={'accessed':'2026-10-02','original_files':len(records),'crop_data_files_with_source_md5':45,
        'original_bytes':sum(r['bytes'] for r in records),'crop_original_bytes':sum(r['bytes'] for r in records[:-1]),
        'zip_crc_passed':len(zip_checks),'original_geotiffs_opened':len(paths),
        'source_to_mask_sample_checks':pixel_checks,'annual_count_full_reconciliation':True,
        'primary_downloads_complete':True,'notes':'Raster inventory classes are sampled; masks are classifications, not independent ground truth.'}
    (ROOT/'metadata/delivery_checks.json').write_text(json.dumps(checks,indent=2))
    print(json.dumps(checks,indent=2),flush=True)

if __name__=='__main__':main()
