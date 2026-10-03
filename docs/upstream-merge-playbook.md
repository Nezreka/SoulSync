# Upstream-Merge-Playbook für `library-overhaul`

Dieses Dokument legt fest, wie ein Sync von `upstream/dev` (Nezreka/SoulSync)
auf diesen Fork abläuft. Es gilt für jede Session, egal ob Claude oder Codex.

**Grundsatz:** Ein Upstream-Sync ist ein *Port*, kein reiner Merge. Jede
Upstream-Funktion und jeder Upstream-Fix bekommt eine von drei Entscheidungen:

| Entscheidung | Bedeutung |
| --- | --- |
| übernommen | Der Upstream-Code läuft unverändert und funktioniert mit Library v2. |
| portiert | Die *Absicht* der Änderung wurde in die Library-v2-Stelle eingebaut, die bei uns denselben Zweck erfüllt. |
| abgelehnt | Nur mit konkretem Grund, warum Library v2 die Änderung nicht braucht (z. B. „Library v2 vergleicht exakte Schlüssel, der Fuzzy-Fehler kann nicht auftreten“). |

„Der Job ist bei uns stillgelegt“ oder „die Seite ist gelöscht“ ist **kein**
Grund. Dann ist zu prüfen, welche Library-v2-Funktion an ihre Stelle getreten
ist, und ob sie denselben Fehler oder dieselbe Lücke hat. Beim Sync auf 3.5.0
wurden so zuerst drei Ports übersehen: die `- From "…"`-Titelendungen der
Duplikaterkennung (#1315), der Release-Typ beim Reorganize (558d96864,
d6252ba1f) und der Fake-Lossless-Fix.

## 0. Vorbereitung

1. `git fetch upstream`, Merge-Basis und Commit-Liste festhalten:
   `git log --reverse --no-merges --format='%h %s' $(git merge-base HEAD upstream/dev)..upstream/dev`.
2. Sicherungsbranch `backup/library-overhaul-pre-sync-YYYYMMDD`.
3. `python3 scripts/deleted_path_upstream_audit.py --upstream upstream/dev --since <letzter-tag>`
   listet Upstream-Arbeit an Pfaden, die dieser Fork gelöscht hat. Jeder Treffer
   kommt in die Port-Matrix (Abschnitt 2). Entschiedene Commits mit
   `--reviewed <sha> "PORTED/DECLINED/N/A: …"` eintragen; offene Entscheidungen
   **nicht** eintragen, damit sie beim nächsten Lauf wieder erscheinen.
4. `git diff <basis> upstream/dev -- requirements*.txt Dockerfile` auf neue
   Abhängigkeiten prüfen und lokal in `.venv` installieren
   (`.venv/bin/python -m pip install …`).

## 1. Feature-Inventar *vor* dem Merge

Die Commit-Liste nach Funktionen gruppieren (nicht nach Dateien). Für jede
Gruppe festhalten:

- Was ändert sich für den Nutzer?
- Welche Oberfläche, welcher Job, welcher Endpunkt ist betroffen?
- Gibt es diese Stelle bei uns unverändert, durch Library v2 ersetzt oder gar nicht?
- Welche Art von ID läuft durch die Änderung (Katalog-ID, Server-ID, alte Legacy-ID)?
- Liest oder schreibt sie Katalogdaten (`artists`/`albums`/`tracks` oder deren Spalten)?

## 2. Port-Matrix: Upstream-Stelle → Library-v2-Gegenstück

Diese Zuordnung ist bei jedem Treffer zu prüfen. Fehlt eine Zeile, wird sie
hier ergänzt.

