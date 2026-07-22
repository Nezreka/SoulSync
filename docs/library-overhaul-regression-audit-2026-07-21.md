# Library Overhaul – Regressions-Audit und Handoff

**Stand:** 2026-07-21
**Branch:** `library-overhaul`
**Geprüfter Branch-HEAD:** `4bd82830da9eb8e094cbb4accdb81070613e5dea`
**Geprüftes `upstream/dev`:** `eb958e10146372a885a6864638e08c88dcf3f64b`
**Merge-Base:** `663c0eea3754c99af78dab16f19d44f24bd5660c`
**Entscheidung:** **Noch nicht sicher für Merge oder Release.**

## Checkpoint-Status – 2026-07-21, 22:52 CEST

Die Implementierungsrunde wurde auf Wunsch des Benutzers an diesem Prüfpunkt
beendet. Basis vor dem abschließenden Handoff-Commit ist `270d8a03`; die bereits
vor dem Audit vorhandenen 23 Änderungen wurden separat als `f6a5f2fa`
gesichert. Danach wurden die unten beschriebenen Audit-Fixes und die
semantischen Upstream-Ports aufgebaut.

| Bereich | Stand am Checkpoint |
|---|---|
| C-01 | implementiert: unvollständige/Preview-Dateien dürfen Bestand nicht ersetzen |
| H-01 bis H-18 | implementiert, inklusive Job-ID-/Settings-Migration, Bootstrap-Fencing, Admin-/Profilgrenzen, Composite-Identitäten, Alias-Scope, ACL, Import-Review und zentralem nicht abschaltbarem V2-Cutover |
| M-01 bis M-06 | implementiert: Source-Fallback, zweiphasiger Album-Grab, retrybare Candidates, Disc-Nummer, Quality-Vererbung und Finding-Fingerprint |
| M-07 | filesystem-basierte Abdeckung für Fake-Lossless, Lossy-Converter, Tracknummer, ReplayGain und Corruption wiederhergestellt; Cutoff-Bewertung bleibt absichtlich katalogabhängig, Orphans tragen bis zum Import gemessene Qualitätsfakten |
| M-08 | `expired_download_cleaner` und `library_reorganize` wieder als sichtbare Jobs mit Review-/Apply-Pfad registriert; alte IDs sind wieder direkt verwendbar |
| M-09 bis M-15 | implementiert: albumgenauer Playlist-Scope, idempotenter Teilmigrations-Reconcile, V2-Artists in globaler Suche, UI-Rollback/Retry, zentraler Flag-Vertrag, wahrheitsgemäßes Langläufer-Polling und malformed Queue-ID-Guard |
| Upstream-Rückstand | die in Abschnitt 8 aufgelisteten Verhaltensfixes wurden semantisch integriert; der V2-Artist-Picker nutzt den ID-basierten `core.metadata.artist_image`-Stack |
| L-01/L-02 | im Handoff-Commit entfernt (`config.json.bak` und das eingecheckte MP3-Artefakt) |

Checkpoint-Prüfungen:

- Python: 132 Tests bestanden in 2,35 s (`search_orchestrator`, Queue-Status,
  Monitor-Reconcile, Artwork-Picker sowie ausgewählte Repair-Jobs).
- Frontend: 3 Vitest-Dateien / 11 Tests bestanden
  (`monitoring`, `mutation-boundaries`, `track-play-button`).
- Frühere gezielte Läufe während der Runde waren unter anderem für Bootstrap,
  Materialisierung, Monitoring, Queries, History, Delete/Wanted, Playback und
  die neuen Feature-Cutover-Verträge grün.

Noch nicht ausgeführt und deshalb Release-Blocker:

- vollständige Python-Test-Suite;
- vollständige Frontend-Test-Suite, Typecheck/Lint und Produktions-Build;
- realer End-to-End-Lauf mit externen Download-/Torrent-/Usenet-Clients;
- Migrations-/Restart-Soak-Test auf einer Kopie einer produktiven Datenbank.

Am Checkpoint gab es in den ausgeführten Tests keine bekannten Fehler. Die
Entscheidung bleibt trotzdem **noch nicht sicher für Merge oder Release**, bis
die vier vollständigen Prüfblöcke oben nachgeholt wurden.

Dieses Dokument ist als Arbeitsgrundlage für die nächste Session gedacht. Es
enthält die bestätigten Findings, Reproduktionsideen, vorgeschlagene Fixes,
Testlücken, den Integrationsrückstand gegenüber `upstream/dev` und eine
empfohlene Abarbeitungsreihenfolge.

## 0. Arbeitsregeln für die Folgesession

1. Vor jeder Änderung Branch-HEAD, `upstream/dev` und Merge-Base neu prüfen.
2. Den bestehenden Dirty-Worktree nicht überschreiben oder zurücksetzen.
3. Pro Finding zuerst einen Regressionstest oder ein isoliertes
   Reproduktionsszenario erstellen.
4. Migration verursachte Regressionen und post-divergence Upstream-Fixes
   weiterhin getrennt behandeln.
5. Einen Fix erst als abgeschlossen markieren, wenn Altverhalten,
   beabsichtigtes V2-Verhalten, Upgrade, Wiederholung und Restart geprüft sind.
6. Grüne bestehende Tests allein gelten nicht als Beleg: Mehrere Tests pinnen
   aktuell die fehlerhafte Semantik oder lassen die relevanten Grenzfälle aus.

### Vorhandener Dirty-Worktree zum Auditzeitpunkt

Die folgenden 23 Änderungen existierten bereits vor dem Audit und sind **keine
Audit-Fixes**:

```text
api/library_v2.py
core/downloads/monitor.py
core/downloads/source_policy.py
core/downloads/status.py
core/library2/discography.py
core/library2/monitor_sync.py
core/library2/profile_lookup.py
core/library2/queue_status.py
core/library2/sql_util.py
core/library2/track_files.py
core/library2/wanted.py
core/quality/lossless.py
core/repair_jobs/orphan_file_detector.py
core/repair_jobs/quality_info_backfill.py
core/torrent_clients/aria2.py
core/torrent_clients/deluge.py
core/torrent_clients/qbittorrent.py
core/torrent_clients/transmission.py
core/usenet_clients/nzbget.py
core/wishlist/processing.py
tests/quality/test_lossless.py
tests/test_infer_candidate_source.py
web_server.py
```

