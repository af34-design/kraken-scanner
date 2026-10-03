#!/usr/bin/env python3
"""
Walk-forward accuracy test for the liquidation heat map, plus a test of using
its levels as a filter in kraken-bot's EMA strategy.

  python liqmap/backtest.py --data data --out results

Every 4 hours of history it rebuilds the heat map exactly like the live
collector does (last 168 hourly prices, open interest/funding *up to that hour
only*), then looks at the next 24 hours:

  magnet    - is the biggest cluster reached more often than a level at the
              same distance would be by chance? (observed / expected hits)
  reaction  - after price touches a cluster, does it reverse more, or run
              through more, than after touching a random level?

Controls are random levels on the same side at a random distance in the same
0.3-6% range, drawn at the same moments. Confidence intervals come from a
day-block bootstrap, since 24h windows that start 4h apart overlap.

Standard-library only. Market data research, not trading advice.
"""
import argparse, json, math, os, random, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect as C  # noqa: E402

H = 3600
WINDOW = 168           # hours of price the live model uses
STEP = 4               # evaluate every 4 hours
AHEAD = 24             # look this many hours forward
REACT = 6              # hours after a touch used to judge the reaction
DMIN, DMAX = 0.3, 6.0  # cluster distance range considered, %
N_CONTROLS = 5
MAIN = ["bitcoin", "ethereum", "solana", "ripple", "dogecoin"]


