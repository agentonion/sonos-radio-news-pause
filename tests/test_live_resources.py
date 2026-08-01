#!/usr/bin/env python3
"""Tests for live-view config/speaker caching."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from live import SPEAKER_REDISCOVER_SECONDS, LiveResources
from sonos_common import AppConfig


class LiveResourcesTests(unittest.TestCase):
    def test_config_reloads_only_when_mtime_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text('pause_minutes = 6\ntimezone = "Europe/London"\n', encoding="utf-8")
            resources = LiveResources()
            first = AppConfig(pause_minutes=6)
            second = AppConfig(pause_minutes=8)

            with (
                patch("live.CONFIG_PATH", path),
                patch("live.load_config", side_effect=[first, second]) as load,
            ):
                self.assertIs(resources.get_config(), first)
                self.assertIs(resources.get_config(), first)
                self.assertEqual(load.call_count, 1)

                path.write_text('pause_minutes = 8\ntimezone = "Europe/London"\n', encoding="utf-8")
                self.assertIs(resources.get_config(), second)
                self.assertEqual(load.call_count, 2)

    def test_speakers_cached_until_interval(self) -> None:
        resources = LiveResources()
        config = AppConfig()
        speakers_a = [object()]
        speakers_b = [object()]

        with (
            patch(
                "live.discover_speakers_from_config",
                side_effect=[speakers_a, speakers_b],
            ) as discover,
            patch("live.time.monotonic", side_effect=[0.0, 10.0, SPEAKER_REDISCOVER_SECONDS + 1]),
        ):
            self.assertIs(resources.get_speakers(config), speakers_a)
            self.assertIs(resources.get_speakers(config), speakers_a)
            self.assertIs(resources.get_speakers(config), speakers_b)
            self.assertEqual(discover.call_count, 2)


if __name__ == "__main__":
    unittest.main()
