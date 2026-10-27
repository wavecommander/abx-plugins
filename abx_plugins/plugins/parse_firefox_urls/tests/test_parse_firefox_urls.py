#!/usr/bin/env python3
"""Tests for the parse_firefox_urls extractor (Firefox bookmark JSON backups)."""

import json
import os
import subprocess
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).parent.parent
SCRIPT_PATH = next(PLUGIN_DIR.glob("on_Snapshot__*_parse_firefox_urls.*"), None)
FIXTURE = Path(__file__).parent / "fixtures" / "bookmarks-2026-01-15.json"


def run_hook(tmp_path, input_file, env=None):
    result = subprocess.run(
        [str(SCRIPT_PATH), "--url", input_file.as_uri()],
        cwd=tmp_path,
        env={**os.environ, "SNAP_DIR": str(tmp_path), **(env or {})},
        capture_output=True,
        text=True,
        timeout=30,
    )
    records = [
        json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")
    ]
    return result, records


def of_type(records, record_type):
    return [record for record in records if record.get("type") == record_type]


def read_urls_file(tmp_path):
    path = tmp_path / "parse_firefox_urls" / "urls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def folder(name, children):
    return {
        "title": name,
        "typeCode": 2,
        "type": "text/x-moz-place-container",
        "children": children,
    }


def bookmark(url, title="Bookmark"):
    return {
        "title": title,
        "typeCode": 1,
        "type": "text/x-moz-place",
        "uri": url,
    }


def places_root(*children):
    return {
        "guid": "root________",
        "title": "",
        "typeCode": 2,
        "type": "text/x-moz-place-container",
        "root": "placesRoot",
        "children": [
            {
                "title": "menu",
                "typeCode": 2,
                "type": "text/x-moz-place-container",
                "root": "bookmarksMenuFolder",
                "children": list(children),
            },
        ],
    }


