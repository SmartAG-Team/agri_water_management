"""Check text geometry in current main and supplementary figure renderers."""
from pathlib import Path
import json
import itertools
import hashlib
import math

import matplotlib.pyplot as plt

import revise_manuscript_figures as primary
import remove_panel_titles as supplement

ROOT=Path(__file__).resolve().parents[1]
PUB=ROOT/'publication'
RECORDS=[]


def check(fig,relative):
    fig.canvas.draw();fig.canvas.draw()
    renderer=fig.canvas.get_renderer()
    texts=[]
    for ai,ax in enumerate(fig.axes):
        objects=[('x axis label',ax.xaxis.label),('y axis label',ax.yaxis.label),
                 ('panel letter',ax.title),('panel letter',ax._left_title),('panel letter',ax._right_title)]
        # Matplotlib keeps visible=True on tick objects outside the view interval,
        # but Axis.draw does not paint those objects.
        objects += [('x tick',t) for x,t in zip(ax.get_xticks(),ax.get_xticklabels())
                    if min(ax.get_xlim())<=x<=max(ax.get_xlim())]
        objects += [('y tick',t) for y,t in zip(ax.get_yticks(),ax.get_yticklabels())
                    if min(ax.get_ylim())<=y<=max(ax.get_ylim())]
        objects += [('annotation',t) for t in ax.texts]
        legend=ax.get_legend()
        if legend:objects += [('legend',t) for t in legend.get_texts()]
        for kind,t in objects:
            if not t.get_visible() or not t.get_text().strip():continue
            box=t.get_window_extent(renderer)
            if not all(math.isfinite(x) for x in box.extents) or box.width<1 or box.height<1:continue
            texts.append(dict(axis=ai,kind=kind,text=t.get_text(),box=box))
    for legend in fig.legends:
        for t in legend.get_texts():texts.append(dict(axis=None,kind='legend',text=t.get_text(),box=t.get_window_extent(renderer)))
    overlaps=[]
    for a,b in itertools.combinations(texts,2):
        dx=min(a['box'].x1,b['box'].x1)-max(a['box'].x0,b['box'].x0)
        dy=min(a['box'].y1,b['box'].y1)-max(a['box'].y0,b['box'].y0)
        if dx>.8 and dy>.8:
            overlaps.append({'first':{k:v for k,v in a.items() if k!='box'},
                             'second':{k:v for k,v in b.items() if k!='box'},
                             'overlap_pixels':[float(dx),float(dy)]})
    RECORDS.append(dict(figure=relative+'.png',text_count=len(texts),overlap_count=len(overlaps),overlaps=overlaps))
    plt.close(fig)


def main():
    primary.save=lambda fig,relative,caption,sources,svg=False:check(fig,relative)
    for function in [primary.workflow,primary.domain,primary.partitions,primary.seasonal,
                     primary.field_scatter,primary.policies,primary.spatial,primary.adaptive]:function()
    # Use the same supplementary plotting blocks, stripping only panel headings.
    original=supplement.export
    def capture(fig,relative,caption,sources):
        for ax in fig.axes:
            for loc in ['left','center','right']:
                title=ax.get_title(loc=loc)
                if title:ax.set_title(title.split(')')[0]+')',loc=loc,fontweight='bold')
            for text in ax.texts:
                if text.get_text().startswith('('):text.set_text(text.get_text().split(')')[0]+')')
        check(fig,relative)
    supplement.export=capture
    for function in [supplement.field_grain,supplement.soil_storage,
                     supplement.raw_harvest,supplement.station_harvest]:function('')
    import pandas as pd
    d=pd.read_csv(ROOT/'regional/tables/full_quota_spatial_metrics.csv')
    with plt.rc_context({'font.size':8,'axes.labelsize':8,'xtick.labelsize':8,'ytick.labelsize':8}):
        fig,axes=plt.subplots(1,2,figsize=(7.2,3),layout='constrained')
        for crop,colour,marker in [('wheat','#2166ac','o'),('maize','#d6604d','s')]:
            for ax,column,scale in [(axes[0],'yield_kg_ha',1000),(axes[1],'et_mm',1)]:
                q=d[d.crop.eq(crop)&d.variable.eq(column)]
                ax.plot(q.fraction*100,q.rmse/scale,color=colour,marker=marker,label=crop.title())
                ax.set(xlabel='Conventional irrigation retained (%)',xticks=[0,25,50,75,100],ylim=(0,None))
        axes[0].set_ylabel('Grain aggregation RMSE (t ha⁻¹)');axes[1].set_ylabel('ET aggregation RMSE (mm)')
        axes[0].legend(frameon=False)
        check(fig,'figures/closed_axes/all_quota_spatial_errors')
    assert len(RECORDS)==19
    out=PUB/'verification/figure_text_geometry_20261008.json'
    out.write_text(json.dumps({'figures':RECORDS,'figures_checked':19,
        'automatic_overlap_candidates':sum(x['overlap_count'] for x in RECORDS)},ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({x['figure']:x['overlaps'] for x in RECORDS if x['overlaps']},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
