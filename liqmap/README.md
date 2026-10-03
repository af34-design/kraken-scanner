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
