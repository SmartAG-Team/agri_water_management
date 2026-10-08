"""Original vector schematic of the selected Open Crop Model processes."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import re

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, PathPatch, Ellipse, Rectangle
from matplotlib.path import Path as PlotPath
import pymupdf

ROOT = Path(__file__).resolve().parents[1]
PUB = ROOT / 'publication'
FIGURE = 'figures/current_Figure_3_OCM_mechanism.png'
CAPTION = (
    'Figure 3. Crop growth and soil-water coupling in Open Crop Model. '
    'Gold denotes photosynthetically active radiation (PAR) input; '
    'blue solid arrows denote water fluxes; green solid arrows denote dry-matter '
    'transfers; dashed arrows denote process controls and feedbacks. Nutrition '
    'is prescribed, the lower boundary uses free drainage, and soil water carries '
    'through crop/fallow segments. Plant and soil sketches are not to scale.'
)
SOURCE_FILES = [
    'calibration/source_snapshots/native/research/ncp_irrigation/model.py',
    'calibration/source_snapshots/native/research/ncp_irrigation/growth.py',
    'calibration/source_snapshots/native/research/ncp_irrigation/stage_canopy.py',
    'calibration/source_snapshots/native/core/hydrology/balance.py',
    'calibration/parameters/frozen_model.json',
]
BLUE = '#256b99'
GREEN = '#426d51'
GREY = '#59646c'
INK = '#18242c'
GOLD = '#b98729'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def figure_references(text):
    """Shift existing main-figure references; supplementary labels retain their IDs."""
    def replacement(match):
        start, end = int(match.group(2)), match.group(4)
        changed = start + (start >= 3)
        tail = '' if end is None else match.group(3) + str(int(end) + (int(end) >= 3))
        return match.group(1) + str(changed) + tail
    return re.sub(r'(Figures?\s+)(\d+)(?:([–-])(\d+))?', replacement, text)


def original_figure_references(text):
    def replacement(match):
        number = int(match.group(2))
        tail = '' if match.group(4) is None else match.group(3) + str(
            int(match.group(4)) - (int(match.group(4)) >= 4))
        return match.group(1) + str(number - (number >= 4)) + tail
    return re.sub(r'(Figures?\s+)(\d+)(?:([–-])(\d+))?', replacement, text)


def apply_documents(article, supplement):
    existing = next((b for b in article['blocks'] if b.get('figure') == FIGURE), None)
    if existing is not None:
        number=re.match(r'Figure (\d+)\.',existing['caption']).group(1)
        existing['caption'] = re.sub(r'^Figure \d+\.',f'Figure {number}.',CAPTION)
        return article, supplement
    for document in [article, supplement]:
        for block in document['blocks']:
            for key in ['caption', 'table_caption', 'table_note']:
                if isinstance(block.get(key), str):
                    block[key] = figure_references(block[key])
            if 'paragraphs' in block:
                block['paragraphs'] = [figure_references(p) for p in block['paragraphs']]
    index = next(i for i, b in enumerate(article['blocks'])
                 if b.get('heading') == '2.4. Crop model description')
    paragraph = article['blocks'][index]['paragraphs'][0]
    paragraph = paragraph.replace('at a daily time step.', 'at a daily time step (Figure 3).', 1)
    article['blocks'][index]['paragraphs'][0] = paragraph
    article['blocks'].insert(index + 1, {'figure': FIGURE, 'caption': CAPTION, 'paragraphs': []})
    return article, supplement


def registration():
    file = PUB / FIGURE
    with pymupdf.open(file.with_suffix('.pdf')) as pdf:
        text = '\n'.join(p.get_text() for p in pdf)
    return dict(figure=FIGURE, caption=publication_caption(), panel_labels=[],
                descriptive_panel_titles=False, process_labels=True,
                source_size_inches=[10.8, 8.3], minimum_authored_text_points=13.3,
                source_sha256={rel: sha(ROOT / rel) for rel in SOURCE_FILES},
                png_sha256=sha(file), pdf_sha256=sha(file.with_suffix('.pdf')),
                pdf_text_sha256=hashlib.sha256(text.encode()).hexdigest())


def publication_caption():
    source=PUB/'analysis_source/manuscript_blocks.json'
    if source.exists():
        document=json.loads(source.read_text())
        for block in document['blocks']:
            if block.get('figure')==FIGURE:
                return block['caption']
    return CAPTION


def register_publication():
    record = registration()
    captions = {}
    for stem in ['manuscript', 'supplementary']:
        document = json.loads((PUB / f'analysis_source/{stem}_blocks.json').read_text())
        captions.update({b['figure']: b['caption'] for b in document['blocks'] if 'figure' in b})
    for name in ['visual_revision_20261008.json', 'panel_title_removal_20261008.json']:
        path = PUB / 'verification' / name
        value = json.loads(path.read_text())
        rows = [r for r in value['figures'] if r['figure'] != FIGURE] + [record.copy()]
        for row in rows:
            if row['figure'] in captions:
                row['caption'] = captions[row['figure']]
        value['figures'] = rows
        if name.startswith('visual'):
            value['main_figures_revised'] = 9
        else:
            value['main_figures'] = 9
        value['model_mechanism_added'] = True
        value['main_figure_reference_numbering'] = 9
        article=json.loads((PUB/'analysis_source/manuscript_blocks.json').read_text())
        if article.get('main_figure_order'):
            value['main_figure_order']=article['main_figure_order']
        path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def main():
    (PUB / FIGURE).parent.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'pdf.fonttype': 42,
                         'svg.fonttype': 'none', 'savefig.facecolor': 'white'})
    fig = plt.figure(figsize=(10.8, 8.3))
    ax = fig.add_axes([.01, .012, .98, .976])
    ax.set(xlim=(0, 1), ylim=(0, 1)); ax.axis('off')
    texts = []
    connectors = []
    containers = []

    def label(x, y, text, size=13.3, colour=INK, axes=None, **kwargs):
        owner=ax if axes is None else axes
        item = owner.text(x, y, text, fontsize=size, color=colour,
                       ha=kwargs.pop('ha', 'center'), va=kwargs.pop('va', 'center'),
                       linespacing=1.18, zorder=8, **kwargs)
        texts.append(item)
        return item

    def box(x, y, w, h, title, detail, colour=GREEN, axes=None):
        owner=ax if axes is None else axes
        owner.add_patch(FancyBboxPatch((x, y), w, h,
            boxstyle='round,pad=.005,rounding_size=.009',
            facecolor='#f5f8f6' if colour == GREEN else '#f1f6fa',
            edgecolor=colour, linewidth=1.05, zorder=5))
        title_text=label(x+w/2, y+h*.80, title, size=14.2, fontweight='bold',axes=owner)
        detail_text=label(x+w/2, y+h*.34, detail,axes=owner)
        containers.append((title,(x,y,w,h),[title_text,detail_text],owner))

    def arrow(points, colour=GREY, dashed=False, width=1.3, axes=None):
        owner=ax if axes is None else axes
        connectors.append((points,colour,dashed,owner))
        codes = [PlotPath.MOVETO] + [PlotPath.LINETO] * (len(points)-1)
        owner.add_patch(FancyArrowPatch(path=PlotPath(points, codes),
            arrowstyle='-|>', mutation_scale=13, linewidth=width,
            color=colour, linestyle=(0, (4, 3)) if dashed else '-', zorder=4))

    # Atmospheric forcing and explicit management.
    box(.025, .905, .595, .083, 'Weather',
        'Temperature · radiation · rainfall · reference ET', GREY)
    box(.68, .883, .295, .105, 'Management',
        'Sowing and harvest dates\nIrrigation events', GREY)

    # Crop controls, expressed as processes rather than software modules.
    box(.025, .70, .255, .17, 'Crop development',
        'Soil water → germination\nWheat: temperature,\nvernalization, photoperiod\nMaize: thermal time')
    box(.025, .535, .255, .135, 'Canopy dynamics',
        'Stage-specific leaf area\nLeaf growth / senescence\nLAI and canopy cover')
    box(.025, .325, .255, .14, 'Evaporative demand',
        'Canopy cover partitions ET₀\nTranspiration demand\nSoil evaporation demand', BLUE)
    box(.025, .13, .255, .14, 'Root water uptake',
        'Root depth and distribution\nLayer water availability\nCompensatory uptake', BLUE)
    box(.68, .735, .295, .135, 'Biomass production',
        'Intercepted PAR × RUE\nTemperature · nutrition\nTranspiration supply / demand')
    box(.68, .535, .295, .135, 'Dry-matter allocation',
        'Leaves · stems · grain\nLeaf cohorts retain birth SLA\nSenesced leaf dry matter')
    box(.68, .325, .295, .14, 'Grain formation',
        'Biomass at flowering / silking\nGrain number × fixed grain mass\nAssimilates + wheat reserves')
    box(.68, .13, .295, .14, 'Water-stress feedback',
        'Realized transpiration supply\nrelative to demand\nGrowth and leaf responses', BLUE)

    # Aboveground plant and belowground layered profile.
    soil_left, soil_right, surface, bottom = .335, .625, .48, .145
    layer_edges = [surface, .435, .385, .33, .275, .215, bottom]
    for i, (top, low) in enumerate(zip(layer_edges, layer_edges[1:])):
        ax.add_patch(Rectangle((soil_left, low), soil_right-soil_left, top-low,
            facecolor=['#f0ede5', '#e8e3d8', '#e1dacc', '#d8d0c1', '#d0c7b6', '#c7beac'][i],
            edgecolor='white', linewidth=1.2, zorder=1))
    ax.plot([soil_left, soil_right], [surface, surface], color='#7a7161', lw=1.2)
    label(.57, .45, 'Infiltration', size=13.3)
    label(.535, .295, 'Vertical\nredistribution', size=13.3,
          bbox={'facecolor':'#d8d0c1','edgecolor':'none','pad':1.2})
    label(.400, .127, 'Free drainage', size=13.3)

    ax.plot([.454, .461, .465, .466], [.48, .62, .74, .83],
            color=GREEN, linewidth=2.5, zorder=3)
    for origin, tip, shoulder in [((.46,.59),(.372,.647),(.384,.60)),
                                ((.46,.65),(.547,.706),(.527,.655)),
                                ((.465,.71),(.385,.777),(.395,.725)),
                                ((.465,.756),(.538,.817),(.522,.763))]:
        verts=[origin, shoulder, tip, origin]
        patch=PathPatch(PlotPath(verts,[PlotPath.MOVETO,PlotPath.CURVE3,
                       PlotPath.CURVE3,PlotPath.CLOSEPOLY]),
                       facecolor='#709367',edgecolor=GREEN,lw=.8,zorder=3)
        ax.add_patch(patch)
    # Grain-bearing ear and root branches are conceptual organ outlines.
    ax.plot([.466,.471],[.815,.866],color=GOLD,lw=1.6,zorder=3)
    for y in [.827,.839,.85]:
        for sign in [-1,1]:
            ax.add_patch(Ellipse((.469+sign*.011,y),.021,.012,
                angle=sign*40,facecolor='#dbbd72',edgecolor=GOLD,lw=.65,zorder=3))
    for tip in [(.374,.40),(.40,.325),(.362,.235),(.435,.18),(.53,.35),(.548,.25),(.516,.17)]:
        ax.plot([.454,.454+(tip[0]-.454)*.33,tip[0]],
                [surface,surface+(tip[1]-surface)*.6,tip[1]],
                color=GREEN,lw=1.0,zorder=3)

    # Solid flux arrows, separated from state and process controls.
    # Incoming PAR reaches the upper-right leaf; rainfall reaches its tip.
    arrow([(.475,.901),(.512,.795)], GOLD)
    label(.514,.864,'PAR',colour=GOLD)
    arrow([(.594,.901),(.538,.817)],BLUE)
    label(.610,.850,'Rainfall',colour=BLUE)
    arrow([(.539,.805),(.615,.777),(.615,.480)],BLUE)
    label(.642,.600,'Throughfall',colour=BLUE,rotation=90)
    arrow([(.696,.878),(.658,.860),(.658,.510),(.604,.510),(.604,.480)],BLUE)
    label(.691,.510,'Irrigation',colour=BLUE,ha='left')
    arrow([(.571,.424),(.571,.366)],BLUE)
    arrow([(.608,.335),(.608,.22)],BLUE)
    arrow([(.618,.235),(.618,.33)],BLUE)
    arrow([(.48,.176),(.48,.105)],BLUE)
    arrow([(.399,.478),(.399,.547)],BLUE)
    label(.373,.583,'Soil\nevaporation',colour=BLUE)
    arrow([(.405,.755),(.419,.878)],BLUE)
    label(.340,.836,'Canopy\nevaporation',colour=BLUE)
    arrow([(.486,.33),(.495,.66),(.571,.76)],BLUE)
    label(.561,.583,'Transpiration',colour=BLUE,rotation=90)
    arrow([(.335,.457),(.298,.457)],BLUE)
    label(.32,.495,'Runoff',colour=BLUE,ha='right')

    # Carbon production and controls share daily canopy and water states.
    arrow([(.53,.792),(.671,.792)],GREEN)
    arrow([(.828,.727),(.828,.679)],GREEN)
    arrow([(.828,.527),(.828,.475)],GREEN)
    arrow([(.154,.898),(.154,.879)],GREY,True)
    arrow([(.154,.693),(.154,.68)],GREY,True)
    arrow([(.154,.527),(.154,.475)],GREY,True)
    arrow([(.289,.397),(.31,.397),(.31,.372),(.333,.372)],BLUE,True)
    arrow([(.454,.315),(.323,.315),(.323,.202),(.289,.202)],BLUE)
    arrow([(.289,.172),(.313,.172),(.313,.104),(.651,.104),(.651,.202),(.671,.202)],BLUE,True)
    arrow([(.982,.208),(.989,.208),(.989,.708),(.930,.708),(.930,.729)],BLUE,True)
    arrow([(.674,.172),(.670,.172),(.670,.710),(.304,.710),(.304,.602),(.289,.602)],BLUE,True)

    label(.22,.086,'Daily outputs',fontweight='bold')
    label(.22,.043,'LAI · biomass · grain · ET\nSoil water · runoff · drainage')
    label(.66,.080,'Soil-water carry-over',fontweight='bold',colour=BLUE)
    label(.66,.046,'Next day → next crop / fallow',colour=BLUE)
    arrow([(.618,.159),(.667,.159),(.667,.100)],BLUE,True)

    fig.canvas.draw()
    renderer=fig.canvas.get_renderer()
    padding=4*fig.dpi/72
    for title,(x,y,w,h),items,owner in containers:
        _,bottom=owner.transData.transform((x,y))
        _,top=owner.transData.transform((x+w,y+h))
        for text,is_title in zip(items,[True,False]):
            bounds=text.get_window_extent(renderer)
            target=(top-padding-bounds.height/2 if is_title else
                    bottom+padding+bounds.height/2)
            point=owner.transData.transform(text.get_position())
            point[1]+=target-(bounds.y0+bounds.y1)/2
            text.set_position(owner.transData.inverted().transform(point))
    fig.canvas.draw()
    boxes=[t.get_window_extent(fig.canvas.get_renderer()) for t in texts]
    overlaps=[]
    for i,a in enumerate(boxes):
        for j,b in enumerate(boxes[i+1:],i+1):
            if a.overlaps(b):overlaps.append([texts[i].get_text(),texts[j].get_text()])
    box_overflows=[]
    for title,(x,y,w,h),items,owner in containers:
        left,bottom=owner.transData.transform((x,y))
        right,top=owner.transData.transform((x+w,y+h))
        for text in items:
            bounds=text.get_window_extent(fig.canvas.get_renderer())
            if not(left+3<=bounds.x0 and bounds.x1<=right-3 and
                   bottom+3<=bounds.y0 and bounds.y1<=top-3):
                box_overflows.append({'box':title,'text':text.get_text(),
                    'margins_pixels':[bounds.x0-left,right-bounds.x1,
                                      bounds.y0-bottom,top-bounds.y1]})
    arrow_text_collisions=[]
    for points,colour,dashed,owner in connectors:
        for start,end in zip(points,points[1:]):
            segment=PlotPath(owner.transData.transform([start,end]))
            for text,bounds in zip(texts,boxes):
                if segment.intersects_bbox(bounds.padded(1.0),filled=False):
                    arrow_text_collisions.append({'text':text.get_text(),'start':start,'end':end})
    stem=PUB/FIGURE
    for ext in ['png','pdf','svg']:
        fig.savefig(stem.with_suffix('.'+ext),dpi=600)
    stem.with_name(stem.stem+'_caption.txt').write_text(publication_caption()+'\n')
    receipt=registration()
    receipt.update(generated_at_utc=datetime.now(timezone.utc).isoformat(),
        renderer_sha256=sha(Path(__file__)),text_count=len(texts),
        text_overlap_candidates=overlaps,original_vector_artwork=True,
        box_text_overflow_candidates=box_overflows,
        arrow_text_collision_candidates=arrow_text_collisions,
        arrow_count=len(connectors),
        soil_and_plant_drawing_not_to_scale=True)
    (PUB/'verification/model_mechanism_geometry_20261008.json').write_text(
        json.dumps(receipt,indent=2,ensure_ascii=False)+'\n')
    plt.close(fig)
    print('Open Crop Model mechanism:',stem,'; text overlap candidates:',len(overlaps))


if __name__ == '__main__':
    main()
