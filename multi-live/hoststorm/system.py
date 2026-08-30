from __future__ import annotations

import os
import shutil
import subprocess
from .config import VERSION_PATH, DATA_DIR, MEDIA_DIR


def version():
    try:
        return VERSION_PATH.read_text(encoding='utf-8').strip()
    except Exception:
        return '2.0.0'


def command_version(cmd):
    try:
        return subprocess.check_output(cmd,stderr=subprocess.STDOUT,text=True,timeout=5).splitlines()[0]
    except Exception as e:
        return 'indisponível: '+str(e)


def system_info():
    disk=shutil.disk_usage(DATA_DIR)
    return {
        'version':version(),
        'ffmpeg':command_version(['ffmpeg','-version']),
        'ffprobe':command_version(['ffprobe','-version']),
        'ytdlp':command_version(['yt-dlp','--version']),
        'disk_total':disk.total,'disk_used':disk.used,'disk_free':disk.free,
    }
