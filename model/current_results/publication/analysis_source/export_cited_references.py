"""Bibliographic exchange records for the citations actually used in the article."""
from pathlib import Path
import json,re
from word_documents import load_references

P=Path(__file__).resolve().parents[1]

def main():
    ids=set()
    for name in ['manuscript_blocks.json','supplementary_blocks.json']:
        blocks=json.loads((P/'analysis_source'/name).read_text())['blocks']
        for block in blocks:
            for text in block.get('paragraphs',[])+[block.get(key,'') for key in ['text','caption','table_caption','table_note']]:
                for group in re.findall(r'\[CITE:([^\]]+)\]',text):ids.update(group.split('|'))
    records=load_references()
    used=[records[key] for key in sorted(ids)]
    (P/'literature/manuscript_references.csl.json').write_text(json.dumps(used,indent=2,ensure_ascii=False))
    bib=[];ris=[]
    for r in used:
        year=r.get('issued',{}).get('date-parts',[[None]])[0][0]
        authors=['{'+a['literal']+'}' if 'literal' in a else a.get('family','')+', '+a.get('given','') for a in r.get('author',[])]
        fields={'author':' and '.join(authors),'title':r['title'],'year':year,
                'journal':r.get('container-title'),'volume':r.get('volume'),'number':r.get('issue'),
                'pages':r.get('page'),'doi':r.get('DOI'),'url':r.get('URL')}
        if r.get('type')=='software' and r.get('version'):
            fields.update(version=r['version'],note='Software version '+r['version'])
            accessed=r.get('accessed',{}).get('date-parts',[[]])[0]
            if len(accessed)==3:fields['urldate']='-'.join([str(accessed[0]),f'{accessed[1]:02d}',f'{accessed[2]:02d}'])
        if r.get('type')=='manuscript':
            fields.update(note=r.get('note') or r.get('status'))
        kind={'article-journal':'article','book':'book','manuscript':'unpublished'}.get(r.get('type'),'misc')
        if kind=='book':
            fields.update(publisher=r.get('publisher'),address=r.get('publisher-place'),series=r.get('collection-title'),number=r.get('collection-number'),isbn=r.get('ISBN'))
        lines=[f'@{kind}{{{r["id"]},']+[f'  {key} = {{{value}}},' for key,value in fields.items() if value]
        bib.append('\n'.join(lines+['}']))
        ris_type={'article-journal':'JOUR','book':'BOOK','software':'COMP','webpage':'ELEC','manuscript':'UNPB'}.get(r.get('type'),'DATA')
        rows=['TY  - '+ris_type]
        for author in r.get('author',[]):rows.append('AU  - '+(author.get('literal') or author.get('family','')+', '+author.get('given','')))
        for tag,key in [('TI','title'),('JO','container-title'),('VL','volume'),('IS','issue'),('DO','DOI'),('UR','URL'),('PB','publisher'),('CY','publisher-place'),('SN','ISBN')]:
            if r.get(key):rows.append(f'{tag}  - {r[key]}')
        if year is not None:rows.append(f'PY  - {year}')
        if r.get('type')=='manuscript' and r.get('note'):
            rows.append('N1  - '+r['note'])
        if r.get('type')=='software' and r.get('version'):
            rows.append('ET  - '+r['version'])
            if r.get('note'):rows.append('N1  - '+r['note'])
            accessed=r.get('accessed',{}).get('date-parts',[[]])[0]
            if len(accessed)==3:rows.append('Y2  - '+ '/'.join(map(str,accessed)))
        if r.get('page'):
            endpoints=r['page'].replace('–','-').split('-',1)
            rows.append('SP  - '+endpoints[0])
            if len(endpoints)>1:rows.append('EP  - '+endpoints[1])
        rows.extend(['ID  - '+r['id'],'ER  -']);ris.append('\n'.join(rows))
    (P/'literature/manuscript_references.bib').write_text('\n\n'.join(bib)+'\n')
    (P/'literature/manuscript_references.ris').write_text('\n\n'.join(ris)+'\n')
    print(f'Exported {len(used)} cited reference records')

if __name__=='__main__':main()
