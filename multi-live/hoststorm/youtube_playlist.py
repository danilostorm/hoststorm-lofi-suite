from __future__ import annotations

import json
import random
import subprocess
import threading
import time
import urllib.parse
from datetime import timedelta
from types import MethodType

from flask import Blueprint, flash, jsonify, redirect, request, url_for

from .url_sources import validate_remote_url, ytdlp_status
from .utils import now_dt, now_iso

playlist_bp = Blueprint('ytplaylist', __name__)

DB = None
MANAGER = None


def _playlist_args():
    status = ytdlp_status()
    if not status.get('ok'):
        raise RuntimeError('yt-dlp não está disponível no container.')
    args = [status['path'], '--flat-playlist', '--yes-playlist', '--no-warnings', '--socket-timeout', '20']
    if status.get('node'):
        args += ['--js-runtimes', 'node']
    return args


def _entry_url(entry: dict) -> str:
    webpage = str(entry.get('webpage_url') or '').strip()
    if webpage.startswith(('http://', 'https://')):
        return webpage
    raw = str(entry.get('url') or '').strip()
    if raw.startswith(('http://', 'https://')):
        return raw
    vid = str(entry.get('id') or raw or '').strip()
    if vid:
        return 'https://www.youtube.com/watch?v=' + urllib.parse.quote(vid, safe='-_')
    return ''


def probe_youtube_playlist(url: str, timeout=90) -> dict:
    url = validate_remote_url(url)
    cmd = _playlist_args() + ['--dump-single-json', url]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError('yt-dlp não conseguiu ler a playlist: ' + (p.stderr or p.stdout or '')[-1400:])
    try:
        data = json.loads(p.stdout or '{}')
    except Exception as exc:
        raise RuntimeError('Resposta inválida do yt-dlp para playlist.') from exc

    items = []
    known_total = 0.0
    known_count = 0
    for pos, entry in enumerate(list(data.get('entries') or [])):
        if not isinstance(entry, dict):
            continue
        item_url = _entry_url(entry)
        if not item_url:
            continue
        duration = max(0.0, float(entry.get('duration') or 0))
        if duration > 0:
            known_total += duration
            known_count += 1
        items.append({
            'position': len(items),
            'id': str(entry.get('id') or '').strip(),
            'title': str(entry.get('title') or entry.get('fulltitle') or f'Vídeo {pos + 1}').strip()[:500],
            'url': item_url,
            'duration_seconds': duration,
            'availability': str(entry.get('availability') or '').strip(),
        })
    if not items:
        raise RuntimeError('Nenhum vídeo reproduzível foi encontrado nessa playlist.')

    return {
        'ok': True,
        'url': url,
        'title': str(data.get('title') or data.get('playlist_title') or 'Playlist do YouTube').strip()[:500],
        'count': len(items),
        'duration_seconds': known_total,
        'all_durations_known': known_count == len(items),
        'items': items,
        'extractor': str(data.get('extractor_key') or data.get('extractor') or 'youtube:playlist'),
    }


def migrate_playlist_schema(db_module):
    with db_module.connect() as con:
        con.execute('''
        CREATE TABLE IF NOT EXISTS schedule_youtube_playlist_items (
            schedule_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            video_id TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            url TEXT NOT NULL,
            duration_seconds REAL NOT NULL DEFAULT 0,
            availability TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(schedule_id, position),
            FOREIGN KEY(schedule_id) REFERENCES schedules(id) ON DELETE CASCADE
        )
        ''')
        con.execute('CREATE INDEX IF NOT EXISTS idx_yt_playlist_schedule ON schedule_youtube_playlist_items(schedule_id, position)')
        con.execute("INSERT INTO meta(key,value) VALUES('schema_version','6') ON CONFLICT(key) DO UPDATE SET value='6'")


