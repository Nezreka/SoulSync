# Spezialreview: Audio-Dateiimport — abgeschlossen

Stand: 2026-09-05. Branch `library-overhaul`, HEAD `9fade33e54fa3185b75236e85e44531307b7c0df`; Vergleich `upstream/dev` `8889a81c60ffb458a44bdbcf4bc7137d67140cb5`, Merge-Base `c92b8c87e694ce693251fde88d6c3a7c14d0d41c`.

**Ergebnis: ein P1 und zwei P2 als Branch-Regressionen; zusätzlich ein P2 als geerbte, inzwischen upstream behobene Integritätslücke.** Die drei Branch-Befunde wurden mit isolierten aktuellen Funktionskörpern reproduziert. Dabei ist kein physischer Verlust einer Originaldatei nachgewiesen: nachgewiesen sind falscher persistierter Importerfolg, eine weiterhin als gelöscht geführte neu importierte Datei und eine dauerhaft blockierte Wiederaufnahme.

## Umfang und Abgrenzung

Geprüft: `core/imports`, Übergabe aus `core/auto_import_worker.py`, `core/acquisition/main_pipeline_bridge.py`, Acquisition-Abschluss/Retry-Lifecycle, `core/library2/autolink.py`, primäre Dateizeilen sowie atomare Albumveröffentlichung. Schwerpunkt: Integrität, Qualitätsupgrades, Kopien/Move, Quelldateierhaltung, Quarantäne und Wiederaufnahme. Die Legacy-DB-Migration liegt beim anderen Review. **Den Verlust des Upgrade-Intents in `main_pipeline_bridge`/`retry_resume` behandelt der Acquisition-Reviewer; er ist hier kein zusätzliches Finding.** FI-01 betrifft den davon unabhängigen Erfolgsvertrag derselben Übergabe und soll bei der Zusammenführung nur einmal gezählt werden.

Keine Anwendung, Tests, Konfiguration oder Git-Zustände verändert; kein Graphify, keine Subagents, kein Zugriff auf Live-DB/Services. Geschrieben wurden ausschließlich dieser Bericht und `file-import-repro.py` im zugewiesenen Verzeichnis.

## Bestätigte Befunde

### FI-01 — P1: Geplanter Zielpfad schließt einen fehlgeschlagenen oder quarantänisierten Import als erfolgreich ab

**Primäre Fundstelle:** [core/acquisition/main_pipeline_bridge.py:261](/home/cyran/Projects/05_Soulsync_fork/core/acquisition/main_pipeline_bridge.py:261), **Zeilen 261–264**. Branch-Neueinführung.

**Auslöser:** Ein Acquisition-Bundle erreicht die gemeinsame Pipeline. Diese setzt `_final_processed_path` bereits bei der Pfadplanung, vor der Veröffentlichung ([pipeline.py:1881](/home/cyran/Projects/05_Soulsync_fork/core/imports/pipeline.py:1881), erneut 1917). Danach kann das Upgrade abgelehnt und die Arbeitsdatei quarantänisiert werden (1920–1950), oder der Move kann fehlschlagen. Der Wrapper setzt den flüchtigen Download-Task korrekt auf `failed`. Nach seiner Rückkehr ruft die Bridge dennoch `notify_pipeline_import_success` auf, sobald der **Pfadstring** existiert. Weder `_pipeline_import_succeeded` noch Ablehnungsflags oder Taskstatus werden geprüft.

**Konkrete Folge:** [pipeline_callback.py:252](/home/cyran/Projects/05_Soulsync_fork/core/acquisition/pipeline_callback.py:252), Zeilen 252–260, reicht den vermeintlichen Erfolg an `record_pipeline_file_completed` weiter. Auch dort gibt es keine Prüfung der echten Datei oder einer aktiven Library-Dateizeile. [imports.py:597](/home/cyran/Projects/05_Soulsync_fork/core/acquisition/imports.py:597), Zeilen 597–610, trägt den geplanten Pfad unter `processed` ein und entfernt dieselbe Datei aus `result.quarantined`. Wenn alle geplanten Dateien so erfasst sind, setzt 620–636 **Import und Acquisition-Request auf `completed`**; 645–648 schließt den Retry-State ebenfalls als `completed`.

