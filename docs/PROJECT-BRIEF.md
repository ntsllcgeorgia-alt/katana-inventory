# Project brief — who, what, where

This is the "know the business" document. If an AI session or a new engineer walks in
cold, this file is the single source of truth for the non-technical context that isn't
derivable from the code or the API.

Populated 2026-07-30 with everything captured during the initial planning session.
Sections marked **TODO** are gaps Hazem needs to fill in before serious build work starts.

---

## 1. The businesses

Two separate legal entities. This is confirmed and it changes the technical design (see
Finding 4 in `INTEGRATION-PLAN.md` — replenishment goes through Katana **purchase orders**,
not stock transfers).

### NTP — National Truck Parts (the source)

- **Role:** wholesale warehouse. Holds the master stock of chrome, lights, accessories.
- **Location:** Weatherford, TX
- **Legal name:** TODO
- **Warehouse street address:** TODO
- **Warehouse contact / who runs pick-pack-ship there:** TODO
- **In Katana:** currently represented by the location `"WEATHERFORD "` (with a trailing
  space in the name — needs renaming) at ID `176969`.

### NTS Vado — National Truck Stop, Vado NM (the destination)

- **Role:** retail chrome shop. Sells to truckers who stop for fuel.
- **Location:** Vado, NM
- **Legal name:** TODO
- **Street address:** TODO
- **Store manager / who takes shipments in:** TODO
- **Status:** **not open yet.** No shelf inventory to count. Opening stock = the first
  order shipped from NTP. This is the reason the whole build can be done remotely.
- **In Katana:** does not exist yet. Needs to be created as a second location.

### Tax / entity structure — needs an accountant, not this repo

Because the two are separate entities, replenishment shipments are **interstate sales
between two companies (TX → NM)**. Real invoices, real payables/receivables, real sales
tax question. An accountant needs to confirm intercompany pricing and how resale
certificates / NM nexus are handled before Phase 3 of the build. Katana can execute the
mechanics either way — the accountant decides which way is legal.

---

## 2. Physical locations that matter to the build

| # | Location | Purpose | Katana loc | Notes |
|---|---|---|---|---|
| 1 | NTP warehouse, Weatherford TX | Source of stock | Exists (`"WEATHERFORD "`) | Fix trailing space in name |
| 2 | NTS Vado chrome shop, Vado NM | Retail store | **Missing — create** | Set as primary sales/purchase location for NTS |

Anything else (offices, secondary storage, drop-ship) — TODO if it exists.

---

## 3. Product scope — what the chrome shop actually sells

The chrome shop is retail truck accessories aimed at the driver walking in from the fuel
island. Category examples surfaced during planning: **chrome stacks, lights (LED floods,
markers), mudflaps, oil, and general OTR-driver merchandise**.

**Known specifics from the Katana account:**
- One test SKU exists: `1001 — 4.5" ROUND FLOOD LIGHT - 24 LEDS`, $16.99 retail, cost 0
  (cost must be corrected before margin reporting works).

**TODO before catalog import:**
- SKU count estimate (100? 500? 2,000? drives whether import can be done manually or
  needs a batched script — the 60 req/min rate limit means 2,000 SKUs ≈ 35 min)
- Category list — what taxonomies does Hazem want to filter/report on
- Supplier list — chrome distributors, light brands, oil brands
- UPC / barcode availability — barcodes on the physical product control what POS
  hardware makes sense at the counter

---

## 4. POS landscape

This is where the biggest open questions sit. The truck stop industry runs multiple
systems side-by-side; the answer isn't "one POS."

### 4a. Fuel desk — outside the scope of this build

Truck stops universally run a **fuel-card-authorization system** at the fuel desk:
Trendar / Comdata SmartDESQ (industry incumbent — PDI acquired Comdata Merchant Solutions
in Dec 2024), or occasionally Gilbarco Passport, Wayne, etc. This system authorizes
Comdata / EFS / TCH / T-Chek fleet cards and controls the pumps. **We are not touching
it.** Fuel does not go through Katana.

**TODO:** confirm which fuel-desk system this truck stop actually runs — if it's a
Comdata/Trendar/SmartStation setup with SmartConvenience carrying c-store items, there
may be a report-export path worth exploring before buying a new merchandise POS. If it's
anything else, skip this line of inquiry.

### 4b. Merchandise POS — the actual decision

The chrome shop needs to ring sales, decrement inventory, and feed those sales to
Katana. Two candidate paths:

**Path A: Shopify POS** — recommended default.
- Katana has a **native, no-code integration** with Shopify POS.
- A sale rings on the register → sales order lands in Katana → stock decrements
  automatically at the mapped location.
- Multi-location aware: Vado sales hit Vado stock, not Weatherford.
- Cost: Shopify subscription + POS hardware (iPad or terminal + scanner + drawer +
  printer). Roughly $100/mo software + $500–1,500 one-time hardware.
- **Chosen unless something forces path B.**

**Path B: Use the existing truck-stop c-store POS** (Comdata SmartConvenience or a
third-party like Fis-Cal, Store Chek, Petrosoft).
- Only worth exploring if the truck stop already runs one AND it exports item-level sales
  data to a file we can pick up.
- Integration is custom (file drop, NAXML/SSXML, polling) — no API.
- **TODO check:** does the current system export an item-movement or PLU sales report? A
  30-second look at the back office answers this.

### 4c. Neither system will exist "already integrated" — Katana is the linchpin

Whatever POS is picked at the counter, **Katana is the inventory system of record for
NTS Vado.** The POS's job is to feed sales into Katana; Katana's job is to hold the
truth of what's on the shelf and drive replenishment orders back to NTP.

---

## 5. Who's who

- **Hazem** — owner / decision-maker. Based in Weatherford, TX. Non-technical, prefers
  short answers, prefers "do it" over "explain the prerequisites." Runs both NTP and
  NTS Vado. Also owns TruckPartsPlus (unrelated to this project).
- **NTP warehouse staff** — TODO: names, count, who receives shipping labels, who runs
  the pick sheet. This is the person who will act on replenishment POs.
- **NTS Vado store staff** — TODO: names, count, who mans the counter, who receives
  incoming shipments. This is the person who will do the receiving step (Phase 4) and
  who is trained on whatever POS gets picked. **The whole system's accuracy depends on
  this person.**
- **Accountant** — TODO: needed for the intercompany pricing / interstate sales-tax
  question before Phase 3.
- **The contractor reading this** — whoever Hazem hands the build to next.

---

## 6. What's decided vs. what's open

### Decided (won't be revisited unless there's a strong reason)

- Katana MRP is the inventory system of record.
- Two locations in Katana: NTP warehouse (exists) and NTS Vado (create).
- Replenishment path is **`purchase_orders`**, not `stock_transfers` — separate entities.
- Reorder triggers are three-part: **value OR age OR critical-stockout**, whichever
  fires first (see Finding 3 in INTEGRATION-PLAN.md).
- Comdata's developer API is a **dead end for this project** — payments only, no
  merchandise/item endpoints (see Finding 1 and CREDENTIALS.md).

### Open — needs Hazem or an accountant

- Physical addresses for both locations (this file, section 1)
- Merchandise POS decision at Vado — Shopify POS (path A) or a c-store-native POS (path B)
- If path B: does the existing back office export item-movement data?
- Freight threshold `$X` for replenishment (what dollar amount justifies a truck?)
- Age threshold `N days` for the "any item waiting this long triggers a ship" rule
- Intercompany pricing structure NTP → NTS Vado
- Sales tax handling on TX → NM interstate sales (accountant call)
- Approximate SKU count for the catalog (drives import approach)
- Category list and supplier list

None of these block Phase 1 (Katana setup, catalog loading if we know the SKUs). They
all need to be resolved before Phase 3 (replenishment engine).

---

## 7. Build phases at a glance

Detail lives in `INTEGRATION-PLAN.md`. Summary here so the AI session can orient
without reading the long doc first:

1. **Phase 0 — decisions.** POS, entity/tax, thresholds. In progress.
2. **Phase 1 — Katana foundation.** Rename Weatherford location, add Vado, load catalog
   with real costs, set safety stocks.
3. **Phase 2 — POS ↔ Katana.** Wire up whichever POS wins. Path A = configuration.
   Path B = custom bridge.
4. **Phase 3 — replenishment engine.** Scheduled job: read Vado inventory, apply the
   three triggers, create the PO to NTP.
5. **Phase 4 — receiving loop.** Vado marks incoming shipments received in Katana.
   This step is what most warehouse-to-store setups skip; it's also what silently
   corrupts inventory when skipped.
6. **Phase 5 — monitoring.** Stockout tracking, dead-inventory tracking, freight-per-run
   review.

**Nothing past Phase 0 is built yet.** The repo is documentation and verified account
state.