Vor der Abarbeitung jedes Findings prüfen, ob eine dieser Änderungen den
betroffenen Pfad inzwischen teilweise verändert. Sie nicht ungeprüft als Fix
werten.

## 1. Auditbasis und Methode

| Kennzahl | Wert |
|---|---:|
| Branch-Commits seit Merge-Base | 358 |
| Upstream-Commits seit Merge-Base | 218 |
| Branch-Diff | 409 Dateien |
| Insertions/Deletions | +111.493 / −15.629 |
| Neue/geänderte/gelöschte Dateien | 268 / 109 / 32 |
| Binärdateien | 1 |
| Auf beiden Seiten geänderte Pfade | 29 |

Verwendete Referenzachsen:

- **Overhaul-Regression:** Verhalten unterscheidet sich vom Zustand am
  Merge-Base und ist nicht durch Plan, ADR oder eindeutige Tests als gewollt
  belegt.
- **Upstream-Integrationsrückstand:** Das Verhalten wurde nach der Divergenz in
  `upstream/dev` korrigiert. Es wurde nicht durch V2 verursacht, fehlt aber im
  Branch und ist damit vor Release ebenfalls relevant.
- **Dokumentierte offene Lücke:** Kein beabsichtigtes Produktverhalten, sondern
  bereits dokumentiert und weiterhin nicht umgesetzt.

Vollständig gelesen wurden:

- `docs/library-v2.md`
- `docs/library-overhaul-branch-review-2026-07-19.md`
- `docs/library-v2-bug-tracker-2026-07-18.md`
- `docs/library-v2-deep-dive-findings-2026-07-16.md`
- `docs/library-v2-tool-integration-audit-2026-07-18.md`
- `docs/library-v2-ui-requirements.md`
- `docs/metadata-types-migration.md`
- `docs/download-engine-refactor-plan.md`
- `docs/api-response-shapes.md`
- `docs/quarantine-approve-orphan-bug-2026-07-20.md`
- `pr_description.md`

## 2. Beabsichtigtes Design und Kompatibilitätsvertrag

Library V2 soll einen DB-zentrierten Lidarr-artigen Katalog bereitstellen:

- stabile Artist-/Album-/Track-/File-Identitäten;
- Release Group, Edition und Recording als getrennte Konzepte;
- mehrere Dateien pro Track mit Primary-Datei und Lifecycle-State;
- `lib2_monitor_rules → lib2_wanted_tracks` als Track-Intent;
- Legacy-Wishlist/-Watchlist als abgeleitete Ausführungslisten;
- persistente Acquisition-, Grab-, Import- und History-Korrelation;
- restart-sichere Beobachtung externer Clients;
- Provider-Snapshots und getrennte User-Overrides;
- admin-only Library-V2-Intent.

Ausdrücklich erhalten bleiben müssen:

- `download_source.mode`, `hybrid_order`, Source-Priorität und Fallback;
- Quality-Profile, Cutoff, Upgrade-Policy und Fallback;
- Stability-, Integrity-, AcoustID-, Tagging- und Quarantäne-Semantik;
- Retry, Backoff, Next-Candidate und Wiederaufnahme;
- bestehende Wishlist-/Watchlist- und Playlist-Workflows;
- Profilisolation;
- Pfad-Mappings und Restart-Verhalten;
- bestehende Repair-Automationen und gespeicherte Settings;
- keine unerwartete Datei- oder Datenlöschung.

Dokumentationskonflikt: Der ursprüngliche Plan beschreibt V2 als opt-in und
parallel zur Legacy-Library. Commit `51516641` schaltet das Feature später
effektiv standardmäßig ein, weil der P3-Repair-Cutover sonst die Repair-Suite
still deaktiviert. UI, Settings-Vertrag und ursprüngliche Rollout-Doku sind
damit nicht konsistent.

## 3. Abarbeitungsübersicht

### Blocker-Reihenfolge

1. [ ] **C-01** Preview-/Replacement-Datenverlustschutz aus Upstream integrieren.
2. [ ] **H-01/H-02** Repair-Job-ID- und Automation-Semantik migrieren.
3. [ ] **H-03/H-04** Bootstrap-Fencing und Fresh-Install-Watermark beheben.
4. [ ] **H-05/H-06/H-07** Profil- und Wishlist-/Watchlist-Identitäten korrigieren.
5. [ ] **H-08 bis H-13** Repair-/Reorganize-Konsistenz herstellen.
6. [ ] **H-14 bis H-16** Playback-ID, Alias-Scope und Page-Rechte korrigieren.
7. [ ] **H-17** Acquisition-Review-UI fertigstellen.
8. [ ] **H-18** Feature-off-/Repair-Vertrag entscheiden und implementieren.
9. [ ] Medium Findings und Upstream-Integrationsrückstände abarbeiten.
10. [ ] Vollständige Clean-HEAD-, Upgrade-, Fresh-Install-, Restart- und E2E-
   Verifikation wiederholen.

## 4. Kritisches Finding

### C-01 – Preview/Null-Header kann vollständige Datei ersetzen

- **Status:** OFFEN
- **Klasse:** Upstream-Integrationsrückstand
- **Subsystem:** Import / Quality Guard / Replacement
- **Fehlender Commit:** `64736c1a`
- **Schweregrad:** kritisch

**Referenzverhalten:** `upstream/dev` dekodiert Kandidaten mit unzuverlässiger
Header-Dauer und verhindert, dass eine kürzere Datei eine längere bestehende
Datei ersetzt.

**Branchverhalten:** Gleichwertige Decoded-Duration- und
Never-Replace-With-Shorter-Guards fehlen.

**Nutzerszenario:** Ein Provider liefert eine circa 30 Sekunden lange Preview
mit Header-Dauer `0`. Der Branch kann sie als gültigen Ersatz akzeptieren und
damit einen vollständigen vorhandenen Song überschreiben.

**Reproduktion/Test zuerst:**

1. Vorhandene lange Datei und kürzeren Kandidaten anlegen.
2. Kandidat mit Header-Dauer `0`, aber dekodierbarer kurzer Dauer modellieren.
3. Replacement-Pfad bis zur finalen Entscheidung durchlaufen.
4. Erwartung: Kandidat wird abgelehnt; bestehende Datei bleibt unverändert.

**Fixrichtung:** `64736c1a` semantisch portieren, nicht blind cherry-picken;
Guard in die vom V2-Acquisition-Pfad tatsächlich benutzte gemeinsame Pipeline
integrieren.

## 5. Hohe Findings

