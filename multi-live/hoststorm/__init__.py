import os


def create_app():
    from flask import Flask, g
    from .config import MAX_UPLOAD_GB, ADMIN_USER, ADMIN_PASSWORD
    from . import db as db_module
    from . import streaming as streaming_module
    from . import web as legacy_web
    from .pro_db import init_pro_db
    from .push import init_push_db
    from .secure_compat import install_secure_compat
    from .url_sources import install_url_sources, urlmedia_bp
    from .url_resilience import install_url_resilience
    from .pro_streaming import install_professional_streaming
    from .overlay_pro import install_advanced_overlays
    from .passkeys import install_passkey_auth, list_passkeys, passkey_bp
    from .broadcast_automation import automation_bp, install_broadcast_automation
    from .ai_db import init_ai_db
    from .ai_voice import install_ai_voice
    from .schedule_guard import install_schedule_platform_guard
    from .recovery import install_recovery_engine
    from .recovery_retry import install_recovery_retry
    from .live_url_guard import install_live_url_guard
    from .youtube_playlist import install_youtube_playlist, playlist_bp
    from .parallel_schedules import install_parallel_schedules
    from .seamless_playlist import install_seamless_youtube_playlist
    from .tenant_channels import install_creator_tenancy

    app = Flask(__name__, template_folder='../templates', static_folder='../static')
    app.secret_key = os.environ.get('HOSTSTORM_SECRET_KEY') or os.environ.get('LV2_ADMIN_PASSWORD') or os.urandom(32)
    app.config.update(
        MAX_CONTENT_LENGTH=MAX_UPLOAD_GB * 1024 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=os.environ.get('HOSTSTORM_COOKIE_SECURE', '0') == '1',
        PERMANENT_SESSION_LIFETIME=60 * 60 * 12,
    )

    db_module.init_db()
    init_pro_db(ADMIN_USER, ADMIN_PASSWORD)
    init_push_db()
    init_ai_db()
    install_secure_compat(db_module, legacy_web, streaming_module)

    install_url_sources(app, db_module, legacy_web, streaming_module)

    from . import scheduler as scheduler_module
    scheduler_module.list_schedules = db_module.list_schedules

    install_professional_streaming(streaming_module.MANAGER, streaming_module)
    install_advanced_overlays(streaming_module.MANAGER)
    install_url_resilience(streaming_module.MANAGER, streaming_module)
    install_ai_voice(streaming_module.MANAGER, streaming_module)

    from .distributed import install_distributed
    install_distributed(streaming_module.MANAGER)
    install_broadcast_automation(app, db_module, legacy_web, scheduler_module, streaming_module.MANAGER)

    install_schedule_platform_guard(streaming_module.MANAGER, streaming_module)

    # Keep the fully-featured pre-recovery platform launcher. YouTube playlists and
    # parallel sidecar schedules need item-aware checkpoints rather than the old
    # channel-wide seek wrapper, but still need profiles, overlays, URL resilience and TTS.
    streaming_module.MANAGER._hs_pre_recovery_start_platform = streaming_module.MANAGER._start_platform

    install_recovery_engine(streaming_module.MANAGER, streaming_module, db_module)
    install_recovery_retry(streaming_module.MANAGER, streaming_module, db_module)
    install_live_url_guard(streaming_module.MANAGER, streaming_module)

    # v4.1: real YouTube playlists are item-aware and can resume at the correct item/time.
    install_youtube_playlist(app, db_module, legacy_web, scheduler_module, streaming_module.MANAGER, streaming_module)
    # v4.1: different destinations of the same HostStorm channel may run independently.
    install_parallel_schedules(streaming_module.MANAGER, streaming_module, db_module)
    # v4.2: playlist item switches happen behind a persistent local bridge. The RTMP
    # publisher remains connected while only the feeder process changes source videos.
    install_seamless_youtube_playlist(streaming_module.MANAGER, streaming_module, db_module)
    # v4.3: creator accounts own their channels and schedules. Request-scoped filters are
    # installed after all DB/streaming wrappers so background schedulers still see everything.
    install_creator_tenancy(db_module, legacy_web, streaming_module, scheduler_module)

    # Compatibilidade do módulo web profissional: list_backups pertence a professional.py.
    from . import pro_db as pro_db_module
    from .professional import list_backups as professional_list_backups
    pro_db_module.list_backups = professional_list_backups

    legacy_web.ADMIN_PASSWORD = ''
    from . import auth as auth_module
    from .auth import auth_bp
    install_passkey_auth(auth_module)

    from . import integrations as integrations_module
    from .integrations_v32 import check_integration_v32
    integrations_module.check_integration = check_integration_v32

    from .pro_web import pro_bp
    from .ops_web import ops_bp
    from .ai_web import ai_bp
    from .ai_compat import compat_bp

    @app.context_processor
    def passkey_context():
        user = getattr(g, 'user', None)
        try:
            return {'current_passkeys': list_passkeys(user['id']) if user else []}
        except Exception:
            return {'current_passkeys': []}

    app.register_blueprint(auth_bp)
    app.register_blueprint(passkey_bp)
    app.register_blueprint(legacy_web.bp)
    app.register_blueprint(pro_bp)
    app.register_blueprint(ops_bp)
    app.register_blueprint(urlmedia_bp)
    app.register_blueprint(playlist_bp)
    app.register_blueprint(automation_bp)
    app.register_blueprint(ai_bp)
    app.register_blueprint(compat_bp)

    streaming_module.MANAGER.start_threads()
    SCHEDULER = scheduler_module.SCHEDULER
    SCHEDULER.start()
    from .broadcast import BROADCAST
    BROADCAST.start(streaming_module.MANAGER)
    from .services import SERVICES
    SERVICES.start()
    from .ai_host import AI_HOST
    AI_HOST.start(streaming_module.MANAGER)
    return app
