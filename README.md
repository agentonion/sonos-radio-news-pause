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

Dry-run (prints target + match evidence; does not pause):

```bash
python sonos_news_pause.py --dry-run
```

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
| `station_uri_match` | TuneIn / BBC Sounds IDs | Preferred URI fragment match |
| `station_match` | Radio 2 text patterns | Fallback case-insensitive media metadata match |
| `timezone` | `Europe/London` | Schedule timezone |
| `lead_seconds` | `5` | Start watching slightly before `:00` |
| `discovery_timeout` | `5` | SSDP discovery timeout (seconds) |
| `discovery_retries` | `3` | Discovery attempts before giving up |
| `discovery_backoff` | `1.0` | Base backoff between discovery retries (seconds) |

### Station detection

Matching prefers service URI / TuneIn IDs, then falls back to text metadata.

Known-good URI fragments:

| Pattern | Source |
| --- | --- |
| `bbc_radio_two` | BBC Sounds on Sonos (`x-sonosapi-hls:...bbc_radio_two...`) |
| `s24940` | Classic TuneIn Radio 2 station id (`x-sonosapi-stream:s24940?...`) |

Known-good text fallbacks: `radio 2`, `bbc radio 2`, `bbc radio2`.

When a top-of-hour check fails to match, enable debug logging to see the media blob (`logging` level `DEBUG` on `sonos_common`).

### Pause recovery

While paused, intent is written to `pause_state.json` (coordinator UID + resume-at). If launchd `KeepAlive` restarts the daemon mid-pause, startup waits until resume-at and resumes when transport is `PAUSED_PLAYBACK` or `STOPPED`.

## Dependencies

Direct deps are pinned in `requirements.txt`. Refresh with:

```bash
pip install -U soco rich
pip freeze | grep -E '^(soco|rich)==' > requirements.txt
```

## Tests

```bash
python -m unittest discover -s tests -v
```

## Logs

When installed via `launchd`, logs go to `logs/sonos-news-pause.log`.