### H-01 – Alte Repair-Job-IDs und Settings gehen still verloren

- **Status:** OFFEN
- **Subsystem:** Settings / Repair / Upgrade
- **Dateien:** `core/repair_jobs/__init__.py:91-127`,
  `core/repair_worker.py:233-247,467-498,894-910`
- **Commits:** `9ad1303c`, `f3abaf16`

`quality_upgrade_scanner`, `quality_upgrade` und `discography_backfill` stehen
in `RETIRED_JOB_IDS`, aber nicht in `JOB_ID_MIGRATIONS`. Gespeicherte
`enabled`-Werte, Intervalle, Filter und manuelle Aufrufer werden nicht
übernommen. Pending Findings dieser Jobs werden entfernt.

**Altverhalten:** Quality-Jobs waren review-/finding-basiert; Discography-
Backfill besaß eigene Review- und Filtersemantik.

**Aktuelles Verhalten:** Alte Keys bleiben wirkungslos; `run_job_now` mit alter
ID meldet Unknown Job; der neue Quality-Job besitzt `mode=automatic`.

**Repro:** Job-Konfiguration nur unter den drei Alt-IDs anlegen, Worker starten,
Registry und gespeicherte neue Settings prüfen. Erwartung nach Fix: Intervalle
und Aktivierung bleiben erhalten; Quality wird `review`.

**Fix:**

- stabile Alt-IDs migrieren;
- bei zwei vorhandenen Quality-Konfigurationen deterministische Merge-Regel;
- beide Alt-Quality-Jobs auf `mode=review`;
- Discography nicht blind auf action-only Refresh abbilden;
- Pending Findings erst nach verifiziertem Ersatz entfernen;
- Read-Aliases für alte manuelle/API-Aufrufer.

### H-02 – Bestehende Quality-Automation startet nun Downloads

- **Status:** OFFEN
- **Subsystem:** Automation / Quality / Wishlist
- **Dateien:** `core/automation/handlers/quality_scanner.py:18-35`,
  `core/repair_jobs/lib2_upgrade_scan.py:44-57,84-116`
- **Commits:** `9ad1303c`, `f3abaf16`

Die unverändert benannte Automation `start_quality_scan` startete vorher einen
Review-Job. Jetzt startet sie `quality_upgrade_scan`; bei fehlendem Setting ist
der Modus `automatic` und Kandidaten gehen unmittelbar in die Wishlist.

**Regressionstest:** Bestehende Automation ohne neue V2-Settings ausführen;
Finding muss entstehen, `mirror_projected_tracks_wishlist` darf nicht aufgerufen
werden.

**Fix:** Run-spezifischer Review-Override oder kompatibler separater Review-
Einstieg.

### H-03 – Bootstrap-Lease besitzt kein Owner-Fencing

- **Status:** OFFEN
- **Subsystem:** Settings / Migration / Concurrency / Restart
- **Dateien:** `core/library2/bootstrap.py:54-67,129-229`,
  `api/library_v2.py:4237-4311`, `web_server.py:31471-31495`
- **Commit:** `c8f33fac`

`heartbeat`, `mark_done` und `mark_failed` schreiben nur `WHERE id=1`. Nach
einem stale Reclaim kann der frühere Besitzer den Zustand des neuen Laufs
überschreiben. Der manuelle Import aktualisiert den persistenten Bootstrap-
Heartbeat nicht.

**Bereits reproduziert:** A claimt; Heartbeat wird künstlich stale; B reclaimt;
A ruft verspätet `mark_failed`; der Singleton wird trotz B auf `failed` gesetzt.

**Fix:** Owner-/Run-UUID in der Tabelle; alle Updates mit
`WHERE status='running' AND owner_token=?`; Rowcount prüfen; manuellen und
automatischen Import über denselben Token und Heartbeat führen.

### H-04 – Leerer Fresh-Install-Bootstrap wird dauerhaft `done`

- **Status:** OFFEN
- **Subsystem:** Fresh Install / Migration
- **Dateien:** `core/library2/bootstrap.py:249-274`,
  `web_server.py:31486-31493`
- **Commit:** `c8f33fac`

**Reproduziert:** Leere Legacy-Tabellen → Bootstrap erfolgreich mit allen
Zählern null → Status `done`; danach Legacy-Artist eingefügt → erneuter Lauf
liefert `already_done`; `lib2_artists` bleibt leer.

**Fix:** `done` an Quell-Snapshot/Watermark koppeln; leeren, noch nicht
initialisierten Bestand nicht endgültig abschließen; nach Media-Server-/Library-
Sync erneut reconciliieren.

### H-05 – Nicht-Admin-Profile mutieren globalen V2-/Admin-Intent

- **Status:** OFFEN
- **Subsystem:** Wishlist / Watchlist / Profile
- **Dateien:** `core/library2/materialize.py:169-203`,
  `core/wishlist/routes.py:697-705`,
  `core/watchlist_scanner.py:2387-2401`, `services/sync_service.py`
- **Commits:** `8f7989b`, `e539928c`

Profil-2-Wishlist- und Watchlist-Aktionen können V2 materialisieren, Profil 1
verwenden oder globale V2-Quality-Profile in eine Nicht-Admin-Wishlist
übernehmen. Das widerspricht ADR-01.

**Fix:** Zentraler Admin-Guard in `materialize_wishlist_intent`; jeder Caller
übergibt Actor-Profil; Nicht-Admin-Pfade führen keinen V2-Lookup und keine
V2-Profilübernahme aus.

**Tests:** Manuelle Wishlist-Aktion, Watchlist-Scan und Playlist-Sync jeweils
mit Profil 2; V2-Tabellen und Admin-Wishlist müssen unverändert bleiben.

### H-06 – Exaktes Composite-Remove demonitort mehrere Releases

- **Status:** OFFEN
- **Subsystem:** Wishlist / Monitoring / Identity
- **Dateien:** `core/wishlist/routes.py:43-64`, `api/wishlist.py:82-93`,
  `core/library2/monitor_sync.py:211-294,309+`
- **Commit:** `8c535385`

`track::album-a` wird für die Descriptor-Auswahl auf die Bare-ID reduziert.
Damit werden auch `track::album-b` und alle V2-Provider-ID-Treffer erfasst.

**Bereits reproduziert:** Request nur für `same::album-a` ergab zwei
Descriptoren und V2-IDs `[1,2]`.

