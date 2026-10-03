# Liquidation heat map data

`collect.py` runs hourly in GitHub Actions (`.github/workflows/liq-heatmap.yml`). It pulls exact
public data from Kraken Futures (open interest, liquidations, funding, prices) and Crypto.com
(open interest), estimates where leveraged positions would be liquidated, and saves everything
to the `liq-data` branch. The `main` branch is never modified by the workflow.

- `liq-data:coins/<coin>.json` — hourly history per coin (feeds the heat map page)
- `liq-data:levels.json` — current price, biggest clusters above/below, nearby bands, alerts
- `liq-data:status.json` — last run time and any failures

Raw levels file: https://raw.githubusercontent.com/af34-design/kraken-scanner/liq-data/levels.json
Bots can read it with `liqmap/levels_reader.py` (see its docstring).

Settings: repository variable `LIQ_ALERT_PCT` (default 1.5) sets how close price must be to a big
cluster to raise an alert. To stop it, disable the workflow in the Actions tab.
All data here is public exchange data and estimates, not trading advice.

## How accurate is it? (walk-forward backtest, Oct 2026)

`liqmap/backtest.py` (run with the **Liquidation heat map backtest** workflow; results land on the
`liq-backtest` branch) rebuilds the heat map every 4 hours of the last ~95 days using only data available
at that hour, then checks the next 24 hours against random levels at the same distances.
BTC, ETH, SOL, XRP, DOGE pooled:

| Question | Result |
|---|---|
| Does price reach the biggest cluster more than chance? | **No** for ordinary clusters (1.01× expected, 90% CI 0.90–1.12) |
| ...for strong clusters (≥8% of the side, ≥$50K, the alert rule)? | 1.20× expected, but random levels at the same moments get 1.10×, so about half is just volatility |
| After a touch, does price bounce more than at a random level? | Strong clusters: **+0.21%** more reversal over 6h (90% CI 0.07–0.34%) vs same-moment controls |
| Does price "cascade" through clusters more? | No (48% vs 50% run 1%+ through) |

Takeaway: treat ordinary clusters as context, not signal. Strong clusters carry a small, real-looking
effect that is smaller than a round-trip taker fee (~0.8%), so it isn't tradeable on its own. One 95-day
window in a rising market; hourly candles; re-run the workflow periodically to see if it holds.
