from __future__ import annotations

import ipaddress
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import uuid
from datetime import timedelta
from pathlib import Path
from types import MethodType

from flask import Blueprint, flash, jsonify, redirect, request, url_for

from .config import AUDIOS_DIR, VIDEOS_DIR
from .utils import build_target, normalize_time, now_dt, now_iso, safe_filename

urlmedia_bp = Blueprint('urlmedia', __name__)

DB = None
WEB = None
STREAMING = None
MANAGER = None
_YTDLP_CACHE = {'at': 0.0, 'value': None}


def validate_remote_url(url: str) -> str:
    url = str(url or '').strip()
    if not url or len(url) > 4096:
        raise ValueError('URL inválida.')
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
        raise ValueError('Use uma URL http:// ou https:// válida.')
    if parsed.username or parsed.password:
        raise ValueError('URLs com usuário/senha embutidos não são permitidas.')
    if os.environ.get('HOSTSTORM_ALLOW_PRIVATE_SOURCE_URLS', '0') == '1':
        return url
    host = parsed.hostname.rstrip('.').lower()
    if host in {'localhost', 'localhost.localdomain'} or host.endswith('.local'):
        raise ValueError('URL local/privada bloqueada. Ative HOSTSTORM_ALLOW_PRIVATE_SOURCE_URLS=1 se realmente precisar.')
    try:
        addresses = {x[4][0] for x in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == 'https' else 80), type=socket.SOCK_STREAM)}
        for raw in addresses:
            ip = ipaddress.ip_address(raw.split('%', 1)[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                raise ValueError('URL aponta para rede privada/local e foi bloqueada por segurança.')
    except ValueError:
        raise
    except Exception:
        pass
    return url


def ytdlp_status(force=False) -> dict:
    now = time.time()
    if not force and _YTDLP_CACHE['value'] and now - _YTDLP_CACHE['at'] < 120:
        return dict(_YTDLP_CACHE['value'])
    path = shutil.which('yt-dlp')
    node = shutil.which('node') or shutil.which('nodejs')
    if not path:
        value = {'ok': False, 'path': '', 'version': '', 'node': bool(node), 'node_path': node or '', 'message': 'yt-dlp não encontrado'}
    else:
        try:
            p = subprocess.run([path, '--version'], capture_output=True, text=True, timeout=10)
            version = (p.stdout or p.stderr or '').strip().splitlines()[0] if p.returncode == 0 else ''
            value = {'ok': p.returncode == 0, 'path': path, 'version': version, 'node': bool(node), 'node_path': node or '',
                     'message': f'yt-dlp {version}' if p.returncode == 0 else 'yt-dlp encontrado, mas não respondeu'}
        except Exception as e:
            value = {'ok': False, 'path': path, 'version': '', 'node': bool(node), 'node_path': node or '', 'message': str(e)}
    _YTDLP_CACHE['at'] = now
    _YTDLP_CACHE['value'] = dict(value)
    return value


def _yt_base_args():
    status = ytdlp_status()
    if not status.get('ok'):
        raise RuntimeError('yt-dlp não está disponível no container.')
    args = [status['path'], '--no-playlist', '--no-warnings', '--socket-timeout', '20']
    if status.get('node'):
        args += ['--js-runtimes', 'node']
    return args


def _is_direct_media_url(url: str) -> bool:
    path = urllib.parse.urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in ('.mp4', '.m4v', '.mov', '.mkv', '.webm', '.flv', '.ts', '.m3u8', '.mp3', '.m4a', '.aac', '.ogg', '.opus', '.wav'))


def _ffprobe_remote(url: str, timeout=35) -> dict:
    cmd = ['ffprobe', '-v', 'error', '-show_entries',
           'format=duration:stream=codec_type,codec_name,width,height,r_frame_rate',
           '-of', 'json', url]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or 'ffprobe não conseguiu analisar a URL.')[-1200:])
    payload = json.loads(p.stdout or '{}')
    duration = float((payload.get('format') or {}).get('duration') or 0)
    width = height = 0
    codec = ''
    for stream in payload.get('streams') or []:
        if stream.get('codec_type') == 'video':
            width = int(stream.get('width') or 0)
            height = int(stream.get('height') or 0)
            codec = str(stream.get('codec_name') or '')
            break
    return {'duration_seconds': duration, 'width': width, 'height': height, 'codec': codec}