**Fix:** Composite-Key erhalten; direkter `lib2_track_id`-/Stable-ID-Treffer ist
terminal; Provider-Fallback über Albumidentität disambiguieren oder bei
Mehrdeutigkeit abbrechen.

### H-07 – Watchlist-Artist-Match verliert Provider-Namespace

- **Status:** OFFEN
- **Subsystem:** Watchlist / Monitoring / Discography
- **Dateien:** `core/library2/monitor_sync.py:439-511,514-595`,
  `core/library2/importer.py:1903-1920`
- **Commits:** `9abd81f7`, `8c535385`, `9d9f8c7`

Der Watchlist-Snapshot besteht aus einem globalen Namens- und einem
unqualifizierten ID-Set. Gleiche Namen mit widersprechenden Spotify-IDs sowie
Deezer-/iTunes-Namespace-Kollisionen matchen falsch.

**Fix:** Identitäten pro Watchlist-Zeile und Provider speichern; namespace-
genauer Vergleich; kein Namensfallback bei widersprechender starker Identität;
gleiche Semantik in Import, Insert-Time, Reconcile und Remove.

### H-08 – Repair-Intent `remove`/`redownload` geht verloren

- **Status:** OFFEN
- **Subsystem:** Repair / Wanted / Files
- **Dateien:** `core/repair_worker.py:1217-1241,1399-1443,1561-1587`,
  `core/library2/maintenance_sync.py:41-51,439-500`
- **Commit:** `f3abaf16`

Native Handler löschen/markieren die Datei und lassen anschließend global
Wanted neu berechnen. Dadurch queued `redownload` bei unmonitored Tracks nicht,
während `remove only` bei monitored Tracks wieder queueen kann.

**Fix:** Expliziten Repair-Intent bis zum Wanted-/Wishlist-Write transportieren;
separate Tests für monitored/unmonitored × remove/redownload.

### H-09 – Finding wird trotz fehlgeschlagenem V2-Sync resolved

- **Status:** OFFEN
- **Subsystem:** Repair / Error Handling / Resume
- **Datei:** `core/repair_worker.py:1123-1152`
- **Commit:** `cbb21c65`

Nach erfolgreicher physischer Mutation wird ein Sync-Fehler nur im Resultat
vermerkt. `resolve_finding` läuft trotzdem. Datei und Katalog divergieren, ohne
Retry-Anker.

**Fix:** Finding bei Sync-Fehler pending/failed lassen oder persistente Outbox
mit Wiederaufnahme anlegen.

### H-10 – Tracknummer-Reparatur verwendet unvollständige File-Teilmenge

- **Status:** OFFEN
- **Subsystem:** Repair / Track Number / Multi-Disc
- **Datei:** `core/repair_jobs/native_p3.py:55-74`
- **Commit:** `f3abaf16`

Die kanonische Albumliste wird aus `active_file_subjects` aufgebaut. Fehlende
Tracks fehlen damit auch in Total-/Disc-Heuristik. Der alte Pfad verwendete die
vollständige Provider-Trackliste.

**Fix:** Vollständige Edition-/Provider-Trackliste als Soll verwenden; Files nur
als zu reparierende Subjects.

### H-11 – Native Tracknummer-Fixes lassen Legacy-Daten stale

- **Status:** OFFEN
- **Subsystem:** Repair / Legacy Compatibility
- **Datei:** `core/repair_worker.py:1781-1903`

Der V2-Zweig aktualisiert `lib2_tracks`/`lib2_track_files`, aber nicht die über
`legacy_track_id` verbundenen Legacy-Spalten. Legacy-UI, APIs und Jobs sehen
danach andere Nummern und Pfade.

**Fix:** Gemeinsamer dualer Maintenance-Write oder transaktionale Compatibility-
Outbox.

### H-12 – Multi-File-Findings deduplizieren verschiedene Dateien weg

- **Status:** OFFEN
- **Subsystem:** Repair / Multi-File / Idempotency
- **Dateien:** `core/library2/maintenance_subjects.py:60-155`,
  `core/repair_worker.py:933-962`

Alle aktiven Dateien werden gescannt, viele file-semantische Findings verwenden
aber dieselbe Track-Entity-ID. Der globale Dedup unterdrückt das Finding der
zweiten Datei; dismissed/resolved blockieren ebenfalls dauerhaft.

**Fix:** File-ID für file-semantische Jobs; Primary-Datei für track-semantische
Jobs; File-/Config-Fingerprint in Dedup.

### H-13 – Reorganize lässt V2-Dateipfad stale

- **Status:** OFFEN
- **Subsystem:** Reorganize / Import / Path Mapping
- **Dateien:** `core/reorganize_runner.py:70-107`,
  `tests/library2/test_reorganize_track_path_sync.py:67-107`
- **Commits:** Fix `d22fa501`, Regression `f3abaf16`

Nach dem Legacy-Path-Update kann `sync_repair_change` den alten V2-File-Row nicht
mehr zuverlässig auflösen. Der aktuelle Test erwartet ausdrücklich den stale
Pfad und pinnt damit die Regression.

**Fix:** V2-File-ID vor dem Legacy-Update über `legacy_track_id` auflösen und
beide Pfade atomar schreiben. Test wieder auf Pfadsynchronität umstellen.

### H-14 – V2-Track-ID wird als Legacy-/Server-ID interpretiert

- **Status:** OFFEN
- **Subsystem:** Playback / History / Radio
- **Dateien:** `webui/src/routes/library-v2/-ui/library-v2-page.tsx:6567-6585`,
  `core/library2/queries.py:874-903`, `webui/static/library.js:8550-8628`,
  `web_server.py:13590-13657`, `core/playback/play_log.py`
- **Commit:** `2ccb4501`

Der V2-Play-Button übergibt die lokale V2-ID im Legacy-Feld `id`. Bei
fehlgeschlagener oder mehrdeutiger Titel-/Artist-Auflösung kann der falsche
Legacy-Track abgespielt oder protokolliert werden.

**Fix:** Typisierte IDs (`lib2_track_id`, `legacy_track_id`,
`server_track_id`); V2-only-Dateien über V2-aware Resolver/Pfad abspielen.

### H-15 – Alias-Anzeige und Aktions-Scope widersprechen sich

- **Status:** OFFEN
- **Subsystem:** Alias / Files / Monitoring / Quality / History
- **Dateien:** `core/library2/queries.py:223-335`,
  `core/library2/wanted.py:230-245`, `api/library_v2.py:2422-2428`,
  `core/library2/file_delete.py:123-180`,
  `core/library2/history_feed.py:550-578`
