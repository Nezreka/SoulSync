# Spezialreview: Migration, Bootstrap und Legacy-Import

Abgeschlossen am 2026-09-05 für `library-overhaul`, HEAD `9fade33e54fa3185b75236e85e44531307b7c0df`. Vergleich: `upstream/dev` `8889a81c60ffb458a44bdbcf4bc7137d67140cb5`; Merge-Base `c92b8c87e694ce693251fde88d6c3a7c14d0d41c`.

**Ergebnis: zwei P1- und drei P2-Befunde, alle mit ausgeführter Reproduktion.** Anwendungscode, Tests, Konfiguration und Git-Zustand blieben unverändert. Kein graphify, keine Subagenten, keine Live-Datenbank und keine externen Dienste. Eigene Artefakte: dieser Bericht, `migration_repro.py` und das ausdrücklich angeforderte `evidence/migration-repro.log`.

## Befunde

| ID | Priorität | Präzise Fundstelle | Fehler |
|---|---|---|---|
| MIG-01 | P1 | `web_server.py:376-378` | Blueprint-Endpoints umgehen die Migrationssperre |
| MIG-02 | P1 | `api/database_admin.py:1018-1026` | Legacy-Restore reaktiviert den beendeten Bootstrap nicht |
| MIG-03 | P2 | `core/library2/importer.py:1149-1154` | Reset wird vor der Validierung der Legacy-Quelle committed |
| MIG-04 | P2 | `core/library2/importer.py:895-904` | Snapshot-Bereinigung ignoriert nicht-promovierte Provider-IDs |
| MIG-05 | P2 | `core/library2/importer.py:724-731` | Wiederholung einer Artist-Adoption löscht erhaltene Provider-IDs |

### MIG-01 — P1: Qualifizierte Blueprint-Endpoints in der Migrationsbarriere berücksichtigen

**Trigger:** Während einer erforderlichen oder laufenden Migration ruft ein berechtigter Nutzer `POST /api/database/backups/<filename>/restore` oder `POST /api/auto-import/approve/<item_id>` auf.

**Fehler und Folge:** Die Sperrliste enthält `restore_backup_endpoint` und `auto_import_approve` (`web_server.py:347-350`). Die tatsächlichen Flask-Namen lauten `database_admin.restore_backup_endpoint` und `auto_import.auto_import_approve`. Beide Pfade liegen außerhalb `/api/library/v2/`, deshalb gibt der Hook bei `376-378` frei, ohne `migration_required()` zu prüfen. Damit kann der Restore die Datenbank während des Imports ersetzen; Auto-Import kann Zustände ändern und einen Scan starten (`api/auto_import.py:124-134`). Die beabsichtigte exklusive Migration gilt für diese Routen nicht.

**Beleg:** Das Repro extrahiert den unveränderten Hook und seine Sperrliste per AST aus `web_server.py`, liest Blueprint-Namen und Routendekoratoren aus den wirklichen Modulen und registriert nebenwirkungsfreie Handler unter diesen Namen. Bei erzwungenem `migration_required=True`:

```text
GATE database_admin.restore_backup_endpoint 200 ... reached=True
GATE auto_import.auto_import_approve 200 ... reached=True
GATE lib2_mutation 409 ... Library upgrade in progress
```

Registrierung im Server: `web_server.py:20715` und `20980`, ohne Entfernen des Blueprint-Präfixes. Die Ausnahme für `lib2_import` ist dagegen korrekt: `api/library_v2.py:5265-5266` registriert diese Route direkt auf `app`.

**Fixrichtung:** Qualifizierte Namen verwenden oder die Migrationseigenschaft an der Routendefinition hinterlegen und zentral auswerten. Regressionstest muss die tatsächlichen Blueprint-Namen abdecken, einschließlich einer kontrollierten nativen Route.

**Einordnung:** Branch-Regression der neu eingeführten Migrationsbarriere. Die Blueprint-Struktur für Database-Admin existiert bereits in `upstream/dev`; dort gibt es diesen Library-v2-Migrationspfad nicht.

### MIG-02 — P1: Nach Legacy-Restore den Migrations-Lebenszyklus neu starten

**Trigger:** Der Startup-Importer ist bereits beendet, anschließend wird ein Backup ohne `lib2_*` eingespielt. Das betrifft sowohl den abgeschlossenen Upgrade-Import als auch eine native Installation ohne Legacy-Quelle, bei der der Startup-Thread mit `empty_source` ausstieg.

