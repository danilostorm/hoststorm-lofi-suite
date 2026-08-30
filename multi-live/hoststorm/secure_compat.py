from __future__ import annotations

import sqlite3

from .config import DB_PATH
from .security import decrypt_secret, encrypt_secret


def _decrypt_channel(ch):
    if not ch: return ch
    for d in (ch.get('destinations') or {}).values():
        d['stream_key']=decrypt_secret(str(d.get('stream_key') or ''))
    return ch


def install_secure_compat(db_module, web_module, streaming_module):
    original_get=db_module.get_channel
    original_list=db_module.list_channels
    original_save=db_module.save_channel

    def get_channel(cid,include_schedules=True):
        return _decrypt_channel(original_get(cid,include_schedules))

    def list_channels(include_schedules=True):
        rows=original_list(include_schedules)
        return {cid:_decrypt_channel(ch) for cid,ch in rows.items()}

    def save_channel(cid,name,settings,destinations):
        secured={}
        for slug,d in (destinations or {}).items():
            secured[slug]=dict(d)
            key=str(secured[slug].get('stream_key') or '')
            if key: secured[slug]['stream_key']=encrypt_secret(key)
        return original_save(cid,name,settings,secured)

    # Encrypta chaves legadas uma única vez. decrypt_secret continua compatível com texto antigo.
    try:
        con=sqlite3.connect(DB_PATH,timeout=30)
        rows=con.execute("SELECT channel_id,slug,stream_key FROM destinations WHERE stream_key<>''").fetchall()
        for cid,slug,key in rows:
            if key and not str(key).startswith('enc:v1:'):
                con.execute('UPDATE destinations SET stream_key=? WHERE channel_id=? AND slug=?',(encrypt_secret(str(key)),cid,slug))
        con.commit(); con.close()
    except Exception:
        pass

    db_module.get_channel=get_channel; db_module.list_channels=list_channels; db_module.save_channel=save_channel
    web_module.get_channel=get_channel; web_module.list_channels=list_channels; web_module.save_channel=save_channel
    streaming_module.get_channel=get_channel
    return get_channel,list_channels,save_channel
