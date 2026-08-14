#!/usr/bin/env python3
"""Ministerienyt: samlet nyhedsarkiv fra danske ministerier.

Programmet henter officielle nyheder fra 21 ministerielle hjemmesider samt
Regeringen.dk, gemmer et vedvarende arkiv fra 1. januar 2026 og bygger:

* site/index.html  - søgbar, mobilvenlig hjemmeside
* site/feed.xml    - samlet RSS 2.0-feed
* site/status.json - kildestatus fra seneste kørsel
* archive.json     - vedvarende arkiv, som GitHub Actions committer tilbage

Kilderne ligger i sources.json. Hver kilde kan bruge RSS/Atom, HTML-arkiver,
paginering og XML-sitemaps. Metoderne kombineres i stedet for at stoppe efter
første fund, så historikken bliver så komplet som muligt.
"""
from __future__ import annotations

import argparse
import email.utils
import gzip
import hashlib
import html
import json
import re
import sys
import time
from collections import Counter, deque
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qsl, parse_qs, unquote, urlencode, urljoin, urlparse, urlunparse
from xml.etree import ElementTree as ET

import feedparser
import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ARCHIVE_START = datetime(2026, 1, 1, tzinfo=timezone.utc)
USER_AGENT = "Ministerienyt/4.1 (+https://github.com/; public Danish government news aggregator)"
CONNECT_TIMEOUT = 12
READ_TIMEOUT = 35
REQUEST_DELAY_SECONDS = 0.08
MAX_LISTING_PAGES_PER_SOURCE = 160
MAX_SITEMAP_FILES_PER_SOURCE = 100
MAX_ERROR_MESSAGES_PER_SOURCE = 12
ARCHIVE_SCHEMA_VERSION = 2

DANISH_MONTHS = {
    "januar": 1,
    "februar": 2,
    "marts": 3,
    "april": 4,
    "maj": 5,
    "juni": 6,
    "juli": 7,
    "august": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "december": 12,
}
MONTH_NAMES = [
    "",
    "januar",
    "februar",
    "marts",
    "april",
    "maj",
    "juni",
    "juli",
    "august",
    "september",
    "oktober",
    "november",
    "december",
]

SKIP_SEGMENTS = {
    "presse",
    "kontakt",
    "abonner",
    "abonnement",
    "tilmeld",
    "nyhedsbrev",
    "nyheder",
    "nyhedsarkiv",
    "aktuelt",
    "pressemeddelelser",
    "pressemeddelelse",
    "arkiv",
    "search",
    "soeg",
    "soeg2",
    "sog",
    "page",
    "side",
    "2026",
}
GENERIC_LINK_TITLES = {
    "læs mere",
    "laes mere",
    "se mere",
    "mere",
    "read more",
    "åbn",
    "aabn",
    "nyhed",
    "pressemeddelelse",
    "image",
    "billede",
}
BAD_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".zip",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".svg",
    ".webp",
    ".mp3",
    ".mp4",
}
TRACKING_QUERY_KEYS = {
    "embed",
    "searchid",
    "documentoffset",
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
}
PAGINATION_QUERY_KEYS = {
    "page",
    "p",
    "offset",
    "start",
    "skip",
    "pagenumber",
    "currentpage",
    "pageindex",
}


@dataclass(frozen=True)
class Item:
    source: str
    title: str
    url: str
    published: datetime
    description: str = ""


@dataclass
class Candidate:
    url: str
    title: str = ""
    context: str = ""
    published: datetime | None = None
    discovered_by: str = "HTML"
    title_priority: int = 0


@dataclass
class SourceStatus:
    name: str
    home_url: str
    fresh_items: int = 0
    archived_items: int = 0
    listing_pages: int = 0
    sitemap_files: int = 0
    article_candidates: int = 0
    article_fetches: int = 0
    methods: list[str] | None = None
    errors: list[str] | None = None

    def __post_init__(self) -> None:
        if self.methods is None:
            self.methods = []
        if self.errors is None:
            self.errors = []


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def normalize_host(host: str) -> str:
    return (host or "").lower().split(":", 1)[0].removeprefix("www.")


def unwrap_redirect_url(url: str) -> str:
    """Pak kendte søge-/redirectlinks ud, især bm.ankiro.dk."""
    current = url
    for _ in range(4):
        parsed = urlparse(current)
        host = normalize_host(parsed.netloc)
        query = parse_qs(parsed.query)
        embedded = query.get("url", [""])[0]
        if host.endswith("ankiro.dk") and embedded:
            current = unquote(embedded)
            continue
        break
    return current


def normalize_url(url: str, *, keep_query: bool = True) -> str:
    url = unwrap_redirect_url(url.strip())
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return ""

    query = ""
    if keep_query and parsed.query:
        kept: list[tuple[str, str]] = []
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            folded = key.casefold()
            if folded.startswith("utm_") or folded in TRACKING_QUERY_KEYS:
                continue
            kept.append((key, value))
        query = urlencode(sorted(kept))

    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", query, ""))


def canonical_url(url: str) -> str:
    normalized = normalize_url(url, keep_query=True)
    if not normalized:
        return ""
    parsed = urlparse(normalized)
    path = parsed.path.rstrip("/") + "/"
    return urlunparse((parsed.scheme, parsed.netloc, path, "", parsed.query, ""))


def source_hosts(source: dict) -> set[str]:
    urls = [source.get("home_url", ""), *source.get("start_urls", []), *source.get("sitemap_urls", [])]
    hosts = {normalize_host(urlparse(url).netloc) for url in urls if url}
    for value in source.get("extra_hosts", []):
        host = normalize_host(urlparse(value).netloc) if "://" in value else normalize_host(value)
        if host:
            hosts.add(host)
    return hosts


def source_origins(source: dict) -> set[str]:
    result: set[str] = set()
    for url in [source.get("home_url", ""), *source.get("start_urls", [])]:
        parsed = urlparse(url)
        if parsed.scheme and parsed.netloc:
            result.add(urlunparse((parsed.scheme, parsed.netloc, "", "", "", "")))
    return result


def same_source_site(url: str, source: dict) -> bool:
    return normalize_host(urlparse(url).netloc) in source_hosts(source)


