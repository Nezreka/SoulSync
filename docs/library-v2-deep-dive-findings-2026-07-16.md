# Library V2 — Deep-Dive-Findings (2026-07-16)

**Methode:** Vollständige Lektüre von `docs/library-v2.md` (§1–§27) +
`docs/library-v2-ui-requirements.md`, Code-Analyse von
`webui/src/routes/library-v2/` (Page, Modals, API-Layer, Types),
`api/library_v2.py`, `core/library2/` (artwork, retag, match_status,
source_info, queries, schema), `core/acquisition/history.py`, Legacy-Vergleich
gegen `webui/static/library.js` (Enhanced View) und Lidarr-Recherche
(Servarr-Wiki + bekannte Lidarr-UI-Semantik).

**Scope dieses Dokuments:** NUR Punkte, die noch **nicht** in `library-v2.md`
oder `library-v2-ui-requirements.md` getrackt sind (bereits Getracktes wird am
Ende nur referenziert, Abschnitt F). Direkte Fehler in `library-v2.md`
(veraltete Status-Marker) wurden dort korrigiert.

**Aufbau:** A = Bugs/Denkfehler Runde 1 (nutzer-getriggert verifiziert +
vertieft) · B = UI/UX-Vorschläge · C = Backend-Designs · D = Kleinfunde ·
E = Priorisierung · **G = Code-Audit Runde 2 (neu gefundene Bugs, Zeile für
Zeile)** · **H = vollständiger Legacy-Gap** · **I = vollständiger
Lidarr-Gap** · F = Querverweise auf bereits Getracktes.

**⚠️ Vorgehen bei Library-Parität (Abschnitte H + I):** Das sind reine
Feature-Gap-Enumerationen, keine fertig durchdachten Specs — bevor an einem
H-/I-Punkt gearbeitet wird, erst beim Nutzer nachfragen, was genau und wie
umgesetzt werden soll (Scope, UI-Ansatz, Priorität innerhalb des Punkts).
Nicht vorab schon klären — erst wenn wir tatsächlich an diesem Punkt
angekommen sind und ihn umsetzen wollen.

---

## A. Echte Bugs / Denkfehler (neu gefunden)

### A1. Gewähltes Cover erreicht die Audio-Dateien NIE (Embed-Lücke, zweischichtig) — KRITISCHSTER FUND — ✅ behoben (siehe library-v2.md §28)

**Beobachtung (Nutzer):** Cover-Picker anwenden → Cover in der UI/DB neu, aber
in den vorhandenen Tracks bleibt das alte Cover embedded.

**Root-Cause-Analyse — es sind ZWEI unabhängige Lücken:**

1. **Kein Trigger:** `apply_manual_artwork()` (`core/library2/artwork.py:402`)
   setzt das `image_url`-Override und schreibt den Artwork-Cache — löst aber
   keinerlei Tag-Write für die Dateien des Albums aus. Der Embed-Pfad existiert
   (`core/library2/retag.py::write_tags` embedded das Cover aus dem
   lib2-Artwork-Cache), wird nur nie aufgerufen.
