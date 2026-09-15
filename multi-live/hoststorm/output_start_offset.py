from __future__ import annotations

import json
from types import MethodType

from flask import Blueprint, abort, jsonify, request


output_start_bp = Blueprint('output_start', __name__)
DB = None
WEB = None
STREAMING = None
MANAGER = None


def _safe_seconds(value) -> float:
    try:
        return max(0.0, min(24 * 3600.0, float(value or 0)))
    except Exception:
        return 0.0


def _load_settings(raw) -> dict:
    try:
        value = json.loads(raw or '{}')
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def output_initial_start_seconds(channel: dict | None, slug: str, destination: dict | None = None) -> float:
    channel = channel or {}
    destination = destination or ((channel.get('destinations') or {}).get(slug) or {})
    return _safe_seconds(destination.get('output_initial_start_seconds'))


def rewrite_initial_start_args(
    args: list[str], *, start_seconds: float, first_start: bool, recovery: bool = False,
) -> list[str]:
    """Apply the per-output initial seek only to a fresh manual start.

    Recovery/reconnects deliberately skip this seek so an existing recovery/checkpoint policy
    is not replaced by the user's initial-start preference. Rerun cycles are also excluded by
    the caller, allowing the independent rerun offset to own second and later plays.
    """
    result = list(args or [])
    seek = _safe_seconds(start_seconds)
    if not first_start or recovery or seek <= 0.05:
        return result

    try:
        input_index = result.index('-i')
    except ValueError:
        return result

    # Another recovery/source wrapper may already have placed a seek before the input. In
    # that case it is more specific than the initial-start preference and must win.
    if '-ss' in result[:input_index]:
        return result
    return result[:input_index] + ['-ss', f'{seek:.3f}'] + result[input_index:]


def _save_output_start(cid: str, slug: str, start_seconds: float) -> bool:
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
        settings['output_initial_start_seconds'] = _safe_seconds(start_seconds)
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


@output_start_bp.route('/api/output-start/<cid>')
def output_start_settings(cid):
    channel = WEB.get_channel(cid, False) if WEB else None
    if not channel:
        abort(404)
    outputs = {
        slug: {'start_seconds': output_initial_start_seconds(channel, slug, destination)}
        for slug, destination in (channel.get('destinations') or {}).items()
    }
    return jsonify({'ok': True, 'channel_id': cid, 'outputs': outputs})


@output_start_bp.route('/lives/<cid>/outputs/<slug>/start-offset', methods=['POST'])
def save_output_start(cid, slug):
    channel = WEB.get_channel(cid, False) if WEB else None
    if not channel:
        abort(404)
    destination = (channel.get('destinations') or {}).get(slug)
    if not destination:
        return jsonify({'ok': False, 'message': 'Destino não encontrado.'}), 404

    start_seconds = _safe_seconds(_payload_value('start_seconds', '0'))
    if not _save_output_start(cid, slug, start_seconds):
        return jsonify({'ok': False, 'message': 'Não foi possível salvar o ponto inicial desta saída.'}), 500

    running = False
    try:
        running = bool((((MANAGER.channel_status(cid) or {}).get('platforms') or {}).get(slug) or {}).get('running'))
    except Exception:
        pass
    suffix = ' Vale no próximo início desta saída.' if running else ''
    message = (
        f'Esta saída iniciará em {int(start_seconds)}s.' if start_seconds > 0.05
        else 'Esta saída iniciará normalmente em 00:00.'
    )
    try:
        STREAMING.audit(
            'info', 'output_initial_start_saved', cid, message,
            {'platform': slug, 'start_seconds': start_seconds},
        )
    except Exception:
        pass
    return jsonify({
        'ok': True,
        'message': message + suffix,
        'start_seconds': start_seconds,
        'running': running,
    })


def install_output_start_offset(app, db_module, web_module, streaming_module):
    """Install an independent initial playback offset for every manual RTMP output."""
    global DB, WEB, STREAMING, MANAGER
    DB, WEB, STREAMING, MANAGER = db_module, web_module, streaming_module, streaming_module.MANAGER
    manager = MANAGER

    original_input_args = manager._input_args
    original_start_platform = manager._start_platform
    original_status = manager.channel_status

    def start_platform(self, session, slug, recovery=False):
        marker = object()
        previous = getattr(session, '_hs_output_start_context', marker)
        session._hs_output_start_context = {'slug': str(slug), 'recovery': bool(recovery)}
        try:
            return original_start_platform(session, slug, recovery)
        finally:
            if previous is marker:
                try:
                    delattr(session, '_hs_output_start_context')
                except Exception:
                    pass
            else:
                session._hs_output_start_context = previous

    def input_args(self, session, vertical=False):
        args = list(original_input_args(session, vertical))
        if str(getattr(session, 'trigger', '')) != 'manual':
            return args
        context = getattr(session, '_hs_output_start_context', {}) or {}
        slug = str(context.get('slug') or getattr(session, '_hs_active_output_slug', '') or '')
        if not slug:
            return args

        channel = session.work_channel or {}
        destination = (channel.get('destinations') or {}).get(slug) or {}
        cycles = getattr(session, '_hs_rerun_cycles', {}) or {}
        first_start = int(cycles.get(slug, 0) or 0) <= 0
        return rewrite_initial_start_args(
            args,
            start_seconds=output_initial_start_seconds(channel, slug, destination),
            first_start=first_start,
            recovery=bool(context.get('recovery')),
        )

    def status(self, cid):
        data = original_status(cid)
        channel = WEB.get_channel(cid, False) if WEB else None
        if not channel:
            return data
        data['output_initial_start'] = {
            slug: output_initial_start_seconds(channel, slug, destination)
            for slug, destination in (channel.get('destinations') or {}).items()
        }
        return data

    manager._start_platform = MethodType(start_platform, manager)
    manager._input_args = MethodType(input_args, manager)
    manager.channel_status = MethodType(status, manager)
    app.register_blueprint(output_start_bp)
    return manager
