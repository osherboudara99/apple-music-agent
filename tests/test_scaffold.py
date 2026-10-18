import os

import music_agent


def test_version_matches_pyproject():
    assert music_agent.__version__ == "0.1.0"


def test_home_is_isolated(isolated_home):
    assert os.environ["MUSIC_AGENT_HOME"] == str(isolated_home)
