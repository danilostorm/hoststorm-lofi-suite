from __future__ import annotations

import copy
import json
import os
import threading
import time
from types import MethodType

from flask import Blueprint, abort, jsonify, request

from . import db, multi_output
from .distributed import _request, _sync_media, _sync_audio
from .output_management import _mark_output_stopped, _set_output_desired
from .pro_db import connect as pro_connect, list_nodes
from .utils import now_iso, safe_filename

cluster_bp = Blueprint('cluster_v5', __name__)
MANAGER = None
WEB = None
STREAMING = None

_CACHE_LOCK = threading.RLock()
_NODE_CACHE: dict[str, tuple[float, dict]] = {}


def init_cluster_store():
    with pro_connect() as con:
        con.execute(
            '''
            CREATE TABLE IF NOT EXISTS cluster_output_runs (
              channel_id TEXT NOT NULL,
              slug TEXT NOT NULL,
              node_id TEXT NOT NULL,
              desired INTEGER NOT NULL DEFAULT 1,
              payload_json TEXT NOT NULL DEFAULT '{}',
              failures INTEGER NOT NULL DEFAULT 0,
              started_at TEXT NOT NULL DEFAULT '',
              updated_at TEXT NOT NULL DEFAULT '',
              PRIMARY KEY(channel_id,slug)
            )
            '''
        )


def _save_assignment(cid: str, slug: str, node_id: str, payload: dict, desired=True, failures=0):
    ts = now_iso()
    with pro_connect() as con:
        old = con.execute(
            'SELECT started_at FROM cluster_output_runs WHERE channel_id=? AND slug=?',
            (str(cid), str(slug)),
        ).fetchone()
        started = old['started_at'] if old and old['started_at'] else ts
        con.execute(
            '''
            INSERT INTO cluster_output_runs(channel_id,slug,node_id,desired,payload_json,failures,started_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(channel_id,slug) DO UPDATE SET
              node_id=excluded.node_id,
              desired=excluded.desired,
              payload_json=excluded.payload_json,
              failures=excluded.failures,
              updated_at=excluded.updated_at
            ''',
            (
                str(cid), str(slug), str(node_id), int(bool(desired)),
                json.dumps(payload or {}, ensure_ascii=False), int(failures), started, ts,
            ),
        )


def _delete_assignment(cid: str, slug: str):
    with pro_connect() as con:
        con.execute('DELETE FROM cluster_output_runs WHERE channel_id=? AND slug=?', (str(cid), str(slug)))


def _assignment(cid: str, slug: str) -> dict | None:
    with pro_connect() as con:
        row = con.execute(
            'SELECT * FROM cluster_output_runs WHERE channel_id=? AND slug=?',
            (str(cid), str(slug)),
        ).fetchone()
    if not row:
        return None
    data = dict(row)
    try:
        data['payload'] = json.loads(data.pop('payload_json') or '{}')
    except Exception:
        data['payload'] = {}
    return data


def _assignments(cid: str | None = None, desired_only=False) -> list[dict]:
    sql = 'SELECT * FROM cluster_output_runs'
    args: list = []
    where = []
    if cid is not None:
        where.append('channel_id=?')
        args.append(str(cid))
    if desired_only:
        where.append('desired=1')
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += ' ORDER BY channel_id,slug'
    with pro_connect() as con:
        rows = con.execute(sql, args).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item['payload'] = json.loads(item.pop('payload_json') or '{}')
        except Exception:
            item['payload'] = {}
        result.append(item)
    return result


def _node(node_id: str) -> dict | None:
    return next((n for n in list_nodes() if str(n.get('id')) == str(node_id)), None)


def _score(node: dict) -> float:
    return (
        float(node.get('priority') or 100)
        + float(node.get('cpu') or 0) * .35
        + float(node.get('ram') or 0) * .25
        + float(node.get('gpu') or 0) * .15
        + int(node.get('active_streams') or 0) * 8
    )


def output_placement(channel: dict, slug: str, destination: dict | None = None) -> tuple[str, str]:
    destination = destination or ((channel.get('destinations') or {}).get(slug) or {})
    mode = str(destination.get('output_node_mode') or 'inherit').strip().lower()
    node_id = str(destination.get('output_node_id') or '').strip()
    if mode not in {'inherit', 'local', 'auto', 'specific'}:
        mode = 'inherit'
    if mode == 'inherit':
        channel_mode = str(channel.get('node_mode') or 'local').strip().lower()
        if channel_mode not in {'local', 'auto', 'specific'}:
            channel_mode = 'local'
        mode = channel_mode
        if not node_id:
            node_id = str(channel.get('node_id') or '').strip()
    return mode, node_id


