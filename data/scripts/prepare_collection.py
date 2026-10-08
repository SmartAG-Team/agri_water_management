"""Preserve downloads, repair legacy ZIP filename decoding, and catalogue tables.

Run: .venv/bin/python data/scripts/prepare_collection.py
CSV exports preserve worksheet cell order, including titles and unit rows;
they are not assumed to be harmonized observation tables.
"""
from pathlib import Path
import csv
import hashlib
import json
import re
import shutil
import zipfile
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]
DATA=ROOT/'data'

def save_csv(rows,path):
    path.parent.mkdir(parents=True,exist_ok=True)
    pd.DataFrame(rows).to_csv(path,index=False,encoding='utf-8-sig')

def station(title):
    for label,key in [('栾城','luancheng'),('禹城','yucheng'),('商丘','shangqiu'),('豫东','shangqiu'),('封丘','fengqiu'),('固城','gucheng'),('不同播期','gucheng'),('CERN地下水','cern_groundwater')]:
        if label in title:return key
    return 'other'

def flattened(d):
    result=dict(d)
    for section in d.get('customConfigurationData',[]):result.update(section)
    return result

def safe_name(s):
    return re.sub(r'[/\\:*?"<>|\n\r]','_',str(s))[:70]

def main():
    for p in list((DATA/'raw/nesdc').glob('*/metadata.json')):
        d=json.loads(p.read_text())['data'];site=station(d.get('dataSetTitle',''))
        dest=DATA/'raw/nesdc'/site/p.parent.name
        dest.parent.mkdir(exist_ok=True)
        if not dest.exists():shutil.move(str(p.parent),dest)

    files=[];archive_issues=[]
    for p in sorted((DATA/'raw').rglob('*')):
        if not p.is_file() or p.name in ['metadata.json','files_metadata.json','source_terms.json','download_issue.txt']:continue
        content=p.read_bytes()
        info={'path':str(p.relative_to(ROOT)),'bytes':len(content),'sha256':hashlib.sha256(content).hexdigest(),'archive_crc':'not_archive'}
        if p.suffix.lower()=='.zip':
            try:
                with zipfile.ZipFile(p,metadata_encoding='gb18030') as z:
                    bad=z.testzip()
                    if bad:raise ValueError('CRC failure: '+bad)
                    dest=DATA/'extracted'/p.relative_to(DATA/'raw').with_suffix('')
                    for member in z.infolist():
                        if member.is_dir() or '__MACOSX' in member.filename:continue
                        target=dest/member.filename
                        if not target.resolve().is_relative_to(dest.resolve()):raise ValueError('Unsafe archive path')
                        target.parent.mkdir(parents=True,exist_ok=True)
                        target.write_bytes(z.read(member))
                    info['archive_crc']='pass'
            except Exception as e:
                info['archive_crc']='failed';archive_issues.append({'file':str(p),'error':str(e)})
        files.append(info)
    save_csv(files,DATA/'catalog/files.csv')
    (DATA/'catalog/archive_issues.json').write_text(json.dumps(archive_issues,ensure_ascii=False,indent=2))

    sheets=[];table_errors=[]
    candidates=[]
    for base in [DATA/'raw',DATA/'extracted']:
        candidates.extend(p for p in base.rglob('*') if p.is_file() and p.suffix.lower() in ['.xlsx','.xls','.xlsm','.csv'] and 'documentation' not in p.parts)
    for p in sorted(candidates):
        rel=p.relative_to(DATA);rid=next((x for x in p.parts if re.fullmatch('[0-9a-f]{24}',x)),None)
        try:
            if p.suffix.lower()=='.csv':
                # Existing CSVs, including large FLUXNET products, remain unchanged.
                with p.open(encoding='utf-8-sig',errors='replace',newline='') as stream:
                    reader=csv.reader(stream);head=next(reader,[]);n=sum(1 for _ in reader)
                sheets.append({'dataset_id':rid or p.parts[p.parts.index('external')+1],'source_file':str(p.relative_to(ROOT)),'sheet':'CSV','rows_including_header':n+1,'columns':len(head),'first_nonempty_rows':json.dumps(head[:30],ensure_ascii=False),'table_csv':str(p.relative_to(ROOT))})
                continue
            book=pd.ExcelFile(p)
            for name in book.sheet_names:
                frame=pd.read_excel(book,sheet_name=name,header=None)
                key=hashlib.sha256(str(rel).encode()).hexdigest()[:8]
                dest=DATA/'tables'/rel.parent/safe_name(p.stem)/(safe_name(name)+'__'+key+'.csv')
                dest.parent.mkdir(parents=True,exist_ok=True)
                frame.to_csv(dest,index=False,header=False,encoding='utf-8-sig')
                preview=frame.dropna(how='all').head(5).fillna('').astype(str).values.tolist()
                sheets.append({'dataset_id':rid or p.parts[p.parts.index('external')+1],'source_file':str(p.relative_to(ROOT)),'sheet':name,'rows_including_header':len(frame),'columns':frame.shape[1],'first_nonempty_rows':json.dumps(preview,ensure_ascii=False)[:4000],'table_csv':str(dest.relative_to(ROOT))})
        except Exception as e:table_errors.append({'file':str(p.relative_to(ROOT)),'error':str(e)})
    save_csv(sheets,DATA/'catalog/worksheets.csv')
    sheet_frame=pd.DataFrame(sheets)
    irrigation=sheet_frame[sheet_frame.first_nonempty_rows.str.contains('灌溉制度|灌溉量|灌水量|Irrigation',case=False,na=False)]
    irrigation.to_csv(DATA/'catalog/irrigation_tables.csv',index=False,encoding='utf-8-sig')
    save_csv(table_errors,DATA/'catalog/table_read_issues.csv')
    (DATA/'catalog/preparation_summary.json').write_text(json.dumps({'original_files':len(files),'bytes':sum(x['bytes'] for x in files),'zip_archives':sum(x['archive_crc']=='pass' for x in files),'archive_issues':len(archive_issues),'table_sheets':len(sheets),'table_read_issues':len(table_errors)},indent=2))
    print((DATA/'catalog/preparation_summary.json').read_text())

if __name__=='__main__':main()
