# Ministerienyt og Styrelsesnyt 7.0.1

Version 7.0.1 er en kilderettelse til Styrelsesnyt. De 78 myndigheder, funktionerne og sidernes opbygning er uændrede.

## Rettet i version 7.0.1

- 16 forældede listeadresser eller artikelstier er opdateret til myndighedernes aktuelle officielle struktur.
- Banedanmarks flytning fra `banedanmark.dk` til `bane.dk` er håndteret uden at åbne for andre domæner.
- Rigspolitiet er fortsat afgrænset til centrale Rigspolitiet-nyheder, selv om indgangen nu er Rigspolitiets forside.
- DMI, KFST, Forsyningstilsynet, NFA, Hjemmeværnet, Skattestyrelsen, Skatteankestyrelsen, Finanstilsynet, Sundhedsstyrelsen, Sundhedsdatastyrelsen, Slots- og Kulturstyrelsen, Rigsarkivet og Ankestyrelsen bruger deres nye officielle indgange.
- Erhvervsstyrelsen og Danmarks Domstole har fået en ekstra officiel forside som skånsom reserveindgang. Der er ikke slået generel sitemap-crawling til.

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
