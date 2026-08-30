from __future__ import annotations

import hmac
import json
import os
import queue
import time
from pathlib import Path

import psutil
from flask import Blueprint, Response, flash, jsonify, redirect, render_template, request, send_from_directory, stream_with_context, url_for
from werkzeug.utils import secure_filename

from .config import (
    ADMIN_USER, ADMIN_PASSWORD, VIDEOS_DIR, AUDIOS_DIR, VIDEO_EXTENSIONS, AUDIO_EXTENSIONS,
    LOGS_DIR, WEEKDAY_LABELS, MAX_UPLOAD_GB, DEFAULT_DESTINATIONS, DEFAULT_CHANNEL_SETTINGS,
)
from .db import (
    audit, create_channel, delete_channel, delete_schedule, get_channel, get_schedule, get_setting,
    list_audit, list_channels, list_history, list_schedules, save_channel, save_schedule, set_setting,
    update_schedule_status,
)
from .events import BUS
from .media import delete_media, ffprobe_meta, list_files, scan_library
from .notifications import notify
from .scheduler import due_info
from .streaming import MANAGER
from .system import system_info, version
from .utils import datetime_br, duration_hms, now_br, now_dt

bp=Blueprint('web',__name__)


def _auth_ok():
    auth=request.authorization
    if not ADMIN_PASSWORD:
        return True
    return bool(auth and hmac.compare_digest(auth.username or '',ADMIN_USER) and hmac.compare_digest(auth.password or '',ADMIN_PASSWORD))

@bp.before_app_request
def auth():
    if request.path=='/healthz': return None
    if not _auth_ok():
        return Response('Login obrigatório.\n',401,{'WWW-Authenticate':'Basic realm="HostStorm Admin"'})


def _status_channels():
    channels=list_channels()
    for cid,ch in channels.items():
        st=MANAGER.channel_status(cid)
        ch['runtime']=st
        ch['running']=st.get('running',False)
        ch['active_platforms']=[slug for slug,s in st.get('platforms',{}).items() if s.get('running')]
    return channels


def _masked_key(value):
    value=str(value or '')
    if len(value)<=6: return '••••••' if value else ''
    return value[:3]+'••••••'+value[-3:]


def _calendar(schedules):
    days=[[] for _ in range(7)]; special=[]
    for s in schedules:
        kind=s.get('kind')
        if kind=='weekly':
            for d in s.get('weekdays') or []:
                if 0<=int(d)<=6: days[int(d)].append(s)
        elif kind=='daily':
            for d in range(7): days[d].append(s)
        elif kind=='weekdays':
            for d in range(5): days[d].append(s)
        else:
            special.append(s)
    for arr in days: arr.sort(key=lambda x:x.get('time',''))
    return days,special

@bp.app_template_filter('duration_hms')
def _duration(value): return duration_hms(value)

@bp.app_template_filter('datetime_br')
def _dt(value): return datetime_br(value)

@bp.app_template_filter('filesize')
def filesize(value):
    try: n=float(value or 0)
    except Exception: n=0
    units=['B','KB','MB','GB','TB']; i=0
    while n>=1024 and i<len(units)-1: n/=1024; i+=1
    return f'{n:.1f} {units[i]}'

@bp.app_context_processor
def globals_ctx():
    return {'app_version':version(),'weekday_labels':WEEKDAY_LABELS,'now_br':now_br()}

@bp.route('/healthz')
def healthz(): return jsonify({'ok':True,'version':version()})

