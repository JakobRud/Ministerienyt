# Opdatér til version 7.5.5

1. Pak `Ministerienyt-7.5.5.zip` ud.
2. Åbn roden af [Ministerienyt-repositoryet](https://github.com/JakobRud/Ministerienyt), vælg **Add file → Upload files**, og upload alle seks filer fra pakken:

   - `ministerier_nyheder.py`
   - `sources.json`
   - `agency_sources.json`
   - `regression_tests.py`
   - `README.md`
   - `TRIN-FOR-TRIN.md`

3. Commit til `main`. Den eksisterende Actions-kørsel bygger og udgiver begge visninger.
4. Kontrollér under **Actions**, at regressionstests og udgivelse bliver grønne, og at footeren viser **v7.5.5**.
5. Åbn **Kilder og dækning** på begge visninger. Efter en vellykket kørsel bør de gennemgåede bemærkninger være væk. Socialministeriet kan fortsat have en intern 403-fejl, men henter samtidig fra ministeriets egen Via Ritzau-kanal.

De lette opdateringer henter de aktuelle resultater først. Den eksisterende natlige dybere kontrol gennemgår resten af arkiverne. Hvis du ønsker en fuld gennemgang straks, kan du bruge **Actions → Opdater Ministerienyt og Styrelsesnyt → Run workflow** og vælge fuld audit. Det er valgfrit og tager længere tid.

Du skal ikke uploade eller erstatte artikelarkiver, statusfiler, site-konfiguration, workflow eller genererede HTML-filer. Rettelserne anvendes på de eksisterende arkiver ved næste kørsel. Antallet af kilder er fortsat 22 i Ministerienyt og 79 i Styrelsesnyt indtil den allerede planlagte myndighedssammenlægning i december.
