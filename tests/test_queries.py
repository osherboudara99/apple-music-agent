from zoneinfo import ZoneInfo

import pytest

from music_agent import queries as q
from music_agent import store as st
from music_agent.models import PlayEvent
from music_agent.periods import resolve
from tests.factories import dt, make_track

LA = ZoneInfo("America/Los_Angeles")
NOW = dt("2026-09-27T19:00:00")  # 12:00 PDT Sunday


@pytest.fixture
def store(isolated_home):
    s = st.Store(isolated_home / "plays.db")
    tracks = [
        make_track("A", name="Heretic", artist="Avenged Sevenfold", genre="Hard Rock",
                   played_count=10, played_date=dt("2026-09-27T18:00:00")),
        make_track("B", name="Zero", artist="The Smashing Pumpkins", genre="Alternative",
                   played_count=4, played_date=dt("2026-09-26T20:00:00")),
        make_track("C", name="Money Trees", artist="Kendrick Lamar", genre="Hip-Hop/Rap",
                   played_count=7, played_date=dt("2026-09-27T17:00:00")),
        make_track("D", name="Young Lust", artist="Pink Floyd", genre="Rock",
                   played_count=2, played_date=dt("2026-03-01T00:00:00")),
        make_track("E", name="Unplayed 100% Pure", artist="Nobody", genre="Jazz"),
        make_track("F", name="Gone", artist="Removed Band", genre="Rock",
                   played_count=50, played_date=dt("2026-01-15T00:00:00")),
    ]
    plays = [
        PlayEvent("A", dt("2026-09-27T18:00:00"), None, dt("2026-09-27T18:05:00"), False),
        PlayEvent("A", dt("2026-09-27T18:00:00"), dt("2026-09-27T16:00:00"),
                  dt("2026-09-27T18:05:00"), True),
        PlayEvent("A", dt("2026-09-27T18:00:00"), dt("2026-09-27T16:00:00"),
                  dt("2026-09-27T18:05:00"), True),
        PlayEvent("C", dt("2026-09-27T17:00:00"), None, dt("2026-09-27T17:02:00"), False),
        PlayEvent("B", dt("2026-09-26T20:00:00"), None, dt("2026-09-26T20:10:00"), False),
    ]
    with s.transaction() as conn:
        st.upsert_tracks(conn, tracks, dt("2026-09-27T18:05:00"))
        st.mark_removed(conn, ["F"], dt("2026-09-27T18:05:00"))
        st.insert_plays(conn, plays)
        st.set_meta(conn, "install_at", "2026-09-20T00:00:00+00:00")
        st.record_snapshot(conn, dt("2026-09-27T18:05:00"), 5, 3, 600)
    return s


def period(name):
    return resolve(name, None, None, LA, NOW)


def test_played_tracks_today_counts_and_order(store):
    rows = q.played_tracks(store, period("today"))
    assert [(r.id, r.plays_in_range) for r in rows] == [("A", 3), ("C", 1)]
    assert rows[0].approx is True and rows[1].approx is False


def test_played_tracks_genre_family(store):
    rows = q.played_tracks(store, period("past_week"), family="rock")
    assert [r.id for r in rows] == ["A", "B"]


def test_played_tracks_uses_played_date_before_install(store):
    rows = q.played_tracks(store, period("this_year"))
    assert {r.id: r.plays_in_range for r in rows} == {"A": 3, "C": 1, "B": 1, "D": 1, "F": 1}


def test_played_tracks_limit(store):
    assert len(q.played_tracks(store, period("this_year"), limit=2)) == 2


def test_listening_stats_today(store):
    stats = q.listening_stats(store, period("today"), LA)
    assert stats["plays"] == 4
    assert stats["distinct_tracks"] == 2
    assert stats["distinct_artists"] == 2
    assert stats["plays_is_lower_bound"] is False
    assert stats["has_approx_times"] is True
    assert stats["top_tracks"][0] == {"name": "Heretic", "artist": "Avenged Sevenfold", "plays": 3}
    assert stats["top_genres"][0] == {"genre": "Hard Rock", "plays": 3, "tracks": 1}
    assert stats["period"]["start"] == "2026-09-27T00:00-07:00"
    assert stats["plays_counted_since"] == "2026-09-19T17:00-07:00"


