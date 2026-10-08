"""Export groundwater observations with provenance and explicit quality flags.

Sources remain separate: overlapping releases are not independent replicates.
No imputation, coordinate matching, datum conversion, or record deletion occurs.
"""
from pathlib import Path
import json
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]
DATA=ROOT/'data'
OUT=DATA/'curated/groundwater'
OUT.mkdir(parents=True,exist_ok=True)

def source_path(p):return str(p.relative_to(ROOT))

def export(frame,name,value):
    frame['missing_or_nonnumeric_value']=pd.to_numeric(frame[value],errors='coerce').isna()
    frame['possible_sentinel_value']=pd.to_numeric(frame[value],errors='coerce').le(-999)
    frame['duplicate_well_date']=frame.duplicated(['well_id','date'],keep=False)
    frame['conflicting_values_same_well_date']=frame.groupby(['well_id','date'])[value].transform('nunique').gt(1)
    if 'station' in frame:
        frame['station_code_mismatch']=[not str(w).startswith(str(s)) for w,s in zip(frame.well_id,frame.station)]
    else:frame['station_code_mismatch']=False
    frame['invalid_date']=frame['date'].isna()
    frame.to_csv(OUT/name,index=False,encoding='utf-8-sig')
    return {'table':name,'rows':len(frame),'wells':frame['well_id'].nunique(),
            'valid_numeric_values':int((~frame['missing_or_nonnumeric_value'] & ~frame['possible_sentinel_value']).sum()),
            'missing_or_nonnumeric':int(frame['missing_or_nonnumeric_value'].sum()),
            'possible_sentinel':int(frame['possible_sentinel_value'].sum()),
            'duplicate_excess_rows':int(frame.duplicated(['well_id','date']).sum()),
            'rows_with_conflicting_same_date_values':int(frame.conflicting_values_same_well_date.sum()),
            'station_code_mismatch_rows':int(frame.station_code_mismatch.sum()),
            'invalid_dates':int(frame.invalid_date.sum()),
            'date_start':str(frame.date.min()),'date_end':str(frame.date.max())}

def dates(frame):
    return pd.to_datetime(frame[['年','月','日']].rename(columns={'年':'year','月':'month','日':'day'}),errors='coerce')

summaries=[]
p=next((DATA/'raw/nesdc/yucheng').rglob('*地下水位数据.xlsx'))
d=pd.read_excel(p)
f=pd.DataFrame({'date':dates(d),'well_id':d['样地代码'],'station':'YCA','well_name':d['样地名称'],
                'vegetation':d['植被名称'],'groundwater_depth_m':d['地下水埋深'],
                'ground_elevation_m':d['地面高程'],'source_excel_row':d.index+2,
                'source_doi':'10.12199/nesdc.ecodb.2021YFF0703900.yca.2025.3','source_file':source_path(p),
                'datum_status':'ground-elevation vertical datum not specified in workbook'})
summaries.append(export(f,'yucheng_2005_2022.csv','groundwater_depth_m'))
f.loc[f.duplicate_well_date].to_csv(OUT/'yucheng_duplicate_dates_review.csv',index=False,encoding='utf-8-sig')

p=next((DATA/'raw/nesdc/cern_groundwater').rglob('CERN_DXSW_2005-2014.xls'))
book=pd.ExcelFile(p)
for site in ['LCA','YCA','FQA']:
    d=pd.read_excel(book,sheet_name=site)
    f=pd.DataFrame({'date':dates(d),'source_observation_date':d['观测日期'],'well_id':d['地下水位观测井代码'],'station':site,'well_name':d['样地名称'],
                    'vegetation':d['植被名称'],'groundwater_depth_m':d['地下水埋深'],
                    'ground_elevation_m':d['地面高程'],'source_excel_row':d.index+2,
                    'source_doi':'10.11922/sciencedb.293','source_file':source_path(p),'source_sheet':site,
                    'datum_status':'ground-elevation vertical datum not reconciled'})
    summaries.append(export(f,f'cern_{site.lower()}_2005_2014.csv','groundwater_depth_m'))
    f.loc[f.duplicate_well_date|f.station_code_mismatch].to_csv(OUT/f'cern_{site.lower()}_review.csv',index=False,encoding='utf-8-sig')

