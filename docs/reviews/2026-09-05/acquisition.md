# Specialist Review: Acquisition, Downloads, Quality und Repair

Abgeschlossen am 2026-09-05. Drei bestätigte P2-Befunde; kein belegter P1-Befund in diesem Teilreview. Read-only: Anwendungscode, Tests, Konfiguration und Git-Zustand unverändert; keine Live-Datenbank, Dienste, graphify oder Subagenten verwendet.

Reviewstand: `library-overhaul` / HEAD `9fade33e54fa3185b75236e85e44531307b7c0df`, Vergleich `upstream/dev` `8889a81c60ffb458a44bdbcf4bc7137d67140cb5`, Merge-base `c92b8c87e694ce693251fde88d6c3a7c14d0d41c`. Die drei Befunde liegen in der neuen Acquisition-Schicht; `core/acquisition` existiert weder am Merge-base noch im geprüften `upstream/dev`. Sie sind keine übernommenen Upstream-Fehler.

## Bestätigte Befunde

### ACQ-01 · [P2] Erneuter Grab nach Runtime-Fehler bleibt am alten fehlgeschlagenen Versuch hängen

**Primärer Ort:** [api/library_v2.py:651](/home/cyran/Projects/05_Soulsync_fork/api/library_v2.py:651), Zeilen **651–658**.

**Trigger:** Ein Download scheitert vor der Übermittlung an einem vorübergehenden Runtime-Problem, etwa einem unkonfigurierten Client oder einer nicht mehr auflösbaren Download-Referenz. Nach Behebung wird derselbe Request über `/retry` erneut gesucht; derselbe Release mit derselben GUID wird erneut ausgewählt.

**Folge:** Der Grab-Endpunkt antwortet mit HTTP 200, `success=true`, `created=false` und dem **alten fehlgeschlagenen Grab**, ohne einen neuen Client-Auftrag auszulösen. Der Request bleibt `candidates_ready`. Erneutes Klicken hilft nicht. Ein neuer Request oder anderer Kandidat kann das Problem umgehen.

**Ursache und Aufruferkette:** `retry_acquisition_request` erhöht `attempts` und setzt denselben Request auf `searching` ([workflow.py:389](/home/cyran/Projects/05_Soulsync_fork/core/acquisition/workflow.py:389), 389–395). Kandidatenregistrierung erhält bei gleicher Request-ID/GUID die Kandidaten-ID ([candidates.py:391](/home/cyran/Projects/05_Soulsync_fork/core/acquisition/candidates.py:391), 391–400). `find_request_candidate_grab` sucht anschließend ohne Filter auf Status oder Versuch ([grabs.py:235](/home/cyran/Projects/05_Soulsync_fork/core/acquisition/grabs.py:235), 235–241). Der Endpunkt kehrt bei jedem Treffer vor Vorbereitung und Submission zurück. Runtime-Fehler werden absichtlich nicht blocklistet; der erneute Versuch wäre nach der eigenen Fehlerklassifikation erlaubt.

**Reproduziert:** `acquisition_repro.py`, Ausgabe `REPRO 1 PASS`. Die Reproduktion verwendet echte Request-/Kandidaten-/Workflow-Funktionen und den unveränderten, per AST aus seiner verschachtelten Registrierung geladenen Flask-Handler. Nach Fehler → Retry → erneuter Same-GUID-Registrierung → Evaluation bestätigt sie HTTP 200, den alten `failed`-Grab, null Submission-Aufrufe und `candidates_ready`.

**Fix-Richtung:** Grab-Idempotenz an den aktuellen Request-Versuch binden. Wiederholte HTTP-Aufrufe desselben aktiven Versuchs dürfen denselben Grab erhalten; ein explizit neu gestarteter Versuch muss nach einem terminalen Fehler einen neuen Grab anlegen können. Eine DB-Constraint/atomare Vorbereitung sollte parallele Submissions innerhalb desselben Versuchs weiterhin verhindern.

### ACQ-02 · [P2] Acquisition-Retry verliert die serverseitige Upgrade-Autorisierung

**Primäre Orte:** [core/acquisition/main_pipeline_bridge.py:243](/home/cyran/Projects/05_Soulsync_fork/core/acquisition/main_pipeline_bridge.py:243), **243–252**, sowie [core/acquisition/retry_resume.py:92](/home/cyran/Projects/05_Soulsync_fork/core/acquisition/retry_resume.py:92), **92–99**.

**Trigger:** Ein automatisch beschafftes Acquisition-Bundle enthält einen Track, der einen vorhandenen Library-v2-Track verbessern soll. Der erste Kandidat landet in Quarantäne und der bestehende Worker versucht einen Ersatzkandidaten. Betroffen sind sowohl der unmittelbare Retry als auch Wiederaufnahme nach Neustart.