`advance_import` priorisiert diesen persistenten Abschluss sogar vor `BridgeDispatchResult.waiting` (import_pipeline.py:244–255). Der laufende Task kann also `failed` anzeigen, während der persistente Auftrag erfolgreich abgeschlossen ist; weitere reguläre Importdurchläufe und aktive Retry-Walks nehmen ihn nicht mehr auf. Bei einem Move-Fehler fehlt die neue Datei im Ziel. Bei abgelehntem Upgrade bleibt die alte Datei bestehen und die neue in Quarantäne, während die persistierte Verarbeitung die Ablehnung bereits gelöscht hat. **Das ist kein nachgewiesenes Löschen der Originaldatei, sondern ein dauerhafter falscher Abschluss mit unterdrückter Wiederherstellung.**

**Repro/Evidenz:** `phantom_completion()` führt die aktuelle Bridge mit einem fehlgeschlagenen Processor aus und ruft deren echten `record_pipeline_file_completed` gegen SQLite `:memory:` auf. Ergebnis: `task=failed`, Bridge `waiting`, persistierter Import `completed`, `quarantined=[]`, Request-Transition und Retry-Abschluss `completed`. Es wird keine Datei veröffentlicht. Die Request-/Retry-Aufrufe werden aufgezeichnet; die Importzeile wird tatsächlich per aktuellem SQL geändert.

**Fixrichtung:** Nur einen ausdrücklich bestätigten Pipeline-Erfolg mit existierender Zieldatei und aktiver, passender Library-Dateizeile veröffentlichen. Alle Ablehnungen müssen davor ausgeschlossen sein. Erfolgsjournal möglichst an eine einzige Stelle nach dem Registrierungs-Gate verschieben; auch `side_effects.py:501–507` liegt aktuell vor `require_library_v2_registration` im aufrufenden Ablauf. Tests brauchen Fehler **nach** Setzen des Zielpfades. Der vorhandene Quarantäne-Bridge-Test setzt dieses Feld nicht und verfehlt genau den Fehlerfall.

### FI-02 — P2: Autolink reaktiviert erneut importierte Dateizeilen nicht; Recovery meldet dennoch Erfolg

**Primäre Fundstelle:** [core/library2/autolink.py:870](/home/cyran/Projects/05_Soulsync_fork/core/library2/autolink.py:870), **Zeilen 870–885**; ergänzend Auswahl 866–869. Branch-Neueinführung.

**Auslöser:** Eine frühere Dateizeile bleibt als `deleted`, `missing_confirmed` oder `quarantined` erhalten. Für denselben Track wird wieder auf denselben Pfad importiert. Konkreter Standardfall: Datei über Library-Dateilöschung entfernen, anschließend denselben Track neu herunterladen; die Löschung erhält die Zeile als `deleted` (`file_delete.py:429–446`). Auf einer Plex-/Jellyfin-/Navidrome-Installation greift der zusätzliche SoulSync-Writer nicht (`side_effects.py:533–535`).

**Fehler:** Autolink findet `(track_id, path)` unabhängig vom bisherigen Zustand und aktualisiert Qualitäts-/Verifikationsdaten, setzt aber **weder `file_state='active'` noch die Missing-Marker zurück**. `retire_replaced_files` überspringt den neuen `keep_path` ausdrücklich (track_files.py:410–411). Die Primary-Trigger wählen nur einen Dateizeiger; sie reaktivieren keine Zeile.

