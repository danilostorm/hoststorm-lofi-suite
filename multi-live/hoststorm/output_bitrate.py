from __future__ import annotations

from types import MethodType

from . import multi_output
from .utils import parse_bitrate_k


BITRATE_MODES = {'auto', 'inherit', 'custom'}


def _kind(slug: str, destination: dict | None = None) -> str:
    return multi_output.platform_kind(slug, destination or {})


def _fps(channel: dict) -> int:
    try:
        return 60 if float(channel.get('fps') or 30) >= 50 else 30
    except Exception:
        return 30


def _resolution(channel: dict, slug: str, destination: dict | None = None) -> str:
    kind = _kind(slug, destination)
    if kind == 'youtube_shorts' or (kind == 'kwai' and str((destination or {}).get('mode') or '') == 'vertical'):
        # Current HostStorm vertical pipeline is 1080x1920.
        return '1080x1920'
    return str(channel.get('resolution') or '1920x1080')


def youtube_recommended_k(channel: dict, slug: str, destination: dict | None = None) -> int:
    resolution = _resolution(channel, slug, destination)
    try:
        width, height = [int(x) for x in resolution.lower().split('x', 1)]
    except Exception:
        width, height = 1920, 1080
    short_side = min(width, height)
    long_side = max(width, height)
    fps = _fps(channel)
    # H.264 live recommendations from YouTube Help (Sep/2026):
    # 720p30 4 Mbps, 720p60 6 Mbps, 1080p30 10 Mbps, 1080p60 12 Mbps,
    # 1440p30 15 Mbps, 1440p60 24 Mbps, 2160p30 30 Mbps, 2160p60 35 Mbps.
    if short_side <= 720 and long_side <= 1280:
        return 6000 if fps == 60 else 4000
    if short_side <= 1080 and long_side <= 1920:
        return 12000 if fps == 60 else 10000
    if short_side <= 1440 and long_side <= 2560:
        return 24000 if fps == 60 else 15000
    return 35000 if fps == 60 else 30000


def inherited_k(channel: dict, slug: str, destination: dict | None = None) -> int:
    kind = _kind(slug, destination)
    vertical = kind == 'youtube_shorts' or (kind == 'kwai' and str((destination or {}).get('mode') or '') == 'vertical')
    key = 'shorts_video_bitrate' if vertical else 'video_bitrate'
    return parse_bitrate_k(channel.get(key), 3500 if vertical else 4500)


def bitrate_mode(destination: dict | None) -> str:
    destination = destination or {}
    raw = str(destination.get('output_bitrate_mode') or '').strip().lower()
    if raw in BITRATE_MODES:
        return raw
    # Existing YouTube outputs opt into the safer automatic live recommendation.
    # Other platforms preserve the old channel-level behaviour.
    return 'auto' if _kind('', destination) in {'youtube', 'youtube_shorts'} else 'inherit'


def target_bitrate_k(channel: dict, slug: str, destination: dict | None = None) -> int:
    destination = destination or {}
    mode = bitrate_mode(destination)
    if mode == 'inherit':
        return inherited_k(channel, slug, destination)
    if mode == 'custom':
        try:
            custom = int(float(destination.get('output_video_bitrate_k') or 0))
        except Exception:
            custom = 0
        if custom > 0:
            return max(500, min(50000, custom))
        return inherited_k(channel, slug, destination)
    if _kind(slug, destination) in {'youtube', 'youtube_shorts'}:
        return youtube_recommended_k(channel, slug, destination)
    return inherited_k(channel, slug, destination)


def _replace_arg(cmd: list[str], flag: str, value: str):
    if flag in cmd:
        idx = cmd.index(flag)
        if idx + 1 < len(cmd):
            cmd[idx + 1] = value
            return
    try:
        insert_at = cmd.index('-f')
    except ValueError:
        insert_at = max(0, len(cmd) - 1)
    cmd[insert_at:insert_at] = [flag, value]


def enforce_cbr(cmd: list[str], target_k: int) -> list[str]:
    """Make RTMP bitrate stable instead of allowing x264 ABR to fall far below target."""
    cmd = list(cmd)
    rate = f'{int(target_k)}k'
    _replace_arg(cmd, '-b:v', rate)
    _replace_arg(cmd, '-minrate', rate)
    _replace_arg(cmd, '-maxrate', rate)
    _replace_arg(cmd, '-bufsize', f'{int(target_k) * 2}k')

    encoder = ''
    if '-c:v' in cmd:
        idx = cmd.index('-c:v')
        if idx + 1 < len(cmd):
            encoder = str(cmd[idx + 1])
    if encoder == 'libx264':
        params = 'nal-hrd=cbr:force-cfr=1'
        if '-x264-params' in cmd:
            idx = cmd.index('-x264-params')
            current = str(cmd[idx + 1] or '') if idx + 1 < len(cmd) else ''
            if 'nal-hrd=' not in current:
                cmd[idx + 1] = (current + ':' + params).strip(':')
        else:
            try:
                insert_at = cmd.index('-f')
            except ValueError:
                insert_at = max(0, len(cmd) - 1)
            cmd[insert_at:insert_at] = ['-x264-params', params]
    elif encoder == 'h264_nvenc':
        _replace_arg(cmd, '-rc', 'cbr')
    return cmd


def install_output_bitrate(manager):
    original_build = manager._build_cmd

    def build_cmd(self, session, slug):
        cmd = original_build(session, slug)
        destination = ((session.work_channel or {}).get('destinations') or {}).get(slug) or {}
        target = target_bitrate_k(session.work_channel or {}, slug, destination)
        kind = _kind(slug, destination)
        # RTMP live platforms expect a stable bitrate; YouTube explicitly recommends CBR.
        if kind in {'youtube', 'youtube_shorts', 'kick', 'twitch', 'kwai', 'custom'}:
            cmd = enforce_cbr(cmd, target)
        return cmd

    manager._build_cmd = MethodType(build_cmd, manager)
    return manager
