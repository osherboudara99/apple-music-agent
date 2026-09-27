import pytest

from music_agent import store as st
from music_agent.models import PlayEvent
from tests.factories import dt, make_track


@pytest.fixture
def store(isolated_home):
    return st.Store(isolated_home / "plays.db")


def test_iso_round_trip():
    value = dt("2026-09-27T18:43:23")
    assert st.to_iso(value) == "2026-09-27T18:43:23+00:00"
    assert st.from_iso(st.to_iso(value)) == value
    assert st.to_iso(None) is None and st.from_iso(None) is None


def test_schema_and_wal(store):
    with store.connect() as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"tracks", "plays", "snapshots", "meta", "conversations", "usage"} <= tables
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert st.get_meta(conn, "schema_version") == "1"


def test_upsert_and_load_active(store):
    a = make_track("A", name="Heretic", played_count=3, played_date=dt("2026-09-26T20:55:30"))
    with store.transaction() as conn:
        st.upsert_tracks(conn, [a, make_track("B")], dt("2026-09-27T00:00:00"))
    with store.connect() as conn:
        loaded = st.load_active_tracks(conn)
    assert loaded["A"] == a
    assert set(loaded) == {"A", "B"}


def test_unicode_round_trip(store):
    t = make_track("H", name="שוש אלמוזלינו", artist="Habiluim")
    with store.transaction() as conn:
        st.upsert_tracks(conn, [t], dt("2026-09-27T00:00:00"))
    with store.connect() as conn:
        assert st.load_active_tracks(conn)["H"].name == "שוש אלמוזלינו"


def test_removed_tracks_hidden_then_revived(store):
    with store.transaction() as conn:
        st.upsert_tracks(conn, [make_track("A")], dt("2026-09-27T00:00:00"))
        st.mark_removed(conn, ["A"], dt("2026-09-27T01:00:00"))
    with store.connect() as conn:
        assert st.load_active_tracks(conn) == {}
    with store.transaction() as conn:
        st.upsert_tracks(conn, [make_track("A")], dt("2026-09-27T02:00:00"))
    with store.connect() as conn:
        assert "A" in st.load_active_tracks(conn)


def test_insert_plays(store):
    event = PlayEvent("A", dt("2026-09-27T18:00:00"), None, dt("2026-09-27T18:05:00"), False)
    with store.transaction() as conn:
        st.insert_plays(conn, [event])
    with store.connect() as conn:
        row = conn.execute("SELECT * FROM plays").fetchone()
    assert row["persistent_id"] == "A"
    assert row["played_at"] == "2026-09-27T18:00:00+00:00"
    assert row["approx"] == 0 and row["source"] == "snapshot"


def test_transaction_rolls_back_on_error(store):
    with pytest.raises(RuntimeError), store.transaction() as conn:
        st.upsert_tracks(conn, [make_track("A")], dt("2026-09-27T00:00:00"))
        raise RuntimeError("boom")
    with store.connect() as conn:
        assert st.load_active_tracks(conn) == {}


def test_last_snapshot_ignores_failed_runs(store):
    with store.transaction() as conn:
        assert st.last_snapshot_at(conn) is None
        st.record_snapshot(conn, dt("2026-09-27T10:00:00"), 5, 0, 12)
        st.record_snapshot(conn, dt("2026-09-27T10:10:00"), 0, 0, 3, error="Music.app failed")
    with store.connect() as conn:
        assert st.last_snapshot_at(conn) == dt("2026-09-27T10:00:00")


def test_meta(store):
    with store.transaction() as conn:
        st.set_meta(conn, "install_at", "x")
        st.set_meta(conn, "install_at", "y")
    with store.connect() as conn:
        assert st.get_meta(conn, "install_at") == "y"
        assert st.get_meta(conn, "missing") is None
