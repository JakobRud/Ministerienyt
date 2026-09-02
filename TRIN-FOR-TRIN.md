# Opdatér til version 7.0

## 1. Pak leverancen ud

Pak `Ministerienyt-7.0.zip` ud. Mappestrukturen skal bevares, især `.github/workflows/pages.yml`.

## 2. Upload rodfilerne

Åbn roden af Ministerienyt-repositoryet på GitHub, vælg **Add file → Upload files**, og upload disse filer:

- `ministerier_nyheder.py`
- `regression_tests.py`
- `requirements.txt`
- `sources.json`
- `agency_sources.json`
- `site_config.json`
- `agency_site_config.json`
- `README.md`
- `TRIN-FOR-TRIN.md`

Vælg at erstatte filer med samme navn. Upload ikke `archive.json`, `health.json`, `diagnostics.json` eller andre genererede statusfiler fra pakken; den aktuelle historik i GitHub skal bevares.

## 3. Erstat workflowet

Gå til `.github/workflows`, og erstat den eksisterende `pages.yml` med filen fra pakkens `.github/workflows`-mappe.

Der skal kun ligge ét aktivt Pages-workflow. Der er ikke behov for en ekstra `pages.yml` i repositoryets rod eller for at indsætte kode manuelt under **Settings → Pages**.

## 4. Commit ændringerne

Commit de uploadede filer direkte til `main`. En push-kørsel starter normalt automatisk.

## 5. Følg første kørsel

1. Gå til fanen **Actions**.
2. Åbn **Opdater Ministerienyt og Styrelsesnyt**.
3. Kontrollér, at trinnene med regressionstests, generering og Pages-udgivelse bliver grønne.

Den første 7.0-kørsel er et let friskhedstjek og opbygger Styrelsesnyt med de aktuelle nyheder fra de 78 officielle kilder. Den planlagte kørsel kl. 03 foretager den dybere gennemgang af 2026-arkiverne. Workflowets tidsgrænse er 90 minutter.

## 6. Kontrollér siderne

- Åbn Ministerienyts normale Pages-adresse.
- Kontrollér, at **Ministerienyt** og **Styrelsesnyt** står ved siden af hinanden øverst.
- Vælg **Styrelsesnyt**.
- Kontrollér søgning, kildefilter, **Mine myndigheder**, **Kun nye**, 7/30 dage og **Kilder og dækning**.
- Kontrollér, at kildelisten viser **78 kilder** og ministerområderne.

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
