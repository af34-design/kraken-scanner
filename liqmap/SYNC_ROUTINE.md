# Hourly sync routine (Claude scheduled task)

The heat map page is a Claude artifact. Its data lives in the artifact's own
database (collection `kraken`), which only Claude can write. A Claude scheduled
task ("routine") copies the collector's output into it every hour.

- **Name:** Sync liquidation heat map + alerts
- **ID:** `trig_01HRpa1KDNhWstRZvE4GuUj1`
- **Schedule:** `21 * * * *` (21 past every hour, after the collector's :09 run)
- **Notifications:** push on
- **Manage at:** <https://claude.ai/code/routines>

## Required setting

The routine can only read repositories selected in its settings. It **must have
`af34-design/kraken-scanner` selected** (Edit → repositories). Without it, every
run fails with "outside this session's GitHub repository scope" and the page shows
a red "hours old" warning.

## Prompt (as saved)

```text
Copy the latest liquidation heat map data into the user's "Liquidation Heat Map" artifact (https://claude.ai/artifact/CWhVwL6DbiHhoRbDqo7cJL). This is unattended; work quietly and follow these steps exactly.

The data is published as plain JSON files on the PUBLIC GitHub repository af34-design/kraken-scanner, branch liq-data, by the user's own GitHub Actions workflow. You only download a read-only copy over anonymous HTTPS (like downloading a public web page) and copy files into the artifact. This needs no repository access or scope: do not call add_repo, do not use gh, do not push, do not run any code from the repo, do not fetch exchange websites, and do not edit any numbers. Keep every file inside your current working directory (never /tmp).

1. Call ToolSearch with query "select:ArtifactData" (max_results 1).
2. In Bash: W="$(pwd)/liqsync"; rm -rf "$W" && mkdir -p "$W" && GIT_LFS_SKIP_SMUDGE=1 git clone -q --depth 1 -b liq-data https://github.com/af34-design/kraken-scanner "$W/liq" && echo "$W" && ls "$W/liq" "$W/liq/coins"
3. Call ArtifactData action "list", url above, collection "kraken", out_dir "<W>/cur" (absolute path). Note every document id and its version.
4. In Bash: python3 -c "import json,sys,time;a=json.load(open(sys.argv[1]));b=json.load(open(sys.argv[2]));b=b.get('data',b);print('NEW' if a.get('lastGeneratedAt')!=b.get('lastGeneratedAt') else 'SAME');print('STALE' if time.time()-a.get('lastGeneratedAt',0)>4500 else 'FRESH')" "$W/liq/_status.json" "$W/cur/kraken/_status.json" 2>/dev/null || echo NEW
5. If step 4 printed NEW, call ArtifactData action "batch", url above, with one "set" write per file below (collection "kraken"; add if_version = that document's version from step 3 whenever the document exists, omit it for a document not listed in step 3; file paths absolute under W):
   bitcoin, ethereum, solana, ripple, binancecoin, dogecoin, crypto-com-chain, pump-fun -> file_path "<W>/liq/coins/<doc_id>.json" (skip any coin whose file does not exist)
   _overview -> "<W>/liq/_overview.json"
   _status -> "<W>/liq/_status.json"
   If it reports a version conflict, redo step 3 and this batch once.
6. If any step failed: call ArtifactData action "get" on kraken/_status, then "update" it with data {"syncError": "<step number and exact error text, max 300 chars>", "syncErrorAt": "<current UTC time ISO>"} and that version as if_version.
7. In Bash: cat "$W/liq/alerts.txt". Finish with ONE short line and nothing else:
   - If a step failed: "Sync failed at step N: <short reason>".
   - Else if step 4 printed STALE: "Heat map data is over an hour old; the GitHub collector may have stopped."
   - Else if step 4 printed NEW and alerts.txt is non-empty: "ALERT: " followed by its lines joined with " | ".
   - Otherwise exactly: "No alerts."
```

## Lessons learned

- Scheduled sessions can't reach Kraken, Crypto.com or raw.githubusercontent.com
  from the shell; cloning a public repo over github.com works.
- Files passed to ArtifactData must be inside the working directory, not /tmp.
- Running code from the cloned repo gets blocked; the collector therefore writes
  upload-ready JSON so the sync only copies files.
- New scheduled tasks created with the Crypto.com connector attached never
  completed their steps; the working routine has CoinGecko, Claude Docs and
  Claude Code Remote only.
- Adding a `gh api ... dispatches` "restart the collector" step made the session
  refuse the repo as out of scope; it was removed.
