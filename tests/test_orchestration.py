#!/usr/bin/env python3
"""Unit tests for pause orchestration, target selection, and CLI helpers."""

from __future__ import annotations

import io
import logging
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from live import NewsPauseController
from sonos_common import (
    AppConfig,
    Radio2Target,
    StationMatch,
    attempt_news_pause,
    find_radio_2_target,
    should_trigger_news_pause,
)
from sonos_news_pause import parse_args, run_dry_run, run_once

TZ = ZoneInfo("Europe/London")


def _speaker(name: str = "Kitchen", uid: str = "RINCON_ABC", *, playing: bool = True) -> MagicMock:
    speaker = MagicMock()
    speaker.player_name = name
    speaker.uid = uid
    speaker.group = None
    return speaker


class OrchestrationTests(unittest.TestCase):
    def setUp(self) -> None:
        logging.disable(logging.CRITICAL)

    def tearDown(self) -> None:
        logging.disable(logging.NOTSET)

    def test_should_trigger_top_of_hour_and_news_catchup(self) -> None:
        config = AppConfig(
            lead_seconds=5,
            top_of_hour_window_seconds=15,
            pause_minutes=6,
        )
        at_hour = datetime(2026, 7, 31, 21, 0, 3, tzinfo=TZ)
        mid_news = datetime(2026, 7, 31, 21, 3, 0, tzinfo=TZ)
        after_news = datetime(2026, 7, 31, 21, 7, 0, tzinfo=TZ)
        self.assertTrue(should_trigger_news_pause(at_hour, config))
        # Catch-up after Mac sleep through :00.
        self.assertTrue(should_trigger_news_pause(mid_news, config))
        self.assertFalse(should_trigger_news_pause(after_news, config))

    def test_find_radio_2_target_returns_match_evidence(self) -> None:
        speaker = _speaker()
        match = StationMatch(True, "uri:bbc_radio_two", "blob", "x-sonosapi-hls:bbc_radio_two")
        with (
            patch("sonos_common.select_target", return_value=speaker),
            patch("sonos_common.is_playing", return_value=True),
            patch("sonos_common.radio_2_match", return_value=match),
        ):
            found = find_radio_2_target([speaker], "", ["radio 2"], ["bbc_radio_two"])
        assert found is not None
        self.assertIs(found.speaker, speaker)
        self.assertEqual(found.match.evidence, "uri:bbc_radio_two")

    def test_attempt_news_pause_skips_when_not_playing(self) -> None:
        config = AppConfig()
        speaker = _speaker()
        with (
            patch("sonos_common.discover_speakers_from_config", return_value=[speaker]),
            patch("sonos_common.find_radio_2_target", return_value=None),
            patch("sonos_common.log_match_miss") as miss,
            patch("sonos_common.pause_for_news") as pause,
        ):
            result = attempt_news_pause(config)
        self.assertEqual(result.status, "skipped")
        pause.assert_not_called()
        miss.assert_called_once()

    def test_attempt_news_pause_pauses_and_notifies(self) -> None:
        config = AppConfig(pause_minutes=6)
        speaker = _speaker()
        found = Radio2Target(
            speaker=speaker,
            match=StationMatch(True, "text:radio 2", "radio 2", "uri"),
        )
        seen: list[str] = []

        with (
            patch("sonos_common.discover_speakers_from_config", return_value=[speaker]),
            patch("sonos_common.find_radio_2_target", return_value=found),
            patch("sonos_common.pause_for_news", return_value="resumed") as pause,
        ):
            result = attempt_news_pause(
                config,
                on_target=lambda target: seen.append(target.speaker.player_name),
            )

        self.assertEqual(result.status, "paused")
        self.assertEqual(result.outcome, "resumed")
        self.assertEqual(result.evidence, "text:radio 2")
        self.assertEqual(seen, ["Kitchen"])
        pause.assert_called_once_with(speaker, 6.0)

    def test_attempt_news_pause_catchup_uses_remaining_minutes(self) -> None:
        config = AppConfig(pause_minutes=6)
        speaker = _speaker()
        found = Radio2Target(
            speaker=speaker,
            match=StationMatch(True, "uri:bbc_radio_two", "blob", "uri"),
        )
        mid_news = datetime(2026, 7, 31, 21, 2, 0, tzinfo=TZ)

        with (
            patch("sonos_common.discover_speakers_from_config", return_value=[speaker]),
            patch("sonos_common.find_radio_2_target", return_value=found),
            patch("sonos_common.pause_for_news", return_value="resumed") as pause,
        ):
            result = attempt_news_pause(config, now=mid_news)

        self.assertEqual(result.status, "paused")
        pause.assert_called_once_with(speaker, 4.0)

    def test_run_once_uses_shared_helper(self) -> None:
        config = AppConfig()
        with patch("sonos_news_pause.attempt_news_pause") as attempt:
            run_once(config)
        attempt.assert_called_once_with(config, now=None)

    def test_run_dry_run_exits_nonzero_without_match(self) -> None:
        config = AppConfig()
        speaker = _speaker()
        playing = MagicMock(
            room="Kitchen",
            group="Kitchen",
            state="PLAYING",
            label="BBC Radio 1",
            uri="x-rincon",
            match_evidence="",
            is_radio_2=False,
        )
        with (
            patch("sonos_news_pause.discover_speakers_from_config", return_value=[speaker]),
            patch("sonos_news_pause.select_target", return_value=speaker),
            patch("sonos_news_pause.now_playing_for", return_value=playing),
            patch("sys.stdout", new_callable=io.StringIO) as out,
        ):
            code = run_dry_run(config)
        self.assertEqual(code, 1)
        self.assertIn("would_pause: no", out.getvalue())

    def test_run_dry_run_exits_zero_when_matched(self) -> None:
        config = AppConfig()
        speaker = _speaker()
        playing = MagicMock(
            room="Kitchen",
            group="Kitchen",
            state="PLAYING",
            label="BBC Radio 2",
            uri="x-sonosapi-hls:bbc_radio_two",
            match_evidence="uri:bbc_radio_two",
            is_radio_2=True,
        )
        with (
            patch("sonos_news_pause.discover_speakers_from_config", return_value=[speaker]),
            patch("sonos_news_pause.select_target", return_value=speaker),
            patch("sonos_news_pause.now_playing_for", return_value=playing),
            patch("sys.stdout", new_callable=io.StringIO) as out,
        ):
            code = run_dry_run(config)
        self.assertEqual(code, 0)
        self.assertIn("would_pause: yes", out.getvalue())

    def test_parse_args_modes(self) -> None:
        self.assertTrue(parse_args(["--once"]).once)
        self.assertTrue(parse_args(["--dry-run"]).dry_run)
        self.assertTrue(parse_args(["--daemon"]).daemon)
        self.assertFalse(parse_args([]).once)

    def test_live_controller_catchup_once_per_hour(self) -> None:
        controller = NewsPauseController()
        config = AppConfig(pause_minutes=6, lead_seconds=5, top_of_hour_window_seconds=15)
        mid_news = datetime(2026, 7, 31, 21, 3, 0, tzinfo=TZ)

        with patch("live.threading.Thread") as thread_cls:
            thread = MagicMock()
            thread_cls.return_value = thread
            controller.maybe_start_pause(config, mid_news)
            thread_cls.assert_called_once()
            thread.start.assert_called_once()
            self.assertEqual(
                controller._last_pause_hour,
                mid_news.replace(minute=0, second=0, microsecond=0),
            )

            # Second call in the same hour must not start another pause.
            controller.maybe_start_pause(config, mid_news.replace(minute=4))
            self.assertEqual(thread_cls.call_count, 1)

        after_news = datetime(2026, 7, 31, 21, 7, 0, tzinfo=TZ)
        with patch("live.threading.Thread") as thread_cls:
            controller.maybe_start_pause(config, after_news)
            thread_cls.assert_not_called()


if __name__ == "__main__":
    unittest.main()
