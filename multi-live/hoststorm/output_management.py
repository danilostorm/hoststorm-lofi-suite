from __future__ import annotations

import json
import threading
import time
from types import MethodType

from flask import Blueprint, abort, jsonify

from . import multi_output


output_management_bp = Blueprint('output_management', __name__)
DB = None
WEB = None
STREAMING = None
MANAGER = None

_RETRY_DELAYS = (5, 15, 30, 60, 60, 60)


def _json_settings(raw: str | None) -> dict:
    try:
        value = json.loads(raw or '{}')
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _truthy(value) -> bool:
    return str(value or '').strip().lower() in {'1', 'true', 'yes', 'on', 'sim'}


def _set_output_desired(cid: str, slug: str, desired: bool) -> bool:
    """Persist whether one manual destination should be kept online across restarts."""
    if not DB:
        return False
    with DB.connect() as con:
        row = con.execute(
            'SELECT settings_json FROM destinations WHERE channel_id=? AND slug=?',
            (str(cid), str(slug)),
        ).fetchone()
        if not row:
            return False
        settings = _json_settings(row['settings_json'])
        settings['manual_desired_running'] = bool(desired)
        con.execute(
            'UPDATE destinations SET settings_json=? WHERE channel_id=? AND slug=?',
            (json.dumps(settings, ensure_ascii=False), str(cid), str(slug)),
        )
    return True


def _output_desired_state(cid: str, slug: str):
    """Return (is_explicit, desired) for one destination."""
    if not DB:
        return False, False
    with DB.connect() as con:
        row = con.execute(
            'SELECT settings_json FROM destinations WHERE channel_id=? AND slug=?',
            (str(cid), str(slug)),
        ).fetchone()
    if not row:
        return False, False
    settings = _json_settings(row['settings_json'])
    if 'manual_desired_running' not in settings:
        return False, False
    return True, _truthy(settings.get('manual_desired_running'))


def _mark_output_stopped(cid: str, slug: str, reason='parada individual'):
    """Persist an individual stop in both destination state and recovery checkpoint."""
    try:
        _set_output_desired(cid, slug, False)
    except Exception:
        pass
    try:
        from .recovery import remove_platform_from_checkpoint
        remove_platform_from_checkpoint(cid, slug, reason)
    except Exception:
        pass


def _recovery_owns_output(cid: str, slug: str) -> bool:
    """True while channel recovery is responsible for restarting this desired output."""
    try:
        from .recovery import get_checkpoint
        state = get_checkpoint(cid) or {}
    except Exception:
        return False
    if str(state.get('trigger') or 'manual') != 'manual':
        return False
    if not state.get('resume_enabled') or str(state.get('status') or '') not in {'running', 'reconnecting'}:
        return False
    platforms = {str(x) for x in (state.get('platforms') or [])}
    return not platforms or str(slug) in platforms

def _clear_channel_desired(cid: str):
    if not DB:
        return
    with DB.connect() as con:
        rows = con.execute(
            'SELECT slug,settings_json FROM destinations WHERE channel_id=?', (str(cid),)
        ).fetchall()
        for row in rows:
            settings = _json_settings(row['settings_json'])
            if not _truthy(settings.get('manual_desired_running')):
                continue
            settings['manual_desired_running'] = False
            con.execute(
                'UPDATE destinations SET settings_json=? WHERE channel_id=? AND slug=?',
                (json.dumps(settings, ensure_ascii=False), str(cid), row['slug']),
            )


def _desired_outputs() -> list[tuple[str, str]]:
    if not DB:
        return []
    result = []
    with DB.connect() as con:
        rows = con.execute('SELECT channel_id,slug,settings_json FROM destinations').fetchall()
        for row in rows:
            settings = _json_settings(row['settings_json'])
            if _truthy(settings.get('manual_desired_running')):
                result.append((str(row['channel_id']), str(row['slug'])))
    return result


def _seed_desired_from_recovery():
    """Migrate already-running v4.6 manual outputs into per-destination desired state."""
    try:
        from .recovery import list_resumable
        states = list_resumable()
    except Exception:
        return
    for state in states:
        if str(state.get('trigger') or 'manual') != 'manual':
            continue
        cid = str(state.get('channel_id') or '')
        if not cid:
            continue
        channel = WEB.get_channel(cid, False) if WEB else None
        if not channel:
            continue
        destinations = channel.get('destinations') or {}
        for slug in list(state.get('platforms') or []):
            if slug not in destinations:
                continue
            # Migration is allowed only when the flag did not exist in older versions.
            # An explicit False means the user stopped this output and is authoritative.
            explicit, _desired = _output_desired_state(cid, slug)
            if explicit:
                continue
            try:
                _set_output_desired(cid, slug, True)
            except Exception:
                pass


def _json_result(ok: bool, message: str, status=200):
    return jsonify({'ok': bool(ok), 'message': str(message)}), status


@output_management_bp.route('/lives/<cid>/outputs/<slug>/remove', methods=['POST'])
def remove_output(cid, slug):
    """Remove any RTMP destination, including the original/base destinations."""
    channel = WEB.get_channel(cid) if WEB else None
    if not channel:
        abort(404)
    destination = (channel.get('destinations') or {}).get(slug)
    if not destination:
        return _json_result(False, 'Destino não encontrado.', 404)

    schedules = WEB.list_schedules(cid) if WEB else []
    used = [s for s in schedules if slug in (s.get('platforms') or [])]
    if used:
        return _json_result(
            False,
            f'Esta saída está usada em {len(used)} agendamento(s). Remova-a das agendas antes de excluir.',
            409,
        )

    label = str(destination.get('label') or slug)
    try:
        multi_output._stop_output(MANAGER, cid, slug, 'destino RTMP removido')
    except Exception:
        pass
    with DB.connect() as con:
        con.execute('DELETE FROM destinations WHERE channel_id=? AND slug=?', (cid, slug))
    try:
        STREAMING.audit(
            'info', 'destination_deleted', cid,
            f'Destino RTMP removido: {label}', {'platform': slug, 'label': label},
        )
    except Exception:
        pass
    return _json_result(True, f'Saída “{label}” excluída.')


