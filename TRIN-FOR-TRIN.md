# Opdatér til version 7.5.3

1. Pak `Ministerienyt-7.5.3.zip` ud.
2. Åbn roden af [Ministerienyt-repositoryet](https://github.com/JakobRud/Ministerienyt), vælg **Add file → Upload files**, og upload alle fem filer fra pakken:

   - `ministerier_nyheder.py`
   - `sources.json`
   - `regression_tests.py`
   - `README.md`
   - `TRIN-FOR-TRIN.md`

3. Commit til `main`. Den eksisterende Actions-kørsel bygger og udgiver siden.
4. Kontrollér, at regressionstests og udgivelse bliver grønne, og at footeren viser **v7.5.3**.
5. Åbn **Kilder og dækning** på Ministerienyt. Forsknings-, Uddannelses- og Digitaliseringsministeriet skal efter en vellykket kørsel hente fra **fudm.dk** uden bemærkningen om uventet domæne eller faldet kandidatantal.
6. Åbn en eksisterende artikel fra ministeriet: kildelinket skal nu pege på samme artikelsti på fudm.dk. De fire ældre historier fra Digitaliseringsministeriet beholder deres digmin.dk-links.

Pakken indeholder kun fem ændrede filer. Du skal ikke erstatte artikelarkiver, statusfiler, konfiguration, workflow eller genererede HTML-filer. Planen med lette tjek hver halve time fra 7.5.2 bevares.
