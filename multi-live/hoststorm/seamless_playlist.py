from __future__ import annotations

import json
import math
import random
import signal
import socket
import subprocess
import threading
import time
from types import MethodType

from .url_resilience import resolve_remote_inputs
from .utils import now_iso
from .youtube_playlist import get_playlist_items, probe_youtube_playlist, replace_playlist_items


def _free_udp_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(('127.0.0.1', 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def _item_position(items, elapsed: float, repeat: bool):
    """Return (index, cycle, offset) for an accumulated playlist elapsed time."""
    elapsed = max(0.0, float(elapsed or 0))
    durations = [max(0.0, float(x.get('duration_seconds') or 0)) for x in items]
    if not items:
        return 0, 0, 0.0
    if not all(d > 0 for d in durations):
        return 0, 0, elapsed
    cycle_duration = sum(durations)
    cycle = int(elapsed // cycle_duration) if repeat and cycle_duration > 0 else 0
    remaining = elapsed - cycle * cycle_duration
    for idx, duration in enumerate(durations):
        if remaining < duration:
            return idx, cycle, remaining
        remaining -= duration
    return len(items) - 1, cycle, max(0.0, durations[-1] - 0.25)


def _total_limit(schedule, items) -> float:
    maximum = max(0, int(schedule.get('max_duration_minutes') or 0))
    if maximum:
        return float(maximum * 60)
    durations = [max(0.0, float(x.get('duration_seconds') or 0)) for x in items]
    if schedule.get('repeat_playlist'):
        return float(10 * 365 * 24 * 3600)
    if items and all(d > 0 for d in durations):
        return max(1.0, sum(durations) - max(0, int(schedule.get('stop_before_seconds') or 0)))
    return float(24 * 3600)


def _terminate(proc):
    if not proc or proc.poll() is not None:
        return
    try:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=4)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def install_seamless_youtube_playlist(manager, streaming_module, db_module):
    """Keep the RTMP publisher alive while YouTube playlist items change.

    The publisher FFmpeg reads a local MPEG-TS/UDP bridge. A short-lived feeder resolves
    each YouTube item and sends it to that bridge. At item boundaries only the feeder is
    replaced; the FFmpeg process holding the RTMP socket never closes.
    """
    original_start = manager.start
    original_stop = manager.stop
    original_status = manager.channel_status
    original_input_args = manager._input_args
    original_build_cmd = manager._build_cmd

    manager._hs_seamless_pending = {}
    manager._hs_seamless_runs = {}

    def context_for_session(self, session):
        return self._hs_seamless_runs.get(str(getattr(session, 'run_id', '') or '')) or self._hs_seamless_pending.get(str(session.channel_id))

    def input_args(self, session, vertical=False):
        bridge = str(getattr(session, '_hs_active_bridge_url', '') or '')
        if bridge:
            session._hs_remote_input_count = 1
            return [
                '-thread_queue_size', '4096',
                '-fflags', '+genpts+discardcorrupt',
                '-use_wallclock_as_timestamps', '1',
                '-probesize', '5000000', '-analyzeduration', '2000000',
                '-f', 'mpegts', '-i', bridge,
            ]
        return original_input_args(session, vertical)

    def build_cmd(self, session, slug):
        ctx = context_for_session(self, session)
        if not ctx or slug not in ctx.get('bridge_urls', {}):
            return original_build_cmd(session, slug)
        previous = getattr(session, '_hs_active_bridge_url', '')
        session._hs_active_bridge_url = ctx['bridge_urls'][slug]
        try:
            return original_build_cmd(session, slug)
        finally:
            session._hs_active_bridge_url = previous

    def update_snapshot(session, ctx, item_position=0.0):
        snap = dict(ctx.get('schedule') or {})
        snap['seamless_index'] = int(ctx.get('index') or 0)
        snap['seamless_cycle'] = int(ctx.get('cycle') or 0)
        snap['seamless_item_position'] = max(0.0, float(item_position or 0))
        snap['playlist_runtime_index'] = snap['seamless_index']
        snap['playlist_runtime_cycle'] = snap['seamless_cycle']
        session._hs_schedule_snapshot = snap

    def save_runtime(ctx, item_position=0.0, status='running'):
        try:
            with db_module.connect() as con:
                con.execute('''
                    CREATE TABLE IF NOT EXISTS seamless_playlist_state (
                        run_id TEXT PRIMARY KEY, channel_id TEXT NOT NULL, schedule_id TEXT NOT NULL,
                        platforms_json TEXT NOT NULL DEFAULT '[]', current_index INTEGER NOT NULL DEFAULT 0,
                        current_cycle INTEGER NOT NULL DEFAULT 0, item_position REAL NOT NULL DEFAULT 0,
                        current_title TEXT NOT NULL DEFAULT '', next_title TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL DEFAULT 'running', updated_at TEXT NOT NULL
                    )
                ''')
                items = ctx.get('items') or []
                idx = int(ctx.get('index') or 0)
                title = items[idx].get('title', '') if items and idx < len(items) else ''
                nxt = ''
                if items:
                    ni = idx + 1
                    if ni >= len(items) and ctx.get('repeat'):
                        ni = 0
                    if ni < len(items):
                        nxt = items[ni].get('title', '')
                con.execute('''
                    INSERT INTO seamless_playlist_state(run_id,channel_id,schedule_id,platforms_json,current_index,current_cycle,item_position,current_title,next_title,status,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(run_id) DO UPDATE SET platforms_json=excluded.platforms_json,current_index=excluded.current_index,
                    current_cycle=excluded.current_cycle,item_position=excluded.item_position,current_title=excluded.current_title,
                    next_title=excluded.next_title,status=excluded.status,updated_at=excluded.updated_at
                ''', (
                    ctx.get('run_id',''), ctx.get('channel_id',''), ctx.get('schedule_id',''),
                    json.dumps(ctx.get('platforms') or []), idx, int(ctx.get('cycle') or 0),
                    max(0.0, float(item_position or 0)), title, nxt, status, now_iso(),
                ))
        except Exception:
            pass

    def feeder_command(session, ctx, item, seek=0.0):
        resolved = resolve_remote_inputs(item['url'])
        inputs = list(resolved.get('inputs') or [])
        if not inputs:
            raise RuntimeError('yt-dlp não retornou entrada para o item da playlist.')
        cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'warning']
        seek = max(0.0, float(seek or 0))
        for source in inputs[:2]:
            cmd += ['-re']
            if seek > 0.5:
                cmd += ['-ss', f'{seek:.3f}']
            cmd += ['-i', source]
        cmd += ['-map', '0:v:0']
        cmd += ['-map', '1:a:0' if len(inputs) >= 2 else '0:a?']

        ch = session.work_channel or {}
        raw_res = str(ch.get('resolution') or '1920x1080')
        try:
            width, height = [max(2, int(x)) for x in raw_res.lower().split('x', 1)]
        except Exception:
            width, height = 1920, 1080
        try:
            fps = max(1, int(float(ch.get('fps') or 30)))
        except Exception:
            fps = 30
        vf = f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,format=yuv420p'
        cmd += [
            '-vf', vf, '-r', str(fps), '-c:v', 'libx264', '-preset', 'ultrafast', '-tune', 'zerolatency',
            '-crf', '18', '-g', str(fps * 2), '-keyint_min', str(fps * 2), '-sc_threshold', '0',
            '-c:a', 'aac', '-b:a', '160k', '-ar', '44100', '-ac', '2',
            '-mpegts_flags', '+resend_headers+initial_discontinuity', '-muxdelay', '0', '-muxpreload', '0',
        ]
        outputs = []
        for url in ctx.get('feed_urls', {}).values():
            outputs.append(f'[f=mpegts:onfail=ignore]{url}')
        if not outputs:
            raise RuntimeError('Nenhuma ponte RTMP disponível para a playlist.')
        cmd += ['-f', 'tee', '|'.join(outputs)]
        return cmd

    def finish_session(self, session, ctx, reason):
        if ctx.get('finishing'):
            return
        ctx['finishing'] = True
        ctx['stop'].set()
        _terminate(ctx.get('feeder'))
        session.stop_requested = True
        session.desired_running = False
        for slug, ps in list((session.platform_states or {}).items()):
            _terminate(getattr(ps, 'process', None))
            try:
                streaming_module.upsert_platform_run(session.run_id, slug, status='stopped', ended_at=now_iso(), pid=0)
            except Exception:
                pass
        try:
            streaming_module.finish_live_run(session.run_id, 'finished', reason)
            if session.schedule_id:
                streaming_module.update_schedule_status(session.schedule_id, last_finished_at=now_iso(), last_status='Finalizada: ' + reason)
            streaming_module.audit('info', 'seamless_playlist_finished', session.channel_id, reason, {'run_id': session.run_id, 'schedule_id': session.schedule_id})
        except Exception:
            pass
        with self.lock:
            if self.sessions.get(session.channel_id) is session:
                self.sessions.pop(session.channel_id, None)
            if hasattr(self, '_hs_parallel_sessions'):
                self._hs_parallel_sessions.pop(session.run_id, None)
        save_runtime(ctx, 0, 'finished')
        self._hs_seamless_runs.pop(session.run_id, None)

    def feeder_loop(self, session, ctx, initial_seek=0.0):
        retries = 0
        seek = max(0.0, float(initial_seek or 0))
        items = ctx['items']
        try:
            while not ctx['stop'].is_set() and not session.stop_requested and session.desired_running:
                idx = int(ctx.get('index') or 0)
                item = items[idx]
                ctx['item_started_monotonic'] = time.monotonic() - seek
                update_snapshot(session, ctx, seek)
                save_runtime(ctx, seek)
                try:
                    cmd = feeder_command(session, ctx, item, seek)
                    with self._log_path(session.channel_id).open('a', encoding='utf-8') as log:
                        log.write(f'\n[{now_iso()}] PLAYLIST SEAMLESS {idx + 1}/{len(items)}: {item.get("title") or item["url"]}\n')
                        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, text=True)
                        ctx['feeder'] = proc
                        last_save = 0.0
                        while proc.poll() is None and not ctx['stop'].is_set() and not session.stop_requested:
                            pos = max(0.0, time.monotonic() - float(ctx['item_started_monotonic']))
                            if time.monotonic() - last_save >= 5:
                                update_snapshot(session, ctx, pos)
                                save_runtime(ctx, pos)
                                last_save = time.monotonic()
                            time.sleep(0.5)
                        if ctx['stop'].is_set() or session.stop_requested:
                            _terminate(proc)
                            return
                        code = proc.returncode
                except Exception as exc:
                    code = -1
                    try:
                        self.log(session.channel_id, 'Falha no feeder da playlist: ' + str(exc))
                    except Exception:
                        pass

                if code not in (0, None) and retries < 3:
                    retries += 1
                    seek = max(0.0, time.monotonic() - float(ctx.get('item_started_monotonic') or time.monotonic()))
                    time.sleep(min(15, 2 ** retries))
                    continue

                if code not in (0, None):
                    try:
                        streaming_module.audit('warning', 'seamless_playlist_item_skipped', session.channel_id,
                                               f'Item {idx + 1} pulado após falhas.', {'index': idx, 'title': item.get('title','')})
                    except Exception:
                        pass
                retries = 0
                seek = 0.0
                idx += 1
                if idx >= len(items):
                    if ctx.get('repeat'):
                        idx = 0
                        ctx['cycle'] = int(ctx.get('cycle') or 0) + 1
                        if ctx.get('shuffle'):
                            random.shuffle(items)
                    else:
                        finish_session(self, session, ctx, 'playlist concluída sem derrubar o RTMP entre os vídeos')
                        return
                ctx['index'] = idx
                update_snapshot(session, ctx, 0)
                save_runtime(ctx, 0)
                try:
                    streaming_module.audit('info', 'seamless_playlist_advanced', session.channel_id,
                                           f'Playlist avançou para {idx + 1}/{len(items)} sem reiniciar o publicador RTMP.',
                                           {'index': idx, 'count': len(items), 'cycle': ctx.get('cycle',0)})
                except Exception:
                    pass
        finally:
            ctx['feeder'] = None

    def start(self, cid, platforms=None, media=None, trigger='manual', schedule=None):
        if trigger != 'scheduled' or not schedule or str(schedule.get('source_mode') or '') != 'youtube_playlist':
            return original_start(cid, platforms, media, trigger, schedule)

        schedule = dict(schedule)
        sid = str(schedule.get('id') or '')
        try:
            fresh = probe_youtube_playlist(schedule.get('source_url', ''), timeout=90)
            items = list(fresh.get('items') or [])
            if items:
                replace_playlist_items(db_module, sid, items)
                schedule['source_title'] = fresh.get('title') or schedule.get('source_title') or 'Playlist do YouTube'
        except Exception as exc:
            items = get_playlist_items(db_module, sid)
            if not items:
                return False, 'Não foi possível carregar a playlist: ' + str(exc)
        if not items:
            return False, 'A playlist não possui vídeos reproduzíveis.'

        requested = getattr(self, '_hs_resume_requests', {}).get(str(cid)) or {}
        requested_schedule = dict(requested.get('schedule') or {})
        repeat = bool(schedule.get('repeat_playlist'))
        if schedule.get('shuffle') and not requested:
            random.shuffle(items)

        resume_total = max(0.0, float(requested.get('elapsed_seconds') or requested.get('position_seconds') or 0))
        if 'seamless_index' in requested_schedule:
            index = max(0, min(int(requested_schedule.get('seamless_index') or 0), len(items)-1))
            cycle = max(0, int(requested_schedule.get('seamless_cycle') or 0))
            item_seek = max(0.0, float(requested_schedule.get('seamless_item_position') or 0))
        else:
            index, cycle, item_seek = _item_position(items, resume_total, repeat)

        requested_platforms = list(platforms or schedule.get('platforms') or [])
        if not requested_platforms:
            return False, 'Selecione pelo menos uma plataforma.'
        bridge_urls = {}
        feed_urls = {}
        for slug in requested_platforms:
            port = _free_udp_port()
            bridge_urls[slug] = f'udp://127.0.0.1:{port}?fifo_size=1000000&overrun_nonfatal=1&reuse=1'
            feed_urls[slug] = f'udp://127.0.0.1:{port}?pkt_size=1316&buffer_size=65535'

        total = _total_limit(schedule, items)
        synthetic = dict(schedule)
        synthetic['source_mode'] = 'url'
        synthetic['source_url'] = items[index]['url']
        synthetic['source_title'] = schedule.get('source_title') or 'Playlist do YouTube'
        synthetic['source_duration_seconds'] = total
        synthetic['stop_before_seconds'] = 0
        synthetic['max_duration_minutes'] = int(math.ceil(total / 60.0))
        synthetic['_seamless_youtube_playlist'] = True

        pending = {
            'channel_id': str(cid), 'schedule_id': sid, 'schedule': dict(schedule), 'items': items,
            'index': index, 'cycle': cycle, 'repeat': repeat, 'shuffle': bool(schedule.get('shuffle')),
            'bridge_urls': bridge_urls, 'feed_urls': feed_urls, 'platforms': requested_platforms,
            'stop': threading.Event(), 'feeder': None, 'finishing': False,
        }
        self._hs_seamless_pending[str(cid)] = pending
        try:
            ok, msg = original_start(cid, requested_platforms, [], trigger, synthetic)
        finally:
            self._hs_seamless_pending.pop(str(cid), None)
        if not ok:
            return ok, msg

        with self.lock:
            session = self.sessions.get(str(cid)) or self.sessions.get(cid)
            if not session and hasattr(self, '_hs_parallel_sessions'):
                candidates = [s for s in self._hs_parallel_sessions.values() if str(s.channel_id)==str(cid) and str(s.schedule_id or '')==sid]
                session = candidates[-1] if candidates else None
        if not session:
            return False, 'Publicador iniciou, mas a sessão da playlist não foi encontrada.'

        pending['run_id'] = session.run_id
        pending['platforms'] = list(session.platforms or requested_platforms)
        self._hs_seamless_runs[session.run_id] = pending
        session._hs_seamless_playlist = True
        session._hs_total_duration_limit = total
        update_snapshot(session, pending, item_seek)
        if requested:
            for ps in (session.platform_states or {}).values():
                ps._hs_base_position = resume_total
                ps._hs_started_monotonic = time.monotonic()

        thread = threading.Thread(target=feeder_loop, args=(self, session, pending, item_seek), daemon=True,
                                  name=f'seamless-playlist-{cid}-{session.run_id}')
        pending['thread'] = thread
        thread.start()
        try:
            streaming_module.update_schedule_status(sid, last_started_at=now_iso(), last_status=f'Playlist contínua iniciada: {len(items)} vídeo(s).')
            streaming_module.audit('info', 'seamless_playlist_started', cid,
                                   f'Playlist contínua iniciada com {len(items)} vídeo(s); RTMP persistente.',
                                   {'schedule_id': sid, 'run_id': session.run_id, 'index': index, 'platforms': pending['platforms']})
        except Exception:
            pass
        return True, f'Playlist contínua iniciada: {len(items)} vídeo(s), sem reconectar o RTMP entre itens.'

    def stop(self, cid, *args, **kwargs):
        for ctx in list(self._hs_seamless_runs.values()):
            if str(ctx.get('channel_id')) == str(cid):
                ctx['stop'].set()
                _terminate(ctx.get('feeder'))
                save_runtime(ctx, 0, 'stopped')
        return original_stop(cid, *args, **kwargs)

    def status(self, cid):
        data = original_status(cid)
        runs = []
        for run_id, ctx in list(self._hs_seamless_runs.items()):
            if str(ctx.get('channel_id')) != str(cid):
                continue
            items = ctx.get('items') or []
            idx = max(0, min(int(ctx.get('index') or 0), len(items)-1)) if items else 0
            pos = 0.0
            if ctx.get('item_started_monotonic'):
                pos = max(0.0, time.monotonic() - float(ctx['item_started_monotonic']))
            next_idx = idx + 1
            if items and next_idx >= len(items) and ctx.get('repeat'):
                next_idx = 0
            runs.append({
                'active': True, 'run_id': run_id, 'position': idx + 1, 'count': len(items),
                'index': idx, 'cycle': int(ctx.get('cycle') or 0), 'item_position_seconds': round(pos, 1),
                'title': items[idx].get('title','') if items else '',
                'next_title': items[next_idx].get('title','') if items and next_idx < len(items) else '',
                'repeat': bool(ctx.get('repeat')), 'seamless': True,
            })
        if runs:
            data['youtube_playlist'] = runs[0]
            data['seamless_playlists'] = runs
        return data

    manager._input_args = MethodType(input_args, manager)
    manager._build_cmd = MethodType(build_cmd, manager)
    manager.start = MethodType(start, manager)
    manager.stop = MethodType(stop, manager)
    manager.channel_status = MethodType(status, manager)
    return manager
