#!/usr/bin/env python3
"""Små regressionstests for de kilde-layouts, der tidligere har givet fejl."""
import sys
import types
import unittest
from datetime import datetime, timezone

try:
    import feedparser  # noqa: F401
except ImportError:  # gør filen testbar i offline udviklingsmiljøer
    sys.modules["feedparser"] = types.SimpleNamespace(parse=lambda content: types.SimpleNamespace(entries=[]))

from bs4 import BeautifulSoup
import ministerier_nyheder as m


class DateRegressionTests(unittest.TestCase):
    def test_kulturministeriet_plain_listing_date(self):
        soup = BeautifulSoup('''<article><a href="/aktuelt/nyheder/test"><h2>Kulturminister vil beskytte det danske sprog</h2></a><p>Manchet.</p><span>20.08.2026</span></article>''', "html.parser")
        source = {"name":"Kulturministeriet","home_url":"https://kum.dk/","start_urls":["https://kum.dk/aktuelt/nyheder"],"article_prefixes":["/aktuelt/nyheder/"],"allow_plain_listing_date":True}
        _, _, published, _ = m.listing_fields(soup.find("a"), "https://kum.dk/aktuelt/nyheder/test", "https://kum.dk/aktuelt/nyheder", source)
        self.assertEqual(published.date().isoformat(), "2026-08-20")

    def test_baebm_date_after_h1(self):
        soup = BeautifulSoup('<h1>Nyhed</h1><div>07-08-2026</div><p>Brødtekst med 15-09-2026.</p>', "html.parser")
        self.assertEqual(m.date_from_soup(soup, {"allow_unlabeled_after_h1_date": True}).date().isoformat(), "2026-08-07")

    def test_svmn_date_before_h1(self):
        soup = BeautifulSoup('<div>17-08-2026</div><h1>Knap 100.000 danskere har fået glæde af kørselsfradraget</h1>', "html.parser")
        self.assertEqual(m.date_from_soup(soup, {"allow_unlabeled_header_date": True}).date().isoformat(), "2026-08-17")

    def test_kefm_body_date_is_not_publication_date(self):
        soup = BeautifulSoup('<meta property="article:published_time" content="2026-07-02T09:00:00+02:00"><h1>Tommy Ahlers er ny bestyrelsesformand</h1><p>Tiltræder den 15. august 2026.</p>', "html.parser")
        self.assertEqual(m.date_from_soup(soup, {}).date().isoformat(), "2026-07-02")

    def test_statsministeriet_date_after_h1(self):
        soup = BeautifulSoup('<h1>Statsministeren skal drøfte luftforsvar med præsident Zelenskyy i Kyiv på flagdag</h1><ul><li>23.08.2026</li><li>Mette Frederiksen</li></ul><p>Brødtekst med 24.08.2026.</p>', "html.parser")
        source = {"allow_unlabeled_after_h1_date": True, "after_h1_date_max_text_nodes": 10}
        self.assertEqual(m.date_from_soup(soup, source).date().isoformat(), "2026-08-23")

    def test_kulturministeriet_article_header_date(self):
        soup = BeautifulSoup('<h1>Kulturminister vil beskytte det danske sprog</h1><p>Kort manchet.</p><span>Pressemeddelelse</span><span>Nyhed</span><div>20.08.2026</div><p>Brødtekst med 01.09.2026.</p>', "html.parser")
        source = {"allow_unlabeled_after_h1_date": True, "after_h1_date_max_text_nodes": 12}
        self.assertEqual(m.date_from_soup(soup, source).date().isoformat(), "2026-08-20")

    def test_mgtp_date_after_h1_corrects_body_event_date(self):
        soup = BeautifulSoup('<h1>Naturstyrelsen har skudt problemulv nær Houstrup</h1><div>14-08-2026</div><span>Nyhed</span><p>Naturstyrelsen har torsdag den 13. august skudt en problemulv.</p>', "html.parser")
        source = {"allow_unlabeled_after_h1_date": True, "after_h1_date_max_text_nodes": 8}
        self.assertEqual(m.date_from_soup(soup, source).date().isoformat(), "2026-08-14")

    def test_mssb_date_after_lead(self):
        soup = BeautifulSoup('<h1>Gode råd til voksne og forældre: Tal med dit barn om kriser</h1><h2>Nu lanceres syv gode råd til voksne.</h2><ul><li>26.08.2026</li></ul><p>Brødtekst.</p>', "html.parser")
        source = {"allow_unlabeled_after_h1_date": True, "after_h1_date_max_text_nodes": 12}
        self.assertEqual(m.date_from_soup(soup, source).date().isoformat(), "2026-08-26")

    def test_mim_date_after_lead(self):
        soup = BeautifulSoup('<h1>TotalEnergies politianmeldt for oliespild og kemikalieudledninger i Nordsøen</h1><p>Miljøstyrelsen har politianmeldt firmaet.</p><div>21. august 2026</div><p>Den 20. august har Miljøstyrelsen politianmeldt virksomheden.</p>', "html.parser")
        source = {"allow_unlabeled_after_h1_date": True, "after_h1_date_max_text_nodes": 14}
        self.assertEqual(m.date_from_soup(soup, source).date().isoformat(), "2026-08-21")

    def test_baebm_prefers_article_h1_over_generic_og_title(self):
        soup = BeautifulSoup('<meta property="og:title" content="Børne-, Ældre- og Boligministeriet"><h1>Ny undersøgelse viser stor tilfredshed blandt forældre til børn i dagtilbud</h1>', "html.parser")
        source = {"name": "Børne-, Ældre- og Boligministeriet"}
        self.assertEqual(m.title_from_soup(soup, source), "Ny undersøgelse viser stor tilfredshed blandt forældre til børn i dagtilbud")


