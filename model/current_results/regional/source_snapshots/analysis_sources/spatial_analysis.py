"""Spatial decomposition of sealed equal-budget irrigation scenarios.

No crop refitting, new simulations, spatial interpolation or groundwater inference.
"""
from pathlib import Path
import hashlib,json,shutil
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm,BoundaryNorm
from matplotlib.collections import PolyCollection
from pyproj import Transformer

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT.parent/'2026-10-04_regional_parameter_sensitivity'
PARAMETERS=['archived','source_screened']
CUTS=[.25,.5,.75]
VARIABLES=['yield_kg_ha','irrigation_mm','modeled_total_et_mm','annual_bottom_drainage_mm']
checks=[]

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,value):
    path=ROOT/path;path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')
def check(name,condition):
    checks.append({'check':name,'passed':bool(condition)})
    if not condition:raise AssertionError(name)

def prepare():
    if (ROOT/'verification/completion.json').exists():raise FileExistsError('Completed analysis is immutable')
    for folder in ['data','tables','figures','verification','source_snapshots']:(ROOT/folder).mkdir(exist_ok=True)
    receipt=json.loads((SOURCE/'verification/completion.json').read_text())
    check('Source sensitivity sealed and passed',receipt['all_checks_passed'] and not receipt['parameters_promoted'])
    manifest=json.loads((SOURCE/'verification/file_manifest.json').read_text())
    for name,item in manifest.items():check('Unchanged sensitivity source '+name,sha(SOURCE/name)==(item['sha256'] if isinstance(item,dict) else item))
    names={
        'data/all_source_cell_mapping.csv':'data/source_cells.csv',
        'data/representative_cells.csv':'data/representative_cells.csv',
        'predictions/rotation_summaries.csv':'data/screened_rotation_responses.csv',
        'source_snapshots/archived_regional/rotation_summaries.csv':'data/archived_rotation_responses.csv',
        'source_snapshots/archived_regional/frozen_training_allocation.csv':'data/archived_allocation.csv',
        'parameters/frozen_training_allocation.csv':'data/screened_allocation.csv',
        'tables/all_parameter_policy_years.csv':'data/regional_policy_reference.csv',
        'tables/allocation_turnover.csv':'data/regional_turnover_reference.csv',
        'data/used_daily_weather.csv.gz':'data/used_daily_weather.csv.gz',
        'data/used_hydraulic_profiles.json':'data/used_hydraulic_profiles.json',
        'data/calibration_provenance/used_observation_records.csv':'data/used_calibration_observations.csv',
        'parameters/used_crop_parameters.json':'source_snapshots/screened_crop_parameters.json',
        'source_snapshots/archived_regional/used_crop_parameters.json':'source_snapshots/archived_crop_parameters.json',
        'parameters/frozen_protocol.json':'source_snapshots/regional_protocol.json',
        'analysis_source/sensitivity.py':'source_snapshots/sensitivity.py',
        'verification/completion.json':'source_snapshots/source_completion.json',
        'verification/file_manifest.json':'source_snapshots/source_file_manifest.json'}
    inputs=[]
    for source,destination in names.items():
        path=ROOT/destination;shutil.copy2(SOURCE/source,path)
        inputs.append({'source':str((SOURCE/source).resolve()),'snapshot':destination,'sha256':sha(path)})
    write('verification/input_manifest.json',inputs)