- **Commits:** `67a2dac3`, `4d9d8371`, `fdb4bf91`, `65f91c5f`

Die Detailansicht zeigt den Katalog der Alias-Gruppe; Files, Monitoring,
Quality, Refresh, Wanted, Delete und History arbeiten häufig nur auf exakt
einer `artist_id`.

**Fix:** Zentralen Alias-Scope-Resolver für Anzeige und Aktionen verwenden;
bewusst engere Delete-Semantik in UI/Preview klar ausweisen.

### H-16 – `allowed_pages` wird für Library V2 umgangen

- **Status:** OFFEN
- **Subsystem:** Profile / Navigation / Authorization
- **Datei:** `webui/static/init.js:526-532,2760-2773`
- **Commits:** `9abd81f7`, `10bfdd64`

`library-v2` wird clientseitig immer erlaubt. Profile ohne bisheriges Library-
Recht erhalten Navigation und Read-Zugriff auf Pfade, Files und History.

**Fix:** V2 vom bestehenden `library`-Recht erben lassen oder neuen Page-Key
migrieren; sensitive Reads bei Bedarf serverseitig autorisieren.

### H-17 – Acquisition-Review-Backend hat keine UI

- **Status:** OFFEN, dokumentiert als `A10`/§83
- **Subsystem:** Acquisition / API / UI
- **Datei:** `api/library_v2.py:302-1181`

Mehrdeutige Bundle-Zuordnungen bleiben auf manuelle `assignments` warten. Das
Backend kann sie auflösen, das WebUI bietet keinen Aufrufer.

**Fix:** Review-UI für Assignment, Konflikte, Retry und Resume; Browser-E2E vom
mehrdeutigen Bundle bis zum abgeschlossenen Import.

### H-18 – `features.library_v2=false` deaktiviert Repair still

- **Status:** OFFEN / Produktentscheidung erforderlich
- **Subsystem:** Config / Repair / Rollout
- **Dateien:** `core/library2/maintenance_subjects.py:17-23`,
  `config/config.example.json:75-79`
- **Commits:** `f3abaf16`, `51516641`

Vor dem Cutover liefen Legacy-Jobs ohne V2. Jetzt liefern native Jobs bei
`false` leere Scopes und null Findings. Default-on kaschiert dies nur für
fehlende Keys.

**Fixoptionen:**

1. Legacy-Jobs solange erhalten, wie V2 deaktivierbar ist; oder
2. Feature als nicht mehr abschaltbaren Katalog-/Repair-Cutover behandeln und
   Migration/UI/Doku entsprechend ändern.

In beiden Fällen darf „deaktiviert“ nicht als „alles sauber“ erscheinen.

## 6. Mittlere Findings

### M-01 – Legacy-Hybrid-Fallback geht verloren

- `core/downloads/source_policy.py:104-118`, Commit `2a8c5d2d`
- Ungültige/alte Primary-/Secondary-Werte fielen zuvor auf Soulseek zurück.
  Die neue Registry-Filterung kann eine leere oder verkürzte Chain liefern.
- **Fix:** Alt-Konfigurationsfälle als Regressionstests; kompatible
  Normalisierung/Fallback.

### M-02 – Album-Grab kann teilweise starten und danach 503 melden

- `web_server.py:7094-7160`
- Tracks werden einzeln vorbereitet und sofort dispatcht. Scheitert ein späterer
  Track im Strict-Gate, meldet die Route „download not started“, obwohl frühere
  Tracks bereits laufen. Retry kann duplizieren.
- **Fix:** Zweiphasig: alle Tracks vorbereiten, dann geschlossen dispatchen.

### M-03 – Gate-Fehler verbraucht Candidate ohne Download

- `core/downloads/candidates.py:252-280,406-430`
- `used_sources` wird vor Acquisition-Preparation gesetzt. Temporärer Gate-
  Fehler macht den Kandidaten für spätere Retries unsichtbar.
- **Fix:** Erst nach erfolgreicher Preparation verbrauchen oder Zustand als
  retrybar persistieren.

### M-04 – Autolink speichert neue Disc-Nummer nicht

- `core/library2/autolink.py:244-314`
- `disc_number` wird beim Matching berücksichtigt, aber im INSERT weggelassen.
  Neue Disc-2-Tracks landen auf Disc 1.
- **Fix:** Spalte und Wert einfügen; Multi-Disc-Test mit gleichem Trackslot.

### M-05 – Gelöschtes explizites Quality-Profil pinnt Ersatzprofil

- `database/music_database.py:9525-9619`,
  `core/library2/profile_lookup.py:56-79`
- Commits `ec64f83c`, `d08a98f1`
- Profil-ID wird auf den damaligen Default umgebogen, aber
  `quality_profile_explicit=1` bleibt. Ein späterer Default-/Parent-Wechsel
  greift nicht.
- **Fix:** Explicit-Flag entfernen und Vererbung neu berechnen.

### M-06 – Dismissed Quality-Finding kehrt nach Profiländerung nie zurück

- `core/repair_worker.py:952-962`
- Dedup umfasst pending/resolved/dismissed ohne Profil-/Target-/File-
  Fingerprint.
- **Fix:** Konfigurations- und Primary-File-Fingerprint wie im alten Scanner.

### M-07 – Lose/unindexierte Files verlieren Repair-Funktionalität

- Der opt-in Orphan-Library-Scan kann Qualität anzeigen, ersetzt aber nicht
  Fake-Lossless-, Converter-, Tracknummer- und vollständige Quality-Workflows.
- **Fix:** Äquivalente filesystem-basierte Subjects oder ausdrücklich
  dokumentierte und akzeptierte Funktionsreduktion.

### M-08 – Retired Tools ohne gleichwertigen Ersatz

- `expired_download_cleaner` besitzt keinen 1:1-Nachfolger.
- `library_reorganize` erzeugt keine neuen Review-Findings mehr.
- Alte manuelle IDs sind unbrauchbar.
- **Fix:** Ersatzpfad oder sichtbare Migrationswarnung.

### M-09 – Playlist-Scope verliert Albumidentität

- `core/automation/handlers/_pipeline_shared.py:28-55`,
  `core/wishlist/processing.py:955-977`, Commit `9d9f8c7`
- Scope für Album A kann `track::album-b` mitdispatchen.
- **Fix:** Exakter Wishlist-Key oder Track+Album-ID; Bare-Fallback nur eindeutig.