class TestParseFirefoxUrls:
    def test_parses_realistic_backup(self, tmp_path):
        result, records = run_hook(tmp_path, FIXTURE)
        assert result.returncode == 0, result.stderr

        snapshots = of_type(records, "Snapshot")
        assert [s["url"] for s in snapshots] == [
            "https://arxiv.org/abs/1706.03762",
            "https://example.com/papers/untitled",
            "https://example.com/",
            "https://m.example.org/page",
        ]
        assert all(s["plugin"] == "parse_firefox_urls" for s in snapshots)
        assert all(s["depth"] == 1 for s in snapshots)

        paper, untitled, toolbar, mobile = snapshots
        assert paper["title"] == "Attention Is All You Need"
        assert paper["tags"] == "ml,transformers,Research/Papers"
        assert paper["bookmarked_at"] == "2025-01-15T00:00:00+00:00"
        assert paper["last_modified"] == "2025-01-16T00:00:00+00:00"
        assert paper["notes"] == "The original transformer paper"
        assert paper["keyword"] == "attn"

        # Empty title, no tags beyond the folder, no notes/keyword.
        assert "title" not in untitled
        assert untitled["tags"] == "Research/Papers"
        assert untitled["bookmarked_at"] == "2025-01-15T00:03:20+00:00"
        assert "notes" not in untitled
        assert "keyword" not in untitled

        # Bookmarks directly in a root container have no folder tag.
        assert toolbar["title"] == "Example Domain"
        assert "tags" not in toolbar
        assert toolbar["bookmarked_at"] == "2023-11-14T22:20:00+00:00"
        assert mobile["title"] == "Mobile Bookmark"
        assert "tags" not in mobile

        # Favicon URIs, place: queries, about: pages and %s keyword templates.
        emitted = " ".join(s["url"] for s in snapshots)
        for skipped in ("favicon", "place:", "about:", "wikipedia", "%s"):
            assert skipped not in emitted

        tags = [t["name"] for t in of_type(records, "Tag")]
        assert tags == ["Research/Papers", "ml", "transformers"]
        # Tag records precede Snapshot records.
        types = [r["type"] for r in records]
        assert types.index("Snapshot") > max(
            i for i, t in enumerate(types) if t == "Tag"
        )

        archive_result = of_type(records, "ArchiveResult")[0]
        assert archive_result["status"] == "succeeded"
        assert archive_result["output_str"] == "4 URLs parsed"
        stored = read_urls_file(tmp_path)
        assert [row["url"] for row in stored] == [s["url"] for s in snapshots]
        assert stored[0]["tags"] == "ml,transformers,Research/Papers"

    def test_folder_tags_can_be_disabled(self, tmp_path):
        result, records = run_hook(
            tmp_path,
            FIXTURE,
            env={"PARSE_FIREFOX_URLS_FOLDER_TAGS": "False"},
        )
        assert result.returncode == 0, result.stderr
        snapshots = of_type(records, "Snapshot")
        assert snapshots[0]["tags"] == "ml,transformers"
        assert "tags" not in snapshots[1]
        assert [t["name"] for t in of_type(records, "Tag")] == ["ml", "transformers"]
        assert (
            "Research/Papers"
            not in (tmp_path / "parse_firefox_urls" / "urls.jsonl").read_text()
        )

    def test_detects_backup_by_root_guid_and_type_without_root_key(self, tmp_path):
        for top_level in (
            {"guid": "root________", "children": [bookmark("https://a.example/")]},
            {
                "type": "text/x-moz-place-container",
                "children": [bookmark("https://a.example/")],
            },
        ):
            input_file = tmp_path / "bookmarks.json"
            input_file.write_text(json.dumps(top_level))
            result, records = run_hook(tmp_path, input_file)
            assert result.returncode == 0, result.stderr
            assert [s["url"] for s in of_type(records, "Snapshot")] == [
                "https://a.example/",
            ]

    @pytest.mark.parametrize(
        "uri",
        [
            "place:parent=toolbar_____",
            "about:blank",
            "javascript:alert(1)",
            "data:text/html,hi",
            "chrome://browser/content/browser.xhtml",
            "moz-extension://abc/page.html",
            "file:///tmp/x.html",
            "https://search.example/?q=%s",
            "PLACE:sort=1",
        ],
    )
    def test_skips_non_archivable_uris(self, tmp_path, uri):
        input_file = tmp_path / "bookmarks.json"
        input_file.write_text(
            json.dumps(places_root(bookmark(uri), bookmark("https://keep.example/"))),
        )
        result, records = run_hook(tmp_path, input_file)
        assert result.returncode == 0, result.stderr
        assert [s["url"] for s in of_type(records, "Snapshot")] == [
            "https://keep.example/",
        ]

    def test_second_and_millisecond_timestamps(self, tmp_path):
        first = bookmark("https://seconds.example/")
        first["dateAdded"] = 1736899200
        second = bookmark("https://millis.example/")
        second["dateAdded"] = 1736899200000
        third = bookmark("https://garbage.example/")
        third["dateAdded"] = "not-a-number"
        input_file = tmp_path / "bookmarks.json"
        input_file.write_text(json.dumps(places_root(first, second, third)))
        result, records = run_hook(tmp_path, input_file)
        assert result.returncode == 0, result.stderr
        snapshots = of_type(records, "Snapshot")
        assert snapshots[0]["bookmarked_at"] == "2025-01-15T00:00:00+00:00"
        assert snapshots[1]["bookmarked_at"] == "2025-01-15T00:00:00+00:00"
        assert "bookmarked_at" not in snapshots[2]

    def test_depth_is_incremented(self, tmp_path):
        result = subprocess.run(
            [str(SCRIPT_PATH), "--url", FIXTURE.as_uri(), "--depth", "2"],
            cwd=tmp_path,
            env={**os.environ, "SNAP_DIR": str(tmp_path)},
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        depths = {
            json.loads(line)["depth"]
            for line in result.stdout.splitlines()
            if line.startswith("{") and json.loads(line)["type"] == "Snapshot"
        }
        assert depths == {3}

    def test_long_folder_path_drops_leading_segments(self, tmp_path):
        folders = ["A" * 60, "B" * 30, "C" * 30]
        tree = bookmark("https://deep.example/")
        for name in reversed(folders):
            tree = folder(name, [tree])
        input_file = tmp_path / "bookmarks.json"
        input_file.write_text(json.dumps(places_root(tree)))
        result, records = run_hook(tmp_path, input_file)
        assert result.returncode == 0, result.stderr
        expected = "B" * 30 + "/" + "C" * 30
        assert of_type(records, "Snapshot")[0]["tags"] == expected
        assert [t["name"] for t in of_type(records, "Tag")] == [expected]

        # A path that exactly fits (100 chars) is kept whole.
        folders = ["A" * 49, "B" * 50]
        tree = bookmark("https://deep.example/")
        for name in reversed(folders):
            tree = folder(name, [tree])
        input_file.write_text(json.dumps(places_root(tree)))
        result, records = run_hook(tmp_path, input_file)
        assert of_type(records, "Snapshot")[0]["tags"] == "/".join(folders)
        assert len("/".join(folders)) == 100

    def test_blank_folder_titles_are_skipped_and_tags_deduplicated(self, tmp_path):
        item = bookmark("https://dup.example/")
        item["tags"] = "Work, Work ,,Work/Notes"
        tree = folder("Work", [folder("  ", [folder(" Notes ", [item])])])
        input_file = tmp_path / "bookmarks.json"
        input_file.write_text(json.dumps(places_root(tree)))
        result, records = run_hook(tmp_path, input_file)
        assert result.returncode == 0, result.stderr
        assert of_type(records, "Snapshot")[0]["tags"] == "Work,Work/Notes"
        assert [t["name"] for t in of_type(records, "Tag")] == ["Work", "Work/Notes"]


class TestParseFirefoxUrlsNoResults:
    @pytest.mark.parametrize(
        ("name", "content"),
        [
            ("object.json", json.dumps({"url": "https://example.com", "title": "x"})),
            ("list.json", json.dumps([{"url": "https://example.com"}])),
            (
                "urls.jsonl",
                '{"url": "https://example.com/a"}\n{"url": "https://example.com/b"}\n',
            ),
            (
                "page.html",
                "<html><body><a href='https://example.com'>x</a></body></html>",
            ),
            ("notes.txt", "Visit https://example.com for details."),
            ("invalid.json", '{"guid": "root________", "children": ['),
            ("empty.json", ""),
        ],
    )
    def test_non_firefox_input_yields_noresults(self, tmp_path, name, content):
        input_file = tmp_path / name
        input_file.write_text(content)
        stale = tmp_path / "parse_firefox_urls" / "urls.jsonl"
        stale.parent.mkdir()
        stale.write_text('{"url": "https://stale.example/"}\n')

        result, records = run_hook(tmp_path, input_file)

        assert result.returncode == 0, result.stderr
        assert of_type(records, "Snapshot") == []
        assert of_type(records, "Tag") == []
        archive_result = of_type(records, "ArchiveResult")[0]
        assert archive_result["status"] == "noresults"
        assert archive_result["output_str"] == "0 URLs parsed"
        assert not stale.exists()

    def test_places_tree_without_bookmarks_yields_noresults(self, tmp_path):
        input_file = tmp_path / "bookmarks.json"
        input_file.write_text(json.dumps(places_root(folder("Empty", []))))
        result, records = run_hook(tmp_path, input_file)
        assert result.returncode == 0, result.stderr
        assert of_type(records, "ArchiveResult")[0]["status"] == "noresults"
        assert not (tmp_path / "parse_firefox_urls" / "urls.jsonl").exists()

    def test_http_fetch_failure_yields_noresults(self, tmp_path):
        result = subprocess.run(
            [str(SCRIPT_PATH), "--url", "http://127.0.0.1:1/bookmarks.json"],
            cwd=tmp_path,
            env={**os.environ, "SNAP_DIR": str(tmp_path)},
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        records = [
            json.loads(line)
            for line in result.stdout.splitlines()
            if line.startswith("{")
        ]
        assert of_type(records, "ArchiveResult")[0]["status"] == "noresults"

    def test_missing_file_fails(self, tmp_path):
        result, records = run_hook(tmp_path, tmp_path / "missing.json")
        assert result.returncode == 1
        assert of_type(records, "ArchiveResult")[0]["status"] == "failed"