| Upstream | Library-v2-Gegenstück bei uns |
| --- | --- |
| Tabellen `artists`/`albums`/`tracks`, `tracks.file_path` | `lib2_artists`, `lib2_albums`, `lib2_tracks`, `lib2_track_files` (`path`, `file_state`, `is_primary`, `owner_profile_id`), `lib2_track_artists` |
| Provider-Spalten (`spotify_artist_id`, `deezer_id`, `itunes_*`, …) | `spotify_id`/`musicbrainz_id` als Spalte, alles andere in `external_ids` (JSON) |
| `thumb_url` | `image_url` |
| `listening_history.db_track_id` als Bibliotheks-ID | `listening_history.lib2_track_id`; `db_track_id` ist die Server-ID |
| Player-Zeile `id` | Server-/Legacy-ID; Katalog-ID ist `lib2_track_id` |
| Repair-Jobs mit eigener Track-SQL | `core/library2/maintenance_subjects.py`, Finding-IDs `lib2:<id>` |
| `os.remove` in Fix-Handlern | `core/library2/file_delete.py` (Journal; `mode='quarantine'` für Verschieben in `.deleted`) |
| Duplicate Detector (stillgelegt) | `core/library2/duplicate_relationship.py` (`_normalized_title`), `core/library2/importer.py` (`dedup_title_key`), Manage-Tracks-Dialog |
| Single/Album Dedup (stillgelegt) | Single↔Album-Verknüpfung im Importer (`dedup_title_key`) |
| Quality Upgrade / Quality Upgrade Scanner (stillgelegt) | Wanted-Projektion (`core/library2/wanted*.py`), Quality-Evaluation |
| Reorganize (`core/library_reorganize._build_post_process_context`) | `core/library2/reorganize_plan.py` (baut den Kontext aus dem Katalog) |
| Legacy-Library-Seite (`webui/src/routes/library/-library.*`) | Library V2 (`-library-v2.*`, `-ui/library-v2-page.tsx`); Sortierung in `core/library2/queries.py` (`_SORTS`, `_alpha_key`) |
| Duplicate Detector „Keep Best“ / Playlist-Mitgliedschaft | Manage Tracks → Duplikate (`/api/library/v2/artists/<id>/duplicates`), `media_mappings.track_server_playlists` |
| Fix-Handler eines Finding-Typs (`_fix_*` in `core/repair_worker.py`) | gleicher Handler auf `lib2:<id>`-Subjekten; Datei-Findings tragen die **Datei**-ID, der Track kommt aus `lib2_track_files` bzw. `details.library_v2.track_id` |
| `get_library_artists` / Library-API | Kompatibilitäts-API in `database/music_database.py` auf `lib2_*` |
| Artist-Detail-Seite | bleibt Upstreams Seite für Künstler außerhalb des Katalogs (Stufe 1) |
| Wishlist-Owner pro Profil (H10) | Owner-Tags `_wishlist_profile_id`/`_wishlist_library`, Dedupe pro Bibliothek (#1199) |
| Batch-Profil | `library_owner_id` auf dem Batch (`core/library_scope.py`) |

## 3. Merge und Konflikte

- Konflikt „unsere lib2-Seite gegen ihre Legacy-Seite“: unsere Seite behalten,
  dann Upstreams *Absicht* darauf portieren.
- Upstream hebt Endpunkte in Module (`api/*.py`): den Lift übernehmen und unsere
  Deltas in das Modul tragen, nie zwei Regeln für eine URL.
- Datei bei uns gelöscht, bei Upstream geändert (DU): gelöscht lassen, Änderung
  aber in die Port-Matrix aufnehmen.
- Reine Formatierungskonflikte im Frontend: Upstreams Datei nehmen, `npx oxfmt`
  auf unsere Änderungen.
- Neue Upstream-Dateien in gelöschten Ordnern (z. B. Komponenten der alten
  Library-Seite) entfernen; `npx tsc --noEmit` findet sie.

## 4. Statische Prüfungen nach dem Merge

1. `tests/library2/test_legacy_usage_ratchet.py` muss 0/0 zeigen. Die Ratsche
   sieht nur wörtliches `FROM/JOIN/INTO/UPDATE artists|albums|tracks`.
2. Zusätzlich suchen, was die Ratsche nicht sieht:
   `PRAGMA table_info(tracks|albums|artists)`, `not_locked_sql(cur, 'albums'…)`,
   `_has_column(cur, 'artists'…)`, alte Spaltennamen in neuer SQL.
3. Aufrufe von `MusicDatabase`-Methoden, die es bei uns nicht gibt (Skript:
   Methodennamen aus geänderten Dateien gegen `dir(MusicDatabase)`).
4. ID-Typen jeder neuen Funktion verfolgen: Katalog-ID, Server-ID, Finding-ID
   `lib2:<id>`. Frontends, die `finding.entity_id` an einen Endpunkt schicken,
   brauchen dort das Abschneiden von `lib2:`.
5. `web_server` importieren und doppelte (Regel, Methode)-Paare zählen; nicht
   dauerhaft starten (Hintergrund-Worker greifen sonst auf Netz und echte DB zu).
6. `cd webui && npx tsc --noEmit && npm run check`.

## 5. Commit-für-Commit-Audit

Tests grün heißt nicht Port vollständig. Nach jedem Sync jeden Upstream-Commit
einstufen: übernommen, portiert, begründet abgelehnt oder **verloren/kaputt**.

- Lesende Audit-Agents über Commit-Bereiche verteilen; große Commits (wie
  4a34aad9a in 3.5.0) bekommen einen eigenen Agent.
- Jeder Agent bekommt eine Kontextdatei mit: Stand des Merges, Library-v2-Fakten
  (Abschnitt 2), bereits erledigten Ports, Bewertungskategorien, Verbot zu
  editieren und `web_server` zu booten.
- Ausdrücklich fragen: „Ist der Fix in einer bei uns gelöschten Datei gelandet,
  und braucht das Library-v2-Gegenstück denselben Fix?“
- Danach jedes „verloren/kaputt“ beheben und mit einem Test absichern, der ohne
  den Fix fehlschlägt.

## 6. Tests

- Neue Upstream-Tests auf Library v2 portieren: `tests/lib2_seed.py`
  (namensbasiert), `tests/support/catalogue_seed.py` (Server-IDs).
  Nur Tests für stillgelegte Oberflächen löschen, mit Begründung.
- Test-DBs ohne Katalog scheitern am Upgrade-Gate (fail closed):
  `core.library2.migration_gate.migration_required` patchen.
- `PYTHON=.venv/bin/python ./scripts/run_tests_chunked.sh` (~15 min). Jede Zeile
  muss „passed“ zeigen: ein Sammelfehler in einer Datei lässt einen ganzen
  `tests/*.py`-Slice ausfallen. Hängt ein Abschnitt, ihn einzeln wiederholen.
- Frontend: `npm run check`, `npm test`.
- Bekannte Umgebungsprobleme: `/usr/bin/rubberband` 4.0 (CLI geändert),
  Sample-Studio-Abhängigkeiten in `.venv`.

## 7. Abschluss

1. Bericht `docs/upstream-sync-<version>.md`: Umfang, Konflikte, Port-Matrix mit
   allen drei Entscheidungen (abgelehnt immer mit Grund), offene Entscheidungen
   für den Nutzer, Prüfergebnisse.
2. Lokal committen, nie pushen ohne ausdrücklichen Auftrag.
3. Neue Zuordnungen und Fallen in dieses Playbook zurückschreiben.

## Beispiele aus 3.5.0

| Upstream | Zuerst übersehen | Port |
| --- | --- | --- |
| #1315: `Rabbit Run - From "8 Mile" Soundtrack` = `Rabbit Run` | „Duplicate Detector stillgelegt“ | Titelendung in `_normalized_title` und `dedup_title_key` |
| 558d96864 / 4f09b31b8: Reorganize legt ein Release ab, wo der Download es ablegte | Fix im alten Kontextbauer, Library-v2-Planer unberührt | Typentscheidung des Downloads am Album speichern, Planer nutzt sie |
| Fake-Lossless-Fix | „bleibt review-only“ | Quarantäne über das Journal plus Redownload-Absicht; UI sucht über den Track der Datei |
| 33da6be49: A–Z ignoriert führende Satzzeichen | nur die Kompatibilitäts-API portiert | gleicher Sortierschlüssel in der nativen Künstlerliste und der Album-Reihenfolge |
| e573bd5fc: Keep Best behält die Playlist-Kopie | „Detector stillgelegt“ | Playlist-Hinweis in der Manage-Tracks-Duplikatliste |
| #1350 OR-NOT-EXISTS-Volltabellenscan | Fork-Version hatte denselben Fehler | drei `NOT IN`-Listen über die materialisierte CTE |
| #1417 Sync bemerkt gelöschte Tracks | Server-ID vs. Katalog-ID | Präsenzprüfung über Server-Mappings |
