# Library-v2-Rewrite-Audit — 13. August 2026

## Auftrag und Scope

Geprüft wurde ausschließlich der Umbau der Worker, Dienste und öffentlichen
Kompatibilitätsschichten auf den Library-v2-Katalog. Der untersuchte Verlauf ist
`eb69f18fb^..7595b6dd4` (48 Commits); der eigentliche Worker-/Service-Deep-Dive
lag auf `20f43a337^..7595b6dd4` (47 Commits, inklusive Provider-Attempt-Ledger).
Absichtlich noch geparkte Legacy-Flächen wurden nicht allein deshalb als Fehler
gewertet, dass ihre Migration noch aussteht.

Gelesene Projektquellen:

- alle `docs/library-v2-*.md` einschließlich Guide, Features, Issues, Status,
  Metadaten-Migration und Pre-Fixes;
- die Claude-Projekt-Memory unter
  `/home/cyran/.claude/projects/-home-cyran-Projects-05-Soulsync_fork/memory`;
- Diff, Aufrufer und Tests der betroffenen Library-/Worker-/API-Pfade.

Mehrere externe Claude-/Codex-Unterprüfungen wurden angestoßen, konnten in
dieser Sandbox aber wegen gesperrtem Netzwerk/Timeout nicht laufen. Die
nachstehenden Befunde stammen deshalb aus unabhängigen manuellen Querschnitten,
statischen Aufruferanalysen, gezielten SQLite-Reproduktionen und Tests in dieser
Arbeitskopie; es wird kein erfolgreicher externer Agentenlauf behauptet.

## Verifizierte Verträge

- lib2 ist die Katalogautorität; Legacy-IDs sind Rückreferenzen, keine native
  Identität.
- Medienserver-IDs sind nur zusammen mit `server_source` eindeutig und dürfen
  nicht mit lib2-Primärschlüsseln verwechselt werden.
- `origin='library'` bezeichnet Besitz. Provider-Diskografie allein ist weder
  „owned“ noch ein lokaler Track ohne Dateigröße.
- Physische Dateien sind `lib2_track_files`-Zeilen; gelöschte Zeilen sind
  Historie, und mehrere aktive Formate/Alternativen pro Track sind zulässig.
- Die öffentliche API behält die bisherigen Feldnamen und Antwortformen.
- Provider-Attempts verhindern, dass ein Worker dieselbe Entität dauerhaft
  erneut auswählt oder nach dem Wechsel die ganze Bibliothek erneut abfragt.

## Findings

### LV2-AUD-01 — P1 — Provider-Attempt-Backfill ist nicht fortsetzbar

`core/library2/provider_attempts.py:235-240` liest pro Entität höchstens 100.000
Legacy-Zeilen ohne Cursor/Offset. Der Mirror setzt anschließend
`_seeded_provider_attempts=True`, auch nach einem Teil- oder Fehlerlauf. Bei mehr
als 100.000 Track-Rückreferenzen bleiben alle späteren Zeilen dauerhaft ohne
Ledger; ein Neustart liest wieder denselben ersten Block. Reproduktion mit drei
Zeilen und `limit=2`: auch der zweite Lauf erreicht die dritte Zeile nicht.

### LV2-AUD-02 — P2 — Similar-Artists-Historie wird nicht übernommen

`core/library2/provider_attempts.py:221-224` leitet Backfill-Spalten nur aus
`match_status.SERVICES` ab. `similar_artists` ist bewusst ein
`DERIVED_SERVICE`, sein Legacy-Status liegt aber in
`artists.similar_artists_match_status`. Bereits abgearbeitete Künstler werden
nach dem Umschalten erneut als pending behandelt. Gezielte Reproduktion: ein
Legacy-Artist mit `similar_artists_match_status='matched'` erzeugt keine
Attempt-Zeile.

### LV2-AUD-03 — P2 — Similar-Artists-Queue akzeptiert unbrauchbare Provider-IDs

`core/library2/worker_queue.py:75-78` betrachtet jedes nichtleere
`external_ids`-Objekt als verwendbare Identität. Der Similar-Worker kann aber
nur Spotify, iTunes, Deezer und MusicBrainz als `source_artist_id` verwenden.
Ein Discogs-only-Artist wird gewählt, `pick_source_artist_id()` liefert `None`,
und der Worker produziert periodische Fehler statt den Artist aus seiner
Arbeitsmenge auszuschließen.

### LV2-AUD-04 — P2 — Similar-Graph mischt unqualifizierte Fremd-IDs

