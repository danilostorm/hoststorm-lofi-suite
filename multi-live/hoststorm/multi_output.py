from __future__ import annotations

import copy
import json
import signal
import threading
import time
import uuid
from pathlib import Path
from types import MethodType

import psutil

from flask import Blueprint, abort, flash, jsonify, redirect, request, url_for

from .config import DEFAULT_CHANNEL_SETTINGS, DEFAULT_DESTINATIONS
from .utils import build_target, now_iso


multi_output_bp = Blueprint('multi_output', __name__)
DB = None
WEB = None
STREAMING = None
MANAGER = None

BASE_PLATFORMS = tuple(DEFAULT_DESTINATIONS.keys())


def platform_kind(slug: str, destination: dict | None = None) -> str:
    destination = destination or {}
    explicit = str(destination.get('platform') or '').strip()
    if explicit in DEFAULT_DESTINATIONS:
        return explicit
    slug = str(slug or '')
    if slug in DEFAULT_DESTINATIONS:
        return slug
    prefix = slug.split('__', 1)[0]
    return prefix if prefix in DEFAULT_DESTINATIONS else slug


def is_extra_output(slug: str, destination: dict | None = None) -> bool:
    destination = destination or {}
    return bool(destination.get('is_extra')) or '__' in str(slug or '')


def _truthy(value) -> bool:
    return str(value or '').strip().lower() in {'1', 'true', 'yes', 'on', 'sim'}


def _safe_seconds(value) -> float:
    try:
        return max(0.0, min(24 * 3600.0, float(value or 0)))
    except Exception:
        return 0.0


def _channel_settings(channel: dict) -> dict:
    reserved = {
        'id', 'name', 'desired_running', 'created_at', 'updated_at', 'destinations', 'schedules',
        'runtime', 'running', 'active_platforms', 'owner_user_id',
    }
    settings = {k: v for k, v in channel.items() if k not in reserved}
    for key, default in DEFAULT_CHANNEL_SETTINGS.items():
        settings.setdefault(key, default)
    return settings


def _running(process) -> bool:
    return bool(process and process.poll() is None)


def _kill(process):
    if not _running(process):
        return
    try:
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=4)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def _kill_orphan_publishers(cid: str, slug: str) -> int:
    """Kill FFmpeg publishers for this exact RTMP target that are no longer tracked.

    A startup race in older versions could leave one publisher alive after its PlatformState
    had been replaced. Matching the exact target (including stream key) keeps the cleanup
    scoped to one destination and gives 'Parar só esta' a reliable emergency path.
    """
    channel = WEB.get_channel(cid, False) if WEB else None
    destination = ((channel or {}).get('destinations') or {}).get(slug) or {}
    target = build_target(destination.get('rtmp_url'), destination.get('stream_key'))
    if not target:
        return 0
    killed = 0
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            name = str((proc.info or {}).get('name') or '').lower()
            cmdline = [str(x) for x in ((proc.info or {}).get('cmdline') or [])]
            if 'ffmpeg' not in name and not any('ffmpeg' in x.lower() for x in cmdline[:1]):
                continue
            if target not in cmdline and target not in ' '.join(cmdline):
                continue
            proc.terminate()
            try:
                proc.wait(timeout=4)
            except psutil.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
            killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        except Exception:
            continue
    return killed

def _update_run_platforms(run_id: str, platforms: list[str]):
    if not DB or not run_id:
        return
    try:
        with DB.connect() as con:
            con.execute(
                'UPDATE live_runs SET platforms_json=? WHERE id=?',
                (json.dumps(list(dict.fromkeys(platforms)), ensure_ascii=False), run_id),
            )
    except Exception:
        pass


