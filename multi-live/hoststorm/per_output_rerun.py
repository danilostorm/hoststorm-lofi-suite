from __future__ import annotations

import json
import threading
import time
from types import MethodType

from flask import Blueprint, abort, jsonify, request


output_rerun_bp = Blueprint('output_rerun', __name__)
DB = None
WEB = None
STREAMING = None
MANAGER = None


def _truthy(value) -> bool:
    return str(value or '').strip().lower() in {'1', 'true', 'yes', 'on', 'sim'}


def _safe_seconds(value) -> float:
    try:
        return max(0.0, min(24 * 3600.0, float(value or 0)))
    except Exception:
        return 0.0


def output_rerun_enabled(channel: dict | None, slug: str, destination: dict | None = None) -> bool:
    """Return the per-output rerun policy, falling back to the legacy channel setting.

    The fallback keeps channels created before v4.8 behaving exactly as before. As soon as
    an output is saved from the new card UI it owns an explicit independent value.
    """
    channel = channel or {}
    destination = destination or ((channel.get('destinations') or {}).get(slug) or {})
    if 'output_rerun_enabled' in destination:
        return _truthy(destination.get('output_rerun_enabled'))
    return _truthy(channel.get('rerun_enabled'))


def output_rerun_start_seconds(channel: dict | None, slug: str, destination: dict | None = None) -> float:
    channel = channel or {}
    destination = destination or ((channel.get('destinations') or {}).get(slug) or {})
    if 'output_rerun_start_seconds' in destination:
        return _safe_seconds(destination.get('output_rerun_start_seconds'))
    return _safe_seconds(channel.get('rerun_start_seconds'))


def rewrite_input_args(args: list[str], *, enabled: bool, cycle: int, start_seconds: float) -> list[str]:
    """Turn FFmpeg's infinite local loop into supervised reruns with an optional seek.

    First play starts at 00:00. After a natural EOF, the rerun supervisor starts the same
    destination again and this function inserts ``-ss`` before the first media input.
    """
    result = list(args or [])
    if not enabled:
        return result

    cleaned = []
    i = 0
    while i < len(result):
        if result[i] == '-stream_loop' and i + 1 < len(result) and str(result[i + 1]) == '-1':
            i += 2
            continue
        cleaned.append(result[i])
        i += 1
    result = cleaned

    seek = _safe_seconds(start_seconds)
    if int(cycle or 0) <= 0 or seek <= 0.05:
        return result
    for index, token in enumerate(result):
        if token == '-i':
            return result[:index] + ['-ss', f'{seek:.3f}'] + result[index:]
    return result


def _load_settings(raw) -> dict:
    try:
        data = json.loads(raw or '{}')
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_output_rerun(cid: str, slug: str, enabled: bool, start_seconds: float) -> bool:
    if not DB:
        return False
    with DB.connect() as con:
        row = con.execute(
            'SELECT settings_json FROM destinations WHERE channel_id=? AND slug=?',
            (str(cid), str(slug)),
        ).fetchone()
        if not row:
            return False
        settings = _load_settings(row['settings_json'])
        settings['output_rerun_enabled'] = bool(enabled)
        settings['output_rerun_start_seconds'] = _safe_seconds(start_seconds)
        con.execute(
            'UPDATE destinations SET settings_json=? WHERE channel_id=? AND slug=?',
            (json.dumps(settings, ensure_ascii=False), str(cid), str(slug)),
        )
    return True


def _payload_value(name, default=''):
    payload = request.get_json(silent=True) if request.is_json else request.form
    if not payload:
        return default
    return payload.get(name, default)


@output_rerun_bp.route('/api/output-rerun/<cid>')
def output_rerun_settings(cid):
    channel = WEB.get_channel(cid, False) if WEB else None
    if not channel:
        abort(404)
    outputs = {}
    for slug, destination in (channel.get('destinations') or {}).items():
        outputs[slug] = {
            'enabled': output_rerun_enabled(channel, slug, destination),
            'start_seconds': output_rerun_start_seconds(channel, slug, destination),
            'explicit': 'output_rerun_enabled' in destination,
        }
    return jsonify({'ok': True, 'channel_id': cid, 'outputs': outputs})


@output_rerun_bp.route('/lives/<cid>/outputs/<slug>/rerun', methods=['POST'])
def save_output_rerun(cid, slug):
    channel = WEB.get_channel(cid, False) if WEB else None
    if not channel:
        abort(404)
    destination = (channel.get('destinations') or {}).get(slug)
    if not destination:
        return jsonify({'ok': False, 'message': 'Destino não encontrado.'}), 404

    enabled = _truthy(_payload_value('enabled', '0'))
    start_seconds = _safe_seconds(_payload_value('start_seconds', '0'))
    if not _save_output_rerun(cid, slug, enabled, start_seconds):
        return jsonify({'ok': False, 'message': 'Não foi possível salvar o rerun desta saída.'}), 500

    running = False
    try:
        running = bool((((MANAGER.channel_status(cid) or {}).get('platforms') or {}).get(slug) or {}).get('running'))
    except Exception:
        pass
    suffix = ' Reinicie somente esta saída para aplicar.' if running else ''
    message = (
        f'Rerun desta saída ativo; próximas repetições começam em {int(start_seconds)}s.'
        if enabled else 'Rerun personalizado desta saída desativado.'
    )
    try:
        STREAMING.audit(
            'info', 'output_rerun_saved', cid, message,
            {'platform': slug, 'enabled': enabled, 'start_seconds': start_seconds},
        )
    except Exception:
        pass
    return jsonify({
        'ok': True,
        'message': message + suffix,
        'enabled': enabled,
        'start_seconds': start_seconds,
        'running': running,
    })