`core/watchlist_scanner.py:473-478` (ebenso `api/chat.py`) reicht alle Werte aus
`external_ids` an den unqualifizierten `similar_artists.source_artist_id`-Join
weiter. Die Tabelle kann nur die vier vom Similar-Worker gewählten Quellen
repräsentieren. Eine numerische Discogs-/Amazon-ID, die zufällig einer
iTunes-/Deezer-ID eines anderen Artists entspricht, zieht damit dessen Kanten
und Empfehlungen herein. Vor dem Port wurden ausdrücklich nur die vier
unterstützten IDs verwendet.

### LV2-AUD-05 — P1 — Vorhandene Provider-ID blockiert Worker-Queues

`core/itunes_worker.py:271-274` kehrt bei vorhandener ID zurück, ohne den neuen
Attempt-Ledger auf `matched` zu setzen; Amazon und Deezer haben denselben Zweig.
Wenn eine ID aus Import, manuellem Match oder unvollständigem Backfill stammt,
wählt `next_pending()` dieselbe erste Entität in jeder Schleife erneut und die
restliche Queue verhungert. Andere bereits portierte Worker markieren genau
diesen Fall ausdrücklich als matched.

### LV2-AUD-06 — P1 — Title-only-Fallback verschmilzt verschiedene Tracks

`core/library2/media_server_sync.py:185-191` identifiziert einen Track nach
Album und Titel, ignoriert aber Disc-, Tracknummer und Recording-ID. Zwei
Tracks namens „Intro“ auf Disc 1 und Disc 2 werden zu einer Katalogzeile; der
zweite Upsert überschreibt Server-ID und Discnummer des ersten. Die gezielte
SQLite-Reproduktion hinterließ eine statt zwei Trackzeilen.

### LV2-AUD-07 — P2 — Medienserver-Umzug lässt den alten Pfad aktiv

`core/library2/media_server_sync.py:243-245` entzieht alten Dateizeilen nur das
Primary-Flag. Bei gleicher Server-ID und geändertem Pfad bleiben alter und neuer
Pfad aktiv; nur der neue ist primär. Das widerspricht dem dokumentierten
Scan-Vertrag, nach dem ein Umzug die Dateizeile ersetzt statt eine zweite aktive
danebenzustellen, und erzeugt falsche Disk-/Repair-Ergebnisse.

### LV2-AUD-08 — P2 — Primärartist-Wechsel hinterlässt alte Junction-Credits

`core/library2/media_server_sync.py:167-169` und der entsprechende Track-Upsert
fügen neue `role='primary'`-Zeilen nur hinzu. Korrigiert der Medienserver einen
Album- oder Trackartist, wird die skalare Referenz geändert, die alte
Primary-Junction bleibt aber bestehen. Nach der Reproduktion hatten alter und
neuer Artist gleichzeitig die Primary-Rolle.

### LV2-AUD-09 — P1 — Full Refresh löscht zusammengeführte Provider-/Importdaten

`database/music_database.py:6728-6742` behandelt `server_source` als exklusiven
Besitzstempel. Die neuen Upserts übernehmen jedoch vorhandene Provider-,
Download- und Importzeilen anhand ihrer Identität und stempeln genau diese
Zeilen. Ein Full Refresh oder die gleichartig implementierte Stale-/Removed-
Bereinigung löscht deshalb auch Provider-Diskografie, Enrichment und alternative
Soulseek-Dateien. Eine gemischte Reproduktion reduzierte Artist, owned Album,
Provider-only Album/Track und Soulseek-Datei auf null.

### LV2-AUD-10 — P2 — Disk-Usage zählt Provider-Diskografie als Deep-Scan-Lücke

`database/music_database.py:5666-5669` zählt jede `lib2_tracks`-Zeile in
`tracks_without_size`, auch Tracks aus Provider-Diskografien ohne Besitz oder
Datei. Dadurch zeigt die Stats-Seite nach Enrichment tausende vermeintlich noch
zu scannende Tracks. Die Zählmenge muss owned/file-basiert sein.

### LV2-AUD-11 — P2 — Library-Stats zählen den gesamten Provider-Katalog

`database/music_database.py:6685-6692` zählt beim Aufruf ohne Serverfilter alle
lib2-Artists, -Alben und -Tracks. Genau dieser Zweig speist
`/api/library/stats` und `/api/system/stats`, obwohl `get_statistics()` bereits
die korrekte owned-Semantik besitzt. Provider-only-Diskografie wird damit als
lokale Bibliothek gemeldet.

### LV2-AUD-12 — P2 — Listening-Name-Key wird mit `.lower()` nachgeschlagen

`core/listening_stats_worker.py:198-202` und die Top-Track-Anreicherung bauen
Lookup-Schlüssel mit `.lower()`, während die SQL-Ergebnisse den lib2-`name_key`
zurückgeben. Bei Namen wie `Straße` ist der gespeicherte Schlüssel `strasse`,
der Consumer sucht `straße`; History-Events bleiben ohne `lib2_track_id` und
Top-Track-Art/IDs werden nicht angereichert.