def plausible_published_date(dt: datetime) -> datetime | None:
    """Normalisér en publiceringsdato og afvis åbenlyst fejltolkede fremtidsår."""
    dt = (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    if dt.year < 2000:
        return None
    # En lille margen håndterer tidszoner og planlagte udgivelser samme døgn,
    # men forhindrer at beløb som "75 mio." bliver tolket som år 2075.
    if dt > datetime.now(timezone.utc) + timedelta(days=1):
        return None
    return dt


def parse_date(value: str) -> datetime | None:
    if not value:
        return None
    value = clean_text(value)

    # ISO-format, også med millisekunder og tidszone.
    match = re.search(
        r"\b(20\d{2}-\d{2}-\d{2})(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?",
        value,
        flags=re.IGNORECASE,
    )
    if match:
        try:
            parsed = date_parser.parse(match.group(0))
            result = plausible_published_date(parsed)
            if result:
                return result
        except Exception:
            pass

    # Dansk månedsnavn: 3. juni 2026.
    match = re.search(
        r"\b(\d{1,2})\.?\s+(" + "|".join(DANISH_MONTHS) + r")\s+(20\d{2})\b",
        value.casefold(),
    )
    if match:
        try:
            return plausible_published_date(
                datetime(
                    int(match.group(3)),
                    DANISH_MONTHS[match.group(2)],
                    int(match.group(1)),
                    tzinfo=timezone.utc,
                )
            )
        except ValueError:
            return None

    # 03-06-2026, 03.06.2026 eller 03/06/2026.
    match = re.search(r"\b(\d{1,2})[./-](\d{1,2})[./-](20\d{2})\b", value)
    if match:
        try:
            return plausible_published_date(
                datetime(
                    int(match.group(3)),
                    int(match.group(2)),
                    int(match.group(1)),
                    tzinfo=timezone.utc,
                )
            )
        except ValueError:
            return None

    # RFC-datoer fra RSS/Atom, fx "Sat, 1 Aug 2026 09:00:00 +0200".
    english_month = re.search(
        r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\b",
        value,
        flags=re.IGNORECASE,
    )
    if english_month and re.search(r"\b20\d{2}\b", value):
        try:
            parsed = date_parser.parse(value, dayfirst=True, fuzzy=True)
            return plausible_published_date(parsed)
        except Exception:
            pass
    return None

def date_from_soup(soup: BeautifulSoup) -> datetime | None:
    candidates: list[str] = []
    for key, value in [
        ("property", "article:published_time"),
        ("name", "article:published_time"),
        ("itemprop", "datePublished"),
        ("name", "date"),
        ("name", "publish-date"),
        ("name", "dcterms.date"),
    ]:
        tag = soup.find("meta", attrs={key: value})
        if tag and tag.get("content"):
            candidates.append(str(tag["content"]))

    for tag in soup.find_all("time"):
        if tag.get("datetime"):
            candidates.append(str(tag["datetime"]))
        candidates.append(tag.get_text(" ", strip=True))

    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except Exception:
            continue
        stack = obj if isinstance(obj, list) else [obj]
        while stack:
            current = stack.pop()
            if isinstance(current, dict):
                for field in ("datePublished", "dateCreated"):
                    if current.get(field):
                        candidates.append(str(current[field]))
                stack.extend(v for v in current.values() if isinstance(v, (dict, list)))
            elif isinstance(current, list):
                stack.extend(current)

    main = soup.find("main")
    visible = (main or soup).get_text(" ", strip=True)
    candidates.append(visible[:8000])

    for candidate in candidates:
        parsed = parse_date(candidate)
        if parsed:
            return parsed
    return None


def title_from_soup(soup: BeautifulSoup) -> str:
    for attrs in ({"property": "og:title"}, {"name": "twitter:title"}):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            return clean_text(str(tag["content"]))
    h1 = soup.find("h1")
    if h1:
        return clean_text(h1.get_text(" ", strip=True))
    return clean_text(soup.title.get_text(" ", strip=True) if soup.title else "")


def description_from_soup(soup: BeautifulSoup) -> str:
    for attrs in (
        {"property": "og:description"},
        {"name": "description"},
        {"name": "twitter:description"},
    ):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            return clean_text(str(tag["content"]))[:900]

    main = soup.find("main") or soup.find("article")
    if main:
        for paragraph in main.find_all("p"):
            text = clean_text(paragraph.get_text(" ", strip=True))
            if len(text) >= 40:
                return text[:900]
    paragraph = soup.find("p")
    return clean_text(paragraph.get_text(" ", strip=True) if paragraph else "")[:900]


def article_targets_in_node(node, base_url: str, source: dict) -> set[str]:
    result: set[str] = set()
    for link in node.find_all("a", href=True):
        target = normalize_url(urljoin(base_url, str(link["href"])), keep_query=True)
        if target and looks_like_article(target, source):
            result.add(canonical_url(target))
    return result


def listing_context_node(anchor, target: str, base_url: str, source: dict):
    """Find det mindste kort, som indeholder både artiklen og dens dato.

    Flere ministeriesider – især kum.dk – har ét link omkring overskriften og
    et andet link omkring manchetten. Det gamle udtræk kunne derfor bruge
    manchetten som overskrift og et tal i teksten som årstal. Her går vi op i
    DOM-træet, indtil vi finder det fælles artikelkort med en rigtig dato.
    """
    target_key = canonical_url(target)
    fallback = None
    for node in anchor.parents:
        if getattr(node, "name", None) not in {"article", "li", "div", "section"}:
            continue
        text = clean_text(node.get_text(" ", strip=True))
        if not (10 <= len(text) <= 5000):
            continue
        targets = article_targets_in_node(node, base_url, source)
        if target_key not in targets or len(targets) > 2:
            continue
        if fallback is None:
            fallback = node
        if parse_date(text):
            return node
    return fallback or anchor.parent or anchor


def heading_title_in_node(node, target: str, base_url: str) -> str:
    target_key = canonical_url(target)
    for heading in node.find_all(["h1", "h2", "h3", "h4", "h5"]):
        link = heading.find("a", href=True)
        if link:
            linked = canonical_url(normalize_url(urljoin(base_url, str(link["href"])), keep_query=True))
            if linked != target_key:
                continue
        title = clean_text(heading.get_text(" ", strip=True))
        if not is_generic_title(title):
            return title
    return ""


def listing_description(node, target: str, base_url: str, title: str) -> str:
    target_key = canonical_url(target)
    choices: list[str] = []
    for link in node.find_all("a", href=True):
        linked = canonical_url(normalize_url(urljoin(base_url, str(link["href"])), keep_query=True))
        if linked != target_key:
            continue
        text = clean_text(link.get_text(" ", strip=True))
        if not text or text.casefold() == title.casefold() or is_generic_title(text):
            continue
        if 25 <= len(text) <= 1200:
            choices.append(text)
    for paragraph in node.find_all("p"):
        text = clean_text(paragraph.get_text(" ", strip=True))
        if text and text.casefold() != title.casefold() and 25 <= len(text) <= 1200:
            choices.append(text)
    if not choices:
        return ""
    # En manchet er typisk længere end overskriften, men kortere end hele kortet.
    return max(choices, key=len)[:900]


def listing_fields(anchor, target: str, base_url: str, source: dict) -> tuple[str, str, datetime | None, int]:
    node = listing_context_node(anchor, target, base_url, source)
    title = heading_title_in_node(node, target, base_url)
    priority = 3 if title else 1
    if not title:
        title = clean_text(anchor.get_text(" ", strip=True))
        if getattr(anchor.parent, "name", "") in {"h1", "h2", "h3", "h4", "h5"}:
            priority = 3
    description = listing_description(node, target, base_url, title)
    published = parse_date(clean_text(node.get_text(" ", strip=True)))
    return title, description, published, priority


def create_session() -> requests.Session:
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "da,en;q=0.7"})
    return session


