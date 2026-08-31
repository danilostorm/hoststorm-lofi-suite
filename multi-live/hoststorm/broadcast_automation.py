from __future__ import annotations

import json
import os
from types import MethodType

from flask import Blueprint, flash, has_request_context, jsonify, redirect, request, session, url_for

from .auth import require_role
from .integrations_v32 import PROVIDER_SPECS, enabled_integrations, get_integration, provider_specs, save_integration_v32
from .kick_integration_v33 import apply_metadata_v33 as apply_metadata, search_categories_v33 as search_categories
from .kick_oauth import KICK_SCOPES, authorize_url, discover_account, exchange_code, new_pkce, new_state, normalize_token_payload
from .utils import now_dt, now_iso

automation_bp = Blueprint('automation', __name__)

DB = None
MANAGER = None


def migrate_schedule_schema(db_module):
    columns = {
        'metadata_enabled': "INTEGER NOT NULL DEFAULT 0",
        'metadata_integrations_json': "TEXT NOT NULL DEFAULT '[]'",
        'broadcast_title': "TEXT NOT NULL DEFAULT ''",
        'broadcast_category': "TEXT NOT NULL DEFAULT ''",
        'broadcast_description': "TEXT NOT NULL DEFAULT ''",
        'metadata_failure_policy': "TEXT NOT NULL DEFAULT 'continue'",
        'metadata_last_report_json': "TEXT NOT NULL DEFAULT '[]'",
    }
    with db_module.connect() as con:
        existing = {r['name'] for r in con.execute('PRAGMA table_info(schedules)').fetchall()}
        for name, ddl in columns.items():
            if name not in existing:
                con.execute(f'ALTER TABLE schedules ADD COLUMN {name} {ddl}')
        con.execute("INSERT INTO meta(key,value) VALUES('schema_version','4') ON CONFLICT(key) DO UPDATE SET value='4'")


def _metadata_row(db_module, sid):
    with db_module.connect() as con:
        row = con.execute(
            'SELECT metadata_enabled,metadata_integrations_json,broadcast_title,broadcast_category,broadcast_description,'
            'metadata_failure_policy,metadata_last_report_json FROM schedules WHERE id=?', (sid,)
        ).fetchone()
    if not row:
        return {}
    out = dict(row)
    try:
        out['metadata_integrations'] = json.loads(out.pop('metadata_integrations_json') or '[]')
    except Exception:
        out['metadata_integrations'] = []
    try:
        out['metadata_last_report'] = json.loads(out.pop('metadata_last_report_json') or '[]')
    except Exception:
        out['metadata_last_report'] = []
    out['metadata_enabled'] = bool(out.get('metadata_enabled'))
    return out


def _augment(db_module, schedule):
    if schedule:
        schedule.update(_metadata_row(db_module, schedule['id']))
    return schedule


def install_schedule_metadata(db_module, web_module, scheduler_module):
    migrate_schedule_schema(db_module)
    original_list = db_module.list_schedules
    original_get = db_module.get_schedule
    original_save = db_module.save_schedule

    def list_schedules(channel_id=None, con=None):
        return [_augment(db_module, x) for x in original_list(channel_id, con)]

    def get_schedule(sid):
        return _augment(db_module, original_get(sid))

    def save_schedule(data):
        payload = dict(data or {})
        if has_request_context():
            payload.setdefault('metadata_enabled', request.form.get('metadata_enabled') == 'on')
            payload.setdefault('metadata_integrations', request.form.getlist('metadata_integrations'))
            payload.setdefault('broadcast_title', request.form.get('broadcast_title', ''))
            payload.setdefault('broadcast_category', request.form.get('broadcast_category', ''))
            payload.setdefault('broadcast_description', request.form.get('broadcast_description', ''))
            payload.setdefault('metadata_failure_policy', request.form.get('metadata_failure_policy', 'continue'))
        sid = original_save(payload)
        ids = [str(x) for x in (payload.get('metadata_integrations') or []) if x]
        policy = str(payload.get('metadata_failure_policy') or 'continue')
        if policy not in {'continue', 'block'}:
            policy = 'continue'
        with db_module.connect() as con:
            con.execute(
                'UPDATE schedules SET metadata_enabled=?,metadata_integrations_json=?,broadcast_title=?,broadcast_category=?,'
                'broadcast_description=?,metadata_failure_policy=?,updated_at=? WHERE id=?',
                (
                    int(bool(payload.get('metadata_enabled'))), json.dumps(ids, ensure_ascii=False),
                    str(payload.get('broadcast_title') or '')[:500], str(payload.get('broadcast_category') or '')[:300],
                    str(payload.get('broadcast_description') or '')[:10000], policy, now_iso(), sid,
                ),
            )
        return sid

    db_module.list_schedules = list_schedules
    db_module.get_schedule = get_schedule
    db_module.save_schedule = save_schedule
    web_module.list_schedules = list_schedules
    web_module.get_schedule = get_schedule
    web_module.save_schedule = save_schedule
    scheduler_module.list_schedules = list_schedules
    return list_schedules, get_schedule, save_schedule