**Konkrete Folge:** Die erfolgreich verschobenen Bytes liegen am Ziel, ihre Zeile bleibt jedoch `deleted`. Der normale Registrierungs-Gate findet deshalb keine aktive Datei (`side_effects.py:300–304`). Bei verbrauchter Quelle wird die Exception-Recovery aufgerufen, die Autolink erneut ausführt und jede nichtleere zurückgegebene ID als Erfolg wertet ([pipeline.py:885](/home/cyran/Projects/05_Soulsync_fork/core/imports/pipeline.py:885), Zeilen 885–889); der Aufrufer setzt anschließend `_pipeline_import_succeeded` (2310–2315). Die Wiedergabe-/Ownership-Abfragen sehen weiterhin keine aktive Datei. Für `deleted` liest `primary_file_row` die Zeile überhaupt nicht (track_files.py:95–103); auch der normale Library-Dateiscan schließt sie aus (scan.py:84–86). Eine spätere vollständige Neuaufnahme kann den Zustand reparieren, die unmittelbare Import-/Scan-Kette tut es nicht zuverlässig.

**Repro/Evidenz:** `reactivation()` verwendet die aktuelle `LIB2_TRACK_FILES_DDL`, Autolink-UPDATE, den echten Registrierungs-Gate und die echte Recovery-Funktion mit SQLite `:memory:`. Eine Zeile `id=3, file_state=deleted` bleibt nach Autolink gelöscht; der normale Gate weist sie zurück, Recovery liefert trotzdem `True`, aktive Zeile bleibt `None`. Datei-I/O und Nebenwirkungen sind Doubles; der Zustand/UPDATE ist echtes SQLite. **Kein Verlust der neuen Audiodatei nachgewiesen; der Fehler betrifft Katalogzustand und Erfolgsmeldung, daher P2.**

**Fixrichtung:** Autolink muss nach verifiziertem Import die vorhandene Zeile aktivieren, Missing-Marker zurücksetzen und die gemeinsame Primary-Wahl auslösen. Normalabschluss und Recovery müssen denselben aktiven Registrierungsnachweis verwenden. Dass `_upsert_file` im alternativen SoulSync-Writer bereits reaktiviert (`media_server_sync.py:345–354`), zeigt eine konkrete Abweichung zwischen den zwei Schreibern; Tests müssen auch externe Medienserver abdecken.

### FI-03 — P2: Prozessabbruch nach dem Tagging macht die Acquisition-Arbeitskopie dauerhaft unretrybar

**Primäre Fundstelle:** [core/acquisition/main_pipeline_bridge.py:159](/home/cyran/Projects/05_Soulsync_fork/core/acquisition/main_pipeline_bridge.py:159), **Zeilen 159–166**. Branch-Neueinführung.

**Auslöser:** Die Bridge kopiert das Download-Original auf den deterministischen Arbeitspfad `<import-id>_<track-id>_<source-name>` (155–158). Die Pipeline verändert diese Kopie beim Metadaten-Enhancement (pipeline.py:1894), gegebenenfalls zusätzlich beim Downsampling. **Danach stürzt der Prozess ab oder wird neu gestartet, bevor die Datei verschoben und der Import abgeschlossen ist.** Der Import bleibt persistent `importing`; der Dispatch-Lease läuft aus (imports.py:283–305) und erlaubt die Wiederaufnahme.

**Konkrete Folge:** Beim nächsten Dispatch existiert die eigene Arbeitskopie bereits. `_stage_working_copy` akzeptiert sie ausschließlich, wenn Größe **und kompletter SHA-256** noch dem unberührten Download-Original entsprechen. Das eigene Tagging hat gerade diese Bedingung verletzt. Die Funktion wirft `existing acquisition working copy has different content`, bevor der Copier erreicht wird. Die Bridge liefert einen Fehler; `advance_import` verschiebt den Auftrag mit Backoff (import_pipeline.py:246–248), ohne die Arbeitskopie zu ersetzen oder kontrolliert fortzusetzen. Jede spätere Wiederholung trifft auf denselben Konflikt. Der Import bleibt offen, obwohl das vollständige Original weiterhin verfügbar ist.

