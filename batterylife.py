"""Deye BatteryLife - daily dynamic minimum SoC (Victron BatteryLife style) for Deye hybrid inverters.

Run once a day (Task Scheduler, 18:00). For every non-blacklisted inverter it
  1. reads the live TOU table (cloud order) and Modbus regs 100-199 (shutdown SoC, consistency check),
  2. takes the maximum SoC of the last N hours from the history API,
  3. raises / keeps / lowers the minimum SoC (= SoC of all 6 TOU rows) in steps, within [shutdown+5, cap],
  4. limits grid charging to the night rows of the TOU table,
  5. writes the table (live stations only), reads it back and logs everything.
Everything not in live_allowlist runs as dry-run (reads only, logs what it would do).

Usage: python batterylife.py [--dry-run] [--retry] [--only ID[,ID]] [--config config.json]
"""
import argparse
import csv
import datetime as dt
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
import deye_api as api  # noqa: E402
import health  # noqa: E402
import logic  # noqa: E402

REG_FRAME = "01030064006405FE"  # Modbus RTU: read holding regs 100..199 of unit 1
TRANSIENT = ("2104004", "2104013")  # command concurrent running / order is sending
LOCK_MAX_AGE_S = 3 * 3600
log = logging.getLogger("batterylife")


class Busy(Exception):
    """Station could not be reached in time (lock held, order pending, gateway errors)."""


class Skip(Exception):
    """Station is deliberately left alone this run; the message is the reason."""


# ---------------------------------------------------------------- API helpers
def with_retry(fn, cfg, what):
    last = "no attempt"
    for _ in range(cfg["retries"]):
        try:
            res = fn()
        except Exception as e:  # HTTP 5xx, timeouts
            last = "exception %s" % str(e)[:100]
        else:
            if res.get("success"):
                return res
            if res.get("code") not in TRANSIENT:
                raise RuntimeError("%s failed: %s %s" % (what, res.get("code"), res.get("msg")))
            last = "%s %s" % (res.get("code"), res.get("msg"))
        time.sleep(cfg["retry_pause_s"])
    raise Busy("%s: %s" % (what, last))


def poll(fn, ready, cfg, what):
    for _ in range(cfg["poll_tries"]):
        try:
            res = fn()
            if ready(res):
                return res
        except Exception:
            pass
        time.sleep(cfg["poll_pause_s"])
    raise Busy("%s: result not ready" % what)


def read_tou(tok, sn, cfg):
    r = with_retry(lambda: api.call("/v1.0/strategy/dynamicControl/read", {"deviceSn": sn}, tok), cfg, "tou read")
    return poll(lambda: api.call("/v1.0/strategy/dynamicControl/readResult", {"orderId": r["orderId"]}, tok),
                lambda x: x.get("success") and x.get("timeUseSettingItems"), cfg, "tou read")


def read_regs(tok, sn, cfg):
    r = with_retry(lambda: api.call("/v1.0/order/customControl",
                                    {"deviceSn": sn, "content": REG_FRAME, "timeoutSeconds": 60}, tok), cfg, "modbus read")
    res = poll(lambda: api.get("/v1.0/order/%s" % r["orderId"], tok),
               lambda x: str(x.get("analysisResult") or "").startswith("0103"), cfg, "modbus read")
    a = res["analysisResult"]
    n = int(a[4:6], 16)
    data = a[6:6 + n * 2]
    if n != 200 or len(data) != 400:
        raise RuntimeError("unexpected modbus answer length %d" % n)
    return {100 + i: int(data[i * 4:i * 4 + 4], 16) for i in range(100)}


def open_alerts(tok, sn, days, cfg):
    """Alerts of the last `days` days that DeyeCloud has not closed yet."""
    now = int(time.time())
    r = with_retry(lambda: api.call("/v1.0/device/alertList?language=en", {
        "deviceSn": sn, "startTimestamp": now - days * 86400, "endTimestamp": now, "page": 1, "size": 100}, tok), cfg, "alerts")
    return [{"name": a.get("alertName"), "level": a.get("level"), "start": int(a["alertStartTime"])}
            for a in r.get("alertList") or [] if not a.get("alertEndTime")]


