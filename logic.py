"""Pure decision logic for Deye BatteryLife (no I/O, unit-testable)."""


def hm_to_min(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def decide_soc(current, max_soc, user_min, cap, low_thr, high_thr, step):
    """Victron-BatteryLife-style daily adjustment of the dynamic minimum SoC.

    max_soc < low_thr           -> raise by step (battery did not get full enough)
    low_thr <= max_soc < high   -> keep
    max_soc >= high_thr         -> lower by step
    The result is always clamped into [user_min, cap]. Returns (new_soc, reason).
    """
    hi = max(user_min, cap)
    if max_soc < low_thr:
        proposed, why = current + step, "max %d%% < %d%%: raise" % (max_soc, low_thr)
    elif max_soc >= high_thr:
        proposed, why = current - step, "max %d%% >= %d%%: lower" % (max_soc, high_thr)
    else:
        proposed, why = current, "max %d%% in %d..%d%%: keep" % (max_soc, low_thr, high_thr - 1)
    new = min(max(proposed, user_min), hi)
    if new != proposed:
        why += " (clamped to %d)" % new
    elif new == current and proposed != current:
        why += " (limit reached)"
    return new, why


def plan_grid_charge(times, zone, min_row_minutes):
    """Grid-charge flags per TOU row: on only for rows lying completely inside the night zone.

    TOU row i is active from times[i] until times[i+1] (cyclic, wrapping at midnight).
    Returns (flags, note). flags is None when the table cannot be interpreted or no row
    fits the night zone - the caller then leaves the existing flags untouched.
    """
    t = [hm_to_min(x) for x in times]
    n = len(t)
    lens = [(t[(i + 1) % n] - t[i]) % 1440 for i in range(n)]
    if sum(lens) != 1440:
        return None, "irregular time table (rows overlap / wrap twice): grid-charge flags unchanged"
    zs, ze = hm_to_min(zone[0]), hm_to_min(zone[1])
    zlen = (ze - zs) % 1440 or 1440
    flags = [lens[i] >= min_row_minutes and ((t[i] - zs) % 1440) + lens[i] <= zlen for i in range(n)]
    if not any(flags):
        return None, "no row inside night zone %s-%s: grid-charge flags unchanged" % tuple(zone)
    return flags, ""


def build_items(items, new_soc, flags):
    """New 6-row TOU table: times/power/voltage/generation/sell as read, SoC set, grid-charge per flags."""
    out = []
    for i, it in enumerate(items):
        out.append({
            "time": it["time"],
            "power": it["power"],
            "voltage": it["voltage"],
            "soc": new_soc,
            "enableGridCharge": bool(it["enableGridCharge"]) if flags is None else bool(flags[i]),
            "enableGeneration": bool(it["enableGeneration"]),
            "enableSell": bool(it.get("enableSell", False)),
        })
    return out


def same_table(a, b):
    keys = ("time", "soc", "enableGridCharge", "power", "voltage")
    return len(a) == len(b) and all(all(x[k] == y[k] for k in keys) for x, y in zip(a, b))


def apply_ignored(units, ignored):
    """Drop individual inverters (by serial number) from every part of the run.

    Returns (kept_units, stations_with_ignored_inverters, ignored_serials_not_found).
    """
    kept = [u for u in units if u["sn"] not in ignored]
    stations = {u["station_id"] for u in units if u["sn"] in ignored}
    missing = sorted(set(ignored) - {u["sn"] for u in units})
    return kept, stations, missing


def fault_reason(alerts, voltage, voltage_min, min_level):
    """Why a station must not get an SoC adjustment right now ('' = fine).

    alerts: open (not closed) DeyeCloud alerts [{name, level}]; voltage: battery voltage in V or None.
    """
    serious = [a for a in alerts if (a.get("level") or 0) >= min_level]
    if serious:
        return "; ".join("%s (level %s)" % (a["name"], a["level"]) for a in serious)
    if voltage is not None and voltage < voltage_min:
        return "battery voltage %.1f V below %s V" % (voltage, voltage_min)
    return ""
