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


SAMPLE = json.dumps(
    [
        {
            "persistent_id": "3385DCEE8B3BB7F9",
            "name": "שוש אלמוזלינו",
            "artist": "Habiluim",
            "album": "",
            "genre": "Rock",
            "duration_s": 201.5,
            "date_added": "2024-01-02T03:04:05.000Z",
            "played_count": 3,
            "played_date": "2026-09-27T18:43:23.000Z",
        },
        {
            "persistent_id": "0000000000000001",
            "name": "Never Played",
            "artist": "X",
            "album": "Y",
            "genre": "",
            "duration_s": 0,
            "date_added": None,
            "played_count": 0,
            "played_date": None,
        },
    ],
    ensure_ascii=False,
)


def test_parse_tracks():
    first, second = music.parse_tracks(SAMPLE)
    assert first.persistent_id == "3385DCEE8B3BB7F9"
    assert first.name == "שוש אלמוזלינו"
    assert first.played_date == dt("2026-09-27T18:43:23")
    assert first.date_added == dt("2024-01-02T03:04:05")
    assert second.played_date is None and second.date_added is None
    assert second.played_count == 0


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