def battery_voltage(tok, sn, cfg):
    r = with_retry(lambda: api.call("/v1.0/device/latest", {"deviceList": [sn]}, tok), cfg, "latest data")
    for d in r.get("deviceDataList") or []:
        for k in d["dataList"]:
            if k["key"] == "BatteryVoltage":
                try:
                    return float(k["value"])
                except (TypeError, ValueError):
                    return None
    return None


def history_soc(tok, sn, hours, cfg):
    now = int(time.time())
    r = with_retry(lambda: api.call("/v1.0/device/historyRaw", {"deviceSn": sn, "startTimestamp": now - hours * 3600,
                                    "endTimestamp": now, "measurePoints": ["SOC"]}, tok), cfg, "history")
    vals = []
    for d in r.get("dataList") or []:
        for it in d.get("itemList") or []:
            if it.get("key") == "SOC":
                vals.append(int(float(it["value"])))
    return vals


def write_tou(tok, sn, items, cfg):
    """Write the TOU rows, then verify by reading back. Timeouts do not prove failure: always verify."""
    for attempt in range(cfg["retries"]):
        try:
            res = api.call("/v1.0/strategy/dynamicControl", {"deviceSn": sn, "timeUseSettingItems": items}, tok)
            if not res.get("success") and res.get("code") not in TRANSIENT:
                raise RuntimeError("write failed: %s %s" % (res.get("code"), res.get("msg")))
        except RuntimeError:
            raise
        except Exception as e:
            log.info("%s write attempt %d: %s - verifying by readback", sn, attempt + 1, str(e)[:80])
        time.sleep(cfg["retry_pause_s"] / 2)
        try:
            back = read_tou(tok, sn, cfg)["timeUseSettingItems"]
            if logic.same_table(back, items):
                return True
        except Busy:
            pass
        time.sleep(cfg["retry_pause_s"])
    raise Busy("write not confirmed by readback")


# ---------------------------------------------------------------- fleet
def fetch_units(tok, cfg):
    stations, status, page = {}, {}, 1
    while True:
        r = with_retry(lambda: api.call("/v1.0/station/list", {"page": page, "size": 200}, tok), cfg, "station list")
        for s in r.get("stationList") or []:
            stations[str(s["id"])] = s["name"].strip()
            status[str(s["id"])] = s.get("connectionStatus")
        if len(stations) >= r.get("total", 0) or not r.get("stationList"):
            break
        page += 1
    ids = list(stations)
    units = []
    for i in range(0, len(ids), 5):
        batch = ids[i:i + 5]
        r = with_retry(lambda: api.call("/v1.0/station/device", {"page": 1, "size": 200, "stationIds": [int(x) for x in batch]}, tok),
                       cfg, "device list")
        for d in r.get("deviceListItems") or []:
            if d["deviceType"] == "INVERTER":
                sid = str(d["stationId"])
                units.append({"station_id": sid, "name": stations[sid], "sn": d["deviceSn"],
                              "product": d.get("productId"), "online": d.get("connectStatus") == 1,
                              "station_status": status[sid], "collection_time": d.get("collectionTime")})
    return units, stations


