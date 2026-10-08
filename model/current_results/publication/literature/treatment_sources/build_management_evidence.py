"""Build factual treatment transcriptions and an input-completeness audit.

Numeric values are manual transcriptions from the identified public primary
sources. This script does not simulate, fit, digitize figures, or modify a model.
"""
from pathlib import Path
import csv
import hashlib
import json
import re
from collections import Counter

BASE = Path(__file__).resolve().parents[2]
OUT = BASE / 'audit'
SRC = Path(__file__).resolve().parent
RETRIEVED = '2026-10-03'
ROWS = []

SOURCES = {
    'sun2006': {
        'id': 'sun2006', 'doi': '10.1016/j.agwat.2006.04.008',
        'title': 'Effects of irrigation on water balance, yield and WUE of winter wheat in the North China Plain',
        'journal': 'Agricultural Water Management', 'publication_date': '2006-09',
        'published_online': '2006-06-15',
        'url': 'https://www.researchgate.net/publication/330752682_Effects_of_irrigation_on_water_balance_yield_and_WUE_of_winter_wheat_in_the_North_China_Plain',
        'source_type': 'published_paper_public_author_upload',
        'author_upload_attribution': 'Hongyong Sun; 2019-01-31',
        'access': 'Full published paper readable in public author-upload page; direct PDF download returned 403.',
        'experiment_id': 'Luancheng_Sun_1999_2002',
        'site': 'Luancheng', 'latitude': 37+53/60, 'longitude': 114+41/60,
        'coordinate_scope': 'experimental_station; exact plot corners not reported',
        'experimental_unit': '5 m x 10 m field plot; concrete boundary extends 1.5 m below surface',
        'replication': 'A-D: 3 plots; E: 4 plots. Random assignment not stated.',
        'crop_seasons': ['1999/2000', '2000/2001', '2001/2002'],
        'claims_supported': ['15 treatment-season grain-yield means', '15 irrigation totals measured with water meter', '15 water-balance ET estimates and profile depletion means', 'seven-layer reported site bulk density, FC and WP', 'stage-specific controlled theta/FC ratios', 'monthly station rainfall'],
        'page_provenance': {'soil': 'Table 1, p212', 'rainfall': 'Table 2, p212', 'management_and_dates': 'Methods, p212', 'theta_fc_targets': 'Table 3, p213', 'irrigation_and_et': 'Table 4, p214', 'evaporation_transpiration': 'Table 5, p215', 'grain_yield': 'Table 6, p215'},
        'missing_input_dimensions': ['exact year-specific sowing dates', 'exact year-specific harvest dates', 'irrigation event dates and individual depths after establishment', 'depth and trigger semantics of controlled soil-water targets', 'initial volumetric water content by layer', 'original daily meteorology', 'saturation water content', 'saturated hydraulic conductivity', 'full retention/conductivity curves', 'root-depth observations', 'grain moisture correction basis', 'definition of reported plus/minus dispersion'],
        'data_limits': ['ET is inferred from observed inputs/profile depletion and estimated drainage, not an independent flux measurement.', 'Soil Table 1 reports site characteristics; FC/WP determination protocols and uncertainty are not specified.', 'Treatment E received 80 mm establishment/prewinter irrigation; it is not a zero-irrigation control.', 'The same 16 plots were followed across years; treatment-year means are not 15 independent experiments.', 'Three between-row microlysimeters are technical subsamples, not extra field-plot replicates.', 'A separate ResearchGate record has DOI .04.012 in metadata; the published paper and Crossref verify .04.008.'],
        'readiness': 'Observed response benchmark available; complete source-faithful daily event replay unavailable.'
    },
    'wang2025': {
        'id': 'wang2025', 'doi': '10.1016/j.agwat.2025.109792',
        'title': 'Optimized integrated soil-crop system management enhances crop yield while reducing water resource consumption',
        'journal': 'Agricultural Water Management', 'publication_date': '2025-10',
        'url': 'https://www.sciencedirect.com/science/article/pii/S0378377425005062',
        'source_type': 'open_access_primary_publisher_indexed_fulltext',
        'access': 'Public primary publisher text and Tables 2-3 readable in search index; direct publisher page/assets returned 403.',
        'experiment_id': 'Dawenkou_Wang_2022_2023',
        'site': 'Dawenkou', 'latitude': 36+11/60, 'longitude': 117+6/60,
        'coordinate_scope': 'research field; exact treatment plot corners not reported',
        'experimental_unit': '240 m2 plot (40 m x 6 m); 0.5 m buffer; randomized complete block design',
        'replication': '3 field plots per treatment; soil samples repeated thrice are technical measurements.',
        'crop_seasons': ['2022', '2023'],
        'claims_supported': ['12 maize treatment-season grain-yield means', '12 irrigation totals', '12 water-balance ET estimates', 'paired O-OPT1/OPT1 and O-OPT2/OPT2 irrigation-method comparisons with fixed N, density, tillage and harvest date', 'six-layer bulk density and field capacity in unspecified percent basis', 'treatment nitrogen rates and harvest dates in Fig. 2'],
        'page_provenance': {'yield': 'Table 2, section 3.3; PDF page unavailable', 'irrigation_et_wue': 'Table 3, section 3.8; PDF page unavailable', 'design': 'section 2.1', 'yield_measurement': 'section 2.3', 'water_balance': 'section 2.2'},
        'missing_input_dimensions': ['exact sowing dates; Fig. 2 shows harvest month/day only', 'calendar dates of stage fertilizer applications', 'supplementary 2023 irrigation event dates and depths', 'initial soil-water profile', 'field-capacity percentage basis', 'layer WP/Ksat', 'original daily weather series', 'numeric replicate SD/SE for yield and ET'],
        'data_limits': ['Other agronomic practices vary among the six treatment packages; only the corresponding optimized/unoptimized irrigation-method pairs isolate the intended method contrast at the package level.', 'ET is a soil-water-balance estimate; drainage and runoff are estimated.', 'The 2023 reported WUE values differ from ratios formed from published Table 2 yield and Table 3 ET means; both reported values are preserved.', 'No plot-level response data or numerical uncertainty were recovered.'],
        'readiness': 'Observed maize package/method contrasts available; daily source-faithful input replay incomplete.'
    },
    'ali2026_companion': {
        'id': 'ali2026_companion', 'doi': '10.1016/j.agwat.2026.110267',
        'title': 'Doubling gains, halting losses: Subsurface drip irrigation reshapes wheat-maize system sustainability',
        'journal': 'Agricultural Water Management', 'publication_date': '2026-04',
        'url': 'https://www.researchgate.net/publication/401621836_Doubling_gains_halting_losses_Subsurface_drip_irrigation_reshapes_wheat-maize_system_sustainability',
        'publisher_url': 'https://www.sciencedirect.com/science/article/pii/S0378377426001484',
        'source_type': 'published_open_access_paper_public_author_upload',
        'author_upload_attribution': 'Md Razzab Ali; 2026-03-06; CC BY 4.0',
        'access': 'Full published article readable on author-upload page; direct PDF download returned403. Normal public publisher supplement URL returned200 and TablesS1/S2 recovered.',
        'experiment_id': 'Luancheng_Ali_lysimeters_2022_2024',
        'site': 'Luancheng', 'latitude': None, 'longitude': None,
        'coordinate_scope': 'Luancheng station name verified; precise lysimeter plot coordinates unrecovered',
        'experimental_unit': '1 m2 lysimeter; three lysimeters per treatment, one weighing and two non-weighing',
        'replication': 'Yield/growth: 3 lysimeters per treatment. ET: 1 instrumented weighing lysimeter per treatment.',
        'crop_seasons': ['2022/2023 rotation', '2023 maize', '2023/2024 rotation', '2024 maize'],
        'claims_supported': ['6 observed maize grain-yield means', '6 annual wheat-plus-maize grain-yield totals', 'dated irrigation applications', '3 annual ET estimates from instrumented weighing lysimeters', 'treatment-specific nitrogen amounts', 'exact sowing dates', 'seven-layer BD/FC/WP from public supplementary Table S1', 'yield ANOVA P-values from supplementary Table S2'],
        'page_provenance': {'design_sowing': 'section 2.2, p2', 'lysimeter_soil': 'section 2.3, pp2-3', 'irrigation_and_fertilizer': 'Table 1, p4', 'annual_water_inputs_and_et': 'section 3.1, p5', 'grain_yield': 'section 3.2, pp5-6'},
        'missing_input_dimensions': ['exact harvest dates', 'initial soil water by layer', 'saturation/Ksat/retention parameters beyond FC/WP', 'original daily weather values; supplementary Fig. S2 is a plot only', 'plot-level yield observations and numerical dispersion', 'wheat grain-yield means not stated numerically in text', 'resolved annual SDIP irrigation discrepancy', 'confirmed lysimeter identities and soil origins'],
        'data_limits': ['Nitrogen rates, application methods and irrigation methods vary together; observed yield contrast cannot identify an irrigation-only effect.', 'ET has one instrumented unit per treatment; half-hourly records are repeated measurements.', 'Annual rotation totals reuse the maize observations and are not additional independent experiments.', 'Pre-experiment soil saturation for four months makes 2022/23 wheat a precharged-soil treatment, not a natural rainfed control.', 'Table 1 SDIP event sums (234.9 mm for the 2023/24 rotation) conflict with section 3.1 annual SDIP input (218 mm).', 'Table 1 lists 03.04.2024 topdressing for 2022/23 wheat, an unresolved season/date inconsistency.', 'Methods alternately describe undisturbed monoliths and backfilled soil; lysimeter depth is alternately 2 m and 1.8 m.', 'Potential experimental overlap with Ali DOI 10.1016/j.agwat.2026.110744 requires identification before joint pooling.'],
        'readiness': 'New observed coupled water-nitrogen maize and annual response benchmark; no irrigation-only validation or complete lysimeter replay.'
    },
    'yang2024': {
        'id': 'yang2024', 'doi': '10.1016/j.agwat.2024.108726',
        'title': 'Optimal irrigation for wheat-maize rotation depending on precipitation in the North China Plain: Evidence from a four-year experiment',
        'journal': 'Agricultural Water Management', 'publication_date': '2024-04',
        'url': 'https://www.researchgate.net/publication/378268040_Optimal_irrigation_for_wheat-maize_rotation_depending_on_precipitation_in_the_North_China_Plain_Evidence_from_a_four-year_experiment',
        'source_type': 'published_paper_public_author_upload',
        'author_upload_attribution': 'Yadong Yang; 2024-02-17',
        'access': 'Full published article readable on author-upload page; direct PDF unavailable. Normal public publisher supplement URL returned200 and TablesS1-S7 recovered.',
        'experiment_id': 'Wuqiao_Yang_2015_2019',
        'site': 'Wuqiao', 'latitude': 37+37/60+48/3600, 'longitude': 116+26/60+37/3600,
        'coordinate_scope': 'experimental station; plot corners not reported',
        'experimental_unit': '100 m2 field plot (10 m x 10 m); randomized complete block design; 1 m isolation',
        'replication': '3 field plots per treatment; paired-year figure n=6 combines 2 years x 3 plots.',
        'crop_seasons': ['2015/2016', '2016/2017', '2017/2018', '2018/2019'],
        'claims_supported': ['16 wheat and 16 maize observed aboveground dry-biomass means and SE', '32 crop-season ET/profile-storage means', 'exact sowing, harvest and irrigation dates in supplementary Table S1', 'eight four-year aggregate grain-yield treatment means in supplementary Table S5', 'constant N158.4 kg/ha per crop across water treatments'],
        'page_provenance': {'biomass': 'Table 1, pp5-6 in public text pagination; precise visual page unrecovered', 'irrigation_design': 'section 2.2, pp2-3', 'crop_management': 'section 2.2', 'measurements': 'section 2.3'},
        'missing_input_dimensions': ['original daily weather values; Fig. S1 is a plot', 'initial water by layer beyond total0-200cm storage', 'layer soil hydraulic parameters', 'season-specific grain yields beyond Fig. S2 graphical presentation'],
        'data_limits': ['Maize response reflects carryover of the wheat irrigation regime with common maize management; it is not a randomized maize irrigation-dose experiment.', 'No grain-yield or ET figure digitization was performed.', 'The same treatment plots were followed over four years; yearly means and four-year aggregates reuse the same plot observations.', '2015/16 maize precipitation in Table S3 yields a water-balance discrepancy of about100.6mm, and equals annual Table S4 precipitation; the source value is preserved.', 'Some stage ET estimates are negative; total ET and storage are water-balance estimates, not direct flux measurements.'],
        'readiness': 'Observed water-only wheat schedule, rotation carryover, biomass and ET benchmark with exact calendar/events; layer hydraulics and original daily weather remain incomplete.'
    }
}

