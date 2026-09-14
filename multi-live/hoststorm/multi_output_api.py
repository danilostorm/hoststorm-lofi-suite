from __future__ import annotations

from flask import Blueprint, abort, jsonify

from . import multi_output


multi_output_api_bp = Blueprint('multi_output_api', __name__)


def _human_time(seconds) -> str:
    try:
        seconds = max(0, int(float(seconds or 0)))
    except Exception:
        seconds = 0
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f'{hours:02d}:{minutes:02d}:{secs:02d}'
    return f'{minutes:02d}:{secs:02d}'


@multi_output_api_bp.route('/api/output-settings/<cid>')
def output_settings(cid):
    web = multi_output.WEB
    if not web:
        abort(503)
    channel = web.get_channel(cid, False)
    if not channel:
        abort(404)
    seconds = multi_output._safe_seconds(channel.get('rerun_start_seconds'))
    return jsonify({
        'ok': True,
        'channel_id': cid,
        'rerun_enabled': multi_output._truthy(channel.get('rerun_enabled')),
        'rerun_start_seconds': seconds,
        'rerun_start_human': _human_time(seconds),
    })
