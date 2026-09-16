# Opdatér til version 7.2.1

## 1. Pak leverancen ud

Pak `Ministerienyt-7.2.1.zip` ud.

## 2. Upload rodfilerne

Åbn roden af Ministerienyt-repositoryet på GitHub, vælg **Add file → Upload files**, og upload kun disse syv ændrede filer:

- `ministerier_nyheder.py`
- `regression_tests.py`
- `sources.json`
- `agency_sources.json`
- `erst_verified_archive.json`
- `README.md`
- `TRIN-FOR-TRIN.md`

Vælg at erstatte filer med samme navn. Upload ikke `archive.json`, `health.json`, `diagnostics.json` eller andre genererede statusfiler fra pakken; den aktuelle historik i GitHub skal bevares.

## 3. Lad workflow og Pages være urørt

Der er ingen ændring i `.github/workflows/pages.yml`, og du skal ikke ændre noget under **Settings → Pages**.

## 4. Commit ændringerne

Commit de uploadede filer direkte til `main`. En push-kørsel starter normalt automatisk.

## 5. Følg første kørsel

1. Gå til fanen **Actions**.
2. Åbn **Opdater Ministerienyt og Styrelsesnyt**.
3. Kontrollér, at trinnene med regressionstests, generering og Pages-udgivelse bliver grønne.

Version 7.2.1 kræver ikke en ny genopbygning af arkiverne. Efter den første kørsel skal FMI og Beredskabsstyrelsen stå uden timeout-bemærkninger. Rettelserne fra 7.1.4 bevares: Dansk Dekommissionering skal stå med **8 artikler** fra 2026, og Forsvaret/Forsvarskommandoen skal være uden timeout-bemærkning.

## 6. Kontrollér siderne

- Åbn Ministerienyts normale Pages-adresse.
- Kontrollér, at **Ministerienyt** og **Styrelsesnyt** står ved siden af hinanden øverst.
- Vælg **Styrelsesnyt**.
- Kontrollér søgning, kildefilter, **Mine myndigheder**, **Kun nye**, **Alle**, **I dag**, **3 dage**, **7 dage**, **30 dage** og **Kilder og dækning**.
- Gem fx `klima, Ukraine` under **Mine emner**, aktivér filteret, og kontrollér at valget også er tilgængeligt på den anden hovedside.
- Kontrollér, at kildelisten viser **74 kilder** og ministerområderne.
- Kontrollér, at kildelisten har kolonnen **Indhold**.
- Kontrollér, at FMI og Beredskabsstyrelsen står med **OK** uden timeout-bemærkninger.
- Kontrollér, at Skatteankestyrelsen, Havarikommissionen og Styrelsen for Undervisning og Kvalitet ikke længere står i kildelisten.

## 7. Lad de genererede filer blive liggende

Efter første vellykkede kørsel opretter workflowet blandt andet:

- `agency_archive.json`
- `agency_health.json`
- `agency_diagnostics.json` og `agency_diagnostics.html`
- `agency_source_state.json`
- `agency_alerts.json`
- `agency_source_audit.json`
- `agency_rejected_candidates.json`

De filer er normale drifts- og historikfiler og skal blive i repositoryet. Du behøver ikke redigere eller uploade dem manuelt ved senere kodeopdateringer.

## Hvis første kørsel fejler

Åbn det røde trin i Actions og læs den konkrete fejl. Start ikke flere parallelle kørsler; vent på den nyeste kørsel. Når den nyeste kørsel er grøn, er en ældre fejlet kørsel i sig selv ikke et problem.