def test_listening_stats_this_year_is_lower_bound(store):
    stats = q.listening_stats(store, period("this_year"), LA)
    assert stats["plays_is_lower_bound"] is True
    assert stats["distinct_tracks"] == 5


def test_listening_stats_empty_period(store):
    stats = q.listening_stats(store, resolve(None, "2020-01-01", "2020-01-31", LA, NOW), LA)
    assert stats["plays"] == 0 and stats["distinct_tracks"] == 0 and stats["top_tracks"] == []


def test_all_time_top(store):
    assert [r["name"] for r in q.all_time_top(store, "track", 2)] == ["Heretic", "Money Trees"]
    assert q.all_time_top(store, "artist", 1) == [
        {"artist": "Avenged Sevenfold", "plays": 10, "tracks": 1}
    ]
    assert q.all_time_top(store, "genre", 1)[0]["genre"] == "Hard Rock"
    with pytest.raises(ValueError):
        q.all_time_top(store, "album")


def test_removed_tracks_excluded_from_all_time(store):
    assert "Gone" not in [r["name"] for r in q.all_time_top(store, "track", 10)]


def test_list_genres_library_and_period(store):
    library = {g["genre"]: g for g in q.list_genres(store)}
    assert library["Hard Rock"] == {"genre": "Hard Rock", "family": "rock", "tracks": 1, "plays": 10}
    assert "Jazz" in library
    week = {g["genre"] for g in q.list_genres(store, period("past_week"))}
    assert week == {"Hard Rock", "Alternative", "Hip-Hop/Rap"}


def test_search_library(store):
    assert [r["id"] for r in q.search_library(store, query="money")] == ["C"]
    assert [r["id"] for r in q.search_library(store, artist="pink")] == ["D"]
    assert [r["id"] for r in q.search_library(store, genre="rock")] == ["A", "B", "D"]


def test_search_library_treats_wildcards_literally(store):
    assert [r["id"] for r in q.search_library(store, query="100%")] == ["E"]
    assert [r["id"] for r in q.search_library(store, query="%")] == ["E"]  # literal %, not "match all"
    assert q.search_library(store, query="_") == []
    assert q.search_library(store, query="O'Brien") == []


def test_track_names(store):
    assert q.track_names(store, ["A", "C", "missing"]) == {
        "A": "Avenged Sevenfold - Heretic",
        "C": "Kendrick Lamar - Money Trees",
    }


def test_status_summary(store):
    summary = q.status_summary(store, NOW)
    assert summary["track_count"] == 5
    assert summary["plays_recorded"] == 5
    assert summary["install_at"] == dt("2026-09-20T00:00:00")
    assert summary["last_snapshot"]["events_added"] == 3
    assert summary["last_snapshot"]["error"] is None
    assert summary["sync_lag"]["samples"] == 3
    assert summary["sync_lag"]["median_min"] == 5.0


def test_last_played_is_the_last_play_inside_a_past_window(isolated_home):
    s = st.Store(isolated_home / "window.db")
    with s.transaction() as conn:
        st.upsert_tracks(
            conn,
            [
                make_track("A", played_count=3, played_date=dt("2026-09-27T18:00:00")),
                make_track("B", played_count=1, played_date=dt("2026-09-20T09:00:00")),
            ],
            dt("2026-09-27T18:05:00"),
        )
        st.insert_plays(
            conn,
            [
                PlayEvent("A", dt("2026-09-20T12:00:00"), None, dt("2026-09-20T12:05:00"), False),
                PlayEvent("A", dt("2026-09-27T18:00:00"), None, dt("2026-09-27T18:05:00"), False),
            ],
        )
        st.set_meta(conn, "install_at", "2026-09-01T00:00:00+00:00")
    window = resolve(None, "2026-09-20", "2026-09-20", LA, NOW)
    rows = {r.id: r for r in q.played_tracks(s, window)}
    assert rows["A"].plays_in_range == 1
    assert rows["A"].last_played == dt("2026-09-20T12:00:00")  # not the replay on Sep 27
    assert rows["B"].last_played == dt("2026-09-20T09:00:00")
    assert [r.id for r in q.played_tracks(s, window)] == ["A", "B"]  # sorted by in-window time
