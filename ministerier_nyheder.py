#!/usr/bin/env python3
"""
Samler nyheder og pressemeddelelser fra danske ministeriers officielle
hjemmesider i ét RSS 2.0-feed.

Strategi:
1) Brug et eksplicit RSS-feed, hvis kilden har et.
2) Prøv automatisk ?rss=true på kildens oversigtsside.
3) Fald tilbage til skånsom HTML-udtrækning fra den officielle nyhedsside.

Kildelisten ligger i sources.json.
"""
from __future__ import annotations

import argparse
import email.utils
import hashlib
import html
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse, urlunparse

import feedparser
import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser
from xml.etree import ElementTree as ET

USER_AGENT = "DanskeMinisterierRSS/1.0 (+public RSS aggregator; respectful crawler)"
TIMEOUT = 25
MAX_ITEMS_PER_MINISTRY = 12
MAX_ARTICLE_FETCHES_PER_START_URL = 5
LOOKBACK_DAYS = 180
REQUEST_DELAY_SECONDS = 0.15

DANISH_MONTHS = {
    "januar": 1, "februar": 2, "marts": 3, "april": 4, "maj": 5, "juni": 6,
    "juli": 7, "august": 8, "september": 9, "oktober": 10,
    "november": 11, "december": 12,
}

SKIP_SEGMENTS = {
    "presse", "kontakt", "abonner", "abonnement", "tilmeld", "nyhedsbrev",
    "nyheder", "nyhedsarkiv", "aktuelt", "pressemeddelelser", "pressemeddelelse",
    "arkiv", "search", "soeg", "sog", "page"
}

@dataclass(frozen=True)
class Item:
    source: str
    title: str
    url: str
    published: datetime
    description: str = ""

def canonical_url(url: str) -> str:
    p = urlparse(url)
    return urlunparse((p.scheme or "https", p.netloc.lower(), p.path.rstrip("/") + "/", "", "", ""))

def same_site(a: str, b: str) -> bool:
    ha = urlparse(a).netloc.lower().removeprefix("www.")
    hb = urlparse(b).netloc.lower().removeprefix("www.")
    return ha == hb

def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()

def parse_date(value: str) -> datetime | None:
    if not value:
        return None
    value = clean_text(value)

    # ISO / RFC / almindelige numeriske datoer.
    try:
        dt = date_parser.parse(value, dayfirst=True, fuzzy=False)
        if dt.year >= 2000:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
    except Exception:
        pass

    # Dansk måned: 3. juni 2026
    m = re.search(
        r"\b(\d{1,2})\.?\s+("
        + "|".join(DANISH_MONTHS)
        + r")\s+(\d{4})\b",
        value.lower(),
    )
    if m:
        return datetime(
            int(m.group(3)), DANISH_MONTHS[m.group(2)], int(m.group(1)),
            tzinfo=timezone.utc,
        )

    # 03-06-2026 / 03.06.2026 / 03/06/2026
    m = re.search(r"\b(\d{1,2})[./-](\d{1,2})[./-](20\d{2})\b", value)
    if m:
        return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), tzinfo=timezone.utc)

    # 2026-06-03
    m = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", value)
    if m:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)

    return None

def date_from_soup(soup: BeautifulSoup) -> datetime | None:
    candidates: list[str] = []

    for key, value in [
        ("property", "article:published_time"),
        ("name", "article:published_time"),
        ("itemprop", "datePublished"),
        ("name", "date"),
        ("name", "publish-date"),
    ]:
        tag = soup.find("meta", attrs={key: value})
        if tag and tag.get("content"):
            candidates.append(tag["content"])

    for tag in soup.find_all("time"):
        if tag.get("datetime"):
            candidates.append(tag["datetime"])
        candidates.append(tag.get_text(" ", strip=True))

    # JSON-LD
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
            cur = stack.pop()
            if isinstance(cur, dict):
                if cur.get("datePublished"):
                    candidates.append(str(cur["datePublished"]))
                stack.extend(v for v in cur.values() if isinstance(v, (dict, list)))
            elif isinstance(cur, list):
                stack.extend(cur)

    # Synlig tekst tæt på toppen.
    text = clean_text(soup.get_text(" ", strip=True)[:5000])
    candidates.append(text)

    for candidate in candidates:
        dt = parse_date(candidate)
        if dt:
            return dt
    return None

