from __future__ import annotations

import json
import os
import random
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from .config import VIDEOS_DIR, AUDIOS_DIR, LOGS_DIR, TMP_DIR, BR_TZ, SUPERVISOR_INTERVAL_SECONDS
from .db import (
    get_channel, set_desired_running, create_live_run, finish_live_run,
    upsert_platform_run, audit, update_schedule_status,
)
from .events import BUS
from .media import probe_duration
from .notifications import notify
from .utils import now_dt, now_iso, now_br, build_target, parse_bitrate_k, safe_filename

RESTART_BACKOFF = [5, 15, 30, 60, 60, 60]

@dataclass
class PlatformState:
    slug: str
    label: str
    process: subprocess.Popen | None = None
    cmd: list[str] = field(default_factory=list)
    retries: int = 0
    next_retry_at: float = 0
    last_error: str = ''

@dataclass
class Session:
    channel_id: str
    run_id: str
    trigger: str
    schedule_id: str | None
    platforms: list[str]
    media: list[str]
    started_at: str
    stop_at: str = ''
    stop_requested: bool = False
    desired_running: bool = True
    platform_states: dict[str, PlatformState] = field(default_factory=dict)
    work_channel: dict = field(default_factory=dict)
    playlist_path: str = ''
    max_duration_seconds: float = 0