def write_csv(path, rows, fieldnames=None):
    fieldnames = fieldnames or list(rows[0])
    with path.open('w', newline='', encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=fieldnames);w.writeheader();w.writerows(rows)

def add(paper, crop, season, year, treatment, **values):
    s=SOURCES[paper]
    row=dict(record_id=f'{paper}:{crop}:{season}:{treatment}', paper_id=paper,
             experiment_id=s['experiment_id'], source_doi=s['doi'], source_url=s['url'],
             retrieved_on=RETRIEVED, site=s['site'], latitude=s['latitude'], longitude=s['longitude'],
             coordinate_scope=s['coordinate_scope'], crop=crop, season=season, harvest_year=year,
             treatment=treatment, aggregation_level='treatment_crop_season_mean',
             quantity_origin='published_observed_treatment_mean', figure_digitized=False,
             source_location='', irrigation_mm=None, irrigation_basis='', irrigation_event_sum_mm=None,
             irrigation_events_json='', target_theta_fc_stages_json='',
             yield_kg_ha=None, yield_uncertainty_kg_ha=None, yield_uncertainty_type='not_reported',
             yield_significance_letters='', yield_moisture_basis='not_reported', yield_replicates=None,
             et_mm=None, et_uncertainty_mm=None, et_uncertainty_type='not_reported',
             et_significance_letters='', et_method='', et_field_units=None,
             biomass_kg_ha=None, biomass_uncertainty_kg_ha=None, biomass_uncertainty_type='not_reported',
             biomass_significance_letters='', biomass_replicates=None,
             station_rainfall_mm=None, effective_precipitation_mm=None,
             soil_water_depletion_mm=None, soil_water_depletion_uncertainty_mm=None,
             estimated_drainage_mm=None, evaporation_mm=None, derived_transpiration_mm=None,
             soil_water_storage_initial_mm=None, soil_water_storage_depth_cm=None,
             source_storage_change_mm=None, source_storage_change_convention='',
             source_yield_anova_p=None,
             reported_wue_kg_m3=None, reported_iwue_kg_m3=None, nitrogen_kg_ha=None,
             sowing_date='', harvest_date='', sowing_window='', harvest_window='',
             holdout_inventory_check='no_matching_site_crop_harvest_year_in_current_case_inventory',
             native_validation_status='not_run', uncertainty_flags='', candidate_use='')
    row.update(values);ROWS.append(row)

# Sun: measured soil parameters, ratio conversion is derived algebra, not a new observation.
soil_original=[(0,20,'sandy loam',1.41,36.4,9.6),(20,35,'sandy loam',1.51,34.9,11.4),
               (35,65,'light loam',1.47,33.3,13.9),(65,90,'medium loam',1.51,34.3,13.9),
               (90,145,'light clay',1.54,34.4,13.0),(145,170,'light clay',1.64,39,13.9),
               (170,190,'sandy clay',1.59,38.1,16.4)]
