"""Standalone manuscript figures from sealed observations and actual native runs."""
from pathlib import Path
import hashlib,json
import numpy as np
import pandas as pd
import rasterio
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import BoundaryNorm

P=Path(__file__).resolve().parents[1]
OUT=P/'figures/manuscript'
R=P/'runs/regional_quota_conductivity'
BLUE='#0072B2';ORANGE='#D55E00';GREEN='#009E73';GREY='#5E646B'
plt.rcParams.update({'font.family':['Arial','DejaVu Sans'],'font.size':9,'axes.titlesize':10,
    'axes.labelsize':9,'axes.spines.top':False,'axes.spines.right':False,
    'pdf.fonttype':42,'ps.fonttype':42,'savefig.dpi':600,'axes.linewidth':.7,
    'xtick.direction':'out','ytick.direction':'out','lines.linewidth':1.5})

def label(ax,s):ax.text(-.12,1.05,'('+s+')',transform=ax.transAxes,fontweight='bold',fontsize=11)
def save(fig,name):
    fig.savefig(OUT/(name+'.png'),bbox_inches='tight',facecolor='white')
    fig.savefig(OUT/(name+'.pdf'),bbox_inches='tight',facecolor='white')
    plt.close(fig)

def grid(frame,column):
    lat=np.round(np.arange(32.05,41.05,.1),2);lon=np.round(np.arange(112.05,121.55,.1),2)
    # AgERA5 centers lie on integer tenths, so derive the exact source grid.
    lat=np.sort(frame.latitude.unique());lon=np.sort(frame.longitude.unique())
    a=frame.pivot(index='latitude',columns='longitude',values=column).reindex(index=lat,columns=lon)
    return lon,lat,np.ma.masked_invalid(a.to_numpy())

def domain(stations=None,grace_path=None,benchmark_sites=None):
    fig=plt.figure(figsize=(7.2,6.2));gs=fig.add_gridspec(2,2,height_ratios=[1.7,1],hspace=.45,wspace=.4)
    mapping=pd.read_csv(R/'all_source_cell_mapping.csv');reps=pd.read_csv(R/'representative_cells.csv')
    ax=fig.add_subplot(gs[0,0]);label(ax,'a')
    with rasterio.open(P/'data/regional/operational_plain_e200_s2_0p01deg.tif') as ds:
        a=np.ma.masked_where(ds.read(1)!=1,ds.read(1))
        ax.imshow(a,extent=[ds.bounds.left,ds.bounds.right,ds.bounds.bottom,ds.bounds.top],origin='upper',cmap=matplotlib.colors.ListedColormap(['#E3E8EB']),interpolation='nearest',aspect='auto')
    x,y,z=grid(mapping,'used_area_ha')
    im=ax.pcolormesh(x,y,z/1000,cmap='viridis',vmin=0,vmax=10,shading='nearest',rasterized=True)
    ax.set(xlim=(112,121.5),ylim=(32,41),xlabel='Longitude (°E)',ylabel='Latitude (°N)',title='Mapped wheat–maize rotation')
    fig.colorbar(im,ax=ax,pad=.02,shrink=.7,label='Area per 0.1° cell (10³ ha)')
    ax=fig.add_subplot(gs[0,1]);label(ax,'b')
    ax.scatter(mapping.longitude,mapping.latitude,s=2,color='#C9CED1',rasterized=True)
    ax.scatter(reps.longitude,reps.latitude,s=18,facecolor=BLUE,edgecolor='white',linewidth=.4)
    if stations is not None:
        for row in stations:
            ax.scatter(row['longitude'],row['latitude'],marker='*',s=60,color='#D55E00',edgecolor='white',lw=.5,zorder=4)
            dx,dy={'Gucheng':(4,5),'Luancheng':(4,4),'Yucheng':(4,4),'Fengqiu':(-39,5),'Shangqiu':(4,-9)}[row['site']]
            ax.annotate(row['site'],(row['longitude'],row['latitude']),xytext=(dx,dy),textcoords='offset points',fontsize=7,color='#333333',zorder=5)
    if benchmark_sites:
        for row in benchmark_sites:
            ax.scatter(row['longitude'],row['latitude'],marker='D',s=42,facecolors='none',edgecolors='#333333',lw=.9,zorder=4)
            ax.annotate(row['site'],(row['longitude'],row['latitude']),xytext=(6,4),textcoords='offset points',fontsize=7,color='#333333',zorder=5)
    ax.set(xlim=(112,121.5),ylim=(32,41),xlabel='Longitude (°E)',ylabel='Latitude (°N)',title='Simulation units and observation sites' if stations is not None else '32 area-weighted representatives')
    ax.text(.04,.04,'3,641 source cells\n9.867 million ha',transform=ax.transAxes,fontsize=8)
    ax=fig.add_subplot(gs[1,:]);label(ax,'c')
    g=pd.read_csv(grace_path or P/'runs/grace_context/observed_domain_twsa.csv')
    g=g.rename(columns={'nominal_month':'month','observed_twsa_mm':'observed_twsa_mm'})
    g['date']=pd.to_datetime(g.month)
    monthly=g.set_index('date').observed_twsa_mm.asfreq('MS')
    ax.plot(monthly.index,monthly.values,color=GREY,linewidth=.8,label='Observed monthly TWS anomaly')
    rolling=monthly.rolling(12,min_periods=9).mean()
    ax.plot(rolling.index,rolling.values,color=BLUE,linewidth=1.6,label='12-month mean (≥9 observations)')
    ax.axhline(0,color='#9A9A9A',lw=.5)
    ax.set(ylabel='TWS anomaly (mm)',xlabel='Year',xlim=(monthly.index.min(),monthly.index.max()),
        xticks=pd.to_datetime(['2002-04-01','2008-01-01','2014-01-01','2020-01-01','2026-07-01']),
        xticklabels=['2002','2008','2014','2020','2026'])
    ax.legend(frameon=False,ncol=2,fontsize=7,loc='upper right')
    save(fig,'Figure_1_domain_and_observed_storage')