class StreamManager:
    def __init__(self):
        self.sessions: dict[str, Session] = {}
        self.lock=threading.RLock()
        self._supervisor_started=False

    def start_threads(self):
        if self._supervisor_started: return
        self._supervisor_started=True
        threading.Thread(target=self._supervisor_loop,daemon=True,name='stream-supervisor').start()
        threading.Thread(target=self._resume_247_lives,daemon=True,name='resume-247').start()

    def _resume_247_lives(self):
        # Recria apenas lives manuais marcadas como 24/7 depois de reiniciar o container.
        time.sleep(7)
        try:
            from .db import list_channels
            for cid,ch in list_channels(False).items():
                if ch.get('desired_running') and not self.channel_status(cid).get('running'):
                    ok,msg=self.start(cid,trigger='manual')
                    self.log(cid,'Retomada 24/7 após reinício: '+msg)
        except Exception as e:
            audit('error','resume_247_error','',str(e))

    def channel_status(self, cid: str):
        with self.lock:
            s=self.sessions.get(cid)
            if not s:
                return {'running':False,'platforms':{},'run_id':'','started_at':'','stop_at':'','trigger':''}
            platforms={}
            for slug,ps in s.platform_states.items():
                alive=bool(ps.process and ps.process.poll() is None)
                platforms[slug]={'running':alive,'pid':ps.process.pid if alive else 0,'retries':ps.retries,'last_error':ps.last_error,'label':ps.label}
            return {'running':any(x['running'] for x in platforms.values()),'platforms':platforms,'run_id':s.run_id,'started_at':s.started_at,'stop_at':s.stop_at,'trigger':s.trigger,'schedule_id':s.schedule_id or ''}

    def all_status(self):
        with self.lock:
            ids=list(self.sessions)
        return {cid:self.channel_status(cid) for cid in ids}

    def _resolve_stream_url(self,url):
        url=str(url or '').strip()
        if not url: raise RuntimeError('URL da fonte vazia.')
        if url.startswith(('rtmp://','rtmps://')): return url
        if url.startswith(('http://','https://')) and any(x in url.lower() for x in ('.m3u8','.mp4','.flv','.mov','.mkv','.webm','.ts')): return url
        try:
            out=subprocess.check_output(['yt-dlp','--no-playlist','-f','best[height<=1080]/best','-g',url],stderr=subprocess.STDOUT,text=True,timeout=45).strip()
            lines=[x.strip() for x in out.splitlines() if x.strip()]
            if not lines: raise RuntimeError('yt-dlp não retornou URL direta.')
            return lines[0]
        except subprocess.CalledProcessError as e:
            raise RuntimeError('Falha no yt-dlp: '+(e.output or str(e))[-800:])

    def _make_concat_file(self, cid, run_id, media):
        p=TMP_DIR/f'playlist-{cid}-{run_id}.txt'
        def esc(x): return str(x).replace("'", "'\\''")
        p.write_text('\n'.join([f"file '{esc(VIDEOS_DIR/name)}'" for name in media])+'\n',encoding='utf-8')
        return p

    def _input_args(self, session: Session, vertical=False):
        ch=session.work_channel
        # Scheduled runs always use the selected playlist. Manual runs can use URL or configured local media.
        if session.trigger=='scheduled':
            if len(session.media)>1:
                args=['-re']
                if ch.get('_repeat_playlist'): args += ['-stream_loop','-1']
                args += ['-f','concat','-safe','0','-i',session.playlist_path]
                return args
            video=VIDEOS_DIR/session.media[0]
            args=['-re']
            if ch.get('_repeat_playlist'): args += ['-stream_loop','-1']
            args += ['-i',str(video)]
            return args
        source_mode=str(ch.get('shorts_source_mode') if vertical and ch.get('shorts_source_mode') not in (None,'same') else ch.get('source_mode','local'))
        source_url=str(ch.get('shorts_source_url') if vertical and source_mode=='url' else ch.get('source_url',''))
        if source_mode=='url':
            return ['-re','-i',self._resolve_stream_url(source_url)]
        video_name=safe_filename(ch.get('shorts_video') if vertical and ch.get('shorts_video') else ch.get('video'))
        if not video_name or not (VIDEOS_DIR/video_name).exists(): raise RuntimeError('Vídeo local não encontrado.')
        return ['-re','-stream_loop','-1','-i',str(VIDEOS_DIR/video_name)]

    def _video_filter(self,ch,vertical=False):
        if vertical:
            fit=str(ch.get('shorts_fit','contain') or 'contain')
            if fit=='stretch': return 'scale=1080:1920,setsar=1,format=yuv420p'
            if fit=='crop': return 'scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,format=yuv420p'
            return 'scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,format=yuv420p'
        res=str(ch.get('resolution','1920x1080') or '1920x1080').replace('x',':')
        return f'scale={res}:force_original_aspect_ratio=decrease,pad={res}:(ow-iw)/2:(oh-ih)/2,format=yuv420p'

    def _build_cmd(self, session: Session, slug: str):
        ch=session.work_channel
        dest=(ch.get('destinations') or {}).get(slug) or {}
        target=build_target(dest.get('rtmp_url'),dest.get('stream_key'))
        if not target: raise RuntimeError(f'Destino {slug} sem RTMP URL/chave válida.')
        vertical = slug=='youtube_shorts' or (slug=='kwai' and str(dest.get('mode','horizontal'))=='vertical')
        cmd=['ffmpeg','-hide_banner','-loglevel','warning'] + self._input_args(session,vertical=vertical)
        # External audio. Scheduled runs use channel audio; vertical can override via shorts_audio.
        audio_name=ch.get('audio','')
        if vertical and ch.get('shorts_audio','__same__')!='__same__': audio_name=ch.get('shorts_audio','')
        if audio_name:
            audio=AUDIOS_DIR/safe_filename(audio_name)
            if audio.exists(): cmd += ['-stream_loop','-1','-i',str(audio),'-map','0:v:0','-map','1:a:0']
            else: cmd += ['-map','0:v:0','-map','0:a?']
        else:
            cmd += ['-map','0:v:0','-map','0:a?']
        fps=str(ch.get('fps','30')); fps_i=max(1,int(float(fps)))
        vb=str(ch.get('shorts_video_bitrate','3500k') if vertical else ch.get('video_bitrate','4500k'))
        ab=str(ch.get('audio_bitrate','160k')); preset=str(ch.get('preset','veryfast'))
        vbnum=parse_bitrate_k(vb,3500 if vertical else 4500)
        cmd += ['-vf',self._video_filter(ch,vertical),'-r',fps,'-c:v','libx264','-preset',preset,'-b:v',vb,'-maxrate',vb,'-bufsize',f'{vbnum*2}k','-g',str(fps_i*2),'-keyint_min',str(fps_i*2),'-sc_threshold','0','-c:a','aac','-b:a',ab,'-ar','44100']
        if session.max_duration_seconds>0: cmd += ['-t',f'{session.max_duration_seconds:.3f}']
        cmd += ['-f','flv',target]
        return cmd

    def _masked(self,cmd,ch):
        text=' '.join(map(str,cmd))
        for d in (ch.get('destinations') or {}).values():
            k=str(d.get('stream_key') or '')
            if k: text=text.replace(k,'***STREAM_KEY***')
        return text

    def _log_path(self,cid): return LOGS_DIR/f'{cid}.log'
    def log(self,cid,message):
        with self._log_path(cid).open('a',encoding='utf-8') as f: f.write(f'[{now_br()}] {message}\n')

    def _start_platform(self, session: Session, slug: str, recovery=False):
        ch=session.work_channel
        dest=(ch.get('destinations') or {}).get(slug) or {}
        label=str(dest.get('label') or slug)
        ps=session.platform_states.get(slug) or PlatformState(slug=slug,label=label)
        cmd=self._build_cmd(session,slug)
        f=open(self._log_path(session.channel_id),'a',encoding='utf-8')
        f.write(f'\n[{now_br()}] {"RECUPERANDO" if recovery else "INICIANDO"} {label}\n{self._masked(cmd,ch)}\n')
        f.flush()
        try:
            proc=subprocess.Popen(cmd,stdout=f,stderr=subprocess.STDOUT,text=True)
            f.close()
            ps.process=proc; ps.cmd=cmd; ps.last_error=''; ps.next_retry_at=0
            session.platform_states[slug]=ps
            upsert_platform_run(session.run_id,slug,pid=proc.pid,status='running',started_at=now_iso(),retries=ps.retries,last_error='')
            BUS.publish('platform_started',{'channel_id':session.channel_id,'slug':slug,'pid':proc.pid,'recovery':recovery})
            self.log(session.channel_id,f'{label} iniciado. PID {proc.pid}.')
            return True
        except Exception as e:
            try: f.close()
            except Exception: pass
            ps.last_error=str(e); session.platform_states[slug]=ps
            upsert_platform_run(session.run_id,slug,status='error',last_error=str(e),retries=ps.retries)
            self.log(session.channel_id,f'Falha iniciando {label}: {e}')
            return False

    def _playlist_duration(self, media):
        total=0
        for name in media:
            d=probe_duration(name)
            if d<=0: raise RuntimeError(f'Não foi possível detectar a duração de {name}.')
            total += d
        return total

    def start(self,cid,platforms=None,media=None,trigger='manual',schedule=None):
        with self.lock:
            if cid in self.sessions and self.channel_status(cid)['running']:
                return False,'Canal já está ao vivo.'
            ch=get_channel(cid)
            if not ch: return False,'Canal não encontrado.'
            platforms=list(platforms or [slug for slug,d in ch['destinations'].items() if d.get('enabled')])
            platforms=[p for p in platforms if p in ch['destinations']]
            if not platforms: return False,'Selecione pelo menos uma plataforma.'
            missing=[ch['destinations'][p].get('label',p) for p in platforms if not build_target(ch['destinations'][p].get('rtmp_url'),ch['destinations'][p].get('stream_key'))]
            if missing: return False,'Configure RTMP/chave: '+', '.join(missing)
            media=list(media or [])
            max_duration=0; stop_at=''; schedule_id=None
            work=json.loads(json.dumps(ch))
            if trigger=='scheduled':
                if not schedule: return False,'Agenda inválida.'
                schedule_id=schedule['id']
                if not media: media=list(schedule.get('media') or [])
                if schedule.get('shuffle'): random.shuffle(media)
                if not media: return False,'Agenda sem mídia.'
                total=self._playlist_duration(media)
                stop_before=max(0,int(schedule.get('stop_before_seconds') or 60))
                if schedule.get('repeat_playlist') and int(schedule.get('max_duration_minutes') or 0)>0:
                    max_duration=max(60,int(schedule['max_duration_minutes'])*60-stop_before)
                else:
                    max_duration=total-stop_before
                if max_duration<=0: return False,'A duração total da agenda precisa ser maior que a antecedência de parada.'
                stop_at=(now_dt()+timedelta(seconds=max_duration)).isoformat()
                work['_repeat_playlist']=bool(schedule.get('repeat_playlist'))
            else:
                # Validate manual source.
                if str(work.get('source_mode','local'))!='url':
                    name=safe_filename(work.get('video'))
                    if not name or not (VIDEOS_DIR/name).exists(): return False,'Escolha um vídeo válido ou URL.'
                    media=[name]
            media_label=' → '.join(media) if media else str(work.get('source_url') or '')
            run_id=create_live_run(cid,schedule_id,trigger,media_label,platforms,stop_at)
            sess=Session(channel_id=cid,run_id=run_id,trigger=trigger,schedule_id=schedule_id,platforms=platforms,media=media,started_at=now_iso(),stop_at=stop_at,desired_running=True,work_channel=work,max_duration_seconds=max_duration)
            if trigger=='scheduled' and len(media)>1:
                sess.playlist_path=str(self._make_concat_file(cid,run_id,media))
            self.sessions[cid]=sess
            if trigger=='manual': set_desired_running(cid,True)
            started=[]; errors=[]
            for slug in platforms:
                if self._start_platform(sess,slug): started.append(slug)
                else: errors.append(slug)
            if not started:
                self.sessions.pop(cid,None); finish_live_run(run_id,'failed','Nenhuma plataforma iniciou.')
                if trigger=='manual': set_desired_running(cid,False)
                return False,'Nenhuma plataforma conseguiu iniciar.'
            if schedule_id:
                update_schedule_status(schedule_id,last_started_at=now_iso(),last_status='Live agendada iniciada: '+', '.join(started))
            audit('info','live_started',cid,f'Live {trigger} iniciada em: {", ".join(started)}',{'run_id':run_id,'platforms':started})
            notify(f'🟢 HostStorm: {ch["name"]} iniciou ({trigger}) em {", ".join(started)}.')
            BUS.publish('live_started',{'channel_id':cid,'run_id':run_id,'platforms':started,'trigger':trigger,'stop_at':stop_at})
            return True,'Live iniciada: '+', '.join(started)+((' | falharam: '+', '.join(errors)) if errors else '')

    def stop(self,cid,reason='manual'):
        with self.lock:
            sess=self.sessions.get(cid)
            if not sess:
                set_desired_running(cid,False)
                return True,'Canal já estava parado.'
            sess.stop_requested=True; sess.desired_running=False
            for slug,ps in sess.platform_states.items():
                p=ps.process
                if p and p.poll() is None:
                    try: p.send_signal(signal.SIGTERM); p.wait(timeout=4)
                    except Exception:
                        try: p.kill()
                        except Exception: pass
                upsert_platform_run(sess.run_id,slug,status='stopped',ended_at=now_iso(),pid=0)
            finish_live_run(sess.run_id,'finished',reason)
            if sess.schedule_id: update_schedule_status(sess.schedule_id,last_finished_at=now_iso(),last_status='Finalizada: '+reason)
            if sess.trigger=='manual': set_desired_running(cid,False)
            self.sessions.pop(cid,None)
            if sess.playlist_path:
                try: Path(sess.playlist_path).unlink(missing_ok=True)
                except Exception: pass
            ch=get_channel(cid)
            audit('info','live_stopped',cid,f'Live encerrada: {reason}',{'run_id':sess.run_id})
            notify(f'🔴 HostStorm: {(ch or {}).get("name",cid)} encerrou. Motivo: {reason}.')
            BUS.publish('live_stopped',{'channel_id':cid,'run_id':sess.run_id,'reason':reason})
            self.log(cid,'Live encerrada: '+reason)
            return True,'Live encerrada.'

    def preflight(self,cid,platforms=None,media=None):
        checks=[]; ok=True
        ch=get_channel(cid)
        if not ch: return {'ok':False,'checks':[{'name':'Canal','ok':False,'message':'Não encontrado'}]}
        def add(name,good,msg):
            nonlocal ok; checks.append({'name':name,'ok':bool(good),'message':msg}); ok=ok and bool(good)
        add('FFmpeg',bool(shutil_which('ffmpeg')),'Disponível' if shutil_which('ffmpeg') else 'Não encontrado')
        add('FFprobe',bool(shutil_which('ffprobe')),'Disponível' if shutil_which('ffprobe') else 'Não encontrado')
        pfs=list(platforms or [s for s,d in ch['destinations'].items() if d.get('enabled')])
        for slug in pfs:
            d=ch['destinations'].get(slug,{})
            good=bool(build_target(d.get('rtmp_url'),d.get('stream_key')))
            add(d.get('label',slug),good,'RTMP configurado' if good else 'Falta URL/chave RTMP')
        names=list(media or [])
        if not names and ch.get('source_mode')!='url': names=[safe_filename(ch.get('video'))]
        for name in names:
            p=VIDEOS_DIR/safe_filename(name); good=p.exists()
            msg='Arquivo encontrado'
            if good:
                try: msg=f'Duração {int(probe_duration(name))}s'
                except Exception as e: good=False; msg=str(e)
            add('Mídia '+name,good,msg)
        if ch.get('source_mode')=='url': add('URL fonte',bool(ch.get('source_url')),'Configurada' if ch.get('source_url') else 'Vazia')
        return {'ok':ok,'checks':checks}

    def _supervisor_loop(self):
        time.sleep(3)
        while True:
            try:
                now=time.time(); stop_ids=[]
                with self.lock:
                    sessions=list(self.sessions.values())
                for sess in sessions:
                    if sess.stop_at:
                        try:
                            from .utils import parse_iso
                            dt=parse_iso(sess.stop_at)
                            if dt and now_dt()>=dt:
                                stop_ids.append((sess.channel_id,'horário/duração programada concluída'))
                                continue
                        except Exception: pass
                    all_dead=True
                    for slug in sess.platforms:
                        ps=sess.platform_states.get(slug)
                        if not ps: continue
                        p=ps.process
                        if p and p.poll() is None:
                            all_dead=False
                            continue
                        if sess.stop_requested or not sess.desired_running:
                            continue
                        # Scheduled one-shot that exits naturally near the end should finish, not restart forever.
                        if sess.trigger=='scheduled' and sess.max_duration_seconds>0:
                            from .utils import parse_iso
                            started=parse_iso(sess.started_at)
                            if started and (now_dt()-started).total_seconds() >= max(0,sess.max_duration_seconds-10):
                                continue
                        # If a retry was already scheduled, execute it once the timer expires.
                        if ps.next_retry_at:
                            if now < ps.next_retry_at:
                                continue
                            ps.next_retry_at=0
                            if self._start_platform(sess,slug,recovery=True):
                                all_dead=False
                                notify(f'✅ HostStorm: {ps.label} recuperado automaticamente.')
                                continue
                        code=p.returncode if p else None
                        ps.last_error=f'FFmpeg encerrou com código {code}'
                        delay=RESTART_BACKOFF[min(ps.retries,len(RESTART_BACKOFF)-1)]
                        ps.retries += 1
                        ps.next_retry_at=now+delay
                        upsert_platform_run(sess.run_id,slug,status='reconnecting',retries=ps.retries,last_error=ps.last_error,ended_at=now_iso())
                        BUS.publish('platform_reconnecting',{'channel_id':sess.channel_id,'slug':slug,'retry_in':delay,'attempt':ps.retries})
                        self.log(sess.channel_id,f'{ps.label} caiu. Nova tentativa em {delay}s.')
                        notify(f'⚠️ HostStorm: {ps.label} caiu em {(get_channel(sess.channel_id) or {}).get("name",sess.channel_id)}. Tentativa {ps.retries} em {delay}s.')
                    if all_dead and sess.trigger=='scheduled' and sess.max_duration_seconds>0:
                        from .utils import parse_iso
                        started=parse_iso(sess.started_at)
                        if started and (now_dt()-started).total_seconds() >= max(0,sess.max_duration_seconds-10):
                            stop_ids.append((sess.channel_id,'mídia agendada concluída'))
                for cid,reason in stop_ids: self.stop(cid,reason)
            except Exception as e:
                audit('error','supervisor_error','',str(e))
            time.sleep(SUPERVISOR_INTERVAL_SECONDS)

MANAGER=StreamManager()

def shutil_which(name):
    import shutil
    return shutil.which(name)
