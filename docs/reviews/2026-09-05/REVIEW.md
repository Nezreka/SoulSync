# Branch-Review: library-overhaul gegen upstream/dev

**Reviewurteil: Dieser Stand ist noch nicht bereit für ein reibungsloses Upgrade und die Integration.** Die Prüfung ergab **25 bestätigte Branch-Befunde: vier P1 und 21 P2**. Zusätzlich fehlt ein relevanter, inzwischen upstream vorhandener Importfix. Architekturverbesserungen, ungenutzter Code und nicht grüne Prüfungen sind unten getrennt aufgeführt und in dieser Fehlerzahl nicht enthalten.

Die Grundentscheidung für einen eigenständigen Katalog mit getrennten Dateien, Monitoring-Absichten, Medienserver-Zuordnungen und persistenter Acquisition ist nachvollziehbar. Die belegten Schwächen liegen vor allem an den Übergängen: Ein geplanter Pfad gilt als Importerfolg, zwei Arten von Profil-IDs werden vermischt, Wishlist-Identitäten ändern zwischen Schritten ihre Bedeutung und Verbraucher projizieren dieselben Metadaten unterschiedlich. Hier sind gemeinsame Verträge und gezielte Integrationstests nötig.

Stand: **2026-09-05**. Sechs spezialisierte Agenten prüften Migration, Dateiimport, Wishlist/Watchlist/Sync, Acquisition/Qualität/Repair, UI und Katalogarchitektur. Die Koordination prüfte zusätzlich Listening/Search-Verträge, Testergebnisse, Upstream-Abgrenzung und Zusammenführung. Alle sechs Agenten sind geschlossen; weitere Agentenarbeit erfolgt gemäß Nutzerwunsch nacheinander. Graphify wurde nicht verwendet. **Anwendungscode, vorhandene Tests und Konfiguration blieben unverändert; nur Review-Artefakte wurden erstellt. Kein Commit oder Merge.**

## Die vier P1-Befunde

1. **FI-01 – Ein fehlgeschlagener Import wird dauerhaft als erfolgreich abgeschlossen.** [main_pipeline_bridge.py:261](/home/cyran/Projects/05_Soulsync_fork/core/acquisition/main_pipeline_bridge.py:261) ruft den Erfolgs-Callback schon bei gesetztem Zielpfad auf. Dieser wird vor Tagging, Upgrade-Prüfung und Veröffentlichung gesetzt. Nach späterer Ablehnung oder Move-Fehler kann deshalb der Task fehlgeschlagen sein, während Import, Request und Retry als abgeschlossen gespeichert werden; der Quarantäneeintrag verschwindet aus dem Importresultat. Die Repro bestätigt diesen Widerspruch ohne veröffentlichte Datei. Abschluss erst nach ausdrücklichem Pipeline-Erfolg und nachgewiesener aktiver Dateiregistrierung zulassen. [Details](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/file-import.md).

2. **MIG-01 – Die Migrationssperre greift für wichtige Blueprint-Routen nicht.** [web_server.py:376](/home/cyran/Projects/05_Soulsync_fork/web_server.py:376) vergleicht unqualifizierte Funktionsnamen mit qualifizierten Flask-Endpoints. Unter anderem erreichen Backup-Restore und Auto-Import-Approve ihre Handler trotz erforderlicher Migration; der native Kontrollendpoint wird korrekt gesperrt. Dadurch kann ein Restore die Datenbank während des Imports ersetzen. Die tatsächlichen Routennamen in den gemeinsamen Schutz aufnehmen und über reale Blueprint-Registrierung testen. [Details](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/migration.md).

3. **MIG-02 – Das Wiederherstellen eines Legacy-Backups lässt den nativen Katalog leer und Worker pausiert.** [database_admin.py:1018](/home/cyran/Projects/05_Soulsync_fork/api/database_admin.py:1018) initialisiert nach Restore das Schema, startet aber den bereits beendeten Bootstrap nicht erneut. Der Supervisor erkennt die erforderliche Migration und pausiert lediglich Worker. Die Repro liefert Restore-Erfolg bei null nativen Künstlern und ausstehender Migration. Restore muss denselben vollständigen Migrationsablauf wie Startup aktivieren oder einen verbindlichen Neustart auslösen. [Details](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/migration.md).

