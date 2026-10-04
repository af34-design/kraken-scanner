# Liquidation heat map — handoff

Everything needed to understand, run and change the heat map. Last updated 2026-10-04.

**The page:** <https://claude.ai/artifact/CWhVwL6DbiHhoRbDqo7cJL> (private Claude artifact).
**Rule:** only coins listed on Kraken (the exchange the owner trades on).
Coins: BTC, ETH, SOL, XRP, BNB, DOGE, CRO, PUMP.

## How it fits together

```
 GitHub Actions (this repo, public)              Claude
 ─────────────────────────────────               ─────────────────────────────────
 liq-heatmap.yml  runs at :09 every hour   ──►   routine "Sync liquidation heat map"
   liqmap/collect.py + extra.py                    (:21 every hour, see SYNC_ROUTINE.md)
   writes branch  liq-data                         clones liq-data, copies JSON into
     coins/<coin>.json  levels.json                the artifact database (collection
     _overview.json  _status.json                  "kraken") ──► the heat map page
     status.json  alerts.txt                       reads it; live prices come from the
                                                   viewer's CoinGecko + Crypto.com
 kraken-bot reads levels.json (shadow filter)      connectors every 30-60 s
```

## Pieces

| Path | What |
|---|---|
| `liqmap/collect.py` | hourly collector: Kraken Futures OI/liquidations/funding, Crypto.com OI, model, alerts, upload-ready docs |
| `liqmap/extra.py` | Hyperliquid OI/funding + real position liquidation prices (~450 accounts), Kraken spot order-book walls |
| `liqmap/backtest.py` | walk-forward accuracy test + kraken-bot filter test (workflow `liq-backtest.yml`, results on branch `liq-backtest`) |
| `liqmap/levels_reader.py` | small reader for bots: `nearest_clusters("XBTUSD")` |
| `liqmap/page/liq-heatmap.html` | source of the artifact page (copy of what is published) |
| `.github/workflows/liq-heatmap.yml` | collector; each run's `next` job starts the following run at :09 (GitHub skips hourly cron), cron kept as backup |
| `liqmap/SYNC_ROUTINE.md` | the Claude routine's prompt, required repo setting, lessons learned |

### Artifact database (collection `kraken`)
`<coin>` per coin (OI history, liquidations, funding, `cdc` and `hl` hourly OI
snapshots), `_overview` (levels per coin incl. `hyperliquid` positions and `book`
walls, alerts), `_status` (run status, `syncError` if a sync failed).
Access rule: `kraken` read=view, write=admin.

### Republishing the page
Publish `liqmap/page/liq-heatmap.html` to the URL above with capabilities:
`mcp` → CoinGecko `get-coin-market-chart`, Crypto.com `get_ticker`; `db` with rule
`{path:"kraken", read:"view", write:"admin"}`; `downloads: true`.
(Omitting capabilities on a republish keeps the stored ones.)

## The model (short)
Each hour, a rise in open interest × price becomes new positions, split long/short by
funding (`0.5 ± 0.2`), spread over leverage tiers 10/25/50/100× (weights .30/.30/.25/.15,
0.5% maintenance margin). A fall in OI closes that share of all positions, measured
against **total** OI. A level is cleared once price trades through it. Crypto.com and
Hyperliquid OI changes count only once their history covers the whole window (otherwise
Hyperliquid, 20–200× Kraken's size, swamps it). Dollar sizes show when coverage > 90%.
Alerts: price within 1.5% of a cluster holding ≥8% of its side and ≥$50K, OI ≥ $5M.

Accuracy (backtest, Oct 3, before the Oct 4 model fixes): ordinary clusters ≈ chance;
strong clusters show a small extra bounce (~0.2% over 6h), smaller than trading fees.
See README "How accurate is it?".

## Open items
1. **Routine repo setting:** add `af34-design/kraken-scanner` to the sync routine's
   repositories at claude.ai/code/routines, or the page stops updating (see SYNC_ROUTINE.md).
2. **Re-run the backtest** (Actions → "Liquidation heat map backtest") — the Oct 4 fixes
   changed the model.
3. Hyperliquid OI starts counting in the 7-day model about 2026-10-10 (a week of history).
4. CRO is not on Hyperliquid (no real positions for it).
5. kraken-bot's own hourly cron is throttled by GitHub to every 4–8 h; its heat-map filter
   runs in shadow mode (`BOT_LIQ_FILTER`), logging to `liq_filter.csv`.

Market data, not trading advice.
