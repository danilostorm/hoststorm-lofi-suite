from __future__ import annotations

import time
import urllib.parse

from .integrations import get_integration
from .integrations_v32 import (
    _kick_headers,
    _request_json,
    apply_metadata,
    check_integration_v32,
    save_integration_v32,
    search_categories,
)
from .kick_oauth import normalize_token_payload, refresh_tokens


def ensure_kick_token(iid: str):
    item = get_integration(iid)
    if not item:
        raise RuntimeError('Integração não encontrada.')
    if item.get('provider') != 'kick':
        return item
    config = dict(item.get('config') or {})
    refresh = str(config.get('refresh_token') or '').strip()
    access = str(config.get('access_token') or '').strip()
    try:
        expires_at = int(float(config.get('token_expires_at') or 0))
    except Exception:
        expires_at = 0
    should_refresh = bool(refresh) and (not access or (expires_at > 0 and expires_at <= int(time.time()) + 90))
    if should_refresh:
        client_id = str(config.get('client_id') or '').strip()
        client_secret = str(config.get('client_secret') or '').strip()
        if not client_id or not client_secret:
            raise RuntimeError('Kick OAuth: Client ID/Secret ausentes; não foi possível renovar o token.')
        payload = refresh_tokens(client_id, client_secret, refresh)
        config.update(normalize_token_payload(payload, refresh))
        save_integration_v32('kick', item.get('name') or 'Kick', config, bool(item.get('enabled')), iid)
        item = get_integration(iid)
    return item


def search_categories_v33(iid: str, query: str):
    ensure_kick_token(iid)
    return search_categories(iid, query)


def apply_metadata_v33(iid: str, metadata: dict):
    ensure_kick_token(iid)
    return apply_metadata(iid, metadata)


def check_integration_v33(iid: str):
    item = get_integration(iid)
    if not item:
        return {'ok': False, 'message': 'Integração não encontrada.'}
    if item.get('provider') != 'kick':
        return check_integration_v32(iid)
    try:
        item = ensure_kick_token(iid)
        config = item.get('config') or {}
        # Com User Access Token, a API oficial pode retornar o canal autenticado sem slug manual.
        data = _request_json('https://api.kick.com/public/v1/channels', _kick_headers(config))
        rows = data.get('data') or []
        if not rows:
            slug = str(config.get('channel_slug') or '').strip()
            if slug:
                data = _request_json(
                    'https://api.kick.com/public/v1/channels?' + urllib.parse.urlencode({'slug': slug}),
                    _kick_headers(config),
                )
                rows = data.get('data') or []
        if not rows:
            return {'ok': True, 'live': False, 'message': 'Kick OAuth válido, mas o canal não foi retornado pela API.'}
        row = rows[0]
        slug = str(row.get('slug') or config.get('channel_slug') or '').strip()
        if slug and slug != config.get('channel_slug'):
            updated = dict(config)
            updated['channel_slug'] = slug
            updated['broadcaster_user_id'] = str(row.get('broadcaster_user_id') or updated.get('broadcaster_user_id') or '')
            save_integration_v32('kick', item.get('name') or slug or 'Kick', updated, bool(item.get('enabled')), iid)
        stream = row.get('stream') or {}
        live = bool(stream.get('is_live') or row.get('is_live'))
        mode = 'OAuth oficial' if config.get('oauth_connected') else 'token manual'
        return {'ok': True, 'live': live, 'message': f'Kick conectado via {mode}' + (f' • {slug}' if slug else '') + (' • online' if live else ' • offline')}
    except Exception as e:
        return {'ok': False, 'message': str(e)}
