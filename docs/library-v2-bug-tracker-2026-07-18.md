# Library v2 – Bug- und Integritäts-Tracker

Stand: 19. Juli 2026
Status dieses Dokuments: aktive Arbeitsgrundlage; ergänzt den Library-v2-Plan und ersetzt dort keine ADRs.

## 1. Zweck und Quellen

Dieser Tracker bündelt die aktuell bekannten Fehler an den Grenzen zwischen Library v2, Wishlist, Download-Runtime, Acquisition, Import-Pipeline, Quarantäne, Review Queue und Dateisystem. Er basiert auf:

- `docs/library-v2.md`
- `docs/library-v2-deep-dive-findings-2026-07-16.md`
- `docs/download-engine-refactor-plan.md`
- `/home/cyran/Desktop/findings.md`
- `/home/cyran/Desktop/odetari_analysis_proposal.md`
- den vom Benutzer am 18. Juli 2026 beschriebenen Reproduktionen
- einer erneuten Codeanalyse der betroffenen API-, Runtime-, Import-, Scan-, Repair- und UI-Statuspfade
- einer ausschließlich lesenden Stichprobe aus `database/music_library.db`

Wichtig: „implementiert“ bedeutet in diesem Dokument „im aktuellen Worktree geändert und durch die angegebenen Tests abgesichert“. Es bedeutet noch nicht, dass der Fix deployed wurde oder ein realer End-to-End-Lauf gegen Soulseek und einen externen Media Server erfolgt ist.

## 2. Verbindliche Invarianten

Die folgenden Regeln sind die Grundlage für Priorität und Abnahme:

1. Ein Track darf nur dann dauerhaft in der Wishlist stehen, wenn die Wanted-Projektion ihn als gewollt ausweist. Im Normalfall heißt das: Missing beziehungsweise Cutoff Unmet und effektiv monitored.
2. Ein manueller Klick auf „Automatic Search“ ist ein einmaliger Suchauftrag. Er darf weder `monitored` verändern noch den Track allein wegen des Klicks in der Wishlist persistieren.
3. Eine track-spezifische Automatic Search darf genau diesen Track durch dieselbe Candidate-, Retry-, Quality-, AcoustID-, Quarantine- und Import-Pipeline schicken, die auch Wishlist-Downloads verwenden.
4. Ein terminaler Task (`completed`, `failed`, `cancelled`, `not_found`, `skipped`, `already_owned`) darf niemals durch einen älteren Runtime-Kontext wieder als `queued`, `searching` oder `downloading` erscheinen.
5. Ein Import gilt erst als abgeschlossen, wenn Dateisystem, Library-v2-Dateizeile, Legacy-/Media-Server-Sicht, Runtime-Task und persistenter Acquisition-Zustand entweder erfolgreich synchronisiert oder explizit als fehlgeschlagen markiert wurden.
6. Repair-, Scan- und Review-Aktionen dürfen Library v2 nicht als bloßen Cache behandeln. `lib2_track_files` ist ein eigener autoritativer Dateiindex.
7. Ein physisch verschobenes File darf bei einem späteren Post-Processing-Fehler nicht als unsichtbarer Orphan zurückbleiben.
8. Ein fehlender Pfad wird nur bei gesundem Storage-Root bestätigt. Der Zwischenzustand `missing_suspected` muss sichtbar sein und darf nicht fälschlich als sicher `present` erscheinen.
9. Provider-Identitäten sind stärker als Parser-Fragmente. Zwei Artist-Zeilen mit derselben konfliktfreien Provider-ID müssen zusammengeführt werden können, auch wenn ihre normalisierten Namen verschieden sind.
10. „Run Pipeline“ auf einer Playlist darf in der Wishlist-Phase ausschließlich Tracks dieser Playlist verarbeiten; ein fehlender Scope darf niemals auf die globale Queue zurückfallen.
11. Ein neu erzeugter Library-v2-Artist darf nur dann `monitored=1` erhalten, wenn eine echte Watchlist-Zeile oder eine explizite Library-v2-Regel dies begründet.

## 3. Priorisierte Übersicht

