#!/usr/bin/env python3
"""List Sonos rooms and what they are currently playing."""

from __future__ import annotations

from sonos_common import DEFAULT_STATION_MATCH, discover_speakers, iter_coordinators, now_playing_for


def main() -> int:
    try:
        speakers = discover_speakers()
    except RuntimeError as exc:
        print(exc)
        return 1

    for target in sorted(iter_coordinators(speakers), key=lambda s: s.player_name.casefold()):
        playing = now_playing_for(target, DEFAULT_STATION_MATCH)
        print(playing.room)
        print(f"  group: {playing.group}")
        print(f"  state: {playing.state}")
        print(f"  now:   {playing.label}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