**Abgrenzung:** Ein normal zurückkehrender Move-Fehler nach Tagging wird derzeit oft bereits von FI-01 fälschlich abgeschlossen. Der hier beschriebene **Crash vor dem Erfolgs-Callback** ist unabhängig davon erreichbar und erzeugt tatsächlich einen wiederaufzunehmenden Auftrag. Dieser Befund betrifft nicht den separat geprüften Verlust des Upgrade-Intents.

**Repro/Evidenz:** `working_copy()` führt `_stage_working_copy` und `_content_hash` mit einem virtuellen, gleich großen, aber umgetaggten Dateipaar aus. Zwei aufeinanderfolgende Versuche schlagen identisch fehl; der Copier wird nie gerufen; die Originalbytes bleiben unverändert. Keine Audio-/Crash-End-to-End-Simulation: Crashfenster und Wiederaufnahme wurden entlang der realen Aufrufer geprüft.

**Fixrichtung:** Eigene Arbeitskopien müssen eine persistierte Herkunft/Phase haben. Beim erneuten Claim eine bereits verarbeitete eigene Kopie kontrolliert fortsetzen oder erhalten und eine neue Kopie aus dem Original unter neuer Versuch-ID anlegen. Die Kollisionsprüfung gegen fremde Inhalte nicht pauschal entfernen. Ein Recovery-Test muss eine **vom vorherigen Pipeline-Durchlauf selbst veränderte** Kopie abdecken; vorhandene Tests behandeln nur identische Wiederverwendung und echte Fremdinhaltskollision.

### FI-04 — P2: Der bereits upstream behobene Lossless-Preview-Schutz fehlt im universellen Import

**Primäre Fundstelle:** [core/imports/file_integrity.py:275](/home/cyran/Projects/05_Soulsync_fork/core/imports/file_integrity.py:275), **Zeilen 275–278**; dazu Rückgabe 332–335 beziehungsweise 375–376. **Geerbte Lücke, keine neue Branch-Regression.** Diese Datei ist gegenüber der Merge-Base unverändert; upstream behebt sie in `75384d3d18d7e106a705e20c48fb801e48845f29` (`fix(imports): catch lossless preview clips`).

**Auslöser/Folge:** Eine FLAC-/ALAC-Preview hat einen plausiblen positiven Containerwert, etwa 180 Sekunden, enthält aber überwiegend Stille oder viel weniger dekodierbares Audio. Größe über 10 KiB, erfolgreicher Mutagen-Parse und passender Header-Dauerwert lassen `check_audio_integrity` bestehen. Der zusätzliche Decode-Guard ist standardmäßig deaktiviert (`pipeline.py:1204–1213`). Auch die Ersatzlängenprüfung verwendet bei positiven Headerwerten zunächst Mutagen (`pipeline.py:106–119`), sodass ein gefälschter voller Wert diesen Schutz ebenfalls passieren kann. Dateien aus Quellen ohne eigenen Preview-Schutz werden als vollständig importiert; bei erfüllten übrigen Gates können sie eine vorhandene Aufnahme ersetzen. Lokale Importe ohne erwartete Dauer passieren die universelle Prüfung ebenfalls.

**Upstream-Abhilfe:** Die neue Prüfung erkennt tatsächliche Lossless-Codecs, verwendet geringe Datendichte nur als Anlass zur bestätigenden Dekodierung und lehnt erst bei nachgewiesener Trunkierung/Stille ab. Sie darf nicht als bloße Mindestbitrate portiert werden: ruhige Musik komprimiert ebenfalls stark. Der Commit ergänzt außerdem die Zero-Length-/No-Decoder-Behandlung und vereinheitlicht `is_fake_lossless_bitrate` in `hifi_client.py`. Der Fork hat nur den alten HiFi-spezifischen Helfer; er schützt nicht alle Importquellen.

