from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

KICK_AUTHORIZE_URL = 'https://id.kick.com/oauth/authorize'
KICK_TOKEN_URL = 'https://id.kick.com/oauth/token'
KICK_API_BASE = 'https://api.kick.com'
KICK_SCOPES = ('user:read', 'channel:read', 'channel:write')


def new_pkce():
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode('ascii')).digest()
    challenge = base64.urlsafe_b64encode(digest).decode('ascii').rstrip('=')
    return verifier, challenge


def new_state():
    return secrets.token_urlsafe(32)


def authorize_url(client_id: str, redirect_uri: str, state: str, challenge: str, scopes=None) -> str:
    params = {
        'response_type': 'code',
        'client_id': str(client_id or '').strip(),
        'redirect_uri': str(redirect_uri or '').strip(),
        'state': str(state or '').strip(),
        'scope': ' '.join(scopes or KICK_SCOPES),
        'code_challenge': str(challenge or '').strip(),
        'code_challenge_method': 'S256',
    }
    if not params['client_id'] or not params['redirect_uri'] or not params['state'] or not params['code_challenge']:
        raise ValueError('Kick OAuth: Client ID, redirect URI, state e PKCE são obrigatórios.')
    return KICK_AUTHORIZE_URL + '?' + urllib.parse.urlencode(params)


def _form_json(url: str, fields: dict, timeout=20) -> dict:
    data = urllib.parse.urlencode({k: str(v) for k, v in fields.items() if v not in (None, '')}).encode('utf-8')
    req = urllib.request.Request(
        url,
        data=data,
        method='POST',
        headers={'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode('utf-8', 'replace').strip()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8', 'replace') if hasattr(e, 'read') else ''
        raise RuntimeError(f'Kick OAuth HTTP {e.code}: {(raw or str(e))[-1600:]}') from e


def exchange_code(client_id: str, client_secret: str, redirect_uri: str, code: str, verifier: str) -> dict:
    return _form_json(KICK_TOKEN_URL, {
        'grant_type': 'authorization_code',
        'client_id': client_id,
        'client_secret': client_secret,
        'redirect_uri': redirect_uri,
        'code_verifier': verifier,
        'code': code,
    })


def refresh_tokens(client_id: str, client_secret: str, refresh_token: str) -> dict:
    return _form_json(KICK_TOKEN_URL, {
        'grant_type': 'refresh_token',
        'client_id': client_id,
        'client_secret': client_secret,
        'refresh_token': refresh_token,
    })


def normalize_token_payload(payload: dict, previous_refresh='') -> dict:
    expires_in = int(float(payload.get('expires_in') or 0))
    return {
        'access_token': str(payload.get('access_token') or '').strip(),
        'refresh_token': str(payload.get('refresh_token') or previous_refresh or '').strip(),
        'token_type': str(payload.get('token_type') or 'Bearer').strip(),
        'token_scope': str(payload.get('scope') or '').strip(),
        'token_expires_at': str(int(time.time()) + max(0, expires_in - 30)) if expires_in else '',
    }


def _api_json(path: str, access_token: str, timeout=15) -> dict:
    req = urllib.request.Request(
        KICK_API_BASE + path,
        headers={'Authorization': 'Bearer ' + str(access_token or '').strip(), 'Accept': 'application/json'},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode('utf-8', 'replace').strip()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8', 'replace') if hasattr(e, 'read') else ''
        raise RuntimeError(f'Kick API HTTP {e.code}: {(raw or str(e))[-1600:]}') from e


def discover_account(access_token: str) -> dict:
    users = _api_json('/public/v1/users', access_token)
    channels = _api_json('/public/v1/channels', access_token)
    user_rows = users.get('data') or []
    channel_rows = channels.get('data') or []
    user = user_rows[0] if user_rows else {}
    channel = channel_rows[0] if channel_rows else {}
    slug = str(channel.get('slug') or channel.get('channel_slug') or user.get('name') or user.get('username') or '').strip()
    return {
        'kick_user_id': str(user.get('user_id') or user.get('id') or '').strip(),
        'channel_slug': slug,
        'broadcaster_user_id': str(channel.get('broadcaster_user_id') or channel.get('user_id') or '').strip(),
        'oauth_connected': '1',
    }
