# Data repo rules

- Public repo: wiki and official-API data only. Never commit [fast] Farming Community data here (private, addon repo only, until the creator agrees). gw2.app data lives only on `gw2app-*` branches and never ships.
- The wiki is reachable only from GitHub Actions; investigate with the probe job (`probe/queries.json`), never by scraping. 1 s per page, 2 s per SMW ask, long backoff on 403/429/5xx, a few builds a day at most; use `[skip ci]` on commits that don't need a build.
- `snapshot/` holds every answer; CI rebuilds offline and fails if the pack differs. Keep it green.
- Builds are dry runs unless published by hand (`publish` input) or `AUTO_PUBLISH=true`.
- Start every session with the addon repo's `docs/HANDOFF.md` (claude.ai project `claude/HANDOFF.md`) and the Data chat brief (`claude/chats/data.md`). Update HANDOFF before ending.
