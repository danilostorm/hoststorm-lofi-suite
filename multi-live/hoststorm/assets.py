from __future__ import annotations

from pathlib import Path

from flask import Blueprint, flash, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

from .auth import require_role
from .config import MEDIA_DIR

assets_bp = Blueprint('assets', __name__)
ASSETS_DIR = MEDIA_DIR / 'assets'
ASSETS_DIR.mkdir(parents=True, exist_ok=True)
ALLOWED = {'.png', '.jpg', '.jpeg', '.webp'}


def list_assets():
    out=[]
    for p in sorted(ASSETS_DIR.iterdir(), key=lambda x:x.name.lower()):
        if p.is_file() and p.suffix.lower() in ALLOWED:
            out.append({'name':p.name,'size':p.stat().st_size})
    return out

@assets_bp.app_context_processor
def assets_context():
    try:return {'overlay_assets':list_assets()}
    except Exception:return {'overlay_assets':[]}

@assets_bp.route('/professional/assets', methods=['GET','POST'])
@require_role('operator')
def assets():
    if request.method=='POST':
        file=request.files.get('file')
        if not file or not file.filename:
            flash('Escolha uma imagem.','error');return redirect(url_for('assets.assets'))
        name=secure_filename(file.filename);ext=Path(name).suffix.lower()
        if ext not in ALLOWED:
            flash('Formato inválido. Use PNG, JPG ou WebP.','error');return redirect(url_for('assets.assets'))
        file.save(ASSETS_DIR/name);flash('Asset salvo.','success');return redirect(url_for('assets.assets'))
    return render_template('assets.html',assets=list_assets())

@assets_bp.route('/professional/assets/<path:name>/delete',methods=['POST'])
@require_role('operator')
def delete(name):
    p=ASSETS_DIR/Path(name).name
    if p.exists() and p.is_file():p.unlink()
    flash('Asset removido.','success');return redirect(url_for('assets.assets'))
