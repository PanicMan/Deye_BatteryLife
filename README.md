# Deye BatteryLife

[🇩🇪 Deutsch](README.de.md) · 🇬🇧 English

Daily dynamic minimum SoC for Deye hybrid inverters, modelled on [Victron BatteryLife](https://www.victronenergy.com/media/pg/Energy_Storage_System/en/controlling-depth-of-discharge.html), plus a fleet health report. It talks to the DeyeCloud Open API and needs nothing but Python (standard library only).

> **Disclaimer:** Unofficial project, not affiliated with Deye. The script writes settings to real inverters and batteries.
> Use at your own risk, without warranty (license: GPL-3.0). Always test with `--dry-run` first.

## What it does

Once a day (default 18:00) it looks at every inverter of your DeyeCloud organisation and adjusts the **minimum SoC** (the SoC of all six TOU rows):

| Maximum SoC in the last 18 h | Minimum SoC |
|---|---|
| below 85 % | +5 points (the battery did not get full) |
| 85 – 94 % | unchanged |
| 95 % or more | −5 points |

- Limits: *shutdown SoC + 5* up to a configurable cap (default 50 %).
- Grid charging is only allowed in TOU rows that lie completely inside the night zone (default 00:00–06:00); in all other rows it is switched off. Unusual but valid time tables are handled by their night rows; tables that cannot be interpreted (overlapping rows) keep their grid-charge flags, only the SoC is adjusted.
- Every change is read back from the inverter and verified; the previous TOU table is saved first (rollback).
- Stations run as **dry-run** (read and log only) unless their ID is in `live_allowlist`. Blacklisted stations are never touched.

A second part writes a **health report** (`Gesundheitsbericht.md`, written in German for now) for *all* stations without sending any command to the inverters: battery not moving, inverter offline, stale data, SoC mismatch between inverter and BMS, commands not answered for several days. Known findings can be acknowledged in the config; new stations are listed for 14 days.

## Setup

Requirements: Python 3.10+ (no packages needed).

1. Copy `config.example.json` to `config.json` and `secrets.example.json` to `secrets.json` and fill them in.
   `secrets.json` holds the DeyeCloud AppId/AppSecret and login (password as SHA256 hex) and must never be committed.
   On Windows restrict access: `icacls secrets.json /inheritance:r /grant:r "%USERNAME%:(F)"`.
2. Run the tests: `python -m unittest discover`.
3. Try it without writing: `python batterylife.py --dry-run --only <StationID>`, then `python batterylife.py --health-only`.
4. Register the scheduled tasks (Windows): `powershell -File register_tasks.ps1`.

**Security:** the API access can change battery settings of every station in the organisation. Never put it in the repository, chats or tickets.
Only code and example configs are versioned; customer names, station IDs, histories and backups
(`config.json`, `state/`, `logs/`, `backups/`, `reports/`, `data/`, `runs.csv`, `Stationsliste.csv`, `Gesundheitsbericht.md`) are in `.gitignore`.

## Scheduled tasks

| Task | Time | Command |
|---|---|---|
| Deye BatteryLife | 18:00 | `batterylife.py` (adjustment + health report) |
| Deye BatteryLife Retry | 18:45, 19:30 | `batterylife.py --retry` (only stations not handled successfully yet) |

The computer has to be running and the user account that registered the tasks has to be logged in.

## Files

| File | Purpose |
|---|---|
| `batterylife.py` | main script (read, decide, write, verify, health report) |
| `logic.py`, `health.py` | decision rules and health rules (pure functions, unit tested) |
| `tools/` | API client and fleet inventory helpers |
| `config.json` (local) | thresholds, `live_allowlist`, `blacklist`, `health`, `health_acknowledged` |
| `secrets.json` (local) | credentials |
| `runs.csv`, `logs/`, `state/<SN>.json` (local) | run history, daily log, per-inverter state |
| `backups/<date>/<SN>.json` (local) | TOU table before each change |
| `Gesundheitsbericht.md` (local) | health report, archive in `reports/` |

## Common tasks

- **Switch a station live:** add its station ID to `live_allowlist`.
- **Exclude a station** (the owner manages it) : add its ID with a reason to `blacklist`. Nothing is read or written; only the health report still looks at its history data.
- **Acknowledge a finding:** add to `health_acknowledged`:
  `"<StationID>": {"reason": "...", "rules": "all" | ["stuck_battery"], "until": "2026-12-31" | null}`.
  With `until` the finding shows up as open again after that date.
- **Roll back:** write the table from `backups/<date>/<SN>.json` back with `strategy/dynamicControl` (only `timeUseSettingItems`).
- **Manual runs:** `--dry-run` (never write), `--only <StationID>,...`, `--health-only` (no inverter commands at all), `--no-health`, `--retry`.

## New stations

The station list is fetched fresh from DeyeCloud on every run. A new station automatically runs as **dry-run** and is listed under "new stations" in the health report for 14 days. It only becomes live once its ID is in `live_allowlist`. Unsupported inverter types (only `0_5411_1` is enabled) are skipped.

## Good to know

- Do not work on the same station in the DeyeCloud web UI at the same time: the API then answers "command concurrent running" and the script retries later.
- A timeout (HTTP 504) while writing does not mean nothing arrived; the script always verifies by reading back.
- Inverter settings are read and written through the Open API's `strategy/dynamicControl` and Modbus read orders (`order/customControl`, registers 100–199); the register map was verified against the DeyeCloud web UI on `0_5411_1` three-phase hybrids with different firmware versions.
- Tests: `python -m unittest discover` (also run by GitHub Actions on every push).
