#!/usr/bin/env python3
"""Unit tests for Radio 2 station matching."""

from __future__ import annotations

import unittest

from sonos_common import match_station

URI_PATTERNS = ["s24940", "bbc_radio_two"]
TEXT_PATTERNS = ["radio 2", "bbc radio 2", "bbc radio2"]


class StationMatchTests(unittest.TestCase):
    def test_prefers_bbc_sounds_uri(self) -> None:
        uri = (
            "x-sonosapi-hls:stations%7eplayable%7e%7ebbc_radio_two"
            "%7e%7eurn%3abbc%3aradio%3anetwork%3abbc_radio_two?sid=325"
        )
        match = match_station("madonna vogue", uri, TEXT_PATTERNS, URI_PATTERNS)
        self.assertTrue(match.matched)
        self.assertEqual(match.evidence, "uri:bbc_radio_two")

    def test_prefers_tunein_id(self) -> None:
        uri = "x-sonosapi-stream:s24940?sid=254&flags=8224&sn=0"
        match = match_station("something else", uri, TEXT_PATTERNS, URI_PATTERNS)
        self.assertTrue(match.matched)
        self.assertEqual(match.evidence, "uri:s24940")

    def test_falls_back_to_text(self) -> None:
        match = match_station(
            "bbc radio 2 — vera lynn",
            "x-rincon-mp3radio://example",
            TEXT_PATTERNS,
            URI_PATTERNS,
        )
        self.assertTrue(match.matched)
        self.assertEqual(match.evidence, "text:radio 2")

    def test_no_false_match(self) -> None:
        match = match_station(
            "bbc radio 1 — breakfast",
            "x-sonosapi-hls:stations%7ebbc_radio_one",
            TEXT_PATTERNS,
            URI_PATTERNS,
        )
        self.assertFalse(match.matched)
        self.assertEqual(match.evidence, "")


if __name__ == "__main__":
    unittest.main()
