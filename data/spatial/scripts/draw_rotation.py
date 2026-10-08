"""Export a source-backed regional overview and geographic reference layers."""
from pathlib import Path
import json
import numpy as np
import rasterio
import shapefile
from shapely.geometry import shape, box, mapping
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.ticker import PercentFormatter
from matplotlib.patches import Polygon

ROOT = Path(__file__).resolve().parents[1]
ROI = (112,32,120,41)

def main():
    from draw_ccd_full_provinces import main as draw_complete_ccd
    draw_complete_ccd()
    stations = json.loads((ROOT/'metadata/stations.json').read_text())
    reader = shapefile.Reader(str(ROOT/'extracted/natural_earth/ne_50m_admin_1_states_provinces.shp'))
    features = []
    for sr in reader.iterShapeRecords():
        props = sr.record.as_dict()
        if props['adm0_a3'] == 'CHN':
            geom = shape(sr.shape.__geo_interface__)
            if geom.intersects(box(*ROI)):
                features.append({'type':'Feature','geometry':mapping(geom),
                    'properties':{'name':props['name'],'source':'Natural Earth 1:50m admin-1 v5.1.1',
                    'purpose':'Cartographic context only; not an analysis boundary'}})
    (ROOT/'derived/context_provinces.geojson').write_text(json.dumps({'type':'FeatureCollection','features':features}))
    (ROOT/'derived/NCP_display_box.geojson').write_text(json.dumps({'type':'FeatureCollection','features':[
        {'type':'Feature','geometry':mapping(box(*ROI)),'properties':{'name':'NCP and adjoining Huang-Huai display box',
         'west':112,'east':120,'south':32,'north':41,'note':'Extraction/display box, NOT a formal NCP boundary'}}]}))
    for kind, year, title, subtitle, doi in [
        ('chinacp_wheat10m_2020',2020,'Wheat–maize rotation | 2020',
         'ChinaCP-Wheat10m · actual downloaded grid ≈16 × 20 m at 36.5°N', '10.6084/m9.figshare.28646687.v3')]:
        path=ROOT/'derived'/kind/f'NCPbox_mapped_rotation_fraction_{year}_overview.tif'
        with rasterio.open(path) as s:
            a=s.read(1); bounds=s.bounds
        plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,'axes.titlesize':15,'pdf.fonttype':42})
        fig=plt.figure(figsize=(9,10.5),facecolor='white')
        ax=fig.add_axes([.09,.20,.76,.69])
        ax.set_facecolor('#f5f5f2')
        selected={'Hebei','Henan','Shandong','Tianjin','Anhui','Jiangsu'}
        for f in features:
            g=shape(f['geometry'])
            polygons=list(g.geoms) if g.geom_type=='MultiPolygon' else [g]
            for poly in polygons:
                x,y=poly.exterior.xy
                if kind.startswith('ccd') and f['properties']['name'] not in selected:
                    ax.add_patch(Polygon(np.array(poly.exterior.coords),facecolor='#e5e5e5',edgecolor='none',zorder=0))
                ax.plot(x,y,color='#747474',linewidth=.65,zorder=3)
        cmap=LinearSegmentedColormap.from_list('rotation',['#edf3ee','#8ab797','#17643b'])
        masked=np.ma.masked_where(a<=0,a)
        im=ax.imshow(masked,extent=[bounds.left,bounds.right,bounds.bottom,bounds.top],origin='upper',
            cmap=cmap,vmin=0,vmax=1,interpolation='nearest',zorder=2)
        labels={'Hebei':(115.1,39.15),'Henan':(113.5,33.5),'Shandong':(118.6,37.2),
            'Anhui':(116.4,32.55),'Jiangsu':(119.25,32.65),'Tianjin':(117.55,39.3),
            'Shanxi':(112.65,37.9),'Beijing':(116.15,40.25)}
        for name,(x,y) in labels.items():
            ax.text(x,y,name,fontsize=10,color='#555555',ha='center',zorder=4,
                bbox={'facecolor':'white','edgecolor':'none','alpha':.8,'pad':1.5})
        offsets={'Yucheng':(9,6),'Luancheng':(9,6),'Shangqiu':(9,-13),'Fengqiu':(-9,7)}
        for p in stations:
            ax.scatter(p['lon'],p['lat'],s=42,facecolors='white',edgecolors='#202020',linewidths=1.1,zorder=6)
            dx,dy=offsets[p['name']]
            ax.annotate(p['name'],(p['lon'],p['lat']),xytext=(dx,dy),textcoords='offset points',
                ha='left' if dx>0 else 'right',fontsize=10,fontweight='bold',zorder=7,
                bbox={'facecolor':'white','edgecolor':'none','alpha':.9,'pad':2})
        ax.set_xlim(112,120);ax.set_ylim(32,41)
        ax.set_aspect(1/np.cos(np.deg2rad(36.5)))
        ax.set_xticks(np.arange(112,121,2));ax.set_yticks(np.arange(32,42,2))
        ax.set_xticklabels([f'{x}°E' for x in np.arange(112,121,2)])
        ax.set_yticklabels([f'{y}°N' for y in np.arange(32,42,2)])
        ax.grid(color='#777777',alpha=.18,linewidth=.5,zorder=0)
        cax=fig.add_axes([.86,.32,.025,.42])
        cbar=fig.colorbar(im,cax=cax,format=PercentFormatter(1))
        cbar.set_label('Grid positions mapped as rotation (%)',labelpad=9)
        fig.text(.09,.95,title,fontsize=18,fontweight='bold',color='#202020')
        fig.text(.09,.92,subtitle,fontsize=11,color='#505050')
        fig.text(.09,.145,'NCP and adjoining Huang–Huai region · extraction box, not a formal regional boundary',fontsize=10)
        fig.text(.09,.112,'Display cells aggregate native-grid rotation detections (~1 km). Blank cells do not prove absence.\n'
            'Station markers are reference locations, not verified field boundaries.',fontsize=9.3,color='#555555',linespacing=1.5)
        fig.text(.09,.062,f'Data: doi:{doi}\nBoundaries: Natural Earth · prepared 2026-10-02',fontsize=9,color='#555555',linespacing=1.5)
        target=ROOT/'maps'/f'NCP_wheat_maize_{year}_{kind.split("_")[0]}'
        fig.savefig(target.with_suffix('.png'),dpi=250)
        fig.savefig(target.with_suffix('.pdf'),dpi=250)
        plt.close(fig)
        print('Saved map',target,flush=True)

if __name__=='__main__':main()
