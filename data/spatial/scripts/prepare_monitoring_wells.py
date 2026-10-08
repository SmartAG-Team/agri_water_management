"""Create the monitoring-well display layer from the downloaded original workbook.

Keep well IDs at their reported coordinates; co-located aquifers are not merged
or jittered. Only wells with at least one valid monthly value enter the figure.
"""
from pathlib import Path
import hashlib
import json
import numpy as np
import pandas as pd
from shapely.geometry import Point, shape
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT.parent

def prepare():
    source = next((DATA/'extracted/external/groundwater_zenodo_17799537').rglob('GroundwaterDepth_2005_2017.xlsx'))
    raw = pd.read_excel(source)
    assert raw.ID.is_unique
    dates = pd.to_datetime(raw.columns[5:],format='%Y-%m')
    observations = raw.iloc[:,5:].apply(pd.to_numeric,errors='coerce')
    valid = observations.notna() & np.isfinite(observations) & observations.gt(-999)
    f = raw[['ID','City','Type','Lon','Lat']].rename(columns={
        'ID':'well_id','City':'city','Type':'aquifer_type_code','Lon':'longitude','Lat':'latitude'})
    f['aquifer_class'] = np.select([f.aquifer_type_code.between(11,19),f.aquifer_type_code.between(21,29)],
        ['Unconfined','Confined'],default='Unspecified')
    f['valid_months'] = valid.sum(axis=1)
    f['first_observation'] = [dates[v].min().strftime('%Y-%m') if v.any() else '' for v in valid.to_numpy()]
    f['last_observation'] = [dates[v].max().strftime('%Y-%m') if v.any() else '' for v in valid.to_numpy()]
    f['coordinate_valid'] = np.isfinite(f.longitude)&np.isfinite(f.latitude)&f.longitude.between(-180,180)&f.latitude.between(-90,90)
    f['plotted'] = f.coordinate_valid & f.valid_months.gt(0)
    f['source_doi'] = '10.5281/zenodo.17799537'
    f['source_excel_row'] = np.arange(2,len(f)+2)
    boundaries=json.loads((ROOT/'derived/ccd_full_provinces_2024/province_display_boundaries.geojson').read_text())
    union=unary_union([shape(v['geometry']) for v in boundaries['features']])
    f['inside_crop_display_provinces']=[bool(union.covers(Point(x,y))) for x,y in zip(f.longitude,f.latitude)]
    f['same_coordinate_well_count'] = f.groupby(['longitude','latitude']).well_id.transform('size')
    folder=ROOT/'derived/monitoring_wells';folder.mkdir(exist_ok=True,parents=True)
    f.to_csv(folder/'monitoring_well_inventory.csv',index=False,encoding='utf-8-sig')
    plotted=f.loc[f.plotted].copy()
    features=[]
    for row in plotted.to_dict('records'):
        features.append({'type':'Feature','geometry':{'type':'Point','coordinates':[row['longitude'],row['latitude']]},
            'properties':row})
    (folder/'monitoring_wells_2005_2017.geojson').write_text(json.dumps({'type':'FeatureCollection','features':features},ensure_ascii=False))
    summary={'source_doi':'10.5281/zenodo.17799537','source_file':str(source.relative_to(DATA.parent)),
        'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'source_period':'2005–2017',
        'source_well_ids':len(f),'plotted_well_ids':len(plotted),
        'excluded_no_valid_observations':int(f.valid_months.eq(0).sum()),
        'excluded_invalid_coordinates':int((~f.coordinate_valid).sum()),
        'plotted_unique_coordinate_pairs':len(plotted[['longitude','latitude']].drop_duplicates()),
        'plotted_type_counts':plotted.aquifer_class.value_counts().to_dict(),
        'plotted_valid_monthly_values':int(plotted.valid_months.sum()),
        'inside_crop_display_provinces':int(plotted.inside_crop_display_provinces.sum()),
        'outside_crop_display_provinces':int((~plotted.inside_crop_display_provinces).sum()),
        'outside_crop_display_cities':plotted.loc[~plotted.inside_crop_display_provinces].city.value_counts().to_dict(),
        'coordinate_note':'Reported Lon/Lat plotted as geographic longitude/latitude without datum conversion. Source README does not explicitly name a horizontal datum.',
        'plot_policy':'All wells with valid observations within the figure extent, including Beijing. Co-located IDs are retained without coordinate jitter.',
        'time_note':'Historical monitoring locations over a 2024 crop map; not 2024 groundwater observations.',
        'type_note':'Unconfined: 11–19; confined: 21–29; all other source type codes left unspecified.'}
    (ROOT/'metadata/monitoring_well_layer.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    assert len(plotted)==885 and len(f)==891
    assert sum(summary['plotted_type_counts'].values())==len(plotted)
    assert len(features)==len(plotted)
    return plotted,summary

if __name__=='__main__':
    _,summary=prepare();print(json.dumps(summary,ensure_ascii=False,indent=2))
