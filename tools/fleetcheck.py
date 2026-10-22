"""READ-ONLY fleet check: per inverter read TOU table (dynamicControl/read) + Modbus regs 100-199."""
import json, sys, time, threading
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, __import__("os").path.dirname(__file__))
import deye_api as d

OUT = sys.argv[1]
tok = d.business_token()
inv = json.load(open(sys.argv[2]))  # inventory.json
FRAME = "01030064006405FE"  # read regs 100..199
lock = threading.Lock()


def retry(fn, tries=8, pause=6):
    last = None
    for _ in range(tries):
        try:
            r = fn()
        except Exception as e:  # timeouts / 5xx
            last = {"exc": str(e)[:200]}
            time.sleep(pause); continue
        if r.get("success"):
            return r
        last = r
        if r.get("code") in ("2104004",):  # command concurrent running
            time.sleep(pause); continue
        return r
    return last


def wait_order(oid, tries=15, pause=3):
    for _ in range(tries):
        r = d.get(f"/v1.0/order/{oid}", tok)
        if r.get("success") and r.get("analysisResult"):
            return r
        time.sleep(pause)
    return r


def read_tou(sn):
    r = retry(lambda: d.call("/v1.0/strategy/dynamicControl/read", {"deviceSn": sn}, tok))
    if not r or not r.get("success"):
        return {"error": r}
    for _ in range(15):
        res = d.call("/v1.0/strategy/dynamicControl/readResult", {"orderId": r["orderId"]}, tok)
        if res.get("success") and res.get("timeUseSettingItems"):
            return res
        time.sleep(3)
    return {"error": res}


def read_regs(sn):
    r = retry(lambda: d.call("/v1.0/order/customControl", {"deviceSn": sn, "content": FRAME, "timeoutSeconds": 60}, tok))
    if not r or not r.get("success"):
        return {"error": r}
    res = wait_order(r["orderId"])
    a = (res or {}).get("analysisResult")
    if not a or not a.startswith("0103"):
        return {"error": res}
    n = int(a[4:6], 16)
    data = a[6:6 + n * 2]
    return {"regs": {100 + i: int(data[i*4:i*4+4], 16) for i in range(n // 2)}}


def one(x):
    sn = x["deviceSn"] if "deviceSn" in x else x["sn"]
    out = {"station": x["station"], "sn": sn, "product": x["product"], "main": x["main"], "hmi": x["hmi"], "rated": x["rated"]}
    t0 = time.time()
    try:
        tou = read_tou(sn)
        out["tou"] = tou
        time.sleep(2)
        out["regs"] = read_regs(sn)
    except Exception as e:
        out["fatal"] = str(e)[:300]
    out["secs"] = round(time.time() - t0)
    with lock:
        with open(OUT + "l", "a", encoding="utf-8") as f:
            f.write(json.dumps(out, ensure_ascii=False) + chr(10))
        print(out["station"], "ok" if "fatal" not in out and "regs" in out["regs"] and "timeUseSettingItems" in out["tou"] else "PROBLEM", out["secs"], "s", flush=True)
    return out


todo = [x for x in inv if x["online"] == 1]
print("online inverters:", len(todo), "skipped offline:", [x["station"] for x in inv if x["online"] != 1], flush=True)
with ThreadPoolExecutor(max_workers=3) as ex:
    results = list(ex.map(one, todo))
json.dump(results, open(OUT, "w"), indent=1, ensure_ascii=False)
print("DONE", len(results))