def description_from_soup(soup: BeautifulSoup) -> str:
    for attrs in (
        {"property": "og:description"},
        {"name": "description"},
        {"name": "twitter:description"},
    ):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            return clean_text(tag["content"])[:700]
    p = soup.find("p")
    return clean_text(p.get_text(" ", strip=True) if p else "")[:700]

def title_from_soup(soup: BeautifulSoup) -> str:
    for attrs in ({"property": "og:title"}, {"name": "twitter:title"}):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            return clean_text(tag["content"])
    if soup.find("h1"):
        return clean_text(soup.find("h1").get_text(" ", strip=True))
    return clean_text(soup.title.get_text(" ", strip=True) if soup.title else "")

def request(session: requests.Session, url: str) -> requests.Response:
    r = session.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    time.sleep(REQUEST_DELAY_SECONDS)
    return r

def rss_candidates(source: dict) -> list[str]:
    result = list(source.get("rss_urls", []))
    for start in source["start_urls"]:
        if "?" in start:
            result.append(start + "&rss=true")
        else:
            result.append(start.rstrip("/") + "/?rss=true")
    # Bevar rækkefølge, fjern dubletter.
    return list(dict.fromkeys(result))

def items_from_rss(session: requests.Session, source: dict) -> list[Item]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    for url in rss_candidates(source):
        try:
            r = request(session, url)
        except Exception:
            continue
        content_type = (r.headers.get("content-type") or "").lower()
        prefix = r.text[:500].lower()
        if not any(x in content_type for x in ("xml", "rss", "atom")) and "<rss" not in prefix and "<feed" not in prefix:
            continue

        feed = feedparser.parse(r.content)
        if not feed.entries:
            continue

        items: list[Item] = []
        for e in feed.entries[:MAX_ITEMS_PER_MINISTRY * 2]:
            dt = None
            for key in ("published", "updated", "created"):
                if getattr(e, key, None):
                    dt = parse_date(getattr(e, key))
                    if dt:
                        break
            if not dt or dt < cutoff:
                continue
            link = getattr(e, "link", "") or ""
            title = clean_text(getattr(e, "title", ""))
            if not link or not title:
                continue
            desc = clean_text(getattr(e, "summary", "") or "")[:700]
            items.append(Item(source["name"], title, link, dt, desc))
        if items:
            return items[:MAX_ITEMS_PER_MINISTRY]
    return []

def looks_like_article(url: str, start_url: str, prefixes: list[str]) -> bool:
    if not same_site(url, start_url):
        return False
    p = urlparse(url)
    path = p.path.rstrip("/") + "/"
    if not any(path.startswith(prefix.rstrip("/") + "/") for prefix in prefixes):
        return False

    # Arkiv-/kategorisider er typisk meget korte eller ender i år/måned.
    segs = [s for s in p.path.split("/") if s]
    if not segs:
        return False
    last = segs[-1].lower()

    if last in SKIP_SEGMENTS:
        return False
    if re.fullmatch(r"20\d{2}", last):
        return False
    if last in DANISH_MONTHS:
        return False
    if re.fullmatch(r"(jan|feb|mar|apr|jun|jul|aug|sep|okt|nov|dec)", last):
        return False
    if re.fullmatch(r"side|page", last):
        return False

    # Kræv en rimelig artikel-slug, ikke bare ét kort menupunkt.
    return len(last) >= 8

def closest_context(anchor) -> str:
    for tag_name in ("article", "li", "section", "div"):
        node = anchor.find_parent(tag_name)
        if node:
            text = clean_text(node.get_text(" ", strip=True))
            if 10 <= len(text) <= 1800:
                return text
    return clean_text(anchor.parent.get_text(" ", strip=True) if anchor.parent else anchor.get_text(" ", strip=True))

