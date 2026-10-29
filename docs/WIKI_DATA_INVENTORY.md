# Wiki data inventory and snapshot sizing

Every piece of GW2 wiki data the addon needs, why it's needed, and how big the committed snapshot gets.

Status: **estimates**. The wiki was down when this was written (2026-10-29), so page counts and sizes come from typical page shapes, not measurement. The first successful build must log real counts and bytes per category (see §6) and this doc gets corrected.

## 1. Rule: wiki only for what the API can't give

Checked feature by feature. These come from the **official API or Hoard & Seek**, so they are *not* cached from the wiki:

| Data | Source |
|---|---|
| Legendary item list, new-legendary detection | `/v2/legendaryarmory` |
| Item names, flags (account bound, no-sell), rarity, type | `/v2/items` |
| Normal discipline recipes | `/v2/recipes` |
| Daily time-gated crafts list | `/v2/dailycrafting` |
| Achievement/collection definitions, bits, rewards | `/v2/achievements` |
| Waypoints, POIs, coordinates, chat codes | `/v2/continents/.../maps` |
| Currencies | `/v2/currencies` |
| Wizard's Vault listings | `/v2/wizardsvault/listings` |
| TP prices, buy orders | live API, never snapshotted |
| Everything about *your* account | Hoard & Seek |
| Icons | `render.guildwars2.com` (API render service) |

**Never cached:** wiki images (would be gigabytes), talk/user pages, page history, rendered HTML.

## 2. What the wiki must supply, by addon feature

| Feature | Wiki data needed |
|---|---|
| Legendary library | Family membership (Gen 1/2/3, armor…) |
| Crafting tree | Mystic Forge recipes, gift recipes, precursor collection chains |
| Cheapest / Fastest routes | Every non-TP source of every material: drops, containers, vendors, reward tracks, events, forge RNG |
| Yield estimates | Container contents + drop rates, drop research pages |
| Deadlines / time gates | Weekly caps, rotations (daily fractals, weekly raid/strike), festival availability windows |
| Today widget & alerts | Meta event schedule (event timer data) |
| Expansion / content filters | Map → expansion, activity → mode (PvE/PvP/WvW) |
| Step guidance | Acquisition text, collection item notes, NPC/event locations (map area → nearest waypoint via API) |

## 3. Categories (Gen 2 weapons, v1)

Avg size = page source (wikitext) as JSON. SMW = Semantic MediaWiki query results (`action=ask`): the wiki builds lists like "Contained in" and "Sold by" from its database, so they are **not** in page source and have to be fetched as query results.

### A. Recipe trees

| # | Category | Pages | Avg KB | MB |
|---|---|---|---|---|
| A1 | Legendary weapon pages | 16 | 15 | 0.24 |
| A2 | Precursor items (3 tiers each) | 48 | 4 | 0.19 |
| A3 | Precursor collection achievement pages (3 per weapon, big tables with locations/notes) | 48 | 35 | 1.68 |
| A4 | Collection item pages (~35 per weapon: acquisition text) | 560 | 3 | 1.68 |
| A5 | Gifts (weapon-specific + shared) | 50 | 5 | 0.25 |
| A6 | Intermediate components (clovers, gemstones, shards, scrolls, sub-gifts) | 80 | 5 | 0.40 |
| A7 | Leaf materials (T6 mats, ecto, coins, map currencies…: acquisition sections) | 120 | 6 | 0.72 |
| A8 | Prerequisite masteries / achievements (e.g. Legendary Crafting mastery, map-completion gifts) | 15 | 20 | 0.30 |
| A9 | Mystic Forge recipe list pages (bulk cross-check) | 4 | 200 | 0.80 |

### B. Locations for guidance

| # | Category | Pages | Avg KB | MB |
|---|---|---|---|---|
| B1 | NPC / vendor pages (location, currency costs) | 200 | 10 | 2.00 |
| B2 | Event / heart pages (where an item or currency drops) | 150 | 8 | 1.20 |

### C. Activities and yields

