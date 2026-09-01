from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from .pro_db import connect
from .security import decrypt_secret, encrypt_secret
from .utils import now_iso

AI_SCHEMA = r'''
CREATE TABLE IF NOT EXISTS ai_settings (
  id INTEGER PRIMARY KEY CHECK(id=1),
  config_json TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_personas (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  system_prompt TEXT NOT NULL DEFAULT '',
  style_json TEXT NOT NULL DEFAULT '{}',
  builtin INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_providers (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  name TEXT NOT NULL,
  provider TEXT NOT NULL,
  config_enc TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_bindings (
  integration_id TEXT PRIMARY KEY,
  channel_id TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1,
  read_chat INTEGER NOT NULL DEFAULT 1,
  write_chat INTEGER NOT NULL DEFAULT 1,
  read_events INTEGER NOT NULL DEFAULT 1,
  last_status TEXT NOT NULL DEFAULT '',
  last_error TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_chat_messages (
  id TEXT PRIMARY KEY,
  platform TEXT NOT NULL,
  integration_id TEXT NOT NULL DEFAULT '',
  external_id TEXT NOT NULL,
  channel_id TEXT NOT NULL DEFAULT '',
  user_id TEXT NOT NULL DEFAULT '',
  username TEXT NOT NULL DEFAULT '',
  display_name TEXT NOT NULL DEFAULT '',
  text TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT 'chat',
  score REAL NOT NULL DEFAULT 0,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  received_at TEXT NOT NULL,
  processed_at TEXT NOT NULL DEFAULT '',
  selected INTEGER NOT NULL DEFAULT 0,
  self_message INTEGER NOT NULL DEFAULT 0,
  UNIQUE(platform,integration_id,external_id)
);
CREATE INDEX IF NOT EXISTS idx_ai_chat_pending ON ai_chat_messages(processed_at,received_at);
CREATE INDEX IF NOT EXISTS idx_ai_chat_user ON ai_chat_messages(platform,user_id,received_at DESC);
CREATE TABLE IF NOT EXISTS ai_chat_responses (
  id TEXT PRIMARY KEY,
  message_id TEXT NOT NULL DEFAULT '',
  platform TEXT NOT NULL,
  integration_id TEXT NOT NULL DEFAULT '',
  channel_id TEXT NOT NULL DEFAULT '',
  user_id TEXT NOT NULL DEFAULT '',
  username TEXT NOT NULL DEFAULT '',
  reply_text TEXT NOT NULL,
  voice_text TEXT NOT NULL DEFAULT '',
  mode TEXT NOT NULL DEFAULT 'copilot',
  status TEXT NOT NULL DEFAULT 'pending',
  provider_id TEXT NOT NULL DEFAULT '',
  score REAL NOT NULL DEFAULT 0,
  reason TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  sent_at TEXT NOT NULL DEFAULT '',
  error TEXT NOT NULL DEFAULT '',
  tts_status TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_ai_responses_created ON ai_chat_responses(created_at DESC);
CREATE TABLE IF NOT EXISTS ai_viewer_memory (
  platform TEXT NOT NULL,
  user_id TEXT NOT NULL,
  username TEXT NOT NULL DEFAULT '',
  facts_json TEXT NOT NULL DEFAULT '[]',
  interactions INTEGER NOT NULL DEFAULT 0,
  last_seen_at TEXT NOT NULL DEFAULT '',
  last_replied_at TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL,
  PRIMARY KEY(platform,user_id)
);
CREATE TABLE IF NOT EXISTS ai_live_memory (
  channel_id TEXT NOT NULL,
  key TEXT NOT NULL,
  value_json TEXT NOT NULL DEFAULT '{}',
  expires_at TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL,
  PRIMARY KEY(channel_id,key)
);
CREATE TABLE IF NOT EXISTS ai_sent_messages (
  id TEXT PRIMARY KEY,
  platform TEXT NOT NULL,
  integration_id TEXT NOT NULL DEFAULT '',
  external_id TEXT NOT NULL DEFAULT '',
  text_hash TEXT NOT NULL,
  sent_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_sent_hash ON ai_sent_messages(platform,integration_id,text_hash,sent_at DESC);
CREATE TABLE IF NOT EXISTS ai_event_subscriptions (
  provider TEXT NOT NULL,
  integration_id TEXT NOT NULL,
  event_name TEXT NOT NULL,
  remote_id TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT '',
  last_error TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL,
  PRIMARY KEY(provider,integration_id,event_name)
);
CREATE TABLE IF NOT EXISTS ai_tts_jobs (
  id TEXT PRIMARY KEY,
  response_id TEXT NOT NULL DEFAULT '',
  channel_id TEXT NOT NULL DEFAULT '',
  text TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  path TEXT NOT NULL DEFAULT '',
  duration_seconds REAL NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  played_at TEXT NOT NULL DEFAULT '',
  error TEXT NOT NULL DEFAULT ''
);
'''