soil=[]
for top,bottom,texture,bd,fc,wp in soil_original:
    soil.append(dict(source_doi=SOURCES['sun2006']['doi'],source_url=SOURCES['sun2006']['url'],
                     source_location='Table 1, p212', quantity_origin='reported_site_soil_characteristics; FC/WP determination protocol unavailable',depth_top_cm=top,depth_bottom_cm=bottom,
                     texture=texture,bulk_density_g_cm3=bd,field_capacity_vol_percent=fc,
                     wilting_point_vol_percent=wp,field_capacity_m3_m3=fc/100,wilting_point_m3_m3=wp/100,
                     saturation_m3_m3=None,ksat_mm_day=None,
                     derived_theta_for_theta_fc_0_8_m3_m3=.8*fc/100,
                     derived_paw_remaining_fraction_for_theta_fc_0_8=round((.8*fc-wp)/(fc-wp),9),
                     derived_paw_remaining_fraction_for_theta_fc_1_0=1.0,
                     derived_conversion_formula='PAW_remaining=(q*FC-WP)/(FC-WP); q=theta/FC',
                     rootzone_aggregation_status='root/wetting depth and layer weights not reported'))
write_csv(SRC/'sun2006_soil_profile.csv',soil)
ali_soil=[]
for s in soil:
    a=dict(s);a['source_doi']=SOURCES['ali2026_companion']['doi']
    a['source_url']='https://ars.els-cdn.com/content/image/1-s2.0-S0378377426001484-mmc1.docx'
    a['source_location']='Supplementary Table S1; DOCX table1'
    a['quantity_origin']='reported_station_soil_characteristics; identical numeric profile to Sun2006; not a new independent measurement'
    ali_soil.append(a)
write_csv(SRC/'ali2026_soil_profile.csv',ali_soil)
wang_soil=[]
for top,bottom,texture,bd,fc in [(0,20,'medium loam',1.38,35.0),(20,40,'medium loam',1.46,30.2),(40,60,'medium loam',1.47,28.1),(60,80,'medium loam',1.58,25.9),(80,100,'light loam',1.74,23.0),(100,120,'light loam',1.79,22.8)]:
    wang_soil.append(dict(source_doi=SOURCES['wang2025']['doi'],source_url=SOURCES['wang2025']['url'],source_location='Table1 section2.1',depth_top_cm=top,depth_bottom_cm=bottom,texture=texture,bulk_density_g_cm3=bd,field_capacity_reported_percent=fc,field_capacity_basis='percentage basis not specified; do not assume gravimetric or volumetric',wilting_point_m3_m3=None,ksat_mm_day=None))
write_csv(SRC/'wang2025_soil_profile.csv',wang_soil)
targets={'A':[1,None,.8,.8,.8],'B':[1,.8,None,.8,.8],'C':[1,.8,.8,.8,None],
         'D':[1,1,1,1,1],'E':[1,None,None,None,None]}
stages=['winter_dormancy','recovering','stem_elongation','heading','grain_filling']
rules=[]
for treatment,vals in targets.items():
    for stage,q in zip(stages,vals):
        rules.append(dict(source_doi=SOURCES['sun2006']['doi'],source_url=SOURCES['sun2006']['url'],
                          source_location='Table 3, p213',treatment=treatment,growth_stage=stage,
                          controlled_theta_fc_ratio=q,irrigation_applied_at_stage=q is not None,
                          numerical_semantics='controlled soil moisture level; not an explicit daily depletion trigger',
                          event_date=None,event_depth_mm=None,wetting_depth_cm=None))
write_csv(SRC/'sun2006_stage_targets.csv',rules)
rainfall={
 '1999/2000':[20.7,6.1,.2,11.4,0,.8,2.5,11.8,0],
 '2000/2001':[53.7,8.4,0,8.1,6.9,.5,23.3,20.2,18.2],
 '2001/2002':[9.4,10,.4,1.2,0,5.5,30.1,45.7,6.3]}
rain=[]
for season,vals in rainfall.items():
    for month,value in zip([10,11,12,1,2,3,4,5,6],vals):
        year=int(season.split('/')[0 if month>=10 else 1])
        rain.append(dict(source_doi=SOURCES['sun2006']['doi'],source_url=SOURCES['sun2006']['url'],source_location='Table 2, p212',season=season,year=year,month=month,station_rainfall_mm=value))
write_csv(SRC/'sun2006_monthly_rainfall.csv',rain)
# I, profile depletion, depletion ±, drainage, ET, ET ±, ET letters, yield,
# yield ±, yield letters, WUE, evaporation, transpiration.
sun={
 '1999/2000':[
  [284.5,65.7,15.2,20.5,383.2,11.7,'bc',5467,179,'a',1.43,129.9,253.3],
  [323.9,61.1,5.8,17.1,421.4,20.6,'b',5487,252,'a',1.31,134.9,286.5],
  [247.9,82.9,19.9,10.8,373.4,21.4,'c',5584,151,'a',1.50,128.9,244.5],
  [404.8,42.2,12.6,36.5,464.0,35.1,'a',5306,64,'a',1.14,140.4,323.6],
  [80.0,66.5,18.5,8.0,192.0,16.5,'d',3552,172,'b',1.83,108.0,84.0]],
 '2000/2001':[
  [272.7,16.0,8.8,11.0,417.0,70.3,'a',4893,172,'a',1.19,135.7,281.3],
  [234.0,16.0,8.0,20.5,368.8,11.2,'a',4965,142,'a',1.33,138.1,230.7],
  [230.0,33.3,17.3,17.1,385.5,29.5,'a',5177,276,'a',1.34,137.7,247.8],
  [308.7,20.4,15.0,24.7,443.7,70.8,'a',4972,96,'a',1.12,143.0,300.7],
  [80.0,30.0,7.6,8.0,241.3,22.5,'b',3328,80,'b',1.38,108.7,132.6]],
 '2001/2002':[
  [249.3,58.5,10.3,18.6,397.9,36.4,'b',4299,174,'a',1.08,121.9,276.0],
  [247.5,71.1,20.9,20.7,406.4,16.1,'ab',4431,79,'a',1.09,121.1,285.3],
  [250.7,82.4,5.9,10.7,431.0,11.8,'ab',4417,165,'a',1.03,104.8,326.2],
  [354.1,13.7,5.3,31.4,444.9,25.5,'a',4333,137,'a',.97,139.9,305.0],
  [80.0,79.1,11.7,8.0,259.7,11.7,'c',3526,52,'b',1.36,97.9,161.9]]}
for season,vals in sun.items():
    for t,v in zip('ABCDE',vals):
        i,swd,swd_pm,drain,et,et_pm,et_sig,y,y_pm,y_sig,wue,e,trans=v
        add('sun2006','wheat',season,int(season[-4:]),t,
            source_location='Table 4 p214; Tables 5-6 p215; stage schedule Table 3 p213; soil Table 1 p212',
            irrigation_mm=i,irrigation_basis='season total measured with water meter; includes approximately 80 mm establishment/prewinter irrigation',
            target_theta_fc_stages_json=json.dumps(dict(zip(stages,targets[t])),separators=(',',':')),
            yield_kg_ha=y,yield_uncertainty_kg_ha=y_pm,yield_uncertainty_type='reported_plus_minus_unspecified',
            yield_significance_letters=y_sig,yield_replicates=4 if t=='E' else 3,
            et_mm=et,et_uncertainty_mm=et_pm,et_uncertainty_type='reported_plus_minus_unspecified',
            et_significance_letters=et_sig,et_method='water balance from measured I/P/profile depletion and estimated drainage; runoff/capillary rise ignored',et_field_units=4 if t=='E' else 3,
            station_rainfall_mm=round(sum(rainfall[season]),1),soil_water_depletion_mm=swd,
            soil_water_depletion_uncertainty_mm=swd_pm,estimated_drainage_mm=drain,
            evaporation_mm=e,derived_transpiration_mm=trans,reported_wue_kg_m3=wue,
            nitrogen_kg_ha=130,sowing_window='beginning of October; exact date unreported',harvest_window='mid-June; exact date unreported',
            uncertainty_flags='unresolved_pm_definition;missing_event_dates;missing_initial_profile;theta_fc_not_paw_fraction'+(';establishment_irrigated_control' if t=='E' else ''),
            candidate_use='unfitted treatment contrast and season-level yield/water-balance benchmark; not a complete daily input record')
write_csv(SRC/'sun2006_treatment_means.csv',[r for r in ROWS if r['paper_id']=='sun2006'])

