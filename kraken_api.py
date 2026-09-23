"""Kraken public REST helpers for the scanner."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API = "https://api.kraken.com/0/public"
USER_AGENT = "kraken-scan/live-layout"


def http_get(path: str, params: dict[str, str] | None = None, timeout: float = 20.0, retries: int = 3) -> dict[str, Any]:
    url = f"{API}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.load(resp)
            errors = payload.get("error") or []
            if errors:
                joined = "; ".join(errors)
                if "RateLimit" in joined and attempt < retries - 1:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise RuntimeError(joined)
            return payload["result"]
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_exc = exc
            time.sleep(0.6 * (attempt + 1))
    raise last_exc or RuntimeError("request failed")


def fetch_pair_names() -> dict[str, str]:
    names: dict[str, str] = {}
    try:
        result = http_get("AssetPairs", {"assetVersion": "1"})
    except Exception:
        result = http_get("AssetPairs")
    for key, meta in result.items():
        if not isinstance(meta, dict):
            continue
        wsname = meta.get("wsname") or meta.get("altname") or key
        for alias in {key, meta.get("altname"), meta.get("wsname"), wsname}:
            if alias:
                names[str(alias)] = str(wsname)
    return names


def is_usd_spot(key: str, display: str) -> bool:
    if key.endswith("USDT") or display.endswith("/USDT"):
        return False
    return key.endswith("USD") or display.endswith("/USD")


def fallback_name(key: str) -> str:
    name = key[:-4] if key.endswith("ZUSD") else key[:-3] if key.endswith("USD") else key
    for old, new in (("XXBT", "BTC"), ("XBT", "BTC"), ("XETH", "ETH"), ("XXRP", "XRP"), ("XLTC", "LTC"), ("XXDG", "DOGE")):
        if name == old or name.startswith(old):
            name = new + name[len(old) :]
            break
    return f"{name}/USD"


def parse_ticker_row(key: str, raw: dict[str, Any], names: dict[str, str]) -> dict[str, Any] | None:
    try:
        ask = float(raw["a"][0])
        bid = float(raw["b"][0])
        last = float(raw["c"][0])
        open_ = float(raw["o"])
        high = float(raw["h"][1])
        low = float(raw["l"][1])
        vol = float(raw["v"][1])
        trades = int(raw["t"][1])
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    change = 0.0 if open_ == 0 else (last - open_) / open_ * 100.0
    mid = (ask + bid) / 2.0 if ask and bid else last
    spread_bps = ((ask - bid) / mid * 10_000.0) if mid else 0.0
    display = names.get(key) or names.get(key.replace("XBT", "BTC")) or fallback_name(key)
    return {
        "key": key,
        "pair": display,
        "last": last,
        "bid": bid,
        "ask": ask,
        "open": open_,
        "high": high,
        "low": low,
        "change": change,
        "vol": vol,
        "notional": vol * last,
        "trades": trades,
        "spread_bps": spread_bps,
    }


def fetch_usd_tickers(names: dict[str, str]) -> list[dict[str, Any]]:
    result = http_get("Ticker")
    rows: list[dict[str, Any]] = []
    for key, raw in result.items():
        display = names.get(key, "")
        if not is_usd_spot(key, display):
            continue
        row = parse_ticker_row(key, raw, names)
        if row:
            rows.append(row)
    rows.sort(key=lambda r: r["notional"], reverse=True)
    return rows


def resolve_focus(token: str | None, rows: list[dict[str, Any]]) -> str:
    if not rows:
        return token or ""
    if not token:
        return rows[0]["key"]
    needle = token.upper().replace("-", "/")
    for row in rows:
        aliases = {row["key"].upper(), row["pair"].upper(), row["pair"].upper().replace("/", "")}
        if needle in aliases:
            return row["key"]
    return rows[0]["key"]


def fetch_ohlc_closes(pair_key: str, interval: int = 1440) -> list[float]:
    result = http_get("OHLC", {"pair": pair_key, "interval": str(interval)})
    series_key = next(k for k in result if k != "last")
    return [float(candle[4]) for candle in result[series_key]]
