from __future__ import annotations

import json
import shutil
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

from .config import DB_PATH, LEGACY_CHANNELS_PATH, DEFAULT_DESTINATIONS, DEFAULT_CHANNEL_SETTINGS
from .utils import now_iso, safe_filename, normalize_time

_LOCK = threading.RLock()

SCHEMA = r'''
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS channels (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    settings_json TEXT NOT NULL DEFAULT '{}',
    desired_running INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS destinations (
    channel_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    label TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    rtmp_url TEXT NOT NULL DEFAULT '',
    stream_key TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL DEFAULT 'horizontal',
    dedicated INTEGER NOT NULL DEFAULT 1,
    settings_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(channel_id, slug),
    FOREIGN KEY(channel_id) REFERENCES channels(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS schedules (
    id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'weekly',
    weekdays_json TEXT NOT NULL DEFAULT '[]',
    schedule_time TEXT NOT NULL,
    run_date TEXT NOT NULL DEFAULT '',
    start_date TEXT NOT NULL DEFAULT '',
    end_date TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    conflict_policy TEXT NOT NULL DEFAULT 'skip',
    stop_before_seconds INTEGER NOT NULL DEFAULT 60,
    platforms_json TEXT NOT NULL DEFAULT '[]',
    shuffle INTEGER NOT NULL DEFAULT 0,
    repeat_playlist INTEGER NOT NULL DEFAULT 0,
    max_duration_minutes INTEGER NOT NULL DEFAULT 0,
    last_run_key TEXT NOT NULL DEFAULT '',
    last_started_at TEXT NOT NULL DEFAULT '',
    last_finished_at TEXT NOT NULL DEFAULT '',
    last_status TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(channel_id) REFERENCES channels(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS schedule_media (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    filename TEXT NOT NULL,
    FOREIGN KEY(schedule_id) REFERENCES schedules(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_schedule_media_position ON schedule_media(schedule_id, position);
CREATE INDEX IF NOT EXISTS idx_schedules_channel ON schedules(channel_id);

CREATE TABLE IF NOT EXISTS live_runs (
    id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    schedule_id TEXT,
    trigger TEXT NOT NULL,
    media_label TEXT NOT NULL DEFAULT '',
    platforms_json TEXT NOT NULL DEFAULT '[]',
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL DEFAULT '',
    planned_stop_at TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'running',
    message TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(channel_id) REFERENCES channels(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_live_runs_channel ON live_runs(channel_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_live_runs_status ON live_runs(status);

CREATE TABLE IF NOT EXISTS platform_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    live_run_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    pid INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'starting',
    started_at TEXT NOT NULL DEFAULT '',
    ended_at TEXT NOT NULL DEFAULT '',
    retries INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    UNIQUE(live_run_id, slug),
    FOREIGN KEY(live_run_id) REFERENCES live_runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS media_meta (
    kind TEXT NOT NULL,
    filename TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    mtime REAL NOT NULL DEFAULT 0,
    duration_seconds REAL NOT NULL DEFAULT 0,
    width INTEGER NOT NULL DEFAULT 0,
    height INTEGER NOT NULL DEFAULT 0,
    codec TEXT NOT NULL DEFAULT '',
    fps REAL NOT NULL DEFAULT 0,
    sha256 TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(kind, filename)
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    level TEXT NOT NULL,
    event_type TEXT NOT NULL,
    channel_id TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_events(created_at DESC);
'''


@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        con = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON')
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()


def init_db():
    with connect() as con:
        con.executescript(SCHEMA)
        con.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('schema_version','2')")
    migrate_legacy_channels()


def get_meta(key: str, default='') -> str:
    with connect() as con:
        row = con.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return row['value'] if row else default


