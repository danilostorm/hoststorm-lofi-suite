from __future__ import annotations

import os
from functools import wraps

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for

from .pro_db import get_user, get_user_by_username, list_users, save_user, set_totp, touch_login, user_totp_secret
from .security import LOGIN_LIMITER, new_totp_secret, role_allows, totp_uri, verify_password, verify_totp

auth_bp=Blueprint('auth',__name__)

PUBLIC_ENDPOINTS={'auth.login','web.healthz','static'}
PUBLIC_WEBHOOK_PATHS={'/api/ai/kick/webhook','/api/kick/webhook'}

@auth_bp.before_app_request
def load_identity():
    if request.path.startswith('/static/') or request.endpoint in PUBLIC_ENDPOINTS:
        return None
    # Webhooks da plataforma precisam chegar sem cookie de sessão. A rota do AI Host valida
    # criptograficamente a assinatura oficial antes de aceitar qualquer payload.
    if request.method=='POST' and request.path in PUBLIC_WEBHOOK_PATHS:
        return None
    uid=session.get('uid')
    user=get_user(uid) if uid else None
    if user and user.get('enabled'):
        g.user=user
        return None
    # API bearer authentication is resolved in pro_web to support scoped tokens.
    if request.path.startswith('/api/v1/'):
        return None
    return redirect(url_for('auth.login',next=request.full_path if request.method=='GET' else request.referrer or '/'))


def require_role(minimum='viewer'):
    def deco(fn):
        @wraps(fn)
        def inner(*args,**kwargs):
            user=getattr(g,'user',None)
            if not user: abort(401)
            if not role_allows(user.get('role'),minimum): abort(403)
            return fn(*args,**kwargs)
        return inner
    return deco

@auth_bp.route('/login',methods=['GET','POST'])
def login():
    if request.method=='GET':
        return render_template('login.html')
    ip=request.headers.get('X-Forwarded-For',request.remote_addr or '').split(',')[0].strip()
    key=f'{ip}:{request.form.get("username","").lower()}'
    if not LOGIN_LIMITER.allowed(key):
        flash('Muitas tentativas. Tente novamente em alguns minutos.','error'); return render_template('login.html'),429
    user=get_user_by_username(request.form.get('username','').strip())
    password=request.form.get('password','')
    if not user or not user.get('enabled') or not verify_password(user.get('password_hash',''),password):
        LOGIN_LIMITER.fail(key); flash('Usuário ou senha inválidos.','error'); return render_template('login.html'),401
    if user.get('totp_enabled'):
        code=request.form.get('totp','')
        if not verify_totp(user_totp_secret(user),code):
            LOGIN_LIMITER.fail(key); flash('Código 2FA inválido.','error'); return render_template('login.html'),401
    LOGIN_LIMITER.success(key); session.clear(); session['uid']=user['id']; session.permanent=True; touch_login(user['id'])
    nxt=request.args.get('next') or '/'
    return redirect(nxt if nxt.startswith('/') and not nxt.startswith('//') else '/')

@auth_bp.route('/logout',methods=['POST','GET'])
def logout():
    session.clear(); return redirect(url_for('auth.login'))

@auth_bp.route('/security/users')
@require_role('admin')
def users():
    return render_template('users.html',users=list_users())

@auth_bp.route('/security/users/save',methods=['POST'])
@require_role('admin')
def user_save():
    uid=request.form.get('id') or None
    try:
        save_user(request.form.get('username','').strip(),request.form.get('role','viewer'),request.form.get('password',''),uid,request.form.get('enabled')=='on')
        flash('Usuário salvo.','success')
    except Exception as e: flash(str(e),'error')
    return redirect(url_for('auth.users'))

@auth_bp.route('/security/2fa',methods=['GET','POST'])
@require_role('viewer')
def two_factor():
    user=get_user(g.user['id'])
    secret=session.get('pending_totp')
    if request.method=='POST':
        action=request.form.get('action')
        if action=='begin':
            secret=new_totp_secret(); session['pending_totp']=secret
        elif action=='enable':
            secret=session.get('pending_totp','')
            if secret and verify_totp(secret,request.form.get('code','')):
                set_totp(user['id'],secret,True); session.pop('pending_totp',None); flash('2FA ativado.','success')
                return redirect(url_for('auth.two_factor'))
            flash('Código inválido.','error')
        elif action=='disable':
            if verify_password(user['password_hash'],request.form.get('password','')):
                set_totp(user['id'],'',False); session.pop('pending_totp',None); flash('2FA desativado.','success')
            else: flash('Senha inválida.','error')
    uri=totp_uri(secret,user['username']) if secret else ''
    qr=''
    if uri:
        try:
            import io,base64,qrcode
            b=io.BytesIO(); qrcode.make(uri).save(b,format='PNG'); qr='data:image/png;base64,'+base64.b64encode(b.getvalue()).decode()
        except Exception: pass
    return render_template('two_factor.html',user=get_user(user['id']),secret=secret or '',uri=uri,qr=qr)
