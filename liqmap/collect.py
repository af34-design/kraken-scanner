#!/usr/bin/env python3
"""
Liquidation heat map data collector (runs hourly in GitHub Actions).

Pulls exact public data straight from the exchanges, keeps a rolling history
per coin, computes estimated liquidation levels, and flags price/cluster alerts.

  python liqmap/collect.py --data data             # normal hourly update
  python liqmap/collect.py --data data --backfill  # rebuild all history

Writes into the --data folder (committed to the `liq-data` branch):
  coins/<coin>.json   hourly open interest, liquidations, funding, Crypto.com OI
  levels.json         current price, biggest clusters, nearby bands, alerts (bots can read this)
  status.json         when it ran and anything that failed

Standard-library Python 3.8+ only. Market data, not financial advice.
"""
import argparse, calendar, json, math, os, sys, time, urllib.request, urllib.error

COINS = {  # coin id -> (Kraken perpetual, Crypto.com perpetual or None, ticker)
    "bitcoin": ("PF_XBTUSD", "BTCUSD-PERP", "BTC"),
    "ethereum": ("PF_ETHUSD", "ETHUSD-PERP", "ETH"),
    "solana": ("PF_SOLUSD", "SOLUSD-PERP", "SOL"),
    "ripple": ("PF_XRPUSD", "XRPUSD-PERP", "XRP"),
    "binancecoin": ("PF_BNBUSD", None, "BNB"),
    "dogecoin": ("PF_DOGEUSD", "DOGEUSD-PERP", "DOGE"),
    "crypto-com-chain": ("PF_CROUSD", "CROUSD-PERP", "CRO"),
    "pump-fun": ("PF_PUMPUSD", "PUMPUSD-PERP", "PUMP"),
}
H, H4 = 3600, 14400
KEEP_HOURS = 120 * 24          # rolling hourly history kept per coin
HOURLY_BACKFILL_DAYS = 40      # Kraken returns up to 1000 points per request
COARSE_DAYS = 95               # 4-hour history before the hourly window
TIERS = [(10, .30), (25, .30), (50, .25), (100, .15)]   # leverage, weight
MM = 0.005                     # maintenance margin
BINS = 170
ALERT_PCT = float(os.environ.get("LIQ_ALERT_PCT") or 1.5)   # alert when price is this close to a big cluster
ALERT_MIN_OI_USD = 5e6         # skip alerts on very thin markets
REALERT_HOURS = 6
ALERT_MIN_CLUSTER_SHARE = 0.08    # cluster must hold at least 8% of all mapped liquidations on its side...
ALERT_MIN_CLUSTER_USD = 50_000    # ...and at least $50K


def money(v):
    a = abs(v)
    return (f"${v / 1e9:.2f}B" if a >= 1e9 else f"${v / 1e6:.1f}M" if a >= 1e6
            else f"${v / 1e3:.0f}K" if a >= 1e3 else f"${v:.0f}")

KF = "https://futures.kraken.com"
CDC = "https://api.crypto.com/exchange/v1/public/get-tickers?instrument_name="


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def get(url, tries=3):
    last = None
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "liq-heatmap/1.0"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode())
        except Exception as e:  # network hiccup: brief backoff, then retry
            last = e
            time.sleep(1.5 * (k + 1))
    raise RuntimeError(f"{url.split('?')[0]}: {last}")


# ----------------------------- exchange data -----------------------------
def kraken_analytics(sym, kind, since, interval, to=None):
    """{timestamp: value} for open-interest (close) or liquidation-volume, following `more` pages."""
    out, s = {}, int(since)
    for _ in range(20):
        url = f"{KF}/api/charts/v1/analytics/{sym}/{kind}?since={s}&interval={interval}"
        if to:
            url += f"&to={int(to)}"
        res = get(url).get("result") or {}
        ts, data = res.get("timestamp") or [], res.get("data") or []
        if len(ts) != len(data):
            raise RuntimeError(f"{sym} {kind}: timestamp/data length mismatch")
        for t, d in zip(ts, data):
            out[int(t)] = float(d[3]) if isinstance(d, list) else float(d)
        if not res.get("more") or not ts:
            break
        s = int(ts[-1]) + interval
    return out


