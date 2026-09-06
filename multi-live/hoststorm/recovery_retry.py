from __future__ import annotations

import threading
import time
from types import MethodType

from .recovery import _is_stale, _remaining_seconds, list_resumable, mark_state


RETRY_BACKOFF = (5, 15, 30, 60, 60, 60)


def _retry_delay(attempt: int) -> int:
    return RETRY_BACKOFF[min(max(0, int(attempt or 0)), len(RETRY_BACKOFF) - 1)]


def install_recovery_retry(manager, streaming_module, db_module):
    """Keep boot recovery alive when the host returns before the Internet does.

    The first recovery attempt happens very early during app startup. If DNS/Internet or
    the platform is still unavailable at that exact moment, this worker keeps trying with
    backoff instead of leaving a scheduled live permanently stopped until human action.
    """
    original_start_threads = manager.start_threads
    manager._hs_recovery_retry_started = False

    def worker(self):
        attempts = {}
        next_attempt = {}
        time.sleep(8)
        while True:
            try:
                now = time.time()
                active_ids = set()
                for state in list_resumable():
                    cid = str(state.get('channel_id') or '')
                    if not cid:
                        continue
                    active_ids.add(cid)
                    if self.channel_status(cid).get('running'):
                        attempts.pop(cid, None)
                        next_attempt.pop(cid, None)
                        continue
                    if _is_stale(state):
                        mark_state(cid, 'expired', 'Checkpoint antigo demais para retomada automática.', False)
                        attempts.pop(cid, None)
                        next_attempt.pop(cid, None)
                        continue
                    total = max(0.0, float(state.get('max_duration_seconds') or 0))
                    if total > 0 and _remaining_seconds(state) <= 3:
                        mark_state(cid, 'finished', 'Checkpoint já estava no fim da programação.', False)
                        attempts.pop(cid, None)
                        next_attempt.pop(cid, None)
                        continue
                    if now < float(next_attempt.get(cid, 0) or 0):
                        continue

                    trigger = str(state.get('trigger') or 'manual')
                    platforms = list(state.get('platforms') or [])
                    media = list(state.get('media') or [])
                    schedule = dict(state.get('schedule') or {})
                    if trigger == 'scheduled':
                        sid = str(state.get('schedule_id') or '')
                        if not schedule and sid and not sid.startswith('grid-'):
                            try:
                                schedule = db_module.get_schedule(sid) or {}
                            except Exception:
                                schedule = {}
                        if not schedule:
                            mark_state(cid, 'error', 'Não foi possível reconstruir o agendamento interrompido.', False)
                            continue
                        if media and not schedule.get('media'):
                            schedule['media'] = list(media)
                        if platforms and not schedule.get('platforms'):
                            schedule['platforms'] = list(platforms)

                    self._hs_resume_requests[cid] = state
                    try:
                        if trigger == 'scheduled':
                            ok, msg = self.start(cid, platforms=platforms, media=media, trigger='scheduled', schedule=schedule)
                        else:
                            ok, msg = self.start(cid, platforms=platforms, trigger='manual')
                    except Exception as exc:
                        ok, msg = False, str(exc)
                    finally:
                        self._hs_resume_requests.pop(cid, None)

                    if ok:
                        attempts.pop(cid, None)
                        next_attempt.pop(cid, None)
                        try:
                            streaming_module.audit(
                                'info', 'recovery_retry_success', cid,
                                'Live retomada automaticamente após a conectividade voltar.',
                                {'position_seconds': state.get('position_seconds', 0)},
                            )
                        except Exception:
                            pass
                    else:
                        attempt = int(attempts.get(cid, 0)) + 1
                        attempts[cid] = attempt
                        delay = _retry_delay(attempt - 1)
                        next_attempt[cid] = now + delay
                        mark_state(cid, 'reconnecting', f'Retomada pendente; nova tentativa em {delay}s. {msg}', True)

                # Remove counters for checkpoints that were manually stopped/expired.
                for cid in list(attempts):
                    if cid not in active_ids:
                        attempts.pop(cid, None)
                        next_attempt.pop(cid, None)
            except Exception as exc:
                try:
                    streaming_module.audit('error', 'recovery_retry_error', '', str(exc))
                except Exception:
                    pass
            time.sleep(2)

    def start_threads(self):
        original_start_threads()
        if self._hs_recovery_retry_started:
            return
        self._hs_recovery_retry_started = True
        threading.Thread(target=worker, args=(self,), daemon=True, name='live-recovery-retry').start()

    manager.start_threads = MethodType(start_threads, manager)
    return manager
