# Opdatér til version 7.5.2

1. Pak `Ministerienyt-7.5.2.zip` ud.
2. Åbn roden af [Ministerienyt-repositoryet](https://github.com/JakobRud/Ministerienyt), vælg **Add file → Upload files**, og upload disse fire filer:

   - `ministerier_nyheder.py`
   - `regression_tests.py`
   - `README.md`
   - `TRIN-FOR-TRIN.md`

3. Opdatér også den eksisterende **`.github/workflows/pages.yml`**. Åbn [workflowfilen på GitHub](https://github.com/JakobRud/Ministerienyt/blob/main/.github/workflows/pages.yml), vælg blyanten, og erstat hele indholdet med `pages.yml` fra pakkens `.github/workflows`-mappe. Commit til `main`. Filen skal ligge på denne sti, ikke i repositoryets rod. På computere, der skjuler mapper med punktum, kan du slå visning af skjulte filer til.
4. Kontrollér under **Actions**, at den seneste kørsel bliver grøn, og at begge visninger viser **v7.5.2** i footeren.
5. De lette tjek er nu planlagt kl. **xx.17 og xx.47 fra kl. 06 til 22**, samt kl. 00.17. Den dybere kontrol ligger kl. 03.17. Alle tider er danske, også efter skift til vintertid.

Pakken indeholder fem ændrede filer. Artikelarkiver, kildelister, konfiguration og genererede HTML-filer skal ikke erstattes.

## Hvis der stadig går mange timer mellem opdateringerne

Se [Actions-kørslerne](https://github.com/JakobRud/Ministerienyt/actions). GitHub kan forsinke eller udelade planlagte kørsler, selv om planen er korrekt. En kørsel med status `waiting` er endnu ikke nødvendigvis begyndt at hente nyheder.

Hvis en kørsel bliver ved med at stå i `waiting`, åbn den og læs GitHubs konkrete besked. Kontrollér derefter **Settings → Environments → github-pages**, at `main` er tilladt, og at der ikke er utilsigtede godkendelseskrav. De offentlige oplysninger ved kontrollen viste ingen reviewere eller ventetimer, så der er ikke påvist et sådant krav.

Du kan starte et almindeligt let tjek via **Actions → Opdater Ministerienyt og Styrelsesnyt → Run workflow**. Lad feltet for fuld audit være slået fra. Hvis også den manuelle kørsel venter, løser hyppigere tidsplaner ikke selve blokeringen.

Advarslen på siden vises stadig først efter mindst tre timer. Tidsstemplet angiver den faktiske sideopbygning, også når der ikke er fundet nye artikler.