### LV2-AUD-13 — P1 — Öffentliche API projiziert keine Legacy-Feldnamen

`database/music_database.py:15666` und die Album-/Track-Geschwister geben rohe
lib2-Zeilen an unveränderte `api/serializers.py`-Serializer. Diese erwarten
unter anderem `thumb_url`, `spotify_artist_id`, `artist_id`,
`musicbrainz_release_id` und die bisherigen Statusfelder. Die API antwortet
deshalb erfolgreich, liefert vorhandene Bilder/Provider-IDs aber als `null`.
Die dokumentierte Kompatibilitätsprojektion fehlt.

### LV2-AUD-14 — P1 — `get_track_by_id` vermischt Katalog- und Server-ID

`database/music_database.py:7529` sucht mit `t.id = ? OR t.server_id = ?` ohne
Quelle oder Priorität. Bei numerischen Plex-IDs kann die Server-ID einer Zeile
dem lokalen lib2-PK einer anderen entsprechen; `fetchone()` liefert dann eine
beliebige Zeile. Zusätzlich wird selbst bei korrektem Match `t.id` als
`DatabaseTrack.id` zurückgegeben, obwohl mehrere Aufrufer diesen Wert als
Media-Server-ID verwenden.

### LV2-AUD-15 — P1 — Track-Suche gibt lib2-PKs an den Medienserver weiter

`database/music_database.py:7847-7852` selektiert `tracks.*`; `_rows_to_tracks`
setzt daraus `DatabaseTrack.id=t.id`. Sync-Service, Cache und Plex-`fetchItem`
behandeln diese ID weiterhin als RatingKey/Server-Song-ID. Sobald lib2-PK und
`server_id` auseinanderliegen, werden falsche Tracks gecacht, zu Playlists
hinzugefügt oder als 404 verworfen. Der dokumentierte Kompatibilitätsvertrag
sagt ausdrücklich, dass diese Aufrufer weiter über Server-IDs adressieren.

### LV2-AUD-16 — P2 — Playlist Explorer nennt Provider-Alben owned

`core/playlists/explorer.py:235-237` lädt für einen Artist alle lib2-Alben ohne
`origin='library'`. Provider-Diskografie setzt dadurch `owned=true`, zeigt den
Haken „Already in library“ und wird bei der Standardauswahl zum Wishlisten/
Download übersprungen, obwohl keine Datei existiert.

### LV2-AUD-17 — P1 — Duplicate-Merge verwirft strukturierte und Nutzerzustände

`database/music_database.py:6884-6890` kopiert `external_ids` und `genres` nur
als Ganzes, wenn der Keeper leer ist, und berücksichtigt `enrichment`,
`monitored`, Quality-Profile sowie `lib2_monitor_rules` gar nicht. Haben Keeper
und Donor komplementäre Provider-IDs oder liegt explizite Monitor-Absicht auf
dem Donor, gehen diese Daten beim anschließenden Delete irreversibel verloren;
die Reproduktion ließ zudem eine Monitor-Regel auf den gelöschten Artist zeigen.

### LV2-AUD-18 — P1 — Neue Track-Side-Effects laufen nach Scans nicht mehr

`database/music_database.py:7453-7455` gibt für Insert und Update nur `True`
zurück. Beide Scanpfade füllen `_new_track_ids` ausschließlich bei
`track_success == 'inserted'`; der Post-Scan-Reconciler läuft daher nie für neue
Tracks. Gleichzeitig verschwand der frühere `is_new_track`-History-Eintrag.
Selbst nach Wiederherstellung des Rückgabewerts muss der Reconciler noch von
seinen Legacy-`tracks`-Queries auf lib2-IDs/Dateizeilen portiert werden.

### LV2-AUD-19 — P2 — Gelöschte Dateizeilen lösen weiterhin Trackbesitz auf

`core/library2/track_files.py:216-229` filtert weder beim exakten noch beim
Basename-Match nach `file_state`. Existiert nur noch eine `deleted`-Zeile,
liefert `track_id_for_path()` trotzdem deren Track. Damit können stale manuelle
Matches und neue Download-Provenienz an eine historisch gelöschte Datei
gekoppelt werden; die gezielte Reproduktion gab die alte Track-ID zurück.

### LV2-AUD-20 — P2 — Mirrored-Playlist-Status zählt Provider-Tracks als lokal

