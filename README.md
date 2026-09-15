# katana-inventory

Setting up **Katana MRP** as the inventory system for a new retail chrome shop at
National Truck Stop in Vado, NM. Stock lives at the NTP warehouse in Weatherford, TX and
ships to the store on demand.

**Status:** documentation + verified Katana account. Nothing built yet.

## If an AI session is reading this

Read **[`AI-SESSION-STARTUP.md`](AI-SESSION-STARTUP.md)** first. It tells your AI what
to read, in what order, and the absolute rules it needs to follow.

## If a human is reading this

Fast tour:

| File | What it is |
|---|---|
| [`AI-SESSION-STARTUP.md`](AI-SESSION-STARTUP.md) | How to spin up an AI session on this project |
| [`CLAUDE.md`](CLAUDE.md) | Claude Code auto-loads this. Project overview, API details, current Katana state |
| [`docs/PROJECT-BRIEF.md`](docs/PROJECT-BRIEF.md) | Businesses, locations, people, POS landscape, decisions |
| [`docs/INTEGRATION-PLAN.md`](docs/INTEGRATION-PLAN.md) | Architecture, phased build plan, findings |
| [`docs/CREDENTIALS.md`](docs/CREDENTIALS.md) | What credentials the build actually needs |
| [`.env.example`](.env.example) | Variable names only — copy to `.env` locally and fill in |

Read order for humans is the same as for AIs: startup → CLAUDE → BRIEF → PLAN →
CREDENTIALS.

## Getting a local environment

```bash
git clone https://github.com/ntsllcgeorgia-alt/katana-inventory.git
cd katana-inventory
cp .env.example .env
# then paste the Katana API key into KATANA_API_KEY inside .env
```

The Katana API key is **not** in this repo. Get it from Hazem out-of-band, or generate
a new one in Katana → Settings → API.

## Repository conventions

- `.env` is gitignored and never committed. `.env.example` documents variable names only.
- `docs/` holds decisions, findings, and reference material.
- `scripts/` holds one-off imports and utilities. Empty for now — populated once Phase 1
  begins.
- Commits should focus on the *why*, not the *what*. Follow the existing commit style.
