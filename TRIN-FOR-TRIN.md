# Opdatér til version 7.5.6

1. Pak `Ministerienyt-7.5.6.zip` ud.
2. Åbn roden af [Ministerienyt-repositoryet](https://github.com/JakobRud/Ministerienyt), vælg **Add file → Upload files**, og upload alle fem filer fra pakken:

   - `ministerier_nyheder.py`
   - `agency_sources.json`
   - `regression_tests.py`
   - `README.md`
   - `TRIN-FOR-TRIN.md`

3. Commit til `main`. Den eksisterende Actions-kørsel bygger og udgiver begge visninger.
4. Kontrollér, at regressionstests og udgivelse bliver grønne, og at footeren viser **v7.5.6**.
5. Under **Kilder og dækning** på Styrelsesnyt skal Folketingets Ombudsmand efter en vellykket kørsel hente via **GoBasic API** uden den tidligere bemærkning om nul kandidater. De tidligere historier bevares, og de manglende oktoberhistorier hentes.

De almindelige kørsler henter de aktuelle resultater først. Den natlige dybere kontrol gennemgår resten af årsarkivet. Du skal ikke erstatte artikelarkiver, statusfiler, site-konfiguration, sources.json, workflow eller genererede HTML-filer.
