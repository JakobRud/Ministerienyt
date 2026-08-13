# Ministerienyt

Ministerienyt samler nyheder og pressemeddelelser fra 21 danske ministeriers officielle hjemmesider på én offentlig webside.

Siden bliver automatisk genopbygget én gang i timen med GitHub Actions og udgivet gratis med GitHub Pages.

## Hvad du får

- Én kronologisk nyhedsstrøm fra alle ministerier
- Søgning på tværs af nyhederne
- Filter efter ministerium
- Direkte link til originalartiklen
- En kildeliste over alle 21 ministerier nederst på siden
- Et samlet RSS-feed på `/feed.xml`

## Kom i gang

Se `TRIN-FOR-TRIN.md` for den komplette opsætningsguide.

Hvis dit GitHub-brugernavn er `ditnavn`, og dit repository hedder `ministerienyt`, bliver adresserne normalt:

- Hjemmeside: `https://ditnavn.github.io/ministerienyt/`
- RSS: `https://ditnavn.github.io/ministerienyt/feed.xml`

## Filer

- `sources.json` – listen over de 21 ministerier og deres nyhedskilder
- `ministerier_nyheder.py` – henter nyheder og bygger HTML + RSS
- `.github/workflows/pages.yml` – opdaterer og publicerer siden automatisk
- `requirements.txt` – Python-afhængigheder
- `TRIN-FOR-TRIN.md` – installationsguide