DEFAULT_SETTINGS = {
    'enabled': False,
    'mode': 'copilot',
    'persona_id': 'hoststorm-natural',
    'llm_provider_id': '',
    'tts_provider_id': '',
    'window_min_seconds': 15,
    'window_max_seconds': 30,
    'responses_per_hour': 20,
    'per_user_cooldown_seconds': 180,
    'global_min_gap_seconds': 12,
    'send_delay_min_seconds': 2,
    'send_delay_max_seconds': 7,
    'cross_platform_context': True,
    'memory_enabled': True,
    'memory_retention_days': 30,
    'max_recent_context': 18,
    'reply_questions': True,
    'reply_mentions': True,
    'reply_jokes': True,
    'reply_greetings': True,
    'reply_events': True,
    'question_probability': 0.72,
    'mention_probability': 0.85,
    'joke_probability': 0.48,
    'greeting_probability': 0.18,
    'event_probability': 0.92,
    'emoji_level': 'moderate',
    'max_reply_chars': 240,
    'ai_signature': ' 🤖',
    'tts_enabled': False,
    'tts_reply_probability': 0.35,
    'tts_volume': 1.0,
    'ducking_strength': 0.55,
    'voice_cooldown_seconds': 45,
    'vision_enabled': False,
    'vision_interval_seconds': 45,
    'vision_max_width': 768,
    'prompt_injection_filter': True,
    'links_filter': True,
    'profanity_policy': 'contextual',
}

DEFAULT_PERSONA = {
    'id': 'hoststorm-natural',
    'name': 'HostStorm Natural Gamer',
    'description': 'Apresentador gamer brasileiro, informal e espontâneo.',
    'system_prompt': (
        'Você é o AI Live Host oficial do canal. Converse em português do Brasil de forma curta, '
        'natural, calorosa e gamer. Você é um assistente de IA do canal e nunca deve afirmar que é '
        'uma pessoa humana. Não fale como atendimento ao cliente, não comece respostas com Claro! '
        'e não tente responder tudo. Use humor leve quando combinar. Faça perguntas de volta às vezes.'
    ),
    'style': {'humor': 0.65, 'sarcasm': 0.25, 'competitive': 0.55, 'informal': 0.9},
}


def _loads(value, default):
    try:
        return json.loads(value) if value else default
    except Exception:
        return default


def init_ai_db():
    ts = now_iso()
    with connect() as con:
        con.executescript(AI_SCHEMA)
        row = con.execute('SELECT config_json FROM ai_settings WHERE id=1').fetchone()
        if not row:
            con.execute('INSERT INTO ai_settings(id,config_json,updated_at) VALUES(1,?,?)',
                        (json.dumps(DEFAULT_SETTINGS, ensure_ascii=False), ts))
        if not con.execute('SELECT 1 FROM ai_personas WHERE id=?', (DEFAULT_PERSONA['id'],)).fetchone():
            con.execute(
                'INSERT INTO ai_personas(id,name,description,system_prompt,style_json,builtin,enabled,created_at,updated_at) '
                'VALUES(?,?,?,?,?,1,1,?,?)',
                (DEFAULT_PERSONA['id'], DEFAULT_PERSONA['name'], DEFAULT_PERSONA['description'],
                 DEFAULT_PERSONA['system_prompt'], json.dumps(DEFAULT_PERSONA['style'], ensure_ascii=False), ts, ts),
            )


