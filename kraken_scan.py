#!/usr/bin/env python3
"""Kraken scanner: Rich Live + Layout.\n\nPublic endpoints (no API key): AssetPairs, Ticker, OHLC.\n\nKeys: j/k focus · [ ] page · r reload OHLC · q or Ctrl+C quit\nFOCUS_PAIR env pins a start pair (Kraken key or wsname).\n"""

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