**Fehler und Folge:** Der Restore ersetzt die Datenbank und ruft nur `get_database()` zur Schema-Initialisierung auf. `database/music_database.py:1465-1474` legt das lib2-Schema mit `run_backfills=False` an. Der Handler zählt die daher noch leeren nativen Künstler und meldet Erfolg. Der einmalige Autostart endet bei `already_done`, `empty_source` oder erfolgreichem Import (`core/library2/bootstrap.py:733-741`; `web_server.py:18269-18272`) und wird ausschließlich beim Startup erzeugt (`web_server.py:22177-22178`). Der Supervisor bemerkt jetzt zwar eine erforderliche Migration, pausiert aber nur Worker; er startet keinen Import. Ergebnis: leerer nativer Katalog und dauerhaft gesperrte Katalogarbeit bis zu Neustart oder manuellem Import.

**Beleg:** Ausgeführt wurden der per AST isolierte echte `_autostart_library_v2_bootstrap_import`, der echte Restore-Funktionskörper, das echte Schema und `MigrationPauseSupervisor.tick()`. SQLite `backup()` arbeitet zwischen benannten In-Memory-Datenbanken. Dateipfade/Sidecar-Schreiben und globale DB-Initialisierung sind durch lokale Adapter ersetzt; `web_server` wird nicht importiert. Der reproduzierte Thread-Ausstieg ist konkret `empty_source`:

```text
RESTORE before autostart_returned=True state=waiting_for_source artists=1
RESTORE response ... artist_count: 0, success: True
RESTORE after state=pending legacy_artists=1 native_artists=0 migration_required=True worker_paused=True
```

Für `already_done` und erfolgreichen Import ist derselbe terminale `return` statisch nachvollzogen; diese beiden Varianten wurden nicht zusätzlich dynamisch simuliert.

**Fixrichtung:** Nach Restore denselben koordinierten Ablauf aus Backfills, Claim, Import, Post-Import und Worker-Freigabe erneut aktivieren. Alternativ muss ein verpflichtender Neustart Teil des Restore-Ablaufs werden. Eine bloße Erfolgsmeldung nach Erstellung leerer Tabellen genügt nicht.

**Einordnung:** Branch-Regression am neuen nativen Katalog. Das Hot-Restore-Muster ist upstream vorhanden; dort waren die restaurierten `artists` direkt der aktive Katalog und benötigten keinen zusätzlichen einmaligen Import.

### MIG-03 — P2: Fehlende Legacy-Quelle vor dem Reset ablehnen

**Trigger:** Der Admin ruft den weiterhin implementierten `POST /api/library/v2/import` mit `{"reset":true}` auf einer nativen Installation ohne Legacy-Tabellen auf. Frische Installationen ohne diese Tabellen sind ausdrücklich unterstützt (`core/library2/bootstrap.py:792-804`). Der manuelle Handler besitzt diesen Schutz nicht (`api/library_v2.py:5270`, `5324-5325`).

**Fehler und Folge:** `importer.py:1135-1139` löscht den Katalog; `1149` committed diese Löschung. Erst `1152-1154` validiert `_legacy_projection` die erforderlichen Quellspalten. Die Funktion wirft dann `legacy table is missing required columns: id, name`, nachdem Künstler, Tracks und Dateizuordnungen bereits unwiederbringlich aus diesem Datenbankstand gelöscht wurden. Physische Musikdateien werden dabei nicht gelöscht. Die Operation scheitert ohne importierbare Quelle und ohne automatischen Rollback der vorher committed Löschung.

**Beleg:** Echter `import_legacy_library(db, reset=True)` auf dem aktuellen Schema mit einem nativen Künstler, Album, Track und Dateieintrag, aber ohne Legacy-Tabellen:

```text
RESET_NO_SOURCE legacy table is missing required columns: id, name before=(1, 1, 1) after=(0, 0, 0)
```

Die Tupel zählen Künstler, Tracks und Dateieinträge. Hier wird **nicht** das ausdrücklich angeforderte Löschen an sich beanstandet, sondern der destructive Commit vor einer möglichen, erforderlichen Quellenprüfung. Die aktuelle Haupt-UI nutzt `startLibraryV2Import(false)` und blendet Reimport bei gefülltem Katalog aus (`webui/src/routes/library/-ui/library-v2-page.tsx:11580`, `11645`); deshalb P2 für den erreichbaren Admin-/Recovery-Pfad.

**Fixrichtung:** Alle benötigten Quelltabellen und Projektionen prüfen, bevor ein Reset beginnt bzw. committed wird. Ein automatischer Test muss bei ungültiger Quelle den ursprünglichen Katalog unverändert vorfinden.