**Evidenz/Limit:** Quellcodevergleich des vollständigen Upstream-Commits und Verfolgung des aktuellen Integritäts-, optionalen Decode- und Ersatzpfades. Kein echter Preview-Download und keine Audio-End-to-End-Reproduktion durchgeführt. Fixrichtung: den Upstream-Schutz samt `tests/imports/test_lossless_density_guard.py` übernehmen und mit den Profil-/Upgrade-Pfaden dieses Branches prüfen.

## Architektur und Robustheit: Stärken

- Die Acquisition-Bridge bleibt ein Adapter in die gemeinsame Importpipeline. Dateiintegrität, Qualitätsfilter, AcoustID, Tagging, Quarantäne und Zielpfadbildung sind weitgehend an einer Stelle gebündelt.
- `safe_move_file` veröffentlicht über Rename; der Cross-Device-Fallback kopiert zunächst in eine versteckte temporäre Datei, führt `fsync` aus und ersetzt erst dann das Ziel (`file_ops.py:138–180`). Ein gescheiterter Copy überschreibt deshalb nicht vorzeitig die bekannte alte Zieldatei.
- Die Downloader-Bundle-Übergabe arbeitet mit echten Kopien statt Hardlinks (`album_bundle.py:340–377`); nachgelagertes Tagging verändert keine Torrent-Seeding-Quelle. Pfadgrenzen werden vor dem Staging geprüft (`main_pipeline_bridge.py:33–43`).
- Serverseitig versiegelte Upgrade-Intents, Track-Locks, ein effektiver Profil-Snapshot und erneute Prüfung vor Veröffentlichung reduzieren versehentliche oder konkurrierende Überschreibungen. Der Befund zum Intent-Transport liegt beim Acquisition-Reviewer.
- Qualitäts- und Integritätsablehnungen erhalten den Quarantänepfad und versuchen andere Kandidaten; ein fehlgeschlagener Quarantäne-Move führt nicht zum ersatzweisen Löschen der Quelle.
- Die atomare Albumveröffentlichung berücksichtigt Dateiseitencars und rollt veröffentlichte Dateien sowie bereits umgestellte DB-Zeiger bei späteren Fehlern zurück. Null aktualisierte Audiozeilen werden als Fehler behandelt. Einschränkung: Es bleibt eine Folge einzelner Dateimoves mit Rückabwicklung, kein einzelner atomarer Verzeichniswechsel.
- Auto-Import prüft Rückgabeflags **und** reale Zieldatei (`auto_import_worker.py:2061–2069`), statt jede normale Rückkehr als Erfolg zu zählen. Diese Datei und `file_ops.py` sind seit der Merge-Base unverändert; hieraus wurde keine neue Branch-Regression konstruiert.

## Tote Stellen und sinnvolle Vereinheitlichung