def _youtube_preview(info: dict, url: str) -> str:
    extractor = str(info.get('extractor_key') or info.get('extractor') or '').lower()
    vid = str(info.get('id') or '').strip()
    host = (urllib.parse.urlparse(url).hostname or '').lower()
    if vid and ('youtube' in extractor or host.endswith('youtube.com') or host == 'youtu.be'):
        return f'https://www.youtube-nocookie.com/embed/{urllib.parse.quote(vid, safe="")}'
    return ''


def probe_remote_source(url: str, timeout=70) -> dict:
    url = validate_remote_url(url)
    status = ytdlp_status()
    info = {}
    error = ''
    if status.get('ok'):
        try:
            cmd = _yt_base_args() + ['--skip-download', '--dump-single-json', url]
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if p.returncode == 0 and p.stdout.strip():
                info = json.loads(p.stdout)
            else:
                error = (p.stderr or p.stdout or '')[-1200:]
        except Exception as e:
            error = str(e)

    duration = float(info.get('duration') or 0) if info else 0
    preview = _youtube_preview(info, url) if info else ''
    meta = {
        'ok': False,
        'url': url,
        'title': str(info.get('title') or '').strip() if info else '',
        'duration_seconds': duration,
        'extractor': str(info.get('extractor_key') or info.get('extractor') or '').strip() if info else '',
        'thumbnail': str(info.get('thumbnail') or '').strip() if info else '',
        'width': int(info.get('width') or 0) if info else 0,
        'height': int(info.get('height') or 0) if info else 0,
        'webpage_url': str(info.get('webpage_url') or url) if info else url,
        'preview_url': preview,
        'preview_kind': 'iframe' if preview else '',
        'ytdlp': status,
    }
    if not meta['title']:
        meta['title'] = Path(urllib.parse.urlparse(url).path).name or urllib.parse.urlparse(url).hostname or 'Vídeo remoto'

    if duration <= 0 and _is_direct_media_url(url):
        try:
            direct = _ffprobe_remote(url, min(timeout, 40))
            meta.update({k: v for k, v in direct.items() if v})
            duration = float(meta.get('duration_seconds') or 0)
        except Exception as e:
            if not error:
                error = str(e)
        if not meta['preview_url'] and urllib.parse.urlparse(url).path.lower().endswith(('.mp4', '.webm', '.mov', '.m4v')):
            meta['preview_url'] = url
            meta['preview_kind'] = 'video'

    if info or _is_direct_media_url(url):
        meta['ok'] = True
    if not meta['ok']:
        if status.get('ok'):
            raise RuntimeError('yt-dlp não conseguiu analisar esta URL. ' + (error or 'Fonte não suportada.'))
        raise RuntimeError('yt-dlp não está ativo e a URL não parece ser um arquivo de mídia direto.')
    return meta


