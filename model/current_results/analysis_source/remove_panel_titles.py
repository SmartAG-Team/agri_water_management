"""Render the current publication with panel letters and caption-based identities.

Frozen plotting routines are loaded as syntax trees without their mutating
entrypoints. Chart values, observations, limits and source snapshots are retained.
"""
from pathlib import Path
from datetime import datetime, timezone
import ast
import hashlib
import json
import re

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.text import Text
import numpy as np
import pandas as pd
import pymupdf

import revise_manuscript_figures as main_figures

ROOT = Path(__file__).resolve().parents[1]
PUB = ROOT / 'publication'
CAL = ROOT / 'calibration'
REG = ROOT / 'regional'
EXPORTS = []


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def signature(fig):
    data = []
    for ax in fig.axes:
        data.append((ax.get_xlim(), ax.get_ylim(),
                     [(repr(l.get_xdata()), repr(l.get_ydata())) for l in ax.lines],
                     [(repr(c.get_offsets()), repr(c.get_paths()), repr(c.get_array()))
                      for c in ax.collections]))
    return hashlib.sha256(repr(data).encode()).hexdigest()


def export(fig, relative, caption, sources):
    before = signature(fig)
    removed = []
    labels = []
    for ax in fig.axes:
        for loc in ['left', 'center', 'right']:
            title = ax.get_title(loc=loc)
            if not title:
                continue
            match = re.match(r'^(\([a-z]\))\s*(.*)', title, re.S)
            label = match.group(1) if match else ''
            if match and match.group(2):
                removed.append(title)
            elif not match:
                removed.append(title)
            ax.set_title(label, loc=loc, fontweight='bold')
            if label:
                labels.append(label)
        for text in ax.texts:
            match = re.match(r'^(\([a-z]\))\s+(.+)', text.get_text(), re.S)
            if match:
                removed.append(text.get_text())
                text.set_text(match.group(1))
                text.set_fontweight('bold')
                labels.append(match.group(1))
    if fig._suptitle is not None:
        removed.append(fig._suptitle.get_text())
        fig._suptitle.remove()
        fig._suptitle = None
    assert before == signature(fig), 'Panel-title removal changed chart values or limits'
    for text in fig.findobj(Text):
        text.set_fontfamily('DejaVu Sans')
    stem = PUB / relative
    for ext in ['png', 'pdf']:
        fig.savefig(stem.with_suffix('.'+ext), dpi=400, bbox_inches='tight', pad_inches=.08)
    stem.with_name(stem.name+'_caption.txt').write_text(caption+'\n')
    EXPORTS.append(dict(figure=relative+'.png', caption=caption, panel_labels=labels,
                        descriptive_panel_titles=False, removed_panel_titles=removed,
                        chart_data_and_limits_unchanged=True, chart_signature=before,
                        source_sha256={str(s.relative_to(ROOT)):sha(s) for s in sources},
                        png_sha256=sha(stem.with_suffix('.png')),
                        pdf_sha256=sha(stem.with_suffix('.pdf'))))
    plt.close(fig)


def function_tree(path, name):
    tree = ast.parse(path.read_text())
    return next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)


def execute(function, scope):
    tree = ast.Module(body=[function], type_ignores=[])
    exec(compile(ast.fix_missing_locations(tree), '<preserved-plot-function>', 'exec'), scope)
    return scope[function.name]


def style(ax, letter, title):
    ax.set_title(f'({letter}) {title}', loc='left', pad=5)
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color('#34383b')
        spine.set_linewidth(.7)
    ax.tick_params(direction='out', length=3, width=.7)


def field_grain(caption):
    source = CAL / 'analysis_source/package_crop_run.py'
    function = function_tree(source, 'harvest_figures')
    loop = next(n for n in function.body if isinstance(n, ast.For))
    # Keep the published grain plot; the biomass plot is outside this package.
    assert isinstance(loop.iter, ast.List) and len(loop.iter.elts) == 2
    loop.iter.elts = loop.iter.elts[1:]
    sources = [source, CAL/'predictions/field_comparisons.csv',
               CAL/'data/wuqiao_digitized_annual_yields.csv', CAL/'data/confirmed_field_biomass.csv']
    def save(root, fig, name, original_caption):
        assert name == 'Field_grain_yield'
        export(fig, 'figures/current_annual_field_grain', caption, sources)
    scope = dict(ROOT=CAL, pd=pd, np=np, plt=plt, BLUE='#2378a8',
                 style=lambda ax, i, title:style(ax, chr(97+i), title), save=save,
                 selected_version=lambda:'management_refit')
    with plt.rc_context({'font.size':9, 'axes.labelsize':9, 'axes.titlesize':9,
                         'xtick.labelsize':8, 'ytick.labelsize':8}):
        execute(function, scope)()