def items_from_html(session: requests.Session, source: dict) -> list[Item]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    by_url: dict[str, Item] = {}

    for start_url in source["start_urls"]:
        try:
            r = request(session, start_url)
        except Exception as exc:
            print(f"[advarsel] {source['name']}: kan ikke hente {start_url}: {exc}", file=sys.stderr)
            continue

        soup = BeautifulSoup(r.text, "html.parser")
        candidates: list[tuple[str, str, str, datetime | None]] = []

        for a in soup.find_all("a", href=True):
            url = urljoin(start_url, a["href"])
            if not looks_like_article(url, start_url, source["article_prefixes"]):
                continue
            title = clean_text(a.get_text(" ", strip=True))
            if len(title) < 8:
                continue
            context = closest_context(a)
            dt = parse_date(context)
            candidates.append((url, title, context, dt))

        # Bevar første forekomst af hver URL.
        unique: dict[str, tuple[str, str, str, datetime | None]] = {}
        for c in candidates:
            unique.setdefault(c[0], c)
        candidates = list(unique.values())[:MAX_ITEMS_PER_MINISTRY * 3]

        article_fetches = 0
        for url, title, context, dt in candidates:
            desc = ""
            if not dt and article_fetches < MAX_ARTICLE_FETCHES_PER_START_URL:
                try:
                    ar = request(session, url)
                    article_fetches += 1
                    article_soup = BeautifulSoup(ar.text, "html.parser")
                    dt = date_from_soup(article_soup)
                    title = title_from_soup(article_soup) or title
                    desc = description_from_soup(article_soup)
                except Exception:
                    pass

            if not dt or dt < cutoff:
                continue

            if not desc:
                # Brug kun kontekst, hvis den ikke bare gentager hele navigationsområdet.
                desc = clean_text(context)
                if len(desc) > 700:
                    desc = desc[:697] + "..."

            key = canonical_url(url)
            by_url[key] = Item(source["name"], title, url, dt, desc)

    return sorted(by_url.values(), key=lambda x: x.published, reverse=True)[:MAX_ITEMS_PER_MINISTRY]

def collect_items(sources_path: Path) -> list[Item]:
    sources = json.loads(sources_path.read_text(encoding="utf-8"))
    session = requests.Session()
    all_items: dict[str, Item] = {}

    for source in sources:
        print(f"[henter] {source['name']}", file=sys.stderr)
        items = items_from_rss(session, source)
        if not items:
            items = items_from_html(session, source)

        for item in items:
            key = canonical_url(item.url)
            existing = all_items.get(key)
            if existing is None or item.published > existing.published:
                all_items[key] = item

        print(f"  -> {len(items)} emner", file=sys.stderr)

    return sorted(all_items.values(), key=lambda x: x.published, reverse=True)

def build_rss(items: Iterable[Item], site_url: str, feed_url: str) -> bytes:
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "Nyheder fra danske ministerier"
    ET.SubElement(channel, "link").text = site_url
    ET.SubElement(channel, "description").text = (
        "Samlet RSS-feed med nyheder og pressemeddelelser fra danske ministeriers officielle hjemmesider."
    )
    ET.SubElement(channel, "language").text = "da"
    ET.SubElement(channel, "lastBuildDate").text = email.utils.format_datetime(datetime.now(timezone.utc))
    ET.SubElement(channel, "generator").text = "DanskeMinisterierRSS"
    if feed_url:
        atom = "http://www.w3.org/2005/Atom"
        ET.register_namespace("atom", atom)
        ET.SubElement(channel, f"{{{atom}}}link", {
            "href": feed_url, "rel": "self", "type": "application/rss+xml"
        })

    for item in items:
        node = ET.SubElement(channel, "item")
        ET.SubElement(node, "title").text = f"{item.source}: {item.title}"
        ET.SubElement(node, "link").text = item.url
        guid = ET.SubElement(node, "guid", {"isPermaLink": "false"})
        guid.text = hashlib.sha256(canonical_url(item.url).encode("utf-8")).hexdigest()
        ET.SubElement(node, "pubDate").text = email.utils.format_datetime(item.published)
        ET.SubElement(node, "category").text = item.source
        src = ET.SubElement(node, "source", {"url": item.url})
        src.text = item.source
        if item.description:
            ET.SubElement(node, "description").text = item.description

    ET.indent(rss, space="  ")
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)


def fmt_date_da(dt: datetime) -> str:
    months = ["", "januar", "februar", "marts", "april", "maj", "juni", "juli", "august", "september", "oktober", "november", "december"]
    return f"{dt.day}. {months[dt.month]} {dt.year}"


