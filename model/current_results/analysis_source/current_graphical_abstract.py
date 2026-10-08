"""Minimal graphical abstract: crop model, regional allocation, grain–ET trade-off."""
from pathlib import Path
import json
import warnings

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.collections import PatchCollection, LineCollection
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.patches import Ellipse, FancyArrowPatch, PathPatch, Rectangle
from matplotlib.path import Path as PlotPath
import numpy as np
import pandas as pd
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
R = ROOT / "publication"
BLUE, GOLD, INK, GREY, OLIVE = "#25637C", "#B47D28", "#20313A", "#73828A", "#737D48"


def main():
    annual=pd.read_csv(ROOT/"regional/tables/regional_policy_annual_results.csv")
    means=annual.loc[annual.period.eq("testing")].groupby("policy").mean(numeric_only=True)
    u,t=means.loc["uniform_50pct"],means.loc["targeted_50pct"]
    np.testing.assert_allclose(u.field_irrigation_m3,t.field_irrigation_m3)
    np.testing.assert_allclose(u.field_irrigation_m3,means.loc["conventional","field_irrigation_m3"]*.5)
    cells=pd.read_csv(ROOT/"regional/tables/current_source_cell_map_values.csv")
    assert len(cells)==3641 and not cells.duplicated(["longitude","latitude"]).any()
    np.testing.assert_allclose(cells.used_area_ha.sum(),t.mapped_rotation_area_ha)
    np.testing.assert_allclose(np.average(cells.source_screened_targeted_mean_quota,
                                         weights=cells.used_area_ha),.5)
    grain=(t.grain_production_t-u.grain_production_t)/1e6
    et=(t.modeled_total_et_volume_m3-u.modeled_total_et_volume_m3)/1e9
    grain_per_ha=grain*1e6/t.mapped_rotation_area_ha
    et_mm=et*1e9/(t.mapped_rotation_area_ha*10)
    family="DejaVu Sans"
    plt.rcParams.update({"font.family":family,"svg.fonttype":"none","pdf.fonttype":42})
    fig=plt.figure(figsize=(15,6),facecolor="white")
    ax=fig.add_axes([0,0,1,1]); ax.set(xlim=(0,15),ylim=(0,6)); ax.axis("off")
    texts=[]

    def text(x,y,value,size=21,color=INK,**kw):
        item=ax.text(x,y,value,fontsize=size,color=color,va="center",**kw)
        texts.append(item)

    def arrow(x1,y1,x2,y2,color=GREY,scale=22):
        ax.add_patch(FancyArrowPatch((x1,y1),(x2,y2),arrowstyle="-|>",
                                    mutation_scale=scale,lw=2,color=color))

    def wheat(x,y,h=1.65):
        ax.plot([x,x],[y,y+h],color=OLIVE,lw=3)
        for i in range(6):
            for side in [-1,1]:
                ax.add_patch(Ellipse((x+side*.12,y+h*(.55+i*.067)),.28,.11,
                                     angle=side*42,facecolor=GOLD,edgecolor="none"))
        for side in [-1,1]:
            ax.add_patch(Ellipse((x+side*.18,y+.37),.5,.09,
                                 angle=side*30,facecolor=OLIVE,edgecolor="none"))
        for dx in [-.27,0,.27]:
            ax.plot([x,x+dx],[y,y-.3],color="#AAA291",lw=1.4)

    def maize(x,y,h=1.7):
        ax.plot([x,x],[y,y+h],color=OLIVE,lw=3)
        for level,side in [(.23,-1),(.43,1),(.63,-1),(.79,1)]:
            ax.add_patch(Ellipse((x+side*.22,y+level*h),.65,.14,
                                 angle=side*28,facecolor=OLIVE,edgecolor="none"))
        ax.add_patch(Ellipse((x+.12,y+.74),.16,.4,angle=-17,
                             facecolor=GOLD,edgecolor="none"))
        for dx in [-.18,0,.18]:
            ax.plot([x,x+dx],[y+h,y+h+.16],color=GOLD,lw=2)
            ax.plot([x,x+dx*1.5],[y,y-.3],color="#AAA291",lw=1.4)

    def drop(x,y,s=.36):
        vertices=np.array([[0,1],[-.63,.2],[-.6,-.6],[0,-.65],
                           [.6,-.6],[.63,.2],[0,1]])*s+[x,y]
        ax.add_patch(PathPatch(PlotPath(vertices,[PlotPath.MOVETO]+[PlotPath.CURVE4]*6),
                               facecolor=BLUE,edgecolor="none"))

    # Daily crop–soil modeling is depicted by original, nonquantitative vector symbols.
    wheat(.97,2.75); maize(2.41,2.75)
    arrow(1.49,3.7,1.91,3.7,scale=16)
    for bottom,color in [(2.37,"#E9E5DC"),(2.5,"#DAD5C9"),(2.63,"#C8C1B3")]:
        ax.add_patch(Rectangle((.42,bottom),2.61,.13,facecolor=color,edgecolor="none"))
    text(1.7,4.86,"Wheat–maize",size=22,ha="center")
    text(1.7,1.92,"Open Crop Model",size=23,ha="center",weight="bold")
    arrow(3.23,3.39,3.83,3.39)

    # Actual 0.1-degree rotation cells, shown in an equirectangular projection.
    # Standard parallel 36 N and central meridian 117 E preserve geographic geometry.
    radius=6378.137
    x=radius*np.radians(cells.longitude.to_numpy()-117)*np.cos(np.radians(36))
    y=radius*np.radians(cells.latitude.to_numpy())
    dx=radius*np.radians(.1)*np.cos(np.radians(36)); dy=radius*np.radians(.1)
    cmap=LinearSegmentedColormap.from_list("irrigation",["#E8F0F3",BLUE])
    for left,quotas in [(3.99,np.full(len(cells),.5)),
                         (7.22,cells.source_screened_targeted_mean_quota.to_numpy())]:
        map_ax=fig.add_axes([left/15,1.89/6,2.78/15,3.22/6])
        patches=[Rectangle((cx-dx/2,cy-dy/2),dx,dy) for cx,cy in zip(x,y)]
        collection=PatchCollection(patches,cmap=cmap,edgecolor="none",linewidth=0)
        collection.set_array(quotas); collection.set_clim(0,1)
        map_ax.add_collection(collection)
        cart=R/'source_snapshots/cartography'
        for file,color,width in [('province_boundaries_polylines.csv','#64686b',.35),('study_area_outline_exterior_polylines.csv','#242424',.55)]:
            frame=pd.read_csv(cart/file)
            segments=[g[['x_m','y_m']].to_numpy()/1000 for _,g in frame.groupby('segment_id',sort=False)]
            map_ax.add_collection(LineCollection(segments,colors=color,linewidths=width))
        map_ax.set(xlim=(radius*np.radians(112-117)*np.cos(np.radians(36)),radius*np.radians(121.8-117)*np.cos(np.radians(36))),
                   ylim=(radius*np.radians(31.8),radius*np.radians(41.05)),aspect="equal")
        map_ax.axis("off")
    text(7,5.47,"Same irrigation: −50%",size=25,ha="center")
    text(5.38,1.65,"Uniform",size=22,ha="center")
    text(8.61,1.65,"Targeted",size=22,ha="center")
    arrow(6.82,3.39,7.1,3.39,scale=18)
    text(7,1.2,"North China Plain",size=17,ha="center",color=GREY)
    # One shared color scale describes the mapped irrigation fractions.
    bar_left,bar_width,bar_y=4.75,2.1,.93
    for i in range(40):
        ax.add_patch(Rectangle((bar_left+i*bar_width/40,bar_y),bar_width/40,.11,
                               facecolor=cmap((i+.5)/40),edgecolor="none"))
    for value,label in [(0,"0"),(.5,"50"),(1,"100%")]:
        text(bar_left+value*bar_width,.76,label,size=12,ha="center",color=GREY)
    text(7.16,.98,"Irrigation fraction",size=14,color=GREY)
    arrow(10.14,3.39,10.72,3.39)

    # Positive deltas are labeled directly; icon sizes do not encode their magnitude.
    for yy in [4.1,2.52]:
        arrow(11.11,yy-.25,11.11,yy+.25,color=GOLD if yy>3 else BLUE,scale=26)
    text(11.52,4.19,f"+{grain_per_ha:.2f}",size=35,weight="bold",color=GOLD)
    text(11.53,3.68,r"t ha$^{-1}$ yr$^{-1}$ grain",size=20,color=GOLD)
    text(11.53,3.24,f"{grain:.2f} Mt yr⁻¹ region-wide",size=16,color=GREY)
    text(11.52,2.61,f"+{et_mm:.2f}",size=35,weight="bold",color=BLUE)
    text(11.53,2.1,r"mm yr$^{-1}$ ET",size=22,color=BLUE)
    text(11.53,1.66,f"{et:.2f} km³ yr⁻¹ region-wide",size=16,color=GREY)

    text(7.5,.42,"Allocation: production retention + consumption limits",size=25,ha="center",weight="bold",color=BLUE)
    text(7.5,.13,"Conditional 2014–2025 rotation means; targeted − uniform",size=13.5,ha="center",color=GREY)
    fig.canvas.draw()
    renderer=fig.canvas.get_renderer()
    for item in texts:
        box=item.get_window_extent(renderer)
        assert fig.bbox.contains(*box.p0) and fig.bbox.contains(*box.p1),item.get_text()
    labels=[t.get_text() for t in texts]
    assert not any(token in " ".join(labels) for token in ["(a)","(b)","(c)","graphical abstract"])
    stem=R/"figures/graphical_abstract"
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        for extension in ["png","pdf","svg"]:
            fig.savefig(stem.with_suffix("."+extension),dpi=300,facecolor="white")
        assert not any("Glyph" in str(w.message) for w in caught)
    with Image.open(stem.with_suffix(".png")) as png:
        png.convert("RGB").save(stem.with_suffix(".tiff"),compression="tiff_lzw",dpi=(300,300))
        for width in [1500,500]:
            preview=png.convert("RGB"); preview.thumbnail((width,int(width*.4)))
            preview.save(R/f"verification/preview_{width}px.png")
    (R/"verification/labels.json").write_text(json.dumps(labels,ensure_ascii=False,indent=2)+"\n")
    metadata={"title":None,"panel_labels":False,"grain_delta_Mt_per_year":float(grain),
              "grain_delta_t_ha_per_year":float(grain_per_ha),"ET_delta_mm_per_year":float(et_mm),
              "ET_delta_km3_per_year":float(et),"map_cells":len(cells),
              "map_area_ha":float(cells.used_area_ha.sum()),"map_targeted_area_weighted_fraction":.5,
              "projection":"Equirectangular, standard parallel 36°N, central meridian 117°E",
              "map_boundaries":"Verified Natural Earth provincial context and operational study outline",
              "icons":"Original nonquantitative vector symbols", "font":family,
              "comparison":"2014–2025 mean, targeted minus uniform, same 50% field-irrigation reduction",
              "scope":"Conditional model scenarios; groundwater recovery is not quantified",
              "visible_word_count":len(" ".join(labels).split())}
    (R/"verification/figure_evidence.json").write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+"\n")
    plt.close(fig)
    print(json.dumps(metadata,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
