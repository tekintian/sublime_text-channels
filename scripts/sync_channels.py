#!/usr/bin/env python3
"""Sublime Text channel mirror sync.

Fetch official channel_v3/channel_v4, merge local overrides (custom first,
custom wins on name conflicts), write mirror files, validate, and skip
commit when nothing changed.

Only the Python standard library is used.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import zlib
from json import JSONDecodeError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

UPSTREAM_V3 = "https://packagecontrol.io/channel_v3.json"
UPSTREAM_V4 = "https://packagecontrol.github.io/channel/channel_v4.json"

V3_FILE = "channel_v3.json"
V4_FILE = "channel_v4.json"
OVERRIDES_V3 = os.path.join("overrides", "channel_v3.json")
OVERRIDES_V4 = os.path.join("overrides", "channel_v4.json")
SNAPSHOT_DIR = "upstream"

V3_KEY = "v3"
V4_KEY = "v4"

UPSTREAMS = (
    (V3_KEY, UPSTREAM_V3),
    (V4_KEY, UPSTREAM_V4),
)

SOURCES = (
    (V3_KEY, V3_FILE, OVERRIDES_V3, "3.0.0"),
    (V4_KEY, V4_FILE, OVERRIDES_V4, "4.0.0"),
)

CACHE_KEYS = ("packages_cache", "libraries_cache", "dependencies_cache")

FETCH_ATTEMPTS = 3
FETCH_TIMEOUT = 120
FETCH_MAX_BYTES = 128 * 1024 * 1024
MIN_SHRINK_RATIO = 0.8

OFFLINE = os.environ.get("OFFLINE", "").lower() in {"1", "true", "yes"}

LITERAL_RE = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null")


def log(msg: str) -> None:
    print(msg, flush=True)


def fetch(url: str) -> str:
    last_error = "no attempt executed"
    for attempt in range(1, FETCH_ATTEMPTS + 1):
        last_error = ""
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "sublime-text-channels-mirror-sync/1.0",
                    "Accept": "application/json, text/plain, */*",
                    "Accept-Encoding": "gzip, deflate",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                },
            )
            with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
                raw = resp.read(FETCH_MAX_BYTES + 1)
                encoding = (resp.headers.get("Content-Encoding") or "").lower()
                expected = resp.headers.get("Content-Length")
                if len(raw) > FETCH_MAX_BYTES:
                    raise ValueError(f"response too large (> {FETCH_MAX_BYTES} bytes)")
                if encoding == "gzip" or raw[:2] == b"\x1f\x8b":
                    raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                elif encoding == "deflate":
                    raw = zlib.decompress(raw)
                if expected and encoding not in {"gzip", "deflate"}:
                    if len(raw) != int(expected):
                        raise ValueError(f"truncated response: {len(raw)}/{expected} bytes")
            text = raw.decode("utf-8", errors="replace")
            if not text.strip():
                raise ValueError("empty response body")
            return text
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError, EOFError, zlib.error) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            log(f"  fetch attempt {attempt}/{FETCH_ATTEMPTS} failed for {url}: {last_error}")
            if attempt < FETCH_ATTEMPTS:
                time.sleep(5 * attempt)
    raise RuntimeError(f"cannot fetch {url}: {last_error}")


def repair_channel_text(text: str) -> str:
    """Make the channel text strict JSON.

    Official channel files use spaces instead of commas between members
    (", " was replaced by " "), so a comma must be re-inserted between a
    finished value and the following member. Only string and bracket
    positions are considered, so string contents are never touched.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    need_comma = False
    while i < n:
        ch = text[i]
        if ch == '"':
            if need_comma:
                out.append(",")
            start = i
            i += 1
            while i < n:
                c = text[i]
                if c == "\\":
                    i += 2
                    continue
                i += 1
                if c == '"':
                    break
            out.append(text[start:i])
            need_comma = True
            continue
        if ch == ":":
            out.append(":")
            need_comma = False
            i += 1
            continue
        if ch in "[{":
            if need_comma:
                out.append(",")
            out.append(ch)
            need_comma = False
            i += 1
            continue
        if ch in "]}":
            out.append(ch)
            need_comma = True
            i += 1
            continue
        if ch == ",":
            out.append(",")
            need_comma = False
            i += 1
            continue
        if ch.isspace():
            out.append(ch)
            i += 1
            continue
        literal = LITERAL_RE.match(text, i)
        if literal:
            if need_comma:
                out.append(",")
            out.append(literal.group(0))
            need_comma = True
            i = literal.end()
            continue
        out.append(ch)
        need_comma = True
        i += 1
    return "".join(out)