**Folge:** Der erste Pipeline-Aufruf besitzt den versiegelten `LibraryV2UpgradeIntent`; der Retry besitzt ihn nicht. Der Ersatzimport erhält deshalb weder die daran gebundene Track-Sperre und den aktuellen Upgrade-/Profil-Snapshot noch den Vergleich der tatsächlich gemessenen Qualität gegen die vorhandene Primärdatei. Eine konkrete Folge ist ein erfolgloses FLAC→FLAC-Upgrade: Trifft die bessere Datei denselben Zielpfad mit vorhandenen Tags, läuft sie in den normalen Überschreibschutz, der bei gleichem Dateiformat die neue Datei als gleichwertig behandelt und verwirft. Die spezifische Upgrade-Transaktion samt Prüfung auf zwischenzeitlich geänderte Primärdatei/Profil wird ebenfalls ausgelassen. Allgemeine Import-/Integritätsprüfungen bleiben vorhanden.

**Ursache und Aufruferkette:** `_pipeline_context` stellt den geprüften Intent ausschließlich auf der obersten Context-Ebene aus ([main_pipeline_bridge.py:135](/home/cyran/Projects/05_Soulsync_fork/core/acquisition/main_pipeline_bridge.py:135), 135–139). Beide Task-Konstruktionen übernehmen nur `context['track_info']` und gewöhnliche Task-Felder. Der Candidate-Verbraucher liest den Intent dagegen ausschließlich aus dem Task ([downloads/candidates.py:374](/home/cyran/Projects/05_Soulsync_fork/core/downloads/candidates.py:374), 374–375) und reicht ihn an den nächsten Context weiter (665–669); Staging macht dasselbe (349–350 und 479–480). Ohne Intent bleibt `_upgrade_snapshot` leer; der besondere Upgrade-Vergleich unter [imports/pipeline.py:1910](/home/cyran/Projects/05_Soulsync_fork/core/imports/pipeline.py:1910), 1910–1920, entfällt. Der normale Same-Format-Schutz liegt dort bei 2037–2062.

**Reproduziert:** `acquisition_repro.py`, Ausgabe `REPRO 2 PASS`. Die echte Bridge erhält ein serverseitig in SQLite angelegtes Import-Mapping. Der injizierte Processor sieht den gültigen Intent für Track 101, während der gleichzeitig erzeugte Retry-Task keinen enthält. Die echte `_rebuild_task`-Funktion erzeugt aus dem Retry-Journal ebenfalls keinen Intent. Dateistaging und Audio-Processor sind ausschließlich Beobachtungs-Stubs; der tatsächliche Audioimport und die physische Ersetzung wurden nicht ausgeführt. Die beschriebene Same-Format-Folge folgt aus der geprüften Aufruferkette.

**Fix-Richtung:** Den bereits aufgelösten Intent auf den initialen Task übernehmen. Beim Neustart den frisch aus dem serverseitigen Importplan erzeugten Intent ebenfalls auf den rekonstruierten Task setzen. Gemeinsamen Builder für diese Übergabe verwenden. Den Intent nicht als JSON-Autorisierung persistieren oder ungeprüft aus Client-Metadaten rekonstruieren.

### ACQ-03 · [P2] Absturz nach Usenet-Annahme verliert die Client-Korrelation beim Neustart

**Primärer Ort:** [core/acquisition/client_monitor.py:419](/home/cyran/Projects/05_Soulsync_fork/core/acquisition/client_monitor.py:419), **419–431**.

**Trigger:** Der Grab wurde lokal als `submitting` committed. SABnzbd/NZBGet akzeptiert `add_nzb`; der Prozess stürzt während des Aufrufs oder nach der Rückkehr, aber vor `record_external_submission`/Commit ab. Der lokale Datensatz hat noch keine `external_job_id` und keinen `submission_unknown`-Marker.

**Folge:** Beim Neustart erklärt `fail_stale_local_submissions` genau diesen Datensatz für sicher fehlgeschlagen, obwohl der externe Transfer weiterläuft. Grab und Request werden terminal `failed`. Der Grab verschwindet aus der offenen Menge; die normale Adoption und Import-Zuordnung können ihn nicht mehr reparieren. Ein tatsächlich heruntergeladenes Bundle bleibt dadurch ohne diesen automatischen Importpfad; ein neuer Request kann zusätzlich einen doppelten Transfer auslösen.

