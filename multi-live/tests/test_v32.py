import json

import hoststorm.broadcast_automation as automation
import hoststorm.db as db
import hoststorm.integrations_v32 as integrations
import hoststorm.passkeys as passkeys
import hoststorm.pro_db as pro_db


def setup_db(tmp_path):
    db.DB_PATH = tmp_path / 'hoststorm.db'
    db.LEGACY_CHANNELS_PATH = tmp_path / 'channels.json'
    pro_db.DB_PATH = db.DB_PATH
    db.init_db()
    pro_db.init_pro_db('admin', 'secret')


def test_passkey_schema(tmp_path):
    setup_db(tmp_path)
    passkeys.init_passkey_db()
    with pro_db.connect() as con:
        names = {r['name'] for r in con.execute('PRAGMA table_info(passkeys)').fetchall()}
    assert {'credential_id', 'public_key', 'sign_count', 'user_id'} <= names


def test_schedule_metadata_schema(tmp_path):
    setup_db(tmp_path)
    automation.migrate_schedule_schema(db)
    with db.connect() as con:
        names = {r['name'] for r in con.execute('PRAGMA table_info(schedules)').fetchall()}
    assert {'metadata_enabled', 'metadata_integrations_json', 'broadcast_title', 'broadcast_category', 'broadcast_description'} <= names


def test_metadata_tokens():
    schedule = {'source_title': 'Street Fighter', 'media': [], 'id': 's1', 'channel_id': 'c1', 'time': '20:00'}
    channel = {'name': 'Portal Super Game'}
    value = automation._replace_tokens('AO VIVO | {source} | {channel}', channel, schedule)
    assert 'Street Fighter' in value
    assert 'Portal Super Game' in value


def test_twitch_update(monkeypatch):
    calls = []

    def fake(url, headers=None, method='GET', payload=None, timeout=15):
        calls.append((url, method, payload))
        if '/helix/users?' in url:
            return {'data': [{'id': '42'}]}
        if '/helix/search/categories?' in url:
            return {'data': [{'id': '509658', 'name': 'Just Chatting'}]}
        return {}

    monkeypatch.setattr(integrations, '_request_json', fake)
    result = integrations._twitch_update(
        {'client_id': 'client', 'access_token': 'token', 'channel_login': 'teste'},
        {'title': 'Minha live', 'category': 'Just Chatting'},
    )
    assert result['ok']
    patch = [x for x in calls if x[1] == 'PATCH'][0]
    assert patch[2]['title'] == 'Minha live'
    assert patch[2]['game_id'] == '509658'


def test_kick_update(monkeypatch):
    calls = []

    def fake(url, headers=None, method='GET', payload=None, timeout=15):
        calls.append((url, method, payload))
        if '/categories?' in url:
            return {'data': [{'id': 12, 'name': 'Just Chatting'}]}
        return {}

    monkeypatch.setattr(integrations, '_request_json', fake)
    result = integrations._kick_update(
        {'access_token': 'token', 'channel_slug': 'teste'},
        {'title': 'Kick live', 'category': 'Just Chatting'},
    )
    assert result['ok']
    patch = [x for x in calls if x[1] == 'PATCH'][0]
    assert patch[2] == {'stream_title': 'Kick live', 'category_id': 12}


def test_youtube_update(monkeypatch):
    calls = []

    def fake(url, headers=None, method='GET', payload=None, timeout=15):
        calls.append((url, method, payload))
        if 'liveBroadcasts?' in url and 'broadcastStatus=active' in url:
            return {'items': [{'id': 'yt123', 'snippet': {'scheduledStartTime': '2026-08-31T20:00:00Z'}}]}
        if 'liveBroadcasts?' in url and 'id=yt123' in url:
            return {'items': [{'id': 'yt123', 'snippet': {'title': 'Antes', 'description': 'Antes', 'categoryId': '20', 'scheduledStartTime': '2026-08-31T20:00:00Z'}}]}
        if 'videoCategories?' in url:
            return {'items': [{'id': '20', 'snippet': {'title': 'Gaming'}}]}
        return {}

    monkeypatch.setattr(integrations, '_request_json', fake)
    result = integrations._youtube_update(
        {'access_token': 'token'},
        {'title': 'Nova live', 'category': 'Gaming', 'description': 'Descrição'},
    )
    assert result['ok']
    put = [x for x in calls if x[1] == 'PUT'][0]
    assert put[2]['snippet']['title'] == 'Nova live'
    assert put[2]['snippet']['categoryId'] == '20'
    assert put[2]['snippet']['scheduledStartTime'] == '2026-08-31T20:00:00Z'