def resolve_remote_stream(url: str, timeout=55) -> str:
    url = validate_remote_url(url)
    if _is_direct_media_url(url):
        return url
    cmd = _yt_base_args() + [
        '-f', 'best[height<=1080][vcodec!=none][acodec!=none]/best[height<=1080]/best',
        '--get-url', url,
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError('Falha no yt-dlp ao resolver a fonte: ' + (p.stderr or p.stdout or '')[-1000:])
    lines = [x.strip() for x in (p.stdout or '').splitlines() if x.strip()]
    if not lines:
        raise RuntimeError('yt-dlp não retornou uma URL reproduzível.')
    return lines[0]


def import_remote_to_library(url: str, kind='video') -> dict:
    url = validate_remote_url(url)
    kind = 'audio' if kind == 'audio' else 'video'
    target = AUDIOS_DIR if kind == 'audio' else VIDEOS_DIR
    target.mkdir(parents=True, exist_ok=True)
    status = ytdlp_status()
    if status.get('ok'):
        template = str(target / '%(title).120s-%(id)s.%(ext)s')
        cmd = _yt_base_args() + ['--restrict-filenames', '--newline', '-o', template]
        if kind == 'audio':
            cmd += ['-x', '--audio-format', 'mp3', '--audio-quality', '0']
        else:
            cmd += ['-f', 'bestvideo[height<=1080]+bestaudio/best[height<=1080]/best',
                    '--merge-output-format', 'mp4']
        cmd += ['--print', 'after_move:filepath', url]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
        if p.returncode != 0:
            raise RuntimeError((p.stderr or p.stdout or 'Falha no yt-dlp.')[-1800:])
        paths = [x.strip() for x in (p.stdout or '').splitlines() if x.strip()]
        name = Path(paths[-1]).name if paths else ''
        return {'ok': True, 'message': f'Importação concluída{": " + name if name else "."}', 'filename': name}

    if not _is_direct_media_url(url):
        raise RuntimeError('yt-dlp não está disponível; somente URLs diretas de arquivo podem ser importadas.')
    name = safe_filename(Path(urllib.parse.urlparse(url).path).name) or f'import-{int(time.time())}.mp4'
    dest = target / name
    with urllib.request.urlopen(url, timeout=90) as response, dest.open('wb') as out:
        shutil.copyfileobj(response, out)
    return {'ok': True, 'message': f'Importação direta concluída: {dest.name}', 'filename': dest.name}


def migrate_schedule_schema(db_module):
    columns = {
        'source_mode': "TEXT NOT NULL DEFAULT 'library'",
        'source_url': "TEXT NOT NULL DEFAULT ''",
        'source_title': "TEXT NOT NULL DEFAULT ''",
        'source_duration_seconds': "REAL NOT NULL DEFAULT 0",
        'source_extractor': "TEXT NOT NULL DEFAULT ''",
        'source_preview_url': "TEXT NOT NULL DEFAULT ''",
    }
    with db_module.connect() as con:
        existing = {r['name'] for r in con.execute('PRAGMA table_info(schedules)').fetchall()}
        for name, sql in columns.items():
            if name not in existing:
                con.execute(f'ALTER TABLE schedules ADD COLUMN {name} {sql}')
        con.execute("INSERT INTO meta(key,value) VALUES('schema_version','3') ON CONFLICT(key) DO UPDATE SET value='3'")


def _source_row(db_module, sid: str) -> dict:
    with db_module.connect() as con:
        row = con.execute(
            'SELECT source_mode,source_url,source_title,source_duration_seconds,source_extractor,source_preview_url '
            'FROM schedules WHERE id=?', (sid,)
        ).fetchone()
    return dict(row) if row else {}


def _augment_schedule(db_module, schedule):
    if not schedule:
        return schedule
    schedule.update(_source_row(db_module, schedule['id']))
    schedule['source_mode'] = schedule.get('source_mode') or 'library'
    return schedule


def install_schedule_db(db_module, web_module):
    migrate_schedule_schema(db_module)
    original_list = db_module.list_schedules
    original_get = db_module.get_schedule
    original_save = db_module.save_schedule

    def list_schedules(channel_id=None, con=None):
        return [_augment_schedule(db_module, s) for s in original_list(channel_id, con)]

    def get_schedule(schedule_id):
        return _augment_schedule(db_module, original_get(schedule_id))

    def save_schedule(data: dict) -> str:
        source_mode = 'url' if str(data.get('source_mode') or 'library') == 'url' else 'library'
        if source_mode == 'library':
            sid = original_save(data)
            with db_module.connect() as con:
                con.execute(
                    "UPDATE schedules SET source_mode='library',source_url='',source_title='',source_duration_seconds=0,"
                    "source_extractor='',source_preview_url='',updated_at=? WHERE id=?",
                    (now_iso(), sid),
                )
            return sid

        sid = data.get('id') or uuid.uuid4().hex[:10]
        ts = now_iso()
        schedule_time = normalize_time(data.get('time'))
        if not schedule_time:
            raise ValueError('Horário inválido.')
        weekdays = sorted({int(x) for x in data.get('weekdays', []) if str(x).isdigit() and 0 <= int(x) <= 6})
        platforms = [str(x) for x in data.get('platforms', []) if x]
        if not platforms:
            raise ValueError('Escolha pelo menos uma plataforma.')
        kind = str(data.get('kind') or 'weekly')
        if kind not in {'weekly', 'daily', 'weekdays', 'once'}:
            kind = 'weekly'
        if kind == 'weekly' and not weekdays:
            raise ValueError('Marque pelo menos um dia da semana.')
        if kind == 'once' and not str(data.get('run_date') or ''):
            raise ValueError('Informe a data da execução única.')
        conflict = str(data.get('conflict_policy') or 'skip')
        if conflict not in {'skip', 'stop_current', 'wait'}:
            conflict = 'skip'

        source_url = validate_remote_url(data.get('source_url'))
        source_title = str(data.get('source_title') or '').strip()[:500]
        source_extractor = str(data.get('source_extractor') or '').strip()[:120]
        source_preview_url = str(data.get('source_preview_url') or '').strip()[:4096]
        try:
            source_duration = max(0.0, float(data.get('source_duration_seconds') or 0))
        except Exception:
            source_duration = 0.0
        stop_before = max(0, min(600, int(data.get('stop_before_seconds', 60) or 0)))
        max_minutes = max(0, int(data.get('max_duration_minutes') or 0))
        if source_duration <= stop_before and max_minutes <= 0:
            if source_duration > 0:
                raise ValueError('A duração detectada da URL precisa ser maior que o tempo de parada antecipada.')
            raise ValueError('Não foi possível detectar a duração. Informe uma duração máxima em minutos para essa URL.')

        with db_module.connect() as con:
            exists = con.execute('SELECT created_at FROM schedules WHERE id=?', (sid,)).fetchone()
            created = exists['created_at'] if exists else ts
            con.execute(
                'INSERT INTO schedules('
                'id,channel_id,name,kind,weekdays_json,schedule_time,run_date,start_date,end_date,enabled,'
                'conflict_policy,stop_before_seconds,platforms_json,shuffle,repeat_playlist,max_duration_minutes,'
                'last_run_key,last_started_at,last_finished_at,last_status,created_at,updated_at,'
                'source_mode,source_url,source_title,source_duration_seconds,source_extractor,source_preview_url'
                ') VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) '
                'ON CONFLICT(id) DO UPDATE SET '
                'channel_id=excluded.channel_id,name=excluded.name,kind=excluded.kind,weekdays_json=excluded.weekdays_json,'
                'schedule_time=excluded.schedule_time,run_date=excluded.run_date,start_date=excluded.start_date,end_date=excluded.end_date,'
                'enabled=excluded.enabled,conflict_policy=excluded.conflict_policy,stop_before_seconds=excluded.stop_before_seconds,'
                'platforms_json=excluded.platforms_json,shuffle=0,repeat_playlist=0,max_duration_minutes=excluded.max_duration_minutes,'
                'source_mode=excluded.source_mode,source_url=excluded.source_url,source_title=excluded.source_title,'
                'source_duration_seconds=excluded.source_duration_seconds,source_extractor=excluded.source_extractor,'
                'source_preview_url=excluded.source_preview_url,updated_at=excluded.updated_at',
                (
                    sid, data['channel_id'], str(data.get('name') or ''), kind, json.dumps(weekdays), schedule_time,
                    str(data.get('run_date') or ''), str(data.get('start_date') or ''), str(data.get('end_date') or ''),
                    int(bool(data.get('enabled', True))), conflict, stop_before, json.dumps(platforms), 0, 0, max_minutes,
                    str(data.get('last_run_key') or ''), str(data.get('last_started_at') or ''),
                    str(data.get('last_finished_at') or ''), str(data.get('last_status') or 'Aguardando próximo horário.'),
                    created, ts, 'url', source_url, source_title, source_duration, source_extractor, source_preview_url,
                ),
            )
            con.execute('DELETE FROM schedule_media WHERE schedule_id=?', (sid,))
        db_module.audit('info', 'schedule_saved', data['channel_id'], f'Agendamento por URL salvo: {sid}',
                        {'schedule_id': sid, 'source': 'url', 'title': source_title})
        return sid

    db_module.list_schedules = list_schedules
    db_module.get_schedule = get_schedule
    db_module.save_schedule = save_schedule
    web_module.list_schedules = list_schedules
    web_module.get_schedule = get_schedule
    web_module.save_schedule = save_schedule
    return list_schedules, get_schedule, save_schedule


def install_streaming(manager, streaming_module):
    original_input = manager._input_args
    original_start = manager.start

    def resolve(self, url):
        return resolve_remote_stream(url)

    def input_args(self, session, vertical=False):
        remote = str(session.work_channel.get('_schedule_source_url') or '').strip()
        if session.trigger == 'scheduled' and remote:
            return ['-re', '-i', self._resolve_stream_url(remote)]
        return original_input(session, vertical)

    def start(self, cid, platforms=None, media=None, trigger='manual', schedule=None):
        if trigger != 'scheduled' or not schedule or str(schedule.get('source_mode') or 'library') != 'url':
            return original_start(cid, platforms, media, trigger, schedule)

        with self.lock:
            if cid in self.sessions and self.channel_status(cid).get('running'):
                return False, 'Canal já está ao vivo.'
            ch = streaming_module.get_channel(cid)
            if not ch:
                return False, 'Canal não encontrado.'
            platforms = list(platforms or schedule.get('platforms') or [])
            platforms = [p for p in platforms if p in ch['destinations']]
            if not platforms:
                return False, 'Selecione pelo menos uma plataforma.'
            missing = [
                ch['destinations'][p].get('label', p)
                for p in platforms
                if not build_target(ch['destinations'][p].get('rtmp_url'), ch['destinations'][p].get('stream_key'))
            ]
            if missing:
                return False, 'Configure RTMP/chave: ' + ', '.join(missing)

            source_url = str(schedule.get('source_url') or '').strip()
            if not source_url:
                return False, 'Agenda por URL sem fonte configurada.'
            try:
                validate_remote_url(source_url)
            except Exception as e:
                return False, str(e)

            duration = float(schedule.get('source_duration_seconds') or 0)
            title = str(schedule.get('source_title') or '').strip()
            try:
                fresh = probe_remote_source(source_url, timeout=45)
                if float(fresh.get('duration_seconds') or 0) > 0:
                    duration = float(fresh['duration_seconds'])
                title = fresh.get('title') or title
            except Exception as e:
                self.log(cid, f'Não foi possível reanalisar a URL antes da live; usando metadados salvos: {e}')

            stop_before = max(0, int(schedule.get('stop_before_seconds') or 0))
            max_minutes = max(0, int(schedule.get('max_duration_minutes') or 0))
            if duration > 0:
                max_duration = duration - stop_before
                if max_minutes > 0:
                    max_duration = min(max_duration, max_minutes * 60)
            elif max_minutes > 0:
                max_duration = max_minutes * 60
            else:
                return False, 'A URL não informou duração e a agenda não possui duração máxima.'
            if max_duration <= 0:
                return False, 'Duração útil da fonte por URL é inválida.'

            stop_at = (now_dt() + timedelta(seconds=max_duration)).isoformat()
            work = json.loads(json.dumps(ch))
            work['_schedule_source_url'] = source_url
            work['_schedule_source_title'] = title
            work['_repeat_playlist'] = False
            schedule_id = schedule['id']
            media_label = title or source_url
            run_id = streaming_module.create_live_run(cid, schedule_id, trigger, media_label, platforms, stop_at)
            sess = streaming_module.Session(
                channel_id=cid, run_id=run_id, trigger=trigger, schedule_id=schedule_id,
                platforms=platforms, media=[], started_at=now_iso(), stop_at=stop_at,
                desired_running=True, work_channel=work, max_duration_seconds=max_duration,
            )
            self.sessions[cid] = sess
            started, errors = [], []
            for slug in platforms:
                if self._start_platform(sess, slug):
                    started.append(slug)
                else:
                    errors.append(slug)
            if not started:
                self.sessions.pop(cid, None)
                streaming_module.finish_live_run(run_id, 'failed', 'Nenhuma plataforma iniciou.')
                return False, 'Nenhuma plataforma conseguiu iniciar a URL.'
            streaming_module.update_schedule_status(
                schedule_id, last_started_at=now_iso(),
                last_status='Live por URL iniciada: ' + ', '.join(started),
            )
            streaming_module.audit(
                'info', 'live_started', cid, f'Live por URL iniciada em: {", ".join(started)}',
                {'run_id': run_id, 'platforms': started, 'source_url': source_url, 'source_title': title},
            )
            streaming_module.notify(f'🟢 HostStorm: {ch["name"]} iniciou URL agendada em {", ".join(started)}.')
            streaming_module.BUS.publish(
                'live_started',
                {'channel_id': cid, 'run_id': run_id, 'platforms': started, 'trigger': trigger, 'stop_at': stop_at, 'source': 'url'},
            )
            msg = 'Live por URL iniciada: ' + ', '.join(started)
            if errors:
                msg += ' | falharam: ' + ', '.join(errors)
            return True, msg

    manager._resolve_stream_url = MethodType(resolve, manager)
    manager._input_args = MethodType(input_args, manager)
    manager.start = MethodType(start, manager)
    return manager


def _remote_preflight(channel, platforms, meta):
    checks = []
    ok = True

    def add(name, good, message):
        nonlocal ok
        checks.append({'name': name, 'ok': bool(good), 'message': str(message)})
        ok = ok and bool(good)

    add('FFmpeg', bool(shutil.which('ffmpeg')), shutil.which('ffmpeg') or 'não encontrado')
    add('FFprobe', bool(shutil.which('ffprobe')), shutil.which('ffprobe') or 'não encontrado')
    ytdlp = ytdlp_status()
    direct = _is_direct_media_url(meta.get('url', ''))
    add('yt-dlp', bool(ytdlp.get('ok') or direct),
        ytdlp.get('message') if ytdlp.get('ok') else ('URL direta: yt-dlp opcional' if direct else 'yt-dlp obrigatório para esta fonte'))
    for slug in platforms:
        d = (channel.get('destinations') or {}).get(slug, {})
        good = bool(build_target(d.get('rtmp_url'), d.get('stream_key')))
        add(d.get('label', slug), good, 'RTMP configurado' if good else 'Falta URL/chave RTMP')
    duration = float(meta.get('duration_seconds') or 0)
    add('Fonte remota', bool(meta.get('ok')), meta.get('title') or meta.get('url') or 'não analisada')
    if duration > 0:
        add('Duração', True, f'{int(duration)} segundos detectados')
    return {'ok': ok, 'checks': checks}


@urlmedia_bp.route('/api/media/probe-url', methods=['POST'])
def api_probe_url():
    body = request.get_json(silent=True) or request.form
    try:
        return jsonify(probe_remote_source(body.get('url', '')))
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e), 'ytdlp': ytdlp_status()}), 400