**Einordnung:** Branch-spezifischer Fehler; upstream existieren weder dieser Importer noch das native Installationsmodell ohne Legacy-Katalog.

### MIG-04 — P2: Alle unterstützten Provider-Identitäten bei Snapshot-Bereinigung erhalten

**Trigger:** Bei einem Wiederholungsimport fehlt eine zuvor importierte Legacy-Zeile im neuen Snapshot, beispielsweise nach Quelländerung zwischen fehlgeschlagenem Import und Wiederaufnahme. Die native Zeile besitzt eine gültige Deezer-/Tidal-/Qobuz-Identität in `external_ids`, aber keine Spotify-/MusicBrainz-Spalte, keine ISRC und keinen zusätzlichen expliziten Monitor-Grund oder unabhängigen Dateieintrag.

**Fehler und Folge:** `_reconcile_legacy_snapshot()` verspricht, provideridentifizierte Metadaten vom Legacy-Snapshot zu lösen statt sie zu löschen (`importer.py:864-869`). Die Prüfung bei Tracks erkennt jedoch nur Spotify, MusicBrainz und ISRC (`895-904`). Bei Alben (`928-934`) und Künstlern (`957-968`) fehlt `external_ids` ebenso. Dadurch wird die gesamte Kette gelöscht, obwohl der Importer diese anderen Provider-IDs ausdrücklich übernimmt und andere Resolver sie als Identität verwenden. Spotify-/MusicBrainz-identifizierte Zeilen überleben dagegen als vom Legacy-Katalog gelöste Einträge.

**Beleg:** Echter Reconciler auf aktuellem Schema, je eine Künstler-/Album-/Trackzeile mit alten Legacy-Run-Markern und ausschließlich gültigen numerischen Deezer-IDs in `external_ids`:

```text
PROVIDER_PRESERVATION ... reconciled_tracks: 1, reconciled_albums: 1, reconciled_artists: 1
remaining= [0, 0, 0]
```

Das vorhandene `test_reconcile_detaches_provider_identity_instead_of_deleting_it` (`tests/library2/test_importer.py:1289`) deckt die promovierte Spotify-Identität ab, nicht diesen Fall. Die Reproduktion prüft direkt den echten Bereinigungsschritt; ein kompletter Quellwechsel-/Neustartzyklus wurde dafür nicht gestartet.

**Fixrichtung:** Die zentrale Provider-ID-Auswertung auch hier benutzen und nur anerkannte, nichtleere IDs als unabhängige Identität behandeln. Tests für mindestens Deezer-only Tracks, Alben und Künstler ergänzen.

**Einordnung:** Branch-spezifischer Preservation-Fehler in der neuen Snapshot-Bereinigung.

### MIG-05 — P2: Wiederholte Adoption eines Künstlers muss zusätzliche IDs erhalten

**Trigger:** Vor dem Import existiert bereits ein nativer Künstler mit Deezer- und MusicBrainz-ID. Die Legacy-Zeile kennt nur Deezer. Der erste Import adoptiert den vorhandenen Künstler und bewahrt dessen zusätzliche MusicBrainz-ID. Derselbe Artist-Schritt wird anschließend wiederholt, etwa nach einem Crash zwischen Batch-Commit und Checkpoint-Veröffentlichung (`importer.py:1066-1070`) oder bei nichtdestruktivem Reimport.

**Fehler und Folge:** `upsert_legacy()` vereinigt gespeicherte und Legacy-IDs nur bei `adopted=True` (`724-731`). Ab der Wiederholung ist der Künstler bereits über `legacy_artist_id` bekannt, daher ist `adopted=False`; `row_ids` enthält nur noch die spärlicheren Legacy-IDs. Das nachfolgende Update (`735-736`) ersetzt `external_ids` und setzt `musicbrainz_id` auf NULL. So verliert ein zulässiges Replay Identitäten, die der erste Lauf ausdrücklich erhalten hat. Spätere Providerauflösung, Erkennung und Zuordnung werden dadurch schlechter bzw. müssen neu ermittelt werden.

**Beleg:** Echter `_ArtistResolver`, aktuelle Tabellen, zwei neue Resolver-Instanzen mit demselben Legacy-Künstler und demselben `run_id`, Commit nach jedem Durchlauf:

```text
ADOPTION_REPLAY [
  {musicbrainz_id: 'a3200370-345c-46e0-8786-347b300ce267', external_ids: '{"deezer":"42","musicbrainz":"a3200370-345c-46e0-8786-347b300ce267"}'},
  {musicbrainz_id: None, external_ids: '{"deezer":"42"}'}
]
```