def analyze():
    mapping=pd.read_csv(ROOT/'data/source_cells.csv');reps=pd.read_csv(ROOT/'data/representative_cells.csv')
    check('Unique 3641 source cells',len(mapping)==3641 and not mapping.zone_id.duplicated().any())
    check('32 distinct representatives',len(reps)==32 and not reps.representative_id.duplicated().any())
    check('Positive spatial support',mapping.used_area_ha.gt(0).all())
    areas=mapping.groupby('representative_id').used_area_ha.sum().sort_index()
    check('Represented areas reconcile',np.allclose(areas,reps.set_index('representative_id').represented_area_ha.sort_index(),rtol=1e-12))
    annual=[];unit=[]
    for label in PARAMETERS:
        prefix='screened' if label=='source_screened' else 'archived'
        response=pd.read_csv(ROOT/f'data/{prefix}_rotation_responses.csv')
        allocation=pd.read_csv(ROOT/f'data/{prefix}_allocation.csv')
        check(label+' complete rotation grid',len(response)==32*5*29 and not response.duplicated(['representative_id','fraction','harvest_year']).any())
        response=response[response.harvest_year.between(2014,2025)]
        for cut in CUTS:
            shares=allocation[allocation.reduction_fraction.eq(cut)]
            check(label+str(cut)+' complete area mixtures',np.allclose(shares.groupby('representative_id').area_share.sum(),1.,atol=1e-10))
            merged=response.merge(shares[['representative_id','fraction','area_share']],on=['representative_id','fraction'],validate='many_to_one')
            for rid,g in merged.groupby('representative_id'):
                q=shares[shares.representative_id.eq(rid)]
                quota=float(np.sum(q.fraction*q.area_share))
                h=g[g.fraction.eq(1-cut)].set_index('harvest_year')
                full=g[g.fraction.eq(1.)].set_index('harvest_year')
                for year,gy in g.groupby('harvest_year'):
                    item={'parameter_set':label,'reduction_fraction':cut,'representative_id':rid,'harvest_year':int(year),
                          'represented_area_ha':float(areas.loc[rid]),'targeted_mean_quota':quota}
                    for column in VARIABLES:
                        targeted=float(np.sum(gy[column]*gy.area_share));uniform=float(h.loc[year,column]);conventional=float(full.loc[year,column])
                        item['targeted_'+column]=targeted;item['uniform_'+column]=uniform
                        item['delta_'+column]=targeted-uniform;item['targeted_minus_conventional_'+column]=targeted-conventional
                    annual.append(item)
    annual=pd.DataFrame(annual)
    group=['parameter_set','reduction_fraction','representative_id']
    units=annual.groupby(group,as_index=False).mean(numeric_only=True).drop(columns='harvest_year')
    loss=annual.assign(loss=annual.delta_yield_kg_ha.lt(-1e-6),gain=annual.delta_yield_kg_ha.gt(1e-6)).groupby(group).agg(grain_loss_years=('loss','sum'),grain_gain_years=('gain','sum')).reset_index()
    units=units.merge(loss,on=group,validate='one_to_one')
    reference=pd.read_csv(ROOT/'data/regional_policy_reference.csv')
    for key,g in annual.groupby(['parameter_set','reduction_fraction','harvest_year']):
        label,cut,year=key;target='archived_targeted' if label=='archived' else 'refit_targeted'
        row=reference[(reference.parameter_set.eq(label))&reference.policy.eq(f'{target}_{int(cut*100)}pct')&reference.harvest_year.eq(year)].iloc[0]
        for col,ref,scale in [('targeted_yield_kg_ha','grain_production_t',.001),('targeted_irrigation_mm','field_irrigation_m3',10.),('targeted_modeled_total_et_mm','modeled_total_et_volume_m3',10.),('targeted_annual_bottom_drainage_mm','bottom_drainage_volume_m3',10.)]:
            check('Spatial totals reconcile '+str(key)+col,np.isclose(np.sum(g[col]*g.represented_area_ha)*scale,row[ref],rtol=2e-10,atol=.01))
        check('Equal regional irrigation contrasts '+str(key),abs(np.sum(g.delta_irrigation_mm*g.represented_area_ha)*10)<.01)
    old=pd.read_csv(ROOT/'data/archived_allocation.csv');new=pd.read_csv(ROOT/'data/screened_allocation.csv')
    joined=old.merge(new,on=['reduction_fraction','representative_id','fraction'],suffixes=('_old','_new'),validate='one_to_one')
    joined['minimum_changed_share']=abs(joined.area_share_new-joined.area_share_old)/2
    change=joined.groupby(['reduction_fraction','representative_id'],as_index=False).minimum_changed_share.sum()
    change=change.merge(areas.rename('represented_area_ha'),on='representative_id',validate='many_to_one')
    turnover=pd.read_csv(ROOT/'data/regional_turnover_reference.csv').set_index('reduction_fraction')
    for cut,g in change.groupby('reduction_fraction'):
        check('Spatial turnover reconciles '+str(cut),np.isclose(np.sum(g.minimum_changed_share*g.represented_area_ha),turnover.loc[cut,'minimum_reassigned_area_ha'],rtol=1e-10))
    selected=units[units.reduction_fraction.eq(.5)].drop(columns='reduction_fraction').pivot(index='representative_id',columns='parameter_set')
    selected.columns=[f'{param}_{column}' for column,param in selected.columns]
    selected=selected.reset_index().merge(change[change.reduction_fraction.eq(.5)][['representative_id','minimum_changed_share']],on='representative_id',validate='one_to_one')
    cells=mapping[['zone_id','representative_id','latitude','longitude','used_area_ha','profile_available_water_mm','training_mean_precipitation_mm','training_mean_et0_mm']].merge(selected,on='representative_id',validate='many_to_one')
    cells['training_climatic_deficit_mm']=cells.training_mean_et0_mm-cells.training_mean_precipitation_mm
    cells['screened_minus_archived_quota']=cells.source_screened_targeted_mean_quota-cells.archived_targeted_mean_quota
    cells['latitude_band']=pd.cut(cells.latitude,[32,36,38,41],right=False,labels=['Southern (32–36°N)','Central (36–38°N)','Northern (38–41°N)'])
    check('All cells have a reporting band',cells.latitude_band.notna().all())
    bands=[]
    for band,g in cells.groupby('latitude_band',observed=True):
        area=g.used_area_ha.sum();row={'latitude_band':str(band),'mapped_area_ha':area,'area_fraction':area/cells.used_area_ha.sum()}
        for column in ['training_mean_precipitation_mm','training_mean_et0_mm','training_climatic_deficit_mm','profile_available_water_mm','minimum_changed_share']:
            row[column]=float(np.average(g[column],weights=g.used_area_ha))
        for param in PARAMETERS:
            for col in ['targeted_mean_quota','delta_yield_kg_ha','delta_irrigation_mm','delta_modeled_total_et_mm','delta_annual_bottom_drainage_mm']:
                row[param+'_'+col]=float(np.average(g[param+'_'+col],weights=g.used_area_ha))
            row[param+'_grain_gain_t']=float(np.sum(g[param+'_delta_yield_kg_ha']*g.used_area_ha)/1000)
            row[param+'_extra_et_m3']=float(np.sum(g[param+'_delta_modeled_total_et_mm']*g.used_area_ha)*10)
        bands.append(row)
    bands=pd.DataFrame(bands)
    summary={'mapped_area_ha':float(cells.used_area_ha.sum()),'n_cells':len(cells),'n_simulated_units':len(reps),'testing_years':list(range(2014,2026))}
    for param in PARAMETERS:
        area=cells.used_area_ha;gain=cells[param+'_delta_yield_kg_ha'];et=cells[param+'_delta_modeled_total_et_mm']
        summary[param]={
            'grain_gain_million_t':float(np.sum(gain*area)/1e9),
            'extra_et_km3':float(np.sum(et*area)*10/1e9),
            'positive_mean_grain_area_pct':float(area[gain>1e-6].sum()/area.sum()*100),
            'negative_mean_grain_area_pct':float(area[gain< -1e-6].sum()/area.sum()*100),
            'grain_gain_and_extra_et_area_pct':float(area[(gain>1e-6)&(et>1e-6)].sum()/area.sum()*100),
            'all_twelve_years_grain_gain_area_pct':float(area[cells[param+'_grain_gain_years'].eq(12)].sum()/area.sum()*100),
            'positive_contribution_million_t':float(np.sum(gain.clip(lower=0)*area)/1e9),
            'negative_contribution_million_t':float(np.sum(gain.clip(upper=0)*area)/1e9)}
    summary['minimum_reassigned_area_pct']=float(np.average(cells.minimum_changed_share,weights=cells.used_area_ha)*100)
    summary['positive_mean_in_both_parameter_sets_area_pct']=float(cells.loc[(cells.archived_delta_yield_kg_ha>1e-6)&(cells.source_screened_delta_yield_kg_ha>1e-6),'used_area_ha'].sum()/cells.used_area_ha.sum()*100)
    for filename,frame in [('unit_annual_contrasts',annual),('unit_period_means',units),('source_cell_map_values',cells),('latitude_band_decomposition',bands),('allocation_change_by_unit',change)]:frame.to_csv(ROOT/f'tables/{filename}.csv',index=False)
    write('tables/spatial_findings.json',summary)
    return cells,summary

