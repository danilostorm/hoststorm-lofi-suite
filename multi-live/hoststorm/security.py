from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import struct
import time
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken
from werkzeug.security import check_password_hash, generate_password_hash

PBKDF2_ROUNDS = 390000


def _key_material() -> bytes:
    raw = os.environ.get('HOSTSTORM_SECRET_KEY') or os.environ.get('LV2_ADMIN_PASSWORD') or 'hoststorm-dev-key'
    return hashlib.sha256(raw.encode('utf-8')).digest()


def fernet() -> Fernet:
    return Fernet(base64.urlsafe_b64encode(_key_material()))


def encrypt_secret(value: str) -> str:
    if not value:
        return ''
    return 'enc:v1:' + fernet().encrypt(value.encode('utf-8')).decode('ascii')


def decrypt_secret(value: str) -> str:
    if not value:
        return ''
    if not value.startswith('enc:v1:'):
        return value
    try:
        return fernet().decrypt(value[7:].encode('ascii')).decode('utf-8')
    except (InvalidToken, ValueError):
        return ''


def hash_password(password: str) -> str:
    return generate_password_hash(password, method='pbkdf2:sha256', salt_length=16)


def verify_password(password_hash: str, password: str) -> bool:
    return bool(password_hash and check_password_hash(password_hash, password))


def new_api_token() -> str:
    return 'hst_' + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode('ascii').rstrip('=')


def _totp(secret: str, counter: int, digits: int = 6) -> str:
    padded = secret.upper() + '=' * ((8 - len(secret) % 8) % 8)
    key = base64.b32decode(padded)
    msg = struct.pack('>Q', counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack('>I', digest[offset:offset+4])[0] & 0x7fffffff) % (10 ** digits)
    return str(code).zfill(digits)


def verify_totp(secret: str, code: str, window: int = 1, step: int = 30) -> bool:
    code = ''.join(ch for ch in str(code or '') if ch.isdigit())
    if len(code) != 6 or not secret:
        return False
    counter = int(time.time() // step)
    return any(hmac.compare_digest(_totp(secret, counter + delta), code) for delta in range(-window, window + 1))


def totp_uri(secret: str, username: str, issuer: str = 'HostStorm') -> str:
    from urllib.parse import quote
    return f'otpauth://totp/{quote(issuer)}:{quote(username)}?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30'


ROLE_RANK = {'viewer': 10, 'operator': 20, 'admin': 30}


def role_allows(role: str, minimum: str) -> bool:
    return ROLE_RANK.get(role or '', 0) >= ROLE_RANK.get(minimum or '', 999)


@dataclass
class RateWindow:
    count: int = 0
    first_at: float = 0.0
    blocked_until: float = 0.0


class LoginLimiter:
    def __init__(self, max_attempts=8, window_seconds=900, block_seconds=900):
        self.max_attempts=max_attempts; self.window_seconds=window_seconds; self.block_seconds=block_seconds
        self.state: dict[str, RateWindow] = {}

    def allowed(self, key: str) -> bool:
        now=time.time(); w=self.state.get(key)
        return not w or w.blocked_until <= now

    def fail(self, key: str):
        now=time.time(); w=self.state.get(key) or RateWindow()
        if not w.first_at or now-w.first_at > self.window_seconds:
            w=RateWindow(count=0, first_at=now)
        w.count += 1
        if w.count >= self.max_attempts:
            w.blocked_until=now+self.block_seconds
        self.state[key]=w

    def success(self, key: str):
        self.state.pop(key, None)

LOGIN_LIMITER=LoginLimiter()
