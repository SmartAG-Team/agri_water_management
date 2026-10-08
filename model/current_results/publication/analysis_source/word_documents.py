"""Word authoring with verified CSL data and embedded Zotero citation fields."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import pandas as pd
from docx import Document
from docx.shared import Inches,Pt,RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.part import Part
from docx.opc.packuri import PackURI
from docx.opc.constants import CONTENT_TYPE,RELATIONSHIP_TYPE
from lxml import etree
from citeproc import CitationStylesStyle,CitationStylesBibliography,Citation,CitationItem,formatter
from citeproc.source.json import CiteProcJSON
from inline_equations import add_text as add_mathematical_text

RUN=Path(__file__).resolve().parents[1]
OUT=RUN/'documents'
STYLE_ID='http://www.zotero.org/styles/agricultural-water-management'
TITLE='Irrigation strategies under contrasting water-storage and rainfall conditions in the North China Plain'
AFFILIATION='College of Soil and Water Conservation Science and Engineering (Institute of Soil and Water Conservation), Northwest A&F University, Yangling, China'


def author_information():
    return json.loads((RUN/'analysis_source/author_information.json').read_text())


def add_author_block(doc,line_spacing=1.5):
    info=author_information();paragraphs=[]
    paragraph=doc.add_paragraph();paragraphs.append(paragraph)
    for index,author in enumerate(info['authors']):
        if index:paragraph.add_run(', ')
        paragraph.add_run(author['name'])
        markers=[str(i) for i in author['affiliations']]
        if author['equal_first']:markers.append('†')
        if author['corresponding']:markers.append('*')
        paragraph.add_run(','.join(markers)).font.superscript=True
    for index,affiliation in info['affiliations'].items():
        paragraph=doc.add_paragraph();paragraphs.append(paragraph)
        paragraph.add_run(index).font.superscript=True
        paragraph.add_run(' '+affiliation)
    paragraphs.append(doc.add_paragraph('† '+info['equal_first_note']))
    people=[a['name']+' ('+a['email']+')' for a in info['authors'] if a['corresponding']]
    paragraphs.append(doc.add_paragraph('* Corresponding authors: '+' and '.join(people)+'.'))
    for index,paragraph in enumerate(paragraphs):
        paragraph.paragraph_format.line_spacing=line_spacing
        paragraph.paragraph_format.keep_with_next=index<len(paragraphs)-1
    return paragraphs


def cover_signoff():
    info=author_information();people=[a for a in info['authors'] if a['corresponding']]
    return [' and '.join(a['name'] for a in people),
            info['affiliations'][str(people[0]['affiliations'][0])],
            '; '.join(a['email'] for a in people)]


def load_references():
    references=json.loads((RUN/'literature/verified_references.json').read_text())
    extra=RUN/'literature/treatment_sources/additional_treatment_references.json'
    if extra.exists():
        records=json.loads(extra.read_text())
        references.extend(records if isinstance(records,list) else records.get('references',[]))
    supplemental=RUN/'literature/supplemental_reference_records.json'
    if supplemental.exists():references.extend(json.loads(supplemental.read_text()))
    seen={}
    for r in references:
        # citeproc-py uses page for Elsevier article numbers; verified metadata
        # are retained unchanged in source snapshots and embedded itemData.
        if 'article-number' in r and 'page' not in r:r['page']=str(r['article-number'])
        doi=r.get('DOI','').lower()
        if r['id'] in seen:raise ValueError('Duplicate citation identifier')
        if doi and any(x.get('DOI','').lower()==doi for x in seen.values()):continue
        seen[r['id']]=r
    return seen


def new_document(title=None):
    doc=Document()
    section=doc.sections[0]
    section.top_margin=section.bottom_margin=Inches(.8)
    section.left_margin=section.right_margin=Inches(.9)
    normal=doc.styles['Normal']
    normal.font.name='Times New Roman';normal.font.size=Pt(11)
    normal.paragraph_format.line_spacing=1.5
    normal.paragraph_format.space_after=Pt(6)
    title_style=doc.styles['Title']
    title_style.font.name='Times New Roman';title_style.font.size=Pt(16)
    title_style.font.bold=True;title_style.font.color.rgb=RGBColor(0,0,0)
    title_style.paragraph_format.line_spacing=1.1
    title_style.paragraph_format.space_after=Pt(12)
    for name in ['Heading 1','Heading 2','Heading 3']:
        doc.styles[name].font.name='Times New Roman'
        doc.styles[name].font.color.rgb=RGBColor(0,0,0)
    for style in [title_style]+[doc.styles[n] for n in ['Heading 1','Heading 2','Heading 3']]:
        if style._element.pPr is not None:
            for border in style._element.pPr.findall(qn('w:pBdr')):style._element.pPr.remove(border)
    caption=doc.styles['Caption']
    caption.font.name='Times New Roman';caption.font.size=Pt(9)
    caption.font.color.rgb=RGBColor(0,0,0);caption.font.italic=False
    caption.paragraph_format.line_spacing=1.1
    doc.core_properties.author='; '.join(a['name'] for a in author_information()['authors'])
    doc.core_properties.title=title or TITLE
    footer=section.footer.paragraphs[0]
    footer.alignment=2
    simple=OxmlElement('w:fldSimple');simple.set(qn('w:instr'),'PAGE')
    footer._p.append(simple)
    return doc


def field(paragraph,instruction,result):
    run=paragraph.add_run()
    begin=OxmlElement('w:fldChar');begin.set(qn('w:fldCharType'),'begin');run._r.append(begin)
    code=OxmlElement('w:instrText');code.set(qn('xml:space'),'preserve');code.text=instruction
    paragraph.add_run()._r.append(code)
    sep=OxmlElement('w:fldChar');sep.set(qn('w:fldCharType'),'separate');paragraph.add_run()._r.append(sep)
    paragraph.add_run(result)
    end=OxmlElement('w:fldChar');end.set(qn('w:fldCharType'),'end');paragraph.add_run()._r.append(end)


class Citations:
    def __init__(self):
        self.records=load_references()
        style=CitationStylesStyle(str(RUN/'literature/elsevier-harvard.csl'),validate=False)
        rendering_records=[{k:v for k,v in r.items() if k!='article-number'} for r in self.records.values()]
        self.processor=CitationStylesBibliography(style,CiteProcJSON(rendering_records),formatter.plain)
        self.groups=[]
        self.occurrences=0
    def prepare(self,blocks):
        # citeproc-py does not implement CSL year-suffix disambiguation. Assign
        # suffixes only among this document's cited records and render them in
        # both citations and bibliography; embedded source dates stay intact.
        used={key for block in blocks
              for text in ([block.get(name,'') for name in ['text','caption','table_caption','table_note']] + block.get('paragraphs',[]))
              for group in re.findall(r'\[CITE:([^\]]+)\]',text)
              for key in group.split('|')}
        same_author_year={}
        for key in used:
            record=self.records[key]
            authors=tuple((a.get('literal',''),a.get('family',''),a.get('given','')) for a in record.get('author',[]))
            year=record.get('issued',{}).get('date-parts',[[None]])[0][0]
            if authors and year is not None:same_author_year.setdefault((authors,year),[]).append(key)
        for keys in same_author_year.values():
            if len(keys)<2:continue
            for index,key in enumerate(sorted(keys,key=lambda k:(self.records[k]['title'].casefold(),k))):
                suffix=chr(ord('a')+index)
                self.processor.source[key]['year_suffix']=self.processor.source.parse_string(suffix)
        style=self.processor.style
        namespace={'csl':'http://purl.org/net/xbiblio/csl'}
        issued=style.root.xpath('csl:macro[@name="issued"]/csl:choose/*[@variable="issued"]',namespaces=namespace)[0]
        issued.append(etree.fromstring(b'<text xmlns="http://purl.org/net/xbiblio/csl" variable="year-suffix"/>',parser=style.parser))
        for block in blocks:
            for text in ([block.get(key,'') for key in ['text','caption','table_caption','table_note']] + block.get('paragraphs',[])):
                for match in re.finditer(r'\[CITE:([^\]]+)\]',text):
                    ids=match.group(1).split('|')
                    if not all(i in self.records for i in ids):raise KeyError(ids)
                    citation=Citation([CitationItem(i) for i in ids])
                    self.processor.register(citation)
                    self.groups.append((ids,citation))
    def add_text(self,paragraph,text):
        position=0
        for match in re.finditer(r'\[CITE:([^\]]+)\]',text):
            add_mathematical_text(paragraph,text[position:match.start()])
            ids=match.group(1).split('|')
            _,citation=next(g for g in self.groups if g[0]==ids)
            rendered=str(self.processor.cite(citation,lambda c:None))
            self.occurrences+=1
            cid=hashlib.sha256((str(self.occurrences)+'|'+str(match.start())+'|'.join(ids)+text).encode()).hexdigest()[:16]
            payload=dict(citationID=cid,properties=dict(formattedCitation=rendered,plainCitation=rendered,noteIndex=0),
                citationItems=[dict(id=i,itemData=self.records[i]) for i in ids],
                schema='https://github.com/citation-style-language/schema/raw/master/csl-citation.json')
            field(paragraph,' ADDIN ZOTERO_ITEM CSL_CITATION '+json.dumps(payload,ensure_ascii=False,separators=(',',':'))+' ',rendered)
            position=match.end()
        add_mathematical_text(paragraph,text[position:])
    def bibliography(self,doc):
        doc.add_heading('References',level=1)
        rows=[str(x) for x in self.processor.bibliography()]
        # The journal CSL omits repository URLs and versions for software.
        # Retain those identifiers for versioned parameter-definition sources.
        for record in self.records.values():
            if record.get('type')!='software' or not record.get('version') or not record.get('URL'):continue
            for index,row in enumerate(rows):
                if record['title'] not in row:continue
                authors='; '.join(a.get('literal') or a.get('family','') for a in record.get('author',[]))
                year=record.get('issued',{}).get('date-parts',[[None]])[0][0]
                version=record['version']
                display_version=version[:7] if re.fullmatch(r'[0-9a-f]{40}',version) else version
                accessed=record.get('accessed',{}).get('date-parts',[[]])[0]
                access=''
                if len(accessed)==3:
                    from datetime import date
                    access=' (accessed '+date(*accessed).strftime('%-d %B %Y')+')'
                rows[index]=f"{authors}, {year or 'n.d.'}. {record['title']} [software]. Version {display_version}. {record['URL']}{access}."
        if not rows:raise ValueError('A manuscript bibliography requires cited references')
        p=doc.add_paragraph()
        field(p,' ADDIN ZOTERO_BIBL {"uncited":[],"omitted":[],"custom":[]} CSL_BIBLIOGRAPHY ',rows[0])
        closing=p._p[-1];p._p.remove(closing)
        paragraphs=[p]
        for row in rows[1:]:paragraphs.append(doc.add_paragraph(row))
        paragraphs[-1]._p.append(closing)
        for p in paragraphs:
            p.paragraph_format.left_indent=Inches(.2);p.paragraph_format.first_line_indent=Inches(-.2)
        return rows


def zotero_preferences(doc):
    # Embedded itemData is authentic verified reference metadata. No unavailable
    # local-library URI or invented Zotero item key is inserted.
    xml='<data data-version="3" zotero-version="7.0"><session id="ncp-irrigation"/><style id="'+STYLE_ID+'" locale="en-US" hasBibliography="1" bibliographyStyleHasBeenSet="1"/><prefs><pref name="fieldType" value="Field"/><pref name="automaticJournalAbbreviations" value="1"/><pref name="noteType" value="0"/></prefs></data>'
    namespace='http://schemas.openxmlformats.org/officeDocument/2006/custom-properties'
    vt='http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes'
    properties=etree.Element('{'+namespace+'}Properties',nsmap={None:namespace,'vt':vt})
    prop=etree.SubElement(properties,'{'+namespace+'}property',
        fmtid='{D5CDD505-2E9C-101B-9397-08002B2CF9AE}',pid='2',name='ZOTERO_PREF_1')
    etree.SubElement(prop,'{'+vt+'}lpwstr').text=xml
    part=Part(PackURI('/docProps/custom.xml'),CONTENT_TYPE.OFC_CUSTOM_PROPERTIES,
        etree.tostring(properties,xml_declaration=True,encoding='UTF-8',standalone=True),doc.part.package)
    doc.part.package.relate_to(part,RELATIONSHIP_TYPE.CUSTOM_PROPERTIES)


def render_blocks(doc,blocks,citations):
    anchored={}
    for block in blocks:
        if block.get('figure') and block.get('after_paragraph'):
            anchor=block['after_paragraph']
            anchored.setdefault((anchor['heading'],anchor['index']),[]).append(block)
    emitted=set()
    for block in blocks:
        if block.get('figure') and block.get('after_paragraph'):
            if block['figure'] not in emitted:
                raise ValueError('Figure paragraph anchor was not rendered: '+block['figure'])
            continue
        if block.get('page_break'):doc.add_page_break()
        if block.get('heading'):doc.add_heading(block['heading'],level=block.get('level',1))
        for index,text in enumerate(block.get('paragraphs',[])):
            paragraph=doc.add_paragraph(style='List Bullet' if block.get('bullet_list') else None)
            citations.add_text(paragraph,text)
            for figure in anchored.get((block.get('heading'),index),[]):
                inline={key:value for key,value in figure.items() if key!='after_paragraph'}
                render_blocks(doc,[inline],citations)
                emitted.add(figure['figure'])
        if block.get('text'):citations.add_text(doc.add_paragraph(),block['text'])
        if block.get('figure'):
            path=RUN/block['figure']
            if not path.exists():raise FileNotFoundError(path)
            # Keep each embedded figure and caption together in the text.
            from PIL import Image
            with Image.open(path) as im:
                width=min(6.25,6.6*im.width/im.height)
            picture=doc.add_paragraph()
            picture.alignment=1
            picture.paragraph_format.keep_with_next=True
            picture.paragraph_format.space_after=Pt(3)
            picture.add_run().add_picture(str(path),width=Inches(width))
            p=doc.add_paragraph(style='Caption');citations.add_text(p,block['caption'])
            p.paragraph_format.keep_together=True
        if block.get('table'):
            frame=pd.read_csv(RUN/block['table']).fillna('—')
            if block.get('columns'):frame=frame[block['columns']]
            if block.get('table_caption'):
                caption=doc.add_paragraph(style='Caption');citations.add_text(caption,block['table_caption'])
                caption.paragraph_format.keep_with_next=True
            tab=doc.add_table(rows=1,cols=len(frame.columns));tab.style='Normal Table'
            borders=OxmlElement('w:tblBorders')
            for edge in ['top','bottom','left','right','insideH','insideV']:
                border=OxmlElement('w:'+edge)
                border.set(qn('w:val'),'single' if edge in ['top','bottom'] else 'nil')
                if edge in ['top','bottom']:
                    border.set(qn('w:sz'),'8');border.set(qn('w:color'),'000000')
                borders.append(border)
            tab._tbl.tblPr.append(borders)
            sized=bool(block.get('column_weights')) or 'Variable' in frame.columns
            if sized:
                weights={'Crop':.55,'Partition':.85,'Variable':1.25,'n':.30,'Unit':.72}
                sizes=block.get('column_weights') or [weights.get(str(c),.58) for c in frame.columns]
                if len(sizes)!=len(frame.columns) or any(s<=0 for s in sizes):raise ValueError('Invalid manuscript table column widths')
                tab.autofit=False
                for column,size in zip(tab.columns,sizes):column.width=Inches(6.7*size/sum(sizes))
            headers={'r2':'NSE','R2':'R²','Bias':'MBE','bias':'MBE','rmse':'RMSE','fraction':'Irrigation fraction',
                'n_cell_years':'Cell-years','Calibration_cases':'Calibration cases','Testing_cases':'Testing cases',
                'Quota_pct':'Quota (%)','Wheat_I_mm':'Wheat I (mm)','Maize_I_mm':'Maize I (mm)',
                'Training_grain_pct':'Training grain (%)','Testing_grain_pct':'Testing grain (%)',
                'Testing_ET_reduction_mm':'Testing ET reduction (mm)','relative_class':'Class',
                'grain_retention_target_pct':'Grain target (%)','selected_quota_fraction':'Selected fraction',
                'field_irrigation_mm':'Applied I (mm)','training_grain_retention_pct':'Training grain (%)',
                'training_n_harvest_years':'Training years','period':'Period',
                'n_representative_years':'Unit-years','n_harvest_years':'Years',
                'class_area_year_ha':'Area–years (ha yr)','fraction_of_period_area_year':'Area–year fraction',
                'policy':'Training target','scope':'Evaluation years','n_years':'Years',
                'grain_retention_pct':'Grain retained (%)','et_reduction_mm':'ET reduction (mm)',
                'annual_grain_retention_min_pct':'Minimum annual grain (%)'}
            from crop_model_terminology import normalize
            display_headers={'Partition':'Dataset','Testing_cases':'Evaluation cases',
                'Quota_pct':'Irrigation (%)','Training_grain_pct':'Selection grain (%)',
                'Testing_grain_pct':'Evaluation grain (%)','Testing_ET_reduction_mm':'Evaluation ET reduction (mm)',
                'policy':'Selection target','Legacy RMSE':'Original RMSE','Refit RMSE':'Recalibrated RMSE',
                'Refit bias':'Recalibrated MBE','Refit NSE':'Recalibrated NSE','Refit R²':'Recalibrated R²',
                'Primary quota':'Original fraction','Screened quota':'Reoptimized fraction',
                'Estimates / allocation':'Parameters / allocation'}
            for cell,name in zip(tab.rows[0].cells,frame.columns):
                cell.text=normalize(block.get('header_labels',{}).get(str(name),display_headers.get(str(name),headers.get(str(name),str(name).replace('_',' ')))),policy=True)
            for row in frame.itertuples(index=False,name=None):
                for cell,value,name in zip(tab.add_row().cells,row,frame.columns):
                    value={'lower_storage_dry':'Lower storage / lower rainfall','lower_storage_wet':'Lower storage / higher rainfall',
                        'higher_storage_dry':'Higher storage / lower rainfall','higher_storage_wet':'Higher storage / higher rainfall',
                        'missing_storage':'Missing storage','t ha-1':'t ha⁻¹','mm d-1':'mm d⁻¹','m2 m-2':'m² m⁻²',
                        'adaptive_95':'95%','adaptive_98':'98%','all_years':'All years',
                        'class_available_years':'Class available'}.get(str(value),value)
                    digits=2 if str(name).endswith('_pct') else 3
                    if name in ['Quota_pct','Wheat_I_mm','Maize_I_mm','field_irrigation_mm','grain_retention_target_pct']:digits=0
                    digits=block.get('precision',{}).get(str(name),digits)
                    if pd.isna(value):cell.text='—'
                    elif isinstance(value,float) and 0<abs(value)<10**(-digits):
                        mantissa,exponent=f'{value:.2e}'.split('e')
                        cell.text=mantissa+' × 10'+str(int(exponent)).translate(str.maketrans('-0123456789','⁻⁰¹²³⁴⁵⁶⁷⁸⁹'))
                    else:cell.text=f'{value:.{digits}f}' if isinstance(value,float) else normalize(str(value),policy=True)
            for i,row in enumerate(tab.rows):
                for cell in row.cells:
                    props=cell._tc.get_or_add_tcPr()
                    for shading in list(props.findall(qn('w:shd'))):props.remove(shading)
                    if i==0:
                        cell_borders=OxmlElement('w:tcBorders')
                        rule=OxmlElement('w:bottom');rule.set(qn('w:val'),'single');rule.set(qn('w:sz'),'6');rule.set(qn('w:color'),'000000')
                        cell_borders.append(rule);props.append(cell_borders)
                    for paragraph in cell.paragraphs:
                        paragraph.paragraph_format.keep_with_next=i<len(tab.rows)-1 or bool(block.get('keep_table_with_note'))
                        paragraph.paragraph_format.line_spacing=1
                        for run in paragraph.runs:
                            run.font.size=Pt(8)
                            if i==0:run.font.bold=True
                if sized:
                    for cell,size in zip(row.cells,sizes):cell.width=Inches(6.7*size/sum(sizes))
            if block.get('table_note'):citations.add_text(doc.add_paragraph(),block['table_note'])


def title_page():
    doc=new_document()
    doc.add_heading(TITLE,0)
    add_author_block(doc)
    doc.add_paragraph('Keywords: winter wheat–summer maize rotation; irrigation management; crop modeling; GRACE; water availability; consumptive water use')
    doc.save(OUT/'title_page.docx')


def manuscript(source,destination):
    payload=json.loads(Path(source).read_text())
    blocks=payload['blocks']
    citations=Citations();citations.prepare(blocks)
    doc=new_document(payload.get('title',TITLE))
    doc.add_heading(payload.get('title',TITLE),0)
    add_author_block(doc,line_spacing=2)
    render_blocks(doc,blocks,citations)
    bibliography=citations.bibliography(doc)
    zotero_preferences(doc)
    for paragraph in doc.element.xpath('.//w:p'):
        properties=paragraph.get_or_add_pPr()
        spacing=properties.get_or_add_spacing()
        spacing.set(qn('w:line'),'480');spacing.set(qn('w:lineRule'),'auto')
    for section in doc.sections:
        numbering=section._sectPr.find(qn('w:lnNumType'))
        if numbering is None:
            numbering=OxmlElement('w:lnNumType')
            section._sectPr.insert_element_before(numbering,'w:pgNumType','w:cols','w:formProt','w:vAlign',
                'w:noEndnote','w:titlePg','w:textDirection','w:bidi','w:rtlGutter','w:docGrid','w:printerSettings','w:sectPrChange')
        numbering.set(qn('w:countBy'),'1');numbering.set(qn('w:start'),'1')
        numbering.set(qn('w:restart'),'continuous');numbering.set(qn('w:distance'),'240')
    doc.save(destination)
    return dict(citation_groups=len(citations.groups),bibliography_entries=len(bibliography))


if __name__=='__main__':
    OUT.mkdir(parents=True,exist_ok=True)
    title_page()
    print('Created title page from confirmed author information')