def install_per_output_rerun(app, db_module, web_module, streaming_module):
    """Replace channel-wide manual rerun behaviour with a policy owned by each output."""
    global DB, WEB, STREAMING, MANAGER
    DB, WEB, STREAMING, MANAGER = db_module, web_module, streaming_module, streaming_module.MANAGER
    manager = MANAGER

    original_input_args = manager._input_args
    original_status = manager.channel_status
    original_start_threads = manager.start_threads

    # multi_output.py owns the legacy rerun worker. create_app starts all threads only after
    # every wrapper is installed, so pre-marking this flag keeps the old global worker off.
    manager._hs_rerun_worker_started = True
    manager._hs_output_rerun_worker_started = False

    def input_args(self, session, vertical=False):
        slug = str(getattr(session, '_hs_active_output_slug', '') or '')
        if session.trigger != 'manual' or not slug:
            return list(original_input_args(session, vertical))

        channel = session.work_channel or {}
        destination = (channel.get('destinations') or {}).get(slug) or {}
        enabled = output_rerun_enabled(channel, slug, destination)
        start_seconds = output_rerun_start_seconds(channel, slug, destination)
        cycles = getattr(session, '_hs_rerun_cycles', {}) or {}
        cycle = int(cycles.get(slug, 0) or 0)

        # Neutralize the legacy channel-wide wrapper while obtaining the normal source args.
        marker = object()
        previous_enabled = channel.get('rerun_enabled', marker)
        previous_start = channel.get('rerun_start_seconds', marker)
        channel['rerun_enabled'] = '0'
        channel['rerun_start_seconds'] = '0'
        try:
            args = list(original_input_args(session, vertical))
        finally:
            if previous_enabled is marker:
                channel.pop('rerun_enabled', None)
            else:
                channel['rerun_enabled'] = previous_enabled
            if previous_start is marker:
                channel.pop('rerun_start_seconds', None)
            else:
                channel['rerun_start_seconds'] = previous_start

        return rewrite_input_args(
            args, enabled=enabled, cycle=cycle, start_seconds=start_seconds,
        )

    def status(self, cid):
        data = original_status(cid)
        channel = WEB.get_channel(cid, False) if WEB else None
        if not channel:
            return data
        with self.lock:
            session = self.sessions.get(cid)
        cycles = dict(getattr(session, '_hs_rerun_cycles', {}) or {}) if session else {}
        outputs = {}
        for slug, destination in (channel.get('destinations') or {}).items():
            outputs[slug] = {
                'enabled': output_rerun_enabled(channel, slug, destination),
                'start_seconds': output_rerun_start_seconds(channel, slug, destination),
                'cycle': int(cycles.get(slug, 0) or 0),
            }
        data['output_rerun'] = outputs
        return data

    def rerun_loop(self):
        while True:
            try:
                with self.lock:
                    sessions = list(self.sessions.values())
                for session in sessions:
                    if session.trigger != 'manual' or session.stop_requested or not session.desired_running:
                        continue
                    channel = session.work_channel or {}
                    for slug in list(session.platforms or []):
                        destination = (channel.get('destinations') or {}).get(slug) or {}
                        if not output_rerun_enabled(channel, slug, destination):
                            continue
                        ps = (session.platform_states or {}).get(slug)
                        process = getattr(ps, 'process', None) if ps else None
                        if not process or process.poll() is None or process.returncode not in (0, None):
                            continue
                        pid = int(getattr(process, 'pid', 0) or 0)
                        if int(getattr(ps, '_hs_last_output_rerun_pid', 0) or 0) == pid:
                            continue
                        ps._hs_last_output_rerun_pid = pid
                        cycles = getattr(session, '_hs_rerun_cycles', {}) or {}
                        cycles[slug] = int(cycles.get(slug, 0) or 0) + 1
                        session._hs_rerun_cycles = cycles
                        ps.retries = 0
                        ps.next_retry_at = 0
                        start_seconds = output_rerun_start_seconds(channel, slug, destination)
                        try:
                            self.log(
                                session.channel_id,
                                f'Rerun {cycles[slug]} de {ps.label}: reiniciando esta saída em {int(start_seconds)}s.',
                            )
                        except Exception:
                            pass
                        if self._start_platform(session, slug, recovery=False):
                            try:
                                STREAMING.audit(
                                    'info', 'output_rerun_started', session.channel_id,
                                    f'Rerun de {slug} iniciado a partir de {start_seconds:.1f}s.',
                                    {
                                        'platform': slug,
                                        'cycle': cycles[slug],
                                        'start_seconds': start_seconds,
                                    },
                                )
                            except Exception:
                                pass
            except Exception as exc:
                try:
                    STREAMING.audit('error', 'output_rerun_supervisor_error', '', str(exc))
                except Exception:
                    pass
            time.sleep(0.7)

    def start_threads(self):
        original_start_threads()
        if self._hs_output_rerun_worker_started:
            return
        self._hs_output_rerun_worker_started = True
        threading.Thread(
            target=rerun_loop, args=(self,), daemon=True, name='per-output-rerun-supervisor',
        ).start()

    manager._input_args = MethodType(input_args, manager)
    manager.channel_status = MethodType(status, manager)
    manager.start_threads = MethodType(start_threads, manager)
    app.register_blueprint(output_rerun_bp)
    return manager
