from __future__ import annotations

from types import MethodType

from flask import abort, g, has_request_context


def current_creator_id() -> str:
    """Return the current creator user id only while handling an authenticated request."""
    if not has_request_context():
        return ''
    user = getattr(g, 'user', None) or {}
    if str(user.get('role') or '') != 'creator':
        return ''
    return str(user.get('id') or '')


def channel_visible_to_current_user(channel: dict | None) -> bool:
    creator_id = current_creator_id()
    if not creator_id:
        return True
    return bool(channel) and str(channel.get('owner_user_id') or '') == creator_id


def _ensure_owner_column(db_module):
    with db_module.connect() as con:
        columns = {r['name'] for r in con.execute('PRAGMA table_info(channels)').fetchall()}
        if 'owner_user_id' not in columns:
            con.execute("ALTER TABLE channels ADD COLUMN owner_user_id TEXT NOT NULL DEFAULT ''")
        con.execute('CREATE INDEX IF NOT EXISTS idx_channels_owner_user ON channels(owner_user_id)')
        con.execute("INSERT INTO meta(key,value) VALUES('creator_tenancy_version','1') ON CONFLICT(key) DO UPDATE SET value='1'")


def install_creator_tenancy(db_module, web_module, streaming_module, scheduler_module=None):
    """Scope channels/schedules to the new creator role without affecting background workers.

    Admin/operator/viewer behaviour is preserved.  Only requests authenticated as ``creator``
    are tenant-scoped; scheduler/recovery threads run outside a Flask request and therefore
    continue to see all channels.
    """
    _ensure_owner_column(db_module)

    original_get_channel = db_module.get_channel
    original_list_channels = db_module.list_channels
    original_create_channel = db_module.create_channel
    original_save_channel = db_module.save_channel
    original_delete_channel = db_module.delete_channel
    original_get_schedule = db_module.get_schedule
    original_list_schedules = db_module.list_schedules
    original_save_schedule = db_module.save_schedule
    original_delete_schedule = db_module.delete_schedule
    original_update_schedule_status = db_module.update_schedule_status
    original_list_history = db_module.list_history
    original_list_audit = db_module.list_audit

    manager = streaming_module.MANAGER
    original_manager_start = manager.start
    original_manager_stop = manager.stop
    original_manager_status = manager.channel_status
    original_manager_all_status = manager.all_status

    def owner_for(cid: str) -> str:
        with db_module.connect() as con:
            row = con.execute('SELECT owner_user_id FROM channels WHERE id=?', (str(cid),)).fetchone()
            return str(row['owner_user_id'] or '') if row else ''

    def enrich(channel):
        if not channel:
            return channel
        channel['owner_user_id'] = owner_for(channel.get('id', ''))
        return channel

    def require_owned_channel(cid: str):
        creator_id = current_creator_id()
        if creator_id and owner_for(cid) != creator_id:
            abort(403)

    def get_channel(cid, include_schedules=True):
        channel = enrich(original_get_channel(cid, include_schedules))
        if not channel_visible_to_current_user(channel):
            return None
        return channel

    def list_channels(include_schedules=True):
        rows = original_list_channels(include_schedules)
        creator_id = current_creator_id()
        result = {}
        for cid, channel in rows.items():
            channel['owner_user_id'] = owner_for(cid)
            if creator_id and channel['owner_user_id'] != creator_id:
                continue
            result[cid] = channel
        return result

    def create_channel(name):
        cid = original_create_channel(name)
        creator_id = current_creator_id()
        if creator_id:
            with db_module.connect() as con:
                con.execute('UPDATE channels SET owner_user_id=? WHERE id=?', (creator_id, cid))
            try:
                db_module.audit('info', 'channel_owner_assigned', cid, 'Canal atribuído ao criador.', {'owner_user_id': creator_id})
            except Exception:
                pass
        return cid

    def save_channel(cid, name, settings, destinations):
        require_owned_channel(cid)
        return original_save_channel(cid, name, settings, destinations)

    def delete_channel(cid):
        require_owned_channel(cid)
        return original_delete_channel(cid)

    def get_schedule(sid):
        schedule = original_get_schedule(sid)
        if schedule and current_creator_id() and owner_for(schedule.get('channel_id', '')) != current_creator_id():
            return None
        return schedule

    def list_schedules(channel_id=None, con=None):
        creator_id = current_creator_id()
        if creator_id and channel_id and owner_for(channel_id) != creator_id:
            return []
        rows = original_list_schedules(channel_id, con)
        if not creator_id:
            return rows
        return [s for s in rows if owner_for(s.get('channel_id', '')) == creator_id]

    def save_schedule(data):
        require_owned_channel(str((data or {}).get('channel_id') or ''))
        sid = str((data or {}).get('id') or '')
        if sid and current_creator_id():
            existing = original_get_schedule(sid)
            if existing and owner_for(existing.get('channel_id', '')) != current_creator_id():
                abort(403)
        return original_save_schedule(data)

    def delete_schedule(sid):
        schedule = original_get_schedule(sid)
        if schedule and current_creator_id() and owner_for(schedule.get('channel_id', '')) != current_creator_id():
            abort(403)
        return original_delete_schedule(sid)

    def update_schedule_status(sid, **fields):
        schedule = original_get_schedule(sid)
        if schedule and current_creator_id() and owner_for(schedule.get('channel_id', '')) != current_creator_id():
            abort(403)
        return original_update_schedule_status(sid, **fields)

    def list_history(limit=200):
        creator_id = current_creator_id()
        if not creator_id:
            return original_list_history(limit)
        rows = original_list_history(max(500, int(limit or 200) * 20))
        return [row for row in rows if owner_for(row.get('channel_id', '')) == creator_id][:int(limit or 200)]

    def list_audit(limit=300):
        creator_id = current_creator_id()
        if not creator_id:
            return original_list_audit(limit)
        rows = original_list_audit(max(600, int(limit or 300) * 20))
        return [row for row in rows if row.get('channel_id') and owner_for(row.get('channel_id', '')) == creator_id][:int(limit or 300)]

    def manager_start(self, cid, *args, **kwargs):
        creator_id = current_creator_id()
        if creator_id and owner_for(cid) != creator_id:
            return False, 'Canal não pertence a este usuário.'
        return original_manager_start(cid, *args, **kwargs)

    def manager_stop(self, cid, *args, **kwargs):
        creator_id = current_creator_id()
        if creator_id and owner_for(cid) != creator_id:
            abort(403)
        return original_manager_stop(cid, *args, **kwargs)

    def manager_status(self, cid):
        creator_id = current_creator_id()
        if creator_id and owner_for(cid) != creator_id:
            return {'running': False, 'platforms': {}, 'run_id': '', 'started_at': '', 'stop_at': '', 'trigger': ''}
        return original_manager_status(cid)

    def manager_all_status(self):
        data = original_manager_all_status()
        creator_id = current_creator_id()
        if not creator_id:
            return data
        return {cid: status for cid, status in data.items() if owner_for(cid) == creator_id}

    db_module.get_channel = get_channel
    db_module.list_channels = list_channels
    db_module.create_channel = create_channel
    db_module.save_channel = save_channel
    db_module.delete_channel = delete_channel
    db_module.get_schedule = get_schedule
    db_module.list_schedules = list_schedules
    db_module.save_schedule = save_schedule
    db_module.delete_schedule = delete_schedule
    db_module.update_schedule_status = update_schedule_status
    db_module.list_history = list_history
    db_module.list_audit = list_audit

    # web.py imported these functions directly, so refresh its references too.
    web_module.get_channel = get_channel
    web_module.list_channels = list_channels
    web_module.create_channel = create_channel
    web_module.save_channel = save_channel
    web_module.delete_channel = delete_channel
    web_module.get_schedule = get_schedule
    web_module.list_schedules = list_schedules
    web_module.save_schedule = save_schedule
    web_module.delete_schedule = delete_schedule
    web_module.update_schedule_status = update_schedule_status
    web_module.list_history = list_history
    web_module.list_audit = list_audit

    # StreamManager resolves this module-global symbol when a live starts.
    streaming_module.get_channel = get_channel
    manager.start = MethodType(manager_start, manager)
    manager.stop = MethodType(manager_stop, manager)
    manager.channel_status = MethodType(manager_status, manager)
    manager.all_status = MethodType(manager_all_status, manager)

    if scheduler_module is not None:
        scheduler_module.list_schedules = list_schedules

    return manager
