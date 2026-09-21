from __future__ import annotations

import copy
import shutil
import subprocess
import urllib.parse
from types import MethodType

from flask import Blueprint, abort, jsonify, request

from .config import VIDEOS_DIR, AUDIOS_DIR
from .utils import build_target, now_iso, safe_filename
from .url_sources import validate_remote_url
from . import multi_output
from .output_bitrate import BITRATE_MODES, bitrate_mode, inherited_k, target_bitrate_k, youtube_recommended_k
from .pro_db import list_nodes


output_sources_bp = Blueprint('output_sources', __name__)
WEB = None
MANAGER = None
STREAMING = None

SOURCE_MODES = {'channel', 'local', 'url'}
AUDIO_MODES = {'inherit', 'original', 'library', 'url', 'mix_library', 'mix_url'}
AUDIO_MIX_PROFILES = {'podcast', 'balanced', 'manual'}
NODE_MODES = {'inherit', 'local', 'auto', 'specific'}


def source_mode(destination: dict | None) -> str:
    destination = destination or {}
    mode = str(destination.get('output_source_mode') or 'channel').strip().lower()
    return mode if mode in SOURCE_MODES else 'channel'


def audio_mode(destination: dict | None) -> str:
    destination = destination or {}
    mode = str(destination.get('output_audio_mode') or 'inherit').strip().lower()
    return mode if mode in AUDIO_MODES else 'inherit'


def apply_audio_override(channel: dict, destination: dict | None) -> dict:
    """Apply only the audio policy owned by one RTMP destination.

    Original removes channel-level replacement audio so FFmpeg maps the source video's
    own audio. Library points both horizontal and vertical paths to one library file.
    URL clears inherited audio; the URL is injected as a dedicated FFmpeg input later.
    """
    work = copy.deepcopy(channel or {})
    destination = destination or {}
    mode = audio_mode(destination)
    if mode == 'inherit':
        return work
    if mode == 'original':
        work['audio'] = ''
        work['shorts_audio'] = ''
        return work
    if mode == 'library':
        name = safe_filename(destination.get('output_audio_file'))
        work['audio'] = name
        work['shorts_audio'] = name
        return work
    # URL replacement and both mix modes need the source video's original audio mapped
    # by the base command. A later wrapper adds/replaces the external audio as needed.
    work['audio'] = ''
    work['shorts_audio'] = ''
    return work


def validate_audio(destination: dict | None) -> tuple[bool, str]:
    destination = destination or {}
    mode = audio_mode(destination)
    if mode in {'inherit', 'original'}:
        return True, ''
    if mode in {'library', 'mix_library'}:
        name = safe_filename(destination.get('output_audio_file'))
        if not name:
            return False, 'Selecione um áudio da Biblioteca para esta saída.'
        if not (AUDIOS_DIR / name).is_file():
            return False, f'O áudio "{name}" não existe mais na Biblioteca.'
        return True, ''
    url = str(destination.get('output_audio_url') or '').strip()
    if not url:
        return False, 'Informe a URL de áudio desta saída.'
    try:
        validate_remote_url(url)
    except Exception as exc:
        return False, str(exc)
    return True, ''


def _is_direct_audio_url(url: str) -> bool:
    path = urllib.parse.urlparse(str(url or '')).path.lower()
    return any(path.endswith(ext) for ext in (
        '.mp3', '.m4a', '.aac', '.flac', '.ogg', '.opus', '.wav',
        '.mp4', '.m4v', '.mov', '.mkv', '.webm', '.m3u8',
    ))


