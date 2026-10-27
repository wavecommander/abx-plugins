#!/usr/bin/env python3
"""Tests for notes, folder-path tags, and extra fields from Netscape bookmarks."""

import json
import os
import subprocess
from pathlib import Path

PLUGIN_DIR = Path(__file__).parent.parent
SCRIPT_PATH = next(PLUGIN_DIR.glob("on_Snapshot__*_parse_netscape_urls.*"), None)

FIREFOX_EXPORT = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<!-- This is an automatically generated file. -->
<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks Menu</H1>

<DL><p>
    <DT><H3 ADD_DATE="1609459200" LAST_MODIFIED="1609545600" PERSONAL_TOOLBAR_FOLDER="true">Bookmarks Toolbar</H3>
    <DD>Toolbar folder description
    <DL><p>
        <DT><A HREF="https://toolbar.example.com/" ADD_DATE="1609459200" ICON_URI="https://toolbar.example.com/favicon.ico">Toolbar Link</A>
        <DT><H3 ADD_DATE="1609459200" LAST_MODIFIED="1609545600">Research</H3>
        <DD>Folder description that is not a bookmark note
        <DL><p>
            <DT><H3 ADD_DATE="1609459200" LAST_MODIFIED="1609545600">Papers</H3>
            <DL><p>
                <DT><A HREF="https://arxiv.org/abs/1706.03762" ADD_DATE="1609459200" LAST_MODIFIED="1609632000" TAGS="ml,attention" SHORTCUTURL="attn" ICON_URI="https://arxiv.org/icon.png" ICON="data:image/png;base64,iVBORw0KGgo=">Attention Is All You Need</A>
                <DD>Transformer paper &amp; <b>notes</b>
                with a second line
                <DT><A HREF="https://example.com/no-notes" ADD_DATE="1609459200">No Notes</A>
            </DL><p>
            <DT><A HREF="https://example.com/research-root" ADD_DATE="1609459200" TAGS="ml">Research Root</A>
            <DD>   
        </DL><p>
    </DL><p>
    <DT><A HREF="https://example.com/menu-root" ADD_DATE="1609459200">Menu Root</A>
    <DD>Top-level note
</DL><p>
"""

CHROME_EXPORT = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks</H1>
<DL><p>
    <DT><H3 ADD_DATE="1609459200" LAST_MODIFIED="1609459200" PERSONAL_TOOLBAR_FOLDER="true">Bookmarks bar</H3>
    <DL><p>
        <DT><A HREF="https://bar.example.com/" ADD_DATE="1609459200">On The Bar</A>
        <DT><H3 ADD_DATE="1609459200" LAST_MODIFIED="1609459200">News</H3>
        <DL><p>
            <DT><A HREF="https://news.example.com/" ADD_DATE="1609459200">News Site</A>
        </DL><p>
    </DL><p>
    <DT><H3 ADD_DATE="1609459200" LAST_MODIFIED="1609459200">Other bookmarks</H3>
    <DL><p>
        <DT><A HREF="https://other.example.com/" ADD_DATE="1609459200">In Other</A>
    </DL><p>
</DL><p>
"""


