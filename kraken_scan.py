#!/usr/bin/env python3
"""Kraken scanner: Rich Live + Layout.

Public endpoints (no API key): AssetPairs, Ticker, OHLC.

Keys: j/k or n/p move focus · r reload OHLC · q or Ctrl+C quit
FOCUS_PAIR env pins a start pair (Kraken key or wsname).
"""

from __future__ import annotations

import os
import time
import urllib.error
from datetime import datetime, timezone
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from kraken_api import fetch_ohlc_closes, fetch_pair_names, fetch_usd_tickers, resolve_focus
from kraken_ui import apply_keys, make_layout, raw_stdin, read_keys, render_chart, render_table

POLL_SECONDS = 2.0
OHLC_SECONDS = 30.0
FOCUS_PAIR: str | None = os.environ.get("FOCUS_PAIR") or None


def main() -> None:
    console = Console()
    layout = make_layout()
    names = fetch_pair_names()
    focus_key = FOCUS_PAIR
    closes: list[float] = []
    last_ohlc_at = 0.0
    ohlc_for: str | None = None
    status = "boot"
    rows: list[dict[str, Any]] = []

    with raw_stdin():
        with Live(layout, console=console, screen=True, refresh_per_second=2, transient=False) as live:
            running = True
            while running:
                t0 = time.time()
                force_ohlc = False
                ohlc_error = None
                try:
                    pending = read_keys()
                    if pending and rows:
                        focus_key, quit, force_ohlc = apply_keys(pending, rows, focus_key or rows[0]["key"])
                        if quit:
                            running = False
                            continue
                    rows = fetch_usd_tickers(names)
                    if not rows:
                        raise RuntimeError("no USD pairs returned")
                    focus_key = resolve_focus(focus_key, rows)
                    focus = next(r for r in rows if r["key"] == focus_key)
                    need_ohlc = force_ohlc or ohlc_for != focus_key or (time.time() - last_ohlc_at > OHLC_SECONDS)
                    if need_ohlc:
                        closes = fetch_ohlc_closes(focus_key)
                        last_ohlc_at = time.time()
                        ohlc_for = focus_key
                    status = f"ok  {len(rows)} USD pairs  poll {time.time() - t0:.2f}s"
                except (urllib.error.URLError, TimeoutError, RuntimeError, StopIteration, OSError) as exc:
                    focus = rows[0] if rows else {
                        "key": focus_key or "?",
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
                    ohlc_error = str(exc)
                    status = f"error  {exc}"

                now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
                layout["header"].update(
                    Panel(
                        Text.from_markup(
                            f"[bold magenta]KRAKEN SCAN[/]   [white]{len(rows)} USD markets[/]   [dim]{now}[/]"
                        ),
                        border_style="magenta",
                    )
                )
                if rows:
                    layout["list"].update(
                        Panel(
                            render_table(rows, focus["key"]),
                            title="current \u00b7 USD \u00b7 vol$ since 00:00 UTC",
                            border_style="cyan",
                        )
                    )
                layout["chart"].update(render_chart(focus, closes, ohlc_error))
                layout["footer"].update(
                    Panel(
                        Text.from_markup(
                            f"[dim]j/k focus \u00b7 1-9 jump \u00b7 r OHLC \u00b7 q quit \u00b7 "
                            f"data {POLL_SECONDS:.0f}s \u00b7 focus {focus.get('pair', '\u2014')} \u00b7 {status}[/]"
                        ),
                        border_style="bright_black",
                    )
                )
                live.refresh()
                elapsed = time.time() - t0
                time.sleep(max(0.15, POLL_SECONDS - elapsed))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        Console().print("\n[dim]stopped[/]")
