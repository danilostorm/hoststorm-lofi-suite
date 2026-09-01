from __future__ import annotations

import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .ai_db import get_settings, set_live_memory
from .ai_providers import vision
from .config import VIDEOS_DIR
from .media import probe_duration
from .utils import now_iso


def _elapsed(started_at):
    try:
        dt=datetime.fromisoformat(str(started_at).replace('Z','+00:00'))
        if dt.tzinfo is None:dt=dt.replace(tzinfo=timezone.utc)
        return max(0.0,(datetime.now(timezone.utc)-dt.astimezone(timezone.utc)).total_seconds())
    except Exception:return 0.0


def _local_source(session,elapsed):
    media=list(getattr(session,'media',[]) or [])
    if media:
        durations=[];total=0.0
        for name in media:
            d=float(probe_duration(name) or 0);durations.append((name,d));total+=max(0,d)
        if total>0:
            pos=elapsed%total if getattr(session,'work_channel',{}).get('_repeat_playlist') else min(elapsed,max(0,total-.1))
            for name,d in durations:
                if d<=0:continue
                if pos<d:return str(VIDEOS_DIR/name),pos,name
                pos-=d
        name=media[0];return str(VIDEOS_DIR/name),elapsed,name
    ch=getattr(session,'work_channel',{}) or {};name=str(ch.get('video') or '')
    if name and (VIDEOS_DIR/name).exists():
        d=float(probe_duration(name) or 0);return str(VIDEOS_DIR/name),(elapsed%d if d else elapsed),name
    return '','',''


def capture_frame(manager,channel_id,max_width=768):
    with manager.lock:session=manager.sessions.get(channel_id)
    if not session:return None,{}
    elapsed=_elapsed(session.started_at);ch=session.work_channel or {};remote=str(ch.get('_schedule_source_url') or (ch.get('source_url') if str(ch.get('source_mode'))=='url' else '') or '')
    label=str(ch.get('_schedule_source_title') or '')
    source='';offset=elapsed
    if remote:
        try:source=manager._resolve_stream_url(remote)
        except Exception:source=remote
        label=label or remote
    else:
        source,offset,local_label=_local_source(session,elapsed);label=label or local_label
    if not source:return None,{}
    width=max(320,min(1280,int(max_width or 768)))
    cmd=['ffmpeg','-hide_banner','-loglevel','error']
    if offset>0:cmd+=['-ss',f'{offset:.3f}']
    cmd+=['-i',source,'-frames:v','1','-vf',f'scale={width}:-2:force_original_aspect_ratio=decrease','-q:v','4','-f','image2pipe','-vcodec','mjpeg','pipe:1']
    proc=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=35)
    if proc.returncode!=0 or not proc.stdout:raise RuntimeError('Snapshot visual falhou: '+proc.stderr.decode('utf-8','replace')[-800:])
    return proc.stdout,{'source':label,'elapsed_seconds':round(elapsed,1),'run_id':session.run_id,'trigger':session.trigger}


class VisionContext:
    def __init__(self):self.manager=None;self.started=False;self.stop_event=threading.Event();self.last={}
    def start(self,manager):
        self.manager=manager
        if self.started:return
        self.started=True;threading.Thread(target=self._loop,daemon=True,name='ai-vision-context').start()
    def stop(self):self.stop_event.set()
    def _loop(self):
        while not self.stop_event.is_set():
            settings=get_settings();interval=max(20,int(settings.get('vision_interval_seconds') or 45))
            if settings.get('enabled') and settings.get('vision_enabled') and settings.get('llm_provider_id') and self.manager:
                for cid,st in self.manager.all_status().items():
                    if not st.get('running'):continue
                    if time.time()-self.last.get(cid,0)<interval:continue
                    self.last[cid]=time.time()
                    try:
                        image,ctx=capture_frame(self.manager,cid,settings.get('vision_max_width',768))
                        if not image:continue
                        prompt=(
                            'Analise este único frame da transmissão. Responda JSON com reply contendo no máximo duas frases objetivas em português do Brasil. '
                            'Diga somente o que é visualmente evidente e útil para conversar com o chat; não adivinhe nomes, placar ou eventos que não estejam claros. '
                            f'Contexto técnico: fonte={ctx.get("source","")}; tempo aproximado={ctx.get("elapsed_seconds",0)}s.'
                        )
                        summary=vision(settings.get('llm_provider_id',''),prompt,image,'image/jpeg')
                        if summary:set_live_memory(cid,'vision',{'summary':summary,'at':now_iso(),'source':ctx.get('source',''),'elapsed_seconds':ctx.get('elapsed_seconds',0)},ttl_seconds=max(interval*3,180))
                    except Exception as exc:
                        set_live_memory(cid,'vision_error',{'error':str(exc)[-500:],'at':now_iso()},ttl_seconds=180)
            self.stop_event.wait(5)


VISION=VisionContext()
