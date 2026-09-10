from hoststorm.seamless_playlist import _item_position, _total_limit


def _items(*durations):
    return [{'title': f'v{i}', 'duration_seconds': d, 'url': f'https://example.test/{i}'} for i, d in enumerate(durations)]


def test_item_position_advances_without_restarting_publisher():
    items = _items(60, 120, 180)
    assert _item_position(items, 0, False) == (0, 0, 0.0)
    assert _item_position(items, 65, False) == (1, 0, 5.0)
    assert _item_position(items, 200, False) == (2, 0, 20.0)


def test_item_position_wraps_repeating_playlist():
    items = _items(60, 120)
    index, cycle, offset = _item_position(items, 190, True)
    assert index == 0
    assert cycle == 1
    assert offset == 10.0


def test_total_limit_uses_playlist_duration_when_not_repeating():
    assert _total_limit({'repeat_playlist': False, 'stop_before_seconds': 10}, _items(60, 120)) == 170.0


def test_total_limit_honors_explicit_maximum():
    assert _total_limit({'repeat_playlist': True, 'max_duration_minutes': 30}, _items(60, 120)) == 1800.0
