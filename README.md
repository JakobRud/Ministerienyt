# Ministerienyt og Styrelsesnyt 7.1

Version 7.1 udvider periodefilteret og afslutter kildegennemgangen fra 7.0.3. Dynamiske myndighedsarkiver hentes via deres officielle læse-API'er, og falske artikelkandidater frasorteres før datokontrollen.

## Nyt i version 7.1

- Periodefilteret på både Ministerienyt og Styrelsesnyt har nu valgene **I dag**, **3 dage**, **7 dage**, **30 dage** og **Alle**.
- **I dag** følger kalenderdatoen i dansk tid. De øvrige periodevalg bruger samme løbende dagsfilter som hidtil.
- De nye periodevalg kan deles via **Del visning** og gendannes fra URL'en.
- DST og DMI følger nu pagineringen sekventielt, så direkte spring til sidste arkivside ikke bruger sidebudgettet. DMI's afsluttende punktum i datolinjen accepteres sikkert.
- Spillemyndigheden bruger det aktuelle officielle Next.js-indeks og kildens kanoniske værtsnavn.

## Rettet i version 7.0.3

- Styrelsesnyt understøtter officielle GoBasic-lister, Forsvarsministeriets ListPage-endpoint, Skatteforvaltningens Next.js-søge-API og Drupal JSON:API. Det reparerer en lang række kilder, der tidligere stod med `0`, selv om de havde offentliggjort nyheder i 2026.
- Officielle sitemaps er aktiveret selektivt på årssikre kilder, og synlige datolinjer efter artikeloverskriften læses kun på de konkret kontrollerede websites.
- Ruterne hos blandt andre Danmarks Statistik, Rigspolitiet, PET, STAR, Vejdirektoratet, Motorstyrelsen, Toldstyrelsen, Hjemmeværnet og Styrelsen for Patientsikkerhed er opdateret.
- Navigationssider hos blandt andre Politiklagemyndigheden, TET, Finanstilsynet, Lægemiddelstyrelsen og Slots- og Kulturstyrelsen frasorteres, så de ikke giver misvisende bemærkninger om manglende dato.
- Regeringen.dk genopbygges, så generelle organisationsmetadata ikke vises som artikelbeskrivelse, og den manglende sikre publiceringsdato kan læses fra artikelheaderen.
- Fuld audit stopper på kilder med lange arkiver, når en kontrolleret listeside er kommet forbi 1. januar 2026. Det fjerner misvisende bemærkninger om sidegrænsen uden at forkorte 2026-dækningen.
- Rettelserne fra 7.0.2 er med: Banedanmark genopbygges fra artikelsiderne, og en fungerende kilde med nul artikler viser kun `0` uden en særskilt bemærkning.

## Rettet i version 7.0.2

- Banedanmarks gemte fejlkombination af rubrik, link og manchet ryddes med en målrettet engangsgenopbygning. Fremover kontrolleres alle tre felter på selve artikelsiden.
- En teknisk fungerende kilde med nul arkiverede artikler viser blot `0` i kolonnen **Artikler**. Nul artikler udløser ikke længere teksten “Ingen artikler fundet fra kilden”. Reelle tekniske fejl og datakvalitetsproblemer vises fortsat som bemærkninger.

## Rettet i version 7.0.1

- Konkurrence- og Forbrugerstyrelsens nedlagte `/pressemeddelelser/`-indgang er erstattet af de aktuelle officielle presse- og arkivsider.
- Ankestyrelsens nye `/nyhedsarkiv` indlæses via hjemmesidens officielle GoBasic-læse-API, fordi artikellisten ikke findes i den første HTML-respons.
- Danmarks Domstoles aktuelle arkiv hentes fra det officielle Umbraco-origin, når det offentlige CDN afviser GitHub Actions. Artikellinks vises fortsat på `www.domstol.dk`.
- Erhvervsstyrelsen og Sundhedsstyrelsen bruger deres egne sider som hovedkilde og deres officielle Via Ritzau-presserum som reserve, hvis GitHub Actions mødes af henholdsvis 403 eller 429.
- Rigsarkivets komplette linkkort afgrænses nu enkeltvis, og det fejlbehæftede autoopdagede feed er koblet fra. Artikelrubrik og dato kontrolleres på hver artikelside, og den første kørsel genopbygger kun Rigsarkivets del af arkivet.
- Rigspolitiet og de øvrige ruter, der blev opdateret i den oprindelige 7.0.1-pakke, er fortsat bevaret og regressionstestet.

