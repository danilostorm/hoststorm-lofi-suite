from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MethodType

from .config import VIDEOS_DIR
from .media import probe_duration
from .pro_db import connect
from .utils import now_dt, now_iso, safe_filename


RECOVERY_CHECKPOINT_SECONDS = max(2, min(60, int(os.environ.get('HOSTSTORM_RECOVERY_CHECKPOINT_SECONDS', '5') or 5)))
RECOVERY_MAX_AGE_HOURS = max(1, min(168, int(os.environ.get('HOSTSTORM_RECOVERY_MAX_AGE_HOURS', '24') or 24)))


def ensure_recovery_table():
    """Create/migrate the persistent state used to resume interrupted lives."""
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
        existing = {r['name'] for r in con.execute('PRAGMA table_info(playback_recovery_state)').fetchall()}
        additions = {
            'trigger': "TEXT NOT NULL DEFAULT 'manual'",
            'schedule_id': "TEXT NOT NULL DEFAULT ''",
            'platforms_json': "TEXT NOT NULL DEFAULT '[]'",
            'media_json': "TEXT NOT NULL DEFAULT '[]'",
            'schedule_json': "TEXT NOT NULL DEFAULT '{}'",
            'elapsed_seconds': 'REAL NOT NULL DEFAULT 0',
            'max_duration_seconds': 'REAL NOT NULL DEFAULT 0',
            'stop_at': "TEXT NOT NULL DEFAULT ''",
            'started_at': "TEXT NOT NULL DEFAULT ''",
            'recovery_count': 'INTEGER NOT NULL DEFAULT 0',
            'last_reason': "TEXT NOT NULL DEFAULT ''",
            'resume_enabled': 'INTEGER NOT NULL DEFAULT 1',
        }
        for name, sql in additions.items():
            if name not in existing:
                con.execute(f'ALTER TABLE playback_recovery_state ADD COLUMN {name} {sql}')
        con.execute('CREATE INDEX IF NOT EXISTS idx_playback_recovery_channel ON playback_recovery_state(channel_id,updated_at DESC)')


def _state_id(channel_id: str) -> str:
    return 'channel:' + str(channel_id or '').strip()


def _json_load(value, default):
    try:
        parsed = json.loads(value or '')
        return parsed if isinstance(parsed, type(default)) else default
    except Exception:
        return default


def _row_dict(row):
    if not row:
        return None
    data = dict(row)
    data['platforms'] = _json_load(data.pop('platforms_json', '[]'), [])
    data['media'] = _json_load(data.pop('media_json', '[]'), [])
    data['schedule'] = _json_load(data.pop('schedule_json', '{}'), {})
    return data


def save_checkpoint(
    channel_id,
    source,
    position_seconds,
    source_type='url',
    live_run_id='',
    duration_seconds=0,
    quality='auto',
    *,
    trigger='manual',
    schedule_id='',
    platforms=None,
    media=None,
    schedule=None,
    elapsed_seconds=None,
    max_duration_seconds=0,
    stop_at='',
    started_at='',
    recovery_count=0,
    last_reason='',
    status='running',
    resume_enabled=True,
):
    ensure_recovery_table()
    position = max(0.0, float(position_seconds or 0))
    elapsed = position if elapsed_seconds is None else max(0.0, float(elapsed_seconds or 0))
    with connect() as con:
        con.execute('''
        INSERT INTO playback_recovery_state(
            id,channel_id,live_run_id,source_type,source,position_seconds,duration_seconds,quality,status,updated_at,
            trigger,schedule_id,platforms_json,media_json,schedule_json,elapsed_seconds,max_duration_seconds,
            stop_at,started_at,recovery_count,last_reason,resume_enabled
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            channel_id=excluded.channel_id,live_run_id=excluded.live_run_id,source_type=excluded.source_type,
            source=excluded.source,position_seconds=excluded.position_seconds,duration_seconds=excluded.duration_seconds,
            quality=excluded.quality,status=excluded.status,updated_at=excluded.updated_at,trigger=excluded.trigger,
            schedule_id=excluded.schedule_id,platforms_json=excluded.platforms_json,media_json=excluded.media_json,
            schedule_json=excluded.schedule_json,elapsed_seconds=excluded.elapsed_seconds,
            max_duration_seconds=excluded.max_duration_seconds,stop_at=excluded.stop_at,started_at=excluded.started_at,
            recovery_count=excluded.recovery_count,last_reason=excluded.last_reason,resume_enabled=excluded.resume_enabled
        ''', (
            _state_id(channel_id), str(channel_id), str(live_run_id or ''), str(source_type or ''), str(source or ''),
            position, max(0.0, float(duration_seconds or 0)), str(quality or 'auto'), str(status or 'running'), now_iso(),
            str(trigger or 'manual'), str(schedule_id or ''), json.dumps(list(platforms or []), ensure_ascii=False),
            json.dumps(list(media or []), ensure_ascii=False), json.dumps(dict(schedule or {}), ensure_ascii=False), elapsed,
            max(0.0, float(max_duration_seconds or 0)), str(stop_at or ''), str(started_at or ''),
            max(0, int(recovery_count or 0)), str(last_reason or ''), int(bool(resume_enabled)),
        ))