# Equal-area Lambert cylindrical geometry is constructed from the original
# 0.1-degree source-cell edges. There is no interpolation between units.
PROJECT=Transformer.from_crs('EPSG:4326','EPSG:6933',always_xy=True)
def geometry(cells):
    vertices=[]
    for row in cells.itertuples():
        lon=np.array([row.longitude-.05,row.longitude+.05,row.longitude+.05,row.longitude-.05])
        lat=np.array([row.latitude-.05,row.latitude-.05,row.latitude+.05,row.latitude+.05])
        x,y=PROJECT.transform(lon,lat);vertices.append(np.column_stack([x,y])/1000)
    return vertices
def frame(ax,letter,title):
    x,y=PROJECT.transform([112,121.5],[32,41]);ax.set_xlim(x[0]/1000,x[1]/1000);ax.set_ylim(y[0]/1000,y[1]/1000)
    lons=[113,116,119];lats=[33,36,39]
    xx,_=PROJECT.transform(lons,[36]*3);_,yy=PROJECT.transform([116]*3,lats)
    ax.set_xticks(np.array(xx)/1000,[f'{i}°E' for i in lons]);ax.set_yticks(np.array(yy)/1000,[f'{i}°N' for i in lats])
    ax.set_aspect('equal');ax.tick_params(direction='in',labelsize=8)
    for spine in ax.spines.values():spine.set_visible(True);spine.set_linewidth(.7)
    ax.text(0,1.045,f'({letter})',transform=ax.transAxes,weight='bold',fontsize=11)
    ax.set_title(title,fontsize=9,pad=8)
