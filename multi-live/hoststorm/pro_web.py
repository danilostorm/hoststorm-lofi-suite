from __future__ import annotations

import json
import os
import threading
from functools import wraps

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for

from .auth import require_role
from .broadcast import delete_block, list_blocks, save_block
from .clips import create_clip, list_recordings
from .db import get_channel, list_channels, list_history, list_schedules
from .distributed import upsert_snapshot
from .integrations import check_integration, delete_integration, list_integrations, save_integration
from .media import list_files, scan_library
from .pro_db import (
    ack_alert, add_alert, add_marker, authenticate_token, choose_node, create_token, list_alerts,
    list_backups, list_markers, list_nodes, list_profiles, list_tokens, recent_metrics, save_node,
    save_profile, update_node_health,
)
from .professional import cleanup, create_backup, diagnose, encoder_capabilities, import_url, restore_backup, system_snapshot, watch_scan
from .security import new_api_token, role_allows
from .streaming import MANAGER
from .system import version
from .utils import now_dt

pro_bp=Blueprint('pro',__name__)

@pro_bp.app_context_processor
def professional_globals():
    try:return {'pro_profiles':list_profiles(),'encoder_caps':encoder_capabilities()}
    except Exception:return {'pro_profiles':[],'encoder_caps':{}}


def _bearer():
    h=request.headers.get('Authorization','');return h[7:].strip() if h.lower().startswith('bearer ') else ''

def api_required(scope='read'):
    def deco(fn):
        @wraps(fn)
        def inner(*args,**kwargs):
            if getattr(g,'user',None):
                if not role_allows(g.user.get('role'),'operator' if scope in {'write','control','agent'} else 'viewer'):abort(403)
                g.api_identity={'type':'session','username':g.user['username'],'role':g.user['role'],'scopes':['*']};return fn(*args,**kwargs)
            token=authenticate_token(_bearer())
            if not token:abort(401)
            scopes=set(token.get('scopes') or [])
            if '*' not in scopes and scope not in scopes:abort(403)
            g.api_identity=token;return fn(*args,**kwargs)
        return inner
    return deco

def _masked_channels():
    channels=list_channels(False)
    for ch in channels.values():
        for d in (ch.get('destinations') or {}).values():
            if d.get('stream_key'):d['stream_key']='••••••'
    return channels

@pro_bp.route('/professional')
@require_role('viewer')
def center():
    snap=system_snapshot();alerts=list_alerts(10,True);nodes=list_nodes();profiles=list_profiles();channels=list_channels(False);statuses={cid:MANAGER.channel_status(cid) for cid in channels}
    return render_template('professional.html',snap=snap,alerts=alerts,nodes=nodes,profiles=profiles,channels=channels,statuses=statuses)

@pro_bp.route('/professional/profiles',methods=['GET','POST'])
@require_role('operator')
def profiles():
    if request.method=='POST':
        try:
            save_profile({'id':request.form.get('id') or None,'name':request.form.get('name','').strip(),'platform':request.form.get('platform','custom'),'width':request.form.get('width',1920),'height':request.form.get('height',1080),'fps':request.form.get('fps',30),'video_bitrate_k':request.form.get('video_bitrate_k',4500),'audio_bitrate_k':request.form.get('audio_bitrate_k',160),'encoder':request.form.get('encoder','auto'),'preset':request.form.get('preset','veryfast')});flash('Perfil salvo.','success')
        except Exception as e:flash(str(e),'error')
        return redirect(url_for('pro.profiles'))
    return render_template('profiles.html',profiles=list_profiles(),caps=encoder_capabilities())

@pro_bp.route('/professional/nodes',methods=['GET','POST'])
@require_role('admin')
def nodes():
    if request.method=='POST':
        try:
            save_node({'id':request.form.get('id') or None,'name':request.form.get('name','').strip(),'base_url':request.form.get('base_url','').strip(),'token':request.form.get('token','').strip(),'priority':request.form.get('priority',100),'enabled':request.form.get('enabled')=='on','tags':[x.strip() for x in request.form.get('tags','').split(',') if x.strip()]});flash('Servidor salvo.','success')
        except Exception as e:flash(str(e),'error')
        return redirect(url_for('pro.nodes'))
    return render_template('nodes.html',nodes=list_nodes(),best=choose_node())

