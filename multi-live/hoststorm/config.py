from pathlib import Path
from zoneinfo import ZoneInfo
import os

APP_DIR = Path(os.environ.get('HOSTSTORM_APP_DIR', '/app'))
MEDIA_DIR = APP_DIR / 'media'
VIDEOS_DIR = MEDIA_DIR / 'videos'
AUDIOS_DIR = MEDIA_DIR / 'audios'
DATA_DIR = APP_DIR / 'data'
LOGS_DIR = APP_DIR / 'logs'
TMP_DIR = DATA_DIR / 'tmp'
DB_PATH = DATA_DIR / 'hoststorm.db'
LEGACY_CHANNELS_PATH = DATA_DIR / 'channels.json'
VERSION_PATH = APP_DIR / 'VERSION'

for path in (VIDEOS_DIR, AUDIOS_DIR, DATA_DIR, LOGS_DIR, TMP_DIR):
    path.mkdir(parents=True, exist_ok=True)

BR_TZ = ZoneInfo('America/Sao_Paulo')
WEEKDAY_LABELS = ['Segunda-feira', 'Terça-feira', 'Quarta-feira', 'Quinta-feira', 'Sexta-feira', 'Sábado', 'Domingo']
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.mkv', '.webm', '.avi', '.m4v', '.ts'}
AUDIO_EXTENSIONS = {'.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg', '.opus'}

ADMIN_USER = os.environ.get('LV2_ADMIN_USER', 'admin')
ADMIN_PASSWORD = os.environ.get('LV2_ADMIN_PASSWORD', '')
MAX_UPLOAD_GB = int(os.environ.get('LV2_MAX_UPLOAD_GB', '40'))
SCHEDULER_GRACE_SECONDS = int(os.environ.get('LV2_SCHEDULER_GRACE_SECONDS', '120'))
SUPERVISOR_INTERVAL_SECONDS = int(os.environ.get('LV2_SUPERVISOR_INTERVAL_SECONDS', '5'))
SCHEDULER_INTERVAL_SECONDS = int(os.environ.get('LV2_SCHEDULER_INTERVAL_SECONDS', '5'))
STOP_BEFORE_SECONDS = 60

DEFAULT_DESTINATIONS = {
    'youtube': {
        'label': 'YouTube', 'enabled': True,
        'rtmp_url': 'rtmp://a.rtmp.youtube.com/live2', 'stream_key': '',
        'mode': 'horizontal', 'dedicated': True,
    },
    'youtube_shorts': {
        'label': 'YouTube Shorts 9:16', 'enabled': False,
        'rtmp_url': 'rtmp://a.rtmp.youtube.com/live2', 'stream_key': '',
        'mode': 'vertical', 'dedicated': True,
    },
    'twitch': {
        'label': 'Twitch', 'enabled': False,
        'rtmp_url': 'rtmp://live.twitch.tv/app', 'stream_key': '',
        'mode': 'horizontal', 'dedicated': True,
    },
    'kick': {
        'label': 'Kick', 'enabled': False,
        'rtmp_url': 'rtmps://fa723fc1b171.global-contribute.live-video.net:443/app', 'stream_key': '',
        'mode': 'horizontal', 'dedicated': True,
    },
    'kwai': {
        'label': 'Kwai', 'enabled': False,
        'rtmp_url': 'rtmp://overseas-tx-sp-push.itotio.com:8100/livecloud', 'stream_key': '',
        'mode': 'horizontal', 'dedicated': True,
    },
    'custom': {
        'label': 'Custom RTMP', 'enabled': False,
        'rtmp_url': '', 'stream_key': '',
        'mode': 'horizontal', 'dedicated': True,
    },
}

DEFAULT_CHANNEL_SETTINGS = {
    'source_mode': 'local',
    'source_url': '',
    'video': '',
    'audio': '',
    'shorts_source_mode': 'same',
    'shorts_source_url': '',
    'shorts_video': '',
    'shorts_audio': '__same__',
    'resolution': '1920x1080',
    'fps': '30',
    'video_bitrate': '4500k',
    'shorts_video_bitrate': '3500k',
    'audio_bitrate': '160k',
    'preset': 'veryfast',
    'shorts_fit': 'contain',
    'format_mode': 'horizontal',
}
