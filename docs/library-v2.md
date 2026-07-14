# Library Manager v2 — Konsolidierte Doku (Plan, Status, Review)

> Zusammengeführt am 2026-07-13 aus: `docs/library-v2-context.md`,
> `docs/library-v2-plan.md`, `docs/library-v2-branch-review-2026-07-06.md`,
> `core/library2/STATUS.md` (alle vier danach gelöscht). Diese Datei ist jetzt
> die einzige Quelle für Library-v2-Kontext, -Plan, -Status und -Review-Historie.
> Letzter inhaltlicher Stand der Quellen: 2026-07-12 (Phase 4/5 Acquisition,
> LIB2-011 Findings); am 2026-07-13 ergänzt um Abschnitt 4.5 (Main-Pipeline-
> Hardening-Split, unabhängig von Library v2 vorzuziehen); am 2026-07-14
> ergänzt um Abschnitt 5.3.1 (Umbenennung „Decision Engine" →
> `Entity-Eligibility-Gate`) und 5.3.2 (Force-Grab↔Quarantäne-Brücke, Teil
> von F06); am 2026-07-13 ergänzt um Abschnitt 10 (ADR-Log + Findings-Nachtrag
> aus `docs/library-v2-architecture-audit-2026-07-10.md`, danach gelöscht —
> diese Datei war die letzte verbleibende separate Library-v2-Doku); am
> 2026-07-13 Abschnitt 8 (Retry-Persistenz) von Spec auf implementiert
> gestellt (F07 geschlossen bis auf Deployment-Acceptance); am 2026-07-13
> die Force-Grab↔Quarantäne-Brücke aus 5.3.2 umgesetzt (F06 geschlossen,
> Commit `6ea7f3e2`); am 2026-07-14 ergänzt um Abschnitt 11 (Playlist-
> Quality-Profile-Konfliktauflösung — bewusst zurückgestellt; korrigiert
> nebenbei ein separat vom User eingebrachtes, veraltetes externes
> Findings-Dokument zu Playlists/Artwork, dessen Kernannahmen — Playlists
> unbegonnen, P2-05 offen — beim Gegenprüfen gegen den Code bereits
> überholt waren).

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
  path-gemappte Setups). Die letzte bekannte Ausnahme in Artwork wurde am
  2026-07-14 geschlossen (Roadmap-Punkt 22 / P2-05).
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
- Discography-, Spotify/Deezer-Tracklist- und Artwork-Fallbacks queren eine
  typed Adapter-Boundary. Provider-spezifische Response-Dicts verlassen
  `provider_adapters.py` nicht. Provider-IDs werden exakt gematcht und
  strukturell gemerged; partielle Discography-Snapshots prunen nie Releases.
  Artwork benutzt weiterhin die eine gemeinsame Artist-/Cover-Art-Engine;
  der Adapter liefert lediglich URL, tatsächliche Quelle und Entity-ID typisiert
  an Library v2 zurück.
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
  `all` werden alle neu ENTDECKTEN Releases auto-monitored; `new` akzeptiert
  nur Releases mit bekanntem Datum, das strikt nach dem neuesten bereits vor
  dem Sync bekannten Release liegt. Undatierte Releases und spät gelieferte
  Backkatalog-Einträge bleiben bei `new` unmonitored. Der
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
- Album-Monitor-Intent wird bei jedem Re-Import aktiv aus
  `lib2_monitor_rules` auf die Kompatibilitätsspalte zurückprojiziert und ist
  damit unabhängig davon, welche Tracks gerade in der Legacy-Wishlist stehen.
  Auch `reset=True` bewahrt nicht-legacy Album-Regeln über Spotify-/
  MusicBrainz-/deterministische `stable_id` und bindet sie nach dem Rebuild an
  die neue lokale Album-ID; alte surrogate-ID-Regeln werden nicht mitgeschleppt.

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
  (alle effektiv wanted Missing Tracks sind bereits wishlist-gemirrort) statt Blind
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
- Manual und Automatic Search verwenden dieselbe versionierte Filter-/
  Override-Instanz mit Rejections, Warnings und deterministischem Ranking —
  ursprünglich „Decision Engine" genannt, seit 2026-07-14 umbenannt zu
  **`Entity-Eligibility-Gate`** (Abschnitt 5.3.1), weil ihr tatsächlicher
  Scope nach der F01-Korrektur nur noch Edition/Entity-Match + Force-Grab
  ist — Quelle/Qualität entscheidet die geteilte Main-Pipeline. Force Grab
  ist Admin-only, übergeht nur ausdrücklich overridable Policy-Reasons und
  schreibt Audit-History; siehe 5.3.2 für die Force-Grab↔Quarantäne-Brücke.
- Prowlarr liefert im neuen Pfad nur Release-Bundles. Search läuft außerhalb
  langer SQLite-Transaktionen; einzelne Source-/Parse-Fehler bleiben isoliert.
- `lib2_wanted_tracks` kann RecordingRequests idempotent als ADR-02-Shadow
  materialisieren. Dieser Shadow dispatcht bewusst noch keinen Download; die
  Legacy-Wishlist bleibt als abgeleitete Ausführungsliste operativ, ist aber
  nicht mehr Wanted-Source-of-Truth (Roadmap-Punkt 5).
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

**2. Usenet-Age/Retention-Guard (aus `core/acquisition/eligibility_gate.py:395-406`, vor dem 2026-07-14-Rename `decision_engine.py`) — kleinster, sicherster Fix**
Lehnt Usenet-Kandidaten ab, die jünger als eine konfigurierte
Propagation-Delay-Schwelle oder älter als eine konfigurierte
Retention-Schwelle sind. Feldnamen im Prototyp:
`policy.minimum_age_seconds` (Default `0` = deaktiviert) und
`policy.maximum_age_seconds` (Default `None` = unlimitiert) —
`eligibility_gate.py:84-85`. **Das gibt es aktuell gar nicht** in der
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
seit 2026-07-14 über die vorhandenen Mirrored-Playlist-Reads und die zentrale
Playlist-Pipeline in Library v2 integriert.)

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

### 5.3.1 Umbenennung: „Decision Engine" → `Entity-Eligibility-Gate` (2026-07-14)

Nach der F01-Korrektur entscheidet dieses Modul **nicht mehr** über Quelle
oder Qualität — das läuft komplett durch den geteilten Source-Policy-Resolver
und das geteilte Quality-Profile-Gate (dieselbe Main-Pipeline-Logik, die auch
Legacy-Wishlist/Interactive-Search nutzen). Der Name „Decision Engine" ist
danach irreführend, weil er eine zweite Entscheidungsinstanz suggeriert —
genau das Muster, das F01 als Fehler markiert hat. Der Modulname wird daher
konzeptionell (und bei der nächsten Implementierung auch im Code:
`core/acquisition/decision_engine.py` → `core/acquisition/eligibility_gate.py`)
umbenannt zu **`Entity-Eligibility-Gate`**. **Code-Rename umgesetzt
2026-07-14:** Modul + Klasse (`DecisionEngine` → `EligibilityGate`) + alle
Imports/Tests/Fehlertexte; `test_decision_engine.py` →
`test_eligibility_gate.py`. Bewusst NICHT umbenannt: das persistierte
`candidate_decisions.engine_version`-Datum (`acquisition-decision/1`) und die
Persistenz-Module `decisions.py`/`requests.py`/`history.py` (siehe unten).
Sein tatsächlicher, schmaler Scope nach der Korrektur ist nur noch:

1. **Edition/Entity-Match** — passt ein von der geteilten Pipeline bereits
   nach Quelle/Qualität akzeptierter Kandidat zur *genau* angefragten Edition
   dieser Acquisition-Request (Tracklist-Länge, Release-Type, gewählte
   Edition-ID)? Das kann die Main-Pipeline nicht wissen, weil sie nichts von
   Editions-Identität hat — sie kennt nur „Suche → bestes Ergebnis".
2. **Admin Force-Grab** — ein gezielter, auditierter Override eines
   einzelnen, ausdrücklich „overridable" Ablehnungsgrundes (siehe 5.3.2 für
   die konkrete Anschluss-Regel an die Quarantäne).

Die persistente Buchführung (Acquisition-History, Request↔Grab↔Import-
Korrelation) bleibt bewusst ein eigenes Modul (`core/acquisition/history.py`,
`requests.py`) und ist NICHT Teil des Eligibility-Gate — das Gate filtert nur,
es protokolliert nicht selbst.

### 5.3.2 Force-Grab ↔ Quarantäne-Brücke (umgesetzt 2026-07-13, Teil von F06)

