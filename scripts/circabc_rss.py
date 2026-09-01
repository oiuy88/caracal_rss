#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import html
import json
import re
import sys
import time
import xml.etree.ElementTree as ET

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import (
    parse_qs,
    urlencode,
    urljoin,
    urlparse,
    urlunparse,
)

import requests
import yaml
from bs4 import BeautifulSoup


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]

CONFIG_FILE = ROOT / "config.yml"
STATE_FILE = ROOT / "state" / "state.json"
FEED_FILE = ROOT / "docs" / "feed.xml"


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

USER_AGENT = (
    "Mozilla/5.0 "
    "(compatible; CARACAL-CIRCABC-RSS/1.0; "
    "+https://github.com/)"
)


DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,"
        "application/xhtml+xml,"
        "application/xml;q=0.9,"
        "*/*;q=0.8"
    ),
    "Accept-Language": "en-GB,en;q=0.9,de;q=0.8",
    "Cache-Control": "no-cache",
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Item:
    id: str
    title: str
    url: str

    folder_url: str = ""
    folder_name: str = ""

    modified: str = ""
    created: str = ""
    version: str = ""
    author: str = ""

    description: str = ""
    download_url: str = ""

    is_folder: bool = False

    # Filled in by change detection.
    change_type: str = ""


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def clean(value: str | None) -> str:
    if not value:
        return ""

    value = html.unescape(value)

    return re.sub(
        r"\s+",
        " ",
        value,
    ).strip()


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        raise RuntimeError(
            f"Missing configuration file: {CONFIG_FILE}"
        )

    with CONFIG_FILE.open(
        "r",
        encoding="utf-8",
    ) as fh:
        return yaml.safe_load(fh) or {}


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}

    try:
        with STATE_FILE.open(
            "r",
            encoding="utf-8",
        ) as fh:
            return json.load(fh)

    except (
        OSError,
        json.JSONDecodeError,
    ):
        print(
            "Warning: state.json could not be read. "
            "Starting with empty state.",
            file=sys.stderr,
        )

        return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with STATE_FILE.open(
        "w",
        encoding="utf-8",
    ) as fh:
        json.dump(
            state,
            fh,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )


def normalize_url(url: str) -> str:
    """
    Remove pagination/query parameters when using a URL as a
    stable folder identifier.
    """

    parsed = urlparse(url)

    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path.rstrip("/"),
            parsed.params,
            "",
            "",
        )
    )


def is_circabc_url(url: str) -> bool:
    parsed = urlparse(url)

    return parsed.netloc.lower() in {
        "circabc.europa.eu",
        "www.circabc.europa.eu",
    }


def make_id(url: str) -> str:
    return hashlib.sha256(
        url.encode("utf-8")
    ).hexdigest()


def xml_text(value: str) -> str:
    return html.escape(
        value or "",
        quote=True,
    )


# ---------------------------------------------------------------------------
# CIRCABC client
# ---------------------------------------------------------------------------

