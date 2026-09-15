# NTS Vado chrome shop — inventory & auto-replenishment plan

Drafted 2026-07-30. Status: **plan only, nothing built.** Two blocking questions at the
bottom must be answered before any code is written.

## The goal in one line

Ring a sale at the Vado chrome shop → Katana drops the stock → items accumulate on a
replenishment list → when the list is worth enough to justify a truck, it becomes a pick
order at the NTP warehouse → warehouse ships to Vado.

Verdict: **the concept is sound and it's a normal retail replenishment pattern.** But it
does not work the way it's currently framed, for one specific reason, and the dollar-only
trigger has a flaw. Both are fixable. Details below.

---

## Finding 1 — CORRECTED 2026-07-30. Trendar does carry items. The blocker is narrower.

**A previous draft of this document claimed Trendar has no SKU catalog and produces only
department totals. That was wrong** — it generalized from a reseller page describing a
fuel-desk-only tier. Hazem has the system in front of him and reports the back office
manages merchandise (grocery, drinks, chrome, oil). Independent evidence agrees:

- **Trendar/SmartDESQ carries a price book with items.** Petrosoft's CStoreOffice pushes
  "departments, promotions, **items**" and price modifiers down to a Comdata/SmartDESQ
  register, exchanged as **NAXML / SSXML**.
- **Multiple back offices ship a Trendar interface.** ApexBOS MyPriceBook is "an approved
  interface partner" for "Trendar, SmartDESQ"; SSCS interfaces Comdata SmartDESQ via
  CS-Poll; CSA Horizon sells a dedicated Trendar interface module.
- **"Secondary POS" does not mean "no items."** PDI's checklist does say Essentials
  supports Trendar only as a Secondary POS — but the same document says "item changes sent
  from PDI Essentials are assigned the correct Department and Tax Type on both the Primary
  and **Secondary** POS." Secondary means it sits alongside a primary register, not that it
  is item-blind.
- Context: **PDI acquired Comdata Merchant Solutions in Dec 2024**, including the POS
  hardware and software. Trendar's roadmap is PDI's now.

### What IS confirmed dead: the Comdata API path

Comdata's developer portal (resourcecenter.comdata.com) documents **only payment services** —
API Developer Portal (account info, ordering/blocking cards, vehicles), Web Services
1.0/2.0/2.1, MCWS, PropWS, VEWS, VCWS. **Zero merchandise, PLU, SKU, inventory, or
item-movement APIs.** So there is no REST API path to store item data. Anything we get
comes through the back-office price-book/polling interface (file-based, NAXML/SSXML), not
a modern API.

### The system in front of Hazem: Comdata Smart Solutions → SmartStation

Confirmed 2026-07-30. The suite is **Comdata Smart Solutions**; the back office module is
**SmartStation**. Comdata's own product brochure lists SmartStation as doing:

> › Monitors transaction activity and cashier behavior in real time
> › **Accesses more than 40 reports, with the ability to export data for analysis**
> › **Manages product set-up** and user-level profiles
> › Customizes payment methods and direct bill accounts

Two things that matter land right there: **product set-up exists** (so the back office does
manage merchandise — Hazem was right) and **report export exists** (so we can get data out
without an API). Register-side products are SmartDESQ (fuel desk) and SmartConvenience
(store, "processing of both fuel and nonfuel transactions").

### The one thing still unknown — and it decides the architecture

Whether one of those 40+ reports is **item-level movement** — units sold per SKU — or only
category totals. The caution flag: every register in the line is described as offering
"configurable **product categories**," which is category-level language. Petrosoft's
documented return path from a Comdata register is likewise **shift report data**, not
explicitly per-item sales.

If an item-movement report exists and exports → **we build on the existing system, no new
POS.** If reports stop at category totals → the chrome shop needs its own retail POS
(Shopify POS being the low-code path, per Finding 2).

This is a 30-second look at the screen, not more research. See blocking question 1.

## Finding 2 — the shortcut that removes most of the custom work

Katana has a **native Shopify integration, including Shopify POS**, with multi-location
mapping. Out of the box:

- A Shopify POS sale imports into Katana as a sales order automatically.
- "Stock levels in Katana are reduced immediately for relevant products."
- Retail locations map to the correct Katana location, so Vado sales hit Vado stock.

That is step 1 → step 2 of your flow, already built, no code. If you pick Shopify POS for
the chrome shop counter, the only thing left to build is the replenishment trigger.

The alternative — a truck-stop c-store POS (Fis-Cal, Store Chek, PDI, Gilbarco) — also does
item-level inventory, but connects by file drop and would need a custom bridge to Katana.
More money, more moving parts, and it only makes sense if the truck stop's existing systems
force your hand.