| ID | Prio | Status | Kurzbeschreibung |
|---|---:|---|---|
| LV2-001 | P0 | IMPLEMENTIERT | Track Automatic Search persistierte unmonitored Tracks in der Wishlist |
| LV2-002 | P0 | IMPLEMENTIERT | Failed/Completed wurde durch stale Runtime-Kontext wieder als `queued` angezeigt |
| LV2-003 | P0 | IMPLEMENTIERT | Import-Wrapper verlor Completion-, Scan-, Automation- und Repair-Callbacks |
| LV2-004 | P0 | IMPLEMENTIERT | Fehler nach erfolgreichem File-Move erzeugte einen echten DB-Orphan |
| LV2-005 | P0 | IMPLEMENTIERT | Quarantäne-Approve ohne ursprünglichen Task löste keinen Media-Scan aus |
| LV2-006 | P0 | IMPLEMENTIERT | Persistente Acquisition-Grabs können tagelang auf `legacy_dispatched` hängen |
| LV2-007 | P1 | IMPLEMENTIERT | Orphan Detector kannte nur Legacy-`tracks.file_path` |
| LV2-008 | P1 | IMPLEMENTIERT | Human Approve synchronisierte `lib2_track_files.verification_status` nicht |
| LV2-009 | P1 | IMPLEMENTIERT | Recover to Staging bewegt nur Dateien; persistente Lifecycle-Zustände bleiben unklar |
| LV2-010 | P1 | IMPLEMENTIERT | Erster physischer Miss wird im UI als `present` verborgen |
| LV2-011 | P1 | IMPLEMENTIERT | `w/` erzeugte Artist-Fragmente wie `Odetari w` |
| LV2-012 | P1 | IMPLEMENTIERT, DATENREPARATUR AUSSTEHEND | Dedup war name-only und ignorierte gemeinsame Provider-ID |
| LV2-013 | P1 | IMPLEMENTIERT (READ-ONLY) | End-to-End-Reconciler für Disk, Legacy, Library v2, Runtime und Acquisition fehlt |
| LV2-014 | P2 | OFFEN | Enhanced-Search „In Your Library"-Erkennung sieht rein v2-native Artists nicht |
| LV2-015 | P0 | IMPLEMENTIERT | Playlist-„Run Pipeline“ verarbeitete in Phase 4 die globale Wishlist |
| LV2-016 | P0 | IMPLEMENTIERT + REPAIR | Neue Artists starteten per Schema-Default fälschlich als monitored |

## 4. Detailbefunde und Abnahme

### LV2-001 – Automatic Search darf keine Wishlist-Zeile erzeugen

Symptom:

- Automatic Search auf einem nicht überwachten Track schlug fehl.
- Der Track erschien danach trotzdem in der Wishlist.
- Das UI zeigte weiter eine laufende Download-Aktivität.

Root Cause:

- `api/library_v2.py` rief im direkten Track-Pfad `mirror_tracks_wishlist(..., monitored=True, user_initiated=True)` auf.
- Der Klick wurde damit fälschlich zu persistentem Monitoring-/Wishlist-State.
- `core/downloads/wishlist_failed.py` fügte einen endgültig fehlgeschlagenen Task erneut zur Wishlist hinzu, ohne zwischen einer persistenten Wishlist-Ausführung und einer transienten Einzelsuche zu unterscheiden.

Implementierter Fix:

- `core/library2/wishlist_mirror.py` baut mit `track_direct_download_payload()` einen serverseitig aufgelösten, transienten Payload inklusive `lib2_track_id`, `lib2_album_id`, Quality Profile, Artist- und Album-Kontext.
- `core/wishlist/processing.py` startet mit `start_direct_track_download_batch()` einen Batch `library_v2_search`, ohne die Wishlist zu lesen oder zu schreiben.
- Der Batch trägt `requeue_failed_to_wishlist=False` und `transient_search=True`.
- `core/downloads/wishlist_failed.py` respektiert dieses Flag: der Fail wird abgeschlossen und gezählt, aber nicht persistiert.
- Artist-/Album-Suchen behalten den Wanted-Projektionspfad und spiegeln nur effektiv gewollte Tracks.

Abnahme:

- Unmonitored Missing Track suchen: genau ein transienter Batch, keine Wishlist-Zeile, `monitored` bleibt unverändert.
- Fehler provozieren: terminaler Fail, keine spätere Wishlist-Zeile.
- Erfolgreich suchen: File läuft durch Quality/AcoustID/Import und wird direkt mit dem ursprünglichen Library-v2-Track verknüpft.
- Bereits erfülltes Quality Profile: kein unnötiger Download.

Regressionstests:

- `tests/library2/test_scoped_search_endpoint.py`
- `tests/wishlist/test_manual_download.py`
- `tests/wishlist/test_transient_search_failure.py`

### LV2-002 – Terminale Tasks dürfen nicht als Queued wiederauferstehen

Symptom:

- Ein manueller Grab war erfolgreich, File und AcoustID waren korrekt, Library v2 zeigte das File, aber der Track blieb dauerhaft `Queued`.
- Derselbe Mechanismus konnte einen fehlgeschlagenen Automatic Search als weiterhin aktiv zeigen.

