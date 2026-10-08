"""Retrieve public CCD province rasters for 2020–2024 through published URLs.

File list comes from the same public file-tree and Download-all-URLs controls
used by ScienceDB's dataset page. Downloads require no login credentials.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import json
import time
import requests
from download_figshare import digest

ROOT = Path(__file__).resolve().parents[1]
YEARS = set(range(2020, 2025))

def fetch(item):
    province = item['path'].split('/')[2]
    folder = ROOT / 'raw' / 'ccd_30m_2020_2024' / province
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / item['fileName']
    good = lambda p: p.exists() and p.stat().st_size == item['size'] and digest(p, 'md5') == item['md5']
    if not good(dest):
        part = dest.with_suffix('.tif.part')
        for attempt in range(4):
            try:
                print(f"Download {dest.name}: {item['size']/1e6:.1f} MB", flush=True)
                with requests.get(item['download_url'], stream=True, timeout=(30, 120)) as r:
                    r.raise_for_status()
                    with part.open('wb') as f:
                        for b in r.iter_content(1024 * 1024):
                            f.write(b)
                if not good(part):
                    raise ValueError(f'Size/MD5 mismatch: {dest.name}')
                part.replace(dest)
                break
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(5)
    print(f'Verified {dest.name}', flush=True)
    return {'dataset': 'ccd_30m_2020_2024', 'path': str(dest.relative_to(ROOT)),
        'province': province, 'year': int(dest.name.split('-')[-2]),
        'bytes': dest.stat().st_size, 'md5': digest(dest, 'md5'),
        'sha256': digest(dest, 'sha256'), 'md5_verified': True,
        'source_url': item['download_url'], 'doi': '10.57760/sciencedb.32361',
        'license': 'CC BY 4.0', 'accessed': '2026-10-02'}

def main():
    items = json.loads((ROOT / 'metadata' / 'ccd_selected_province_files.json').read_text())
    urls = (ROOT / 'metadata' / 'ccd_public_urls.txt').read_text().splitlines()
    lookup = {parse_qs(urlparse(u).query)['fileId'][0]: u for u in urls}
    selected = [i for i in items if int(i['fileName'].split('-')[-2]) in YEARS]
    selected.sort(key=lambda i: (-int(i['fileName'].split('-')[-2]), i['fileName']))
    for i in selected:
        i['download_url'] = lookup[i['id']]
    manifest = []
    with ThreadPoolExecutor(2) as pool:
        for result in pool.map(fetch, selected):
            manifest.append(result)
            (ROOT / 'metadata' / 'ccd_downloads.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
