from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from .integrations import get_integration, list_integrations, save_integration

PROVIDER_SPECS = {
    'twitch': {
        'label': 'Twitch', 'platforms': ['twitch'], 'capabilities': ['title', 'category'],
        'scope': 'channel:manage:broadcast',
        'hint': 'Título e categoria/jogo. Requer User Access Token com channel:manage:broadcast.',
    },
    'youtube': {
        'label': 'YouTube', 'platforms': ['youtube', 'youtube_shorts'], 'capabilities': ['title', 'category', 'description'],
        'scope': 'https://www.googleapis.com/auth/youtube.force-ssl',
        'hint': 'Título, descrição e categoria do broadcast ativo/próximo. Requer OAuth Access Token.',
    },
    'kick': {
        'label': 'Kick', 'platforms': ['kick'], 'capabilities': ['title', 'category'],
        'scope': 'channel:read channel:write',
        'hint': 'Título e categoria. Requer token OAuth da Kick com channel:write.',
    },
    'webhook': {
        'label': 'Webhook / API externa', 'platforms': [], 'capabilities': ['title', 'category', 'description'],
        'scope': 'custom',
        'hint': 'Envia JSON para uma automação externa antes da live.',
    },
}


def provider_specs():
    return {k: dict(v) for k, v in PROVIDER_SPECS.items()}