`database/music_database.py:17382-17390` und der Einzelplaylist-Zweig matchen
nur Artist- und Trackname. Ein gleichnamiger Provider-only-Track genügt für
`in_library`, obwohl weder `origin='library'` noch eine owned-Datei vorliegt.
Die Sync-/Automation-Anzeige meldet dadurch zu hohe In-Library-Zahlen.

### LV2-AUD-21 — P1 — Löschen eines Pfads entfernt die ganze Trackidentität

`database/music_database.py:16150-16153` löscht `lib2_tracks`, sobald genau eine
zugehörige Dateizeile den Pfad trägt. Bei einem Track mit FLAC plus MP3/Keeper
entfernt das Löschen/Expiry einer Datei somit die gesamte Katalogzeile und
entkoppelt auch alle übrigen Dateien. In ADR-03 muss zuerst die konkrete
Dateizeile entfernt/markiert und der Track nur ohne verbleibenden Besitz
bereinigt werden.

### LV2-AUD-22 — P2 — Album-Issue-Snapshot fragt Legacy-Spalten aus lib2 ab

`web_server.py:12846` wechselte die Quelle zu `lib2_albums`, ließ im SELECT aber
`thumb_url`, `record_type`, `spotify_album_id` und
`musicbrainz_release_id` stehen. Jeder Album-Snapshot endet deshalb mit
`no such column: al.thumb_url` und enthält nur `_snapshot_error`, obwohl der
Status ausdrücklich vollständige Snapshot-Parität verspricht.

### LV2-AUD-23 — P2 — Navidrome-Pfadfallback streamt mit dem lokalen PK

`web_server.py:13119-13124` liest beim Pfadfallback `t.id` und baut daraus die
Subsonic-Stream-URL. Seit dem Rewrite ist das der lokale Katalog-PK; Navidrome
erwartet `t.server_id`. Wenn ein Caller keine ID mitsendet und der Musikpfad
nicht in SoulSync gemountet ist, streamt die Funktion den falschen Song oder
bekommt 404.

### LV2-AUD-24 — P2 — Your-Artists-Modal liest alte Schlüssel aus rohen lib2-Zeilen

`web_server.py:33174-33177` findet korrekt eine lib2-Zeile, mappt danach aber
weiter `thumb_url`, `spotify_artist_id`, `deezer_id`, `itunes_artist_id`,
`lastfm_*` usw. aus der rohen Zeile. Weil bei einem DB-Hit sofort zurückgegeben
wird, greifen Cache/API-Fallbacks nicht: Bild, Provider-Links und Statistiken im
Info-Modal bleiben leer, obwohl sie in `image_url`, `external_ids` oder
`enrichment` vorhanden sind.

### LV2-AUD-25 — P2 — Enhanced Search verliert die Server-ID owned Tracks

`core/search/library_check.py:50` projiziert `legacy_track_id` als
`track_id`, obwohl neu gescannte lib2-Tracks absichtlich keinen Legacy-Backfill
bekommen und ihre Medienserver-ID in `server_id` liegt. Bei solchen Tracks ist
der Owned-Badge zwar korrekt, der Playback-Row hat aber keine verwertbare ID;
insbesondere servergestütztes Playback bei nicht gemounteten Pfaden kann nicht
den richtigen Song adressieren.

## Ausgeführte Verifikation

- Repair-/native-P3-Auswahl: **264 passed**.
- Provider-Attempts und Worker-Queue: **59 passed**.
- Media-Server-Sync, Deep Scan und Media-Track-Upsert: **24 passed**.
- Listening-Stats: **27 passed**.
- Library-Listen, Disk Usage und Search: **33 passed**.
- Frontend-Querschnitt über 23 Artist-/Search-Dateien: **469 passed**.
- Frontend-Build: erfolgreich.
- Frontend-Check: **0 errors**, 377 bereits vorhandene Warnungen.
- Die vollständige Pytest-Suite blieb in dieser Umgebung hängen und wurde
  abgebrochen; der Audit beansprucht daher keinen grünen Full-Suite-Lauf.

Zusätzlich wurden die Kernfälle für Attempt-Paging, Similar-Ledger,
Discogs-only-Queue, Track-Kollision, Pfadumzug, stale Primary-Credits,
mixed-origin Full Refresh, Deleted-Path-Auflösung, Serializer-Projektion,
Statistik-Origin, ID-Kollision, Listening-Normalisierung und Album-Snapshot mit
kleinen temporären SQLite-Datenbanken direkt reproduziert.

## Gesamturteil

Der Rewrite ist noch nicht korrekt freigabefähig. Die höchste Priorität haben
destruktive Scan-/Merge-Pfade, Track-ID-Domänen, die Track-Upsert-Identität und
die blockierenden Provider-Queues; danach folgen Ownership-/API-Projektionen
und die übrigen UI-/Statistikregressionen.