# Wang: means and significance groups are numerical table facts.
wang={2022:[('CK',9714.9,'d',102.1,357,'f',2.7,9.5),('OPT1',12368.6,'c',102.1,417.8,'c',3.0,12.1),
            ('O-OPT1',13172.1,'b',68.8,369.1,'e',3.6,19.2),('HY',14555.8,'a',102.1,445.3,'a',3.3,14.3),
            ('OPT2',13741.9,'b',102.1,435.2,'b',3.2,13.5),('O-OPT2',14612.7,'a',68.8,387.2,'d',3.8,21.3)],
      2023:[('CK',10246.1,'e',185.4,349.5,'f',2.8,5.5),('OPT1',12949,'d',185.4,413.2,'c',3.0,7.0),
            ('O-OPT1',13730.1,'c',134.2,358.2,'e',3.6,10.2),('HY',15443.8,'a',185.4,440.2,'a',3.4,8.3),
            ('OPT2',14465.4,'b',185.4,422.8,'b',3.3,7.8),('O-OPT2',15232.9,'a',134.2,382.1,'d',3.8,11.4)]}
for year,vals in wang.items():
    for t,y,ys,i,et,es,wue,iwue in vals:
        nitrogen={'CK':225,'OPT1':160.5,'O-OPT1':160.5,'HY':450,'OPT2':184.5,'O-OPT2':184.5}[t]
        h={'CK':'09-20','OPT1':'10-01','O-OPT1':'10-01','HY':'10-05','OPT2':'10-05','O-OPT2':'10-05'}[t]
        add('wang2025','maize',str(year),year,t,source_location='Table 2 section 3.3; Table 3 section 3.8; PDF page unavailable',
            irrigation_mm=i,irrigation_basis='published Table 3 season total; water meter used',
            yield_kg_ha=y,yield_significance_letters=ys,yield_replicates=3,
            yield_moisture_basis='14 percent stated; section 2.3 wording also says oven dry to constant weight',
            et_mm=et,et_significance_letters=es,et_field_units=3,
            et_method='soil water balance 0-120 cm; estimated drainage/runoff; capillary rise zero',
            reported_wue_kg_m3=wue,reported_iwue_kg_m3=iwue,
            nitrogen_kg_ha=nitrogen,harvest_date=str(year)+'-'+h,
            uncertainty_flags='no_numeric_replicate_dispersion;missing_sowing_and_event_dates;ET_not_independent_flux;package_covariates_vary_across_unmatched_pairs'+(';reported_WUE_cross_table_ratio_discrepancy' if year==2023 else ''),
            candidate_use='paired O-OPT1/OPT1 and O-OPT2/OPT2 observed irrigation-method contrast; other package comparisons confounded')

# Yang: harvest aboveground dry biomass, not grain yield. SE and n=3 explicit.
yang={
 '2015/2016':{'wheat':[(15.7,.6,'c'),(14.8,.7,'c'),(18.1,.3,'b'),(20.7,.3,'a')],
              'maize':[(22.9,.3,'a'),(22.2,2.0,'a'),(21.6,1.1,'a'),(20.8,.7,'a')]},
 '2016/2017':{'wheat':[(17.2,.3,'c'),(19.8,.3,'b'),(26.2,.6,'a'),(25.5,.3,'a')],
              'maize':[(23.3,1.8,'a'),(22.9,.9,'a'),(23.3,.9,'a'),(22.4,.5,'a')]},
 '2017/2018':{'wheat':[(6.2,.3,'b'),(6.8,.3,'b'),(13.6,.4,'a'),(14.2,1.0,'a')],
              'maize':[(22.0,.9,'a'),(20.9,.8,'a'),(17.0,.7,'b'),(18.9,1.1,'ab')]},
 '2018/2019':{'wheat':[(4.8,.1,'c'),(6.6,.5,'b'),(13.8,.2,'a'),(13.8,.1,'a')],
              'maize':[(18.5,.9,'b'),(19.2,2.0,'ab'),(22.5,.4,'a'),(17.9,.2,'b')]}}
for season,crops in yang.items():
    for crop,vals in crops.items():
        for idx,(biomass,se,sig) in enumerate(vals):
            add('yang2024',crop,season,int(season[-4:]),'W'+str(idx),source_location='Table 1 pp5-6 in public text pagination; irrigation design section 2.2',
                irrigation_mm=75*idx if crop=='wheat' else None,
                irrigation_basis='derived nominal season total from 75 mm per prescribed wheat event; individual event records unrecovered' if crop=='wheat' else 'common maize irrigation across wheat treatments; exact maize season total unrecovered',
                biomass_kg_ha=biomass*1000,biomass_uncertainty_kg_ha=se*1000,
                biomass_uncertainty_type='standard_error',biomass_significance_letters=sig,biomass_replicates=3,
                nitrogen_kg_ha=158.4,
                uncertainty_flags='missing_exact_dates;missing_initial_profile;missing_soil_hydraulics'+(';maize_response_is_wheat_irrigation_carryover;maize_I_unknown' if crop=='maize' else ''),
                candidate_use='observed aboveground growth response with SE; not grain-yield/ET benchmark')

# Public Yang supplementary tables: parser retains means and letter groups.
yang_sup=json.loads((SRC/'yang2024_supplement_extracted.json').read_text())
def number(value):
    match=re.match(r'^(-?\d+(?:\.\d+)?)',value.strip())
    return float(match.group(1)) if match else None
def letters(value):return re.sub(r'^-?\d+(?:\.\d+)?','',value.strip())
yang_events=[]
calendars={
 '2015/2016':{'wheat_sow':'2015-10-21','wheat_harvest':'2016-06-10','pre':'2015-10-15','joint':'2016-04-10','anth':'2016-05-05','maize_harvest':'2016-10-03'},
 '2016/2017':{'wheat_sow':'2016-10-12','wheat_harvest':'2017-06-07','pre':'2016-10-06','joint':'2017-04-07','anth':'2017-05-01','maize_harvest':'2017-10-04'},
 '2017/2018':{'wheat_sow':'2017-10-15','wheat_harvest':'2018-06-07','pre':'2017-10-12','joint':'2018-04-10','anth':'2018-05-07','maize_harvest':'2018-10-04'},
 '2018/2019':{'wheat_sow':'2018-10-15','wheat_harvest':'2019-06-06','pre':'2018-10-12','joint':'2019-04-10','anth':'2019-05-07','maize_harvest':'2019-10-06'}}
yang_supp_url='https://ars.els-cdn.com/content/image/1-s2.0-S0378377424000611-mmc1.docx'
for crop,tb_index in [('wheat',1),('maize',2)]:
    season=None
    for tr in yang_sup['tables'][tb_index]:
        if re.match(r'^201\d-201\d$',tr[0]):season=tr[0].replace('-','/');continue
        if tr[0] not in ['W0','W1','W2','W3']:continue
        row=next(r for r in ROWS if r['paper_id']=='yang2024' and r['crop']==crop and r['season']==season and r['treatment']==tr[0])
        idx=int(tr[0][-1]);cal=calendars[season];year=row['harvest_year']
        if crop=='wheat':
            p,i,initial,change,et,wue=[number(tr[j]) for j in [4,5,6,7,15,16]]
            et_sig=letters(tr[15]);events=[{'date':cal[k],'mm':75,'stage':label} for k,label in list(zip(['pre','joint','anth'],['pre_sowing','jointing','anthesis']))[:idx]]
            sow=cal['wheat_sow'];harvest=cal['wheat_harvest'];location='Supplementary Table S2'
        else:
            p,i,initial,change,et,wue=[number(tr[j]) for j in range(1,7)]
            et_sig=letters(tr[5]);events=[{'date':str(year)+'-06-18','mm':75,'stage':'after_sowing'}] if i else []
            sow=str(year)+'-06-16';harvest=cal['maize_harvest'];location='Supplementary Table S3'
        row.update(irrigation_mm=i,irrigation_event_sum_mm=sum(e['mm'] for e in events),
                   irrigation_basis='observed scheduled amounts and dates in supplementary Table S1; total corroborated by '+location,
                   irrigation_events_json=json.dumps(events,separators=(',',':')),
                   et_mm=et,et_significance_letters=et_sig,et_field_units=3,
                   et_method='soil water balance from gravimetric0-200cm storage; runoff and drainage ignored',
                   station_rainfall_mm=p,soil_water_storage_initial_mm=initial,soil_water_storage_depth_cm=200,
                   source_storage_change_mm=change,source_storage_change_convention='positive=profile depletion for ET reconstruction; retain printed DeltaSWS sign',
                   soil_water_depletion_mm=change,reported_wue_kg_m3=wue,sowing_date=sow,harvest_date=harvest,
                   source_location=row['source_location']+'; '+location+'; calendar Table S1',
                   uncertainty_flags='no_numeric_ET_dispersion;missing_layer_initial_water;missing_soil_hydraulics;ET_not_independent_flux'+(';maize_is_wheat_irrigation_carryover' if crop=='maize' else '')+(';published_rainfall_ET_balance_conflict_100.6mm' if crop=='maize' and year==2016 else ''),
                   candidate_use='observed biomass and water-balance ET response with exact irrigation calendar; source layer hydraulics missing')
        for event in events:
            yang_events.append(dict(source_doi=SOURCES['yang2024']['doi'],source_url=yang_supp_url,source_location='Supplementary Table S1',crop=crop,season=season,treatment=tr[0],event_date=event['date'],growth_stage=event['stage'],irrigation_mm=event['mm']))
