#!/usr/bin/env python3
"""Tests that media helpers avoid redundant Sonos API chatter."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from sonos_common import fetch_media, now_playing_for, radio_2_match


class MediaFetchTests(unittest.TestCase):
    def test_fetch_media_single_track_info_call(self) -> None:
        speaker = MagicMock()
        speaker.player_name = "Kitchen"
        speaker.get_current_track_info.return_value = {
            "title": "BBC Radio 2",
            "artist": "",
            "album": "",
            "uri": "x-sonosapi-hls:bbc_radio_two",
            "playlist_position": "1",
        }
        speaker.get_current_media_info.return_value = {"channel": "Radio 2"}

        track, uri, blob = fetch_media(speaker)
        self.assertEqual(uri, "x-sonosapi-hls:bbc_radio_two")
        self.assertIn("bbc_radio_two", blob)
        self.assertIn("radio 2", blob)
        # playlist_position must not be part of the match blob.
        self.assertNotIn("1", blob)
        speaker.get_current_track_info.assert_called_once()
        speaker.get_current_media_info.assert_called_once()
        self.assertEqual(track["title"], "BBC Radio 2")

    def test_radio_2_match_and_now_playing_share_single_fetch_path(self) -> None:
        speaker = MagicMock()
        speaker.player_name = "Kitchen"
        speaker.group = None
        speaker.get_current_track_info.return_value = {
            "title": "BBC Radio 2",
            "artist": "Vera Lynn",
            "album": "",
            "uri": "x-sonosapi-stream:s24940?sid=254",
        }
        speaker.get_current_media_info.return_value = {}
        speaker.get_current_transport_info.return_value = {
            "current_transport_state": "PLAYING",
        }

        match = radio_2_match(speaker, ["radio 2"], ["s24940", "bbc_radio_two"])
        self.assertTrue(match.matched)
        self.assertEqual(match.evidence, "uri:s24940")
        self.assertEqual(speaker.get_current_track_info.call_count, 1)

        playing = now_playing_for(speaker, ["radio 2"], ["s24940", "bbc_radio_two"])
        self.assertTrue(playing.is_radio_2)
        # One more fetch for now_playing — not a double fetch inside that call.
        self.assertEqual(speaker.get_current_track_info.call_count, 2)


if __name__ == "__main__":
    unittest.main()