def fetch(session: requests.Session, url: str) -> requests.Response:
    response = session.get(url, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT), allow_redirects=True)
    response.raise_for_status()
    if REQUEST_DELAY_SECONDS:
        time.sleep(REQUEST_DELAY_SECONDS)
    return response


def append_error(status: SourceStatus, message: str) -> None:
    message = clean_text(message)
    if message and len(status.errors or []) < MAX_ERROR_MESSAGES_PER_SOURCE:
        assert status.errors is not None
        status.errors.append(message[:400])


def path_has_archive_year(url: str) -> bool:
    return bool(re.search(r"/(?:20)?26(?:/|$)", urlparse(url).path))


def looks_like_article(url: str, source: dict) -> bool:
    url = normalize_url(url, keep_query=True)
    if not url or not same_source_site(url, source):
        return False
    parsed = urlparse(url)
    lowered_path = parsed.path.casefold()
    if any(lowered_path.endswith(ext) for ext in BAD_EXTENSIONS):
        return False

    prefixes = source.get("article_prefixes", [])
    if prefixes and not any(lowered_path.startswith(prefix.casefold().rstrip("/") + "/") for prefix in prefixes):
        return False

    segments = [segment for segment in parsed.path.split("/") if segment]
    if not segments:
        return False
    last = segments[-1].casefold()
    if last in SKIP_SEGMENTS or re.fullmatch(r"20\d{2}", last):
        return False
    if last in DANISH_MONTHS or re.fullmatch(r"(?:jan|feb|mar|apr|jun|jul|aug|sep|okt|nov|dec)", last):
        return False
    if len(last) < 5 and not parsed.query:
        return False
    return True


def is_generic_title(title: str) -> bool:
    folded = clean_text(title).casefold().strip(" .:–—-→↗")
    return not folded or folded in GENERIC_LINK_TITLES or len(folded) < 6


def merge_candidate(existing: Candidate | None, new: Candidate) -> Candidate:
    if existing is None:
        return new
    title = existing.title
    title_priority = existing.title_priority
    if (
        new.title_priority > title_priority
        or (is_generic_title(title) and not is_generic_title(new.title))
        or (
            new.title_priority == title_priority
            and not is_generic_title(new.title)
            and len(title) > 220
            and len(new.title) < len(title)
        )
    ):
        title = new.title
        title_priority = new.title_priority
    context = new.context if len(new.context) > len(existing.context) else existing.context
    published = existing.published or new.published
    discovered_by = existing.discovered_by
    if new.discovered_by not in discovered_by.split("+"):
        discovered_by += "+" + new.discovered_by
    return Candidate(existing.url, title, context, published, discovered_by, title_priority)


def discovered_feed_urls(soup: BeautifulSoup, base_url: str) -> list[str]:
    result: list[str] = []
    for link in soup.find_all("link", href=True):
        rel = " ".join(link.get("rel", [])).casefold()
        mime = str(link.get("type", "")).casefold()
        if "alternate" in rel and any(token in mime for token in ("rss", "atom", "xml")):
            result.append(normalize_url(urljoin(base_url, str(link["href"])), keep_query=True))
    return [url for url in result if url]


def listing_link_candidate(anchor, target: str, current_url: str, source: dict) -> bool:
    target = normalize_url(target, keep_query=True)
    if not target or not same_source_site(target, source) or looks_like_article(target, source):
        return False
    if canonical_url(target) == canonical_url(current_url):
        return False

    text = clean_text(anchor.get_text(" ", strip=True)).casefold()
    parsed = urlparse(target)
    query_keys = {key.casefold() for key, _ in parse_qsl(parsed.query, keep_blank_values=True)}
    path = parsed.path.casefold()

    if text in {"næste", "naeste", "next", "›", "»", ">", "flere", "flere nyheder", "ældre", "aeldre", "older"}:
        return True
    if re.fullmatch(r"\d{1,3}", text):
        return True
    if "flere nyheder" in text or "næste" in text or "naeste" in text:
        return True
    if query_keys & PAGINATION_QUERY_KEYS:
        return True
    if re.search(r"/(?:page|side)/\d+/?$", path):
        return True
    if text == "2026" or path_has_archive_year(target):
        return True

    configured = {canonical_url(url) for url in source.get("start_urls", [])}
    return canonical_url(target) in configured


def crawl_listing_pages(
    session: requests.Session,
    source: dict,
    status: SourceStatus,
) -> tuple[dict[str, Candidate], list[str]]:
    queue: deque[str] = deque(normalize_url(url, keep_query=True) for url in source.get("start_urls", []))
    visited: set[str] = set()
    candidates: dict[str, Candidate] = {}
    feed_urls: list[str] = []

    max_pages = int(source.get("max_listing_pages", MAX_LISTING_PAGES_PER_SOURCE))
    while queue and len(visited) < max_pages:
        requested_url = queue.popleft()
        page_key = normalize_url(requested_url, keep_query=True)
        if not page_key or page_key in visited:
            continue
        visited.add(page_key)
        try:
            response = fetch(session, requested_url)
        except Exception as exc:
            append_error(status, f"Liste kunne ikke hentes: {requested_url}: {exc}")
            continue

        final_url = normalize_url(response.url, keep_query=True) or requested_url
        content_type = (response.headers.get("content-type") or "").casefold()
        prefix = response.content[:500].lstrip().lower()
        if any(token in content_type for token in ("rss", "atom", "xml")) or prefix.startswith(b"<?xml"):
            feed_urls.append(final_url)
            continue

        status.listing_pages += 1
        soup = BeautifulSoup(response.text, "html.parser")
        feed_urls.extend(discovered_feed_urls(soup, final_url))

        for anchor in soup.find_all("a", href=True):
            raw_target = urljoin(final_url, str(anchor["href"]))
            target = normalize_url(raw_target, keep_query=True)
            if not target:
                continue

            if looks_like_article(target, source):
                title, context, published, title_priority = listing_fields(
                    anchor, target, final_url, source
                )
                candidate = Candidate(
                    url=target,
                    title=title,
                    context=context,
                    published=published,
                    discovered_by="HTML",
                    title_priority=title_priority,
                )
                key = canonical_url(target)
                candidates[key] = merge_candidate(candidates.get(key), candidate)
            elif listing_link_candidate(anchor, target, final_url, source):
                if target not in visited:
                    queue.append(target)

    if queue:
        append_error(
            status,
            f"Sikkerhedsgrænsen på {max_pages} listesider blev nået; kontrollér kilden ved meget store arkiver.",
        )
    return candidates, list(dict.fromkeys(url for url in feed_urls if url))