Offene Frage, die die bisherige Spec nicht beantwortet hatte: Ein Admin
forced einen Grab trotz eines Quality-Profile-Ablehnungsgrundes (z.B. „below
profile" / zu klein). Der fertige Download landet danach — weil die
Quality-Messung am echten File denselben Grund erneut bestätigt — in der
normalen Post-Download-Quarantäne. Muss der Admin diesen bereits bewusst
akzeptierten Grund ein zweites Mal manuell approven?

**Nein — erwartetes Verhalten:** Force-Grab schreibt den exakt übergangenen
Ablehnungsgrund (Reason-Code, z.B. `quality_below_profile`,
`size_too_small`) als Teil des persistenten Acquisition-Request/History-
Eintrags fest. Landet der Download in der Quarantäne und ist der
Quarantäne-Grund **derselbe Reason-Code**, der beim Force-Grab bereits
übergangen wurde, wird die Quarantäne **automatisch durchgewunken** (File
wiederhergestellt, geteilte Pipeline re-dispatcht) — ohne zweiten manuellen
Klick. Das ist keine neue Regel, sondern dieselbe Semantik wie das normale
Quarantäne-Approve nur vorgezogen: „Approval darf nur den spezifisch
approvten Check umgehen" (oben) gilt genauso für einen *vorab* erteilten
Approve.

**Wichtige Grenze:** Nur der exakt übergangene Reason-Code wird
auto-approved. Löst dieselbe Datei einen **anderen** Quarantäne-Grund aus,
den der Admin nicht explizit übergangen hat (z.B. Integrity-Fehler,
AcoustID-Mismatch, falscher Artist) — muss die Quarantäne ganz normal
manuell reviewt werden. Force-Grab ist kein Freifahrtschein für alle Checks,
sondern übergeht exakt einen benannten, im Voraus akzeptierten Grund.

**Warum das noch nicht existiert:** Das ist Teil der offenen Korrektur
LIB2-F06 (Abschnitt 5.4) — aktuell gibt es keine Brücke zwischen dem
Force-Grab-Override zum Such-/Auswahlzeitpunkt und der
Post-Download-Quarantäne-Entscheidung; ein Force-Grab, das denselben Grund
erneut auslöst, würde heute ein zweites Mal (unnötig) manuell landen.

**Abnahmekriterium (Ergänzung zu F06):** Ein Force-Grab mit übergangenem
Reason-Code X, dessen Download post-download denselben Reason-Code X
auslöst, muss automatisch aus der Quarantäne freigegeben werden. Ein
Force-Grab mit übergangenem Reason-Code X, dessen Download einen anderen
Reason-Code Y auslöst, muss normal in der Quarantäne pausieren.

**Status 2026-07-13: implementiert** (`6ea7f3e2`). Der bestehende
Main-Pipeline-Quality-Guard meldet einen echten File-Reject als
`quality_not_allowed` an
`pipeline_callback.notify_force_quarantine_auto_approved`. Die Bridge
autorisiert nicht anhand des serialisierten Pipeline-Kontexts, sondern prüft
fail-closed direkt in der DB: Import↔Grab↔Decision-Run müssen zusammengehören,
der Run muss `forced=1` sein und exakt derselbe Reason-Code muss dort als
`rejection` + `overridable=1` persistiert sein. Zusätzlich muss der Track zum
persistierten Import-Plan gehören. Nur dann wird der bereits erteilte Approve
als append-only History-Event `force_quarantine_auto_approved` verbucht und
das Quality-Gate fortgesetzt; AcoustID, Integrity und alle anderen Checks
laufen unverändert weiter. Bei anderem Code, normalem Grab, inkonsistentem
Kontext oder DB-Fehler greift unverändert die normale Quarantäne.

Die Implementierung konsumiert den vorab erteilten Approve direkt am
gemeinsamen Guard, bevor die Datei redundant physisch in Quarantäne verschoben
und sofort wiederhergestellt würde. Das ist dieselbe fachliche
Approve/Re-Dispatch-Semantik ohne einen verschachtelten zweiten Pipeline-Lauf
und ohne zweite Decision- oder Import-Implementierung.

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
erneut laufen. **Ergänzt 2026-07-14** (siehe 5.3.2): das schließt explizit die
Force-Grab↔Quarantäne-Brücke ein — ein bereits beim Force-Grab übergangener
Reason-Code muss bei erneutem Auftreten in der Quarantäne automatisch
durchgewunken werden, ohne zweiten manuellen Approve.
**Status 2026-07-13: geschlossen.** Acquisition-Kontext überlebt Sidecar und
Approval bereits im geteilten Main-Pipeline-Pfad; die noch fehlende exakte
Force-Grab-Brücke ist mit Commit `6ea7f3e2` umgesetzt und in 5.3.2
dokumentiert. Gezielte Tests beweisen exakten Code-Match, Ablehnung eines
anderen Codes, Ablehnung nicht erzwungener Runs und Import-Plan-Bindung.

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
**Status 2026-07-13: implementiert** gemäß der Spec in Abschnitt 8
(Retry-Journal + Restart-Resume, Commits `e3eca302`/`899536db`/`364262bf`);
offen bleibt nur die echte Docker-Restart-Acceptance (Teil des ohnehin
offenen Deployment-Acceptance-Punkts in 5.5) und die F08-Paritäts-Matrix.

**LIB2-F08 — Behavior-Parität durch Contract-Matrix absichern (P1).**
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
**Status 2026-07-13: Contract-Gate implementiert** (`d921c1eb`).
`tests/acquisition/test_legacy_parity_contract.py` speist beide Pfade mit
denselben Szenarien und vergleicht normalisierte Business-Outcomes für:

- Priority vs. `best_quality` (Selected Source + Candidate-Order);
- Quality-Accept/Reject gegen das gemeinsame Profilmodell;
- `acceptable`/`until_cutoff`/`until_top` gegen Legacy-Upgrade-Job und
  Library-v2-Evaluation;
- Quarantäne-, Per-Track-Completion- und globalen Request-Terminal-State;
- Next-Candidate-Retry mit identischem Legacy-Task-State (Acquisition ergänzt
  nur den Journal-Snapshot);
- Restart-Resume gegen den vor dem Neustart persistierten Walk.

Zusammen mit den F06-Tests für exakten Force-Reason-Match/abweichenden Grund
liefen 60 gezielte Tests grün. Die bewusst nachgelagerte Python-Fullsuite ist
am 2026-07-13 nach den unten dokumentierten Python-3.14-/Harness-Korrekturen
ebenfalls abgeschlossen: **8081 passed, 2 deselected, 400 warnings in
291.13s**. Damit ist das lokale F08-/LIB2-011-Meilenstein-Gate geschlossen;
echte Client-/Docker-Acceptance bleibt ein separater Deployment-Punkt.

Der erste Fullsuite-Anlauf am 2026-07-13 deckte zusätzlich eine Python-3.14-
Blockade im prozessweiten Sync-to-Async-Adapter auf: die erste
`run_coroutine_threadsafe()`-Einreichung konnte den Selector-Loop-Thread in
dieser Laufzeit nicht aufwecken. Commit `74ec9ceb` behält den persistenten
Einzel-Loop bei, übergibt Arbeit aber über eine threadsichere Queue und pinnt
den ersten Aufruf in einem frischen Prozess. Zwei Blocklist-Guard-Tests sind
außerdem vom nachgelagerten externen Download isoliert. 70 gezielte Tests für
Async-Bridge, Soulseek, Blocklist, Client-Monitor und Candidate-Store liefen
danach grün; zu diesem Zeitpunkt blieb der erneute Fullsuite-Lauf das
Meilenstein-Gate.
Der diagnostische Wiederholungslauf fand danach zwei reine Harness-Leaks:
`test_app` startete pro Test zwölf nie endende Socket.IO-Emitter-Threads (456
Threads bis 63 Prozent), und drei SoundCloud-Aggregat-Tests ließen ungemockte
Schwester-Clients Executor-I/O starten. Commit `297dc099` macht die bereits
durch `reset_state` isolierte Test-App sessionweit und mockt in diesen
Contract-Tests alle Aggregat-Plugins. 61 Socket.IO-/SoundCloud-Tests liefen
danach grün; die Produktionspfade wurden in diesem zweiten Fix nicht geändert.
Der nächste Lauf überschritt diese Stelle und diagnostizierte bei 76 Prozent
denselben Python-3.14-Selector-Wakeup-Effekt im lokalen Event-Loop von
`tests/test_prowlarr_client.py`: ein fertiger Executor-Job weckte den Loop
nicht zuverlässig. Commit `8ea30221` lässt den Test-Runner mit kurzen
Async-Ticks bis zur Task-Completion weiterlaufen und schließt den Loop danach;
alle 13 Prowlarr-Tests liefen in 0,17 Sekunden grün. Auch dieser Fix ändert
keinen Produktionspfad.
Der folgende Lauf erreichte 82 Prozent und fand denselben Effekt noch im
SoundCloud-Testhelper: `asyncio.run()` wartete beim Executor-Shutdown, obwohl
die gemockte yt-dlp-Arbeit fertig war. Commit `47ec6365` verwendet dort
denselben deterministischen Heartbeat-Loop mit direktem Close. Die komplette
Datei lief danach mit 48 bestandenen und zwei erwartungsgemäß abgewählten
Live-Tests grün; auch dieser Commit ändert nur Testinfrastruktur.
Der nächste Lauf erreichte 94 Prozent und traf denselben Executor-Shutdown im
kleinen YouTube-Leading-Dash-Regressionstest. Commit `70336a57` stellt dessen
zwei Async-Aufrufe ebenfalls auf den deterministischen lokalen Runner um; alle
drei Tests der Datei liefen danach in 0,15 Sekunden grün. Die getestete
yt-dlp-Query-Escaping-Produktionslogik blieb unverändert.
Ein weiterer Lauf diagnostizierte denselben Wakeup-Effekt im gemeinsamen
Test-Loop der SABnzbd-/NZBGet-Adapter. Commit `ee896e4d` verwendet auch dort
Heartbeat plus direkten Loop-Close; alle 46 Usenet-Adaptertests liefen danach
in 0,20 Sekunden grün. Die Adapter-Produktionslogik blieb unverändert.
Der anschließende vollständige Lauf von `pytest tests/` endete mit **8081
passed, 2 deselected, 400 warnings in 291.13s** und Exitcode 0.

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
- ein beim Force-Grab übergangener Quality-Reason wird am echten File-Gate nur
  bei identischem, im unveränderlichen Decision-Run persistiertem Reason-Code
  automatisch akzeptiert; andere Gründe gehen normal in Quarantäne
  (`6ea7f3e2`);
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
- ~~gecachte Candidates, used/exhausted Sources und automatische
  Next-Candidate-Continuation nach einem Prozess-Neustart persistieren oder
  rekonstruieren~~ — **erledigt 2026-07-13** (Abschnitt 8: Retry-Journal
  `acquisition_retry_state` + Resume im periodischen Import-Zyklus, Commits
  `e3eca302`/`899536db`/`364262bf`);
- ~~die Old-vs-Library-v2-Paritäts-Matrix erweitern~~ — **erledigt
  2026-07-13** (`d921c1eb`, 11 neue Contract-Szenarien; 60 relevante Tests
  grün). Die abschließende Python-Fullsuite ist ebenfalls grün (8081 passed,
  2 deselected; 291.13s);
- ~~echte SAB/NZBGet-, gemountete Path-Mapping- und Docker-Restart-
  Acceptance-Tests durchführen.~~ **Erledigt 2026-07-14:** echte isolierte
  SABnzbd-5.0.4- und NZBGet-26.2-Container bestehen Connection, NZB-Submit,
  Job-Status/Category und Remove über die produktiven SoulSync-Adapter. Dabei
  wurde verifiziert, dass SAB eine dort nicht angelegte Kategorie still auf
  `*` zurückschreibt; der Settings-Connection-Test validiert deshalb jetzt
  die konfigurierte SAB-Kategorie (`96c323a2`, 50 gezielte Tests + echter
  Client-Check). **Restart-/Mapping-Slice ebenfalls erledigt 2026-07-14:**
  der opt-in Contract `test_live_usenet_deployment.py` persistiert einen
  absichtlich unklaren Submit ohne externe Job-ID; ein neuer SoulSync-
  Container öffnet dieselbe DB, adoptiert den pausierten echten SAB-/NZBGet-
  Job über den zentralen Monitor und bestätigt das gemeinsame Bind-Mount über
  den produktiven Path-Resolver als `mapped/readable` (`00c57184`). Beide
  Prepare→Container-Ende→Verify-Flows sind real grün. **Inventory-/Review-
  Slice erledigt 2026-07-14** (`5ab9f726`): die Verify-Container führen den
  adoptierten echten Job mit einem providerfrei erzeugten, client-kompatiblen
  Completion-Snapshot und finalem Remote-Pfad weiter. Eine echte getaggte
  FLAC im gemeinsamen Mount wird über den produktiven Resolver inventarisiert;
  ein absichtlicher Edition-Mismatch persistiert `needs_review`, und die
  manuelle Track-Zuordnung persistiert anschließend `importing`. Das lief für
  SAB und NZBGet über getrennte echte SoulSync-Container grün. **Shared-
  Pipeline-Slice ebenfalls erledigt** (`abd70368`): dieselbe echte FLAC läuft
  danach ohne Test-Processor durch Stability, Integrity, Quality, AcoustID-
  Capability-Fallback, Tagging/Move und den persistenten Acquisition-Callback
  bis `completed`. SAB Prepare→Verify lief in 9,47 s, NZBGet in 11,94 s;
  85 angrenzende gezielte Tests sind grün. Ein tatsächlicher NNTP-Payload-
  Download bleibt ein credentials-abhängiger Deployment-Smoke-Test, ist aber
  kein Branch-Gate: Submit, Client-Korrelation/Adoption, terminaler Snapshot,
  Mapping, Inventory/Review und die reale Main-Pipeline sind vollständig
  abgenommen;
- erst während des späteren globalen Wishlist-Cutovers den
  Compatibility-Wishlist-Output durch direkte Acquisition Requests ersetzen.
  Das nicht früher tun, wenn es das etablierte Wishlist/Main-Pipeline-
  Verhalten umgehen oder duplizieren würde.

Correction-Commits: `e1272be`, `e6484cb`, `2917f3c`, `99ffd2c`, `7d80e96`,
`e394e2d`, `39549f0`, `e27070f`, `3eb0e92`, `a7344e5`, `6bc4d01`, `b464543`,
`903cbd3`, `6ea7f3e2`, `d921c1eb`, `74ec9ceb`, `297dc099`, `8ea30221`,
`47ec6365`, `70336a57`, `ee896e4d`.

**Session-Status 2026-07-14:** F06, F07, das F08-Contract-Gate und das lokale
LIB2-011-Meilenstein-Gate sind abgeschlossen. Der erste Fullsuite-Anlauf hat die
Python-3.14-Async-Bridge-Blockade gefunden; sie ist mit `74ec9ceb` behoben und
mit 70 gezielten Tests verifiziert. Der anschließende diagnostische Lauf hat
die Harness-Thread-/Client-Leaks gefunden; `297dc099` behebt sie und ist mit
61 gezielten Tests verifiziert. Der folgende Lauf überschritt die alte
Blockade und fand den isolierten Prowlarr-Testloop-Wakeup; `8ea30221` behebt
ihn mit 13 grünen Prowlarr-Tests. Der nächste Lauf fand bei 82 Prozent den
gleichen Executor-Shutdown im SoundCloud-Testhelper; `47ec6365` ist mit 48
bestandenen und zwei abgewählten Live-Tests verifiziert. Der folgende Lauf
fand bei 94 Prozent den letzten bekannten yt-dlp-Executor-Shutdown;
`70336a57` ist mit drei grünen YouTube-Regressionstests verifiziert. Ein
weiterer Lauf isolierte den gleichen Wakeup im Usenet-Adapter-Testloop;
`ee896e4d` ist mit 46 grünen SABnzbd-/NZBGet-Tests verifiziert. Der finale
Fullsuite-Lauf ist grün (8081 passed, 2 deselected; 291.13s).
Die ersten echten Deployment-Acceptance-Slices sind ebenfalls abgeschlossen:
SABnzbd 5.0.4 und NZBGet 26.2 wurden in isolierten Containern über die
produktiven Adapter verbunden, mit einem synthetischen NZB beschickt,
beobachtet und bereinigt. Der dabei gefundene SAB-Category-Fallback auf `*`
ist durch einen fail-closed Settings-Check behoben (`96c323a2`).
Der neue opt-in Deployment-Contract (`00c57184`) lief danach für beide Clients
über zwei echte SoulSync-Container: Prepare persistierte
`submission_unknown`, Verify adoptierte nach Container-Ende/-Neustart den
echten Job und verifizierte den gemounteten Pfad als `mapped/readable`.
Mit `5ab9f726` läuft derselbe Contract nach der Adoption providerfrei bis zum
Completion-/Import-Review-Vertrag: echter gemounteter FLAC-Output wird
inventarisiert, der Edition-Mismatch landet in `needs_review`, und eine
manuelle Zuordnung setzt denselben Import persistent auf `importing`.
`abd70368` führt ihn anschließend durch die unveränderte Main-Pipeline bis zum
persistenten `completed`-Callback; beide echten Client-/Container-Varianten
sind grün. Ein vollständiger NNTP-Payload-Download bleibt mangels Provider-
Credentials ein optionaler Deployment-Smoke-Test, nicht mehr das lokale
Phase-5-Gate. Der abschließende Fullsuite-Lauf nach allen Phase-5-
Acceptance-Änderungen ist ebenfalls grün: **8085 passed, 2 skipped,
2 deselected, 299 warnings in 211.79s**. Die beiden Skips sind die bewusst
opt-in markierten Live-Deployment-Varianten, die separat für SABnzbd und
NZBGet in echten Zwei-Container-Flows bestanden haben. Frontend-Code wurde in
dieser Etappe nicht verändert; deshalb waren keine Frontend-Gates erforderlich.
**Logischer nächster Schritt:** Roadmap-Punkt 3 — bestehende Interactive-/
Wishlist-Consumer schrittweise auf den Acquisition-Contract umstellen, dabei
weiterhin Source-Auswahl, Retry, Quarantäne und Import ausschließlich aus der
geteilten Main-Pipeline beziehen.

**Session 2026-07-14 (Fortsetzung):** Der 5.3.1-Code-Rename ist umgesetzt
(`849a64cc`: `eligibility_gate.py`/`EligibilityGate`, 223 Acquisition-Tests
grün). Die erste Roadmap-3-Scheibe ist implementiert (`e88b3e93`, Details am
Roadmap-Punkt selbst): manuelle lib2-Grabs korrelieren observational in den
Acquisition-Contract; 10 neue Tests in
`tests/acquisition/test_manual_grab.py`, angrenzend 902 Acquisition-/
Import-Tests und 198 library2-Tests grün. Der bestehende App-Level-Test
`test_library_v2_profile_reaches_download_pipeline` fand dabei einen echten
Fehler im ersten Route-Wiring (Registry-Lookup außerhalb der Fail-open-
Grenze konnte den Download mit 500 failen); behoben in `3a417590` — beide
`/api/download`-Branches laufen durch einen komplett abgesicherten Helper,
der bei nicht identifizierbarer Quelle die Korrelation überspringt statt
eine Source-Familie zu raten (ADR-08). Frontend unberührt.
**Session-Abschluss-Gate:** volle Python-Suite grün — **8095 passed,
2 skipped, 2 deselected in 211.41s** (Skips = die opt-in
Live-Deployment-Varianten).

**Session 2026-07-14 (dritte Etappe):** Die zweite Roadmap-3-Scheibe ist
implementiert (`0bb3f6d5`, Details am Roadmap-Punkt 3): Wishlist-Worker-
Dispatches mit lib2-Mirror-Kontext korrelieren als `trigger=scheduled` in
den Acquisition-Contract — geteilter Korrelations-Kern in `manual_grab.py`
(kein zweiter Pfad), Hook im Candidate-Walk, gleiche Callbacks/Marker,
Stale-Sweep generalisiert (`fail_stale_correlated_grabs`). TDD: 6 neue
Tests in `tests/acquisition/test_scheduled_grab.py` + 6 neue Dispatch-Tests
in `tests/downloads/test_downloads_candidates.py`; gezielte Suiten grün
(tests/acquisition+downloads+library2: 1071 passed, 2 skipped;
tests/wishlist+imports: 834 passed). Frontend unberührt. Der im letzten
Status offene Prüfauftrag zum Retry-Journal ist beantwortet (siehe
Roadmap-Punkt 3): Journal-Hook bleibt exklusiv nativ, Marker reist über
Matched-Context/Sidecar.
**Session-Abschluss-Gate:** volle Python-Suite grün — **8107 passed,
2 skipped, 2 deselected in 213.40s** (Skips = die opt-in
Live-Deployment-Varianten).
**Logischer nächster Schritt:** dritte Roadmap-3-Scheibe — Cancel-Wiring:
der Downloads-Cancel-Endpoint soll korrelierte Grabs (manual + scheduled)
als `cancelled` schließen statt sie dem 7-Tage-Sweep zu überlassen; danach
Grabs ohne lib2-Entity betrachten, erst dann globale Durchsetzung.

**Session 2026-07-14 (vierte Etappe):** Die dritte Roadmap-3-Scheibe ist
implementiert: Der bestehende Einzel-Downloads-Cancel-Endpoint schließt
korrelierte `manual`- und `scheduled`-Grabs nach erfolgreichem Client-Cancel
persistent als `cancelled`. Die Korrelation speichert dazu die bereits vom
Endpoint verwendete Legacy-Transfer-ID im bestehenden Grab-Kontext; der neue
fail-open Pipeline-Callback sucht ausschließlich offene Roadmap-3-Grabs und
setzt über `record_grab_cancelled` den vorhandenen Acquisition-Workflow samt
append-only `cancelled`-History in Gang. Bereits abgeschlossene, native
Acquisition- und unkorrelierte Legacy-Downloads bleiben No-ops; ein DB- oder
Callback-Fehler kann den normalen Cancel-Erfolg nie ändern. Gezielte Tests
für manual, scheduled, bereits completed, unbekannt und den fail-open
Endpoint liefen grün (48 passed). Frontend unberührt.
**Logischer nächster Schritt:** Roadmap-Punkt 3 mit Grabs ohne lib2-Entity
fortsetzen; erst danach die globale Durchsetzung betrachten.

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
- ~~**A3 — Job-Registry statt zweier Modul-globaler Dicts.**~~ `_job_state`
  war faktisch ein Ein-Slot-Scheduler. **Umgesetzt 2026-07-14:** Eine kleine
  threadsichere Registry hält opaque Job-IDs und unabhängige Zustände;
  Bulk-Monitor, Retag und Upgrade-Scan dürfen als verschiedene Jobtypen
  parallel laufen, doppelte Typen bleiben serialisiert. Startantworten liefern
  `job_id`, der React-Client pollt genau diese ID, und `/jobs/status` behält
  den kompatiblen Latest-Fallback ohne ID. Der Importjob bleibt bewusst bei
  seinem eigenen exklusiven Import-Slot, weil parallele Full-Imports nicht
  zulässig sind (siehe Abschnitt 7).
- ~~**A4 — Wishlist→Autolink über `lib2_track_id` schließen.**~~
  `wishlist_mirror` legt `_source_info.lib2_track_id` auf die Wishlist-Row —
  bisher matchte `autolink` den fertigen Download aber wieder per Namens-
  Normalisierung. **Umgesetzt 2026-07-14:** Die vorhandene `source_info`
  überlebt bereits Wishlist → Task → Downloadkontext → Import-Chokepoint;
  `autolink` liest dort nun über denselben zentralen Parser zuerst
  `lib2_track_id`/`lib2_album_id`. Explizites server-resolved `lib2_entity`
  bleibt höher priorisiert; nur fehlende oder stale IDs fallen auf den
  bestehenden Legacy-Namensmatcher zurück. Damit verschwinden falsche Album-/
  Compilation-Zuordnungen durch Titelvarianten, ohne einen zweiten Matcher.
- ~~**A5 — Importer-Skalierung.**~~ Der Importer arbeitete row-by-row mit vielen
  Einzel-SELECTs. Für die 285-Track-Referenzbibliothek egal; für
  100k-Track-Bibliotheken wären es Minuten im Write-Lock. **Umgesetzt
  2026-07-14:** Legacy-Album-Trackcounts, claimbare Discography-Releases,
  bestehende Track-Files sowie Wishlist-Album-/Track-Identitäten werden je
  Import einmal vorgeladen. Wishlist-Counts werden einmal pro betroffenem
  Album statt pro Row aktualisiert; Track-Credits verwenden `executemany`.
  Der Instrumentierungstest mit 63 Legacy-Entities plus 30 Wishlist-Rows
  verbietet die alten per-row-SELECT-Signaturen. Notwendige ID-/Trigger-
  abhängige Writes bleiben geordnet in derselben Transaktion; Progress,
  Reset-Reconciliation, Rules/Provenance und Projektionen sind unverändert.
- ~~**A6 — Legacy- vs. lib2-Datenbasis der Repair-Jobs explizit machen.**~~ Die
  per-Artist gescopten Jobs (Gap-Fill, Tag-Consistency, Library-Retag)
  scannen Legacy-Tabellen. Solange der Legacy-Import die Quelle ist,
  deckungsgleich — aber autolink-erzeugte lib2-Rows ohne Legacy-Pendant sieht
  keiner dieser Jobs. Mittelfristig brauchen die Jobs eine lib2-Datenquelle
  oder lib2 einen Rück-Sync. **Explizit gemacht 2026-07-14:** Ein vollständiges
  Registry-Manifest klassifiziert jeden Repair-Job als `legacy`, `lib2`,
  `filesystem` oder `mixed`; Registrierung ohne gültige Zuordnung schlägt
  sofort fehl. Der Worker liefert `data_basis` über die bestehende Repair-API,
  Stats → Repair zeigt sie auf jeder Jobkarte und das Library-v2-Maintenance-
  Modal an jedem angebotenen Job. Das migriert bewusst keine Scanlogik und
  macht insbesondere die noch legacy-basierten Artist-Jobs sichtbar. Drei
  neue Registry/API-Vertragstests (18 Repair-Worker-Tests insgesamt),
  Frontend-Check/Typecheck, fünf gezielte Vitests und Production-Build sind
  grün.
- ~~**A7 — `until_top`-Wording im Code vereinheitlichen.**~~ `is_upgrade_policy`
  akzeptiert beide, `evaluate_file` behandelt `until_top` als Cutoff 0 —
  korrekt. **Abgeschlossen 2026-07-14:** Quality-Evaluator, Library-v2-
  Quality-API, globale Quality-Profile-API und Frontend-Typ dokumentieren nun
  denselben Vertrag: `acceptable` stoppt am ersten akzeptierten Target,
  `until_cutoff` am konfigurierten `upgrade_cutoff_index`, `until_top` als
  persistenter Legacy-Alias ausschließlich bei Index 0. Keine Runtime-
  Semantik oder gespeicherte Row wurde verändert. 36 gezielte Quality-/
  Library-v2-/Legacy-Parity-Tests plus Ruff, Frontend-Check/Typecheck, drei
  Vitests und Production-Build sind grün.

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
| End-to-End | Docker-Acceptance für Phase-C/D seit 2026-07-14 automatisiert geklickt; zwei Playwright-Flows grün (isolierte 4-Artist-/4-Album-/16-File-Fixture) |

**Empfohlene manuelle Docker-Checks (damals vor dem nächsten Merge, jeweils
mit Fund-Referenz):** Multi-Profil-Install-Bulk-Monitor (B2); Setup mit
Path-Mapping (B4); EP im Artist-Detail (B1); Refresh & Scan Thumb-Update (B3);
Wishlist-Track auf unowned Discography-Album (B5); „Search Monitored" auf
vollständiger Library (B6). Alle sechs sind seit dem 2026-07-07-Pass gefixt.

---

## 7. Offene Roadmap (konsolidiert, nicht vergessen, nicht versehentlich „fixen")

Aus Plan + STATUS + Review, priorisiert. **Siehe auch Abschnitt 10.3** für
zusätzliche, kleinteiligere offene Punkte aus dem 2026-07-10-Audit, die hier
noch nicht als eigene Zeile standen (u.a. P1-02, P1-06, P1-24, P1-26, P1-28,
P2-05 und eine Reihe P2-UX/Robustheits-Findings).

1. ~~**LIB2-011 abschließen**: F08-Contract-Matrix plus nachgelagerte
   Python-Fullsuite als Meilenstein-Gate.~~ **Erledigt 2026-07-13**
   (`d921c1eb`; final 8081 passed, 2 deselected in 291.13s).
2. ~~**Deployment-Acceptance für Phase 5**: echte SAB/NZBGet-, gemountete
   Path-Mapping- und Docker-Restart-Tests durchführen. Client-Monitor,
   Category-Adoption, Bundle-Inventory/Matching, `acquisition_imports` und
   Manual Review sind implementiert; die reale Deployment-Abnahme fehlt.~~
   **Erledigt 2026-07-14:** echte Client-API-/Submit-/Status-/Remove-Flows
   sind für SABnzbd 5.0.4 und NZBGet 26.2 grün; SAB-Category-Konfiguration
   wird nun beim Connection-Test validiert (`96c323a2`). Restart-Adoption und
   gemountetes Path-Mapping sind für beide Clients über getrennte echte
   SoulSync-Container grün (`00c57184`). Inventory, Edition-Mismatch-
   `needs_review` und persistente Manual Resolution sind ebenfalls für beide
   Container-Flows grün (`5ab9f726`, providerfreier client-kompatibler
   Completion-Snapshot). Der manuell aufgelöste Import läuft danach durch die
   unveränderte Shared Main Pipeline bis zum persistenten `completed`-
   Callback (`abd70368`; SAB 9,47 s, NZBGet 11,94 s). Ein realer NNTP-Payload-
   Download bleibt ein credentials-abhängiger optionaler Deployment-Smoke.
   Der abschließende Branch-Gate-Lauf ist grün (8085 passed, 2 skipped,
   2 deselected in 211.79s).
3. ~~Bestehende Interactive-/Wishlist-Consumer auf den Acquisition-Contract
   umstellen; erst danach global durchsetzen, dass kein Download ohne
   AcquisitionRequest startet.~~ **Abgeschlossen 2026-07-14.**
   **Erste Scheibe erledigt 2026-07-14**
   (`e88b3e93`, `core/acquisition/manual_grab.py`): manuelle
   Interactive-Search-Grabs mit lib2-Entity (Track- UND Album-Branch von
   `/api/download`) persistieren Request→Candidate→Gate-Run→Grab→History
   (trigger=manual) — strikt observational/fail-open, Gate-Ergebnis wird mit
   `forced=0` protokolliert und nie durchgesetzt (ein manueller Pick ist die
   User-Entscheidung; die F06-Brücke kann dadurch nichts auto-approven).
   Shared-Pipeline-Success schließt Grab+Request (Marker
   `_acquisition_grab_download_id` überlebt Quarantäne-Sidecars); Quarantäne
   wird als History journaliert, Request bleibt für den Approve-Flow offen;
   ein Sweep in `advance_open_imports` failt verwaiste manuelle Grabs nach
   7 Tagen (`failure_kind=runtime`, blocklistet nie). Bundle-Quellen
   (usenet/torrent/lidarr) bewusst ausgenommen — deren Plugins schreiben
   eigene Grab-Rows. **Zweite Scheibe erledigt 2026-07-14** (`0bb3f6d5`):
   Wishlist-Worker-Dispatches korrelieren als `trigger=scheduled` — der
   Candidate-Walk (`core/downloads/candidates.py`) erkennt den
   lib2-Mirror-Kontext (`track_info.source_info.lib2_track_id`, von
   `wishlist_mirror` über die Wishlist-Row bis in den Task getragen) und
   ruft die geteilte Korrelation (`manual_grab.py::_correlate_grab`,
   `correlate_scheduled_grab`, `shadow_source=legacy_wishlist_worker`,
   `legacy_task_id`/`legacy_batch_id` in den Search-Options). Gleiche
   observational Semantik: Gate-Ergebnis mit `forced=0` protokolliert
   (`gate_rejections_observed_not_enforced`), nie durchgesetzt; derselbe
   Marker, dieselben Pipeline-Callbacks schließen den Grab. Ausgeschlossen:
   acquisition-native Dispatches (`_acquisition_import_id`, sonst
   Doppel-Buchung) und User-Manual-Picks aus dem Candidates-Modal. Der
   Stale-Sweep heißt jetzt `fail_stale_correlated_grabs` und deckt manual
   UND scheduled ab. Retry-Journal-Prüfauftrag beantwortet: der Journal-Hook
   in `attempt_download_with_candidates` bleibt exklusiv für native
   Acquisition-Walks (`_acquisition_task_ref` liest nur
   `_acquisition_import_id`); der Grab-Marker reist unabhängig davon über
   den Matched-Context/Sidecar. Bewusste Grenze: requeued Walks derselben
   Legacy-Task erzeugen pro Dispatch einen eigenen Request; der vorherige
   bleibt bis Approve-Erfolg oder 7-Tage-Sweep `grabbing` (identisch zur
   Manual-Semantik). **Dritte Scheibe erledigt 2026-07-14:** Der bestehende
   Einzel-Downloads-Cancel-Endpoint speichert nach erfolgreichem
   Client-Cancel korrelierte `manual`- und `scheduled`-Grabs über den
   bestehenden Workflow als `cancelled` samt History (`record_grab_cancelled`),
   statt sie dem 7-Tage-Sweep zu überlassen. Die persistierte
   `legacy_download_id` im Grab-Kontext verbindet die externe Transfer-ID
   mit dem synthetischen Correlation-ID; der Callback ist fail-open und
   ignoriert completed, native Acquisition- und normale Legacy-Downloads.
   Gezielte Tests: manual, scheduled, completed, unbekannt sowie Callback-
   Fehler am Endpoint (48 passed). **Vierte Scheibe erledigt 2026-07-14:**
   Auch Admin-Profil-Grabs ohne lib2-Entity werden jetzt über denselben
   observational/fail-open Adapter korreliert. Ein vorhandener lib2-Verweis
   bleibt unverändert die exakte Entity; normale Interactive-Picks und
   Wishlist-Tasks erhalten stattdessen eine explizit namespacete
   `legacy_shadow`-Recording-Identität aus einem stabilen, redigierten Digest.
   Der serverseitige Wishlist-Task ist dabei die Ziel-Wahrheit, nicht der
   Candidate; manuelle Picks verwenden die bereits dispatchten Pick-Fakten.
   Es werden bewusst keine künstlichen lib2-Katalogzeilen erzeugt. Das
   Eligibility-Gate bleibt observational (`forced=0`), während Quality,
   Source-Auswahl, Retry, Quarantäne und Import weiter vollständig der
   Main-Pipeline gehören. ADR-01 bleibt erhalten: nicht-administrative
   Profile verbleiben in ihrem unabhängigen Legacy-Wishlist-Pfad. 51 gezielte
   Korrelations-/Candidate-/Cancel-Tests sind grün. **Noch offen:** globale
   Durchsetzung nach einem fail-open-/Coverage-Gate entwerfen und erst dann
   aktivieren. **Fünfte Scheibe erledigt 2026-07-14:** Der manuelle Consumer
   persistiert Request→Candidate→Gate-Run→Grab jetzt VOR dem externen
   Client-Aufruf (`status=submitting`) und bindet die echte Legacy-Transfer-ID
   erst nach bestätigtem Dispatch (`status=downloading`, `grab_submitted`).
   Ein eindeutig abgelehnter/geworfener Dispatch schließt den vorbereiteten
   Request als Runtime-Failure, ohne den Candidate zu blocklisten; ein
   Bookkeeping-Fehler bleibt bis zur späteren expliziten Durchsetzung
   fail-open. Der gemeinsame Grab-Service besitzt dafür einen kleinen
   JSON-Context-Patch statt consumerseitiger SQL-Kopien. Damit ist die
   notwendige Persist-before-External-Work-Reihenfolge für Interactive-Grabs
   hergestellt. **Logischer nächster Schritt:** denselben zweiphasigen
   Prepare→Dispatch→Bind-Vertrag im Wishlist-Candidate-Walk nutzen; erst wenn
   beide Legacy-Consumer vorab persistieren, ein opt-in Fail-closed-Gate
   hinzufügen. **Sechste Scheibe erledigt 2026-07-14:** Auch der Wishlist-
   Candidate-Walk persistiert seine scheduled Request-/Grab-Kette jetzt vor
   `download_orchestrator.download`, bindet danach die echte Transfer-ID und
   schließt eindeutige None-/Exception-Dispatches als Runtime-Failure. Der
   bestehende Same-/Next-Candidate-Walk bleibt unverändert die einzige Retry-
   Engine; jeder tatsächliche Dispatch erhält weiterhin seinen eigenen
   observational Request. Die Cancellation-Race direkt nach Client-Start
   meldet einen erfolgreich entfernten Transfer nun ebenfalls an den
   vorhandenen Correlation-Cancel-Callback statt den Request dem TTL-Sweep zu
   überlassen. Gezielte Tests beweisen Prepare→Dispatch→Bind-Reihenfolge,
   abgelehnten Dispatch, native-/Manual-/Fremdprofil-Ausnahmen und Cancel.
   **Logischer nächster Schritt:** ein default-off Fail-closed-Gate für die
   beiden jetzt vorab persistierenden Legacy-Consumer hinzufügen und dabei
   Bundle-/native Acquisition sowie Nicht-Admin-Profile explizit ausnehmen.
   **Siebte Scheibe erledigt 2026-07-14:** Das opt-in Gate
   `features.acquisition_contract_enforce=true` blockiert in den zwei
   konvertierten Legacy-Consumern einen Admin-Recording-Dispatch, wenn dessen
   Request/Grab-Vorbereitung keinen Marker liefern konnte. Default ist
   bewusst `false`, sodass bestehende Installationen zunächst unverändert
   fail-open beobachten können. Ausgenommen bleiben native Acquisition-Walks,
   Bundle-Quellen mit eigener Grab-Persistenz, User-Manual-Picks aus dem
   Candidate-Modal und Nicht-Admin-Profile (ADR-01). Tests beweisen, dass im
   Strict-Modus weder der manuelle Route-Client noch der Wishlist-Candidate-
   Client aufgerufen wird, wenn die Vorbereitung fehlt, während die
   Ausnahmen weiter dispatchen. **Logischer nächster Schritt:** Coverage/
   Failure-Observability für die Vorab-Persistenz ergänzen und das Gate in
   echter Docker-Nutzung erst nach null ungeklärten Recording-Dispatches
   aktivieren; danach kann Roadmap-Punkt 3 als global durchgesetzt gelten.
   **Achte Scheibe erledigt 2026-07-14:**
   `acquisition_correlation_coverage` hält ausschließlich tägliche Aggregate
   pro Consumer (`manual`/`scheduled`) und Outcome (`prepared`,
   `unprepared_dispatched`, `blocked`) — keine Entity, Dateinamen, Pfade oder
   Client-IDs. Erfolgreiche Prepare-Events werden in derselben Transaktion wie
   Request/Grab gezählt; fail-open-/blocked-Lücken werden best-effort am
   Consumer erfasst und zusätzlich strukturiert geloggt, falls selbst die
   Diagnose-DB nicht erreichbar ist. Der redigierte Endpoint
   `GET /api/library/v2/acquisition/correlation-coverage?days=7` liefert
   Manual-/Scheduled-Coverage, Strict-Gate-Status und `ready=true` erst bei
   beobachteten Prepares ohne ungeklärten Dispatch in beiden Consumern.
   **Logischer nächster Schritt:** in echter Docker-Nutzung mit Gate aus ein
   Beobachtungsfenster fahren; erst bei 100%/`ready` das opt-in Gate aktivieren
   und Manual+Wishlist smoke-testen. Code-seitig ist Roadmap-Punkt 3 damit bis
   auf diese reale Aktivierungs-Acceptance abgeschlossen; danach Punkt 4
   (Identity/Provenance) aufnehmen. **Neunte/abschließende Scheibe erledigt
   2026-07-14:** Der opt-in Deployment-Contract
   `test_contract_enforcement_deployment.py` startet einen frischen Prozess im
   gebauten `soulsync:dev`-Produktionsimage mit isolierter Config/DB. Er führt
   die echte `/api/download`-Route und den echten Wishlist-Candidate-Walk mit
   aktiviertem Strict Gate gegen dieselbe Acquisition-Persistenz aus; nur die
   credential-/peerabhängige externe Downloader-Grenze ist ein deterministischer
   Recording-Client. Verifiziert sind Prepare→Dispatch→Bind für beide Consumer,
   je ein persistierter `manual`-/`scheduled`-Request, zwei
   `grab_submitted`-Events, 100% Coverage, null
   `unprepared_dispatched` und der echte Coverage-Endpoint mit
   `enforced=true`/`ready=true`. Der Docker-Contract ist grün (1 passed); alle
   sechs angrenzenden Korrelations-/Route-/Candidate-Suiten sind ebenfalls grün
   (**121 passed**). Damit ist Roadmap-Punkt 3 vollständig abgeschlossen.

**Session-Abschluss-Gate:** volle Python-Suite grün — **8112 passed,
2 skipped, 2 deselected in 291.41s**. Die zwei Skips sind weiterhin die
bewusst opt-in markierten Live-Deployment-Varianten. Frontend unberührt;
daher keine Frontend-Gates erforderlich.

**Session-Abschluss-Gate 2026-07-14 (Roadmap-3-Fortsetzung):** Die volle
Python-Suite nach den Legacy-Shadow-, Prepare→Dispatch→Bind-, Strict-Gate- und
Coverage-Slices ist grün: **8132 passed, 2 skipped, 2 deselected, 302 warnings
in 212.62s**. Die zwei Skips sind weiterhin ausschließlich die bewusst opt-in
Live-Deployment-Varianten. Frontend-Code wurde in dieser Session nicht
verändert; daher waren Frontend-Typecheck/Vitest/Build nicht erforderlich.
**Nächster logischer Schritt:** Roadmap-Punkt 4 (Identity/Provenance): zuerst
das vorhandene Shadow-Modell und die verbliebenen Identitäts-Schreib-/Read-
Pfade inventarisieren, dann dedizierte externe-/Old-ID-History als kleinste
persistente Slice umsetzen. Field-Level-Overrides und Read-Projection bauen
darauf auf.
4. ~~Phase-3-Identity/Provenance fertigstellen: dedizierte externe-/
   Old-ID-History, Merge-/Move-History, Field-Level-User-Overrides und
   Read-Projection. Typed Adapters über Discography/Tracklist hinaus
   erweitern.~~ **Vollständig abgeschlossen 2026-07-14. Erste Slice:**
   `lib2_external_id_history` ist eine append-only History für alle heutigen
   skalaren Provider-/Legacy-IDs auf Artist, Release Group, Track,
   ReleaseEdition und Recording sowie die Long-Tail-`external_ids`-JSONs.
   Trigger an der gemeinsamen DB-Grenze erfassen Assign/Replace/Remove und
   Entity-Delete unabhängig davon, ob Importer, Discography, Edition-Backfill
   oder ein späterer Adapter schreibt; dadurch musste kein zweiter
   Identity-Resolver entstehen. Ein idempotenter Baseline-Backfill nimmt
   bestehende Installationen auf, alte IDs überleben Entity-Löschungen, und
   DB-Guards verbieten UPDATE/DELETE der History. Der Read-Helper liefert
   validierte, newest-first Events; aktuelle Katalogspalten bleiben bis zum
   späteren Read-Projection-Cutover die Write-Source-of-Truth. 58 gezielte
   Schema-/Importer-/Edition-/Snapshot-Tests sind grün. **Zweite Slice erledigt
   2026-07-14:** `lib2_entity_history` journalisiert Canonical-Link/Relink/
   Unlink, Track-File-Moves und ADR-04-ReleaseTrack-Moves zwischen Recording
   bzw. ReleaseEdition über DB-Trigger. Explizite transaktionsneutrale
   `record_entity_merge`-/`record_entity_move`-Helper stehen für kommende
   atomare Commands bereit; sie führen die Operation bewusst nicht selbst aus.
   Das append-only Journal speichert nur lokale Typen/IDs und whitelisted
   Kontext, nie Pfade, Titel oder Provider-Payloads. Bestehende Canonical-Links
   erhalten einen idempotenten Baseline-Eintrag. 49 gezielte Manage-Tracks-/
   Edition-/Importer-/Schema-Tests sind grün. **Dritte Slice erledigt
   2026-07-14:** `lib2_metadata_overrides` trennt validierte, feldgenaue
   Admin-Korrekturen für Artist, Release Group, Track, ReleaseEdition und
   Recording von den Provider-/Importer-Baselines. Provider-Refreshes können
   ihre Baseline weiter aktualisieren; effektive Reads aus Artist-Liste,
   Artist-/Album-/Track-Detail projizieren den Override darüber und geben die
   angewendeten `user_overrides` transparent mit aus. Die bestehende
   Album-Type-Edit-Route nutzt denselben Store statt `lib2_albums` umzuschreiben;
   eine generische admin-only Set/Clear-Route erschließt die weiteren
   freigegebenen Felder. Entity-Deletes räumen den Current-State auf,
   Monitoring/Quality/IDs/Pfade sind nicht überschreibbar. 87 gezielte
   Override-/Query-/API-/Schema-/Snapshot-/Discography-Tests sind grün.
   **Vierte/abschließende Slice erledigt 2026-07-14:** Auch der letzte direkte
   Library-v2-Providerpfad, der Artwork-Fallback, läuft jetzt über
   `provider_adapters.py`. `ArtworkProviderResult` normalisiert URL, tatsächliche
   Quelle und Provider-Entity-ID; Artist-/Album-Overrides werden vor dem Lookup
   effektiv projiziert und ReleaseEdition-IDs einbezogen. Die vorhandene
   zentrale Artist-Image-/Cover-Art-Priorisierung wurde wiederverwendet; ihr
   kompatibler URL-Entry-Point delegiert an denselben Resolver, der typed
   Consumer zusätzlich die gewählte Quelle liefert. Außer dem lokalen
   Embedded-Art-Parser liegen damit alle `core.metadata`-Providerzugriffe aus
   `core/library2` ausschließlich in der Adapterdatei. 122 gezielte Adapter-/
   Artwork-/API-/Override-/Snapshot-/Discography-/Completeness-Tests sind grün.
   **Nächster logischer Schritt nach dem Phase-3-Meilenstein-Gate:** Roadmap-
   Punkt 5, den gestaffelten Wanted-Cutover anhand der vorhandenen Drift-
   Metriken und verbliebenen `monitored`-Consumer aufnehmen.

**Phase-3-Meilenstein-Gate 2026-07-14:** Die volle Python-Suite nach External-
ID-History, Merge-/Move-Journal, Field-Level-Overrides/Read-Projection und der
vollständigen typed Provider-Boundary ist grün: **8164 passed, 3 skipped,
2 deselected, 335 warnings in 234.76s**. Die drei Skips sind die bewusst
opt-in markierten Docker-/Live-Deployment-Varianten. Frontend-Code wurde nicht
verändert; daher waren Frontend-Typecheck/Vitest/Build nicht erforderlich.
5. ~~Gestaffelten Wanted-Cutover fertigstellen: Consumer, die noch
   `monitored`-Flags nutzen, müssen nach Drift-Metriken-Beweis der Parität
   auf `lib2_wanted_tracks` wechseln.~~ **Abgeschlossen 2026-07-14:**
   Track-Acquisition, manueller/periodischer Upgrade-Scan, zentrale Track-
   Reads/Artist-Stats und das Legacy-Wishlist-Mirroring konsumieren jetzt die
   versionierte `lib2_wanted_tracks`-Projektion statt `t.monitored`. Artist-/
   Album-Flags bleiben nur Eingabe-/Kompatibilitätszustand; die Legacy-Wishlist
   ist eine transaktional abgeleitete Ausführungsliste. Projected Mirror-
   Enqueues brechen bei fehlenden/veralteten Rows ab statt still auf Flags
   zurückzufallen. `GET /api/library/v2/wanted-projection/status` meldet
   Coverage (`missing`/`stale`), Version, Wanted-Count und Flag-Drift;
   `consumer_ready` verlangt vollständige aktuelle Projektion, während
   dokumentierte Parent-Rule-Abweichungen sichtbar bleiben dürfen. Importer,
   Tracklist-Materialisierung und Auto-Link legen neue Projektionszeilen an.
   Der Cutover deckte einen realen Legacy-Randfall auf: konkrete Admin-
   Wishlist-Tracks erhalten nun `wishlist_import`-Provenance und werden nicht
   mehr von der unmonitorierten importierten Parent-Album-Regel überstimmt.
   Bulk-Monitor und Track-File-Move schreiben jetzt ebenfalls Rules + Projektion
   und spiegeln daraus; Bulk wahrt explizite Track-Vetos. 154 gezielte Wanted-/
   Importer-/Mirror-/Query-/API-/Upgrade-/Discography-/Move-Tests sind grün.
   **Nächster logischer Schritt:** Roadmap-Punkt 6 — Monitor-Provenance bei
   Re-Imports prüfen und Album-Intent unabhängig vom Track-Wishlist-Zustand
   dauerhaft absichern.
6. ~~**Monitor-Provenance**: Album-Monitoring, das Re-Imports unabhängig vom
   Track-Wishlist-Zustand überlebt (Provenance-/Mode-Spalten statt
   Ableitung).~~ **Abgeschlossen 2026-07-14:** Nicht-destruktive Re-Imports
   projizieren Artist-/Album-Regeln aktiv auf die Compatibility-Flags zurück.
   Für `reset=True` wird nicht-legacy Album-Intent vor dem Delete über
   Provider-ID oder deterministische Album-`stable_id` gesichert, nach dem
   Rebuild auf die neue lokale ID restored und erst danach werden fehlende
   Legacy-Regeln ergänzt/Wanted neu berechnet. Damit kann Track-Wishlist-
   Seeding Album-Intent weder erzeugen noch löschen; verwaiste surrogate-ID-
   Regeln werden beim Reset bewusst entfernt. 100 gezielte Importer-/Monitor-
   Rule-/Wanted-/Stable-ID-/Discography-/API-Tests sind grün.
   **Nächster logischer Schritt:** Roadmap-Punkt 7, die globale `_job_state`-
   Einzelbelegung in eine Job-ID-Registry überführen.
7. ~~**Job-Registry** (M8/A3): Bulk-Monitor/Retag/Upgrade-Scan vom globalen
   `_job_state` auf Job-IDs umstellen.~~ **Abgeschlossen 2026-07-14:** Die
   threadsichere In-Process-Registry verwaltet pro Lauf eine opaque UUID,
   Zeitstempel, Fortschritt, Ergebnis und Fehler. Unterschiedliche Typen
   laufen unabhängig; doppelte Retag-/Upgrade-/Monitor-Jobs werden mit 409 und
   der bereits aktiven Job-ID abgewiesen. Alle Monitor-Scopes teilen bewusst
   einen Typ, weil sie dieselben Rules/Projections verändern. Die drei
   Startendpunkte liefern `job_id`; gezieltes `GET /jobs/status?job_id=...`
   verhindert Cross-Job-Races im React-Client, während der Statusaufruf ohne
   ID als kompatibler Latest-Fallback bestehen bleibt und zusätzlich die
   Registry-Sicht liefert. 51 gezielte Python-Tests plus Ruff sowie Frontend-
   Check/Typecheck, 96 Vitest-Tests und Production-Build sind grün.
   **Nächster logischer Schritt:** Roadmap-Punkt 8 — das vorhandene zentrale
   Override-/Read-Projection-Fundament für breiteres Metadaten-Edit und eine
   deep-linkbare Album-Detailansicht nutzen, ohne Provider-Snapshots direkt zu
   überschreiben.
8. ~~**Breiteres Metadaten-Edit** (Titel/Jahr/Artists) über den
   Release-Type-Edit hinaus; deep-linkbare Album-Detail-Ansicht.~~ **Erste Slice
   2026-07-14 abgeschlossen:** `?album=<id>` ist jetzt ein validierter,
   direkt ladbarer Zustand statt totem Search-Parameter. Der Route-Loader
   prefetcht Albumdaten; eine eigenständige Albumansicht zeigt effektive
   Metadaten, Monitoring, Quality-Profil, Vollständigkeit und die bestehende
   Tracktabelle. Track-Suche und Interactive Search verwenden dort dieselben
   bestehenden Handler samt lib2-Entity-/Profilkontext; jede Release-Zeile
   verlinkt explizit in die Ansicht und zurück zum Primary Artist. Frontend-
   Check/Typecheck, zwei gezielte Schema-Tests und Production-Build sind grün.
   **Zweite Slice 2026-07-14 abgeschlossen:** Die bestehende generische
   Override-API besitzt zusätzlich einen transaktionalen Batch-Command pro
   Entity. Ein Request kann mehrere validierte Felder atomar über die
   vorhandenen `set_field_override`-/`clear_field_override`-Helper setzen und
   löschen; ein ungültiges Feld rollt die gesamte Änderung zurück. Shape,
   Set/Clear-Überlappung, Entity-Existenz und Admin-only-Grenze werden
   serverseitig erzwungen. Damit braucht ein Metadaten-Modal keine partiellen
   Einzelrequests und es entsteht keine zweite Override-Logik. 61 gezielte
   Override-/Query-/API-Tests plus Ruff sind grün. **Nächste Slice:** Titel/
   Jahr und Artist-Metadaten im React-Edit-Modal auf diesen Batch-Command
   setzen, inklusive explizitem Reset auf die Provider-Baseline; Artist-
   Relationen bleiben unangetastet. **Dritte/abschließende Slice 2026-07-14:**
   Das Release-Modal editiert nun effektiven Titel, Jahr und Release-Typ; das
   Artist-Modal editiert Name und Genres. Beide senden genau einen Batch-
   Command, zeigen Validierungsfehler, invalidieren die zentrale Query und
   bieten für vorhandene `user_overrides` einen expliziten Reset auf die
   aktuelle Provider-Baseline. Dasselbe Release-Modal ist aus Artist-Detail und
   deep-linkbarer Albumansicht erreichbar. `user_overrides` ist im Client
   typisiert; Providerfelder, IDs, Monitoring/Quality und Artist-Relationen
   werden nicht direkt verändert. Frontend-Check/Typecheck, vier gezielte
   Schema-/API-Vertragstests und Production-Build sind grün. Damit ist
   Roadmap-Punkt 8 vollständig abgeschlossen. **Nächster logischer Schritt:**
   Roadmap-Punkt 9 — Reorganize/Dedup ehrlich auf Artist-Pfade scopen, mit
   Root-Safety und ohne einen SQL-Filter als Dateisystem-Scope auszugeben.
9. ~~**Artist-Scope für Reorganize/Dedup** (brauchen Pfad-Scoping, kein
   SQL-Filter — im Maintenance-Modal laufen diese Jobs derzeit ehrlich
   gekennzeichnet library-wide).~~ **Erste Slice 2026-07-14 abgeschlossen:**
   Die Repair-Run-Grenze kann eine lib2-Artist-ID jetzt serverseitig in eine
   exakte Allowlist der über `lib2_album_artists`/Tracks verknüpften Dateipfade
   auflösen; eine explizit leere Liste bleibt ein leerer Scope und fällt nie
   auf library-wide zurück. `single_album_dedup` begrenzt die actionable/
   löschbare Single-Seite auf diese Pfade, hält albumweite Vergleichskandidaten
   aber lesbar. `library_reorganize` mappt erlaubte Quellpfade auf Alben und
   queued nur vollständig im Scope liegende Alben; ein teilweise fremdes Album
   wird übersprungen, weil die wiederverwendete Reorganize-Queue atomar pro
   Album arbeitet. Es gibt weder einen Artist-Name-SQL-Scheinfilter noch eine
   zweite Move-Implementierung. 28 gezielte File-Scope-/Repair-Worker-/
   Reorganize-Tests plus Ruff sind grün. **Nächste Slice:** Maintenance-Client
   auf Artist-ID + Name umstellen, beide Jobs ehrlich als „this artist“
   markieren und den Frontend-Gate laufen lassen. **Zweite/abschließende Slice
   2026-07-14:** Der Maintenance-Client sendet für alle artist-scoped Jobs die
   lib2-Artist-ID plus effektiven Namen; ein Vertragstest pinnt den Request.
   Reorganize und Single/Album-Dedup tragen erst seit der wirksamen Path-
   Allowlist das „this artist“-Badge und beschreiben den begrenzten Datei-Scope
   ehrlich. Frontend-Check/Typecheck, drei gezielte API-Tests und Production-
   Build sind grün. Damit ist Roadmap-Punkt 9 vollständig abgeschlossen.
   **Nächster logischer Schritt:** Roadmap-Punkt 11/A4 — die bereits in der
   Wishlist gespeicherte `lib2_track_id` bis zur Download-Provenance tragen und
   Autolink deterministisch zuerst über diese ID schließen; die bestehende
   heuristische Namensauflösung bleibt nur Legacy-Fallback.
10. ~~**Album-Detail-Deep-Link** (`search.album`-Parameter, M14) implementieren
    oder entfernen.~~ **Abgeschlossen in Roadmap-8-Slice 1 am 2026-07-14:**
    validierter Search-State, Loader-Prefetch, eigenständige Detailansicht und
    Navigation aus jeder Albumzeile.
11. ~~**Wishlist→Autolink über `lib2_track_id`** deterministisch schließen
    (A4).~~ **Abgeschlossen 2026-07-14:** dict- und JSON-`source_info` landen
    deterministisch auf der gespeicherten Track-Row; explizites
    `lib2_entity` gewinnt weiterhin, Heuristik bleibt nur Legacy-/stale-ID-
    Fallback. Ein Downloadkontext-Test pinnt die vollständige Übergabe. 75
    gezielte Autolink-/Wishlist-Mirror-/Outbox-/Download-/Import-Side-Effect-
    Tests plus Ruff sind grün. **Nächster logischer Schritt:** Roadmap-Punkt
    12/A5 — Importer-Skalierung messen und die nachgewiesenen N+1-Hotspots mit
    vorgeladenen Maps/Batches reduzieren, ohne Progress, Reset-Reconciliation
    oder die zentrale Importsemantik zu duplizieren.
12. ~~**Importer-Skalierung** für sehr große Libraries (A5).~~ **Abgeschlossen
    2026-07-14:** Count-/Discography-/File-/Wishlist-Maps ersetzen die
    nachgewiesenen N+1-Reads, Credit-Writes sind gebündelt und Album-Recounts
    dedupliziert. Ein SQL-Trace-Regressionstest pinnt die entfernten Query-
    Muster. 86 gezielte Importer-/Monitor-Rule-/Wanted-/Edition-/Stable-ID-/
    Identity-/Override-/Query-Tests plus Ruff sind grün. **Nächster logischer
    Schritt:** Roadmap-Punkt 13/A6 — Repair-Jobs deklarativ nach Legacy- bzw.
    lib2-Datenbasis kennzeichnen und diese Basis in API/UI sichtbar machen,
    bevor einzelne Jobs migriert werden.
13. ~~**Legacy- vs. lib2-Datenbasis der Repair-Jobs** explizit machen (A6).~~
    **Abgeschlossen 2026-07-14:** Das exhaustive Registry-Manifest erzwingt
    für alle 32 Jobs eine der vier Datenbasen `legacy`, `lib2`, `filesystem`
    oder `mixed`; `RepairWorker.get_all_job_info()` exponiert sie und sowohl
    die globale Stats-/Repair-UI als auch Library-v2-Maintenance zeigen sie
    sichtbar an. Damit bleibt Legacy weiterhin die ehrliche Basis der noch
    nicht migrierten Jobs, statt durch einen impliziten lib2-Scope kaschiert zu
    werden. 18 gezielte Python-Tests, Frontend-Check/Typecheck, fünf Vitests
    und Production-Build sind grün. **Nächster logischer Schritt:** Roadmap-
    Punkt 14 / Phase E — Playlists zuletzt über die vorhandene Wishlist-/
    Acquisition-Pipeline anbinden, ohne zweiten Importer oder zweite
    Decision-Engine.
14. ~~**Playlists** (Phase E, bewusst zuletzt).~~ **Erste Slice 2026-07-14:**
    Library v2 hat jetzt typisierte List-/Detail-Reads auf die vorhandenen
    `mirrored_playlists`-Endpoints und einen dünnen Start-Client für exakt die
    bestehende `run_mirrored_playlist_pipeline`-Kette (Refresh → Discovery →
    Server-Sync → Wishlist). Route/Search-State tragen `section=playlists`
    und stabile Playlist-Deep-Links; Loader prefetchten nur die jeweilige
    bestehende Query. Keine neue Playlist-Decision-Engine, kein Importpfad.
    Frontend-Check/Typecheck, acht gezielte API-/Schema-Vitests und Production-
    Build sind grün. **Nächste Slice:** Playlist-Index und -Detail mit Track-/
    Pipeline-Status auf der Library-Seite darstellen und den vorhandenen
    Pipeline-Start sichtbar verdrahten.
    **Zweite/abschließende Slice 2026-07-14:** Der Library-Header schaltet
    stabil zwischen Artists und Playlists; Playlist-Index und `playlist=<id>`-
    Detail zeigen Source, Owner, Artwork, Tracks, Discovery-/Wanted-/Library-
    Zähler sowie den live gepollten Pipeline-Status. „Run pipeline" delegiert
    ausschließlich an den vorhandenen Endpoint und damit an denselben
    `run_mirrored_playlist_pipeline`; nicht refreshbare File-/Beatport-Quellen
    bleiben ehrlich deaktiviert. Mirroring/Quellenauswahl bleibt bewusst auf
    der etablierten Playlists-Seite statt in Library v2 dupliziert. Frontend-
    Check/Typecheck, acht gezielte API-/Schema-Vitests und Production-Build
    sind grün. Damit ist Phase E / Roadmap-Punkt 14 abgeschlossen.
    **Nächster logischer Schritt:** Roadmap-Punkt 15 — die bisher code-/curl-
    verifizierten Phase-C/D-Flows im Docker-Deployment per Browser klicken und
    nur tatsächlich reproduzierbare UI-Lücken korrigieren.
15. ~~**Browser-Klick-Verifikation** in Docker für Phase-C/D-Flows, die nur
    code-/curl-verified sind.~~ **Abgeschlossen 2026-07-14:** Vom aktuellen
    Branch gebautes Docker-Image, isolierte Beispielkonfiguration und kopierte
    Fixture-DB (lib2-Import: 4 Artists, 4 Alben, 16 Files). Zwei persistente
    Playwright-Acceptance-Tests klicken Artist- und Album-Deep-Links, Retag-
    Preview, Maintenance inkl. Datenbasis, Manage Tracks, History, Artist-/
    Album-Edit, Monitoring, Quality Profile, beide sicheren Delete-Dialoge,
    Album-Expansion, Interactive Search, Manual Import sowie die Playlist-
    Sektion. **2 passed** im echten Browser gegen den Container. Dabei wurde
    eine reale Testinfrastruktur-Lücke gefunden und geschlossen:
    `playwright.config.ts` erzwingt nicht länger das oft fehlende
    `/usr/bin/chromium`, sondern nutzt optional `PLAYWRIGHT_CHROMIUM_PATH` und
    sonst Playwrights gepinnten Browser. Destruktive Bestätigungen sowie echte
    Such-/Downloadstarts wurden bewusst nicht ausgelöst. **Nächster logischer
    Schritt:** nach dem ebenfalls geschlossenen 5.4-Finding A7 ist P1-02 aus
    10.3 der oberste offene Punkt: Legacy-Import per Run-Snapshot gegen
    gelöschte/geänderte Quellzeilen reconciliieren.
16. ~~**Legacy-Import Snapshot-Reconciliation (P1-02).**~~ **Abgeschlossen
    2026-07-14:** Jeder vollständige Legacy-Import prägt Artist-, Album-,
    Track- und importer-eigenen File-Zeilen eine eindeutige Run-ID auf. Nach
    dem Einlesen werden nicht mehr gesehene Legacy-Files gelöscht und
    entfernte Tracks/Alben/Artists bottom-up reconciliert; ein geänderter
    `file_path` ersetzt dadurch die alte Importer-Datei, statt eine zweite
    Phantom-Datei stehen zu lassen. Die Ownership-Grenze ist explizit:
    manuelle/sekundäre Files werden nie vom Snapshot-Pruner berührt. Entfallene
    Metadaten mit Provider-ID, expliziter Nutzer-/Wishlist-Absicht oder
    unabhängigem File werden von der Legacy-ID gelöst (Album zurück auf
    `origin='discography'`) statt gelöscht. Additive Spaltenmigrationen nehmen
    bestehende Installationen mit; exakt passende Alt-Files werden beim ersten
    Lauf sicher adoptiert. **96 gezielte Tests** über Import, Schema,
    Monitor-Regeln, Wanted-Projektion, Editions, Multi-File, Provider-Snapshots
    und Metadata-Overrides sowie Ruff sind grün. **Nächster logischer Schritt:**
    P1-06 — Canonical-/Move-Validierung und Multi-File-Move-Semantik härten.
17. ~~**Canonical-/Move-Invarianten (P1-06).**~~ **Abgeschlossen 2026-07-14:**
    Canonical-Link und File-Move verwenden jetzt denselben konservativen
    Duplicate-Pair-Validator: gemeinsame Artist-Credits, normalisierter Titel,
    Dauer-Toleranz (5 Sekunden bzw. 3 %) und widerspruchsfreie ISRC-/MBID-/
    Spotify-Recording-IDs. Canonical-Ketten werden abgewiesen; ungültige IDs
    liefern kontrolliert 400 statt 500. Der Move verschiebt atomar **alle**
    Source-File-Zeilen statt nur des Primary-Files und setzt erst den danach
    wirklich filelosen Source-Track unmonitored. Beim legitimen Gegenmove vom
    bisherigen Canonical zum direkten Duplicate wird die Beziehung atomar
    umgedreht, sodass die filebesitzende Seite Canonical bleibt. Die alte
    `moved_file_id`-Antwort bleibt kompatibel, ergänzt um IDs/Count aller Files.
    **84 gezielte Python-Tests**, Ruff, Frontend-Check/Typecheck, acht Vitests
    und Production-Build sind grün. **Nächster logischer Schritt:** P1-24 —
    `monitor_new_items='new'` anhand des Release-Datums wirklich von `all`
    unterscheiden.
18. ~~**Echte `monitor_new_items='new'`-Semantik (P1-24).**~~
    **Abgeschlossen 2026-07-14:** Discography-Refresh berechnet vor dem Sync
    einen festen Cutoff aus dem neuesten datierten, bereits bekannten Release.
    `all` auto-monitort weiterhin jede neue Entdeckung; `new` ausschließlich
    Entdeckungen mit Datum strikt nach diesem Cutoff. Spät gelieferter
    Backkatalog und undatierte Releases bleiben unmonitored, und ein fehlender
    datierter Baseline-Bestand wird konservativ nicht als Freigabe behandelt.
    Der Cutoff bleibt pro Snapshot konstant, sodass Provider-Sortierung das
    Ergebnis nicht beeinflusst; API und periodischer Repair-Job verwenden
    weiterhin denselben Discography-Pfad. **33 gezielte Discography-/Monitor-/
    Wanted-Tests** und Ruff sind grün. **Nächster logischer Schritt:** P1-26 —
    Tracklist-Materialisierung darf überwachte/fileless Rows beim Kürzen nicht
    stumm löschen.
19. ~~**Verlustfreie Tracklist-Materialisierung (P1-26).**~~ **Abgeschlossen
    2026-07-14:** Eine vollständige Provider-Tracklist wird nicht mehr auf
    einen veralteten kleineren `expected_track_count` abgeschnitten; der Count
    konvergiert stattdessen mindestens auf die bestätigte Liste. Der
    Surplus-Pruner entfernt nur noch filelose, nicht-Legacy- und wirklich
    ungewollte Rows: Compatibility-Monitorflag, positive Monitor-Regel und
    Wanted-Projektion sind jeweils Schutzgründe. Für tatsächlich entfernte
    Rows werden Monitor-/Wanted- und Edition-Shadowdaten mitbereinigt.
    Bewusst erhaltene lokale/Wishlist-Zeilen erhöhen den Expected-Count, damit
    der Precache dieselbe absichtliche Abweichung nicht endlos erneut prüft.
    **99 gezielte Tests** über Completeness, Queries, Wanted/Monitor, Editions,
    Provider-Snapshots und API sowie Ruff sind grün. **Nächster logischer
    Schritt:** P1-28 — Refresh & Scan und Retag müssen den Tag-/Gap-Cache
    konsistent aktualisieren bzw. invalidieren.
20. ~~**Tag-/Gap-Cache-Konsistenz (P1-28).**~~ **Abgeschlossen 2026-07-14:**
    Ein gemeinsamer `core/library2/tag_cache.py`-Adapter persistiert die
    Ergebnisse der bereits etablierten `core.tag_writer.read_file_tags`-Engine
    in `tags_json`, `missing_tags_json` und `metadata_gaps_json`; es gibt keinen
    zweiten Tag-Reader. Refresh & Scan aktualisiert Tagcache und Audioqualität
    unabhängig, sodass ein fehlgeschlagener Quality-Probe keine frischen Tags
    verhindert. Retag persistiert sowohl beim „unchanged“-Fastpath als auch
    nach erfolgreichem Schreiben den tatsächlich gelesenen Stand. Ein
    Lesefehler invalidiert alte Listen explizit auf JSON-`null` (unknown),
    statt alte Gaps oder fälschlich „gap-free“ anzuzeigen. **67 gezielte
    Scan-/Retag-/Query-/API-Tests** und Ruff sind grün. **Nächster logischer
    Schritt:** P2-02 — Missing-File-Lifecycle mit bestätigtem Zustand und
    Mount-sicherer Semantik vervollständigen.
21. ~~**Mount-sicherer Missing-File-Lifecycle (P2-02).**~~ **Abgeschlossen
    2026-07-14:** `lib2_track_files` persistiert additiv `missing_since` und
    `missing_scan_count`. Refresh & Scan führt bei glaubwürdig gesundem Root
    `active → missing_suspected → missing_confirmed` über zwei aufeinander
    folgende Misses; ein wieder sichtbares File wird `active` und setzt beide
    Felder zurück. Ein Miss zählt nur, wenn der direkte absolute Parent lesbar
    ist oder **alle** expliziten `library.music_paths`-Roots gemountet sind;
    unbekannte bzw. teilweise ausgefallene Mounts lassen den Zustand bewusst
    unverändert. `quarantined`/`deleted` werden vom Scan nicht überschrieben.
    Confirmed/deleted Files zählen in Artist-/Albumstatistiken nicht mehr als
    present, werden nicht auf Qualität bewertet und erscheinen im Trackstatus
    als missing; Pfad und Lifecycle bleiben zur Diagnose sichtbar. **89
    gezielte Schema-/Scan-/Multi-File-/Query-/API-/Wanted-Adapter-Tests** und
    Ruff sind grün. **Nächster logischer Schritt:** P2-05 — Artwork auf den
    verbindlichen `resolve_lib2_path`-Resolver umstellen.
22. ~~**Artwork-Path-Resolver vereinheitlichen (P2-05).**~~ **Abgeschlossen
    2026-07-14:** `core/library2/artwork.py::_resolve_abs` delegiert jetzt wie
    Scan, Retag und Skip-Cleanup an `core.library2.paths.resolve_lib2_path` und
    reicht denselben Config-Manager durch. Damit gilt die Section-1-Invariante
    ohne bekannte Ausnahme; Fehler bleiben best-effort (`None`). Zwei gezielte
    Delegations-/Fehlertests plus Ruff sind grün. **Nächster logischer Schritt:**
    ADR-05-Umsetzung (physisches Löschen) ist groß und destruktiv; davor den
    nächsten kleineren offenen Robustheits-/UX-Punkt P2-03 prüfen.
23. ~~**Wirksamer Manual-Skip-Audit (P2-03).**~~ **Abgeschlossen 2026-07-14:**
    Ein gemeinsamer `core/library2/manual_skips.py`-Helper bildet den Audit
    zweiphasig ab: Beim manuellen Dispatch werden deduplizierte Checks und die
    echte Request-`profile_id` gespeichert; nach erfolgreichem Post-Processing
    bindet der bestehende Importpfad den neuesten ungebundenen Eintrag an den
    tatsächlichen Finalpfad. Unbestätigte, profilbezogene Overrides werden nun
    auch konsumiert: Library-v2-Upgrade-Auswahl überspringt einen geschützten
    Primary-File-Pfad bei `quality`/`bit_depth`, der bestehende AcoustID-Repair-
    Job überspringt `acoustid`-geschützte Pfade vor dem Fingerprinting. Nach
    `acknowledged=1` greift der Schutz nicht mehr; der vorhandene Cleanup-Job
    bleibt für Pfad-/Retention-Ablauf zuständig. **55 gezielte Manual-Skip-/
    Wishlist-/AcoustID-/Import-Pipeline-/Repair-Tests**, Webserver-Compile und
    Ruff sind grün. **Nächster logischer Schritt:** P2-01 — Scan/Retag-I/O von
    lang gehaltenen SQLite-Verbindungen entkoppeln.
24. ~~**Kurze SQLite-Transaktionen für Scan/Retag (P2-01).**~~ **Abgeschlossen
    2026-07-14. Erste Slice:** Refresh & Scan materialisiert den DB-Scope
    einmal und schließt die Snapshot-Verbindung, bevor Pfadauflösung,
    Root-Health, Tag-Reader, Quality-Probe oder Größenabfrage das Dateisystem
    berühren. Tag- und Quality-Ergebnis werden danach pro File in einer kurzen
    Transaktion persistiert; der Missing-Lifecycle liest seinen aktuellen
    Zustand erst in dieser Transaktion neu, damit konkurrierende Änderungen
    nicht aus einem alten Snapshot überschrieben werden. Der bestehende
    `core.tag_writer.read_file_tags`-Pfad bleibt über einen read-only
    Tag-Cache-Helper die einzige Tag-Engine. Ein Connection-Lifecycle-
    Regressionstest sowie 20 gezielte Scan-/Retag-/API-Tests und Ruff sind
    grün. **Zweite/abschließende Slice:** Preview und Write materialisieren
    Track-, Album- und Artist-Credits vollständig in gebatchten DB-Reads.
    Danach laufen Pfadauflösung, Tag-Diff, Cover-Read und der unveränderte
    `core.tag_writer.write_tags_to_file` ohne offene Snapshot-Verbindung;
    unveränderte bzw. erfolgreich geschriebene Tag-Snapshots werden je File in
    einer kurzen Transaktion gespeichert. Preview behält sein 500-Track-Limit,
    der Write-Pfad verarbeitet weiterhin beliebig viele angeforderte Tracks in
    DB-Batches. Ein eigener Connection-Lifecycle-Test pinnt die Grenze; 61
    gezielte Retag-/Scan-/API-Tests, Compile und Ruff sind grün. **Nächster
    logischer Schritt:** ADR-05 als obersten offenen Architekturpunkt mit einer
    nicht-destruktiven Preview-/Journal-/Root-Safety-Slice beginnen; physisches
    Löschen erst nach abgesichertem Vertrag freischalten.
25. ~~**ADR-05 — getrenntes physisches Datei-Löschen.**~~ **Vollständig
    abgeschlossen 2026-07-14. Erste, nicht-destruktive Slice:** Der neue,
    admin-geschützte
    `GET /api/library/v2/<artists|albums>/<id>/file-delete-preview`-Vertrag
    materialisiert ausschließlich Files von Releases, die das Ziel wirklich
    besitzt, schließt SQLite und löst erst danach die Pfade über den
    verbindlichen `resolve_lib2_path` auf. Die Preview zeigt gruppierte
    physische Dateien, DB-File-IDs, Root, Größe, Sicherheitsgrund und einen
    deterministischen Snapshot-Token; mehrere DB-Links auf denselben Pfad
    werden nur einmal als physische Datei gezählt. Löschbar ist ein File nur,
    wenn sein kanonischer Realpath innerhalb eines explizit konfigurierten,
    aktuell vorhandenen `library.music_paths`-Roots liegt. Fehlende Roots,
    unauflösbare Pfade, Nicht-Dateien und Symlink-Escapes werden fail-closed
    abgewiesen. Die Route mutiert weder DB noch Filesystem; der bestehende
    Entity-Delete bleibt unverändert ein reines Unlink/Unmonitor-Command.
    48 gezielte File-Delete-/API-Tests, Compile und Ruff sind grün. **Zweite
    Slice abgeschlossen 2026-07-14:** Der separate
    `POST /api/library/v2/<artists|albums>/<id>/file-delete`-Command akzeptiert
    ausschließlich den exakten Preview-Token und vergleicht ihn unmittelbar
    vor Ausführung mit Größe, MTime, Realpath, Root und File-ID-Snapshot.
    `lib2_file_delete_operations` und `lib2_file_delete_items` journalisieren
    Operation und jedes physische File vor dem ersten `unlink`; unmittelbar
    davor wird `deleting` committed. Erfolg setzt alle auf denselben Pfad
    zeigenden DB-File-Zeilen über den vorhandenen ADR-03-Lifecycle auf
    `file_state='deleted'`, ohne Artist/Album/Track zu entfernen. Teilerfolge
    bleiben als `partial` mit Item-Fehlern lesbar; ein Prozessabbruch nach
    `unlink`, aber vor DB-Abschluss wird beim nächsten Execute aus dem
    persistenten `deleting`-Zustand abgeschlossen, während ein noch vorhandenes
    File fail-closed auf `failed` geht und nie nach Neustart automatisch
    gelöscht wird. Ein Read-Endpoint exponiert das Journal. Es werden keine
    Verzeichnisse entfernt. 71 gezielte Journal-/Schema-/Track-File-/API-Tests
    und Ruff sind grün. **Dritte/abschließende Slice:** Die vorhandene Delete-UI
    stellt „Remove from library“ und „Delete physical files“ als zwei
    unabhängige Aktionen dar. Der physische Bereich zeigt jede gruppierte Datei
    mit Pfad, Root, Größe und Blockgrund, fasst Anzahl/Gesamtgröße zusammen und
    aktiviert den irreversiblen Command erst bei vollständig sicherer Preview
    plus eigener „permanently deleted“-Checkbox. Er sendet exakt den sichtbaren
    Snapshot-Token und zeigt Teilerfolg/Journal-ID; Entity-Delete bleibt
    unverändert dateilos. Frontend-Format/Lint/Typecheck liefen ohne Warnungen,
    6 gezielte API-Vitests und der Production-Build sind grün. **Nächster
    logischer Schritt:** P2-04 — Artwork-Bytes/Dateiendung/Response-MIME
    konsistent machen, ohne die bereits funktionierende Cache-Invalidierung
    oder die bewusst noch ungeklärte Artist-vs.-Album-Priorität zu verändern.
26. ~~**Artwork-Cacheformat/MIME konsistent machen (P2-04).**~~ **Abgeschlossen
    2026-07-14:** Embedded- und Provider-Bytes queren vor dem atomaren
    Cache-Write genau eine Pillow-Grenze: Image-Decode validiert den Inhalt,
    EXIF-Orientierung wird angewendet, Transparenz auf Weiß komponiert und das
    Ergebnis als RGB-JPEG gespeichert. Ungültige Bytes erzeugen keine
    Cachedatei. Der `.jpg`-Cache und seine Thumbnails enthalten damit
    garantiert JPEG; alte PNG/WEBP-Rohbytes unter `.jpg` werden per Magic-Guard
    im Build- und HTTP-Fastpath erkannt, entfernt und beim nächsten Zugriff
    normal neu aufgebaut. Der HTTP-Vertrag bleibt dadurch korrekt
    `image/jpeg`; ein Routentest öffnet die gelieferten Bytes zusätzlich als
    echtes JPEG. Cache-Control, Refresh/Retag/Delete-Invalidierung und die
    vorhandene Embedded→Provider-Quellenengine wurden nicht verändert. Ebenso
    bleibt die separate Produktfrage, ob Artist-Art künftig Provider-Photos vor
    Embedded-Albumcovers priorisieren soll, bewusst unentschieden. 51 gezielte
    Artwork-/API-Tests, Compile und Ruff sind grün. **Nächster logischer
    Schritt:** P2-06 — UI-Mutationen müssen sichtbare, endliche Fehlerzustände
    statt stillem Scheitern oder Dauer-Loading liefern.

**Session-Abschluss-Gate 2026-07-14 (Roadmap 24–26):** P2-01 wurde in zwei
kleinen Slices geschlossen (`7aea92f9` Scan, `388f375e` Retag); DB-Snapshots
und lange Datei-I/O sind getrennt. ADR-05 wurde als drei getrennte Etappen
geliefert (`f37526e8` sichere Preview, `0eb79e7d` Journal/Execute/Recovery,
`b706ef93` explizite UI) und bleibt strikt unabhängig vom bestehenden
Entity-Delete. P2-04 normalisiert den Artwork-Cache auf echte JPEG-Bytes und
heilt alte Format-Mismatches (`6ad10fe7`). Das einmalige Session-End-Gate ist
grün: Python **8235 passed, 3 skipped, 2 deselected, 343 warnings in 240.34s**;
die drei Skips sind weiterhin ausschließlich opt-in Deployment-Contracts.
Frontend Format/Lint/Typecheck liefen ohne Fehler oder Warnungen, **18
Testdateien / 105 Vitests passed**, Production-Build erfolgreich (nur der
bekannte Main-Chunk-Hinweis). **Logischer nächster Schritt:** P2-06 als kleinste
    Mutation-für-Mutation-Slices beginnen — zuerst Monitor-Toggle und
    `monitor_new_items`-Save mit sichtbarem Fehler/Retry und garantiertem Ende des
    Loading-Zustands; anschließend dieselbe Fehlergrenze auf die restlichen
    Library-v2-Mutationen ausrollen.
27. ~~**Endliche, sichtbare UI-Mutationsfehler (P2-06).**~~ **Abgeschlossen
    2026-07-14. Erste Slice:** Der Bookmark-Monitor-Toggle zeigt einen fehlgeschlagenen
    Artist-/Album-/Track-Write jetzt als sichtbaren Alert mit dem redigiert vom
    API-Client gelieferten Fehler und bleibt danach für denselben Klick als
    Retry bedienbar. Der `monitor_new_items`-Select besitzt explizite
    Pending-/Success-/Error-Zustände, hält den fehlgeschlagenen Zielwert
    sichtbar und bietet einen eigenen Retry, der exakt diesen Wert erneut
    sendet. Beide Controls verlassen Pending bei Erfolg und Fehler; die
    Monitoring-/Wishlist-Backendsemantik wurde nicht verändert. Zwei neue
    Komponenten-Vertragstests, alle 11 Library-v2-Vitests, Frontend-Format/
    Lint/Typecheck und Production-Build sind grün. **Zweite Slice abgeschlossen
    2026-07-14:** Der Bulk-Monitor-Dialog wertet nun auch den terminalen
    Background-Job-Fehler aus (vorher wurde er trotz `error` geschlossen),
    zeigt Start-/Polling-/Job-Fehler sichtbar, beendet Busy zuverlässig und
    wiederholt per Retry exakt Scope und Zielzustand des fehlgeschlagenen
    Commands. Nur die tatsächlich laufende Option zeigt „Applying“. Drei
    Monitoring-Komponenten-Vertragstests sind grün. **Nächste Slice:** die
    Quality-Profile-Mutation mit derselben sichtbaren Error/Retry-Grenze
    absichern; danach die verbliebenen Artist-/Album-Mutationen inventarisieren.
    **Dritte Slice abgeschlossen 2026-07-14:** Eine abgelehnte Artist-/Album-
    Quality-Profile-Zuweisung bleibt jetzt im offenen Modal sichtbar und kann
    mit exakt derselben serverseitigen Profil-ID, Cascade- und
    `monitor_existing`-Entscheidung wiederholt werden. Erfolg schließt wie
    bisher erst nach Query-Invalidierung; der bestehende app-weite Profile-
    und Pipeline-Vertrag bleibt unverändert. Ein neuer Komponenten-
    Vertragstest pinnt Failure→Retry→Success. **Nächste Slice:** die übrigen
    Mutations-Catches in Artist-/Album-Detail, Retag, Search, Delete und
    Playlist-Trigger systematisch klassifizieren; bereits sichtbare Fehler
    nicht umbauen, sondern zuerst den nächsten tatsächlich stillen oder
    unendlichen Pfad schließen. **Vierte Slice abgeschlossen 2026-07-14:** Die
    per Album-/EP-/Singles-Sektion angebotene Bulk-Monitor-Action besitzt nun
    eine isolierte gemeinsame Komponente, die Start-, Polling- und terminale
    Job-Fehler sichtbar macht. Der alte Pfad ignorierte insbesondere das von
    `awaitBulkJob` gelieferte `state.error`. Busy endet in allen Fällen; Retry
    wiederholt exakt Artist, Release-Scope und Monitor-Ziel. Vier gezielte
    Monitoring-Vertragstests sind grün. **Nächste Slice:** Artist „Refresh &
    Scan“ meldet Fehler derzeit nur als abgewiesenes Promise und muss einen
    sichtbaren Retry erhalten; anschließend Import-Polling-Timeout und
    Interactive-Grab-Fehlertext prüfen. **Fünfte Slice abgeschlossen
    2026-07-14:** Artist „Refresh & Scan“ läuft nun durch einen isolierten
    React-Query-Mutationsvertrag. Backend-/Scan-Fehler erscheinen sichtbar am
    Toolbar-Control, Pending endet garantiert und derselbe Button wird zu
    „Retry Refresh & Scan“; Erfolg invalidiert weiterhin ausschließlich die
    zentrale Library-v2-Query. Ein Komponenten-Vertragstest beweist
    Failure→Retry→Success. **Nächste Slice:** Import-Polling muss bei
    ausgeschöpftem Zeitbudget explizit von „Importing…“ auf Timeout wechseln;
    danach den Interactive-Grab-Fehlergrund sichtbar machen. **Sechste Slice
    abgeschlossen 2026-07-14:** Der Import-Button nutzt nun einen gemeinsamen
    endlichen Status-Poller. Ein terminaler Importzustand wird unverändert
    ausgewertet; bleibt der Job über das 10-Minuten-Budget hinaus `running`,
    wirft der Poller einen sichtbaren Timeout statt den reaktivierten Button
    mit dem falschen Dauertext „Importing…“ zurückzulassen. Zwei gezielte
    Polling-Vertragstests pinnen Timeout und terminalen Fehlerzustand.
    **Nächste Slice:** Interactive-Grab bewahrt bislang nur den generischen
    Buttonzustand „Retry“, verwirft aber den konkreten API-Fehler; Grund
    sichtbar machen und denselben Kandidaten retrybar halten. **Siebte Slice
    abgeschlossen 2026-07-14:** Interactive Search speichert den redigierten
    Downloadfehler pro Ergebnis-Key und zeigt ihn direkt unter dem weiterhin
    bedienbaren Retry. Ein erneuter Klick löscht nur den Fehler dieser Zeile
    und dispatcht dasselbe serverseitig ausgewählte Ergebnis mit denselben
    Quality-/AcoustID-/Entity-Optionen; andere Kandidatenzustände bleiben
    unberührt. Ein Komponenten-Vertragstest beweist Error→identischer
    Retry→Grabbed. **Nächster logischer Schritt:** P2-06-Inventur fortsetzen:
    Mirror-Outbox-Retry zeigt aktuell keinen eigenen API-Fehler, und der
    Playlist-Pipeline-Start zeigt zwar den Grund, labelt denselben Button aber
    nicht explizit als Retry. Diese beiden kleinen Mutationsgrenzen als nächste
    Slice schließen, bevor P2-07 (verschachtelte Buttons) aufgenommen wird.
    **Achte/abschließende Slice 2026-07-14:** Ein fehlgeschlagener Mirror-
    Outbox-Retry zeigt nun seinen konkreten API-Fehler und labelt die weiterhin
    bedienbare Action „Retry again“. Ein abgelehnter Playlist-Pipeline-Start
    zeigte den Grund bereits; sein Button heißt danach jetzt explizit „Retry
    pipeline“. Zwei neue Komponenten-Vertragstests beweisen beide
    Failure→Retry-Pfade. Die abschließende Inventur aller Library-v2-
    Mutationsgrenzen bestätigt: Delete/File-Delete, Metadaten-Edit, Manage
    Tracks, Maintenance, Retag, Search, Discography, Upgrade-Scan und
    Wishlist-Processing besaßen bereits sichtbare endliche Fehlerzustände bzw.
    einen direkt wieder bedienbaren Command; stille bzw. fälschlich dauerhafte
    Pfade sind geschlossen. Insgesamt sind 20 Library-v2-Vitests,
    Frontend-Format/Lint/Typecheck und Production-Build grün. **Nächster
    logischer Schritt:** P2-07 — Artist-Karten dürfen den Monitor-Button nicht
    länger in einen zweiten Button verschachteln; semantische Card-Navigation
    und Toggle als getrennte interaktive Elemente modellieren.
28. ~~**Gültige Artist-Card-Interaktion (P2-07).**~~ **Abgeschlossen
    2026-07-14:** Die Karte ist nun ein nicht-interaktiver `article`; ihr
    vollflächiger, fokussierbarer Navigations-Button und der darüberliegende
    Bookmark-Monitor-Toggle sind echte Geschwister statt verschachtelte
    Buttons. Maus-/Toggle-Klicks können dadurch keine Navigation mehr
    miterzeugen, native Button-Tastatursemantik und ein sichtbarer
    `focus-visible`-Ring bleiben erhalten. Ein Komponenten-Vertragstest
    verbietet `button button`, prüft getrennten Fokus/Klick und pinnt, dass
    Monitoring genau einen API-Write ohne Card-Navigation auslöst. 21
    Library-v2-Vitests, Frontend-Format/Lint/Typecheck und Production-Build
    sind grün. **Nächster logischer Schritt:** P2-08 — die heute globalen
    „Search Monitored“-/„Search Upgrades“-Actions nicht länger optisch als
    Artist-/Album-Scope darstellen; Scope im Label und Bestätigungs-/Status-
    Text unmissverständlich machen, ohne eine zweite Suchpipeline zu bauen.
29. ~~**Globale Search-Actions ehrlich beschriften (P2-08).**~~ **Abgeschlossen
    2026-07-14:** Die Artist-Toolbar sagt jetzt sichtbar „Search All Monitored
    (global)“ und „Search All Upgrades (global)“; Pending-, Success- und
    Tooltip-Texte benennen ausdrücklich die gesamte Wishlist bzw. den ganzen
    Library-v2-Katalog. Das albumlokal platzierte Such-Icon kennzeichnet sich
    ebenfalls als globale Wishlist-Action. Die Implementierung bleibt
    unverändert bei `POST /api/wishlist/process` und dem einen bestehenden
    `lib2_upgrade_scan`; es entstand kein scheinbarer Artist-/Album-Filter und
    keine zweite Search-/Upgrade-Pipeline. 21 Library-v2-Vitests,
    Frontend-Format/Lint/Typecheck und Production-Build sind grün. **Nächster
    logischer Schritt:** P2-09 — Interactive Search muss die vorhandene
    Source-Auswahl ehrlich nutzen bzw. anzeigen; zuerst den bestehenden
    `/api/search/sources`-Vertrag und die Main-Pipeline-Modi inventarisieren,
    ohne eine parallele Source-Decision zu bauen.
30. ~~**Interactive-Search-Sourceauswahl ehrlich verdrahten (P2-09).**~~
    **Abgeschlossen 2026-07-14:** Der bestehende `/api/search/sources`-
    Vertrag wird nun typisiert mit Modus, interner Source-ID und Displayname
    gelesen. Interactive Search zeigt die konfigurierte Default-/erste
    Priority-Source ehrlich statt „Searching all configured sources“ und
    bietet jede vom Server gelieferte Source zur expliziten Auswahl an. Ein
    Pick wird ausschließlich als vorhandener `source`-Parameter an
    `/api/search` gereicht; ohne Pick entscheidet weiterhin der bestehende
    Backend-Orchestrator. Es gibt weder Client-Fan-out noch clientseitiges
    Ranking oder eine zweite Source-Decision. Zwei Interactive-Search-
    Komponententests pinnen identischen Candidate-Retry und den exakten
    Default-/expliziten Source-Request. 22 Library-v2-Vitests,
    Frontend-Format/Lint/Typecheck und Production-Build sind grün. **Nächster
    logischer Schritt:** P2-11 — unbekannte Publish-Daten beim Descending-Age-
    Sort stabil hinter bekannte Releases stellen, ohne die Source-Daten oder
    Ranking-Defaults zu erfinden.
31. ~~**Unknown-Age-Sort stabilisieren (P2-11).**~~ **Abgeschlossen
    2026-07-14:** Der bestehende Interactive-Search-Sortierer ist als reine
    Funktion gekapselt. Beim Age-Key bilden fehlende und ungültige
    Publish-Daten nun eine explizite Unknown-Gruppe, die sowohl auf- als auch
    absteigend hinter allen bekannten Daten bleibt; bekannte Releases und der
    vorhandene Quality-/Size-Tiebreak behalten ihre bisherige Reihenfolge.
    Es werden weder Alter noch Provider-Fakten geschätzt. Ein Regressionstest
    pinnt beide Richtungen inklusive fehlendem und ungültigem Datum. 23
    Library-v2-Vitests, Frontend-Format/Lint/Typecheck und Production-Build
    sind grün. **Nächster logischer Schritt:** P2-12 — „Monitor all“ darf aus
    „My Library“ nicht unsichtbare provider-only Releases miterfassen; UI-
    Scope und Backend-Scope müssen denselben sichtbaren Satz meinen.
32. ~~**Sichtbarer Bulk-Monitor-Scope (P2-12).**~~ **Abgeschlossen
    2026-07-14:** Der vorhandene Release-Bulk-Monitor akzeptiert optional eine
    streng validierte `album_ids`-Allowlist und schneidet sie serverseitig
    zusätzlich mit Artist und Release-Type-Scope. Die Album-/EP-/Singles-
    Sektionen senden exakt die IDs, die der aktuelle „My Library“-/„All
    Releases“-View anzeigt; Retry bewahrt dieselbe Liste. Eine leere Allowlist
    bleibt leer, fremde/verborgene Releases werden nie durch den groben Scope
    ergänzt. Der Monitoring-Dialog ohne sichtbaren Listenfilter verwendet
    bewusst weiter den bestehenden vollständigen `all`/`missing`-Scope. Rules,
    Wanted-Projektion und Wishlist-Mirror laufen unverändert durch denselben
    Bulk-Worker. Drei gezielte Backend- und vier Monitoring-UI-Tests sowie
    Ruff, 23 Library-v2-Vitests, Frontend-Format/Lint/Typecheck und
    Production-Build sind grün. **Nächster logischer Schritt:** P2-13 —
    gleichzeitigen manuellen und periodischen Discography-Sync pro Artist
    serialisieren und Snapshot-/Auto-Monitor-Folgen deterministisch halten.
33. ~~**Per-Artist-Discography-Sync-Serialisierung (P2-13).**~~ **Abgeschlossen
    2026-07-14:** Eine gemeinsame reentrante In-Process-Grenze keyed nach
    Datenbank und Artist serialisiert den kompletten Provider-Snapshot-/
    Katalog-Refresh. Der neue gemeinsame `refresh_artist_discography`-Helper
    hält dieselbe Grenze zusätzlich über die anschließende Tracklist-
    Materialisierung und Wishlist-Mirroring neu auto-monitorierter Releases.
    Manueller API-Endpoint und periodischer Repair-Job rufen beide exakt diese
    Sequenz; ein zweiter Consumer-Workflow entstand nicht. Verschiedene Artists
    behalten getrennte Locks. Ein Zwei-Thread-Regressionstest blockiert den
    ersten Providerlauf kontrolliert und beweist `max_active == 1` für denselben
    Artist. Ein zweiter Regressionstest hält die Auto-Monitor-Phase gezielt
    offen und weist nach, dass auch sie Teil derselben Grenze ist; alle 18
    Discography-Tests sowie Ruff sind grün. **Nächster
    logischer Schritt:** P2-15 — den providerlosen Deezer-Tracklist-Fallback
    mit vorhandenen Jahr-/Trackcount-/External-ID-Fakten gegen falsche
    Editions absichern, weiterhin innerhalb des typed Provider-Adapters.
34. ~~**Deezer-Tracklist-Fallback an Edition-Fakten binden (P2-15).**~~
    **Abgeschlossen 2026-07-14:** Der vorhandene typed Tracklist-Adapter
    reichert einen namenbasierten Deezer-Suchtreffer über den bereits
    bestehenden Deezer-Client an und akzeptiert ihn fail-closed nur, wenn alle
    bekannten Editionsfakten übereinstimmen: Release-Jahr, erwartete Trackzahl
    und UPC/Barcode. Eine vorhandene Deezer-ID bleibt als harte Identität der
    direkte Pfad und benötigt keine heuristische Gegenprüfung. Der bestehende
    `resolve_tracklist`-Consumer reicht die effektiven Fakten der
    Default-Edition weiter; dieselben Fakten sind Bestandteil der bestehenden
    Provider-Snapshot-Referenz, sodass alte oder nach Editionsänderungen
    unpassende Tracklist-Caches invalidiert werden. Es entstand weder ein
    zweiter Resolver noch ein zweiter Importpfad. **40 gezielte Adapter-,
    Completeness-, Snapshot- und Editions-Tests** sowie Ruff sind grün.
    **Nächster logischer Schritt:** P2-16 — fehlende oder ungültige
    Qualitätsinformationen im zentralen `quality_eval.py` als explizites
    `unknown` statt als fälschliches `meets_profile=True` modellieren.
35. ~~**Tri-State-Qualitätsauswertung (P2-16).**~~ **Abgeschlossen
    2026-07-14:** Das bestehende zentrale `quality_eval.py` liefert für eine
    fehlende, explizit unbekannte oder ungültige Dateiqualität nun
    `meets_profile=null` und `upgrade_candidate=null`; ein Profil ohne Ziele
    bleibt dagegen korrekt unbeschränkt. Albumdetail und React-Typvertrag
    bewahren den dritten Zustand, und die UI zeigt „quality unknown“ mit dem
    Hinweis auf Refresh & Scan statt eines stillen Erfolgs. Der bestehende
    Wishlist-Mirror schickt unbekannte Qualität bei aktivem Upgrade-Profil zur
    erneuten Prüfung durch dieselbe Probe-/Upgrade-Pipeline und kennzeichnet
    diese Entscheidung im vorhandenen `source_info`; bekannte erfüllte und
    echte Upgrade-Fälle bleiben unverändert getrennt. Es entstand keine zweite
    Quality-Decision-Engine. **37 gezielte Quality-/Query-/Wishlist-/Parity-
    Tests**, **10 Library-v2-Vitest-Dateien / 25 Tests**, Frontend-Format/Lint/
    Typecheck, Production-Build und Ruff sind grün (nur der bekannte
    Main-Chunk-Hinweis). **Nächster logischer Schritt:** P2-17 — die
    Albumdetail-/Index-Queries mit realistischen Row-Counts profilieren und
    zunächst die belegten N+1-/korrelierten Hotspots in den bestehenden Query-
    Helpern bündeln.
36. ~~**Query-Skalierung für Index und Details (P2-17).**~~ **Abgeschlossen
    2026-07-14. Erste Slice:** Die vier korrelierten Artist-Index-
    Statistiksubqueries sind durch zwei gruppierte CTEs für Album-/Single- und
    wanted-or-owned Track-/Present-Zähler ersetzt. Artist-Detail zählt Tracks
    und vorhandene Files in demselben gruppierten Release-Read statt über zwei
    korrelierte Subqueries je Release. Der bestehende Metadata-Override-Store
    bietet einen Batch-Read, sodass Artist- und Release-Resultsets ihre
    effektiven Felder mit einer Abfrage statt N Entity-Abfragen projizieren.
    Ein SQL-Trace-Regressionstest ergänzt je 20 Artists und Releases und pinnt
    für beide Read-Pfade eine konstante Statement-Zahl. **Zweite/abschließende
    Slice:** Albumdetail lädt Primary Files in einem Window-Read nach der
    bestehenden ADR-03-Reihenfolge und bündelt Track-Credits sowie Artist-/
    Track-Overrides über dieselbe Batch-Projektion. Fehlende Audiofakten werden
    aus einem gemeinsamen Legacy-`track_downloads`-Candidate-Read aufgelöst;
    die bisherige Priorität (exakter Pfad, Filename, Hard IDs, danach
    Titel/Artist/Album) bleibt erhalten. Der Einzeltrack-Read nutzt weiterhin
    dieselben kompatiblen Helper. Zwei SQL-Trace-Verträge pinnen konstante
    Statement-Zahlen nach je 20 zusätzlichen Artists/Releases bzw. Tracks;
    Primary- und Provenance-Priorität besitzen eigene Regressionstests.
    **316 Library-v2-Tests** sowie Ruff sind grün. **Nächster logischer
    Schritt:** P2-18 — die inventarisierten Request-Validierungslücken in
    kleinen Endpoint-Gruppen schließen, beginnend mit ungeschützten `int()`-
    Konversionen vor Background- oder Mutation-Starts.
37. ~~**Request-Validierungsgrenzen schließen (P2-18).**~~ **Abgeschlossen
    2026-07-14:** Artist-Pagination lehnt nichtpositive Seiten und Limits
    außerhalb 1–500 ab; Acquisition-History validiert 1–1000 vor jedem
    Request-Lookup, Artist-History 1–200 vor dem Read. Quality-Profile-
    Assignment akzeptiert nur positive Integer-IDs und Objekt-JSON, bevor es
    eine DB-Zeile verändert. Retag-Starts akzeptieren nur ein Objekt mit 1 bis
    `MAX_TRACKS` positiven Integer-IDs, deduplizieren erst danach und können
    bei ungültigem Input keinen Job belegen. Persistiertes ungültiges bzw.
    nichtobjektförmiges `repair_settings`-JSON wird vor der Mutation geloggt und
    zu `{}` normalisiert, statt erst nach dem Commit einen 500er zu erzeugen.
    Die bestehenden tieferen Validatoren bleiben erhalten; es entstand kein
    zweiter Command-Pfad. **76 API-Tests** sowie Ruff sind grün. **Nächster
    logischer Schritt:** P2-20 — Prozent-/Progresswerte an der gemeinsamen
    Job-/Import-Statusgrenze auf 0–100 klemmen und Über-/Unterlauf mit
    Vertrags-Tests pinnen.

**Session-Abschluss-Gate 2026-07-14:** Seit dem vorherigen Full-Gate wurden
Roadmap 13 sowie 16–23 und Phase E vollständig abgeschlossen und jeweils
gezielt getestet, dokumentiert, committet und gepusht. Der abschließende
Gesamtstand ist grün: Python **8219 passed, 3 skipped, 2 deselected**; Frontend
Format/Lint/Typecheck ohne Fehler oder Warnungen, **18 Testdateien / 104
Vitests passed** und Production-Build erfolgreich. **Nächster logischer
Schritt bleibt P2-01:** Scan und Retag sollen den Lesesnapshot vor langer
Dateisystem-I/O erfassen, die SQLite-Verbindung schließen und Ergebnisse in
kleinen, klar begrenzten Transaktionen zurückschreiben.

---

## 8. Retry-Persistenz nach Quality-/Integrity-/AcoustID-Fehlern (implementiert 2026-07-13)

Dieser Punkt war als eigener Folgejob offen (Teil von LIB2-F07). Der
bestehende Worker besitzt Kandidatenliste, `used_sources`, erschöpfte Quellen
und Retry-Zähler bisher nur im RAM. Für Library-Acquisition muss dieser
Zustand nach einem Neustart wiederherstellbar werden, ohne die bestehende
Auswahl- oder Retry-Logik zu duplizieren.

> **Status: implementiert am 2026-07-13** (Commits `e3eca302` Journal-Modul,
> `899536db` Write-/Close-Hooks, `364262bf` Restart-Resume). Ein früherer
> Anlauf (`17a309fa`) war als unverdrahtetes Standalone-Modul reverted worden
> (`4d03bd30`); diese Umsetzung folgt der untenstehenden Spec vollständig.
> Umsetzungsdetails:
> - `core/acquisition/retry_state.py`: Tabelle `acquisition_retry_state`
>   (PK = Legacy-`task_id`, plus `import_id`/`track_id`), redigierter
>   Kandidaten-Snapshot per Feld-Whitelist (nie `_source_metadata`/URLs/
>   Magnets/Tokens), used/exhausted Sources, Zähler pro Quelle + gesamt,
>   `query_count`, Status `active/completed/failed/approved/cancelled`
>   (terminale Rows können nie reaktiviert werden), 7-Tage-Expiry.
> - Hooks (`899536db`): `requeue_quarantined_task_for_retry` snapshottet den
>   Walk VOR der Worker-Resubmission (bzw. schließt die Row bei Cancel/
>   Budget-Erschöpfung) — dafür wurde die Funktion in einen gelockten
>   Entscheidungs-Teil und Journal-I/O außerhalb von `tasks_lock` zerlegt;
>   `attempt_download_with_candidates` persistiert jede neue used-Source vor
>   dem externen Download-Start; `record_pipeline_file_completed`/`record_
>   import_failure` schließen Rows in derselben Transaktion; Quarantäne-
>   Approve und Task-Cancel schließen via `pipeline_callback`-Notifier.
>   Alles fail-open; Legacy-Tasks ohne Acquisition-Marker zahlen nur einen
>   Dict-Lookup.
> - `core/acquisition/retry_resume.py` (`364262bf`): läuft am Anfang jedes
>   `advance_open_imports`-Zyklus (also im 15s-Monitor-Takt), purged expired
>   Rows und baut für jede aktive Row ohne lebenden `download_tasks`-Eintrag
>   den Legacy-Task wieder auf (Track-Kontext aus dem persistierten
>   Import-Plan via Bridge-`_pipeline_context`, Kandidaten als rekonstruierte
>   `TrackResult`s inkl. `confidence`, `_quarantine_retry=True` für den
>   Cached-First-Walk) und resubmittet den EXISTIERENDEN Worker.
> - Manual Picks journalen nie (Requeue verweigert sie vor jedem Snapshot) —
>   ein Resume kann daher nie eine manuelle Kandidatenwahl überschreiben.
> - Bewusste Grenzen: (1) Cancel-Pfade jenseits des Einzel-Task-Cancel-
>   Endpoints (z.B. Cancel-All) schließen Rows nicht sofort — terminale
>   Import-Transitions + TTL decken das ab; (2) wird der Retry-Toggle
>   (`retry_next_candidate_on_mismatch`) mitten im Walk deaktiviert, bleibt
>   die Row bis zum TTL aktiv — ein Resume walkt dann einmal die Kandidaten,
>   eine erneute Quarantäne wird aber ohne Requeue normal fehlschlagen;
>   (3) die echte Docker-Restart-Acceptance steht noch aus (Teil des offenen
>   Deployment-Acceptance-Punkts in 5.5).
> Tests: `tests/acquisition/test_retry_state.py`,
> `test_retry_journal_hooks.py`, `test_retry_resume.py`.

**Umsetzung (ursprüngliche Spec, unverändert gültig):**
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

---

## 10. Audit-Nachtrag (2026-07-10-Architektur-Audit, hierher konsolidiert)

Diese Section fasst zusammen, was aus
`docs/library-v2-architecture-audit-2026-07-10.md` (3145 Zeilen, seither
gelöscht) inhaltlich erhalten bleiben muss: getroffene Architektur-
entscheidungen (ADRs), bereits gefixte Findings ohne bisherige Doku-Spur, und
Findings, die bisher NICHT in Abschnitt 7 (Roadmap) auftauchten. Der Rest des
Audits (Verifikations-Snapshots, Lidarr-Vergleichstabellen, das ursprünglich
vorgeschlagene, inzwischen durch ADR-04 abgelöste `library_*`-Zieldatenmodell,
Test-/Observability-/Security-Checklisten, 19-Punkte-Definition-of-Done) war
zum Löschzeitpunkt entweder überholt oder reine Methodik ohne Entscheidungs-
wert und wurde bewusst nicht übernommen.

### 10.1 ADR-Log (Kernentscheidungen, Audit-Kapitel 20/25a)

- **ADR-01 — Ist Library v2 profilbezogen? Entschieden: Admin-only.** Library
  v2 kennt nur einen maßgeblichen Nutzer-Intent: das Admin-User-Profil
  (`profiles.id=1`, `is_admin=1`). Andere Haushalts-/User-Profile haben
  keinen eigenen Monitoring-/Wanted-Zustand in Library v2 (ihre eigene Legacy-
  Watchlist/Wishlist außerhalb Library v2 bleibt unberührt). Wichtig: das ist
  eine andere Achse als **Quality-Profile** (`quality_profiles`,
  Lidarr-artige Presets) — die bleiben app-weit und pro Artist/Album/Track
  zuweisbar, nur das *Zuweisen/Ändern* ist ebenfalls auf Admin beschränkt.
  Technisch erzwungen (nicht nur UI-Ausblendung): `core/library2/importer.py`
  lehnt Fremdprofile hart ab (`6ab520f`), Library-v2-Writes sind auf
  Profil 1 begrenzt (`10bfdd6`). Macht P0-02 (globaler Monitorzustand
  kollidiert mit Multi-Profil) obsolet, nicht durch neues Datenmodell,
  sondern durch erzwungene Beschränkung — die für Multi-Profil ursprünglich
  geplante `monitor_rules`/Wanted-Projektions-Phase ist dadurch stark
  vereinfacht (existiert trotzdem, siehe ADR-02, aber ohne Merge-Problem
  zwischen mehreren Nutzer-Profilen).
- **ADR-02 — Source of Truth für „Wanted"/Monitoring.** Übergang
  (umgesetzt): Option 3 — `lib2_*` und Legacy-Wishlist/Watchlist bleiben
  nebeneinander, aber über eine transaktionale Outbox (`lib2_mirror_outbox`,
  `bdc95b2`) statt best-effort gespiegelt, plus periodischer Reconciler
  (`3ca3000`). **Track-Cutover abgeschlossen 2026-07-14:** Option 1 gilt für
  Wanted-Consumer — `lib2_monitor_rules` → `lib2_wanted_tracks` ist die
  alleinige Track-Intent-Wahrheit, die Wishlist nur noch abgeleitete
  Ausführungsliste (Abschnitt 7 Punkt 5). Option 2 (Wishlist bleibt Wahrheit,
  lib2 nur Anzeige) wurde bewusst verworfen.
- **ADR-03 — File-Kardinalität. Entschieden: Multi-File-Modell mit
  `is_primary`.** Mehrere Dateien pro Track bleiben erlaubt (z.B.
  FLAC+MP3 derselben Aufnahme, alte+neue Datei während Upgrade), aber mit
  definierter Auswahl statt `ORDER BY id LIMIT 1` (willkürlich älteste
  Zeile). **Umgesetzt** (`1df403d`): `lib2_track_files` hat `is_primary` +
  `file_state` (`active`/`missing_suspected`/`missing_confirmed`/
  `quarantined`/`deleted`); Auswahlstrategie (active > lossless >
  Bit-Tiefe/Sample-Rate/Bitrate > neueste Zeile) über Insert-/Move-/
  Delete-Trigger erzwungen. Alle vorher willkürlichen Read-Pfade
  (Track-Serialisierung, Wishlist-Upgrade-Eval, Retag, Track-File-Move,
  Duplicate-View, Embedded-Artwork) nutzen jetzt die Primary-Datei.
- **ADR-04 — Release Group vs. Edition. Entschieden: immer beide
  modellieren** (Lidarr-Vorbild `Album`/`AlbumRelease`). Bereits in
  Abschnitt 3 „Provider Snapshots"/§3 „Refresh & Scan" grob erwähnt; hier
  die vollständige Entscheidung: additives Shadow-Modell (`7743641`) —
  `lib2_albums` bleibt Release Group, neu sind `lib2_release_editions`
  (genau eine Default-Edition je Album, partieller Unique-Index),
  `lib2_recordings` (harte IDs) und `lib2_release_tracks`
  (Kompat-Link auf `lib2_tracks`). Recordings mergen NUR über
  ISRC/MB-Recording-ID/Spotify-ID — Titel mergen nie (Live/Remaster bleiben
  getrennt); unverifizierte Canonical-Links landen als
  `lib2_recording_review`-Findings statt still gemergt zu werden. Größter
  strukturell Einzelumbau im ganzen Fahrplan; Discography-Matching/
  Duplicate-Linking lesen noch nicht vollständig aus dem neuen Modell
  (bleibt Shadow, siehe Roadmap Punkt 4).
- **ADR-05 — Löschsemantik. Entschieden: getrennte Commands +
  Preview/Journal.** „Library-Entity entfernen" (Katalogeintrag,
  Monitoring, Verknüpfungen) und „physische Datei löschen" sind und bleiben
  zwei unabhängige, einzeln auslösbare Aktionen — nie stillschweigend
  kombiniert. Jede physische Löschung muss vorher eine Preview zeigen
  (betroffene Dateien, Root, Anzahl); wo möglich Recycle-Bin/Journal statt
  sofortigem `unlink()`. Physisches Löschen wird bewusst erst freigeschaltet,
  wenn Preview/Journal/Root-Safety produktiv stehen — **vollständig umgesetzt
  2026-07-14:** getrennte UI-Commands, fail-closed Preview/Token/Root-Safety,
  persistentes Journal, per-File-Execute und Crash-Recovery (Roadmap-Punkt 25).