def set_meta(key: str, value: str):
    with connect() as con:
        con.execute('INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, str(value)))


def get_setting(key: str, default='') -> str:
    with connect() as con:
        row = con.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return row['value'] if row else default


def set_setting(key: str, value: str):
    with connect() as con:
        con.execute(
            'INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) '
            'ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at',
            (key, str(value), now_iso()),
        )


def json_load(raw, default):
    try:
        return json.loads(raw) if raw not in (None, '') else default
    except Exception:
        return default


def merge_channel_settings(raw: dict | None) -> dict:
    out = dict(DEFAULT_CHANNEL_SETTINGS)
    if raw:
        out.update(raw)
    return out


def create_channel(name: str) -> str:
    cid = uuid.uuid4().hex[:8]
    ts = now_iso()
    with connect() as con:
        con.execute(
            'INSERT INTO channels(id,name,settings_json,desired_running,created_at,updated_at) VALUES(?,?,?,?,?,?)',
            (cid, name or 'Nova live', json.dumps(DEFAULT_CHANNEL_SETTINGS, ensure_ascii=False), 0, ts, ts),
        )
        for slug, d in DEFAULT_DESTINATIONS.items():
            con.execute(
                'INSERT INTO destinations(channel_id,slug,label,enabled,rtmp_url,stream_key,mode,dedicated,settings_json) VALUES(?,?,?,?,?,?,?,?,?)',
                (cid, slug, d['label'], int(bool(d.get('enabled'))), d.get('rtmp_url',''), '', d.get('mode','horizontal'), int(bool(d.get('dedicated', True))), '{}'),
            )
    audit('info', 'channel_created', cid, f'Canal criado: {name}')
    return cid


def delete_channel(cid: str):
    with connect() as con:
        con.execute('DELETE FROM channels WHERE id=?', (cid,))
    audit('info', 'channel_deleted', cid, 'Canal removido')


def _channel_from_rows(row, destinations, schedules=None):
    settings = merge_channel_settings(json_load(row['settings_json'], {}))
    out = {
        'id': row['id'], 'name': row['name'], 'desired_running': bool(row['desired_running']),
        'created_at': row['created_at'], 'updated_at': row['updated_at'],
        **settings,
        'destinations': destinations,
    }
    if schedules is not None:
        out['schedules'] = schedules
    return out


def list_channels(include_schedules=True) -> dict:
    with connect() as con:
        rows = con.execute('SELECT * FROM channels ORDER BY name COLLATE NOCASE').fetchall()
        result = {}
        for row in rows:
            dest_rows = con.execute('SELECT * FROM destinations WHERE channel_id=? ORDER BY slug', (row['id'],)).fetchall()
            dests = {}
            for d in dest_rows:
                extra = json_load(d['settings_json'], {})
                dests[d['slug']] = {
                    'label': d['label'], 'enabled': bool(d['enabled']), 'rtmp_url': d['rtmp_url'],
                    'stream_key': d['stream_key'], 'mode': d['mode'], 'dedicated': bool(d['dedicated']), **extra,
                }
            schedules = list_schedules(row['id'], con=con) if include_schedules else None
            result[row['id']] = _channel_from_rows(row, dests, schedules)
        return result


def get_channel(cid: str, include_schedules=True):
    with connect() as con:
        row = con.execute('SELECT * FROM channels WHERE id=?', (cid,)).fetchone()
        if not row:
            return None
        dest_rows = con.execute('SELECT * FROM destinations WHERE channel_id=? ORDER BY slug', (cid,)).fetchall()
        dests = {}
        for d in dest_rows:
            extra = json_load(d['settings_json'], {})
            dests[d['slug']] = {
                'label': d['label'], 'enabled': bool(d['enabled']), 'rtmp_url': d['rtmp_url'],
                'stream_key': d['stream_key'], 'mode': d['mode'], 'dedicated': bool(d['dedicated']), **extra,
            }
        schedules = list_schedules(cid, con=con) if include_schedules else None
        return _channel_from_rows(row, dests, schedules)


def save_channel(cid: str, name: str, settings: dict, destinations: dict):
    ts = now_iso()
    clean_settings = merge_channel_settings(settings)
    with connect() as con:
        con.execute('UPDATE channels SET name=?,settings_json=?,updated_at=? WHERE id=?', (name, json.dumps(clean_settings, ensure_ascii=False), ts, cid))
        for slug, incoming in destinations.items():
            base = DEFAULT_DESTINATIONS.get(slug, {})
            label = incoming.get('label') or base.get('label') or slug
            enabled = int(bool(incoming.get('enabled')))
            rtmp_url = str(incoming.get('rtmp_url') or '')
            stream_key = str(incoming.get('stream_key') or '')
            mode = str(incoming.get('mode') or base.get('mode') or 'horizontal')
            dedicated = int(bool(incoming.get('dedicated', True)))
            extras = {k:v for k,v in incoming.items() if k not in {'label','enabled','rtmp_url','stream_key','mode','dedicated'}}
            con.execute(
                'INSERT INTO destinations(channel_id,slug,label,enabled,rtmp_url,stream_key,mode,dedicated,settings_json) '
                'VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(channel_id,slug) DO UPDATE SET '
                'label=excluded.label,enabled=excluded.enabled,rtmp_url=excluded.rtmp_url,stream_key=excluded.stream_key,mode=excluded.mode,dedicated=excluded.dedicated,settings_json=excluded.settings_json',
                (cid,slug,label,enabled,rtmp_url,stream_key,mode,dedicated,json.dumps(extras,ensure_ascii=False)),
            )
    audit('info','channel_saved',cid,'Configurações salvas')


def set_desired_running(cid: str, desired: bool):
    with connect() as con:
        con.execute('UPDATE channels SET desired_running=?,updated_at=? WHERE id=?', (int(bool(desired)), now_iso(), cid))


def _schedule_from_row(row, con):
    media = [r['filename'] for r in con.execute('SELECT filename FROM schedule_media WHERE schedule_id=? ORDER BY position,id', (row['id'],)).fetchall()]
    return {
        'id': row['id'], 'channel_id': row['channel_id'], 'name': row['name'], 'kind': row['kind'],
        'weekdays': json_load(row['weekdays_json'], []), 'time': row['schedule_time'], 'run_date': row['run_date'],
        'start_date': row['start_date'], 'end_date': row['end_date'], 'enabled': bool(row['enabled']),
        'conflict_policy': row['conflict_policy'], 'stop_before_seconds': row['stop_before_seconds'],
        'platforms': json_load(row['platforms_json'], []), 'shuffle': bool(row['shuffle']),
        'repeat_playlist': bool(row['repeat_playlist']), 'max_duration_minutes': row['max_duration_minutes'],
        'last_run_key': row['last_run_key'], 'last_started_at': row['last_started_at'],
        'last_finished_at': row['last_finished_at'], 'last_status': row['last_status'],
        'created_at': row['created_at'], 'updated_at': row['updated_at'], 'media': media,
    }


def list_schedules(channel_id: str | None = None, con=None):
    own = con is None
    if own:
        ctx = connect(); con = ctx.__enter__()
    try:
        if channel_id:
            rows = con.execute('SELECT * FROM schedules WHERE channel_id=? ORDER BY schedule_time,id', (channel_id,)).fetchall()
        else:
            rows = con.execute('SELECT * FROM schedules ORDER BY schedule_time,id').fetchall()
        return [_schedule_from_row(r, con) for r in rows]
    finally:
        if own:
            ctx.__exit__(None,None,None)


def get_schedule(schedule_id: str):
    with connect() as con:
        row = con.execute('SELECT * FROM schedules WHERE id=?', (schedule_id,)).fetchone()
        return _schedule_from_row(row, con) if row else None


def save_schedule(data: dict) -> str:
    sid = data.get('id') or uuid.uuid4().hex[:10]
    ts = now_iso()
    schedule_time = normalize_time(data.get('time'))
    if not schedule_time:
        raise ValueError('Horário inválido')
    weekdays = sorted({int(x) for x in data.get('weekdays', []) if str(x).isdigit() and 0 <= int(x) <= 6})
    platforms = [str(x) for x in data.get('platforms', []) if x]
    media = [safe_filename(x) for x in data.get('media', []) if safe_filename(x)]
    if not media:
        raise ValueError('Escolha pelo menos um vídeo')
    if not platforms:
        raise ValueError('Escolha pelo menos uma plataforma')
    kind = str(data.get('kind') or 'weekly')
    if kind not in {'weekly','daily','weekdays','once'}:
        kind = 'weekly'
    if kind == 'weekly' and not weekdays:
        raise ValueError('Marque pelo menos um dia da semana.')
    if kind == 'once' and not str(data.get('run_date') or ''):
        raise ValueError('Informe a data da execução única.')
    conflict = str(data.get('conflict_policy') or 'skip')
    if conflict not in {'skip','stop_current','wait'}:
        conflict = 'skip'
    with connect() as con:
        exists = con.execute('SELECT created_at FROM schedules WHERE id=?', (sid,)).fetchone()
        created = exists['created_at'] if exists else ts
        con.execute(
            'INSERT INTO schedules(id,channel_id,name,kind,weekdays_json,schedule_time,run_date,start_date,end_date,enabled,conflict_policy,stop_before_seconds,platforms_json,shuffle,repeat_playlist,max_duration_minutes,last_run_key,last_started_at,last_finished_at,last_status,created_at,updated_at) '
            'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) '
            'ON CONFLICT(id) DO UPDATE SET channel_id=excluded.channel_id,name=excluded.name,kind=excluded.kind,weekdays_json=excluded.weekdays_json,schedule_time=excluded.schedule_time,run_date=excluded.run_date,start_date=excluded.start_date,end_date=excluded.end_date,enabled=excluded.enabled,conflict_policy=excluded.conflict_policy,stop_before_seconds=excluded.stop_before_seconds,platforms_json=excluded.platforms_json,shuffle=excluded.shuffle,repeat_playlist=excluded.repeat_playlist,max_duration_minutes=excluded.max_duration_minutes,updated_at=excluded.updated_at',
            (sid,data['channel_id'],str(data.get('name') or ''),kind,json.dumps(weekdays),schedule_time,str(data.get('run_date') or ''),str(data.get('start_date') or ''),str(data.get('end_date') or ''),int(bool(data.get('enabled',True))),conflict,int(data.get('stop_before_seconds') or 60),json.dumps(platforms),int(bool(data.get('shuffle'))),int(bool(data.get('repeat_playlist'))),int(data.get('max_duration_minutes') or 0),str(data.get('last_run_key') or ''),str(data.get('last_started_at') or ''),str(data.get('last_finished_at') or ''),str(data.get('last_status') or 'Aguardando próximo horário.'),created,ts),
        )
        con.execute('DELETE FROM schedule_media WHERE schedule_id=?', (sid,))
        con.executemany('INSERT INTO schedule_media(schedule_id,position,filename) VALUES(?,?,?)', [(sid,i,f) for i,f in enumerate(media)])
    audit('info','schedule_saved',data['channel_id'],f'Agendamento salvo: {sid}', {'schedule_id':sid})
    return sid


def update_schedule_status(sid: str, **fields):
    allowed = {'enabled','last_run_key','last_started_at','last_finished_at','last_status'}
    parts=[]; values=[]
    for k,v in fields.items():
        if k in allowed:
            parts.append(f'{k}=?'); values.append(int(v) if k=='enabled' else str(v or ''))
    if not parts:
        return
    parts.append('updated_at=?'); values.append(now_iso()); values.append(sid)
    with connect() as con:
        con.execute(f"UPDATE schedules SET {','.join(parts)} WHERE id=?", values)


def delete_schedule(sid: str):
    with connect() as con:
        con.execute('DELETE FROM schedules WHERE id=?', (sid,))


def create_live_run(channel_id: str, schedule_id: str | None, trigger: str, media_label: str, platforms: list[str], planned_stop_at='') -> str:
    rid = uuid.uuid4().hex[:12]
    with connect() as con:
        con.execute('INSERT INTO live_runs(id,channel_id,schedule_id,trigger,media_label,platforms_json,started_at,planned_stop_at,status) VALUES(?,?,?,?,?,?,?,?,?)',
                    (rid,channel_id,schedule_id,trigger,media_label,json.dumps(platforms),now_iso(),planned_stop_at,'running'))
    return rid


def finish_live_run(run_id: str, status='finished', message=''):
    with connect() as con:
        con.execute('UPDATE live_runs SET ended_at=?,status=?,message=? WHERE id=?', (now_iso(),status,message,run_id))


def upsert_platform_run(run_id: str, slug: str, **fields):
    with connect() as con:
        con.execute('INSERT OR IGNORE INTO platform_runs(live_run_id,slug) VALUES(?,?)', (run_id,slug))
        allowed={'pid','status','started_at','ended_at','retries','last_error'}
        parts=[]; vals=[]
        for k,v in fields.items():
            if k in allowed:
                parts.append(f'{k}=?'); vals.append(v)
        if parts:
            vals.extend([run_id,slug])
            con.execute(f"UPDATE platform_runs SET {','.join(parts)} WHERE live_run_id=? AND slug=?", vals)


def list_history(limit=200):
    with connect() as con:
        rows = con.execute('SELECT lr.*,c.name channel_name FROM live_runs lr LEFT JOIN channels c ON c.id=lr.channel_id ORDER BY lr.started_at DESC LIMIT ?', (int(limit),)).fetchall()
        out=[]
        for row in rows:
            d=dict(row); d['platforms']=json_load(d.pop('platforms_json'),[])
            d['platform_runs']=[dict(x) for x in con.execute('SELECT * FROM platform_runs WHERE live_run_id=? ORDER BY slug',(d['id'],)).fetchall()]
            out.append(d)
        return out


def list_running_runs():
    with connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM live_runs WHERE status='running' ORDER BY started_at DESC").fetchall()]


def audit(level: str, event_type: str, channel_id: str, message: str, payload=None):
    with connect() as con:
        con.execute('INSERT INTO audit_events(created_at,level,event_type,channel_id,message,payload_json) VALUES(?,?,?,?,?,?)',
                    (now_iso(),level,event_type,channel_id or '',message,json.dumps(payload or {},ensure_ascii=False)))


def list_audit(limit=300):
    with connect() as con:
        return [dict(r) for r in con.execute('SELECT * FROM audit_events ORDER BY id DESC LIMIT ?', (int(limit),)).fetchall()]


def media_meta_get(kind: str, filename: str):
    with connect() as con:
        row = con.execute('SELECT * FROM media_meta WHERE kind=? AND filename=?',(kind,filename)).fetchone()
        return dict(row) if row else None


def media_meta_upsert(meta: dict):
    with connect() as con:
        con.execute(
            'INSERT INTO media_meta(kind,filename,size_bytes,mtime,duration_seconds,width,height,codec,fps,sha256,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?) '
            'ON CONFLICT(kind,filename) DO UPDATE SET size_bytes=excluded.size_bytes,mtime=excluded.mtime,duration_seconds=excluded.duration_seconds,width=excluded.width,height=excluded.height,codec=excluded.codec,fps=excluded.fps,sha256=excluded.sha256,updated_at=excluded.updated_at',
            (meta['kind'],meta['filename'],meta.get('size_bytes',0),meta.get('mtime',0),meta.get('duration_seconds',0),meta.get('width',0),meta.get('height',0),meta.get('codec',''),meta.get('fps',0),meta.get('sha256',''),now_iso()),
        )


def media_usage(filename: str) -> int:
    with connect() as con:
        c=con.execute('SELECT COUNT(*) n FROM schedule_media WHERE filename=?',(filename,)).fetchone()['n']
        for row in con.execute('SELECT settings_json FROM channels').fetchall():
            s=json_load(row['settings_json'],{})
            if filename in {s.get('video'),s.get('shorts_video')}:
                c += 1
        return int(c)


def migrate_legacy_channels():
    if get_meta('legacy_channels_imported','') == '1':
        return
    if not LEGACY_CHANNELS_PATH.exists():
        set_meta('legacy_channels_imported','1')
        return
    try:
        payload=json.loads(LEGACY_CHANNELS_PATH.read_text(encoding='utf-8'))
        if not isinstance(payload, dict):
            payload={}
    except Exception:
        payload={}
    if payload:
        backup = LEGACY_CHANNELS_PATH.with_name('channels.json.pre-v2-backup')
        if not backup.exists():
            shutil.copy2(LEGACY_CHANNELS_PATH, backup)
    ts=now_iso()
    with connect() as con:
        for cid, raw in payload.items():
            if not isinstance(raw,dict):
                continue
            name=str(raw.get('name') or cid)
            dests=raw.get('destinations') or {}
            if not dests:
                dests=json.loads(json.dumps(DEFAULT_DESTINATIONS))
                dests['youtube']['stream_key']=str(raw.get('stream_key') or '')
                dests['youtube']['rtmp_url']=str(raw.get('rtmp_url') or dests['youtube']['rtmp_url'])
            settings={k:v for k,v in raw.items() if k not in {'id','name','destinations','schedules','desired_running','pid','running','started_at','stopped_at','scheduled_active','scheduled_event_id','scheduled_video','scheduled_started_at','scheduled_stop_at','shorts_pid','kwai_pid','twitch_pid','kick_pid','shorts_running','kwai_running','twitch_running','kick_running'}}
            settings=merge_channel_settings(settings)
            con.execute('INSERT OR IGNORE INTO channels(id,name,settings_json,desired_running,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                        (cid,name,json.dumps(settings,ensure_ascii=False),int(bool(raw.get('desired_running'))),str(raw.get('created_at') or ts),ts))
            for slug, base in DEFAULT_DESTINATIONS.items():
                d=dict(base); d.update(dests.get(slug) or {})
                extras={k:v for k,v in d.items() if k not in {'label','enabled','rtmp_url','stream_key','mode','dedicated'}}
                con.execute('INSERT OR REPLACE INTO destinations(channel_id,slug,label,enabled,rtmp_url,stream_key,mode,dedicated,settings_json) VALUES(?,?,?,?,?,?,?,?,?)',
                            (cid,slug,str(d.get('label') or base['label']),int(bool(d.get('enabled'))),str(d.get('rtmp_url') or ''),str(d.get('stream_key') or ''),str(d.get('mode') or base.get('mode','horizontal')),int(bool(d.get('dedicated',True))),json.dumps(extras,ensure_ascii=False)))
            for old in raw.get('schedules') or []:
                if not isinstance(old,dict):
                    continue
                sid=str(old.get('id') or uuid.uuid4().hex[:10])
                w=int(old.get('weekday',-1)) if str(old.get('weekday','')).lstrip('-').isdigit() else -1
                time_value=normalize_time(old.get('time'))
                video=safe_filename(old.get('video'))
                if not time_value or not video or w < 0 or w > 6:
                    continue
                platforms=old.get('platforms') or [slug for slug,d in dests.items() if d.get('enabled')]
                if not platforms:
                    platforms=['youtube']
                con.execute('INSERT OR IGNORE INTO schedules(id,channel_id,name,kind,weekdays_json,schedule_time,enabled,conflict_policy,stop_before_seconds,platforms_json,last_run_key,last_started_at,last_finished_at,last_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                            (sid,cid,'','weekly',json.dumps([w]),time_value,int(bool(old.get('enabled',True))),'skip',int(old.get('stop_before_seconds') or 60),json.dumps(platforms),str(old.get('last_run_key') or ''),str(old.get('last_started_at') or ''),str(old.get('last_finished_at') or ''),str(old.get('last_status') or 'Importado do v1'),ts,ts))
                con.execute('INSERT OR IGNORE INTO schedule_media(schedule_id,position,filename) VALUES(?,?,?)',(sid,0,video))
    set_meta('legacy_channels_imported','1')
    audit('info','migration','',f'Migração v1 → v2 concluída: {len(payload)} canal(is) importado(s).')
