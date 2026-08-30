from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from .config import VIDEOS_DIR, AUDIOS_DIR, VIDEO_EXTENSIONS, AUDIO_EXTENSIONS
from .db import media_meta_get, media_meta_upsert, media_usage
from .utils import sha256_file


def media_path(kind: str, filename: str) -> Path:
    base = VIDEOS_DIR if kind == 'video' else AUDIOS_DIR
    return base / os.path.basename(filename)


def list_files(kind: str) -> list[str]:
    base = VIDEOS_DIR if kind == 'video' else AUDIOS_DIR
    exts = VIDEO_EXTENSIONS if kind == 'video' else AUDIO_EXTENSIONS
    return sorted([p.name for p in base.iterdir() if p.is_file() and p.suffix.lower() in exts], key=str.casefold)


def _fps(value: str) -> float:
    try:
        if '/' in value:
            a,b=value.split('/',1)
            return round(float(a)/float(b),3) if float(b) else 0
        return round(float(value),3)
    except Exception:
        return 0


def ffprobe_meta(kind: str, filename: str, compute_hash=False, force=False) -> dict:
    path=media_path(kind, filename)
    if not path.exists():
        raise FileNotFoundError(filename)
    st=path.stat()
    cached=media_meta_get(kind, filename)
    if cached and not force and cached.get('size_bytes')==st.st_size and abs(float(cached.get('mtime',0))-st.st_mtime)<0.001 and (cached.get('sha256') or not compute_hash):
        cached['usage_count']=media_usage(filename)
        return cached
    cmd=['ffprobe','-v','error','-show_entries','format=duration:stream=index,codec_type,codec_name,width,height,r_frame_rate','-of','json',str(path)]
    out=subprocess.check_output(cmd,stderr=subprocess.STDOUT,text=True,timeout=30)
    payload=json.loads(out or '{}')
    duration=float((payload.get('format') or {}).get('duration') or 0)
    width=height=0; codec=''; fps=0
    for stream in payload.get('streams') or []:
        if stream.get('codec_type')=='video':
            width=int(stream.get('width') or 0); height=int(stream.get('height') or 0)
            codec=str(stream.get('codec_name') or ''); fps=_fps(str(stream.get('r_frame_rate') or '0'))
            break
    meta={'kind':kind,'filename':filename,'size_bytes':st.st_size,'mtime':st.st_mtime,'duration_seconds':duration,'width':width,'height':height,'codec':codec,'fps':fps,'sha256':cached.get('sha256','') if cached else ''}
    if compute_hash:
        meta['sha256']=sha256_file(path)
    media_meta_upsert(meta)
    meta['usage_count']=media_usage(filename)
    return meta


def probe_duration(filename: str) -> float:
    return float(ffprobe_meta('video',filename).get('duration_seconds') or 0)


def scan_library(compute_hash=False) -> list[dict]:
    rows=[]
    for kind in ('video','audio'):
        for name in list_files(kind):
            try:
                rows.append(ffprobe_meta(kind,name,compute_hash=compute_hash))
            except Exception as e:
                path=media_path(kind,name)
                rows.append({'kind':kind,'filename':name,'size_bytes':path.stat().st_size if path.exists() else 0,'duration_seconds':0,'width':0,'height':0,'codec':'','fps':0,'sha256':'','usage_count':media_usage(name),'error':str(e)})
    return rows


def delete_media(kind: str, filename: str) -> tuple[bool,str]:
    filename=os.path.basename(filename)
    usage=media_usage(filename)
    if usage:
        return False, f'Arquivo em uso por {usage} configuração(ões)/agenda(s).'
    path=media_path(kind,filename)
    if not path.exists():
        return False,'Arquivo não encontrado.'
    path.unlink()
    return True,'Arquivo excluído.'


def disk_free_bytes() -> int:
    return shutil.disk_usage(VIDEOS_DIR).free
