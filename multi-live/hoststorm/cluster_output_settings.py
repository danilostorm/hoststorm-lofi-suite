from __future__ import annotations

import copy

from flask import Blueprint, abort, jsonify, request

from . import multi_output
from .pro_db import list_nodes

cluster_output_settings_bp = Blueprint('cluster_output_settings', __name__)
WEB = None

VALID_MODES = {'inherit', 'local', 'auto', 'specific'}


@cluster_output_settings_bp.route('/lives/<cid>/outputs/<slug>/placement', methods=['POST'])
def save_output_placement(cid, slug):
    channel = WEB.get_channel(cid) if WEB else None
    if not channel:
        abort(404)
    destination = (channel.get('destinations') or {}).get(slug)
    if not destination:
        abort(404)

    body = request.get_json(silent=True) or request.form
    mode = str(body.get('mode') or 'inherit').strip().lower()
    node_id = str(body.get('node_id') or '').strip()
    if mode not in VALID_MODES:
        return jsonify({'ok': False, 'message': 'Modo de servidor inválido.'}), 400
    if mode == 'specific':
        node = next((n for n in list_nodes() if str(n.get('id')) == node_id and n.get('enabled')), None)
        if not node:
            return jsonify({'ok': False, 'message': 'Servidor selecionado não existe ou está desabilitado.'}), 400
    else:
        node_id = ''

    updated = copy.deepcopy(destination)
    updated['output_node_mode'] = mode
    updated['output_node_id'] = node_id
    destinations = copy.deepcopy(channel.get('destinations') or {})
    destinations[slug] = updated
    WEB.save_channel(
        cid,
        channel.get('name') or 'Canal',
        multi_output._channel_settings(channel),
        destinations,
    )
    label = {
        'inherit': 'herdar padrão do canal',
        'local': 'controlador local',
        'auto': 'automático',
        'specific': next((n.get('name') for n in list_nodes() if str(n.get('id')) == node_id), node_id),
    }[mode]
    return jsonify({'ok': True, 'message': f'Servidor desta saída: {label}.', 'mode': mode, 'node_id': node_id})


def install_cluster_output_settings(app, web_module):
    global WEB
    WEB = web_module
    app.register_blueprint(cluster_output_settings_bp)
    return app
