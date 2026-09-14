from hoststorm.config import DEFAULT_CHANNEL_SETTINGS
from hoststorm.multi_output import is_extra_output, platform_kind, _safe_seconds, _truthy
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