p=next((DATA/'extracted/external/groundwater_zenodo_17799537').rglob('GroundwaterDepth_2005_2017.xlsx'))
d=pd.read_excel(p)
f=d.melt(id_vars=['ID','City','Type','Lon','Lat'],var_name='date',value_name='groundwater_depth_m')
f=f.rename(columns={'ID':'well_id','City':'city','Type':'aquifer_type_code','Lon':'longitude','Lat':'latitude'})
f['date']=pd.to_datetime(f['date'],format='%Y-%m',errors='coerce')
f['aquifer_class']=f.aquifer_type_code.map(lambda x:'unconfined' if 10<x<=19 else 'confined' if 20<x<=29 else 'unknown')
f['source_doi']='10.5281/zenodo.17799537';f['source_file']=source_path(p)
summaries.append(export(f,'tsinghua_ncp_2005_2017_monthly.csv','groundwater_depth_m'))

folder=DATA/'raw/external/groundwater_zenodo_7798617'
p=folder/'hydraulic_datacenter.xlsx';d=pd.read_excel(p).copy()
d.insert(0,'well_id',['datacenter_row_'+str(i+2) for i in range(len(d))])
f=d.melt(id_vars=['well_id','LON','LAT'],var_name='date',value_name='groundwater_level_anomaly_m')
f=f.rename(columns={'LON':'longitude','LAT':'latitude'});f['date']=pd.to_datetime(f.date,errors='coerce')
f['source_doi']='10.5281/zenodo.7798617';f['source_file']=source_path(p)
f['quantity_note']='Anomaly per worksheet name; baseline not established; not absolute depth'
summaries.append(export(f,'wrr_datacenter_2005_2018_anomaly.csv','groundwater_level_anomaly_m'))

p=folder/'hydraulic_yearbook.xlsx';d=pd.read_excel(p)
ids=list(d.columns[:6]);f=d.melt(id_vars=ids,var_name='date',value_name='reported_groundwater_value_m')
f=f.rename(columns={ids[1]:'well_id',ids[2]:'aquifer_type_code',ids[3]:'ground_elevation_m',ids[4]:'longitude',ids[5]:'latitude'}).drop(columns=[ids[0]])
f['date']=pd.to_datetime(f.date,errors='coerce');f['source_doi']='10.5281/zenodo.7798617';f['source_file']=source_path(p)
f['quantity_note']='Workbook does not state depth versus head in column header; reconcile against paper before use'
summaries.append(export(f,'wrr_yearbook_2005_2016_reported_values.csv','reported_groundwater_value_m'))

index=pd.read_csv(DATA/'catalog/worksheets.csv')
r=index[index['sheet']=='Fig1.groundwater water level'].iloc[0]
p=ROOT/r.source_file;d=pd.read_excel(p,sheet_name=r.sheet)
f=pd.DataFrame({'date':pd.to_datetime(d['Date'],errors='coerce'),'well_id':'luancheng_figure_series_well_id_unspecified',
                'groundwater_depth_m':d['Water table depth (m)'],'source_doi':'10.17632/sfbbjrvktj.1',
                'source_file':source_path(p),'source_excel_row':d.index+2,
                'series_note':'Mixed temporal frequency; well identifier and historical comparability need verification; overlaps other Luancheng releases'})
summaries.append(export(f,'luancheng_1974_2023_reported_depth.csv','groundwater_depth_m'))

r=index[index['sheet']=='Fig2.rainfall,Irrigation and ET'].iloc[0]
p=ROOT/r.source_file;d=pd.read_excel(p,sheet_name=r.sheet)
d=d.rename(columns={'Date':'date','Rainfall (mm)':'rainfall_mm','Irrigation (mm)':'irrigation_mm','ET (mm)':'reported_et_mm'})
d['source_doi']='10.17632/sfbbjrvktj.1';d['source_file']=source_path(p)
d['et_method_status']='Repository figures mix observations and simulations; independently verify ET measurement/estimation method before validation'
d.to_csv(OUT/'luancheng_2021_2023_daily_water_balance.csv',index=False,encoding='utf-8-sig')

pd.DataFrame(summaries).to_csv(DATA/'catalog/groundwater_quality.csv',index=False,encoding='utf-8-sig')
(DATA/'catalog/groundwater_quality.json').write_text(json.dumps(summaries,ensure_ascii=False,indent=2))
print(pd.DataFrame(summaries).drop(columns=['date_start','date_end']).to_string(index=False))
