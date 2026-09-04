#!/usr/bin/env python3
import requests
from bs4 import BeautifulSoup
import json
import os
from datetime import datetime
from urllib.parse import urljoin

HEADERS = {"User-Agent": "Ministerienyt-scraper/1.0 (+https://github.com/jakobrud/Ministerienyt)"}
BASE_DIR = os.path.dirname(os.path.dirname(__file__))
DATA_PATH = os.path.join(BASE_DIR, "data", "sources.json")
SOURCES_LIST = os.path.join(BASE_DIR, "scripts", "sources-list.txt")

def read_list():
    urls = []
    if os.path.exists(SOURCES_LIST):
        with open(SOURCES_LIST, "r", encoding="utf-8") as f:
            for line in f:
                line=line.strip()
                if not line or line.startswith("#"): continue
                urls.append(line)
    if not urls and os.path.exists(DATA_PATH):
        try:
            with open(DATA_PATH, "r", encoding="utf-8") as f:
                j = json.load(f)
                for it in j.get("items", []):
                    if it.get("url"): urls.append(it["url"])
        except Exception:
            pass
    return urls

def fetch_meta(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        status = r.status_code
        if status != 200:
            return {"url": url, "status": "dead", "title": None, "publisher": None, "date": None, "canonical": r.url}
        html = r.text
        soup = BeautifulSoup(html, "html.parser")
        title = None
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            title = og_title["content"].strip()
        publisher = None
        og_site = soup.find("meta", property="og:site_name")
        if og_site and og_site.get("content"):
            publisher = og_site["content"].strip()
        date = None
        for name in ["article:published_time", "og:updated_time", "date", "pubdate"]:
            tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
            if tag and tag.get("content"):
                date = tag["content"].strip()
                break
        canonical = None
        link_c = soup.find("link", rel="canonical")
        if link_c and link_c.get("href"):
            canonical = urljoin(r.url, link_c["href"].strip())
        else:
            canonical = r.url
        return {"url": url, "status": "ok", "title": title, "publisher": publisher, "date": date, "canonical": canonical}
    except Exception as e:
        return {"url": url, "status": "dead", "title": None, "publisher": None, "date": None, "canonical": url}

def main():
    urls = read_list()
    items = []
    for u in urls:
        print("Henter:", u)
        meta = fetch_meta(u)
        items.append(meta)
    version = "7.0.3"
    out = {"version": version, "generated_at": datetime.utcnow().isoformat() + "Z", "items": items}
    os.makedirs(os.path.join(BASE_DIR, "data"), exist_ok=True)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("Wrote", DATA_PATH)

if __name__ == "__main__":
    main()
