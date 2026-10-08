"""Readable manuscript figures from unchanged current predictions and observations."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import math
import shutil

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.collections import LineCollection, PatchCollection
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle, FancyBboxPatch, FancyArrowPatch
import matplotlib.patheffects as pe
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PUB = ROOT / 'publication'
REG = ROOT / 'regional'
TABLES = PUB / 'tables'
CART = PUB / 'source_snapshots/cartography'
BLUE, ORANGE, GREY = '#2166a5', '#c56d20', '#646a70'
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11.5,'axes.titlesize':12.5,
                     'axes.labelsize':11.5,'xtick.labelsize':11.2,'ytick.labelsize':11.2,
                     'legend.fontsize':11.2,'axes.linewidth':.7,'lines.linewidth':1.6,
                     'text.color':'black','axes.labelcolor':'black','pdf.fonttype':42,
                     'svg.fonttype':'none','savefig.facecolor':'white'})
EXPORTS = []


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def style(ax, label, title):
    ax.set_title(f'({label})', loc='left', pad=8, fontweight='bold')
    ax.tick_params(direction='out', length=3, width=.7)
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color('#34383b')


def save(fig, relative, caption, sources, svg=False):
    stem = PUB / relative
    stem.parent.mkdir(parents=True, exist_ok=True)
    fig.canvas.draw()
    for extension in ['png','pdf'] + (['svg'] if svg else []):
        fig.savefig(stem.with_suffix('.'+extension), dpi=400, bbox_inches='tight', pad_inches=.08)
    stem.with_name(stem.name+'_caption.txt').write_text(caption+'\n')
    width, height = fig.get_size_inches()
    body = min(t.get_fontsize() for t in fig.findobj(matplotlib.text.Text) if t.get_text().strip())
    EXPORTS.append({'figure':relative+'.png','caption':caption,'source_sha256':{
        str(p.relative_to(ROOT)):sha(p) for p in sources},'source_size_inches':[float(width),float(height)],
        'minimum_authored_text_points':float(body),
        'panel_labels':[ax.get_title(loc='left') for ax in fig.axes if ax.get_title(loc='left')],
        'descriptive_panel_titles':False,
        'png_sha256':sha(stem.with_suffix('.png')),'pdf_sha256':sha(stem.with_suffix('.pdf'))})
    plt.close(fig)


def xy(lon, lat):
    # Same WGS84 equirectangular projection as the verified boundary vectors.
    return 6378137*np.radians(np.asarray(lon)-117)*np.cos(np.radians(36)), 6378137*np.radians(np.asarray(lat))


def boundaries(ax, labels=False):
    for file, colour, width, z in [('province_boundaries_polylines.csv','#64686b',.45,4),
                                   ('study_area_outline_exterior_polylines.csv','#191919',.65,5)]:
        d = pd.read_csv(CART/file)
        lines = [g[['x_m','y_m']].to_numpy() for _,g in d.groupby('segment_id',sort=False)]
        ax.add_collection(LineCollection(lines,colors=colour,linewidths=width,zorder=z))
    if labels:
        d = pd.read_csv(CART/'province_labels.csv')
        for row in d.itertuples():
            if row.name not in ['Henan','Hebei','Shandong','Anhui','Jiangsu','Shanxi']:
                continue
            ax.text(row.x_m,row.y_m,row.name,fontsize=11.2,ha='center',color='#292929',zorder=7,
                    path_effects=[pe.withStroke(linewidth=2.5,foreground='white')])


def map_axes(ax):
    lo,hi = xy([112,121.8],[31.8,41.05])
    ax.set_xlim(lo[0],lo[1]);ax.set_ylim(hi[0],hi[1]);ax.set_aspect('equal')
    xs,_=xy([113,116,119],np.zeros(3));_,ys=xy(np.zeros(3),[33,36,39])
    ax.set_xticks(xs,['113°E','116°E','119°E'])
    ax.set_yticks(ys,['33°N','36°N','39°N'])
    ax.tick_params(labelsize=11.2)


def cell_map(ax, cells, values, cmap, norm=None, vmin=None, vmax=None):
    x,y = xy(cells.longitude,cells.latitude)
    dx = 6378137*np.radians(.1)*np.cos(np.radians(36));dy=6378137*np.radians(.1)
    patches=[Rectangle((cx-dx/2,cy-dy/2),dx,dy) for cx,cy in zip(x,y)]
    artist=PatchCollection(patches,cmap=cmap,norm=norm,edgecolor='none',linewidth=0,rasterized=True,zorder=1)
    artist.set_array(np.asarray(values))
    if norm is None:artist.set_clim(vmin,vmax)
    ax.add_collection(artist);map_axes(ax);boundaries(ax)
    return artist


def workflow():
    fig,ax=plt.subplots(figsize=(8.4,5.6))
    fig.subplots_adjust(left=.01,right=.99,top=.99,bottom=.01)
    ax.set(xlim=(0,1),ylim=(0,1));ax.axis('off')
    def box(x,y,w,h,title,detail,colour=BLUE):
        ax.add_patch(FancyBboxPatch((x,y),w,h,boxstyle='round,pad=.009,rounding_size=.015',
                     facecolor='#f6f8fa',edgecolor=colour,linewidth=1.0))
        ax.text(x+w/2,y+h*.77,title,ha='center',va='center',fontsize=13,fontweight='bold',color='black')
        ax.text(x+w/2,y+h*.31,detail,ha='center',va='center',fontsize=11.7,linespacing=1.25,color='black')
    def arrow(a,b):
        ax.add_patch(FancyArrowPatch(a,b,arrowstyle='-|>',mutation_scale=13,color='#41464b',lw=1.1))
    box(.025,.75,.43,.205,'Crop observations','4 wheat sites · 5 maize sites\nGrowth parameters and site-year partitions')
    box(.545,.75,.43,.205,'Wuqiao irrigation experiment','Water calibration: 2016–2018\nRetrospective testing: 2019',ORANGE)
    arrow((.25,.737),(.38,.665));arrow((.76,.737),(.62,.665))
    box(.18,.45,.64,.205,'Continuous regional rotations','Shared crop parameters · weather · soil · rotation area\n32 representatives × 5 irrigation levels')
    arrow((.38,.437),(.25,.365));arrow((.62,.437),(.76,.365))
    box(.025,.135,.43,.225,'Spatial irrigation allocation','Uniform versus targeted\nIdentical regional irrigation budgets\nSelection: 1997–2013')
    box(.545,.135,.43,.225,'Annual irrigation strategies','Storage–rainfall versus rainfall-only\nMatched monitoring availability\nSelection: 2003–2013')
    ax.text(.5,.065,'Comparison: 2014–2025',ha='center',fontsize=13,fontweight='bold',color='black')
    ax.text(.5,.012,'Rotation grain · irrigation · ET · drainage · spatial gains and losses',ha='center',fontsize=11.7,color='black')
    save(fig,'figures/closed_axes/Figure_2_model_and_experiment',
         'Figure 1. Study workflow. Multisite observations support growth parameters, while documented Wuqiao treatments calibrate water-use parameters and provide retrospective testing. Shared parameters drive continuous regional rotations. Spatial allocation compares uniform and targeted irrigation under identical budgets; annual strategies compare storage–rainfall and rainfall-only selection under matched monitoring availability. Outcomes cover 2014–2025.',
         [ROOT/'calibration/parameters/frozen_model.json',REG/'parameters/frozen_protocol.json'],svg=True)


def domain():
    cells=pd.read_csv(REG/'data/all_source_cell_mapping.csv');reps=pd.read_csv(REG/'data/representative_cells.csv')
    fig=plt.figure(figsize=(8.4,7.2),layout='constrained')
    gs=fig.add_gridspec(2,2,height_ratios=[1.5,.8])
    a=fig.add_subplot(gs[0,0]);style(a,'a','Mapped rotation area')
    art=cell_map(a,cells,cells.used_area_ha/1000,'viridis',vmin=0,vmax=10)
    boundaries(a,labels=True)
    cb=fig.colorbar(art,ax=a,orientation='horizontal',pad=.045,shrink=.96)
    cb.set_label('Rotation area per cell (10³ ha)',fontsize=11)
    a=fig.add_subplot(gs[0,1]);style(a,'b','Sites and simulation units')
    cell_map(a,cells,np.ones(len(cells)),matplotlib.colors.ListedColormap(['#e4e8eb']),vmin=0,vmax=1)
    x,y=xy(reps.longitude,reps.latitude);a.scatter(x,y,s=24,c=BLUE,edgecolors='white',linewidths=.6,zorder=6)
    stations=json.loads((CART/'observation_site_coordinates.json').read_text())['stations']
    offsets={'Gucheng':(6,6),'Luancheng':(-70,7),'Yucheng':(6,-10),'Fengqiu':(-52,7),'Shangqiu':(5,-12)}
    for row in stations:
        x,y=xy(row['longitude'],row['latitude']);a.scatter(x,y,marker='*',s=82,color=ORANGE,edgecolors='white',linewidths=.6,zorder=7)
        a.annotate(row['site'],(x,y),xytext=offsets[row['site']],textcoords='offset points',fontsize=11.2,zorder=8,
                   path_effects=[pe.withStroke(linewidth=2.5,foreground='white')])
    for row in json.loads((CART/'field_experiment_coordinates.json').read_text()):
        x,y=xy(row['longitude'],row['latitude']);a.scatter(x,y,marker='D',s=46,facecolors='white',edgecolors='black',linewidths=1,zorder=7)
        a.annotate(row['site'],(x,y),xytext=(8,-9),textcoords='offset points',fontsize=11.2,zorder=8,
                   path_effects=[pe.withStroke(linewidth=2.5,foreground='white')])
    a.text(.02,.02,'9.867 million rotation ha',transform=a.transAxes,fontsize=11.2,zorder=9,
           bbox={'facecolor':'white','edgecolor':'none','alpha':.9,'pad':2})
    a=fig.add_subplot(gs[1,:]);style(a,'c','Observed regional terrestrial water storage')
    grace=pd.read_csv(REG/'data/observed_regional_twsa_monthly.csv')
    grace['date']=pd.to_datetime(grace.nominal_month)
    monthly=grace.set_index('date').observed_twsa_mm.asfreq('MS')
    a.plot(monthly.index,monthly.values,color=GREY,lw=.85,label='Monthly anomaly')
    a.plot(monthly.index,monthly.rolling(12,min_periods=9).mean(),color=BLUE,lw=1.7,label='12-month mean (≥9 observations)')
    a.axhline(0,lw=.6,color='#8d9296');a.set(ylabel='TWS anomaly (mm)',xlabel='Year',xlim=(monthly.index.min(),monthly.index.max()))
    a.xaxis.set_major_locator(mdates.YearLocator(4));a.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
    a.legend(loc='lower left',ncol=2,frameon=False,fontsize=11.2)
    sources=[REG/'data/all_source_cell_mapping.csv',REG/'data/representative_cells.csv',REG/'data/observed_regional_twsa_monthly.csv',
             CART/'province_boundaries_polylines.csv',CART/'study_area_outline_exterior_polylines.csv',
             CART/'observation_site_coordinates.json',CART/'field_experiment_coordinates.json']
    save(fig,'figures/closed_axes/Figure_1_domain_and_observed_storage',
         'Figure 2. Study domain, rotation area, observation sites and regional water storage. (a) Mapped 2020 wheat–maize rotation area. (b) Five crop-observation sites, the Wuqiao irrigation experiment and 32 regional simulation units. (c) Observed CSR monthly terrestrial-water-storage anomalies and a 12-calendar-month mean requiring at least nine observations. Missing months remain gaps. Thin grey lines show provincial boundaries; black outlines delimit the operational physiographic study domain. Maps use an equirectangular projection (36°N, 117°E). Provincial boundaries: Natural Earth Admin 1 v5.1.1 [CITE:naturalearth_admin1].',sources)


def partitions():
    inventory=pd.read_csv(TABLES/'current_case_partition.csv');field=pd.read_csv(TABLES/'current_field_figure_sources.csv')
    fig,axs=plt.subplots(1,2,figsize=(8.4,4.7),layout='constrained',gridspec_kw={'width_ratios':[1.6,1]})
    rows=sorted(set(zip(inventory.site,inventory.crop)))
    for y,(site,crop) in enumerate(rows):
        for split,marker,colour in [('calibration','o',BLUE),('validation','s',ORANGE)]:
            years=inventory[(inventory.site==site)&(inventory.crop==crop)&(inventory.split==split)].year.unique()
            axs[0].scatter(years,np.full(len(years),y),marker=marker,c=colour,s=24)
    axs[0].set_yticks(range(len(rows)),[site+' · '+crop for site,crop in rows]);axs[0].invert_yaxis()
    axs[0].set(xlim=(1997,2023),xticks=[1998,2004,2010,2016,2022],xlabel='Observation group year')
    style(axs[0],'a','Whole station site-year partitions')
    for i,crop in enumerate(['wheat','maize']):
        for split,marker,colour in [('calibration','o',BLUE),('validation','s',ORANGE)]:
            g=field[(field.crop==crop)&(field.split==split)]
            axs[1].scatter(g.harvest_year,i+g.treatment.str[-1].astype(int)*.13,marker=marker,c=colour,s=28)
    axs[1].set(xlim=(2015.65,2019.35),ylim=(-.15,1.7),xticks=[2016,2017,2018,2019],xlabel='Harvest year')
    axs[1].set_yticks([.195,1.195],['Wheat W0–W3','Maize W0–W3']);style(axs[1],'b','Wuqiao treatment seasons')
    axs[1].text(.04,.95,'12 calibration + 4 testing\nseasons per crop',transform=axs[1].transAxes,va='top',fontsize=11.2)
    fig.legend(handles=[Line2D([],[],ls='none',marker='o',color=BLUE,label='Calibration partition'),
                        Line2D([],[],ls='none',marker='s',color=ORANGE,label='Retrospective evaluation/testing')],
               loc='outside lower center',ncol=2,frameon=False,fontsize=11.2)
    save(fig,'figures/current_Figure_3_observation_partitions',
         'Figure 3. Evidence partitions. (a) Whole station site-years retain their original calibration and retrospective-evaluation partitions; dates denote observation-group years. All five eligible sites are included. (b) Four Wuqiao treatment means per crop-year provide 12 calibration seasons in 2016–2018 and four retrospective testing seasons in 2019. Station comparisons retain earlier growth-fitting history and unconfirmed complete irrigation management.',
         [TABLES/'current_case_partition.csv',TABLES/'current_field_figure_sources.csv'])


def curve_legend():
    return [Line2D([],[],color='black',marker='o',ls='none',label='Calibration observations'),
            Line2D([],[],color='black',marker='s',mfc='white',ls='none',label='Evaluation observations'),
            Line2D([],[],color=BLUE,label='Calibration simulation'),
            Line2D([],[],color=ORANGE,ls='--',label='Evaluation simulation')]


def curve(ax,g):
    factor=.001 if g.variable.iloc[0]=='biomass' else 1.
    for split,colour,marker,linestyle in [('calibration',BLUE,'o','-'),('validation',ORANGE,'s','--')]:
        d=g[g.split==split].sort_values('bin')
        if d.empty:continue
        ax.errorbar(d.das,d.observed*factor,yerr=d.observed_SE.fillna(0)*factor,
                    ls='none',marker=marker,ms=3.3,mfc='black' if split=='calibration' else 'white',
                    mec='black',mew=.6,color='black',ecolor='#74797e',elinewidth=.7,capsize=1.6,zorder=4)
        groups=d.bin.diff().gt(1).cumsum()
        for _,part in d.groupby(groups):
            ax.plot(part.das,part.predicted*factor,color=colour,ls=linestyle,lw=1.7)


def curve_limits(data,variable):
    d=data[data.variable==variable];factor=.001 if variable=='biomass' else 1
    maximum=max(float((d.observed+d.observed_SE.fillna(0)).max()),float(d.predicted.max()))*factor
    step={'lai':1,'biomass':5,'et':1}[variable]
    return 0,math.ceil(maximum*1.07/step)*step


def seasonal():
    d=pd.read_csv(TABLES/'current_balanced_seasonal_curves.csv')
    fig,axs=plt.subplots(2,3,figsize=(8.4,5.9),layout='constrained')
    labels={'lai':'LAI (m² m⁻²)','biomass':'Dry biomass (t ha⁻¹)','et':'Daily ET (mm d⁻¹)'}
    names={'lai':'LAI','biomass':'Biomass','et':'ET'}
    for i,crop in enumerate(['wheat','maize']):
        for j,variable in enumerate(['lai','biomass','et']):
            ax=axs[i,j];curve(ax,d[(d.crop==crop)&(d.variable==variable)])
            style(ax,chr(97+i*3+j),crop.title()+' · '+names[variable])
            ax.set(xlim=(0,260 if crop=='wheat' else 120),ylim=curve_limits(d,variable),xlabel='Days after sowing',ylabel=labels[variable])
            ax.tick_params(labelsize=11.2)
    fig.legend(handles=curve_legend(),loc='outside lower center',ncol=2,frameon=False,fontsize=11.2)
    save(fig,'figures/current_Figure_4_multisite_seasonal_curves',
         'Figure 4. Multisite seasonal crop comparisons. Wheat and maize occupy the top and bottom rows; columns show LAI, aboveground dry biomass and daily actual ET. Filled and open observations distinguish the original calibration and retrospective-evaluation partitions; solid blue and dashed orange lines show the corresponding simulations. Error bars describe standard errors among site-year means. Balanced means use 15-day wheat and seven-day maize bins, and unobserved bins remain gaps. Accuracy scores use unbinned records. Complete station irrigation management remains unconfirmed.',
         [TABLES/'current_balanced_seasonal_curves.csv'])
    site=pd.read_csv(TABLES/'current_site_seasonal_curves.csv')
    for number,crop,variable,stem in [(6,'wheat','lai','current_Figure_S7_wheat_lai_seasonal_curves'),
          (7,'wheat','biomass','current_Figure_S8_wheat_biomass_seasonal_curves'),
          (8,'wheat','et','current_Figure_S9_wheat_et_seasonal_curves'),
          (9,'maize','lai','current_Figure_S10_maize_lai_seasonal_curves'),
          (10,'maize','biomass','current_Figure_S11_maize_biomass_seasonal_curves'),
          (11,'maize','et','current_Figure_S12_maize_et_seasonal_curves')]:
        q=site[(site.crop==crop)&(site.variable==variable)];sites=sorted(q.site.unique())
        fig,axs=plt.subplots(math.ceil(len(sites)/2),2,figsize=(8.4,2.25*math.ceil(len(sites)/2)+.65),layout='constrained',squeeze=False)
        for i,name in enumerate(sites):
            ax=axs.flat[i];curve(ax,q[q.site==name]);style(ax,chr(97+i),name)
            ax.set(xlim=(0,260 if crop=='wheat' else 120),ylim=curve_limits(q,variable),xlabel='Days after sowing',ylabel=labels[variable])
        for ax in list(axs.flat)[len(sites):]:ax.remove()
        fig.legend(handles=curve_legend(),loc='outside lower center',ncol=2,frameon=False,fontsize=11.2)
        term=names[variable] if variable in ['lai','et'] else 'aboveground biomass'
        panels='; '.join(f'({chr(97+i)}) {name}' for i,name in enumerate(sites))
        save(fig,'figures/'+stem,f'Figure S{number}. Site-specific {crop} {term} comparisons: {panels}. Calibration and retrospective-evaluation observations and simulations share the same axes within each site. Error bars describe site-year standard errors; gaps are retained. The observations, weighting and conditional management interpretation follow Figure 4.',
             [TABLES/'current_site_seasonal_curves.csv'])


def field_scatter():
    d=pd.read_csv(TABLES/'current_field_figure_sources.csv')
    fig,axs=plt.subplots(2,2,figsize=(8.4,7.0),layout='constrained')
    fig.set_constrained_layout_pads(w_pad=.08,h_pad=.10,wspace=.08,hspace=.10)
    for row,var in enumerate(['et','grain']):
        for col,crop in enumerate(['wheat','maize']):
            ax=axs[row,col];g=d[d.crop==crop]
            observed='observed_et_mm' if var=='et' else 'yield_13pct_kg_ha'
            predicted='predicted_et_mm' if var=='et' else 'grain_13pct_kg_ha'
            factor=1 if var=='et' else .001
            limits=(150,610) if var=='et' else ((1.5,11.5) if crop=='wheat' else (8.5,14.5))
            for split,marker,colour in [('calibration','o',BLUE),('validation','s',ORANGE)]:
                part=g[g.split==split];x=part[observed]*factor;y=part[predicted]*factor
                if var=='grain':
                    ax.errorbar(x,y,xerr=part.standard_error_kg_ha.fillna(0)*factor,ls='none',ecolor=colour,elinewidth=.8,capsize=2,zorder=2)
                ax.scatter(x,y,marker=marker,c=colour,s=34,edgecolors='#242424',linewidths=.6,zorder=3)
                assert x.between(*limits).all() and y.between(*limits).all(),(crop,var)
            ax.plot(limits,limits,'--',color='#34383b',lw=1.1)
            ax.set(xlim=limits,ylim=limits,aspect='equal')
            unit='seasonal ET (mm)' if var=='et' else 'grain (t ha⁻¹)'
            ax.set_xlabel('Observed '+unit);ax.set_ylabel('Simulated '+unit)
            style(ax,chr(97+row*2+col),crop.title()+' · '+('Seasonal ET' if var=='et' else 'Annual grain'))
    fig.legend(handles=[Line2D([],[],ls='none',marker='o',color=BLUE,label='Calibration: 2016–2018 (n = 12 per crop)'),
                        Line2D([],[],ls='none',marker='s',color=ORANGE,label='Retrospective testing: 2019 (n = 4 per crop)')],
               loc='outside lower center',ncol=1,frameon=False,fontsize=11.2)
    save(fig,'figures/current_Figure_5_Wuqiao_calibration_testing',
         'Figure 5. Wuqiao calibration and retrospective testing. (a,b) Seasonal ET; (c,d) annual treatment-mean grain at 13% moisture. Wheat occupies the left column and maize the right. Circles show 2016–2018 treatments used to fit water-use parameters; squares show 2019, withheld from the fit and selection. Dashed lines denote 1:1 agreement. Crop-specific grain axes focus on their observed ranges. Horizontal grain error bars are the published SE from three field replicates. Observed ET follows the published 0–2 m water balance; simulated dry grain is converted once by division by 0.87. Maize labels retain preceding wheat management with identical maize irrigation within each year.',
         [TABLES/'current_field_figure_sources.csv'])


def policies():
    d=pd.read_csv(REG/'tables/regional_policy_annual_results.csv');d=d[d.harvest_year.between(2014,2025)].copy()
    area=float(d.mapped_rotation_area_ha.iloc[0]);d['grain_t_ha']=d.grain_production_t/area;d['et_mm']=d.modeled_total_et_volume_m3/(area*10)
    d.to_csv(TABLES/'current_figure6_policy_values.csv',index=False)
    fig,axs=plt.subplots(2,2,figsize=(8.4,6.4),layout='constrained')
    for variable,ax,label in [('grain_t_ha',axs[0,0],'Rotation dry grain (t ha⁻¹ yr⁻¹)'),('et_mm',axs[0,1],'Crop + fallow ET (mm yr⁻¹)')]:
        for policy,colour,marker in [('uniform',ORANGE,'s'),('targeted',BLUE,'o')]:
            means=[];spread=[]
            for cut in [0,25,50,75]:
                name='conventional' if cut==0 else f'{policy}_{cut}pct';g=d[d.policy==name]
                means.append(g[variable].mean());spread.append(g[variable].std(ddof=1))
            ax.errorbar([0,25,50,75],means,yerr=spread,color=colour,marker=marker,ms=5,lw=1.7,elinewidth=1.0,capsize=3,label=policy.title())
        ax.set(xlabel='Field irrigation reduction (%)',ylabel=label,xticks=[0,25,50,75])
        style(ax,'a' if variable=='grain_t_ha' else 'b','Grain retained' if variable=='grain_t_ha' else 'Consumptive water use')
    axs[0,0].legend(frameon=False,loc='lower left')
    pivot=d.pivot(index='harvest_year',columns='policy',values='grain_t_ha')
    for cut,colour,marker in [(25,GREY,'^'),(50,BLUE,'o'),(75,ORANGE,'s')]:
        delta=pivot[f'targeted_{cut}pct']-pivot[f'uniform_{cut}pct']
        axs[1,0].plot(delta.index,delta,color=colour,marker=marker,ms=4,lw=1.2,label=f'{cut}% cut')
    axs[1,0].axhline(0,color='#777',lw=.7);axs[1,0].set(xlabel='Harvest year',ylabel='Targeted − uniform grain (t ha⁻¹ yr⁻¹)',xticks=[2014,2018,2022,2025])
    style(axs[1,0],'c','Annual allocation advantage');axs[1,0].legend(ncol=1,fontsize=11.2,frameon=False,loc='upper right')
    m=d.groupby('policy').mean(numeric_only=True)
    for cut in [25,50,75]:
        u,t=m.loc[f'uniform_{cut}pct'],m.loc[f'targeted_{cut}pct']
        axs[1,1].plot([u.et_mm,t.et_mm],[u.grain_t_ha,t.grain_t_ha],color='#92979b',lw=1.0)
        axs[1,1].scatter([u.et_mm],[u.grain_t_ha],marker='s',c=ORANGE,s=42)
        axs[1,1].scatter([t.et_mm],[t.grain_t_ha],marker='o',c=BLUE,s=42)
        axs[1,1].annotate(f'{cut}%',(u.et_mm,u.grain_t_ha),xytext=(5,-12),ha='left',textcoords='offset points',fontsize=11.2)
    c=m.loc['conventional'];axs[1,1].scatter([c.et_mm],[c.grain_t_ha],marker='D',c=GREY,s=34)
    axs[1,1].annotate('Conventional',(c.et_mm,c.grain_t_ha),xytext=(-4,8),ha='right',textcoords='offset points',fontsize=11.2)
    axs[1,1].set(xlabel='Crop + fallow ET (mm yr⁻¹)',ylabel='Rotation dry grain (t ha⁻¹ yr⁻¹)',ylim=(13,17.4),xlim=(720,920))
    style(axs[1,1],'d','Grain–ET trade-off')
    save(fig,'figures/closed_axes/Figure_3_policy_tradeoffs',
         'Figure 6. Irrigation allocation and grain–water trade-offs during 2014–2025. (a,b) Rotation-area-weighted annual combined wheat–maize dry grain and crop-plus-fallow ET; error bars are between-year SD. (c) Annual targeted-minus-uniform grain at identical irrigation budgets. (d) Period means; connectors join policies with the same budget. Values per hectare use the same 9.867 million mapped rotation hectares, rather than separate crop areas. Targeting retains more grain than uniform cuts while remaining below conventional production.',
         [REG/'tables/regional_policy_annual_results.csv'])


def spatial():
    cells=pd.read_csv(REG/'tables/current_source_cell_map_values.csv')
    fig,axs=plt.subplots(2,2,figsize=(8.4,7.2),layout='constrained')
    cmap=LinearSegmentedColormap.from_list('allocation',['#f3f6f8',BLUE])
    art=cell_map(axs[0,0],cells,100*cells.source_screened_targeted_mean_quota,cmap,vmin=0,vmax=100)
    style(axs[0,0],'a','Irrigation retained')
    cb=fig.colorbar(art,ax=axs[0,0],orientation='horizontal',pad=.045,shrink=.95);cb.set_label('Conventional quota retained (%)',fontsize=11.2)
    diverge=LinearSegmentedColormap.from_list('contrast',[ORANGE,'#f7f7f7',BLUE])
    for ax,label,column,scale,title,unit in [(axs[0,1],'b','source_screened_delta_yield_kg_ha',.001,'Grain contrast','t ha⁻¹ yr⁻¹'),
                                           (axs[1,0],'c','source_screened_delta_modeled_total_et_mm',1,'ET contrast','mm yr⁻¹')]:
        values=cells[column]*scale;bound=math.ceil(max(abs(values.min()),abs(values.max())))
        art=cell_map(ax,cells,values,diverge,norm=TwoSlopeNorm(vcenter=0,vmin=-bound,vmax=bound))
        style(ax,label,title)
        cb=fig.colorbar(art,ax=ax,orientation='horizontal',pad=.045,shrink=.95)
        cb.set_label('Targeted − uniform ('+unit+')',fontsize=11.2)
    d=pd.read_csv(TABLES/'current_spatial_distribution.csv');d=d[np.isclose(d.reduction_fraction,.5)].set_index('outcome_group')
    ax=axs[1,1];order=['gain','loss','unchanged'];colors=[BLUE,ORANGE,'#b4babf']
    vals=[d.loc[s,'area_pct'] for s in order]
    ax.barh(range(3),vals,color=colors,height=.55)
    ax.set_yticks(range(3),['Gain','Loss','Unchanged']);ax.set(xlim=(0,60),ylim=(3.25,-.6),xlabel='Mapped rotation area (%)')
    for y,value in enumerate(vals):ax.text(value+1,y,f'{value:.2f}%',va='center',fontsize=11.5)
    style(ax,'d','Rotation area affected')
    ax.text(.02,.05,'Area-weighted median: 0\nRegional mean: +0.732 t ha⁻¹ yr⁻¹',transform=ax.transAxes,fontsize=11.2,
            bbox={'facecolor':'white','edgecolor':'none','pad':1.5})
    save(fig,'figures/closed_axes/Figure_7_current_spatial_policy_outcomes',
         'Figure 7. Spatial redistribution at a common 50% regional irrigation reduction, 2014–2025. (a) Targeted fraction of the conventional quota; uniform allocation retains 50% everywhere. (b,c) Targeted-minus-uniform combined rotation dry grain and crop-plus-fallow ET. (d) Mapped rotation-area shares with positive, negative or unchanged mean grain contrasts. The 3,641 cells inherit 32 representative responses. Thin grey provincial lines and black study-domain outlines follow Figure 2; unmapped areas remain blank.',
         [REG/'tables/current_source_cell_map_values.csv',TABLES/'current_spatial_distribution.csv',CART/'province_boundaries_polylines.csv',CART/'study_area_outline_exterior_polylines.csv'])


def adaptive():
    d=pd.read_csv(REG/'adaptive/tables/figure_adaptive_testing_values.csv')
    fig,axs=plt.subplots(2,2,figsize=(8.4,6.4),layout='constrained')
    fields=['irrigation_mm','grain_retention_pct','et_reduction_mm']
    labels=['Field irrigation (mm yr⁻¹)','Conventional grain retained (%)','ET reduction (mm yr⁻¹)']
    titles=['Irrigation supplied','Annual production retention','Consumption reduction']
    for ax,label,field,unit,title in zip(list(axs.flat)[:3],'abc',fields,labels,titles):
        for policy,colour,marker in [('adaptive_95',BLUE,'o'),('adaptive_98',ORANGE,'s')]:
            g=d[d.policy==policy].sort_values('harvest_year')
            ax.plot(g.harvest_year,g[field],color=colour,lw=1.5)
            for available in [True,False]:
                q=g[g.class_available==available]
                ax.scatter(q.harvest_year,q[field],s=27,marker=marker,edgecolors=colour,
                           facecolors=colour if available else 'white',linewidths=1,zorder=4)
        for year in [2014,2018,2019]:ax.axvspan(year-.3,year+.3,color='#eceff1',zorder=0)
        ax.set(xlabel='Harvest year',ylabel=unit,xticks=[2014,2018,2022,2025]);style(ax,label,title)
    axs[0,0].axhline(380,ls='--',color=GREY,lw=.9);axs[0,0].set_ylim(170,400)
    axs[0,1].axhline(100,ls='--',color=GREY,lw=.9);axs[0,1].axhline(95,ls=':',color=GREY,lw=.9);axs[0,1].axhline(98,ls=':',color='#92979b',lw=.7);axs[0,1].set_ylim(90,101)
    axs[1,0].axhline(0,color=GREY,lw=.7)
    for policy,colour,marker in [('adaptive_95',BLUE,'o'),('adaptive_98',ORANGE,'s')]:
        g=d[d.policy==policy]
        for available in [True,False]:
            q=g[g.class_available==available]
            axs[1,1].scatter(q.irrigation_reduction_mm,q.grain_retention_pct,s=30,marker=marker,edgecolors=colour,
                             facecolors=colour if available else 'white',linewidths=1)
    axs[1,1].axhline(95,ls=':',color=GREY,lw=.9);axs[1,1].set(xlabel='Field irrigation reduction (mm yr⁻¹)',ylabel='Conventional grain retained (%)',ylim=(90,101));style(axs[1,1],'d','Annual grain–irrigation trade-off')
    handles=[Line2D([],[],color=BLUE,marker='o',label='95% selection target'),Line2D([],[],color=ORANGE,marker='s',label='98% selection target'),
             Line2D([],[],color=GREY,marker='o',mfc='white',ls='none',label='Conventional quota in GRACE gaps')]
    fig.legend(handles=handles,loc='outside lower center',ncol=2,frameon=False,fontsize=11.2)
    save(fig,'figures/closed_axes/Figure_8_continuous_class_irrigation',
         'Figure 8. Continuous annual irrigation strategies during 2014–2025. (a) Field irrigation; (b) grain retained relative to conventional irrigation; (c) crop-plus-fallow ET reduction; (d) annual production–irrigation combinations. Blue circles and orange squares identify 95% and 98% selection targets. Shading and open markers identify missing-GRACE years with conventional quotas. Grain and ET can still differ in those years because preceding strategy decisions alter carried soil-water states. Matched rainfall-only histories coincide with the storage–rainfall histories.',
         [REG/'adaptive/tables/figure_adaptive_testing_values.csv'])


def main(selected=None):
    functions=[workflow,domain,partitions,seasonal,field_scatter,policies,spatial,adaptive]
    for function in functions:
        if selected and function.__name__ not in selected:continue
        function()
    receipt_path=PUB/'verification/visual_revision_20261008.json'
    if selected and receipt_path.exists():
        old=json.loads(receipt_path.read_text())['figures']
        merged={x['figure']:x for x in old}
        merged.update({x['figure']:x for x in EXPORTS})
        exported=list(merged.values())
    else:exported=EXPORTS
    receipt={'generated_utc':datetime.now(timezone.utc).isoformat(),'source_predictions_modified':False,
             'main_figures_revised':8,'supplementary_seasonal_figures_revised':6,'figures':exported,
             'renderer_sha256':sha(Path(__file__)),
             'map_projection':'WGS84 equirectangular; standard parallel36N, central meridian117E',
             'all_figure1_text_black':True,'regional_grain_basis':'combined annual wheat–maize dry grain per mapped rotation hectare'}
    receipt_path.write_text(json.dumps(receipt,indent=2,ensure_ascii=False)+'\n')
    print('Revised eight main figures and six site-specific seasonal figures from unchanged current data.')


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--figures',nargs='+')
    main(parser.parse_args().figures)
