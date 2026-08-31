import os


def create_app():
    from flask import Flask
    from .config import MAX_UPLOAD_GB, ADMIN_USER, ADMIN_PASSWORD
    from . import db as db_module
    from . import streaming as streaming_module
    from . import web as legacy_web
    from .pro_db import init_pro_db
    from .push import init_push_db
    from .secure_compat import install_secure_compat
    from .pro_streaming import install_professional_streaming
    from .overlay_pro import install_advanced_overlays

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
    install_secure_compat(db_module, legacy_web, streaming_module)
    install_professional_streaming(streaming_module.MANAGER, streaming_module)
    install_advanced_overlays(streaming_module.MANAGER)

    # Distributed wrapper is applied after local streaming enhancements, so local fallback keeps telemetry/recording/overlays.
    from .distributed import install_distributed
    install_distributed(streaming_module.MANAGER)

    # Compatibilidade do módulo web profissional: list_backups pertence a professional.py.
    from . import pro_db as pro_db_module
    from .professional import list_backups as professional_list_backups
    pro_db_module.list_backups = professional_list_backups

    # A autenticação Basic da v2 é substituída pela autenticação profissional da v3.
    legacy_web.ADMIN_PASSWORD = ''
    from .auth import auth_bp
    from .pro_web import pro_bp
    from .ops_web import ops_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(legacy_web.bp)
    app.register_blueprint(pro_bp)
    app.register_blueprint(ops_bp)

    streaming_module.MANAGER.start_threads()
    from .scheduler import SCHEDULER
    SCHEDULER.start()
    from .broadcast import BROADCAST
    BROADCAST.start(streaming_module.MANAGER)
    from .services import SERVICES
    SERVICES.start()
    return app
