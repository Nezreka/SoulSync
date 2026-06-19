# soulsync 2.7.4 — `dev` → `main`

patch release on top of 2.7.3. headline is **re-identify** — re-file an already-imported track under the right release without re-downloading it.

---

## what's new

### re-identify a track (#889)
filed a track under the wrong release (single vs ep vs album)? there's now a ⇄ button in the library Enhanced view that lets you fix it. search any configured source (tabs, defaults to your active one), see the same song across its single / ep / album with type badges, pick the right one, and soulsync re-files the file you already have under that release — correct year, in-album track number, and art. opt to replace the original entry or keep both.

built additively over 5 phases (hint store → import seam → multi-source search → modal → button), all riding the existing import pipeline so a no-hint import is byte-identical to before. and it can't lose your file: replace deletes the old entry only *after* the re-import lands, and never if you pick the release it's already in.

### cleaner libraries & imports
- **#890** — track titles no longer keep the "01 - " prefix from the filename when there's no embedded title tag (which made the real track read as a false "missing"). stripped conservatively so "7 Rings" / "1-800-273-8255" / "1979" are left alone.
- **#891** — a Library Reorganize now sweeps the leftover cover.jpg / .lrc / sidecars from the old folder so it actually empties, plus an opt-in "Remove Residual Files" toggle on the Empty Folder Cleaner for the image-only folders you already have.
- **Sokhi's batch** — same-album songs group under one canonical release id (no more split discographies / mixed cover art); a single can match its parent album; a mid-enrichment crash on an art-less file no longer leaves it untagged; and a sequel digit glued to a CJK title no longer matches the wrong album.

### quality & sources
- **#886** — AAC (.m4a) as an opt-in soulseek quality tier, ranked above mp3 / below flac. off by default; existing profiles unchanged until you enable it.
- **#887** — enrichment on Spotify Free now reads "Running (Spotify Free)" instead of wrongly showing "Not Authenticated".
- **#884** — NZBGet imports from the finished location, not the incomplete "….#NZBID" folder.
- **#885** — setting the timezone to Australia/Sydney no longer makes the cache-maintenance job loop every 5 seconds.

### polish
- the artist-detail header no longer bleeds the blurred artist photo behind it.

---

## tests
strictly additive across the board — every new behavior is opt-in or gated so default flows are unchanged. ~100 new tests this cycle (re-identify seam, title-strip danger cases, the shared residual-file classifier, aac tier, tz scheduler, spotify-free status). full imports / matching / reorganize / auto-import suites green, ruff clean.

## post-merge
- [ ] tag `v2.7.4` on `main`
- [ ] docker-publish with `version_tag: 2.7.4`
- [ ] discord announce (auto-fired by the workflow)
- [ ] reply on #889 / #890 / #891
