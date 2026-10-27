#!/usr/bin/env python3
"""Operate Zotero Desktop's local API and connector server.

This helper is dependency-free on purpose: it runs with Python 3 stdlib only,
and all Zotero calls go through the local Desktop HTTP surfaces on
http://127.0.0.1:23119.
"""

from __future__ import annotations

import argparse
import configparser
import ctypes
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = os.environ.get("ZOTERO_LOCAL_BASE_URL", "http://127.0.0.1:23119")
LOCAL_API_PREF = "extensions.zotero.httpServer.localAPI.enabled"
LOCAL_USER = "/api/users/0"
API_VERSION_HEADERS = {"Zotero-API-Version": "3"}
CONNECTOR_HEADERS = {"X-Zotero-Connector-API-Version": "3"}
TEXT_LIMIT = 300
API_PAGE_LIMIT = 100


@dataclass(frozen=True)
class Response:
    status: int | None
    headers: dict[str, str]
    text: str
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is not None and 200 <= self.status < 300

    @property
    def content_type(self) -> str:
        return self.headers.get("Content-Type", "")


def dump_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=False))


def exit_with(message: str) -> None:
    raise SystemExit(message)


def zotero_roots() -> list[Path]:
    home = Path.home()
    system = platform.system()
    roots: list[Path] = []

    if system == "Darwin":
        roots.append(home / "Library/Application Support/Zotero")
    elif system == "Windows":
        appdata = os.environ.get("APPDATA")
        if appdata:
            roots.extend([Path(appdata) / "Zotero/Zotero", Path(appdata) / "Zotero"])
    else:
        roots.extend(
            [
                home / ".zotero/zotero",
                home / ".var/app/org.zotero.Zotero/data/zotero",
            ]
        )

    # Useful fallback when scripts run under shells whose platform config is odd.
    roots.append(home / "Library/Application Support/Zotero")
    return list(dict.fromkeys(roots))


def profiles_ini_path() -> Path | None:
    for root in zotero_roots():
        candidate = root / "profiles.ini"
        if candidate.exists():
            return candidate
    return None


def profile_dir() -> Path | None:
    ini = profiles_ini_path()
    if ini is None:
        return None

    parser = configparser.RawConfigParser()
    parser.read(ini)
    root = ini.parent
    candidates: list[tuple[int, Path]] = []

    for section in parser.sections():
        if not section.lower().startswith("profile") or not parser.has_option(section, "Path"):
            continue
        raw_path = parser.get(section, "Path")
        path = (
            root / raw_path
            if parser.get(section, "IsRelative", fallback="1") == "1"
            else Path(raw_path)
        )
        score = 0
        if parser.get(section, "Default", fallback="0") == "1":
            score += 10
        if (path / "prefs.js").exists():
            score += 5
        candidates.append((score, path))

    if candidates:
        return max(candidates, key=lambda item: item[0])[1]

    profiles = sorted((root / "Profiles").glob("*.default*"))
    return profiles[0] if profiles else None


def prefs_file() -> Path | None:
    profile = profile_dir()
    if profile is None:
        return None
    candidate = profile / "prefs.js"
    return candidate if candidate.exists() else None


def pref_pattern() -> re.Pattern[str]:
    return re.compile(r'user_pref\("' + re.escape(LOCAL_API_PREF) + r'",\s*(true|false)\s*\);')


def read_local_api_pref() -> bool | None:
    prefs = prefs_file()
    if prefs is None:
        return None
    match = pref_pattern().search(prefs.read_text(encoding="utf-8", errors="replace"))
    if match is None:
        return None
    return match.group(1) == "true"


def set_local_api_pref(enabled: bool) -> Path:
    prefs = prefs_file()
    if prefs is None:
        exit_with("Could not find Zotero prefs.js. Start Zotero once, then retry.")

    backup = prefs.with_suffix(prefs.suffix + f".zotero-skill-backup-{int(time.time())}")
    shutil.copy2(prefs, backup)

    text = prefs.read_text(encoding="utf-8", errors="replace")
    new_line = f'user_pref("{LOCAL_API_PREF}", {str(enabled).lower()});'
    pattern = pref_pattern()
    if pattern.search(text):
        text = pattern.sub(new_line, text, count=1)
    else:
        text = text.rstrip("\n") + "\n" + new_line + "\n"
    prefs.write_text(text, encoding="utf-8")
    return backup


def url_for(path: str, base_url: str = DEFAULT_BASE_URL) -> str:
    return base_url.rstrip("/") + path


def request(
    path: str,
    *,
    method: str = "GET",
    data: Any = None,
    headers: dict[str, str] | None = None,
    timeout: float = 5.0,
) -> Response:
    req_headers = dict(headers or {})
    body: bytes | None = None

    if path.startswith("/api"):
        req_headers.update({k: v for k, v in API_VERSION_HEADERS.items() if k not in req_headers})
    if path.startswith("/connector"):
        req_headers.update({k: v for k, v in CONNECTOR_HEADERS.items() if k not in req_headers})

    if data is not None:
        if isinstance(data, (dict, list)):
            body = json.dumps(data).encode("utf-8")
            req_headers.setdefault("Content-Type", "application/json")
        elif isinstance(data, bytes):
            body = data
        else:
            body = str(data).encode("utf-8")

    try:
        req = urllib.request.Request(url_for(path), data=body, method=method, headers=req_headers)
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return Response(
                status=response.status,
                headers=dict(response.headers.items()),
                text=response.read().decode("utf-8", errors="replace"),
            )
    except urllib.error.HTTPError as exc:
        return Response(
            status=exc.code,
            headers=dict(exc.headers.items()),
            text=exc.read().decode("utf-8", errors="replace"),
            error=str(exc),
        )
    except Exception as exc:  # local server down, Zotero closed, malformed URL, etc.
        return Response(status=None, headers={}, text="", error=str(exc))


def parse_body(response: Response) -> Any:
    if "json" not in response.content_type.lower():
        return response.text
    try:
        return json.loads(response.text or "null")
    except json.JSONDecodeError:
        return response.text


def require_ok(response: Response, action: str) -> Response:
    if response.ok:
        return response
    detail = response.error or response.text[:TEXT_LIMIT] or "no response body"
    exit_with(f"{action} failed: status={response.status} detail={detail}")
    raise AssertionError("unreachable")


def api_response(path: str) -> Response:
    api_path = path if path.startswith("/api") else "/api" + path
    return require_ok(request(api_path), f"GET {api_path}")


def api_get(path: str) -> Any:
    return parse_body(api_response(path))


