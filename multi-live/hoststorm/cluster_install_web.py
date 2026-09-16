from __future__ import annotations

import os
import secrets
import threading
import time
import uuid

from flask import Blueprint, jsonify, request

from .auth import require_role
from .cluster_bootstrap import install_agent
from .pro_db import save_node, update_node_health

cluster_install_bp = Blueprint('cluster_install', __name__)
_JOBS: dict[str, dict] = {}
_LOCK = threading.RLock()


def _job_update(job_id: str, **values):
    with _LOCK:
        job = _JOBS.setdefault(job_id, {'id': job_id, 'status': 'queued', 'logs': [], 'created_at': time.time()})
        job.update(values)
        job['updated_at'] = time.time()


def _job_log(job_id: str, message: str):
    with _LOCK:
        job = _JOBS.setdefault(job_id, {'id': job_id, 'status': 'queued', 'logs': [], 'created_at': time.time()})
        logs = job.setdefault('logs', [])
        logs.append(str(message))
        if len(logs) > 120:
            del logs[:-120]
        job['updated_at'] = time.time()


def _install(job_id: str, payload: dict):
    _job_update(job_id, status='running', message='Conectando ao servidor…')
    try:
        node_id = str(payload.get('node_id') or uuid.uuid4().hex[:12])
        payload['node_id'] = node_id
        result = install_agent(payload, lambda text: _job_log(job_id, text))
        tags = [x.strip() for x in str(payload.get('tags') or '').split(',') if x.strip()]
        tags = list(dict.fromkeys(tags + result.tags))
        save_node({
            'id': node_id,
            'name': payload.get('name') or result.name,
            'base_url': result.base_url,
            'token': result.agent_token,
            'priority': int(payload.get('priority') or 100),
            'enabled': True,
            'tags': tags,
        })
        update_node_health(node_id, 0, 0, 0, 0, 'online')
        _job_update(
            job_id,
            status='success',
            message='Servidor instalado, registrado e online.',
            node_id=node_id,
            base_url=result.base_url,
            capabilities=result.capabilities,
        )
    except Exception as exc:
        _job_log(job_id, 'ERRO: ' + str(exc))
        _job_update(job_id, status='error', message=str(exc))


@cluster_install_bp.route('/professional/nodes/install', methods=['POST'])
@require_role('admin')
def install_node():
    body = request.get_json(silent=True) or request.form
    payload = {
        'name': str(body.get('name') or '').strip(),
        'host': str(body.get('host') or '').strip(),
        'ssh_port': body.get('ssh_port') or 22,
        'username': str(body.get('username') or 'root').strip(),
        'password': str(body.get('password') or ''),
        'agent_port': body.get('agent_port') or 3040,
        'base_url': str(body.get('base_url') or '').strip(),
        'priority': body.get('priority') or 100,
        'tags': str(body.get('tags') or '').strip(),
        'controller_url': str(body.get('controller_url') or os.environ.get('HOSTSTORM_PUBLIC_URL') or '').strip(),
    }
    if not payload['host'] or not payload['password']:
        return jsonify({'ok': False, 'message': 'Informe IP/host e senha SSH.'}), 400
    job_id = secrets.token_hex(8)
    _job_update(job_id, status='queued', message='Provisionamento agendado.')
    threading.Thread(target=_install, args=(job_id, payload), daemon=True, name=f'cluster-install-{job_id[:6]}').start()
    return jsonify({'ok': True, 'job_id': job_id, 'message': 'Instalação iniciada.'}), 202


@cluster_install_bp.route('/api/cluster/install/<job_id>')
@require_role('admin')
def install_status(job_id):
    with _LOCK:
        job = dict(_JOBS.get(job_id) or {})
    if not job:
        return jsonify({'ok': False, 'message': 'Instalação não encontrada.'}), 404
    return jsonify({'ok': True, 'job': job})


def install_cluster_install_web(app):
    app.register_blueprint(cluster_install_bp)
    return app
