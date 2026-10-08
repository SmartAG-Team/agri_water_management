"""Extract explicitly labelled irrigation-event tables from four NCP stations.

Keeps reported year and calendar date separate for winter-wheat seasons.
Unparsed dates/amounts and duplicate candidates are flagged, never imputed.
"""
from pathlib import Path
import pandas as pd

ROOT=Path(__file__).resolve().parents[2];DATA=ROOT/'data'
out=DATA/'curated/irrigation';out.mkdir(parents=True,exist_ok=True)
catalog=pd.read_csv(DATA/'catalog/datasets.csv').set_index('dataset_id')
index=pd.read_csv(DATA/'catalog/irrigation_tables.csv')
frames=[]
for _,record in index.iterrows():
    if '/nesdc/' not in record.source_file:continue
    d=pd.read_excel(ROOT/record.source_file,sheet_name=record.sheet,header=None)
    if d.empty or '灌溉制度' not in str(d.iloc[0,0]):continue
    headers=[i for i,row in d.head(6).iterrows() if row.astype(str).str.contains('灌溉时间').any()]
    if not headers or d.shape[1]!=7:continue
    start=headers[0]+1
    f=d.iloc[start:].copy();f.columns=['reported_year','crop_original','event_date_original','growth_stage_original','water_source_original','method_original','irrigation_amount_original']
    f['source_excel_row']=f.index+1
    # Exclude only all-empty rows, unit rows and notes without an event year.
    numeric_year=pd.to_numeric(f.reported_year,errors='coerce')
    f=f.loc[numeric_year.between(1900,2100)].copy()
    if f.empty:continue
    header=' '.join(d.iloc[:start+1].fillna('').astype(str).values.ravel())
    f['amount_unit']='mm' if 'mm' in header else 'unverified'
    f['event_date']=pd.to_datetime(f.event_date_original,errors='coerce',format='mixed')
    f['irrigation_mm']=pd.to_numeric(f.irrigation_amount_original,errors='coerce').where(f.amount_unit=='mm')
    f['invalid_date']=f.event_date.isna();f['amount_needs_review']=f.irrigation_mm.isna()
    f['station']=catalog.loc[record.dataset_id,'site'];f['plot_sheet']=record.sheet
    f['source_doi']=catalog.loc[record.dataset_id,'doi'];f['source_file']=record.source_file
    f['duplicate_candidate']=f.duplicated(['event_date','crop_original','irrigation_mm'],keep=False)
    frames.append(f)
events=pd.concat(frames,ignore_index=True)
events.to_csv(out/'station_irrigation_events_all_crops.csv',index=False,encoding='utf-8-sig')
target=events[events.crop_original.astype(str).str.contains('小麦|玉米')]
target.to_csv(out/'station_irrigation_events_wheat_maize.csv',index=False,encoding='utf-8-sig')
summary=events.groupby('station').agg(event_rows=('station','size'),plot_sheets=('plot_sheet','nunique'),date_start=('event_date','min'),date_end=('event_date','max'),date_flags=('invalid_date','sum'),amount_flags=('amount_needs_review','sum'))
summary.to_csv(DATA/'catalog/irrigation_events_summary.csv',encoding='utf-8-sig')
print(summary.to_string());print('wheat/maize event rows',len(target))
