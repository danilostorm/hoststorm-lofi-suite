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
    from .pro_streaming import install_professional_streaming
    from .overlay_pro import install_advanced_overlays
    from .passkeys import install_passkey_auth, list_passkeys, passkey_bp
    from .broadcast_automation import automation_bp, install_broadcast_automation
    from .ai_db import init_ai_db
    from .ai_voice import install_ai_voice

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

    # v3.1: fontes remotas entram antes dos wrappers profissionais/distribuídos.
    install_url_sources(app, db_module, legacy_web, streaming_module)

    # web.py carrega scheduler.py cedo para expor due_info. Mantemos a referência sincronizada.
    from . import scheduler as scheduler_module
    scheduler_module.list_schedules = db_module.list_schedules

    install_professional_streaming(streaming_module.MANAGER, streaming_module)
    install_advanced_overlays(streaming_module.MANAGER)
    # v4: o barramento TTS entra depois dos overlays/profiles para injetar áudio no comando FFmpeg final.
    install_ai_voice(streaming_module.MANAGER, streaming_module)

    # Distributed wrapper fica dentro da automação de metadados: o Controller altera título/categoria
    # antes de delegar a execução a um nó remoto.
    from .distributed import install_distributed
    install_distributed(streaming_module.MANAGER)
    install_broadcast_automation(app, db_module, legacy_web, scheduler_module, streaming_module.MANAGER)

    # Compatibilidade do módulo web profissional: list_backups pertence a professional.py.
    from . import pro_db as pro_db_module
    from .professional import list_backups as professional_list_backups
    pro_db_module.list_backups = professional_list_backups

    # A autenticação Basic da v2 é substituída pela autenticação profissional da v3.
    legacy_web.ADMIN_PASSWORD = ''
    from . import auth as auth_module
    from .auth import auth_bp
    install_passkey_auth(auth_module)

    # v3.2 amplia o verificador usado pelo painel sem quebrar o armazenamento criptografado existente.
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