### M-10 – Teilmigrierte Wishlist kann Reconcile-Churn erzeugen

- **Status:** HYPOTHESE
- `core/library2/monitor_sync.py:648-674`,
  `database/music_database.py:10176-10190`
- Alte Bare-ID ohne Album-ID/`source_info` wird nicht als gespiegelt erkannt;
  Reconcile kann Composite-Row anlegen, Duplicate-Cleanup wieder löschen und
  dies stündlich wiederholen.
- **Nächster Schritt:** E2E-Repro mit zwei Reconcile-Läufen und Row-/Outbox-
  Counts.

### M-11 – V2-native Artists fehlen in globaler Suche

- **Status:** OFFEN, dokumentiert als `LV2-014`
- Globale Enhanced Search liest nur Legacy-Artists.
- **Fix:** Legacy- und V2-Ergebnisse über stabile/providerbasierte Identität
  vereinigen und deduplizieren.

### M-12 – UI-Mutationen können still scheitern

- Alias-Unlink: keine sichtbare Fehlerbehandlung.
- `monitor_new_items`: optimistische UI ohne Rollback.
- Album-ReplayGain: fehlender Toast/Error-State nach Jobfehler.
- **Fix:** Einheitlicher Mutation-Error-State, Retry und Rollback; MSW-4xx-Tests.

### M-13 – Feature-Flag-Vertrag ist inkonsistent

- Default-on nur über verteilte Inline-Fallbacks.
- Kein editierbarer Settings-Key im UI/API, obwohl UI zum Aktivieren auffordert.
- Keine Feature-Environment-Variable.
- Strenges `is True`: Strings wie `"true"` und numerisches `1` deaktivieren.
- **Fix:** Kanonischer Default, zentrale Typnormalisierung, klare UI/Doku.

### M-14 – UI erfindet nach fünf Minuten terminalen Jobstatus

- **Status:** PLAUSIBEL, nicht mit realem Langläufer reproduziert
- `webui/src/routes/library-v2/-ui/library-v2-page.tsx:5033-5055`
- Nach 300 Polls wird lokal `running:false` erzeugt, obwohl Serverjob weiterlaufen
  kann.
- **Fix:** Detached/running darstellen; niemals clientseitig terminalen
  Serverzustand erfinden.

### M-15 – Queue-Status kann an malformed Album-ID scheitern

- Committed `HEAD` konvertiert `album_id` in `_record` ungeschützt per `int()`.
- Eine vorhandene uncommitted Änderung in `core/library2/queue_status.py` nutzt
  bereits `_safe_int`, ist aber nicht Teil des Branches.
- **Fix:** WIP prüfen, gezielten malformed-Context-Test ergänzen und sauber
  committen.

## 7. Niedrige Findings / Hygiene

### L-01 – Getracktes Config-Backup

`config/config.json.bak` wurde in `cc039249` eingecheckt. Die geprüften
Credential-Felder enthalten zwar nur Platzhalter, das Artefakt ist jedoch
redundant und schafft ein Muster für versehentliches Veröffentlichen lokaler
Configs.

### L-02 – 7,3-MB-MP3 im Branch

`Stream/d8ea218dc2fa431a/Stream/Justin Bieber - YUKON.mp3` wurde ebenfalls in
`cc039249` eingecheckt. Vor Merge Lizenz-/Distributionsthema klären und das
Artefakt in der Regel entfernen.

## 8. Upstream-Integrationsrückstand

Vor Release semantisch integrieren oder ausdrücklich ersetzen:

| Schwere | Commit | Verhalten |
|---|---|---|
| kritisch | `64736c1a` | Null-Header-/30s-Preview darf keine vollständige Datei ersetzen |
| hoch | `fffdc4ea`, `d5c4d920` | Force Download ersetzt wirklich; Replace-Intent ist eigener Batch-Key |
| hoch | `da1d3293` | Bestätigte manuelle Imports umgehen Quality-Veto |
| hoch | `cd2254bc` | Template-Änderungen führen tatsächlich zu Reorganize |
| hoch | `3d809c64` | Eigene Files nicht wegen Provider-Duration-Drift quarantänisieren |
| hoch | `9ddcbd3f` | Downloads-Folder-Bleed, späte Cancel-Landings und falsches Stuck verhindern |
| hoch | `decf8175` | Torrent-Save-Path über Inhalt statt nur Existenz verifizieren |
| hoch | `0800fdbb` | Minimum-Free-Disk-Guard |
| hoch | `b73bcc8e` | `.torrent` serverseitig laden; keine private Prowlarr-URL an Client |
| hoch | `4344fbc9` | Preview-Repair erkennt Null-Längen-Header |
| hoch | aktueller Artwork-Stack | V2 auf ID-aware `core.metadata.artist_image` statt alte/name-only Helper migrieren |
| mittel | `6365b6b1` | `.lrc`-Sidecars mitbewegen |
| mittel | `ebfd2883` | Multi-Artist-Singles unter Hauptartist ablegen |
| mittel | `f73c915e` | Exakte Albumidentifikation über IDs/ISRC-Konsens |
| mittel | `73a6940a` | Multi-Disc-Kollision und editierbare Disc-Nummer |
| mittel | `841c6c91` | Write Tags berührt nur betroffene Files |
| mittel | `c767fc15` | Corrupt-File-Detector findet Files zuverlässig |
| mittel | `eb958e10` | qBittorrent 5 stop/start |
| mittel | `a9efaed3`, `d5efb299` | Torrent-Seeding-Lifecycle und Enforcement-Modus |
| mittel | `7704bf32` | Playlist-Matches 0,70–0,79 gelten als matched |
| mittel | `92c9ec26` | Rescue für stale Plex-`ratingKey` |
| mittel | `f10ed9c7`, `6646861d` | Geplante Watchlist umfasst Labels; Label-Count bricht Scan nicht ab |

## 9. Kompatibilitätsmatrix