# ---------------------------------------------------------------- per inverter
def process(u, tok, cfg, mode, today, state_dir, backup_dir, retry=False):
    row = {"station_id": u["station_id"], "station": u["name"], "sn": u["sn"], "mode": mode, "max_soc": "", "points": "",
           "shutdown": "", "user_min": "", "current": "", "new": "", "result": "", "note": ""}
    state_file = state_dir / ("%s.json" % u["sn"])
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}
    try:
        if mode == "live" and state.get("done_date") == today:
            row.update(result="done-today", note="already processed today (%s)" % state.get("done_result"))
            return row
        if retry and state.get("last_run", "")[:10] == today and state.get("last_result") not in ("busy", "error"):
            row.update(result="done-today", note="retry pass: already handled today (%s)" % state.get("last_result"))
            return row
        fault = logic.fault_reason(open_alerts(tok, u["sn"], cfg["health"].get("alert_days", 7), cfg), battery_voltage(tok, u["sn"], cfg),
                                   cfg["health"].get("battery_voltage_min", 40), cfg.get("fault_block_min_level", 2))
        if fault:
            raise Skip("active fault, no adjustment: %s" % fault)
        tou, regs = read_tou(tok, u["sn"], cfg), read_regs(tok, u["sn"], cfg)
        items = tou["timeUseSettingItems"]
        times = [i["time"] for i in items]
        socs = [i["soc"] for i in items]
        row["current"], row["shutdown"] = socs[0], regs[115]
        m_times = ["%02d:%02d" % divmod(regs[148 + k], 100) for k in range(6)]
        m_socs = [regs[166 + k] for k in range(6)]
        m_gc = [bool(regs[172 + k]) for k in range(6)]
        if tou.get("touAction") != "on":
            row.update(result="skipped", note="TOU is switched off")
        elif len(items) != 6 or times != m_times or socs != m_socs or [bool(i["enableGridCharge"]) for i in items] != m_gc:
            row.update(result="skipped", note="Modbus readback does not match TOU table (unknown register layout)")
        elif len(set(socs)) != 1:
            row.update(result="skipped", note="SoC differs between TOU rows %s" % socs)
        else:
            _decide_and_apply(u, tok, cfg, mode, today, tou, regs, items, times, row, state, backup_dir)
    except Skip as e:
        row.update(result="skipped", note=str(e))
    except Busy as e:
        row.update(result="busy", note=str(e))
    except Exception as e:
        row.update(result="error", note="%s: %s" % (type(e).__name__, str(e)[:200]))
    state.update(last_run=dt.datetime.now().isoformat(timespec="seconds"), last_result=row["result"], last_note=row["note"],
                 last_new=row["new"], last_max=row["max_soc"])
    if row["shutdown"] != "":
        state["shutdown"] = row["shutdown"]
    if row["result"] in ("busy", "error"):  # commands not answered: count days, not retry passes
        if state.get("busy_date") != today:
            state.update(busy_streak=state.get("busy_streak", 0) + 1, busy_date=today)
    else:
        state.update(busy_streak=0)
    if mode == "live" and row["result"] in ("changed", "nochange"):
        state.update(done_date=today, done_result=row["result"])
    state_file.write_text(json.dumps(state, indent=1), encoding="utf-8")
    return row


def _decide_and_apply(u, tok, cfg, mode, today, tou, regs, items, times, row, state, backup_dir):
    shutdown, cur = regs[115], items[0]["soc"]
    user_min = shutdown + cfg["min_soc_offset_over_shutdown"]
    row["user_min"] = user_min
    hist = history_soc(tok, u["sn"], cfg["history_hours"], cfg)
    row["points"] = len(hist)
    if len(hist) < cfg["min_history_points"]:
        row.update(result="skipped", note="only %d SoC history points in %dh" % (len(hist), cfg["history_hours"]))
        return
    row["max_soc"] = max(hist)
    new, why = logic.decide_soc(cur, max(hist), user_min, cfg["max_soc_cap"], cfg["low_soc_threshold"],
                                cfg["high_soc_threshold"], cfg["step"])
    flags, gc_note = logic.plan_grid_charge(times, cfg["night_zone"], cfg["min_row_minutes"], cfg.get("grid_charge", "all"))
    new_items = logic.build_items(items, new, flags)
    row["new"] = new
    notes = [why] + ([gc_note] if gc_note else [])
    if tou.get("gridChargeAction") != "on":
        notes.append("WARNING: global grid charge is OFF (table flags have no effect) - not touched")
    row["note"] = "; ".join(notes)
    if logic.same_table(items, new_items):
        row["result"] = "nochange"
    elif mode != "live":
        row["result"] = "dry-would-change"
    else:
        (backup_dir / ("%s.json" % u["sn"])).write_text(json.dumps(tou, indent=1), encoding="utf-8")
        write_tou(tok, u["sn"], new_items, cfg)
        row["result"] = "changed"