def _replace_tokens(text, channel, schedule):
    value = str(text or '')
    now = now_dt()
    source = str(schedule.get('source_title') or '').strip()
    if not source:
        source = ' → '.join(schedule.get('media') or [])
    replacements = {
        '{source}': source,
        '{channel}': str((channel or {}).get('name') or ''),
        '{date}': now.strftime('%d/%m/%Y'),
        '{time}': now.strftime('%H:%M'),
    }
    for token, replacement in replacements.items():
        value = value.replace(token, replacement)
    return value.strip()


def _schedule_metadata(channel, schedule):
    return {
        'title': _replace_tokens(schedule.get('broadcast_title'), channel, schedule),
        'category': _replace_tokens(schedule.get('broadcast_category'), channel, schedule),
        'description': _replace_tokens(schedule.get('broadcast_description'), channel, schedule),
        'channel_id': schedule.get('channel_id'),
        'schedule_id': schedule.get('id'),
        'schedule_name': schedule.get('name') or '',
        'source': schedule.get('source_title') or ' → '.join(schedule.get('media') or []),
        'scheduled_time': schedule.get('time') or '',
        'at': now_iso(),
    }


def apply_schedule_metadata(db_module, channel, schedule, platforms):
    ids = list(schedule.get('metadata_integrations') or [])
    metadata = _schedule_metadata(channel, schedule)
    results = []
    for iid in ids:
        account = get_integration(iid)
        if not account:
            results.append({'ok': False, 'integration_id': iid, 'message': 'Integração não encontrada.'})
            continue
        spec = PROVIDER_SPECS.get(account.get('provider'), {})
        expected = set(spec.get('platforms') or [])
        if expected and not expected.intersection(set(platforms or [])):
            results.append({'ok': True, 'skipped': True, 'integration_id': iid, 'integration_name': account.get('name'), 'provider': account.get('provider'), 'message': 'Plataforma não selecionada nesta agenda.'})
            continue
        try:
            results.append(apply_metadata(iid, metadata))
        except Exception as e:
            results.append({'ok': False, 'integration_id': iid, 'integration_name': account.get('name'), 'provider': account.get('provider'), 'message': str(e)})
    with db_module.connect() as con:
        con.execute('UPDATE schedules SET metadata_last_report_json=?,updated_at=? WHERE id=?', (json.dumps(results, ensure_ascii=False), now_iso(), schedule['id']))
    return results


def install_stream_metadata(manager, db_module):
    original_start = manager.start

    def start(self, cid, platforms=None, media=None, trigger='manual', schedule=None):
        schedule_obj = dict(schedule or {}) if schedule else None
        if trigger == 'scheduled' and schedule_obj and schedule_obj.get('metadata_enabled') and not schedule_obj.get('_metadata_applied'):
            channel = db_module.get_channel(cid, False)
            report = apply_schedule_metadata(db_module, channel, schedule_obj, platforms or schedule_obj.get('platforms') or [])
            failures = [x for x in report if not x.get('ok')]
            summary = ' | '.join(str(x.get('message') or '') for x in report if x.get('message')) or 'Sem alterações de metadados.'
            try:
                self.log(cid, 'Automação de metadados: ' + summary)
            except Exception:
                pass
            db_module.audit('error' if failures else 'info', 'broadcast_metadata', cid, summary, {'schedule_id': schedule_obj.get('id'), 'results': report})
            schedule_obj['_metadata_applied'] = True
            if failures and schedule_obj.get('metadata_failure_policy') == 'block':
                db_module.update_schedule_status(schedule_obj['id'], last_status='Live bloqueada: falha ao atualizar metadados da plataforma.')
                return False, 'Live bloqueada porque a automação de título/categoria falhou: ' + '; '.join(x.get('message', '') for x in failures)
        return original_start(cid, platforms, media, trigger, schedule_obj)

    manager.start = MethodType(start, manager)
    return manager


def _public_base_url():
    configured = str(os.environ.get('HOSTSTORM_PUBLIC_URL') or '').strip().rstrip('/')
    if configured:
        return configured
    if has_request_context():
        return request.url_root.rstrip('/')
    return ''


def _kick_callback_url():
    base = _public_base_url()
    return base + '/api/kick/oauth/callback' if base else '/api/kick/oauth/callback'


def install_broadcast_automation(app, db_module, web_module, scheduler_module, manager):
    global DB, MANAGER
    DB = db_module
    MANAGER = manager
    install_schedule_metadata(db_module, web_module, scheduler_module)
    install_stream_metadata(manager, db_module)

    @app.context_processor
    def automation_context():
        return {
            'integration_accounts': enabled_integrations(),
            'integration_provider_specs': provider_specs(),
            'kick_callback_url': _kick_callback_url(),
            'kick_public_url_configured': bool(str(os.environ.get('HOSTSTORM_PUBLIC_URL') or '').strip()),
            'kick_required_scopes': ' '.join(KICK_SCOPES),
        }


