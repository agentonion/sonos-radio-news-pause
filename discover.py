#!/usr/bin/env python3
"""List Sonos rooms and what they are currently playing."""

from __future__ import annotations

from sonos_common import (
    discover_speakers_from_config,
    iter_coordinators,
    load_config,
    now_playing_for,
    station_patterns,
    station_uri_patterns,
)


def main() -> int:
    config = load_config()
    patterns = station_patterns(config)
    uri_patterns = station_uri_patterns(config)
    try:
        speakers = discover_speakers_from_config(config)
    except RuntimeError as exc:
        print(exc)
        return 1

    for target in sorted(iter_coordinators(speakers), key=lambda s: s.player_name.casefold()):
        playing = now_playing_for(target, patterns, uri_patterns)
        print(playing.room)
        print(f"  group: {playing.group}")
        print(f"  state: {playing.state}")
        print(f"  now:   {playing.label}")
        if playing.uri:
            print(f"  uri:   {playing.uri}")
        if playing.match_evidence:
            print(f"  match: {playing.match_evidence}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