@bp.route('/')
def dashboard():
    channels=_status_channels(); schedules=list_schedules(); history=list_history(20)
    running=sum(1 for c in channels.values() if c['running'])
    today=now_dt().date(); due_today=[]
    for s in schedules:
        # Build today's candidate even if not due yet.
        if not s.get('enabled'): continue
        wd=now_dt().weekday(); kind=s.get('kind')
        applies=(kind=='daily' or (kind=='weekdays' and wd<5) or (kind=='weekly' and wd in (s.get('weekdays') or [])) or (kind=='once' and s.get('run_date')==today.isoformat()))
        if applies: due_today.append(s)
    due_today.sort(key=lambda x:x.get('time',''))
    next_schedule=None
    current_hm=now_dt().strftime('%H:%M')
    for s in due_today:
        if s.get('time','')>=current_hm: next_schedule=s; break
    failures=sum(1 for h in history if h.get('status') in {'failed','error'})
    return render_template('dashboard.html',channels=channels,schedules=schedules,due_today=due_today,next_schedule=next_schedule,history=history,
                           stats={'running':running,'channels':len(channels),'schedules':sum(1 for x in schedules if x.get('enabled')),'failures':failures,'cpu':psutil.cpu_percent(interval=.05),'ram':psutil.virtual_memory().percent,'disk':psutil.disk_usage('/').percent})

@bp.route('/lives')
def lives(): return render_template('lives.html',channels=_status_channels())

@bp.route('/lives/create',methods=['POST'])
def live_create():
    cid=create_channel(request.form.get('name','').strip() or 'Nova live')
    flash('Canal criado.','success')
    return redirect(url_for('web.live_edit',cid=cid))

@bp.route('/lives/<cid>')
def live_edit(cid):
    ch=get_channel(cid)
    if not ch: return redirect(url_for('web.lives'))
    ch['runtime']=MANAGER.channel_status(cid)
    for d in ch['destinations'].values(): d['masked_key']=_masked_key(d.get('stream_key'))
    return render_template('live_edit.html',ch=ch,videos=list_files('video'),audios=list_files('audio'))

@bp.route('/lives/<cid>/save',methods=['POST'])
def live_save(cid):
    ch=get_channel(cid)
    if not ch: return redirect(url_for('web.lives'))
    reserved={'id','name','desired_running','created_at','updated_at','destinations','schedules','runtime','running','active_platforms'}
    settings={k:v for k,v in ch.items() if k not in reserved}
    for key,default in DEFAULT_CHANNEL_SETTINGS.items():
        settings[key]=request.form.get(key,ch.get(key,default))
    destinations={}
    for slug,old in ch['destinations'].items():
        destinations[slug]=dict(old)
        destinations[slug]['enabled']=request.form.get(f'{slug}_enabled')=='on'
        destinations[slug]['rtmp_url']=request.form.get(f'{slug}_rtmp_url',old.get('rtmp_url','')).strip()
        new_key=request.form.get(f'{slug}_stream_key','').strip()
        destinations[slug]['stream_key']=new_key if new_key else old.get('stream_key','')
        destinations[slug]['mode']=request.form.get(f'{slug}_mode',old.get('mode','horizontal'))
        destinations[slug]['dedicated']=True
    save_channel(cid,request.form.get('name',ch['name']).strip() or ch['name'],settings,destinations)
    flash('Configurações salvas.','success')
    return redirect(url_for('web.live_edit',cid=cid))

@bp.route('/lives/<cid>/start',methods=['POST'])
def live_start(cid):
    ok,msg=MANAGER.start(cid,trigger='manual'); flash(msg,'success' if ok else 'error')
    return redirect(request.referrer or url_for('web.lives'))

@bp.route('/lives/<cid>/stop',methods=['POST'])
def live_stop(cid):
    ok,msg=MANAGER.stop(cid,'parada manual'); flash(msg,'success' if ok else 'error')
    return redirect(request.referrer or url_for('web.lives'))

@bp.route('/lives/<cid>/delete',methods=['POST'])
def live_delete(cid):
    MANAGER.stop(cid,'canal removido'); delete_channel(cid); flash('Canal removido.','success')
    return redirect(url_for('web.lives'))

@bp.route('/api/preflight/<cid>',methods=['POST'])
def api_preflight(cid):
    payload=request.get_json(silent=True) or {}
    return jsonify(MANAGER.preflight(cid,payload.get('platforms'),payload.get('media')))

