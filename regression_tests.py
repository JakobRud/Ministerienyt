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
        html = m.build_html([m.DisplayEntry(item)], "feed.xml", [source], [status], goatcounter_code="ministerienyt", ui_config={})
        soup = BeautifulSoup(html, "html.parser")
        rows = soup.select("footer .footer-row")
        self.assertEqual(len(rows), 2)
        self.assertIn("v6.0", soup.select_one("footer").get_text(" ", strip=True))
        self.assertIn("Unikke besøg seneste 30 dage", rows[1].get_text(" ", strip=True))


if __name__ == "__main__":
    unittest.main()