Root Cause:

- `core/library2/queue_status.py` blendete terminale Einträge aus `download_tasks` korrekt aus.
- Danach wurde derselbe Track aber aus einem älteren `matched_downloads_context` erneut als `queued` eingefügt.
- Der weniger präzise Fallback gewann dadurch gegen einen bereits bekannten terminalen Task.

Implementierter Fix:

- Terminal beobachtete Library-v2-Track-IDs werden gesammelt.
- Für diese IDs wird ein stale `matched_downloads_context` vollständig unterdrückt.
- Das gilt für Success und alle terminalen Failure-/Cancel-Varianten.

Abnahme:

- `completed`, `failed`, `cancelled`, `not_found`, `skipped` und `already_owned` plus stale Shadow Context ergeben keinen aktiven Queue-Status.
- Ein wirklich aktiver präziser Task bleibt sichtbar und behält Vorrang.

Regressionstest:

- `tests/library2/test_queue_status.py`

### LV2-003 – Import-Runtime war ohne zentrale Abschluss-Hooks gebaut

Symptom:

- Physisch erfolgreiche Imports konnten im Batch-/Task-Status hängenbleiben.
- Media Scan, Automation und Repair-Folgeaktionen liefen je nach Einstiegspfad nicht.

Root Cause:

- Die beiden Web-Wrapper `_post_process_matched_download_with_verification` und `_post_process_matched_download` bauten ihre Runtime ohne `on_download_completed`, `automation_engine`, `web_scan_manager` und `repair_worker`.
- Die Core-Pipeline unterstützt diese Abhängigkeiten, erhielt sie an dieser zentralen Web-Grenze aber nicht.

Implementierter Fix:

- Beide Wrapper injizieren jetzt alle vier Runtime-Hooks.
- Dadurch erreicht ein echter Pipeline-Abschluss wieder Batch Accounting, Scan-Koordination, Automation und Repair-Registrierung.

Abnahme:

- Erfolgreicher manueller Grab wird terminal `completed` und verschwindet aus der aktiven Queue.
- Fehlgeschlagener Grab wird terminal `failed` und bleibt nicht `downloading`.
- Externe Media-Server-Konfiguration erhält genau einen koaleszierten Scan-Request.
- Standalone schreibt den Track in die lokale Library.

### LV2-004 – Post-Move-Exception erzeugte echte Orphans

Symptom aus `findings.md`:

- Die Quelldatei wurde bereits erfolgreich an den finalen Ort verschoben.
- Eine spätere Exception brach Side Effects und DB-Verknüpfung ab.
- Weil die Quelle nicht mehr existierte, wurde nicht erneut versucht; das reale Zielfile blieb ohne Library-Zuordnung.

Root Cause:

- Der äußere Exception-Pfad in `core/imports/pipeline.py` kannte nur „Quelle existiert, retry“ oder „Quelle weg, nicht retry“.
- Er prüfte nicht, ob `_final_processed_path` real auf Disk existiert und deshalb ein Recovery statt eines normalen Retries nötig ist.

Implementierter Fix:

- `_recover_moved_file_bookkeeping()` greift ausschließlich bei einem real existierenden Ziel.
- Es reconciled best-effort und idempotent:
  - Standalone-/Legacy-Library per finalem Pfad,
  - Library-v2-Autolink,
  - native Acquisition Completion,
  - korrelierten manuellen/scheduled Grab.
- Append-only History und Provenance werden absichtlich nicht blind wiederholt, weil die Exception auch nach deren erstem Insert eingetreten sein kann.

Abnahme:

- Künstliche Exception direkt nach dem Move: Zielfile bleibt vorhanden, mindestens ein autoritativer Dateiindex kennt es, persistenter Grab wird abgeschlossen.
- Fehlendes Ziel: keine falsche Success-Reconciliation.

Regressionstest:

- `tests/imports/test_import_pipeline.py`

### LV2-005 – Quarantäne Human Approve ohne Live-Task

Symptom:

- Ein Quarantäne-Eintrag kann einen Server-Neustart überleben, sein ursprünglicher In-Memory-Task nicht.
- Human Approve reimportierte das File, löste aber ohne Task-/Batch-Callback keinen Media Scan aus.

Implementierter Fix:

- Der tasklose Manager-Approve-Pfad prüft nach der Reprocess-Pipeline:
  - keine verbleibende Import-Rejection,
  - real existierender finaler Pfad,
  - aktivierte Scan-Automation.
- Danach wird ein koaleszierter `web_scan_manager.request_scan()` ausgelöst.
- Die zentral korrigierte Import-Runtime stellt zusätzlich Library-/Acquisition-Side-Effects bereit.