write_csv(SRC/'yang2024_observed_irrigation_events.csv',yang_events)
# Four-year averages are reported field outcomes, not independent extra experiments.
for crop,values in [('wheat',[(4934,'b'),(5506,'b'),(7781,'a'),(8015,'a')]),('maize',[(11865,'ab'),(12479,'a'),(11254,'b'),(11357,'ab')])]:
    for idx,(yield_value,sig) in enumerate(values):
        add('yang2024',crop,'2015/2016-2018/2019',None,'W'+str(idx),
            aggregation_level='four_year_treatment_mean_reuses_season_observations',source_location='Supplementary Table S5 average outputs',
            yield_kg_ha=yield_value,yield_significance_letters=sig,yield_replicates=3,yield_moisture_basis='13 percent grain moisture',nitrogen_kg_ha=158.4,
            holdout_inventory_check='no_Wuqiao_site_in_current_case_inventory',
            uncertainty_flags='four_year_aggregate_reuses_season_observations;no_numeric_yield_dispersion;not_season_specific',
            candidate_use='four-year irrigation-treatment grain-yield mean; compare only with matching four-year model aggregate')

# Ali: dated events and independent weighing-unit count are retained separately.
ali_events=[]
event_schedule={
 ('maize','2023','SDIP'):[('2023-06-22',73.4)],('maize','2023','FIL'):[('2023-06-21',80)],('maize','2023','CK'):[('2023-06-21',80)],
 ('wheat','2023/2024','SDIP'):[('2023-10-22',55),('2024-04-05',56),('2024-05-02',57),('2024-05-19',52)],
 ('wheat','2023/2024','FIL'):[('2023-10-22',80),('2024-04-05',80),('2024-05-03',80)],
 ('wheat','2023/2024','CK'):[('2023-10-22',80),('2024-04-05',80),('2024-05-03',80),('2024-05-19',80)],
 ('maize','2024','SDIP'):[('2024-06-20',14.9)],('maize','2024','FIL'):[('2024-06-22',60)],('maize','2024','CK'):[('2024-06-22',80)]}
for (crop,season,t),events in event_schedule.items():
    for n,(date,amount) in enumerate(events,1):
        ali_events.append(dict(source_doi=SOURCES['ali2026_companion']['doi'],source_url=SOURCES['ali2026_companion']['url'],source_location='Table 1 p4',crop=crop,season=season,treatment=t,event_number=n,event_date=date,irrigation_mm=amount,
                               irrigation_method='subsurface drip' if t=='SDIP' else 'flood',
                               transcription_limit='public indexed table column order; annual SDIP sum inconsistent with narrative'))
write_csv(SRC/'ali2026_observed_irrigation_events.csv',ali_events)
ali_calendar=[]
for crop,season,sow in [('wheat','2022/2023','2022-11-26'),('maize','2023','2023-06-20'),('wheat','2023/2024','2023-10-11'),('maize','2024','2024-06-20')]:
    for t in ['SDIP','FIL','CK']:
        if crop=='maize':
            basal_n={'SDIP':135,'FIL':157.5,'CK':225}[t];top_n=0
            basal_date=({'SDIP':'2023-06-23','FIL':'2023-06-18','CK':'2023-06-18'} if season=='2023' else {'SDIP':'2024-06-20','FIL':'2024-06-13','CK':'2024-06-13'})[t]
            top_date=''
        elif season=='2022/2023':
            basal_n=135;top_n=138;basal_date='2022-11-25';top_date='2024-04-03'
        else:
            basal_n={'SDIP':81,'FIL':94.5,'CK':135}[t];top_n={'SDIP':82.8,'FIL':96.6,'CK':138}[t]
            basal_date={'SDIP':'2023-10-22','FIL':'2023-10-10','CK':'2023-10-10'}[t];top_date='2024-04-05'
        ali_calendar.append(dict(source_doi=SOURCES['ali2026_companion']['doi'],source_url=SOURCES['ali2026_companion']['url'],source_location='section2.2 p2 sowing; Table1 p4 N dates/rates',crop=crop,season=season,treatment=t,sowing_date=sow,harvest_date=None,
                                 basal_N_kg_ha=basal_n,basal_N_date=basal_date,topdress_N_kg_ha=top_n,topdress_N_date_as_printed=top_date,
                                 N_total_kg_ha=round(basal_n+top_n,3),N_total_status='derived sum of printed doses; not an independently measured seasonal total',
                                 fertilizer_method='with irrigation water' if t=='SDIP' and not(crop=='wheat' and season=='2022/2023') else 'broadcast',
                                 date_consistency='printed_topdress2024_date_outside2022_2023_crop_season' if crop=='wheat' and season=='2022/2023' else 'no_calendar_conflict_identified',
                                 water_only_contrast_available=False))
write_csv(SRC/'ali2026_management_calendar.csv',ali_calendar)
ali_anova=json.loads((SRC/'ali2026_companion_supplement_extracted.json').read_text())['tables'][1]
anova_rows=[]
for tr in ali_anova[1:]:
    anova_rows.append(dict(source_doi=SOURCES['ali2026_companion']['doi'],source_url='https://ars.els-cdn.com/content/image/1-s2.0-S0378377426001484-mmc1.docx',source_location='Supplementary TableS2',crop_period=tr[2],yield_ANOVA_p=float(tr[3]),biomass_ANOVA_p=float(tr[4]),comparison='three coupled irrigation-nitrogen treatment packages',numeric_replicate_dispersion_available=False))
write_csv(SRC/'ali2026_observed_ANOVA.csv',anova_rows)
ali_yield={2023:{'SDIP':12.81,'FIL':11.64,'CK':12.00},2024:{'SDIP':13.54,'FIL':11.65,'CK':12.03}}
ali_effective_p={2023:{'SDIP':363.8,'FIL':366.1,'CK':360.6},2024:{'SDIP':252.7,'FIL':254.7,'CK':250.7}}
for year,vals in ali_yield.items():
    for t,y in vals.items():
        events=event_schedule[('maize',str(year),t)]
        add('ali2026_companion','maize',str(year),year,t,source_location='Table 1 p4 irrigation and nitrogen; section 3.2 pp5-6 yield',
            irrigation_mm=sum(e[1] for e in events),irrigation_event_sum_mm=sum(e[1] for e in events),
            irrigation_basis='dated single summer-maize irrigation event in Table 1',
            irrigation_events_json=json.dumps([{'date':d,'mm':v} for d,v in events],separators=(',',':')),
            yield_kg_ha=y*1000,yield_replicates=3,yield_moisture_basis='grain dry weight used in methods; exact reporting correction not recovered',
            effective_precipitation_mm=ali_effective_p[year][t],nitrogen_kg_ha={'SDIP':135,'FIL':157.5,'CK':225}[t],sowing_date=f'{year}-06-20',
            uncertainty_flags='water_N_and_method_covary;lysimeter_not_fieldplot;missing_initial_layers;no_numeric_yield_dispersion;potential_shared_trial_with_DOI_110744',
            candidate_use='new observed coupled management maize benchmark; cannot identify irrigation-only effect')
