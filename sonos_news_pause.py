#!/usr/bin/env python3
"""Pause Sonos during BBC Radio 2 hourly news, then resume."""

from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from sonos_common import (
    discover_speakers_from_config,
    find_radio_2_target,
    in_top_of_hour_window,
    load_config,
    log_match_miss,
    now_playing_for,
    pause_for_news,
    radio_2_match,
    recover_orphaned_pause,
    seconds_until_next_hour,
    select_target,
    station_patterns,
    station_uri_patterns,
)

LOG = logging.getLogger("sonos_news_pause")


def wait_until_top_of_hour(tz: ZoneInfo, lead_seconds: int) -> None:
    """Block until inside the top-of-hour window.

    The :59 lead-in must keep waiting into :00 — never treat minute>=1 as a
    miss (that skipped the hour and waited ~3600s; STU-107 / #9).
    """
    while True:
        now = datetime.now(tz)
        delay = seconds_until_next_hour(now, lead_seconds)
        if delay <= 0:
            if in_top_of_hour_window(now, lead_seconds):
                LOG.info("Top-of-hour window reached (%s).", now.strftime("%H:%M:%S %Z"))
                return
            # Outside the trigger window after a miss; recompute.
            time.sleep(1)
            continue

        LOG.info("Next check at top of hour in %.0f seconds.", delay)
        deadline = time.monotonic() + delay
        while time.monotonic() < deadline:
            time.sleep(min(30.0, max(deadline - time.monotonic(), 0)))


def run_once(config: dict) -> None:
    LOG.info("Running hourly Radio 2 pause check.")
    speakers = discover_speakers_from_config(config)
    patterns = station_patterns(config)
    uri_patterns = station_uri_patterns(config)
    target = find_radio_2_target(
        speakers,
        config.get("room_name", ""),
        patterns,
        uri_patterns,
    )
    if target is None:
        LOG.info("Radio 2 not playing — nothing to pause.")
        log_match_miss(speakers, config.get("room_name", ""), patterns, uri_patterns)
        return
    match = radio_2_match(target, patterns, uri_patterns)
    LOG.info("Matched Radio 2 via %s on %s.", match.evidence or "unknown", target.player_name)
    pause_for_news(target, float(config.get("pause_minutes", 6)))


def run_dry_run(config: dict) -> int:
    """Print which room/group would be paused without changing transport state."""
    speakers = discover_speakers_from_config(config)
    patterns = station_patterns(config)
    uri_patterns = station_uri_patterns(config)
    room_name = config.get("room_name", "")

    target = select_target(speakers, room_name, patterns, uri_patterns)
    playing = now_playing_for(target, patterns, uri_patterns)
    match = radio_2_match(target, patterns, uri_patterns)

    print(f"room:    {playing.room}")
    print(f"group:   {playing.group}")
    print(f"state:   {playing.state}")
    print(f"now:     {playing.label}")
    print(f"uri:     {playing.uri or '(none)'}")
    print(f"match:   {match.evidence or '(no match)'}")

    if not (playing.state == "PLAYING" and match.matched):
        print("would_pause: no")
        LOG.debug("Dry-run media blob: %s", match.blob)
        return 1

    print("would_pause: yes")
    print(f"evidence: {match.evidence}")
    return 0


def run_daemon() -> None:
    LOG.info("Starting Sonos Radio 2 hourly news pause.")
    try:
        outcome = recover_orphaned_pause()
        if outcome is not None:
            LOG.info("Startup pause recovery finished: %s", outcome)
    except Exception:
        LOG.exception("Startup pause recovery failed.")

    while True:
        config = load_config()
        tz = ZoneInfo(config.get("timezone", "Europe/London"))
        lead = int(config.get("lead_seconds", 5))
        wait_until_top_of_hour(tz, lead)
        try:
            run_once(config)
        except Exception:
            LOG.exception("Hourly pause failed.")
        # Avoid double-triggering in the same minute.
        time.sleep(70)


def parse_args() -> argparse.Namespace:
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
        help="Run continuously (default). Used by launchd.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if args.dry_run:
        return run_dry_run(load_config())

    if args.once:
        run_once(load_config())
        return 0

    run_daemon()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
