#!/usr/bin/env python3
"""Frozen-clock tests for hourly schedule helpers."""

from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from sonos_common import (
    in_news_window,
    in_top_of_hour_window,
    next_hour_mark,
    seconds_until_next_hour,
)

TZ = ZoneInfo("Europe/London")


def at(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 7, 31, hour, minute, second, tzinfo=TZ)


class ScheduleTests(unittest.TestCase):
    def test_next_hour_mark(self) -> None:
        self.assertEqual(next_hour_mark(at(14, 20)).hour, 15)
        self.assertEqual(next_hour_mark(at(23, 45)).hour, 0)

    def test_seconds_until_mid_hour(self) -> None:
        delay = seconds_until_next_hour(at(14, 20), lead_seconds=5)
        # 14:59:55 - 14:20:00 = 39m 55s
        self.assertEqual(delay, 39 * 60 + 55)

    def test_seconds_until_inside_lead_window(self) -> None:
        self.assertEqual(seconds_until_next_hour(at(14, 59, 50), 5), 5)
        self.assertEqual(seconds_until_next_hour(at(14, 59, 58), 5), 2)

    def test_lead_in_at_59_does_not_skip_hour(self) -> None:
        """Regression for STU-107 / #9: :59 must not schedule ~3600s ahead."""
        # Exact timestamps from the failing production log pattern.
        self.assertEqual(seconds_until_next_hour(at(20, 59, 54), 5), 1)
        self.assertEqual(seconds_until_next_hour(at(20, 59, 55), 5), 5)
        self.assertLess(seconds_until_next_hour(at(20, 59, 55), 5), 60)
        self.assertFalse(in_top_of_hour_window(at(20, 59, 55), 5))
        self.assertEqual(seconds_until_next_hour(at(21, 0, 0), 5), 0)
        self.assertTrue(in_top_of_hour_window(at(21, 0, 0), 5))

    def test_seconds_until_at_top_of_hour(self) -> None:
        self.assertEqual(seconds_until_next_hour(at(15, 0, 3), 5), 0)
        self.assertTrue(in_top_of_hour_window(at(15, 0, 3), 5))

    def test_seconds_until_after_missed_window(self) -> None:
        delay = seconds_until_next_hour(at(15, 0, 20), 5)
        # Next check is 15:59:55
        self.assertEqual(delay, 59 * 60 + 35)
        self.assertFalse(in_top_of_hour_window(at(15, 0, 20), 5))

    def test_news_window(self) -> None:
        self.assertTrue(in_news_window(at(15, 0, 30), 6))
        self.assertTrue(in_news_window(at(15, 5, 59), 6))
        self.assertFalse(in_news_window(at(15, 6, 0), 6))


if __name__ == "__main__":
    unittest.main()