1. **Ein Abschlussvertrag für alle Caller:** Momentan konkurrieren Kontextflags, flüchtiger Taskstatus, aktive Dateizeile und Acquisition-Journal. Ein explizites Ergebnis mit Status, finaler Datei-ID und Pfad würde FI-01/FI-02 an ihrer gemeinsamen Ursache beseitigen. Der reine Pfadstring ist aktuell gleichzeitig Planung und Erfolgsbeweis.
2. **Ein Ownership-/Dateizeilen-Upsert:** Autolink und `record_soulsync_library_entry` → `media_server_sync._upsert_file` schreiben dieselben `lib2_*`-Dateien mit unterschiedlichen Reaktivierungsregeln. SoulSync-spezifische Server-Mappings von der universellen Dateiregistrierung trennen und denselben Upsert verwenden. Das ist eine konkret belegte Vereinheitlichung, keine Empfehlung für einen weiteren Parallelimporter.
3. **Ausgabe-Transformationen gemeinsam modellieren:** `_prepare_upgrade_artifact` (pipeline.py:579–637) und `_apply_profile_output_transforms` (961–1012) duplizieren Downsample-/Lossy-/Provenienzlogik. Der Unterschied, ob vor oder nach Veröffentlichung transformiert wird und ob ein Fehler den Upgrade verhindert, sollte ausdrückliche Policy bleiben; die gemeinsame Transformationsbeschreibung muss nicht doppelt implementiert werden.
4. **Toter Wrapper-Zweig:** Der zweite `_race_guard_failed`-Block (pipeline.py:2603–2615) ist nach dem ersten Block 2469–2479 mit `return` unerreichbar. Entfernen oder die einmalige Behandlung zentralisieren; kein eigenständiges produktives P3-Finding nötig.
5. **Kleine konkrete Bereinigung:** `check_flac_bit_depth` wird in pipeline.py:45 importiert, dort aber nicht verwendet. Die Funktion selbst kann ein Kompatibilitäts-API sein und sollte nicht ohne Caller-Prüfung entfernt werden. Die separaten `detect_mostly_silent`/`detect_incomplete_audio` besitzen unter `core/` keine weiteren Aufrufer; produktiv wird `detect_broken_audio` verwendet. Vor Entfernung externe/testseitige Nutzung prüfen.
6. **Upstream-Vereinheitlichung erhalten:** Den Lossless-Dichtehelfer aus HiFi in die universelle Integritätsprüfung übernehmen, ohne eine dritte separate Qualitätsdefinition einzuführen.

## Verifikation und Prüfgrenzen

Repro: [file-import-repro.py](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/file-import-repro.py).

```text
.venv/bin/python -B docs/reviews/2026-09-05/file-import-repro.py
REACTIVATION: {"id": 3, "file_state": "deleted", "is_primary": 0, "size": 123456} normal gate=reject, recovery=True, active row=None
PUBLICATION: task=failed, bridge=waiting, persisted import=completed, quarantined=[], retry=completed; no file was published
WORKING COPY: two retries both reject a retagged copy; copier calls=0; original bytes unchanged
```

Der Repro wurde hier erfolgreich ausgeführt und laut Parent unabhängig ebenfalls erfolgreich wiederholt. Er extrahiert aktuelle Funktionskörper per AST; lokale Imports sind durch explizite Doubles ersetzt. SQLite läuft ausschließlich in `:memory:`; keine Anwendungsinitialisierung, kein Live-Datenbestand und keine Audio-End-to-End-Verarbeitung. Die Reaktivierungsprobe installiert nicht sämtliche Produktions-Trigger; diese wurden separat gelesen und verändern den hier entscheidenden `file_state` nicht. Request-/Retry-Abschluss wird über die tatsächlich aufgerufenen Argumente belegt, nicht über eine vollständige Acquisition-Datenbank.

Laut Parent: **18.228 Python-Tests bestanden, drei dort separat untersuchte Fehler; 7.795 Vitest-Tests bestanden; Build erfolgreich.** Diese Gesamtläufe wurden hier nicht wiederholt. Die dortigen Fehler betreffen Active-Source-Blocklist-Suche, `no_results_reports_acquisition_retry_exhaustion` und einen gebundenen Prowlarr-Video-Endpunkt und werden nicht als eigene Importbefunde gezählt.

Nicht vollständig geprüft: reale Audio-/Codec-Matrix, echte NAS-/Cross-Device-Fehler und Parallelität, alle Drittanbieter-Downloader, Browser-UI, kompletter Scan-/Bootstrap-Lifecycle sowie Legacy-Migration. Das ist ein Spezialreview des Dateiimport-Scopes, keine Behauptung, alle 975 Branch-Dateien geprüft zu haben. Zum Abschluss zeigt `git status --short` ausschließlich den bereits gemeinsam verwendeten untracked Bereich `docs/reviews/`; HEAD und Upstream-Referenz entsprechen den oben genannten Werten.
