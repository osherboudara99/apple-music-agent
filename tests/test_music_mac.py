import re
import subprocess
from pathlib import Path

import pytest

from music_agent import music

pytestmark = pytest.mark.mac


def test_read_real_library():
    tracks = music.read_library()
    assert len(tracks) > 0
    assert all(re.fullmatch(r"[0-9A-F]{16}", t.persistent_id) for t in tracks[:100])
    assert any(t.played_date is not None for t in tracks)


CLEANUP = Path(__file__).parent / "jxa" / "delete_playlist.js"
TEST_FOLDER = "zz music-agent test folder"
TEST_NAME = "zz music-agent test playlist"


def _cleanup():
    subprocess.run(
        ["osascript", "-l", "JavaScript", str(CLEANUP), TEST_NAME, f"{TEST_NAME} (fallback)",
         TEST_FOLDER],
        capture_output=True, text=True, check=False,
    )


def test_create_playlist_in_folder_and_report_missing():
    played = sorted(
        (t for t in music.read_library() if t.played_date is not None),
        key=lambda t: t.played_date,
        reverse=True,
    )[:3]
    ids = [t.persistent_id for t in played] + ["FFFFFFFFFFFFFFFF"]
    try:
        first = music.create_playlist(TEST_NAME, ids, TEST_FOLDER, f"{TEST_NAME} (fallback)")
        assert first.name == TEST_NAME
        assert first.track_count == 3
        assert first.missing_ids == ["FFFFFFFFFFFFFFFF"]
        second = music.create_playlist(TEST_NAME, ids[:1], TEST_FOLDER, f"{TEST_NAME} (fallback)")
        assert second.name == f"{TEST_NAME} (fallback)"
    finally:
        _cleanup()
