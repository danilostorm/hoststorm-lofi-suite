from __future__ import annotations

import json
import subprocess
from types import MethodType

from . import url_sources


def _session_source_url(session, vertical=False) -> str:
    ch = session.work_channel or {}
    scheduled = str(ch.get('_schedule_source_url') or '').strip()
    if session.trigger == 'scheduled' and scheduled:
        return scheduled
    source_mode = str(
        ch.get('shorts_source_mode')
        if vertical and ch.get('shorts_source_mode') not in (None, 'same')
        else ch.get('source_mode', 'local')
    )
    if source_mode != 'url':
        return ''
    if vertical and str(ch.get('shorts_source_mode') or '') == 'url':
        return str(ch.get('shorts_source_url') or '').strip()
    return str(ch.get('source_url') or '').strip()


def _probe_is_live(url: str, timeout=35) -> bool:
    """Return True only when yt-dlp explicitly identifies a currently live source."""
    if not url or url_sources._is_direct_media_url(url):
        return False
    cmd = url_sources._yt_base_args() + ['--skip-download', '--dump-single-json', url]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0 or not (proc.stdout or '').strip():
        return False
    try:
        info = json.loads(proc.stdout)
    except Exception:
        return False
    live_status = str(info.get('live_status') or '').lower()
    return bool(info.get('is_live')) or live_status in {'is_live', 'live'}


def _strip_seek(args: list[str]) -> list[str]:
    result = []
    i = 0
    while i < len(args):
        if args[i] == '-ss' and i + 1 < len(args):
            i += 2
            continue
        result.append(args[i])
        i += 1
    return result


def install_live_url_guard(manager, streaming_module):
    """Keep recovery semantics correct for VOD and true live sources.

    VOD URLs resume with -ss from the persisted checkpoint. A YouTube/Twitch-style live
    source must instead reconnect at its current live edge; seeking by accumulated wall
    time can make FFmpeg reach the edge/EOF repeatedly and trigger a recovery loop.
    """
    original_start = manager.start
    original_input = manager._input_args
    manager._hs_live_url_flags = {}

    def start(self, cid, platforms=None, media=None, trigger='manual', schedule=None):
        source_url = ''
        if trigger == 'scheduled' and schedule and str(schedule.get('source_mode') or '') == 'url':
            source_url = str(schedule.get('source_url') or '').strip()
        else:
            try:
                ch = streaming_module.get_channel(cid) or {}
                if str(ch.get('source_mode') or '') == 'url':
                    source_url = str(ch.get('source_url') or '').strip()
            except Exception:
                source_url = ''
        if source_url:
            try:
                self._hs_live_url_flags[source_url] = _probe_is_live(source_url)
            except Exception:
                # If probing itself fails, the normal resolver will surface the real
                # source error. Do not misclassify the source as live.
                self._hs_live_url_flags[source_url] = False
        return original_start(cid, platforms, media, trigger, schedule)

    def input_args(self, session, vertical=False):
        args = list(original_input(session, vertical))
        source_url = _session_source_url(session, vertical)
        if source_url and self._hs_live_url_flags.get(source_url) is True:
            session.work_channel['_hs_source_is_live'] = True
            session.work_channel['_hs_source_quality'] = (
                session.work_channel.get('_hs_source_quality') or 'live edge / máxima automática'
            )
            return _strip_seek(args)
        return args

    manager.start = MethodType(start, manager)
    manager._input_args = MethodType(input_args, manager)
    return manager
