# Library V2 — Bugs, Findings und Root-Cause-Register

> Branch-Split (22. Juli 2026): Playlist-spezifische Diagnosen bleiben hier
> als historische Begründung erhalten, sind aber kein aktiver Scope von
> `library-overhaul`. Die UI liegt auf `library-v2-playlist-ui`; native
> Watchlist-/Playlist-Quality-Profile gehören zur Foundation.

Dieses Dokument bewahrt Fehlerbilder, technische Ursachen, Auswirkungen,
Fixverträge und Reproduktionsideen. Es sagt bewusst **nicht**, ob ein Finding
offen oder erledigt ist. Der einzige Statusort ist
[library-v2-status.md](library-v2-status.md). Produktwünsche wie die fehlende
Acquisition-Review-UI stehen in
[library-v2-features.md](library-v2-features.md), nicht als Bug in diesem
Register.

Wo mehrere historische Audits denselben Fehler fanden, wird die Diagnose nur
einmal ausführlich geführt und über Alias-IDs referenziert. Dadurch geht kein
technisches Detail verloren, ohne dieselbe Statusgeschichte mehrfach zu
duplizieren.

---

## 1. Review-Findings vom 22. Juli 2026

### <a name="find22-01"></a> Finding 1 — Nur das tatsächlich verschobene File aktualisieren

**Ort:** `core/reorganize_runner.py`

Wenn ein Legacy-backed V2-Track mehrere File-Rows besitzt, wählte der
`legacy_track_id`-Zweig alle Files dieses Tracks. Das anschließende Update
schrieb dadurch auch Secondary-/native Files auf den Pfad des einen
verschobenen Legacy-Files um. Mehrere reale Dateien kollabierten im Katalog
auf denselben Pfad.

**Korrekturvertrag:** Der Reorganize-Plan trägt die konkrete `lib2_file_id`
oder löst exakt über Legacy-File-ID plus alten Pfad auf. Nur diese Row bekommt
den Zielpfad. Der Regressionstest benötigt einen Track mit mindestens zwei
Files und beweist, dass das nicht bewegte File unverändert bleibt.

### <a name="find22-02"></a> Finding 2 — Acquisition-Import vor Dispatch exklusiv claimen

**Ort:** `core/acquisition/import_pipeline.py`

Periodischer Monitor und Admin-Resume konnten denselben Import gleichzeitig
lesen und dispatchen. Beide Prozesse staged dieselben Matches, überschrieben
Runtime-Task-IDs und liefen in konkurrierende Read/Modify/Write-Callbacks.
Eine Datei konnte doppelt bewegt oder ein Processed-Eintrag verloren werden.

**Korrekturvertrag:** Ein atomarer per-import Claim bzw. Lease liegt vor jedem
Dispatch. Claim-Owner und Release/Expiry sind persistiert. Tests starten zwei
Caller an einer Barrier und erwarten genau einen Dispatcher.

### <a name="find22-03"></a> Finding 3 — Automatische Expiry-Deletes durch den V2-Lifecycle führen

**Ort:** `core/repair_jobs/expired_download_cleaner.py`

Der direkte automatische Delete-Pfad umging `sync_repair_change`. Die Datei
und Legacy-Zeile verschwanden, `lib2_track_files` blieb aber aktiv und Wanted
wurde nicht neu berechnet. V2 zeigte Besitz und konnte einen Ersatzdownload
unterdrücken.

**Korrekturvertrag:** Jeder automatische Delete läuft über dieselbe
File-Lifecycle-, Wanted-, Outbox- und History-Grenze wie ein manueller Delete.
Erst nach erfolgreicher Synchronisation wird der Fund als bearbeitet gezählt.

### <a name="find22-04"></a> Finding 4 — Bootstrap in begrenzte Transaktionen teilen

**Ort:** `core/library2/importer.py`

Artist-, Album-, Track-, File-, Reconcile- und Wanted-Writes blieben bei
großen Libraries bis zu einem einzigen Abschluss-Commit in einer SQLite-
Write-Transaktion. Während der Server bereits Traffic annahm, konnten andere
Writes das Busy-Timeout überschreiten; Heartbeats waren innerhalb derselben
Transaktion für andere Connections unsichtbar.

**Korrekturvertrag:** Restart-sichere Batches committen; finale Reconciliation
separat ausführen; Heartbeat außerhalb der langen Arbeitstransaktion sichtbar
halten. Failure-Injection nach jedem Batch muss einen idempotenten Neustart
erlauben.

### <a name="find22-05"></a> Finding 5 — Legacy-Rows beim Bootstrap streamen

**Ort:** `core/library2/importer.py`

`SELECT *` plus `fetchall()` hielt bei großen Libraries sämtliche Legacy-Rows
einschließlich Lyrics und Enrichment-Texten im Speicher, zusätzlich zu allen
Artist-/Album-/File-Maps. Ein 320k-Track-Bestand konnte hunderte Megabyte oder
mehr belegen und beim Pflicht-Erststart vom Host beendet werden.

**Korrekturvertrag:** Nur benötigte Spalten auswählen und bounded iterieren.
Ein Skalierungstest misst Peak-Speicher und stellt sicher, dass er nicht linear
mit dem vollständigen Textpayload wächst.

### <a name="find22-06"></a> Finding 6 — Beliebige Artwork-Fetch-Ziele ablehnen

**Ort:** `api/library_v2.py`

Eine eingereichte URL ging mit Redirects direkt an `requests.get`; Scheme,
Ziel-IP, private/loopback Netze und Response-Größe waren nicht begrenzt.
Dadurch waren SSRF gegen lokale/Cloud-Metadata-Dienste und Memory Exhaustion
durch große Bodies möglich.

**Korrekturvertrag:** Bevorzugt serverseitige Candidate-Tokens. Andernfalls
jeden Redirect neu validieren, nur erlaubte Schemes akzeptieren, private
Netze blockieren und Body sowie Bilddimensionen gestreamt hart begrenzen.

### <a name="find22-07"></a> Finding 7 — Enrich-Matching verlangt Artist-Kontext

**Ort:** `core/library2/native_enrich.py`

Bei common Titles wie „Home“, „Intro“ oder „Greatest Hits“ verglich das
Ranking nur den Entity-Titel. Kandidaten enthielten Artist-/Albumkontext, der
aber ignoriert wurde. Ein gleichnamiger Treffer eines anderen Artists konnte
eine perfekte Punktzahl erhalten und seine Provider-ID automatisch
persistieren.

**Korrekturvertrag:** Artist muss übereinstimmen; Track-Matches berücksichtigen
zusätzlich Album-/Editionkontext und eine Ambiguitätsmarge. Unsichere Treffer
bleiben Manual Review.

### <a name="find22-08"></a> Finding 8 — Artist-Aggregation auf die angefragte Seite begrenzen

**Ort:** `core/library2/queries.py`

Jeder Artist-List-/Search-Request aggregierte und deduplizierte den gesamten
Track-/File-Katalog, bevor `LIMIT/OFFSET` griff. Bei hunderttausenden Tracks
verursachten Page Load und jeder Such-Tastendruck Full-Library-Joins,
Distinct-Counts und Window-Sort.

**Korrekturvertrag:** Wo die Sortierung es erlaubt, zuerst Artists filtern und
paginieren; Aggregate nur für die Seite berechnen oder indizierte Counter
materialisieren. Tests prüfen Queryplan und bounded Row-Touch.

### <a name="find22-09"></a> Finding 9 — Nicht-lateinische Enrich-Titel bewahren

**Ort:** `core/library2/native_enrich.py`

Ein ASCII-only Normalizer reduzierte vollständig CJK- oder anders
nicht-lateinische Titel auf den leeren String. Der Ranking-Loop übersprang
damit alle Kandidaten; solche Entities konnten nie eine fehlende Provider-ID
erhalten.

**Korrekturvertrag:** Den projektweiten Unicode-erhaltenden Normalizer nutzen.
Regressionen enthalten identische und ähnliche CJK-Titel sowie unterschiedliche
numerische Suffixe.

### <a name="find22-10"></a> Finding 10 — Native Enrich behält den Metadata-Update-Vertrag

**Ort:** `core/library2/native_enrich.py`

Bei bereits gematchten Entities blieb `hit` leer und Enrich aktualisierte nur
Artwork bzw. Duration. Genres, Jahr, Label, UPC, Style, Mood, Summary, Lyrics
und weitere dokumentierte Felder wurden trotz Erfolgsmeldung nicht neu
geschrieben.

**Korrekturvertrag:** Provider-spezifische descriptive Enrichment-Daten in
native Rows projizieren. Ein vorhandener Match ist kein Grund, den
Metadata-Refresh in einen stillen No-op zu verwandeln.

### <a name="find22-11"></a> Finding 11 — Monitor-Mutation bei Outbox-Fehler abbrechen

**Ort:** `core/library2/mirror_outbox.py`

Wenn der Wishlist-Payload während einer Monitoränderung fehlschlug, wurde die
Exception nur debug-geloggt. Der V2-State konnte committen, obwohl keine
Outbox-Zeile und damit kein Retry-Anker existierte. Intent und
Ausführungswishlist divergierten dauerhaft unsichtbar.

**Korrekturvertrag:** Outbox-Build/Insert gehört in dieselbe Transaktion und
propagiert Fehler; alternativ wird ausdrücklich eine failed retryable
Operation persistiert. Nie erfolgreich antworten, wenn weder Mirror noch
Retry-Anker existiert.

### <a name="find22-12"></a> Finding 12 — Alias-Rows in Suche und Totals falten

**Ort:** `core/library2/queries.py`

Alias-Rows wurden aus der Liste versteckt, Suche und Stats gruppierten aber
weiter nach raw `artist_id`. Alias-eigene Albums, Tracks und Bytes
verschwanden aus der Canonical Card; count-basierte Sortierung war falsch und
Suche nach dem Aliasnamen lieferte nichts.

**Korrekturvertrag:** Mitglieder bereits beim Filter und in allen Aggregaten
auf Canonical-ID auflösen. Aliasnamen sind Suchbegriffe des Canonical Artists.

### <a name="find22-13"></a> Finding 13 — Alle artist-weiten Aktionen auf Alias-Gruppen anwenden

**Ort:** `api/library_v2.py` und artist-scoped Helper

Die Detailseite zeigte Releases der gesamten Alias-Gruppe; Refresh & Scan,
Retag, Reorganize, Bulk Monitoring, Duplicates, Wanted, Delete und History
arbeiteten teilweise nur auf einer exakten `artist_id`. Sichtbare Inhalte und
Aktionsscope widersprachen sich.

**Korrekturvertrag:** Ein gemeinsamer Alias-Scope-Resolver wird von Read und
Actions verwendet. Absichtlich engere destruktive Aktionen benennen ihren
Scope bereits in Preview und Confirm.

### <a name="find22-14"></a> Finding 14 — Album-Artist-Credits bei Reimport neu aufbauen

**Ort:** `core/library2/importer.py`

Track-Junctions wurden neu aufgebaut, Album-Credits jedoch nur per `INSERT OR
IGNORE` ergänzt. Entfernte Featured Artists oder ein geänderter Primary Artist
blieben als Ghost-Credits bestehen und verfälschten Releases, Counts und
Aktionsscope.

**Korrekturvertrag:** Derived Album-Credits nach den Tracks eines importierten
Albums deterministisch rebuilden. Ein Metadatenänderungstest entfernt und
ersetzt Credits und erwartet keine alten Junctions.

### <a name="find22-15"></a> Finding 15 — Queue-Status einmal pro Artist-Seite pollen

**Ort:** `library-v2-page.tsx`

Jeder gemountete, auch eingeklappte AlbumBlock startete eine eigene
Queue-Status-Query alle drei Sekunden. Bei 100 Releases waren ungefähr 33
Requests pro Sekunde möglich; jeder öffnete die DB und scannte Runtime-
Kontexte.

**Korrekturvertrag:** Eine artist-scoped Statusmap pollen und Album-/Track-
Einträge verteilen; alternativ nur sichtbare/expandierte Rows pollen. Keine
N+1-Poller pro Entity.

### <a name="find22-16"></a> Finding 16 — Bestehende Staging-Copies nach Inhalt verifizieren

**Ort:** `core/acquisition/main_pipeline_bridge.py`

Nach unterbrochenem Import, Rescan oder Reassignment konnte am
deterministischen Ziel bereits anderes Material gleicher Bytegröße liegen,
etwa gleiche Basenames aus verschiedenen Disc-Ordnern. Ein Size-only Check
akzeptierte diese stale Copy für einen neuen Match.

**Korrekturvertrag:** Content-Hash vergleichen oder die Working Copy unter
dem exklusiven Import-Claim atomar ersetzen. Tests verwenden unterschiedliche
Inhalte mit identischer Größe.

### <a name="find22-17"></a> Finding 17 — Refresh & Scan beobachtbar und asynchron

