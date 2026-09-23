#!/usr/bin/env python3
"""Kraken scanner: Rich Live + Layout.

Public endpoints (no API key): AssetPairs, Ticker, OHLC.

Keys: j/k focus · [ ] page · r reload OHLC · q or Ctrl+C quit
FOCUS_PAIR env pins a start pair (Kraken key or wsname).
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from kraken_api import fetch_ohlc_closes, fetch_pair_names, fetch_usd_tickers, resolve_focus
from kraken_ui import (
    apply_keys,
    clamp_offset,
    make_layout,
    page_count,
    raw_stdin,
    read_keys,
    render_chart,
    render_table,
    TABLE_ROWS,
)

POLL_SECONDS = 2.0
OHLC_SECONDS = 30.0
FOCUS_PAIR: str | None = os.environ.get("FOCUS_PAIR") or None


class MarketCache:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.names: dict[str, str] = {}
        self.rows: list[dict[str, Any]] = []
        self.closes: list[float] = []
        self.ohlc_for: str | None = None
        self.status = "boot"
        self.error: str | None = None
        self.focus_wanted: str | None = FOCUS_PAIR
        self.force_ohlc = False
        self.stop = threading.Event()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "rows": list(self.rows),
                "closes": list(self.closes),
                "ohlc_for": self.ohlc_for,
                "status": self.status,
                "error": self.error,
            }

    def request_ohlc(self, pair_key: str) -> None:
        with self.lock:
            self.focus_wanted = pair_key
            self.force_ohlc = True


def worker(cache: MarketCache) -> None:
    try:
        names = fetch_pair_names()
        with cache.lock:
            cache.names = names
            cache.status = "names loaded"
    except Exception as exc:
        with cache.lock:
            cache.error = str(exc)
            cache.status = f"error  {exc}"
        names = {}

    last_ticker = 0.0
    last_ohlc = 0.0
    while not cache.stop.is_set():
        now = time.time()
        try:
            if now - last_ticker >= POLL_SECONDS:
                rows = fetch_usd_tickers(names or cache.names)
                with cache.lock:
                    cache.rows = rows
                    cache.error = None
                    cache.status = f"ok  {len(rows)} USD pairs"
                last_ticker = time.time()
                names = names or cache.names

            with cache.lock:
                want = cache.focus_wanted
                force = cache.force_ohlc
                have = cache.ohlc_for
            if want and (force or have != want or time.time() - last_ohlc >= OHLC_SECONDS):
                closes = fetch_ohlc_closes(want)
                with cache.lock:
                    cache.closes = closes
                    cache.ohlc_for = want
                    cache.force_ohlc = False
                    cache.error = None
                last_ohlc = time.time()
        except Exception as exc:
            with cache.lock:
                cache.error = str(exc)
                cache.status = f"error  {exc}"
        cache.stop.wait(0.15)


def empty_focus(key: str | None) -> dict[str, Any]:
    return {
        "key": key or "?",
        "pair": "\u2014",
        "last": 0.0,
        "bid": 0.0,
        "ask": 0.0,
        "change": 0.0,
        "high": 0.0,
        "low": 0.0,
        "trades": 0,
        "spread_bps": 0.0,
    }


def main() -> None:
    console = Console()
    layout = make_layout()
    cache = MarketCache()
    thread = threading.Thread(target=worker, args=(cache,), name="kraken-poll", daemon=True)
    thread.start()

    focus_key = FOCUS_PAIR
    offset = 0

    with raw_stdin():
        with Live(layout, console=console, screen=True, refresh_per_second=4, transient=False) as live:
            running = True
            while running:
                snap = cache.snapshot()
                rows: list[dict[str, Any]] = snap["rows"]
                pending = read_keys()
                if pending:
                    focus_key, quit, force_ohlc, offset = apply_keys(
                        pending, rows, focus_key or (rows[0]["key"] if rows else ""), offset
                    )
                    if quit:
                        running = False
                        continue
                    if force_ohlc and focus_key:
                        cache.request_ohlc(focus_key)

                if rows:
                    focus_key = resolve_focus(focus_key, rows)
                    offset = clamp_offset(offset, len(rows))
                    focus = next((r for r in rows if r["key"] == focus_key), rows[0])
                    if snap["ohlc_for"] != focus_key:
                        cache.request_ohlc(focus_key)
                else:
                    focus = empty_focus(focus_key)

                pages = page_count(len(rows))
                page = (offset // TABLE_ROWS) + 1 if rows else 1
                now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
                layout["header"].update(
                    Panel(
                        Text.from_markup(
                            f"[bold magenta]KRAKEN SCAN[/]   [white]{len(rows)} USD[/]  "
                            f"page {page}/{pages}   [dim]{now}[/]"
                        ),
                        border_style="magenta",
                    )
                )
                if rows:
                    layout["list"].update(
                        Panel(
                            render_table(rows, focus["key"], offset),
                            title="current · USD · vol$ since 00:00 UTC",
                            border_style="cyan",
                        )
                    )
                layout["chart"].update(render_chart(focus, snap["closes"], snap["error"]))
                layout["footer"].update(
                    Panel(
                        Text.from_markup(
                            f"[dim]j/k focus · [ ] page · r OHLC · q quit · "
                            f"bg poll {POLL_SECONDS:.0f}s · {focus.get('pair', '\u2014')} · {snap['status']}[/]"
                        ),
                        border_style="bright_black",
                    )
                )
                live.refresh()
                time.sleep(0.12)

    cache.stop.set()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        Console().print("\n[dim]stopped[/]")
