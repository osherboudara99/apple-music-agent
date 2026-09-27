import json
import subprocess

import pytest

from music_agent import music
from tests.factories import dt


def fake_runner(stdout="", stderr="", returncode=0, calls=None):
    def run(args):
        if calls is not None:
            calls.append(args)
        return subprocess.CompletedProcess(args, returncode, stdout, stderr)

    return run


COLUMNS = {
    "ids": ["3385DCEE8B3BB7F9", "0000000000000001"],
    "names": ["שוש אלמוזלינו", "Never Played"],
    "artists": ["Habiluim", "X"],
    "albums": [None, "Y"],
    "genres": ["Rock", None],
    "durations": [201.5, None],
    "added": ["2024-01-02T03:04:05.000Z", None],
    "counts": [3, None],
    "played": ["2026-09-27T18:43:23.000Z", None],
    "ids_after": ["3385DCEE8B3BB7F9", "0000000000000001"],
}
SAMPLE = json.dumps(COLUMNS, ensure_ascii=False)


def test_parse_tracks():
    first, second = music.parse_tracks(SAMPLE)
    assert first.persistent_id == "3385DCEE8B3BB7F9"
    assert first.name == "שוש אלמוזלינו"
    assert first.played_date == dt("2026-09-27T18:43:23")
    assert first.date_added == dt("2024-01-02T03:04:05")
    assert second.played_date is None and second.date_added is None
    assert second.played_count == 0 and second.genre == "" and second.duration_s == 0.0
    assert first.album == ""


def test_parse_empty_library():
    empty = {key: [] for key in COLUMNS}
    assert music.parse_tracks(json.dumps(empty)) == []


def test_misaligned_columns_are_rejected():
    bad = dict(COLUMNS, counts=[3])  # a track changed between property reads
    with pytest.raises(music.MusicError, match="changed while reading"):
        music.parse_tracks(json.dumps(bad))


def test_library_changing_during_read_is_rejected():
    bad = dict(COLUMNS, ids_after=["3385DCEE8B3BB7F9", "FFFFFFFFFFFFFFFF"])
    with pytest.raises(music.MusicError, match="changed while reading"):
        music.parse_tracks(json.dumps(bad))


def test_read_library_calls_osascript_with_script():
    calls = []
    tracks = music.read_library(runner=fake_runner(stdout=SAMPLE, calls=calls))
    assert len(tracks) == 2
    args = calls[0]
    assert args[:3] == ["osascript", "-l", "JavaScript"]
    assert args[3].endswith("jxa/read_library.js")


def test_permission_denied_error():
    stderr = "read_library.js: execution error: Not authorized to send Apple events to Music. (-1743)"
    with pytest.raises(music.MusicError) as info:
        music.run_jxa("read_library.js", runner=fake_runner(stderr=stderr, returncode=1))
    assert info.value.code == -1743
    assert info.value.permission_denied
    assert "Automation" in str(info.value)


def test_generic_error_keeps_message():
    stderr = "execution error: Error: Can't get object. (-1728)"
    with pytest.raises(music.MusicError) as info:
        music.run_jxa("read_library.js", runner=fake_runner(stderr=stderr, returncode=1))
    assert info.value.code == -1728
    assert "Can't get object" in str(info.value)


def test_error_without_code():
    with pytest.raises(music.MusicError) as info:
        music.run_jxa("read_library.js", runner=fake_runner(stderr="", returncode=1))
    assert info.value.code is None


def test_create_playlist_builds_payload_and_parses_result():
    calls = []
    out = json.dumps(
        {"name": "Rock (Sep 27)", "persistent_id": "ABCDEF0123456789", "track_count": 2,
         "missing_ids": ["GONE"]}
    )
    result = music.create_playlist(
        name="Rock",
        track_ids=["A1", "B2", "A1", "GONE"],
        folder="Music Agent",
        fallback_name="Rock (Sep 27)",
        description="rock from this week",
        runner=fake_runner(stdout=out, calls=calls),
    )
    args = calls[0]
    assert args[3].endswith("jxa/create_playlist.js")
    payload = json.loads(args[4])
    assert payload.pop("temp_name").startswith("music-agent building ")
    assert payload == {
        "name": "Rock",
        "fallback_name": "Rock (Sep 27)",
        "folder": "Music Agent",
        "description": "rock from this week",
        "track_ids": ["A1", "B2", "GONE"],
    }
    assert result == music.PlaylistResult("Rock (Sep 27)", "ABCDEF0123456789", 2, ["GONE"])


@pytest.mark.parametrize(("name", "ids"), [("  ", ["A"]), ("Rock", [])])
def test_create_playlist_rejects_empty_input(name, ids):
    with pytest.raises(ValueError):
        music.create_playlist(name, ids, "Music Agent", "x", runner=fake_runner(stdout="{}"))


def test_run_jxa_timeout_becomes_music_error():
    def slow(args):
        raise subprocess.TimeoutExpired(args, 300)

    with pytest.raises(music.MusicError) as info:
        music.run_jxa("read_library.js", runner=slow)
    assert info.value.timed_out
    assert "didn't respond" in str(info.value)


def test_create_playlist_builds_under_unique_temp_name():
    calls = []
    out = json.dumps({"name": "Rock", "persistent_id": "P", "track_count": 1, "missing_ids": []})
    runner = fake_runner(stdout=out, calls=calls)
    music.create_playlist("Rock", ["A"], "Music Agent", "Rock (x)", runner=runner)
    music.create_playlist("Rock", ["A"], "Music Agent", "Rock (x)", runner=runner)
    first, second = (json.loads(c[4])["temp_name"] for c in calls)
    assert first.startswith("music-agent building ") and first != second


def test_create_playlist_timeout_deletes_only_its_temp_playlist():
    calls = []

    def runner(args):
        calls.append(args)
        if args[3].endswith("create_playlist.js"):
            raise subprocess.TimeoutExpired(args, 300)
        return subprocess.CompletedProcess(args, 0, "1", "")

    with pytest.raises(music.MusicError, match="nothing was created"):
        music.create_playlist("Rock", ["A"], "Music Agent", "Rock (x)", runner=runner)
    temp_name = json.loads(calls[0][4])["temp_name"]
    assert calls[1][3].endswith("jxa/delete_temp_playlist.js")
    assert calls[1][4:] == [temp_name]


def test_create_playlist_timeout_reports_leftover_if_cleanup_fails():
    def runner(args):
        if args[3].endswith("create_playlist.js"):
            raise subprocess.TimeoutExpired(args, 300)
        return subprocess.CompletedProcess(args, 1, "", "execution error: boom (-1)")

    with pytest.raises(music.MusicError, match="music-agent building"):
        music.create_playlist("Rock", ["A"], "Music Agent", "Rock (x)", runner=runner)


def test_default_runner_uses_utf8(monkeypatch):
    seen = {}

    def fake_run(args, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(music.subprocess, "run", fake_run)
    music._default_runner(["osascript"])
    assert seen["encoding"] == "utf-8" and seen["check"] is False and seen["timeout"] > 0


def test_timeout_after_rename_warns_playlist_may_exist():
    def runner(args):
        if args[3].endswith("create_playlist.js"):
            raise subprocess.TimeoutExpired(args, 300)
        return subprocess.CompletedProcess(args, 0, "0\n", "")  # cleanup deleted nothing

    with pytest.raises(music.MusicError) as info:
        music.create_playlist("Rock", ["A"], "Music Agent", "Rock (x)", runner=runner)
    text = str(info.value)
    assert "nothing was created" not in text
    assert '"Rock"' in text and "may already exist" in text