@urlmedia_bp.route('/schedules/save-v31', methods=['POST'])
def schedule_save():
    cid = request.form.get('channel_id', '')
    ch = DB.get_channel(cid, False) if DB else None
    if not ch:
        flash('Canal inválido.', 'error')
        return redirect(url_for('web.schedules'))

    source_mode = 'url' if request.form.get('source_mode') == 'url' else 'library'
    platforms = request.form.getlist('platforms')
    media = request.form.getlist('media') if source_mode == 'library' else []
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
        'platforms': platforms,
        'media': media,
        'shuffle': request.form.get('shuffle') == 'on' if source_mode == 'library' else False,
        'repeat_playlist': request.form.get('repeat_playlist') == 'on' if source_mode == 'library' else False,
        'max_duration_minutes': int(request.form.get('max_duration_minutes', '0') or 0),
        'source_mode': source_mode,
    }

    meta = None
    if source_mode == 'url':
        source_url = request.form.get('source_url', '').strip()
        data['source_url'] = source_url
        already_probed = request.form.get('source_probe_url', '').strip() == source_url
        try:
            cached_duration = float(request.form.get('source_duration_seconds', '0') or 0)
        except Exception:
            cached_duration = 0
        if already_probed and (request.form.get('source_title') or cached_duration > 0):
            meta = {
                'ok': True,
                'url': source_url,
                'title': request.form.get('source_title', '').strip(),
                'duration_seconds': cached_duration,
                'extractor': request.form.get('source_extractor', '').strip(),
                'preview_url': request.form.get('source_preview_url', '').strip(),
            }
            validate_remote_url(source_url)
        else:
            try:
                meta = probe_remote_source(source_url)
            except Exception as e:
                flash('Não foi possível analisar a URL: ' + str(e), 'error')
                return redirect(request.referrer or url_for('web.schedules'))
        data.update({
            'source_title': meta.get('title', ''),
            'source_duration_seconds': meta.get('duration_seconds', 0),
            'source_extractor': meta.get('extractor', ''),
            'source_preview_url': meta.get('preview_url', ''),
        })

    try:
        sid = DB.save_schedule(data)
        flash('Agendamento salvo.', 'success')
        if request.form.get('run_preflight') == '1':
            result = _remote_preflight(ch, platforms, meta) if source_mode == 'url' else MANAGER.preflight(cid, platforms, media)
            bad = [x['name'] + ': ' + x['message'] for x in result.get('checks', []) if not x.get('ok')]
            flash('Pré-teste: ' + ('OK' if result.get('ok') else 'há pendências' + (': ' + ' | '.join(bad) if bad else '')),
                  'success' if result.get('ok') else 'error')
        return redirect(url_for('web.schedule_edit', sid=sid))
    except Exception as e:
        flash(str(e), 'error')
        return redirect(request.referrer or url_for('web.schedules'))


