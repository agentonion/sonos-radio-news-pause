# Sonos Radio 2 news pause

Pauses Sonos at the top of every hour (UK time) when BBC Radio 2 is playing, then resumes after 6 minutes.

Works with playback started from the Sonos phone app — this Mac just needs to stay on the same Wi‑Fi.

Requires **Python 3.11+** (`tomllib`).

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt          # headless daemon
pip install -r requirements-live.txt     # optional: live terminal dashboard
```

Or with the project extras:

```bash
pip install -e ".[live]"     # daemon + live.py
pip install -e ".[dev]"      # + ruff for local checks
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

Run continuously in the foreground (same as default / `--daemon`):

```bash
python sonos_news_pause.py
# or explicitly (used by launchd):
python sonos_news_pause.py --daemon
```

## macOS LaunchAgent

Install / start at login:

```bash
./install_launchd.sh
```

Uninstall / stop:

```bash
./uninstall_launchd.sh
```

Or manually:

```bash
launchctl bootout "gui/$(id -u)/com.user.sonos-news-pause"
```

After deploying code changes, restart the agent so it picks up the new process:

```bash
launchctl kickstart -k "gui/$(id -u)/com.user.sonos-news-pause"
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
| `lead_seconds` | `5` | Wake this many seconds before `:00` |
| `top_of_hour_window_seconds` | `15` | Seconds after `:00` that still count as the trigger window (effective window is `max(lead_seconds, this)`) |
| `discovery_timeout` | `5` | SSDP discovery timeout (seconds) |
| `discovery_retries` | `3` | Discovery attempts before giving up |
| `discovery_backoff` | `1.0` | Base backoff between discovery retries (seconds) |

### Scheduling

Daemon and live view share one rule:

1. Prefer the top-of-hour window (`minute == 0` and
   `second <= max(lead_seconds, top_of_hour_window_seconds)` — about `:00:00`–`:00:15`)
2. **Catch-up:** if the Mac slept through `:00`, still attempt a pause anytime
   during the news duration (`pause_minutes`), for whatever time remains

Waits re-check wall-clock every ≤30s so a laptop sleep cannot push the alarm past
the hour (macOS `time.monotonic()` does not advance while asleep).

### Station detection

Matching prefers service URI / TuneIn IDs, then falls back to text metadata.
Default patterns live in `sonos_common.py` (`DEFAULT_STATION_MATCH` / `DEFAULT_STATION_URI_MATCH`); `config.toml` and tests use the same values.

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

- Runtime (daemon): `soco` in `requirements.txt`
- Optional live UI: `rich` via `requirements-live.txt` or `.[live]`

Pinned direct deps. Refresh with:

```bash
pip install -U soco rich
pip freeze | grep -E '^(soco|rich)=='
# then update requirements.txt / requirements-live.txt / pyproject.toml to match
```

## Tests

Use the project venv (system Python will fail without deps):

```bash
source .venv/bin/activate
python -m unittest discover -s tests -v
```

CI runs the same command on pull requests (plus `ruff check`).

## Logs

When installed via `launchd`, logs go to `logs/sonos-news-pause.log`.