**Ort:** `api/library_v2.py`

Ein großer Artist oder langsames Netzlaufwerk wurde synchron im Request
gescannt und konnte Browser/Proxy-Timeouts überschreiten. Ein Top-Level-Fehler
wurde gefangen und trotzdem als `success: true` mit leeren Stats gemeldet.

**Korrekturvertrag:** Observable Background-Job mit Status, Progress und
terminalem Fehler. Per-File-Fehler bleiben tolerant, der komplette Lauf darf
aber nicht als Erfolg erscheinen, wenn er gar nicht stattfand.

---

## 2. Regression-Audit vom 21. Juli 2026

### <a name="c-01"></a> C-01 — Preview/Null-Header kann vollständige Datei ersetzen

Ein Provider kann eine circa 30-Sekunden-Preview mit Header-Dauer `0`
liefern. Ohne Decoded-Duration- und Never-Replace-With-Shorter-Guard kann sie
als gültiger Ersatz einer vollständigen Datei durchgehen.

**Reproduktion:** Lange bestehende Datei, kürzerer Kandidat mit Header-Dauer
null, aber dekodierbarer kurzer Dauer. Der Kandidat muss vor jeder Mutation
abgelehnt werden; die bestehende Datei bleibt unverändert.

### <a name="h-01"></a> H-01 — Alte Repair-Job-IDs und Settings gehen verloren

`quality_upgrade_scanner`, `quality_upgrade` und `discography_backfill` waren
retired, aber nicht vollständig in `JOB_ID_MIGRATIONS` abgebildet. Aktivierung,
Intervalle, Filter, manuelle Aufrufer und pending Findings konnten verschwinden.

**Korrekturvertrag:** stabile Read-Aliases; deterministisches Merge mehrerer
alter Quality-Konfigurationen; Review-Semantik bleibt Review; Findings erst
nach verifiziertem Ersatz entfernen.

### <a name="h-02"></a> H-02 — Bestehende Quality-Automation startet Downloads

Die unverändert benannte Automation `start_quality_scan` bedeutete früher
Review/Finding. Nach dem Cutover konnte fehlende neue Konfiguration den
`automatic`-Modus wählen und sofort Wishlist-Downloads starten.

**Korrekturvertrag:** Alte Automation ruft einen run-spezifischen
Review-Modus auf. Ein Regressionstest erwartet Finding und keinen
Wishlist-Dispatch.

### <a name="h-03"></a> H-03 — Bootstrap-Lease ohne Owner-Fencing

`heartbeat`, `mark_done` und `mark_failed` aktualisierten nur Singleton-ID 1.
Nach stale Reclaim konnte der alte Besitzer den Zustand des neuen Laufs
überschreiben. Der manuelle Import führte denselben persistenten Heartbeat
nicht.

**Korrekturvertrag:** Run-/Owner-UUID; jedes Update enthält
`WHERE status='running' AND owner_token=?` und prüft Rowcount. Manueller und
automatischer Import teilen Token und Heartbeat.

### <a name="h-04"></a> H-04 — Leerer Fresh-Install-Bootstrap wird dauerhaft abgeschlossen

Leere Legacy-Tabellen konnten einen finalen Wasserstand erzeugen. Später
hinzugefügte Artists führten zu keinem neuen Import.

**Korrekturvertrag:** Abschluss an Quell-Watermark koppeln; leeren, noch nicht
initialisierten Bestand nicht endgültig schließen; nach realem Library-Sync
erneut reconciliieren.

### <a name="h-05"></a> H-05 — Nicht-Admin-Profile mutieren globalen V2-Intent

Wishlist-/Watchlist-Aktionen eines anderen Profils konnten V2 materialisieren,
Profil 1 verwenden oder globale Quality-Zuweisungen übernehmen.

**Korrekturvertrag:** zentraler Actor-/Admin-Guard in jedem
Materialisierungseingang. Nicht-Admin-Pfade ändern weder V2-Tabellen noch
Admin-Wishlist.

### <a name="h-06"></a> H-06 — Composite Remove demonitort mehrere Releases

`track::album-a` wurde vor Descriptor-Auswahl zur Bare Track-ID reduziert.
Dadurch konnten `track::album-b` und weitere Provider-ID-Treffer ebenfalls
demonitort werden.

**Korrekturvertrag:** Composite-Key bewahren; direkter V2-/Stable-ID-Treffer
ist terminal; Provider-Fallback mit Album disambiguieren oder bei
Mehrdeutigkeit abbrechen.

### <a name="h-07"></a> H-07 — Watchlist-Artist-Match verliert Provider-Namespace

Ein globales Namens- und unqualifiziertes ID-Set ließ gleiche Namen sowie
Deezer-/iTunes-/Spotify-Kollisionen falsch matchen.

**Korrekturvertrag:** Identitäten pro Watchlist-Row und Provider; kein
Namensfallback bei widersprechender starker ID; dieselbe Semantik für Import,
Insert, Reconcile und Remove.

### <a name="h-08"></a> H-08 — Repair-Intent `remove`/`redownload` geht verloren

Handler mutierten das File und ließen global Wanted neu berechnen. Dadurch
queued `redownload` bei unmonitored Tracks nicht, während `remove only` bei
monitored Tracks wieder queueen konnte.

**Korrekturvertrag:** Intent bis zum Wanted-/Wishlist-Write transportieren;
Matrix monitored/unmonitored × remove/redownload testen.

### <a name="h-09"></a> H-09 — Finding wird trotz fehlgeschlagenem V2-Sync resolved

Nach erfolgreicher physischer Mutation blieb ein Syncfehler nur im Resultat;
das Finding wurde trotzdem resolved. Disk und Katalog divergierten ohne
Retry-Anker.

**Korrekturvertrag:** Finding pending/failed lassen oder persistente
Maintenance-Outbox erzeugen. Erfolg erst nach synchronem oder
restart-sicherem Katalogabschluss.

### <a name="h-10"></a> H-10 — Tracknummer-Reparatur nutzt unvollständige File-Teilmenge

Die kanonische Albumliste wurde aus vorhandenen Files gebaut; Missing Tracks
fehlten in Total-/Disc-Heuristik.

**Korrekturvertrag:** vollständige Edition-/Provider-Tracklist ist Soll;
Files sind nur die zu mutierenden Subjects.

### <a name="h-11"></a> H-11 — Native Tracknummer-Fixes lassen Legacy stale

Der V2-Zweig aktualisierte `lib2_tracks`/Files, aber nicht verbundene
Legacy-Nummern und Pfade. Legacy-UI, APIs und Jobs sahen andere Daten.

**Korrekturvertrag:** gemeinsamer transaktionaler Maintenance-Write oder
Compatibility-Outbox.

### <a name="h-12"></a> H-12 — Multi-File-Findings deduplizieren Files weg

Mehrere aktive Files wurden gescannt, file-semantische Findings verwendeten
aber dieselbe Track-ID. Globaler Dedup unterdrückte das zweite File;
dismissed/resolved blockierte spätere neue Fakten.

**Korrekturvertrag:** File-ID plus File-/Config-Fingerprint für
file-semantische Jobs, Primary-Datei für track-semantische Jobs.

### <a name="h-13"></a> H-13 — Reorganize lässt V2-Pfad stale

Nach Legacy-Path-Update konnte `sync_repair_change` den alten V2-File-Row
nicht mehr finden. Ein Test pinnte sogar den stale Pfad.

