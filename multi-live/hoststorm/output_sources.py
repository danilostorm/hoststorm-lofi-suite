from __future__ import annotations

import copy
import urllib.parse
from types import MethodType

from flask import Blueprint, abort, jsonify, request

from .config import VIDEOS_DIR
from .utils import safe_filename
from .url_sources import validate_remote_url
from . import multi_output


output_sources_bp = Blueprint('output_sources', __name__)
WEB = None
MANAGER = None

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


@output_sources_bp.route('/lives/<cid>/outputs/<slug>/source', methods=['POST'])
def save_output_source(cid, slug):
    if not WEB:
        abort(503)
    channel = WEB.get_channel(cid)
    if not channel:
        abort(404)
    destination = (channel.get('destinations') or {}).get(slug)
    if not destination:
        abort(404)

    mode = str(request.form.get('mode') or 'channel').strip().lower()
    if mode not in SOURCE_MODES:
        return _json_or_error(False, 'Tipo de fonte inválido.', 400)

    updated = copy.deepcopy(destination)
    updated['output_source_mode'] = mode
    updated['output_source_video'] = safe_filename(request.form.get('video')) if mode == 'local' else ''
    updated['output_source_url'] = str(request.form.get('url') or '').strip() if mode == 'url' else ''

    ok, error = validate_source(updated)
    if not ok:
        return _json_or_error(False, error, 400)

    destinations = copy.deepcopy(channel.get('destinations') or {})
    destinations[slug] = updated
    WEB.save_channel(
        cid,
        channel.get('name') or 'Canal',
        multi_output._channel_settings(channel),
        destinations,
    )

    running = False
    try:
        running = bool(((MANAGER.channel_status(cid).get('platforms') or {}).get(slug) or {}).get('running'))
    except Exception:
        pass
    if running:
        return _json_or_error(True, 'Fonte salva. Pare e inicie somente esta saída para aplicar a nova fonte.')
    return _json_or_error(True, 'Fonte desta saída salva.')


def install_output_sources(app, web_module, streaming_module):
    """Install independent manual sources per RTMP destination."""
    global WEB, MANAGER
    WEB = web_module
    MANAGER = streaming_module.MANAGER
    manager = MANAGER

    original_build_cmd = manager._build_cmd
    original_start_output = multi_output._start_output

    def build_cmd(self, session, slug):
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
        ok, error = validate_source(destination)
        if not ok:
            return False, error
        return original_start_output(manager_obj, cid, slug)

    manager._build_cmd = MethodType(build_cmd, manager)
    multi_output._start_output = start_output
    app.register_blueprint(output_sources_bp)
    return manager
