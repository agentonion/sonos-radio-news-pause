#!/usr/bin/env python3
"""Pause Sonos during BBC Radio 2 hourly news, then resume."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sonos_common import (
    AppConfig,
    attempt_news_pause,
    current_hour_mark,
    discover_speakers_from_config,
    load_config,
    now_playing_for,
    recover_orphaned_pause,
    seconds_until_next_hour,
    select_target,
    should_trigger_news_pause,
    sleep_until,
    station_patterns,
    station_uri_patterns,
)

LOG = logging.getLogger("sonos_news_pause")


def wait_until_news_pause(config: AppConfig, tz: ZoneInfo) -> None:
    """Block until a news-pause attempt should run.

    Uses short wall-clock rechecks so Mac sleep cannot push a long ``time.sleep``
    past the top-of-hour / news window (monotonic clocks pause while asleep).
    """
    while True:
        now = datetime.now(tz)
        if should_trigger_news_pause(now, config):
            LOG.info("News pause window reached (%s).", now.strftime("%H:%M:%S %Z"))
            return

        delay = seconds_until_next_hour(
            now,
            config.lead_seconds,
            config.top_of_hour_window_seconds,
            pause_minutes=config.pause_minutes,
        )
        chunk = min(max(delay, 0.0), 30.0) or 1.0
        LOG.info("Next news pause check in %.0f seconds.", delay)
        time.sleep(chunk)


def run_once(config: AppConfig, *, now: datetime | None = None) -> None:
    LOG.info("Running hourly Radio 2 pause check.")
    attempt_news_pause(config, now=now)


def run_dry_run(config: AppConfig) -> int:
    """Print which room/group would be paused without changing transport state."""
    speakers = discover_speakers_from_config(config)
    patterns = station_patterns(config)
    uri_patterns = station_uri_patterns(config)

    target = select_target(speakers, config.room_name, patterns, uri_patterns)
    playing = now_playing_for(target, patterns, uri_patterns)

    print(f"room:    {playing.room}")
    print(f"group:   {playing.group}")
    print(f"state:   {playing.state}")
    print(f"now:     {playing.label}")
    print(f"uri:     {playing.uri or '(none)'}")
    print(f"match:   {playing.match_evidence or '(no match)'}")

    if not (playing.state == "PLAYING" and playing.is_radio_2):
        print("would_pause: no")
        return 1

    print("would_pause: yes")
    print(f"evidence: {playing.match_evidence}")
    return 0


def run_daemon() -> None:
    LOG.info("Starting Sonos Radio 2 hourly news pause.")
    try:
        outcome = recover_orphaned_pause()
        if outcome is not None:
            LOG.info("Startup pause recovery finished: %s", outcome)
    except Exception:
        LOG.exception("Startup pause recovery failed.")

    last_attempt_hour: datetime | None = None
    while True:
        config = load_config()
        tz = ZoneInfo(config.timezone)
        wait_until_news_pause(config, tz)
        now = datetime.now(tz)
        hour_mark = current_hour_mark(now)
        if last_attempt_hour == hour_mark:
            # Already tried this hour; wait out the rest of the news window.
            window_end = hour_mark + timedelta(minutes=config.pause_minutes)
            LOG.info(
                "Already attempted pause for %s; waiting until news window ends.",
                hour_mark.strftime("%H:%M"),
            )
            sleep_until(window_end.astimezone(UTC))
            continue

        last_attempt_hour = hour_mark
        try:
            run_once(config, now=now)
        except Exception:
            LOG.exception("Hourly pause failed.")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--once",
        action="store_true",
        help="Run a single pause check immediately, then exit.",
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Print which room/group would be paused; do not change transport.",
    )
    mode.add_argument(
        "--daemon",
        action="store_true",
        help="Run continuously (same as default; kept for launchd ProgramArguments).",
    )
    return parser.parse_args(argv)


def _configure_logging() -> None:
    # launchd redirects stdio to a file; force line buffering so logs appear promptly.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True)
        except Exception:
            pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _configure_logging()

    if args.dry_run:
        return run_dry_run(load_config())

    if args.once:
        run_once(load_config())
        return 0

    run_daemon()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
