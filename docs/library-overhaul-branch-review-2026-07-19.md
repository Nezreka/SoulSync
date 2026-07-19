# library-overhaul — Max-Effort Review & PR-Split-Inventar (2026-07-19)

Scope: `git diff upstream/dev...HEAD` — 328 eigene Commits (Autoren `dev` + `nick2000713`), 399 Dateien, ~109k eingefügte Zeilen. Nicht Teil des Scopes: der übrige, von `upstream/dev` geerbte Code (1783 Commits gegen `main`, davon die meisten fremd).

Methode: 10 parallele Finder-Agents (5 Korrektheits-Winkel, 3 Cleanup-Winkel, 1 Altitude-Winkel, 1 PR-Split-Inventar) + manuelle Direktverifikation der High-Severity-Kandidaten am realen Code (siehe „Verifiziert" je Fund). Ein Kandidat (`core/discovery/sync.py:128`, ursprünglich von Angle A gemeldet) wurde bei der Verifikation **widerlegt** — Details am Ende von Teil A.

---

## Teil A — Korrektheits-Findings (Library v2 / Acquisition)

Rangfolge nach Schweregrad. „Verifiziert" = ich habe den Code selbst gelesen und die Kette bestätigt, nicht nur den Finder-Agent zitiert.

### A1. Komplette native Repair-Job-Suite läuft nur mit `features.library_v2=true` — Default ist `false` (KRITISCH)
**Dateien:** `core/library2/maintenance_subjects.py:17-23`, `core/repair_jobs/dead_file_cleaner.py:51-55`, `config/config.example.json:78`
**Verifiziert:** ja, direkt gelesen.

`_enabled()` verlangt `config_manager.get("features.library_v2", False) is True`; das Default in `config.example.json` ist `false`. Jeder P3-migrierte Job (Dead-File, Orphan, AcoustID, Cover-Art, Lyrics, ReplayGain, Metadata-Gap, Fake-Lossless, Lossy-Converter, Tag-Consistency, Quality-Upgrade-Scan, Audio-Corruption — alles mit `JOB_DATA_BASIS='lib2'`) ruft `active_file_subjects()` auf, bekommt `[]` zurück und returned **ohne Findings, ohne Fehler** — sieht aus wie „alles sauber", ist aber „läuft gar nicht". Vor der P3-Migration lasen dieselben Jobs direkt aus den Legacy-Tabellen und funktionierten für jede Installation. Betrifft jede Standardinstallation, die Library v2 (noch experimentell) nicht aktiviert hat.

### A2. `library_reorganize` in `RETIRED_JOB_IDS`, aber niemand erzeugt mehr `path_mismatch`-Findings — bestehende werden bei jedem Neustart gelöscht
**Dateien:** `core/repair_jobs/__init__.py:86-93`, `core/repair_worker.py:475-489` (`_prune_retired_job_findings`), `core/reorganize_runner.py:75-104`, `core/library2/maintenance_sync.py:406-422` (`sync_repair_change`)
**Verifiziert:** ja — `sync_repair_change` ruft nachweislich nur `set_file_state`/Rescan auf, nie `create_finding`. `grep` nach `create_finding.*path_mismatch` im gesamten `core/` liefert keinen Treffer.

`_prune_retired_job_findings()` löscht bei jedem Worker-Start alle **pending** Findings mit `job_id IN RETIRED_JOB_IDS` — inklusive `library_reorganize`. Der Commit-Kommentar zu §81 behauptet, die native Reorganize-Engine „produziert diesen Finding-Typ weiterhin", das stimmt nicht: `reorganize_runner.py` ruft nur `sync_repair_change(job_id="library_reorganize", finding_type="path_mismatch", ...)`, was ausschließlich lib2-Mirror-State synchronisiert, nie einen Fund in `repair_findings` anlegt. Die Dispatch-Handler `_fix_path_mismatch` (repair_worker.py:1176) und der Bulk-Fix-Eintrag (Zeile 3257) sind seitdem toter Code. **Konkreter Datenverlust:** alte, noch nicht durchgesehene `path_mismatch`-Findings verschwinden beim nächsten Neustart.

### A3. Discography-Refresh hat die Content-Type-Filter verloren (Live/Remix/Acoustic/Compilation/Instrumental)
**Datei:** `core/library2/discography.py:787` (`refresh_artist_discography`)
**Verifiziert:** ja — `grep -n "is_live_version\|is_remix_version\|is_acoustic_version\|is_compilation_album\|is_instrumental_version"` in `discography.py` liefert null Treffer.

Der alte, jetzt retirete `discography_backfill`-Job respektierte explizit die Content-Filter aus `core.watchlist_scanner` (Nutzer-Einstellungen „Include Live/Remixes/Acoustic/Compilations/Instrumentals"). Der native Ersatzpfad hat keinen dieser Filter. Für überwachte Artists werden bei erneuter Discography-Expansion jetzt Live-Alben, Remixe, Compilations etc. ungefiltert automonitort und in die Wishlist gespiegelt — Nutzer, die diese bewusst ausgeschlossen hatten, bekommen sie plötzlich zurück.

### A4. Quality-Upgrade-Scan prüft keine losen/nicht-importierten Dateien mehr auf der Platte
**Datei:** `core/repair_jobs/lib2_upgrade_scan.py:87` + `core/library2/wishlist_mirror.py:298-327` (`upgrade_candidate_track_ids`)
**Status:** PLAUSIBLE (starke Textbeleg-Evidenz aus dem gelöschten Job-Docstring, nicht selbst nachgestellt).

Der alte `quality_upgrade_scanner`-Job walkte den gesamten Musikordner (`os.walk`) und prüfte auch Dateien ohne DB-Match (`library_tracks_only` default `False`, ausdrücklich um lose/verwaiste Dateien einzuschließen). Der native Ersatz fragt nur noch `lib2_tracks JOIN lib2_wanted_tracks` per SQL ab — rührt die Festplatte nie an. Fehlgeschlagene Imports oder nicht gematchte Dateien bekommen dauerhaft keinen Qualitäts-Check mehr.

### A5. Discography-Refresh läuft nur für Artists, die schon einmal manuell „expanded" wurden
**Datei:** `core/repair_jobs/lib2_discography_refresh.py:59-66`
**Status:** PLAUSIBLE (Docstring der neuen Datei bestätigt die Absicht explizit, aber die Konsequenz — nie berührte Artists bleiben für immer ohne Backfill — ist wahrscheinlich unbeabsichtigt für alle, die keine Discography-Expansion je manuell ausgelöst haben).

Der SQL-Filter verlangt `discography_synced_at IS NOT NULL`. Der alte `discography_backfill`-Job scannte planmäßig **jeden** Artist unabhängig vom Expansion-Status. Praktisch: importierte Artists ohne manuellen „Update Discography"-Klick bekommen nie ein automatisches Backfill fehlender Alben/Tracks.

### A6. Playlist-Pipeline mit mehreren Wishlist-Profilen stempelt alle Downloads mit dem falschen Profil
**Datei:** `core/wishlist/processing.py:1024-1031`
**Verifiziert:** ja, direkt gelesen; `runtime.profile_id` wird an 5 Stellen (Zeilen 230, 274, 325, 769, 804) für Batch-/Download-Zuordnung verwendet.

```python
if len(scoped_profile_ids) == 1:
    runtime.profile_id = next(iter(scoped_profile_ids))
```
Nur der Single-Profile-Fall aktualisiert `runtime.profile_id`; im Multi-Profile-Fall bleibt es beim Konstruktor-Default (`profile_id=1`, Admin). Deckt eine „Run Pipeline"-Playlist Tracks aus 2+ Profilen ab, werden alle resultierenden Download-Batches fälschlich unter Profil 1 verbucht — falsche Quality-Settings, falsche Zuordnung/Historie für das eigentliche Profil. **Unabhängig von zwei Findern (Angle A + Angle D) gefunden.**

### A7/A8. Queue-Status-Badges: Shape-Mismatch + ungeschützte `int()`-Konvertierung
**Datei:** `core/library2/queue_status.py:98-103` vs. `core/acquisition/main_pipeline_bridge.py:73-98`
**Verifiziert:** ja, beide Seiten gelesen.

- **A7 (Shape-Mismatch):** Die Acquisition-Bridge (`_pipeline_context`) baut `track_info` mit `lib2_entity` auf oberster Ebene — **kein** `source_info`-Schlüssel. `get_queue_status()`s erste Schleife liest aber `track_info.get("source_info", {}).get("lib2_track_id")`. Für jeden über die Bridge dispatchten Download (Torrent/Usenet-Bundle-Match, Manual-Grab-Pfad) ist das immer `None` → `continue` → **kein Downloading/Processing-Badge für die gesamte Download-Dauer**, obwohl der Download tatsächlich läuft.
- **A8 (Crash-Risiko):** `int(raw_track_id)` in Zeile 101 ist nicht abgesichert; die Flask-Route (`api/library_v2.py`, `lib2_queue_status`) hat kein try/except drumherum. Ein einziger Task mit malformed `lib2_track_id` (z. B. leerer String) lässt den **gesamten** Queue-Status-Endpoint mit 500 sterben — für alle Alben/Tracks, nicht nur den betroffenen.

### A9. Watchlist-Removal-Fallback matched rein nach Name — kann falschen Artist demonitoren
**Datei:** `core/library2/monitor_sync.py:80-85` (`_match_lib2_artists`)
**Status:** PLAUSIBLE.

Wenn Provider-IDs beim Entfernen aus der Watchlist fehlen/veraltet sind, fällt die Funktion auf `WHERE LOWER(name) = LOWER(?)` zurück — ohne `LIMIT`, ohne Dedup-Guard. Zwei `lib2_artists`-Zeilen mit demselben Namen (echte Namensgleichheit oder ein noch nicht gemergter Duplikat-Rest — ein in diesem Projekt dokumentiertes offenes Problem) werden **beide** demonitort und aus der Wishlist entfernt, auch wenn der Nutzer nur einen davon meinte.

### A10. Kompletter Acquisition-Review-Backend-Bereich hat keine UI-Anbindung
**Datei:** `api/library_v2.py:295-1181` (`/acquisition/requests*`, `/acquisition/imports*` inkl. `/resolve` Zeile 877, `/acquisition/grabs*`, `/acquisition/blocklist*`, `/acquisition/path-health`, `/acquisition/correlation-coverage`)
**Verifiziert:** ja — `grep` nach diesen Pfaden in `webui/src/` liefert **null** Treffer.

Wenn ein Soulseek-Album-Bundle-Grab mehrdeutige Track-Datei-Zuordnungen hat (Bundle-Matching kann nicht automatisch jede Datei zuordnen), bleibt der Import auf manuelle `assignments` warten — es gibt aber keinen Button/keine Seite im Frontend, um das aufzulösen. Der Import hängt permanent fest, obwohl das Backend die Auflösung vollständig unterstützt.

### A11. Cover-Art-Speichern und „Write tags" teilen sich denselben Job-Mutex — verwirrender 409
**Datei:** `api/library_v2.py:2073` und `:4080` (beide `_job_registry.start("retag", ...)`)
**Verifiziert:** ja, beide Stellen gelesen.

Album-Cover speichern löst im Hintergrund einen Retag-Job unter demselben `"retag"`-Kind aus wie der explizite „Write tags"-Button im Retag-Modal. Klickt der Nutzer kurz nach dem Cover-Wechsel auf „Write tags", bekommt er `JobAlreadyRunning` → 409 „Library v2 job retag is already running." — obwohl nichts kaputt ist, nur zufällig zeitlich überlappt.

### A12–A15. Weitere reale, aber niedriger priorisierte Verhaltens-Bugs (nicht Blocker, aber user-sichtbar)
- **`core/library2/native_enrich.py:280`** — Fuzzy-Artist-Matching (SequenceMatcher, Schwelle 0.72) umgeht das dedizierte, projektweite Gate `core/worker_utils.py::artist_name_matches` (Schwelle 0.85, extra dafür gewählt, um genau „Blance/Blanke"-Fehltreffer abzuweisen). Bei CJK-Namen normalisiert der lokale `[^a-z0-9]+`-Filter beide Namen zu Leerstrings → `SequenceMatcher('', '').ratio() == 1.0` → der erste zurückgegebene Kandidat wird immer akzeptiert.
- **`core/library2/monitor_sync.py:483`** (+ 453, 559) — Ad-hoc `strip().casefold()` statt dem kanonischen `normalize_name` (`core/library2/importer.py:61`, das zusätzlich internen Whitespace kollabiert). Ein Artist mit doppeltem Leerzeichen im Tag matched via `normalize_name` beim Autolink, aber nicht beim Watchlist-Abgleich hier — dieselbe Bug-Klasse wie der schon gefixte „Odetari w"-Fall, nur an einer neuen Stelle.
- **`webui/.../interactive-search.tsx:94`** vs. **`-library-v2.api.ts:1779`** — zwei unabhängige, textuell fast identische Quality-Ranking-Implementierungen für dieselben Suchergebnisse (Interactive Search vs. Automatic-Grab). Wird eine erweitert (z. B. um AIFF/DSD), zeigt Interactive Search ein anderes „bestes" Ergebnis als Automatic Search tatsächlich greift — genau die Art Diskrepanz, nach der explizit gefragt wurde.
- **`core/library2/native_enrich.py:367`** — `_get_or_create_component_artist` defaultet `monitored=1`, während das Schema (`schema.py:63`) `DEFAULT 0` erzwingt (der dedizierte LV2-016-Fix). Aktuell sicher, weil der einzige Aufrufer den Wert explizit übergibt — aber die Signatur lädt einen künftigen Aufrufer ein, genau den gerade gefixten Bug erneut einzuschleppen.

### Widerlegter Kandidat: `core/discovery/sync.py:128`
Angle A meldete, der Legacy-Post-Sync-Hook (`_post_sync_automation_followup`) triggere für **jede** Mirrored-Playlist-Automation einen globalen, ungescopten Wishlist-Lauf — im Widerspruch zu LV2-015. Bei Verifikation zeigte sich: Der „Run Pipeline"-Pfad (`core/playlists/pipeline.py:109`) ruft `sync_one_fn` intern **mit `_automation_id: None`** auf — der Hook returned dann sofort (`if not automation_id: return`), feuert also nie parallel zum neuen gescopten `run_wishlist_phase`. Der Hook selbst stammt aus Commit `0b1fdba2` (bereits in `upstream/dev`, nicht aus dieser Branch) und ist für den separaten, eigenständig buchbaren Automation-Baustein „Sync Playlist" gedacht (`core/automation/blocks.py:214`) — dort ist der globale Fallback vermutlich beabsichtigtes Alt-Verhalten, keine Regression dieser Branch. **Kein Bug, korrekt entworfen für einen anderen Anwendungsfall.**

---

## Teil B — Qualität/Cleanup (kein Blocker, aber wert es anzugehen)

Kurzfassung der wertvollsten Cleanup-Funde (voller Bericht in den Agent-Transkripten verfügbar, hier nur die Essenz):

**Effizienz (relevant für 24/7-Betrieb auf Unraid):**
- `core/library2/monitor_sync.py:693` — der **stündliche** `monitoring_list_reconcile`-Job baut für JEDEN wanted Track das volle Wishlist-Payload (~6 Queries) neu auf, auch wenn der Track längst korrekt in der Wishlist steht — bei 100k Tracks mehrere 100k Queries/Stunde im Leerlauf.
- `core/library2/wanted.py:149` — N+1 Profil-Lookup pro Track in derselben Schleife, plus unbedingtes UPSERT auch bei unverändertem Datensatz.
- `core/library2/monitor_sync.py:574` — dasselbe Muster: unbedingtes UPSERT jeder Artist-Regel jede Stunde, selbst ohne Änderung.
- `webui/.../library-v2-page.tsx:5215` — Queue-Status-Polling (3s, unbedingt) pro `AlbumBlock` statt einer gemeinsamen artist-scoped Query — bei 30 Releases 10 Requests/Sekunde dauerhaft.

**Reuse (Drift-Risiko):**
- Lossless-Format-Set existiert **dreifach** unabhängig (`core/quality/lossless.py`, `core/library2/status.py:23`, `core/library2/track_files.py:41`) — bereits auseinandergedriftet (DSD/WavPack/M4A uneinheitlich behandelt); Badge und Datei-Auswahl können sich widersprechen.
- IN-Clause-Placeholder-Builder mehrfach dupliziert innerhalb `core/library2/` (`maintenance_sync.py:98` vs. `history_feed.py:101`, ~35 weitere Inline-Stellen) — SQLite-999-Var-Limit-Fix müsste an jeder Stelle einzeln nachgezogen werden.

**Simplification:**
- `core/library2/monitor_sync.py:89` und `:363` — je zwei ~90-Zeilen- bzw. 15-Zeilen-Funktionspaare (Artist- vs. Track-Variante) sind bereits auseinandergedriftet (Artist-Version nutzt zwei Outbox-Aufrufe, Track-Version nur einen).
- `core/library2/schema.py:786` — Migrations-Loop fragt `PRAGMA table_info` pro Spalte statt pro Tabelle ab (~30 statt ~4 Abfragen bei jedem Start).

**Altitude:**
- `core/wishlist/processing.py:995` — der `scoped`-Boolean durchzieht ~10 Verzweigungspunkte derselben Funktion statt eines dedizierten Scope-Objekts — jede zukünftige Änderung riskiert, eine Stelle zu vergessen.
- `core/automation/handlers/_pipeline_shared.py:334` — `automation_id=None` macht jedes interne Progress-Update im gescopten Playlist-Wishlist-Lauf zum No-op; der Nutzer sieht nur eine Zeile Zusammenfassung statt der reichhaltigen Details, die der Timer-Pfad zeigt.

---

## Teil C — PR-Split-Inventar: was lässt sich separat an Nezreka/dev geben

Klassifiziert wurden alle 328 eigenen Commits nach Dateipfad + tatsächlichen Symbol-Referenzen (nicht nur Pfad-Heuristik). Ergebnis: 166 reine Lib2-Commits, 61 nur Docs, 79 gemischt, 22 ganz ohne Lib2-Pfade — von letzteren wurden die folgenden 8 als sauber bis teilbar verifiziert.

### Sauber extrahierbar (bereit für eigene PRs)

| # | Commit | Was | Lib2-Abhängigkeit | Aufwand |
|---|---|---|---|---|
| 1 | `62a8848d` | **Security:** Torrent/Usenet-Downloadlinks (inkl. potenzieller Indexer-API-Keys in Magnet-URIs) gingen roh an den Browser und zurück; jetzt serverseitiges Opaque-Token (`candidate_store.py`). **Nicht mit `f6ace722` squashen** (der erweitert es lib2-spezifisch). | keine | clean cherry-pick |
| 2 | `ba4e8569` | Bundle-Completion (Torrent/Usenet): Fallback auf den Client-Staging-Ordner erst nach zwei stabilen Polls (Größe/Anzahl/mtime), verhindert Import halbfertiger Dateien | keine | clean (docs-Hunk droppen) |
| 3 | `7bdd5fdc` | Python-3.14-Race im Async-Event-Loop-Bridge-Start (`utils/async_helpers.py`) | keine | clean cherry-pick |
| 4 | `dbb3b84e` | Track-Nummer-Fallback nach Scan-Reihenfolge statt Kollaps auf Track 1 bei fehlender Nummerierung (echter Datenverlust vorher) | keine | clean cherry-pick |
| 5 | `d8f51a0f` | „Simple Downloads" (ohne Provider-Enhancement) bekommen jetzt die schon bekannten Tags (Titel/Artist/Album) geschrieben statt leer zu bleiben | keine | clean cherry-pick |
| 6 | `815253e8` | SABnzbd „Test Connection" prüft jetzt wirklich, ob die konfigurierte Kategorie existiert | keine | clean cherry-pick |
| 7 | `76085876` | Release-Quellen teilen sich nicht mehr das Retry-Budget | keine | trivial, clean |
| 8 | `c9a7df90` (nur `.py`-Hälfte) | `tag_writer.py`: Falsch-positive Retag-Warnungen bei reinen Datums-Format-Unterschieden oder Genre-Substring-Vergleichen — betrifft auch den Legacy-Import/Repair-Pfad, nicht nur lib2 | UI-Hälfte (`retag-modal.tsx`) droppen | leicht teilbar |
| 9 | `dcee311c` (nur Backend-Hälfte) | `core/automation/progress.py`: `update_progress()` clampt jetzt auf 0–100 — gemeinsamer Schreibpfad für Watchlist-Scan, Discovery-Sync, Wishlist-Processing | Frontend-Hälfte (`clampPercent` in lib2-UI) droppen | leicht teilbar |
| 10 | `ec64f83c` (mit trivialem Strip) | `delete_quality_profile()` hängt Referenzen nicht mehr in der Luft | ein `IF EXISTS`-Guard auf lib2-Tabellen, harmlos oder trivial entfernbar | fast clean |

**Vorschlag Reihenfolge:** #1 (Security) zuerst, dann #4 (Datenverlust-Fix), Rest nach Belieben.

### Geprüft und als untrennbar verworfen

- **Quarantine-Orphan-Fix bei manuellem Recover** (dein Beispiel a): `core/imports/quarantine.py`s neuer journal-basierter Recovery-Pfad wird ausschließlich von `core/acquisition/recovery.py` aufgerufen, das jeden Schritt über die neue `acquisition_quarantine_recoveries`-Tabelle + Request/Candidate/Download-Korrelationsobjekte protokolliert. `upstream/dev`s Route ruft noch den alten, einfachen `recover_to_staging` — unsere Route jetzt den neuen journalierten Pfad. Das gelöste Atomicity-Problem (Crash mitten im Move → Orphan/Duplikat) ist real und für upstream wertvoll, aber nicht ohne das Acquisition-Schema portierbar — bräuchte einen Rewrite ohne Ledger.
- **Quality-Profile-Auswahl im Suchfenster** (dein Beispiel b): Der UI-Teil (`webui/static/shared-helpers.js`) ist generisches Vanilla-JS ohne Lib2-Bezug. Der gewählte Wert wird aber **nur** gelesen, wenn `features.library_v2=true` ist (`web_server.py::start_missing_tracks_process`) — bei deaktiviertem Lib2 ist die Dropdown-Auswahl ein stiller No-op. Bräuchte eine neue, parallele Verdrahtung in den Legacy-Download-Pfad.
- Alle P0–P3-„native V2 tool integration"-Commits — entweder fügen sie `lib2_*`-Jobs hinzu oder **löschen physisch** Legacy-Repair-Jobs (deren native Ersätze nur in dieser Branch existieren). Isoliert gecherry-pickt wären die Löschungen eine reine Regression für upstream.
- Acquisition-Correlation-Refactor-Stack (`998efe8c`, `50a03b68`, `88f7b8ec` u. a.) — baut nur Audit-Trail-Einträge in `core/acquisition/*`-Tabellen, ohne die es funktional bedeutungslos ist.
- `4967aa6d` (Repair-Job-Scope nach Artist-Dateien) — Lib2-Artist-ID-basiert, kein Legacy-Äquivalent vorhanden.
- `5516548b` (Wishlist idempotente Add-Keys) — bündelt einen echten generischen DB-Fix mit einer lib2-spezifischen Änderung im selben Commit; müsste per Hunk-Split entwirrt werden (nicht abschließend geprüft).

---

## Zusammenfassung für die nächsten Schritte

1. **A1/A2/A3 zuerst fixen** — das sind stille Suite-weite Regressionen bzw. Datenverlust, kein Randfall.
2. **A6/A7/A8** sind konkrete User-sichtbare Bugs im gerade gemergten LV2-015/016-Cluster — sollten vor dem nächsten Merge nach `main` gefixt werden.
3. **Teil-C-Liste #1 (Security) und #4 (Datenverlust)** eignen sich am besten für sofortige separate PRs an Nezreka — beide unabhängig von Library v2 und mit klarem Nutzerschaden ohne den Fix.
4. Teil B (Cleanup) ist nicht blockierend, aber `monitor_sync.py`s stündlicher Reconcile-Job (Effizienz-Cluster) lohnt sich vor dem produktiven Rollout auf großen Bibliotheken.
