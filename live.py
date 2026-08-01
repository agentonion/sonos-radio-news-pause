#!/usr/bin/env python3
"""Live terminal view of Sonos playback + hourly Radio 2 news pause."""

from __future__ import annotations

import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from rich.align import Align
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from soco import SoCo

from sonos_common import (
    CONFIG_PATH,
    AppConfig,
    Radio2Target,
    attempt_news_pause,
    discover_speakers_from_config,
    load_config,
    next_hour_mark,
    now_playing_for,
    select_target,
    should_trigger_news_pause,
    station_patterns,
    station_uri_patterns,
)

REFRESH_SECONDS = 1.0
SPEAKER_REDISCOVER_SECONDS = 60.0


class LiveResources:
    """Cache config (by mtime) and speakers so the 1 Hz UI does not SSDP every tick."""

    def __init__(self) -> None:
        self._config: AppConfig | None = None
        self._config_mtime: float | None = None
        self._speakers: list[SoCo] = []
        self._speakers_at = 0.0

    def get_config(self) -> AppConfig:
        try:
            mtime = CONFIG_PATH.stat().st_mtime
        except OSError:
            mtime = None
        if self._config is None or mtime != self._config_mtime:
            self._config = load_config()
            self._config_mtime = mtime
        return self._config

    def get_speakers(self, config: AppConfig, *, force: bool = False) -> list[SoCo]:
        now = time.monotonic()
        stale = now - self._speakers_at >= SPEAKER_REDISCOVER_SECONDS
        if force or not self._speakers or stale:
            self._speakers = discover_speakers_from_config(config)
            self._speakers_at = now
        return self._speakers


class NewsPauseController:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.status = "Waiting for top of hour"
        self.last_track = ""
        self.track_changed_at: datetime | None = None
        self._pause_thread: threading.Thread | None = None
        self._last_pause_hour: datetime | None = None

    def note_track(self, label: str, now: datetime) -> None:
        with self.lock:
            if label and label != self.last_track:
                if self.last_track:
                    self.track_changed_at = now
                self.last_track = label

    def set_status(self, status: str) -> None:
        with self.lock:
            self.status = status

    def maybe_start_pause(
        self,
        config: AppConfig,
        now: datetime,
        speakers: list[SoCo] | None = None,
    ) -> None:
        # Same rule as the daemon: top-of-hour window, or catch-up while news runs
        # (covers Mac sleep that missed :00). Once per hour via _last_pause_hour.
        if not should_trigger_news_pause(now, config):
            return

        hour_mark = now.replace(minute=0, second=0, microsecond=0)
        if self._last_pause_hour == hour_mark:
            return
        if self._pause_thread and self._pause_thread.is_alive():
            return

        self._last_pause_hour = hour_mark
        self._pause_thread = threading.Thread(
            target=self._run_pause,
            args=(config, speakers),
            daemon=True,
        )
        self._pause_thread.start()

    def _run_pause(self, config: AppConfig, speakers: list[SoCo] | None) -> None:
        try:
            def on_target(found: Radio2Target) -> None:
                self.set_status(
                    f"News pause on {found.speaker.player_name} "
                    f"({config.pause_minutes:g} min)"
                )

            result = attempt_news_pause(
                config,
                speakers=speakers,
                on_target=on_target,
                now=datetime.now(),
            )
            if result.status == "skipped":
                self.set_status("Top of hour — Radio 2 not playing, skipped")
                return
            if result.outcome == "resumed":
                self.set_status("Resumed after news")
            else:
                self.set_status(f"Left alone after news (state: {result.outcome})")
        except Exception as exc:  # noqa: BLE001 — surface any failure in the UI
            self.set_status(f"Pause failed: {exc}")


def format_countdown(now: datetime, tz: ZoneInfo) -> str:
    local = now.astimezone(tz)
    target = next_hour_mark(local)
    remaining = target - local
    total = max(int(remaining.total_seconds()), 0)
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        pretty = f"{hours}h {minutes:02d}m {seconds:02d}s"
    else:
        pretty = f"{minutes:02d}m {seconds:02d}s"
    return f"{target.strftime('%H:%M')} (in {pretty})"


def build_view(
    controller: NewsPauseController,
    config: AppConfig,
    resources: LiveResources,
) -> Panel:
    tz = ZoneInfo(config.timezone)
    now = datetime.now(tz)
    patterns = station_patterns(config)
    uri_patterns = station_uri_patterns(config)

    try:
        speakers = resources.get_speakers(config)
        target = select_target(speakers, config.room_name, patterns, uri_patterns)
        playing = now_playing_for(target, patterns, uri_patterns)
        error = None
    except Exception as exc:  # noqa: BLE001 — show discovery/UI errors in the panel
        playing = None
        speakers = None
        error = str(exc)

    if playing:
        controller.note_track(playing.label, now)
        controller.maybe_start_pause(config, now, speakers)

    with controller.lock:
        status = controller.status
        changed_at = controller.track_changed_at
        last_track = controller.last_track

    table = Table.grid(padding=(0, 2))
    table.add_column(justify="right", style="dim")
    table.add_column()

    table.add_row("Time", now.strftime("%a %d %b %H:%M:%S %Z"))
    table.add_row("Next news pause", format_countdown(now, tz))
    table.add_row("Pause length", f"{config.pause_minutes:g} minutes at :00")

    if error:
        table.add_row("Status", Text(error, style="bold red"))
    elif playing:
        state_style = "green" if playing.state == "PLAYING" else "yellow"
        table.add_row("Room", playing.room)
        table.add_row("Group", playing.group)
        table.add_row("State", Text(playing.state, style=state_style))

        track = Text(playing.label, style="bold cyan")
        if changed_at and (now - changed_at).total_seconds() < 8:
            track.append("  • changed", style="bold magenta")
        table.add_row("Now playing", track)

        if playing.album:
            table.add_row("Album", playing.album)

        radio = Text("yes", style="bold green") if playing.is_radio_2 else Text("no", style="dim")
        table.add_row("Radio 2 detected", radio)
        table.add_row("Automation", status)
        if last_track and last_track != playing.label:
            table.add_row("Previous", last_track)
    else:
        table.add_row("Status", "No speakers found")

    hint = Text(
        "Live view — same :00 trigger as the daemon. Press Ctrl+C to quit.",
        style="dim",
    )
    body = Group(table, Text(""), Align.center(hint))
    return Panel(
        body,
        title="Sonos · Radio 2 news pause",
        border_style="bright_blue",
        padding=(1, 2),
    )


def main() -> int:
    console = Console()
    controller = NewsPauseController()
    resources = LiveResources()
    console.print("Starting live Sonos view…")
    try:
        with Live(console=console, refresh_per_second=4, screen=False) as live:
            while True:
                config = resources.get_config()
                live.update(build_view(controller, config, resources))
                time.sleep(REFRESH_SECONDS)
    except KeyboardInterrupt:
        console.print("\nStopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
