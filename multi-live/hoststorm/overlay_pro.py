from __future__ import annotations

from pathlib import Path
from types import MethodType

from .broadcast import list_blocks, block_active
from .config import MEDIA_DIR, TMP_DIR
from .utils import now_dt

ASSETS_DIR = MEDIA_DIR / 'assets'
ASSETS_DIR.mkdir(parents=True, exist_ok=True)


def _esc_movie(path: Path) -> str:
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


def _advanced_graph(base_graph: str, logo_path: Path | None, qr_path: Path | None, program: dict):
    # Build a single simple filtergraph preserving the original scaling/padding chain.
    # [in]/[out] are the documented implicit pads for -vf graphs.
    parts = [f'[in]{base_graph}[hsbase]']
    current_label = 'hsbase'
    counter = 0

    if logo_path and logo_path.exists():
        logo = _esc_movie(logo_path)
        parts.append(f"movie='{logo}',scale='min(220,iw)':-1[hslogo]")
        parts.append(f'[{current_label}][hslogo]overlay=W-w-24:24[hsv{counter}]')
        current_label = f'hsv{counter}'
        counter += 1

    if qr_path and qr_path.exists():
        qr = _esc_movie(qr_path)
        parts.append(f"movie='{qr}',scale=150:150[hsqr]")
        parts.append(f'[{current_label}][hsqr]overlay=W-w-24:H-h-24[hsv{counter}]')
        current_label = f'hsv{counter}'
        counter += 1

    text_filters = []
    if program.get('current'):
        current = _esc_text('AGORA: ' + program['current'])
        text_filters.append(f"drawtext=text='{current}':x=24:y=24:fontsize=25:fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=10")
    if program.get('next'):
        nxt = _esc_text(f"PRÓXIMO {program.get('next_time', '')}: {program['next']}")
        text_filters.append(f"drawtext=text='{nxt}':x=24:y=h-text_h-24:fontsize=22:fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=9")

    if text_filters:
        parts.append(f'[{current_label}]' + ','.join(text_filters) + '[out]')
    else:
        parts.append(f'[{current_label}]null[out]')
    return ';'.join(parts)


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

        logo_name = Path(str(ch.get('overlay_logo') or '')).name
        logo_path = ASSETS_DIR / logo_name if logo_name else None
        qr_path = _ensure_qr(session.channel_id, ch.get('overlay_qr_text', ''))
        program = _program_context(session.channel_id) if str(ch.get('overlay_program_info', '0')).lower() in {'1', 'true', 'on', 'yes'} else {}

        if (logo_path and logo_path.exists()) or qr_path or program:
            cmd[vf_index + 1] = _advanced_graph(cmd[vf_index + 1], logo_path, qr_path, program)
        return cmd

    manager._build_cmd = MethodType(build_cmd, manager)
    return manager
