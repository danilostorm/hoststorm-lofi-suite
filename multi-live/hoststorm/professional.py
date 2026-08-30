from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import time
import urllib.request
from pathlib import Path

import psutil

from .config import APP_DIR, DATA_DIR, DB_PATH, LOGS_DIR, MEDIA_DIR, TMP_DIR, VIDEOS_DIR, AUDIOS_DIR
from .utils import now_iso

BACKUP_DIR=DATA_DIR/'backups'
RECORDINGS_DIR=MEDIA_DIR/'recordings'
CLIPS_DIR=MEDIA_DIR/'clips'
IMPORT_DIR=MEDIA_DIR/'imports'
WATCH_DIR=MEDIA_DIR/'watch'
MAINTENANCE_DIR=MEDIA_DIR/'maintenance'
ASSETS_DIR=MEDIA_DIR/'assets'
for p in (BACKUP_DIR,RECORDINGS_DIR,CLIPS_DIR,IMPORT_DIR,WATCH_DIR,MAINTENANCE_DIR,ASSETS_DIR): p.mkdir(parents=True,exist_ok=True)


def _run(cmd,timeout=8):
    try:
        p=subprocess.run(cmd,capture_output=True,text=True,timeout=timeout)
        return p.returncode,(p.stdout or '')+(p.stderr or '')
    except Exception as e:
        return 127,str(e)


def encoder_capabilities():
    rc,out=_run(['ffmpeg','-hide_banner','-encoders'],12)
    low=out.lower()
    caps={
      'libx264': 'libx264' in low,
      'h264_nvenc': 'h264_nvenc' in low,
      'h264_qsv': 'h264_qsv' in low,
      'h264_vaapi': 'h264_vaapi' in low,
    }
    gpu={'nvidia':False,'intel':False,'vaapi':Path('/dev/dri').exists(),'details':[]}
    rc2,nv=_run(['nvidia-smi','--query-gpu=name,utilization.gpu,memory.used,memory.total','--format=csv,noheader,nounits'],5)
    if rc2==0 and nv.strip(): gpu['nvidia']=True; gpu['details']=[x.strip() for x in nv.splitlines() if x.strip()]
    if Path('/dev/dri/renderD128').exists(): gpu['intel']=True
    recommended='h264_nvenc' if caps['h264_nvenc'] and gpu['nvidia'] else ('h264_qsv' if caps['h264_qsv'] and gpu['intel'] else ('h264_vaapi' if caps['h264_vaapi'] and gpu['vaapi'] else 'libx264'))
    return {'encoders':caps,'gpu':gpu,'recommended':recommended}


def system_snapshot():
    disk=psutil.disk_usage(str(APP_DIR if APP_DIR.exists() else '/'))
    net=psutil.net_io_counters()
    return {
      'at':now_iso(),'hostname':socket.gethostname(),'cpu':psutil.cpu_percent(interval=.1),'ram':psutil.virtual_memory().percent,
      'disk_percent':disk.percent,'disk_free':disk.free,'disk_total':disk.total,
      'net_sent':net.bytes_sent,'net_recv':net.bytes_recv,'load':list(os.getloadavg()) if hasattr(os,'getloadavg') else [],
      **encoder_capabilities(),
    }


def diagnose():
    checks=[]
    def add(name,ok,message,severity='error'): checks.append({'name':name,'ok':bool(ok),'message':str(message),'severity':severity})
    for binary in ('ffmpeg','ffprobe'):
        path=shutil.which(binary); add(binary,bool(path),path or 'não encontrado')
    add('SQLite',DB_PATH.exists(),str(DB_PATH))
    try:
        with sqlite3.connect(DB_PATH,timeout=5) as con: row=con.execute('PRAGMA integrity_check').fetchone(); ok=row and row[0]=='ok'
        add('Integridade SQLite',ok,row[0] if row else 'sem resposta')
    except Exception as e: add('Integridade SQLite',False,e)
    try:
        test=TMP_DIR/'diag-write.tmp'; test.write_text('ok'); test.unlink(); add('Permissões de escrita',True,str(TMP_DIR))
    except Exception as e: add('Permissões de escrita',False,e)
    try:
        free=shutil.disk_usage(DATA_DIR).free; add('Espaço em disco',free>2*1024**3,f'{free/1024**3:.1f} GB livres','warning')
    except Exception as e: add('Espaço em disco',False,e)
    try:
        socket.getaddrinfo('a.rtmp.youtube.com',1935); add('DNS',True,'resolução OK')
    except Exception as e: add('DNS',False,e)
    caps=encoder_capabilities(); add('Encoder',True,'recomendado: '+caps['recommended'])
    add('Diretórios de mídia',VIDEOS_DIR.exists() and AUDIOS_DIR.exists(),f'{VIDEOS_DIR} / {AUDIOS_DIR}')
    return {'ok':all(c['ok'] or c['severity']=='warning' for c in checks),'checks':checks,'system':system_snapshot()}


