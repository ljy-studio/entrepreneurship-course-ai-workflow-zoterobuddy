# Zotero 9.x SQLite classification writes

This is an explicitly user-authorized, unsupported fallback for adding tags and collection membership to existing top-level bibliographic items. Zotero's own documentation says the database should be accessed read-only: even while Zotero is closed, direct writes bypass Zotero validation and referential-integrity checks, and the schema may change between releases. Corruption, data loss, incorrect UI state, failed sync, lost changes, or sync conflicts are possible. Prefer upgrading to Zotero 10+ and using the local API.

After the user authorizes the exact batch, the helper entry point is:

```text
python <skill-root>/scripts/sqlite_classify.py --file classifications.json --risk-acknowledged --yes
```

Omit `--risk-acknowledged` only when the user previously chose and still has a recorded permanent authorization. The helper itself rejects versions other than Zotero 9.x, checks the configured database schema, closes Zotero gracefully, creates a timestamped backup, uses a transaction, and reopens/reads back the records. If it reports incomplete verification, stop and retain the backup; do not rerun blindly.

## Authorization

Before the first write in a task, state plainly that the proposed route modifies `zotero.sqlite` directly while Zotero is closed, is unsupported by Zotero, and can corrupt the database or interfere with sync. Ask the user to choose one-time authorization (default) or permanent authorization for future Zotero 9.x classification writes.

- One-time authorization applies only to the concrete item batch and changes the user just approved. It expires at the end of that task.
- Permanent authorization may be recorded only when the user explicitly selects it. Store a minimal preference under the skill's private settings, e.g. `{"sqlite_classification_authorization":"permanent","scope":"add #tags and collection memberships for user-approved batches"}`. Never include library data, passwords, API keys, or other secrets.
- Permanent authorization only removes the repeated *SQLite risk* confirmation. Before every later batch, still show the detected unclassified count and proposed mapping and ask the user to authorize that batch. It never authorizes unrelated metadata edits, deletion, imports, sync operations, or taxonomy changes.
- The user can switch back to one-time mode or revoke the preference at any time; update/remove the setting immediately when asked.

## Required procedure

1. Confirm from a fresh `status --json` that the running Zotero major version is 9. Never use this route on Zotero 10+; use its local API.
2. Resolve the configured Zotero data directory from the active profile's `extensions.zotero.dataDir` preference, falling back to the standard profile data directory only if the preference is absent. Confirm that `zotero.sqlite` is inside that directory. Do not guess from a previous machine or profile.
3. Do a read-only preflight while Zotero is open only through the local API. Prepare the exact item keys, destination collection keys, `#` tags, and any explicitly requested source collection removals. Do not perform SQLite reads while Zotero is running.
4. Close Zotero gracefully and wait until every Zotero process exits. Never force-kill it. If it does not close, stop and tell the user; do not touch the database.
5. With Zotero closed, inspect the database schema and SQLite integrity. Continue only if the expected tables and columns for `items`, `itemTags`, `tags`, `collections`, and `collectionItems` are present and consistent. Resolve all keys in the same user library; reject groups, child attachments/notes, missing keys, duplicate keys, or collection/library mismatches. If schema differs from the inspected schema, stop rather than adapting SQL by guesswork.
6. Make a timestamped backup using SQLite's backup API after Zotero has closed and WAL state is settled. Do not copy only the main database while `-wal`/`-shm` files are active. Keep the backup until the user confirms the result.
7. Apply only tag additions and collection membership changes in one SQLite transaction. Use parameterized SQL, preserve every existing tag and collection, and never update bibliographic fields or delete records. Remove a source collection only when the user explicitly requested it. Roll back on any unexpected row count, constraint error, or verification mismatch.
8. Before commit, verify every requested tag and destination membership, check that unrequested tags/collections remain, and run `PRAGMA integrity_check`. Commit only if all checks pass; otherwise roll back. After commit, reopen Zotero normally, re-read the records through its local read API when available, and verify the changes again. Do not trigger sync. Report any uncertainty about sync propagation; direct writes may not be recognized correctly by Zotero's sync machinery.
9. If the app cannot reopen or the database fails verification, do not attempt a second write. Keep the backup and report its exact path so the user can restore it.

Do not run a broad, unreviewed SQL update. Classify only the manifest the user approved, in batches of at most 50, and record which keys were verified. This fallback is not a supported Zotero database API and must never be described as safe or recommended by Zotero.