def run_hook(tmp_path, html, extra_env=None):
    input_file = tmp_path / "bookmarks.html"
    input_file.write_text(html)
    env = {**os.environ, **(extra_env or {})}
    result = subprocess.run(
        [str(SCRIPT_PATH), "--url", input_file.as_uri()],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    records = [
        json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")
    ]
    snapshots = {r["url"]: r for r in records if r.get("type") == "Snapshot"}
    tags = [r["name"] for r in records if r.get("type") == "Tag"]
    return snapshots, tags, records


class TestFirefoxMetadata:
    def test_notes_folder_tags_and_extra_fields(self, tmp_path):
        snapshots, tags, _ = run_hook(tmp_path, FIREFOX_EXPORT)

        assert set(snapshots) == {
            "https://toolbar.example.com/",
            "https://arxiv.org/abs/1706.03762",
            "https://example.com/no-notes",
            "https://example.com/research-root",
            "https://example.com/menu-root",
        }

        paper = snapshots["https://arxiv.org/abs/1706.03762"]
        assert paper["title"] == "Attention Is All You Need"
        assert paper["notes"] == "Transformer paper & notes with a second line"
        assert paper["tags"] == "ml,attention,Research/Papers"
        assert paper["bookmarked_at"] == "2021-01-01T00:00:00+00:00"
        assert paper["last_modified"] == "2021-01-03T00:00:00+00:00"
        assert paper["keyword"] == "attn"

        no_notes = snapshots["https://example.com/no-notes"]
        assert "notes" not in no_notes
        assert no_notes["tags"] == "Research/Papers"
        assert "keyword" not in no_notes
        assert "last_modified" not in no_notes

        # Whitespace-only <DD> emits no notes; user tags are not duplicated.
        research_root = snapshots["https://example.com/research-root"]
        assert "notes" not in research_root
        assert research_root["tags"] == "ml,Research"

        # Browser root containers never appear in the folder path.
        toolbar = snapshots["https://toolbar.example.com/"]
        assert "tags" not in toolbar
        # The toolbar's folder-level <DD> is not attached to its first bookmark.
        assert "notes" not in toolbar

        menu_root = snapshots["https://example.com/menu-root"]
        assert "tags" not in menu_root
        assert menu_root["notes"] == "Top-level note"

        assert set(tags) == {
            "ml",
            "attention",
            "Research/Papers",
            "Research",
        }
        assert not any("Toolbar" in tag for tag in tags)

    def test_folder_dd_not_attached_and_icons_not_urls(self, tmp_path):
        snapshots, _, records = run_hook(tmp_path, FIREFOX_EXPORT)
        for record in snapshots.values():
            assert "Folder description" not in record.get("notes", "")
            assert "Toolbar folder description" not in record.get("notes", "")
        emitted = json.dumps(records)
        assert "favicon.ico" not in emitted
        assert "arxiv.org/icon.png" not in emitted
        assert "data:image/png" not in emitted
        assert "iVBORw0KGgo" not in emitted

    def test_folder_tags_can_be_disabled(self, tmp_path):
        snapshots, tags, _ = run_hook(
            tmp_path,
            FIREFOX_EXPORT,
            {"PARSE_NETSCAPE_URLS_FOLDER_TAGS": "False"},
        )
        assert snapshots["https://arxiv.org/abs/1706.03762"]["tags"] == "ml,attention"
        assert "tags" not in snapshots["https://example.com/no-notes"]
        assert snapshots["https://example.com/research-root"]["tags"] == "ml"
        assert set(tags) == {"ml", "attention"}
        # Notes and extra fields are unaffected by the folder tag option.
        paper = snapshots["https://arxiv.org/abs/1706.03762"]
        assert paper["notes"] == "Transformer paper & notes with a second line"
        assert paper["keyword"] == "attn"


class TestChromeMetadata:
    def test_chrome_roots_are_excluded(self, tmp_path):
        snapshots, tags, _ = run_hook(tmp_path, CHROME_EXPORT)
        assert "tags" not in snapshots["https://bar.example.com/"]
        assert snapshots["https://news.example.com/"]["tags"] == "News"
        # "Other bookmarks" is the browser's unfiled root only when flagged;
        # Chrome writes it as a plain <H3>, so it is kept like any other folder.
        assert snapshots["https://other.example.com/"]["tags"] == "Other bookmarks"
        assert set(tags) == {"News", "Other bookmarks"}

    def test_unfiled_bookmarks_folder_attribute_is_excluded(self, tmp_path):
        html = (
            "<!DOCTYPE NETSCAPE-Bookmark-file-1>\n<DL><p>\n"
            '<DT><H3 UNFILED_BOOKMARKS_FOLDER="true">Other Bookmarks</H3>\n<DL><p>\n'
            '<DT><A HREF="https://unfiled.example.com/">Unfiled</A>\n'
            '<DT><H3 ADD_DATE="1">Sub</H3>\n<DL><p>\n'
            '<DT><A HREF="https://sub.example.com/">Sub Link</A>\n'
            "</DL><p>\n</DL><p>\n</DL><p>\n"
        )
        snapshots, tags, _ = run_hook(tmp_path, html)
        assert "tags" not in snapshots["https://unfiled.example.com/"]
        assert snapshots["https://sub.example.com/"]["tags"] == "Sub"
        assert tags == ["Sub"]


class TestMalformedStructure:
    def test_unclosed_and_unquoted_markup_yields_every_bookmark(self, tmp_path):
        html = (
            "<!doctype netscape-bookmark-file-1>\n"
            "<dl><p>\n"
            "<dt><h3 add_date=1>Outer</h3>\n"
            "<dl><p>\n"
            "<dt><a href=https://one.example.com/ shortcuturl=one>One</a>\n"
            "<dd>first note\n"
            "<dt><h3>Inner</h3>\n"
            "<dl><p>\n"
            "<dt><a href='https://two.example.com/'>Two</a>\n"
            '<dt><A HREF="https://three.example.com/">Three</A>\n'
            "<dd>third note\n"
            # No closing </DL> tags at all.
        )
        snapshots, _, _ = run_hook(tmp_path, html)
        assert list(snapshots) == [
            "https://one.example.com/",
            "https://two.example.com/",
            "https://three.example.com/",
        ]
        one = snapshots["https://one.example.com/"]
        assert one["tags"] == "Outer"
        assert one["notes"] == "first note"
        assert one["keyword"] == "one"
        assert snapshots["https://two.example.com/"]["tags"] == "Outer/Inner"
        assert "notes" not in snapshots["https://two.example.com/"]
        three = snapshots["https://three.example.com/"]
        assert three["tags"] == "Outer/Inner"
        assert three["notes"] == "third note"

    def test_extra_closing_dl_does_not_break_parsing(self, tmp_path):
        html = (
            "<!DOCTYPE NETSCAPE-Bookmark-file-1>\n</DL></DL>\n<DL>\n"
            '<DT><H3>A</H3>\n<DL>\n<DT><A HREF="https://a.example.com/">A1</A>\n'
            '</DL></DL></DL>\n<DT><A HREF="https://b.example.com/">B1</A>\n'
        )
        snapshots, _, _ = run_hook(tmp_path, html)
        assert snapshots["https://a.example.com/"]["tags"] == "A"
        assert "tags" not in snapshots["https://b.example.com/"]

    def test_folder_without_dl_does_not_leak_into_next_folder(self, tmp_path):
        html = (
            "<!DOCTYPE NETSCAPE-Bookmark-file-1>\n<DL>\n"
            "<DT><H3>Empty</H3>\n"
            '<DT><A HREF="https://x.example.com/">X</A>\n'
            "<DT><H3>Real</H3>\n<DL>\n"
            '<DT><A HREF="https://y.example.com/">Y</A>\n</DL>\n</DL>\n'
        )
        snapshots, _, _ = run_hook(tmp_path, html)
        assert "tags" not in snapshots["https://x.example.com/"]
        assert snapshots["https://y.example.com/"]["tags"] == "Real"

    def test_folder_name_commas_do_not_split_tag(self, tmp_path):
        html = (
            "<!DOCTYPE NETSCAPE-Bookmark-file-1>\n<DL>\n"
            "<DT><H3>Reading, Later</H3>\n<DL>\n"
            '<DT><A HREF="https://c.example.com/">C</A>\n</DL>\n</DL>\n'
        )
        snapshots, tags, _ = run_hook(tmp_path, html)
        assert snapshots["https://c.example.com/"]["tags"] == "Reading Later"
        assert tags == ["Reading Later"]


class TestFolderPathTruncation:
    def test_long_folder_path_drops_leading_segments(self, tmp_path):
        names = ["a" * 40, "b" * 40, "c" * 40, "d" * 10]
        html = "<!DOCTYPE NETSCAPE-Bookmark-file-1>\n<DL>\n"
        for name in names:
            html += f"<DT><H3>{name}</H3>\n<DL>\n"
        html += '<DT><A HREF="https://deep.example.com/">Deep</A>\n'
        html += "</DL>\n" * (len(names) + 1)

        snapshots, tags, _ = run_hook(tmp_path, html)
        folder_tag = snapshots["https://deep.example.com/"]["tags"]
        # Full path is 40+1+40+1+40+1+10 = 133 chars; dropping "a"*40 leaves 92.
        assert folder_tag == "/".join(names[1:])
        assert len(folder_tag) == 92
        assert tags == [folder_tag]

    def test_exactly_100_chars_is_kept(self, tmp_path):
        names = ["a" * 49, "b" * 50]
        html = "<!DOCTYPE NETSCAPE-Bookmark-file-1>\n<DL>\n"
        for name in names:
            html += f"<DT><H3>{name}</H3>\n<DL>\n"
        html += (
            '<DT><A HREF="https://edge.example.com/">Edge</A>\n</DL>\n</DL>\n</DL>\n'
        )
        snapshots, _, _ = run_hook(tmp_path, html)
        assert snapshots["https://edge.example.com/"]["tags"] == "/".join(names)
        assert len("/".join(names)) == 100