def _finish_core_session(manager, session, reason: str):
    session.stop_requested = True
    session.desired_running = False
    try:
        STREAMING.finish_live_run(session.run_id, 'finished', reason)
        if session.schedule_id:
            STREAMING.update_schedule_status(
                session.schedule_id, last_finished_at=now_iso(), last_status='Finalizada: ' + reason,
            )
        if session.trigger == 'manual':
            STREAMING.set_desired_running(session.channel_id, False)
    except Exception:
        pass
    with manager.lock:
        if manager.sessions.get(session.channel_id) is session:
            manager.sessions.pop(session.channel_id, None)
    if session.playlist_path:
        try:
            Path(session.playlist_path).unlink(missing_ok=True)
        except Exception:
            pass
    try:
        STREAMING.audit('info', 'live_stopped', session.channel_id, reason, {'run_id': session.run_id})
        STREAMING.BUS.publish('live_stopped', {'channel_id': session.channel_id, 'run_id': session.run_id, 'reason': reason})
    except Exception:
        pass


def _stop_parallel_session(manager, session, reason: str):
    session.stop_requested = True
    session.desired_running = False
    ctx = getattr(manager, '_hs_seamless_runs', {}).get(session.run_id) if hasattr(manager, '_hs_seamless_runs') else None
    if ctx:
        try:
            ctx['stop'].set()
            _kill(ctx.get('feeder'))
        except Exception:
            pass
    try:
        STREAMING.finish_live_run(session.run_id, 'finished', reason)
        if session.schedule_id:
            STREAMING.update_schedule_status(
                session.schedule_id, last_finished_at=now_iso(), last_status='Finalizada: ' + reason,
            )
    except Exception:
        pass
    try:
        with DB.connect() as con:
            con.execute(
                "UPDATE parallel_schedule_state SET status='stopped',resume_enabled=0,updated_at=? WHERE run_id=?",
                (now_iso(), session.run_id),
            )
    except Exception:
        pass
    with manager.lock:
        if hasattr(manager, '_hs_parallel_sessions'):
            manager._hs_parallel_sessions.pop(session.run_id, None)
    if ctx and hasattr(manager, '_hs_seamless_runs'):
        manager._hs_seamless_runs.pop(session.run_id, None)


def _stop_output(manager, cid: str, slug: str, reason='parada individual') -> tuple[bool, str]:
    stopped = False
    with manager.lock:
        core = manager.sessions.get(cid)
    if core and slug in (core.platform_states or {}):
        ps = core.platform_states.get(slug)
        _kill(getattr(ps, 'process', None))
        if ps:
            ps.next_retry_at = 0
            ps.last_error = ''
        core.platforms = [p for p in list(core.platforms or []) if p != slug]
        try:
            STREAMING.upsert_platform_run(core.run_id, slug, status='stopped', ended_at=now_iso(), pid=0)
        except Exception:
            pass
        _update_run_platforms(core.run_id, core.platforms)
        stopped = True
        if not any(_running(getattr(core.platform_states.get(p), 'process', None)) for p in core.platforms):
            _finish_core_session(manager, core, reason)

    for session in list(getattr(manager, '_hs_parallel_sessions', {}).values()):
        if str(session.channel_id) != str(cid) or slug not in (session.platform_states or {}):
            continue
        ps = session.platform_states.get(slug)
        _kill(getattr(ps, 'process', None))
        if ps:
            ps.next_retry_at = 0
            ps.last_error = ''
        session.platforms = [p for p in list(session.platforms or []) if p != slug]
        try:
            STREAMING.upsert_platform_run(session.run_id, slug, status='stopped', ended_at=now_iso(), pid=0)
        except Exception:
            pass
        _update_run_platforms(session.run_id, session.platforms)
        ctx = getattr(manager, '_hs_seamless_runs', {}).get(session.run_id) if hasattr(manager, '_hs_seamless_runs') else None
        if ctx:
            ctx['platforms'] = list(session.platforms)
            for name in ('bridge_urls', 'feed_urls'):
                if isinstance(ctx.get(name), dict):
                    ctx[name].pop(slug, None)
        stopped = True
        if not any(_running(getattr(session.platform_states.get(p), 'process', None)) for p in session.platforms):
            _stop_parallel_session(manager, session, reason)

    orphan_count = _kill_orphan_publishers(cid, slug)
    if orphan_count:
        stopped = True

    if stopped:
        try:
            STREAMING.audit('info', 'platform_stopped_individually', cid, f'Destino {slug} encerrado individualmente.', {'platform': slug, 'orphan_processes': orphan_count})
            STREAMING.BUS.publish('platform_stopped', {'channel_id': cid, 'slug': slug, 'reason': reason})
        except Exception:
            pass
        return True, 'Live deste destino encerrada.'
    return True, 'Este destino já estava parado.'


