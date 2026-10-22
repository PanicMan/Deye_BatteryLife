import collections, json, sys
sys.path.insert(0, __import__("os").path.dirname(__file__))
import deye_api as d

tok = d.business_token()
st = d.call("/v1.0/station/list", {"page": 1, "size": 200}, tok)["stationList"]
names = {s["id"]: s["name"] for s in st}
ids = list(names)
inv = []
for i in range(0, len(ids), 5):
    r = d.call("/v1.0/station/device", {"page": 1, "size": 200, "stationIds": ids[i:i+5]}, tok)
    if not r.get("success"): print("ERR", r)
    for x in r.get("deviceListItems") or []:
        if x["deviceType"] == "INVERTER":
            inv.append(x)
print("stations", len(st), "inverters", len(inv))
info = {}
sns = [x["deviceSn"] for x in inv]
for i in range(0, len(sns), 10):
    r = d.call("/v1.0/device/latest", {"deviceList": sns[i:i+10]}, tok)
    for dev in r.get("deviceDataList") or []:
        m = {k["key"]: k["value"] for k in dev["dataList"]}
        info[dev["deviceSn"]] = m
rows = []
for x in inv:
    m = info.get(x["deviceSn"], {})
    rows.append(dict(station=names[x["stationId"]], sn=x["deviceSn"], online=x["connectStatus"],
                     product=x.get("productId"), rated=m.get("RatedPower"), main=m.get("MAIN"),
                     hmi=m.get("HMI"), cap=m.get("BatteryRatedCapacity"), soc=m.get("SOC"),
                     phases=sum(1 for k in ("ACVoltageRUA","ACVoltageSVB","ACVoltageTWC") if float(m.get(k) or 0) > 100)))
json.dump(rows, open(sys.argv[1], "w"), indent=1)
c = collections.Counter((r["product"], r["rated"], r["phases"]) for r in rows)
print("(productId, ratedW, phases) -> count")
for k, v in sorted(c.items(), key=lambda kv: -kv[1]): print(k, v)
print("no telemetry:", [r["station"] for r in rows if not r["rated"]])
print("offline:", [r["station"] for r in rows if r["online"] != 1])