4. **ARCH-01 – MusicBrainz-Discography bricht bei der zweiten neuen Release-Group ab.** [discography.py:704](/home/cyran/Projects/05_Soulsync_fork/core/library2/discography.py:704) ergänzt einen unvollständigen Dictionary-Eintrag; der nächste Durchlauf erwartet das fehlende Feld musicbrainz_release_group_id. Ergebnis: reproduzierbarer KeyError, kein erreichter Commit, zurückgerollte Erweiterung. Geladene und neu angelegte Indexeinträge müssen denselben Feldvertrag besitzen; mehrere neue Gruppen in einem Lauf und anschließenden Wiederholungslauf testen. [Details](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/architecture.md).

P1 bezeichnet hier einen Fehler, der vor Integration behoben werden sollte. Es wurde **kein physischer Originaldateiverlust** durch die Importrepros nachgewiesen. Der unter MIG-03 belegte Verlust betrifft Katalogzeilen nach einem fehlerhaften administrativen Reset.

## Weitere bestätigte Branch-Befunde

Alle folgenden Einträge sind P2. Die Spezialberichte enthalten Trigger, Gegenproben, Aufruferketten, genaue Zeilenbereiche und Fixrichtungen.

| ID | Fundstelle | Reproduziertes Problem und Auswirkung |
|---|---|---|
| MIG-03 | [importer.py:1149](/home/cyran/Projects/05_Soulsync_fork/core/library2/importer.py:1149) | Manueller Reset committed die Kataloglöschung vor der Quellenprüfung. Auf einer nativen Installation ohne Legacy-Tabellen scheitert der Import anschließend und hinterlässt einen geleerten Katalog. Quelle vor jeder Löschung validieren. |
| MIG-04 | [importer.py:895](/home/cyran/Projects/05_Soulsync_fork/core/library2/importer.py:895) | Snapshot-Bereinigung bewahrt promovierte Spotify-/MusicBrainz-IDs, ignoriert aber beispielsweise Deezer-IDs in external_ids. Entfällt die alte Quellzeile beim Wiederholungsimport und fehlt ein anderer Erhaltungsgrund, werden weiterhin provideridentifizierte Einträge gelöscht. |
| MIG-05 | [importer.py:724](/home/cyran/Projects/05_Soulsync_fork/core/library2/importer.py:724) | Erste Artist-Adoption bewahrt zusätzliche IDs; Wiederholung desselben Schritts überschreibt sie mit den spärlicheren Legacy-IDs. Reproduziert: MusicBrainz-ID nach Replay NULL. Bei jedem Upsert dieselbe Erhaltungsregel verwenden. |
| FI-02 | [autolink.py:870](/home/cyran/Projects/05_Soulsync_fork/core/library2/autolink.py:870) | Neuimport auf denselben früher gelöschten Dateipfad aktualisiert die Zeile, aktiviert sie aber nicht. Normales Registrierungsgate lehnt ab, Recovery akzeptiert trotzdem die ID. Der zusätzliche SoulSync-Writer kann kompensieren; der Fehler ist für externe Medienserver relevant. |
| FI-03 | [main_pipeline_bridge.py:159](/home/cyran/Projects/05_Soulsync_fork/core/acquisition/main_pipeline_bridge.py:159) | Nach Crash zwischen Tagging und Move weicht die eigene Arbeitskopie vom Originalhash ab. Jeder Resume lehnt denselben Arbeitspfad ab, obwohl das unberührte Original vorhanden ist. Eigene Kopien mit Herkunft und Verarbeitungsphase verwalten. |
| SYNC-01 | [sync_service.py:597](/home/cyran/Projects/05_Soulsync_fork/services/sync_service.py:597), [watchlist_scanner.py:2515](/home/cyran/Projects/05_Soulsync_fork/core/watchlist_scanner.py:2515) | Neue Playlist-/Watchlist-Wishes verlieren das gewählte Qualitätsprofil. Persistenz speichert Default 3 statt Profil 4; normaler Reconcile korrigiert dies nicht. Profilparameter ausdrücklich weitergeben. |
| SYNC-02 | [mirror_outbox.py:108](/home/cyran/Projects/05_Soulsync_fork/core/library2/mirror_outbox.py:108) | Rücknahme prüft nackte Track-ID, Insert speichert dagegen track::album: erfüllte Wünsche bleiben stehen. Beim Unmonitoring eines Releases entfernt der breite nackte Key dagegen auch ein weiter gewünschtes anderes Release. |
| SYNC-03 | [mirror_outbox.py:233](/home/cyran/Projects/05_Soulsync_fork/core/library2/mirror_outbox.py:233) | Zwei Release-Wünsche mit gleicher Provider-Track-ID im selben Drain gelten als gegenseitig überholt; nur eines wird angelegt. Supersession muss die vollständige Release-Identität beachten. |
| SYNC-04 | [autolink.py:952](/home/cyran/Projects/05_Soulsync_fork/core/library2/autolink.py:952) | Qualitätsprofil-ID wird als Benutzerprofil-ID an Wanted übergeben. Bei Default ungleich 1 kann einem neuen Track die Admin-Projektion fehlen; die Upgrade-Liste übersieht ihn. Ein vorhandener Test erwartet sogar die falsche Kopplung. |
| ACQ-01 | [library_v2.py:651](/home/cyran/Projects/05_Soulsync_fork/api/library_v2.py:651) | Nach Runtime-Fehler, explizitem Retry und erneutem Suchfund derselben GUID liefert Grab den alten fehlgeschlagenen Versuch als HTTP-200-Erfolg. Keine neue Submission. Idempotenz an den Request-Versuch binden. |
| ACQ-02 | [main_pipeline_bridge.py:243](/home/cyran/Projects/05_Soulsync_fork/core/acquisition/main_pipeline_bridge.py:243), [retry_resume.py:92](/home/cyran/Projects/05_Soulsync_fork/core/acquisition/retry_resume.py:92) | Initialer Processor besitzt den versiegelten Upgrade-Intent, Retry-Task und Restart-Rebuild nicht. Spezifische Upgrade-Sperre und Prüfung entfallen; bessere Dateien gleichen Formats können am normalen Überschreibschutz scheitern. Intent in den Taskvertrag aufnehmen. |
| ACQ-03 | [client_monitor.py:419](/home/cyran/Projects/05_Soulsync_fork/core/acquisition/client_monitor.py:419) | Crash nach externer Usenet-Annahme, vor lokaler Korrelation: Startup erklärt submitting ohne Ergebnis-Marker für sicher fehlgeschlagen und entfernt es vor Adoption aus der offenen Menge. Submission-Beginn vor dem Netzaufruf dauerhaft festhalten. |
| ARCH-02 | [queries.py:1881](/home/cyran/Projects/05_Soulsync_fork/core/library2/queries.py:1881) | Fehlende überwachte Platzhalter werden doppelt gezählt. Repro: zwei erwartete Tracks, null vorhanden, vier fehlend. Fehlende Slots genau einmal berücksichtigen. |
| ARCH-03 | [reorganize_plan.py:179](/home/cyran/Projects/05_Soulsync_fork/core/library2/reorganize_plan.py:179) | Disc-Anzahl wird erst nach Ausfiltern aller Tracks ohne Datei bestimmt. Bekanntes Album mit zwei Discs wird vorübergehend als Single-Disc reorganisiert; die nächste Datei ändert den Zielpfad wieder. Disc-Metadaten vor Dateifilterung auswerten. |
| ARCH-04 | [retag.py:179](/home/cyran/Projects/05_Soulsync_fork/core/library2/retag.py:179), [reorganize_plan.py:159](/home/cyran/Projects/05_Soulsync_fork/core/library2/reorganize_plan.py:159) | Artist-Namensoverride erscheint korrekt in der UI, Retag und Reorganize verwenden weiter den alten Namen. Dieselbe effektive Metadatenprojektion für Anzeige, Tags und Pfade verwenden. |
| UI-01 | [library-v2-page.tsx:11601](/home/cyran/Projects/05_Soulsync_fork/webui/src/routes/library/-ui/library-v2-page.tsx:11601) | Abschluss der automatischen Migration invalidiert den Künstlercache nicht. Geöffnete Seite bleibt leer oder unvollständig, obwohl der Katalog fertig importiert wurde. |
| UI-02 | [library-v2-page.tsx:4579](/home/cyran/Projects/05_Soulsync_fork/webui/src/routes/library/-ui/library-v2-page.tsx:4579) | Wanted → Artists übernimmt eine zu hohe Seitennummer. Repro: zwölf Künstler, Seite 2 leer, keine sichtbare Pagination zur Rückkehr. Tabwechsel setzt Seite zurück. |
| UI-03 | [library-v2.api.ts:1835](/home/cyran/Projects/05_Soulsync_fork/webui/src/routes/library/-library-v2.api.ts:1835) | Nach Bootstrap-Fehler stoppt das Polling. Automatischer Wiederanlauf und Erfolg erreichen die UI nicht mehr. Wiederholbare Fehler mit Backoff weiter beobachten. |
| INT-01 | [lastfm.py:391](/home/cyran/Projects/05_Soulsync_fork/core/listening_import/lastfm.py:391) | Last.fm löst native IDs auf, schreibt aber die Legacy-Spalte. Cover-/Genreverknüpfung fehlt; späterer Legacy-Backfill kann bei ID-Kollision den falschen Track verknüpfen. |
| INT-02 | [stats/queries.py:476](/home/cyran/Projects/05_Soulsync_fork/core/stats/queries.py:476) | Chartdetails lesen die alte ID-Spalte, während Server/Webplayer native Events in lib2_track_id schreiben. Cover und Kataloglinks fehlen oder zeigen bei Kollision auf die falsche Zeile. |
| INT-03 | [library_check.py:69](/home/cyran/Projects/05_Soulsync_fork/core/search/library_check.py:69), [stats/queries.py:83](/home/cyran/Projects/05_Soulsync_fork/core/stats/queries.py:83) | Trackauflösung benutzt ausschließlich Albumartist. Vorhandener Muse-Track auf einer Various-Artists-Compilation gilt trotz Trackcredits und aktiver Datei als fehlend; Stats-Wiedergabeauflösung scheitert. |

