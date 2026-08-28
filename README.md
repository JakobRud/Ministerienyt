# Ministerienyt 6.3 – komplet arkiv fra 2026

Ministerienyt samler officielle nyheder fra 21 danske ministerielle hjemmesider samt Regeringen.dk.

## Nyt i version 6.3

- Workflowet opdaterer i dansk tid hver time kl. 06–18 samt kl. 21, 00 og 03. De almindelige kørsler er begrænset til få aktive listesider pr. kilde.
- Kildetjek og kvalitetsadvarsler er fjernet fra toppen. Konkrete bemærkninger kan ses under **Kilder og dækning**.
- **Mine ministerier** samler valg og filtrering i én menu: vælg kilderne, og brug derefter **Vis kun mine**.
- Mellemrum ved footerteksten for unikke besøg er rettet.

## Tidligere forbedringer i version 6.2

- Sitemap-baserede kilder kontrolleres straks, når HTML, RSS og Ritzau ikke giver en brugbar opdagelsesmetode. De er derfor ikke længere afhængige af en 24-timers sitemap-cache.
- Fuld audit springer URL'er med et sikkert år før 2026 over. Det reducerer både køretid og falske datoadvarsler markant.
- Arkivet er suppleret med 10 manglende artikler fundet i den fulde 6.1-audit.
- En gammel BAEBM-post har fået sin korrekte artikeloverskrift, og crawleren kan fremover hele titler, der fejlagtigt er lig kildenavnet.
- **Del visning** med **Mine ministerier** medtager nu de valgte favoritter i linket.
- Forsiden skelner mellem teknisk kildestatus og kvalitetsadvarsler.
- Canonical-, Open Graph- og Twitter-metadata forbedrer deling og søgemaskineindeksering.
- `site/status.json` publiceres igen som maskinlæsbar status.
- En fuld audit kan startes manuelt fra GitHub Actions uden at ændre kode.
- XML-sitemaps parses med `defusedxml` som ekstra sikkerhed.

## Drift

Workflowet forsøger at opdatere siden hver time kl. 06–18 samt kl. 21, 00 og 03 i dansk tid. De almindelige kørsler laver lette friskhedstjek, mens kl. 03-kørslen kontrollerer aktive arkiver og sitemaps dybere. GitHub kan forsinke planlagte kørsler, så en opdatering inden for en time er et servicemål og ikke en hård garanti. Den første dag i hver måned køres automatisk en fuld audit.

En let kørsel besøger højst fire aktive listesider pr. kilde og genbruger kendte artikler fra `archive.json`. En fuld audit gennemtvinger kontrol af historiske ruter og sitemaps, men arkivet bliver kun erstattet, hvis kildekontrollerne ser plausible ud.

## Manuel fuld audit

1. Gå til **Actions → Opdater Ministerienyt**.
2. Vælg **Run workflow**.
3. Markér **Gennemtving fuld kontrol af alle 2026-arkiver og sitemaps**.
4. Vælg **Run workflow**.

Der sendes ikke e-mails eller oprettes issues af workflowet. Teknisk diagnostik gemmes i repositoryets JSON- og HTML-filer.

## Brugerfunktioner

- søgning, kildevalg og perioder på 7 eller 30 dage
- **Kun nye** baseret på den enkelte browsers sidste besøg
- lokale valg i **Mine ministerier**, filteret **Vis kun mine** og delbare links
- dubletsamling, når samme historie ligger hos et ministerium og Regeringen.dk
- samlet RSS-feed og installerbar webapp

## Vedvarende historik og status

`archive.json` bliver automatisk opdateret og committed af GitHub Actions. En fundet artikel bliver derfor i arkivet, selv hvis den senere forsvinder fra et ministeriums forside eller feed.

Forsiden viser kun tidspunktet for seneste opdatering. Kildestatus og konkrete kvalitetsbemærkninger findes under **Kilder og dækning** nederst på siden. `site/status.json` indeholder status fra seneste kørsel. Mere detaljerede filer som `diagnostics.json`, `diagnostics.html`, `alerts.json` og `source_audit.json` gemmes kun i repositoryet.

## Om fuldstændighed

Løsningen gemmer artikler fra 1. januar 2026, som kan opdages via de officielle RSS-feeds, arkivsider, paginering, Via Ritzau-kilder og sitemaps. Officielle sites kan ændre struktur eller undlade at eksponere ældre indhold; derfor kombinerer version 6.2 flere opdagelsesmetoder med automatiske selvtests og en månedlig fuld audit.

Se [TRIN-FOR-TRIN.md](TRIN-FOR-TRIN.md) for opdatering af et eksisterende repository.
