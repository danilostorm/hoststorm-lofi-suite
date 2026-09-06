from __future__ import annotations

import json
import uuid
from datetime import datetime

from .pro_db import connect
from .utils import now_iso


def ensure_recovery_table():
    with connect() as con:
        con.execute('''
        CREATE TABLE IF NOT EXISTS playback_recovery_state (
            id TEXT PRIMARY KEY,
            channel_id TEXT NOT NULL,
            live_run_id TEXT NOT NULL DEFAULT '',
            source_type TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            position_seconds REAL NOT NULL DEFAULT 0,
            duration_seconds REAL NOT NULL DEFAULT 0,
            quality TEXT NOT NULL DEFAULT 'auto',
            status TEXT NOT NULL DEFAULT 'idle',
            updated_at TEXT NOT NULL
        )
        ''')


def save_checkpoint(channel_id, source, position_seconds, source_type='url', live_run_id='', duration_seconds=0, quality='auto'):
    ensure_recovery_table()
    with connect() as con:
        con.execute('''
        INSERT INTO playback_recovery_state
        (id,channel_id,live_run_id,source_type,source,position_seconds,duration_seconds,quality,status,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
        position_seconds=excluded.position_seconds,
        duration_seconds=excluded.duration_seconds,
        quality=excluded.quality,
        status=excluded.status,
        updated_at=excluded.updated_at
        ''', (
            f'{channel_id}:{live_run_id}' if live_run_id else uuid.uuid4().hex,
            channel_id,
            live_run_id,
            source_type,
            source,
            float(position_seconds or 0),
            float(duration_seconds or 0),
            quality,
            'checkpoint',
            now_iso()
        ))


def get_checkpoint(channel_id, live_run_id=''):
    ensure_recovery_table()
    with connect() as con:
        row = con.execute(
            'SELECT * FROM playback_recovery_state WHERE channel_id=? AND live_run_id=? ORDER BY updated_at DESC LIMIT 1',
            (channel_id, live_run_id)
        ).fetchone()
    return dict(row) if row else None
