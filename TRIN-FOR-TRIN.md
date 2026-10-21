# Opdatér til version 7.5.4

1. Pak `Ministerienyt-7.5.4.zip` ud.
2. Åbn roden af [Ministerienyt-repositoryet](https://github.com/JakobRud/Ministerienyt), vælg **Add file → Upload files**, og upload alle fem filer fra pakken:

   - `ministerier_nyheder.py`
   - `agency_sources.json`
   - `regression_tests.py`
   - `README.md`
   - `TRIN-FOR-TRIN.md`

3. Commit til `main`. Den eksisterende Actions-kørsel bygger og udgiver siden.
4. Kontrollér, at regressionstests og udgivelse bliver grønne, og at footeren viser **v7.5.4**.
5. Under **Kilder og dækning** på Styrelsesnyt skal Banedanmark nu også hente via **Via Ritzau API**. Arkivet suppleres med pressemeddelelser fra Banedanmarks egen kanal.

**Vigtigt:** Banedanmarks hjemmeside var utilgængelig ved kontrollen 21. oktober. Opdateringen sikrer en ekstra fungerende presseindgang; den reparerer ikke bane.dk. Bemærkningen kan derfor stadig være synlig, indtil hjemmesiden fungerer igen, fordi nogle nyheder kun findes der. Eksisterende artikler bevares under udfaldet.

Du skal ikke erstatte artikelarkiver, statusfiler, konfiguration, workflow eller genererede HTML-filer. Opdateringsplanen bevares.