Noch zu prüfen:

- End-to-End mit einem vor Neustart erzeugten Quarantäne-Sidecar.
- Verhalten bei deaktiviertem `batch_complete -> scan_library` muss unverändert bleiben.

### LV2-006 – Persistente Acquisition-Zustände ohne zeitnahe Reconciliation

Read-only-DB-Snapshot am 18. Juli 2026:

| Tabelle | Terminal | Nicht terminal |
|---|---:|---:|
| `acquisition_requests` | 131 `completed` | 102 `grabbing` |
| `acquisition_grabs` | 131 `completed` | 102 `downloading` |

Die offenen Rows liegen zwischen 14. und 17. Juli 2026 und tragen überwiegend `last_client_state='legacy_dispatched'`. Der existierende `fail_stale_correlated_grabs()`-Sweep greift für manuelle und scheduled Legacy-Korrelationen erst nach sieben Tagen. Das verhindert zwar „für immer“, ist aber kein zeitnaher Abgleich mit dem wirklichen Runtime-, Client- oder Dateistatus.

Risiko:

- Neustarts und verlorene Completion-Callbacks hinterlassen tagelang aktive persistente Zustände.
- Eine pauschale Verkürzung der TTL könnte legitime langsame Transfers fälschlich failen.

Implementierter Fix:

- `core/acquisition/reconciler.py` gleicht offene, request-gebundene Grabs
  evidenzbasiert mit Runtime-Tasks, Post-Processing-Kontexten, externem
  Client-Snapshot, Quarantäne-Sidecars, Acquisition-Imports und realen,
  gemappten Index-Pfaden ab.
- Eine Completion wird nur übernommen, wenn das finale File real und in
  Legacy oder Library v2 aktiv indexiert ist. Auch ein bereits terminaler
  Acquisition-Import muss reale, indexierte `processed.final_path`-Evidenz
  besitzen.
- Eindeutige Client-/Runtime-Fails und Cancels werden als kleine,
  idempotente Transitionen übernommen. Evidenzlose Altzustände werden erst
  nach konfigurierbarer TTL (Default 24 Stunden, Minimum eine Stunde) als
  Runtime-Failure geschlossen und nie blocklistet.
- Jede Transition läuft in einem Savepoint; ein konkurrierend geänderter
  Grab kann dadurch keine halb angewandte Request-/Grab-Kombination erzeugen.
- Der bestehende `UsenetAcquisitionMonitor` führt den Reconciler direkt nach
  Start und danach in jedem periodischen Zyklus aus, auch wenn kein Usenet-
  Client konfiguriert oder kein Usenet-Grab offen ist.
- `GET /api/library/v2/maintenance/reconcile-acquisition` liefert den
  Admin-Dry-Run samt reason-coded Counts/Entscheidungen. Nur
  `POST ... {"apply": true}` wendet die sicheren Transitionen an.

Regressionstests:

- `tests/acquisition/test_reconciler.py`
- `tests/acquisition/test_client_monitor.py`
- `tests/library2/test_maintenance_reconciliation_endpoints.py`

### LV2-007 – Orphan Detector war Legacy-only

Symptom:

- Library-v2-only Files wie die in `findings.md` genannten Beispiele wurden als Orphans gemeldet, obwohl `lib2_track_files` sie kannte.

Root Cause:

- `core/repair_jobs/orphan_file_detector.py` las ausschließlich `tracks.file_path` und Legacy-Title/Artist.

Implementierter Fix:

- Schema-optional werden nicht gelöschte `lib2_track_files.path` in die bekannten Pfadsuffixe aufgenommen.
- Library-v2-Track-/Artist-Identitäten fließen in den Tag-Fallback ein.

Regressionstest:

- `tests/test_orphan_file_detector.py::test_library_v2_only_file_is_not_reported_as_orphan`

Hinweis zur Stichprobe:

- Die Datenbank enthielt 156 Legacy-Tracks, 1.928 Library-v2-Tracks und 206 Library-v2-Files. Dieses Größenverhältnis erklärt, warum eine Legacy-only-Erkennung systematisch falsche Positives erzeugt.

### LV2-008 – Review Queue Human Approve war nicht Library-v2-konsistent

Symptom:

- Human Approve aktualisierte `library_history`, Legacy-`tracks` und den File-Tag.
- Die zugehörige Zeile in `lib2_track_files` behielt einen alten Verification-Status.

Implementierter Fix:

- `core/library2/verification.py` aktualisiert passende Library-v2-Dateizeilen anhand des rohen und des path-gemappten/resolvierten Pfads.
- Der Endpoint liefert `lib2_files_updated` zurück.
- Installationen ohne Library-v2-Schema bleiben ein No-op.

