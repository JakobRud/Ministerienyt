#!/usr/bin/env python3
"""Ministerienyt: samlet nyhedsarkiv fra danske ministerier.

Programmet henter officielle nyheder fra 21 ministerielle hjemmesider samt
Regeringen.dk, gemmer et vedvarende arkiv fra 1. januar 2026 og bygger:

* site/index.html  - søgbar, mobilvenlig hjemmeside
* site/feed.xml    - samlet RSS 2.0-feed
* health.json     - kildestatus fra seneste kørsel
* diagnostics.json- intern kvalitetsrapport fra seneste kørsel
* archive.json     - vedvarende arkiv, som GitHub Actions committer tilbage

Kilderne ligger i sources.json. Hver kilde kan bruge RSS/Atom, HTML-arkiver,
paginering og XML-sitemaps. Metoderne kombineres i stedet for at stoppe efter
første fund, så historikken bliver så komplet som muligt.
"""
from __future__ import annotations

import argparse
import email.utils
import gzip
import difflib
import unicodedata
import hashlib
import html
import json
import re
import sys
import time
import struct
import zlib
from collections import Counter, deque
from statistics import median
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
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
USER_AGENT = "Ministerienyt/5.4 (+https://github.com/; public Danish government news aggregator)"
CONNECT_TIMEOUT = 12
READ_TIMEOUT = 35
REQUEST_DELAY_SECONDS = 0.08
MAX_LISTING_PAGES_PER_SOURCE = 160
MAX_SITEMAP_FILES_PER_SOURCE = 100
MAX_ERROR_MESSAGES_PER_SOURCE = 12
ARCHIVE_SCHEMA_VERSION = 7

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

