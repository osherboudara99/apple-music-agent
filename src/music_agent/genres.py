"""Group Music.app's granular genres into broad families (rock, hip-hop, ...)."""

from __future__ import annotations

import re

# Order matters: family_of() returns the first matching family.
FAMILY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "rock": ("rock", "metal", "punk", "grunge", "alternative", "emo", "hardcore"),
    "hip-hop": ("hip-hop", "hip hop", "rap", "trap"),
    "electronic": (
        "electronic", "dance", "house", "techno", "edm", "trance", "drum & bass", "dubstep",
    ),
    "r&b": ("r&b", "soul", "funk"),
    "pop": ("pop",),
    "jazz": ("jazz",),
    "blues": ("blues",),
    "country": ("country",),
    "classical": ("classical", "opera"),
    "folk": ("folk", "singer/songwriter"),
    "latin": ("latin", "reggaeton"),
    "reggae": ("reggae", "dancehall"),
    "soundtrack": ("soundtrack", "score"),
}

_PATTERNS = {
    family: [re.compile(rf"(?<![a-z]){re.escape(kw)}(?![a-z])") for kw in keywords]
    for family, keywords in FAMILY_KEYWORDS.items()
}


def families_of(genre: str) -> list[str]:
    g = genre.strip().lower()
    return [family for family, patterns in _PATTERNS.items() if any(p.search(g) for p in patterns)]


def family_of(genre: str) -> str:
    families = families_of(genre)
    return families[0] if families else genre.strip().lower()


def matches(genre: str, family: str | None = None, genres: list[str] | None = None) -> bool:
    """True if no filter is given, or the genre is in `genres`, or belongs to `family`."""
    if not family and not genres:
        return True
    g = genre.strip().lower()
    if genres and g in {x.strip().lower() for x in genres}:
        return True
    if family:
        f = family.strip().lower()
        return f in families_of(genre) or g == f
    return False