Regressionstests:

- `tests/library2/test_verification_sync.py`

### LV2-009 – Recover to Staging bewegt Disk, nicht Lifecycle

Symptom aus `findings.md`:

- `/api/quarantine/<entry_id>/recover` ruft `recover_to_staging()` auf.
- File und Sidecar werden verschoben/entfernt, aber Request, Grab, Import, History und Library-v2-Zustand erhalten keine explizite Transition.

Implementierter Fix:

- Neuer persistenter Importstatus und History-Event
  `recovered_to_staging`; bestehende SQLite-CHECK-Constraints werden ohne
  Datenverlust gezielt erweitert. Sehr alte Tabellen ohne Status-CHECK
  werden nicht unnötig neu aufgebaut.
- `acquisition_quarantine_recoveries` journalisiert Entry-ID, Quell-,
  Sidecar- und Staging-Pfad sowie Request-/Candidate-/Grab-/Import-
  Korrelation **vor** dem Move.
- Reihenfolge: Move planen und committen, File verschieben, Lifecycle
  committen, erst danach Sidecar entfernen. Ein Crash vor oder nach jedem
  Schritt ist idempotent wiederaufnehmbar.
- Der spätere manuelle Staging-Import erhält die nativen Acquisition- oder
  Manual-Grab-Marker zurück und läuft wieder durch dieselbe Shared Pipeline.
  Ein transient fehlgeschlagener Versuch bleibt erneut importierbar, solange
  das Staging-File noch existiert.
- Der Import-Monitor lässt `recovered_to_staging` bewusst warten, statt ihn
  automatisch als Success oder Fail umzudeuten.

Regressionstests:

- `tests/acquisition/test_quarantine_recovery.py`

### LV2-010 – Library-v2-Scan ist physisch, aber erster Miss wird versteckt

Korrektur der ursprünglichen Vermutung:

- `core/library2/scan.py::rescan_files()` prüft nicht nur DB-Existenz. Der gespeicherte Pfad wird über `resolve_lib2_path()` aufgelöst und die reale Datei wird gelesen/probed.
- Fehlende Pfade werden nur bei gesundem Root fortgeschrieben.
- Erst ein Miss ergibt `missing_suspected`, der zweite `missing_confirmed`.

Der tatsächliche Bug:

- `core/library2/status.py::file_status()` behandelt `missing_suspected` weiter als `present`.
- Damit meldet die UI nach dem ersten bestätigten physischen Miss einen zu sicheren Zustand und verschleiert, dass eine zweite Prüfung aussteht.

Implementierter Fix:

- `core/library2/status.py::file_status()` gibt beim ersten gesunden Miss
  explizit `missing_suspected` zurück.
- TypeScript-Vertrag und Track-Tabelle zeigen diesen Zustand amber als
  `checking missing` mit Hinweis auf die erforderliche zweite Prüfung.
- Der Zustand bleibt presence-seitig aktiv und löst weiterhin keinen Wanted-
  oder Redownload-Pfad aus. Erst `missing_confirmed` wird `missing`.

Regressionstests:

- `tests/library2/test_queries.py`
- `webui/src/routes/library-v2/-ui/album-track-table.test.tsx`

### LV2-011 – `Odetari w/ 9lives` wurde als `Odetari w` gesplittet

Root Cause:

- Die Featured-Artist-Regex kannte `feat`, `ft`, `featuring` und `with`, aber nicht `w/`.
- Der allgemeine `/`-Listenseparator zerlegte danach den String in `Odetari w` und `9lives`.

Implementierter Fix:

- `w/` wird in Artist-Credits und parenthesized Title-Credits als expliziter Feature-Separator erkannt.
- Das Feature-Präfix wird vor dem allgemeinen Listensplit entfernt.

Regressionstests:

- `tests/library2/test_importer.py`

### LV2-012 – Provider-ID-Dedup fehlte

Root Cause:

- `repair_duplicate_artists()` gruppierte nur nach normalisiertem Namen.
- Ein Fragment `Odetari w` konnte später dieselbe Spotify-ID wie `Odetari` erhalten, wurde wegen des anderen Namens aber nie zusammengeführt.

Implementierter Fix:

- Zweiter Dedup-Pass über konfliktfreie IDs von Spotify, MusicBrainz, Deezer, Tidal und Qobuz.
- Vor jedem Merge werden die noch existierenden Rows frisch gelesen.
- Bei widersprüchlichen anderen Provider-IDs wird nicht automatisch gemerged.
- Alben werden nach dem Artist-Merge erneut innerhalb des Survivors gefaltet.

