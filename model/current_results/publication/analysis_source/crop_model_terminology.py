"""Publication terminology without changes to archived data or model parameters."""
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import re

P=Path(__file__).resolve().parents[1]

TERMS=[
    ('open_crop_model','Open Crop Model'),
    ('conservative crop–soil model','crop model'),
    ('coupled crop–soil model','crop model'),
    ('coupled crop–soil-water application','complete crop model'),
    ('crop–soil model','crop model'),
    ('crop-soil model','crop model'),
    ('crop–soil simulation','crop model simulation'),
    ('primary coupled model','primary crop model'),
    ('coupled-model','crop model'),
    ('coupled crop model','crop model'),
    ('coupled model','crop model'),
    ('canopy–weather ET','canopy-based ET'),
    ('potential-growth','potential crop growth'),
    ('crop-growth','crop growth'),
    ('crop cards','crop parameter sets'),
    ('crop-card','crop-parameter'),
    ('root-extraction coefficient','root water uptake coefficient'),
    ('root-extraction starts','initial root water uptake coefficients'),
    ('root-extraction','root water uptake'),
    ('root uptake','root water uptake'),
    ('meteorological forcing','weather inputs'),
    ('weather forcing','weather inputs'),
    ('forcing subsets','weather subsets'),
    ('primary forcing','primary weather inputs'),
    ('AgERA5 v2.0 forcing','AgERA5 v2.0 weather data'),
    ('Reference ET₀','Reference evapotranspiration (ET₀)'),
    ('hydrological substep','subdaily water-balance time step'),
    ('engineering prior','assumed parameter value'),
    ('particle-density prior','assumed particle density'),
    ('literature-informed soil profile','literature-based soil profile'),
    ('soil priors','assumed soil properties'),
    ('soil-state handoffs','soil-water continuity between crop seasons'),
    ('root-zone dynamics','root-zone soil-water dynamics'),
    ('grain biomass','grain dry matter'),
    ('physiological completion','simulated maturity'),
    ('grain-mass ceiling','maximum individual grain mass'),
    ('late-leaf allocation multiplier','late-season leaf biomass partitioning multiplier'),
    ('source-screened recalibration','recalibration after data quality screening'),
    ('source-screened full refit','recalibration after data quality screening'),
    ('source-screened refit','recalibration after data quality screening'),
    ('source-screened shared crop estimates','recalibrated shared crop parameters'),
    ('source-screened shared growth, grain and ET estimates','recalibrated growth, grain and ET parameters'),
    ('source-screened shared coefficients','recalibrated shared parameters'),
    ('source-screened coefficients','recalibrated coefficients'),
    ('source-screened parameters','recalibrated parameters'),
    ('source-screened crop','recalibrated crop'),
    ('source-screened regional experiment','regional parameter sensitivity experiment'),
    ('source-screened diagnostics','evaluation using quality-controlled observations'),
    ('source-screened calibration records','quality-controlled calibration records'),
    ('source-screened calibration','calibration with quality-controlled observations'),
    ('source-screened','quality-controlled'),
    ('screened-parameter','recalibrated-parameter'),
    ('screened coefficients','recalibrated coefficients'),
    ('screened shared crop estimates','recalibrated shared crop parameters'),
    ('screened parameters','recalibrated parameters'),
    ('screened regional responses','regional responses with recalibrated parameters'),
    ('screened responses','responses with recalibrated parameters'),
    ('screened allocations','allocations with recalibrated parameters'),
    ('screened allocation','allocation with recalibrated parameters'),
    ('screened recalibration','recalibration after data quality screening'),
    ('screened regional phenology','original regional phenology'),
    ('refitted crop parameter sets','recalibrated crop parameter sets'),
    ('full refit','recalibration'),
    ('refit growth/site/ET coefficients','recalibrated growth, site-specific adjustment and ET coefficients'),
    ('refit','recalibration'),
    ('legacy coefficients','original coefficients'),
    ('legacy inner scores','original calibration-subset scores'),
    ('screened-transferred','recalibrated with original allocation'),
    ('screened-reoptimized','recalibrated with reoptimized allocation'),
    ('retrospective-testing','retrospective evaluation'),
    ('retrospective testing','retrospective evaluation'),
    ('calibration/testing','calibration/evaluation'),
    ('policy-selection holdout','period for evaluating selected strategies'),
    ('inner-training loss','calibration-subset objective'),
    ('fitting-only scales','residual scales estimated from calibration data'),
    ('common-cohort response','response over common evaluation years'),
    ('common testing cohort','common evaluation years'),
    ('common-cohort','common evaluation-year'),
    ('fixed-quota histories','continuous simulations at fixed irrigation levels'),
    ('quota histories','irrigation scenarios'),
    ('quota treatments','irrigation treatments'),
    ('quota fractions','irrigation fractions'),
    ('quota fraction','irrigation fraction'),
    ('irrigation-quota assignments','irrigation assignments'),
    ('quota-area-share differences','differences in area shares assigned to irrigation levels'),
    ('irrigation quotas','irrigation levels'),
    ('Primary quota','Original irrigation'),
    ('Reoptimized quota','Reoptimized irrigation'),
    ('Screened quota','Recalibrated irrigation'),
    ('Archived estimates','Original parameters'),
    ('Screened, transferred allocation','Recalibrated, original allocation'),
    ('Screened, reoptimized allocation','Recalibrated, reoptimized allocation'),
    ('Screened / transferred','Recalibrated / original allocation'),
    ('Screened / reoptimized','Recalibrated / reoptimized allocation'),
    ('Archived / archived','Original / original allocation'),
    ('Held-out harvest year','Evaluation harvest year'),
    ('Dry-grain advantage','Grain production difference'),
    ('Annual dry grain','Annual grain production'),
    ('wax maturity','dough stage'),
    ('Three leaves','Three-leaf stage'),
    ('Five leaves','Five-leaf stage'),
    ('Seven leaves','Seven-leaf stage'),
]