class CircabcClient:

    def __init__(
        self,
        config: dict,
    ):
        self.config = config

        self.base_url = (
            config.get(
                "circabc_base_url",
                "https://circabc.europa.eu",
            )
            .rstrip("/")
        )

        self.timeout = int(
            config.get(
                "request_timeout",
                30,
            )
        )

        self.retries = int(
            config.get(
                "request_retries",
                3,
            )
        )

        self.delay = float(
            config.get(
                "request_delay",
                0.75,
            )
        )

        self.items_per_page = int(
            config.get(
                "items_per_page",
                100,
            )
        )

        self.session = requests.Session()

        self.session.headers.update(
            DEFAULT_HEADERS
        )


    # -----------------------------------------------------------------------
    # HTTP
    # -----------------------------------------------------------------------

    def get(
        self,
        url: str,
    ) -> requests.Response:

        last_error = None

        for attempt in range(
            1,
            self.retries + 1,
        ):

            try:

                print(
                    f"HTTP GET: {url}"
                )

                response = self.session.get(
                    url,
                    timeout=self.timeout,
                    allow_redirects=True,
                )

                print(
                    f"HTTP {response.status_code}: "
                    f"{response.url}"
                )

                if response.status_code == 404:

                    print(
                        "\nWARNING: CIRCABC returned 404."
                    )

                    print(
                        "Requested URL:"
                    )

                    print(
                        f"  {url}"
                    )

                    print(
                        "Final URL:"
                    )

                    print(
                        f"  {response.url}"
                    )

                    print(
                        "Content-Type:"
                    )

                    print(
                        f"  {response.headers.get('content-type')}"
                    )

                    # Save a diagnostic copy.
                    diagnostic = (
                        ROOT
                        / "state"
                        / "last_response.html"
                    )

                    diagnostic.parent.mkdir(
                        parents=True,
                        exist_ok=True,
                    )

                    diagnostic.write_text(
                        response.text,
                        encoding="utf-8",
                    )

                    raise RuntimeError(
                        "CIRCABC returned HTTP 404. "
                        "The requested library may require "
                        "authentication, may no longer exist, "
                        "or CIRCABC may have changed its URL structure."
                    )

                response.raise_for_status()

                time.sleep(
                    self.delay
                )

                return response

            except requests.RequestException as exc:

                last_error = exc

                print(
                    f"Request failed "
                    f"{attempt}/{self.retries}: "
                    f"{exc}",
                    file=sys.stderr,
                )

                if attempt < self.retries:

                    time.sleep(
                        attempt * 2
                    )

        raise RuntimeError(
            f"Could not fetch {url}: "
            f"{last_error}"
        )


    # -----------------------------------------------------------------------
    # Pagination
    # -----------------------------------------------------------------------

    def page_url(
        self,
        folder_url: str,
        page: int,
    ) -> str:

        parsed = urlparse(
            folder_url
        )

        query = parse_qs(
            parsed.query,
            keep_blank_values=True,
        )

        # CIRCABC uses p/n for pagination in the UI.
        query["p"] = [
            str(page)
        ]

        query["n"] = [
            str(self.items_per_page)
        ]

        query["sort"] = [
            "modified_DESC"
        ]

        new_query = urlencode(
            query,
            doseq=True,
        )

        return urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                new_query,
                parsed.fragment,
            )
        )


    # -----------------------------------------------------------------------
    # Parse metadata
    # -----------------------------------------------------------------------

    @staticmethod
    def metadata(
        text: str,
        patterns: list[str],
    ) -> str:

        for pattern in patterns:

            match = re.search(
                pattern,
                text,
                flags=re.IGNORECASE,
            )

            if match:

                return clean(
                    match.group(1)
                )

        return ""


    # -----------------------------------------------------------------------
    # Extract folder/document links
    # -----------------------------------------------------------------------

    def parse_page(
        self,
        html_text: str,
        current_url: str,
        folder_url: str,
        folder_name: str,
    ) -> tuple[list[Item], list[tuple[str, str]]]:

        soup = BeautifulSoup(
            html_text,
            "html.parser",
        )

        items = []
        folders = []

        seen = set()

        # -------------------------------------------------------------------
        # First pass: links
        # -------------------------------------------------------------------

        for link in soup.find_all(
            "a",
            href=True,
        ):

            href = link.get(
                "href",
                "",
            ).strip()

            if not href:
                continue

            absolute = urljoin(
                current_url,
                href,
            )

            if not is_circabc_url(
                absolute
            ):
                continue

            parsed = urlparse(
                absolute
            )

            title = clean(
                link.get_text(
                    " ",
                    strip=True,
                )
            )

            if not title:
                continue

            # ---------------------------------------------------------------
            # Folder links
            # ---------------------------------------------------------------

            folder_match = re.search(
                r"/ui/group/"
                r"([0-9a-fA-F-]{36})"
                r"/library/"
                r"([0-9a-fA-F-]{36})",
                parsed.path,
            )

            if folder_match:

                group_id = folder_match.group(1)
                library_id = folder_match.group(2)

                # The root folder itself.
                if normalize_url(
                    absolute
                ) == normalize_url(
                    folder_url
                ):
                    continue

                key = (
                    "folder",
                    group_id,
                    library_id,
                )

                if key in seen:
                    continue

                seen.add(
                    key
                )

                folder_item = Item(
                    id=library_id,
                    title=title,
                    url=normalize_url(
                        absolute
                    ),
                    folder_url=folder_url,
                    folder_name=folder_name,
                    is_folder=True,
                )

                items.append(
                    folder_item
                )

                folders.append(
                    (
                        normalize_url(
                            absolute
                        ),
                        title,
                    )
                )

                continue

            # ---------------------------------------------------------------
            # Direct document/download links
            # ---------------------------------------------------------------

            is_document = (
                "/rest/download/" in parsed.path
                or "/d/" in parsed.path
            )

            if not is_document:
                continue

            key = (
                "document",
                absolute,
            )

            if key in seen:
                continue

            seen.add(
                key
            )

            # ---------------------------------------------------------------
            # Find surrounding card/container
            # ---------------------------------------------------------------

            container = link

            for _ in range(8):

                if not container.parent:
                    break

                container = container.parent

            container_text = clean(
                container.get_text(
                    " ",
                    strip=True,
                )
            )

            # ---------------------------------------------------------------
            # Metadata
            # ---------------------------------------------------------------

            modified = self.metadata(
                container_text,
                [
                    r"modified\s*:?\s*(.+?)(?=\s+created|\s+author|\s+version|$)",
                    r"last modified\s*:?\s*(.+?)(?=\s+created|\s+author|\s+version|$)",
                    r"geändert\s*:?\s*(.+?)(?=\s+erstellt|\s+autor|\s+version|$)",
                ],
            )

            created = self.metadata(
                container_text,
                [
                    r"created\s*:?\s*(.+?)(?=\s+modified|\s+author|\s+version|$)",
                    r"erstellt\s*:?\s*(.+?)(?=\s+geändert|\s+autor|\s+version|$)",
                ],
            )

            version = self.metadata(
                container_text,
                [
                    r"version\s*:?\s*([0-9A-Za-z._-]+)",
                    r"versions?\s*:?\s*([0-9A-Za-z._-]+)",
                ],
            )

            author = self.metadata(
                container_text,
                [
                    r"author\s*:?\s*(.+?)(?=\s+modified|\s+created|\s+version|$)",
                    r"autor\s*:?\s*(.+?)(?=\s+geändert|\s+erstellt|\s+version|$)",
                ],
            )

            # ---------------------------------------------------------------
            # Try to find a better document title
            # ---------------------------------------------------------------

            document_title = title

            # If the link is just "Download", "PDF", etc.,
            # search nearby headings.
            if title.lower() in {
                "download",
                "downloaden",
                "herunterladen",
                "pdf",
                "open",
                "öffnen",
            }:

                for candidate in container.find_all(
                    [
                        "h1",
                        "h2",
                        "h3",
                        "h4",
                        "strong",
                    ]
                ):

                    candidate_text = clean(
                        candidate.get_text(
                            " ",
                            strip=True,
                        )
                    )

                    if (
                        candidate_text
                        and
                        candidate_text.lower()
                        not in {
                            "download",
                            "herunterladen",
                            "pdf",
                        }
                    ):

                        document_title = (
                            candidate_text
                        )

                        break

            item = Item(
                id=make_id(
                    absolute
                ),
                title=document_title,
                url=absolute,
                folder_url=folder_url,
                folder_name=folder_name,
                modified=modified,
                created=created,
                version=version,
                author=author,
                description=container_text,
                download_url=absolute,
                is_folder=False,
            )

            items.append(
                item
            )

        return (
            items,
            folders,
        )


    # -----------------------------------------------------------------------
    # Crawl recursively
    # -----------------------------------------------------------------------

    def crawl(
        self,
        start_url: str,
        folder_name: str,
        recursive: bool,
    ) -> list[Item]:

        results = []

        queue = [
            (
                normalize_url(
                    start_url
                ),
                folder_name,
            )
        ]

        visited_folders = set()

        max_pages = int(
            self.config.get(
                "max_pages",
                50,
            )
        )

        while queue:

            current_url, current_name = (
                queue.pop(0)
            )

            current_url = normalize_url(
                current_url
            )

            if current_url in visited_folders:
                continue

            visited_folders.add(
                current_url
            )

            print()
            print(
                "=" * 70
            )
            print(
                f"FOLDER: {current_name}"
            )
            print(
                current_url
            )
            print(
                "=" * 70
            )

            for page in range(
                0,
                max_pages,
            ):

                page_url = self.page_url(
                    current_url,
                    page,
                )

                try:

                    response = self.get(
                        page_url
                    )

                except Exception as exc:

                    print(
                        f"Could not fetch "
                        f"{page_url}: {exc}",
                        file=sys.stderr,
                    )

                    break

                items, child_folders = (
                    self.parse_page(
                        response.text,
                        response.url,
                        current_url,
                        current_name,
                    )
                )

                print(
                    f"Page {page}: "
                    f"{len(items)} items, "
                    f"{len(child_folders)} folders"
                )

                if not items:

                    break

                results.extend(
                    items
                )

                if recursive:

                    for child_url, child_name in (
                        child_folders
                    ):

                        child_url = normalize_url(
                            child_url
                        )

                        if (
                            child_url
                            not in visited_folders
                        ):

                            queue.append(
                                (
                                    child_url,
                                    child_name,
                                )
                            )

                # If fewer items than the configured page
                # size were found, this is probably the last page.
                document_count = sum(
                    1
                    for item in items
                    if not item.is_folder
                )

                if (
                    document_count
                    < self.items_per_page
                ):
                    break

        # Deduplicate.
        unique = {}

        for item in results:

            unique[item.id] = item

        return list(
            unique.values()
        )


