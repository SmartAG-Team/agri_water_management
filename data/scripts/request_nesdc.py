"""Submit the normal NESDC public-data purpose form for the requested IDs."""
import json
import sys
import time
from browser_bridge import js, navigate

ids=sys.argv[1:]
if len(ids)>10:raise ValueError('NESDC permits at most ten datasets per order')
navigate('https://www.nesdc.org.cn/order/add?dataSetId='+','.join(ids))
for _ in range(90):
    if js('Boolean(document.getElementById("orderPurpose_dataPurpose"))')=='true':break
    time.sleep(1)
else:raise RuntimeError('Purpose form did not load')
fields={
 'orderPurpose_dataPurpose':'用于华北平原冬小麦与夏玉米作物模型验证及灌溉管理研究，分析作物生长、产量、土壤水分和蒸散过程。',
 'orderPurpose_projectName':'华北平原小麦玉米作物模型验证与灌溉管理研究',
 'orderPurpose_projectMaster':'赵刚',
 'orderPurpose_projectType':'其他项目'}
js('Object.entries('+json.dumps(fields,ensure_ascii=False)+').forEach(([k,v])=>{'
   'let e=document.getElementById(k);e.value=v;e.dispatchEvent(new Event("change",{bubbles:true}));});'
   'Array.from(document.querySelectorAll("a")).find(e=>e.getAttribute("onclick")==="finishOrder();").click();"submitted"')
for _ in range(90):
    if js('location.pathname')=='/order/orderSuccess':
        print('Public-data purpose form accepted:',len(ids),'datasets',flush=True)
        break
    time.sleep(1)
else:raise RuntimeError('Could not verify successful submission; inspect browser before retrying')
