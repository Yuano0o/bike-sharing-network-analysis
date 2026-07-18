#!/usr/bin/env python3
"""Download official bikeshare historical trip data by city.

The downloader starts from each city's official system-data page, extracts
historical trip-data links, filters them by year if requested, and downloads
matching files into per-city folders.
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, Iterable, List, Optional
from urllib.error import URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET


USER_AGENT = "Mozilla/5.0 (compatible; bikeshare-research-downloader/1.0)"


CITY_SOURCES: Dict[str, str] = {
    "san_francisco": "https://s3.amazonaws.com/baywheels-data",
    "nyc": "https://citibikenyc.com/system-data",
    "washington": "https://s3.amazonaws.com/capitalbikeshare-data",
    "chicago": "https://divvy-tripdata.s3.amazonaws.com",
    "columbus": "https://cogobikeshare.com/system-data",
    "portland": "https://s3.amazonaws.com/biketown-tripdata-public",
}


LIKELY_DOWNLOAD_PATTERNS = (
    ".zip",
    ".csv",
    "tripdata",
    "trip-data",
    "trip_history",
    "trip history",
    "historical data",
    "history data",
    "s3.amazonaws.com",
    "amazonaws.com",
)


DOWNLOAD_HOST_HINTS = (
    "amazonaws.com",
    "s3.",
    "lyft.com",
    "citibikenyc.com",
    "capitalbikeshare.com",
    "divvybikes.com",
    "cogobikeshare.com",
    "biketownpdx.com",
)


YEAR_PATTERN = re.compile(r"(20\d{2}|201\d)")


@dataclass
class LinkInfo:
    url: str
    text: str


class LinkExtractor(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url
        self.links: List[LinkInfo] = []
        self._current_href: Optional[str] = None
        self._text_parts: List[str] = []

    def handle_starttag(self, tag: str, attrs):
        if tag.lower() != "a":
            return
        attr_map = dict(attrs)
        href = attr_map.get("href")
        if href:
            self._current_href = urljoin(self.base_url, href)
            self._text_parts = []

    def handle_data(self, data: str):
        if self._current_href is not None:
            self._text_parts.append(data.strip())

    def handle_endtag(self, tag: str):
        if tag.lower() != "a" or self._current_href is None:
            return
        text = " ".join(part for part in self._text_parts if part).strip()
        self.links.append(LinkInfo(url=self._current_href, text=text))
        self._current_href = None
        self._text_parts = []


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download official bikeshare historical trip data.")
    parser.add_argument(
        "--cities",
        nargs="*",
        choices=sorted(CITY_SOURCES.keys()),
        default=sorted(CITY_SOURCES.keys()),
        help="Cities to download. Default: all configured cities.",
    )
    parser.add_argument(
        "--output-root",
        default="data",
        help="Directory where per-city folders will be created.",
    )
    parser.add_argument(
        "--years",
        nargs="*",
        type=int,
        help="Optional year filter, e.g. --years 2019 2022 2023.",
    )
    parser.add_argument(
        "--exclude-from",
        help="Optional inclusive exclusion start in YYYY-MM format, e.g. 2020-03.",
    )
    parser.add_argument(
        "--exclude-to",
        help="Optional inclusive exclusion end in YYYY-MM format, e.g. 2021-12.",
    )
    parser.add_argument(
        "--exclude-pandemic",
        action="store_true",
        help="Exclude files whose month falls within 2020-03 through 2021-12.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional per-city download limit after filtering.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show discovered links without downloading.",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.5,
        help="Pause between downloads. Default: 0.5s.",
    )
    parser.add_argument(
        "--manifest-name",
        default="download_manifest.json",
        help="Per-city manifest filename.",
    )
    return parser.parse_args()


def fetch_text(url: str) -> str:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=60) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.read().decode(charset, errors="replace")
    except URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, ssl.SSLCertVerificationError):
            print(f"[WARN] SSL verification failed for {url}; retrying without certificate verification.")
            insecure_context = ssl._create_unverified_context()
            with urlopen(req, timeout=60, context=insecure_context) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                return resp.read().decode(charset, errors="replace")
        raise


def parse_s3_xml_listing(page_url: str, payload: str) -> List[LinkInfo]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return []

    tag_name = root.tag.split("}")[-1]
    if tag_name != "ListBucketResult":
        return []

    links: List[LinkInfo] = []
    base = page_url.rstrip("/")
    for content in root.findall(".//{*}Contents"):
        key = content.findtext("{*}Key")
        if not key:
            continue
        url = f"{base}/{key}"
        links.append(LinkInfo(url=url, text=key))
    return links


def extract_links(page_url: str) -> List[LinkInfo]:
    payload = fetch_text(page_url)

    xml_links = parse_s3_xml_listing(page_url, payload)
    if xml_links:
        return xml_links

    parser = LinkExtractor(page_url)
    parser.feed(payload)
    return parser.links


def looks_like_download(link: LinkInfo) -> bool:
    url = link.url.lower()
    text = link.text.lower()
    if any(fragment in url for fragment in LIKELY_DOWNLOAD_PATTERNS):
        return True
    if any(fragment in text for fragment in LIKELY_DOWNLOAD_PATTERNS):
        return True
    parsed = urlparse(link.url)
    return parsed.scheme in {"http", "https"} and any(hint in parsed.netloc.lower() for hint in DOWNLOAD_HOST_HINTS)


def keep_historical_trip_link(link: LinkInfo) -> bool:
    haystack = f"{link.url} {link.text}".lower()
    if haystack.endswith(".html") or "/index.html" in haystack:
        return False
    if "gbfs" in haystack or "real-time" in haystack or "real time" in haystack:
        return False
    if "open data portal" in haystack:
        return False
    if "tableau" in haystack:
        return False
    if not looks_like_download(link):
        return False
    return any(
        keyword in haystack
        for keyword in (
            "trip",
            "history",
            "historical",
            "ride",
            ".zip",
            ".csv",
        )
    )


def dedupe_links(links: Iterable[LinkInfo]) -> List[LinkInfo]:
    seen = set()
    unique = []
    for link in links:
        if link.url in seen:
            continue
        seen.add(link.url)
        unique.append(link)
    return unique


def sort_links(links: List[LinkInfo]) -> List[LinkInfo]:
    return sorted(links, key=lambda item: item.url)


def filter_links_by_year(links: List[LinkInfo], years: Optional[List[int]]) -> List[LinkInfo]:
    if not years:
        return links
    year_strings = {str(year) for year in years}
    filtered = []
    for link in links:
        haystack = f"{link.url} {link.text}"
        found_years = set(YEAR_PATTERN.findall(haystack))
        if found_years & year_strings:
            filtered.append(link)
    return filtered


def parse_year_month_token(link: LinkInfo) -> Optional[str]:
    haystack = f"{link.url} {link.text}"
    match = re.search(r"((?:19|20)\d{2})[-_]?((?:0[1-9]|1[0-2]))", haystack)
    if match:
        return f"{match.group(1)}-{match.group(2)}"
    return None


def infer_period_range(link: LinkInfo) -> tuple[Optional[str], Optional[str]]:
    haystack = f"{link.url} {link.text}"

    month_match = re.search(r"((?:19|20)\d{2})[-_]?((?:0[1-9]|1[0-2]))", haystack)
    if month_match:
        token = f"{month_match.group(1)}-{month_match.group(2)}"
        return token, token

    quarter_match = re.search(r"((?:19|20)\d{2})[_-]?Q([1-4])", haystack, re.I)
    if quarter_match:
        year = quarter_match.group(1)
        quarter = int(quarter_match.group(2))
        start_month = 1 + (quarter - 1) * 3
        end_month = start_month + 2
        return f"{year}-{start_month:02d}", f"{year}-{end_month:02d}"

    year_match = re.search(r"((?:19|20)\d{2})", haystack)
    if year_match:
        year = year_match.group(1)
        return f"{year}-01", f"{year}-12"

    return None, None


def filter_links_by_exclusion_range(
    links: List[LinkInfo],
    exclude_from: Optional[str],
    exclude_to: Optional[str],
) -> List[LinkInfo]:
    if not exclude_from or not exclude_to:
        return links

    filtered = []
    for link in links:
        start_token, end_token = infer_period_range(link)
        if start_token is None or end_token is None:
            filtered.append(link)
            continue
        overlaps = not (end_token < exclude_from or start_token > exclude_to)
        if overlaps:
            continue
        filtered.append(link)
    return filtered


def safe_filename_from_url(url: str) -> str:
    parsed = urlparse(url)
    name = Path(parsed.path).name
    if not name:
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", parsed.netloc)
    return name


def download_file(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    req = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=120) as resp, destination.open("wb") as out:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
    except URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, ssl.SSLCertVerificationError):
            print(f"[WARN] SSL verification failed for {url}; retrying download without certificate verification.")
            insecure_context = ssl._create_unverified_context()
            with urlopen(req, timeout=120, context=insecure_context) as resp, destination.open("wb") as out:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
            return
        raise


def city_manifest_path(city_dir: Path, manifest_name: str) -> Path:
    return city_dir / manifest_name


def read_manifest(path: Path) -> dict:
    if not path.exists():
        return {"downloads": []}
    return json.loads(path.read_text())


def write_manifest(path: Path, manifest: dict) -> None:
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True))


def manifest_has_url(manifest: dict, url: str) -> bool:
    return any(item.get("url") == url for item in manifest.get("downloads", []))


def main() -> None:
    args = parse_args()
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    if args.exclude_pandemic:
        args.exclude_from = "2020-03"
        args.exclude_to = "2021-12"

    for city in args.cities:
        page_url = CITY_SOURCES[city]
        city_dir = output_root / city
        city_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = city_manifest_path(city_dir, args.manifest_name)
        manifest = read_manifest(manifest_path)

        print(f"\n[INFO] Discovering links for {city}: {page_url}")
        links = extract_links(page_url)
        links = [link for link in links if keep_historical_trip_link(link)]
        links = dedupe_links(links)
        links = filter_links_by_year(links, args.years)
        links = filter_links_by_exclusion_range(links, args.exclude_from, args.exclude_to)
        links = sort_links(links)

        if args.limit is not None:
            links = links[: args.limit]

        print(f"[INFO] {city}: found {len(links)} candidate historical files")
        if not links:
            print(f"[WARN] {city}: no matching links found; this city may need manual inspection.")
            continue

        for link in links:
            filename = safe_filename_from_url(link.url)
            destination = city_dir / filename
            if manifest_has_url(manifest, link.url) and destination.exists():
                print(f"[SKIP] {city}: already downloaded {filename}")
                continue

            if args.dry_run:
                print(f"[DRY]  {city}: {link.url}")
                continue

            print(f"[GET]  {city}: {filename}")
            download_file(link.url, destination)
            manifest.setdefault("downloads", []).append(
                {
                    "url": link.url,
                    "text": link.text,
                    "filename": filename,
                    "downloaded_at_unix": int(time.time()),
                }
            )
            write_manifest(manifest_path, manifest)
            time.sleep(args.sleep_seconds)


if __name__ == "__main__":
    main()