def maps(cells):
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':9,'pdf.fonttype':42,'savefig.dpi':450})
    verts=geometry(cells)
    specs={
        'Figure_5_spatial_policy_outcomes':[
            ('archived_targeted_mean_quota','Primary quota','Quota fraction',0,1,'cividis'),
            ('source_screened_targeted_mean_quota','Reoptimized quota','Quota fraction',0,1,'cividis'),
            ('source_screened_delta_yield_kg_ha','Grain difference','t ha⁻¹ yr⁻¹',None,None,'RdBu'),
            ('source_screened_delta_modeled_total_et_mm','ET difference','mm yr⁻¹',None,None,'RdBu'),
            ('minimum_changed_share','Allocation change','Area fraction',0,1,'viridis'),
            ('source_screened_grain_gain_years','Grain-gain years','Years (of 12)',0,12,'cividis')],
        'Figure_S25_spatial_parameter_comparison':[
            ('archived_delta_yield_kg_ha','Primary grain difference','t ha⁻¹ yr⁻¹',None,None,'RdBu'),
            ('source_screened_delta_yield_kg_ha','Screened grain difference','t ha⁻¹ yr⁻¹',None,None,'RdBu'),
            ('screened_minus_archived_quota','Quota change','Quota-fraction difference',-1,1,'RdBu'),
            ('archived_delta_modeled_total_et_mm','Primary ET difference','mm yr⁻¹',None,None,'RdBu'),
            ('source_screened_delta_modeled_total_et_mm','Screened ET difference','mm yr⁻¹',None,None,'RdBu'),
            ('source_screened_delta_annual_bottom_drainage_mm','Drainage difference','mm yr⁻¹',None,None,'RdBu')],
        'raw_climate_soil_maps':[
            ('training_mean_precipitation_mm','Mean precipitation','mm yr⁻¹',None,None,'viridis'),
            ('training_mean_et0_mm','Reference ET₀','mm yr⁻¹',None,None,'viridis'),
            ('training_climatic_deficit_mm','Climatic deficit','mm yr⁻¹',None,None,'viridis'),
            ('profile_available_water_mm','Available water (2 m)','mm',None,None,'viridis')]}
    exports={}
    # Matched difference panels use common symmetric scales across estimates.
    yield_limit=max(abs(cells.archived_delta_yield_kg_ha).max(),abs(cells.source_screened_delta_yield_kg_ha).max())/1000
    et_limit=max(abs(cells.archived_delta_modeled_total_et_mm).max(),abs(cells.source_screened_delta_modeled_total_et_mm).max())
    for name,panels in specs.items():
        rows=2;cols=len(panels)//2;fig,axes=plt.subplots(rows,cols,figsize=(7.8,7.3 if cols==3 else 7.1),layout='constrained')
        for ax,letter,spec in zip(axes.flat,'abcdef',panels):
            column,title,unit,vmin,vmax,cmap=spec;values=cells[column].to_numpy(float)
            if column.endswith('delta_yield_kg_ha'):values=values/1000;vmin,vmax=-yield_limit,yield_limit
            if column.endswith('delta_modeled_total_et_mm'):vmin,vmax=-et_limit,et_limit
            if 'delta_annual_bottom_drainage' in column:vmin,vmax=-abs(values).max(),abs(values).max()
            if vmin is None:vmin=float(values.min());vmax=float(values.max())
            norm=TwoSlopeNorm(vcenter=0,vmin=vmin,vmax=vmax) if vmin<0<vmax else matplotlib.colors.Normalize(vmin,vmax)
            if column.endswith('grain_gain_years'):norm=BoundaryNorm(np.arange(-.5,13.5,1),256)
            coll=PolyCollection(verts,array=values,cmap=cmap,norm=norm,edgecolors='none',rasterized=True)
            ax.add_collection(coll);frame(ax,letter,title)
            cb=fig.colorbar(coll,ax=ax,orientation='horizontal',pad=.045,fraction=.052,shrink=.95)
            cb.set_label(unit,fontsize=8);cb.ax.tick_params(labelsize=7)
            if column.endswith('grain_gain_years'):cb.set_ticks([0,3,6,9,12])
        for suffix in ['.png','.pdf']:
            target=ROOT/'figures'/(name+suffix);fig.savefig(target,bbox_inches='tight',facecolor='white')
            exports[target.name]={'sha256':sha(target),'n_map_panels':len(panels),'projection':'EPSG:6933','interpolation':False}
        plt.close(fig)
    write('verification/figure_exports.json',exports)
    features=[]
    keep=['archived_targeted_mean_quota','source_screened_targeted_mean_quota','source_screened_delta_yield_kg_ha','source_screened_delta_modeled_total_et_mm','minimum_changed_share','source_screened_grain_gain_years']
    for row in cells.to_dict('records'):
        x,y=row['longitude'],row['latitude'];ring=[[x-.05,y-.05],[x+.05,y-.05],[x+.05,y+.05],[x-.05,y+.05],[x-.05,y-.05]]
        features.append({'type':'Feature','geometry':{'type':'Polygon','coordinates':[ring]},'properties':{k:row[k] for k in ['zone_id','representative_id','used_area_ha']+keep}})
    write('data/spatial_policy_cells.geojson',{'type':'FeatureCollection','features':features})

def workbook():
    sheets={'Map_cell_values':'tables/source_cell_map_values.csv','Unit_annual_contrasts':'tables/unit_annual_contrasts.csv','Unit_period_means':'tables/unit_period_means.csv',
        'Latitude_band_results':'tables/latitude_band_decomposition.csv','Allocation_changes':'tables/allocation_change_by_unit.csv',
        'Archived_quota_responses':'data/archived_rotation_responses.csv','Screened_quota_responses':'data/screened_rotation_responses.csv',
        'Archived_allocations':'data/archived_allocation.csv','Screened_allocations':'data/screened_allocation.csv','Calibration_observations':'data/used_calibration_observations.csv'}
    with pd.ExcelWriter(ROOT/'tables/spatial_policy_analysis.xlsx',engine='openpyxl') as writer:
        for name,path in sheets.items():pd.read_csv(ROOT/path).to_excel(writer,sheet_name=name,index=False)

def main():
    prepare();cells,summary=analyze();maps(cells);workbook()
    write('verification/analysis_checks.json',{'all_checks_passed':True,'check_count':len(checks),'checks':checks})
    print(json.dumps(summary,indent=2))

if __name__=='__main__':main()
