# UI-Spezialreview – abgeschlossen

Stand: 2026-09-05. `library-overhaul` HEAD `9fade33e54fa3185b75236e85e44531307b7c0df`, Vergleich `upstream/dev` `8889a81c60ffb458a44bdbcf4bc7137d67140cb5`, Merge-Base `c92b8c87e694ce693251fde88d6c3a7c14d0d41c`.

## Ergebnis

**Drei bestätigte P2-Befunde**, davon zwei im automatischen Migrationsablauf. Alle drei sind mit isolierten Reproduktionen am aktuellen UI-Code bestätigt. Kein P1 im geprüften UI-Ausschnitt nachgewiesen. Die neuen Bootstrap- und Wanted-Abläufe gibt es in dieser Form auf `upstream/dev` nicht; die Befunde betreffen Branch-Funktionalität, keine übernommenen Upstream-Fehler. Für einen reibungslosen Upgrade-Ablauf sollten insbesondere UI-01 und UI-03 vor der Integration behoben werden.

## Bestätigte Befunde

### [P2] UI-01: Nach automatischer Migration bleibt der geöffnete Katalog leer oder unvollständig

**Fundstelle:** `webui/src/routes/library/-ui/library-v2-page.tsx:11601–11607`.

**Auslöser:** Nach Upgrade `/library` öffnen, solange die automatische Migration läuft, und die Seite offen lassen. Die erste Künstlerabfrage enthält noch keine oder erst einige migrierte Künstler.

**Folge:** Der Fortschrittsstatus wird aktualisiert, der Künstler-Query aber nicht. Nach erfolgreichem Migrationsabschluss kann die Seite weiterhin „Your library is empty“/„Import library“ oder einen unvollständigen Katalog anzeigen. Erst ein neuer Abruf durch Navigation, Filterwechsel oder Reload stellt den tatsächlichen Katalog dar. Die Aussage „Artists appear here as they are migrated“ im Empty State wird damit ebenfalls nicht eingelöst.

**Belege:**

- `api/library_v2.py:5483–5490` liefert den automatischen Zustand als `bootstrap`; das separate `_import_state.running` bleibt dabei `false` (Initialisierung :36–39, Autostart `web_server.py:18266–18268`).
- `ImportButton` setzt `observedRunning` ausschließlich für `importState.running`. Bei automatischer Migration erfolgt deshalb der frühe Rücksprung :11607; die Katalog-Invalidierung :11614 wird nie erreicht.
- Das Status-Polling in `-library-v2.api.ts:1835–1841` berücksichtigt bereits `bootstrap.status`. Die Künstler-Query-Options (:1852–1866) haben kein eigenes Polling. `useLibraryChanged`/`useMaintenanceChanged` abonnieren keine Bootstrap-Abschlüsse. `webui/src/app/query-client.ts:8` deaktiviert Refetch bei Fensterfokus; bloßes Veralten des Caches startet ebenfalls keinen Abruf.

**Reproduktion:** Tatsächlichen `ImportButton` rendern, einen aktiven Künstler-Query mit leerem Ergebnis registrieren, Datenlieferanten auf einen vorhandenen Künstler umstellen und `bootstrap.running → done` abrufen, während `running=false` bleibt. Ergebnis: genau ein Künstlerabruf, null Künstler im Cache und wieder „Import library“.

**Fixrichtung:** Bootstrap-Zustände in dieselbe Abschlussbeobachtung aufnehmen und beim Abschluss den Katalog invalidieren. Soll der Katalog bereits während der Migration wachsen, zusätzlich gedrosselt aktualisieren. Bestehende Tests prüfen automatische Migration als statischen Banner-/Disabled-Zustand; die Abschluss-Invalidierung wird nur beim manuellen `running: true → false` geprüft.

### [P2] UI-02: Wechsel von Wanted-Seite 2 zu Artists versteckt vorhandene Künstler

**Fundstelle:** `webui/src/routes/library/-ui/library-v2-page.tsx:4577–4583`.

**Auslöser:** Beispielsweise 12 Künstler und mehr als 75 Wanted-Tracks: Wanted-Seite 2 öffnen, dann „Artists“ klicken.

**Folge:** Die Artists-Abfrage übernimmt `page=2`, obwohl der Künstlerkatalog nur eine Seite hat. Die API liefert null Zeilen bei `total_count=12,total_pages=1`. Die UI zeigt „Your library is empty“/„Import library“ und versteckt die Pagination. Ein Reload derselben URL behebt das nicht; Nutzer müssen etwa Filter/Sortierung ändern oder die URL korrigieren.

**Belege:** Der Artists-Tab setzt `section`, `q`, `artist` und `album`, aber nicht `page`. Der Wanted-Tab setzt dagegen ausdrücklich `page: 1` (:4600). `api/library_v2.py:1379–1420` reicht die Seite ohne Begrenzung auf die letzte vorhandene Seite weiter; `core/library2/queries.py:458` berechnet `(page-1)*limit`. Die Empty-State-Bedingung (:4410–4411) prüft nur die Zeilen der aktuellen Seite, die Pagination (:4540) erscheint nur bei mehr als einer Gesamtseite.