def create_backup(label='manual'):
    BACKUP_DIR.mkdir(parents=True,exist_ok=True)
    stamp=time.strftime('%Y%m%d-%H%M%S')
    out=BACKUP_DIR/f'hoststorm-{label}-{stamp}.db'
    src=sqlite3.connect(DB_PATH,timeout=30)
    dst=sqlite3.connect(out)
    try: src.backup(dst)
    finally: dst.close(); src.close()
    return out


def list_backups():
    return [{'name':p.name,'size':p.stat().st_size,'mtime':p.stat().st_mtime} for p in sorted(BACKUP_DIR.glob('*.db'),key=lambda p:p.stat().st_mtime,reverse=True)]


def restore_backup(name):
    src=BACKUP_DIR/Path(name).name
    if not src.exists(): raise FileNotFoundError('Backup não encontrado.')
    before=create_backup('pre-restore')
    tmp=DB_PATH.with_suffix('.restore.tmp')
    shutil.copy2(src,tmp)
    os.replace(tmp,DB_PATH)
    return before


def cleanup(retention_days=30,backup_keep=20,recording_days=30,clip_days=60):
    now=time.time(); removed=[]
    def prune_dir(folder,days,patterns=('*',)):
        cutoff=now-days*86400
        for pat in patterns:
            for p in folder.glob(pat):
                try:
                    if p.is_file() and p.stat().st_mtime<cutoff: p.unlink(); removed.append(str(p))
                except Exception: pass
    prune_dir(LOGS_DIR,max(1,retention_days),('*.log',))
    prune_dir(TMP_DIR,2)
    prune_dir(RECORDINGS_DIR,max(1,recording_days))
    prune_dir(CLIPS_DIR,max(1,clip_days))
    backups=sorted(BACKUP_DIR.glob('*.db'),key=lambda p:p.stat().st_mtime,reverse=True)
    for p in backups[max(1,backup_keep):]:
        try: p.unlink(); removed.append(str(p))
        except Exception: pass
    return removed


def import_url(url,kind='video'):
    url=str(url or '').strip()
    if not url.startswith(('http://','https://')): raise ValueError('URL inválida.')
    target=VIDEOS_DIR if kind=='video' else AUDIOS_DIR
    ytdlp=shutil.which('yt-dlp')
    if ytdlp:
        template=str(target/'%(title).120s-%(id)s.%(ext)s')
        p=subprocess.run([ytdlp,'--no-playlist','--restrict-filenames','-o',template,url],capture_output=True,text=True,timeout=3600)
        if p.returncode!=0: raise RuntimeError((p.stdout+p.stderr)[-1500:])
        return {'ok':True,'message':'Importação concluída.'}
    name=Path(url.split('?',1)[0]).name or f'import-{int(time.time())}.mp4'
    dest=target/name
    with urllib.request.urlopen(url,timeout=60) as r,dest.open('wb') as f: shutil.copyfileobj(r,f)
    return {'ok':True,'message':dest.name}


def watch_scan():
    moved=[]
    for p in WATCH_DIR.iterdir():
        if not p.is_file(): continue
        ext=p.suffix.lower()
        dest=VIDEOS_DIR/p.name if ext in {'.mp4','.mov','.mkv','.webm','.avi','.m4v','.ts'} else (AUDIOS_DIR/p.name if ext in {'.mp3','.wav','.m4a','.aac','.flac','.ogg','.opus'} else None)
        if dest:
            final=dest
            if final.exists(): final=dest.with_name(dest.stem+f'-{int(time.time())}'+dest.suffix)
            shutil.move(str(p),str(final)); moved.append(final.name)
    return moved


def quality_label(fps,expected_fps,bitrate_k,target_k,speed,dropped=0):
    if speed and speed<0.85: return 'critical'
    if expected_fps and fps<expected_fps*.75: return 'critical'
    if target_k and bitrate_k and bitrate_k<target_k*.55: return 'warning'
    if dropped>100: return 'warning'
    return 'excellent'
