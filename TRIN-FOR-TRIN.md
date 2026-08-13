# Ministerienyt – komplet arkiv fra 2026

Ministerienyt samler officielle nyheder fra 21 danske ministerielle hjemmesider samt Regeringen.dk.

## Denne version gør følgende

- henter nyheder fra **1. januar 2026 og frem**
- har ingen grænse på 12 artikler pr. ministerium
- kombinerer RSS/Atom, HTML-arkiver, paginering og sitemaps
- gemmer fundne artikler i `archive.json`, så de ikke forsvinder ved senere opdateringer
- markerer artikler, der er kommet til siden siden brugerens sidste besøg
- har knappen **Kun nye**
- inkluderer Regeringen.dk
- medtager også de officielle 2026-arkiver på tidligere domæner, hvor ressortområder er flyttet (bl.a. trm.dk, skm.dk og digmin.dk)
- håndterer Beskæftigelses- og Ligestillingsministeriets redirectlinks og både nyheder og pressemeddelelser
- opdaterer automatisk én gang i timen

## Opdater eksisterende GitHub-repository

1. Pak ZIP-filen ud.
2. Åbn dit Ministerienyt-repository på GitHub.
3. Vælg **Add file → Upload files**.
4. Upload og erstat disse filer:
   - `ministerier_nyheder.py`
   - `sources.json`
   - `archive.json`
   - `requirements.txt`
   - `.github/workflows/pages.yml`
5. Klik **Commit changes** direkte til `main`.
6. Gå til **Actions → Opdater Ministerienyt** og følg den nye kørsel.

Første fulde kørsel skal gennemgå 2026-arkiverne og tager normalt cirka 5–15 minutter, men kan tage længere, hvis en officiel hjemmeside svarer langsomt. GitHub-workflowet har en tidsgrænse på 45 minutter. Senere timekørsler tager typisk 1–4 minutter, fordi kendte artikler genbruges fra arkivet.

## Sådan virker “Ny siden sidst”

Første besøg efter opdateringen etablerer et udgangspunkt. Fra det næste besøg markeres artikler, der ikke var på siden ved det foregående besøg. Oplysningen gemmes lokalt i browseren, så markeringen gælder separat for hver browser/enhed.

## Vedvarende historik

`archive.json` bliver automatisk opdateret og committed af GitHub Actions. Det betyder, at en artikel bliver i arkivet, selv hvis den senere forsvinder fra et ministeriums forside eller RSS-feed.

## Kildestatus

Nederst på hjemmesiden vises antallet af arkiverede artikler pr. kilde. `site/status.json` indeholder desuden teknisk status og eventuelle kildeadvarsler fra seneste kørsel.

## Om fuldstændighed

Løsningen har ingen vilkårlig artikelgrænse og gemmer alle artikler fra 1. januar 2026, som kan opdages via ministeriernes officielle RSS-feeds, arkivsider, paginering og sitemaps. Et officielt site kan dog ændre struktur eller undlade at eksponere ældre artikler. Kildestatus nederst på siden og `site/status.json` gør sådanne huller synlige.
