from types import SimpleNamespace

from hoststorm.parallel_schedules import _source_seek
from hoststorm.scheduler import _running_platforms


def test_scheduler_conflict_is_platform_scoped():
    status = {
        'running': True,
        'platforms': {
            'youtube': {'running': True},
            'kick': {'running': False},
        },
    }
    active = _running_platforms(status)
    assert active == {'youtube'}
    assert active.isdisjoint({'kick'})
    assert active.intersection({'youtube'}) == {'youtube'}


def test_parallel_url_recovery_uses_elapsed_seek():
    session = SimpleNamespace(
        work_channel={'_schedule_source_url': 'https://youtube.com/watch?v=abc'},
        media=[],
    )
    assert _source_seek(session, 123.5) == 123.5


def test_parallel_youtube_playlist_recovery_uses_item_relative_seek():
    session = SimpleNamespace(
        work_channel={
            '_hs_youtube_playlist_active': True,
            '_hs_youtube_playlist_elapsed_before': 7200,
            '_schedule_source_url': 'https://youtube.com/watch?v=current',
        },
        media=[],
    )
    assert _source_seek(session, 7312.5) == 112.5


def test_parallel_repeating_local_playlist_wraps_seek_to_cycle():
    session = SimpleNamespace(
        work_channel={'_repeat_playlist': True},
        media=['a.mp4', 'b.mp4'],
        _hs_parallel_cycle_duration=600,
    )
    assert _source_seek(session, 1430) == 230
