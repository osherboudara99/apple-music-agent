"""The six tools Claude can call. Handlers return JSON-serializable dicts."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from . import periods, queries
from .config import Config
from .music import MusicError, PlaylistResult
from .snapshot import SnapshotResult
from .store import Store

log = logging.getLogger(__name__)

_WINDOW_PROPS = {
    "period": {
        "type": "string",
        "enum": list(periods.PERIOD_NAMES),
        "description": "Named time window. Use this OR start/end, not both.",
    },
    "start": {
        "type": "string",
        "description": "Start date or datetime in ISO 8601, local time, e.g. 2026-09-01.",
    },
    "end": {
        "type": "string",
        "description": "End date (that whole day is included) or datetime, local time. "
        "Defaults to now.",
    },
}
_GENRE_PROPS = {
    "genre_family": {
        "type": "string",
        "description": "Broad genre family such as rock, hip-hop, electronic, pop, r&b, jazz. "
        "Use list_genres to see the user's genres and their families.",
    },
    "genres": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Exact genre names to include (combined with genre_family using OR).",
    },
}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict], dict]

    def to_api(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


@dataclass
class ToolContext:
    store: Store
    config: Config
    clock: Callable[[], datetime]
    refresh: Callable[[], SnapshotResult | None]
    create_playlist: Callable[..., PlaylistResult]


def _schema(properties: dict, required: list[str] | None = None) -> dict:
    schema = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        schema["required"] = required
    return schema


class Toolbox:
    def __init__(self, ctx: ToolContext):
        self.ctx = ctx
        self.specs = {spec.name: spec for spec in self._build()}

    def definitions(self) -> list[dict]:
        return [spec.to_api() for spec in self.specs.values()]

    def run(self, name: str, args: dict) -> dict:
        spec = self.specs.get(name)
        if spec is None:
            result = {"error": f"Unknown tool: {name}"}
        else:
            try:
                result = spec.handler(args or {})
            except (ValueError, MusicError) as exc:
                result = {"error": str(exc)}
            except Exception as exc:  # report to Claude instead of crashing the chat
                log.exception("tool %s failed", name)
                result = {"error": f"Internal error in {name}: {exc}"}
        result["now"] = queries.local_iso(self.ctx.clock(), self.ctx.config.tz)
        return result

    # --- helpers -------------------------------------------------------------

    def _period(self, args: dict, required: bool = True) -> periods.Period | None:
        if not required and not (args.get("period") or args.get("start")):
            return None
        return periods.resolve(
            args.get("period"), args.get("start"), args.get("end"),
            self.ctx.config.tz, self.ctx.clock(),
        )

    def _refresh_for(self, period: periods.Period | None) -> dict:
        if period is None or period.end <= self.ctx.clock() - timedelta(hours=24):
            return {}
        stale = (
            "Could not refresh from Music.app; data may be up to "
            f"{self.ctx.config.snapshot_interval_minutes} minutes old"
        )
        try:
            result = self.ctx.refresh()
        except MusicError as exc:
            return {"warning": f"{stale}: {exc}"}
        if result is not None and result.error:
            return {"warning": f"{stale}: {result.error}"}
        return {}

    # --- handlers ------------------------------------------------------------

    def _listening_stats(self, args: dict) -> dict:
        period = self._period(args)
        extra = self._refresh_for(period)
        stats = queries.listening_stats(
            self.ctx.store, period, self.ctx.config.tz, args.get("genre_family"), args.get("genres")
        )
        return {**stats, **extra}

    def _played_tracks(self, args: dict) -> dict:
        period = self._period(args)
        extra = self._refresh_for(period)
        limit = max(1, min(int(args.get("limit", 200)), 500))
        rows = queries.played_tracks(
            self.ctx.store, period, args.get("genre_family"), args.get("genres")
        )
        tz = self.ctx.config.tz
        return {
            "period": {
                "label": period.label,
                "start": queries.local_iso(period.start, tz),
                "end": queries.local_iso(period.end, tz),
            },
            "count": min(len(rows), limit),
            "truncated": len(rows) > limit,
            "tracks": [r.as_dict(tz) for r in rows[:limit]],
            **extra,
        }

    def _all_time_top(self, args: dict) -> dict:
        limit = max(1, min(int(args.get("limit", 10)), 50))
        return {"results": queries.all_time_top(self.ctx.store, args.get("by", "track"), limit)}

    def _list_genres(self, args: dict) -> dict:
        period = self._period(args, required=False)
        extra = self._refresh_for(period)
        return {"genres": queries.list_genres(self.ctx.store, period), **extra}

    def _search_library(self, args: dict) -> dict:
        limit = max(1, min(int(args.get("limit", 50)), 200))
        results = queries.search_library(
            self.ctx.store, args.get("query"), args.get("artist"), args.get("genre"), limit
        )
        return {"results": results}

    def _create_playlist(self, args: dict) -> dict:
        name = str(args.get("name", ""))
        track_ids = [str(t) for t in args.get("track_ids", [])]
        today = self.ctx.clock().astimezone(self.ctx.config.tz).date().isoformat()
        result = self.ctx.create_playlist(
            name=name,
            track_ids=track_ids,
            folder=self.ctx.config.playlist_folder,
            fallback_name=f"{name} ({today})",
            description=str(args.get("description", "")),
        )
        missing = set(result.missing_ids)
        present = [t for t in dict.fromkeys(track_ids) if t not in missing]
        names = queries.track_names(self.ctx.store, present[:5])
        return {
            "name": result.name,
            "folder": self.ctx.config.playlist_folder,
            "track_count": result.track_count,
            "missing_track_ids": result.missing_ids,
            "sample_tracks": [names[t] for t in present[:5] if t in names],
        }

    def _build(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                "listening_stats",
                "Listening statistics for a time window: plays, distinct tracks and artists, top "
                "tracks/artists/genres. plays_is_lower_bound=true means repeat plays before "
                "plays_counted_since are not known.",
                _schema({**_WINDOW_PROPS, **_GENRE_PROPS}),
                self._listening_stats,
            ),
            ToolSpec(
                "played_tracks",
                "Tracks played in a time window with their ids (for create_playlist), sorted by "
                "plays in the window.",
                _schema(
                    {
                        **_WINDOW_PROPS,
                        **_GENRE_PROPS,
                        "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                    }
                ),
                self._played_tracks,
            ),
            ToolSpec(
                "all_time_top",
                "All-time most played tracks, artists or genres from the library's lifetime "
                "play counts.",
                _schema(
                    {
                        "by": {"type": "string", "enum": ["track", "artist", "genre"]},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                    },
                    ["by"],
                ),
                self._all_time_top,
            ),
            ToolSpec(
                "list_genres",
                "The user's genres with their family, track and play counts; library-wide, or "
                "for a time window if period/start is given.",
                _schema(_WINDOW_PROPS),
                self._list_genres,
            ),
            ToolSpec(
                "search_library",
                "Search the whole library by text (title/artist/album), artist, or genre/genre "
                "family. Returns track ids for create_playlist.",
                _schema(
                    {
                        "query": {"type": "string"},
                        "artist": {"type": "string"},
                        "genre": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                    }
                ),
                self._search_library,
            ),
            ToolSpec(
                "create_playlist",
                "Create a NEW playlist from track ids (from played_tracks or search_library). "
                "Never modifies existing playlists. A date is appended if the name is taken.",
                _schema(
                    {
                        "name": {"type": "string"},
                        "track_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                            "maxItems": 500,
                        },
                        "description": {"type": "string"},
                    },
                    ["name", "track_ids"],
                ),
                self._create_playlist,
            ),
        ]
