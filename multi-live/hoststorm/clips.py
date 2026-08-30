from __future__ import annotations

import subprocess
from pathlib import Path

from .pro_db import connect
from .professional import CLIPS_DIR


def list_recordings(limit=300):
    with connect() as con:
        return [dict(r) for r in con.execute('SELECT * FROM recordings ORDER BY created_at DESC LIMIT ?',(int(limit),)).fetchall()]


def get_marker(mid):
    with connect() as con:
        r=con.execute('SELECT * FROM markers WHERE id=?',(mid,)).fetchone(); return dict(r) if r else None


def recording_for_run(run_id):
    with connect() as con:
        r=con.execute('SELECT * FROM recordings WHERE live_run_id=? ORDER BY created_at DESC LIMIT 1',(run_id,)).fetchone(); return dict(r) if r else None


def create_clip(marker_id,before=15,after=45):
    m=get_marker(marker_id)
    if not m: raise FileNotFoundError('Marcador não encontrado.')
    rec=recording_for_run(m['live_run_id'])
    if not rec: raise FileNotFoundError('Não existe gravação para esta live.')
    src=Path(rec['path'])
    if not src.exists(): raise FileNotFoundError('Arquivo de gravação não encontrado.')
    CLIPS_DIR.mkdir(parents=True,exist_ok=True)
    start=max(0,float(m['offset_seconds'])-max(0,int(before))); duration=max(1,int(before)+int(after))
    out=CLIPS_DIR/f"clip-{m['channel_id']}-{marker_id}.mp4"
    cmd=['ffmpeg','-hide_banner','-loglevel','error','-ss',f'{start:.3f}','-i',str(src),'-t',str(duration),'-c:v','libx264','-preset','veryfast','-crf','20','-c:a','aac','-b:a','160k','-movflags','+faststart','-y',str(out)]
    p=subprocess.run(cmd,capture_output=True,text=True,timeout=max(120,duration*5))
    if p.returncode!=0: raise RuntimeError((p.stderr or p.stdout)[-1500:])
    with connect() as con: con.execute('UPDATE markers SET clip_path=? WHERE id=?',(str(out),marker_id))
    return out


def update_recording_stats(run_id,path,duration=0):
    p=Path(path)
    size=p.stat().st_size if p.exists() else 0
    with connect() as con: con.execute('UPDATE recordings SET size_bytes=?,duration_seconds=? WHERE live_run_id=?',(size,float(duration or 0),run_id))
