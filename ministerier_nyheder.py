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
USER_AGENT = "Ministerienyt/4.6 (+https://github.com/; public Danish government news aggregator)"
CONNECT_TIMEOUT = 12
READ_TIMEOUT = 35
REQUEST_DELAY_SECONDS = 0.08
MAX_LISTING_PAGES_PER_SOURCE = 160
MAX_SITEMAP_FILES_PER_SOURCE = 100
MAX_ERROR_MESSAGES_PER_SOURCE = 12
ARCHIVE_SCHEMA_VERSION = 5

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


# Afviste kandidater gemmes kun som diagnostik i repositoryets rod. Filen
# publiceres ikke via GitHub Pages og indgår ikke i forsiden.
REJECTED_CANDIDATES: dict[tuple[str, str, str], dict] = {}

REJECTION_REASON_DA = {
    "missing_safe_publication_date": "Ingen sikker publiceringsdato",
    "future_publication_date": "Publiceringsdato ligger i fremtiden",
    "before_archive_start": "Publiceringsdato er før 1. januar 2026",
    "generic_title": "Manglende eller generisk overskrift",
    "not_article_url": "URL matcher ikke kildens nyhedsartikler",
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
    """Normalisér en publiceringsdato og afvis enhver dato i fremtiden.

    Ministerienyt viser kun allerede publicerede artikler. Derfor accepterer vi
    ikke planlagte udgivelsesdatoer eller datoer, som ved en fejl er hentet fra
    artikelteksten. En datoværdi uden tidszone behandles som UTC.
    """
    dt = (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    if dt.year < 2000:
        return None
    if dt > datetime.now(timezone.utc):
        return None
    return dt


def parse_date_unchecked(value: str) -> datetime | None:
    """Parse de samme datoformater som parse_date, men uden fremtidsfilter.

    Bruges kun diagnostisk til at kunne skelne mellem "ingen sikker dato" og
    "en ellers sikker publiceringsdato ligger i fremtiden". Funktionen må ikke
    bruges til at godkende en artikel.
    """
    if not value:
        return None
    value = clean_text(value)

    match = re.search(
        r"\b(20\d{2}-\d{2}-\d{2})(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?",
        value,
        flags=re.IGNORECASE,
    )
    if match:
        try:
            parsed = date_parser.parse(match.group(0))
            return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
        except Exception:
            pass

    match = re.search(
        r"\b(\d{1,2})\.?\s+(" + "|".join(DANISH_MONTHS) + r"),?\s+(20\d{2})\b",
        value.casefold(),
    )
    if match:
        try:
            return datetime(
                int(match.group(3)), DANISH_MONTHS[match.group(2)], int(match.group(1)), tzinfo=timezone.utc
            )
        except ValueError:
            return None

    match = re.search(r"\b(\d{1,2})[./-](\d{1,2})[./-](20\d{2})\b", value)
    if match:
        try:
            return datetime(int(match.group(3)), int(match.group(2)), int(match.group(1)), tzinfo=timezone.utc)
        except ValueError:
            return None

    english_month = re.search(
        r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\b",
        value,
        flags=re.IGNORECASE,
    )
    if english_month and re.search(r"\b20\d{2}\b", value):
        try:
            parsed = date_parser.parse(value, dayfirst=True, fuzzy=True)
            return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
        except Exception:
            pass
    return None


def future_date(value: str) -> datetime | None:
    parsed = parse_date_unchecked(value)
    if parsed and parsed > datetime.now(timezone.utc):
        return parsed
    return None


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
        r"\b(\d{1,2})\.?\s+(" + "|".join(DANISH_MONTHS) + r"),?\s+(20\d{2})\b",
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

def parse_labeled_publication_date(value: str) -> datetime | None:
    """Læs kun en dato, når teksten eksplicit markerer den som publiceringsdato."""
    if not value:
        return None
    text = clean_text(value)
    month_names = "|".join(DANISH_MONTHS)
    date_pattern = (
        rf"(\d{{1,2}}\.?\s+(?:{month_names})\s+20\d{{2}}"
        r"|\d{1,2}[.\-/]\d{1,2}[.\-/]20\d{2}"
        r"|20\d{2}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?)"
    )
    match = re.search(
        rf"\b(?:publiceret|offentliggjort|udgivet|publiceringsdato)\b\s*(?:den\s*)?(?::|[-–—])?\s*{date_pattern}",
        text,
        flags=re.IGNORECASE,
    )
    return parse_date(match.group(1)) if match else None


def metadata_publication_dates(soup: BeautifulSoup) -> list[datetime]:
    """Returnér publiceringsdatoer fra sidens strukturerede, officielle metadata."""
    values: list[str] = []
    for key, value in [
        ("property", "article:published_time"),
        ("property", "og:published_time"),
        ("name", "article:published_time"),
        ("itemprop", "datePublished"),
        ("name", "date"),
        ("name", "publish-date"),
        ("name", "dcterms.date"),
    ]:
        for tag in soup.find_all("meta", attrs={key: value}):
            if tag.get("content"):
                values.append(str(tag["content"]))

    # Semantiske <time>-felter er metadata. Almindelige <time>-tags i brødteksten
    # bruges ikke, medmindre de tydeligt er markeret som publiceringsdato.
    for tag in soup.find_all("time"):
        attrs_text = " ".join(
            [
                str(tag.get("itemprop", "")),
                str(tag.get("class", "")),
                str(tag.get("id", "")),
                str(tag.get("aria-label", "")),
            ]
        ).casefold()
        nearby = clean_text(tag.parent.get_text(" ", strip=True) if tag.parent else "")[:350]
        is_publication_time = (
            "datepublished" in attrs_text
            or "publish" in attrs_text
            or "publiceret" in attrs_text
            or parse_labeled_publication_date(nearby) is not None
        )
        if not is_publication_time:
            continue
        if tag.get("datetime"):
            values.append(str(tag["datetime"]))
        else:
            values.append(tag.get_text(" ", strip=True))

    # JSON-LD: datePublished er førstevalg. dateCreated bruges kun som officiel
    # fallback, hvis siden ikke leverer datePublished.
    published_values: list[str] = []
    created_values: list[str] = []
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
                if current.get("datePublished"):
                    published_values.append(str(current["datePublished"]))
                if current.get("dateCreated"):
                    created_values.append(str(current["dateCreated"]))
                stack.extend(v for v in current.values() if isinstance(v, (dict, list)))
            elif isinstance(current, list):
                stack.extend(current)
    values.extend(published_values or created_values)

    result: list[datetime] = []
    for value in values:
        parsed = parse_date(value)
        if parsed:
            result.append(parsed)
    return result


def labeled_publication_date_from_soup(soup: BeautifulSoup) -> datetime | None:
    """Find en synlig 'Publiceret …'-dato uden at scanne artikelens brødtekst."""
    label_re = re.compile(r"\b(?:publiceret|offentliggjort|udgivet|publiceringsdato)\b", re.IGNORECASE)
    for text_node in soup.find_all(string=label_re):
        node = getattr(text_node, "parent", None)
        # Gå kun få niveauer op og accepter kun små metadata-lignende blokke.
        for _ in range(4):
            if node is None:
                break
            text = clean_text(node.get_text(" ", strip=True))
            if 0 < len(text) <= 500:
                parsed = parse_labeled_publication_date(text)
                if parsed:
                    return parsed
            node = getattr(node, "parent", None)
    return None


def exact_date_text(value: str) -> datetime | None:
    """Læs en tekst, kun hvis hele feltet i praksis er en dato.

    Bruges udelukkende i afgrænsede metadata-/kortområder. Det er derfor ikke
    en genvej til at scanne artikelens brødtekst for datoer.
    """
    if not value:
        return None
    value = clean_text(value)
    month_names = "|".join(DANISH_MONTHS)
    patterns = [
        rf"^\d{{1,2}}\.?\s+(?:{month_names}),?\s+20\d{{2}}(?:\s*[-–—]\s*(?:kl\.?\s*)?\d{{1,2}}[.:]\d{{2}})?$",
        r"^\d{1,2}[./-]\d{1,2}[./-]20\d{2}(?:\s*[-–—]\s*(?:kl\.?\s*)?\d{1,2}[.:]\d{2})?$",
        r"^20\d{2}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$",
    ]
    if not any(re.fullmatch(pattern, value, flags=re.IGNORECASE) for pattern in patterns):
        return None
    return parse_date(value)


def header_publication_date_from_soup(soup: BeautifulSoup) -> datetime | None:
    """Læs en umærket datolinje umiddelbart før artikelens H1.

    Nogle officielle ministeriesider (fx svmn.dk) viser publiceringsdatoen som
    en selvstændig datolinje lige før overskriften, uden schema.org-metadata og
    uden ordet 'Publiceret'. Vi accepterer kun en dato, hvis hele tekstnoden er
    en dato og den ligger blandt de nærmeste tekstnoder FØR H1. Dermed kan en
    dato senere i brødteksten aldrig blive brugt som publiceringsdato.
    """
    h1 = soup.find("h1")
    if h1 is None:
        return None
    checked = 0
    for text_node in h1.find_all_previous(string=True):
        value = clean_text(str(text_node))
        if not value:
            continue
        checked += 1
        parsed = exact_date_text(value)
        if parsed:
            return parsed
        # Hold søgningen helt tæt på artikelhovedet; navigationsdatoer længere
        # oppe på siden må ikke kunne blive valgt.
        if checked >= 12:
            break
    return None


def plain_listing_date_from_node(node) -> datetime | None:
    """Læs en ren datolinje fra ét officielt nyhedskort.

    Denne fallback er opt-in pr. kilde og bruges fx på kum.dk, hvor datoen står
    i samme kort som overskrift/manchet, men uden 'Publiceret'-label. Vi ser kun
    efter tekstnoder, der udelukkende består af en dato.
    """
    for text_node in node.find_all(string=True):
        parsed = exact_date_text(str(text_node))
        if parsed:
            return parsed
    return None


def date_from_soup(soup: BeautifulSoup, source: dict | None = None) -> datetime | None:
    """Find artikelens publiceringsdato uden at læse vilkårlige brødtekstdatoer.

    Prioritet:
    1) officielle strukturerede metadata (article:published_time, datePublished osv.)
    2) en synlig dato eksplicit markeret 'Publiceret', 'Udgivet' mv.
    3) kun for opt-in-kilder: en ren datolinje helt tæt på og FØR artikelens H1

    Der er bevidst ingen fallback til hele sidens tekst.
    """
    metadata_dates = metadata_publication_dates(soup)
    if metadata_dates:
        return metadata_dates[0]
    labeled = labeled_publication_date_from_soup(soup)
    if labeled:
        return labeled
    if source and source.get("allow_unlabeled_header_date"):
        return header_publication_date_from_soup(soup)
    return None


def date_from_listing_node(node, source: dict | None = None) -> datetime | None:
    """Læs publiceringsdato fra et afgrænset officielt nyhedskort."""
    metadata_dates = metadata_publication_dates(node)
    if metadata_dates:
        return metadata_dates[0]
    labeled = parse_labeled_publication_date(clean_text(node.get_text(" ", strip=True)))
    if labeled:
        return labeled
    # Et <time datetime> i en liste er i sig selv semantisk metadata og ikke
    # artikelbrødtekst. Det må derfor bruges som næste listing-fallback.
    for tag in node.find_all("time"):
        value = str(tag.get("datetime", "")) or tag.get_text(" ", strip=True)
        parsed = parse_date(value)
        if parsed:
            return parsed
    if source and source.get("allow_plain_listing_date"):
        return plain_listing_date_from_node(node)
    return None


def trusted_future_publication_date_from_soup(
    soup: BeautifulSoup,
    source: dict | None = None,
) -> datetime | None:
    """Find kun en FREMTIDIG dato i de samme sikre felter som date_from_soup.

    Funktionen er diagnostik til afvisningsloggen. Den scanner aldrig den
    egentlige artikelbrødtekst for vilkårlige datoer.
    """
    raw_values: list[str] = []
    for key, value in [
        ("property", "article:published_time"),
        ("property", "og:published_time"),
        ("name", "article:published_time"),
        ("itemprop", "datePublished"),
        ("name", "date"),
        ("name", "publish-date"),
        ("name", "dcterms.date"),
    ]:
        for tag in soup.find_all("meta", attrs={key: value}):
            if tag.get("content"):
                raw_values.append(str(tag["content"]))

    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except Exception:
            continue
        stack = obj if isinstance(obj, list) else [obj]
        published_values: list[str] = []
        created_values: list[str] = []
        while stack:
            current = stack.pop()
            if isinstance(current, dict):
                if current.get("datePublished"):
                    published_values.append(str(current["datePublished"]))
                if current.get("dateCreated"):
                    created_values.append(str(current["dateCreated"]))
                stack.extend(v for v in current.values() if isinstance(v, (dict, list)))
            elif isinstance(current, list):
                stack.extend(current)
        raw_values.extend(published_values or created_values)

    label_re = re.compile(r"\b(?:publiceret|offentliggjort|udgivet|publiceringsdato)\b", re.IGNORECASE)
    month_names = "|".join(DANISH_MONTHS)
    date_pattern = (
        rf"(\d{{1,2}}\.?\s+(?:{month_names})\s+20\d{{2}}"
        r"|\d{1,2}[.\-/]\d{1,2}[.\-/]20\d{2}"
        r"|20\d{2}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?)"
    )
    for text_node in soup.find_all(string=label_re):
        node = getattr(text_node, "parent", None)
        for _ in range(4):
            if node is None:
                break
            value = clean_text(node.get_text(" ", strip=True))
            if 0 < len(value) <= 500:
                match = re.search(
                    rf"\b(?:publiceret|offentliggjort|udgivet|publiceringsdato)\b\s*(?:den\s*)?(?::|[-–—])?\s*{date_pattern}",
                    value,
                    flags=re.IGNORECASE,
                )
                if match:
                    raw_values.append(match.group(1))
            node = getattr(node, "parent", None)

    if source and source.get("allow_unlabeled_header_date"):
        h1 = soup.find("h1")
        if h1 is not None:
            checked = 0
            for text_node in h1.find_all_previous(string=True):
                value = clean_text(str(text_node))
                if not value:
                    continue
                checked += 1
                month_names = "|".join(DANISH_MONTHS)
                patterns = [
                    rf"^\d{{1,2}}\.?\s+(?:{month_names}),?\s+20\d{{2}}(?:\s*[-–—]\s*(?:kl\.?\s*)?\d{{1,2}}[.:]\d{{2}})?$",
                    r"^\d{1,2}[./-]\d{1,2}[./-]20\d{2}(?:\s*[-–—]\s*(?:kl\.?\s*)?\d{1,2}[.:]\d{2})?$",
                    r"^20\d{2}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$",
                ]
                if any(re.fullmatch(pattern, value, flags=re.IGNORECASE) for pattern in patterns):
                    raw_values.append(value)
                if checked >= 12:
                    break

    futures = [future_date(value) for value in raw_values]
    futures = [value for value in futures if value]
    return min(futures) if futures else None


def trusted_future_publication_date_from_context(context: str, source: dict) -> datetime | None:
    """Diagnostisk fremtidsdato fra et afgrænset listing-kort."""
    text = clean_text(context)
    if not text:
        return None
    # Mærket publiceringsdato er altid sikker metadata.
    label_match = re.search(
        r"\b(?:publiceret|offentliggjort|udgivet|publiceringsdato)\b.{0,60}?"
        r"(\d{1,2}[./-]\d{1,2}[./-]20\d{2}|\d{1,2}\.?\s+(?:"
        + "|".join(DANISH_MONTHS)
        + r")\s+20\d{2}|20\d{2}-\d{2}-\d{2})",
        text,
        flags=re.IGNORECASE,
    )
    if label_match:
        future = future_date(label_match.group(1))
        if future:
            return future

    if source.get("allow_plain_listing_date"):
        # Context kommer fra ét afgrænset nyhedskort; find dato-tokenet, men kun
        # på kilder hvor denne fallback eksplicit er godkendt.
        patterns = [
            r"\b\d{1,2}[./-]\d{1,2}[./-]20\d{2}\b",
            r"\b\d{1,2}\.?\s+(?:" + "|".join(DANISH_MONTHS) + r")\s+20\d{2}\b",
            r"\b20\d{2}-\d{2}-\d{2}\b",
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                future = future_date(match.group(0))
                if future:
                    return future
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


def strip_leading_publication_date(value: str) -> str:
    """Fjern publiceringsmetadata fra starten af en manchet/brødtekst.

    Datoen vises allerede separat på nyhedskortet. Funktionen er bevidst
    begrænset til starten af teksten, så datoer inde i den egentlige artikel
    ikke fjernes. Den køres også ved HTML-rendering, så gamle poster i
    archive.json bliver rettet uden at arkivet skal slettes.
    """
    text = clean_text(value)
    if not text:
        return ""

    month_names = "|".join(DANISH_MONTHS)
    date_pattern = (
        rf"(?:\d{{1,2}}\.?\s+(?:{month_names})\s+20\d{{2}}"
        r"|\d{1,2}[.\-/]\d{1,2}[.\-/]20\d{2}"
        r"|20\d{2}-\d{2}-\d{2})"
    )
    prefix_pattern = (
        r"(?:(?:pressemeddelelse|nyhed)(?:\s*-\s*ligestilling)?\s*(?:[/|–—-]\s*)?)?"
        r"(?:(?:publiceret|offentliggjort|udgivet|opdateret|senest\s+opdateret)"
        r"\s*(?:den\s*)?(?::|[-–—])?\s*)?"
    )
    time_pattern = r"(?:\s*(?:kl\.?|klokken)\s*\d{1,2}(?::|\.)\d{2})?"
    trailing_separator = r"\s*(?:[|/–—:-]\s*)?"

    # Højst et par runder er nødvendige, fx hvis både type og dato gentages.
    for _ in range(3):
        updated = re.sub(
            rf"^{prefix_pattern}{date_pattern}{time_pattern}{trailing_separator}",
            "",
            text,
            count=1,
            flags=re.IGNORECASE,
        ).strip()
        if updated == text:
            break
        text = updated
    return clean_text(text)


def tidy_description_text(value: str, title: str = "") -> str:
    """Rens en manchet eller det første brødtekstafsnit fra en artikelside."""
    text = strip_leading_publication_date(value)
    if not text:
        return ""

    if title and text.casefold().startswith(title.casefold()):
        text = text[len(title) :].lstrip(" .:–—-/")

    # Datoen kan stå umiddelbart efter en gentaget overskrift.
    text = strip_leading_publication_date(text)
    return clean_text(text)


def useful_description(value: str, title: str = "") -> str:
    text = tidy_description_text(value, title)
    if len(text) < 40:
        return ""
    folded = text.casefold()
    if title and folded == title.casefold():
        return ""
    if any(
        folded.startswith(prefix)
        for prefix in (
            "vi bruger cookies",
            "denne hjemmeside bruger cookies",
            "gå til sidens indhold",
            "beskæftigelsesministeriet nyheder",
        )
    ):
        return ""
    return text[:900]


def description_from_soup(
    soup: BeautifulSoup,
    title: str = "",
    selectors: Iterable[str] | None = None,
) -> str:
    """Find en kort, læsbar manchet eller begyndelsen af brødteksten.

    Nogle ministeriesider har ingen brugbar meta-description. Derfor søger vi
    også i JSON-LD, manchet-/lead-felter og de første afsnit efter H1. Når en
    kilde har egne selectors, prioriteres sidens synlige tekst før metadata.
    Det er især nødvendigt på bm.dk's pressemeddelelser.
    """
    scope = soup.find("main") or soup.find("article") or soup

    def from_selectors(values: Iterable[str]) -> str:
        seen_nodes: set[int] = set()
        for selector in values:
            try:
                nodes = scope.select(selector)
            except Exception:
                continue
            for node in nodes:
                marker = id(node)
                if marker in seen_nodes:
                    continue
                seen_nodes.add(marker)
                candidate = useful_description(node.get_text(" ", strip=True), title)
                if candidate:
                    return candidate
        return ""

    configured = list(selectors or [])
    if configured:
        candidate = from_selectors(configured)
        if candidate:
            return candidate

        h1 = scope.find("h1")
        if h1:
            for paragraph in h1.find_all_next("p", limit=16):
                if scope is not soup and paragraph not in scope.descendants:
                    break
                candidate = useful_description(paragraph.get_text(" ", strip=True), title)
                if candidate:
                    return candidate

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
                for field in ("description", "articleBody"):
                    if current.get(field):
                        candidate = useful_description(str(current[field]), title)
                        if candidate:
                            return candidate
                stack.extend(v for v in current.values() if isinstance(v, (dict, list)))
            elif isinstance(current, list):
                stack.extend(current)

    for attrs in (
        {"property": "og:description"},
        {"name": "description"},
        {"name": "twitter:description"},
    ):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            candidate = useful_description(str(tag["content"]), title)
            if candidate:
                return candidate

    preferred = [
        ".manchet",
        ".lead",
        ".intro",
        ".teaser",
        ".article__lead",
        ".article__intro",
        ".article-summary",
        ".page-intro",
        '[class*="manchet"]',
        '[class*="lead"]',
        '[class*="intro"]',
    ]
    candidate = from_selectors(preferred)
    if candidate:
        return candidate

    h1 = scope.find("h1")
    if h1:
        for paragraph in h1.find_all_next("p", limit=16):
            if scope is not soup and paragraph not in scope.descendants:
                break
            candidate = useful_description(paragraph.get_text(" ", strip=True), title)
            if candidate:
                return candidate

    for paragraph in scope.find_all("p"):
        candidate = useful_description(paragraph.get_text(" ", strip=True), title)
        if candidate:
            return candidate
    return ""

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
    published = date_from_listing_node(node, source)
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


def record_rejection(
    source: str,
    title: str,
    url: str,
    reason: str,
    *,
    discovered_by: str = "",
    detail: str = "",
    detected_date: datetime | None = None,
) -> None:
    normalized_url = normalize_url(url, keep_query=True) or url
    key = (source, canonical_url(normalized_url) or normalized_url, reason)
    entry = {
        "source": source,
        "title": clean_text(title),
        "url": normalized_url,
        "reason": reason,
        "reason_da": REJECTION_REASON_DA.get(reason, reason),
        "discovered_by": discovered_by,
        "detail": clean_text(detail)[:500],
    }
    if detected_date:
        entry["detected_date"] = detected_date.astimezone(timezone.utc).isoformat()
    existing = REJECTED_CANDIDATES.get(key)
    if existing:
        # Bevar mest informative titel/detalje og kombiner fundmetoder.
        if len(entry["title"]) > len(existing.get("title", "")):
            existing["title"] = entry["title"]
        if len(entry["detail"]) > len(existing.get("detail", "")):
            existing["detail"] = entry["detail"]
        methods = [m for m in (existing.get("discovered_by", "") + "+" + discovered_by).split("+") if m]
        existing["discovered_by"] = "+".join(dict.fromkeys(methods))
        if detected_date and not existing.get("detected_date"):
            existing["detected_date"] = detected_date.astimezone(timezone.utc).isoformat()
        return
    REJECTED_CANDIDATES[key] = entry


def save_rejection_log(path: Path) -> None:
    entries = sorted(
        REJECTED_CANDIDATES.values(),
        key=lambda entry: (entry.get("reason", ""), entry.get("source", ""), entry.get("title", "")),
    )
    counts = Counter(entry.get("reason", "unknown") for entry in entries)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": "Diagnostik fra seneste crawl. Filen publiceres ikke på GitHub Pages.",
        "total_rejected": len(entries),
        "summary_by_reason": dict(sorted(counts.items())),
        "rejected": entries,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
            future_published = None
            for field in ("published", "updated", "created"):
                value = getattr(entry, field, None)
                if value:
                    parsed = parse_date(str(value))
                    if parsed:
                        published = parsed
                        break
                    if future_published is None:
                        future_published = future_date(str(value))
            link = normalize_url(str(getattr(entry, "link", "") or ""), keep_query=True)
            title = clean_text(str(getattr(entry, "title", "") or ""))
            if not link or not looks_like_article(link, source):
                continue
            if is_generic_title(title):
                record_rejection(source["name"], title, link, "generic_title", discovered_by="RSS/Atom")
                continue
            if not published:
                if future_published:
                    record_rejection(
                        source["name"], title, link, "future_publication_date",
                        discovered_by="RSS/Atom", detected_date=future_published,
                    )
                else:
                    record_rejection(source["name"], title, link, "missing_safe_publication_date", discovered_by="RSS/Atom")
                continue
            if published < ARCHIVE_START:
                record_rejection(
                    source["name"], title, link, "before_archive_start",
                    discovered_by="RSS/Atom", detected_date=published,
                )
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
    article_soup = None
    fetch_error = ""

    if published and published < ARCHIVE_START:
        record_rejection(
            source["name"], title, final_url, "before_archive_start",
            discovered_by=candidate.discovered_by, detected_date=published,
        )
        return None

    must_fetch = bool(source.get("always_fetch_articles")) or not published or is_generic_title(title)
    if must_fetch:
        try:
            response = fetch(session, candidate.url)
            status.article_fetches += 1
            final_url = normalize_url(response.url, keep_query=True) or candidate.url
            article_soup = BeautifulSoup(response.text, "html.parser")
            published = date_from_soup(article_soup, source) or published
            page_title = title_from_soup(article_soup)
            if page_title and not is_generic_title(page_title):
                title = page_title
            page_description = description_from_soup(
                article_soup,
                title,
                source.get("description_selectors"),
            )
            if page_description:
                description = page_description
        except Exception as exc:
            fetch_error = str(exc)
            append_error(status, f"Artikel kunne ikke hentes: {candidate.url}: {exc}")

    if not published:
        future_published = None
        if article_soup is not None:
            future_published = trusted_future_publication_date_from_soup(article_soup, source)
        if future_published is None:
            future_published = trusted_future_publication_date_from_context(candidate.context, source)
        if future_published:
            record_rejection(
                source["name"], title, final_url, "future_publication_date",
                discovered_by=candidate.discovered_by,
                detail=("Artikelhentning fejlede: " + fetch_error) if fetch_error else "",
                detected_date=future_published,
            )
        else:
            record_rejection(
                source["name"], title, final_url, "missing_safe_publication_date",
                discovered_by=candidate.discovered_by,
                detail=("Artikelhentning fejlede: " + fetch_error) if fetch_error else "",
            )
        return None
    if published < ARCHIVE_START:
        record_rejection(
            source["name"], title, final_url, "before_archive_start",
            discovered_by=candidate.discovered_by, detected_date=published,
        )
        return None
    if is_generic_title(title):
        record_rejection(source["name"], title, final_url, "generic_title", discovered_by=candidate.discovered_by)
        return None
    if not looks_like_article(final_url, source):
        record_rejection(source["name"], title, final_url, "not_article_url", discovered_by=candidate.discovered_by)
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
            raw_release_date = str(release.get("date", ""))
            published = parse_date(raw_release_date)
            if not published:
                future_published = future_date(raw_release_date)
                versions = release.get("versions", {})
                version = versions.get("da") if isinstance(versions, dict) else {}
                title_hint = clean_text(str(version.get("title", ""))) if isinstance(version, dict) else ""
                url_hint = ritzau_public_url(str(version.get("url", ""))) if isinstance(version, dict) else endpoint
                record_rejection(
                    source["name"], title_hint, url_hint,
                    "future_publication_date" if future_published else "missing_safe_publication_date",
                    discovered_by="Via Ritzau API", detected_date=future_published,
                )
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
    ET.SubElement(channel, "generator").text = "Ministerienyt 4.4"
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
        # Rens også ved rendering, så allerede arkiverede beskrivelser ikke
        # viser en dubleret publiceringsdato i selve brødteksten.
        description = tidy_description_text(item.description, item.title)
        if len(description) > 280:
            description = description[:277].rstrip() + "..."
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
:root{{--ink:#18222c;--muted:#5d6974;--line:#dce2e7;--bg:#f4f6f7;--paper:#fff;--brand:#7d1b2a;--brand2:#5f1420;--new:#fff7e6;--max:1120px}}*{{box-sizing:border-box}}[hidden]{{display:none!important}}html{{color-scheme:light}}body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}a{{color:inherit}}a:focus-visible,input:focus-visible,select:focus-visible,button:focus-visible{{outline:3px solid #0867c8;outline-offset:3px}}.sr-only{{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}}.top{{background:var(--brand2);color:#fff}}.wrap{{width:min(calc(100% - 32px),var(--max));margin:auto}}.top .wrap{{min-height:48px;display:flex;align-items:center;justify-content:space-between;gap:18px}}.brand{{font-weight:800;letter-spacing:.01em}}.rss{{color:#fff;text-decoration:none}}.rss:hover{{text-decoration:underline}}.hero{{background:var(--paper);border-bottom:1px solid var(--line)}}.hero .wrap{{padding:24px 0 20px}}h1{{font-size:clamp(1.9rem,4vw,3rem);line-height:1.04;letter-spacing:-.035em;margin:0;max-width:900px}}.intro{{max-width:none;color:var(--muted);font-size:1rem;margin:8px 0 0}}.controls{{display:grid;grid-template-columns:minmax(0,1.8fr) minmax(230px,1fr) auto;gap:10px;margin-top:16px;align-items:end}}label{{display:block;font-size:.82rem;font-weight:750;margin-bottom:5px}}input,select{{width:100%;min-height:44px;border:1px solid #aeb8c2;border-radius:7px;background:#fff;color:var(--ink);padding:8px 11px;font:inherit}}.new-only{{min-height:44px;border:1px solid #aeb8c2;border-radius:7px;background:#fff;color:var(--ink);padding:9px 13px;font:700 .92rem/1 system-ui;cursor:pointer}}.new-only[aria-pressed="true"]{{background:var(--brand2);color:#fff;border-color:var(--brand2)}}main.wrap{{padding:22px 0 48px}}.head{{display:flex;justify-content:space-between;align-items:end;gap:20px;margin-bottom:11px}}.head h2{{font-size:1.16rem;margin:0}}#count{{margin:0;color:var(--muted)}}.head-left{{display:grid;gap:2px}}.new-summary{{margin:0;color:var(--brand2);font-size:.88rem;font-weight:700}}.list{{display:grid;gap:10px}}.card{{background:var(--paper);border:1px solid var(--line);border-radius:9px;padding:17px 18px}}.card.is-new{{border-left:5px solid var(--brand);padding-left:14px;background:var(--new);box-shadow:0 2px 8px rgba(95,20,32,.07)}}.card:hover{{border-color:#c3cbd2}}.meta{{display:flex;gap:6px 12px;flex-wrap:wrap;align-items:center;color:var(--muted);font-size:.82rem}}.source-name{{color:var(--brand2);font-weight:800}}.new-badge{{display:none;background:#f0d9dd;color:var(--brand2);border-radius:999px;padding:2px 7px;font-size:.72rem;line-height:1.45;text-transform:uppercase;letter-spacing:.04em;font-weight:850}}.card.is-new .new-badge{{display:inline-flex}}.card h2{{font-size:clamp(1.08rem,2.3vw,1.38rem);line-height:1.23;letter-spacing:-.01em;margin:5px 0 7px;font-weight:500}}.card.is-new h2{{font-weight:800}}.card h2 a{{text-decoration:none;font-weight:inherit}}.card h2 a:hover{{text-decoration:underline;text-decoration-thickness:2px;text-underline-offset:3px}}.card p{{color:#414d57;margin:0 0 9px;max-width:900px;line-height:1.42}}.more{{display:inline-block;color:var(--brand2);font-size:.9rem;font-weight:750;text-decoration:none}}.more:hover{{text-decoration:underline}}.load-more{{display:block;margin:16px auto 0;min-height:44px;border:1px solid var(--brand2);border-radius:7px;background:#fff;color:var(--brand2);padding:9px 18px;font:750 .94rem/1 system-ui;cursor:pointer}}.load-more:hover{{background:#f8f1f2}}.empty{{display:none;background:#fff;border:1px solid var(--line);border-radius:10px;padding:24px;text-align:center;color:var(--muted)}}.sources{{margin-top:28px;border-top:1px solid var(--line);padding-top:14px}}.sources summary{{display:flex;align-items:center;gap:6px;cursor:pointer;list-style:none;width:max-content;max-width:100%;color:var(--brand2);font-weight:800;font-size:.96rem;padding:7px 0}}.sources summary::-webkit-details-marker{{display:none}}.sources summary::before{{content:"＋";display:inline-grid;place-items:center;width:1.35rem;height:1.35rem;border:1px solid #b8c0c7;border-radius:50%;font-size:.9rem;line-height:1;color:var(--brand2);background:#fff}}.sources[open] summary::before{{content:"−"}}.source-count{{color:var(--muted);font-weight:500}}.sources-content{{padding-top:6px}}.sources-content>p{{color:var(--muted);margin:0 0 12px;font-size:.9rem}}.table-wrap{{overflow:auto;background:#fff;border:1px solid var(--line);border-radius:10px}}table{{width:100%;border-collapse:collapse}}th,td{{padding:11px 14px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}}th{{font-size:.82rem;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}}tr:last-child td{{border-bottom:0}}td a{{color:var(--brand2)}}.warning{{display:inline-block;margin-left:6px;padding:1px 6px;border-radius:999px;background:#fff0cf;color:#784e00;font-size:.72rem;font-weight:800}}footer{{background:#fff;border-top:1px solid var(--line)}}footer .wrap{{padding:24px 0 32px;color:var(--muted);font-size:.9rem}}footer p{{margin:4px 0}}footer a{{color:var(--brand2)}}@media(min-width:700px){{.intro{{white-space:nowrap}}}}@media(max-width:800px){{.controls{{grid-template-columns:1fr}}.hero .wrap{{padding:20px 0 18px}}.card{{padding:15px 16px}}.head{{align-items:start;flex-direction:column;gap:3px}}.new-only{{width:100%}}}}@media(prefers-reduced-motion:reduce){{*{{scroll-behavior:auto!important}}}}
</style></head><body>
<div class="top"><div class="wrap"><div class="brand">Ministerienyt</div><a class="rss" href="{feed_href}">RSS-feed</a></div></div>
<header class="hero"><div class="wrap"><h1>Nyheder fra danske ministerier</h1><p class="intro">Seneste nyt fra ministerierne og Regeringen.dk – samlet ét sted.</p><div class="controls" role="search"><div class="search-field"><label class="sr-only" for="search">Søg i nyheder</label><input id="search" type="search" placeholder="Søg fx klima, økonomi eller sundhed" aria-label="Søg i nyheder" autocomplete="off"></div><div><label for="ministry">Kilde</label><select id="ministry">{''.join(options)}</select></div><div><label for="new-only">Visning</label><button id="new-only" class="new-only" type="button" aria-pressed="false">Kun nye</button></div></div></div></header>
<main class="wrap"><div class="head"><div class="head-left"><h2>Nyhedsarkiv</h2><p id="new-summary" class="new-summary" aria-live="polite"></p></div><p id="count">{len(items)} nyheder</p></div><section class="list" id="list">{''.join(cards)}</section><button id="load-more" class="load-more" type="button" hidden>Vis flere nyheder</button><div class="empty" id="empty">Ingen nyheder matcher dit filter.</div><details class="sources"><summary>Kilder og dækning <span class="source-count">({len(ministries)} kilder)</span></summary><div class="sources-content"><p>Antallet viser, hvor mange artikler fra 1. januar 2026 der aktuelt er gemt i arkivet. Nogle historier kan optræde både hos et ministerium og på Regeringen.dk.</p><div class="table-wrap"><table><thead><tr><th>Kilde</th><th>Artikler</th><th>Fundet via</th></tr></thead><tbody>{''.join(source_rows)}</tbody></table></div></div></details></main>
<footer><div class="wrap"><p><strong>Ministerienyt</strong> er en uafhængig samling af links til officielle kilder.</p><p>Alle artikler åbner hos den oprindelige udgiver. Senest opdateret {esc(fmt_datetime_da(updated))}. <a href="{feed_href}">RSS-feed</a>.</p></div></footer>
<script>(()=>{{
const search=document.getElementById('search'),sourceSelect=document.getElementById('ministry'),newOnly=document.getElementById('new-only'),loadMore=document.getElementById('load-more'),cards=[...document.querySelectorAll('.card')],count=document.getElementById('count'),empty=document.getElementById('empty'),newSummary=document.getElementById('new-summary');
const SEEN_KEY='ministerienyt.seenArticleIds.v2',VISIT_KEY='ministerienyt.lastVisit.v2',PAGE_SIZE=15;
const norm=value=>(value||'').toLocaleLowerCase('da-DK').trim();
let previousIds=null,lastVisit=null,visibleLimit=PAGE_SIZE;
try{{const raw=localStorage.getItem(SEEN_KEY);if(raw)previousIds=new Set(JSON.parse(raw));lastVisit=localStorage.getItem(VISIT_KEY)}}catch(error){{previousIds=null;lastVisit=null}}
const currentIds=cards.map(card=>card.dataset.id).filter(Boolean);let newCount=0;
if(previousIds){{for(const card of cards){{if(card.dataset.id&&!previousIds.has(card.dataset.id)){{card.classList.add('is-new');newCount++}}}}}}
function visitText(value){{if(!value)return'';const date=new Date(value);if(Number.isNaN(date.getTime()))return'';return new Intl.DateTimeFormat('da-DK',{{dateStyle:'medium',timeStyle:'short'}}).format(date)}}
if(!previousIds){{newSummary.textContent='Nye artikler markeres fra dit næste besøg.'}}else if(newCount===0){{newSummary.textContent='Ingen nye artikler siden dit sidste besøg.'}}else{{const when=visitText(lastVisit);newSummary.textContent=(newCount===1?'1 ny artikel':newCount+' nye artikler')+' siden dit sidste besøg'+(when?' ('+when+')':'')+'.'}}
try{{const merged=[...(previousIds?[...previousIds]:[]),...currentIds];const unique=[...new Set(merged)].slice(-10000);localStorage.setItem(SEEN_KEY,JSON.stringify(unique));localStorage.setItem(VISIT_KEY,new Date().toISOString())}}catch(error){{}}
function applyFilters(resetLimit=false){{
 if(resetLimit)visibleLimit=PAGE_SIZE;
 const query=norm(search.value),selected=norm(sourceSelect.value),onlyNew=newOnly.getAttribute('aria-pressed')==='true',matching=[];
 for(const card of cards){{const match=(!query||card.dataset.search.includes(query))&&(!selected||card.dataset.ministry===selected)&&(!onlyNew||card.classList.contains('is-new'));if(match)matching.push(card);else card.hidden=true}}
 matching.forEach((card,index)=>{{card.hidden=index>=visibleLimit}});
 const shown=Math.min(visibleLimit,matching.length),remaining=Math.max(0,matching.length-shown);
 if(matching.length===0)count.textContent='0 nyheder';else if(remaining>0)count.textContent=shown+' af '+matching.length+' nyheder';else count.textContent=matching.length===1?'1 nyhed':matching.length+' nyheder';
 empty.style.display=matching.length?'none':'block';
 loadMore.hidden=remaining===0;
 if(remaining>0){{const next=Math.min(PAGE_SIZE,remaining);loadMore.textContent=remaining<=PAGE_SIZE?'Vis de sidste '+remaining+' nyheder':'Vis '+next+' flere nyheder'}}
 const url=new URL(location);query?url.searchParams.set('q',search.value.trim()):url.searchParams.delete('q');selected?url.searchParams.set('kilde',sourceSelect.value):url.searchParams.delete('kilde');onlyNew?url.searchParams.set('nye','1'):url.searchParams.delete('nye');history.replaceState(null,'',url)
}}
const params=new URLSearchParams(location.search);if(params.get('q'))search.value=params.get('q');if(params.get('kilde'))sourceSelect.value=params.get('kilde');if(params.get('nye')==='1')newOnly.setAttribute('aria-pressed','true');
search.addEventListener('input',()=>applyFilters(true));sourceSelect.addEventListener('change',()=>applyFilters(true));newOnly.addEventListener('click',()=>{{newOnly.setAttribute('aria-pressed',newOnly.getAttribute('aria-pressed')==='true'?'false':'true');applyFilters(true)}});loadMore.addEventListener('click',()=>{{visibleLimit+=PAGE_SIZE;applyFilters(false)}});applyFilters(true);
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
    parser.add_argument("--rejected-log", default="rejected_candidates.json")
    parser.add_argument("--site-url", default="https://example.invalid/")
    parser.add_argument("--feed-url", default="")
    args = parser.parse_args()

    started = time.monotonic()
    REJECTED_CANDIDATES.clear()
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
    save_rejection_log(Path(args.rejected_log))
    print(f"Afvisningslog: {len(REJECTED_CANDIDATES)} kandidater.", file=sys.stderr)
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
