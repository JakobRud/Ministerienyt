# Opdatér Ministerienyt til version 6.2

## 1. Pak leverancen ud

Pak `Ministerienyt-6.2.zip` ud på din computer. Mappestrukturen skal bevares, især `.github/workflows/pages.yml`.

## 2. Upload rodfilerne

Åbn dit Ministerienyt-repository på GitHub, vælg **Add file → Upload files**, og erstat:

- `ministerier_nyheder.py`
- `regression_tests.py`
- `requirements.txt`
- `archive.json`
- `README.md`
- `TRIN-FOR-TRIN.md`

Commit ændringerne direkte til `main`.

## 3. Upload workflowet

Åbn mappen `.github/workflows` i repositoryet, vælg **Add file → Upload files**, og erstat `pages.yml`. Commit igen til `main`.

## 4. Følg den automatiske kørsel

Gå til **Actions → Opdater Ministerienyt**. Et push til `main` starter workflowet. Det udfører regressionstests, genererer side, RSS, PWA-filer og `site/status.json`, opdaterer kvalitetsfilerne og udgiver GitHub Pages.

Den medfølgende `archive.json` er allerede schema 10 og indeholder de 10 artikler, som blev fundet under den fulde 6.1-audit.

## 5. Kør eventuelt en fuld audit

En almindelig kørsel er nok til selve opgraderingen. Hvis du vil verificere alle 2026-ruter med det samme:

1. Vælg **Run workflow** på Actions-siden.
2. Markér **Gennemtving fuld kontrol af alle 2026-arkiver og sitemaps**.
3. Start kørslen.

En fuld audit kan tage væsentligt længere end en almindelig kørsel. Den kører også automatisk den første dag i hver måned.

## 6. Kontrollér resultatet

Når workflowet er grønt:

- åbn [Ministerienyt](https://jakobrud.github.io/Ministerienyt/)
- kontrollér, at footeren viser `v6.2`
- prøv **Mine ministerier → Del visning** i en privat browser; favoritvalgene skal følge med linket
- åbn `https://jakobrud.github.io/Ministerienyt/status.json`; den skal returnere JSON
- kontrollér, at Actions ikke viser en ny fejl efter arkiv-committet

Der er ingen e-mail- eller issue-alarmer i workflowet. Interne advarsler ses i repositoryets `diagnostics.html`, `diagnostics.json`, `alerts.json` og `source_audit.json`.
