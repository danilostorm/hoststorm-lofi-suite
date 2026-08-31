from __future__ import annotations

import base64
import json
import os
import urllib.parse
import uuid

from flask import Blueprint, flash, g, jsonify, redirect, request, session, url_for
from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from .pro_db import connect, get_user, touch_login
from .security import LOGIN_LIMITER, verify_password
from .utils import now_iso

passkey_bp = Blueprint('passkey', __name__)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(bytes(data)).rstrip(b'=').decode('ascii')


def init_passkey_db():
    with connect() as con:
        con.execute('''
            CREATE TABLE IF NOT EXISTS passkeys (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              name TEXT NOT NULL DEFAULT 'Passkey',
              credential_id TEXT NOT NULL UNIQUE,
              public_key TEXT NOT NULL,
              sign_count INTEGER NOT NULL DEFAULT 0,
              transports_json TEXT NOT NULL DEFAULT '[]',
              device_type TEXT NOT NULL DEFAULT '',
              backed_up INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              last_used_at TEXT NOT NULL DEFAULT '',
              FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        ''')
        con.execute('CREATE INDEX IF NOT EXISTS idx_passkeys_user ON passkeys(user_id,created_at)')
        con.execute("INSERT INTO meta(key,value) VALUES('passkey_schema_version','1') ON CONFLICT(key) DO UPDATE SET value='1'")


def list_passkeys(user_id):
    with connect() as con:
        return [dict(x) for x in con.execute(
            'SELECT id,user_id,name,credential_id,sign_count,transports_json,device_type,backed_up,created_at,last_used_at '
            'FROM passkeys WHERE user_id=? ORDER BY created_at DESC', (user_id,)
        ).fetchall()]


def get_passkey_by_credential(credential_id):
    with connect() as con:
        row = con.execute('SELECT * FROM passkeys WHERE credential_id=?', (credential_id,)).fetchone()
        return dict(row) if row else None


def save_passkey(user_id, name, verification, credential):
    pid = uuid.uuid4().hex[:12]
    response = (credential or {}).get('response') or {}
    transports = response.get('transports') or []
    device_type = getattr(verification.credential_device_type, 'value', str(verification.credential_device_type or ''))
    with connect() as con:
        con.execute(
            'INSERT INTO passkeys(id,user_id,name,credential_id,public_key,sign_count,transports_json,device_type,backed_up,created_at) '
            'VALUES(?,?,?,?,?,?,?,?,?,?)',
            (
                pid, user_id, str(name or 'Passkey')[:120], _b64(verification.credential_id),
                _b64(verification.credential_public_key), int(verification.sign_count or 0),
                json.dumps(transports), device_type, int(bool(verification.credential_backed_up)), now_iso(),
            ),
        )
    return pid


def update_passkey_use(pid, verification):
    device_type = getattr(verification.credential_device_type, 'value', str(verification.credential_device_type or ''))
    with connect() as con:
        con.execute(
            'UPDATE passkeys SET sign_count=?,device_type=?,backed_up=?,last_used_at=? WHERE id=?',
            (int(verification.new_sign_count or 0), device_type, int(bool(verification.credential_backed_up)), now_iso(), pid),
        )


def delete_passkey(pid, user_id):
    with connect() as con:
        con.execute('DELETE FROM passkeys WHERE id=? AND user_id=?', (pid, user_id))


def _public_origin():
    configured = str(os.environ.get('HOSTSTORM_PUBLIC_URL') or '').strip().rstrip('/')
    if configured:
        parsed = urllib.parse.urlparse(configured)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
            raise RuntimeError('HOSTSTORM_PUBLIC_URL inválida.')
        origin = f'{parsed.scheme}://{parsed.netloc}'
        rp_id = str(os.environ.get('HOSTSTORM_WEBAUTHN_RP_ID') or parsed.hostname)
    else:
        proto = request.headers.get('X-Forwarded-Proto', request.scheme).split(',')[0].strip()
        host = request.headers.get('X-Forwarded-Host', request.host).split(',')[0].strip()
        parsed = urllib.parse.urlparse(f'{proto}://{host}')
        origin = f'{proto}://{host}'
        rp_id = str(os.environ.get('HOSTSTORM_WEBAUTHN_RP_ID') or parsed.hostname or '')
    if not rp_id:
        raise RuntimeError('Não foi possível determinar o domínio da Passkey.')
    local = rp_id in {'localhost', '127.0.0.1', '::1'}
    if not origin.startswith('https://') and not local:
        raise RuntimeError('Passkeys exigem HTTPS. Configure HOSTSTORM_PUBLIC_URL=https://seu-dominio.')
    return rp_id, origin


def install_passkey_auth(auth_module):
    init_passkey_db()
    auth_module.PUBLIC_ENDPOINTS.update({'passkey.login_options', 'passkey.login_verify'})


