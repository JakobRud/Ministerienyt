# Opdatér til version 7.5.1

1. Pak `Ministerienyt-7.5.1.zip` ud.
2. Åbn roden af [Ministerienyt-repositoryet](https://github.com/JakobRud/Ministerienyt), vælg **Add file → Upload files**, og upload alle seks filer fra pakken:

   - `ministerier_nyheder.py`
   - `agency_sources.json`
   - `agency_site_config.json`
   - `regression_tests.py`
   - `README.md`
   - `TRIN-FOR-TRIN.md`

3. Commit ændringerne til `main`. Den eksisterende Actions-kørsel bygger og udgiver siden.
4. Kontrollér, at regressionstests og udgivelse bliver grønne, og at footeren viser **v7.5.1**.
5. Åbn **Kilder og dækning** på Styrelsesnyt. Medarbejder- og Kompetencestyrelsen skal efter en vellykket kørsel stå **OK**, og de eksisterende 63 artikler er bevaret.

Pakken indeholder kun de ændrede filer. Du skal ikke uploade eller erstatte artikelarkiver, statusfiler, workflow eller genererede HTML-filer.

## Sammenlægningen den 1. december 2026

Der er ikke behov for en ny upload den dag. Første planlagte sideopbygning den 1. december eller senere samler DMI og Klimadatastyrelsen til **DMI, Kort og Grunddata** i kildeliste, kildefilter og favoritmenu.

Indtil da vises begge myndigheder som hidtil. Historiske artikler bevares med deres oprindelige afsender. Begge arkiver hentes fortsat, og gamle favoritvalg og delte links virker efter skiftet. Den samlede liste går fra 79 til 78 myndigheder.

Den månedlige myndighedskontrol fortsætter. Når den nye myndigheds permanente nyhedsside er offentliggjort, bør crawlerens indgange kontrolleres.
