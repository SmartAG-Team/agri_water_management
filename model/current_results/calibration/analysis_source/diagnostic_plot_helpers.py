"""Inspectable weather/profile diagnostics, observations, figures and provenance."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D

PROJECT = Path(__file__).resolve().parents[3]
WEATHER = PROJECT/'model/2026-10-06_field_weather_water_balance_ET'
PROFILE = PROJECT/'model/2026-10-06_antecedent_soil_profile_ET'
BLUE, ORANGE, GREY = '#2378a8', '#bb713c', '#595959'
plt.rcParams.update({'font.family':'DejaVu Sans', 'font.size':9,
    'axes.labelsize':9, 'axes.titlesize':9, 'xtick.labelsize':8,
    'ytick.labelsize':8, 'axes.linewidth':.7, 'lines.linewidth':1.3,
    'pdf.fonttype':42, 'ps.fonttype':42, 'figure.facecolor':'white'})


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def save(root, fig, name, caption):
    folder=root/'figures'
    folder.mkdir(exist_ok=True)
    fig.savefig(folder/(name+'.png'),dpi=300,bbox_inches='tight',facecolor='white')
    fig.savefig(folder/(name+'.pdf'),bbox_inches='tight',facecolor='white')
    plt.close(fig)
    (folder/(name+'_caption.txt')).write_text(caption+'\n')


def style(ax, number, title):
    ax.set_title(f'({chr(97+number)}) {title}',loc='left',pad=5)
    for spine in ax.spines.values():
        spine.set_visible(True)
    ax.tick_params(direction='out',length=3,width=.7)


def treatment_curves(root, frame, versions, labels, name, caption):
    fig,axes=plt.subplots(2,4,figsize=(11.4,5.5),sharey='row',layout='constrained')
    excluded_displayed = False
    for i,crop in enumerate(['wheat','maize']):
        for j,year in enumerate([2016,2017,2018,2019]):
            ax=axes[i,j]
            g=frame[frame.crop.eq(crop)&frame.harvest_year.eq(year)]
            observed=g.drop_duplicates('treatment').sort_values('treatment')
            x=np.arange(len(observed))
            eligible=observed.et_eligible.to_numpy(bool)
            ax.plot(x[eligible],observed.observed_et_mm.to_numpy()[eligible],color='black',marker='o',ms=4,lw=1.0)
            if (~eligible).any():
                excluded_displayed = True
                ax.plot(x[~eligible],observed.observed_et_mm.to_numpy()[~eligible],color=GREY,marker='o',mfc='white',ms=4,ls='none')
            for k,version in enumerate(versions):
                q=g[g.version.eq(version)].sort_values('treatment')
                assert len(q)==4
                ax.plot(x,q.predicted_et_mm,color=[BLUE,ORANGE][k],ls=['-','--'][k],marker=['s','^'][k],ms=3)
            ax.set_xticks(x,['W0','W1','W2','W3'])
            ax.set_ylim(0,650)
            style(ax,i*4+j,f'{crop.capitalize()} {year} · '+('calibration' if year<=2017 else 'testing'))
            if j==0:ax.set_ylabel('Seasonal actual ET (mm)')
    handles=[Line2D([],[],color='black',marker='o',label='Source ET (eligible)')]
    if excluded_displayed:
        handles.append(Line2D([],[],color=GREY,marker='o',mfc='white',ls='none',label='Source ET (excluded: inconsistent rainfall table)'))
    handles += [Line2D([],[],color=[BLUE,ORANGE][i],ls=['-','--'][i],marker=['s','^'][i],label=label)
                for i,label in enumerate(labels)]
    fig.legend(handles=handles,loc='outside lower center',ncol=2,frameon=False,fontsize=8)
    save(root,fig,name,caption)


def raw_observations(root):
    q=pd.read_csv(root/'data/wuqiao_used_seasonal_observations.csv')
    fig,axes=plt.subplots(2,3,figsize=(10,5.6),layout='constrained')
    for i,crop in enumerate(['wheat','maize']):
        g=q[q.crop.eq(crop)]
        for j,(variable,label,factor) in enumerate([
            ('et_mm','Source seasonal ET (mm)',1),
            ('soil_water_storage_initial_mm','Source initial 0–2 m storage (mm)',1),
            ('source_storage_change_mm','Source 0–2 m storage depletion (mm)',1)]):
            ax=axes[i,j]
            for t,marker in zip(['W0','W1','W2','W3'],['o','s','^','D']):
                a=g[g.treatment.eq(t)].sort_values('harvest_year')
                ax.plot(a.harvest_year,a[variable]*factor,color=GREY,marker=marker,ms=4,lw=.8,
                        mfc='white',label=t)
            ax.set_xticks([2016,2017,2018,2019])
            ax.set_xlabel('Harvest year');ax.set_ylabel(label)
            style(ax,i*3+j,crop.capitalize())
            if j==2:ax.axhline(0,color='0.7',lw=.6,zorder=0)
    fig.legend(*axes[0,0].get_legend_handles_labels(),loc='outside lower center',ncol=4,frameon=False)
    save(root,fig,'Raw_field_water_observations',
         'Published field observations for winter wheat and summer maize, 2016–2019. ET is inferred from the '
         '0–2 m soil-water balance; initial storage and depletion are source measurements. Lines connect annual '
         'treatment means and do not denote continuous monitoring. Source ET dispersion is unavailable. '
         'Maize 2016 ET remains excluded from quantitative accuracy scores because the printed maize rainfall '
         'is inconsistent with the annual rainfall table. Maize W0–W3 labels denote preceding wheat irrigation.')


def raw_station_dynamics(root):
    obs=pd.read_csv(root/'data/observations.csv',low_memory=False)
    cases=pd.read_csv(root/'data/case_inventory.csv').set_index('case_id')
    starts=pd.to_datetime(obs.case_id.map(cases.start_date))
    obs['das']=((pd.to_datetime(obs.window_start)-starts).dt.days+
                (pd.to_datetime(obs.window_end)-starts).dt.days)/2
    fig,axes=plt.subplots(2,3,figsize=(10.4,5.5),layout='constrained')
    for i,crop in enumerate(['wheat','maize']):
        for j,(variable,label,factor) in enumerate([
            ('lai','LAI (m² m⁻²)',1),('biomass','Aboveground dry biomass (t ha⁻¹)',.001),
            ('et','Daily actual ET (mm d⁻¹)',1)]):
            g=obs[obs.crop.eq(crop)&obs.variable.eq(variable)]
            for split,color,marker in [('calibration',BLUE,'o'),('validation',ORANGE,'^')]:
                q=g[g.split.eq(split)]
                axes[i,j].scatter(q.das,q.value*factor,s=6,color=color,marker=marker,
                                  alpha=.35,lw=0,label='Calibration' if split=='calibration' else 'Testing')
            axes[i,j].set_xlabel('Days after sowing');axes[i,j].set_ylabel(label)
            style(axes[i,j],i*3+j,crop.capitalize())
    fig.legend(*axes[0,0].get_legend_handles_labels(),loc='outside lower center',ncol=2,frameon=False)
    save(root,fig,'Raw_eligible_station_dynamics',
         'Eligible unaggregated station observations at all sites with each variable. Dates use the '
         'midpoint of the recorded sampling window relative to the matched crop sowing date. Calibration '
         'and testing retain whole site-years. Marker density reflects archive sampling intensity and '
         'does not give balanced site weights. Biomass denotes aboveground dry mass during crop growth; '
         'harvest endpoint measurements are distinct targets. Quality screening is unchanged.')


def weather_figures():
    digitized=pd.read_csv(WEATHER/'data/digitized_published_daily_weather.csv')
    baseline=pd.read_csv(WEATHER/'predictions/used_weather_audit.csv.gz')
    baseline=baseline[baseline.version.eq('baseline')].drop_duplicates(['year','date'])
    fig,axes=plt.subplots(4,2,figsize=(11.4,8.7),layout='constrained')
    for i,year in enumerate([2016,2017,2018,2019]):
        g=digitized[digitized.harvest_year.eq(year)].copy()
        b=baseline[baseline.year.eq(year)].sort_values('date')
        d=pd.to_datetime(g.date);bd=pd.to_datetime(b.date)
        axes[i,0].vlines(bd,0,b.original_rain_mm,color=BLUE,lw=.6,alpha=.7)
        axes[i,0].plot(d,g.raw_rain_mm,ls='none',marker='.',color=ORANGE,ms=2.5)
        axes[i,0].set_ylabel('Daily precipitation (mm)')
        axes[i,0].set_ylim(0,185)
        axes[i,1].plot(bd,b.original_temperature_midpoint_c,color=BLUE,lw=.9)
        axes[i,1].plot(d,g.mean_temperature_c,color=ORANGE,lw=.9,ls='--')
        axes[i,1].set_ylabel('Daily temperature (°C)');axes[i,1].set_ylim(-15,40)
        for j in range(2):
            style(axes[i,j],i*2+j,f'{year-1}/{year}')
            ticks=pd.to_datetime([f'{year-1}-11-01',f'{year}-02-01',f'{year}-05-01',f'{year}-08-01',f'{year}-10-01'])
            axes[i,j].set_xticks(ticks,['Nov','Feb','May','Aug','Oct'])
            axes[i,j].set_xlim(pd.Timestamp(f'{year-1}-10-01'),pd.Timestamp(f'{year}-10-10'))
    fig.legend(handles=[Line2D([],[],color=BLUE,label='Original gridded forcing'),
                        Line2D([],[],color=ORANGE,ls='--',label='Published figure extraction')],
               loc='outside lower center',ncol=2,frameon=False)
    save(WEATHER,fig,'Raw_published_weather_extraction',
         'Independent extraction of daily precipitation bars and mean-temperature curves from published '
         'supplementary Figure S1, compared with the original gridded model forcing. Figure-derived rainfall '
         'has approximately 0.38 mm pixel resolution and ±1 day date uncertainty; unresolvable rain bars '
         'are represented as zero in the extraction and are not measured zero precipitation. Temperature '
         'has approximately 0.11 °C pixel resolution. Gridded temperature is the minimum–maximum midpoint. '
         'The extracted curves are observational input reconstructions, not fitted crop outcomes.')
    fields=pd.read_csv(WEATHER/'predictions/field_weather_and_water_balance.csv')
    treatment_curves(WEATHER,fields,['baseline','paper_rain_temperature'],
        ['Frozen baseline','Published rain timing + mean-temperature reconstruction'],
        'Field_ET_weather_sensitivity',
        'Conditional seasonal actual ET sensitivity to published rainfall timing and mean temperature. '
        'Seasonal rain totals, ET0, crop parameters, soil, management and treatment-year partitions remain fixed. '
        'Reconstructed temperature extrema retain the gridded amplitude and are not measured extrema. '
        'The weather reconstruction was not selected by testing accuracy. Source ET uses the 0–2 m water '
        'balance with assumed negligible runoff and drainage. No field ET dispersion is available. '
        'Maize treatments denote preceding wheat irrigation, not different current-maize irrigation doses.')
    # Exact signed bridge: source storage mismatch minus modeled drainage and
    # all remaining measured-window/forcing boundary terms equals actual ET error.
    baseline=fields[fields.version.eq('baseline')&fields.et_eligible].copy()
    baseline['storage_term']=baseline.matched_source_storage_operator_mm-baseline.observed_et_mm
    baseline['drainage_term']=-baseline.full_drainage_mm
    baseline['other_term']=baseline.predicted_et_mm-baseline.observed_et_mm-baseline.storage_term-baseline.drainage_term
    assert np.allclose(baseline[['storage_term','drainage_term','other_term']].sum(axis=1),
                       baseline.predicted_et_mm-baseline.observed_et_mm,rtol=0,atol=1e-8)
    baseline.to_csv(WEATHER/'tables/ET_error_water_balance_bridge.csv',index=False)
    fig,axes=plt.subplots(2,1,figsize=(11.4,5.7),layout='constrained')
    for i,crop in enumerate(['wheat','maize']):
        g=baseline[baseline.crop.eq(crop)].sort_values(['harvest_year','treatment'])
        pos=np.zeros(len(g));neg=pos.copy();x=np.arange(len(g))
        for column,color,label in [('storage_term',GREY,'Source storage mismatch'),
                                   ('drainage_term',ORANGE,'Modeled drainage contribution'),
                                   ('other_term',BLUE,'Other interval/forcing terms')]:
            y=g[column].to_numpy();bottom=np.where(y>=0,pos,neg)
            axes[i].bar(x,y,bottom=bottom,width=.72,color=color,label=label)
            pos+=np.maximum(y,0);neg+=np.minimum(y,0)
        axes[i].plot(x,g.predicted_et_mm-g.observed_et_mm,'ko',ms=3,label='Actual ET error')
        spread=max(20.,float(max(pos.max(),-neg.min()))*.12)
        axes[i].set_ylim(float(neg.min())-spread,float(pos.max())+spread)
        axes[i].axhline(0,color='black',lw=.6)
        axes[i].set_xticks(x,[f'{int(y)}\n{t}' for y,t in zip(g.harvest_year,g.treatment)])
        axes[i].set_ylabel('Signed actual ET error (mm)');style(axes[i],i,crop.capitalize())
    fig.legend(*axes[0].get_legend_handles_labels(),loc='outside lower center',ncol=2,frameon=False)
    save(WEATHER,fig,'ET_error_water_balance_bridge',
         'Signed decomposition of baseline modeled actual ET minus source ET. Grey bars are the source '
         'apparent storage operator minus source ET; orange bars are negative modeled bottom drainage; '
         'blue bars combine presowing fluxes, forcing-total differences and nonsoil boundary storage. '
         'The signed contributions exactly sum to the black actual-ET error marker. Drainage is not '
         'counted as actual ET. Maize 2016 is excluded under the frozen source-quality rule. The diagnostic '
         'does not validate the source assumption of negligible drainage or infer measured drainage.')


def profile_figures():
    profiles=pd.read_csv(PROFILE/'tables/conditioned_soil_profiles.csv')
    fig,axes=plt.subplots(4,3,figsize=(9.6,9.1),sharey=True,sharex=True,layout='constrained')
    for i,treatment in enumerate(['W0','W1','W2','W3']):
        for j,year in enumerate([2017,2018,2019]):
            q=profiles[profiles.harvest_year.eq(year)&profiles.treatment.eq(treatment)].sort_values('layer')
            assert len(q)==8
            depth=(q.top_mm+q.bottom_mm)/20
            ax=axes[i,j]
            ax.plot(q.baseline_theta,depth,color=GREY,ls=':',label='Original initial profile')
            ax.plot(q.antecedent_theta,depth,color=ORANGE,ls='--',label='Antecedent crop + fallow')
            ax.plot(q.conditioned_theta,depth,color=BLUE,label='Conditioned profile')
            ax.axhspan(0,20,color='0.9',zorder=0)
            ax.set_ylim(200,0);ax.set_xlim(.05,.46)
            style(ax,i*3+j,f'{year} · {treatment}')
            if j==0:ax.set_ylabel('Soil depth (cm)')
            if i==3:ax.set_xlabel('Volumetric water content (m³ m⁻³)')
    fig.legend(*axes[0,0].get_legend_handles_labels(),loc='outside lower center',ncol=3,frameon=False,fontsize=8)
    save(PROFILE,fig,'Initial_soil_profile_priors',
         'Conditional initial soil-water profiles for winter wheat. The preceding maize crop and dated '
         'fallow interval provide an estimated vertical shape. Observed initial 0–2 m total storage is '
         'preserved by bounded adjustment below 20 cm; the shaded seedbed retains its estimated antecedent '
         'water content. All displayed profiles are model priors rather than observed layer measurements. '
         'The first experimental wheat year retains its original profile because preceding maize '
         'management is unavailable. Conditioning does not use current crop ET, biomass or yield outcomes.')
    frame=pd.read_csv(PROFILE/'predictions/field_profile_comparisons.csv')
    treatment_curves(PROFILE,frame,['baseline','antecedent_profile'],
                     ['Frozen baseline','Antecedent profile prior'],
                     'Field_ET_initial_profile_sensitivity',
                     'Seasonal actual ET under the original and antecedent soil-profile priors. Measured '
                     'initial total water, crop parameters, weather, irrigation and phenology parameters '
                     'are unchanged. Wheat 2016 and all maize cases are unchanged. Source ET error bars '
                     'are unavailable. Model profile shape is unmeasured; the comparison is a conditional '
                     'initialization sensitivity. Testing years were previously inspected. Maize '
                     'treatment labels denote preceding wheat irrigation.')


def export_workbook(root, kind):
    obs=pd.read_csv(root/'data/observations.csv',low_memory=False)
    tables={'Field_observations':pd.read_csv(root/'data/wuqiao_used_seasonal_observations.csv'),
            'Annual_yield_source':pd.read_csv(root/'data/wuqiao_digitized_annual_yields.csv'),
            'Case_inventory':pd.read_csv(root/'data/case_inventory.csv'),
            'Partitions':pd.read_csv(root/'data/partitions.csv')}
    for variable,q in obs.groupby('variable'):
        tables['Observed_'+variable]=q
    if kind=='weather':
        tables.update(Digitized_weather=pd.read_csv(root/'data/digitized_published_daily_weather.csv'),
                      Weather_extraction_audit=pd.read_csv(root/'tables/published_weather_digitization_audit.csv'),
                      Field_predictions=pd.read_csv(root/'predictions/field_weather_and_water_balance.csv'),
                      Field_metrics=pd.read_csv(root/'tables/weather_water_balance_metrics.csv'),
                      ET_contrasts=pd.read_csv(root/'tables/field_ET_contrasts.csv'),
                      Water_balance_bridge=pd.read_csv(root/'tables/ET_error_water_balance_bridge.csv'))
    else:
        tables.update(Initial_soil_profiles=pd.read_csv(root/'tables/conditioned_soil_profiles.csv'),
                      Field_predictions=pd.read_csv(root/'predictions/field_profile_comparisons.csv'),
                      Field_metrics=pd.read_csv(root/'tables/fixed_profile_metrics.csv'),
                      Antecedent_weather=pd.read_csv(root/'data/field_grid_weather_2015_2019.csv'))
    tables['Station_comparisons']=pd.read_csv(root/'predictions/unchanged_station_comparisons.csv',low_memory=False)
    tables['Station_metrics']=pd.read_csv(root/'tables/unchanged_station_metrics.csv')
    tables['Source_SHA256']=pd.DataFrame(json.loads((root/'verification/input_manifest.json').read_text()))
    with pd.ExcelWriter(root/'tables/validation_data_and_results.xlsx',engine='openpyxl') as w:
        for name,q in tables.items():
            q.to_excel(w,sheet_name=name[:31],index=False)
            sheet=w.sheets[name[:31]];sheet.freeze_panes='A2';sheet.auto_filter.ref=sheet.dimensions
    return len(tables)


def verify_and_report(root, kind):
    sources=json.loads((root/'verification/input_manifest.json').read_text())
    for entry in sources:
        assert sha(root/entry['snapshot'])==entry['sha256'],entry['snapshot']
    q=pd.read_csv(root/'predictions/unchanged_station_comparisons.csv',low_memory=False)
    assert len(q)==q.observation_id.nunique()==7397
    assert q.groupby('source_group_id').split.nunique().eq(1).all()
    assert q.site.nunique()==5
    metrics_name='weather_water_balance_metrics.csv' if kind=='weather' else 'fixed_profile_metrics.csv'
    m=pd.read_csv(root/'tables'/metrics_name)
    fields=pd.read_csv(root/'predictions'/('field_weather_and_water_balance.csv' if kind=='weather' else 'field_profile_comparisons.csv'))
    for row in m[m.variable.eq('seasonal_et')].itertuples():
        a=fields[fields.version.eq(row.version)&fields.crop.eq(row.crop)&fields.split.eq(row.split)&fields.et_eligible]
        o,p=a.observed_et_mm.to_numpy(),a.predicted_et_mm.to_numpy()
        assert abs(float(np.sqrt(np.mean((p-o)**2)))-row.rmse)<1e-8
    notes = (
        '## Published daily weather and field water accounting\n\n'
        'Published weather curves support an input-only sensitivity to rain-event timing and temperature. '
        'Rain extraction has approximately 0.38 mm pixel resolution and ±1 day date uncertainty. '
        'Some source seasonal rain totals disagree with the digitized bars. These discrepancies remain '
        'in the audit; source outcomes do not set extraction parameters. Conditional rain-shape runs '
        'retain the independently specified seasonal rain total. Temperature extrema are reconstructed '
        'with the original gridded amplitude, and reference ET0 remains fixed.\n\n'
        'Maize testing seasonal actual ET RMSE is 52.13 mm under the frozen baseline, '
        '38.06 mm with the published rain shape and 37.96 mm with rain and temperature together. '
        'The maize W3−W0 ET response error remains above 50 mm. Wheat testing RMSE increases from '
        '76.64 to 80.81 mm with combined forcing changes. Better maize aggregate agreement therefore '
        'does not establish adequate two-crop irrigation performance.\n\n'
        'Source ET is inferred from the 0–2 m soil-water balance with negligible runoff and drainage '
        'assumed. Modeled actual ET is transpiration plus soil and canopy evaporation. The apparent '
        'source storage operator is a diagnostic and is not an actual ET prediction. The signed '
        'water-accounting bridge isolates storage mismatch, modeled drainage and interval/forcing '
        'terms. Model drainage is unmeasured and cannot independently invalidate the source ET '
        'assumption. Maize treatment labels identify preceding wheat irrigation.\n\n'
        'All 132 station cases and 7,397 observations remain unchanged. Source snapshots, original '
        'input hashes, the 192 field simulations, weather reconstructions, exclusions, original '
        'observations and metric tables are retained. No candidate is promoted to regional scenarios.\n'
    ) if kind=='weather' else (
        '## Antecedent soil-water profile sensitivity\n\n'
        'The preceding maize crop and dated fallow weather provide estimated winter-wheat soil-water '
        'profiles. Measured initial total storage is preserved by bounded conditioning below 20 cm; '
        'estimated seedbed water remains unchanged. Layer-by-layer measurements are unavailable. '
        'Profiles are conditional priors, not measured root-zone water states. The first experimental '
        'year retains its original prior because preceding maize management is unavailable.\n\n'
        'Wheat testing seasonal ET RMSE decreases from 76.64 to 70.48 mm; calibration RMSE remains '
        '99.26 mm. Testing nRMSE is 18.52%, exceeding the working 15% criterion. Wheat biomass and '
        'yield are retained as separate checks; calibration harvest-biomass RMSE is 8.10 t ha⁻¹. '
        'The profile change alone does not resolve the crop water-use and growth errors.\n\n'
        'All maize crop runs, crop parameters, irrigation, weather and phenology parameters are '
        'unchanged. Observed ET, biomass and yield are not used to construct the profile. All '
        '132 station cases, 7,397 observations and site-year partitions remain unchanged. '
        'Soil and crop carbon budgets close within numerical tolerance. Testing years have previously '
        'been inspected. No candidate is promoted to regional scenarios.\n'
    )
    (root/'diagnostics.md').write_text(notes)
    sheets=export_workbook(root,kind)
    receipt=dict(source_snapshots_verified=len(sources),station_observations=7397,
        eligible_sites=5,source_metrics_independently_recomputed=True,
        raw_data_figures_retained=True,scientific_figures_PNG_and_PDF=True,
        workbook_sheets=sheets,testing_previously_inspected=True,regional_promoted=False,
        verified_at_utc=datetime.now(timezone.utc).isoformat())
    (root/'verification/diagnostic_packaging_receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(root.name,receipt,flush=True)


def main():
    weather_figures();profile_figures()
    for root,kind in [(WEATHER,'weather'),(PROFILE,'profile')]:
        raw_observations(root);raw_station_dynamics(root);verify_and_report(root,kind)


if __name__=='__main__':
    main()
