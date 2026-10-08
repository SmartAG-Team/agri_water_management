"""Use the already authenticated Chrome tab; never exports browser credentials."""
import json
import subprocess
import time

WINDOW = 104775437
TAB = 104775514

def js(source):
    script = ('tell application "Google Chrome"\n'
              f'tell tab id {TAB} of window id {WINDOW}\n'
              f'return execute javascript {json.dumps(source, ensure_ascii=False)}\n'
              'end tell\nend tell')
    return subprocess.check_output(['osascript', '-e', script], text=True).strip()

def navigate(url):
    script = ('tell application "Google Chrome"\n'
              f'set URL of tab id {TAB} of window id {WINDOW} to {json.dumps(url)}\n'
              'end tell')
    subprocess.check_call(['osascript', '-e', script])

def fetch_json(path, method='GET', data=None, timeout=60):
    options = {'method': method, 'credentials': 'same-origin'}
    if data is not None:
        from urllib.parse import urlencode
        options.update(headers={'Content-Type':'application/x-www-form-urlencoded'},body=urlencode(data))
    js('window.__researchFetch=null; fetch('+json.dumps(path)+','+json.dumps(options)+')'
       '.then(async r=>({status:r.status,data:await r.json()}))'
       '.then(x=>window.__researchFetch=x).catch(e=>window.__researchFetch={error:String(e)}); "started"')
    for _ in range(timeout):
        result=js('JSON.stringify(window.__researchFetch)')
        if result not in ('null','undefined','missing value',''):
            return json.loads(result)
        time.sleep(1)
    raise TimeoutError(path)

def download(path, destination, timeout=180):
    """Fetch an authorized file in Chrome and stream its encoded body locally."""
    import base64
    js('window.__researchBlob=null;window.__researchB64=null;fetch('+json.dumps(path)+',{credentials:"same-origin"})'
       '.then(async r=>{if(!r.ok)throw Error(r.status);let b=await r.blob();'
       'let f=new FileReader();f.onload=()=>{window.__researchB64=f.result.split(",")[1];'
       'window.__researchBlob={size:b.size,type:b.type,length:window.__researchB64.length};};f.readAsDataURL(b);})'
       '.catch(e=>window.__researchBlob={error:String(e)});"started"')
    for _ in range(timeout):
        raw=js('JSON.stringify(window.__researchBlob)')
        if raw not in ('null','undefined','missing value',''):
            info=json.loads(raw)
            if 'error' in info:raise RuntimeError(info['error'])
            if 'html' in info['type']:raise RuntimeError('Download returned HTML instead of a data file')
            with open(destination,'wb') as out:
                for i in range(0,info['length'],262144):
                    chunk=js(f'window.__researchB64.slice({i},{i+262144})')
                    out.write(base64.b64decode(chunk))
            js('window.__researchBlob=null;window.__researchB64=null;"cleared"')
            return info
        time.sleep(1)
    raise TimeoutError(path)