def kraken_ticker(sym):
    t = get(f"{KF}/derivatives/api/v3/tickers/{sym}").get("ticker") or {}
    mark = float(t.get("markPrice") or 0)
    rel = (float(t["fundingRate"]) / mark) if mark and t.get("fundingRate") is not None else None
    return {"mark": mark, "oi": float(t.get("openInterest") or 0), "fundingRel": rel}


def kraken_funding_history(sym, since):
    rates = get(f"{KF}/derivatives/api/v4/historicalfundingrates?symbol={sym}").get("rates") or []
    out = {}
    for r in rates:
        try:
            t = calendar.timegm(time.strptime(r["timestamp"][:19], "%Y-%m-%dT%H:%M:%S"))
        except Exception:
            continue
        if t >= since and r.get("relativeFundingRate") is not None:
            out[t - t % H] = float(r["relativeFundingRate"])
    return out


def kraken_prices(sym, hours=168):
    now = int(time.time())
    url = f"{KF}/api/charts/v1/trade/{sym}/1h?from={now - hours * H}&to={now}"
    c = get(url).get("candles") or []
    return [int(x["time"]) // 1000 for x in c], [float(x["close"]) for x in c]


def cdc_ticker(inst):
    d = (get(CDC + inst).get("result") or {}).get("data") or []
    if not d:
        return None
    return {"oi": float(d[0]["oi"]), "t": int(d[0]["t"]) // 1000, "last": float(d[0].get("a") or 0)}


# ----------------------------- series helpers -----------------------------
def to_series(points, start, interval, end):
    n = (end - start) // interval + 1
    arr = [None] * n
    for t, v in points.items():
        i = (t - start) // interval
        if 0 <= i < n and (t - start) % interval == 0:
            arr[i] = v
    return arr


def series_at(sr, t, key="oi"):
    if not sr or not isinstance(sr.get(key), list):
        return None
    a, s, h = sr[key], sr["start"], sr["interval"]
    f = (t - s) / h
    if f < 0 or f > len(a) - 1:
        return None
    i, j = math.floor(f), math.ceil(f)
    while i >= 0 and a[i] is None:
        i -= 1
    while j < len(a) and a[j] is None:
        j += 1
    if i < 0 and j >= len(a):
        return None
    if i < 0:
        return a[j]
    if j >= len(a):
        return a[i]
    if i == j:
        return a[i]
    return a[i] + (a[j] - a[i]) * (f - i) / (j - i)


def oi_at(doc, t):
    v = series_at(doc, t)
    if v is None and t < doc["start"] and doc.get("coarse"):
        v = series_at(doc["coarse"], t)
    return v


def long_share(doc, t):
    """Funding tilts the long/short split: positive funding = longs crowded."""
    r = series_at(doc.get("funding"), t, "rate")
    if r is None:
        return 0.5
    r8 = r * 8
    return 0.5 + max(-1.0, min(1.0, r8 / 0.0005)) * 0.2


def trim_front(sr, keep, keys):
    drop = len(sr[keys[0]]) - keep
    if drop > 0:
        for k in keys:
            sr[k] = sr[k][drop:]
        sr["start"] += drop * sr["interval"]


# ----------------------------- update one coin -----------------------------
def update_coin(coin, path, backfill, status):
    kf, cdc, tk = COINS[coin]
    now = int(time.time())
    now_h = now - now % H
    doc = None
    if os.path.exists(path) and not backfill:
        with open(path) as f:
            doc = json.load(f)
    rebuild = doc is None or not doc.get("oi") or doc.get("version") != 2
    if rebuild:
        start = now_h - HOURLY_BACKFILL_DAYS * 86400
        oi = kraken_analytics(kf, "open-interest", start, H)
        liq = kraken_analytics(kf, "liquidation-volume", start, H)
        doc = {"version": 2, "coin": coin, "symbol": kf, "interval": H, "start": start,
               "unit": "contracts (1 = 1 coin)", "source": "Kraken Futures analytics"}
        end = max(list(oi) + [start])
        doc["oi"] = to_series(oi, start, H, end)
        doc["liq"] = [v or 0.0 for v in to_series(liq, start, H, end)]
        cstart = start - COARSE_DAYS * 86400
        cstart -= cstart % H4
        coi = kraken_analytics(kf, "open-interest", cstart, H4, to=start - H4)
        cliq = kraken_analytics(kf, "liquidation-volume", cstart, H4, to=start - H4)
        cend = max(list(coi) + [cstart])
        doc["coarse"] = {"interval": H4, "start": cstart, "oi": to_series(coi, cstart, H4, cend),
                         "liq": [v or 0.0 for v in to_series(cliq, cstart, H4, cend)]}
        try:
            fh = kraken_funding_history(kf, start)
            doc["funding"] = {"interval": H, "start": start, "rate": to_series(fh, start, H, end)}
        except Exception as e:
            status["warnings"].append(f"{tk} funding history: {e}")
            doc["funding"] = {"interval": H, "start": start, "rate": []}
        log(f"{tk}: rebuilt {len(doc['oi'])} hourly + {len(doc['coarse']['oi'])} 4h points")
    else:
        last = doc["start"] + H * (len(doc["oi"]) - 1)
        since = last - 6 * H
        oi = kraken_analytics(kf, "open-interest", since, H)
        liq = kraken_analytics(kf, "liquidation-volume", since, H)
        for t in sorted(oi):
            i = (t - doc["start"]) // H
            if i < 0:
                continue
            while len(doc["oi"]) <= i:
                doc["oi"].append(None)
                doc["liq"].append(0.0)
            doc["oi"][i] = oi[t]
            doc["liq"][i] = liq.get(t, doc["liq"][i] or 0.0)
    # funding now (hourly snapshot from the ticker)
    tick = kraken_ticker(kf)
    fd = doc.setdefault("funding", {"interval": H, "start": doc["start"], "rate": []})
    if tick["fundingRel"] is not None:
        i = (now_h - fd["start"]) // H
        while len(fd["rate"]) <= i:
            fd["rate"].append(None)
        fd["rate"][i] = tick["fundingRel"]
    doc["fundingNow8h"] = tick["fundingRel"] * 8 if tick["fundingRel"] is not None else None
    # Crypto.com open interest snapshot
    if cdc:
        try:
            c = cdc_ticker(cdc)
            if c and c["oi"] > 0 and now - c["t"] < 900:
                sr = doc.setdefault("cdc", {"symbol": cdc, "interval": H, "start": c["t"] - c["t"] % H,
                                            "oi": [], "updatedAt": 0, "source": "Crypto.com Exchange public tickers"})
                i = (c["t"] - c["t"] % H - sr["start"]) // H
                while len(sr["oi"]) <= i:
                    sr["oi"].append(None)
                sr["oi"][i] = c["oi"]
                sr["updatedAt"] = c["t"]
                trim_front(sr, KEEP_HOURS, ["oi"])
        except Exception as e:
            status["warnings"].append(f"{tk} Crypto.com: {e}")
    trim_front(doc, KEEP_HOURS, ["oi", "liq"])
    trim_front(fd, KEEP_HOURS, ["rate"])
    if doc.get("coarse"):
        keep4 = max(0, (doc["start"] - (now_h - COARSE_DAYS * 86400)) // H4)
        trim_front(doc["coarse"], max(keep4, 1), ["oi", "liq"])
    lastv = len(doc["oi"]) - 1
    while lastv >= 0 and doc["oi"][lastv] is None:
        lastv -= 1
    doc["updatedAt"] = doc["start"] + H * max(lastv, 0)
    doc["collectedAt"] = now
    with open(path, "w") as f:
        json.dump(doc, f, separators=(",", ":"))
    return doc, tick


# ----------------------------- the model (mirrors the web page) -----------------------------
def build_levels(doc, times, prices):
    n = len(prices)
    if n < 3:
        return None
    lo, hi = min(prices), max(prices)
    pmin, pmax = lo * 0.9, hi * 1.1
    step = (pmax - pmin) / BINS
    add, close, real = [0.0] * n, [0.0] * n, [0] * n
    rsum = rn = 0
    for i in range(1, n):
        o0, o1 = oi_at(doc, times[i - 1]), oi_at(doc, times[i])
        if o0 is not None and o1 is not None:
            c0, c1 = series_at(doc.get("cdc"), times[i - 1]), series_at(doc.get("cdc"), times[i])
            d = (o1 - o0 + ((c1 - c0) if c0 is not None and c1 is not None else 0)) * prices[i]
            real[i] = 1
            if d > 0:
                add[i] = d
            else:
                close[i] = -d
            rsum += abs(d)
            rn += 1
    scale = rsum / rn / 0.006 if rn else 1e6
    for i in range(1, n):
        if not real[i]:
            add[i] = abs(math.log(prices[i] / prices[i - 1])) * scale + scale * 0.0005
    wsum = sum(w for _, w in TIERS)
    longs, shorts = [], []
    for i in range(n):
        p = prices[i]
        prev = prices[i - 1] if i else p
        low, high = min(p, prev), max(p, prev)
        if i:
            longs = [l for l in longs if l[0] < low]
            shorts = [s for s in shorts if s[0] > high]
        if close[i] > 0:
            tot = sum(l[1] for l in longs) + sum(s[1] for s in shorts)
            if tot > 0:
                f = max(0.0, 1 - close[i] / tot)
                longs = [[a, b * f] for a, b in longs]
                shorts = [[a, b * f] for a, b in shorts]
        if add[i] > 0:
            ls = long_share(doc, times[i])
            for L, w in TIERS:
                sz = add[i] * w / wsum
                longs.append([p * (1 - 1 / L + MM), sz * ls])
                shorts.append([p * (1 + 1 / L - MM), sz * (1 - ls)])
    pl, ps = [0.0] * BINS, [0.0] * BINS
    for a, b in longs:
        k = int((a - pmin) // step)
        if 0 <= k < BINS:
            pl[k] += b
    for a, b in shorts:
        k = int((a - pmin) // step)
        if 0 <= k < BINS:
            ps[k] += b
    cov = sum(real) / max(1, n - 1)
    cur = prices[-1]
    mid = lambda k: pmin + (k + .5) * step

    def side(arr, above):
        idx = [k for k in range(BINS) if arr[k] > 0 and ((mid(k) > cur) if above else (mid(k) < cur))]
        if not idx:
            return None, []
        big = max(idx, key=lambda k: arr[k])
        top = sorted(idx, key=lambda k: -arr[k])[:10]
        mx = arr[big]
        near = [k for k in idx if arr[k] >= 0.25 * mx]
        nk = min(near, key=lambda k: abs(mid(k) - cur)) if near else big
        f = lambda k: {"price": round(mid(k), 8), "usd": round(arr[k], 2), "pct": round((mid(k) / cur - 1) * 100, 3)}
        return {"largest": f(big), "nearest": f(nk)}, [f(k) for k in sorted(top, key=lambda k: mid(k))]

    s_info, s_bands = side(ps, True)
    l_info, l_bands = side(pl, False)
    return {"price": cur, "time": times[-1], "coverage": round(cov, 3), "sized": cov > 0.9,
            "shorts": s_info, "longs": l_info, "shortBands": s_bands, "longBands": l_bands,
            "shortTotalUSD": round(sum(ps), 2), "longTotalUSD": round(sum(pl), 2),
            "binUSDWidth": round(step, 10)}


def latest(arr):
    for v in reversed(arr or []):
        if v is not None:
            return v
    return None


# ----------------------------- main -----------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--backfill", action="store_true")
    a = ap.parse_args()
    os.makedirs(os.path.join(a.data, "coins"), exist_ok=True)
    status = {"startedAt": int(time.time()), "ok": [], "failed": {}, "warnings": []}
    lv_path = os.path.join(a.data, "levels.json")
    prev = {}
    if os.path.exists(lv_path):
        try:
            prev = json.load(open(lv_path))
        except Exception:
            prev = {}
    alert_state = prev.get("alertState", {})
    history = prev.get("alertHistory", [])
    levels = {"generatedAt": int(time.time()), "alertPct": ALERT_PCT, "coins": {},
              "note": "Estimated liquidation levels from Kraken + Crypto.com open interest. Market data, not trade signals."}
    new_alerts = []
    for coin, (kf, cdc, tk) in COINS.items():
        try:
            doc, tick = update_coin(coin, os.path.join(a.data, "coins", coin + ".json"), a.backfill, status)
            times, prices = kraken_prices(kf)
            lv = build_levels(doc, times, prices)
            if lv is None:
                raise RuntimeError("no price candles")
            oi_k = (latest(doc["oi"]) or 0) * lv["price"]
            oi_c = (latest((doc.get("cdc") or {}).get("oi")) or 0) * lv["price"]
            lv.update({"ticker": tk, "openInterestUSD": round(oi_k + oi_c, 2), "krakenOIUSD": round(oi_k, 2),
                       "cryptoComOIUSD": round(oi_c, 2), "funding8h": doc.get("fundingNow8h"),
                       "liquidated24hUSD": round(sum(doc["liq"][-24:]) * lv["price"], 2),
                       "dataThrough": doc["updatedAt"] + H})
            levels["coins"][coin] = lv
            status["ok"].append(coin)
            # alerts: price close to the biggest cluster on either side
            if lv["sized"] and lv["openInterestUSD"] >= ALERT_MIN_OI_USD:
                for side_key, word in (("shorts", "short"), ("longs", "long")):
                    info = lv.get(side_key)
                    if not info:
                        continue
                    c = info["largest"]
                    side_total = lv["shortTotalUSD"] if side_key == "shorts" else lv["longTotalUSD"]
                    big = c["usd"] >= max(ALERT_MIN_CLUSTER_USD, ALERT_MIN_CLUSTER_SHARE * side_total)
                    if big and abs(c["pct"]) <= ALERT_PCT:
                        key = f"{coin}:{side_key}"
                        st = alert_state.get(key)
                        fresh = (not st or time.time() - st["at"] > REALERT_HOURS * H
                                 or abs(st["price"] / c["price"] - 1) > 0.01)
                        if fresh:
                            msg = (f"{tk} ${lv['price']:,.6g} is {abs(c['pct']):.1f}% "
                                   f"{'below' if c['pct'] > 0 else 'above'} the biggest cluster of {word} "
                                   f"liquidations (~{money(c['usd'])} at ${c['price']:,.6g}).")
                            al = {"id": f"{key}:{int(time.time())}", "coin": coin, "ticker": tk, "side": side_key,
                                  "price": lv["price"], "cluster": c, "at": int(time.time()), "message": msg}
                            new_alerts.append(al)
                            alert_state[key] = {"at": al["at"], "price": c["price"]}
        except Exception as e:
            status["failed"][coin] = str(e)[:300]
            log(f"{tk}: FAILED {e}")
            if coin in prev.get("coins", {}):
                levels["coins"][coin] = dict(prev["coins"][coin], stale=True)
    history = (new_alerts + history)[:50]
    levels.update({"alerts": new_alerts, "alertHistory": history, "alertState": alert_state})
    with open(lv_path, "w") as f:
        json.dump(levels, f, indent=1)
    status["finishedAt"] = int(time.time())
    status["newAlerts"] = len(new_alerts)
    with open(os.path.join(a.data, "status.json"), "w") as f:
        json.dump(status, f, indent=1)
    # upload-ready documents for the heat map page (copied as-is by the hourly sync)
    with open(os.path.join(a.data, "_overview.json"), "w") as f:
        json.dump({k: v for k, v in levels.items() if k != "alertState"}, f, separators=(",", ":"))
    with open(os.path.join(a.data, "_status.json"), "w") as f:
        json.dump(dict(status, lastGeneratedAt=levels["generatedAt"]), f, separators=(",", ":"))
    with open(os.path.join(a.data, "alerts.txt"), "w") as f:
        f.write("\n".join(al["message"] for al in new_alerts))
    for al in new_alerts:
        log("ALERT " + al["message"])
    log(f"done: {len(status['ok'])} ok, {len(status['failed'])} failed")
    return 0 if status["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