def _select_node(channel: dict, slug: str, destination: dict | None = None, exclude=None) -> dict | None:
    mode, node_id = output_placement(channel, slug, destination)
    if mode == 'local':
        return None
    excluded = {str(x) for x in (exclude or [])}
    nodes = [
        n for n in list_nodes()
        if n.get('enabled') and n.get('status') == 'online' and str(n.get('id')) not in excluded
    ]
    if mode == 'specific':
        return next((n for n in nodes if str(n.get('id')) == node_id), None)
    tags_raw = str((destination or {}).get('required_node_tags') or channel.get('required_node_tags') or '')
    required = {x.strip() for x in tags_raw.split(',') if x.strip()}
    nodes = [n for n in nodes if required.issubset(set(n.get('tags') or []))]
    return min(nodes, key=_score) if nodes else None


def _node_status(node: dict, max_age=2.0) -> dict:
    nid = str(node.get('id') or '')
    now = time.time()
    with _CACHE_LOCK:
        cached = _NODE_CACHE.get(nid)
        if cached and now - cached[0] <= max_age:
            return cached[1]
    payload = _request(node, '/api/v1/status', timeout=5)
    with _CACHE_LOCK:
        _NODE_CACHE[nid] = (now, payload)
    return payload


def _media_for_output(channel: dict, slug: str) -> list[str]:
    destination = ((channel.get('destinations') or {}).get(slug) or {})
    mode = str(destination.get('output_source_mode') or 'channel')
    if mode == 'local':
        name = safe_filename(destination.get('output_source_video'))
        return [name] if name else []
    if mode == 'channel':
        name = safe_filename(channel.get('video'))
        return [name] if name else []
    return []



def _audio_for_output(channel: dict, slug: str) -> list[str]:
    destination = ((channel.get('destinations') or {}).get(slug) or {})
    mode = str(destination.get('output_audio_mode') or 'inherit').strip().lower()
    if mode in {'library', 'mix_library'}:
        name = safe_filename(destination.get('output_audio_file'))
        return [name] if name else []
    if mode in {'original', 'url', 'mix_url'}:
        return []

    kind = multi_output.platform_kind(slug, destination)
    vertical = kind == 'youtube_shorts' or (
        kind == 'kwai' and str(destination.get('mode') or 'horizontal') == 'vertical'
    )
    name = ''
    if vertical and str(channel.get('shorts_audio') or '__same__') != '__same__':
        name = safe_filename(channel.get('shorts_audio'))
    if not name:
        name = safe_filename(channel.get('audio'))
    return [name] if name else []


def _remote_snapshot(channel: dict, slug: str) -> dict:
    snapshot = copy.deepcopy(channel)
    snapshot['node_mode'] = 'local'
    snapshot['node_id'] = ''
    destination = ((snapshot.get('destinations') or {}).get(slug) or {})
    destination['output_node_mode'] = 'local'
    destination['output_node_id'] = ''
    snapshot.setdefault('destinations', {})[slug] = destination
    return snapshot


def _dispatch(node: dict, cid: str, slug: str, channel: dict) -> tuple[bool, str]:
    snapshot = _remote_snapshot(channel, slug)
    media = _media_for_output(snapshot, slug)
    audio = _audio_for_output(snapshot, slug)
    synced = _sync_media(node, snapshot, media)
    synced_audio = _sync_audio(node, audio)
    payload = {'channel': snapshot, 'slug': slug, 'media': media, 'audio': audio}
    response = _request(
        node,
        f'/api/v1/agent/output/{cid}/{slug}/start',
        'POST',
        payload,
        45,
    )
    if not response.get('ok'):
        raise RuntimeError(response.get('message') or response.get('error') or 'Agent recusou a saída.')
    _save_assignment(cid, slug, node['id'], payload, True, 0)
    _set_output_desired(cid, slug, True)
    total_synced = len(synced) + len(synced_audio)
    suffix = f' · {total_synced} arquivo(s) sincronizado(s)' if total_synced else ''
    return True, f"Live desta saída iniciada em {node.get('name') or node['id']}.{suffix}"