## Nyt i version 7.0

- **Ministerienyt** og **Styrelsesnyt** kan vælges ved siden af hinanden i topbjælken.
- Styrelsesnyt udgives på `/styrelsesnyt/` med eget arkiv, RSS-feed, status, diagnostik og PWA-filer.
- Styrelsesnyt har søgning, kildefilter, **Mine myndigheder**, **Kun nye**, perioder på 7 og 30 dage, delbar visning, kopiering af links og trinvis indlæsning.
- Brugerens læste artikler og valgte myndigheder gemmes separat fra Ministerienyt.
- Samme historie fra flere myndigheder samles i ét kort på Styrelsesnyt. En historie kan stadig fremgå én gang på både Ministerienyt og Styrelsesnyt, fordi siderne er selvstændige.
- De 78 kilder vises med ansvarligt ministerområde under **Kilder og dækning**.
- Rigspolitiets kilde er begrænset til centrale nyheder; lokale døgnrapporter medtages ikke.
- Delte officielle arkiver filtreres på udgiver, så SIRI og Udlændingestyrelsen ikke overtager hinandens artikler.
- Timekørslerne besøger højst to aktive listesider pr. myndighed. Den daglige dybe kontrol går højst 12 sider tilbage. Det holder belastningen af de officielle hjemmesider nede.

Version 7.0 indeholder også rettelsen fra 6.3.1, hvor Kulturministeriets synlige artikelmanchet prioriteres over en generel organisationstekst.

## De to hovedsider

| Side | URL | Kilder | Lokale valg | Datafiler |
| --- | --- | ---: | --- | --- |
| Ministerienyt | repositoryets Pages-forside | 22 | Mine ministerier | `archive.json`, `health.json` m.fl. |
| Styrelsesnyt | `/styrelsesnyt/` | 78 | Mine myndigheder | `agency_archive.json`, `agency_health.json` m.fl. |

De to crawlerkørsler bruger samme gennemprøvede program, men forskellige konfigurationer og arkiver.

## Drift

Workflowet kører i dansk tid hver time kl. 06–18 samt kl. 21, 00 og 03. De almindelige kørsler og kørslen efter en upload er lette friskhedstjek; kl. 03 foretages en dybere kontrol. Den første dag i hver måned køres en fuld audit.

Siden viser kun en diskret driftsbemærkning under **Kilder og dækning**, hvis to planlagte opdateringer i træk ser ud til at være udeblevet, plus 20 minutters afslutningstid. Det svarer normalt til godt to timer om dagen og op til godt seks timer om natten.

Workflowet sender ikke selv e-mails eller opretter issues. GitHubs egne Actions-mails styres under **Settings → Notifications → System → Actions** på GitHub.

## Vedvarende filer

GitHub Actions opretter og vedligeholder de genererede `agency_*`-filer efter første vellykkede kørsel. De skal ligge i repositoryet, når de først er oprettet, men de skal ikke uploades manuelt ved denne opgradering.

`archive.json` og `agency_archive.json` bevarer fundne artikler, selv hvis de senere forsvinder fra en officiel forside eller et feed. Diagnostikfilerne ligger kun i repositoryet; de vises ikke som topadvarsler på siderne.

## Manuel fuld audit

1. Gå til **Actions → Opdater Ministerienyt og Styrelsesnyt**.
2. Vælg **Run workflow**.
3. Markér **Gennemtving fuld kontrol af alle 2026-arkiver og sitemaps**.
4. Start kørslen.

Se [TRIN-FOR-TRIN.md](TRIN-FOR-TRIN.md) for den præcise opdatering.