**Korrekturvertrag:** konkrete V2-File-ID vor Move auflösen und beide Indizes
atomar/restart-sicher schreiben. Ausführliche Produktionsbeweise stehen bei
[LV2-017](#lv2-017).

### <a name="h-14"></a> H-14 — V2-Track-ID wird als Legacy-/Server-ID interpretiert

Der Play-Button übergab lokale V2-ID im Legacy-Feld `id`. Bei fehlgeschlagener
Titel-/Artist-Auflösung konnte ein anderer gleich nummerierter Track gespielt
oder geloggt werden.

**Korrekturvertrag:** typisierte IDs und V2-aware File-/Stream-Resolver.

### <a name="h-15"></a> H-15 — Alias-Anzeige und Aktionsscope widersprechen sich

Entspricht [Finding 13](#find22-13): Die Seite zeigt die Alias-Gruppe,
mehrere Aktionen benutzen nur eine Raw-ID.

### <a name="h-16"></a> H-16 — `allowed_pages` wird umgangen

`library-v2` wurde clientseitig immer erlaubt. Profile ohne Library-Recht
erhielten Navigation und Zugriff auf Pfade/History.

**Korrekturvertrag:** bestehendes Library-Recht erben oder Page-Key migrieren;
sensitive Reads serverseitig schützen.

### <a name="h-18"></a> H-18 — Deaktivierter V2-Katalog schaltet Repair still ab

Native Jobs erhielten bei `features.library_v2=false` leere Subjects und null
Findings. Default-on kaschierte den Bruch nur.

**Korrekturvertrag:** Entweder Legacy-Jobs bleiben solange der Katalog
abschaltbar ist, oder der Cutover ist ausdrücklich nicht abschaltbar und wird
mit Migration/UI/Doku behandelt. „Disabled“ darf nie wie „clean“ aussehen.

> Das frühere H-17 („Acquisition Review backend has no UI“) ist eine
> unvollständige Produktfunktion und steht deshalb als
> [F-12](library-v2-features.md#feat-acq-review) in der Feature-Spezifikation.
> Ob und wie weit die spätere UI umgesetzt ist, steht ausschließlich in der
> Statusdatei.

### <a name="m-01"></a> M-01 — Legacy-Hybrid-Fallback geht verloren

Alte/ungültige Primary-/Secondary-Werte fielen früher auf Soulseek zurück;
Registry-Filterung konnte eine leere oder verkürzte Chain liefern.
Alt-Konfigurationen benötigen Regressionstests und kompatible Normalisierung.

### <a name="m-02"></a> M-02 — Album-Grab startet teilweise und meldet danach 503

Tracks wurden einzeln vorbereitet und sofort dispatcht. Scheiterte ein
späterer Track am Gate, meldete die Route „nicht gestartet“, obwohl frühere
Downloads liefen; Retry konnte duplizieren. Erst alle vorbereiten, dann
geschlossen dispatchen.

### <a name="m-03"></a> M-03 — Gate-Fehler verbraucht Candidate ohne Download

`used_sources` wurde vor Acquisition Preparation gesetzt. Ein temporärer
Gatefehler machte den Candidate unsichtbar. Verbrauch erst nach Preparation
oder explizit retrybaren Zustand persistieren.

### <a name="m-04"></a> M-04 — Autolink speichert Disc-Nummer nicht

`disc_number` floss ins Matching, fehlte aber im INSERT. Disc-2-Tracks
landeten auf Disc 1. Regression: gleiche Tracknummer auf zwei Discs.

### <a name="m-05"></a> M-05 — Gelöschtes explizites Profil pinnt Ersatzprofil

Die Profil-ID wurde auf den damaligen Default umgebogen,
`quality_profile_explicit=1` blieb. Spätere Parent-/Default-Änderungen griffen
nicht. Explicit-Flag löschen und Vererbung neu berechnen.

### <a name="m-06"></a> M-06 — Dismissed Quality-Finding kehrt nach Profiländerung nie zurück

Dedup umfasste pending/resolved/dismissed ohne Profil-, Target- oder
File-Fingerprint. Neue Konfiguration muss ein neues fachliches Finding
erlauben.

### <a name="m-07"></a> M-07 — Lose Files verlieren Repair-Funktionalität

Ein Orphan-Scan ersetzt Fake-Lossless, Converter, Tracknummer, ReplayGain,
Corruption und Quality nicht. Filesystem-Subjects müssen diese Fakten
weiterhin prüfen; Cutoff bleibt bis zur Katalogzuordnung ggf. unbewertbar.

### <a name="m-08"></a> M-08 — Retired Tools ohne gleichwertigen Ersatz

Expired Cleaner und Reorganize-Review hatten zeitweise keinen sichtbaren
1:1-Pfad; alte manuelle IDs waren unbrauchbar. Ersatzpfade, Settings-Migration
und Review/Apply-Semantik müssen vor Retirement vorhanden sein.

### <a name="m-09"></a> M-09 — Playlist-Scope verliert Albumidentität

Album-A-Scope konnte `track::album-b` dispatchen. Exakten Wishlist-Key oder
Track+Album verwenden; Bare-Fallback nur bei Eindeutigkeit.

### <a name="m-10"></a> M-10 — Teilmigrierte Wishlist kann Reconcile-Churn erzeugen

Alte Bare-ID ohne Album/`source_info` konnte als ungespiegelt gelten:
Reconcile legt Composite-Row an, Duplicate-Cleanup löscht sie, nächste Stunde
wiederholt sich der Zyklus. E2E-Test mit zwei Läufen und stabilen Row-/Outbox-
Counts ist erforderlich.

### <a name="m-11"></a> M-11 — V2-native Artists fehlen in globaler Suche

Globale Search las nur Legacy-Artists. Legacy- und V2-Ergebnisse müssen über
stabile Provider-Identität vereinigt und dedupliziert werden. Siehe
[LV2-014](#lv2-014).

### <a name="m-12"></a> M-12 — UI-Mutationen können still scheitern

Alias-Unlink ohne sichtbaren Fehler, optimistisches `monitor_new_items` ohne
Rollback und Album-ReplayGain ohne Error Toast hinterließen falsche UI-
Annahmen. Einheitlicher Mutation-State, Retry/Rollback und MSW-4xx-Tests.

### <a name="m-13"></a> M-13 — Feature-Flag-Vertrag ist inkonsistent

Default war über Inline-Fallbacks verteilt, UI/API boten keinen klaren Key,
Strings oder `1` scheiterten an `is True`. Ein zentral normalisierter,
dokumentierter Vertrag ist erforderlich.

### <a name="m-14"></a> M-14 — UI erfindet nach fünf Minuten terminalen Jobstatus

Nach 300 Polls setzte der Client lokal `running:false`, obwohl der Serverjob
weiterlaufen konnte. UI darf detached/running anzeigen, aber nie einen
terminalen Serverzustand erfinden.

### <a name="m-15"></a> M-15 — Malformed Album-ID bricht Queue-Status

Ungeschütztes `int(album_id)` ließ einen einzelnen kaputten Context den
gesamten Endpoint auf 500 setzen. Safe Parser und isolierte Ignore-/Diagnose-
Semantik.

### <a name="l-01"></a> L-01 — Getracktes Config-Backup

`config/config.json.bak` etablierte trotz Placeholdern ein gefährliches
Muster für lokale Secrets. Lokale Config-Backups gehören nicht ins Repo.

### <a name="l-02"></a> L-02 — MP3-Artefakt im Branch

Eine 7,3-MB-MP3 lag im Git-Branch. Neben Repository-Bloat entsteht ein
Lizenz-/Distributionsthema. Testmedien müssen klein, synthetisch und
rechtssicher sein.

---

## 3. Reuse-Audit der Acquisition-Schicht vom 12. Juli 2026

### <a name="lib2-f01"></a> LIB2-F01 — Doppelte Acquisition-Decision-Logik

`acquisition/search_service.py` suchte Adapter parallel und eine neue Decision
Engine rankte die Kandidaten unabhängig vom `DownloadOrchestrator`. Der volle
`download_source.mode`-/`hybrid_order`-Vertrag floss nicht ein. Derselbe
Request konnte via V2, Wishlist und Interactive Search unterschiedliche
Quellen wählen.

**Korrekturvertrag:** gemeinsamen Selection-Service/Orchestrator verwenden.
`best_quality` durchsucht alle Quellen und wählt global; Hybrid geht die
konfigurierte Chain der Reihe nach. Beide dürfen nicht auf einen einzigen
numerischen Source-Score reduziert werden.

### <a name="lib2-f02"></a> LIB2-F02 — Bundle Import umgeht Main Post-Processing

Der ursprüngliche Bundle-Importer staged, probte Basis-Quality und schrieb
direkt `lib2_track_files`. Stability, Integrity, Quality, AcoustID,
Verification, Quarantäne, Tagging, Conversion und Finalization waren damit
nicht dieselben wie im Legacy-/Wishlist-Pfad.

**Korrekturvertrag:** Bundle-Schicht ist nur Inventar-/Matching-Koordinator.
Jedes File wird mit Editionkontext an den gemeinsamen File-Processing-Service
delegiert; V2-Completion erst nach dessen Erfolg.

### <a name="lib2-f03"></a> LIB2-F03 — Quality-Profil im Bundle-Pfad unvollständig

`probe_audio_quality` ist kein Quality-Gate. Ranked Targets, Fallback,
Downsample/Lossy Copy, AcoustID, Deep Verification und profilspezifische
Importsettings fehlten.

**Korrekturvertrag:** exaktes Request-Profil live auflösen und denselben
Post-Processing-Kontext/Guards verwenden. Identische Settings müssen in
Legacy und V2 dasselbe Accept/Reject liefern.

### <a name="lib2-f04"></a> LIB2-F04 — Import-Fail verliert automatische Retry-Semantik

`record_import_failure` blocklistete einen Kandidaten und setzte Request
direkt auf failed. Nächster gecachter Kandidat, restliche Source-Chain und
Cross-Source-Retry nach Quality/Integrity/AcoustID fanden nicht statt.

**Korrekturvertrag:** präzises Blocklist-Event, Request retryable halten und
den gemeinsamen Candidate-/Source-Walk fortsetzen. Erst erschöpfte Kandidaten
und Quellen erzeugen terminalen Request-Fail.

### <a name="lib2-f05"></a> LIB2-F05 — Upgrade-Output-Ownership war unklar

V2 erkannte Upgrade-Kandidaten, während bestehender Quality-Upgrade-Job und
Wishlist/Main-Pipeline der kanonische Downloadpfad waren. Ein direkter V2-
Output hätte eine zweite Upgrade-Pipeline geschaffen.

**Korrekturvertrag:** ein Evaluator, bestehende Upgrade-Policy/Cutoff-
Semantik, Wishlist-Mirror als Compatibility-Adapter bis zum globalen Cutover.
Der Adapter trägt das exakte Profil.

### <a name="lib2-f06"></a> LIB2-F06 — Bundle Import war nicht an Quarantäne/Approval angeschlossen

Der bestehende Sidecar-/Approve-Pfad stellte Files wieder her und übersprang
nur den bestätigten Check. Der neue Bundle-Pfad bewahrte Acquisition-/Edition-
Kontext und Re-Dispatch nicht zuverlässig.

**Korrekturvertrag:** Kontext im Sidecar; `approve_quarantine_entry`
wiederverwenden; alle nicht approvten Checks erneut ausführen. Force-Grab darf
nur denselben vorab akzeptierten Reason-Code automatisch freigeben.

### <a name="lib2-f07"></a> LIB2-F07 — Persistenter State und In-Memory-Retry waren nicht gebrückt

Legacy-Retry kannte Candidate-Cache, Used/Exhausted Sources und Sidecar-IDs;
Acquisition-Tabellen verwendeten andere Identifier. Ein Restart konnte die
exakte nächste Entscheidung verlieren.

**Korrekturvertrag:** expliziter Adapter zwischen Task/Batch und Request,
Grab, Candidate, Import, History. Jeden retry-relevanten Fakt vor externer
oder Filesystem-Arbeit persistieren.

### <a name="lib2-f08"></a> LIB2-F08 — Parität brauchte eine Contract-Matrix

Viele Unit-Transitions bewiesen keine Gleichheit für `best_quality`, Hybrid,
Upgrade-Policy, Quality-Quarantäne, AcoustID-Approval, Next Candidate und
Restart.

**Korrekturvertrag:** identische Legacy-/V2-Szenarien laufen lassen und
Selected Source, Candidate-Reihenfolge, Rejection, Quarantäne, Approval,
Retry und terminalen State als normalisierte Business Outcomes vergleichen.

---

## 4. Bug- und Integritätscluster LV2-001 bis LV2-017

### <a name="lv2-001"></a> LV2-001 — Automatic Search erzeugt Wishlist-State

**Symptom:** Eine direkte Suche auf einem unmonitored Track schlug fehl; der
Track stand danach trotzdem in Wishlist und UI blieb aktiv.

**Ursache:** Der direkte Pfad rief `mirror_tracks_wishlist(...,
monitored=True, user_initiated=True)` auf. Der Klick wurde zu persistentem
Monitoring. Der Failure-Handler requeue-te zudem ohne Unterscheidung zwischen
Wishlist-Lauf und transienter Suche.

**Korrekturvertrag:** Serverseitig aufgelöster transienter Payload mit
`requeue_failed_to_wishlist=False`. Erfolg läuft durch dieselbe Pipeline;
Fail ist terminal; `monitored` und Wishlist bleiben unverändert.

### <a name="lv2-002"></a> LV2-002 — Terminale Tasks stehen wieder als Queued da

**Symptom:** Erfolgreicher Manual Grab mit vorhandenem File blieb dauerhaft
`Queued`; Failed Search konnte weiter aktiv erscheinen.

**Ursache:** Terminale `download_tasks` wurden korrekt ausgeblendet, danach
legte ein älterer `matched_downloads_context` dieselbe Track-ID wieder als
queued an.

**Korrekturvertrag:** Für jede terminal beobachtete Track-ID stale Shadow-
Kontexte unterdrücken. Gilt für completed, failed, cancelled, not_found,
skipped und already_owned.

### <a name="lv2-003"></a> LV2-003 — Import-Runtime verliert Abschluss-Hooks

**Symptom:** Physisch erfolgreiche Imports blieben im Batchstatus hängen;
Media Scan, Automation oder Repair liefen je nach Einstieg nicht.

**Ursache:** Web-Wrapper injizierten `on_download_completed`,
`automation_engine`, `web_scan_manager` und `repair_worker` nicht in die
Core-Runtime.

**Korrekturvertrag:** Alle Einstiegspunkte bauen dieselbe vollständige
Runtime. Success/Fail wird terminal, Scan genau einmal koalesziert und
Standalone/V2 erhält den File-Eintrag.

### <a name="lv2-004"></a> LV2-004 — Exception nach Move erzeugt physischen Orphan

**Symptom:** File war am finalen Ort, eine spätere Exception verhinderte
Side Effects und DB-Link. Quelle existierte nicht mehr, daher normaler Retry
unmöglich.

**Ursache:** Outer Exception kannte nur „Quelle existiert → Retry“ und
„Quelle weg → nicht Retry“, prüfte aber keinen realen `_final_processed_path`.

**Korrekturvertrag:** Existiert das Ziel real, idempotent Legacy/V2,
Acquisition und Grab anhand des finalen Pfads reconciliieren. Append-only
History nicht blind doppelt schreiben. Fehlendes Ziel darf keinen falschen
Success erzeugen.

### <a name="lv2-005"></a> LV2-005 — Quarantäne-Approve ohne Live-Task löst keinen Scan aus

Ein Sidecar kann Neustart überleben, sein In-Memory-Task nicht. Nach
erfolgreichem Reimport fehlte dadurch der Batch-/Scan-Callback.

**Korrekturvertrag:** Taskloser Approve prüft finalen Pfad und verbleibende
Rejection, triggert bei aktivierter Automation genau einen koaleszierten Scan
und führt alle Library-/Acquisition-Side-Effects aus.

### <a name="lv2-006"></a> LV2-006 — Persistente Grabs hängen auf `legacy_dispatched`

DB-Stichproben zeigten zahlreiche Requests in `grabbing` und Grabs in
`downloading`, tagelang ohne reale Aktivität. Ein pauschal kürzeres TTL würde
legitime lange Transfers failen.

**Korrekturvertrag:** Evidenzbasierter Reconciler vergleicht Runtime,
Post-Processing, Client, Quarantäne, Imports und reale gemappte Indexpfade.
Completion nur bei real indexiertem File; eindeutige Fail/Cancel übernehmen;
evidenzlose Altzustände erst nach konfigurierbarer TTL schließen. Jede
Transition läuft idempotent im Savepoint.

### <a name="lv2-007"></a> LV2-007 — Orphan Detector war Legacy-only

V2-only Files wurden als Orphan gemeldet, weil bekannte Pfade nur aus
Legacy-`tracks.file_path` kamen.

**Korrekturvertrag:** aktive `lib2_track_files`, V2-Artist-/Track-Identitäten
und Pfad-Mapping in den Index aufnehmen. Ein V2-only File mit existierender
File-Row wird nie als Orphan gemeldet.

### <a name="lv2-008"></a> LV2-008 — Human Approve synchronisiert Verification nicht

Approve aktualisierte History, Legacy und File-Tag; die passende
`lib2_track_files.verification_status` blieb alt.

**Korrekturvertrag:** rohe und gemappte Pfade auf passende V2-File-Rows
auflösen, Verification aktualisieren und Anzahl zurückmelden; ohne V2-Schema
No-op.

### <a name="lv2-009"></a> LV2-009 — Recover to Staging bewegt Disk, nicht Lifecycle

File/Sidecar wurden bewegt bzw. entfernt, Request, Grab, Import und History
hatten keine ausdrückliche Transition. Ein Crash zwischen den Schritten
erzeugte unklare Zustände.

**Korrekturvertrag:** Recovery-Journal vor Move; Reihenfolge Plan committen →
File bewegen → Lifecycle committen → Sidecar entfernen. Jeder Crashpunkt ist
idempotent wiederaufnehmbar; späterer Staging-Import erhält Korrelation und
läuft wieder durch die Shared Pipeline.

### <a name="lv2-010"></a> LV2-010 — Erster physischer Miss wird als Present verborgen

`rescan_files` erkannte korrekt `missing_suspected`; `file_status()` machte
daraus weiter `present`.

**Korrekturvertrag:** Amber `checking missing`, noch kein Wanted/Delete. Erst
zweiter gesunder Miss wird `missing_confirmed`. Ungesunder Root bleibt
unresolved.

### <a name="lv2-011"></a> LV2-011 — `w/` zerlegt Artist-Credits falsch

Die Featured-Regex kannte `w/` nicht; der allgemeine Slash-Split machte aus
`Odetari w/ 9lives` die Artists `Odetari w` und `9lives`.

**Korrekturvertrag:** `w/` vor Listensplit als Feature-Separator erkennen,
auch in parenthesized Title-Credits.

### <a name="lv2-012"></a> LV2-012 — Provider-ID-Dedup fehlte

Artist-Dedup gruppierte nur nach normalisiertem Namen. Ein Fragment mit
anderem Namen, aber derselben Spotify-ID wurde nie zusammengeführt.

**Korrekturvertrag:** Zweiter Pass über konfliktfreie Spotify-, MusicBrainz-,
Deezer-, Tidal- und Qobuz-IDs; widersprechende andere IDs blockieren Auto-
Merge; nach Merge Albums erneut falten. Produktiv nur nach Backup/Dry Run.

### <a name="lv2-013"></a> LV2-013 — Übergreifender Integritätsreport fehlte

Einzelne Fixes erkannten nicht die gesamte Kette aus Disk, Legacy, V2,
Runtime, Acquisition, Quarantäne, externem Client und Media-Projektion.

**Korrekturvertrag:** streng read-only, bounded Report mit Code, Severity,
Komponente, Entity, Grund und Details. Root Health bleibt Gate. Report findet
unter anderem stale Runtime, Lifecycle-Divergenz, unindexierte Files,
Legacy/V2-Abweichung, Completed Import ohne File, fehlende Recovery-Files und
verwaiste Sidecars. Keine Deletes oder Schema-Migration.

### <a name="lv2-014"></a> LV2-014 — V2-native Artists erscheinen nicht als „In Your Library“

Enhanced Search baute DB Artists nur aus dem Legacy-Media-Spiegel. V2-only
Artists mit `legacy_artist_id=NULL` erschienen weiter als externe Ergebnisse.

**Korrekturvertrag:** Legacy- und `lib2_artists`-Treffer über qualifizierte
Provider-Identität vereinigen und deduplizieren; Ownership nicht durch eine
künstliche Legacy-Zeile vortäuschen.

### <a name="lv2-015"></a> LV2-015 — Playlist-Pipeline startet globale Wishlist

Refresh/Discovery/Sync waren playlist-scoped, Phase 4 rief jedoch
`process_wishlist_automatically` ohne Track-/Profilfilter auf.

**Korrekturvertrag:** Scope aus tatsächlich verarbeiteten Playlist-Zeilen,
Discovery-ID und konkretem Profil; alle Kategorien nur für exakt passende
Rows; kein globales Duplicate-Cleanup; fail closed ohne Identität.

### <a name="lv2-016"></a> LV2-016 — Neue Artists starten als monitored

Schema-Default war `1`; Autolink/Resolver ließen das Feld aus. Eine echte
Watchlist-Ableitung lief nur im Initialimport.

**Korrekturvertrag:** Default und alle Inserts `0`; neuer Artist prüft echte
Watchlist; Reconciler lässt explizite Regeln gewinnen und leitet nur alte/
default/imported Flags aus Watchlist ab.

### <a name="lv2-017"></a> LV2-017 — Rename desynchronisiert `lib2_track_files.path`

**Produktionsbeweis:** Track 14484 meldete bei ReplayGain und Lyrics „File no
longer exists“. V2 speicherte `01-01 - …flac`, im korrekt gemounteten
Container lag `01 - …flac`. Weitere Beispiele betrafen Adele, Arc North und
Sawano Hiroyuki.

**Warum der Name wechselte:** Früher expandierten `$disc/$discnum` bei
Single-Disc zu `01`; später wurden sie leer und ein verwaister Bindestrich
entfernt. Ein Template `$disc-$track - $title` wechselte dadurch von `01-03`
zu `03`. Multi-Disc behält `01-10`; loser Single ohne Album verwendet
`single_path`.

**Root Cause:** Reorganize schrieb zuerst Legacy-`tracks.file_path`. Danach
übergab es eine bare Legacy-Track-ID und nur den neuen Pfad an die native
Maintenance-Grenze. Diese akzeptierte nur `lib2:<id>`; Path Match konnte den
alten V2-Pfad nicht mehr finden. Zudem fehlte dort eine Operation, die den
V2-Pfad überhaupt auf das Ziel setzt.

**Korrekturvertrag:** Plan trägt vor dem Move stabile V2-File-ID. Nach Move
schreiben beide Indizes denselben Zielpfad; Fehler zwischen Rename und Commit
haben Recovery. Matrix: Single Release Track 1/10, Single-Disc Album 1/10,
Multi-Disc Disc 1/2 Track 10, loser Single. Betroffene Installationen erhalten
read-only Backfill-Dry-Run; unsichere Mehrfachtreffer werden nicht gewählt.

---

## 5. Deep-Dive-Findings vom 16. Juli 2026

### <a name="dd-a1"></a> DD-A1 — Gewähltes Cover erreicht Audio-Files nicht

`apply_manual_artwork` änderte Override/Cache, triggerte aber keinen Tag
Write. Selbst manueller Retag half nicht, weil `write_tags` Files ohne
Text-Tag-Diff übersprang; Cover war kein Diff-Feld.

**Korrekturvertrag:** `force_cover` oder Cover-Hash-Diff; Album-Art-Apply
startet denselben Background-Tag-Write mit Progress/Option „Embed“.

### <a name="dd-a2"></a> DD-A2 — Mutable Artwork-URL wurde sieben Tage immutable gecacht

Stabile URL plus `Cache-Control: ... immutable` zeigte nach Cover-Pick das
alte Bild trotz React-Query-Invalidierung.

**Korrekturvertrag:** URL-Version aus Artwork-Mtime/Version; dann darf
immutable bestehen bleiben.

### <a name="dd-a3"></a> DD-A3 — Album-Automatic-Search war global

Der Albumtitel stand nur im Action-String; Handler rief globale Wishlist auf.
Deep-Link Album View hatte asymmetrischen No-op-Handler.

**Korrekturvertrag:** serverseitig artist-/album-/track-scoped; globale
Aktion nur in globalem Kontext.

### <a name="dd-a4"></a> DD-A4 — Track-Automatic-Search war zweite Decision Engine im Client

`autoGrabBest` rankte lossless/score/slots in TypeScript ohne vollständiges
Profil, Candidate-Walk, Retry oder Blocklist.

**Korrekturvertrag:** derselbe serverseitige Wishlist-/Candidate-Service wie
alle anderen Downloads.

### <a name="dd-a5"></a> DD-A5 — BPM/Duration erreichten die UI nicht

`bpm` existierte im Schema/Importer, fehlte in Payload und UI; `duration`
war im Payload, aber unsichtbar. Beide gehören als sortierbare optionale
Spalten in die Track-Tabelle.

### <a name="dd-a6"></a> DD-A6 — History las nur `track_downloads`

Acquisition-, Entity-, Delete- und Skip-Journale existierten bereits, wurden
aber nicht scope-genau zusammengeführt. Die Feature-Spezifikation F-10
definiert den gemeinsamen Feed.

### <a name="dd-a7"></a> DD-A7 — Pipeline-Lifecycle blieb im Track unsichtbar

Quality-Gate, AcoustID-Grund und Quarantänegeschichte waren teilweise
persistiert, aber nicht am Track/File korreliert. Kompaktes
`pipeline_result_json` plus History-Feed müssen auch Pre-Autolink-Fails
sichtbar machen.

### <a name="dd-a8"></a> DD-A8 — Match-Chips zeigten nie konfigurierte Provider

Statische Service-Liste erzeugte dauerhaft graues Tidal/Qobuz etc. Server
liefert Availability; User Preferences wählen sichtbare Provider, Default nur
konfigurierte.

### <a name="dd-a9"></a> DD-A9 — Artist-Image-Picker fehlte trotz Override-Feld

Album-Picker und Artist-Override existierten, aber kein Artist-Options-
Endpoint/Modal. Der Artist-Picker soll die gemeinsame Artist-Image-Engine und
die festgelegte Providerfoto→Embedded-Reihenfolge nutzen.

### <a name="dd-g1"></a> DD-G1 — Discography-Match verschluckt gleichnamige Single

Nach ID- und bucket-gleichem Titelmatch fiel `_match_existing` auf
`candidates[0]` über Bucketgrenzen. Single „Faith“ konnte auf Album „Faith“
fallen; `_merge_external_id` überschieb dann die Album-ID mit der Single-ID.

**Folge:** Single fehlt als Row und Album lädt beim nächsten Refresh die
falsche Tracklist. Cross-Bucket-Fallback nur ohne eigene Provider-ID;
abweichende vorhandene ID nie still überschreiben.

### <a name="dd-g2"></a> DD-G2 — Album-ReplayGain aktualisiert Tag-Cache bei Path Mapping nicht

Nach Tag Write suchte das Update per aufgelöstem Pfad, gespeichert war die
Media-Server-Sicht. Bereits vorhandene File-/Track-IDs gingen in einer Liste
verloren. Update muss über stabile File-ID laufen.

### <a name="dd-g3"></a> DD-G3 — Track-ReplayGain invalidiert Query nicht

Success setzte nur lokalen Done-State; `has_replaygain` erschien erst bei
fremdem Refetch. Der gleiche Query-Invalidation-Vertrag wie Album/Bulk ist
erforderlich.

### <a name="dd-g4"></a> DD-G4 — Autolink füllt Missing-Slot nicht und erzeugt Duplikat

Matching kannte nur Spotify-ID und exakten Titel. „One Dance“ vs. „One Dance
(feat. …)“ erzeugte neue File-Row, während die Wanted-Row missing blieb und
erneut lud.

**Korrekturvertrag:** `dedup_title_key` plus eindeutiger Disc/Track-Slot vor
Create; direkte IDs bleiben stärker.

### <a name="dd-g5"></a> DD-G5 — Lyrics-Badge widerspricht Lyrics-Tab

`has_lyrics` prüfte nur `lyrics`, der Tab auch `unsyncedlyrics`; LRC-Sidecars
waren ebenfalls möglich. Eine gemeinsame Ableitung ist erforderlich.

### <a name="dd-g6"></a> DD-G6 — Search-Fußnote behauptet fälschlich manuellen Rescan

Die UI forderte nach Download „Refresh & Scan“, obwohl Autolink fertige Files
automatisch verknüpft. Das erzeugte unnötige Full Scans und falsche
Erwartungen.

### <a name="dd-g7"></a> DD-G7 — Reorganize war fire-and-forget

Das Modal meldete nur „N queued“; Kollisionen/Fehler, Cancel und Completion
waren unsichtbar. Bestehendes Queue-API/Panel muss bis terminal pollen.

### <a name="dd-g8"></a> DD-G8 — Weitere Scope- und Default-Fehler

- Auto-Monitor setzte Flags auch bei explizitem Unmonitor-Veto.
- Retry filterte nur `primary_artist_id`, Index/Prune über Junction.
- Autolink rief Wanted mit hartem Profil 1 auf.
- Artist-Slow-Path scannte die ganze Tabelle und ignorierte External IDs.
- Track-Suchquery verlor Albumkontext bei generischen Titeln.

Die jeweiligen Korrekturverträge sind: explizites Veto bewahren,
Junction-Scope konsistent nutzen, Default-Profil dynamisch auflösen, ID-Match
vor Name und serverseitige scoped Search.

---

## 6. Branch-Review-Findings vom 19. Juli 2026

Mehrere Branch-Funde überlappen spätere Regressionen:

| Branch-ID | Ausführliche Diagnose |
|---|---|
| A1 | [H-18](#h-18) — Feature-off macht native Repair-Suite zum stillen No-op |
| A2 | [M-08](#m-08) — Reorganize-Findings/alte IDs verloren |
| A4 | [M-07](#m-07) — lose Files verlieren Quality-/Repair-Coverage |
| A6 | Playlist-Multiprofil-Dispatch, siehe F-09 und M-09 |
| A7/A8 | [M-15](#m-15) plus fehlender `lib2_entity`-Shape-Read |
| A9 | [H-07](#h-07) — Name-only Watchlist-Fallback |
| A10 | Feature [F-12](library-v2-features.md#feat-acq-review) |

### <a name="br-01"></a> BR-01/A3 — Discography-Refresh verliert Content-Type-Filter

Der native Ersatz verwendete keine Live/Remix/Acoustic/Compilation/
Instrumental-Filter des Watchlist-Scanners. Ausgeschlossene Releases konnten
automonitored und gewishlistet werden. Der native Pfad muss dieselben Artist
Settings auswerten.

### <a name="br-02"></a> BR-02/A5 — Refresh überspringt nie manuell expandierte Artists

Ein Filter auf `discography_synced_at IS NOT NULL` ließ importierte Artists
ohne früheren Update-Discography-Klick dauerhaft ohne periodisches Backfill.
Scheduled Refresh muss alle fachlich monitored Artists abdecken; Marker
steuert Erst-/Re-Expansion, nicht grundsätzliche Teilnahme.

### <a name="br-03"></a> BR-03/A11 — Cover-Embed und Write Tags teilen denselben Mutex

Beide starteten Jobkind `retag`. Unmittelbar nach Cover-Wechsel lieferte Write
Tags einen verwirrenden 409. Entweder denselben Lauf sichtbar wiederverwenden
oder getrennte, fachlich benannte Jobs/Scopes mit korrekter Serialisierung.

### <a name="br-04"></a> BR-04/A12 — Fuzzy Matching umgeht kanonisches Gate

Lokaler Threshold 0,72 umging `artist_name_matches` mit 0,85; ASCII-
Normalisierung machte zwei CJK-Namen leer und damit perfekt ähnlich.
Projektweiten Unicode-Normalizer und Match-Gate nutzen.

### <a name="br-05"></a> BR-05/A13 — Watchlist-Sync nutzt abweichende Whitespace-Normalisierung

`strip().casefold()` kollabiert internen Whitespace nicht. Ein Artist mit
doppeltem Leerzeichen konnte beim Autolink matchen, beim Watchlist-Abgleich
aber nicht. Ein kanonischer Normalizer für alle Pfade.

### <a name="br-06"></a> BR-06/A14 — Quality-Ranking doppelt im Frontend

Interactive Search und Automatic Grab hatten fast identische, getrennte
TypeScript-Ranker. Neue Formate konnten unterschiedliche „beste“ Kandidaten
erzeugen. Ranking gehört vollständig in den Server; UI zeigt nur Resultat und
Erklärung.

### <a name="br-07"></a> BR-07/A15 — Component Artist defaultet monitored

Eine Helper-Signatur hatte `monitored=1`, obwohl Schema und Produktregel 0
verlangen. Auch wenn der damalige Caller explizit übergab, lädt der Default
eine spätere Regression ein. Sicherer Default 0 bzw. Pflichtparameter.

### <a name="br-08"></a> BR-08 — Reconcile verursacht Leerlauf-Query-Flut

Der stündliche Job baute pro Wanted Track ein volles Payload mit mehreren
Queries, schrieb unveränderte Regeln und hatte Profil-N+1. Er darf nur Deltas
spiegeln, Profile in die Auswahl joinen und No-op-Writes überspringen.

### <a name="br-09"></a> BR-09 — Wiederholte PRAGMA-/IN-Clause- und Scope-Probleme

`PRAGMA table_info` wurde pro Spalte statt Tabelle gelesen; IN-Placeholder-
Logik war dupliziert und riskierte SQLite-Variablenlimits; ein `scoped`
Boolean verzweigte den Wishlist-Prozessor an vielen Stellen; Progress mit
`automation_id=None` wurde unsichtbar. Gemeinsame SQL-Helper, Scope-Objekt und
expliziter Progress-Kontext reduzieren Drift.

---

## 7. <a name="orphan-bug"></a> Quarantäne-Approve wird später als Orphan erkannt

### Symptom

1. Ein Song liegt nach Integrity-/AcoustID-/Bitdepth-Fail in Quarantäne.
2. Der Nutzer klickt One-Click Approve.
3. Der Song importiert erfolgreich und erscheint in der Library.
4. Ein späterer `orphan_file_detector` meldet genau dieses File als Orphan.

Kein Rename und kein offensichtlicher Crash sind beteiligt. Der Nutzer hat
das Verhalten auch auf älteren Ständen erlebt; es ist daher nicht bloß eine
branch-lokale V2-Regression.

### Empirisch ausgeschlossen

**Sidecar verliert `track_info` nicht:** Ein realistischer Context überlebt
`serialize_quarantine_context → json.dumps → json.loads` verlustfrei.

**Kein stale Final Path:** Alle vier `move_to_quarantine`-Calls für Integrity,
Duration, Quality und AcoustID passieren vor dem finalen `safe_move_file`.
`_final_processed_path/_final_path` existieren beim Sidecar-Schreiben noch
nicht; der Reimport berechnet das Ziel frisch.

### Nicht mit Acquisition-Recovery verwechseln

`acquisition_quarantine_recoveries` löst Crash-Atomicity beim „Recover to
Staging“-Fallback für dünne Sidecars. Hier gibt es keinen Crash: Import wirkt
erfolgreich, erst später fehlt die Katalogzuordnung.

### Arbeitshypothese

`link_download_into_library_v2()` bricht ohne direkte V2-ID und ohne
Titel+Artist ab:

```python
if not direct_track_id and not direct_album_id and (not title or not artist_name):
    return None
```

Legacy-Registrierung und History können trotzdem erfolgreich sein; nur
`lib2_track_files` fehlt. Der native Orphan Detector baut bekannte Pfade aus
aktiven V2-Subjects und V2-Tag-/Filename-Fallbacks. Ohne Entity/File-Row kann
er den Song nicht finden.

Simple Downloads verwenden in vorhandenen Tests ausdrücklich leeres
`track_info`. Ob der reale Approve-Fall aus genau diesem Eingang stammt, ist
die noch zu beweisende Verbindung.

### Reproduktion vor Fix

Den bestehenden Harness in `tests/imports/test_import_pipeline.py` nutzen:

1. echte Quarantäne via Integrity-Check erzwingen;
2. `approve_quarantine_entry()` real aufrufen;
3. Reimport mit restauriertem Context und dem echten Approve-Bypass ausführen;
4. `link_download_into_library_v2` beobachten;
5. `lib2_track_files` und anschließenden Orphan-Scan prüfen;
6. Matrix aus Simple Download mit leerem `track_info` und regulärem Match mit
   vollständigem Context.

Erst ein roter Test, der die Kette beweist, autorisiert eine Korrektur. Eine
unbestätigte Hypothese darf nicht zur Datenmodellmutation werden.

### Relevante Pfade

- `web_server.py` — Quarantäne Approve/Recover Routes;
- `core/imports/quarantine.py` — Approve und Recover;
- `core/imports/pipeline.py` — Quarantäne und Reimport;
- `core/imports/side_effects.py` — Provenance/Autolink-Hook;
- `core/library2/autolink.py` — Early Return;
- `core/library2/maintenance_subjects.py` — bekannte V2-Files;
- `core/repair_jobs/orphan_file_detector.py` — Scanlogik.

---

## 8. Historische Diagnosen aus dem früheren Monolith

Diese Root Causes waren im großen `library-v2.md` ausführlich dokumentiert
und gingen in der ersten Vier-Dateien-Konsolidierung vollständig verloren.

### <a name="hist-source-info"></a> Source Info meldet trotz Provenienz „No download source data“

Provenienz existierte in Legacy-/Downloadtabellen, der V2-Read versuchte aber
die lokale V2-ID als Legacy-/Server-ID zu verwenden oder matchte nur eine
unvollständige ID-Achse. Titel-/Artist-Fallback konnte zudem bei mehreren
Versionen falsch zuordnen.

**Korrekturvertrag:** typisierte V2-, Legacy-, Server- und Download-IDs bis
zum Read tragen; harte Korrelation vor Textfallback; Source Info nennt
Service, User, ursprünglichen File-/Release-Namen, Quality und History.

### <a name="hist-partial-monitor"></a> Teil-Import monitort das gesamte Album

Beim Import einzelner gewünschter Tracks wurde Album-/Parent-Intent als
Track-Intent interpretiert. Dadurch konnten alle fehlenden Tracks des Albums
wanted werden, obwohl der Nutzer nur eine Teilmenge gewählt hatte.

**Korrekturvertrag:** Import-/Search-Context trägt die expliziten Track-IDs.
Album-Monitoring wird nur durch eine ausdrückliche Albumaktion gesetzt;
Materialisierung eines Parents ist keine Monitorentscheidung.

### <a name="hist-track-number"></a> Tracknummer-Kollision und fehlender Healing-Pfad

Fehlende/korrupt gelesene Tracknummern konnten bei Albums alle Files auf 1
oder wiederholte 2/3/4 setzen. Ein vorhandener Heilungsalgorithmus lief für
Bestandsalben nicht, weil nur der Neuimportpfad ihn aufrief. Multi-Disc-
Sollwerte wurden außerdem aus der File-Teilmenge statt der vollständigen
Edition abgeleitet.

**Korrekturvertrag:** vollständige Provider-/Edition-Tracklist plus Disc ist
Soll; Scan-Reihenfolge ist nur kontrollierter Fallback; Healing läuft auch
für vorhandene Albums; File-/Legacy-/V2-Tags und DB werden zusammen
aktualisiert.

### <a name="hist-date"></a> Rohes ISO-Datum und falsche Date-Diffs

Provider-/Legacy-Datumswerte wie Datum, Timestamp und Zeitzonenvariante
wurden nicht auf eine gemeinsame Release-Date-Repräsentation normalisiert.
UI zeigte rohe ISO-Werte und Retag meldete reine Formatunterschiede als
fachliche Änderung.

**Korrekturvertrag:** kanonisches Release-Date für Anzeige/Matching; Tag
Writer vergleicht semantisch äquivalente Werte und überschreibt nicht allein
wegen Sekunden-/Zeitzonenformat.

### <a name="hist-all-releases"></a> „All Releases“ lädt nicht im Startzustand

Discography-Fetch war nur an den expliziten Toggle-Klick gebunden. War die
Route bereits mit `releases=all` geöffnet, fand kein Fetch statt.

**Korrekturvertrag:** Datenbedarf aus dem aktuellen Route-State ableiten, nicht
aus dem Klickevent. Deep Link und Reload verhalten sich wie manueller Toggle.

### <a name="hist-metadata-missing"></a> Missing Track zeigt vollständige Tags

Ein fileless Placeholder erbte einen positiven Metadata-Status aus erwarteten
Providerfeldern. Die UI sagte „All expected tags are present“, obwohl keine
Datei und damit keine geschriebenen Tags existierten.

**Korrekturvertrag:** Provider-Metadaten, Tag-Snapshot und physische
File-Präsenz getrennt modellieren. File-Tag-Erfolg setzt ein tatsächlich
gelesenes File voraus.

### <a name="hist-import-performance"></a> Import skaliert durch serielle Precache-Arbeit schlecht

Artwork-, Tracklist- und Tag-Precache sowie große Row-Mengen lagen teilweise
seriell am Abschlussweg. Bei tausenden Songs blieb UI lange ohne echten
Fortschritt; Artwork-Fetch konnte den fachlich fertigen Katalogimport
blockieren.

**Korrekturvertrag:** bounded Parallelität, monotone Phasen/Zähler, Artwork-
Precache vom kritischen Importabschluss entkoppeln und idempotent fortsetzen.
Kein unbounded Thread-Fanout und kein vollständiges `fetchall()`.

### <a name="hist-import-data-loss"></a> Importer verliert vorhandene File-Metadaten

Legacy-Dateieigenschaften wie ReplayGain, Lyrics, BPM, Style, Mood, Label,
Explicit, Disc-/Tracknummer und descriptive Metadata erreichten V2 oder den
Tag-Cache nicht vollständig. Ein späterer Refresh konnte einiges heilen, aber
der erste Read zeigte falsche „fehlt“-Zustände.

**Korrekturvertrag:** File-Tags direkt nach Import über den gemeinsamen
Tag-Cache lesen; Providerdeskription und File-Truth getrennt halten; alle
reicheren Felder durch API und Edit/Retag führen.

### <a name="hist-tag-status"></a> Tag-Status täuscht ungeprüftes Cover oder Tags vor

Ein grünes `tags ✓` konnte allein aus DB-/Providerfeldern abgeleitet sein und
externes Artwork wie eingebettetes File-Cover behandeln. Die Anzeige wurde
damit zugleich falscher Status und unklarer Fix-Button.

**Korrekturvertrag:** Text-Tags, Embedded Cover, externe/cache Artwork,
ReplayGain, Lyrics und Verification getrennt ausweisen. Ein klickbares Badge
startet nur den passenden Fix und zeigt laufende Arbeit bzw. Fehler sichtbar.

### <a name="hist-lyrics-path"></a> Lyrics-Fix meldet „File not found“, obwohl File existiert

Lyrics-/ReplayGain-Aktionen verwendeten stale oder nicht gemappte V2-Pfade.
Die reale Datei konnte am reorganisierten bzw. aufgelösten Ort liegen, während
`lib2_track_files.path` oder ein roher `os.path.exists` auf die falsche Sicht
zeigte.

**Korrekturvertrag:** jeder Filezugriff durch `resolve_lib2_path`, vorherige
Reorganize-Pfadsynchronität sicherstellen und Fehlermeldung mit Root-/Mapping-
Diagnose unterscheiden.

### <a name="hist-dev-environment"></a> Falsche Dev-Startart lässt Features scheinbar fehlen

Eine Nutzer-Bugsession lief nicht über den vorgesehenen `dev.py`-/Frontend-
Buildpfad. Dadurch wurde ein stale UI-Bundle gegen neuen Backend-Code geprüft;
bereits vorhandene Match-Chips und Funktionen wirkten „fehlend“.

**Korrekturvertrag:** Reproduktion nennt exakten Start-/Buildpfad, Commit und
geladene Assets. Vor einer Codekorrektur prüfen, ob Backend und WebUI aus
demselben Stand laufen.

---

## 9. Performance-Findings: Artist-Liste/Artwork spürbar langsamer als Legacy (25. Juli 2026)

Nutzerbeobachtung: Die Legacy-Library rendert die Artist-Liste inkl. Bildern
merklich schneller als Library V2 — auch dann, wenn der Artwork-Cache bereits
warm ist. Vergleich von Endpunkten und Query-Pfaden ergab vier unabhängige
Ursachen; keine ist reines Bild-Fetching allein.

### <a name="perf25-01"></a> Finding 1 — `os.stat()` pro Artist bei jedem List-Request

**Ort:** `api/library_v2.py:264-277` (`_artwork_url`), aufgerufen aus
`lib2_list_artists()` für jede Zeile der Seite.

Der Cache-Busting-Parameter `?v=<mtime>` wird durch einen synchronen
`Path.stat()`-Syscall pro Artist pro Listenaufruf gebaut. Bei 75 Artists pro
Seite sind das 75 Dateisystemzugriffe im Request-Thread, bevor überhaupt JSON
zurückgeht — unabhängig davon, ob das Artwork selbst schon gecacht ist.
Legacy macht in seinem List-Handler keinerlei Dateisystemarbeit; `thumb_url`
ist eine bereits befüllte DB-Spalte.

**Korrekturvertrag:** mtime/Version in-memory oder als DB-Spalte cachen und
nur beim (Re-)Schreiben des Artwork-Files aktualisieren, statt bei jedem
List-Request zu stat'en; alternativ Versions-Parameter entfernen und stattdessen
auf `ETag`/`If-Modified-Since` am Artwork-Endpoint selbst setzen (der bereits
`conditional=True` nutzt).

### <a name="perf25-02"></a> Finding 2 — Kalte Artist-Artworks lösen synchron, sequenziell und blockierend auf

**Ort:** `core/library2/provider_adapters.py:791-832` → `fetch_artwork_url` →
`core/metadata/artist_image.py:48-197` (`_get_artist_image_from_source`),
aufgerufen aus `build_artwork`/`_build_artwork_unlocked`
(`core/library2/artwork.py:455-548`) im kalten Pfad von
`GET /api/library/v2/artwork/<kind>/<id>` (`api/library_v2.py:1996-2052`).

Anders als der Artwork-Picker, der Quellen parallel über einen
`ThreadPoolExecutor` befragt (`core/metadata/art_lookup.py:601-613`), probiert
dieser Fallback-Pfad die konfigurierten Provider-Quellen **sequenziell**, mit
je einem blockierenden HTTP-Call pro Quelle. Bei Treffer folgt zusätzlich ein
synchroner Download plus vollständiger Pillow-Decode/Resize/Re-Encode zweier
JPEG-Varianten — alles im Request-Thread. Ein Single-Flight-Lock pro Entity
verhindert Doppelarbeit für denselben Artist, aber nicht über mehrere kalte
Artists auf einer Seite hinweg.

Das ist der Preis für das ausdrückliche Designziel „kein Mediaserver nötig“
(§2.1): Legacy lässt bei Plex/Jellyfin/Navidrome-Installationen den Browser
das Bild direkt vom Mediaserver laden und macht serverseitig nichts.

**Korrekturvertrag:** Erste Ansicht eines Artists darf nicht auf Live-Provider-
Resolution blockieren. Placeholder sofort ausliefern, Auflösung/Caching im
Hintergrund anstoßen (bestehenden Precache-Mechanismus eager pro
Artist bei Add/Import triggern statt nur im großen Batch-Job), und den
sequenziellen Provider-Fallback wie im Picker parallelisieren.

### <a name="perf25-03"></a> Finding 3 — Artist-Listen-Query berechnet Aggregate live, die Legacy gar nicht kennt

**Ort:** `core/library2/queries.py:124-253` (`list_artists`).

Mehrere gejointe/materialisierte CTEs pro Seitenaufruf: Album-/Single-Counts
über eine `UNION` aus `lib2_album_artists`/`lib2_track_artists`, Wanted-/
Monitored-Counts über einen Join mit `lib2_wanted_tracks`, eine
Window-Function (`ROW_NUMBER() OVER (PARTITION BY tf.track_id ...)`) über
alle Dateien der Seiten-Artists sowie ein `SUM`-Größen-Rollup darüber. Legacy
(`database/music_database.py:13073-13114`) ist eine flache
`WHERE/ORDER/LIMIT`-Query; Album-/Track-Counts werden dort nur einmal
batched für die sichtbare Seite nachgeladen, keine Wanted-/Monitoring-Kaskade
und kein Datei-Größen-Rollup auf der Listenansicht.

**Korrekturvertrag:** Prüfen, ob Wanted-/Monitored-/Size-Rollups in der
eingeklappten Listenansicht überhaupt gebraucht werden; wenn ja, pro Zeile
lazy nachladen oder als invalidierbaren Cache statt bei jedem Request live
per CTE zu berechnen.

### <a name="perf25-04"></a> Finding 4 — Precache läuft nicht zuverlässig vor dem ersten Seitenbesuch

**Ort:** `core/library2/precache_all_artwork` (`core/library2/artwork.py:571-646`).

Der Background-Precache-Job existiert bereits und ist für genau dieses
Problem gebaut, deckt aber nur den Zustand nach dem letzten Lauf ab. Neu
hinzugefügte Artists, frische oder teilmigrierte Installationen laufen kalt,
wenn die Seite besucht wird, bevor der Precache durchgelaufen ist — dann
greift Finding 2 in voller Härte.

**Korrekturvertrag:** Precache nach jeder Discography-/Library-Änderung
prompt anstoßen (nicht nur nach vollständigen Imports), damit der kalte Pfad
im Alltag selten getroffen wird.

### <a name="perf25-05"></a> Finding 5 — Keine Virtualisierung nötig; Vergleich mit Lidarr/Sonarr ist kein Frontend-Rendering-Problem, sondern Server-Roundtrip pro Seite plus unnötige Bildverarbeitung

**Ort:** `webui/src/routes/library-v2/-ui/library-v2-page.tsx:4243-4256` (`ArtistCards`)
vs. `webui/static/library.js` (Legacy-Grid); `core/image_cache.py:169-230`
vs. `core/library2/artwork.py:222-267` (`_normalize_jpeg_variants`).

Nutzerbeobachtung: Lidarr/Sonarr scrollen tausende Einträge praktisch
instant. Geprüft, ob das an fehlender DOM-Virtualisierung liegt: **Nein** —
weder Legacy noch V2 virtualisieren, aber beide begrenzen die DOM-Größe
bereits serverseitig auf eine Page (75–100 Einträge, Prev/Next-Pagination,
kein Infinite-Scroll). Das Rendering selbst ist in beiden identisch schnell.

Der reale Unterschied zu Lidarr/Sonarr ist architektonisch: Diese Apps laden
die komplette (kleine) Metadatenliste einmal und virtualisieren rein
client-seitig — kein Server-Roundtrip pro Scroll/Seitenwechsel. SoulSync
(Legacy wie V2) macht bei jedem Seitenwechsel einen vollen Request; V2s
Roundtrip ist zusätzlich durch Finding 1+3 schwerer als Legacys.

Zusätzlich, unabhängig vom Mediaserver-Vergleich aus Finding 2: Selbst im
„eigenständigen“ Legacy-Pfad ohne Plex/Jellyfin/Navidrome
(`core/image_cache.py:169-230`, `/api/image-cache/<hash>`) werden
Originalbytes 1:1 auf Platte gestreamt — **keine** Bildverarbeitung. V2s
`_normalize_jpeg_variants` (`core/library2/artwork.py:222-267`) dekodiert
dagegen bei jedem kalten Cache-Miss das Bild vollständig mit Pillow,
EXIF-transponiert es, konvertiert nach RGB und kodiert zwei
JPEG-Varianten (voll + Thumbnail) mit `optimize=True` neu — das ist
zusätzlich zur Netzwerk-Latenz aus Finding 2 spürbare CPU-Zeit pro Bild, die
Legacys eigener Cache gar nicht aufwendet.

**Korrekturvertrag:** Keine Virtualisierung nötig (DOM-Größe ist bereits
begrenzt). Statt: (a) prüfen, ob `optimize=True` und die doppelte
Encode-Pass wirklich nötig sind oder ob ein günstigerer Pfad (z. B. nur
Thumbnail sofort, volle Variante lazy) reicht; (b) den Seitenwechsel selbst
beschleunigen (Finding 1+3), damit der unvermeidbare Server-Roundtrip so
leicht wie möglich bleibt.

**Priorität (Aufwand/Nutzen):** Finding 1 (sofort, keine Verhaltensänderung)
→ Finding 2 (größter Effekt, Designziel-bedingt) → Finding 5b (Pillow-Overhead
im kalten Pfad) → Finding 3 → Finding 4.

---

## 10. Search-Ergebnis „In Your Library" verlinkt auf die alte Library statt auf Library V2 (25. Juli 2026)

Nutzerbeobachtung: Sucht man auf der Search-Seite einen Artist, der bereits
in der Library ist ("In Your Library"-Badge), führt ein Klick auf das
Ergebnis zur **alten** Library-Detailseite statt zu Library V2.

### <a name="find25-search-01"></a> Finding 1 — Frontend-Logik ist korrekt; der fehlende Link kommt vom Backend-Merge

**Ort:** `webui/static/search.js:460-476` (`renderDropdownResults`, DB-Artists-
Sektion) und identisch `webui/static/downloads.js:6626` (globales
Such-Widget).

Der Klick-Handler ist bereits richtig geschrieben:

```js
href: artist.library_v2_id
    ? `/library-v2?artist=${encodeURIComponent(artist.library_v2_id)}`
    : buildArtistDetailPath(artist.id),
```

Das Problem liegt nicht im Frontend, sondern darin, dass `artist.library_v2_id`
oft gar nicht gesetzt ankommt — dann greift der Fallback
`buildArtistDetailPath(artist.id)` (`webui/static/init.js:2988-3001`), der auf
die alte `/artist-detail/library/<legacy_artist_id>`-Route
(`webui/static/library.js:812`) verweist.

### <a name="find25-search-02"></a> Finding 2 — Root Cause: Legacy↔lib2-Verknüpfung schlägt beim Merge fehl

**Ort:** `core/search/orchestrator.py` `_build_db_artists()` (Zeilen 121-235).

`db_artists` wird aus der **Legacy**-`artists`-Tabelle gebaut
(`deps.database.search_artists(...)`, Zeile 123) und versucht anschließend,
`library_v2_id` nachträglich zu mergen — entweder über
`lib2_artists.legacy_artist_id` (Zeile 196-197) oder über eine gemeinsame
Provider-ID (Spotify/iTunes/Deezer/MusicBrainz/Amazon/Tidal/Qobuz, Zeilen
148-172, 212-217). Gelingt keins von beidem, bleibt der Eintrag ohne
`library_v2_id`, und der Merge kann sogar einen zweiten, separaten
V2-native-Pseudo-Eintrag für denselben Artist anhängen (Zeilen 224-229).

Ursache, warum der Merge scheitert: `core/library2/autolink.py
_find_or_create_artist()` (Zeilen 118-183) legt bei jedem normalen
Download-Abschluss ohne bereits vorhandenen lib2-Artist eine neue
`lib2_artists`-Zeile an — der `INSERT` (Zeile 181-183) setzt bewusst **kein**
`legacy_artist_id` (bleibt `NULL`), nur eine Provider-ID, falls dieser
konkrete Download eine mitgebracht hat. Das deckt sich mit dem dokumentierten
Verhalten in `core/library2/native_enrich.py:1-10`: „Artists born inside
lib2 … carry `legacy_artist_id = NULL`."

Der einmalige Startup-Bootstrap (`core/library2/bootstrap.py`, angestoßen von
`web_server.py:31974-31996`) importiert die Legacy-Library nur **einmal**
und läuft danach nie wieder. Jeder Artist, der **nach** diesem einen Lauf zum
ersten Mal über einen normalen Download in die Library kommt, bekommt also
eine lib2-Zeile ohne `legacy_artist_id`. Existiert daneben eine
Legacy-`artists`-Zeile für denselben Artist (z. B. aus einem
Mediaserver-Scan) ohne dieselbe Provider-ID, verbindet der Merge in
`_build_db_artists` beide Zeilen nicht — der Legacy-Eintrag bleibt ohne
`library_v2_id`, und `search.js` fällt korrekt, aber unerwünscht auf den
Legacy-Link zurück.

**Kein dedizierter Lookup-Endpoint** „gegebene Legacy-ID → lib2-ID" existiert
im Backend (kein Treffer für `legacy_artist_id`/`by-legacy` als Route in
`web_server.py`). Die korrekte V2-URL-Form ist bereits an anderer Stelle
etabliert: `/library-v2?artist=<lib2_artists.id>`
(`webui/src/routes/library-v2/-ui/library-v2-page.tsx:4254`) — exakt das,
was `search.js:473` schon voraussetzt, wenn die ID vorhanden ist.

**Testlücke:** `tests/search/test_search_orchestrator.py:283-324` deckt nur
den Fall ab, in dem der Provider-ID-Match gelingt. Kein Test prüft den Fall
„Legacy-Zeile existiert, lib2-Zeile existiert, aber weder
`legacy_artist_id` noch eine gemeinsame Provider-ID verbinden beide" — genau
der hier beschriebene Fehlerfall.

**Korrekturvertrag:** Der Fix gehört in den Orchestrator-Merge, nicht ins
Frontend (das ist bereits korrekt). Kandidat: `lib2_artists.legacy_artist_id`
nachträglich befüllen, sobald `_build_db_artists` einen Namens-/Fuzzy-Match
zwischen Legacy- und lib2-Zeile findet und noch keine andere Verknüpfung
existiert — plus einen Regressionstest für genau den Fall ohne
`legacy_artist_id` und ohne gemeinsame Provider-ID.

---

## 11. Abnahmeinvarianten für den Bug-Cluster

| Aktion | Ausgang | Erwartung |
|---|---|---|
| Track Automatic Search | unmonitored + not found | keine Wishlist-/Monitoränderung; terminaler Fail |
| Track Automatic Search | unmonitored + success | reales File am exakten V2-Track; kein persistenter Wishlist-Eintrag |
| Track Automatic Search | monitored + fail | bestehender Wanted-Intent bleibt; kein künstlicher neuer Intent |
| Manual Grab | success | File, Verification, Runtime und V2 synchron; nicht queued |
| Manual Grab | quarantine | Sidecar und Korrelation vorhanden; keine falsche Completion |
| Human Approve nach Restart | success | File verknüpft, Scan koalesziert, Grab geschlossen |
| Post-Move-Exception | Ziel existiert | kein physischer DB-Orphan; idempotente Reconciliation |
| Refresh & Scan | erster gesunder Miss | `missing_suspected`, kein Download/Delete |
| Refresh & Scan | zweiter gesunder Miss | `missing_confirmed`, Wanted korrekt |
| Reorganize | Multi-File-Track | nur konkretes File erhält neuen Pfad |
| Bundle-Import | zwei konkurrierende Caller | exakt ein Import-Dispatcher |
| Artist-Seite | viele Releases | eine gemeinsame Queue-Status-Abfrage |

---

## 12. Branch-Review-Findings vom 25. Juli 2026 (Nacharbeit zu §9/§10)

Review des Branch-Diffs `library-overhaul` gegen `main` nach den Commits
`1a6758b5`, `d51e85d8`, `78bf84c9`, `a965e829`, `bca2ec04` und `d82ad12b` —
also genau der Korrekturen aus §9 und §10. Fünfzehn Findings, nummeriert nach
Schwere. Findings 1, 5, 6, 7 und 12 wurden zusätzlich direkt am Code
nachgeprüft; die übrigen sind Review-Aussagen ohne eigene Reproduktion.

Sammelaussage: Die §9/§10-Korrekturen wirken, verschieben die Arbeit aber in
einen Hintergrundpfad, dessen Fehler-, Lebenszyklus- und
Aktualisierungssemantik noch unvollständig ist (Findings 1, 2, 8, 9, 10), und
die neue Namensverknüpfung aus §10 ist über abgeschnittenen Ergebnisfenstern zu
großzügig (Findings 5, 7). Zwei Perf-Korrekturen kehren sich auf großen
Bibliotheken ins Gegenteil (Findings 3, 4).

**Status (25. Juli 2026, Nachtrag):** 13 der 15 Findings sind im selben
Aufwasch behoben — siehe die Statuszeile in jeder Finding-Überschrift unten
und die Zusammenfassung in [status §13](library-v2-status.md#13-branch-review-findings-vom-25-juli).
Offen bleiben Finding 2 und 10; beide brauchen zuerst die
Kaltstart-Vertrags-Entscheidung aus
[features F-01](library-v2-features.md#feat-artwork) und sind bewusst nicht
mitimplementiert.

### <a name="rev25-01"></a> Finding 1 — `_background_inflight` leakt beim Verbindungsfehler und sperrt die Entity dauerhaft — Behoben, 25. Juli 2026

**Ort:** `core/library2/artwork.py:645-668` (`schedule_artwork_build._run`).

Der `except`-Zweig um `database._get_connection()` macht `return False`, bevor
der zweite `try`-Block mit `finally: _background_inflight.discard(key)`
überhaupt betreten wird. Ein einzelner transienter Verbindungsfehler (SQLite
„unable to open database file" auf einem Docker-Bind-Mount, `EMFILE` unter
Last) lässt den Key dauerhaft in `_background_inflight`; jeder spätere
`schedule_artwork_build` für dieselbe Entity liefert `None`. Da der
HTTP-Kaltpfad seit `78bf84c9` nur noch plant und nichts mehr inline auflöst,
bleibt dieses Cover für die gesamte Prozesslaufzeit Placeholder.

**Testlücke:** `test_build_failure_does_not_pin_the_entity` deckt nur die
`build_artwork`-Exception ab — die erreicht das `finally` ja gerade.

**Korrekturvertrag:** Freigabe des Keys in genau einem `finally`, das den
gesamten `_run`-Körper inklusive Verbindungsaufbau umschließt, plus
Regressionstest mit fehlschlagendem `_get_connection`.

### <a name="rev25-02"></a> Finding 2 — Kaltes Cover kann dauerhaft Placeholder bleiben — Offen (Produktentscheidung)

**Ort:** `api/library_v2.py:2053-2065` und
`webui/src/routes/library-v2/-ui/library-v2-page.tsx:314-345`.

Beim ersten Besuch einer ungecachten Library liefert `_apply_artwork_urls` eine
URL ohne `?v=` (`artwork_version` gibt 0), der Endpoint antwortet 404 und plant.
Der Client versucht es nach 1,5 s / 4 s / 9 s erneut (`ARTWORK_RETRY_DELAYS_MS`,
zusammen 14,5 s) und gibt dann endgültig auf. Ein kalter Build (Provider-Walk +
HTTP-Download + zwei Pillow-Encodes) liegt regelmäßig darüber, und 75 Zeilen
serialisieren hinter ~6 Workern — die meisten Zeilen verbrauchen ihr
Retry-Budget, bevor ihr Build fertig ist. Die URL ändert sich erst mit einem
Listen-Refetch (dann mit `?v=<mtime>`), die Artists-Query hat kein
`refetchInterval`; `X-Artwork-Pending` hat im gesamten Repo null Konsumenten.
Das ist eine Regression gegenüber dem alten synchronen Pfad, der langsam war,
aber immer irgendwann gemalt hat.

**Korrekturvertrag:** Das Ergebnis des Hintergrund-Builds muss den Client
erreichen: entweder den Pending-Header tatsächlich auswerten und gezielt
nachfragen (Polling gegen den Endpoint statt drei fixer Versuche) oder nach
abgeschlossenem Build einen Refetch der sichtbaren Seite auslösen. Das
Retry-Budget an die reale Build-Dauer koppeln, nicht an eine Konstante. Die
Produktseite dieser Entscheidung gehört nach
[library-v2-features.md F-01](library-v2-features.md#feat-artwork).

### <a name="rev25-03"></a> Finding 3 — `_artwork_versions` kostet auf großen Bibliotheken mehr Syscalls als die 75 `stat()`, die es ersetzt — Behoben, 25. Juli 2026

**Ort:** `core/library2/artwork.py:100-130`.

`lib2_artwork/` hält zwei Dateien pro Artist und zwei pro Album (voll + `_t`).
Bei 20.000 Alben sind das ~80.000 Einträge; `DirEntry.stat()` ist unter Linux
ein echter Syscall pro Eintrag. Ein kalter Aufruf kostet also ~80.000 Syscalls,
um eine 75-Zeilen-Seite zu beantworten, die vorher 75 gekostet hat. Amortisiert
wird das nur, solange der Verzeichnis-mtime stabil bleibt — aber jeder
erfolgreiche `_build_artwork_unlocked` ruft `forget_artwork_versions()`, und
derselbe Branch stößt Hintergrund-Builds jetzt aus Listenrendering
(`api/library_v2.py:2059`), Autolink (`autolink.py:553`) und Discography-Expand
(`discography.py:611`) an. Auf einem importierenden oder Artwork-wärmenden
System wird der Snapshot laufend verworfen, der Listenrequest zahlt den vollen
Verzeichnis-Scan also nahezu bei jedem Aufruf. Der Snapshot-Dict bleibt zudem
für die Prozesslaufzeit im Speicher (in dieser Größenordnung zweistellige MB).

**Korrekturvertrag:** Nur die ~75 tatsächlich benötigten Dateinamen stat'en
oder pro Entity per LRU cachen — das behält den beabsichtigten Gewinn ohne die
Inversion.

### <a name="rev25-04"></a> Finding 4 — Voller Artwork-Verzeichnis-Scan auf dem Per-Download-Importpfad — Behoben, 25. Juli 2026

**Ort:** `core/library2/autolink.py:553` (`_warm_new_artwork`).

`link_download_into_library_v2` läuft einmal pro fertigem Download
(`core/imports/pipeline.py:529`, `core/imports/side_effects.py:398`).
`_warm_new_artwork` → `schedule_missing_artwork` → `_artwork_versions`
re-scandirt das komplette Cache-Verzeichnis, sobald der Snapshot verworfen war —
und jeder gleichzeitige Hintergrund-Build verwirft ihn. N Downloads gegen M
gecachte Bilder kosten grob O(N·M) `stat()`-Syscalls, synchron im
Import-Worker nach dem Commit. Auf einem NAS-Datenverzeichnis ist das exakt die
blockierende I/O, die §9 aus dem Weg räumen wollte.

**Korrekturvertrag:** `schedule_missing_artwork` prüft nur die zwei Dateinamen
der Zielentity oder bekommt den bereits bekannten Cache-Zustand übergeben.

### <a name="rev25-05"></a> Finding 5 — Namens-Backfill persistiert eine Identität aus einer Eindeutigkeitsprüfung über abgeschnittenen Fenstern — Behoben, 25. Juli 2026

**Ort:** `core/search/orchestrator.py:265-276` (Namensmatch) und `:130-148`
(`_backfill_legacy_link`).

`legacy_by_name` entsteht aus `deps.database.search_artists(query, limit=5)`,
`v2_ids_by_name` aus einer lib2-Query mit `LIMIT 10`. `len(candidates) == 1 and
len(v2_ids_by_name[name_key]) == 1` belegt Eindeutigkeit deshalb nur
**innerhalb dieser Fenster**, nicht in der Datenbank. Beispiel: drei Artists
„John Williams" in der Library, Suche „john" — die Fenster können problemlos
genau einen je Seite zeigen, der Code erklärt das Paar für eindeutig, und
`_backfill_legacy_link` schreibt `lib2_artists.legacy_artist_id` dauerhaft
fest. Der `NOT EXISTS`-Guard verhindert nur, eine **bereits belegte**
Legacy-ID zu stehlen — nicht, die falsche **freie** zu belegen. Der Diff hat
den Kommentar „names alone are deliberately not a dedup key" entfernt, ohne
eine gleichwertige Invariante zu setzen; die neuen Tests decken nur den
Einzeltreffer- und den Zwei-identische-lib2-Zeilen-Fall ab, nicht die
Truncation.

**Korrekturvertrag:** Eindeutigkeit vor dem Persistieren gegen die Datenbank
prüfen (`COUNT(*)` über den normalisierten Namen auf beiden Seiten, ohne
LIMIT). Der reine In-Memory-Merge darf großzügiger bleiben als der geschriebene
Link — nur das Schreiben braucht die harte Invariante.

### <a name="rev25-06"></a> Finding 6 — Eingeschaltete Size-Spalte zeigt „—" für jeden Artist — Behoben, 25. Juli 2026

**Ort:** `api/library_v2.py:1343-1350` (Ableitung von `include_size`),
`library-v2-page.tsx:5490-5498` (`useUiPreferencesMutation`) und `:4358-4363`
(Zelle).

`include_size` kommt aus `artist_table.columns.size`, Default `False`.
`useUiPreferencesMutation` macht ausschließlich `setQueryData` auf dem
`ui-preferences`-Key und invalidiert `LIBRARY_V2_QUERY_KEY` nie. Das Einschalten
der Spalte rendert deshalb sofort aus der gecachten Payload, in der jede Zeile
`total_size_bytes: 0` trägt — `total_size_bytes > 0 ? … : '—'` zeigt für alle
Artists „—", bis zufällig eine andere Mutation oder ein Window-Refocus die
Liste neu holt.

**Korrekturvertrag:** Entweder invalidiert die Preference-Mutation die
Artist-Liste, oder — besser, siehe Finding 11 — die Payload hängt gar nicht
erst an einer Preference.

### <a name="rev25-07"></a> Finding 7 — Der Such-Lesepfad schreibt und committet jetzt — Behoben, 25. Juli 2026

**Ort:** `core/search/orchestrator.py:130-148`, aufgerufen aus
`_build_db_artists` (GET Global Search).

Auf einer WAL-Datenbank mit aktivem Importer/Scanner blockiert das `UPDATE` bis
zum `busy_timeout` der Verbindung (30 s, `database/music_database.py:259`),
bevor der `except` es schluckt — eine Nutzer-Suche hängt an einer Reparatur,
die niemand angefordert hat. Der Versuch wiederholt sich außerdem bei jeder
Suche erneut, solange die Guard-Bedingungen weiter scheitern.

**Korrekturvertrag:** Die Reparatur gehört in einen Reconcile-/Maintenance-Job
— das Muster existiert im Codebase bereits — und nicht in den Request. Die
Suche bleibt lesend.

### <a name="rev25-08"></a> Finding 8 — Modulglobaler Executor: eingefrorene Konfiguration, kein Shutdown, unbegrenzte Queue — Behoben, 25. Juli 2026

**Ort:** `core/library2/artwork.py:615-640`.

`max_workers` wird beim ersten Aufruf aus dem zuerst eintreffenden
`config_manager` eingefroren — Caller ohne `config_manager` (Tests,
`schedule_missing_artwork`) bekommen den hartkodierten Default 6 —, spätere
Änderungen an `library_v2.artwork_cache_workers` bzw. `auto_import.max_workers`
wirken für diesen Pool nie, anders als bei `precache_all_artwork`, das pro Lauf
neu liest. Der Pool wird außerdem nie heruntergefahren: `concurrent.futures`
registriert einen threading-atexit-Hook, der die Non-Daemon-Worker joint — ein
an einem langsamen Socket hängender Provider-Download verzögert den
Interpreter-Exit und damit den Container-Shutdown nach SIGTERM. Und `submit`
hat keine Queue-Grenze: ein Client, der `/api/library/v2/artwork/artist/<n>`
über fortlaufende IDs abklappert, oder wiederholte Renders einer Seite mit
ungecachten Covern füllen die Queue schneller, als sechs Worker sie leeren —
jeder wartende Eintrag öffnet beim Lauf seine eigene DB-Verbindung.

**Korrekturvertrag:** Worker-Zahl pro Lauf aus der Config auflösen, Pool an den
App-Shutdown hängen, Queue begrenzen (Verwerfen statt unbegrenztem Wachstum).

### <a name="rev25-09"></a> Finding 9 — `forget_artwork_versions` kann von einem parallel laufenden Scan still zurückgenommen werden — Behoben, 25. Juli 2026

**Ort:** `core/library2/artwork.py:100-130`.

`_artwork_versions` liest `directory.stat().st_mtime_ns` **vor** dem `scandir`
und speichert das Ergebnis unter diesem Vorher-Stempel. Thread A liest Stempel
S und beginnt zu scannen; Thread B schreibt ein Cover und ruft
`forget_artwork_versions()` — ein No-op, weil A noch nichts gespeichert hat; A
speichert anschließend (S, Versionen ohne Bs Änderung). Auf einem Dateisystem
mit Sekundenauflösung für Verzeichnisstempel ist der mtime weiterhin S, der
stale Snapshot validiert, `artwork_version` liefert das alte Token,
`_apply_artwork_urls` das alte `?v=` — und der Endpoint liefert es mit
`Cache-Control: public, max-age=604800, immutable` aus. Der Browser behält das
überholte Cover damit eine Woche. Das verletzt direkt die Zusage aus F-01,
mutable Artwork-URLs nicht ohne korrekten Versionsparameter als `immutable`
auszuliefern.

**Korrekturvertrag:** Generationszähler, den `forget_artwork_versions` erhöht
und den der Store unter demselben Lock gegenprüft.

### <a name="rev25-10"></a> Finding 10 — Kein Negativ-Cache: Entities ohne auflösbares Bild werden bei jedem Render neu gewalkt — Offen (Produktentscheidung)

**Ort:** `api/library_v2.py:2053-2065` in Verbindung mit
`library-v2-page.tsx:317`.

`build_artwork` liefert `None` für eine Entity ohne embedded Cover und ohne
Providerbild — es wird nichts geschrieben, der nächste Request ist wieder ein
Kalt-Miss und plant erneut einen vollen Provider-Walk. `_background_inflight`
bündelt nur **gleichzeitige** Duplikate, nicht Wiederholungen über Renders
hinweg. Bei 75 bildlosen Artists auf einer Seite kostet ein Seitenbesuch bis zu
4 × 75 = 300 404-Requests (Erstversuch plus drei Client-Retries) und 75
Provider-Walks — bei jedem Besuch aufs Neue. Der alte synchrone Pfad hatte
denselben fehlenden Negativ-Cache, war aber natürlich auf einen Versuch pro
Request gedrosselt; die Retry-Schleife hebt diese Drosselung auf.

**Korrekturvertrag:** Kurzlebiger Negativmarker oder persistiertes „artwork
resolution failed at T" mit Backoff, bevor erneut gewalkt wird.

### <a name="rev25-11"></a> Finding 11 — Altitude: eine Tabellen-Preference entscheidet die Payload der gesamten Artist-Response — Behoben, 25. Juli 2026

**Ort:** `api/library_v2.py:1343-1350`.

`artist_table.columns.size` steuert genau eine Tabellenspalte, `include_size`
schaltet das Feld aber für die komplette `/api/library/v2/artists`-Response.
Die Card-/Grid-Ansicht, der Typ in `-library-v2.types.ts` (deklariert
`total_size_bytes: number`, nicht optional), künftige Exporte und jedes Skript
gegen den Endpoint bekommen still `0` — ununterscheidbar von einem Artist ohne
Dateien — ohne Möglichkeit, den echten Wert anzufordern. Zusätzlich zahlt jeder
Listenrequest einen `get_ui_preferences`-Read plus JSON-Parse.

**Korrekturvertrag:** Expliziter Request-Parameter (z. B. `?include=size`),
gesetzt von der Komponente, die die Spalte rendert. Das macht den
React-Query-Key vom Parameter abhängig und löst Finding 6 gleich mit.

### <a name="rev25-12"></a> Finding 12 — Beim `src`-Wechsel wird ein Frame mit altem Retry-Zähler committet — Behoben, 25. Juli 2026

**Ort:** `webui/src/routes/library-v2/-ui/library-v2-page.tsx:341-348`.

`url` wird im Render aus `attempt` berechnet, `attempt` aber erst im
`useEffect(…, [base])` zurückgesetzt — also nach dem Commit dieses Renders.
Steht eine Zeile auf `/artwork/artist/7` mit `attempt=2` und ändert ein Refetch
die Prop auf `/artwork/artist/7?v=1699`, committet React einmal
`…?v=1699&retry=2`; der Browser fordert eine garantiert HTTP-Cache-missende URL
an (dasselbe Bild wird zweimal geladen), erst danach setzt der Effect zurück und
rendert die saubere URL. Wird `src` stattdessen `''`, ist `''.includes('?')`
false, `url` wird der truthy String `'?retry=2'` — das `<img>` löst ihn gegen
das aktuelle Dokument auf und lädt die HTML-Seite als Bild, sichtbar als kurz
aufblitzendes Broken-Image vor dem Placeholder.

**Korrekturvertrag:** Retry-State an `base` koppeln (Reset im Render beim
Base-Wechsel oder State-Key), und das `retry`-Suffix nur an ein nicht-leeres
`base` hängen.

### <a name="rev25-13"></a> Finding 13 — Weggefallenes `optimize=True` vergrößert auch die Variante, die die Detailseiten ausliefern — Behoben, 25. Juli 2026

**Ort:** `core/library2/artwork.py:320-330`.

Die Begründung im Kommentar („a file the list view never requests") beschreibt
nicht die tatsächliche Nutzung: `<Artwork>` ohne `thumb`-Prop liefert die
Vollvariante im Album-Detail-Header (`library-v2-page.tsx:4481`), im
Artist-Detail-Header (`:4916`) und in den Match-Dialogen (`:960`, `:1054`) —
also bei jedem Detailseitenaufruf. Die ~5-10 % Mehrbytes fallen zusätzlich
dauerhaft auf Platte an, für jedes gecachte Cover der Bibliothek.

**Korrekturvertrag:** Entweder `optimize` auf der Vollvariante behalten und
stattdessen auf dem (kleineren, billiger zu kodierenden) Thumbnail weglassen,
oder den Mehrverbrauch auf Detailseiten ausdrücklich als akzeptiert
dokumentieren und den Kommentar korrigieren.

### <a name="rev25-14"></a> Finding 14 — Zwei Implementierungen von „ist dieses Artwork gecacht?" — Behoben, 25. Juli 2026

**Ort:** `core/library2/artwork.py:761` (`precache_all_artwork`) gegenüber
`:694` (`schedule_missing_artwork`).

`precache_all_artwork` prüft weiterhin per Entity mit
`(cache_dir / f"{kind}_{eid}.jpg").exists()`, während `schedule_missing_artwork`
den neuen Verzeichnis-Snapshot nutzt. Zwei Antworten auf dieselbe Frage im
selben Modul: ~40.000 `exists()`-Syscalls bei 20.000 Alben, die der Snapshot in
einem Durchgang beantworten könnte, plus Driftrisiko, sobald sich Namensschema
oder `is_cached_jpeg`-Behandlung ändern.

**Korrekturvertrag:** Eine Implementierung — abhängig von der in Finding 3
gewählten Lösung.

### <a name="rev25-15"></a> Finding 15 — Globaler PIL-Patch im neuen Formattest, Verbindungsfehlerpfad ungetestet — Behoben, 25. Juli 2026

**Ort:** `tests/library2/test_artwork_format.py:105` und
`tests/library2/test_artwork_background_build.py`.

`test_full_variant_skips_the_extra_huffman_optimize_pass` weist
`Image.Image.save` direkt auf der geteilten PIL-Klasse zu, statt die
`monkeypatch`-Fixture zu nutzen, die alle anderen Tests derselben Datei
verwenden: unter pytest-xdist oder jeder parallelen Ausführung schreibt ein
fremder JPEG-Encode in `saves` und `full, thumbnail = saves` bricht mit
`ValueError`. Unabhängig davon gelingt `_shim._get_connection` in
`test_artwork_background_build.py` immer, sodass der Verbindungsfehler-Pfad aus
Finding 1 — der einzige, der wirklich leakt — von keinem Test abgedeckt wird.

**Korrekturvertrag:** `monkeypatch` verwenden; Test mit fehlschlagendem
`_get_connection`.

**Priorität (Schwere/Aufwand):** Finding 1 (dauerhafter Fehlzustand, trivialer
Fix) → 6 und 12 (sichtbare UI-Fehler, klein) → 5 und 7 (persistierte
Fehlverknüpfung bzw. Write auf dem Lesepfad) → 2 und 10 (Kaltstart-Semantik,
braucht die Produktentscheidung aus F-01) → 3 und 4 (Perf-Inversion auf großen
Bibliotheken) → 8 und 9 → 11, 13, 14, 15.

**Nachtrag 25. Juli 2026:** In dieser Reihenfolge abgearbeitet bis auf 2 und
10, die weiterhin auf die F-01-Produktentscheidung warten. 3/4/8/9/14 wurden
im selben Umbau gelöst (ein Per-Entity-Mtime-Cache mit Generation-Marker statt
des Whole-Directory-Snapshots), 6 im selben Umbau wie 11 (expliziter
`?include=size`-Parameter).
