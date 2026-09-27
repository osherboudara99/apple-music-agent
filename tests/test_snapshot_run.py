import threading
from datetime import timedelta
from itertools import count

import pytest

from music_agent import store as st
from music_agent.snapshot import maybe_snapshot, run_snapshot
from tests.factories import dt, make_track


@pytest.fixture
def store(isolated_home):
    return st.Store(isolated_home / "plays.db")


def ticking_clock(start="2026-09-27T18:00:00", step_seconds=60):
    ticks = count()
    base = dt(start)
    return lambda: base + timedelta(seconds=step_seconds * next(ticks))


def plays(store):
    with store.connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM plays").fetchone()[0]


def test_first_run_sets_install_and_baseline(store):
    clock = ticking_clock()
    result = run_snapshot(store, lambda: [make_track("A", played_count=5)], clock)
    assert result.first_run and result.events_added == 0 and result.track_count == 1
    with store.connect() as conn:
        assert st.get_meta(conn, "install_at") == "2026-09-27T18:00:00+00:00"
        assert st.last_snapshot_at(conn) == dt("2026-09-27T18:00:00")


def test_second_run_records_plays(store):
    clock = ticking_clock()
    run_snapshot(store, lambda: [make_track("A", played_count=5)], clock)
    live = [make_track("A", played_count=7, played_date=dt("2026-09-27T18:00:30"))]
    result = run_snapshot(store, lambda: live, clock)
    assert not result.first_run and result.events_added == 2
    assert plays(store) == 2


def test_read_error_is_recorded_and_changes_nothing(store):
    clock = ticking_clock()
    run_snapshot(store, lambda: [make_track("A", played_count=5)], clock)

    def broken():
        raise RuntimeError("Music.app script failed")

    result = run_snapshot(store, broken, clock)
    assert result.error == "Music.app script failed"
    with store.connect() as conn:
        assert st.last_snapshot_at(conn) == dt("2026-09-27T18:00:00")
        assert "A" in st.load_active_tracks(conn)


def test_empty_library_read_is_an_error_not_a_mass_removal(store):
    clock = ticking_clock()
    run_snapshot(store, lambda: [make_track("A"), make_track("B")], clock)
    result = run_snapshot(store, list, clock)
    assert result.error is not None and "0 tracks" in result.error
    with store.connect() as conn:
        assert set(st.load_active_tracks(conn)) == {"A", "B"}


def test_overlapping_snapshots_count_each_play_once(store):
    clock = ticking_clock()
    run_snapshot(store, lambda: [make_track("A", played_count=5)], clock)
    live = [make_track("A", played_count=7, played_date=dt("2026-09-27T18:00:30"))]
    barrier = threading.Barrier(2)

    def read_after_both_have_read():
        barrier.wait(timeout=5)  # both threads hold the same live data before either writes
        return live

    threads = [
        threading.Thread(target=run_snapshot, args=(store, read_after_both_have_read, clock))
        for _ in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert plays(store) == 2


def test_maybe_snapshot_skips_when_recent(store):
    run_snapshot(store, lambda: [make_track("A")], lambda: dt("2026-09-27T18:00:00"))
    calls = []

    def read():
        calls.append(1)
        return [make_track("A")]

    assert maybe_snapshot(store, read, clock=lambda: dt("2026-09-27T18:00:30")) is None
    assert calls == []
    assert maybe_snapshot(store, read, clock=lambda: dt("2026-09-27T18:02:00")) is not None
    assert calls == [1]


def test_drop_then_restore_creates_no_phantom_plays(store):
    clock = ticking_clock()
    played = dt("2026-03-01T00:00:00")
    run_snapshot(store, lambda: [make_track("A", played_count=150, played_date=played)], clock)
    run_snapshot(store, lambda: [make_track("A", played_count=0, played_date=None)], clock)
    run_snapshot(store, lambda: [make_track("A", played_count=150, played_date=played)], clock)
    assert plays(store) == 0


def test_stale_read_committed_late_does_not_double_count(store):
    clock = ticking_clock()
    run_snapshot(store, lambda: [make_track("A", played_count=5)], clock)
    fresh = [make_track("A", played_count=6, played_date=dt("2026-09-27T18:00:30"))]
    stale = [make_track("A", played_count=5)]
    run_snapshot(store, lambda: fresh, clock)  # B: newer read commits first -> 1 play
    run_snapshot(store, lambda: stale, clock)  # A: older read commits late
    run_snapshot(store, lambda: fresh, clock)  # next regular snapshot
    assert plays(store) == 1
