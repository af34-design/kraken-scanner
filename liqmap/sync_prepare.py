#!/usr/bin/env python3
"""
Prepare the heat map page's database update from a checkout of the `liq-data` branch.
Used by the hourly Claude sync task; standard library only.

  python liqmap/sync_prepare.py --data <liq-data dir> --prev <current _status.json or ''> \
      --versions '{"bitcoin": 5, ...}' --out <dir>

Writes <out>/<doc>.json for each document, <out>/batch.json (ready to pass as the
ArtifactData batch `writes`), and <out>/alerts.txt (new alert lines, may be empty).
"""
import argparse, json, os, time

COINS = ["bitcoin", "ethereum", "solana", "ripple", "binancecoin", "dogecoin", "crypto-com-chain"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--prev", default="")
    ap.add_argument("--versions", default="{}")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    versions = json.loads(a.versions or "{}")
    prev = {}
    if a.prev and os.path.exists(a.prev):
        p = json.load(open(a.prev))
        prev = p.get("data", p)  # ArtifactData out_dir files may wrap the body
    levels = json.load(open(os.path.join(a.data, "levels.json")))
    status = json.load(open(os.path.join(a.data, "status.json")))
    new_run = levels.get("generatedAt") != prev.get("lastGeneratedAt")
    writes = []

    def add(doc_id, body):
        path = os.path.abspath(os.path.join(a.out, doc_id + ".json"))
        with open(path, "w") as f:
            json.dump(body, f, separators=(",", ":"))
        w = {"op": "set", "collection": "kraken", "doc_id": doc_id, "file_path": path}
        if doc_id in versions:
            w["if_version"] = int(versions[doc_id])
        writes.append(w)

    alerts = []
    if new_run:
        for c in COINS:
            fp = os.path.join(a.data, "coins", c + ".json")
            if os.path.exists(fp):
                add(c, json.load(open(fp)))
        add("_overview", {k: v for k, v in levels.items() if k != "alertState"})
        alerts = [al["message"] for al in levels.get("alerts", [])]
    age_h = (time.time() - levels.get("generatedAt", 0)) / 3600
    add("_status", dict(status, syncedAt=int(time.time()), lastGeneratedAt=levels.get("generatedAt"),
                        syncedBy="scheduled", newRun=new_run, collectorAgeHours=round(age_h, 2)))
    json.dump(writes, open(os.path.join(a.out, "batch.json"), "w"), indent=1)
    open(os.path.join(a.out, "alerts.txt"), "w").write("\n".join(alerts))
    print(f"documents: {len(writes)} | new collector run: {new_run} | collector age: {age_h:.1f}h | alerts: {len(alerts)}")
    for m in alerts:
        print("ALERT " + m)


if __name__ == "__main__":
    main()