## Abgleich mit upstream/dev

| Bezug | Fixierter Stand |
|---|---|
| Branch | library-overhaul |
| HEAD | 9fade33e54fa3185b75236e85e44531307b7c0df |
| Frisch gefetchtes upstream/dev | 8889a81c60ffb458a44bdbcf4bc7137d67140cb5 |
| Merge-Base | c92b8c87e694ce693251fde88d6c3a7c14d0d41c |
| Branch-Diff ab Merge-Base | 975 Dateien; 179.848 hinzugefügte, 63.323 entfernte Zeilen |
| Nicht gemeinsame Commits | 264 im Branch, 15 auf upstream/dev |

**FI-04 – fehlender Upstream-Importfix, separat von den 25 Branch-Regressionen:** Commit **75384d3d18d7e106a705e20c48fb801e48845f29** (fix(imports): catch lossless preview clips) schließt eine Lücke in [file_integrity.py:275](/home/cyran/Projects/05_Soulsync_fork/core/imports/file_integrity.py:275). Der Branch enthält dort den unveränderten Merge-Base-Stand. Eine unvollständige Lossless-Datei kann mit plausiblen Headerwerten den universellen Check passieren; der zusätzliche Decode-Check ist standardmäßig deaktiviert. Den Upstream-Codec-/Dichte-/Decode-Schutz samt Tests übernehmen und im Upgradepfad prüfen. **Statisch verifiziert, keine reale Audioreproduktion.** Dichte allein ist kein sicherer Ablehnungsgrund; den bestätigenden Decode-Schritt erhalten. [Details](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/file-import.md).

