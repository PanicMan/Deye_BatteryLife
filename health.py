"""Health rules for Deye stations (pure functions, no I/O) and report rendering (German or English)."""
import datetime as dt

REPORT_FILES = {"de": "Gesundheitsbericht.md", "en": "health_report.md"}

T = {
    "de": {
        "title": "Gesundheitsbericht Deye-Anlagen",
        "rules": {"no_data": "Keine Daten", "offline": "Wechselrichter offline", "station_status": "Station nicht NORMAL",
                  "stale_data": "Daten veraltet", "stuck_battery": "Akku bewegt sich nicht",
                  "soc_mismatch": "SoC-Abweichung Wechselrichter/BMS", "commands": "Befehle gehen nicht durch",
                  "check_failed": "Prüfung fehlgeschlagen"},
        "last_data": "letzte Daten vor %s", "unknown_time": "unbekannter Zeit", "stale": "letzter Datenpunkt vor %.0f min",
        "status": "Status %s", "commands": "Befehle seit %d Tagen ohne Antwort (Datenlogger/Wechselrichter reagiert nicht)",
        "no_history": "keine SoC-Verlaufsdaten der letzten %d h",
        "stuck": "SoC %d%% konstant über %.0f h, Batterieleistung max %.0f W, Last Ø %.0f W, PV max %.0f W",
        "mismatch": "Wechselrichter %s%% / BMS %s%%", "ack_expired": "Quittierung abgelaufen (%s): %s",
        "summary": "Stand: %s · geprüft: %d Wechselrichter in %d Stationen · **offen: %d Anlagen** · quittiert: %d Befunde",
        "h_open": "Offen", "none_open": "Keine offenen Befunde.", "cols_open": ["Anlage", "Befund", "Details", "seit"],
        "day": "%s (%d. Tag)",
        "h_new": "Neue Anlagen (laufen nur im Dry-Run, bis sie in `live_allowlist` stehen)", "cols_new": ["Anlage", "seit", "Hinweis"],
        "h_noinv": "Stationen ohne Wechselrichter (werden nicht geprüft)",
        "h_acked": "Quittiert (bekannt, nur zur Übersicht)", "none": "Keine.", "cols_acked": ["Anlage", "Befund", "Grund", "gültig bis"],
        "unlimited": "unbefristet", "h_rules": "Prüfregeln",
        "r_stuck": "- **Akku bewegt sich nicht**: SoC schwankt höchstens %(stuck_max_range)s Punkt über mindestens %(stuck_min_span_hours)s h, liegt mindestens %(stuck_above_floor)s Punkte über dem Minimum und unter %(stuck_max_soc)s %%, es gibt Last oder PV und die Batterieleistung bleibt unter %(stuck_max_battery_w)s W.",
        "r_offline": "- **Offline / Daten veraltet**: Wechselrichter länger als %(offline_hours)s h offline bzw. letzter Datenpunkt älter als %(stale_minutes)s min.",
        "r_commands": "- **Befehle gehen nicht durch**: mindestens %(busy_days)s Tage hintereinander keine Antwort auf Lesebefehle.",
        "r_mismatch": "- **SoC-Abweichung**: Wechselrichter-SoC und BMS-SoC weichen um mindestens %(soc_mismatch)s Punkte ab.",
        "r_ack": "- Quittieren: Eintrag unter `health_acknowledged` in `config.json` (Station-ID, Grund, optional `until`).",
        "note_blacklist": "Blacklist", "note_type": "Typ %s nicht unterstützt",
        "note_dry": "Dry-Run; in live_allowlist eintragen zum Scharfschalten",
    },
    "en": {
        "title": "Deye station health report",
        "rules": {"no_data": "No data", "offline": "Inverter offline", "station_status": "Station not NORMAL",
                  "stale_data": "Data outdated", "stuck_battery": "Battery not moving",
                  "soc_mismatch": "SoC mismatch inverter/BMS", "commands": "Commands not answered",
                  "check_failed": "Check failed"},
        "last_data": "last data %s ago", "unknown_time": "an unknown time", "stale": "last data point %.0f min ago",
        "status": "status %s", "commands": "no answer to commands for %d days (data logger/inverter not responding)",
        "no_history": "no SoC history for the last %d h",
        "stuck": "SoC constant at %d%% for %.0f h, battery power max %.0f W, load avg %.0f W, PV max %.0f W",
        "mismatch": "inverter %s%% / BMS %s%%", "ack_expired": "acknowledgement expired (%s): %s",
        "summary": "As of: %s · checked: %d inverters in %d stations · **open: %d stations** · acknowledged: %d findings",
        "h_open": "Open", "none_open": "No open findings.", "cols_open": ["Station", "Finding", "Details", "since"],
        "day": "%s (day %d)",
        "h_new": "New stations (dry-run only until added to `live_allowlist`)", "cols_new": ["Station", "since", "Note"],
        "h_noinv": "Stations without an inverter (not checked)",
        "h_acked": "Acknowledged (known, for reference only)", "none": "None.", "cols_acked": ["Station", "Finding", "Reason", "valid until"],
        "unlimited": "unlimited", "h_rules": "Rules",
        "r_stuck": "- **Battery not moving**: SoC varies by at most %(stuck_max_range)s point over at least %(stuck_min_span_hours)s h, is at least %(stuck_above_floor)s points above the minimum and below %(stuck_max_soc)s %%, there is load or PV and battery power stays below %(stuck_max_battery_w)s W.",
        "r_offline": "- **Offline / outdated data**: inverter offline for more than %(offline_hours)s h or last data point older than %(stale_minutes)s min.",
        "r_commands": "- **Commands not answered**: no answer to read commands on at least %(busy_days)s consecutive days.",
        "r_mismatch": "- **SoC mismatch**: inverter SoC and BMS SoC differ by at least %(soc_mismatch)s points.",
        "r_ack": "- Acknowledge: add an entry under `health_acknowledged` in `config.json` (station ID, reason, optional `until`).",
        "note_blacklist": "blacklisted", "note_type": "type %s not supported",
        "note_dry": "dry-run; add to live_allowlist to go live",
    },
}


