from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from types import MethodType

from . import db
from .pro_db import list_nodes, update_node_health
from .security import encrypt_secret
from .utils import now_iso


def _request(node,path,method='GET',payload=None,timeout=12):
    base=str(node.get('base_url') or '').rstrip('/')
    if not base: raise RuntimeError('Nó sem URL base.')
    data=json.dumps(payload).encode() if payload is not None else None
    req=urllib.request.Request(base+path,data=data,method=method,headers={'Authorization':'Bearer '+str(node.get('token') or ''),'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def upsert_snapshot(ch):
    cid=str(ch.get('id') or '')
    if not cid: raise ValueError('Snapshot sem ID do canal.')
    reserved={'id','name','desired_running','created_at','updated_at','destinations','schedules','runtime','running','active_platforms'}
    settings={k:v for k,v in ch.items() if k not in reserved}
    ts=now_iso()
    with db.connect() as con:
        old=con.execute('SELECT created_at FROM channels WHERE id=?',(cid,)).fetchone(); created=old['created_at'] if old else ts
        con.execute('INSERT INTO channels(id,name,settings_json,desired_running,created_at,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,settings_json=excluded.settings_json,updated_at=excluded.updated_at',
                    (cid,ch.get('name') or cid,json.dumps(settings,ensure_ascii=False),0,created,ts))
        for slug,d in (ch.get('destinations') or {}).items():
            extras={k:v for k,v in d.items() if k not in {'label','enabled','rtmp_url','stream_key','mode','dedicated','masked_key'}}
            con.execute('INSERT INTO destinations(channel_id,slug,label,enabled,rtmp_url,stream_key,mode,dedicated,settings_json) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(channel_id,slug) DO UPDATE SET label=excluded.label,enabled=excluded.enabled,rtmp_url=excluded.rtmp_url,stream_key=excluded.stream_key,mode=excluded.mode,dedicated=excluded.dedicated,settings_json=excluded.settings_json',
                        (cid,slug,d.get('label',slug),int(bool(d.get('enabled'))),d.get('rtmp_url',''),encrypt_secret(str(d.get('stream_key') or '')),d.get('mode','horizontal'),int(bool(d.get('dedicated',True))),json.dumps(extras,ensure_ascii=False)))
    return cid


class NodeMonitor:
    def __init__(self): self.started=False
    def start(self):
        if self.started:return
        self.started=True; threading.Thread(target=self._loop,daemon=True,name='node-monitor').start()
    def _loop(self):
        time.sleep(5)
        while True:
            for node in list_nodes():
                if not node.get('enabled') or not node.get('base_url'): continue
                try:
                    d=_request(node,'/api/v1/status',timeout=6); sys=d.get('system') or {}; channels=d.get('channels') or {}
                    active=sum(1 for c in channels.values() if c.get('running'))
                    gpu=0.0
                    details=(sys.get('gpu') or {}).get('details') or []
                    if details:
                        try: gpu=float(str(details[0]).split(',')[1].strip())
                        except Exception: pass
                    update_node_health(node['id'],float(sys.get('cpu',0)),float(sys.get('ram',0)),gpu,active,'online')
                except Exception:
                    update_node_health(node['id'],100,100,100,node.get('active_streams',0),'offline')
            time.sleep(15)

NODE_MONITOR=NodeMonitor()


def _score(n): return n.get('priority',100)+n.get('cpu',0)*.35+n.get('ram',0)*.25+n.get('gpu',0)*.15+n.get('active_streams',0)*8

def _select_node(ch,exclude=None):
    mode=str(ch.get('node_mode') or 'local'); exclude=set(exclude or [])
    nodes=[n for n in list_nodes() if n.get('enabled') and n.get('status')=='online' and n['id'] not in exclude]
    if mode=='specific':
        return next((n for n in nodes if n['id']==ch.get('node_id')),None)
    if mode=='auto':
        tags={x.strip() for x in str(ch.get('required_node_tags') or '').split(',') if x.strip()}
        candidates=[n for n in nodes if tags.issubset(set(n.get('tags') or []))]
        return min(candidates,key=_score) if candidates else None
    return None


def install_distributed(manager):
    local_start=manager.start; local_stop=manager.stop; local_status=manager.channel_status
    remote_runs={}; lock=threading.RLock()

    def dispatch(node,cid,ch,platforms,media,trigger,schedule):
        payload={'channel':ch,'platforms':platforms,'media':media,'trigger':trigger,'schedule':schedule}
        d=_request(node,'/api/v1/agent/run','POST',payload,20)
        if not d.get('ok'): raise RuntimeError(d.get('message') or d.get('error') or 'Falha no agente.')
        with lock: remote_runs[cid]={'node':node,'payload':payload,'started_at':now_iso(),'failures':0,'desired':True}
        return True,f"Live delegada para {node['name']}: {d.get('message','OK')}"

    def start(self,cid,platforms=None,media=None,trigger='manual',schedule=None):
        ch=db.get_channel(cid)
        if not ch:return False,'Canal não encontrado.'
        mode=str(ch.get('node_mode') or 'local')
        if mode=='local': return local_start(cid,platforms,media,trigger,schedule)
        node=_select_node(ch)
        if not node:
            if mode=='auto': return local_start(cid,platforms,media,trigger,schedule)
            return False,'Servidor específico indisponível.'
        try:return dispatch(node,cid,ch,platforms,media,trigger,schedule)
        except Exception as e:
            if mode=='auto':
                try:return local_start(cid,platforms,media,trigger,schedule)
                except Exception:pass
            return False,'Falha enviando ao agente: '+str(e)

    def stop(self,cid,reason='manual'):
        with lock:r=remote_runs.get(cid)
        if not r:return local_stop(cid,reason)
        r['desired']=False
        try:d=_request(r['node'],f'/api/v1/lives/{cid}/stop','POST',{'reason':reason},10); ok=bool(d.get('ok')); msg=d.get('message','')
        except Exception as e:ok=False;msg=str(e)
        with lock:remote_runs.pop(cid,None)
        return ok,msg or ('Live remota parada.' if ok else 'Falha parando live remota.')

    def status(self,cid):
        with lock:r=remote_runs.get(cid)
        if not r:return local_status(cid)
        try:
            d=_request(r['node'],'/api/v1/status',timeout=5); st=(d.get('channels') or {}).get(cid) or {'running':False,'platforms':{}}
            st['node_id']=r['node']['id'];st['node_name']=r['node']['name'];st['remote']=True
            return st
        except Exception:
            return {'running':False,'platforms':{},'remote':True,'node_id':r['node']['id'],'node_name':r['node']['name'],'error':'agente indisponível'}

    def supervisor():
        time.sleep(12)
        while True:
            with lock:items=list(remote_runs.items())
            for cid,r in items:
                if not r.get('desired'):continue
                try:
                    d=_request(r['node'],'/api/v1/status',timeout=5); st=(d.get('channels') or {}).get(cid) or {}
                    if st.get('running'):r['failures']=0;continue
                    r['failures']=r.get('failures',0)+1
                except Exception:r['failures']=r.get('failures',0)+1
                if r['failures']<3:continue
                ch=db.get_channel(cid); next_node=_select_node(ch,exclude={r['node']['id']}) if ch else None
                if next_node:
                    try:
                        p=r['payload'];dispatch(next_node,cid,ch,p.get('platforms'),p.get('media'),p.get('trigger','manual'),p.get('schedule'))
                    except Exception:pass
                elif ch and str(ch.get('node_mode'))=='auto':
                    try:
                        p=r['payload'];local_start(cid,p.get('platforms'),p.get('media'),p.get('trigger','manual'),p.get('schedule'));remote_runs.pop(cid,None)
                    except Exception:pass
            time.sleep(10)

    manager.start=MethodType(start,manager);manager.stop=MethodType(stop,manager);manager.channel_status=MethodType(status,manager)
    threading.Thread(target=supervisor,daemon=True,name='distributed-failover').start();NODE_MONITOR.start()
    return manager