Der Verlust ist dynamisch nachgewiesen; der konkrete Prozessabbruch zwischen den zwei Commits wurde nicht durch ein OS-Signal simuliert. Das Crashfenster folgt direkt aus der Commit-Reihenfolge.

**Fixrichtung:** Identitäts-Erhalt beim Update nicht vom einmaligen Adoption-Zweig abhängig machen. Für alle Wiederholungen dieselbe Merge-/Konfliktregel verwenden; insbesondere darf fehlende Legacy-Information eine zusätzlich vorhandene Identität nicht löschen. Replay-Test mit einer nur im ursprünglichen nativen Künstler vorhandenen ID ergänzen.

**Einordnung:** Branch-spezifischer Idempotenzfehler; upstream enthält keinen solchen Importer.

## Architektur und Robustheit: Stärken

- **Persistierter Importzustand:** CAS-Claim mit Owner-Token, separate Heartbeats und Keepalive sowie `mark_done`/`mark_failed` vermeiden rein flüchtige Importzustände. Zustandsschreibzugriffe sind gegen alte Owner eingezäunt. Das ist eine gute Basis für Crash-Recovery; keine Behauptung einer vollständigen Einzäunung aller Katalogwrites.
- **Resume behält die Run-ID:** Der Import speichert Stage, Rowid und Run-ID und verwendet beim Wiederaufnehmen denselben Run. Damit gelten übersprungene, bereits geschriebene Zeilen bei der späteren Snapshot-Bereinigung weiter als gesehen. Vor dem Fortschrittsmarker wird committed; das verhindert Checkpoints für noch ungesicherte Daten. MIG-05 betrifft die notwendige Idempotenz eines Replay, nicht die grundsätzliche Marker-Reihenfolge.
- **Erhalt des Album-Monitorings:** Reset und persistierte Intent-Sicherung liegen in derselben Transaktion; Wiederaufnahme lädt den erhaltenen Intent und entfernt die Sicherung erst nach Wiederherstellung. Vorhandene Tests adressieren den Crash zwischen Löschung und Wiederherstellung (`tests/library2/test_importer.py:2239`).
- **Begrenzte Source-Projektionen und Keyset-Walks:** Der Import liest gezielte Spalten und Batchgrößen, akzeptiert Text-IDs und verarbeitet optionale Legacy-Spalten defensiv. Provider-IDs sind an mehreren Stellen sauber nach Quelle getrennt; mehrdeutige Künstlernamen werden nicht pauschal auseinandergerissen.
- **Gemeinsame Post-Import-Arbeit:** `post_import.py:30-76` bündelt Tracklist-, Tag- und Artist-Rollup-Precache für automatischen und manuellen Import. Optionale Cachefehler sollen committed Katalogdaten nicht zurückrollen.
- **Explizite Worker-Barriere:** Worker lassen sich vor ihrem ersten Start zurückstellen; der Supervisor nimmt nur selbst gesetzte Pausen zurück. Die Barriere behandelt bereits ein unbeanspruchtes erforderliches Upgrade als aktiv. MIG-01 und MIG-02 sind Integrationslücken um dieses ansonsten sinnvolle Modell.
- **Schema und große Backfills teilweise getrennt:** Startup ruft `ensure_library_v2_schema(..., run_backfills=False)` auf; größere Konvergenzläufe werden separat ausgeführt und teilweise in Batches committed und gecheckpointet. Das verbessert Startzeit und SQLite-Schreibverhalten gegenüber einem einzigen langen Startup-Commit.

## Vereinheitlichung und toter Code: konkrete Kandidaten

