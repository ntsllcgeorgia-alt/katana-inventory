"""Sync SmartStation sales into Katana.

Runtime bridge that closes the loop from register-at-Vado to inventory-in-Katana.
Reads a CSV of sales rows and, for each one:

  1. Creates a stock_adjustment that decrements the sold quantity at the Vado
     location (the mechanical effect: "one is gone from the shelf")
  2. Optionally records a sales_order (audit trail — useful for reporting)

Idempotent: state file tracks (sku, sale_id) tuples already processed. Safe to
re-run; safe to point at a growing CSV.

Rate-limited: 55 writes/min against Katana's 60/min ceiling.

CSV format expected (case-insensitive column names, any order):
    sku        - the SKU sold (must exist in Katana as a variant SKU)
    quantity   - integer count sold (positive)
    sale_id    - unique per-sale identifier from SmartStation (idempotency key)
    sold_at    - ISO timestamp of the sale (optional but recommended)
    price      - unit sale price (optional; falls back to variant's sales_price)

Environment (.env):
    KATANA_API_KEY, KATANA_API_BASE   - as always
    NTS_VADO_LOCATION_ID              - Katana location id to decrement from
                                        (create the location in the UI, then
                                        put its id here)

Usage:
    python scripts/sync_sales_to_katana.py --csv path/to/sales.csv
    python scripts/sync_sales_to_katana.py --csv path/to/sales.csv --dry-run
    python scripts/sync_sales_to_katana.py --csv path/to/sales.csv --no-order
"""
import argparse
import csv
import json
import socket
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
STATE_DIR = SCRIPT_DIR / "state"
LOG_DIR = PROJECT_ROOT / "logs"
STATE_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)
STATE_FILE = STATE_DIR / "sales_sync_state.json"

KATANA_PACE = 1.1  # 55/min against 60/min

# CSV column name aliases — SmartStation exports vary between installs
COL_ALIASES = {
    "sku":        ["sku", "part_number", "part number", "partnumber", "item", "item_id", "plu"],
    "quantity":   ["quantity", "qty", "qty_sold", "quantity_sold", "count", "units"],
    "sale_id":    ["sale_id", "transaction_id", "trans_id", "receipt_id", "receipt", "id"],
    "sold_at":    ["sold_at", "sale_time", "timestamp", "date_time", "datetime", "sold_at_utc"],
    "price":      ["price", "unit_price", "sale_price", "amount"],
}


def load_env():
    lines = (PROJECT_ROOT / ".env").read_text(encoding="utf-8").splitlines()
    env = {}
    for l in lines:
        if "=" not in l or l.strip().startswith("#"):
            continue
        k, _, v = l.partition("=")
        env[k.strip()] = v.strip()
    return env


