from __future__ import annotations

import json
import urllib.parse
import urllib.request
import uuid

from .pro_db import connect
from .security import encrypt_secret,decrypt_secret
from .utils import now_iso


def list_integrations(mask=True):
    with connect() as con: rows=con.execute('SELECT * FROM integration_accounts ORDER BY provider,name').fetchall()
    out=[]
    for r in rows:
        d=dict(r); cfg=json.loads(decrypt_secret(d.pop('config_enc','')) or '{}')
        if mask:
            for k in list(cfg):
                if any(x in k.lower() for x in ('token','secret','key')) and cfg[k]: cfg[k]='••••••'
        d['config']=cfg; out.append(d)
    return out


def get_integration(iid):
    with connect() as con:r=con.execute('SELECT * FROM integration_accounts WHERE id=?',(iid,)).fetchone()
    if not r:return None
    d=dict(r);d['config']=json.loads(decrypt_secret(d.pop('config_enc','')) or '{}');return d


def save_integration(provider,name,config,enabled=True,iid=None):
    iid=iid or uuid.uuid4().hex[:12];ts=now_iso()
    with connect() as con:
        old=con.execute('SELECT config_enc,created_at FROM integration_accounts WHERE id=?',(iid,)).fetchone()
        if old:
            prev=json.loads(decrypt_secret(old['config_enc']) or '{}')
            for k,v in list(config.items()):
                if not v or v=='••••••': config[k]=prev.get(k,'')
            created=old['created_at']
        else:created=ts
        con.execute('INSERT INTO integration_accounts(id,provider,name,config_enc,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET provider=excluded.provider,name=excluded.name,config_enc=excluded.config_enc,enabled=excluded.enabled,updated_at=excluded.updated_at',(iid,provider,name,encrypt_secret(json.dumps(config)),int(enabled),created,ts))
    return iid


def delete_integration(iid):
    with connect() as con:con.execute('DELETE FROM integration_accounts WHERE id=?',(iid,))


def _json(url,headers=None,timeout=8):
    req=urllib.request.Request(url,headers=headers or {})
    with urllib.request.urlopen(req,timeout=timeout) as r:return json.loads(r.read().decode())


def check_integration(iid):
    item=get_integration(iid)
    if not item:return {'ok':False,'message':'Integração não encontrada.'}
    p=item['provider'];c=item['config']
    try:
        if p=='twitch':
            headers={'Client-Id':c.get('client_id',''),'Authorization':'Bearer '+c.get('access_token','')}
            login=c.get('channel_login','');d=_json('https://api.twitch.tv/helix/streams?user_login='+urllib.parse.quote(login),headers)
            live=bool(d.get('data'));return {'ok':True,'live':live,'message':'Twitch online' if live else 'Twitch offline','raw':d.get('data',[])[:1]}
        if p=='youtube':
            key=c.get('api_key','');channel=c.get('channel_id','');q=urllib.parse.urlencode({'part':'snippet','channelId':channel,'eventType':'live','type':'video','key':key})
            d=_json('https://www.googleapis.com/youtube/v3/search?'+q);live=bool(d.get('items'));return {'ok':True,'live':live,'message':'YouTube online' if live else 'YouTube offline','raw':d.get('items',[])[:1]}
        return {'ok':False,'message':'Provider sem verificação online implementada.'}
    except Exception as e:return {'ok':False,'message':str(e)}