def build_html(items: list[Item], feed_url: str, sources: list[dict]) -> str:
    ministries = sorted((x["name"] for x in sources), key=str.casefold)
    updated = datetime.now(timezone.utc)

    def esc(s: str) -> str:
        return html.escape(s or "", quote=True)

    cards = []
    for item in items:
        desc = clean_text(item.description)
        if len(desc) > 320:
            desc = desc[:317].rstrip() + "..."
        cards.append(f'''<article class="card" data-ministry="{esc(item.source.lower())}" data-search="{esc((item.source + ' ' + item.title + ' ' + desc).lower())}">
  <div class="meta"><span>{esc(item.source)}</span><time datetime="{esc(item.published.isoformat())}">{esc(fmt_date_da(item.published))}</time></div>
  <h2><a href="{esc(item.url)}" target="_blank" rel="noopener noreferrer">{esc(item.title)}</a></h2>
  {f'<p>{esc(desc)}</p>' if desc else ''}
  <a class="more" href="{esc(item.url)}" target="_blank" rel="noopener noreferrer">Læs hos ministeriet &nearr;</a>
</article>''')

    options = ['<option value="">Alle ministerier</option>'] + [
        f'<option value="{esc(m.lower())}">{esc(m)}</option>' for m in ministries
    ]
    feed_href = esc(feed_url or 'feed.xml')
    source_lookup = {x['name']: x for x in sources}
    source_links = ''.join(
        f'<li><a href="{esc(source_lookup[m].get("home_url", source_lookup[m]["start_urls"][0]))}" target="_blank" rel="noopener noreferrer">{esc(m)}</a></li>'
        for m in ministries
    )
    return f'''<!doctype html>
<html lang="da"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="description" content="Samlet oversigt over nyheder og pressemeddelelser fra danske ministeriers officielle hjemmesider."><title>Nyheder fra danske ministerier</title><link rel="alternate" type="application/rss+xml" title="RSS" href="{feed_href}">
<style>
:root{{--ink:#18222c;--muted:#5d6974;--line:#dce2e7;--bg:#f4f6f7;--paper:#fff;--brand:#7d1b2a;--brand2:#5f1420;--max:1120px}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}a{{color:inherit}}a:focus-visible,input:focus-visible,select:focus-visible{{outline:3px solid #0867c8;outline-offset:3px}}.top{{background:var(--brand2);color:#fff}}.wrap{{width:min(calc(100% - 32px),var(--max));margin:auto}}.top .wrap{{min-height:54px;display:flex;align-items:center;justify-content:space-between;gap:18px}}.brand{{font-weight:800;letter-spacing:.01em}}.rss{{color:#fff;text-decoration:none}}.rss:hover{{text-decoration:underline}}.hero{{background:var(--paper);border-bottom:1px solid var(--line)}}.hero .wrap{{padding:52px 0 38px}}.eyebrow{{margin:0 0 10px;color:var(--brand);font-size:.82rem;font-weight:800;text-transform:uppercase;letter-spacing:.09em}}h1{{font-size:clamp(2.15rem,5vw,3.8rem);line-height:1.02;letter-spacing:-.04em;margin:0;max-width:900px}}.intro{{max-width:800px;color:var(--muted);font-size:1.08rem;margin:18px 0 0}}.controls{{display:grid;grid-template-columns:minmax(0,1.8fr) minmax(230px,1fr);gap:12px;margin-top:30px}}label{{display:block;font-size:.84rem;font-weight:750;margin-bottom:6px}}input,select{{width:100%;min-height:49px;border:1px solid #aeb8c2;border-radius:7px;background:#fff;color:var(--ink);padding:10px 12px;font:inherit}}main.wrap{{padding:32px 0 58px}}.head{{display:flex;justify-content:space-between;align-items:end;gap:20px;margin-bottom:15px}}.head h2{{font-size:1.22rem;margin:0}}#count{{margin:0;color:var(--muted)}}.list{{display:grid;gap:14px}}.card{{background:var(--paper);border:1px solid var(--line);border-radius:10px;padding:22px}}.card:hover{{border-color:#c3cbd2}}.meta{{display:flex;gap:8px 14px;flex-wrap:wrap;color:var(--muted);font-size:.88rem}}.meta span{{color:var(--brand2);font-weight:800}}.card h2{{font-size:clamp(1.18rem,2.6vw,1.55rem);line-height:1.25;letter-spacing:-.012em;margin:8px 0 10px}}.card h2 a{{text-decoration:none}}.card h2 a:hover{{text-decoration:underline;text-decoration-thickness:2px;text-underline-offset:3px}}.card p{{color:#414d57;margin:0 0 14px;max-width:900px}}.more{{display:inline-block;color:var(--brand2);font-size:.94rem;font-weight:750;text-decoration:none}}.more:hover{{text-decoration:underline}}.empty{{display:none;background:#fff;border:1px solid var(--line);border-radius:10px;padding:28px;text-align:center;color:var(--muted)}}.sources{{margin-top:42px;padding-top:26px;border-top:1px solid var(--line)}}.sources h2{{margin:0 0 6px}}.sources p{{color:var(--muted);margin:0 0 14px}}.sources ul{{columns:2;column-gap:28px;margin:0;padding-left:20px}}.sources li{{break-inside:avoid;margin:5px 0}}.sources a{{color:var(--brand2)}}footer{{background:#fff;border-top:1px solid var(--line)}}footer .wrap{{padding:28px 0 38px;color:var(--muted);font-size:.9rem}}footer p{{margin:4px 0}}footer a{{color:var(--brand2)}}@media(max-width:720px){{.controls{{grid-template-columns:1fr}}.hero .wrap{{padding:34px 0 28px}}.card{{padding:18px}}.head{{align-items:start;flex-direction:column;gap:3px}}.sources ul{{columns:1}}}}
</style></head><body>
<div class="top"><div class="wrap"><div class="brand">Ministerienyt</div><a class="rss" href="{feed_href}">RSS-feed</a></div></div>
<header class="hero"><div class="wrap"><p class="eyebrow">Samlet nyhedsoverblik</p><h1>Nyheder fra danske ministerier</h1><p class="intro">Seneste nyheder og pressemeddelelser samlet fra ministeriernes officielle hjemmesider. Søg på tværs eller vælg et bestemt ministerium.</p><div class="controls" role="search"><div><label for="search">Søg i nyheder</label><input id="search" type="search" placeholder="Fx klima, økonomi eller sundhed" autocomplete="off"></div><div><label for="ministry">Ministerium</label><select id="ministry">{''.join(options)}</select></div></div></div></header>
<main class="wrap"><div class="head"><h2>Seneste nyheder</h2><p id="count">{len(items)} nyheder</p></div><section class="list" id="list">{''.join(cards)}</section><div class="empty" id="empty">Ingen nyheder matcher din søgning.</div><section class="sources"><h2>Kilder</h2><p>Ministerienyt henter fra disse 21 officielle ministerielle hjemmesider:</p><ul>{source_links}</ul></section></main>
<footer><div class="wrap"><p><strong>Ministerienyt</strong> er en uafhængig samling af links til ministeriernes egne nyheder.</p><p>Alle artikler åbner på den officielle kilde. Senest opdateret {esc(fmt_date_da(updated))}. <a href="{feed_href}">RSS-feed</a>.</p></div></footer>
<script>(()=>{{const s=document.getElementById('search'),m=document.getElementById('ministry'),cards=[...document.querySelectorAll('.card')],count=document.getElementById('count'),empty=document.getElementById('empty');const norm=v=>(v||'').toLocaleLowerCase('da-DK').trim();function go(){{const q=norm(s.value),mv=norm(m.value);let n=0;cards.forEach(c=>{{const show=(!q||c.dataset.search.includes(q))&&(!mv||c.dataset.ministry===mv);c.hidden=!show;if(show)n++}});count.textContent=n===1?'1 nyhed':n+' nyheder';empty.style.display=n?'none':'block';const u=new URL(location);q?u.searchParams.set('q',s.value.trim()):u.searchParams.delete('q');mv?u.searchParams.set('ministerium',m.value):u.searchParams.delete('ministerium');history.replaceState(null,'',u)}}const p=new URLSearchParams(location.search);if(p.get('q'))s.value=p.get('q');if(p.get('ministerium'))m.value=p.get('ministerium');s.addEventListener('input',go);m.addEventListener('change',go);go()}})();</script>
</body></html>'''

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default="sources.json")
    ap.add_argument("--rss-output", default="site/feed.xml")
    ap.add_argument("--html-output", default="site/index.html")
    ap.add_argument("--site-url", default="https://example.invalid/")
    ap.add_argument("--feed-url", default="")
    args = ap.parse_args()

    sources_path = Path(args.sources)
    sources = json.loads(sources_path.read_text(encoding="utf-8"))
    items = collect_items(sources_path)
    if not items:
        print("Ingen nyheder fundet. Eksisterende output blev ikke overskrevet.", file=sys.stderr)
        return 2

    rss_output = Path(args.rss_output)
    html_output = Path(args.html_output)
    rss_output.parent.mkdir(parents=True, exist_ok=True)
    html_output.parent.mkdir(parents=True, exist_ok=True)
    rss_output.write_bytes(build_rss(items, args.site_url, args.feed_url))
    html_output.write_text(build_html(items, args.feed_url or "feed.xml", sources), encoding="utf-8")
    print(f"Skrev {len(items)} nyheder til {html_output} og {rss_output}", file=sys.stderr)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
