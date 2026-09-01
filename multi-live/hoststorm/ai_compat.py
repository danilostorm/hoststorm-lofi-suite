from __future__ import annotations

from flask import Blueprint, jsonify, request

from .ai_chat import ingest_kick_webhook
from .db import list_channels

compat_bp=Blueprint('ai_compat',__name__)


@compat_bp.app_context_processor
def ai_template_context():
    # Mantém o seletor de canal disponível nos templates do AI Host sem duplicar consultas em cada rota.
    try:return {'channels':list_channels(False)}
    except Exception:return {'channels':{}}


@compat_bp.route('/api/kick/webhook',methods=['POST'])
def kick_webhook_legacy():
    """Alias para apps Kick já configurados antes do AI Host.

    O usuário não precisa trocar o webhook no Developer Dashboard: a assinatura RSA continua obrigatória.
    """
    raw=request.get_data(cache=False)
    try:
        mid=ingest_kick_webhook(request.headers,raw);return jsonify({'ok':True,'message_id':mid}),200
    except PermissionError as exc:return jsonify({'ok':False,'error':str(exc)}),401
    except Exception as exc:return jsonify({'ok':False,'error':str(exc)}),400