| Workflow | Referenz | Branch | Bewertung |
|---|---|---|---|
| Source-Priorität | Legacy-Mode und Hybrid-Fallback | gemeinsame Policy, Alt-Fallbacklücke | teilweise regressiv |
| Candidate-Retry | Candidate erst bei realem Versuch verbraucht | Gate-Fehler kann Candidate verbrauchen | Regression |
| Album-Grab | Erfolg oder klarer Teilstatus | Teilstart plus „nicht gestartet“ möglich | Regression |
| Restart-Retry | überwiegend In-Memory | persistentes Acquisition-Journal | Verbesserung, Monitor unvollständig verifiziert |
| Quarantäne | gemeinsame Pipeline | weitgehend wiederverwendet | grundsätzlich kompatibel |
| Preview-/Replacement | aktuelles Upstream schützt Bestand | zentrale Guards fehlen | kritisch |
| Tagging/Import | gemeinsame Pipeline | überwiegend wiederverwendet | mehrere Lücken |
| Reorganize | eine konsistente Pfadwahrheit | Legacy/V2 können divergieren | Regression |
| Repair-Scans | Legacy-/Filesystem-Subjects | V2-Subjects; lose Files nur teilweise | Regression |
| Repair-Aktionen | Remove/Redownload explizit | Intent geht verloren | Regression |
| Repair-Fehler | Finding bleibt Retry-Anker | trotz Sync-Fehler resolved | Regression |
| Scheduled Jobs | stabile IDs/Settings | IDs und Settings teilweise verloren | Regression |
| Quality-Automation | Review vor Wishlist | Default automatic | Regression |
| Exaktes Wishlist-Remove | exakt eine Composite-Zeile | mehrere Releases möglich | Regression |
| Nicht-Admin-Profile | getrennte Listen | globaler V2-/Admin-Intent möglich | Regression |
| Watchlist-Artist | konkrete Identität | Name-/ID-OR ohne Namespace | Regression |
| Monitoring-Outbox | nicht vorhanden | persistent/retrybar | Verbesserung |
| Alias-Ansicht | getrennte Artists | vereinigte Ansicht | beabsichtigt |
| Alias-Aktionen | Scope entspricht Ansicht | engerer Scope als Ansicht | Regression |
| Playback-ID | Legacy-/Server-ID | V2-ID kann Legacy-ID werden | Regression |
| Page-Rechte | `allowed_pages` gilt | V2 immer sichtbar | Regression |
| Fresh Install | späterer Scan baut Library auf | leerer Bootstrap kann dauerhaft `done` sein | Regression |
| Existing Upgrade | Tools/Settings bleiben | Migration unvollständig | nicht sicher |
| Feature off | Legacy-Tools funktionieren | Repair-Suite silent no-op | Regression |
| Acquisition Review | manuelle Auflösung vorhanden | Backend ohne UI | dokumentierter Blocker |

## 10. Diff-/Datei-Coverage

Alle 409 geänderten Dateien wurden einem primären Block zugeordnet.

| Primärer Block | Dateien |
|---|---:|
| Download-Pipeline/Acquisition | 89 |
| Post-Processing/Import | 40 |
| Quality/Repair | 57 |
| Wishlist/Watchlist/Monitoring | 29 |
| Settings/Migration | 8 |
| Metadata/Search | 8 |
| Library-V2-API/UI/Querschnitt | 120 |
| Dokumentation/Build/Fixtures | 15 |
| Runtime/sonstige Koordination | 43 |
| **Gesamt** | **409** |

### Vollständiges Pfadgruppen-Manifest

| Pfadgruppe | Dateien |
|---|---:|
| `.github/` | 1 |
| `.gitignore` | 1 |
| `Stream/` | 1 |
| `api/` | 4 |
| `config/` | 3 |
| `core/` | 160 |
| `database/` | 1 |
| `docs/` | 9 |
| `pyproject.toml` | 1 |
| `scripts/` | 1 |
| `services/` | 1 |
| `tests/` | 176 |
| `utils/` | 1 |
| `web_server.py` | 1 |
| `webui/` | 48 |
| **Gesamt** | **409** |

Core-Auflösung:

| Core-Gruppe | Dateien |
|---|---:|
| `core/acquisition/` | 30 |
| `core/automation/` | 4 |
| `core/connection_test.py` | 1 |
| `core/discovery/` | 1 |
| `core/download_engine/` | 1 |
| `core/download_orchestrator.py` | 1 |
| `core/download_plugins/` | 4 |
| `core/downloads/` | 6 |
| `core/enrichment/` | 1 |
| `core/imports/` | 5 |
| `core/library/` | 4 |
| `core/library2/` | 59 |
| `core/metadata/` | 2 |
| `core/reorganize_runner.py` | 1 |
| `core/repair_jobs/` | 33 |
| `core/repair_worker.py` | 1 |
| `core/tag_writer.py` | 1 |
| `core/usenet_clients/` | 1 |
| `core/watchlist_scanner.py` | 1 |
| `core/wishlist/` | 3 |
| **Gesamt** | **160** |

Test-Auflösung:

| Testgruppe | Dateien |
|---|---:|
| `tests/` direkt | 39 |
| `tests/acquisition/` | 30 |
| `tests/automation/` | 3 |
| `tests/blocklist/` | 1 |
| `tests/discovery/` | 1 |
| `tests/downloads/` | 3 |
| `tests/imports/` | 4 |
| `tests/library/` | 2 |
| `tests/library2/` | 68 |
| `tests/matching/` | 1 |
| `tests/metadata/` | 3 |
| `tests/quality/` | 3 |
| `tests/repair/` | 3 |
| `tests/repair_jobs/` | 7 |
| `tests/utils/` | 1 |
| `tests/wishlist/` | 7 |
| **Gesamt** | **176** |

WebUI: 2 Root-Dateien, 40 Dateien unter `webui/src`, 5 unter
`webui/static`, 1 E2E-Datei.

## 11. Test- und Verifikationsstand

Ausgeführt:

- Collection: 10.541 Tests ausgewählt, 2 deselected, keine Collection-Fehler.
- Post-Processing/Import: 786 passed.
- Quality/Repair: 60 passed.
- Settings/Migration: 77 passed, 2 bestehende Async-Warnings.
- Wishlist/Monitoring: 120 passed; zusätzlicher Scope 184 passed.
- `tests/library2`: 918 passed.
- Library-V2-/Shell-Frontendtests: 156 passed.
- Search-/Playback-/Alias-/History-/API-Scope: 263 passed.
- Bootstrap/Source-Policy/Reorganize-Spotcheck: 17 passed.
- Download-Policy/Gate/Retry: 4 + 2 + 19 passed.

Einschränkungen:

- Zahlen verschiedener Scopes überlappen; nicht summieren.
- `tests/acquisition/test_client_monitor.py` hing reproduzierbar in einem
  Runtime-/DB-Connection-Test. Bis dahin waren 10/20 Tests durchgelaufen.