- **ADR-06 — Providerpriorität und User Overrides. Entschieden: getrennt
  speichern, Override gewinnt.** Providerfelder tragen eigene Provenance
  (`provider_updated_at`, Snapshot-Version) und werden bei Refresh
  korrigiert, außer ein Nutzer hat das konkrete Feld explizit überschrieben;
  Overrides liegen strukturell getrennt von Providerdaten. Löst weg vom
  bisherigen `COALESCE`-Verhalten (einmal gesetzter Wert bleibt für immer,
  auch wenn er falsch war). **Status 2026-07-12: Infrastruktur umgesetzt**
  (`library_provider_snapshots` mit Provenance/Completeness/Hash, `c396a4f`;
  typisierte Discography-/Tracklist-Adapter, `bd5d29d`/`16210f5`; gezielte
  Tracklist-Invalidierung bei Edition-/Providerwechsel). **Field-Level-
  User-Overrides und zentrale Read-Projektion sind seit 2026-07-14 ebenfalls
  umgesetzt** (`lib2_metadata_overrides`; Roadmap Punkt 4, dritte Slice).
  Der letzte direkte Library-v2-Providerpfad (Artwork) quert seit der vierten
  Roadmap-4-Slice ebenfalls die typed Boundary. Verbliebene `COALESCE`-Updates
  sind lokale Merge-Policy in Import/Refresh bzw. File-Probing, keine
  provider-spezifischen Wire-Dict-Grenzen.