2. **Selbst ein manueller Retag würde es NICHT heilen:** `write_tags`
   (`retag.py:232–237`) skippt jede Datei, deren Text-Tag-Diff leer ist —
   `build_tag_diff` (`core/tag_writer.py:238`) vergleicht **nur Textfelder**
   (title/artist/album/year/genre/track#/disc#/bpm), nie das Cover. Ein Album
   mit korrekten Tags aber altem Cover wird also immer als „unchanged"
   übersprungen → das neue Cover kann auf keinem existierenden Weg in die
   Dateien gelangen.

**Fix-Richtung (Reuse-First):**
- `write_tags` bekommt einen Parameter `force_cover=True` (oder `force=True`),
  der den Unchanged-Fastpath überspringt (bzw. nur die Cover-Embed-Prüfung
  erzwingt).
- `POST /albums/<id>/art` triggert nach erfolgreichem Apply einen
  Background-Tag-Write für alle file-tragenden Tracks des Albums mit diesem
  Flag (gleicher Job-Registry-Pfad wie `/tags/write`). UI: kleiner Hinweis im
  Picker („Cover wird in N Dateien geschrieben…" mit Fortschritt) oder
  zumindest eine Checkbox „Embed into files" (default an).
- Optional sauberer: `build_tag_diff` um einen Cover-Vergleich erweitern
  (Hash der embedded Art vs. Hash der Cache-Datei) — dann bleibt der Fastpath
  ehrlich. Teurer, aber macht auch „Preview Retag" cover-aware.

### A2. Neues Cover wird im Browser bis zu 7 Tage NICHT angezeigt (immutable Cache auf mutable URL) — ✅ behoben (siehe library-v2.md §28)

`_send_art` (`api/library_v2.py:1588–1591`) sendet
`Cache-Control: public, max-age=604800, immutable`, aber die Bild-URL ist stabil
(`/api/library/v2/artwork/album/<id>`, `_artwork_url` `api/library_v2.py:80`).
Nach einem Cover-Pick invalidiert das Frontend zwar React-Query
(`art-picker-modal.tsx:39`), der Browser bedient das `<img>` aber weiter aus dem
HTTP-Cache — `immutable` unterdrückt sogar die Revalidierung. Ergebnis: altes
Cover bleibt in Zeilen-Thumb + Detail-Ansicht sichtbar (bis Hard-Reload).
Legacy löste das mit Cache-Bustern.

**Fix-Richtung:** Versionsparameter in die URL aufnehmen —
`_artwork_url` um `?v=<mtime der Cachedatei>` ergänzen (oder ein
`artwork_version`-Feld im Payload, das die UI anhängt). `apply_manual_artwork`
ändert die mtime ohnehin → URL ändert sich automatisch, `immutable` bleibt
korrekt.

### A3. „Automatic Search" auf Album-Zeile ist in Wahrheit die GLOBALE Wishlist-Verarbeitung — ✅ behoben (siehe library-v2.md §29, C1)

Der Album-Button (`library-v2-page.tsx:3409–3413`) feuert
`Automatic Search: <Albumtitel>` → `AUTOMATIC_SEARCH_RE` (`:137`) matcht →
`searchMonitored()` → `POST /api/wishlist/process` (global). Der Albumtitel im
Action-String wird **komplett ignoriert**. Der Tooltip („find & grab the best
source (global Wishlist action)") ist in sich widersprüchlich: er verspricht
eine Album-Aktion und liefert eine globale. P2-08 hat die Artist-Toolbar
ehrlich beschriftet, aber die Album-Zeile suggeriert durch ihre Position
weiterhin Album-Scope.

**Zusatz-Inkonsistenz:** In der deep-linkbaren `AlbumDetailView`
(`:2556–2561`) behandelt `handleAction` `AUTOMATIC_SEARCH_RE` gar nicht — ein
dort gefeuertes „Automatic Search:" wäre ein stilles No-Op. Aktuell nicht
auslösbar (die View hat den Button nicht), aber die Handler-Asymmetrie ist eine
wartende Falle.

**Fix-Richtung:** siehe C1 (scoped Automatic Search); bis dahin den Button aus
der Album-Zeile entfernen (Interactive Search reicht dort) statt ihn global
wirken zu lassen.

### A4. Track-Level „Automatic Search" = clientseitige Best-Pick-Heuristik (zweite Decision-Engine im Frontend) — ✅ behoben (siehe library-v2.md §29, C1)

`autoGrabBest` (`-library-v2.api.ts:1194–1214`) implementiert die
Source-Auswahl **im Client**: eigenes Scoring (lossless > quality_score >
upload slots), dann direkter `/api/download`. Das verletzt die eigene
Kernregel „keine zweite Decision-Engine / Reuse-First" (§1, §5.3) auf der
Frontend-Seite:
- Quality-Profile der Entity fließt in die **Auswahl** nicht ein (nur später in
  den Import-Check) — Lidarrs Automatic Search wählt profile-getrieben.
- Kein Candidate-Walk/Retry bei Fehlschlag (die Wishlist-Pipeline hat genau
  das), keine Blacklist-/exhausted-sources-Beachtung bei der Auswahl.
- Die Scoring-Logik existiert damit doppelt (Server-Kandidaten-Ranking vs.
  diese drei Zeilen TS) und wird zwangsläufig divergieren.

**Fix-Richtung:** Track-scoped Automatic Search serverseitig (siehe C1):
Track ist bei Monitoring ohnehin wishlist-gemirrort — ein
`POST /api/wishlist/process` mit `track`-Scope (oder ein dünner
`/api/library/v2/tracks/<id>/search`-Endpoint, der den bestehenden
Candidate-Walk für genau diesen Wishlist-Eintrag anstößt) ersetzt die
Client-Heuristik vollständig.

### A5. BPM existiert im Schema, erreicht aber weder API noch UI — ✅ behoben (siehe library-v2.md §29)

`lib2_tracks.bpm` ist migriert (`schema.py:149,377`) und wird vom Importer
befüllt (§17.7-Fixes), aber `queries.py` projiziert es in keinem
Track-Payload, `LibraryV2Track` hat kein Feld, die UI keine Spalte. Legacy
zeigt BPM als sortier- und editierbare Spalte (`library.js:4795,4333`).
Analog: `duration` IST im Payload (`queries.py:700`, Type `:231`), wird aber
nirgends angezeigt. Legacy zeigt Duration als Spalte.

### A6. History-Modal liest nur `track_downloads`, obwohl Lidarr-Parity-Daten längst journaliert werden — ✅ behoben (siehe library-v2.md §35)

`GET /artists/<id>/history` (`api/library_v2.py:2844`) = nur
Download-Provenance per Artist-**Namens**-Match. Dabei existieren bereits vier
reichere, ungelesene Quellen:
- **`acquisition_history`** (`core/acquisition/history.py:15–42`): 26
  Event-Typen — `grab_submitted`, `grab_failed`, `retry_started`, `cancelled`,
  `import_file_quarantined`, `import_needs_review`,
  `import_resolved_manually`, `import_completed`, `import_failed`,
  `candidate_blocklisted`, `force_quarantine_auto_approved`, … Das ist eine
  **Obermenge** der Lidarr-Event-Typen (Grabbed / Download Failed / Track
  Imported / Import Incomplete / Deleted / Renamed / Retagged / Ignored).
  Requests tragen `scope` + `entity_id` (lib2!) → per Artist/Album/Track
  joinbar.
- **`lib2_entity_history`**: Canonical-Link/Unlink, File-Moves.
- **`lib2_file_delete_operations`/`_items`**: physische Löschungen (ADR-05).
- **`lib2_manual_skips`**: manuelle Check-Overrides.

**Fix-Richtung:** History-Endpoint zu einem Event-Union erweitern (Typ-Spalte +
Farb-Chips wie Lidarr: grabbed=gelb, imported=grün, deleted=rot,
quarantined=orange, retagged/moved=blau), Filter nach Event-Typ. Kein neues
Journal nötig — reine Read/JOIN + UI-Arbeit.

### A7. Track-Info-Tab: Pipeline-Lifecycle bleibt unsichtbar (Nutzer-Wunsch: „wie ging es durch die Pipeline?") — ✅ teilweise behoben (siehe library-v2.md §37)

Der Info-Tab zeigt Verification-Badge + `lib2_manual_skips` + Download-Historie
(§18.3, `TrackLifecycleSection`, `library-v2-page.tsx:4450`). Was fehlt, obwohl
teilweise persistiert:
- **Quality-Gate-Ergebnis** beim Import (bestanden? mit welchem Profil? Fallback
  gegriffen?) — für acquisition-korrelierte Grabs in `acquisition_history`
  (`candidates_evaluated`, Gate-Runs mit `forced=0`) vorhanden, für den reinen
  Legacy-Pfad nicht pro File persistiert. → Lücke benennen: der eine
  Import-Callback sollte das Gate-/Quality-Resultat auf die
  `lib2_track_files`-Zeile (oder ein kleines Journal) schreiben.
- **AcoustID-Detail** (welcher Fehler genau, Score) — heute nur der Endstatus
  (`verification_status`); der konkrete Fehlgrund geht verloren.
- **Quarantäne-Geschichte** („war in Quarantäne, manuell importiert am …") —
  als `import_file_quarantined`/`import_resolved_manually` journaliert, aber
  nur über `/acquisition/requests/<id>/history` erreichbar, nie per Track.

**Fix-Richtung:** Info-Tab um eine chronologische „Lifecycle"-Timeline
erweitern, die `acquisition_history` (via `download_id`/`entity_id`-Korrelation),
`lib2_entity_history`, File-Delete-Journal und `manual_skips` merged. Gleiche
Datenbasis wie A6, nur Track-gefiltert.

**Update 2026-07-16:** Quality-Gate-Fallback (Downsample/Lossy-Copy) und
AcoustID-Detail (Klartext-Grund) sind jetzt pro File persistiert und in der
Lifecycle-Sektion sichtbar — der eine Import-Callback (`autolink.py`) schreibt
sie, siehe library-v2.md §37. Bewusst NICHT Teil davon: die
„Quarantäne-Geschichte"-Timeline (dritter Punkt oben) — der bereits
existierende History-Feed (§35/A6) liefert dieselbe Korrelation bereits
scope-generisch; eine zweite, Track-Info-Tab-eigene Verlinkung wäre Redundanz
ohne neuen Nutzen. Kein AcoustID-Score, da im Pipeline-Kontext keiner als Zahl
existiert, nur die Klartext-Message.

### A8. Match-Chips zeigen alle 13 Provider — auch nie konfigurierte (ewig graues Tidal) — ✅ behoben (siehe library-v2.md §29)

`SERVICES` (`core/library2/match_status.py:27–54`) ist statisch; weder Backend
noch UI filtern nach konfigurierten/aktiven Providern. Ein Nutzer ohne
Tidal/Qobuz/JioSaavn sieht dauerhaft tote graue Chips in jeder Track-Zeile —
reines Rauschen (Nutzer-Beschwerde). Vorbild im eigenen Code:
`gather_album_art_candidates` filtert mit `is_art_source_available(s)`
(`core/metadata/art_lookup.py:483`) auf tatsächlich verfügbare Quellen.

**Fix-Richtung (zweistufig):**
1. Server: Match-Status-Endpoints um Verfügbarkeits-Flag ergänzen (Provider
   konfiguriert/enabled?) — analoge Availability-Prüfung wie die
   Enrichment-Worker/`is_art_source_available`.
2. User-Einstellung darüber (siehe B5): Default = nur konfigurierte Provider,
   opt-in „alle zeigen".

### A9. Kein Artist-Image-Picker (Override-Feld existiert bereits) — ✅ behoben (siehe library-v2.md §29)

Der §49-Picker gibt es nur für Alben. Das `image_url`-Override ist für
`artist` im Whitelist bereits freigeschaltet und `build_artwork` liest es für
beide Kinds — es fehlt nur `GET /artists/<id>/art-options` (Artist-Fotos via
bestehende Artist-Image-Engine) + derselbe Modal. Geringer Aufwand, rundet §49
ab. (Betrifft auch die in §11.2/P2-04 bewusst offene Frage
„Provider-Foto vs. Embedded-Cover als Artist-Bild" — der Picker wäre die
Nutzer-Antwort darauf.)

**Update 2026-07-17:** Die spätere Nutzerentscheidung aus §52.5 ist ebenfalls
umgesetzt (§53): manuelles Override zuerst, dann Provider-Artist-Foto, danach
Embedded-Albumcover als Fallback. Der Picker und Cachevertrag bleiben dabei
unverändert.

---

## B. UI/UX — Überladung reduzieren, Lidarr-Alignment

**Lidarr-Referenz (recherchiert):** Artist-Seite hat ~6 Toolbar-Aktionen
(Refresh & Scan, Search Monitored, Preview Rename, Preview Retag, Monitor
Toggle, Edit/Delete); Album-Zeilen auf der Artist-Seite haben **2–3 Icons**
(Automatic Search, Interactive Search, ggf. „…"); Datei-Verwaltung
(Manage Track Files), History und Details liegen auf der **Album-Detailseite**
(Tabs), nicht als Icon-Batterie an jeder Zeile. Tabellen haben einen
„Options"-Zahnrad für Spalten-Konfiguration.

### B1. Album-Zeile: 10 Icon-Buttons → Ziel 3–4 + Overflow-Menü — ✅ behoben (siehe library-v2.md §30)

Aktuell (`library-v2-page.tsx:3401–3459`): Open Detail, Automatic Search,
Interactive Search, Preview Retag, ReplayGain, Reorganize, Change Cover,
Enrich, Album Details, Delete. Vorschlag:

| Bleibt sichtbar | Wandert |
|---|---|
| Automatic Search (nach C1 wirklich album-scoped) | **Open Album Detail entfernen** → Albumtitel wird Link zur Detail-Ansicht (Lidarr-Muster); Chevron/Kopfzeilen-Klick bleibt Inline-Expand |
| Interactive Search | ReplayGain → Features-Badge (B3) |
| „…"-Overflow-Menü | Preview Retag, Reorganize, Change Cover, Enrich, Delete → ins Overflow-Menü |
| | „Album Details"(Edit-Icon)-Modal mit dem Overflow bzw. der Detail-Ansicht zusammenführen (zwei konkurrierende „Details"-Konzepte an einer Zeile verwirren) |

### B2. Album-Detail-Ansicht (Deep-Link) verliert fast alle Album-Funktionen — ✅ behoben (siehe library-v2.md §30)

`AlbumDetailView` (`:2542–2682`) bietet nur Edit-Metadata + Track-Tabelle.
Retag, ReplayGain, Reorganize, Cover-Picker, Enrich, Quality-Profil, Delete,
Monitoring-Strategie — alles nur an der eingeklappten Zeile auf der
Artist-Seite verfügbar. Genau invers zu Lidarr (dort ist die Detailseite der
Ort für all das). **Fix:** dieselbe Action-Leiste (bzw. das Overflow-Menü aus
B1) in den Detail-Header heben — die Handler existieren alle schon als
Komponenten, es ist reine Verdrahtung. Erst DANN kann B1 die Zeile guten
Gewissens entrümpeln.

### B3. Features-Spalte: RG/LR immer anzeigen, ausgegraut wenn fehlend, klickbar als Aktion — ✅ behoben (siehe library-v2.md §29)

Aktuell (`:3650–3676`): Badges erscheinen nur bei Vorhandensein, sonst „—";
ReplayGain hat zusätzlich einen eigenen Action-Button in der Actions-Spalte.
Nutzer-Wunsch + Button-Ersparnis:
- **RG**-Badge immer rendern: grün = vorhanden (Tooltip: Werte), grau =
  fehlt → Klick startet `POST /tracks/<id>/replaygain` (Endpoint existiert);
  der separate `TrackReplayGainButton` entfällt.
- **LR**-Badge immer rendern: grün = vorhanden → Klick öffnet den Lyrics-Tab;
  grau = fehlt → Klick startet einen Lyrics-Fetch. Backend-Reuse:
  `core/repair_jobs/missing_lyrics.py` (LRClib, prüft Verfügbarkeit, embedded
  + .lrc-Sidecar) — braucht nur einen dünnen
  `POST /api/library/v2/tracks/<id>/fetch-lyrics`, der dieselbe Logik
  track-scoped aufruft.
- Pending-State im Badge (Spinner) statt separatem Button.
- Gleiches Muster perspektivisch für Album-Scope (Album-RG-Button → Badge im
  Album-Kopf), dann verschwindet noch ein Zeilen-Button.

### B4. Artist-Toolbar: 16 Aktionen → gruppieren, globale Aktionen raus aus dem Artist-Kontext — ✅ behoben (siehe library-v2.md §30/§54; optionale Monitoring-/Profile-Konsolidierung nach späterem Nutzerreview umgesetzt)

Aktuell (`:2888–3003`): Refresh & Scan, Automatic Search (global!),
Interactive Search, Update Discography, Search Upgrades (global!), Preview
Retag, Reorganize All, Maintenance, Manual Import, Manage Tracks, History,
Enrich, Edit Metadata, Monitoring, Profile, Delete. Vorschlag:

1. **Globale Aktionen verschieben:** „Automatic Search (global)" und „Search
   Upgrades (global)" gehören auf die Library-Übersicht (Header neben
   Import/Filter) oder zur Wishlist-Seite — nicht in die Artist-Toolbar
   (deckt sich mit §25.1; bis der scoped Search existiert, ist Verschieben der
   ehrlichste Zwischenschritt).
2. **Primärleiste (Lidarr-Kern):** Refresh & Scan · Automatic Search
   (artist-scoped, C1) · Interactive Search · Update Discography.
3. **Sekundär als Dropdown „Files/Tools":** Preview Retag, Reorganize All,
   Maintenance, Manual Import, Enrich.
4. **Rechts (Entity-Verwaltung):** Manage Tracks, History, Monitoring, Profile,
   Edit, Delete — Edit/Monitoring/Profile ggf. in EIN „Edit"-Modal mit Tabs
   (Metadata / Monitoring / Quality Profile), wie das Track-Detail-Modal es
   bereits vormacht.

**Update 2026-07-17:** Das spätere verbindliche Nutzerreview in §52.3/§52.4
hat die optionale Zusammenlegung konkretisiert. §54 ersetzt die getrennten
Artist-Aktionen „Monitoring" und „Profile" durch ein Settings-Gear neben dem
gebookmarkten Artist. Die gemeinsame Oberfläche verwendet die bestehende
Watchlist-Zeile, den app-weiten Quality-Profile-Picker und das vorhandene
Provider-Re-Matching; vorhandene und zukünftige Releases bleiben darin klar
getrennte Aktionsbereiche. Nur bei nicht gebookmarkten Artists bleibt der
Profile-Button separat erreichbar: Quality ist orthogonal zum Monitoring,
während ohne Bookmark noch keine Watchlist-Zeile für Artist Settings besteht.

### B5. Nutzer-konfigurierbare Anzeige (Spalten + Match-Provider + Features) — „richtig modal" — ✅ behoben (siehe library-v2.md §31)

Nutzer-Wunsch: pro Nutzer einstellen, welche Match-Provider, Spalten und
Badges sichtbar sind (Lidarr: „Table Options"-Zahnrad pro Tabelle).

**Design-Vorschlag:**
- Neue UI-Preferences, persistiert pro Profil (kleine
  `lib2_ui_preferences`-Tabelle oder JSON-Blob in `app_config`;
  localStorage wäre billiger, überlebt aber keinen Browser-Wechsel — DB
  bevorzugt, die App ist ohnehin profilbewusst).
- Ein „Options"-Popover an der Track-Tabelle (Zahnrad im Tabellenkopf):
  - Spalten an/aus: #, Disc, Artists, Match, Quality, Features, Metadata,
    Duration (A5), BPM (A5), File-Pfad, Format/Bitrate getrennt.
  - Match-Provider-Auswahl (Set aus `SERVICES`), Default „nur konfigurierte"
    (A8).
  - Features-Badges an/aus.
- Dieselbe Preferences-Quelle steuert Artist-Tabelle/Cards
  (Sort-/Spaltenwahl) und Interactive-Search-Spalten.
- Query-Seite: Spalten wie BPM/Duration/Pfad sind bereits im
  Album-Detail-Payload bzw. trivial ergänzbar — kein teures Backend.

### B6. Track-Tabelle: fehlende Legacy-Spalten, kein Sort, keine Mehrfachauswahl — ✅ behoben (siehe library-v2.md §31)

Legacy Enhanced View (`library.js:4787–4806`): Play, #, Disc, Title, Duration,
Format, Bitrate, BPM, File, Match, Queue + WriteTag/Delete, **sortierbar**,
mit Select-All-Checkbox + Batch-Edit + Batch-ReplayGain. V2-Tabelle
(`:3535–3547`): Monitor, #, Title, Artists, Match, Quality, Features,
Metadata, Actions — **nicht sortierbar, keine Auswahl, kein Bulk**.
- Sortierbare Header (rein clientseitig, Daten sind schon da).
- Checkbox-Spalte + Bulk-Leiste (Monitor an/aus, Quality-Profil, ReplayGain,
  Write Tags, Delete-Files-Preview) — Backend für alles vorhanden
  (`/tags/write` nimmt Track-Listen, ReplayGain-Batch existiert legacy-seitig,
  Monitor-Bulk existiert).
- Duration/BPM/File-Pfad als opt-in Spalten (B5). Damit ist die
  Enhanced-View-Spalten-Parity komplett.

### B7. Suche: Benennung & Scope endgültig Lidarr-konform machen (Zusammenführung) — ✅ behoben (siehe library-v2.md §30)

Zielbild nach C1 (ergänzt §25.1 um die Upgrade-Frage, die dort offen blieb):
- **Automatic Search** (Artist/Album/Track) = scoped: sucht Missing **und**
  Upgrades gemäß Quality-Profil (Upgrade-Erlaubnis/Cutoff entscheidet das
  Profil — exakt Lidarrs Verhalten: erlaubt das Profil Upgrades, upgraded die
  Suche automatisch; sonst nur Missing).
- **„Search Upgrades"-Button entfällt** auf Artist-Ebene ersatzlos — die
  Funktion geht im scoped Automatic Search auf. Der globale
  `lib2_upgrade_scan`-Repair-Job (24h) bleibt die Automation; die globale
  manuelle Variante wandert in die Library-Übersicht (B4.1).
- Interactive Search bleibt wie ist (bereits Lidarr-konform benannt).

---

## C. Backend-Design-Findings

### C1. Artist-/Album-/Track-scoped Automatic Search (konkretisiertes Design zu §25.1) — ✅ umgesetzt (siehe library-v2.md §29)

Reuse-first, keine zweite Pipeline:
1. **Scope-Auflösung serverseitig:** neuer Endpoint
   `POST /api/library/v2/<entity>/<id>/search` →
   (a) für den Scope einen **inline Upgrade-Scan** laufen lassen
   (`wishlist_mirror.py` kann Upgrade-Kandidaten bereits selektieren — heute
   nur global verdrahtet; auf Artist-/Album-/Track-Filter der
   `lib2_wanted_tracks`/Files einschränken), damit profile-erlaubte Upgrades
   frisch in der Wishlist liegen;
   (b) anschließend die Wishlist-Verarbeitung **nur für diese Items** anstoßen.
2. Die Wishlist-Verarbeitung braucht dafür einen Scope-Parameter
   (`/api/wishlist/process` akzeptiert heute keinen — `web_server.py:18229`).
   Kleinster Eingriff: optionale `wishlist_ids`/`lib2_scope`-Liste, die der
   Worker als Filter auf seine Task-Auswahl legt; Retry-/Candidate-Walk bleibt
   unverändert.
3. Track-Level ersetzt `autoGrabBest` (A4) durch denselben Endpoint.
4. Alle Grabs laufen damit automatisch durch die bestehende
   Acquisition-Korrelation (`scheduled_grab_correlated`) → History (A6) wird
   automatisch reicher.

**Update 2026-07-17:** Der im Nutzerreview §52.6 gefundene Restfall ist nun
ebenfalls geschlossen (§53): eine direkt ausgelöste Track-Suche darf einen
unmonitored Track einmalig in Wishlist/Dispatcher geben, ohne dessen
Monitorflag oder Wanted-Regel zu verändern. Artist-/Album-Scope bleibt
wanted-only. Ergänzend ist die Produktentscheidung für frühe Materialisierung
jetzt verbindlich: Jeder bestätigte Wishlist-/Acquisition-Write aus Search,
Playlist-Sync, Watchlist-Scanner oder einem anderen Eingangspfad muss die
zugehörigen lib2-Entities samt explizitem Profil **vor** dem Download
idempotent anlegen; der technische Restumfang bleibt library-v2.md §52.8.

### C2. Manage Tracks → Lidarr „Manage Track Files" — ✅ behoben (siehe library-v2.md §30)

Heute: nur Single↔Album-Duplikat-Paare (`/artists/<id>/duplicates`).
Lidarr-Modell: Liste **aller Track-Files** (Pfad relativ, Größe, Quality,
Datum) mit Mehrfachauswahl + Delete. Alles Nötige existiert:
`lib2_track_files` (Pfad/Größe/Quality/Lifecycle), ADR-05-File-Delete-Maschine
(Preview-Token, Journal, fail-closed Root-Safety) — nur heute auf
Artist-/Album-Scope begrenzt.
- Neuer Read: `GET /artists/<id>/track-files` (paginiert, mit Quality/State).
- ADR-05-Preview/Execute um eine `file_ids`-Auswahl erweitern (der
  Snapshot-Token-Mechanismus trägt File-IDs bereits).
- UI: Manage-Tracks-Modal bekommt zwei Tabs: „Duplicates" (heutiger Inhalt,
  inkl. der offenen ui-req-3.1/3.2-Delete-Wünsche) + „Files" (neue Liste).
- Deckt zugleich `library-v2-ui-requirements.md` §5.1 ab (dort als größerer
  Refactor notiert — mit ADR-05-Reuse ist es deutlich kleiner als dort
  vermutet).

### C3. History-Read-Vereinheitlichung (Umsetzung zu A6) — 🟡 Basis umgesetzt, Track-/Pipeline-Sicht wieder offen (siehe library-v2.md §52.9)

Ein `core/library2/history_feed.py`-Helper, der pro Scope (artist/album/track)
die vier Quellen merged und ein einheitliches
`{date, event_type, title, detail, source}`-Schema liefert; Endpoint ersetzt
den heutigen `track_downloads`-only-Read (dessen Zeilen als
`event_type='downloaded'` einfließen). Namens-Matching (`track_artist LIKE`)
nur noch als Legacy-Fallback — primär über `entity_id`-Joins
(acquisition_requests tragen lib2-Scope+ID; `track_downloads` ist über
`legacy_track_id` erreichbar, wie `source_info.py` es vormacht).

**Update 2026-07-17:** Der Merge-Helper und die Artist-History sind eine
brauchbare Basis, erfüllen aber die neue Nutzeranforderung noch nicht. Ein
Quality-/Acoustic-ID-Fail kann vor dem finalen File-Autolink passieren; die
Track-Info zeigt nicht die vollständige korrelierte Search→Check→Quarantäne→
Freigabe→Import-Timeline. §52.9 ist daher der verbindliche Rest-Scope und
ersetzt den früheren Eindruck „vollständig umgesetzt".

### C4. Pipeline-Resultate pro File persistieren (Lücke hinter A7) — ✅ teilweise umgesetzt (siehe library-v2.md §37)

`lib2_track_files` kennt `verification_status` + `import_status`, aber nicht:
Quality-Gate-Ergebnis (bestanden/Fallback/übersprungen + Profil-ID),
AcoustID-Fehlgrund, Quarantäne-Referenz. Der Import-Callback (eine Stelle,
`record_download_provenance`/Autolink-Hook) sollte ein kompaktes
`pipeline_result_json` auf die File-Zeile schreiben (oder ein
`lib2_file_events`-Journal im Stil von `lib2_entity_history`). Ohne das bleibt
A7 für nicht-acquisition-korrelierte Downloads (der Normalfall heute) leer.

---

## D. Kleinere Beobachtungen

- **D1. Track-Detail wieder als Edit-Aktion — ✅ umgesetzt (siehe library-v2.md §53):**
  Track-Actions-Spalte nutzte das `edit`-Icon für „Track details", die
  Album-Zeile für „Album details", der Artist-Header für „Edit Metadata" —
  dreimal dasselbe Icon, drei verschiedene Bedeutungen. `TrackDetailButton`
  verwendete zwischenzeitlich das `info`-Icon. Da das dahinterliegende Modal
  nicht nur Infos zeigt, sondern Quality Profile, Metadata, Tags und Lyrics
  bearbeitet, verwendet es jetzt wieder das Pencil/Edit-Icon mit entsprechend
  vollständigem Tooltip. Diese konkrete Nutzerentscheidung ersetzt die
  frühere D1-Lösung aus §33.
- **D2. `EnrichDropdown` vs. `ManualMatchModal`:** Enrich re-queried einen
  Provider, Manual Match ändert die Provider-ID — für Nutzer schwer zu
  unterscheiden („frische Daten holen" vs. „Zuordnung korrigieren"). Ein
  gemeinsames „Provider"-Modal (Chips + pro Provider: Status, Re-Match,
  Enrich) würde beide Konzepte an einem Ort erklären und einen
  Toolbar-Eintrag sparen. Weiterhin offen — größerer Modal-Merge, keine
  Notwendigkeit ihn ungefragt anzugehen.
- **D3. Interactive-Search-Ergebnisspalten** — **Teil 1 (Profil-Filter) ✅
  behoben (siehe library-v2.md §33):** eine „Only show results meeting
  cutoff"-Checkbox nutzt dieselbe `profileTargetRank`-Logik wie das
  bestehende `ProfileBadge` (inkl. dessen „nie fälschlich verstecken"-Regel
  für Ergebnisse ohne beurteilbare Qualitäts-Fakten). **Teil 2
  (konfigurierbare Spalten, B5 einbeziehen) bewusst ausgelassen:** bei nur 7
  festen Spalten ist der Nutzen gegenüber dem Aufwand gering, anders als bei
  der deutlich breiteren Track-Tabelle.
- **D4. Import-Polling/Live-Fortschritt — ✅ behoben (siehe library-v2.md
  §40):** Der manuelle `waitForLibraryV2Import`-Loop samt Zehn-Minuten-Timeout
  ist entfernt. Ein geteilter React-Query-Status hängt sich auch nach Reload
  an laufende Importe, pollt ohne künstliches Zeitbudget, zeigt Backend-Stufe,
  Zähler, Prozent und Progress-Bar und invalidiert nach Erfolg die Library-v2-
  Queries. `window.location.reload()` entfällt vollständig. Die Backend-
  Precache-Stufen garantieren zusätzlich Start-/Endstände auch unterhalb ihrer
  früheren Batch-Schwellen; Tracklist-Fortschritt bleibt über Cache-/Provider-
  Phase monoton.
- **D5. Play/Preview fehlt komplett** (Legacy hat `col-play` mit
  Stream-Preview pro Track). Bewusste Lücke? Nirgends dokumentiert — als
  Produktentscheidung festhalten oder als Parity-Punkt aufnehmen.
- **D6. Artist-Tabelle — ✅ behoben (siehe library-v2.md §33):** hatte keine
  Quality-Profile-/Genre-/Added-Spalte und keinen Spalten-Zahnrad. Neue
  `artist_table.columns`-Sektion in den UI-Preferences (Default aus) +
  Zahnrad neben dem Cards/Table-Umschalter (nur in der Table-Ansicht). Backend
  brauchte nichts Neues — `quality_profile_id`/`genres`/`added_at` waren im
  Summary-Payload bereits vorhanden.

---

## E. Priorisierung (Vorschlag — inkl. Runde-2-Funde aus G/H/I)

**Bugs zuerst (klein, klar umrissen, real):** — ✅ Punkte 1–4 behoben
2026-07-16, siehe library-v2.md §28.
1. ~~**A1 + A2 (Cover-Embed + Cache-Bust)** — kritisch: Kernversprechen des
   frisch gelieferten §49 ist sonst nicht eingelöst.~~
2. ~~**G1 (Discography-Single-Swallow + External-ID-Vergiftung)** —
   korrumpiert Katalog-Identitäten; wahrscheinliche Mit-Ursache des
   §38-Restsymptoms.~~
3. ~~**G4 (Autolink-Feat-Titel-Duplikat → Wishlist lädt doppelt)** — kostet
   real Downloads/Bandbreite.~~
4. ~~**G2 + G3 (ReplayGain-Tag-Cache auf gemappten Pfaden / fehlende
   Invalidierung)** und **G5 (has_lyrics vs. unsyncedlyrics)** — kleine,
   gezielte Fixes; G6 (falsche Fußnote) nebenbei.~~

**Dann Architektur/UX:** — ✅ Punkte 5–8 + 11 (bis auf H5/I10/I6/H13, s.u.)
+ A8/A9 aus Punkt 11 behoben 2026-07-16, siehe library-v2.md §29/§30.
5. ~~**C1 (scoped Automatic Search)** — größter Verständnis-Gewinn, ersetzt
   A3/A4 gleich mit.~~ ~~**B7** (Search-Upgrades-Button-Konsolidierung auf
   der Artist-Toolbar)~~ behoben 2026-07-16 (§30). **I10** („search on
   monitor") ist durch C1 jetzt trivial nachziehbar, aber noch nicht
   umgesetzt — reine UI-Verdrahtung, kein neues Backend nötig; bleibt ein
   I-Punkt (Nutzer-Abstimmung vor Umsetzung).
6. ~~**G7 (Reorganize-Queue-Status)**~~ — **I6** (Queue-Sichtbarkeit direkt an
   der Album-/Track-Zeile, nicht nur im Reorganize-Modal) und **H13**
   (identisch mit G7, Legacy-Referenz) bleiben offen — I/H-Punkte brauchen
   erst Nutzer-Abstimmung zu Scope/UI-Ansatz (siehe Hinweis oben).
7. ~~**B3 (klickbare RG/LR-Badges)** + **A5 (BPM/Duration)**~~ — sichtbare
   Quick-Wins, gespart wurde der separate ReplayGain-Button.
8. ~~**B1 + B2 + B4 (Entrümpelung Zeile/Toolbar/Detail-Ansicht)**~~ behoben
   2026-07-16 (§30) — neues generisches Overflow-Menü-Muster, Albumtitel als
   Detail-Link, `AlbumOverflowMenu` jetzt auch im Detail-Header (B2), Artist-
   Toolbar in Primär-/Tools-Dropdown-/Entity-Gruppen (B4). Die damals nur als
   „ggf." markierte Monitoring-/Profile-Zusammenlegung wurde nach dem späteren
   verbindlichen Nutzerreview in §52.3/§52.4 umgesetzt (§54), ohne die
   Metadatenbearbeitung künstlich in dasselbe Formular zu zwingen.
9. ~~**A6/C3 (History)**~~ behoben 2026-07-16 (§35): die befürchtete
   Fehlzuordnungsgefahr entfiel, weil `core/acquisition/catalog.py` die
   scope→lib2-Entity-Auflösung für den Search-Pfad bereits korrekt besitzt —
   `history_feed.py` läuft dieselbe Beziehungskette nur rückwärts, statt einen
   zweiten Resolver zu erfinden. ~~**A7/C4 (Lifecycle-Persistenz pro File)**~~
   teilweise behoben 2026-07-16 (§37): derselbe Import-Callback (`autolink.py`)
   persistiert jetzt Quality-Gate-Fallback + AcoustID-Detail pro File — die
   Quarantäne-Geschichte-Timeline aus A7 blieb bewusst aus (Redundanz zu §35).
10. ~~**B5/B6 (konfigurierbare Spalten/Provider, Sort, Bulk)**~~ behoben
    2026-07-16 (§31: Options-Zahnrad mit persistierten Spalten- + Provider-
    Sichtbarkeits-Prefs, clientseitiger Sort, Checkbox-Mehrfachauswahl +
    Bulk-Leiste Monitor/Unmonitor/Write-Tags/ReplayGain/Delete). **H6/H7/H8**
    (A-Z-Selector/Inline-Edit/eigene Bulk-Bar-Variante der Legacy-Tabelle)
    bleiben als H-Punkte offen (Nutzer-Abstimmung vor Umsetzung) — B6s
    Bulk-Leiste deckt den funktionalen Kern von H8 bereits ab, ohne dass H8
    selbst als Punkt geschlossen wäre.
11. ~~**C2 (Manage Track Files)**~~ behoben 2026-07-16 (§30, inkl. optionalem
    `file_ids`-Scope auf den bestehenden ADR-05-Endpoints) + **H5** nach dem
    verbindlichen §52.11-Redesign ebenfalls umgesetzt (§54: gemeinsamer
    DB-only/permanenter Dialog und Journalvertrag) + ~~A8/A9~~.
12. **Strategisch klären, dann bauen:** I1 (Add Artist), I2 (Wanted-Views),
    I4 (Metadata Profile), H1 (Playback), H3 (Discography-Batch-Download),
    H9 (Multi-User-Frage von lib2).

---

## G. Code-Audit Runde 2 — neue Bugs (Zeile für Zeile gefunden, nicht Nutzer-gemeldet)

### G1. Discography-Match frisst Singles, die den Titel eines Albums teilen — und vergiftet dessen Provider-Identität — ✅ behoben (siehe library-v2.md §28)

`_match_existing` (`core/library2/discography.py:155–175`): Nach dem
Provider-ID-Match und dem bucket-gleichen Titel-Match fällt es auf
`candidates[0]` zurück — **über Bucket-Grenzen hinweg**. Szenario: Library hat
Album „Faith" (Deezer-ID A). Der Provider-Katalog enthält Album „Faith" (ID A)
und Single „Faith" (ID S). Die Single findet keinen ID-Match und keinen
Single-Bucket-Kandidaten → Fallback matcht sie aufs **Album** →
`_merge_external_id` (`:103–109`) überschreibt bedingungslos
`external_ids["deezer"] = S`. Folgen:
1. Die Single erscheint **nie** als eigene Row („Update Discography findet nur
   Singles nicht" / unvollständiger Katalog — passt zum §38-Restsymptom).
2. Das Album trägt jetzt die **Provider-ID der Single** → die nächste
   Tracklist-Auflösung (`completeness.py` bindet an `external_ids`) kann die
   Single-Tracklist für das Album fetchen und via Snapshot-Referenz-Wechsel
   den korrekten Cache invalidieren.

**Fix-Richtung:** (a) Cross-Bucket-Fallback nur zulassen, wenn die
Provider-Release **keine** eigene ID hat; (b) `_merge_external_id` darf eine
VORHANDENE, abweichende ID derselben Source nie stillschweigend überschreiben
(Konflikt loggen, Row unangetastet lassen). Regressionstest: Album+Single
gleichen Titels im Provider-Snapshot.

### G2. Album-ReplayGain aktualisiert den Tag-Cache auf path-gemappten Setups nie (Resolver-Invariante verletzt) — ✅ behoben (siehe library-v2.md §28)

`analyze_album_replaygain` (`core/library2/replaygain.py:125–139`): Nach dem
Tag-Write wird die File-Row per `WHERE path=?` mit dem **aufgelösten** Pfad
gesucht — gespeichert ist aber die Media-Server-Sicht (§1-Invariante!). Auf
jedem Setup mit Path-Mapping (Docker: `/music/...` vs. lokal) findet das
UPDATE nichts → `tags_json` bleibt alt → RG-Badge bleibt grau, obwohl die
Tags geschrieben wurden. Die `analyzed`-Liste verliert `track_id`/`file_id`
(`:96,113`) — genau die hätte man durchreichen müssen; die
Track-Level-Variante (`:201`) macht es korrekt mit `file_row["id"]`.

### G3. Per-Track-ReplayGain-Button invalidiert die Query nicht — ✅ behoben (siehe library-v2.md §28)

`TrackReplayGainButton` (`library-v2-page.tsx:3716–3736`): `onSuccess` setzt
nur `setDone(true)` — keine `invalidateQueries`. Das frisch geschriebene
`has_replaygain` (Backend persistiert es korrekt via
`read_and_persist_tag_cache`) erscheint erst nach irgendeinem fremden Refetch.
Der Album-RG-Button macht es richtig (`awaitBulkJob` invalidiert am Ende,
`:3209`).

### G4. Autolink-Heuristik hat die §39-Lektion nicht gelernt → Duplikat-Track statt Missing-Slot-Füllung — ✅ behoben (siehe library-v2.md §28)

`_find_or_create_track` (`core/library2/autolink.py:107–137`) matcht nur
Spotify-ID → exakter normalisierter Titel. Der häufigste reale Unterschied —
Featured-Annotation im Titel („One Dance (feat. Wizkid & Kyla)" vs. „One
Dance") — wurde in §39 für den Importer mit `dedup_title_key` gefixt, hier
aber nicht: ein fertiger Download mit Feat-Titel matcht die fileless
Wanted-Row NICHT → neue Duplikat-Row mit File, die Wanted-Row bleibt missing
→ **die Wishlist lädt denselben Track erneut**. Betrifft alle Downloads ohne
`lib2_track_id`-Kontext (Watchlist-New-Releases, manuelle Suchen von der
Search-Seite, Playlists). Fix: `dedup_title_key` wiederverwenden + Fallback
auf (disc, track_number)-Slot, bevor eine neue Row entsteht.

### G5. `has_lyrics` erkennt nur den `lyrics`-Tag — Lyrics-Tab und LR-Badge widersprechen sich — ✅ behoben (siehe library-v2.md §28)

`queries.py:677`: `has_lyrics = bool(tags_data.get("lyrics"))`. Der
Lyrics-Tab liest aber `tags.lyrics || tags.unsyncedlyrics`
(`library-v2-page.tsx:4384`), und der `missing_lyrics`-Repair-Job legt
zusätzlich `.lrc`-Sidecars an. Ein Track mit USLT-only-Lyrics oder Sidecar
zeigt LR=fehlend, obwohl der Lyrics-Tab Text anzeigt. Fix: `unsyncedlyrics`
in die Ableitung aufnehmen (Sidecar-Erkennung optional, dann aber auch im
Tag-Cache erfassen).

### G6. Interactive-Search-Fußnote ist faktisch falsch (Autolink existiert) — ✅ behoben (siehe library-v2.md §28)

`interactive-search.tsx:594–597`: „Use ‚Refresh & Scan' afterwards to pull
new files into the v2 library" — seit dem Autolink-Hook (§3) verlinken
fertige Downloads automatisch. Die Fußnote schickt Nutzer in unnötige
Full-Scans und untergräbt das Vertrauen in den Auto-Flow.

### G7. lib2-Reorganize ist fire-and-forget — die Queue hat in lib2 kein Gesicht — ✅ behoben (siehe library-v2.md §29)

`reorganize-modal.tsx` meldet nur „N queued". Die Legacy-UI hat ein
komplettes Queue-Status-Panel (`mountReorganizeStatusPanel`,
Per-Item-Cancel, Clear, Live-Polling gegen die Reorganize-Queue). In lib2
sind Kollisionen/Fehler nach dem Enqueue **unsichtbar**; ob Files wirklich
gemoved wurden, sieht man nur indirekt. Fix: das bestehende
Queue-Status-API wiederverwenden (dünner Read reicht — Panel oder
Status-Zeile im Modal mit Poll bis Queue leer).

### G8. Kleinere Runde-2-Funde (gesammelt) — 4 von 4 behoben (siehe library-v2.md §30, 4. Punkt siehe §38)

- **`auto_monitor_releases` überfährt Flags:** `UPDATE lib2_tracks SET
  monitored=1 WHERE album_id=?` (`discography.py:522`) flippt auch explizit
  unmonitorte Tracks (nur das Kompatibilitäts-Flag; die Projektion respektiert
  die `user_explicit`-Rule — aber Flag und Projektion divergieren dann
  sichtbar). — ✅ behoben: veto per `explicitly_unmonitored_track_ids`.
- **Retry-/Prune-Scope-Asymmetrie:** der Auto-Monitor-Retry
  (`discography.py:267–282`) filtert auf `primary_artist_id`, Index/Prune
  laufen über die `lib2_album_artists`-Junction — ein Album, dessen Primary
  ein anderer Artist ist, wird nie retried. — ✅ behoben: Retry-Query joint
  jetzt ebenfalls über die Junction.
- **Autolink → `recompute_wanted` mit Default-Profil 1**
  (`autolink.py:279`): verletzt die §1-Invariante „Profil-IDs nie hart auf 1"
  (im Pipeline-Kontext gibt es kein Request-Profil; sauber wäre der
  Admin-/Default-Lookup wie überall sonst). — ✅ behoben:
  `default_quality_profile_id(conn)`.
- **`_find_or_create_artist`-Slow-Path** (`autolink.py:62`) scannt bei jedem
  nicht-exakten Treffer die GANZE Artist-Tabelle pro fertigem Download —
  bei großen Libraries messbar; und er kennt weder `external_ids` noch die
  §40-Alias-Gruppen (Duplikat-Artist-Risiko, deckt sich mit §40). — ✅
  behoben (siehe library-v2.md §38): ID-Match (`spotify_id`, bereits
  indiziert) läuft jetzt VOR dem Namens-Matching und wird auf Namens-Treffer
  zurückgeschrieben (Backfill), sodass der Slow-Path-Scan mit der Zeit
  zunehmend seltener erreicht wird. Bewusst NICHT Teil des Fixes: eine
  Alias-Erkennungs-Heuristik — die bleibt laut §40.1 manuell, um genau die
  Fehlzuordnung zu vermeiden, vor der A6/A7 schon zurückschrecken ließ.
- **Track-Automatic-Search-Query ohne Albumkontext:**
  `buildSearchQuery` wirft den Album-Teil bewusst weg — bei generischen
  Titeln („Intro") grabbt der Best-Pick beliebige Versionen. Wird durch C1
  (serverseitige scoped Search) mit erledigt.

---

## H. Vollständiger Feature-Gap: Legacy Enhanced View → V2 (über §45–§51 hinaus)

**Vor Umsetzung eines H-Punkts: erst beim Nutzer nachfragen**, siehe Hinweis
oben — diese Tabelle ist eine Enumeration, kein Spec.

Systematische Enumeration aller `library.js`-Funktionen (9.691 Zeilen) gegen
den V2-Stand. Neu identifiziert, bisher NIRGENDS getrackt:

| # | Legacy-Feature | Code-Referenz | V2-Stand |
|---|---|---|---|
| H1 | **Track-Playback/Preview** (Play-Button pro Track, Streaming) | `playLibraryTrack`, `col-play` | ✅ umgesetzt (siehe library-v2.md §36) — Play-Spalte ruft die Legacy-Funktion über die Shell-Bridge auf, kein neuer Player |
| H2 | **Artist Top Tracks** (Hero-Sektion, Last.fm-Fallback, „Download one/all") | `_loadArtistTopTracks`, `/api/artist/<id>/top-tracks` | ❌ vom User am 2026-07-17 ausdrücklich nicht gewollt |
| H3 | **Discography-Download-Modal** (Releases multi-selektieren → Batch-Download, Filter, Select-All) | `openDiscographyModal`, `startDiscographyDownload` | fehlt — lib2 kann nur monitor→wishlist pro Release |
| H4 | **Track-Redownload-Modal** (Quellen streamen, gezielt neu laden) | `showTrackRedownloadModal` | ⏸️ zurückgestellt/nicht nötig; falls später: neu suchen und erst nach verifiziertem Import atomar ersetzen (§52.1/§52.6) |
| H5 | **Track-/Album-Delete in der Tabelle** (inkl. Smart-Delete-Dialog) | `deleteLibraryTrack`, `_showSmartDeleteDialog`, `col-delete` | ✅ umgesetzt nach verbindlichem Nutzerreview §52.11: gemeinsamer Dialog für Manage Tracks und Album-/Artist-Shortcut, DB-only vs. permanent, sichere Pfade und gemeinsames Journal (library-v2.md §54) |
| H6 | **A-Z-Alphabet-Selector + Source-/Watchlist-Filter + Stats-Header** der Artist-Liste | ❌ vom User am 2026-07-17 verworfen (nicht nötig; Textsuche und Paging reichen aus) | V2 hat nur Suche/Sort/Monitor-Filter/Paging |
| H7 | **Inline-Edit in der Tabelle** (Klick auf BPM-Zelle etc.) | ❌ vom User am 2026-07-17 verworfen (Modale reichen aus und sind robuster) | fehlt (V2: Modal-only) |
| H8 | **Bulk-Selektion + Bulk-Bar** (Batch-Write-Tags, Batch-ReplayGain, Bulk-Edit-Modal) | ✅ vom User am 2026-07-17 beibehalten (Bulk-Edit-Modal zum gemeinsamen Überschreiben von Metadaten-Feldern mehrerer Tracks umsetzen) | fehlt (deckt sich mit B6) |
| H9 | **Report-Button für Nicht-Admins** (Multi-User: Problem melden statt löschen) | ❌ vom User am 2026-07-17 verworfen (nicht nötig, da Library v2 admin-only ist) | fehlt — lib2 ist bisher rein admin-gedacht |
| H10 | **„Watch All Unwatched"-Bulk-Tool** | ❌ vom User am 2026-07-17 verworfen (nicht nötig; gezielte Auswahl ist besser) | fehlt |
| H11 | **Artist-Record-Inspector** (Raw-JSON-Ansicht mit Filter/Copy/Download) | ❌ vom User am 2026-07-17 verworfen (nicht nötig; Entwicklerkonsole reicht aus) | fehlt (Debug-Feature, niedrig) |
| H12 | **Export**: Artist-Roster (Watchlist/Library) + **M3U-Export** | ⏸️ vom User am 2026-07-17 aufgeschoben/zurückgestellt | fehlt |
| H13 | **Reorganize-Queue-Status-Panel** (Live, Cancel, Clear) | `mountReorganizeStatusPanel` | ✅ behoben (siehe library-v2.md §29 / G7) |

Bewertung: H1 (Playback) und H3 (Discography-Batch-Download) waren die
größten funktionalen Regressionen; H6/H8 die alltäglichsten. H9 ist
strategisch (Multi-User-Fähigkeit von lib2 ist ungeklärt — ADR-01 sagt
admin-only, die Legacy-UI hatte aber ein Nicht-Admin-Verhalten). **Update
2026-07-16:** H1 umgesetzt (library-v2.md §36); H3 vom Nutzer nach Rückfrage
explizit nicht gewollt (kein Bedarf für die Mehrfachauswahl-UI) — bleibt als
Enumerationspunkt stehen, aber ohne Umsetzungsabsicht. **Update 2026-07-17:**
Dasselbe gilt nun ausdrücklich für H2. H4 ist zurückgestellt. Die Tabelle ist
keine allgemeine Legacy-Paritäts-Roadmap; §52 definiert den angenommenen Scope.

---

## I. Vollständiger Feature-Gap: Lidarr → V2 (über die Search-Semantik hinaus)

**Vor Umsetzung eines I-Punkts: erst beim Nutzer nachfragen**, siehe Hinweis
oben — diese Tabelle ist eine Enumeration, kein Spec.

| # | Lidarr-Konzept | V2-Stand | Einschätzung |
|---|---|---|---|
| I1 | **Add Artist** (Provider-Suche → hinzufügen mit Monitor-Optionen all/future/missing/existing/first/latest/none + „search on add") | ❌ vom User am 2026-07-17 verworfen (nicht notwendig, da bereits über Search/Watchlist möglich) | größte konzeptionelle Lücke für „Library Manager" |
| I2 | **Wanted-Views**: globale „Missing"- und „Cutoff Unmet"-Listen | ✅ vom User am 2026-07-17 beibehalten (globale Übersichten über die gesamte Library hinweg) | passt zu B4/B7 — dorthin gehören auch die globalen Search-Buttons |
| I3 | **Mass Editor** (Artists multi-selektieren → Monitor/Profil setzen; Album Studio) | ❌ vom User am 2026-07-17 verworfen (nicht benötigt, da Einzel-Zahnrad ausreichend) | mittel |
| I4 | **Metadata Profile** (welche Release-Typen/Status der Discography-Fetch berücksichtigt) | ❌ vom User am 2026-07-17 verworfen (kein separates Profilsystem nötig; Watchlist-Regeln reichen aus) | kein separates Profilsystem bauen, solange nach §52.4 kein echter Restbedarf belegt ist |
| I5 | **Kalender / kommende Releases** | fehlt (Watchlist-Scanner arbeitet unsichtbar) | ❌ vom User am 2026-07-17 ausdrücklich abgelehnt |
| I6 | **Queue-Sichtbarkeit an der Entity** (Lidarr zeigt laufende Grabs/Queue direkt an Album/Track-Zeile) | ✅ vom User am 2026-07-17 beibehalten (laufende Downloads direkt an Album-/Track-Zeilen visualisieren) | hoch für Vertrauen in den Auto-Flow; Daten existieren (acquisition_grabs + Downloads-API) |
| I7 | **Blocklist-Ansicht** (einsehen/aufheben) | ❌ vom User am 2026-07-17 verworfen (keine separate UI nötig; Blockierungen sind selten) | klein |
| I8 | **Root-Folder/Pfad + Diskspace am Artist** | ✅ vom User am 2026-07-17 beibehalten (Speicherplatzgröße pro Artist/Album anzeigen, absoluter Pfad nicht zwingend nötig) | klein |
| I9 | **Unmapped Files** (Dateien ohne Katalog-Zuordnung) | ❌ vom User am 2026-07-17 verworfen (Hintergrund-Job `orphan_file_detector` reicht aus) | klein |
| I10 | **„Search on monitor"**: Lidarr sucht direkt nach dem Monitoren (opt-in) | ❌ vom User am 2026-07-17 verworfen (nicht nötig; gezieltes Suchen läuft bereits direkt über den Automatic Search Button des Tracks) | Quick-Win: Option „search immediately" am Monitor-Flow (nach C1 trivial) |

---

## F. Bereits getrackt (hier NICHT dupliziert, nur Querverweis)

- Artist-scoped Automatic Search als Grundidee: `library-v2.md` §25.1 /
  ui-requirements §6.4 (hier durch C1/B7 konkretisiert und um die
  Upgrade-Semantik erweitert).
- §45 Reidentify, §51 „I Have This"-Hälfte: offen laut §15 (Status dort
  korrigiert) — beide bewusst zurückgestellt (siehe library-v2.md §34):
  echte Datei-mutierende Pfade (Staged Copy + Re-Import-Pipeline bzw.
  Hint-Datei), keine dünnen Wrapper wie ursprünglich angenommen. §48
  Rich-Metadata-Edit-Rest (BPM/Style/Mood/Label/Bulk) ist jetzt vollständig
  umgesetzt (library-v2.md §34).
- ~~Manage-Tracks-Delete-Wünsche: ui-requirements §3.1/§3.2/§5.1~~ durch C2
  konkretisiert und mit dem gemeinsamen §52.11-Flow in library-v2.md §54
  abgeschlossen.
- ~~Import-Progress/Timeout: P2-25~~ umgesetzt in library-v2.md §40;
  ~~Artist-Credit-Splitting-Restrisiko P2-24 + Download-Engine-Verantwortung
  P2-23~~ umgesetzt in library-v2.md §41.
- §40/§41 Alias-Registry-Folgearbeiten (Fan-out-Sweep, Merge-UI), §38-Rest
  (Live-Katalog-Fetch-Verifikation), §42 (laut ui-req §5.2 abgeschlossen).
