"""
Import NTP BigCommerce catalog into Katana MRP.

Rule B+ (full path with cleanup):
  - Only products in the 'All Products' tree get a category
  - category_name = "Level2 > Level3 > Leaf" (strip 'All Products >' prefix)
  - Admin/promo overlays (App Promotions, Fishbowl Product Export, Promotions,
    Shop All (Legacy), Applications) are ignored for category assignment
  - Products in admin-only or no category → category_name = null

Data mapping (BC -> Katana):
  name              <- BC.name
  category_name     <- deepest 'All Products' path (see Rule B+) or None
  additional_info   <- BC.description (stripped of HTML tags, truncated)
  uom               <- "pcs"
  is_sellable       <- True
  is_purchasable    <- True
  is_producible     <- False
  batch_tracked     <- False
  serial_tracked    <- False
  variants[0].sku              <- BC.sku
  variants[0].sales_price      <- BC.price     (retail)
  variants[0].purchase_price   <- 0            (BC has no cost data - backfill later)
  variants[0].registered_barcode <- BC.upc

Rate limit: Katana 60/min. We pace at 55/min (1.1s between writes).
Resumable: state file records completed SKUs. Re-run picks up where it stopped.
Idempotent: skips any SKU already in Katana.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
ENV_PATH = PROJECT_ROOT / ".env"
LOG_DIR = PROJECT_ROOT / "logs"
STATE_DIR = SCRIPT_DIR / "state"
LOG_DIR.mkdir(exist_ok=True)
STATE_DIR.mkdir(exist_ok=True)

ADMIN_ROOTS = {
    "App Promotions",
    "Applications",
    "Fishbowl Product Export",
    "Promotions",
    "Shop All (Legacy)",
}

KATANA_PACE_SECONDS = 1.1  # 55/min against 60/min limit
BC_PAGE_SIZE = 250
KATANA_PAGE_SIZE = 250

_html_tag = re.compile(r"<[^>]+>")
_ws = re.compile(r"\s+")


def load_env(path):
    if not path.exists():
        sys.exit(f"Missing {path}. Copy .env.example to .env and fill in creds.")
    env = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip()
    return env


def http(method, url, headers=None, body=None, timeout=60):
    """Simple HTTP wrapper. Returns (status, parsed_body, headers)."""
    req = urllib.request.Request(url, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=data, timeout=timeout) as r:
            raw = r.read().decode("utf-8")
            return r.status, (json.loads(raw) if raw else None), dict(r.headers)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            body_parsed = json.loads(raw)
        except Exception:
            body_parsed = raw
        return e.code, body_parsed, dict(e.headers)
    except urllib.error.URLError as e:
        return -1, {"error": str(e)}, {}


def bc_get_all(env, path, params=""):
    """Paginate through a BC v3 endpoint. Returns list of items."""
    base = env["BC_API_BASE"]
    headers = {"X-Auth-Token": env["BC_ACCESS_TOKEN"], "Accept": "application/json"}
    out = []
    page = 1
    while True:
        sep = "&" if "?" in path else "?"
        url = f"{base}/{path}{sep}limit={BC_PAGE_SIZE}&page={page}{('&' + params) if params else ''}"
        status, body, _ = http("GET", url, headers)
        if status != 200:
            sys.exit(f"BC {status} on {url}: {body}")
        out.extend(body.get("data", []))
        pg = body.get("meta", {}).get("pagination", {})
        if pg.get("current_page", 1) >= pg.get("total_pages", 1):
            break
        page += 1
    return out


def build_category_maps(cats):
    """Returns (id_to_full_path, allowed_ids_set).

    id_to_full_path: {cat_id: "Level2 > Level3 > Leaf"} for cats under All Products.
    allowed_ids_set: cat_ids whose root is 'All Products' (the real taxonomy).
    """
    by_id = {c["id"]: c for c in cats}

    def full_path(cat_id):
        names = []
        cur = by_id.get(cat_id)
        while cur:
            names.insert(0, cur["name"])
            if cur.get("parent_id") and cur["parent_id"] in by_id:
                cur = by_id[cur["parent_id"]]
            else:
                cur = None
        return names

    id_to_path = {}
    allowed = set()
    for c in cats:
        chain = full_path(c["id"])
        if not chain:
            continue
        if chain[0] == "All Products" and len(chain) > 1:
            # strip the "All Products" root
            id_to_path[c["id"]] = " > ".join(chain[1:])
            allowed.add(c["id"])
    return id_to_path, allowed


def pick_category(product, id_to_path, allowed_ids):
    """Return the category_name string for a BC product, or None.

    Rule: among the product's BC categories that are in the 'All Products' tree,
    pick the DEEPEST (most specific). Break ties alphabetically for stability.
    """
    candidates = []
    for cid in product.get("categories", []) or []:
        if cid in allowed_ids:
            path = id_to_path[cid]
            candidates.append((path.count(">"), path))  # more '>' = deeper
    if not candidates:
        return None
    candidates.sort(key=lambda x: (-x[0], x[1]))  # deepest first, then alpha
    return candidates[0][1]


def clean_description(html):
    if not html:
        return ""
    text = _html_tag.sub(" ", html)
    text = _ws.sub(" ", text).strip()
    # Katana additional_info: keep reasonable length
    return text[:2000]


def build_katana_payload(product, category_name):
    """BC product record -> Katana POST /products body."""
    sku = str(product.get("sku") or "").strip()
    upc = str(product.get("upc") or "").strip() or None
    price = float(product.get("price") or 0)
    return {
        "name": (product.get("name") or "").strip()[:255],
        "category_name": category_name,
        "additional_info": clean_description(product.get("description")),
        "uom": "pcs",
        "is_sellable": True,
        "is_purchasable": True,
        "is_producible": False,
        "batch_tracked": False,
        "serial_tracked": False,
        "variants": [
            {
                "sku": sku,
                "sales_price": price,
                "purchase_price": 0,
                "registered_barcode": upc,
            }
        ],
    }


def katana_existing_skus(env):
    """Fetch all existing variant SKUs so we skip duplicates."""
    base = env["KATANA_API_BASE"]
    headers = {"Authorization": f"Bearer {env['KATANA_API_KEY']}"}
    skus = set()
    page = 1
    while True:
        url = f"{base}/variants?limit={KATANA_PAGE_SIZE}&page={page}"
        status, body, _ = http("GET", url, headers)
        if status != 200:
            sys.exit(f"Katana {status} listing variants: {body}")
        data = body.get("data", []) if isinstance(body, dict) else []
        if not data:
            break
        for v in data:
            if v.get("sku"):
                skus.add(str(v["sku"]).strip())
        if len(data) < KATANA_PAGE_SIZE:
            break
        page += 1
    return skus


def katana_create_product(env, payload):
    base = env["KATANA_API_BASE"]
    headers = {"Authorization": f"Bearer {env['KATANA_API_KEY']}"}
    return http("POST", f"{base}/products", headers, payload)


def load_state(state_path):
    if state_path.exists():
        return json.loads(state_path.read_text(encoding="utf-8"))
    return {"done": [], "failed": {}, "started_at": None, "finished_at": None}


def save_state(state_path, state):
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Import NTP BigCommerce -> Katana MRP")
    ap.add_argument("--live", action="store_true",
                    help="Actually write to Katana. Default is dry-run (no writes).")
    ap.add_argument("--limit", type=int, default=None,
                    help="Only process the first N products (useful for testing).")
    ap.add_argument("--sample-preview", type=int, default=10,
                    help="How many sample payloads to print in dry-run mode.")
    args = ap.parse_args()

    env = load_env(ENV_PATH)
    for k in ("BC_API_BASE", "BC_ACCESS_TOKEN", "KATANA_API_BASE", "KATANA_API_KEY"):
        if not env.get(k):
            sys.exit(f"Missing {k} in .env")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    mode = "LIVE" if args.live else "DRY-RUN"
    log_path = LOG_DIR / f"import_{stamp}_{mode.lower()}.jsonl"
    state_path = STATE_DIR / "import_state.json"

    print(f"[{mode}] NTP BigCommerce -> Katana catalog import")
    print(f"Log file: {log_path}")
    print()

    # 1. Load BC categories, build maps
    print("Fetching BC categories...", end=" ", flush=True)
    cats = bc_get_all(env, "catalog/categories")
    id_to_path, allowed_ids = build_category_maps(cats)
    print(f"{len(cats)} total, {len(allowed_ids)} under 'All Products' tree")

    # 2. Load BC products (with the fields we need)
    print("Fetching BC products (all fields)...", end=" ", flush=True)
    fields = "id,name,sku,price,cost_price,upc,description,categories,inventory_level"
    products = bc_get_all(env, "catalog/products", f"include_fields={fields}")
    print(f"{len(products)} products")

    if args.limit:
        products = products[:args.limit]
        print(f"  (limited to first {args.limit})")

    # 3. Load Katana existing SKUs (skip duplicates)
    print("Fetching existing Katana variant SKUs...", end=" ", flush=True)
    existing = katana_existing_skus(env)
    print(f"{len(existing)} existing")
    print()

    # 4. Load prior state for resume
    state = load_state(state_path)
    done_set = set(state.get("done", []))
    if not state.get("started_at") or args.live:
        state["started_at"] = state.get("started_at") or datetime.now(timezone.utc).isoformat()

    # 5. Plan
    plan = []
    stats = {
        "total": len(products),
        "skip_no_sku": 0,
        "skip_existing": 0,
        "skip_already_done": 0,
        "would_write": 0,
        "cat_null": 0,
        "cat_mapped": 0,
    }
    for p in products:
        sku = str(p.get("sku") or "").strip()
        if not sku:
            stats["skip_no_sku"] += 1
            continue
        if sku in existing:
            stats["skip_existing"] += 1
            continue
        if sku in done_set:
            stats["skip_already_done"] += 1
            continue
        cat = pick_category(p, id_to_path, allowed_ids)
        if cat is None:
            stats["cat_null"] += 1
        else:
            stats["cat_mapped"] += 1
        plan.append((p, cat))
        stats["would_write"] += 1

    print("=== Plan ===")
    print(f"  Total BC products:              {stats['total']}")
    print(f"  Skip (no SKU):                  {stats['skip_no_sku']}")
    print(f"  Skip (SKU already in Katana):   {stats['skip_existing']}")
    print(f"  Skip (done in prior run):       {stats['skip_already_done']}")
    print(f"  --")
    print(f"  Would create:                   {stats['would_write']}")
    print(f"    with category:                {stats['cat_mapped']}")
    print(f"    without category (null):      {stats['cat_null']}")
    print()

    # 6. Category preview
    cat_hist = {}
    for _, cat in plan:
        cat_hist[cat] = cat_hist.get(cat, 0) + 1
    if not args.live:
        print(f"=== Category distribution (top 20 of {len(cat_hist)} unique) ===")
        for cat, cnt in sorted(cat_hist.items(), key=lambda x: -x[1])[:20]:
            label = cat if cat is not None else "(no category)"
            print(f"  {cnt:>5}  {label}")
        print()

    # 7. Sample payload preview (dry-run only)
    if not args.live and plan:
        n = min(args.sample_preview, len(plan))
        print(f"=== Sample of {n} product payloads ===")
        for i in range(n):
            p, cat = plan[i]
            payload = build_katana_payload(p, cat)
            print(f"--- {i+1}. SKU={payload['variants'][0]['sku']} ---")
            print(json.dumps(payload, indent=2, ensure_ascii=False)[:800])
            print()

    # 8. Time estimate
    seconds = int(stats["would_write"] * KATANA_PACE_SECONDS)
    print(f"Estimated LIVE run time: ~{seconds // 60}m {seconds % 60}s "
          f"({stats['would_write']} writes at {KATANA_PACE_SECONDS}s each)")
    print()

    if not args.live:
        print("Dry-run complete. Nothing was written.")
        print("Re-run with --live to actually import.")
        return

    # 9. LIVE mode — write to Katana
    print("=== LIVE IMPORT STARTING ===")
    print(f"Writing to {env['KATANA_API_BASE']}")
    print()

    logf = log_path.open("w", encoding="utf-8")
    successes = 0
    failures = 0
    start = time.time()

    for i, (p, cat) in enumerate(plan, 1):
        sku = str(p.get("sku") or "").strip()
        payload = build_katana_payload(p, cat)
        status, resp, hdrs = katana_create_product(env, payload)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "sku": sku,
            "bc_id": p.get("id"),
            "status": status,
            "category_name": cat,
        }
        if status in (200, 201):
            entry["katana_id"] = resp.get("id") if isinstance(resp, dict) else None
            successes += 1
            done_set.add(sku)
            state["done"] = list(done_set)
            # Save state every 10 successes to enable resume
            if successes % 10 == 0:
                save_state(state_path, state)
        else:
            failures += 1
            entry["error"] = resp
            state.setdefault("failed", {})[sku] = {"status": status, "error": str(resp)[:500]}

        logf.write(json.dumps(entry, ensure_ascii=False) + "\n")
        logf.flush()

        if i % 25 == 0 or i == len(plan):
            elapsed = time.time() - start
            rate = i / max(elapsed, 0.1)
            eta = (len(plan) - i) / max(rate, 0.001)
            print(f"  [{i:>4}/{len(plan)}]  ok={successes}  fail={failures}  "
                  f"elapsed={int(elapsed)}s  eta={int(eta)}s")

        # Rate-limit pace
        time.sleep(KATANA_PACE_SECONDS)

    logf.close()
    state["finished_at"] = datetime.now(timezone.utc).isoformat()
    save_state(state_path, state)

    print()
    print("=== DONE ===")
    print(f"  Success: {successes}")
    print(f"  Failed:  {failures}")
    print(f"  Log:     {log_path}")
    print(f"  State:   {state_path}")
    if failures:
        print()
        print("Some products failed. Re-run to retry (they'll be attempted again).")


if __name__ == "__main__":
    main()