ali_annual={'2022/2023':{'SDIP':19.11,'FIL':17.74,'CK':18.33},'2023/2024':{'SDIP':22.06,'FIL':19.97,'CK':20.40}}
for season,vals in ali_annual.items():
    year=int(season[-4:])
    for t,y in vals.items():
        events=event_schedule[('maize',str(year),t)]+(event_schedule[('wheat',season,t)] if year==2024 else [])
        event_sum=round(sum(e[1] for e in events),1)
        annual_i={'SDIP':218,'FIL':300,'CK':400}[t] if year==2024 else event_sum
        add('ali2026_companion','wheat_maize_rotation',season,year,t,
            aggregation_level='annual_rotation_sum_reuses_crop_observations',
            source_location='section 3.2 pp5-6 annual yield; section 3.1 p5 annual ET/input; Table 1 p4 events',
            irrigation_mm=annual_i,irrigation_event_sum_mm=event_sum,
            irrigation_basis='reported annual water input in section 3.1; pre-experiment saturation excluded',
            irrigation_events_json=json.dumps([{'date':d,'mm':v} for d,v in sorted(events)],separators=(',',':')),
            yield_kg_ha=y*1000,yield_replicates=3,yield_moisture_basis='grain dry weight used in methods; exact reporting correction not recovered',
            et_mm={'SDIP':736,'FIL':796,'CK':804}[t] if year==2024 else None,
            et_method='annual water balance directly from weighing lysimeter' if year==2024 else '',
            et_field_units=1 if year==2024 else None,
            uncertainty_flags='rotation_total_reuses_maize_observation;water_N_and_method_covary;ET_instrumented_n1;missing_soil_layers'+(';SDIP_event_sum_234.9_vs_reported_annual_218' if year==2024 and t=='SDIP' else '')+(';preexperiment_four_month_saturation;wheat_topdress_date_inconsistent' if year==2023 else ''),
            candidate_use='annual coupled management summary; not an additional independent replicate; no irrigation-only attribution')

assert len(ROWS)==79
assert len({r['record_id'] for r in ROWS})==79
OUT.mkdir(parents=True,exist_ok=True)
write_csv(OUT/'observed_irrigation_treatments.csv',ROWS)

# Crossref-vetted additions for embedded CSL itemData; existing master exports unchanged.
additional=[]
for name in ['wang2025','ali2026_companion','fang2014']:
    m=json.loads((SRC/(name+'_crossref.json')).read_text())['message']
    c=dict(id=name,type='article-journal',title=m['title'][0],
           author=[{k:a[k] for k in ['given','family'] if k in a} for a in m['author']],
           issued=m['published'],DOI=m['DOI'],URL=m.get('URL','https://doi.org/'+m['DOI']),
           **{'container-title':m['container-title'][0]})
    for key in ['volume','issue','page','article-number']:
        if key in m:c[key]=m[key]
    if 'page' not in c and 'article-number' in c:c['page']=c['article-number']
    additional.append(c)
(SRC/'additional_treatment_references.json').write_text(json.dumps(additional,ensure_ascii=False,indent=2)+'\n')
ris=[]
bib=[]
for c in additional:
    year=c['issued']['date-parts'][0][0]
    record=['TY  - JOUR','ID  - '+c['id']]
    record += ['AU  - '+a['family']+', '+a.get('given','') for a in c['author']]
    record += ['TI  - '+c['title'],'JO  - '+c['container-title'],'PY  - '+str(year),
               'VL  - '+c.get('volume',''),'SP  - '+c.get('page',''),
               'DO  - '+c['DOI'],'UR  - '+c['URL'],'ER  - ']
    ris.append('\n'.join(record))
    fields={'author':' and '.join(a['family']+', '+a.get('given','') for a in c['author']),
            'title':c['title'],'journal':c['container-title'],'year':str(year),
            'volume':c.get('volume',''),'pages':c.get('page',''),'doi':c['DOI'],'url':c['URL']}
    bib.append('@article{'+c['id']+',\n'+',\n'.join('  '+k+' = {'+v+'}' for k,v in fields.items())+'\n}')
(SRC/'additional_treatment_references.ris').write_text('\n\n'.join(ris)+'\n')
(SRC/'additional_treatment_references.bib').write_text('\n\n'.join(bib)+'\n')

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
PROJECT=BASE.parents[1]
inventory=PROJECT/'model/2026-10-03_submission_package/source_snapshots/calibration/inputs/case_inventory.csv'
with inventory.open(encoding='utf-8-sig') as f:inv=list(csv.DictReader(f))
inventory_keys={(r['site'],r['crop'],r['cutting_date'][:4]) for r in inv if r.get('cutting_date')}
overlap_checks=[]
for paper in SOURCES:
    r=[r for r in ROWS if r['paper_id']==paper and r['aggregation_level']=='treatment_crop_season_mean']
    keys=sorted({(x['site'],x['crop'],str(x['harvest_year'])) for x in r})
    overlap_checks.append(dict(paper_id=paper,checked_site_crop_harvest_year=keys,matching_inventory_keys=[k for k in keys if k in inventory_keys]))
    assert not any(k in inventory_keys for k in keys)

sun_balance=[]
for r in ROWS:
    if r['paper_id']=='sun2006':
        estimate=r['irrigation_mm']+r['station_rainfall_mm']+r['soil_water_depletion_mm']-r['estimated_drainage_mm']
        sun_balance.append(dict(record_id=r['record_id'],et_reported_mm=r['et_mm'],et_reconstructed_mm=round(estimate,4),difference_mm=round(r['et_mm']-estimate,4)))
wang_ratios=[]
for r in ROWS:
    if r['paper_id']=='wang2025':
        ratio=r['yield_kg_ha']/(10*r['et_mm'])
        wang_ratios.append(dict(record_id=r['record_id'],reported_wue_kg_m3=r['reported_wue_kg_m3'],ratio_of_published_means_kg_m3=round(ratio,6),difference_kg_m3=round(r['reported_wue_kg_m3']-ratio,6),
                                interpretation='Not a correction: means of ratios may differ from ratio of means; replicate data unavailable.'))

local_paths=[
 'data/curated/irrigation/station_irrigation_events_wheat_maize.csv',
 'data/raw/external/shandong_agricultural_university_irrigation_2012_2014/metadata.json',
 'data/raw/external/cau_grass_maize_irrigation_2023_2024/metadata.json',
 'data/raw/external/luancheng_vadose_zone/metadata.json']
local=[]
for rel in local_paths:
    p=PROJECT/rel;local.append(dict(path=rel,sha256=sha(p),modified=False))

yang_balance=[]
for r in ROWS:
    if r['paper_id']=='yang2024' and r['et_mm'] is not None:
        calculated=r['irrigation_mm']+r['station_rainfall_mm']+r['source_storage_change_mm']
        yang_balance.append(dict(record_id=r['record_id'],et_reported_mm=r['et_mm'],et_reconstructed_from_printed_means_mm=round(calculated,3),difference_mm=round(r['et_mm']-calculated,3),status='published_P_ET_storage_inconsistency' if abs(r['et_mm']-calculated)>1.1 else 'consistent_with_rounding'))
