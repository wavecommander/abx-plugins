#!/usr/bin/env -S abxpkg run --script --deps-from=./config.json:required_binaries python3
# /// script
# requires-python = ">=3.12"
# ///
#
# Parse Firefox bookmark JSON backups and extract URLs.
#
# This is a standalone extractor that can run without ArchiveBox.
# It reads Firefox Places bookmark backups (bookmarks-YYYY-MM-DD.json, from
# Library > Import and Backup > Backup) from SNAP_DIR/staticfile/*.txt or an HTTP URL.
#
# Usage:
#     ./on_Snapshot__72_parse_firefox_urls.py --url=<url>
# Output: Appends discovered URLs to SNAP_DIR/parse_firefox_urls/urls.jsonl
#
import sys
import json
import os
from pathlib import Path

from abx_plugins.plugins.base.url_cleaning import sanitize_extracted_url
from abx_plugins.plugins.base.utils import (
    emit_archive_result_record,
    emit_snapshot_record,
    emit_tag_record,
    load_config,
    parse_bookmark_timestamp,
    read_snapshot_source_text,
    write_text_atomic,
)

import rich_click as click

PLUGIN_NAME = "parse_firefox_urls"
PLUGIN_DIR = Path(__file__).resolve().parent.name
CONFIG = load_config()
SNAP_DIR = Path(CONFIG.SNAP_DIR or ".").resolve()
OUTPUT_DIR = SNAP_DIR / PLUGIN_DIR
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
os.chdir(OUTPUT_DIR)
URLS_FILE = Path("urls.jsonl")
NORESULTS_OUTPUT = "0 URLs parsed"

PLACE_TYPE = "text/x-moz-place"
CONTAINER_TYPE = "text/x-moz-place-container"
SEPARATOR_TYPE = "text/x-moz-place-separator"
DESCRIPTION_ANNO = "bookmarkProperties/description"
# Internal, non-web, and keyword-search URIs are not archivable bookmarks.
SKIPPED_URI_PREFIXES = (
    "place:",
    "about:",
    "javascript:",
    "data:",
    "chrome:",
    "moz-extension:",
    "file:",
)
MAX_TAG_LENGTH = 100  # ArchiveBox Tag.name limit


def looks_like_firefox_bookmarks(data: object) -> bool:
    """Only accept the root node of a Firefox Places tree, not arbitrary JSON."""
    if not isinstance(data, dict):
        return False
    node_type = data.get("type")
    return (
        data.get("root") == "placesRoot"
        or data.get("guid") == "root________"
        or (isinstance(node_type, str) and node_type.startswith(PLACE_TYPE))
    )


def is_node_type(node: dict, type_name: str, type_code: int) -> bool:
    return node.get("type") == type_name or node.get("typeCode") == type_code


def build_folder_tag(folders: list[str]) -> str:
    """Join folder titles with `/`, dropping leading segments to fit Tag.name."""
    segments = [title.strip() for title in folders if title and title.strip()]
    while len(segments) > 1 and len("/".join(segments)) > MAX_TAG_LENGTH:
        segments.pop(0)
    return "/".join(segments)[:MAX_TAG_LENGTH].strip()


def iter_bookmarks(node: dict, folders: list[str] | None = None):
    """Yield (bookmark node, ancestor folder titles) for every bookmark under node."""
    folders = folders or []
    if is_node_type(node, SEPARATOR_TYPE, 3):
        return

    if is_node_type(node, PLACE_TYPE, 1):
        if isinstance(node.get("uri"), str):
            yield node, folders
        return

    children = node.get("children")
    if not isinstance(children, list):
        return
    # Menu/toolbar/unfiled/mobile roots carry a `root` key and are not folders.
    title = node.get("title")
    if "root" not in node and isinstance(title, str):
        folders = [*folders, title]
    for child in children:
        if isinstance(child, dict):
            yield from iter_bookmarks(child, folders)


def bookmark_description(node: dict) -> str:
    annos = node.get("annos")
    if not isinstance(annos, list):
        return ""
    for anno in annos:
        if isinstance(anno, dict) and anno.get("name") == DESCRIPTION_ANNO:
            value = anno.get("value")
            return value.strip() if isinstance(value, str) else ""
    return ""