def api_get_all(path: str, *, page_size: int = API_PAGE_LIMIT) -> list[dict[str, Any]]:
    """Fetch a paginated local API collection completely using Total-Results."""
    parsed = urllib.parse.urlsplit(path)
    base_params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    rows: list[dict[str, Any]] = []
    start = 0
    while True:
        params = [(key, value) for key, value in base_params if key not in {"limit", "start"}]
        params.extend([("limit", str(page_size)), ("start", str(start))])
        page_path = urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(params), parsed.fragment)
        )
        response = api_response(page_path)
        page = parse_body(response)
        if not isinstance(page, list):
            exit_with(f"Expected an array response while paging {parsed.path}.")
        rows.extend(item for item in page if isinstance(item, dict))
        total = total_results(response)
        start += page_size
        if total is not None and start >= total:
            break
        if not page or len(page) < page_size:
            break
    return rows


def zotero_major_version(version: str | None) -> int | None:
    match = re.match(r"\s*(\d+)", version or "")
    return int(match.group(1)) if match else None


def merged_classification(
    data: dict[str, Any],
    collection_key: str,
    tags: list[str],
    remove_collection_key: str | None = None,
) -> dict[str, list[Any]]:
    """Return only the complete tag/collection arrays needed for a PATCH."""
    collections = list(data.get("collections") or [])
    if remove_collection_key:
        collections = [key for key in collections if key != remove_collection_key]
    if collection_key not in collections:
        collections.append(collection_key)

    item_tags = list(data.get("tags") or [])
    existing = {
        entry.get("tag")
        for entry in item_tags
        if isinstance(entry, dict) and isinstance(entry.get("tag"), str)
    }
    for tag in tags:
        if tag not in existing:
            item_tags.append({"tag": tag})
            existing.add(tag)
    return {"collections": collections, "tags": item_tags}


def writable_server_id() -> str:
    status = status_payload()
    if not status["api_running"] or not status["local_api_enabled_pref"]:
        exit_with(
            "Zotero local API is unavailable or disabled. Enable it in Zotero Settings → "
            "Advanced → Allow other applications on this computer to communicate with Zotero."
        )
    major = zotero_major_version(status.get("zotero_version"))
    if major is None or major < 10:
        exit_with(
            "Safe local writes require Zotero 10 or later. This Zotero version is read-only "
            "for this skill; upgrade Zotero before classifying items. No library changes were made."
        )
    server_id = status.get("server_id")
    if not server_id:
        exit_with("Zotero did not provide a local server ID; refusing to write.")
    return server_id


def authorize_local_write(server_id: str, *, timeout: float = 120.0) -> str:
    auth = request(
        "/api/local/authorize",
        method="POST",
        data={"appName": "Zotero Buddy"},
        headers={"Zotero-Server-ID": server_id},
        timeout=timeout,
    )
    if auth.status == 403:
        exit_with("Zotero write authorization was denied; no library changes were made.")
    require_ok(auth, "Request Zotero local write authorization")
    auth_data = parse_body(auth)
    api_key = auth_data.get("key") if isinstance(auth_data, dict) else None
    if not api_key:
        exit_with("Zotero did not return a local write key; refusing to write.")
    if auth_data.get("remember") is True:
        save_remembered_local_key(server_id, api_key)
    return api_key


def local_key_cache_path() -> Path:
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    return codex_home / "secrets" / "zoterobuddy-local-api-keys.bin"


def _dpapi_transform(data: bytes, *, protect: bool) -> bytes:
    """Protect/unprotect cache contents for the current Windows user."""
    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    source_buffer = ctypes.create_string_buffer(data)
    source = DataBlob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if protect:
        ok = crypt32.CryptProtectData(
            ctypes.byref(source), "Zotero Buddy local API keys", None, None, None, 1, ctypes.byref(target)
        )
    else:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)
        )
    if not ok:
        raise OSError("Windows could not protect or unprotect the local API key cache")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(target.pbData, ctypes.c_void_p))