def get_settings():
    with connect() as con:
        row = con.execute('SELECT config_json FROM ai_settings WHERE id=1').fetchone()
    cfg = dict(DEFAULT_SETTINGS)
    if row:
        cfg.update(_loads(row['config_json'], {}))
    return cfg


def save_settings(data: dict):
    cfg = get_settings()
    cfg.update(data or {})
    cfg['window_min_seconds'] = max(5, min(300, int(cfg.get('window_min_seconds', 15))))
    cfg['window_max_seconds'] = max(cfg['window_min_seconds'], min(600, int(cfg.get('window_max_seconds', 30))))
    cfg['responses_per_hour'] = max(0, min(500, int(cfg.get('responses_per_hour', 20))))
    cfg['per_user_cooldown_seconds'] = max(0, min(86400, int(cfg.get('per_user_cooldown_seconds', 180))))
    cfg['global_min_gap_seconds'] = max(0, min(3600, int(cfg.get('global_min_gap_seconds', 12))))
    cfg['max_reply_chars'] = max(40, min(500, int(cfg.get('max_reply_chars', 240))))
    cfg['tts_reply_probability'] = max(0.0, min(1.0, float(cfg.get('tts_reply_probability', 0.35))))
    cfg['tts_volume'] = max(0.0, min(3.0, float(cfg.get('tts_volume', 1.0))))
    cfg['ducking_strength'] = max(0.0, min(1.0, float(cfg.get('ducking_strength', 0.55))))
    cfg['vision_interval_seconds'] = max(20, min(600, int(cfg.get('vision_interval_seconds', 45))))
    with connect() as con:
        con.execute(
            'INSERT INTO ai_settings(id,config_json,updated_at) VALUES(1,?,?) '
            'ON CONFLICT(id) DO UPDATE SET config_json=excluded.config_json,updated_at=excluded.updated_at',
            (json.dumps(cfg, ensure_ascii=False), now_iso()),
        )
    return cfg


def list_personas():
    with connect() as con:
        rows = con.execute('SELECT * FROM ai_personas ORDER BY builtin DESC,name').fetchall()
    out = []
    for row in rows:
        d = dict(row); d['style'] = _loads(d.pop('style_json', '{}'), {}); out.append(d)
    return out


def get_persona(pid):
    with connect() as con:
        row = con.execute('SELECT * FROM ai_personas WHERE id=?', (pid,)).fetchone()
    if not row: return None
    d = dict(row); d['style'] = _loads(d.pop('style_json', '{}'), {}); return d


def save_persona(data):
    pid = data.get('id') or uuid.uuid4().hex[:12]; ts = now_iso(); style = data.get('style') or {}
    with connect() as con:
        old = con.execute('SELECT created_at,builtin FROM ai_personas WHERE id=?', (pid,)).fetchone()
        created = old['created_at'] if old else ts; builtin = old['builtin'] if old else 0
        con.execute(
            'INSERT INTO ai_personas(id,name,description,system_prompt,style_json,builtin,enabled,created_at,updated_at) '
            'VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,description=excluded.description,'
            'system_prompt=excluded.system_prompt,style_json=excluded.style_json,enabled=excluded.enabled,updated_at=excluded.updated_at',
            (pid, str(data.get('name') or 'Persona').strip()[:120], str(data.get('description') or '').strip()[:500],
             str(data.get('system_prompt') or '').strip()[:12000], json.dumps(style, ensure_ascii=False), builtin,
             int(bool(data.get('enabled', True))), created, ts),
        )
    return pid


