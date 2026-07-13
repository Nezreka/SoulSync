# Library Manager v2 — Konsolidierte Doku (Plan, Status, Review)

> Zusammengeführt am 2026-07-13 aus: `docs/library-v2-context.md`,
> `docs/library-v2-plan.md`, `docs/library-v2-branch-review-2026-07-06.md`,
> `core/library2/STATUS.md` (alle vier danach gelöscht). Diese Datei ist jetzt
> die einzige Quelle für Library-v2-Kontext, -Plan, -Status und -Review-Historie.
> Letzter inhaltlicher Stand der Quellen: 2026-07-12 (Phase 4/5 Acquisition,
> LIB2-011 Findings); am 2026-07-13 ergänzt um Abschnitt 4.5 (Main-Pipeline-
> Hardening-Split, unabhängig von Library v2 vorzuziehen).

Opt-in, Lidarr-style Library-Manager auf SoulSyncs eigener
Such-/Download-/Processing-/Tagging-Pipeline. Gated hinter
`features.library_v2`; die Legacy-Library bleibt unangetastet.
Code: `core/library2/`, `api/library_v2.py`, `webui/src/routes/library-v2/`,
Tests: `tests/library2/`.

---

## 1. Nicht verhandelbare Designregeln (Core principles — do not break)

- **NIE Media-Server-abhängig** (Plex/Jellyfin/Navidrome) — auch nicht für
  Artwork. Artwork = Embedded-Cover aus der Datei (primär) → Metadata-Provider
  (Fallback) → Disk-Cache `<db_dir>/lib2_artwork/`, serviert via
  `/api/library/v2/artwork/<kind>/<id>[?size=thumb]`.
- **Monitoring spiegelt die Bestandssysteme** über interne DB-Calls:
  Artist-Monitor ⇄ Watchlist; Album/Single/Track-Monitor ⇄ Wishlist. Ein Artist
  ist NUR monitored, wenn er auf der Watchlist steht; ein gewishlisteter Song
  monitored nie den ganzen Artist oder das Parent-Release. Track-Monitoring muss
  einen erfolgreichen Download überleben, wenn es für Upgrades gebraucht wird.
- **Quality-Profile = die app-weite `quality_profiles`-Tabelle**, nie eine
  Parallelkopie. Jeder Mirror-Aufruf trägt `quality_profile_id`; jede
  Pipeline-Stufe löst das Profil LIVE über
  `core/quality/selection.load_profile_by_id` auf.
- **DB ist Source of Truth**; jede Datei-Location liegt pro File in
  `lib2_track_files`.
- **SoulSync-Funktionen wiederverwenden**, nicht neu erfinden
  (Suche/Download/Tagging/Repair/Quality) — siehe „Reused assets"-Index
  (Abschnitt 6).

### Invarianten aus dem Fix-Pass 2026-07-07 (beim Weiterbauen nicht brechen)

- **Jeder** lib2-Dateizugriff läuft über `core/library2/paths.resolve_lib2_path`
  (gespeicherte Pfade sind die Media-Server-Sicht; roher `os.path.exists` bricht
  path-gemappte Setups).
- Background-Threads dürfen **nie** `_profile()` aufrufen — das aktive
  Nutzerprofil im Request-Kontext auflösen und explizit in den Thread reichen
  (sonst stiller Fallback auf Profil 1).
- SQLite-Lock-Regel: das `lib2_*`-Flag-Update committen und den Write-Lock
  freigeben, **bevor** Watchlist-/Wishlist-Methoden laufen (die öffnen eigene
  Connections).
- Bulk-Re-Monitor-Pfade wenden `_NOT_CONSOLIDATED_SQL` an (`api/library_v2.py`):
  Tracks, deren Datei bewusst zur kanonischen Duplikat-Seite verschoben wurde,
  werden nicht wieder „wanted".
- „Search Monitored" = `POST /api/wishlist/process` (nie Blind-Auto-Grab).
- Eine Datei an einem `origin='discography'`-Album ⇒ Origin wird zu
  `'library'` (Sichtbarkeitsregel „My Library" = `origin='library'` ODER
  `monitored`).
- `monitor_new_items`-Enforcement: Erstexpansion monitored nie automatisch; die
  Re-Expansions-Erkennung hängt am Marker
  `lib2_artists.discography_synced_at` (nicht an übrig gebliebenen pristinen
  Provider-Rows).
- Profil-IDs nie hart auf 1 — Fallbacks über
  `core/library2/profile_lookup.default_quality_profile_id`.

### Umgebung / Verifikation

- Verifizieren nur via Docker: `docker build -t soulsync:dev .` mit realer
  Config/DB-Kopie + gemounteter Musik; Feature-Flag `features.library_v2=true`.
  Frontend-Typecheck: `docker build --target webui-builder`.
- Pure-Python-Tests laufen ohne App-Stack: `pytest tests/library2` (inkl.
  Flask-Routen-Tests in `test_api_routes.py`).
- Windows+Docker-Gotcha: nie mit einem Host-`sqlite3` auf die live gebundene
  Container-DB lesen (kann die laufende App locken).

**Run / verify (lokal):**
```powershell
.venv\Scripts\python -m pytest tests
Set-Location webui
npm test
npm run check
npm run build
```
Alternativ im Container:
```
docker build -t soulsync:dev .
# run with the user's real config+DB copy + music mounted (covers come from embedded art):
#   -v <config>:/app/config  -v <data>:/app/data  -v <Music>:/music:ro
# set features.library_v2=true (in DB metadata app_config OR config.json)
```

---

## 2. Aktueller Stand in einem Satz

