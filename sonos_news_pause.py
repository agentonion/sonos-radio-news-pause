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
    in_top_of_hour_window,
    load_config,
    pause_for_news,
    seconds_until_next_hour,
    station_patterns,
)

LOG = logging.getLogger("sonos_news_pause")


def wait_until_top_of_hour(tz: ZoneInfo, lead_seconds: int) -> None:
    while True:
        now = datetime.now(tz)
        delay = seconds_until_next_hour(now, lead_seconds)
        if delay <= 0:
            if in_top_of_hour_window(now, lead_seconds):
                return
            # Outside the trigger window after a miss; recompute.
            time.sleep(1)
            continue

        LOG.info("Next check at top of hour in %.0f seconds.", delay)
        deadline = time.monotonic() + delay
        while time.monotonic() < deadline:
            time.sleep(min(30.0, max(deadline - time.monotonic(), 0)))


def run_once(config: dict) -> None:
    speakers = discover_speakers()
    target = find_radio_2_target(
        speakers,
        config.get("room_name", ""),
        station_patterns(config),
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
