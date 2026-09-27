import pytest

from music_agent import store as st
from music_agent.config import Config
from music_agent.models import PlayEvent
from music_agent.music import MusicError, PlaylistResult
from music_agent.snapshot import SnapshotResult
from music_agent.tools import Toolbox, ToolContext
from tests.factories import dt, make_track

NOW = dt("2026-09-27T19:00:00")
NAMES = {
    "listening_stats", "played_tracks", "all_time_top", "list_genres", "search_library",
    "create_playlist",
}


@pytest.fixture
def store(isolated_home):
    s = st.Store(isolated_home / "plays.db")
    with s.transaction() as conn:
        st.upsert_tracks(
            conn,
            [
                make_track("A", name="Heretic", artist="Avenged Sevenfold", genre="Hard Rock",
                           played_count=10, played_date=dt("2026-09-27T18:00:00")),
                make_track("C", name="Money Trees", artist="Kendrick Lamar", genre="Hip-Hop/Rap",
                           played_count=7, played_date=dt("2026-09-27T17:00:00")),
            ],
            dt("2026-09-27T18:05:00"),
        )
        st.insert_plays(
            conn,
            [PlayEvent("A", dt("2026-09-27T18:00:00"), None, dt("2026-09-27T18:05:00"), False)],
        )
        st.set_meta(conn, "install_at", "2026-09-20T00:00:00+00:00")
    return s


class Recorder:
    def __init__(self, refresh_result=None, refresh_error=None):
        self.refresh_calls = 0
        self.playlist_calls = []
        self.refresh_result = refresh_result
        self.refresh_error = refresh_error

    def refresh(self):
        self.refresh_calls += 1
        if self.refresh_error:
            raise self.refresh_error
        return self.refresh_result

    def create_playlist(self, **kwargs):
        self.playlist_calls.append(kwargs)
        return PlaylistResult(kwargs["name"], "PL1", len(kwargs["track_ids"]) - 1, ["GONE"])


def make_toolbox(store, recorder):
    ctx = ToolContext(
        store=store,
        config=Config(timezone="America/Los_Angeles"),
        clock=lambda: NOW,
        refresh=recorder.refresh,
        create_playlist=recorder.create_playlist,
    )
    return Toolbox(ctx)


def test_definitions_are_valid_schemas(store):
    defs = make_toolbox(store, Recorder()).definitions()
    assert {d["name"] for d in defs} == NAMES
    for d in defs:
        assert d["description"]
        assert d["input_schema"]["type"] == "object"
        assert d["input_schema"]["additionalProperties"] is False
        assert set(d["input_schema"].get("required", [])) <= set(d["input_schema"]["properties"])


def test_listening_stats_today_refreshes_and_adds_now(store):
    rec = Recorder()
    result = make_toolbox(store, rec).run("listening_stats", {"period": "today"})
    assert rec.refresh_calls == 1
    assert result["distinct_tracks"] == 2
    assert result["now"] == "2026-09-27T12:00-07:00"
    assert "warning" not in result


def test_old_explicit_range_does_not_refresh(store):
    rec = Recorder()
    make_toolbox(store, rec).run("listening_stats", {"start": "2026-01-01", "end": "2026-01-31"})
    assert rec.refresh_calls == 0


def test_refresh_failure_becomes_warning(store):
    rec = Recorder(refresh_error=MusicError("Music.app script failed", -1))
    result = make_toolbox(store, rec).run("listening_stats", {"period": "today"})
    assert "Could not refresh" in result["warning"]
    assert result["distinct_tracks"] == 2


def test_refresh_result_with_error_becomes_warning(store):
    failed = SnapshotResult(NOW, 0, 0, False, [], error="boom")
    result = make_toolbox(store, Recorder(refresh_result=failed)).run(
        "played_tracks", {"period": "today"}
    )
    assert "boom" in result["warning"]


def test_played_tracks_shape_and_limit(store):
    result = make_toolbox(store, Recorder()).run("played_tracks", {"period": "today", "limit": 1})
    assert result["count"] == 1 and result["truncated"] is True
    assert result["tracks"][0]["id"] == "A"
    assert result["tracks"][0]["last_played"] == "2026-09-27T11:00-07:00"


def test_played_tracks_genre_family(store):
    result = make_toolbox(store, Recorder()).run(
        "played_tracks", {"period": "today", "genre_family": "rock"}
    )
    assert [t["id"] for t in result["tracks"]] == ["A"]


def test_bad_period_returns_error(store):
    result = make_toolbox(store, Recorder()).run("listening_stats", {"period": "fortnight"})
    assert "Unknown period" in result["error"]
    assert "now" in result


def test_unknown_tool(store):
    assert "Unknown tool" in make_toolbox(store, Recorder()).run("delete_everything", {})["error"]


def test_all_time_top_and_search_and_genres(store):
    box = make_toolbox(store, Recorder())
    assert box.run("all_time_top", {"by": "artist"})["results"][0]["artist"] == "Avenged Sevenfold"
    assert box.run("search_library", {"query": "money"})["results"][0]["id"] == "C"
    genres = box.run("list_genres", {})["genres"]
    assert {g["genre"] for g in genres} == {"Hard Rock", "Hip-Hop/Rap"}


def test_create_playlist_passes_folder_and_fallback_and_reports_missing(store):
    rec = Recorder()
    result = make_toolbox(store, rec).run(
        "create_playlist", {"name": "Rock week", "track_ids": ["A", "GONE"], "description": "rock"}
    )
    call = rec.playlist_calls[0]
    assert call == {
        "name": "Rock week",
        "track_ids": ["A", "GONE"],
        "folder": "Music Agent",
        "fallback_name": "Rock week (2026-09-27)",
        "description": "rock",
    }
    assert result["name"] == "Rock week"
    assert result["track_count"] == 1
    assert result["missing_track_ids"] == ["GONE"]
    assert result["folder"] == "Music Agent"
    assert result["sample_tracks"] == ["Avenged Sevenfold - Heretic"]


def test_unexpected_exception_is_contained(store):
    rec = Recorder()

    def explode(**kwargs):
        raise KeyError("surprise")

    rec.create_playlist = explode
    result = make_toolbox(store, rec).run("create_playlist", {"name": "x", "track_ids": ["A"]})
    assert result["error"].startswith("Internal error in create_playlist")
