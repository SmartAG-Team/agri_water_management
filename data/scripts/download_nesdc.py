"""Download public-sharing records after submitting the site's purpose form.

Run from the workspace root. Uses only an existing authorized Chrome session.
Original files and public metadata are preserved without credential storage.
"""
import json
import sys
from pathlib import Path
from browser_bridge import fetch_json, download

for resource in sys.argv[1:]:
    root=Path('data/raw/nesdc')
    target=next(root.glob('*/'+resource),root/resource)
    target.mkdir(parents=True,exist_ok=True)
    try:
        listing=fetch_json('/sdo/getFileByCERN','POST',{'sdoId':resource})
        (target/'files_metadata.json').write_text(json.dumps(listing,ensure_ascii=False,indent=2))
        for f in listing['data']['data']:
            name=Path(f['fileName']).name
            dest=target/name
            if dest.exists():
                print(resource,'already exists',name,flush=True)
                continue
            check=fetch_json('/sdo/checkDownloadFiles','POST',{'listId':f['id']})
            if check['data'] is not True:
                raise RuntimeError(str(check['data']))
            info=download('/sdo/downloadOneFile?source=total&fileId='+f['id'],dest)
            print(resource,name,info['size'],'bytes',flush=True)
    except Exception as e:
        (target/'download_issue.txt').write_text(str(e))
        print(resource,'FAILED',str(e),flush=True)