@passkey_bp.route('/api/passkeys/register/options', methods=['POST'])
def register_options():
    user = get_user(getattr(g, 'user', {}).get('id')) if getattr(g, 'user', None) else None
    if not user:
        return jsonify({'ok': False, 'error': 'Login obrigatório.'}), 401
    body = request.get_json(silent=True) or {}
    if not verify_password(user.get('password_hash', ''), str(body.get('password') or '')):
        return jsonify({'ok': False, 'error': 'Confirme sua senha atual para cadastrar uma Passkey.'}), 403
    try:
        rp_id, origin = _public_origin()
        exclude = [PublicKeyCredentialDescriptor(id=base64url_to_bytes(x['credential_id'])) for x in list_passkeys(user['id'])]
        options = generate_registration_options(
            rp_id=rp_id,
            rp_name='HostStorm Broadcast',
            user_id=user['id'].encode('utf-8'),
            user_name=user['username'],
            exclude_credentials=exclude,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.REQUIRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
            timeout=60000,
        )
        payload = json.loads(options_to_json(options))
        session['passkey_reg_challenge'] = payload['challenge']
        session['passkey_reg_rp_id'] = rp_id
        session['passkey_reg_origin'] = origin
        session['passkey_reg_name'] = str(body.get('name') or 'Passkey')[:120]
        return jsonify({'ok': True, 'publicKey': payload})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 400


@passkey_bp.route('/api/passkeys/register/verify', methods=['POST'])
def register_verify():
    user = get_user(getattr(g, 'user', {}).get('id')) if getattr(g, 'user', None) else None
    if not user:
        return jsonify({'ok': False, 'error': 'Login obrigatório.'}), 401
    body = request.get_json(silent=True) or {}
    credential = body.get('credential') or {}
    try:
        verification = verify_registration_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(session.pop('passkey_reg_challenge')),
            expected_rp_id=session.pop('passkey_reg_rp_id'),
            expected_origin=session.pop('passkey_reg_origin'),
            require_user_verification=True,
        )
        save_passkey(user['id'], session.pop('passkey_reg_name', 'Passkey'), verification, credential)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': 'Não foi possível validar a Passkey: ' + str(e)}), 400


@passkey_bp.route('/api/passkeys/login/options', methods=['POST'])
def login_options():
    ip = request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip()
    key = f'passkey:{ip}'
    if not LOGIN_LIMITER.allowed(key):
        return jsonify({'ok': False, 'error': 'Muitas tentativas. Aguarde alguns minutos.'}), 429
    try:
        rp_id, origin = _public_origin()
        options = generate_authentication_options(
            rp_id=rp_id,
            user_verification=UserVerificationRequirement.REQUIRED,
            timeout=60000,
        )
        payload = json.loads(options_to_json(options))
        session['passkey_login_challenge'] = payload['challenge']
        session['passkey_login_rp_id'] = rp_id
        session['passkey_login_origin'] = origin
        return jsonify({'ok': True, 'publicKey': payload})
    except Exception as e:
        LOGIN_LIMITER.fail(key)
        return jsonify({'ok': False, 'error': str(e)}), 400


@passkey_bp.route('/api/passkeys/login/verify', methods=['POST'])
def login_verify():
    ip = request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip()
    key = f'passkey:{ip}'
    body = request.get_json(silent=True) or {}
    credential = body.get('credential') or {}
    record = get_passkey_by_credential(str(credential.get('id') or ''))
    if not record:
        LOGIN_LIMITER.fail(key)
        return jsonify({'ok': False, 'error': 'Passkey não reconhecida.'}), 401
    user = get_user(record['user_id'])
    if not user or not user.get('enabled'):
        LOGIN_LIMITER.fail(key)
        return jsonify({'ok': False, 'error': 'Conta desativada.'}), 403
    try:
        verification = verify_authentication_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(session.pop('passkey_login_challenge')),
            expected_rp_id=session.pop('passkey_login_rp_id'),
            expected_origin=session.pop('passkey_login_origin'),
            credential_public_key=base64url_to_bytes(record['public_key']),
            credential_current_sign_count=int(record.get('sign_count') or 0),
            require_user_verification=True,
        )
        update_passkey_use(record['id'], verification)
        LOGIN_LIMITER.success(key)
        session.clear()
        session['uid'] = user['id']
        session.permanent = True
        touch_login(user['id'])
        return jsonify({'ok': True, 'redirect': '/'})
    except Exception as e:
        LOGIN_LIMITER.fail(key)
        return jsonify({'ok': False, 'error': 'Falha ao validar Passkey: ' + str(e)}), 401


@passkey_bp.route('/security/passkeys/<pid>/delete', methods=['POST'])
def passkey_delete(pid):
    user = get_user(getattr(g, 'user', {}).get('id')) if getattr(g, 'user', None) else None
    if not user:
        return redirect(url_for('auth.login'))
    if not verify_password(user.get('password_hash', ''), request.form.get('password', '')):
        flash('Senha inválida. A Passkey não foi removida.', 'error')
    else:
        delete_passkey(pid, user['id'])
        flash('Passkey removida.', 'success')
    return redirect(url_for('auth.two_factor'))