@urlmedia_bp.route('/library/import-url-v31', methods=['POST'])
def library_import_url():
    source = request.form.get('url', '').strip()
    kind = request.form.get('kind', 'video')
    try:
        validate_remote_url(source)
    except Exception as e:
        flash(str(e), 'error')
        return redirect(url_for('web.library'))

    def work():
        try:
            result = import_remote_to_library(source, kind)
            try:
                from .pro_db import add_alert
                add_alert('info', 'import', 'Importação concluída', result.get('message', source))
            except Exception:
                pass
        except Exception as e:
            try:
                from .pro_db import add_alert
                add_alert('error', 'import', 'Falha na importação', str(e))
            except Exception:
                pass

    threading.Thread(target=work, daemon=True, name='library-url-import').start()
    flash('Importação iniciada com yt-dlp em segundo plano. A biblioteca será atualizada quando terminar.', 'success')
    return redirect(url_for('web.library'))


def install_url_sources(app, db_module, web_module, streaming_module):
    global DB, WEB, STREAMING, MANAGER
    DB, WEB, STREAMING, MANAGER = db_module, web_module, streaming_module, streaming_module.MANAGER
    install_schedule_db(db_module, web_module)
    install_streaming(streaming_module.MANAGER, streaming_module)
    app.add_template_global(ytdlp_status, 'ytdlp_status')

    try:
        from . import professional
        original_diagnose = professional.diagnose

        def diagnose_with_ytdlp():
            report = original_diagnose()
            status = ytdlp_status()
            report.setdefault('checks', []).insert(2, {
                'name': 'yt-dlp',
                'ok': bool(status.get('ok')),
                'message': status.get('message', '') + f" · JS runtime: {'Node OK' if status.get('node') else 'Node ausente'}",
                'severity': 'error',
            })
            report['ok'] = all(c.get('ok') or c.get('severity') == 'warning' for c in report['checks'])
            return report

        professional.diagnose = diagnose_with_ytdlp
    except Exception:
        pass
    return streaming_module.MANAGER