def get_checkpoint(channel_id, live_run_id=''):
    ensure_recovery_table()
    with connect() as con:
        if live_run_id:
            row = con.execute(
                'SELECT * FROM playback_recovery_state WHERE channel_id=? AND live_run_id=? ORDER BY updated_at DESC LIMIT 1',
                (channel_id, live_run_id),
            ).fetchone()
        else:
            row = con.execute('SELECT * FROM playback_recovery_state WHERE id=?', (_state_id(channel_id),)).fetchone()
            if not row:
                row = con.execute(
                    'SELECT * FROM playback_recovery_state WHERE channel_id=? ORDER BY updated_at DESC LIMIT 1', (channel_id,)
                ).fetchone()
    return _row_dict(row)


def list_resumable():
    ensure_recovery_table()
    with connect() as con:
        rows = con.execute(
            "SELECT * FROM playback_recovery_state WHERE resume_enabled=1 AND status IN ('running','reconnecting') ORDER BY updated_at DESC"
        ).fetchall()
    result = []
    seen = set()
    for row in rows:
        data = _row_dict(row)
        if not data or data['channel_id'] in seen:
            continue
        seen.add(data['channel_id'])
        result.append(data)
    return result


def mark_state(channel_id, status, reason='', resume_enabled=None):
    ensure_recovery_table()
    with connect() as con:
        fields = ['status=?', 'last_reason=?', 'updated_at=?']
        values = [str(status), str(reason or ''), now_iso()]
        if resume_enabled is not None:
            fields.append('resume_enabled=?')
            values.append(int(bool(resume_enabled)))
        values.append(_state_id(channel_id))
        con.execute(f"UPDATE playback_recovery_state SET {','.join(fields)} WHERE id=?", values)