## Finding 3 — the dollar threshold alone will starve your fast movers

Your rule as stated: accumulate need, ship when the order hits $X.

The problem: the trigger is global but demand is per-SKU. If chrome stacks sell every week
but the overall list crawls toward $2,500 because nothing else moves, the stacks sit at zero
for a month waiting on slow items to justify the truck. You lose the sales you were most
sure of.

**Fix — trigger on whichever fires first:**

1. **Value:** replenishment list ≥ $X (your freight-efficiency number)
2. **Age:** any item has been waiting ≥ N days (stops slow accumulation from starving fast movers)
3. **Critical:** any A-item (top ~20% of revenue) is at or below zero — ship now regardless

Rule 1 is your economics. Rule 2 and 3 are what keep the shelf full. All three are a few
lines of logic — the cost of getting this wrong is far higher than the cost of the rule.

## Finding 4 — ANSWERED 2026-07-30: separate companies → **purchase order**

Hazem confirmed NTP warehouse and National Truck Stop Vado are **separate legal entities**.
That settles it: replenishment is a **`purchase_orders`** flow, not `stock_transfers`.
NTS Vado buys from NTP warehouse.

What that decision drags in — none of it optional, all of it before Phase 3:

- **Intercompany pricing.** NTP must sell to NTS at a defined price. That price becomes NTS's
  cost basis and drives every margin number at the Vado counter. Cost cannot be 0.
- **Real invoices.** Each replenishment creates an actual payable/receivable between two
  companies. This is bookkeeping, not just a stock movement.
- **TX → NM interstate sales tax.** A genuine interstate sale between two entities. Resale
  certificate handling and NM nexus are accountant questions.
- **Both sides in Katana.** NTP ships (sales side) and NTS receives (purchase side). If both
  entities live in one Katana account, the PO and its matching sales order need to reconcile.

**Action: this now needs the accountant before Phase 3 is built**, not before Phase 1. The
Katana foundation work (locations, catalog, costs) is unaffected and can proceed.

Original comparison retained for reference:

| | Stock Transfer | Purchase Order |
|---|---|---|
| Use when | NTP warehouse and NTS Vado are the **same legal entity** | They are **separate companies** |
| Katana object | `stock_transfers` | `purchase_orders` |
| Effect | Moves stock between locations, cost carries over | NTS buys from NTP; creates a real payable/receivable |
| Also needs | nothing | intercompany pricing, invoicing, NM sales-tax treatment |

TX → NM is an interstate move, so if they're separate entities this has real tax
consequences. **Ask your accountant before we build.** Both are one API call; picking the
wrong one creates a year of cleanup.

---

## Proposed architecture

```
  Chrome shop counter (Vado, NM)
    Retail POS w/ barcode scanning  ──native──┐
                                              │
  Fuel desk: Trendar/Comdata                  │   (fuel only — not connected,
    stays exactly as it is                    │    and that is correct)
                                              ▼
                                      ┌──────────────────┐
                                      │      KATANA      │
                                      │  Loc A: NTP whse │
                                      │  Loc B: NTS Vado │
                                      └────────┬─────────┘
                                               │ sale decrements Vado stock
                                               ▼
                                  replenishment engine (nightly)
                                   below safety stock at Vado?
                                               │
                                    value ≥ $X  OR  aged ≥ N days
                                     OR A-item stocked out
                                               │
                                               ▼
                                  Stock Transfer / Purchase Order
                                               │
                                               ▼
                                 NTP warehouse pick list → ship → Vado
                                               │
                                        receive at Vado
                                     (stock lands, cycle closes)
```

## Build phases

**Phase 0 — decisions (no code).** POS choice. Entity structure → transfer vs PO. Freight
threshold $X and age N. Who at Vado receives shipments.

**Phase 1 — Katana foundation.** Second location for Vado (one exists: "WEATHERFORD ", note
the trailing space — fix it). Load the chrome catalog: SKU, barcode, purchase price (cost),
sales price, supplier. **Purchase price must be loaded** — the existing test item has cost 0,
which makes margin reporting useless. Set safety stock per SKU at Vado.

**Phase 2 — POS ↔ Katana.** If Shopify POS: connect the native integration, map the Vado
retail location, verify a test sale decrements Vado and not Weatherford. If another POS:
build the file-drop bridge (bigger job — scope separately).

**Phase 3 — replenishment engine.** A scheduled script: read inventory at Vado, compare to
safety stock, build the need list, apply the three triggers, create the transfer/PO, notify
the warehouse. Respect the **60 req/min** rate limit.

**Phase 4 — receiving loop.** Warehouse ships → Vado receives in Katana → stock lands.
Without this the numbers drift within weeks. This step is the one most often skipped and
it's the one that kills the system.

