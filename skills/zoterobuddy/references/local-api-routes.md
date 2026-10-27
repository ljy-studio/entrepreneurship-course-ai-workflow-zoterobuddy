# Zotero local API and connector routes

Base URL: `http://127.0.0.1:23119`.

## Desktop local API

The local API is under `/api/`. It implements Zotero Web API v3 for the local logged-in desktop user.

Important constraints:

- Use `/api/users/0/...` for the local user by default.
- Local API reads do not require an API key.
- Reads are available on supported desktop versions. Local write authorization and item/collection write methods require Zotero 10+.
- Atom output is not supported locally.
- Attachment file URLs and full text can expose local file paths or document text; only retrieve them when the user asks.

Safe read routes:

```text
/api/
/api/schema
/api/itemTypes
/api/itemFields
/api/itemTypeFields?itemType=journalArticle
/api/itemTypeCreatorTypes?itemType=journalArticle
/api/creatorFields
/api/users/0/collections
/api/users/0/collections/top
/api/users/0/items
/api/users/0/items/top
/api/users/0/items/trash
/api/users/0/items/<itemKey>
/api/users/0/items/<itemKey>/children
/api/users/0/items?format=keys
/api/users/0/items?format=versions
/api/users/0/items?format=bibtex
/api/users/0/items?include=data,citation&style=apa
/api/users/0/items?q=<query>
/api/users/0/tags
/api/users/0/searches
/api/users/0/searches/<searchKey>/items
/api/users/0/groups
/api/users/0/fulltext?since=0
/api/users/0/items/<attachmentKey>/fulltext
/api/users/0/items/<attachmentKey>/file/view/url
```

For Zotero 10+ local writes:

1. Read `/api/` and capture `Zotero-Server-ID` from the response.
2. If no cached remembered key is available, request authorization with `POST /api/local/authorize`, JSON `{"appName":"Zotero Buddy"}`, and the `Zotero-Server-ID` header. Honor the user's choice. If Zotero returns `remember: true` after **Always Allow**, securely cache and reuse that key; do not call the authorize endpoint before every write. **Allow** returns a single-use key, so a later write needs a new authorization.
3. For one item, send `PATCH /api/users/0/items/<itemKey>`; for multiple items (up to 50), send `POST /api/users/0/items` with an array of keyed, versioned item objects. Include `Zotero-Server-ID` and `Zotero-API-Key`. For tag or collection changes, send the complete intended `tags` and `collections` arrays; arrays replace the existing lists, so read/merge first to preserve existing values.
4. Re-read the item and verify the requested changes. A `412` version conflict or mismatch is a stop condition; do not blindly repeat the write.

Single-use authorization keys are used only for the current write and are not cached. Remembered keys are stored in the Codex user's private secrets directory; on Windows they are protected with DPAPI for the current Windows user, and on POSIX systems the cache file is owner-readable only. They are never printed. A remembered local key is not scoped to a collection and allows writes to any library the Zotero user can edit; it can be revoked in Zotero Settings → Advanced → Clear Write Authorizations. Zotero 9.x does not support these local write routes. If the user chooses to remain on Zotero 9.x, the skill may use its explicitly authorized, unsupported SQLite classification fallback. Read [sqlite-legacy-writes.md](sqlite-legacy-writes.md) and obtain the required one-time or permanent risk authorization before using it. Zotero recommends SQLite access be read-only, including when Zotero is closed, because direct modifications bypass its validation and integrity checks.

## Connector server

The Zotero Connector server shares port `23119` and is used for desktop writes/imports.

The Connector route used by this helper is:

```text
GET  /connector/ping
POST /connector/ping
POST /connector/getSelectedCollection
POST /connector/import?session=<uuid>
```

Use `/connector/import` only for importing BibTeX/RIS strings into the currently selected Zotero library or collection. It is not the classification/update path for existing records. Treat connector imports as Zotero library modifications and require explicit user intent.
