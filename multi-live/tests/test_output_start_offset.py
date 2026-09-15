from hoststorm.output_start_offset import (
    output_initial_start_seconds,
    rewrite_initial_start_args,
)
from hoststorm.per_output_rerun import rewrite_input_args


def test_initial_start_defaults_to_zero_and_is_per_output():
    channel = {
        'destinations': {
            'youtube_shorts__mk3': {'output_initial_start_seconds': 60},
            'youtube_shorts__dk3': {'output_initial_start_seconds': 125},
            'youtube_shorts__mk2': {},
        }
    }
    assert output_initial_start_seconds(channel, 'youtube_shorts__mk3') == 60
    assert output_initial_start_seconds(channel, 'youtube_shorts__dk3') == 125
    assert output_initial_start_seconds(channel, 'youtube_shorts__mk2') == 0


def test_first_manual_start_seeks_to_selected_timestamp():
    args = ['-re', '-stream_loop', '-1', '-i', '/media/MK3.mkv']
    result = rewrite_initial_start_args(
        args, start_seconds=60, first_start=True, recovery=False,
    )
    assert result == ['-re', '-stream_loop', '-1', '-ss', '60.000', '-i', '/media/MK3.mkv']


def test_zero_initial_start_keeps_existing_behavior():
    args = ['-re', '-stream_loop', '-1', '-i', '/media/MK3.mkv']
    assert rewrite_initial_start_args(
        args, start_seconds=0, first_start=True, recovery=False,
    ) == args


def test_recovery_does_not_jump_back_to_initial_timestamp():
    args = ['-re', '-stream_loop', '-1', '-i', '/media/MK3.mkv']
    assert rewrite_initial_start_args(
        args, start_seconds=60, first_start=True, recovery=True,
    ) == args


def test_existing_checkpoint_seek_has_priority():
    args = ['-re', '-ss', '431.250', '-i', '/media/MK3.mkv']
    assert rewrite_initial_start_args(
        args, start_seconds=60, first_start=True, recovery=False,
    ) == args


def test_rerun_offset_remains_independent_from_initial_offset():
    base = ['-re', '-stream_loop', '-1', '-i', '/media/DK3.mkv']
    rerun = rewrite_input_args(base, enabled=True, cycle=1, start_seconds=30)
    result = rewrite_initial_start_args(
        rerun, start_seconds=60, first_start=False, recovery=False,
    )
    assert result == ['-re', '-ss', '30.000', '-i', '/media/DK3.mkv']


def test_initial_offset_is_safely_clamped():
    channel = {'destinations': {'youtube__x': {'output_initial_start_seconds': 999999}}}
    assert output_initial_start_seconds(channel, 'youtube__x') == 24 * 3600
