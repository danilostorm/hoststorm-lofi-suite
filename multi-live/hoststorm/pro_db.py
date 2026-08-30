from __future__ import annotations

import json
import secrets
import sqlite3
import uuid
from contextlib import contextmanager

from .config import DB_PATH
from .security import encrypt_secret, decrypt_secret, hash_password, hash_token
from .utils import now_iso

PRO_SCHEMA = r'''
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'viewer',
  totp_secret TEXT NOT NULL DEFAULT '',
  totp_enabled INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_login_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS api_tokens (
  id TEXT PRIMARY KEY,
  user_id TEXT,
  name TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  scopes_json TEXT NOT NULL DEFAULT '[]',
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  last_used_at TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS stream_profiles (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  platform TEXT NOT NULL DEFAULT 'custom',
  width INTEGER NOT NULL,
  height INTEGER NOT NULL,
  fps INTEGER NOT NULL,
  video_bitrate_k INTEGER NOT NULL,
  audio_bitrate_k INTEGER NOT NULL,
  encoder TEXT NOT NULL DEFAULT 'auto',
  preset TEXT NOT NULL DEFAULT 'veryfast',
  extra_json TEXT NOT NULL DEFAULT '{}',
  builtin INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS nodes (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  base_url TEXT NOT NULL DEFAULT '',
  token_enc TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1,
  priority INTEGER NOT NULL DEFAULT 100,
  tags_json TEXT NOT NULL DEFAULT '[]',
  cpu REAL NOT NULL DEFAULT 0,
  ram REAL NOT NULL DEFAULT 0,
  gpu REAL NOT NULL DEFAULT 0,
  active_streams INTEGER NOT NULL DEFAULT 0,
  last_seen_at TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'unknown',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS program_blocks (
  id TEXT PRIMARY KEY,
  channel_id TEXT NOT NULL,
  name TEXT NOT NULL,
  start_time TEXT NOT NULL,
  end_time TEXT NOT NULL,
  weekdays_json TEXT NOT NULL DEFAULT '[]',
  media_json TEXT NOT NULL DEFAULT '[]',
  bumper_before TEXT NOT NULL DEFAULT '',
  bumper_after TEXT NOT NULL DEFAULT '',
  commercial_interval_minutes INTEGER NOT NULL DEFAULT 0,
  commercial_media_json TEXT NOT NULL DEFAULT '[]',
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rotation_state (
  channel_id TEXT NOT NULL,
  collection_key TEXT NOT NULL,
  filename TEXT NOT NULL,
  last_played_at TEXT NOT NULL DEFAULT '',
  play_count INTEGER NOT NULL DEFAULT 0,
  weight REAL NOT NULL DEFAULT 1,
  PRIMARY KEY(channel_id,collection_key,filename)
);
CREATE TABLE IF NOT EXISTS markers (
  id TEXT PRIMARY KEY,
  live_run_id TEXT NOT NULL,
  channel_id TEXT NOT NULL,
  offset_seconds REAL NOT NULL,
  label TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  clip_path TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS alert_events (
  id TEXT PRIMARY KEY,
  severity TEXT NOT NULL,
  source TEXT NOT NULL,
  channel_id TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL,
  message TEXT NOT NULL,
  acknowledged INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  acknowledged_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_alert_events_created ON alert_events(created_at DESC);
CREATE TABLE IF NOT EXISTS runtime_metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  channel_id TEXT NOT NULL,
  platform TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  fps REAL NOT NULL DEFAULT 0,
  bitrate_k REAL NOT NULL DEFAULT 0,
  speed REAL NOT NULL DEFAULT 0,
  dropped_frames INTEGER NOT NULL DEFAULT 0,
  quality TEXT NOT NULL DEFAULT 'unknown'
);
CREATE INDEX IF NOT EXISTS idx_runtime_metrics_channel ON runtime_metrics(channel_id,recorded_at DESC);
CREATE TABLE IF NOT EXISTS recordings (
  id TEXT PRIMARY KEY,
  live_run_id TEXT NOT NULL,
  channel_id TEXT NOT NULL,
  path TEXT NOT NULL,
  size_bytes INTEGER NOT NULL DEFAULT 0,
  duration_seconds REAL NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS integration_accounts (
  id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  name TEXT NOT NULL,
  config_enc TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
'''

@contextmanager
def connect():
    con=sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    con.row_factory=sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    try:
        yield con; con.commit()
    except Exception:
        con.rollback(); raise
    finally:
        con.close()


def init_pro_db(admin_user='admin', admin_password=''):
    with connect() as con:
        con.executescript(PRO_SCHEMA)
        con.execute("INSERT INTO meta(key,value) VALUES('pro_schema_version','3') ON CONFLICT(key) DO UPDATE SET value='3'")
    seed_profiles()
    if admin_password:
        ensure_admin(admin_user, admin_password)