@pro_bp.route('/professional/diagnostics')
@require_role('operator')
def diagnostics():return render_template('diagnostics.html',report=diagnose())

@pro_bp.route('/professional/retention',methods=['POST'])
@require_role('admin')
def retention_run():
    removed=cleanup(int(request.form.get('log_days',30)),int(request.form.get('backup_keep',20)),int(request.form.get('recording_days',30)),int(request.form.get('clip_days',60)));flash(f'Limpeza concluída: {len(removed)} arquivo(s) removido(s).','success');return redirect(url_for('pro.diagnostics'))

@pro_bp.route('/professional/backups',methods=['GET','POST'])
@require_role('admin')
def backups():
    if request.method=='POST':p=create_backup('manual');flash('Backup criado: '+p.name,'success');return redirect(url_for('pro.backups'))
    return render_template('backups.html',backups=list_backups())

@pro_bp.route('/professional/backups/restore',methods=['POST'])
@require_role('admin')
def backup_restore():
    try:before=restore_backup(request.form.get('name',''));flash('Backup restaurado. Snapshot anterior: '+before.name+'. Reinicie o serviço para recarregar tudo.','success')
    except Exception as e:flash(str(e),'error')
    return redirect(url_for('pro.backups'))

@pro_bp.route('/professional/alerts')
@require_role('viewer')
def alerts():return render_template('alerts.html',alerts=list_alerts(500))

@pro_bp.route('/professional/alerts/<aid>/ack',methods=['POST'])
@require_role('operator')
def alert_ack(aid):ack_alert(aid);return redirect(request.referrer or url_for('pro.alerts'))

@pro_bp.route('/professional/analytics')
@require_role('viewer')
def analytics():
    hist=list_history(2000);metrics=recent_metrics(2000);total=len(hist);success=sum(1 for h in hist if h.get('status') in {'finished','running'});failures=sum(1 for h in hist if h.get('status') in {'failed','error'});per_platform={}
    for h in hist:
        for p in h.get('platforms',[]) if isinstance(h.get('platforms'),list) else []:per_platform[p]=per_platform.get(p,0)+1
    return render_template('analytics.html',stats={'total':total,'success':success,'failures':failures,'rate':round(success*100/max(1,total),1)},per_platform=per_platform,metrics=metrics[:100])

@pro_bp.route('/professional/noc')
@require_role('viewer')
def noc():
    channels=list_channels(False);return render_template('noc.html',channels=channels,statuses={cid:MANAGER.channel_status(cid) for cid in channels},nodes=list_nodes())

@pro_bp.route('/professional/library/import',methods=['POST'])
@require_role('operator')
def library_import():
    source=request.form.get('url','');kind=request.form.get('kind','video')
    def work():
        try:import_url(source,kind);add_alert('info','import','Importação concluída',source)
        except Exception as e:add_alert('error','import','Falha na importação',str(e))
    threading.Thread(target=work,daemon=True).start();flash('Importação iniciada em segundo plano.','success');return redirect(url_for('web.library'))

@pro_bp.route('/professional/library/watch-scan',methods=['POST'])
@require_role('operator')
def watch_folder_scan():
    moved=watch_scan();flash(f'{len(moved)} arquivo(s) importado(s) da watch folder.','success');return redirect(url_for('web.library'))

@pro_bp.route('/professional/tokens',methods=['GET','POST'])
@require_role('admin')
def tokens():
    issued=''
    if request.method=='POST':issued=new_api_token();create_token(g.user['id'],request.form.get('name','API'),issued,request.form.getlist('scopes') or ['read']);flash('Token criado. Copie agora; ele não será mostrado novamente.','success')
    return render_template('tokens.html',tokens=list_tokens(),issued=issued)

@pro_bp.route('/professional/markers')
@require_role('viewer')
def markers():return render_template('markers.html',markers=list_markers())