Die übrigen Upstream-Commits betreffen vor allem Video, Chat, Overlay-Sharing und Testausführung. Sie sind inventarisiert, aber nicht durch diesen Review integriert. Der Audit gelöschter Upstream-Pfade meldet **150 geprüfte Pfade, keine neue offene Entscheidung, 20 bereits erfasste Entscheidungen**. Das bestätigt den Ledger-Abgleich, keine lückenlose Feature-Parität. Den vorhandenen [Audit](/home/cyran/Projects/05_Soulsync_fork/scripts/deleted_path_upstream_audit.py) beim nächsten Upstream-Abgleich verbindlich ausführen.

Die textuelle Merge-Vorschau enthielt keine Konfliktmarker. Ein tatsächlicher Merge und dessen Prüfung fanden nicht statt; ein konfliktfreier Textmerge würde die hier nachgewiesenen Vertragsfehler nicht beheben.

## Architektur: erhalten und gezielt vereinheitlichen

**Erhalten:** Katalog und Dateibesitz sind sauberer getrennt als im alten Trackmodell. Zentrale Primary-Dateiauswahl, providerqualifizierte Medienserver-Mappings, getrennte manuelle Metadatenwerte, transaktionale Mirror-Outbox, persistierte Import-/Retry-Zustände, Löschjournal und gemeinsame Audio-Pipeline sind sinnvolle Bausteine. Partielle Provider-Snapshots unterdrücken Pruning; der Import besitzt Claim, Heartbeat, Checkpoints und Wiederaufnahme.