contrasts=[]
for year,control,optimized in [(str(y),a,b) for y in [2022,2023] for a,b in [('OPT1','O-OPT1'),('OPT2','O-OPT2')]]:
    a=next(r for r in ROWS if r['paper_id']=='wang2025' and r['season']==year and r['treatment']==control)
    b=next(r for r in ROWS if r['paper_id']=='wang2025' and r['season']==year and r['treatment']==optimized)
    contrasts.append(dict(source_doi=a['source_doi'],source_url=a['source_url'],source_location='Tables2-3 means; Fig2 N/density/tillage/harvest',experiment_id=a['experiment_id'],crop='maize',season=year,control=control,comparison=optimized,N_both_kg_ha=a['nitrogen_kg_ha'],fixed_other_covariates='density,tillage,harvest,fertilizer doses/timing; irrigation method and amount vary',irrigation_difference_mm=round(b['irrigation_mm']-a['irrigation_mm'],3),yield_difference_kg_ha=round(b['yield_kg_ha']-a['yield_kg_ha'],3),ET_difference_mm=round(b['et_mm']-a['et_mm'],3),quantity_origin='difference of published observed treatment means',native_validation_status='not_run',difference_uncertainty='unavailable; no numerical replicate variance/covariance'))
write_csv(SRC/'wang2025_fixed_N_irrigation_contrasts.csv',contrasts)
wang_management=[]
for t,density,N,h,tillage in [('CK',60000,225,'09-20','straw cover; no till'),('OPT1',67500,160.5,'10-01','straw return; deep till'),('O-OPT1',67500,160.5,'10-01','straw return; deep till'),('HY',87000,450,'10-05','straw return; deep till'),('OPT2',75000,184.5,'10-05','straw return; deep till'),('O-OPT2',75000,184.5,'10-05','straw return; deep till')]:
    wang_management.append(dict(source_doi=SOURCES['wang2025']['doi'],source_url='https://ars.els-cdn.com/content/image/1-s2.0-S0378377425005062-gr2_lrg.jpg',source_location='Fig2 printed numerical labels; manual transcription without curve digitization',treatment=t,planting_density_ha=density,N_total_kg_ha=N,harvest_month_day=h,sowing_date=None,tillage=tillage,N_rate_origin='derived sum of printed stage doses; matching pair doses/timing visually identical',irrigation_method='micro-sprinkler' if t.startswith('O-') else 'border'))
write_csv(SRC/'wang2025_fixed_management.csv',wang_management)