def is_archivable_uri(uri: str) -> bool:
    lowered = uri.strip().lower()
    return not lowered.startswith(SKIPPED_URI_PREFIXES) and "%s" not in lowered


def timestamp_iso(value: object) -> str | None:
    dt = (
        parse_bookmark_timestamp(value)
        if isinstance(value, (str, int, float))
        else None
    )
    return dt.isoformat() if dt else None


def emit_result(status: str, output_str: str) -> None:
    """Emit final ArchiveResult JSONL plus a short stderr summary."""
    emit_archive_result_record(status, output_str)
    if output_str:
        click.echo(output_str, err=True)


def persist_records(records: list[dict]) -> tuple[str, str]:
    """Write extracted URLs when present, otherwise clear stale output after success."""
    if records:
        write_text_atomic(
            URLS_FILE,
            "\n".join(json.dumps(record) for record in records) + "\n",
        )
        return "succeeded", f"{len(records)} URLs parsed"

    URLS_FILE.unlink(missing_ok=True)
    return "noresults", NORESULTS_OUTPUT


@click.command(
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.option("--url", required=True, help="Firefox bookmark JSON file URL to parse")
@click.option("--depth", type=int, default=0, help="Current depth level")
def main(
    url: str,
    depth: int = 0,
):
    """Parse Firefox bookmark JSON backups and extract URLs."""
    print("parsing 1 files for urls...")
    try:
        content = read_snapshot_source_text(
            url,
            snap_dir=SNAP_DIR,
            timeout=CONFIG.TIMEOUT,
            user_agent=CONFIG.USER_AGENT,
        )
    except Exception as e:
        if url.startswith(("http://", "https://")):
            # Snapshot URL fetching is only a fallback when no staticfile import
            # artifact exists. Normal webpages, blocked requests, or transient
            # network errors should not make this parser hook look broken.
            status, output_str = persist_records([])
            print(output_str)
            emit_result(status, output_str)
            sys.exit(0)
        emit_result("failed", f"Failed to fetch {url}: {e}")
        sys.exit(1)

    try:
        data = json.loads(content.strip())
    except (ValueError, RecursionError):
        data = None

    if not looks_like_firefox_bookmarks(data):
        status, output_str = persist_records([])
        print(output_str)
        emit_result(status, output_str)
        sys.exit(0)

    urls_found = []
    all_tags = set()

    for node, folders in iter_bookmarks(data):
        if not is_archivable_uri(node["uri"]):
            continue
        bookmark_url = sanitize_extracted_url(node["uri"])
        if not bookmark_url:
            continue

        tags = []
        node_tags = node.get("tags")
        if isinstance(node_tags, str):
            tags.extend(tag.strip() for tag in node_tags.split(","))
        if CONFIG.PARSE_FIREFOX_URLS_FOLDER_TAGS:
            tags.append(build_folder_tag(folders))
        tags = list(dict.fromkeys(tag for tag in tags if tag))

        entry = {
            "type": "Snapshot",
            "url": bookmark_url,
            "plugin": PLUGIN_NAME,
            "depth": depth + 1,
        }
        title = node.get("title")
        if isinstance(title, str) and title.strip():
            entry["title"] = title.strip()
        if bookmarked_at := timestamp_iso(node.get("dateAdded")):
            entry["bookmarked_at"] = bookmarked_at
        if tags:
            entry["tags"] = ",".join(tags)
            all_tags.update(tags)
        if notes := bookmark_description(node):
            entry["notes"] = notes
        if last_modified := timestamp_iso(node.get("lastModified")):
            entry["last_modified"] = last_modified
        keyword = node.get("keyword")
        if isinstance(keyword, str) and keyword.strip():
            entry["keyword"] = keyword.strip()

        urls_found.append(entry)

    # Emit Tag records first (to stdout as JSONL)
    for tag_name in sorted(all_tags):
        emit_tag_record(tag_name)

    # Emit Snapshot records (to stdout as JSONL)
    for entry in urls_found:
        emit_snapshot_record(entry)

    # Emit ArchiveResult record to mark completion
    status, output_str = persist_records(urls_found)
    print(output_str)
    emit_result(status, output_str)
    sys.exit(0)


if __name__ == "__main__":
    main()
