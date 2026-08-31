import base64
import hashlib
import urllib.parse

import hoststorm.integrations as legacy_integrations
import hoststorm.kick_integration_v33 as kick_v33
import hoststorm.kick_oauth as oauth


def test_kick_pkce_authorize_url():
    verifier, challenge = oauth.new_pkce()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).decode('ascii').rstrip('=')
    assert challenge == expected
    url = oauth.authorize_url('client123', 'https://host.example/api/kick/oauth/callback', 'state123', challenge)
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    assert parsed.netloc == 'id.kick.com'
    assert query['response_type'] == ['code']
    assert query['code_challenge_method'] == ['S256']
    assert query['redirect_uri'] == ['https://host.example/api/kick/oauth/callback']
    assert set(query['scope'][0].split()) == {'user:read', 'channel:read', 'channel:write'}


def test_normalize_kick_token_payload(monkeypatch):
    monkeypatch.setattr(oauth.time, 'time', lambda: 1000)
    data = oauth.normalize_token_payload({
        'access_token': 'access',
        'refresh_token': 'refresh-new',
        'expires_in': 3600,
        'scope': 'user:read channel:read channel:write',
    })
    assert data['access_token'] == 'access'
    assert data['refresh_token'] == 'refresh-new'
    assert data['token_expires_at'] == str(1000 + 3600 - 30)


def test_kick_refresh_is_persisted(monkeypatch):
    item = {
        'id': 'kick1', 'provider': 'kick', 'name': 'Canal', 'enabled': 1,
        'config': {
            'client_id': 'cid', 'client_secret': 'secret', 'access_token': 'old',
            'refresh_token': 'refresh', 'token_expires_at': '100',
        },
    }
    saved = {}
    monkeypatch.setattr(kick_v33.time, 'time', lambda: 1000)
    monkeypatch.setattr(kick_v33, 'get_integration', lambda iid: item if not saved else {**item, 'config': saved['config']})
    monkeypatch.setattr(kick_v33, 'refresh_tokens', lambda *args: {'access_token': 'new', 'refresh_token': 'newrefresh', 'expires_in': 3600, 'scope': 'channel:write'})
    monkeypatch.setattr(kick_v33, 'normalize_token_payload', lambda payload, previous_refresh='': {'access_token': 'new', 'refresh_token': 'newrefresh', 'token_expires_at': '5000', 'token_scope': 'channel:write'})
    monkeypatch.setattr(kick_v33, 'save_integration_v32', lambda provider, name, config, enabled, iid: saved.update({'config': dict(config), 'iid': iid}))
    result = kick_v33.ensure_kick_token('kick1')
    assert saved['iid'] == 'kick1'
    assert saved['config']['access_token'] == 'new'
    assert result['config']['refresh_token'] == 'newrefresh'


def test_legacy_check_route_uses_v33(monkeypatch):
    monkeypatch.setattr(kick_v33, 'check_integration_v33', lambda iid: {'ok': True, 'message': 'v33:' + iid})
    assert legacy_integrations.check_integration('abc')['message'] == 'v33:abc'