def _start_output(manager, cid: str, slug: str) -> tuple[bool, str]:
    channel = WEB.get_channel(cid) if WEB else None
    if not channel:
        return False, 'Canal não encontrado.'
    destination = (channel.get('destinations') or {}).get(slug)
    if not destination:
        return False, 'Destino não encontrado.'
    if not build_target(destination.get('rtmp_url'), destination.get('stream_key')):
        return False, 'Configure a URL RTMP e a chave deste destino antes de iniciar.'
    status = manager.channel_status(cid)
    if (status.get('platforms') or {}).get(slug, {}).get('running'):
        return True, 'Este destino já está ao vivo.'

    with manager.lock:
        session = manager.sessions.get(cid)
    if session and not session.stop_requested and session.desired_running:
        session.work_channel.setdefault('destinations', {})[slug] = copy.deepcopy(destination)
        if slug not in session.platforms:
            session.platforms.append(slug)
        if manager._start_platform(session, slug, recovery=False):
            _update_run_platforms(session.run_id, session.platforms)
            try:
                STREAMING.audit('info', 'platform_started_individually', cid, f'Destino {slug} iniciado individualmente.', {'platform': slug})
            except Exception:
                pass
            return True, 'Live deste destino iniciada.'
        session.platforms = [p for p in session.platforms if p != slug]
        return False, 'Não foi possível iniciar este destino.'

    return manager.start(cid, platforms=[slug], trigger='manual')


@multi_output_bp.route('/lives/<cid>/outputs/add', methods=['POST'])
def add_output(cid):
    channel = WEB.get_channel(cid) if WEB else None
    if not channel:
        abort(404)
    platform = str(request.form.get('platform') or '').strip()
    if platform not in DEFAULT_DESTINATIONS:
        flash('Plataforma inválida.', 'error')
        return redirect(url_for('web.live_edit', cid=cid))
    existing = [
        (slug, d) for slug, d in (channel.get('destinations') or {}).items()
        if platform_kind(slug, d) == platform
    ]
    slug = f'{platform}__{uuid.uuid4().hex[:8]}'
    base = copy.deepcopy(DEFAULT_DESTINATIONS[platform])
    custom_label = str(request.form.get('label') or '').strip()[:80]
    base.update({
        'label': custom_label or f"{base.get('label', platform)} #{len(existing) + 1}",
        'enabled': False,
        'stream_key': '',
        'platform': platform,
        'is_extra': True,
        'dedicated': True,
    })
    destinations = copy.deepcopy(channel.get('destinations') or {})
    destinations[slug] = base
    WEB.save_channel(cid, channel.get('name') or 'Canal', _channel_settings(channel), destinations)
    flash(f"Novo destino criado: {base['label']}. Informe a chave e salve as configurações.", 'success')
    return redirect(url_for('web.live_edit', cid=cid) + '#destinos')


@multi_output_bp.route('/lives/<cid>/outputs/<slug>/delete', methods=['POST'])
def delete_output(cid, slug):
    channel = WEB.get_channel(cid) if WEB else None
    if not channel:
        abort(404)
    destination = (channel.get('destinations') or {}).get(slug)
    if not destination or not is_extra_output(slug, destination):
        flash('Somente destinos adicionais podem ser removidos.', 'error')
        return redirect(url_for('web.live_edit', cid=cid))
    schedules = WEB.list_schedules(cid) if WEB else []
    used = [s for s in schedules if slug in (s.get('platforms') or [])]
    if used:
        flash(f'Este destino está usado em {len(used)} agendamento(s). Remova-o das agendas antes de excluir.', 'error')
        return redirect(url_for('web.live_edit', cid=cid))
    _stop_output(MANAGER, cid, slug, 'destino adicional removido')
    with DB.connect() as con:
        con.execute('DELETE FROM destinations WHERE channel_id=? AND slug=?', (cid, slug))
    flash('Destino adicional removido.', 'success')
    return redirect(url_for('web.live_edit', cid=cid))