**Reproduktion:** Den tatsächlichen `LibrarySectionTabs`-Button mit Ausgangssuche `section=wanted,page=2` anklicken. Die neue Suche enthält `section=artists,page=2`. Der nachgelesene API-Offset ergibt für 12 Künstler null Zeilen und keine sichtbare Pagination. Der Button wurde mit React/JSDOM ausgeführt; die API-Offset-Auswertung ist eine reine In-Memory-Vertragsreproduktion, kein DB-Test.

**Fixrichtung:** Bei beiden Sektionswechseln `page: 1` setzen. Zusätzlich eine leere Ergebnisseite von einer leeren Bibliothek unterscheiden und einen Rückweg von überhöhten Seiten anbieten.

### [P2] UI-03: Nach Migrationsfehler beendet die UI das Polling vor dem automatischen Wiederanlauf

**Fundstelle:** `webui/src/routes/library/-library-v2.api.ts:1835–1841`.

**Auslöser:** Automatische Migration schlägt vorübergehend fehl; der Importstatus liefert `running=false`, `artwork_cache.running=false`, `bootstrap.status=failed`. Der Server startet später den nächsten Versuch.

**Folge:** Die geöffnete Seite bleibt dauerhaft beim Fehlerstatus, obwohl der Server wieder migriert oder bereits fertig ist. Der Text „It retries on its own“ wird angezeigt, aber der angekündigte Wiederanlauf ist nicht beobachtbar; Nutzer können dadurch unnötig einen manuellen Import versuchen, während der automatische Import bereits den Lock hält.

**Belege:** Die Intervallfunktion gibt für `failed` ausdrücklich `false` zurück. Der automatische Server-Loop `web_server.py:18263–18282` läuft nach nicht erfolgreichem Ergebnis mit Backoff weiter. Ohne erneuten Abruf kann der Query-Cache den nächsten `running`-Status nicht kennen und das Polling nicht wieder einschalten. Fensterfokus-Refetch ist global deaktiviert. Die Fehlermeldung mit Wiederanlaufversprechen steht in `library-v2-page.tsx:11409–11414`.

**Reproduktion:** Mit den tatsächlichen Query-Options und React Query zunächst `running` beobachten, dann `failed`. Anschließend liefert der simulierte Server wieder `running`; nach sechs möglichen Poll-Intervallen sind **null** weitere Statusabrufe erfolgt und die gerenderte Anzeige lautet weiterhin `failed`.

**Fixrichtung:** Für einen automatisch wiederholbaren Migrationsfehler mit angemessenem Backoff weiter beobachten oder einen garantierten Wiederanlauf-Event verwenden. Erst echte terminale Zustände beenden die Beobachtung. Dieser Fehler ist unabhängig von UI-01: Selbst mit korrekter Abschluss-Invalidierung erfährt die Seite hier nichts vom späteren Abschluss.

## Verifizierte Reproduktionen

Datei: `docs/reviews/2026-09-05/ui-repro.cjs`.

Aufruf aus dem Repository: `node docs/reviews/2026-09-05/ui-repro.cjs`.

Das Skript extrahiert aktuelle Funktionen unverändert per TypeScript-AST, transpiliert sie und führt sie mit den installierten React-/React-Query-/JSDOM-Bibliotheken aus. Datenlieferanten und Router-Navigation sind In-Memory-Fakes. Es gibt keine HTTP-Aufrufe, Live-Datenbanken oder Anwendungsschreibzugriffe. Die Assertions bestätigen das vorhandene Fehlverhalten; Exit 0 bedeutet hier erfolgreiche Fehlerreproduktion, nicht Fehlerfreiheit.

```text
UI-01 reproduced: {"bootstrap":"done","artistFetches":1,"cachedArtists":0,"actualArtists":1,"button":"Import library"}
UI-02 reproduced: {"search":{"section":"artists","q":"","page":2},"actualArtists":12,"returnedRows":0,"totalPages":1,"paginationVisible":false}
UI-03 reproduced: {"serverBootstrap":"running","renderedBootstrap":"failed","statusReadsAfterRetry":0}
```

## Architektur-Stärken