def replace_playlist_items(db_module, sid: str, items: list[dict]):
    with db_module.connect() as con:
        con.execute('DELETE FROM schedule_youtube_playlist_items WHERE schedule_id=?', (sid,))
        for pos, item in enumerate(items):
            con.execute(
                'INSERT INTO schedule_youtube_playlist_items(schedule_id,position,video_id,title,url,duration_seconds,availability) VALUES(?,?,?,?,?,?,?)',
                (sid, pos, str(item.get('id') or ''), str(item.get('title') or '')[:500], str(item.get('url') or ''),
                 max(0.0, float(item.get('duration_seconds') or 0)), str(item.get('availability') or '')[:120]),
            )


def get_playlist_items(db_module, sid: str) -> list[dict]:
    with db_module.connect() as con:
        rows = con.execute(
            'SELECT position,video_id,title,url,duration_seconds,availability FROM schedule_youtube_playlist_items WHERE schedule_id=? ORDER BY position',
            (sid,),
        ).fetchall()
    return [
        {'position': int(r['position']), 'id': r['video_id'], 'title': r['title'], 'url': r['url'],
         'duration_seconds': float(r['duration_seconds'] or 0), 'availability': r['availability']}
        for r in rows
    ]


def _augment_playlist(db_module, schedule):
    if not schedule:
        return schedule
    if str(schedule.get('source_mode') or '') == 'youtube_playlist':
        items = get_playlist_items(db_module, schedule['id'])
        schedule['youtube_playlist_count'] = len(items)
        schedule['youtube_playlist_items'] = items
    else:
        schedule['youtube_playlist_count'] = 0
        schedule['youtube_playlist_items'] = []
    return schedule


def install_playlist_db(db_module, web_module, scheduler_module):
    migrate_playlist_schema(db_module)
    original_list = db_module.list_schedules
    original_get = db_module.get_schedule

    def list_schedules(channel_id=None, con=None):
        return [_augment_playlist(db_module, s) for s in original_list(channel_id, con)]

    def get_schedule(sid):
        return _augment_playlist(db_module, original_get(sid))

    db_module.list_schedules = list_schedules
    db_module.get_schedule = get_schedule
    web_module.list_schedules = list_schedules
    web_module.get_schedule = get_schedule
    scheduler_module.list_schedules = list_schedules


def _cycle_duration(items):
    return sum(max(0.0, float(x.get('duration_seconds') or 0)) for x in items)


def _all_known(items):
    return bool(items) and all(float(x.get('duration_seconds') or 0) > 0 for x in items)


def _elapsed_before_index(items, index, cycle=0):
    return max(0, int(cycle or 0)) * _cycle_duration(items) + sum(
        max(0.0, float(x.get('duration_seconds') or 0)) for x in items[:max(0, index)]
    )


def _total_limit(schedule, items):
    max_minutes = max(0, int(schedule.get('max_duration_minutes') or 0))
    stop_before = max(0, int(schedule.get('stop_before_seconds') or 0))
    if max_minutes > 0:
        return max(1.0, max_minutes * 60.0)
    if schedule.get('repeat_playlist'):
        return 10 * 365 * 24 * 3600.0
    if _all_known(items):
        return max(1.0, _cycle_duration(items) - stop_before)
    # With unavailable/unknown-duration items, let the playlist engine decide the end.
    return 10 * 365 * 24 * 3600.0


def _runtime_schedule(schedule: dict, item: dict) -> dict:
    out = dict(schedule or {})
    duration = max(0.0, float(item.get('duration_seconds') or 0))
    out['source_mode'] = 'url'
    out['source_url'] = item['url']
    out['source_title'] = item.get('title') or str(schedule.get('source_title') or 'Playlist do YouTube')
    out['source_duration_seconds'] = duration
    out['stop_before_seconds'] = 0
    # Unknown/live playlist items need a generous per-item ceiling; normal VOD items use
    # their detected duration so FFmpeg exits naturally and the engine advances.
    out['max_duration_minutes'] = 24 * 60 if duration <= 0 else 0
    out['_youtube_playlist_runtime'] = True
    out['_youtube_playlist_original_mode'] = 'youtube_playlist'
    return out