# ---------------------------------------------------------------------------
# Change detection
# ---------------------------------------------------------------------------

def fingerprint(
    item: Item,
) -> str:

    values = [
        item.id,
        item.title,
        item.url,
        item.folder_url,
        item.folder_name,
        item.modified,
        item.created,
        item.version,
        item.author,
        item.download_url,
    ]

    return hashlib.sha256(
        "|".join(
            values
        ).encode(
            "utf-8"
        )
    ).hexdigest()


def detect_changes(
    items: list[Item],
    old_state: dict,
) -> tuple[list[Item], dict]:

    changes = []

    new_state = {}

    now = datetime.now(
        timezone.utc
    ).isoformat()

    for item in items:

        fp = fingerprint(
            item
        )

        previous = old_state.get(
            item.id
        )

        if previous is None:

            item.change_type = "new"

        elif previous.get(
            "fingerprint"
        ) != fp:

            item.change_type = "updated"

        else:

            item.change_type = ""

        record = asdict(
            item
        )

        record["fingerprint"] = fp

        record["last_seen"] = now

        # Don't need change_type in persistent state.
        record.pop(
            "change_type",
            None,
        )

        new_state[item.id] = record

        if item.change_type:

            changes.append(
                item
            )

    return (
        changes,
        new_state,
    )


