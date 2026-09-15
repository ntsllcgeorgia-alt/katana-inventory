# katana-inventory

Setting up Katana MRP as the inventory system for TruckPartsPlus / NationalTruckParts.

## Start here (next session)

1. Open this folder in VS Code.
2. `cp .env.example .env` and paste the Katana API key into `KATANA_API_KEY`.
3. Ask Claude to verify the connection — it will make one authenticated call and
   report what the account actually contains before anything gets built.

## Layout

| Path       | What's in it                                    |
|------------|-------------------------------------------------|
| `CLAUDE.md`| Project context Claude loads automatically      |
| `docs/`    | Decisions, field mappings, gotchas              |
| `scripts/` | One-off imports, exports, sync scripts          |
| `.env`     | Real keys (gitignored)                          |