def _restore_order(items, order_ids):
    order_ids = [str(x) for x in (order_ids or []) if x]
    if not order_ids:
        return list(items)
    by_id = {str(x.get('id') or ''): x for x in items if x.get('id')}
    ordered = [by_id[x] for x in order_ids if x in by_id]
    used = {str(x.get('id') or '') for x in ordered}
    ordered.extend(x for x in items if str(x.get('id') or '') not in used)
    return ordered or list(items)


def install_playlist_streaming(manager, streaming_module, db_module):
    original_start = manager.start
    original_start_platform = manager._start_platform
    original_status = manager.channel_status
    manager._hs_playlist_start_context = {}

    def mark_session(self, session, ctx):
        items = list(ctx.get('items') or [])
        if not items:
            return
        index = max(0, min(int(ctx.get('index') or 0), len(items) - 1))
        cycle = max(0, int(ctx.get('cycle') or 0))
        elapsed_before = max(0.0, float(ctx.get('elapsed_before') or _elapsed_before_index(items, index, cycle)))
        session._hs_youtube_playlist_items = items
        session._hs_youtube_playlist_index = index
        session._hs_youtube_playlist_cycle = cycle
        session._hs_youtube_playlist_repeat = bool(ctx.get('repeat'))
        session._hs_youtube_playlist_shuffle = bool(ctx.get('shuffle'))
        session.work_channel['_hs_youtube_playlist_active'] = True
        session.work_channel['_hs_youtube_playlist_elapsed_before'] = elapsed_before
        session.work_channel['_hs_youtube_playlist_index'] = index
        session.work_channel['_schedule_source_url'] = items[index]['url']
        session.work_channel['_schedule_source_title'] = items[index].get('title') or 'Vídeo da playlist'
        snapshot = dict(ctx.get('schedule') or {})
        snapshot['source_mode'] = 'youtube_playlist'
        snapshot['playlist_runtime_index'] = index
        snapshot['playlist_runtime_cycle'] = cycle
        snapshot['playlist_runtime_elapsed_before'] = elapsed_before
        snapshot['playlist_runtime_order'] = [str(x.get('id') or '') for x in items if x.get('id')]
        session._hs_schedule_snapshot = snapshot

    def start_platform(self, session, slug, recovery=False):
        ctx = self._hs_playlist_start_context.get(str(session.channel_id))
        if ctx and not getattr(session, '_hs_youtube_playlist_items', None):
            mark_session(self, session, ctx)

        items = list(getattr(session, '_hs_youtube_playlist_items', []) or [])
        raw = getattr(self, '_hs_pre_recovery_start_platform', None)
        if not items:
            if (session.work_channel or {}).get('_hs_parallel_session') and raw:
                return raw(session, slug, recovery)
            return original_start_platform(session, slug, recovery)

        ps = session.platform_states.get(slug)
        advanced = False
        total_elapsed = 0.0
        if recovery:
            if (session.work_channel or {}).get('_hs_parallel_session'):
                total_elapsed = max(0.0, float(getattr(session, '_hs_parallel_last_elapsed', 0) or 0))
            else:
                try:
                    from .recovery import get_checkpoint
                    state = get_checkpoint(session.channel_id) or {}
                    total_elapsed = max(0.0, float(state.get('elapsed_seconds') or state.get('position_seconds') or 0))
                except Exception:
                    total_elapsed = 0.0

        if recovery and ps:
            current = int(getattr(session, '_hs_youtube_playlist_index', 0) or 0)
            ps_index = int(getattr(ps, '_hs_playlist_item_index', current) or 0)
            process = getattr(ps, 'process', None)
            returncode = process.poll() if process else None
            item = items[current]
            duration = max(0.0, float(item.get('duration_seconds') or 0))
            started_mono = float(getattr(ps, '_hs_playlist_started_monotonic', 0) or 0)
            played = max(0.0, time.monotonic() - started_mono) if started_mono else 0.0
            natural = returncode == 0 or (duration > 0 and played >= max(1.0, duration - 6.0))
            exhausted = int(getattr(ps, 'retries', 0) or 0) >= 3

            if ps_index == current and (natural or exhausted):
                with self.lock:
                    if int(getattr(session, '_hs_youtube_playlist_index', 0) or 0) == current:
                        next_index = current + 1
                        cycle = int(getattr(session, '_hs_youtube_playlist_cycle', 0) or 0)
                        if next_index >= len(items):
                            if getattr(session, '_hs_youtube_playlist_repeat', False):
                                next_index = 0
                                cycle += 1
                            else:
                                session.stop_requested = True
                                session.desired_running = False
                                threading.Thread(
                                    target=self.stop,
                                    args=(session.channel_id, 'playlist do YouTube concluída'),
                                    daemon=True,
                                    name=f'yt-playlist-stop-{session.channel_id}',
                                ).start()
                                return True
                        elapsed_before = _elapsed_before_index(items, next_index, cycle)
                        session._hs_youtube_playlist_index = next_index
                        session._hs_youtube_playlist_cycle = cycle
                        session.work_channel['_hs_youtube_playlist_index'] = next_index
                        session.work_channel['_hs_youtube_playlist_elapsed_before'] = elapsed_before
                        session.work_channel['_schedule_source_url'] = items[next_index]['url']
                        session.work_channel['_schedule_source_title'] = items[next_index].get('title') or 'Vídeo da playlist'
                        session.work_channel.pop('_hs_remote_inputs', None)
                        snap = dict(getattr(session, '_hs_schedule_snapshot', {}) or {})
                        snap['playlist_runtime_index'] = next_index
                        snap['playlist_runtime_cycle'] = cycle
                        snap['playlist_runtime_elapsed_before'] = elapsed_before
                        session._hs_schedule_snapshot = snap
                        total_elapsed = elapsed_before
                        advanced = True
                        try:
                            streaming_module.audit(
                                'warning' if exhausted and not natural else 'info', 'youtube_playlist_advanced', session.channel_id,
                                f'Playlist avançou para {next_index + 1}/{len(items)}: {items[next_index].get("title") or items[next_index]["url"]}',
                                {'index': next_index, 'count': len(items), 'cycle': cycle, 'skipped_after_errors': bool(exhausted and not natural)},
                            )
                        except Exception:
                            pass

        current = int(getattr(session, '_hs_youtube_playlist_index', 0) or 0)
        elapsed_before = max(0.0, float((session.work_channel or {}).get('_hs_youtube_playlist_elapsed_before') or 0))
        session.work_channel['_schedule_source_url'] = items[current]['url']
        session.work_channel['_schedule_source_title'] = items[current].get('title') or 'Vídeo da playlist'
        session.work_channel.pop('_hs_remote_inputs', None)

        # Playlist recovery is item-aware. Bypass the channel-level recovery wrapper so an
        # accumulated playlist position (e.g. 8h) is never applied as -ss to a 20min item.
        launcher = raw or original_start_platform
        previous_seek = getattr(session, '_hs_active_seek_seconds', 0.0)
        if recovery and not advanced:
            session._hs_active_seek_seconds = max(0.0, total_elapsed - elapsed_before)
        else:
            session._hs_active_seek_seconds = 0.0
        try:
            ok = launcher(session, slug, recovery)
        finally:
            session._hs_active_seek_seconds = previous_seek

        if ok:
            ps = session.platform_states.get(slug)
            if ps:
                ps._hs_playlist_item_index = current
                ps._hs_playlist_started_monotonic = time.monotonic()
                ps.retries = 0
                # Keep the generic recovery checkpoint monotonic across playlist items.
                if not (session.work_channel or {}).get('_hs_parallel_session'):
                    ps._hs_base_position = max(elapsed_before, total_elapsed if recovery and not advanced else elapsed_before)
                    ps._hs_started_monotonic = time.monotonic()
            try:
                streaming_module.audit(
                    'info', 'youtube_playlist_item_started', session.channel_id,
                    f'Playlist YouTube {current + 1}/{len(items)}: {items[current].get("title") or items[current]["url"]}',
                    {'index': current, 'count': len(items), 'url': items[current]['url'], 'recovery': bool(recovery)},
                )
            except Exception:
                pass
        return ok

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
                schedule['source_duration_seconds'] = fresh.get('duration_seconds') or schedule.get('source_duration_seconds') or 0
        except Exception as exc:
            items = get_playlist_items(db_module, sid)
            if not items:
                return False, 'Não foi possível atualizar a playlist do YouTube: ' + str(exc)
            try:
                self.log(cid, 'Falha atualizando playlist; usando snapshot salvo: ' + str(exc))
            except Exception:
                pass

        requested = getattr(self, '_hs_resume_requests', {}).get(str(cid)) or {}
        requested_schedule = dict(requested.get('schedule') or {})
        order = requested_schedule.get('playlist_runtime_order') or schedule.get('playlist_runtime_order') or []
        if order:
            items = _restore_order(items, order)
        elif schedule.get('shuffle'):
            random.shuffle(items)

        index = int(requested_schedule.get('playlist_runtime_index', schedule.get('playlist_runtime_index', 0)) or 0)
        cycle = int(requested_schedule.get('playlist_runtime_cycle', schedule.get('playlist_runtime_cycle', 0)) or 0)
        index = max(0, min(index, len(items) - 1))
        elapsed_before = float(requested_schedule.get('playlist_runtime_elapsed_before', schedule.get('playlist_runtime_elapsed_before', 0)) or 0)
        if elapsed_before <= 0:
            elapsed_before = _elapsed_before_index(items, index, cycle)

        ctx = {
            'items': items, 'index': index, 'cycle': cycle, 'elapsed_before': elapsed_before,
            'repeat': bool(schedule.get('repeat_playlist')), 'shuffle': bool(schedule.get('shuffle')), 'schedule': dict(schedule),
        }
        self._hs_playlist_start_context[str(cid)] = ctx
        synthetic = _runtime_schedule(schedule, items[index])
        try:
            ok, msg = original_start(cid, platforms, [], trigger, synthetic)
        finally:
            self._hs_playlist_start_context.pop(str(cid), None)
        if not ok:
            return ok, msg

        total_limit = _total_limit(schedule, items)
        with self.lock:
            session = self.sessions.get(str(cid)) or self.sessions.get(cid)
            if session:
                mark_session(self, session, ctx)
                session._hs_total_duration_limit = total_limit
                # On boot recovery the generic engine already reduced max_duration to the
                # remaining time. Preserve that; on a fresh start expand from item duration
                # to the whole playlist duration.
                if not requested:
                    session.max_duration_seconds = total_limit
                    session.stop_at = (now_dt() + timedelta(seconds=total_limit)).isoformat()
        try:
            streaming_module.update_schedule_status(sid, last_started_at=now_iso(), last_status=f'Playlist YouTube iniciada: {len(items)} vídeo(s).')
            streaming_module.audit(
                'info', 'youtube_playlist_started', cid, f'Playlist YouTube iniciada com {len(items)} vídeo(s).',
                {'schedule_id': sid, 'count': len(items), 'start_index': index, 'repeat': bool(schedule.get('repeat_playlist'))},
            )
        except Exception:
            pass
        return True, f'Playlist YouTube iniciada: {len(items)} vídeo(s).'

    def status(self, cid):
        data = original_status(cid)
        with self.lock:
            session = self.sessions.get(cid)
            if session and getattr(session, '_hs_youtube_playlist_items', None):
                items = session._hs_youtube_playlist_items
                index = int(getattr(session, '_hs_youtube_playlist_index', 0) or 0)
                data['youtube_playlist'] = {
                    'active': True, 'index': index, 'position': index + 1, 'count': len(items),
                    'cycle': int(getattr(session, '_hs_youtube_playlist_cycle', 0) or 0),
                    'title': items[index].get('title') or '', 'url': items[index].get('url') or '',
                    'repeat': bool(getattr(session, '_hs_youtube_playlist_repeat', False)),
                }
        return data

    manager._start_platform = MethodType(start_platform, manager)
    manager.start = MethodType(start, manager)
    manager.channel_status = MethodType(status, manager)


