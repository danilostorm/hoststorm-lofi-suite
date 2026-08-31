from __future__ import annotations

import json
import os
import uuid
from functools import wraps
from pathlib import Path

from flask import Blueprint, abort, flash, g, jsonify, redirect, request, url_for

from .auth import require_role
from .config import DATA_DIR, VIDEOS_DIR
from .pro_db import authenticate_token
from .push import delete_subscription, public_key, save_subscription
from .security import role_allows
from .utils import now_iso

ops_bp = Blueprint('ops', __name__)

UPDATE_REQUEST = DATA_DIR / 'update-request.json'
UPDATE_PROCESSING = DATA_DIR / 'update-processing.json'
UPDATE_RESULT = DATA_DIR / 'update-result.json'
VIDEO_EXTS = {'.mp4', '.mov', '.mkv', '.webm', '.avi', '.m4v', '.ts'}


def _atomic_json(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, path)


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
    except Exception:
        return None


def _bearer():
    h = request.headers.get('Authorization', '')
    return h[7:].strip() if h.lower().startswith('bearer ') else ''


def api_scope(scope='read'):
    def deco(fn):
        @wraps(fn)
        def inner(*args, **kwargs):
            user = getattr(g, 'user', None)
            if user:
                minimum = 'operator' if scope in {'write', 'control', 'agent'} else 'viewer'
                if not role_allows(user.get('role'), minimum):
                    abort(403)
                return fn(*args, **kwargs)
            token = authenticate_token(_bearer())
            if not token:
                abort(401)
            scopes = set(token.get('scopes') or [])
            if '*' not in scopes and scope not in scopes:
                abort(403)
            g.api_identity = token
            return fn(*args, **kwargs)
        return inner
    return deco


@ops_bp.route('/professional/push/public-key')
@require_role('viewer')
def push_public_key():
    return jsonify({'ok': True, 'public_key': public_key()})


@ops_bp.route('/professional/push/subscribe', methods=['POST'])
@require_role('viewer')
def push_subscribe():
    payload = request.get_json(silent=True) or {}
    try:
        sid = save_subscription(
            g.user.get('id', ''),
            payload,
            request.headers.get('User-Agent', ''),
        )
        return jsonify({'ok': True, 'subscription_id': sid})
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 400


@ops_bp.route('/professional/push/unsubscribe', methods=['POST'])
@require_role('viewer')
def push_unsubscribe():
    payload = request.get_json(silent=True) or {}
    delete_subscription(payload.get('endpoint', ''))
    return jsonify({'ok': True})


@ops_bp.route('/professional/update/request', methods=['POST'])
@require_role('admin')
def update_request():
    channel = str(request.form.get('channel') or 'stable').lower().strip()
    if channel not in {'stable', 'beta'}:
        flash('Canal de atualização inválido.', 'error')
        return redirect(url_for('pro.updater'))
    if UPDATE_REQUEST.exists() or UPDATE_PROCESSING.exists():
        flash('Já existe uma atualização aguardando ou em processamento.', 'error')
        return redirect(url_for('pro.updater'))
    payload = {
        'id': uuid.uuid4().hex[:12],
        'channel': channel,
        'requested_at': now_iso(),
        'requested_by': g.user.get('username', ''),
        'status': 'queued',
    }
    _atomic_json(UPDATE_REQUEST, payload)
    flash(f'Atualização {channel.upper()} enviada ao agente do host.', 'success')
    return redirect(url_for('pro.updater'))


@ops_bp.route('/professional/update/status')
@require_role('admin')
def update_status():
    return jsonify({
        'ok': True,
        'queued': _read_json(UPDATE_REQUEST),
        'processing': _read_json(UPDATE_PROCESSING),
        'result': _read_json(UPDATE_RESULT),
    })


@ops_bp.route('/api/v1/agent/media/manifest')
@api_scope('agent')
def agent_media_manifest():
    items = {}
    for p in VIDEOS_DIR.iterdir():
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS:
            try:
                st = p.stat()
                items[p.name] = {'size': st.st_size, 'mtime': int(st.st_mtime)}
            except OSError:
                pass
    return jsonify({'ok': True, 'items': items})


@ops_bp.route('/api/v1/agent/media/<path:name>', methods=['PUT'])
@api_scope('agent')
def agent_media_put(name):
    filename = Path(name).name
    if filename != name or Path(filename).suffix.lower() not in VIDEO_EXTS:
        return jsonify({'ok': False, 'error': 'Nome de mídia inválido.'}), 400
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    target = VIDEOS_DIR / filename
    tmp = VIDEOS_DIR / ('.sync-' + uuid.uuid4().hex + '-' + filename)
    total = 0
    try:
        with tmp.open('wb') as fh:
            while True:
                chunk = request.stream.read(8 * 1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                fh.write(chunk)
        expected = request.content_length
        if expected is not None and total != expected:
            raise RuntimeError(f'Tamanho recebido {total} difere do esperado {expected}.')
        os.replace(tmp, target)
        return jsonify({'ok': True, 'name': filename, 'size': total})
    except Exception as exc:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return jsonify({'ok': False, 'error': str(exc)}), 500
