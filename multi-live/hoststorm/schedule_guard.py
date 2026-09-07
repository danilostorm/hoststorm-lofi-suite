from __future__ import annotations

from types import MethodType

from .utils import build_target


def install_schedule_platform_guard(manager, streaming_module):
    """Do not let one stale/unconfigured destination cancel an entire schedule.

    Schedules persist the platform slugs selected at save time. A destination can later
    be disabled, cleared or inherited accidentally while editing another channel. Before
    a scheduled start, keep only destinations that still have a complete RTMP target.
    If at least one valid destination remains the live starts there and the invalid ones
    are reported in the audit instead of aborting every platform.
    """
    original_start = manager.start

    def start(self, cid, platforms=None, media=None, trigger='manual', schedule=None):
        if trigger != 'scheduled':
            return original_start(cid, platforms, media, trigger, schedule)

        ch = streaming_module.get_channel(cid)
        if not ch:
            return original_start(cid, platforms, media, trigger, schedule)

        requested = list(platforms or (schedule or {}).get('platforms') or [])
        valid = []
        ignored = []
        for slug in requested:
            dest = (ch.get('destinations') or {}).get(slug)
            if not dest:
                ignored.append(str(slug))
                continue
            if build_target(dest.get('rtmp_url'), dest.get('stream_key')):
                valid.append(slug)
            else:
                ignored.append(str(dest.get('label') or slug))

        if not valid:
            labels = ', '.join(ignored) if ignored else 'nenhuma plataforma configurada'
            return False, 'Nenhuma plataforma da agenda possui RTMP/chave válida. Verifique: ' + labels

        if ignored:
            try:
                streaming_module.audit(
                    'warning', 'schedule_platform_ignored', cid,
                    'Destino(s) sem RTMP/chave ignorado(s): ' + ', '.join(ignored),
                    {'schedule_id': (schedule or {}).get('id', ''), 'ignored': ignored, 'valid': valid},
                )
            except Exception:
                pass

        ok, msg = original_start(cid, valid, media, trigger, schedule)
        if ignored:
            suffix = ' | ignorado(s) sem RTMP/chave: ' + ', '.join(ignored)
            msg = str(msg or '') + suffix
        return ok, msg

    manager.start = MethodType(start, manager)
    return manager
