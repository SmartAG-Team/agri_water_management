"""Download selected public Figshare releases; verify original size/MD5.

Run from workspace root using .venv/bin/python. No credentials required.
"""
from pathlib import Path
import hashlib
import json
import time
import requests

ROOT = Path(__file__).resolve().parents[1]
SELECTION = {
    14936052: ('chinacp_500m_2015_2021', None),
    28646687: ('chinacp_wheat10m_2020', {'wheat_maize_china_2020.zip',
        'README.md', 'CARI_index.txt', 'SingleWheat_index.txt',
        'Rice_Upland_index.txt', 'REIP_index.txt', 'NMDI_index.txt',
        'Couple_VI_VV.txt'}),
}

def digest(path, alg):
    h = hashlib.new(alg)
    with path.open('rb') as f:
        for b in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()

def main():
    manifest = []
    for rid, (name, selected) in SELECTION.items():
        folder = ROOT / 'raw' / name
        folder.mkdir(parents=True, exist_ok=True)
        r = requests.get(f'https://api.figshare.com/v2/articles/{rid}', timeout=60)
        r.raise_for_status()
        meta = r.json()
        (folder / 'repository_metadata.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2))
        for item in meta['files']:
            if selected is not None and item['name'] not in selected:
                continue
            path = folder / item['name']
            expected = item['computed_md5']
            if not (path.exists() and path.stat().st_size == item['size'] and digest(path, 'md5') == expected):
                part = path.with_suffix(path.suffix + '.part')
                for attempt in range(4):
                    try:
                        print(f"Downloading {name}/{path.name}: {item['size']/1e6:.1f} MB", flush=True)
                        with requests.get(item['download_url'], stream=True, timeout=(30, 90)) as resp:
                            resp.raise_for_status()
                            with part.open('wb') as out:
                                for chunk in resp.iter_content(1024 * 1024):
                                    out.write(chunk)
                        if part.stat().st_size != item['size'] or digest(part, 'md5') != expected:
                            raise ValueError('Download size or MD5 mismatch')
                        part.replace(path)
                        break
                    except Exception:
                        if attempt == 3:
                            raise
                        time.sleep(3)
            manifest.append({'dataset': name, 'path': str(path.relative_to(ROOT)),
                'bytes': path.stat().st_size, 'md5': digest(path, 'md5'),
                'sha256': digest(path, 'sha256'), 'md5_verified': True,
                'source_url': item['download_url'], 'doi': meta['doi'],
                'license': meta['license'], 'accessed': '2026-10-02'})
            print(f'Verified {path.name}', flush=True)
            (ROOT / 'metadata' / 'figshare_downloads.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