def parse_channel_text(text: str, label: str) -> dict:
    cleaned = text.lstrip("\ufeff")
    try:
        data = json.loads(cleaned)
    except (JSONDecodeError, TypeError):
        data = json.loads(repair_channel_text(cleaned))
    if not isinstance(data, dict):
        raise ValueError(f"{label}: channel root is not an object")
    log(f"  parsed {label}: {len(cleaned)} chars")
    return data


def parse_channel_file(path: str) -> dict:
    with open(path, encoding="utf-8", errors="surrogatepass") as fh:
        return parse_channel_text(fh.read(), path)


def load_optional(path: str) -> dict:
    if not os.path.exists(path):
        log(f"  no overrides at {path}, skipped")
        return {}
    return parse_channel_file(path)


def unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def entry_identity(entry: dict) -> str:
    name = entry.get("name")
    if isinstance(name, str) and name:
        return name
    return "release:" + (entry.get("url") or json.dumps(entry, sort_keys=True, ensure_ascii=False))


def merge_entries(custom: list, official: list) -> list:
    """Custom entries first and win on identity conflict, then official extras."""
    merged: list = []
    seen: set[str] = set()
    for source in (custom, official):
        for entry in source:
            key = entry_identity(entry)
            if key in seen:
                continue
            seen.add(key)
            merged.append(entry)
    return merged


def merge_repositories(custom: list, official: list) -> list:
    return unique([str(x) for x in custom] + [str(x) for x in official])


def merge_caches(custom: dict, official: dict) -> dict:
    """Merge every cache section bucket by bucket.

    Custom entries come first inside every bucket and, more importantly,
    a custom package name shadows the same name from the official buckets,
    so the published channel never lists a duplicated package.
    """
    result: dict = {}
    for cache_name in CACHE_KEYS:
        cust_cache = custom.get(cache_name) or {}
        off_cache = official.get(cache_name) or {}

        custom_names: set[str] = set()
        for entries in cust_cache.values():
            for entry in entries or []:
                custom_names.add(entry_identity(entry))

        merged_buckets: dict = {}
        for bucket in [b for b in cust_cache] + [b for b in off_cache if b not in cust_cache]:
            custom_entries = []
            for entry in cust_cache.get(bucket) or []:
                custom_entries.append(entry)
            official_entries = [
                entry
                for entry in off_cache.get(bucket) or []
                if entry_identity(entry) not in custom_names
            ]
            merged_buckets[bucket] = merge_entries(custom_entries, official_entries)
        result[cache_name] = merged_buckets
    return result


def build_channel(schema_version: str, custom: dict, official: dict) -> dict:
    official_repos = [str(x) for x in official.get("repositories") or []]
    custom_repos = [str(x) for x in custom.get("repositories") or []]

    cache_buckets: list[str] = []
    for cache_name in CACHE_KEYS:
        for bucket in (custom.get(cache_name) or {}):
            cache_buckets.append(bucket)

    channel: dict = {}
    if schema_version.startswith("4"):
        channel["$schema"] = official.get("$schema") or custom.get("$schema") or "sublime://packagecontrol.io/schemas/channel"
    channel["schema_version"] = official.get("schema_version") or schema_version
    channel["repositories"] = merge_repositories(custom_repos + cache_buckets, official_repos)
    channel.update(merge_caches(custom, official))
    return channel


def dump_channel(data: dict, path: str) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=2)
    with open(path, "w", encoding="utf-8", errors="surrogatepass", newline="\n") as fh:
        fh.write(text)
        fh.write("\n")


def count_entries(channel: dict) -> int:
    total = 0
    for cache_name in CACHE_KEYS:
        for entries in (channel.get(cache_name) or {}).values():
            total += len(entries)
    return total


