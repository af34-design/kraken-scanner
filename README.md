# kraken-scanner

Terminal scanner for Kraken USD spot markets. Rich **Live + Layout**: current ticker table + daily OHLC sparkline.

Public REST only. No API key.

## Run

```bash
pip install rich
python3 kraken_scan.py
```

Pin a pair:

```bash
FOCUS_PAIR=ETH/USD python3 kraken_scan.py
```

## Keys

| Key | Action |
|---|---|
| `j` / `n` / down | Next pair |
| `k` / `p` / up | Previous pair |
| `1`–`9` | Jump to that row |
| `r` | Reload OHLC |
| `q` / Ctrl+C | Quit |

## Data

- `/public/AssetPairs` display names
- `/public/Ticker` last, today % (vs 00:00 UTC open), spread bps, trades, session volume $
- `/public/OHLC` up to 720 daily closes for the focused pair

## Layout

Header / USD table / history pane / status. Paint 2 fps, ticker ~2s, OHLC ~30s or on focus change.
