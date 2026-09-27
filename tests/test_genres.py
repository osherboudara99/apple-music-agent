import pytest

from music_agent.genres import families_of, family_of, matches


@pytest.mark.parametrize(
    ("genre", "family"),
    [
        ("Rock", "rock"),
        ("Hard Rock", "rock"),
        ("Alternative", "rock"),
        ("Metal", "rock"),
        ("Pop Punk", "rock"),
        ("Hip-Hop/Rap", "hip-hop"),
        ("Hip-Hop", "hip-hop"),
        ("Trap", "hip-hop"),
        ("Electronic", "electronic"),
        ("Dance", "electronic"),
        ("R&B/Soul", "r&b"),
        ("K-Pop", "pop"),
        ("Singer/Songwriter", "folk"),
        ("Dancehall", "reggae"),
        ("Worldwide", "worldwide"),
        ("", ""),
    ],
)
def test_family_of(genre, family):
    assert family_of(genre) == family


def test_keywords_match_whole_words_only():
    assert "hip-hop" not in families_of("Trapeze")  # "trap" inside a longer word
    assert "electronic" not in families_of("Dancehall")
    assert "rock" not in families_of("Memo")  # "emo" inside a word


def test_pop_punk_is_in_both_families():
    assert families_of("Pop Punk") == ["rock", "pop"]


def test_matches_no_filter_is_true():
    assert matches("Jazz")


def test_matches_family_case_insensitive():
    assert matches("Hard Rock", family="ROCK")
    assert not matches("Jazz", family="rock")


def test_matches_unknown_genre_by_its_own_name():
    assert matches("Worldwide", family="worldwide")


def test_matches_exact_genre_list():
    assert matches("Hard Rock", genres=["hard rock", "Metal"])
    assert not matches("Rock", genres=["Hard Rock"])


def test_matches_family_or_genres():
    assert matches("Jazz", family="rock", genres=["Jazz"])
