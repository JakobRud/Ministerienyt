# Opdatér Ministerienyt til version 6.3.1

## 1. Pak leverancen ud

Pak `Ministerienyt-6.3.1-KUM-rettelse.zip` ud på din computer.

## 2. Upload rodfilerne

Åbn roden af dit Ministerienyt-repository på GitHub, vælg **Add file → Upload files**, og erstat disse fem filer:

- `ministerier_nyheder.py`
- `regression_tests.py`
- `sources.json`
- `README.md`
- `TRIN-FOR-TRIN.md`

Commit ændringerne direkte til `main`.

## 3. Lad workflowet køre

Du skal ikke ændre noget i **Pages** eller uploade en workflowfil. Commit til `main` starter automatisk **Opdater Ministerienyt**.

## 4. Følg den automatiske kørsel

Gå til **Actions → Opdater Ministerienyt**. Et push til `main` starter workflowet. Det udfører regressionstests, genererer side, RSS, PWA-filer og `site/status.json`, opdaterer kvalitetsfilerne og udgiver GitHub Pages.

Behold repositoryets eksisterende `archive.json` og kvalitetsfiler. Ved denne kørsel genkontrolleres Kulturministeriets artikler én gang, hvorefter den forkerte beskrivelse erstattes automatisk. Arkivet og kvalitetsfilerne opdateres af workflowet.

## 5. Ingen fuld audit nødvendig

Den automatiske almindelige kørsel er nok. Du skal ikke markere **Gennemtving fuld kontrol af alle 2026-arkiver og sitemaps**.

## 6. Kontrollér resultatet

Når workflowet er grønt:

- åbn [Ministerienyt](https://jakobrud.github.io/Ministerienyt/)
- kontrollér, at footeren viser `v6.3.1`
- kontrollér, at KUM-artiklen **For få bruger kulturpasset: Nu tager kulturministeren konsekvensen** viser manchetten, der begynder med **Færre unge end forventet har søgt om at få et digitalt kulturpas**
- åbn **Mine ministerier**, vælg mindst én kilde, aktivér **Vis kun mine**, og brug derefter **Del visning**; valgene skal følge med linket i en privat browser
- kontrollér, at kildestatus ikke står i toppen, og at eventuelle konkrete bemærkninger kan læses under **Kilder og dækning**
- åbn `https://jakobrud.github.io/Ministerienyt/status.json`; den skal returnere JSON
- kontrollér, at Actions ikke viser en ny fejl efter arkiv-committet

Workflowet opretter ikke selv e-mails eller issues. For at slå GitHubs egne Actions-mails fra: åbn dine GitHub-notifikationsindstillinger, og vælg **System → Actions → Don't notify**. Interne advarsler ses i repositoryets `diagnostics.html`, `diagnostics.json`, `alerts.json` og `source_audit.json`.

Workflowet kører i dansk tid hver time kl. 06–18 samt kl. 21, 00 og 03. De almindelige kørsler er lette friskhedstjek, og kl. 03 køres en dybere kontrol. Det giver normalt nye artikler på siden inden for en time i dagtimerne, men GitHub kan stadig forsinke enkelte planlagte kørsler. Den diskrete driftsbemærkning vises først efter to udeblevne planlagte opdateringer plus 20 minutters afslutningstid.