def soil_storage(caption):
    source = REG/'source_snapshots/analysis_sources/export_results.py'
    function = function_tree(source, 'figures')
    indices = [i for i,n in enumerate(function.body) if isinstance(n, ast.Assign)
               and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Attribute)
               and n.value.func.attr == 'subplots']
    assert len(indices) == 2
    function.body = function.body[indices[1]:]
    # Replace the source's save loop and final CSV write with one publication export.
    stop = next(i for i,n in enumerate(function.body) if isinstance(n, ast.For)
                and isinstance(n.target, ast.Name) and n.target.id == 'extension')
    function.body = function.body[:stop] + ast.parse('save(fig)').body
    daily = [REG/f'adaptive/per_representative/rep_{rid:03d}.daily.csv.gz' for rid in [0,31]]
    scope = dict(ROOT=REG/'adaptive', pd=pd, plt=plt,
                 COLORS={'adaptive_95':'#0072B2','adaptive_98':'#D55E00'},
                 LABELS={'adaptive_95':'95% training target','adaptive_98':'98% training target'},
                 save=lambda fig:export(fig, 'figures/closed_axes/Figure_S14_continuous_soil_storage',
                                        caption, [source]+daily))
    with plt.rc_context({'font.size':8, 'axes.labelsize':8, 'xtick.labelsize':8,
                         'ytick.labelsize':8}):
        fig = execute(function, scope)()


def raw_harvest(caption):
    # The plotting block below retains the exact original values and 0.01 unit factor.
    source = PUB/'source_snapshots/harvest_quality/raw_harvest_records.csv'
    raw = pd.read_csv(source)
    flagged = {(r['plot'],r['crop'],int(r['year']))
               for r in raw[raw.source_quarantined].to_dict('records')}
    with plt.rc_context({'font.size':8, 'axes.labelsize':8, 'xtick.labelsize':7,
                         'ytick.labelsize':7}):
        fig, axs = plt.subplots(2,2,figsize=(8,5.4),layout='constrained')
        main = raw[raw['plot'].eq('YCAZH01ABC_01')]
        for j,(crop,var) in enumerate((c,v) for c in ['wheat','maize'] for v in ['biomass','grain']):
            ax=axs.flat[j];g=main[main.crop.eq(crop)]
            for year,s in g.groupby('year'):
                values=s[var]*.01;bad=('YCAZH01ABC_01',crop,int(year)) in flagged
                jitter=np.linspace(-.13,.13,len(s))
                ax.scatter(year+jitter,values,s=10,color='#CC6677' if bad else '.65',alpha=.75)
                ax.errorbar(year,values.mean(),yerr=values.std(ddof=1)/np.sqrt(len(values)),
                            fmt='D' if bad else 'o',color='#AA3377' if bad else '#222222',
                            ms=4,capsize=2,lw=.7)
            style(ax,chr(97+j),f'{crop.capitalize()} {"grain" if var=="grain" else "aboveground biomass"}')
            ax.set_ylabel('Dry mass (t ha⁻¹)');ax.set_xlabel('Source harvest year');ax.set_ylim(bottom=0)
            ax.set_xticks([2005,2008,2011,2014,2017,2020,2022]);ax.tick_params(axis='x',rotation=30)
        export(fig, 'figures/closed_axes/Figure_S20_raw_harvest_quality', caption, [source])