- **Explizite Identitäten für Playback:** `-library-v2.play.ts` und `webui/src/shell/library-globals.ts:318–331,428–438` unterscheiden native, Legacy- und Media-Server-Track-IDs. Native IDs werden nicht mehr irrtümlich als Server-IDs verwendet.
- **Geteilte Mutationseinstiege:** `useMonitorMutation` (:616–647) zentralisiert Monitoring samt Materialisierung fehlender Tracks und Cache-Invalidierung. `UnifiedFileRemovalDialog` (:3921) wird für Entity- und Dateiaktionen wiederverwendet.
- **Aussagekräftige Teilergebnisse:** Track-Bulk-Aktionen verwenden `Promise.allSettled` (:8540,8759), können Erfolge und Fehlschläge getrennt darstellen und gezielt erneut versuchen.
- **Asynchrone Vorgänge sind sichtbar:** Import/Artwork haben getrennte Fortschrittszustände; Scoped Search nutzt Sequenzschutz gegen verspätete Banner (:7163–7205); Künstler-Seiten teilen sich die Queue-Status-Abfrage mit expandierten Alben (:8888–8922).
- **Erhaltene Routeneinstiege:** `/library-v2` übernimmt beim Redirect den Querystring; Provider-/Artist-Links werden zentral im alten Artist-Routeneinstieg auf die neue Ansicht gelenkt. Das vermeidet divergierende Einzelkorrekturen an jedem Caller.

## Dead Code / Vereinheitlichung – Empfehlungen, keine P1-Befunde

1. **`autoGrabBest` prüfen/entfernen:** Definition `-library-v2.api.ts:2292–2310`, nur direkte Testaufrufe gefunden, keine produktiven Caller in `webui/src`/`webui/tests`. Die UI verwendet inzwischen serverseitige Scoped Search über `useScopedSearchBanner`. Der Kommentar behauptet weiterhin einen aktiven Automatic-Search-Pfad. Entfernen reduziert die Gefahr, später versehentlich einen abweichenden Downloadpfad wieder einzubauen. `rankSearchResultQuality` selbst ist aktiv und muss erhalten bleiben.
2. **`fetchLibraryV2AlbumReorganizeSources` prüfen/entfernen:** Definition `-library-v2.api.ts:1568`, nur API-Testreferenzen gefunden. Die aktuelle Reorganize-UI verwendet den globalen Quellenpfad. Das ist ein ungenutzter Frontend-Wrapper, keine Freigabe zum Entfernen des Backend-Endpunkts.
3. **Library-Modul nach Verantwortlichkeiten teilen:** Die neue `library-v2-page.tsx` umfasst 11.684 Zeilen, das CSS 4.949 und der API-Adapter 2.340. Sinnvolle Grenzen sind Import-/Jobbeobachtung, Künstlerübersicht/Wanted, Künstler-/Albumdetails, Tracktabelle und Dialoge. Ein gemeinsamer Observer für Bootstrap/Import/Jobs sollte Katalog-Invalidierung besitzen; Statusdarstellung darf nicht darüber entscheiden, ob Aktualisierungen erkannt werden. Monolith und vom Hauptreview gemeldetes fehlendes Chunking sind Architektur-/Ladezeitrisiken, hier keine isoliert nachgewiesenen P1-Fehler.
4. **Dialog-Grundstruktur konsolidieren:** `ModalShell` verwendet bereits den gemeinsamen `DialogFrame` (:895–927); `quality-profile-modal.tsx` und andere ausgelagerte Modals verwenden noch eigene Backdrops mit `useModalA11y`. Ein gemeinsamer Rahmen würde Fokus-, Escape-, Scroll- und Fehlerverhalten leichter einheitlich halten. Aus dieser Doppelstruktur allein wird kein konkreter Accessibility-Bug behauptet.

## Umfang, Upstream-Abgrenzung und Grenzen

Read-only Review von geänderten Library-Routen, Import-/Bootstrapstatus, Künstler-/Album-/Discovery-Ansichten, Teilen von Aktionen, Interactive Search, Settings, Playback, Cache-Invalidierung und UI-Vereinheitlichung. Caller und Backend-Verträge wurden für die Befunde verfolgt. Keine Subagenten und kein Graphify. Ausschließlich Bericht und Repro-Skript mit Präfix `ui` wurden geschrieben; Anwendungscode, Tests, Konfiguration und Git-Zustand blieben unverändert.

Keine visuelle Browser-/Live-Systemprüfung, kein vollständiger Durchgang aller 975 geänderten Dateien und kein Beweis vollständiger Feature-Parität. Layout wurde nur anhand der Struktur beurteilt; kein belegter visueller Layoutbefund wird erhoben. Die Repros isolieren React-Komponenten/Query-Options und ersetzen keinen vollständigen Router-/Browser-/Migrationsintegrationstest. Backend-Importkonsistenz, Migrationstransaktionen und die drei Python-Fehler liegen beim koordinierenden Review.

Übernommener Prüfstand der Koordination: Vitest **7.795 bestanden**, Produktionsbuild erfolgreich; Python **18.228 bestanden, drei Fehler** dort in Bearbeitung. Diese großen Prüfungen sowie Format-/TypeScript-Prüfungen wurden hier nicht wiederholt. Insbesondere werden bereits upstream vorhandene TypeScript-Fehler nicht als Branch-Regressionen gemeldet.
