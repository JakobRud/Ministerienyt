# Opdatér fra 8.0.1 til 8.0.2

Pakken indeholder seks filer, som alle skal ligge i repositoryets rod:

- `ministerier_nyheder.py`
- `regression_tests.py`
- `sources.json`
- `agency_sources.json`
- `README.md`
- `TRIN-FOR-TRIN.md`

1. Pak `Ministerienyt-8.0.2.zip` ud.
2. Åbn [repositoryets rod](https://github.com/JakobRud/Ministerienyt), og vælg **Add file → Upload files**.
3. Upload de seks filer direkte i roden, og commit dem samlet til `main`. Bevar alle andre filer.
4. Kontrollér under **Actions**, at `check`, `build` og `deploy` gennemføres. Efter udgivelse skal begge visninger vise **v8.0.2**.

Workflow, token, cron-job.org og site-konfiguration skal ikke ændres. Artikelarkiverne skal bevares.

Rettelserne dækker DREAMs tomme nyhedsarkiv, en timeout ved Natur og Dyrevelfærd og en overstået timeout hos Færdselsstyrelsen. Fremtidige vedvarende fejl vil fortsat kunne give en reel bemærkning. Se README for kontrolresultater og begrænsninger.
