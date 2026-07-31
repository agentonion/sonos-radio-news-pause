#!/usr/bin/env python3
"""Pause Sonos during BBC Radio 2 hourly news, then resume."""

from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from sonos_common import (
    discover_speakers,
    find_radio_2_target,
    load_config,
    seconds_until_next_hour,
    transport_state,
)

LOG = logging.getLogger("sonos_news_pause")


def wait_until_top_of_hour(tz: ZoneInfo, lead_seconds: int) -> None:
    while True:
        now = datetime.now(tz)
        delay = seconds_until_next_hour(now, lead_seconds)
        LOG.info("Next check at top of hour in %.0f seconds.", delay)
        deadline = time.monotonic() + delay
        while time.monotonic() < deadline:
            time.sleep(min(30.0, max(deadline - time.monotonic(), 0)))

        now = datetime.now(tz)
        if now.minute == 0 and now.second <= max(lead_seconds, 15):
            return
        # Missed the window (machine slept); wait for the next hour.
        if now.minute >= 1:
            continue
        time.sleep(0.5)


def pause_for_news(target, pause_minutes: float) -> None:
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
    else:
        LOG.info("Not resuming %s — transport state is %s.", target.player_name, state)


def run_once(config: dict) -> None:
    speakers = discover_speakers()
    target = find_radio_2_target(
        speakers,
        config.get("room_name", ""),
        config.get("station_match", ["radio 2"]),
    )
    if target is None:
        LOG.info("Radio 2 not playing — nothing to pause.")
        return
    pause_for_news(target, float(config.get("pause_minutes", 6)))


def run_daemon() -> None:
    LOG.info("Starting Sonos Radio 2 hourly news pause.")
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

    if args.once:
        run_once(load_config())
        return 0

    run_daemon()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
