"""
Import NTP BigCommerce catalog into Katana MRP.

Category rule: Rule A (deepest leaf only).
  Originally we used Rule B+ (full path "Level2 > Level3 > Leaf") but Katana's
  category_name field has an undocumented ~36-char limit — anything longer
  returns HTTP 500. Full paths like "Exterior > Rear Frame Accessories >
  Stainless Steel Rear Light Panels" (69 chars) always fail. Empirically
  verified: 35 chars OK, 38 chars fail.

  So we use the leaf name only (e.g. "Stainless Steel Rear Light Panels").
  Colliding leaf names (e.g. "Warning Lights" under both Lighting and Safety)
  get disambiguated by appending "(parent)" — e.g. "Warning Lights (Safety)".
  Names that still exceed 36 chars after collision handling are truncated
  with an ellipsis.

  Admin/promo overlays (App Promotions, Fishbowl Product Export, Promotions,
  Shop All (Legacy), Applications) are ignored — products only in those get
  no category (category_name omitted from payload).

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


def http(method, url, headers=None, body=None, timeout=30, retries=3):
    """HTTP wrapper with retries + socket-level timeouts.

    urllib.urlopen honors a `timeout` for the initial connect, but socket reads
    can still stall on Windows if the server keeps the connection open without
    sending data. We explicitly set the socket timeout as belt-and-suspenders,
    then retry on any URLError with exponential backoff.
    """
    import socket
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    last_err = None
    for attempt in range(retries):
        req = urllib.request.Request(url, method=method)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            # Set socket-level timeout too — urlopen's timeout can be defeated
            # on Windows in some cases.
            old_to = socket.getdefaulttimeout()
            socket.setdefaulttimeout(timeout)
            try:
                with urllib.request.urlopen(req, data=data, timeout=timeout) as r:
                    raw = r.read().decode("utf-8")
                    return r.status, (json.loads(raw) if raw else None), dict(r.headers)
            finally:
                socket.setdefaulttimeout(old_to)
        except urllib.error.HTTPError as e:
            # HTTP-level error is definitive — don't retry (e.g. 400/401/409)
            raw = e.read().decode("utf-8", errors="replace")
            try:
                body_parsed = json.loads(raw)
            except Exception:
                body_parsed = raw
            return e.code, body_parsed, dict(e.headers)
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(2 ** attempt)  # 1s, 2s, 4s
                continue
            return -1, {"error": f"{type(e).__name__}: {e}", "attempts": retries}, {}
    return -1, {"error": str(last_err)}, {}


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


KATANA_CATEGORY_MAX_LEN = 36  # Undocumented: Katana returns HTTP 500 above ~36 chars

def build_category_maps(cats):
    """Returns (id_to_leaf, id_to_parent_name, allowed_ids).

    Katana's category_name has an undocumented ~36-char limit — full paths like
    "Exterior > Rear Frame Accessories > Stainless Steel Rear Light Panels" (69
    chars) trigger HTTP 500. So we use Rule A (deepest leaf name only).

    Leaf collisions are rare (e.g. "Warning Lights" exists under both Lighting
    and Safety); we disambiguate collisions by appending the immediate parent.
    """
    by_id = {c["id"]: c for c in cats}

    def path_names(cat_id):
        names = []
        cur = by_id.get(cat_id)
        while cur:
            names.insert(0, cur["name"])
            if cur.get("parent_id") and cur["parent_id"] in by_id:
                cur = by_id[cur["parent_id"]]
            else:
                cur = None
        return names

    id_to_leaf = {}
    id_to_parent = {}
    id_to_depth = {}
    allowed = set()
    for c in cats:
        chain = path_names(c["id"])
        if not chain or chain[0] != "All Products" or len(chain) < 2:
            continue
        leaf = chain[-1]
        parent = chain[-2] if len(chain) >= 2 else None
        id_to_leaf[c["id"]] = leaf
        id_to_parent[c["id"]] = parent
        id_to_depth[c["id"]] = len(chain)  # actual tree depth
        allowed.add(c["id"])

    # Detect leaf name collisions across different parents
    from collections import Counter
    leaf_counts = Counter(id_to_leaf.values())
    return id_to_leaf, id_to_parent, id_to_depth, allowed, leaf_counts


def truncate_cat(name):
    """Truncate a category name to fit Katana's undocumented ~36-char limit."""
    if not name:
        return name
    if len(name) <= KATANA_CATEGORY_MAX_LEN:
        return name
    return name[:KATANA_CATEGORY_MAX_LEN - 1].rstrip() + "…"


def pick_category(product, id_to_leaf, id_to_parent, id_to_depth, allowed_ids, leaf_counts):
    """Return the category_name (leaf-only) for a BC product, or None.

    Rule A: pick the deepest All-Products category the product is in. Use its
    leaf name. Disambiguate colliding leaves by appending "(parent)". Truncate
    to Katana's 36-char limit if still too long.
    """
    candidates = [cid for cid in (product.get("categories") or []) if cid in allowed_ids]
    if not candidates:
        return None
    # Sort by actual tree depth (deepest first), then alphabetical on leaf for stability
    candidates.sort(key=lambda cid: (-id_to_depth.get(cid, 0), id_to_leaf.get(cid, "")))
    best = candidates[0]
    leaf = id_to_leaf[best]
    if leaf_counts[leaf] > 1:
        parent = id_to_parent.get(best)
        if parent:
            leaf = f"{leaf} ({parent})"
    return truncate_cat(leaf)


def clean_description(html):
    if not html:
        return ""
    text = _html_tag.sub(" ", html)
    text = _ws.sub(" ", text).strip()
    # Katana additional_info: keep reasonable length
    return text[:2000]


def build_katana_payload(product, category_name):
    """BC product record -> Katana POST /products body.

    Notes on gotchas found the hard way:
      - category_name > 36 chars returns HTTP 500 (see truncate_cat)
      - registered_barcode: null returns 422 (must be string) — OMIT the field
        entirely when there's no UPC, don't send null.
    """
    sku = str(product.get("sku") or "").strip()
    upc = str(product.get("upc") or "").strip()
    price = float(product.get("price") or 0)
    variant = {
        "sku": sku,
        "sales_price": price,
        "purchase_price": 0,
    }
    if upc:  # only include if we have one — null triggers 422
        variant["registered_barcode"] = upc
    payload = {
        "name": (product.get("name") or "").strip()[:255],
        "additional_info": clean_description(product.get("description")),
        "uom": "pcs",
        "is_sellable": True,
        "is_purchasable": True,
        "is_producible": False,
        "batch_tracked": False,
        "serial_tracked": False,
        "variants": [variant],
    }
    if category_name:  # omit key if null — cleaner
        payload["category_name"] = category_name
    return payload


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
    id_to_leaf, id_to_parent, id_to_depth, allowed_ids, leaf_counts = build_category_maps(cats)
    collisions = sum(1 for c in leaf_counts.values() if c > 1)
    print(f"{len(cats)} total, {len(allowed_ids)} under 'All Products' tree, "
          f"{collisions} leaf-name collisions (disambiguated by parent)")

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
        cat = pick_category(p, id_to_leaf, id_to_parent, id_to_depth, allowed_ids, leaf_counts)
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