Offene Datenaktion:

- Der Reparaturlauf wurde absichtlich nicht ungeprüft auf der Live-Datenbank ausgeführt. Vor dem produktiven Merge ist ein Dry-Run/Backup und die Ausgabe aller Survivor-/Duplicate-Paare erforderlich.

Regressionstests:

- `tests/library2/test_dedup_repair.py`

### LV2-013 – Ein übergreifender Integritäts-Reconciler fehlt

Die Einzel-Fixes werden jetzt durch einen zentralen, restart-sicheren
Soll/Ist-Abgleich ergänzt über:

- reales File und Path Mapping,
- `tracks.file_path`,
- `lib2_track_files`,
- `library_history` und `track_downloads`,
- `download_tasks` und `matched_downloads_context`,
- `acquisition_requests`, `acquisition_grabs` und `acquisition_imports`,
- Quarantäne-Sidecars,
- externen Download-Client und Media Server.

Implementierter Fix:

- `core/library2/integrity_reconciler.py` erzeugt einen strikt lesenden,
  begrenzten Report mit Code, Severity, Komponente, Entität, Begründung und
  Details pro Abweichung.
- Abgedeckt sind reale/gemappte Pfade, `tracks.file_path`, aktive
  `lib2_track_files`, `track_downloads`, `library_history`, terminale und
  aktive Runtime-Kontexte, Acquisition-Request/Grab/Import, Recovery-
  Journal, Quarantäne-File/Sidecar-Paare und externe Client-Snapshots. Die
  aktive Media-Server-Projektion wird über `tracks.server_source`/
  `tracks.file_path` und ihren Connection-Status ausgewiesen.
- Findings umfassen unter anderem stale Runtime-Kontexte, offene/terminale
  Lifecycle-Divergenzen, reale unindizierte Files, Legacy-v2-Index-
  Abweichungen, Completed Imports ohne indexiertes File, fehlende Recovery-
  Staging-Files und verwaiste Sidecars.
- Das Storage-Health-Gate bleibt verbindlich; ein ungesunder Root erzeugt
  nur `unresolved`, nie einen bestätigten Miss. Der Auditor führt keinerlei
  Delete, Statusänderung oder Schema-Migration aus.
- `GET /api/library/v2/maintenance/integrity-report` stellt den admin-only
  Read-only-Report bereit; `max_findings` begrenzt Details, während Counts
  den vollständigen Scan abbilden.

Regressionstests:

- `tests/library2/test_integrity_reconciler.py`
- `tests/library2/test_maintenance_reconciliation_endpoints.py`

### LV2-014 – Enhanced-Search erkennt v2-native Artists nicht als „In Your Library"

Symptom:

- Auf der Search-Seite (`enh-db-artists-section` vs. `enh-spotify-artists-section`)
  kann ein Artist, den der Nutzer über Library v2 bereits importiert/monitort
  hat, trotzdem in der externen „Artists"-Spalte statt unter „In Your
  Library" auftauchen.

Root Cause:

- `core/search/orchestrator.py::_build_db_artists()` ruft
  `deps.database.search_artists(...)` auf, was ausschließlich gegen die
  Legacy-Medienserver-Spiegel-Tabelle in `database/music_database.py`
  (befüllt über `insert_or_update_media_artist(..., server_source=...)`)
  matcht.
- `core/library2/mirror_outbox.py` spiegelt ausschließlich Watchlist-/
  Wishlist-Operationen zwischen Library v2 und der Legacy-DB — keine
  allgemeine Artist-Ownership.
- Artists, die rein über Library v2 entstehen (Wishlist-Funde,
  Discography-Discovery) tragen `legacy_artist_id = NULL`
  (`core/library2/native_enrich.py`) und haben daher nie eine Zeile in der
  Legacy-Spiegel-Tabelle, gegen die `search_artists()` matcht.

Empfehlung (noch nicht umgesetzt):

- `_build_db_artists()` zusätzlich gegen `lib2_artists` matchen lassen
  (oder Legacy- und v2-Treffer je Query vereinigen/deduplizieren), sodass
  v2-native Artists korrekt als „In Your Library" erkannt werden.
- Kein Blocker für den aktuellen PR-Stand: betrifft nur Artists ohne
  `legacy_artist_id`-Link, aktuell eine Minderheit. Wird relevanter, je
  mehr Imports komplett am Legacy-Pfad vorbeilaufen.

### LV2-015 – Playlist-Pipeline darf nicht die globale Wishlist starten

Symptom:

- Ein Klick auf „Run Pipeline“ bei einer einzelnen Mirrored Playlist führte
  Refresh, Discovery und Sync korrekt nur für diese Playlist aus.
