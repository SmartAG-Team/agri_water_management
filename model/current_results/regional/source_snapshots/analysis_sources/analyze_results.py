"""Regional sensitivity summaries, raw climate figures and publication exports."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

ROOT=Path(__file__).resolve().parents[1]
COLORS=['#595959','#C48A2C','#216B94']
LABELS=['Archived estimates','Screened, transferred allocation','Screened, reoptimized allocation']
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':9,'axes.labelsize':9,'xtick.labelsize':8,
                     'ytick.labelsize':8,'axes.linewidth':.7,'pdf.fonttype':42,'ps.fonttype':42})


def save(fig,name):
    for ax in fig.axes:
        for side in ['top','right','left','bottom']:ax.spines[side].set_visible(True)
        ax.tick_params(direction='in',width=.6)
    for ext in ['png','pdf']:fig.savefig(ROOT/f'figures/{name}.{ext}',dpi=350,bbox_inches='tight')
    return fig


def climate():
    weather=pd.read_csv(ROOT/'data/used_daily_weather.csv.gz')
    reps=pd.read_csv(ROOT/'data/representative_cells.csv')
    weather['year']=pd.to_datetime(weather.date).dt.year
    weather['month']=pd.to_datetime(weather.date).dt.month
    weather=weather[weather.year.ge(1997)]
    yearly=weather.groupby(['point','year'],as_index=False)[['precipitation_mm','reference_et0_mm']].sum()
    monthly=weather.groupby(['point','year','month'],as_index=False)[['precipitation_mm','reference_et0_mm']].sum()
    area=reps.set_index('representative_id').represented_area_ha
    for frame in [yearly,monthly]:
        frame['area_weight']=frame.point.map(area)/area.sum()
        for col in ['precipitation_mm','reference_et0_mm']:frame[col]*=frame.area_weight
    annual=yearly.groupby('year',as_index=False)[['precipitation_mm','reference_et0_mm']].sum()
    months=monthly.groupby(['year','month'],as_index=False)[['precipitation_mm','reference_et0_mm']].sum().groupby('month',as_index=False).mean()
    annual.to_csv(ROOT/'tables/raw_area_weighted_annual_climate.csv',index=False)
    months.drop(columns='year').to_csv(ROOT/'tables/raw_area_weighted_monthly_climate.csv',index=False)
    fig,axes=plt.subplots(1,2,figsize=(7.4,2.85),layout='constrained')
    for col,label,color,mark in [('precipitation_mm','Precipitation','#216B94','o'),('reference_et0_mm','Reference ET₀','#C48A2C','s')]:
        axes[0].plot(annual.year,annual[col],color=color,lw=1.25,marker=mark,ms=2.8,label=label)
        axes[1].plot(months.month,months[col],color=color,lw=1.25,marker=mark,ms=3,label=label)
    axes[0].axvline(2013.5,color='#888888',ls=':',lw=.9)
    axes[0].set(xlabel='Year',ylabel='Annual depth (mm)',xticks=[1997,2004,2011,2018,2025],xlim=(1996.5,2025.5))
    axes[1].set(xlabel='Month',ylabel='Mean monthly depth (mm)',xticks=[1,3,5,7,9,12],xlim=(.7,12.3))
    for ax,label in zip(axes,['(a)','(b)']):ax.text(.025,.95,label,transform=ax.transAxes,va='top',fontweight='bold')
    handles,labels=axes[0].get_legend_handles_labels()
    fig.legend(handles,labels,loc='outside lower center',ncol=2,frameon=False,fontsize=8)
    return save(fig,'raw_regional_climate')


def summaries():
    contrasts=pd.read_csv(ROOT/'tables/policy_contrasts.csv')
    rows=[]
    for (card,period,reduction,policy),g in contrasts.groupby(['parameter_set','period','reduction_fraction','policy']):
        rows.append(dict(parameter_set=card,period=period,reduction_fraction=reduction,policy=policy,n_years=len(g),
            mean_additional_grain_t=g.additional_grain_t.mean(),minimum_additional_grain_t=g.additional_grain_t.min(),
            maximum_additional_grain_t=g.additional_grain_t.max(),n_negative_grain_years=int(g.additional_grain_t.lt(0).sum()),
            n_grain_loss_years_over_1_t=int(g.additional_grain_t.lt(-1.).sum()),
            mean_additional_et_m3=g.additional_et_m3.mean(),minimum_additional_et_m3=g.additional_et_m3.min(),
            maximum_additional_et_m3=g.additional_et_m3.max(),mean_additional_drainage_m3=g.additional_drainage_m3.mean(),
            maximum_absolute_water_difference_m3=g.field_water_difference_m3.abs().max()))
    pd.DataFrame(rows).to_csv(ROOT/'tables/policy_advantage_summary.csv',index=False)
    changes=pd.read_csv(ROOT/'tables/allocation_changes.csv')
    reps=pd.read_csv(ROOT/'data/representative_cells.csv')
    changes=changes.merge(reps[['representative_id','represented_area_ha']],on='representative_id',validate='many_to_one')
    turnover=[]
    for reduction,g in changes.groupby('reduction_fraction'):
        # Total variation is the minimum land fraction whose treatment changes.
        turnover.append(dict(reduction_fraction=reduction,
            minimum_reassigned_area_ha=float((g.area_share_difference.abs()*g.represented_area_ha).sum()/2),
            minimum_reassigned_area_fraction=float((g.area_share_difference.abs()*g.represented_area_ha).sum()/2/reps.represented_area_ha.sum())))
    pd.DataFrame(turnover).to_csv(ROOT/'tables/allocation_turnover.csv',index=False)
    response=[]
    for card,path in [('archived',ROOT/'source_snapshots/archived_regional/all_season_summaries.csv'),
                      ('source_screened',ROOT/'predictions/all_season_summaries.csv')]:
        s=pd.read_csv(path);s=s[s.crop.isin(['wheat','maize'])]
        s['period']=np.where(s.harvest_year.le(2013),'training','testing')
        for (crop,period,fraction,year),g in s.groupby(['crop','period','fraction','harvest_year']):
            response.append(dict(parameter_set=card,crop=crop,period=period,fraction=fraction,harvest_year=year,
                yield_kg_ha=float(np.average(g.yield_kg_ha,weights=g.represented_area_ha)),
                et_mm=float(np.average(g.et_mm,weights=g.represented_area_ha))))
    pd.DataFrame(response).to_csv(ROOT/'tables/crop_quota_responses.csv',index=False)


def gains():
    data=pd.read_csv(ROOT/'tables/policy_contrasts.csv');data=data[data.period.eq('testing')]
    series=[('archived','archived_targeted'),('source_screened','archived_targeted'),('source_screened','refit_targeted')]
    fig,axes=plt.subplots(2,3,figsize=(7.4,4.7),sharex=True,sharey='row',layout='constrained')
    for j,reduction in enumerate([.25,.5,.75]):
        for k,(card,policy) in enumerate(series):
            g=data[data.parameter_set.eq(card)&data.policy.eq(policy)&data.reduction_fraction.eq(reduction)].sort_values('harvest_year')
            for i,(column,scale) in enumerate([('additional_grain_t',1e6),('additional_et_m3',1e9)]):
                axes[i,j].plot(g.harvest_year,g[column]/scale,color=COLORS[k],marker=['o','s','^'][k],ms=3,lw=1.2,
                    ls=['--',':','-'][k],label=LABELS[k])
        axes[0,j].set_title(f'{int(reduction*100)}% irrigation reduction',fontsize=9)
        for i in range(2):
            ax=axes[i,j];ax.axhline(0,color='#555555',lw=.7);ax.text(.035,.94,f'({chr(97+i*3+j)})',transform=ax.transAxes,va='top',fontweight='bold')
            ax.set(xlim=(2013.7,2025.3),xticks=[2014,2018,2022,2025])
        axes[1,j].set_xlabel('Harvest year')
    axes[0,0].set_ylabel('Additional dry grain (Mt)')
    axes[1,0].set_ylabel('Additional actual ET (km³)')
    handles,labels=axes[0,0].get_legend_handles_labels()
    fig.legend(handles,labels,loc='outside lower center',ncol=1,frameon=False,fontsize=8)
    return save(fig,'regional_policy_parameter_sensitivity')


def responses():
    data=pd.read_csv(ROOT/'tables/crop_quota_responses.csv')
    means=data.groupby(['parameter_set','crop','period','fraction'],as_index=False).mean(numeric_only=True)
    fig,axes=plt.subplots(1,2,figsize=(7.4,2.9),sharey=True,layout='constrained')
    for j,crop in enumerate(['wheat','maize']):
        for card,color in [('archived',COLORS[0]),('source_screened',COLORS[2])]:
            for period,line,marker in [('training','--','o'),('testing','-','s')]:
                g=means[means.parameter_set.eq(card)&means.crop.eq(crop)&means.period.eq(period)].sort_values('fraction')
                axes[j].plot(g.fraction*100,g.yield_kg_ha/1000,color=color,ls=line,marker=marker,ms=3.5,lw=1.3,
                    label=f'{"Archived" if card=="archived" else "Screened"}, {period}')
        axes[j].set(xlabel='Full-irrigation quota (%)',title=crop.capitalize(),xticks=[0,25,50,75,100],xlim=(-3,103))
        axes[j].text(.03,.94,f'({chr(97+j)})',transform=axes[j].transAxes,va='top',fontweight='bold')
    axes[0].set_ylabel('Dry grain yield (t ha⁻¹)')
    handles,labels=axes[0].get_legend_handles_labels()
    fig.legend(handles,labels,loc='outside lower center',ncol=2,frameon=False,fontsize=8)
    return save(fig,'crop_quota_parameter_sensitivity')


def workbook():
    sheets={
        'Policy_years':'tables/all_parameter_policy_years.csv','Policy_period_means':'tables/policy_period_means.csv',
        'Policy_contrasts':'tables/policy_contrasts.csv','Advantage_summary':'tables/policy_advantage_summary.csv',
        'Allocation_turnover':'tables/allocation_turnover.csv','Allocation_changes':'tables/allocation_changes.csv',
        'Frozen_refit_allocation':'parameters/frozen_training_allocation.csv','Quota_crop_responses':'tables/crop_quota_responses.csv',
        'Native_segments':'predictions/all_season_summaries.csv','Rotation_predictions':'predictions/rotation_summaries.csv',
        'Raw_annual_climate':'tables/raw_area_weighted_annual_climate.csv','Raw_monthly_climate':'tables/raw_area_weighted_monthly_climate.csv',
        'Representative_units':'data/representative_cells.csv','Mapped_source_cells':'data/all_source_cell_mapping.csv',
        'Used_daily_weather':'data/used_daily_weather.csv.gz','Calibration_observations':'data/calibration_provenance/used_observation_records.csv',
        'Calibration_partitions':'data/calibration_provenance/calibration_partitions.csv'}
    # Write-only worksheets bound memory even for all 350,656 forcing records.
    from openpyxl import Workbook
    book=Workbook(write_only=True)
    for name,path in sheets.items():
        frame=pd.read_csv(ROOT/path,low_memory=False)
        sheet=book.create_sheet(name);sheet.append(list(frame.columns))
        for row in frame.itertuples(index=False,name=None):
            sheet.append([None if pd.isna(v) else v for v in row])
    book.save(ROOT/'tables/regional_parameter_sensitivity.xlsx')


def main():
    if (ROOT/'verification/completion.json').exists():raise FileExistsError('Completed run is immutable')
    summaries();figures=[climate(),gains(),responses()]
    with PdfPages(ROOT/'figures/regional_sensitivity_atlas.pdf') as pdf:
        for fig in figures:pdf.savefig(fig,bbox_inches='tight');plt.close(fig)
    workbook()
    print('Three PNG/PDF figures, atlas and complete data workbook exported',flush=True)


if __name__=='__main__':main()
