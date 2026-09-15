from contextlib import contextmanager
import sqlite3

import hoststorm.output_management as output_management
from hoststorm.professional import quality_label


class FakeDB:
    def __init__(self, path):
        self.path = path

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()


def test_output_desired_state_is_persisted_per_destination(tmp_path, monkeypatch):
    db = FakeDB(tmp_path / 'test.db')
    with db.connect() as con:
        con.execute('CREATE TABLE destinations(channel_id TEXT,slug TEXT,settings_json TEXT)')
        con.execute(
            'INSERT INTO destinations(channel_id,slug,settings_json) VALUES(?,?,?)',
            ('channel-a', 'youtube_shorts__one', '{"output_source_mode":"local"}'),
        )
        con.execute(
            'INSERT INTO destinations(channel_id,slug,settings_json) VALUES(?,?,?)',
            ('channel-a', 'youtube_shorts__two', '{}'),
        )
    monkeypatch.setattr(output_management, 'DB', db)

    assert output_management._set_output_desired('channel-a', 'youtube_shorts__one', True) is True
    assert output_management._desired_outputs() == [('channel-a', 'youtube_shorts__one')]

    assert output_management._set_output_desired('channel-a', 'youtube_shorts__one', False) is True
    assert output_management._desired_outputs() == []


def test_clear_channel_desired_does_not_touch_other_channels(tmp_path, monkeypatch):
    db = FakeDB(tmp_path / 'test.db')
    with db.connect() as con:
        con.execute('CREATE TABLE destinations(channel_id TEXT,slug TEXT,settings_json TEXT)')
        con.execute(
            'INSERT INTO destinations(channel_id,slug,settings_json) VALUES(?,?,?)',
            ('a', 'youtube', '{"manual_desired_running":true}'),
        )
        con.execute(
            'INSERT INTO destinations(channel_id,slug,settings_json) VALUES(?,?,?)',
            ('b', 'youtube', '{"manual_desired_running":true}'),
        )
    monkeypatch.setattr(output_management, 'DB', db)

    output_management._clear_channel_desired('a')
    assert output_management._desired_outputs() == [('b', 'youtube')]


def test_frame_rate_conversion_drops_do_not_create_false_warning():
    # A 60fps source converted to a healthy 30fps output can accumulate many FFmpeg
    # drop_frames even though realtime delivery, FPS and bitrate are all correct.
    assert quality_label(29.99, 30, 10095, 10000, 1.0, 178000) == 'excellent'


def test_drops_still_warn_when_realtime_delivery_is_degraded():
    assert quality_label(29.0, 30, 10000, 10000, 0.90, 500) == 'warning'