**Phase 5 — watch it.** Weekly: stockouts, items that never move (dead capital), whether
$X is producing sensible freight.

---

## Can this be built remotely?

### CORRECTED 2026-07-30 — there is no greenfield. Both stores need a count.

**An earlier draft of this section assumed the Vado chrome shop had not opened and that
opening stock would simply be the first truck from the warehouse. That was wrong.** Hazem
confirmed: **both Amarillo and Vado have been open ~10 years, both hold existing chrome
stock, and neither has ever had a real count system.**

Consequence, stated plainly: **the free opening count does not exist.** Auto-replenishment
cannot run on unknown starting quantities — if Katana believes there are 12 and the shelf
holds 2, it won't reorder and the shelf goes empty. No amount of code fixes an unknown
starting number. **Somebody counts, once, per item we intend to manage.**

### What keeps this manageable: scope, not scale

The count is **chrome only** — the scope of this entire project. Grocery, drinks, oil and
snacks are untouched and stay uncounted. A few hundred chrome SKUs is one or two people for
a day. Not a store shutdown, and it happens exactly once *provided* receiving and cycle
counts are then kept up (see Phase 4 — this is where these systems actually die).

### Making the count cheap

Do **not** hand anyone a clipboard and a blank page. SmartStation already holds the price
book — item names and barcodes. Export the **product list** and it becomes the count sheet,
pre-filled, in shelf order.

Worth separating two different exports, because they have different odds of existing:

| Export | Odds | Value if present |
|---|---|---|
| **Product/price book list** | High — "manages product set-up" is a documented SmartStation feature | Count sheet **and** the Katana catalog load. Saves days of typing. |
| **Item sales / movement** | Unconfirmed — the open question | The ongoing sales feed. Decides file-bridge vs new POS. |

**Even if item movement does not export, the product list almost certainly does — and that
alone is worth having.** It is the difference between typing several hundred SKUs into
Katana by hand and importing them.

### Sequencing: do the first count where Hazem can stand in it

The earlier "Vado first because the count is free" logic is void. New logic: **the first
count sets the system's credibility, and remote-supervising a first count in another state
is how it ends up wrong.** Pilot at whichever store he can physically supervise.

Also treat the count as a one-time audit: after 10 years with no counting, expect dead
stock — chrome that has not moved in years and is tying up capital. Flag it while counting.

Fully remote:
- Katana setup, locations, catalog, costs, safety stock
- POS selection, configuration, catalog load
- The integration and replenishment engine
- Reporting and thresholds

Needs hands at Vado — **but not necessarily yours**:
- POS hardware install (counter, scanner, drawer, network) — one-time, vendor or staff
- Confirming the first shipment matches the packing list — one person, one afternoon
- Receiving future shipments into Katana — ongoing staff task, ~2 min per shipment
- Cycle counts (rolling section counts, not a full shutdown count) — staff, weekly

The honest caveat: **the whole system's accuracy depends on staff at Vado doing receiving
and counts consistently.** You can build every line of this from Weatherford. You cannot
remotely make someone scan a box in. Budget real training time for whoever runs that
counter — that's the actual risk here, not the software.

One thing worth a site visit at some point: internet reliability at the store. A cloud POS
on bad connectivity is a genuinely painful problem, and it's cheaper to learn now.

---

## A note on Katana itself

Katana is a manufacturing MRP. You're using it as multi-location retail distribution, so
some of what you're paying for (production orders, BOMs, operations) will go unused. It
does work — the multi-location inventory, PO/transfer engine, and API are all solid and
verified working on your account. Just know you're buying a factory tool for a shop. If the
chrome shop is the only use case, it's worth a look at whether Shopify's own multi-location
inventory plus a small script covers it. If NTP warehouse operations are going into Katana
too, it earns its keep.

---

## Blocking questions

1. **OPEN — the only true blocker left.** In SmartStation, is there a report showing units
   sold **per item** (not per category), and does it export to a file? Open SmartStation →
   Reports, and look for anything named *Item Sales*, *PLU*, *Product Movement*, or
   *Merchandise Sales*. Check for an Export / Download button. Brochure says 40+ reports with
   export, and that product set-up exists — this confirms whether item-level movement is
   among them.
   - **Yes, exports** → build on the existing system. No new POS.
   - **On screen only** → workable; extraction needs a different route.
   - **Categories only** → chrome shop needs its own retail POS (see Finding 2).

2. ~~Same legal entity?~~ **ANSWERED: separate companies → purchase order.** See Finding 4.
   Accountant needed on intercompany pricing and TX→NM tax before Phase 3.

Nice-to-know: rough SKU count for the chrome shop, and your freight-efficiency number for $X.
