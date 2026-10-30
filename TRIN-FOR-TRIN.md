# Opdatér til version 8.0

## Upload de otte filer samlet

Pakken indeholder syv filer i roden og én workflowfil:

- `ministerier_nyheder.py`
- `agency_sources.json` **(Fødevarestyrelsens adresse rettet)**
- `regression_tests.py`
- `update_gate.py` **(ny hjælpefil)**
- `README.md`
- `TRIN-FOR-TRIN.md`
- `EKSTERN-OPDATERING.md` **(ny vejledning)**
- `.github/workflows/pages.yml` **(skal erstatte den eksisterende workflowfil)**

1. Pak `Ministerienyt-8.0.zip` ud.
2. Åbn [repositoryets rod](https://github.com/JakobRud/Ministerienyt), og vælg **Add file → Upload files**.
3. Træk de syv rod-filer samt mappen **`.github`** fra pakken ind i uploadfeltet. Kontrollér, at GitHub viser workflowfilen på stien **`.github/workflows/pages.yml`**. Den må ikke ende som `pages.yml` i roden eller under en ekstra Ministerienyt-mappe.
4. Commit alle otte filer til `main` i samme commit, så hjælpefil og workflow følges ad.
5. Under **Actions** skal den nye kørsel vise tre job: **check → build → deploy**. Den første upload skal bygge og udgive, også selv om siden blev opdateret for få minutter siden.
6. Når udgivelsen er grøn, skal begge visninger vise **v8.0**. Åbn **Om siden og kontakt**, og kontrollér den nye tekst om forsinkelse og fuldstændighed.

Mapper med punktum kan være skjulte på nogle computere. Slå om nødvendigt visning af skjulte filer til. Alternativt kan [GitHubs webeditor](https://github.dev/JakobRud/Ministerienyt) bruges til at ændre alle otte filer og committe dem samlet.

Du skal ikke erstatte artikelarkiver, andre kildelister, statusfiler, site-konfiguration eller genererede HTML-filer.

## Aktivér den mere stabile udløser

Følg **EKSTERN-OPDATERING.md** efter upload. Den viser den præcise opsætning af én uafhængig tidsplan hos cron-job.org.

**Den eksterne tidsplan er ikke aktiveret af ZIP-filen.** Indtil den er sat op, bruger siden fortsat GitHubs eksisterende tidsplan og kan derfor stadig have de hidtidige lange pauser. GitHub-nøglen indtastes privat hos den eksterne tjeneste og må ikke uploades til repositoryet.

Efter opsætningen kan kørsler, der kun udfører `check` og derefter springer hentning over, være helt normale. Det betyder, at siden netop er opdateret. En reel crawl- eller udgivelsesfejl vil fortsat være synlig i Actions og den eksisterende statusvisning efter dens normale grænse.