def _resolve_audio_url(url: str) -> str:
    """Resolve a webpage such as YouTube to an audio-only media URL."""
    url = validate_remote_url(url)
    if _is_direct_audio_url(url):
        return url
    ytdlp = shutil.which('yt-dlp')
    if not ytdlp:
        raise RuntimeError('yt-dlp não está disponível para resolver o áudio externo.')
    proc = subprocess.run(
        [ytdlp, '--no-playlist', '--no-warnings', '-f', 'bestaudio/best', '-g', url],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError('Falha resolvendo áudio externo: ' + (proc.stderr or proc.stdout or '')[-900:])
    lines = [line.strip() for line in (proc.stdout or '').splitlines() if line.strip()]
    if not lines:
        raise RuntimeError('yt-dlp não retornou uma URL de áudio reproduzível.')
    return lines[0]


def _safe_gain_db(value, default: float) -> float:
    try:
        return max(-30.0, min(12.0, float(value)))
    except Exception:
        return float(default)


def _audio_mix_profile(destination: dict | None) -> str:
    destination = destination or {}
    profile = str(destination.get('output_audio_mix_profile') or 'podcast').strip().lower()
    return profile if profile in AUDIO_MIX_PROFILES else 'podcast'


def _mix_gains(destination: dict | None) -> tuple[float, float]:
    destination = destination or {}
    profile = _audio_mix_profile(destination)
    if profile == 'balanced':
        return -6.0, -6.0
    if profile == 'manual':
        return (
            _safe_gain_db(destination.get('output_audio_original_gain_db'), -10.0),
            _safe_gain_db(destination.get('output_audio_external_gain_db'), 0.0),
        )
    # Podcast em destaque: o externo fica na frente e o gameplay é reduzido.
    return -10.0, 0.0


def _external_audio_source(destination: dict) -> str:
    mode = audio_mode(destination)
    if mode in {'library', 'mix_library'}:
        name = safe_filename(destination.get('output_audio_file'))
        if not name:
            raise RuntimeError('Áudio da Biblioteca não selecionado.')
        return str(AUDIOS_DIR / name)
    return _resolve_audio_url(destination.get('output_audio_url'))


def _strip_audio_map(cmd: list[str]) -> list[str]:
    result = []
    i = 0
    while i < len(cmd):
        if cmd[i] == '-map' and i + 1 < len(cmd) and ':a' in str(cmd[i + 1]):
            i += 2
            continue
        result.append(cmd[i])
        i += 1
    return result


def _inject_mixed_audio(cmd: list[str], audio_source: str, destination: dict) -> list[str]:
    """Mix original program audio with an external/library source.

    Podcast profile normalizes the external track and ducks gameplay while speech/audio
    is present. Balanced keeps both at similar level. Manual uses the saved dB gains.
    """
    result = list(cmd or [])
    next_input = sum(1 for token in result if token == '-i')
    try:
        insert_at = result.index('-map')
    except ValueError:
        try:
            insert_at = result.index('-vf')
        except ValueError:
            positions = [i for i, token in enumerate(result) if token == '-f']
            insert_at = positions[-1] if positions else max(0, len(result) - 1)

    result[insert_at:insert_at] = ['-re', '-stream_loop', '-1', '-i', audio_source]
    result = _strip_audio_map(result)

    original_db, external_db = _mix_gains(destination)
    profile = _audio_mix_profile(destination)
    if profile == 'podcast':
        graph = (
            f'[0:a:0]aresample=44100:async=1:first_pts=0,volume={original_db:.1f}dB[game];'
            f'[{next_input}:a:0]aresample=44100:async=1:first_pts=0,'
            f'loudnorm=I=-16:TP=-1.5:LRA=11,volume={external_db:.1f}dB[podcast];'
            f'[game][podcast]sidechaincompress=threshold=0.035:ratio=8:attack=20:release=650[ducked];'
            f'[ducked][podcast]amix=inputs=2:duration=first:dropout_transition=2:normalize=0,'
            f'alimiter=limit=0.95[aout]'
        )
    else:
        graph = (
            f'[0:a:0]aresample=44100:async=1:first_pts=0,volume={original_db:.1f}dB[game];'
            f'[{next_input}:a:0]aresample=44100:async=1:first_pts=0,volume={external_db:.1f}dB[external];'
            f'[game][external]amix=inputs=2:duration=first:dropout_transition=2:normalize=0,'
            f'alimiter=limit=0.95[aout]'
        )

    try:
        map_at = result.index('-map')
    except ValueError:
        map_at = insert_at + 4
        result[map_at:map_at] = ['-map', '0:v:0']
        map_at += 2

    result[map_at:map_at] = ['-filter_complex', graph]
    # Keep the video's existing map and add the mixed audio label after it.
    map_positions = [i for i, token in enumerate(result) if token == '-map']
    insert_map_at = map_positions[-1] + 2 if map_positions else map_at + 2
    result[insert_map_at:insert_map_at] = ['-map', '[aout]']

    if '-shortest' not in result:
        positions = [i for i, token in enumerate(result) if token == '-f']
        output_at = positions[-1] if positions else max(0, len(result) - 1)
        result[output_at:output_at] = ['-shortest']
    return result


def _inject_external_audio(cmd: list[str], audio_url: str) -> list[str]:
    """Add an audio input and map it instead of the source video's audio."""
    result = list(cmd or [])
    next_input = sum(1 for token in result if token == '-i')
    try:
        insert_at = result.index('-map')
    except ValueError:
        try:
            insert_at = result.index('-vf')
        except ValueError:
            positions = [i for i, token in enumerate(result) if token == '-f']
            insert_at = positions[-1] if positions else max(0, len(result) - 1)

    result[insert_at:insert_at] = ['-re', '-stream_loop', '-1', '-i', audio_url]
    audio_map = f'{next_input}:a:0'
    replaced = False
    i = insert_at + 4
    while i < len(result) - 1:
        if result[i] == '-map':
            current = str(result[i + 1])
            if ':a' in current:
                result[i + 1] = audio_map
                replaced = True
                break
            i += 2
            continue
        i += 1
    if not replaced:
        try:
            video_map = result.index('-map', insert_at + 4)
            result[video_map + 2:video_map + 2] = ['-map', audio_map]
        except ValueError:
            result[insert_at + 4:insert_at + 4] = ['-map', '0:v:0', '-map', audio_map]

    # Replacement audio loops forever. Shortest lets finite/rerun video end naturally.
    if '-shortest' not in result:
        positions = [i for i, token in enumerate(result) if token == '-f']
        output_at = positions[-1] if positions else max(0, len(result) - 1)
        result[output_at:output_at] = ['-shortest']
    return result


def apply_source_override(channel: dict, destination: dict | None) -> dict:
    work = copy.deepcopy(channel or {})
    destination = destination or {}
    mode = source_mode(destination)
    if mode == 'channel':
        return work
    if mode == 'local':
        video = safe_filename(destination.get('output_source_video'))
        work['source_mode'] = 'local'
        work['video'] = video
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

    rate_mode = str(request.form.get('bitrate_mode') or bitrate_mode(destination)).strip().lower()
    if rate_mode not in BITRATE_MODES:
        return None, 'Modo de bitrate inválido.'
    updated['output_bitrate_mode'] = rate_mode
    if rate_mode == 'custom':
        try:
            custom_k = int(float(request.form.get('bitrate_k') or destination.get('output_video_bitrate_k') or 0))
        except Exception:
            custom_k = 0
        if not 500 <= custom_k <= 50000:
            return None, 'Bitrate personalizado deve ficar entre 500 e 50000 kbps.'
        updated['output_video_bitrate_k'] = custom_k
    else:
        updated['output_video_bitrate_k'] = 0

    node_mode = str(request.form.get('node_mode') or destination.get('output_node_mode') or 'inherit').strip().lower()
    if node_mode not in NODE_MODES:
        return None, 'Modo de servidor inválido.'
    node_id = str(request.form.get('node_id') or destination.get('output_node_id') or '').strip()
    if node_mode == 'specific':
        node = next((n for n in list_nodes() if str(n.get('id')) == node_id and n.get('enabled')), None)
        if not node:
            return None, 'Servidor selecionado não existe ou está desabilitado.'
    else:
        node_id = ''
    updated['output_node_mode'] = node_mode
    updated['output_node_id'] = node_id

    if 'audio_mode' in request.form:
        amode = str(request.form.get('audio_mode') or 'inherit').strip().lower()
        if amode not in AUDIO_MODES:
            return None, 'Tipo de áudio inválido.'
        updated['output_audio_mode'] = amode
        if amode in {'library', 'mix_library'}:
            updated['output_audio_file'] = safe_filename(request.form.get('audio_file'))
            updated['output_audio_url'] = ''
        elif amode in {'url', 'mix_url'}:
            updated['output_audio_file'] = ''
            updated['output_audio_url'] = str(request.form.get('audio_url') or '').strip()
        else:
            updated['output_audio_file'] = ''
            updated['output_audio_url'] = ''
        mix_profile = str(request.form.get('audio_mix_profile') or destination.get('output_audio_mix_profile') or 'podcast').strip().lower()
        if mix_profile not in AUDIO_MIX_PROFILES:
            mix_profile = 'podcast'
        updated['output_audio_mix_profile'] = mix_profile
        updated['output_audio_original_gain_db'] = _safe_gain_db(
            request.form.get('audio_original_gain_db'), destination.get('output_audio_original_gain_db', -10)
        )
        updated['output_audio_external_gain_db'] = _safe_gain_db(
            request.form.get('audio_external_gain_db'), destination.get('output_audio_external_gain_db', 0)
        )

    audio_ok, audio_error = validate_audio(updated)
    if not audio_ok:
        return None, audio_error

    if include_transport:
        rtmp_url = str(request.form.get('rtmp_url') or '').strip()
        if rtmp_url:
            updated['rtmp_url'] = rtmp_url
        stream_key = str(request.form.get('stream_key') or '').strip()
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
    videos = sorted([p.name for p in VIDEOS_DIR.iterdir() if p.is_file()], key=lambda value: value.casefold())
    audios = sorted([p.name for p in AUDIOS_DIR.iterdir() if p.is_file()], key=lambda value: value.casefold())
    nodes = [
        {
            'id': str(node.get('id') or ''),
            'name': str(node.get('name') or node.get('id') or ''),
            'status': str(node.get('status') or 'unknown'),
            'cpu': float(node.get('cpu') or 0),
            'ram': float(node.get('ram') or 0),
            'gpu': float(node.get('gpu') or 0),
            'active_streams': int(node.get('active_streams') or 0),
            'tags': list(node.get('tags') or []),
        }
        for node in list_nodes() if node.get('enabled')
    ]
    outputs = {}
    for slug, destination in (channel.get('destinations') or {}).items():
        outputs[slug] = {
            'mode': source_mode(destination),
            'video': str(destination.get('output_source_video') or ''),
            'url': str(destination.get('output_source_url') or ''),
            'label': str(destination.get('label') or slug),
            'platform': multi_output.platform_kind(slug, destination),
            'bitrate_mode': bitrate_mode(destination),
            'bitrate_k': int(destination.get('output_video_bitrate_k') or 0),
            'inherited_bitrate_k': inherited_k(channel, slug, destination),
            'recommended_bitrate_k': youtube_recommended_k(channel, slug, destination),
            'effective_bitrate_k': target_bitrate_k(channel, slug, destination),
            'audio_mode': audio_mode(destination),
            'audio_file': str(destination.get('output_audio_file') or ''),
            'audio_url': str(destination.get('output_audio_url') or ''),
            'audio_mix_profile': _audio_mix_profile(destination),
            'audio_original_gain_db': _mix_gains(destination)[0],
            'audio_external_gain_db': _mix_gains(destination)[1],
            'node_mode': str(destination.get('output_node_mode') or 'inherit'),
            'node_id': str(destination.get('output_node_id') or ''),
        }
    return jsonify({'ok': True, 'channel_id': cid, 'videos': videos, 'audios': audios, 'nodes': nodes, 'outputs': outputs})


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
        return _json_or_error(True, 'Fonte, áudio, bitrate e servidor salvos. Reinicie somente esta saída para aplicar.')
    return _json_or_error(True, 'Configuração desta saída salva.')


@output_sources_bp.route('/lives/<cid>/outputs/<slug>/source/start', methods=['POST'])
def save_and_start_output(cid, slug):
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
    global WEB, MANAGER, STREAMING
    WEB = web_module
    MANAGER = streaming_module.MANAGER
    STREAMING = streaming_module
    manager = MANAGER

    original_build_cmd = manager._build_cmd
    original_start_output = multi_output._start_output

    def build_cmd(self, session, slug):
        destination = ((session.work_channel or {}).get('destinations') or {}).get(slug) or {}
        source_override = session.trigger == 'manual' and source_mode(destination) != 'channel'
        amode = audio_mode(destination)
        audio_override = amode != 'inherit'
        if not source_override and not audio_override:
            return original_build_cmd(session, slug)

        work = copy.deepcopy(session.work_channel or {})
        if source_override:
            ok, error = validate_source(destination)
            if not ok:
                raise RuntimeError(error)
            work = apply_source_override(work, destination)

        if audio_override:
            ok, error = validate_audio(destination)
            if not ok:
                raise RuntimeError(error)
            work = apply_audio_override(work, destination)

        shadow = copy.copy(session)
        shadow.work_channel = work
        cmd = original_build_cmd(shadow, slug)
        if amode == 'url':
            cmd = _inject_external_audio(cmd, _resolve_audio_url(destination.get('output_audio_url')))
        elif amode in {'mix_library', 'mix_url'}:
            cmd = _inject_mixed_audio(cmd, _external_audio_source(destination), destination)
        return cmd

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

        with manager_obj.lock:
            session = manager_obj.sessions.get(cid)
        if session and not session.stop_requested and session.desired_running:
            return original_start_output(manager_obj, cid, slug)
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
            channel_id=cid, run_id=run_id, trigger='manual', schedule_id=None,
            platforms=[slug], media=media, started_at=now_iso(), desired_running=True,
            work_channel=work, max_duration_seconds=0,
        )
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
            STREAMING.audit('info', 'platform_started_individually', cid, f'Destino {slug} iniciado com fonte própria.', {'platform': slug, 'source_mode': source_mode(destination), 'run_id': run_id})
            STREAMING.BUS.publish('live_started', {'channel_id': cid, 'run_id': run_id, 'platforms': [slug], 'trigger': 'manual', 'stop_at': ''})
            STREAMING.BUS.publish('platform_started', {'channel_id': cid, 'slug': slug, 'run_id': run_id, 'source_mode': source_mode(destination)})
        except Exception:
            pass
        return True, 'Live desta saída iniciada.'

    manager._build_cmd = MethodType(build_cmd, manager)
    multi_output._start_output = start_output
    app.register_blueprint(output_sources_bp)
    return manager