| Reihenfolge | Gemeinsamer Vertrag | Begründung aus den Befunden |
|---|---|---|
| 1 | **Importresultat und Dateiregistrierung** | Explizites Ergebnis mit Status, finaler Datei-ID und Pfad ersetzt verstreute Erfolgsflags. Veröffentlichung, aktive Registrierung und persistenter Abschluss müssen übereinstimmen. Autolink und SoulSync-Writer teilen denselben universellen Datei-Upsert. FI-01/02. |
| 2 | **Migrationskoordinator für Startup, manuell und Restore** | Quellenvalidierung, Claim, Import, Post-Import und Worker-Freigabe gemeinsam steuern. Replay muss Identitäten erhalten. MIG-01–05. |
| 3 | **Explizite Identitäten** | Benutzerprofil, Qualitätsprofil, Legacy-Track, nativer Track und Medienserver-Track brauchen eindeutig benannte Grenzen. Wishlist-Insert, Remove und Supersession verwenden denselben vollständigen Schlüssel. SYNC-01–04 und INT-01/02. |
| 4 | **Task-/Retry-Übergabe und Versuchsgeneration** | Gemeinsamer Builder erhält Qualität, Korrelation und prozesslokalen Upgrade-Intent. Expliziter Versuch und dauerhaft erfasster Submission-Beginn bestimmen Retry/Adoption. ACQ-01–03. |
| 5 | **Effektive Metadatenprojektion** | Trackcredits, Albumcredits, Overrides, Disc-Anzahl und fehlende Slots einmal fachlich definieren; Anzeige, Retag, Reorganize und Suche konsumieren diese Projektion. ARCH-02–04, INT-03. |
| 6 | **UI-Beobachtung von Hintergrundjobs** | Gemeinsamer Hook beobachtet manuellen und automatischen Import, Wiederanlauf und Abschluss und invalidiert die betroffenen Queries. UI-01/03. |

Die Library-Seite umfasst 11.684 Zeilen, ihr CSS 4.949 und der API-Adapter 2.340. Nach den Vertragskorrekturen entlang von Import-/Jobbeobachtung, Übersicht/Wanted, Details, Tracktabelle und Dialogen aufteilen. Das gebaute Haupt-JavaScript beträgt rund **2,58 MB / 721 KB gzip** und erzeugt eine Chunk-Warnung. Aufteilung und bedarfsgeladenes Routing sind Verbesserungskandidaten; keine reale Ladezeit oder Browser-Speicherkurve gemessen.

Tests sollten häufiger vollständige fachliche Übergaben prüfen. Einzelfunktions- und AST-Tests können denselben Irrtum wie der Code festschreiben: Der Autolink-Test akzeptiert die falsche Profil-ID, ein Prowlarr-Test verlangt zwei Aufrufe trotz gemeinsamer Hilfsfunktion, MusicBrainz-Tests verwenden nur eine neue Gruppe. Bestehende Unit-Tests um die belegten Übergänge erweitern.

## Dead Code und konkrete Vereinfachungen