# ------------------------------- data -------------------------------
def fetch_candles(sym, start, end):
    out = {}
    t = start
    while t < end:
        t2 = min(end, t + 30 * 24 * H)
        url = f"{C.KF}/api/charts/v1/trade/{sym}/1h?from={t}&to={t2}"
        for c in C.get(url).get("candles") or []:
            out[int(c["time"]) // 1000] = (float(c["high"]), float(c["low"]), float(c["close"]))
        t = t2
        time.sleep(0.3)
    ts = sorted(k for k in out if k % H == 0)
    return ts, [out[k][0] for k in ts], [out[k][1] for k in ts], [out[k][2] for k in ts]


def cut(sr, t, keys):
    """Copy of a series truncated so nothing after time t is visible."""
    if not sr or not isinstance(sr.get(keys[0]), list):
        return None
    n = int((t - sr["start"]) // sr["interval"]) + 1
    if n <= 0:
        return None
    out = dict(sr)
    for k in keys:
        out[k] = sr[k][:n]
    return out


def doc_at(doc, t):
    d = dict(doc)
    hourly = cut(doc, t, ["oi", "liq"])
    if hourly is None:
        d["oi"], d["liq"] = [], []
    else:
        d["oi"], d["liq"] = hourly["oi"], hourly["liq"]
    d["coarse"] = cut(doc.get("coarse"), t, ["oi", "liq"]) if doc.get("coarse") else None
    d["funding"] = cut(doc.get("funding"), t, ["rate"])
    d["cdc"] = cut(doc.get("cdc"), t, ["oi"])
    return d


def levels_at(doc, ts, closes, i):
    w = slice(i - WINDOW + 1, i + 1)
    return C.build_levels(doc_at(doc, ts[i]), ts[w], closes[w])


def pick(lv, side):
    """Biggest band on a side within DMIN..DMAX %, with its share of that side."""
    bands = lv["shortBands" if side == "up" else "longBands"]
    total = lv["shortTotalUSD" if side == "up" else "longTotalUSD"] or 0
    ok = [b for b in bands if DMIN <= abs(b["pct"]) <= DMAX]
    if not ok or total <= 0:
        return None
    b = max(ok, key=lambda x: x["usd"])
    return {"d": abs(b["pct"]), "usd": b["usd"], "share": b["usd"] / total}


# ----------------------------- outcomes -----------------------------
def outcome(side, d, i, highs, lows, closes):
    cur = closes[i]
    lvl = cur * (1 + d / 100) if side == "up" else cur * (1 - d / 100)
    for k in range(i + 1, i + AHEAD + 1):
        if (highs[k] >= lvl) if side == "up" else (lows[k] <= lvl):
            e = min(k + REACT, len(closes) - 1)
            after = closes[e]
            rev = ((lvl - after) / lvl if side == "up" else (after - lvl) / lvl) * 100
            beyond = (max(highs[k:e + 1]) / lvl - 1 if side == "up"
                      else 1 - min(lows[k:e + 1]) / lvl) * 100
            return {"hit": 1, "rev": rev, "through": beyond}
    return {"hit": 0}


def excursion(side, i, highs, lows, closes):
    cur = closes[i]
    seg = range(i + 1, i + AHEAD + 1)
    return ((max(highs[k] for k in seg) / cur - 1) if side == "up"
            else (1 - min(lows[k] for k in seg) / cur)) * 100


# ----------------------------- stats --------------------------------
def boot(events, stat, n=1000, seed=7):
    """Day-block bootstrap 90% interval for stat(events)."""
    days = {}
    for e in events:
        days.setdefault(e["day"], []).append(e)
    keys = list(days)
    if len(keys) < 5:
        return None
    rng = random.Random(seed)
    vals = []
    for _ in range(n):
        smp = [e for _k in keys for e in days[rng.choice(keys)]]
        v = stat(smp)
        if v is not None:
            vals.append(v)
    vals.sort()
    return [round(vals[int(0.05 * len(vals))], 3), round(vals[int(0.95 * len(vals))], 3)]


def magnet_ratio(evs):
    exp = sum(e["exp"] for e in evs)
    return sum(e["hit"] for e in evs) / exp if exp else None


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


def frac(xs, f):
    xs = list(xs)
    return sum(1 for x in xs if f(x)) / len(xs) if xs else None


def reaction(evs):
    t = [e for e in evs if e["hit"]]
    return {
        "touched": len(t),
        "meanReversalPct": mean(e["rev"] for e in t),
        "reversed>=0.5%": frac((e["rev"] for e in t), lambda r: r >= 0.5),
        "ranThrough>=1%": frac((e["through"] for e in t), lambda r: r >= 1.0),
    }


def summarize(cl, ct):
    def r3(x):
        return None if x is None else round(x, 3)
    out = {"events": len(cl),
           "magnet": {"observedHits": sum(e["hit"] for e in cl),
                      "expectedHits": round(sum(e["exp"] for e in cl), 1),
                      "ratio": r3(magnet_ratio(cl)), "ci90": boot(cl, magnet_ratio),
                      "controlRatio": r3(magnet_ratio(ct)), "controlCi90": boot(ct, magnet_ratio)}}
    rc, rt = reaction(cl), reaction(ct)
    out["reaction"] = {"clusters": {k: r3(v) if k != "touched" else v for k, v in rc.items()},
                       "controls": {k: r3(v) if k != "touched" else v for k, v in rt.items()}}

    def diff(evs):
        a = [e for e in evs if e["hit"] and e["kind"] == "c"]
        b = [e for e in evs if e["hit"] and e["kind"] == "r"]
        if not a or not b:
            return None
        return mean(e["rev"] for e in a) - mean(e["rev"] for e in b)
    out["reaction"]["reversalEdgePct"] = r3(diff(cl + ct))
    out["reaction"]["reversalEdgeCi90"] = boot(cl + ct, diff)
    return out


# --------------------------- bot strategy ---------------------------
def ema(values, n):
    k, out, e = 2 / (n + 1), [], None
    for v in values:
        e = v if e is None else v * k + e * (1 - k)
        out.append(e)
    return out


def bot_sim(closes, start, levels_fn, variant, fast=20, slow=50, stop_pct=5.0, fee=0.004):
    """kraken-bot's simulate(): EMA 20/50 crossover, 5% stop on closes, 0.4% fee.
    variant: base | avoid_long_cluster | need_short_magnet | cluster_stop"""
    f, s = ema(closes, fast), ema(closes, slow)
    usd, qty, entry, stop = 1000.0, 0.0, 0.0, 0.0
    trades = wins = skipped = 0
    peak, mdd = 1000.0, 0.0
    for i in range(max(start, slow + 2), len(closes)):
        price, a, b = closes[i], i, i - 1
        sig = ("buy" if f[b] <= s[b] and f[a] > s[a] else
               "sell" if f[b] >= s[b] and f[a] < s[a] else None)
        if qty and price <= stop:
            sig = "sell"
        if sig == "buy" and not qty:
            this_stop = price * (1 - stop_pct / 100)
            if variant != "base":
                lv = levels_fn(i)
                if lv:
                    below = [x for x in lv["longBands"] if x["pct"] < 0]
                    above = [x for x in lv["shortBands"] if x["pct"] > 0]
                    tot_l, tot_s = lv["longTotalUSD"] or 1, lv["shortTotalUSD"] or 1
                    if variant == "avoid_long_cluster":
                        if any(-1.5 <= x["pct"] and x["usd"] / tot_l >= 0.08 for x in below):
                            skipped += 1
                            continue
                    elif variant == "need_short_magnet":
                        if not any(x["pct"] <= 3 and x["usd"] / tot_s >= 0.08 for x in above):
                            skipped += 1
                            continue
                    elif variant == "cluster_stop":
                        big = [x for x in below if -8 <= x["pct"] <= -2]
                        if big:
                            lvl = max(big, key=lambda x: x["usd"])["price"]
                            this_stop = lvl * 0.998
            qty, entry, stop, usd = usd * (1 - fee) / price, price, this_stop, 0.0
        elif sig == "sell" and qty:
            usd, trades = qty * price * (1 - fee), trades + 1
            wins += price > entry
            qty = 0.0
        eq = usd + qty * price
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak * 100)
    final = usd + qty * closes[-1]
    return {"returnPct": round((final / 1000 - 1) * 100, 2), "trades": trades, "wins": wins,
            "maxDrawdownPct": round(mdd, 2), "skippedBuys": skipped}


# ------------------------------- main -------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--out", default="results")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    rng = random.Random(42)
    report = {"generatedAt": int(time.time()), "settings": {
        "window": WINDOW, "step": STEP, "ahead": AHEAD, "react": REACT,
        "distanceRangePct": [DMIN, DMAX], "controlsPerEvent": N_CONTROLS}, "coins": {}, "bot": {}}
    pooled = {"main": ([], []), "main_strong": ([], [])}

    for coin, (kf, _cdc, tick) in C.COINS.items():
        path = os.path.join(args.data, "coins", f"{coin}.json")
        if not os.path.exists(path):
            continue
        doc = json.load(open(path))
        first = doc["coarse"]["start"] if doc.get("coarse") else doc["start"]
        now = int(time.time()) // H * H
        C.log(f"{tick}: fetching prices")
        ts, hi, lo, cl = fetch_candles(kf, first, now)
        if len(ts) < WINDOW + AHEAD + 10:
            C.log(f"{tick}: not enough candles ({len(ts)})")
            continue
        cache = {}

        def lv_fn(i):
            if i not in cache:
                cache[i] = levels_at(doc, ts, cl, i) if i >= WINDOW - 1 else None
            return cache[i]

        raw = {"up": [], "down": []}
        exc = {"up": [], "down": []}
        for i in range(WINDOW - 1, len(ts) - AHEAD - REACT, STEP):
            lv = lv_fn(i)
            if not lv:
                continue
            day = ts[i] // 86400
            for side in ("up", "down"):
                exc[side].append(excursion(side, i, hi, lo, cl))
                p = pick(lv, side)
                if p:
                    o = outcome(side, p["d"], i, hi, lo, cl)
                    raw[side].append(dict(o, kind="c", d=p["d"], share=p["share"], usd=p["usd"],
                                          day=day, coin=coin))
                for _ in range(N_CONTROLS):
                    d = rng.uniform(DMIN, DMAX)
                    o = outcome(side, d, i, hi, lo, cl)
                    raw[side].append(dict(o, kind="r", d=d, day=day, coin=coin))

        # expected hit chance at each distance from this coin's own excursions
        for side in ("up", "down"):
            xs = sorted(exc[side])
            for e in raw[side]:
                lo_i, hi_i = 0, len(xs)
                while lo_i < hi_i:
                    m = (lo_i + hi_i) // 2
                    if xs[m] < e["d"]:
                        lo_i = m + 1
                    else:
                        hi_i = m
                e["exp"] = (len(xs) - lo_i) / len(xs)

        evs = raw["up"] + raw["down"]
        cl_e = [e for e in evs if e["kind"] == "c"]
        ct_e = [e for e in evs if e["kind"] == "r"]
        report["coins"][tick] = {"hours": len(ts), "from": ts[0], "to": ts[-1],
                                 **summarize(cl_e, ct_e)}
        if coin in MAIN:
            pooled["main"][0].extend(cl_e)
            pooled["main"][1].extend(ct_e)
            strong = [e for e in cl_e if e["share"] >= C.ALERT_MIN_CLUSTER_SHARE
                      and e["usd"] >= C.ALERT_MIN_CLUSTER_USD]
            pooled["main_strong"][0].extend(strong)
            pooled["main_strong"][1].extend(ct_e)

        report["bot"][tick] = {v: bot_sim(cl, WINDOW, lv_fn, v) for v in
                               ("base", "avoid_long_cluster", "need_short_magnet", "cluster_stop")}
        report["bot"][tick]["holdPct"] = round((cl[-1] / cl[WINDOW] - 1) * 100, 2)
        C.log(f"{tick}: {len(cl_e)} cluster events, bot done")

    report["pooled"] = {k: summarize(a, b) for k, (a, b) in pooled.items() if a}
    json.dump(report, open(os.path.join(args.out, "backtest.json"), "w"), indent=1)
    C.log("wrote " + os.path.join(args.out, "backtest.json"))


if __name__ == "__main__":
    main()
