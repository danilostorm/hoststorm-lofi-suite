from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

from .config import BR_TZ, SCHEDULER_INTERVAL_SECONDS, SCHEDULER_GRACE_SECONDS
from .db import list_schedules, get_channel, update_schedule_status, audit
from .events import BUS
from .streaming import MANAGER
from .utils import now_dt, now_iso


def _date_in_range(schedule, today):
    iso=today.isoformat()
    if schedule.get('start_date') and iso < schedule['start_date']: return False
    if schedule.get('end_date') and iso > schedule['end_date']: return False
    return True


def due_info(schedule, now=None):
    now=now or now_dt()
    today=now.date()
    if not schedule.get('enabled') or not _date_in_range(schedule,today): return None
    kind=schedule.get('kind','weekly')
    if kind=='once':
        if schedule.get('run_date') != today.isoformat(): return None
    elif kind=='weekdays':
        if now.weekday()>4: return None
    elif kind=='weekly':
        if now.weekday() not in set(schedule.get('weekdays') or []): return None
    # daily always applies
    try:
        hh,mm=map(int,schedule['time'].split(':'))
    except Exception:
        return None
    target=datetime(today.year,today.month,today.day,hh,mm,tzinfo=BR_TZ)
    delta=(now-target).total_seconds()
    policy=schedule.get('conflict_policy','skip')
    grace=12*3600 if policy=='wait' else SCHEDULER_GRACE_SECONDS
    if delta < 0 or delta > grace: return None
    run_key=f'{schedule["id"]}:{today.isoformat()}:{schedule["time"]}'
    if schedule.get('last_run_key')==run_key: return None
    return {'run_key':run_key,'target':target,'lateness_seconds':int(delta)}


class Scheduler:
    def __init__(self): self.started=False
    def start(self):
        if self.started: return
        self.started=True
        threading.Thread(target=self.loop,daemon=True,name='schedule-engine').start()

    def loop(self):
        time.sleep(4)
        while True:
            try:
                now=now_dt()
                for schedule in list_schedules():
                    info=due_info(schedule,now)
                    if not info: continue
                    cid=schedule['channel_id']
                    status=MANAGER.channel_status(cid)
                    if status.get('running'):
                        policy=schedule.get('conflict_policy','skip')
                        if policy=='wait':
                            update_schedule_status(schedule['id'],last_status='Aguardando a live atual terminar.')
                            continue
                        if policy=='stop_current':
                            MANAGER.stop(cid,'interrompida por novo agendamento')
                            time.sleep(1)
                        else:
                            update_schedule_status(schedule['id'],last_run_key=info['run_key'],last_status='Ignorada: canal já estava ao vivo.')
                            audit('warning','schedule_skipped',cid,'Agenda ignorada porque o canal já estava ao vivo.',{'schedule_id':schedule['id']})
                            BUS.publish('schedule_skipped',{'channel_id':cid,'schedule_id':schedule['id']})
                            continue
                    ok,msg=MANAGER.start(cid,platforms=schedule.get('platforms'),media=schedule.get('media'),trigger='scheduled',schedule=schedule)
                    updates={'last_run_key':info['run_key'],'last_status':msg}
                    if ok: updates['last_started_at']=now_iso()
                    update_schedule_status(schedule['id'],**updates)
                    audit('info' if ok else 'error','schedule_triggered',cid,msg,{'schedule_id':schedule['id'],'ok':ok})
                    BUS.publish('schedule_triggered',{'channel_id':cid,'schedule_id':schedule['id'],'ok':ok,'message':msg})
            except Exception as e:
                audit('error','scheduler_error','',str(e))
            time.sleep(SCHEDULER_INTERVAL_SECONDS)

SCHEDULER=Scheduler()
