"""Seal a fully checked diagnostic run without treating accuracy failure as success."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]


def main():
    manifest = ROOT / 'verification/artifact_manifest.json'
    if manifest.exists():
        raise RuntimeError('Completed run preserved; verify existing sealed artifacts.')
    assert (ROOT / 'verification/independent_checks.json').exists()
    review = json.loads((ROOT / 'verification/figure_review.json').read_text())
    for row in review['figures']:
        assert row['readable'] and row['no_clipping'] and row['legends_clear']
        assert hashlib.sha256((ROOT / row['path']).read_bytes()).hexdigest() == row['sha256']
    excluded = {'artifact_manifest.json', 'seal.json', '.DS_Store'}
    rows = []
    for path in sorted(ROOT.rglob('*')):
        if path.is_file() and path.name not in excluded and '__pycache__' not in path.parts:
            rows.append(dict(path=path.relative_to(ROOT).as_posix(),
                             sha256=hashlib.sha256(path.read_bytes()).hexdigest(), bytes=path.stat().st_size))
    manifest.write_text(json.dumps(rows, indent=2, ensure_ascii=False)+'\n')
    seal = dict(sealed_at_utc=datetime.now(timezone.utc).isoformat(), artifacts=len(rows),
                artifact_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
                diagnostic_run_sealed=True, accuracy_objective_not_inferred_from_sealing=True)
    (ROOT / 'verification/seal.json').write_text(json.dumps(seal, indent=2)+'\n')
    for row in rows:
        assert hashlib.sha256((ROOT / row['path']).read_bytes()).hexdigest() == row['sha256']
    print('Sealed and verified', len(rows), 'diagnostic artifacts.')


if __name__ == '__main__':
    main()
