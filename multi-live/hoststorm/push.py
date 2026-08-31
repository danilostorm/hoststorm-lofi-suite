from __future__ import annotations

import base64
import hashlib
import json
import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from .config import DATA_DIR
from .pro_db import connect
from .utils import now_iso

VAPID_PRIVATE_PATH = DATA_DIR / 'vapid-private.pem'
VAPID_PUBLIC_PATH = DATA_DIR / 'vapid-public.txt'


def init_push_db():
    with connect() as con:
        con.execute(
            '''
            CREATE TABLE IF NOT EXISTS push_subscriptions (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL DEFAULT '',
              endpoint TEXT NOT NULL UNIQUE,
              p256dh TEXT NOT NULL,
              auth TEXT NOT NULL,
              user_agent TEXT NOT NULL DEFAULT '',
              enabled INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            '''
        )
        con.execute('CREATE INDEX IF NOT EXISTS idx_push_enabled ON push_subscriptions(enabled)')


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def ensure_vapid_keys():
    env_public = os.environ.get('HOSTSTORM_VAPID_PUBLIC_KEY', '').strip()
    env_private = os.environ.get('HOSTSTORM_VAPID_PRIVATE_KEY', '').strip()
    if env_public and env_private:
        return env_public, env_private

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not VAPID_PRIVATE_PATH.exists() or not VAPID_PUBLIC_PATH.exists():
        private_key = ec.generate_private_key(ec.SECP256R1())
        private_pem = private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        pub = private_key.public_key().public_numbers()
        public_raw = b'\x04' + pub.x.to_bytes(32, 'big') + pub.y.to_bytes(32, 'big')
        tmp_private = VAPID_PRIVATE_PATH.with_suffix('.tmp')
        tmp_public = VAPID_PUBLIC_PATH.with_suffix('.tmp')
        tmp_private.write_bytes(private_pem)
        try:
            os.chmod(tmp_private, 0o600)
        except OSError:
            pass
        tmp_public.write_text(_b64url(public_raw), encoding='utf-8')
        os.replace(tmp_private, VAPID_PRIVATE_PATH)
        os.replace(tmp_public, VAPID_PUBLIC_PATH)

    return VAPID_PUBLIC_PATH.read_text(encoding='utf-8').strip(), str(VAPID_PRIVATE_PATH)


def public_key() -> str:
    return ensure_vapid_keys()[0]


def save_subscription(user_id: str, payload: dict, user_agent: str = ''):
    endpoint = str((payload or {}).get('endpoint') or '').strip()
    keys = (payload or {}).get('keys') or {}
    p256dh = str(keys.get('p256dh') or '').strip()
    auth = str(keys.get('auth') or '').strip()
    if not endpoint or not p256dh or not auth:
        raise ValueError('Assinatura Web Push incompleta.')
    sid = hashlib.sha256(endpoint.encode('utf-8')).hexdigest()[:24]
    ts = now_iso()
    with connect() as con:
        con.execute(
            '''
            INSERT INTO push_subscriptions(id,user_id,endpoint,p256dh,auth,user_agent,enabled,created_at,updated_at)
            VALUES(?,?,?,?,?,?,1,?,?)
            ON CONFLICT(endpoint) DO UPDATE SET
              user_id=excluded.user_id,
              p256dh=excluded.p256dh,
              auth=excluded.auth,
              user_agent=excluded.user_agent,
              enabled=1,
              updated_at=excluded.updated_at
            ''',
            (sid, str(user_id or ''), endpoint, p256dh, auth, str(user_agent or '')[:500], ts, ts),
        )
    return sid


def delete_subscription(endpoint: str):
    endpoint = str(endpoint or '').strip()
    if not endpoint:
        return
    with connect() as con:
        con.execute('DELETE FROM push_subscriptions WHERE endpoint=?', (endpoint,))


def list_subscriptions():
    with connect() as con:
        return [dict(r) for r in con.execute(
            'SELECT * FROM push_subscriptions WHERE enabled=1 ORDER BY updated_at DESC'
        ).fetchall()]


def send_push(message: str, title: str = 'HostStorm', url: str = '/professional/alerts') -> int:
    subscriptions = list_subscriptions()
    if not subscriptions:
        return 0
    try:
        from pywebpush import WebPushException, webpush
    except Exception:
        return 0

    _, private_key = ensure_vapid_keys()
    claims = {'sub': os.environ.get('HOSTSTORM_VAPID_SUBJECT', 'mailto:hoststorm@localhost')}
    payload = json.dumps({'title': title, 'body': str(message), 'url': url}, ensure_ascii=False)
    sent = 0
    stale = []
    for row in subscriptions:
        info = {
            'endpoint': row['endpoint'],
            'keys': {'p256dh': row['p256dh'], 'auth': row['auth']},
        }
        try:
            webpush(
                subscription_info=info,
                data=payload,
                vapid_private_key=private_key,
                vapid_claims=claims,
                ttl=120,
            )
            sent += 1
        except WebPushException as exc:
            status = getattr(getattr(exc, 'response', None), 'status_code', None)
            if status in {404, 410}:
                stale.append(row['endpoint'])
        except Exception:
            pass
    for endpoint in stale:
        delete_subscription(endpoint)
    return sent
