# AI session startup — for the contractor picking this up

You (the human) are handing this repo to someone whose first move will be to open it in
Claude Code, Cursor, ChatGPT, or similar. **This file is the thing their AI reads first.**

If you *are* that contractor: welcome. Read this file, then follow the instructions.

---

## In one minute

- The project is **setting up Katana MRP** (inventory / manufacturing ERP) to run a new
  retail chrome shop at a truck stop in Vado, NM. Stock lives at a warehouse in
  Weatherford, TX (NTP) and gets shipped to the store (NTS Vado) on demand.
- **The Katana account is live and the API is verified working.** The catalog is not
  yet loaded. Nothing has been built beyond documentation.
- Design and phased build plan are in `docs/INTEGRATION-PLAN.md`. Business context and
  who-what-where is in `docs/PROJECT-BRIEF.md`.

---

## Read in this order

1. **`README.md`** (repo root) — one-page tour of the repo
2. **`CLAUDE.md`** (repo root) — auto-loaded by Claude Code; project overview, verified
   API details, current Katana account state
3. **`docs/PROJECT-BRIEF.md`** — businesses, locations, people, POS landscape, decisions
4. **`docs/INTEGRATION-PLAN.md`** — architecture, phased build plan, all four findings
5. **`docs/CREDENTIALS.md`** — every credential the build needs, and the ones confirmed
   not to exist

Do not skip PROJECT-BRIEF. Half the meaningful decisions ("separate legal entities → PO
flow, not stock transfer") are business-context calls that don't show up anywhere in the
code or API.

---

## If you're using Claude Code

Open the repo folder in VS Code with the Claude Code extension. `CLAUDE.md` auto-loads.
As a first prompt:

```
Read CLAUDE.md, docs/PROJECT-BRIEF.md, and docs/INTEGRATION-PLAN.md in that order.
Then summarize back to me: what the goal is, what's confirmed, what's open, what
Phase 1 actually consists of, and what's the single most important open decision.
Don't touch the Katana API yet — just read.
```

That summary is the sanity check that the handoff worked. If anything the AI says back
contradicts these docs, stop and figure out why before doing anything else.

---

## If you're using a different AI (ChatGPT, Claude web, etc.)

Paste all four files (`CLAUDE.md`, `docs/PROJECT-BRIEF.md`, `docs/INTEGRATION-PLAN.md`,
`docs/CREDENTIALS.md`) into the conversation, then paste this prompt:

```
I've given you documentation for a Katana MRP inventory project. Read all four files.
Then summarize: goal, confirmed facts, open decisions, what Phase 1 consists of, and
the single most important open decision. Don't offer to write code yet — just prove
you understood the docs.
```

---

## Absolute rules — the AI needs to know these

Put these in your system prompt or the first message. They are non-negotiable:

1. **The Katana API key is not in the repo.** It lives only in `.env` on the build
   machine. `.env` is gitignored. Do not ever write it into a file that isn't
   gitignored, do not paste it into chat, do not commit it. If you need to run against
   the account, either use the key locally in `.env` or generate a new key from
   Katana → Settings → API. Rotate the key when the current build phase ends.

2. **`KATANA_API_KEY` is full-access, long-lived, and un-scoped.** It can delete
   products, orders, and inventory records. Treat it as the master password. On any
   destructive operation (delete, mass update, reset), stop and confirm with the human
   before proceeding.

3. **The Katana rate limit is 60 requests / minute.** Any batch job must throttle.
   Bulk imports of 2,000+ SKUs run for 30+ minutes minimum; account for it.

4. **Nothing production is running yet.** No POS, no customer sales, no shipments. You
   are in a safe environment. That will change once the catalog loads and the store
   opens; the AI should ask "is this live?" before any write operation once we're past
   Phase 1.

5. **Verify claims before recommending.** This project has been through one incorrect
   claim already (see Finding 1 in INTEGRATION-PLAN.md — "Trendar has no SKU catalog"
   was wrong; the earlier session generalized from a marketing page). If something in
   these docs contradicts what you find in Katana or in a real vendor doc, trust the
   live source, flag the discrepancy, and fix the doc.

---

## What the human owner (Hazem) prefers

- Non-technical. Wants results, not explanations of prerequisites.
- Brief answers by default. He'll ask for more if he wants more.
- Do things rather than propose things. If a tool is missing, install it.
- Confirm before destructive or shared-state actions (deletes, pushes to GitHub, sends
  to a warehouse, publishes to a live storefront).
- If a decision needs an accountant or another person, say so plainly — don't guess
  around it.

---

## Where the gaps are

`docs/PROJECT-BRIEF.md` has an explicit **TODO list** — addresses, staff names, SKU
counts, freight threshold, sales-tax treatment. These need to come from Hazem, not from
you. Ask him in one bundled message rather than trickling questions one at a time.

---

## Getting your local environment working

```bash
git clone https://github.com/ntsllcgeorgia-alt/katana-inventory.git
cd katana-inventory
cp .env.example .env      # then paste the Katana API key into KATANA_API_KEY
```

Sanity test (should return HTTP 200 and one product):

```bash
curl -s -o /dev/null -w "HTTP %{http_code}\n" \
  -H "Authorization: Bearer $(grep KATANA_API_KEY .env | cut -d= -f2)" \
  https://api.katanamrp.com/v1/products?limit=1
```

If that returns 200, you're wired up.
