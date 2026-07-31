#!/usr/bin/env python3
"""Live terminal view of Sonos playback + hourly Radio 2 news pause."""

from __future__ import annotations

import sys
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

from sonos_common import (
    discover_speakers,
    find_radio_2_target,
    in_news_window,
    load_config,
    next_hour_mark,
    now_playing_for,
    select_target,
    transport_state,
)

REFRESH_SECONDS = 1.0


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

    def maybe_start_pause(self, config: dict, now: datetime) -> None:
        tz = ZoneInfo(config.get("timezone", "Europe/London"))
        local = now.astimezone(tz)
        hour_mark = local.replace(minute=0, second=0, microsecond=0)
        pause_minutes = float(config.get("pause_minutes", 6))

        if not in_news_window(local, pause_minutes):
            return
        if self._last_pause_hour == hour_mark:
            return
        if self._pause_thread and self._pause_thread.is_alive():
            return

        self._last_pause_hour = hour_mark
        self._pause_thread = threading.Thread(
            target=self._run_pause,
            args=(config, pause_minutes),
            daemon=True,
        )
        self._pause_thread.start()

    def _run_pause(self, config: dict, pause_minutes: float) -> None:
        try:
            speakers = discover_speakers()
            target = find_radio_2_target(
                speakers,
                config.get("room_name", ""),
                config.get("station_match", ["radio 2"]),
            )
            if target is None:
                self.set_status("Top of hour — Radio 2 not playing, skipped")
                return

            self.set_status(f"News pause on {target.player_name} ({pause_minutes:g} min)")
            target.pause()
            time.sleep(pause_minutes * 60)

            state = transport_state(target)
            if state == "PAUSED_PLAYBACK":
                target.play()
                self.set_status("Resumed after news")
            else:
                self.set_status(f"Left alone after news (state: {state})")
        except Exception as exc:  # noqa: BLE001
            self.set_status(f"Pause failed: {exc}")


def format_countdown(now: datetime, tz: ZoneInfo) -> str:
    local = now.astimezone(tz)
    target = next_hour_mark(local)
    remaining = target - local
    total = int(remaining.total_seconds())
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        pretty = f"{hours}h {minutes:02d}m {seconds:02d}s"
    else:
        pretty = f"{minutes:02d}m {seconds:02d}s"
    return f"{target.strftime('%H:%M')} (in {pretty})"


def build_view(controller: NewsPauseController, config: dict) -> Panel:
    tz = ZoneInfo(config.get("timezone", "Europe/London"))
    now = datetime.now(tz)
    patterns = config.get("station_match", ["radio 2"])
    pause_minutes = float(config.get("pause_minutes", 6))

    try:
        speakers = discover_speakers()
        target = select_target(speakers, config.get("room_name", ""), patterns)
        playing = now_playing_for(target, patterns)
        error = None
    except Exception as exc:  # noqa: BLE001
        speakers = []
        playing = None
        error = str(exc)

    if playing:
        controller.note_track(playing.label, now)
        controller.maybe_start_pause(config, now)

    with controller.lock:
        status = controller.status
        changed_at = controller.track_changed_at
        last_track = controller.last_track

    table = Table.grid(padding=(0, 2))
    table.add_column(justify="right", style="dim")
    table.add_column()

    table.add_row("Time", now.strftime("%a %d %b %H:%M:%S %Z"))
    table.add_row("Next news pause", format_countdown(now, tz))
    table.add_row("Pause length", f"{pause_minutes:g} minutes at :00")

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

    hint = Text("Live view — updates as the track changes. Press Ctrl+C to quit.", style="dim")
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
    console.print("Starting live Sonos view…")
    try:
        with Live(console=console, refresh_per_second=4, screen=False) as live:
            while True:
                config = load_config()
                live.update(build_view(controller, config))
                time.sleep(REFRESH_SECONDS)
    except KeyboardInterrupt:
        console.print("\nStopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