# ---------------------------------------------------------------- main
def acquire_lock(path):
    if path.exists() and time.time() - path.stat().st_mtime < LOCK_MAX_AGE_S:
        return False
    path.write_text("%s %s" % (os.getpid(), dt.datetime.now().isoformat()), encoding="utf-8")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "config.json"))
    ap.add_argument("--dry-run", action="store_true", help="force dry-run for all stations")
    ap.add_argument("--only", default="", help="comma separated station IDs")
    ap.add_argument("--retry", action="store_true", help="retry pass: only stations not handled successfully today")
    ap.add_argument("--health-only", action="store_true", help="only write the health report (no device commands at all)")
    ap.add_argument("--no-health", action="store_true", help="skip the health report")
    args = ap.parse_args()
    for path, example in ((Path(args.config), "config.example.json"), (ROOT / "secrets.json", "secrets.example.json")):
        if not path.exists():
            sys.exit("%s fehlt - bitte %s kopieren und ausfüllen (siehe README, Abschnitt Einrichtung)" % (path.name, example))
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    secrets = json.loads((ROOT / "secrets.json").read_text(encoding="utf-8"))
    today = dt.date.today().isoformat()
    for sub in ("logs", "state", "backups/" + today):
        (ROOT / sub).mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=[
        logging.FileHandler(ROOT / "logs" / ("%s.log" % today), encoding="utf-8"), logging.StreamHandler(sys.stdout)])
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    lock = ROOT / "run.lock"
    if not acquire_lock(lock):
        log.error("another run is active (run.lock younger than 3h) - exiting")
        return 3
    try:
        return run(cfg, secrets, args, today)
    finally:
        lock.unlink(missing_ok=True)


def run(cfg, secrets, args, today):
    tok = api.business_token(secrets)
    units, stations = fetch_units(tok, cfg)
    ignored = cfg.get("ignored_devices", {})
    units, partial_stations, missing = logic.apply_ignored(units, ignored)
    for sn in missing:
        log.warning("ignored device %s (%s) not found in fleet any more", sn, ignored[sn])
    for u in units:  # a station with an ignored (e.g. offline, battery-less) inverter is never fully NORMAL
        if u["station_id"] in partial_stations:
            u["station_status"] = None
    log.info("fleet: %d stations, %d inverters (%d ignored)", len(stations), len(units), len(ignored) - len(missing))
    blacklist, allow = cfg["blacklist"], set(cfg["live_allowlist"])
    only = {x.strip() for x in args.only.split(",") if x.strip()}
    for sid in blacklist:
        if sid not in stations:
            log.warning("blacklist ID %s (%s) not found in fleet any more", sid, blacklist[sid])
    rows, todo = [], []
    for u in units:
        base = {"station_id": u["station_id"], "station": u["name"], "sn": u["sn"], "max_soc": "", "points": "", "shutdown": "",
                "user_min": "", "current": "", "new": ""}
        if only and u["station_id"] not in only:
            continue
        if u["station_id"] in blacklist:
            rows.append({**base, "mode": "off", "result": "excluded", "note": blacklist[u["station_id"]]})
        elif u["product"] not in cfg["supported_products"]:
            rows.append({**base, "mode": "off", "result": "skipped", "note": "unsupported model %s" % u["product"]})
        elif not u["online"]:
            rows.append({**base, "mode": "off", "result": "skipped", "note": "offline"})
        else:
            todo.append(u)
    state_dir, backup_dir = ROOT / "state", ROOT / "backups" / today

    def job(u):
        mode = "live" if (u["station_id"] in allow and not args.dry_run) else "dry"
        r = process(u, tok, cfg, mode, today, state_dir, backup_dir, args.retry)
        log.info("%-24s [%-4s] cur=%s max=%s -> %s | %s | %s", u["name"], mode, r["current"], r["max_soc"], r["new"] or "-",
                 r["result"], r["note"])
        return r

    if not args.health_only:
        with ThreadPoolExecutor(max_workers=cfg["workers"]) as ex:
            rows += list(ex.map(job, todo))
        write_runs(rows)
        summarize(rows)
    if args.health_only or not (args.retry or args.no_health):
        try:
            run_health([u for u in units if not only or u["station_id"] in only], stations, tok, cfg, today,
                       fleet_units=units, partial=bool(only))
        except Exception:
            log.exception("health report failed")
    return 1 if any(r["result"] == "error" for r in rows) else 0


