# Opdatér Ministerienyt til version 6.3

## 1. Pak leverancen ud

Pak `Ministerienyt-6.3.zip` ud på din computer. Mappestrukturen skal bevares, især `.github/workflows/pages.yml`.

## 2. Upload rodfilerne

Åbn dit Ministerienyt-repository på GitHub, vælg **Add file → Upload files**, og erstat:

- `ministerier_nyheder.py`
- `regression_tests.py`
- `site_config.json`
- `README.md`
- `TRIN-FOR-TRIN.md`

Commit ændringerne direkte til `main`.

## 3. Upload workflowet

Åbn mappen `.github/workflows` i repositoryet, vælg **Add file → Upload files**, og erstat `pages.yml`. Commit igen til `main`.

## 4. Følg den automatiske kørsel

Gå til **Actions → Opdater Ministerienyt**. Et push til `main` starter workflowet. Det udfører regressionstests, genererer side, RSS, PWA-filer og `site/status.json`, opdaterer kvalitetsfilerne og udgiver GitHub Pages.

Behold repositoryets eksisterende `archive.json` og kvalitetsfiler. De er nyere end leverancen og opdateres automatisk af workflowet.

## 5. Kør eventuelt en fuld audit

En almindelig kørsel er nok til selve opgraderingen. Hvis du vil verificere alle 2026-ruter med det samme:

1. Vælg **Run workflow** på Actions-siden.
2. Markér **Gennemtving fuld kontrol af alle 2026-arkiver og sitemaps**.
3. Start kørslen.

En fuld audit kan tage væsentligt længere end en almindelig kørsel. Den kører også automatisk den første dag i hver måned.

## 6. Kontrollér resultatet

Når workflowet er grønt:

- åbn [Ministerienyt](https://jakobrud.github.io/Ministerienyt/)
- kontrollér, at footeren viser `v6.3`
- åbn **Mine ministerier**, vælg mindst én kilde, aktivér **Vis kun mine**, og brug derefter **Del visning**; valgene skal følge med linket i en privat browser
- kontrollér, at kildestatus ikke står i toppen, og at eventuelle konkrete bemærkninger kan læses under **Kilder og dækning**
- åbn `https://jakobrud.github.io/Ministerienyt/status.json`; den skal returnere JSON
- kontrollér, at Actions ikke viser en ny fejl efter arkiv-committet

Der er ingen e-mail- eller issue-alarmer i workflowet. Interne advarsler ses i repositoryets `diagnostics.html`, `diagnostics.json`, `alerts.json` og `source_audit.json`.

Workflowet kører i dansk tid hver time kl. 06–18 samt kl. 21, 00 og 03. De almindelige kørsler er lette friskhedstjek, og kl. 03 køres en dybere kontrol. Det giver normalt nye artikler på siden inden for en time i dagtimerne, men GitHub kan stadig forsinke enkelte planlagte kørsler.