def _request_json(url, headers=None, method='GET', payload=None, timeout=15):
    body = None
    hdr = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        hdr.setdefault('Content-Type', 'application/json')
    req = urllib.request.Request(url, data=body, headers=hdr, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode('utf-8', 'replace').strip()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8', 'replace') if hasattr(e, 'read') else ''
        message = raw[-1600:] if raw else str(e)
        raise RuntimeError(f'HTTP {e.code}: {message}') from e


def _twitch_headers(config):
    client_id = str(config.get('client_id') or '').strip()
    token = str(config.get('access_token') or '').strip()
    if not client_id or not token:
        raise RuntimeError('Twitch: Client ID e OAuth Access Token são obrigatórios.')
    return {'Client-Id': client_id, 'Authorization': 'Bearer ' + token}


def _twitch_broadcaster(config):
    broadcaster = str(config.get('broadcaster_id') or '').strip()
    if broadcaster:
        return broadcaster
    login = str(config.get('channel_login') or '').strip()
    if not login:
        raise RuntimeError('Twitch: informe Channel login.')
    data = _request_json('https://api.twitch.tv/helix/users?login=' + urllib.parse.quote(login), _twitch_headers(config))
    rows = data.get('data') or []
    if not rows:
        raise RuntimeError('Twitch: canal não encontrado para o login informado.')
    return str(rows[0].get('id') or '')


def _twitch_categories(config, query):
    query = str(query or '').strip()
    if not query:
        return []
    data = _request_json(
        'https://api.twitch.tv/helix/search/categories?' + urllib.parse.urlencode({'query': query, 'first': 10}),
        _twitch_headers(config),
    )
    return [{'id': str(x.get('id') or ''), 'name': str(x.get('name') or '')} for x in (data.get('data') or [])]


def _kick_headers(config):
    token = str(config.get('access_token') or '').strip()
    if not token:
        raise RuntimeError('Kick: OAuth Access Token é obrigatório.')
    return {'Authorization': 'Bearer ' + token, 'Accept': 'application/json'}


def _kick_categories(config, query):
    query = str(query or '').strip()
    if not query:
        return []
    if query.isdigit():
        return [{'id': query, 'name': query}]
    q = urllib.parse.urlencode({'name': query, 'limit': 10})
    data = _request_json('https://api.kick.com/public/v2/categories?' + q, _kick_headers(config))
    return [{'id': str(x.get('id') or ''), 'name': str(x.get('name') or '')} for x in (data.get('data') or [])]


def _youtube_headers(config):
    token = str(config.get('access_token') or '').strip()
    if not token:
        raise RuntimeError('YouTube: OAuth Access Token é obrigatório para alterar metadados.')
    return {'Authorization': 'Bearer ' + token, 'Accept': 'application/json'}


def _youtube_categories(config, query):
    query = str(query or '').strip()
    if not query:
        return []
    if query.isdigit():
        return [{'id': query, 'name': query}]
    params = {'part': 'snippet', 'regionCode': 'BR', 'hl': 'pt_BR'}
    key = str(config.get('api_key') or '').strip()
    if key:
        params['key'] = key
    data = _request_json('https://www.googleapis.com/youtube/v3/videoCategories?' + urllib.parse.urlencode(params), _youtube_headers(config))
    low = query.casefold()
    rows = []
    for item in data.get('items') or []:
        title = str((item.get('snippet') or {}).get('title') or '')
        if low in title.casefold() or title.casefold() in low:
            rows.append({'id': str(item.get('id') or ''), 'name': title})
    if not rows:
        rows = [{'id': str(x.get('id') or ''), 'name': str((x.get('snippet') or {}).get('title') or '')} for x in (data.get('items') or [])]
    return rows[:10]


def search_categories(iid, query):
    item = get_integration(iid)
    if not item or not item.get('enabled'):
        raise RuntimeError('Integração não encontrada ou desativada.')
    provider = item.get('provider')
    config = item.get('config') or {}
    if provider == 'twitch':
        return _twitch_categories(config, query)
    if provider == 'kick':
        return _kick_categories(config, query)
    if provider == 'youtube':
        return _youtube_categories(config, query)
    return []


def _pick_category(rows, query):
    if not rows:
        return ''
    q = str(query or '').strip().casefold()
    exact = next((x for x in rows if str(x.get('name') or '').casefold() == q), None)
    return str((exact or rows[0]).get('id') or '')


def _twitch_update(config, metadata):
    broadcaster = _twitch_broadcaster(config)
    payload = {}
    if metadata.get('title'):
        payload['title'] = str(metadata['title'])[:140]
    if metadata.get('category'):
        cid = _pick_category(_twitch_categories(config, metadata['category']), metadata['category'])
        if not cid:
            raise RuntimeError('Twitch: categoria/jogo não encontrado.')
        payload['game_id'] = cid
    if payload:
        url = 'https://api.twitch.tv/helix/channels?' + urllib.parse.urlencode({'broadcaster_id': broadcaster})
        _request_json(url, _twitch_headers(config), method='PATCH', payload=payload)
    return {'ok': True, 'provider': 'twitch', 'applied': sorted(payload), 'message': 'Twitch atualizado.' if payload else 'Twitch sem campos para alterar.'}


def _kick_update(config, metadata):
    payload = {}
    if metadata.get('title'):
        payload['stream_title'] = str(metadata['title'])[:200]
    if metadata.get('category'):
        cid = _pick_category(_kick_categories(config, metadata['category']), metadata['category'])
        if not cid:
            raise RuntimeError('Kick: categoria não encontrada.')
        payload['category_id'] = int(cid) if str(cid).isdigit() else cid
    if payload:
        _request_json('https://api.kick.com/public/v1/channels', _kick_headers(config), method='PATCH', payload=payload)
    return {'ok': True, 'provider': 'kick', 'applied': sorted(payload), 'message': 'Kick atualizado.' if payload else 'Kick sem campos para alterar.'}


def _youtube_find_broadcast(config):
    configured = str(config.get('broadcast_id') or '').strip()
    if configured:
        return configured
    headers = _youtube_headers(config)
    base = 'https://www.googleapis.com/youtube/v3/liveBroadcasts?'
    for status in ('active', 'upcoming'):
        params = {'part': 'id,snippet,status', 'broadcastStatus': status, 'mine': 'true', 'maxResults': 10}
        data = _request_json(base + urllib.parse.urlencode(params), headers)
        rows = data.get('items') or []
        if rows:
            if status == 'upcoming':
                rows.sort(key=lambda x: str((x.get('snippet') or {}).get('scheduledStartTime') or ''))
            return str(rows[0].get('id') or '')
    raise RuntimeError('YouTube: nenhum broadcast ativo ou próximo foi encontrado. Você pode informar Broadcast ID na integração.')


def _youtube_update(config, metadata):
    headers = _youtube_headers(config)
    bid = _youtube_find_broadcast(config)
    params = urllib.parse.urlencode({'part': 'snippet', 'id': bid})
    current = _request_json('https://www.googleapis.com/youtube/v3/liveBroadcasts?' + params, headers)
    rows = current.get('items') or []
    if not rows:
        raise RuntimeError('YouTube: broadcast não encontrado.')
    old = rows[0].get('snippet') or {}
    allowed = {'title', 'description', 'categoryId', 'scheduledStartTime', 'scheduledEndTime'}
    snippet = {k: v for k, v in old.items() if k in allowed and v not in (None, '')}
    applied = []
    if metadata.get('title'):
        snippet['title'] = str(metadata['title'])[:100]
        applied.append('title')
    if metadata.get('description'):
        snippet['description'] = str(metadata['description'])[:10000]
        applied.append('description')
    if metadata.get('category'):
        cid = _pick_category(_youtube_categories(config, metadata['category']), metadata['category'])
        if not cid:
            raise RuntimeError('YouTube: categoria não encontrada.')
        snippet['categoryId'] = cid
        applied.append('category')
    if applied:
        url = 'https://www.googleapis.com/youtube/v3/liveBroadcasts?' + urllib.parse.urlencode({'part': 'snippet'})
        _request_json(url, headers, method='PUT', payload={'id': bid, 'snippet': snippet})
    return {'ok': True, 'provider': 'youtube', 'broadcast_id': bid, 'applied': applied, 'message': 'YouTube atualizado.' if applied else 'YouTube sem campos para alterar.'}


def _webhook_update(config, metadata):
    endpoint = str(config.get('endpoint_url') or '').strip()
    if not endpoint.startswith(('http://', 'https://')):
        raise RuntimeError('Webhook: endpoint_url inválido.')
    headers = {'Accept': 'application/json'}
    token = str(config.get('bearer_token') or '').strip()
    if token:
        headers['Authorization'] = 'Bearer ' + token
    response = _request_json(endpoint, headers, method='POST', payload=metadata)
    return {'ok': True, 'provider': 'webhook', 'applied': ['payload'], 'message': 'Webhook enviado.', 'response': response}


def apply_metadata(iid, metadata):
    item = get_integration(iid)
    if not item:
        raise RuntimeError('Integração não encontrada.')
    if not item.get('enabled'):
        return {'ok': True, 'provider': item.get('provider'), 'skipped': True, 'message': 'Integração desativada.'}
    provider = item.get('provider')
    config = item.get('config') or {}
    if provider == 'twitch':
        result = _twitch_update(config, metadata)
    elif provider == 'youtube':
        result = _youtube_update(config, metadata)
    elif provider == 'kick':
        result = _kick_update(config, metadata)
    elif provider == 'webhook':
        result = _webhook_update(config, metadata)
    else:
        raise RuntimeError('Provider sem automação de metadados implementada.')
    result['integration_id'] = iid
    result['integration_name'] = item.get('name')
    return result


def check_integration_v32(iid):
    item = get_integration(iid)
    if not item:
        return {'ok': False, 'message': 'Integração não encontrada.'}
    p = item.get('provider')
    c = item.get('config') or {}
    try:
        if p == 'twitch':
            broadcaster = _twitch_broadcaster(c)
            data = _request_json('https://api.twitch.tv/helix/streams?user_id=' + urllib.parse.quote(broadcaster), _twitch_headers(c))
            return {'ok': True, 'live': bool(data.get('data')), 'message': 'Twitch conectado • ' + ('online' if data.get('data') else 'offline')}
        if p == 'kick':
            slug = str(c.get('channel_slug') or '').strip()
            if not slug:
                raise RuntimeError('Kick: informe Channel slug.')
            data = _request_json('https://api.kick.com/public/v1/channels?' + urllib.parse.urlencode({'slug': slug}), _kick_headers(c))
            rows = data.get('data') or []
            live = bool(rows and (rows[0].get('stream') or {}).get('is_live'))
            return {'ok': True, 'live': live, 'message': 'Kick conectado • ' + ('online' if live else 'offline')}
        if p == 'youtube':
            token = str(c.get('access_token') or '').strip()
            if token:
                data = _request_json('https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true', _youtube_headers(c))
                name = str(((data.get('items') or [{}])[0].get('snippet') or {}).get('title') or 'canal')
                return {'ok': True, 'live': False, 'message': 'YouTube OAuth conectado • ' + name}
            key = str(c.get('api_key') or '').strip()
            channel = str(c.get('channel_id') or '').strip()
            if not key or not channel:
                raise RuntimeError('YouTube: informe OAuth token ou API key + Channel ID.')
            q = urllib.parse.urlencode({'part': 'snippet', 'channelId': channel, 'eventType': 'live', 'type': 'video', 'key': key})
            data = _request_json('https://www.googleapis.com/youtube/v3/search?' + q)
            return {'ok': True, 'live': bool(data.get('items')), 'message': 'YouTube leitura conectada (sem permissão de escrita OAuth).'}
        if p == 'webhook':
            return {'ok': True, 'live': False, 'message': 'Webhook cadastrado. O endpoint será chamado apenas quando a agenda executar.'}
        return {'ok': False, 'message': 'Provider desconhecido.'}
    except Exception as e:
        return {'ok': False, 'message': str(e)}


def save_integration_v32(provider, name, config, enabled=True, iid=None):
    if provider not in PROVIDER_SPECS:
        raise ValueError('Provider inválido.')
    return save_integration(provider, name, config, enabled, iid)


def enabled_integrations():
    return [x for x in list_integrations(mask=True) if x.get('enabled')]