def feed_candidate_urls(source: dict, discovered: Iterable[str]) -> list[str]:
    result = [normalize_url(url, keep_query=True) for url in source.get("rss_urls", [])]
    result.extend(discovered)
    for start_url in source.get("start_urls", []):
        separator = "&" if "?" in start_url else "?"
        result.append(normalize_url(start_url + separator + "rss=true", keep_query=True))
    return list(dict.fromkeys(url for url in result if url))


def strip_markup(value: str) -> str:
    if not value:
        return ""
    return clean_text(BeautifulSoup(value, "html.parser").get_text(" ", strip=True))


def collect_feed_items(
    session: requests.Session,
    source: dict,
    feed_urls: Iterable[str],
    status: SourceStatus,
) -> list[Item]:
    result: dict[str, Item] = {}
    used = False
    for feed_url in feed_candidate_urls(source, feed_urls):
        try:
            response = fetch(session, feed_url)
        except Exception:
            continue
        content_type = (response.headers.get("content-type") or "").casefold()
        prefix = response.content[:500].lower()
        if not any(token in content_type for token in ("xml", "rss", "atom")) and b"<rss" not in prefix and b"<feed" not in prefix:
            continue

        feed = feedparser.parse(response.content)
        if not feed.entries:
            continue
        used = True
        for entry in feed.entries:
            published = None
            for field in ("published", "updated", "created"):
                value = getattr(entry, field, None)
                if value:
                    published = parse_date(str(value))
                    if published:
                        break
            link = normalize_url(str(getattr(entry, "link", "") or ""), keep_query=True)
            title = clean_text(str(getattr(entry, "title", "") or ""))
            if not published or published < ARCHIVE_START or not link or is_generic_title(title):
                continue
            if not looks_like_article(link, source):
                continue
            description = strip_markup(str(getattr(entry, "summary", "") or ""))[:900]
            item = Item(source["name"], title, link, published, description)
            result[canonical_url(link)] = better_item(result.get(canonical_url(link)), item)
    if used:
        status.methods.append("RSS/Atom")
    return list(result.values())


def decompress_if_needed(content: bytes) -> bytes:
    if content.startswith(b"\x1f\x8b"):
        try:
            return gzip.decompress(content)
        except Exception:
            return content
    return content


def xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].casefold()


def sitemap_seed_urls(session: requests.Session, source: dict, status: SourceStatus) -> list[str]:
    result = [normalize_url(url, keep_query=True) for url in source.get("sitemap_urls", [])]
    for origin in source_origins(source):
        result.extend(
            [
                normalize_url(origin + "/sitemap.xml", keep_query=True),
                normalize_url(origin + "/sitemap_index.xml", keep_query=True),
            ]
        )
        try:
            response = fetch(session, origin + "/robots.txt")
            for line in response.text.splitlines():
                if line.casefold().startswith("sitemap:"):
                    result.append(normalize_url(line.split(":", 1)[1].strip(), keep_query=True))
        except Exception:
            pass
    return list(dict.fromkeys(url for url in result if url))


def discover_sitemap_candidates(
    session: requests.Session,
    source: dict,
    status: SourceStatus,
) -> dict[str, Candidate]:
    queue: deque[str] = deque(sitemap_seed_urls(session, source, status))
    visited: set[str] = set()
    result: dict[str, Candidate] = {}

    while queue and len(visited) < MAX_SITEMAP_FILES_PER_SOURCE:
        sitemap_url = queue.popleft()
        key = normalize_url(sitemap_url, keep_query=True)
        if not key or key in visited:
            continue
        visited.add(key)
        try:
            response = fetch(session, sitemap_url)
            content = decompress_if_needed(response.content)
            root = ET.fromstring(content)
        except Exception:
            continue

        status.sitemap_files += 1
        root_name = xml_local_name(root.tag)
        if root_name == "sitemapindex":
            for node in root.iter():
                if xml_local_name(node.tag) == "loc" and node.text:
                    nested = normalize_url(node.text.strip(), keep_query=True)
                    if nested and nested not in visited:
                        queue.append(nested)
            continue

        if root_name != "urlset":
            continue

        for url_node in list(root):
            if xml_local_name(url_node.tag) != "url":
                continue
            loc = ""
            lastmod = None
            for child in list(url_node):
                name = xml_local_name(child.tag)
                if name == "loc" and child.text:
                    loc = normalize_url(child.text.strip(), keep_query=True)
                elif name == "lastmod" and child.text:
                    lastmod = parse_date(child.text.strip())
            if not loc or not looks_like_article(loc, source):
                continue

            # Undgå at hente mange års historik. 2026 i URL'en eller en
            # sitemap-lastmod fra 2026 er nok til at kontrollere artiklen.
            if not path_has_archive_year(loc) and not (lastmod and lastmod >= ARCHIVE_START):
                continue
            candidate = Candidate(loc, published=None, discovered_by="Sitemap")
            item_key = canonical_url(loc)
            result[item_key] = merge_candidate(result.get(item_key), candidate)

    if queue:
        append_error(
            status,
            f"Sikkerhedsgrænsen på {MAX_SITEMAP_FILES_PER_SOURCE} sitemap-filer blev nået.",
        )
    if result:
        status.methods.append("Sitemap")
    return result


