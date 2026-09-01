from __future__ import annotations

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from .ai_chat import CHAT_HUB, ingest_kick_webhook, sync_kick_subscriptions
from .ai_db import (
    delete_provider, get_settings, list_bindings, list_event_subscriptions, list_personas,
    list_providers, list_responses, list_viewers, recent_messages, save_binding, save_persona,
    save_provider, save_settings, stats,
)
from .ai_host import AI_HOST
from .ai_providers import test_provider
from .auth import require_role
from .db import list_channels
from .integrations import list_integrations

ai_bp=Blueprint('ai',__name__)


def _accounts():
    return [x for x in list_integrations(mask=True) if x.get('provider') in {'kick','twitch','youtube'}]


def _binding_map():return {x['integration_id']:x for x in list_bindings()}


@ai_bp.route('/ai')
@require_role('viewer')
def overview():
    return render_template('ai_overview.html',settings=get_settings(),stats=stats(),host_status=AI_HOST.status(),accounts=_accounts(),bindings=_binding_map(),responses=list_responses(12),messages=recent_messages(18))


@ai_bp.route('/ai/chat')
@require_role('viewer')
def chat():
    return render_template('ai_chat.html',settings=get_settings(),responses=list_responses(120),messages=recent_messages(160),host_status=AI_HOST.status())


@ai_bp.route('/ai/persona',methods=['GET','POST'])
@require_role('operator')
def persona():
    if request.method=='POST':
        try:
            save_persona({'id':request.form.get('id') or None,'name':request.form.get('name','').strip(),'description':request.form.get('description','').strip(),'system_prompt':request.form.get('system_prompt','').strip(),'enabled':request.form.get('enabled')=='on','style':{'humor':float(request.form.get('humor',.65)),'sarcasm':float(request.form.get('sarcasm',.25)),'competitive':float(request.form.get('competitive',.55)),'informal':float(request.form.get('informal',.9))}});flash('Persona salva.','success')
        except Exception as exc:flash(str(exc),'error')
        return redirect(url_for('ai.persona'))
    return render_template('ai_persona.html',personas=list_personas(),settings=get_settings())


@ai_bp.route('/ai/memory')
@require_role('viewer')
def memory():return render_template('ai_memory.html',viewers=list_viewers(400),messages=recent_messages(60),settings=get_settings())


@ai_bp.route('/ai/settings',methods=['GET','POST'])
@require_role('operator')
def settings():
    if request.method=='POST':
        try:
            bools={'enabled','cross_platform_context','memory_enabled','reply_questions','reply_mentions','reply_jokes','reply_greetings','reply_events','prompt_injection_filter','links_filter','tts_enabled','vision_enabled'}
            data={k:(request.form.get(k)=='on') for k in bools}
            data.update({
                'mode':'autopilot' if request.form.get('mode')=='autopilot' else 'copilot',
                'persona_id':request.form.get('persona_id',''), 'llm_provider_id':request.form.get('llm_provider_id',''), 'tts_provider_id':request.form.get('tts_provider_id',''),
                'window_min_seconds':request.form.get('window_min_seconds',15),'window_max_seconds':request.form.get('window_max_seconds',30),'responses_per_hour':request.form.get('responses_per_hour',20),'per_user_cooldown_seconds':request.form.get('per_user_cooldown_seconds',180),'global_min_gap_seconds':request.form.get('global_min_gap_seconds',12),'send_delay_min_seconds':request.form.get('send_delay_min_seconds',2),'send_delay_max_seconds':request.form.get('send_delay_max_seconds',7),'max_reply_chars':request.form.get('max_reply_chars',240),'ai_signature':request.form.get('ai_signature',' 🤖'),
                'question_probability':float(request.form.get('question_probability',72))/100,'mention_probability':float(request.form.get('mention_probability',85))/100,'joke_probability':float(request.form.get('joke_probability',48))/100,'greeting_probability':float(request.form.get('greeting_probability',18))/100,'event_probability':float(request.form.get('event_probability',92))/100,'emoji_level':request.form.get('emoji_level','moderate'),
                'tts_reply_probability':float(request.form.get('tts_reply_probability',35))/100,'tts_volume':request.form.get('tts_volume',1),'ducking_strength':float(request.form.get('ducking_strength',55))/100,'voice_cooldown_seconds':request.form.get('voice_cooldown_seconds',45),'vision_interval_seconds':request.form.get('vision_interval_seconds',45),'vision_max_width':request.form.get('vision_max_width',768),'memory_retention_days':request.form.get('memory_retention_days',30),
            });save_settings(data);flash('Regras do AI Live Host salvas.','success')
        except Exception as exc:flash(str(exc),'error')
        return redirect(url_for('ai.settings'))
    return render_template('ai_settings.html',settings=get_settings(),personas=list_personas(),llm_providers=list_providers('llm'),tts_providers=list_providers('tts'))


