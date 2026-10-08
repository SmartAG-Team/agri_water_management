"""Render the 2024 CCD figure with complete, sharply clipped provincial outlines.

Uses all six original province rasters, not the previous NCP display-box crop.
Province paths clip the display only; analytical native-grid masks are unchanged.
"""
from pathlib import Path
from collections import Counter
import hashlib
import json
import math
import shutil
import xml.etree.ElementTree as ET
import numpy as np
import rasterio
from rasterio.windows import Window
import shapefile
from shapely.geometry import shape, mapping
from shapely.geometry.polygon import orient
from shapely.ops import unary_union
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.path import Path as MplPath
from matplotlib.patches import PathPatch
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.lines import Line2D
from matplotlib import patheffects
from prepare_monitoring_wells import prepare as prepare_wells

ROOT = Path(__file__).resolve().parents[1]
PROVINCES = ('Hebei', 'Henan', 'Shandong', 'Tianjin', 'Anhui', 'Jiangsu')
FACTOR = 32

def polygon_path(geometry):
    polygons = list(geometry.geoms) if geometry.geom_type == 'MultiPolygon' else [geometry]
    paths = []
    for polygon in polygons:
        polygon = orient(polygon, sign=1.0)
        for ring in [polygon.exterior, *polygon.interiors]:
            vertices = np.asarray(ring.coords)
            codes = np.full(len(vertices), MplPath.LINETO, dtype='uint8')
            codes[0] = MplPath.MOVETO
            codes[-1] = MplPath.CLOSEPOLY
            paths.append(MplPath(vertices, codes))
    return MplPath.make_compound_path(*paths)