# ---------------------------------------------------------------- health report (reads only, no device commands)
def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def track_new_stations(stations, fleet_units, cfg, today, partial):
    """New stations (not seen in earlier runs) stay listed in the report for new_station_days days."""
    path = ROOT / "state" / "_known_stations.json"
    known = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    if known is None:  # first run: current fleet is the baseline, nothing is 'new'
        known = {sid: "baseline" for sid in stations}
    elif not partial:
        for sid in stations:
            known.setdefault(sid, today)
    if not partial:
        path.write_text(json.dumps(known, indent=1), encoding="utf-8")
    prod = {u["station_id"]: u["product"] for u in fleet_units}
    lang = cfg.get("report_language", "de")
    new = []
    for sid, since in known.items():
        if sid in stations and since != "baseline" and (dt.date.fromisoformat(today) - dt.date.fromisoformat(since)).days < cfg["health"].get("new_station_days", 14):
            if sid in cfg["blacklist"]:
                note = health.tr(lang)["note_blacklist"]
            elif prod.get(sid) not in cfg["supported_products"]:
                note = health.tr(lang)["note_type"] % prod.get(sid)
            else:
                note = health.tr(lang)["note_dry"]
            new.append({"name": stations[sid], "id": sid, "since": since, "note": note})
    with_inv = {u["station_id"] for u in fleet_units}
    no_inv = [{"name": stations[sid], "id": sid} for sid in stations if sid not in with_inv]
    return sorted(new, key=lambda n: n["since"]), sorted(no_inv, key=lambda n: n["name"])


