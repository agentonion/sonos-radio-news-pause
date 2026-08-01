#!/usr/bin/env python3
"""Unit tests for persisted pause intent helpers."""

from __future__ import annotations

import logging
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from sonos_common import (
    AppConfig,
    PauseIntent,
    clear_pause_state,
    load_pause_state,
    recover_orphaned_pause,
    resume_if_owned,
    write_pause_state,
)


class PauseStateTests(unittest.TestCase):
    def setUp(self) -> None:
        logging.disable(logging.CRITICAL)

    def tearDown(self) -> None:
        logging.disable(logging.NOTSET)

    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pause_state.json"
            resume_at = datetime(2026, 7, 31, 15, 6, tzinfo=UTC)
            intent = PauseIntent(uid="RINCON_ABC", resume_at=resume_at, player_name="Kitchen")
            write_pause_state(intent, path)
            loaded = load_pause_state(path)
            assert loaded is not None
            self.assertEqual(loaded.uid, "RINCON_ABC")
            self.assertEqual(loaded.player_name, "Kitchen")
            self.assertEqual(loaded.resume_at, resume_at)
            clear_pause_state(path)
            self.assertIsNone(load_pause_state(path))

    def test_corrupt_state_cleared(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pause_state.json"
            path.write_text("{not-json", encoding="utf-8")
            self.assertIsNone(load_pause_state(path))
            self.assertFalse(path.exists())

    def test_resume_if_owned_accepts_stopped(self) -> None:
        speaker = MagicMock()
        speaker.player_name = "Kitchen"
        with (
            patch("sonos_common.transport_state", side_effect=["STOPPED", "PLAYING"]),
            patch("sonos_common.time.sleep"),
        ):
            self.assertEqual(resume_if_owned(speaker), "resumed")
            speaker.play.assert_called_once()

    def test_resume_if_owned_retries_when_still_stopped(self) -> None:
        speaker = MagicMock()
        speaker.player_name = "Kitchen"
        with (
            patch(
                "sonos_common.transport_state",
                side_effect=["STOPPED", "STOPPED", "PLAYING"],
            ),
            patch("sonos_common.time.sleep"),
        ):
            self.assertEqual(resume_if_owned(speaker), "resumed")
            self.assertEqual(speaker.play.call_count, 2)

    def test_resume_if_owned_skips_playing(self) -> None:
        speaker = MagicMock()
        speaker.player_name = "Kitchen"
        with patch("sonos_common.transport_state", return_value="PLAYING"):
            self.assertEqual(resume_if_owned(speaker), "PLAYING")
            speaker.play.assert_not_called()

    def test_recover_orphaned_pause_resumes_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pause_state.json"
            resume_at = datetime.now(UTC) - timedelta(seconds=1)
            write_pause_state(
                PauseIntent(uid="RINCON_ABC", resume_at=resume_at, player_name="Kitchen"),
                path,
            )
            speaker = MagicMock()
            speaker.uid = "RINCON_ABC"
            speaker.player_name = "Kitchen"
            speaker.group = None

            with (
                patch(
                    "sonos_common.transport_state",
                    side_effect=["STOPPED", "PLAYING"],
                ),
                patch("sonos_common.time.sleep"),
                patch("sonos_common.discover_speakers_from_config", return_value=[speaker]),
            ):
                outcome = recover_orphaned_pause(config=AppConfig(), state_path=path)

            self.assertEqual(outcome, "resumed")
            speaker.play.assert_called_once()
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