def _stop_remote(assignment: dict, reason='parada individual') -> tuple[bool, str]:
    node = _node(assignment.get('node_id'))
    if not node:
        _delete_assignment(assignment['channel_id'], assignment['slug'])
        _mark_output_stopped(assignment['channel_id'], assignment['slug'], reason)
        return True, 'Saída removida do cluster; nó anterior não está mais cadastrado.'
    try:
        response = _request(
            node,
            f"/api/v1/agent/output/{assignment['channel_id']}/{assignment['slug']}/stop",
            'POST',
            {'reason': reason},
            12,
        )
        ok = bool(response.get('ok'))
        message = response.get('message') or ('Live remota parada.' if ok else 'Falha parando live remota.')
    except Exception as exc:
        ok = False
        message = 'Agent indisponível: ' + str(exc)
    if ok:
        _delete_assignment(assignment['channel_id'], assignment['slug'])
        _mark_output_stopped(assignment['channel_id'], assignment['slug'], reason)
    return ok, message


@cluster_bp.route('/api/cluster/nodes')
def cluster_nodes():
    nodes = []
    for node in list_nodes():
        nodes.append({
            'id': node.get('id'), 'name': node.get('name'), 'status': node.get('status'),
            'cpu': node.get('cpu'), 'ram': node.get('ram'), 'gpu': node.get('gpu'),
            'active_streams': node.get('active_streams'), 'tags': node.get('tags') or [],
        })
    return jsonify({'ok': True, 'nodes': nodes})


@cluster_bp.route('/lives/<cid>/outputs/<slug>/migrate', methods=['POST'])
def migrate_output(cid, slug):
    channel = WEB.get_channel(cid, False) if WEB else None
    if not channel:
        abort(404)
    destination = (channel.get('destinations') or {}).get(slug)
    if not destination:
        abort(404)
    target = str((request.get_json(silent=True) or request.form).get('node_id') or '').strip()
    previous = _assignment(cid, slug)
    if target in {'', 'local'}:
        if previous:
            _stop_remote(previous, 'migração para controlador local')
        ok, message = _ORIGINAL_START(MANAGER, cid, slug)
        return jsonify({'ok': ok, 'message': message}), 200 if ok else 400
    node = _node(target)
    if not node or not node.get('enabled') or node.get('status') != 'online':
        return jsonify({'ok': False, 'message': 'Servidor de destino indisponível.'}), 409
    try:
        ok, message = _dispatch(node, cid, slug, channel)
        if ok and previous and previous.get('node_id') != target:
            old = _node(previous.get('node_id'))
            if old:
                try:
                    _request(old, f'/api/v1/agent/output/{cid}/{slug}/stop', 'POST', {'reason': 'migração concluída'}, 10)
                except Exception:
                    pass
        return jsonify({'ok': ok, 'message': message}), 200 if ok else 400
    except Exception as exc:
        return jsonify({'ok': False, 'message': str(exc)}), 400


_ORIGINAL_START = None
_ORIGINAL_STOP = None


