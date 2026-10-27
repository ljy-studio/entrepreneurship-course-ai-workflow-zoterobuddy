---
name: zoterobuddy
description: "Operate the local Zotero Desktop library: search records, manage citations and imports, inspect its taxonomy, and classify items with tags and collections."
---

# Zotero Buddy

Operate the user's local Zotero Desktop library through its local API where supported. Read-only access is preferred. Classification writes require the user's authorization for the particular records and changes. Read [references/initialization-and-classification.md](references/initialization-and-classification.md) for first-use setup and recurring unclassified-item handling.

Core helper:

```text
python <skill-root>/scripts/zotero.py <command>
```

Resolve `<skill-root>` as the directory containing this file. The helper uses only Python's standard library.

## Every invocation

1. Run `status --json` to confirm the actual Zotero version and available interface. Do not rely on a version remembered from an earlier session.
2. Read the collection tree and scan top-level records using configured taxonomy roles. Distinguish subject classification collections from parallel workspaces (e.g. courses); an item may be missing subject classification while already filed in a course workspace. Attachments/notes are not separate bibliographic records. Report anomalous non-literature items separately.
3. Prepare a read-only `classify-preview` for proposed changes. Show destinations, tags to add, current collections, and any stale/invalid rows. Ask permission for the exact batch before writing. A prior permanent SQLite risk authorization does not authorize a new batch or broaden its item scope.
4. For Zotero 10+, use `classify` or `classify-batch` through the local API. Preserve all current tags (including `/unread`), collection memberships, and bibliographic fields; remove a source collection only if requested. Re-read and verify every changed item. Stop on conflicts, denied authorization, or verification mismatches.
5. For Zotero 9.x, offer the user an upgrade first. If they choose to remain on 9.x, explain the SQLite risks and request explicit authorization before any database write. Follow [references/sqlite-legacy-writes.md](references/sqlite-legacy-writes.md) and use the separate `scripts/sqlite_classify.py` helper only after the task-specific permission and risk gate pass. Never infer permission from a previous unrelated task.

## First-use initialization

On first use after installation, follow [references/initialization-and-classification.md](references/initialization-and-classification.md) before normal operation. Store only an explicit authorization-mode preference; do not store library contents, API keys, or plugin secrets in skill settings.

## Existing commands

```text
python <skill-root>/scripts/zotero.py status --json
python <skill-root>/scripts/zotero.py inventory --json
python <skill-root>/scripts/zotero.py collections --json
python <skill-root>/scripts/zotero.py unclassified --json
python <skill-root>/scripts/zotero.py classify-preview --file classifications.json
python <skill-root>/scripts/zotero.py tags --json
python <skill-root>/scripts/zotero.py search "query" --json
python <skill-root>/scripts/zotero.py saved-searches --json
python <skill-root>/scripts/zotero.py saved-search-items SAVEDSEARCHKEY --json
python <skill-root>/scripts/zotero.py export-bibtex --out references.bib
python <skill-root>/scripts/zotero.py classify ITEMKEY --collection-key COLLECTIONKEY --tag "#topic" --yes
python <skill-root>/scripts/zotero.py classify-batch --file classifications.json --yes
python <skill-root>/scripts/zotero.py cite --item-key ITEMKEY --markdown draft.md --bib references.bib --preview
```

For Zotero 10+ local write route details, see [references/local-api-routes.md](references/local-api-routes.md). For Zotero 9.x direct SQLite operation, see [references/sqlite-legacy-writes.md](references/sqlite-legacy-writes.md).

## Output

For classification, report item keys, titles, destination collections, added tags, preserved state, and verification status. For 9.x, also report the backup path and whether Zotero was reopened. Never claim changes succeeded without read-after-write verification.