def install_youtube_playlist(app, db_module, web_module, scheduler_module, manager, streaming_module):
    global DB, MANAGER
    DB = db_module
    MANAGER = manager
    install_playlist_db(db_module, web_module, scheduler_module)
    install_playlist_streaming(manager, streaming_module, db_module)


@playlist_bp.route('/api/media/probe-youtube-playlist', methods=['POST'])
def api_probe_playlist():
    body = request.get_json(silent=True) or request.form
    try:
        data = probe_youtube_playlist(body.get('url', ''))
        preview = dict(data)
        preview['items'] = data['items'][:100]
        return jsonify(preview)
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc), 'ytdlp': ytdlp_status()}), 400


@playlist_bp.route('/schedules/save-youtube-playlist', methods=['POST'])
def save_youtube_playlist_schedule():
    cid = request.form.get('channel_id', '')
    ch = DB.get_channel(cid, False) if DB else None
    if not ch:
        flash('Canal inválido.', 'error')
        return redirect(url_for('web.schedules'))

    source_url = request.form.get('playlist_url', '').strip()
    try:
        meta = probe_youtube_playlist(source_url)
    except Exception as exc:
        flash('Não foi possível analisar a playlist: ' + str(exc), 'error')
        return redirect(request.referrer or url_for('web.schedules'))

    data = {
        'id': request.form.get('id', '') or None,
        'channel_id': cid,
        'name': request.form.get('name', '').strip(),
        'kind': request.form.get('kind', 'weekly'),
        'weekdays': request.form.getlist('weekdays'),
        'time': request.form.get('time', ''),
        'run_date': request.form.get('run_date', ''),
        'start_date': request.form.get('start_date', ''),
        'end_date': request.form.get('end_date', ''),
        'enabled': request.form.get('enabled') == 'on',
        'conflict_policy': request.form.get('conflict_policy', 'skip'),
        'stop_before_seconds': int(request.form.get('stop_before_seconds', '60') or 0),
        'platforms': request.form.getlist('platforms'),
        'media': [],
        'shuffle': request.form.get('playlist_shuffle') == 'on',
        'repeat_playlist': request.form.get('playlist_repeat') == 'on',
        'max_duration_minutes': int(request.form.get('max_duration_minutes', '0') or 0),
        # Reuse the proven URL schedule storage/validation, then change only the source type.
        'source_mode': 'url',
        'source_url': source_url,
        'source_title': str(meta.get('title') or 'Playlist do YouTube')[:500],
        'source_duration_seconds': max(1.0, float(meta.get('duration_seconds') or 1)),
        'source_extractor': str(meta.get('extractor') or 'youtube:playlist')[:120],
        'source_preview_url': '',
    }
    try:
        sid = DB.save_schedule(data)
        replace_playlist_items(DB, sid, list(meta.get('items') or []))
        with DB.connect() as con:
            con.execute(
                "UPDATE schedules SET source_mode='youtube_playlist',shuffle=?,repeat_playlist=?,source_duration_seconds=?,updated_at=? WHERE id=?",
                (int(bool(data['shuffle'])), int(bool(data['repeat_playlist'])), max(0.0, float(meta.get('duration_seconds') or 0)), now_iso(), sid),
            )
        DB.audit(
            'info', 'youtube_playlist_saved', cid,
            f'Playlist YouTube salva: {sid} · {len(meta.get("items") or [])} vídeo(s).',
            {'schedule_id': sid, 'count': len(meta.get('items') or []), 'url': source_url},
        )
        flash(f'Playlist YouTube salva com {len(meta.get("items") or [])} vídeo(s).', 'success')
        return redirect(url_for('web.schedule_edit', sid=sid))
    except Exception as exc:
        flash(str(exc), 'error')
        return redirect(request.referrer or url_for('web.schedules'))
