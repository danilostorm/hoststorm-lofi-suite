from __future__ import annotations

import os
from pathlib import Path

from flask import Blueprint, jsonify, request

from . import multi_output
from .config import VIDEOS_DIR
from .distributed import upsert_snapshot
from .pro_web import api_required

agent_cluster_bp = Blueprint('agent_cluster', __name__)
MANAGER = None


def _safe_name(value: str) -> str:
    return Path(str(value or '')).name


@agent_cluster_bp.route('/api/v1/agent/media/manifest')
@api_required('agent')
def media_manifest():
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    items = {}
    for path in VIDEOS_DIR.iterdir():
        if not path.is_file():
            continue
        stat = path.stat()
        items[path.name] = {'size': stat.st_size, 'mtime': int(stat.st_mtime)}
    return jsonify({'ok': True, 'items': items})


@agent_cluster_bp.route('/api/v1/agent/media/<path:name>', methods=['PUT'])
@api_required('agent')
def media_upload(name):
    safe = _safe_name(name)
    if not safe or safe in {'.', '..'}:
        return jsonify({'ok': False, 'error': 'Nome de arquivo inválido.'}), 400
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    target = VIDEOS_DIR / safe
    temp = VIDEOS_DIR / ('.upload-' + safe + '.part')
    total = 0
    try:
        with temp.open('wb') as fh:
            while True:
                chunk = request.stream.read(8 * 1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                fh.write(chunk)
        os.replace(temp, target)
    except Exception as exc:
        try:
            temp.unlink(missing_ok=True)
        except Exception:
            pass
        return jsonify({'ok': False, 'error': str(exc)}), 500
    return jsonify({'ok': True, 'name': safe, 'size': total})


@agent_cluster_bp.route('/api/v1/agent/output/<cid>/<slug>/start', methods=['POST'])
@api_required('agent')
def start_output(cid, slug):
    body = request.get_json(silent=True) or {}
    channel = dict(body.get('channel') or {})
    if not channel:
        return jsonify({'ok': False, 'error': 'Snapshot do canal obrigatório.'}), 400
    channel['node_mode'] = 'local'
    channel['node_id'] = ''
    destination = ((channel.get('destinations') or {}).get(slug) or {})
    if not destination:
        return jsonify({'ok': False, 'error': 'Destino não existe no snapshot.'}), 404
    destination['output_node_mode'] = 'local'
    destination['output_node_id'] = ''
    channel.setdefault('destinations', {})[slug] = destination
    channel['id'] = str(channel.get('id') or cid)
    upsert_snapshot(channel)
    ok, message = multi_output._start_output(MANAGER, str(cid), str(slug))
    return jsonify({'ok': bool(ok), 'message': message, 'channel_id': cid, 'slug': slug}), 200 if ok else 400


@agent_cluster_bp.route('/api/v1/agent/output/<cid>/<slug>/stop', methods=['POST'])
@api_required('agent')
def stop_output(cid, slug):
    reason = str((request.get_json(silent=True) or {}).get('reason') or 'cluster controller')
    ok, message = multi_output._stop_output(MANAGER, str(cid), str(slug), reason)
    return jsonify({'ok': bool(ok), 'message': message}), 200 if ok else 400


def install_agent_cluster_api(app, streaming_module):
    global MANAGER
    MANAGER = streaming_module.MANAGER
    app.register_blueprint(agent_cluster_bp)
    return MANAGER
