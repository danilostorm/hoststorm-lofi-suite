from hoststorm.config import DEFAULT_CHANNEL_SETTINGS
from hoststorm.multi_output import is_extra_output, platform_kind, _kill_orphan_publishers, _safe_seconds, _truthy
from hoststorm.multi_output_api import _human_time


def test_extra_output_platform_detection():
    dest = {'platform': 'youtube_shorts', 'is_extra': True}
    assert platform_kind('youtube_shorts__abc12345', dest) == 'youtube_shorts'
    assert is_extra_output('youtube_shorts__abc12345', dest) is True


def test_base_destination_is_not_extra():
    assert platform_kind('youtube', {}) == 'youtube'
    assert is_extra_output('youtube', {}) is False


def test_rerun_defaults_are_backward_compatible():
    assert DEFAULT_CHANNEL_SETTINGS['rerun_enabled'] == '0'
    assert DEFAULT_CHANNEL_SETTINGS['rerun_start_seconds'] == '0'
    assert _truthy('on') is True
    assert _truthy('0') is False


def test_rerun_seconds_are_sanitized_and_formatted():
    assert _safe_seconds(-10) == 0
    assert _safe_seconds(60) == 60
    assert _safe_seconds(999999) == 24 * 3600
    assert _human_time(60) == '01:00'
    assert _human_time(3661) == '01:01:01'



def test_force_stop_matches_only_exact_output_target(monkeypatch):
    import hoststorm.multi_output as multi_output

    class FakeWeb:
        @staticmethod
        def get_channel(cid, include_schedules=False):
            return {
                'destinations': {
                    'kick': {
                        'rtmp_url': 'rtmp://example.test/live',
                        'stream_key': 'secret-key',
                    }
                }
            }

    class FakeProc:
        def __init__(self, cmdline):
            self.info = {'pid': 99, 'name': 'ffmpeg', 'cmdline': cmdline}
            self.terminated = False
        def terminate(self):
            self.terminated = True
        def wait(self, timeout=None):
            return 0
        def kill(self):
            self.terminated = True

    match = FakeProc(['ffmpeg', '-f', 'flv', 'rtmp://example.test/live/secret-key'])
    other = FakeProc(['ffmpeg', '-f', 'flv', 'rtmp://example.test/live/other-key'])
    monkeypatch.setattr(multi_output, 'WEB', FakeWeb)
    monkeypatch.setattr(multi_output.psutil, 'process_iter', lambda fields: [match, other])

    assert _kill_orphan_publishers('channel-a', 'kick') == 1
    assert match.terminated is True
    assert other.terminated is False