Milestone 1 (Foundation) bis Phase C sind fertig und in Docker gegen die reale
~285-Track-Bibliothek verifiziert; Phase D ist teilweise fertig (Quality-Profile
+ Manage Tracks + Delete, aber kein breites Metadaten-Edit); die serverseitige
Acquisition/Decision-Architektur (Phase 4/5, „LIB2-011") ist der aktuell aktive
Baustein und hat einen eigenen Findings-Katalog (Abschnitt 5.4), der vor
weiteren Acquisition-Features zuerst geschlossen werden muss. Phase E
(Playlists) ist unbegonnen.

---

## 3. DONE & verifiziert (in Docker gegen die reale ~285-Track-Library)

### Foundation + Read-UI (Milestone 1)
- Schema `core/library2/schema.py` (`lib2_*`): artists/albums/tracks +
  Multi-Artist-Junctions + `lib2_track_files` (DB-Row↔File),
  `lib2_quality_profiles` (früh, später migriert — siehe Abschnitt 4),
  `lib2_manual_skips`. Idempotente, additive Column-Migrationen.
- Importer `importer.py`: legacy→v2, Multi-Artist-Split (`feat./&/x`),
  Single-vs-Album-Link (`canonical_track_id`), `expected_track_count`,
  Monitoring aus Watchlist/Wishlist.
- Read-API `api/library_v2.py` + `queries.py`: Artists-Index (Stats),
  Artist-Detail (Albums/Singles gruppiert), Album-Detail (Track-Tabelle).
- React-Route `webui/src/routes/library-v2/`: full-width, Card-/Table-Views,
  Filter, Lidarr-Style expandable Album-Blocks (inline Track-Tabellen),
  Monitor-Toggles.

### Artwork (media-server-unabhängig)
- `artwork.py`: Embedded Cover aus der Datei (`extract_embedded_art`) →
  Provider-Fallback (`get_artist_image_url` / `art_lookup`) → Disk-Cache unter
  `<db_dir>/lib2_artwork/`, **Thumbnails** (Pillow) + Short-Circuit-Static-Serve
  via `/api/library/v2/artwork/<kind>/<id>[?size=thumb]`. Background-Precache
  nach Import.

### Missing Tracks
- `completeness.py`: kanonische Tracklist holen (Spotify-ID → Deezer-Suche),
  gecacht in `lib2_albums.tracklist_json`; Provider-Tracklist-Einträge werden
  als fileless `lib2_tracks`-Rows persistiert, sodass Missing Tracks echte
  Titel **und** Monitor-Buttons haben.
- Album/Single/Track-Monitor-Toggles spiegeln Missing Rows auch ohne
  Spotify-ID in die Legacy-Wishlist, via stabiler `lib2-track:<id>`-Keys.
  Wishlist-`source_info` trägt das zugewiesene Library-v2-Quality-Profil,
  damit der nächste Worker Per-Item-Quality-Settings respektiert.

### Interactive Search → Download (Phase B)
- Reused `/api/search` (Multi-Source, konfigurierte Prioritäten) +
  `/api/download`.
- Modal `interactive-search.tsx`: **source-aware** Ergebnisse
  (Source/Title/Artist/Quality/Size/Availability) — Usenet zeigt Grabs,
  Soulseek zeigt Slots/Queue. Quality- + AcoustID-Check-Toggles (`skip_acoustid`
  → Pipeline `_skip_quarantine_check`).
- Nur **Interactive Search** öffnet das Fenster; **Search/Grab** grabbt das
  beste Ergebnis automatisch (Status-Banner).
- Usenet-/Torrent-Plugins geben `publish_date` in `_source_metadata` →
  **Age**-Spalte ("3d"/"8mo"/"2.1y", Tooltip = Rohdatum). Alle Spalten
  sortierbar (source/title/quality/size/age/availability), Default-Sort
  quality-desc mit Size-Tiebreak. Availability bleibt source-aware
  (grabs vs. seeders vs. slots/queue); Source-Badges nach Familie eingefärbt
  (usenet/torrent/streaming/p2p).
- **Profile-Preview-Badges** (Lidarrs Rejection-Hints): jedes Ergebnis wird
  gegen die Ranked Targets der Ziel-Entity gemessen → „meets cutoff" /
  „acceptable" / „below profile". Source-aware: Facts, die eine Source nicht
  liefert, lassen ein Target nie fehlschlagen; Hi-Res-Targets brauchen
  positive Bit-Depth-Evidenz. Rein informativ — der echte Quality-Check der
  Pipeline bleibt beim Import maßgeblich.

### Quality-Profiles — app-weit, pipeline-enforced (Phase D M1+M2)
- Library v2 nutzt die **app-weite `quality_profiles`-Tabelle** direkt
  (Settings → Quality verwaltet sie; `core/quality/selection.load_profile_by_id`
  löst sie live in jeder Pipeline-Stufe auf). Die frühere parallele
  `lib2_quality_profiles`-Tabelle wird beim Start wegmigriert
  (`_migrate_lib2_profiles_to_app_wide`: Remap by Name, Tabelle droppen).
- **Assignments erreichen die Pipeline**: der Wishlist-Mirror gibt
  `add_to_wishlist(quality_profile_id=…)` mit, sodass „dieser Artist muss
  Profil X erfüllen" von den echten Such-/Import-Entscheidungen durchgesetzt
  wird, nicht nur angezeigt. Der Watchlist-Scanner-New-Release-Queueing schaut
  ebenfalls das lib2-Per-Artist-Profil nach (`profile_lookup.py`, fail-open auf
  Default).
- Per-Track-Evaluation `quality_eval.py` (reused `core/quality`):
  `meets_profile` / `upgrade_candidate` unter Beachtung von `upgrade_policy`
  **inkl. `until_cutoff` + `upgrade_cutoff_index`** (Lidarr-Style Cutoff;
  `until_top` = Legacy-Alias). UI: Profile-Picker-Modal (labelt den Cutoff) +
  Per-Track-Badges.

### Skip-Audit
- `lib2_manual_skips`: protokolliert user-initiierte Check-Skips
  (acoustid/quality) bei manuellen Downloads, damit spätere Cleanup-/Repair-Jobs
  den Override respektieren.

### Discography — alle Releases eines Artists (Lidarr-Style)
- `discography.py`: `expand_artist_discography` holt den vollen
  Provider-Katalog (`core/metadata/discography.get_artist_detail_discography` —
  Source-Priority mit Fallback) und persistiert jedes Release als
  `lib2_albums`-Row mit `origin='discography'`, `monitored=0`. Bestehende
  Releases werden gematcht (Provider-ID → normalisierter Titel,
  Single-vs-Release-Bucket) und in-place angereichert; verschwundene pristine
  Provider-Rows werden geprunt (monitored/tracked Rows überleben).
- Der Importer claimt Discography-Rows, wenn später Files ankommen
  (`_claim_discography_album`) — eine Release-Identität, keine Duplikate;
  Monitor-State bleibt erhalten.
- UI: Artist-Detail hat einen **My Library / All Releases**-Toggle
  (URL-Param `releases`), einen **EPs**-Bereich, per-Section
  **Monitor all / Unmonitor all** (Background-Bulk-Job `/releases/monitor` +
  `/jobs/status`-Polling), einen **Update Discography**-Toolbar-Button, und
  „not in library"-Badges. Der erste Wechsel zu All Releases fetcht
  automatisch.
- Monitoring eines unowned Release materialisiert zuerst dessen
  Provider-Tracklist (`resolve_tracklist`), sodass echte, monitorable
  Track-Rows in die Wishlist gemirrort werden; Expandieren macht dasselbe via
  `GET /albums/<id>?resolve=1`.

### Provider Snapshots und typed Refresh Boundary (Phase 3)
- `library_provider_snapshots` speichert normalisierte Provider-Payloads pro
  Entity/Scope mit Completeness, Cursor/Page-Count, Parser-Version,
  ETag/Version und stabilem Hash. Entity-Delete-Trigger verhindern verwaiste
  Snapshots.
- Discography- und Spotify/Deezer-Tracklists queren eine typed Adapter-Boundary
  vor der Library-v2-Persistenz. Provider-IDs werden exakt gematcht und
  strukturell gemerged; partielle Discography-Snapshots prunen nie Releases.
- Tracklist-Snapshots sind an die gewählte Default-ReleaseEdition und deren
  externe IDs gebunden. Ein Edition-/Provider-Wechsel invalidiert den alten
  Cache auch wenn der Ersatz-Provider temporär nicht erreichbar ist; Legacy
  Caches werden einmalig mit explizitem `legacy-cache`-Provenance markiert.

### Refresh & Scan liest echte File-Tags
- `scan.py`: `rescan_files` probet Files mit
  `core/imports/file_ops.probe_audio_quality` (mutagen Ground-Truth) →
  `lib2_track_files.sample_rate/bit_depth/bitrate/format/size` +
  `quality_tier`. Verdrahtet in `/refresh` (Artist-/Album-Scope). Fehlende
  Pfade werden geskippt (Docker Bind-Mounts), nie als gelöscht behandelt.

### Auto-Link neuer Downloads in lib2
- `autolink.py`: Post-Processing-Hook (aufgerufen von
  `core/imports/side_effects.record_download_provenance`) verlinkt jede fertige
  Download-Datei in lib2 — matcht bestehende Artist/Album/Track-Rows
  (inkl. fileless Wanted-Rows, flippt sie missing→present), erstellt Rows nur
  bei echtem Neuzugang, probet echte Qualität. Gated auf
  `features.library_v2`; wirft nie in die Pipeline.

### lib2-aware Upgrade-Scan — manuell UND periodisch
- Geteilte Implementierung `wishlist_mirror.py` (Payload-Build,
  Wishlist Add/Remove mit Per-Item `quality_profile_id`,
  Upgrade-Candidate-Selection), genutzt von: `POST /api/library/v2/upgrade-scan`
  (der „Search Upgrades"-Button) und dem **`lib2_upgrade_scan`-Repair-Job**
  (registriert, default-off, 24h-Kadenz) — unter Stats → Repair-Jobs aktivieren
  und Upgrades laufen weiter, ohne dass etwas gedrückt werden muss.

### monitor_new_items-Enforcement
- Bei einer *Re*-Expansion eines monitored Artists mit `monitor_new_items`
  'all'/'new' werden neu ENTDECKTE Releases auto-monitored: der
  Discography-Endpoint materialisiert deren Tracklists und mirrort sie in die
  Wishlist. Die ERSTE Expansion auto-monitored nie (das würde den ganzen
  Backkatalog mit einem Klick queuen).

### Manage Tracks (Phase D, erste Slice)
- `GET /api/library/v2/artists/<id>/duplicates`: Single↔Album-Duplikat-Paare
  aus den `canonical_track_id`-Links des Importers, jede Seite mit
  File-Quality + Monitor-State.
- Manage-Tracks-Modal zeigt die Paare mit Per-Side-Monitor-Toggles
  („welche Version bleibt wanted"), einer **Unlink**-Action
  (`POST /tracks/<id>/canonical`, akzeptiert auch manuellen Link), und
  **Move file** (`POST /tracks/<id>/move-file`,
  `core/library2/track_file_move.py`): wenn genau eine Seite die Datei hat,
  wird deren File-Link auf die andere Version umgehängt — Disk unangetastet
  (Rename/Reorganize refoldert später), Quelle unmonitored +
  wishlist-unmirrored, damit die konsolidierte Variante nicht erneut
  heruntergeladen wird. Duplicate-FILE-Removal bleibt der
  `single_album_dedup`-Maintenance-Job (im Maintenance-Modal).

### Per-Artist-Scope für Repair-Jobs
- `JobContext.scope` + `RepairWorker.run_job_now(job_id, scope=…)` +
  `/api/repair/jobs/<id>/run` Body `{"artist_name": …}`. Jobs, die
  `supports_artist_scope` deklarieren, filtern ihr Scan-SQL:
  **metadata_gap_filler, album_tag_consistency, library_retag**. Das
  Maintenance-Modal schickt den Artist automatisch und labelt gescopte Jobs
  „this artist" (unknown_artist_fixer bleibt naturgemäß library-wide — seine
  Tracks SIND Unknown Artist). Scheduled Runs tragen nie einen Scope.

### Profile-scoped Import
- `import_legacy_library(profile_id=…)`: das aus Watchlist/Wishlist
  abgeleitete Monitoring (und Wishlist-only-Seeding) ist auf das aktive
  Nutzerprofil gescoped, sodass der Wanted-State eines anderen Profils nicht
  mehr in diese View leakt. `None` behält das Legacy-Read-Everything-Verhalten;
  Tabellen von vor der `profile_id`-Column werden behandelt.

### Skip-Audit-Housekeeping
- Repair-Job `lib2_skips_cleanup` (default-off, wöchentlich): expired
  `lib2_manual_skips`-Rows, deren Datei verschwunden ist oder die die
  Retention überschritten haben (Default 180 Tage). Nur Audit-Rows — nie
  Files, nie Findings.

### Phase C — Tag-Preview/Re-Tag + Maintenance + Manual Import
- `retag.py`: Per-Track-Diff von File-Tags vs. lib2-Metadata
  (`core/tag_writer.read_file_tags` + `build_tag_diff`) und Batch-Write
  (`write_tags_to_file` mit seinen Placeholder-Guards). Multi-Artist-Credits
  aus der Junction (`artists_list`), Source-IDs embedded, Cover aus dem
  **lib2-Artwork-Cache** (nie ein Media-Server). API:
  `GET /<entity>/<id>/tag-preview`, `POST /tags/write` (Background-Job, poll
  `/jobs/status`).
- UI: **Preview Retag** auf der Artist-Toolbar und pro Album-Block —
  Lidarr-Style Diff-Tabelle (File → Library pro Feld), Per-Track-Checkboxen,
  Write mit Live-Progress.
- **Maintenance**-Modal fährt die bestehenden library-weiten Repair-Jobs von
  der Artist-Seite aus (Metadata Gap Fill, Fix Unknown Artist, Album Tag
  Consistency, Rename/Reorganize, Full Library Retag). Ehrlich beim Scope:
  diese scannen die ganze Library; Per-Artist-Scoping braucht Job-Level-Support
  (siehe Roadmap).
- **Manual Import** öffnet die bestehende Import-Seite (Staging-Flow) — Reuse,
  keine Kopie.
- **Manage Tracks** blieb zunächst bewusst ein Roadmap-Placeholder-Modal
  (per User-Präferenz: Placeholder dokumentieren, was noch fehlt) — inzwischen
  durch die echte Implementierung oben ersetzt.

### Artist-Page-Actions (jeder Button ist funktional)
- **Monitoring**-Modal: Monitor all / Monitor missing only / Unmonitor
  everything (Background-Bulk-Job) + „future releases"
  (`monitor_new_items` via `/edit`).
- **Search Upgrades**: läuft den lib2-aware `/upgrade-scan` und meldet
  Queued-Count.
- **History**-Modal: letzte `track_downloads`-Provenance für den Artist
  (Datum, Titel, Album, Source, Quality, Status).
- **Delete artist / delete album** mit Confirm: entfernt lib2-Rows, zieht
  Wishlist-/Watchlist-Mirrors zurück, **rührt nie Files auf Disk an**.
- Buttons ohne echtes Backend (Preview Rename/Retag, Manage Tracks, Manual
  Import) wurden ENTFERNT statt als Dead-Placeholder gelassen — kamen mit
  Phase C zurück.

### 2026-07-07 Review-Fix-Pass (schließt alle Findings aus Abschnitt 5)
Alle Findings des Deep-Branch-Reviews (Abschnitt 5) wurden in einem Pass
behoben:
- **Path-Resolution vereinheitlicht** (`paths.py::resolve_lib2_path`): scan,
  retag und der Skip-Audit-Cleanup lösen gespeicherte (Media-Server-Sicht)
  Pfade jetzt so auf, wie Artwork es schon immer tat — path-gemappte Setups
  sehen nicht mehr „alles missing" / der Audit wird nicht mehr gewischt (fixt
  B4).
- **Profile-Scope in Background-Threads**: Bulk-Monitor + Upgrade-Scan lösen
  das aktive Nutzerprofil im Request-Kontext auf und reichen es in den Thread
  (war: stiller Fallback auf Profil 1 auf Multi-Profil-Installs) (fixt B2).
- **Search Monitored ist jetzt real**: triggert `POST /api/wishlist/process`
  (alle monitored Missing Tracks sind bereits wishlist-gemirrort) statt Blind
  Auto-Grab des besten Ergebnisses für eine bloße Artist-Namen-Query (fixt B6).
- **Consolidated-Duplicate-Guard**: Bulk-Re-Monitor und
  Upgrade-Profil-Assignment überspringen Tracks, deren Datei bewusst zum
  kanonischen Partner verschoben wurde (`_NOT_CONSOLIDATED_SQL`) —
  Manage-Tracks-Cleanups werden nicht requeued (fixt B9).
- Artwork: EPs bekommen die lokale Artwork-URL auch (fixt B1); Refresh/Force
  bustet das THUMBNAIL zusätzlich zum Vollbild (fixt B3); Delete entfernt
  gecachte Art; Slow-Path-Resolution ist per Entity serialisiert (kein
  Provider-Stampede, entschärft M13).
- Importer: Wishlist-Seeding klemmt `expected_track_count` eines
  Discography-Release nicht mehr (hätte spätere Tracklist-Materialisierung
  getrunkiert, fixt B5); volle Bandnamen („Simon & Garfunkel") werden nicht
  mehr in Geister-Artists gesplittet, wenn der Artist bereits existiert
  (entschärft M1); das tote `COALESCE`-Update (M2) wurde entfernt/korrigiert.
- Autolink: Anhängen einer Datei an ein provider-only Release flippt `origin`
  zu 'library' (Sichtbarkeitsregel zählt es wieder, fixt B8); Artist-Lookup
  bekam einen SQL-Fast-Path (entschärft M11).
- **`lib2_discography_refresh`-Repair-Job** (default-off, wöchentlich):
  periodische Re-Expansion für bereits expandierte monitored Artists —
  `monitor_new_items` funktioniert jetzt ohne „Update Discography" zu drücken
  (geteilter `discography.auto_monitor_releases`-Helper; erste Expansion
  bleibt manuell; `lib2_artists.discography_synced_at` markiert Expansion
  explizit) (fixt M3/M4).
- **Album Edit** (Phase-D-Slice): `POST /albums/<id>/edit` + UI-Modal refiled
  den Release-Type (album/ep/single/compilation/live) — fixt die
  Track-Count-Heuristik-Fehlklassifikationen.
- Interactive Search: Skip-Check-Toggles gelten jetzt auch für ALBUM-Grabs
  (web_server Album-Zweig + Audit, fixt M15); Grab-Button-State funktioniert
  für Album-Ergebnisse (fixt B7); Manual-Skip-Audit schreibt nur, wenn das
  Feature-Flag an ist.
- Index-Stats zählen nur wanted-or-owned Tracks (Browsen einer Discography
  bläht „missing" nicht mehr auf, fixt M7); Multi-Disc-Missing-Slots kommen
  aus der gecachten Tracklist (fixt M6); History matched Multi-Artist-Credit-
  Strings; Retag verarbeitet >500 Tracks in Batches und pickt Files
  deterministisch (fixt M9/M16); Artists-Liste lehnt schlechtes Paging mit 400
  ab (kleinigkeit); debounced Filter-Box (fixt M12).
- **API-Layer ist jetzt getestet**: `tests/library2/test_api_routes.py`
  (Flask-Test-Client — Artwork-Rewrite inkl. EPs, Monitor-Mirror mit aktivem
  Profil, Consolidated-Guard beim Profile-Assign, Delete-Cleanup inkl.
  Artwork, Album-Edit, Refresh-Thumb-Busting).

### Phase 4 Acquisition / Decision (serverseitiger Pfad)
- Persistente, idempotente AcquisitionRequests tragen Admin-Intent, getrenntes
  Quality-Profil, Entity-Scope und serverseitig abgeleitete Search-Optionen.
- ReleaseCandidates liegen mit TTL und opaquen IDs serverseitig; URL/Magnet
  und Provider-Secrets erscheinen weder in API noch History. Explizite
  Source-Capabilities verhindern Track-/Bundle-Verwechslungen (ADR-08).
- Manual und Automatic Search verwenden dieselbe versionierte Decision Engine
  mit Rejections, Warnings und deterministischem Ranking. Force Grab ist
  Admin-only, übergeht nur ausdrücklich overridable Policy-Reasons und
  schreibt Audit-History.
- Prowlarr liefert im neuen Pfad nur Release-Bundles. Search läuft außerhalb
  langer SQLite-Transaktionen; einzelne Source-/Parse-Fehler bleiben isoliert.
- `lib2_wanted_tracks` kann RecordingRequests idempotent als ADR-02-Shadow
  materialisieren. Dieser Shadow dispatcht bewusst noch keinen Download; die
  Legacy-Wishlist bleibt bis zum gemessenen Cutover operativ.
- Acquisition-History ist append-only; Failed Candidates werden über
  Source/Indexer/GUID exakt blockiert. Retry bewertet alte und neue
  Candidates erneut und kann einen blockierten Release nicht automatisch
  wieder wählen.
- Neue Usenet-Grabs werden vor dem externen Client-Aufruf persistiert, danach
  mit Category und externer Job-ID korreliert und vom bestehenden Poller
  überwacht. Ein unklarer Submit bleibt `submission_unknown`, um
  Duplicate-Submits zu vermeiden.

**Bewusste Grenze:** Legacy-Interactive-/Wishlist-Routen und die bestehende UI
sind noch nicht auf diesen Vertrag umgestellt. Der neue Entity-Link reicht bis
Grab/History, nicht bis zum editionbezogenen Bundle-Import. Zentraler
Client-Monitor mit Category-Adoption, `acquisition_imports` und Manual-Import
bei Ambiguität sind Phase 5 (siehe Abschnitt 5.5 für aktuellen Status).

**Verifiziert am 2026-07-12 (`672c9ba`):** Backend-Vollsuite 7928 bestanden,
7 übersprungen, 2 deselektiert; Frontend Vitest 96/96; oxfmt/oxlint 0
Warnungen und 0 Fehler; Vite-Produktionsbuild erfolgreich. Der bekannte
Hinweis auf den großen Main-Chunk (~1,09 MB) bleibt bestehen.

---

## 4. Quality-Profile Pipeline Modularization — historisch, jetzt eigener Branch

Zwischenzeitlich höchste Priorität (vor Fortsetzung der Phasen A–E), mit
explizitem Ziel des Users: beweisen, dass verschiedene Kontexte (nicht nur
verschiedene Wishlist-Items) unter genuin unterschiedlichen
Quality/AcoustID/Import-Regeln laufen können — ein eigenständiges,
mergeable Feature, das gleichzeitig das Fundament ist, das Library v2 braucht.
Alle 6 Milestones plus der Per-Context-Beweis (Auto-Import kann das app-weite
Default-Profil overriden) wurden auf `library-overhaul` gebaut und verifiziert.

**2026-07-02: extrahiert in einen eigenständigen `quality-profiles`-Branch**
(erstellt vom `library-overhaul`-HEAD zu dem Zeitpunkt), damit der User einen
fokussierten Upstream-PR öffnen kann *ohne* dass Library v2 mitreitet —
Library v2 ist noch experimentell/ungereviewed, und ein Bundling hätte das
Review der Quality-Profile-Arbeit auf eigenen Verdiensten blockiert/verkompliziert.
Die Extraktion war eine echte Subtraktion, keine reine Directory-Kopie —
Library v2 war überraschend gut isoliert (`core/library2/`, `api/library_v2.py`,
eigene React-Route + Tests) bis auf eine geteilte Datei:
`core/library2/schema.py` hielt sowohl das `lib2_*`-DDL als auch das
`quality_profiles`-DDL/Migration/Seeding zusammen. Das wurde in ein neues,
Library-v2-unabhängiges `core/quality/schema.py`
(`ensure_quality_profiles_schema`) gesplittet, das `database/music_database.py`
jetzt direkt aufruft statt `ensure_library_v2_schema`.
`core/repair_jobs/quality_upgrade.py`'s Milestone-5 „profile-aware scan" hing
ebenfalls von Library v2 ab (`lib2_tracks.legacy_track_id` Per-Track-Profile-
Links) und wurde auf dem Standalone-Branch auf Global-Profile-only-Verhalten
zurückgesetzt — dieser spezifische Per-Track-Override muss re-added werden,
sobald Library v2 auf dem gemergten `quality-profiles`-Branch rebased.

**Auch in den Split gefaltet** (eine Design-Frage, die beim Erklären der
Pipeline-Architektur aufkam): `wishlist_tracks` trug früher
`quality_profile_id` PLUS 3 weitere denormalisierte Flag-Columns
(`acoustid_required`/`fallback_allowed`/`downsample_enabled`), einmalig beim
Insert aufgelöst. Das Tracing der echten Pipeline zeigte: 2 der 3 waren Dead
Code (nie gelesen — das Import-Gate löste sie bereits LIVE vom Profil auf) und
der 3. (`acoustid_required`, genutzt von der Download-seitigen
AcoustID-Skip-Entscheidung) war die eine Stelle, an der ein eingefrorener
Snapshot still von einem später editierten Profil abdriften konnte. Fixed:
`wishlist_tracks` speichert jetzt NUR `quality_profile_id` (den Pointer); jede
Pipeline-Stufe — Search-Ranking, AcoustID-Skip, Import-Quality-Gate,
quality_upgrade, Auto-Import — löst die tatsächlichen Profil-Settings LIVE via
`core/quality/selection.py::load_profile_by_id(quality_profile_id)` auf, wenn
sie sie braucht.

Standalone-Branch-Status: neues `core/quality/schema.py`-Modul extrahiert +
Wishlist-Columns vereinfacht + Library-v2-Files/Route-Wiring entfernt +
`quality_upgrade.py` auf global-only zurückgesetzt + alle betroffenen Tests
gefixt. Voll `pytest tests/` grün, `oxlint --type-check` 0 Fehler, und ein
echter Docker-Boot eines **frischen** Installs (isolierte Scratch-Config/Daten,
nicht der reale Container des Users) bestätigte: keine `lib2_*`-Tabellen
werden jemals erstellt, `quality_profiles` wird direkt erstellt,
`wishlist_tracks` hat nur die eine Pointer-Column, und
`/api/quality-profile/custom` / `/api/auto-import/settings` / die
redesignte Settings-UI funktionieren alle mit null Library-v2-Referenzen
irgendwo im gerenderten HTML/JS/TS.

**2026-07-02, zweiter Hardening-Pass** (user-angefragter deep-critical Review
gegen die Lidarr-Referenzquelle in `_reference/Lidarr`): fand und fixte eine
ECHTE semantische Regression — Profil `acoustid_required=False` wurde als
„AcoustID komplett überspringen" behandelt (master.py Per-Item-Skip +
Auto-Import `_skip_quarantine_check`-Injection), aber die Migration füllt das
Feld aus `acoustid.require_verified` (False für die meisten Nutzer), was
stillschweigend den FAIL-Quarantäne-Schutz bei jedem Wishlist-Download nach
dem Upgrade deaktiviert hätte. Korrigierte Semantik: `acoustid_required` ist
NUR der Strictness-Dial (durchgesetzt am require-verified-Check der Pipeline,
jetzt aus dem Item-Profil gelesen); den Check komplett zu überspringen bleibt
eine explizite Per-Download-Nutzeraktion. Auch die „jede Stufe fragt das
Profil"-Architektur wurde vervollständigt: Deep-Verify, Replace-Lower,
Downsample und Lossy-Copy in
`core/imports/pipeline.py`/`file_ops.py` lesen jetzt das Item-Profil (via
per-file gecachtes `_resolve_context_quality_profile`) statt globaler
Config-Keys — Config-Keys bleiben nur als Storage der Settings-Seite, in
beide Richtungen synchron gehalten
(`apply_quality_profile_to_settings` Profil→Config bei Apply;
`sync_default_quality_profile_from_config` Config→Default-Profil bei jedem
Settings-Save — die fehlende Richtung, die sonst Settings-Seiten-Edits für
die Pipeline unsichtbar gemacht hätte). Profil-Löschung räumt jetzt auch
Referenzen auf (Wishlist-Rows → NULL, passender Auto-Import-Override
gecleared — Lidarr macht eine In-Use-Refusal, wir machen dokumentierte
Fallback-Semantik stattdessen). Plus: Schema-Default `acoustid_required`
korrigiert 1→0 (lenient, matching Config-Default), Duplicate-Name-Rename
bekommt einen proper Error, und der `folder_artist_override`-Toggle (der in
früheren Runden seine gesamte UI verloren hatte — funktionale Regression) ist
zurück als Checkbox auf der Quality-on-Import-Tile, pro Profil erfasst. Noch
offen war zu dem Zeitpunkt: ein echter Browser-Click-Through (Chrome-Extension-
Automation war die ganze Session über unavailable — nur code- und
curl-verified).

**Coming back to Library v2**: sobald `quality-profiles` upstream merged,
`library-overhaul`'s Library-v2-Commits daraufhin rebasen, sodass Library v2's
eigenes schema.py aufhört, `lib2_quality_profiles` (oder den
Promotion-/Rename-Schritt) zu erstellen, und stattdessen einfach die jetzt
upstream `quality_profiles`-Tabelle direkt referenziert; den
`quality_upgrade.py`-Per-Track-Library-v2-Link on top der zurückgesetzten
Global-only-Version re-adden.

---

## 4.5 Reuse-First-Philosophie & zweiter Split: Main-Pipeline-Hardening (2026-07-13)

### Die Philosophie

Beim Bau der Acquisition-Schicht (Abschnitt 5.4/5.5) sind wiederholt Stellen
aufgefallen, an denen die neue, Library-v2-getriebene Recherche etwas
freigelegt hat, das der **bestehenden Legacy-Wishlist/Watchlist-Pipeline
selbst fehlt** — unabhängig davon, ob Library v2 je mergt. Genau wie beim
`quality-profiles`-Split (Abschnitt 4) gilt: wenn ein Stück Arbeit einen
echten, eigenständigen Wert für die Main-Pipeline hat, wird es **nicht** in
den großen, noch experimentellen Library-v2-PR gepackt, sondern zuerst,
separat und unabhängig gecommittet/als PR eingereicht.

**Regel:** Jedes Stück Code aus `core/acquisition/*` wird gegen die Frage
geprüft: *Ist das etwas, das auch der Legacy-Pipeline (Wishlist/Watchlist,
`core/download_orchestrator.py`, `core/downloads/*`, `core/imports/*`) nützt,
selbst wenn Library v2 nie landet?* Falls ja → eigener, früherer,
kleinerer Main-Pipeline-Commit/PR, damit er unabhängig reviewt und gemergt
werden kann, bevor der große Library-v2-PR kommt. Das hält den
Library-v2-PR kleiner und gibt sofortigen Nutzen an die bestehende App,
statt dass generische Verbesserungen im Library-v2-Rucksack mitreisen.

### Konkrete Kandidaten (Reuse-Audit 2026-07-13, gegen die Legacy-Pipeline geprüft)

Priorisiert nach Wert × Portierungssicherheit:

**1. Path-Health-Diagnose (aus `core/acquisition/path_health.py`) — sicherster Gewinn**
`inspect_mapping_configuration()` / `inspect_reported_path()` validieren
`download_source.usenet_path_mappings`-Syntax und prüfen, ob gemappte/lokale
Zielordner tatsächlich lesbar sind — als redigierter Status
(`mapped`/`direct`/`unreadable`/`mapping_unavailable`), ohne echte
Server-Pfade preiszugeben. **Das gibt es heute nirgends** unter
`core/downloads/`/`core/imports/` — Path-Mapping-Auflösung existiert
(`core.download_plugins.album_bundle.resolve_reported_save_path`), aber keine
Health-Check-Oberfläche dafür. Zero Library-v2-Kopplung (nur Config +
Resolver) → 1:1 portierbar als neuer Diagnose-Endpoint/Settings-Check.

**2. Usenet-Age/Retention-Guard (aus `core/acquisition/decision_engine.py:385-396`) — kleinster, sicherster Fix**
Lehnt Usenet-Kandidaten ab, die jünger als eine konfigurierte
Propagation-Delay-Schwelle oder älter als eine konfigurierte
Retention-Schwelle sind. Feldnamen im Prototyp:
`policy.minimum_age_seconds` (Default `0` = deaktiviert) und
`policy.maximum_age_seconds` (Default `None` = unlimitiert) —
`decision_engine.py:74-75`. **Das gibt es aktuell gar nicht** in der
Legacy-Pipeline: Usenet-Kandidaten werden nicht nach Alter/Retention
gefiltert. Für den Main-Pipeline-Port vorgeschlagene neue Config-Keys
(analog zu Lidarrs Indexer-Settings „Minimum Age"/„Retention"):
- `download_source.usenet_minimum_age_minutes` (Default `0`, deaktiviert)
- `download_source.usenet_retention_days` (Default `0` = unlimitiert)

Beide Defaults bewusst „aus", damit der Fix additiv/dormant ist (bricht
nichts für bestehende Setups). **Die PR sollte direkt die Settings-UI dafür
mitbringen** (zwei Zahlenfelder im Usenet-/Download-Source-Bereich der
Settings-Seite, analog zu bestehenden Usenet-Settings) — nicht nur einen
harten Default ohne Einstellmöglichkeit, da Retention/Propagation-Delay
je nach Usenet-Provider stark variieren.

**3. Präzises Blocklisting nach Source/Indexer/GUID (aus `core/acquisition/blocklist.py`) — reale Lücke, mittlerer Aufwand**
Die Legacy-Pipeline blockt Downloads nur über `download_blacklist`
(`database/music_database.py`), keyed auf `(username, filename)` —
Soulseek-spezifisch, kann Usenet-/Torrent-Releases nicht abbilden. Kein
Reason-Code, keine Expiry, keine Audit-Historie. Das neue Modell
(`dedupe_key` aus source/indexer/guid + Reason-Codes + Expiry + Audit-Trail)
ist ein echter, engerer Ersatz. Portierung: `dedupe_key`-Konzept in
`core/downloads/candidates.py`/`task_worker.py` einziehen + Schema-Erweiterung.

**4. Unveränderliches Audit-Log (aus `core/acquisition/history.py`) — reale Lücke, mittlerer Aufwand**
Append-only Tabelle mit DB-Trigger, der UPDATE/DELETE verbietet, redigierte
Payloads, geschlossenes Event-Enum (search/grab/import-Lifecycle). Die
Legacy-Pipeline hat dafür nichts Äquivalentes — `core/downloads/history.py`
ist nur Spotify-Sync-Batch-Historie für die UI, keine
Download-Attempt-Audit-Kette. Portierung: neue Tabelle + Call-Sites in
`download_orchestrator.py`/`task_worker.py`/`lifecycle.py`.

**5. Client-Monitor-Reconciliation (aus `core/acquisition/client_monitor.py`) — höchster Umbauaufwand, zurückgestellt**
Der Abgleichs-Algorithmus (Job-ID-Matching + Title-Fallback-Adoption für
Usenet-Jobs ohne DB-ID) wäre wertvoll — die heutige Usenet-Überwachung
(`core/downloads/monitor.py`) ist Soulseek-fokussiert und dünn für Usenet.
Aber: die Klasse selbst hängt fest an lib2-Tabellen; nur der Algorithmus ist
extrahierbar, nicht die Klasse. Zurückgestellt bis 1–4 gelandet sind.

### Vorgeschlagene Reihenfolge für den eigenständigen, frühen PR

1. Path-Health-Diagnose (Item 1)
2. Usenet-Age/Retention-Guard **inkl. Settings-UI** (Item 2)
3. Präzises Blocklisting (Item 3)
4. Audit-History-Tabelle (Item 4)
5. *(zurückgestellt)* Client-Monitor-Reconciliation (Item 5)

Dieser PR ist unabhängig vom Library-v2-PR und kann vor ihm gemergt werden —
analog zum `quality-profiles`-Split. Sobald er upstream ist, rebased
`library-overhaul`'s Acquisition-Code darauf, statt die Logik doppelt zu
halten (gleiches Muster wie „Coming back to Library v2" oben).

---

## 5. Milestone-Plan, Architektur-Regeln, Deep-Dive-Review, Findings

### 5.1 Kontext & Locked Decisions (ursprünglicher Plan)

SoulSyncs bisherige „Library" ist ein flacher, read-only Mirror des
Media-Servers. Ziel: ein **Lidarr-äquivalenter Library-Manager** — gleiche
Informationsarchitektur und Feature-Set wie Lidarr — aber komplett auf
SoulSyncs eigener Such-/Download-/Processing-/Tagging-Pipeline (Soulseek + die
anderen konfigurierten Sources) laufend, als **opt-in** Feature parallel zur
alten Library. Funktionalität vor Schönheit; klarer Status statt Song-Masse;
**Datenbank ist Source of Truth** (jede Datei-Location ist in der DB
festgehalten, damit die Library unabhängig vom Folder-Layout jedes Users
rekonstruierbar ist).

**Korrekturen aus User-Feedback (überschreiben M1-Entscheidungen):**
1. **NIE Media-Server-Abhängigkeit.** Artwork darf NICHT von
   Plex/Jellyfin/Navidrome kommen (`normalize_image_url` war falsch). Wie
   Lidarrs MediaCover: Art aus den Files selbst (embedded covers) und von
   Metadata-Providern holen, **lokal auf Disk cachen**, von einem lokalen
   Endpoint servieren. Muss für ein reines SoulSync-Install ohne Media-Server
   funktionieren.
2. **Monitoring = die bestehende Watchlist/Wishlist**, via interne Calls.
   Artist-„Monitor" ON = zur **Watchlist**; Album/Single/Track-„Monitor" ON =
   zur **Wishlist**. So bleibt die bestehende Auto-Scan-/Auto-Download-Maschinerie
   funktionsfähig und die Seiten bleiben synchron (später können die alten
   Seiten pensioniert werden). Toggling eines `lib2`-Monitored-Flags mirrort zu
   Watchlist/Wishlist.
3. **Volles Lidarr-Feature-Set** (phasiert): Interactive/Manual Search →
   Release wählen → SoulSync-Download-Pipeline; Manual Import; Re-Tag +
   Preview Re-Tag; Metadata Gap Fill / Fix Unknown Artist / Album Tag
   Consistency; Refresh & Scan; Search Monitored; Single↔Album Move/Dedup;
   Manage Tracks; Edit; Delete (mit Confirm).
4. **UI:** full-width / edge-to-edge (nicht in eine kleine zentrierte Card
   boxen); globale Suchleiste auf dieser Seite entfernen; Text-Kontrast fixen;
   Lidarr-Style **Tabellen** im Artist-Detail (Albums & Singles gruppiert,
   Monitored-Toggle pro Row).

**Locked decisions:**
- Paralleles `lib2_*`-Schema (behalten) — DB ist Source of Truth, File-Location
  pro File gespeichert (behalten).
- Frontend in React/TanStack unter `webui/src/routes/library-v2/` (behalten).
- Artwork: **Embedded Art (primär) + Provider-Lookup (Fallback), gecacht auf
  lokalem Disk via den bestehenden ImageCache**, serviert als
  `/api/image-cache/<key>` — media-server-unabhängig. `artist.jpg` /
  `cover.jpg` in den Musikordner zu schreiben wird als *optionale* Aktion
  angeboten, wo der Ordner schreibbar ist (der managed Cache ist die
  verlässliche Primärquelle, da Library-Folder read-only sein können).
  — **Später korrigiert**: der Artwork-Cache liegt tatsächlich unter
  `<db_dir>/lib2_artwork/` mit eigenem Endpoint (Lidarr-MediaCover-artiger,
  siehe Abschnitt 3 „Artwork") statt im ImageCache/`/api/image-cache/<key>` —
  die STATUS-Variante ist die bessere; dieser Plan-Text war hier einfach
  älter.
- Monitoring mirrort zu Watchlist/Wishlist per externer ID; Artists mit nur
  einer `soul_`-ID bleiben lib2-lokal (graceful degradation).

### 5.2 Phasenplan (Original-Roadmap)

**Phase A — Look/feel right + media-server-unabhängiges Artwork + Monitoring↔Watchlist/Wishlist**
- A1. Full-width, themed, keine globale Suchbox, Kontrast: `'library-v2'` in
  `_gsHidePages` (`webui/static/downloads.js`); Route edge-to-edge (Card-Wrapper
  raus aus `library-v2-page.module.css`); Design-Tokens aus `style.css`.
- A2. Media-server-unabhängiges Artwork-Subsystem — neues
  `core/library2/artwork.py`: Embedded Cover (primär) via
  `core/metadata/art_apply.py::extract_embedded_art`; Provider-Fallback via
  `core/metadata/artist_image.py` / `art_lookup.py`; Cache auf Disk + Serve via
  `core/image_cache.py`; `image_local_url`-Columns auf `lib2_artists`/
  `lib2_albums`; neuer Endpoint `GET /api/library/v2/artwork/<kind>/<id>`.
- A3. Monitoring ↔ Watchlist/Wishlist-Mirroring — Monitor-Stub durch echtes
  Mirroring ersetzen (`db.add_artist_to_watchlist`/`remove_...`,
  `db.add_to_wishlist`/`remove_from_wishlist`), `lib2_*.monitored`-Flag immer
  mitführen, graceful degradation ohne externe ID.
- A4. Lidarr-Style Artist-Detail + Tabellen + Refresh & Scan: Albums/Singles
  als separate Lidarr-Style Tabellen; „Refresh & Scan"-Action (Artist-/
  Album-Level) liest File-Tags neu ein und re-resolved Artwork; neuer Endpoint
  `POST /api/library/v2/<entity>/<id>/refresh`.
- **Verify A:** Image neu bauen, Seite gegen die reale Library öffnen —
  full-width, keine Suchbox, Cover sichtbar (embedded-art-derived, kein
  Media-Server), Artist-Monitor fügt Watchlist-Row hinzu, Album-Monitor fügt
  Wishlist-Row hinzu (check via DB), Refresh & Scan repopuliert Tags/Art.

**Phase B — Interactive/Manual Search → SoulSync Download Pipeline**
Pro Artist/Album/Single/Track: Suche über die konfigurierten Sources **mit
ihren Prioritäten**, Ergebnistabelle zeigen (Title, Artist, Album, Length,
Quality, Format, Size, Source/User, Bitrate, Slots/Seeders, Score, Warnings),
User wählt ein Release, Download durch die Pipeline, dann Import → `lib2`.
Reused: `core/search/orchestrator.py::run_enhanced_search`/
`stream_source_search` (Metadata-Identify), dann Source-/Candidate-Layer
`POST /api/manual-search/<task_id>` + `POST /api/download` /
`/api/download-selected-candidate/<task_id>`
(`core/download_orchestrator.py`, `core/downloads/task_worker.py`);
Config-Keys `download_source.mode`/`hybrid_order` für Prioritäten. Post-Download-
Import via `core/imports/pipeline.py::post_process_matched_download` → Link in
`lib2_track_files`.

#### Kritische Reuse-Regel für jeden neuen Acquisition-/Import-Pfad

Library v2 muss die bestehende, kampferprobte Such-, Download- und
Post-Processing-Behavior wiederverwenden, wo die Semantik gleich ist. Eine neue
Orchestrierungsschicht darf persistente Acquisition Requests, Release-Level-
Korrelation, restart-sicheren State, Edition/Track-Matching und atomare
Library-Writes hinzufügen. Sie darf keine zweite Implementierung der
bestehenden File-Processing-Policy erschaffen.

Folgendes ist verpflichtendes geteiltes Verhalten:
- konfigurierte Source- und Protokoll-Prioritäten müssen bei der
  Replacement-Candidate-Auswahl angewendet werden;
- Quality Profiles müssen akzeptierte Quality, Cutoff und die
  Upgrade-Policy (`acceptable`, `until_cutoff` oder `until_top` /
  Upgrade-until-Target) kontrollieren;
- Retention/Mindestalter und Custom Formats müssen die bestehende
  Profil- und Decision-Logik nutzen;
- Stability, Integrity, Quality, AcoustID und andere aktivierte
  Post-Processing-Checks müssen die bestehenden Implementierungen nutzen;
- fehlgeschlagene Files müssen die bestehende Quarantäne- und
  Audit-Semantik nutzen;
- ein fehlgeschlagener Candidate muss präzise geblockt werden, und der
  nächste geeignete Candidate — auch von einer anderen konfigurierten Source —
  muss nach denselben Prioritätsregeln gewählt werden;
- Retry-State muss einen Neustart überleben und darf nicht vom
  Legacy-In-Memory-`download_tasks`-State abhängen.

Der Phase-5-Bundle-Importer ist deshalb nur ein Release-/Bundle-Koordinator: er
inventarisiert den fertigen Output, matcht ihn gegen die erwartete Edition und
delegiert Per-File-Validation, Quarantäne, Retry und Final-Processing an
geteilte Services. Wenn ein alter Helper an Legacy-Task-IDs oder
In-Memory-State gekoppelt ist, einen source-unabhängigen Service extrahieren
oder einen Adapter hinzufügen; nicht die alte Logik in eine zweite Pipeline
kopieren. Phase 5 ist erst komplett, wenn Tests beweisen, dass ein
fehlgeschlagener erster Candidate erfolgreich durch einen Candidate derselben
Source und durch einen von einer niedriger priorisierten Source ersetzt wird,
und dass Upgrade-Requests am konfigurierten Upgrade-until-Target des Quality
Profils stoppen.

**Phase C — Re-Tag/Preview, Metadata Gap Fill, Fix Unknown Artist, Album Tag Consistency, Manual Import**
(Details siehe Abschnitt 3 „Phase C" — vollständig implementiert.)

**Phase D — Single↔Album-Handling, Manage Tracks, Edit, Delete**
Single ins Album verschieben/mergen/Duplikat entfernen (nutzt
`canonical_track_id` + Reorganize/Move-Funktionen); Manage-Tracks-Editor;
Edit Artist/Album/Track-Metadaten (reuse `PUT /api/library/...`); Delete
File/Unlink (DB-erfasster Pfad → safe Delete) — destruktive Actions brauchen
Confirmation. (Status: teilweise — siehe Abschnitt 7 TODO.)

**Phase E — Search Monitored/Auto-Sync, Playlists (zuletzt)**
„Search Monitored" triggert Wishlist-Processing
(`POST /api/wishlist/process`) + Watchlist-Scan
(`core/watchlist_scanner.py`). Playlists-Integration zuletzt. (Status:
Search Monitored korrekt implementiert seit 2026-07-07-Pass; Playlists
unbegonnen.)

### 5.3 Architektur-Korrektur — bestehende Main-Pipeline wiederverwenden

Das ursprüngliche Library-v2-Ziel bleibt erhalten: Library v2 muss SoulSyncs
bestehende Download-Pipeline erweitern und daran andocken, nicht deren
Entscheidungsfindung mit einer zweiten Implementierung ersetzen. Die
bestehende Pipeline ist die behaviorale Source of Truth für Search-Mode,
Source-Selection, Quality-Policy, Retries, Post-Processing, Quarantäne und
Approval.

Der neue Library-v2-Code darf nur die fehlenden Library-Concerns hinzufügen:
- persistente Acquisition-Request/Grab/History-Korrelation;
- Release-Bundle- und Edition/Recording-Kontext;
- restart-sichere Beobachtung eines externen Clients;
- Bundle-Inventory und Edition/Track-Matching;
- atomare Writes in `lib2_*` NACH erfolgreicher geteilter Import-Pipeline.

Folgendes muss wiederverwendet oder in geteilte Services extrahiert werden,
niemals in einer zweiten Decision Engine oder einem zweiten Bundle-Importer
neu implementiert:
- `download_source.mode`, inkl. `best_quality` und Hybrid-Verhalten;
- `download_source.hybrid_order` und die konfigurierte Source-Prioritätskette;
- Source-by-Source-Fallback und das bestehende Next-Candidate-Retry-Verhalten;
- das komplette Quality Profile, inkl. Ranked Targets, Fallback,
  `upgrade_policy` (`acceptable`, `until_cutoff`, `until_top`), Cutoff und alle
  AcoustID-/Quality-/Import-Settings;
- `core/download_orchestrator.py` und `core/downloads/task_worker.py` für
  Candidate-Ordering, Source-Dispatch und Retry-Semantik;
- `core/imports/pipeline.py`, `file_integrity.py`, `guards.py` und
  `quarantine.py` für Stability, Integrity, Quality, AcoustID, Quarantäne,
  Approval und Final-Processing.

Library-v2-Acquisition muss behavioral ununterscheidbar vom alten Pfad für
dieselben User-Settings sein. Eine monitor-getriggerte Acquisition und eine
manuell gewishlistete Acquisition dürfen unterschiedlichen persistenten
Kontext haben, müssen aber dieselben Source-, Quality-, Retry-, Quarantäne-
und Approval-Entscheidungen treffen.

**Quality-Upgrade-Integration:** die bestehenden Quality-Upgrade-Jobs bleiben
der kanonische Upgrade-Mechanismus. `core/library2/quality_eval.py`
bestimmt, ob eine bestehende Datei ein Upgrade-Kandidat ist. Der periodische
`lib2_upgrade_scan` läuft nur für Profile, deren `upgrade_policy` Upgrades
erlaubt, und respektiert `until_cutoff`/`until_top`. Die bestehende
`quality_upgrade`-Provider-Search- und Finding-Logik muss wiederverwendet
werden. Während des gestaffelten Cutovers ist `mirror_tracks_wishlist`
bewusst der Output-Adapter, weil er in die kampferprobte
Wishlist/Main-Pipeline mit dem exakten Quality Profile eintritt. Ein direkter
Library-v2-Acquisition-Output darf dies erst als Teil des späteren globalen
Wishlist-Cutovers ersetzen, nachdem Parität bewiesen ist; er darf nie
Source-Selection, Retry, Quarantäne oder Import-Verhalten still umgehen oder
duplizieren.

**Quarantäne- und Manual-Approval-Integration:** ein Library-v2-Download, der
Integrity-, Quality-, AcoustID- oder einen anderen aktivierten
Post-Processing-Check nicht besteht, muss dem bestehenden
Quarantäne-Lifecycle folgen. Das Quarantäne-Sidecar muss Library-v2-
Acquisition- und Edition-Kontext bewahren. Das Approven einer quarantänten
Datei muss sie wiederherstellen und die geteilte Post-Processing-Pipeline
re-dispatchen. Approval darf nur den spezifisch approvten Check umgehen (z.B.
AcoustID); alle anderen aktivierten Checks müssen erneut laufen. Die Datei
darf nicht allein durchs Approven als completed markiert werden, und der
Library-v2-Import-/History-State darf erst nach finalem geteiltem
Pipeline-Erfolg fortschreiten. Legacy-Thin-Sidecars laufen weiter über den
bestehenden Manual-Staging-Fallback.

### 5.4 Findings aus dem Reuse-Audit (2026-07-12) — Korrekturarbeit vor weiteren Acquisition-Features

Diese Findings beschreiben den damaligen Branch-Stand inkl. lokaler
Phase-5-Commits und der uncommitteten Import-Pipeline-Arbeit. Sie müssen als
Korrekturarbeit behandelt werden, BEVOR weitere Library-v2-Acquisition-
Features hinzugefügt werden.

**LIB2-F01 — Duplicate Acquisition Decision Path (P0).**
`core/acquisition/search_service.py` durchsucht alle gegebenen Adapter
concurrently und `core/acquisition/decision_engine.py` ranked die
resultierenden Candidates. Das ist ein neuer Decision-Path — nicht das
bestehende `DownloadOrchestrator`-Verhalten, und nicht an den vollständigen
`download_source.mode`/`hybrid_order`-Contract verdrahtet.
`EffectivePolicy.from_profile` holt die Legacy-Source-Mode-Settings ebenfalls
nicht aus der Config. Ergebnis kann zwischen einem Library-v2-Request und dem
gleichen Request via Wishlist oder Interactive Search differieren.
**Required correction:** die bestehende Orchestrator-/Worker-Selection-
Semantik nutzen oder deren source-unabhängigen Selection-Service extrahieren.
`best_quality` (alle konfigurierten Sources durchsuchen, global wählen) und
Hybrid/Source-Priority (die konfigurierte Source-Chain der Reihe nach
abgehen) explizit unterstützen. Beide Modi nicht auf einen numerischen
`source_priorities`-Sort-Key reduzieren.

**LIB2-F02 — Bundle Import umgeht die Main-Post-Processing-Pipeline (P0).**
`core/acquisition/bundle_import.py` staged Files, probet Basis-Quality-Facts
und schreibt `lib2_track_files` direkt. Delegiert nicht jedes File an den
bestehenden `core/imports/pipeline.py`-Pfad. Der neue Pfad erbt daher noch
nicht das vollständige Stability-, Integrity-, Quality-, AcoustID-,
Verification-, Quarantäne-, Tagging-, Conversion- und Finalization-Verhalten.
**Required correction:** die Bundle-Schicht nur als Orchestrator machen. Sie
muss Release-/Edition-Kontext an einen geteilten File-Processing-Service
geben und diesen entscheiden lassen, ob eine Datei fortfahren darf. Direkte
Lib2-Completion nur erlaubt, nachdem die geteilte Pipeline Erfolg meldet.

**LIB2-F03 — Quality-Profile-Enforcement ist im Bundle-Pfad unvollständig (P0).**
Der Bundle-Importer ruft `probe_audio_quality`, aber ein Probe ist nicht
dasselbe wie das bestehende Quality-Profile-Gate. Ranked Targets, Fallback,
Downsample-/Lossy-Copy-Verhalten, AcoustID-Requirements, Deep-Verification
oder profil-spezifische Import-Settings werden nicht von sich aus
durchgesetzt. Der neue Pfad kann daher eine Datei akzeptieren, die der
etablierte Import-Pfad quarantänen würde.
**Required correction:** das exakte Quality Profile des Requests auflösen und
die bestehenden profil-aware Guards und den Post-Processing-Kontext
wiederverwenden. Dieselben Settings müssen in beiden Pfaden dasselbe
Accept-/Reject-Ergebnis produzieren.

**LIB2-F04 — Fehlgeschlagene Imports haben nicht die alte automatische
Retry-Semantik (P0).**
`record_import_failure` kann einen Candidate blocklisten, überführt den
Request aber direkt in `failed`. Die neue Import-Pipeline wählt nicht
automatisch den nächsten gecachten Candidate, durchsucht nicht die restliche
Source-Chain und macht nicht mit einer anderen Source weiter nach einem
Quality-/Integrity-/AcoustID-Fehler. Die alte Pipeline macht das via
Worker-Retry-State und `requeue_quarantined_task_for_retry`-Verhalten.
**Required correction:** nach einem Candidate-Level-Processing-Fehler das
exakte Blocklist-Event persistieren, den Acquisition-Request als retryable
erhalten, und die bestehende Candidate-/Source-Retry-Semantik über einen
Adapter aufrufen. Nur erschöpfte Candidates/Sources dürfen terminales
Request-Failure produzieren.

**LIB2-F05 — Quality-Upgrade-Output-Ownership brauchte eine explizite
Entscheidung (P1).**
`core/repair_jobs/lib2_upgrade_scan.py` erkennt Library-v2-Upgrade-Candidates
und ruft `mirror_tracks_wishlist`. Der bestehende `quality_upgrade`-Job und
Wishlist/Main-Pipeline sind der kanonische, getestete Upgrade- und
Download-Pfad.
**Decision/correction:** die bestehenden periodischen Jobs und ihre
`upgrade_policy`/`upgrade_cutoff_index`-Semantik behalten. Wishlist-Mirroring
als Compatibility-Adapter bis zum globalen Wishlist-Cutover wiederverwenden;
er muss das exakte Profil tragen und in dieselbe Main-Such-/Download-/
Import-Pipeline eintreten. Keine direkte parallele Upgrade-Pipeline erfinden,
nur um eine Acquisition-Row zu erzeugen.

**LIB2-F06 — Quarantäne und Manual-Approval sind nicht mit Bundle Import
verbunden (P0).**
Die bestehende Quarantäne-Implementierung persistiert serialisierten
Kontext, stellt approvte Files wieder her und re-dispatched Processing,
wobei nur der approvte Check umgangen wird. Der neue Bundle-Importer hat
keinen äquivalenten Quarantäne-Sidecar-Flow und keine Library-v2-
Approval/Re-Dispatch-Integration. Für ein durch AcoustID, Quality oder
Integrity abgelehntes File ist noch nicht garantiert, dass es sich beim
Approven wie ein Old-Path-Quarantäne-Eintrag verhält.
**Required correction:** Acquisition-/Edition-Kontext im Quarantäne-Sidecar
bewahren, `approve_quarantine_entry` wiederverwenden, die Datei
wiederherstellen und in die geteilte Pipeline re-entern. Nur der approvte
Check darf umgangen werden; alle anderen Checks müssen vor Lib2-Completion
erneut laufen.

**LIB2-F07 — Persistenter State und Legacy-In-Memory-Retry-State sind nicht
gebrückt (P1).**
Der alte Retry-Pfad nutzt Task-/Batch-Kontext wie gecachte Candidates,
used/exhausted Sources und Quarantäne-Entry-IDs. Die neuen
Acquisition-Tabellen speichern andere Identifier und liefern aktuell kein
vollständiges dauerhaftes Äquivalent. Ein Neustart kann daher die exakte
Retry-Entscheidung verlieren, auch wenn die neuen Monitor-/Import-Rows
überleben.
**Required correction:** einen expliziten Adapter definieren, der
Legacy-Task-/Batch-Kontext auf Acquisition Request, Grab, Candidate, Import
und History-IDs mappt, dann jeden retry-relevanten Fact persistieren, BEVOR
externe oder Filesystem-Arbeit passiert.

**LIB2-F08 — Behavior-Parität ist noch nicht durch die Test-Matrix bewiesen
(P1).**
Aktuelle gezielte Tests decken viele neue State-Transitions, Inventory- und
Matching-Fälle ab, beweisen aber noch keine Parität für alle relevanten
Kombinationen aus `best_quality`, Hybrid/Source-Order,
Quality-Profile-Upgrade-Policy, Quality-Quarantäne, AcoustID-Approval,
Next-Candidate-Retry und Restart. Die dokumentierte Full-Suite datiert vor
der neuesten lokalen Phase-5-Arbeit.
**Required correction:** Contract-Tests hinzufügen, die äquivalente Legacy-
und Library-v2-Szenarien laufen lassen und Selected Source, Candidate-Order,
Rejection, Quarantäne, Approval, Retry und Terminal-State vergleichen. Die
Full-Suite erst nach diesem Paritäts-Gate laufen lassen.

Diese Findings ersetzen jede frühere Annahme, dass die neue Decision Engine
und der Bundle-Importer als unabhängige Implementierungen akzeptabel waren.
Die nächste Implementierungsphase ist LIB2-011, nicht ein weiteres Feature
auf dem aktuellen gespaltenen Verhalten.

### 5.5 LIB2-011 Implementierungs-Status (2026-07-12)

**Completed:**
- der direkte Lib2-Bundle-Importer wurde reverted;
- Acquisition und der Legacy-Orchestrator teilen sich einen
  Source-Policy-Resolver für `best_quality`, Priority-Mode, `hybrid_order` und
  Profil-Ordering;
- deterministisches Bundle-Inventory, Edition-Track-Matching und Manual
  Review sind persistent und restart-sicher;
- gematchte Files werden durch die bestehende Import-Pipeline dispatched,
  nicht durch eine zweite Quality-/Import-Implementierung;
- Pipeline-Erfolg und Quarantäne werden pro geplantem Track persistiert; der
  bestehende Sidecar-/Approve-Pfad behält Acquisition-Marker und completed
  erst, nachdem die restlichen Checks bestehen;
- das exakte `lib2_entity` und Quality Profile überleben Legacy-Candidate-
  Retries;
- Torrent und Usenet behalten getrennte erschöpfende Retry-Budgets;
- eine erschöpfte Legacy-Worker-Suche lässt den persistenten Import/Request
  fehlschlagen und blockt das exakte Release, statt es unbegrenzt weiter
  „importing" zu lassen;
- ein redacted Path-Health-Endpoint validiert Mapping-Syntax, gemountete
  Target-Roots und offene Import-Pfade ohne Server-Pfade zurückzugeben;
- `lib2_upgrade_scan` nutzt bewusst weiterhin `mirror_tracks_wishlist` als
  Compatibility-Adapter in die normale Wishlist/Main-Pipeline. Er wählt nur
  monitored Tracks unter `until_top`/`until_cutoff`, re-evaluiert das
  primäre File gegen den Cutoff und trägt die exakte Profil-ID.

**Noch offen, bevor LIB2-011/Phase 5 als komplett gilt:**
- gecachte Candidates, used/exhausted Sources und automatische
  Next-Candidate-Continuation nach einem Prozess-Neustart persistieren oder
  rekonstruieren. Aktuelle Persistenz verhindert Blind-Redispatch der
  quarantänten Datei und bewahrt Manual Approval, rekreiert aber nicht die
  In-Memory-Candidate-Liste des alten Workers;
- die Old-vs-Library-v2-Paritäts-Matrix für echtes Client-Verhalten
  erweitern. Die komplette Python-Suite ist grün (8031 bestanden, 7
  übersprungen, 2 deselektiert);
- echte SAB/NZBGet-, gemountete Path-Mapping- und Docker-Restart-
  Acceptance-Tests durchführen (die read-only Health-API ist implementiert;
  echte Deployment-Acceptance ist es nicht);
- erst während des späteren globalen Wishlist-Cutovers den
  Compatibility-Wishlist-Output durch direkte Acquisition Requests ersetzen.
  Das nicht früher tun, wenn es das etablierte Wishlist/Main-Pipeline-
  Verhalten umgehen oder duplizieren würde.

Correction-Commits: `e1272be`, `e6484cb`, `2917f3c`, `99ffd2c`, `7d80e96`,
`e394e2d`, `39549f0`, `e27070f`, `3eb0e92`, `a7344e5`, `6bc4d01`, `b464543`,
`903cbd3`.

### 5.6 Verifikation (pro Phase, End-to-End in Docker)

Lokales Image bauen (`docker build -t soulsync:dev .`), mit der realen
Config+DB-Kopie des Users + gemounteter Musik laufen lassen (Cover kommen aus
Embedded Art, daher zählt der Mount). Nach jeder Phase: `pytest tests/library2/`
grün + manueller UI-Check + DB-Spot-Checks (Watchlist-/Wishlist-Rows erscheinen
beim Monitor-Toggle; Artwork lädt ohne erreichbaren Media-Server; Downloads
importieren in `lib2`). Die alte Library + Watchlist/Wishlist-Seiten bleiben
während dessen funktionsfähig.

---

## 6. Deep-Dive-Review des Branches `library-overhaul` (Stand 2026-07-06) — historisch, alle Findings gefixt

> **Status: alle B1–B9 und M1–M16 (außer M8, bewusst Roadmap) wurden am
> 2026-07-07 gefixt** — siehe Abschnitt 3 „2026-07-07 Review-Fix-Pass" für die
> konkreten Fixes. Dieser Abschnitt bleibt als historischer Record der
> Review-Methodik, Begründungen und Architektur-Vorschläge (A1–A7) erhalten.

Vollständige Prüfung aller Code-Änderungen des Branches gegen den Plan und
`core/library2/STATUS.md`. Gelesen wurde **jede Zeile** der lib2-Kernmodule,
der API, des Frontends und aller Integrationspunkte (web_server, Repair-Worker,
Watchlist-Scanner, Import-Pipeline). Es wurden **keine Code-Änderungen**
vorgenommen (reines Review).

### 6.0 Scope-Klarstellung: Was ist eigentlich der Diff zu `dev`?

`git diff dev...library-overhaul` umfasste ~26.000 Zeilen in 179 Dateien —
irreführend, weil `dev` (Merge-Base `cd0279a4`) weit hinter `main` lag. Der
Diff enthielt drei Kategorien: (1) bereits gemergte Arbeit (Quality-Profiles
PR #974, Discover-Adventurousness-Dial, Artist Web Graph, JioSaavn, diverse
Fixes — nicht Gegenstand dieses Reviews), (2) die eigentliche
Library-v2-Arbeit (8 Commits `7e8efcfd..be0c0658`, ~11.000 Zeilen in 60
Dateien), (3) kleine geteilte Integrationsänderungen (publish_date in den
Download-Plugins, Repair-Job-Scope, Watchlist-Scanner-Hook). Das Review
konzentrierte sich auf (2) und (3). Empfehlung fürs PR: gegen aktuellen
`main`/`dev` rebasen oder den PR explizit als „nur die 8 library-v2-Commits"
schneiden.

### 6.1 Plan-Abgleich — Kern-Designregeln

| Regel | Status (2026-07-06) | Anmerkung |
|---|---|---|
| Nie Media-Server-abhängig (inkl. Artwork) | ⚠️ 95% | Artwork-Subsystem sauber. **Aber:** EPs bekamen im Artist-Detail keine lokale Artwork-URL → Legacy-`thumb_url` konnte durchsickern (B1, seit gefixt). |
| Monitoring ⇄ Watchlist/Wishlist via interne Calls | ✅ | Commit-vor-Mirror-Ordnung überall korrekt eingehalten. |
| App-weite `quality_profiles`, nie Parallelkopie | ✅ | `_migrate_lib2_profiles_to_app_wide` konvergiert Altbestände. |
| DB als Source of Truth, Datei-Location pro File | ✅ | `lib2_track_files` mit eigenem Row-Lifecycle. |

**Phasen-Status damals:** Phase A fertig; Phase B fertig; Phase C weitgehend
fertig; Phase D teilweise (fehlte: allgemeines Metadaten-Edit; Delete-mit-
Datei-Löschung bewusst nicht); Phase E NICHT plangemäß (Search Monitored war
Blind-Auto-Grab statt Wishlist-Processing — seit gefixt, siehe B6); Playlists
unbegonnen (ok, war zuletzt geplant). Discography/monitor_new_items/
periodischer Upgrade-Scan (nicht im Original-Plan, in STATUS.md nachgeführt):
implementiert und größtenteils solide.

**Bewusste, dokumentierte Abweichungen (in Ordnung, aber festgehalten):**
- `lib2_*`-Tabellen werden für ALLE Installs angelegt (unconditional in
  `music_database._initialize_database`), nicht nur bei aktiviertem Flag —
  anders als der separate `quality-profiles`-Branch, der verifiziert, dass ein
  Fresh-Install keine `lib2_*`-Tabellen bekommt. Vertretbar, aber im
  PR-Text festzuhalten.
- Artwork-Cache liegt unter `<db_dir>/lib2_artwork/` mit eigenem Endpoint statt
  (wie im Plan zuerst skizziert) im ImageCache — die STATUS.md-Variante ist
  die bessere (Lidarr-MediaCover-artig).

### 6.2 Gefundene Fehler (Stand 2026-07-06, alle seit gefixt — siehe 3.)

**Hohe Priorität:**
- **B1** — EPs bekamen keine lokale Artwork-URL (Media-Server-Leak möglich).
  `api/library_v2.py:136`: die Iteration über Albums+Singles vergaß
  `data["eps"]`. Fix: `+ data.get("eps", [])`.
- **B2** — `_profile()` in Background-Threads fiel immer auf Profil 1 zurück.
  `get_current_profile_id` liest `g.profile_id` und wirft außerhalb des
  Request-Kontexts → Fallback 1. Bulk-Monitor- und Upgrade-Scan-Threads riefen
  `_mirror_tracks_wishlist` → `_profile()` im Thread auf, statt das Profil vor
  `threading.Thread(...)` aufzulösen und durchzureichen (wie der
  Import-Endpoint es korrekt machte). Führte zu verwaisten Wishlist-Einträgen
  auf Multi-Profil-Installs.
- **B3** — Refresh invalidierte Thumbnails nicht.
  `api/library_v2.py:972-986`: `/refresh` löschte nur `artwork_file`, nicht
  `thumb_file`. Der Thumb-Fastpath servierte danach dauerhaft veraltete Cover.
- **B4** — Pfad-Auflösung inkonsistent: `scan.py`, `retag.py`,
  `lib2_skips_cleanup` nutzten rohe DB-Pfade statt (wie `artwork.py`) über
  `resolve_library_file_path` aufzulösen. Auf path-gemappten Setups zählte
  ALLES als „missing"; `lib2_skips_cleanup.py:97` löschte sogar den gesamten
  Skip-Audit, weil jede Row als verwaist galt.
- **B5** — `expected_track_count`-Clobbering-Kette: Wishlist-Seeding konnte
  Discography-Alben auf 1 Track stutzen. `importer.py:664-684` setzte für
  Alben ohne Files/`legacy_album_id` `expected_track_count` auf
  `COUNT(lib2_tracks)` — traf ein Wishlist-Track auf ein Discography-Album
  (Provider sagte z.B. 12 Tracks), wurde das auf 1 geklemmt, `completeness.py`
  trimmte die Provider-Tracklist entsprechend.
- **B6** — „Search Monitored" war semantisch falsch (und potenziell
  gefährlich): `library-v2-page.tsx:1044-1072` rief `autoGrabBest(artistName)`
  auf — eine Source-Suche nach dem bloßen Artistnamen, bestbewertetes
  Ergebnis (ggf. ein beliebiges Album!) sofort heruntergeladen. Der Plan
  definierte „Search Monitored" korrekt als Trigger von
  `POST /api/wishlist/process` + Watchlist-Scan.
- **B7** — Interactive Search: Grab-Status-Key stimmte für Album-Ergebnisse
  nicht. `interactive-search.tsx:341` schrieb den Status unter
  `${username}::${r.filename}`, gelesen wurde er über `resultKey()`, das für
  Alben `album_path ?? album_title` nutzt — Button zeigte nie „Grabbed ✓" bei
  Alben, erlaubte Doppel-Grabs.
- **B8** — Autolink ließ ein Album mit Datei unsichtbar, wenn es
  `origin='discography'` und `monitored=0` war. `_find_or_create_album`
  matchte bestehende Discography-Rows (gut), setzte aber weder
  `origin='library'` noch berücksichtigten `_ARTIST_STATS`/`visibleReleases`
  „hat Dateien" als Sichtbarkeitskriterium.
- **B9** — Profil-Zuweisung mit Upgrade-Policy re-monitored bewusst
  unmonitorte Tracks. `api/library_v2.py:379-402`: Beim Zuweisen eines
  `until_top`/`until_cutoff`-Profils auf Artist/Album wurde `monitored=1` auf
  ALLE Tracks gesetzt — auch auf per Manage-Tracks konsolidierte, bewusst
  abgewählte Varianten.

**Mittlere Priorität:**
- **M1** — Artist-Split-Heuristik zerlegte Bandnamen (`_LIST_SEP_RE` splittete
  auch an `&`/`and`/`x`/`+`, z.B. „Simon & Garfunkel" → Geister-Artist-Rows).
- **M2** — Totes Update im Wishlist-Seeding
  (`image_url = COALESCE(NULLIF(image_url, ''), image_url)` — Tautologie).
- **M3** — `monitor_new_items`-Erkennung hing an „existiert noch eine
  pristine Discography-Row" — brauchte einen expliziten Marker.
- **M4** — `monitor_new_items` wurde nur bei manuellem „Update Discography"
  durchgesetzt, kein periodischer Re-Expansion-Job.
- **M5** — `quality_profile_id` DEFAULT 1 hart kodiert (Schema-Defaults +
  diverse `or 1`-Fallbacks) — riskant, wenn Profil 1 gelöscht wird.
- **M6** — Missing-Slot-Platzhalter ignorierten Multi-Disc (nur `disc 1, n`
  generiert).
- **M7** — Index-Statistiken zählten materialisierte, unmonitorte
  Provider-Tracks (reines Browsen erhöhte „missing"-Badge).
- **M8** — Bulk-Job-Singleton: `_job_state` war EIN globales Dict für
  Bulk-Monitor/Upgrade-Scan/Retag — für Single-User ok, **bewusst als
  Roadmap-Punkt offen gelassen** (siehe Abschnitt 7, Punkt „Job-Registry").
- **M9** — `write_tags`/Preview kappten still bei 500 Tracks
  (`MAX_TRACKS` in `retag.py:23`), UI zeigte den Hinweis nur im Preview-Fall.
- **M10** — Provenance-Fallback machte Table-Scans pro Track
  (`_download_provenance_for_path` in `queries.py:240`).
- **M11** — O(N)-Python-Scans über alle Artists
  (`autolink._find_or_create_artist`, `profile_lookup`).
- **M12** — Kein Debounce am Index-Suchfeld.
- **M13** — Artwork-Slow-Path ohne Dedup/Lock (Thundering Herd auf
  Deezer/CAA/iTunes nach Cache-Bust).
- **M14** — `search.album`-Parameter war toter Code (Zod-Schema definiert,
  aber `LibraryV2Page` wertete nur `search.artist` aus).
- **M15** — `lib2_manual_skips`-Audit schrieb auch bei deaktiviertem Flag
  (`web_server.py:6861-6879`), und nur im Track-Zweig, nicht bei Album-Grabs.
- **M16** — `retag._track_rows`: `GROUP BY t.id` mit LEFT JOIN wählte bei
  Tracks mit mehreren `lib2_track_files`-Rows eine willkürliche Datei.

**Kleinigkeiten:**
- `lib2_list_artists`: `int(request.args.get("page", 1))` ohne try → `?page=abc`
  gab 500 statt 400.
- History-Matching nur `lower(track_artist) = lower(name)` — Multi-Artist-
  Strings („A feat. B") matchten nicht.
- Delete Artist/Album räumte Artwork-Cache-Dateien nicht auf (nur Müll, kein
  Falschbild-Risiko dank AUTOINCREMENT).
- Search-Query für titellose Missing-Rows wurde zu „Artist Track 5"
  (Label-Fallback nur teilweise gestrippt).
- Deezer-Tracklist-Fallback (Suche nach Artist+Titel) kann eine falsche
  Edition treffen — best-effort, bewusst, als bekannte Grenze dokumentiert.
- „Interactive Search"/„Search Monitored" auf Artist-Ebene waren deaktiviert,
  solange der Artist nicht monitored war — für reine *Suche* eine unnötige
  Hürde (Lidarr erlaubt Interactive Search immer).
- `reset=True`-Import löschte `lib2_manual_skips` bewusst nicht (Audit) — ok,
  aber nirgends dokumentiert.
- `importer.seed_wishlist_tracks` setzte `monitored=0` auf bestehende
  Artists; korrekt nur, weil `apply_monitoring_from_watchlist_wishlist`
  danach lief — reihenfolge-gekoppelt, ohne dass es irgendwo stand.

### 6.3 Architektur-Beobachtungen & Verbesserungsvorschläge

- **A1 — Ein gemeinsamer Datei-Resolver für lib2.** `artwork.py` hatte
  `_resolve_abs`; scan/retag/skips-cleanup/autolink brauchten dieselbe Logik.
  Ein `core/library2/paths.py::resolve(db_path, config_manager)` beseitigt B4
  strukturell. **→ Umgesetzt** als `paths.py::resolve_lib2_path` im
  2026-07-07-Pass.
- **A2 — Sichtbarkeits-/„in library"-Regel an genau einer Stelle.** Die Regel
  „origin == 'library' OR monitored" existierte dreimal unabhängig: SQL
  (`_ARTIST_STATS`), Python (`get_artist._in_library`), TypeScript
  (`visibleReleases`). B8 zeigte, wie die Kopien auseinanderlaufen konnten.
  Vorschlag: die API liefert ein berechnetes `in_library`-Flag pro Release,
  UI und Stats konsumieren nur noch dieses Feld.
- **A3 — Job-Registry statt zweier Modul-globaler Dicts.** `_import_state`/
  `_job_state` sind faktisch ein Ein-Slot-Scheduler. Ein Mini-Registry
  (`{job_id: state}`, Endpoint `/jobs/<id>`) macht Bulk-Monitor, Retag und
  Upgrade-Scan parallel möglich, beseitigt M8 und ist Voraussetzung für
  Multi-User. Alternativ: die Jobs in den bestehenden RepairWorker hängen.
  **Weiterhin offen** (siehe Abschnitt 7).
- **A4 — Wishlist→Autolink über `lib2_track_id` schließen.**
  `wishlist_mirror` legt `_source_info.lib2_track_id` auf die Wishlist-Row —
  aber `autolink` matcht den fertigen Download wieder per
  Namens-Normalisierung. Wenn der Download-Kontext die `source_info` bis in
  `record_download_provenance` trägt, könnte Autolink deterministisch auf die
  genaue lib2-Track-Row linken, statt heuristisch zu matchen — eliminiert
  eine ganze Fehlerklasse (falsches Album, Compilation-Zuordnung,
  Titelvarianten). **Weiterhin offen.**
- **A5 — Importer-Skalierung.** Der Importer arbeitet row-by-row mit vielen
  Einzel-SELECTs. Für die 285-Track-Referenzbibliothek egal; für
  100k-Track-Bibliotheken wären es Minuten im Write-Lock. Für Fremdnutzer:
  `executemany`-Batches, vorgeladene Count-Maps, Progress-Callback behalten.
  **Weiterhin offen.**
- **A6 — Legacy- vs. lib2-Datenbasis der Repair-Jobs explizit machen.** Die
  per-Artist gescopten Jobs (Gap-Fill, Tag-Consistency, Library-Retag)
  scannen Legacy-Tabellen. Solange der Legacy-Import die Quelle ist,
  deckungsgleich — aber autolink-erzeugte lib2-Rows ohne Legacy-Pendant sieht
  keiner dieser Jobs. Mittelfristig brauchen die Jobs eine lib2-Datenquelle
  oder lib2 einen Rück-Sync. **Weiterhin offen, als bekannte Grenze
  festzuhalten.**
- **A7 — `until_top`-Wording im Code vereinheitlichen.** `is_upgrade_policy`
  akzeptiert beide, `evaluate_file` behandelt `until_top` als Cutoff 0 —
  korrekt, aber Docstrings/API-Doku nennen nur eine Variante. Kosmetik.

### 6.4 Positives (bewusst festgehalten, Stand 2026-07-06)

- Die vier Kern-Designregeln fast überall konsequent umgesetzt — insbesondere
  die Commit-vor-Mirror-Reihenfolge (SQLite-Lock-Gotcha) an allen sechs
  Stellen korrekt, inkl. Kommentar.
- `wishlist_mirror` als geteilte Implementierung (Button + Repair-Job) ist
  genau die richtige Abstraktion — Queueing-Regeln können nicht driften.
- Fail-open-Disziplin: Autolink, Profile-Lookup, Artwork, Completeness —
  nichts davon kann die Pipeline oder den Request-Pfad crashen; überall
  try/except + Debug-Log statt Raise.
- Der Extraktions-Schnitt zum `quality-profiles`-Branch (eigene
  `core/quality/schema.py`, Live-Resolution statt Snapshot-Spalten) war
  sauber und zahlt sich hier aus: lib2 hängt nur noch am Pointer.
- Ehrliche UI: Buttons ohne Backend wurden entfernt statt als Dead-Placeholder
  gelassen; Maintenance-Modal kennzeichnet Scope ehrlich; Delete-Dialoge sagen
  explizit „Files on disk are not deleted".
- Testabdeckung der Core-Module gut: 70 lib2-Tests + Job-Scope-Tests, alle
  grün (2026-07-06); angrenzende Suiten (quality/imports/wishlist, 971 Tests)
  ebenfalls grün.

### 6.5 Test- & Verifikationsstatus (Stand 2026-07-06)

| Suite | Ergebnis |
|---|---|
| `tests/library2` + `tests/repair/test_job_scope.py` | 70 passed |
| `tests/quality`, `tests/imports`, `tests/wishlist` | 971 passed |
| Flask-Routen `api/library_v2.py` | keine Tests (Lücke — seit geschlossen, siehe 3.) |
| Frontend | kein lokales Node; Typecheck via `docker build --target webui-builder` |
| End-to-End | in Docker gegen die reale ~285-Track-Bibliothek verifiziert; Phase-C/D-Klick-Flows teilweise nur code-verified |

**Empfohlene manuelle Docker-Checks (damals vor dem nächsten Merge, jeweils
mit Fund-Referenz):** Multi-Profil-Install-Bulk-Monitor (B2); Setup mit
Path-Mapping (B4); EP im Artist-Detail (B1); Refresh & Scan Thumb-Update (B3);
Wishlist-Track auf unowned Discography-Album (B5); „Search Monitored" auf
vollständiger Library (B6). Alle sechs sind seit dem 2026-07-07-Pass gefixt.

---

## 7. Offene Roadmap (konsolidiert, nicht vergessen, nicht versehentlich „fixen")

Aus Plan + STATUS + Review, priorisiert:

1. **Phase 5 fortsetzen**: zentraler Client-Monitor mit Category-Adoption,
   dann edition-aware Bundle-Inventory/Matching, persistente
   `acquisition_imports`, Manual Import für ambige Bundles (siehe Abschnitt
   5.5 für Detailstatus/offene Punkte).
2. **LIB2-011-Findings zuerst schließen** (Abschnitt 5.4, F01–F08), bevor
   weitere Acquisition-Features gebaut werden.
3. Bestehende Interactive-/Wishlist-Consumer auf den Acquisition-Contract
   umstellen; erst danach global durchsetzen, dass kein Download ohne
   AcquisitionRequest startet.
4. Phase-3-Identity/Provenance fertigstellen: dedizierte externe-/
   Old-ID-History, Merge-/Move-History, Field-Level-User-Overrides und
   Read-Projection. Typed Adapters über Discography/Tracklist hinaus
   erweitern.
5. Gestaffelten Wanted-Cutover fertigstellen: Consumer, die noch
   `monitored`-Flags nutzen, müssen nach Drift-Metriken-Beweis der Parität
   auf `lib2_wanted_tracks` wechseln.
6. **Monitor-Provenance**: Album-Monitoring, das Re-Imports unabhängig vom
   Track-Wishlist-Zustand überlebt (Provenance-/Mode-Spalten statt
   Ableitung).
7. **Job-Registry** (M8/A3): heute teilen sich Bulk-Monitor/Retag/
   Upgrade-Scan EINEN globalen Job-Slot (`_job_state`) — vor Multi-User-
   Nutzung auf Job-IDs umstellen.
8. **Breiteres Metadaten-Edit** (Titel/Jahr/Artists) über den
   Release-Type-Edit hinaus; deep-linkbare Album-Detail-Ansicht.
9. **Artist-Scope für Reorganize/Dedup** (brauchen Pfad-Scoping, kein
   SQL-Filter — im Maintenance-Modal laufen diese Jobs derzeit ehrlich
   gekennzeichnet library-wide).
10. **Album-Detail-Deep-Link** (`search.album`-Parameter, M14) implementieren
    oder entfernen.
11. **Wishlist→Autolink über `lib2_track_id`** deterministisch schließen
    (A4).
12. **Importer-Skalierung** für sehr große Libraries (A5).
13. **Legacy- vs. lib2-Datenbasis der Repair-Jobs** explizit machen (A6).
14. **Playlists** (Phase E, bewusst zuletzt).
15. **Browser-Klick-Verifikation** in Docker für Phase-C/D-Flows, die nur
    code-/curl-verified sind.

---

## 8. Retry-Persistenz nach Quality-/Integrity-/AcoustID-Fehlern (Spec, noch nicht implementiert)

Dieser Punkt ist bewusst als eigener Folgejob offen (Teil von LIB2-F07). Der
bestehende Worker besitzt Kandidatenliste, `used_sources`, erschöpfte Quellen
und Retry-Zähler bisher nur im RAM. Für Library-Acquisition muss dieser
Zustand nach einem Neustart wiederherstellbar werden, ohne die bestehende
Auswahl- oder Retry-Logik zu duplizieren.

**Geplante Umsetzung:**
- Ein kurzlebiges Retry-Journal pro Acquisition-Task und Track speichern.
- Nur redigierte Kandidatenfelder speichern: Quelle, Dateiname,
  Qualitätsdaten und Reihenfolge; keine URLs, Magnet-Links, Tokens oder
  Provider-Geheimnisse.
- `used_sources`, erschöpfte Source-Buckets, Source-spezifische
  Retry-Zähler, Gesamtversuche, letzter Fehler und letzter Fortschritt
  persistieren.
- Beim Start oder beim nächsten Acquisition-Worker-Lauf den bestehenden
  Legacy-Task mit diesem Zustand wiederherstellen und anschließend
  ausschließlich den vorhandenen `task_worker`/`monitor`-Retry ausführen.
- Nach Erfolg, manuellem Approve oder endgültiger Erschöpfung den Zustand
  als abgeschlossen markieren.
- Detaillierte Retry-Zeilen nach einer kurzen Retention automatisch löschen
  (vorgesehener Standard: sieben Tage); die dauerhafte Acquisition-History
  behält nur das fachliche Ergebnis und den Grund.
- Abbruch und Cancel müssen den Retry-Zustand beenden und dürfen keinen
  automatischen Neustart auslösen.

**Abnahmekriterien:**
- Neustart nach Quality-Quarantäne setzt mit dem nächsten Kandidaten fort
  und lädt nicht erneut dieselbe Quelle.
- Source-Priority, `best_quality`, `hybrid_order`, Torrent-/Usenet-Budgets
  und manuelle Kandidatenauswahl verhalten sich identisch zum Legacy-Pfad.
- Approve überspringt weiterhin nur den bestätigten Check; die übrigen
  Checks laufen erneut.
- Terminale Requests hinterlassen keine unbegrenzt wachsenden Worker-Daten.

---

## 9. Reused Assets (nicht neu bauen) — Quick-Index

- **Watchlist:** `database/music_database.py`
  `add_artist_to_watchlist`/`remove_artist_from_watchlist`/
  `is_artist_in_watchlist`; Scanner `core/watchlist_scanner.py`.
- **Wishlist:** `database/music_database.py`
  `add_to_wishlist`/`remove_from_wishlist`; `core/wishlist/service.py`;
  Processor `POST /api/wishlist/process`.
- **Artwork:** `core/metadata/art_apply.py`, `core/metadata/artist_image.py`,
  `core/metadata/art_lookup.py`, `core/metadata/artwork.py`,
  `core/image_cache.py`, `core/library/artist_image.py`,
  `core/library/path_resolver.py`.
- **Search/Download:** `core/search/orchestrator.py`,
  `core/download_orchestrator.py`, `core/downloads/task_worker.py`, Routen
  `/api/manual-search/<id>`, `/api/download`,
  `/api/download-selected-candidate/<id>`.
- **Tagging/Repair:** `core/tag_writer.py`, `core/repair_jobs/*`,
  `core/imports/pipeline.py`.