def run_health(units, stations, tok, cfg, today, fleet_units=None, partial=False):
    h, acks, now = cfg["health"], cfg.get("health_acknowledged", {}), int(time.time())
    lang = cfg.get("report_language", "de")
    new_stations, no_inverter = track_new_stations(stations, fleet_units or units, cfg, today, partial)
    latest = {}
    online = [u for u in units if u["online"]]
    for i in range(0, len(online), 10):
        batch = online[i:i + 10]
        try:
            r = with_retry(lambda: api.call("/v1.0/device/latest", {"deviceList": [u["sn"] for u in batch]}, tok), cfg, "latest data")
            for d in r.get("deviceDataList") or []:
                m = {k["key"]: k["value"] for k in d["dataList"]}
                latest[d["deviceSn"]] = {"time": d.get("collectionTime"), "soc": _num(m.get("SOC")), "bms": _num(m.get("BMSSOC")),
                                         "state": d.get("deviceState"), "volt": _num(m.get("BatteryVoltage"))}
        except Exception as e:
            log.warning("health: latest data batch failed: %s", str(e)[:100])

    def check(u):
        sn, state_file = u["sn"], ROOT / "state" / ("%s.json" % u["sn"])
        state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}
        lt = latest.get(sn, {})
        inp = {"now": now, "online": u["online"], "station_status": u["station_status"], "series": [], "pv": [], "load": [],
               "battery": [], "soc_now": lt.get("soc"), "bms_soc": lt.get("bms"), "busy_streak": state.get("busy_streak", 0),
               "alerts": [], "device_state": lt.get("state"), "battery_voltage": lt.get("volt"),
               "collection_time": lt.get("time") or u.get("collection_time"),
               "floor": (state["shutdown"] + cfg["min_soc_offset_over_shutdown"]) if "shutdown" in state else None}
        try:
            if u["online"]:
                for a in open_alerts(tok, sn, h.get("alert_days", 7), cfg):
                    inp["alerts"].append({"name": a["name"], "level": a["level"],
                                          "since": time.strftime("%d.%m. %H:%M", time.localtime(a["start"]))})
                r = with_retry(lambda: api.call("/v1.0/device/historyRaw", {
                    "deviceSn": sn, "startTimestamp": now - h["window_hours"] * 3600, "endTimestamp": now,
                    "measurePoints": ["SOC", "BatteryPower", "TotalSolarPower", "TotalConsumptionPower"]}, tok), cfg, "health history")
                for d in r.get("dataList") or []:
                    for it in d.get("itemList") or []:
                        v = _num(it.get("value"))
                        if v is None:
                            continue
                        if it["key"] == "SOC":
                            inp["series"].append((int(d["time"]), v))
                        else:
                            inp[{"BatteryPower": "battery", "TotalSolarPower": "pv", "TotalConsumptionPower": "load"}[it["key"]]].append(v)
            found = health.evaluate(inp, h, lang)
        except Exception as e:
            found = [("check_failed", "%s: %s" % (type(e).__name__, str(e)[:120]))]
        first = state.get("health", {})
        state["health"] = {rule: first.get(rule, today) for rule, _ in found}
        state_file.write_text(json.dumps(state, indent=1), encoding="utf-8")
        fs = []
        for rule, detail in found:
            acked, note = health.ack_for(acks, u["station_id"], rule, today, lang)
            fs.append({"rule": rule, "detail": detail, "since": state["health"][rule], "acked": acked, "note": note})
        return {"station": u["name"], "station_id": u["station_id"], "sn": sn, "findings": fs}

    with ThreadPoolExecutor(max_workers=cfg["workers"] + 1) as ex:
        entries = list(ex.map(check, units))
    meta = {"time": dt.datetime.now().strftime("%d.%m.%Y %H:%M"), "units": len(units), "stations": len({u["station_id"] for u in units}),
            "health": h, "lang": lang, "new": new_stations, "no_inverter": no_inverter}
    text = health.render(entries, meta, acks, today)
    (ROOT / "reports").mkdir(exist_ok=True)
    report_name = health.REPORT_FILES.get(lang, health.REPORT_FILES["de"])
    (ROOT / report_name).write_text(text, encoding="utf-8")
    (ROOT / "reports" / ("health_%s.md" % today)).write_text(text, encoding="utf-8")
    open_names = sorted({e["station"] for e in entries if any(not f["acked"] for f in e["findings"])})
    log.info("HEALTH: %d stations with open findings (%s), %d acknowledged findings -> %s", len(open_names),
             ", ".join(open_names) or "-", sum(1 for e in entries for f in e["findings"] if f["acked"]), report_name)


def write_runs(rows):
    path = ROOT / "runs.csv"
    cols = ["time", "station_id", "station", "sn", "mode", "max_soc", "points", "shutdown", "user_min", "current", "new", "result", "note"]
    new_file = not path.exists()
    with open(path, "a", newline="", encoding="utf-8-sig" if new_file else "utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, delimiter=";", extrasaction="ignore")
        if new_file:
            w.writeheader()
        now = dt.datetime.now().isoformat(timespec="seconds")
        for r in rows:
            w.writerow({"time": now, **r})


def summarize(rows):
    counts = {}
    for r in rows:
        counts[r["result"]] = counts.get(r["result"], 0) + 1
    log.info("SUMMARY %s", ", ".join("%s=%d" % kv for kv in sorted(counts.items())))
    for r in rows:
        if r["result"] in ("error", "busy"):
            log.warning("PROBLEM %s (%s): %s - %s", r["station"], r["sn"], r["result"], r["note"])


if __name__ == "__main__":
    sys.exit(main())
