from datetime import datetime

import hoststorm.db as db
import hoststorm.distributed as distributed
import hoststorm.pro_db as pro_db
import hoststorm.push as push
import hoststorm.security as security
from hoststorm.broadcast import block_active, list_blocks, save_block
from hoststorm.overlay_pro import _advanced_graph
from hoststorm.professional import quality_label
from hoststorm.security import decrypt_secret, encrypt_secret, hash_password, new_api_token, verify_password


def setup_db(tmp_path):
    db.DB_PATH = tmp_path / 'hoststorm.db'
    db.LEGACY_CHANNELS_PATH = tmp_path / 'channels.json'
    pro_db.DB_PATH = db.DB_PATH
    db.init_db()
    pro_db.init_pro_db('admin', 'secret')


def test_secret_roundtrip(monkeypatch):
    monkeypatch.setenv('HOSTSTORM_SECRET_KEY', 'unit-test-key')
    enc = encrypt_secret('abc123')
    assert enc.startswith('enc:v1:')
    assert decrypt_secret(enc) == 'abc123'
    assert decrypt_secret('legacy-text') == 'legacy-text'


def test_password_hash():
    h = hash_password('senha-forte')
    assert verify_password(h, 'senha-forte')
    assert not verify_password(h, 'errada')


def test_persistent_security_key(tmp_path, monkeypatch):
    setup_db(tmp_path)
    monkeypatch.delenv('HOSTSTORM_SECRET_KEY', raising=False)
    monkeypatch.delenv('LV2_ADMIN_PASSWORD', raising=False)
    monkeypatch.setattr(security, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(security, 'DB_PATH', db.DB_PATH)
    monkeypatch.setattr(security, 'KEY_PATH', tmp_path / 'security.key')
    first = security._key_material()
    second = security._key_material()
    assert first == second
    assert (tmp_path / 'security.key').exists()


def test_pro_users_profiles_and_tokens(tmp_path):
    setup_db(tmp_path)
    admin = pro_db.get_user_by_username('admin')
    assert admin and admin['role'] == 'admin'
    assert len(pro_db.list_profiles()) >= 5
    token = new_api_token()
    pro_db.create_token(admin['id'], 'teste', token, ['read'])
    auth = pro_db.authenticate_token(token)
    assert auth and 'read' in auth['scopes']


def test_broadcast_block_and_antirepeat_schema(tmp_path):
    setup_db(tmp_path)
    cid = db.create_channel('TV')
    bid = save_block({
        'channel_id': cid,
        'name': 'Noite',
        'start_time': '18:00',
        'end_time': '23:00',
        'weekdays': [5],
        'media': ['a.mp4', 'b.mp4'],
        'enabled': True,
    })
    b = [x for x in list_blocks(cid) if x['id'] == bid][0]
    assert block_active(b, datetime(2026, 8, 29, 20, 0))
    assert not block_active(b, datetime(2026, 8, 30, 20, 0))


def test_quality_label():
    assert quality_label(60, 60, 6000, 6000, 1.0, 0) == 'excellent'
    assert quality_label(20, 60, 6000, 6000, 1.0, 0) == 'critical'
    assert quality_label(60, 60, 2000, 6000, 1.0, 0) == 'warning'


def test_advanced_overlay_graph(tmp_path):
    logo = tmp_path / 'logo.png'
    qr = tmp_path / 'qr.png'
    logo.write_bytes(b'x')
    qr.write_bytes(b'x')
    graph = _advanced_graph(
        'scale=1920:1080,format=yuv420p',
        logo,
        qr,
        {'current': 'Programa A', 'next': 'Programa B', 'next_time': '20:00'},
    )
    assert '[in]scale=1920:1080,format=yuv420p[hsbase]' in graph
    assert 'movie=' in graph
    assert 'AGORA\\: Programa A' in graph
    assert 'PRÓXIMO 20\\:00\\: Programa B' in graph
    assert graph.endswith('[out]')


def test_push_subscription_and_vapid_generation(tmp_path, monkeypatch):
    setup_db(tmp_path)
    monkeypatch.delenv('HOSTSTORM_VAPID_PUBLIC_KEY', raising=False)
    monkeypatch.delenv('HOSTSTORM_VAPID_PRIVATE_KEY', raising=False)
    push.VAPID_PRIVATE_PATH = tmp_path / 'vapid-private.pem'
    push.VAPID_PUBLIC_PATH = tmp_path / 'vapid-public.txt'
    push.init_push_db()
    public = push.public_key()
    assert public
    sid = push.save_subscription(
        'user1',
        {
            'endpoint': 'https://push.example/subscription',
            'keys': {'p256dh': 'abc', 'auth': 'def'},
        },
        'pytest',
    )
    assert sid
    assert len(push.list_subscriptions()) == 1
    push.delete_subscription('https://push.example/subscription')
    assert push.list_subscriptions() == []


def test_distributed_media_selection(tmp_path, monkeypatch):
    monkeypatch.setattr(distributed, 'VIDEOS_DIR', tmp_path)
    (tmp_path / 'main.mp4').write_bytes(b'a')
    (tmp_path / 'backup.mp4').write_bytes(b'b')
    ch = {
        'video': 'main.mp4',
        'fallback_video': 'backup.mp4',
        'maintenance_video': 'missing.mp4',
    }
    assert distributed._media_names(ch, ['main.mp4']) == ['main.mp4', 'backup.mp4']
