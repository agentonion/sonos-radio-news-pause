"""Shared Sonos helpers for news pause and live status."""

from __future__ import annotations

import json
import logging
import time
import tomllib
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import soco
from soco import SoCo
from soco.exceptions import SoCoException
from soco.groups import ZoneGroup

CONFIG_PATH = Path(__file__).with_name("config.toml")
PAUSE_STATE_PATH = Path(__file__).with_name("pause_state.json")
LOG = logging.getLogger("sonos_common")

# Text metadata fallbacks (title/artist/album/media info).
DEFAULT_STATION_MATCH: tuple[str, ...] = ("radio 2", "bbc radio 2", "bbc radio2")

# Prefer these URI / service ID fragments when available (TuneIn + BBC Sounds).
DEFAULT_STATION_URI_MATCH: tuple[str, ...] = ("s24940", "bbc_radio_two")

# Minimum seconds after :00 that still count as the trigger window.
# Clocks / sleep wakeups can land a few seconds late; lead_seconds alone is often too tight.
DEFAULT_TOP_OF_HOUR_WINDOW_SECONDS = 15

RESUMABLE_STATES = frozenset({"PAUSED_PLAYBACK", "STOPPED"})

# SoCo UPnP calls commonly surface these; keep broader OSError for network flakes.
SONOS_ERRORS = (OSError, TimeoutError, SoCoException)


