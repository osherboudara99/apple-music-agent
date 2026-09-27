from music_agent.models import PlayEvent
from music_agent.snapshot import diff
from tests.factories import dt, make_track

PREV = dt("2026-09-27T18:00:00")
NOW = dt("2026-09-27T18:10:00")


def test_first_run_records_no_events():
    live = [make_track("A", played_count=5, played_date=dt("2026-09-27T17:59:00"))]
    result = diff({}, live, None, NOW)
    assert result.events == [] and result.removed_ids == [] and result.warnings == []


def test_count_up_by_one_is_exact():
    old = make_track("A", played_count=5, played_date=dt("2026-09-26T10:00:00"))
    new = make_track("A", played_count=6, played_date=dt("2026-09-27T18:04:00"))
    result = diff({"A": old}, [new], PREV, NOW)
    assert result.events == [PlayEvent("A", dt("2026-09-27T18:04:00"), None, NOW, False)]


def test_count_up_by_three_gives_one_exact_two_approx():
    old = make_track("A", played_count=5, played_date=dt("2026-09-26T10:00:00"))
    new = make_track("A", played_count=8, played_date=dt("2026-09-27T18:09:00"))
    events = diff({"A": old}, [new], PREV, NOW).events
    assert len(events) == 3
    assert events[0] == PlayEvent("A", dt("2026-09-27T18:09:00"), None, NOW, False)
    for e in events[1:]:
        assert e == PlayEvent("A", dt("2026-09-27T18:09:00"), PREV, NOW, True)


def test_count_up_without_new_played_date_is_approx():
    old = make_track("A", played_count=5, played_date=dt("2026-09-27T17:00:00"))
    new = make_track("A", played_count=6, played_date=dt("2026-09-27T17:00:00"))
    # never back-date to the old played_date: the play happened since the last snapshot
    assert diff({"A": old}, [new], PREV, NOW).events == [PlayEvent("A", NOW, PREV, NOW, True)]


def test_count_up_with_missing_played_date_uses_now():
    old = make_track("A", played_count=0)
    new = make_track("A", played_count=1, played_date=None)
    assert diff({"A": old}, [new], PREV, NOW).events == [PlayEvent("A", NOW, PREV, NOW, True)]


def test_new_track_played_since_last_snapshot():
    new = make_track("N", played_count=1, played_date=dt("2026-09-27T18:05:00"))
    assert diff({}, [new], PREV, NOW).events == [
        PlayEvent("N", dt("2026-09-27T18:05:00"), None, NOW, False)
    ]


def test_new_track_played_before_last_snapshot_is_baseline():
    new = make_track("N", played_count=4, played_date=dt("2026-09-20T00:00:00"))
    assert diff({}, [new], PREV, NOW).events == []


def test_new_unplayed_track_is_baseline():
    assert diff({}, [make_track("N")], PREV, NOW).events == []


def test_count_decrease_warns_and_records_nothing():
    old = make_track("A", name="Heretic", artist="A7X", played_count=9)
    new = make_track("A", name="Heretic", artist="A7X", played_count=2)
    result = diff({"A": old}, [new], PREV, NOW)
    assert result.events == []
    assert result.warnings == [
        "play count went down for A7X - Heretic (9 -> 2); keeping 9 until it is exceeded"
    ]


def test_unchanged_track_records_nothing():
    t = make_track("A", played_count=3, played_date=dt("2026-09-20T00:00:00"))
    assert diff({"A": t}, [t], PREV, NOW).events == []


def test_missing_tracks_are_removed():
    stored = {"A": make_track("A"), "B": make_track("B"), "C": make_track("C")}
    result = diff(stored, [make_track("B")], PREV, NOW)
    assert result.removed_ids == ["A", "C"]


def test_count_decrease_keeps_high_water_mark_as_baseline():
    old = make_track("A", played_count=9, played_date=dt("2026-09-01T00:00:00"))
    new = make_track("A", name="Renamed", played_count=0, played_date=None)
    (kept,) = diff({"A": old}, [new], PREV, NOW).baseline
    assert kept.played_count == 9 and kept.played_date == dt("2026-09-01T00:00:00")
    assert kept.name == "Renamed"  # metadata still refreshes


def test_baseline_is_live_data_otherwise():
    new = make_track("A", played_count=6, played_date=dt("2026-09-27T18:04:00"))
    old = make_track("A", played_count=5)
    assert diff({"A": old}, [new], PREV, NOW).baseline == [new]
