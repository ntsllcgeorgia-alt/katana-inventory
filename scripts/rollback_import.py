"""Roll back today's Katana import — delete every product created on/after CUTOFF.

Preserves anything created before CUTOFF. Idempotent (retries safe).
Also clears the resumable state file so future runs start clean.

Usage:
    python scripts/rollback_import.py --cutoff 2026-09-16
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
STATE_FILE = SCRIPT_DIR / "state" / "import_state.json"
KATANA_PACE = 1.1  # seconds between writes (55/min against 60/min limit)


def load_env():
    lines = (PROJECT_ROOT / ".env").read_text().splitlines()
    return dict(l.split("=", 1) for l in lines
                if "=" in l and not l.strip().startswith("#"))


def http(method, url, headers=None, retries=3):
    import socket
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(url, method=method)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            socket.setdefaulttimeout(30)
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read().decode("utf-8")
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            return e.code, {"error": e.read().decode("utf-8", errors="replace")[:500]}
        except Exception as e:
            last = e
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
    return -1, {"error": str(last)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cutoff", required=True,
                    help="Delete products created on or after this ISO date (e.g. 2026-09-16)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would be deleted without doing it")
    args = ap.parse_args()

    env = load_env()
    base = env["KATANA_API_BASE"]
    key = env["KATANA_API_KEY"]
    headers = {"Authorization": f"Bearer {key}"}

    print(f"Rollback cutoff: >= {args.cutoff}")
    print(f"Target: {base}")
    print()

    # Fetch every product
    print("Fetching all Katana products...", end=" ", flush=True)
    all_products = []
    page = 1
    while True:
        st, body = http("GET", f"{base}/products?limit=250&page={page}", headers)
        if st != 200:
            sys.exit(f"GET /products page {page} HTTP {st}: {body}")
        data = body.get("data", []) if isinstance(body, dict) else []
        all_products.extend(data)
        if len(data) < 250:
            break
        page += 1
    print(f"{len(all_products)} total")

    to_delete = [p for p in all_products if p.get("created_at", "") >= args.cutoff]
    to_keep = [p for p in all_products if p.get("created_at", "") < args.cutoff]

    print(f"  Keep (created before {args.cutoff}): {len(to_keep)}")
    print(f"  Delete (created on/after):           {len(to_delete)}")
    print()
    if to_keep:
        print("Preserving:")
        for p in to_keep[:10]:
            print(f"  id={p['id']}  created={p['created_at'][:10]}  {p['name'][:60]}")
        if len(to_keep) > 10:
            print(f"  ... and {len(to_keep) - 10} more")
        print()

    if args.dry_run:
        secs = int(len(to_delete) * KATANA_PACE)
        print(f"[DRY-RUN] Would delete {len(to_delete)} products in ~{secs//60}m {secs%60}s")
        return

    if not to_delete:
        print("Nothing to delete. Done.")
        return

    print(f"Deleting {len(to_delete)} products at {KATANA_PACE}s pace...")
    ok = 0
    fail = 0
    start = time.time()
    for i, p in enumerate(to_delete, 1):
        st, body = http("DELETE", f"{base}/products/{p['id']}", headers)
        if st in (200, 204):
            ok += 1
        else:
            fail += 1
            print(f"  FAIL id={p['id']} HTTP {st}: {str(body)[:200]}")
        if i % 25 == 0 or i == len(to_delete):
            elapsed = time.time() - start
            rate = i / max(elapsed, 0.1)
            eta = (len(to_delete) - i) / max(rate, 0.001)
            print(f"  [{i:>4}/{len(to_delete)}]  ok={ok}  fail={fail}  "
                  f"elapsed={int(elapsed)}s  eta={int(eta)}s")
        time.sleep(KATANA_PACE)

    print()
    print(f"=== DONE ===")
    print(f"  Deleted: {ok}")
    print(f"  Failed:  {fail}")

    # Clear resumable state so any future import starts fresh
    if STATE_FILE.exists():
        STATE_FILE.unlink()
        print(f"  Cleared state file: {STATE_FILE}")


if __name__ == "__main__":
    main()
