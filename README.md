# Legendary Crafting Guide — data pack

Public, auto-generated game data for the Legendary Crafting Guide Nexus addon: Mystic Forge recipes, collection steps, guidance text, wiki links, nearest waypoints and (later) activity yields — the parts the official GW2 API does not expose.

Nothing here is hand-maintained. A weekly GitHub Action rebuilds the pack and publishes it:

- `releases/latest` → `pack.json`, `pack.json.sha256` (what the addon downloads)
- `releases/pack-YYYYMMDD-HHMM` → dated history
- An issue is opened when new legendary item IDs appear in `/v2/legendaryarmory`.

## Sources and manners

- Official GW2 API (`api.guildwars2.com`), no key.
- GW2 wiki through the **MediaWiki API** only (`api.php`), never rendered HTML. One request per second, cached between runs, identifying User-Agent.
- Wiki content is © its contributors under the wiki's license; GW2 data © ArenaNet. Fan project, not affiliated with ArenaNet.

## When the wiki or API is down

Every successful response is committed under `snapshot/`. During an outage the builder uses those snapshots (any age), marks the pack with `stale_sources`, and a daily retry rebuilds once sources are back. If a needed page has never been fetched, the build aborts and `latest` stays as it was.

## Local run

```bash
pip install pytest
pytest -q tests
python tools/build_pack.py --out dist --limit 3
```

## Layout

```
config/families.json   legendary families → wiki category (only gen2_weapon enabled)
schema/pack.schema.json pack format v1
tools/build_pack.py    generator
tests/                 parser tests (offline)
```

> First CI run: verify the `wiki_category` names in `config/families.json`. The build fails loudly if a family matches zero legendaries.
