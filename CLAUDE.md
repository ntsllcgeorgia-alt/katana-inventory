# katana-inventory

Katana MRP (cloud inventory / manufacturing ERP, katanamrp.com) set up for Hazem's
truck-parts businesses.

## What this project is

Configure Katana as the single source of truth for parts inventory, then wire its API
into the existing stack (TruckPartsPlus / NationalTruckParts).

Started: 2026-07-30.

## Status

- [x] Katana account + API key obtained
- [x] API connection verified 2026-07-30 — `GET /products` returns HTTP 200
- [ ] Product / part catalog imported
- [ ] Stock levels seeded
- [ ] Sync direction decided (Katana -> storefront, storefront -> Katana, or both)
- [ ] Integration written

## Account state as of 2026-07-30 (verified, read-only)

The account is effectively empty — one test walkthrough, no real catalog. Nothing to
migrate or preserve; treat it as a clean slate.

| Entity          | Count | Detail                                                      |
|-----------------|-------|-------------------------------------------------------------|
| locations       | 1     | "WEATHERFORD " (trailing space in the name), primary, created 2025-12-29 |
| products        | 1     | `4.5" ROUND FLOOD LIGHT - 24 LEDS`, category "Utility Lights", id 16212609 |
| variants        | 1     | SKU `1001`, sales price 16.99, purchase price 0, id 38110266 |
| inventory       | 1     | 71 in stock, avg cost 7.85, value 557.35, reorder point 0    |
| customers       | 1     | "Test" — Mazen Abdeljalil / national Truck parts             |
| sales_orders    | 1     | SO-1, $16.99, NOT_SHIPPED, notInvoiced                       |
| materials       | 0     |                                                              |
| suppliers       | 0     |                                                              |
| purchase_orders | 0     |                                                              |

Oddities worth confirming before import:
- `quantity_missing_or_excess` is 70 on the one inventory row — stock was likely typed in
  directly rather than received via a PO. Decide how stock gets in before bulk loading.
- Purchase price is 0, so margin/COGS reporting won't work until costs are loaded.
- The location name has a trailing space. Fix it before anything keys off the name.

## API notes (verified 2026-07-30)

- Base URL: `https://api.katanamrp.com/v1` — confirmed working
- Auth: `Authorization: Bearer $KATANA_API_KEY` — confirmed working
- Responses are `{"data": [...]}`. Pagination is `?limit=N&page=N`; page beyond the end
  returns an empty `data` array.
- Total counts come back in the `X-Pagination` **response header**, not the body:
  `{"total_records":"1","total_pages":"1","offset":"0","page":"1",...}`. Read the header
  instead of paging to count.
- **Rate limit: 60 requests/minute** (`X-RateLimit-Limit: 60`, with `-Remaining` and
  `-Reset` headers). Any bulk import must throttle — a 2,000-SKU load is ~35 min minimum.
- Verified present and working on this account: `webhooks`, `stock_transfers`,
  `stock_adjustments`, `inventory_movements`, `purchase_orders`, `variants`, `inventory`.
- Full docs: https://developer.katanamrp.com/

## Credentials

Never commit keys. `.env` is gitignored; `.env.example` lists the variable names.

Katana API key is generated in the Katana web app under Settings -> API.
Store it as `KATANA_API_KEY` in `.env`.

## Working notes

Decisions and gotchas go in `docs/`. Throwaway scripts go in `scripts/`.