def station_harvest(caption):
    source=ROOT/'analysis_source/current_crop_publication.py'
    function=function_tree(source,'supplementary_figures')
    start=next(i for i,n in enumerate(function.body) if isinstance(n,ast.Assign)
               and any(isinstance(t,ast.Name) and t.id=='harvest' for t in n.targets))
    function.body=function.body[start:]
    station=pd.read_csv(CAL/'predictions/station_comparisons.csv',low_memory=False)
    station=station[station.version.eq('management_refit')].copy()
    def verify_export(grouped, name):
        assert name=='annual_station_harvest_sources'
        original=pd.read_csv(PUB/'tables/current_annual_station_harvest_sources.csv')
        pd.testing.assert_frame_equal(grouped, original, check_dtype=False, atol=1e-9, rtol=1e-12)
    scope=dict(pd=pd,np=np,plt=plt,BLUE='#2166a5',style=style,export=verify_export,
               Line2D=matplotlib.lines.Line2D,
               save_figure=lambda fig,name,original:export(fig,
                   'figures/current_Figure_S6_Yucheng_harvest_comparisons',caption,
                   [source,CAL/'predictions/station_comparisons.csv',
                    PUB/'tables/current_annual_station_harvest_sources.csv']))
    with plt.rc_context({'font.size':8.5,'axes.labelsize':8.5,'axes.titlesize':9,
                         'xtick.labelsize':8.5,'ytick.labelsize':8.5}):
        execute(function,scope)(station)


def main():
    supplement=json.loads((PUB/'analysis_source/supplementary_blocks.json').read_text())['blocks']
    captions={b['figure']:b['caption'] for b in supplement if 'figure' in b}
    s2='figures/current_annual_field_grain.png'
    captions[s2]=captions[s2].replace('Panels (a–d) show wheat and (e–h) maize in 2016–2019.',
        'Panels (a–d) show wheat and (e–h) maize; columns from left to right show 2016, 2017, 2018 and 2019.')
    s3='figures/closed_axes/Figure_S14_continuous_soil_storage.png'
    captions[s3]=captions[s3].replace('in two representative units.',
        'in representative units (a) 0 and (b) 31.')
    s5='figures/current_Figure_S6_Yucheng_harvest_comparisons.png'
    if 'Wheat occupies the left column' not in captions[s5]:
        captions[s5]=captions[s5].replace('Conditional Yucheng harvest comparisons.',
            'Conditional Yucheng harvest comparisons. Wheat occupies the left column and maize the right; '
            'the top row shows aboveground biomass and the bottom row grain.')
    main_figures.main()
    plt.rcParams.update({'pdf.fonttype':42,'savefig.facecolor':'white'})
    field_grain(captions[s2]);soil_storage(captions[s3])
    s4='figures/closed_axes/Figure_S20_raw_harvest_quality.png'
    raw_harvest(captions[s4]);station_harvest(captions[s5])
    visual=json.loads((PUB/'verification/visual_revision_20261008.json').read_text())['figures']
    records=visual+EXPORTS
    s1='figures/closed_axes/all_quota_spatial_errors.png'
    source=PUB/s1
    records.append(dict(figure=s1,caption=captions[s1],panel_labels=[],
                        descriptive_panel_titles=False,already_without_panel_titles=True,
                        png_sha256=sha(source),pdf_sha256=sha(source.with_suffix('.pdf')),
                        source_sha256={'regional/tables/full_quota_spatial_metrics.csv':
                                      sha(REG/'tables/full_quota_spatial_metrics.csv')}))
    assert len(records)==19 and len({r['figure'] for r in records})==19
    for record in records:
        with pymupdf.open((PUB/record['figure']).with_suffix('.pdf')) as pdf:
            text='\n'.join(p.get_text() for p in pdf)
        record['pdf_text_sha256']=hashlib.sha256(text.encode()).hexdigest()
        for title in record.get('removed_panel_titles',[]):
            assert title not in text, (record['figure'],title)
        if record.get('panel_labels'):
            assert all(label in text for label in record['panel_labels'])
        assert not re.search(r'\([a-z]\)\s+(?:Wheat|Maize|Fengqiu|Gucheng|Luancheng|Shangqiu|Yucheng|Simulation unit)',text)
    receipt=dict(completed_utc=datetime.now(timezone.utc).isoformat(),
                 main_figures=8,supplementary_figures=11,descriptive_panel_titles=False,
                 source_predictions_modified=False,figures=records,renderer_sha256=sha(Path(__file__)))
    (PUB/'verification/panel_title_removal_20261008.json').write_text(
        json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    print('All 19 publication figures have no descriptive panel titles; panel identities are in captions.')


if __name__=='__main__':
    main()