def install_cluster_v5(app, web_module, streaming_module):
    global MANAGER, WEB, STREAMING, _ORIGINAL_START, _ORIGINAL_STOP
    MANAGER, WEB, STREAMING = streaming_module.MANAGER, web_module, streaming_module
    init_cluster_store()
    if os.environ.get('HOSTSTORM_AGENT_MODE') == '1':
        app.register_blueprint(cluster_bp)
        return MANAGER

    original_start = multi_output._start_output
    original_stop = multi_output._stop_output
    original_status = MANAGER.channel_status
    original_start_threads = MANAGER.start_threads
    _ORIGINAL_START, _ORIGINAL_STOP = original_start, original_stop
    MANAGER._hs_cluster_v5_started = False

    def start_output(manager_obj, cid: str, slug: str):
        channel = WEB.get_channel(cid) if WEB else None
        if not channel:
            return False, 'Canal não encontrado.'
        destination = (channel.get('destinations') or {}).get(slug)
        if not destination:
            return False, 'Destino não encontrado.'
        mode, _ = output_placement(channel, slug, destination)
        if mode == 'local':
            existing = _assignment(cid, slug)
            if existing:
                _stop_remote(existing, 'alterado para execução local')
            return original_start(manager_obj, cid, slug)
        node = _select_node(channel, slug, destination)
        if not node:
            if mode == 'auto':
                return original_start(manager_obj, cid, slug)
            return False, 'Servidor selecionado está indisponível.'
        try:
            return _dispatch(node, cid, slug, channel)
        except Exception as exc:
            if mode == 'auto':
                try:
                    return original_start(manager_obj, cid, slug)
                except Exception:
                    pass
            return False, 'Falha iniciando no servidor remoto: ' + str(exc)

    def stop_output(manager_obj, cid: str, slug: str, reason='parada individual'):
        assignment = _assignment(cid, slug)
        if assignment and assignment.get('desired'):
            return _stop_remote(assignment, reason)
        return original_stop(manager_obj, cid, slug, reason)

    def channel_status(self, cid):
        data = original_status(cid)
        platforms = dict(data.get('platforms') or {})
        for assignment in _assignments(cid, desired_only=True):
            slug = assignment['slug']
            node = _node(assignment['node_id'])
            remote = {
                'running': False, 'pid': 0, 'retries': int(assignment.get('failures') or 0),
                'last_error': '', 'remote': True, 'node_id': assignment['node_id'],
                'node_name': node.get('name') if node else assignment['node_id'],
            }
            if node:
                try:
                    payload = _node_status(node)
                    state = (((payload.get('channels') or {}).get(str(cid)) or {}).get('platforms') or {}).get(slug) or {}
                    remote.update(state)
                    remote['remote'] = True
                    remote['node_id'] = node['id']
                    remote['node_name'] = node.get('name') or node['id']
                except Exception as exc:
                    remote['last_error'] = 'Agent indisponível: ' + str(exc)
            platforms[slug] = remote
        data['platforms'] = platforms
        data['running'] = any(bool(state.get('running')) for state in platforms.values())
        data['cluster'] = {
            'outputs': {
                a['slug']: {'node_id': a['node_id'], 'desired': bool(a['desired']), 'failures': a['failures']}
                for a in _assignments(cid)
            }
        }
        return data

    def supervisor(self):
        time.sleep(8)
        while True:
            for assignment in _assignments(desired_only=True):
                cid, slug = assignment['channel_id'], assignment['slug']
                channel = WEB.get_channel(cid, False) if WEB else None
                if not channel or slug not in (channel.get('destinations') or {}):
                    _delete_assignment(cid, slug)
                    continue
                node = _node(assignment['node_id'])
                healthy = False
                if node:
                    try:
                        payload = _node_status(node, max_age=0)
                        state = (((payload.get('channels') or {}).get(cid) or {}).get('platforms') or {}).get(slug) or {}
                        healthy = bool(state.get('running'))
                    except Exception:
                        healthy = False
                if healthy:
                    if assignment.get('failures'):
                        _save_assignment(cid, slug, assignment['node_id'], assignment.get('payload') or {}, True, 0)
                    continue
                failures = int(assignment.get('failures') or 0) + 1
                _save_assignment(cid, slug, assignment['node_id'], assignment.get('payload') or {}, True, failures)
                if failures < 3:
                    continue
                destination = (channel.get('destinations') or {}).get(slug) or {}
                mode, _ = output_placement(channel, slug, destination)
                if mode != 'auto':
                    continue
                next_node = _select_node(channel, slug, destination, exclude={assignment['node_id']})
                if next_node:
                    try:
                        _dispatch(next_node, cid, slug, channel)
                        STREAMING.audit('warning', 'cluster_failover', cid, f'{slug} migrado automaticamente para {next_node.get("name")}.', {'platform': slug, 'node_id': next_node['id']})
                        continue
                    except Exception:
                        pass
                try:
                    ok, _ = original_start(self, cid, slug)
                    if ok:
                        _delete_assignment(cid, slug)
                        STREAMING.audit('warning', 'cluster_failover_local', cid, f'{slug} retomado no controlador local.', {'platform': slug})
                except Exception:
                    pass
            time.sleep(10)

    def start_threads(self):
        original_start_threads()
        if self._hs_cluster_v5_started:
            return
        self._hs_cluster_v5_started = True
        threading.Thread(target=supervisor, args=(self,), daemon=True, name='cluster-v5-failover').start()

    multi_output._start_output = start_output
    multi_output._stop_output = stop_output
    MANAGER.channel_status = MethodType(channel_status, MANAGER)
    MANAGER.start_threads = MethodType(start_threads, MANAGER)
    app.register_blueprint(cluster_bp)
    return MANAGER
