"""Shared Sonos helpers for news pause and live status."""

from __future__ import annotations

import json
import logging
import time
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import soco
from soco import SoCo
from soco.groups import ZoneGroup

CONFIG_PATH = Path(__file__).with_name("config.toml")
PAUSE_STATE_PATH = Path(__file__).with_name("pause_state.json")
LOG = logging.getLogger("sonos_common")

# Text metadata fallbacks (title/artist/album/media info).
DEFAULT_STATION_MATCH = ["radio 2", "bbc radio 2", "bbc radio2"]

# Prefer these URI / service ID fragments when available (TuneIn + BBC Sounds).
DEFAULT_STATION_URI_MATCH = ["s24940", "bbc_radio_two"]

RESUMABLE_STATES = frozenset({"PAUSED_PLAYBACK", "STOPPED"})


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
    match_evidence: str = ""

    @property
    def label(self) -> str:
        if self.title and self.artist:
            return f"{self.title} — {self.artist}"
        return self.title or self.artist or "(nothing)"


@dataclass(frozen=True)
class StationMatch:
    matched: bool
    evidence: str
    blob: str
    uri: str


@dataclass(frozen=True)
class PauseIntent:
    uid: str
    resume_at: datetime
    player_name: str = ""

    def to_dict(self) -> dict:
        return {
            "uid": self.uid,
            "resume_at": self.resume_at.astimezone(timezone.utc).isoformat(),
            "player_name": self.player_name,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PauseIntent:
        resume_at = datetime.fromisoformat(str(data["resume_at"]))
        if resume_at.tzinfo is None:
            resume_at = resume_at.replace(tzinfo=timezone.utc)
        return cls(
            uid=str(data["uid"]),
            resume_at=resume_at,
            player_name=str(data.get("player_name") or ""),
        )


def load_config() -> dict:
    with CONFIG_PATH.open("rb") as f:
        return tomllib.load(f)


def station_patterns(config: dict) -> list[str]:
    return list(config.get("station_match", DEFAULT_STATION_MATCH))


def station_uri_patterns(config: dict) -> list[str]:
    return list(config.get("station_uri_match", DEFAULT_STATION_URI_MATCH))


def discovery_settings(config: dict) -> tuple[float, int, float]:
    timeout = float(config.get("discovery_timeout", 5))
    retries = max(int(config.get("discovery_retries", 3)), 1)
    backoff = float(config.get("discovery_backoff", 1.0))
    return timeout, retries, backoff


def discover_speakers(
    timeout: float = 5,
    retries: int = 3,
    backoff: float = 1.0,
) -> list[SoCo]:
    """Discover Sonos speakers, retrying with short backoff on empty results."""
    last_empty = False
    for attempt in range(max(retries, 1)):
        speakers = list(soco.discover(timeout=timeout) or [])
        if speakers:
            if attempt > 0:
                LOG.info("Discovery succeeded on attempt %d.", attempt + 1)
            return speakers
        last_empty = True
        if attempt + 1 < retries:
            delay = backoff * (attempt + 1)
            LOG.warning(
                "No speakers found (attempt %d/%d); retrying in %.1fs.",
                attempt + 1,
                retries,
                delay,
            )
            time.sleep(delay)

    if last_empty:
        raise RuntimeError("No Sonos speakers found on the local network.")
    raise RuntimeError("Sonos discovery failed.")


def discover_speakers_from_config(config: dict) -> list[SoCo]:
    timeout, retries, backoff = discovery_settings(config)
    return discover_speakers(timeout=timeout, retries=retries, backoff=backoff)


def speaker_for_room(speakers: list[SoCo], room_name: str) -> SoCo:
    wanted = room_name.strip().casefold()
    for speaker in speakers:
        if speaker.player_name.casefold() == wanted:
            return speaker
    names = ", ".join(sorted(s.player_name for s in speakers))
    raise RuntimeError(f"Room {room_name!r} not found. Available: {names}")


def speaker_by_uid(speakers: list[SoCo], uid: str) -> SoCo | None:
    for speaker in speakers:
        if speaker.uid == uid:
            return coordinator(speaker)
    return None


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


def track_uri(speaker: SoCo) -> str:
    try:
        track = speaker.get_current_track_info() or {}
        return str(track.get("uri") or "")
    except Exception as exc:  # noqa: BLE001
        LOG.debug("track uri failed on %s: %s", speaker.player_name, exc)
        return ""


def transport_state(speaker: SoCo) -> str:
    try:
        return speaker.get_current_transport_info().get("current_transport_state", "UNKNOWN")
    except Exception as exc:  # noqa: BLE001
        LOG.debug("transport info failed on %s: %s", speaker.player_name, exc)
        return "UNKNOWN"


def is_playing(speaker: SoCo) -> bool:
    return transport_state(speaker) == "PLAYING"


def match_station(
    blob: str,
    uri: str,
    text_patterns: list[str],
    uri_patterns: list[str],
) -> StationMatch:
    """Prefer URI / TuneIn / BBC Sounds IDs, then fall back to text metadata."""
    uri_cf = uri.casefold()
    blob_cf = blob.casefold()

    for pattern in uri_patterns:
        needle = pattern.casefold()
        if needle and needle in uri_cf:
            return StationMatch(True, f"uri:{pattern}", blob_cf, uri)

    for pattern in text_patterns:
        needle = pattern.casefold()
        if needle and needle in blob_cf:
            return StationMatch(True, f"text:{pattern}", blob_cf, uri)

    return StationMatch(False, "", blob_cf, uri)


def matches_radio_2(
    speaker: SoCo,
    patterns: list[str],
    uri_patterns: list[str] | None = None,
) -> bool:
    return radio_2_match(speaker, patterns, uri_patterns).matched


def radio_2_match(
    speaker: SoCo,
    patterns: list[str],
    uri_patterns: list[str] | None = None,
) -> StationMatch:
    return match_station(
        media_blob(speaker),
        track_uri(speaker),
        patterns,
        uri_patterns if uri_patterns is not None else DEFAULT_STATION_URI_MATCH,
    )


def group_label(speaker: SoCo) -> str:
    if not speaker.group:
        return speaker.player_name
    names = sorted({m.player_name for m in speaker.group.members}, key=str.casefold)
    return ", ".join(names)


def now_playing_for(
    speaker: SoCo,
    patterns: list[str],
    uri_patterns: list[str] | None = None,
) -> NowPlaying:
    target = coordinator(speaker)
    track: dict = {}
    try:
        track = target.get_current_track_info() or {}
    except Exception as exc:  # noqa: BLE001
        LOG.debug("track info failed on %s: %s", target.player_name, exc)
    uri = str(track.get("uri") or "")
    match = match_station(
        media_blob(target),
        uri,
        patterns,
        uri_patterns if uri_patterns is not None else DEFAULT_STATION_URI_MATCH,
    )
    return NowPlaying(
        room=target.player_name,
        group=group_label(target),
        state=transport_state(target),
        title=str(track.get("title") or ""),
        artist=str(track.get("artist") or ""),
        album=str(track.get("album") or ""),
        uri=uri,
        is_radio_2=match.matched,
        speaker=target,
        match_evidence=match.evidence,
    )


def select_target(
    speakers: list[SoCo],
    room_name: str,
    patterns: list[str],
    uri_patterns: list[str] | None = None,
) -> SoCo:
    """Prefer configured room, else a playing Radio 2 group, else any playing group."""
    if room_name.strip():
        return coordinator(speaker_for_room(speakers, room_name))

    playing: list[SoCo] = []
    for target in iter_coordinators(speakers):
        if is_playing(target) and matches_radio_2(target, patterns, uri_patterns):
            return target
        if is_playing(target):
            playing.append(target)
    if playing:
        return playing[0]
    return coordinator(speakers[0])


def find_radio_2_target(
    speakers: list[SoCo],
    room_name: str,
    patterns: list[str],
    uri_patterns: list[str] | None = None,
) -> SoCo | None:
    target = select_target(speakers, room_name, patterns, uri_patterns)
    if is_playing(target) and matches_radio_2(target, patterns, uri_patterns):
        return target
    return None


def log_match_miss(speakers: list[SoCo], room_name: str, patterns: list[str], uri_patterns: list[str]) -> None:
    """Debug-log media blobs when Radio 2 is not detected at the top of the hour."""
    try:
        target = select_target(speakers, room_name, patterns, uri_patterns)
    except Exception as exc:  # noqa: BLE001
        LOG.debug("Could not select target for match miss logging: %s", exc)
        return
    match = radio_2_match(target, patterns, uri_patterns)
    LOG.debug(
        "Radio 2 match failed on %s (state=%s uri=%r blob=%r)",
        target.player_name,
        transport_state(target),
        match.uri,
        match.blob,
    )


def write_pause_state(intent: PauseIntent, path: Path = PAUSE_STATE_PATH) -> None:
    path.write_text(json.dumps(intent.to_dict(), indent=2) + "\n", encoding="utf-8")


def clear_pause_state(path: Path = PAUSE_STATE_PATH) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        LOG.warning("Could not clear pause state %s: %s", path, exc)


def load_pause_state(path: Path = PAUSE_STATE_PATH) -> PauseIntent | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return PauseIntent.from_dict(data)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        LOG.warning("Ignoring corrupt pause state %s: %s", path, exc)
        clear_pause_state(path)
        return None


def resume_if_owned(target: SoCo) -> str:
    """Resume when transport is paused/stopped after a pause we initiated.

    Radio / TuneIn streams often report STOPPED instead of PAUSED_PLAYBACK.
    play() can also no-op once; retry once if we are still not PLAYING.
    """
    state = transport_state(target)
    if state not in RESUMABLE_STATES:
        LOG.info("Not resuming %s — transport state is %s.", target.player_name, state)
        return state

    LOG.info("Resuming %s after news (was %s).", target.player_name, state)
    try:
        target.play()
    except Exception:
        LOG.exception("Resume play() failed on %s.", target.player_name)
        return "resume_failed"

    time.sleep(1.5)
    after = transport_state(target)
    if after == "PLAYING":
        return "resumed"

    LOG.warning(
        "Resume issued on %s but state is %s; retrying play().",
        target.player_name,
        after,
    )
    try:
        target.play()
    except Exception:
        LOG.exception("Resume retry failed on %s.", target.player_name)
        return "resume_failed"
    return "resumed"


def pause_for_news(target: SoCo, pause_minutes: float) -> str:
    """Pause target, persist intent, wait, resume if still paused/stopped."""
    resume_at = datetime.now(timezone.utc) + timedelta(minutes=pause_minutes)
    intent = PauseIntent(uid=target.uid, resume_at=resume_at, player_name=target.player_name)
    write_pause_state(intent)

    LOG.info(
        "Pausing %s for %.1f minutes (Radio 2 news).",
        target.player_name,
        pause_minutes,
    )
    try:
        target.pause()
        time.sleep(pause_minutes * 60)
        outcome = resume_if_owned(target)
        LOG.info("Hourly pause outcome on %s: %s", target.player_name, outcome)
        return outcome
    except Exception:
        LOG.exception("Pause/resume failed on %s.", target.player_name)
        raise
    finally:
        clear_pause_state()


def recover_orphaned_pause(
    speakers: list[SoCo] | None = None,
    *,
    config: dict | None = None,
    state_path: Path = PAUSE_STATE_PATH,
) -> str | None:
    """On startup, finish a pause left behind by a KeepAlive restart."""
    intent = load_pause_state(state_path)
    if intent is None:
        return None

    now = datetime.now(timezone.utc)
    remaining = (intent.resume_at - now).total_seconds()
    LOG.info(
        "Found persisted pause for %s (uid=%s); resume_at=%s (%.0fs remaining).",
        intent.player_name or "speaker",
        intent.uid,
        intent.resume_at.isoformat(),
        max(remaining, 0),
    )

    if remaining > 0:
        LOG.info("Waiting %.0fs before resume recovery.", remaining)
        time.sleep(remaining)

    try:
        if speakers is None:
            speakers = discover_speakers_from_config(config or load_config())
        target = speaker_by_uid(speakers, intent.uid)
        if target is None:
            LOG.warning(
                "Could not find speaker uid=%s (%s) to resume; clearing pause state.",
                intent.uid,
                intent.player_name,
            )
            return "missing"
        return resume_if_owned(target)
    finally:
        clear_pause_state(state_path)


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
