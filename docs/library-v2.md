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
> überholt waren); am 2026-07-16 Abschnitt 31 ergänzt (Deep-Dive Runde 4:
> B5 konfigurierbare Spalten/Match-Provider, B6 Sort/Mehrfachauswahl/
> Bulk-Leiste an der Track-Tabelle); am 2026-07-17 Abschnitt 40 ergänzt
> (P2-25/D4: echter Import-Fortschritt, unbegrenztes Reattach-Polling und
> Query-Invalidierung statt Full-Page-Reload); am 2026-07-17 Abschnitt 41
> ergänzt (P2-23: finaler Download-Dispatch in die bestehende Engine gezogen;
> P2-24: unbekannte mehrdeutige Band-Credits erzeugen keine Phantom-Artists
> mehr); am 2026-07-17 ergänzt um Abschnitt 52 (verbindliches Nutzer-Review
> zu Quality-Profile-Priorität, konsolidiertem Monitoring/Artist Settings,
> Search→Library-v2-Materialisierung, Pipeline-History und Delete-UX); am
> 2026-07-17 Abschnitt 53 ergänzt (§52.2 Profilherkunft für Track/Album/Artist/
> Global, §52.5 Artist-Providerfoto zuerst, §52.6 direkte Track-Suche trotz
> unmonitored und §52.10 Edit-Icon/Herkunftsanzeige umgesetzt); am 2026-07-17
> Abschnitt 54 ergänzt (§52.3/§52.4 gemeinsame Artist-/Watchlist-Settings und
> §52.11 einheitlicher DB-only/permanenter Datei-Entfernungsflow umgesetzt).
> Am 2026-07-17 wurde außerdem die Materialisierungsentscheidung aus §52.12.2
> präzisiert: Jeder bestätigte Wishlist-/Acquisition-Intent materialisiert
> lib2 sofort, unabhängig davon, ob er aus Search, Playlist-Sync, Watchlist-
> Scanner oder einem anderen Eingangspfad stammt.
> Die Bulk-Monitoring-UI invalidiert nach abgeschlossenen Jobs ebenfalls
> den Library-Query-Cache; explizite Track-Entscheidungen werden dadurch
> unmittelbar und ohne stale Anzeige sichtbar.

Opt-in, Lidarr-style Library-Manager auf SoulSyncs eigener
Such-/Download-/Processing-/Tagging-Pipeline. Gated hinter
`features.library_v2`; die Legacy-Library bleibt unangetastet.
Code: `core/library2/`, `api/library_v2.py`, `webui/src/routes/library-v2/`,
Tests: `tests/library2/`.

---

## 1. Nicht verhandelbare Designregeln (Core principles — do not break)

- **NIE Media-Server-abhängig** (Plex/Jellyfin/Navidrome) — auch nicht für
  Artwork. Album-/Track-Artwork = Embedded-Cover aus der Datei (primär) →
  Metadata-Provider (Fallback); Artist-Artwork = Provider-Artist-Foto (primär)
  → Embedded-Albumcover (Fallback; Nutzerentscheidung §52.5) → Disk-Cache
  `<db_dir>/lib2_artwork/`, serviert via
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

Milestone 1 bis Phase E, breites Metadaten-Edit und die serverseitliche
Acquisition-/Eligibility-Architektur (Phase 4/5, „LIB2-011") sind umgesetzt
und über die in den jeweiligen Roadmap-Punkten festgehaltenen Gates
verifiziert. Das Nutzer-Review in §52 legt den nächsten Produkt-Scope fest:
Quality-Profile brauchen eine nachvollziehbare Track→Album→Artist→Playlist-
Priorität, Monitoring/Artist Settings müssen die bestehende Watchlist
wiederverwenden, Search muss Einträge vor dem Download in Library v2
materialisieren, und fehlgeschlagene bzw. quarantänisierte Pipeline-Schritte
müssen in der Entity-History sichtbar bleiben. Kalender und Artist Top Tracks
sind ausdrücklich kein Ziel; weitere Legacy-/Lidarr-Gaps bleiben reine
Enumeration, solange sie nicht einzeln angenommen wurden.
<!-- Veralteter Satzrest des vorherigen Statusabschnitts:
+ Manage Tracks + Delete, aber kein breites Metadaten-Edit); die serverseitige
-->

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
- ~~P2-20: Fortschritts-Prozentwerte sind nicht auf 0-100 geklemmt.~~
  **Behoben 2026-07-14:** Der eine gemeinsame Schreibpfad
  `core/automation/progress.py::update_progress` (genutzt von Watchlist-
  Auto-Scan, Discovery-Sync/Playlist-Pipeline und Wishlist-Processing,
  inkl. der von Library v2 angezeigten Playlist-Pipeline) klemmt den
  `progress`-Kwarg jetzt zentral auf 0-100 (mit Rundung), statt dass jeder
  der über 20 Call-Sites einzeln validiert. Im Frontend nutzen `pipelineLabel`
  und der Album-Track-Completion-Balken (`library-v2-page.tsx`) denselben neu
  extrahierten `clampPercent`-Helper statt zweier bisher abweichender
  Ad-hoc-Berechnungen (eine ungeklemmt, eine nur teilweise geklemmt — echte
  UI-Inkonsistenz bei genau demselben `state.progress`-Wert). 4 gezielte
  Python-Tests (`tests/automation/test_automation_progress.py`) und 6
  gezielte Vitests (`progress-clamp.test.ts`) pinnen Über-/Unterlauf,
  Rundung und In-Range-Passthrough. Bewusst nicht angefasst: die Legacy-
  Reorganize-Queue-Prozentanzeige in `webui/static/library.js` (außerhalb
  des Library-v2-Scopes). **Nächster logischer Schritt:** P2-21 —
  Bundle-Completion darf nach Wartefrist nicht auf einen unvollständigen
  `incomplete_path` zurückfallen.
- ~~P2-21: Bundle-Completion kann nach Wartefrist auf einen unvollständigen
  `incomplete_path` zurückfallen statt einen finalen Pfad zu verlangen.~~
  **Behoben 2026-07-14:** Der geteilte Poll-Loop
  (`core/download_plugins/album_bundle.py::poll_album_download`, genutzt
  von Torrent- UND Usenet-Bundle-Downloads) sowie der duplizierte
  Per-Track-Loop in `usenet.py::_download_thread` akzeptieren
  `incomplete_path` nicht mehr allein deshalb, weil das
  Completed-no-path-Zeitfenster abgelaufen ist — der Client kann in
  genau diesem Moment noch in dieses Verzeichnis schreiben (Unpack/
  Repair/Move). Ein neuer, injizierbarer
  `snapshot_incomplete_path`-Fingerprint (Größe/Dateianzahl/mtime) muss
  auf zwei aufeinanderfolgenden Polls identisch sein, bevor der Pfad als
  final akzeptiert wird; bis dahin wird weitergepollt, begrenzt vom
  äußeren Poll-Timeout (6h Default) statt vom kurzen Fenster — läuft die
  Stabilisierung nie ein, endet der Download in einem lauten Timeout-
  Fehler statt eines stillen Imports aus einem eventuell
  unvollständigen/korrupten Verzeichnis. Die #721-Geduldsregel (SAB
  braucht bis zu 2 Minuten für `storage`) bleibt unverändert erhalten.
  9 gezielte Poll-/Snapshot-Tests (`tests/test_album_bundle.py`,
  `tests/test_torrent_usenet_plugins.py`) sowie der volle
  Usenet/Torrent/Bundle/Acquisition-Testausschnitt (604 passed) und Ruff
  sind grün. **Damals nächster logischer Schritt:** P2-23 — inzwischen
  geschlossen (siehe direkt folgenden Punkt und Abschnitt 41).
- ~~P2-23: Orchestrator und Download-Engine teilen weiterhin
  Download-Verantwortung (Engine macht Suche/Status/Cancel, Orchestrator ruft
  aber weiter direkt `client.download(...)` auf).~~ **Behoben 2026-07-17:**
  Der finale Source-/Alias-/Soulseek-Peer-Dispatch liegt jetzt in
  `DownloadEngine.dispatch_download`; der Orchestrator ist auch für diesen
  Pfad nur noch Fassade. Nicht initialisierte, aber registrierte Sources
  bleiben im Engine-Katalog und schlagen sichtbar fehl, statt fälschlich als
  Soulseek-Peer interpretiert zu werden. Details in Abschnitt 41.
- ~~P2-24 (Rest-Risiko): Artist-Credit-Splitting an `&`/`and`/Kommas kann bei
  bisher unbekannten Bandnamen (nicht nur beim M1-Fixfall) weiterhin
  Phantom-Artists erzeugen, wenn der volle Credit-String noch nicht als
  Artist bekannt ist.~~ **Behoben 2026-07-17:** Mehrdeutige providerlose
  Credits bleiben verlustlos als Gesamtname erhalten; nur explizite
  Kollaborationsmarker bzw. bereits belegte Einzelidentitäten werden
  aufgeteilt. Details in Abschnitt 41.
- ~~P2-25 (gefunden via PR #1025, Nezreka, 320k-Track-Library): Import zeigt
  keinen echten Live-Fortschritt an. Backend loggt granularen Fortschritt
  (`import_legacy_library` alle 200 Rows in `core/library2/importer.py:474,
  555,665`, danach `precache_tracklists` alle 20 Alben und
  `precache_all_artwork` alle 25 Items) und exponiert ihn über
  `GET /api/library/v2/import/status` (`api/library_v2.py:2657-2662`) — das
  Frontend (`ImportButton`, `webui/.../library-v2-page.tsx:2907-2938`) liest
  `stage`/`current`/`total` aber nie aus und zeigt nur ein statisches
  "Importing…". Zusätzlich pollt `waitForLibraryV2Import`
  (`library-v2-page.tsx:2895-2905`) nur 10 Minuten und wirft danach einen
  Timeout, obwohl der Import als Daemon-Thread im Backend unbeeinflusst
  weiterläuft. Ursache für die lange Laufzeit selbst: nach dem reinen
  DB-Insert (schnell, batched) läuft synchron ein Enrichment-Schritt, der pro
  Album sequenzielle Live-Requests an Spotify/Deezer schickt
  (`provider_adapters.py:294-330`, `artwork.py:264-271`) — bei 320k Tracks
  potenziell zehntausende Calls. **Fix-Richtung:** echten Fortschritt im UI
  anzeigen (Backend liefert die Daten bereits) und den 10-Minuten-Timeout im
  Frontend entfernen/erhöhen, da er keine reale Fehlerbedingung abbildet.~~
  **Behoben 2026-07-17:** Der Importstatus ist eine geteilte React-Query, die
  beim Laden an einen bereits laufenden Backend-Import anknüpft und nur
  solange pollt, wie `running=true` ist — ohne künstliches Zeitlimit. Stufe,
  Zähler und geklemmter Prozentwert werden live mit Progress-Bar angezeigt;
  nach Abschluss werden die Library-v2-Queries invalidiert statt die ganze
  Seite neu zu laden. Backendseitig melden Artist-/Album-/Track-Import sowie
  Tracklist-, Tag- und Artwork-Precache jetzt auch für kleine Libraries
  garantiert Start und Abschluss; Tracklist-Cached-/Provider-Phasen bilden
  einen monotonen Gesamtzähler, und Artwork reicht seinen zuvor ungenutzten
  Progress-Callback bis zum Statusendpoint durch. Details und Verifikation in
  Abschnitt 40.

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
- **Artist- vs. Album-Art-Strategie war tatsächlich uneindeutig**, aber
  anders gelagert als im externen Dokument vermutet: `build_artwork`
  bevorzugt für `kind == "artist"` HEUTE das Embedded-Cover eines beliebigen
  Albums des Artists (Priorität: kein Single, dann neuestes Jahr) und fällt
  erst danach auf ein Provider-Artist-Photo zurück
  (`core/library2/artwork.py:205-221`) — bewusst so gebaut ("fast, local").
  Das Nutzer-Review vom 2026-07-17 entscheidet diese Frage nun anders:
  **Provider-Artist-Foto zuerst, Embedded-Albumcover nur als Fallback** (siehe
  §52.5). Das ist eine noch umzusetzende Verhaltensänderung, kein bereits
  geschlossener Bugfix.

### 11.3 Die eigentliche offene Anforderung: Quality-Profile-Konfliktauflösung

Das externe Dokument stellte 1.3 ("Quality Profile Assignment for
Playlists") als offene Frage mit drei Optionen dar. Der User hat sie in der
anschließenden Voice-Sessions konkretisiert. Die jüngste, verbindliche Fassung
steht in §52.2; sie ersetzt die frühere dreistufige Fassung dieses Abschnitts.

Sobald Playlist-Tracks mehr als einen Read-Only-View bekommen und
tatsächlich in die Wishlist gemirrort/monitored werden können, entsteht ein
Prioritätskonflikt, den es bei reinem Artist-/Album-Monitoring so nicht gab:
ein Track kann gleichzeitig über mehrere Pfade "wanted" werden — als Teil
einer Playlist (mit Playlist-Default-Profil), als Teil eines
Artist-Monitorings (mit Artist-Profil), über ein Release/Album und/oder
individuell manuell gesetzt (Track-spezifisches Profil). Nutzer-Erwartung
(zuletzt bestätigt am 2026-07-17): **das spezifischste explizite Profil
gewinnt**, in dieser Reihenfolge:

1. **Track-spezifisch** gesetztes Profil (höchste Priorität) — der User hat
   für genau diesen Song explizit ein Profil gewählt.
2. **Album-/Release-spezifisches** Profil — gilt nur für Tracks dieses
   Releases und überschreibt keinen expliziten Track-Wert.
3. **Artist-spezifisches** Profil, falls für den Artist bereits eines gesetzt
   ist (`lib2_artists.quality_profile_id`, s.
   `core/library2/profile_lookup.py:58-69`).
4. **Playlist-Default-Profil** — greift nur, wenn keine der spezifischeren
   Ebenen 1–3 einen expliziten Wert liefert.
5. **App-weites Default-Profil** als rein technischer letzter Fallback.

Das ist eine Erweiterung des bestehenden Fallback-Musters, aber **nicht**
durch blindes Kopieren derselben Profil-ID auf alle Kinder lösbar:
`profile_lookup.default_quality_profile_id` löst heute Artist vor Global-
Default auf; jeder Wishlist-Mirror-Call trägt bereits ein Per-Item
`quality_profile_id` (s. Abschnitt 1, Designregel "Quality-Profile";
`wishlist_mirror.py`). Der heutige Library-v2-Cascade schreibt hingegen die
Profil-ID direkt in Album-/Track-Zeilen und verliert damit, ob ein Wert dort
explizit gewählt oder nur geerbt wurde. Der gewünschte Vorrang ist deshalb
erst belastbar, wenn Herkunft/Explizitheit gespeichert oder aus getrennten
Ebenen zur Laufzeit aufgelöst wird. Zusätzlich braucht das bestehende
Playlist-Modell einen Ort für sein Default-Profil; eine parallele
`lib2_playlists`-Welt soll dafür nicht entstehen (Details §52.2).

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
- **Playlist-Profil im bestehenden Sync-Modell:** Um ein Playlist-Default-
  Profil zu speichern, wird Persistenz im bestehenden Mirrored-Playlist-/
  Sync-Modell gebraucht, bevorzugt als additive Spalte an
  `mirrored_playlists` bzw. dessen bestehender Settings-Struktur. Ein neues
  `lib2_playlists` nur für diese Einstellung ist nach dem Nutzer-Review vom
  2026-07-17 ausdrücklich nicht gewünscht. Der Picker gehört dorthin, wo die
  jeweilige Spotify-/Provider-Playlist im bestehenden Sync-/Mirroring-Flow
  konfiguriert wird; Library v2 soll diesen Wert lesen und anwenden, nicht eine
  zweite Einstellung führen. Der User berichtet, dass in diesem Flow bereits
  ein Quality-Profile-Picker sichtbar ist; der Code-Audit fand im
  Playlist-Core noch keinen eindeutigen persistierten Quality-Profile-Bezug.
  Vor der Migration ist deshalb zu prüfen, ob der sichtbare Picker bereits
  anderswo gespeichert, nur teilweise verdrahtet oder noch rein UI-seitig ist.

### 11.5 Voraussetzungen — durch §52 wieder aktiv, noch nicht umgesetzt

Die frühere Zurückstellung ist mit dem Nutzer-Review vom 2026-07-17
aufgehoben. Die Profilpriorität ist Teil des angenommenen Scopes, soll aber
zusammen mit der Monitoring-/Search-Konsolidierung aus §52 umgesetzt werden,
nicht als isolierter Playlist-Sonderweg.

Voraussetzungen dafür:

1. Persistenzentscheidung im **bestehenden** Playlist-/Sync-Modell (11.4),
   inklusive Migration und API-Vertrag für den dortigen Picker.
2. Explizite Profilherkunft bzw. ein Resolver, der geerbte Werte nicht als
   explizite Track-/Album-Werte materialisiert.
3. Playlist-Track → Wishlist-Mirror-Pfad um die vollständige Profil-Kette
   (Track > Album > Artist > Playlist > Global) erweitern — ansetzend dort,
   wo der Playlist→Wishlist-Mirror-Call real passiert. Aktuell triggert
   Library v2 nur die bestehende Pipeline (`run_mirrored_playlist_pipeline`,
   s. `core/playlists/pipeline.py`); dort ist noch kein
   Quality-Profile-Bezug vorhanden (verifiziert per Grep) — die Kette müsste
   dort oder im nachgelagerten Wishlist-Mirror-Schritt ansetzen.
4. Gleichrangige Konflikte definieren: derselbe Track kann in mehreren
   Playlists mit unterschiedlichen Default-Profilen vorkommen. §52.2 lässt
   diese Entscheidung bewusst offen, statt willkürlich "zuletzt gewinnt" zu
   implementieren.

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

**✅ Zwei Root-Causes gefunden + gefixt (2026-07-15, gegen die REALE DB des Nutzers verifiziert, `.venv/bin/python` + Read-only-Kopie):** Michael Jackson (lib2 id=21) hatte `external_ids='{}'`, obwohl die Legacy-`artists`-Zeile `deezer_id='259'` (Deezer = SoulSyncs Default-Quelle!) und `musicbrainz_id` trägt. `expand_artist_discography` (`discography.py:229–236`) baut seine `source_artist_ids` NUR aus `external_ids` + `spotify_id` → leer → kein Provider-ID → Katalog-Fetch fällt auf Namenssuche zurück und findet fast nichts („nur Singles"). Ursachen:
  - **(1) Falsche Spalten-Namen im Importer.** `upsert_legacy`/Album-Import lasen `deezer_artist_id`/`tidal_artist_id`/`qobuz_artist_id` bzw. `deezer_album_id`/… — die REALE Legacy-Schema-Spalte heißt aber `deezer_id`/`tidal_id`/`qobuz_id` (nur Spotify/MusicBrainz tragen das `*_artist_id`/`*_release_id`-Suffix). `_pick` schluckt die fehlende Spalte still → `None` → `external_ids` blieb `{}`. Die alten Tests kodierten dieselbe falsche Annahme (`deezer_artist_id`), deshalb blieb der Bug unentdeckt. Fix: `_pick(row, "deezer_artist_id", "deezer_id")` (beide Namen akzeptiert, alte Tests bleiben grün, reale Schemata funktionieren). Neue Regressionstests mit den ECHTEN Spaltennamen für Artist + Album.
  - **(2) Legacy-ID-Typ-Mismatch → Duplikat-Artist bei Re-Import.** Die Legacy-`artists.id` ist bei soulsync/Deezer-generierten Artists TEXT (`'476516869'`), die lib2-Spalte `legacy_artist_id` ist INTEGER-Affinität. `_ArtistResolver._by_legacy` wurde beim Re-Seed mit dem INT-Schlüssel befüllt, aber mit der TEXT-Legacy-ID nachgeschlagen → str-vs-int-Miss → **jeder Re-Import legte eine Duplikat-Artist-Row an, verwaiste das Original** (`legacy_artist_id→NULL`, `external_ids` blieb leer) und übersprang via `get_legacy`-Miss die Alben des Artists. Das erklärt zugleich die in der Beobachtung genannte „aliasing-bedingte Duplikation". Fix: neuer `_legacy_key()`-Helper coerct beide Seiten auf `str`. Regressionstest mit TEXT-`artists.id`, zweimaliger Import → genau EINE Row, external_ids befüllt.
  - **(3) Nachtrag (Nutzer-Feedback): derselbe ID-Typ-Mismatch dupliziert auch Alben/EPs/Singles bei Re-Import.** Nach Fix (2) wurde der Artist nicht mehr dupliziert, aber jedes Album/EP/Single erschien nach jedem Re-Import doppelt (z.B. „Thriller 40" zweimal unter Michael Jackson). Ursache identisch: `album_map` und `track_map` wurden mit dem INTEGER-`legacy_album_id`/`legacy_track_id` befüllt, aber mit der TEXT-Legacy-`albums.id`/`tracks.id` nachgeschlagen → 100%-Miss → der Re-Import matchte NIE eine bestehende Release, legte eine frische Library-Row an (ihre lib2-id wandert 1→2), und `_reconcile_legacy_snapshot` **löste die verwaiste Original-Row in einen `origin='discography'`-Zwilling ab**, sobald sie eine Provider-Identität hatte (sichtbarer Duplikat-Zwilling), sonst löschte es sie. Fix: `_legacy_key()` auf Modulebene gehoben und an ALLEN `album_map`/`track_map`-Seed- und Lookup-Stellen angewandt. Regressionstest `test_reimport_keeps_stable_album_and_track_ids_with_text_legacy_ids` prüft die eigentliche Invariante (Idempotenz): nach zwei Importen bleibt die lib2-id von Album UND Track stabil und es entsteht kein `discography`-Zwilling. Verifiziert gegen eine Read-only-Kopie der Live-DB: reset→reimport bleibt bei 4 Alben (vorher 4→7, drei Zwillinge für MJ/Justin Bieber/VØJ-Releases mit Provider-id).
  - **Verifikation:** Re-Import gegen eine Read-only-Kopie der Live-DB → MJ (id=21) heilt IN-PLACE (kein Duplikat-Artist UND kein Duplikat-Album), `external_ids={"deezer":"259","musicbrainz":"f27ec8db…"}`, attribuierte Alben 2→3, Gesamt-Alben 4→4 (statt 4→7). `pytest tests/library2` 399 grün, `tests/imports` 676 grün. **Offen (braucht die reale DB + Live-Provider-API):** der eigentliche Katalog-FETCH beim „Update Discography" (Netzwerk) sowie der Hirokyu-Samono-Alias-Fall (Artist nicht in der aktuellen Dev-DB vorhanden). Die Datenschicht-Root-Causes, die den Fetch überhaupt erst blockierten UND die Re-Import-Duplikation verursachten, sind geschlossen.

**No-Docker-Verifikation (wichtig, gilt für alle Importer-/lib2-Bugs):** Die echte Library-DB des Nutzers liegt lokal unter `database/music_library.db` (config `database.path`) und enthält sowohl die Legacy-Tabellen (`artists`/`albums`/`tracks`) als auch alle `lib2_*`-Tabellen. Man braucht KEIN Docker und keinen laufenden Server, um Importer-/lib2-Bugs zu root-causen: (a) read-only inspizieren via `sqlite3.connect("file:database/music_library.db?mode=ro", uri=True)` + `PRAGMA query_only=1`; (b) einen Fix verifizieren, indem man mit der Backup-API einen konsistenten Snapshot auf eine Kopie zieht (`src.backup(dst)`) und den echten Importer gegen die KOPIE laufen lässt (ein winziger Shim mit `_get_connection()` erfüllt den `MusicDatabase`-Kontrakt). So wurden §38 UND dieser Album-Nachtrag verifiziert, ohne die Live-DB zu berühren. Caveat: die aktuelle Dev-DB ist eine kleine/zurückgesetzte Library (~4 Artists) — Artists, die nicht drin sind (z.B. Hirokyu Samono), lassen sich so nicht reproduzieren.

---

### 39. Managed Tracks funktioniert nicht (UI sagt „No Duplicates found", aber Duplikate existieren)

**Beobachtung:** Managed Tracks Modal zeigt bei Artists mit echten Single↔Album-Duplikaten falsch „No Single Album Duplicates found for the artist".

**Vermutete Root Causes:**
- Query `GET /api/library/v2/artists/<id>/duplicates` filtert zu streng oder hat SQL-Bug.
- Canonical-Link-Logik (`lib2_tracks.canonical_track_id`) wird nicht konsistent gespeichert beim Import.
- Duplikat-Erkennung läuft nur wenn beide Varianten mindestens eine Datei haben; fileless Wanted-Rows werden übersehen.

**Scope:** Backend-Audit von `core/library2/manage.py` + `api/library_v2.py:GET /artists/<id>/duplicates` Query.

**Nächster Schritt:** Manually created Single↔Album-Duplikate in Test-DB einfügen und Query-Korrektheit prüfen.

**✅ Root-Cause-Slice umgesetzt (2026-07-15):** Der Endpoint-SQL und die Query waren korrekt — sie hatten schlicht nichts anzuzeigen, weil der Importer den `canonical_track_id`-Link gar nicht erst setzte. Ursache liegt in `link_single_album_duplicates` (`core/library2/importer.py`): die Duplikat-Gruppierung schlüsselte auf `normalize_name(title)`, das `(feat. …)`-Annotationen NICHT entfernt. Genau daran unterscheiden sich echte Single↔Album-Paare in der Praxis am häufigsten — der Album-Cut nennt die Gäste im Titel (`One Dance (feat. Wizkid & Kyla)`), die Single-Version nicht (`One Dance`) → verschiedene Gruppen → kein Link → Modal zeigt fälschlich „No duplicates found". Reproduziert (throwaway-Script, 4 Fälle) und mit Regressionstests fixiert. Fix: neue reine Funktion `dedup_title_key(title)` strippt Featured-Artist-Annotationen (parenthesiert `(feat./ft./with …)` via bestehendem `_FEAT_IN_TITLE_RE` + ein bloßer nachgestellter `feat./ft.`-Tail), **behält** aber bewusst Versions-Qualifier (Remix/Live/Remastered/Acoustic — das sind eigenständige Aufnahmen, kein Duplikat). Nur die Gruppierungs-Schlüssel-Zeile in `link_single_album_duplicates` geändert; End-to-End über die exakte Endpoint-Query verifiziert (liefert jetzt das Paar). Tests: `test_single_album_linkage_survives_feat_suffix_on_album_cut`/`…_on_single` + `test_dedup_title_key_strips_only_featured_annotations` (`tests/library2/test_importer.py`), `pytest tests/library2` 395 grün, `tests/imports` 676 grün. **Offen (braucht die reale DB des Nutzers, autonom nicht reproduzierbar):** weitere mögliche Mit-Ursachen desselben Symptoms — album_type-Fehlklassifikation eines Multi-Track-„Single"-Release (→ `ep` statt `single`) und alias-getrennte Artist-Rows (teilweise durch §16.3(b) adressiert). Der Feat-Suffix-Fall ist der häufigste und ist jetzt geschlossen.

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

**Audit-Ergänzung 2026-07-14 (Legacy-Enhanced-View-Parity-Audit, siehe Abschnitt 15):** Die Logik existiert bereits vollständig und wiederverwendbar — muss NICHT neu gebaut werden.
- Per-Track: "RG"-Button pro Track-Row (`library.js:4406-4411`) → `POST /api/library/track/<id>/analyze-replaygain` (`web_server.py:12521`) — synchron, LUFS-basiert (`core/replaygain.py`, `RG_REFERENCE_LUFS`), schreibt sofort in Datei-Tags.
- Per-Album: "♫ ReplayGain"-Button (`:3949-3955`) → `POST /api/library/album/<id>/analyze-replaygain` (`web_server.py:12564`) — Background-Thread, 2-Pass (Analyse dann Schreiben von Track+Album-Gain aus Mean-LUFS), gepollt via `GET .../analyze-replaygain/status` (`:12668`).
- Batch (Selektion): `POST /api/library/tracks/analyze-replaygain-batch` (`:12677`).
- Alle drei nutzen dasselbe Modul `core/replaygain.py`, das auch der automatisierte Repair-Job `core/repair_jobs/replaygain_filler.py` nutzt — Analyse-/Tag-Writing-Logik ist bereits identisch zwischen manueller UI-Aktion und Automation.
- **Für Library v2 reduziert sich der Scope auf:** dünne `lib2`-Endpoints (Track-/Album-/Artist-Scope, ID-Mapping auf `lib2_track_id`/`lib2_album_id`) + UI-Buttons analog zu Legacy. Keine neue Analyse-Logik nötig.

---

### 44. Enrich Album/Track-Funktion fehlt — ✅ gefixt (2026-07-16)

**Beobachtung:** Die alte Library bot Enrich: gezielt zusätzliche Metadaten für ein Album/Track abfragen und einzufügen (z.B. Year, Genre, Labels).

**Scope:** Neue Library-v2-Action:
- `POST /api/library/v2/albums/<id>/enrich` — hole aktuelle Metadaten vom bestehenden Provider, update `lib2_albums` + `lib2_tracks`.
- UI: Modal mit durchsuchbaren Quellen (Spotify, Deezer, MB) + Diff-Preview.

**Nächster Schritt:** Audit bestehender Enrichment-Logik (wahrscheinlich im Import-Flow), Scope für Library-v2-Only-Action definieren.

**Audit-Ergänzung 2026-07-14 (Legacy-Enhanced-View-Parity-Audit, siehe Abschnitt 15):** Legacy-Backend existiert vollständig und ist pro-Provider granularer als hier ursprünglich angenommen.
- Legacy `Enrich ▾`-Dropdown (Artist: `library.js:3250-3289`; Album: `:3907-3940`) listet bis zu 12 Provider **einzeln** auf: Spotify, MusicBrainz, Deezer, JioSaavn, Discogs, AudioDB, iTunes, Last.fm, Genius, Bandcamp, Tidal, Qobuz — je nach Entity-Typ eingeschränkt (Genius kein Album, Discogs kein Track, Bandcamp kein Artist). Klick auf einen Eintrag enriched **nur von dieser einen Quelle**.
- `POST /api/library/enrich` (`web_server.py:13629`) → `_run_single_enrichment()` (`:13721-13859`) dispatcht an die bereits initialisierten Background-Enrichment-Worker (`spotify_enrichment_worker`, `deezer_worker`, `mb_worker`, `audiodb_worker`, `itunes_enrichment_worker`, `lastfm_worker`, `genius_worker`, `tidal_enrichment_worker`, `qobuz_enrichment_worker`, `discogs_worker`, `bandcamp_worker`, `jiosaavn_worker`) — eine echte Provider-Re-Query (Genres, Bilder, externe IDs, Bio), kein reines Re-Matching. Per-Service-Concurrency-Lock (`_enrichment_locks`) verhindert Overlap.
- **Für Library v2 reduziert sich der Scope auf:** dünne `lib2`-Endpoints, die an dieselben bestehenden Worker-Methoden delegieren (ID-Mapping auf `lib2_artist_id`/`lib2_album_id`/`lib2_track_id`) + UI-Dropdown analog zu Legacy. Keine neue Provider-Integration nötig.

**Umsetzung (2026-07-16):** Die Enrichment-Worker (`_run_single_enrichment` in `web_server.py`) kennen NUR das Legacy-Schema — sie schreiben direkt in `artists`/`albums`/`tracks`, nie in `lib2_*`. Da lib2-Zeilen ein zeitpunktbezogener Spiegel der Legacy-Library sind (siehe `core/library2/importer.py`), wäre das Ergebnis eines Enrich-Aufrufs ohne einen Re-Sync-Schritt bis zum nächsten vollständigen Re-Import unsichtbar geblieben.
  - `POST /api/library/v2/<entity>/<id>/enrich` (`api/library_v2.py`) löst den `legacy_{artist,album,track}_id`-Back-ref auf (derselbe Mechanismus wie `core/library2/match_status.py`), validiert den Service gegen dessen `SERVICES`-Spaltenmap (Genius kein Album, Discogs kein Track, Bandcamp kein Artist), und delegiert an den per Dependency-Injection übergebenen `run_enrichment`-Callable (`web_server.py` reicht `_run_single_enrichment` direkt durch — kein zirkulärer Import nötig, gleiches DI-Muster wie `acquisition_submission_adapter_getter`).
  - Ein Discography-only-Release (nie aus der Legacy-Library importiert) hat keinen Legacy-Back-ref → `409` mit einer klaren Fehlermeldung statt eines stillen No-Ops.
  - Neues Modul `core/library2/enrich.py::resync_entity_from_legacy` liest nach einem erfolgreichen Worker-Aufruf die jetzt aktualisierte Legacy-Zeile neu ein und überschreibt NUR die deskriptiven Provider-Felder der lib2-Zeile (genres/summary/style/mood/label/banner_url/image_url für Artist; genres/label/explicit/upc/image_url für Album; bpm/explicit/genius_lyrics/copyright für Track) — mit `COALESCE`, damit ein von einem ANDEREN, nicht angefragten Provider unberührtes NULL-Feld nichts Vorhandenes überschreibt. Identitätsfelder (Name/Titel) werden bewusst nicht angefasst. User-Overrides (`metadata_overrides`) liegen in einer separaten Tabelle und werden zur Lesezeit projiziert — ein Overwrite der Basis-Zeile ist daher immer sicher.
  - UI: `EnrichModal` (analog `ManualMatchModal`) + "Enrich"-Action im Artist-Toolbar und ein Icon-Button in der Album-Zeile (Track-Ebene bewusst ausgelassen, um den UI-Umfang zu begrenzen — der Endpoint ist generisch und track-fähig, falls später gewünscht).
  - Tests: `pytest tests/library2/test_enrich_resync.py tests/library2/test_enrich_endpoint.py` (16 neue Tests) + `pytest tests/library2 tests/imports` grün (1153); `vitest`/`oxfmt`/`oxlint --type-check`/`tsc --noEmit` clean.

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

---

## 14. UI-Improvements Session 2 (2026-07-14, späte Session fortgesetzt)

**Konsolidierte UI-Fixes nach Nutzer-Feedback:**

1. **Quality Display mit Boxen (final)** (`a96410da`):
   - Format, Resolution, Bitrate jeweils in eigener Box/Umrandung
   - Kompakt aber deutlich sichtbar (padding 2px 6px, subtle Borders)
   - CSS: `.qualityTag` mit blaulich getöntem Hintergrund + Border
   - Gap zwischen Boxen: 0.5rem für Lesbarkeit

2. **Quality Badges (below profile / upgrade / quality unknown)**:
   - Kompakt: font-size 9px, padding 1px 4px
   - Inline display, minimales Rauschen
   - Farben für Unterscheidung (rot/orange/blau)

3. **Icons konsolidiert (final)**:
   - **Interactive Search**: Mensch-Silhouette (user icon, stroke-basiert, 24x24 viewBox)
   - **Quality Profile**: ⭐ Stern-Icon (einfach, elegant, stroke-basiert)
   - Beide auf gleicher Höhe (alignment fixed)
   - **Automatic Search (global)**: Lupe-Icon (automatic)

4. **Actions Layout**:
   - albumActions: `flex` + `height: 32px` für konsistente Vertical Alignment
   - Alle Toolbar-Buttons auf gleicher Höhe

5. **Nomenklatur Lidarr-aligned**:
   - "Search (global)" → **"Automatic Search (global)"** (überall)
   - Action-Namen konsistent: `AUTOMATIC_SEARCH_RE` statt `SEARCH_MONITORED_RE`
   - Interactive Search: Titel + Icon unverändert

**Nicht implementierbar (UI-only Limitation):**

- **"Match via Source" Display:** Die Information, welche Metadaten-Quelle (Spotify vs. Deezer) matched wurde, ist **nicht in der API vorhanden**. Braucht Backend-Schema um zu trackten, welcher Provider die Metadata liefert. Für nächste Session in Roadmap aufnehmen. **Vertieft in Abschnitt 15, Punkt 46** — Legacy hat dafür ein vollständiges Match-Status-Chip-System + manuelle Re-Match-Funktion, keine reine Anzeige-Frage.

**Bekannte Backend-Issues für Roadmap:**

- **Refresh & Scan Missing Files** (Roadmap #21): Dateien, die nach Scan fehlen, sollten als `missing_confirmed` markiert werden (nicht nur ignoriert). Aktuell wird `file_state` nicht korrekt gesetzt.
- **Managed Tracks Query Bug** (Roadmap #39): GET `/api/library/v2/artists/<id>/duplicates` returnt false negatives.
- **Update Discovery Instability** (Roadmap #38): Michael Jackson, Hirokyu Samono zeigen nicht alle Releases.

**Frontend:** 121 Tests ✓, TypeCheck ✓, Build ✓. Alle UI-Änderungen sind non-breaking und reuse bestehende Data-Structures.

---

## 15. Legacy-Enhanced-View-Parity-Audit (2026-07-14) — vollständiger Feature-Vergleich

**Auslöser:** Nutzer-Frage, ob alle Enrich-artigen Features der alten Library ("Enhanced View", `webui/static/library.js`, 9691 Zeilen) bereits in Library v2 erfasst sind. Ein dedizierter Explore-Agent hat den kompletten Legacy-Code (`library.js` + zugehörige `web_server.py`-Routen + Backend-Module) systematisch nach 9 konkret benannten Feature-Kategorien durchsucht und gegen den aktuellen `library-v2-page.tsx`-Stand verglichen. **Reine Dokumentation — keine Implementierung in dieser Session.**

**Wichtiger Architektur-Hinweis aus dem Audit:** `api/library.py` (311 Zeilen) ist nur eine schreibgeschützte Public-REST-API (GET-only, externe API-Clients) — **nicht** das Backend der Legacy-UI. Alle interaktiven Legacy-Features unten sind über interne Routen in `web_server.py` (40.961 Zeilen) verdrahtet.

**Bereits bekannt/erfasst (siehe Roadmap 43/44 oben, jetzt mit Architektur-Details ergänzt):** ReplayGain, Enrich (Multi-Provider-Dropdown).

**Neu identifiziert, bisher NICHT in der Roadmap erfasst:**

### 45. Reidentify — Re-Filing eines Tracks unter anderem Release fehlt

**Beobachtung:** Legacy hat eine `↔`-Aktion pro Track ("Re-identify — file this track under a different release", `library.js:4427`, `openReidentifyModal`, `:9507-9539`, referenziert Issue #889). Erlaubt es, dieselbe Aufnahme (ISRC-Treffer bevorzugt gerankt) unter einem anderen Release-Typ (Single vs. EP vs. Album) neu einzuordnen und die physische Datei dorthin umzuziehen.

**Architektur (bereits vollständig im Backend vorhanden, wiederverwendbar):**
- `GET /api/reidentify/sources` → `core.imports.rematch_search.available_sources()`
- `GET /api/reidentify/search?source=&q=` → `core.imports.rematch_search.search_release_candidates()`
- `POST /api/reidentify/apply` → staged Copy + Hint via `core.imports.rematch_apply.stage_file_for_reidentify` + `core.imports.rematch_hints.create_hint`; Original wird erst nach erfolgreichem Re-Import entfernt (nur bei `replace=true`).

**Unterschied zu Enrich (Punkt 44):** Enrich holt neue Metadaten-Felder für eine bestehende Entity; Reidentify ändert, **welchem Release** die physische Datei zugeordnet ist (läuft erneut durch die Import-Pipeline unter anderer kanonischer Release-Identität).

**Scope für Library v2:** Reuse der bestehenden `core.imports.rematch_*`-Module (keine zweite Implementierung nötig) + neue `lib2`-Endpoints mit `lib2_track_id`/`lib2_album_id`-Mapping statt Legacy-IDs; UI-Modal analog zu Legacy.

**Priorität:** Mittel — Nischen-Feature (Single-vs-Album-Fehlklassifikation), aber ohne es bleibt dieser Fehlerfall in Library v2 unkorrigierbar.

---

### 46. Match-Status-Anzeige & manuelles Re-Match pro Provider fehlt — ✅ umgesetzt (siehe §17.1; Chips + ManualMatchModal live)

**Beobachtung:** Legacy zeigt auf Artist-/Album-/Track-Ebene farbcodierte Chips (`matched`/`not_found`/`pending`) pro Metadaten-Provider (Spotify, MusicBrainz, Deezer, JioSaavn, AudioDB, Discogs, iTunes, Last.fm, Genius, Tidal, Qobuz, Amazon). Klick öffnet `openManualMatchModal()` — freitextige Provider-Suche (`POST /api/library/search-service` → `core.library.service_search._search_service`) → Auswahl schreibt externe ID + `matched`-Status (`PUT /api/library/manual-match`), oder löst den Match (`PUT /api/library/clear-match`).

**Ergänzt die Notiz in Abschnitt 14** ("Match via Source Display... nicht in der API vorhanden") — dieser Audit zeigt: es ist mehr als reine Anzeige, es ist eine vollständige manuelle Re-Match-Funktion inkl. Locking des kanonischen Release bei manuellem Album-Match (`core.metadata.canonical_version.should_pin_manual_canonical`).

**Wichtige Architektureinschränkung bei fehlenden (Missing) Tracks:**
Da fehlende Tracks (`file_status = 'missing'`) keine Entsprechung (Zeile) in der alten Legacy-Datenbanktabelle `tracks` besitzen, bleibt ihre `legacy_entity_id` dauerhaft `None` (auch nach der Materialisierung in `lib2_tracks`). Daher können diese Tracks auf Einzelebene nicht manuell per Match-Chip zugeordnet werden.

**Nicht zu verwechseln mit:** `core/library/manual_library_match.py` — anderes Feature (Wishlist/Sync-History-Source zu existierendem Library-Track verlinken, um Re-Downloads zu verhindern), keine Metadaten-Provider-Zuordnung.

**Scope für Library v2:** Audit nötig, ob `lib2_artists`/`lib2_albums`/`lib2_tracks` dieselben `{service}_match_status`/`{service}_id`-Spalten spiegeln, die Legacy nutzt, oder nur die IDs ohne Status. Falls Spalten vorhanden: reiner UI-Reuse (Chips + Modal); falls nicht: kleine additive Migration nötig.

**Priorität:** Mittel-Hoch — vom Nutzer explizit als fehlend benannt.

---

### 47. Source-Info-Popover (Download-Provenance) fehlt — ✅ umgesetzt (Track-Detail „Info"-Tab: Service/User/File/Quality/Historie + Blacklist; §16.1-Legacy-ID-Fix in `core/library2/source_info.py`)

**Beobachtung:** Legacy zeigt pro Track ein `ℹ`-Popover (`showTrackSourceInfo`, `:4960-…`) mit: Service-Icon/Label, Soulseek-Username, Original-Dateiname, Dateigröße, Audio-Qualität-String, Bit-Depth/Sample-Rate/Bitrate, "downloaded"-Zeitstempel, Status (rot bei Nicht-„completed"), Anzahl historischer Download-Records. Zusätzlich eine "⛔ Blacklist This Source"-Action direkt aus dem Popover.

**API:** `GET /api/library/track/<id>/source-info` → `database.get_track_downloads(track_id)` mit Fallback-Matching per Pfad/Dateiname.

**Unterschied zu Match (Punkt 46):** Match ist über Metadaten-Provider-IDs; Source-Info ist über **woher die tatsächliche Audiodatei kam** (Download-Quelle/-Qualität/-Provenienz).

**Scope für Library v2:** `database.get_track_downloads()` ist bereits app-weit vorhanden und wiederverwendbar; braucht nur einen `lib2`-Endpoint (Track-ID-Mapping) + UI-Popover-Komponente. Blacklist-Action (`POST /api/library/blacklist`) ebenfalls reusable.

**Priorität:** Niedrig-Mittel — nützlich für Debugging/Vertrauen, nicht blockierend für Kernfunktion.

**Root-Cause-Nachtrag (2026-07-14, Deep-Audit — siehe Abschnitt 16.1):** Der `lib2`-Endpoint EXISTIERT bereits (`api/library_v2.py:1156-1168` → `core/library2/source_info.py:26`), zeigt aber für praktisch jeden Track „No download source data" an, weil er `track_downloads` nur per aktuellem File-Pfad abfragt und dabei den längst vorhandenen `legacy_track_id`-Link ignoriert, den der Importer für JEDEN migrierten Track korrekt setzt. Das ist kein fehlendes Feature mehr, sondern ein konkreter, gut lokalisierter Bug — Priorität entsprechend höher als „nice-to-have".

---

### 48. Rich-Metadata-Edit (Free-Text-Felder + Track-Level-Edit + Bulk-Edit) — ✅ gefixt (2026-07-16, siehe §34)

**Beobachtung:** Legacy erlaubt Editieren von Artist (`name`, `genres`, `label`, `style`, `mood`, `summary`), Album (`title`, `genres`, `year`, `release_date`, `explicit`, `track_count`, `label`, `style`, `mood`) und **Track** (`title`, `track_number`, `bpm`, `explicit`, `style`, `mood`) über inline-editierbare Felder + `PUT /api/library/{artist,album,track}/<id>` mit serverseitigem Feld-Whitelist (`database/music_database.py:12808-12810`). Zusätzlich ein Bulk-Edit-Modal für mehrere selektierte Tracks (`PUT /api/library/tracks/batch` → `database.batch_update_tracks()`).

**Library v2 aktueller Stand** (Roadmap Punkt 8, bereits umgesetzt): Nur `title`/`year`/`release_type` fürs Album (Batch-Override-API) und `name`/`genres` fürs Artist. **Update 2026-07-16:** Track-Level-Basis-Edit existiert inzwischen (Track-Detail-Modal „Metadata"-Tab: `title`/`track_number`/`disc_number` über den Override-Batch-Command, plus „Write Tags to File"). **Update 2026-07-16 (später, siehe §34): BPM/Style/Mood/Label/Explicit-Felder + Bulk-Edit über mehrere selektierte Tracks jetzt umgesetzt.** (Summary bleibt Artist-only, wie im Legacy-Schema — Album/Track hatten dort nie ein Summary-Feld.)

**Wichtiger Architektur-Unterschied:** Library v2 nutzt bereits ein saubereres Konzept — `lib2_metadata_overrides` (Roadmap Punkt 4, Slice 3) trennt validierte Admin-Overrides von der Provider-Baseline, mit Read-Projection. Fehlende Felder (BPM, Style, Mood, Label, Summary, Explicit, Track-Title/Number) sollten in dieses bestehende Override-System **erweitert** werden, nicht als Parallel-Struktur wie Legacys direktes Feld-Whitelisting auf der Katalogtabelle.

**Scope für Library v2:** Override-Store um die fehlenden Felder erweitern; Track-Entity-Typ zum bestehenden Batch-Override-Command hinzufügen (existiert bereits für Artist/Album); Bulk-Edit-UI analog zu Legacy, aber über denselben Override-Endpoint statt einer zweiten Route.

**Priorität:** Mittel-Hoch — Kernfunktionalität einer "Library Manager"-UI; aktuell deutlich eingeschränkter als Legacy.

---

### 49. Alternate-Cover-Art-Picker fehlt — ✅ gefixt (2026-07-16, siehe §27)

**Beobachtung:** Legacy erlaubt Klick auf Album-Cover → "Change cover"-Overlay → `openAlbumArtPicker()` (`:3664-3790`) zeigt mehrere Kandidaten-Cover mit Quellen-Badge (`GET /api/album/<id>/art-options`), Anwenden via `POST /api/album/<id>/art`.

**Library v2 aktueller Stand:** Nur statischer Cover-Platzhalter (`library-v2-page.tsx:201`), kein Picker, keine Alternative-Auswahl.

**Scope für Library v2:** Audit nötig, ob `/api/album/<id>/art-options`/`/art` über `lib2_album_id`-Mapping wiederverwendbar sind oder ein `lib2`-Wrapper nötig ist. Kernlogik (Kandidaten-Fetch von Providern) sollte über den bestehenden `provider_adapters.py`-Boundary laufen (Konsistenz mit Artwork-Regeln aus Abschnitt 1).

**Priorität:** Niedrig — kosmetisch, aber von Nutzern geschätzt bei falschem/fehlendem Auto-Cover.

---

### 50. Interaktives Reorganize (Preview, Mode/Source-Picker, Album-Einzelaktion) fehlt — ✅ gefixt (2026-07-16, siehe §26)

**Beobachtung:** Legacy bietet:
- Pro Album: "📁 Reorganize"-Button → Preview-Modal (`showReorganizeModal`) mit Live-Vorschau (aktueller vs. vorgeschlagener Pfad) → `GET /api/library/album/<id>/reorganize/preview` → Apply via `POST /api/library/album/<id>/reorganize` (enqueued via `core/reorganize_queue.py`).
- Pro Artist: "Reorganize All"-Button → Modal mit Metadata-Mode (API vs. embedded Tags) + Metadata-Source-Picker (Spotify/Deezer/…/auto) → wendet auf **alle Alben des Artists** an (`POST /api/library/artist/<id>/reorganize-all`).
- **Wichtig:** Es gibt **kein** globales „ganze Library reorganisieren"-Feature in Legacy — jede Reorganize-Aktion ist Artist- oder Album-scoped. Das nächstliegende ist der Repair-Job `core/repair_jobs/library_reorganize.py` (library-weiter Scanner, off-by-default, dry-run-default), der aber dieselbe Planner-/Queue-Logik wiederverwendet (`core.library_reorganize.preview_album_reorganize`/`reorganize_album` + `core.reorganize_queue`) — keine zweite Implementierung.

**Library v2 aktueller Stand:** "Rename / Reorganize Files" ist nur ein generischer Eintrag in der `MAINTENANCE_JOBS`-Liste (`library-v2-page.tsx:1009-1042`, `id: 'library_reorganize'`, `scoped: true`) — läuft wie ein Lidarr-Style-Batch-Job ohne Preview, ohne Mode/Source-Wahl, kein Einzelalbum-Sofort-Reorganize.

**Scope für Library v2:** Die zugrundeliegende Planner-Logik (`core/library_reorganize.py::preview_album_reorganize`/`reorganize_album`) und Queue (`core/reorganize_queue.py`) sind bereits app-weit vorhanden und wiederverwendbar — reine Frage neuer, dünner `lib2`-Endpoints (ID-Mapping) + UI-Preview-Modal analog zu Legacy statt des generischen Maintenance-Batch-Eintrags. **Deckt sich mit Roadmap-Punkt 9** (Artist-Scope für Reorganize/Dedup, bereits abgeschlossen für Path-Safety), ergänzt aber die fehlende **interaktive Einzelalbum-Preview-UI**, die Punkt 9 nicht abdeckte.

**Priorität:** Mittel — Kernfeature für Bibliotheks-Pflege, aktuell nur als generischer Batch-Job ohne Feedback/Kontrolle nutzbar.

---

### 51. Interaktiver Missing-Track-Manager ("Manage" → Add to Library / I Have This) fehlt

**Beobachtung:** Legacy zeigt fehlende Tracks disc-/tracknummer-genau in derselben Tabelle wie vorhandene (`enhanced-missing-track-row`, echter Titel aus kanonischer Tracklist statt "Track N", "Missing"-Badge statt Play-Button). Pro fehlendem Track gibt es einen "Manage"-Button (`openMissingTrackManageModal`) mit zwei Pfaden:
- **"Add to Library"** → normaler Wishlist-Flow (`wishlistEnhancedMissingTrack`).
- **"I Have This"** → Datei-Picker, importiert eine existierende Datei direkt in den exakten Album-Slot (`POST /api/library/album/<id>/import-existing-track`).

**Library v2 aktueller Stand:** `completeness.py` löst kanonische Tracklists bereits serverseitig auf (echte Titel statt "Track N", disc-aware) — das Backend-Fundament ist da. **Update 2026-07-16 (Teil-Umsetzung):** Die „Add to Library"-Hälfte existiert inzwischen — `MissingTrackAddButton` pro Missing-Row (materialisiert den Slot via `POST /albums/<id>/missing-tracks/materialize` und monitored ihn → Wishlist-Mirror). **Offen bleibt nur die „I Have This"-Hälfte** (Datei-Picker, Datei direkt an den `lib2_track_id`-Slot binden).

**Scope für Library v2:** UI-seitig ist am wenigsten zu tun (Backend-Tracklist-Auflösung existiert schon) — "Manage"-Button + Modal mit denselben zwei Pfaden hinzufügen: Wishlist-Mirror für "Add to Library" ist bereits die etablierte lib2-Monitoring-Mechanik; "I Have This" bräuchte einen neuen `lib2`-Import-Endpoint, der eine hochgeladene/ausgewählte Datei direkt an den bestehenden `lib2_track_id`-Slot bindet (analog zu Legacys `import-existing-track`, aber durch die lib2-Autolink-Logik statt Legacy-Tabellen).

**Priorität:** Mittel-Hoch — direkt sichtbare UX-Regression gegenüber Legacy (Nutzer sehen "12 missing", können aber nichts tun außer den globalen "Search Monitored"-Button zu drücken).

---

### Priorisierung der neuen Punkte 45–51

1. **48 (Rich-Metadata-Edit) & 51 (Missing-Track-Manager)** — Mittel-Hoch: direkt sichtbare Kernfunktions-Lücken gegenüber Legacy.
2. **46 (Match-Status-Anzeige)** — Mittel-Hoch: vom Nutzer explizit benannt.
3. **45 (Reidentify) & 50 (Interaktives Reorganize)** — Mittel: Nischenfälle bzw. bereits teilweise über generischen Batch-Job abgedeckt.
4. **47 (Source-Info-Popover) & 49 (Cover-Art-Picker)** — Niedrig-Mittel: nützlich, nicht blockierend.

**Gemeinsames Muster über alle 7 Punkte:** Fast überall existiert die eigentliche Backend-Logik bereits app-weit (Reorganize-Planner, Enrichment-Worker, ReplayGain-Analyse, Rematch-Module, Download-Provenance-Query, Manual-Match-Service) und ist **wiederverwendbar** — der Aufwand für Library v2 liegt primär in dünnen `lib2`-ID-Mapping-Endpoints + UI, nicht in neuer Kernlogik. Das passt zur bestehenden Reuse-First-Philosophie (Abschnitt 4.5).

**Status:** Reine Dokumentation, keine Implementierung. Nächster Schritt bei Aufnahme in aktive Arbeit: mit Nutzer Priorität festlegen, dann pro Punkt eine TDD-Slice wie bei den bisherigen Roadmap-Punkten.

---

## 16. Importer-Deep-Audit (2026-07-14, zweite Session) — drei konkrete Root-Causes

**Auslöser:** Nutzer bat um einen erneuten, gezielten Blick auf `core/library2/importer.py` und einen Feature-Vergleich gegen die alte Library, plus drei live im Dev-Server beobachtete Bugs. Zwei dedizierte Explore-Agents haben die Root-Causes mit Datei:Zeile-Belegen identifiziert. **Reine Dokumentation — keine Implementierung in dieser Session.**

### 16.1 Source-Info zeigt „No download source data" trotz vorhandener Provenance-Daten

Vertieft Punkt 47 (Abschnitt 15). Bestätigter Root Cause, keine Vermutung mehr:

- `core/library2/source_info.py:26-50` (`track_source_info`, aufgerufen von `api/library_v2.py:1156-1168`) fragt `track_downloads` **ausschließlich per aktuellem Datei-Pfad** ab (exact match, dann `LIKE '%/<filename>'`-Fallback) — laut eigenem Docstring (`source_info.py:6-11`) bewusst so gebaut, weil „lib2-Track-IDs ein eigener ID-Space sind, unabhängig von den Legacy-`tracks`-Rows, auf die `track_downloads.track_id` zeigt".
- Das ist unnötig vorsichtig: `lib2_tracks.legacy_track_id` (`core/library2/schema.py:142`) UND `lib2_track_files.legacy_track_id` (`schema.py:196`) existieren genau für diesen Zweck und werden vom Importer für **jeden** migrierten Track zuverlässig gesetzt — sowohl beim Insert (`importer.py:596-598`, `642-649`) als auch beim Update (`importer.py:655-664`), bestätigt durch `tests/library2/test_importer.py:158-169,190,204-215`. Andere lib2-Module nutzen `legacy_track_id` bereits genau so als Join-Schlüssel (`match_status.py:60,149-162`, `completeness.py:123,149`, `identity_history.py:68`) — `source_info.py` ist der einzige Ausreißer.
- Die Legacy-Route macht es umgekehrt richtig (`web_server.py:14229-14260`): erst `database.get_track_downloads(track_id)` per **ID** (`database/music_database.py:14151-14164`), Pfad/Dateiname nur als Fallback — und **self-healed** dabei sogar den Link zurück (`get_download_by_filename(..., link_track_id=track_id)`, `database/music_database.py:14197-14222`).
- Warum reine Pfad-Suche in der Praxis so oft leerläuft: `tracks.file_path` (Legacy) und `lib2_track_files.path` können unabhängig voneinander durch Rename/Reorganize/Repair verändert werden (`core/reorganize_runner.py:71`, `core/imports/pipeline.py:1482`, `core/repair_jobs/track_number_repair.py:979`, `core/repair_worker.py:1759-1762,3679-3691`) — keiner dieser Call-Sites aktualisiert `track_downloads.file_path` mit. Es gibt sogar einen dafür vorgesehenen Helper, `database.update_provenance_file_path()` (`database/music_database.py:14166-14179`), der aber **nirgends im Code aufgerufen wird** (nur seine eigene Definition matched einen Grep). Sobald sich der Pfad seit dem Download geändert hat, findet die pfad-only Suche nichts mehr, obwohl die Zeile via `legacy_track_id` trivial erreichbar wäre.

**Fix-Richtung (für spätere Umsetzung):** `source_info.py` soll zuerst `track_downloads WHERE track_id = lib2_track.legacy_track_id` (bzw. `lib2_track_files.legacy_track_id`) probieren — analog zur Legacy-Route — und nur bei `legacy_track_id IS NULL` (reine Autolink-Neuanlage ohne Legacy-Pendant) auf die bestehende Pfad-/Dateiname-Suche zurückfallen.

**✅ Umgesetzt (2026-07-15, Commit `ff6edb10`):** genau wie oben — `track_source_info` löst jetzt zuerst über `lib2_tracks.legacy_track_id` (dann den Primary-File-Link) auf und fällt nur bei fehlender Legacy-ID auf die Pfad-/Suffix-Kette zurück. Tests in `tests/library2/test_source_info.py` (Datei verschoben → nur noch via Legacy-ID auffindbar; Suffix-Kollision wird nicht mehr fälschlich mitgezogen; Autolink-only Track fällt weiterhin auf den Pfad zurück).

**Priorität:** Hoch genug, um vor den generischen Roadmap-Punkten 45–51 behandelt zu werden — es ist ein Bug mit klarer Lokalisierung, kein offenes Feature-Gap.

---

### 16.2 Album wird beim Teil-Import komplett monitored statt nur die gewünschten Tracks

**Beobachtung (User):** Import eines Albums, von dem nur 3 Tracks je in der Wishlist standen bzw. heruntergeladen wurden — trotzdem wird das GESAMTE Album auf `monitored` gesetzt, nicht nur die 3 gewünschten Tracks.

**Root Cause:** `lib2_albums.monitored` hat Schema-Default `1` (`core/library2/schema.py:100`); der Album-INSERT im Haupt-Legacy-Import-Loop (`importer.py:539-548`) trägt `monitored` gar nicht explizit in die Insert-Felder ein — jedes importierte Album mit mindestens einer lokalen Track-Zeile bekommt also automatisch `monitored=1`, unabhängig davon, wie viele seiner Tracks tatsächlich gewollt waren. Das steht im Kontrast zu `seed_wishlist_tracks` (`importer.py:782-1063`), das für den Wishlist-Seeding-Pfad explizit `monitored=0` auf Album-Ebene einsetzt (`importer.py:929-934`, Docstring `789-791`: „ein gewishlisteter Song darf nicht das ganze Album monitored machen") — diese Sorgfalt existiert aber nur im Wishlist-Seeding-Pfad, nicht im Haupt-Album-Loop, der die Album-Zeile für ein teilweise vorhandenes Album tatsächlich erzeugt.

Der Fehler pflanzt sich fort:
1. `seed_legacy_rules` (`monitor_rules.py:179-200`, aufgerufen von `importer.py:687`) friert den (bereits falschen) Album-`monitored`-Wert 1:1 als `lib2_monitor_rules`-Zeile mit `provenance='legacy_import'` ein — ohne zu prüfen, wie viele Tracks tatsächlich gewishlistet/heruntergeladen wurden.
2. `wanted.py::_decide()` (Zeilen 68-84, aufgerufen via `recompute_wanted`, `importer.py:697`) lässt jeden Track OHNE eigene Track-Regel auf die Album-Regel zurückfallen (`alb_mon is not None`, Zeile 78-79) — die 3 tatsächlich gewünschten Tracks bekommen korrekt eigene `wishlist_import`-Regeln, aber ALLE anderen Tracks des Albums erben `wanted=True` einzig weil das Album fälschlich monitored=1 ist.
3. `completeness.py::_persist_tracklist_tracks` (Zeile 250) verstärkt das: neu materialisierte Missing-Track-Platzhalter werden direkt mit `monitored = 1 if al["monitored"] else 0` angelegt — die Platzhalter für die nie gewollten Songs kommen also schon monitored zur Welt.

Die Album-Monitor-Intent-Snapshot/Restore-Logik (`snapshot_album_monitor_intent`/`restore_album_monitor_intent`, `monitor_rules.py:55-102`, `importer.py:434-439,680-682`) bewahrt NUR Album-Entity-Regeln über Re-Importe hinweg — sie leitet den initialen Album-Flag nie aus „wie viele/welche Tracks waren gewollt" ab. Track-Ebene ist an sich korrekt (`_has_preserved_intent`/`_reconcile_legacy_snapshot`, `importer.py:253-264`), das Album-Flag selbst ist der Bug.

**Fix-Richtung:** Album-INSERT im Haupt-Loop (`importer.py:539-548`) sollte `monitored` aus der tatsächlichen Track-Datenlage ableiten (z.B. `monitored=1` nur wenn ALLE oder eine konfigurierbare Mehrheit der bekannten Tracks lokal vorhanden/gewishlistet sind), statt den Schema-Default durchzureichen — analog zur bereits vorhandenen Sorgfalt in `seed_wishlist_tracks`.

**✅ Umgesetzt (2026-07-15):**
- Album-Flag (Commit `15742513`): der Haupt-Loop-INSERT leitet `monitored` jetzt aus der Ownership ab — `1` nur wenn das Album voll vorhanden ist (present ≥ bekannte Rows und ≥ dem Metadaten-`expected_track_count`), sonst `0`. Ein Teil-Album startet damit unmonitored; nur die konkret gewollten Tracks (`wishlist_import`-Regeln, die die Album-Regel überstimmen) bleiben `wanted`. Fully-owned Alben behalten `monitored=1`. Missing Tracks bleiben in der Album-Detail-Ansicht sichtbar (über `expected_track_count`).
- Track-Flag-Folgefix (Commit `d76a8222`): der Track-INSERT setzt `monitored` jetzt konsistent aus dem Album-Flag, damit die Roh-Flag-Spalte nicht mehr von der `wanted`-Projektion abweicht (ein Missing-Track eines Teil-Albums liest damit auch als Flag `0`, nicht mehr Schema-Default `1`).
- Zwei Tests, die das alte Blanket-Monitoring kodierten, wurden auf die korrigierte Semantik aktualisiert (`test_queries.py`, `test_wanted_projection.py`); neuer Regressionstest `test_partial_album_is_not_blanket_monitored_on_import`.

**Präzisierung 2026-07-17 (§67 supersedes die Track-Flag-Aussage):** Eine
vorhandene Datei ist selbst konkrete Track-Abdeckung und bleibt deshalb auch
innerhalb eines partiellen Albums individuell monitored/wanted. Dafür existiert
die eigene Provenienz `file_import`, die eine rein abgeleitete negative
`legacy_import`-Album-Baseline überstimmt. Der Parent wird genau bei
vollständiger Abdeckung gesetzt: jeder kanonische Slot hat entweder eine aktive
Datei oder eine positive `wishlist_import`-Regel. Explizite Benutzerregeln
bleiben stärker.

**Priorität:** Hoch — verfälscht das Monitoring-Verhalten für jeden Nutzer, der nur Teile eines Albums wollte, und führt zu ungewollten Auto-Downloads/Upgrade-Scans für nie gewünschte Tracks.

---

### 16.3 Track-Nummer-Korruption ("swag" → alle Tracks Nummer 1; "Thriller 40" → alle Tracks 2/3/4 dupliziert)

**Beobachtung (User, live Dev-Server):** Album "swag" — jeder Track bekam `track_number=1`, wodurch alle bis auf einen als "missing" erscheinen (nur ein DB-Slot für Position 1). Album "Thriller 40" (40th-Anniversary-Edition) — Tracks bleiben durchgehend bei Nummer 2/3/4 (dupliziert), auch nach "Update Discography"; Verdacht: falsch gematchter Artist.

Zwei unabhängige Root Causes in verschiedenen Modulen:

**(a) Alle Tracks bekommen dieselbe Nummer — Default-Floor ohne Batch-Eindeutigkeits-Check:**
- `core/imports/track_number.py::resolve_track_number()` (Zeilen 106-161) probiert Album-Info → Track-Info → verschachtelte Spotify-Daten → Dateiname-Präfix → Embedded-Tag, gibt `None` zurück wenn alles fehlt.
- `core/imports/pipeline.py:892-904` ist die Stelle, die den finalen Fallback anwendet:
  ```
  if not isinstance(track_number, int) or track_number < 1:
      logger.error(f"Invalid track number ({track_number}), defaulting to 1")
      track_number = 1
  ```
  Das ist ein **Per-Track-Fallback ohne Batch-/Album-weiten Eindeutigkeits-Check** — wenn für einen ganzen Batch (z.B. ein via Search-Endpoint ohne Positions-Daten gematchtes Album, Dateinamen ohne numerisches Präfix, kein Embedded-Tag) keine der 4 Quellen eine brauchbare Nummer liefert, floort JEDER Track unabhängig auf `1`.
- `core/library2/importer.py:579,587,595` (`_pick(row, "track_number")`) kopiert den (bereits korrupten) Legacy-Wert 1:1 nach `lib2_tracks.track_number` — **keine Validierung, keine Deduplizierung, kein Fallback auf Datei-/Scan-Reihenfolge**. Der Importer selbst hat keine Logik, „N Tracks teilen sich track_number=1" zu erkennen, bevor die Zeile geschrieben wird.

**(b) Falscher Artist verhindert Selbstheilung via Tracklist-Abgleich:**
- `core/library2/importer.py::_ArtistResolver.get_or_create_by_name` (Zeilen 147-159) matched/erstellt `lib2_artists` rein über `normalize_name(name)`-Stringgleichheit — keine Disambiguierung über `spotify_id`/`musicbrainz_id`/Genres. Das ist der wahrscheinliche Mechanismus, über den ein Album an die falsche Artist-Entity gehängt wird (Namenskollision oder falsch getaggter Upload) — genutzt sowohl beim Wishlist-Artist-Seeding (`importer.py:888,1020`) als auch im Haupt-Legacy-Import (`resolver.upsert_legacy`/`get_legacy`).
- `core/library2/completeness.py::resolve_tracklist` (Zeilen 286-389) holt die kanonische Tracklist über `al["primary_artist_id"]` → löst NUR den Namen dieses Artists auf (`completeness.py:350-357`) und übergibt `(album_title, artist_name)` an `fetch_album_tracklist`. Zeigt `primary_artist_id` auf die falsche Artist-Entity, wird die falsche (oder gar keine) Provider-Release geholt — die echte "Thriller 40th Anniversary"-Tracklist wird nie abgeglichen.
- Entscheidend: `_persist_tracklist_tracks` (`completeness.py:170-283`) matched/dedupliziert kanonische Einträge gegen lokale Zeilen **ausschließlich über `(album_id, disc_number, track_number)`** (`completeness.py:226-230`) — **niemals über den Titel**. Selbst wenn eine (evtl. falsche) Tracklist geholt wird, aktualisieren Einträge, deren `track_number` mit einer bereits korrupten lokalen Zeile kollidiert (aus (a): dupliziertes 2/3/4), einfach dieselbe existierende Zeile wieder und wieder. Es gibt keinen titel-basierten Re-Key, der „diese 3 verschiedenen Songs beanspruchen alle track_number=3" erkennen könnte. Genau deshalb repariert "Update Discography" die Duplikate nicht — der Abgleichs-Schlüssel (disc+number) ist exakt das korrupte Feld, kann sich also über diesen Pfad nie selbst heilen.

**Fix-Richtung:**
- `pipeline.py:892-904`: Batch-/Album-weiten Uniqueness-Check vor dem `default to 1`-Fallback einziehen (z.B. Scan-Reihenfolge/Dateiname-Sortierung als letzter Fallback statt eines konstanten Werts für alle).
- `_ArtistResolver.get_or_create_by_name`: Disambiguierung über Provider-IDs vor reinem Namens-Match, deckt sich mit Roadmap-Punkt 40 (Artist-Aliasing).
- `completeness.py::_persist_tracklist_tracks`: Titel-basierten Abgleich (zusätzlich zu disc+number) einziehen, damit ein korrekt gematchter Re-Fetch auch kollidierende/duplizierte Nummern reparieren kann statt sie nur zu bestätigen.

**Umsetzungsstand (2026-07-15):**
- **✅ Titel-basierte Heilung (Commit `eca36caa`):** `_persist_tracklist_tracks` bevorzugt jetzt einen eindeutigen, noch nicht in diesem Lauf berührten lokalen Row mit gleichem (normalisiertem) Titel über den `(disc, number)`-Schlüssel und schreibt dessen `disc`/`number` in-place um. Ein doppelter Titel heilt nie (Eindeutigkeits-Guard → Remixe/Intros bleiben sicher). Damit repariert ein korrekt gefetchter Re-Fetch kollabierte/vertauschte Nummern, statt Duplikate anzulegen — genau der Grund, warum „Update Discography" es vorher nie reparierte. Regressionstest `test_persist_tracklist_heals_duplicated_track_numbers_by_title`.
- **✅ Prävention (a) Main-Pipeline Scan-Order-Fallback (Commit `dbb3b84e`):** statt jeden nicht auflösbaren `track_number` konstant auf `1` zu floren, nimmt `pipeline.py` jetzt die 1-basierte Sortier-Position der Datei unter ihren Audio-Geschwistern im selben Verzeichnis (`track_number.py::track_number_from_directory_order`, reine Funktion, unit-getestet). Album-Bundles stagen alle Dateien eines Albums gemeinsam in ein Verzeichnis → stabile, DISTINKTE Fallback-Reihenfolge statt Kollaps auf 1. Greift ausschließlich, wenn keine Quelle eine Nummer lieferte → kann nie eine echte Nummer überschreiben. Generischer Main-Pipeline-Gewinn (§4.5), unabhängig von Library v2.
- **✅ Prävention (b) Provider-NEUTRALE Artist-/Album-Disambiguierung (Commits `c5f3828c` + `610482f6`):** `_ArtistResolver` matcht jetzt über die **ID EINER BELIEBIGEN Quelle** — Deezer (SoulSyncs *Default*-Quelle!), MusicBrainz, Spotify, Tidal, Qobuz — nicht mehr Spotify-hardcoded. Der Schlüssel ist der app-weite `external_ids`-Source→ID-Map (genau der, den `discography.py` schon nutzt): ID-Match schlägt den Namensschlüssel, gleiche ID → gleiche Entity (auch unter anderem Anzeigenamen), KONFLIKT pro Quelle (gleiche Quelle, andere ID) → eigener Row, sonst werden neue IDs adoptiert. **Wichtig (Nutzer-Feedback):** der Importer importiert jetzt AUCH ALLE Provider-IDs — `upsert_legacy` schreibt `deezer_artist_id`/`musicbrainz_artist_id`/`spotify_artist_id`/tidal/qobuz in `external_ids`, und der Album-Import schreibt `deezer_album_id`/`spotify_album_id`/`musicbrainz_release_id` (+ tidal/qobuz) in Album-`external_ids`, die `completeness.resolve_tracklist` bereits liest. Damit verliert ein Deezer-Nutzer seine Identität nicht mehr, ein Album landet nicht an der falschen gleichnamigen Entity, und die EXAKTE Provider-Release ist holbar → Titel-Heilung repariert die Nummern (deckt auch „Thriller 40" ab). Grenze: die Legacy-Wishlist bleibt Spotify-Schema (`spotify_track_id`/`spotify_data`); Value-basiertes Matching vereint ihre Rows trotzdem mit den Library-IDs. Beschränkte Slice von Roadmap-Punkt 40 (Artist-Aliasing).

**Priorität:** Hoch — führt zu sichtbarem Datenverlust in der UI (Tracks als "missing" trotz vorhandener Datei) und ist durch normale Nutzer-Aktionen ("Update Discography") nicht selbst-heilend.

---

### 16.4 Priorisierung Abschnitt 16

1. **16.3 (Track-Nummer-Korruption)** — Kritisch: sichtbarer Datenverlust ("missing" trotz vorhandener Datei), nicht selbstheilend.
2. **16.2 (Über-breites Album-Monitoring)** — Hoch: verfälscht Monitoring-/Auto-Download-Verhalten systematisch bei jedem Teil-Album-Import.
3. **16.1 (Source-Info-Query-Bug)** — Hoch-genug-lokalisiert, um vor Roadmap 45–51 behandelt zu werden, aber kein Datenverlust — nur fehlende Anzeige bereits vorhandener Daten.

**Status:** Reine Dokumentation (zwei Explore-Agents, Datei:Zeile-verifiziert), keine Implementierung in dieser Session.

---

### 16.5 Umsetzungsstand (2026-07-15)

Abschnitt 16 wurde am 2026-07-15 in mehreren fokussierten, TDD-getriebenen
Commits (jeder mit eigenem Regressionstest, `pytest tests/library2` grün — 392
Tests, `tests/imports` grün — 676 Tests) **vollständig** abgeschlossen:

| Punkt | Stand | Commit(s) |
|-------|-------|-----------|
| **16.1** Source-Info-Query-Bug | ✅ vollständig | `ff6edb10` |
| **16.2** Über-breites Album-Monitoring (Album-Flag) | ✅ vollständig | `15742513` |
| **16.2** Track-Flag-Konsistenz-Folgefix | ✅ vollständig | `d76a8222` |
| **16.3** Titel-basierte Nummern-Heilung (lib2) | ✅ vollständig | `eca36caa` |
| **16.3(a)** Main-Pipeline Scan-Order-Fallback (statt `floor-to-1`) | ✅ vollständig | `dbb3b84e` |
| **16.3(b)** Provider-neutrale Artist-/Album-Disambiguierung + Import ALLER Provider-IDs (Deezer/MB/Spotify/…) | ✅ vollständig | `c5f3828c` + `610482f6` |

**Alle drei Bugs sind vollständig geschlossen — inkl. Prävention der
Erstkorruption.** Die zwei zuvor bewusst zurückgestellten Präventions-Teile
wurden auf ausdrücklichen Nutzerwunsch nachgezogen: 16.3(a) ist ein generischer
Main-Pipeline-Gewinn (§4.5, unabhängig von Library v2 — Scan-Order-Fallback nur
als letzter Ausweg, überschreibt nie eine echte Nummer), 16.3(b) ist die
beschränkte Slice von Roadmap-Punkt 40 (Artist-Aliasing), die 16.3 braucht:
korrekt gematchter Artist → korrekter Tracklist-Fetch → Titel-Heilung repariert
die Nummern (auch der „Thriller 40"-Fall). Alle Fixes sind additiv und mit
eigenen Tests abgesichert.

> **Korrektur (2026-07-15, siehe §17.2): diese „vollständig geschlossen"-Aussage
> ist zu optimistisch.** Die Heilungslogik selbst ist korrekt, wird aber für
> bereits vorhandene, bereits korrumpierte Alben **nie aufgerufen** — live in der
> Dev-DB reproduziert (SWAG-Album, Nutzer-Report). Siehe §17.2 für den
> Root-Cause und Fix-Scope.

---

## 17. Nutzer-Bug-Report-Session (2026-07-15) — Live-Verifikation + Root-Cause-Audit

**Auslöser:** Nutzer meldete eine lange Liste von vermeintlichen Regressionen/fehlenden
Features in Library v2 gegenüber der Legacy-Enhanced-View, teils Wiederholungen von
Dingen, die dieses Dokument bereits als „gelöst" markiert hatte. Diese Session hat
JEDEN Punkt gegen den echten laufenden Dev-Server + die echte Dev-DB verifiziert
(nicht nur Code gelesen) — inklusive zweier dedizierter Research-Agents für die
größeren Audits (Import-Performance, Importer-Datenverlust). **Reine Dokumentation —
keine Implementierung in dieser Session**, mit einer Ausnahme: die lokale Dev-Umgebung
wurde neu gestartet (siehe §17.0), weil sie einen Großteil der gemeldeten Probleme
verursacht hat.

### 17.0 KRITISCH: Lokale Dev-Umgebung lief nicht über `dev.py` — erklärt einen Großteil der gemeldeten „fehlenden" Features

**Befund:** Der laufende Backend-Prozess war ein nackter `python web_server.py`
(gestartet 2026-07-14 20:31 Uhr), nicht über `dev.py` orchestriert. Das bedeutet zwei
unabhängige Staleness-Probleme gleichzeitig:

1. **Backend 19+ Stunden veraltet:** zwischen 20:31 Uhr und dem Zeitpunkt dieser
   Session landeten ~15 Commits auf diesem Branch, darunter **genau die Commits, die
   der Nutzer als „fehlend" meldete** — u.a. `1733ed74` (Match-Chips + manueller
   Re-Match, §46, 23:33 Uhr), `eca36caa`/`dbb3b84e`/`c5f3828c`/`610482f6` (§16.3-Fixes,
   heute Vormittag/Mittag). Der Prozess kannte diese Routen/Fixes schlicht nicht.
2. **Frontend wurde aus einem vorgebauten statischen Bundle serviert, nicht per Vite-HMR:**
   `webui/static/dist/assets/main-tMy59lLz.js` (Build-Zeitstempel 2026-07-14 21:00 Uhr)
   enthält **null** Vorkommen des Strings `match-status` — der Match-Chip-Code war zum
   Build-Zeitpunkt noch gar nicht geschrieben. Ein separat laufender, aber vom Backend
   entkoppelter `vite --port 5173`-Prozess änderte daran nichts, weil `web_server.py`
   ohne die von `dev.py` gesetzten Env-Vars startet und deshalb NICHT auf den
   Vite-Dev-Server proxied, sondern den statischen Build ausliefert
   (`core/webui.py::should_serve_webui_spa`/`build_webui_vite_assets`).

**Fix für diese Session:** beide Prozesse gestoppt, sauber über
`.venv/bin/python dev.py` neu gestartet. Verifiziert: die Seite lädt jetzt
`http://127.0.0.1:5173/static/dist/@vite/client` + `main.tsx` direkt (echtes HMR)
statt eines gehashten Bundles.

**Für den Nutzer wichtig:** Bitte **immer `dev.py` verwenden**, nie `web_server.py`
direkt starten — sonst driftet die laufende Instanz unbemerkt von main auseinander und
neue Features/Fixes wirken „fehlend", obwohl sie längst im Code sind. Ein guter Teil
der unten als „bereits erledigt" markierten Punkte (17.1) war exakt aus diesem Grund
als Bug gemeldet worden. Die verbleibenden Punkte (17.2–17.5) wurden NACH dem Neustart
gegen den aktuellen Code erneut verifiziert und sind echte, weiterhin offene Bugs.

---

### 17.1 §46 Match-Status-Chips — bereits vollständig implementiert, war nur die Umgebung (17.0)

Nach dem Neustart live verifiziert (Playwright, headless): `GET
/api/library/v2/artists/21/match-status` liefert die vollen Legacy-Match-Daten, und
die UI rendert sie korrekt im Artist-Header:

```html
<button class="_matchChip..." title="no id · click to (re)match">Spotify: pending</button>
<button class="_matchChip..." title="id: 259 · last: 2026-07-14 19:26 · click to (re)match">Deezer: matched</button>
```

— exakt das vom Nutzer gewünschte Verhalten (Spotify: pending, anklickbar, öffnet
manuelles Re-Match). Album-/Track-Ebene nutzt denselben Mechanismus
(`album_match_bundle`, `core/library2/match_status.py:129-158`) und ist nach demselben
Muster verdrahtet (`library-v2-page.tsx:3130-3136` für Track-Zeilen). **Kein weiterer
Implementierungsbedarf** — der Nutzer sollte dies nach einem `dev.py`-Neustart
(17.0) direkt sehen können.

---

### 17.2 §16.3 Track-Nummer-Korruption ist NICHT vollständig geschlossen — Heilungspfad wird für Bestandsalben nie aufgerufen

**Live reproduziert (2026-07-15, nach dem Neustart, also gegen aktuellen Code) mit
genau dem vom Nutzer genannten Fall — Justin Bieber, Album „SWAG":**

```
lib2_tracks WHERE album_id=574 (SWAG):
  13 Zeilen mit disc=1, track_number=1  (ALLE mit legacy_track_id gesetzt = echte, heruntergeladene Dateien)
  20 Zeilen mit disc=1, track_number=2..21  (KEIN legacy_track_id = Missing-Platzhalter)
```

Mehrere Titel existieren doppelt — einmal als „present" bei `track_number=1`, einmal
als „missing"-Platzhalter bei der korrekten Nummer (z.B. „DAISIES" bei Nummer 1 UND
Nummer 2, „YUKON" bei 1 UND 3, „GO BABY" bei 1 UND 4, „ZUMA HOUSE" bei 1 UND 19, …).
Die API zeigt entsprechend `1/34` present, `33 missing` — genau das vom Nutzer
beschriebene Symptom.

**Das ist exakt der Fall, den die Titel-Heilung (`eca36caa`, §16.3) reparieren
sollte.** Test: `POST /api/library/v2/artists/19/discography/refresh` (= der
„Update Discography"-Button) ausgeführt — **vorher und nachher per SQL verglichen,
keine einzige Zeile hat sich verändert.** Response: `{"added":0, "enriched":56,
"snapshot_changed":false}`.

**Root Cause (Code-Trace):** `resolve_tracklist`/`_persist_tracklist_tracks`
(die Funktion mit der Titel-Heilung) wird **ausschließlich** über
`auto_monitor_releases(album_ids=...)` aufgerufen
(`core/library2/discography.py:402-416`), und diese Liste kommt aus
`stats["auto_monitor_album_ids"]` — das sind **nur neu entdeckte Releases** dieses
Laufs (`refresh_artist_discography`, `discography.py:497-516`). SWAG ist bereits
bekannt (`origin='library'`, physische Dateien vorhanden) → landet nie in dieser
Liste → `resolve_tracklist` wird für dieses Album **nie erneut aufgerufen** →
die Titel-Heilung kommt nie zum Zug, egal wie oft „Update Discography" geklickt wird.

Auch der periodische Reparatur-Sweep (`precache_tracklists`,
`core/library2/completeness.py:456-487`, iteriert `_partial_album_rows`) greift
hier nicht: dessen Kriterium ist `expected_track_count <> COUNT(*)`
(`completeness.py:440-453`) — bei SWAG ist das bereits `33 == 33` (13 echte + 20
Platzhalter-Zeilen ergeben zusammen die erwartete Zahl), das Album sieht für diesen
Zähler also fälschlich „vollständig" aus, obwohl 13 Zeilen kollidieren und 20 davon
unnötige Duplikate sind.

**Fix-Richtung:** „Update Discography" (bzw. ein neuer expliziter „Repair Track
Numbers"-Button) muss `resolve_tracklist` zusätzlich für **bereits vorhandene**
`origin='library'`-Alben des Artists aufrufen — mindestens für solche mit erkennbarer
Track-Number-Kollision (mehrere Zeilen mit gleichem `(disc_number, track_number)`)
oder Titel-Duplikaten zwischen present/missing. Am einfachsten: die
`auto_monitor_album_ids`-Liste in `refresh_artist_discography`
(`discography.py:507`) um die Album-IDs des Artists erweitern, die eine
`(disc,number)`-Kollision haben (billige SQL-Aggregation), statt nur neu entdeckte
IDs zu nehmen.

---

### 17.3 Rohes ISO-Datumsformat bei „Thriller 40" — kein Matching-Problem, sondern fehlende Datums-Normalisierung

Nutzer-Hypothese war „liegt am nicht gematchten Artist" — **live widerlegt:**
Michael Jackson ist über Deezer/MusicBrainz/iTunes/AudioDB/Discogs vollständig
gematcht (nur Spotify pending, siehe §17.1), UND derselbe Artist hat parallel ein
sauber formatiertes Release („Run It Up! (Remix)": `release_date: "2023-04-14"`)
sowie das kaputte („Thriller 40": `release_date: "1982-11-29T08:00:00Z"`).
Der Unterschied ist nicht der Artist, sondern die **Herkunft** des Release
(`origin='library'` vs. `origin='discography'`).

**Root Cause:** Der Rohwert steckt bereits so in der LEGACY-Tabelle:
`sqlite3 database/music_library.db "SELECT release_date FROM albums WHERE id=630009860"`
→ `1982-11-29T08:00:00Z`. Der lib2-Importer kopiert ihn 1:1 verbatim
(`_pick(row, "release_date")`, `core/library2/importer.py:709`, sowie
UPDATE-Pfad `:730`) — keine Normalisierung, keine Formatierung. Das Frontend
rendert den Rohwert ebenfalls ungefiltert (`library-v2-page.tsx:2283`:
`album.release_date ?? album.year`; `:2921`: `album.release_date ||
album.year`) — an keiner Stelle im gesamten Anzeige-Pfad existiert eine
Datums-Parse-/Format-Funktion.

**Fix-Richtung:** eine kleine `formatReleaseDate(value)`-Hilfsfunktion im Frontend
(oder alternativ zentral bei der Serialisierung in `core/library2/queries.py`), die
sowohl `YYYY-MM-DD` als auch volle ISO-8601-Strings mit Zeitanteil robust auf ein
einheitliches Anzeigeformat normalisiert (z.B. nur den Datumsteil extrahieren,
dann lokalisiert formatieren). **Nutzer hat ein konfigurierbares EU/US-Datumsformat
explizit als „später, nicht jetzt" zurückgestellt** — für diese Session reicht ein
festes, konsistentes Format; die Konfigurierbarkeit kann eine spätere Iteration
sein.

---

### 17.4 „All Releases"-Tab lädt nicht automatisch, wenn er der Startzustand ist (nur bei explizitem Klick)

Bereits als Design in §3 dokumentiert („Der erste Wechsel zu All Releases fetcht
automatisch") — aber genau das Wort „Wechsel" ist der Bug. Code:

```tsx
// library-v2-page.tsx:2348
const releasesMode = search.releases;   // direkt aus dem URL-Search-Param, kein useState

// library-v2-page.tsx:2424-2430
function setReleasesMode(mode: 'library' | 'all') {
  void navigate({ search: (p) => ({ ...p, releases: mode }) });
  if (mode === 'all' && artist && artist.discography_count === 0 && !discographyBusy) {
    void updateDiscography();
  }
}
```

`setReleasesMode` ist **nur** an die `onClick`-Handler der Toggle-Buttons gebunden
(`:2650`, `:2657`). Wenn `releasesMode` aber schon beim Mount `'all'` ist (URL-Param
aus vorheriger Navigation/Browser-Persistenz/Bookmark), wird dieser Handler nie
aufgerufen — es gab ja keinen Klick, der Tab war schon aktiv — und
`updateDiscography()` feuert nie. Erst ein Wechsel weg und zurück löst den
`onClick`-Pfad tatsächlich aus, was exakt das vom Nutzer beschriebene Workaround-
Verhalten erklärt.

**Fix-Richtung:** die Fetch-Bedingung aus dem Klick-Handler in einen `useEffect`
verschieben, der auf `[releasesMode, artist?.discography_count, discographyBusy]`
lauscht — dann feuert er sowohl bei einem echten Klick als auch beim initialen
Mount mit `releasesMode==='all'` aus der URL.

---

### 17.5 „All expected tags are present" wird fälschlich für nicht heruntergeladene/fehlende Tracks angezeigt

Root Cause in `core/library2/status.py::compute_metadata_gaps` (Zeilen 78-118):

```python
if not file_row or not file_row.get("path"):
    return []   # ← "keine Lücken" statt "kann nicht geprüft werden"
```

Ein Track ohne Datei (kein `file_row`, kein `path`) bekommt eine **leere**
Gap-Liste zurück — das Frontend interpretiert `metadata_gaps.length === 0` als
„tags ✓ / All expected metadata tags present" (`library-v2-page.tsx:3153-3156`),
obwohl schlicht nichts geprüft werden konnte, weil keine Datei existiert. Diese
Kollision von „nichts fehlt" und „nichts prüfbar" ist der Bug.

**Fix-Richtung:**
1. `compute_metadata_gaps` (oder der Aufrufer in `core/library2/queries.py:633`)
   muss den „keine Datei"-Fall separat signalisieren (z.B. `None` statt `[]`
   zurückgeben, oder ein zusätzliches `has_file`-Flag), damit das Frontend zwischen
   „✓ vollständig" und „— nicht heruntergeladen" unterscheiden kann. Der
   File-Status ist ohnehin schon separat verfügbar (`file_status()`, gleiche
   Datei, Zeile 121-133) — das Frontend könnte ersatzweise einfach zusätzlich auf
   `track.file_status === 'missing'` prüfen, bevor es den „tags ✓"-Badge zeigt.
2. **Zusätzlicher Nutzerwunsch:** Hover-Tooltip soll auch im OK-Fall die erwarteten
   Tags auflisten (aktuell nur der Lücken-Fall tut das, `:3160`:
   `title={\`Missing: ${track.metadata_gaps.join(', ')}\`}`). Der OK-Fall hat nur den
   statischen String `"All expected metadata tags present"` (`:3154`) ohne Aufzählung.
   Fix: Tooltip im OK-Fall auf `` `Present: ${EXPECTED_TAGS.join(', ')}` `` o.ä.
   erweitern (Liste kommt aus `core/library2/status.py:17-20`,
   `EXPECTED_TAGS`, muss dem Frontend mitgegeben oder dort dupliziert werden).

---

### 17.6 Import-Performance bei Tausenden Songs (Research-Agent-Audit)

Zwei unterschiedliche Importpfade, unterschiedlich betroffen:

**Pfad A — Legacy→lib2-Migration** (`import_legacy_library`,
`core/library2/importer.py:562-936`, `POST /api/library/v2/import`,
`api/library_v2.py:2738-2789`): Der eigentliche Zeilen-Kopiervorgang ist reines
SQLite-zu-SQLite (`_pick()`, `importer.py:106-115`) — **keine einzige
Netzwerk-Anfrage** in dieser Kernfunktion; bereits bekannte Provider-IDs werden
wiederverwendet, nicht neu abgefragt (`importer.py:641-656`, `:762-768`). Läuft
bereits im Hintergrund-Thread mit Poll-Status (`GET
/api/library/v2/import/status`, `_import_state`-Dict alle 200 Zeilen
aktualisiert, `importer.py:662,774,899`) statt Websocket-Push — für die
Nutzer-Wahrnehmung "hängt scheinbar" ist das ok, solange das UI tatsächlich pollt.

**Der eigentliche Flaschenhals:** derselbe Hintergrund-Thread hängt danach zwei
weitere Stufen an, beide **seriell, ohne Concurrency**:
- `precache_tracklists` (`completeness.py:456-487`) — nur für „partielle" Alben,
  sollte bei einer voll besessenen Bibliothek meist übersprungen werden.
- `precache_all_artwork` (`core/library2/artwork.py:289-320`) — iteriert **jeden**
  Artist/jedes Album einzeln (`:307-316`); bei einer Erstmigration ist noch nichts
  gecacht, jedes Cover ohne brauchbares Embedded-Artwork löst einen synchronen
  Netzwerk-Call aus (`_provider_art_url`, `:125-204`) — **ein Request nach dem
  anderen, ein Thread**. Das ist der wahrscheinlichste Grund für „dauert ewig" bei
  Tausenden Tracks.

Kein `time.sleep`/Rate-Limit in importer.py/completeness.py/artwork.py — die
Langsamkeit kommt rein aus fehlender Parallelität, nicht aus bewusstem Throttling.

**Pfad B — Erstimport aus Staging-Ordner** (`core/auto_import_worker.py`) ist
bereits vernünftig gebaut: `ThreadPoolExecutor(max_workers=3)`
(`:186-201,393`), günstige Identifikations-Strategien zuerst (Tags → Ordnername →
erst dann AcoustID-Fingerprinting), AcoustID-Rate-Limit (`time.sleep(1)`, `:1326`)
ist bewusst und lokal begrenzt (max. 3 Files/Kandidat).

**Fix-Richtung:** `precache_all_artwork` und `precache_tracklists` mit einem
kleinen `ThreadPoolExecutor` parallelisieren — exakt das bereits in
`auto_import_worker.py` etablierte Muster (`max_workers=3`, Config-Key
`auto_import.max_workers`) wiederverwenden. Für eine Migration von Tausenden
Items zusätzlich erwägen, diese zwei Stufen als niedrig priorisierten
Hintergrund-Job zu queuen, auf den die UI nicht wartet — `lib2_*`-Zeilen sind
auch ohne gecachtes Artwork bereits voll nutzbar.

---

### 17.7 Importer verliert Metadaten gegenüber Legacy (Research-Agent-Audit, über §16.3(b) hinaus)

§16.3(b) hat bereits das Spotify-Hardcoding bei Provider-IDs auf Artist-/Album-Ebene
gefixt. Der Audit fand **weitere, andersartige** Lücken — Felder, die in der Legacy-DB
existieren, aber in KEINER lib2-Spalte landen (echter Datenverlust, nicht nur
fehlende Edit-UI wie in §48 bereits dokumentiert):

- **Artists** (`upsert_legacy`, `importer.py:320-362`): kopiert nur
  `name/sort_name/spotify_id/musicbrainz_id/external_ids/image_url/genres/summary`.
  `lib2_artists` (`schema.py:47-65`) hat **keine Spalte** für `style`, `mood`,
  `label`, `aliases`, `banner_url`, oder jegliche Last.fm-/Genius-/Discogs-
  Anreicherungsfelder (Bio-Text, Listeners, Similar-Artists, Tags).
- **Albums** (`fields`-Tupel, `importer.py:707-713`): `_merge_album_external_ids`
  (`:138-164`) deckt nur Spotify/Deezer/MusicBrainz/Tidal/Qobuz ab — iTunes/AudioDB/
  Discogs/Amazon/JioSaavn/Bandcamp-IDs fehlen (gleiche Klasse Bug wie das bereits
  gefixte Spotify-Hardcoding, nur nicht auf die volle Provider-Liste gezogen).
  Keine Spalte für `explicit`, `label`, **`upc`** (Barcode — echter
  Identifier-Verlust, analog zu `isrc` bei Tracks), `style`, `mood`.
- **Tracks — die tiefste Lücke:** `lib2_tracks` (`schema.py:127-149`) hat **gar
  keine `external_ids`-Spalte** (im Gegensatz zu Artists/Albums). `tfields`
  (`importer.py:810-815`) kopiert nur `isrc/musicbrainz_id/spotify_id` — Deezer-,
  Tidal-, Qobuz-, iTunes-, AudioDB-, Genius-, Amazon-, JioSaavn-, Bandcamp-Track-IDs
  sind auf Track-Ebene in lib2 nicht abbildbar. Ebenfalls ohne Zielspalte
  verloren: `bpm`, `explicit`, `style`, `mood`, `copyright`, **`genius_lyrics`**
  (echter Songtext!), Last.fm-Statistiken, sowie **`play_count`/`last_played`**
  (Hörstatistik). Zusätzlich: das per-Track `quality_profile_id` aus der Legacy-Zeile
  wird nie gelesen — neue lib2-Tracks bekommen immer `default_profile_id`
  (`importer.py:826-833`), eine in Legacy manuell gesetzte Profil-Zuordnung wird
  beim Import stillschweigend verworfen.

**Wichtiger Unterschied zu §48:** §48 dokumentiert fehlende Edit-UI für Felder wie
BPM/Style/Mood — die Empfehlung dort war, sie ins bestehende
`lib2_metadata_overrides`-Overlay-System aufzunehmen. Dieser Audit zeigt: für die
meisten dieser Felder gibt es noch **gar keinen Rohwert**, den man overlayen
könnte — der Importer muss zuerst neue Zielspalten bekommen, bevor ein
Override-System dort überhaupt etwas zu überschreiben hätte.

**Fix-Richtung (höchster Impact zuerst):**
1. `lib2_tracks.external_ids` (neue Spalte, analog zu Artists/Albums) +
   `_merge_track_external_ids`-Helper (Vorbild: `_merge_album_external_ids`,
   `importer.py:138-164`), aufgerufen beim Track-Insert/Update
   (`importer.py:816-834`) mit allen 9 Provider-IDs via `_pick`.
2. `bpm`, `explicit` auf `lib2_tracks`; `explicit`, `label`, `upc` auf
   `lib2_albums` — `tfields`/`fields`-Tupel entsprechend erweitern
   (`importer.py:810-815` bzw. `:707-713`).
3. `_merge_album_external_ids`s ID-Dict (`importer.py:762-768`) um
   itunes/audiodb/discogs/amazon/jiosaavn/bandcamp erweitern (analog zum bereits
   vorhandenen Artist-Pattern bei `:650-656`).

---

### 17.8 „Write All Tags" — Status-Check (Research-Agent-Audit)

**Nicht fehlend, in beiden UIs vorhanden und funktional verdrahtet:**
- **Legacy:** `_build_library_tag_db_data` (`web_server.py:11874`) baut den
  Payload, Endpoints `/api/library/track/<id>/write-tags` +
  `/tracks/write-tags-batch(/status)`, UI in `webui/static/library.js:7097-7463`.
- **Library v2:** `core/library2/retag.py::write_tags` (`:195-261`), Endpoint
  `POST /api/library/v2/tags/write` (`api/library_v2.py:2629-2681`,
  Hintergrund-Job via `_job_registry`), UI: „Library Retag"
  (`library-v2-page.tsx:1331-1333`) + „Preview Retag" pro Artist/Album
  (`:2533-2537`, `:2681`) — im Live-Screenshot dieser Session als Toolbar-Button
  sichtbar bestätigt.

**Aber:** begrenzt durch §17.7 — `_db_data_for_row` (`retag.py:91-113`) kann nur
Felder emittieren, die überhaupt in `lib2_tracks` existieren
(`title/artist_name/track_artist/album_title/year/release_date/genres/
track_number/disc_number/track_count/spotify_track_id/
musicbrainz_recording_id`). Kein BPM, kein iTunes-Track-ID — nicht weil der
Tag-Writer das nicht könnte, sondern weil die DB den Wert nie bekommen hat. §42
(Preview Retag zeigt falsches „File not found") bleibt ein separater, bereits
dokumentierter Bug in diesem Bereich.

---

### Priorisierung Abschnitt 17

1. ~~**17.2 (Track-Nummer-Heilung für Bestandsalben)**~~ — Kritisch: sichtbarer
   Datenverlust, betrifft jeden Nutzer mit vor dem Fix importierten Alben, kein
   Workaround außer manuellem DB-Fix. **Gefixt, siehe Abschnitt 19.**
2. ~~**17.6 (Import-Performance)**~~ — Hoch: blockiert die Migration großer
   Bibliotheken praktisch nutzbar zu machen; Fix ist ein bekanntes, bereits im
   Code vorhandenes Muster (ThreadPoolExecutor), kein Neubau. **Gefixt, siehe
   Abschnitt 20.**
3. ~~**17.5 (falsches „tags ✓" bei fehlender Datei)**~~ — Hoch: irreführend,
   verdeckt fehlende Downloads als „vollständig". **Gefixt, identisch mit 18.8
   (siehe Abschnitt 18).**
4. ~~**17.4 (All-Releases-Tab lädt nicht)**~~ — Mittel: UX-Bug mit bekanntem
   Workaround (hin- und zurückklicken), kleine, gut lokalisierte Änderung.
   **Gefixt, siehe Abschnitt 21.**
5. ~~**17.3 (rohes ISO-Datum)**~~ — Mittel: kosmetisch, aber sichtbar bei jedem
   library-origin Release mit historisch unsauberem Legacy-Datum. **Gefixt,
   identisch mit 18.7 (siehe Abschnitt 18).**
6. **17.7 (Importer-Datenverlust)** — Mittel: größerer Scope (Schema-Änderungen),
   aber additiv/risikoarm; kein akuter Datenverlust im Sinne von „Datei nicht
   auffindbar", sondern „Metadaten dauerhaft nicht verfügbar". **Fix-Richtung
   (1)–(3) gefixt, siehe Abschnitt 22; Artist-Anreicherung/Hörstatistik/
   per-Track-Profil bleiben offen.**
7. **17.1 / 17.8** — Kein Implementierungsbedarf, nur zur Kenntnisnahme.
8. **17.0 (Dev-Umgebung)** — Bereits behoben für diese Session; als Prozess-Hinweis
   für künftige Sessions/den Nutzer festgehalten, keine Code-Änderung nötig.

**Status:** Reine Dokumentation + Live-Verifikation (DB-Queries, API-Calls,
Playwright-Screenshots, zwei Research-Agents), keine Implementierung in dieser
Session außer dem Dev-Umgebungs-Neustart (17.0). Nächster Schritt: mit dem Nutzer
Priorität bestätigen, dann jeder Punkt als eigene TDD-Slice wie bei den bisherigen
Roadmap-Punkten.

---

## 18. Gewünschte Erweiterungen & Feature-Roadmap (Vom Nutzer angefordert — 2026-07-15)

In der Session vom 15.07.2026 hat der Nutzer eine konkrete Liste an UI-Verbesserungen und funktionalen Erweiterungen für die Library v2 vorgegeben. Diese Anforderungen dienen dazu, die Detailtiefe des Legacy-Download-Inspektors (Quarantäne/History) direkt in die Library v2 zu übertragen und die Darstellung konsistenter zu gestalten.

### 18.1 Live-Inspektor für Tags & Lyrics im Track-Edit-Modal
- **Problem/Wunsch:** Klickt man in Library v2 auf das Stift-Icon (Edit) eines Tracks, soll man analog zum Quarantäne-„Inspect“-Modal die tatsächlich eingebetteten Tags und Songtexte (Lyrics) live aus der Datei auslesen und anzeigen können.
- **Backend-Implementierung:**
  - Route in `api/library_v2.py`: `GET /api/library/v2/tracks/<int:track_id>/file-tags`
  - Ablauf: Pfad des aktiven Files über `lib2_track_files` holen, mit `paths.resolve_lib2_path` auflösen, `core.tag_writer.read_file_tags` aufrufen und die uniformen Tags zurückgeben.
- **Frontend-Implementierung:**
  - In `library-v2-page.tsx` das `TrackDetailModal` um zwei Tabs erweitern: „Tags“ (zeigt das Grid mit den freundlichen Labels aus `_AUDIT_TAG_LABELS` an) und „Lyrics“ (rendert den LRC/Lyrics-Text über einen analog zu `_renderLyricsBody` gebauten Container).

### 18.2 Manueller Schreibvorgang ("Write Tags to File")
- **Problem/Wunsch:** Es soll eine manuelle Funktion geben, um die Metadaten der Datenbank direkt wieder in die physische Musikdatei zu schreiben (analog zum Legacy-Feature `col-writetag`).
- **Backend-Implementierung:**
  - Route in `api/library_v2.py`: `POST /api/library/v2/tracks/<int:track_id>/write-tags`
  - Ablauf: Ruft im Hintergrund (oder direkt, da Single-Track-Operation) `core.library2.retag.py::write_tags` für die entsprechende Datei auf und invalidiert den Cache.
- **Frontend-Implementierung:**
  - Im „Metadata“-Tab des `TrackDetailModal` wird ein Button „Write Tags to File“ (mit Lade-Spinner und Toast-Meldung) integriert.

### 18.3 Detaillierter Lifecycle-Log / Prüfungs-Historie (Info-Tab)
- **Problem/Wunsch:** Der Nutzer möchte genau nachvollziehen können, welche Checks (AcoustID, Qualität etc.) die Datei durchlaufen hat, ob sie je in der Quarantäne war, warum und welche Schritte manuell übersprungen wurden.
- **Backend-Implementierung:**
  - Über `/api/library/v2/tracks/<int:track_id>/source-info` werden die Historien- und Provenienz-Einträge der Downloads geliefert.
- **Frontend-Implementierung:**
  - Das `TrackInfoPanel` wird um eine übersichtliche Visualisierung (z. B. einen Stepper oder Lifecycle-Einträge) erweitert. Diese zeigt:
    - Ob die Datei in der Quarantäne war und warum (`quarantine_reason`).
    - Ob Bypasses vorgenommen wurden (z. B. `force_imported` wg. Version-Mismatch oder `skip_acoustid`).
    - Das AcoustID-Ergebnis (`pass`, `skip` oder `fail`).

### 18.4 ReplayGain-Präsenz darstellen
- **Problem/Wunsch:** Anzeige, ob für den Track eine ReplayGain-Analyse durchgeführt wurde und entsprechende Tags vorliegen.
- **Implementierung:**
  - Die ReplayGain-Tags (Track Gain, Track Peak, Album Gain, Album Peak) werden im neuen „Tags“-Tab des Track-Edit-Modals aufgeführt.
  - Optional wird in der Spalte „Quality“ oder als Tooltip ein kleiner Indikator (z. B. `RG`) eingeblendet.

### 18.5 Kompaktes, zusammenhängendes Qualitäts-Badge (Cohesive Badge)
- **Problem/Wunsch:** Auf der Downloads-Seite werden Format, Bit-Tiefe und Frequenz in einem gemeinsamen Badge dargestellt (z. B. `FLAC 16-bit 44.1kHz`). In der Library v2 sind dies momentan drei separate Badges. Die Bitrate (kbps) soll jedoch ein eigenständiges Badge bleiben.
- **Frontend-Implementierung:**
  - Anpassung der `QualityDisplay`-Komponente in `library-v2-page.tsx`. Die Werte für Format, Bittiefe und Sample-Rate werden in einem einzigen `<span className={styles.qualityTag}>` zusammengefasst (z. B. mit Trennzeichen `·`), während die Bitrate als separates Badge gerendert wird.

### 18.6 Hover-Tooltip für Metadaten-Status (Tag-Details)
- **Problem/Wunsch:** Beim Bewegen der Maus über `tags ✓` oder `X tag gaps` soll eine Liste aller in der Datei vorhandenen Tags angezeigt werden.
- **Frontend-Implementierung:**
  - In `TrackRow` wird dem Metadaten-Status-Span ein `title`-Attribut mitgegeben, das basierend auf `track.file.tags_json` (bzw. den gaps) die vorhandenen und fehlenden Tags auflistet (z. B. *Present: Title, Artist, Album... / Missing: Genre*).

### 18.7 Normalisierung der Release-Datumsangaben
- **Problem/Wunsch:** Manche Releasetermine (besonders bei library-Herkunft) enthalten unerwünschte Zeitstempel (z. B. `1982-11-29T08:00:00Z` oder `1994-06-21 00:00:00`). Es soll nur das reine Datum (`YYYY-MM-DD`) oder das Jahr angezeigt werden.
- **Frontend-Implementierung:**
  - Eine Hilfsfunktion `formatReleaseDate(value: string | null): string` in `library-v2-page.tsx` einbauen, die das Datum sauber abschneidet (z. B. `value.slice(0, 10)`), um einheitliche Datumsanzeigen zu gewährleisten.

### 18.8 Korrektur: Metadaten-Status bei missing Tracks
- **Problem/Wunsch:** Tracks, die als fehlend (`missing`, rot markiert) eingetragen sind, zeigen fälschlicherweise den Status `tags ✓` (oder `0 tag gaps`), obwohl gar keine Datei vorliegt.
- **Frontend-Implementierung:**
  - In der `TrackRow` der Tabellenzelle für Metadaten die Abfrage erweitern: Nur wenn `track.id` vorhanden ist **UND** der Track nicht missing ist (`!missing`), wird der Tag-Prüfungsstatus gerendert. Andernfalls wird `—` (dash) angezeigt.

### Umsetzungsstand (2026-07-15, Fortsetzungs-Session)

Alle acht Punkte (18.1–18.8) sind implementiert, no-Docker-verifiziert (`npm run check`, `vitest run`, gezielte `pytest`-Läufe) und noch nicht committed:

- **18.1 (Live Tags/Lyrics):** Neue Route `GET /api/library/v2/tracks/<id>/file-tags` (`api/library_v2.py`) — löst die primäre Datei über `lib2_track_files`/`resolve_lib2_path` auf und liest sie mit `core.library.file_tags.read_embedded_tags` (nicht `tag_writer.read_file_tags` — dieser Reader ist bereits die Grundlage des Legacy-Audit-Trail-Modals und liefert Lyrics + das volle Tag-Set inkl. ReplayGain mit). `TrackDetailModal` bekommt zwei neue Tabs „Tags" (kategorisiertes Grid: Track/Album/ReplayGain/Source-IDs/Other, ein einziger Live-Read für beide Tabs) und „Lyrics" (Klartext-Render mit Zeilenumbrüchen).
- **18.2 (Write Tags to File):** Kein neuer Endpunkt nötig — der bereits vorhandene Bulk-Endpunkt `POST /api/library/v2/tags/write` (den `RetagModal` für Album/Artist nutzt) wird jetzt auch von einem neuen `TrackWriteTagsButton` im Metadata-Tab mit `track_ids: [id]` aufgerufen, inkl. Job-Polling über den bestehenden `awaitBulkJob`-Helper.
- **18.3 (Lifecycle-Log):** `quarantine_reason` existiert nur als Sidecar-Datei während der Quarantäne selbst und wird nach dem Import nicht persistiert — daher stützt sich der Info-Tab stattdessen auf zwei bereits vorhandene, aber bisher ungenutzte DB-Facts: `lib2_track_files.verification_status` (bereits über die existierende `TrackVerificationBadge`-Komponente mit guten Tooltips abgedeckt, jetzt auch im Info-Tab gerendert) und die `lib2_manual_skips`-Audit-Tabelle (neue Funktion `skip_history_for_path` in `core/library2/manual_skips.py`, verdrahtet in die `/source-info`-Route als zusätzliches `manual_skips`-Feld). `lib2_track_files.acoustid_status` existiert zwar im Schema, wird aber im gesamten Code nie beschrieben (immer `NULL`) — bewusst NICHT verdrahtet, um keine tote UI zu bauen.
- **18.4 (ReplayGain-Präsenz):** Durch 18.1 abgedeckt — die vier ReplayGain-Tags erscheinen automatisch im neuen Tags-Tab. Der optionale „RG"-Badge in der Quality-Spalte wurde bewusst ausgelassen: er bräuchte einen zusätzlichen Cache-Fetch pro Zeile (oder eine Erweiterung von `tags_json`/`metadata_gaps` um RG-Präsenz), was für ein als „optional" markiertes Nice-to-have nicht im Verhältnis stand.
- **18.5 (Cohesive Badge):** `QualityDisplay` fasst Format+Auflösung jetzt in einem `qualityTag`-Span zusammen (`·`-getrennt), kbps bleibt eigenes Badge.
- **18.6 (Hover-Tooltip):** Neue `metadataGapsTooltip()`-Hilfsfunktion (mirrored `EXPECTED_TAGS`-Reihenfolge aus `core/library2/status.py`) liefert „Present: … / Missing: …" als `title` für beide Zustände (✓ und N gaps).
- **18.7 (Datums-Normalisierung):** `formatReleaseDate()` schneidet auf die ersten 10 Zeichen; angewendet auf Album-Subtitle und Album-Head-Datum-Badge.
- **18.8 (Missing-Track-Tags-Fix):** War bereits in einem vorherigen Commit (`b1e49b0b`) umgesetzt — verifiziert, keine Änderung nötig.

Reuse-Bilanz: kein einziger neuer Backend-Endpunkt für 18.2 (bestehende Bulk-Route reicht); 18.1 nutzt den bereits für den Legacy-Audit-Trail gebauten `read_embedded_tags`-Reader 1:1 statt eines neuen Parsers; 18.3 fördert zwei DB-Spalten zutage, die bereits existierten aber nirgends im UI ankamen, statt neue Tracking-Logik zu bauen.

---

## 19. §17.2 Track-Nummer-Kollision auf Bestandsalben — gefixt (2026-07-15, Fortsetzungs-Session 2)

TDD, `pytest tests/library2` 408/408 grün (war 404). Zwei Teil-Fixes, beide nötig,
damit der live-reproduzierte SWAG-Fall tatsächlich heilt:

- **Invocation-Gap (der in §17.2 dokumentierte Root Cause):** neue Funktion
  `core/library2/discography.py::repair_track_number_collisions()` findet
  `origin='library'`-Alben des Artists mit einer `(disc_number, track_number)`-
  Kollision (billige `EXISTS(...GROUP BY...HAVING COUNT(*)>1)`-Subquery) und
  ruft für sie direkt `resolve_tracklist` auf — **nicht** über
  `auto_monitor_releases`, denn das würde zusätzlich alle Tracks monitored=1
  setzen und eine `new_release`-Provenance-Regel stempeln, was bei einem
  bereits vorhandenen Album falsch wäre. Verdrahtet in
  `refresh_artist_discography()` (läuft also sowohl über den "Update
  Discography"-Button als auch über den wöchentlichen
  `lib2_discography_refresh`-Repair-Job, ohne Code-Duplikation). Neues Stats-
  Feld `repaired_track_number_collisions` (Liste im Backend, Anzahl in der
  API-Response).
- **Zweiter, beim Root-Cause-Audit nicht erkannter Gap in der Heilungslogik
  selbst:** `_unique_untouched_title_match` (§16.3, `eca36caa`) verlangt EINE
  eindeutige Titel-Übereinstimmung. Genau der vom Nutzer gemeldete Fall
  ("DAISIES bei Nummer 1 UND Nummer 2") erzeugt aber ZWEI Kandidaten mit
  demselben Titel: die echte, heruntergeladene Datei (falsch bei Nummer 1) UND
  einen fileless Platzhalter, der von einem früheren `resolve_tracklist`-Lauf
  bereits an der korrekten Nummer angelegt wurde. Die alte Eindeutigkeitsregel
  gab in diesem Fall `None` zurück (ambig) → die echte Zeile blieb korrupt,
  der Platzhalter blieb als sichtbares Duplikat stehen. Fix: bei mehreren
  Titel-Kandidaten wird jetzt geprüft, ob GENAU EINER eine Datei hat
  (`lib2_track_files`) und alle übrigen gefahrlos löschbare Platzhalter sind
  (kein `legacy_track_id`, nicht monitored, keine positive Monitor-Regel,
  nicht wanted — dieselben Kriterien wie `_trim_excess_fileless_tracks`); dann
  wird die echte Zeile geheilt (umnummeriert) und der/die Platzhalter gelöscht
  (neuer gemeinsamer Helper `_delete_track_row`). Ohne diesen zweiten Fix hätte
  die reine Invocation-Reparatur den gemeldeten Fall NICHT tatsächlich gelöst.
- Tests: `test_persist_tracklist_heals_real_track_over_its_own_placeholder_duplicate`
  (test_completeness.py) für die Matching-Lücke;
  `test_refresh_repairs_track_number_collision_on_existing_library_album` +
  `test_refresh_track_number_repair_does_not_touch_clean_library_albums` +
  `test_refresh_track_number_repair_does_not_remonitor_or_reprovenance`
  (test_discography.py) für Erkennung/Verdrahtung + die bewusste Abgrenzung
  zu `auto_monitor_releases`'s Nebenwirkungen.
- Nicht angefasst: die eigentliche Live-DB-Verifikation gegen die reale SWAG-
  Album-Instanz des Nutzers (siehe [[local-realdb-verify-workflow]]) — diese
  Session hat nur gegen die synthetische Test-DB verifiziert.

---

## 20. §17.6 Import-Performance — Artwork-/Tracklist-Precache parallelisiert (2026-07-15, Fortsetzungs-Session 3)

TDD, `pytest tests/library2` 413/413 grün (war 408). Genau der in §17.6 vorgeschlagene
Fix: der bereits etablierte `ThreadPoolExecutor`-Pattern aus
`core/auto_import_worker.py` (Config-Key `auto_import.max_workers`, Default 3)
wiederverwendet statt neu gebaut.

- **`core/library2/artwork.py::precache_all_artwork`**: liest zunächst nur
  `artist_ids`/`album_ids` über eine kurzlebige Verbindung, schließt sie, baut
  daraus die Liste der noch nicht gecachten Einträge (`artwork_file(...).exists()`-
  Check bleibt im Hauptthread, ist reiner Filesystem-Stat) und verteilt genau
  diese Pending-Liste über einen `ThreadPoolExecutor` an `build_artwork` — jeder
  Pool-Worker öffnet dafür seine **eigene** `database._get_connection()`
  (Kommentar am Original bestätigt bereits "Get a NEW database connection for
  each operation (thread-safe)", `database/music_database.py:239`), da
  `sqlite3.Connection`-Objekte nicht threadübergreifend geteilt werden dürfen.
  Fortschritts-Callback (`progress(...)`) bleibt erhalten, jetzt hinter einem
  `threading.Lock` um den gemeinsamen Zähler. Zählung pro Art (`artists`/
  `albums`) unverändert.
- **`core/library2/completeness.py::precache_tracklists`**: gleiches Muster,
  neuer gemeinsamer Helper `_resolve_stage()` führt jede der beiden bestehenden
  Phasen (erst `cached=True` ohne Provider-Calls, dann `cached=False` mit
  Provider-Calls) als eigenen bounded-Pool-Durchlauf aus — jeder Worker ruft
  `resolve_tracklist(config_manager, thread_conn, album_id)` mit einer eigenen
  Verbindung auf. Reihenfolge der beiden Phasen (erst Cache, dann Provider)
  bleibt erhalten, nur die Parallelität innerhalb jeder Phase ist neu.
- Neuer gemeinsamer (aber pro Modul dupliziert, um keine Cross-Import-Kopplung
  zwischen `artwork.py` und `completeness.py` einzuführen) 8-Zeilen-Helper
  `_precache_max_workers(config_manager, default=3)` — liest denselben
  Config-Key wie `AutoImportWorker`, floort bei 1, fällt bei fehlendem/kaputtem
  `config_manager` sauber auf 3 zurück.
- Aufrufer (`api/library_v2.py:2836,2843`, der bereits vorhandene
  Import-Hintergrundthread) unverändert — Signatur beider Funktionen ist
  identisch geblieben, kein API-Bruch.
- Tests (neu, beide Module nach demselben Muster wie
  `tests/imports/test_auto_import_executor.py::test_pool_runs_candidates_in_parallel`
  / `test_executor_max_workers_caps_concurrency`): mit einem
  Sperr-geschützten In-Flight-Zähler + `threading.Event` wird bewiesen, dass
  (a) mit mehr Pending-Items als `max_workers` die Spitzenparallelität exakt
  `max_workers` erreicht (Default 3) — belegt, dass die alte serielle Schleife
  tatsächlich seriell WAR (RED-Phase schlug mit Peak=1 fehl), und (b) ein
  konfigurierter `auto_import.max_workers=2` die Parallelität hart deckelt.
  Zusätzlich ein reiner Korrektheitstest für `precache_all_artwork`, der
  beweist, dass ein bereits gecachtes Element übersprungen (nicht neu gebaut)
  wird und die Kind-Zähler (`artists`/`albums`) nach dem Wechsel auf den Pool
  weiterhin exakt stimmen. Neue Fixture `legacy_db_factory` (in
  `tests/library2/conftest.py`) erzeugt eine Legacy-DB mit N Alben unter einem
  Artist — genug unabhängige Arbeitseinheiten, um echte Nebenläufigkeit statt
  zufälligem Timing zu beweisen; `LegacyDBShim` bekam zusätzlich eine
  `database_path`-Property (Alias auf `.path`), damit `artwork.py`s
  Pfad-Helper auch gegen den Test-Shim funktionieren.
- Nicht angefasst: `precache_tracklists`s `cached=True`-Phase wird in den neuen
  Tests nur strukturell (über eine gemockte `resolve_tracklist`) auf
  Nebenläufigkeit geprüft, nicht mit einer echten JSON-Cache-Fixture — die
  bereits bestehenden `resolve_tracklist`-Tests decken die inhaltliche Logik
  dieser Phase weiterhin ab und liefen unverändert grün. Der zweite Teil von
  §17.6 (Vorschlag, die beiden Precache-Stufen als niedrig priorisierten
  Hintergrund-Job zu queuen statt den Import darauf warten zu lassen) wurde
  NICHT umgesetzt — bewusst zurückgestellt, da bereits Hintergrundthread +
  jetzt Parallelität den ursprünglich gemeldeten "dauert ewig"-Fall adressieren
  sollten; bei Bedarf (Bibliotheken mit vielen Tausend Alben) eigenständig
  nachrüstbar.

---

## 21. §17.4 "All Releases"-Tab lädt nicht automatisch — gefixt (2026-07-15, Fortsetzungs-Session 3)

TDD, `vitest run` 149/149 grün (war 144), `npm run check` (oxfmt+oxlint --type-check)
sauber. Exakt der in §17.4 vorgeschlagene Fix: die Fetch-Bedingung aus dem
Klick-Handler in einen `useEffect` verschoben, der auch beim initialen Mount
mit `releasesMode==='all'` aus der URL feuert.

- **Neue reine Entscheidungsfunktion** `shouldAutoFetchDiscography()`
  (`library-v2-page.tsx`, direkt neben `visibleReleases`, `export`iert für
  Tests) kapselt die Logik isoliert von React/Router — testbar ohne die große
  `ArtistDetailView`-Komponente zu mounten (die `useNavigate`/
  `Route.useSearch()` aus TanStack Router braucht).
- **Wichtiger, in der ursprünglichen §17.4-Analyse nicht erwähnter Fallstrick:**
  eine naive Umsetzung der vorgeschlagenen Dependency-Liste
  `[releasesMode, artist?.discography_count, discographyBusy]` hätte für einen
  Artist mit einer *echt leeren* Provider-Discography (Count bleibt nach dem
  Fetch bei 0) eine Endlosschleife erzeugt: jeder `discographyBusy`-Übergang
  `true→false` erfüllt die Bedingung erneut, ruft `updateDiscography()` erneut
  auf, setzt `discographyBusy` erneut auf `true`, usw. Fix: `alreadyAttempted`-
  Parameter (gespeichert in einem `useRef`, das beim Verlassen von
  `releasesMode==='all'` zurückgesetzt wird) — genau ein Fetch-Versuch pro
  Tab-Wechsel zu "All Releases", nicht pro Render.
- `setReleasesMode()` vereinfacht auf reines Navigieren; der `useEffect`
  besitzt jetzt die alleinige Fetch-Entscheidung für BEIDE Auslöser (Klick und
  Mount-mit-URL-State).
- Tests: 5 neue Fälle in `webui/src/routes/library-v2/-ui/releases-mode.test.ts` (lädt/
  wartet/hat schon Daten/läuft bereits/Endlosschleifen-Regressionswächter) für
  die reine Funktion — deckt exakt den Bug UND den beim Entwurf gefundenen
  Zweitbug in einem Rutsch ab.
- **Live-Verifikation gegen die echte Dev-DB (no Docker, `dev.py`,
  Playwright+CDP gegen headless Chromium, siehe [[local-realdb-verify-workflow]]-
  Pattern):** direkter Aufruf von `/library-v2?artist=<id>&releases=all` (kein
  Klick!) für zwei Artists mit `discography_count===0`:
  - Artist 24 (Justin Bieber, "SWAG" — der in §17.2/§19 bereits als Repro-Fall
    genutzte Artist): Auto-Fetch feuerte beim Mount, `discography_count` ging
    von 0 → 55, UI zeigte danach "All Releases 55" mit vollem Albenkatalog.
  - Artist 23 (VØJ): Netzwerk-Log bestätigt **genau ein** POST auf
    `/api/library/v2/artists/23/discography/refresh` in einem 6-Sekunden-
    Beobachtungsfenster (kein Loop), `discography_count` ging von 0 → 93.
  - Keine Konsolenfehler durch die Änderung (ein einzelner unabhängiger 404 zu
    `docs.brandfetch.com/logo-api/overview` ist ein bereits vorhandener
    externer Artist-Image-Fallback, nicht durch diese Änderung verursacht).

---

## 22. §17.7 Importer-Datenverlust — Fix-Richtung (1)-(3) umgesetzt (2026-07-15, Fortsetzungs-Session 4)

TDD, `pytest tests/library2` 417/417 grün (war 413, +4 neue Regressionstests).
Genau die drei in §17.7 vorgeschlagenen Schritte, in Prioritätsreihenfolge:

- **Schritt 1 — `lib2_tracks.external_ids`:** neue Spalte (`core/library2/schema.py`,
  `LIB2_TRACKS_DDL` + `_ADDED_COLUMNS`-Migrationseintrag für bestehende
  Installationen), analog zu `lib2_artists`/`lib2_albums`. `isrc`/
  `musicbrainz_id`/`spotify_id` behalten ihre eigenen Spalten; `external_ids`
  trägt jetzt den Long Tail (Deezer/Tidal/Qobuz/iTunes/AudioDB/Genius/Amazon/
  JioSaavn/Bandcamp/Last.fm).
- **Schritt 2 — `bpm`/`explicit` (Tracks), `explicit`/`label`/`upc` (Albums):**
  neue Spalten (gleiches DDL+Migration-Muster), `tfields`/`fields`-Tupel und die
  zugehörigen INSERT/UPDATE-Statements in `import_legacy_library`
  (`core/library2/importer.py`) entsprechend erweitert. Die UPDATE-Zweige
  nutzen `COALESCE(?, spalte)` (wie bereits bei `spotify_id`/`image_url`), damit
  ein Re-Import von einer DB ohne diese Legacy-Spalten einen zuvor gesetzten
  Wert nicht mit `NULL` überschreibt.
- **Schritt 3 — Long-Tail-Provider-IDs für Alben:** `_merge_album_external_ids`s
  Dict um iTunes/AudioDB/Discogs/Amazon/JioSaavn/Bandcamp erweitert.
- **Über den Plan hinaus (Reuse statt manuellem Nacherfinden):** Schritt 1 und 3
  hätten laut Audit ~9 einzelne `_pick`-Paare pro Entity gebraucht — genau das
  Muster, das schon zweimal (Artist- und Album-Provider-IDs) von Hand gepflegt
  wird. Stattdessen nutzt ein neuer Helper `_extra_provider_ids(row, entity_type,
  exclude)` die bereits existierende, geprüfte Service→Spalten-Tabelle
  `match_status.SERVICES` (dieselbe Tabelle, die die Provider-Chips in der UI
  speist) und liest daraus automatisch jede Spalte, die es für `'album'`/
  `'track'` gibt. Ein neuer Provider muss dadurch nur noch einmal (in
  `match_status.py`) eingetragen werden, nicht mehr zusätzlich im Importer.
  `_merge_album_external_ids`/eine neue `_merge_track_external_ids` sind jetzt
  dünne Wrapper um einen gemeinsamen `_merge_external_ids(cursor, table, id,
  ids)`-Helper (vorher war die Album-Variante eigenständiger, fast identischer
  Code) — reine Konsolidierung, kein Verhaltensunterschied für Alben.
- Alle Provider-Spaltennamen (`deezer_id`/`tidal_id`/`itunes_track_id`/
  `audiodb_id`/`genius_id`/`amazon_id`/`jiosaavn_id`/`bandcamp_url`/
  `lastfm_url` usw.) wurden gegen die echten Migrationsschritte in
  `database/music_database.py` verifiziert (nicht geraten) — exakt die Spalten,
  die `match_status.SERVICES` bereits referenziert.
- Tests (`tests/library2/test_importer.py`): `test_import_captures_track_
  provider_ids_into_external_ids` (alle 10 Long-Tail-Provider auf einem
  Track), `test_import_captures_track_bpm_and_explicit`,
  `test_import_captures_album_explicit_label_upc`,
  `test_import_captures_album_long_tail_provider_ids_into_external_ids`
  (iTunes/AudioDB/Discogs/Amazon/JioSaavn/Bandcamp auf einem Album). Migration-
  Pfad (ALTER auf eine simulierte alte Installation ohne die neuen Spalten) und
  Idempotenz (`ensure_library_v2_schema` zweimal aufgerufen) manuell gegen eine
  In-Memory-DB verifiziert, nicht Teil der `pytest`-Suite (kein bestehendes
  Testmuster dafür in `tests/library2` — die vorhandenen Migrationstests decken
  bereits andere Spalten mit demselben Mechanismus ab).
- **Nicht angefasst (bewusst außerhalb der 3 Fix-Richtung-Schritte, im Audit nur
  als weitere Lücken genannt, nicht priorisiert):** Artist-Anreicherungsfelder
  (`style`/`mood`/`label`/`aliases`/`banner_url`, Last.fm-/Genius-/Discogs-
  Bio/Listeners/Similar-Artists/Tags) haben weiterhin keine lib2-Zielspalte;
  `genius_lyrics` (echter Songtext), `copyright`, `play_count`/`last_played`
  (Hörstatistik) auf Tracks ebenso nicht; das per-Track `quality_profile_id`
  aus der Legacy-Zeile wird weiterhin nie gelesen (neue Tracks bekommen immer
  `default_profile_id`). Größerer Scope, eigene Priorisierung nötig — siehe
  §17.7-Originaltext für die vollständige Lückenliste.

### Priorisierung Abschnitt 17 — Update

Punkt 6 (17.7) ist damit **teilweise** gefixt (die drei benannten
Fix-Richtung-Schritte), nicht vollständig — die oben aufgeführten
Artist-Anreicherungs- und Hörstatistik-Felder bleiben offen. Verbleibend aus
der ursprünglichen Liste: **17.5** (falsches „tags ✓" bei fehlender Datei) und
**17.3** (rohes ISO-Datum) sind inhaltlich bereits durch **18.8** bzw. **18.7**
gefixt (identische Problembeschreibung, im Vorgänger-Abschnitt nur nicht als
„gefixt" markiert) — die Priorisierungsliste oben wurde entsprechend
nachgezogen.

---

## 23. §17.7 Importer-Datenverlust — Restliche Lücken geschlossen (2026-07-15, Fortsetzungs-Session 5)

TDD, `pytest tests/library2` 425/425 grün (war 417, +8 neue Regressionstests).
Schließt exakt die drei am Ende von §22 als „nicht angefasst" benannten
Lücken: Artist-Anreicherungsfelder, Track-Hörstatistik/-Lyrics, und das
per-Track `quality_profile_id` aus der Legacy-Zeile.

- **Artist-Anreicherung, Teil 1 — flache Felder:** `style`/`mood`/`label`
  (AudioDB-Herkunft), `aliases` (MusicBrainz, JSON-Array) und `banner_url`
  sind jetzt eigene `lib2_artists`-Spalten. `aliases` wird über den
  bestehenden `_normalize_genres()`-Helper normalisiert (JSON-Array-ODER-CSV-
  String → JSON-Array-String) — der ist bereits exakt diese Logik für
  `genres`, eine neue Funktion wäre reine Duplikation gewesen. Die UPDATE-
  Seite von `_ArtistResolver.upsert_legacy` nutzt für die vier Skalarfelder
  `COALESCE(?, spalte)` (wie `bpm`/`explicit` in §22), damit ein Re-Import von
  einer DB ohne diese Migrationsspalten einen zuvor gesetzten Wert nicht
  nullt. `aliases` folgt dagegen bewusst demselben Muster wie `genres` (reines
  Overwrite ohne COALESCE) — beides sind JSON-Array-Spiegelfelder derselben
  Legacy-Kategorie, kein neuer Sonderfall.
- **Artist-Anreicherung, Teil 2 — Last.fm/Genius/Discogs:** bio/listeners/
  tags/similar/url (Last.fm), description/alt_names/url (Genius), bio/
  members/urls (Discogs) landen in einer neuen `lib2_artists.enrichment`-
  JSON-Spalte, provider-verschachtelt (`{"lastfm": {...}, "genius": {...},
  "discogs": {...}}`) statt als ~11 einzelne Spalten — das sind Anreicherungs-
  *Inhalte* unterschiedlicher Quellen (eine Last.fm-Bio und eine Genius-
  Beschreibung sind verschiedener Text, kein Fall für eine gemeinsame Spalte
  wie bei den Provider-*IDs*). Neuer `_merge_artist_enrichment()`-Helper
  spiegelt die Nie-überschreiben-Semantik von `_merge_external_ids()`: pro
  Provider UND pro Feld wird nur befüllt, was noch nicht gesetzt ist, damit
  ein dünnerer Re-Import nie eine bereits eingefangene reichhaltigere Bio
  überschreibt. Es gibt in der echten Legacy-Spalte KEIN `genius_bio` — das
  Genius-Äquivalent heißt `genius_description` (per Grep gegen
  `database/music_database.py` verifiziert, nicht geraten).
- **Tracks — Hörstatistik/Lyrics:** `genius_lyrics` (echter Songtext, nicht zu
  verwechseln mit `genius_description` auf Artist-Ebene), `copyright`,
  `play_count`, `last_played` sind neue `lib2_tracks`-Spalten, `tfields`/UPDATE
  folgen demselben `COALESCE(?, spalte)`-Muster wie `bpm`/`explicit`.
  `play_count INTEGER NOT NULL DEFAULT 0` erzwingt beim INSERT einen
  expliziten Fallback auf `0` (der Schema-Default greift nur, wenn die Spalte
  im INSERT ausgelassen wird, nicht bei einem expliziten `NULL`) — die UPDATE-
  Seite nutzt weiterhin `COALESCE`, damit ein Re-Import ohne Legacy-Wert einen
  bereits akkumulierten Zähler nicht auf 0 zurücksetzt.
- **Per-Track `quality_profile_id`:** wurde laut Audit „weiterhin nie
  gelesen" — neue Tracks bekamen ausschließlich das lauf-weite
  `default_profile_id`. Fix beschränkt sich bewusst auf den INSERT-Zweig
  (neue Tracks); der UPDATE-Zweig (bestehende Tracks) bleibt unverändert, weil
  der Audit-Satz explizit nur „neue Tracks" nennt und ein blindes Überschreiben
  bei jedem Re-Import einen in der Library-v2-UI absichtlich geänderten
  Profil-Wert riskieren würde (außerhalb des auditierten Scopes). Der Legacy-
  Wert wird gegen die tatsächlich existierenden `quality_profiles`-Zeilen
  validiert (einmal VOR der Track-Schleife geladen, nicht pro Zeile — die in
  §20 gerade erst behobene Import-Performance sollte nicht durch ein neues
  Per-Track-SELECT wieder verlangsamt werden) und fällt bei einer
  baumelnden/gelöschten Profil-Referenz auf `default_profile_id` zurück statt
  sie unvalidiert zu übernehmen. Die `quality_profiles`-Tabelle gehört
  `core/quality/schema.py`, nicht lib2, und existiert in einer minimalen/
  Test-DB u. U. gar nicht — der Ladeversuch ist deshalb fail-open (leeres Set
  bei fehlender Tabelle), exakt wie `default_quality_profile_id()` es für den
  Default-Fall bereits vormacht.
- Schema: alle neuen Spalten sowohl in `LIB2_ARTISTS_DDL`/`LIB2_TRACKS_DDL`
  (Neuinstallationen) als auch in `_ADDED_COLUMNS` (bestehende Installationen,
  idempotente `ALTER TABLE`) — dasselbe Doppel-Muster wie die bereits
  vorhandenen §22-Spalten.
- Tests (`tests/library2/test_importer.py`, alle gegen die synthetische
  `legacy_db`-Fixture mit `ALTER TABLE`-Spalten wie die bestehenden §17.7-
  Tests): Capture-Test für die fünf flachen Artist-Felder; ein COALESCE-
  Regressionstest, der beweist, dass ein Re-Import ohne die Migrationsspalte
  einen zuvor gesetzten Wert NICHT nullt; Capture-Test für alle drei Provider-
  Enrichment-Blöcke; ein Merge-Regressionstest, der beweist, dass ein
  dünnerer Re-Import eine bereits eingefangene Bio nicht überschreibt;
  Capture-Test für die vier Track-Felder; ein Regressionstest, der beweist,
  dass ein fehlendes `play_count` beim INSERT nicht gegen die NOT-NULL-
  Constraint crasht (die eigentliche Motivation für den `tfields[:-2]`-Split
  zwischen INSERT und UPDATE); zwei Tests für `quality_profile_id` — ein
  gültiger Legacy-Wert wird übernommen (ein Geschwister-Track ohne Legacy-Wert
  bekommt weiterhin den Default), ein baumelnder Legacy-Wert (Profil-Id
  existiert nicht in `quality_profiles`) fällt auf den Default zurück statt
  übernommen zu werden. Zusätzlich gegen `tests/repair_jobs/
  test_lib2_upgrade_scan.py`, `tests/quality/test_quality_profiles_crud.py`,
  `tests/acquisition/test_{wanted_adapter,main_pipeline_bridge,
  scheduled_grab,manual_grab}.py`, `tests/repair/test_file_scope.py`,
  `tests/test_admin_gating.py` verifiziert (84/84 grün) — alle anderen
  Konsumenten von `lib2_artists`/`lib2_tracks` außerhalb von
  `tests/library2/`, um auszuschließen, dass die neuen Spalten dortige INSERT/
  SELECT-Annahmen brechen.
- **Nicht angefasst:** keine Live-Verifikation gegen die echte DB des Nutzers
  (reiner Importer-Schema-Fix, dieselbe Einschränkung wie schon in §22 — die
  synthetische Test-DB deckt die Spalten-Mechanik ab, nicht ob die echten
  Legacy-Werte in der Praxis wie erwartet aussehen). Kein UI für die neuen
  Felder — weder Anzeige noch Edit; nur die Datenerhaltung beim Import war der
  auditierte Scope. `lib2_albums`/`lib2_artists` erhalten weiterhin
  IMMER `default_profile_id` (nur Tracks waren im Audit als betroffen
  benannt).

### Priorisierung Abschnitt 17 — Update 2

§17.7 ist damit vollständig gefixt (alle drei ursprünglichen Schritte aus §22
plus die drei in §22 als „nicht angefasst" benannten Lücken). Von der
ursprünglichen §17-Liste (17.1–17.8) ist damit alles entweder gefixt oder als
„kein Implementierungsbedarf" markiert — siehe [[open-issues-tracker]] für den
verbleibenden Gesamt-Backlog (§12 Punkte 40–44, Alias-Design, Manual-Matching-
UI, Preview-Retag).

---

## 24. §40 Artist-Alias-Registry — Design + Umsetzung (2026-07-15/16, Fortsetzungs-Session 6)

Durch Brainstorming mit dem Nutzer erarbeitet und abschnittsweise freigegeben,
danach direkt (ohne separate writing-plans-Zwischenstation, auf expliziten
Nutzerwunsch) mit TDD umgesetzt. **Status: implementiert, committed
(`67a2dac3`).** `pytest tests/library2 tests/repair_jobs` grün (+38 neue Tests),
`vitest`/`oxfmt`/`oxlint` clean.

- Schema: `lib2_artists.canonical_artist_id` (Selbstreferenz, NULL = kanonisch),
  analog `canonical_track_id` aus §39; in `LIB2_ARTISTS_DDL` UND
  `_ADDED_COLUMNS`/Index für Bestandsinstallationen.
- `core/library2/artist_aliases.py`: `link_artist_alias`/`unlink_artist_alias`
  (Validierung: kein Self-Link, keine Ketten, kein Gruppen-Merge in v1) +
  `resolve_alias_group` — beweisbar max. 1 Ebene tief.
- `discography.py`: `expand_artist_discography`/`refresh_artist_discography`
  fächern über die volle Alias-Gruppe auf (jede Zeile behält ihre eigenen
  `lib2_albums`-Zeilen); der Standalone-Fall (keine Aliase) ist
  byte-identisch zum alten Einzel-Artist-Pfad — keine Verhaltensänderung für
  den Normalfall.
- `lib2_discography_refresh`-Sweep-Job: Root-Auswahl überspringt
  Alias-Zeilen (deren Gruppe deckt der Fan-out der kanonischen Zeile ab) —
  verhindert N²-Refetches pro Durchlauf bei einer N-köpfigen Gruppe.
- `queries.py`: `list_artists` blendet Alias-Zeilen aus; `get_artist` merged
  Alben/EPs/Singles über die Gruppe (Header bleibt die kanonische Zeile),
  funktioniert für kanonische UND Alias-IDs gleichermaßen.
- `api/library_v2.py`: `GET/POST/DELETE .../artists/<id>/link-alias` +
  `.../aliases`. Defensive `canonical_artist_id`-Bereinigung an beiden
  Artist-Delete-Pfaden (API-Delete + Importer-Reconcile) ergänzt, weil
  ALTER-migrierte Installationen nie die FK-Constraint der Spalte bekommen
  (nur Neuinstallationen) — App-seitige Bereinigung passend zum bestehenden
  Muster aller anderen Artist-Delete-Nebenwirkungen in dieser Datei.
- Minimal-UI: Alias-Chips + „Link alias"-Modal, das den bestehenden
  Artist-Such-Endpoint wiederverwendet (keine neue Such-Infrastruktur) —
  die volle Recovery-UI mit Match-Vorschlägen bleibt §41.

**Nicht angefasst:** keine Live-Verifikation gegen die echte DB des Nutzers
(reiner Schema-/Backend-/UI-Fix, synthetische Test-DBs decken die Mechanik ab).
Automatische Erkennungs-Heuristik und Gruppen-Merges bleiben bewusst out of
scope (siehe 24.1) — genau wie geplant.

### 24.1 Scope-Klärung gegenüber §38/§41

§38 hat die Fälle bereits geschlossen, in denen zwei Legacy-/lib2-Artist-Zeilen
über eine GEMEINSAME Provider-ID (Deezer/MusicBrainz/Spotify/…) zusammengeführt
werden können — `_ArtistResolver` (`importer.py`) matcht bereits source-agnostisch
auf jede vorhandene Provider-ID. Der in §40 verbleibende Kernfall ist der Fall
OHNE gemeinsamen Schlüssel: dieselbe reale Person tritt bei den Providern unter
zwei GETRENNTEN Katalog-Einträgen auf (Beispiel Hirokyu Samono: Kanji- und
Romaji-Namensvariante sind bei Deezer/Spotify eigenständige Artist-IDs — kein
automatischer Match möglich). Symptome laut §40-Beobachtung:
(a) Artist-Watchlist zeigt beide Zeilen getrennt, (b) „Update Discography" auf
einer Zeile findet nie den Katalog der anderen, (c) der Importer legt bei
Re-Importen weiterhin zwei Zeilen an, weil es strukturell keinen gemeinsamen
Schlüssel gibt, den er finden könnte.

Explizit NICHT Teil dieses Designs (bewusst abgegrenzt, siehe Brainstorming-
Antworten):
- **Automatische Erkennungs-Heuristik** (Fuzzy-/Transliterations-Namensvergleich,
  der Alias-Kandidaten selbst vorschlägt). §40 liefert nur die Verknüpfungs-
  Mechanik; das Verknüpfen selbst bleibt ein manueller, expliziter Schritt.
- **Gruppen-Merges** (zwei bereits-kanonische Zeilen mit jeweils eigenen Aliasen
  zusammenführen). v1 erlaubt nur das Einhängen einer einzelnen, alias-freien
  Zeile in eine bestehende Gruppe.
- **Volle Fehler-Recovery-UI** mit Vorschlägen/Diff-Ansicht — das ist §41
  („Manual Artist Matching UI"), ein separates, größeres Ticket. §40 liefert nur
  die minimale Verknüpfungs-UI, die §41 später aufgreifen/ersetzen kann.

### 24.2 Schema & Verknüpfungs-Semantik

Neue Spalte:

```sql
ALTER TABLE lib2_artists ADD COLUMN canonical_artist_id INTEGER
    REFERENCES lib2_artists(id) ON DELETE SET NULL;
```

`NULL` = diese Zeile ist kanonisch (oder eigenständig, keine bekannten Aliase).
Gesetzt = Alias der referenzierten Zeile. Kein separates Junction-Table nötig —
eine 1:n-Beziehung (eine kanonische Zeile, beliebig viele Alias-Zeilen zeigen auf
sie) reicht, analog zum bereits etablierten `canonical_track_id`-Muster aus §39
(`link_single_album_duplicates`/`duplicate_relationship.py`). Schema-Eintrag
sowohl in `LIB2_ARTISTS_DDL` (Neuinstallationen) als auch `_ADDED_COLUMNS`
(bestehende Installationen, idempotente ALTER) — dasselbe Doppel-Muster wie alle
bisherigen lib2-Schema-Erweiterungen (§22/§23).

Neues Modul `core/library2/artist_aliases.py`, strukturelles Pendant zu
`duplicate_relationship.py`:

- `link_artist_alias(conn, artist_id, alias_of_id)` — validiert + schreibt.
  Regeln (jede Verletzung wirft `AliasLinkError(message, status)`, analog
  `DuplicateRelationshipError`):
  - Kein Self-Link (`artist_id == alias_of_id`).
  - `alias_of_id` muss selbst kanonisch sein (`canonical_artist_id IS NULL`) —
    verhindert Ketten.
  - `artist_id` darf nicht bereits kanonische Wurzel einer eigenen Alias-Gruppe
    sein (keine Gruppen-Merges in v1, siehe 24.1).
  - Beide IDs müssen existieren (sonst 404 auf API-Ebene).

  Durch diese drei Regeln ist die Struktur beweisbar maximal eine Ebene tief —
  keine Rekursion beim Auflösen nötig.
- `unlink_artist_alias(conn, artist_id)` — setzt `canonical_artist_id` dieser
  EINEN Zeile zurück auf `NULL`. Der Rest der Gruppe bleibt unangetastet. Voll
  reversibel.
- `resolve_alias_group(conn, artist_id) -> List[int]` — kanonische ID zuerst,
  danach alle Alias-IDs (sortiert). Nimmt sowohl eine kanonische als auch eine
  Alias-ID entgegen und liefert in beiden Fällen dieselbe Gruppe. Zentrale
  Wiederverwendung durch Discography-Fetch UND Anzeige-Queries (24.3/24.4).

### 24.3 Discography-Fetch (Fan-out) & Scheduled-Sweep

`expand_artist_discography` / `refresh_artist_discography` (`discography.py`)
lösen künftig zuerst `resolve_alias_group(conn, artist_id)` auf und rufen die
HEUTIGE, unveränderte Einzel-Fetch-Logik (`_expand_artist_discography` inkl.
Monitor-/Track-Repair-Schritten) für JEDES Gruppenmitglied einzeln auf — jede
Zeile behält weiterhin ihre eigenen `lib2_albums`-Zeilen wie heute, kein Umbau
von `primary_artist_id`/`lib2_album_artists`. Ein Klick auf die kanonische Zeile
ODER auf eine Alias-Zeile aktualisiert immer die ganze Gruppe. Stats werden pro
Mitglied gesammelt und zu Summen aggregiert (`added`/`enriched`/`removed`/
`total`) für die Rückgabe; scheitert der Fetch für ein Mitglied (z. B.
Rate-Limit), läuft der Fan-out für die restlichen Mitglieder weiter (per-Member
`try`/`except`, wie es `repair_track_number_collisions` bereits für seine
Album-Schleife macht) statt die gesamte Operation abzubrechen — der Fehler wird
pro Mitglied in den aggregierten Stats reportet.

Der geplante Sweep-Job (`core/repair_jobs/lib2_discography_refresh.py`, iteriert
aktuell ALLE monitored Artists) bekommt einen Filter `AND canonical_artist_id
IS NULL` auf seine Root-Auswahl. Ohne diesen Filter würde eine 3er-Gruppe pro
Sweep-Durchlauf 9 statt 3 Fetches auslösen (jedes Mitglied fächert beim eigenen
Sweep-Aufruf erneut in die ganze Gruppe auf) — Alias-Zeilen werden also nicht
mehr als eigener Sweep-Start behandelt, weil sie durch den Fan-out ihrer
kanonischen Zeile bereits mit-aktualisiert werden.

### 24.4 Anzeige (Listing & Artist-Detail)

`Q.list_artists` (`core/library2/queries.py`) bekommt zusätzlich
`AND a.canonical_artist_id IS NULL` im WHERE — Alias-Zeilen verschwinden aus
Watchlist/Grid, nur die kanonische Zeile bleibt als ein Eintrag sichtbar.

`Q.get_artist` löst beim Aufruf zuerst `resolve_alias_group` auf (funktioniert
also gleichermaßen, wenn man die kanonische ODER eine Alias-ID öffnet, z. B.
über einen alten Deep-Link) und ersetzt in der Album-Query
`WHERE aa.artist_id = ?` durch `WHERE aa.artist_id IN (<Gruppen-IDs>)` — die
Detailseite zeigt dann Alben/Singles/EPs aus ALLEN Gruppenmitgliedern gemergt,
das Artist-Header selbst (Bio, Bild, Genres, …) bleibt das der kanonischen
Zeile.

### 24.5 API-Endpoints

Neu in `api/library_v2.py`:

- `POST /api/library/v2/artists/<id>/link-alias` — Body `{"alias_of": <id>}`.
  400 bei Regelverstoß (24.2), 404 wenn eine der beiden IDs nicht existiert.
- `DELETE /api/library/v2/artists/<id>/link-alias` — löst nur diese eine Zeile
  aus ihrer Gruppe.
- `GET /api/library/v2/artists/<id>/aliases` — liefert kanonische ID + volle
  Mitgliederliste (id/name/image) für die UI.

### 24.6 Minimal-UI

Auf dem Artist-Header ein schlichter „Aliases"-Bereich (Chips mit Namen, ✕ zum
Lösen) + Button „Als Alias verknüpfen" → Modal mit Textsuche, die den bereits
existierenden `/api/library/v2/artists?search=`-Endpoint wiederverwendet (keine
neue Such-Infrastruktur nötig, keine Duplikation). Klick auf ein Suchergebnis
ruft `link-alias` auf. Bewusst schlicht gehalten — die vollwertige
Recovery-UI mit Fehlererkennung/Vorschlägen ist §41 und kann diese Modal-Basis
später ersetzen oder erweitern.

### 24.7 Edge Cases & Error Handling

- Self-Link → 400.
- `alias_of` ist selbst bereits Alias (`canonical_artist_id NOT NULL`) → 400
  „must link to a canonical artist" (keine Ketten).
- `artist_id` ist selbst bereits kanonische Wurzel einer eigenen Gruppe → 400
  (kein Gruppen-Merge in v1, siehe 24.1).
- `artist_id`/`alias_of` existiert nicht → 404.
- Kanonische Zeile wird gelöscht (Artist-Delete) → `ON DELETE SET NULL` macht
  alle bisherigen Alias-Zeilen automatisch wieder zu eigenständigen kanonischen
  Zeilen — kein Datenverlust, keine verwaisten FKs.
- Provider-Fetch für ein Gruppenmitglied schlägt fehl → Fan-out läuft für die
  restlichen Mitglieder weiter (24.3).

### 24.8 Testing-Plan (TDD, ein Commit pro Baustein)

- `tests/library2/test_artist_aliases.py` — alle Validierungsregeln aus
  `link_artist_alias`/`unlink_artist_alias`; `resolve_alias_group` für
  Standalone-Fall, Gruppe mit 1 und mit 2+ Aliasen.
- `tests/library2/test_discography.py` (erweitert) — Fan-out mit 2 gemockten
  Provider-IDs, Trigger von JEDER Gruppen-ID liefert dasselbe Gesamtergebnis;
  Teil-Fehler-Test (ein Mitglied schlägt fehl, Rest läuft durch).
- `tests/repair_jobs/…` (lib2-Discography-Sweep-Test) — Sweep überspringt
  Alias-Zeilen als eigenen Root.
- `tests/library2/test_queries.py` — `list_artists` blendet Alias-Zeilen aus;
  `get_artist` merged Alben über die Gruppe, auch beim Öffnen einer Alias-ID.
- API-Contract-Tests für die drei neuen Endpoints (dort, wo die bestehenden
  lib2-Endpoint-Tests liegen).

### 24.9 Entscheidungs-Log (aus dem Brainstorming)

Kurzfassung der im Dialog getroffenen Entscheidungen, damit sie beim Schreiben
des Implementierungsplans nicht erneut aufgerollt werden müssen:

1. Kernfall = „gleiche Person, verschiedene Provider-Identität" (nicht die
   bereits gefixten Importer-Duplikate über gemeinsame Provider-ID).
2. Verknüpfung wird nur manuell ausgelöst, kein Vorschlags-Algorithmus in v1.
3. Soft-Link (beide Zeilen bleiben bestehen) statt hartem Merge — reversibel,
   kein FK-Umbau bei bestehenden Alben/Tracks.
4. Architektur = Gruppentabelle/-spalte + Fan-out-Fetch + gemergte Anzeige
   (nicht: kanonisches Re-Attachment mit Datenmigration; nicht: reine
   Lese-Zeit-Union ohne Auto-Fetch — Letzteres hätte Symptom (b) nur teilweise
   gelöst).

## 25. Zukünftige UI/UX Roadmap-Punkte (Lidarr-Alignment)

### 25.1 Artist-spezifische automatische Suche
- **Ziel:** Der "Automatic Search"-Button in der Artist-Detailansicht (und entsprechend bei Alben) darf nicht die gesamte globale Wishlist verarbeiten. Er soll sich wie in Lidarr verhalten:
  - Sucht ausschließlich nach gemonitorten Titeln des spezifischen Künstlers/Albums.
  - Führt automatische Upgrades durch, sofern das Quality Profile dies erlaubt.
  - Eine globale Suche ("Automatic Search (Global)") soll weiterhin existieren, jedoch nur im globalen Dashboard bzw. der Wishlist-Ansicht, nicht auf Artist-/Album-Ebene.
- **Scope:** Backend-Erweiterung (Einschränkung des Such-Job-Scopes auf den spezifischen Artist/Album) + UI-Verdrahtung.

### 25.2 Korrekter Import von Track-Features (ReplayGain, Lyrics) aus der Legacy-Library — ✅ gefixt (2026-07-16)
- **Problem:** Beim Importieren aus der alten/legacy Bibliothek (`import_legacy_library`) werden die Track-Feature-Flags (wie `has_replaygain` und `has_lyrics` auf den Track-Dateien) nicht direkt übernommen. Sie werden erst erkannt und in der UI sichtbar, wenn ein manueller "Refresh & Scan" auf dem Artist/Album ausgeführt wird.
- **Root Cause:** `has_replaygain`/`has_lyrics` werden nicht in der DB gespeichert, sondern zur Anzeigezeit aus `lib2_track_files.tags_json` berechnet (`queries.py:663-677`). Der Importer setzt beim Anlegen einer `lib2_track_files`-Zeile aber nie `tags_json` — es bleibt beim Schema-Default `'{}'` (`schema.py:205`), weil der Importer nur Format/Bitrate/Size aus der Legacy-DB kennt, nie die Datei selbst öffnet. Die Legacy-Tabellen (`track_files`/`tracks`) besitzen ihrerseits KEIN separates ReplayGain/Lyrics-Flag zum 1:1-Übernehmen — sie werden real erst durch `core.tag_writer.read_file_tags` (mutagen) gelesen, was bislang nur "Refresh & Scan" (`core/library2/scan.py::rescan_files`) triggerte.
- **Fix:** Neue Funktion `precache_tag_cache()` (`core/library2/tag_cache.py`) liest `tags_json` für alle noch nie gescannten Dateien (`tags_json = '{}'`-Filter, damit ein erneuter Lauf nach einem echten Scan ein No-Op ist) direkt nach dem Import — analog zu `precache_all_artwork`/`precache_tracklists`: bounded `ThreadPoolExecutor` (`auto_import.max_workers`), damit tausende Dateien den Import nicht seriell blockieren. Als neue Stage `"tags"` zwischen Tracklist- und Artwork-Precache in `POST /api/library/v2/import` (`api/library_v2.py`) eingehängt.
- **Verifikation:** `pytest tests/library2/test_tag_cache_precache.py` (5 neue Tests: Population, Skip bereits gescannter Dateien, Nebenläufigkeit + `max_workers`-Cap, nicht auflösbarer Pfad) + `pytest tests/library2 tests/imports` grün (1137, siehe [[stale-dev-server-false-bug-reports]]-Verifikationsdisziplin). Zusätzlich gegen eine Backup-Kopie der echten Nutzer-DB verifiziert (kein Docker, siehe §12-Nachtrag zur No-Docker-Verifikation): 30/43 Track-Dateien waren `tags_json='{}'`; nach `precache_tag_cache()` 0/43 — reale Lyrics/ReplayGain/Genre-Tags korrekt gelesen und persistiert.
- **Scope:** `core/library2/tag_cache.py` (neu: `precache_tag_cache`), `api/library_v2.py` (Import-Flow-Hook).

## 26. §50 Interaktives Reorganize — Preview + Apply, Album- und Artist-Scope — ✅ gefixt (2026-07-16)

**Architektur-Vorentscheidung:** Ein Explore-Audit zeigte, dass `core.library_reorganize.preview_album_reorganize`/`reorganize_album` hart gegen die LEGACY-Tabellen (`albums`/`artists`/`tracks`) verdrahtet sind (`load_album_and_tracks`) — eine komplette Neuimplementierung gegen `lib2_*`-Tabellen (Staging, Copy, Post-Processing/Re-Tag, Quality-Gate, Sidecar-Handling) wäre eine zweite Pipeline, die synchron gehalten werden müsste (widerspricht §4.5 Reuse-First). Der Audit deckte aber einen echten, unabhängig vom aktuellen Feature bereits bestehenden Bug auf: `core/reorganize_runner.py::_update_track_path` aktualisierte nach einem Datei-Move NUR die Legacy-`tracks.file_path` — nie `lib2_track_files.path` über den vorhandenen `legacy_track_id`-Rückverweis. Jedes Reorganize eines bereits importierten Albums (egal ob über die Legacy-UI, den `library_reorganize`-Repair-Job oder das neue lib2-Feature) hätte lib2 also stillschweigend desynchronisiert.

**Fix vor dem eigentlichen Feature:** `_update_track_path` synct jetzt zusätzlich `lib2_track_files.path` (best-effort, bricht den Reorganize-Lauf nicht ab, no-op auf einer reinen Legacy-Installation ohne lib2-Schema). Erst danach war die Bridge-Strategie (Pattern A, wie bei §44 Enrich: `legacy_album_id`/`legacy_artist_id` auflösen, an die bestehende Pipeline delegieren) sicher.

**Umsetzung:**
- `core/library2/reorganize_bridge.py` (neu): `resolve_legacy_album_id`/`resolve_legacy_artist_id` (404 fehlende Entity, 409 „kein Legacy-Datensatz" — z.B. ein per Update Discography hinzugefügtes Album), plus dünne Wrapper `album_reorganize_sources`, `global_reorganize_sources`, `preview_album_reorganize`, `enqueue_album_reorganize`, `enqueue_artist_reorganize_all` — jede löst zuerst die Legacy-ID auf und delegiert dann unverändert an `core.library_reorganize`/`core.reorganize_queue`.
- `api/library_v2.py`: 5 neue Endpoints (`GET .../albums/<id>/reorganize/sources`, `GET .../reorganize/sources` global, `POST .../albums/<id>/reorganize/preview`, `POST .../albums/<id>/reorganize`, `POST .../artists/<id>/reorganize-all`).
- Frontend (`reorganize-modal.tsx`, neu): `AlbumReorganizeModal` (Live-Vorschau-Tabelle current→new Path, Source-/Mode-Picker, Rename-only-Checkbox) hinter einem neuen „Reorganize"-Icon-Button (`folder`-Icon, bisher ungenutzt, entspricht Legacys 📁) pro Album; `ArtistReorganizeAllModal` hinter „Reorganize All" im Artist-Toolbar.

**Verifikation:** `pytest tests/library2 tests/test_reorganize*.py tests/test_library_reorganize*.py` — 737 grün (28 neue Tests: Runner-Sync-Fix, Bridge-Unit-Tests gegen eine echte importierte Legacy+lib2-DB, Flask-Route-Tests). `vitest`/`oxfmt`/`oxlint` clean (6 neue Frontend-API-Tests). Keine Live-Docker-Verifikation (kein laufender Provider-Auth-Kontext in dieser Session) — reine Testabdeckung.

**Scope:** `core/reorganize_runner.py` (Bugfix), `core/library2/reorganize_bridge.py` (neu), `api/library_v2.py`, `webui/.../reorganize-modal.tsx` (neu), `webui/.../-library-v2.api.ts`, `webui/.../-library-v2.types.ts`.

## 27. §49 Alternate-Cover-Art-Picker — ✅ gefixt (2026-07-16)

**Architektur:** Anders als §50 brauchte dieses Feature KEINE Legacy-Brücke — die Kandidaten-Suche (`core.metadata.art_lookup.gather_album_art_candidates`) ist bereits rein namensbasiert (Artist-/Album-String + optionale MusicBrainz-Release-ID), und lib2 hat mit `core/library2/artwork.py` bereits ein vollständig lib2-natives, medienserver-unabhängiges Artwork-Cache-System (`build_artwork`, `<db_dir>/lib2_artwork/<kind>_<id>.jpg`, serviert über `/api/library/v2/artwork/<kind>/<id>`). Damit funktioniert der Picker auch für reine Discography-Alben ohne Legacy-Datensatz — ein Vorteil gegenüber der Legacy-Variante.

**Persistenz-Entscheidung:** Statt eines separaten „Pin"-Flags (wie Legacys nicht-leere `thumb_url`) nutzt die Auswahl das bereits vorhandene, generische `lib2_metadata_overrides`-Feld `image_url` (war für `release_group`/`artist` bereits im Feld-Whitelist vorhanden, aber bisher von keinem Feature gelesen). `build_artwork()` prüft dieses Override jetzt VOR dem eingebetteten Cover/Provider-Fallback — dadurch übersteht eine manuelle Auswahl auch ein späteres erzwungenes „Refresh & Scan" oder einen Precache-Lauf, ohne dass eine zweite Pin-Mechanik gebaut werden musste.

**Umsetzung:**
- `core/library2/artwork.py`: `_manual_art_override_url()` (liest das `image_url`-Override), `apply_manual_artwork()` (lädt die gewählte URL herunter, validiert sie als Bild, setzt das Override, schreibt sofort in den Cache — in dieser Reihenfolge, damit ein totes URL niemals ein Override hinterlässt, das bei jedem künftigen Refresh erneut fehlschlägt).
- `api/library_v2.py`: `GET .../albums/<id>/art-options` (kurzlebiger In-Prozess-TTL-Cache, spiegelt den Legacy-Cache) und `POST .../albums/<id>/art`.
- Frontend (`art-picker-modal.tsx`, neu): `AlbumArtPickerModal` (Kandidaten-Grid, Klick zum Anwenden) hinter einem neuen „Change cover"-Icon-Button (`cover`-Icon, neu).

**Verifikation:** 6 neue Core-Modul-Tests (Override-Präzedenz + explizite Prüfung, dass ein Force-Refresh eine manuelle Auswahl NICHT überschreibt) + 8 neue Flask-Route-Tests + 7 neue Frontend-API-Tests, alle grün. `pytest tests/library2` 513 grün gesamt. `oxfmt`/`oxlint` clean.

**⚠️ Nachtrag Deep-Dive 2026-07-16 (Status-Einschränkung) — ✅ behoben, siehe §28.**

**Scope:** `core/library2/artwork.py`, `api/library_v2.py`, `webui/.../art-picker-modal.tsx` (neu), `webui/.../-library-v2.api.ts`, `webui/.../-library-v2.types.ts`.

## 28. Deep-Dive-Findings 2026-07-16, Runde 1 (A1/A2, G1–G6) — ✅ behoben

Siehe `docs/library-v2-deep-dive-findings-2026-07-16.md` für die vollständige
Analyse; hier nur die Fix-Zusammenfassung der in dieser Session behobenen
Bugs (Priorisierung §E, Punkte 1–4):

- **A1 (Cover-Embed-Lücke):** `core/library2/retag.write_tags` bekam einen
  `force_cover`-Parameter, der den Unchanged-Text-Diff-Fastpath überspringt,
  wenn ein Album-Cover zum Embedden vorliegt. `POST .../albums/<id>/art`
  stößt nach erfolgreichem Apply automatisch einen Background-Retag-Job
  (`force_cover=True`) für alle file-tragenden Tracks des Albums an —
  derselbe Job-Registry-Pfad wie `/tags/write`.
- **A2 (7-Tage-Browser-Cache):** `_artwork_url` hängt jetzt `?v=<mtime der
  Cache-Datei>` an — ein neuer Pick ändert die Cache-Datei-mtime und damit
  die URL, sodass `Cache-Control: immutable` nicht mehr im Weg steht.
- **G1 (Discography-Single-Swallow):** `_match_existing` erlaubt den
  Cross-Bucket-Fallback nur noch, wenn die Provider-Release KEINE eigene
  Provider-ID trägt; `_merge_external_id` überschreibt nie mehr eine
  vorhandene, abweichende ID derselben Source (loggt den Konflikt statt
  die Row zu vergiften).
- **G2 (Album-RG-Tag-Cache auf gemappten Pfaden):** `analyze_album_replaygain`
  trägt `file_id` jetzt direkt durch die Analyse-Pipeline statt die
  File-Zeile nach dem Write per `WHERE path=?` (aufgelöster Pfad) erneut
  zu suchen — funktioniert jetzt auch mit Path-Mapping (Docker).
- **G3 (Track-RG-Button ohne Invalidierung):** `TrackReplayGainButton`
  invalidiert jetzt `LIBRARY_V2_QUERY_KEY` nach Erfolg, analog zum
  Album-RG-Button.
- **G4 (Autolink-Feat-Titel-Duplikat):** `_find_or_create_track` nutzt jetzt
  `dedup_title_key` (dieselbe Feat.-Normalisierung wie der Importer, §39)
  statt eines rohen Exact-Title-Matches, plus einen (disc, track_number)
  -Slot-Fallback, bevor eine neue Row entsteht.
- **G5 (`has_lyrics` ignoriert `unsyncedlyrics`):** `queries.py` prüft jetzt
  beide Tag-Keys, deckungsgleich mit dem Lyrics-Tab und dem
  `missing_lyrics`-Repair-Job.
- **G6 (falsche Interactive-Search-Fußnote):** Text korrigiert — Autolink
  verlinkt fertige Downloads automatisch, kein manuelles „Refresh & Scan"
  nötig.

**Verifikation:** `pytest tests/library2` 533 grün (20 neue Tests). `oxfmt`/
`oxlint --type-check` clean für die geänderten Frontend-Dateien.

**Noch offen aus dem Deep-Dive:** A3–A9, B1–B7, C1–C4, G7–G8, H1–H13, I1–I10 —
siehe `docs/library-v2-deep-dive-findings-2026-07-16.md` Abschnitt E für die
Priorisierung der nächsten Runde.

## 29. Deep-Dive-Findings 2026-07-16, Runde 2 (C1, G7, A5, A8, A9, B3) — ✅ behoben

Siehe `docs/library-v2-deep-dive-findings-2026-07-16.md` für die vollständige
Analyse; hier die Fix-Zusammenfassung der in dieser Session behobenen Punkte
(Priorisierung §E, Punkte 5–7 + Teile von 11).

- **C1 (Scoped Automatic Search)** — größter Punkt: `A3` (Album-Zeile feuerte
  in Wahrheit die globale Wishlist) und `A4` (Track-Level `autoGrabBest` war
  eine zweite Client-Decision-Engine) sind komplett ersetzt. Neuer Endpoint
  `POST /api/library/v2/<entity>/<id>/search` (`entity` ∈
  `artists|albums|tracks`) löst den Track-Scope auf (neuer
  `core/library2/wanted.entity_track_ids`-Helper, aus
  `recompute_wanted_for_entity` extrahiert), recomputet die Wanted-Projektion,
  mirrort sie in die Wishlist (`mirror_projected_tracks_wishlist` — dieselbe
  "inline upgrade scan"-Logik wie der globale Upgrade-Scan und die
  Monitor-Toggles) und übergibt die daraus tatsächlich "should-queue"-fähigen
  Wishlist-IDs an einen neu injizierten `scoped_wishlist_search_dispatcher`.
  Der Dispatcher (`web_server._library_v2_scoped_wishlist_search`) ruft
  denselben `start_manual_wishlist_download_batch` auf, den
  `/api/wishlist/download_missing` bereits nutzt — **keine zweite
  Download-Pipeline**, nur eine neue Scope-Auflösung davor. Track-Scope nutzt
  `user_initiated=True` (bypasst eine Ignore-Liste, wie beim Track-Monitor-
  Toggle), Album-/Artist-Scope nicht (Cascade-Regel, P1-11-Präzedenzfall).
  Frontend: `AUTOMATIC_SEARCH_RE`/`AUTO_GRAB_RE`/`autoGrabBest`/
  `processWishlist`-Callsite entfernt, durch eine einzige `SCOPED_SEARCH_RE` +
  `runScopedSearch()`-Helper-Funktion ersetzt, die anhand des mitgegebenen
  `Lib2EntityRef` (trackId > albumId > Artist-Fallback) den Scope bestimmt.
  Behebt nebenbei die in A3 genannte Handler-Asymmetrie in `AlbumDetailView`
  (`Automatic Search` war dort ein stiller No-Op).
- **G7 (Reorganize-Queue-Status)** — reiner Frontend-Fix, kein Backend nötig:
  neue `fetchLibraryV2ReorganizeQueueSnapshot()` liest das bestehende
  `/api/library/reorganize/queue` (dieselbe Legacy-Queue, die
  `mountReorganizeStatusPanel` pollt). `AlbumReorganizeModal` trackt sein
  eigenes `queue_id` (das Apply-Response bereits lieferte, auch im
  „already queued"-Fall) und pollt bis zu einem Terminalstatus
  (`done`/`failed`/`cancelled`) mit Live-Fortschritt. `ArtistReorganizeAllModal`
  hat keine Per-Album-IDs vom Bulk-Endpoint — pollt best-effort nach
  `artist_name`-Match, bis nichts mehr für diesen Artist aktiv/queued ist.
- **A5 (BPM/Duration)** — `bpm` (Spalte existierte bereits, wurde nie
  projiziert) jetzt in `_serialize_track`/`_missing_track_placeholder`
  (`core/library2/queries.py`) und im `LibraryV2Track`-Typ. Neue Duration-
  (mm:ss, `lib2_tracks.duration` ist Millisekunden) und BPM-Spalte in der
  Track-Tabelle (`AlbumTrackTable`), zwischen Artists und Match.
- **A8 (Match-Chips nur für konfigurierte Provider)** — `entity_match_status`/
  `album_match_bundle` (`core/library2/match_status.py`) bekommen einen
  optionalen `available_services`-Parameter; jeder Chip trägt jetzt
  `available: bool`. Der neue DI-Callable `configured_match_services_getter`
  (`web_server._library_v2_configured_match_services`) leitet das aus
  `_get_enrichment_status()` ab — denselben "configured"-Flags, die die
  Settings-Enrichment-Karten zeigen (per Remap-Tabelle für die
  `_enrichment`-Suffix-Keys) — keine Duplikation der Worker-/Config-Checks.
  `MatchChips` filtert `available === false` standardmäßig raus (Default „nur
  konfigurierte", siehe B5-Vorschlag; ein globaler „alle zeigen"-Toggle bleibt
  B5 vorbehalten). Das dauerhaft graue Tidal-Rauschen ist damit weg.
- **A9 (Artist-Image-Picker)** — rundet §49 ab. Neues
  `core.metadata.art_lookup.gather_artist_image_candidates`: ein Kandidat pro
  konfigurierter Quelle (Spotify/Deezer/iTunes/Discogs — die vier mit
  einheitlichem `search_artists()→Artist.image_url`; AudioDB/Last.fm/Genius/
  Tidal/Qobuz/JioSaavn/Bandcamp fehlen mangels einheitlicher Client-Methode
  bewusst, kein sauberer Wiederverwendungspfad ohne Risiko). Nutzt dieselbe
  Signifikant-Token-Namensabgleich-Logik wie der Album-Matcher
  (`_artist_name_matches`, aus `_significant_tokens` abgeleitet). Neue
  Endpoints `GET/POST .../artists/<id>/art-options|art` — `apply_manual_
  artwork` unterstützte `kind="artist"` bereits vollständig (§49-Design), kein
  Cover-Embed-Retag nötig (Artist-Foto wird in keine Audiodatei eingebettet).
  Frontend: `art-picker-modal.tsx` bekommt `ArtistImagePickerModal` (identisches
  Karten-Grid), neuer „Change Photo"-Button im Artist-Toolbar.
- **B3 (klickbare RG/LR-Badges)** — Features-Spalte zeigt RG/LR jetzt IMMER
  (grau + klickbar wenn fehlend, statt „—"): RG-Klick ruft den bestehenden
  `/tracks/<id>/replaygain`-Endpoint; LR-Klick bei fehlenden Lyrics ruft einen
  neuen `POST /api/library/v2/tracks/<id>/fetch-lyrics`-Endpoint (neues
  `core/library2/lyrics.py`, spiegelt `replaygain.py`s Orchestrierungs-Muster:
  injizierbarer `resolve_fn`/`lyrics_client_obj`, nutzt denselben
  `LyricsClient`/`create_lrc_file` wie der `missing_lyrics`-Repair-Job, rescannt
  den Tag-Cache danach). LR-Klick bei vorhandenen Lyrics öffnet den
  Lyrics-Tab des Track-Detail-Modals (`TrackDetailButton`/`TrackDetailModal`
  bekommen ein extern kontrolliertes `openTab`, damit Badge und „Details"-Icon
  dieselbe Modal-Instanz teilen). Der separate `TrackReplayGainButton` in der
  Actions-Spalte entfällt (durch die Badge ersetzt).

**Verifikation:** `pytest tests/library2 tests/metadata` — 562 + 728 grün in
Isolation (6 vorbestehende, unabhängige Test-Order-Pollution-Failures in
`tests/metadata/test_metadata_completion_canonical.py`/
`test_metadata_discography.py` treten nur auf, wenn beide Suiten in
DERSELBEN pytest-Session nacheinander laufen — per `git stash`-Vergleich
verifiziert als bereits vor dieser Session vorhanden, nicht durch diese
Änderungen verursacht). 41 neue Backend-Tests. `vitest` 179 grün (24 neue
Frontend-Tests), `oxfmt`/`oxlint --type-check` clean.

**Scope:** `core/library2/wanted.py`, `core/library2/wishlist_mirror.py`
(unverändert, nur wiederverwendet), `core/library2/lyrics.py` (neu),
`core/library2/match_status.py`, `core/library2/queries.py`,
`core/metadata/art_lookup.py`, `api/library_v2.py`, `web_server.py`,
`webui/.../-library-v2.api.ts`, `webui/.../-library-v2.types.ts`,
`webui/.../library-v2-page.tsx`, `webui/.../reorganize-modal.tsx`,
`webui/.../art-picker-modal.tsx`.

**Noch offen aus dem Deep-Dive:** A6/A7 (History-Vereinheitlichung +
Pipeline-Lifecycle-Persistenz, C3/C4) — bewusst zurückgestellt: die
Scope-Korrelation von `acquisition_requests` (Scope-Werte `recording`/
`release_group`/`release_edition`/`artist_missing`/`upgrade`, nicht 1:1
Artist/Album/Track) auf lib2-Entities zurück auf Artist/Album/Track ist nicht
trivial und verdient eine eigene fokussierte Recherche-Session, bevor Code
geschrieben wird — falsches Zuordnen wäre schlimmer als der Status quo
(nur `track_downloads`). B1/B2/B4 (UI-Entrümpelung), B5/B6 (konfigurierbare
Spalten/Sort/Bulk), C2 (Manage Track Files), G8 (kleinere Runde-2-Funde),
H1–H13, I1–I10 — siehe `docs/library-v2-deep-dive-findings-2026-07-16.md`
Abschnitt E.

## 30. Deep-Dive-Findings 2026-07-16, Runde 3 (B7, G8, C2, B1/B2/B4) — ✅ behoben

Siehe `docs/library-v2-deep-dive-findings-2026-07-16.md` Abschnitt E,
Punkte 5, 8, 10 (Teil) und 11 (Teil). H-/I-Punkte bleiben unangetastet
(brauchen erst Nutzer-Abstimmung, siehe Hinweis am Kopf des Dokuments);
A6/A7/C3/C4 bleiben bewusst zurückgestellt (§29-Begründung gilt unverändert).

- **B7 (Search-Upgrades-Konsolidierung)** — der globale
  `lib2_upgrade_scan`-Trigger stand fälschlich in jeder Artist-Toolbar
  (er scannte immer den GANZEN Katalog, nie nur diesen Artist). Button aus
  der Artist-Toolbar entfernt, neue `UpgradeScanButton`-Komponente lebt jetzt
  auf der Library-Übersicht neben Import — ehrlicher Ort für eine global
  wirkende Aktion. Reiner Frontend-Move, kein Backend-Unterschied.
- **G8 (drei der vier gesammelten Runde-2-Kleinfunde)**:
  - `auto_monitor_releases` (`core/library2/discography.py`) setzte
    `UPDATE lib2_tracks SET monitored=1 WHERE album_id=?` bedingungslos beim
    Retry/Materialize einer auto-monitorten Release — überfuhr damit einen
    Track, den der Nutzer zuvor explizit unmonitort hatte (P1-14-Verletzung).
    Fix: dieselbe `explicitly_unmonitored_track_ids`-Veto-Prüfung wie im
    Bulk-Monitor-Endpoint (`api/library_v2.py`) davor geschaltet — nur
    unveto'te Tracks werden geflippt.
  - Der Retry-Query für stecken gebliebene Tracklist-Materialisierungen
    filterte auf `al.primary_artist_id=?`, während Index und Prune-Query
    im selben Modul über die `lib2_album_artists`-Junction laufen — ein Album,
    dessen Primary ein anderer (verlinkter) Artist ist, wurde nie retried.
    Jetzt joint auch der Retry-Query über die Junction, konsistent mit den
    anderen beiden Queries.
  - `link_download_into_library_v2` (`core/library2/autolink.py`) rief
    `recompute_wanted(conn, track_ids=[track_id])` ohne `profile_id` auf —
    Default `1`, eine harte §1-Invarianten-Verletzung im Pipeline-Kontext
    (kein Request-Profil vorhanden). Fix: `default_quality_profile_id(conn)`
    wie überall sonst im selben Modul.
  - Die vierte Beobachtung (`_find_or_create_artist`-Volltabellen-Scan +
    fehlende External-ID-/§40-Alias-Berücksichtigung) bleibt bewusst offen —
    anders als die drei oben hat sie keine konkrete Fix-Richtung im
    Deep-Dive-Dokument, und ein Alias-Match-Heuristik-Fix ohne eigene
    Recherche riskiert genau das falsche Zusammenführen von Artists, vor dem
    schon A6/A7 zurückschrecken ließ.
- **C2 (Manage Track Files)** — Lidarr-Style Datei-Liste + Bulk-Delete,
  ADR-05-Reuse wie vom Deep-Dive vorgeschlagen:
  - Neuer Read `core.library2.queries.list_artist_track_files` +
    `GET /api/library/v2/artists/<id>/track-files` (paginiert, Suche über
    Track-/Album-Titel), Scope identisch zu `file_delete._scope_snapshot`
    (`primary_artist_id`, `file_state<>'deleted'`) — eine Selektion aus
    dieser Liste passt exakt zu dem, was die ADR-05-Endpoints für dieselben
    File-IDs sehen.
  - `core/library2/file_delete.py`: `preview_entity_files`/
    `delete_entity_files` bekommen ein optionales `file_ids`, das
    `_scope_snapshot`s WHERE-Klausel zusätzlich einschränkt — Journal,
    Preview-Token-Mechanismus und Root-Safety-Checks bleiben unverändert,
    keine zweite Delete-Pipeline. API:
    `GET .../file-delete-preview?file_ids=1,2,3`,
    `POST .../file-delete` mit `file_ids` im Body.
  - Frontend: `ManageTracksModal` bekommt einen Tab-Umschalter
    ("Duplicates" — unverändert, jetzt `ManageTracksDuplicatesTab` — und neu
    "Files"). `ArtistFilesTab` listet paginiert mit Checkbox-Spalte,
    „Select all on page", Freitext-Filter; `FilesDeleteConfirm` spiegelt
    exakt das UX-Muster von `DeleteConfirmModal`s physischem Lösch-Abschnitt
    (Preview, Root-Safety-Anzeige, Bestätigungs-Checkbox, Danger-Button),
    nur auf die Auswahl begrenzt.
- **B1/B2/B4 (UI-Entrümpelung nach C1)** — neues generisches
  „…"-Overflow-Menü-Muster (`styles.overflowMenu`/`overflowMenuItem`,
  gleiche Optik wie das bestehende `EnrichDropdown`, aber ohne dessen
  Enrich-spezifischen Namen) ersetzt an zwei Stellen eine Reihe einzelner
  Icon-Buttons:
  - **Album-Zeile** (`AlbumBlock`): von 10 auf 3 sichtbare Controls reduziert
    — Automatic Search, Interactive Search, „…". „Open Detail" entfällt
    ersatzlos: der Albumtitel selbst ist jetzt der Link zur Detail-Ansicht
    (Lidarr-Muster), Zeilen-Klick bleibt Inline-Expand (eigenes
    `stopPropagation` auf dem Titel-Button verhindert Doppel-Trigger).
    Preview Retag, ReplayGain, Reorganize, Change Cover, Enrich, Album
    Details (vormals `AlbumDetailButton`, jetzt entfernt und in den neuen
    `AlbumOverflowMenu` gefaltet) und Delete wandern ins Overflow-Menü.
  - **Album-Detail-Ansicht** (`AlbumDetailView`, B2) hatte vorher NUR
    Edit-Metadata; bekommt jetzt exakt denselben `AlbumOverflowMenu` im
    Header wie die Zeile — dieselbe Komponente, `onDeleted` unterscheidet nur
    das Verhalten nach einem erfolgreichen Löschen (Zeile: verschwindet von
    selbst durch Query-Invalidierung; Detail-Ansicht: `onDeleted` navigiert
    zurück zum Artist, da die aufgerufene Seite sonst auf ein gelöschtes
    Album zeigen würde).
  - **Artist-Toolbar** (`ArtistDetailView`, B4): von 16 auf 12 sichtbare
    Controls, in drei Gruppen statt drei beliebigen Reihen. Primärleiste
    unverändert (Refresh & Scan, Automatic Search, Interactive Search, Update
    Discography — Lidarr-Kern). Neue `ArtistToolsMenu`-Dropdown bündelt
    Preview Retag, Reorganize All, Maintenance, Manual Import, Enrich (exakt
    die vom Deep-Dive vorgeschlagene "Files/Tools"-Gruppe). Rechte Gruppe
    (Entity-Verwaltung): Manage Tracks, History, Edit Metadata, Change Photo,
    Monitoring, Profile, Delete — die im Deep-Dive als „ggf." (optional)
    markierte Zusammenlegung von Edit/Monitoring/Profile in ein gemeinsames
    Tab-Modal wurde bewusst NICHT gemacht (spekulativer Umbau ohne
    festgeschriebene Notwendigkeit; reine Gruppierung erfüllt den
    Kern-Vorschlag bereits).
  - `retagTarget`/`deleteTarget` in `ArtistDetailView` sind jetzt auf
    `entity: 'artists'` verengt (Album-Retag/-Delete läuft komplett über den
    neuen `AlbumOverflowMenu`, der seinen eigenen `RetagModal`/
    `DeleteConfirmModal` besitzt statt über lifted State im Parent).

**Verifikation:** `pytest tests/library2` (570 grün) und `tests/metadata`
(728 grün) je isoliert; kombiniert dieselben 6 vorbestehenden
Test-Order-Pollution-Failures aus §29 (Cross-Suite-Only, nicht durch diese
Session verursacht — mit `tests/library2`/`tests/metadata` einzeln
verifiziert). 16 neue Backend-Tests (`test_discography.py`,
`test_autolink.py`, `test_queries.py`, `test_file_delete.py`,
`test_api_routes.py`). `vitest` 181 grün (39 neue Frontend-Tests in
`-library-v2.api.test.ts`), `oxfmt --check`/`oxlint --type-check` über den
gesamten `webui/src`-Baum clean. Zusätzlich live gegen den laufenden
Dev-Server verifiziert (`dev.py`, echte Daten, Artist „Justin Bieber"):
`GET .../track-files` direkt per curl (13 Files über 5 Seiten), UI per
Playwright/Chromium-Screenshots — Album-Overflow-Menü, Artist-Toolbar
„Files/Tools"-Dropdown, Album-Detail-Header-Menü (B2) und der neue
Files-Tab inkl. Checkbox-Auswahl + Delete-Preview (die ADR-05-
Root-Safety-Sperre griff dabei korrekt, weil die Dev-Config keinen
passenden `library.music_paths`-Root für die Testdateien hat) sehen alle
wie entworfen aus, keine Konsolenfehler. Der visuelle Check deckte dabei
einen echten Bug auf: `qualityText` in `ArtistFilesTab` nahm an, `bitrate`
sei immer in bps (zeigte „1kbps" statt „923kbps") — gefixt mit derselben
Normalisierungs-Heuristik (`bitrate > 5000 ? /1000 : bitrate`), die
`QualityDisplay`/`fileText` an anderer Stelle schon nutzen (Datenmodell
liefert Bitrate je nach Quelle inkonsistent in bps oder schon in kbps).

**Scope:** `core/library2/discography.py`, `core/library2/autolink.py`,
`core/library2/queries.py`, `core/library2/file_delete.py`,
`api/library_v2.py`, `webui/.../-library-v2.api.ts`,
`webui/.../-library-v2.page.module.css`,
`webui/.../-ui/library-v2-page.tsx`.

**Noch offen aus dem Deep-Dive:** A6/A7/C3/C4 (History/Lifecycle, §29
unverändert zurückgestellt), B5/B6 (konfigurierbare Spalten/Sort/Bulk —
größter verbleibender Block), G8-Vierter-Punkt (`_find_or_create_artist`
Perf + Alias-Awareness, s.o.), H1–H13, I1–I10 (Legacy-/Lidarr-Gap, brauchen
Nutzer-Abstimmung vor Umsetzung).

---

## 31. Deep-Dive-Findings 2026-07-16, Runde 4 (B5, B6) — ✅ behoben

Siehe `docs/library-v2-deep-dive-findings-2026-07-16.md` Abschnitt E,
Punkt 10 — der zu diesem Zeitpunkt größte verbleibende Block, der (anders
als H/I) **keine** Nutzer-Abstimmung vor Umsetzung brauchte (reine
B-Kategorie: UI/UX-Vorschlag, kein Legacy-/Lidarr-Paritäts-Punkt).
A6/A7/C3/C4 und H1–H13/I1–I10 bleiben unverändert zurückgestellt
(Begründung §29/Kopf des Deep-Dive-Dokuments gilt weiter).

- **B5 (nutzerkonfigurierbare Spalten + Match-Provider-Sichtbarkeit)** —
  neue, sehr kleine `lib2_ui_preferences`-Tabelle (ein JSON-Blob, ein Row —
  lib2 ist Admin-only/Single-Profil laut ADR-01, also kein Grund für eine
  Multi-User-Tabelle) statt localStorage: überlebt Browser-/Profilwechsel.
  `core/library2/ui_preferences.py` (`get_ui_preferences`/
  `update_ui_preferences`, One-Level-Deep-Merge pro Sektion, sodass ein
  Patch wie `{"track_table":{"columns":{"bpm":false}}}` nur dieses Feld
  ändert) + `GET`/`PUT /api/library/v2/ui-preferences` (PUT admin-only via
  `_guard()`, GET für jedes Profil). Frontend: Zahnrad-Icon-Popover
  (`TrackTableOptionsMenu`, gleiches optische Muster wie das bestehende
  Overflow-Menü) an der Track-Tabelle jedes Albums — Spalten-Checkboxen
  (Artists/Duration/BPM/Match/Quality/Features/Metadata/File-Pfad) + „Show
  all match providers"-Toggle (überschreibt A8s Default-Filterung auf nur
  konfigurierte Provider). Jede Checkbox schreibt sofort einen Patch,
  React-Query-Cache wird optimistisch aus der Response gesetzt. Bewusst
  NICHT umgesetzt (kleinere Restpunkte aus dem Deep-Dive-Vorschlag, um den
  Scope beherrschbar zu halten): eine eigene Disc-Spalte (Alben sind
  überwiegend Single-Disc; `disc_number` ist im Payload, aber es gibt noch
  keine Spalte, die man ein-/ausblenden könnte) und die Aufspaltung von
  Format/Bitrate in zwei separate Spalten (`QualityDisplay` zeigt beides
  bereits kombiniert in einem Badge). Beides sind reine Politur-Punkte,
  kein funktionaler Gap.
- **B6 (Sort, Mehrfachauswahl, Bulk-Leiste)** — rein clientseitiger Sort
  (alle Felder sind bereits im Album-Payload; kein Server-Roundtrip nötig)
  über klickbare `#`/Title/Duration/BPM-Header (`SortableHeader`,
  drei-Zustands-Zyklus asc→desc→unsortiert). Checkbox-Spalte + „Select
  all"-Header-Checkbox (nur für Zeilen mit echter `track.id`, also nicht
  für Missing-Slots ohne Row). Bulk-Leiste (`TrackTableBulkBar`) erscheint
  bei nicht-leerer Auswahl, reine Wiederverwendung bestehender
  Endpoints/Mutationen statt neuer Bulk-Pipeline:
  - **Monitor/Unmonitor**: `Promise.all` über den bestehenden
    Einzel-Track-`/monitor`-Endpoint (kein neuer Bulk-Endpoint nötig).
  - **Write Tags**: `/tags/write` nahm bereits eine `track_ids`-Liste
    entgegen (unverändert seit §18.2) — direkter Aufruf +
    `awaitBulkJob`-Polling (dasselbe Muster wie `RetagModal`).
  - **ReplayGain**: `Promise.all` über den bestehenden
    Einzel-Track-`/replaygain`-Endpoint (analog zu Monitor — die
    Auswahl ist album-scoped, ein Album-weiter Bulk-Endpoint wäre hier zu
    grobkörnig für eine Teilauswahl).
  - **Delete files…**: reused denselben `file_ids`-scoped ADR-05-Flow, den
    C2 (§30) für den Artist-Files-Tab gebaut hat — `FilesDeleteConfirm`
    wurde dafür von hart auf `entity: 'artists'` verdrahtet auf
    `entity`/`eid`-Parameter generalisiert (ein Caller, ArtistFilesTab,
    entsprechend angepasst), sodass dieselbe Preview/Execute/Root-Safety-
    Komponente jetzt auch album-scoped für eine Track-Auswahl funktioniert.
    Dafür brauchte `track.file` ein neues `file_id`-Feld (lib2_track_files-
    Row-id) — bisher trug der Payload nur den Pfad, nicht die id, die
    ADR-05 zum Scopen braucht (`core/library2/queries.py::_serialize_track`,
    eine Stelle, versorgt sowohl `get_album` als auch `get_track`).
  - Ein gemischt-selektiertes „Delete files…" (manche Zeilen missing, ohne
    File) deleted nur die File-tragenden — der Button bleibt aktiv, solange
    mindestens eine Auswahl-Zeile eine Datei hat.

**Verifikation:** `pytest tests/library2` (577 grün, 4 neue Backend-Tests:
`test_ui_preferences.py` + Ergänzungen in `test_schema.py`/
`test_queries.py`/`test_api_routes.py`) und `tests/metadata` (728 grün) je
isoliert grün; kombiniert dieselben 6 vorbestehenden Cross-Suite-Test-
Order-Pollution-Failures aus §29/§30 (nicht durch diese Session verursacht).
`vitest` 184 grün (7 neue Frontend-Tests in `-library-v2.api.test.ts`),
`oxfmt --check`/`oxlint --type-check` über den gesamten `webui/src`-Baum
clean. Live gegen den laufenden Dev-Server verifiziert (`dev.py`, echte
Daten, Artist „Michael Jackson"): `GET`/`PUT .../ui-preferences` per curl
(Round-Trip inkl. partiellem Patch bestätigt persistent über Requests
hinweg), UI per Playwright/Chromium-Screenshots — Options-Popover mit allen
8 Spalten-Checkboxen + Provider-Toggle, Bulk-Leiste bei 2 ausgewählten
Tracks (inkl. korrekt aktiviertem „Delete files…" trotz einer Missing-Zeile
in der Auswahl), Sort-Klick dreimal verifiziert (unsortiert → aufsteigend →
absteigend, Titel-Reihenfolge bei jedem Schritt geprüft) — alles wie
entworfen, keine Konsolenfehler.

**Scope:** `core/library2/schema.py`, `core/library2/ui_preferences.py`
(neu), `core/library2/queries.py`, `api/library_v2.py`,
`webui/.../-library-v2.types.ts`, `webui/.../-library-v2.api.ts`,
`webui/.../-ui/library-v2-page.tsx`,
`webui/.../-ui/library-v2-page.module.css`.

**Noch offen aus dem Deep-Dive:** A6/A7/C3/C4 (History/Lifecycle, §29
unverändert zurückgestellt), G8-Vierter-Punkt (`_find_or_create_artist`
Perf + Alias-Awareness), H1–H13, I1–I10 (Legacy-/Lidarr-Gap, brauchen
Nutzer-Abstimmung vor Umsetzung) sowie die zwei in B5 oben genannten
kleinen Politur-Punkte (Disc-Spalte, Format/Bitrate-Split).

---

## 32. UI-Politur nach Runde 4 (Enrich-Konsolidierung, Monitor-Materialize, Zell-Styling)

Nachträglich dokumentiert (Commit `546f8b67`, direkt nach §31 entstanden, aber
ohne eigenen Doc-Abschnitt committed) — reines Frontend-Polish, kein neuer
Deep-Dive-Punkt:

- **Enrich-Dropdowns vereinheitlicht:** Album-Overflow-Menü und Artist-Enrich
  bekommen eine „Enrich with all"-Sammel-Option plus eine Submenü-Darstellung
  am Album-Overflow-Menü; einheitliche `alignLeft`/`alignRight`-Utility-Klassen
  halten alle Popover unabhängig von der Trigger-Position im sichtbaren
  Bereich.
- **Monitor materialisiert fehlende Slots inline:** Klick auf Monitor bei
  einem Missing-Track ruft jetzt direkt `materializeLibraryV2MissingTrack`
  auf und ersetzt den separaten `MissingTrackAddButton` — eine Aktion statt
  zwei, gleicher Mirror-Monitor-Pfad wie zuvor.
- **Reorganize-/Retag-Preview-Modals** bekommen dedizierte Zell-Klassen
  (`filePathCell` für gekürzte Pfade, `diffCell` für umgebrochene Tag-Diffs,
  `modalActionsText` für Footer-Hinweistexte) statt `qualityText`/`muted`
  zweckzuentfremden.

**Verifikation (aus der Commit-Message):** `vitest` 184/184, `oxlint
--type-check` clean, `oxfmt` clean, `pytest tests/library2` 577/577 (Backend
von dieser Änderung nicht berührt).

**Scope:** `webui/.../-ui/library-v2-page.tsx`,
`webui/.../-ui/library-v2-page.module.css`,
`webui/.../-ui/reorganize-modal.tsx`, `webui/.../-ui/retag-modal.tsx`.

---

## 33. Deep-Dive-Findings 2026-07-16, Runde 5 (B5-Rest Disc-Spalte, D6, D3-Teil, D1) — ✅ behoben

Vier kleine, konkret umsetzbare „Kleinere Beobachtungen"/Restpunkte aus
`docs/library-v2-deep-dive-findings-2026-07-16.md`, die (anders als H/I)
keine Nutzer-Abstimmung brauchten — reine B/D-Kategorie (UI/UX, kein
Legacy-/Lidarr-Paritäts-Punkt). A6/A7/C3/C4, der G8-Vierte-Punkt und alle
H1–H13/I1–I10 bleiben unverändert zurückgestellt (Begründung §29/Kopf des
Deep-Dive-Dokuments gilt weiter); D2 (Enrich/Manual-Match-Modal-Merge), D4
(an P2-25 gekoppelt), D5 (Play/Preview — Produktentscheidung, siehe
Diskussion im Deep-Dive-Dokument) bewusst nicht angefasst.

- **B5-Rest: Disc-Spalte** — `core/library2/ui_preferences.py`s
  `DEFAULT_PREFERENCES` bekommt `track_table.columns.disc` (Default: aus,
  die meisten Alben sind Single-Disc). Frontend: neue opt-in Spalte in der
  Track-Tabelle (`disc_number`), erscheint im bestehenden
  `TrackTableOptionsMenu`-Zahnrad ohne Zusatzcode (das Menü iteriert bereits
  generisch über die Spalten-Labels). Die zweite B5-Rest-Idee
  (Format/Bitrate-Split) bleibt bewusst ausgelassen — `QualityDisplay` zeigt
  beides schon in einem Badge, ein Split wäre reine Redundanz ohne
  funktionalen Gewinn.
- **D6: konfigurierbare Artist-Übersicht-Spalten** — neue
  `artist_table.columns`-Sektion in den UI-Preferences (`quality_profile`,
  `genres`, `added`, alle Default aus — die Tabellen-Ansicht ist gerade wegen
  ihrer Dichte gegenüber der Card-Grid interessant, zusätzliche Spalten
  bleiben daher opt-in). Neues Zahnrad-Icon neben dem Cards/Table-Umschalter
  (nur sichtbar in der Table-Ansicht) öffnet dasselbe Popover-Muster wie die
  Track-Tabelle. Backend brauchte nichts Neues: `quality_profile_id`,
  `genres`, `added_at` waren im `LibraryV2ArtistSummary`-Payload schon
  vorhanden.
  - **Refactor im Zuge dessen:** `TrackTableOptionsMenu` und die neue
    `ArtistTableOptionsMenu` teilen sich jetzt einen generischen
    `ColumnsOptionsMenu<K>`-Baustein (Spalten-Checkboxen + optionaler
    `extra`-Slot für Track-Tabelle-spezifische Zusatz-Toggles wie „Show all
    match providers") statt zwei fast identischer Popover-Komponenten.
- **D3 (Teil 1 von 2): „Only show results meeting cutoff"-Filter** in
  Interactive Search — Checkbox neben Quality-/AcoustID-Check, nutzt dieselbe
  `profileTargetRank`-Logik wie das bestehende `ProfileBadge` (inkl. dessen
  „nie fälschlich verstecken"-Regel: Ergebnisse ohne beurteilbare
  Qualitäts-Fakten bleiben auch bei aktivem Filter sichtbar). Eigener
  Leerzustand-Hinweis, wenn der Filter alle Treffer wegfiltert. **Teil 2
  (konfigurierbare Ergebnis-Spalten) bewusst ausgelassen** — bei nur 7 festen
  Spalten in diesem Modal ist der Nutzen gegenüber dem Aufwand gering, anders
  als bei der deutlich breiteren Track-Tabelle.
- **D1: Doppeltes Edit-Icon-Konzept aufgelöst** — `TrackDetailButton` (reine
  Ansichts-Aktion „Track details") nutzte bisher dasselbe `edit`-Icon wie der
  Artist-Header („Edit Metadata", eine echte Metadaten-Änderung). Auf das
  bereits vorhandene `info`-Icon umgestellt; `edit` bleibt ausschließlich für
  echte Edit-Aktionen reserviert. „Album details" im Overflow-Menü war schon
  vorher textbasiert (kein Icon-Konflikt dort).

**Verifikation:** `pytest tests/library2` 579 grün (2 neue Tests in
`test_ui_preferences.py` für Disc-Spalten-Default und
`artist_table`-Merge-Verhalten), `tests/metadata` 728 grün unverändert.
`vitest` 186 grün (2 neue Tests: ein API-Roundtrip-Test für
`artist_table`-Patches, ein Komponententest für den Cutoff-Filter in
`interactive-search.test.tsx`). `oxfmt --check`/`oxlint --type-check` über
den gesamten `webui/src`-Baum clean.

**Scope:** `core/library2/ui_preferences.py`,
`webui/.../-library-v2.types.ts`, `webui/.../-library-v2.api.ts`,
`webui/.../-ui/library-v2-page.tsx`, `webui/.../-ui/interactive-search.tsx`.

**Noch offen aus dem Deep-Dive:** A6/A7/C3/C4, G8-Vierter-Punkt, H1–H13,
I1–I10 (alle wie gehabt), plus D2/D4/D5 (siehe Begründung oben) und der
Format/Bitrate-Split-Politurpunkt aus B5.

---

## 34. §48 Rich-Metadata-Edit-Rest — BPM/Style/Mood/Label/Explicit + Bulk-Edit — ✅ gefixt (2026-07-16)

Letzter offener Kern-Punkt aus §15/§48: Legacy erlaubte Artist/Album/Track das
Editieren von `style`/`mood`/`label` (Artist), `explicit`/`label`/`style`/`mood`
(Album) und `bpm`/`explicit`/`style`/`mood` (Track); Library v2 hatte dafür
weder überall eine Spalte noch einen Override-Pfad, und keine Mehrfachauswahl
für Tracks. Durchgehend Reuse-First über das bestehende
`lib2_metadata_overrides`-System (ADR-06) — keine Parallelstruktur.

- **Schema:** `lib2_albums.style`/`lib2_albums.mood` und
  `lib2_tracks.style`/`lib2_tracks.mood` neu (lib2_tracks.bpm/explicit und
  lib2_albums.explicit/label existierten bereits aus §17.7).
- **Importer + Enrich-Resync:** Album-/Track-Upsert (Insert **und** Update)
  übernehmen jetzt `style`/`mood` aus der Legacy-Zeile, exakt wie bereits für
  den Artist — `insert_fields`-Slicing für `play_count`/`last_played` beim
  Track-Insert blieb dabei unangetastet (neue Felder wurden davor, nicht
  danach eingefügt). `core/library2/enrich.py` COALESCEd dieselben Felder
  beim Re-Sync nach einem manuellen Enrich-Aufruf (§44).
- **Override-Store (`core/library2/metadata_overrides.py`):** `_FIELD_SPECS`
  um die neuen Felder erweitert (artist: `style`/`mood`/`label`;
  release_group: `explicit`/`label`/`style`/`mood`; track:
  `bpm`/`explicit`/`style`/`mood`). Zwei neue Validierungs-Kinds:
  `bool01` (bool/0/1 → int, sonst Fehler) für `explicit`,
  `nonnegative_number` (float, ≥0) für `bpm` — der bestehende generische
  `PATCH /api/library/v2/metadata-overrides/<entity_type>/<id>`-Batch-Endpoint
  brauchte dadurch **keine** Code-Änderung, er ist bereits rein
  spec-getrieben.
- **Read-Projection (`core/library2/queries.py`):** `get_artist()` (Header +
  Album-Zeilen-SELECT/-Entry), `get_album()` und `_serialize_track()` (geteilt
  zwischen Album-Tabelle und Einzel-Track-Detail) projizieren die neuen
  Felder jetzt in jede betroffene Payload; die Missing-Track-Placeholder-Zeile
  bekam passend `explicit`/`style`/`mood: null` für Typ-Konsistenz.
- **UI:** `EditArtistModal` (Style/Mood/Label-Felder), `AlbumMetadataForm`
  (Explicit als Tri-State-Select „Unknown/Explicit/Clean" + Label/Style/Mood),
  `TrackMetadataForm` (BPM-Zahlenfeld, Explicit-Select, Style/Mood) — alle
  nach demselben Muster wie die bestehenden Felder (Diff gegen die aktuelle
  effektive Metadata, PATCH nur der geänderten Felder, „Restore provider
  values" für aktive Overrides). Der Track-Diff wurde in die bereits
  getestete reine Funktion `computeTrackEditValues`
  (`-metadata-edit.ts`) integriert statt inline dupliziert.
- **Bulk-Edit (neu, B6-Lücke geschlossen):** `BulkEditTracksModal`, aus der
  Track-Tabellen-Bulk-Leiste („Bulk edit…"-Button neben Monitor/Write
  Tags/ReplayGain/Delete) — pro Feld ein Checkbox-Gate („Apply this to all
  selected tracks") statt Diff-gegen-Baseline (bei einer Mehrfachauswahl gibt
  es keine gemeinsame Baseline). Sendet pro Feld+Track denselben bestehenden
  Override-Endpoint parallel (`Promise.all`) — kein neuer Bulk-Endpoint
  nötig, identisch zum bereits etablierten Bulk-Muster (Monitor/ReplayGain
  laufen genauso pro Track parallel).

**Verifikation:** `pytest tests/library2` 585 grün (6 neue Tests: Importer
Album/Track-Style/Mood-Carry-over, Enrich-Resync-Erweiterung,
Override-Validierung neuer Felder inkl. `bool01`/`nonnegative_number`,
Read-Projection-Rundtrip über Artist/Album/Track inkl. Override-Overlay),
`tests/imports` unverändert (die 5 vorbestehenden Fails in
`test_simple_download_tags.py` sind unabhängig von dieser Änderung — Ursache
ist eine bereits vor dieser Session umbenannte `write_tags_to_file`-Funktion
in `core/imports/pipeline.py`, nicht berührt hier). `vitest` 192 grün (12 neue
Tests: 6 in `-metadata-edit.test.ts` für bpm/explicit/style/mood-Diffs, 2 in
`bulk-edit-tracks-modal.test.tsx`). `tsc --noEmit` und
`oxfmt --check`/`oxlint --type-check` über den gesamten `webui/src`-Baum
clean.

**Scope:** `core/library2/schema.py`, `core/library2/importer.py`,
`core/library2/enrich.py`, `core/library2/metadata_overrides.py`,
`core/library2/queries.py`, `webui/.../-library-v2.types.ts`,
`webui/.../-metadata-edit.ts`, `webui/.../-ui/library-v2-page.tsx`.

**Bewusst zurückgestellt (nicht Teil dieser Runde):** §45 (Reidentify) und
die „I Have This"-Hälfte von §51 — bei genauerem Blick auf die zugrunde
liegenden Module (`core/imports/rematch_*`, `core/library/missing_track_import.py`)
sind das KEINE dünnen ID-Mapping-Wrapper wie ursprünglich im Deep-Dive
angenommen: beide kopieren echte Dateien, laufen durch die
Post-Processing-Pipeline bzw. schreiben ein Hint-File fürs Re-Import, und
`missing_track_import.py` schreibt zusätzlich direkt (mit
hand-genrolltem ID-Vergabe-Schema) in die Legacy-`tracks`-Tabelle. Ein
sauberer lib2-natives Äquivalent braucht eine eigene fokussierte
Design-Session (insbesondere die Quellen-Auswahl-UX für „I Have This" —
woher kommt die Datei, wenn es noch keinen sichtbaren
„Unmapped Files"-Browser in lib2 gibt, siehe I9), keine Ad-hoc-Umsetzung in
derselben Session wie §48. A6/A7/C3/C4, G8-Vierter-Punkt und alle H/I-Punkte
bleiben aus denselben bereits dokumentierten Gründen unverändert offen.

---

## 35. §A6/C3 History-Read-Vereinheitlichung — ✅ umgesetzt (2026-07-16)

Der einzige Deep-Dive-Punkt aus der „braucht erst eine fokussierte
Recherche-Session"-Kategorie (§E, Punkt 9), der sich bei genauerem Hinsehen
doch sauber umsetzen ließ, ohne die dort befürchtete Fehlzuordnungsgefahr
einzugehen: `acquisition_requests.scope` (`recording`/`release_group`/
`release_edition`/`artist_missing`) ist tatsächlich nicht 1:1 mit einer
lib2-Artist-/Album-/Track-ID — aber `core/acquisition/catalog.py` löst genau
diese Beziehung für den Search-Pfad bereits korrekt auf (scope+entity_id →
lib2-Relationship-IDs). Statt einen zweiten Resolver zu bauen, läuft das neue
`core/library2/history_feed.py` dieselbe Beziehungskette rückwärts: lib2-ID →
passende `recording`/`release_group`/`release_edition`-IDs → passende
`acquisition_requests.id` → `acquisition_history`-Events. Kein neues Journal —
reine Read/JOIN-Schicht über vier bestehende Quellen:

- **`acquisition_history`** (via `acquisition_requests`): 26 Event-Typen auf
  Kategorien gemappt (grabbed/imported/failed/quarantined/blocklist), inkl.
  der bislang komplett toten `release_group`/`release_edition`/
  `artist_missing`-Scopes (heute erzeugt kein Code-Pfad sie — nur der
  generische `POST /acquisition/requests`-Endpoint akzeptiert sie — aber die
  Kontraktabdeckung ist jetzt vollständig, nicht nur für den heute einzig
  genutzten `recording`-Scope). `upgrade`-Scope bewusst ausgelassen: dafür
  existiert bis heute gar kein Erzeugungs-Pfad (keine `entity_type`-Konvention
  zum Testen), ihn zu resolven wäre reine Spekulation.
- **`lib2_entity_history`** (bestehendes `list_entity_history`-Muster, hier
  aber als eine gebündelte IN-Query statt N Einzelaufrufen pro Track):
  Canonical-Link/Relink/Unlink, File-Moves.
- **`lib2_file_delete_operations`** (ADR-05): direkt per `entity_type`/
  `entity_id` (`artist`/`release_group`) — Album-/Artist-Scope brauchten hier
  keine Übersetzung.
- **`lib2_manual_skips`**: per Primary-File-Pfad (dasselbe Muster wie der
  bestehende Track-Info-Tab), für Album/Artist über einen Join auf alle
  zugehörigen Tracks aggregiert statt N Einzelabfragen.
- **`track_downloads`**: wie bisher primär über `legacy_track_id` (rename-fest,
  Muster aus `source_info.py`), Pfad-Fallback für reine Autolink-Tracks ohne
  Legacy-ID; der bisherige Name-Match-Query bleibt als Legacy-Fallback NUR für
  Artist-Scope erhalten (fängt Downloads ohne jeden lib2-Track-Bezug ab, z.B.
  gelöschte/ersetzte Tracks) und dedupliziert gegen die ID-Treffer.

**Album-/Artist-Zuordnung** läuft über `lib2_album_artists` (nicht nur
`primary_artist_id`) — dasselbe bereits etablierte G8-Muster, damit
featured/verlinkte Alben nicht stillschweigend fehlen.

**Endpoint:** `GET /api/library/v2/artists/<id>/history` liefert jetzt
`{date, event_type, category, title, detail, source}` statt der alten
`track_downloads`-Rohspalten (Titel/Album/Quality/Bit-Depth). `scoped_history()`
selbst ist bereits für `artist`/`album`/`track`-Scope generisch nutzbar; nur
der Artist-Scope ist verdrahtet — Album-/Track-History-Buttons existieren in
der UI heute nicht, sie neu einzuführen wäre Scope über das eigentliche
Deep-Dive-Ziel (A6: die Artist-History-Ansicht ist zu eng) hinaus.

- **UI:** `HistoryModal` zeigt Date/Event/Detail/Source statt
  Date/Title/Album/Source/Quality/Status; Event-Zelle ist ein farbiger Chip
  (gelb=grabbed, grün=imported, rot=failed/deleted, orange=quarantined,
  blau=moved — Lidarr-Konvention wie im Deep-Dive vorgeschlagen) mit einem
  client-seitigen Kategorie-Filter-Dropdown (alle Events sind ohnehin schon
  geladen, kein Zusatz-Request nötig).

**Verifikation:** 10 neue gezielte Tests in
`tests/library2/test_history_feed.py` — der Kernpunkt ist Isolation: zwei
unabhängige Artists werden geseedet und jeder Scope-Typ (`recording`,
`release_group`, `artist_missing`) wird geprüft, dass ein Event NUR beim
richtigen Artist/Album/Track auftaucht und beim Nachbar-Entity NICHT (genau
die Fehlzuordnungsgefahr, vor der der Deep-Dive gewarnt hatte). Volle
`pytest tests/library2` (595 Tests, davon 10 neu) grün, `ruff check` clean,
`tsc --noEmit`/`oxfmt --check`/`oxlint --type-check` clean, `vitest`
(192 Tests) grün, Production-Build grün — alle Checks 2026-07-16 erneut
verifiziert.

**Scope:** `core/library2/history_feed.py` (neu), `api/library_v2.py`
(`lib2_artist_history`), `webui/.../-library-v2.api.ts`
(`LibraryV2HistoryEntry`/`LibraryV2HistoryCategory`),
`webui/.../-ui/library-v2-page.tsx` (`HistoryModal`),
`webui/.../-ui/library-v2-page.module.css` (Kategorie-Chip-Farben).

**Bewusst nicht Teil dieser Runde:** A7/C4 (Pipeline-Ergebnis-Persistenz pro
File) bleibt offen — das ist ein eigener, invasiverer Eingriff in den
Import-Callback der Haupt-Pipeline, nicht nur eine Read-Schicht wie C3, und
verdient laut Deep-Dive-Dokument eine eigene fokussierte Design-Session statt
in derselben Runde mitgezogen zu werden. G8-Vierter-Punkt und alle H/I-Punkte
bleiben aus denselben bereits dokumentierten Gründen unverändert offen.

---

## 36. §H1 Track-Playback — Legacy-Player wiederverwendet, kein neuer Player — ✅ umgesetzt (2026-07-16)

Nutzer-Vorgabe war explizit „übernimm den Legacy-Player, erfinde nichts
Neues" — library-v2 (React) und das Legacy-`library.js` laufen im selben
Browser-Fenster/`window` und teilen sich bereits eine Bridge
(`window.SoulSyncWebShellBridge`, gesetzt von `shell-bridge.js`). Diese Bridge
hat seit Kurzem schon eine vollständige, typisierte, in `stats-page.tsx`
bereits genutzte `playLibraryTrack()`-Methode, die 1:1 an Legacys eigene
Play-Funktion durchreicht (inkl. der bestehenden Media-Bar am unteren
Bildschirmrand, `/api/library/play`, Navidrome-Stream-Fallback — alles
unverändert). Für library-v2 gab es also buchstäblich nichts Neues zu bauen
außer einem Knopf, der diese Methode aufruft.

**Umsetzung:**
- Neue optionale Spalte **„Play"** in der Track-Tabelle (Options-Zahnrad,
  gleiches Muster wie Duration/BPM/File-Pfad aus §31/B5) — Default **aus**,
  da der Nutzer den Knopf explizit nicht dauerhaft sichtbar haben wollte
  (`core/library2/ui_preferences.py`: `track_table.columns.play: False`).
- `TrackPlayButton` (`library-v2-page.tsx`) ruft
  `getShellBridge()?.playLibraryTrack({id, title, file_path, bitrate,
  artist_id, album_id}, albumTitle, artistName)` — dieselben Felder, die die
  Zeile ohnehin schon im Payload hat, kein Zusatz-Request. Disabled + Tooltip
  „No file available", wenn Track keine Datei hat (identisch zur
  Legacy-Logik in `playLibraryTrack`/`col-play`).
- Kein neuer Endpoint, kein neuer Player-State, keine neue Player-UI.

**Bewusst nicht umgesetzt (H3, Discography-Batch-Auswahl):** ursprünglich
zusammen mit H1 als „größte funktionale Regressionen" geplant, vom Nutzer
nach kurzer Rückfrage explizit abgelehnt — kein Bedarf für eine
Mehrfachauswahl-UI auf dem „All Releases"-Tab. Bleibt als H3 in der
Feature-Gap-Enumeration stehen, aber ohne Umsetzungsabsicht.

**Verifikation:** `pytest tests/library2` (596 Tests, davon 1 neu für den
`play`-Spalten-Default) grün, `ruff check` clean, `tsc --noEmit`/
`oxfmt --check`/`oxlint --type-check` clean, `vitest` (195 Tests, davon 3 neu)
grün, Production-Build grün.

**Scope:** `core/library2/ui_preferences.py`, `tests/library2/test_ui_preferences.py`,
`webui/.../-library-v2.types.ts` (`LibraryV2TrackTableColumns.play`),
`webui/.../-ui/library-v2-page.tsx` (`TrackPlayButton`, `play`-Icon,
Spalten-Default/-Label), `webui/.../-ui/library-v2-page.module.css`
(`.colPlay`), `webui/.../-ui/track-play-button.test.tsx` (neu).

---

## 37. §A7/C4 Pipeline-Ergebnis-Persistenz pro File — ✅ umgesetzt (2026-07-16)

Der einzige verbliebene A/C-Punkt aus dem Deep-Dive (H/I brauchen weiterhin
erst Nutzer-Abstimmung, siehe Dokument-Kopf). Kernbefund beim Umsetzen: das
war nicht nur „mehr Detail nachrüsten" — die bereits gebaute
Verification-Badge-UI (§18.3, `TrackVerificationBadge`/`TrackLifecycleSection`)
war für **jede autolink-erzeugte Datei** (also den Normalfall: jeder fertige
Download, der über `link_download_into_library_v2` reinläuft) faktisch tot,
weil `verification_status` dort nie gesetzt wurde — nur legacy-importierte
Zeilen (`importer.py`) hatten je einen Wert. Reines Lesen hätte also nichts
gezeigt; der Fix musste am Schreibpunkt ansetzen.

**Root Cause:** `link_download_into_library_v2` (`core/library2/autolink.py`)
ist exakt der „eine Import-Callback", den C4 meint — jeder fertige Download
läuft hier durch, ob acquisition-korreliert oder nicht. Er setzte beim
INSERT/UPDATE der `lib2_track_files`-Zeile nie `verification_status` oder
`acoustid_status`, obwohl beide Spalten im Schema bereits existierten (die
erste seit Langem tot, die zweite von Anfang an nie beschrieben). Die Pipeline
berechnet den Wert (`context['_verification_status']`,
`core/matching/verification_status.py::status_for_import`) im selben
Funktionsaufruf, kurz bevor `record_download_provenance` (→ Autolink) läuft —
er ging schlicht beim Reichen durch die Callback-Grenze verloren.

**Umsetzung (Reuse-First, keine neue Architektur):**
- `core/imports/pipeline.py`: an allen vier Stellen, die
  `context['_acoustid_result']` setzen (Pass/Skip via `verify_audio_file`,
  „missing track/artist info", „not available", Exception), wird jetzt
  zusätzlich `context['_acoustid_message']` mit dem bereits vorhandenen
  Klartext-Grund gesetzt — sonst verschwindet der einzige Hinweis, WARUM
  AcoustID skip/disabled/error war, sobald die Funktion zurückkehrt. Am
  Downsample- bzw. Lossy-Copy-Fallback (`downsample_hires_flac`/
  `create_lossy_copy`, beide nur im Metadata-Enhancement-Pfad, nicht beim
  Simple-Download) wird bei Anwendung `context['_quality_fallback_downsample']`
  bzw. `_quality_fallback_lossy_copy'] = True` gesetzt.
- `core/library2/schema.py`: neue Spalte `lib2_track_files.pipeline_result_json`
  (`TEXT NOT NULL DEFAULT '{}'`, DDL + `_ADDED_COLUMNS`-Migration) für das
  kompakte Detail, das keine eigene Spalte verdient (AcoustID-Grund,
  Version-Mismatch-Fallback-Version, Quality-Fallback-Liste).
- `core/library2/autolink.py`: `link_download_into_library_v2` schreibt jetzt
  `verification_status` (aus `context['_verification_status']`),
  `acoustid_status` (aus `context['_acoustid_result']`, gemappt auf die
  schmalere Schema-Vokabel `'pass'|'skip'|None` — ein hartes `FAIL` erreicht
  diesen Callback nie, weil die Datei dann quarantiniert wird und die Pipeline
  vorher zurückkehrt) und `pipeline_result_json` (AcoustID-Message,
  `version_mismatch_fallback`, `quality_fallback`-Liste) — sowohl im
  INSERT- als auch im idempotenten UPDATE-Zweig (COALESCE für die
  Status-Spalten, damit ein Kontext ohne diese Keys eine zuvor bekannte
  Verification nicht stillschweigend löscht; `pipeline_result_json` wird pro
  Lauf frisch geschrieben, da es das Ergebnis DIESES Durchlaufs beschreibt).
- `core/library2/queries.py`: `file_info`-Payload um `acoustid_status` und
  geparstes `pipeline_result` (leeres Dict als Default) ergänzt.
- **UI:** `TrackVerificationBadge`-Tooltip hängt die AcoustID-Message an, wenn
  vorhanden; `TrackLifecycleSection` bekommt eine neue „Quality gate"-Zeile
  für angewandte Fallbacks („Hi-Res downsampled"/„Lossy copy created").

**Bewusst nicht Teil dieser Runde:** kein AcoustID-Score/Konfidenzwert (den
gibt es im Pipeline-Kontext nirgends als Zahl, nur die Klartext-Message);
keine Quarantäne-Referenz auf der File-Zeile (dafür existiert bereits der
History-Feed aus §35, der denselben Kontext scope-generisch liefert — eine
zweite Verlinkung wäre Redundanz, kein neuer Nutzen). G8-Vierter-Punkt
(`_find_or_create_artist`-Slow-Path/Alias-Risiko) bleibt offen — verdient
laut Deep-Dive-Dokument dieselbe eigene Recherche wie C3/C4 vor ihm, nicht
ungefragt nebenbei.

**Verifikation:** 5 neue gezielte Tests in `tests/library2/test_autolink.py`
(Insert- und Update-Pfad, `error`/`disabled` → keine Status-Behauptung,
Quality-Fallback- und Version-Mismatch-Detail im JSON) + 2 neue in
`tests/library2/test_queries.py` (Read-Seite: Werte kommen durch, Default ist
`{}`/`None` statt Roh-String). Volle `pytest tests/library2` (603 Tests,
davon 7 neu) grün, gezielt `pytest tests/imports tests/downloads/test_downloads_post_processing.py`
(698 von 703 grün — die 5 vorbestehenden Fails in
`test_simple_download_tags.py` sind unabhängig von dieser Änderung, per
Stash-Vergleich verifiziert: `write_tags_to_file` fehlt bereits auf `main`),
`ruff check` clean (ein vorbestehender S110-Fund in `queries.py:753`, nicht
von dieser Änderung berührt), `tsc --noEmit`/`oxfmt --check`/
`oxlint --type-check` clean, `vitest` (195 Tests) grün.

**Scope:** `core/imports/pipeline.py`, `core/library2/schema.py`,
`core/library2/autolink.py`, `core/library2/queries.py`,
`tests/library2/test_autolink.py`, `tests/library2/test_queries.py`,
`webui/.../-library-v2.types.ts` (`LibraryV2TrackFile.acoustid_status`/
`.pipeline_result`, neues `LibraryV2PipelineResult`),
`webui/.../-ui/library-v2-page.tsx` (`TrackVerificationBadge`,
`TrackLifecycleSection`, `QUALITY_FALLBACK_LABELS`).

---

## 38. §G8-Vierter-Punkt — `_find_or_create_artist` External-ID-Awareness — ✅ umgesetzt (2026-07-16)

Der letzte offene Punkt aus G8 (siehe library-v2.md §30): der Autolink-Pfad
kannte beim Artist-Matching weder `spotify_id` noch die §40-Alias-Gruppen und
scannte bei jedem Nicht-Exakt-Treffer die volle `lib2_artists`-Tabelle. Der
Deep-Dive hatte hierfür bewusst KEINE konkrete Fix-Richtung vorgegeben — eine
Auto-Erkennungs-Heuristik für Alias-Kandidaten wäre exakt das, was §40.1
explizit ausschließt (Fehlzuordnungsrisiko), und war deshalb nie das Ziel
dieser Runde.

**Umgesetzter Teil (sicher, ohne neue Heuristik):** ID-Matching als
zusätzliche, stärkere Signalquelle VOR dem Namens-Matching — dieselbe
Präferenz, die `_ArtistResolver` (§38-Provider-ID-Merge, `importer.py`) für
den Import-Pfad bereits etabliert hat, hier für den Autolink-Pfad
nachgezogen:
- `_primary_artist_spotify_id(ti)` liest `artists[0]["id"]` — aber NUR wenn
  `ti["provider"] == "spotify"` (dieselbe Gate-Regel wie beim bereits
  vorhandenen `spotify_track_id`), weil dasselbe Feld bei anderen Clients
  (JioSaavn, Amazon, …) provider-eigene, nicht-Spotify-IDs trägt, die niemals
  in die `spotify_id`-Spalte geraten dürfen.
- `_find_or_create_artist` prüft, falls eine Spotify-Artist-ID vorliegt,
  zuerst `WHERE spotify_id=?` (bereits indiziert —
  `idx_lib2_artists_spotify` existierte schon, keine Schema-Änderung nötig).
  Ein Treffer hier braucht keinen Namensvergleich — löst also genau den Fall,
  den ein reiner String-Vergleich strukturell nie lösen kann (kanji- vs.
  romaji-Credit derselben Provider-Identität, oder jede andere
  Formatierungs-Abweichung bei sonst identischer ID).
- Ein Namens-Treffer (Fast- oder Slow-Path, unverändert) BACKFILLT die
  mitgelieferte Spotify-ID auf die gematchte Zeile, sofern diese noch NULL
  ist (`AND spotify_id IS NULL` — überschreibt nie eine bereits bekannte,
  ggf. abweichende ID). Jeder Treffer über den Namenspfad macht die Zeile
  damit für das NÄCHSTE fertige Download ID-matchbar statt wieder auf den
  O(n)-Slow-Path angewiesen zu sein — die Perf-Beobachtung aus G8 entschärft
  sich dadurch mit der Zeit von selbst, ohne dass der Scan-Mechanismus selbst
  angefasst werden musste (SQLite hat kein `casefold()`; der Slow-Path bleibt
  der einzige Weg, Whitespace-/Unicode-Normalisierungsunterschiede zu fangen,
  die `lower()` nicht sieht — er wird nur zunehmend seltener gebraucht).
- Neu erstellte Artist-Zeilen persistieren die mitgelieferte Spotify-ID
  gleich beim `INSERT` (vorher: nie gesetzt, obwohl die Spalte seit Langem
  existiert — jede autolink-erzeugte Artist-Zeile war dadurch strukturell
  blind für künftige ID-Matches, unabhängig vom jetzigen Fix).

**Bewusst nicht Teil dieser Runde:**
- **Keine Alias-Erkennungs-Heuristik.** §40 bleibt bei manuellem Verlinken;
  dieser Fix ändert nichts an `resolve_alias_group`/Fan-out-Verhalten. Trifft
  ein Download eine bereits als Alias verlinkte Zeile (per Name oder ID),
  matcht er weiterhin auf GENAU diese Zeile — konsistent mit dem
  §40.4-Anzeigeprinzip „jede Zeile behält ihre eigenen Alben".
- **Kein Ersatz des Slow-Path-Scans** durch einen Schema-Umbau (z. B. eine
  normalisierte Namens-Spalte/Index). Die ID-Präferenz reduziert, wie oft der
  Scan überhaupt noch erreicht wird; ein struktureller Umbau des Scans selbst
  wäre eine eigene, größere Änderung ohne akuten Bedarf.
- Musicbrainz-/Deezer-IDs werden hier nicht ergänzt — im Autolink-Kontext
  liegt ausschließlich die Spotify-Artist-ID aus dem Track-Kontext vor
  (dieselbe Quelle, aus der auch `spotify_track_id`/`spotify_album_id` schon
  gelesen werden); ein Ausbau auf weitere Provider bräuchte erst eine eigene
  Quelle dafür im Pipeline-Kontext.

**Verifikation:** 6 neue gezielte Tests in `tests/library2/test_autolink.py`
(ID-Match trotz Namens-Drift, Backfill auf NULL, Backfill überschreibt nie
eine vorhandene ID, neue Zeile persistiert die ID, Provider-Gate für
`_primary_artist_spotify_id`, End-to-End über `link_download_into_library_v2`).
Volle `pytest tests/library2` (609 Tests, davon 6 neu) grün; gezielt
`pytest tests/imports tests/downloads/test_downloads_post_processing.py`
(698 von 703 grün — dieselben 5 vorbestehenden, unabhängigen Fails wie in
§37 dokumentiert). `ruff check` clean. Keine Frontend-Änderung (reiner
Backend-Fix), daher kein `vitest`/`tsc`-Lauf nötig.

**Scope:** `core/library2/autolink.py`, `tests/library2/test_autolink.py`.

---

## 39. Excel-like Resizable Columns (Spaltenbreiten veränderbar machen) — ❌ verworfen / aufgeschoben (2026-07-16)

Ein erster Prototyp für Excel-style Resizable Columns wurde entwickelt, um Spaltenbreiten in Artist- und Tracklist-Tabellen per Drag-and-Drop veränderbar zu machen und im `localStorage` zu persistieren. Das Feature wurde aufgrund von Layout- und Interaktionsmängeln verworfen. Der Code wurde per Hard-Reset gelöscht und die Anforderungen in das Backlog übertragen.

### Anforderungen für eine spätere Umsetzung:
- **Zieh-Verhalten (Spreadsheet-Konvention):** Das Resizen einer Spalte darf andere Spalten nicht automatisch stauchen oder dehnen. Spalten links von der Maus bleiben starr, Spalten rechts davon verschieben sich als Block, und das Gesamtlayout passt sich entsprechend an (mit Scrollbar-Unterstützung bei Überschreiten des Bildschirms).
- **Flexible Spalte:** Um Leerraum auf großen Monitoren zu vermeiden, füllt die jeweils letzte sichtbare Daten-Spalte (z.B. Dateipfad oder Added-Datum) den restlichen Platz dynamisch aus.
- **Persistence:** Speichern der vom Nutzer gezogenen Spaltenbreiten im `localStorage` des Browsers.
- **Visuelle Griffe:** Sichtbare Trennlinien an den Spaltengrenzen im Header, die bei Hover deutlicher hervorgehoben werden und einen breiteren Klickbereich (`~12px`) haben, um das Greifen zu erleichtern.
- **Minimum-Grenzen:** Mindestbreiten (ca. 40px) pro Spalte zur Vermeidung von Collapses, aber keine Maximum-Grenzen.

---

## 40. §P2-25/D4 Library-Import mit echtem Live-Fortschritt — ✅ umgesetzt (2026-07-17)

Der bei großen Bibliotheken irreführende statische Zustand „Importing…“ ist
durch einen durchgängigen, reconnect-fähigen Statusvertrag ersetzt:

- `import_legacy_library` meldet für Artists, Alben und Tracks jeweils
  `(stage, 0, total)` sowie garantiert `(stage, total, total)`. Die bisherigen
  200er-Zwischenstände bleiben erhalten, verwenden jetzt aber verarbeitete
  Zeilen statt nullbasierter Indizes.
- Tracklist-Precache fasst Cache- und Provider-Phase in EINEN monotonen
  Gesamtzähler. Tag- und Artwork-Precache melden auch unterhalb ihrer
  50er-/25er-Batchgrenzen Start und Abschluss; der Import-Worker reicht den
  Progress-Callback nun auch tatsächlich an Artwork weiter. Beim Wechsel der
  Stufen werden alte `current`-/`total`-Werte zurückgesetzt.
- `libraryV2ImportStatusQueryOptions` ist die gemeinsame Statusquelle aller
  Import-Buttons. Ein Seiten-Reload oder zweiter Button hängt sich damit an
  denselben laufenden Import; gepollt wird ausschließlich während
  `running=true`, ohne den falschen bisherigen Zehn-Minuten-Timeout. Ein
  vorübergehender Statusfehler bleibt sichtbar und React Query versucht die
  laufende Abfrage weiter.
- Die UI zeigt verständliche Stufen (`Importing artists`, `Resolving
  tracklists`, `Reading file tags`, `Caching artwork`), `current/total`, einen
  auf 0–100 geklemmten Prozentwert und eine native Progress-Bar. Der
  Abschluss nennt Artist-/Album-/Track-Zahlen.
- `window.location.reload()` ist entfernt. Nach Erfolg invalidiert der Client
  den bestehenden `LIBRARY_V2_QUERY_KEY`; Navigation, Filter und sonstiger
  React-Zustand bleiben erhalten.
- Die bereits im Worktree vorhandene Tooltip-Politur aus §18.6 bleibt
  erhalten: vorhandene und fehlende Dateitags erscheinen jetzt zeilenweise
  mit `✓`/`✗`, statt als schwer lesbare kommagetrennte Einzeile.

**Verifikation:** `pytest tests/library2` **615 passed**; Frontend Format,
Lint und Typecheck ohne Fehler/Warnungen; Vitest **34 Dateien / 198 Tests
passed**; Production-Build erfolgreich (nur der bekannte Main-Chunk-Hinweis).
Vier neue Backend-Verträge pinnen kleine/terminale und monotone
Fortschrittsmeldungen; fünf neue Frontend-Tests pinnen Formatierung, Reattach,
Start→Live-Status→Abschluss, terminale Fehler und Query-Invalidierung. Die Vollsuite deckte
zusätzlich fünf veraltete Frontend-Tests auf: Match-Chip-Tests laufen jetzt im
erforderlichen QueryClient-/Preferences-Harness, Quality-Tests prüfen den
aktuellen kompakten Statuspunkt-/Tooltip-Vertrag statt alter Text-Badges.

**Scope:** `core/library2/importer.py`, `completeness.py`, `tag_cache.py`,
`artwork.py`, `api/library_v2.py`, Library-v2-API/Page/CSS sowie die
zugehörigen Backend-/Frontend-Vertragstests.

---

## 41. P2-23/P2-24 — Download-Dispatch-Grenze + sichere Artist-Credits — ✅ umgesetzt (2026-07-17)

Zwei letzte kompakte Robustheitsreste aus §10.3 sind geschlossen:

- **P2-23 / eine Download-Verantwortung:** `DownloadEngine` besitzt jetzt auch
  `dispatch_download`. Der Resolver unterscheidet kanonische Source-Namen,
  Legacy-Aliase (z. B. `deezer_dl`) und echte Soulseek-Peer-Namen an derselben
  Grenze, die bereits Status und Cancel besitzt. `DownloadOrchestrator.download`
  delegiert nur noch. Der Engine-Katalog enthält zusätzlich bekannte, beim
  Start nicht initialisierbare Sources als `None`; ein expliziter Pick dieser
  Source liefert dadurch den korrekten Fehler und fällt nie still auf Soulseek
  zurück. Cross-Source-Download-Fallback wurde bewusst nicht erfunden, weil
  das übergebene `filename`/Target-ID source-spezifisch ist; Source-Auswahl und
  Candidate-Walk bleiben in der etablierten Pipeline.
- **P2-24 / keine erfundenen Artist-Identitäten:** Der liberale reine Parser
  `split_artist_credits` bleibt für belegte Listen erhalten. Der Importpfad
  legt darüber eine identity-sichere Grenze: ein unbekannter Credit mit
  mehrdeutigen Bestandteilen wie Komma, `&`, `and`, `/` oder `+` wird als
  vollständiger Anzeigename gespeichert. Aufgeteilt wird nur bei expliziten
  Kollaborationsmarkern (`feat.`, `ft.`, `featuring`, `with`, `x`, `vs`, `×`)
  oder wenn die Einzelteile bereits bekannte Artists sind. Titel-Features
  benutzen denselben Guard; ein durch `track_artist` bekannter Gast dient als
  Anker, sodass bestätigte Fälle wie `Wizkid & Kyla` weiterhin mehrwertig
  bleiben. Diese konservative Projektion ist verlustlos und kann später durch
  Provider-/Alias-Matching verfeinert werden; Phantom-Zeilen wären dagegen
  nicht zuverlässig rückrechenbar.

**Verifikation:** 62 Importer-Tests; das kombinierte Library-v2-/Download-
Engine-/Orchestrator-/Quality-Gate **687 passed, 2 skipped**; Ruff für alle
geänderten Python-Dateien. Die vollständige Python-Suite erreichte **10.302
passed, 3 skipped, 2 deselected**; ihre 25 Fehler liegen vollständig in drei
unberührten Testmodulen (`cross_batch_dedup`, `simple_download_tags`,
`normalize_version_symmetry`) und reproduzieren unverändert beim isolierten
Lauf dieser drei Dateien (**25 failed, 4 passed**).

---

## 52. Nutzer-Review 2026-07-17 — Profile, Monitoring, Search, History und Delete-UX

**Status: verbindliche Produktanforderung und Code-Audit; zwei vertikale
Slices umgesetzt, siehe §53/§54.** Dieser Abschnitt dokumentiert das Nutzerfeedback vom 2026-07-17
und ersetzt widersprechende ältere Annahmen, besonders die frühere
dreistufige Profilregel in §11 sowie die reine Gap-Enumeration in der
Deep-Dive-Doku. Ziel ist möglichst viel Wiederverwendung: Watchlist,
Wishlist, bestehende Search-/Download-/Import-Pipeline, Quality Profiles und
Quarantäne bleiben die fachlichen Systeme; Library v2 wird die verständliche,
zusammenhängende Oberfläche darüber und keine zweite Implementierung.

### 52.1 Angenommener Scope und klare Nicht-Ziele

Angenommen sind:

- nachvollziehbare Quality-Profile-Vererbung für Track, Album, Artist und
  Playlist;
- konsolidierte Artist-Monitoring-/Watchlist-Einstellungen in Library v2;
- bessere Provider-Zuordnung inklusive Artist-Foto und verständlicher
  Anzeige des aktuell gematchten Artists;
- Lidarr-vertraute Semantik für Automatic und Interactive Search;
- frühe Library-v2-Materialisierung bei der globalen Track-Suche;
- vollständige, korrelierte Search-/Download-/Check-/Quarantäne-/Import-
  History auf Entity- und File-Ebene;
- eine gemeinsame, verständliche Delete-Files-Oberfläche für Albumansicht
  und Manage Tracks.

Ausdrücklich **nicht** angenommen sind:

- **Kalender / Upcoming Releases:** nicht implementieren. Der unsichtbare
  Watchlist-Scanner darf weiterarbeiten; daraus folgt keine Kalender-UI.
- **Artist Top Tracks:** nicht in Library v2 übernehmen.
- **vollständige Legacy- oder Lidarr-Parität als Selbstzweck:** nur die in
  diesem Abschnitt angenommenen Funktionen werden gebaut.
- **gezieltes Re-Download-Modal:** vorerst zurückgestellt/nicht notwendig.
  Falls es später wieder aufgenommen wird, muss es neu suchen und die alte
  Datei erst nach einem erfolgreichen, verifizierten Import ersetzen.
- **Reidentify-vs.-„I Have This"-Reihenfolge:** weiterhin zurückgestellt; die
  Reihenfolge ist dem User derzeit egal.

### 52.2 Quality Profile: verbindliche Priorität und Herkunft

Für einen konkreten Track gilt von höchster zu niedrigster Priorität:

1. **explizites Track-Profil**;
2. **explizites Album-/Release-Profil**;
3. **explizites Artist-Profil**;
4. **Playlist-Default-Profil** der Playlist, über die der Track gewünscht
   wurde;
5. **app-weites Default-Profil** als technischer Fallback.

Eine niedrigere Ebene darf eine bereits gesetzte höhere Ebene nie
überschreiben. Insbesondere darf das Hinzufügen eines bereits bekannten Songs
zu einer Playlist dessen Track-, Album- oder Artist-Profil nicht verändern.
Ein Playlist-Profil ist ein Default für noch unentschiedene Tracks, kein
Massen-Override.

**Aktuelle Lücke:** `POST /api/library/v2/<entity>/<id>/quality-profile`
schreibt mit `cascade=true` dieselbe Profil-ID direkt auf Nachfahren.
`wanted.py`/`wishlist_mirror.py` projizieren anschließend primär die
Track-Spalte. Damit ist nicht mehr erkennbar, ob der Trackwert explizit
gewählt oder nur vom Album/Artist kopiert wurde. Eine korrekte Umsetzung
braucht daher einen gemeinsamen `effective_quality_profile`-Resolver samt
Herkunft, zum Beispiel:

```text
effective_profile = {
  id: <quality_profile_id>,
  source: track | album | artist | playlist | global,
  source_id: <entity id or null>,
  explicit: true | false
}
```

Die genaue DB-Repräsentation ist Implementierungsdetail, aber folgende
Invarianten sind verbindlich:

- explizite Werte und geerbte/effektive Werte dürfen nicht verwechselt
  werden;
- UI, Wishlist-Mirror, Automatic Search und Import-Quality-Gate benutzen
  denselben serverseitigen Resolver;
- das UI zeigt Profil **und Herkunft**, zum Beispiel „Lossless (Album)" oder
  „Standard (Playlist: Discover Weekly)";
- das Ändern einer höheren Ebene berechnet Nachfahren neu, ohne deren
  explizite Overrides zu löschen;
- `cascade` darf nicht länger blind explizite Kindwerte überschreiben;
- ein vorhandener Track in mehreren Wanted-Kontexten bleibt eine Entity und
  wird nicht pro Playlist dupliziert.

Der Playlist-Picker gehört in den bestehenden Sync-/Mirrored-Playlist-
Einstellungen zur jeweiligen Spotify-/Provider-Playlist. Library v2 liest
und verwendet diesen Wert; es soll weder ein paralleles Playlist-
Konfigurationsmodell noch eine zweite Quality-Profile-Tabelle geben.

**Umsetzungsstand 2026-07-17:** Track→Album→Artist→Global ist mit expliziter
Herkunft, zentralem Resolver, schonender Bestandsmigration, Clear-to-Inherit,
Pipeline-Verwendung und UI-Anzeige umgesetzt (§53). Die Playlist-Stufe bleibt
bewusst offen, weil ihre gleichrangige Konfliktregel weiterhin §52.12.1
unterliegt.

**Noch offene Produktentscheidung:** Befindet sich derselbe Track in zwei
Playlists mit unterschiedlichen Profilen und existiert kein Track-, Album-
oder Artist-Profil, kollidieren zwei gleichrangige Playlist-Defaults. Dafür
ist vor Umsetzung eine deterministische Regel nötig (siehe §52.12); „letzter
Write gewinnt" darf nicht versehentlich das Verhalten bestimmen.

### 52.3 Ein Monitoring-Modell statt mehrerer unklarer Schalter

Heute liegen ähnliche Einstellungen an mehreren Orten:

- das Bookmark neben dem Artist-Namen spiegelt Artist ⇄ Watchlist;
- das Library-v2-Monitoring-Modal bietet sinngemäß „all releases",
  „missing only", „unmonitor everything" und die Behandlung neuer Releases;
- die bestehenden Watchlist Artist Settings führen zusätzlich
  `auto_download`, Lookback, Release-Typen, bevorzugte Metadatenquelle und
  das manuelle Provider-Linking;
- Track-/Album-Monitoring spiegelt weiterhin in die Wishlist.

Diese Zustände dürfen nicht als vier unabhängige Monitoring-Systeme
weiterentwickelt werden. Die fachliche Bedeutung soll lauten:

- **Bookmark gesetzt:** Der Artist ist Mitglied der Watchlist und damit als
  Artist monitored. Bookmark entfernen nimmt ihn aus der Watchlist, löscht
  aber weder Library-Dateien noch automatisch explizit gemonitorte Tracks.
- **Artist Settings:** bestimmen, welche neuen Releases beobachtet werden
  und ob passende neue Releases automatisch in die vorhandene
  Search-/Download-Pipeline gehen.
- **Release-/Track-Monitoring:** bestimmt, welche konkreten vorhandenen oder
  fehlenden Items aktuell wanted sind. Ein einzelner Wishlist-Track monitored
  nie automatisch die gesamte Discography seines Artists.
- **Quality Profile:** ist orthogonal zum Monitorstatus. Es entscheidet die
  erlaubte Qualität und Upgrades, nicht ob überhaupt gesucht wird.

Neben Bookmark/Artist-Name erscheint ein Settings-/Gear-Icon, sobald der
Artist gebookmarkt ist. Es öffnet in Library v2 die **bestehenden Watchlist
Artist Settings** als gemeinsame Komponente bzw. über denselben API-Vertrag,
nicht eine reduzierte Kopie. Dort müssen mindestens verfügbar sein:

- Quality Profile für den Artist;
- Auto-download new releases an/aus;
- Release-Typen: Albums, EPs, Singles sowie die bereits unterstützten Live,
  Remixes, Acoustic, Compilations und Instrumentals;
- Lookback/ab welchem Zeitraum neue Releases berücksichtigt werden;
- bevorzugter Metadatenprovider;
- aktuell verknüpfter Provider-Artist und manuelles Re-Matching.

Das bestehende Library-v2-Monitoring-Modal wird in diese Semantik integriert
oder durch sie ersetzt. Begriffe wie „Monitor all releases" und „Monitor
missing only" müssen im UI erklären, welche **bestehenden** Releases/Tracks
sie markieren; die Watchlist-Einstellungen erklären getrennt, was bei
zukünftigen Releases passiert. Das Bookmark braucht einen Tooltip, der seine
Watchlist-Wirkung explizit nennt.

**Umsetzungsstand 2026-07-17:** Diese Konsolidierung ist umgesetzt (§54).
Ein Gear neben gebookmarkten Artists öffnet die bestehende administrative
Watchlist-Konfiguration zusammen mit dem app-weiten Quality-Profile-Picker
und dem bestehenden Provider-Match-UI. Die getrennten Artist-Toolbar-Aktionen
„Monitoring" und „Profile" sind entfernt. Aktionen auf vorhandene Releases
bleiben im selben Dialog sichtbar, aber sprachlich und technisch von Regeln
für zukünftige Releases getrennt.

### 52.4 „Metadata Profile" nicht als drittes Profilsystem nachbauen

In Lidarr beschreibt ein Metadata Profile vor allem, welche Release-Gruppen
und Release-Status in die Discography aufgenommen werden. In SoulSync decken
die vorhandenen Watchlist-Felder `include_*` plus `monitor_new_items` einen
großen Teil dieser Absicht bereits ab. Deshalb wird **vorerst kein separates
Metadata-Profile-Modell** eingeführt.

Stattdessen zuerst:

1. Watchlist-Release-Typen und Library-v2-Monitoring fachlich konsolidieren;
2. dieselben Werte in Artist Settings sichtbar/editierbar machen;
3. prüfen, welche Lidarr-Release-Status danach tatsächlich noch fehlen.

Nur wenn wiederverwendbare benannte Presets später einen klaren Mehrwert
haben, ist ein eigenes Metadata-Profile-Konzept erneut zu entscheiden. Es
darf nicht neben Watchlist-Regeln und `monitor_new_items` eine dritte,
widersprüchliche Wahrheit erzeugen.

**Umsetzungsstand 2026-07-17:** Die vorhandenen `watchlist_artists.include_*`-
Felder, Lookback-, Auto-Download- und Provider-Werte werden direkt gelesen und
geschrieben; `monitor_new_items` bleibt die einzige lib2-spezifische
Discography-Regel. Es wurde weder eine parallele Settings-Tabelle noch ein
Metadata-Profile-Modell eingeführt (§54).

Referenz zur Begriffsklärung: [Lidarr Library / Metadata
Profile](https://wiki.servarr.com/lidarr/library).

### 52.5 Artist Matching und Artist-Darstellung

Library v2 soll den bereits reicheren Watchlist-Providerpfad wiederverwenden.
Beim aktuellen und beim manuellen Match werden nicht nur Codes/IDs gezeigt,
sondern – soweit der Provider Daten liefert –:

- Artist-Name und Provider;
- Artist-Profilbild;
- Provider-ID als sekundäre technische Information mit Copy-Aktion;
- Follower-/Fan-Zahl, Popularität und Genres;
- Match-Status bzw. ob die Zuordnung manuell oder automatisch entstand.

Der aktuell verknüpfte Artist muss schon vor einer neuen Suche als
verständliche Karte/Zeile sichtbar sein. Match-Kandidaten verwenden dieselbe
Darstellung, damit der User nicht nur zwischen anonymen Codes entscheidet.
Das bestehende Library-v2-Identity-Modell bleibt maßgeblich; die Watchlist-UI
liefert Daten und Interaktionsmuster, keine zweite Identitätstabelle.

**Entschiedene Artwork-Reihenfolge für Artists:** Provider-Artist-Foto zuerst
→ Embedded-Albumcover nur als Fallback → bestehender Cache/Placeholder. Das
ersetzt die heutige Reihenfolge aus §11.2. Fehlende Follower-/Fan-Zahlen sind
kein Fehler, wenn der gewählte Provider sie nicht anbietet; das UI nennt dann
die Datenquelle bzw. zeigt keinen erfundenen Nullwert.

**Umsetzungsstand 2026-07-17:** Die Artwork-Reihenfolge ist umgesetzt und
getestet (Providerfoto vor Embedded-Albumcover, manuelles Override bleibt
weiterhin höchste Priorität). Die reichere Current-Match-/Candidate-Karte mit
Followern/Genres bleibt offen.

### 52.6 Automatic Search: erwartetes Lidarr-Verhalten

Automatic Search ist immer auf den gewählten Artist, das Album oder den Track
gescoped und benutzt die bestehende Wishlist-Candidate-/Quality-/Download-
Pipeline.

Für fehlende Dateien:

- Artist-/Album-Suche berücksichtigt nur monitored/wanted Tracks;
- es wird der beste zulässige Kandidat nach dem effektiven Quality Profile
  gewählt und durch alle bestehenden Checks geführt.

Für bereits vorhandene Dateien:

- nur suchen/grabben, wenn das effektive Profil ein Upgrade erlaubt;
- nur einen Kandidaten laden, der nach derselben serverseitigen
  Qualitätsbewertung besser als die aktuelle Datei ist und das Profilziel
  erfüllt;
- wenn Upgrade nicht erlaubt ist oder kein besserer zulässiger Kandidat
  existiert: **nichts verändern**;
- die bestehende Datei erst ersetzen/löschen, nachdem der neue Download alle
  Quality-, Acoustic-ID-, Tagging- und Import-Checks erfolgreich bestanden
  hat. Ein Fehler oder eine Quarantäne lässt die alte Datei unangetastet.

Eine **direkt am einzelnen Track** manuell gestartete Automatic Search ist
expliziter Nutzer-Intent und läuft auch dann, wenn dieser Track nicht
monitored ist. Sie setzt den Monitorstatus nicht stillschweigend um. Das ist
anders als die Artist-/Album-/globale Suche, die weiterhin nur monitored/
wanted Items verarbeitet.

**Aktuelle Lücke:** Der scoped Endpoint berechnet Wanted-State neu und
filtert anschließend auch einen explizit ausgewählten unmonitored Track weg.
Dieser Spezialfall muss als expliziter Track-Override bis zur
Eligibility-Grenze getragen werden, ohne die globalen Monitoring-Regeln
aufzuweichen.

**Umsetzungsstand 2026-07-17:** Diese konkrete Lücke ist geschlossen. Eine
direkte Track-Suche erzeugt einen einmaligen, idempotenten Wishlist-Add und
dispatcht den Track, ohne `monitored` oder die Wanted-Projektion zu ändern;
Artist-/Album-Suchen bleiben weiterhin strikt wanted-only (§53).

### 52.7 Interactive Search und Manual Grab

Interactive Search zeigt Kandidaten und überlässt die Auswahl dem User, führt
den gewählten Kandidaten danach aber durch **dieselbe** Download-/Import-
Pipeline wie Automatic Search. Die UI trifft keine eigene Quality-
Entscheidung und darf einen bestehenden File-Row nicht vor erfolgreichem
Import überschreiben.

Ein Manual Grab bedeutet nicht automatisch „alle Checks ignorieren":

- Quality Profile und Acoustic-ID werden normal geprüft;
- ein Hard-Fail muss mit Grund in Quarantäne/History landen;
- `Force Download` und `Skip Acoustic ID Verification` sind explizite,
  sichtbare Overrides und werden mit Actor, Zeitpunkt und Grund/Quelle
  protokolliert;
- ein späteres „Approve / Human Verified / Tag + DB" aus der Quarantäne wird
  als manueller Entscheidungsschritt an denselben Versuch angehängt;
- bei einem Replacement bleibt die alte Datei bis zum erfolgreichen finalen
  Import bestehen.

**Noch zu entscheiden:** Ob Interactive Search Kandidaten außerhalb des
Quality Profiles grundsätzlich nur anzeigen, anzeigen aber blockieren oder
nach einer zweiten expliziten Force-Bestätigung erlauben soll (§52.12). Die
Pipeline darf den Fall bis dahin nicht stillschweigend akzeptieren.

### 52.8 Globale Search-Seite → frühe Library-v2-Materialisierung

Im bestehenden Search-Flow wählt der User zuerst Provider/Resultat und danach
Aktionen wie „Begin Analysis", „Add to Wishlist", „Force Download" und
„Skip Acoustic ID Verification". Dieser Flow erhält einen Quality-Profile-
Picker.

Sobald der User die gewählte Aktion verbindlich abschickt, muss der Server
**vor Search/Download** idempotent:

1. den richtigen Artist, das Release/Album und den Track anhand stabiler
   Provider-IDs in Library v2 auflösen oder anlegen;
2. den Track unter den richtigen Parents verknüpfen, ohne Artist-/Track-
   Duplikate zu erzeugen;
3. das ausgewählte Profil als **explizites Track-Profil** speichern (höchste
   Priorität);
4. den konkreten Track explizit monitoren/wanted setzen und über den
   bestehenden Mirror in die Wishlist bringen;
5. Library-v2-Entity-IDs, Profilherkunft und eine gemeinsame Correlation-ID
   an Analysis, Grab, Quarantäne und Import weiterreichen.

Das Anlegen darf nicht erst im finalen Autolink nach einem erfolgreichen
Import passieren. Sonst existiert bei einem frühen Quality-/Acoustic-ID-Fail
keine zuverlässige Library-v2-Entity, an der der Versuch und die Quarantäne
sichtbar werden. Der heutige Search-Request führt außerdem noch keine
`quality_profile_id`; diese Lücke ist Teil dieses Scopes.

**Verbindlicher allgemeiner Vertrag:** Das gilt nicht nur für die globale
Search-Seite. Jeder Eingangspfad, der einen bestätigten Track-/Release-Intent
in die Wishlist oder Acquisition schreibt — insbesondere Search-Aktionen,
Playlist-Sync, Watchlist-Scanner, direkte Track-Suche und manuelle Wishlist-
Aktionen — muss denselben idempotenten Materialisierungs- und Profilresolver
verwenden. Die lib2-Entity muss nach diesem bestätigten Write sofort lesbar
sein, auch wenn noch keine Datei existiert und der spätere Download scheitert,
quarantänisiert oder nie gestartet wird. Ein unverbindlicher Klick auf ein
Suchresultat ohne bestätigten Wishlist-/Acquisition-Write materialisiert noch
nichts.

Das explizite Track-Monitoring weitet sich standardmäßig **nicht** auf alle
Releases des Artists. Der Artist wird als korrekter Parent angelegt; die
gesamte Artist-Watchlist wird nur über das Bookmark/Artist Settings aktiviert.
Falls mit „beim richtigen Artist auf Monitor" stattdessen zwingend die ganze
Artist-Watchlist gemeint ist, ist das vor Implementierung als offene
Entscheidung in §52.12 zu bestätigen.

### 52.9 Pipeline-Ergebnis, Quarantäne und History dürfen nicht verloren gehen

Der Code-Audit zeigt heute:

- Quality-/Acoustic-ID-Hard-Fails können vor dem finalen Library-v2-Autolink
  in Quarantäne gehen;
- Acquisition History journalisiert Quarantäne und manuelle Auflösung;
- `pipeline_result_json`/Verification wird primär an einer erfolgreich
  verknüpften finalen File-Zeile persistiert;
- die Artist-History merged mehrere Quellen, die Track-Info-Ansicht zeigt
  jedoch im Wesentlichen File-/Source-Zusammenfassung und nicht die gesamte
  Timeline eines fehlgeschlagenen Versuchs.

Dadurch sind die Daten teilweise vorhanden, aber nicht stabil an derselben
Track-/File-Ansicht korreliert. Der Zielvertrag ist ein durchgängiger Versuch
mit einer Correlation-ID von Search/Watchlist bis zum Endzustand:

```text
search_requested
→ candidates_evaluated
→ candidate_selected / manual_grab
→ quality_checked
→ acoustic_id_checked
→ download_started / download_finished
→ quarantined [optional]
→ human_verified / rejected / retried [optional]
→ imported
→ previous_file_replaced [upgrade only]
```

Jeder Schritt trägt mindestens Zeit, Entity-Scope, Actor (`system`/User),
Quelle/Kandidat, Entscheidung, strukturierten Grund und relevante Vorher-/
Nachher-Qualität. Nicht ausgeführte Checks werden als `not_run`/`skipped` mit
Grund unterschieden, nicht durch fehlende Daten suggeriert.

Die History soll Lidarr-vertraut Search/Grab, Download, Import, Upgrade,
Delete, Rename/Move, Failure und Quarantäne in einer chronologischen Sicht
zeigen. Die bestehende History-Zusammenführung wird dafür erweitert, nicht
durch ein separates UI-only-Protokoll ersetzt. Referenz zum erwarteten
Informationsumfang: [Lidarr Activity / History](https://wiki.servarr.com/lidarr/activity).

### 52.10 Track-Detail: Edit/Settings statt reinem Info-Icon

Das Track-Modal bearbeitet Quality Profile, Metadata, Tags und Lyrics und
zeigt zusätzlich Info. Deshalb soll der Tabellen-Button wieder als
**Pencil/Edit oder Settings** erscheinen, nicht als reines Info-Icon. Diese
Nutzerentscheidung ersetzt die frühere D1-Entscheidung aus §33.

Die Tabs bleiben fachlich erhalten, werden aber ergänzt:

- **Info/Pipeline:** chronologischer Stepper mit allen Checks, Quarantäne,
  manueller Freigabe und Replacement;
- **Tags:** dieselbe lesbare, gruppierte Key/Value-Formatierung wie
  „Quarantine → Inspect → Tags", statt einer schlechter formatierten
  Parallelansicht;
- **Quality:** effektives Profil plus Herkunft und expliziter Override;
- **Metadata/Lyrics:** weiter editierbar wie heute.

Vom Track aus müssen auch fehlgeschlagene Versuche sichtbar sein, die nie eine
neue `lib2_track_files`-Zeile erreicht haben. Vom File aus wird zusätzlich
der konkrete erfolgreiche Import-/Verification-Zustand angezeigt.

**Umsetzungsstand 2026-07-17:** Pencil/Edit-Icon sowie Quality-Profil inkl.
Herkunft und „Use inherited profile" sind umgesetzt. Der chronologische
Pipeline-Stepper und die gemeinsame Quarantine-Tag-Formatierung bleiben offen.

### 52.11 Manage Tracks und Delete Files: ein gemeinsamer Dialog

„Manage Tracks → Delete Selected" ist die kanonische Oberfläche für
Dateiverwaltung. Eine Delete-Files-Aktion direkt am Album darf als Shortcut
bleiben, öffnet aber **denselben** Dialog und denselben Backend-Vertrag; es
gibt keine zweite Delete-Implementierung.

Der aktuelle Dialog stapelt „aus DB entfernen" und „physische Dateien
löschen" mit langen Rohpfadlisten in einer schwer lesbaren Darstellung. Der
neue Dialog zeigt zuerst eine kompakte Auswahlzusammenfassung (Tracks,
Dateien, Gesamtgröße) und genau eine verständliche Wahl:

- **nur aus der Library-Datenbank entfernen** – Dateien auf Disk behalten;
- **permanent löschen** – Library-Einträge und die dazugehörigen Dateien auf
  Disk entfernen.

Für die permanente Option folgt eine deutliche Destructive-Bestätigung. Die
Backend-Kommandos und Journale aus ADR-05 bleiben getrennt und sicher, auch
wenn die UI sie als einen zusammenhängenden Entscheidungsflow präsentiert.

Lange Pfade:

- stehen nicht als ungebrochene Volltextwand im Hauptdialog;
- werden sinnvoll in der Mitte gekürzt, mit vollständigem Pfad in Tooltip,
  Reveal und Copy;
- liegen in einer scrollbaren/aufklappbaren Liste, nach Root/Album gruppiert;
- verändern nicht die Breite des Modals.

Die History erfasst Modus, betroffene File-IDs/Pfade, Actor, Erfolg/Fehler und
bei Permanent Delete den tatsächlichen Disk-Ausgang. Als Referenz für die
einheitliche Entscheidung dient der offizielle Lidarr-Flow „Remove from
Lidarr only" vs. „Remove from Lidarr and delete files": [Lidarr Tips and
Tricks](https://wiki.servarr.com/en/lidarr/tips-and-tricks).

**Umsetzungsstand 2026-07-17:** Der gemeinsame Dialog und Backend-Vertrag sind
umgesetzt (§54). Album-/Artist-Delete und „Manage Tracks → Delete Selected"
verwenden dieselbe Komponente. DB-only bleibt auch bei unsicheren Pfaden
möglich; Permanent Delete bleibt fail-closed, erfordert die destruktive
Bestätigung und löscht die Entity erst nach vollständig erfolgreichem
Dateikommando. Beide Modi werden samt Actor/Profil getrennt historisiert.

### 52.12 Entscheidungen, die vor der Umsetzung noch bestätigt werden müssen

Die Grundrichtung ist entschieden. Offen bleiben nur Punkte, bei denen eine
technische Annahme sichtbares Nutzerverhalten festlegen würde:

1. **Zwei Playlist-Profile auf demselben Track:** Soll das strengere Profil,
   eine explizit gewählte Primär-Playlist oder eine andere sichtbare Regel
   gewinnen? Empfehlung: „strengstes erreichbares Profil" nur verwenden,
   wenn Quality-Ränge profilübergreifend eindeutig vergleichbar sind;
   ansonsten Konflikt anzeigen und explizite Auswahl verlangen.
2. **Search-Materialisierungszeitpunkt:** ✅ entschieden. Sobald ein
   bestätigter Search-, Playlist-, Watchlist- oder anderer Acquisition-Intent
   tatsächlich in die Wishlist/Acquisition geschrieben wird, materialisiert
   der Server Artist, Release und Track idempotent **vor** Search/Download und
   mit dem aufgelösten expliziten Profil. Ein unverbindlicher Klick auf ein
   Suchresultat materialisiert weiterhin nichts. Die technische Umsetzung
   bleibt §52.8; die Produktentscheidung ist nicht mehr offen.
3. **Artist-Monitoring bei globaler Track-Suche:** Empfehlung: nur den Track
   monitoren und den Artist als Parent anlegen; die gesamte Watchlist nur bei
   gesetztem Bookmark. Bestätigen, ob der User stattdessen automatisch den
   ganzen Artist in die Watchlist aufnehmen will.
4. **Manual Grab außerhalb des Profils:** nur anzeigen, anzeigen aber
   blockieren oder nach explizitem Force-Override erlauben? Empfehlung:
   anzeigen mit rotem Fail-Grund und nur über eine separat bestätigte,
   auditierte Force-Aktion zulassen.
5. **Replacement-Aufbewahrung:** technisch ist entschieden, dass die alte
   Datei bis zum erfolgreichen Import bestehen bleibt. Noch festzulegen ist,
   ob sie danach sofort permanent gelöscht, in einen konfigurierbaren
   Backup/Trash verschoben oder für eine kurze Rollback-Frist behalten wird.

Alle übrigen Aussagen dieses Abschnitts sind Anforderungen, keine offenen
Fragen. Vor Implementierung sollte daraus ein vertikaler Arbeitsplan mit
Schema-/API-Migration, zentralem Profilresolver, Early-Materialization,
Correlation-History und anschließend den gemeinsamen UI-Komponenten erstellt
werden.

---

## 53. §52 erster vertikaler Slice — Profilherkunft, Artist-Artwork, direkte Track-Suche und Edit-UX — ✅ umgesetzt (2026-07-17)

Dieser Slice schließt alle entscheidungsfreien Kernteile, die sich ohne eine
der fünf offenen Produktfragen aus §52.12 belastbar implementieren ließen.

### 53.1 Quality Profile: explizit vs. geerbt

- `lib2_artists`, `lib2_albums` und `lib2_tracks` tragen neu
  `quality_profile_explicit`. Die weiterhin persistierte Profil-ID ist die
  effektive Kompatibilitätsprojektion für älteren Code; das Flag hält fest,
  ob die Entity die Wahl wirklich besitzt.
- Die additive Migration legt das Flag auf Bestandsinstallationen zuerst als
  NULL an und inferiert die frühere Kaskade genau einmal: gleicher Wert wie
  Parent = geerbt, abweichender Wert = explizites Override; am Artist-Root
  gilt ein vom App-Default abweichender Wert als explizit. Gleichwertige alte
  Overrides waren im alten Schema nicht rekonstruierbar und bleiben deshalb
  konservativ geerbt.
- `core/library2/profile_lookup.py::effective_quality_profile` ist der
  gemeinsame Resolver für **Track > Album > Artist > Global** und liefert
  `{id, source, source_id, explicit}`. Ein DB-Trigger projiziert einen später
  geänderten App-Default sofort auf alle nicht expliziten Artists, Albums und
  Tracks; explizite Kinder bleiben dabei gepinnt.
- `assign_quality_profile` setzt oder entfernt (`inherit=true`) ein Override.
  Parent-Änderungen aktualisieren nur geerbte Nachfahren; explizite Album- und
  Track-Werte werden niemals überschrieben. Das alte `cascade`-Feld bleibt als
  Request-Kompatibilität erhalten, bedeutet aber nicht mehr „blind alle
  Kinder überschreiben".
- Wishlist-Payload, Wanted-Projektion, scoped Automatic Search und damit das
  Import-Quality-Gate erhalten dieselbe effektive Profil-ID. Wishlist-
  `source_info` führt zusätzlich Herkunft und Source-ID mit.
- API-Payloads und React-UI zeigen Profil plus Herkunft, z. B.
  `Lossless (Album)`. Der Picker zeigt den effektiven Ursprung und bietet
  „Use inherited profile" für explizite Overrides. Wishlist-importierte
  Track-Profile werden als explizite Track-Wahl markiert.
- Der React-Picker setzt `monitor_existing=false`: eine Profiländerung ist
  gemäß §52.3 keine versteckte Wanted-/Monitoring-Aktion. Der bestehende
  serverseitige Opt-in bleibt für explizite andere Workflows verfügbar.

Die Playlist-Stufe ist absichtlich nicht geraten worden. Persistenz am
bestehenden Mirrored-Playlist-Modell und Konfliktregel bei zwei Playlists
folgen erst nach §52.12.1.

### 53.2 Drei weitere geschlossene §52-Punkte

- **Artist-Artwork (§52.5):** manuelles Override bleibt zuerst; danach wird
  das Provider-Artist-Foto versucht und nur bei fehlendem/fehlerhaftem Bild
  auf Embedded-Art eines Albums zurückgefallen. Der bestehende JPEG-/Thumb-
  Cache bleibt unverändert.
- **Direkte Track-Suche (§52.6):** ein Klick am einzelnen Track darf nun auch
  einen unmonitored Track suchen. Das ist ein einmaliger Wishlist-/Dispatcher-
  Intent und ändert weder Monitorflag noch Wanted-Regel. Artist-/Album-Scope
  bleibt monitored/wanted-only; vorhandene Dateien bleiben weiterhin durch
  dieselbe Upgrade-/Quality-Bewertung geschützt.
- **Track-Detail (§52.10):** der Tabellenbutton verwendet wieder das
  Pencil/Edit-Icon und benennt im Tooltip den tatsächlichen bearbeitbaren
  Umfang. Im Quality-Tab sind effektives Profil, Herkunft und Clear-to-Inherit
  sichtbar.

### 53.3 Verifikation und verbleibender Scope

- Backend: `pytest tests/library2` → **631 passed**.
- Frontend: Vitest → **34 Dateien / 199 Tests passed**.
- Frontend Format + OXLint-Typecheck → **0 Warnungen / 0 Fehler**; TypeScript
  `--noEmit` ebenfalls grün.

§52.3/§52.4 und §52.11 wurden im anschließenden Slice §54 geschlossen; §52.8
Early Materialization wurde (mit Ausnahme der legacy Search-/Download-Routen)
im Slice §55 geschlossen. Weiterhin offen und nicht als erledigt dargestellt:
die reichere Provider-Match-Karte aus §52.5 (Follower/Popularität/Match-
Provenienz), Replacement-Teil von §52.6, §52.7 Manual-Grab-Policy und §52.9
Correlation-History/Stepper. Die fünf sichtbaren Entscheidungen aus §52.12
bleiben unverändert Voraussetzung für ihre jeweiligen Teilstücke.

---

## 54. §52 zweiter vertikaler Slice — gemeinsame Artist Settings und Datei-Entfernung — ✅ umgesetzt (2026-07-17)

Dieser Slice schließt die zwei größten verbleibenden entscheidungsfreien
UI-/Vertragspakete aus §52: eine fachliche Artist-/Watchlist-Konfiguration
ohne paralleles Settings-Modell sowie einen gemeinsamen, auditierbaren
DB-only/permanenten File-Removal-Flow.

### 54.1 Artist Settings: eine Oberfläche, vorhandene Wahrheiten

- `GET/PUT /api/library/v2/artists/<id>/settings` löst den lib2-Artist über
  Provider-IDs und als konservativen Fallback über den Namen auf die
  **bestehende administrative** `watchlist_artists`-Zeile auf. Der Vertrag ist
  bewusst admin-authoritativ und erzeugt keine zweite Settings-Zeile.
- Release-Filter (`include_albums`, `include_eps`, `include_singles`, Live,
  Remixes, Acoustic, Compilations, Instrumentals), `auto_download`, Lookback
  und bevorzugte Metadata-Source bleiben auf `watchlist_artists`.
  `monitor_new_items` bleibt auf `lib2_artists`, weil es die lib2-Discography-
  Re-Expansion steuert. Ein geänderter Lookback setzt den vorhandenen
  Scan-Marker zurück; Eingaben und mindestens ein Kern-Release-Typ werden
  serverseitig validiert.
- `ArtistSettingsModal` zeigt Watchlist-Identität, Provider-IDs und den
  vorhandenen manuellen Re-Match-Flow, verwendet den app-weiten
  `QualityProfilePicker` und trennt klar zwischen Regeln für **zukünftige**
  Releases und Monitor-/Wanted-Aktionen für **bereits bekannte** Releases.
- Das Gear erscheint nur bei gebookmarkten/monitored Artists. Die bisherigen
  separaten Toolbar-Aktionen „Monitoring" und „Profile" sind dort entfernt;
  der Bookmark-Tooltip benennt die Watchlist-Wirkung explizit. Bei einem
  **nicht** gebookmarkten Artist bleibt der eigenständige Profile-Button
  erreichbar, weil Quality laut §52.3 orthogonal zum Monitoring ist und noch
  keine Watchlist-Zeile für die übrigen Settings existiert.
- Die reichere Kandidatenkarte aus §52.5 ist nur teilweise abgedeckt:
  Foto/Name/Genres/Provider-IDs und bestehendes Re-Matching sind integriert;
  Follower, Popularität und automatische-vs.-manuelle Match-Provenienz bleiben
  offen, bis der jeweilige Provider diese Daten zuverlässig liefert.

### 54.2 Einheitlicher File-Removal-Flow

- `UnifiedFileRemovalDialog` wird sowohl vom Artist-/Album-Delete-Shortcut als
  auch von „Manage Tracks → Delete Selected" verwendet. Er zeigt Track-/File-
  Anzahl und Gesamtgröße kompakt; Pfade liegen gekürzt in einer aufklappbaren,
  scrollbaren Liste mit Vollpfad-Tooltip und Copy-Aktion.
- Die Wahl ist explizit: **Library database only** behält Dateien auf Disk,
  **Permanently delete files** verwendet weiterhin Preview-Token und Root-
  Safety aus ADR-05. Unsichere Pfade deaktivieren nur den permanenten Modus,
  nicht die DB-only-Alternative. Permanent Delete verlangt eine zweite
  destruktive Bestätigung.
- `POST /api/library/v2/<entity>/<id>/file-remove` markiert ausgewählte
  `lib2_track_files` als gelöscht, ohne den Datenträger anzufassen. Der
  bestehende `file-delete`-Endpoint bleibt das physische Kommando. Beide
  liefern betroffene Track-IDs; danach wird Wanted neu projiziert und der
  bestehende Wishlist-Mirror synchronisiert.
- Der `deleted`-Lifecycle-State gilt nun durchgängig als nicht vorhandene
  Library-Datei: Wishlist-/Upgrade-Entscheidung, Missing-Zählung,
  Discography-Completeness, Scan, Retag und Artwork ignorieren solche Zeilen.
  Ein später neu importiertes aktives File übernimmt zuverlässig das Primary-
  Flag; die Schema-Reparatur heilt auch ältere Deleted-Primary-Konstellationen.
- Das additive ADR-05-Journal trägt jetzt `mode`, `actor` und
  `actor_profile_id`. DB-only und permanent erscheinen als unterschiedliche
  History-Events. Dabei wurde zugleich die bestehende Singular-/Plural-Lücke
  im History-Read (`artist`/`release_group` vs. tatsächlich gespeichertes
  `artists`/`albums`) rückwärtskompatibel geschlossen.
- Beim kombinierten Artist-/Album-Delete läuft das Dateikommando zuerst. Die
  Entity wird bei Permanent Delete nur nach Status `completed` entfernt; ein
  partieller oder fehlgeschlagener Disk-Delete lässt sie für Retry/Audit
  bestehen.

### 54.3 Verifikation und Rest-Scope

- Backend: `python -m pytest tests/library2` → **642 passed**.
- Frontend: Vitest → **36 Dateien / 206 Tests passed**.
- Frontend: `npm run check` → **0 Warnungen / 0 Fehler**; TypeScript
  `--noEmit` ebenfalls grün.

Neu bzw. erweitert getestet sind Watchlist-Auflösung/Validierung und
Profil-Scope, beide File-Removal-Modi samt Wanted-Reprojektion und History,
die Artist-Settings-UI sowie derselbe Delete-Dialog aus beiden
Einstiegspunkten. Offen bleibt ausschließlich der schon in §53.3 und §52.12
benannte Scope; insbesondere wurden keine H-/I-Gaps außerhalb der durch §52
verbindlich angenommenen Funktionen stillschweigend umgesetzt.

---

## 55. §52.8 dritter vertikaler Slice — Early Materialization für bestätigte Wishlist-/Acquisition-Intents — ✅ teilweise umgesetzt (2026-07-17)

Dieser Slice adressiert §52.8, den einzigen in §53.3 verbliebenen Punkt, dessen
zugrundeliegende Produktentscheidung bereits abschließend getroffen ist
(§52.12 Punkt 2: „✅ entschieden"). Die übrigen offenen §52-Punkte
(Manual-Grab-Policy §52.7, Replacement-Aufbewahrung §52.6, die reichere
Match-Karte §52.5, Correlation-History/Stepper §52.9) bleiben unverändert
entweder an eine offene §52.12-Entscheidung gebunden oder ein eigener
größerer Schnitt und wurden hier nicht angefasst.

### 55.1 Gemeinsamer Resolver

`core/library2/materialize.py` ist neu und bündelt das Reuse-First-Muster,
das der Post-Download-Autolink (`core/library2/autolink.py`) bereits kennt,
für den Zeitpunkt VOR Search/Download:

- `materialize_track_intent(conn, ...)` löst Artist/Release/Track über die
  bestehenden `find_or_create_*`-Helfer auf (jetzt öffentlich benannte
  Aliase der bisherigen `_find_or_create_*`-Funktionen in `autolink.py`,
  keine Logikänderung), setzt bei explizit gewähltem Profil
  `assign_quality_profile(conn, "tracks", track_id, explicit_profile_id)`,
  markiert **nur den konkreten Track** über `record_rule(..., "track", ...)`
  plus `recompute_wanted_for_entity` als monitored/wanted — ausdrücklich ohne
  auf Album/Artist zu kaskadieren — und liefert die aufgelösten IDs plus das
  effektive Profil zurück. Committet nicht selbst und mirrort nicht selbst in
  die Wishlist; das bleibt Sache des jeweiligen Aufrufers.
- `materialize_from_spotify_track(conn, spotify_track_data, ...)` adaptiert
  die im Code weit verbreitete `spotify_track_data`-Form
  (`{"id","name","artists":[...],"album":{...},"track_number",...}`) auf den
  Resolver; liefert `None` ohne Seiteneffekt, wenn Titel oder Artist fehlen
  (z. B. ein Wing-it-Platzhalter).
- `materialize_wishlist_intent(spotify_track_data, ...)` ist der Best-Effort-
  Einstiegspunkt für Aufrufer außerhalb von `core.library2`: eigene
  Connection, eigener Commit, fail-open wie
  `autolink.link_download_into_library_v2` — eine Materialisierung darf einen
  bereits erfolgreichen Wishlist-Write niemals nachträglich zum Scheitern
  bringen.

### 55.2 Verkabelte Einstiegspunkte

Von den in §52.8 genannten Pfaden („insbesondere Search-Aktionen,
Playlist-Sync, Watchlist-Scanner, direkte Track-Suche und manuelle
Wishlist-Aktionen") sind jetzt verkabelt:

- **Manuelle Wishlist-Aktion / Search-Seite „Add to Wishlist"**
  (`core/wishlist/routes.py::add_album_track_to_wishlist`): nach einem
  erfolgreichen Legacy-Wishlist-Add materialisiert derselbe Aufruf sofort
  das lib2-Trio aus `track`/`artist`/`album`. Schlägt der Wishlist-Add selbst
  fehl, wird nicht materialisiert (kein „unverbindlicher" Write).
- **Playlist-Sync** (`services/sync_service.py`, Auto-Add nicht gematchter
  Tracks): jeder erfolgreiche `add_spotify_track_to_wishlist`-Aufruf
  materialisiert direkt danach denselben `spotify_track_data`.
- **Watchlist-Scanner** (`core/watchlist_scanner.py::add_track_to_wishlist`):
  ersetzt die bisherige, nur-artist-level `lib2_quality_profile_for_artist`-
  Abfrage durch einen vollen Materialize-Aufruf (Artist **und** Album **und**
  Track werden aufgelöst/angelegt, nicht nur der Artist-Profil-Wert gelesen);
  bei Fehlschlag/deaktiviertem Feature-Flag fällt der Code auf die alte
  artist-only-Abfrage zurück, damit das Verhalten nie schlechter als vorher
  wird.
- **Direkte Track-Suche** benötigte keine Änderung — sie materialisiert
  bereits seit §53.2 idempotent, nur ohne die volle Artist/Album/Track-
  Neuanlage (dort existiert die Entity immer schon, weil Automatic Search nur
  auf bestehenden lib2-Entities läuft).

**Bewusst nicht angefasst:** die legacy `/api/search`- und
`/api/download`-Routen in `web_server.py` (Search-Seite „Begin Analysis" /
„Force Download"). Das ist der mit Abstand größte Blast-Radius im
Repository (der zentrale Such-/Grab-Hotpath für praktisch jeden Download)
und der einfache Track-Grab-Zweig führt dort teils nur `title`/`artist` ohne
Album- oder Provider-IDs — ein Materialize-Aufruf würde für viele nicht mit
Spotify verknüpfte Downloads Alben aus dem bloßen Tracktitel raten. Diese
beiden Routen bleiben offener Scope für einen eigenen, vorsichtigeren Slice.

### 55.3 Verifikation und Rest-Scope

- Backend: `python -m pytest tests/library2` → **657 passed** (+15 neue Tests
  in `tests/library2/test_materialize.py` für Resolver-Idempotenz,
  Deduplizierung, explizite-Profil-Präzedenz und Nicht-Kaskadieren aufs
  Artist-Monitoring).
- Gezielte Wiring-Tests für zwei der drei verkabelten Aufrufer:
  `tests/wishlist/test_routes.py` (Materialize wird bei Erfolg aufgerufen,
  bei Fehlschlag nicht) und das neue
  `tests/test_watchlist_scanner_materialize.py` (voller Materialize-Pfad plus
  Fallback bei `None`/Exception). Für den dritten Aufrufer
  (`services/sync_service.py`) existiert **keine** dedizierte Unit-Test-
  Abdeckung — `sync_playlist` ist eine sehr große, tief mit Media-Client/
  Matching-Engine verzahnte Methode; ein isolierter Test dafür wäre ein
  eigenes, unverhältnismäßig großes Test-Scaffolding gewesen. Die Änderung
  dort folgt exakt demselben, an den anderen beiden Stellen bereits
  verifizierten Aufrufmuster.
- Kein H-/I-Gap wurde außerhalb des durch §52 angenommenen Scopes
  angefasst.

Weiterhin offen: §52.5 (reichere Match-Karte), §52.6-Replacement-Teil,
§52.7 Manual-Grab-Policy, §52.9 Correlation-History/Stepper — alle wie in
§53.3 benannt, jetzt zusätzlich die legacy Search-/Download-Routen aus
§55.2.

---

## 56. §52.5 Follower-/Popularitäts-Daten in der Match-Kandidatenliste — ✅ teilweise umgesetzt (2026-07-17)

Dieser Slice schließt einen Teil der in §54.1 offen gelassenen Lücke
„Follower, Popularität und automatische-vs.-manuelle Match-Provenienz
bleiben offen" — konkret die Follower-/Popularitäts-Hälfte, für die
Kandidatenliste des manuellen Providersuche-Modals (`ManualMatchModal`).

### 56.1 Was umgesetzt wurde

- `core/library/service_search.py`: die Spotify-Artist-Suche liefert jetzt
  `followers`/`popularity` mit — Daten, die die geteilte `Artist`-Dataclass
  (Spotify, SpotipyFree UND der iTunes-/Deezer-Fallback dieses selben
  Zweigs) bei jedem Suchtreffer bereits mitführt, nach der bestehenden
  Konvention „Spotify-only; 0 elsewhere". Kein zusätzlicher API-Call. Die
  separate, rohe Deezer-Such-Route (eigener API-Zweig, nicht die
  `Artist`-Dataclass) liefert zusätzlich die Fan-Zahl als `followers`.
  Andere Provider (iTunes, MusicBrainz, Last.fm, Genius, Tidal, Qobuz,
  Discogs, AudioDB, Amazon, Bandcamp, JioSaavn) bleiben unverändert, da sie
  keine vergleichbare, eindeutig benennbare Kennzahl liefern (Last.fms
  „listeners" ist z. B. KEIN Follower-Äquivalent und wurde bewusst nicht
  umgelabelt).
- `webui/src/routes/library-v2/-library-v2.api.ts`:
  `LibraryV2MatchSearchResult` trägt jetzt optionale `followers`/
  `popularity`-Felder.
- `webui/.../-ui/library-v2-page.tsx`: `ManualMatchModal`s Kandidatenzeile
  zeigt eine zusätzliche Zeile („54M followers · 97 popularity"), sobald
  ein Kandidat diese Werte trägt; `formatMatchStat`/`formatCompactNumber`
  behandeln `0` weiterhin als „vom Provider nicht geliefert", nicht als
  echten Wert — konsistent mit der bestehenden Backend-Konvention.

### 56.2 Bewusst nicht in diesem Slice

- **Die aktuell verknüpfte Artist-Karte** (`ArtistSettingsModal`s Watchlist-
  Identity-Bereich, gespeist aus `watchlist_artists` statt einer Live-
  Providerabfrage) zeigt weiterhin keine Follower/Popularität — das würde
  entweder eine neue Spalte plus Schreibpfad in jedem Match-Worker oder
  einen zusätzlichen Live-API-Call bei jedem Settings-Laden erfordern,
  beides ein größerer, eigener Schnitt als die hier umgesetzte
  Kandidatenliste (die diese Daten ohnehin schon live abruft).
- **Match-Provenienz (automatisch vs. manuell):** alle ~12 Enrichment-
  Worker UND `library_manual_match()` schreiben denselben
  `{service}_match_status = 'matched'` ohne Herkunftsunterscheidung — es
  gibt heute keinerlei Signal, ob eine Zuordnung automatisch oder manuell
  entstand. Das zu beheben bräuchte eine neue Spalte/Konvention plus
  Schreibpfad-Änderungen in jedem einzelnen Worker (audiodb, amazon, tidal,
  lastfm, discogs, qobuz, itunes, spotify, genius, …) — deutlich größerer
  Blast-Radius (legacy-Code, von der Enhanced View mitgenutzt) als in einem
  Nebenschlag sinnvoll. Bleibt offener Scope.

### 56.3 Verifikation

- Backend: neue `tests/library/test_service_search_social_stats.py`
  (Spotify mit echten Werten, Spotify-Ergebnis vom iTunes-Fallback bedient
  mit 0/0, Deezer-Fan-Zahl) — `pytest tests/library tests/library2` →
  **818 passed**.
- Frontend: 2 neue Tests in `match-chips.test.tsx` (Stats-Zeile erscheint/
  bleibt weg) — `vitest run` → **208 passed**; `npm run check` (oxfmt +
  oxlint --type-check) und `tsc --noEmit` beide grün.

Weiterhin offen aus §52: die aktuelle-Match-Karte (§56.2), Match-Provenienz
(§56.2), §52.6-Replacement-Teil, §52.7 Manual-Grab-Policy, §52.9
Correlation-History/Stepper, sowie die legacy Search-/Download-Routen aus
§55.2.

---

## 57. §52.9/§52.10 Track-Pipeline-Timeline im Info-Tab — ✅ teilweise umgesetzt (2026-07-17)

Dieser Slice schließt den in §52.10 konkret benannten Rest-Punkt „vom Track
aus müssen auch fehlgeschlagene Versuche sichtbar sein, die nie eine neue
`lib2_track_files`-Zeile erreicht haben" — die einzige Teilmenge von §52.9
Correlation-History/Stepper, die ohne eine neue §52.12-Entscheidung
umsetzbar war.

### 57.1 Vorhandener Resolver, fehlender Einstiegspunkt

`core/library2/history_feed.py::scoped_history` unterstützt `scope='track'`
bereits seit §35/A6-C3 vollständig (per `recording_id`
korrelierte `acquisition_history`-Events, `lib2_entity_history`,
`lib2_manual_skips`, `track_downloads`) und war dafür in
`tests/library2/test_history_feed.py` bereits getestet — nur exponierte
kein API-Endpoint und keine UI diesen Scope. Der Info-Tab zeigte bis dahin
ausschließlich die aktuelle Quelle plus rohe `track_downloads`-Historie, nie
Quarantäne-/Grab-Versuche, die vor einem finalen Autolink scheiterten.

- Neuer Endpoint `GET /api/library/v2/tracks/<id>/history` (analog zum
  bestehenden Artist-Endpoint, reiner Reuse von `scoped_history`).
- Frontend: `fetchLibraryV2TrackHistory` + neue, exportierte
  `TrackPipelineTimeline`-Komponente rendert die Events chronologisch
  (älteste zuerst — ein Pipeline-Verlauf liest sich wie eine Geschichte,
  anders als die flache, neueste-zuerst-sortierte Artist-History-Tabelle)
  als vertikale Timeline im Info-Tab, direkt oberhalb der bestehenden
  Verification-/Source-Info-Sektion. Wiederverwendet dieselben
  Kategorie-Farben (`sourceBadge[data-tone=…]`) wie das Artist-History-Modal.

### 57.2 Real-DB-Fund: `_track_download_events` verlor die meisten Downloads

Beim Live-Verifizieren gegen die echte Dev-DB (nicht die Unit-Tests) zeigte
sich, dass der neue Track-Scope für reale Tracks fast immer leer blieb,
obwohl der Artist-Scope „Downloaded"-Events zeigte. Root Cause: in dieser DB
haben **165 von 173** `track_downloads`-Zeilen ein `NULL` `track_id` (nie
zurückgeschrieben) — auch für Tracks, deren `lib2_tracks.legacy_track_id`
korrekt gesetzt ist. `_track_download_events` versuchte den
Pfad-Fallback bisher nur, wenn ein Track **gar keine** `legacy_track_id`
hatte, nicht wenn die Legacy-ID-Abfrage schlicht nichts fand.
`core/library2/source_info.py::track_source_info` (dieselbe fachliche
Aufgabe, pro-Track statt gebatcht) hatte dieses Fallthrough-Verhalten
bereits korrekt — `_track_download_events` gebatched jetzt beide Abfragen
(Legacy-ID-Treffer zuerst, dann Pfad-Fallback nur für die *nicht getroffenen*
Tracks) und spiegelt damit dasselbe Verhalten ohne N+1-Query. Regressionstest
`test_track_download_surfaces_via_path_fallback_when_legacy_id_stale`
reproduziert exakt diesen Fall (Legacy-ID gesetzt, aber keine passende
`track_downloads`-Zeile — nur eine über den Pfad).

### 57.3 Bewusst nicht in diesem Slice

- Kein neues `quality_checked`/`acoustic_id_checked`-Eventvokabular in
  `acquisition_history` — die von §52.9 verlangte granulare Schrittstruktur
  (Actor, Vorher-/Nachher-Qualität, `not_run`/`skipped` explizit markiert)
  bräuchte Schreibpfad-Änderungen in `pipeline_callback.py`/`workflow.py`/
  `manual_grab.py`/`imports.py` und damit eine eigene Design-Runde, keine
  reine Wiederverwendung wie dieser Slice.
- Kein horizontaler Node-Stepper wie das Legacy-„Download Audit Trail"-Modal
  (`webui/static/wishlist-tools.js::openDownloadAuditModal`) — dessen
  Schritte werden aus einer einzelnen `track_downloads`/`library_history`-
  Zeile abgeleitet, nicht aus der korrelierten `acquisition_history`-Kette,
  und sind für lib2-Tracks ohne Legacy-ID/exakten Pfadmatch gar nicht
  erreichbar. Die neue Timeline verwendet bewusst die reichere,
  bereits-korrelierte `scoped_history`-Quelle statt dieses älteren Musters.
- Album-Scope-Timeline im Album-Detail: `scoped_history(scope='album', …)`
  ist bereits vom Resolver abgedeckt, aber ohne konkrete Nutzeranfrage für
  diesen Slice nicht verdrahtet.

### 57.4 Verifikation

- Backend: neuer Endpoint + Regressionstest — `pytest tests/library2` →
  **664 passed** (+7 neue Tests: 3 API-Route-Tests, 1
  Fallback-Regressionstest, 3 parametrisierte Limit-Validierungsfälle).
  `ruff check` clean.
- Frontend: neue `track-pipeline-timeline.test.tsx` (leere vs. gefüllte
  Timeline, chronologische Reihenfolge) + 2 neue API-Layer-Tests — `vitest
  run` → **212 passed**; `npm run check` (oxfmt + oxlint --type-check) und
  `tsc --noEmit` beide grün.
- Live gegen die echte Dev-DB verifiziert (Playwright, `dev.py`): Track-Detail
  → Info-Tab zeigt „Pipeline — 2 events" mit beiden historischen
  Soulseek-/HiFi-Downloads für „DAISIES" (Justin Bieber, SWAG), chronologisch
  aufsteigend, korrekt eingefärbt.

Weiterhin offen aus §52: die aktuelle-Match-Karte (§56.2), Match-Provenienz
(§56.2), §52.6-Replacement-Teil, §52.7 Manual-Grab-Policy, das granulare
`quality_checked`/`acoustic_id_checked`-Eventvokabular sowie der
Album-/Artist-Zweig von §52.9, sowie die legacy Search-/Download-Routen aus
§55.2.

---

## 58. §52.9 Album-Zweig der History — ✅ umgesetzt (2026-07-17)

Schließt den in §57.3 offen gelassenen Punkt „Album-Scope-Timeline im
Album-Detail: `scoped_history(scope='album', …)` ist bereits vom Resolver
abgedeckt, aber ohne konkrete Nutzeranfrage nicht verdrahtet" — reiner
Wiederverwendungs-Slice, keine neue Design-Entscheidung nötig.

### 58.1 Einstiegspunkt

- Neuer Endpoint `GET /api/library/v2/albums/<id>/history` (analog zu
  Artist/Track, reiner Reuse von `scoped_history(scope='album', …)`, das
  bereits seit §29/A6-C3 vollständig getestet ist — u. a. gegen Leck in
  Nachbaralben, `lib2_manual_skips`, File-Deletes).
- Frontend: `HistoryModal` (bisher artist-only) generalisiert auf einen
  `scope: 'artist' | 'album'`-Prop statt eines festen `artistId`-Props;
  Leertext jetzt „No recorded history for this {scope} yet." statt hart
  „…this artist…". Neuer `fetchLibraryV2AlbumHistory`.
- Ein neues „History"-Item im bereits existierenden `AlbumOverflowMenu"
  (wiederverwendet in der Album-Zeile der Artist-Detailansicht **und** im
  Album-Detail-Header — beide Stellen bekommen die Funktion automatisch, da
  beide dieselbe Komponente rendern) öffnet dieselbe flache Tabellen-Modal
  wie die Artist-History — bewusst **nicht** die vertikale
  `TrackPipelineTimeline` aus §57, da Alben (anders als Tracks) keinen
  eigenen Info-Tab haben; die flache Tabelle ist der konsistente Fit zum
  bestehenden Artist-Muster.

### 58.2 Verifikation

- Backend: 5 neue Tests (Skip-Event sichtbar, kein Leck ins Nachbaralbum,
  404 für unbekanntes Album, 4 parametrisierte Limit-Validierungsfälle) —
  `pytest tests/library2` → **671 passed**. `ruff check` clean.
- Frontend: 2 neue API-Layer-Tests in `-library-v2.api.test.ts` — `vitest
  run src/routes/library-v2/` → **21 Dateien, 118 Tests grün**; `npm run
  check` (oxfmt + oxlint --type-check) und `tsc --noEmit` beide grün.
- Live gegen die echte Dev-DB verifiziert (Playwright, `dev.py`): Artist
  „Hiroyuki Sawano" → Album „TVアニメ「進撃の巨人」Season 2" → „…" → History
  zeigt ausschließlich Grab-/Quarantäne-/Download-Events für dieses eine
  Album (u. a. mehrere „Quarantined: Duration mismatch"-Versuche für den
  fehlenden Track), nicht die Events des zweiten, vollständigen Albums
  desselben Artists.

Damit bleibt aus §52 offen: die aktuelle-Match-Karte (§56.2),
Match-Provenienz (§56.2), §52.6-Replacement-Teil, §52.7 Manual-Grab-Policy,
das granulare `quality_checked`/`acoustic_id_checked`-Eventvokabular sowie
die legacy Search-/Download-Routen aus §55.2 — jeweils entweder an eine
§52.12-Entscheidung gebunden oder ein eigener größerer Schreibpfad-Slice.

---

## 59. §56.2 aktuelle-Match-Karte — Live-Follower/Popularity im Artist-Settings-Modal — ✅ umgesetzt (2026-07-17)

Schließt die in §56.2 offen gelassene Hälfte: „die CURRENT-match Identity-Karte
in `ArtistSettingsModal` (sourced aus der `watchlist_artists`-DB-Zeile, kein
Live-Fetch) — Follower dort hinzuzufügen bräuchte entweder eine neue
gecachte Spalte + Schreibpfad, oder einen zusätzlichen Live-API-Call pro
Settings-Load." Die zweite Option ist bereits ein etabliertes,
produktionserprobtes Muster im selben Repo — der legacy
`/api/watchlist/artist/<id>/config`-Endpoint macht exakt das seit Langem
(ein `spotify_client.sp.artist(id)`-Call pro Config-Load, hinter
`is_authenticated()` + globalem Rate-Limit-Flag, stiller Fallback auf 0 bei
Fehlern) — reiner Wiederverwendungs-Slice, keine neue Schema-Entscheidung.

### 59.1 Umsetzung

- `web_server.py::_library_v2_live_artist_stats(spotify_id)` — portiert exakt
  dasselbe Auth-/Rate-Limit-/Fehler-Gating wie der Legacy-Endpoint, gibt
  `{"followers", "popularity"} | None` zurück. Injiziert in
  `register_library_v2_routes` als neuer optionaler
  `live_artist_stats_getter`-Parameter (gleiches Injection-Muster wie
  `run_enrichment`/`scoped_wishlist_search_dispatcher`, um den
  Zirkularimport zurück in `web_server.py` zu vermeiden).
- `GET /api/library/v2/artists/<id>/settings` ruft den Getter nur bei GET
  (nicht bei PUT) und nur wenn ein `provider_ids.spotify` vorhanden ist auf;
  das Ergebnis landet als `artist_stats`-Top-Level-Key in der JSON-Antwort,
  komplett weggelassen (nicht `null`, kein Key) wenn kein Getter injiziert
  wurde, kein Spotify-Link existiert, oder der Live-Call scheitert — die
  Karte degradiert exakt so still wie die bisherige Watchlist-identity-Karte.
- Frontend: `formatMatchStat` (bisher an `LibraryV2MatchSearchResult`
  gebunden, §56) auf den strukturellen Typ `{followers?, popularity?}`
  verallgemeinert und in `ArtistSettingsModal`s „Watchlist identity"-Karte
  wiederverwendet — dieselbe Kompaktzahl-Formatierung („45.2M followers · 61
  popularity") wie in der Match-Kandidatenliste.

### 59.2 Verifikation

- Backend: 3 neue Tests (Stats erscheinen bei vorhandener Spotify-ID,
  bleiben weg ohne Spotify-ID, Route bleibt funktionsfähig ganz ohne
  injizierten Getter) — `pytest tests/library2` → **674 passed**. `ruff
  check` clean.
- Frontend: 2 neue API-Layer-Tests — `vitest run src/routes/library-v2/` →
  **21 Dateien, 119 Tests grün**; `npm run check` und `tsc --noEmit` beide
  grün.
- Live gegen die echte Dev-DB verifiziert (Playwright, `dev.py`): für
  „Hiroyuki Sawano" (echte Spotify-ID `0Riv2KnFcLZA3JSVryRg4y` verlinkt)
  liefert sowohl der neue lib2-Endpoint als auch der legacy
  `/api/watchlist/artist/<id>/config`-Endpoint `followers: 0`/kein
  `artist_stats` — diese Dev-Instanz hat keine authentifizierte
  Spotify-Session, exakt dasselbe stille Zero-Fallback-Verhalten wie das
  Legacy-Muster, kein Fehler. Um den datentragenden Pfad zu verifizieren,
  wurde die `/settings`-Response per Playwright-Routen-Interception um
  `artist_stats: {followers: 45230000, popularity: 61}` ergänzt — die Karte
  rendert korrekt „45.2M followers · 61 popularity" unterhalb des
  Artist-Namens, Layout unverändert.

Damit bleibt aus §52 offen: Match-Provenienz (§56.2, größerer Blast-Radius
über ~12 Enrichment-Worker), §52.6-Replacement-Teil, §52.7
Manual-Grab-Policy, das granulare
`quality_checked`/`acoustic_id_checked`-Eventvokabular sowie die legacy
Search-/Download-Routen aus §55.2.

---

## 60. §52.12 Entscheidungen bestätigt — Replacement-Retention, Manual-Grab-Force, Track-Watch-Scope (2026-07-17)

Der Nutzer hat 4 der 5 offenen §52.12-Punkte entschieden. Dieser Slice setzt
die drei um, die tatsächlich Code-Impact haben.

### 60.1 §52.12.5 Replacement-Retention: sofortiges Löschen nach erfolgreichem Import

Entscheidung: die alte Datei wird sofort permanent gelöscht, sobald die
bessere Version erfolgreich importiert ist (Lidarrs Default-Verhalten ohne
konfigurierten Recycle-Bin-Pfad). Klarstellung: das betrifft ausschließlich
dieselbe Track-Entität — legitime Mehrfachversionen (Remix/Live/Remaster)
sind bereits eigene Track-Zeilen (siehe `dedup_title_key`, §12/#39) und
bleiben unberührt.

Der sichere „erst löschen, wenn der Import wirklich fertig ist"-Mechanismus
existierte bereits für den Artist-Quality-Enhance-Bulk-Flow
(`core/artists/quality.py` → `source_context={'enhance': True,
'original_file_path': ...}` → `core/imports/paths.py:486-493` platziert die
neue Datei am alten Pfad → `core/imports/pipeline.py:1089-1102` löscht die
alte Datei erst NACH `safe_move_file`). Der Quality-Upgrade-Finder
(`core/repair_worker.py::_fix_quality_upgrade`, `redownload`-Zweig) fügte
bisher nur mit `source_type='repair'` zur Wishlist hinzu, ohne diesen
Mechanismus zu triggern — die alte Datei blieb für immer liegen
([[quality-upgrade-auto-replace]]). Fix: ein zusätzliches `'enhance': True`
im `source_info`-Dict reicht, da Pipeline und Pfad-Resolver nur auf
`source_info` prüfen, nie auf `source_type` (verifiziert: `source_type`
wird nirgendwo sonst auf `'repair'`/`'quality_upgrade'` verglichen). Kein
neuer Code-Pfad, reine Wiederverwendung. Tests:
`tests/repair_jobs/test_quality_upgrade.py` (2 Tests erweitert um
`source_info['enhance'] is True` + `original_file_path`-Assertion).

### 60.2 §52.12.4 Manual Grab außerhalb des Profils: Force-Bestätigung

Entscheidung: außerhalb des Profils liegende Kandidaten werden mit rotem
Fail-Grund gezeigt; ein Download nur über eine separate, explizit
bestätigte und auditierte Force-Aktion.

Der Audit-Teil existierte bereits vollständig, nur unauffällig verkabelt:
`web_server.py::_audit_manual_skip` (aufgerufen aus beiden `/api/download`-
Zweigen, Album L7170-7174 + Single-Track L7300-7303) schreibt bei jedem
`skip_acoustid`/`quality_check=false`-Grab einen Audit-Row über
`core/library2/manual_skips.py::record_manual_skip` (Actor = `profile_id`,
Zeitpunkt = `created_at`, Grund = `skipped_checks`); der Import-Pipeline-Teil
(`core/imports/pipeline.py::_attach_manual_skip_path`, 2 Aufrufstellen)
bindet den Row nachträglich an den finalen Dateipfad. Was fehlte: eine
**separate, pro Kandidat bestätigte** Force-Aktion — bisher galten die
„Quality check"/„AcoustID check"-Checkboxen still für die ganze
Such-Session, ohne Rückfrage.

Fix (`webui/src/routes/library-v2/-ui/interactive-search.tsx::grab`): wenn
„Quality check" aus ist UND der angeklickte Kandidat laut
`profileTargetRank` unterhalb aller Profil-Targets liegt, zeigt ein
`window.confirm` den Fail-Grund und verlangt eine explizite Bestätigung,
bevor der Grab dispatcht wird — Ablehnen bricht ohne jeden Server-Call ab.
Bewusst clientseitig (kein neuer Server-Enforcement-Pfad): passt zum
bestehenden Trust-Modell dieser App (admin-only lib2, „trust local
network", siehe `admin_only`-Docstring) und der Audit-Trail greift ohnehin
serverseitig unabhängig von der UI-Bestätigung. Test:
`interactive-search.test.tsx` — Ablehnen dispatcht keinen Request, Bestätigen
dispatcht genau einen.

### 60.3 §52.12.3 Track-Suche watcht nicht automatisch den ganzen Artist — bereits korrekt

Entscheidung: nur den Track monitoren, Artist nur als Parent anlegen; die
volle Watchlist-Aufnahme bleibt ein separater Bookmark-Klick. Verifiziert
gegen den aktuellen Code (kein Fix nötig): `core/library2/materialize.py`
ruft ausschließlich `find_or_create_artist/album/track` +
`recompute_wanted_for_entity` auf; der einzige Schreibpfad in
`watchlist_artists` läuft über `database/music_database.py::add_artist_to_watchlist`,
erreichbar nur von den expliziten Artist-Monitor-Toggle-Endpoints
(`api/library_v2.py`) und dem `watchlist_add`-Mirror-Outbox — keiner davon
ist von `materialize_track_intent`/`materialize_wishlist_intent` aus
erreichbar. Verhalten entspricht der Entscheidung bereits 1:1.

### 60.4 §52.12.1 Playlist-Profil-Konflikt: Entscheidung notiert, Feature existiert noch nicht

Entscheidung: bei zwei Playlists mit unterschiedlichem Quality Profile auf
demselben Track immer eine Konflikt-UI zeigen, nie automatisch auflösen.
Nicht umgesetzt in diesem Slice: ein **Playlist-Quality-Profile-Konzept
existiert aktuell in keiner Form** — weder Schema-Spalte noch API noch UI,
weder in lib2 noch in den Legacy-Playlist-Tabellen
(`mirrored_playlists`/`personalized_playlists`; `mirrored_playlists.profile_id`
referenziert das Wishlist-**User**-Profil, nicht `quality_profiles` — ein
anderes Konzept). Die Konflikt-Policy ist damit vgl. entschieden, aber ohne
zugrundeliegendes Feature nichts, worauf sie angewendet werden könnte — das
Anlegen von Playlist-Quality-Profiles selbst ist ein eigenständiges,
bislang undesigntes Feature und wird nicht ungefragt neu gebaut (Scope- und
Blast-Radius-Vorsicht). Bleibt offen für eine eigene Anfrage/Session.

### 60.5 Verifikation

- Backend: `pytest tests/repair_jobs/` → 66 passed; `tests/imports/test_import_pipeline.py`
  + `tests/imports/test_single_to_album.py` + `tests/artists/test_quality.py` → 63 passed.
  `ruff check core/repair_worker.py` clean.
- Frontend: `vitest run src/routes/library-v2/` → 21 Dateien, 120 Tests grün
  (+1 neu); `tsc --noEmit` clean; `npm run check` (oxfmt+oxlint) clean.

---

## 61. §56.2 Match-Provenienz und visuelle Match-Identität (2026-07-17)

Der offene Provenienz-Punkt aus §56.2 ist umgesetzt, ohne die rund zwölf
bestehenden Enrichment-Worker einzeln umzubauen. Eine normalisierte Tabelle
`metadata_match_provenance` hält pro Entität und Provider `automatic`,
`manual` oder `legacy`, Provider-ID, Zeitpunkt und Actor. Additive
SQLite-Trigger hängen am gemeinsamen bestehenden Schreibvertrag der
`artists`-, `albums`- und `tracks`-Tabellen: Worker-Schreibvorgänge werden
damit automatisch erfasst, eine erneut bestätigte identische Provider-ID
überschreibt aber keine manuelle Entscheidung. Beim Upgrade vorhandene
Matches werden bewusst als `legacy` statt fälschlich als automatisch
eingestuft; Clear löscht auch die Provenienz. Die Library-v2-Match-Status-API
liefert `match_origin` und `matched_at` nun mit aus.

Das Match-Modal wurde anhand der Legacy-Library und der Watchlist-Konfiguration
neu aufgebaut: größeres Dialogfenster, 84px große runde Artistbilder,
prominente aktuelle Identität, Provider/ID mit Copy-Aktion,
Provenienz-Badges, Follower/Popularität, klare Current-Markierung und eine
explizite Clear-Aktion. Signierte externe Bild-URLs werden nicht mehr durch
ein pauschales `?size=thumb` beschädigt; ein Bild-Fehler wird bei einer neuen
URL zurückgesetzt. In den Artist Settings ist das Identitätsbild 112px groß,
Genres und Live-Stats stehen direkt dabei.

Für Artist-Kandidaten zeigt der Dialog zusätzlich echte Album-/Release-Kontexte
mit Cover, Titel, Typ und Datum. Die ersten drei Resultate werden automatisch
geladen, weitere bewusst erst auf Klick, damit breite Suchergebnisse Provider
nicht unnötig belasten. Der neue Endpoint verwendet die bestehenden
provider-spezifischen Albumadapter und fällt bei nicht unterstützten oder
fehlerhaften Providern stabil auf eine leere Vorschau zurück. Der aktuelle
Match zeigt daneben bis zu sechs lokale Library-v2-Releases als Kontext.

Die kombinierte Artist-Settings-/Watchlist-Oberfläche synchronisiert manuelle
Provider-Änderungen jetzt transaktional in den konkreten Watchlist-Row. Der
Server akzeptiert das nur, wenn Watchlist und Legacy-Artist über Namen oder
eine bestehende Provider-ID sicher zusammengehören, und blockiert doppelte
Provider-IDs. Bei Spotify-Suchen mit einem iTunes-Fallback wird die tatsächliche
Provider-Herkunft des Kandidaten gespeichert statt irrtümlich Spotify.

### 61.1 Verifikation

- Backend: Provenienz-Trigger, Legacy-Seeding, Clear/Sticky-Manual,
  Match-Status-Ausgabe und Provider-Release-Normalisierung sind durch
  dedizierte Pytest-Tests abgedeckt.
- Frontend: API-Verträge, Watchlist-Synchronisierung, externe/signed Images,
  Provenienz, Album-Kontext und tatsächlicher Fallback-Provider sind durch
  Vitest/MSW-Tests abgedeckt; Format-, Lint-, TypeScript- und Build-Prüfung
  gehören zum Abschluss dieses Slices.

## 62. Deep-Dive: Doppelte Alben/Artists bei Provider-Divergenz (Fall „Hiroyuki Sawano") — 🔍 Analyse, noch nicht umgesetzt (2026-07-17)

Nutzerreport: Nach „Update Discography" erscheint dasselbe Album doppelt —
einmal als `TV Anime "Attack on Titan Season 2" (Original Soundtrack)`
(0/33, not in library) und einmal als
`TVアニメ「進撃の巨人」Season 2 オリジナルサウンドトラック` (33/33, in library),
beide mit Release-Datum 2017-06-07. Die Untersuchung lief gegen die echte
Dev-DB plus Live-Anfragen an Deezer und MusicBrainz. Ergebnis: Es sind
**vier gestapelte Ursachen**, die sich gegenseitig verstärken. Keine davon
ist ein einzelner Tippfehler; alle vier sind strukturell.

### 62.1 Faktenlage aus der DB (Beweiskette)

Zwei Artist-Rows für denselben Künstler:

| id | name | spotify_id | musicbrainz_id | external_ids | erstellt | von |
|----|------|-----------|----------------|--------------|----------|-----|
| 31 | Hiroyuki Sawano | – | – | `{}` | 07-16 16:39:18 | Wishlist-Materialize (nur Name) |
| 32 | Hiroyuki Sawano | 0Riv2… | 60d2ea34-… | deezer 1315147, mb, spotify | 07-16 16:42:41 | Legacy-Import (`upsert_legacy`) |

Die Duplikat-Alben (Auszug):

| id | artist | title | provider-ids | origin |
|----|--------|-------|--------------|--------|
| 1163 | 31 | TVアニメ「進撃の巨人」Season 2 … | spotify_id=**1239706770** (das ist eine iTunes-ID!) | library |
| 1229 | 32 | TVアニメ「進撃の巨人」Season 2 … | deezer 196470602, itunes 1239706770, „spotify" 1239706770 | library |
| 1274 | 32 | TV Anime "Attack on Titan Season 2" (OST) | deezer **42695001** | discography |
| 1169 | 31 | TV Anime "Attack on Titan" OST | spotify_id=**42388621** (das ist eine Deezer-ID!) | library |
| 1230 | 32 | TV Anime "Attack on Titan" OST | deezer 42388621, mb d7595352-… | library |
| 1167/1173 | 31/32 | Sengoku BASARA Digital Original Director's Special Edition | – | discography |

Timeline (aus `lib2_external_id_history`):
1. 16:39:18 — User bestätigt einen Wishlist-/Search-Intent → `materialize_track_intent`
   erzeugt Artist 31 (nur Name, keine IDs) und Album 1163. Die „spotify_id"
   1239706770 stammt aus dem Legacy-Suchpayload — tatsächlich eine iTunes-ID.
2. 16:42:08 — Discography-Sync auf Artist 31 (ID-los → Namenssuche) findet auf
   Deezer den **falschen Artist-Eintrag 234170331** („Hiroyuki Sawano", nur 4
   Alben — ein Deezer-eigener Fragment-Duplikat-Artist) und importiert dessen
   4 Sengoku-BASARA-Releases (1164–1167). Live verifiziert: Deezer hat mind.
   5 verschiedene Artists namens „Hiroyuki Sawano" (234170331: 4 Alben,
   **1315147: 104 Alben**, 218685045: 1, 352973132: 2, 7865594: 2).
3. 16:42:41 — Legacy-Import läuft: `upsert_legacy` keyed **nur** auf
   `legacy_artist_id` und legt Artist 32 an, obwohl Artist 31 mit exakt
   demselben (normalisierten) Namen existiert. `_by_name`/`_by_provider`
   werden in diesem Pfad nie konsultiert — nur `get_or_create_by_name` tut das.
4. 20:28:33 — Legacy-Album-Import erzeugt 1229/1230 unter Artist 32. Album
   1163 (identischer Titel) hängt an Artist 31 und wird nicht gefunden, weil
   das Album-Claiming pro Artist gescoped ist → Cross-Artist-Duplikat.
5. 07-17 13:37 — „Update Discography" auf Artist 32 (Quelle: Deezer, Artist
   1315147) listet die **internationale EN-Ausgabe 42695001**. Row 1229 trägt
   aber deezer=196470602 (JP-Ausgabe) → kein ID-Match; JP-Titel ≠ EN-Titel →
   kein Titel-Match → Insert 1274. Das ist das vom User gemeldete Paar.

### 62.2 Ursache A: Gleiche Release-Group, verschiedene Provider-Releases

Live-Verifikation: Deezer führt **beide** Ausgaben als getrennte Alben —
42695001 (EN-Titel, UPC 4988013932357) und 196470602 (JP-Titel, UPC
4988013316096), beide 2017-06-07, beide 33 Tracks. Es sind zwei echte
Handels-Releases (verschiedene Barcodes: Japan- vs. International-Edition)
derselben Release-Group. **Auch die UPC unifiziert also nicht.**

MusicBrainz kennt dagegen beide Barcodes und hängt beide Releases an
**eine** Release-Group `f17d521f-f8e9-41d8-9b0e-e270d5d905ed`. Die Identität,
die wir bräuchten, existiert in der Industrie — wir fragen sie nur nie ab.

Pikant: `lib2_albums` IST konzeptionell bereits die Release-Group-Ebene
(die ID-History nennt den Entity-Typ sogar `release_group`), und mit
`lib2_release_editions` (ADR-04, §14) existiert die Edition-Ebene samt
`edition_signature()` schon im Schema — der Discography-Sync benutzt sie
aber nicht: Er legt pro Provider-Release flache `lib2_albums`-Rows an,
statt Provider-Releases als **Editionen einer** Release-Group zu behandeln.
Genau die Lücke, die ADR-04 schließen sollte („P1-04: duplicate detection
hatte nichts außer normalisierten Titeln"), besteht im Sync-Pfad fort.

Zusatzbefund: Deezer listet für Artist 1315147 sogar gleich-titlige
Duplikate im eigenen Katalog (2× `"Attack on Titan" Season 3 Original
Soundtrack`, 2× `TV Anime "Attack on Titan" Original Soundtrack`). Diese
werden heute nur zufällig absorbiert, weil der Titel exakt gleich ist —
`_merge_external_id` loggt dann einen ID-Konflikt und behält die erste ID
(G1-Schutz). Sobald sich die Titel auch nur in Anführungszeichen, Klammern
oder Sprache unterscheiden, entsteht ein Duplikat.

### 62.3 Ursache B: Titel-Matching ist zu schwach für den Realfall

`_match_existing` (discography.py) matcht in genau zwei Stufen: exakte
Provider-ID, sonst `normalize_name` = casefold + Whitespace-Kollaps.
Kein NFKC, keine Interpunktions-/Klammern-/Quote-Normalisierung, keine
Transliteration, kein Sekundärsignal (Datum, Trackcount). Für
`TVアニメ「進撃の巨人」…` vs. `TV Anime "Attack on Titan…"` ist das chancenlos,
obwohl **Release-Datum (2017-06-07) und Trackcount (33) exakt übereinstimmen**
— beide Signale liegen im `DiscographyRelease` bereits vor und werden beim
Matching schlicht ignoriert.

### 62.4 Ursache C: Provider-ID-Namespace-Verschmutzung

Die Legacy-Pipeline schreibt IDs fremder Provider in `spotify_*`-Spalten:
`core/seasonal_discovery.py:568` sagt es wörtlich („Column name is
spotify_album_id but stores iTunes ID too"); der `SpotifyClient` fällt bei
Rate-Limit/fehlender Konfiguration transparent auf Free-Sources (iTunes/
Deezer) zurück, und `spotify_worker` schreibt das Ergebnis als
`spotify_album_id` mit `spotify_match_status='matched'`. In der Dev-DB sind
3 von 9 Legacy-Alben und 4 von 277 lib2-Alben so verschmutzt (`spotify_id`
rein numerisch — echte Spotify-IDs sind 22-stellig base62): 1163/1229 tragen
eine iTunes-ID, 1169 sogar eine **Deezer**-ID als „spotify_id".

Folgen fürs Matching: (a) Ein echter Spotify-Sync kann diese Rows nie per
ID matchen. (b) Die richtige ID steckt teils im falschen Namespace — der
Deezer-Sync sucht `external_ids.deezer`, die Deezer-ID von 1169 steht aber
in `spotify_id` → Miss trotz vorhandener ID. (c) Latent: `_ArtistResolver.
_by_provider` keyed **nur auf den ID-Wert** (source-agnostisch, importer.py
~353) — kollidierende numerische iTunes-/Deezer-IDs verschiedener Künstler
würden stillschweigend denselben Artist treffen.

### 62.5 Ursache D: Artist-Duplikate multiplizieren Album-Duplikate

Drei Wege erzeugen bzw. verfestigen Artist-Duplikate:
1. `materialize_track_intent`/`autolink._find_or_create_artist` legen
   ID-lose Artists aus bloßen Namen an (Artist 31) — der Suchpayload hätte
   die Provider-ID oft dabei, sie landet aber nur als (falsch benannte)
   `spotify_id` oder gar nicht.
2. `upsert_legacy` prüft weder Namens- noch Provider-Index, bevor es
   inserted (Artist 32 neben identisch benanntem Artist 31).
3. Provider haben selbst Fragment-Artists (Deezer: 5× „Hiroyuki Sawano");
   ID-lose Namenssuche pickt den erstbesten (Artist 31 bekam so den
   4-Alben-Fragment-Katalog 234170331).

Da `_existing_release_index` und das Legacy-Album-Claiming pro Artist
gescoped sind, wird jedes Artist-Duplikat automatisch zur Album-Duplikat-
Fabrik: 1163↔1229, 1169↔1230, 1167↔1173 existieren NUR wegen der
31/32-Spaltung. Die §40-Alias-Registry (soft-link `canonical_artist_id`)
könnte 31↔32 heute schon verknüpfen, wurde hier aber nie angewandt —
und sie verhindert Neuentstehung nicht.

### 62.6 Lösungsvorschlag (noch nicht umgesetzt)

Empfohlene Reihenfolge — jede Stufe ist einzeln shipbar und reduziert
Duplikate messbar:

**Stufe 1 — Matching-Härtung im Discography-Sync (klein, sofort wirksam):**
- `_match_existing` um eine dritte Stufe erweitern: Kandidaten mit
  **gleichem Release-Datum (exakt) + gleichem erwarteten Trackcount +
  gleichem Typ-Bucket** gelten als dieselbe Release-Group, auch wenn der
  Titel nicht matcht. Beide Signale sind im `DiscographyRelease` schon da.
  Bei Datum UND Trackcount identisch ist die False-Positive-Gefahr minimal
  (ggf. zusätzlich absichern: nur wenn genau EIN Kandidat übrig bleibt).
- Titel-Normalisierung für Dedup-Zwecke von `normalize_name` auf eine
  schärfere Variante heben (NFKC wie `duplicate_relationship._normalized_title`,
  plus Quote-/Klammer-/Interpunktions-Strip). Display-Namen unangetastet.
- Ergebnis für den Sawano-Fall: 1274 wäre nie entstanden (Datum+33 Tracks
  matchen 1229), stattdessen wäre deezer=42695001 als **Edition/Alt-ID**
  gemerged worden.

**Stufe 2 — Editionen statt Alt-ID-Verlust (nutzt vorhandenes ADR-04):**
- Wenn Stufe 1 (oder ein Titel-Match mit ID-Konflikt, der G1-Fall) eine
  zweite Provider-Release-ID für dieselbe Release-Group liefert: statt
  Warning-und-Wegwerfen eine `lib2_release_editions`-Row anlegen
  (`edition_signature` existiert). Damit bleibt „Deezer kennt diese Gruppe
  als 42695001 UND 196470602" abfragbar — wichtig für Tracklist-Fetch,
  Interactive Search und künftige Syncs.

**Stufe 3 — MusicBrainz-Release-Group als Schiedsrichter (asynchron):**
- Für Alben mit MB-Artist-Kontext (Artist 32 hat die MBID) einen
  Reconcile-Schritt: Release-Group-Browse des MB-Artists, Zuordnung
  vorhandener lib2_albums via Barcode/Provider-URL-Relationships/Titel+Datum,
  Persistenz der RG-MBID in `lib2_albums.musicbrainz_id`. Zwei lib2-Rows,
  die auf dieselbe RG-MBID zeigen → Merge-Kandidat (Auto-Merge nur wenn
  eine Seite fileless/pristine ist, sonst Review-Finding wie bei
  `lib2_recording_review`). MB ist rate-limitiert (1 req/s) → als
  Hintergrund-Job/on-Update, nicht im Sync-Hot-Path.

**Stufe 4 — Artist-Duplikate an der Quelle schließen:**
- `upsert_legacy`: vor dem Insert `_by_provider`- und `_by_name`-Lookup wie
  in `get_or_create_by_name` (Adoptions-Semantik: Legacy-IDs auf den
  vorhandenen Row mergen, solange kein Same-Source-ID-Konflikt besteht).
  Hätte Artist 32 verhindert.
- `materialize`/`autolink`: Provider-IDs aus dem Suchpayload **mit
  Herkunfts-Namespace** durchreichen (der Payload weiß, welcher Provider
  aktiv war — §61 hat das für Match-Provenance schon gelöst) statt alles
  als `spotify_id` zu labeln; ID-lose Artist-Anlage nur noch als letzter
  Fallback.
- Discography-Sync für ID-lose Artists: Namenssuche nicht blind den ersten
  Treffer nehmen, sondern den Kandidaten mit größtem Katalog/Follower-Zahl
  bzw. — besser — die §56-Kandidatenliste dem User zeigen (der Sync auf
  einem ID-losen Artist ist fast immer ein Fehlgriff, siehe Artist 31 mit
  Fragment-Katalog 234170331).
- Einmalige Repair-Migration: `lib2_artists` nach normalisiertem Namen
  gruppieren; Gruppen ohne Same-Source-ID-Konflikt mergen (Alben/Tracks/
  Monitoring/History umhängen), sonst §40-Alias-Link + Review-Finding.

**Stufe 5 — Namespace-Sanierung (Hygiene, macht ID-Matching wieder scharf):**
- Migration: numerische `spotify_id`-Werte in lib2 (und beim Legacy-Import)
  als das behandeln, was sie sind — gegen `itunes`/`deezer`-Spalten des
  Legacy-Rows abgleichen und in den richtigen `external_ids`-Namespace
  verschieben; `spotify_id` nur noch für echte (base62-)Spotify-IDs.
- Schreibseite: `spotify_worker`/Free-Fallback müssen die tatsächliche
  Quelle deklarieren (analog §61-Fix „Bei Spotify-Suchen mit iTunes-Fallback
  wird die tatsächliche Provider-Herkunft gespeichert").
- `_ArtistResolver._by_provider` auf `(source, value)`-Keys umstellen.

Nicht empfohlen: Transliterations-/Übersetzungs-Matching (JP→EN) als
Primärmechanismus — zu fehleranfällig, und mit Datum+Trackcount (Stufe 1)
plus MB-Release-Groups (Stufe 3) unnötig.

### 62.7 Betroffene Stellen (Referenz)

- `core/library2/discography.py` — `_match_existing`, `_existing_release_index`,
  `_merge_external_id` (G1-Konfliktpfad = künftiger Edition-Einstieg)
- `core/library2/importer.py` — `_ArtistResolver.upsert_legacy` (Insert ohne
  Name/Provider-Lookup), `_by_provider` (value-only Keys), Legacy-Album-Mapping
  `"spotify": spotify_album_id` (Verschmutzungs-Durchreiche)
- `core/library2/autolink.py` / `materialize.py` — ID-lose Artist-Anlage,
  `spotify_id`-Labeling beliebiger Provider-IDs
- `core/library2/editions.py` — vorhandene, im Sync ungenutzte Edition-Ebene
- `core/library2/provider_adapters.py` — `DiscographyRelease` (kein
  UPC/RG-Feld; Datum/Trackcount vorhanden, aber ungenutzt fürs Matching)
- `core/spotify_worker.py`, `core/seasonal_discovery.py`,
  `core/metadata/registry.py` — Free-Source-Fallback schreibt Fremd-IDs als
  Spotify

---

## 63. §62.6 umgesetzt — alle fünf Stufen der Duplikat-Behebung — ✅ implementiert (2026-07-17)

Alle fünf Stufen aus §62.6 sind TDD-first umgesetzt (jede Änderung mit vorher
rot gesehenen Tests) und gegen eine Kopie der echten Dev-DB live verifiziert.

### 63.1 Stufe 1 — Matching-Härtung im Discography-Sync

- Neuer Dedup-Schlüssel `release_title_key()` (importer.py): NFKC + casefold,
  Interpunktion/Quotes/Klammern als Trenner. Verwendet in
  `_match_existing`/`_existing_release_index` (discography.py), im
  Legacy-Album-Claiming (`_claim_discography_album`), im Wishlist-Seeding
  (`album_by_identity`) und in `autolink._find_or_create_album`.
- Dritte Match-Stufe in `_match_existing`: volles Release-Datum (Y-M-D
  zwingend, nie nur Jahr) + gleicher erwarteter Trackcount + gleicher
  Typ-Bucket, nur bei GENAU einem Kandidaten. Drei 1-Track-Singles am
  selben Tag bleiben getrennt (Ambiguitäts-Guard, Test abgedeckt).

### 63.2 Stufe 2 — Alternative Releases werden Editionen

- `editions.record_alternative_edition()`: idempotent pro (source, id) und
  Gruppe. Der G1-Konfliktpfad (`_merge_external_id_details`) legt beim
  Same-Source-ID-Konflikt jetzt eine nicht-default `lib2_release_editions`-
  Row an statt die zweite Release-ID wegzuloggen.

### 63.3 Stufe 3 — MusicBrainz-Release-Group-Reconcile

- Neues Modul `core/library2/mb_reconcile.py`:
  `reconcile_artist_release_groups(db, artist_id)` browst die RGs des
  Artist-MBID (paginiert, Rate-Limit im Client), stempelt RG-MBIDs per
  Titel-Key (bei Mehrdeutigkeit per primary-type, sonst gar nicht) und per
  Datum-Fallback, merged Rows mit gleicher RG-MBID automatisch nur wenn die
  Verlierer-Row pristine ist, sonst Finding in `lib2_release_group_review`.
- Verkabelung: der Refresh-Endpoint startet den Reconcile als
  Hintergrund-Thread (`lib2-mb-reconcile`), nie im Hot-Path; Fehler brechen
  den Refresh nicht.
- Zwei Real-DB-Funde während der Live-Verifikation, beide gefixt + getestet:
  1. **Fileless-Placeholder-Tracks:** Die auto-monitorte EN-Duplikat-Row
     (1274) hatte 33 Track-Rows OHNE Files („0/33") und Provenance
     `new_release`. Pristine-Kriterium ist jetzt „keine FILES" statt „keine
     Track-Rows", Maschinen-Monitoring (`new_release`/`legacy_import`)
     blockt nicht (nur `user_explicit`/`wishlist_import`); der Fold löscht
     die Placeholder-Tracks und enqueued `wishlist_remove`-Mirrors über die
     bestehende Outbox (Drain nach Commit).
  2. **Datum-Fallback-Fehlgriff:** LOSTandFOUND (EP, 7 Tracks) teilte den
     Release-Tag mit dem Hathaway-OST (14 Tracks) und hätte dessen RG
     gestohlen. Der Fallback prüft jetzt Trackcount-Kompatibilität mit den
     bisherigen Haltern der RG (Zwei-Pass-Zuordnung).
- Außerdem: der Registry-MB-Client ist der Search-Adapter — `_default_mb_client()`
  entpackt `._client` bzw. instanziiert den rohen `MusicBrainzClient`.

### 63.4 Stufe 4 — Artist-Duplikate: Prävention + Repair

- `_pick_best_artist_match` (album_tracks.py): bei MEHREREN exakten
  Namens-Treffern gewinnt Katalog-Größe (`nb_album`), dann Fans/Follower —
  Deezers fünf „Hiroyuki Sawano"-Fragmente picken nicht mehr den 4-Alben-
  Fragment-Artist. Ohne Signale bleibt das alte First-Hit-Verhalten.
- `upsert_legacy` adoptiert vor dem Insert einen vorhandenen Artist über
  Provider-ID-Wert oder konfliktfreien Namen (nie eine Row, die schon einen
  ANDEREN Legacy-Artist spiegelt); IDs werden setdefault-gemerged.
- Neues Modul `core/library2/dedup_repair.py`:
  `repair_duplicate_artists(db)` — Namensgruppen ohne Same-Source-Konflikt
  werden in den reichsten Row gemerged (Alben/Credits/Monitor-Rules
  umgehängt), Konfliktgruppen per §40-Alias soft-verlinkt; danach werden
  gleiche Titel-Keys innerhalb des Survivors mit den Stufe-3-Regeln gefoldet
  bzw. als `duplicate_title_unmerged`-Finding notiert. Läuft am Ende jedes
  Legacy-Imports (best-effort) und via
  `POST /api/library/v2/maintenance/repair-duplicates`.
- Findings lesbar über `GET /api/library/v2/maintenance/duplicate-findings`
  (mit Artist-/Albumtiteln aufgelöst, `resolved=0`).

### 63.5 Stufe 5 — Namespace-Sanierung

- `looks_like_foreign_provider_id()` (rein numerisch oder UUID = sicher kein
  Spotify): Gate an allen lib2-Schreibpfaden — `autolink`
  (`_provider_namespace`: Provider-Marker autoritativ, sonst Shape),
  `materialize` (Payload-`source`/`provider` wird durchgereicht, numerische
  Track-IDs erreichen `lib2_tracks.spotify_id` nie mehr) und Importer
  (`_legacy_spotify_id()` an allen vier spotify_*-Mappings).
- Value-Matching bleibt kompatibel: autolink matcht nicht-Spotify-IDs gegen
  `external_ids` (namespace-bewusst) UND gegen verschmutzte `spotify_id`-
  Spalten, damit Bestandsrows weiter gefunden werden.
- `_sanitize_provider_namespaces()` (dedup_repair, läuft VOR dem
  Artist-Grouping): foreign-shaped `spotify_id`-Werte in
  lib2_artists/lib2_albums/lib2_release_editions werden aufgelöst — eigener
  external_ids-Eintrag > UUID→musicbrainz > Wert-Lookup in den
  Legacy-Spalten (itunes/deezer/tidal/qobuz) > Parkplatz `legacy_unknown`.
- `_ArtistResolver._by_provider` keyed auf `(source, value)` statt nur Wert.
- Artist-Import erfasst jetzt auch die Long-Tail-Provider-IDs
  (`_extra_provider_ids(row, "artist", …)` — vorher fehlte z. B. die
  iTunes-Artist-ID komplett).

### 63.6 Live-Verifikation gegen die echte Dev-DB (Kopie)

Sequenz `repair_duplicate_artists` → `reconcile_artist_release_groups(32)`:

- `namespaces_fixed=7`, keine numerischen spotify_ids mehr; Artist 31 in 32
  gemerged (nur noch EIN „Hiroyuki Sawano"); Sengoku-Duplikat 1167→1173
  gefoldet.
- 81 Alben bekamen RG-MBIDs (181 RGs auf MB); **die vom User gemeldete
  EN-Duplikat-Row 1274 wurde automatisch in die JP-Row gefoldet** (deezer
  42695001 adoptiert, 33 Placeholder-Tracks weg, Wishlist-Unmirrors in der
  Outbox); LOSTandFOUND blieb sauber.
- Verbleibende Findings (bewusst nicht auto-gemerged, beide Seiten haben
  echte Files): 1163↔1229 (JP-Doppel mit Files auf beiden Seiten) und
  1169↔1230 — sichtbar über den Findings-Endpoint.

### 63.7 Testabdeckung und bekannte Grenzen

- Suites: tests/library2 724 passed (inkl. 4 neue Testdateien/Testblöcke),
  tests/metadata 731 passed, plus 52 Root-Tests (admin-gating, acoustid,
  confirmed-search, watchlist-materialize). Die 3 Failures von
  `test_metadata_discography.py` in KOMBINIERTEN Läufen sind pre-existing
  Cross-File-Pollution (auf sauberem HEAD reproduziert, `_DummyConfigManager`
  leakt aus fremden Root-Testdateien).
- Bewusst offen:
  - Kein UI für `lib2_release_group_review`-Findings (nur der neue
    GET-Endpoint) und kein UI-Button für den Repair (läuft beim Import mit).
  - Legacy-Schreibseite (`spotify_worker`/Seasonal-Discovery schreiben bei
    Free-Fallback weiterhin Fremd-IDs in spotify_*-Spalten) — lib2 ist durch
    die Import-/Boundary-Guards geschützt, die Legacy-Tabellen selbst nicht.
  - `lib2_tracks`-Bestandsrows mit numerischen spotify_ids werden (anders
    als Artists/Alben/Editionen) noch nicht rückwirkend saniert.
  - Der Reconcile läuft nur nach „Update Discography"; ein periodischer
    Sweep über alle MBID-Artists wäre ein späterer Ausbau.

---

## 64. §52 H- und I-Elemente Entscheidungen (2026-07-17)

In einer schrittweisen Abstimmung mit dem Nutzer wurden am 2026-07-17 verbindliche Entscheidungen für die verbleibenden H- und I-Elemente (Legacy- und Lidarr-Feature-Gaps) getroffen. Diese Entscheidungen dienen als Richtlinie für zukünftige Entwicklungsphasen:

### I-Elemente (Lidarr-Parität)

* **I1 — Add Artist:** ❌ **Verworfen / Nicht benötigt.** Der Nutzer erachtet diese Funktion als überflüssig, da die Künstler-Suche und das Hinzufügen bereits über die Haupt-Suche/Watchlist abgedeckt sind und von dort automatisch in die Library v2 gespiegelt werden.
* **I2 — Wanted-Views (globale Listen für Missing/Cutoff Unmet):** ✅ **Beibehalten / Umsetzen.** Die globalen Listen für alle fehlenden oder verbesserungswürdigen Tracks sollen über die gesamte Library hinweg implementiert werden.
* **I3 — Mass Editor:** ❌ **Verworfen / Nicht benötigt.** Bulk-Aktionen auf Künstler-Ebene sind nicht erforderlich, das Einstellungs-Zahnrad pro Künstler ist ausreichend.
* **I4 — Metadata Profile:** ❌ **Verworfen / Nicht benötigt.** Ein drittes Profil-System ist redundant. Die in den Artist Settings konsolidierten Watchlist-Regeln (`include_*` + `monitor_new_items`) genügen vollkommen.
* **I5 — Kalender / kommende Releases:** ❌ **Verworfen / Ausdrücklich abgelehnt.** (Bereits im Review vom 17. Juli abgelehnt).
* **I6 — Queue-Sichtbarkeit an der Entity:** ✅ **Beibehalten / Umsetzen.** Der Status und Fortschritt laufender Downloads soll direkt an Album- und Track-Zeilen visualisiert werden.
* **I7 — Blocklist-Ansicht:** ❌ **Verworfen / Nicht benötigt.** Keine eigene UI für blockierte Download-Kandidaten erforderlich.
* **I8 — Root-Folder/Pfad + Diskspace am Artist:** ✅ **Teilweise Beibehalten / Umsetzen.** Die Speicherplatzbelegung (Größe) pro Artist und Album soll angezeigt werden, der absolute Pfad hingegen nicht.
* **I9 — Unmapped Files:** ❌ **Verworfen / Nicht benötigt.** Ein UI-Bereich für nicht-zugeordnete Mediendateien ist nicht erforderlich; der Hintergrund-Job `orphan_file_detector` reicht aus.
* **I10 — „Search on monitor“ (opt-in):** ❌ **Verworfen / Nicht benötigt.** Das gezielte Suchen beim Überwachen läuft bereits direkt und granular über den Automatic Search Button des jeweiligen Tracks.

### H-Elemente (Legacy-Parität)

* **H2 — Artist Top Tracks:** ❌ **Verworfen / Ausdrücklich nicht gewollt.** (Bereits im Review vom 17. Juli abgelehnt).
* **H3 — Discography-Download-Modal:** ❌ **Verworfen / Ausdrücklich nicht gewollt.** (Bereits im Review vom 17. Juli abgelehnt).
* **H4 — Track-Redownload-Modal:** ⏸️ **Aufgeschoben / Nicht benötigt.** (Falls später, gilt das Prinzip: neu suchen und erst nach verifiziertem Import atomar ersetzen).
* **H6 — A-Z-Alphabet-Selector, Source-Filter & Stats-Header:** ❌ **Verworfen / Nicht benötigt.** Moderne Textsuche und Paging ersetzen die Buchstabenleiste vollkommen.
* **H7 — Inline-Edit in der Tabelle:** ❌ **Verworfen / Nicht benötigt.** Editierungen laufen robuster und sicherer ausschließlich über Detail-Modale.
* **H8 — Bulk-Selektion + Bulk-Bar / Bulk-Edit-Modal:** ✅ **Bereits erledigt** — geprüft am 2026-07-18: `BulkEditTracksModal` (`webui/src/routes/library-v2/-ui/library-v2-page.tsx`) wurde bereits am 2026-07-16 in §34 (Commit `c03150bf`, vor dieser Entscheidung) gebaut und deckt Style/Mood/BPM/Explicit ab — exakt Legacys tatsächliches `showBulkEditModal`-Feldset (Track Number/BPM/Style/Mood/Explicit) minus des Bulk-Track-Number-Felds, dessen Eins-zu-eins-Übertragung auf mehrere verschiedene Tracks ohnehin keinen Sinn ergibt. Die hier ursprünglich genannten Felder „Genre, Jahr" waren nie Teil von Legacys echtem Bulk-Modal (Faktenirrtum in dieser Zeile); kein weiterer Code nötig.
* **H9 — Report-Button für Nicht-Admins:** ❌ **Verworfen / Nicht benötigt.** Library v2 ist rein für Administratoren konzipiert (admin-only).
* **H10 — „Watch All Unwatched“-Bulk-Tool:** ❌ **Verworfen / Nicht benötigt.** Gezielte Auswahl über Bookmarks/Watchlist ist besser, um API-Limits nicht zu überlasten.
* **H11 — Artist-Record-Inspector:** ❌ **Verworfen / Nicht benötigt.** Eine rohe JSON-Ansicht in der User-UI ist nicht notwendig; Entwickler können die Browser-Konsole oder SQLite nutzen.
* **H12 — Export (Artist-Roster + M3U-Export):** ⏸️ **Aufgeschoben / Zurückgestellt.**

---

## 65. Legacy-TEXT-IDs im Importer und in Metadata-Brücken — ✅ gefixt (2026-07-17)

**Produktionsbefund:** Der Library-v2-Import brach auf einer Main-Instanz in
`importer.py:977` mit
`invalid literal for int() with base 10: '01MoTj8w4VkVtgdPOijUUE'` ab.
Die Legacy-Library verwendet für `artists.id`/`albums.id`/`tracks.id` und die
zugehörigen Fremdschlüssel absichtlich `TEXT`; je nach Media-Server bzw.
Erzeugungspfad kann ein solcher Schlüssel numerisch, UUID-förmig oder
Spotify-Base62-förmig sein.

### 65.1 Root Cause und Importer-Fix

- `album_map`, `_ArtistResolver` und `track_map` verwendeten seit §38/§40
  bereits `_legacy_key()` und behandelten Legacy-IDs damit korrekt als opaque
  Strings.
- Nur `actual_track_counts` und `present_track_counts` wandelten
  `tracks.album_id` noch mit `int(...)` um. Dadurch scheiterte eine gültige
  alphanumerische Album-ID, bevor das erste Album materialisiert wurde.
- Beide Count-Maps und ihre Album-Lookups verwenden jetzt ebenfalls
  `_legacy_key()`. Single-Erkennung, Ownership-/Monitoring-Ableitung und
  Re-Import-Idempotenz funktionieren damit für numerische und opaque IDs
  identisch.

### 65.2 Zusammenspiel mit §63 (Matching und Provider-Namespaces)

Die Korrektur vermischt keine Identitätsebenen: Eine Spotify-förmige
`albums.id` ist weiterhin ausschließlich ein Legacy-PK/Backref im Namespace
`legacy_album`. Sie wird **nicht** automatisch zu `lib2_albums.spotify_id`
oder `external_ids.spotify`. Eine Provider-ID entsteht nur aus der expliziten
Legacy-Provider-Spalte bzw. einer nachweisbaren Provider-Herkunft. Damit bleiben
§63.1–§63.5 (Titel-/Datum-/Trackcount-Matching, Editions, MB-Reconcile,
Artist-Dedup und Namespace-Sanierung) unverändert und scharf getrennt.

Der anschließende Boundary-Audit fand denselben veralteten Integer-Zwang noch
in nachgelagerten Library-v2-Brücken. Diese akzeptieren nun ebenfalls opaque
Legacy-IDs:

- Match-Chips und `metadata_match_provenance` (inklusive manueller Matches und
  Album-Track-Bundle),
- Track Source Info und Download-/Pipeline-History,
- Enrich + Resync,
- Reorganize-Bridge,
- Frontend-Verträge für `legacy_entity_id` (`number | string`).

Die lokalen lib2-Entity-IDs bleiben unverändert numerische Surrogat-IDs; nur
die Backrefs in die Legacy-Library sind polymorph. Regressionstests verwenden
die konkrete Produktions-ID `01MoTj8w4VkVtgdPOijUUE`, prüfen Import +
Re-Import, fehlende Provider-Namespace-Kontamination sowie alle genannten
Brücken. Verifikation: 216 fokussierte Backend-Tests (inkl. §63 Discography,
Editions, MB-Reconcile und Dedup-Repair) und 57 betroffene Frontend-Tests grün.

---

## 66. Import-Abschluss von Artwork-Precache entkoppelt + Cache beschleunigt — ✅ umgesetzt (2026-07-17)

### 66.1 Bottleneck und neue Abschlussgrenze

Der eigentliche Legacy→lib2-Import bleibt der in §17.6 beschriebene lokale
SQLite-Pfad. Langsam war weiterhin die nachgelagerte Artwork-Stufe: pro
ungecachtem Artist/Album können Pfadauflösung und Datei-`stat` auf einem NAS,
Mutagen-Reads des Embedded Covers, Pillow-Decode/JPEG/Thumbnail-Encoding sowie
synchrone Provider-Auflösung und Bild-Download anfallen. Die Parallelisierung
aus §20 verkürzte diese Stufe, ließ sie aber weiterhin im selben
`_import_state.running`-Lebenszyklus. Die UI invalidierte ihre Library-Queries
daher erst nach dem letzten Cover.

Artwork ist jetzt eine eigene, nicht-kritische Background-Stufe mit dem
Statusobjekt `artwork_cache` (`running/current/total/stats/error/started_at/
finished_at`) im bestehenden Import-Statusendpoint. Der verbindliche Import
gilt nach folgenden weiterhin synchron garantierten Schritten als fertig:

1. Artists/Alben/Tracks/Files + Monitoring/Wanted/Editionen,
2. Post-Import-Dedup und Namespace-Sanierung aus §63,
3. Tracklist-Materialisierung,
4. Tag-Cache (Lyrics/ReplayGain/Gap-Fakten).

Danach wechselt der Hauptstatus auf `done`, `running=false`; die UI invalidiert
sofort `LIBRARY_V2_QUERY_KEY` und zeigt „Library ready to browse“, während sie
den Artwork-Fortschritt separat weiterpollt. Ein Artwork-Fehler macht einen
erfolgreichen Import nie nachträglich fehlgeschlagen: fehlende Covers werden
über den bestehenden HTTP-Slowpath weiterhin on demand aufgelöst. Ein Re-Import
wird während des kurzen Background-Jobs abgewiesen, um einen destruktiven
`reset` nicht mit offenen Artwork-DB-Reads zu überlappen.

### 66.2 Beschleunigung ohne Semantikänderung

- Artwork hat einen eigenen optionalen Worker-Key
  `library_v2.artwork_cache_workers`, Default 6, Hard-Cap 16. Fehlt der Key,
  bleibt ein vorhandenes `auto_import.max_workers` als kompatibler Fallback
  wirksam. Tracklist- und Tag-Worker werden nicht ungefragt hochgedreht.
- UI-Slowpath und Precache teilen jetzt denselben Per-DB/Entity-Single-Flight-
  Lock in `core/library2/artwork.py`. Sofortiges Browsen während des Precaches
  erzeugt daher keinen doppelten Provider-/NAS-Read und keine konkurrierenden
  `.writing`-Dateien; manuelle Art-Picks nutzen dieselbe Schreibgrenze.
- Der Pending-Scan erzeugt das Cache-Verzeichnis einmal statt über
  `artwork_file()` pro Entity. Nach `resolve_lib2_path` entfällt ein redundanter
  zweiter `exists()`-Call auf den Mediendateipfad.
- Full-JPEG und Thumbnail werden nach genau einem Pillow-Decode/EXIF-Transpose
  aus demselben RGB-Bild erzeugt. Die P2-04-Garantie bleibt vollständig
  erhalten: beliebige Embedded-/Provider-Bytes werden validiert, Transparenz
  wird auf Weiß komponiert, Cachedatei und Thumbnail sind echte JPEGs. Es wird
  lediglich das erneute Öffnen und Dekodieren des gerade geschriebenen Full-
  JPEGs eingespart.

### 66.3 Abgrenzung zu §63/§65 und Verifikation

Nicht verändert wurden Titel-/Datum-/Trackcount-Matching, Editionen,
MusicBrainz-Reconcile, Artist-Adoption/Dedup, Provider-Namespaces und opaque
Legacy-Backrefs. Artwork bleibt medienserver-unabhängig und alle Dateizugriffe
laufen weiterhin über `resolve_lib2_path`.

Verifikation: `tests/library2` **736 passed**; gezielter Artwork/API-Block
**149 passed**; Frontend Format/Lint/Typecheck ohne Fehler, Vitest **38 Dateien /
224 Tests passed**. Die neuen Verträge prüfen den vorgezogenen Importabschluss
bei absichtlich blockiertem Artwork-Worker, Background-Polling/UX,
Single-Flight, Worker-Cap, JPEG+Thumbnail-Normalisierung und den vermiedenen
doppelten NAS-`stat`. Der repository-weite Python-Lauf erreichte **10.445
passed, 3 skipped, 2 deselected** bei 21 Failures: 20 reproduzieren isoliert in
den bereits in §41 dokumentierten, unberührten Baseline-Dateien
`test_cross_batch_dedup.py`/`test_normalize_version_symmetry.py` (20 failed,
4 passed); der einzelne Video-Fehler ist Cross-File-Pollution und läuft isoliert
mit der ganzen Datei grün (8 passed). Production-Build erfolgreich; nur der
bekannte Main-Chunk-Größenhinweis bleibt.

---

## 67. Import-Monitoring, vollständige Tracklisten und „tote Artists“ — ✅ umgesetzt (2026-07-17)

### 67.1 Verbindliche Monitoring-Regel

Der Import unterscheidet jetzt Parent-Intent und konkrete Track-Abdeckung:

1. Ein Track mit aktiver lokaler Datei erhält `file_import`; ein Track aus der
   Admin-Wishlist erhält `wishlist_import`. Beide sind individuell monitored
   und wanted, auch wenn ihr Album nicht vollständig ist.
2. Album/EP/Single werden nur dann als Parent monitored, wenn die kanonische
   Gesamtgröße bekannt/repräsentiert ist und **jeder** Track entweder eine
   aktive Datei oder eine Wishlist-Regel besitzt.
3. Daraus folgen die gewünschten Fälle ohne Sonderheuristik: ein 1-Track-
   Single mit Datei/Wishlist ist vollständig; ein partielles Album bleibt als
   Parent aus, seine vorhandenen/gewünschten Tracks bleiben einzeln an; Dateien
   plus Wishlist für sämtliche übrigen Slots machen das Album vollständig an.
4. `user_explicit`, `cascade` und `new_release` werden beim Reimport nicht von
   dieser abgeleiteten Import-Baseline überschrieben. Die Wanted-Projektion
   wurde wegen der neuen `file_import`-Prioritätsstufe auf Version 2 erhöht.

### 67.2 Root Cause „Dangerous zeigt nur Track 7“ und Discovery-Reparatur

`seed_wishlist_tracks()` übernahm zunächst korrekt
`album.total_tracks`, setzte danach aber für Wishlist-only-Releases sowohl
`track_count` als auch `expected_track_count` wieder auf die Zahl der bereits
angelegten Wishlist-Rows. Aus „Dangerous, 14 Tracks, Wishlist Track 7“ wurde so
„1 erwartet, 1 vorhanden“. `_partial_album_rows()` sah keine Lücke und der
Tracklist-Precache lief nie. Artwork war daran nicht beteiligt.

Der Clamp ist entfernt. Bis zur Provider-Auflösung zeigt die Detailansicht alle
erwarteten Slots als Missing-Placeholder; der weiterhin importkritische
Tracklist-Precache materialisiert anschließend echte Titel/Nummern. Ein
fehlgeschlagener Provider-Call reduziert die erwartete Größe nicht wieder.

„Update Discovery“ heilt außerdem Bestandsimporte:

- Provider-Trackcounts konvergieren jetzt monoton nach oben (`MAX`) statt einen
  alten Wert durch `COALESCE` für immer festzuhalten.
- Nach dem Discography-Abgleich werden unterfüllte `origin='library'`-Releases
  gezielt über `resolve_tracklist()` materialisiert. Das ist reine
  Browse-/Metadaten-Reparatur: keine `new_release`-Regel, keine pauschale
  Monitor-Kaskade.
- Nach jeder kanonischen Materialisierung wird die vollständige
  Datei/Wishlist-Abdeckung erneut berechnet.

### 67.3 Root Cause „Artist hat Stats, aber keine Releases“

Der Import schrieb zusätzliche Credits nur nach `lib2_track_artists`. Die
Artist-Liste zählte deshalb den Track, während `get_artist()` Releases korrekt
über `lib2_album_artists` liest. Ein Featured Artist konnte somit „1
present/missing“ anzeigen und gleichzeitig leere My-Library-/All-Releases-
Bereiche haben.

Der Import schreibt für jeden Track-Credit nun auch eine idempotente
Album-Appearance (`role='featured'`; der Album-Artist bleibt `primary`). Damit
zeigt die Artist-Seite wenigstens das Release, auf dem der gezählte Track
tatsächlich vorkommt, und Discovery kann denselben Artist-/Album-Junction-Pfad
verwenden. Für bereits importierte Produktionsdaten vereinigt das Read-Model
zusätzlich Album-Junctions mit den über `lib2_track_artists` belegten
Appearances; die leere Artist-Seite ist damit direkt nach dem Update behoben,
noch bevor ein Reimport die dauerhafte Junction nachzieht.

Native lib2-Zeilen ohne `legacy_artist_id`/`legacy_album_id`/`legacy_track_id`
waren zusätzlich im Frontend nicht manuell matchbar: die Chips waren hart an
den Legacy-Endpoint gekoppelt und disabled. Match-Status liefert nun immer eine
`library_v2_entity_id`; ein eigener admin-gated PUT/DELETE-Pfad schreibt
dedizierte Spotify-/MusicBrainz-IDs bzw. namespace-korrekte `external_ids` und
manuelle Provenienz. Legacy-backed Rows benutzen weiterhin unverändert den
Legacy-Pfad.

### 67.4 Abgrenzung zu §63/§65

Die Identitätsregeln bleiben unverändert: opaque Legacy-TEXT-IDs werden nicht
zu Provider-IDs umgedeutet; manuelle native Matches verlangen einen expliziten
Provider-Namespace; Discography-Matching, Editions, MB-Reconcile und
Duplicate-Repair aus §63 bleiben bestehen. Die neue unterfüllte-Tracklist-
Reparatur reichert nur bereits gematchte Release-Rows an und führt weder einen
neuen unsicheren Title-Fold noch eine Namespace-Konvertierung ein.

### 67.5 Verifikation

- Library-v2-Backend: **744 passed**.
- Frontend: Format/Lint/Typecheck ohne Fehler; Vitest **38 Dateien / 226 Tests
  passed**; Production-Build erfolgreich (nur bekannter Chunk-Hinweis).
- Repository-weiter Python-Lauf: **10.454 passed, 3 skipped, 2 deselected**.
  Die verbleibenden 20 Failures sind exakt die bereits dokumentierten,
  unberührten Baseline-Dateien `tests/downloads/test_cross_batch_dedup.py` (6)
  und `tests/matching/test_normalize_version_symmetry.py` (14); keine neue
  Library-v2-Regression.

---

## 68. „Unmapped Artists" — native Artists enrichbar machen + smart-split — ✅ umgesetzt (2026-07-17)

**Auslöser:** Nutzer-Bugreport (`~/Desktop/Artist not matchet failiuers`, drei
Fälle: Afrojack #1209, „Big Sean and BabyTron" #1214, „Ian Asher & Galantis"
#1153). Importierte „Artists" zeigen alle Provider-Chips `pending`/`not_found`,
kein Cover, keine Metadaten. Genaue Analyse ergab **zwei** verschiedene
Ursachen.

### 68.1 Root Cause RC1 — native Artists haben keinen Enrichment-Pfad

Artists, die INNERHALB von lib2 entstehen — Featured-Credits
(`_featured_names_for_import`), Wishlist-Zeilen, Discography-Discoveries —
tragen `legacy_artist_id = NULL`. Die gesamte Metadaten-/Enrichment-Maschine ist
legacy-zeilen-basiert (`web_server._run_single_enrichment` schreibt die Legacy-
`artists`-Zeile, `core.library2.enrich.resync_entity_from_legacy` spiegelt
zurück). Für einen nativen Artist ist damit **jeder Pfad eine Sackgasse**:

- **Enrich** lehnt `legacy_id IS NULL` hart ab (`api/library_v2.py`).
- **Manual-Match** (§67) schreibt zwar die ID, zieht aber **kein Artwork**.
- **Discography-Expand** löst zwar per Name auf, wirft die gefundene Artist-
  Provider-ID aber weg (`discography.py` schreibt nur `discography_synced_at`;
  `provider_adapters.fetch_artist_discography` echot nur die Eingabe-ID) und
  braucht Monitoring als Trigger.

⇒ §67 machte native Artists *matchbar*, aber nie *enrichbar*. Die Match-Status-
Chips synthetisieren für native Zeilen aus den eigenen `spotify_id`/
`external_ids` — leer ⇒ alles `pending`.

**Fix:**

- `core/metadata/album_tracks.py::resolve_artist_identity(name)` — läuft die
  Source-Priority-Kette, nimmt den strikten `_pick_best_artist_match` (#988/
  §62.5) und gibt `{source, artist_id, name, image_url, genres}` zurück oder
  `None`. Das war der fehlende Baustein: die Namensauflösung existierte in
  `get_artist_discography`, die gefundene Artist-ID wurde aber nie
  herausgereicht.
- Neues Modul `core/library2/native_enrich.py`:
  - `resolve_and_enrich_native_artist` — löst per Name auf und schreibt ID
    (Spotify/MB in die dedizierte Spalte, sonst namespace-korrekt in
    `external_ids`) + Cover + Genres **direkt** auf die lib2-Zeile; lehnt
    legacy-backed Zeilen ab.
  - `enrich_native_artist_artwork` — zieht Cover aus bereits gespeicherten IDs
    (für den Manual-Match-Pfad).
  - `reconcile_unmapped_native_artists` — Backlog-Heiler über alle nativen
    Artists ohne Provider-ID.
  - Resolver/Artwork-Fetcher sind injizierbar (DI), damit Tests nie den
    Metadaten-Stack ziehen.
- Verdrahtung (`api/library_v2.py`): **Enrich**-Endpoint bekommt einen nativen
  Artist-Zweig (statt 409); **Manual-Match** zieht nach dem ID-Setzen jetzt
  best-effort das Cover; neuer Background-Job
  `POST /api/library/v2/maintenance/reconcile-unmapped-artists`.
- Frontend: Button **„Reconcile Unmapped Artists"** in der Maintenance-Modal
  (pollt `jobs/status`, zeigt `scanned/matched/split/unmatched`).
- **Bewusst NICHT umgesetzt:** Provenienz-Badge für native Zeilen.
  `metadata_match_provenance` hat `CHECK(entity_type IN artist/album/track)` —
  eine Erweiterung bräuchte einen Tabellen-Rebuild; der Chip flippt ohnehin
  über die gespeicherte ID auf `matched`, der Origin-Badge ist kosmetisch und
  wird bei Bedarf später nachgezogen.

### 68.2 Root Cause RC2 — Kollab-Namen sind kein einzelner Provider-Artist

„Big Sean and BabyTron", „Ian Asher & Galantis" sind zwei Artists per `and`/`&`.
Der P2-24-Guard (§41) teilt mehrdeutige `and`/`&`/Komma-Credits bewusst NICHT
(schützt echte Bandnamen wie „Hall & Oates"). Folge: solche Zeilen existieren
als ein Artist, den kein Provider als eine Entität kennt (Ian Asher & Galantis
wurde tatsächlich versucht → überall `not_found` außer einer Last.fm-Fuzzy-
Seite).

**Fix — smart-split mit Provider-Sicherheitscheck** (Nutzer-Entscheid: Variante
A, destruktiv, Ghost löschen). `native_enrich.smart_split_combined_artist`:

- Läuft nur als Fallback, wenn der Single-Entity-Resolve fehlschlägt. Ein echter
  Bandname wie „Hall & Oates" wird upstream als eine Entität gematcht und
  erreicht den Split nie — starke Garantie: gesplittet wird **nur** bei echten
  Konkatenationen.
- Teilt den Namen (`split_artist_credits`) und verlangt, dass **jede** Komponente
  zu einem echten Provider-Artist auflöst; sonst Abbruch (kein Phantom).
- Jede Komponente wird zu einem echten (enrichten) Artist
  (`_get_or_create_component_artist` — bestehende Zeile wird wiederverwendet,
  nie dupliziert); der Release wird auf die erste Komponente umgehängt, die
  übrigen als Credits ergänzt, der kombinierte Ghost gelöscht.
- **Cascade-Sicherheit (kritisch):** `lib2_albums.primary_artist_id` ist
  `ON DELETE CASCADE` — würde der Ghost gelöscht, solange er Primary eines Albums
  ist, riss es Album + Tracks + Files mit. `_rehome_and_delete_combined` hängt
  daher ZUERST alle Primaries um und schreibt die Junctions neu; der finale
  Delete findet dann keine Abhängigkeit mehr. Reihenfolge ist getestet.
- Im Reconcile-Job zwischen `matched` und `unmatched` eingehängt; Ergebnis
  zählt `split`.

### 68.3 Verifikation

- Library-v2-Backend: **760 passed** (16 neue Tests: `test_native_enrich.py` 13,
  `test_resolve_artist_identity.py` 4 — sowie `test_enrich_endpoint.py` +3).
- Ruff für alle geänderten Python-Dateien sauber.
- Frontend: `npm run check` (Format + Typecheck + oxlint) 0 Warnungen/0 Fehler;
  Vitest **226 passed**; Production-Build erfolgreich (nur bekannter Chunk-
  Hinweis).
- **Reale Bestätigung steht beim Nutzer aus:** die drei Beispiel-Artists liegen
  auf seinem Prod-Server (nicht in der Dev-DB). Endgültiger Beweis = „Reconcile
  Unmapped Artists" dort klicken (Afrojack → auto-matched; Kollab-Namen →
  gesplittet oder manuell matchbar MIT Cover).

---

## 69. Offene Nutzer-Reports 2026-07-17 — Watchlist/Wishlist-Sync + Manual-Grab/Auto-Search — 🔍 gemeldet, noch NICHT untersucht

Aus derselben Session wie §68. **Reine Erfassung der gemeldeten Symptome** —
noch keine Root-Cause-Analyse, noch keine Umsetzung. Reihenfolge nach Nutzer-
Priorität: erst §68 (erledigt), dann §69.1, dann §69.2. Für §69.1 besteht
Dev-DB-Zugriff.

### 69.1 Watchlist ↔ Library-Monitored muss BEIDSEITIG synchron sein — ✅ gefixt (2026-07-18, siehe §70)

**Gemeldet:** Der Sync ist derzeit nur einseitig.

- ✅ **Library → Watchlist funktioniert:** einen Artist auf *Monitored* setzen
  legt ihn in der Watchlist an (`enqueue_artist_watchlist`, `api/library_v2.py`).
- ❌ **Watchlist → Library fehlt:**
  - *„Clear Watchlist"* muss ALLE zugehörigen Library-Artists wieder
    **demonitoren** — sonst bleibt es bei uns `monitored`, ist aber nicht mehr
    in der Watchlist (Zustände laufen auseinander).
  - Einen Artist in der Watchlist **löschen** muss denselben Artist in der
    Library demonitoren. Und umgekehrt.
- ❌ **Track-Monitored ↔ Wishlist-Gap (gleiche Klasse Bug):** Songs sind bei uns
  `monitored`, stehen aber NICHT in der Wishlist. Konkretes Beispiel des
  Nutzers: **„Lost and Found" von SawanoHiroyuki[nZk]** — monitored, aber nicht
  in der Wishlist. Ein monitored *missing* Track muss in der Wishlist landen und
  umgekehrt.

**Zu untersuchen:** die Library→Watchlist-Kante (`enqueue_artist_watchlist`,
`core/library2/wishlist_mirror.py`, `mirror_outbox.py`, `core/watchlist_scanner.py`)
und die fehlende Rück-Kante (Watchlist-Delete/Clear → lib2 demonitor) bauen;
plus den Track-`monitored` → Wishlist-Mirror reparieren, warum ein monitored
missing Track (Sawano) nicht projiziert wurde. Beidseitig, idempotent.

### 69.2 Manual Grab (Interactive Search) — geht nicht in die Download-Pipeline

**Gemeldet:** Manual-Grab/Interactive-Search scheint end-to-end nicht zu
funktionieren. UI zeigt „Downloading"/„gegrabbt", aber:
- es geht offenbar nicht in die Download-Pipeline,
- der Track erscheint **nie** in Library V2,
- in der **Quarantäne** ist ebenfalls nichts zu finden.

⇒ Wahrscheinlich noch nicht (vollständig) implementiert. **Muss neu analysiert
werden:** wohin der Manual-Grab-Dispatch geht, ob er die Download-/Post-
Processing-Pipeline überhaupt erreicht, und wo er verloren geht. (Vgl. §60
„Manual-Grab-Force-Confirm" — die Force-Bestätigung wurde umgesetzt, aber der
Ende-zu-Ende-Durchlauf offenbar nicht verifiziert.)

### 69.3 Automatic Search eines einzelnen Tracks — Wishlist wird nicht ausgeführt

**Gemeldet:** Ein *Automatic Search* auf einen bestimmten Track legt ihn zwar in
die Wishlist, führt ihn aber nicht sofort aus. Erwartet: **genau dieser eine
Track** wird direkt regulär gesucht/abgearbeitet — wie jeder andere Wishlist-
Track — **ohne die gesamte Wishlist zu starten**. (Verwandt mit §69.1 Track-
Wishlist-Kante und §29/C1 scoped Automatic Search; hier fehlt die sofortige
Einzel-Track-Ausführung nach dem Wishlist-Insert.)

---

## 70. §69.1 Watchlist ↔ Library-Monitored beidseitig synchron — ✅ umgesetzt (2026-07-18)

Root-Cause gegen die reale Dev-DB verifiziert (read-only Kopie). Der Mirror war
**rein edge-getriggert**: eine lib2-Monitor-Umschaltung spiegelt via Outbox in
Watchlist/Wishlist (`mirror_outbox`, `wishlist_mirror`), aber nur *im Moment des
Toggles*. Daraus zwei Lücken:

### 70.1 Befund an der echten DB

- **Wishlist komplett leer (0 Zeilen)**, obwohl **168 Tracks autoritativ
  `wanted=1`** sind (davon **72 monitored+missing**). Historie: 3370
  `wishlist_add` + 3289 `wishlist_remove` (done), letzter Outbox-Batch waren
  Massen-`wishlist_remove`. Sobald ein Wishlist-Eintrag geht (heruntergeladen,
  „Clear", TTL), bringt ihn **nichts** zurück — kein Job re-projiziert die
  autoritative `lib2_wanted_tracks` in die Wishlist. Das ist exakt §69.1 „ein
  monitored missing Track muss in der Wishlist landen" (Nutzerbeispiel
  SawanoHiroyuki[nZk] „Lost and Found").
- **Watchlist → Library fehlte ganz.** `db.remove_artist_from_watchlist`
  löscht nur die Legacy-Zeile; der passende lib2-Artist blieb `monitored=1`.
  „Clear Watchlist" / Artist löschen ließ die Zustände auseinanderlaufen.

### 70.2 Fix (`core/library2/monitor_sync.py`)

- **Rück-Kante Watchlist → Library (event-getrieben).**
  `demonitor_lib2_artists_for_removed_watchlist` matcht den entfernten
  Watchlist-Artist per Provider-ID (`spotify_id`/`musicbrainz_id`/`external_ids`)
  bzw. als Fallback per normalisiertem Namen auf die lib2-Zeile(n), setzt sie
  `monitored=0`, schreibt eine `user_explicit`-Unmonitor-Regel, re-projiziert
  und mirrort **nur die Artist-tier-entschiedenen Tracks** (`reason LIKE
  'artist_rule%'`) — owned Tracks mit Album-/Track-Regel bleiben unberührt,
  genau wie der Forward-Toggle Track-Flags nie cascadet. **Kein** erneutes
  `enqueue_artist_watchlist` (die Zeile ist bereits weg → keine Loop). Idempotent.
- **Choke-Point ist der Endpoint, NICHT die DB-Methode.** `remove_artist_from_watchlist`
  ist auch der *Rückweg* des Forward-Mirrors (`mirror_outbox._execute_op`) —
  ein Hook dort hätte einen Feedback-Loop erzeugt. Verdrahtet daher an den
  drei nutzer­seitigen Removal-Pfaden: `web_server` `/api/watchlist/remove`
  (single) + `/api/watchlist/remove-batch` (= „Clear Watchlist"), sowie dem
  Blueprint `api/watchlist.py` DELETE. Neuer DB-Helper
  `get_watchlist_artist_descriptor` erfasst Name + alle Provider-IDs **vor** dem
  Löschen. Feature-gated + best-effort (`sync_watchlist_removal`) — ein
  Reverse-Sync-Fehler darf die Watchlist-Entfernung nie brechen.
- **Wanted-Projektion → Wishlist Re-Assertion (Reconcile).**
  `reconcile_track_wishlist` recomputet die volle Projektion und mirrort sie
  über denselben Outbox-Pfad wie die Toggles: jeder `wanted`-Track wird
  (re-)eingereiht (nur ohne befriedigende Datei → `should_queue`), jeder noch
  in der Wishlist stehende lib2-Track ohne `wanted` wird geprunt („und
  umgekehrt"). `user_initiated=False` ⇒ die Ignore-Liste (bewusste Cancels)
  bleibt respektiert.

### 70.3 Verdrahtung & Auslösepfade

- Neuer Repair-Job `core/repair_jobs/lib2_wishlist_reconcile.py`
  (`default_enabled=True`, Intervall 6 h) ⇒ heilt Drift dauerhaft von selbst.
  Bewusst *kein* Artist-Reconcile-Job: „nicht in der Watchlist" ist nicht von
  „nie hinzugefügt" unterscheidbar, also ist die Rück-Kante zwingend
  event-getrieben, nicht reconcile-ableitbar. (`lib2_mirror_reconcile` drainiert
  nur PENDING-Outbox-Rows und re-derived nichts — deckt die Lücke nicht.)
- Neuer Maintenance-Endpoint `POST /api/library/v2/maintenance/reconcile-wishlist`
  (Background-Job, poll `jobs/status`) + Frontend-Button **„Reconcile Wishlist"**
  in der Maintenance-Modal (Ergebnis `{wanted, wishlisted, mirrored}`), damit
  der Nutzer den aktuell leeren Wishlist-Backlog sofort heilen kann.

### 70.4 Verifikation

- Neue Tests `tests/library2/test_monitor_sync.py` (8): Reconcile re-added
  monitored+missing / prunet not-wanted / idempotent; Reverse-Edge matcht per
  Spotify-ID + Namens-Fallback, idempotent, No-Match=No-Op, Feature-Gate.
- **Real-DB-Beweis** (read-only Kopie): Reconcile reiht die 72 fehlenden Tracks
  ein (73 Adds inkl. 1 Upgrade-Kandidat, 0 Removes bei leerer Wishlist);
  „Clear Watchlist" demonitort beide realen Artists (VØJ, Justin Bieber) →
  0 monitored lib2-Artists, mirror-Ops verengt (VØJ 9→0, Bieber 219→12).
- `tests/library2` + `tests/wishlist` + `tests/repair` + `tests/repair_jobs`
  + Watchlist-Tests: **1026 passed**. Ruff sauber (inkl. `web_server.py`).
  Frontend `npm run check` 0/0, Vitest **226 passed**, Production-Build ok.

### 70.5 Offen aus §69

§69.2 (Manual Grab erreicht die Download-Pipeline nicht) und §69.3 (Automatic
Search eines Einzel-Tracks führt die Wishlist nicht sofort aus) bleiben offen —
separate Analyse.

---

## 71. §69.3 behoben (Such-Pool-Contention) und §69.2 behoben (Manual Grab durchläuft jetzt die volle Pipeline) (2026-07-18)

### 71.1 §69.3 — ✅ behoben

Root Cause verifiziert: der scoped-Search-Endpoint (`POST
/api/library/v2/<entity>/<id>/search`, §29/C1) dispatcht bereits korrekt und
synchron über `start_manual_wishlist_download_batch` — dieselbe Engine wie
„Download Wishlist" — kein zweiter Pfad, keine Verzögerung durch einen
periodischen Zyklus. Die tatsächliche Ursache für „läuft nicht sofort": der
Dispatch teilt sich `missing_download_executor` (3 Worker) mit dem
periodischen Wishlist-Auto-Zyklus (`core/wishlist/processing.py`, dieselbe
Klasse Bug wie #740, dort aber nur für Album-Bundles gefixt — Einzeltrack-
Dispatch teilte weiterhin den Pool). Bei einem aktiven Auto-Zyklus blockierten
alle 3 Worker-Slots, sodass ein Scoped-Search-Klick real hinter der Batch-
Verarbeitung „verhungerte" — von außen ununterscheidbar vom gemeldeten
Symptom. Zusätzlich verschluckte der Endpoint einen fehlgeschlagenen
Dispatch (`batch_payload.get("success") == False`) stillschweigend — die UI
zeigte „Search started", obwohl nichts lief.

**Fix:**
- Neuer dedizierter `scoped_search_executor` (2 Worker,
  `web_server.py`), analog zum bestehenden `album_bundle_executor`-Präzedenzfall
  (#740): `_library_v2_scoped_wishlist_search` übergibt ihn statt des
  geteilten Pools an `_WishlistManualDownloadRuntime.missing_download_executor`
  — sowohl die Vorbereitung als auch der eigentliche Track-Download laufen
  darüber, ohne den globalen Wishlist-Flow zu verändern.
  In den Shutdown-Pfad aufgenommen.
- `api/library_v2.py::lib2_scoped_search`: ein fehlgeschlagener Dispatch wird
  jetzt geloggt und als `dispatch_error` im Job-Result zurückgegeben.
  Frontend (`runScopedSearch`, `library-v2-page.tsx`) zeigt bei gesetztem
  `dispatch_error` jetzt eine Fehler- statt einer OK-Banner.
- `LibraryV2JobState.result` Typ erweitert (`Record<string, number | string |
  null>`), da `batch_id`/`dispatch_error` Strings sind.

**Verifikation:** `tests/library2/test_scoped_search_endpoint.py` (neuer Test
`test_dispatcher_failure_is_surfaced_not_silently_swallowed` + 2 bestehende
Tests um das neue `dispatch_error`-Feld ergänzt) — 9/9 grün.
`tests/library2` + `tests/wishlist` gesamt: 939 grün. `ruff check` sauber.
Frontend `tsc --noEmit` clean, `vitest run src/routes/library-v2/` 131 grün
(23 Dateien), `npm run check` (oxfmt+oxlint) clean.

### 71.2 §69.2 — ✅ behoben

Die ursprüngliche Hypothese (kein serverseitiges Completion-Detection für
manuelle Grabs) ist **widerlegt**: `_simple_monitor_task` (`web_server.py`,
seit App-Start unbedingt aktiv, 1s-Poll) ruft dieselbe Logik wie
`GET /api/downloads/status` unabhängig von jedem Frontend-Polling auf, sobald
`matched_downloads_context` nicht leer ist.

**Der tatsächliche Fund:** `web_server.py::start_download` setzte für JEDEN
manuellen Grab unbedingt `search_result.is_simple_download = True`
(Album- und Single-Track-Zweig) — unabhängig davon, ob `_lib2_ctx` eine
echte Library-v2-Entität aufgelöst hat. Das routete die Datei in
`core/imports/pipeline.py`s „Simple Download"-Kurzschluss: Datei landet
unverändert (keine Tag-Anreicherung, keine Ordnerorganisation nach
Artist/Album) im rohen `/Transfer`-Ordner statt im echten, organisierten
Library-Pfad. Die `lib2_track_files`-Zeile zeigte danach zwar auf eine
reale Datei, aber die lag außerhalb der echten Musik-Bibliothek — für jeden
externen Medienserver (Plex/Jellyfin/Navidrome), der nur den echten
Library-Pfad scannt, existierte der Track schlicht nicht.

**Der Nutzer-Einwand war der richtige Hebel:** Automatic Search/Wishlist-
Downloads laufen bereits erfolgreich durch die volle Pipeline — der einzige
Unterschied bei Manual Grab ist, dass der Nutzer den Kandidaten selbst
auswählt statt der automatischen Suche. Ein erster geprüfter Fix
(„`is_simple_download` false setzen, wenn `lib2_entity` vorliegt") crashte
zunächst mit „Missing artist context" — aber nicht, weil die volle Pipeline
echte Provider-/Spotify-Metadaten braucht (`get_import_context_artist`
prüft nur `context["artist"]`, ein reiner Name genügt, siehe
`core/imports/context.py`), sondern weil der manuelle Grab bislang nur
`spotify_artist: None` setzte und nie das neutrale `artist`-Feld befüllte.

**Fix (`core/library2/grab_context.py`):**
- `resolve_lib2_grab_context` liefert jetzt zusätzlich `artist_name`,
  `album_name`, `track_title`, `track_number`, `disc_number` — per JOIN
  gegen `lib2_tracks`/`lib2_albums`/`lib2_artists` direkt aus der bereits
  aufgelösten Entität (Ground Truth, zuverlässiger als das ggf. veraltete
  Suchergebnis-Kärtchen im Browser).
- Neue Funktion `build_lib2_import_pipeline_fields(data, lib2_context,
  album_name=None)`: baut `{is_simple_download: False, artist: {name},
  album: {name}, track_info}` aus der Entität — leer (`{}`) ohne aufgelöste
  Entität (dann bleibt alles beim alten Simple-Download-Shortcut, unverändert
  für den „Search-Seite ohne Library-Ziel"-Fall).
- `web_server.py::start_download` (Album- und Single-Track-Zweig) nutzt das
  jetzt zum Aufbau von `matched_downloads_context[...]` statt hartkodiertem
  `is_simple_download: True` + `spotify_artist/album: None`.

Ergebnis: ein Manual Grab mit bekanntem `lib2_track_id` durchläuft jetzt
dieselbe volle Pipeline (echte Ordner-Platzierung, Tag-Schreibung,
Quarantäne-Gate, Autolink) wie jeder Automatic-Search-Download — nur der
Kandidat kommt vom Nutzer statt von der Suche. Ein Grab ohne Library-Ziel
bleibt exakt beim alten `/Transfer`-Verhalten.

**Bewusst nicht in diesem Slice:** Album-Grabs setzen `is_album_download`
nicht explizit (wie zuvor) — jeder Track im Album-Grab durchläuft die
normale Single-Track-Album-Erkennung (`detect_album_info_web`) statt
forcierter Album-Gruppierung; funktioniert, ist aber nicht so robust wie ein
echter Album-Bundle-Download. Kann bei Bedarf in einem eigenen Slice
nachgezogen werden.

**Verifikation:** `tests/library2/test_manual_grab_pipeline.py` (2 Tests:
voller Pipeline-Pfad mit Autolink in einen echten organisierten Pfad +
unveränderter Simple-Download-Shortcut ohne Entität) und
`tests/library2/test_grab_context.py` (4 neue/angepasste Tests für die
erweiterte Rückgabe + `build_lib2_import_pipeline_fields`). Gesamt
`tests/library2` + `tests/imports` + `tests/wishlist`: **1624 grün** (6
vorbestehende, unabhängige Failures in `tests/downloads/
test_cross_batch_dedup.py` per `git stash`-Vergleich bestätigt bereits vor
dieser Session vorhanden). `ruff check` sauber.

---

## 72. §64 I8 — Diskspace pro Artist/Album — ✅ umgesetzt (2026-07-18)

Erster der vier am 2026-07-17 angenommenen, aber unimplementierten I/H-Punkte
(§64: I2 Wanted-Views, I6 Queue-Sichtbarkeit, I8 Diskspace, H8
Bulk-Edit-Modal). Entscheidung: „Speicherplatzbelegung (Größe) pro Artist
und Album anzeigen, absoluter Pfad nicht nötig."

**Backend (`core/library2/queries.py`):** `total_size_bytes` in
`list_artists`, `get_artist` (pro Release + Artist-Summe) und `get_album`.
Zählt je Track exakt eine primäre Datei (ADR-03 `primary_order`, dieselbe
Auswahlregel wie `core/library2/track_files.py::primary_file_row`) —
bewusst NICHT einfach `SUM(lib2_track_files.size)` über den bestehenden,
bereits für die Presence-Zählung gefanten (`LEFT JOIN lib2_track_files`)
Join, da das historische/gelöschte Datei-Zeilen mitzählen und Werte
vervielfachen würde. `get_album` summiert stattdessen direkt aus den
bereits serialisierten Tracks (`track["file"]["size"]`), `list_artists`/
`get_artist` nutzen eine eigene, per `ROW_NUMBER() OVER (PARTITION BY
track_id ...)` auf Rang 1 begrenzte CTE, separat von der bestehenden
Track-Zähl-CTE gejoint.

**Frontend:** `total_size_bytes` in allen vier TS-Typen
(`LibraryV2ArtistSummary/Detail`, `LibraryV2AlbumSummary/Detail`);
Artist- und Album-Detail-Header zeigen die Größe als neues Detail-Label
(`formatFileSize`, bereits vorhandener Helper) neben Quality-Profile/
Monitored/Tracks. Neue optionale Spalte „Size" in der Artist-Tabelle,
default aus (wie alle B5/D6-Spalten dieser Tabelle) — `lib2_ui_preferences`
Default + `LibraryV2ArtistTableColumns`-Typ + Renderer + Options-Menü-Label
ergänzt.

**Bewusst nicht in diesem Slice:** kein Root-Folder/Pfad (laut Entscheidung
nicht gewünscht); keine Album-Tabellen-Spalte (nur Artist-Tabelle + beide
Detail-Header, wie im Feature-Wunsch beschrieben — „pro Artist und Album"
ist über die Detail-Header beider Entitäten abgedeckt).

**Verifikation:** `tests/library2/test_queries.py` (2 neue Tests: Basis-Summe
gegen die Seed-Daten + eine gezielte Regression, dass eine zusätzliche
gelöschte historische Datei-Zeile die Summe NICHT verändert, inkl.
Konsistenz zwischen `get_album`/`list_artists`/`get_artist`),
`tests/library2/test_ui_preferences.py` (1 Test angepasst). Gesamt
`tests/library2`: **777 grün**. Frontend `tsc --noEmit` clean, `vitest run
src/routes/library-v2/` 131 grün (23 Dateien), `npm run check` clean,
Production-Build ok. `ruff check` sauber.

**Noch offen aus §64:** I2 (Wanted-Views), I6 (Queue-Sichtbarkeit, siehe
§73), H8 (Bulk-Edit-Modal).

---

## 73. §64 I6 — Queue-Sichtbarkeit an der Entity — ✅ umgesetzt (2026-07-18)

Entscheidung nach Rückfragen mit dem Nutzer, zweiter der vier §64-Punkte.
Ziel: Status/Fortschritt laufender Downloads direkt an Track- und
Album-Zeilen in Library v2 sichtbar machen, nicht nur auf der separaten
Downloads-Seite.

### 73.1 Bestandsaufnahme

Zwei parallele, bereits vorhandene In-Flight-Tracking-Systeme
(`core/runtime_state.py`): `download_tasks`/`download_batches` (Batch-/
Wishlist-Pfad, Status-Werte `pending/queued/searching/downloading/
post_processing/completed/failed/cancelled/not_found`, Fortschritt via
`core/downloads/status.py`) und `matched_downloads_context` (manueller
Grab/Interactive Search, byte-genauer Fortschritt aus dem slskd/Streaming-
Transfer). Beide tragen die lib2-Track-Identität bereits mit sich —
`matched_downloads_context[...]['lib2_entity']['track_id']` (gesetzt via
§71/`resolve_lib2_grab_context`) bzw.
`download_tasks[...]['track_info']['source_info']['lib2_track_id']`
(gesetzt via `core/library2/wishlist_mirror.py`) — aber keine der
bestehenden Status-APIs (`/api/downloads/status`, `/api/downloads/all`)
surfaced das heute.

### 73.2 Entschiedenes Design

- **Scope:** Track-Zeilen in der Album-Detailansicht bekommen ein Status-
  Badge; die Album-Liste in der Artist-Detailansicht bekommt ein kleines
  Roll-up ("2 downloading") neben der bestehenden Presence-Progress-Bar.
  Beide Download-Pfade (Batch/Queue UND manueller Grab) zählen.
- **Lebensdauer:** rein aktiv — nur sichtbar während
  `pending/queued/searching/downloading/post_processing`. Kein neuer
  persistenter Fehlzustand; `failed`/`not_found` verschwindet mit dem
  bestehenden Task-Purge (5 min) wieder, die vorhandenen Quality-/
  Verification-Badges decken das Ergebnis bereits ab.
- **Backend:** neuer read-only Helper `core/library2/queue_status.py::
  get_queue_status(track_ids)` scannt beide In-Memory-Strukturen gefiltert
  auf die übergebenen lib2-Track-IDs und mappt auf
  `{track_id: {status, progress_pct}}` — terminale/leere Einträge werden
  weggelassen statt kodiert. Eine gescopte Route, generisch über den Entity-
  Typ, exakt nach dem Muster des bestehenden §29/C1 Scoped-Search-Endpunkts:
  `GET /api/library/v2/<entity>/<id>/queue-status` (`entity` ∈
  `artists/albums/tracks`, Track-IDs im Scope via die bereits vorhandene
  `core.library2.wanted.entity_track_ids`).
- **Frontend:** kein neuer Toggle-Spalten-Eintrag (wie bei I8) — das Badge
  ist die meiste Zeit leer, daher inline neben dem Tracktitel in `TrackRow`
  bzw. als Pill neben der Progress-Bar in `AlbumBlock`, jeweils nur
  gerendert wenn ein Eintrag existiert. Polling: einfacher
  `refetchInterval: 3000` in der jeweils gemounteten Detailansicht (Artist-
  /Album-Detail), kein bedingtes An/Aus nötig, da der Endpoint durch das
  Scoping bereits günstig ist; ein Fetch-Fehler wird still geschluckt (das
  Badge ist ein Overlay, darf die Seite nie blockieren). Beide Download-
  Pfade normalisieren serverseitig auf dieselben fünf Status-Strings, das
  Frontend braucht nur eine gemeinsame Icon/Label-Zuordnung (Vokabular
  angelehnt an die bestehende Downloads-Seite, `pages-extra.js
  _adlStatusLabel`: "Queued", "Searching", "⏬ Downloading N%",
  "Processing").
- **Bewusst abgelehnt:** `/api/downloads/all` wiederverwenden und
  clientseitig filtern (verworfen — Payload wächst mit der Gesamtzahl aller
  Batches, nicht mit der sichtbaren Entity) und WebSocket/SSE-Push
  (verworfen — kein bestehendes Push-Infra in dieser Codebase, unnötige
  Komplexität für ein Nice-to-have-Overlay).

### 73.3 Umsetzungsdetail: Album-Roll-up ohne N+1-Polling

Beim Bau fiel auf, dass ein separates Artist-weites Roll-up (ein Query pro
Artist-Seite) für `AlbumBlock` nicht ausreicht, weil `AlbumBlock` nur die
Album-Summary kennt (keine Track-ID-Liste) — eine Filterung "welche Tracks
gehören zu diesem Album" wäre ohne Zusatzdaten nicht möglich gewesen. Beide
In-Memory-Quellen tragen aber ihre `lib2_album_id` bereits neben der
Track-ID mit (`source_info`/`lib2_entity`), also liefert `get_queue_status`
beide Ebenen aus demselben einen Scan zurück: `{"tracks": {track_id: {...}},
"albums": {album_id: aktive_Track_Anzahl}}`. `AlbumTrackTable` liest nur
`tracks` (Album-Detail-Scope deckt exakt seine eigenen Tracks ab);
`AlbumBlock` pollt seinerseits denselben Endpoint scoped auf sein eigenes
`album.id` (kein Artist-weiter Extra-Request) und liest nur `albums[album.id]`
darüber — bleibt pro sichtbarem Album ein einzelner leichter Request, kein
Artist-weiter Fan-out.

### 73.4 Verifikation

`tests/library2/test_queue_status.py` — 25 neue Tests: Status-Bucket-Mapping
für alle vier aktiven Zustände, alle sechs terminalen Zustände korrekt
weggelassen, Live-Fortschritt aus dem gecachten Transfer-Lookup, Album-Roll-up
über mehrere aktive Tracks, ein Batch-Task gewinnt gegenüber einem
korrelierten Shadow-Manual-Grab-Kontext für denselben Track (kein
Doppelzählen im Roll-up), sowie Flask-Endpoint-Tests (unbekannte Entity →
400, unbekannte ID → 404, Artist- und Album-Scope, fehlende injizierte Deps
degradiert auf leere Maps statt Fehler). Gesamt `tests/library2`: **802
grün** (777 + 25). Frontend: `album-track-table.test.tsx` um zwei Tests
erweitert (Badge erscheint mit korrektem Label/Prozent, verschwindet ohne
In-Flight-Eintrag) — `vitest run src/routes/library-v2/` **133 grün** (23
Dateien, war 131). `tsc --noEmit` clean, `npm run check` (oxfmt+oxlint)
clean, Production-Build ok. Backend gesamt `tests/library2` + `tests/imports`
+ `tests/wishlist` + `tests/downloads`: 2290 grün (6 vorbestehende,
unabhängige Failures in `tests/downloads/test_cross_batch_dedup.py`, siehe
§71). `ruff check` sauber.

---

## 74. §64 I2 — globale Wanted Views (Missing / Cutoff Unmet) — ✅ umgesetzt (2026-07-18)

Vierter der vier §64-Punkte (nach I8, I6, H8). Ziel: bibliotheksweite
Lidarr-Style-Übersichten über alle gewünschten Tracks — „Missing" (kein
File) und „Cutoff Unmet" (File vorhanden, aber unter dem Qualitätsprofil-
Ziel) — statt nur pro Artist/Album einsehbar.

### 74.1 Rückfrage vor Umsetzung

Nach [[feedback-ask-before-library-parity-work]] wurde vor Beginn erst per
Rückfrage geklärt, welcher der beiden verbliebenen §64-Punkte (I2/H8)
zuerst drankommt. Dabei stellte sich heraus, dass H8 bereits durch die
`BulkEditTracksModal` aus §34 (Commit `c03150bf`, 2026-07-16 — vor der
§64-Entscheidung) erledigt war: die §64-Beschreibung nannte „Genre, Jahr"
als Beispielfelder, aber Legacys echtes `showBulkEditModal` deckte nur
Track Number/BPM/Style/Mood/Explicit ab — exakt das Feldset, das die
§34-Modal (minus des sinnfreien Bulk-Track-Number-Felds) bereits abdeckt.
§64 wurde entsprechend korrigiert (kein neuer Code für H8). Für I2 wurde
eine neue Top-Level-Seite (statt eines Filters innerhalb der bestehenden
Library-Ansicht) gewählt.

### 74.2 Backend: `core/library2/wanted_views.py`

Liest ausschließlich die bereits materialisierte Wanted-Projektion
(`lib2_wanted_tracks`, §11.2/ADR-02) statt Monitor-Regeln neu aufzulösen,
und wertet Dateien exakt wie `get_album()` über `core.library2.quality_eval`
aus — Badges und diese Übersicht können dadurch nie auseinanderlaufen.

- **`list_missing`:** reines SQL — `wanted=1 AND NOT EXISTS(aktives File)`,
  mit Pagination direkt in der Query. Schließt konsolidierte Duplikate aus
  (derselbe `_NOT_CONSOLIDATED_SQL`-Gedanke wie beim Bulk-Remonitor in
  `api/library_v2.py`, bewusst in den Core-Layer dupliziert statt über die
  Schichtgrenze importiert — Präzedenzfall: `_config_fingerprint` in
  `quality_upgrade.py`/`quality_upgrade_scanner.py`) — sonst würde ein
  bewusst entfernter Duplikat-Single erneut zum Redownload vorgeschlagen.
- **`list_cutoff_unmet`:** „ist ein File ein Upgrade-Kandidat" lässt sich
  nicht in SQL ausdrücken (hängt vom aufgelösten Qualitätsprofil samt
  Ranked Targets ab, bereits als `effective_profile_id` auf
  `lib2_wanted_tracks` materialisiert). Holt daher alle
  `wanted=1 AND File vorhanden`-Kandidaten, wertet sie in Python aus
  (gruppiert nach der kleinen Menge tatsächlich genutzter Profile, nicht
  pro Track) und paginiert erst danach über das gefilterte Ergebnis. Ein
  nicht auflösbares Profil/Format liefert laut `quality_eval`s Tri-State-
  Vertrag nie einen falsch-positiven Fund.

### 74.3 API + Frontend

- `GET /api/library/v2/wanted?kind=missing|cutoff_unmet&search=&page=&limit=`
  — selbe Pagination-Form wie `/api/library/v2/artists`.
- Neue Sektion `wanted` im bestehenden `section`-Suchparameter-Muster
  (`docs`: „ein Route, der aktuelle View kommt aus Search-Params" — wie
  schon bei `playlists`), dritter Tab in `LibrarySectionTabs`. Zwei
  Unter-Tabs (Missing/Cutoff Unmet) über einen neuen `wantedKind`-Param.
  Jede Zeile verlinkt Artist/Album zurück in die normale Detail-Navigation;
  der Search-Button pro Zeile ist der bereits bestehende gescopte
  Automatic-Search-Endpunkt (§29/C1, `tracks`-Scope) — kein neuer
  Suchmechanismus.

### 74.4 Verifikation

`tests/library2/test_wanted_views.py` — 17 neue Tests: Missing/Cutoff-
Unmet-Query-Logik (Duplikat-Ausschluss, `missing_confirmed`-File-State,
Pagination, Suche, `until_cutoff`-Policy mit Cutoff-Index, Tri-State-
Sicherheit bei unauflösbarem Profil) sowie Flask-Endpoint-Tests. Gesamt
`tests/library2`: **819 grün** (802 + 17). Frontend: neue
`fetchLibraryV2Wanted`-Tests in `-library-v2.api.test.ts` sowie
`wanted-quality.test.ts` für die reine Formatierungsfunktion (gleiches
Muster wie `progress-clamp.test.ts`) — `vitest run src/routes/library-v2/`
**140 grün** (24 Dateien, war 133). `tsc --noEmit` clean, `npm run check`
(oxfmt+oxlint) clean, Production-Build ok, `ruff check` sauber. Live-
Browser-Verifikation war in dieser Session nicht möglich: `dev.py` crash-
loopte lokal unabhängig von dieser Änderung (letzter sauberer Lauf lag vor
Sessionbeginn, vermutlich fehlendes `gunicorn` im venv) — Vite HMR hat die
neue Datei jedoch fehlerfrei übernommen (`"new export"`-Invalidate ohne
Fehlermeldung), ein zusätzliches, wenn auch schwächeres Signal.

---

## 75. Bug-Tracker LV2-006/009/010/013 geschlossen — persistente Reconciliation und Integritätsreport (2026-07-18)

Der ergänzende Tracker `docs/library-v2-bug-tracker-2026-07-18.md` wurde
vollständig gegen die konsolidierten Invarianten dieses Dokuments umgesetzt.
Die zuvor offenen Codepunkte sind geschlossen; operative Live-Daten- und
externe End-to-End-Läufe bleiben bewusst vom Code-Fix getrennt.

### 75.1 LV2-006 — evidenzbasierte persistente Grab-Reconciliation

`core/acquisition/reconciler.py` verbindet offene request-gebundene Legacy-
Grabs mit realer Evidenz aus Runtime-Tasks, `matched_downloads_context`,
externem Client, Quarantäne-Sidecars, nativen Acquisition-Imports und finalen
Dateipfaden. Eine positive Completion braucht ein reales und in Legacy oder
Library v2 aktiv indexiertes File; Path-Mapping läuft über den gemeinsamen
Resolver. Ein Completed Acquisition-Import ist nur dann Abschluss-Evidenz,
wenn seine `processed.final_path`-Einträge ebenfalls real und indexiert sind.

Eindeutige Success-/Fail-/Cancel-Zustände werden idempotent und pro Grab in
einem Savepoint übernommen. Reine Abwesenheit wird erst nach konfigurierbarer
TTL (Default 24 Stunden, Minimum eine Stunde) zum Runtime-Failure und erzeugt
keinen Blocklist-Eintrag. Der bestehende Acquisition-Monitor führt den Lauf
beim Start und periodisch aus. Admin-Schnittstellen:

- `GET /api/library/v2/maintenance/reconcile-acquisition`: immer Dry-Run,
- `POST ... {"apply": true}`: sichere Transitionen anwenden.

Beide Antworten enthalten Counts und Begründungen pro Entscheidung.

### 75.2 LV2-009 — crash-sicheres Recover to Staging

`recovered_to_staging` ist jetzt ein eigener Acquisition-Importstatus und
Append-only-History-Event. `acquisition_quarantine_recoveries` persistiert
Move-Plan und Request-/Candidate-/Grab-/Import-Korrelation vor dem
Dateisystemzugriff. Erst nach Move und Lifecycle-Commit wird das Sidecar
entfernt; ein Crash an jeder Grenze ist wiederaufnehmbar. Der spätere manuelle
Staging-Import erhält die persistierten Marker zurück und schließt über die
Shared Pipeline ab. Solange ein transient fehlgeschlagener Reimport das
Staging-File nicht entfernt hat, bleibt er erneut versuchbar.

### 75.3 LV2-010 — erster physischer Miss ist sichtbar

`file_status()` gibt `missing_suspected` statt `present` zurück. Die Track-
Tabelle zeigt `checking missing` in Amber mit Erklärung der zweiten
Bestätigungsprüfung. Der Zustand bleibt bewusst vom Wanted-/Redownload-Pfad
ausgeschlossen; erst `missing_confirmed` wird fachlich `missing`.

### 75.4 LV2-013 — zentraler Read-only-Integritätsreport

`core/library2/integrity_reconciler.py` vergleicht alle verfügbaren
Integritätsindizes: Disk und Path-Mapping, Legacy-/Media-Server-Projektion,
`lib2_track_files`, `track_downloads`, `library_history`, Runtime-Tasks und
Matched Contexts, Acquisition-Request/Grab/Import, Recovery-Journal,
Quarantäne-File/Sidecar-Paare und externe Client-Snapshots. Findings sind
reason-coded und nach Severity/Komponente/Entität zählbar. Der
Storage-Health-Gate verhindert falsche Missing-Befunde bei ungemounteten
Roots; der Report mutiert weder DB noch Files.

Admin-Route:

- `GET /api/library/v2/maintenance/integrity-report?max_findings=1000`

Das Detail-Limit begrenzt nur die Payload, nicht die vollständigen Counts.

### 75.5 Verifikation und Betriebsgrenze

Backend-Breitsuite (`tests/library2`, `tests/wishlist`, `tests/imports`,
`tests/acquisition`, Orphan Detector): **1970 passed, 3 skipped**. Die 31
Warnungen sind bestehende Python-3.12-Deprecation-Warnungen. Library-v2-
Frontend: **141 passed** in 24 Vitest-Dateien; Production-Build erfolgreich;
Formatter und Type-Lint für die berührten Dateien sauber. `py_compile` für
die geänderten Python-Grenzen inklusive `web_server.py` war sauber.

Die produktive Provider-ID-Datenreparatur aus LV2-012 wurde weiterhin nicht
blind gegen `database/music_library.db` ausgeführt: dafür bleiben Dry-Run-
Paarliste und Backup verbindliche Betriebsbedingungen. Echte Soulseek- und
Plex/Jellyfin/Navidrome-End-to-End-Läufe sind Deployment-Abnahme, kein noch
offener Codefix.

---

## 76. Repair-/Maintenance-Tools — vollständiger V2-Audit und erste Umsetzung (2026-07-18)

Alle 33 im Repair-Worker registrierten Tools wurden gegen Datenbasis,
Findings, Fix-Handler, automatische Mutationen, Datei-/Wanted-/Artwork-Folgen,
History und Feature-Gating geprüft. Die vollständige Matrix,
Konsolidierungsentscheidungen und der spätere Legacy-Löschplan stehen in
[`library-v2-tool-integration-audit-2026-07-18.md`](library-v2-tool-integration-audit-2026-07-18.md).

Das erste Prioritätspaket ist implementiert: ein exhaustives Datenbasis- und
V2-Effekt-Manifest, stabile V2-Subjects an Repair-Findings, eine strikt durch
`features.library_v2 is True` gegatete zentrale Mutation-Synchronisation,
gezielte File-Rescans, Artwork-Invalidation, Delete/New-File/Wanted-Folgen und
Entity-History. Live-Mutationen sowie die Reorganize-Queue melden Änderungen
über dieselbe Grenze. Vorhandene V2-Tabellen allein aktivieren keinen Pfad.
Die Repair-UI verwendet bereits neutrale Namen und Basis-Badges (`Library`,
`Library + files`, `Files`); nur stabile interne Übergangs-IDs tragen noch den
`lib2_`-Prefix.

Wichtig: „sicher synchronisiert“ ist nicht dasselbe wie „Scanner bereits
nativ vollständig“. Mehrere ältere Scanner lesen weiterhin den Legacy-
Katalog und erfassen darum V2-only Subjects ohne Legacy-Backref noch nicht.
Diese P1-Migration ist im Audit pro Tool sichtbar. Der gemeinsame, strikt
gegatete V2-File-Subject-Enumerator ist bereits gebaut; ReplayGain und Lyrics
erfassen damit auch V2-only/derivative Files. Als Nächstes folgen Cover Art
und AcoustID mit ihren zusätzlichen Album-/Verification-Schreibgrenzen.

Verifikation dieses Pakets: **852** Library-v2-Backendtests, **309** breite
Repair-/Repair-Job-Tests und **141** Library-v2-Frontendtests grün; Ruff,
Formatter/Type-Lint der berührten Frontend-Dateien und Vite Production-Build
sauber. Der globale Frontend-Check trifft weiterhin eine vorbestehende
Formatabweichung in der unberührten Datei `track-feature-badges.test.tsx`.

---

## 77. P1 komplett + P2-Konsolidierung der Quality-/Discography-Familie (2026-07-18)

Die im Audit ([`library-v2-tool-integration-audit-2026-07-18.md`](library-v2-tool-integration-audit-2026-07-18.md))
definierte P1-Migration ist vollständig: Der gemeinsame V2-Subject-Enumerator
liefert jetzt vollen Track-/Album-/Provider-Kontext, dazu ein Album-Pendant
für Release-orientierte Tools. Nativ V2-fähig (V2-only Subjects ohne
Legacy-Backref werden erfasst und gefixt) sind damit: ReplayGain, Lyrics,
AcoustID (Verification nativ auf `lib2_track_files`), Cover Art (nativer Fix
auf `lib2_albums`/`lib2_artists`), Corruption, Preview Clip, Lossy Converter,
Fake Lossless, Metadata Gap (nativer Fix auf `lib2_tracks`), Album Tag
Consistency und Track Number Repair (V2-Ordner außerhalb des
Transfer-Walks). Delete+Rewishlist-Fixes laden ihren Redownload-Payload für
`lib2:`-Subjects nativ; File-State/Wanted übernimmt weiterhin die zentrale
Brücke aus §76.

P2 (Beschluss endgültig, keine Rücksicht auf die alte Library): Die
Quality-Familie ist konsolidiert — `lib2_upgrade_scan` („Quality Upgrade
Scan (monitored)") ist der einzige Evaluator mit den Modi `automatic`
(direkte Upgrade-Queue) und `review` (`quality_below_cutoff`-Findings mit
Queue-Fix pro Track). Entfernt wurden `quality_upgrade_scanner`,
`quality_upgrade`, `discography_backfill` (nativ ersetzt durch Discography
Refresh + Wanted-Views) und das nur noch von ihnen genutzte
`core/discovery/quality_scanner.py`. Entfernte Job-IDs stehen in
`RETIRED_JOB_IDS`; der Repair-Worker räumt deren pendente Findings beim
Start ab. Die Automation-Aktion `start_quality_scan` triggert den nativen
Scan. Die Cutoff-Parität bleibt durch einen eingefrorenen Orakel-Test in
`tests/acquisition/test_legacy_parity_contract.py` belegt.

Noch offene P2-Ablösungen (Brücke bleibt sicher): Single/Album Dedup,
Album Completeness, Canonical/MBID-Reconcile, Library Re-tag, Unknown
Artist, Live/Commentary, Duplicate Detector. P3 (Legacy-Removal nach
Löschplan §7 des Audits) folgt in einer späteren Session.