def http(method, url, headers=None, body=None, retries=3, timeout=30):
    """HTTP with retries + explicit socket timeouts (Windows stall workaround)."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(url, method=method)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            old = socket.getdefaulttimeout()
            socket.setdefaulttimeout(timeout)
            try:
                with urllib.request.urlopen(req, data=data, timeout=timeout) as r:
                    raw = r.read().decode("utf-8")
                    return r.status, (json.loads(raw) if raw else None)
            finally:
                socket.setdefaulttimeout(old)
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, raw
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
            last = e
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            return -1, {"error": f"{type(e).__name__}: {e}"}
    return -1, {"error": str(last)}


def pick_col(row_low, aliases):
    for a in aliases:
        if a in row_low and str(row_low[a]).strip():
            return row_low[a]
    return None


def parse_csv(path):
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for i, raw in enumerate(csv.DictReader(f)):
            low = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
            r = {
                "sku": pick_col(low, COL_ALIASES["sku"]),
                "quantity": pick_col(low, COL_ALIASES["quantity"]),
                "sale_id": pick_col(low, COL_ALIASES["sale_id"]),
                "sold_at": pick_col(low, COL_ALIASES["sold_at"]),
                "price": pick_col(low, COL_ALIASES["price"]),
                "_line": i + 2,  # human-friendly line number (header is line 1)
                "_raw": raw,
            }
            if not r["sku"]:
                continue
            try:
                r["quantity"] = int(float(r["quantity"]))
            except (TypeError, ValueError):
                continue
            if r["quantity"] <= 0:
                continue
            if r["price"]:
                try:
                    r["price"] = float(str(r["price"]).replace("$", "").replace(",", ""))
                except ValueError:
                    r["price"] = None
            rows.append(r)
    return rows


def katana_variant_map(env):
    """Fetch all variants; return {sku: (variant_id, product_id, sales_price)}."""
    base = env["KATANA_API_BASE"]
    headers = {"Authorization": f"Bearer {env['KATANA_API_KEY']}"}
    out = {}
    page = 1
    while True:
        st, body = http("GET", f"{base}/variants?limit=250&page={page}", headers)
        if st != 200:
            sys.exit(f"Katana /variants page {page} HTTP {st}: {body}")
        data = body.get("data", []) if isinstance(body, dict) else []
        if not data:
            break
        for v in data:
            sku = str(v.get("sku") or "").strip()
            if sku:
                out[sku] = (v["id"], v["product_id"], float(v.get("sales_price") or 0))
        if len(data) < 250:
            break
        page += 1
    return out


def katana_stock_adjustment(env, variant_id, location_id, delta, reason, ref):
    """POST a stock_adjustment. delta is negative for sales."""
    base = env["KATANA_API_BASE"]
    headers = {"Authorization": f"Bearer {env['KATANA_API_KEY']}"}
    body = {
        "location_id": int(location_id),
        "stock_adjustment_date": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "additional_info": ref,
        "stock_adjustment_rows": [
            {"variant_id": variant_id, "quantity": float(delta)}
        ],
    }
    return http("POST", f"{base}/stock_adjustments", headers, body)


def katana_sales_order(env, sku_line_items, location_id, customer_id, ref):
    """Optional: record a sales_order for reporting.

    sku_line_items: [{variant_id, quantity, price}, ...]
    """
    base = env["KATANA_API_BASE"]
    headers = {"Authorization": f"Bearer {env['KATANA_API_KEY']}"}
    body = {
        "order_no": ref,
        "customer_id": int(customer_id),
        "location_id": int(location_id),
        "source": "smartstation",
        "sales_order_rows": [
            {
                "variant_id": li["variant_id"],
                "quantity": li["quantity"],
                "price_per_unit": li["price"],
            } for li in sku_line_items
        ],
    }
    return http("POST", f"{base}/sales_orders", headers, body)


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"done_sale_ids": [], "last_run": None}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="Path to SmartStation sales export CSV")
    ap.add_argument("--dry-run", action="store_true",
                    help="Read + plan but don't write to Katana")
    ap.add_argument("--no-order", action="store_true",
                    help="Skip creating sales_orders — only decrement stock")
    ap.add_argument("--customer-id", type=int, default=None,
                    help="Katana customer_id for the sales_order (required unless --no-order)")
    ap.add_argument("--location-id", type=int, default=None,
                    help="Override NTS_VADO_LOCATION_ID from .env")
    args = ap.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        sys.exit(f"Not found: {csv_path}")

    env = load_env()
    location_id = args.location_id or int(env.get("NTS_VADO_LOCATION_ID", "0"))
    if not location_id:
        sys.exit("NTS_VADO_LOCATION_ID missing from .env (and no --location-id given). "
                 "Create the Vado location in Katana UI and put its id in .env.")

    mode = "DRY-RUN" if args.dry_run else "LIVE"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"sales_sync_{stamp}_{mode.lower()}.jsonl"
    print(f"[{mode}] Sales sync: {csv_path.name} -> Katana (location {location_id})")
    print(f"Log: {log_path}")
    print()

    print("Parsing CSV...", end=" ", flush=True)
    rows = parse_csv(csv_path)
    print(f"{len(rows)} valid sale rows")

    print("Building Katana variant map...", end=" ", flush=True)
    var_map = katana_variant_map(env)
    print(f"{len(var_map)} variants")

    state = load_state()
    done = set(state.get("done_sale_ids", []))

    plan = []
    stats = {"total": len(rows), "skip_done": 0, "skip_missing_sku": 0,
             "missing_skus": set(), "would_sync": 0}
    for r in rows:
        sale_key = r["sale_id"] or f"{r['sku']}|{r['sold_at']}|{r['_line']}"
        if sale_key in done:
            stats["skip_done"] += 1
            continue
        if r["sku"] not in var_map:
            stats["skip_missing_sku"] += 1
            stats["missing_skus"].add(r["sku"])
            continue
        variant_id, product_id, default_price = var_map[r["sku"]]
        price = r["price"] if r["price"] is not None else default_price
        plan.append({
            "sale_key": sale_key,
            "sku": r["sku"],
            "variant_id": variant_id,
            "quantity": r["quantity"],
            "price": price,
            "sold_at": r["sold_at"],
        })
        stats["would_sync"] += 1

    print()
    print("=== Plan ===")
    print(f"  Total rows in CSV:          {stats['total']}")
    print(f"  Skip (already synced):      {stats['skip_done']}")
    print(f"  Skip (SKU not in Katana):   {stats['skip_missing_sku']}")
    if stats["missing_skus"]:
        preview = list(stats["missing_skus"])[:5]
        print(f"    Examples: {preview}{'...' if len(stats['missing_skus']) > 5 else ''}")
    print(f"  Would sync:                 {stats['would_sync']}")
    print()

    if args.dry_run:
        print("Dry-run complete. No writes performed.")
        return

    if not plan:
        print("Nothing to sync. Done.")
        return

    # Group by sale_key so multi-line sales become one sales_order
    from collections import defaultdict
    by_sale = defaultdict(list)
    for p in plan:
        by_sale[p["sale_key"]].append(p)

    logf = log_path.open("w", encoding="utf-8")
    ok = 0
    fail = 0
    start = time.time()

    for sale_idx, (sale_key, items) in enumerate(by_sale.items(), 1):
        # 1. Stock adjustment (decrement) — one adjustment can carry multiple rows
        base = env["KATANA_API_BASE"]
        headers = {"Authorization": f"Bearer {env['KATANA_API_KEY']}"}
        adj_body = {
            "location_id": location_id,
            "stock_adjustment_date": datetime.now(timezone.utc).isoformat(),
            "reason": "Retail sale",
            "additional_info": f"SmartStation sale {sale_key}",
            "stock_adjustment_rows": [
                {"variant_id": it["variant_id"], "quantity": -float(it["quantity"])}
                for it in items
            ],
        }
        st, resp = http("POST", f"{base}/stock_adjustments", headers, adj_body)
        adj_ok = st in (200, 201)
        entry = {"ts": datetime.now(timezone.utc).isoformat(), "sale_key": sale_key,
                 "step": "stock_adjustment", "status": st,
                 "items": [{"sku": it["sku"], "qty": it["quantity"]} for it in items]}
        if not adj_ok:
            entry["error"] = resp
        logf.write(json.dumps(entry) + "\n")
        logf.flush()

        # 2. Sales order (optional, for reporting)
        if adj_ok and not args.no_order and args.customer_id:
            so_body = {
                "order_no": f"POS-{sale_key}",
                "customer_id": args.customer_id,
                "location_id": location_id,
                "source": "smartstation",
                "sales_order_rows": [
                    {"variant_id": it["variant_id"],
                     "quantity": it["quantity"],
                     "price_per_unit": float(it["price"] or 0)}
                    for it in items
                ],
            }
            time.sleep(KATANA_PACE)  # respect rate limit between the two calls
            st_so, so_resp = http("POST", f"{base}/sales_orders", headers, so_body)
            entry_so = {"ts": datetime.now(timezone.utc).isoformat(), "sale_key": sale_key,
                        "step": "sales_order", "status": st_so}
            if st_so not in (200, 201):
                entry_so["error"] = so_resp
            logf.write(json.dumps(entry_so) + "\n")
            logf.flush()

        if adj_ok:
            ok += 1
            done.add(sale_key)
            state["done_sale_ids"] = list(done)
            if ok % 10 == 0:
                save_state(state)
        else:
            fail += 1

        if sale_idx % 25 == 0 or sale_idx == len(by_sale):
            elapsed = time.time() - start
            rate = sale_idx / max(elapsed, 0.1)
            eta = (len(by_sale) - sale_idx) / max(rate, 0.001)
            print(f"  [{sale_idx:>4}/{len(by_sale)}]  ok={ok}  fail={fail}  "
                  f"elapsed={int(elapsed)}s  eta={int(eta)}s")

        time.sleep(KATANA_PACE)

    state["last_run"] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    logf.close()

    print()
    print("=== DONE ===")
    print(f"  Synced:  {ok}")
    print(f"  Failed:  {fail}")
    print(f"  Log:     {log_path}")


if __name__ == "__main__":
    main()