def validate_channel(channel: dict, label: str, previous: dict | None, up_key: str, upstream_ok: bool) -> None:
    if not channel.get("repositories"):
        raise ValueError(f"{label}: repositories is empty")
    if not channel.get("schema_version"):
        raise ValueError(f"{label}: schema_version missing")
    total = count_entries(channel)
    if up_key == V3_KEY and total == 0:
        raise ValueError(f"{label}: package cache is empty")
    if channel.get("libraries_cache") is None and channel.get("packages_cache") is None:
        raise ValueError(f"{label}: no cache section at all")
    if upstream_ok and previous is not None:
        previous_total = count_entries(previous)
        if previous_total and total < previous_total * MIN_SHRINK_RATIO:
            raise ValueError(
                f"{label}: entry count dropped from {previous_total} to {total} "
                f"(< {MIN_SHRINK_RATIO:.0%}), refusing to publish a truncated channel"
            )
    names: set[tuple[str, str]] = set()
    for cache_name in CACHE_KEYS:
        for bucket, entries in (channel.get(cache_name) or {}).items():
            bucket_names: set[str] = set()
            for entry in entries:
                key = entry_identity(entry)
                if key in bucket_names:
                    raise ValueError(f"{label}: duplicate {cache_name} entry '{key}' in bucket {bucket}")
                bucket_names.add(key)
                names.add((cache_name, key))
    log(f"  validated {label}: {total} entries, {len(names)} unique names")

    repos = set(channel["repositories"])
    for cache_name in CACHE_KEYS:
        orphans = [b for b in (channel.get(cache_name) or {}) if b not in repos]
        if orphans:
            raise ValueError(f"{label}: {cache_name} bucket not in repositories: {orphans[:3]}")


def write_json(data: dict, path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    dump_channel(data, path)


def main() -> int:
    os.chdir(ROOT)
    if not OFFLINE and not os.path.isdir(SNAPSHOT_DIR):
        os.makedirs(SNAPSHOT_DIR, exist_ok=True)

    log("== fetch upstream ==")
    upstream_ok = {V3_KEY: False, V4_KEY: False}
    raw_texts: dict[str, str] = {}
    for tag, url in UPSTREAMS:
        if OFFLINE:
            log(f"  offline mode, skip {url}")
            continue
        try:
            raw_texts[tag] = fetch(url)
            upstream_ok[tag] = True
            log(f"  fetched {tag}: {len(raw_texts[tag])} chars from {url}")
        except RuntimeError as exc:
            log(f"  WARNING: {exc}")

    results: dict[str, dict] = {}
    for tag, file_name, overrides_path, schema_version in SOURCES:
        official = None
        if upstream_ok[tag]:
            try:
                official = parse_channel_text(raw_texts[tag], f"upstream {tag}")
            except (JSONDecodeError, ValueError) as exc:
                log(f"  WARNING: upstream {tag} body is invalid ({exc}), keeping previous snapshot")
                official = None
        if official is not None:
            official = {k: v for k, v in official.items() if k in {"$schema", "schema_version", "repositories", *CACHE_KEYS}}
            write_json(official, os.path.join(SNAPSHOT_DIR, file_name))
        if official is None and os.path.exists(os.path.join(SNAPSHOT_DIR, file_name)):
            official = parse_channel_file(os.path.join(SNAPSHOT_DIR, file_name))
            log(f"  fallback to snapshot {SNAPSHOT_DIR}/{file_name}")
        if official is None:
            log(f"  no upstream and no snapshot for {tag}, skipped")
            continue
        channel = build_channel(schema_version, load_optional(overrides_path), official)
        previous = parse_channel_file(file_name) if os.path.exists(file_name) else None
        validate_channel(channel, f"channel_{tag}", previous, tag, upstream_ok[tag])
        write_json(channel, file_name)
        results[tag] = channel

    if not results:
        log("ERROR: nothing could be built, mirror files untouched")
        return 1

    log("== summary ==")
    for tag in sorted(results):
        log(f"  channel_{tag}: {count_entries(results[tag])} entries, {len(results[tag]['repositories'])} repositories")
    if not all(upstream_ok.values()):
        log("  note: at least one upstream source failed, rebuilt from snapshot + overrides")
    return 0


if __name__ == "__main__":
    sys.exit(main())