def policies():
    d=pd.read_csv(R/'regional_policy_annual_results.csv');d=d[d.period.eq('testing')]
    fig,axs=plt.subplots(2,2,figsize=(7.2,5.7));fig.subplots_adjust(hspace=.45,wspace=.4)
    for a,c in zip(axs.flat,'abcd'):label(a,c)
    for name,col,marker in [('Uniform',ORANGE,'s'),('Targeted',BLUE,'o')]:
        for v,ax,scale in [('grain_production_t',axs[0,0],1e6),('modeled_total_et_volume_m3',axs[0,1],1e9)]:
            rows=[]
            for cut in [0,.25,.5,.75]:
                key='conventional' if cut==0 else f'{name.lower()}_{int(cut*100)}pct'
                q=d[d.policy.eq(key)][v]/scale;rows.append((cut*100,q.mean(),q.std(ddof=1)))
            a=np.array(rows);ax.errorbar(a[:,0],a[:,1],yerr=a[:,2],color=col,marker=marker,capsize=2,label=name)
    axs[0,0].set(xlabel='Irrigation reduction (%)',ylabel='Annual dry grain (million t)',xticks=[0,25,50,75])
    axs[0,1].set(xlabel='Irrigation reduction (%)',ylabel='Crop + fallow ET (km³ yr⁻¹)',xticks=[0,25,50,75])
    axs[0,0].legend(frameon=False,fontsize=8)
    a=axs[1,0]
    for cut,m,col in [(25,'o',BLUE),(50,'s',GREEN),(75,'^',ORANGE)]:
        t=d[d.policy.eq(f'targeted_{cut}pct')].set_index('harvest_year');u=d[d.policy.eq(f'uniform_{cut}pct')].set_index('harvest_year')
        a.plot(t.index,(t.grain_production_t-u.grain_production_t)/1e6,marker=m,color=col,label=f'{cut}% cut',markersize=3)
    a.set(xlabel='Held-out harvest year',ylabel='Dry-grain advantage (million t)',xticks=[2014,2018,2022,2025]);a.axhline(0,color=GREY,lw=.5)
    a.legend(frameon=False,ncol=3,fontsize=7)
    a=axs[1,1]
    for name,col,marker in [('Uniform',ORANGE,'s'),('Targeted',BLUE,'o')]:
        for cut in [25,50,75]:
            v=d[d.policy.eq(f'{name.lower()}_{cut}pct')]
            x=v.modeled_total_et_volume_m3/1e9;y=v.grain_production_t/1e6
            a.scatter(x,y,s=10,color=col,marker=marker,alpha=.4)
            a.scatter(x.mean(),y.mean(),s=36,color=col,marker=marker,edgecolor='white',linewidth=.4)
            a.annotate(f'{cut}%',(x.mean(),y.mean()),xytext=(3,6 if name=='Targeted' else -11),textcoords='offset points',fontsize=7,color=col)
    a.set(xlabel='Crop + fallow ET (km³ yr⁻¹)',ylabel='Annual dry grain (million t)')
    save(fig,'Figure_3_policy_tradeoffs')
    means=d.groupby(['policy','reduction_fraction']).mean(numeric_only=True).reset_index()
    means.to_csv(P/'tables/conditional_policy_testing_means.csv',index=False)