@bp.route('/schedules')
def schedules():
    schedules=list_schedules(); days,special=_calendar(schedules); channels=list_channels(False)
    return render_template('schedules.html',schedules=schedules,days=days,special=special,channels=channels)

@bp.route('/schedules/new')
def schedule_new():
    cid=request.args.get('channel',''); channels=list_channels(False)
    if not cid and channels: cid=next(iter(channels))
    return render_template('schedule_edit.html',schedule=None,channels=channels,selected_channel=cid,videos=list_files('video'))

@bp.route('/schedules/<sid>/edit')
def schedule_edit(sid):
    s=get_schedule(sid)
    if not s: return redirect(url_for('web.schedules'))
    return render_template('schedule_edit.html',schedule=s,channels=list_channels(False),selected_channel=s['channel_id'],videos=list_files('video'))

@bp.route('/schedules/save',methods=['POST'])
def schedule_save():
    cid=request.form.get('channel_id','')
    ch=get_channel(cid,False)
    if not ch:
        flash('Canal inválido.','error'); return redirect(url_for('web.schedules'))
    media=request.form.getlist('media')
    platforms=request.form.getlist('platforms')
    data={
        'id':request.form.get('id','') or None,'channel_id':cid,'name':request.form.get('name','').strip(),
        'kind':request.form.get('kind','weekly'),'weekdays':request.form.getlist('weekdays'),'time':request.form.get('time',''),
        'run_date':request.form.get('run_date',''),'start_date':request.form.get('start_date',''),'end_date':request.form.get('end_date',''),
        'enabled':request.form.get('enabled')=='on','conflict_policy':request.form.get('conflict_policy','skip'),
        'stop_before_seconds':int(request.form.get('stop_before_seconds','60') or 60),'platforms':platforms,'media':media,
        'shuffle':request.form.get('shuffle')=='on','repeat_playlist':request.form.get('repeat_playlist')=='on',
        'max_duration_minutes':int(request.form.get('max_duration_minutes','0') or 0),
    }
    try:
        sid=save_schedule(data); flash('Agendamento salvo.','success')
        if request.form.get('run_preflight')=='1':
            result=MANAGER.preflight(cid,platforms,media)
            flash('Pré-teste: '+('OK' if result['ok'] else 'há pendências'),'success' if result['ok'] else 'error')
        return redirect(url_for('web.schedule_edit',sid=sid))
    except Exception as e:
        flash(str(e),'error'); return redirect(request.referrer or url_for('web.schedules'))

@bp.route('/schedules/<sid>/toggle',methods=['POST'])
def schedule_toggle(sid):
    s=get_schedule(sid)
    if s: update_schedule_status(sid,enabled=not s.get('enabled'),last_status='Ativado.' if not s.get('enabled') else 'Pausado.')
    return redirect(request.referrer or url_for('web.schedules'))

@bp.route('/schedules/<sid>/delete',methods=['POST'])
def schedule_delete(sid):
    delete_schedule(sid); flash('Agendamento removido.','success'); return redirect(url_for('web.schedules'))

@bp.route('/schedules/<sid>/run',methods=['POST'])
def schedule_run_now(sid):
    s=get_schedule(sid)
    if not s: flash('Agenda não encontrada.','error')
    else:
        st=MANAGER.channel_status(s['channel_id'])
        if st.get('running') and s.get('conflict_policy')=='stop_current': MANAGER.stop(s['channel_id'],'substituída por execução manual da agenda')
        ok,msg=MANAGER.start(s['channel_id'],s['platforms'],s['media'],'scheduled',s); flash(msg,'success' if ok else 'error')
    return redirect(request.referrer or url_for('web.schedules'))

@bp.route('/library')
def library():
    compute=request.args.get('duplicates')=='1'
    items=scan_library(compute)
    dupes={}
    if compute:
        groups={}
        for m in items:
            if m.get('sha256'): groups.setdefault(m['sha256'],[]).append(m['filename'])
        for sha,names in groups.items():
            if len(names)>1:
                for name in names: dupes[name]=[x for x in names if x!=name]
    return render_template('library.html',items=items,dupes=dupes,duplicates_scanned=compute)