def _read_local_key_cache() -> dict[str, str]:
    path = local_key_cache_path()
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}
    try:
        if os.name == "nt":
            raw = _dpapi_transform(raw, protect=False)
        value = json.loads(raw.decode("utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def _write_local_key_cache(keys: dict[str, str]) -> None:
    path = local_key_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(keys, ensure_ascii=False).encode("utf-8")
    if os.name == "nt":
        raw = _dpapi_transform(raw, protect=True)
    path.write_bytes(raw)
    if os.name != "nt":
        path.chmod(0o600)


def save_remembered_local_key(server_id: str, api_key: str) -> None:
    keys = _read_local_key_cache()
    keys[server_id] = api_key
    _write_local_key_cache(keys)


def forget_remembered_local_key(server_id: str) -> None:
    keys = _read_local_key_cache()
    if server_id in keys:
        del keys[server_id]
        _write_local_key_cache(keys)


def local_write_request(
    path: str,
    *,
    server_id: str,
    method: str,
    data: Any,
    timeout: float = 5.0,
) -> Response:
    """Reuse an Always Allow key and reauthorize once only after a definite 401."""
    api_key = _read_local_key_cache().get(server_id)
    remembered = api_key is not None
    if api_key is None:
        api_key = authorize_local_write(server_id)
        remembered = server_id in _read_local_key_cache()
    headers = {"Zotero-Server-ID": server_id, "Zotero-API-Key": api_key}
    result = request(path, method=method, data=data, headers=headers, timeout=timeout)
    if result.status == 401 and remembered:
        forget_remembered_local_key(server_id)
        api_key = authorize_local_write(server_id)
        headers["Zotero-API-Key"] = api_key
        result = request(path, method=method, data=data, headers=headers, timeout=timeout)
    return result


def cmd_classify(args: argparse.Namespace) -> None:
    if not args.yes:
        exit_with("Refusing to modify Zotero without --yes. Use this only for the item and changes the user requested.")
    server_id = writable_server_id()

    requested_tags = list(dict.fromkeys(tag.strip() for tag in args.tag if tag.strip()))
    if not requested_tags:
        exit_with("Provide at least one non-empty --tag.")
    invalid_tags = [tag for tag in requested_tags if not tag.startswith("#")]
    if invalid_tags:
        exit_with("Classification tags must start with '#': " + ", ".join(invalid_tags))
    if args.remove_collection_key == args.collection_key:
        exit_with("The source collection and destination collection must be different.")

    raw_item = api_get(f"{LOCAL_USER}/items/{urllib.parse.quote(args.item_key)}")
    item_data = raw_item.get("data", {})
    if item_data.get("itemType") == "attachment":
        parent_key = item_data.get("parentItem")
        if not parent_key:
            exit_with("This attachment has no parent bibliographic item; refusing to classify it.")
        raw_item = api_get(f"{LOCAL_USER}/items/{urllib.parse.quote(parent_key)}")
        item_data = raw_item.get("data", {})
        if not item_data or item_data.get("itemType") == "attachment":
            exit_with("Could not resolve the attachment to a bibliographic parent item.")

    item_library = (raw_item.get("library") or {}).get("id")
    collection = api_get(
        f"{LOCAL_USER}/collections/{urllib.parse.quote(args.collection_key)}"
    )
    if (collection.get("library") or {}).get("id") != item_library:
        exit_with("The destination collection is not in the same Zotero library as the item.")
    if args.remove_collection_key:
        source = api_get(
            f"{LOCAL_USER}/collections/{urllib.parse.quote(args.remove_collection_key)}"
        )
        if (source.get("library") or {}).get("id") != item_library:
            exit_with("The source collection is not in the same Zotero library as the item.")

    patch = merged_classification(
        item_data,
        args.collection_key,
        requested_tags,
        args.remove_collection_key,
    )
    if patch["collections"] == (item_data.get("collections") or []) and patch["tags"] == (
        item_data.get("tags") or []
    ):
        dump_json({"status": "unchanged", "item_key": item_data.get("key")})
        return

    item_key = item_data.get("key")
    write_response = local_write_request(
        f"/api/users/0/items/{urllib.parse.quote(item_key)}",
        server_id=server_id,
        method="PATCH",
        data={"version": item_data.get("version"), **patch},
    )
    # Never retry a write whose outcome may be uncertain. Read-after-write is safe.
    if write_response.status == 412:
        exit_with("Zotero reports that the item changed during classification (412); refetch and retry.")
    if not write_response.ok:
        detail = write_response.error or write_response.text[:TEXT_LIMIT] or "no response body"
        exit_with(f"Zotero classification write failed: status={write_response.status} detail={detail}")

    verified = api_get(f"{LOCAL_USER}/items/{urllib.parse.quote(item_key)}").get("data", {})
    expected_tags = {entry["tag"] for entry in patch["tags"] if isinstance(entry, dict) and "tag" in entry}
    actual_tags = {entry.get("tag") for entry in (verified.get("tags") or []) if isinstance(entry, dict)}
    expected_collections = set(patch["collections"])
    actual_collections = set(verified.get("collections") or [])
    if not expected_tags.issubset(actual_tags) or actual_collections != expected_collections:
        exit_with("Write returned success, but read-after-write verification did not match; inspect Zotero before retrying.")
    dump_json(
        {
            "status": "updated",
            "item_key": item_key,
            "title": verified.get("title"),
            "collections": verified.get("collections", []),
            "tags": verified.get("tags", []),
        }
    )


def cmd_classify_batch(args: argparse.Namespace) -> None:
    if not args.yes:
        exit_with("Refusing to modify Zotero without --yes. Use this only for records the user requested.")
    try:
        manifest = json.loads(Path(args.file).expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        exit_with(f"Could not read classification manifest: {exc}")
    if not isinstance(manifest, list) or not manifest:
        exit_with("Classification manifest must be a non-empty JSON array.")
    if len(manifest) > 50:
        exit_with("A classification batch can contain at most 50 records.")

    server_id = writable_server_id()
    prepared: list[dict[str, Any]] = []
    seen_item_keys: set[str] = set()
    library_id: Any = None
    for index, row in enumerate(manifest):
        if not isinstance(row, dict):
            exit_with(f"Manifest row {index + 1} must be an object.")
        item_key = row.get("item_key")
        collection_key = row.get("collection_key")
        tags = row.get("tags")
        source_key = row.get("remove_collection_key")
        expected_collections = row.get("expected_collection_keys")
        if not isinstance(item_key, str) or not item_key.strip():
            exit_with(f"Manifest row {index + 1} is missing item_key.")
        if not isinstance(collection_key, str) or not collection_key.strip():
            exit_with(f"Manifest row {index + 1} is missing collection_key.")
        if not isinstance(tags, list) or not tags or any(not isinstance(tag, str) for tag in tags):
            exit_with(f"Manifest row {index + 1} must have a non-empty tags string array.")
        tags = list(dict.fromkeys(tag.strip() for tag in tags if tag.strip()))
        if not tags or any(not tag.startswith("#") for tag in tags):
            exit_with(f"Every tag in manifest row {index + 1} must start with '#'.")
        if source_key is not None and not isinstance(source_key, str):
            exit_with(f"Manifest row {index + 1} remove_collection_key must be a string.")
        if source_key == collection_key:
            exit_with(f"Manifest row {index + 1} source and destination collections must differ.")
        if expected_collections is not None and (
            not isinstance(expected_collections, list)
            or any(not isinstance(key, str) for key in expected_collections)
        ):
            exit_with(f"Manifest row {index + 1} expected_collection_keys must be a string array.")

        raw_item = api_get(f"{LOCAL_USER}/items/{urllib.parse.quote(item_key)}")
        item_data = raw_item.get("data", {})
        if item_data.get("itemType") == "attachment":
            parent_key = item_data.get("parentItem")
            if not parent_key:
                exit_with(f"Manifest row {index + 1} attachment has no parent item.")
            raw_item = api_get(f"{LOCAL_USER}/items/{urllib.parse.quote(parent_key)}")
            item_data = raw_item.get("data", {})
            if not item_data or item_data.get("itemType") == "attachment":
                exit_with(f"Manifest row {index + 1} could not resolve a bibliographic parent.")
        if expected_collections is not None and set(item_data.get("collections") or []) != set(
            expected_collections
        ):
            exit_with(
                f"Manifest row {index + 1} collection membership changed since inventory; "
                "no batch write was submitted."
            )
        resolved_key = item_data.get("key")
        if not resolved_key or resolved_key in seen_item_keys:
            exit_with(f"Manifest resolves duplicate or invalid item key at row {index + 1}.")
        seen_item_keys.add(resolved_key)

        item_library = (raw_item.get("library") or {}).get("id")
        if library_id is None:
            library_id = item_library
        elif item_library != library_id:
            exit_with("Batch items must belong to the same Zotero library.")
        collection = api_get(
            f"{LOCAL_USER}/collections/{urllib.parse.quote(collection_key)}"
        )
        if (collection.get("library") or {}).get("id") != item_library:
            exit_with(f"Destination collection in row {index + 1} belongs to another library.")
        if source_key:
            source = api_get(f"{LOCAL_USER}/collections/{urllib.parse.quote(source_key)}")
            if (source.get("library") or {}).get("id") != item_library:
                exit_with(f"Source collection in row {index + 1} belongs to another library.")

        patch = merged_classification(item_data, collection_key, tags, source_key)
        if patch["collections"] == (item_data.get("collections") or []) and patch["tags"] == (
            item_data.get("tags") or []
        ):
            prepared.append({"key": resolved_key, "unchanged": True, "expected": patch})
        else:
            prepared.append(
                {
                    "key": resolved_key,
                    "unchanged": False,
                    "object": {"key": resolved_key, "version": item_data.get("version"), **patch},
                    "expected": patch,
                }
            )

    changed = [entry for entry in prepared if not entry["unchanged"]]
    if not changed:
        dump_json({"status": "unchanged", "count": len(prepared), "items": [x["key"] for x in prepared]})
        return

    write_response = local_write_request(
        "/api/users/0/items",
        server_id=server_id,
        method="POST",
        data=[entry["object"] for entry in changed],
        timeout=30.0,
    )
    if write_response.status == 412:
        exit_with("Zotero reports a batch version conflict (412); refetch and retry the batch.")
    if not write_response.ok:
        detail = write_response.error or write_response.text[:TEXT_LIMIT] or "no response body"
        exit_with(f"Zotero classification batch failed: status={write_response.status} detail={detail}")

    response_data = parse_body(write_response)
    failed = response_data.get("failed", {}) if isinstance(response_data, dict) else {}
    results = []
    for entry in prepared:
        key = entry["key"]
        current = api_get(f"{LOCAL_USER}/items/{urllib.parse.quote(key)}").get("data", {})
        expected_tags = {
            tag["tag"] for tag in entry["expected"]["tags"] if isinstance(tag, dict) and "tag" in tag
        }
        actual_tags = {
            tag.get("tag") for tag in (current.get("tags") or []) if isinstance(tag, dict)
        }
        expected_collections = set(entry["expected"]["collections"])
        actual_collections = set(current.get("collections") or [])
        verified = expected_tags.issubset(actual_tags) and actual_collections == expected_collections
        failure_index = next(
            (str(i) for i, changed_entry in enumerate(changed) if changed_entry["key"] == key),
            None,
        )
        row_failure = failed.get(failure_index) if failure_index is not None else None
        results.append(
            {
                "item_key": key,
                "status": "failed" if row_failure else ("unchanged" if entry["unchanged"] else "updated"),
                "verified": verified,
                "error": row_failure,
            }
        )
    complete = all(x["verified"] and x["status"] != "failed" for x in results)
    dump_json({"status": "verified" if complete else "partial_failure", "items": results})


def cmd_classify_preview(args: argparse.Namespace) -> None:
    """Read-only classification preflight suitable for showing before approval."""
    try:
        manifest = json.loads(Path(args.file).expanduser().read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        exit_with(f"Could not read classification manifest: {exc}")
    if not isinstance(manifest, list) or not manifest or len(manifest) > 50:
        exit_with("Classification preview needs a non-empty JSON array with at most 50 rows.")
    collections = {
        row["key"]: row for row in (summarize_collection(c) for c in api_get(f"{LOCAL_USER}/collections"))
    }
    seen: set[str] = set()
    results: list[dict[str, Any]] = []
    for index, row in enumerate(manifest, 1):
        if not isinstance(row, dict):
            exit_with(f"Manifest row {index} must be an object.")
        item_key, destination, tags = row.get("item_key"), row.get("collection_key"), row.get("tags")
        if not isinstance(item_key, str) or not isinstance(destination, str):
            exit_with(f"Manifest row {index} needs item_key and collection_key.")
        if not isinstance(tags, list) or not tags or any(not isinstance(tag, str) or not tag.startswith("#") for tag in tags):
            exit_with(f"Manifest row {index} needs a non-empty array of #tags.")
        raw = api_get(f"{LOCAL_USER}/items/{urllib.parse.quote(item_key)}")
        data = raw.get("data", {})
        if data.get("itemType") == "attachment":
            parent_key = data.get("parentItem")
            if not parent_key:
                exit_with(f"Manifest row {index} is a standalone attachment without a bibliographic parent.")
            raw = api_get(f"{LOCAL_USER}/items/{urllib.parse.quote(parent_key)}")
            data = raw.get("data", {})
        expected_collections = row.get("expected_collection_keys")
        if expected_collections is not None and set(data.get("collections") or []) != set(expected_collections):
            exit_with(
                f"Collection membership changed for {data.get('key') or item_key}; "
                "refresh the inventory and regenerate the preview."
            )
        resolved = data.get("key")
        if not resolved or resolved in seen:
            exit_with(f"Manifest row {index} resolves to a duplicate or invalid item key.")
        seen.add(resolved)
        destination_obj = api_get(f"{LOCAL_USER}/collections/{urllib.parse.quote(destination)}")
        if (destination_obj.get("library") or {}).get("id") != (raw.get("library") or {}).get("id"):
            exit_with(f"Destination collection in row {index} belongs to another library.")
        source_key = row.get("remove_collection_key")
        if source_key:
            source = api_get(f"{LOCAL_USER}/collections/{urllib.parse.quote(source_key)}")
            if (source.get("library") or {}).get("id") != (raw.get("library") or {}).get("id"):
                exit_with(f"Source collection in row {index} belongs to another library.")
        current_tags = {entry.get("tag") for entry in (data.get("tags") or []) if isinstance(entry, dict)}
        current_collections = list(data.get("collections") or [])
        changed_tags = [tag for tag in dict.fromkeys(tags) if tag not in current_tags]
        resulting_collections = list(current_collections)
        if source_key:
            resulting_collections = [key for key in resulting_collections if key != source_key]
        if destination not in resulting_collections:
            resulting_collections.append(destination)
        results.append(
            {
                "item_key": resolved,
                "itemType": data.get("itemType"),
                "title": data.get("title"),
                "creators": creators_from_item(data),
                "year": (data.get("date") or "")[:4],
                "from_attachment": resolved != item_key,
                "current_collections": [
                    {"key": key, "name": collections.get(key, {}).get("name", "(unknown collection)")}
                    for key in current_collections
                ],
                "destination": {"key": destination, "name": (destination_obj.get("data") or {}).get("name")},
                "remove_collection_key": source_key,
                "tags_to_add": changed_tags,
                "existing_tags_preserved": sorted(current_tags),
                "already_classified_in_destination": destination in current_collections,
            }
        )
    dump_json({"status": "preview_only_no_changes_made", "count": len(results), "items": results})


def query(params: dict[str, str | int | bool | None]) -> str:
    clean = {key: value for key, value in params.items() if value is not None}
    return urllib.parse.urlencode(clean)


def restart_zotero(wait_for_api: bool = True) -> bool:
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.run(
                ["osascript", "-e", 'tell application "Zotero" to quit'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
            time.sleep(1)
            subprocess.run(["open", "-a", "Zotero"], check=False)
        elif system == "Windows":
            close_script = (
                "$p=Get-Process -Name zotero -ErrorAction SilentlyContinue; "
                "if (-not $p) { exit 0 }; "
                "$ui=$p | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1; "
                "if (-not $ui) { exit 2 }; "
                "if (-not $ui.CloseMainWindow()) { exit 3 }; "
                "if (-not $ui.WaitForExit(30000)) { exit 4 }"
            )
            closed = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", close_script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=35,
                check=False,
            )
            if closed.returncode != 0:
                return False
            time.sleep(1)
            subprocess.Popen(["zotero.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.run(
                ["pkill", "-f", "zotero"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            time.sleep(1)
            subprocess.Popen(["zotero"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return False

    if not wait_for_api:
        return True
    for _ in range(30):
        if request("/api/", timeout=1).ok:
            return True
        time.sleep(0.5)
    return False


def creators_from_item(data: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for creator in data.get("creators", []) or []:
        name = creator.get("name") or " ".join(
            part for part in [creator.get("firstName"), creator.get("lastName")] if part
        )
        if name:
            names.append(name)
    return names


def year_from_date(raw: str | None) -> str | None:
    if not raw:
        return None
    match = re.search(r"(\d{4})", raw)
    return match.group(1) if match else None


def summarize_item(item: dict[str, Any]) -> dict[str, Any]:
    data = item.get("data", item)
    return {
        "key": item.get("key") or data.get("key"),
        "itemType": data.get("itemType"),
        "title": data.get("title"),
        "creators": creators_from_item(data),
        "year": year_from_date(data.get("date")),
    }


def summarize_collection(collection: dict[str, Any]) -> dict[str, Any]:
    data = collection.get("data", collection)
    return {
        "key": collection.get("key") or data.get("key"),
        "name": data.get("name"),
        "parentCollection": data.get("parentCollection"),
        "version": collection.get("version"),
    }


def summarize_tag(tag: dict[str, Any]) -> dict[str, Any]:
    return {"tag": tag.get("tag"), "numItems": (tag.get("meta") or {}).get("numItems")}


def summarize_group(group: dict[str, Any]) -> dict[str, Any]:
    data = group.get("data", group)
    return {
        "id": group.get("id") or data.get("id"),
        "name": data.get("name"),
        "type": data.get("type"),
    }


def print_items(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        creators = ", ".join(row.get("creators") or [])
        print(
            f"{row.get('key') or '':10} "
            f"{row.get('itemType') or '':14} "
            f"{row.get('year') or '':4} "
            f"{row.get('title') or ''} | {creators}"
        )


def extract_bibtex_keys(text: str) -> list[str]:
    return re.findall(r"@\w+\s*\{\s*([^,\s]+)", text)


def count_bibtex_entries(text: str) -> int:
    return len(extract_bibtex_keys(text))


def total_results(response: Response) -> int | None:
    raw = response.headers.get("Total-Results")
    return int(raw) if raw and raw.isdigit() else None


def export_bibtex(item_key: str | None = None, *, include_children: bool = False) -> str:
    if item_key:
        params = query({"itemKey": item_key, "format": "bibtex", "limit": API_PAGE_LIMIT})
        return api_response(f"{LOCAL_USER}/items?{params}").text

    endpoint = "items" if include_children else "items/top"
    start = 0
    chunks: list[str] = []
    while True:
        params = query(
            {
                "format": "bibtex",
                "sort": "title",
                "direction": "asc",
                "limit": API_PAGE_LIMIT,
                "start": start,
            }
        )
        response = api_response(f"{LOCAL_USER}/{endpoint}?{params}")
        if response.text.strip():
            chunks.append(response.text.strip())

        total = total_results(response)
        start += API_PAGE_LIMIT
        if total is not None and start >= total:
            break
        if total is None and count_bibtex_entries(response.text) < API_PAGE_LIMIT:
            break

    text = "\n\n".join(chunks)
    return text + "\n" if text else ""


def write_text_output(text: str, out: str | None) -> None:
    if out is None:
        print(text, end="" if text.endswith("\n") else "\n")
        return

    path = Path(out).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    dump_json(
        {
            "path": str(path),
            "bytes": len(text.encode("utf-8")),
            "bibtex_entries": count_bibtex_entries(text),
        }
    )


def append_bib_entry(bib_path: Path, entry: str) -> tuple[str, bool]:
    keys = extract_bibtex_keys(entry)
    if not keys:
        exit_with("Could not extract a BibTeX key from Zotero export")
    key = keys[0]

    existing = bib_path.read_text(encoding="utf-8", errors="replace") if bib_path.exists() else ""
    already_present = re.search(r"@\w+\s*\{\s*" + re.escape(key) + r"\s*,", existing) is not None
    if already_present:
        return key, False

    bib_path.parent.mkdir(parents=True, exist_ok=True)
    prefix = existing.rstrip("\n") + "\n\n" if existing else ""
    bib_path.write_text(prefix + entry.strip() + "\n", encoding="utf-8")
    return key, True


def insert_citation(target: Path, citation: str, marker: str | None) -> None:
    text = target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""
    if marker:
        if marker not in text:
            exit_with(f"Marker not found in {target}: {marker!r}")
        target.write_text(text.replace(marker, citation, 1), encoding="utf-8")
        return

    suffix = "" if not text or text.endswith("\n") else "\n"
    target.write_text(text + suffix + citation + "\n", encoding="utf-8")


def find_item(*, item_key: str | None, query_text: str | None) -> dict[str, Any]:
    if item_key:
        return api_get(f"{LOCAL_USER}/items/{urllib.parse.quote(item_key)}")
    if not query_text:
        exit_with("Provide --item-key or --query")

    params = query({"q": query_text})
    matches = api_get_all(f"{LOCAL_USER}/items/top?{params}")
    if not matches:
        exit_with(f"No top-level Zotero items matched query: {query_text}")
    if len(matches) > 1:
        candidates = [
            {
                "key": row.get("key"),
                "title": (row.get("data") or {}).get("title"),
                "creators": creators_from_item(row.get("data") or {}),
                "year": ((row.get("data") or {}).get("date") or "")[:4],
                "itemType": (row.get("data") or {}).get("itemType"),
            }
            for row in matches
        ]
        exit_with(
            f"Query matched {len(matches)} records; citation insertion was not performed. "
            "Choose an item key and rerun with --item-key. Candidates: "
            + json.dumps(candidates, ensure_ascii=False)
        )
    return matches[0]


def status_payload() -> dict[str, Any]:
    root = request("/api/", timeout=2)
    connector = request("/connector/ping", timeout=2)
    profile = profile_dir()
    prefs = prefs_file()
    return {
        "profile": str(profile) if profile else None,
        "prefs_file": str(prefs) if prefs else None,
        "local_api_enabled_pref": read_local_api_pref(),
        "api_running": root.ok,
        "api_status": root.status,
        "api_error": root.error,
        "zotero_version": root.headers.get("X-Zotero-Version")
        or connector.headers.get("X-Zotero-Version"),
        "api_version": root.headers.get("Zotero-API-Version"),
        "schema_version": root.headers.get("Zotero-Schema-Version"),
        "server_id": root.headers.get("Zotero-Server-ID"),
        "connector_running": connector.ok,
        "connector_status": connector.status,
        "connector_error": connector.error,
        "base_url": DEFAULT_BASE_URL,
    }


def cmd_status(args: argparse.Namespace) -> None:
    payload = status_payload()
    if args.json:
        dump_json(payload)
        return
    print(f"Zotero local API pref: {payload['local_api_enabled_pref']} ({payload['prefs_file']})")
    print(
        "API running: "
        f"{payload['api_running']} status={payload['api_status']} "
        f"version={payload['api_version']} zotero={payload['zotero_version']}"
    )
    print(f"Connector running: {payload['connector_running']} status={payload['connector_status']}")


def cmd_set_pref(args: argparse.Namespace, enabled: bool) -> None:
    backup = set_local_api_pref(enabled)
    restarted = restart_zotero(wait_for_api=enabled) if args.restart else False
    dump_json(
        {
            "enabled": enabled,
            "backup": str(backup),
            "restarted": restarted,
            "status": status_payload(),
        }
    )


def cmd_restart(_: argparse.Namespace) -> None:
    dump_json({"restarted": restart_zotero(wait_for_api=True)})


def cmd_probe(args: argparse.Namespace) -> None:
    endpoints = [
        ("root", "/api/"),
        ("schema", "/api/schema"),
        ("itemTypes", "/api/itemTypes"),
        ("itemFields", "/api/itemFields"),
        ("creatorFields", "/api/creatorFields"),
        ("collections", f"{LOCAL_USER}/collections"),
        ("topCollections", f"{LOCAL_USER}/collections/top"),
        ("topItems", f"{LOCAL_USER}/items/top"),
        ("tags", f"{LOCAL_USER}/tags"),
        ("searches", f"{LOCAL_USER}/searches"),
        ("groups", f"{LOCAL_USER}/groups"),
        ("fulltextVersions", f"{LOCAL_USER}/fulltext?since=0"),
        ("connectorPing", "/connector/ping"),
    ]
    rows: list[dict[str, Any]] = []
    for label, path in endpoints:
        response = request(path)
        parsed = parse_body(response)
        if isinstance(parsed, list):
            summary: Any = {"type": "array", "len": len(parsed)}
        elif isinstance(parsed, dict):
            summary = {"type": "object", "keys": list(parsed)[:8]}
        else:
            summary = str(parsed)[:160]
        rows.append(
            {
                "label": label,
                "path": path,
                "status": response.status,
                "content_type": response.content_type,
                "total": response.headers.get("Total-Results"),
                "summary": summary,
            }
        )

    if args.json:
        dump_json(rows)
        return
    for row in rows:
        print(
            f"{row['status'] or 'ERR':>3} {row['label']:18} {row['path']:45} total={row['total']} {row['summary']}"
        )


def cmd_inventory(args: argparse.Namespace) -> None:
    endpoint = "items" if args.include_children else "items/top"
    params = query({"sort": "title", "direction": "asc"})
    rows = [summarize_item(item) for item in api_get_all(f"{LOCAL_USER}/{endpoint}?{params}")]
    dump_json(rows) if args.json else print_items(rows)


def cmd_collections(args: argparse.Namespace) -> None:
    rows = [summarize_collection(collection) for collection in api_get_all(f"{LOCAL_USER}/collections")]
    if args.json:
        dump_json(rows)
        return
    for row in rows:
        parent = f" parent={row['parentCollection']}" if row.get("parentCollection") else ""
        print(f"{row.get('key') or '':10} {row.get('name') or ''}{parent}")


def user_settings_path() -> Path:
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    return codex_home / "skills" / "zoterobuddy" / "user-settings.json"


def read_user_settings() -> dict[str, Any]:
    try:
        value = json.loads(user_settings_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def collection_descendants(collections: list[dict[str, Any]], roots: set[str]) -> set[str]:
    children: dict[str | None, list[str]] = {}
    for collection in collections:
        children.setdefault(collection.get("parentCollection"), []).append(collection.get("key"))
    result: set[str] = set()
    pending = list(roots)
    while pending:
        key = pending.pop()
        if key in result:
            continue
        result.add(key)
        pending.extend(children.get(key, []))
    return result


def cmd_unclassified(args: argparse.Namespace) -> None:
    settings = read_user_settings()
    taxonomy = settings.get("taxonomy", {})
    configured_roots = taxonomy.get("subject_collection_roots", []) if isinstance(taxonomy, dict) else []
    configured_workspaces = taxonomy.get("workspace_collection_roots", []) if isinstance(taxonomy, dict) else []
    if not isinstance(configured_roots, list) or any(not isinstance(key, str) for key in configured_roots):
        exit_with("Invalid taxonomy.subject_collection_roots setting; expected an array of collection keys.")
    if not isinstance(configured_workspaces, list) or any(not isinstance(key, str) for key in configured_workspaces):
        exit_with("Invalid taxonomy.workspace_collection_roots setting; expected an array of collection keys.")
    roots = set(args.classification_root or configured_roots)
    if not roots:
        collections = [summarize_collection(c) for c in api_get_all(f"{LOCAL_USER}/collections")]
        exit_with(
            "No subject classification roots are configured. Review `collections --json`, "
            "then set user-settings.json taxonomy.subject_collection_roots after user approval. "
            + json.dumps(collections, ensure_ascii=False)
        )
    all_collections = [summarize_collection(c) for c in api_get_all(f"{LOCAL_USER}/collections")]
    by_key = {row.get("key"): row for row in all_collections}
    missing_roots = sorted(roots - by_key.keys())
    if missing_roots:
        exit_with("Unknown classification collection keys: " + ", ".join(missing_roots))
    subject_keys = collection_descendants(all_collections, roots)
    top_items = api_get_all(f"{LOCAL_USER}/items/top")
    workspace_roots = set(configured_workspaces)
    missing_workspace_roots = sorted(workspace_roots - by_key.keys())
    if missing_workspace_roots:
        exit_with("Unknown workspace collection keys: " + ", ".join(missing_workspace_roots))
    workspace_keys = collection_descendants(all_collections, workspace_roots)
    rows = []
    root_unfiled = 0
    for item in top_items:
        data = item.get("data", {})
        memberships = set(data.get("collections") or [])
        if not memberships:
            root_unfiled += 1
        if not (memberships & subject_keys):
            rows.append(
                {
                    "key": item.get("key"),
                    "itemType": data.get("itemType"),
                    "title": data.get("title"),
                    "creators": creators_from_item(data),
                    "year": (data.get("date") or "")[:4],
                    "collectionKeys": sorted(memberships),
                    "collectionNames": [by_key[k]["name"] for k in sorted(memberships) if k in by_key],
                    "inWorkspaceOnly": bool(memberships & workspace_keys),
                    "tags": [tag.get("tag") for tag in data.get("tags", []) if isinstance(tag, dict)],
                }
            )
    payload = {
        "taxonomyRoots": [{"key": key, "name": by_key[key]["name"]} for key in sorted(roots)],
        "subjectClassificationDescendantCount": len(subject_keys),
        "topLevelItemCount": len(top_items),
        "rootUnfiledCount": root_unfiled,
        "missingSubjectClassificationCount": len(rows),
        "items": rows,
    }
    dump_json(payload) if args.json else print_items(rows)


def cmd_tags(args: argparse.Namespace) -> None:
    rows = [summarize_tag(tag) for tag in api_get_all(f"{LOCAL_USER}/tags")]
    if args.json:
        dump_json(rows)
        return
    for row in rows:
        print(f"{row.get('tag') or ''} ({row.get('numItems') or 0})")


def cmd_groups(args: argparse.Namespace) -> None:
    rows = [summarize_group(group) for group in api_get_all(f"{LOCAL_USER}/groups")]
    if args.json:
        dump_json(rows)
        return
    for row in rows:
        print(f"{row.get('id') or '':>10} {row.get('type') or '':12} {row.get('name') or ''}")


def cmd_search(args: argparse.Namespace) -> None:
    params = query({"q": args.query})
    rows = [summarize_item(item) for item in api_get_all(f"{LOCAL_USER}/items/top?{params}")]
    if args.with_bibtex_keys:
        for row in rows:
            bibtex = export_bibtex(row.get("key")) if row.get("key") else ""
            keys = extract_bibtex_keys(bibtex)
            row["bibtexKey"] = keys[0] if keys else None
    dump_json(rows) if args.json else print_items(rows)


def cmd_saved_searches(args: argparse.Namespace) -> None:
    searches = api_get_all(f"{LOCAL_USER}/searches")
    rows = []
    for search_data in searches:
        data = search_data.get("data", {})
        rows.append(
            {
                "key": search_data.get("key"),
                "name": data.get("name"),
                "conditions": data.get("conditions", []),
                "version": search_data.get("version"),
            }
        )
    dump_json(rows) if args.json else print_items(
        [{"key": row["key"], "title": row["name"], "creators": [], "year": ""} for row in rows]
    )


def cmd_saved_search_items(args: argparse.Namespace) -> None:
    items = api_get_all(
        f"{LOCAL_USER}/searches/{urllib.parse.quote(args.search_key)}/items"
    )
    rows = [summarize_item(item) for item in items]
    dump_json(rows) if args.json else print_items(rows)


def cmd_export_bibtex(args: argparse.Namespace) -> None:
    write_text_output(
        export_bibtex(args.item_key, include_children=args.include_children), args.out
    )


def cmd_sync_bib(args: argparse.Namespace) -> None:
    text = export_bibtex(include_children=args.include_children)
    path = Path(args.out).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    dump_json({"path": str(path), "entries": count_bibtex_entries(text)})


def cmd_citations(args: argparse.Namespace) -> None:
    params = query({"include": "data,citation", "style": args.style})
    rows: list[dict[str, Any]] = []
    for item in api_get_all(f"{LOCAL_USER}/items/top?{params}"):
        row = summarize_item(item)
        row["citation"] = item.get("citation")
        rows.append(row)
    if args.json:
        dump_json(rows)
        return
    for row in rows:
        print(f"{row.get('key')} {row.get('citation')}")


def cmd_children(args: argparse.Namespace) -> None:
    data = api_get_all(f"{LOCAL_USER}/items/{urllib.parse.quote(args.item_key)}/children")
    rows = [summarize_item(item) for item in data]
    dump_json(rows) if args.json else print_items(rows)


def cmd_fulltext(args: argparse.Namespace) -> None:
    data = api_get(f"{LOCAL_USER}/items/{urllib.parse.quote(args.attachment_key)}/fulltext")
    content = data.get("content", "") if isinstance(data, dict) else str(data)
    if args.out is None:
        print(content)
        return
    path = Path(args.out).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    dump_json(
        {
            "path": str(path),
            "chars": len(content),
            "indexedPages": data.get("indexedPages") if isinstance(data, dict) else None,
            "totalPages": data.get("totalPages") if isinstance(data, dict) else None,
        }
    )


def cmd_file_url(args: argparse.Namespace) -> None:
    print(api_get(f"{LOCAL_USER}/items/{urllib.parse.quote(args.attachment_key)}/file/view/url"))


def cmd_cite(args: argparse.Namespace) -> None:
    item = find_item(item_key=args.item_key, query_text=args.query)
    item_key = item.get("key")
    if not item_key:
        exit_with("Matched Zotero item has no key")

    entry = export_bibtex(item_key)
    keys = extract_bibtex_keys(entry)
    if not keys:
        exit_with(f"Zotero did not return a BibTeX key for item {item_key}.")
    citekey = keys[0]
    citation = f"\\cite{{{citekey}}}" if args.tex else f"[@{citekey}]"
    target = Path(args.tex or args.markdown).expanduser().resolve()
    bib_path = Path(args.bib).expanduser().resolve()
    target_text = target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""
    if args.marker and args.marker not in target_text:
        exit_with(f"Marker not found in {target}: {args.marker!r}; no citation files were changed.")
    if args.preview:
        existing = bib_path.read_text(encoding="utf-8", errors="replace") if bib_path.exists() else ""
        already_present = re.search(r"@\w+\s*\{\s*" + re.escape(citekey) + r"\s*,", existing) is not None
        dump_json(
            {
                "status": "preview_only_no_files_changed",
                "item_key": item_key,
                "title": summarize_item(item).get("title"),
                "bibtex_key": citekey,
                "citation": citation,
                "bib_path": str(bib_path),
                "bib_entry_already_present": already_present,
                "edited_file": str(target),
                "marker": args.marker,
                "marker_found": (args.marker in target_text) if args.marker else None,
            }
        )
        return
    _, added = append_bib_entry(bib_path, entry)
    insert_citation(target, citation, args.marker)
    dump_json(
        {
            "item_key": item_key,
            "title": summarize_item(item).get("title"),
            "bibtex_key": citekey,
            "bib_path": str(bib_path),
            "bib_entry_added": added,
            "edited_file": str(target),
            "inserted": citation,
        }
    )


def connector_post(path: str, payload: Any, *, content_type: str = "application/json") -> Response:
    return request(path, method="POST", data=payload, headers={"Content-Type": content_type})


def cmd_selected_target(args: argparse.Namespace) -> None:
    response = require_ok(
        connector_post("/connector/getSelectedCollection", {}),
        "POST /connector/getSelectedCollection",
    )
    payload = parse_body(response)
    print(json.dumps(payload, indent=2) if args.json else payload)


def cmd_import_records(args: argparse.Namespace, kind: str) -> None:
    if not args.yes:
        exit_with(
            f"Refusing to write to Zotero without --yes. "
            f"This imports {kind} into the selected Zotero library/collection."
        )
    text = Path(args.file).expanduser().read_text(encoding="utf-8") if args.file else args.text
    if not text:
        exit_with("Provide --file or --text")

    session = args.session or f"zoterobuddy-{uuid.uuid4().hex}"
    path = f"/connector/import?{query({'session': session})}"
    response = require_ok(connector_post(path, text, content_type="text/plain"), f"POST {path}")
    dump_json({"status": response.status, "session": session, "response": parse_body(response)})


def add_json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json", action="store_true", help="Print JSON instead of a compact text summary"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Operate Zotero Desktop local API and connector server."
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    status = subcommands.add_parser("status", help="Show Zotero local API and connector readiness")
    add_json_flag(status)
    status.set_defaults(func=cmd_status)

    enable = subcommands.add_parser("enable", help="Enable Zotero's local Desktop API preference")
    enable.add_argument(
        "--restart", action="store_true", help="Restart Zotero after editing prefs.js"
    )
    enable.set_defaults(func=lambda args: cmd_set_pref(args, True))

    disable = subcommands.add_parser(
        "disable", help="Disable Zotero's local Desktop API preference"
    )
    disable.add_argument(
        "--restart", action="store_true", help="Restart Zotero after editing prefs.js"
    )
    disable.set_defaults(func=lambda args: cmd_set_pref(args, False))

    restart = subcommands.add_parser("restart", help="Restart Zotero and wait for the local API")
    restart.set_defaults(func=cmd_restart)

    probe = subcommands.add_parser("probe", help="Probe common safe local API routes")
    add_json_flag(probe)
    probe.set_defaults(func=cmd_probe)

    inventory = subcommands.add_parser("inventory", help="List Zotero items")
    inventory.add_argument(
        "--include-children", action="store_true", help="Include child notes and attachments"
    )
    inventory.add_argument(
        "--all", action="store_true", dest="include_children", help=argparse.SUPPRESS
    )
    add_json_flag(inventory)
    inventory.set_defaults(func=cmd_inventory)

    collections = subcommands.add_parser("collections", help="List collections")
    add_json_flag(collections)
    collections.set_defaults(func=cmd_collections)

    unclassified = subcommands.add_parser(
        "unclassified", help="Find records lacking subject classification using configured taxonomy roots"
    )
    unclassified.add_argument(
        "--classification-root", action="append", help="Subject taxonomy collection root key; repeatable"
    )
    add_json_flag(unclassified)
    unclassified.set_defaults(func=cmd_unclassified)

    tags = subcommands.add_parser("tags", help="List tags")
    add_json_flag(tags)
    tags.set_defaults(func=cmd_tags)

    groups = subcommands.add_parser("groups", help="List synced group libraries visible locally")
    add_json_flag(groups)
    groups.set_defaults(func=cmd_groups)

    search = subcommands.add_parser("search", help="Search top-level Zotero items")
    search.add_argument("query")
    search.add_argument("--with-bibtex-keys", action="store_true")
    add_json_flag(search)
    search.set_defaults(func=cmd_search)

    saved_searches = subcommands.add_parser("saved-searches", help="List saved searches and their conditions")
    add_json_flag(saved_searches)
    saved_searches.set_defaults(func=cmd_saved_searches)

    saved_search_items = subcommands.add_parser("saved-search-items", help="List records matching a saved search")
    saved_search_items.add_argument("search_key")
    add_json_flag(saved_search_items)
    saved_search_items.set_defaults(func=cmd_saved_search_items)

    export = subcommands.add_parser("export-bibtex", help="Export Zotero items as BibTeX")
    export.add_argument("--item-key")
    export.add_argument("--include-children", action="store_true")
    export.add_argument(
        "--all", action="store_true", dest="include_children", help=argparse.SUPPRESS
    )
    export.add_argument("--out")
    export.set_defaults(func=cmd_export_bibtex)

    sync_bib = subcommands.add_parser("sync-bib", help="Write a references.bib export")
    sync_bib.add_argument("--out", default="references.bib")
    sync_bib.add_argument("--include-children", action="store_true")
    sync_bib.add_argument(
        "--all", action="store_true", dest="include_children", help=argparse.SUPPRESS
    )
    sync_bib.set_defaults(func=cmd_sync_bib)

    citations = subcommands.add_parser("citations", help="Render formatted citations")
    citations.add_argument("--style", default="apa")
    add_json_flag(citations)
    citations.set_defaults(func=cmd_citations)

    children = subcommands.add_parser("children", help="List child notes/attachments for an item")
    children.add_argument("item_key")
    add_json_flag(children)
    children.set_defaults(func=cmd_children)

    fulltext = subcommands.add_parser(
        "fulltext", help="Print or save indexed full text for an attachment"
    )
    fulltext.add_argument("attachment_key")
    fulltext.add_argument("--out")
    fulltext.set_defaults(func=cmd_fulltext)

    file_url = subcommands.add_parser(
        "file-url", help="Print Zotero's local file URL for an attachment"
    )
    file_url.add_argument("attachment_key")
    file_url.set_defaults(func=cmd_file_url)

    cite = subcommands.add_parser(
        "cite", help="Insert a citation into a TeX or Markdown file and update a .bib file"
    )
    source = cite.add_mutually_exclusive_group(required=True)
    source.add_argument("--item-key")
    source.add_argument("--query")
    target = cite.add_mutually_exclusive_group(required=True)
    target.add_argument("--tex")
    target.add_argument("--markdown")
    cite.add_argument("--bib", default="references.bib")
    cite.add_argument(
        "--marker", help="Replace this marker with the citation; otherwise append the citation"
    )
    cite.add_argument("--preview", action="store_true", help="Show citation and bibliography changes without editing files")
    cite.set_defaults(func=cmd_cite)

    selected = subcommands.add_parser(
        "selected-target", help="Show the currently selected Zotero library/collection"
    )
    add_json_flag(selected)
    selected.set_defaults(func=cmd_selected_target)

    classify = subcommands.add_parser(
        "classify",
        help="Add #tags and a collection to an existing item via Zotero 10+ local API",
    )
    classify.add_argument("item_key", help="Zotero item key; attachment keys resolve to their parent")
    classify.add_argument("--collection-key", required=True, help="Destination collection key")
    classify.add_argument(
        "--remove-collection-key",
        help="Optional source collection key to remove, e.g. 100.未分类",
    )
    classify.add_argument(
        "--tag", action="append", required=True, help="Tag to add (must start with #; repeatable)"
    )
    classify.add_argument("--yes", action="store_true", help="Confirm the requested tag/collection update")
    classify.set_defaults(func=cmd_classify)

    classify_batch = subcommands.add_parser(
        "classify-batch",
        help="Classify up to 50 existing items in one authorized Zotero 10+ API request",
    )
    classify_batch.add_argument("--file", required=True, help="JSON array of classification rows")
    classify_batch.add_argument("--yes", action="store_true", help="Confirm the requested batch update")
    classify_batch.set_defaults(func=cmd_classify_batch)

    classify_preview = subcommands.add_parser(
        "classify-preview", help="Validate and display a classification manifest without writing"
    )
    classify_preview.add_argument("--file", required=True, help="JSON array of classification rows")
    classify_preview.set_defaults(func=cmd_classify_preview)

    for command, kind in [("import-bibtex", "BibTeX"), ("import-ris", "RIS")]:
        import_cmd = subcommands.add_parser(command, help=f"Import {kind} through Zotero Connector")
        input_group = import_cmd.add_mutually_exclusive_group(required=True)
        input_group.add_argument("--file")
        input_group.add_argument("--text")
        import_cmd.add_argument("--session")
        import_cmd.add_argument("--yes", action="store_true", help="Confirm Zotero library write")
        import_cmd.set_defaults(func=lambda args, kind=kind: cmd_import_records(args, kind))

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
