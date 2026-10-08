"""Download versioned GRACE source files with checksums and request metadata."""
from pathlib import Path
import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[1]


def download(url, folder, filename=None, expected_md5=None, expected_bytes=None):
    dest = ROOT/'raw'/folder/(filename or url.rsplit('/', 1)[-1])
    dest.parent.mkdir(parents=True, exist_ok=True)
    metadata = ROOT/'metadata/downloads'/f'{folder}__{dest.name}.json'
    metadata.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and metadata.exists():
        previous = json.loads(metadata.read_text())
        digest = hashlib.file_digest(dest.open('rb'), 'sha256').hexdigest()
        if digest == previous['sha256']:
            print('Verified existing:', dest.name, flush=True)
            return previous
    session = requests.Session()
    session.mount('https://', HTTPAdapter(max_retries=Retry(total=4, backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504])))
    part = dest.with_suffix(dest.suffix+'.part')
    for attempt in range(3):
        try:
            offset=part.stat().st_size if part.exists() else 0
            headers={'Range':f'bytes={offset}-'} if offset else {}
            with session.get(url, headers=headers, stream=True, timeout=(30, 90)) as response:
                response.raise_for_status()
                resumed=offset>0 and response.status_code==206
                if resumed:
                    assert response.headers['Content-Range'].startswith(f'bytes {offset}-')
                size = offset if resumed else 0
                sha = hashlib.sha256()
                md5 = hashlib.md5()
                if resumed:
                    with part.open('rb') as stream:
                        for block in iter(lambda:stream.read(1024*1024),b''):
                            sha.update(block);md5.update(block)
                last = time.monotonic()
                print('Downloading:', dest.name, 'resume bytes:',size,flush=True)
                with part.open('ab' if resumed else 'wb') as stream:
                    for block in response.iter_content(1024*1024):
                        if not block:
                            continue
                        stream.write(block); sha.update(block); md5.update(block); size += len(block)
                        if time.monotonic()-last > 20:
                            print(dest.name, round(size/1e6, 1), 'MB', flush=True)
                            last = time.monotonic()
                if expected_bytes is not None:
                    assert size == expected_bytes, (dest.name, size, expected_bytes)
                http_size=response.headers.get('Content-Range','').rsplit('/',1)[-1] if resumed else response.headers.get('Content-Length')
                if http_size and http_size.isdigit() and not response.headers.get('Content-Encoding'):
                    assert size==int(http_size),(dest.name,'HTTP length mismatch',size,http_size)
                if expected_md5:
                    assert md5.hexdigest() == expected_md5, dest.name+' checksum mismatch'
                # An HTTP success must not be a login/error page disguised as data.
                with part.open('rb') as stream:
                    signature = stream.read(8)
                if dest.suffix in ('.nc', '.h5'):
                    assert signature.startswith((b'CDF', b'\x89HDF')), (dest.name, signature)
                entry = {'url':url, 'final_url':response.url,
                    'retrieved_utc':datetime.now(timezone.utc).isoformat(),
                    'relative_path':str(dest.relative_to(ROOT)), 'bytes':size,
                    'sha256':sha.hexdigest(), 'md5':md5.hexdigest(),
                    'source_md5':expected_md5, 'source_md5_verified':bool(expected_md5),
                    'last_modified':response.headers.get('Last-Modified'),
                    'etag':response.headers.get('ETag'), 'content_type':response.headers.get('Content-Type')}
                part.replace(dest)
                metadata.write_text(json.dumps(entry, indent=2))
                print('Saved:', dest.name, size, 'bytes', flush=True)
                return entry
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2*(attempt+1))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('product', choices=['csr','gsfc','graice'])
    args=parser.parse_args()
    if args.product=='csr':
        base='https://download.csr.utexas.edu/outgoing/grace/RL0603_mascons/'
        for name in ['CSR_GRACE_GRACE-FO_RL0603_Mascons_all-corrections.nc',
                'CSR_GRACE_GRACE-FO_RL06_Mascons_v02_LandMask.nc',
                'CSR_GRACE_GRACE-FO_RL0603_mascons_mapping_file.nc']:
            download(base+name,'csr_rl0603')
    elif args.product=='gsfc':
        base='https://earth.gsfc.nasa.gov/sites/default/files/'
        names=['geo/gsfc.glb_.200204_202603_rl06v2.0_obp-ice6gd_halfdegree.nc',
                'geo/gsfc.glb_.200204_202603_rl06v2.0_obp-ice6gd.h5',
                '2022-05/gsfc_mascons_hdf5_format_rl06v2.pdf']
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(lambda name:download(base+name,'gsfc_rl06v20'),names))
    else:
        url='https://zenodo.org/api/records/20616569'
        r=requests.get(url, timeout=60);r.raise_for_status();record=r.json()
        (ROOT/'metadata').mkdir(parents=True,exist_ok=True)
        (ROOT/'metadata/graice_source.json').write_text(json.dumps(record,indent=2))
        def fetch(f):
            return download(f['links']['self'],'graice_v2',filename=f['key'],
                expected_md5=f['checksum'].removeprefix('md5:'),expected_bytes=f['size'])
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(fetch,record['files']))


if __name__=='__main__':
    main()