@multi_output_bp.route('/lives/<cid>/outputs/<slug>/start', methods=['POST'])
def start_output(cid, slug):
    channel = WEB.get_channel(cid) if WEB else None
    if not channel or slug not in (channel.get('destinations') or {}):
        abort(404)
    ok, message = _start_output(MANAGER, cid, slug)
    flash(message, 'success' if ok else 'error')
    return redirect(url_for('web.live_edit', cid=cid) + '#destinos')


@multi_output_bp.route('/lives/<cid>/outputs/<slug>/stop', methods=['POST'])
def stop_output(cid, slug):
    channel = WEB.get_channel(cid) if WEB else None
    if not channel or slug not in (channel.get('destinations') or {}):
        abort(404)
    ok, message = _stop_output(MANAGER, cid, slug)
    flash(message, 'success' if ok else 'error')
    return redirect(url_for('web.live_edit', cid=cid) + '#destinos')


@multi_output_bp.route('/api/output-labels')
def output_labels():
    channels = WEB.list_channels(False) if WEB else {}
    data = {}
    for cid, channel in channels.items():
        data[cid] = {
            slug: {
                'label': str(destination.get('label') or slug),
                'platform': platform_kind(slug, destination),
                'extra': is_extra_output(slug, destination),
            }
            for slug, destination in (channel.get('destinations') or {}).items()
        }
    return jsonify({'ok': True, 'channels': data})