# ---------------------------------------------------------------------------
# RSS
# ---------------------------------------------------------------------------

def parse_date(
    value: str,
) -> datetime | None:

    if not value:
        return None

    formats = [
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
        "%d.%m.%Y %H:%M",
        "%d.%m.%Y",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d-%m-%Y %H:%M",
        "%d-%m-%Y",
    ]

    for fmt in formats:

        try:

            result = datetime.strptime(
                value.strip(),
                fmt,
            )

            return result.replace(
                tzinfo=timezone.utc
            )

        except ValueError:
            pass

    return None


def item_date(
    item: Item,
) -> datetime:

    for value in (
        item.modified,
        item.created,
    ):

        parsed = parse_date(
            value
        )

        if parsed:
            return parsed

    return datetime.now(
        timezone.utc
    )


def rss_title(
    item: Item,
) -> str:

    if item.change_type == "new":

        prefix = (
            "[NEW FOLDER]"
            if item.is_folder
            else "[NEW]"
        )

    else:

        prefix = (
            "[UPDATED FOLDER]"
            if item.is_folder
            else "[UPDATED]"
        )

    return (
        f"{prefix} {item.title}"
    )


def rss_description(
    item: Item,
) -> str:

    parts = []

    if item.folder_name:

        parts.append(
            "<p><strong>Folder:</strong> "
            f"{xml_text(item.folder_name)}"
            "</p>"
        )

    if item.modified:

        parts.append(
            "<p><strong>Modified:</strong> "
            f"{xml_text(item.modified)}"
            "</p>"
        )

    if item.version:

        parts.append(
            "<p><strong>Version:</strong> "
            f"{xml_text(item.version)}"
            "</p>"
        )

    if item.author:

        parts.append(
            "<p><strong>Author:</strong> "
            f"{xml_text(item.author)}"
            "</p>"
        )

    if item.description:

        # Don't dump a gigantic card into RSS.
        description = item.description

        if len(description) > 1200:
            description = (
                description[:1200]
                + "..."
            )

        parts.append(
            "<p>"
            f"{xml_text(description)}"
            "</p>"
        )

    if item.url:

        parts.append(
            "<p>"
            '<a href="'
            f"{xml_text(item.url)}"
            '">Open in CIRCABC</a>'
            "</p>"
        )

    if item.download_url:

        parts.append(
            "<p>"
            '<a href="'
            f"{xml_text(item.download_url)}"
            '">Download</a>'
            "</p>"
        )

    return "".join(
        parts
    )