def tr(lang):
    return T.get(lang, T["de"])


def evaluate(i, h, lang="de"):
    """i: inputs of one inverter, h: health config. Returns [(rule, detail)].

    i keys: now, online, station_status, collection_time, series [(t, soc)], pv [W], load [W], battery [W],
            soc_now, bms_soc, floor, busy_streak
    """
    t, out = tr(lang), []
    age_h = (i["now"] - i["collection_time"]) / 3600 if i.get("collection_time") else None
    if not i["online"]:
        if age_h is None or age_h >= h["offline_hours"]:
            out.append(("offline", t["last_data"] % (("%.0f h" % age_h) if age_h is not None else t["unknown_time"])))
    elif age_h is not None and age_h * 60 >= h["stale_minutes"]:
        out.append(("stale_data", t["stale"] % (age_h * 60)))
    if i.get("station_status") not in (None, "NORMAL") and i["online"]:
        out.append(("station_status", t["status"] % i["station_status"]))
    if i.get("busy_streak", 0) >= h["busy_days"]:
        out.append(("commands", t["commands"] % i["busy_streak"]))
    if not i["online"] or any(r in ("offline", "stale_data") for r, _ in out):
        return out  # the data based rules below need fresh data
    series = i["series"]
    if not series:
        out.append(("no_data", t["no_history"] % h["window_hours"]))
        return out
    socs = [v for _, v in series]
    span_h = (series[-1][0] - series[0][0]) / 3600
    soc_range = max(socs) - min(socs)
    floor = i.get("floor") or h["assumed_floor_soc"]
    bp_max = max((abs(v) for v in i["battery"]), default=0)
    load_avg = sum(i["load"]) / len(i["load"]) if i["load"] else 0
    pv_max = max(i["pv"], default=0)
    if (span_h >= h["stuck_min_span_hours"] and soc_range <= h["stuck_max_range"]
            and floor + h["stuck_above_floor"] <= max(socs) < h["stuck_max_soc"]
            and (load_avg >= h["stuck_min_load_w"] or pv_max >= h["stuck_min_pv_w"]) and bp_max < h["stuck_max_battery_w"]):
        out.append(("stuck_battery", t["stuck"] % (max(socs), span_h, bp_max, load_avg, pv_max)))
    if i.get("bms_soc") is not None and i.get("soc_now") is not None and abs(i["soc_now"] - i["bms_soc"]) >= h["soc_mismatch"]:
        out.append(("soc_mismatch", t["mismatch"] % (i["soc_now"], i["bms_soc"])))
    return out


def ack_for(acks, station_id, rule, today, lang="de"):
    """Returns (acked, note). An expired acknowledgement does not hide the finding."""
    a = acks.get(station_id)
    if not a:
        return False, ""
    rules = a.get("rules", "all")
    if rules != "all" and rule not in rules:
        return False, ""
    until = a.get("until")
    if until and until < today:
        return False, tr(lang)["ack_expired"] % (until, a.get("reason", ""))
    return True, a.get("reason", "")


def _dm(date_iso):
    return dt.date.fromisoformat(date_iso).strftime("%d.%m.")


def _table(cols):
    return ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]


def render(entries, meta, acks, today):
    """entries: [{station, station_id, sn, findings: [{rule, detail, since, acked, note}]}]; meta['lang'] selects the language."""
    t = tr(meta.get("lang", "de"))
    open_rows, acked_rows = [], []
    for e in entries:
        for f in e["findings"]:
            days = (dt.date.fromisoformat(today) - dt.date.fromisoformat(f["since"])).days + 1
            since = t["day"] % (_dm(f["since"]), days)
            label = "%s (%s)" % (e["station"], e["station_id"])
            title = t["rules"].get(f["rule"], f["rule"])
            if f["acked"]:
                until = acks.get(e["station_id"], {}).get("until") or t["unlimited"]
                acked_rows.append("| %s | %s | %s | %s |" % (label, title, f["note"], until))
            else:
                detail = f["detail"] + (" – " + f["note"] if f["note"] else "")
                open_rows.append("| %s | %s | %s | %s |" % (label, title, detail, since))
    n_open = len({r.split("|")[1] for r in open_rows})
    lines = ["# " + t["title"], "", t["summary"] % (meta["time"], meta["units"], meta["stations"], n_open, len(acked_rows)),
             "", "## " + t["h_open"], ""]
    lines += (_table(t["cols_open"]) + sorted(open_rows)) if open_rows else [t["none_open"]]
    if meta.get("new"):
        lines += ["", "## " + t["h_new"], ""] + _table(t["cols_new"])
        lines += ["| %s (%s) | %s | %s |" % (n["name"], n["id"], _dm(n["since"]), n["note"]) for n in meta["new"]]
    if meta.get("no_inverter"):
        lines += ["", "## " + t["h_noinv"], ""] + ["- %s (%s)" % (n["name"], n["id"]) for n in meta["no_inverter"]]
    lines += ["", "## " + t["h_acked"], ""]
    lines += (_table(t["cols_acked"]) + sorted(acked_rows)) if acked_rows else [t["none"]]
    lines += ["", "## " + t["h_rules"], ""] + [t[k] % meta["health"] for k in ("r_stuck", "r_offline", "r_commands", "r_mismatch")] + [t["r_ack"], ""]
    return "\n".join(lines)
