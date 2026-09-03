#!/usr/bin/env python3
"""Små regressionstests for de kilde-layouts, der tidligere har givet fejl."""
import sys
import types
import unittest
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

try:
    import feedparser  # noqa: F401
except ImportError:  # gør filen testbar i offline udviklingsmiljøer
    sys.modules["feedparser"] = types.SimpleNamespace(parse=lambda content: types.SimpleNamespace(entries=[]))

from bs4 import BeautifulSoup
import ministerier_nyheder as m


class DateRegressionTests(unittest.TestCase):
    def test_danish_abbreviated_listing_month(self):
        self.assertEqual(m.parse_date("13 aug. 2026").date().isoformat(), "2026-08-13")

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

    def test_kulturministeriet_prefers_article_lead_over_site_metadata(self):
        title = "For få bruger kulturpasset: Nu tager kulturministeren konsekvensen"
        correct_lead = (
            "Færre unge end forventet har søgt om at få et digitalt kulturpas. "
            "Derfor afsætter kulturministeren 20 mio. kr. til, at flere unge kan "
            "blive en del af skræddersyede kulturpasforløb."
        )
        soup = BeautifulSoup(
            '''<script type="application/ld+json">{"@type":"NewsArticle","description":"Kulturministeriets væsentligste opgaver består i ministerrådgivning og lovgivningsmæssige initiativer."}</script>
            <meta name="description" content="Kulturministeriets væsentligste opgaver består i ministerrådgivning og lovgivningsmæssige initiativer.">
            <main><h1>For få bruger kulturpasset: Nu tager kulturministeren konsekvensen</h1><p>Færre unge end forventet har søgt om at få et digitalt kulturpas. Derfor afsætter kulturministeren 20 mio. kr. til, at flere unge kan blive en del af skræddersyede kulturpasforløb.</p></main>''',
            "html.parser",
        )
        self.assertEqual(
            m.description_from_soup(soup, title, [".manchet", ".lead", ".intro"]),
            correct_lead,
        )

    def test_kulturministeriet_keeps_completed_one_time_schema_refresh(self):
        sources = json.loads(Path("sources.json").read_text(encoding="utf-8"))
        kulturministeriet = next(source for source in sources if source["name"] == "Kulturministeriet")
        self.assertEqual(kulturministeriet["refresh_before_schema"], 11)
        self.assertLessEqual(kulturministeriet["refresh_before_schema"], m.ARCHIVE_SCHEMA_VERSION)

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

    def test_agency_config_has_exactly_78_unique_official_sources(self):
        raw = json.loads(Path("agency_sources.json").read_text(encoding="utf-8"))
        sources = m.load_sources_config(Path("agency_sources.json"))
        names = [source["name"] for source in sources]
        self.assertEqual(len(sources), 78)
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(all(source.get("responsible_ministry") for source in sources))
        self.assertEqual(raw["defaults"]["max_listing_pages"], 12)
        self.assertEqual(
            sorted(source["responsible_ministry"] for source in sources).count("Justitsministeriet"),
            9,
        )
        for source in sources:
            self.assertTrue(source.get("start_urls"), source["name"])
            for url in [source.get("home_url", ""), *source["start_urls"]]:
                parsed = urlparse(url)
                self.assertEqual(parsed.scheme, "https", f"{source['name']}: {url}")
                self.assertTrue(parsed.netloc, f"{source['name']}: {url}")
        for required in (
            "Statens Administration", "DREAM", "Civilstyrelsen",
            "Tilsynet med Efterretningstjenesterne", "CPR", "Havarikommissionen",
            "Forsvarsministeriets Auditørkorps", "Administrations- og Servicestyrelsen",
            "Udviklings- og Forenklingsstyrelsen", "It-tilsynet",
            "Styrelsen for Patientklager",
        ):
            self.assertIn(required, names)

    def test_police_source_is_limited_to_central_rigspolitiet_news(self):
        sources = m.load_sources_config(Path("agency_sources.json"))
        police = next(source for source in sources if source["name"] == "Rigspolitiet/politi.dk")
        self.assertEqual(police["start_urls"], ["https://politi.dk/rigspolitiet"])
        self.assertTrue(m.looks_like_article("https://politi.dk/rigspolitiet/nyhedsliste/central-nyhed", police))
        self.assertFalse(m.looks_like_article("https://politi.dk/koebenhavns-politi/doegnrapporter/lokal-rapport", police))

    def test_agency_redesign_routes_are_current(self):
        sources = {source["name"]: source for source in m.load_sources_config(Path("agency_sources.json"))}
        expected = {
            "Konkurrence- og Forbrugerstyrelsen": "https://www.kfst.dk/Menu/Presse",
            "Danmarks Domstole/Domstolsstyrelsen": "https://domstoldk.euwest01.umbraco.io/aktuelt/",
            "Rigspolitiet/politi.dk": "https://politi.dk/rigspolitiet",
            "PET": "https://pet.dk/pet",
            "Forsyningstilsynet": "https://forsyningstilsynet.dk/nyheder",
            "DMI": "https://www.dmi.dk/nyhedsoverblik",
            "Banedanmark": "https://www.bane.dk/da/Presse/Pressemeddelelser",
            "Det Nationale Forskningscenter for Arbejdsmiljø": "https://nfa.dk/nyt/",
            "Hjemmeværnet": "https://www.hjemmevaernet.dk/da/aktuelt/nyheder/",
            "Skattestyrelsen": "https://sktst.dk/nyheder-og-pressemeddelelser",
            "Skatteankestyrelsen": "https://skatteankestyrelsen.dk/aktuelt",
            "Finanstilsynet": "https://www.finanstilsynet.dk/nyheder-og-presse/nyheder-og-pressemeddelelser",
            "Sundhedsstyrelsen": "https://www.sst.dk/nyheder",
            "Sundhedsdatastyrelsen": "https://sundhedsdatastyrelsen.dk/nyheder",
            "Slots- og Kulturstyrelsen": "https://slks.dk/nyheder/",
            "Rigsarkivet": "https://www.rigsarkivet.dk/nyheder/",
            "Ankestyrelsen": "https://www.ast.dk/nyhedsarkiv",
        }
        for name, url in expected.items():
            self.assertIn(url, sources[name]["start_urls"], name)

        banedanmark = sources["Banedanmark"]
        self.assertTrue(m.looks_like_article(
            "https://www.bane.dk/da/Presse/Pressemeddelelser/Banedanmark-faar-ny-direktoer-for-Vedligehold",
            banedanmark,
        ))
        self.assertFalse(m.looks_like_article("https://www.bane.dk/da/om-banedanmark", banedanmark))

        self.assertEqual(
            sources["Danmarks Domstole/Domstolsstyrelsen"]["public_origin"],
            "https://www.domstol.dk",
        )
        self.assertTrue(sources["Ankestyrelsen"]["gobasic_dynamic_list"])
        self.assertEqual(sources["Erhvervsstyrelsen"]["ritzau_pressroom_id"], 11727618)
        self.assertEqual(sources["Sundhedsstyrelsen"]["ritzau_pressroom_id"], 13561973)
        self.assertTrue(sources["Rigsarkivet"]["disable_feeds"])
        self.assertEqual(sources["Rigsarkivet"]["refresh_before_schema"], m.ARCHIVE_SCHEMA_VERSION)

    def test_domstole_origin_is_canonicalized_and_published_on_public_host(self):
        source = {
            "name": "Danmarks Domstole/Domstolsstyrelsen",
            "home_url": "https://www.domstol.dk/",
            "start_urls": ["https://domstoldk.euwest01.umbraco.io/aktuelt/"],
            "extra_hosts": ["domstoldk.euwest01.umbraco.io"],
            "public_origin": "https://www.domstol.dk",
            "article_prefixes": ["/aktuelt/"],
        }
        origin_url = "https://domstoldk.euwest01.umbraco.io/aktuelt/2026/8/en-nyhed/"
        public_url = "https://www.domstol.dk/aktuelt/2026/8/en-nyhed/"
        self.assertTrue(m.looks_like_article(origin_url, source))
        self.assertEqual(m.canonical_url(origin_url), m.canonical_url(public_url))
        self.assertEqual(m.public_url_for_source(origin_url, source), public_url)

    def test_feed_discovery_can_be_disabled_for_broken_feed(self):
        source = {
            "start_urls": ["https://example.dk/nyheder/"],
            "rss_urls": ["https://example.dk/feed.xml"],
            "disable_feeds": True,
        }
        self.assertEqual(m.feed_candidate_urls(source, ["https://example.dk/auto.xml"]), [])

    def test_wrapped_article_card_isolated_from_sibling_titles(self):
        soup = BeautifulSoup('''<div class="list">
          <a class="card" href="/nyheder/foerste"><span>1. september 2026</span><h2>Første rubrik</h2><p>Første manchet.</p></a>
          <a class="card" href="/nyheder/anden"><span>25. august 2026</span><h2>Anden rubrik</h2><p>Anden manchet.</p></a>
        </div>''', "html.parser")
        source = {
            "name": "Testmyndighed",
            "home_url": "https://example.dk/",
            "start_urls": ["https://example.dk/nyheder/"],
            "article_prefixes": ["/nyheder/"],
            "allow_plain_listing_date": True,
        }
        anchor = soup.select('a[href="/nyheder/anden"]')[0]
        title, _, published, _ = m.listing_fields(
            anchor,
            "https://example.dk/nyheder/anden",
            "https://example.dk/nyheder/",
            source,
        )
        self.assertEqual(title, "Anden rubrik")
        self.assertEqual(published.date().isoformat(), "2026-08-25")

    def test_gobasic_dynamic_archive_returns_article_candidates(self):
        source = {
            "name": "Ankestyrelsen",
            "home_url": "https://www.ast.dk/",
            "start_urls": ["https://www.ast.dk/nyhedsarkiv"],
            "article_prefixes": ["/nyhedsarkiv/2026/"],
            "gobasic_dynamic_list": True,
            "max_listing_pages": 2,
        }
        config = json.dumps({"options": {"specification": {"options": {"maxItemsShown": 10}}}})
        shell = f'<div class="archive-search-result" data-config=\'{config}\'></div>'
        page_html = '''<div class="item">
          <h2><a href="/nyhedsarkiv/2026/sep/en-ny-officiel-afgoerelse">En ny officiel afgørelse</a></h2>
          <span data-date="2026-09-02T11:09:38Z">02-09-2026</span>
          <p>Kort beskrivelse af afgørelsen.</p>
        </div>'''

        class FakeSession:
            def __init__(self):
                self.calls = []

            def post(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return types.SimpleNamespace(
                    raise_for_status=lambda: None,
                    json=lambda: {
                        "pageHtml": page_html,
                        "totalResultCount": {"All": 1},
                    },
                )

        fake_session = FakeSession()
        original_fetch = m.fetch
        try:
            m.fetch = lambda *args, **kwargs: types.SimpleNamespace(
                url="https://www.ast.dk/nyhedsarkiv",
                headers={"content-type": "text/html; charset=utf-8"},
                content=shell.encode(),
                text=shell,
            )
            status = m.SourceStatus(source["name"], source["home_url"])
            candidates, _ = m.crawl_listing_pages(fake_session, source, status)
        finally:
            m.fetch = original_fetch

        self.assertEqual(len(candidates), 1)
        candidate = next(iter(candidates.values()))
        self.assertEqual(candidate.title, "En ny officiel afgørelse")
        self.assertEqual(candidate.published.date().isoformat(), "2026-09-02")
        self.assertIn("GoBasic API", status.methods)
        self.assertEqual(fake_session.calls[0][0], "https://www.ast.dk/gbapi/search/getPage")
        self.assertEqual(fake_session.calls[0][1]["headers"]["Referer"], "https://www.ast.dk/nyhedsarkiv")

    def test_shared_archive_source_filter_keeps_agencies_separate(self):
        siri = {"required_article_text": ["Publiceret af: SIRI", "Publiceret af SIRI"]}
        us = {"required_article_text": ["Publiceret af: Udlændingestyrelsen"]}
        article = "Nyhed Publiceret af: SIRI Denne tekst handler om en ny ordning."
        self.assertTrue(m.source_text_filter_matches(siri, article))
        self.assertFalse(m.source_text_filter_matches(us, article))

    def test_article_url_regex_rejects_non_news_route(self):
        source = {
            "home_url": "https://example.dk/",
            "article_url_regex": r"^/artikler/20\d{2}/",
        }
        self.assertTrue(m.looks_like_article("https://example.dk/artikler/2026/en-nyhed", source))
        self.assertFalse(m.looks_like_article("https://example.dk/om-os/kontakt", source))

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
            def fake_listing(session, src, status, source_state, fast=False, full_audit=False):
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

    def test_fast_crawl_limits_pagination_without_warning(self):
        source = {
            "name": "Testministeriet",
            "home_url": "https://example.dk/",
            "start_urls": ["https://example.dk/nyheder"],
            "historical_start_urls": ["https://example.dk/nyheder/2026"],
            "article_prefixes": ["/nyheder/"],
        }
        calls = []
        original_fetch = m.fetch
        old_cfg = dict(m.RUNTIME_CONFIG)
        try:
            m.RUNTIME_CONFIG["fast_listing_pages"] = 4
            def fake_fetch(session, url):
                calls.append(url)
                page = len(calls)
                body = (
                    f'<a href="/nyheder/artikel-{page}">En tydelig artikeloverskrift nummer {page}</a>'
                    f'<a href="/nyheder?page={page + 1}">Næste</a>'
                )
                return types.SimpleNamespace(
                    url=url,
                    headers={"content-type": "text/html"},
                    content=body.encode(),
                    text=body,
                )
            m.fetch = fake_fetch
            status = m.SourceStatus(source["name"], source["home_url"])
            candidates, _ = m.crawl_listing_pages(None, source, status, {}, fast=True)
        finally:
            m.fetch = original_fetch
            m.RUNTIME_CONFIG.clear(); m.RUNTIME_CONFIG.update(old_cfg)
        self.assertEqual(status.listing_pages, 4)
        self.assertEqual(len(candidates), 4)
        self.assertTrue(status.fast_mode)
        self.assertTrue(status.pagination_limited)
        self.assertEqual(status.errors, [])
        self.assertNotIn(source["historical_start_urls"][0], calls)

    def test_fast_state_keeps_deep_candidate_baseline(self):
        status = m.SourceStatus("Testministeriet", "https://example.dk/")
        status.fast_mode = True
        status.listing_pages = 4
        status.article_candidates = 3
        status.methods.append("HTML")
        previous = {"schema_version": 1, "sources": {"Testministeriet": {"last_full_candidate_count": 120}}}
        updated = m.update_source_state(previous, [status])
        self.assertEqual(updated["sources"]["Testministeriet"]["last_full_candidate_count"], 120)

    def test_workflow_uses_danish_time_and_light_hourly_checks(self):
        workflow = Path(".github/workflows/pages.yml").read_text(encoding="utf-8")
        self.assertIn('cron: "7 6-18 * * *"', workflow)
        self.assertIn('cron: "7 0,21 * * *"', workflow)
        self.assertIn('cron: "7 3 * * *"', workflow)
        self.assertGreaterEqual(workflow.count('timezone: "Europe/Copenhagen"'), 4)
        self.assertIn('CRAWL_FLAG="--fast"', workflow)
        self.assertIn('--sources agency_sources.json', workflow)
        self.assertIn('--archive agency_archive.json', workflow)
        self.assertIn('else\n            CRAWL_FLAG="--fast"', workflow)
        self.assertIn('--html-output site/styrelsesnyt/index.html', workflow)

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

    def test_better_item_heals_kulturministeriet_boilerplate_description(self):
        dt = datetime.fromisoformat("2026-09-01T08:00:00+00:00")
        old = m.Item(
            "Kulturministeriet", "Kulturpas", "https://kum.dk/aktuelt/nyheder/kulturpas", dt,
            "Kulturministeriets væsentligste opgaver består i ministerrådgivning og lovgivningsmæssige initiativer. Kulturministeriet består af et departement og en styrelse.",
        )
        new = m.Item(
            "Kulturministeriet", "Kulturpas", "https://kum.dk/aktuelt/nyheder/kulturpas", dt,
            "Færre unge end forventet har søgt om at få et digitalt kulturpas.",
        )
        self.assertEqual(m.better_item(old, new).description, new.description)

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
        self.assertIn("v7.0.1", soup.select_one("footer").get_text(" ", strip=True))
        self.assertIn("Kulturministeriets synlige artikelmanchet", html)
        self.assertEqual([link.get_text(strip=True) for link in soup.select(".brand-nav .brand-link")], ["Ministerienyt", "Styrelsesnyt"])
        self.assertEqual(soup.select_one(".brand-nav .brand-link.active").get_text(strip=True), "Ministerienyt")
        self.assertEqual(soup.select_one(".brand-nav a[href='styrelsesnyt/']").get_text(strip=True), "Styrelsesnyt")
        self.assertIn("Unikke besøg seneste 30 dage", rows[1].get_text(" ", strip=True))
        self.assertIn("dage:&nbsp;<span id=\"visit-counter\"", html)
        outage = soup.select_one("#sources > summary #outage-status")
        self.assertIsNotNone(outage)
        self.assertTrue(outage.has_attr("hidden"))
        self.assertIsNone(soup.select_one("header #outage-status"))
        self.assertIn("const STALLED_AFTER_MISSED_RUNS = 2", html)
        self.assertIn("const STALLED_GRACE_MS = 20 * 60 * 1000", html)
        self.assertIn("SCHEDULED_HOURS_COPENHAGEN", html)
        self.assertIn("missedScheduledRuns(stamp, Date.now()) >= STALLED_AFTER_MISSED_RUNS", html)
        self.assertNotIn("Opdatering forsinket", html)
        self.assertEqual(soup.select_one('link[rel="canonical"]')["href"], "https://example.dk/ministerienyt/")
        self.assertEqual(soup.select_one('meta[property="og:title"]')["content"], "Ministerienyt")
        self.assertIn("params.getAll('favorit')", html)
        self.assertIn("url.searchParams.append('favorit', favorite)", html)
        self.assertEqual(len(soup.select("#favorites-menu")), 1)
        self.assertIsNotNone(soup.select_one("#favorites-menu #mine-only"))
        self.assertIsNone(soup.select_one(".quick-actions > #mine-only"))
        self.assertNotIn("★ Favoritter", html)

    def test_styrelsesnyt_is_independent_main_page(self):
        item = self.item("Digitaliseringsstyrelsen", "Ny digital løsning gør hverdagen enklere", "https://digst.dk/nyheder/test", "2026-08-20")
        source = {
            "name": "Digitaliseringsstyrelsen",
            "home_url": "https://digst.dk/",
            "start_urls": ["https://digst.dk/nyheder/"],
            "article_prefixes": ["/nyheder/"],
            "responsible_ministry": "Forsknings-, Uddannelses- og Digitaliseringsministeriet",
        }
        status = m.SourceStatus(source["name"], source["home_url"])
        status.listing_pages = 1
        config = {
            "site_name": "Styrelsesnyt",
            "site_kind": "agencies",
            "page_heading": "Nyheder fra danske styrelser og myndigheder",
            "favorites_label": "Mine myndigheder",
            "storage_namespace": "styrelsesnyt",
            "ministerienyt_href": "../",
            "styrelsesnyt_href": "./",
        }
        html = m.build_html(
            [m.DisplayEntry(item)], "feed.xml", [source], [status],
            site_url="https://example.dk/ministerienyt/styrelsesnyt/", ui_config=config,
        )
        soup = BeautifulSoup(html, "html.parser")
        self.assertEqual(soup.title.get_text(strip=True), "Styrelsesnyt")
        self.assertEqual(soup.select_one("h1").get_text(strip=True), "Nyheder fra danske styrelser og myndigheder")
        self.assertEqual(soup.select_one(".brand-nav .brand-link.active").get_text(strip=True), "Styrelsesnyt")
        self.assertEqual(soup.select_one(".brand-nav a[href='../']").get_text(strip=True), "Ministerienyt")
        self.assertIn("Mine myndigheder", soup.select_one("#favorites-summary").get_text(" ", strip=True))
        self.assertIn('"styrelsesnyt.seenArticleIds.v2"', html)
        self.assertNotIn('"ministerienyt.seenArticleIds.v2"', html)
        self.assertIn("Forsknings-, Uddannelses- og Digitaliseringsministeriet", html)
        self.assertIn("Samme historie fra flere styrelser eller myndigheder samles i ét kort.", html)

    def test_styrelsesnyt_rss_has_own_identity(self):
        item = self.item("Digitaliseringsstyrelsen", "Ny digital løsning gør hverdagen enklere", "https://digst.dk/nyheder/test", "2026-08-20")
        rss = m.build_rss(
            [m.DisplayEntry(item)],
            "https://example.dk/styrelsesnyt/",
            "https://example.dk/styrelsesnyt/feed.xml",
            {item.source: {}},
            {"site_name": "Styrelsesnyt", "rss_title": "Styrelsesnyt – nyheder fra danske styrelser og myndigheder"},
        ).decode("utf-8")
        self.assertIn("Styrelsesnyt – nyheder fra danske styrelser og myndigheder", rss)
        self.assertIn("Styrelsesnyt 7.0.1", rss)

    def test_quality_warning_is_visible_separately_from_technical_status(self):
        item = self.item("Testministeriet", "En testartikel med en tydelig titel", "https://example.dk/nyheder/test", "2026-08-20")
        source = {"name":"Testministeriet","home_url":"https://example.dk/","start_urls":["https://example.dk/nyheder"],"article_prefixes":["/nyheder/"]}
        status = m.SourceStatus("Testministeriet", "https://example.dk/")
        status.listing_pages = 1
        status.self_test = "warn"
        status.self_test_notes = ["En hentemetode fejlede, men en anden lykkedes."]
        html = m.build_html([m.DisplayEntry(item)], "feed.xml", [source], [status])
        soup = BeautifulSoup(html, "html.parser")
        self.assertIsNone(soup.select_one("#health-link"))
        self.assertIn("1 bemærkning", soup.select_one("#sources summary").get_text(" ", strip=True))
        self.assertIn("Bemærkning", soup.select_one("#sources tbody tr").get_text(" ", strip=True))
        self.assertIn("En hentemetode fejlede, men en anden lykkedes.", html)


if __name__ == "__main__":
    unittest.main()
