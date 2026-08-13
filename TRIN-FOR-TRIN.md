# Trin for trin: få Ministerienyt på nettet

Du behøver ikke selv have en server. GitHub kører opdateringen og hoster hjemmesiden.

## Trin 1 – opret en GitHub-konto

Hvis du ikke allerede har en konto, opret en på GitHub.

## Trin 2 – opret et repository

1. Log ind på GitHub.
2. Klik **New repository**.
3. Skriv `ministerienyt` som navn.
4. Vælg **Public**.
5. Klik **Create repository**.

Du kan vælge et andet navn, men så ændres den endelige webadresse tilsvarende.

## Trin 3 – pak ZIP-filen ud

Pak `ministerienyt-opdateret.zip` ud på din computer.

Du skal kunne se blandt andet:

- `ministerier_nyheder.py`
- `sources.json`
- `requirements.txt`
- `README.md`
- `TRIN-FOR-TRIN.md`
- mappen `.github`

Bemærk: `.github` kan være skjult på nogle computere. Den skal med, fordi den indeholder den automatiske opdatering.

## Trin 4 – upload filerne til GitHub

På forsiden af dit nye repository:

1. Vælg **Add file → Upload files**.
2. Træk alle filer og mapper fra den udpakkede pakke ind i browseren.
3. Kontroller især, at `.github/workflows/pages.yml` er kommet med.
4. Skriv fx `Første version af Ministerienyt` som commit-besked.
5. Klik **Commit changes**.

## Trin 5 – aktivér GitHub Pages

1. Gå til **Settings** i repositoryet.
2. Klik **Pages** i venstre side.
3. Find **Build and deployment**.
4. Under **Source** vælger du **GitHub Actions**.

## Trin 6 – kør den første opdatering

1. Gå tilbage til repositoryet.
2. Klik fanen **Actions**.
3. Klik **Opdater Ministerienyt** i venstre side.
4. Klik **Run workflow**.
5. Vælg `main`.
6. Klik **Run workflow** igen.

Workflowet henter nu nyheder, bygger hjemmesiden og udgiver den.

## Trin 7 – find din webadresse

Når workflowet er færdigt, kan du normalt åbne:

`https://DIT-GITHUB-NAVN.github.io/ministerienyt/`

Eksempel: Hvis dit GitHub-navn er `jenshansen`, bliver adressen:

`https://jenshansen.github.io/ministerienyt/`

Dit RSS-feed ligger på:

`https://DIT-GITHUB-NAVN.github.io/ministerienyt/feed.xml`

Du kan også finde den præcise Pages-adresse under **Settings → Pages**.

## Trin 8 – derefter passer siden sig selv

Workflowet er sat til at køre én gang i timen. Du skal derfor ikke manuelt opdatere hjemmesiden.

Ved hver kørsel:

1. hentes ministeriernes nyhedssider,
2. artiklerne sorteres efter dato,
3. hjemmesiden bygges på ny,
4. RSS-feedet bygges på ny,
5. den nye version publiceres på GitHub Pages.

## Hvad du ser på hjemmesiden

Forsiden viser alle fundne nyheder i én kronologisk strøm. Du kan søge efter fx `klima`, `Ukraine` eller `folkeskole`, og du kan filtrere på et bestemt ministerium.

Nederst står **Kilder**, hvor alle 21 ministerier vises med link til deres officielle hjemmeside.

## Hvis et ministerium ændrer hjemmeside

Ministerier ændrer indimellem navn eller webstruktur. Hvis en kilde en dag stopper med at levere nyheder, retter du den relevante post i `sources.json`.

De to vigtige ændringer efter regeringsdannelsen i juni 2026 er allerede indarbejdet:

- By-, Land- og Transportministeriet bruger `https://www.bltm.dk/`
- Skatte- og Vækstministeriet bruger `https://svmn.dk/`

## Hvis en GitHub Actions-kørsel fejler

Åbn **Actions → Opdater Ministerienyt → den røde kørsel**. Her kan du se præcis, hvilket trin der fejlede.

Hvis fejlen skyldes, at et ministerium har ændret sin hjemmeside, vil det normalt være nok at opdatere `sources.json`.