- Kein belastbarer vollständiger Clean-HEAD-Gesamtlauf.
- Tests liefen im Dirty-Worktree; relevante WIP-Dateien jeweils berücksichtigen.
- Keine Live-Soulseek-, SABnzbd-, NZBGet-, Torrent-, Provider- oder Navidrome-
  E2E-Verifikation.
- Keine produktive DB wurde verändert.

## 12. Verbleibende Unsicherheiten

- Restart mitten in realem Soulseek-/Usenet-/Torrent-Transfer.
- Externe Client-Adoption nach Prozessabsturz.
- Echte Quarantäne-Approval-Kette mit AcoustID und anschließendem Import.
- Docker-/Windows-Pfad-Mappings mit produktiven Mounts.
- Physische Delete-/Recycle-/Recovery-Flows.
- Sehr großer Bootstrap über die zehnminütige Lease-Grenze.
- Teilmigrierte reale Wishlist-/Watchlist-Daten.
- V2-only-Playback gegen Navidrome.
- Mehrdeutiger Bundle-Import als vollständiges Browser-E2E.
- Jobs über fünf Minuten.
- M-10 Reconcile-Churn ist bislang nur statisch belegt.

## 13. Upgrade-Einschätzung

### Bestehende Installation

Nicht sicher. Fehlender Feature-Key aktiviert V2 automatisch; alte Job-Settings
werden teilweise ignoriert; eine Quality-Automation kann Downloads starten;
Page-Rechte und Nicht-Admin-Grenzen ändern ihre Bedeutung; Repair-/Reorganize-
Writes können Legacy und V2 auseinanderlaufen lassen.

### Frische Installation

Nicht sicher. Der Bootstrap kann vor dem ersten Library-Scan einen leeren Stand
dauerhaft als erledigt markieren. Default und Aktivierungsweg sind zwischen
Backend, UI und Doku inkonsistent.

### Teilmigrierte Installation

Besonders riskant. Bare Wishlist-IDs, fehlende `source_info`-Daten und alte
Job-Keys besitzen keine vollständige Übergangsmigration. Failure-Injection und
Resume für teilweise angelegte Schemas fehlen.

## 14. PR-Aufteilung

### Sauber extrahierbare allgemeine PRs

| Zweck | Commits | Hauptdateien | Hinweise |
|---|---|---|---|
| Opaque Candidate-Tokens | `62a8848d`, `4fc1167a` | Candidate Store, Torrent/Usenet | Guter Security-PR; Race-Follow-up mitnehmen |
| Bundle-Completion | `ba4e8569`, `c38e3912` | Album Bundle, Torrent/Usenet | Remote-Mount-/Empty-Dir-Follow-up zwingend |
| Python-3.14 Async-Bridge | `7bdd5fdc`, `d5c982e0` | `utils/async_helpers.py` | Beide gemeinsam, sonst Concurrency-Regression |
| Tracknummer-Fallback | `dbb3b84e`, `a9dc169d` | Import-Pipeline/Resolver | Follow-up begrenzt auf Album-Bundles |
| Simple-Download-Tags | `d8f51a0f` | Import-Pipeline | Weitgehend V2-unabhängig |
| SAB-Kategorieprüfung | `815253e8`, `21a95e12` | Connection Test/SAB | Trim-Follow-up mitnehmen |
| Retry-Budgets | `76085876` | Download Monitor | Kleiner unabhängiger PR |
| Retag Date/Genre | Backend aus `c9a7df90` + `c3e7c7ed` | `tag_writer.py` | V2-UI-Hunk abtrennen |
| Progress Clamp | Backend aus `dcee311c` | Automation Progress | V2-UI-Hunk abtrennen |

`ec64f83c`/`d08a98f1` zur Quality-Profil-Löschung erst nach Fix von M-05
separat extrahieren.

### Späterer abhängiger Repair-/Rollout-PR

Zusammengehöriger Stack:

```text
cbb21c65
fd0ff252
3df1bf25
9ad1303c
f3abaf16
c8f33fac
5bbfcfa1
51516641
```

einschließlich späterer Korrekturen wie `b09e40c8`, `737e76a3` und
`e66644e6`.

Dieser Stack darf erst nach Katalogfundation, Alt-ID-Migration,
Bootstrap-Fencing, korrekter Repair-Intent-Semantik und vollständigen
Upgrade-/Restart-Tests deployt werden.

### Nicht sinnvoll standalone

- Schema ohne Importer und Queries;
- API ohne Datenmodell;
- UI ohne API-/Schema-Vertrag;
- Wanted-Projektion ohne Outbox und Reverse-Sync;
- Acquisition ohne Review-UI und gemeinsame Import-Pipeline;
- Job-Retirements ohne Settings-Migration und Rollout-Entscheidung.

Empfohlene gestapelte Reihenfolge:

1. allgemeine, V2-unabhängige Hardening-PRs;
2. opt-in Katalogfundation;
3. Outbox, Monitor-Regeln und Wanted-Projektion;
4. Acquisition inklusive Review-UI;
5. Legacy-Caller und Reverse-Sync;
6. zuletzt Repair-Cutover, Bootstrap und Default-on.

## 15. Release-Gate

Vor einem erneuten Release-Audit müssen mindestens erfüllt sein:

- [ ] C-01 integriert und mit realem Audio-Replacement-Test belegt.
- [ ] H-01/H-02 vollständig migriert.
- [ ] H-03/H-04 mit Concurrency-, Restart- und Fresh-Install-Tests geschlossen.
- [ ] H-05/H-06/H-07 mit Multi-Profil- und Mehrdeutigkeits-Tests geschlossen.
- [ ] H-08 bis H-13 mit dualer Legacy-/V2-Konsistenz geschlossen.
- [ ] Playback-ID und Alias-Scope korrigiert.
- [ ] `allowed_pages`-Migration entschieden und getestet.
- [ ] Acquisition-Review im UI vollständig nutzbar.
- [ ] Feature-off-/Default-on-Vertrag eindeutig entschieden.
- [ ] Relevante Upstream-Fixes integriert.
- [ ] Clean-HEAD Unit-/Integration-/Frontend-Suiten grün.
- [ ] Upgrade-, Fresh-Install-, Repeat-, Partial-Migration- und Restart-Szenarien
  erfolgreich.

**Abschließende Einstufung zum Stand dieses Dokuments:**

> **Noch nicht sicher für Merge oder Release.**
