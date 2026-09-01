#!/usr/bin/env python3

from __future__ import annotations

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
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import requests
import yaml
from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[1]

CONFIG_FILE = ROOT / "config.yml"
STATE_FILE = ROOT / "state" / "state.json"
FEED_FILE = ROOT / "docs" / "feed.xml"


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; CIRCABC-RSS-Monitor/1.0; "
        "+https://github.com/)"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.8",
}


@dataclass
class Item:
    id: str
    title: str
    url: str
    folder_id: str
    folder_name: str

    modified: str = ""
    created: str = ""
    version: str = ""
    author: str = ""
    description: str = ""
    file_type: str = ""
    file_size: str = ""
    download_url: str = ""

    is_folder: bool = False


def load_config() -> dict:
    with CONFIG_FILE.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}

    try:
        with STATE_FILE.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    with STATE_FILE.open("w", encoding="utf-8") as fh:
        json.dump(
            state,
            fh,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )


class CIRCABC:
    def __init__(self, config: dict):
        self.config = config

        self.base_url = config.get(
            "circabc_base_url",
            "https://circabc.europa.eu",
        ).rstrip("/")

        self.timeout = int(
            config.get("request_timeout", 30)
        )

        self.retries = int(
            config.get("request_retries", 3)
        )

        self.delay = float(
            config.get("request_delay", 0.5)
        )

        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

    def get(self, url: str) -> requests.Response:
        last_error = None

        for attempt in range(1, self.retries + 1):
            try:
                response = self.session.get(
                    url,
                    timeout=self.timeout,
                    allow_redirects=True,
                )

                response.raise_for_status()

                time.sleep(self.delay)

                return response

            except requests.RequestException as exc:
                last_error = exc

                print(
                    f"Request failed "
                    f"{attempt}/{self.retries}: "
                    f"{url}: {exc}",
                    file=sys.stderr,
                )

                if attempt < self.retries:
                    time.sleep(attempt * 2)

        raise RuntimeError(
            f"Could not fetch {url}: {last_error}"
        )

    def library_url(
        self,
        folder_id: str,
        page: int = 1,
    ) -> str:

        # CIRCABC library URLs use the group + library UUID
        # in the current UI. For direct monitoring we use the
        # stable browse UUID whenever possible.
        #
        # The ?p= parameter is the page index used by CIRCABC.
        if page <= 1:
            return (
                f"{self.base_url}/w/browse/"
                f"{folder_id}"
            )

        params = {
            "p": page - 1,
            "n": 100,
            "sort": "modified_DESC",
        }

        return (
            f"{self.base_url}/w/browse/"
            f"{folder_id}"
            f"?{urlencode(params)}"
        )

    def fetch_folder(
        self,
        folder_id: str,
        folder_name: str,
        page: int,
    ) -> tuple[list[Item], list[str]]:

        url = self.library_url(
            folder_id,
            page,
        )

        print(f"Fetching: {url}")

        response = self.get(url)

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

        items = []
        child_folders = []

        # CIRCABC pages contain UUID-based browse links.
        for link in soup.find_all("a", href=True):

            href = link["href"]

            if "/w/browse/" not in href:
                continue

            absolute = urljoin(
                self.base_url,
                href,
            )

            parsed = urlparse(absolute)

            match = re.search(
                r"/w/browse/([0-9a-fA-F-]{36})",
                parsed.path,
            )

            if not match:
                continue

            item_id = match.group(1)

            if item_id == folder_id:
                continue

            title = clean(
                link.get_text(
                    " ",
                    strip=True,
                )
            )

            if not title:
                continue

            # Determine the closest useful container.
            container = link

            for _ in range(5):
                if container.parent:
                    container = container.parent

            container_text = clean(
                container.get_text(
                    " ",
                    strip=True,
                )
            )

            is_probable_folder = (
                "folder" in container_text.lower()
                or "space" in container_text.lower()
            )

            # Look for a direct download link in the same
            # container.
            download_url = ""

            for dl in container.find_all(
                "a",
                href=True,
            ):
                dl_href = urljoin(
                    self.base_url,
                    dl["href"],
                )

                if (
                    "/d/" in dl_href
                    or "/rest/download/" in dl_href
                ):
                    download_url = dl_href
                    break

            modified = extract_metadata(
                container_text,
                [
                    r"modified\s*:?\s*"
                    r"([0-9]{1,2}[./-][0-9]{1,2}[./-][0-9]{2,4}"
                    r"(?:\s+[0-9:]+)?)",
                    r"last modified\s*:?\s*"
                    r"(.+?)(?=\s+created|\s+author|$)",
                ],
            )

            created = extract_metadata(
                container_text,
                [
                    r"created\s*:?\s*"
                    r"([0-9]{1,2}[./-][0-9]{1,2}[./-][0-9]{2,4}"
                    r"(?:\s+[0-9:]+)?)",
                ],
            )

            version = extract_metadata(
                container_text,
                [
                    r"version\s*:?\s*([0-9A-Za-z._-]+)",
                ],
            )

            author = extract_metadata(
                container_text,
                [
                    r"author\s*:?\s*(.+?)(?=\s+modified|\s+created|$)",
                ],
            )

            item = Item(
                id=item_id,
                title=title,
                url=absolute,
                folder_id=folder_id,
                folder_name=folder_name,
                modified=modified,
                created=created,
                version=version,
                author=author,
                description=container_text,
                download_url=download_url,
                is_folder=is_probable_folder,
            )

            items.append(item)

            if is_probable_folder:
                child_folders.append(item_id)

        # Remove duplicates.
        unique = {}

        for item in items:
            unique[item.id] = item

        return (
            list(unique.values()),
            list(dict.fromkeys(child_folders)),
        )

    def crawl(
        self,
        folder_id: str,
        folder_name: str,
        recursive: bool,
        max_pages: int,
    ) -> list[Item]:

        all_items = []

        queue = [
            (
                folder_id,
                folder_name,
            )
        ]

        visited = set()

        while queue:

            current_id, current_name = queue.pop(0)

            if current_id in visited:
                continue

            visited.add(current_id)

            print(
                f"\nCrawling folder: "
                f"{current_name} "
                f"({current_id})"
            )

            for page in range(
                1,
                max_pages + 1,
            ):

                try:
                    items, children = self.fetch_folder(
                        current_id,
                        current_name,
                        page,
                    )

                except Exception as exc:
                    print(
                        f"Could not fetch folder "
                        f"{current_id}, page {page}: "
                        f"{exc}",
                        file=sys.stderr,
                    )
                    break

                if not items:
                    break

                all_items.extend(items)

                if not recursive:
                    continue

                for child_id in children:

                    if child_id in visited:
                        continue

                    # Try to find the child's display name.
                    child = next(
                        (
                            x for x in items
                            if x.id == child_id
                        ),
                        None,
                    )

                    child_name = (
                        child.title
                        if child
                        else child_id
                    )

                    queue.append(
                        (
                            child_id,
                            child_name,
                        )
                    )

        unique = {}

        for item in all_items:
            unique[item.id] = item

        return list(unique.values())


