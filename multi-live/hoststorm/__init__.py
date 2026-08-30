def create_app():
    from flask import Flask
    from .config import MAX_UPLOAD_GB
    from .db import init_db
    from .scheduler import SCHEDULER
    from .streaming import MANAGER
    from .web import bp

    app=Flask(__name__, template_folder='../templates', static_folder='../static')
    app.secret_key='hoststorm-v2-local-secret'
    app.config['MAX_CONTENT_LENGTH']=MAX_UPLOAD_GB*1024*1024*1024
    init_db()
    app.register_blueprint(bp)
    MANAGER.start_threads()
    SCHEDULER.start()
    return app
