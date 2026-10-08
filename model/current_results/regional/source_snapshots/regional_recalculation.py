"""Recalculate continuous regional irrigation responses with frozen revised traits.

Every used observation, forcing, hydraulic profile, mapping, crop card and native
source is copied into this subrun. Existing completed experiments are read only.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from copy import deepcopy
import hashlib
import importlib.util
import json
from multiprocessing import get_context
from pathlib import Path
import shutil
import sys
import time
import zipfile

sys.dont_write_bytecode = True

import numpy as np
import pandas as pd
from scipy.optimize import linprog

PRODUCT = Path(__file__).resolve().parents[1]
PROJECT = PRODUCT.parents[1]
CALIBRATION = PROJECT/'model/2026-10-07_documented_management_water_calibration'
PREVIOUS_REGIONAL = PROJECT/'model/2026-10-04_stage_specific_recalibration/regional'
FRACTIONS = [0., .25, .5, .75, 1.]
REDUCTIONS = [.25, .5, .75]
POLICIES = ['adaptive_95', 'adaptive_98', 'rain_95_matched', 'rain_98_matched',
            'rain_95_available', 'rain_98_available']
TECH = dict(method='flood', wetted_fraction=1., application_evaporation_fraction=0.,
            drift_fraction=0., canopy_fraction=0., application_depth_mm=0.)
_WORKER = {}


def source_module(root, name):
    source = Path(root)/'source_snapshots/analysis_sources'/f'{name}.py'
    spec = importlib.util.spec_from_file_location('regional_archived_'+name, source)
    value = importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    return value


def close_figure_axes(fig):
    from matplotlib.text import Text
    from textwrap import fill
    for value in fig.findobj(Text):
        value.set_fontsize(max(11., value.get_fontsize()))
        value.set_fontfamily('DejaVu Sans')
    for ax in fig.axes:
        for spine in ax.spines.values():
            spine.set_visible(True)
        ax.tick_params(direction='in')
        title = ax.get_title()
        if '\n' not in title and len(title) > 24:
            ax.set_title(fill(title, width=24), fontsize=max(11., ax.title.get_fontsize()))


def archived_plot_module(root, name):
    root = Path(root);module = source_module(root, name)
    if name == 'publication_figures':
        module.R = root/'tables';module.OUT = root/'figures';module.P = root
    else:
        module.ROOT = root/'adaptive' if name == 'export_results' else root
    if name == 'analyze_results':
        module.LABELS = ['Archived regional, original allocation', 'Selected 7 October, original allocation',
            'Selected 7 October, reoptimized allocation']
    if name == 'spatial_analysis':
        original_frame = module.frame
        def frame(ax, letter, title):
            title = title.replace('Primary ', 'Archived regional ').replace('Screened ', 'Selected 7 October ')
            if title == 'Reoptimized quota':
                title = 'Selected 7 October quota'
            return original_frame(ax, letter, title)
        module.frame = frame
    if name in ['publication_figures', 'analyze_results'] and hasattr(module, 'save'):
        original_save = module.save
        def save(fig, filename):
            close_figure_axes(fig)
            return original_save(fig, filename)
        module.save = save
    return module


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, value):
    path = Path(path);path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)+'\n')
    temporary.replace(path)


def freeze_source(source, root, destination):
    source, root = Path(source).resolve(), Path(root)
    target = root/destination;target.parent.mkdir(parents=True, exist_ok=True)
    checksum = sha(source)
    if target.exists():
        if sha(target) != checksum:
            raise ValueError('Snapshot already exists with different data: '+destination)
    else:
        shutil.copy2(source, target)
    if sha(target) != checksum:
        raise ValueError('Source changed while freezing: '+str(source))
    return dict(source=str(source), snapshot=destination, sha256=checksum)


def verify_manifest(root, manifest):
    for item in manifest:
        if sha(Path(root)/item['snapshot']) != item['sha256']:
            raise ValueError('Snapshot changed: '+item['snapshot'])
        # Frozen snapshots remain authoritative after historical workspaces are
        # archived. Original paths describe provenance, not runtime dependencies.


def calibration_ready(root=CALIBRATION):
    root = Path(root)
    selected = json.loads((root/'parameters/frozen_model.json').read_text())
    if selected['selected_version'] != 'management_refit' or selected['testing_used_for_selection']:
        raise ValueError('Expected the training-selected documented-management model')
    if selected['field_calibration_years'] != [2016, 2017, 2018] or selected['field_testing_years'] != [2019]:
        raise ValueError('Documented-field partitions differ')
    for crop in ['wheat', 'maize']:
        candidate = selected['candidates']['management_refit'][crop]
        if not candidate['success'] or candidate['testing_used']:
            raise ValueError('Selected water coefficients are not frozen before retrospective testing')
        paths = sorted((root/'inputs/resolved/management_refit/field').glob(f'yang2024_{crop}_*_crop.json'))
        if len(paths) != 16:
            raise ValueError('Expected all sixteen resolved field treatments per crop')
        unique = {json.dumps(json.loads(path.read_text())['parameters'], sort_keys=True) for path in paths}
        if len(unique) != 1:
            raise ValueError('Resolved Wuqiao crop parameters are not a single shared transfer card')
    return selected


def transfer_cards(payloads):
    # These are effective cards already resolved with the selected vectors.
    # Applying the multipliers here a second time would change the fitted model.
    cards, hydro = {}, {'fallow': dict(drainage_method='matric_gradient',
        matric_interface_method='relative_arithmetic', soil_evaporation_method='two_stage_storage',
        readily_evaporable_fraction=.3)}
    for crop, payload in payloads.items():
        cards[crop] = deepcopy(payload['parameters']['crop'])
        hydro[crop] = deepcopy(payload['parameters']['hydrology'])
        if cards[crop]['germination_water_response'] != 'seed_layer_available_water':
            raise ValueError('Selected seed-layer germination response is required')
        if hydro[crop]['plant_water_stress_method'] != 'compensated_layer_depletion':
            raise ValueError('Selected compensated root-water extraction is required')
    return cards, hydro


def segment_parameters(crop, cards, hydro):
    return dict(crop=deepcopy(cards[crop]) if crop != 'fallow' else {'soil_evaporation_coefficient': 1.},
                hydrology=deepcopy(hydro[crop]))


def calendar():
    segments = [('fallow', '1996-01-01', '1996-10-11', 1996)]
    for year in range(1997, 2026):
        segments.extend([('wheat', f'{year-1}-10-12', f'{year}-06-10', year),
            ('fallow', f'{year}-06-11', f'{year}-06-14', year),
            ('maize', f'{year}-06-15', f'{year}-10-05', year)])
        if year < 2025:
            segments.append(('fallow', f'{year}-10-06', f'{year}-10-11', year))
    return segments


def training_allocation(rotation, reductions=REDUCTIONS):
    keys = ['representative_id', 'fraction', 'harvest_year']
    if rotation.duplicated(keys).any():
        raise ValueError('Duplicate unit-option-year response')
    train = rotation[rotation.harvest_year.between(1997, 2013)]
    if train.empty:
        raise ValueError('No allocation training responses')
    means = train.groupby(['representative_id', 'fraction'], as_index=False).mean(numeric_only=True)
    ids = sorted(means.representative_id.unique())
    production = means.pivot(index='representative_id', columns='fraction', values='yield_kg_ha').reindex(index=ids, columns=FRACTIONS).to_numpy()
    irrigation = means.pivot(index='representative_id', columns='fraction', values='irrigation_mm').reindex(index=ids, columns=FRACTIONS).to_numpy()
    area = means.groupby('representative_id').represented_area_ha.first().reindex(ids).to_numpy()
    if not np.isfinite(production).all() or not np.isfinite(irrigation).all() or np.any(area <= 0):
        raise ValueError('Incomplete allocation response grid')
    n = len(ids);equalities = np.zeros((n+1, n*len(FRACTIONS)))
    for i in range(n):
        equalities[i, i*5:(i+1)*5] = 1.
    records = []
    for reduction in reductions:
        target = float(np.sum(area*irrigation[:, -1])*(1-reduction));scale = max(target, 1.)
        equalities[-1] = (area[:, None]*irrigation).ravel()/scale
        result = linprog(-(area[:, None]*production).ravel()/area.sum(), A_eq=equalities,
            b_eq=np.r_[np.ones(n), target/scale], bounds=(0, 1), method='highs')
        if not result.success:
            raise RuntimeError(result.message)
        shares = result.x.reshape(n, 5)
        if not np.allclose(shares.sum(axis=1), 1., atol=1e-9) or not np.isclose(
                np.sum(area[:, None]*irrigation*shares), target, rtol=1e-9, atol=1e-5):
            raise AssertionError('Allocation violates the area or actual field-water budget')
        records.extend(dict(reduction_fraction=reduction, representative_id=rid, fraction=fraction,
            area_share=float(shares[i, j]), allocated_area_ha=float(area[i]*shares[i, j]))
            for i, rid in enumerate(ids) for j, fraction in enumerate(FRACTIONS))
    return pd.DataFrame(records)


def policy_results(rotation, allocation, reductions=REDUCTIONS, target_prefix='targeted'):
    records = []
    for year, group in rotation.groupby('harvest_year'):
        full = group[group.fraction.eq(1.)]
        conventional_water = float(np.sum(full.represented_area_ha*full.irrigation_mm)*10)
        definitions = [('conventional', 0., None)] + [(f'{kind}_{int(cut*100)}pct', cut,
            allocation if kind == target_prefix else None) for cut in reductions for kind in ['uniform', target_prefix]]
        for policy, cut, shares in definitions:
            g = group.copy()
            if shares is None:
                g['area_share'] = g.fraction.eq(1-cut).astype(float)
            else:
                g = g.merge(shares[shares.reduction_fraction.eq(cut)][['representative_id', 'fraction', 'area_share']],
                    on=['representative_id', 'fraction'], validate='one_to_one')
            active = g.represented_area_ha*g.area_share;area = float(active.sum())
            water = float(np.sum(active*g.irrigation_mm)*10)
            if not np.isclose(area, full.represented_area_ha.sum(), rtol=1e-10) or not np.isclose(
                    water, conventional_water*(1-cut), rtol=1e-9, atol=.001):
                raise AssertionError('Policy violates exact annual area or field-water budget')
            records.append(dict(harvest_year=int(year), period='training' if year <= 2013 else 'testing',
                policy=policy, reduction_fraction=cut, grain_production_t=float(np.sum(active*g.yield_kg_ha)/1000),
                field_irrigation_m3=water, crop_et_volume_m3=float(np.sum(active*g.crop_et_mm)*10),
                modeled_total_et_volume_m3=float(np.sum(active*g.modeled_total_et_mm)*10),
                bottom_drainage_volume_m3=float(np.sum(active*g.annual_bottom_drainage_mm)*10),
                runoff_volume_m3=float(np.sum(active*g.annual_runoff_mm)*10), mapped_rotation_area_ha=area))
    return pd.DataFrame(records)


def aggregate_rotations(seasons, identity='fraction'):
    active = seasons[seasons.harvest_year.ge(1997)].sort_values('start_date')
    keys = ['representative_id', identity, 'harvest_year']
    crop = active[active.crop.isin(['wheat', 'maize'])].groupby(keys, as_index=False).agg(
        yield_kg_ha=('yield_kg_ha', 'sum'), crop_et_mm=('et_mm', 'sum'))
    total = active.groupby(keys, as_index=False).agg(irrigation_mm=('irrigation_field_mm', 'sum'),
        modeled_total_et_mm=('et_mm', 'sum'), annual_bottom_drainage_mm=('bottom_drainage_mm', 'sum'),
        annual_runoff_mm=('runoff_mm', 'sum'), precipitation_mm=('precipitation_mm', 'sum'),
        represented_area_ha=('represented_area_ha', 'first'), initial_storage_mm=('initial_storage_mm', 'first'),
        final_storage_mm=('final_storage_mm', 'last'))
    total = total.merge(crop, on=keys, validate='one_to_one')
    total['water_balance_residual_mm'] = total.precipitation_mm+total.irrigation_mm-total.modeled_total_et_mm-total.annual_bottom_drainage_mm-total.annual_runoff_mm-(total.final_storage_mm-total.initial_storage_mm)
    if total.water_balance_residual_mm.abs().max() > 1e-6:
        raise AssertionError('Crop-plus-fallow rotation water balance does not close')
    return total


def select_management_rules(fixed):
    train = fixed[fixed.harvest_year.between(2003, 2013)&fixed.period.eq('training')&fixed.class_available].copy()
    class_rules = {p: {} for p in ['adaptive_95', 'adaptive_98']}
    rain_rules = {p: {} for p in POLICIES if p.startswith('rain_')}
    candidates = []
    for grouping, destination in [('relative_class', class_rules), ('relative_precipitation', rain_rules)]:
        for label, group in train.groupby(grouping):
            # Distinct classes can share a rainfall group; exact full-grid keys
            # preserve one matched baseline record per observed unit-year-class.
            keys = ['representative_id', 'harvest_year', 'relative_class']
            baseline = group[group.fraction.eq(1.)].set_index(keys).sort_index()
            if baseline.empty or baseline.index.duplicated().any():
                raise ValueError('Incomplete or duplicated management training cohort')
            choices = []
            for fraction, g in group.groupby('fraction'):
                g = g.set_index(keys).sort_index();b = baseline.reindex(g.index)
                if len(g) != len(baseline) or b.yield_kg_ha.isna().any() or not np.allclose(g.represented_area_ha, b.represented_area_ha):
                    raise ValueError('Management training candidates use unequal cohorts')
                mean = float(np.average(g.yield_kg_ha, weights=g.represented_area_ha))
                full = float(np.average(b.yield_kg_ha, weights=b.represented_area_ha))
                if full <= 0:
                    raise ValueError('Nonpositive management reference grain production')
                record = dict(grouping=grouping, relative_class=str(label), fraction=float(fraction),
                    training_grain_retention_pct=100*mean/full, n_unit_years=len(g),
                    n_training_years=int(g.index.get_level_values('harvest_year').nunique()),
                    selection_uses_testing_outcomes=False)
                choices.append(record);candidates.append(record)
            for policy in destination:
                target = int(policy.split('_')[1])
                feasible = [r for r in choices if r['training_grain_retention_pct'] >= target-1e-10]
                if not feasible:
                    raise ValueError('No feasible management candidate: '+str(label))
                destination[policy][str(label)] = min(feasible, key=lambda r: r['fraction'])['fraction']
    return class_rules, rain_rules, pd.DataFrame(candidates)


def route_quota(policy, year, member, class_rules, rain_rules):
    if policy == 'conventional_replay' or year < 2003 or member is None:
        return 1.
    available = bool(member['class_available'])
    if policy.startswith('adaptive_'):
        return float(class_rules[policy][member['relative_class']]) if available else 1.
    rain = member.get('relative_precipitation')
    if rain not in ['dry', 'wet'] or (policy.endswith('_matched') and not available):
        return 1.
    return float(rain_rules[policy][rain])


def resume_output(path, signature):
    path = Path(path);receipt = path.with_suffix('.receipt.json')
    if not path.exists():
        if receipt.exists():
            raise ValueError('Worker receipt has no output: '+str(path))
        return False
    if not receipt.exists():
        raise ValueError('Worker output has no provenance receipt: '+str(path))
    old = json.loads(receipt.read_text())
    if old['signature'] != signature:
        raise ValueError('Stale worker signature: '+str(path))
    if old['output_sha256'] != sha(path):
        raise ValueError('Modified worker output: '+str(path))
    for relative, checksum in old.get('additional_output_hashes', {}).items():
        if sha(path.parent/relative) != checksum:
            raise ValueError('Modified worker output: '+relative)
    return True


def worker_signature(root, point, mode):
    root = Path(root)
    protocol = json.loads((root/'parameters/frozen_protocol.json').read_text())
    signature = dict(source_fingerprint=protocol['source_fingerprint'], driver_sha256=sha(Path(__file__)),
        mode=mode, point={k:point[k] for k in ['representative_id', 'zone_id', 'latitude', 'elevation_m', 'represented_area_ha']})
    if mode == 'adaptive':
        signature['decisions_sha256'] = sha(root/'data/annual_policy_decisions.csv')
    return signature


def period_grain_retention(actual, reference):
    actual, reference = np.asarray(actual, float), np.asarray(reference, float)
    if actual.shape != reference.shape or actual.size == 0 or not np.isfinite(actual).all() or not np.isfinite(reference).all() or reference.mean() <= 0:
        raise ValueError('Finite paired production with a positive reference is required')
    return float(100*actual.mean()/reference.mean())


def verify_selections(root):
    root = Path(root);receipt = json.loads((root/'verification/allocation_freeze.json').read_text())
    names = dict(allocation_sha256='parameters/frozen_training_allocation.csv',
        decisions_sha256='data/annual_policy_decisions.csv', class_rules_sha256='parameters/frozen_class_rules.json',
        rain_rules_sha256='parameters/frozen_rain_rules.json')
    for key, path in names.items():
        if sha(root/path) != receipt[key]:
            raise ValueError('Frozen training selection changed: '+path)


def paired_spatial_results(actual, parent, cells):
    actual = actual[actual.crop.isin(['wheat', 'maize'])].merge(
        cells[['representative_id', 'zone_id', 'parent_representative_id']],
        on='representative_id', validate='many_to_one')
    parent = parent[parent.crop.isin(['wheat', 'maize'])].rename(columns={'representative_id': 'parent_representative_id'})
    variables = ['yield_kg_ha', 'et_mm', 'irrigation_field_mm']
    keys = ['parent_representative_id', 'crop', 'fraction', 'harvest_year']
    paired = actual.merge(parent[keys+variables], on=keys, suffixes=('_cell', '_medoid'), validate='many_to_one')
    if len(paired) != len(actual) or paired.duplicated(['zone_id', 'crop', 'fraction', 'harvest_year']).any():
        raise ValueError('Spatial actual/representative response identities differ')
    test = paired[paired.harvest_year.between(2014, 2025)];metrics = []
    for (crop, fraction), g in test.groupby(['crop', 'fraction']):
        for variable in variables:
            error = g[variable+'_medoid']-g[variable+'_cell']
            metrics.append(dict(crop=crop, fraction=float(fraction), variable=variable,
                n_cells=int(g.zone_id.nunique()), n_cell_years=len(g), rmse=float(np.sqrt(np.mean(error**2))),
                bias=float(error.mean()), mean_actual_cell_prediction=float(g[variable+'_cell'].mean())))
    responses = []
    for (zone, crop, year), g in test.groupby(['zone_id', 'crop', 'harvest_year']):
        g = g.set_index('fraction')
        for fraction in [0., .25, .5, .75]:
            actual_change = float(g.at[fraction, 'yield_kg_ha_cell']-g.at[1., 'yield_kg_ha_cell'])
            representative_change = float(g.at[fraction, 'yield_kg_ha_medoid']-g.at[1., 'yield_kg_ha_medoid'])
            responses.append(dict(zone_id=zone, crop=crop, harvest_year=int(year), fraction=fraction,
                actual_cell_yield_change_kg_ha=actual_change, representative_yield_change_kg_ha=representative_change,
                response_error_kg_ha=representative_change-actual_change))
    return paired, pd.DataFrame(metrics), pd.DataFrame(responses)


def prepare(root):
    root = Path(root)
    if (root/'verification/input_manifest.json').exists():
        verify_run(root);print('Regional inputs already frozen; verified resume', flush=True);return
    fitted = calibration_ready()
    for folder in ['data', 'parameters', 'predictions/per_representative', 'tables', 'figures',
                   'verification', 'source_snapshots', 'adaptive/per_representative', 'spatial/per_representative']:
        (root/folder).mkdir(parents=True, exist_ok=True)
    manifest = []
    def copy(source, relative):
        manifest.append(freeze_source(source, root, relative))
    for name in ['used_daily_weather.csv.gz', 'used_hydraulic_profiles.json', 'representative_cells.csv',
                 'all_source_cell_mapping.csv', 'excluded_source_cells.csv', 'forcing_temperature_order_repairs.csv']:
        source = PREVIOUS_REGIONAL/'data'/name
        copy(source, ('source_snapshots/regional_inputs/'+name) if name == 'used_hydraulic_profiles.json' else 'data/'+name)
    for name in ['used_crop_parameters.json', 'frozen_training_allocation.csv', 'regional_policy_annual_results.csv',
                 'rotation_summaries.csv', 'all_season_summaries.csv', 'preparation.json', 'verification.json']:
        copy(PREVIOUS_REGIONAL/'source_snapshots/archived_regional'/name, 'source_snapshots/archived_regional/'+name)
    for name in ['class_memberships.csv', 'observed_regional_twsa_monthly.csv', 'pre_sowing_storage_availability.csv',
                 'antecedent_precipitation_by_representative_year.csv', 'antecedent_daily_weather_used.csv.gz',
                 'used_csr_landmask_subset.nc', 'used_csr_native_mascon_mapping_subset.nc',
                 'used_csr_native_monthly_subset.nc', 'exact_grace_support_weights.nc']:
        copy(PREVIOUS_REGIONAL/'data'/name, 'data/'+name)
    for name in ['withheld_source_cells.csv', 'used_daily_weather.csv.gz', 'used_hydraulic_profiles.json']:
        source = PREVIOUS_REGIONAL/'data/spatial'/name
        copy(source, ('source_snapshots/regional_inputs/spatial/'+name) if name == 'used_hydraulic_profiles.json' else 'data/spatial/'+name)
    copy(PREVIOUS_REGIONAL/'source_snapshots/original_class_thresholds.json', 'source_snapshots/original_class_thresholds.json')
    copy(CALIBRATION/'parameters/frozen_model.json', 'parameters/selected_model.json')
    for name in ['run_receipt.json', 'independent_checks.json', 'input_manifest.json']:
        copy(CALIBRATION/'verification'/name, 'source_snapshots/calibration_verification/'+name)
    for source in sorted((CALIBRATION/'data').iterdir()):
        if source.is_file():
            copy(source, 'data/calibration_provenance/'+source.name)
    native = CALIBRATION/'source_snapshots/native'
    if not (native/'research/ncp_irrigation/model.py').exists():
        raise FileNotFoundError('Revised native model snapshot is required before regional preparation')
    for source in sorted(native.rglob('*.py')):
        if '__pycache__' not in source.parts:
            copy(source, 'source_snapshots/native_process/'+str(source.relative_to(native)))
    native_hashes = {str(p.relative_to(native)): sha(p) for p in sorted(native.rglob('*.py'))}
    write_json(root/'verification/native_source_identity.json', dict(source=str(native.resolve()),
        source_binding='exact selected documented-management field engine', files=native_hashes,
        n_python_files=len(native_hashes), sha256=hashlib.sha256(json.dumps(native_hashes, sort_keys=True).encode()).hexdigest()))
    archive = root/'source_snapshots/native_process_source.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as stream:
        for relative in native_hashes:
            info = zipfile.ZipInfo(relative, (2026, 10, 7, 0, 0, 0));info.compress_type = zipfile.ZIP_DEFLATED
            stream.writestr(info, (root/'source_snapshots/native_process'/relative).read_bytes())
    manifest.append(dict(source=str(archive.resolve()), snapshot=str(archive.relative_to(root)), sha256=sha(archive)))
    copy(Path(__file__), 'source_snapshots/regional_recalculation.py')
    for name in ['publication_figures', 'analyze_results', 'spatial_analysis', 'export_results', 'build_classes']:
        copy(PREVIOUS_REGIONAL/f'source_snapshots/analysis_sources/{name}.py', f'source_snapshots/analysis_sources/{name}.py')
    copy(CALIBRATION/'analysis_source/retention_priors.py', 'source_snapshots/analysis_sources/retention_priors.py')
    for name in ['baseline_objective', 'fit_objective', 'extraction_fit', 'field_water_fit']:
        copy(CALIBRATION/f'analysis_source/{name}.py', f'source_snapshots/parameter_adjustment_sources/{name}.py')
    payloads = {}
    for crop in ['wheat', 'maize']:
        name = f'yang2024_{crop}_2015_2016_W0_crop.json'
        relative = f'source_snapshots/selected_resolved_field/{name}'
        copy(CALIBRATION/'inputs/resolved/management_refit/field'/name, relative)
        copy(CALIBRATION/'inputs/field'/name, f'source_snapshots/inherited_shared_base/{name}')
        payloads[crop] = json.loads((root/relative).read_text())
    cards, hydro = transfer_cards(payloads)
    retention = source_module(root, 'retention_priors')
    for subdir in ['', 'spatial/']:
        source_path = root/f'source_snapshots/regional_inputs/{subdir}used_hydraulic_profiles.json'
        profiles = json.loads(source_path.read_text())
        for profile in profiles:
            profile['layers'] = [retention.reconstruct(layer) for layer in profile['layers']]
        target = root/f'data/{subdir}used_hydraulic_profiles.json'
        write_json(target, profiles)
        manifest.append(dict(source=str(target.resolve()), snapshot=str(target.relative_to(root)), sha256=sha(target)))
    write_json(root/'verification/parameter_transfer.json', dict(selected_model_sha256=sha(root/'parameters/selected_model.json'),
        resolved_parameters_transferred_without_reapplying_multipliers=True, station_offsets_used=False,
        field_resolved_parameter_sets_per_crop=1, inherited_shared_base_preserved=True,
        effective_parameters={crop: payload['parameters'] for crop, payload in payloads.items()},
        radiation_basis='intercepted PAR', par_fraction=.45,
        hydraulic_retention_priors='VG curves through original FC at -3300 mm and WP at -150000 mm; original endpoints retained',
        regional_initialization='one 1996 profile at WP + 0.8*(FC-WP), continuous carry-over thereafter',
        regional_results_classification='conditional scenarios; not independent validation or causal groundwater effects'))
    for crop in ['wheat', 'maize']:
        growth = cards[crop]['growth_process']
        if growth['version'] != 'canopy_v5' or 'max_lai' in growth or 'sla_by_stage' not in growth:
            raise ValueError('Expected revised stage-specific canopy without fitted maximum LAI')
    write_json(root/'parameters/used_crop_parameters.json', cards)
    write_json(root/'parameters/used_hydrology_by_crop.json', hydro)
    reps = pd.read_csv(root/'data/representative_cells.csv');mapping = pd.read_csv(root/'data/all_source_cell_mapping.csv')
    if len(reps) != 32 or len(mapping) != 3641 or reps.representative_id.duplicated().any():
        raise ValueError('The frozen 32-unit regional footprint differs')
    areas = mapping.groupby('representative_id').used_area_ha.sum().sort_index()
    if not np.allclose(areas, reps.set_index('representative_id').represented_area_ha.sort_index(), rtol=1e-12):
        raise ValueError('Represented areas do not reconcile with exact source-cell mapping')
    memberships = pd.read_csv(root/'data/class_memberships.csv')
    if memberships.duplicated(['representative_id', 'harvest_year']).any() or not memberships.n_antecedent_days.eq(92).all():
        raise ValueError('Invalid frozen pre-season class identities')
    if sorted(memberships.loc[~memberships.class_available, 'harvest_year'].unique()) != [2014, 2018, 2019]:
        raise ValueError('Missing GRACE years changed')
    manifest.extend(dict(source=str((root/name).resolve()), snapshot=name, sha256=sha(root/name))
        for name in ['parameters/used_crop_parameters.json', 'parameters/used_hydrology_by_crop.json'])
    write_json(root/'verification/input_manifest.json', manifest)
    write_json(root/'parameters/frozen_protocol.json', dict(display_model_name='Open Crop Model',
        model_variant='documented_management_water_calibration', radiation_basis='intercepted PAR', quota_fractions=FRACTIONS,
        reductions=REDUCTIONS, calendar=calendar(), technology=TECH, training_allocation_years=list(range(1997, 2014)),
        class_selection_years=list(range(2003, 2014)), evaluation_years=list(range(2014, 2026)),
        policy_selection_uses_evaluation=False, regional_crop_parameter_validation=False,
        original_class_thresholds_preserved=True, original_completed_runs_modified=False,
        selected_version=fitted['selected_version'], selected_model_sha256=sha(root/'parameters/selected_model.json'),
        native_source_sha256=sha(archive), native_file_identity_sha256=hashlib.sha256(json.dumps(native_hashes, sort_keys=True).encode()).hexdigest(),
        regional_results_classification='conditional model scenarios', field_testing_retrospective=True,
        field_water_calibration_years=[2016, 2017, 2018], field_testing_years=[2019],
        driver_sha256=sha(Path(__file__)), source_fingerprint=hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()))
    verify_run(root);print('Frozen revised regional inputs and native source', flush=True)


def verify_run(root):
    root = Path(root);manifest = json.loads((root/'verification/input_manifest.json').read_text())
    verify_manifest(root, manifest)
    protocol = json.loads((root/'parameters/frozen_protocol.json').read_text())
    fingerprint = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    if protocol['source_fingerprint'] != fingerprint:
        raise ValueError('Regional source manifest fingerprint differs from frozen protocol')
    if protocol['driver_sha256'] != sha(Path(__file__)):
        raise ValueError('Regional runner differs from frozen source')
    return protocol


def initialize_worker(root):
    global _WORKER
    root = Path(root);native = root/'source_snapshots/native_process'
    sys.path.insert(0, str(native))
    from research.ncp_irrigation.model import simulate_season
    if Path(simulate_season.__code__.co_filename).resolve() != (native/'research/ncp_irrigation/model.py').resolve():
        raise AssertionError('Worker imported a model outside the revised frozen source')
    weather = pd.read_csv(root/'data/used_daily_weather.csv.gz')
    _WORKER = dict(root=root, simulate=simulate_season,
        weather={int(i): g.drop(columns='point').set_index('date').sort_index() for i, g in weather.groupby('point')},
        soils={int(s['representative_id']): s['layers'] for s in json.loads((root/'data/used_hydraulic_profiles.json').read_text())},
        cards=json.loads((root/'parameters/used_crop_parameters.json').read_text()),
        hydro=json.loads((root/'parameters/used_hydrology_by_crop.json').read_text()),
        source_fingerprint=json.loads((root/'parameters/frozen_protocol.json').read_text())['source_fingerprint'])
    decisions = root/'data/annual_policy_decisions.csv'
    if decisions.exists():
        d = pd.read_csv(decisions)
        _WORKER['decisions'] = {(int(r), p): g.set_index('harvest_year').quota_fraction.to_dict()
            for (r, p), g in d.groupby(['representative_id', 'policy'])}
        _WORKER['decisions_sha256'] = sha(decisions)
    spatial_weather = pd.read_csv(root/'data/spatial/used_daily_weather.csv.gz')
    _WORKER['spatial_weather'] = {int(i): g.drop(columns='point').set_index('date').sort_index()
        for i, g in spatial_weather.groupby('point')}
    _WORKER['spatial_soils'] = {int(s['representative_id']): s['layers']
        for s in json.loads((root/'data/spatial/used_hydraulic_profiles.json').read_text())}


def simulate_representative(task):
    point, mode = task;root = _WORKER['root'];rid = int(point['representative_id'])
    path = root/(f'predictions/per_representative/rep_{rid:03d}.csv' if mode == 'responses'
                 else f'{mode}/per_representative/rep_{rid:03d}.csv')
    signature = worker_signature(root, point, mode)
    if resume_output(path, signature):
        return str(path)
    fixed = mode in ['responses', 'spatial']
    options = FRACTIONS if fixed else POLICIES+(['conventional_replay'] if rid in [0, 31] else [])
    summaries, diagnostic = [], []
    weather = _WORKER['spatial_weather' if mode == 'spatial' else 'weather'][rid]
    layers = _WORKER['spatial_soils' if mode == 'spatial' else 'soils'][rid]
    for option in options:
        state = None
        quotas = {} if fixed or option == 'conventional_replay' else _WORKER['decisions'][(rid, option)]
        for crop, start, end, year in calendar():
            fraction = float(option) if fixed else float(quotas.get(year, 1.))
            forcing = weather.loc[start:end].reset_index().to_dict('records')
            if len(forcing) != (pd.Timestamp(end)-pd.Timestamp(start)).days+1:
                raise ValueError('Incomplete frozen daily weather calendar')
            inputs = dict(crop=crop, maturity_group='middle', latitude_deg=point['latitude'], elevation_m=point['elevation_m'],
                start_date=start, end_date=end, weather=forcing, soil_layers=layers, technology=TECH,
                et0_method='provided', irrigation_events=[], management_class='fertilized')
            if state is None:
                inputs['initial_theta'] = [l['wilting_point']+.8*(l['field_capacity']-l['wilting_point']) for l in layers]
            if crop != 'fallow':
                inputs['cutting_date'] = end
                schedule = [(start, 60), (f'{year}-03-25', 90), (f'{year}-04-15', 90), (f'{year}-05-10', 60)] if crop == 'wheat' else [(start, 80)]
                inputs['irrigation_events'] = [dict(event_id=f'quota_{i}', date=day, amount_mm=amount*fraction,
                    measurement_location='field') for i, (day, amount) in enumerate(schedule) if fraction > 0]
            previous = state.storage_mm() if state is not None else None
            parameters = segment_parameters(crop, _WORKER['cards'], _WORKER['hydro'])
            result = _WORKER['simulate'](inputs, parameters, state)
            continuity = result.summary['initial_storage_mm']-previous if previous is not None else 0.
            water = max(abs(d['balance_residual_mm']) for d in result.daily)
            carbon = max(abs(d['crop_carbon_residual_kg_ha']) for d in result.daily)
            excess = max(d['yield_kg_ha']-d['grain_fill_ceiling_kg_ha'] for d in result.daily)
            if abs(continuity) > 1e-7 or water > 1e-6 or carbon > 1e-6 or excess > 1e-6:
                raise AssertionError('Continuous soil, water/carbon or grain-sink conservation failed')
            state = result.final_state
            meta = dict(representative_id=rid, source_zone_id=point['zone_id'], crop=crop, harvest_year=year,
                start_date=start, end_date=end, represented_area_ha=point['represented_area_ha'])
            meta.update({'fraction': fraction} if fixed else {'policy': option, 'quota_fraction': fraction})
            summary = {k:v for k,v in result.summary.items() if not isinstance(v, (dict, list))}
            summaries.append(dict(**meta, maximum_daily_residual_mm=water, maximum_carbon_residual_kg_ha=carbon,
                maximum_grain_ceiling_excess_kg_ha=excess, storage_continuity_error_mm=continuity,
                root_extraction_fraction_day=parameters['hydrology'].get('root_extraction_fraction_day', 1.), **summary))
            if (mode == 'adaptive' and rid in [0, 31]) or (mode == 'responses' and rid < 2 and year == 2015):
                diagnostic.extend({**meta, **day} for day in result.daily)
    path.parent.mkdir(parents=True, exist_ok=True);temporary = path.with_suffix('.tmp.csv')
    pd.DataFrame(summaries).to_csv(temporary, index=False);temporary.replace(path)
    extras = {}
    if diagnostic:
        daily_path = path.with_suffix('.daily.csv.gz');pd.DataFrame(diagnostic).to_csv(daily_path, index=False)
        extras[daily_path.name] = sha(daily_path)
    write_json(path.with_suffix('.receipt.json'), dict(signature=signature, output_sha256=sha(path),
        additional_output_hashes=extras, n_segments=len(summaries)))
    return str(path)


def run_workers(root, mode, workers):
    verify_run(root)
    reps = pd.read_csv(Path(root)/('data/spatial/withheld_source_cells.csv' if mode == 'spatial' else 'data/representative_cells.csv'))
    with ProcessPoolExecutor(max_workers=workers, mp_context=get_context('spawn'),
            initializer=initialize_worker, initargs=(str(root),)) as pool:
        futures = [pool.submit(simulate_representative, (point, mode)) for point in reps.to_dict('records')]
        for i, future in enumerate(as_completed(futures), 1):
            print(f'{mode}: {i}/{len(futures)} {Path(future.result()).name}', flush=True)


def response_seasons(root):
    reps = pd.read_csv(root/'data/representative_cells.csv')
    paths = [root/f'predictions/per_representative/rep_{int(i):03d}.csv' for i in reps.representative_id]
    if not all(p.exists() and p.with_suffix('.receipt.json').exists() for p in paths):
        raise ValueError('All revised regional responses must complete first')
    for point, path in zip(reps.to_dict('records'), paths):
        resume_output(path, worker_signature(root, point, 'responses'))
    seasons = pd.concat([pd.read_csv(p) for p in paths], ignore_index=True)
    if len(seasons) != 32*5*116 or seasons.duplicated(['representative_id', 'fraction', 'start_date']).any():
        raise ValueError('Regional fixed-history response grid differs')
    return seasons


def spatial_checks(root, parent):
    cells = pd.read_csv(root/'data/spatial/withheld_source_cells.csv')
    for point in cells.to_dict('records'):
        path = root/f'spatial/per_representative/rep_{int(point["representative_id"]):03d}.csv'
        if not resume_output(path, worker_signature(root, point, 'spatial')):
            raise ValueError('Spatial actual-cell responses are incomplete')
    actual = pd.concat([pd.read_csv(root/f'spatial/per_representative/rep_{int(i):03d}.csv')
        for i in cells.representative_id], ignore_index=True)
    if len(actual) != 8*5*116:
        raise ValueError('Eight actual-cell five-fraction histories are incomplete')
    actual.to_csv(root/'spatial/all_season_summaries.csv', index=False)
    paired, metrics, responses = paired_spatial_results(actual, parent, cells)
    if len(paired) != 8*5*29*2 or not metrics.n_cell_years.eq(96).all():
        raise ValueError('Spatial check calendar/support differs from original check')
    paired.to_csv(root/'tables/all_quota_paired_predictions.csv', index=False)
    metrics.to_csv(root/'tables/full_quota_spatial_metrics.csv', index=False)
    responses.to_csv(root/'tables/management_response_errors.csv', index=False)
    rows = []
    for (crop, fraction), g in responses.groupby(['crop', 'fraction']):
        rows.append(dict(crop=crop, fraction=float(fraction), n_cell_years=len(g),
            rmse_response_error_kg_ha=float(np.sqrt(np.mean(g.response_error_kg_ha**2))),
            mean_response_error_kg_ha=float(g.response_error_kg_ha.mean())))
    pd.DataFrame(rows).to_csv(root/'tables/management_response_metrics.csv', index=False)
    write_json(root/'verification/spatial_check_completion.json', dict(all_checks_passed=True,
        n_cells=8, quota_fractions=FRACTIONS, native_process_changed=True,
        both_actual_cells_and_representatives_recomputed=True, field_observation_validation=False,
        purposive_stress_test=True, probability_sample=False,
        maximum_water_residual_mm=float(actual.maximum_daily_residual_mm.max()),
        maximum_carbon_residual_kg_ha=float(actual.maximum_carbon_residual_kg_ha.max())))


def allocations(root):
    root = Path(root);verify_run(root)
    seasons = response_seasons(root);seasons.to_csv(root/'predictions/all_season_summaries.csv', index=False)
    spatial_checks(root, seasons)
    rotation = aggregate_rotations(seasons);rotation.to_csv(root/'predictions/rotation_summaries.csv', index=False)
    allocation = training_allocation(rotation);allocation.to_csv(root/'parameters/frozen_training_allocation.csv', index=False)
    primary = policy_results(rotation, allocation);primary.to_csv(root/'tables/regional_policy_annual_results.csv', index=False)
    old = pd.read_csv(root/'source_snapshots/archived_regional/frozen_training_allocation.csv')
    transferred = policy_results(rotation, old, target_prefix='archived_targeted')
    revised = primary.copy();revised.policy = revised.policy.str.replace('targeted_', 'refit_targeted_', regex=False)
    revised['parameter_set'] = 'source_screened'
    transferred = transferred[transferred.policy.str.startswith('archived_targeted')].assign(parameter_set='source_screened')
    archived = pd.read_csv(root/'source_snapshots/archived_regional/regional_policy_annual_results.csv')
    archived.policy = archived.policy.str.replace('targeted_', 'archived_targeted_', regex=False)
    archived['parameter_set'] = 'archived'
    combined = pd.concat([archived, revised, transferred], ignore_index=True)
    combined.to_csv(root/'tables/all_parameter_policy_years.csv', index=False)
    contrasts = []
    for (card, year), g in combined.groupby(['parameter_set', 'harvest_year']):
        g = g.set_index('policy')
        for cut in REDUCTIONS:
            for kind in ['archived_targeted', 'refit_targeted']:
                policy = f'{kind}_{int(cut*100)}pct'
                if policy not in g.index:
                    continue
                target, uniform = g.loc[policy], g.loc[f'uniform_{int(cut*100)}pct']
                contrasts.append(dict(parameter_set=card, harvest_year=int(year), period=target.period,
                    reduction_fraction=cut, policy=kind, additional_grain_t=target.grain_production_t-uniform.grain_production_t,
                    additional_et_m3=target.modeled_total_et_volume_m3-uniform.modeled_total_et_volume_m3,
                    additional_drainage_m3=target.bottom_drainage_volume_m3-uniform.bottom_drainage_volume_m3,
                    field_water_difference_m3=target.field_irrigation_m3-uniform.field_irrigation_m3))
    contrasts = pd.DataFrame(contrasts);contrasts.to_csv(root/'tables/policy_contrasts.csv', index=False)
    combined.groupby(['parameter_set', 'period', 'policy'], as_index=False).mean(numeric_only=True).to_csv(root/'tables/policy_period_means.csv', index=False)
    changes = allocation.merge(old, on=['reduction_fraction', 'representative_id', 'fraction'], suffixes=('_new', '_old'), validate='one_to_one')
    changes['area_share_difference'] = changes.area_share_new-changes.area_share_old
    changes.to_csv(root/'tables/allocation_changes.csv', index=False)
    memberships = pd.read_csv(root/'data/class_memberships.csv')
    fixed = rotation[rotation.harvest_year.between(2003, 2025)].merge(memberships.drop(columns='represented_area_ha'),
        on=['representative_id', 'harvest_year'], validate='many_to_one')
    class_analysis = source_module(root, 'build_classes');class_analysis.ROOT = root
    metrics = class_analysis.METRICS
    reference = fixed[fixed.fraction.eq(1.)][['representative_id', 'harvest_year']+metrics]
    reference = reference.rename(columns={column: 'full_'+column for column in metrics})
    fixed = fixed.merge(reference, on=['representative_id', 'harvest_year'], validate='many_to_one')
    for column in metrics:
        fixed['delta_'+column] = fixed[column]-fixed['full_'+column]
    fixed['paired_grain_retention_fraction'] = fixed.yield_kg_ha/fixed.full_yield_kg_ha
    fixed.to_csv(root/'data/class_fixed_quota_predictions.csv', index=False)
    response_table, class_years = class_analysis.response_tables(fixed, persist=False)
    response_table.to_csv(root/'tables/class_quota_responses.csv', index=False)
    class_years.to_csv(root/'tables/class_quota_yearly_responses.csv', index=False)
    api_selected, class_evaluation = class_analysis.select_candidates(response_table, class_years, criteria=(.95, .98), persist=False)
    class_evaluation.to_csv(root/'tables/selected_quota_testing_evaluation.csv', index=False)
    exposure = []
    for (period, label), g in memberships.groupby(['period', 'relative_class']):
        exposure.append(dict(period=period, relative_class=label, n_representative_years=len(g),
            n_harvest_years=int(g.harvest_year.nunique()), class_area_year_ha=float(g.represented_area_ha.sum()),
            fraction_of_period_area_year=float(g.represented_area_ha.sum()/memberships.loc[memberships.period.eq(period), 'represented_area_ha'].sum())))
    pd.DataFrame(exposure).to_csv(root/'tables/class_counts_and_area_exposure.csv', index=False)
    class_rules, rain_rules, candidates = select_management_rules(fixed)
    for row in api_selected.itertuples():
        if class_rules[f'adaptive_{int(row.grain_retention_target_pct)}'][row.relative_class] != row.selected_quota_fraction:
            raise AssertionError('Independent class selector differs from frozen class-response arithmetic')
    write_json(root/'parameters/frozen_class_rules.json', class_rules);write_json(root/'parameters/frozen_rain_rules.json', rain_rules)
    candidates.to_csv(root/'tables/training_selection_candidates.csv', index=False)
    selected = []
    for policy, rules in class_rules.items():
        for label, quota in rules.items():
            candidate = candidates[(candidates.grouping == 'relative_class')&candidates.relative_class.eq(label)&candidates.fraction.eq(quota)].iloc[0]
            selected.append(dict(relative_class=label, grain_retention_target_pct=int(policy.split('_')[1]),
                primary_criterion=policy == 'adaptive_95', selected_quota_fraction=quota, field_irrigation_mm=380*quota,
                training_grain_retention_pct=candidate.training_grain_retention_pct, selection_uses_testing_responses=False))
    pd.DataFrame(selected).to_csv(root/'tables/training_selected_quotas.csv', index=False)
    decisions = []
    reps = pd.read_csv(root/'data/representative_cells.csv')
    lookup = {(int(r.representative_id), int(r.harvest_year)): r._asdict() for r in memberships.itertuples(index=False)}
    for point in reps.itertuples():
        for year in range(1997, 2026):
            member = lookup.get((int(point.representative_id), year))
            for policy in POLICIES:
                quota = route_quota(policy, year, member, class_rules, rain_rules)
                decisions.append(dict(representative_id=int(point.representative_id), harvest_year=year, policy=policy,
                    quota_fraction=quota, relative_class=member['relative_class'] if member else 'before_class_period',
                    class_available=bool(member['class_available']) if member else False,
                    relative_precipitation=member['relative_precipitation'] if member else 'before_class_period',
                    decision_date=f'{year-1}-10-11', wheat_irrigation_mm=300*quota, maize_irrigation_mm=80*quota,
                    represented_area_ha=point.represented_area_ha))
    pd.DataFrame(decisions).to_csv(root/'data/annual_policy_decisions.csv', index=False)
    write_json(root/'verification/allocation_freeze.json', dict(allocation_sha256=sha(root/'parameters/frozen_training_allocation.csv'),
        decisions_sha256=sha(root/'data/annual_policy_decisions.csv'), class_rules_sha256=sha(root/'parameters/frozen_class_rules.json'),
        rain_rules_sha256=sha(root/'parameters/frozen_rain_rules.json'), selection_uses_testing_outcomes=False))
    print('Revised spatial allocation and class/rainfall strategies frozen from training responses', flush=True)


def aggregate_adaptive(root):
    root = Path(root);verify_selections(root);reps = pd.read_csv(root/'data/representative_cells.csv')
    for point in reps.to_dict('records'):
        path = root/f'adaptive/per_representative/rep_{int(point["representative_id"]):03d}.csv'
        if not resume_output(path, worker_signature(root, point, 'adaptive')):
            raise ValueError('Adaptive/rainfall responses are incomplete')
    seasons = pd.concat([pd.read_csv(root/f'adaptive/per_representative/rep_{int(i):03d}.csv') for i in reps.representative_id], ignore_index=True)
    if len(seasons) != 32*6*116+2*116 or seasons.duplicated(['representative_id', 'policy', 'start_date']).any():
        raise ValueError('Adaptive/rainfall continuous response grid differs')
    fixed = pd.read_csv(root/'predictions/all_season_summaries.csv')
    replay = seasons[seasons.policy.eq('conventional_replay')].merge(fixed[fixed.fraction.eq(1.)],
        on=['representative_id', 'crop', 'start_date', 'end_date', 'harvest_year'], suffixes=('_new', '_fixed'), validate='one_to_one')
    differences = {column: float((replay[column+'_new']-replay[column+'_fixed']).abs().max())
        for column in ['yield_kg_ha', 'et_mm', 'bottom_drainage_mm', 'final_storage_mm']}
    if len(replay) != 232 or max(differences.values()) > 1e-7:
        raise AssertionError('Adaptive conventional histories do not reproduce revised fixed histories')
    seasons.to_csv(root/'adaptive/seasonal_predictions.csv', index=False)
    rotation = aggregate_rotations(seasons[seasons.policy.isin(POLICIES)], identity='policy')
    reference = pd.read_csv(root/'predictions/rotation_summaries.csv')
    reference = reference[reference.fraction.eq(1.)].drop(columns='fraction').assign(policy='conventional')
    rotation = pd.concat([rotation, reference], ignore_index=True)
    members = pd.read_csv(root/'data/class_memberships.csv')[['representative_id', 'harvest_year', 'relative_class', 'class_available']]
    rotation = rotation.merge(members, on=['representative_id', 'harvest_year'], how='left', validate='many_to_one')
    rotation['class_available'] = rotation.class_available.fillna(False).astype(bool)
    rotation.to_csv(root/'adaptive/rotation_predictions.csv', index=False)
    regional = []
    for (policy, year), g in rotation.groupby(['policy', 'harvest_year']):
        if len(g) != 32:
            raise AssertionError('Incomplete adaptive footprint')
        area = g.represented_area_ha
        item = dict(policy=policy, harvest_year=int(year), period='training' if year <= 2013 else 'testing',
            represented_area_ha=float(area.sum()), class_available=bool(g.class_available.all()),
            class_available_area_pct=float(100*np.sum(area*g.class_available)/area.sum()),
            grain_production_t=float(np.sum(area*g.yield_kg_ha)/1000))
        for column in ['irrigation_mm', 'modeled_total_et_mm', 'annual_bottom_drainage_mm', 'annual_runoff_mm']:
            item[column] = float(np.average(g[column], weights=area))
            item[column.replace('_mm', '_volume_m3')] = float(np.sum(area*g[column])*10)
        regional.append(item)
    regional = pd.DataFrame(regional);full = regional[regional.policy.eq('conventional')].set_index('harvest_year')
    regional['grain_retention_pct'] = 100*regional.grain_production_t/regional.harvest_year.map(full.grain_production_t)
    regional['irrigation_reduction_mm'] = regional.harvest_year.map(full.irrigation_mm)-regional.irrigation_mm
    regional['et_reduction_mm'] = regional.harvest_year.map(full.modeled_total_et_mm)-regional.modeled_total_et_mm
    regional.to_csv(root/'tables/adaptive_regional_policy_annual.csv', index=False)
    summaries = []
    for policy, g in regional[regional.harvest_year.between(2014, 2025)].groupby('policy'):
        for scope, h in [('common_GRACE_available', g[g.class_available]), ('all_testing_years', g)]:
            reference = full.loc[h.harvest_year]
            summaries.append(dict(policy=policy, scope=scope, n_years=len(h),
                training_target_pct=int(policy.split('_')[1]) if '_' in policy else None,
                grain_retention_pct=period_grain_retention(h.grain_production_t, reference.grain_production_t),
                grain_production_Mt=float(h.grain_production_t.mean()/1e6), field_irrigation_mm=float(h.irrigation_mm.mean()),
                irrigation_reduction_mm=float(h.irrigation_reduction_mm.mean()), et_reduction_mm=float(h.et_reduction_mm.mean()),
                actual_ET_mm=float(h.modeled_total_et_mm.mean()), annual_minimum_grain_retention_pct=float(h.grain_retention_pct.min()),
                annual_maximum_grain_retention_pct=float(h.grain_retention_pct.max()),
                years_retaining_at_least_95pct=int(h.grain_retention_pct.ge(95).sum())))
    pd.DataFrame(summaries).to_csv(root/'tables/policy_comparison.csv', index=False)
    write_json(root/'verification/adaptive_completion.json', dict(all_checks_passed=True,
        conventional_replay_differences=differences, n_segments=len(seasons),
        maximum_water_residual_mm=float(seasons.maximum_daily_residual_mm.max()),
        maximum_carbon_residual_kg_ha=float(seasons.maximum_carbon_residual_kg_ha.max()),
        maximum_storage_discontinuity_mm=float(seasons.storage_continuity_error_mm.abs().max()),
        maximum_annual_residual_mm=float(rotation.water_balance_residual_mm.abs().max()),
        decisions_sha256=sha(root/'data/annual_policy_decisions.csv')))
    print('Six revised continuous management strategies and both evaluation scopes aggregated', flush=True)


def spatial_tables(root):
    mapping = pd.read_csv(root/'data/all_source_cell_mapping.csv')
    new = pd.read_csv(root/'predictions/rotation_summaries.csv')
    old = pd.read_csv(root/'source_snapshots/archived_regional/rotation_summaries.csv')
    allocations = {'source_screened': pd.read_csv(root/'parameters/frozen_training_allocation.csv'),
        'archived': pd.read_csv(root/'source_snapshots/archived_regional/frozen_training_allocation.csv')}
    years = []
    variables = ['yield_kg_ha', 'irrigation_mm', 'modeled_total_et_mm', 'annual_bottom_drainage_mm']
    for label, response in [('archived', old), ('source_screened', new)]:
        response = response[response.harvest_year.between(2014, 2025)]
        for cut in REDUCTIONS:
            allocation = allocations[label][allocations[label].reduction_fraction.eq(cut)]
            shares = response.merge(allocation[['representative_id', 'fraction', 'area_share']], on=['representative_id', 'fraction'], validate='many_to_one')
            for (rid, year), g in shares.groupby(['representative_id', 'harvest_year']):
                uniform = g[g.fraction.eq(1-cut)].iloc[0]
                row = dict(parameter_set=label, representative_id=int(rid), harvest_year=int(year),
                    reduction_fraction=cut, represented_area_ha=float(uniform.represented_area_ha),
                    targeted_mean_quota=float(np.sum(g.fraction*g.area_share)))
                for column in variables:
                    target = float(np.sum(g[column]*g.area_share));row['targeted_'+column] = target
                    row['uniform_'+column] = float(uniform[column]);row['delta_'+column] = target-float(uniform[column])
                years.append(row)
    annual = pd.DataFrame(years);annual.to_csv(root/'tables/spatial_unit_annual_results.csv', index=False)
    keys = ['parameter_set', 'reduction_fraction', 'representative_id']
    units = annual.groupby(keys, as_index=False).mean(numeric_only=True).drop(columns='harvest_year')
    counts = annual.assign(gain=annual.delta_yield_kg_ha.gt(1e-6), loss=annual.delta_yield_kg_ha.lt(-1e-6)).groupby(keys, as_index=False).agg(grain_gain_years=('gain', 'sum'), grain_loss_years=('loss', 'sum'))
    units = units.merge(counts, on=keys, validate='one_to_one');units.to_csv(root/'tables/spatial_unit_period_means.csv', index=False)
    changes = pd.read_csv(root/'tables/allocation_changes.csv');changes['minimum_changed_share'] = changes.area_share_difference.abs()/2
    turnover = changes.groupby(['reduction_fraction', 'representative_id'], as_index=False).minimum_changed_share.sum()
    reps = pd.read_csv(root/'data/representative_cells.csv')
    turnover = turnover.merge(reps[['representative_id', 'represented_area_ha']], on='representative_id', validate='many_to_one')
    totals = []
    for cut, g in turnover.groupby('reduction_fraction'):
        changed_area = float(np.sum(g.minimum_changed_share*g.represented_area_ha))
        totals.append(dict(reduction_fraction=cut, minimum_reassigned_area_ha=changed_area,
            minimum_reassigned_area_fraction=changed_area/float(g.represented_area_ha.sum())))
    pd.DataFrame(totals).to_csv(root/'tables/allocation_turnover.csv', index=False)
    selected = units[units.reduction_fraction.eq(.5)].drop(columns='reduction_fraction').pivot(index='representative_id', columns='parameter_set')
    selected.columns = [f'{parameter}_{column}' for column, parameter in selected.columns]
    selected = selected.reset_index().merge(turnover[turnover.reduction_fraction.eq(.5)][['representative_id', 'minimum_changed_share']], on='representative_id', validate='one_to_one')
    columns = ['zone_id', 'representative_id', 'latitude', 'longitude', 'elevation_m', 'used_area_ha', 'profile_available_water_mm', 'training_mean_precipitation_mm', 'training_mean_et0_mm']
    cells = mapping[columns].merge(selected, on='representative_id', validate='many_to_one')
    cells['training_climatic_deficit_mm'] = cells.training_mean_et0_mm-cells.training_mean_precipitation_mm
    cells['screened_minus_archived_quota'] = cells.source_screened_targeted_mean_quota-cells.archived_targeted_mean_quota
    cells['latitude_band'] = pd.cut(cells.latitude, [32, 36, 38, 41], right=False, labels=['Southern (32–36°N)', 'Central (36–38°N)', 'Northern (38–41°N)'])
    if cells.latitude_band.isna().any():
        raise AssertionError('Source cell missing a latitude reporting band')
    cells.to_csv(root/'tables/source_cell_map_values.csv', index=False)
    bands = []
    for label, g in cells.groupby('latitude_band', observed=True):
        area = g.used_area_ha;row = dict(latitude_band=str(label), mapped_area_ha=float(area.sum()), area_fraction=float(area.sum()/cells.used_area_ha.sum()))
        for column in ['elevation_m', 'training_mean_precipitation_mm', 'training_mean_et0_mm', 'training_climatic_deficit_mm', 'profile_available_water_mm', 'minimum_changed_share']:
            row[column] = float(np.average(g[column], weights=area))
        for parameter in ['archived', 'source_screened']:
            for column in ['targeted_mean_quota', 'delta_yield_kg_ha', 'delta_irrigation_mm', 'delta_modeled_total_et_mm', 'delta_annual_bottom_drainage_mm']:
                row[parameter+'_'+column] = float(np.average(g[parameter+'_'+column], weights=area))
            row[parameter+'_grain_gain_t'] = float(np.sum(g[parameter+'_delta_yield_kg_ha']*area)/1000)
            row[parameter+'_extra_et_m3'] = float(np.sum(g[parameter+'_delta_modeled_total_et_mm']*area)*10)
        bands.append(row)
    pd.DataFrame(bands).to_csv(root/'tables/spatial_latitude_bands.csv', index=False)


def analyze(root):
    root = Path(root);verify_run(root);verify_selections(root)
    if not (root/'verification/adaptive_completion.json').exists():
        raise ValueError('All revised adaptive/rainfall histories must complete before analysis')
    spatial_tables(root)
    contrasts = pd.read_csv(root/'tables/policy_contrasts.csv')
    summaries = []
    for key, g in contrasts[contrasts.period.eq('testing')].groupby(['parameter_set', 'policy', 'reduction_fraction']):
        summaries.append(dict(parameter_set=key[0], policy=key[1], reduction_fraction=float(key[2]),
            mean_additional_grain_t=float(g.additional_grain_t.mean()), mean_additional_et_m3=float(g.additional_et_m3.mean()),
            mean_additional_drainage_m3=float(g.additional_drainage_m3.mean()), n_loss_years=int(g.additional_grain_t.lt(-1.).sum()),
            minimum_additional_grain_t=float(g.additional_grain_t.min()), maximum_water_difference_m3=float(g.field_water_difference_m3.abs().max())))
    pd.DataFrame(summaries).to_csv(root/'tables/contrast_summary.csv', index=False)
    # Archived plotting functions are imported without invoking their mutating
    # main entrypoints; all figure writes use this new scientific subrun.
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    publication = archived_plot_module(root, 'publication_figures')
    publication.policies()
    sensitivity = archived_plot_module(root, 'analyze_results')
    sensitivity.gains()
    spatial = archived_plot_module(root, 'spatial_analysis')
    from matplotlib.figure import Figure
    unstyled_save = Figure.savefig
    def save_spatial(fig, path, *args, **kwargs):
        close_figure_axes(fig)
        return unstyled_save(fig, path, *args, **kwargs)
    Figure.savefig = save_spatial
    try:
        spatial.maps(pd.read_csv(root/'tables/source_cell_map_values.csv'))
    finally:
        Figure.savefig = unstyled_save
    adaptive = archived_plot_module(root, 'export_results')
    # Match the historical plotting input layout without duplicating simulation
    # output data; these relative links remain internal to the new subrun.
    legacy_daily = root/'adaptive/predictions/per_representative'
    legacy_daily.mkdir(parents=True, exist_ok=True)
    for rid in [0, 31]:
        link = legacy_daily/f'rep_{rid:03d}.daily.csv.gz'
        if not link.exists():
            link.symlink_to(Path('../../per_representative')/link.name)
    (root/'adaptive/tables').mkdir(exist_ok=True);(root/'adaptive/figures').mkdir(exist_ok=True)
    annual = pd.read_csv(root/'tables/adaptive_regional_policy_annual.csv')
    annual[annual.policy.isin(['adaptive_95', 'adaptive_98', 'conventional'])].to_csv(root/'adaptive/tables/regional_policy_annual.csv', index=False)
    # Historical fixed axes would hide new low-retention or larger-reduction
    # outcomes. Expand them from the actual new data before every export.
    original_save = Figure.savefig
    def save_adaptive(fig, path, *args, **kwargs):
        close_figure_axes(fig)
        if len(fig.axes) == 4:
            plotted = annual[annual.period.eq('testing')&annual.policy.isin(['adaptive_95', 'adaptive_98'])]
            lower = min(85., float(plotted.grain_retention_pct.min())-2.)
            upper = max(104., float(plotted.grain_retention_pct.max())+2.)
            for ax in [fig.axes[1], fig.axes[3]]:
                ax.set_ylim(lower, upper)
            low_et, high_et = float(plotted.et_reduction_mm.min()), float(plotted.et_reduction_mm.max())
            padding = max(2., (high_et-low_et)*.08)
            fig.axes[2].set_ylim(min(-5., low_et-padding), max(1., high_et+padding))
            fig.axes[3].set_xlim(min(-10., float(plotted.irrigation_reduction_mm.min())-10.),
                max(300., float(plotted.irrigation_reduction_mm.max())+10.))
        return original_save(fig, path, *args, **kwargs)
    Figure.savefig = save_adaptive
    try:
        adaptive.figures()
    finally:
        Figure.savefig = original_save
    plt.rcParams['font.family'] = 'DejaVu Sans'
    for p in (root/'adaptive/figures').glob('*'):
        if p.suffix in ['.png', '.pdf']:
            shutil.copy2(p, root/'figures'/p.name)
    spatial_metrics = pd.read_csv(root/'tables/full_quota_spatial_metrics.csv')
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.), layout='constrained')
    for crop, color, marker in [('wheat', '#2166ac', 'o'), ('maize', '#d6604d', 's')]:
        for ax, column, scale in [(axes[0], 'yield_kg_ha', 1000), (axes[1], 'et_mm', 1)]:
            values = spatial_metrics[(spatial_metrics.crop == crop)&spatial_metrics.variable.eq(column)]
            ax.plot(values.fraction*100, values.rmse/scale, color=color, marker=marker, label=crop.capitalize())
            ax.set(xlabel='Conventional irrigation retained (%)', xticks=[0, 25, 50, 75, 100], ylim=(0, None))
            ax.tick_params(direction='in')
    axes[0].set_ylabel('Grain aggregation RMSE (t ha⁻¹)');axes[1].set_ylabel('ET aggregation RMSE (mm)')
    axes[0].legend(frameon=False)
    close_figure_axes(fig)
    for extension in ['png', 'pdf']:
        fig.savefig(root/f'figures/all_quota_spatial_errors.{extension}', dpi=450)
    plt.close(fig)
    comparison = pd.read_csv(root/'tables/policy_comparison.csv')
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.2), layout='constrained')
    common = comparison[(comparison.scope == 'common_GRACE_available')&comparison.policy.ne('conventional')]
    for r in common.itertuples():
        color = '#2166ac' if r.policy.startswith('adaptive') else '#d6604d' if r.policy.endswith('matched') else '#238b45'
        for ax, x in zip(axes, [r.irrigation_reduction_mm, r.et_reduction_mm]):
            ax.scatter(x, r.grain_retention_pct, color=color, marker='o' if '95' in r.policy else 's', label=r.policy)
    for ax, title in zip(axes, ['Field irrigation reduction (mm)', 'Crop-plus-fallow ET reduction (mm)']):
        ax.set(xlabel=title, ylabel='Conventional grain retained (%)');ax.tick_params(direction='in')
    axes[1].legend(fontsize=6, frameon=False)
    close_figure_axes(fig)
    for extension in ['png', 'pdf']:
        fig.savefig(root/f'figures/rainfall_only_controls.{extension}', dpi=450)
    plt.close(fig)
    tables = ['regional_policy_annual_results', 'policy_period_means', 'contrast_summary', 'policy_contrasts',
        'allocation_changes', 'allocation_turnover', 'spatial_unit_period_means', 'spatial_latitude_bands',
        'policy_comparison', 'adaptive_regional_policy_annual', 'training_selected_quotas', 'training_selection_candidates',
        'full_quota_spatial_metrics', 'management_response_metrics', 'class_quota_responses',
        'class_quota_yearly_responses', 'selected_quota_testing_evaluation', 'class_counts_and_area_exposure']
    with pd.ExcelWriter(root/'tables/revised_regional_results.xlsx', engine='openpyxl') as writer:
        for name in tables:
            pd.read_csv(root/'tables'/f'{name}.csv').to_excel(writer, sheet_name=name[:31], index=False)
        for name in ['representative_cells', 'class_memberships', 'annual_policy_decisions']:
            pd.read_csv(root/'data'/f'{name}.csv').to_excel(writer, sheet_name=name[:31], index=False)
        pd.read_csv(root/'parameters/frozen_training_allocation.csv').to_excel(writer, sheet_name='Frozen_spatial_allocation', index=False)
    seasons = pd.read_csv(root/'predictions/all_season_summaries.csv')
    write_json(root/'verification/completion.json', dict(all_checks_passed=True,
        revised_model_name='Open Crop Model', model_variant='documented_management_water_calibration',
        selected_version='management_refit', parameters_promoted=True, independent_regional_validation=False,
        regional_results_classification='conditional model scenarios', causal_groundwater_effects_identified=False,
        source_exclusions_preserved=True,
        n_fixed_segments=len(seasons), n_fixed_rotation_responses=32*5*29, representatives=32,
        maximum_water_residual_mm=float(seasons.maximum_daily_residual_mm.max()),
        maximum_carbon_residual_kg_ha=float(seasons.maximum_carbon_residual_kg_ha.max()),
        maximum_storage_discontinuity_mm=float(seasons.storage_continuity_error_mm.abs().max()),
        maximum_equal_water_difference_m3=float(contrasts.field_water_difference_m3.abs().max()),
        native_process_changed=True, archived_completed_runs_modified=False,
        source_fingerprint=verify_run(root)['source_fingerprint']))
    manifest = {str(p.relative_to(root)): dict(sha256=sha(p), bytes=p.stat().st_size)
        for p in sorted(root.rglob('*')) if p.is_file() and '__pycache__' not in p.parts
        and p.name != 'file_manifest.json' and p.suffix != '.log'}
    write_json(root/'verification/file_manifest.json', manifest)
    print('Revised regional scientific tables, spatial maps and PNG/PDF figures complete', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=['prepare', 'responses', 'allocations', 'adaptive', 'analyze'], required=True)
    parser.add_argument('--run-root', type=Path, default=PRODUCT/'regional')
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args();root = args.run_root.resolve()
    if args.workers < 1:
        parser.error('--workers must be positive')
    if (root/'verification/completion.json').exists():
        verify_run(root)
        for name, item in json.loads((root/'verification/file_manifest.json').read_text()).items():
            if sha(root/name) != item['sha256']:
                raise ValueError('Completed regional artifact changed: '+name)
        print('Completed regional subrun is immutable; all existing artifacts verified', flush=True);return
    if args.stage == 'prepare':
        prepare(root)
    elif args.stage == 'responses':
        run_workers(root, 'responses', args.workers)
        run_workers(root, 'spatial', args.workers)
    elif args.stage == 'allocations':
        allocations(root)
    elif args.stage == 'adaptive':
        verify_selections(root)
        run_workers(root, 'adaptive', args.workers);aggregate_adaptive(root)
    elif args.stage == 'analyze':
        analyze(root)


if __name__ == '__main__':
    main()
