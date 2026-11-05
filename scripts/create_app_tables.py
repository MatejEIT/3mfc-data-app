"""Create the app writeback bucket and tables in Keboola Storage.

DRY RUN by default. Run with --execute only after explicit approval.
Existing tables are never dropped or truncated; they are reported and skipped.

    python scripts/create_app_tables.py                # show the plan
    python scripts/create_app_tables.py --execute      # create missing objects
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.schema import APP_TABLES  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args()

    url = os.environ.get("KBC_URL", "https://connection.europe-west3.gcp.keboola.com")
    token = os.environ.get("KBC_TOKEN", "")
    bucket = os.environ.get("APP_BUCKET", "in.c-3mfc-app")
    stage, name = bucket.split(".", 1)
    name = name.removeprefix("c-")

    print(f"Target bucket: {bucket}")
    for t in APP_TABLES:
        print(f"  {t.name:34s} PK={','.join(t.primary_key):45s} cols={len(t.columns)}"
              f"{'  (append-only)' if t.append_only else ''}")
    if not args.execute:
        print("\nDry run - nothing created. Re-run with --execute after approval.")
        return 0
    if not token:
        print("KBC_TOKEN missing", file=sys.stderr)
        return 1

    from kbcstorage.client import Client

    client = Client(url, token)
    if bucket not in {b["id"] for b in client.buckets.list()}:
        client.buckets.create(name, stage=stage, description="3MFC Data App writeback (app-owned, do not overwrite)")
        print(f"created bucket {bucket}")
    existing = {t["name"] for t in client.buckets.list_tables(bucket)}
    for t in APP_TABLES:
        if t.name in existing:
            print(f"skip {t.name} (exists)")
            continue
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f"{t.name}.csv"
            with path.open("w", newline="", encoding="utf-8") as fh:
                csv.writer(fh).writerow(t.columns)
            client.tables.create(bucket, t.name, str(path), primary_key=list(t.primary_key))
        print(f"created {t.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
