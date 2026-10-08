"""Apply cohesive manuscript structure, audited units and spatial statistics."""
from pathlib import Path
from copy import deepcopy
import json
import re

import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
PUB=ROOT/'publication'


def section(document,prefix):
    matches=[b for b in document['blocks'] if b.get('heading','').startswith(prefix)]
    if len(matches)!=1:raise ValueError(prefix)
    return matches[0]


def apply(article,supplement):
    article,supplement=deepcopy(article),deepcopy(supplement)
    proposal=json.loads((ROOT/'analysis_source/narrative_revision_20261008.json').read_text())
    for heading,paragraphs in proposal['paragraphs_by_heading'].items():
        if heading.startswith('2.3.3.'):
            section(article,'2.3.3.')['heading']=heading
        if heading == '4.6. Limitations and future directions' and not any(
                b.get('heading') == heading for b in article['blocks']):
            position=next(i for i,b in enumerate(article['blocks']) if b.get('heading')=='5. Conclusions')
            article['blocks'].insert(position,{'heading':heading,'level':2,'paragraphs':[]})
        section(article,heading)['paragraphs']=paragraphs
    from current_model_mechanism import original_figure_references
    visual=json.loads((PUB/'verification/visual_revision_20261008.json').read_text())
    normalize_caption=(original_figure_references if visual.get('main_figure_reference_numbering')==9 else lambda text:text)
    captions={b['figure']:normalize_caption(b['caption']) for b in visual['figures']}
    panel_receipt=PUB/'verification/panel_title_removal_20261008.json'
    if panel_receipt.exists():
        panels=json.loads(panel_receipt.read_text())
        normalize_caption=(original_figure_references if panels.get('main_figure_reference_numbering')==9 else lambda text:text)
        captions.update({b['figure']:normalize_caption(b['caption']) for b in panels['figures']})
    for document in [article,supplement]:
        for b in document['blocks']:
            if b.get('figure') in captions:b['caption']=captions[b['figure']]
    annual=pd.read_csv(ROOT/'regional/tables/regional_policy_annual_results.csv')
    testing=annual[annual.harvest_year.between(2014,2025)]
    means=testing.groupby('policy').mean(numeric_only=True)
    area=float(means.mapped_rotation_area_ha.iloc[0])
    c,u,t=[means.loc[n] for n in ['conventional','uniform_50pct','targeted_50pct']]
    grain_delta=(t.grain_production_t-u.grain_production_t)/area
    grain_mt=(t.grain_production_t-u.grain_production_t)/1e6
    et_delta=(t.modeled_total_et_volume_m3-u.modeled_total_et_volume_m3)/(area*10)
    et_km3=(t.modeled_total_et_volume_m3-u.modeled_total_et_volume_m3)/1e9
    gain_percent=100*(t.grain_production_t/u.grain_production_t-1)
    section(article,'Abstract')['paragraphs']=[
        'Efficient irrigation allocation is essential for sustaining grain production while limiting water consumption in the North China Plain wheat–maize rotation. '
        'How spatial targeting redistributes production losses, and whether regional water-storage information improves rainfall-based irrigation decisions, remain unresolved. '
        'Continuous process-based crop simulations compared uniform and targeted allocations under equal irrigation budgets and evaluated annual storage–rainfall strategies against matched rainfall-only controls during 2014–2025. Growth parameters were fitted to multisite observations; water-use parameters were calibrated against Wuqiao treatments in 2016–2018 and retrospectively tested in 2019. '
        f'At a 50% irrigation reduction, targeting retained {grain_delta:.2f} t ha⁻¹ yr⁻¹ more simulated combined dry grain than uniform cuts across {area/1e6:.2f} million rotation hectares, equivalent to {grain_mt:.2f} million t yr⁻¹ or {gain_percent:.2f}% of uniform production. Mean production was lower than under uniform cuts on 37.60% of mapped area, while crop-plus-fallow evapotranspiration increased by {et_delta:.2f} mm yr⁻¹ ({et_km3:.2f} km³ yr⁻¹). Across nine years with eligible Gravity Recovery and Climate Experiment (GRACE) observations, strategies selected at 95% and 98% grain targets retained 96.27% and 99.63% of conventional production, respectively. The 95% strategy reduced irrigation by 130.72 mm and evapotranspiration by 36.84 mm per rotation. Matched storage–rainfall and rainfall-only strategies selected identical quotas and produced identical outcomes. '
        'These conditional results support joint production and consumptive-use constraints and local production safeguards, while showing that additional storage information provides no decision benefit within the tested quota design.'
    ]
    # Detailed optimizer settings support reproduction in the supplement.
    parameterization=section(article,'2.5.')
    detail=parameterization['paragraphs'][1:3]
    section(supplement,'S2.4.')['paragraphs'].extend(detail)
    parameterization['paragraphs']=[
        'Phenological coefficients, stage-specific specific leaf area and senescence, initial LAI, stem-reserve coefficients and nutrition modifiers follow the crop parameterization described in Section S2. RUE, leaf allocation and grain-number parameters were estimated from 480 wheat and 433 maize growth and harvest observations across four wheat sites and five maize sites. Whole site-years retain their original partitions, with balanced contributions from variables, sites, site-years and crop cases. Station simulations retain their recorded site and cultivar parameterization; Wuqiao and regional simulations use the shared crop parameters (Table S3). Individual grain masses are fixed at 0.045 g for wheat and 0.300 g for maize.',
        'Eight water-use parameters per crop are fitted to the documented Wuqiao treatments in 2016–2018: the transpiration coefficient, soil evaporation coefficient, readily available water fraction, assimilation water-stress exponent, root-density decay, maximum rooting depth, root water-uptake coefficient and root compensation fraction. Each crop contributes 12 seasonal ET, 12 dry-grain and 12 dry-biomass targets, with three annual W3−W0 ET contrasts derived from the same treatment seasons. RUE, leaf allocation and grain-number parameters remain fixed. Bounded least squares combines scaled observation residuals and weak parameter priors; objective weights, bounds and convergence settings are specified in Section S2.4.',
        'The fitted water parameters are fixed before comparison with the four 2019 treatment means per crop. Previous inspection of these outcomes makes the test retrospective. Station observations have zero weight in this water fit and assess transfer under unconfirmed complete irrigation histories. Regional strategy selection uses historical weather responses; the crop-calibration years overlap the 2014–2025 regional comparison period, so those outcomes are conditional scenarios rather than time-independent crop validation. Observed and simulated quantities retain matched sampling windows and grain-moisture bases.'
    ]
    # Place the evidence partition graphic with its methods, before result figures.
    partition=next(b for b in article['blocks'] if b.get('figure')=='figures/current_Figure_3_observation_partitions.png')
    article['blocks'].remove(partition)
    blocks=article['blocks'];position=blocks.index(parameterization)+1;blocks.insert(position,partition)
    # Bring storage observations alongside the other environmental data.
    grace=section(article,'2.9.');blocks.remove(grace)
    grace['heading']='2.3.4. Terrestrial-water-storage observations';grace['level']=2
    position=blocks.index(section(article,'2.4.'));blocks.insert(position,grace)
    alloc=section(article,'2.7.');metrics=section(article,'2.8.');classes=section(article,'2.10.');adaptive=section(article,'2.11.');controls=section(article,'2.12.')
    for b in [alloc,metrics,classes,adaptive,controls]:blocks.remove(b)
    alloc['heading']='2.7.1. Spatial allocation under common irrigation budgets';alloc['level']=2
    # Calendar overlap is already defined in parameterization.
    alloc['paragraphs']=[alloc['paragraphs'][0],alloc['paragraphs'][2]]
    classes['heading']='2.7.2. Annual quotas based on antecedent water availability';classes['level']=2
    classes['paragraphs']=classes['paragraphs'][:3]+adaptive['paragraphs']
    controls['heading']='2.7.3. Rainfall-only controls and matched monitoring coverage';controls['level']=2
    metrics['heading']='2.8. Evaluation metrics, spatial statistics and water accounting'
    spatial=json.loads((ROOT/'analysis_source/spatial_analysis_narrative.json').read_text())
    metrics['paragraphs'].append(spatial['methods_paragraph'])
    regional=section(article,'2.6.')
    regional['paragraphs'][1]+=' Nutrition is represented by prescribed fertilized-crop modifiers; nitrogen cycling and spatially varying nutrient limitations are not dynamically simulated.'
    position=blocks.index(regional)+1
    blocks[position:position]=[{'heading':'2.7. Irrigation strategies and comparisons','level':1,'paragraphs':[]},alloc,classes,controls,metrics]
    # Results describe crop response, the regional balance, then its spatial distribution.
    station=section(article,'3.1.');station['paragraphs']=station['paragraphs'][1:]
    station['paragraphs'][0]='Seasonal comparisons distinguish canopy growth, biomass accumulation and water use across the station partitions (Figure 4). '+station['paragraphs'][0]
    field=section(article,'3.2.')
    field['paragraphs'][0]='Documented treatments provide the corresponding test of seasonal water use and harvest production. '+field['paragraphs'][0]
    paragraph=section(article,'3.3.')
    paragraph['paragraphs']=[
        f'At a 50% irrigation reduction, targeted allocation produced {t.grain_production_t/area:.3f} t ha⁻¹ yr⁻¹ of combined wheat–maize dry grain, compared with {u.grain_production_t/area:.3f} under uniform cuts. Both remained below conventional production of {c.grain_production_t/area:.3f} t ha⁻¹ yr⁻¹. Targeting therefore retained {grain_delta:.3f} t ha⁻¹ yr⁻¹ more grain than uniform allocation ({gain_percent:.2f}%), equivalent to {grain_mt:.3f} million t yr⁻¹ across the fixed {area/1e6:.3f} million rotation hectares (Table 4; Figure 6). This is a reduction in grain loss under constrained irrigation; targeted production remained {(c.grain_production_t-t.grain_production_t)/1e6:.3f} million t yr⁻¹ below conventional production.',
        f'Both allocations supplied {t.field_irrigation_m3/(area*10):.0f} mm yr⁻¹ of field irrigation. Targeted and uniform crop-plus-fallow ET were {t.modeled_total_et_volume_m3/(area*10):.2f} and {u.modeled_total_et_volume_m3/(area*10):.2f} mm yr⁻¹, respectively. The grain advantage accompanied {et_delta:.2f} mm yr⁻¹ ({et_km3:.3f} km³ yr⁻¹) greater consumption. Profile drainage was {t.bottom_drainage_volume_m3/(area*10):.2f} versus {u.bottom_drainage_volume_m3/(area*10):.2f} mm yr⁻¹, and runoff was {t.runoff_volume_m3/(area*10):.2f} versus {u.runoff_volume_m3/(area*10):.2f} mm yr⁻¹ (Table 4).',
        'The targeted-minus-uniform grain contrast at the 50% cut was positive in all twelve comparison years (Figure 6c). Its regional sign nevertheless masks differences among locations, which are resolved by the spatial distribution in Section 3.4.'
    ]
    old_spatial=proposal['paragraphs_by_heading']['3.3. Grain production and water balance under irrigation allocation'][2]
    spatial_block={'heading':'3.4. Spatial distribution and persistence of allocation effects','level':1,
        'paragraphs':[old_spatial.replace('The targeted-minus-uniform regional grain contrast was positive in all twelve years. ',''),
                      spatial['results_paragraphs'][0]+' (Figure 7d).',
                      spatial['results_paragraphs'][1],
                      spatial['results_paragraphs'][2],
                      spatial['results_paragraphs'][3]]}
    # Leave the spatial figure next to its own result section.
    spatial_figure=next(b for b in blocks if b.get('caption','').startswith('Figure 7.'))
    blocks.remove(spatial_figure)
    quota=section(article,'3.4.')
    position=blocks.index(quota);blocks[position:position]=[spatial_block,spatial_figure]
    quota['heading']='3.5. Antecedent water availability and annual irrigation strategies'
    quota['paragraphs'][0]='Annual quota selection adjusted the irrigation budget through time, complementing its spatial redistribution. '+quota['paragraphs'][0]
    # Per-hectare table units make the denominator visible throughout the results.
    order=['conventional','uniform_25pct','targeted_25pct','uniform_50pct','targeted_50pct','uniform_75pct','targeted_75pct']
    names={'conventional':'Conventional','uniform_25pct':'Uniform, 25% cut','targeted_25pct':'Targeted, 25% cut',
           'uniform_50pct':'Uniform, 50% cut','targeted_50pct':'Targeted, 50% cut','uniform_75pct':'Uniform, 75% cut','targeted_75pct':'Targeted, 75% cut'}
    rows=[]
    for name in order:
        r=means.loc[name]
        rows.append({'Strategy':names[name],'Dry grain (t ha⁻¹ yr⁻¹)':r.grain_production_t/area,
            'Irrigation (mm yr⁻¹)':r.field_irrigation_m3/(area*10),'ET (mm yr⁻¹)':r.modeled_total_et_volume_m3/(area*10),
            'Drainage (mm yr⁻¹)':r.bottom_drainage_volume_m3/(area*10),'Runoff (mm yr⁻¹)':r.runoff_volume_m3/(area*10)})
    table=pd.DataFrame(rows);table.to_csv(PUB/'tables/main_policy_water_balance.csv',index=False)
    b=next(b for b in blocks if b.get('table_caption','').startswith('Table 4.'))
    b.update(columns=list(table.columns),table_caption='Table 4. Simulated annual rotation production and water balance under equal irrigation reductions, 2014–2025.',
        table_note='All per-hectare means use the same 9.866602 million mapped rotation hectares. Dry grain combines wheat and maize harvests; ET includes crops and fallows. Regional grain in million tonnes equals rotation yield multiplied by mapped area in million hectares. Regional water in km³ equals depth in mm multiplied by hectares and 10, divided by 10⁹. The conventional schedule supplies 300 mm to wheat and 80 mm to maize.',
        precision={'Dry grain (t ha⁻¹ yr⁻¹)':3,'Irrigation (mm yr⁻¹)':2,'ET (mm yr⁻¹)':2,'Drainage (mm yr⁻¹)':2,'Runoff (mm yr⁻¹)':2},
        column_weights=[1.3,1.05,1.,.8,.9,.9])
    # Add the spatial-distribution inference and the absolute-yield scope to Discussion.
    discussion=section(article,'4.1.')
    discussion['paragraphs'].insert(2,'The spatial distribution sharpens this distinction between regional and local outcomes. Relative to uniform allocation, mean losses occupy 37.60% of mapped rotation area, and losses persist in every comparison year on 21.34%. Gross gains of 10.568 Mt yr⁻¹ are partly offset by losses of 3.349 Mt yr⁻¹; the area-weighted median is zero despite a positive regional mean. The regional grain objective balances gains and losses among locations. Local production floors constitute an additional allocation constraint.')
    reliability=section(article,'4.5.')
    reliability['paragraphs'].insert(2,'The absolute regional production reference also depends on the prescribed management and crop parameters. Conventional simulations produce 8.065 t ha⁻¹ of wheat dry grain and 8.691 t ha⁻¹ of maize dry grain per year. These values represent fixed irrigation dates, crop calendars and nutrition modifiers rather than an independently validated regional harvest inventory. The 0.732 t ha⁻¹ yr⁻¹ allocation advantage depends on the same simulated marginal responses; its regional scaling does not remove field-response uncertainty.')
    conclusions=section(article,'5.')
    conclusions['paragraphs'][0]=(
        f'At an identical 50% irrigation reduction, targeted allocation retained {grain_delta:.3f} t ha⁻¹ yr⁻¹ more simulated combined wheat–maize dry grain than uniform cuts during 2014–2025. This reduced regional grain loss by {grain_mt:.3f} million t yr⁻¹ ({gain_percent:.2f}% of uniform production), while increasing crop-plus-fallow ET by {et_delta:.2f} mm yr⁻¹ ({et_km3:.3f} km³ yr⁻¹). Relative to uniform allocation, mean grain losses occurred on 37.60% of mapped rotation area and persisted in all twelve years on 21.34%. Regional production retention therefore requires separate consumption and local production constraints.')
    # Avoid run-history vocabulary in the finished prose, preserving evidence roles.
    for document in [article,supplement]:
        for block in document['blocks']:
            for key in ['caption','table_caption','table_note','text']:
                if key in block:
                    block[key]=block[key].replace('current-parameter ','').replace('current crop-model ','crop-model ').replace('Current conditional ','Conditional ').replace('Current simulated ','Simulated ').replace('current conditional ','conditional ').replace('current selected ','selected ')
            block['paragraphs']=[p.replace('One frozen current parameter set','One shared parameter set').replace('current station comparisons','station comparisons').replace('current water fit','water-parameter fit') for p in block.get('paragraphs',[])]
    # Dataset attribution supplements the existing scientific reference set.
    file=PUB/'literature/supplemental_reference_records.json';records=json.loads(file.read_text())
    record={'id':'naturalearth_admin1','type':'map','title':'Natural Earth, 1:10m Admin 1 – States, Provinces (version 5.1.1)',
            'author':[{'literal':'Natural Earth'}],'URL':'https://www.naturalearthdata.com/downloads/10m-cultural-vectors/10m-admin-1-states-provinces/',
            'accessed':{'date-parts':[[2026,10,8]]},'note':'Public-domain cartographic dataset. Source version and license are frozen with the map inputs.'}
    records=[r for r in records if r['id']!='naturalearth_admin1']+[record]
    file.write_text(json.dumps(records,indent=2,ensure_ascii=False)+'\n')
    from revise_results_wording import apply as results_revision
    return results_revision(article,supplement)


def highlights():
    return ['Irrigation cuts require balancing grain retention, water use and local losses.',
            'Targeting added 0.732 t ha⁻¹ dry grain and 20.76 mm ET yearly over uniform 50% cuts.',
            'At 50% cuts, mean grain was below uniform allocation on 37.60% of the area.',
            'Over nine GRACE years, grain retention was 96.27% (95% target) and 99.63% (98%).',
            'Matched rainfall-only controls differed in grain production by 0% at both targets.']