def build_feed(
    changes: list[Item],
    config: dict,
) -> None:

    max_items = int(
        config.get(
            "max_feed_items",
            200,
        )
    )

    feed_url = config[
        "feed_url"
    ]

    changes.sort(
        key=item_date,
        reverse=True,
    )

    changes = changes[
        :max_items
    ]

    now = datetime.now(
        timezone.utc
    )

    rss = ET.Element(
        "rss",
        {
            "version": "2.0",
            "xmlns:atom":
                "http://www.w3.org/2005/Atom",
        },
    )

    channel = ET.SubElement(
        rss,
        "channel",
    )

    ET.SubElement(
        channel,
        "title",
    ).text = (
        "CIRCABC – CARACAL"
    )

    ET.SubElement(
        channel,
        "link",
    ).text = config[
        "circabc_base_url"
    ]

    ET.SubElement(
        channel,
        "description",
    ).text = (
        "New and modified documents "
        "in monitored CIRCABC folders"
    )

    ET.SubElement(
        channel,
        "language",
    ).text = "en"

    ET.SubElement(
        channel,
        "lastBuildDate",
    ).text = format_datetime(
        now
    )

    ET.SubElement(
        channel,
        "{http://www.w3.org/2005/Atom}link",
        {
            "href": feed_url,
            "rel": "self",
            "type": "application/rss+xml",
        },
    )

    for item in changes:

        rss_item = ET.SubElement(
            channel,
            "item",
        )

        ET.SubElement(
            rss_item,
            "title",
        ).text = rss_title(
            item
        )

        ET.SubElement(
            rss_item,
            "link",
        ).text = item.url

        # Important:
        # A new version/update gets a different GUID from the
        # original version, so RSS readers see it as a new item.
        guid_source = (
            f"{item.id}|"
            f"{item.change_type}|"
            f"{fingerprint(item)}"
        )

        guid = (
            "circabc:"
            + hashlib.sha256(
                guid_source.encode(
                    "utf-8"
                )
            ).hexdigest()
        )

        ET.SubElement(
            rss_item,
            "guid",
            {
                "isPermaLink": "false"
            },
        ).text = guid

        ET.SubElement(
            rss_item,
            "pubDate",
        ).text = format_datetime(
            item_date(item)
        )

        ET.SubElement(
            rss_item,
            "description",
        ).text = rss_description(
            item
        )

    ET.indent(
        rss,
        space="  ",
    )

    FEED_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    tree = ET.ElementTree(
        rss
    )

    tree.write(
        FEED_FILE,
        encoding="utf-8",
        xml_declaration=True,
    )

    print()
    print(
        f"RSS written to: {FEED_FILE}"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():

    print()
    print(
        "CIRCABC RSS monitor"
    )
    print(
        "==================="
    )

    config = load_config()

    old_state = load_state()

    client = CircabcClient(
        config
    )

    all_items = []

    folders = config.get(
        "folders",
        [],
    )

    if not folders:

        raise RuntimeError(
            "No folders configured in config.yml."
        )

    for folder in folders:

        name = folder[
            "name"
        ]

        url = folder[
            "url"
        ]

        recursive = bool(
            folder.get(
                "recursive",
                True,
            )
        )

        print()
        print(
            f"Starting folder: {name}"
        )

        print(
            url
        )

        items = client.crawl(
            url,
            name,
            recursive,
        )

        print(
            f"Found {len(items)} items "
            f"in {name}"
        )

        all_items.extend(
            items
        )

    # Deduplicate.
    unique = {}

    for item in all_items:

        unique[item.id] = item

    all_items = list(
        unique.values()
    )

    print()
    print(
        f"Total unique items: "
        f"{len(all_items)}"
    )

    if not all_items:

        raise RuntimeError(
            "CIRCABC returned zero items. "
            "Check state/last_response.html "
            "and the GitHub Actions log."
        )

    changes, new_state = (
        detect_changes(
            all_items,
            old_state,
        )
    )

    print()
    print(
        f"Changes detected: "
        f"{len(changes)}"
    )

    for item in changes:

        print(
            f"  {item.change_type.upper():8} "
            f"{item.title}"
        )

    save_state(
        new_state
    )

    build_feed(
        changes,
        config,
    )


if __name__ == "__main__":
    main()