| # | Category | Pages | Avg KB | MB |
|---|---|---|---|---|
| C1 | Meta events / world bosses (all expansions) | 60 | 40 | 2.40 |
| C2 | Fractal reward pages and chests | 40 | 10 | 0.40 |
| C3 | Raids / strikes: encounter rewards, raid currencies | 50 | 15 | 0.75 |
| C4 | WvW / PvP reward tracks | 100 | 8 | 0.80 |
| C5 | Containers / loot bags / reward chests with contents | 800 | 8 | 6.40 |
| C6 | Home instance / guild hall gathering nodes | 50 | 5 | 0.25 |
| C7 | Map currency pages (where earned, caps) | 30 | 8 | 0.24 |
| C8 | Material promotion / conversion (forge T5→T6, Philosopher's Stones etc.) | 10 | 30 | 0.30 |
| C9 | Drop research pages (drop rates, salvage, forge RNG odds) | 100 | 30 | 3.00 |
| C10 | Festival pages (time-limited sources and their windows) | 10 | 40 | 0.40 |

### D. Schedules and filters

| # | Category | Pages / queries | Avg KB | MB |
|---|---|---|---|---|
| D1 | Event timer data (meta schedule for Today widget and alerts) | 1 | 100 | 0.10 |
| D2 | Rotations: daily fractals, weekly raid/strike emboldened etc. | 5 | 30 | 0.15 |
| D3 | Map → expansion (one SMW query) | 3 | 70 | 0.20 |

### E. SMW query results

| # | Category | Queries | Avg KB | MB |
|---|---|---|---|---|
| E1 | "Contained in" + "Sold by" for ~700 materials and collection items | 1,400 | 5 | 7.00 |
| E2 | All Mystic Forge recipes (bulk SMW recipe query, paged) | 10 | 300 | 3.00 |

E2 replaces fragile per-page `{{Recipe}}` parsing for forge recipes, and the A9 list pages are kept as a cross-check.

### Gen 2 wiki totals

| | Files | Raw MB |
|---|---|---|
| A Recipe trees | 941 | 6.3 |
| B Locations | 350 | 3.2 |
| C Activities | 1,250 | 15.0 |
| D Schedules/filters | 9 | 0.5 |
| E SMW results | 1,410 | 10.0 |
| **Total** | **~3,960** | **~35** |

## 4. API part of the snapshot

| Data | Raw MB | Note |
|---|---|---|
| `/v2/items` for ~3,000 referenced items | 3.0 | batched 200/request |
| `/v2/recipes` for tree | 0.3 | |
| `/v2/achievements` for collections/prereqs | 0.4 | |
| `/v2/continents` map data | 10–20 raw → **0.3** | Store only the extracted waypoint/POI table. Map data is static and always re-fetchable, so keeping the raw floor JSON is waste. |
| **Total** | **~4** | |

## 5. Sizing summary

Low = ×0.5, high = ×2 of mid, to cover estimate error. Git compresses JSON/wikitext roughly 4–5×.

| Scope | Files | Raw (low / mid / high) | In git (mid) |
|---|---|---|---|
| **Gen 2 (v1)** | ~4,000 | 20 / **39** / 80 MB | **~9 MB** |
| + Gen 1, Gen 3, armor, trinkets, backs, runes/sigils, relic | +~4,800 (activities mostly shared) | +14 / **+28** / +56 MB | +6 MB |
| **All families** | ~8,800 | 34 / **67** / 136 MB | **~15 MB** |

Growth (only changed pages are stored again; pages change ~2–5%/week, SMW results ~10%/week):

| Scope | Per week (git) | Per year (git) |
|---|---|---|
| Gen 2 | ~0.4 MB | ~20 MB |
| All families | ~0.8 MB | ~40 MB |

`index.json` (fetch times): ~9,000 entries × ~90 B ≈ 0.8 MB, changes every run but deltas are small.

**Limits:** GitHub hard limit 100 MB per file (largest single file here ≈ 0.3 MB); recommended repo size under 1 GB. All families after 5 years ≈ 15 + 200 = ~215 MB. Comfortable. If it ever gets close, squash old snapshot history into one commit; nothing reads old history.

## 6. Build changes these numbers require

1. **Revision check first.** `prop=revisions&rvprop=ids` takes up to 50 titles per request, so only pages whose revision changed get refetched. Weekly runs then touch ~100 pages, not 4,000.
2. **Batch content.** Fetch changed pages 50 per request (`prop=revisions&rvprop=content&rvslots=main`).
   First build ≈ 2,550 pages / 50 + ~80 batched SMW queries + ~50 API calls ≈ **~180 requests ≈ 3 minutes** at 1 request/second. Unbatched would be ~70 minutes.
3. **Batch SMW queries.** OR-conditions (`[[X::A||B||C]]`) group ~20 items per query: 1,400 lookups → ~70 queries.
4. **Measure.** Each build logs files and bytes per category (A1…E2) to `warnings.txt`, and CI warns above 200 MB.
5. **Store extracts, not dumps,** for static API data (continents).

## 7. Triple-check list

Feature walk (SPEC §§5–8), each needs-wiki item mapped to a category:

- [x] Pick legendary → A1, API
- [x] Tree → A2–A9, E2
- [x] Owned stock → Hoard & Seek (no wiki)
- [x] Cheapest: all non-TP sources → B1, C1–C10, E1
- [x] Fastest: yields per hour → C5, C9 (+ DRF later)
- [x] Balanced → same data as above
- [x] Time-gate floor → API daily crafts, C3/C7 weekly caps, D2 rotations, C10 festivals
- [x] Buy order / instant buy → live API only
- [x] Sell suggestions → live API only
- [x] Daily/weekly/monthly goals → D1, D2
- [x] Today widget / alerts → D1
- [x] Expansion filter → D3 + C1 (activity → map)
- [x] PvE/PvP/WvW toggles → category of activity (C1–C4)
- [x] Guidance text + wiki link → A3, A4, B1, B2
- [x] Nearest waypoint → area from wiki pages, waypoint from API (§4)
- [x] New legendaries → API
- [x] Armor (later) → included in "all families" row
- [x] Outage fallback → this whole snapshot

Source walk (every way GW2 hands out a material): TP (API) · vendor (B1, E1) · crafting (API) · Mystic Forge incl. RNG (E2, C8, C9) · drops & salvage (C9) · containers (C5, E1) · events/hearts (B2) · metas/world bosses (C1) · fractals (C2) · raids/strikes (C3) · WvW/PvP reward tracks (C4) · map currencies (C7) · gathering nodes (C6) · achievements/collections (API rewards, A3) · Wizard's Vault (API) · festivals (C10) · daily crafts (API). No gaps found.
