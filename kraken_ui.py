"""Rich Live + Layout widgets for the Kraken scanner."""

from __future__ import annotations

import select
import sys
from contextlib import contextmanager
from typing import Any, Iterator

from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

TABLE_ROWS = 22


def sparkline(values: list[float], width: int = 48) -> str:
    blocks = "▁▂▃▄▅▆▇█"
    if not values:
        return "no history"
    if len(values) > width:
        step = len(values) / width
        bucketed = []
        for i in range(width):
            sl = values[int(i * step) : int((i + 1) * step)]
            bucketed.append(sl[-1] if sl else values[-1])
        values = bucketed
    lo, hi = min(values), max(values)
    span = hi - lo or 1.0
    return "".join(blocks[min(7, int((v - lo) / span * 7))] for v in values)


def fmt_price(value: float) -> str:
    if value >= 1000:
        return f"{value:,.2f}"
    if value >= 1:
        return f"{value:,.4f}"
    return f"{value:.8f}".rstrip("0").rstrip(".")


def fmt_notional(value: float) -> str:
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return f"{value:.0f}"


def fmt_trades(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def make_layout() -> Layout:
    layout = Layout(name="root")
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body", ratio=1, minimum_size=10),
        Layout(name="footer", size=3),
    )
    layout["body"].split_row(
        Layout(name="list", ratio=3, minimum_size=40),
        Layout(name="chart", ratio=2, minimum_size=24),
    )
    layout["header"].update(Panel("[bold magenta]KRAKEN SCAN[/]  starting…", border_style="magenta"))
    layout["list"].update(Panel("loading tickers", title="USD pairs", border_style="cyan"))
    layout["chart"].update(Panel("loading OHLC", title="history", border_style="green"))
    layout["footer"].update(Panel("Live + Layout  ·  poll pending", border_style="bright_black"))
    return layout


def render_table(rows: list[dict[str, Any]], focus_key: str) -> Table:
    table = Table(expand=True, box=None, header_style="bold cyan", show_edge=False, pad_edge=False)
    table.add_column("#", justify="right", style="dim", width=3)
    table.add_column("Pair", style="bold")
    table.add_column("Last", justify="right")
    table.add_column("Today", justify="right")
    table.add_column("Spr bps", justify="right", style="dim")
    table.add_column("Trades", justify="right", style="dim")
    table.add_column("Vol $", justify="right", style="dim")
    for i, row in enumerate(rows[:TABLE_ROWS], start=1):
        chg = row["change"]
        chg_text = Text(f"{chg:+.2f}%", style="green" if chg >= 0 else "red")
        pair = Text(row["pair"])
        if row["key"] == focus_key:
            pair.stylize("bold yellow")
        table.add_row(
            str(i),
            pair,
            fmt_price(row["last"]),
            chg_text,
            f"{row['spread_bps']:.1f}",
            fmt_trades(row["trades"]),
            fmt_notional(row["notional"]),
        )
    return table


def render_chart(focus: dict[str, Any], closes: list[float], error: str | None) -> Panel:
    if error and not closes:
        body = Text(error, style="red")
    else:
        spark = sparkline(closes, width=42)
        first, last = (closes[0], closes[-1]) if closes else (0.0, 0.0)
        hist = 0.0 if first == 0 else (last - first) / first * 100.0
        style = "green" if hist >= 0 else "red"
        extra = f"\n[red]{error}[/]" if error else ""
        body = Text.from_markup(
            f"[bold]{focus['pair']}[/]\n"
            f"Last  {fmt_price(focus['last'])}   "
            f"today [{style}]{focus['change']:+.2f}%[/]\n"
            f"Bid {fmt_price(focus.get('bid', 0))}  Ask {fmt_price(focus.get('ask', 0))}  "
            f"spr {focus.get('spread_bps', 0):.1f} bps\n"
            f"H {fmt_price(focus['high'])}  L {fmt_price(focus['low'])}  "
            f"trades {fmt_trades(int(focus.get('trades', 0)))}\n\n"
            f"[cyan]{spark}[/]\n"
            f"OHLC daily  n={len(closes)}  window [{style}]{hist:+.1f}%[/]\n"
            "[dim]Kraken max 720 candles · today % = vs midnight UTC open[/]"
            f"{extra}"
        )
    return Panel(body, title="past + current", border_style="green")


@contextmanager
def raw_stdin() -> Iterator[None]:
    if not sys.stdin.isatty():
        yield
        return
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def read_keys() -> list[str]:
    keys: list[str] = []
    if not sys.stdin.isatty():
        return keys
    while True:
        ready, _, _ = select.select([sys.stdin], [], [], 0)
        if not ready:
            break
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            extra = ""
            if select.select([sys.stdin], [], [], 0.02)[0]:
                extra += sys.stdin.read(1)
            if select.select([sys.stdin], [], [], 0.02)[0]:
                extra += sys.stdin.read(1)
            if extra == "[A":
                keys.append("up")
            elif extra == "[B":
                keys.append("down")
            else:
                keys.append("esc")
        else:
            keys.append(ch)
    return keys


def apply_keys(keys: list[str], rows: list[dict[str, Any]], focus_key: str) -> tuple[str, bool, bool]:
    quit = False
    force_ohlc = False
    visible = rows[:TABLE_ROWS]
    keys_list = [r["key"] for r in visible]
    idx = keys_list.index(focus_key) if focus_key in keys_list else 0
    for key in keys:
        if key in {"q", "Q", "\x03"}:
            quit = True
        elif key in {"k", "p", "up"}:
            idx = max(0, idx - 1)
            force_ohlc = True
        elif key in {"j", "n", "down"}:
            idx = min(len(keys_list) - 1, idx + 1)
            force_ohlc = True
        elif key == "r":
            force_ohlc = True
        elif key.isdigit() and key != "0":
            pick = int(key) - 1
            if pick < len(keys_list):
                idx = pick
                force_ohlc = True
    if keys_list:
        focus_key = keys_list[idx]
    return focus_key, quit, force_ohlc