def context_description(candidate: Candidate) -> str:
    text = clean_text(candidate.context)
    if candidate.title and text.casefold().startswith(candidate.title.casefold()):
        text = text[len(candidate.title) :].lstrip(" .:–—-/")
    text = re.sub(
        r"^(?:pressemeddelelse|nyhed)(?:\s*-\s*ligestilling)?\s*/?\s*\d{1,2}[.-]\d{1,2}[.-]20\d{2}\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return clean_text(text)[:900]


def item_from_candidate(
    session: requests.Session,
    source: dict,
    candidate: Candidate,
    status: SourceStatus,
) -> Item | None:
    title = clean_text(candidate.title)
    published = candidate.published
    description = context_description(candidate)
    final_url = candidate.url

    if published and published < ARCHIVE_START:
        return None
    must_fetch = bool(source.get("always_fetch_articles")) or not published or is_generic_title(title)
    if must_fetch:
        try:
            response = fetch(session, candidate.url)
            status.article_fetches += 1
            final_url = normalize_url(response.url, keep_query=True) or candidate.url
            soup = BeautifulSoup(response.text, "html.parser")
            published = date_from_soup(soup) or published
            page_title = title_from_soup(soup)
            if page_title and not is_generic_title(page_title):
                title = page_title
            page_description = description_from_soup(soup)
            if page_description:
                description = page_description
        except Exception as exc:
            append_error(status, f"Artikel kunne ikke hentes: {candidate.url}: {exc}")

    if not published or published < ARCHIVE_START or is_generic_title(title):
        return None
    if not looks_like_article(final_url, source):
        return None
    return Item(source["name"], title, final_url, published, description[:900])


def better_item(existing: Item | None, new: Item) -> Item:
    if existing is None:
        return new
    title = existing.title
    if is_generic_title(title) and not is_generic_title(new.title):
        title = new.title
    elif len(new.title) > len(title) and not is_generic_title(new.title):
        title = new.title
    description = new.description if len(new.description) > len(existing.description) else existing.description
    published = existing.published
    if abs((new.published - existing.published).total_seconds()) < 48 * 3600:
        published = min(existing.published, new.published)
    return Item(new.source or existing.source, title, new.url or existing.url, published, description)



def ritzau_public_url(raw_url: str) -> str:
    parsed = urlparse(urljoin("https://via.ritzau.dk", raw_url))
    path = parsed.path
    if path.startswith("/release/"):
        path = "/pressemeddelelse/" + path[len("/release/") :]
    return normalize_url(urlunparse((parsed.scheme, parsed.netloc, path, "", parsed.query, "")), keep_query=True)


def collect_ritzau_items(
    session: requests.Session,
    source: dict,
    known_urls: set[str],
    status: SourceStatus,
) -> tuple[list[Item], bool]:
    pressroom_id = source.get("ritzau_pressroom_id")
    if not pressroom_id:
        return [], False

    page_size = int(source.get("ritzau_page_size", 20))
    max_pages = int(source.get("ritzau_max_pages", 50))
    result: dict[str, Item] = {}
    api_ok = False
    candidate_count = 0

    for page in range(max_pages):
        endpoint = (
            f"https://via.ritzau.dk/public-website-api/pressroom/{pressroom_id}/"
            f"releases/{page_size}/{page}"
        )
        try:
            response = fetch(session, endpoint)
            payload = response.json()
            api_ok = True
        except Exception as exc:
            append_error(status, f"Via Ritzau API kunne ikke hentes: {exc}")
            break

        releases = payload.get("releases", []) if isinstance(payload, dict) else []
        if not releases:
            break

        reached_older = False
        for release in releases:
            if not isinstance(release, dict):
                continue
            published = parse_date(str(release.get("date", "")))
            if not published:
                continue
            if published < ARCHIVE_START:
                reached_older = True
                continue
            versions = release.get("versions", {})
            if not isinstance(versions, dict) or not versions:
                continue
            version = versions.get("da") or next(iter(versions.values()), {})
            if not isinstance(version, dict):
                continue
            title = clean_text(str(version.get("title", "")))
            url = ritzau_public_url(str(version.get("url", "")))
            if not url or is_generic_title(title):
                continue
            candidate_count += 1
            key = canonical_url(url)
            if key in known_urls:
                continue
            description = clean_text(str(version.get("metadescription", "")))[:900]
            result[key] = Item(source["name"], title, url, published, description)

        paging = payload.get("paging", {}) if isinstance(payload, dict) else {}
        total = int(paging.get("count", 0) or 0) if isinstance(paging, dict) else 0
        if reached_older or (total and (page + 1) * page_size >= total):
            break

    if api_ok:
        status.methods.append("Via Ritzau API")
        status.article_candidates = candidate_count
    return sorted(result.values(), key=lambda item: item.published, reverse=True), api_ok

def collect_source(
    session: requests.Session,
    source: dict,
    known_urls: set[str],
) -> tuple[list[Item], SourceStatus]:
    status = SourceStatus(source["name"], source.get("home_url", source.get("start_urls", [""])[0]))

    ritzau_items, ritzau_ok = collect_ritzau_items(session, source, known_urls, status)
    if ritzau_ok:
        status.fresh_items = len(ritzau_items)
        return ritzau_items, status

    listing_candidates, discovered_feeds = crawl_listing_pages(session, source, status)
    if listing_candidates:
        status.methods.append("HTML")

    feed_items = collect_feed_items(session, source, discovered_feeds, status)
    sitemap_candidates = (
        {}
        if source.get("disable_sitemap")
        else discover_sitemap_candidates(session, source, status)
    )

    candidates = dict(listing_candidates)
    for key, candidate in sitemap_candidates.items():
        candidates[key] = merge_candidate(candidates.get(key), candidate)
    status.article_candidates = len(candidates)

    fresh: dict[str, Item] = {}
    for item in feed_items:
        key = canonical_url(item.url)
        fresh[key] = better_item(fresh.get(key), item)

    for key, candidate in candidates.items():
        if key in known_urls:
            continue
        item = item_from_candidate(session, source, candidate, status)
        if item:
            item_key = canonical_url(item.url)
            fresh[item_key] = better_item(fresh.get(item_key), item)

    status.fresh_items = len(fresh)
    return sorted(fresh.values(), key=lambda item: item.published, reverse=True), status


def collect_fresh_items(
    sources: list[dict],
    known_urls: set[str],
) -> tuple[list[Item], list[SourceStatus]]:
    session = create_session()
    all_items: dict[str, Item] = {}
    statuses: list[SourceStatus] = []

    for index, source in enumerate(sources, start=1):
        print(f"[{index}/{len(sources)}] Henter {source['name']} ...", file=sys.stderr)
        started = time.monotonic()
        try:
            items, status = collect_source(session, source, known_urls)
        except Exception as exc:
            status = SourceStatus(source["name"], source.get("home_url", ""))
            append_error(status, f"Uventet kildefejl: {exc}")
            items = []
        for item in items:
            key = canonical_url(item.url)
            all_items[key] = better_item(all_items.get(key), item)
        statuses.append(status)
        duration = time.monotonic() - started
        method_text = ", ".join(dict.fromkeys(status.methods or [])) or "ingen fund"
        print(
            f"  -> {len(items)} nye/opdaterede, {status.article_candidates} kandidater, "
            f"{method_text}, {duration:.1f} sek.",
            file=sys.stderr,
        )

    return sorted(all_items.values(), key=lambda item: item.published, reverse=True), statuses


def item_from_archive_dict(raw: dict) -> Item | None:
    try:
        published = parse_date(str(raw.get("published", "")))
        if not published or published < ARCHIVE_START:
            return None
        source = clean_text(str(raw.get("source", "")))
        title = clean_text(str(raw.get("title", "")))
        url = normalize_url(str(raw.get("url", "")), keep_query=True)
        description = clean_text(str(raw.get("description", "")))[:900]
        if not source or is_generic_title(title) or not url:
            return None
        return Item(source, title, url, published, description)
    except Exception:
        return None


def load_archive(path: Path, allowed_sources: set[str]) -> tuple[list[Item], int]:
    if not path.exists():
        return [], 0
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"Advarsel: kunne ikke læse {path}: {exc}", file=sys.stderr)
        return [], 0
    schema_version = int(raw.get("schema_version", 0) or 0) if isinstance(raw, dict) else 0
    rows = raw.get("items", []) if isinstance(raw, dict) else raw
    result: dict[str, Item] = {}
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            item = item_from_archive_dict(row)
            if not item or item.source not in allowed_sources:
                continue
            key = canonical_url(item.url)
            result[key] = better_item(result.get(key), item)
    return sorted(result.values(), key=lambda item: item.published, reverse=True), schema_version

