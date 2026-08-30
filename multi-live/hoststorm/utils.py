from __future__ import annotations

from datetime import datetime
from pathlib import Path
from .config import BR_TZ
import hashlib
import os
import re


def now_dt() -> datetime:
    return datetime.now(BR_TZ)


def now_iso() -> str:
    return now_dt().isoformat()


def now_br() -> str:
    return now_dt().strftime('%d/%m/%Y %H:%M:%S')


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=BR_TZ)
        return dt.astimezone(BR_TZ)
    except Exception:
        return None


def datetime_br(value: str | None) -> str:
    dt = parse_iso(value)
    if not dt:
        return str(value or '-')
    return dt.strftime('%d/%m/%Y às %H:%M:%S')


def date_br(value: str | None) -> str:
    dt = parse_iso(value)
    if not dt:
        return str(value or '-')
    return dt.strftime('%d/%m/%Y')


def duration_hms(seconds) -> str:
    try:
        total = max(0, int(float(seconds or 0)))
    except Exception:
        total = 0
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f'{h:02d}:{m:02d}:{s:02d}'


def normalize_time(value: str | None) -> str:
    raw = str(value or '').strip()
    for fmt in ('%H:%M', '%H:%M:%S'):
        try:
            return datetime.strptime(raw, fmt).strftime('%H:%M')
        except ValueError:
            pass
    return ''


def safe_filename(value: str | None) -> str:
    return os.path.basename(str(value or '').strip())


def build_target(rtmp_url: str | None, stream_key: str | None) -> str:
    url = str(rtmp_url or '').strip()
    key = str(stream_key or '').strip()
    if not url:
        return ''
    if key and not url.endswith(key):
        return f'{url.rstrip("/")}/{key}'
    return url


def parse_bitrate_k(value: str | None, default=4500) -> int:
    m = re.search(r'(\d+)', str(value or ''))
    return int(m.group(1)) if m else default


def sha256_file(path: Path, chunk_size=1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()
