"""
Read the liquidation levels published hourly on the public `liq-data` branch of
af34-design/kraken-scanner. Standard library only; works anywhere with internet
(your laptop, GitHub Actions, a server).

    from liqmap.levels_reader import load_levels, nearest_clusters
    lv = load_levels()                       # whole file (all coins)
    print(nearest_clusters("XBTUSD", lv))    # biggest clusters above/below + distance in %

These are estimates built from public exchange data. Use them as one input among
others, not as a trading signal.
"""
import json, time, urllib.request

LEVELS_URL = "https://raw.githubusercontent.com/af34-design/kraken-scanner/liq-data/levels.json"
COIN_IDS = {"BTC": "bitcoin", "XBT": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "ripple",
            "BNB": "binancecoin", "DOGE": "dogecoin", "XDG": "dogecoin", "CRO": "crypto-com-chain"}


def load_levels(path_or_url=None):
    src = path_or_url or LEVELS_URL
    if src.startswith("http"):
        req = urllib.request.Request(src + ("&" if "?" in src else "?") + f"t={int(time.time())}",
                                     headers={"User-Agent": "liq-levels-reader/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    with open(src) as f:
        return json.load(f)


def coin_id(name):
    """'XBTUSD', 'BTC', 'bitcoin' -> 'bitcoin'."""
    n = name.upper().replace("PF_", "")
    if n.endswith("USD"):
        n = n[:-3]
    return COIN_IDS.get(n, name.lower())


def nearest_clusters(coin, levels=None):
    """{'price', 'above', 'below', 'sized', 'stale', 'age_min'} or None if the coin isn't covered.
    above = biggest cluster of short liquidations above price; below = longs below price.
    Each is {'price', 'usd', 'pct'} (pct = distance from current price)."""
    levels = levels or load_levels()
    c = levels.get("coins", {}).get(coin_id(coin))
    if not c:
        return None
    return {"price": c["price"],
            "above": (c.get("shorts") or {}).get("largest"),
            "below": (c.get("longs") or {}).get("largest"),
            "sized": c.get("sized"), "stale": c.get("stale", False),
            "age_min": round((time.time() - levels.get("generatedAt", 0)) / 60)}


if __name__ == "__main__":
    import sys
    print(json.dumps(nearest_clusters(sys.argv[1] if len(sys.argv) > 1 else "bitcoin"), indent=1))