1. **Ein Import-Koordinator für Startup, manuell und Restore.** Claim, Keepalive, Fortschritt, Fehlerbehandlung, Reparaturen, Abschluss und Artwork-Start sind noch zwischen `bootstrap.py:744-875`, `api/library_v2.py:5265-5375` und dem Startup-Wrapper verteilt. Ein gemeinsamer Lifecycle würde die fehlende Restore-Reaktivierung, unterschiedliche Quellenprüfungen und die voneinander abweichenden Abschlusswege verringern. Post-Import ist bereits sinnvoll gemeinsam umgesetzt.
2. **Einheitliche Provider-ID-Erhaltung.** `_ArtistResolver`, `_merge_external_ids` und `_reconcile_legacy_snapshot` treffen eigene Entscheidungen darüber, welche Identität existiert und welche erhalten werden muss. MIG-04 und MIG-05 zeigen konkrete Unterschiede. Hier gemeinsame Auswertung und klar getrennte Merge-/Remove-Semantik etablieren.
3. **Fortschritts-/Checkpoint-Vertrag explizit machen.** Funktionsattribute `lib2_connection_aware` und `lib2_resume_aware` bilden einen versteckten Aufrufvertrag. Eine benannte Struktur bzw. ein kleiner Callback-Typ würde klarer trennen, welche Meldung nur Anzeige ist und welche einen wiederaufnehmbaren Stand bezeichnet.
4. **`featured_from_title` als Alt-API prüfen.** Repositoryweite Python-Suche findet die Funktion (`importer.py:183`), ihren Export und einen Unit-Test, aber keinen Produktionsaufrufer. Der Import benutzt `_featured_names_for_import` mit Identitätsschutz. Kandidat zum Internalisieren/Entfernen nach Prüfung externer Nutzer; kein behaupteter Laufzeitfehler.
5. **Schema-Dokumentation aktualisieren.** `run_backfills=False` wird als „DDL only“ beschrieben, führt jedoch weiterhin mehrere Datenmigrationen aus, etwa Profil-Provenienz und History-Backfills. Ebenso spricht `run_library_v2_backfills` von fünf Pässen, enthält inzwischen mehr. Besser eine klare Liste zwingender Startup-Migrationen und aufschiebbarer Datenarbeit pflegen. Kein eigenständiger Performancebefund ohne Messung.

## Reproduktion und Evidenz

Ausgeführt und erfolgreich beendet (Exit 0):

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python docs/reviews/2026-09-05/migration_repro.py
```

- Skript: `docs/reviews/2026-09-05/migration_repro.py`.
- Vollständiger erfolgreicher Lauf: `docs/reviews/2026-09-05/evidence/migration-repro.log`.
- Jeder Fehlerpfad enthält Assertions. Ein Python-Audit-Hook verbietet Dateischreibzugriffe, nicht speicherbasierte SQLite-Verbindungen, Netzwerkverbindungen und gestartete Subprozesse während der Reproduktion. Nebenwirkungsreiche Server-Globals/Sidecar-Ausgaben werden durch In-Memory-Adapter ersetzt.
- Die Flask-Gate-Reproduktion benutzt den echten Hook sowie echte Blueprint-/Routennamen mit Stub-Handlern. Die Restore-Reproduktion benutzt den echten Handlerkörper und SQLite-Backup, jedoch keinen realen Serverstart, keine Authentifizierungsprüfung und keine produktive globale Datenbankinitialisierung.
- Nach Abschluss: HEAD unverändert, `git diff --name-only` leer; `git status --short` zeigt ausschließlich das schon zuvor unversionierte `docs/reviews/`.

## Abdeckung und Grenzen

Geprüft wurden die fünf beauftragten Module mit Schwerpunkt auf Claim/Resume, Reset, Snapshot-Bereinigung, Schema-Erstellung, Post-Import und den zugehörigen HTTP-/Startup-/Restore-Aufrufern. Die genannten Primärfundstellen wurden am angegebenen HEAD mit aktuellen Zeilennummern abgeglichen. Das ist ein Spezialreview innerhalb der Gesamtprüfung, keine Vollprüfung aller 975 geänderten Dateien.

Vorhandene Resume-/Import-/Migrationstests wurden gezielt gelesen. Parent meldet den Gesamtlauf mit **18.228 bestandenen Python-Tests, drei Fehlern**, **7.795 bestandenen Vitest-Tests** und erfolgreichem Build. Die drei Fehler zu Blocklist-Suchquelle, `no_results_reports_acquisition_retry_exhaustion` und manuellem Prowlarr-Video-Endpoint bearbeitet der Parent; diese Zahlen wurden hier nicht nochmals selbst ausgeführt.

Keine Live-Upgrades, Backupdateien aus der Arbeitsinstallation, realen Dateitag-/NAS-Zugriffe, Providerdienste, großmaßstäblichen WAL-/Lock-Messungen oder echten Prozessabbrüche getestet. Für alten Zwischenständen der lib2-Schemata wurde keine exhaustive Upgrade-Matrix aufgebaut. Die genannten funktionalen Reproduktionen reichen für die fünf konkreten Befunde; sie beweisen keine allgemeine Fehlerfreiheit der übrigen Migration. Keine zusätzlichen P3-Bugs oder unbelegten Hypothesen aufgenommen.
