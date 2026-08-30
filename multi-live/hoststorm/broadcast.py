from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timedelta

from .media import probe_duration
from .pro_db import connect
from .utils import normalize_time, now_dt, now_iso


def list_blocks(channel_id=None):
    with connect() as con:
        if channel_id: rows=con.execute('SELECT * FROM program_blocks WHERE channel_id=? ORDER BY start_time',(channel_id,)).fetchall()
        else: rows=con.execute('SELECT * FROM program_blocks ORDER BY channel_id,start_time').fetchall()
    out=[]
    for r in rows:
        d=dict(r); d['weekdays']=json.loads(d.pop('weekdays_json') or '[]'); d['media']=json.loads(d.pop('media_json') or '[]'); d['commercial_media']=json.loads(d.pop('commercial_media_json') or '[]'); out.append(d)
    return out


def get_block(bid):
    for b in list_blocks():
        if b['id']==bid: return b
    return None


def save_block(data):
    bid=data.get('id') or uuid.uuid4().hex[:12]; ts=now_iso(); start=normalize_time(data.get('start_time')); end=normalize_time(data.get('end_time'))
    if not start or not end: raise ValueError('Horário inicial/final inválido.')
    weekdays=sorted({int(x) for x in data.get('weekdays',[]) if str(x).isdigit() and 0<=int(x)<=6})
    if not weekdays: raise ValueError('Marque pelo menos um dia.')
    media=[str(x) for x in data.get('media',[]) if x]
    if not media: raise ValueError('Escolha conteúdo para o bloco.')
    with connect() as con:
        old=con.execute('SELECT created_at FROM program_blocks WHERE id=?',(bid,)).fetchone(); created=old['created_at'] if old else ts
        con.execute('INSERT INTO program_blocks(id,channel_id,name,start_time,end_time,weekdays_json,media_json,bumper_before,bumper_after,commercial_interval_minutes,commercial_media_json,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET channel_id=excluded.channel_id,name=excluded.name,start_time=excluded.start_time,end_time=excluded.end_time,weekdays_json=excluded.weekdays_json,media_json=excluded.media_json,bumper_before=excluded.bumper_before,bumper_after=excluded.bumper_after,commercial_interval_minutes=excluded.commercial_interval_minutes,commercial_media_json=excluded.commercial_media_json,enabled=excluded.enabled,updated_at=excluded.updated_at',
                    (bid,data['channel_id'],data.get('name','Programa'),start,end,json.dumps(weekdays),json.dumps(media),data.get('bumper_before',''),data.get('bumper_after',''),int(data.get('commercial_interval_minutes') or 0),json.dumps(data.get('commercial_media',[])),int(data.get('enabled',True)),created,ts))
    return bid


def delete_block(bid):
    with connect() as con: con.execute('DELETE FROM program_blocks WHERE id=?',(bid,))


def _minutes_between(start,end):
    sh,sm=map(int,start.split(':')); eh,em=map(int,end.split(':')); a=sh*60+sm; b=eh*60+em
    if b<=a: b+=1440
    return b-a


def block_active(block, now=None):
    now=now or now_dt(); wd=now.weekday()
    if not block.get('enabled') or wd not in block.get('weekdays',[]): return False
    cur=now.hour*60+now.minute; sh,sm=map(int,block['start_time'].split(':')); eh,em=map(int,block['end_time'].split(':')); a=sh*60+sm; b=eh*60+em
    if b>a: return a<=cur<b
    return cur>=a or cur<b


def _rotation_order(channel_id, collection_key, media):
    with connect() as con:
        rows={r['filename']:dict(r) for r in con.execute('SELECT * FROM rotation_state WHERE channel_id=? AND collection_key=?',(channel_id,collection_key)).fetchall()}
        ordered=sorted(media,key=lambda f:(rows.get(f,{}).get('last_played_at',''),rows.get(f,{}).get('play_count',0),f.lower()))
        return ordered


def _mark_rotation(channel_id, collection_key, media):
    with connect() as con:
        for f in media:
            con.execute('INSERT INTO rotation_state(channel_id,collection_key,filename,last_played_at,play_count,weight) VALUES(?,?,?,?,1,1) ON CONFLICT(channel_id,collection_key,filename) DO UPDATE SET last_played_at=excluded.last_played_at,play_count=rotation_state.play_count+1',(channel_id,collection_key,f,now_iso()))


def build_playout(block):
    main=_rotation_order(block['channel_id'],'block:'+block['id'],block.get('media') or [])
    result=[]
    if block.get('bumper_before'): result.append(block['bumper_before'])
    interval=max(0,int(block.get('commercial_interval_minutes') or 0))*60; ads=list(block.get('commercial_media') or []); acc=0.0; ad_i=0
    for item in main:
        result.append(item)
        try: acc += max(0,probe_duration(item))
        except Exception: pass
        if interval and ads and acc>=interval:
            result.append(ads[ad_i%len(ads)]); ad_i+=1; acc=0
    if block.get('bumper_after'): result.append(block['bumper_after'])
    _mark_rotation(block['channel_id'],'block:'+block['id'],main)
    return result


class BroadcastScheduler:
    def __init__(self): self.started=False; self.current={}; self.manager=None
    def start(self,manager):
        if self.started:return
        self.started=True; self.manager=manager; threading.Thread(target=self._loop,daemon=True,name='broadcast-grid').start()
    def _loop(self):
        time.sleep(8)
        while True:
            try:self.tick()
            except Exception: pass
            time.sleep(10)
    def tick(self):
        now=now_dt(); blocks=list_blocks(); by_channel={}
        for b in blocks:
            if block_active(b,now): by_channel.setdefault(b['channel_id'],[]).append(b)
        for cid,arr in by_channel.items():
            arr.sort(key=lambda b:b['start_time']); block=arr[-1]; key=block['id']+':'+now.strftime('%Y-%m-%d')
            if self.current.get(cid)==key: continue
            st=self.manager.channel_status(cid)
            if st.get('running'): self.manager.stop(cid,'troca automática da grade 24/7')
            media=build_playout(block); duration=_minutes_between(block['start_time'],block['end_time'])
            schedule={'id':'grid-'+block['id'],'media':media,'shuffle':False,'repeat_playlist':True,'max_duration_minutes':duration,'stop_before_seconds':0}
            ok,msg=self.manager.start(cid,media=media,trigger='scheduled',schedule=schedule)
            if ok:self.current[cid]=key
        # Stop grade sessions whose block window ended.
        for cid,key in list(self.current.items()):
            if cid not in by_channel:
                st=self.manager.channel_status(cid)
                if st.get('running') and st.get('trigger')=='scheduled': self.manager.stop(cid,'fim do bloco da grade')
                self.current.pop(cid,None)

BROADCAST=BroadcastScheduler()