@automation_bp.route('/integrations/save-v32', methods=['POST'])
@require_role('admin')
def integration_save():
    provider = request.form.get('provider', 'twitch')
    name = request.form.get('name', '').strip() or PROVIDER_SPECS.get(provider, {}).get('label', provider)
    iid = request.form.get('id', '').strip() or None
    keys = [
        'client_id', 'client_secret', 'access_token', 'refresh_token', 'channel_login', 'broadcaster_id',
        'channel_slug', 'channel_id', 'api_key', 'broadcast_id', 'endpoint_url', 'bearer_token',
        'kick_user_id', 'broadcaster_user_id', 'token_scope', 'token_expires_at', 'oauth_connected',
    ]
    config = {k: request.form.get(k, '').strip() for k in keys}
    try:
        iid = save_integration_v32(provider, name, config, request.form.get('enabled') == 'on', iid)
        if provider == 'kick' and request.form.get('oauth_connect') == '1':
            return redirect(url_for('automation.kick_oauth_start', iid=iid))
        flash('Integração salva. Use “Testar API” para validar token e permissões.', 'success')
    except Exception as e:
        flash(str(e), 'error')
    return redirect(url_for('pro.integrations'))


@automation_bp.route('/professional/integrations/<iid>/kick/connect')
@require_role('admin')
def kick_oauth_start(iid):
    item = get_integration(iid)
    if not item or item.get('provider') != 'kick':
        flash('Integração Kick não encontrada.', 'error')
        return redirect(url_for('pro.integrations'))
    config = item.get('config') or {}
    client_id = str(config.get('client_id') or '').strip()
    client_secret = str(config.get('client_secret') or '').strip()
    if not client_id or not client_secret:
        flash('Informe Client ID e Client Secret da aplicação Kick antes de conectar.', 'error')
        return redirect(url_for('pro.integrations', edit=iid))
    redirect_uri = _kick_callback_url()
    if not redirect_uri.startswith(('http://', 'https://')):
        flash('Configure HOSTSTORM_PUBLIC_URL com o domínio público HTTPS do painel para usar OAuth da Kick.', 'error')
        return redirect(url_for('pro.integrations', edit=iid))
    verifier, challenge = new_pkce()
    state = new_state()
    session['kick_oauth_pending'] = {
        'iid': iid,
        'state': state,
        'verifier': verifier,
        'redirect_uri': redirect_uri,
    }
    return redirect(authorize_url(client_id, redirect_uri, state, challenge, KICK_SCOPES))


@automation_bp.route('/api/kick/oauth/callback')
def kick_oauth_callback():
    pending = session.pop('kick_oauth_pending', None) or {}
    state = str(request.args.get('state') or '')
    code = str(request.args.get('code') or '')
    error = str(request.args.get('error') or '')
    if error:
        flash('Kick recusou a autorização: ' + error, 'error')
        return redirect(url_for('pro.integrations'))
    if not pending or not state or state != str(pending.get('state') or ''):
        flash('Kick OAuth: state inválido ou sessão expirada. Tente conectar novamente.', 'error')
        return redirect(url_for('pro.integrations'))
    if not code:
        flash('Kick OAuth não retornou o código de autorização.', 'error')
        return redirect(url_for('pro.integrations'))
    iid = str(pending.get('iid') or '')
    item = get_integration(iid)
    if not item or item.get('provider') != 'kick':
        flash('Integração Kick não encontrada após o retorno do OAuth.', 'error')
        return redirect(url_for('pro.integrations'))
    config = dict(item.get('config') or {})
    try:
        tokens = exchange_code(
            str(config.get('client_id') or ''),
            str(config.get('client_secret') or ''),
            str(pending.get('redirect_uri') or ''),
            code,
            str(pending.get('verifier') or ''),
        )
        token_data = normalize_token_payload(tokens, str(config.get('refresh_token') or ''))
        if not token_data.get('access_token'):
            raise RuntimeError('Kick OAuth não retornou access_token.')
        config.update(token_data)
        config.update(discover_account(token_data['access_token']))
        config['oauth_connected'] = '1'
        save_integration_v32('kick', item.get('name') or config.get('channel_slug') or 'Kick', config, bool(item.get('enabled')), iid)
        flash('Kick conectado por OAuth oficial. Access token e refresh token foram salvos criptografados.', 'success')
    except Exception as e:
        flash('Falha no OAuth da Kick: ' + str(e), 'error')
    return redirect(url_for('pro.integrations', edit=iid))


@automation_bp.route('/api/integrations/<iid>/categories')
def integration_categories(iid):
    query = request.args.get('q', '').strip()
    try:
        return jsonify({'ok': True, 'items': search_categories(iid, query)})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e), 'items': []}), 400
