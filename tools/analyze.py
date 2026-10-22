import json, collections, sys
sys.stdout.reconfigure(encoding="utf-8")
rows = [json.loads(l) for l in open("fleet_raw.jsonl", encoding="utf-8")]
REG = ["01:00", "05:00", "09:00", "13:00", "17:00", "21:00"]
res = []
for r in rows:
    o = {"station": r["station"], "sn": r["sn"], "main": r["main"], "hmi": r["hmi"], "product": r["product"]}
    t, g = r.get("tou", {}), r.get("regs", {})
    items = t.get("timeUseSettingItems")
    regs = {int(k): v for k, v in g["regs"].items()} if g.get("regs") else None
    o["err_tou"] = None if items else json.dumps(t.get("error"), ensure_ascii=False)[:160]
    o["err_regs"] = None if regs else json.dumps(g.get("error"), ensure_ascii=False)[:160]
    if items:
        o["times"] = [i["time"] for i in items]; o["soc"] = [i["soc"] for i in items]
        o["gc"] = [bool(i["enableGridCharge"]) for i in items]
        o["tou_on"] = t.get("touAction"); o["gridChargeAction"] = t.get("gridChargeAction")
    if regs:
        g2 = lambda a, b: [regs[x] for x in range(a, b + 1)]
        o["shut"], o["restart"], o["low"] = regs[115], regs[116], regs[117]
        o["m_times"] = ["%02d:%02d" % divmod(v, 100) for v in g2(148, 153)]
        o["m_soc"] = g2(166, 171); o["m_gc"] = [bool(v) for v in g2(172, 177)]
    if items and regs:
        o["consistent"] = o["times"] == o["m_times"] and o["soc"] == o["m_soc"] and o["gc"] == o["m_gc"]
    res.append(o)
json.dump(res, open("fleet_analysis.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

ok = [o for o in res if "times" in o and "shut" in o]
print("gelesen: %d von %d  | TOU-Fehler: %d | Register-Fehler: %d" % (len(ok), len(res), sum(1 for o in res if o["err_tou"]), sum(1 for o in res if o["err_regs"])))
for o in res:
    if o["err_tou"] or o["err_regs"]: print("  FEHLER", o["station"], o["sn"], "| tou:", o["err_tou"], "| regs:", o["err_regs"])
print("\nTOU-Modbus-Konsistenz (Zeiten, SoC, Netzladung gleich):", collections.Counter(o.get("consistent") for o in ok))
for o in ok:
    if not o["consistent"]: print("  ABWEICHUNG", o["station"], o["times"], o["m_times"], o["soc"], o["m_soc"], o["gc"], o["m_gc"])
print("\nTabellen-Zeiten regelmäßig (01,05,09,13,17,21):", collections.Counter(o["times"] == REG for o in ok))
print("\nSoC einheitlich in allen 6 Zeilen:", collections.Counter(len(set(o["soc"])) == 1 for o in ok))
print("\nAbschalt-SoC-Verteilung:", sorted(collections.Counter(o["shut"] for o in ok).items()))
print("Restart:", sorted(collections.Counter(o["restart"] for o in ok).items()), " Low:", sorted(collections.Counter(o["low"] for o in ok).items()))
print("TOU-SoC (Zeile 1) minus Abschalt-SoC:", sorted(collections.Counter(o["soc"][0] - o["shut"] for o in ok if len(set(o["soc"])) == 1).items()))
def gcp(o):
    return "alle an" if all(o["gc"]) else "alle aus" if not any(o["gc"]) else "gemischt"
print("Netzladung:", collections.Counter(gcp(o) for o in ok))
print("TOU aktiv:", collections.Counter(o["tou_on"] for o in ok), " Netzladung global:", collections.Counter(o["gridChargeAction"] for o in ok))
print("\nUnregelmäßige Zeiten:")
for o in ok:
    if o["times"] != REG: print("  ", o["station"], o["times"], "gc", ["J" if x else "N" for x in o["gc"]], "soc", o["soc"], "shut", o["shut"])
print("\nSoC nicht einheitlich:")
for o in ok:
    if len(set(o["soc"])) != 1: print("  ", o["station"], o["soc"], "shut", o["shut"])
print("\nTOU-SoC != Abschalt-SoC+5 (einheitliche Tabellen):")
for o in ok:
    if len(set(o["soc"])) == 1 and o["soc"][0] != o["shut"] + 5: print("  ", o["station"], "soc", o["soc"][0], "shut", o["shut"], "restart", o["restart"], "low", o["low"])
