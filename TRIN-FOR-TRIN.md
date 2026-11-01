# Opdatér fra 8.0 til 8.0.1

Pakken indeholder seks filer, som alle skal ligge i repositoryets rod:

- `ministerier_nyheder.py`
- `regression_tests.py`
- `agency_sources.json`
- `agency_site_config.json`
- `README.md`
- `TRIN-FOR-TRIN.md`

1. Pak `Ministerienyt-8.0.1.zip` ud.
2. Åbn [repositoryets rod](https://github.com/JakobRud/Ministerienyt), og vælg **Add file → Upload files**.
3. Upload de seks filer direkte i roden, og commit dem samlet til `main`. Bevar alle andre filer.
4. Kontrollér under **Actions**, at `check`, `build` og `deploy` gennemføres. Efter udgivelse skal begge visninger vise **v8.0.1**.

Ingen ændring af workflow, GitHub-token eller cron-job.org er nødvendig. Eksisterende arkiver og indstillinger skal bevares.

## Hvad sker der med bemærkningen?

Dansk Dekommissionerings domæne havde DNS-udfald ved kontrollen den 1. november. Den nye version forsøger igen før udgivelsen og kommer automatisk tilbage til normal status ved en vellykket hentning. Vedvarende fejl vises stadig. Arkivets 11 artikler forsvinder ikke under udfaldet.

## Myndighedsændringer

Sundhedsdatastyrelsens kilde overgår automatisk til Arkiv fra 1. januar 2027 dansk tid. Ingen historiske artikler omdøbes eller slettes. Den nye organisations nyhedskilde samt Dyrevelfærdskontrollen skal først verificeres og derefter tilføjes i en kommende version. De planlagte ændringer og officielle kilder er dokumenteret i README og agency_site_config.json.
