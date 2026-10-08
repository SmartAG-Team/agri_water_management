"""Apply reviewed Results wording and retain source citations in Methods."""
from pathlib import Path
import json
import re

ROOT=Path(__file__).resolve().parents[1]


def uncite(text):
    return re.sub(r'\s*\[CITE:[^\]]+\]', '', text)


def clean(block):
    for key in ['paragraphs','caption','table_caption','table_note','text']:
        if key not in block:continue
        block[key]=[uncite(v) for v in block[key]] if isinstance(block[key],list) else uncite(block[key])


def apply(article,supplement):
    revision=json.loads((ROOT/'analysis_source/results_wording_20261008.json').read_text())
    for prefix,content in revision['sections'].items():
        block=next(b for b in article['blocks'] if b.get('heading','').startswith(prefix+'.'))
        block.update(heading=content['heading'],paragraphs=content['paragraphs'],level=2)
    active=False
    for block in article['blocks']:
        heading=block.get('heading','')
        if heading.startswith('3.'):active=True
        elif heading.startswith('4.'):active=False
        if active:clean(block)
    # The supplementary observation-source section supplies the field attribution.
    sources=next(b for b in supplement['blocks'] if b.get('heading','').startswith('S1.'))
    paragraph=sources['paragraphs'][2]
    target='Wuqiao 2016–2018 supplies 12 documented treatment seasons per crop for that fit;'
    assert target in paragraph
    sources['paragraphs'][2]=paragraph.replace(target,
        'Wuqiao 2016–2018 supplies 12 documented treatment seasons per crop for that fit [CITE:yang2024_precipitation];')
    active=False
    for block in supplement['blocks']:
        if block.get('heading','').startswith('S4.'):active=True
        elif active and block.get('heading'):active=False
        if active:clean(block)
    return article,supplement
