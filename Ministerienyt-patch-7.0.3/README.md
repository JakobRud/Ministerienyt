# Ministerienyt - patch 7.0.3

Denne pakke indeholder forbedringer til Styrelsesnyt: robust server-side scraping, bedre metadata-hÃ¥ndtering, de-duplication og fejlhÃ¥ndtering.

## Installation (hurtigt)
1. Pak zip ud.
2. KopiÃ©r filerne til dit repo (erstat eksisterende filer).
3. Ã…bn `.github/workflows/scrape-and-validate.yml` og indsÃ¦t dit eksisterende cron-udtryk i stedet for kommentaren:
   - Erstat `# PASTE_YOUR_EXISTING_CRON_EXPRESSION_HERE` med cron-linjen fra din nuvÃ¦rende workflow.
4. Commit og push til GitHub.
5. Trigger workflow manuelt fÃ¸rste gang via Actions eller vent pÃ¥ nÃ¦ste planlagte kÃ¸rsel.

## Hvordan scraperen finder kilder
- PrimÃ¦rt lÃ¦ser `scripts/sources-list.txt` (Ã©n URL per linje).
- Hvis `sources-list.txt` er tom, bruger scraper eksisterende `data/sources.json` som fallback.
- Scraper henter title, publisher (ogsÃ¥ fra og:site_name), date (hvis tilgÃ¦ngelig) og canonical URL.
- DÃ¸de links markeres med `"status":"dead"` og vises nederst pÃ¥ Styrelsesnyt-siden.

## Version
Denne patch sÃ¦tter versionsnummeret til **7.0.3**.

## BemÃ¦rkninger
- Hvis du vil bevare din nuvÃ¦rende liste af kilder, kopier dem ind i `scripts/sources-list.txt` fÃ¸r fÃ¸rste kÃ¸rsel.
- Hvis du Ã¸nsker at jeg laver en PR i stedet for at du uploader manuelt, sÃ¥ sig til.
