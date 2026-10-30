# Aktivér den uafhængige opdatering i version 8.0

Denne opsætning er nødvendig, hvis du vil have en udløser, der er uafhængig af GitHubs tidsplan. Upload af version 8.0 opretter ikke automatisk en konto eller et job hos en ekstern tjeneste.

GitHubs eksisterende tidsplan fortsætter som reserve. Den eksterne udløser beder blot GitHub starte det samme workflow. GitHub står fortsat for hentning og udgivelse; din pc behøver ikke være tændt.

## 1. Opret en begrænset GitHub-nøgle

Åbn [GitHubs fine-grained tokens](https://github.com/settings/personal-access-tokens/new).

- Navn: `Ministerienyt ekstern opdatering`.
- Resource owner: `JakobRud`.
- Repository access: **Only select repositories → Ministerienyt**.
- Repository permissions: **Actions → Read and write**. Metadata-læseadgang tilføjes automatisk.
- Vælg en udløbsdato, og notér den. Nøglen skal fornyes, når den udløber.

Kopiér nøglen direkte til næste trin. Den skal ikke sættes i repositoryet, workflowfilen, en URL eller denne vejledning og skal ikke sendes i chatten.

## 2. Opret jobbet hos cron-job.org

Opret en konto på [cron-job.org](https://cron-job.org/), og opret et nyt cronjob med følgende indstillinger:

| Felt | Værdi |
| --- | --- |
| Titel | Ministerienyt – ekstern opdatering |
| URL | `https://api.github.com/repos/JakobRud/Ministerienyt/actions/workflows/pages.yml/dispatches` |
| Tidszone | `Europe/Copenhagen` |
| Minutter | `7` og `37` |
| Timer | Alle timer fra `6` til og med `22` |
| Dage, måneder og ugedage | Alle |
| Request method | `POST` |
| Notifications | Slå mail og andre beskeder fra |

Tilføj disse headers i jobbets avancerede indstillinger:

| Header | Værdi |
| --- | --- |
| `Authorization` | `Bearer DIN_GITHUB_TOKEN` — erstat med nøglen fra trin 1 |
| `Accept` | `application/vnd.github+json` |
| `Content-Type` | `application/json` |
| `X-GitHub-Api-Version` | `2026-03-10` |

Request body skal være præcis:

```json
{"ref":"main","inputs":{"full_audit":false,"only_if_stale":true}}
```

Ingen HTTP basic authentication er nødvendig. Authorization-headeren bruges i stedet.

## 3. Test én gang

Upload først alle version 8.0-filer, herunder `.github/workflows/pages.yml`. Gem derefter jobbet, og brug tjenestens testfunktion.

GitHub skal svare med en successtatus (200 eller 204). Se derefter [Actions](https://github.com/JakobRud/Ministerienyt/actions): en kørsel med typen **workflow_dispatch** skal dukke op.

- Hvis begge visninger er opdateret inden for 20 minutter, består kun `check`. `build` og `deploy` springes over. Det er korrekt og skåner kilderne.
- Hvis mindst én visning er ældre, hentes og udgives begge visninger som normalt.
- En almindelig manuel **Run workflow** uden “only_if_stale” gennemtvinger en opdatering.
- Den dybere natlige kontrol og den månedlige audit springes aldrig over på grund af friskhedstjekket.

Du kan derefter lade jobbet køre kl. 06.07–22.37 dansk tid. Med de normale køretider er målet opdateringer omkring hver halve til hele time i dagtimerne. Det er ikke en garanti: udløseren, GitHub Actions og myndighedernes hjemmesider kan alle blive forsinkede eller have udfald.

## Ved fejl

- **401/403:** kontrollér nøglens udløb og at den har Actions-skriveadgang til netop Ministerienyt.
- **404/422:** kontrollér URL, at workflowfilen ligger på den rigtige sti på `main`, og at version 8.0 er uploadet samlet.
- **Succes hos cron-job.org, men gammel side:** åbn GitHub-kørslen og kontrollér `build` og `deploy`. En vellykket POST betyder kun, at GitHub accepterede udløseren.
- Ved nøglefornyelse ændres kun den private Authorization-header hos cron-job.org. GitHubs reserveplan virker fortsat, hvis den eksterne udløser stopper.

Officiel dokumentation: [GitHub workflow dispatch og nødvendige rettigheder](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event), [cron-job.org: POST og egne headers](https://cron-job.org/en/faq/) og [jobindstillinger](https://docs.cron-job.org/rest-api.html#detailedjob).