def _parse_iso(value):
    try:
        dt = datetime.fromisoformat(str(value or '').replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _is_stale(state) -> bool:
    updated = _parse_iso(state.get('updated_at'))
    if not updated:
        return True
    now = datetime.now(updated.tzinfo or timezone.utc)
    return (now - updated).total_seconds() > RECOVERY_MAX_AGE_HOURS * 3600


def _session_source(session):
    ch = session.work_channel or {}
    scheduled_url = str(ch.get('_schedule_source_url') or '').strip()
    if scheduled_url:
        return 'url', scheduled_url
    if session.trigger == 'scheduled' and session.media:
        return 'library', str(session.media[0])
    source_mode = str(ch.get('source_mode') or 'local')
    if source_mode == 'url':
        return 'url', str(ch.get('source_url') or '')
    name = safe_filename(ch.get('video') or (session.media[0] if session.media else ''))
    return 'local', name


def _cycle_duration(session) -> float:
    ch = session.work_channel or {}
    try:
        if session.trigger == 'scheduled' and session.media:
            total = sum(max(0.0, float(probe_duration(name))) for name in session.media)
            return total
        if str(ch.get('source_mode') or 'local') != 'url':
            name = safe_filename(ch.get('video') or (session.media[0] if session.media else ''))
            if name and (VIDEOS_DIR / name).exists():
                return max(0.0, float(probe_duration(name)))
    except Exception:
        pass
    return 0.0


def _is_looping(session) -> bool:
    ch = session.work_channel or {}
    if session.trigger == 'scheduled':
        return bool(ch.get('_repeat_playlist'))
    return str(ch.get('source_mode') or 'local') != 'url'


def _seek_for_session(session, elapsed_seconds: float) -> float:
    elapsed = max(0.0, float(elapsed_seconds or 0))
    if not _is_looping(session):
        return elapsed
    duration = _cycle_duration(session)
    return elapsed % duration if duration > 0 else elapsed


def _decorate_input_args(args, seek_seconds=0.0, source_input_count=1):
    """Add seek and HTTP reconnect options only to the media source inputs."""
    result = []
    input_index = 0
    i = 0
    while i < len(args):
        token = args[i]
        if token == '-i' and i + 1 < len(args):
            value = str(args[i + 1])
            is_source = input_index < max(1, int(source_input_count or 1))
            if is_source and value.startswith(('http://', 'https://')):
                result += ['-reconnect', '1', '-reconnect_streamed', '1', '-reconnect_delay_max', '15']
            if is_source and float(seek_seconds or 0) > 0.5:
                result += ['-ss', f'{float(seek_seconds):.3f}']
            result += [token, args[i + 1]]
            input_index += 1
            i += 2
            continue
        result.append(token)
        i += 1
    return result


def _current_elapsed(session, fallback=0.0) -> float:
    positions = []
    now_mono = time.monotonic()
    for ps in (session.platform_states or {}).values():
        proc = getattr(ps, 'process', None)
        if not proc or proc.poll() is not None:
            continue
        started = float(getattr(ps, '_hs_started_monotonic', 0) or 0)
        base = float(getattr(ps, '_hs_base_position', 0) or 0)
        if started > 0:
            positions.append(base + max(0.0, now_mono - started))
    return max(positions) if positions else max(0.0, float(fallback or 0))


def _remaining_seconds(state):
    total = max(0.0, float(state.get('max_duration_seconds') or 0))
    elapsed = max(0.0, float(state.get('elapsed_seconds') or state.get('position_seconds') or 0))
    if total <= 0:
        return 0.0
    return max(0.0, total - elapsed)


def install_recovery_engine(manager, streaming_module, db_module):
    """Install persistent checkpoints and source seeking around the final StreamManager wrappers."""
    ensure_recovery_table()
    original_input = manager._input_args
    original_start_platform = manager._start_platform
    original_start = manager.start
    original_stop = manager.stop
    original_status = manager.channel_status
    original_start_threads = manager.start_threads

    manager._hs_resume_requests = {}
    manager._hs_recovery_workers_started = False

    def input_args(self, session, vertical=False):
        args = list(original_input(session, vertical))
        seek = max(0.0, float(getattr(session, '_hs_active_seek_seconds', 0) or 0))
        remote_count = int(getattr(session, '_hs_remote_input_count', 0) or 0)
        source_inputs = remote_count if remote_count > 0 else 1
        return _decorate_input_args(args, seek, source_inputs)

    def start_platform(self, session, slug, recovery=False):
        cid = session.channel_id
        state = get_checkpoint(cid)
        requested = self._hs_resume_requests.get(cid)
        elapsed = 0.0
        if recovery and state and state.get('resume_enabled'):
            elapsed = max(0.0, float(state.get('elapsed_seconds') or state.get('position_seconds') or 0))
            # Signed YouTube/DASH URLs may have expired while the platform was down.
            try:
                session.work_channel.pop('_hs_remote_inputs', None)
            except Exception:
                pass
        elif requested:
            elapsed = max(0.0, float(requested.get('elapsed_seconds') or requested.get('position_seconds') or 0))

        previous_seek = getattr(session, '_hs_active_seek_seconds', 0.0)
        session._hs_active_seek_seconds = _seek_for_session(session, elapsed)
        try:
            ok = original_start_platform(session, slug, recovery)
        finally:
            session._hs_active_seek_seconds = previous_seek

        if ok:
            ps = session.platform_states.get(slug)
            if ps:
                ps._hs_base_position = elapsed
                ps._hs_started_monotonic = time.monotonic()
            if recovery:
                session._hs_recovery_count = max(
                    int(getattr(session, '_hs_recovery_count', 0) or 0) + 1,
                    int((state or {}).get('recovery_count') or 0) + 1,
                )
                try:
                    streaming_module.audit(
                        'info', 'live_recovered', cid,
                        f'Plataforma {slug} retomada em {int(elapsed)}s.',
                        {'platform': slug, 'position_seconds': elapsed, 'attempt': getattr(ps, 'retries', 0) if ps else 0},
                    )
                except Exception:
                    pass
        return ok

    def start(self, *args, **kwargs):
        cid = str(args[0] if args else kwargs.get('cid', '') or '')
        schedule = kwargs.get('schedule')
        if schedule is None and len(args) >= 5:
            schedule = args[4]
        schedule_snapshot = dict(schedule or {})
        requested = self._hs_resume_requests.get(cid)
        try:
            ok, msg = original_start(*args, **kwargs)
        except Exception:
            if requested:
                mark_state(cid, 'reconnecting', 'Falha ao reconstruir a live após reinício.', True)
            raise

        if not ok:
            if requested:
                mark_state(cid, 'reconnecting', str(msg), True)
            return ok, msg

        with self.lock:
            session = self.sessions.get(cid)
            if not session:
                return ok, msg
            session._hs_schedule_snapshot = schedule_snapshot or dict((requested or {}).get('schedule') or {})
            session._hs_recovery_count = int((requested or {}).get('recovery_count') or 0)
            original_limit = max(0.0, float((requested or {}).get('max_duration_seconds') or session.max_duration_seconds or 0))
            session._hs_total_duration_limit = original_limit
            if requested:
                elapsed = max(0.0, float(requested.get('elapsed_seconds') or requested.get('position_seconds') or 0))
                remaining = _remaining_seconds(requested)
                session._hs_resume_elapsed = elapsed
                if original_limit > 0:
                    session.max_duration_seconds = remaining
                    session.stop_at = (now_dt() + timedelta(seconds=remaining)).isoformat() if remaining > 0 else now_dt().isoformat()
                try:
                    self.log(cid, f'Live retomada do checkpoint em {int(elapsed)}s; restante {int(remaining)}s.' if original_limit > 0 else f'Live retomada do checkpoint em {int(elapsed)}s.')
                except Exception:
                    pass
                try:
                    streaming_module.audit(
                        'info', 'live_resume', cid, f'Live retomada em {int(elapsed)}s após interrupção.',
                        {'position_seconds': elapsed, 'remaining_seconds': remaining, 'source': requested.get('source', '')},
                    )
                except Exception:
                    pass
        return ok, msg

    def stop(self, cid, *args, **kwargs):
        reason = args[0] if args else kwargs.get('reason', 'manual')
        try:
            return original_stop(cid, *args, **kwargs)
        finally:
            mark_state(cid, 'stopped', str(reason or 'manual'), False)

    def status(self, cid):
        data = original_status(cid)
        state = get_checkpoint(cid)
        if state:
            data['recovery'] = {
                'status': state.get('status', ''),
                'position_seconds': round(float(state.get('position_seconds') or 0), 1),
                'elapsed_seconds': round(float(state.get('elapsed_seconds') or 0), 1),
                'quality': state.get('quality', 'auto'),
                'recovery_count': int(state.get('recovery_count') or 0),
                'last_reason': state.get('last_reason', ''),
                'updated_at': state.get('updated_at', ''),
            }
        return data

    def _save_session(self, session, status_name='running', reason=''):
        previous = get_checkpoint(session.channel_id) or {}
        elapsed = _current_elapsed(session, previous.get('elapsed_seconds') or previous.get('position_seconds') or 0)
        source_type, source = _session_source(session)
        cycle_duration = _cycle_duration(session)
        total_limit = max(0.0, float(getattr(session, '_hs_total_duration_limit', session.max_duration_seconds) or 0))
        quality = str((session.work_channel or {}).get('_hs_source_quality') or (session.work_channel or {}).get('resolution') or 'auto')
        save_checkpoint(
            session.channel_id, source, elapsed, source_type, session.run_id, cycle_duration, quality,
            trigger=session.trigger, schedule_id=session.schedule_id or '', platforms=session.platforms, media=session.media,
            schedule=getattr(session, '_hs_schedule_snapshot', {}) or {}, elapsed_seconds=elapsed,
            max_duration_seconds=total_limit, stop_at=session.stop_at, started_at=session.started_at,
            recovery_count=int(getattr(session, '_hs_recovery_count', previous.get('recovery_count', 0)) or 0),
            last_reason=reason or previous.get('last_reason', ''), status=status_name, resume_enabled=True,
        )

    def checkpoint_loop(self):
        while True:
            try:
                with self.lock:
                    sessions = list(self.sessions.values())
                for session in sessions:
                    if session.stop_requested or not session.desired_running:
                        continue
                    alive = any(
                        getattr(ps, 'process', None) is not None and ps.process.poll() is None
                        for ps in (session.platform_states or {}).values()
                    )
                    if alive:
                        _save_session(self, session, 'running')
                    else:
                        state = get_checkpoint(session.channel_id)
                        if state and state.get('resume_enabled'):
                            mark_state(session.channel_id, 'reconnecting', state.get('last_reason') or 'Todos os encoders estão reconectando.', True)
            except Exception as exc:
                try:
                    streaming_module.audit('error', 'recovery_checkpoint_error', '', str(exc))
                except Exception:
                    pass
            time.sleep(RECOVERY_CHECKPOINT_SECONDS)

    def boot_resume_loop(self):
        # Run before the legacy 24/7 resumer (7s) so a persisted offset is not lost.
        time.sleep(3)
        for state in list_resumable():
            cid = str(state.get('channel_id') or '')
            if not cid:
                continue
            try:
                if _is_stale(state):
                    mark_state(cid, 'expired', 'Checkpoint antigo demais para retomada automática.', False)
                    continue
                if self.channel_status(cid).get('running'):
                    continue
                total = max(0.0, float(state.get('max_duration_seconds') or 0))
                remaining = _remaining_seconds(state)
                if total > 0 and remaining <= 3:
                    mark_state(cid, 'finished', 'Checkpoint já estava no fim da programação.', False)
                    continue

                trigger = str(state.get('trigger') or 'manual')
                platforms = list(state.get('platforms') or [])
                media = list(state.get('media') or [])
                schedule = dict(state.get('schedule') or {})
                if trigger == 'scheduled':
                    sid = str(state.get('schedule_id') or '')
                    if not schedule and sid and not sid.startswith('grid-'):
                        try:
                            schedule = db_module.get_schedule(sid) or {}
                        except Exception:
                            schedule = {}
                    if not schedule:
                        mark_state(cid, 'error', 'Não foi possível reconstruir o agendamento interrompido.', False)
                        continue
                    if media and not schedule.get('media'):
                        schedule['media'] = list(media)
                    if platforms and not schedule.get('platforms'):
                        schedule['platforms'] = list(platforms)

                self._hs_resume_requests[cid] = state
                mark_state(cid, 'reconnecting', 'Retomando após reinício do serviço/servidor.', True)
                try:
                    if trigger == 'scheduled':
                        ok, msg = self.start(cid, platforms=platforms, media=media, trigger='scheduled', schedule=schedule)
                    else:
                        ok, msg = self.start(cid, platforms=platforms, trigger='manual')
                finally:
                    self._hs_resume_requests.pop(cid, None)
                if ok:
                    try:
                        streaming_module.notify(f'✅ HostStorm: live de {cid} retomada automaticamente do último ponto salvo.')
                    except Exception:
                        pass
                else:
                    mark_state(cid, 'reconnecting', str(msg), True)
            except Exception as exc:
                self._hs_resume_requests.pop(cid, None)
                mark_state(cid, 'reconnecting', str(exc), True)
                try:
                    streaming_module.audit('error', 'recovery_boot_error', cid, str(exc))
                except Exception:
                    pass

    def start_threads(self):
        original_start_threads()
        if self._hs_recovery_workers_started:
            return
        self._hs_recovery_workers_started = True
        threading.Thread(target=checkpoint_loop, args=(self,), daemon=True, name='live-recovery-checkpoint').start()
        threading.Thread(target=boot_resume_loop, args=(self,), daemon=True, name='live-recovery-boot').start()

    manager._input_args = MethodType(input_args, manager)
    manager._start_platform = MethodType(start_platform, manager)
    manager.start = MethodType(start, manager)
    manager.stop = MethodType(stop, manager)
    manager.channel_status = MethodType(status, manager)
    manager.start_threads = MethodType(start_threads, manager)
    return manager