# Domæneskift, hvor gamle og nye officielle URL'er kan pege på samme artikel.
# Bruges kun til intern dublet-/cacheidentifikation; det viste link bevares.
CANONICAL_HOST_ALIASES = {
    "aeldremin.dk": "baebm.dk",
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
    crawl_seconds: float = 0.0
    last_published_at: str = ""
    days_since_last_publication: int | None = None
    median_publication_gap_days: float | None = None
    silence_threshold_days: int | None = None
    silence_warning: bool = False
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
    netloc = parsed.netloc
    alias = CANONICAL_HOST_ALIASES.get(normalize_host(parsed.netloc))
    if alias:
        netloc = alias
    return urlunparse((parsed.scheme, netloc, path, "", parsed.query, ""))


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


def after_header_publication_date_from_soup(soup: BeautifulSoup) -> datetime | None:
    """Læs en ren datolinje umiddelbart EFTER artikelens H1.

    Enkelte officielle sider, bl.a. baebm.dk, viser datoen som en selvstændig
    linje lige efter overskriften. Fallbacken er opt-in pr. kilde og ser kun på
    de første fire ikke-tomme tekstnoder efter H1. Kun en tekstnode, der består
    fuldstændigt af en dato, accepteres. Dermed genindføres ingen scanning af
    artikelens almindelige brødtekst.
    """
    h1 = soup.find("h1")
    if h1 is None:
        return None
    checked = 0
    for text_node in h1.find_all_next(string=True):
        # Spring selve H1-teksten over, hvis parseren returnerer den i sekvensen.
        if h1 in getattr(text_node, "parents", []):
            continue
        value = clean_text(str(text_node))
        if not value:
            continue
        checked += 1
        parsed = exact_date_text(value)
        if parsed:
            return parsed
        if checked >= 4:
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
    3) kun for opt-in-kilder: en ren datolinje helt tæt på artikelens H1
       (før H1 eller, for særskilt godkendte layouts, umiddelbart efter H1)

    Der er bevidst ingen fallback til hele sidens tekst.
    """
    metadata_dates = metadata_publication_dates(soup)
    if metadata_dates:
        return metadata_dates[0]
    labeled = labeled_publication_date_from_soup(soup)
    if labeled:
        return labeled
    if source and source.get("allow_unlabeled_header_date"):
        header_date = header_publication_date_from_soup(soup)
        if header_date:
            return header_date
    if source and source.get("allow_unlabeled_after_h1_date"):
        after_header_date = after_header_publication_date_from_soup(soup)
        if after_header_date:
            return after_header_date
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

    if source and source.get("allow_unlabeled_after_h1_date"):
        h1 = soup.find("h1")
        if h1 is not None:
            checked = 0
            month_names = "|".join(DANISH_MONTHS)
            patterns = [
                rf"^\d{{1,2}}\.?\s+(?:{month_names}),?\s+20\d{{2}}(?:\s*[-–—]\s*(?:kl\.?\s*)?\d{{1,2}}[.:]\d{{2}})?$",
                r"^\d{1,2}[./-]\d{1,2}[./-]20\d{2}(?:\s*[-–—]\s*(?:kl\.?\s*)?\d{1,2}[.:]\d{2})?$",
                r"^20\d{2}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$",
            ]
            for text_node in h1.find_all_next(string=True):
                if h1 in getattr(text_node, "parents", []):
                    continue
                value = clean_text(str(text_node))
                if not value:
                    continue
                checked += 1
                if any(re.fullmatch(pattern, value, flags=re.IGNORECASE) for pattern in patterns):
                    raw_values.append(value)
                if checked >= 4:
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
        duration = time.monotonic() - started
        status.crawl_seconds = round(duration, 3)
        statuses.append(status)
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



@dataclass(frozen=True)
class DisplayEntry:
    primary: Item
    also: tuple[Item, ...] = ()


def duplicate_title_key(title: str) -> str:
    value = unicodedata.normalize("NFKC", clean_text(title)).casefold()
    value = re.sub(r"^(?:pressemeddelelse|nyhed|aktuelt)\s*[:\-–—]\s*", "", value)
    value = re.sub(r"[^0-9a-zæøå]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def duplicate_match(a: Item, b: Item) -> bool:
    """Sikker dubletkontrol, primært mellem Regeringen.dk og ministerierne."""
    if a.source != "Regeringen.dk" and b.source != "Regeringen.dk":
        return False
    if a.source == b.source:
        return False
    if abs((a.published - b.published).total_seconds()) > 2 * 86400:
        return False
    left, right = duplicate_title_key(a.title), duplicate_title_key(b.title)
    if not left or not right:
        return False
    if left == right:
        return True
    if min(len(left), len(right)) < 28:
        return False
    return difflib.SequenceMatcher(None, left, right).ratio() >= 0.94


def deduplicate_for_display(items: Iterable[Item]) -> list[DisplayEntry]:
    ministry_items = [item for item in items if item.source != "Regeringen.dk"]
    government_items = [item for item in items if item.source == "Regeringen.dk"]
    entries: list[DisplayEntry] = [DisplayEntry(item) for item in ministry_items]

    for government_item in government_items:
        best_index = None
        best_score = -1.0
        gov_key = duplicate_title_key(government_item.title)
        for index, entry in enumerate(entries):
            candidate = entry.primary
            if not duplicate_match(government_item, candidate):
                continue
            score = difflib.SequenceMatcher(None, gov_key, duplicate_title_key(candidate.title)).ratio()
            score -= min(abs((government_item.published - candidate.published).total_seconds()) / 86400, 2) * 0.01
            if score > best_score:
                best_score, best_index = score, index
        if best_index is None:
            entries.append(DisplayEntry(government_item))
        else:
            current = entries[best_index]
            entries[best_index] = DisplayEntry(current.primary, current.also + (government_item,))

    return sorted(entries, key=lambda entry: entry.primary.published, reverse=True)


def infer_article_type(item: Item, source: dict | None = None) -> str:
    path = urlparse(item.url).path.casefold()
    title = item.title.casefold()
    if "pressemeddelelse" in path or "pressemeddelelser" in path or title.startswith("pressemeddelelse"):
        return "Pressemeddelelse"
    if re.search(r"/(?:tale|taler)/", path) or title.startswith("tale:"):
        return "Tale"
    if "debatindlaeg" in path or "debatindlæg" in title:
        return "Debatindlæg"
    if "rapport" in path or title.startswith("rapport:"):
        return "Rapport"
    default_type = clean_text(str((source or {}).get("default_article_type", "")))
    if default_type:
        return default_type
    if "/nyhed" in path or "/aktuelt/" in path or item.source == "Regeringen.dk":
        return "Nyhed"
    return ""


def annotate_silence_warnings(statuses: list[SourceStatus], items: list[Item]) -> None:
    """Markér kilder, der er usædvanligt stille i forhold til deres egen rytme.

    Advarslen er bevidst konservativ: kilden skal have publiceret på mindst seks
    forskellige dage inden for de seneste 90 dage, og medianen mellem
    publiceringsdagene skal være højst syv dage. Tærsklen er mindst 10 dage og
    cirka tre gange den normale median, så almindeligt lavfrekvente kilder ikke
    fejlagtigt markeres som defekte.
    """
    now = datetime.now(timezone.utc)
    today = now.date()
    by_source: dict[str, list[Item]] = {}
    for item in items:
        by_source.setdefault(item.source, []).append(item)

    for status in statuses:
        source_items = by_source.get(status.name, [])
        if not source_items:
            continue
        latest = max(source_items, key=lambda item: item.published)
        status.last_published_at = latest.published.astimezone(timezone.utc).isoformat()
        status.days_since_last_publication = max(0, (today - latest.published.date()).days)

        publication_days = sorted(
            {item.published.date() for item in source_items if 0 <= (today - item.published.date()).days <= 90}
        )
        if len(publication_days) < 6:
            continue
        gaps = [
            (publication_days[index] - publication_days[index - 1]).days
            for index in range(1, len(publication_days))
            if (publication_days[index] - publication_days[index - 1]).days > 0
        ]
        if len(gaps) < 5:
            continue
        typical_gap = float(median(gaps))
        status.median_publication_gap_days = round(typical_gap, 1)
        if typical_gap > 7:
            continue
        threshold = max(10, min(30, int(round(typical_gap * 3))))
        status.silence_threshold_days = threshold
        status.silence_warning = bool(
            status.days_since_last_publication is not None
            and status.days_since_last_publication >= threshold
        )


def source_crawl_ok(status: SourceStatus) -> bool:
    return bool((status.methods or []) or status.listing_pages > 0 or status.sitemap_files > 0)


def source_health_ok(status: SourceStatus) -> bool:
    # "OK" er en teknisk kildestatus: kunne crawleren hente/aflæse kilden?
    # Publiceringsfrekvens må ikke gøre en ellers fungerende kilde "ikke OK".
    return source_crawl_ok(status)


def build_rss(entries: Iterable[DisplayEntry], site_url: str, feed_url: str, source_lookup: dict[str, dict]) -> bytes:
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "Ministerienyt – nyheder fra danske ministerier og Regeringen.dk"
    ET.SubElement(channel, "link").text = site_url
    ET.SubElement(channel, "description").text = (
        "Samlet arkiv med officielle nyheder og pressemeddelelser fra 1. januar 2026."
    )
    ET.SubElement(channel, "language").text = "da"
    ET.SubElement(channel, "lastBuildDate").text = email.utils.format_datetime(datetime.now(timezone.utc))
    ET.SubElement(channel, "generator").text = "Ministerienyt 5.4"
    if feed_url:
        atom = "http://www.w3.org/2005/Atom"
        ET.register_namespace("atom", atom)
        ET.SubElement(channel, f"{{{atom}}}link", {"href": feed_url, "rel": "self", "type": "application/rss+xml"})

    for entry in entries:
        item = entry.primary
        node = ET.SubElement(channel, "item")
        ET.SubElement(node, "title").text = f"{item.source}: {item.title}"
        ET.SubElement(node, "link").text = item.url
        guid = ET.SubElement(node, "guid", {"isPermaLink": "false"})
        guid.text = hashlib.sha256(canonical_url(item.url).encode("utf-8")).hexdigest()
        ET.SubElement(node, "pubDate").text = email.utils.format_datetime(item.published)
        ET.SubElement(node, "category").text = item.source
        article_type = infer_article_type(item, source_lookup.get(item.source))
        if article_type:
            ET.SubElement(node, "category").text = article_type
        source_node = ET.SubElement(node, "source", {"url": item.url})
        source_node.text = item.source
        description = item.description
        if entry.also:
            extras = ", ".join(other.source for other in entry.also)
            description = clean_text((description + " " if description else "") + f"Også publiceret på {extras}.")
        if description:
            ET.SubElement(node, "description").text = description

    ET.indent(rss, space="  ")
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)


def fmt_date_da(dt: datetime) -> str:
    return f"{dt.day}. {MONTH_NAMES[dt.month]} {dt.year}"


def fmt_datetime_da(dt: datetime) -> str:
    try:
        local = dt.astimezone(ZoneInfo("Europe/Copenhagen"))
    except Exception:
        local = dt.astimezone(timezone.utc)
    return f"{local.day}. {MONTH_NAMES[local.month]} {local.year} kl. {local:%H:%M}"


def esc(value: str) -> str:
    return html.escape(value or "", quote=True)



def build_html(
    entries: list[DisplayEntry],
    feed_url: str,
    sources: list[dict],
    statuses: list[SourceStatus],
    *,
    noindex: bool = False,
    goatcounter_code: str = "",
) -> str:
    ministries = sorted((source["name"] for source in sources), key=str.casefold)
    source_lookup = {source["name"]: source for source in sources}
    status_lookup = {status.name: status for status in statuses}
    raw_counts = Counter()
    for entry in entries:
        raw_counts[entry.primary.source] += 1
        for extra in entry.also:
            raw_counts[extra.source] += 1
    updated = datetime.now(timezone.utc)
    healthy_count = sum(1 for status in statuses if source_health_ok(status))
    health_class = "ok" if healthy_count == len(sources) else "warn"
    health_text = f"{healthy_count}/{len(sources)} kilder OK"

    entries = sorted(entries, key=lambda entry: entry.primary.published, reverse=True)
    cards: list[str] = []
    for entry in entries:
        item = entry.primary
        description = tidy_description_text(item.description, item.title)
        if len(description) > 280:
            description = description[:277].rstrip() + "..."
        article_id = hashlib.sha256(canonical_url(item.url).encode("utf-8")).hexdigest()
        article_type = infer_article_type(item, source_lookup.get(item.source))
        all_sources = [item.source, *(other.source for other in entry.also)]
        source_keys = "|".join(source.casefold() for source in all_sources)
        also_html = ""
        if entry.also:
            links = ", ".join(
                f'<a href="{esc(other.url)}" target="_blank" rel="noopener noreferrer">{esc(other.source)}</a>'
                for other in entry.also
            )
            also_html = f'<span class="also-published">Også publiceret på {links}</span>'
        type_html = f'<span class="type-badge">{esc(article_type)}</span>' if article_type else ""
        search_text = " ".join([*all_sources, item.title, description, article_type]).casefold()
        cards.append(
            f'''<article class="card" data-id="{article_id}" data-published="{esc(item.published.isoformat())}" data-sources="{esc(source_keys)}" data-search="{esc(search_text)}">
  <div class="meta"><span class="source-name">{esc(item.source)}</span>{type_html}<time datetime="{esc(item.published.isoformat())}">{esc(fmt_date_da(item.published))}</time><span class="new-badge">Ny siden sidst</span></div>
  <h2><a href="{esc(item.url)}" target="_blank" rel="noopener noreferrer">{esc(item.title)}</a></h2>
  {f'<p>{esc(description)}</p>' if description else ''}
  <div class="card-footer"><a class="more" href="{esc(item.url)}" target="_blank" rel="noopener noreferrer">Læs hos kilden &nearr;</a><button class="copy-link" type="button" data-copy-url="{esc(item.url)}" aria-label="Kopiér link til {esc(item.title)}">Kopiér link</button>{also_html}</div>
</article>'''
        )

    options = ['<option value="">Alle kilder</option>'] + [
        f'<option value="{esc(name.casefold())}">{esc(name)}</option>' for name in ministries
    ]
    favorite_checks = "".join(
        f'<label class="favorite-option"><input type="checkbox" value="{esc(name.casefold())}" data-label="{esc(name)}"> <span>{esc(name)}</span></label>'
        for name in ministries
    )

    source_rows: list[str] = []
    for name in ministries:
        source = source_lookup[name]
        status = status_lookup.get(name)
        methods = ", ".join(dict.fromkeys(status.methods or [])) if status else "Arkiv"
        if not methods:
            methods = "Arkiv"
        crawl_ok = source_crawl_ok(status) if status else False
        silence = bool(status and status.silence_warning)
        if not crawl_ok:
            state = '<span class="source-warn">Tjek</span>'
        else:
            state = '<span class="source-ok">OK</span>'
        notes: list[str] = []
        if raw_counts.get(name, 0) == 0:
            warning_text = (status.errors or ["Ingen artikler fundet fra kilden."])[0] if status else "Ingen artikler fundet fra kilden."
            notes.append(f'<span class="warning" title="{esc(warning_text)}">Ingen artikler</span>')
        note = (" " + " ".join(notes)) if notes else ""
        source_rows.append(
            f'''<tr><td><a href="{esc(source.get('home_url', source['start_urls'][0]))}" target="_blank" rel="noopener noreferrer">{esc(name)}</a>{note}</td><td>{raw_counts.get(name, 0)}</td><td>{state}</td><td>{esc(methods)}</td></tr>'''
        )

    feed_href = esc(feed_url or "feed.xml")
    robots_meta = '<meta name="robots" content="noindex,nofollow,noarchive">' if noindex else ''

    # GoatCounter-koden er et offentligt subdomænenavn, fx "ministerienyt" i
    # ministerienyt.goatcounter.com. Kun et sikkert DNS-label accepteres.
    goatcounter_code = clean_text(str(goatcounter_code or "")).strip().casefold()
    if goatcounter_code.endswith(".goatcounter.com"):
        goatcounter_code = goatcounter_code[:-len(".goatcounter.com")]
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", goatcounter_code):
        goatcounter_code = ""
    visit_counter_html = (
        '<span class="footer-sep" aria-hidden="true">·</span>'
        '<span class="visit-counter" id="visit-counter-wrap">Unikke besøg seneste 30 dage: '
        '<span id="visit-counter" aria-live="polite">–</span></span>'
        if goatcounter_code else ""
    )
    goatcounter_html = ""
    if goatcounter_code:
        counter_host = f"https://{goatcounter_code}.goatcounter.com"
        goatcounter_html = f'''<script data-goatcounter="{esc(counter_host)}/count" data-goatcounter-settings='{{"path":"/ministerienyt"}}' async src="https://gc.zgo.at/count.js"></script>
<script>
(() => {{
  const output = document.getElementById('visit-counter');
  if (!output) return;
  // Alle Ministerienyt-visninger spores som den samme faste sti. Dermed tæller
  // reloads, søgninger og filterskift ikke som nye besøg i samme GoatCounter-session.
  const startDate = new Date();
  startDate.setDate(startDate.getDate() - 30);
  const start = `${{startDate.getFullYear()}}-${{String(startDate.getMonth()+1).padStart(2,'0')}}-${{String(startDate.getDate()).padStart(2,'0')}}`;
  const counterPath = encodeURIComponent('/ministerienyt');
  fetch('{esc(counter_host)}/counter/' + counterPath + '.json?start=' + encodeURIComponent(start), {{mode: 'cors'}})
    .then(response => {{ if (!response.ok) throw new Error('counter'); return response.json(); }})
    .then(data => {{
      const raw = String(data && data.count != null ? data.count : '');
      const digits = raw.replace(/\\D/g, '');
      if (digits) output.textContent = Number(digits).toLocaleString('da-DK');
    }})
    .catch(() => {{ output.textContent = '–'; }});
}})();
</script>'''

    style = r'''
:root{--ink:#18222c;--muted:#5d6974;--line:#dce2e7;--bg:#f4f6f7;--paper:#fff;--brand:#7d1b2a;--brand2:#5f1420;--new:#fff7e6;--ok:#236c3b;--warn:#865900;--max:1120px}*{box-sizing:border-box}[hidden]{display:none!important}html{color-scheme:light;scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}a{color:inherit}button{font:inherit}a:focus-visible,input:focus-visible,select:focus-visible,button:focus-visible,summary:focus-visible{outline:3px solid #0867c8;outline-offset:3px}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}.top{background:var(--brand2);color:#fff}.wrap{width:min(calc(100% - 32px),var(--max));margin:auto}.top .wrap{min-height:48px;display:flex;align-items:center;justify-content:space-between;gap:18px}.brand{font-weight:800;letter-spacing:.01em}.top-actions{display:flex;align-items:center;gap:12px}.rss{color:#fff;text-decoration:none}.rss:hover{text-decoration:underline}.install-app{border:1px solid rgba(255,255,255,.55);background:transparent;color:#fff;border-radius:7px;padding:5px 9px;font-size:.78rem;font-weight:750;cursor:pointer}.hero{background:var(--paper);border-bottom:1px solid var(--line)}.hero .wrap{padding:14px 0 12px}h1{font-size:clamp(1.85rem,4vw,2.8rem);line-height:1.04;letter-spacing:-.035em;margin:0;max-width:900px}.run-status{display:flex;flex-wrap:wrap;gap:6px 12px;align-items:center;margin-top:8px;color:var(--muted);font-size:.82rem}.health-link{border:0;background:none;padding:0;color:var(--ok);font:800 inherit;cursor:pointer;text-decoration:none}.health-link.warn{color:var(--warn)}.controls{display:grid;grid-template-columns:minmax(0,1.7fr) minmax(220px,.9fr) auto;gap:9px;margin-top:13px;align-items:end}input,select{width:100%;min-height:42px;border:1px solid #aeb8c2;border-radius:7px;background:#fff;color:var(--ink);padding:7px 10px;font:inherit}.quick-actions{display:flex;gap:7px;align-items:center;flex-wrap:wrap}.filter-button,.favorites-menu>summary,.period-button{min-height:42px;border:1px solid #aeb8c2;border-radius:7px;background:#fff;color:var(--ink);padding:8px 11px;font:700 .88rem/1.1 system-ui;cursor:pointer;display:inline-flex;align-items:center}.filter-button[aria-pressed="true"],.period-button[aria-pressed="true"]{background:var(--brand2);color:#fff;border-color:var(--brand2)}.period-row{display:flex;align-items:center;gap:7px;margin-top:9px;flex-wrap:wrap;color:var(--muted);font-size:.82rem}.period-row .period-button{min-height:34px;padding:5px 9px;font-size:.8rem}.favorites-menu{position:relative}.favorites-menu>summary{list-style:none}.favorites-menu>summary::-webkit-details-marker{display:none}.favorites-panel{position:absolute;z-index:20;right:0;top:48px;width:min(520px,calc(100vw - 32px));background:#fff;border:1px solid var(--line);border-radius:9px;box-shadow:0 10px 30px rgba(0,0,0,.14);padding:13px}.favorites-grid{display:grid;grid-template-columns:1fr 1fr;gap:6px 16px;max-height:300px;overflow:auto}.favorite-option{display:flex;gap:7px;align-items:flex-start;font-size:.88rem}.favorite-option input{width:auto;min-height:auto;margin-top:3px}.favorites-footer{display:flex;justify-content:space-between;gap:12px;align-items:center;margin-top:10px;padding-top:9px;border-top:1px solid var(--line);color:var(--muted);font-size:.82rem}.text-button{border:0;background:none;color:var(--brand2);font-weight:750;cursor:pointer;padding:3px}main.wrap{padding:20px 0 46px}.head{display:flex;justify-content:space-between;align-items:end;gap:20px;margin-bottom:10px}.head h2{font-size:1.14rem;margin:0}#count{margin:0;color:var(--muted)}.head-left{display:grid;gap:3px}.new-summary{border:0;background:none;padding:0;color:var(--brand2);font:750 .86rem/1.35 system-ui;cursor:pointer;text-align:left;text-decoration:underline;text-decoration-style:dotted;text-underline-offset:3px}.new-summary:disabled{color:var(--muted);cursor:default;text-decoration:none}.list{display:grid;gap:9px}.card{background:var(--paper);border:1px solid var(--line);border-radius:9px;padding:16px 18px}.card.is-new{border-left:5px solid var(--brand);padding-left:14px;background:var(--new);box-shadow:0 2px 8px rgba(95,20,32,.07)}.card:hover{border-color:#c3cbd2}.meta{display:flex;gap:5px 10px;flex-wrap:wrap;align-items:center;color:var(--muted);font-size:.81rem}.source-name{color:var(--brand2);font-weight:800}.type-badge{display:inline-flex;background:#edf0f2;color:#46525d;border-radius:999px;padding:1px 7px;font-size:.7rem;font-weight:800}.new-badge{display:none;background:#f0d9dd;color:var(--brand2);border-radius:999px;padding:2px 7px;font-size:.7rem;line-height:1.4;text-transform:uppercase;letter-spacing:.04em;font-weight:850}.card.is-new .new-badge{display:inline-flex}.card h2{font-size:clamp(1.07rem,2.3vw,1.36rem);line-height:1.23;letter-spacing:-.01em;margin:5px 0 7px;font-weight:500}.card.is-new h2{font-weight:800}.card h2 a{text-decoration:none;font-weight:inherit}.card h2 a:hover{text-decoration:underline;text-decoration-thickness:2px;text-underline-offset:3px}.card p{color:#414d57;margin:0 0 8px;max-width:900px;line-height:1.4}.card-footer{display:flex;gap:8px 16px;align-items:center;flex-wrap:wrap}.more{display:inline-flex;color:var(--brand2);font-size:.84rem;font-weight:800;text-decoration:none}.more:hover{text-decoration:underline}.copy-link{border:0;background:none;padding:0;color:var(--muted);font-size:.8rem;font-weight:700;cursor:pointer;text-decoration:underline;text-decoration-style:dotted;text-underline-offset:3px}.copy-link.copied{color:var(--ok);text-decoration:none}.also-published{font-size:.78rem;color:var(--muted)}.also-published a{font-weight:750}.load-more{display:block;margin:18px auto 0;border:1px solid #aeb8c2;background:#fff;border-radius:8px;padding:9px 15px;font-weight:800;color:var(--ink);cursor:pointer}.empty{display:none;background:#fff;border:1px dashed #b8c1c8;border-radius:9px;padding:26px;text-align:center;color:var(--muted)}.sources{margin-top:24px;border-top:1px solid var(--line);padding-top:14px}.sources>summary{font-weight:800;cursor:pointer}.source-count{color:var(--muted);font-weight:500}.sources-content{padding-top:8px;color:var(--muted);font-size:.88rem}.sources-content p{margin:0 0 10px}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;background:#fff;color:var(--ink);font-size:.82rem}th,td{text-align:left;border-bottom:1px solid var(--line);padding:8px 10px;vertical-align:top}th{background:#f0f3f5}.warning{display:inline-block;margin-left:6px;padding:1px 6px;border-radius:999px;background:#fff0cf;color:#784e00;font-size:.7rem;font-weight:800}.source-ok{color:var(--ok);font-weight:800}.source-warn{color:var(--warn);font-weight:800}footer{background:#fff;border-top:1px solid var(--line)}footer .wrap{padding:12px 0 16px;color:var(--muted);font-size:.82rem}footer p{margin:2px 0}.footer-meta{display:flex;align-items:center;gap:5px;flex-wrap:wrap}.footer-sep{color:#a2abb3}.visit-counter{white-space:nowrap}.changelog{display:inline-block;position:relative;margin-left:3px}.changelog>summary{display:inline;cursor:pointer;font-size:.76rem;color:#7a858f;list-style:none;text-decoration:underline;text-decoration-style:dotted;text-underline-offset:3px}.changelog>summary::-webkit-details-marker{display:none}.changelog-panel{margin-top:9px;border:1px solid var(--line);background:#f8f9fa;border-radius:8px;padding:11px 13px;max-width:720px;color:var(--ink);font-size:.8rem}.changelog-panel h3{margin:0 0 7px;font-size:.9rem}.changelog-panel ul{margin:5px 0 8px;padding-left:20px}.changelog-panel li{margin:2px 0}.mobile-dock{display:none}
@media(max-width:900px){.controls{grid-template-columns:1fr 1fr}.quick-actions{grid-column:1/-1}}
@media(max-width:650px){body{padding-bottom:68px}.wrap{width:min(calc(100% - 22px),var(--max))}.top .wrap{min-height:44px}.top-actions{gap:8px}.rss{font-size:.84rem}.install-app{font-size:.72rem;padding:4px 7px}.controls{grid-template-columns:1fr}.quick-actions{grid-column:auto;gap:6px}.quick-actions .filter-button,.quick-actions .favorites-menu>summary{min-height:38px;padding:7px 9px;font-size:.8rem}.period-row{gap:5px}.period-row .period-button{flex:1;justify-content:center}.hero .wrap{padding:12px 0 11px}.card{padding:13px 14px;border-radius:8px}.card.is-new{padding-left:10px}.card h2{font-size:1.08rem;margin-top:4px}.card p{font-size:.91rem;line-height:1.35}.meta{font-size:.75rem}.card-footer{gap:7px 13px}.head{align-items:start;flex-direction:column;gap:3px}.favorites-grid{grid-template-columns:1fr}.favorites-panel{position:fixed;left:10px;right:10px;bottom:64px;top:auto;width:auto;max-height:70vh;overflow:auto}.mobile-dock{position:fixed;display:grid;grid-template-columns:repeat(4,1fr);left:0;right:0;bottom:0;z-index:50;background:rgba(255,255,255,.97);border-top:1px solid var(--line);padding:max(6px,env(safe-area-inset-bottom)) 8px 7px;box-shadow:0 -3px 14px rgba(0,0,0,.08)}.mobile-dock button{border:0;background:none;color:#4c5964;padding:5px 3px;font-size:.74rem;font-weight:750;cursor:pointer}.mobile-dock button.active{color:var(--brand2);font-weight:900}.mobile-dock button span{display:block;font-size:1rem;line-height:1.05;margin-bottom:1px}.table-wrap{margin-inline:-4px}th,td{padding:7px 8px}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important}}
'''
    script = r'''
(() => {
  const search = document.getElementById('search');
  const sourceSelect = document.getElementById('source');
  const newOnly = document.getElementById('new-only');
  const mineOnly = document.getElementById('mine-only');
  const favoritesMenu = document.getElementById('favorites-menu');
  const favoriteBoxes = [...favoritesMenu.querySelectorAll('input[type="checkbox"]')];
  const favoritesCount = document.getElementById('favorites-count');
  const clearFavorites = document.getElementById('clear-favorites');
  const periodButtons = [...document.querySelectorAll('.period-button')];
  const loadMore = document.getElementById('load-more');
  const cards = [...document.querySelectorAll('.card')];
  const count = document.getElementById('count');
  const empty = document.getElementById('empty');
  const newSummary = document.getElementById('new-summary');
  const healthLink = document.getElementById('health-link');
  const sourcesDetails = document.getElementById('sources');
  const installApp = document.getElementById('install-app');
  const mobileSearch = document.getElementById('mobile-search');
  const mobileNew = document.getElementById('mobile-new');
  const mobileMine = document.getElementById('mobile-mine');
  const mobileFavorites = document.getElementById('mobile-favorites');
  const SEEN_KEY = 'ministerienyt.seenArticleIds.v2';
  const VISIT_KEY = 'ministerienyt.lastVisit.v2';
  const FAVORITES_KEY = 'ministerienyt.favoriteSources.v1';
  const PAGE_SIZE = 15;
  const norm = value => (value || '').toLocaleLowerCase('da-DK').trim();
  let previousIds = null;
  let lastVisit = null;
  let visibleLimit = PAGE_SIZE;
  let favorites = new Set();
  let periodDays = '';

  try {
    const raw = localStorage.getItem(SEEN_KEY);
    if (raw) previousIds = new Set(JSON.parse(raw));
    lastVisit = localStorage.getItem(VISIT_KEY);
    const favRaw = localStorage.getItem(FAVORITES_KEY);
    if (favRaw) favorites = new Set(JSON.parse(favRaw));
  } catch (error) {
    previousIds = null;
    lastVisit = null;
    favorites = new Set();
  }

  const currentIds = cards.map(card => card.dataset.id).filter(Boolean);
  let newCount = 0;
  if (previousIds) {
    for (const card of cards) {
      if (card.dataset.id && !previousIds.has(card.dataset.id)) {
        card.classList.add('is-new');
        newCount++;
      }
    }
  }

  const list = document.getElementById('list');
  cards.sort((a, b) => {
    const newDifference = Number(b.classList.contains('is-new')) - Number(a.classList.contains('is-new'));
    if (newDifference !== 0) return newDifference;
    const aTime = Date.parse(a.dataset.published || '') || 0;
    const bTime = Date.parse(b.dataset.published || '') || 0;
    return bTime - aTime;
  });
  for (const card of cards) list.appendChild(card);

  function visitText(value) {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '';
    return new Intl.DateTimeFormat('da-DK', {dateStyle: 'medium', timeStyle: 'short'}).format(date);
  }

  if (!previousIds) {
    newSummary.textContent = 'Nye artikler markeres fra dit næste besøg.';
  } else if (newCount === 0) {
    newSummary.textContent = 'Ingen nye artikler siden dit sidste besøg.';
  } else {
    const when = visitText(lastVisit);
    newSummary.disabled = false;
    newSummary.textContent = (newCount === 1 ? '1 ny artikel' : newCount + ' nye artikler') +
      ' siden dit sidste besøg' + (when ? ' (' + when + ')' : '') + ' – vis dem';
  }

  try {
    const merged = [...(previousIds ? [...previousIds] : []), ...currentIds];
    const unique = [...new Set(merged)].slice(-10000);
    localStorage.setItem(SEEN_KEY, JSON.stringify(unique));
    localStorage.setItem(VISIT_KEY, new Date().toISOString());
  } catch (error) {}

  function saveFavorites() {
    try { localStorage.setItem(FAVORITES_KEY, JSON.stringify([...favorites])); } catch (error) {}
  }

  function syncFavoritesUI() {
    for (const box of favoriteBoxes) box.checked = favorites.has(box.value);
    favoritesCount.textContent = favorites.size === 1 ? '1 valgt' : favorites.size + ' valgt';
    mineOnly.textContent = favorites.size ? 'Mine ministerier (' + favorites.size + ')' : 'Mine ministerier';
  }

  function syncPeriods() {
    for (const button of periodButtons) {
      button.setAttribute('aria-pressed', button.dataset.days === periodDays ? 'true' : 'false');
    }
  }

  function syncMobile() {
    if (mobileNew) {
      mobileNew.classList.toggle('active', newOnly.getAttribute('aria-pressed') === 'true');
      mobileNew.querySelector('span').textContent = newCount ? 'Nye ' + newCount : 'Nye';
    }
    if (mobileMine) mobileMine.classList.toggle('active', mineOnly.getAttribute('aria-pressed') === 'true');
  }

  function cardSources(card) {
    return (card.dataset.sources || '').split('|').filter(Boolean);
  }

  function applyFilters(resetLimit = false) {
    if (resetLimit) visibleLimit = PAGE_SIZE;
    const query = norm(search.value);
    const selected = norm(sourceSelect.value);
    const onlyNew = newOnly.getAttribute('aria-pressed') === 'true';
    const onlyMine = mineOnly.getAttribute('aria-pressed') === 'true';
    const cutoff = periodDays ? Date.now() - Number(periodDays) * 86400000 : 0;
    const matching = [];
    for (const card of cards) {
      const sources = cardSources(card);
      const matchSource = !selected || sources.includes(selected);
      const matchMine = !onlyMine || sources.some(source => favorites.has(source));
      const published = Date.parse(card.dataset.published || '') || 0;
      const matchPeriod = !cutoff || published >= cutoff;
      const match = (!query || card.dataset.search.includes(query)) && matchSource && matchMine &&
        matchPeriod && (!onlyNew || card.classList.contains('is-new'));
      if (match) matching.push(card);
      else card.hidden = true;
    }
    matching.forEach((card, index) => { card.hidden = index >= visibleLimit; });
    const shown = Math.min(visibleLimit, matching.length);
    const remaining = Math.max(0, matching.length - shown);
    if (matching.length === 0) count.textContent = '0 nyheder';
    else if (remaining > 0) count.textContent = shown + ' af ' + matching.length + ' nyheder';
    else count.textContent = matching.length === 1 ? '1 nyhed' : matching.length + ' nyheder';
    empty.style.display = matching.length ? 'none' : 'block';
    loadMore.hidden = remaining === 0;
    if (remaining > 0) {
      const next = Math.min(PAGE_SIZE, remaining);
      loadMore.textContent = remaining <= PAGE_SIZE ? 'Vis de sidste ' + remaining + ' nyheder' : 'Vis ' + next + ' flere nyheder';
    }
    const url = new URL(location);
    query ? url.searchParams.set('q', search.value.trim()) : url.searchParams.delete('q');
    selected ? url.searchParams.set('kilde', sourceSelect.value) : url.searchParams.delete('kilde');
    onlyNew ? url.searchParams.set('nye', '1') : url.searchParams.delete('nye');
    onlyMine ? url.searchParams.set('mine', '1') : url.searchParams.delete('mine');
    periodDays ? url.searchParams.set('periode', periodDays) : url.searchParams.delete('periode');
    history.replaceState(null, '', url);
    syncPeriods();
    syncMobile();
  }

  function toggleNew() {
    newOnly.setAttribute('aria-pressed', newOnly.getAttribute('aria-pressed') === 'true' ? 'false' : 'true');
    applyFilters(true);
  }

  function toggleMine() {
    if (!favorites.size) {
      favoritesMenu.open = true;
      favoritesMenu.scrollIntoView({behavior: 'smooth', block: 'center'});
      return;
    }
    mineOnly.setAttribute('aria-pressed', mineOnly.getAttribute('aria-pressed') === 'true' ? 'false' : 'true');
    if (mineOnly.getAttribute('aria-pressed') === 'true') sourceSelect.value = '';
    applyFilters(true);
  }

  const params = new URLSearchParams(location.search);
  if (params.get('q')) search.value = params.get('q');
  if (params.get('kilde')) sourceSelect.value = params.get('kilde');
  if (params.get('nye') === '1') newOnly.setAttribute('aria-pressed', 'true');
  if (params.get('mine') === '1') mineOnly.setAttribute('aria-pressed', 'true');
  if (['7', '30'].includes(params.get('periode'))) periodDays = params.get('periode');
  syncFavoritesUI();
  syncPeriods();

  search.addEventListener('input', () => applyFilters(true));
  sourceSelect.addEventListener('change', () => {
    mineOnly.setAttribute('aria-pressed', 'false');
    applyFilters(true);
  });
  newOnly.addEventListener('click', toggleNew);
  mineOnly.addEventListener('click', toggleMine);
  periodButtons.forEach(button => button.addEventListener('click', () => {
    periodDays = button.dataset.days || '';
    applyFilters(true);
  }));
  favoriteBoxes.forEach(box => box.addEventListener('change', () => {
    box.checked ? favorites.add(box.value) : favorites.delete(box.value);
    saveFavorites();
    syncFavoritesUI();
    if (mineOnly.getAttribute('aria-pressed') === 'true' && !favorites.size) mineOnly.setAttribute('aria-pressed', 'false');
    applyFilters(true);
  }));
  clearFavorites.addEventListener('click', () => {
    favorites.clear();
    saveFavorites();
    syncFavoritesUI();
    mineOnly.setAttribute('aria-pressed', 'false');
    applyFilters(true);
  });
  newSummary.addEventListener('click', () => {
    if (newCount > 0) {
      newOnly.setAttribute('aria-pressed', 'true');
      applyFilters(true);
      window.scrollTo({top: document.querySelector('main').offsetTop - 10, behavior: 'smooth'});
    }
  });
  healthLink.addEventListener('click', () => {
    sourcesDetails.open = true;
    sourcesDetails.scrollIntoView({behavior: 'smooth', block: 'start'});
  });
  loadMore.addEventListener('click', () => {
    visibleLimit += PAGE_SIZE;
    applyFilters(false);
  });

  async function copyText(value) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(value);
      return true;
    }
    const area = document.createElement('textarea');
    area.value = value;
    area.setAttribute('readonly', '');
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch (error) {}
    area.remove();
    return ok;
  }

  document.querySelectorAll('.copy-link').forEach(button => button.addEventListener('click', async () => {
    const original = 'Kopiér link';
    try {
      const ok = await copyText(button.dataset.copyUrl || '');
      button.textContent = ok ? 'Kopieret ✓' : original;
      button.classList.toggle('copied', ok);
      if (ok) setTimeout(() => { button.textContent = original; button.classList.remove('copied'); }, 1800);
    } catch (error) {
      button.textContent = original;
    }
  }));

  if (mobileSearch) mobileSearch.addEventListener('click', () => {
    document.querySelector('.hero').scrollIntoView({behavior: 'smooth', block: 'start'});
    setTimeout(() => search.focus({preventScroll: true}), 350);
  });
  if (mobileNew) mobileNew.addEventListener('click', toggleNew);
  if (mobileMine) mobileMine.addEventListener('click', toggleMine);
  if (mobileFavorites) mobileFavorites.addEventListener('click', () => {
    favoritesMenu.open = true;
    document.querySelector('.hero').scrollIntoView({behavior: 'smooth', block: 'start'});
  });

  let deferredInstallPrompt = null;
  const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent);
  const isStandalone = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
  window.addEventListener('beforeinstallprompt', event => {
    event.preventDefault();
    deferredInstallPrompt = event;
    if (installApp && !isStandalone) installApp.hidden = false;
  });
  if (installApp && isIOS && !isStandalone) installApp.hidden = false;
  if (installApp) installApp.addEventListener('click', async () => {
    if (deferredInstallPrompt) {
      deferredInstallPrompt.prompt();
      try { await deferredInstallPrompt.userChoice; } catch (error) {}
      deferredInstallPrompt = null;
      installApp.hidden = true;
    } else if (isIOS) {
      alert('På iPhone/iPad: tryk Del og vælg “Føj til hjemmeskærm”.');
    }
  });
  window.addEventListener('appinstalled', () => { if (installApp) installApp.hidden = true; });
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => navigator.serviceWorker.register('service-worker.js').catch(() => {}));
  }

  applyFilters(true);
})();
'''
    changelog_html = '''<details class="changelog"><summary>v5.4</summary><div class="changelog-panel"><h3>Ændringslog</h3><strong>v5.4</strong><ul><li>Diskret tæller for unikke besøg på hele Ministerienyt de seneste 30 dage via valgfri GoatCounter-integration.</li><li>Footer komprimeret: RSS-feed, version og besøgstal samles på samme linje.</li><li>RSS-linket fjernet fra topbjælken, så det kun vises ét sted.</li><li>Den ekstra introduktionslinje under overskriften er fjernet for en lavere top.</li></ul><strong>v5.3</strong><ul><li>BAEBM-kilden gjort robust over for domæneskiftet mellem aeldremin.dk og baebm.dk.</li><li>BAEBM accepterer nu den officielle rene datolinje umiddelbart efter artikeloverskriften.</li><li>Kildestatus måler nu kun teknisk crawl-status; perioder uden nye artikler reducerer ikke antallet af kilder OK.</li></ul><strong>v5.2</strong><ul><li>Alle 21 aktive ministerielle nyhedskilder gennemgået pr. 24. august 2026.</li><li>Børne-, Ældre- og Boligministeriets aktive domæne opdateret til baebm.dk.</li><li>Ekstra officielle RSS- og årsarkiver tilføjet, hvor de giver mere robust dækning.</li></ul><strong>v5.1</strong><ul><li>Advarsel ved usædvanlig stilhed fra normalt aktive kilder.</li><li>Kopiér-link på hver artikel.</li><li>Filtre for alle, 7 dage og 30 dage.</li><li>Installerbar webapp (PWA) og forbedret mobilbetjening.</li><li>Intern diagnostics.json med kvalitetsmålinger.</li></ul><strong>v5.0</strong><ul><li>Kildestatus, dubletkontrol, artikeltyper, favoritter og delbare filtre.</li></ul><strong>v4.7</strong><ul><li>Nye siden sidst sorteres øverst.</li></ul><strong>v4.6</strong><ul><li>Skjult log over afviste kandidater.</li></ul><strong>v4.5</strong><ul><li>Sikker datohåndtering for bl.a. Kulturministeriet og Skatte- og Vækstministeriet.</li></ul></div></details>'''
    return f'''<!doctype html>
<html lang="da"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">{robots_meta}<meta name="description" content="Samlet arkiv over officielle nyheder fra danske ministerier og Regeringen.dk siden 1. januar 2026."><meta name="theme-color" content="#5f1420"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="default"><title>Ministerienyt</title><link rel="alternate" type="application/rss+xml" title="Ministerienyt RSS" href="{feed_href}"><link rel="manifest" href="manifest.webmanifest"><link rel="apple-touch-icon" href="icon-192.png">
<style>{style}</style></head><body>
<div class="top"><div class="wrap"><div class="brand">Ministerienyt</div><div class="top-actions"><button id="install-app" class="install-app" type="button" hidden>Installér app</button></div></div></div>
<header class="hero"><div class="wrap"><h1>Nyheder fra danske ministerier</h1><div class="run-status"><span>Senest opdateret {esc(fmt_datetime_da(updated))}</span><button id="health-link" class="health-link {health_class}" type="button">{esc(health_text)}</button></div><div class="controls" role="search"><div class="search-field"><label class="sr-only" for="search">Søg i nyheder</label><input id="search" type="search" placeholder="Søg fx klima, økonomi eller sundhed" aria-label="Søg i nyheder" autocomplete="off"></div><div><label class="sr-only" for="source">Kilde</label><select id="source">{''.join(options)}</select></div><div class="quick-actions"><button id="new-only" class="filter-button" type="button" aria-pressed="false">Kun nye</button><button id="mine-only" class="filter-button" type="button" aria-pressed="false">Mine ministerier</button><details id="favorites-menu" class="favorites-menu"><summary>★ Favoritter</summary><div class="favorites-panel"><div class="favorites-grid">{favorite_checks}</div><div class="favorites-footer"><span id="favorites-count">0 valgt</span><button id="clear-favorites" class="text-button" type="button">Ryd favoritter</button></div></div></details></div></div><div class="period-row" role="group" aria-label="Tidsperiode"><span>Periode:</span><button class="period-button" type="button" data-days="" aria-pressed="true">Alle</button><button class="period-button" type="button" data-days="7" aria-pressed="false">7 dage</button><button class="period-button" type="button" data-days="30" aria-pressed="false">30 dage</button></div></div></header>
<main class="wrap"><div class="head"><div class="head-left"><h2>Nyhedsarkiv</h2><button id="new-summary" class="new-summary" type="button" disabled aria-live="polite"></button></div><p id="count">{len(entries)} nyheder</p></div><section class="list" id="list">{''.join(cards)}</section><button id="load-more" class="load-more" type="button" hidden>Vis flere nyheder</button><div class="empty" id="empty">Ingen nyheder matcher dit filter.</div><details class="sources" id="sources"><summary>Kilder og dækning <span class="source-count">({len(ministries)} kilder)</span></summary><div class="sources-content"><p>Artikler med samme historie hos et ministerium og Regeringen.dk samles i ét kort. “OK” betyder, at crawleren teknisk kunne hente kilden. Hvor længe der er gået siden seneste nyhed påvirker ikke kildestatus; aktivitetsmønstre gemmes kun i den interne diagnostics.json.</p><div class="table-wrap"><table><thead><tr><th>Kilde</th><th>Artikler</th><th>Status</th><th>Fundet via</th></tr></thead><tbody>{''.join(source_rows)}</tbody></table></div></div></details></main>
<footer><div class="wrap"><p><strong>Ministerienyt</strong> er en uafhængig samling af links til officielle kilder.</p><p class="footer-meta"><span>Alle artikler åbner hos den oprindelige udgiver.</span><span class="footer-sep" aria-hidden="true">·</span><a href="{feed_href}">RSS-feed</a><span class="footer-sep" aria-hidden="true">·</span>{changelog_html}{visit_counter_html}</p></div></footer>
<nav class="mobile-dock" aria-label="Hurtige handlinger"><button id="mobile-search" type="button"><span>⌕</span>Søg</button><button id="mobile-new" type="button"><span>Nye</span>Kun nye</button><button id="mobile-mine" type="button"><span>★</span>Mine</button><button id="mobile-favorites" type="button"><span>☆</span>Favoritter</button></nav>
<script>{script}</script>
{goatcounter_html}
</body></html>'''


def load_previous_health(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload.get("sources", []) if isinstance(payload, dict) else []
        return {str(row.get("name", "")): row for row in rows if isinstance(row, dict) and row.get("name")}
    except Exception:
        return {}


def health_payload(statuses: list[SourceStatus], items: list[Item], previous: dict[str, dict], display_count: int) -> dict:
    counts = Counter(item.source for item in items)
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for status in statuses:
        status.archived_items = counts.get(status.name, 0)
        raw = asdict(status)
        raw["methods"] = list(dict.fromkeys(raw.get("methods") or []))
        crawl_ok = source_crawl_ok(status)
        ok = source_health_ok(status)
        previous_row = previous.get(status.name, {})
        raw["crawl_ok"] = crawl_ok
        raw["ok"] = ok
        raw["last_checked_at"] = now
        raw["last_successful_at"] = now if crawl_ok else previous_row.get("last_successful_at")
        rows.append(raw)
    return {
        "version": "5.4",
        "updated_at": now,
        "archive_start": ARCHIVE_START.date().isoformat(),
        "total_archive_items": len(items),
        "display_items_after_deduplication": display_count,
        "source_count": len(statuses),
        "healthy_sources": sum(1 for row in rows if row.get("ok")),
        "unusually_silent_sources": sum(1 for row in rows if row.get("silence_warning")),
        "sources": rows,
    }


def diagnostics_payload(
    statuses: list[SourceStatus],
    items: list[Item],
    display_entries: list[DisplayEntry],
    duplicates_merged: int,
    elapsed_seconds: float,
) -> dict:
    rejected = list(REJECTED_CANDIDATES.values())
    rejected_by_source: dict[str, Counter] = {}
    for entry in rejected:
        rejected_by_source.setdefault(str(entry.get("source", "")), Counter())[str(entry.get("reason", "unknown"))] += 1
    items_by_source: dict[str, list[Item]] = {}
    for item in items:
        items_by_source.setdefault(item.source, []).append(item)

    rows = []
    for status in statuses:
        source_items = sorted(items_by_source.get(status.name, []), key=lambda item: item.published, reverse=True)
        reasons = rejected_by_source.get(status.name, Counter())
        latest = source_items[0] if source_items else None
        rows.append({
            "name": status.name,
            "crawl_ok": source_crawl_ok(status),
            "health_ok": source_health_ok(status),
            "unusual_silence": status.silence_warning,
            "days_since_last_publication": status.days_since_last_publication,
            "median_publication_gap_days": status.median_publication_gap_days,
            "silence_threshold_days": status.silence_threshold_days,
            "crawl_seconds": status.crawl_seconds,
            "listing_pages": status.listing_pages,
            "sitemap_files": status.sitemap_files,
            "article_candidates": status.article_candidates,
            "article_fetches": status.article_fetches,
            "accepted_from_current_crawl": status.fresh_items,
            "rejected_during_current_crawl": sum(reasons.values()),
            "rejection_reasons": dict(sorted(reasons.items())),
            "archive_items": len(source_items),
            "methods": list(dict.fromkeys(status.methods or [])),
            "errors": status.errors or [],
            "latest_article": ({
                "title": latest.title,
                "url": latest.url,
                "published": latest.published.astimezone(timezone.utc).isoformat(),
            } if latest else None),
        })

    all_reason_counts = Counter(str(entry.get("reason", "unknown")) for entry in rejected)
    return {
        "version": "5.4",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": "Intern kvalitetsrapport fra seneste crawl. Filen publiceres ikke via GitHub Pages.",
        "runtime_seconds": round(elapsed_seconds, 3),
        "archive_items": len(items),
        "display_items_after_deduplication": len(display_entries),
        "duplicates_merged": duplicates_merged,
        "article_candidates": sum(status.article_candidates for status in statuses),
        "accepted_from_current_crawl": sum(status.fresh_items for status in statuses),
        "rejected_during_current_crawl": len(rejected),
        "rejection_reasons": dict(sorted(all_reason_counts.items())),
        "source_count": len(statuses),
        "healthy_sources": sum(1 for status in statuses if source_health_ok(status)),
        "unusually_silent_sources": sum(1 for status in statuses if status.silence_warning),
        "sources": rows,
    }


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def _point_segment_distance(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if not length_sq:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    qx, qy = ax + t * dx, ay + t * dy
    return ((px - qx) ** 2 + (py - qy) ** 2) ** 0.5


def pwa_icon_png(size: int) -> bytes:
    background = (95, 20, 32, 255)
    foreground = (255, 255, 255, 255)
    segments = [
        (0.22, 0.76, 0.22, 0.24),
        (0.22, 0.24, 0.50, 0.56),
        (0.50, 0.56, 0.78, 0.24),
        (0.78, 0.24, 0.78, 0.76),
    ]
    stroke = 0.055
    raw = bytearray()
    for y in range(size):
        raw.append(0)
        ny = (y + 0.5) / size
        for x in range(size):
            nx = (x + 0.5) / size
            pixel = foreground if any(
                _point_segment_distance(nx, ny, *segment) <= stroke for segment in segments
            ) else background
            raw.extend(pixel)
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return signature + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + _png_chunk(b"IEND", b"")


def generate_pwa_assets(site_dir: Path) -> None:
    site_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": "Ministerienyt – danske ministerier",
        "short_name": "Ministerienyt",
        "description": "Samlet overblik over officielle nyheder fra danske ministerier og Regeringen.dk.",
        "id": "./",
        "start_url": "./",
        "scope": "./",
        "display": "standalone",
        "background_color": "#f4f6f7",
        "theme_color": "#5f1420",
        "icons": [
            {"src": "icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
    }
    (site_dir / "manifest.webmanifest").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (site_dir / "icon-192.png").write_bytes(pwa_icon_png(192))
    (site_dir / "icon-512.png").write_bytes(pwa_icon_png(512))
    service_worker = r'''const CACHE = 'ministerienyt-v5.4';
const SHELL = ['./', './manifest.webmanifest', './icon-192.png', './icon-512.png'];
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;
  if (event.request.mode === 'navigate') {
    event.respondWith(fetch(event.request).then(response => {
      const copy = response.clone();
      caches.open(CACHE).then(cache => cache.put('./', copy));
      return response;
    }).catch(() => caches.match('./')));
    return;
  }
  const url = new URL(event.request.url);
  if (url.origin === self.location.origin && /(?:manifest\.webmanifest|icon-192\.png|icon-512\.png)$/.test(url.pathname)) {
    event.respondWith(caches.match(event.request).then(cached => cached || fetch(event.request)));
  }
});
'''
    (site_dir / "service-worker.js").write_text(service_worker, encoding="utf-8")



def load_site_config(path: Path) -> dict:
    defaults = {"noindex": False, "goatcounter_code": ""}
    if not path.exists():
        return defaults
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            defaults.update(raw)
    except Exception as exc:
        print(f"Advarsel: kunne ikke læse {path}: {exc}", file=sys.stderr)
    return defaults


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", default="sources.json")
    parser.add_argument("--archive", default="archive.json")
    parser.add_argument("--rss-output", default="site/feed.xml")
    parser.add_argument("--html-output", default="site/index.html")
    parser.add_argument("--health-output", default="health.json")
    parser.add_argument("--diagnostics-output", default="diagnostics.json")
    parser.add_argument("--status-output", default="")  # bagudkompatibel kopi, hvis ønsket
    parser.add_argument("--rejected-log", default="rejected_candidates.json")
    parser.add_argument("--config", default="site_config.json")
    parser.add_argument("--site-url", default="https://example.invalid/")
    parser.add_argument("--feed-url", default="")
    args = parser.parse_args()

    started = time.monotonic()
    REJECTED_CANDIDATES.clear()
    sources_path = Path(args.sources)
    sources = json.loads(sources_path.read_text(encoding="utf-8"))
    source_lookup = {source["name"]: source for source in sources}
    allowed_sources = set(source_lookup)
    config = load_site_config(Path(args.config))
    archive_path = Path(args.archive)
    existing, archive_version = load_archive(archive_path, allowed_sources)
    refresh_sources = {
        source["name"] for source in sources
        if int(source.get("refresh_before_schema", 0) or 0) > archive_version
    }
    if refresh_sources:
        before = len(existing)
        existing = [item for item in existing if item.source not in refresh_sources]
        removed = before - len(existing)
        print("Genopbygger korrigerede kilder: " + ", ".join(sorted(refresh_sources)) + f" ({removed} gamle poster fjernet).", file=sys.stderr)
    known_urls = {canonical_url(item.url) for item in existing}
    print(f"Eksisterende arkiv: {len(existing)} artikler.", file=sys.stderr)

    fresh, statuses = collect_fresh_items(sources, known_urls)
    save_rejection_log(Path(args.rejected_log))
    print(f"Afvisningslog: {len(REJECTED_CANDIDATES)} kandidater.", file=sys.stderr)
    merged = merge_archive(existing, fresh)
    if not merged:
        print("Ingen artikler kunne findes, og arkivet er tomt. Output blev ikke overskrevet.", file=sys.stderr)
        return 2

    annotate_silence_warnings(statuses, merged)

    if not archive_path.exists() or archive_signature(existing) != archive_signature(merged):
        save_archive(archive_path, merged)
        print(f"Arkivet blev opdateret: {len(merged)} artikler.", file=sys.stderr)
    else:
        print("Arkivet er uændret.", file=sys.stderr)

    display_entries = deduplicate_for_display(merged)
    duplicates_merged = len(merged) - len(display_entries)
    print(f"Dubletkontrol: {duplicates_merged} Regeringen.dk/ministerie-dubletter samlet.", file=sys.stderr)

    rss_output = Path(args.rss_output)
    html_output = Path(args.html_output)
    health_output = Path(args.health_output)
    diagnostics_output = Path(args.diagnostics_output)
    for output in (rss_output, html_output, health_output, diagnostics_output):
        output.parent.mkdir(parents=True, exist_ok=True)

    previous_health = load_previous_health(health_output)
    health = health_payload(statuses, merged, previous_health, len(display_entries))
    health_output.write_text(json.dumps(health, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.status_output:
        status_output = Path(args.status_output)
        status_output.parent.mkdir(parents=True, exist_ok=True)
        status_output.write_text(json.dumps(health, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    rss_output.write_bytes(build_rss(display_entries, args.site_url, args.feed_url, source_lookup))
    html_output.write_text(
        build_html(display_entries, args.feed_url or "feed.xml", sources, statuses, noindex=bool(config.get("noindex")), goatcounter_code=str(config.get("goatcounter_code", ""))),
        encoding="utf-8",
    )
    robots = "User-agent: *\nDisallow: /\n" if config.get("noindex") else "User-agent: *\nAllow: /\n"
    (html_output.parent / "robots.txt").write_text(robots, encoding="utf-8")
    generate_pwa_assets(html_output.parent)

    elapsed = time.monotonic() - started
    diagnostics = diagnostics_payload(statuses, merged, display_entries, duplicates_merged, elapsed)
    diagnostics_output.write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    covered = sum(1 for source in sources if any(item.source == source["name"] for item in merged))
    silent = sum(1 for status in statuses if status.silence_warning)
    print(
        f"Færdig: {len(merged)} arkivposter / {len(display_entries)} viste historier fra "
        f"{covered}/{len(sources)} kilder. Usædvanligt stille: {silent}. Kørselstid: {elapsed / 60:.1f} min.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