def list_providers(kind=None, mask=True):
    with connect() as con:
        rows = con.execute('SELECT * FROM ai_providers WHERE kind=? ORDER BY enabled DESC,name', (kind,)).fetchall() if kind else con.execute('SELECT * FROM ai_providers ORDER BY kind,enabled DESC,name').fetchall()
    out = []
    for row in rows:
        d = dict(row); cfg = _loads(decrypt_secret(d.pop('config_enc', '')), {})
        if mask:
            for k in list(cfg):
                if any(s in k.lower() for s in ('key','token','secret','password')) and cfg[k]: cfg[k] = '••••••'
        d['config'] = cfg; out.append(d)
    return out


def get_provider(pid):
    with connect() as con:
        row = con.execute('SELECT * FROM ai_providers WHERE id=?', (pid,)).fetchone()
    if not row: return None
    d = dict(row); d['config'] = _loads(decrypt_secret(d.pop('config_enc', '')), {}); return d


def save_provider(data):
    pid = data.get('id') or uuid.uuid4().hex[:12]; kind = str(data.get('kind') or 'llm')
    if kind not in {'llm','tts'}: raise ValueError('Tipo de provider inválido.')
    ts = now_iso(); config = dict(data.get('config') or {})
    with connect() as con:
        old = con.execute('SELECT config_enc,created_at FROM ai_providers WHERE id=?', (pid,)).fetchone()
        if old:
            prev = _loads(decrypt_secret(old['config_enc']), {})
            for k,v in list(config.items()):
                if v in ('',None,'••••••'): config[k] = prev.get(k,'')
            created = old['created_at']
        else: created = ts
        con.execute(
            'INSERT INTO ai_providers(id,kind,name,provider,config_enc,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?) '
            'ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,name=excluded.name,provider=excluded.provider,config_enc=excluded.config_enc,enabled=excluded.enabled,updated_at=excluded.updated_at',
            (pid, kind, str(data.get('name') or data.get('provider') or 'Provider')[:120], str(data.get('provider') or '').strip()[:80],
             encrypt_secret(json.dumps(config, ensure_ascii=False)), int(bool(data.get('enabled', True))), created, ts),
        )
    return pid


def delete_provider(pid):
    with connect() as con: con.execute('DELETE FROM ai_providers WHERE id=?', (pid,))


def save_binding(integration_id, channel_id, enabled=True, read_chat=True, write_chat=True, read_events=True):
    with connect() as con:
        con.execute(
            'INSERT INTO ai_bindings(integration_id,channel_id,enabled,read_chat,write_chat,read_events,updated_at) VALUES(?,?,?,?,?,?,?) '
            'ON CONFLICT(integration_id) DO UPDATE SET channel_id=excluded.channel_id,enabled=excluded.enabled,read_chat=excluded.read_chat,write_chat=excluded.write_chat,read_events=excluded.read_events,updated_at=excluded.updated_at',
            (integration_id, channel_id or '', int(bool(enabled)), int(bool(read_chat)), int(bool(write_chat)), int(bool(read_events)), now_iso()),
        )


def list_bindings():
    with connect() as con: return [dict(r) for r in con.execute('SELECT * FROM ai_bindings ORDER BY integration_id').fetchall()]


def get_binding(integration_id):
    with connect() as con: row = con.execute('SELECT * FROM ai_bindings WHERE integration_id=?', (integration_id,)).fetchone()
    return dict(row) if row else None


def set_binding_status(integration_id, status='', error=''):
    with connect() as con: con.execute('UPDATE ai_bindings SET last_status=?,last_error=?,updated_at=? WHERE integration_id=?', (status[:500], error[-1000:], now_iso(), integration_id))


def _hash_text(text): return hashlib.sha256(str(text or '').strip().casefold().encode('utf-8')).hexdigest()