def install_output_management(app, db_module, web_module, streaming_module):
    """Install output deletion, persistent desired state and restart recovery."""
    global DB, WEB, STREAMING, MANAGER
    DB, WEB, STREAMING, MANAGER = db_module, web_module, streaming_module, streaming_module.MANAGER
    manager = MANAGER

    original_start_output = multi_output._start_output
    original_stop_output = multi_output._stop_output
    original_manager_stop = manager.stop
    original_start_threads = manager.start_threads
    manager._hs_output_watchdog_started = False
    manager._hs_per_output_desired_enabled = True

    def start_output(manager_obj, cid: str, slug: str):
        ok, message = original_start_output(manager_obj, cid, slug)
        if ok:
            try:
                _set_output_desired(cid, slug, True)
            except Exception:
                pass
        return ok, message

    def stop_output(manager_obj, cid: str, slug: str, reason='parada individual'):
        _mark_output_stopped(cid, slug, reason)
        return original_stop_output(manager_obj, cid, slug, reason)

    def stop(self, cid, *args, **kwargs):
        reason = str(args[0] if args else kwargs.get('reason', 'manual') or 'manual')
        with self.lock:
            session = self.sessions.get(cid)
            was_manual = bool(session and str(getattr(session, 'trigger', '')) == 'manual')
        result = original_manager_stop(cid, *args, **kwargs)
        # A UI/manual stop must clear persistent output intent even when the in-memory
        # session is already missing/reconnecting. This is the exact ghost-live case.
        manual_request = was_manual or 'manual' in reason.lower() or reason.lower() in {'canal removido', 'destino rtmp removido'}
        if manual_request:
            try:
                _clear_channel_desired(cid)
            except Exception:
                pass
        return result

    def watchdog(self):
        # Start before the legacy 24/7/recovery workers. This is important for channels
        # whose main source is intentionally empty because every destination owns its source.
        time.sleep(1.2)
        _seed_desired_from_recovery()
        attempts: dict[tuple[str, str], int] = {}
        next_attempt: dict[tuple[str, str], float] = {}
        while True:
            try:
                wanted = set(_desired_outputs())
                now = time.time()
                for key in list(attempts):
                    if key not in wanted:
                        attempts.pop(key, None)
                        next_attempt.pop(key, None)

                for cid, slug in wanted:
                    channel = WEB.get_channel(cid, False) if WEB else None
                    if not channel or slug not in (channel.get('destinations') or {}):
                        continue
                    status = manager.channel_status(cid)
                    platform = (status.get('platforms') or {}).get(slug) or {}
                    if platform.get('running'):
                        attempts.pop((cid, slug), None)
                        next_attempt.pop((cid, slug), None)
                        continue
                    # Persistent checkpoint recovery owns manual restarts first. The old
                    # watchdog used to race it after Docker boot, creating duplicate FFmpeg
                    # publishers where one could become invisible to the dashboard.
                    if _recovery_owns_output(cid, slug):
                        continue
                    # The normal FFmpeg supervisor already owns an in-process reconnect.
                    # Do not race it by spawning a second publisher for the same key.
                    if int(platform.get('retries') or 0) > 0:
                        continue
                    if now < float(next_attempt.get((cid, slug), 0) or 0):
                        continue

                    ok, message = multi_output._start_output(manager, cid, slug)
                    if ok:
                        attempts.pop((cid, slug), None)
                        next_attempt.pop((cid, slug), None)
                        try:
                            STREAMING.audit(
                                'info', 'output_auto_recovered', cid,
                                f'Destino {slug} retomado automaticamente.',
                                {'platform': slug, 'reason': 'persistent-desired-state'},
                            )
                            manager.log(cid, f'{slug}: retomado automaticamente pelo watchdog por destino.')
                        except Exception:
                            pass
                    else:
                        attempt = int(attempts.get((cid, slug), 0)) + 1
                        attempts[(cid, slug)] = attempt
                        delay = _RETRY_DELAYS[min(attempt - 1, len(_RETRY_DELAYS) - 1)]
                        next_attempt[(cid, slug)] = now + delay
                        try:
                            STREAMING.audit(
                                'warning', 'output_auto_recovery_pending', cid,
                                f'{slug}: nova tentativa em {delay}s. {message}',
                                {'platform': slug, 'attempt': attempt, 'retry_in': delay},
                            )
                        except Exception:
                            pass
            except Exception as exc:
                try:
                    STREAMING.audit('error', 'output_watchdog_error', '', str(exc))
                except Exception:
                    pass
            time.sleep(2)

    def start_threads(self):
        original_start_threads()
        if self._hs_output_watchdog_started:
            return
        self._hs_output_watchdog_started = True
        threading.Thread(
            target=watchdog, args=(self,), daemon=True, name='output-desired-watchdog'
        ).start()

    multi_output._start_output = start_output
    multi_output._stop_output = stop_output
    manager.stop = MethodType(stop, manager)
    manager.start_threads = MethodType(start_threads, manager)
    app.register_blueprint(output_management_bp)
    return manager
