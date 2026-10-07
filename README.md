# oka-commons-bot

Source code of [OKA bot](https://commons.wikimedia.org/wiki/User:OKA_bot), which uploads freely licensed
collections from public institutions to Wikimedia Commons with their descriptive metadata. It is run by the
[Open Knowledge Association](https://meta.wikimedia.org/wiki/OKA) (operator:
[User:7804j](https://commons.wikimedia.org/wiki/User:7804j)); bot approval:
[Commons:Bots/Requests/OKA bot](https://commons.wikimedia.org/wiki/Commons:Bots/Requests/OKA_bot).

## First collection: swisstopo terrestrial images

About 57,000 scanned glass-plate photographs taken by the Swiss Federal Office of Topography for terrestrial
photogrammetry (1910s to early 1950s), published under the swisstopo
[terms of use for free geodata](https://www.swisstopo.admin.ch/en/terms-of-use-free-geodata-and-geoservices)
(`{{Attribution-Swisstopo}}` on Commons). Uploads go to
[Category:Terrestrial photographs by swisstopo](https://commons.wikimedia.org/wiki/Category:Terrestrial_photographs_by_swisstopo).

For each image the bot:

1. reads the swisstopo metadata (STAC API, per-image CSV/JSON, geo.admin.ch layer): date, survey area and
   station, camera, focal length, plate size, camera position and heading, image footprint, stereo partner,
   expert sheet, Smapshot ID;
2. builds the file page with `{{Photograph}}`, localized fields (Wikidata IDs, `{{Title}}`, `{{Technique}}`,
   `{{Size}}`), `{{Location}}` with heading, `{{Object location}}`, and categories (year, canton, municipality
   via Wikidata P771/P373);
3. downloads the cloud-optimized GeoTIFF and removes its reduced-resolution overviews **without re-encoding**
   (`strip_overviews.py`; `verify_strip.py` checks that every compressed tile and tag is identical and the
   decoded pixels are equal);
4. skips images already on Commons (SHA-1 of the source and inventory-number search) and sets aside any record
   that fails a check (template errors on a dry-run parse, heading not pointing at the photographed area, …);
5. uploads one file at a time, verifies the SHA-1 on Commons, retries transient server errors, and adds
   structured data (creator, inception, collection + inventory number, depicts, location of creation,
   material, source, licence, camera coordinates with heading, captions in en/de/fr);
6. after each batch, checks that every preview renders, links stereo partners by file name once both are
   uploaded, and refreshes `uploads.csv` (all uploaded files with their coordinates and Wikidata IDs).

## Running

```
pip install -r requirements.txt
export COMMONS_BOT_USER="OKA bot@<bot password name>"   # from Special:BotPasswords
export COMMONS_BOT_PASSWORD="..."
bash run_batch.sh batch002 1000                         # progress in batch002.log, last line starts with RESULT
```

On Windows the two variables may also be set as user environment variables. Credentials are never written
to this repository.

| Script | Purpose |
|---|---|
| `run_batch.sh` | one unattended batch: prepare, upload, check previews, link partners, refresh the list |
| `prepare_batch.py` | picks the next images, builds pages, downloads, strips, verifies, dry-runs |
| `swisstopo.py` | swisstopo metadata access and the page / structured-data builder |
| `upload.py` | uploads a prepared batch and writes structured data |
| `check_thumbs.py`, `link_partners.py`, `make_manifest.py` | post-batch checks, partner links, upload list |
| `edit_pages.py` | updates earlier uploads by the bot to the current format |

## Licence

Code: [MIT](LICENSE). The uploaded media keep the licence of their source institution.
