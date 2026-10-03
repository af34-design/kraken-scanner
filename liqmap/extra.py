"""
Extra sources for the liquidation heat map (all public, no keys):

  Hyperliquid  - open interest + funding per coin, and the REAL liquidation prices
                 of open positions held by its largest accounts (positions on
                 Hyperliquid are public). These are actual levels, not estimates.
  Kraken spot  - order book walls: where large resting buy/sell orders sit on the
                 exchange the user trades on.

Every function here is best-effort: failures are reported as warnings by the
caller and never stop the Kraken collection.
"""
import json, statistics, time, urllib.request
from concurrent.futures import ThreadPoolExecutor

HL_INFO = "https://api.hyperliquid.xyz/info"
HL_LEADERBOARD = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
KRAKEN_SPOT = "https://api.kraken.com/0/public"

HL_TOP_ACCOUNTS = 400          # largest accounts by account value
HL_MAX_ACCOUNTS = 700          # plus recent traders, capped
HL_KEEP_POSITIONS = 80         # per coin, largest by value
SPOT_PAIRS = {"BTC": "XBTUSD", "ETH": "ETHUSD", "SOL": "SOLUSD", "XRP": "XRPUSD", "BNB": "BNBUSD",
              "DOGE": "XDGUSD", "CRO": "CROUSD", "PUMP": "PUMPUSD"}
BOOK_RANGE = 0.10              # look at orders within ±10% of mid
BOOK_BIN = 0.0025              # 0.25% price buckets


def _req(url, body=None, timeout=25, tries=3):
    last = None
    for k in range(tries):
        try:
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(url, data=data, headers={"User-Agent": "liq-heatmap/1.0",
                                                                 "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            last = e
            time.sleep(1.5 * (k + 1))
    raise RuntimeError(f"{url.split('?')[0]}: {last}")


def hl(body):
    return _req(HL_INFO, body)


# ----------------------------- Hyperliquid -----------------------------
def hl_market(tickers):
    """{ticker: {"oi": coins, "mark": px, "funding8h": rate}} for listed tickers."""
    meta, ctxs = hl({"type": "metaAndAssetCtxs"})
    out = {}
    for u, c in zip(meta.get("universe", []), ctxs):
        name = u.get("name")
        if name in tickers and not u.get("isDelisted"):
            try:
                out[name] = {"oi": float(c["openInterest"]), "mark": float(c["markPx"]),
                             "funding8h": float(c["funding"]) * 8}   # Hyperliquid funds hourly
            except (KeyError, TypeError, ValueError):
                pass
    return out


def hl_accounts(tickers, warn):
    """Addresses to scan: the largest accounts plus recent traders in our coins."""
    addrs = []
    try:
        rows = _req(HL_LEADERBOARD, timeout=60).get("leaderboardRows") or []
        rows.sort(key=lambda r: -float(r.get("accountValue") or 0))
        addrs += [r["ethAddress"].lower() for r in rows[:HL_TOP_ACCOUNTS] if r.get("ethAddress")]
    except Exception as e:
        warn(f"Hyperliquid leaderboard: {e}")
    for t in tickers:
        try:
            for tr in hl({"type": "recentTrades", "coin": t}) or []:
                addrs += [u.lower() for u in tr.get("users") or [] if u]
        except Exception as e:
            warn(f"Hyperliquid trades {t}: {e}")
    seen, out = set(), []
    for a in addrs:
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out[:HL_MAX_ACCOUNTS]


def hl_positions(tickers, warn):
    """{ticker: {"positions": [[liqPx, usd, side(+1 long/-1 short), leverage]], "accounts": n,
                 "longUSD": x, "shortUSD": y}} from the accounts' open positions."""
    accts = hl_accounts(tickers, warn)
    per = {t: [] for t in tickers}
    errors = 0

    def one(a):
        try:
            return hl({"type": "clearinghouseState", "user": a})
        except Exception:
            return None

    # Hyperliquid allows ~1200 request-weight per minute per IP; this call weighs 2
    with ThreadPoolExecutor(max_workers=6) as ex:
        for i, st in enumerate(ex.map(one, accts)):
            if st is None:
                errors += 1
                continue
            for ap in st.get("assetPositions") or []:
                p = ap.get("position") or {}
                coin = p.get("coin")
                if coin not in per:
                    continue
                try:
                    szi = float(p.get("szi") or 0)
                    liq = p.get("liquidationPx")
                    val = abs(float(p.get("positionValue") or 0))
                    lev = (p.get("leverage") or {}).get("value")
                except (TypeError, ValueError):
                    continue
                if szi == 0 or liq in (None, "") or val <= 0:
                    continue
                per[coin].append([float(liq), round(val, 2), 1 if szi > 0 else -1, lev])
    if accts and errors > len(accts) * 0.5:
        warn(f"Hyperliquid positions: {errors} of {len(accts)} account lookups failed")
    out = {}
    for t, ps in per.items():
        ps.sort(key=lambda x: -x[1])
        out[t] = {"positions": [[round(x[0], 10), x[1], x[2], x[3]] for x in ps[:HL_KEEP_POSITIONS]],
                  "count": len(ps), "accounts": len(accts),
                  "longUSD": round(sum(x[1] for x in ps if x[2] > 0), 2),
                  "shortUSD": round(sum(x[1] for x in ps if x[2] < 0), 2)}
    return out


# ----------------------------- Kraken order book -----------------------------
def kraken_book(ticker):
    """Order-book walls on Kraken spot within ±10%: {"mid", "bids": [[px, usd]], "asks": [...],
    "bid1pct", "ask1pct", "bid2pct", "ask2pct"} (USD resting within 1% / 2% of mid)."""
    pair = SPOT_PAIRS.get(ticker)
    if not pair:
        return None
    res = _req(f"{KRAKEN_SPOT}/Depth?pair={pair}&count=500")
    if res.get("error"):
        raise RuntimeError(", ".join(res["error"]))
    book = next(iter((res.get("result") or {}).values()))
    bids = [(float(p), float(v)) for p, v, *_ in book.get("bids") or []]
    asks = [(float(p), float(v)) for p, v, *_ in book.get("asks") or []]
    if not bids or not asks:
        return None
    mid = (bids[0][0] + asks[0][0]) / 2

    def bucket(side, sign):
        bins = {}
        for p, v in side:
            d = (p / mid - 1) * sign          # distance away from mid, positive
            if d < 0 or d > BOOK_RANGE:
                continue
            k = int(d / BOOK_BIN)
            bins[k] = bins.get(k, 0.0) + p * v
        if not bins:
            return [], 0.0, 0.0
        med = statistics.median(bins.values())
        walls = [(k, u) for k, u in bins.items() if u >= 3 * med]
        walls.sort(key=lambda x: -x[1])
        within = lambda pct: sum(u for k, u in bins.items() if (k + 1) * BOOK_BIN <= pct + 1e-9)
        return ([[round(mid * (1 + sign * (k + 0.5) * BOOK_BIN), 10), round(u, 2)] for k, u in walls[:8]],
                round(within(0.01), 2), round(within(0.02), 2))

    b, b1, b2 = bucket(bids, -1)
    a, a1, a2 = bucket(asks, 1)
    # Kraken returns at most 500 levels; the book may not reach the full ±10% on busy pairs
    reach = min(abs(bids[-1][0] / mid - 1), abs(asks[-1][0] / mid - 1))
    return {"mid": mid, "bids": b, "asks": a, "bid1pct": b1, "ask1pct": a1, "bid2pct": b2, "ask2pct": a2,
            "reachPct": round(reach * 100, 2), "at": int(time.time()), "pair": pair}
