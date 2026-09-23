#!/usr/bin/env python3
"""Kraken scanner: Rich Live + Layout.

Public endpoints (no API key):
  GET /0/public/AssetPairs  display names
  GET /0/public/Ticker      current prices
  GET /0/public/OHLC        last 720 daily candles for the focused pair

Keys: j/k or n/p move focus · r reload OHLC · q or Ctrl+C quit
FOCUS_PAIR env/constant pins a start pair (Kraken key or wsname).
"""

from __future__ import annotations

import json
import os
import select
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

API = "https://api.kraken.com/0/public"
POLL_SECONDS = 2.0
TABLE_ROWS = 22
OHLC_SECONDS = 30.0
FOCUS_PAIR: str | None = os.environ.get("FOCUS_PAIR") or None
USER_AGENT = "kraken-scan/live-layout"