def clean(value: str | None) -> str:
    if not value:
        return ""

    value = html.unescape(value)

    return re.sub(
        r"\s+",
        " ",
        value,
    ).strip()


def extract_metadata(
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
            return clean(match.group(1))

    return ""


def item_fingerprint(item: Item) -> str:
    """
    Generate a fingerprint representing the current state
    of the CIRCABC item.

    If modified/version changes, this changes too.
    """

    values = [
        item.id,
        item.title,
        item.modified,
        item.created,
        item.version,
        item.author,
        item.file_size,
        item.download_url,
    ]

    return "|".join(values)


def determine_changes(
    items: list[Item],
    old_state: dict,
) -> tuple[list[Item], dict]:

    changes = []
    new_state = {}

    for item in items:

        fingerprint = item_fingerprint(item)

        previous = old_state.get(item.id)

        if previous is None:
            change_type = "new"

        elif previous.get("fingerprint") != fingerprint:
            change_type = "updated"

        else:
            change_type = None

        state_record = asdict(item)
        state_record["fingerprint"] = fingerprint
        state_record["last_change"] = (
            datetime.now(timezone.utc).isoformat()
        )

        new_state[item.id] = state_record

        if change_type:
            item._change_type = change_type
            changes.append(item)

    return changes, new_state


def iso_to_datetime(value: str) -> datetime | None:

    if not value:
        return None

    try:
        return datetime.fromisoformat(
            value.replace(
                "Z",
                "+00:00",
            )
        )
    except ValueError:
        return None


def item_date(item: Item) -> datetime:

    for value in [
        item.modified,
        item.created,
    ]:

        parsed = parse_circabc_date(value)

        if parsed:
            return parsed

    return datetime.now(
        timezone.utc
    )


def parse_circabc_date(
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


def xml_escape(value: str) -> str:
    return (
        html.escape(
            value or "",
            quote=True,
        )
    )


def make_description(
    item: Item,
) -> str:

    parts = []

    if item.description:
        parts.append(
            f"<p>{xml_escape(item.description)}</p>"
        )

    metadata = []

    if item.folder_name:
        metadata.append(
            f"<strong>Folder:</strong> "
            f"{xml_escape(item.folder_name)}"
        )

    if item.modified:
        metadata.append(
            f"<strong>Modified:</strong> "
            f"{xml_escape(item.modified)}"
        )

    if item.version:
        metadata.append(
            f"<strong>Version:</strong> "
            f"{xml_escape(item.version)}"
        )

    if item.author:
        metadata.append(
            f"<strong>Author:</strong> "
            f"{xml_escape(item.author)}"
        )

    if metadata:
        parts.append(
            "<p>" +
            "<br/>".join(metadata) +
            "</p>"
        )

    if item.download_url:
        parts.append(
            "<p>"
            '<a href="'
            + xml_escape(item.download_url)
            + '">'
            "Direct download"
            "</a>"
            "</p>"
        )

    return "".join(parts)


def make_title(item: Item) -> str:

    change_type = getattr(
        item,
        "_change_type",
        "updated",
    )

    prefix = {
        "new": "[NEW]",
        "updated": "[UPDATED]",
    }.get(
        change_type,
        "[CHANGE]",
    )

    if item.is_folder:
        prefix = {
            "new": "[NEW FOLDER]",
            "updated": "[FOLDER UPDATED]",
        }.get(
            change_type,
            "[FOLDER]",
        )

    return f"{prefix} {item.title}"


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

    feed_url = config["feed_url"]

    # Sort newest changes first.
    changes.sort(
        key=item_date,
        reverse=True,
    )

    changes = changes[:max_items]

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
        "CIRCABC – monitored folders"
    )

    ET.SubElement(
        channel,
        "link",
    ).text = (
        config["circabc_base_url"]
    )

    ET.SubElement(
        channel,
        "description",
    ).text = (
        "Changes detected in selected "
        "CIRCABC folders"
    )

    ET.SubElement(
        channel,
        "language",
    ).text = "en"

    ET.SubElement(
        channel,
        "lastBuildDate",
    ).text = format_datetime(
        datetime.now(timezone.utc)
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
        ).text = make_title(item)

        ET.SubElement(
            rss_item,
            "link",
        ).text = item.url

        # Stable UUID-based identifier.
        change_type = getattr(
            item,
            "_change_type",
            "updated",
        )

        guid = (
            f"circabc:{item.id}:"
            f"{change_type}:"
            f"{item_fingerprint(item)}"
        )

        ET.SubElement(
            rss_item,
            "guid",
            {
                "isPermaLink": "false",
            },
        ).text = guid

        ET.SubElement(
            rss_item,
            "pubDate",
        ).text = format_datetime(
            item_date(item)
        )

        description = ET.SubElement(
            rss_item,
            "description",
        )

        description.text = make_description(
            item
        )

        if item.download_url:

            ET.SubElement(
                rss_item,
                "source",
                {
                    "url": item.download_url,
                },
            ).text = "CIRCABC"

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

    print(
        f"\nWrote {FEED_FILE}"
    )


def main():

    config = load_config()

    old_state = load_state()

    circabc = CIRCABC(
        config
    )

    all_items = []

    max_pages = int(
        config.get(
            "pages_per_folder",
            20,
        )
    )

    for folder in config.get(
        "folders",
        [],
    ):

        folder_id = folder["id"]

        folder_name = folder.get(
            "name",
            folder_id,
        )

        recursive = bool(
            folder.get(
                "recursive",
                True,
            )
        )

        items = circabc.crawl(
            folder_id,
            folder_name,
            recursive,
            max_pages,
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

    print(
        f"\nFound {len(all_items)} "
        f"unique CIRCABC items."
    )

    if not all_items:
        raise RuntimeError(
            "No CIRCABC items found. "
            "The folder may be inaccessible or "
            "CIRCABC may have changed its UI."
        )

    changes, new_state = determine_changes(
        all_items,
        old_state,
    )

    print(
        f"Detected {len(changes)} changes."
    )

    for item in changes:
        print(
            f"  {getattr(item, '_change_type', 'change')}: "
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
