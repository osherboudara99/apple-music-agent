"""The only module that talks to Music.app (via JXA scripts run by osascript)."""

from __future__ import annotations

import json
import re
import subprocess
import uuid
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


JXA_TIMEOUT_S = 300
TEMP_PREFIX = "music-agent building "


class MusicError(RuntimeError):
    def __init__(self, message: str, code: int | None = None, timed_out: bool = False):
        super().__init__(message)
        self.code = code
        self.timed_out = timed_out

    @property
    def permission_denied(self) -> bool:
        return self.code == -1743


def _default_runner(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, capture_output=True, encoding="utf-8", timeout=JXA_TIMEOUT_S, check=False
    )


def run_jxa(script: str, *args: str, runner: Runner | None = None) -> str:
    run = runner or _default_runner
    try:
        proc = run(["osascript", "-l", "JavaScript", str(JXA_DIR / script), *args])
    except subprocess.TimeoutExpired as exc:
        raise MusicError(
            f"Music.app didn't respond within {JXA_TIMEOUT_S} seconds.", timed_out=True
        ) from exc
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


_COLUMNS = ("names", "artists", "albums", "genres", "durations", "added", "counts", "played")
MISALIGNED_MSG = "Music.app library changed while reading; the next snapshot will retry."


def parse_tracks(payload: str) -> list[Track]:
    data = json.loads(payload)
    ids = data["ids"]
    if data["ids_after"] != ids or any(len(data[c]) != len(ids) for c in _COLUMNS):
        raise MusicError(MISALIGNED_MSG)
    return [
        Track(
            persistent_id=pid,
            name=data["names"][i] or "",
            artist=data["artists"][i] or "",
            album=data["albums"][i] or "",
            genre=data["genres"][i] or "",
            duration_s=float(data["durations"][i] or 0),
            date_added=_parse_time(data["added"][i]),
            played_count=int(data["counts"][i] or 0),
            played_date=_parse_time(data["played"][i]),
        )
        for i, pid in enumerate(ids)
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
    temp_name = f"{TEMP_PREFIX}{uuid.uuid4().hex[:12]}"
    payload = json.dumps(
        {
            "name": name.strip(),
            "fallback_name": fallback_name,
            "temp_name": temp_name,
            "folder": folder,
            "description": description,
            "track_ids": ids,
        },
        ensure_ascii=False,
    )
    try:
        data = json.loads(run_jxa("create_playlist.js", payload, runner=runner))
    except MusicError as exc:
        if not exc.timed_out:
            raise
        try:
            run_jxa("delete_temp_playlist.js", temp_name, runner=runner)
        except MusicError:
            raise MusicError(
                f"Music.app timed out building the playlist. A partial playlist named "
                f"\"{temp_name}\" may be in the \"{folder}\" folder; delete it if so.",
                timed_out=True,
            ) from exc
        raise MusicError(
            "Music.app timed out building the playlist, so nothing was created. "
            "Try fewer tracks.",
            timed_out=True,
        ) from exc
    return PlaylistResult(
        name=data["name"],
        persistent_id=data["persistent_id"],
        track_count=int(data["track_count"]),
        missing_ids=list(data["missing_ids"]),
    )
