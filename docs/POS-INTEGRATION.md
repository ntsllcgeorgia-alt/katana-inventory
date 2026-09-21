# Trendar/Comdata SmartStation → Katana integration

How the register at Vado NM feeds sales into Katana. Trendar has no modern
REST API for merchandise, so this is a file-drop bridge — Katana learns about
sales by reading a report SmartStation produces.

## The picture

```
  ┌───────────────────────────────────┐         ┌──────────────────────┐
  │  Vado, NM                         │         │  Weatherford, TX     │
  │                                   │         │  (or anywhere)       │
  │  Register at counter              │         │                      │
  │    │ (scans, prints receipt)      │         │                      │
  │    ▼                              │         │                      │
  │  SmartDESQ / SmartConvenience     │         │  sync_sales_to_katana│
  │    │ (fuel + retail transactions) │         │  .py runs daily      │
  │    ▼                              │         │    │ reads CSV       │
  │  SmartStation back office ────────┼────CSV──┼─►  │                 │
  │  (item file + reports)            │  drop   │    ▼                 │
  │                                   │         │  Katana MRP API      │
  └───────────────────────────────────┘         │  (stock adjustments  │
                                                │   + sales orders)    │
                                                └──────────────────────┘
```

Two moving parts: the **export step** (SmartStation → CSV) and the
**sync step** (CSV → Katana). The sync step is
`scripts/sync_sales_to_katana.py` — built, tested, ready. The export step
is where the site-specific decisions happen.

## The export step — three ways to get the CSV out of SmartStation

Pick one. Ranked by cost and reliability.

### Option A — Scheduled export via SmartStation reports (recommended)

SmartStation's back office has 40+ reports with an export capability
(confirmed on Comdata's product brochure). One of those reports is per-item
sales movement.

1. Log into SmartStation → **Reports** → find the "Item Sales" or "PLU Movement"
   report
2. Configure export: **CSV**, columns = SKU, Quantity, Sale ID/Receipt ID,
   Timestamp, Unit Price
3. Set up a Windows Scheduled Task on the SmartStation PC:
   - Nightly at (say) 2:00 AM
   - Runs the report for "yesterday"
   - Saves to `\\<share>\SmartStation\exports\sales-YYYY-MM-DD.csv`
4. That share is either OneDrive/Dropbox-synced, an SMB share, or SFTP-accessible

The sync script then picks up any new CSV in that folder and processes it.
Fully automated after setup. Human touches nothing.

### Option B — Manual daily export

Same report, same CSV, but exported by hand instead of scheduled. Whoever
closes the store at night runs the report, saves to a OneDrive folder that
Hazem's machine syncs. Sync script picks it up.

Trades reliability (someone forgets) for zero setup cost. Fine for the first
few weeks while getting a feel for the format.

### Option C — Direct DB read (skip SmartStation UI entirely)

SmartStation runs on Windows against a SQL Server backend. If we can get the
DB connection string and read permission on the transactions table, the sync
script can query it directly — no file drop, no scheduled task.

Faster and near-real-time, but requires network access to the SmartStation PC
and cooperation from whoever admins the truck stop's IT.

**Not the default path.** Use only if we have that admin access.

## The CSV format we ask for

Column names are case-insensitive and the parser handles common aliases (SKU
== PartNumber == Item == PLU). This is what SmartStation should be configured
to export:

```csv
sku,quantity,sale_id,sold_at,price
11103,2,RCPT-102938,2026-09-17T14:22:00Z,0.99
11113,1,RCPT-102938,2026-09-17T14:22:00Z,0.99
13529-S,1,RCPT-102941,2026-09-17T15:03:00Z,53.68
```

Rules:
- **`sku`** — must match the SKU in Katana exactly. If SmartStation uses PLUs
  that differ from NTP's part numbers, we need a separate lookup step (not
  built yet — flag it if this is the case).
- **`quantity`** — integer, always positive. Sales, not returns. Returns
  are a Phase 2 feature.
- **`sale_id`** — unique per transaction. Multiple line items on the same
  receipt share a sale_id — the sync groups them into one Katana sales_order
  and one stock_adjustment. This is also the **idempotency key** — re-running
  the sync on the same CSV won't double-post.
- **`sold_at`** — ISO 8601 timestamp (any TZ, we normalize to UTC).
- **`price`** — unit price at time of sale (optional). If missing, we fall
  back to the variant's `sales_price` in Katana.

## What the sync does per sale

For each unique `sale_id`:

1. **Stock adjustment** — one POST to `/stock_adjustments` with negative
   quantities for every line item. That physically decrements the shelf count
   at the Vado location in Katana.
2. **Sales order** (optional, `--customer-id` required) — one POST to
   `/sales_orders` recording the sale for reporting. Not needed for
   inventory to be right, but nice for "how much did we sell this week"
   queries.

Both calls are rate-limited (55/min against Katana's 60/min ceiling) and
retried on transient network failures.

## Where to run the sync

The sync script is stdlib-only Python. It runs on any machine with:
- Python 3.8+ installed
- Read access to the exported CSVs
- Internet access to `api.katanamrp.com`
- A `.env` file with `KATANA_API_KEY`, `KATANA_API_BASE`, and
  `NTS_VADO_LOCATION_ID`

Suggested setups:

- **From Weatherford** — CSVs land in OneDrive at Vado, sync from OneDrive
  on Hazem's machine, hit Katana over internet. Zero infrastructure at Vado.
- **From Vado** — a dedicated small PC at Vado runs the sync locally. Only
  worth it if SmartStation's export lives on a local share not synced to
  OneDrive.

## What's still open

1. **The exact name of the report to run** — "Item Sales", "PLU Movement",
   "SKU Detail" — vendor-specific. A 30-second look at SmartStation's Reports
   menu answers this.
2. **PLU vs SKU** — does SmartStation store the same SKU strings NTP uses in
   BigCommerce, or does it use different PLU codes? If different, we need a
   translation table.
3. **Return handling** — the sync currently treats quantity as positive-only
   (sales). Returns come back as negative quantities — sync would need one-line
   change to accept them.
4. **Multiple registers** — if Vado has more than one register, sales might
   export from each independently. Sync handles it (they all merge into the
   same CSV parse), but the export configuration must include all.

## Command reference

```bash
# Dry-run against a CSV — shows plan, no writes
python scripts/sync_sales_to_katana.py --csv path/to/sales.csv --dry-run

# Live sync, stock adjustments only (no sales_order record)
python scripts/sync_sales_to_katana.py --csv path/to/sales.csv --no-order

# Live sync with sales_order records (needs a Katana customer_id)
python scripts/sync_sales_to_katana.py --csv path/to/sales.csv --customer-id 104539468

# Override location without editing .env
python scripts/sync_sales_to_katana.py --csv sales.csv --location-id 176969
```

Idempotency state lives in `scripts/state/sales_sync_state.json`. Delete it to
force a full re-sync (advanced — will re-decrement everything).