**Ursache und Aufruferkette:** Der echte Netzaufruf liegt in [submission.py:102](/home/cyran/Projects/05_Soulsync_fork/core/acquisition/submission.py:102), 102–115. `submission_unknown` wird erst nach einer gefangenen Ausnahme oder einem behandelten Persistenzfehler gespeichert; ein Prozessabsturz führt keinen dieser Handler aus. `_read_open_grabs` ruft die terminale Bereinigung vor dem Lesen der zu beobachtenden Grabs auf ([client_monitor.py:519](/home/cyran/Projects/05_Soulsync_fork/core/acquisition/client_monitor.py:519), 519–527). Spätere Adoption berücksichtigt nur offene Grabs und beim unbekannten Job nur ausdrücklich als `submission_unknown` markierte Zeilen (259–267). Das Fehlen dieses Markers beweist daher nicht, dass keine Submission begonnen wurde.

**Reproduziert:** `acquisition_repro.py`, Ausgabe `REPRO 3 PASS`. Eine tatsächlich vorbereitete, vor dem neuen Prozessstart datierte `submitting`-Zeile wird von der unveränderten Bereinigungsfunktion terminal `failed` und aus `open_grabs` entfernt. Die Client-Annahme ist das explizit simulierte Absturzfenster; kein externer Client wurde angesprochen. Der bestehende Test `test_runtime_fails_only_certain_pre_restart_local_submission` prüft die vorhandene Klassifikation, bildet aber die Annahme vor dem Crash nicht ab.

**Fix-Richtung:** Vor dem Netzaufruf einen dauerhaften Zustand „Submission begonnen/Ergebnis unbekannt“ committen und beim Neustart mit dem Client abgleichen. Nur Datensätze ohne gestartete Submission dürfen als rein lokal abgebrochen gelten. Wo möglich einen stabilen Korrelationsschlüssel an den Client übergeben; bei nicht eindeutiger Zuordnung sichtbar offen bzw. zur Prüfung lassen.

## Architektur: Stärken

- **Persistente fachliche Identität:** Requests, Kandidaten, Entscheidungen, Grabs und Imports sind getrennt. Der Client bleibt Quelle für flüchtige Transferwerte. `requests.transition_request` prüft erwartete Zustände; `grabs.update_grab` schützt terminale Zustände vor späteren Statuswechseln. Das ist eine tragfähige Grundlage für Retry und Neustart, trotz der oben belegten Übergangslücken.
- **Gemeinsame Import- und Retry-Ausführung:** `main_pipeline_bridge` und `retry_resume` verwenden den vorhandenen Importer bzw. Download-Worker. Die Acquisition-Schicht führt keine zweite Audioverarbeitung ein. Das verringert Abweichungen bei Qualität, Quarantäne und Metadaten.
- **Qualitätsprofil wird früher berücksichtigt:** `downloads/task_worker.py:697–704` übernimmt Kandidatenordnung und Suchmodus aus dem Profil des einzelnen Tasks; `quality/selection.py:142–156` lädt dafür die gewählte Profil-ID. `targets_from_profile` vereint die Umwandlung alter und neuer Profildarstellungen.
- **Autoritätsgrenzen sind explizit:** Kandidaten werden gegen Request, Profil und Ablaufzeit aufgelöst. Der Token-Store prüft Profil sowie angeforderte Ergebnisart und vorhandene Entity-Bindungen. Der Upgrade-Intent ist ein versiegeltes, prozesslokales Objekt statt eines vom Browser konstruierbaren JSON-Flags. ACQ-02 betrifft seine Weitergabe, nicht eine nachgewiesene Fälschbarkeit.
- **Cancel und Reparatur haben dauerhafte Zustände:** Usenet-Abbrüche bleiben bis zur bestätigten Entfernung `cancel_pending`; Netzwerkzugriffe erfolgen außerhalb der SQLite-Transaktion. Repair-Fixes beanspruchen Findings per atomarem UPDATE (`repair_worker.py:2189–2196`). Schlägt die nachfolgende Library-v2-Synchronisierung fehl, bleibt das Finding als Retry-Anker offen. Die Repair-Registry benennt Datenbasis und Library-Auswirkungen zentral.
- **Quality-Repair nutzt den gemeinsamen Queue-Pfad:** `_fix_quality_below_cutoff` führt über `mirror_projected_tracks_wishlist` zur Mirror-Outbox, statt eine separate Download-Implementierung aufzubauen. Quality-Backfill verwendet die gemeinsame Rescan-Funktion und berücksichtigt aktive Dateien.

## Dead Code und Vereinheitlichung

Diese Punkte sind Wartungskandidaten, keine zusätzlichen belegten Laufzeitfehler.

