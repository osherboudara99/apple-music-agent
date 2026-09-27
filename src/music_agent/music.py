"""The only module that talks to Music.app (via JXA scripts run by osascript)."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .models import Track

JXA_DIR = Path(__file__).parent / "jxa"
Runner = Callable[[list[str]], subprocess.CompletedProcess]

_CODE_RE = re.compile(r"\((-?\d+)\)\s*$")
PERMISSION_HELP = (
    "macOS blocked access to Music.app. Allow it in System Settings > Privacy & Security > "
    "Automation (turn on Music under your terminal app or music-agent), then try again."
)


class MusicError(RuntimeError):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code

    @property
    def permission_denied(self) -> bool:
        return self.code == -1743


def _default_runner(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=300, check=False)


def run_jxa(script: str, *args: str, runner: Runner | None = None) -> str:
    run = runner or _default_runner
    proc = run(["osascript", "-l", "JavaScript", str(JXA_DIR / script), *args])
    if proc.returncode != 0:
        text = (proc.stderr or "").strip()
        match = _CODE_RE.search(text)
        code = int(match.group(1)) if match else None
        if code == -1743:
            raise MusicError(PERMISSION_HELP, code)
        raise MusicError(f"Music.app script failed: {text or 'no error output'}", code)
    return proc.stdout


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value).astimezone(UTC).replace(microsecond=0)


def parse_tracks(payload: str) -> list[Track]:
    return [
        Track(
            persistent_id=item["persistent_id"],
            name=item["name"],
            artist=item["artist"],
            album=item["album"],
            genre=item["genre"],
            duration_s=float(item["duration_s"]),
            date_added=_parse_time(item["date_added"]),
            played_count=int(item["played_count"]),
            played_date=_parse_time(item["played_date"]),
        )
        for item in json.loads(payload)
    ]


def read_library(runner: Runner | None = None) -> list[Track]:
    return parse_tracks(run_jxa("read_library.js", runner=runner))


@dataclass(frozen=True)
class PlaylistResult:
    name: str
    persistent_id: str
    track_count: int
    missing_ids: list[str]


def create_playlist(
    name: str,
    track_ids: list[str],
    folder: str,
    fallback_name: str,
    description: str = "",
    runner: Runner | None = None,
) -> PlaylistResult:
    ids = list(dict.fromkeys(track_ids))
    if not name.strip():
        raise ValueError("Playlist name is empty.")
    if not ids:
        raise ValueError("No tracks to add to the playlist.")
    payload = json.dumps(
        {
            "name": name.strip(),
            "fallback_name": fallback_name,
            "folder": folder,
            "description": description,
            "track_ids": ids,
        },
        ensure_ascii=False,
    )
    data = json.loads(run_jxa("create_playlist.js", payload, runner=runner))
    return PlaylistResult(
        name=data["name"],
        persistent_id=data["persistent_id"],
        track_count=int(data["track_count"]),
        missing_ids=list(data["missing_ids"]),
    )
