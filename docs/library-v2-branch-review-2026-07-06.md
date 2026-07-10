# Library v2 — Deep-Dive-Review des Branches `library-overhaul` (Stand 2026-07-06)

> **UPDATE 2026-07-07 — Fix-Pass abgeschlossen.** Alle Bugs B1–B9, alle mittleren
> Findings M1–M16 (außer M8/Job-Registry, bewusst Roadmap) und die Kleinigkeiten
> wurden behoben; zusätzlich implementiert: periodischer
> `lib2_discography_refresh`-Job (M4/TODO 2), Album-Type-Edit (Phase-D-Slice),
> Skip-Checks für Album-Grabs, API-Routen-Tests (`tests/library2/test_api_routes.py`).
> Details: `core/library2/STATUS.md` → „2026-07-07 review-fix pass".
> Bewusst offen (Roadmap): Monitor-Provenance, Playlists, breiteres Metadaten-Edit,
> Job-Registry (M8), Pfad-Scoping für Reorganize/Dedup.

Vollständige Prüfung aller Code-Änderungen des Branches gegen den Plan
(`~/.claude/plans/sharded-stargazing-clock.md`) und `core/library2/STATUS.md`.
Gelesen wurde **jede Zeile** der lib2-Kernmodule, der API, des Frontends und
aller Integrationspunkte (web_server, Repair-Worker, Watchlist-Scanner,
Import-Pipeline). Es wurden **keine Code-Änderungen** vorgenommen.

---

## 0. Scope-Klarstellung: Was ist eigentlich der Diff zu `dev`?

`git diff dev...library-overhaul` umfasst **~26.000 Zeilen in 179 Dateien** — das ist aber
irreführend, denn `dev` (merge-base `cd0279a4`) liegt weit hinter `main`. Der Diff enthält
deshalb drei Kategorien:

1. **Bereits gemergte Arbeit** (nicht Gegenstand dieses Reviews): Quality-Profiles
   (PR #974, gemergt), Discover-Adventurousness-Dial, Artist Web Graph, JioSaavn
   (Upstream), diverse Fixes. Diese verschwinden aus dem Diff, sobald `dev` von `main`
   nachzieht.
2. **Die eigentliche Library-v2-Arbeit**: die 8 Commits `7e8efcfd..be0c0658`,
   zusammen **~11.000 Zeilen in 60 Dateien** (`git diff f022e70f..HEAD`).
3. **Kleine geteilte Integrationsänderungen** (publish_date in den Download-Plugins,
   Repair-Job-Scope, Watchlist-Scanner-Hook).

Dieses Review konzentriert sich auf (2) und (3).

**Empfehlung fürs spätere PR:** gegen einen aktuellen `main`/`dev` rebasen oder den PR
explizit als „nur die 8 library-v2-Commits" schneiden, sonst reviewt der Upstream 15k
Zeilen fremder, bereits gemergter Arbeit mit.

---

## 1. Plan-Abgleich — was ist fertig, und stimmt es mit dem Plan überein?

### 1.1 Kern-Designregeln (die vier Nicht-Verhandelbaren)

| Regel | Status | Anmerkung |
|---|---|---|
| **Nie Media-Server-abhängig** (inkl. Artwork) | ⚠️ 95 % | Artwork-Subsystem sauber (embedded → Provider → Disk-Cache). **Aber:** EPs bekommen im Artist-Detail KEINE lokale Artwork-URL → Legacy-`thumb_url` (= Media-Server-URL) kann durchsickern (→ Bug B1). |
| **Monitoring ⇄ Watchlist/Wishlist via interne Calls** | ✅ | `_mirror_artist_watchlist` / `wishlist_mirror.py`, Commit-vor-Mirror-Ordnung überall korrekt eingehalten (SQLite-Lock-Gotcha). |
| **App-weite `quality_profiles`, nie Parallelkopie** | ✅ | `_migrate_lib2_profiles_to_app_wide` konvergiert Altbestände; jeder Mirror-Aufruf trägt `quality_profile_id`; Watchlist-Scanner nutzt `profile_lookup`. |
| **DB als Source of Truth, Datei-Location pro File** | ✅ | `lib2_track_files` mit eigenem Row-Lifecycle; move-file verschiebt nur den DB-Link, Disk unberührt. |

### 1.2 Phasen-Status

- **Phase A (Look/Feel, Artwork, Monitoring-Mirror): fertig.** Full-width-Route,
  globale Suchleiste ausgeblendet (`downloads.js` `_gsHidePages`), Artwork-Endpoint mit
  Thumb-Fastpath + Cache-Headern, Monitor-Toggles auf allen Ebenen.
- **Phase B (Interactive Search → Pipeline): fertig.** `interactive-search.tsx` mit
  source-aware Spalten (Grabs/Seeders/Slots), Age-Spalte (publish_date aus den
  torrent/usenet-Plugins), sortierbare Spalten, Profil-Preview-Badges
  („meets cutoff/acceptable/below profile", source-aware und bewusst nur informativ),
  Quality-/AcoustID-Check-Toggles inkl. Skip-Audit (`lib2_manual_skips`).
- **Phase C (Retag/Preview, Maintenance, Manual Import): weitgehend fertig.**
  `retag.py` (Diff + Batch-Write mit lib2-Artwork-Cache als Cover-Quelle, Multi-Artist
  über die Junction), `RetagModal` mit Checkboxen + Live-Progress; Maintenance-Modal mit
  per-Artist-Scope für 3 Jobs (`metadata_gap_filler`, `album_tag_consistency`,
  `library_retag` via `JobContext.scope`); Manual Import = Verweis auf die bestehende
  Import-Seite (Reuse; Autolink fängt die importierten Files, da `post_process_matched_download`
  → `record_download_provenance` → `link_download_into_library_v2`).
- **Phase D (Single↔Album, Manage Tracks, Edit, Delete): teilweise.**
  Fertig: Quality-Profiles-Zuweisung + Kaskade + Auto-Monitor bei Upgrade-Policy,
  Duplicates-API + Manage-Tracks-Modal (Unlink, manuelles Link, Move-file),
  Delete Artist/Album mit Confirm + Mirror-Rückbau (nie Files).
  **Fehlt:** allgemeines Metadaten-Edit (Artist/Album/Track-Felder); `edit` kann nur
  `monitor_new_items`. Delete-mit-Datei-Löschung (Lidarr-Checkbox) fehlt bewusst.
- **Phase E (Search Monitored / Auto-Sync, Playlists): NICHT plangemäß.**
  Der Plan definiert „Search Monitored" als *Wishlist-Processing + Watchlist-Scan
  triggern*. Implementiert ist stattdessen ein Blind-Auto-Grab (→ Bug B6).
  Playlists: nicht begonnen (war als letztes geplant — ok).
- **Discography / monitor_new_items / periodischer Upgrade-Scan** (nicht im
  Original-Plan, in STATUS.md nachgeführt): implementiert und größtenteils solide
  (origin='library'|'discography', Claim beim Import, Prune nur pristiner Rows,
  Erstexpansion monitored nie automatisch).

### 1.3 Bewusste, dokumentierte Abweichungen (in Ordnung, aber festhalten)

- **lib2_*-Tabellen werden für ALLE Installs angelegt** (unconditional in
  `music_database._initialize_database`), nicht nur bei aktiviertem Flag. Der
  Standalone-`quality-profiles`-Branch hatte extra verifiziert, dass ein Fresh-Install
  keine `lib2_*`-Tabellen bekommt — auf diesem Branch ist das (leere Tabellen, dormant)
  wieder anders. Vertretbar, sollte aber im PR-Text stehen.
- Artwork-Cache liegt unter `<db_dir>/lib2_artwork/` mit eigenem Endpoint statt (wie im
  Plan zuerst skizziert) im ImageCache/`/api/image-cache/<key>` — die STATUS.md-Variante
  ist die bessere (Lidarr-MediaCover-artig), Plan-Text ist hier einfach älter.

---

## 2. Gefundene Fehler

### 2.1 Hohe Priorität (echte Bugs / Planverstöße)

**B1 — EPs bekommen keine lokale Artwork-URL (Media-Server-Leak möglich).**
[api/library_v2.py:136](api/library_v2.py:136): `for entry in data.get("albums", []) + data.get("singles", [])`
— `data["eps"]` fehlt. EP-Einträge behalten das rohe `image_url` aus der DB. Für
Legacy-Importe ist das `albums.thumb_url` = Media-Server-URL (verstößt gegen
Designregel #1 und ist ohne Server tot); für Discography-Rows eine Provider-CDN-URL
(funktioniert, aber ungecacht). Fix: `+ data.get("eps", [])`.

**B2 — `_profile()` in Background-Threads fällt immer auf Profil 1 zurück.**
`get_current_profile_id` liest `g.profile_id` und wirft außerhalb des Request-Kontexts
(web_server.py:692ff) → Fallback 1. Der Import-Endpoint löst das korrekt („Resolve the
active profile OUTSIDE the thread", [api/library_v2.py:1015](api/library_v2.py:1015)) —
aber die Threads von **Bulk-Monitor** ([api/library_v2.py:546](api/library_v2.py:546))
und **Upgrade-Scan** ([api/library_v2.py:862](api/library_v2.py:862)) rufen
`_mirror_tracks_wishlist` → `_profile()` **im Thread** auf. Auf Multi-Profil-Installs
landen Wishlist-Mirrors dadurch im Wishlist-Scope von Profil 1 statt beim aktiven
Profil; das spätere Unmonitor (im Request-Kontext, richtiges Profil) findet die Rows
dann nicht → verwaiste Wishlist-Einträge. Fix analog zum Import: Profil vor
`threading.Thread(...)` auflösen und als Parameter durchreichen.

**B3 — Refresh invalidiert Thumbnails nicht.**
[api/library_v2.py:972-986](api/library_v2.py:972): `/refresh` löscht nur
`artwork_file` (`<kind>_<id>.jpg`), nicht `thumb_file` (`<kind>_<id>_t.jpg`). Der
Thumb-Fastpath ([api/library_v2.py:234](api/library_v2.py:234)) serviert danach das
**alte** Thumbnail, während das Vollbild neu aufgelöst wird — Karten/Listen zeigen
dauerhaft veraltete Cover. Fix: beide Dateien löschen (und im `force=1`-Pfad von
`build_artwork` den Thumb ebenfalls explizit unlinken, statt sich auf das Überschreiben
zu verlassen).

**B4 — Pfad-Auflösung inkonsistent: `scan.py`, `retag.py`, `lib2_skips_cleanup` nutzen rohe DB-Pfade.**
`artwork.py` löst Pfade korrekt über `resolve_library_file_path` (Path-Mapping,
Docker/Media-Server-Sicht) auf — aber:
- [core/library2/scan.py:58](core/library2/scan.py:58) prüft `os.path.exists(path)` roh
  → auf gemappten Setups zählt ALLES als „missing", der Quality-Rescan tut nichts;
- [core/library2/retag.py](core/library2/retag.py) gibt `row["file_path"]` direkt an
  `read_file_tags`/`write_tags_to_file` → Preview zeigt „No file"/Fehler, Write schlägt fehl;
- [core/repair_jobs/lib2_skips_cleanup.py:97](core/repair_jobs/lib2_skips_cleanup.py:97)
  hält dann jede Audit-Row für verwaist und **löscht den gesamten Skip-Audit**.

Die lib2-Pfade stammen 1:1 aus `tracks.file_path` (Legacy = Media-Server-Sicht), das
Problem ist also nicht theoretisch. Fix: einen gemeinsamen Resolver-Helper für alle
lib2-Dateizugriffe einziehen (siehe Vorschlag A1). Beim eigenen Setup fällt es nicht
auf, weil Container-Pfade dort direkt existieren — bei fremden Setups (Plex-Pfade,
Path-Mappings) bricht es.

**B5 — `expected_track_count`-Clobbering-Kette: Wishlist-Seeding kann Discography-Alben auf 1 Track stutzen.**
[core/library2/importer.py:664-684](core/library2/importer.py:664): Für Alben ohne
Files und ohne `legacy_album_id` setzt `seed_wishlist_tracks` `track_count` UND
`expected_track_count` auf `COUNT(lib2_tracks)`. Trifft ein Wishlist-Track per
`spotify_id`/Titel auf ein **Discography-Album** (Provider sagte z. B. 12 Tracks), wird
`expected_track_count` auf 1 geklemmt. Folge-Schaden: `_persist_tracklist_tracks`
trimmt die Provider-Tracklist auf `expected`
([core/library2/completeness.py:122-123](core/library2/completeness.py:122)) — beim
späteren Monitoring wird also nur **ein** Track materialisiert und gemirrort statt 12.
Fix-Idee: das Clamp nur auf Alben ohne `origin='discography'` anwenden (oder generell
nie verkleinern, nur `COALESCE`-Auffüllen; die ursprüngliche Intention „keine erfundenen
Track-N-Platzhalter" ließe sich stattdessen im Read-Pfad lösen).

**B6 — „Search Monitored" ist semantisch falsch implementiert (und potenziell gefährlich).**
[library-v2-page.tsx:1044-1072](webui/src/routes/library-v2/-ui/library-v2-page.tsx:1044):
Der Artist-Toolbar-Button (Tooltip: „Search monitored missing tracks") ruft
`autoGrabBest(artistName)` auf — d. h. eine Quellen-Suche nach dem **bloßen
Artistnamen**, deren bestbewertetes Ergebnis (ggf. ein beliebiges Album!) sofort
heruntergeladen wird. Der Plan (Phase E) definiert „Search Monitored" als Trigger von
`POST /api/wishlist/process` + Watchlist-Scan. Da alle monitored Tracks ohnehin in der
Wishlist gespiegelt sind, wäre genau das die korrekte und triviale Implementierung.
Gleiches Muster auf Album-Ebene („Search Monitored: <Album>" → Auto-Grab des besten
Album-Suchergebnisses, unabhängig davon, was fehlt oder monitored ist).

**B7 — Interactive Search: Grab-Status-Key stimmt für Album-Ergebnisse nicht.**
[interactive-search.tsx:341](webui/src/routes/library-v2/-ui/interactive-search.tsx:341)
schreibt den Status unter `${username}::${r.filename}`; die Zeile liest ihn über
`resultKey()` ([:153](webui/src/routes/library-v2/-ui/interactive-search.tsx:153)), das
für Alben `album_path ?? album_title` benutzt. Bei Album-Ergebnissen (ohne `filename`)
zeigt der Button daher nie „…/Grabbed ✓" und erlaubt Doppel-Grabs. Fix: `grab()` auf
`resultKey(r)` umstellen.

**B8 — Autolink lässt ein Album mit Datei unsichtbar, wenn es `origin='discography'` und `monitored=0` ist.**
`_find_or_create_album` ([core/library2/autolink.py:62](core/library2/autolink.py:62))
matcht bewusst bestehende Discography-Rows (gut, keine Duplikate) — setzt aber weder
`origin='library'` noch berücksichtigen `_ARTIST_STATS`
([core/library2/queries.py:27](core/library2/queries.py:27)) oder `visibleReleases`
([library-v2-page.tsx:947](webui/src/routes/library-v2/-ui/library-v2-page.tsx:947))
„hat Dateien" als Sichtbarkeitskriterium. Ein Download, der außerhalb der
lib2-Wanted-Loop startet (normale Suche, Playlist-Sync, Auto-Import) und auf eine
unmonitorte Discography-Row matcht, ist danach in „My Library" **unsichtbar** und zählt
nicht in den Index-Statistiken, obwohl die Datei da ist. Der Importer-Claim
(`_claim_discography_album`) setzt `origin='library'` korrekt — der Autolink-Pfad
sollte das genauso tun (oder die Sichtbarkeitsregel um `has files` erweitert werden,
dann aber an EINER Stelle, siehe A2).

**B9 — Profil-Zuweisung mit Upgrade-Policy re-monitored bewusst unmonitorte Tracks.**
[api/library_v2.py:379-402](api/library_v2.py:379): Beim Zuweisen eines
`until_top`/`until_cutoff`-Profils auf Artist/Album wird `monitored=1` auf **alle**
Tracks gesetzt — auch auf solche, die der User explizit abgewählt hat (z. B. die per
Manage-Tracks/`move_track_file` konsolidierte Single-Variante, die genau deshalb
unmonitored + wishlist-entfernt wurde). Ergebnis: die weggeräumte Variante wird wieder
„wanted" und ggf. erneut heruntergeladen. Vorschlag: nur Tracks re-monitoren, die nicht
Teil eines `canonical_track_id`-Paars mit datei-tragender Gegenseite sind — oder
mindestens einen `auto_monitor: false`-Body-Schalter anbieten.

### 2.2 Mittlere Priorität

**M1 — Artist-Split-Heuristik zerlegt Bandnamen.** `_LIST_SEP_RE`
([core/library2/importer.py:39](core/library2/importer.py:39)) splittet auch an `&`,
`and`, `x`, `+` → ein `track_artist` wie „Simon & Garfunkel" oder „Florence and the
Machine" erzeugt zwei/drei Geister-Artist-Rows (als `featured` verlinkt). Der primäre
Artist bleibt korrekt (kommt vom Album-Artist), aber die Junction und der Artist-Index
verschmutzen. Lidarr löst das über Provider-Credits statt String-Split. Mindestens: `&`/
`and` nur splitten, wenn kein Artist mit dem Gesamtnamen existiert (Resolver kennt die
Namen ja bereits).

**M2 — Totes Update im Wishlist-Seeding.**
[core/library2/importer.py:571](core/library2/importer.py:571):
`image_url = COALESCE(NULLIF(image_url, ''), image_url)` ist eine Tautologie — gemeint
war vermutlich, das Payload-Artist-Bild zu übernehmen; es wird nie gesetzt.

**M3 — `monitor_new_items`-Erkennung hängt an „existiert noch eine pristine Discography-Row".**
[core/library2/discography.py:141-149](core/library2/discography.py:141): `had_discography`
= mind. eine `origin='discography'`-Row. Hat der User alle Releases monitored (oder
wurden alle geclaimt), gilt die nächste Expansion wieder als „Erstexpansion" → neue
Releases werden NICHT auto-monitored. Robuster: expliziter Marker
(`lib2_artists.discography_synced_at`).

**M4 — `monitor_new_items` wird nur bei manuellem „Update Discography" durchgesetzt.**
Es gibt keinen periodischen Re-Expansion-Job (bewusst laut STATUS.md, da der
Watchlist-Scanner Neuerscheinungen eigenständig queued) — aber damit hat das
UI-Feld „Monitor new releases" für **nicht** gewatchlistete Artists faktisch keinen
automatischen Effekt. Entweder dokumentieren („greift bei Update Discography") oder
einen leichten wöchentlichen Re-Expansion-Job für monitored Artists mit
`monitor_new_items != 'none'` ergänzen.

**M5 — `quality_profile_id` DEFAULT 1 hart kodiert.** Schema-Defaults
([core/library2/schema.py:59,95,132](core/library2/schema.py:59)) und diverse
`or 1`-Fallbacks (autolink, completeness) zeigen auf Profil-ID 1. Wird Profil 1
gelöscht (die Profil-Verwaltung erlaubt das; Referenz-Cleanup NULLt nur
`wishlist_tracks`), zeigen neue lib2-Rows auf eine nicht existente ID. Die Read-Seite
ist fail-open (kein Profil → `meets_profile=True`, UI „No quality profile"), es knallt
also nicht — aber Zuweisungen wirken dann still nicht. Besser: Default NULL + Resolve
auf `is_default=1` beim Lesen/Mirroring.

**M6 — Missing-Slot-Platzhalter ignorieren Multi-Disc.**
[core/library2/queries.py:492-497](core/library2/queries.py:492): Slots werden nur als
`(disc 1, n)` generiert; bei Mehr-Disc-Alben mit `expected_track_count` über alles
entstehen falsche/kollidierende Platzhalternummern.

**M7 — Index-Statistiken zählen materialisierte, unmonitorte Provider-Tracks.**
`_ARTIST_STATS.track_count` zählt alle Junction-Tracks — auch fileless Rows, die durch
bloßes **Expandieren** eines unowned Releases (`?resolve=1`) entstehen. Reines Browsen
erhöht so die „missing"-Badge auf der Artist-Karte. Vorschlag: Zählung auf
`monitored=1 OR has_file` einschränken.

**M8 — Bulk-Job-Singleton.** `_job_state` ist EIN globales Dict für Bulk-Monitor,
Upgrade-Scan und Retag ([api/library_v2.py:38](api/library_v2.py:38)); `awaitBulkJob`
im Frontend beobachtet blind „den" Job. Zwei Browser-Tabs/Nutzer können sich
gegenseitig 409s und fremde Ergebnisse einfangen. Für ein Single-User-Feature ok,
sollte aber vor Multi-User-Nutzung eine Job-ID bekommen (siehe A3).

**M9 — `write_tags`/Preview kappen still bei 500 Tracks.** `MAX_TRACKS`
([core/library2/retag.py:23](core/library2/retag.py:23)) schneidet `_track_rows` ab; der
Job meldet `total=len(track_ids)`, verarbeitet aber max. 500. Die UI zeigt den Hinweis
nur im Preview-Fall (`truncated`), beim Write nicht.

**M10 — Provenance-Fallback macht Table-Scans pro Track.**
`_download_provenance_for_path` ([core/library2/queries.py:240](core/library2/queries.py:240))
feuert bei fehlenden Quality-Feldern LIKE-`%/fname`-Queries + mehrstufige
Titel/Artist/Album-Kandidaten gegen `track_downloads` — pro Track, pro
Album-Request. Bei großen Bibliotheken (viele Downloads, Alben mit 20+ Tracks) wird
`get_album` spürbar langsam. Abhilfe: beim Import/Scan einmalig in `lib2_track_files`
persistieren (Spalten existieren ja) statt request-zeitlich zu joinen; mindestens Index
auf `track_downloads(file_path)`.

**M11 — O(N)-Python-Scans über alle Artists.** `autolink._find_or_create_artist`
([core/library2/autolink.py:50](core/library2/autolink.py:50)) und
`profile_lookup` iterieren ALLE `lib2_artists` pro Aufruf (jeder fertige Download bzw.
jede Scanner-Queue-Entscheidung). Bei 10k+ Artists messbar. Abhilfe: normalisierte
Namensspalte (`name_norm`) mit Index, dann SQL-Lookup.

**M12 — Kein Debounce am Index-Suchfeld.**
[library-v2-page.tsx:771-780](webui/src/routes/library-v2/-ui/library-v2-page.tsx:771):
jede Taste = Navigation + Request (React-Query dedupliziert nur identische Keys).

**M13 — Artwork-Slow-Path ohne Dedup/Lock.** Nach Cache-Bust (Refresh) oder beim
Erstladen einer Seite mit 75 Karten laufen parallele `build_artwork`-Requests inkl.
Provider-Lookups gleichzeitig (Thundering Herd auf Deezer/CAA/iTunes). Precache mildert
das, ein per-Entity-Lock (oder 202+Retry) wäre robuster.

**M14 — `search.album`-Parameter ist toter Code.** Im Zod-Schema definiert
([-library-v2.types.ts:22](webui/src/routes/library-v2/-library-v2.types.ts:22)) und im
Routen-Kommentar beschrieben („album set → album/single detail"), aber `LibraryV2Page`
wertet nur `search.artist` aus. Entweder Album-Detail-Deep-Link implementieren oder
Param + Kommentar entfernen.

**M15 — `lib2_manual_skips`-Audit schreibt auch bei deaktiviertem Flag.**
[web_server.py:6861-6879](web_server.py:6861): Der Insert im `start_download`-Pfad ist
nicht auf `features.library_v2` gebedingt (Tabelle existiert ja immer). Harmlos, aber
inkonsistent mit „No-op wenn Flag aus". Außerdem greift das Audit nur im
Track-Zweig — Album-Grabs mit Skip-Checks werden nicht auditiert.

**M16 — `retag._track_rows`: `GROUP BY t.id` mit LEFT JOIN wählt eine willkürliche Datei.**
Bei Tracks mit mehreren `lib2_track_files`-Rows (möglich nach Move/Autolink+Import)
entscheidet SQLite willkürlich, welcher `file_path` getaggt wird.

### 2.3 Kleinigkeiten

- `lib2_list_artists`: `int(request.args.get("page", 1))` ohne try → `?page=abc` = 500
  statt 400 ([api/library_v2.py:100](api/library_v2.py:100)).
- History-Matching nur `lower(track_artist) = lower(name)` — Multi-Artist-Strings
  („A feat. B") matchen nicht; History wirkt dann leer.
- Delete Artist/Album räumt die Artwork-Cache-Dateien (`lib2_artwork/*.jpg`) nicht auf
  (verwaiste Dateien; IDs werden dank AUTOINCREMENT nicht wiederverwendet, also kein
  Falschbild-Risiko — nur Müll).
- `Search`-Query für titellose Missing-Rows wird zu „Artist Track 5" (Label-Fallback
  `Track N - missing` wird nur teilweise gestrippt).
- Deezer-Tracklist-Fallback (Suche nach Artist+Titel) kann eine falsche Edition
  treffen — best-effort, bewusst; als bekannte Grenze dokumentieren.
- „Interactive Search"/„Search Monitored" auf Artist-Ebene sind deaktiviert, solange der
  Artist nicht monitored ist — für eine reine *Suche* eine unnötige Hürde (Lidarr
  erlaubt Interactive Search immer).
- `reset=True`-Import löscht `lib2_manual_skips` bewusst nicht (Audit) — ok, aber
  nirgends dokumentiert.
- `importer.seed_wishlist_tracks` setzt `monitored=0` auf bestehende Artists; korrekt
  nur, weil `apply_monitoring_from_watchlist_wishlist` danach läuft — die Funktionen
  sind also reihenfolge-gekoppelt, ohne dass es irgendwo steht.

---

## 3. Was fehlt noch (konsolidierte TODO-Liste)

Aus Plan + STATUS.md + diesem Review, grob priorisiert:

1. **Bugfix-Runde B1–B9** (oben) — insbesondere B2 (Profil-Scope), B4 (Path-Resolver)
   und B6 (Search Monitored) vor einem PR.
2. **Search Monitored korrekt**: `POST /api/wishlist/process` (+ optional Watchlist-Scan)
   triggern statt Auto-Grab; Auto-Grab kann als eigener Button („Grab best") bleiben.
3. **Edit-Funktionalität (Phase D)**: Artist/Album/Track-Metadaten bearbeiten (der
   Plan verweist auf Reuse von `PUT /api/library/...`).
4. **Monitor-Provenance** (STATUS-TODO 1): Album-Monitoring, das Re-Importe unabhängig
   vom Track-Wishlist-Zustand überlebt (Provenance-/Mode-Spalte statt Ableitung).
5. **Periodische Discography-Re-Expansion** (STATUS-TODO 2 + M4) oder klare Doku, dass
   `monitor_new_items` nur bei manuellem Refresh greift.
6. **Artist-Scope für Reorganize/Dedup** (Pfad-basiertes Scoping, nicht SQL) — im
   Maintenance-Modal laufen diese Jobs derzeit ehrlich gekennzeichnet library-wide.
7. **API-Layer-Tests**: `tests/library2/` deckt die Core-Module gut ab (70 Tests), aber
   **keine** Flask-Routen-Tests für `api/library_v2.py` (Monitor-Kaskade,
   Profil-Kaskade+Auto-Monitor, Delete-Rückbau, Artwork-Fastpath wären lohnend).
8. **Playlists (Phase E, letzter Schritt)** — unbegonnen, plangemäß.
9. **Album-Detail-Deep-Link** (`search.album`) implementieren oder entfernen (M14).
10. **Browser-Klick-Verifikation** in Docker für die Phase-C/D-Flows (STATUS nennt
    einige Punkte nur code-/curl-verified).

---

## 4. Architektur-Beobachtungen & Verbesserungsvorschläge

**A1 — Ein gemeinsamer Datei-Resolver für lib2.** `artwork.py` hat `_resolve_abs`;
scan/retag/skips-cleanup/autolink (`os.path.getsize`) brauchen dieselbe Logik. Ein
`core/library2/paths.py::resolve(db_path, config_manager)` beseitigt B4 strukturell und
verhindert, dass der nächste neue Codepfad denselben Fehler macht.

**A2 — Sichtbarkeits-/„in library"-Regel an genau einer Stelle.** Die Regel „origin ==
'library' OR monitored" existiert dreimal unabhängig: SQL (`_ARTIST_STATS`), Python
(`get_artist._in_library`) und TypeScript (`visibleReleases`). B8 zeigt, wie die Kopien
auseinanderlaufen. Vorschlag: die API liefert ein berechnetes `in_library`-Flag pro
Release (SQL-seitig, inkl. `has files`), UI und Stats konsumieren nur noch dieses Feld.

**A3 — Job-Registry statt zweier Modul-globaler Dicts.** `_import_state`/`_job_state`
sind faktisch ein Ein-Slot-Scheduler mit Polling auf „den" Job. Ein Mini-Registry
(`{job_id: state}`, Endpoint `/jobs/<id>`) macht Bulk-Monitor, Retag und Upgrade-Scan
parallel möglich, beseitigt die Cross-Talk-Probleme (M8) und ist die Voraussetzung für
Multi-User. Alternativ: die Jobs gleich in den bestehenden RepairWorker hängen (der hat
Queue, Progress, Stop, UI).

**A4 — Wishlist→Autolink über `lib2_track_id` schließen.** `wishlist_mirror` legt
`_source_info.lib2_track_id` auf die Wishlist-Row — aber `autolink` matcht den fertigen
Download wieder per Namens-Normalisierung. Wenn der Download-Kontext die
`source_info` der Wishlist-Row bis in `record_download_provenance` trägt (prüfen!),
könnte der Autolink deterministisch auf genau die lib2-Track-Row linken, statt
heuristisch zu matchen — das eliminiert eine ganze Fehlerklasse (falsches Album,
Compilation-Zuordnung, Titelvarianten).

**A5 — Importer-Skalierung.** Der Importer arbeitet row-by-row mit vielen
Einzel-SELECTs (u. a. `COUNT(*)` pro Album, Junction-Delete+Insert pro Track). Für die
285-Track-Referenzbibliothek egal; für 100k-Track-Bibliotheken werden das Minuten im
Write-Lock. Wenn Fremdnutzer das Feature testen sollen: `executemany`-Batches,
vorgeladene Count-Maps, und Progress-Callback beibehalten.

**A6 — Legacy- vs. lib2-Datenbasis der Repair-Jobs explizit machen.** Die per-Artist
gescopten Jobs (Gap-Fill, Tag-Consistency, Library-Retag) scannen **Legacy-Tabellen**.
Solange der Legacy-Import die Quelle ist, deckungsgleich — aber autolink-erzeugte
lib2-Rows ohne Legacy-Pendant sieht keiner dieser Jobs. Mittelfristig (wenn lib2 die
führende Library wird) brauchen die Jobs eine lib2-Datenquelle oder lib2 einen
Rück-Sync. Sollte als bekannte Grenze in STATUS.md stehen.

**A7 — `until_top`-Wording im Code vereinheitlichen.** `is_upgrade_policy` akzeptiert
beide, `evaluate_file` behandelt `until_top` als Cutoff 0 — korrekt, aber der
Docstring/Kommentar von `lib2_upgrade_scan` („under its until_top quality profile")
und die API-Doku nennen nur eine Variante. Kosmetik, verhindert aber Verwirrung.

---

## 5. Positives (bewusst festgehalten)

- **Die vier Kern-Designregeln sind fast überall konsequent umgesetzt** — insbesondere
  die Commit-vor-Mirror-Reihenfolge (SQLite-Lock-Gotcha) ist an allen sechs Stellen
  richtig, inkl. Kommentar.
- **`wishlist_mirror` als geteilte Implementierung** (Button + Repair-Job) ist genau die
  richtige Abstraktion — die Queueing-Regeln können nicht driften.
- **Fail-open-Disziplin**: Autolink, Profile-Lookup, Artwork, Completeness — nichts
  davon kann die Pipeline oder den Request-Pfad crashen; überall `try/except` +
  Debug-Log statt Raise.
- **Der Extraktions-Schnitt zum `quality-profiles`-Branch** (eigene
  `core/quality/schema.py`, Live-Resolution statt Snapshot-Spalten) war sauber und
  zahlt sich hier aus: lib2 hängt nur noch am Pointer.
- **Ehrliche UI**: Buttons ohne Backend wurden entfernt statt als Dead-Placeholder
  gelassen; Maintenance-Modal kennzeichnet Scope ehrlich; Delete-Dialoge sagen explizit
  „Files on disk are not deleted".
- **Testabdeckung der Core-Module ist gut**: 70 lib2-Tests + Job-Scope-Tests, alle grün
  (lokal verifiziert, 2026-07-06); angrenzende Suiten (quality/imports/wishlist,
  971 Tests) ebenfalls grün.

---

## 6. Test- & Verifikationsstatus

| Suite | Ergebnis |
|---|---|
| `tests/library2` + `tests/repair/test_job_scope.py` | **70 passed** (lokal, 2026-07-06) |
| `tests/quality`, `tests/imports`, `tests/wishlist` | **971 passed** (lokal, 2026-07-06) |
| Flask-Routen `api/library_v2.py` | **keine Tests** (Lücke, siehe TODO 7) |
| Frontend | kein lokales Node; Typecheck via `docker build --target webui-builder` (laut STATUS zuletzt grün) |
| End-to-End | laut STATUS in Docker gegen die reale ~285-Track-Bibliothek verifiziert; Phase-C/D-Klick-Flows teilweise nur code-verified |

### Empfohlene manuelle Docker-Checks vor dem nächsten Merge
1. Multi-Profil-Install: Bulk-Monitor als Profil ≠ 1 → landet der Wishlist-Eintrag im
   richtigen Profil? (B2)
2. Setup mit Path-Mapping (Plex-Pfade in `tracks.file_path`): Refresh & Scan → werden
   Files geprobt oder alles „missing"? Retag-Preview → „No file"? (B4)
3. EP im Artist-Detail: lädt das Cover über `/api/library/v2/artwork/...`? (B1)
4. Refresh & Scan → zeigen die Karten-Thumbs danach das neue Cover? (B3)
5. Wishlist-Track auf ein unowned Discography-Album seeden → Album monitoren → werden
   alle Provider-Tracks materialisiert oder nur einer? (B5)
6. „Search Monitored" auf einem Artist mit vollständiger Bibliothek drücken → wird
   trotzdem etwas heruntergeladen? (B6)