def overview(province):
    source = ROOT/'raw/ccd_30m_2020_2024'/province/f'China-{province}-crops-2024-WGS84.tif'
    folder = ROOT/'derived/ccd_full_provinces_2024'
    folder.mkdir(exist_ok=True, parents=True)
    target = folder/f'{province}_mapped_rotation_fraction_2024.tif'
    meta = target.with_suffix('.json')
    if target.exists() and meta.exists():
        return target, json.loads(meta.read_text())
    count = Counter()
    with rasterio.open(source) as src:
        h, w = math.ceil(src.height/FACTOR), math.ceil(src.width/FACTOR)
        fraction = np.zeros((h,w), dtype='float32')
        aggregate_total = 0
        for start in range(0,src.height,FACTOR*32):
            nh = min(FACTOR*32,src.height-start)
            a = src.read(1,window=Window(0,start,src.width,nh))
            assert np.isin(a, [0,1,2,3,4,5,6,9]).all()
            binary = (a==9).astype('uint8')
            detected = int(binary.sum())
            count['rotation_pixels'] += detected
            count['all_grid_positions'] += a.size
            sums = np.add.reduceat(np.add.reduceat(binary,np.arange(0,nh,FACTOR),axis=0),
                np.arange(0,src.width,FACTOR),axis=1)
            aggregate_total += int(sums.sum())
            denom = np.minimum(FACTOR,nh-np.arange(0,nh,FACTOR))[:,None] * np.minimum(
                FACTOR,src.width-np.arange(0,src.width,FACTOR))[None,:]
            fraction[start//FACTOR:start//FACTOR+sums.shape[0]] = sums/denom
        assert aggregate_total == count['rotation_pixels']
        assert np.isfinite(fraction).all() and fraction.min()>=0 and fraction.max()<=1
        with rasterio.open(target,'w',driver='GTiff',width=w,height=h,count=1,dtype='float32',
                crs=src.crs,transform=src.transform*rasterio.Affine.scale(FACTOR,FACTOR),
                compress='deflate',tiled=True,blockxsize=256,blockysize=256) as dst:
            dst.write(fraction,1)
            dst.update_tags(source=str(source.relative_to(ROOT)),source_doi='10.57760/sciencedb.32361',
                definition='Count of source class 9 / all native grid positions per display cell',
                factor=FACTOR,scope='Complete source province tile; administrative clipping occurs only in figure')
        report={'province':province,'source':str(source.relative_to(ROOT)),
            'source_shape':list(src.shape),'source_bounds':list(src.bounds),
            'aggregation_factor':FACTOR,'output_shape':[h,w],**dict(count),
            'aggregation_count_reconciled':True}
    meta.write_text(json.dumps(report,indent=2))
    print('Aggregated complete province:',province,flush=True)
    return target, report

def main():
    reader=shapefile.Reader(str(ROOT/'extracted/natural_earth_10m/ne_10m_admin_1_states_provinces.shp'))
    boundaries={}
    for sr in reader.iterShapeRecords():
        p=sr.record.as_dict()
        if p['adm0_a3']=='CHN' and p['name'] in PROVINCES:
            boundaries[p['name']]=shape(sr.shape.__geo_interface__)
    assert set(boundaries)==set(PROVINCES)
    union=unary_union(list(boundaries.values()))
    xmin,ymin,xmax,ymax=union.bounds
    limits=(xmin-.45,ymin-.4,xmax+.45,ymax+.4)
    features=[{'type':'Feature','geometry':mapping(boundaries[n]),
        'properties':{'name':n,'source':'Natural Earth admin-1 1:10m v5.1.1',
            'purpose':'Complete cartographic clipping boundary; not a cadastral mask'}} for n in PROVINCES]
    (ROOT/'derived/ccd_full_provinces_2024').mkdir(exist_ok=True,parents=True)
    (ROOT/'derived/ccd_full_provinces_2024/province_display_boundaries.geojson').write_text(
        json.dumps({'type':'FeatureCollection','features':features}))
    wells,well_summary=prepare_wells()
    assert wells.longitude.between(limits[0],limits[2]).all()
    assert wells.latitude.between(limits[1],limits[3]).all()

    plt.rcParams.update({
        'font.family':'Arial','font.size':8,'text.color':'#252525',
        'axes.labelcolor':'#252525','xtick.color':'#454545','ytick.color':'#454545',
        'pdf.fonttype':42,'ps.fonttype':42,'svg.fonttype':'none',
        'axes.linewidth':.45,'savefig.facecolor':'white',
    })
    # Physical size fixes typography and line weights in the publication exports.
    fig=plt.figure(figsize=(180/25.4,220/25.4),facecolor='white')
    ax=fig.add_axes([.085,.052,.905,.935],facecolor='white')
    background='#f4f5f1'
    cmap=LinearSegmentedColormap.from_list('rotation',
        [background,'#d3e2cd','#a5c99c','#75aa72','#417f48','#205b32'])
    reports=[]
    for name in PROVINCES:
        path,report=overview(name);reports.append(report)
        polygon=polygon_path(boundaries[name])
        patch=PathPatch(polygon,facecolor=background,edgecolor='none',zorder=1)
        ax.add_patch(patch)
        with rasterio.open(path) as src:
            a=src.read(1);b=src.bounds
        image=ax.imshow(np.ma.masked_where(a<=0,a),extent=[b.left,b.right,b.bottom,b.top],
            origin='upper',cmap=cmap,vmin=0,vmax=1,interpolation='nearest',zorder=2)
        image.set_clip_path(patch)
    # Draw outlines above every raster so adjacent province layers cannot cover them.
    for name in PROVINCES:
        ax.add_patch(PathPatch(polygon_path(boundaries[name]),facecolor='none',
            edgecolor='#606660',linewidth=.45,zorder=4,joinstyle='round'))
    ax.add_patch(PathPatch(polygon_path(union),facecolor='none',edgecolor='#353b35',
        linewidth=.65,zorder=5,joinstyle='round'))

    # The historic well network also includes Beijing, where no CCD province
    # tile was downloaded. Points retain their reported geographic positions.
    well_styles={
        'Unconfined':{'marker':'o','s':5.5,'facecolors':'none','edgecolors':'#2876a5','linewidths':.35},
        'Confined':{'marker':'^','s':6.5,'facecolors':'#c36c2d','edgecolors':'white','linewidths':.12},
        'Unspecified':{'marker':'x','s':8,'color':'#656565','linewidths':.45},
    }
    plotted_counts={}
    for aquifer,style in well_styles.items():
        subset=wells.loc[wells.aquifer_class==aquifer]
        marks=ax.scatter(subset.longitude,subset.latitude,zorder=5.5,**style)
        plotted_counts[aquifer]=len(marks.get_offsets())
    assert plotted_counts==well_summary['plotted_type_counts']

    halo=[patheffects.withStroke(linewidth=2.2,foreground='white',alpha=.95)]
    labels={'Hebei':(115.25,41.15),'Henan':(112.35,33.5),'Shandong':(120.25,36.7),
        'Anhui':(117.25,30.75),'Jiangsu':(120.0,32.65)}
    for name,(x,y) in labels.items():
        ax.text(x,y,name,ha='center',color='#414841',fontsize=9,zorder=6,
            path_effects=halo)
    ax.annotate('Tianjin',xy=(117.5,39.25),xytext=(119.55,40.0),ha='center',fontsize=9,
        color='#414841',arrowprops={'arrowstyle':'-','color':'#656b65','lw':.45,
            'shrinkA':3,'shrinkB':3},zorder=6,path_effects=halo)
    ax.text(116.25,40.6,'Beijing',ha='center',fontsize=8,color='#737773',zorder=6,
        path_effects=halo)
    stations=json.loads((ROOT/'metadata/stations.json').read_text())
    offsets={'Yucheng':(7,-1),'Luancheng':(-7,7),'Fengqiu':(-7,-9),'Shangqiu':(7,-3)}
    for p in stations:
        ax.scatter(p['lon'],p['lat'],s=19,marker='D',facecolors='white',
            edgecolors='#222222',linewidths=.75,zorder=7)
        dx,dy=offsets[p['name']]
        ax.annotate(p['name'],(p['lon'],p['lat']),xytext=(dx,dy),textcoords='offset points',
            ha='left' if dx>0 else 'right',fontsize=8,fontweight='bold',zorder=8,
            path_effects=halo)
    ax.set_xlim(limits[0],limits[2]);ax.set_ylim(limits[1],limits[3])
    ax.set_aspect(1/np.cos(np.deg2rad(36)))
    ax.set_xticks(np.arange(110,123,2));ax.set_yticks(np.arange(30,43,2))
    ax.set_xticklabels([f'{v}°E' for v in np.arange(110,123,2)])
    ax.set_yticklabels([f'{v}°N' for v in np.arange(30,43,2)])
    for spine in ax.spines.values():spine.set_color('#8c918c');spine.set_linewidth(.45)
    ax.tick_params(direction='out',length=2.5,width=.45,color='#787e78',labelsize=8,pad=4)
    counts=well_summary['plotted_type_counts']
    handles=[
        Line2D([],[],marker='o',color='none',markerfacecolor='none',markeredgecolor='#2876a5',
            markeredgewidth=.55,markersize=3.5,label=f"Unconfined ({counts['Unconfined']})"),
        Line2D([],[],marker='^',color='none',markerfacecolor='#c36c2d',markeredgecolor='white',
            markeredgewidth=.2,markersize=4,label=f"Confined ({counts['Confined']})"),
        Line2D([],[],marker='x',color='none',markeredgecolor='#656565',
            markeredgewidth=.6,markersize=3.5,label=f"Unspecified ({counts['Unspecified']})"),
    ]
    ax.text(.028,.967,'Monitoring wells',transform=ax.transAxes,fontsize=8,
        fontweight='bold',ha='left',va='top')
    ax.text(.028,.943,'2005–2017',transform=ax.transAxes,fontsize=7.5,ha='left',va='top',
        color='#626862')
    well_legend=ax.legend(handles=handles,loc='upper left',bbox_to_anchor=(.025,.915),
        frameon=False,fontsize=7.5,handlelength=1.1,handletextpad=.65,labelspacing=.75,
        borderaxespad=0,borderpad=0)
    ax.add_artist(well_legend)
    station_handle=Line2D([],[],marker='D',color='none',markerfacecolor='white',
        markeredgecolor='#222222',markeredgewidth=.75,markersize=4,label='Field station')
    ax.legend(handles=[station_handle],loc='upper left',bbox_to_anchor=(.025,.811),
        frameon=False,fontsize=7.5,handlelength=1.1,handletextpad=.65,borderaxespad=0,borderpad=0)
    ax.text(.028,.742,'Wheat–maize rotation',transform=ax.transAxes,fontsize=8,
        fontweight='bold',ha='left',va='bottom')
    ax.text(.028,.719,'Mapped fraction, 2024 (%)',transform=ax.transAxes,fontsize=7.2,
        ha='left',va='bottom',color='#626862')
    cax=ax.inset_axes([.028,.691,.218,.013])
    cbar=fig.colorbar(image,cax=cax,orientation='horizontal',ticks=[0,.5,1])
    cbar.ax.set_xticklabels(['0','50','100'])
    cbar.ax.tick_params(length=2,width=.4,labelsize=7,pad=2)
    cbar.outline.set_linewidth(.4)
    cbar.outline.set_edgecolor('#8c918c')

    # Retain all provenance and scope notes in the sidecar, not on the artwork.
    fig.canvas.draw()
    renderer=fig.canvas.get_renderer()
    text_boxes=[t.get_window_extent(renderer) for t in ax.texts]
    assert all(fig.bbox.contains(b.x0,b.y0) and fig.bbox.contains(b.x1,b.y1) for b in text_boxes)
    target=ROOT/'maps/NCP_wheat_maize_2024_ccd'
    archive=ROOT/'maps/archive';archive.mkdir(exist_ok=True)
    for ext in ['.png','.pdf']:
        previous=archive/f'NCP_wheat_maize_2024_ccd_with_wells_annotated{ext}'
        if target.with_suffix(ext).exists() and not previous.exists():shutil.copy2(target.with_suffix(ext),previous)
    for ext in ['.png','.pdf','.svg','.tif']:
        kwargs={'pil_kwargs':{'compression':'tiff_lzw'}} if ext=='.tif' else {}
        # Matplotlib outlines text with path effects in SVG. Native SVG strokes
        # preserve both the white label halo and editable geographic labels.
        halo_texts=[t for t in ax.texts if t.get_path_effects()]
        if ext=='.svg':
            for t in halo_texts:t.set_path_effects([])
        fig.savefig(target.with_suffix(ext),dpi=600,**kwargs)
        if ext=='.svg':
            for t in halo_texts:t.set_path_effects(halo)
            svg=ET.parse(target.with_suffix(ext))
            halo_labels={t.get_text() for t in halo_texts}
            for t in svg.iter('{http://www.w3.org/2000/svg}text'):
                if ''.join(t.itertext()) in halo_labels:
                    t.set('style',t.get('style','')+'; paint-order: stroke fill; stroke: white; '
                        'stroke-width: 2.2; stroke-opacity: 0.95; stroke-linejoin: round')
            ET.register_namespace('','http://www.w3.org/2000/svg')
            ET.register_namespace('xlink','http://www.w3.org/1999/xlink')
            svg.write(target.with_suffix(ext),encoding='utf-8',xml_declaration=True)
    plt.close(fig)
    report={'figure':'maps/NCP_wheat_maize_2024_ccd.png','domain':'six complete provincial units',
        'provinces':list(PROVINCES),'province_union_bounds':list(union.bounds),'plot_bounds':list(limits),
        'clip':'Vector compound paths, including interior holes; rasters clipped separately to their own province',
        'background':'White beyond province union; no cropped neighbouring province outlines',
        'boundary_source':json.loads((ROOT/'metadata/natural_earth_10m.json').read_text()),
        'export':{'size_mm':[180,220],'raster_dpi':600,'font':'Arial',
            'formats':['png','pdf','svg','tif'],'pdf_fonttype':42,'svg_text_editable':True,
            'tiff_compression':'LZW','titles_and_prose_on_artwork':False,
            'vector_elements':'Province boundaries, well markers, station markers and text; crop layer is raster',
            'display_projection':'Geographic longitude/latitude with aspect corrected at 36 degrees N'},
        'actual_plotted_well_counts':plotted_counts,
        'monitoring_wells':well_summary,
        'source_aggregation':reports,'all_six_provinces_within_plot':all(
            limits[0]<g.bounds[0] and limits[1]<g.bounds[1] and limits[2]>g.bounds[2] and limits[3]>g.bounds[3]
            for g in boundaries.values()),
        'analytical_NCPbox_masks_modified':False}
    assert report['all_six_provinces_within_plot']
    for ext in ['png','pdf','svg','tif']:
        p=target.with_suffix('.'+ext)
        report[ext+'_sha256']=hashlib.sha256(p.read_bytes()).hexdigest()
    (ROOT/'metadata/figure_2024_full_provinces.json').write_text(json.dumps(report,indent=2))
    print('Saved complete province figure:',target.with_suffix('.png'),flush=True)

if __name__=='__main__':main()
