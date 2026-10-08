"""Relocate historical runs and remove exactly preserved draft bundles once."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import os
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
CURRENT = ROOT / 'model/current_results'
ARCHIVE = ROOT / 'archives/2026-10-07_model_history'
PREPARATION = ROOT / 'archives/.2026-10-07_history_preparation'


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n')


def check_barrier():
    # Read all open-file records once; avoid a recursive scan of every old run.
    result = subprocess.run(['lsof', '-n', '-P', '-Fpn'], capture_output=True, text=True, timeout=30)
    pid = None
    users = []
    for line in result.stdout.splitlines():
        if line.startswith('p'):
            pid = int(line[1:])
        elif line.startswith('n') and line[1:].startswith(str(ROOT / 'model') + '/') and pid != os.getpid():
            users.append({'pid': pid, 'path': line[1:]})
    if users:
        raise RuntimeError('Historical/current model files still open: ' + json.dumps(users[:20]))
    return {'checked_utc': datetime.now(timezone.utc).isoformat(), 'other_open_model_files': []}


def verify_preservation(check_originals):
    command = [sys.executable, '-B', str(ARCHIVE / 'verify_bundle_preservation.py'),
               '--model-root', str(ARCHIVE / 'model'), '--preservation-root', str(ARCHIVE / 'unique_bundle_members')]
    if check_originals:
        command.append('--verify-original-bundles')
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def verify_historical_integrity(plan):
    checked = 0
    for record in plan['provenance_manifest_integrity']:
        target = Path(record['destination'])
        if sha(target) != record['sha256']:
            raise ValueError('Historical provenance changed: ' + str(target))
        checked += 1
    scientific = plan['symlinks']['scientific_links']
    for link in scientific:
        path = ARCHIVE / link['path']
        if not path.is_symlink() or os.readlink(path) != link['target'] or not path.exists():
            raise ValueError('Scientific archive link changed: ' + str(path))
    return {'unchanged_historical_provenance_manifests': checked, 'preserved_scientific_links': len(scientific)}


def main():
    plan = json.loads((CURRENT / 'verification/history_cleanup_plan.json').read_text())
    stage = ROOT / '.current_results_consolidation_stage'
    if (ARCHIVE / 'model').exists() or stage.exists():
        raise ValueError('History relocation already started/completed; inspect journal before retrying.')
    current_check = json.loads((CURRENT / 'verification/current_product_checks.json').read_text())
    if not current_check['all_checks_passed']:
        raise ValueError('Current product verification incomplete')
    barrier = check_barrier()
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    journal = {'started_utc': datetime.now(timezone.utc).isoformat(), 'stage': str(stage),
               'active': str(ROOT / 'model'), 'history': str(ARCHIVE / 'model'), 'process_barrier': barrier,
               'status': 'prepared', 'scientific_run_deletion': False}
    write(ARCHIVE / 'relocation_journal.json', journal)
    stage.mkdir()
    try:
        CURRENT.rename(stage / 'current_results')
        (stage / 'README.md').write_text('The active research product is [current_results](current_results/README.md).\n\n'
            'Earlier runs and drafts are preserved in [the history archive](../archives/2026-10-07_model_history/README.md).\n')
        (ROOT / 'model').rename(ARCHIVE / 'model')
        try:
            stage.rename(ROOT / 'model')
        except BaseException:
            (ARCHIVE / 'model').rename(ROOT / 'model')
            (stage / 'current_results').rename(CURRENT)
            raise
    except BaseException:
        if (stage / 'current_results').exists() and (ROOT / 'model').exists() and not CURRENT.exists():
            (stage / 'current_results').rename(CURRENT)
        journal['status'] = 'relocation_failed_or_rolled_back'
        write(ARCHIVE / 'relocation_journal.json', journal)
        raise
    journal['status'] = 'history_relocated_active_product_restored'
    write(ARCHIVE / 'relocation_journal.json', journal)
    (PREPARATION / 'unique_bundle_members').rename(ARCHIVE / 'unique_bundle_members')
    shutil.copy2(PREPARATION / 'verify_bundle_preservation.py', ARCHIVE / 'verify_bundle_preservation.py')
    shutil.copy2(PREPARATION / 'preservation_verification.json', ARCHIVE / 'pre_move_preservation_verification.json')
    shutil.copy2(CURRENT / 'verification/history_cleanup_plan.json', ARCHIVE / 'history_cleanup_plan.json')
    # Snapshot paths and original absolute source fields remain unchanged.
    write(ARCHIVE / 'path_relocation.json', {'old_prefix': str(ROOT / 'model'),
          'new_historical_prefix': str(ARCHIVE / 'model'), 'active_prefix': str(CURRENT),
          'source_manifest_bytes_rewritten': False, 'mapping': plan['top_level_path_mapping']})
    integrity = verify_historical_integrity(plan)
    before = verify_preservation(True)
    write(ARCHIVE / 'before_bundle_removal_verification.json', before)
    deleted = []
    for record in plan['redundant_draft_bundles']['bundles']:
        path = Path(record['archive_path'])
        if not path.is_relative_to(ARCHIVE / 'model') or not path.name.endswith('.zip') or sha(path) != record['sha256']:
            raise ValueError('Exact redundant bundle identity failed: ' + str(path))
        path.unlink()
        deleted.append({'path': str(path.relative_to(ARCHIVE)), 'bytes': record['logical_bytes'], 'sha256': record['sha256']})
        journal['removed_bundles'] = deleted
        write(ARCHIVE / 'relocation_journal.json', journal)
    after = verify_preservation(False)
    write(ARCHIVE / 'after_bundle_removal_verification.json', after)
    integrity.update(verify_historical_integrity(plan))
    receipt = {'completed_utc': datetime.now(timezone.utc).isoformat(), 'all_checks_passed': True,
               'active_model_entries': sorted(p.name for p in (ROOT / 'model').iterdir()),
               'historical_directories_archived': sum(p.is_dir() for p in (ARCHIVE / 'model').iterdir()),
               'scientific_runs_deleted': 0, 'source_snapshots_or_manifests_rewritten': 0,
               'redundant_bundles_removed': deleted, 'gross_logical_bytes_removed': sum(v['bytes'] for v in deleted),
               'preserved_unique_blob_bytes': plan['disk_effects']['unique_blob_logical_bytes'],
               'net_logical_bytes_removed': sum(v['bytes'] for v in deleted) - plan['disk_effects']['unique_blob_logical_bytes'],
               'exact_bundle_member_records_preserved': after['total_member_records'],
               'archive': str(ARCHIVE), 'original_source_paths_retained_as_provenance': True, **integrity}
    write(ARCHIVE / 'cleanup_receipt.json', receipt)
    write(CURRENT / 'verification/history_cleanup_receipt.json', receipt)
    (ARCHIVE / 'README.md').write_text('# Historical research archive\n\n'
        'All former model runs, source snapshots, observations, weather, predictions, metrics and individual manuscript drafts are retained under `model/`. '
        'The active product is [model/current_results](../../model/current_results/README.md).\n\n'
        'Eleven redundant draft ZIP bundles were removed after all 64,061 member records were verified against retained files or exact content-addressed blobs. '
        'Unique contents and per-bundle member manifests remain in `unique_bundle_members/`. Original input/source provenance is unchanged. '
        'The path relocation map resolves historical absolute paths without altering their recorded bytes.\n\n'
        'Verify bundle-member preservation with:\n\n'
        '```sh\n../../.venv/bin/python -B verify_bundle_preservation.py --model-root model --preservation-root unique_bundle_members\n```\n')
    journal['status'] = 'complete_verified'
    journal['completed_utc'] = receipt['completed_utc']
    write(ARCHIVE / 'relocation_journal.json', journal)
    for p in PREPARATION.iterdir():
        if p.is_file():
            p.unlink()
    if not any(PREPARATION.iterdir()):
        PREPARATION.rmdir()
    print(json.dumps({k: receipt[k] for k in ['active_model_entries', 'historical_directories_archived',
          'scientific_runs_deleted', 'net_logical_bytes_removed', 'exact_bundle_member_records_preserved']}, indent=2))


if __name__ == '__main__':
    main()