class IdentityAndSafetyTests(unittest.TestCase):
    def item(self, source, title, url, day):
        dt = datetime.fromisoformat(day + "T08:00:00+00:00")
        return m.with_item_identity(m.Item(source, title, url, dt, "Kort beskrivelse"), first_seen_at=dt)

    def test_article_id_is_url_independent(self):
        title = "Samme transportnyhed med en lang og tydelig titel"
        a = self.item("By-, Land- og Transportministeriet", title, "https://trm.dk/nyheder/samme", "2026-04-02")
        b = self.item("By-, Land- og Transportministeriet", title, "https://bltm.dk/nyheder/samme", "2026-04-02")
        self.assertEqual(a.article_id, b.article_id)

    def test_cross_source_duplicate(self):
        title = "Regeringen lancerer en ny samlet indsats for grøn omstilling i Danmark"
        a = self.item("Regeringen.dk", title, "https://regeringen.dk/a", "2026-08-20")
        b = self.item("Klima-, Energi- og Forsyningsministeriet", title, "https://kefm.dk/b", "2026-08-20")
        self.assertTrue(m.duplicate_match(a, b))

    def test_refresh_guard_keeps_last_good(self):
        old = [self.item("Testministeriet", f"Gammel artikel nummer {i} med en tydelig titel", f"https://x.dk/n/{i}", f"2026-01-{i+1:02d}") for i in range(10)]
        fresh = [self.item("Testministeriet", "Kun én artikel efter parserændring", "https://x.dk/n/new", "2026-08-20")]
        status = m.SourceStatus("Testministeriet", "https://x.dk/")
        status.listing_pages = 1
        status.self_test = "pass"
        kept, accepted = m.prepare_refresh_merge(old, fresh, [status], {"Testministeriet"})
        self.assertEqual((len(kept), len(accepted), status.self_test), (10, 1, "warn"))


    def test_source_state_and_alert_threshold(self):
        status = m.SourceStatus("Testministeriet", "https://x.dk/")
        status.self_test = "fail"
        state = {"schema_version": 1, "sources": {"Testministeriet": {"consecutive_failures": 2}}}
        updated = m.update_source_state(state, [status])
        self.assertEqual(updated["sources"]["Testministeriet"]["consecutive_failures"], 3)
        old_cfg = dict(m.RUNTIME_CONFIG)
        try:
            m.RUNTIME_CONFIG["alert_after_failures"] = 3
            alerts = m.alerts_payload(updated, [status])
        finally:
            m.RUNTIME_CONFIG.clear(); m.RUNTIME_CONFIG.update(old_cfg)
        self.assertEqual(alerts["active_alerts"], 1)


    def test_self_test_warns_when_safe_dates_are_mostly_missing(self):
        status = m.SourceStatus("Testministeriet", "https://x.dk/")
        status.listing_pages = 1
        status.accepted_new = 1
        old = dict(m.REJECTED_CANDIDATES)
        try:
            m.REJECTED_CANDIDATES.clear()
            for i in range(4):
                m.REJECTED_CANDIDATES[f"missing-{i}"] = {"source": "Testministeriet", "reason": "missing_safe_publication_date"}
            m.evaluate_source_self_test(status, {})
        finally:
            m.REJECTED_CANDIDATES.clear(); m.REJECTED_CANDIDATES.update(old)
        self.assertEqual(status.self_test, "warn")
        self.assertTrue(any("sikker publiceringsdato" in note for note in status.self_test_notes))

    def test_ritzau_can_supplement_dynamic_html_source(self):
        source = {
            "name": "Testministeriet",
            "home_url": "https://example.dk/",
            "start_urls": ["https://example.dk/nyheder"],
            "article_prefixes": ["/nyheder/"],
            "ritzau_pressroom_id": 123,
            "ritzau_supplemental": True,
            "disable_sitemap": True,
        }
        item = self.item(
            "Testministeriet",
            "En officiel pressemeddelelse fundet via Ritzau",
            "https://via.ritzau.dk/pressemeddelelse/test",
            "2026-08-21",
        )
        original_ritzau = m.collect_ritzau_items
        original_listing = m.crawl_listing_pages
        original_feed = m.collect_feed_items
        calls = {"listing": 0}
        try:
            def fake_ritzau(session, src, known, status):
                status.article_candidates = 1
                status.methods.append("Via Ritzau API")
                return [item], True
            def fake_listing(session, src, status, source_state, full_audit=False):
                calls["listing"] += 1
                status.listing_pages = 1
                return {}, []
            m.collect_ritzau_items = fake_ritzau
            m.crawl_listing_pages = fake_listing
            m.collect_feed_items = lambda *args, **kwargs: []
            items, status = m.collect_source(None, source, set(), {}, full_audit=False)
        finally:
            m.collect_ritzau_items = original_ritzau
            m.crawl_listing_pages = original_listing
            m.collect_feed_items = original_feed
        self.assertEqual(calls["listing"], 1)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, item.title)
        self.assertEqual(status.article_candidates, 1)

    def test_sitemap_fallback_runs_when_no_other_discovery_method_works(self):
        source = {
            "name": "Testministeriet",
            "home_url": "https://example.dk/",
            "start_urls": ["https://example.dk/nyheder"],
            "article_prefixes": ["/nyheder/"],
        }
        original_ritzau = m.collect_ritzau_items
        original_listing = m.crawl_listing_pages
        original_feed = m.collect_feed_items
        original_sitemap = m.discover_sitemap_candidates
        original_due = m.due_since
        calls = {"sitemap": 0}
        try:
            m.collect_ritzau_items = lambda *args, **kwargs: ([], False)
            m.crawl_listing_pages = lambda *args, **kwargs: ({}, [])
            m.collect_feed_items = lambda *args, **kwargs: []
            m.due_since = lambda *args, **kwargs: False
            def fake_sitemap(session, src, status):
                calls["sitemap"] += 1
                status.sitemap_files = 1
                return {}
            m.discover_sitemap_candidates = fake_sitemap
            _, status = m.collect_source(None, source, set(), {"last_sitemap_scan_at": datetime.now(timezone.utc).isoformat()})
        finally:
            m.collect_ritzau_items = original_ritzau
            m.crawl_listing_pages = original_listing
            m.collect_feed_items = original_feed
            m.discover_sitemap_candidates = original_sitemap
            m.due_since = original_due
        self.assertEqual(calls["sitemap"], 1)
        self.assertTrue(status.sitemap_scan_performed)

    def test_sitemap_ignores_explicit_old_year_even_with_new_lastmod(self):
        source = {
            "name": "Testministeriet",
            "home_url": "https://example.dk/",
            "start_urls": ["https://example.dk/nyheder"],
            "article_prefixes": ["/nyheder/"],
        }
        xml = b'''<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://example.dk/nyheder/2025/gammel-artikel</loc><lastmod>2026-08-27</lastmod></url>
          <url><loc>https://example.dk/nyheder/2026/ny-artikel</loc><lastmod>2026-08-27</lastmod></url>
        </urlset>'''
        original_seeds = m.sitemap_seed_urls
        original_fetch = m.fetch
        try:
            m.sitemap_seed_urls = lambda *args, **kwargs: ["https://example.dk/sitemap.xml"]
            m.fetch = lambda *args, **kwargs: types.SimpleNamespace(content=xml)
            result = m.discover_sitemap_candidates(None, source, m.SourceStatus(source["name"], source["home_url"]))
        finally:
            m.sitemap_seed_urls = original_seeds
            m.fetch = original_fetch
        self.assertEqual(list(result.values())[0].url, "https://example.dk/nyheder/2026/ny-artikel")
        self.assertEqual(len(result), 1)

    def test_better_item_heals_title_equal_to_source_name(self):
        old = self.item("Testministeriet", "Testministeriet", "https://example.dk/nyheder/test", "2026-08-20")
        new = self.item("Testministeriet", "En rigtig og meningsfuld artikeloverskrift", "https://example.dk/nyheder/test", "2026-08-20")
        self.assertEqual(m.better_item(old, new).title, new.title)

    def test_rss_and_category_routes_are_not_articles(self):
        source = {"home_url": "https://example.dk/", "article_prefixes": ["/nyheder/"]}
        self.assertFalse(m.looks_like_article("https://example.dk/nyheder/nyheder-rss", source))
        self.assertFalse(m.looks_like_article("https://example.dk/nyheder/faglige-nyheder", source))

    def test_archive_identity_survives_serialization_fields(self):
        item = self.item("Testministeriet", "En stabil artikelidentitet med en tydelig titel", "https://x.dk/nyheder/stabil", "2026-08-20")
        raw = {"source": item.source, "title": item.title, "url": item.url, "published": item.published.isoformat(), "description": item.description, "article_id": item.article_id, "first_seen_at": item.first_seen_at.isoformat()}
        loaded = m.item_from_archive_dict(raw)
        self.assertEqual(loaded.article_id, item.article_id)
        self.assertEqual(loaded.first_seen_at, item.first_seen_at)

    def test_footer_is_two_rows(self):
        item = self.item("Testministeriet", "En testartikel med tilstrækkelig lang titel", "https://example.dk/nyheder/test", "2026-08-20")
        source = {"name":"Testministeriet","home_url":"https://example.dk/","start_urls":["https://example.dk/nyheder"],"article_prefixes":["/nyheder/"]}
        status = m.SourceStatus("Testministeriet", "https://example.dk/")
        status.listing_pages = 1
        html = m.build_html(
            [m.DisplayEntry(item)], "feed.xml", [source], [status],
            site_url="https://example.dk/ministerienyt/",
            goatcounter_code="ministerienyt", ui_config={},
        )
        soup = BeautifulSoup(html, "html.parser")
        rows = soup.select("footer .footer-row")
        self.assertEqual(len(rows), 2)
        self.assertIn("v6.2", soup.select_one("footer").get_text(" ", strip=True))
        self.assertIn("Unikke besøg seneste 30 dage", rows[1].get_text(" ", strip=True))
        self.assertEqual(soup.select_one('link[rel="canonical"]')["href"], "https://example.dk/ministerienyt/")
        self.assertEqual(soup.select_one('meta[property="og:title"]')["content"], "Ministerienyt")
        self.assertIn("params.getAll('favorit')", html)
        self.assertIn("url.searchParams.append('favorit', favorite)", html)

    def test_quality_warning_is_visible_separately_from_technical_status(self):
        item = self.item("Testministeriet", "En testartikel med en tydelig titel", "https://example.dk/nyheder/test", "2026-08-20")
        source = {"name":"Testministeriet","home_url":"https://example.dk/","start_urls":["https://example.dk/nyheder"],"article_prefixes":["/nyheder/"]}
        status = m.SourceStatus("Testministeriet", "https://example.dk/")
        status.listing_pages = 1
        status.self_test = "warn"
        html = m.build_html([m.DisplayEntry(item)], "feed.xml", [source], [status])
        self.assertIn("1/1 kilder OK · 1 advarsel", html)


if __name__ == "__main__":
    unittest.main()