@pro_bp.route('/professional/markers/<mid>/clip',methods=['POST'])
@require_role('operator')
def marker_clip(mid):
    try:out=create_clip(mid,int(request.form.get('before',15)),int(request.form.get('after',45)));flash('Clipe criado: '+out.name,'success')
    except Exception as e:flash(str(e),'error')
    return redirect(url_for('pro.markers'))

@pro_bp.route('/professional/recordings')
@require_role('viewer')
def recordings():return render_template('recordings.html',recordings=list_recordings())

@pro_bp.route('/professional/broadcast',methods=['GET','POST'])
@require_role('operator')
def broadcast():
    if request.method=='POST':
        try:
            save_block({'id':request.form.get('id') or None,'channel_id':request.form.get('channel_id',''),'name':request.form.get('name','').strip(),'start_time':request.form.get('start_time',''),'end_time':request.form.get('end_time',''),'weekdays':request.form.getlist('weekdays'),'media':request.form.getlist('media'),'bumper_before':request.form.get('bumper_before',''),'bumper_after':request.form.get('bumper_after',''),'commercial_interval_minutes':request.form.get('commercial_interval_minutes',0),'commercial_media':request.form.getlist('commercial_media'),'enabled':request.form.get('enabled')=='on'});flash('Bloco salvo na grade.','success')
        except Exception as e:flash(str(e),'error')
        return redirect(url_for('pro.broadcast'))
    blocks=list_blocks();by_day=[[] for _ in range(7)]
    for b in blocks:
        for d in b.get('weekdays',[]):by_day[int(d)].append(b)
    for arr in by_day:arr.sort(key=lambda b:b['start_time'])
    return render_template('broadcast.html',blocks=blocks,blocks_by_day=by_day,channels=list_channels(False),videos=list_files('video'))

@pro_bp.route('/professional/broadcast/<bid>/delete',methods=['POST'])
@require_role('operator')
def broadcast_delete(bid):delete_block(bid);flash('Bloco removido.','success');return redirect(url_for('pro.broadcast'))

@pro_bp.route('/professional/broadcast/autofill',methods=['POST'])
@require_role('operator')
def broadcast_autofill():
    videos=list_files('video');prefix=request.form.get('prefix','').strip().lower()
    if prefix:videos=[v for v in videos if v.lower().startswith(prefix)]
    try:
        save_block({'channel_id':request.form.get('channel_id',''),'name':request.form.get('name','Programação automática'),'start_time':request.form.get('start_time','08:00'),'end_time':request.form.get('end_time','23:00'),'weekdays':request.form.getlist('weekdays') or list(range(7)),'media':videos,'commercial_interval_minutes':request.form.get('commercial_interval_minutes',0),'commercial_media':request.form.getlist('commercial_media'),'enabled':True});flash(f'Grade automática criada com {len(videos)} vídeo(s), usando rotação anti-repetição.','success')
    except Exception as e:flash(str(e),'error')
    return redirect(url_for('pro.broadcast'))

@pro_bp.route('/professional/integrations',methods=['GET','POST'])
@require_role('admin')
def integrations():
    if request.method=='POST':
        provider=request.form.get('provider','');config={'client_id':request.form.get('client_id',''),'access_token':request.form.get('access_token',''),'channel_login':request.form.get('channel_login',''),'api_key':request.form.get('api_key',''),'channel_id':request.form.get('channel_id','')}
        try:save_integration(provider,request.form.get('name','').strip(),config,request.form.get('enabled')=='on');flash('Integração salva.','success')
        except Exception as e:flash(str(e),'error')
        return redirect(url_for('pro.integrations'))
    return render_template('integrations.html',integrations=list_integrations())

@pro_bp.route('/professional/integrations/<iid>/check',methods=['POST'])
@require_role('operator')
def integration_check(iid):
    r=check_integration(iid);flash(r.get('message',''), 'success' if r.get('ok') else 'error');return redirect(url_for('pro.integrations'))