| Kandidat | Nachweis / empfohlener Umgang |
|---|---|
| [music_database.py:11067](/home/cyran/Projects/05_Soulsync_fork/database/music_database.py:11067) | Zweiter existing-Zweig ist nach dem vorherigen Return bei identischer Bedingung unerreichbar. Direkter Bereinigungskandidat. |
| [pipeline.py:2603](/home/cyran/Projects/05_Soulsync_fork/core/imports/pipeline.py:2603) | Zweite Behandlung von _race_guard_failed dupliziert den vorherigen Block mit Return. Fehlerbehandlung an einer Stelle halten. |
| [catalogue_refresh.py](/home/cyran/Projects/05_Soulsync_fork/core/library2/catalogue_refresh.py) | refresh_preview/apply_refresh nur aus Tests aufgerufen; keine Produktionsverkabelung gefunden. Als experimentell kennzeichnen, bewusst integrieren oder entfernen. Kein behaupteter Laufzeitfehler. |
| [library-v2.api.ts:2292](/home/cyran/Projects/05_Soulsync_fork/webui/src/routes/library/-library-v2.api.ts:2292) | autoGrabBest nur durch Tests aufgerufen; aktive UI nutzt Scoped Search. Kandidat zur Entfernung; rankSearchResultQuality ist weiterhin aktiv. |
| [library-v2.api.ts:1568](/home/cyran/Projects/05_Soulsync_fork/webui/src/routes/library/-library-v2.api.ts:1568) | Frontend-Wrapper fetchLibraryV2AlbumReorganizeSources ohne Produktionscaller. Backend-Endpunkt separat bewerten. |
| [importer.py:183](/home/cyran/Projects/05_Soulsync_fork/core/library2/importer.py:183) | featured_from_title hat nur Export/Testreferenzen; aktiver Import nutzt anderen Helper. Externe Nutzung vor API-Entfernung klären. |
| [selection.py:159](/home/cyran/Projects/05_Soulsync_fork/core/quality/selection.py:159) | load_rank_candidates_by_quality nur in Tests verwendet. Bereits upstream vorhanden; mit aktivem profilbezogenem Loader vereinheitlichen oder entfernen. |
| [reorganize_bridge.py:119](/home/cyran/Projects/05_Soulsync_fork/core/library2/reorganize_bridge.py:119) | Quellenmodus ist intern wirkungslos, Repair-Fallback erwartet trotzdem no_source_id. Alte interne Zweige und unbenutzten Staging-Einstieg prüfen. Gemeinsame Mover-/Pfadhelfer weiterhin benötigt. |
| [pipeline_callback.py:20](/home/cyran/Projects/05_Soulsync_fork/core/acquisition/pipeline_callback.py:20) / [recovery.py:136](/home/cyran/Projects/05_Soulsync_fork/core/acquisition/recovery.py:136) | Doppelte Context-Korrelationsleser; gemeinsame Priorität und Validierung definieren. |
| Laufzeitabhängigkeit von importer.normalize_name | Reine Namens-/Identitätsnormalisierung in ein kleines gemeinsames Modul verschieben; Kataloglaufzeit benötigt nicht den großen Upgrade-Importer als fachliche Abhängigkeit. |

Der Wishlist-Compatibility-Shim, Legacy-Repair-Handler und große Teile des alten Reorganize-Moduls haben weiterhin produktive bzw. persistierte Kompatibilitätsaufgaben. Ihre vollständige Entfernung wäre durch diesen Review nicht gedeckt. Nur durch Tests aufgerufener Code ist außerdem nicht automatisch versehentlich überflüssig; die obigen Stellen sind konkrete Entscheidungskandidaten.

## Verifikation

| Prüfung | Tatsächliches Ergebnis |
|---|---|
| Vollständiges pytest | **18.228 bestanden, 3 fehlgeschlagen, 4 übersprungen, 7 deselektiert**, 700,81 s. [Log](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/evidence/pytest-full.log). |
| Einordnung der drei Python-Fehler | Eine neue veraltete Query-Anzahl-Assertion; zwei Fixture-/AST-Fehler auch auf unverändertem upstream/dev reproduziert. Keine dieser Assertions belegt einen weiteren Produktfehler. [Isolierter Branch-Lauf](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/evidence/pytest-failures-isolated.log), [Upstream-Lauf](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/evidence/upstream-pytest-failures.log), [Erklärung](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/integration.md). |
| Vollständiges Vitest | **7.795 Tests, 395 Dateien bestanden**, 156,96 s. [Log](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/evidence/vitest-full.log). |
| Produktionsbuild | **Erfolgreich**, aus archiviertem HEAD mit vorhandenen Dependencies; generierter Route-Tree identisch zum getrackten Stand. [Log](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/evidence/build.log). |
| Ruff / Compilecheck | **Erfolgreich**. [Ruff](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/evidence/ruff.log), [Compilecheck](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/evidence/compile.log). |
| Frontend-Format | **Nicht grün:** 47 Dateien, upstream 63. Neun beanstandete Library-Pfade gegenüber Upstream hinzugekommen. [Branch](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/evidence/format.log), [Upstream](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/evidence/upstream-format.log). |
| Frontend-Typecheck/Lint | **Nicht grün:** 413 Warnungen, neun Fehler; upstream 719 Warnungen, zwölf Fehler. Alle neun Branch-Fehler auch upstream vorhanden, keine zusätzlichen Typecheck-Fehler identifiziert. [Branch](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/evidence/oxlint.log), [Upstream](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/evidence/upstream-oxlint.log). |
| Gezielte Repros | Alle sieben Repro-Skripte erfolgreich ausgeführt. Ihre Assertions bestätigen **vorhandene Fehler**, keine behobenen Fehler. |