audit={
 'retrieved_on':RETRIEVED,
 'scope':'Published observed irrigation-treatment responses and completeness for native crop-water comparisons.',
 'literature_scope':'Primary sources in Agricultural Water Management, Field Crops Research and Agricultural and Forest Meteorology; excluded publishers/journals were not actively opened or inspected.',
 'summary':{'rows':len(ROWS),'papers_with_numeric_observed_records':4,'experimental_programs':4,
            'sun_wheat_yield_I_ET_means':15,'wang_maize_yield_I_ET_means':12,'ali_maize_yield_I_means':6,
            'ali_annual_rotation_yield_means':6,'ali_annual_ET_means_instrumented_n1':3,'yang_biomass_means_with_SE':32,
            'yang_season_ET_and_storage_means':32,'yang_four_year_grain_yield_means':8,'wang_fixed_N_pair_year_contrasts':4,
            'grain_yield_rows':sum(r['yield_kg_ha'] is not None for r in ROWS),
            'ET_rows':sum(r['et_mm'] is not None for r in ROWS),
            'figure_digitized_rows':0,'simulated_rows':0,
            'native_irrigation_treatment_validation_completed':False},
 'statistical_unit':'Rows are treatment means. Four experimental programs, repeated crop years, rotation sums and technical subsamples must not be counted as independent experimental replicates.',
 'quantity_contract':{
   'irrigation_mm':'Source-reported season total, or explicitly marked nominal/event-sum derivation. Source disagreement is preserved.',
   'yield_kg_ha':'Published grain yield; t/ha converted by 1000. Annual rotation totals are distinct aggregation rows, not extra crop observations.',
   'biomass_kg_ha':'Published aboveground dry biomass; Mg/ha converted by 1000. Not grain yield.',
   'et_mm':'Source-observational water-balance estimate, not simulated ET. Method and independent instrumented/plot count stated.',
   'yield_uncertainty_type':'Sun plus/minus definition unreported; never relabeled SD or SE.',
   'biomass_uncertainty_type':'Yang Table 1 explicitly states standard error, three field-plot replicates.',
   'missing_values':'Blank cells mean unavailable/not applicable; never zero.',
   'significance_letters':'Within-source/year comparison groups; not a quantitative uncertainty estimate.',
   'effective_precipitation_mm':'Ali lysimeter effective precipitation; not station meteorological rainfall.'},
 'sources':list(SOURCES.values()),
 'native_trigger_conversion':{
   'source_ratio':'q=theta/FC', 'native_ratio':'PAW_remaining=(theta-WP)/(FC-WP)',
   'per_layer_conversion':'PAW_remaining_l=(q*FC_l-WP_l)/(FC_l-WP_l)',
   'rootzone_storage_conversion':'If a common q applies to all included root-zone layers: (sum(q*FC_l*dz_l)-sum(WP_l*dz_l))/(sum(FC_l*dz_l)-sum(WP_l*dz_l)). Apply actual root-depth overlap to dz_l; source root/wetting depth is unknown.',
   'q_0_8_layer_paw_range':[min(s['derived_paw_remaining_fraction_for_theta_fc_0_8'] for s in soil),max(s['derived_paw_remaining_fraction_for_theta_fc_0_8'] for s in soil)],
   'q_1_0_layer_paw':1.0,
   'interpretation':'Table 3 defines controlled stage soil moisture levels, not a daily PAW depletion threshold or a confirmed replenishment rule. Numeric substitution of 0.8 for native PAW is invalid.',
   'files':['literature/treatment_sources/sun2006_soil_profile.csv','literature/treatment_sources/sun2006_stage_targets.csv']},
 'unseen_case_inventory_audit':{
   'path':str(inventory.relative_to(PROJECT)),'sha256':sha(inventory),'case_count':len(inv),'checks':overlap_checks,
   'interpretation':'No matching site-crop-harvest-year appears in the current 132-case inventory. This establishes inventory separation only. Prior model-development exposure to these publications, parameter freezing, experiment identity, and source-complete input construction require confirmation before an independent holdout claim.'},
 'weather_status':{
   'source_daily_station_weather_recovered':False,
   'parent_authorized_weather_approximation':'Parent reports local AgERA5 1996-2025 available. Reanalysis is a weather approximation, not the original experimental meteorology.',
   'sun_station_monthly_rainfall_available':True,
   'constraint':'Do not invent irrigation dates. Approximate sowing/harvest dates or reanalysis drivers require explicit scenario labels and sensitivity analysis; no exact daily observed-management replay is supported.'},
 'numerical_consistency_checks':{'sun_water_balance':sun_balance,'yang_water_balance_from_printed_tables':yang_balance,'wang_reported_wue_vs_ratio_of_table_means':wang_ratios,
   'ali_SDIP_2023_2024':{'event_sum_mm':234.9,'narrative_annual_irrigation_mm':218,'difference_mm':16.9,'status':'unresolved_source_conflict; values preserved'}},
 'additional_priority_sources':[
   {'doi':'10.1016/j.agwat.2026.110744','citation_id':'ali2026_soil_trigger','evidence':'Luancheng2023-2025 experiment in abstract; 38-year RZWQM soil-depletion optimization is simulation. Public supplement recovered; TableS1 is calibrated crop parameters, not observed treatment values.','access_status':'Full article public download403; normal public supplement URL200. Supplement contains location/instrument figure, daily-weather plot and calibrated crop parameters only. No original daily values, management calendar or observed seasonal response means. No author contact.','public_supplement_url':'https://ars.els-cdn.com/content/image/1-s2.0-S0378377426006256-mmc1.docx','response_rows_in_csv':0,'overlap':'Potentially shares2023-2024 lysimeter experiment with DOI110267; do not pool as independent trial without identities.'},
   {'doi':'10.1016/j.agwat.2025.110038','citation_id':'fan2026_rotation_deficit','evidence':'AquaCrop deficit-irrigation rotations and optimized scenarios; not a directly recovered observed treatment data table.','response_rows_in_csv':0,'access_status':'Primary indexed article and software/supplement description recovered; original observed input/response dataset not found.'},
   {'doi':'10.1016/j.fcr.2021.108364','citation_id':'yang2022_long_term_irrigation','evidence':'16-year Luancheng 2003-2018 experiment; abstract gives annual ET 427.3 mm for W0M0 and 891.0 mm for W4M3, WP 2.4 and 1.6 kg/m3. Absolute treatment grain-yield means and ET method not recovered.','journal':'Field Crops Research','response_rows_in_csv':0,'access_status':'Public institution/publisher abstract; full text and supplement not recovered.','limits':'Aggregate 16-year abstract endpoints cannot substitute for season-matched observed treatments. 75 mm per nominal irrigation event does not establish event dates or actual annual amounts. May share observed plots with Wang2023.'},
   {'doi':'10.1016/j.agwat.2023.108229','citation_id':'wang2023_irrigation_gap','evidence':'Local public author/institution PDF recovered. Methods report period-mean observed wheat yield6096(2010-11),6360(2012-13)kg/ha; maize7391,8998kg/ha. Monthly ET means45/40(wheat),70/94(maize)mm/month. Irrigation schedule12scenario table is simulation.','response_rows_in_csv':0,'access_status':'Published PDF https://edepot.wur.nl/588936','limits':'Period means mix crop years and changing irrigation schedules, exact maize70(60)year mapping unclear, original matched events/soil profile supplement not recovered. Not a treatment-response benchmark; not complete-season observed ET.'},
   {'doi':'10.1016/j.agwat.2022.107468','citation_id':'ren2022_water_balance','evidence':'Regional SEBS/Budyko/cropping-area scenario estimates rather than observed randomized irrigation treatment responses.','response_rows_in_csv':0},
   {'doi':'10.1016/j.agrformet.2014.04.009','evidence':'Public published author PDF recovered. Yucheng2003-2005 field EC and lysimeter PET observations; broad planting dates given, seasonal crop model parameters calibrated using same-period LAI/yield/soil water.','public_fulltext':'https://papers.agrivy.com/webfiles/papers/2014-AFM-FANG-QUANXIAO.pdf','response_rows_in_csv':0,'limits':'No randomized paired irrigation response means or event-depth table recovered; fitted soil/model parameter columns are not measured hydraulics. EC energy closure about72 percent before correction. Observed mean PET445.9mm wheat336.0mm maize is not a native irrigation-response holdout.'}
 ],
 'local_dataset_audit':{
   'read_only_snapshots':local,
   'station_irrigation_events':{'rows':216,'all_existing_QC_flags_false':True,'stations':{'Shangqiu':{'rows':23,'event_date_range':['2000-02-26','2006-06-10']},'Fengqiu':{'rows':62,'event_date_range':['2004-03-11','2008-06-27']},'Luancheng':{'rows':110,'event_date_range':['2003-10-04','2008-07-22']},'Yucheng':{'rows':21,'event_date_range':['2003-12-28','2006-08-10']}},'limitation':'Crop-year labels and plot matching to yield/water-profile observations remain unconfirmed. False QC flags do not establish a matched irrigation-response experiment.'},
   'archived_man_figshare':{'doi':'10.6084/m9.figshare.1515106.v1','status':'Archival local metadata retained unchanged; associated methods paper outside allowed journal scope was not actively opened or inspected; excluded from new benchmark.'},
   'cau_grass_maize':{'doi':'10.17632/fzjkfywrnz.1','status':'Triticale-vetch-maize rotation with water/N co-treatments; eligible primary methods paper not linked or verified. Does not establish a wheat-maize benchmark.'},
   'luancheng_vadose_zone':{'doi':'10.17632/b5fvw87kk4.1','paper_doi':'10.1029/2022WR032965','status':'Hydraulic/precipitation/irrigation dataset supports vadose-zone/recharge processes; paired grain-yield treatment response not identified.'}},
 'readiness_conclusion':{
   'new_observed_management_response_values_available':True,
   'complete_source_faithful_native_water_treatment_replay_available':False,
   'independent_native_irrigation_response_validation_completed':False,
   'strongest_clean_water_schedule_benchmark':'YangWuqiao2015-2019 has exact I events/sowing/harvest, fixed N/P/K,32season ET/biomass/storage means and8four-year grain-yield means; soil layer hydraulics and original daily weather remain missing. Sun1999-2002 supplies observed yield/I/ET, stage ratios and FC/WP, but no exact event dates/initial layer states.',
   'fixed_N_irrigation_method_benchmark':'WangDawenkou2022-2023 offers4paired observed method contrasts: OPT1/O-OPT1 atN160.5kg/ha and OPT2/O-OPT2 atN184.5kg/ha, with density,tillage,harvest/fertilizer timing held within pair. Exact sowing and supplemental event dates missing; FC percentage basis unconfirmed, WP/Ksat missing.',
   'recent_observed_benchmark':'Ali2023-2024 maize has dated I and yield but joint N/method changes, confined lysimeters and missing layer inputs. ETn1 excludes replicated ET-effect inference.',
   'permitted_claim':'Published observed treatment responses have been assembled. Conditional comparisons may be assessed against these treatment means with explicit input/structural limitations.',
   'unsupported_claims':['native yield/ET irrigation-response validation completed','native soil-depletion trigger independently verified','observed regional groundwater savings','irrigation-only causal yield effect from Ali treatment packages','all79rows represent independent experiments']},
 'output_files':['audit/management_response_evidence.json','audit/observed_irrigation_treatments.csv',
                 'literature/treatment_sources/sun2006_treatment_means.csv','literature/treatment_sources/sun2006_soil_profile.csv',
                 'literature/treatment_sources/sun2006_stage_targets.csv','literature/treatment_sources/sun2006_monthly_rainfall.csv',
                 'literature/treatment_sources/ali2026_observed_irrigation_events.csv','literature/treatment_sources/ali2026_soil_profile.csv',
                 'literature/treatment_sources/ali2026_management_calendar.csv','literature/treatment_sources/ali2026_observed_ANOVA.csv',
                 'literature/treatment_sources/yang2024_observed_irrigation_events.csv','literature/treatment_sources/wang2025_fixed_N_irrigation_contrasts.csv',
                 'literature/treatment_sources/wang2025_fixed_management.csv','literature/treatment_sources/wang2025_soil_profile.csv','literature/treatment_sources/additional_treatment_references.json']
}
audit['soil_profile_transfer_limit']={'sun_ali_depth_cm':190,'yang_observation_storage_depth_cm':200,
 'particle_density_reported':False,'saturation_reported':False,'Ksat_reported':False,
 'physical_assumption_check':'At145-170cm, printed BD1.64g/cm3 and FC0.390m3/m3 exceed porosity0.3811 inferred from an assumed particle density2.65g/cm3. The source does not report particle density or saturation. Do not alter sourceFC; transfer needs an explicit, independently chosen compatible saturation/particle-density prior and0-200cm profile assumption.'}
# Public primary-source numeric data transcriptions, independent of copyright prose.
numeric_source_records={paper:[r for r in ROWS if r['paper_id']==paper] for paper in SOURCES}
(SRC/'published_numeric_transcriptions.json').write_text(json.dumps(numeric_source_records,ensure_ascii=False,indent=2)+'\n')
audit['source_file_sha256']={str(p.relative_to(BASE)):sha(p) for p in sorted(SRC.iterdir()) if p.is_file() and p.name!='source_file_manifest.json'}
(OUT/'management_response_evidence.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n')
(SRC/'source_file_manifest.json').write_text(json.dumps({'retrieved_on':RETRIEVED,'files':audit['source_file_sha256'],'observed_csv_sha256':sha(OUT/'observed_irrigation_treatments.csv')},ensure_ascii=False,indent=2)+'\n')
print(json.dumps(audit['summary'],indent=2))
print('Sun max water-balance difference mm',max(abs(r['difference_mm']) for r in sun_balance))
print('Sun PAW remaining range at theta/FC=.8',audit['native_trigger_conversion']['q_0_8_layer_paw_range'])
print('All current case-inventory matches:',sum(len(c['matching_inventory_keys']) for c in overlap_checks))
