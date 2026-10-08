"""Bind field-observation descriptions to dataset and prior-use references."""
import re

STATION_SOURCES = (
    "Winter-wheat and summer-maize observations were obtained from the National Ecosystem Science Data Center. "
    "Crop growth records came from the Fengqiu and Luancheng archives [CITE:fengqiu2020_crop|luancheng2020_crop], "
    "the Shangqiu archive [CITE:shangqiu2021_crop] and the Yucheng crop and biological monitoring archives "
    "[CITE:yucheng2021_crop|yucheng2025_biology]. Gucheng observations came from the maize sowing-date "
    "experiment [CITE:geng2023_gucheng_dataset]. Daily ET and antecedent soil-moisture profiles came "
    "from the Fengqiu soil-water archive [CITE:fengqiu2020_soil_water], and Yucheng ET came from the "
    "water-environment archive [CITE:yucheng2025_water]."
)

STATION_HISTORY = (
    "Archived crop-season groups span 2004–2008 at Fengqiu and Luancheng, 2018–2020 at Gucheng, "
    "2001–2007 at Shangqiu and 1998–2022 at Yucheng. Coverage is intermittent; exact crop-by-site years "
    "and dataset identifiers are listed in Tables S1 and S2. Earlier crop-model evaluations used observations "
    "from the Fengqiu, Luancheng and Yucheng monitoring programs [CITE:chen2010_crop_productivity|yu2006_rzwqm_yucheng]."
)

SUPPLEMENT_SOURCES = (
    "Fengqiu, Luancheng and Shangqiu crop phenology, leaf area and biomass records were obtained from "
    "the station growth-monitoring datasets [CITE:fengqiu2020_crop|luancheng2020_crop|shangqiu2021_crop]. "
    "The Gucheng sowing-date dataset supplies maize development and LAI observations [CITE:geng2023_gucheng_dataset]. "
    "Yucheng records combine the 1998–2006 crop archive and the 2005–2022 biological and water-environment "
    "archives [CITE:yucheng2021_crop|yucheng2025_biology|yucheng2025_water]. Fengqiu ET and antecedent "
    "soil-moisture profiles come from the farmland soil-water physical-properties archive [CITE:fengqiu2020_soil_water]."
)

SUPPLEMENT_HISTORY = (
    "Published crop-model evaluations used Fengqiu, Luancheng and Yucheng observations "
    "[CITE:chen2010_crop_productivity|yu2006_rzwqm_yucheng]. Regional crop-water-use evaluation also "
    "used CERN monitoring records from Fengqiu, Luancheng and Shangqiu [CITE:wang2024_crop_water_dataset]. "
    "These publications document earlier use of the station monitoring programs; the exact records, "
    "plots and measurement periods retained here are identified by the source datasets and case inventory (Tables S1 and S2)."
)


def apply(article, supplement):
    """Apply citations without changing observations, results or author metadata."""
    station = next(b for b in article['blocks'] if b.get('heading', '').startswith('2.3.1.'))
    paragraphs = station['paragraphs']
    cited = {key for group in re.findall(r'\[CITE:([^\]]+)\]', ' '.join(paragraphs))
             for key in group.split('|')}
    required = {key for group in re.findall(r'\[CITE:([^\]]+)\]', STATION_SOURCES)
                for key in group.split('|')}
    if not required.issubset(cited):
        paragraphs[0] = STATION_SOURCES
        if len(paragraphs) < 2 or paragraphs[1] != STATION_HISTORY:
            paragraphs.insert(1, STATION_HISTORY)
            for block in article['blocks']:
                anchor=block.get('after_paragraph',{})
                if anchor.get('heading')==station['heading'] and anchor['index']>=1:
                    anchor['index']+=1
    for i, paragraph in enumerate(paragraphs):
        paragraph = paragraph.replace(
            'Fengqiu ET identifies the lysimeter observation plot.',
            'Fengqiu ET identifies the lysimeter observation plot [CITE:fengqiu2020_soil_water].')
        paragraph = paragraph.replace(
            'Yucheng ET retains the measurement definitions of its source archive.',
            'Yucheng ET retains the measurement definitions of its source archive [CITE:yucheng2025_water].')
        paragraph = paragraph.replace(
            'from the preceding calendar month: 11 in the original calibration partition and 15 in retrospective evaluation.',
            'from the preceding calendar month: 11 in the original calibration partition and 15 in retrospective evaluation [CITE:fengqiu2020_soil_water].')
        paragraphs[i] = paragraph

    field = next(b for b in article['blocks'] if b.get('heading', '').startswith('2.3.2.'))
    field['paragraphs'][0] = field['paragraphs'][0].replace(
        'The Wuqiao experiment at 37.630°N, 116.444°E contains four wheat irrigation schedules, W0–W3, during the 2015–2019 rotation [CITE:yang2024_precipitation].',
        'Field observations and management for the Wuqiao experiment at 37.630°N, 116.444°E were obtained from the primary article and its supplementary material [CITE:yang2024_precipitation]. The experiment contains four wheat irrigation schedules, W0–W3, during the 2015–2019 rotation.')

    sources = next(b for b in supplement['blocks'] if b.get('heading', '').startswith('S1.'))
    if not sources['paragraphs'][0].startswith('Fengqiu, Luancheng and Shangqiu crop phenology'):
        sources['paragraphs'][:0] = [SUPPLEMENT_SOURCES, SUPPLEMENT_HISTORY]
    for i, paragraph in enumerate(sources['paragraphs']):
        paragraph = paragraph.replace(
            'Twenty-six Fengqiu cases retain antecedent moisture initialization from the preceding calendar month.',
            'Twenty-six Fengqiu cases retain antecedent moisture initialization from the preceding calendar month [CITE:fengqiu2020_soil_water].')
        paragraph = paragraph.replace(
            'Wuqiao 2016–2018 supplies 12 documented treatment seasons per crop for that fit;',
            'Wuqiao 2016–2018 supplies 12 documented treatment seasons per crop for that fit [CITE:yang2024_precipitation];')
        sources['paragraphs'][i] = paragraph
    return article, supplement