def replace_case(match,replacement):
    original=match[0]
    if original[0].isupper() and not replacement[0].isupper():
        return replacement[0].upper()+replacement[1:]
    return replacement


def normalize(text,policy=False):
    # Protect reference keys, source identifiers, formulas and numeric results.
    placeholders={}
    def protect(match):
        key=f'ZZREFERENCE{len(placeholders)}ZZ';placeholders[key]=match[0];return key
    text=re.sub(r'\[CITE:[^\]]+\]|(?<=reported as )wax maturity',protect,text)
    for original,replacement in TERMS:
        text=re.sub(r'(?<!\w)'+re.escape(original)+r'(?!\w)',
            lambda m,r=replacement:replace_case(m,r),text,flags=re.I)
    for original,replacement in [('testing','evaluation'),('rules','strategies'),('rule','strategy'),
                                  ('quotas','irrigation fractions'),('quota','irrigation fraction')]:
        text=re.sub(r'\b'+original+r'\b',lambda m,r=replacement:replace_case(m,r),text,flags=re.I)
    if policy:
        text=re.sub(r'\btraining\b',lambda m:replace_case(m,'selection'),text,flags=re.I)
    for old,new in [
        ('selected from selection-period responses','selected from historical simulated responses'),
        ('selection-selected','selected'),('selection-only','selection-period'),
        ('selection-reoptimized allocations','allocations reoptimized during the selection period'),
        ('selection grain-retention targets','grain-retention targets'),
        ('strategies selected at 95% and 98% grain-retention targets retained 94.15% and 97.38%',
         'strategies targeting 95% and 98% of conventional grain production maintained 94.15% and 97.38%'),
        ('class-available evaluation years','evaluation years with available GRACE observations'),
        ('95% selection target','95% grain target'),('98% selection target','98% grain target'),
        ('using calibration-subset objective','using the calibration objective'),
        ('The crop model transpiration and evaporation coefficients',"The crop model’s transpiration and evaporation coefficients"),
        ('before modeled simulated maturity','before simulated maturity'),
        ('before physiological completion','before simulated maturity'),
        ('These endpoints remain classified as cuts;','These crop seasons end before simulated maturity;'),
        ('Presowing water-state handoffs are preserved.','Initial soil-water states and continuity between seasons are preserved.'),
        ('Required inputs are daily weather inputs,','Required inputs are daily weather data,'),
        ('reported metrics are evaluation using quality-controlled observations of unchanged predictions.',
         'reported metrics compare unchanged predictions with quality-controlled observations.'),
        ('the coupled Wuqiao evaluation','the complete crop model evaluation at Wuqiao'),
        ('all absolute growth, grain, site-adjustment and ET coefficients',
         'growth, grain, site-specific adjustment and ET parameters'),
        ('two fixed initial root water uptake coefficients','two prescribed root water uptake coefficients'),
        ('screened calibration records','quality-controlled calibration records'),
        ('Frozen recalibrated coefficients','Recalibrated coefficients held fixed'),
        ('screened model comparisons','comparisons with quality-controlled observations'),
        ('screened harvest comparisons','harvest comparisons after data quality screening'),
        ('Coupled soil-water dynamics','Crop model soil-water dynamics'),
    ]:
        text=re.sub(re.escape(old),lambda m,r=new:replace_case(m,r),text,flags=re.I)
    for key,value in placeholders.items():text=text.replace(key,value)
    return text