@ai_bp.route('/ai/providers',methods=['GET','POST'])
@require_role('admin')
def providers():
    if request.method=='POST':
        kind=request.form.get('kind','llm');provider=request.form.get('provider','');config={
            'api_key':request.form.get('api_key',''),'base_url':request.form.get('base_url',''),'model':request.form.get('model',''),'voice':request.form.get('voice',''),'instructions':request.form.get('instructions',''),'response_format':request.form.get('response_format',''),'endpoint_url':request.form.get('endpoint_url',''),'model_path':request.form.get('model_path',''),'binary':request.form.get('binary',''),'sample_rate':request.form.get('sample_rate',''),'timeout':request.form.get('timeout',''),
        }
        try:save_provider({'id':request.form.get('id') or None,'kind':kind,'name':request.form.get('name','').strip(),'provider':provider,'enabled':request.form.get('enabled')=='on','config':config});flash('Provider salvo.','success')
        except Exception as exc:flash(str(exc),'error')
        return redirect(url_for('ai.providers'))
    return render_template('ai_providers.html',providers=list_providers(),settings=get_settings())


@ai_bp.route('/ai/providers/<pid>/delete',methods=['POST'])
@require_role('admin')
def provider_delete(pid):delete_provider(pid);flash('Provider removido.','success');return redirect(url_for('ai.providers'))


@ai_bp.route('/ai/providers/<pid>/test',methods=['POST'])
@require_role('operator')
def provider_test(pid):
    result=test_provider(pid);flash(result.get('message',''), 'success' if result.get('ok') else 'error');return redirect(url_for('ai.providers'))


@ai_bp.route('/ai/bindings/save',methods=['POST'])
@require_role('operator')
def bindings_save():
    iid=request.form.get('integration_id','')
    try:
        save_binding(iid,request.form.get('channel_id',''),request.form.get('enabled')=='on',request.form.get('read_chat')=='on',request.form.get('write_chat')=='on',request.form.get('read_events')=='on');CHAT_HUB.sync_workers();flash('Conexão do AI Host atualizada.','success')
    except Exception as exc:flash(str(exc),'error')
    return redirect(request.referrer or url_for('ai.overview'))


@ai_bp.route('/ai/kick/<iid>/sync',methods=['POST'])
@require_role('operator')
def kick_sync(iid):
    try:sync_kick_subscriptions(iid);flash('Eventos Kick solicitados. O webhook configurado no app Kick receberá chat/follows/subs/gifts.','success')
    except Exception as exc:flash(str(exc),'error')
    return redirect(request.referrer or url_for('ai.overview'))


@ai_bp.route('/ai/responses/<rid>/send',methods=['POST'])
@require_role('operator')
def response_send(rid):
    ok,msg=AI_HOST.approve(rid,request.form.get('reply_text',''));flash(msg,'success' if ok else 'error');return redirect(request.referrer or url_for('ai.chat'))


@ai_bp.route('/ai/responses/<rid>/ignore',methods=['POST'])
@require_role('operator')
def response_ignore(rid):AI_HOST.ignore_response(rid);flash('Sugestão ignorada.','success');return redirect(request.referrer or url_for('ai.chat'))


@ai_bp.route('/ai/history')
@require_role('viewer')
def history():return render_template('ai_history.html',responses=list_responses(500),subscriptions=list_event_subscriptions(),host_status=AI_HOST.status())


@ai_bp.route('/api/ai/kick/webhook',methods=['POST'])
def kick_webhook():
    raw=request.get_data(cache=False)
    try:
        mid=ingest_kick_webhook(request.headers,raw);return jsonify({'ok':True,'message_id':mid}),200
    except PermissionError as exc:return jsonify({'ok':False,'error':str(exc)}),401
    except Exception as exc:return jsonify({'ok':False,'error':str(exc)}),400


@ai_bp.route('/api/ai/feed')
@require_role('viewer')
def feed():return jsonify({'ok':True,'stats':stats(),'status':AI_HOST.status(),'messages':recent_messages(40),'responses':list_responses(40)})


@ai_bp.route('/api/ai/status')
@require_role('viewer')
def api_status():return jsonify({'ok':True,**AI_HOST.status()})


@ai_bp.route('/api/ai/test-message',methods=['POST'])
@require_role('operator')
def test_message():
    # Ferramenta local para validar ranking/provider sem publicar nada em plataforma real.
    body=request.get_json(silent=True) or request.form
    from .ai_db import ingest_message
    mid=ingest_message(str(body.get('platform') or 'kick'),str(body.get('integration_id') or 'test'),'',str(body.get('channel_id') or ''),str(body.get('user_id') or 'tester'),str(body.get('username') or 'viewer-teste'),str(body.get('text') or 'qual jogo é esse?'),'chat',{'display_name':body.get('username') or 'viewer-teste'})
    return jsonify({'ok':bool(mid),'id':mid})


@ai_bp.app_context_processor
def ai_globals():return {'ai_settings_global':get_settings()}