@pro_bp.route('/professional/integrations/<iid>/delete',methods=['POST'])
@require_role('admin')
def integration_delete(iid):delete_integration(iid);flash('Integração removida.','success');return redirect(url_for('pro.integrations'))

@pro_bp.route('/professional/update')
@require_role('admin')
def updater():
    channel=os.environ.get('HOSTSTORM_UPDATE_CHANNEL','stable');return render_template('updater.html',version=version(),channel=channel,git_sha=os.environ.get('HOSTSTORM_GIT_SHA',''),note='Atualizações são aplicadas pelo host usando scripts/update.sh; esta tela acompanha canal e versão para evitar auto-update inseguro dentro do container.')

# REST API v1
@pro_bp.route('/api/v1/status')
@api_required('read')
def api_status():
    channels=list_channels(False);return jsonify({'ok':True,'version':version(),'at':now_dt().isoformat(),'system':system_snapshot(),'channels':{cid:{'name':c['name'],**MANAGER.channel_status(cid)} for cid,c in channels.items()}})

@pro_bp.route('/api/v1/lives/<cid>/start',methods=['POST'])
@api_required('control')
def api_start(cid):
    body=request.get_json(silent=True) or {};ok,msg=MANAGER.start(cid,body.get('platforms'),body.get('media'),body.get('trigger','manual'),body.get('schedule'));return jsonify({'ok':ok,'message':msg}),200 if ok else 400

@pro_bp.route('/api/v1/lives/<cid>/stop',methods=['POST'])
@api_required('control')
def api_stop(cid):
    ok,msg=MANAGER.stop(cid,(request.get_json(silent=True) or {}).get('reason','API'));return jsonify({'ok':ok,'message':msg}),200 if ok else 400

@pro_bp.route('/api/v1/channels')
@api_required('read')
def api_channels():return jsonify({'channels':_masked_channels()})

@pro_bp.route('/api/v1/schedules')
@api_required('read')
def api_schedules():return jsonify({'schedules':list_schedules()})

@pro_bp.route('/api/v1/library')
@api_required('read')
def api_library():return jsonify({'items':scan_library(False)})

@pro_bp.route('/api/v1/diagnostics')
@api_required('read')
def api_diagnostics():return jsonify(diagnose())

@pro_bp.route('/api/v1/nodes/heartbeat',methods=['POST'])
@api_required('agent')
def api_node_heartbeat():
    body=request.get_json(silent=True) or {};nid=body.get('node_id','')
    if not nid:return jsonify({'ok':False,'error':'node_id obrigatório'}),400
    update_node_health(nid,float(body.get('cpu',0)),float(body.get('ram',0)),float(body.get('gpu',0)),int(body.get('active_streams',0)),'online');return jsonify({'ok':True,'controller_time':now_dt().isoformat()})

@pro_bp.route('/api/v1/agent/run',methods=['POST'])
@api_required('agent')
def api_agent_run():
    body=request.get_json(silent=True) or {};ch=dict(body.get('channel') or {})
    if not ch:return jsonify({'ok':False,'error':'snapshot do canal obrigatório'}),400
    ch['node_mode']='local';ch['node_id']='';cid=upsert_snapshot(ch);ok,msg=MANAGER.start(cid,body.get('platforms'),body.get('media'),body.get('trigger','manual'),body.get('schedule'));return jsonify({'ok':ok,'message':msg,'channel_id':cid}),200 if ok else 400

@pro_bp.route('/api/v1/markers/<cid>',methods=['POST'])
@api_required('control')
def api_marker(cid):
    st=MANAGER.channel_status(cid)
    if not st.get('running'):return jsonify({'ok':False,'error':'canal não está ao vivo'}),409
    import datetime
    started=st.get('started_at','');offset=0
    try:offset=max(0,(now_dt()-datetime.datetime.fromisoformat(started)).total_seconds())
    except Exception:pass
    mid=add_marker(st.get('run_id',''),cid,offset,(request.get_json(silent=True) or {}).get('label',''));return jsonify({'ok':True,'marker_id':mid,'offset_seconds':offset})