def install_multi_output(app, db_module, web_module, streaming_module):
    """Install multiple RTMP outputs per platform, individual controls and rerun offsets."""
    global DB, WEB, STREAMING, MANAGER
    DB, WEB, STREAMING, MANAGER = db_module, web_module, streaming_module, streaming_module.MANAGER
    manager = MANAGER

    original_build_cmd = manager._build_cmd
    original_input_args = manager._input_args
    original_start_platform = manager._start_platform
    original_status = manager.channel_status
    original_start_threads = manager.start_threads

    manager._hs_rerun_worker_started = False

    def build_cmd(self, session, slug):
        destination = (session.work_channel.get('destinations') or {}).get(slug) or {}
        base = platform_kind(slug, destination)
        if base == slug or base not in DEFAULT_DESTINATIONS:
            return original_build_cmd(session, slug)

        shadow = copy.copy(session)
        shadow.work_channel = copy.deepcopy(session.work_channel)
        shadow.work_channel.setdefault('destinations', {})[base] = copy.deepcopy(destination)
        shadow.work_channel['destinations'][base]['platform'] = base
        if hasattr(session, '_hs_active_output_slug'):
            shadow._hs_active_output_slug = getattr(session, '_hs_active_output_slug')

        ctx = None
        previous_bridge = None
        marker = object()
        if hasattr(self, '_hs_seamless_runs'):
            ctx = self._hs_seamless_runs.get(str(session.run_id)) or self._hs_seamless_pending.get(str(session.channel_id))
        if ctx and slug in (ctx.get('bridge_urls') or {}):
            previous_bridge = (ctx.get('bridge_urls') or {}).get(base, marker)
            ctx.setdefault('bridge_urls', {})[base] = ctx['bridge_urls'][slug]
        try:
            return original_build_cmd(shadow, base)
        finally:
            if ctx and previous_bridge is not None:
                if previous_bridge is marker:
                    ctx.get('bridge_urls', {}).pop(base, None)
                else:
                    ctx.setdefault('bridge_urls', {})[base] = previous_bridge

    def input_args(self, session, vertical=False):
        args = list(original_input_args(session, vertical))
        channel = session.work_channel or {}
        if session.trigger != 'manual' or not _truthy(channel.get('rerun_enabled')):
            return args

        # Rerun is managed by a lightweight monitor. The first play starts normally;
        # subsequent plays restart from rerun_start_seconds instead of zero.
        cleaned = []
        i = 0
        while i < len(args):
            if args[i] == '-stream_loop' and i + 1 < len(args) and str(args[i + 1]) == '-1':
                i += 2
                continue
            cleaned.append(args[i])
            i += 1
        args = cleaned

        slug = str(getattr(session, '_hs_active_output_slug', '') or '')
        cycles = getattr(session, '_hs_rerun_cycles', {}) or {}
        if int(cycles.get(slug, 0) or 0) <= 0:
            return args
        seek = _safe_seconds(channel.get('rerun_start_seconds'))
        if seek <= 0.05:
            return args
        for index, token in enumerate(args):
            if token == '-i':
                return args[:index] + ['-ss', f'{seek:.3f}'] + args[index:]
        return args

    def start_platform(self, session, slug, recovery=False):
        previous = getattr(session, '_hs_active_output_slug', '')
        session._hs_active_output_slug = slug
        try:
            ok = original_start_platform(session, slug, recovery)
        finally:
            session._hs_active_output_slug = previous
        if ok:
            if not hasattr(session, '_hs_rerun_cycles'):
                session._hs_rerun_cycles = {}
            session._hs_rerun_cycles.setdefault(slug, 0)
        return ok

    def status(self, cid):
        data = original_status(cid)
        channel = WEB.get_channel(cid, False) if WEB else None
        if channel:
            data['output_catalog'] = {
                slug: {
                    'label': str(destination.get('label') or slug),
                    'platform': platform_kind(slug, destination),
                    'extra': is_extra_output(slug, destination),
                }
                for slug, destination in (channel.get('destinations') or {}).items()
            }
        with self.lock:
            session = self.sessions.get(cid)
        if session and _truthy((session.work_channel or {}).get('rerun_enabled')):
            data['rerun'] = {
                'enabled': True,
                'start_seconds': _safe_seconds((session.work_channel or {}).get('rerun_start_seconds')),
                'cycles': dict(getattr(session, '_hs_rerun_cycles', {}) or {}),
            }
        return data

    def rerun_loop(self):
        while True:
            try:
                with self.lock:
                    sessions = list(self.sessions.values())
                for session in sessions:
                    channel = session.work_channel or {}
                    if session.trigger != 'manual' or session.stop_requested or not session.desired_running:
                        continue
                    if not _truthy(channel.get('rerun_enabled')):
                        continue
                    for slug in list(session.platforms or []):
                        ps = (session.platform_states or {}).get(slug)
                        process = getattr(ps, 'process', None) if ps else None
                        if not process or process.poll() is None or process.returncode not in (0, None):
                            continue
                        pid = int(getattr(process, 'pid', 0) or 0)
                        if int(getattr(ps, '_hs_last_rerun_pid', 0) or 0) == pid:
                            continue
                        ps._hs_last_rerun_pid = pid
                        cycles = getattr(session, '_hs_rerun_cycles', {}) or {}
                        cycles[slug] = int(cycles.get(slug, 0) or 0) + 1
                        session._hs_rerun_cycles = cycles
                        ps.retries = 0
                        ps.next_retry_at = 0
                        try:
                            self.log(session.channel_id, f'Rerun {cycles[slug]} de {ps.label}: reiniciando em {int(_safe_seconds(channel.get("rerun_start_seconds")))}s.')
                        except Exception:
                            pass
                        if self._start_platform(session, slug, recovery=False):
                            try:
                                STREAMING.audit(
                                    'info', 'rerun_started', session.channel_id,
                                    f'Rerun iniciado em {slug} a partir de {_safe_seconds(channel.get("rerun_start_seconds")):.1f}s.',
                                    {'platform': slug, 'cycle': cycles[slug], 'start_seconds': _safe_seconds(channel.get('rerun_start_seconds'))},
                                )
                            except Exception:
                                pass
            except Exception as exc:
                try:
                    STREAMING.audit('error', 'rerun_supervisor_error', '', str(exc))
                except Exception:
                    pass
            time.sleep(0.75)

    def start_threads(self):
        original_start_threads()
        if self._hs_rerun_worker_started:
            return
        self._hs_rerun_worker_started = True
        threading.Thread(target=rerun_loop, args=(self,), daemon=True, name='manual-rerun-supervisor').start()

    manager._build_cmd = MethodType(build_cmd, manager)
    manager._input_args = MethodType(input_args, manager)
    manager._start_platform = MethodType(start_platform, manager)
    manager.channel_status = MethodType(status, manager)
    manager.start_threads = MethodType(start_threads, manager)

    app.register_blueprint(multi_output_bp)
    return manager
