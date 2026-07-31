#!/usr/bin/env python3
"""List Sonos rooms and what they are currently playing."""

from __future__ import annotations

from sonos_common import discover_speakers, now_playing_for


def main() -> int:
    try:
        speakers = discover_speakers()
    except RuntimeError as exc:
        print(exc)
        return 1

    seen: set[str] = set()
    for speaker in sorted(speakers, key=lambda s: s.player_name.casefold()):
        playing = now_playing_for(speaker, ["radio 2"])
        if playing.speaker.uid in seen:
            continue
        seen.add(playing.speaker.uid)
        print(playing.room)
        print(f"  group: {playing.group}")
        print(f"  state: {playing.state}")
        print(f"  now:   {playing.label}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