@bp.route('/library/upload',methods=['POST'])
def library_upload():
    files=request.files.getlist('files'); count=0
    for file in files:
        if not file or not file.filename: continue
        name=secure_filename(file.filename); ext=Path(name).suffix.lower()
        if ext in VIDEO_EXTENSIONS: file.save(VIDEOS_DIR/name); count+=1
        elif ext in AUDIO_EXTENSIONS: file.save(AUDIOS_DIR/name); count+=1
    flash(f'{count} arquivo(s) enviado(s).','success')
    return redirect(url_for('web.library'))

@bp.route('/library/<kind>/<path:filename>/meta')
def library_meta(kind,filename):
    try: return jsonify(ffprobe_meta(kind,filename,compute_hash=request.args.get('hash')=='1'))
    except Exception as e: return jsonify({'error':str(e)}),400

@bp.route('/library/<kind>/<path:filename>/delete',methods=['POST'])
def library_delete(kind,filename):
    ok,msg=delete_media(kind,filename); flash(msg,'success' if ok else 'error'); return redirect(url_for('web.library'))

@bp.route('/library/<kind>/<path:filename>/file')
def library_file(kind,filename):
    folder=VIDEOS_DIR if kind=='video' else AUDIOS_DIR
    return send_from_directory(folder,os.path.basename(filename),conditional=True)

@bp.route('/history')
def history(): return render_template('history.html',history=list_history(500))

@bp.route('/logs')
def logs():
    cid=request.args.get('channel',''); channels=list_channels(False); text=''
    if cid:
        p=LOGS_DIR/f'{cid}.log'
        if p.exists(): text=p.read_text(encoding='utf-8',errors='replace')[-60000:]
    return render_template('logs.html',channels=channels,selected=cid,log_text=text,audit=list_audit(200))

@bp.route('/logs/<cid>/clear',methods=['POST'])
def log_clear(cid):
    (LOGS_DIR/f'{cid}.log').write_text('',encoding='utf-8'); return redirect(url_for('web.logs',channel=cid))

@bp.route('/settings',methods=['GET','POST'])
def settings():
    keys=['notifications_enabled','notify_discord_webhook','notify_webhook','notify_telegram_token','notify_telegram_chat_id']
    if request.method=='POST':
        for k in keys:
            set_setting(k,'1' if k=='notifications_enabled' and request.form.get(k)=='on' else (request.form.get(k,'') if k!='notifications_enabled' else '0'))
        flash('Configurações salvas.','success'); return redirect(url_for('web.settings'))
    vals={k:get_setting(k,'') for k in keys}
    return render_template('settings.html',settings=vals,system=system_info())

@bp.route('/settings/test-notification',methods=['POST'])
def test_notification():
    notify('✅ HostStorm Multi Live Manager: notificação de teste.')
    flash('Notificação de teste disparada.','success'); return redirect(url_for('web.settings'))

@bp.route('/api/status')
def api_status():
    channels=_status_channels()
    return jsonify({'at':now_dt().isoformat(),'channels':{cid:{'name':c['name'],**c['runtime']} for cid,c in channels.items()},'cpu':psutil.cpu_percent(interval=.01),'ram':psutil.virtual_memory().percent,'disk':psutil.disk_usage('/').percent})

@bp.route('/api/events')
def api_events():
    q=BUS.subscribe()
    @stream_with_context
    def gen():
        try:
            yield 'retry: 3000\n\n'
            while True:
                try:
                    event=q.get(timeout=20)
                    yield f"event: {event['type']}\ndata: {json.dumps(event,ensure_ascii=False)}\n\n"
                except queue.Empty:
                    yield ': ping\n\n'
        finally:
            BUS.unsubscribe(q)
    return Response(gen(),mimetype='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})