Die breite Testsuite wurde nach Abschluss nicht unnötig wiederholt; neue Repro-Ergänzungen wurden gezielt ausgeführt. Lokale Umgebung: Python 3.14.7, Node 26.8.1; CI verwendet Python 3.11 und Node 24. Die Prüfung mit installierten Dependencies ersetzt keinen sauberen CI-/Containerlauf.

| Teilreview | Bericht | Repro | Ausgabe |
|---|---|---|---|
| Migration | [migration.md](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/migration.md) | [migration_repro.py](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/migration_repro.py) | [Log](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/evidence/migration-repro.log) |
| Audioimport | [file-import.md](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/file-import.md) | [file-import-repro.py](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/file-import-repro.py) | [Log](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/evidence/file-import-repro.log) |
| Wishlist/Watchlist/Sync | [sync.md](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/sync.md) | [sync_repro.py](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/sync_repro.py) | [Log](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/evidence/sync-repro.log) |
| Acquisition/Qualität/Repair | [acquisition.md](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/acquisition.md) | [acquisition_repro.py](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/acquisition_repro.py) | [Log](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/evidence/acquisition-repro.log) |
| Katalogarchitektur | [architecture.md](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/architecture.md) | [architecture_repro.py](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/architecture_repro.py) | [Log](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/evidence/architecture-repro.log) |
| UI | [ui.md](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/ui.md) | [ui-repro.cjs](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/ui-repro.cjs) | [Log](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/evidence/ui-repro.log) |
| Listening/Search/Integration | [integration.md](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/integration.md) | [repro_listening_ids.py](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/evidence/repro_listening_ids.py) | [Log](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/evidence/repro-listening.log) |

## Was vor einem Upgrade noch nachzuweisen ist

Diese Abnahme ist **noch offen**, keine Behauptung bereits ausgeführter End-to-End-Tests:

1. P1-Befunde korrigieren; echte Blueprint-Sperre, Legacy-Restore nach beendetem Bootstrap, abgelehnten Import nach Pfadplanung und mehrteiligen MusicBrainz-Import absichern.
2. Import-Replay und Crash-Recovery mit separaten Testprozessen prüfen: vor/nach Batch-Commit und Checkpoint, nach Tagging vor Move, nach Usenet-Annahme vor lokaler Ergebnispersistenz. Originale, Quarantäne, Datei- und Requeststatus müssen danach übereinstimmen.
3. Upgrade auf einer Kopie repräsentativer Legacy-Daten einschließlich großen Katalogs, fehlender Dateien, mehrerer Provider, Compilations und Multi-Disc-Alben durchführen. Monitoring, Qualitätsprofile, IDs und Datei-Ownership vor/nachher vergleichen; Wiederholung darf keine zusätzlichen IDs verlieren.
4. Playlist-/Watchlist-Profil ungleich Default, globales Qualitätsprofil ungleich Benutzerprofil 1 sowie zwei Releases mit gleicher Recording-ID gemeinsam durch Monitor → Outbox → Wishlist → Import → Rücknahme testen.
5. UI während automatischem Import geöffnet lassen, temporären Fehler und erfolgreichen Retry auslösen, Cacheaktualisierung prüfen; Wanted-Seite 2 → kleine Künstlerliste sowie tatsächliche Darstellung und Dialogbedienung im Browser prüfen.
6. Upstream-Fix FI-04 übernehmen, verbleibende Upstream-Änderungen bewusst integrieren, stale Tests und neue Formatabweichungen bereinigen; im CI-Runtime-/Dependency-Stand neu prüfen.

Der Review deckt den gesamten Branch-Diff als Prüfraum und sechs vertiefte Risikobereiche ab. **Er ist keine Aussage, dass jede der 975 Dateien zeilenweise vollständig geprüft oder die Architektur insgesamt fehlerfrei bewiesen wurde.** Nicht durchgeführt: produktiver Upgrade, realer Media-Server-/Downloader-/Providerbetrieb, Audio-/NAS-/Cross-Device-End-to-End-Matrix, Lastmessung großer Datenbestände oder visuelle Browserprüfung. Die Repros verwenden synthetische SQLite-/React-Zustände und an den beschriebenen Grenzen Stubs bzw. unverändert extrahierte Funktionskörper. Diese Grenzen sind in jedem Teilbericht benannt.