def allocation():
    a=pd.read_csv(R/'frozen_training_allocation.csv');mapping=pd.read_csv(R/'all_source_cell_mapping.csv')
    fig,axs=plt.subplots(1,3,figsize=(7.2,3.8));fig.subplots_adjust(wspace=.35)
    for ax,cut,s in zip(axs,[.25,.5,.75],'abc'):
        h=a[a.reduction_fraction.eq(cut)].copy();h['allocated']=h.fraction*h.area_share
        q=h.groupby('representative_id').allocated.sum().rename('allocated_fraction')
        v=mapping.merge(q,on='representative_id',validate='many_to_one');x,y,z=grid(v,'allocated_fraction')
        im=ax.pcolormesh(x,y,z,cmap='cividis',vmin=0,vmax=1,shading='nearest',rasterized=True)
        ax.set(xlim=(112,121.5),ylim=(32,41),xlabel='Longitude (°E)',title=f'{int(cut*100)}% regional cut');label(ax,s)
    axs[0].set_ylabel('Latitude (°N)')
    fig.colorbar(im,ax=axs,pad=.035,shrink=.65,label='Mean assigned fraction of conventional quota')
    save(fig,'Figure_4_frozen_spatial_allocation')

def raw_observations():
    d=pd.read_csv(P/'source_snapshots/calibration/predictions/observation_comparisons.csv',low_memory=False)
    fig,axs=plt.subplots(2,3,figsize=(7.2,5.5));fig.subplots_adjust(hspace=.65,wspace=.42)
    for row,crop in enumerate(['wheat','maize']):
        for j,(variable,title,unit,scale) in enumerate([('lai','LAI','m² m⁻²',1),('biomass','Aboveground biomass','t ha⁻¹',1000),('et','Daily ET','mm d⁻¹',1)]):
            ax=axs[row,j];label(ax,'abcdef'[row*3+j]);q=d[(d.crop==crop)&(d.variable==variable)]
            sites=sorted(q.site.unique());values=[q[q.site==site].value.to_numpy()/scale for site in sites]
            bp=ax.boxplot(values,tick_labels=sites,patch_artist=True,showfliers=False,medianprops={'color':'black','linewidth':1})
            for box in bp['boxes']:box.set(facecolor=BLUE if crop=='wheat' else GREEN,alpha=.35,linewidth=.7)
            ax.tick_params(axis='x',labelrotation=45,labelsize=7)
            ax.set(ylabel=f'{title} ({unit})',title=crop.capitalize());ax.set_ylim(bottom=0)
    save(fig,'Figure_S1_observed_data_distributions')

def main():
    OUT.mkdir(parents=True,exist_ok=True);domain();policies();allocation();raw_observations()
    captions={
    'Figure_1_domain_and_observed_storage':'Figure 1. Operational lowland domain, rotation footprint and observed storage context. (a) Physical mapped wheat–maize area after the 2020 cropland-fraction correction; grey shading denotes the terrain/catchment domain. (b) Native simulation representatives and their source-cell footprint. (c) CSR RL06.3 observed total-water-storage anomalies relative to 2004–2009. Missing GRACE months remain gaps; the rolling mean requires at least nine observations in twelve calendar months. Total-water storage includes groundwater and other stores; the publication grid does not imply independent 0.25° observations.',
    'Figure_3_policy_tradeoffs':'Figure 3. Conditional uniform and targeted irrigation-policy performance in held-out climate years, 2014–2025. (a,b) Annual dry grain and total crop-plus-fallow evapotranspiration; error bars show interannual standard deviations across twelve modeled years, not parameter uncertainty or confidence intervals. (c) Paired annual grain advantage of targeting at identical field-irrigation volumes. (d) Annual grain–ET combinations; large symbols denote means and small symbols individual years. Allocations were frozen using 1997–2013 outputs.',
    'Figure_4_frozen_spatial_allocation':'Figure 4. Spatial distribution of mean assigned irrigation-quota fractions under targeted reductions. Within-representative crop-area mixtures use the five native simulated quotas. Colors map each representative allocation back to its source cells; they are representative-zone approximations rather than independently optimized 0.1° prescriptions. Regional reductions are exactly 25%, 50% and 75% of the fixed conventional field-irrigation volume.',
    'Figure_S1_observed_data_distributions':'Figure S1. Distributions of quality-screened observations used in the retrospective calibration and testing archive. Boxes denote medians and interquartile ranges; whiskers extend to 1.5 interquartile ranges and individual outliers are omitted from display only. Measurement counts and available years differ among sites. Soil-water observations are retained in source archives but are not presented as validated root-zone dynamics.'}
    (OUT/'captions.json').write_text(json.dumps(captions,indent=2))
    hashes={str(f.relative_to(P)):hashlib.sha256(f.read_bytes()).hexdigest() for f in OUT.glob('*')}
    (P/'verification/publication_figure_exports.json').write_text(json.dumps(hashes,indent=2))
    print('Exported four standalone scientific figures as PNG/PDF')

if __name__=='__main__':main()