def integrate(main,supplement):
    original={'main':deepcopy(main),'supplementary':deepcopy(supplement)}
    main=deepcopy(main);supplement=deepcopy(supplement);changes=[]
    for name,blocks in [('main',main),('supplementary',supplement)]:
        heading=''
        for index,block in enumerate(blocks):
            heading=block.get('heading',heading)
            if name=='main':
                policy=heading.startswith(('Abstract','1.','2.1.','2.6.','2.7.','2.9.','2.10.','2.11.','2.12.',
                                          '3.3.','3.4.','3.5.','3.6.','4.1.','4.2.','4.3.','4.4.','5.'))
            else:policy=heading.startswith(('S3.','S5.','S7.','S8.'))
            for key in ['heading','paragraphs','caption','table_caption','table_note','text']:
                if key not in block:continue
                values=block[key] if key=='paragraphs' else [block[key]]
                revised=[normalize(v,policy) for v in values]
                for old,new in zip(values,revised):
                    if old!=new:changes.append({'document':name,'block':index,'field':key,'original':old,'revised':new})
                block[key]=revised if key=='paragraphs' else revised[0]
    observations=next(b for b in main if b.get('heading','').startswith('2.3.1.'))
    observations['paragraphs']=[p.replace('Tables S1 and 2.','Tables S1 and S2.').replace(
        'Wheat dough stage and full ripeness remain distinct;',
        'Wheat dough-stage observations (reported as wax maturity) remain distinct from full ripeness;')
        for p in observations['paragraphs']]
    next(b for b in main if b.get('heading','').startswith('2.4.'))['heading']='2.4. Crop model description'
    next(b for b in main if b.get('heading','').startswith('4.5.'))['heading']='4.5. Crop model performance and transfer of irrigation responses'
    introduction=next(b for b in main if b.get('heading','').startswith('1.'))
    introduction['paragraphs']=[p.replace('phenology, LAI, biomass','phenology, leaf area index (LAI), biomass') for p in introduction['paragraphs']]
    model=next(b for b in main if b.get('heading','').startswith('2.4.'))
    if '(RUE)' not in model['paragraphs'][1]:
        model['paragraphs'][1]=model['paragraphs'][1].replace('radiation-use efficiency','radiation-use efficiency (RUE)',1)
    calibration=next(b for b in main if b.get('heading','').startswith('2.5.'))
    calibration['paragraphs'][0]=calibration['paragraphs'][0].replace('specific leaf area,','specific leaf area (SLA),')
    metrics=next(b for b in main if b.get('heading','').startswith('2.8.'))
    metrics['paragraphs'][0]=metrics['paragraphs'][0].replace(
        'using RMSE, mean bias, NSE and squared Pearson correlation.',
        'using root mean square error (RMSE), mean bias error (MBE), Nash–Sutcliffe efficiency (NSE) and squared Pearson correlation (R²).')
    for block in main:
        if block.get('figure','').endswith('phenology_stage_timing.png'):
            block['caption']=block['caption'].replace(' NSE denotes Nash–Sutcliffe efficiency where annotated.','')
        if block.get('table','').endswith('main_calibration_testing_metrics.csv'):
            block['table_note']=block['table_note'].replace(
                'Biomass and grain are expressed in t ha⁻¹, LAI in m² m⁻² and daily ET in mm d⁻¹. NSE measures prediction error relative to the observed-mean baseline; R² is the square of the weighted Pearson correlation. ','')
    structure=next(b for b in supplement if b.get('heading','').startswith('S2.1.'))
    structure['paragraphs'][2]=structure['paragraphs'][2].replace(
        'Physiological maturity terminates new biomass production.',
        'Biomass accumulation stops at simulated maturity: BBCH 89 for wheat and R6 for maize.')
    stage_note=(' Wheat dough-stage observations use BBCH 85 as a conditional reference, with BBCH 83–87 '
                'retained as the stage-definition range. Full ripeness corresponds to BBCH 89.')
    if stage_note.strip() not in structure['paragraphs'][1]:structure['paragraphs'][1]+=stage_note
    transfer=next(b for b in supplement if b.get('table','').endswith('supplement_screened_refit_transfer.csv'))
    transfer['table_caption']='Table S10. Evaluation of recalibrated crop parameters in the Wuqiao irrigation experiment.'
    transfer['table_note']=transfer['table_note'].replace(
        'Their signed correlations are reported in Section S17 because R² loses correlation direction.',
        'Signed correlations are retained in the reproducibility archive because R² does not retain correlation direction.')
    next(b for b in supplement if b.get('heading','').startswith('S6.'))['heading']='S6. Harvest data quality and crop model recalibration'
    next(b for b in supplement if b.get('table','').endswith('supplement_screened_full_refit.csv'))['table_caption']='Table S9. Performance of original and recalibrated crop parameters on the same retrospective evaluation data.'
    sensitivity=next(b for b in supplement if b.get('table','').endswith('supplement_regional_parameter_sensitivity.csv'))
    sensitivity['table_note']=sensitivity['table_note'].replace(
        'Archived denotes the historical regional coefficients and allocation; screened denotes the frozen recalibrated shared crop parameters.',
        'Original denotes the historical regional parameters and allocation; recalibrated denotes the parameter estimates after data quality screening.')
    for blocks in [main,supplement]:
        for block in blocks:
            for key in ['heading','table_caption']:
                if key in block:block[key]=block[key].replace('full source-screened refit','recalibrated crop model').replace('full refit','recalibrated crop model')
    abstract=next(b for b in main if b.get('heading')=='Abstract')['paragraphs'][0]
    path=P/'verification/abstract_structure.json'
    if path.exists():
        receipt=json.loads(path.read_text())
        for component in receipt['components']:component['text']=normalize(component['text'],policy=True)
        receipt['word_count']=len(abstract.split());path.write_text(json.dumps(receipt,indent=2)+'\n')
    changes=[]
    for name,blocks in [('main',main),('supplementary',supplement)]:
        for index,(before,after) in enumerate(zip(original[name],blocks)):
            for key in ['heading','paragraphs','caption','table_caption','table_note','text']:
                if key not in before:continue
                old=before[key] if key=='paragraphs' else [before[key]]
                new=after[key] if key=='paragraphs' else [after[key]]
                for a,b in zip(old,new):
                    if a!=b:changes.append({'document':name,'block':index,'field':key,'original':a,'revised':b})
    (P/'verification/crop_model_terminology_changes.json').write_text(json.dumps({
        'changed_fields':len(changes),'changes':changes,'model_parameters_changed':False,
        'references':[
            {'source':'DSSAT model calibration and evaluation terminology','url':'https://dssat.net/tools/'},
            {'source':'APSIM crop growth and soil water terminology','url':'https://www.apsim.info/wp-content/uploads/2019/09/WheatDocumentation.pdf'},
            {'source':'FAO reference evapotranspiration terminology','url':'https://www.fao.org/4/X0490E/x0490e05.htm'},
            {'source':'BBCH cereal development stages','url':'https://www.openagrar.de/servlets/MCRFileNodeServlet/openagrar_derivate_00010428/BBCH-Skala_en.pdf'}],
        'stage_mapping_source':'runs/published_irrigation_benchmark_conductivity/source_snapshots/native_model_84457b3/research/ncp_irrigation/README.md'
    },indent=2)+'\n')
    return main,supplement
