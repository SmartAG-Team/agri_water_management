"""Seal the current product and build one independently checked export ZIP."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {'current_results.zip', 'verification/product_manifest.json', 'verification/package_receipt.json'}


def sha(path):
    value = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def main():
    active_entries=sorted(p.name for p in ROOT.parent.iterdir())
    if 'current_results' not in active_entries:
        raise ValueError('Current publication product is absent')
    for name in ['current_product_checks', 'history_cleanup_receipt', 'current_calibration_checks']:
        record = json.loads((ROOT / 'verification' / (name + '.json')).read_text())
        if not record['all_checks_passed']:
            raise ValueError('Required final verification failed: ' + name)
    files = {}
    for path in sorted(ROOT.rglob('*')):
        name = path.relative_to(ROOT).as_posix()
        if path.is_file() and name not in EXCLUDED:
            files[name] = {'bytes': path.stat().st_size, 'sha256': sha(path)}
    manifest = {'sealed_utc': datetime.now(timezone.utc).isoformat(), 'selected_model': 'management_refit',
                'file_count': len(files), 'files': files, 'excluded_self_and_export_files': sorted(EXCLUDED),
                'current_regional_recalculation': True, 'scientific_validation_status': 'conditional research results; field criteria not all met'}
    manifest_path = ROOT / 'verification/product_manifest.json'
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
    package = ROOT / 'current_results.zip'
    temporary = ROOT / '.current_results_building.zip'
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as bundle:
            for name in files:
                bundle.write(ROOT / name, 'current_results/' + name)
            bundle.write(manifest_path, 'current_results/verification/product_manifest.json')
        with zipfile.ZipFile(temporary) as bundle:
            expected = {'current_results/' + n for n in files} | {'current_results/verification/product_manifest.json'}
            if set(bundle.namelist()) != expected:
                raise ValueError('Export member inventory differs')
            for name, record in files.items():
                h = hashlib.sha256()
                with bundle.open('current_results/' + name) as f:
                    for part in iter(lambda: f.read(4 * 1024 * 1024), b''):
                        h.update(part)
                if h.hexdigest() != record['sha256']:
                    raise ValueError('Export content differs: ' + name)
            if bundle.read('current_results/verification/product_manifest.json') != manifest_path.read_bytes():
                raise ValueError('Export manifest differs')
        temporary.replace(package)
    finally:
        if temporary.exists():
            temporary.unlink()
    receipt = {'completed_utc': datetime.now(timezone.utc).isoformat(), 'all_checks_passed': True,
               'artifact_files_verified': len(files), 'zip_members_verified': len(files) + 1,
               'product_manifest_sha256': sha(manifest_path), 'zip_sha256': sha(package),
               'zip_bytes': package.stat().st_size, 'single_current_results_export': True,
               'other_model_directories_preserved': True,
               'active_model_entries': sorted(p.name for p in ROOT.parent.iterdir())}
    (ROOT / 'verification/package_receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
