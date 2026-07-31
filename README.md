# Sonos Radio 2 news pause

Pauses Sonos at the top of every hour (UK time) when BBC Radio 2 is playing, then resumes after 6 minutes.

Works with playback started from the Sonos phone app — this Mac just needs to stay on the same Wi‑Fi.

> Status: **unreleased / WIP**. Private repo; no GitHub Release yet.

Requires **Python 3.11+** (`tomllib`).

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

List rooms / what's playing:

```bash
python discover.py
```

Live terminal dashboard (updates as the track changes; also runs the hourly pause while open):

```bash
python live.py
```

Optional: set `room_name` in `config.toml` to a specific room from that list. Leave it blank to auto-pause whichever group is playing Radio 2.

Test once immediately:

```bash
python sonos_news_pause.py --once
```

Run in the background at login (macOS):

```bash
./install_launchd.sh
```

Stop the background agent:

```bash
launchctl bootout "gui/$(id -u)/com.user.sonos-news-pause"
```

## Config

Edit `config.toml`:

| Key | Default | Purpose |
| --- | --- | --- |
| `pause_minutes` | `6` | How long to stay paused |
| `room_name` | `""` | Optional Sonos room; blank = auto-detect |
| `station_match` | Radio 2 patterns | Case-insensitive media metadata match |
| `timezone` | `Europe/London` | Schedule timezone |
| `lead_seconds` | `5` | Start watching slightly before `:00` |

## Logs

When installed via `launchd`, logs go to `logs/sonos-news-pause.log`.
