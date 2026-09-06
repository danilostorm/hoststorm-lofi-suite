from types import SimpleNamespace

import hoststorm.pro_db as pro_db
import hoststorm.recovery as recovery


def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(pro_db, 'DB_PATH', tmp_path / 'hoststorm.db')
    recovery.ensure_recovery_table()


def test_checkpoint_roundtrip_and_stop(tmp_path, monkeypatch):
    _tmp_db(tmp_path, monkeypatch)
    recovery.save_checkpoint(
        'channel-a', 'https://youtube.com/watch?v=abc', 123.5, 'url', 'run-1', 3600, 'máxima automática',
        trigger='scheduled', schedule_id='sched-1', platforms=['kick'], media=[],
        schedule={'id': 'sched-1', 'source_mode': 'url'}, elapsed_seconds=123.5,
        max_duration_seconds=3540, status='running', resume_enabled=True,
    )
    state = recovery.get_checkpoint('channel-a')
    assert state['position_seconds'] == 123.5
    assert state['elapsed_seconds'] == 123.5
    assert state['platforms'] == ['kick']
    assert state['schedule']['id'] == 'sched-1'
    assert state['resume_enabled'] == 1

    recovery.mark_state('channel-a', 'stopped', 'manual', False)
    state = recovery.get_checkpoint('channel-a')
    assert state['status'] == 'stopped'
    assert state['resume_enabled'] == 0


def test_remaining_seconds_uses_total_elapsed():
    state = {'max_duration_seconds': 3600, 'elapsed_seconds': 901}
    assert recovery._remaining_seconds(state) == 2699


def test_http_inputs_receive_reconnect_and_seek_only_on_sources():
    args = [
        '-re', '-i', 'https://video.example/v',
        '-re', '-i', 'https://audio.example/a',
        '-stream_loop', '-1', '-i', '/audio/music.mp3',
        '-map', '0:v:0', '-map', '2:a:0',
    ]
    decorated = recovery._decorate_input_args(args, 125.25, 2)
    assert decorated.count('-ss') == 2
    assert decorated.count('-reconnect') == 2
    # External local audio must not be seeked/reconnected.
    external = decorated.index('/audio/music.mp3')
    assert '-ss' not in decorated[max(0, external - 2):external]


def test_looping_seek_wraps_to_media_duration(monkeypatch):
    session = SimpleNamespace(
        trigger='scheduled',
        media=['clip.mp4'],
        work_channel={'_repeat_playlist': True},
    )
    monkeypatch.setattr(recovery, 'probe_duration', lambda name: 100.0)
    assert recovery._seek_for_session(session, 245.0) == 45.0


def test_non_looping_url_seek_keeps_absolute_position():
    session = SimpleNamespace(
        trigger='scheduled',
        media=[],
        work_channel={'_schedule_source_url': 'https://youtube.com/watch?v=abc', '_repeat_playlist': False},
    )
    assert recovery._seek_for_session(session, 245.0) == 245.0
