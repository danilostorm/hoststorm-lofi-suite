from __future__ import annotations

import json
import random
import signal
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import MethodType

from .config import SUPERVISOR_INTERVAL_SECONDS
from .utils import build_target, now_dt, now_iso, parse_iso


RETRY_BACKOFF = [5, 15, 30, 60, 60, 60]


def _parse(value):
    try:
        dt = datetime.fromisoformat(str(value or '').replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _alive(ps):
    p = getattr(ps, 'process', None)
    return bool(p and p.poll() is None)


def _source_seek(session, elapsed):
    elapsed = max(0.0, float(elapsed or 0))
    ch = session.work_channel or {}
    if ch.get('_hs_youtube_playlist_active'):
        before = max(0.0, float(ch.get('_hs_youtube_playlist_elapsed_before') or 0))
        return max(0.0, elapsed - before)
    if str(ch.get('_schedule_source_url') or '').strip():
        return elapsed
    if session.media:
        try:
            total = float(getattr(session, '_hs_parallel_cycle_duration', 0) or 0)
            if total > 0 and ch.get('_repeat_playlist'):
                return elapsed % total
        except Exception:
            pass
    return elapsed


def install_parallel_schedules(manager, streaming_module, db_module):
    """Allow independent scheduled destinations of one HostStorm channel to run together.

    The legacy manager stores one primary session per channel. This extension keeps extra
    scheduled runs in a sidecar registry when their target platforms do not overlap the
    already-running platforms. Each sidecar has its own FFmpeg processes, retry loop,
    duration and persistent checkpoint, so a YouTube run no longer blocks a Kick-only
    schedule on the same HostStorm channel.
    """
    original_start = manager.start
    original_stop = manager.stop
    original_status = manager.channel_status
    original_start_threads = manager.start_threads

    manager._hs_parallel_sessions = {}
    manager._hs_parallel_started = False

    with db_module.connect() as con:
        con.execute('''
        CREATE TABLE IF NOT EXISTS parallel_schedule_state (
            run_id TEXT PRIMARY KEY,
            channel_id TEXT NOT NULL,
            schedule_id TEXT NOT NULL DEFAULT '',
            schedule_json TEXT NOT NULL DEFAULT '{}',
            platforms_json TEXT NOT NULL DEFAULT '[]',
            elapsed_seconds REAL NOT NULL DEFAULT 0,
            stop_at TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'running',
            resume_enabled INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL
        )
        ''')
        con.execute('CREATE INDEX IF NOT EXISTS idx_parallel_state_channel ON parallel_schedule_state(channel_id,updated_at DESC)')

    def parallel_sessions_for(self, cid):
        with self.lock:
            return [s for s in self._hs_parallel_sessions.values() if str(s.channel_id) == str(cid)]

    def status(self, cid):
        data = original_status(cid)
        parallel = []
        for sess in parallel_sessions_for(self, cid):
            row = {'run_id': sess.run_id, 'schedule_id': sess.schedule_id or '', 'platforms': {}, 'started_at': sess.started_at, 'stop_at': sess.stop_at}
            for slug, ps in (sess.platform_states or {}).items():
                alive = _alive(ps)
                item = {'running': alive, 'pid': ps.process.pid if alive else 0, 'retries': ps.retries, 'last_error': ps.last_error, 'label': ps.label, 'parallel': True}
                row['platforms'][slug] = item
                # No overlap is allowed, so this can safely merge into the public platform map.
                data.setdefault('platforms', {})[slug] = item
            if any(x.get('running') for x in row['platforms'].values()):
                parallel.append(row)
        if parallel:
            data['running'] = True
            data['parallel_runs'] = parallel
        return data

    def active_platforms(self, cid):
        st = status(self, cid)
        return {slug for slug, item in (st.get('platforms') or {}).items() if item.get('running')}

    def save_parallel_state(self, session, elapsed=None, status_name='running', resume_enabled=True):
        if elapsed is None:
            elapsed = current_elapsed(self, session)
        schedule = dict(getattr(session, '_hs_schedule_snapshot', {}) or {})
        with db_module.connect() as con:
            con.execute('''
            INSERT INTO parallel_schedule_state(run_id,channel_id,schedule_id,schedule_json,platforms_json,elapsed_seconds,stop_at,status,resume_enabled,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(run_id) DO UPDATE SET
              schedule_json=excluded.schedule_json,platforms_json=excluded.platforms_json,elapsed_seconds=excluded.elapsed_seconds,
              stop_at=excluded.stop_at,status=excluded.status,resume_enabled=excluded.resume_enabled,updated_at=excluded.updated_at
            ''', (
                session.run_id, session.channel_id, session.schedule_id or '', json.dumps(schedule, ensure_ascii=False),
                json.dumps(list(session.platforms or []), ensure_ascii=False), max(0.0, float(elapsed or 0)),
                str(session.stop_at or ''), status_name, int(bool(resume_enabled)), now_iso(),
            ))

    def current_elapsed(self, session):
        values = []
        now_mono = time.monotonic()
        for ps in (session.platform_states or {}).values():
            if not _alive(ps):
                continue
            base = max(0.0, float(getattr(ps, '_hs_parallel_base_position', 0) or 0))
            started = float(getattr(ps, '_hs_parallel_started_monotonic', 0) or 0)
            values.append(base + (max(0.0, now_mono - started) if started else 0.0))
        if values:
            return max(values)
        return max(0.0, float(getattr(session, '_hs_parallel_last_elapsed', 0) or 0))

    def mark_started(self, session, slug, base_elapsed):
        ps = session.platform_states.get(slug)
        if not ps:
            return
        # When the playlist wrapper advanced to a new item, the canonical base is the
        # elapsed duration of all previous items/cycles, not the failed process position.
        ch = session.work_channel or {}
        if ch.get('_hs_youtube_playlist_active'):
            base_elapsed = max(0.0, float(ch.get('_hs_youtube_playlist_elapsed_before') or 0))
        ps._hs_parallel_base_position = max(0.0, float(base_elapsed or 0))
        ps._hs_parallel_started_monotonic = time.monotonic()
        session._hs_parallel_last_elapsed = ps._hs_parallel_base_position

    def stop_parallel(self, run_id, reason='finalizada'):
        with self.lock:
            session = self._hs_parallel_sessions.get(run_id)
        if not session:
            return
        session.stop_requested = True
        session.desired_running = False
        for slug, ps in (session.platform_states or {}).items():
            p = ps.process
            if p and p.poll() is None:
                try:
                    p.send_signal(signal.SIGTERM)
                    p.wait(timeout=4)
                except Exception:
                    try:
                        p.kill()
                    except Exception:
                        pass
            try:
                streaming_module.upsert_platform_run(session.run_id, slug, status='stopped', ended_at=now_iso(), pid=0)
            except Exception:
                pass
        try:
            streaming_module.finish_live_run(session.run_id, 'finished', reason)
            if session.schedule_id:
                streaming_module.update_schedule_status(session.schedule_id, last_finished_at=now_iso(), last_status='Finalizada: ' + reason)
            streaming_module.audit('info', 'parallel_live_stopped', session.channel_id, f'Live paralela encerrada: {reason}', {'run_id': session.run_id, 'schedule_id': session.schedule_id})
        except Exception:
            pass
        save_parallel_state(self, session, current_elapsed(self, session), 'stopped', False)
        if session.playlist_path:
            try:
                Path(session.playlist_path).unlink(missing_ok=True)
            except Exception:
                pass
        with self.lock:
            self._hs_parallel_sessions.pop(run_id, None)

    def start_parallel(self, cid, platforms, media, schedule):
        ch = streaming_module.get_channel(cid)
        if not ch:
            return False, 'Canal não encontrado.'
        requested = [p for p in list(platforms or schedule.get('platforms') or []) if p in (ch.get('destinations') or {})]
        valid = [p for p in requested if build_target(ch['destinations'][p].get('rtmp_url'), ch['destinations'][p].get('stream_key'))]
        if not valid:
            return False, 'Nenhuma plataforma selecionada possui RTMP/chave válida.'
        active = active_platforms(self, cid)
        overlap = active.intersection(valid)
        if overlap:
            labels = [ch['destinations'][p].get('label', p) for p in sorted(overlap)]
            return False, 'Já existe live neste canal em: ' + ', '.join(labels)

        work = json.loads(json.dumps(ch))
        work['_hs_parallel_session'] = True
        source_mode = str(schedule.get('source_mode') or 'library')
        media = list(media or schedule.get('media') or [])
        max_duration = 0.0
        cycle_duration = 0.0

        if source_mode == 'url':
            source_url = str(schedule.get('source_url') or '').strip()
            if not source_url:
                return False, 'Agenda por URL sem fonte configurada.'
            work['_schedule_source_url'] = source_url
            work['_schedule_source_title'] = str(schedule.get('source_title') or '').strip()
            work['_repeat_playlist'] = False
            duration = max(0.0, float(schedule.get('source_duration_seconds') or 0))
            stop_before = max(0, int(schedule.get('stop_before_seconds') or 0))
            max_minutes = max(0, int(schedule.get('max_duration_minutes') or 0))
            if duration > 0:
                max_duration = max(1.0, duration - stop_before)
                if max_minutes > 0:
                    max_duration = min(max_duration, max_minutes * 60.0)
            elif max_minutes > 0:
                max_duration = max_minutes * 60.0
            else:
                return False, 'A URL não informou duração e a agenda não possui duração máxima.'
            media = []
            label = work['_schedule_source_title'] or source_url
        else:
            if schedule.get('shuffle'):
                random.shuffle(media)
            if not media:
                return False, 'Agenda sem mídia.'
            cycle_duration = float(self._playlist_duration(media))
            stop_before = max(0, int(schedule.get('stop_before_seconds') or 60))
            if schedule.get('repeat_playlist') and int(schedule.get('max_duration_minutes') or 0) > 0:
                max_duration = max(1.0, int(schedule.get('max_duration_minutes') or 0) * 60.0 - stop_before)
            elif schedule.get('repeat_playlist'):
                max_duration = 10 * 365 * 24 * 3600.0
            else:
                max_duration = max(1.0, cycle_duration - stop_before)
            work['_repeat_playlist'] = bool(schedule.get('repeat_playlist'))
            label = ' → '.join(media)

        stop_at = (now_dt() + __import__('datetime').timedelta(seconds=max_duration)).isoformat() if max_duration > 0 else ''
        run_id = streaming_module.create_live_run(cid, schedule.get('id'), 'scheduled', label, valid, stop_at)
        session = streaming_module.Session(
            channel_id=cid, run_id=run_id, trigger='scheduled', schedule_id=schedule.get('id'), platforms=valid,
            media=media, started_at=now_iso(), stop_at=stop_at, desired_running=True, work_channel=work,
            max_duration_seconds=max_duration,
        )
        session._hs_schedule_snapshot = dict(schedule)
        session._hs_parallel_cycle_duration = cycle_duration
        resume_elapsed = max(0.0, float(schedule.get('_parallel_resume_elapsed') or 0))
        session._hs_parallel_last_elapsed = resume_elapsed
        if len(media) > 1:
            session.playlist_path = str(self._make_concat_file(cid, run_id, media))

        with self.lock:
            self._hs_parallel_sessions[run_id] = session

        started = []
        errors = []
        for slug in valid:
            session._hs_active_seek_seconds = _source_seek(session, resume_elapsed)
            if self._start_platform(session, slug, recovery=bool(resume_elapsed)):
                started.append(slug)
                mark_started(self, session, slug, resume_elapsed)
            else:
                errors.append(slug)
        session._hs_active_seek_seconds = 0.0

        if not started:
            with self.lock:
                self._hs_parallel_sessions.pop(run_id, None)
            streaming_module.finish_live_run(run_id, 'failed', 'Nenhuma plataforma paralela iniciou.')
            return False, 'Nenhuma plataforma paralela conseguiu iniciar.'

        save_parallel_state(self, session, resume_elapsed, 'running', True)
        try:
            streaming_module.update_schedule_status(schedule.get('id'), last_started_at=now_iso(), last_status='Live paralela iniciada: ' + ', '.join(started))
            streaming_module.audit(
                'info', 'parallel_live_started', cid,
                f'Live paralela iniciada sem interromper as plataformas já ativas: {", ".join(started)}',
                {'run_id': run_id, 'schedule_id': schedule.get('id'), 'platforms': started, 'already_active': sorted(active)},
            )
        except Exception:
            pass
        msg = 'Live paralela iniciada: ' + ', '.join(started)
        if errors:
            msg += ' | falharam: ' + ', '.join(errors)
        return True, msg

    def start(self, cid, platforms=None, media=None, trigger='manual', schedule=None):
        requested = list(platforms or ((schedule or {}).get('platforms') if schedule else []) or [])
        active = active_platforms(self, cid)
        if trigger == 'scheduled' and schedule and (active or schedule.get('_force_parallel_session')):
            overlap = active.intersection(set(requested))
            if not overlap or schedule.get('_force_parallel_session'):
                return start_parallel(self, cid, requested, media, dict(schedule))
        if trigger != 'scheduled' and active:
            ch = streaming_module.get_channel(cid) or {}
            if not requested:
                requested = [slug for slug, d in (ch.get('destinations') or {}).items() if d.get('enabled')]
            overlap = active.intersection(set(requested))
            if overlap:
                return False, 'Já existe live ativa neste canal nas plataformas selecionadas.'
        return original_start(cid, platforms, media, trigger, schedule)

    def stop(self, cid, *args, **kwargs):
        reason = args[0] if args else kwargs.get('reason', 'manual')
        result = original_stop(cid, *args, **kwargs)
        for sess in list(parallel_sessions_for(self, cid)):
            stop_parallel(self, sess.run_id, str(reason or 'manual'))
        return result

    def supervisor_loop(self):
        time.sleep(3)
        while True:
            try:
                sessions = []
                with self.lock:
                    sessions = list(self._hs_parallel_sessions.values())
                now = time.time()
                for session in sessions:
                    if session.stop_requested or not session.desired_running:
                        continue
                    if session.stop_at:
                        dt = parse_iso(session.stop_at)
                        if dt and now_dt() >= dt:
                            stop_parallel(self, session.run_id, 'horário/duração programada concluída')
                            continue
                    total_elapsed = current_elapsed(self, session)
                    session._hs_parallel_last_elapsed = total_elapsed
                    for slug in list(session.platforms):
                        ps = session.platform_states.get(slug)
                        if not ps or _alive(ps):
                            continue
                        if ps.next_retry_at and now < ps.next_retry_at:
                            continue
                        if ps.next_retry_at and now >= ps.next_retry_at:
                            before_index = int(getattr(session, '_hs_youtube_playlist_index', -1) or -1)
                            session._hs_active_seek_seconds = _source_seek(session, total_elapsed)
                            ps.next_retry_at = 0
                            if self._start_platform(session, slug, recovery=True):
                                after_index = int(getattr(session, '_hs_youtube_playlist_index', -1) or -1)
                                base = total_elapsed
                                if after_index != before_index and (session.work_channel or {}).get('_hs_youtube_playlist_active'):
                                    base = float((session.work_channel or {}).get('_hs_youtube_playlist_elapsed_before') or 0)
                                    session._hs_active_seek_seconds = 0.0
                                mark_started(self, session, slug, base)
                                continue
                        code = ps.process.returncode if ps.process else None
                        ps.last_error = f'FFmpeg encerrou com código {code}'
                        delay = RETRY_BACKOFF[min(ps.retries, len(RETRY_BACKOFF) - 1)]
                        ps.retries += 1
                        ps.next_retry_at = now + delay
                        try:
                            streaming_module.upsert_platform_run(session.run_id, slug, status='reconnecting', retries=ps.retries, last_error=ps.last_error, ended_at=now_iso())
                        except Exception:
                            pass
                        self.log(session.channel_id, f'{ps.label} (live paralela) caiu. Nova tentativa em {delay}s.')
                    session._hs_active_seek_seconds = 0.0
                    save_parallel_state(self, session, total_elapsed, 'running', True)
            except Exception as exc:
                try:
                    streaming_module.audit('error', 'parallel_supervisor_error', '', str(exc))
                except Exception:
                    pass
            time.sleep(SUPERVISOR_INTERVAL_SECONDS)

    def boot_resume_loop(self):
        time.sleep(8)
        try:
            with db_module.connect() as con:
                rows = con.execute(
                    "SELECT * FROM parallel_schedule_state WHERE resume_enabled=1 AND status='running' ORDER BY updated_at DESC"
                ).fetchall()
            for row in rows:
                data = dict(row)
                updated = _parse(data.get('updated_at'))
                if updated and (datetime.now(updated.tzinfo or timezone.utc) - updated).total_seconds() > 24 * 3600:
                    continue
                stop_at = parse_iso(data.get('stop_at'))
                if stop_at and now_dt() >= stop_at:
                    with db_module.connect() as con:
                        con.execute("UPDATE parallel_schedule_state SET status='finished',resume_enabled=0,updated_at=? WHERE run_id=?", (now_iso(), data['run_id']))
                    continue
                try:
                    schedule = json.loads(data.get('schedule_json') or '{}')
                except Exception:
                    schedule = {}
                if not schedule:
                    sid = str(data.get('schedule_id') or '')
                    schedule = db_module.get_schedule(sid) if sid else None
                if not schedule:
                    continue
                try:
                    platforms = json.loads(data.get('platforms_json') or '[]')
                except Exception:
                    platforms = []
                schedule = dict(schedule)
                schedule['_force_parallel_session'] = True
                schedule['_parallel_resume_elapsed'] = max(0.0, float(data.get('elapsed_seconds') or 0))
                ok, msg = self.start(data['channel_id'], platforms=platforms, media=schedule.get('media'), trigger='scheduled', schedule=schedule)
                if ok:
                    with db_module.connect() as con:
                        con.execute("UPDATE parallel_schedule_state SET status='superseded',resume_enabled=0,updated_at=? WHERE run_id=?", (now_iso(), data['run_id']))
                    try:
                        streaming_module.audit('info', 'parallel_live_resumed', data['channel_id'], 'Live paralela retomada após reinício.', {'old_run_id': data['run_id'], 'message': msg})
                    except Exception:
                        pass
            
        except Exception as exc:
            try:
                streaming_module.audit('error', 'parallel_boot_resume_error', '', str(exc))
            except Exception:
                pass

    def start_threads(self):
        original_start_threads()
        if self._hs_parallel_started:
            return
        self._hs_parallel_started = True
        threading.Thread(target=lambda: supervisor_loop(self), daemon=True, name='parallel-schedule-supervisor').start()
        threading.Thread(target=lambda: boot_resume_loop(self), daemon=True, name='parallel-schedule-resume').start()

    manager.start = MethodType(start, manager)
    manager.stop = MethodType(stop, manager)
    manager.channel_status = MethodType(status, manager)
    manager.start_threads = MethodType(start_threads, manager)
    return manager