def ensure_admin(username, password):
    with connect() as con:
        row=con.execute('SELECT id FROM users WHERE username=? COLLATE NOCASE',(username,)).fetchone()
        if row: return row['id']
        uid=uuid.uuid4().hex[:12]; ts=now_iso()
        con.execute('INSERT INTO users(id,username,password_hash,role,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                    (uid,username,hash_password(password),'admin',ts,ts))
        return uid


def list_users():
    with connect() as con:
        return [dict(r) for r in con.execute('SELECT id,username,role,totp_enabled,enabled,created_at,last_login_at FROM users ORDER BY username').fetchall()]


def get_user_by_username(username):
    with connect() as con:
        r=con.execute('SELECT * FROM users WHERE username=? COLLATE NOCASE',(username,)).fetchone(); return dict(r) if r else None


def get_user(uid):
    with connect() as con:
        r=con.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone(); return dict(r) if r else None


def save_user(username, role='viewer', password='', uid=None, enabled=True):
    ts=now_iso(); uid=uid or uuid.uuid4().hex[:12]
    with connect() as con:
        exists=con.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
        if exists:
            ph=hash_password(password) if password else exists['password_hash']
            con.execute('UPDATE users SET username=?,role=?,password_hash=?,enabled=?,updated_at=? WHERE id=?',
                        (username,role,ph,int(enabled),ts,uid))
        else:
            if not password: raise ValueError('Senha obrigatória para novo usuário.')
            con.execute('INSERT INTO users(id,username,password_hash,role,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                        (uid,username,hash_password(password),role,int(enabled),ts,ts))
    return uid


def set_totp(uid, secret, enabled):
    with connect() as con:
        con.execute('UPDATE users SET totp_secret=?,totp_enabled=?,updated_at=? WHERE id=?',
                    (encrypt_secret(secret),int(enabled),now_iso(),uid))


def user_totp_secret(user):
    return decrypt_secret(user.get('totp_secret',''))


def touch_login(uid):
    with connect() as con: con.execute('UPDATE users SET last_login_at=?,updated_at=? WHERE id=?',(now_iso(),now_iso(),uid))


def create_token(user_id, name, token, scopes):
    tid=uuid.uuid4().hex[:12]
    with connect() as con:
        con.execute('INSERT INTO api_tokens(id,user_id,name,token_hash,scopes_json,created_at) VALUES(?,?,?,?,?,?)',
                    (tid,user_id,name,hash_token(token),json.dumps(scopes),now_iso()))
    return tid


def authenticate_token(token):
    if not token: return None
    with connect() as con:
        r=con.execute('SELECT t.*,u.username,u.role,u.enabled user_enabled FROM api_tokens t LEFT JOIN users u ON u.id=t.user_id WHERE token_hash=? AND t.enabled=1',(hash_token(token),)).fetchone()
        if not r or not r['user_enabled']: return None
        con.execute('UPDATE api_tokens SET last_used_at=? WHERE id=?',(now_iso(),r['id']))
        d=dict(r); d['scopes']=json.loads(d.pop('scopes_json') or '[]'); return d


def list_tokens(user_id=None):
    with connect() as con:
        if user_id: rows=con.execute('SELECT id,user_id,name,scopes_json,enabled,created_at,last_used_at FROM api_tokens WHERE user_id=? ORDER BY created_at DESC',(user_id,)).fetchall()
        else: rows=con.execute('SELECT id,user_id,name,scopes_json,enabled,created_at,last_used_at FROM api_tokens ORDER BY created_at DESC').fetchall()
        return [{**dict(r),'scopes':json.loads(r['scopes_json'] or '[]')} for r in rows]


def seed_profiles():
    defaults=[
      ('youtube-1080p60','YouTube 1080p60','youtube',1920,1080,60,6000,160,'auto','fast'),
      ('twitch-1080p60','Twitch 1080p60','twitch',1920,1080,60,6000,160,'auto','fast'),
      ('kick-1080p60','Kick 1080p60','kick',1920,1080,60,6000,160,'auto','fast'),
      ('vertical-1080p60','Vertical 1080×1920','vertical',1080,1920,60,4500,160,'auto','fast'),
      ('safe-720p30','Seguro 720p30','custom',1280,720,30,2500,128,'libx264','veryfast'),
    ]
    ts=now_iso()
    with connect() as con:
        for p in defaults:
            con.execute('INSERT OR IGNORE INTO stream_profiles(id,name,platform,width,height,fps,video_bitrate_k,audio_bitrate_k,encoder,preset,builtin,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,1,?,?)',(*p,ts,ts))


def list_profiles():
    with connect() as con: return [dict(r) for r in con.execute('SELECT * FROM stream_profiles ORDER BY builtin DESC,name').fetchall()]


def save_profile(data):
    pid=data.get('id') or uuid.uuid4().hex[:12]; ts=now_iso()
    with connect() as con:
        con.execute('INSERT INTO stream_profiles(id,name,platform,width,height,fps,video_bitrate_k,audio_bitrate_k,encoder,preset,extra_json,builtin,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,platform=excluded.platform,width=excluded.width,height=excluded.height,fps=excluded.fps,video_bitrate_k=excluded.video_bitrate_k,audio_bitrate_k=excluded.audio_bitrate_k,encoder=excluded.encoder,preset=excluded.preset,extra_json=excluded.extra_json,updated_at=excluded.updated_at',
                    (pid,data['name'],data.get('platform','custom'),int(data['width']),int(data['height']),int(data['fps']),int(data['video_bitrate_k']),int(data['audio_bitrate_k']),data.get('encoder','auto'),data.get('preset','veryfast'),json.dumps(data.get('extra',{})),0,ts,ts))
    return pid


def list_nodes():
    with connect() as con:
        rows=con.execute('SELECT * FROM nodes ORDER BY enabled DESC,priority,name').fetchall()
        out=[]
        for r in rows:
            d=dict(r); d['token']=decrypt_secret(d.pop('token_enc','')); d['tags']=json.loads(d.pop('tags_json') or '[]'); out.append(d)
        return out


def save_node(data):
    nid=data.get('id') or uuid.uuid4().hex[:12]; ts=now_iso()
    with connect() as con:
        old=con.execute('SELECT token_enc,created_at FROM nodes WHERE id=?',(nid,)).fetchone()
        token_enc=encrypt_secret(data.get('token','')) if data.get('token') else (old['token_enc'] if old else '')
        created=old['created_at'] if old else ts
        con.execute('INSERT INTO nodes(id,name,base_url,token_enc,enabled,priority,tags_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,base_url=excluded.base_url,token_enc=excluded.token_enc,enabled=excluded.enabled,priority=excluded.priority,tags_json=excluded.tags_json,updated_at=excluded.updated_at',
                    (nid,data['name'],data.get('base_url',''),token_enc,int(data.get('enabled',True)),int(data.get('priority',100)),json.dumps(data.get('tags',[])),created,ts))
    return nid


def update_node_health(nid, cpu, ram, gpu=0, active_streams=0, status='online'):
    with connect() as con:
        con.execute('UPDATE nodes SET cpu=?,ram=?,gpu=?,active_streams=?,status=?,last_seen_at=?,updated_at=? WHERE id=?',(cpu,ram,gpu,active_streams,status,now_iso(),now_iso(),nid))


def choose_node(required_tags=None):
    required=set(required_tags or [])
    candidates=[]
    for n in list_nodes():
        if not n['enabled'] or n['status'] not in {'online','local'}: continue
        if required and not required.issubset(set(n['tags'])): continue
        score=n['priority'] + n['cpu']*.35 + n['ram']*.25 + n['gpu']*.15 + n['active_streams']*8
        candidates.append((score,n))
    return min(candidates,key=lambda x:x[0])[1] if candidates else None


def save_metric(channel_id,platform,**m):
    with connect() as con:
        con.execute('INSERT INTO runtime_metrics(channel_id,platform,recorded_at,fps,bitrate_k,speed,dropped_frames,quality) VALUES(?,?,?,?,?,?,?,?)',
                    (channel_id,platform,now_iso(),float(m.get('fps',0)),float(m.get('bitrate_k',0)),float(m.get('speed',0)),int(m.get('dropped_frames',0)),m.get('quality','unknown')))


def recent_metrics(limit=500):
    with connect() as con: return [dict(r) for r in con.execute('SELECT * FROM runtime_metrics ORDER BY id DESC LIMIT ?',(int(limit),)).fetchall()]


def add_alert(severity,source,title,message,channel_id=''):
    aid=uuid.uuid4().hex[:12]
    with connect() as con: con.execute('INSERT INTO alert_events(id,severity,source,channel_id,title,message,created_at) VALUES(?,?,?,?,?,?,?)',(aid,severity,source,channel_id,title,message,now_iso()))
    return aid


def list_alerts(limit=200,unacked=False):
    with connect() as con:
        sql='SELECT * FROM alert_events' + (' WHERE acknowledged=0' if unacked else '') + ' ORDER BY created_at DESC LIMIT ?'
        return [dict(r) for r in con.execute(sql,(int(limit),)).fetchall()]


def ack_alert(aid):
    with connect() as con: con.execute('UPDATE alert_events SET acknowledged=1,acknowledged_at=? WHERE id=?',(now_iso(),aid))


def add_marker(live_run_id,channel_id,offset_seconds,label=''):
    mid=uuid.uuid4().hex[:12]
    with connect() as con: con.execute('INSERT INTO markers(id,live_run_id,channel_id,offset_seconds,label,created_at) VALUES(?,?,?,?,?,?)',(mid,live_run_id,channel_id,float(offset_seconds),label,now_iso()))
    return mid


def list_markers(limit=200):
    with connect() as con: return [dict(r) for r in con.execute('SELECT * FROM markers ORDER BY created_at DESC LIMIT ?',(int(limit),)).fetchall()]