def merge_archive(existing: Iterable[Item], fresh: Iterable[Item]) -> list[Item]:
    merged: dict[str, Item] = {}
    for item in [*existing, *fresh]:
        if item.published < ARCHIVE_START:
            continue
        key = canonical_url(item.url)
        if key:
            merged[key] = better_item(merged.get(key), item)
    return sorted(merged.values(), key=lambda item: item.published, reverse=True)


def archive_signature(items: Iterable[Item]) -> tuple[tuple[str, str, str, str, str], ...]:
    return tuple(
        sorted(
            (
                item.source,
                item.title,
                canonical_url(item.url),
                item.published.isoformat(),
                item.description,
            )
            for item in items
        )
    )


def save_archive(path: Path, items: list[Item]) -> None:
    payload = {
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        "archive_start": ARCHIVE_START.date().isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "items": [
            {
                "source": item.source,
                "title": item.title,
                "url": item.url,
                "published": item.published.isoformat(),
                "description": item.description,
            }
            for item in items
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def build_rss(items: Iterable[Item], site_url: str, feed_url: str) -> bytes:
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "Ministerienyt – nyheder fra danske ministerier og Regeringen.dk"
    ET.SubElement(channel, "link").text = site_url
    ET.SubElement(channel, "description").text = (
        "Samlet arkiv med officielle nyheder og pressemeddelelser fra 1. januar 2026."
    )
    ET.SubElement(channel, "language").text = "da"
    ET.SubElement(channel, "lastBuildDate").text = email.utils.format_datetime(datetime.now(timezone.utc))
    ET.SubElement(channel, "generator").text = "Ministerienyt 4.1"
    if feed_url:
        atom = "http://www.w3.org/2005/Atom"
        ET.register_namespace("atom", atom)
        ET.SubElement(
            channel,
            f"{{{atom}}}link",
            {"href": feed_url, "rel": "self", "type": "application/rss+xml"},
        )

    for item in items:
        node = ET.SubElement(channel, "item")
        ET.SubElement(node, "title").text = f"{item.source}: {item.title}"
        ET.SubElement(node, "link").text = item.url
        guid = ET.SubElement(node, "guid", {"isPermaLink": "false"})
        guid.text = hashlib.sha256(canonical_url(item.url).encode("utf-8")).hexdigest()
        ET.SubElement(node, "pubDate").text = email.utils.format_datetime(item.published)
        ET.SubElement(node, "category").text = item.source
        source_node = ET.SubElement(node, "source", {"url": item.url})
        source_node.text = item.source
        if item.description:
            ET.SubElement(node, "description").text = item.description

    ET.indent(rss, space="  ")
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)


def fmt_date_da(dt: datetime) -> str:
    return f"{dt.day}. {MONTH_NAMES[dt.month]} {dt.year}"


def fmt_datetime_da(dt: datetime) -> str:
    return f"{dt.day}. {MONTH_NAMES[dt.month]} {dt.year} kl. {dt:%H:%M} UTC"


def esc(value: str) -> str:
    return html.escape(value or "", quote=True)


def build_html(
    items: list[Item],
    feed_url: str,
    sources: list[dict],
    statuses: list[SourceStatus],
) -> str:
    ministries = sorted((source["name"] for source in sources), key=str.casefold)
    source_lookup = {source["name"]: source for source in sources}
    status_lookup = {status.name: status for status in statuses}
    counts = Counter(item.source for item in items)
    updated = datetime.now(timezone.utc)

    cards: list[str] = []
    for item in items:
        description = clean_text(item.description)
        if len(description) > 360:
            description = description[:357].rstrip() + "..."
        article_id = hashlib.sha256(canonical_url(item.url).encode("utf-8")).hexdigest()
        cards.append(
            f'''<article class="card" data-id="{article_id}" data-published="{esc(item.published.isoformat())}" data-ministry="{esc(item.source.casefold())}" data-search="{esc((item.source + ' ' + item.title + ' ' + description).casefold())}">
  <div class="meta"><span class="source-name">{esc(item.source)}</span><time datetime="{esc(item.published.isoformat())}">{esc(fmt_date_da(item.published))}</time><span class="new-badge">Ny siden sidst</span></div>
  <h2><a href="{esc(item.url)}" target="_blank" rel="noopener noreferrer">{esc(item.title)}</a></h2>
  {f'<p>{esc(description)}</p>' if description else ''}
  <a class="more" href="{esc(item.url)}" target="_blank" rel="noopener noreferrer">Læs hos kilden &nearr;</a>
</article>'''
        )

    options = ['<option value="">Alle kilder</option>'] + [
        f'<option value="{esc(name.casefold())}">{esc(name)}</option>' for name in ministries
    ]

    source_rows: list[str] = []
    for name in ministries:
        source = source_lookup[name]
        status = status_lookup.get(name)
        methods = ", ".join(dict.fromkeys(status.methods or [])) if status else "Arkiv"
        if not methods:
            methods = "Arkiv"
        errors = len(status.errors or []) if status else 0
        note = ""
        if counts.get(name, 0) == 0:
            warning_text = (status.errors or ["Ingen artikler fundet fra kilden."])[0] if status else "Ingen artikler fundet fra kilden."
            note = f' <span class="warning" title="{esc(warning_text)}">Ingen artikler</span>'
        source_rows.append(
            f'''<tr><td><a href="{esc(source.get('home_url', source['start_urls'][0]))}" target="_blank" rel="noopener noreferrer">{esc(name)}</a>{note}</td><td>{counts.get(name, 0)}</td><td>{esc(methods)}</td></tr>'''
        )

    feed_href = esc(feed_url or "feed.xml")
    return f'''<!doctype html>
<html lang="da"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="description" content="Samlet arkiv over officielle nyheder fra danske ministerier og Regeringen.dk siden 1. januar 2026."><title>Ministerienyt</title><link rel="alternate" type="application/rss+xml" title="Ministerienyt RSS" href="{feed_href}">
<style>
:root{{--ink:#18222c;--muted:#5d6974;--line:#dce2e7;--bg:#f4f6f7;--paper:#fff;--brand:#7d1b2a;--brand2:#5f1420;--new:#fff7e6;--max:1120px}}*{{box-sizing:border-box}}html{{color-scheme:light}}body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}a{{color:inherit}}a:focus-visible,input:focus-visible,select:focus-visible,button:focus-visible{{outline:3px solid #0867c8;outline-offset:3px}}.top{{background:var(--brand2);color:#fff}}.wrap{{width:min(calc(100% - 32px),var(--max));margin:auto}}.top .wrap{{min-height:54px;display:flex;align-items:center;justify-content:space-between;gap:18px}}.brand{{font-weight:800;letter-spacing:.01em}}.rss{{color:#fff;text-decoration:none}}.rss:hover{{text-decoration:underline}}.hero{{background:var(--paper);border-bottom:1px solid var(--line)}}.hero .wrap{{padding:48px 0 34px}}.eyebrow{{margin:0 0 10px;color:var(--brand);font-size:.82rem;font-weight:800;text-transform:uppercase;letter-spacing:.09em}}h1{{font-size:clamp(2.15rem,5vw,3.8rem);line-height:1.02;letter-spacing:-.04em;margin:0;max-width:900px}}.intro{{max-width:820px;color:var(--muted);font-size:1.08rem;margin:18px 0 0}}.archive-note{{display:inline-flex;margin-top:15px;padding:6px 10px;border-radius:999px;background:#f3e9eb;color:var(--brand2);font-weight:750;font-size:.88rem}}.controls{{display:grid;grid-template-columns:minmax(0,1.8fr) minmax(230px,1fr) auto;gap:12px;margin-top:28px;align-items:end}}label{{display:block;font-size:.84rem;font-weight:750;margin-bottom:6px}}input,select{{width:100%;min-height:49px;border:1px solid #aeb8c2;border-radius:7px;background:#fff;color:var(--ink);padding:10px 12px;font:inherit}}.new-only{{min-height:49px;border:1px solid #aeb8c2;border-radius:7px;background:#fff;color:var(--ink);padding:10px 14px;font:700 .92rem/1 system-ui;cursor:pointer}}.new-only[aria-pressed="true"]{{background:var(--brand2);color:#fff;border-color:var(--brand2)}}main.wrap{{padding:32px 0 58px}}.head{{display:flex;justify-content:space-between;align-items:end;gap:20px;margin-bottom:15px}}.head h2{{font-size:1.22rem;margin:0}}#count{{margin:0;color:var(--muted)}}.head-left{{display:grid;gap:3px}}.new-summary{{margin:0;color:var(--brand2);font-size:.92rem;font-weight:700}}.list{{display:grid;gap:14px}}.card{{background:var(--paper);border:1px solid var(--line);border-radius:10px;padding:22px}}.card.is-new{{border-left:5px solid var(--brand);padding-left:18px;background:var(--new);box-shadow:0 2px 10px rgba(95,20,32,.08)}}.card:hover{{border-color:#c3cbd2}}.meta{{display:flex;gap:8px 14px;flex-wrap:wrap;align-items:center;color:var(--muted);font-size:.88rem}}.source-name{{color:var(--brand2);font-weight:800}}.new-badge{{display:none;background:#f0d9dd;color:var(--brand2);border-radius:999px;padding:2px 8px;font-size:.76rem;line-height:1.5;text-transform:uppercase;letter-spacing:.04em;font-weight:850}}.card.is-new .new-badge{{display:inline-flex}}.card h2{{font-size:clamp(1.18rem,2.6vw,1.55rem);line-height:1.25;letter-spacing:-.012em;margin:8px 0 10px;font-weight:500}}.card.is-new h2{{font-weight:800}}.card h2 a{{text-decoration:none;font-weight:inherit}}.card h2 a:hover{{text-decoration:underline;text-decoration-thickness:2px;text-underline-offset:3px}}.card p{{color:#414d57;margin:0 0 14px;max-width:900px}}.more{{display:inline-block;color:var(--brand2);font-size:.94rem;font-weight:750;text-decoration:none}}.more:hover{{text-decoration:underline}}.empty{{display:none;background:#fff;border:1px solid var(--line);border-radius:10px;padding:28px;text-align:center;color:var(--muted)}}.sources{{margin-top:44px;padding-top:28px;border-top:1px solid var(--line)}}.sources h2{{margin:0 0 6px}}.sources>p{{color:var(--muted);margin:0 0 14px}}.table-wrap{{overflow:auto;background:#fff;border:1px solid var(--line);border-radius:10px}}table{{width:100%;border-collapse:collapse}}th,td{{padding:11px 14px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}}th{{font-size:.82rem;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}}tr:last-child td{{border-bottom:0}}td a{{color:var(--brand2)}}.warning{{display:inline-block;margin-left:6px;padding:1px 6px;border-radius:999px;background:#fff0cf;color:#784e00;font-size:.72rem;font-weight:800}}footer{{background:#fff;border-top:1px solid var(--line)}}footer .wrap{{padding:28px 0 38px;color:var(--muted);font-size:.9rem}}footer p{{margin:4px 0}}footer a{{color:var(--brand2)}}@media(max-width:800px){{.controls{{grid-template-columns:1fr}}.hero .wrap{{padding:34px 0 28px}}.card{{padding:18px}}.head{{align-items:start;flex-direction:column;gap:3px}}.new-only{{width:100%}}}}@media(prefers-reduced-motion:reduce){{*{{scroll-behavior:auto!important}}}}
</style></head><body>
<div class="top"><div class="wrap"><div class="brand">Ministerienyt</div><a class="rss" href="{feed_href}">RSS-feed</a></div></div>
<header class="hero"><div class="wrap"><p class="eyebrow">Samlet nyhedsoverblik</p><h1>Nyheder fra danske ministerier</h1><p class="intro">Nyheder og pressemeddelelser fra 21 ministerielle hjemmesider samt Regeringen.dk, samlet i én kronologisk oversigt.</p><span class="archive-note">Arkiv fra 1. januar 2026</span><div class="controls" role="search"><div><label for="search">Søg i nyheder</label><input id="search" type="search" placeholder="Fx klima, økonomi eller sundhed" autocomplete="off"></div><div><label for="ministry">Kilde</label><select id="ministry">{''.join(options)}</select></div><div><label for="new-only">Visning</label><button id="new-only" class="new-only" type="button" aria-pressed="false">Kun nye</button></div></div></div></header>
<main class="wrap"><div class="head"><div class="head-left"><h2>Nyhedsarkiv</h2><p id="new-summary" class="new-summary" aria-live="polite"></p></div><p id="count">{len(items)} nyheder</p></div><section class="list" id="list">{''.join(cards)}</section><div class="empty" id="empty">Ingen nyheder matcher dit filter.</div><section class="sources"><h2>Kilder og dækning</h2><p>Antallet viser, hvor mange artikler fra 1. januar 2026 der aktuelt er gemt i arkivet. Nogle historier kan optræde både hos et ministerium og på Regeringen.dk.</p><div class="table-wrap"><table><thead><tr><th>Kilde</th><th>Artikler</th><th>Fundet via</th></tr></thead><tbody>{''.join(source_rows)}</tbody></table></div></section></main>
<footer><div class="wrap"><p><strong>Ministerienyt</strong> er en uafhængig samling af links til officielle kilder.</p><p>Alle artikler åbner hos den oprindelige udgiver. Senest opdateret {esc(fmt_datetime_da(updated))}. <a href="{feed_href}">RSS-feed</a>.</p></div></footer>
<script>(()=>{{
const search=document.getElementById('search'),sourceSelect=document.getElementById('ministry'),newOnly=document.getElementById('new-only'),cards=[...document.querySelectorAll('.card')],count=document.getElementById('count'),empty=document.getElementById('empty'),newSummary=document.getElementById('new-summary');
const SEEN_KEY='ministerienyt.seenArticleIds.v2',VISIT_KEY='ministerienyt.lastVisit.v2';
const norm=value=>(value||'').toLocaleLowerCase('da-DK').trim();
let previousIds=null,lastVisit=null;
try{{const raw=localStorage.getItem(SEEN_KEY);if(raw)previousIds=new Set(JSON.parse(raw));lastVisit=localStorage.getItem(VISIT_KEY)}}catch(error){{previousIds=null;lastVisit=null}}
const currentIds=cards.map(card=>card.dataset.id).filter(Boolean);let newCount=0;
if(previousIds){{for(const card of cards){{if(card.dataset.id&&!previousIds.has(card.dataset.id)){{card.classList.add('is-new');newCount++}}}}}}
function visitText(value){{if(!value)return'';const date=new Date(value);if(Number.isNaN(date.getTime()))return'';return new Intl.DateTimeFormat('da-DK',{{dateStyle:'medium',timeStyle:'short'}}).format(date)}}
if(!previousIds){{newSummary.textContent='Nye artikler markeres fra dit næste besøg.'}}else if(newCount===0){{newSummary.textContent='Ingen nye artikler siden dit sidste besøg.'}}else{{const when=visitText(lastVisit);newSummary.textContent=(newCount===1?'1 ny artikel':newCount+' nye artikler')+' siden dit sidste besøg'+(when?' ('+when+')':'')+'.'}}
try{{const merged=[...(previousIds?[...previousIds]:[]),...currentIds];const unique=[...new Set(merged)].slice(-10000);localStorage.setItem(SEEN_KEY,JSON.stringify(unique));localStorage.setItem(VISIT_KEY,new Date().toISOString())}}catch(error){{}}
function applyFilters(){{const query=norm(search.value),selected=norm(sourceSelect.value),onlyNew=newOnly.getAttribute('aria-pressed')==='true';let visible=0;for(const card of cards){{const show=(!query||card.dataset.search.includes(query))&&(!selected||card.dataset.ministry===selected)&&(!onlyNew||card.classList.contains('is-new'));card.hidden=!show;if(show)visible++}}count.textContent=visible===1?'1 nyhed':visible+' nyheder';empty.style.display=visible?'none':'block';const url=new URL(location);query?url.searchParams.set('q',search.value.trim()):url.searchParams.delete('q');selected?url.searchParams.set('kilde',sourceSelect.value):url.searchParams.delete('kilde');onlyNew?url.searchParams.set('nye','1'):url.searchParams.delete('nye');history.replaceState(null,'',url)}}
const params=new URLSearchParams(location.search);if(params.get('q'))search.value=params.get('q');if(params.get('kilde'))sourceSelect.value=params.get('kilde');if(params.get('nye')==='1')newOnly.setAttribute('aria-pressed','true');search.addEventListener('input',applyFilters);sourceSelect.addEventListener('change',applyFilters);newOnly.addEventListener('click',()=>{{newOnly.setAttribute('aria-pressed',newOnly.getAttribute('aria-pressed')==='true'?'false':'true');applyFilters()}});applyFilters();
}})();</script>
</body></html>'''


def status_payload(statuses: list[SourceStatus], items: list[Item]) -> dict:
    counts = Counter(item.source for item in items)
    rows = []
    for status in statuses:
        status.archived_items = counts.get(status.name, 0)
        raw = asdict(status)
        raw["methods"] = list(dict.fromkeys(raw.get("methods") or []))
        rows.append(raw)
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "archive_start": ARCHIVE_START.date().isoformat(),
        "total_items": len(items),
        "sources": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", default="sources.json")
    parser.add_argument("--archive", default="archive.json")
    parser.add_argument("--rss-output", default="site/feed.xml")
    parser.add_argument("--html-output", default="site/index.html")
    parser.add_argument("--status-output", default="site/status.json")
    parser.add_argument("--site-url", default="https://example.invalid/")
    parser.add_argument("--feed-url", default="")
    args = parser.parse_args()

    started = time.monotonic()
    sources_path = Path(args.sources)
    sources = json.loads(sources_path.read_text(encoding="utf-8"))
    allowed_sources = {source["name"] for source in sources}
    archive_path = Path(args.archive)
    existing, archive_version = load_archive(archive_path, allowed_sources)
    refresh_sources = {
        source["name"]
        for source in sources
        if int(source.get("refresh_before_schema", 0) or 0) > archive_version
    }
    if refresh_sources:
        before = len(existing)
        existing = [item for item in existing if item.source not in refresh_sources]
        removed = before - len(existing)
        print(
            "Genopbygger korrigerede kilder: "
            + ", ".join(sorted(refresh_sources))
            + f" ({removed} gamle poster fjernet).",
            file=sys.stderr,
        )
    known_urls = {canonical_url(item.url) for item in existing}
    print(f"Eksisterende arkiv: {len(existing)} artikler.", file=sys.stderr)

    fresh, statuses = collect_fresh_items(sources, known_urls)
    merged = merge_archive(existing, fresh)
    if not merged:
        print("Ingen artikler kunne findes, og arkivet er tomt. Output blev ikke overskrevet.", file=sys.stderr)
        return 2

    if not archive_path.exists() or archive_signature(existing) != archive_signature(merged):
        save_archive(archive_path, merged)
        print(f"Arkivet blev opdateret: {len(merged)} artikler.", file=sys.stderr)
    else:
        print("Arkivet er uændret.", file=sys.stderr)

    rss_output = Path(args.rss_output)
    html_output = Path(args.html_output)
    status_output = Path(args.status_output)
    for output in (rss_output, html_output, status_output):
        output.parent.mkdir(parents=True, exist_ok=True)

    rss_output.write_bytes(build_rss(merged, args.site_url, args.feed_url))
    html_output.write_text(build_html(merged, args.feed_url or "feed.xml", sources, statuses), encoding="utf-8")
    status_output.write_text(
        json.dumps(status_payload(statuses, merged), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    elapsed = time.monotonic() - started
    covered = sum(1 for source in sources if any(item.source == source["name"] for item in merged))
    print(
        f"Færdig: {len(merged)} artikler fra {covered}/{len(sources)} kilder. "
        f"Kørselstid: {elapsed / 60:.1f} min.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
