"""Pull MSRP for every NTP BigCommerce SKU from Fishbowl.

Verified pattern (see _test_msrp_5skus.py for evidence):
  Each Part has 1-3 sibling Product records with different prices.
  MSRP = max(product.price) across all products where product.partId = part.id.
  The '-7' suffix product is typically the retail tier.

Uses Fishbowl's /api/data-query REST endpoint with raw SQL.
Auth: admin user + ChromeOutletLister app registration (already approved).

Output: imports/msrp_by_sku.json
  { sku: { msrp, wholesale, product_num, markup, all_products: [...] } }
"""
import json
import sys
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
OUT = PROJECT_ROOT / "imports" / "msrp_by_sku.json"
OUT.parent.mkdir(exist_ok=True)

# All Fishbowl creds come from .env (gitignored). Never commit real values here.
_env_lines = (PROJECT_ROOT / ".env").read_text().splitlines()
_env = dict(l.split("=", 1) for l in _env_lines
            if "=" in l and not l.strip().startswith("#"))
FB_BASE = _env["FISHBOWL_URL"].strip()
FB_USER = _env["FISHBOWL_USER"].strip()
FB_PASS = _env["FISHBOWL_PASS"].strip()
FB_APP_NAME = _env.get("FISHBOWL_APP_NAME", "ChromeOutletLister").strip()
FB_APP_ID = int(_env.get("FISHBOWL_APP_ID", "4821").strip())


def http(method, url, headers=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Accept": "application/json"}
    if data: h["Content-Type"] = "application/json"
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try: return e.code, json.loads(raw)
        except: return e.code, {"body": raw[:400]}


def fb_login():
    st, d = http("POST", FB_BASE + "/api/login", body={
        "appName": FB_APP_NAME, "appDescription": "Katana inventory MSRP puller",
        "appId": FB_APP_ID, "username": FB_USER, "password": FB_PASS})
    if st != 200:
        raise SystemExit(f"Fishbowl login {st}: {json.dumps(d)[:300]}")
    return d.get("token") or d.get("Token")


def fb_logout(token):
    if not token: return
    http("POST", FB_BASE + "/api/logout", headers={"Authorization": "Bearer " + token})


def fb_query(token, sql):
    url = FB_BASE + "/api/data-query?" + urllib.parse.urlencode({"query": sql})
    st, d = http("GET", url, headers={"Authorization": "Bearer " + token})
    if st != 200:
        raise SystemExit(f"Fishbowl data-query HTTP {st}: {str(d)[:400]}")
    return d


def load_bc_skus():
    env_lines = (PROJECT_ROOT / ".env").read_text().splitlines()
    env = dict(l.split("=", 1) for l in env_lines
               if "=" in l and not l.strip().startswith("#"))
    token = env["BC_ACCESS_TOKEN"].strip()
    base = env["BC_API_BASE"].strip()
    skus = []
    page = 1
    while True:
        url = f"{base}/catalog/products?limit=250&page={page}&include_fields=sku"
        req = urllib.request.Request(url, headers={
            "X-Auth-Token": token, "Accept": "application/json"})
        with urllib.request.urlopen(req) as r:
            body = json.loads(r.read())
        for p in body["data"]:
            if p.get("sku"):
                skus.append(str(p["sku"]).strip())
        pg = body["meta"]["pagination"]
        if pg["current_page"] >= pg["total_pages"]:
            break
        page += 1
    return skus


def main():
    print("[1] Fetching NTP BC SKU list...")
    skus = load_bc_skus()
    print(f"    {len(skus)} SKUs")

    print("[2] Fishbowl login...")
    token = fb_login()
    print("    logged in")

    try:
        # Chunk SKUs — SQL IN() clauses can bloat request size and time
        BATCH = 300
        all_rows = []
        for i in range(0, len(skus), BATCH):
            chunk = skus[i:i + BATCH]
            quoted = ",".join("'" + s.replace("'", "''") + "'" for s in chunk)
            rows = fb_query(token,
                "SELECT p.num AS part_num, p.description AS part_desc, "
                "       prod.num AS product_num, prod.price AS price "
                "FROM part p LEFT JOIN product prod ON prod.partId = p.id "
                "WHERE p.num IN (" + quoted + ")")
            all_rows.extend(rows)
            print(f"    batch {i//BATCH + 1}: {len(chunk)} SKUs -> {len(rows)} rows  "
                  f"(cumulative: {len(all_rows)})")

        print(f"[3] Aggregating by part...")
        by_part = {}
        for r in all_rows:
            by_part.setdefault(r["part_num"], []).append(r)

        results = {}
        with_msrp = 0
        without_msrp = 0
        not_in_fb = 0
        for sku in skus:
            rows = by_part.get(sku, [])
            prices = [r for r in rows if r.get("price") is not None]
            if not rows:
                results[sku] = {"msrp": None, "wholesale": None,
                                "product_num": None, "markup": None,
                                "all_products": [], "note": "part not in Fishbowl"}
                not_in_fb += 1
                without_msrp += 1
                continue
            if not prices:
                results[sku] = {"msrp": None, "wholesale": None,
                                "product_num": None, "markup": None,
                                "all_products": [], "note": "part exists but no product records"}
                without_msrp += 1
                continue
            best = max(prices, key=lambda r: float(r["price"] or 0))
            self_rows = [r for r in prices if str(r["product_num"]) == sku]
            wholesale = float(self_rows[0]["price"]) if self_rows else None
            msrp = float(best["price"])
            markup = round(msrp / wholesale, 3) if wholesale and wholesale > 0 else None
            results[sku] = {
                "msrp": msrp,
                "wholesale": wholesale,
                "product_num": best["product_num"],
                "markup": markup,
                "all_products": [
                    {"num": r["product_num"], "price": float(r["price"])}
                    for r in prices
                ],
            }
            with_msrp += 1

        OUT.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"[4] Wrote {OUT}")

        print()
        print("=== Summary ===")
        print(f"  BC SKUs total:               {len(skus)}")
        print(f"  With MSRP:                   {with_msrp}")
        print(f"  Without MSRP:                {without_msrp}")
        print(f"    of which not in Fishbowl:  {not_in_fb}")

        # Markup distribution
        markups = [r["markup"] for r in results.values() if r["markup"]]
        if markups:
            markups.sort()
            print()
            print("=== Markup distribution (MSRP / wholesale) ===")
            print(f"  Min:      {markups[0]:.2f}x")
            print(f"  Median:   {markups[len(markups)//2]:.2f}x")
            print(f"  Max:      {markups[-1]:.2f}x")
            print(f"  >= 1.5x:  {sum(1 for m in markups if m >= 1.5)} SKUs")
            print(f"  >= 2.0x:  {sum(1 for m in markups if m >= 2.0)} SKUs")
            print(f"  == 1.0x:  {sum(1 for m in markups if abs(m - 1.0) < 0.01)} SKUs (MSRP = wholesale, no retail markup set)")

    finally:
        fb_logout(token)
        print("[5] Fishbowl seat released")


if __name__ == "__main__":
    main()