- **ADR-07 — Interne Queue vs. Client-Queue. Entschieden: Client ist
  Live-Queue.** Bereits ausführlich in Abschnitt 3 „Phase 4
  Acquisition/Decision" beschrieben (persistente Grab-Korrelation, Adoption
  nach Neustart, zweistufiger Cancel). Diese ADR liefert die Begründung:
  entspricht Lidarrs Modell — Live-Progress aus dem externen Client lesen
  ist robuster als eine intern gespiegelte Queue, die bei jedem Neustart neu
  synchronisiert werden müsste. Torrent-Ausweitung (Phase 6) steht noch aus.
- **ADR-08 — Track- vs. Albumquellen. Entschieden: explizite Source
  Capabilities.** Bereits in Abschnitt 3 erwähnt (verhindert Track-/Bundle-
  Verwechslungen). Begründung: jede Downloadquelle deklariert
  `recording_download`/`release_bundle_download` (exklusiv),
  `search_by_id`, `client_queue`, `supports_cancel/remove`,
  `supports_quality_metadata` statt dass Decision-Engine/Auto-Grab/Import
  Quellentyp über Username-/Dateiname-Heuristik raten (behebt zugleich
  P2-10 „Torrent wird im Modal als Soulseek dargestellt" und P1-18
  „Auto-Grab kann Albumresultat bei Trackaktion wählen").

### 10.2 Weitere gefixte Findings ohne bisherige Doku-Spur (Commit-Referenzen)

Diese waren im Audit selbst (§16.1) bereits als geschlossen mit Commit-Hash
vermerkt, tauchten aber nirgends in diesem Dokument auf:

- **P0-01** — Artist-Delete unterschied nicht Primary- von
  Featured-Zuordnung (löschte fremde/geteilte Alben mit). Fix: Löschen
  respektiert die Junction-Rolle, UI zeigt Impact-Vorschau (`1efa72d`).
- **P0-04** — Lib2↔Wishlist-Mirror war nicht atomar (Split-Brain-Risiko).
  Fix: `lib2_mirror_outbox` in derselben Transaktion wie der Lib2-Write,
  idempotenter Drain-Worker, strikte Fehlerweitergabe (`bdc95b2`,
  gehärtet `895d27e`), periodischer Reconciler (`3ca3000`).
- **P1-08/09/10/11** — Wishlist-Add war nicht idempotent (Composite-ID-Bug),
  aktualisierte Profil/Source bei Re-Add nicht, und automatische Mirrors
  überschrieben fälschlich User-Ignore-Entscheidungen. Fix: profil-/track-/
  albumbezogene Composite-Identität mit Upsert-Semantik (`ebdd8a0`); nur
  direkte Track-Aktionen setzen `user_initiated=true` (`a531111`).
- **P1-12** — Providerlose Wishlist-IDs (`lib2-track:<surrogate>`) waren
  nicht migrationsstabil (Reset/Reimport konnte IDs verschieben). Fix:
  persistente `stable_id` (deterministischer Hash der natürlichen
  Identität), Reset+Reimport reproduziert dieselbe ID (`52b0e51`).
- **P1-13/14** — Monitored war nicht zuverlässig „wird gesucht", und
  Album-Unmonitor überschrieb bewussten Track-Level-Intent. Fix:
  `lib2_monitor_rules` mit Provenance
  (`user_explicit`/`wishlist_import`/`cascade`/`new_release`/`legacy_import`); Kaskaden
  überschreiben explizite Track-Entscheidungen nicht mehr (`705beb4`), plus
  `lib2_wanted_tracks` als materialisierte Wanted-Projektion (`45fc67a`).
  Seit Roadmap-Punkt 5 wird sie von Track-Reads, Acquisition/Upgrade und
  Wishlist-Mirror angewendet; Flag-Divergenz bleibt als Metrik sichtbar.
- **P1-15/16/17** — Profilzuweisung setzte ungewollt Tracks auf monitored;
  Interactive Search sendete weder Entity- noch Quality-Profile-Kontext;
  Album-Suche nutzte fälschlich das Artist-Profil. Fix: Profil-Zuweisung und
  Monitoring als getrennte Commands (`bb7c815`); manuelle Grabs tragen
  Lib2-Track-/Album-ID bis zum Post-Processing, Server löst das Profil selbst
  auf (`195e5c6`).
- **P1-29** — Eingecheckter Frontend-Stand war nicht typecheck-stabil
  (fehlende `routeTree.gen.ts`-Route). Fix: CI-Gate committet
  Route-Tree-Diff als Pflichtprüfung (`b498b66`, `b53ce43`, `4845c9f`).
- **P1-01 (Ergänzung zu §1-Invariante)** — Profil-1-Fallback war nicht nur
  Laufzeit-Default, sondern auch Schema-`DEFAULT 1` ohne FK. Vollständig
  behoben über drei Commits: Löschung remapped transaktional (`df285b9`),
  Inserts lösen das Default-Profil live auf (`31a1fb1`), Schema nutzt echte
  FKs ohne numerischen Default (`9e716ab`).

### 10.3 Bisher nicht in Abschnitt 7 (Roadmap) getrackte offene Punkte

**Aus dem Audit übernommene, weiterhin offene Findings:**

- ~~**P1-02** — Legacy-Import reconciliert Löschungen/Pfadänderungen nicht:
  entfernte Legacy-Zeilen bleiben als Phantom-Lib2-Zeilen bestehen, ein
  geänderter `file_path` erzeugt eine neue File-Zeile statt die alte zu
  ersetzen. Braucht Snapshot-Reconciliation mit Run-ID.~~ **Behoben
  2026-07-14:** Run-ID-basierte, ownership-sichere Snapshot-Reconciliation;
  Details und Testumfang in Roadmap-Punkt 16.
- ~~**P1-06** — Canonical-/Move-API validieren Artist/Recording/Titel/Dauer
  nicht; ein bereits-Canonical-Track kann selbst zum Duplicate gemacht
  werden (Ketten möglich); Move-File bewegt nur die erste Source-Datei und
  kann den Track fälschlich unmonitored setzen, wenn mehrere Files existieren.~~
  **Behoben 2026-07-14:** gemeinsamer Pair-Validator, kettenfreie Canonical-
  Semantik und atomarer Multi-File-Move; Details in Roadmap-Punkt 17.
- ~~**P1-24** — `monitor_new_items='new'` verhält sich identisch zu `'all'`
  (beide auto-monitoren jedes neu entdeckte Release, auch alte
  Backkatalog-Einträge). Lidarr vergleicht für „New" das Release-Datum
  gegen das neueste bestehende Release — das fehlt hier noch.~~ **Behoben
  2026-07-14:** fester Pre-Sync-Datumscutoff mit konservativer Behandlung
  undatierter Daten; Details in Roadmap-Punkt 18.
- ~~**P1-26** — Tracklist-Materialisierung: ist `expected_track_count` zu
  klein, schneidet der Import Provider-Einträge ab und löscht anschließend
  überzählige fileless Rows, ohne deren Monitor-Zustand zu prüfen — kann
  bewusst gewishlistete/monitorte Rows stumm entfernen.~~ **Behoben
  2026-07-14:** Provider-Liste bestimmt die Mindestgröße; positive Monitor-/
  Wanted-Absicht schützt fileless Rows. Details in Roadmap-Punkt 19.
- ~~**P1-28** — „Refresh & Scan" liest nur Audioqualität/Größe neu ein;
  `tags_json`/`missing_tags_json`/`metadata_gaps_json` werden nicht
  aktualisiert, Retag invalidiert diesen Cache ebenfalls nicht — UI zeigt
  nach erfolgreichem Retag weiterhin alte Gaps.~~ **Behoben 2026-07-14:**
  gemeinsamer Tag-Cache-Adapter in Scan und Retag; Details in Roadmap-Punkt 20.
- ~~**P2-02** — Missing Files haben keinen belastbaren Lifecycle: Scan lässt
  fehlende Files bewusst unverändert (Mount kann temporär fehlen), aber ohne
  Root-Health/`missing_since`/Miss-Counter bleiben wirklich gelöschte Dateien
  für immer als „present" markiert. Hängt mit dem `file_state`-Feld aus
  ADR-03 zusammen — das Schema-Feld existiert, ist aber noch nicht mit dem
  Scan verdrahtet.~~ **Behoben 2026-07-14:** Zwei-Scan-Bestätigung unter
  konservativer Root-Health und Recovery; Details in Roadmap-Punkt 21.
- ~~**P2-05** — `core/library2/artwork.py::_resolve_abs` verletzt den
  gemeinsamen Path-Resolver-Vertrag (nutzt Legacy-`resolve_library_file_path`
  statt `resolve_lib2_path`).~~ **Behoben 2026-07-14:** Artwork nutzt den
  verbindlichen lib2-Resolver; Details in Roadmap-Punkt 22.
- ~~**ADR-05-Umsetzung** — physisches Datei-Löschen mit Preview/Journal/
  Root-Safety.~~ **Erledigt 2026-07-14:** getrennte Commands und UI,
  fail-closed Root-/Snapshot-Prüfung, persistentes Operation-/Item-Journal,
  per-File-Lifecycle und Crash-Recovery (Roadmap-Punkt 25). Der bestehende
  Entity-Delete löscht weiterhin ausschließlich DB-Einträge.
- ~~**ADR-06-Rest** — Field-Level-User-Overrides (getrennte Speicherung +
  Read-Projektion pro überschriebenem Provider-Feld).~~ **Erledigt
  2026-07-14** (Roadmap Punkt 4, dritte Slice): getrennte validierte Speicherung,
  zentrale effektive Reads und admin-only Set/Clear-API.

**Weitere P2/P3-UX- und Robustheits-Findings ohne Roadmap-Eintrag** (niedrigere
Priorität, kompakt aufgelistet für spätere Aufnahme):

- ~~P2-01: Scan/Retag halten SQLite-Write-Lock über lange Dateisystem-I/O offen
  (Netzwerk-/Bind-Mounts verschärfen das) — Scope lesen, Connection
  schließen, in kleinen Transaktionen schreiben.~~ **Behoben 2026-07-14:**
  Scan und Retag trennen DB-Snapshots vollständig von Datei-I/O und schreiben
  nur kurze Per-File-Transaktionen (Roadmap-Punkt 24).
- ~~P2-03: Skip-Audit (`lib2_manual_skips`) schreibt weder `file_path` noch
  `profile_id` und wird von keinem Quality-/Repair-Job gelesen — die
  versprochene Wirkung „spätere Jobs respektieren den Override" tritt nicht
  ein.~~ **Behoben 2026-07-14:** zweiphasige Finalpfad-Bindung plus Consumer in
  Upgrade-Auswahl und AcoustID-Repair; Details in Roadmap-Punkt 23.
- ~~P2-04: Artwork-Bytes werden ungeprüft als `.jpg` gespeichert und der
  Response-MIME ist immer `image/jpeg`.~~ **Behoben 2026-07-14:** zentrale
  Validierung/JPEG-Normalisierung plus Self-Healing alter Caches; Details in
  Roadmap-Punkt 26. Cache-Control/Invalidierung waren bereits korrekt. Die
  Artist-vs.-Album-Art-Priorität bleibt als bewusste Produktentscheidung
  getrennt offen (Abschnitt 11.2), nicht als Formatbug.
- ~~P2-06: UI-Fehlerzustände bleiben bei mehreren Mutationen als Dauer-Loading
  oder ganz unsichtbar.~~ **Behoben 2026-07-14:** Alle Library-v2-
  Mutationsgrenzen wurden inventarisiert; zuvor stille Monitor-/Refresh-/
  Profile-/Import-/Grab-/Mirror- und Playlist-Fehler besitzen sichtbare,
  endliche Zustände und einen Retry-Vertrag (Roadmap-Punkt 27).
- ~~P2-07: Artist-Karten verschachteln einen Button (MonitorToggle) in einem
  Button (die Karte selbst) — ungültiges HTML, unzuverlässiges Keyboard-/
  Click-Verhalten.~~ **Behoben 2026-07-14:** Card-Navigation und Monitor-
  Toggle sind semantische Geschwister-Buttons (Roadmap-Punkt 28).
- ~~P2-08: „Search Monitored"/„Search Upgrades" wirken auf Artist-/Album-Ebene
  positioniert, laufen aber global über die ganze Wishlist/Library.~~ **Behoben
  2026-07-14:** Labels, Tooltips und Status benennen den globalen Scope
  ausdrücklich; die bestehenden globalen Pipelines bleiben alleinige
  Ausführung (Roadmap-Punkt 29).
- ~~P2-09: Interactive-Search-Modal nutzt die vorhandene Source-Auswahl
  (`/api/search/sources`) nicht; „Searching all configured sources" stimmt
  nur im `best_quality`-Modus.~~ **Behoben 2026-07-14:** typisierte
  Server-Sourceauswahl plus ehrliches Default-/Selected-Source-Label;
  Ausführung bleibt beim vorhandenen `/api/search`-Orchestrator
  (Roadmap-Punkt 30).
- ~~P2-11: Fehlendes Publish-Datum wird beim Descending-Age-Sort als unendlich
  behandelt und kann vor bekannten Releases einsortiert werden.~~ **Behoben
  2026-07-14:** Unknown-Gruppe bleibt richtungsunabhängig am Ende
  (Roadmap-Punkt 31).
- ~~P2-12: „My Library"-Ansicht zeigt nur monitorierte/library Releases, aber
  „Monitor all" wirkt backendseitig auf den vollen Release-Scope inkl.
  versteckter provider-only Discography — Risiko, unbeabsichtigt den ganzen
  Backkatalog zu monitoren.~~ **Behoben 2026-07-14:** sichtbare Release-ID-
  Allowlist wird fail-closed im bestehenden Bulk-Worker angewendet
  (Roadmap-Punkt 32).
- ~~P2-13: Discography-Sync hat keine Concurrency-/Snapshot-Garantie
  (gleichzeitiger manueller + periodischer Refresh, kein Artist-Sync-Lock).~~
  **Behoben 2026-07-14:** gemeinsame per-DB/per-Artist Refresh→Auto-Monitor-
  Sequenz für API und Repair-Job (Roadmap-Punkt 33).
- ~~P2-15: Tracklist-Fallback ohne Spotify-ID kann bei Deezer die falsche
  Edition wählen (kein Jahr-/Trackcount-/UPC-Abgleich).~~ **Behoben
  2026-07-14:** der bestehende typed Adapter prüft namenbasierte Treffer gegen
  effektive Default-Editionsfakten (Roadmap-Punkt 34).
- ~~P2-16: `quality_eval.py` behandelt fehlende/ungültige Qualität als
  „meets_profile=True" — unterdrückt nötige Scans/Upgrades statt eines
  dritten `unknown`-Zustands.~~ **Behoben 2026-07-14:** Tri-State-Auswertung
  bleibt von Backend über Wishlist-Consumer bis zur UI erhalten
  (Roadmap-Punkt 35).
- ~~P2-17: Albumdetail/Index-Stats skalieren mit N+1-Queries und korrelierten
  Subqueries — bei großen Libraries sichtbar (verwandt mit A5/Roadmap-Punkt
  12).~~ **Behoben 2026-07-14:** gruppierte Stats und gebündelte Resultset-
  Projektionen/Primary-Files/Provenance (Roadmap-Punkt 36).
- ~~P2-18: Fehlende Request-Validierung erzeugt vermeidbare 500er
  (ungeschütztes `int()`, `json.loads` nach Commit, ungeklemmte negative
  Limits).~~ **Behoben 2026-07-14:** Range-/Shape-/ID-Validierung vor Reads,
  Mutationen und Jobstarts (Roadmap-Punkt 37).
- P2-20: Fortschritts-Prozentwerte sind nicht auf 0-100 geklemmt.
- P2-21: Bundle-Completion kann nach Wartefrist auf einen unvollständigen
  `incomplete_path` zurückfallen statt einen finalen Pfad zu verlangen.
- P2-23: Orchestrator und Download-Engine teilen weiterhin
  Download-Verantwortung (Engine macht Suche/Status/Cancel, Orchestrator ruft
  aber weiter direkt `client.download(...)` auf) — Bezug zum bestehenden
  `docs/download-engine-refactor-plan.md`.
- P2-24 (Rest-Risiko): Artist-Credit-Splitting an `&`/`and`/Kommas kann bei
  bisher unbekannten Bandnamen (nicht nur beim M1-Fixfall) weiterhin
  Phantom-Artists erzeugen, wenn der volle Credit-String noch nicht als
  Artist bekannt ist.

---

## 11. Playlists Phase 2 — Quality-Profile-Konfliktauflösung (zurückgestellt, niedrige Priorität)

**Anlass:** Am 2026-07-14 brachte der User ein separat verfasstes externes
Findings-Dokument ein ("Library v2 Playlists & Artwork Strategy — Findings &
Design Decisions"), das von einem noch unbegonnenen Playlists-Feature und
einer ungeklärten Artwork-Strategie ausging, mit der Vermutung, es sei
inzwischen veraltet. Gegen den Code geprüft: **größtenteils richtig
vermutet** — das Dokument war zum Zeitpunkt dieser Prüfung bereits überholt.

### 11.1 Was am externen Dokument veraltet war

- **Playlists (Roadmap-Punkt 14 / Phase E) sind bereits umgesetzt**, entgegen
  der Doku-Annahme "Completely unstarted". Siehe Abschnitt 7, Punkt 14 sowie
  Commits `c7c26688` ("Add Library v2 playlist API boundary") und
  `7d06a0ac` ("Integrate playlists into Library v2"), beide Vorfahren von
  HEAD auf diesem Branch. Library v2 hat einen Playlist-Index + Detail-View
  (Header-Toggle Artists/Playlists), der ausschließlich die **vorhandenen**
  `mirrored-playlists`-Endpoints liest (`GET mirrored-playlists`,
  `GET mirrored-playlists/<id>`, `POST mirrored-playlists/<id>/pipeline/run`)
  — ein reiner Read-/Trigger-Client
  (`webui/src/routes/library-v2/-library-v2.api.ts:479-528`), verifiziert im
  Code: **kein** `lib2_playlists`-Schema, kein zweiter Importer, keine
  zweite Decision-Engine. Mirroring/Quellenauswahl bleibt bewusst auf der
  etablierten Playlists-Seite. Damit sind die Design-Fragen 1.1
  (UI-Platzierung: Header-Toggle, eigene Sektion) und 1.5 (Quellen: was auch
  immer die bestehende Mirrored-Playlist-Pipeline unterstützt) faktisch
  bereits entschieden.
- **P2-05** (Artwork-Path-Resolver nutzte den Legacy-Resolver statt
  `resolve_lib2_path`) ist seit 2026-07-14 behoben — verifiziert:
  `core/library2/artwork.py::_resolve_abs` delegiert jetzt an
  `core.library2.paths.resolve_lib2_path`.

### 11.2 Was am externen Dokument weiterhin zutrifft

- **P2-04 ist seit 2026-07-14 geschlossen** (Roadmap-Punkt 26): rohe
  Provider-/Embedded-Bytes werden validiert und als echtes JPEG normalisiert;
  alte falsch benannte Cachedateien heilen sich selbst. Damit stimmen `.jpg`,
  Bytes und `image/jpeg` überein. Cache-Control und Invalidierung waren bereits
  vorher sauber und blieben unverändert.
- **Artist- vs. Album-Art-Strategie ist tatsächlich uneindeutig**, aber
  anders gelagert als im externen Dokument vermutet: `build_artwork`
  bevorzugt für `kind == "artist"` HEUTE das Embedded-Cover eines beliebigen
  Alben des Artists (Priorität: kein Single, dann neuestes Jahr) und fällt
  erst danach auf ein Provider-Artist-Photo zurück
  (`core/library2/artwork.py:205-221`) — bewusst so gebaut ("fast, local").
  Der externe Vorschlag "Artist-Bild IMMER vom Provider, nie embedded" wäre
  also eine bewusste Verhaltensänderung, kein reiner Bugfix — vor Umsetzung
  explizit mit dem User klären (Trade-off: Konsistenz/Lidarr-Parität vs.
  Provider-Coverage/Rate-Limits für Nischen-Artists ohne
  Fanart-/Deezer-Artist-Photo).

### 11.3 Die eigentliche offene Anforderung: Quality-Profile-Konfliktauflösung

Das externe Dokument stellte 1.3 ("Quality Profile Assignment for
Playlists") als offene Frage mit drei Optionen dar. Der User hat sie in der
anschließenden Voice-Session konkretisiert, und diese Konkretisierung — nicht
das ursprüngliche Dokument — ist der Teil, der tatsächlich noch fehlt und
implementiert werden muss:

Sobald Playlist-Tracks mehr als einen Read-Only-View bekommen und
tatsächlich in die Wishlist gemirrort/monitored werden können, entsteht ein
Prioritätskonflikt, den es bei reinem Artist-/Album-Monitoring so nicht gab:
ein Track kann gleichzeitig über mehrere Pfade "wanted" werden — als Teil
einer Playlist (mit Playlist-Default-Profil), als Teil eines
Artist-Monitorings (mit Artist-Profil), und/oder individuell manuell gesetzt
(Track-spezifisches Profil). Nutzer-Erwartung (Voice-Session
2026-07-14): **das spezifischste Profil gewinnt**, in dieser Reihenfolge:

1. **Track-spezifisch** gesetztes Profil (höchste Priorität) — der User hat
   für genau diesen Song explizit ein Profil gewählt.
2. **Artist-spezifisches** Profil, falls für den Artist bereits eines gesetzt
   ist (`lib2_artists.quality_profile_id`, s.
   `core/library2/profile_lookup.py:58-69`).
3. **Playlist-Default-Profil** (niedrigste Priorität) — greift nur, wenn
   weder 1 noch 2 zutrifft.

Das ist kein neues Konzept, sondern eine Erweiterung des bereits
bestehenden Fallback-Musters: `profile_lookup.default_quality_profile_id`
löst heute schon Artist- vor Global-Default auf; jeder Wishlist-Mirror-Call
trägt bereits ein Per-Item `quality_profile_id` (s. Abschnitt 1,
Designregel "Quality-Profile"; `wishlist_mirror.py`). Für Playlists fehlt
lediglich die dritte, niedrigste Stufe in dieser Kette (Playlist-Default)
UND ein Ort, an dem ein Playlist-Default überhaupt gespeichert wird — es
gibt aktuell **kein** Playlist-Datenmodell in Library v2 (kein
`lib2_playlists`), das ein Default-Profil tragen könnte; Playlist-Daten
kommen bislang rein aus den bestehenden `mirrored_playlists`-Tabellen
(`core/playlists/*`), die keinerlei Quality-Profile-Spalte haben. Verifiziert
per Grep: `core/playlists/pipeline.py` und
`core/playlists/materialize_service.py` haben aktuell keinerlei
Quality-Profile-Bezug.

### 11.4 Weitere offene Punkte aus der Voice-Session

- **UI-Redundanz:** Es existieren bereits mehrere Playlist-Verwaltungsseiten
  (die klassische Playlists-Seite für Mirror/Sync zum Mediaserver, jetzt auch
  der neue Library-v2-Read-View, s. 11.1). Ein Playlist-Default-Profil-Picker
  MUSS klar einer dieser Seiten zugeordnet werden, sonst entsteht doppelte,
  widersprüchliche Konfiguration. Tendenz aus der Session: eher in der
  bestehenden Playlists-Seite ansiedeln (dort wo Mirroring/Sync ohnehin
  konfiguriert wird), NICHT als Duplikat in Library v2 — konsistent mit der
  bereits gewählten Architektur (Library-v2-Playlist-View ist bewusst reiner
  Read-/Trigger-Client, keine eigene Konfigurationsoberfläche).
- **Reorder-Frage (ungeklärt):** Was passiert mit bereits gemirrorten/
  heruntergeladenen Tracks, wenn sich die Track-Reihenfolge der
  Quell-Playlist ändert (z.B. eine Spotify-Playlist wird umsortiert)?
  Aktuell nicht spezifiziert — vermutlich irrelevant für Wishlist-Zwecke
  (Reihenfolge ist kein Wanted-Kriterium), aber potenziell relevant für
  Media-Server-Sync (bestehende Pipeline, außerhalb Library v2). Hier nur
  festgehalten, nicht weiter untersucht.
- **Playlists als First-Class-Entity vs. Read-Through:** Um ein
  Playlist-Default-Profil zu speichern, wird zwingend irgendeine Persistenz
  gebraucht — entweder eine schlanke neue Spalte an der bestehenden
  `mirrored_playlists`-Tabelle (kein neues `lib2_playlists`, näher an der
  Architekturregel "keine Parallelstruktur"/"kein zweiter Importer") oder ein
  minimales `lib2_playlists` (nur `id`, `mirrored_playlist_id`,
  `quality_profile_id`). Beide Varianten sind mit den Nicht-verhandelbaren
  Designregeln (Abschnitt 1) vereinbar; welche gewählt wird, sollte im
  Implementierungs-Chat explizit neu entschieden werden, nicht hier
  vorentschieden.

### 11.5 Priorität — bewusst zurückgestellt

**Das hier ist explizit NICHT der nächste Schritt.** Der User hat in der
Voice-Session ausdrücklich gesagt, dass Playlist-Quality-Profile-Konflikte
kompliziert und nicht ganz intuitiv sind (mehrere existierende
Playlist-Seiten sorgen schon für Verwirrung) und dass zuerst "das Richtige"
fertig werden soll, bevor das hier angegangen wird. Diese Sektion existiert
NUR, damit die Idee nicht verloren geht — nicht als Auftrag, sie als
Nächstes umzusetzen. Der tatsächliche nächste Schritt bleibt wie in
Abschnitt 7 vermerkt: **P2-01** (Scan/Retag SQLite-Lock-Dauer über lange
Dateisystem-I/O).

Falls dieser Punkt später aufgegriffen wird, sind die Voraussetzungen dafür:
1. Persistenzentscheidung für das Playlist-Default-Profil (11.4, letzter
   Punkt).
2. Playlist-Track → Wishlist-Mirror-Pfad um die 3-stufige Profil-Kette
   (Track > Artist > Playlist-Default) erweitern — ansetzend dort, wo der
   Playlist→Wishlist-Mirror-Call real passiert. Aktuell triggert Library v2
   nur die bestehende Pipeline (`run_mirrored_playlist_pipeline`,
   s. `core/playlists/pipeline.py`); dort ist noch kein
   Quality-Profile-Bezug vorhanden (verifiziert per Grep) — die Kette müsste
   dort oder im nachgelagerten Wishlist-Mirror-Schritt ansetzen.
3. UI-Entscheidung, wo der Playlist-Default-Profil-Picker sitzt (11.4,
   erster Punkt).

---

## 12. Offene Backend-Findings & Roadmap-Fortsetzung (2026-07-14)

Basierend auf Nutzer-Feedback und real-world Testlauf, aufzunehmend nach Abschluss von Roadmap-Punkt 37:

### 38. Update Discovery funktioniert nicht konsistent (Discography-Sync Robustheit)

**Beobachtung:** Update Discography funktioniert bei manchen Artists nicht zuverlässig:
- Michael Jackson: findet oft nur Singles, nicht den ganzen Katalog.
- Hirokyu Samono: aliasing-bedingte Duplikation, mehrere Versionen desselben Release unter verschiedenen Aliases.
- Generell: Mehrere Release-Variationen unter verschiedenen Artist-Namen-Kombinationen (feat., &, x usw.) werden nicht konsistent erkannt.

**Vermutete Root Causes:**
- Provider (besonders Deezer) liefert Provider-Artist-IDs inkonsistent oder mit Alias-Varianten.
- Normalisierter Discography-Match (§3 / Abschnitt 3 "Discography") nutzt Provider-ID-Matching; fehlende/alternative IDs führen zu „nicht gefunden".
- Multi-Artist-Splits im Importer (`feat./&/x` Parsing) laufen nicht konsistent über alle Provider-Quellen.
- Tracklist-Fallback-Suche (Deezer ohne ID) bei Namensvarianten nicht robust.

**Scope:** Backend-Audit von `core/library2/discography.py` + `core/metadata/discography.py` + Provider-Adapter-Boundary, möglicherweise neue Robust-Heuristik für Alias-Matching.

**Nächster Schritt:** Artist-Watchlist-Sync-Flows in Docker mit gezielten Testkandidaten verifizieren (Michael Jackson, Hirokyu Samono).

---

### 39. Managed Tracks funktioniert nicht (UI sagt „No Duplicates found", aber Duplikate existieren)

**Beobachtung:** Managed Tracks Modal zeigt bei Artists mit echten Single↔Album-Duplikaten falsch „No Single Album Duplicates found for the artist".

**Vermutete Root Causes:**
- Query `GET /api/library/v2/artists/<id>/duplicates` filtert zu streng oder hat SQL-Bug.
- Canonical-Link-Logik (`lib2_tracks.canonical_track_id`) wird nicht konsistent gespeichert beim Import.
- Duplikat-Erkennung läuft nur wenn beide Varianten mindestens eine Datei haben; fileless Wanted-Rows werden übersehen.

**Scope:** Backend-Audit von `core/library2/manage.py` + `api/library_v2.py:GET /artists/<id>/duplicates` Query.

**Nächster Schritt:** Manually created Single↔Album-Duplikate in Test-DB einfügen und Query-Korrektheit prüfen.

---

### 40. Artist Aliasing & Matching braucht Überarbeitung (Multi-Alias Artists)

**Beobachtung:** Artists mit mehreren Aliases (z.B. Hirokyu Samono mit alternativen Romanisierungen) führen zu:
- Artist-Watchlist-Seite zeigt dieselben Releases unter verschiedenen Artists mehrfach.
- „Search/Update Discovery" findet nicht alle Releases, weil sie unter unterschiedlichen Aliases veröffentlicht wurden.
- Importer erstellt Duplikat-Artists statt sie zu mergen.

**Vermutete Root Causes:**
- `core/metadata/discography.py` sucht nur nach Provider-IDs (Spotify, Deezer), nicht nach Namen-Normaliserungen.
- Alias-Matching existiert nur im `_multi_artist_split` (Collaborator-Feature-Feauture, nicht Artist-Alias).
- Keine zentrale Artist-Deduplication auf Basis von Aliases/Name-Normaliserung.

**Scope:** Alias-Registry (wie in Musikdatenbanken MB/Discogs), oder zumindest eine Fallback-Heuristik für Name-Normalisierung vor Duplicate-Create.

**Nächster Schritt:** Audit wie andere Systeme (Lidarr, Music Brainz) Artist-Aliases handhaben; dann Design entwerfen (zentrales `lib2_artist_aliases`-Schema vs. Provider-Snapshot-Basis).

---

### 41. Manual Artist Matching UI (Fehler-Recovery wie Plex)

**Beobachtung:** Wenn eine Discography-Suche fehlschlägt oder Aliases falsch matched werden, gibt es keinen Weg, manuell zu sagen „dieser Download gehört zu Artist X, nicht Y".

**Gewünschte Funktionalität:** Ähnlich Plex/Jellyfin — wo ein Upload/Match-Fehler erkannt wird, kann der Nutzer interaktiv sagen „das ist Artist X" oder „das Release ist falsch, sollte Artist Y sein" und die DB-Verknüpfung korrigieren.

**Scope:** Neue UI-Modal + Endpoint `POST /api/library/v2/artists/<id>/manual-alias-link`, der:
- Eine existierende Artist-Row mit einer anderen Artist-Row verknüpft (als Alias oder Redirect).
- Alle Tracks/Releases dieser Seite per `canonical` oder `merge` zu einer Identität konsolidiert.
- Die bestehende Wishlist/Monitor-Regeln beibehalten.

**Nächster Schritt:** Anforderungs-Design, Datenschema (Alias-Tabelle?), Testing-Strategie.

---

### 42. Preview Retag zeigt falsche „File not found" Fehler

**Beobachtung:** „Preview Retag" zeigt für heruntergeladene Tracks „No File" oder „File not found on disk", obwohl die Datei vorhanden ist.

**Vermutete Root Causes:**
- `core/library2/paths.resolve_lib2_path` wird nicht konsistent aufgerufen; älterer `os.path.exists` wird noch irgendwo verwendet.
- Gespeicherter Pfad in `lib2_track_files.file_path` ist Media-Server-Sicht (z.B. mit Mapping), wird aber gegen den lokalen Filesystem-Pfad geprüft.
- Relative Pfade werden nicht korekt aufgelöst.

**Scope:** Audit aller Pfad-Zugriffe in `core/library2/retag.py` + Preview-Route.

**Nächster Schritt:** Manuell einen Track downloaden, in DB seinen `file_path` inspizieren, Preview Retag öffnen und Fehler-Root-Cause identifizieren.

---

### 43. ReplayGain-Funktion fehlt

**Beobachtung:** Die alte Library bot die Möglichkeit, ReplayGain-Werte zu berechnen und zu Album/Track zu schreiben. Library v2 hat das nicht.

**Scope:** Neue Library-v2-Action (Artist- oder Track-Scope):
- `POST /api/library/v2/artists/<id>/calculate-replay-gain` oder
- `POST /api/library/v2/albums/<id>/calculate-replay-gain`
- Nutzt bestehende ReplayGain-Berechnung (falls vorhanden in `core/imports/*` oder `core/tag_writer.py`), schreibt in Datei-Tags, persistiert in DB.

**Nächster Schritt:** Grep für bestehende ReplayGain-Logik; falls nicht vorhanden, prüfen ob externe Bibliothek (z.B. `python-acoustid`, `librosa`) integrierbar ist.

---

### 44. Enrich Album/Track-Funktion fehlt

**Beobachtung:** Die alte Library bot Enrich: gezielt zusätzliche Metadaten für ein Album/Track abfragen und einzufügen (z.B. Year, Genre, Labels).

**Scope:** Neue Library-v2-Action:
- `POST /api/library/v2/albums/<id>/enrich` — hole aktuelle Metadaten vom bestehenden Provider, update `lib2_albums` + `lib2_tracks`.
- UI: Modal mit durchsuchbaren Quellen (Spotify, Deezer, MB) + Diff-Preview.

**Nächster Schritt:** Audit bestehender Enrichment-Logik (wahrscheinlich im Import-Flow), Scope für Library-v2-Only-Action definieren.

---

## Nächste Schritte (Priorisierung)

1. **38 (Discography):** Kritisch — blockiert Update Discovery. Zuerst Docker-Verifizierung mit Testkandidaten, dann Root-Cause-Audit.
2. **39 (Managed Tracks):** Kritisch — Kernfeature funktioniert nicht. Query-Audit + Testfall.
3. **40 (Aliasing):** Mittelhoch — Design vor Umsetzung. Vielleicht mit 38 kombiniert addressierbar.
4. **41 (Manual Matching UI):** Mittelhoch — nützlich für 40-Recovery, aber größerer Umfang.
5. **42 (Preview Retag):** Niedrig-Mittelhoch — UX-Bug, aber Pfad-Resolver sollte bereits existieren (wurde in 2026-07-07 behoben); wahrscheinlich kleines Regressions-Loch.
6. **43 (ReplayGain):** Niedrig — schön-zu-haben, nicht kritisch.
7. **44 (Enrich):** Niedrig — schön-zu-haben, nicht kritisch.

**Neben diesen Backend-Punkten:** UI-Improvements aus `docs/library-v2-ui-requirements.md` können parallel laufen (Icons, Labels, Layout).

---

**Session 2026-07-14 Abschluss:** Branch bleibt clean, alle Tests bestanden bis Punkt 37. Die Punkte 38–44 sind als separate Aufgaben zu verstehen, die in zukünftigen Sessions aufgegriffen werden.

---

## 13. UI-Improvements Session (2026-07-14, späte Session)

**Dokumentation:** `docs/library-v2-ui-requirements.md` konsolidiert alle Nutzer-Anforderungen zur UI-Verbesserung (Icons, Labels, visuelle Hierarchie).

**Umgesetzt in dieser Session (Klein-Commits):**

1. **Quality Display Refactor** (`f170dc65`):
   - Neue `QualityDisplay`-Komponente statt `detailedQualityText()` String
   - Format, Resolution (Bit-Depth/Sample-Rate), Bitrate als separate visuelle Blöcke
   - CSS Styles für `.qualityDisplay`, `.qualityBlock`, `.qualityMissing`
   - Bessere Lesbarkeit ohne Schrägstrich-Noise
   - Frontend: 121 Tests, TypeCheck sauber, Build erfolgreich

2. **Search Action Labels & Icons (Lidarr-Alignment)** (`07f87a61`):
   - "Search All Monitored (global)" → "Search (global)" (icon: automatic/Lupe)
   - "Search All Upgrades (global)" → "Search Upgrades (global)"
   - "Interactive Search" unverändert (icon: interactive/Mensch-Silhouette)
   - Improved Tooltips für semantische Klarheit
   - Frontend: 121 Tests, TypeCheck sauber, Build erfolgreich

**Nicht umgesetzt (braucht Backend/API-Änderung):**

- **Quality Profile vor Expand zeigen:** `LibraryV2AlbumSummary` hat nur `quality_profile_id`, nicht den vollen Profil-Namen. Entweder API-Erweiterung (Summary-View mit Profil-Objekt) oder UI-seitige ID→Name-Auflösung. Priorisiert für nächste Session.
- **Managed Tracks Fixes:** Backend-Query-Bug (Roadmap 39)
- **Update Discovery Robustheit:** Backend-Audit (Roadmap 38)
- **Artist Aliasing:** Design + Implementation (Roadmap 40–41)

**Nächste Session:** Entweder Quality-Profile-Frontend-Lookups (schnell), oder eine der Backend-Roadmap-Punkte (38/39 Priorität: Critical).
