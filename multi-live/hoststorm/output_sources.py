from __future__ import annotations

import copy
import urllib.parse
from types import MethodType

from flask import Blueprint, abort, jsonify, request

from .config import VIDEOS_DIR
from .utils import build_target, now_iso, safe_filename
from .url_sources import validate_remote_url
from . import multi_output


output_sources_bp = Blueprint('output_sources', __name__)
WEB = None
MANAGER = None
STREAMING = None

SOURCE_MODES = {'channel', 'local', 'url'}


def source_mode(destination: dict | None) -> str:
    destination = destination or {}
    mode = str(destination.get('output_source_mode') or 'channel').strip().lower()
    return mode if mode in SOURCE_MODES else 'channel'


def apply_source_override(channel: dict, destination: dict | None) -> dict:
    """Return a channel snapshot with a destination-specific manual source applied."""
    work = copy.deepcopy(channel or {})
    destination = destination or {}
    mode = source_mode(destination)
    if mode == 'channel':
        return work

    if mode == 'local':
        video = safe_filename(destination.get('output_source_video'))
        work['source_mode'] = 'local'
        work['video'] = video
        # A per-output source must win for vertical destinations as well.
        work['shorts_source_mode'] = 'local'
        work['shorts_video'] = video
        return work

    url = str(destination.get('output_source_url') or '').strip()
    work['source_mode'] = 'url'
    work['source_url'] = url
    work['shorts_source_mode'] = 'url'
    work['shorts_source_url'] = url
    work['shorts_video'] = ''
    return work


def validate_source(destination: dict | None) -> tuple[bool, str]:
    destination = destination or {}
    mode = source_mode(destination)
    if mode == 'channel':
        return True, ''
    if mode == 'local':
        video = safe_filename(destination.get('output_source_video'))
        if not video:
            return False, 'Selecione um vídeo da Biblioteca para esta saída.'
        if not (VIDEOS_DIR / video).is_file():
            return False, f'O vídeo "{video}" não existe mais na Biblioteca.'
        return True, ''

    url = str(destination.get('output_source_url') or '').strip()
    if not url:
        return False, 'Informe a URL externa desta saída.'
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme in {'http', 'https'}:
        try:
            validate_remote_url(url)
        except Exception as exc:
            return False, str(exc)
        return True, ''
    if parsed.scheme in {'rtmp', 'rtmps'} and parsed.netloc:
        return True, ''
    return False, 'Use uma URL http://, https://, rtmp:// ou rtmps:// válida.'


def _json_or_error(ok: bool, message: str, status=200):
    return jsonify({'ok': bool(ok), 'message': str(message)}), status


def _update_destination_from_form(channel: dict, slug: str, include_transport=False) -> tuple[dict | None, str]:
    destination = (channel.get('destinations') or {}).get(slug)
    if not destination:
        return None, 'Destino não encontrado.'

    mode = str(request.form.get('mode') or source_mode(destination)).strip().lower()
    if mode not in SOURCE_MODES:
        return None, 'Tipo de fonte inválido.'

    updated = copy.deepcopy(destination)
    updated['output_source_mode'] = mode
    if mode == 'local':
        updated['output_source_video'] = safe_filename(request.form.get('video'))
        updated['output_source_url'] = ''
    elif mode == 'url':
        updated['output_source_video'] = ''
        updated['output_source_url'] = str(request.form.get('url') or '').strip()
    else:
        updated['output_source_video'] = ''
        updated['output_source_url'] = ''

    if include_transport:
        rtmp_url = str(request.form.get('rtmp_url') or '').strip()
        if rtmp_url:
            updated['rtmp_url'] = rtmp_url
        stream_key = str(request.form.get('stream_key') or '').strip()
        # Empty means "keep the encrypted/current key", matching the main channel form.
        if stream_key:
            updated['stream_key'] = stream_key

    ok, error = validate_source(updated)
    if not ok:
        return None, error
    return updated, ''


def _persist_destination(channel: dict, slug: str, destination: dict):
    destinations = copy.deepcopy(channel.get('destinations') or {})
    destinations[slug] = destination
    WEB.save_channel(
        channel.get('id') or '',
        channel.get('name') or 'Canal',
        multi_output._channel_settings(channel),
        destinations,
    )


@output_sources_bp.route('/api/output-sources/<cid>')
def output_sources(cid):
    if not WEB:
        abort(503)
    channel = WEB.get_channel(cid, False)
    if not channel:
        abort(404)
    videos = sorted(
        [p.name for p in VIDEOS_DIR.iterdir() if p.is_file()],
        key=lambda value: value.casefold(),
    )
    outputs = {}
    for slug, destination in (channel.get('destinations') or {}).items():
        outputs[slug] = {
            'mode': source_mode(destination),
            'video': str(destination.get('output_source_video') or ''),
            'url': str(destination.get('output_source_url') or ''),
            'label': str(destination.get('label') or slug),
        }
    return jsonify({'ok': True, 'channel_id': cid, 'videos': videos, 'outputs': outputs})


@output_sources_bp.route('/lives/<cid>/outputs/<slug>/source', methods=['POST'])
def save_output_source(cid, slug):
    if not WEB:
        abort(503)
    channel = WEB.get_channel(cid)
    if not channel:
        abort(404)
    updated, error = _update_destination_from_form(channel, slug)
    if not updated:
        return _json_or_error(False, error, 400)

    _persist_destination(channel, slug, updated)

    running = False
    try:
        running = bool(((MANAGER.channel_status(cid).get('platforms') or {}).get(slug) or {}).get('running'))
    except Exception:
        pass
    if running:
        return _json_or_error(True, 'Fonte salva. Pare e inicie somente esta saída para aplicar a nova fonte.')
    return _json_or_error(True, 'Fonte desta saída salva.')