def was_recently_sent(platform, integration_id, text, seconds=180):
    cutoff = (datetime.now(timezone.utc)-timedelta(seconds=max(1,int(seconds)))).isoformat()
    with connect() as con:
        row = con.execute('SELECT 1 FROM ai_sent_messages WHERE platform=? AND integration_id=? AND text_hash=? AND sent_at>=? LIMIT 1', (platform,integration_id or '',_hash_text(text),cutoff)).fetchone()
    return bool(row)


def record_sent(platform, integration_id, text, external_id=''):
    sid = uuid.uuid4().hex[:12]
    with connect() as con:
        con.execute('INSERT INTO ai_sent_messages(id,platform,integration_id,external_id,text_hash,sent_at) VALUES(?,?,?,?,?,?)', (sid,platform,integration_id or '',external_id or '',_hash_text(text),now_iso()))
        con.execute("DELETE FROM ai_sent_messages WHERE sent_at < datetime('now','-2 days')")
    return sid


def ingest_message(platform, integration_id, external_id, channel_id, user_id, username, text, kind='chat', metadata=None, self_message=False):
    text = str(text or '').strip()
    if not text and kind == 'chat': return None
    external_id = str(external_id or uuid.uuid4().hex); mid = uuid.uuid4().hex[:16]
    self_flag = bool(self_message) or (bool(text) and was_recently_sent(platform,integration_id,text))
    with connect() as con:
        try:
            con.execute(
                'INSERT INTO ai_chat_messages(id,platform,integration_id,external_id,channel_id,user_id,username,display_name,text,kind,metadata_json,received_at,self_message) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (mid,platform,integration_id or '',external_id,channel_id or '',str(user_id or ''),str(username or '')[:120],str((metadata or {}).get('display_name') or username or '')[:120],text[:2000],kind[:80],json.dumps(metadata or {},ensure_ascii=False),now_iso(),int(self_flag)),
            )
        except Exception: return None
    touch_viewer(platform,str(user_id or username or ''),username); return mid


def get_message(mid):
    with connect() as con: row = con.execute('SELECT * FROM ai_chat_messages WHERE id=?',(mid,)).fetchone()
    if not row: return None
    d=dict(row);d['metadata']=_loads(d.pop('metadata_json','{}'),{});return d


def recent_messages(limit=100, channel_id=None):
    with connect() as con:
        rows = con.execute('SELECT * FROM ai_chat_messages WHERE channel_id=? ORDER BY received_at DESC LIMIT ?',(channel_id,int(limit))).fetchall() if channel_id else con.execute('SELECT * FROM ai_chat_messages ORDER BY received_at DESC LIMIT ?',(int(limit),)).fetchall()
    out=[]
    for row in rows:
        d=dict(row);d['metadata']=_loads(d.pop('metadata_json','{}'),{});out.append(d)
    return out


def pending_messages(limit=200):
    with connect() as con: rows=con.execute("SELECT * FROM ai_chat_messages WHERE processed_at='' AND self_message=0 ORDER BY received_at LIMIT ?",(int(limit),)).fetchall()
    out=[]
    for row in rows:
        d=dict(row);d['metadata']=_loads(d.pop('metadata_json','{}'),{});out.append(d)
    return out


def mark_message(mid, selected=False, score=None):
    with connect() as con:
        if score is None: con.execute('UPDATE ai_chat_messages SET processed_at=?,selected=? WHERE id=?',(now_iso(),int(bool(selected)),mid))
        else: con.execute('UPDATE ai_chat_messages SET processed_at=?,selected=?,score=? WHERE id=?',(now_iso(),int(bool(selected)),float(score),mid))


