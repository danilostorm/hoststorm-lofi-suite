from __future__ import annotations

from pathlib import Path
from types import MethodType

from .broadcast import list_blocks, block_active
from .config import MEDIA_DIR, TMP_DIR
from .utils import now_dt

ASSETS_DIR = MEDIA_DIR / 'assets'
ASSETS_DIR.mkdir(parents=True, exist_ok=True)


def _esc_movie(path: Path) -> str:
    # FFmpeg filtergraph path escaping.
    return str(path).replace('\\', '\\\\').replace(':', '\\:').replace("'", "\\'")


def _esc_text(value: str) -> str:
    return str(value or '').replace('\\', '\\\\').replace(':', '\\:').replace("'", "\\'").replace('%', '\\%')


def _program_context(channel_id: str):
    now = now_dt()
    blocks = [b for b in list_blocks(channel_id) if b.get('enabled') and now.weekday() in b.get('weekdays', [])]
    blocks.sort(key=lambda b: b.get('start_time', ''))
    current = next((b for b in blocks if block_active(b, now)), None)
    upcoming = [b for b in blocks if b.get('start_time', '') > now.strftime('%H:%M')]
    nxt = upcoming[0] if upcoming else None
    return {
        'current': (current or {}).get('name', ''),
        'next': (nxt or {}).get('name', ''),
        'next_time': (nxt or {}).get('start_time', ''),
    }


def _ensure_qr(channel_id: str, text: str):
    text = str(text or '').strip()
    if not text:
        return None
    try:
        import qrcode
        target = TMP_DIR / f'overlay-qr-{channel_id}.png'
        qrcode.make(text).save(target)
        return target
    except Exception:
        return None


def install_advanced_overlays(manager):
    original_build = manager._build_cmd

    def build_cmd(self, session, slug):
        cmd = original_build(session, slug)
        ch = session.work_channel
        if str(ch.get('overlay_enabled', '0')).lower() not in {'1', 'true', 'on', 'yes'}:
            return cmd
        try:
            vf_index = cmd.index('-vf')
        except ValueError:
            return cmd

        graph = cmd[vf_index + 1]
        channel_id = session.channel_id

        logo_name = Path(str(ch.get('overlay_logo') or '')).name
        logo_path = ASSETS_DIR / logo_name if logo_name else None
        if logo_path and logo_path.exists():
            logo = _esc_movie(logo_path)
            graph = f"{graph},movie='{logo}',scale='min(220,iw)':-1[hslogo];[in][hslogo]overlay=W-w-24:24"

        qr_path = _ensure_qr(channel_id, ch.get('overlay_qr_text', ''))
        if qr_path and qr_path.exists():
            qr = _esc_movie(qr_path)
            graph = f"{graph},movie='{qr}',scale=150:150[hsqr];[in][hsqr]overlay=W-w-24:H-h-24"

        if str(ch.get('overlay_program_info', '0')).lower() in {'1', 'true', 'on', 'yes'}:
            ctx = _program_context(channel_id)
            if ctx['current']:
                current = _esc_text('AGORA: ' + ctx['current'])
                graph += f",drawtext=text='{current}':x=24:y=24:fontsize=25:fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=10"
            if ctx['next']:
                nxt = _esc_text(f"PRÓXIMO {ctx['next_time']}: {ctx['next']}")
                graph += f",drawtext=text='{nxt}':x=24:y=h-text_h-24:fontsize=22:fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=9"

        cmd[vf_index + 1] = graph
        return cmd

    manager._build_cmd = MethodType(build_cmd, manager)
    return manager
