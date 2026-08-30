from __future__ import annotations

import signal
import subprocess
import threading
import time
from types import MethodType

from .clips import update_recording_stats
from .config import VIDEOS_DIR
from .pro_db import add_alert, connect as pro_connect, list_profiles, save_metric
from .professional import RECORDINGS_DIR, encoder_capabilities, quality_label
from .utils import now_iso, now_br, parse_bitrate_k, safe_filename


def _profile_map(): return {p['id']:p for p in list_profiles()}

def _apply_profile(ch):
    p=_profile_map().get(str(ch.get('profile_id') or ''))
    if not p:return ch
    ch['resolution']=f"{p['width']}x{p['height']}";ch['fps']=str(p['fps']);ch['video_bitrate']=f"{p['video_bitrate_k']}k";ch['audio_bitrate']=f"{p['audio_bitrate_k']}k";ch['encoder']=p['encoder'];ch['preset']=p['preset'];return ch

def _selected_encoder(ch):
    wanted=str(ch.get('encoder') or 'auto');caps=encoder_capabilities()
    if wanted=='auto':return caps['recommended']
    return wanted if caps['encoders'].get(wanted) else 'libx264'

def _escape_drawtext(value):return str(value or '').replace('\\','\\\\').replace(':','\\:').replace("'","\\'").replace('%','\\%')

def _overlay_filter(base,ch):
    if str(ch.get('overlay_enabled','0')).lower() not in {'1','true','on','yes'}:return base
    filters=[base];pos=str(ch.get('overlay_position') or 'top-right');xy={'top-left':('24','24'),'top-right':('w-text_w-24','24'),'bottom-left':('24','h-text_h-24'),'bottom-right':('w-text_w-24','h-text_h-24')}.get(pos,('w-text_w-24','24'))
    text=_escape_drawtext(ch.get('overlay_text'))
    if text:filters.append(f"drawtext=text='{text}':x={xy[0]}:y={xy[1]}:fontsize=28:fontcolor=white:box=1:boxcolor=black@0.45:boxborderw=10")
    if str(ch.get('overlay_clock','0')).lower() in {'1','true','on','yes'}:filters.append("drawtext=text='%{localtime\\:%d/%m/%Y %H\\:%M\\:%S}':x=24:y=h-text_h-24:fontsize=24:fontcolor=white:box=1:boxcolor=black@0.45:boxborderw=8")
    return ','.join(filters)