1. **Unbenutzter Produktionshelfer:** `core/quality/selection.py:159–176`, `load_rank_candidates_by_quality`, hat bei repositoryweiter Suche ausschließlich vier Testaufrufe, keine Produktionsaufrufer. Der Worker liest denselben Wert bereits profilbezogen in `_candidate_ordering`. Entfernen oder mit dem profilbezogenen Policy-Loader zusammenführen. Der Helfer existiert bereits in `upstream/dev`; sein bloßes Vorhandensein ist keine Branch-Regressionsmeldung.
2. **Task-/Context-Builder vereinheitlichen:** Bridge, Restart-Resume, Master, Candidate-Dispatch und Staging kopieren Qualität, Library-Identität und Upgrade-Intent separat. Ein kleiner typisierter interner Übergabevertrag mit gemeinsamem Builder würde gerade den in ACQ-02 nachgewiesenen Feldverlust vermeiden.
3. **Korrelationszugriff zusammenführen:** `_context_value` ist in `acquisition/pipeline_callback.py:20` und `acquisition/recovery.py:136` dupliziert; `_acquisition_task_ref` in `downloads/candidates.py:219` und `downloads/monitor.py:132` umhüllt denselben Retry-State-Helper. Gemeinsame Leser sollten Top-Level-/track_info-Priorität und Validierung festlegen.
4. **Repair-Altkompatibilität bewusst begrenzen:** Die früheren Quality-/Discography-Scanner sind entfernt, `_fix_legacy_quality_upgrade` und `_fix_legacy_discography_track` bleiben jedoch über `_fix_handlers` erreichbar. Deshalb nicht als toten Code löschen: Sie bedienen ältere persistierte Findings. Nach dokumentierter Migration/Retirement solcher Findings lassen sich diese Adapter aus dem großen `repair_worker.py` entfernen oder in ein Kompatibilitätsmodul verschieben.
5. **Doppelte Lifecycle-Schreiber reduzieren:** Client-Monitor, persistenter Reconciler, Pipeline-Callbacks und Legacy-Korrelation schreiben gemeinsame Grab-/Request-Zustände. Die bestehenden Transition-Funktionen sollten Versuchsgeneration und Submission-Ungewissheit verbindlich besitzen, damit diese Regeln nicht in einzelnen API-/Worker-Zweigen auseinanderlaufen.

## Reproduktion und Prüfgrenzen

Repro-Skript: [acquisition_repro.py](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/acquisition_repro.py).

```bash
.venv/bin/python -B docs/reviews/2026-09-05/acquisition_repro.py
```

Ergebnis: alle drei `REPRO … PASS`, Exit 0. Das bedeutet **Fehler reproduziert**, nicht Fehler behoben. Das Skript verwendet SQLite ausschließlich im Speicher, blockiert jeden Default-/Live-DB-Zugriff ausdrücklich, ersetzt Konfiguration/Logging vor App-Imports und startet keine Dienste. Der isolierte Flask-Handler und die Cleanup-Funktion werden aus dem aktuellen Quelltext geladen; die vollständige Serverregistrierung wird nicht ausgeführt. Audio- und Dateimutationen werden nicht simuliert als wäre ihr Endergebnis gemessen worden.

Geprüft wurden insbesondere Acquisition-Workflow/Requests/Kandidaten/Submission/Monitor, Retry-Journal/Resume, Pipeline-Bridge/-Callbacks, Korrelations- und Cancel-Grenzen, die geänderten Download-Worker-/Candidate-/Profil-Übergaben sowie ausgewählte Repair-Registry-/Claim-/Quality-/Outbox-Grenzen. Der Bericht ist kein vollständiger Einzeldatei-Audit aller 975 Branch-Dateien. Keine Live-Usenet-/Soulseek-/Streaming-Transfers, keine realen Prozess-Kills, kein mehrprozessiger Belastungstest, kein Upgrade einer produktiven Datenbank und keine UI-Browserprüfung. Audio-Ingestion, Autolink-/Deleted-State und Working-Copy-Inhalte werden separat geprüft; kryptographische End-to-End-Prüfung sämtlicher signierter API-Identitäten liegt ebenfalls außerhalb dieser Reproduktionen. Für weitergehende Race- und Queue-Fairness-Fragen wird keine Fehlerfreiheit behauptet.

Parent-Validierung, mitgeteilt und hier nicht erneut ausgeführt: initial 18 228 Python-Tests bestanden, drei Fehler isoliert; der Acquisition-Retry-Exhaustion-Test erwartete veraltet drei statt zwei Suchqueries nach einer Upstream-Änderung und ist kein zusätzlicher Laufzeitbefund dieses Berichts. Die übrigen isolierten Fehler bearbeitet der Parent. Vitest: 7 795 bestanden; Build bestanden.
