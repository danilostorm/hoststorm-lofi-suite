from __future__ import annotations

import os
from types import MethodType

from . import multi_output
from . import cluster_v5
from .output_sources import source_mode


def install_cluster_runtime_guard(web_module, streaming_module):
    """Final controller-side cluster guard.

    The legacy distributed wrapper predates per-output placement and works at channel level.
    This guard guarantees an output explicitly pinned to Local cannot be re-dispatched by
    that legacy channel setting, and makes the channel-level Stop button stop cluster runs too.
    Agents skip this layer because their snapshots are always forced to local execution.
    """
    manager = streaming_module.MANAGER
    if os.environ.get('HOSTSTORM_AGENT_MODE') == '1':
        return manager

    cluster_start = multi_output._start_output
    manager_stop = manager.stop

    def start_output(manager_obj, cid: str, slug: str):
        channel = web_module.get_channel(cid) if web_module else None
        if not channel:
            return False, 'Canal não encontrado.'
        destination = (channel.get('destinations') or {}).get(slug)
        if not destination:
            return False, 'Destino não encontrado.'
        mode, _ = cluster_v5.output_placement(channel, slug, destination)
        if mode != 'local':
            return cluster_start(manager_obj, cid, slug)

        assignment = cluster_v5._assignment(cid, slug)
        if assignment:
            cluster_v5._stop_remote(assignment, 'saída fixada no controlador local')

        with manager_obj.lock:
            session = manager_obj.sessions.get(cid)
        if session and not session.stop_requested and session.desired_running:
            return cluster_v5._ORIGINAL_START(manager_obj, cid, slug)

        if source_mode(destination) != 'channel':
            return cluster_v5._ORIGINAL_START(manager_obj, cid, slug)

        return streaming_module.StreamManager.start(
            manager_obj, cid, platforms=[slug], media=None, trigger='manual', schedule=None,
        )

    def stop(self, cid, reason='manual'):
        remote = list(cluster_v5._assignments(cid, desired_only=True))
        stopped_remote = 0
        errors = []
        for assignment in remote:
            ok, message = cluster_v5._stop_remote(assignment, reason)
            if ok:
                stopped_remote += 1
            else:
                errors.append(message)
        ok_local, message_local = manager_stop(cid, reason)
        if errors and not ok_local:
            return False, '; '.join(errors + [message_local])
        if stopped_remote:
            suffix = f' + {stopped_remote} saída(s) remota(s)'
            return True, (message_local or 'Canal parado') + suffix
        return ok_local, message_local

    multi_output._start_output = start_output
    manager.stop = MethodType(stop, manager)
    return manager
