"""Health rules for Deye stations (pure functions, no I/O) and report rendering."""
import datetime as dt

RULE_TITLES = {
    "no_data": "Keine Daten",
    "offline": "Wechselrichter offline",
    "station_status": "Station nicht NORMAL",
    "stale_data": "Daten veraltet",
    "stuck_battery": "Akku bewegt sich nicht",
    "soc_mismatch": "SoC-Abweichung Wechselrichter/BMS",
    "commands": "Befehle gehen nicht durch",
    "check_failed": "Prüfung fehlgeschlagen",
}


def evaluate(i, h):
    """i: inputs of one inverter, h: health config. Returns [(rule, detail)].

    i keys: now, online, station_status, collection_time, series [(t, soc)], pv [W], load [W], battery [W],
            soc_now, bms_soc, floor, busy_streak
    """
    out = []
    age_h = (i["now"] - i["collection_time"]) / 3600 if i.get("collection_time") else None
    if not i["online"]:
        if age_h is None or age_h >= h["offline_hours"]:
            out.append(("offline", "letzte Daten vor %s" % (("%.0f h" % age_h) if age_h is not None else "unbekannter Zeit")))
    elif age_h is not None and age_h * 60 >= h["stale_minutes"]:
        out.append(("stale_data", "letzter Datenpunkt vor %.0f min" % (age_h * 60)))
    if i.get("station_status") not in (None, "NORMAL") and i["online"]:
        out.append(("station_status", "Status %s" % i["station_status"]))
    if i.get("busy_streak", 0) >= h["busy_days"]:
        out.append(("commands", "Befehle seit %d Tagen ohne Antwort (Datenlogger/Wechselrichter reagiert nicht)" % i["busy_streak"]))
    if not i["online"] or any(r in ("offline", "stale_data") for r, _ in out):
        return out  # the data based rules below need fresh data
    series = i["series"]
    if not series:
        out.append(("no_data", "keine SoC-Verlaufsdaten der letzten %d h" % h["window_hours"]))
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
        out.append(("stuck_battery", "SoC %d%% konstant über %.0f h, Batterieleistung max %.0f W, Last Ø %.0f W, PV max %.0f W"
                    % (max(socs), span_h, bp_max, load_avg, pv_max)))
    if i.get("bms_soc") is not None and i.get("soc_now") is not None and abs(i["soc_now"] - i["bms_soc"]) >= h["soc_mismatch"]:
        out.append(("soc_mismatch", "Wechselrichter %s%% / BMS %s%%" % (i["soc_now"], i["bms_soc"])))
    return out


def ack_for(acks, station_id, rule, today):
    """Returns (acked, note). An expired acknowledgement does not hide the finding."""
    a = acks.get(station_id)
    if not a:
        return False, ""
    rules = a.get("rules", "all")
    if rules != "all" and rule not in rules:
        return False, ""
    until = a.get("until")
    if until and until < today:
        return False, "Quittierung abgelaufen (%s): %s" % (until, a.get("reason", ""))
    return True, a.get("reason", "")


def _de(date_iso):
    return dt.date.fromisoformat(date_iso).strftime("%d.%m.")


def render(entries, meta, acks, today):
    """entries: [{station, station_id, sn, findings: [{rule, detail, since, acked, note}]}]"""
    open_rows, acked_rows = [], []
    for e in entries:
        for f in e["findings"]:
            days = (dt.date.fromisoformat(today) - dt.date.fromisoformat(f["since"])).days + 1
            since = "%s (%d. Tag)" % (_de(f["since"]), days)
            label = "%s (%s)" % (e["station"], e["station_id"])
            title = RULE_TITLES.get(f["rule"], f["rule"])
            if f["acked"]:
                until = acks.get(e["station_id"], {}).get("until") or "unbefristet"
                acked_rows.append("| %s | %s | %s | %s |" % (label, title, f["note"], until))
            else:
                detail = f["detail"] + (" – " + f["note"] if f["note"] else "")
                open_rows.append("| %s | %s | %s | %s |" % (label, title, detail, since))
    n_units, n_open = meta["units"], len({r.split("|")[1] for r in open_rows})
    lines = ["# Gesundheitsbericht Deye-Anlagen", "",
             "Stand: %s · geprüft: %d Wechselrichter in %d Stationen · **offen: %d Anlagen** · quittiert: %d Befunde"
             % (meta["time"], n_units, meta["stations"], n_open, len(acked_rows)), "", "## Offen", ""]
    if open_rows:
        lines += ["| Anlage | Befund | Details | seit |", "|---|---|---|---|"] + sorted(open_rows)
    else:
        lines.append("Keine offenen Befunde.")
    if meta.get("new"):
        lines += ["", "## Neue Anlagen (laufen nur im Dry-Run, bis sie in `live_allowlist` stehen)", "",
                  "| Anlage | seit | Hinweis |", "|---|---|---|"]
        lines += ["| %s (%s) | %s | %s |" % (n["name"], n["id"], _de(n["since"]), n["note"]) for n in meta["new"]]
    if meta.get("no_inverter"):
        lines += ["", "## Stationen ohne Wechselrichter (werden nicht geprüft)", ""]
        lines += ["- %s (%s)" % (n["name"], n["id"]) for n in meta["no_inverter"]]
    lines += ["", "## Quittiert (bekannt, nur zur Übersicht)", ""]
    if acked_rows:
        lines += ["| Anlage | Befund | Grund | gültig bis |", "|---|---|---|---|"] + sorted(acked_rows)
    else:
        lines.append("Keine.")
    lines += ["", "## Prüfregeln", "",
              "- **Akku bewegt sich nicht**: SoC schwankt höchstens %(stuck_max_range)s Punkt über mindestens %(stuck_min_span_hours)s h, liegt "
              "mindestens %(stuck_above_floor)s Punkte über dem Minimum und unter %(stuck_max_soc)s %%, es gibt Last oder PV und die Batterieleistung bleibt unter "
              "%(stuck_max_battery_w)s W." % meta["health"],
              "- **Offline / Daten veraltet**: Wechselrichter länger als %(offline_hours)s h offline bzw. letzter Datenpunkt älter als %(stale_minutes)s min." % meta["health"],
              "- **Befehle gehen nicht durch**: mindestens %(busy_days)s Tage hintereinander keine Antwort auf Lesebefehle." % meta["health"],
              "- **SoC-Abweichung**: Wechselrichter-SoC und BMS-SoC weichen um mindestens %(soc_mismatch)s Punkte ab." % meta["health"],
              "- Quittieren: Eintrag unter `health_acknowledged` in `config.json` (Station-ID, Grund, optional `until`).", ""]
    return "\n".join(lines)
