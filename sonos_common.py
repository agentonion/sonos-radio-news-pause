"""Shared Sonos helpers for news pause and live status."""

from __future__ import annotations

import logging
import time
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import soco
from soco import SoCo
from soco.groups import ZoneGroup

CONFIG_PATH = Path(__file__).with_name("config.toml")
LOG = logging.getLogger("sonos_common")
DEFAULT_STATION_MATCH = ["radio 2"]


@dataclass(frozen=True)
class NowPlaying:
    room: str
    group: str
    state: str
    title: str
    artist: str
    album: str
    uri: str
    is_radio_2: bool
    speaker: SoCo

    @property
    def label(self) -> str:
        if self.title and self.artist:
            return f"{self.title} — {self.artist}"
        return self.title or self.artist or "(nothing)"


def load_config() -> dict:
    with CONFIG_PATH.open("rb") as f:
        return tomllib.load(f)


def station_patterns(config: dict) -> list[str]:
    return list(config.get("station_match", DEFAULT_STATION_MATCH))


def discover_speakers(timeout: float = 5) -> list[SoCo]:
    speakers = list(soco.discover(timeout=timeout) or [])
    if not speakers:
        raise RuntimeError("No Sonos speakers found on the local network.")
    return speakers


def speaker_for_room(speakers: list[SoCo], room_name: str) -> SoCo:
    wanted = room_name.strip().casefold()
    for speaker in speakers:
        if speaker.player_name.casefold() == wanted:
            return speaker
    names = ", ".join(sorted(s.player_name for s in speakers))
    raise RuntimeError(f"Room {room_name!r} not found. Available: {names}")


def coordinator(speaker: SoCo) -> SoCo:
    group: ZoneGroup | None = speaker.group
    return group.coordinator if group else speaker


def iter_coordinators(speakers: list[SoCo]) -> Iterator[SoCo]:
    seen: set[str] = set()
    for speaker in speakers:
        target = coordinator(speaker)
        if target.uid in seen:
            continue
        seen.add(target.uid)
        yield target


def media_blob(speaker: SoCo) -> str:
    parts: list[str] = []
    try:
        track = speaker.get_current_track_info() or {}
        parts.extend(
            str(track.get(key) or "")
            for key in ("title", "artist", "album", "uri", "playlist_position")
        )
    except Exception as exc:  # noqa: BLE001
        LOG.debug("track info failed on %s: %s", speaker.player_name, exc)

    try:
        media = speaker.get_current_media_info() or {}
        parts.extend(str(value or "") for value in media.values())
    except Exception as exc:  # noqa: BLE001
        LOG.debug("media info failed on %s: %s", speaker.player_name, exc)

    return " ".join(parts).casefold()


def transport_state(speaker: SoCo) -> str:
    try:
        return speaker.get_current_transport_info().get("current_transport_state", "UNKNOWN")
    except Exception as exc:  # noqa: BLE001
        LOG.debug("transport info failed on %s: %s", speaker.player_name, exc)
        return "UNKNOWN"


def is_playing(speaker: SoCo) -> bool:
    return transport_state(speaker) == "PLAYING"


def matches_radio_2(speaker: SoCo, patterns: list[str]) -> bool:
    blob = media_blob(speaker)
    return any(pattern.casefold() in blob for pattern in patterns)


def group_label(speaker: SoCo) -> str:
    if not speaker.group:
        return speaker.player_name
    names = sorted({m.player_name for m in speaker.group.members}, key=str.casefold)
    return ", ".join(names)


def now_playing_for(speaker: SoCo, patterns: list[str]) -> NowPlaying:
    target = coordinator(speaker)
    track: dict = {}
    try:
        track = target.get_current_track_info() or {}
    except Exception as exc:  # noqa: BLE001
        LOG.debug("track info failed on %s: %s", target.player_name, exc)
    return NowPlaying(
        room=target.player_name,
        group=group_label(target),
        state=transport_state(target),
        title=str(track.get("title") or ""),
        artist=str(track.get("artist") or ""),
        album=str(track.get("album") or ""),
        uri=str(track.get("uri") or ""),
        is_radio_2=matches_radio_2(target, patterns),
        speaker=target,
    )


def select_target(speakers: list[SoCo], room_name: str, patterns: list[str]) -> SoCo:
    """Prefer configured room, else a playing Radio 2 group, else any playing group."""
    if room_name.strip():
        return coordinator(speaker_for_room(speakers, room_name))

    playing: list[SoCo] = []
    for target in iter_coordinators(speakers):
        if is_playing(target) and matches_radio_2(target, patterns):
            return target
        if is_playing(target):
            playing.append(target)
    if playing:
        return playing[0]
    return coordinator(speakers[0])


def find_radio_2_target(speakers: list[SoCo], room_name: str, patterns: list[str]) -> SoCo | None:
    target = select_target(speakers, room_name, patterns)
    if is_playing(target) and matches_radio_2(target, patterns):
        return target
    return None


def pause_for_news(target: SoCo, pause_minutes: float) -> str:
    """Pause target, wait, resume only if still paused. Returns outcome label."""
    LOG.info(
        "Pausing %s for %.1f minutes (Radio 2 news).",
        target.player_name,
        pause_minutes,
    )
    target.pause()
    time.sleep(pause_minutes * 60)

    state = transport_state(target)
    if state == "PAUSED_PLAYBACK":
        LOG.info("Resuming %s after news.", target.player_name)
        target.play()
        return "resumed"

    LOG.info("Not resuming %s — transport state is %s.", target.player_name, state)
    return state


def next_hour_mark(now: datetime) -> datetime:
    return now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)


def in_top_of_hour_window(now: datetime, lead_seconds: int) -> bool:
    return now.minute == 0 and now.second <= max(lead_seconds, 15)


def seconds_until_next_hour(now: datetime, lead_seconds: int) -> float:
    """Seconds until we should wake for the next top-of-hour bulletin."""
    if in_top_of_hour_window(now, lead_seconds):
        return 0.0

    upcoming = next_hour_mark(now)
    check_at = upcoming - timedelta(seconds=lead_seconds)

    # Already inside the lead-up to :00 — wait until the mark (or run now if past it).
    if now >= check_at:
        return max((upcoming - now).total_seconds(), 0.0)

    return (check_at - now).total_seconds()


def in_news_window(now: datetime, pause_minutes: float) -> bool:
    elapsed = now.minute * 60 + now.second + now.microsecond / 1_000_000
    return elapsed < pause_minutes * 60
