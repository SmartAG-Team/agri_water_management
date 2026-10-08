"""Local Quick Look + isolated Chromium reading-copy rendering.

Microsoft Word AppleEvents timed out and Pages did not import the document.
These PDFs verify full exported content; they are not Word pagination proofs.
"""
from pathlib import Path
import json,re,subprocess,tempfile,shutil,time
import pymupdf as fitz

P=Path(__file__).resolve().parents[1]
DOC=P/'documents';OUT=P/'verification/document_previews'
CHROME='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'

def main(document_names=None):
    OUT.mkdir(parents=True,exist_ok=True);receipts=[]
    names=['title_page','cover_letter','highlights','manuscript','supplementary_material','manuscript_package']
    for name in (names if document_names is None else document_names):
        source=DOC/(name+'.docx')
        subprocess.run(['qlmanage','-p','-o',str(OUT),str(source)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=55)
        preview=OUT/(name+'.docx.qlpreview')/'Preview.html'
        html=preview.read_text()
        # Quick Look emits point-valued, unitless numeric CSS dimensions.
        # Convert these to explicit points for standards-mode print rendering.
        html=re.sub(r'((?:font-size|width|min-height|padding-(?:left|right|top|bottom)|margin-(?:left|right|top|bottom)|text-indent):\s*)(-?\d+(?:\.\d+)?)(\s*[;}])',r'\1\2pt\3',html)
        css='''<style>@page{size:letter;margin:0.7in}body{margin:0!important;font-family:"Times New Roman"}body>div{width:auto!important;padding:0!important;min-height:0!important}img{max-width:100%!important;height:auto!important}table{width:100%!important;page-break-inside:avoid}tr{page-break-inside:avoid}p:has(+table){break-after:avoid}p:has(img){break-inside:avoid;break-after:avoid}p{orphans:3;widows:3}h1,h2,h3{break-after:avoid}</style>'''
        numbered=name in ['manuscript','supplementary_material','manuscript_package']
        if numbered:css+='<style>p,p span{line-height:2!important}</style>'
        if numbered:css+='<style>table{border-collapse:collapse}table tr:first-child>td{border-bottom:.75pt solid #000!important}</style>'
        html=html.replace('</head>',css+'</head>')
        # Keep table captions with their tables. Quick Look inserts style nodes
        # between these elements, so CSS adjacent-sibling selectors miss them.
        from lxml import html as html_parser,etree
        tree=html_parser.fromstring(html)
        from inline_equations import inject_reading_copy_math
        equation_count=inject_reading_copy_math(tree,source)
        for table in tree.xpath('//table'):
            caption=table.getprevious();parent=table.getparent()
            while caption is not None and caption.tag=='style':caption=caption.getprevious()
            if caption is None or not re.match(r'Table S?\d+\.',caption.text_content().strip()):continue
            elements=list(parent)[parent.index(caption):parent.index(table)+1]
            wrapper=etree.Element('div');wrapper.set('style','break-inside:avoid;page-break-inside:avoid')
            parent.insert(parent.index(caption),wrapper)
            for element in elements:wrapper.append(element)
        html=html_parser.tostring(tree,encoding='unicode',method='html')
        rendering=preview.with_name('print_preview.html');rendering.write_text(html)
        scratch=tempfile.mkdtemp(prefix='ncp-word-render-')
        try:
            logfile=OUT/(name+'_chromium.log')
            destination=DOC/(name+'.pdf');started=time.time()
            with logfile.open('w') as log:
                process=subprocess.Popen([CHROME,'--headless','--disable-gpu','--no-first-run','--no-default-browser-check',
                    '--disable-background-networking','--disable-component-update','--disable-extensions','--disable-sync',
                    '--user-data-dir='+scratch,'--no-pdf-header-footer','--print-to-pdf='+str(DOC/(name+'.pdf')),
                    rendering.as_uri()],stdout=log,stderr=log,start_new_session=True)
                ready=False
                try:
                    while time.time()-started<55:
                        if destination.exists() and destination.stat().st_mtime>=started:
                            try:
                                with fitz.open(destination) as probe:ready=len(probe)>0
                            except Exception:ready=False
                            if ready:break
                        if process.poll() is not None:break
                        time.sleep(.2)
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:process.wait(timeout=3)
                        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=3)
            if not ready:raise RuntimeError(f'Chromium produced no valid PDF:{name}')
        finally:shutil.rmtree(scratch,ignore_errors=True)
        if numbered:
            # Quick Look does not display Word line-number fields. Number the
            # actual reading-copy text baselines without implying Word pagination.
            with fitz.open(destination) as numbered_pdf:
                count=1
                for page in numbered_pdf:
                    positions={}
                    for block in page.get_text('dict')['blocks']:
                        for line in block.get('lines',[]):
                            # Fraction and script baselines belong to their prose
                            # line; separate numbers would collide in the margin.
                            spans=[s for s in line['spans'] if s['text'].strip() and
                                   s['size']>=8.5 and any(f in s['font'] for f in ['Times','Arial','Helvetica'])]
                            if not spans:continue
                            y=min(s['origin'][1] for s in spans)
                            if not 45<y<page.rect.height-45:continue
                            key=round(y)
                            positions[key]=min(positions.get(key,9999),min(s['bbox'][0] for s in spans))
                    for y,left in sorted(positions.items()):
                        label=str(count);width=fitz.get_text_length(label,fontname='helv',fontsize=7)
                        page.insert_text((43-width,y),label,fontsize=7,fontname='helv',color=(.45,.45,.45));count+=1
                temporary=destination.with_name(name+'_numbered.pdf');numbered_pdf.save(temporary)
            temporary.replace(destination)
        with fitz.open(DOC/(name+'.pdf')) as pdf:
            pages=len(pdf);text='\n'.join(p.get_text() for p in pdf)
            if len(text)<100:raise ValueError('Empty reading copy')
            receipts.append(dict(document=name,pages=pages,text_characters=len(text),
                native_equations_rendered=equation_count,
                renderer='macOS Quick Look HTML + isolated headless Chromium',native_word_pagination_verified=False,
                double_spaced=numbered,continuous_reading_copy_line_numbers=numbered,
                line_number_scope='Rendered prose/caption baselines; math scripts do not receive separate numbers' if numbered else None))
        print(name,pages,'pages',flush=True)
    receipt_path=P/'verification/document_render_receipt.json'
    if document_names is not None and receipt_path.exists():
        previous={item['document']:item for item in json.loads(receipt_path.read_text())['documents']}
        previous.update({item['document']:item for item in receipts})
        receipts=[previous[name] for name in names if name in previous]
    receipt_path.write_text(json.dumps(dict(
        documents=receipts,word_appleevent='timed out; no source document modified',
        pages_import='returned missing document; no source document modified',
        source_docx_fields_preserved=True,pdf_role='reading copies; Word pagination unavailable'),indent=2))

if __name__=='__main__':main()