@dataclass(frozen=True)
class AppConfig:
    room_name: str = ""
    pause_minutes: float = 6.0
    station_match: tuple[str, ...] = DEFAULT_STATION_MATCH
    station_uri_match: tuple[str, ...] = DEFAULT_STATION_URI_MATCH
    timezone: str = "Europe/London"
    lead_seconds: int = 5
    top_of_hour_window_seconds: int = DEFAULT_TOP_OF_HOUR_WINDOW_SECONDS
    discovery_timeout: float = 5.0
    discovery_retries: int = 3
    discovery_backoff: float = 1.0

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> AppConfig:
        station_match = data.get("station_match", DEFAULT_STATION_MATCH)
        station_uri_match = data.get("station_uri_match", DEFAULT_STATION_URI_MATCH)
        return cls(
            room_name=str(data.get("room_name", "") or ""),
            pause_minutes=float(data.get("pause_minutes", 6)),
            station_match=tuple(str(p) for p in station_match),
            station_uri_match=tuple(str(p) for p in station_uri_match),
            timezone=str(data.get("timezone", "Europe/London") or "Europe/London"),
            lead_seconds=int(data.get("lead_seconds", 5)),
            top_of_hour_window_seconds=int(
                data.get("top_of_hour_window_seconds", DEFAULT_TOP_OF_HOUR_WINDOW_SECONDS)
            ),
            discovery_timeout=float(data.get("discovery_timeout", 5)),
            discovery_retries=max(int(data.get("discovery_retries", 3)), 1),
            discovery_backoff=float(data.get("discovery_backoff", 1.0)),
        )


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

    def to_dict(self) -> dict[str, str]:
        return {
            "uid": self.uid,
            "resume_at": self.resume_at.astimezone(UTC).isoformat(),
            "player_name": self.player_name,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PauseIntent:
        resume_at = datetime.fromisoformat(str(data["resume_at"]))
        if resume_at.tzinfo is None:
            resume_at = resume_at.replace(tzinfo=UTC)
        return cls(
            uid=str(data["uid"]),
            resume_at=resume_at,
            player_name=str(data.get("player_name") or ""),
        )


@dataclass(frozen=True)
class Radio2Target:
    speaker: SoCo
    match: StationMatch


@dataclass(frozen=True)
class PauseAttemptResult:
    """Outcome of shared top-of-hour pause orchestration."""

    status: Literal["skipped", "paused"]
    player_name: str = ""
    evidence: str = ""
    outcome: str = ""
    detail: str = ""


def load_config(path: Path = CONFIG_PATH) -> AppConfig:
    with path.open("rb") as f:
        return AppConfig.from_mapping(tomllib.load(f))


def station_patterns(config: AppConfig) -> list[str]:
    return list(config.station_match)


def station_uri_patterns(config: AppConfig) -> list[str]:
    return list(config.station_uri_match)


def discovery_settings(config: AppConfig) -> tuple[float, int, float]:
    return config.discovery_timeout, config.discovery_retries, config.discovery_backoff


def discover_speakers(
    timeout: float = 5,
    retries: int = 3,
    backoff: float = 1.0,
) -> list[SoCo]:
    """Discover Sonos speakers, retrying with short backoff on empty results."""
    for attempt in range(max(retries, 1)):
        speakers = list(soco.discover(timeout=timeout) or [])
        if speakers:
            if attempt > 0:
                LOG.info("Discovery succeeded on attempt %d.", attempt + 1)
            return speakers
        if attempt + 1 < retries:
            delay = backoff * (attempt + 1)
            LOG.warning(
                "No speakers found (attempt %d/%d); retrying in %.1fs.",
                attempt + 1,
                retries,
                delay,
            )
            time.sleep(delay)

    raise RuntimeError("No Sonos speakers found on the local network.")


def discover_speakers_from_config(config: AppConfig) -> list[SoCo]:
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


def _track_blob_parts(track: Mapping[str, Any]) -> list[str]:
    return [str(track.get(key) or "") for key in ("title", "artist", "album", "uri")]


def fetch_media(speaker: SoCo) -> tuple[dict[str, Any], str, str]:
    """Fetch track + media info once. Returns (track, uri, casefolded blob)."""
    track: dict[str, Any] = {}
    try:
        track = dict(speaker.get_current_track_info() or {})
    except SONOS_ERRORS as exc:
        LOG.debug("track info failed on %s: %s", speaker.player_name, exc)

    uri = str(track.get("uri") or "")
    parts = _track_blob_parts(track)

    try:
        media = speaker.get_current_media_info() or {}
        parts.extend(str(value or "") for value in media.values())
    except SONOS_ERRORS as exc:
        LOG.debug("media info failed on %s: %s", speaker.player_name, exc)

    return track, uri, " ".join(parts).casefold()


def media_blob(speaker: SoCo) -> str:
    return fetch_media(speaker)[2]


def track_uri(speaker: SoCo) -> str:
    return fetch_media(speaker)[1]


def transport_state(speaker: SoCo) -> str:
    try:
        return speaker.get_current_transport_info().get("current_transport_state", "UNKNOWN")
    except SONOS_ERRORS as exc:
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


def radio_2_match(
    speaker: SoCo,
    patterns: list[str],
    uri_patterns: list[str] | None = None,
) -> StationMatch:
    _track, uri, blob = fetch_media(speaker)
    return match_station(
        blob,
        uri,
        patterns,
        uri_patterns if uri_patterns is not None else list(DEFAULT_STATION_URI_MATCH),
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
    track, uri, blob = fetch_media(target)
    match = match_station(
        blob,
        uri,
        patterns,
        uri_patterns if uri_patterns is not None else list(DEFAULT_STATION_URI_MATCH),
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
        if not is_playing(target):
            continue
        if radio_2_match(target, patterns, uri_patterns).matched:
            return target
        playing.append(target)
    if playing:
        return playing[0]
    return coordinator(speakers[0])


def find_radio_2_target(
    speakers: list[SoCo],
    room_name: str,
    patterns: list[str],
    uri_patterns: list[str] | None = None,
) -> Radio2Target | None:
    """Return the playing Radio 2 coordinator and its match evidence, if any."""
    target = select_target(speakers, room_name, patterns, uri_patterns)
    if not is_playing(target):
        return None
    match = radio_2_match(target, patterns, uri_patterns)
    if not match.matched:
        return None
    return Radio2Target(speaker=target, match=match)


def log_match_miss(
    speakers: list[SoCo],
    room_name: str,
    patterns: list[str],
    uri_patterns: list[str],
) -> None:
    """Debug-log media blobs when Radio 2 is not detected at the top of the hour."""
    try:
        target = select_target(speakers, room_name, patterns, uri_patterns)
    except (RuntimeError, OSError, TimeoutError, SoCoException) as exc:
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
    except SONOS_ERRORS:
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
    except SONOS_ERRORS:
        LOG.exception("Resume retry failed on %s.", target.player_name)
        return "resume_failed"
    return "resumed"


def sleep_until(deadline: datetime, *, max_chunk: float = 30.0) -> None:
    """Sleep until a wall-clock deadline, rechecking so Mac sleep cannot overrun."""
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    while True:
        remaining = (deadline - datetime.now(UTC)).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(remaining, max_chunk))


def pause_for_news(target: SoCo, pause_minutes: float) -> str:
    """Pause target, persist intent, wait, resume if still paused/stopped."""
    resume_at = datetime.now(UTC) + timedelta(minutes=pause_minutes)
    intent = PauseIntent(uid=target.uid, resume_at=resume_at, player_name=target.player_name)
    write_pause_state(intent)

    LOG.info(
        "Pausing %s for %.1f minutes (Radio 2 news).",
        target.player_name,
        pause_minutes,
    )
    try:
        target.pause()
        sleep_until(resume_at)
        outcome = resume_if_owned(target)
        LOG.info("Hourly pause outcome on %s: %s", target.player_name, outcome)
        return outcome
    except SONOS_ERRORS:
        LOG.exception("Pause/resume failed on %s.", target.player_name)
        raise
    finally:
        clear_pause_state()


def attempt_news_pause(
    config: AppConfig,
    *,
    speakers: list[SoCo] | None = None,
    on_target: Callable[[Radio2Target], None] | None = None,
    now: datetime | None = None,
) -> PauseAttemptResult:
    """Shared daemon/live orchestration: discover → match → pause_for_news."""
    discovered = speakers if speakers is not None else discover_speakers_from_config(config)
    patterns = station_patterns(config)
    uri_patterns = station_uri_patterns(config)
    found = find_radio_2_target(discovered, config.room_name, patterns, uri_patterns)
    if found is None:
        LOG.info("Radio 2 not playing — nothing to pause.")
        log_match_miss(discovered, config.room_name, patterns, uri_patterns)
        return PauseAttemptResult(status="skipped", detail="Radio 2 not playing")

    # Scheduled callers pass ``now`` so a late wake pauses only the remaining
    # news time. Manual ``--once`` omits ``now`` and uses the full duration.
    if now is not None:
        pause_minutes = remaining_pause_minutes(now, config.pause_minutes)
        if pause_minutes <= 0:
            LOG.info("News window already over — nothing to pause.")
            return PauseAttemptResult(status="skipped", detail="News window over")
    else:
        pause_minutes = config.pause_minutes

    LOG.info(
        "Matched Radio 2 via %s on %s.",
        found.match.evidence or "unknown",
        found.speaker.player_name,
    )
    if on_target is not None:
        on_target(found)
    outcome = pause_for_news(found.speaker, pause_minutes)
    return PauseAttemptResult(
        status="paused",
        player_name=found.speaker.player_name,
        evidence=found.match.evidence,
        outcome=outcome,
    )


def recover_orphaned_pause(
    speakers: list[SoCo] | None = None,
    *,
    config: AppConfig | None = None,
    state_path: Path = PAUSE_STATE_PATH,
) -> str | None:
    """On startup, finish a pause left behind by a KeepAlive restart."""
    intent = load_pause_state(state_path)
    if intent is None:
        return None

    now = datetime.now(UTC)
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
        sleep_until(intent.resume_at)

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


def current_hour_mark(now: datetime) -> datetime:
    return now.replace(minute=0, second=0, microsecond=0)


def top_of_hour_window_seconds(lead_seconds: int, window_seconds: int | None = None) -> int:
    floor = DEFAULT_TOP_OF_HOUR_WINDOW_SECONDS if window_seconds is None else window_seconds
    return max(lead_seconds, floor)


def in_top_of_hour_window(
    now: datetime,
    lead_seconds: int,
    window_seconds: int | None = None,
) -> bool:
    limit = top_of_hour_window_seconds(lead_seconds, window_seconds)
    return now.minute == 0 and now.second <= limit


def news_elapsed_seconds(now: datetime) -> float:
    """Seconds since the current hour mark (:00)."""
    return now.minute * 60 + now.second + now.microsecond / 1_000_000


def in_news_window(now: datetime, pause_minutes: float) -> bool:
    """True while wall-clock is inside the pause duration after :00."""
    return news_elapsed_seconds(now) < pause_minutes * 60


def remaining_pause_minutes(now: datetime, pause_minutes: float) -> float:
    """Minutes left in the news window (0 when outside it)."""
    remaining = pause_minutes * 60 - news_elapsed_seconds(now)
    return max(remaining, 0.0) / 60.0


def seconds_until_next_hour(
    now: datetime,
    lead_seconds: int,
    window_seconds: int | None = None,
    *,
    pause_minutes: float | None = None,
) -> float:
    """Seconds until we should wake for the next news-pause attempt.

    Returns 0 inside the top-of-hour trigger window, and also during the news
    window when ``pause_minutes`` is set (catch-up after Mac sleep / late wake).
    """
    if in_top_of_hour_window(now, lead_seconds, window_seconds):
        return 0.0
    if pause_minutes is not None and in_news_window(now, pause_minutes):
        return 0.0

    upcoming = next_hour_mark(now)
    check_at = upcoming - timedelta(seconds=lead_seconds)

    # Already inside the lead-up to :00 — wait until the mark (or run now if past it).
    if now >= check_at:
        return max((upcoming - now).total_seconds(), 0.0)

    return (check_at - now).total_seconds()


def should_trigger_news_pause(
    now: datetime,
    config: AppConfig,
) -> bool:
    """Single scheduling rule for daemon and live view.

    Prefer the tight top-of-hour window; also allow catch-up anytime during the
    configured news duration so a Mac that slept through :00 can still pause.
    """
    if in_top_of_hour_window(
        now,
        config.lead_seconds,
        config.top_of_hour_window_seconds,
    ):
        return True
    return in_news_window(now, config.pause_minutes)