- Phase 4 startete anschließend jedoch Downloads für beliebige offene
  Wishlist-Tracks aus der gesamten Library.

Root Cause:

- `core/automation/handlers/_pipeline_shared.py::run_wishlist_phase()` rief
  `deps.process_wishlist_automatically(automation_id=None)` ohne Track- oder
  Profilfilter auf.
- `core/wishlist/processing.py::process_wishlist_automatically()` kombinierte
  daraufhin wie für den Timer vorgesehen die Wishlist aller Profile und
  verarbeitete den globalen Album-/Single-Zyklus.

Implementierter Fix:

- Der gemeinsame Pipeline-Tail leitet den Scope aus den tatsächlich
  verarbeiteten Playlist-Zeilen ab: bevorzugte Discovery-ID,
  `spotify_hint`, native `source_track_id` sowie das konkrete Profil.
- Organize-by-Playlist-Einträge sind ausgeschlossen, weil sie ihren eigenen
  direkten Downloadpfad besitzen.
- Der Wishlist-Prozessor akzeptiert einen expliziten `track_ids`-/
  `profile_ids`-Scope, liest nur die exakt passenden Wishlist-Zeilen und
  verarbeitet in diesem Lauf alle Kategorien.
- Der gescopte Lauf führt keine globale Duplicate-Cleanup-Mutation aus und
  verändert den globalen Album-/Single-Zyklus auch bei Completion nicht.
- Fail closed: Kann keine Track-Identität für die Playlist ermittelt werden,
  wird Phase 4 übersprungen; die globale Wishlist wird niemals als Fallback
  gestartet.

Regressionstests:

- `tests/automation/test_playlist_pipeline_folder_mode.py`
- `tests/wishlist/test_automation.py`
- `tests/wishlist/test_processing.py`

### LV2-016 – Neue Artists dürfen nicht per Default monitored sein

Symptom:

- Durch Wishlist-/Download-Materialisierung neu angelegte
  `lib2_artists`-Zeilen erschienen als überwacht, obwohl der Artist nicht auf
  der Watchlist stand.
- Auf laufenden Installationen wurde dieser Drift nach dem einmaligen
  Legacy-Erstimport nicht mehr korrigiert.

Root Cause:

- `core/library2/schema.py` definierte
  `lib2_artists.monitored INTEGER NOT NULL DEFAULT 1`.
- Autolink und Artist-Resolver ließen `monitored` beim Insert aus und erbten
  deshalb den falschen Default.
- Die einzige vollständige Watchlist-Ableitung lief bisher im initialen
  `apply_monitoring_from_watchlist_wishlist()`-Importpfad.

Implementierter Fix:

- Der frische Schema-Default ist `0`. Eine idempotente SQLite-Migration stellt
  auch bestehende Tabellen von Default `1` auf `0` um, ohne vorhandene
  effektive Monitorwerte zu verändern.
- Alle Library-v2-Artist-Insertpfade schreiben `monitored` explizit. Importer
  starten neutral mit `0`; der initiale Import leitet danach weiterhin die
  echte Watchlist-Projektion ab.
- Der Autolink-/Materialize-Pfad prüft bei einer neuen Artist-Zeile direkt die
  Watchlist (Provider-ID, danach Name): nur ein realer Watchlist-Treffer startet
  als monitored.
- Der stündliche Repair-Job `monitoring_list_reconcile` gleicht bestehende
  Installationen dauerhaft ab. Explizite Library-v2-Regeln gewinnen und werden
  in die Watchlist gespiegelt; alte/default/importierte Flags ohne explizite
  Regel werden aus der echten Watchlist abgeleitet. Dadurch werden Phantom-
  Artists demonitoriert, ohne echte Nutzerentscheidungen zu verlieren.

Regressionstests:

- `tests/library2/test_schema.py`
- `tests/library2/test_autolink.py`
- `tests/library2/test_monitor_sync.py`
- `tests/repair_jobs/test_monitoring_list_reconcile.py`

## 5. Verbleibende Rollout-/Betriebsaktionen

Die bekannten Codefehler LV2-001 bis LV2-013 sowie LV2-015 und LV2-016 sind
implementiert. LV2-014 bleibt offen. Keine der
folgenden Aktionen ist eine noch offene Codekorrektur:

1. Acquisition- und Integritäts-Dry-Run nach Deployment prüfen und Counts
   beobachten.
2. Produktiven Odetari-Dedup erst nach Dry-Run-Liste und Backup ausführen;
   die Live-Datenbank wurde in diesem Arbeitslauf bewusst nicht mutiert.