@output_sources_bp.route('/lives/<cid>/outputs/<slug>/source/start', methods=['POST'])
def save_and_start_output(cid, slug):
    """Save the card as shown in the browser and start exactly this destination."""
    if not WEB:
        abort(503)
    channel = WEB.get_channel(cid)
    if not channel:
        abort(404)
    updated, error = _update_destination_from_form(channel, slug, include_transport=True)
    if not updated:
        return _json_or_error(False, error, 400)
    if not build_target(updated.get('rtmp_url'), updated.get('stream_key')):
        return _json_or_error(False, 'Informe a URL RTMP e a chave desta saída antes de iniciar.', 400)

    _persist_destination(channel, slug, updated)
    ok, message = multi_output._start_output(MANAGER, cid, slug)
    return _json_or_error(ok, message, 200 if ok else 400)


def install_output_sources(app, web_module, streaming_module):
    """Install independent manual sources per RTMP destination."""
    global WEB, MANAGER, STREAMING
    WEB = web_module
    MANAGER = streaming_module.MANAGER
    STREAMING = streaming_module
    manager = MANAGER

    original_build_cmd = manager._build_cmd
    original_start_output = multi_output._start_output

    def build_cmd(self, session, slug):
        # Scheduled runs deliberately keep using the media selected by the schedule.
        if session.trigger != 'manual':
            return original_build_cmd(session, slug)
        destination = ((session.work_channel or {}).get('destinations') or {}).get(slug) or {}
        if source_mode(destination) == 'channel':
            return original_build_cmd(session, slug)
        ok, error = validate_source(destination)
        if not ok:
            raise RuntimeError(error)
        shadow = copy.copy(session)
        shadow.work_channel = apply_source_override(session.work_channel or {}, destination)
        return original_build_cmd(shadow, slug)

    def start_output(manager_obj, cid: str, slug: str):
        channel = WEB.get_channel(cid) if WEB else None
        if not channel:
            return False, 'Canal não encontrado.'
        destination = (channel.get('destinations') or {}).get(slug)
        if not destination:
            return False, 'Destino não encontrado.'
        if not build_target(destination.get('rtmp_url'), destination.get('stream_key')):
            return False, 'Configure a URL RTMP e a chave deste destino antes de iniciar.'
        ok, error = validate_source(destination)
        if not ok:
            return False, error

        # If the channel already owns a live core session, the v4.4 path can attach this
        # destination to it. build_cmd() above still gives this output its own source.
        with manager_obj.lock:
            session = manager_obj.sessions.get(cid)
        if session and not session.stop_requested and session.desired_running:
            return original_start_output(manager_obj, cid, slug)

        # "Fonte do canal" can use the normal starter unchanged. A dedicated source needs
        # its own work-channel snapshot *before* StreamManager.start() validates the channel
        # source; otherwise an empty/different main source blocks the independent output.
        if source_mode(destination) == 'channel':
            return original_start_output(manager_obj, cid, slug)

        work = apply_source_override(channel, destination)
        media = []
        if source_mode(destination) == 'local':
            media = [safe_filename(destination.get('output_source_video'))]
            media_label = media[0]
        else:
            media_label = str(destination.get('output_source_url') or '')

        run_id = STREAMING.create_live_run(cid, None, 'manual', media_label, [slug], '')
        live_session = STREAMING.Session(
            channel_id=cid,
            run_id=run_id,
            trigger='manual',
            schedule_id=None,
            platforms=[slug],
            media=media,
            started_at=now_iso(),
            desired_running=True,
            work_channel=work,
            max_duration_seconds=0,
        )

        # Close the race where another click/session appeared after the first check.
        with manager_obj.lock:
            current = manager_obj.sessions.get(cid)
            if current and not current.stop_requested and current.desired_running:
                try:
                    STREAMING.finish_live_run(run_id, 'cancelled', 'Sessão concorrente assumiu o canal.')
                except Exception:
                    pass
                return original_start_output(manager_obj, cid, slug)
            manager_obj.sessions[cid] = live_session

        try:
            STREAMING.set_desired_running(cid, True)
        except Exception:
            pass

        try:
            started = bool(manager_obj._start_platform(live_session, slug, recovery=False))
        except Exception as exc:
            started = False
            error = str(exc)
        else:
            state = (live_session.platform_states or {}).get(slug)
            error = str(getattr(state, 'last_error', '') or '')

        if not started:
            with manager_obj.lock:
                if manager_obj.sessions.get(cid) is live_session:
                    manager_obj.sessions.pop(cid, None)
            try:
                STREAMING.finish_live_run(run_id, 'failed', error or 'Destino não iniciou.')
                STREAMING.set_desired_running(cid, False)
            except Exception:
                pass
            return False, 'Não foi possível iniciar esta saída.' + (f' {error}' if error else '')

        try:
            STREAMING.audit(
                'info', 'platform_started_individually', cid,
                f'Destino {slug} iniciado com fonte própria.',
                {'platform': slug, 'source_mode': source_mode(destination), 'run_id': run_id},
            )
            STREAMING.BUS.publish(
                'live_started',
                {'channel_id': cid, 'run_id': run_id, 'platforms': [slug], 'trigger': 'manual', 'stop_at': ''},
            )
            STREAMING.BUS.publish(
                'platform_started',
                {'channel_id': cid, 'slug': slug, 'run_id': run_id, 'source_mode': source_mode(destination)},
            )
        except Exception:
            pass
        return True, 'Live desta saída iniciada.'

    manager._build_cmd = MethodType(build_cmd, manager)
    multi_output._start_output = start_output
    app.register_blueprint(output_sources_bp)
    return manager
