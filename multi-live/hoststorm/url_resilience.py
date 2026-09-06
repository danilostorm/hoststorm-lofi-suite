from __future__ import annotations

import subprocess
import time
from types import MethodType

from . import url_sources


# Signed YouTube media URLs normally remain valid for a while, but resolving once per
# FFmpeg platform needlessly hits yt-dlp. Re-resolve after this window so supervisor
# recovery can obtain fresh signed URLs during long broadcasts.
RESOLVE_CACHE_SECONDS = 300


def _error_tail(proc) -> str:
    return str((getattr(proc, 'stderr', '') or getattr(proc, 'stdout', '') or '')[-1000:]).strip()


def _get_urls(url: str, selector: str, timeout=55) -> list[str]:
    cmd = url_sources._yt_base_args() + ['-f', selector, '--get-url', url]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(_error_tail(proc) or f'formato indisponível ({selector})')
    lines = [line.strip() for line in (proc.stdout or '').splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(f'yt-dlp não retornou URL para o seletor {selector}.')
    return lines


def resolve_remote_inputs(url: str, timeout=55) -> dict:
    """Resolve a remote source into one muxed input or separate video/audio inputs.

    YouTube increasingly exposes some videos without a progressive A/V format. The old
    HostStorm resolver requested only a single ``best`` stream and failed with
    "Requested format is not available". We prefer a <=1080p muxed stream when one is
    available, then fall back to yt-dlp's documented video-only + audio-only selection.
    """
    url = url_sources.validate_remote_url(url)
    if url_sources._is_direct_media_url(url):
        return {'inputs': [url], 'split_av': False, 'selector': 'direct'}

    errors: list[str] = []
    # First prefer a single A/V stream. The ? keeps formats whose height metadata is
    # unknown eligible instead of rejecting them outright.
    for selector in (
        'b[height<=?1080]/b',
        'best[height<=?1080][vcodec!=none][acodec!=none]/best[vcodec!=none][acodec!=none]',
    ):
        try:
            lines = _get_urls(url, selector, timeout)
            if lines:
                return {'inputs': [lines[0]], 'split_av': False, 'selector': selector}
        except Exception as exc:
            errors.append(str(exc))

    # Official yt-dlp style fallback: best video-only + best audio-only. --get-url
    # returns one URL per selected component; FFmpeg receives them as two inputs and
    # HostStorm maps video from input 0 and audio from input 1.
    for selector in (
        'bv[height<=?1080]+ba/bv+ba/b',
        'bv*+ba/b',
    ):
        try:
            lines = _get_urls(url, selector, timeout)
            if len(lines) >= 2:
                return {'inputs': lines[:2], 'split_av': True, 'selector': selector}
            if len(lines) == 1:
                return {'inputs': [lines[0]], 'split_av': False, 'selector': selector}
        except Exception as exc:
            errors.append(str(exc))

    detail = next((e for e in reversed(errors) if e), 'nenhum formato reproduzível foi encontrado')
    raise RuntimeError('Falha no yt-dlp ao resolver a fonte após os fallbacks: ' + detail)


def _replace_audio_map(cmd: list[str], remote_input_count: int) -> list[str]:
    """Fix FFmpeg audio mapping when yt-dlp returned separate video/audio URLs."""
    if remote_input_count < 2:
        return cmd
    result = list(cmd)
    total_inputs = sum(1 for item in result if item == '-i')
    # If another input exists after the remote pair, it is HostStorm's configured
    # external audio track and must keep priority over the source's own audio.
    audio_index = remote_input_count if total_inputs > remote_input_count else 1
    for idx, item in enumerate(result[:-1]):
        if item == '-map' and ':a' in str(result[idx + 1]):
            result[idx + 1] = f'{audio_index}:a:0'
            break
    return result


def install_url_resilience(manager, streaming_module):
    """Install resilient scheduled-URL resolution after the professional wrappers."""
    original_input = manager._input_args
    original_build = manager._build_cmd
    original_start_platform = manager._start_platform
    original_start = manager.start
    manager._hs_last_source_error = {}

    def input_args(self, session, vertical=False):
        remote = str(session.work_channel.get('_schedule_source_url') or '').strip()
        if session.trigger != 'scheduled' or not remote:
            return original_input(session, vertical)

        cache = session.work_channel.get('_hs_remote_inputs') or {}
        now = time.time()
        if (
            cache.get('url') != remote
            or not cache.get('inputs')
            or now - float(cache.get('resolved_at') or 0) > RESOLVE_CACHE_SECONDS
        ):
            resolved = resolve_remote_inputs(remote)
            cache = {
                'url': remote,
                'inputs': list(resolved['inputs']),
                'split_av': bool(resolved.get('split_av')),
                'selector': str(resolved.get('selector') or ''),
                'resolved_at': now,
            }
            session.work_channel['_hs_remote_inputs'] = cache
            try:
                self.log(
                    session.channel_id,
                    f"Fonte URL resolvida via yt-dlp: {cache['selector']} · "
                    f"{len(cache['inputs'])} input(s).",
                )
            except Exception:
                pass

        inputs = list(cache.get('inputs') or [])
        session._hs_remote_input_count = len(inputs)
        if len(inputs) >= 2:
            return ['-re', '-i', inputs[0], '-re', '-i', inputs[1]]
        if len(inputs) == 1:
            return ['-re', '-i', inputs[0]]
        raise RuntimeError('yt-dlp não retornou entradas reproduzíveis.')

    def build_cmd(self, session, slug):
        cmd = original_build(session, slug)
        remote_count = int(getattr(session, '_hs_remote_input_count', 0) or 0)
        return _replace_audio_map(cmd, remote_count)

    def start_platform(self, session, slug, recovery=False):
        try:
            return original_start_platform(session, slug, recovery)
        except Exception as exc:
            message = str(exc)
            self._hs_last_source_error[session.channel_id] = message
            ch = session.work_channel
            dest = (ch.get('destinations') or {}).get(slug) or {}
            label = str(dest.get('label') or slug)
            ps = session.platform_states.get(slug) or streaming_module.PlatformState(slug=slug, label=label)
            ps.last_error = message
            session.platform_states[slug] = ps
            try:
                streaming_module.upsert_platform_run(
                    session.run_id, slug, status='error', last_error=message, retries=ps.retries,
                )
            except Exception:
                pass
            try:
                self.log(session.channel_id, f'Falha preparando fonte para {label}: {message}')
            except Exception:
                pass
            return False

    def start(self, *args, **kwargs):
        cid = args[0] if args else kwargs.get('cid', '')
        try:
            ok, msg = original_start(*args, **kwargs)
        except Exception as exc:
            # Last safety net for manual "Executar agora": never let source resolution
            # become a Flask 500 page.
            message = str(exc)
            self._hs_last_source_error[cid] = message
            return False, 'Falha ao preparar fonte remota: ' + message
        if not ok:
            detail = self._hs_last_source_error.pop(cid, '')
            if detail and ('Nenhuma plataforma' in str(msg) or not msg):
                msg = 'Falha ao preparar fonte remota: ' + detail
        else:
            self._hs_last_source_error.pop(cid, None)
        return ok, msg

    manager._input_args = MethodType(input_args, manager)
    manager._build_cmd = MethodType(build_cmd, manager)
    manager._start_platform = MethodType(start_platform, manager)
    manager.start = MethodType(start, manager)
    return manager