def create_response(message, reply_text, voice_text='', mode='copilot', provider_id='', score=0, reason=''):
    rid=uuid.uuid4().hex[:16]
    with connect() as con:
        con.execute('INSERT INTO ai_chat_responses(id,message_id,platform,integration_id,channel_id,user_id,username,reply_text,voice_text,mode,status,provider_id,score,reason,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (rid,message.get('id',''),message.get('platform',''),message.get('integration_id',''),message.get('channel_id',''),message.get('user_id',''),message.get('username',''),str(reply_text or '')[:1000],str(voice_text or '')[:1000],mode,'pending' if mode=='copilot' else 'queued',provider_id or '',float(score or 0),str(reason or '')[:1000],now_iso()))
    return rid


def update_response(rid, **values):
    allowed={'status','reply_text','voice_text','sent_at','error','tts_status'};fields=[];args=[]
    for k,v in values.items():
        if k in allowed and v is not None: fields.append(k+'=?');args.append(v)
    if not fields:return
    args.append(rid)
    with connect() as con: con.execute('UPDATE ai_chat_responses SET '+','.join(fields)+' WHERE id=?',args)


def get_response(rid):
    with connect() as con: row=con.execute('SELECT * FROM ai_chat_responses WHERE id=?',(rid,)).fetchone()
    return dict(row) if row else None


def list_responses(limit=200,status=None):
    with connect() as con:
        rows=con.execute('SELECT * FROM ai_chat_responses WHERE status=? ORDER BY created_at DESC LIMIT ?',(status,int(limit))).fetchall() if status else con.execute('SELECT * FROM ai_chat_responses ORDER BY created_at DESC LIMIT ?',(int(limit),)).fetchall()
    return [dict(r) for r in rows]


def touch_viewer(platform,user_id,username=''):
    if not user_id:return
    ts=now_iso()
    with connect() as con:
        row=con.execute('SELECT interactions FROM ai_viewer_memory WHERE platform=? AND user_id=?',(platform,user_id)).fetchone()
        if row: con.execute('UPDATE ai_viewer_memory SET username=?,interactions=interactions+1,last_seen_at=?,updated_at=? WHERE platform=? AND user_id=?',(username or '',ts,ts,platform,user_id))
        else: con.execute('INSERT INTO ai_viewer_memory(platform,user_id,username,facts_json,interactions,last_seen_at,updated_at) VALUES(?,?,?,?,1,?,?)',(platform,user_id,username or '','[]',ts,ts))


def get_viewer(platform,user_id):
    with connect() as con: row=con.execute('SELECT * FROM ai_viewer_memory WHERE platform=? AND user_id=?',(platform,user_id)).fetchone()
    if not row:return None
    d=dict(row);d['facts']=_loads(d.pop('facts_json','[]'),[]);return d


def update_viewer_facts(platform,user_id,username,facts,replied=False):
    if not user_id:return
    current=get_viewer(platform,user_id) or {'facts':[]};merged=[];seen=set()
    for fact in list(current.get('facts') or [])+list(facts or []):
        fact=str(fact or '').strip()[:220];key=fact.casefold()
        if fact and key not in seen:seen.add(key);merged.append(fact)
    merged=merged[-12:];ts=now_iso()
    with connect() as con:
        con.execute('INSERT INTO ai_viewer_memory(platform,user_id,username,facts_json,interactions,last_seen_at,last_replied_at,updated_at) VALUES(?,?,?,?,1,?,?,?) ON CONFLICT(platform,user_id) DO UPDATE SET username=excluded.username,facts_json=excluded.facts_json,last_replied_at=CASE WHEN ? THEN excluded.last_replied_at ELSE ai_viewer_memory.last_replied_at END,updated_at=excluded.updated_at',
                    (platform,user_id,username or '',json.dumps(merged,ensure_ascii=False),ts,ts if replied else '',ts,int(bool(replied))))


def list_viewers(limit=300):
    with connect() as con: rows=con.execute('SELECT * FROM ai_viewer_memory ORDER BY last_seen_at DESC LIMIT ?',(int(limit),)).fetchall()
    out=[]
    for row in rows:
        d=dict(row);d['facts']=_loads(d.pop('facts_json','[]'),[]);out.append(d)
    return out


def set_live_memory(channel_id,key,value,ttl_seconds=0):
    expires=''
    if ttl_seconds:expires=(datetime.now(timezone.utc)+timedelta(seconds=int(ttl_seconds))).isoformat()
    with connect() as con: con.execute('INSERT INTO ai_live_memory(channel_id,key,value_json,expires_at,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(channel_id,key) DO UPDATE SET value_json=excluded.value_json,expires_at=excluded.expires_at,updated_at=excluded.updated_at',(channel_id,key,json.dumps(value,ensure_ascii=False),expires,now_iso()))


def get_live_memory(channel_id):
    now=now_iso()
    with connect() as con:
        con.execute("DELETE FROM ai_live_memory WHERE expires_at!='' AND expires_at<?",(now,));rows=con.execute('SELECT key,value_json FROM ai_live_memory WHERE channel_id=?',(channel_id,)).fetchall()
    return {r['key']:_loads(r['value_json'],{}) for r in rows}


def upsert_event_subscription(provider,integration_id,event_name,remote_id='',status='',error=''):
    with connect() as con: con.execute('INSERT INTO ai_event_subscriptions(provider,integration_id,event_name,remote_id,status,last_error,updated_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(provider,integration_id,event_name) DO UPDATE SET remote_id=excluded.remote_id,status=excluded.status,last_error=excluded.last_error,updated_at=excluded.updated_at',(provider,integration_id,event_name,remote_id or '',status or '',str(error or '')[-1000:],now_iso()))


def list_event_subscriptions():
    with connect() as con:return [dict(r) for r in con.execute('SELECT * FROM ai_event_subscriptions ORDER BY provider,integration_id,event_name').fetchall()]


def create_tts_job(response_id,channel_id,text):
    jid=uuid.uuid4().hex[:16]
    with connect() as con:con.execute('INSERT INTO ai_tts_jobs(id,response_id,channel_id,text,status,created_at) VALUES(?,?,?,?,?,?)',(jid,response_id or '',channel_id or '',str(text or '')[:1000],'queued',now_iso()))
    return jid


def update_tts_job(jid,**values):
    allowed={'status','path','duration_seconds','played_at','error'};fields=[];args=[]
    for k,v in values.items():
        if k in allowed:fields.append(k+'=?');args.append(v)
    if fields:
        args.append(jid)
        with connect() as con:con.execute('UPDATE ai_tts_jobs SET '+','.join(fields)+' WHERE id=?',args)


def hourly_sent_count():
    cutoff=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()
    with connect() as con:row=con.execute("SELECT COUNT(*) n FROM ai_chat_responses WHERE sent_at>=? AND status='sent'",(cutoff,)).fetchone()
    return int(row['n'] if row else 0)


def last_sent_at():
    with connect() as con:row=con.execute("SELECT sent_at FROM ai_chat_responses WHERE status='sent' AND sent_at!='' ORDER BY sent_at DESC LIMIT 1").fetchone()
    return row['sent_at'] if row else ''


def stats():
    with connect() as con:
        received=con.execute('SELECT COUNT(*) n FROM ai_chat_messages').fetchone()['n'];selected=con.execute('SELECT COUNT(*) n FROM ai_chat_messages WHERE selected=1').fetchone()['n'];sent=con.execute("SELECT COUNT(*) n FROM ai_chat_responses WHERE status='sent'").fetchone()['n'];pending=con.execute("SELECT COUNT(*) n FROM ai_chat_responses WHERE status IN ('pending','queued')").fetchone()['n'];viewers=con.execute('SELECT COUNT(*) n FROM ai_viewer_memory').fetchone()['n']
    return {'received':int(received),'selected':int(selected),'sent':int(sent),'pending':int(pending),'viewers':int(viewers)}


def cleanup_ai(retention_days=30):
    days=max(1,int(retention_days))
    with connect() as con:
        con.execute(f"DELETE FROM ai_chat_messages WHERE received_at < datetime('now','-{days} days')")
        con.execute(f"DELETE FROM ai_chat_responses WHERE created_at < datetime('now','-{days} days')")
        con.execute("DELETE FROM ai_sent_messages WHERE sent_at < datetime('now','-2 days')")
