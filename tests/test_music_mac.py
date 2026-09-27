import re

import pytest

from music_agent import music

pytestmark = pytest.mark.mac


def test_read_real_library():
    tracks = music.read_library()
    assert len(tracks) > 0
    assert all(re.fullmatch(r"[0-9A-F]{16}", t.persistent_id) for t in tracks[:100])
    assert any(t.played_date is not None for t in tracks)