3. Reale End-to-End-Matrix für Success, Not Found, Cancel, Quarantine,
   Recover/Approve nach Restart und Post-Move-Failure durchführen.

## 6. Teststand dieses Arbeitslaufs

Ausgeführt:

```text
pytest tests/imports/test_import_pipeline.py
       tests/library2/test_queue_status.py
       tests/library2/test_scoped_search_endpoint.py
       tests/wishlist/test_manual_download.py
       tests/wishlist/test_transient_search_failure.py
       tests/test_orphan_file_detector.py
       tests/library2/test_importer.py
       tests/library2/test_dedup_repair.py
       tests/library2/test_verification_sync.py -q
```

Ergebnis: `163 passed`.

Zusätzlich wurden die geänderten Python-Module inklusive `web_server.py` mit `py_compile` geprüft.

Ergänzender Regressionslauf für LV2-015/LV2-016 und den Monitoring-Reconcile
am 19. Juli 2026:

```text
pytest tests/automation tests/wishlist tests/library2 tests/repair_jobs -q
```

Ergebnis: `1453 passed`. Die drei Warnungen sind zwei bereits vorhandene
Coroutine-Warnungen in `test_handlers_maintenance.py` und eine bestehende
Python-3.12-SQLite-Deprecation-Warnung; es gab keine Testfehler. Zusätzlich
waren `ruff check`, `py_compile` und `git diff --check` für alle berührten
Module sauber.

Breiter Regressionslauf:

```text
pytest tests/library2 tests/wishlist tests/imports tests/acquisition -q
```

Ergebnis: `1970 passed, 3 skipped`. Die 31 Warnungen sind
Python-3.12-Deprecation-Warnungen der bestehenden Metadata-Worker und keine
Testfehler.

Enthalten sind zusätzlich:

- `tests/acquisition/test_reconciler.py`
- `tests/acquisition/test_quarantine_recovery.py`
- `tests/acquisition/test_client_monitor.py`
- `tests/library2/test_integrity_reconciler.py`
- `tests/library2/test_maintenance_reconciliation_endpoints.py`
- die neuen `missing_suspected`-Fälle in `tests/library2/test_queries.py`

Frontend:

```text
vitest run src/routes/library-v2
```

Ergebnis: `141 passed` in 24 Testdateien. Der Production-Build war
erfolgreich. Der gezielte Formatter-/Type-Lint-Lauf der berührten Dateien war
sauber. Der globale `npm run check` stoppt ausschließlich an der bereits
vorhandenen, in diesem Fix nicht geänderten Formatabweichung
`track-feature-badges.test.tsx`.

Nicht ausgeführt:

- kompletter Repository-Testlauf,
- Browser-/Frontend-E2E,
- echter Soulseek-/slskd-Transfer,
- externer Plex/Jellyfin/Navidrome-Scan,
- mutierende Datenreparatur auf `database/music_library.db`.

## 7. Definition of Done für diesen Bug-Cluster

Der Cluster ist erst abgeschlossen, wenn folgende Matrix reproduzierbar grün ist:

| Aktion | Monitoring | Ergebnis | Erwartete Wishlist | Erwarteter UI-Status | Erwartete DB-/Disk-Wirkung |
|---|---|---|---|---|---|
| Track Automatic Search | off | not found | unverändert/kein Eintrag | terminal, nicht aktiv | keine File-Zeile, kein offener Grab |
| Track Automatic Search | off | success | unverändert/kein Eintrag | completed, danach nicht aktiv | reales File direkt am Library-v2-Track |
| Track Automatic Search | on + missing | fail | bestehende Wanted-Spiegelung bleibt | terminal failed | kein künstlicher neuer Monitor-State |
| Manual Grab | beliebig | success | nur fachlich bedingte Änderung | completed, nicht queued | File, Verifikation und Library v2 synchron |
| Manual Grab | beliebig | quarantine | keine falsche Completion | quarantined/failed | Sidecar und Korrelation vorhanden |
| Human Approve nach Restart | beliebig | success | fachlich bedingte Änderung | completed | File verknüpft, Scan angefordert, Grab geschlossen |
| Post-Move-Exception | beliebig | Ziel existiert | fachlich bedingte Änderung | terminal/reconciled | kein physischer DB-Orphan |
| Refresh & Scan, erster Miss | monitored | Pfad fehlt, Root gesund | noch kein voreiliger Download | missing suspected | Counter 1, kein Delete |
| Refresh & Scan, zweiter Miss | monitored | Pfad fehlt, Root gesund | Wanted-Projektion aktiv | missing confirmed | File-Zeile bleibt auditierbar |
