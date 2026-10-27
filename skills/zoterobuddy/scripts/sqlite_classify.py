#!/usr/bin/env python3
"""Explicitly authorized Zotero 9.x tag/collection writes through SQLite.

This is an unsupported fallback. See references/sqlite-legacy-writes.md before use.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import zotero

ZOTERO_EXECUTABLE: str | None = None


def fail(message: str) -> None:
    raise SystemExit(message)


def configured_data_dir() -> Path:
    prefs = zotero.prefs_file()
    if prefs is None:
        fail("Cannot find active Zotero profile prefs.js.")
    text = prefs.read_text(encoding="utf-8", errors="replace")
    match = re.search(r'user_pref\("extensions\.zotero\.dataDir",\s*"((?:[^"\\]|\\.)*)"\);', text)
    if match:
        try:
            raw = json.loads('"' + match.group(1) + '"')
        except json.JSONDecodeError:
            raw = match.group(1)
        path = Path(raw).expanduser()
        if path.is_dir():
            return path
        fail(f"Configured Zotero data directory does not exist: {path}")
    profile = zotero.profile_dir()
    if profile and (profile / "zotero.sqlite").exists():
        return profile
    fail("Could not resolve Zotero's configured data directory; refusing to guess.")


def app_running() -> bool:
    if platform.system() == "Windows":
        result = subprocess.run(["tasklist", "/FI", "IMAGENAME eq zotero.exe", "/FO", "CSV", "/NH"], capture_output=True, text=True)
        return "zotero.exe" in result.stdout.lower()
    name = "Zotero" if platform.system() == "Darwin" else "zotero"
    result = subprocess.run(["pgrep", "-x", name], capture_output=True)
    return result.returncode == 0


def close_gracefully() -> None:
    global ZOTERO_EXECUTABLE
    if not app_running():
        return
    system = platform.system()
    if system == "Windows":
        path_result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", "(Get-Process -Name zotero | Select-Object -First 1 -ExpandProperty Path)"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if path_result.returncode == 0 and path_result.stdout.strip():
            ZOTERO_EXECUTABLE = path_result.stdout.strip()
        script = (
            "$p=Get-Process -Name zotero -ErrorAction SilentlyContinue; "
            "if (-not $p) { exit 0 }; "
            "$ui=$p | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1; "
            "if (-not $ui -or -not $ui.CloseMainWindow()) { exit 2 }; "
            "if (-not $ui.WaitForExit(30000)) { exit 3 }"
        )
        result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script], timeout=35)
        if result.returncode != 0:
            fail("Zotero did not close gracefully; database was not accessed.")
    elif system == "Darwin":
        subprocess.run(["osascript", "-e", 'tell application "Zotero" to quit'], timeout=10, check=False)
    else:
        subprocess.run(["pkill", "-TERM", "-x", "zotero"], timeout=10, check=False)
    for _ in range(60):
        if not app_running():
            time.sleep(1)
            return
        time.sleep(0.5)
    fail("Zotero is still running; database was not accessed.")


def start_zotero_and_wait() -> bool:
    try:
        if platform.system() == "Windows":
            subprocess.Popen([ZOTERO_EXECUTABLE or "zotero.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", "-a", "Zotero"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["zotero"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return False
    for _ in range(60):
        if zotero.request("/api/", timeout=1).ok:
            return True
        time.sleep(0.5)
    return False


def settings_path() -> Path:
    root = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    return root / "skills" / "zoterobuddy" / "user-settings.json"


def permanent_consent() -> bool:
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return data.get("sqlite_classification_authorization") == "permanent"


def columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}


def read_manifest(path: str) -> list[dict[str, Any]]:
    try:
        rows = json.loads(Path(path).expanduser().read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"Could not read batch manifest: {exc}")
    if not isinstance(rows, list) or not rows or len(rows) > 50:
        fail("Manifest must be a non-empty JSON array with at most 50 rows.")
    keys: set[str] = set()
    for i, row in enumerate(rows, 1):
        if not isinstance(row, dict) or not all(isinstance(row.get(k), str) and row[k] for k in ("item_key", "collection_key")):
            fail(f"Manifest row {i} needs item_key and collection_key.")
        if row["item_key"] in keys:
            fail(f"Duplicate item key at manifest row {i}.")
        keys.add(row["item_key"])
        tags = row.get("tags")
        if not isinstance(tags, list) or not tags or any(not isinstance(t, str) or not t.startswith("#") for t in tags):
            fail(f"Manifest row {i} must have non-empty #tags.")
        source = row.get("remove_collection_key")
        if source and source == row["collection_key"]:
            fail(f"Manifest row {i} source and destination are identical.")
        expected = row.get("expected_collection_keys")
        if expected is not None and (not isinstance(expected, list) or any(not isinstance(x, str) for x in expected)):
            fail(f"Manifest row {i} expected_collection_keys must be a string array.")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Unsupported Zotero 9.x SQLite classification fallback")
    parser.add_argument("--file", required=True)
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--risk-acknowledged", action="store_true", help="User explicitly authorized this task's SQLite risks")
    args = parser.parse_args()
    status = zotero.status_payload()
    major = zotero.zotero_major_version(status.get("zotero_version"))
    if major is None:
        fail("Cannot determine the running Zotero version; refusing SQLite writes.")
    if major != 9:
        fail("This fallback is restricted to Zotero 9.x. Use the local API on 10+; other versions are unsupported.")
    if not args.yes:
        fail("Pass --yes only after the user approved this exact classification batch.")
    if not args.risk_acknowledged and not permanent_consent():
        fail("SQLite risk authorization is required. Ask the user, then pass --risk-acknowledged for one-time consent.")
    rows = read_manifest(args.file)
    close_gracefully()
    db_path = configured_data_dir() / "zotero.sqlite"
    if not db_path.is_file():
        fail(f"No zotero.sqlite at configured data directory: {db_path}")
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    backup_path: Path | None = None
    changed: list[dict[str, Any]] = []
    try:
        required = {
            "items": {"itemID", "key", "libraryID"},
            "itemTags": {"itemID", "tagID", "type"},
            "tags": {"tagID", "name", "nameNormalized"},
            "collections": {"collectionID", "key", "libraryID"},
            "collectionItems": {"collectionID", "itemID", "orderIndex"},
            "libraries": {"libraryID", "type"},
            "itemAttachments": {"itemID", "parentItemID"},
            "itemNotes": {"itemID", "parentItemID"},
        }
        for table, expected in required.items():
            if not expected.issubset(columns(conn, table)):
                fail(f"Database schema differs from the supported signature in table {table}; no changes made.")
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            fail("Database integrity check failed; no changes made.")
        backup_path = db_path.with_name(f"zotero.sqlite.zoterobuddy-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.bak")
        backup = sqlite3.connect(str(backup_path))
        conn.backup(backup)
        backup.close()
        conn.execute("BEGIN IMMEDIATE")
        for row in rows:
            item = conn.execute("SELECT itemID,libraryID FROM items WHERE key=?", (row["item_key"],)).fetchone()
            if item is None:
                fail(f"Missing item key {row['item_key']}; transaction rolled back.")
            item_id, library_id = item["itemID"], item["libraryID"]
            child = conn.execute("SELECT 1 FROM itemAttachments WHERE itemID=? AND parentItemID IS NOT NULL UNION SELECT 1 FROM itemNotes WHERE itemID=? AND parentItemID IS NOT NULL", (item_id, item_id)).fetchone()
            if child:
                fail(f"Key {row['item_key']} is a child item; transaction rolled back.")
            lib = conn.execute("SELECT type FROM libraries WHERE libraryID=?", (library_id,)).fetchone()
            if lib is None or lib["type"] != "user":
                fail(f"Key {row['item_key']} is not in the user library; transaction rolled back.")
            current = {x[0] for x in conn.execute("SELECT c.key FROM collectionItems ci JOIN collections c USING(collectionID) WHERE ci.itemID=?", (item_id,))}
            expected = row.get("expected_collection_keys")
            if expected is not None and current != set(expected):
                fail(f"Collection membership changed for {row['item_key']}; transaction rolled back.")
            target = conn.execute("SELECT collectionID,libraryID FROM collections WHERE key=?", (row["collection_key"],)).fetchone()
            if target is None or target["libraryID"] != library_id:
                fail(f"Destination mismatch for {row['item_key']}; transaction rolled back.")
            source_key = row.get("remove_collection_key")
            if source_key:
                source = conn.execute("SELECT collectionID,libraryID FROM collections WHERE key=?", (source_key,)).fetchone()
                if source is None or source["libraryID"] != library_id:
                    fail(f"Source mismatch for {row['item_key']}; transaction rolled back.")
                conn.execute("DELETE FROM collectionItems WHERE itemID=? AND collectionID=?", (item_id, source["collectionID"]))
            conn.execute("INSERT OR IGNORE INTO collectionItems(collectionID,itemID) VALUES(?,?)", (target["collectionID"], item_id))
            for tag in dict.fromkeys(row["tags"]):
                normalized = "".join(ch for ch in unicodedata.normalize("NFKD", tag).casefold() if not unicodedata.combining(ch))
                conn.execute("INSERT OR IGNORE INTO tags(name,nameNormalized) VALUES(?,?)", (tag, normalized))
                tag_id = conn.execute("SELECT tagID FROM tags WHERE name=? OR nameNormalized=? ORDER BY CASE WHEN name=? THEN 0 ELSE 1 END LIMIT 1", (tag, normalized, tag)).fetchone()[0]
                conn.execute("INSERT OR IGNORE INTO itemTags(itemID,tagID,type) VALUES(?,?,0)", (item_id, tag_id))
            changed.append({"item_key": row["item_key"], "collection_key": row["collection_key"], "tags": row["tags"]})
        for record in changed:
            item_id = conn.execute("SELECT itemID FROM items WHERE key=?", (record["item_key"],)).fetchone()[0]
            membership = conn.execute("SELECT 1 FROM collectionItems ci JOIN collections c USING(collectionID) WHERE ci.itemID=? AND c.key=?", (item_id, record["collection_key"])).fetchone()
            actual_tags = {x[0] for x in conn.execute("SELECT t.name FROM itemTags it JOIN tags t USING(tagID) WHERE it.itemID=?", (item_id,))}
            if not membership or not set(record["tags"]).issubset(actual_tags):
                fail(f"Pre-commit verification failed for {record['item_key']}; transaction rolled back.")
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            fail("Pre-commit integrity check failed; transaction rolled back.")
        conn.commit()
    except BaseException:
        conn.rollback()
        conn.close()
        raise
    conn.close()
    reopened = start_zotero_and_wait()
    verification_errors: list[str] = []
    if reopened:
        for record in changed:
            item = zotero.api_get(f"{zotero.LOCAL_USER}/items/{record['item_key']}").get("data", {})
            actual_tags = {entry.get("tag") for entry in item.get("tags", []) if isinstance(entry, dict)}
            if record["collection_key"] not in item.get("collections", []) or not set(record["tags"]).issubset(actual_tags):
                verification_errors.append(record["item_key"])
    print(json.dumps({"status": "verified" if reopened and not verification_errors else "database_updated_verification_incomplete", "backup": str(backup_path), "items": changed, "verification_errors": verification_errors, "zotero_reopened": reopened, "sync_warning": "Direct SQLite writes are unsupported; Zotero sync may not propagate these changes correctly."}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