def _duration_file(path):
    try:return float(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration','-of','default=nw=1:nk=1',str(path)],text=True,timeout=15).strip() or 0)
    except Exception:return 0


def install_professional_streaming(manager,streaming_module):
    original_build=manager._build_cmd;original_input=manager._input_args;original_status=manager.channel_status;original_start=manager.start;original_stop=manager.stop

    def input_args(self,session,vertical=False):
        try:return original_input(session,vertical)
        except Exception as first:
            ch=session.work_channel;fallback=safe_filename(ch.get('fallback_video')) or safe_filename(ch.get('maintenance_video'));path=VIDEOS_DIR/fallback if fallback else None
            if path and path.exists():
                session.source_failover=True;self.log(session.channel_id,f'Fonte principal falhou ({first}). Entrando em failover: {fallback}.');add_alert('warning','source-failover','Fonte reserva ativada',f'{session.channel_id}: {fallback}',session.channel_id);return ['-re','-stream_loop','-1','-i',str(path)]
            raise

    def build_cmd(self,session,slug):
        _apply_profile(session.work_channel);cmd=original_build(session,slug);ch=session.work_channel;encoder=_selected_encoder(ch)
        try:
            idx=cmd.index('-c:v');cmd[idx+1]=encoder
            if encoder=='h264_nvenc' and '-preset' in cmd:cmd[cmd.index('-preset')+1]='p4'
            elif encoder=='h264_qsv' and '-preset' in cmd:cmd[cmd.index('-preset')+1]='medium'
            elif encoder=='h264_vaapi':
                if '-preset' in cmd:
                    pidx=cmd.index('-preset');del cmd[pidx:pidx+2]
                vfidx=cmd.index('-vf');cmd[vfidx+1]=cmd[vfidx+1]+',format=nv12,hwupload'
                cmd[1:1]=['-vaapi_device','/dev/dri/renderD128']
        except Exception:encoder='libx264'
        try:
            vfidx=cmd.index('-vf');cmd[vfidx+1]=_overlay_filter(cmd[vfidx+1],ch)
        except ValueError:pass
        if '-progress' not in cmd:
            try:fidx=len(cmd)-3 if cmd[-3]=='-f' else cmd.index('-f')
            except Exception:fidx=max(1,len(cmd)-1)
            cmd[fidx:fidx]=['-progress','pipe:1','-nostats']
        session.encoder=encoder;return cmd

    def telemetry_reader(self,session,ps,proc,log_handle):
        metrics={'fps':0.0,'bitrate_k':0.0,'speed':0.0,'dropped_frames':0,'quality':'unknown','updated_at':''};last_save=0.0;last_alert=0.0;expected=float(session.work_channel.get('fps') or 30);target=parse_bitrate_k(session.work_channel.get('video_bitrate'),4500)
        try:
            if proc.stdout is None:return
            for raw in proc.stdout:
                line=raw.rstrip('\n');log_handle.write(line+'\n');log_handle.flush()
                if '=' not in line:continue
                k,v=line.split('=',1);v=v.strip()
                try:
                    if k=='fps':metrics['fps']=float(v or 0)
                    elif k=='bitrate':metrics['bitrate_k']=float(v.replace('kbits/s','').strip() or 0)
                    elif k=='speed':metrics['speed']=float(v.rstrip('x') or 0)
                    elif k in {'drop_frames','dropped_frames'}:metrics['dropped_frames']=int(float(v or 0))
                except Exception:pass
                if k=='progress':
                    metrics['quality']=quality_label(metrics['fps'],expected,metrics['bitrate_k'],target,metrics['speed'],metrics['dropped_frames']);metrics['updated_at']=now_iso();ps.metrics=dict(metrics);now=time.time()
                    if now-last_save>=10:
                        last_save=now
                        try:save_metric(session.channel_id,ps.slug,**metrics)
                        except Exception:pass
                    if metrics['quality'] in {'warning','critical'} and now-last_alert>300:
                        last_alert=now;add_alert('critical' if metrics['quality']=='critical' else 'warning','encoder-quality',f'Qualidade {metrics["quality"]}: {ps.label}',f"FPS {metrics['fps']:.1f} · bitrate {metrics['bitrate_k']:.0f}k · speed {metrics['speed']:.2f}x",session.channel_id)
        finally:
            try:log_handle.close()
            except Exception:pass

    def start_platform(self,session,slug,recovery=False):
        ch=session.work_channel;dest=(ch.get('destinations') or {}).get(slug) or {};label=str(dest.get('label') or slug);ps=session.platform_states.get(slug) or streaming_module.PlatformState(slug=slug,label=label);cmd=self._build_cmd(session,slug);log_handle=open(self._log_path(session.channel_id),'a',encoding='utf-8');log_handle.write(f'\n[{now_br()}] {"RECUPERANDO" if recovery else "INICIANDO"} {label} | encoder={getattr(session,"encoder","auto")}\n{self._masked(cmd,ch)}\n');log_handle.flush()
        try:
            proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1);ps.process=proc;ps.cmd=cmd;ps.last_error='';ps.next_retry_at=0;ps.metrics={};session.platform_states[slug]=ps;streaming_module.upsert_platform_run(session.run_id,slug,pid=proc.pid,status='running',started_at=now_iso(),retries=ps.retries,last_error='');threading.Thread(target=telemetry_reader,args=(self,session,ps,proc,log_handle),daemon=True,name=f'telemetry-{session.channel_id}-{slug}').start();streaming_module.BUS.publish('platform_started',{'channel_id':session.channel_id,'slug':slug,'pid':proc.pid,'recovery':recovery});self.log(session.channel_id,f'{label} iniciado. PID {proc.pid}. Encoder {getattr(session,"encoder","auto")}.');return True
        except Exception as e:
            try:log_handle.close()
            except Exception:pass
            ps.last_error=str(e);session.platform_states[slug]=ps;streaming_module.upsert_platform_run(session.run_id,slug,status='error',last_error=str(e),retries=ps.retries);self.log(session.channel_id,f'Falha iniciando {label}: {e}');add_alert('error','platform-start',f'Falha em {label}',str(e),session.channel_id);return False

    def status(self,cid):
        st=original_status(cid)
        with self.lock:
            sess=self.sessions.get(cid)
            if sess:
                st['encoder']=getattr(sess,'encoder','');st['source_failover']=bool(getattr(sess,'source_failover',False));st['recording']=bool(getattr(sess,'record_process',None) and sess.record_process.poll() is None);st['recording_path']=getattr(sess,'record_path','')
                for slug,p in st.get('platforms',{}).items():
                    ps=sess.platform_states.get(slug);p['metrics']=dict(getattr(ps,'metrics',{}) or {}) if ps else {};p['quality']=p['metrics'].get('quality','unknown')
        return st

    def start_recording(self,session):
        ch=session.work_channel
        if str(ch.get('record_enabled','0')).lower() not in {'1','true','on','yes'}:return
        try:
            RECORDINGS_DIR.mkdir(parents=True,exist_ok=True);path=RECORDINGS_DIR/f'{session.channel_id}-{session.run_id}.mkv';args=['ffmpeg','-hide_banner','-loglevel','warning']+self._input_args(session,False)+['-map','0:v?','-map','0:a?','-c','copy','-y',str(path)];proc=subprocess.Popen(args,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);session.record_process=proc;session.record_path=str(path)
            with pro_connect() as con:con.execute('INSERT OR IGNORE INTO recordings(id,live_run_id,channel_id,path,created_at) VALUES(?,?,?,?,?)',(session.run_id,session.run_id,session.channel_id,str(path),now_iso()))
            self.log(session.channel_id,'Gravação local iniciada: '+path.name)
        except Exception as e:self.log(session.channel_id,'Não foi possível iniciar gravação: '+str(e));add_alert('warning','recording','Falha ao iniciar gravação',str(e),session.channel_id)

    def start(self,*args,**kwargs):
        ok,msg=original_start(*args,**kwargs)
        if ok:
            cid=args[0] if args else kwargs.get('cid')
            with self.lock:
                sess=self.sessions.get(cid)
                if sess:start_recording(self,sess)
        return ok,msg

    def stop(self,cid,*args,**kwargs):
        with self.lock:sess=self.sessions.get(cid);rec=getattr(sess,'record_process',None) if sess else None;path=getattr(sess,'record_path','') if sess else '';run_id=getattr(sess,'run_id','') if sess else ''
        if rec and rec.poll() is None:
            try:rec.send_signal(signal.SIGINT);rec.wait(timeout=8)
            except Exception:
                try:rec.kill()
                except Exception:pass
        if path and run_id:
            try:update_recording_stats(run_id,path,_duration_file(path))
            except Exception:pass
        return original_stop(cid,*args,**kwargs)

    manager._input_args=MethodType(input_args,manager);manager._build_cmd=MethodType(build_cmd,manager);manager._start_platform=MethodType(start_platform,manager);manager.channel_status=MethodType(status,manager);manager.start=MethodType(start,manager);manager.stop=MethodType(stop,manager);return manager
