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

### Bestätigte Root Cause (26. Juli)

Bewiesen durch `tests/library2/test_autolink.py::
test_simple_download_never_gets_a_file_row` (grün — pinnt den bestätigten
Fehler deterministisch, siehe Status §16).

`link_download_into_library_v2()` bricht ohne direkte V2-ID und ohne
Titel+Artist ab:

```python
if not direct_track_id and not direct_album_id and (not title or not artist_name):
    return None
```

Legacy-Registrierung und History (`core/imports/side_effects.py::
record_download_provenance`) sind trotzdem erfolgreich, weil sie denselben
`title`/`artist_name`-Ausfall nicht als Abbruchgrund behandeln — nur
`lib2_track_files` fehlt. Der native Orphan Detector baut bekannte Pfade aus
aktiven V2-Subjects und V2-Tag-/Filename-Fallbacks. Ohne Entity/File-Row kann
er den Song nicht finden.

**Wichtige Korrektur gegenüber der ursprünglichen Diagnose:** Das ist **kein
quarantäne-spezifischer Bug**. Simple Downloads (`search_result.
is_simple_download=True`) haben strukturell nie ein `track_info` mit
Titel/Artist UND nie eine `lib2_entity`/`source_info`-ID — der Early-Return
greift bei jedem erfolgreichen Simple Download, unabhängig davon, ob er
jemals in Quarantäne war. Ein Quarantäne-Approve reproduziert die Lücke nur,
weil er denselben (bereits vorher lückenhaften) Context originalgetreu
zurückspielt — die Sidecar-Serialisierung selbst bleibt verlustfrei (wie
bereits empirisch ausgeschlossen, siehe oben).

### Korrekturentscheidung

Ein roter/beweisender Test allein autorisiert noch keine Korrektur — die
Korrektur selbst brauchte eine Produktentscheidung, weil zwei grundsätzlich
verschiedene Richtungen technisch beide tragfähig waren:

1. **Materialisieren:** Simple Downloads ohne Titel/Artist-Match bekommen eine
   Fallback-Entity in lib2 (Muster ähnlich der V2-nativen/Unmapped-Artist-
   Behandlung aus [F-08](library-v2-features.md#feat-unmapped)) statt
   stillschweigend übersprungen zu werden.
2. **Orphan Detector härten:** Der Scan erkennt Files, die zwar keine
   `lib2_track_files`-Row haben, aber eine reale
   `record_download_provenance`/`library_history`-Zeile — und flaggt sie
   nicht als Orphan, statt lib2 künstlich vollständig zu machen.

Beide sind technisch tragfähig, unterscheiden sich aber im Produktverhalten
(Simple Downloads danach in Library v2 sichtbar vs. weiterhin unsichtbar,
aber sicher).

**Entschieden (26. Juli 2026): Option 1, Materialisieren.** Simple Downloads
ohne Titel/Artist-Match bekommen eine Fallback-Entity in lib2, statt vom
Orphan Detector unsichtbar zu bleiben.

**Umgesetzt am 26. Juli 2026.** Die bei der Entscheidung noch offene
Namensableitung ist: eingebettete Tags → Dateiname als `Artist - Titel`
(Track-Numerierung vorher abgeschält) → Dateistamm unter `Unknown Artist`.
Anschließend läuft der reguläre `_find_or_create_*`-Pfad, ein passender
bestehender Eintrag wird also wiederverwendet. Neu über den Fallback angelegte
Album-/Track-Zeilen starten unmonitored, damit eine geratene Identität keinen
Acquisition-Intent erzeugt. Details in
[status.md §22](library-v2-status.md#22-orphan-approve-simple-downloads-werden-materialisiert-26-juli).

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

### <a name="rev25-02"></a> Finding 2 — Kaltes Cover kann dauerhaft Placeholder bleiben — Behoben, 26. Juli 2026

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

**Umgesetzt am 26. Juli 2026** — gewählt wurde die erste Variante: ein
gebündelter Status-Endpoint (`GET /api/library/v2/artwork/status`) über
`artwork_build_states` und ein Client-Abo, das pro Tick *einen* Request für
alle offenen Cover einer Seite stellt. Das Warten endet, wenn der Server
`ready` (rendern) oder `unavailable` (Platzhalter endgültig) meldet — nicht
mehr nach einer Konstanten. `X-Artwork-Pending` bleibt als Signal am
404-Vertrag, hat aber weiterhin bewusst keinen Konsumenten: der Client fragt
den Status ohnehin gezielt ab. Details in
[status.md §24](library-v2-status.md#24-artwork-kaltstart-nachlieferung-an-den-gerenderten-client-26-juli).

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

---

## 13. Nutzer-Bugreport vom 26. Juli 2026

Zwei vom Nutzer beobachtete Symptome, per Codepfad und (für Finding 1) per
Reproduktion der zugrunde liegenden Funktionslogik nachvollzogen.

### <a name="pathdrift25-01"></a> Finding 1 — „Metadaten-Scan pending" für vorhandene Songs durch denselben Pfad-Desync wie LV2-017

**Ort:** `core/library2/scan.py::rescan_files` (Zeilen 209-220),
`core/library2/status.py::metadata_scan_status`, `core/library2/paths.py::resolve_lib2_path`.

**Realbeispiel des Nutzers:** `lib2_track_files.path` nennt
`.../1nonly/Bunny Girl/01-01 - Bunny Girl.flac`; die reale Datei liegt unter
`.../1nonly/Bunny Girl/01 - Bunny Girl.flac` — derselbe
Disc-Präfix-Template-Drift wie bereits in [LV2-017](#lv2-017) dokumentiert
(dort mit ReplayGain/Lyrics als sichtbarem „File no longer exists"-Fehler).

`resolve_lib2_path` gleicht ausschließlich Root-/Mount-Abweichungen aus
(`core/library.path_resolver`); einen abweichenden Dateinamen korrigiert es
nicht und liefert für so einen Track `None`. `rescan_files` behandelt das als
reinen Miss: Es zählt `stats["missing"] += 1` und ruft ausschließlich
`_persist_missing_observation` auf. `persist_tag_cache`
(`tags_json`/`missing_tags_json`) wird für diese Datei **nie** aufgerufen —
unabhängig davon, wie oft „Refresh & Scan" läuft. `metadata_scan_status()`
bleibt deshalb dauerhaft `pending`, nicht `unreadable` (dieser Codepfad
erreicht den Lesefehler-Zweig gar nicht) — exakt der beobachtete Zustand für
tatsächlich vorhandene, bereits heruntergeladene Songs.

**Bisher nicht dokumentierte Zusatzfolge:** `missing_path_root_is_healthy`
prüft nur, ob das übergeordnete Verzeichnis existiert; bei reinem
Dateinamens-Drift ist das der Fall, der Miss gilt also als „gesund" und zählt.
Nach zwei so gezählten Scans (`MISSING_CONFIRMATION_SCANS = 2`) kippt
`file_state` auf `missing_confirmed`, obwohl die Datei physisch vorhanden
ist — mit Risiko, dass nachgelagerte Wanted-/Redownload-Logik einen bereits
vorhandenen Song erneut sucht.

**Fehlendes Werkzeug:** Der ursprüngliche [LV2-017](#lv2-017)-Korrekturvertrag
sah für betroffene Bestandsinstallationen einen „read-only Backfill-Dry-Run"
vor. Im aktuellen Code existiert kein solches Tool — es gibt keine Logik, die
für einen nicht auflösbaren Pfad im selben Verzeichnis nach einer plausibel
zugehörigen, real vorhandenen Datei sucht. Nur die Forward-Korrektur (H-13:
Reorganize schreibt beide Indizes atomar) wurde umgesetzt; bereits vor diesem
Fix desynchronisierte Bestandsdaten bleiben ohne manuellen Eingriff dauerhaft
betroffen.

**Korrekturvertrag:** Ein bounded, read-only Abgleichslauf für Zeilen mit
nicht auflösbarem Pfad: im aus dem gespeicherten Pfad ableitbaren Verzeichnis
nach einer eindeutigen Kandidatendatei suchen (z. B. Tracknummer-/Titel-
Ähnlichkeit oder Content-Hash bei gleicher Dateigröße) und einen Fix-Vorschlag
statt einer stillen Automutation liefern; uneindeutige Mehrfachtreffer werden
nicht automatisch gewählt (wie im LV2-017-Korrekturvertrag festgehalten).
Zusätzlich sollte `rescan_files` einen „Pfad nicht auflösbar, aber
Verzeichnis gesund"-Zustand von einem echten Miss unterscheiden, damit ein
solcher Track nicht in Richtung `missing_confirmed` läuft, während der
eigentliche Fund noch aussteht.

**Umgesetzt am 26. Juli 2026** — `core/library2/path_drift.py` (Matching,
bounded read-only Scan, re-verifizierendes Apply), Repair-Job
`path_drift_reconcile` samt Worker-Fix-Handler, und der Deckel auf
`missing_suspected` in `rescan_files`. Umfang und bewusste Grenzen stehen in
[status.md §20](library-v2-status.md#20-pfad-desync-reconcile-werkzeug-und-missing-lifecycle-schutz-26-juli).
Der am 14. August ergänzte Upgrade-Pfad führt den sicheren Teil dieses
Abgleichs noch vor Öffnung der Migration-Gate automatisch aus: eindeutige
Treffer werden repointed, mehrdeutige nur als `missing_suspected` geschützt.

### <a name="manualmatch25-01"></a> Finding 2 — Manual Match (Artist) läuft in den Client-Timeout durch synchrone Artwork-Nachladung

**Ort:** `api/library_v2.py:1634` (`lib2_native_manual_match`) →
`core/library2/native_enrich.py:222` (`enrich_native_artist_artwork`) →
`core/library2/provider_adapters.py:791` (`fetch_artwork_url`) →
`core/metadata/artist_image.py:48` (`_get_artist_image_from_source`).

**Nutzerbeobachtung:** `PUT /api/library/v2/artists/<id>/manual-match` endet
zuverlässig mit „Request timed out", unabhängig von Artist und gewählter
Metadaten-Quelle.

Nach jedem erfolgreichen Artist-Match (`entity_type in ("artist","artists")`)
ruft der Endpoint unbedingt `enrich_native_artist_artwork` auf, **bevor**
`conn.commit()` erreicht wird. Diese Funktion fragt über `fetch_artwork_url`
alle konfigurierten und am Datensatz gespeicherten Provider-Quellen
**sequenziell** mit je einem blockierenden HTTP-Call ab — derselbe
Architekturbefund wie [perf25-02](#perf25-02), hier aber im
request-kritischen Pfad statt im Artwork-Kaltstart. Die im Request gewählte
`service`/`service_id` bestimmt nur, welche ID gespeichert wird — die
anschließende Artwork-Suche läuft über ALLE am Artist gespeicherten
Provider-IDs, nicht nur über die gerade gewählte. Das erklärt, warum der
Timeout unabhängig von Artist und Quelle reproduzierbar ist.

Das Frontend (`webui/src/app/api-client.ts`: `apiClient = ky.create({baseUrl,
retry: 0})`) setzt kein eigenes `timeout`, wodurch `ky`s Default von 10 s
gilt und exakt mit der Fehlermeldung „Request timed out" abbricht. Da
`conn.commit()` erst nach dem Artwork-Versuch läuft, kann der Match
serverseitig wenig später trotzdem erfolgreich durchlaufen — der Client hat
davon durch den Timeout aber nichts mehr erfahren; ob eine bestimmte
Zuordnung wirklich gespeichert wurde, lässt sich nur durch Neuladen der
Artist-Seite prüfen.

**Korrekturvertrag:** Der Match selbst darf nicht auf die Artwork-Nachladung
warten — `conn.commit()` für den Match gehört vor den Artwork-Versuch, oder
die Artwork-Suche wird analog zum Artwork-Kaltstart-Pfad (siehe
[features F-01](library-v2-features.md#feat-artwork)) in den Hintergrund
verlagert. Kurzfristig zumindest den bestehenden Parallel-Fetch aus dem
Artwork-Picker (`core/metadata/art_lookup.py:601-613`, ThreadPoolExecutor)
statt des sequenziellen Fallbacks verwenden, um unter das Client-Timeout zu
kommen.

**Umgesetzt am 26. Juli 2026** — beide Hauptpunkte zusammen: erst
`conn.commit()`, dann `schedule_native_artist_artwork` off-thread mit eigener
Verbindung. Der Parallel-Fetch wurde bewusst **nicht** übernommen; er war
ausdrücklich nur die kurzfristige Notlösung, um unter das Timeout zu kommen,
und nach der Entkopplung sieht die Latenz niemand mehr (dieselbe Abwägung wie
in [perf25-02](#perf25-02)). Siehe
[status.md §21](library-v2-status.md#21-manual-match-artwork-verlässt-den-request-pfad-26-juli).

---

## 14. Native Repair-Subjects sind gegen die Legacy-SELECT-Breite verschoben (26. Juli 2026)

### <a name="nativepad25-01"></a> Finding 1 — `missing_cover_art` und `metadata_gap_filler` lassen den `ar.id`-Slot aus

**Ort:** `core/repair_jobs/missing_cover_art.py:153-157` und
`core/repair_jobs/metadata_gap_filler.py:134-139`.

Beide Scanner lesen zuerst die Legacy-Tabelle und hängen danach die
Library-v2-nativen Subjects (Entities ohne Legacy-Backref) als zusätzliche
Zeilen an dieselbe Liste. Weil die Legacy-Zeilen `sqlite3.Row`-Tupel sind,
werden die nativen Zeilen von Hand positionsgleich aufgebaut und mit
`*pad` auf die Breite des Legacy-`SELECT` gebracht — der Kommentar sagt genau
das („Rows are padded to the legacy SELECT's width").

Sie waren aber um **einen** Slot zu kurz: die Legacy-Spaltenliste endet mit
`ar.id` (dem Legacy-Artist), die native Zeile hat diesen Slot nie gesetzt.
Alles dahinter — die optionalen, per `PRAGMA table_info` zugeschalteten
Provider-ID-Spalten — verschob sich damit um eine Position nach links, und die
jeweils letzte fiel hinten heraus:

| Job | Basisbreite | Optionale Spalten auf migrierter DB | Zugriff, der ins Leere greift |
|---|---:|---|---|
| `missing_cover_art` | 8 | `itunes_album_id`, `deezer_id`, `discogs_id`, `soul_id` | `row[11]` (`hydrabase_album_id`) |
| `metadata_gap_filler` | 9 | `spotify_track_id`, `itunes_track_id` | `row[10]` (`itunes_track_id`) |

Wirkung auf einer real migrierten Installation: `IndexError: tuple index out of
range`, sobald der Scan die **erste** native Zeile erreicht. Die Schleife läuft
nicht in einem eigenen `try`, der Fehler verlässt also `scan()` — ein einzelnes
V2-natives Album bzw. ein einzelner V2-nativer Track legt den kompletten Job
lahm, inklusive der Legacy-Zeilen, die bereits gefunden waren. Zusätzlich las
`details['artist_id']` für native Zeilen den ersten Pad-Wert statt eines
bewussten `None`.

Auf einer sehr alten, nicht durchmigrierten DB (keine optionale Spalte
vorhanden) trat derselbe Fehler schon einen Slot früher auf, direkt bei
`artist_id = row[7]` bzw. `row[8]`.

**Warum das so lange unentdeckt blieb:** Die beiden Regressionstests
(`tests/library2/test_maintenance_sync.py`) liefen gegen ein synthetisches
Legacy-Schema ohne `albums.spotify_album_id` und ohne `tracks.isrc`. Dort
scheiterte bereits die Legacy-Query, der Scanner fing das ab (`result.errors
+= 1`) — und der anschließende `IndexError` wurde als Schema-Drift der Fixture
gelesen statt als Produktfehler. Der Nachtrag in
[status.md §14](library-v2-status.md#14-rebase-auf-den-foundation-merge-26-juli)
(„vermutlich Test-Fixture ohne vollständige Migrationskette") war damit nur zur
Hälfte richtig: die Fixture ist tatsächlich unvollständig, sie hat den echten
Bug aber nur verdeckt, nicht verursacht.

**Korrekturvertrag:** Die native Zeile setzt den `ar.id`-Slot explizit auf
`None` (ein natives Subject hat keine Legacy-Artist-Zeile; die native
Artist-ID steht ohnehin im `library_v2`-Block des Findings). Getestet wird an
beiden Enden des Pad-Bereichs: migriertes Schema (optionale Spalten vorhanden)
und unmigriertes Schema (keine vorhanden, Legacy-Query scheitert, native
Abdeckung muss trotzdem laufen).

**Nebenbefund, bewusst nicht mitgeändert:** `metadata_gap_filler`s
`column_map` sucht `t.deezer_track_id`; die Migration legt auf `tracks` aber
`deezer_id` an (`database/music_database.py` ~Zeile 3557 — dieselbe
Namensasymmetrie, die `core/personalized_playlists.py:178` bereits
dokumentiert). Die Spalte existiert also nie, die Deezer-ID wird für
ISRC-Enrichment nie herangezogen. Das ist ein eigener Funktionsbefund, kein
Alignment-Fehler, und würde ändern, welche Provider tatsächlich abgefragt
werden.

**Umgesetzt am 26. Juli 2026** — siehe
[status.md §26](library-v2-status.md#26-native-repair-subject-ausrichtung-und-abbau-der-test-schuld-26-juli).

---

## 15. Erster Lauf gegen die reale Produktiv-DB (26. Juli 2026)

Der in §20/§22/§26 und in [status.md §9](library-v2-status.md#9-aktuelle-release-einschätzung)
wiederholt als ausstehend geführte Lauf gegen die echte Bibliothek des Nutzers
ist erstmals ausgeführt worden — auf einem `sqlite3.backup()`-Snapshot der
Live-Datei, nie auf der Live-Datei selbst (Guide §6.1). Die Datenbank enthält
5 Artists, 273 lib2-Alben, 2.048 lib2-Tracks und 270 lib2-Files.

### <a name="realdb25-01"></a> Finding 1 — Der Album-Twin-Pass läuft nur für frisch gemergte Artists

**Ort:** `core/library2/dedup_repair.py::repair_duplicate_artists`, Schleife
`for artist_id in touched_artists`.

`touched_artists` enthält ausschließlich Artists, die im selben Lauf durch
einen Namens- oder Provider-ID-Merge **entstanden** sind. Ein Artist, dessen
Zeile sauber und einmalig ist, wird nie besucht — sein
`_fold_albums_within_artist` läuft nicht, seine Album-Twins werden also weder
gefoldet noch als `lib2_release_group_review`-Finding erfasst.

Die Annahme dahinter („Album-Duplikate entstehen als Folge eines
Artist-Duplikats") stimmt für den §62.5-Entstehungsweg, aber nicht allgemein.
Zwei Katalogzeilen für dieselbe Release entstehen auch ohne jedes
Artist-Duplikat: eine Discography-Expansion liefert eine zweite Provider-
Edition, oder ein Legacy-Reimport landet neben einer bereits nativ angelegten
Zeile.

Genau das ist der reale Zustand. Der Lauf meldete
`artists_merged=0` → `touched_artists` leer → `albums_folded=0`,
`album_review=0`, während gleichzeitig drei echte Album-Twins in der DB lagen:

| Artist | Titel | Zeilen | `stable_id` |
|---|---|---|---|
| Justin Bieber | SWAG II | 1174 / 1344 | identisch |
| Hiroyuki Sawano | TVアニメ「進撃の巨人」Season 2 オリジナルサウンドトラック | 1163 / 1229 | identisch |
| Hiroyuki Sawano | TV Anime "Attack on Titan" Original Soundtrack | 1169 / 1230 | identisch |

Der identische `stable_id` ist der harte Beleg: Er wird deterministisch aus
(Artist, Titel, Album-Typ) gebildet, zwei Zeilen mit demselben Wert sind per
Definition dieselbe Release. Für die beiden Sawano-Paare existierte bereits ein
Review-Finding — die haben es über den MB-Release-Group-Reconcile (§62.6 Stufe
3) bekommen, der von der Artist-Seite aus läuft. Das Bieber-Paar hatte keins:
Für diesen Artist war weder ein Merge noch ein MB-Reconcile gelaufen, und damit
war das Duplikat für den Nutzer **unsichtbar**.

Anzumerken: Selbst bei Aufruf hätten alle drei Paare **nicht** automatisch
gefoldet werden dürfen — beide Seiten tragen echte Files, `_is_pristine` ist
für jede Seite `False`. Der Fehler ist also nicht ein verweigerter Merge,
sondern ein Pass, der gar nicht erst stattfindet und deshalb auch das
Review-Finding schuldig bleibt.

**Korrekturvertrag:** Der Album-Pass läuft für jeden Artist, der einen
Titel-Twin hält, nicht nur für Merge-Survivor. Die Fold-Regeln selbst bleiben
unverändert (pristine + kompatible Counts, sonst Review) — es ändert sich nur,
für wen sie überhaupt ausgewertet werden. Die Kandidatenermittlung darf keine
Query-Flut werden (BR-08): ein einziger Scan über
`lib2_album_artists ⋈ lib2_albums`, danach die teure Detailquery nur für die
Artists mit echtem Twin.

### <a name="realdb25-02"></a> Finding 2 — 112 physische Dateien hängen an mehreren Katalog-Tracks — offen

`core/library2/integrity_reconciler.py::build_integrity_report` (LV2-013,
read-only) meldet auf derselben DB 113 Findings:

| Code | Anzahl |
|---|---:|
| `lib2_path_multiple_tracks` | 112 |
| `lib2_active_file_missing` | 1 |

Die 112 zerfallen in zwei sehr verschiedene Populationen:

- **103 Gruppen über Albumgrenzen hinweg.** Ganz überwiegend die drei
  Album-Twins aus Finding 1 (43 + 32 + 16 Files); der Rest sind Album↔Single-
  Paare (SWAG/DAISIES, SWAG/YUKON …), bei denen dieselbe Datei den Albumtrack
  und die gleichnamige Single bedient. Letzteres ist nach DD-G1 **kein**
  Duplikat: Album und Single sind getrennte Katalogentitäten.
- **21 Gruppen innerhalb desselben Albums.** Hier hat ein Album zwei bis drei
  Track-Zeilen für denselben Song, die auf dieselbe Datei zeigen — Album 1064
  („SWAG") führt 41 Track-Zeilen bei `track_count=21`. Über den gesamten
  Katalog: 80 Album/Titel-Paare mit mehr als einer Zeile, 122 doppelte
  `lib2_tracks.stable_id`. Ein Beispiel:

  | Track | Album | Titel | `track_number` | `legacy_track_id` |
  |---|---|---|---:|---|
  | 633 | 1064 SWAG | DAISIES | 1 | 820355018 |
  | 2677 | 1064 SWAG | DAISIES | 2 | 452943367 |

  Beide zeigen auf `…/02 - DAISIES.flac`, beide sind `monitored=1`.

**Warum hier kein Fix mitgeliefert wird:** Das Falten von Track-Zeilen ist
nicht der Album-Fall in klein. Ein Track trägt Monitor-Rules, die
Wanted-Projektion, History, Quality-Zuweisung und potenziell mehrere Files;
welche Zeile überlebt und was mit dem Intent der anderen geschieht, ist eine
Produktentscheidung wie in [§7](#orphan-bug)/§16 — und Guide-Arbeitsregel 3
plus Regel 7 verbieten dafür einen Platzhalter-MVP. Sichtbar ist der Zustand
bereits: Der Integritätsreport ist genau dafür da und meldet jede dieser
Gruppen einzeln.

**Nicht verwechseln mit `lib2_active_file_missing` (1×):** Diese eine Zeile
zeigt auf eine Datei unter `Transfer/The Jacksons - …`, die auf einem gesunden
Root fehlt — der normale Missing-Lifecycle aus LV2-010, kein Duplikatthema.

### <a name="realdb25-03"></a> Finding 3 — Bestätigungen, die der Lauf mitgeliefert hat

Kein Fehlerbild, sondern die eigentliche Ausbeute des Laufs:

- **§26 auf echten Daten bestätigt.** `missing_cover_art` scannte 33 Alben
  (9 Legacy + 24 V2-nativ), `metadata_gap_filler` 424 Tracks — beide mit
  `errors=0`. Die reale `albums`-Tabelle trägt alle vier optionalen
  Provider-ID-Spalten, die Pad-Breite ist also 4: Vor dem Fix wäre der Scan
  beim **ersten** nativen Album mit `IndexError` abgebrochen.
- **§23 auf echten Daten bestätigt.** Die additive Migration hat
  `acquisition_request_id`, `acquisition_candidate_id`,
  `acquisition_download_id` und `idx_lh_acquisition_request` auf einer
  bestehenden, gewachsenen `library_history` angelegt.
- **§20 ohne Befund.** `path_drift_reconcile` fand 2 unauflösbare Zeilen und
  keinen Drift-Kandidaten — diese Bibliothek hat den LV2-017-Desync nicht.
- **§22 ohne Befund.** Der Orphan-Detector scannte 144 Dateien und meldete
  keinen Orphan.

---

## 16. Reconcile Unmapped Artists: namensbasiertes Matching ignoriert vorhandene starke IDs (26. Juli 2026)

Diskussion beim Review des "Reconcile Unmapped Artists"-Jobs
([features F-08](library-v2-features.md#feat-unmapped),
`core/library2/native_enrich.py::reconcile_unmapped_native_artists`) im
Rahmen der geplanten Automatisierung dieses Jobs nach abgeschlossenen
Imports (siehe [status.md §28](library-v2-status.md#28-reconcile-unmapped-artists-root-cause-dokumentiert-korrektur-ausstehend-26-juli-2026)).

### <a name="unmappedreconcile26-01"></a> Finding 1 — Kein Cross-Check gegen bereits gematchte Alben/Tracks des Artists

**Ort:** `core/library2/native_enrich.py::resolve_and_enrich_native_artist` →
`core/metadata/album_tracks.py::resolve_artist_identity`.

`resolve_and_enrich_native_artist` liest für den Artist ausschließlich die
`name`-Spalte aus `lib2_artists` und übergibt nur diesen String an
`resolve_artist_identity`. Es gibt keinen Blick auf `lib2_albums`/
`lib2_tracks` desselben Artists — weder auf `lib2_albums.spotify_id`/
`musicbrainz_id`/`external_ids` noch auf `lib2_tracks.spotify_id`/`isrc`/
`musicbrainz_id`/`external_ids` —, obwohl diese Spalten existieren und bei
Alben/Tracks, die über Wishlist-Add, Download-Match oder
Discography-Expansion entstanden sind, oft längst gefüllt sind (Schema-
Kommentar: `tracks.spotify_id -- for wishlist mirroring`).

`resolve_artist_identity` läuft stattdessen für jede konfigurierte Quelle
eine Namenssuche über den Artist-Search-Endpunkt und nimmt den ersten
Treffer, den `_pick_best_artist_match` als hinreichend sicher einstuft
(exakter Normalisierungstreffer, bei mehreren exakten Treffern
Katalog-Gewicht-Tiebreak, sonst Fuzzy ≥ 0,85). Genau dieser
Vorsichtsapparat existiert nur, **weil** eine Namenssuche mehrdeutig sein
kann — Provider liefern Fragment-Duplikate unter identischem Namen (5×
"Hiroyuki Sawano" bei Deezer, real nur eine mit 104 statt 4 Alben) und
historisch griff eine blinde erste Trefferannahme daneben (Bug #988:
Deezer-Namenssuche nach "The Outfield" lieferte "The Beatles"). Eine
bereits vorhandene starke ID auf einem Album/Track desselben Artists macht
diese ganze Unsicherheit überflüssig: Ein direkter ID-Abruf des
Albums/Tracks bei der Quelle liefert die Artist-Identität aus der
Provider-Antwort selbst, ohne Namenssuche, ohne Fuzzy-Schwelle. Das ist
exakt der in Guide §2.5 verlangte Vorrang ("Starke IDs schlagen
Namensheuristiken"), der hier für native Artists nicht eingehalten wird.

**Korrekturvertrag:** Vor dem Namens-Resolve prüfen, ob der Artist
mindestens ein Album/Track mit einer starken Provider-ID trägt. Für jede
so gefundene Quelle den Artist über den Album-/Track-Response dieser
Quelle auflösen (ID-Lookup, keine Namenssuche) — und dabei **nicht** wie
der bisherige Namens-Resolve bei der ersten erfolgreichen Quelle stoppen:
Jede Quelle, für die eine 100%-sichere Anker-ID (Album oder Track dieses
Artists) existiert, wird abgefragt und ihre Artist-ID geschrieben, nicht
nur eine einzelne. Anders als beim Namens-Resolve ist das hier ungefährlich,
weil jede einzelne Zuordnung durch eine bereits bestätigte Track-/Album-ID
belegt ist, nicht durch eine Heuristik. Nur wenn **kein** Album/Track des
Artists irgendeine starke ID trägt, fällt das System auf den bisherigen
Namens-Resolve zurück — und behält dort bewusst das Stop-bei-erstem-Treffer-
Verhalten, weil mehrere unabhängige Namens-Fuzzy-Treffer über verschiedene
Quellen das Fehlmatch-Risiko eher vervielfachen als senken würden.

**Status: Pending** — Root Cause bestätigt, keine Korrektur in dieser
Session.

### <a name="unmappedreconcile26-02"></a> Finding 2 — Kein Cooldown für dauerhaft ungematchte Artists

**Ort:** `core/library2/native_enrich.py::_pending_unmapped_artists`,
`reconcile_unmapped_native_artists`.

Die Kandidatenauswahl selektiert ausschließlich danach, ob am Artist noch
keine Provider-ID gespeichert ist. Es gibt keine Markierung, die einen nie
versuchten Artist von einem unterscheidet, der beim letzten Lauf an jeder
konfigurierten Quelle **und** an jeder Smart-Split-Komponente gescheitert
ist (z. B. ein echter Collab-Name, dessen Bestandteile selbst keine
Provider-Treffer sind). Jeder Reconcile-Lauf attackiert deshalb erneut den
gesamten Backlog, einschließlich der Zeilen, die strukturell dauerhaft
unlösbar sind.

Solange der Job nur per Button manuell ausgelöst wird, ist das ein
akzeptabler Klick-Preis. Sobald er automatisch und wiederholt nach
abgeschlossenen Imports läuft (siehe status.md §28), wird derselbe
dauerhaft ungematchte Name bei jedem Trigger erneut gegen alle
konfigurierten externen Provider abgefragt — Frequenz und Rate-Limit-Kosten
hängen dann unkontrolliert davon ab, wie oft Imports abschließen.

**Korrekturvertrag:** Eine `last_attempted_at`/`attempt_count`-Markierung
pro Artist, die es einem automatisierten Lauf erlaubt, kürzlich
gescheiterte Zeilen für ein Backoff-Fenster zu überspringen, statt sie bei
jedem Trigger erneut anzufragen.

**Status: Pending** — Root Cause bestätigt, keine Korrektur in dieser
Session; wird relevant, sobald der automatische Post-Import-Trigger
umgesetzt wird.

---

## 17. Werkzeuge und Library V2 konvergieren nicht: Nutzer-Bugreport vom 26. Juli 2026 (Abend)

Nutzerbeobachtung: Der Cover Art Filler erkennt eine Lücke korrekt, Library V2
zeigt für denselben Song „2 tag gaps" (`genre`, `cover`) — nach „Fix Finding",
„Refresh & Scan", Browser-Reload und Browser-Neustart stehen beide Lücken
weiterhin da. Ein Klick auf „2 tag gaps" meldet „Tags written", ändert aber
nichts; „Preview Retag" behauptet „Tags match". Zusätzlich fehlt eine sichtbare
Spalte dafür, **wie** eine Datei verifiziert wurde, obwohl jede Datei aus dem
Download-Pfad einen Verification-Marker bekommt.

Alle Befunde dieses Abschnitts sind gegen einen Snapshot der realen Produktiv-DB
(`database/music_library.db`, 273 `lib2_albums`, 2.048 `lib2_tracks`,
270 `lib2_track_files`) reproduziert, nicht aus synthetischen Fixtures
abgeleitet. Der Reproduktionsaufbau folgt
[§15](#15-erster-lauf-gegen-die-reale-produktiv-db-26-juli-2026): `sqlite3.backup()`
in den Scratchpad, `MusicDatabase(copy)`, echte Audiodateien.

### <a name="tool26-00"></a> Was nachweislich funktioniert

Damit der Rest nicht als Generalverdacht gelesen wird — folgender Pfad wurde
Ende-zu-Ende gegen echte Dateien durchgespielt und ist **korrekt**:

1. Einer echten FLAC wurden Cover und Genre-Tag entfernt.
2. `rescan_files` (= „Refresh & Scan") liest die Datei neu und schreibt
   `missing_tags_json = ["genre","cover"]` — die Erkennung stimmt.
3. `NativeMissingCoverArtJob`-Finding → `_fix_missing_cover_art` bettet das
   Cover ein (`embedded 1/1`, `cover_written True`).
4. `sync_repair_change` löst über `entity_id='lib2:<album>'` Album → Tracks →
   Files auf und ruft `rescan_files(file_ids=…)`.
5. Ergebnis: `["genre","cover"]` → `["genre"]`.

Der *native* Cover-Art-Pfad konvergiert also. Die Symptome des Nutzers entstehen
an den elf Stellen darunter, an denen genau dieser Kreis nicht geschlossen ist.

### <a name="tool26-01"></a> T-01 — Findings mit Legacy-Entity-ID können Library V2 nie erreichen

**Ort:** `core/library2/maintenance_sync.py::_resolve_links`.

`_resolve_links` kennt genau drei Wege zu einer nativen Identität: eine
ausdrücklich native `lib2:<id>`-Entity-ID, ein `details['library_v2']`-Block
und einen Dateipfad. `_native_entity_id` verwirft eine nackte Zahl bewusst
(„Bare numeric IDs remain ambiguous"). Eine Auflösung über die vorhandenen
Rückverweise `lib2_albums.legacy_album_id`, `lib2_tracks.legacy_track_id` und
`lib2_track_files.legacy_track_id` findet **nicht** statt.

Belegt in der Produktiv-DB — alle drei dort offenen Findings sind genau dieser
Fall, und alle drei ließen sich über den Rückverweis auflösen:

| Finding | Job | `entity_id` | `details.library_v2` | Rückverweis vorhanden |
|---|---|---|---|---|
| 15 | `album_tag_consistency` | `'630009860'` | `None` | `lib2_albums.id=1066` (Thriller 40) |
| 18 | `album_tag_consistency` | `'709335827'` | `None` | `lib2_albums.id=1064` (SWAG) |
| 19 | `library_reorganize` | `'234986381'` | `None` | `lib2_tracks.id=2659` (OH MAN) |

Solche Zeilen entstehen aus zwei Quellen: aus Läufen **vor** dem P3-Cutover
(die nativen Überschreibungen in `core/repair_jobs/native_p3.py` schreiben
inzwischen `lib2:`-IDs) und aus den Jobs, die bis heute rein legacy scannen
(→ [T-11](#tool26-11)).

Konsequenz beim „Fix Finding": Die Datei auf der Platte wird geändert, aber
`sync_repair_change` bricht mit `reason='subject_unlinked'` ab — **kein**
`rescan_files`, **keine** Artwork-Invalidierung, **kein** Maintenance-Event.
Der Tag-/Gap-Cache in `lib2_track_files` bleibt auf dem Stand von vor der
Reparatur stehen. Genau das ist das vom Nutzer beschriebene Bild „Finding
gefixt, Library V2 zeigt die Lücke weiter".

**Korrekturvertrag:** `_resolve_links` löst eine nicht-native `entity_id`
zusätzlich über `legacy_album_id`/`legacy_track_id` auf, bevor es aufgibt.
Der Rückverweis ist eine harte, beim Import geschriebene ID — kein Namensraten,
also mit Guide §2.5 vereinbar. Legacy-IDs bleiben dabei opaques `TEXT`
(Guide §5): der Vergleich läuft als String, nie über `int()`.

### <a name="tool26-02"></a> T-02 — `subject_unlinked` gilt als Erfolg

**Ort:** `core/repair_worker.py::fix_finding`.

`fix_finding` wertet ausschließlich `reason == 'error'` als
Konvergenzfehler und lässt das Finding dann als Retry-Anker offen. Jeder
andere Rückgabewert — einschließlich `subject_unlinked` und `schema_missing` —
führt dazu, dass das Finding als `resolved` markiert wird. Nicht-Konvergenz
ist damit still: Der Nutzer sieht „gefixt", Library V2 weiß nichts davon, und
das Finding, das den Retry hätte tragen können, ist weg.

**Korrekturvertrag:** `subject_unlinked` bleibt kein Fehlerabbruch (es gibt
legitime Fälle — z. B. `empty_folder_cleaner` ohne jede Katalogzeile), muss
aber im Fix-Ergebnis sichtbar sein, damit UI und Logs „auf Platte repariert,
Katalog nicht angefasst" von „vollständig konvergiert" unterscheiden können.

### <a name="tool26-03"></a> T-03 — Klick auf „N tag gaps" schreibt strukturell nichts

**Ort:** `webui/…/library-v2-page.tsx::TrackMetadataGapsCell` →
`POST /api/library/v2/tags/write` (`api/library_v2.py::lib2_write_tags`) →
`core/library2/retag.py::write_tags`.

Gemessen an der oben präparierten echten Datei:

```
write_tags(db, [track], embed_cover=True)  -> {'written': 0, 'skipped': 1, 'failed': 0}
gaps davor:  ["genre", "cover"]
gaps danach: ["genre", "cover"]
```

Ursachenkette:

1. `write_tags` bricht früh ab, wenn `build_tag_diff` keine **Text**-Änderung
   findet (`text_changed` false → `skipped`). Cover ist keine Textänderung.
2. Der einzige Ausweg wäre `force_cover=True` — `lib2_write_tags` übergibt
   diesen Parameter nicht. Nur der Album-Art-Apply
   (`api/library_v2.py`, `lib2_album_art_apply`) setzt ihn.
3. Die Gap-Zelle meldet trotzdem unbedingt „Tags written to file." — der
   `onSuccess`-Handler wertet die Job-Statistik (`written`/`skipped`/`failed`)
   nicht aus.

Damit ist die Affordance „klick die Lückenzahl an, um sie zu schließen"
für genau die zwei häufigsten Lückenarten (`cover`, `genre`) wirkungslos —
und meldet zusätzlich Erfolg.

### <a name="tool26-04"></a> T-04 — „Preview Retag" meldet „Tags match", obwohl das Cover fehlt

**Ort:** `core/library2/retag.py::_db_data_for_row` →
`core/tag_writer.py::build_tag_diff`.

`build_tag_diff` bildet die Cover-Zeile aus `db_data['thumb_url']`:

```python
'file_value': 'Embedded' if file_tags.get('has_cover_art') else 'None',
'db_value':   'Available' if db_data.get('thumb_url') else 'None',
'changed':    not file_tags.get('has_cover_art') and bool(db_data.get('thumb_url')),
```

`_db_data_for_row` liefert `title/artist_name/track_artist/album_title/year/
release_date/genres/track_number/disc_number/track_count` und optional
Spotify-/MusicBrainz-IDs — **kein `thumb_url`**. Die Cover-Zeile lautet
deshalb in Library V2 immer `None → None, changed=False`. Gemessen an
derselben präparierten Datei:

```
FULL diff: … ('Genre','','',False), ('Cover Art','None','None',False)
has_changes: False        # UI: "Tags match"
```

Der Widerspruch, den der Nutzer sieht („Preview sagt match, die Seite sagt 2
Lücken"), ist damit kein Anzeigefehler in der Gap-Spalte, sondern eine echte
Blindstelle der Preview: Sie **kann** einen Cover-Unterschied nicht anzeigen.

**Korrekturvertrag:** `_db_data_for_row` trägt eine Cover-Verfügbarkeit
(Artwork-Cache-Datei bzw. `lib2_albums.image_url`) als `thumb_url` nach, damit
Preview und Write dieselbe Wahrheit über das Cover benutzen.

### <a name="tool26-05"></a> T-05 — `write_tags` kennt als Cover-Quelle nur die Cache-Datei

**Ort:** `core/library2/retag.py::_album_cover_data`.

```python
path = artwork_file(database, "album", album_id)     # nur ein Pfad
if path.exists(): return path.read_bytes(), "image/jpeg"
```

`artwork_file` ist ein reiner Pfad-Builder; er baut nichts. Existiert die
Cache-Datei (noch) nicht — der Normalfall auf einem kalten Artwork-Cache, nach
`invalidate_artwork` oder direkt nach „Refresh & Scan", das die Cache-Dateien
gerade gelöscht hat — liefert `_album_cover_data` `None`. Gemessen:

```
write_tags(…, force_cover=True) -> {'written': 0, 'skipped': 1}
```

Also: Auch mit korrekt gesetztem `force_cover` wird nichts geschrieben. Und
das, obwohl im selben Album `lib2_albums.image_url` eine gültige
Provider-Cover-URL trägt (im Testfall eine Deezer-1000×1000-URL).

**Korrekturvertrag:** `_album_cover_data` materialisiert den Cache über
`build_artwork` bzw. fällt auf `lib2_albums.image_url` zurück, bevor es
aufgibt — einmal pro Album, wie schon heute über das `covers`-Dict gecacht.
Guide §2.1 bleibt gewahrt: Reihenfolge Override → eingebettetes Cover →
Provider-Artwork, kein Media-Server.

### <a name="tool26-06"></a> T-06 — Die Genre-Lücke ist katalogseitig unfüllbar

**Ort:** Katalogzustand + `core/repair_jobs/*`.

Messung auf der Produktiv-DB:

| Kennzahl | Wert |
|---|---|
| `lib2_albums` gesamt | 273 |
| davon mit leerem `genres` (`'[]'`) | **265** |
| `lib2_track_files` mit Gap `["genre"]` | 61 von 270 |

`write_tags` schreibt Genres aus `lib2_albums.genres` (`_db_data_for_row`).
Ist die Spalte leer, gibt es nichts zu schreiben — der Genre-Gap ist über
„N tag gaps" prinzipiell nicht schließbar, unabhängig von jedem der obigen
Fehler. Kein registriertes Werkzeug füllt diese Spalte:

- **Genre Tag Cleanup** (`genre_cleanup`) *entfernt* nur Genres außerhalb der
  Whitelist — und liest ausschließlich Legacy-Tabellen (→ [T-11](#tool26-11)).
- **Metadata Gap Filler** (`NativeMetadataGapFillerJob`) füllt ausschließlich
  `isrc` und `musicbrainz_recording_id`; `_fix_metadata_gap` kennt als
  schreibbare native Spalten `isrc/musicbrainz_id/spotify_id/bpm/explicit/
  style/mood` — **kein `genres`**.
- Der Enrich-Pfad (`native_enrich`, `enrich`) füllt Genres nur beim Import
  bzw. bei einem ausdrücklichen Provider-Enrich einer Entity.

Damit trifft die Nutzererwartung „ich klicke die Lücke an, es sucht ein Cover
und schreibt das Genre" auf ein Werkzeugset, in dem für Genre gar keine
Beschaffungsstelle existiert.

**Gemessene Zusatzbedingung — der naheliegende Korrekturvertrag greift nicht.**
Der erste Entwurf lautete: „Metadata Gap Filler bekommt `fill_genres`, holt die
Album-Genres über die vorhandenen Provider-Clients, `_fix_metadata_gap` schreibt
`lib2_albums.genres`." Dieser Vertrag wurde gegen die realen Alben geprüft und
**widerlegt**: `get_album_for_source` liefert für die betroffenen Alben von
keiner konfigurierten Quelle Genres.

```
album 1163 'TVアニメ「進撃の巨人」…'  musicbrainz -> None   deezer -> genres: None   itunes -> genres: None
album 1169 'TV Anime "Attack on Titan" …'                 deezer -> genres: None
album 1174 'SWAG II'                                      deezer -> genres: None   (Artist-Genres: ["Pop"])
album 1231 'ZUMA HOUSE'                                   keine Provider-ID        (Artist-Genres: ["Pop"])
```

Ein so gebauter Job fände **null** Alben. Genres liegen in dieser Installation
auf der *Artist*-Ebene (`lib2_artists.genres`: 4 von 5 Artists gefüllt) und in
`metadata_cache_entities` (1.716 Zeilen mit Genres — überwiegend Beatport-
*Tracks*), nicht auf der Album-Ebene, aus der `write_tags` liest. Der legacy
Write-Tags-Pfad (`web_server._build_library_tag_db_data`) hat exakt dieselbe
Beschränkung, ist hier also kein Vorbild.

**Offener Entwurf statt Korrekturvertrag.** Vor einer Umsetzung ist eine
Produktentscheidung nötig, die diese Session nicht allein treffen soll:

1. Darf ein Artist-Genre als Album-Genre gelten, wenn das Album selbst keines
   hat (die pragmatische Variante — sie würde in dieser Bibliothek sofort
   greifen), oder ist das eine unzulässige Vermischung zweier Ebenen?
2. Falls ja: als abgeleiteter Lesewert im Retag-`db_data` (nichts wird
   gespeichert, jederzeit revidierbar) oder als geschriebener
   `lib2_albums.genres`-Wert mit Provenance (ADR-06)?
3. Sollen die vorhandenen Track-Genres aus `metadata_cache_entities`
   (Beatport u. a.) eine dritte Quelle sein?

Bis diese Entscheidung gefallen ist, bleibt die Genre-Lücke ehrlich offen —
sichtbar, aber nicht schließbar. Genau deshalb meldet die Gap-Zelle seit
T-03 „Nothing to write" statt „Tags written", wenn nichts geschrieben wurde.

### <a name="tool26-07"></a> T-07 — Ogg/Opus meldet dauerhaft ein fehlendes Cover

**Ort:** `core/tag_writer.py::read_file_tags`.

```python
if isinstance(audio, FLAC):
    result['has_cover_art'] = bool(audio.pictures)
else:
    # OGG doesn't have a standard picture field we can easily check
    result['has_cover_art'] = False
```

`core/metadata/art_apply.py::_audio_has_art` prüft für dieselben Formate sehr
wohl `metadata_block_picture`. Damit widersprechen sich die drei Instanzen:

| Instanz | Ogg mit `metadata_block_picture` |
|---|---|
| `read_file_tags` → `metadata_gaps` | Cover **fehlt** |
| `file_has_embedded_art` (Cover-Art-Filler-Scan) | Cover **vorhanden** → kein Finding |
| `apply_art_to_album_files` (Apply) | Cover **vorhanden** → `skipped` |

Ergebnis: ein Gap, der angezeigt wird, für den nie ein Finding entsteht und
den kein Apply je schließen kann. Reines Anzeigeartefakt — aber ein dauerhaft
unauflösbares.

**Korrekturvertrag:** `read_file_tags` benutzt für Vorbis-Comment-Formate
dieselbe Erkennung wie `art_apply` (`pictures` bzw.
`metadata_block_picture`), damit Gap-Anzeige, Scan und Apply eine einzige
Wahrheit haben.

### <a name="tool26-08"></a> T-08 — „Refresh & Scan" erneuert keine Katalog-/Provider-Metadaten

**Ort:** `api/library_v2.py::lib2_refresh`, `core/library2/scan.py::rescan_files`.

Was der Job heute tut: Artwork-Cache-Dateien des Scopes löschen →
`rescan_files` über alle Album-Files → pro Datei `read_file_tags` (Tags,
ReplayGain, Lyrics) plus `probe_audio_quality` (Format, Bitrate, Sample Rate,
Bit-Tiefe, Größe, `quality_tier`) plus Missing-Lifecycle.

Datei-Tags und Quality werden also tatsächlich neu gelesen — die
Nutzeranforderung „prüfen, ob die Datei da ist, welche Quality sie erfüllt,
alle Tags auslesen" ist für die *Datei* erfüllt. Nicht erfüllt ist sie für
den *Katalog*: `lib2_albums.genres`, `release_date`, `image_url`, Tracklist
und Provider-IDs werden nicht neu geholt. Ein Album ohne Genres bleibt nach
beliebig vielen „Refresh & Scan"-Läufen ohne Genres — was den in
[T-06](#tool26-06) beschriebenen Genre-Gap dauerhaft macht.

Zusätzlich nicht neu abgeleitet, obwohl die Daten vorliegen:
`verification_status` (→ [T-09](#tool26-09)), `content_hash` und die
Primary-File-Wahl (`is_primary`, ADR-03).

**Korrekturvertrag:** Getrennt halten, aber sichtbar machen. „Refresh & Scan"
bleibt der Datei-Pass; ein Katalog-Refresh (Provider-Metadaten) ist die
Aufgabe von Enrich/Discography-Refresh. Was der Button leistet, muss in der
UI benannt sein, und alles, was aus der *bereits gelesenen* Datei ableitbar
ist — Verification-Tag voran — gehört in den Datei-Pass.

### <a name="tool26-09"></a> T-09 — Der Verification-Tag wird gelesen und weggeworfen

**Ort:** `core/library2/tag_cache.py::normalized_tag_snapshot`.

`read_file_tags` liefert `verification_status` aus dem eingebetteten
`SOULSYNC_VERIFICATION`-Tag (TXXX / Vorbis-Comment / MP4-Freeform) —
`core/tag_writer.py` Zeilen 49, 88, 115, 137. `normalized_tag_snapshot`
übernimmt aus diesem Ergebnis `title/artist/album/albumartist/track_number/
disc_number/year/genre/cover` sowie Lyrics und ReplayGain — und lässt
`verification_status` fallen. `rescan_files` liest den Wert also bei **jedem**
Refresh & Scan aus jeder Datei und verwirft ihn.

Messung über alle 268 real vorhandenen Dateien der Produktiv-DB:

| Kennzahl | Wert |
|---|---|
| Dateien mit `SOULSYNC_VERIFICATION`-Tag | **264** |
| `lib2_track_files.verification_status` gesetzt | **70** |
| daraus direkt heilbar | **194** |
| `library_history`-Zeilen mit Verification | 173 (`verified` 147, `unverified` 16, `human_verified` 10) |

`lib2_track_files.verification_status` wird heute nur vom Importer (aus
`tracks.verification_status`, dort selbst nur 7 Zeilen gefüllt), vom
Autolink-Import-Callback, vom AcoustID-Scanner und vom Human-Approve
geschrieben. Für alles, was vor diesen Pfaden importiert wurde, bleibt die
Spalte `NULL` — obwohl die Wahrheit in der Datei steht.

**Korrekturvertrag:** Der Datei-Pass persistiert den gelesenen
Verification-Wert. Er ist eine Beobachtung der Datei, kein neues Urteil: ein
bereits gesetzter, *stärkerer* Zustand (`human_verified`) darf nicht von einem
älteren Tag-Wert überschrieben werden.

### <a name="tool26-10"></a> T-10 — Keine eigene Verification-Spalte in der Track-Tabelle

**Ort:** `core/library2/ui_preferences.py::DEFAULT_PREFERENCES`,
`webui/…/library-v2-page.tsx`.

`TrackVerificationBadge` existiert und rendert vier Zustände
(`verified` / `human_verified` / `force_imported` / `unverified`), wird aber
ausschließlich als vierte Zeile **innerhalb der Quality-Zelle** gezeigt. Die
wählbaren Spalten sind `disc, artists, duration, bpm, match, quality,
features, metadata, file_path, play` — eine Verification-Spalte gibt es nicht.
Wer nach „wie wurde das verifiziert?" sortieren oder scannen will, muss die
Quality-Spalte eingeschaltet lassen und in jeder Zeile suchen. Zusammen mit
[T-09](#tool26-09) (72 % der Zeilen ohne Wert) erklärt das die Nutzeraussage
„es wird nie wirklich angezeigt".

Der Datenpfad ist vollständig: `queries.py` legt `verification_status`,
`acoustid_status` und `pipeline_result` bereits in `track.file`.

**Korrekturvertrag:** Eine eigene, opt-in Spalte `verification` in
`track_table.columns`/`column_order` mit demselben Badge.

### <a name="tool26-11"></a> T-11 — Zwei Jobs deklarieren `lib2`, lesen aber nur Legacy

**Ort:** `core/repair_jobs/__init__.py::JOB_DATA_BASIS` vs.
`core/repair_jobs/genre_cleanup.py`, `core/repair_jobs/comma_artist_splitter.py`.

`JOB_DATA_BASIS` führt beide als `'lib2'`; `register_job` erzwingt diese
Deklaration, prüft sie aber nicht gegen den tatsächlichen Code. `native_p3.py`
überschreibt sechs Job-Identitäten mit nativen Implementierungen
(`track_number_repair`, `acoustid_scanner`, `album_tag_consistency`,
`metadata_gap_filler`, `missing_cover_art`, `live_commentary_cleaner`) —
diese beiden sind nicht dabei:

| Job | gelesene Tabellen | Abdeckung in der Produktiv-DB |
|---|---|---|
| `genre_cleanup` | `artists`, `albums` | 5 von 5 Artists, **9 von 273 Alben** |
| `comma_artist_splitter` | `artists`, `tracks`, `albums` | **156 von 2.048 Tracks** |

Genre Tag Cleanup ist damit für ~97 % des Katalogs wirkungslos — und
gleichzeitig das einzige Genre-Werkzeug (→ [T-06](#tool26-06)). Der Comma
Artist Splitter sieht ~8 % der Tracks. Beide erzeugen zudem Findings mit
Legacy-Entity-IDs und laufen damit direkt in [T-01](#tool26-01).

**Korrekturvertrag:** Beide auf `active_file_subjects`/native Artist- bzw.
Albumzeilen umstellen (Muster: `native_p3.py`), oder — falls die Umstellung
eigenständig gescoped werden soll — bis dahin ehrlich als `filesystem`/legacy
kennzeichnen, statt eine nicht eingelöste `lib2`-Deklaration zu führen.

---

## 18. Auftrag: werkzeugweiser Integrations-Deep-Dive (offen, nach §17)

Ausdrücklicher Nutzerauftrag vom 26. Juli 2026: Nachdem die wichtigsten
Findings aus §17 abgearbeitet sind, ist **jedes einzelne registrierte Werkzeug**
gegen Library V2 zu prüfen. Die Werkzeugliste ist unübersichtlich geworden;
der Deep-Dive soll das auflösen, nicht nur Bugs sammeln.

**Pro Werkzeug zu beantworten:**

1. Liest es native `lib2_*`-Subjects oder noch Legacy-Tabellen? (Abgleich mit
   `JOB_DATA_BASIS` — die Deklaration ist heute ungeprüft, siehe T-11.)
2. Erzeugt es Findings mit auflösbarer nativer Identität (`lib2:<id>` oder
   `details['library_v2']`)?
3. Stimmt sein `JOB_LIBRARY_V2_EFFECTS`-Eintrag mit dem überein, was der Fix
   real anfasst — und löst `sync_repair_change` daraus die richtige
   Nachbereitung aus (Rescan, Artwork-Invalidierung, Wanted, History)?
4. Ergibt die Integration fachlich Sinn, oder ist der Job ein reines
   Betriebs-/Dateiwerkzeug ohne Katalogbezug (`cache_evictor`,
   `empty_folder_cleaner`, `expired_download_cleaner`)?
5. Welche Fehlerbilder sind konstruktiv möglich (Pfad-Mapping, Multi-File,
   Alias-Gruppen, Editionen, read-only Root, Restart mitten im Apply)?
6. Welche Funktionalität ist gegenüber dem Legacy-Stand verloren gegangen?
   (Bezugspunkt: [status.md §7](library-v2-status.md#7-tool-migration-und-cutover)
   und `RETIRED_JOB_IDS`.)
7. Überlappt der Job mit einem anderen? Wenn ja: Welcher ist die
   Beschaffungsstelle, welcher der Aufräumer?

**Vollständige Prüfliste (25 registrierte Jobs, Stand 26. Juli 2026).**
Die Namen in Klammern sind die in der UI sichtbaren Bezeichnungen.

| `job_id` | UI-Name | `data_basis` | erklärte V2-Effekte |
|---|---|---|---|
| `path_drift_reconcile` | Stale Index Paths | lib2 | observe, path |
| `metadata_gap_filler` | Metadata Gap Filler | lib2 | observe, metadata, tags |
| `missing_cover_art` | Cover Art Filler | lib2 | observe, metadata, tags, artwork |
| `dead_file_cleaner` | Dead File Cleaner | lib2 | observe, delete |
| `orphan_file_detector` | Orphan File Detector | lib2 | observe, path, new_file, delete |
| `track_number_repair` | Track Number Repair | lib2 | metadata, tags, path |
| `cache_evictor` | Cache Maintenance | filesystem | none |
| `acoustid_scanner` | AcoustID Scanner | lib2 | observe, tags, metadata |
| `missing_lyrics` | Lyrics Filler | lib2 | observe, tags |
| `replaygain_filler` | ReplayGain Filler | lib2 | observe, tags |
| `fake_lossless_detector` | Fake Lossless Detector | lib2 | observe |
| `empty_folder_cleaner` | Empty Folder Cleaner | filesystem | none |
| `short_preview_track` | Preview Clip Cleanup | lib2 | observe, delete, wanted |
| `genre_cleanup` | Genre Tag Cleanup | lib2 (falsch, T-11) | observe, metadata |
| `album_tag_consistency` | Album Tag Consistency | lib2 | observe, metadata, tags |
| `monitoring_list_reconcile` | Monitoring List Reconcile | lib2 | wanted |
| `library_reorganize` | Library Reorganize | lib2 | observe, path |
| `comma_artist_splitter` | Comma Artist Splitter | lib2 (falsch, T-11) | observe, tags |
| `audio_corruption_detector` | Corrupt File Detector | lib2 | observe, delete, wanted |
| `lossy_converter` | Lossy Converter | lib2 | observe, new_file, tags |
| `live_commentary_cleaner` | Live/Commentary Cleaner | lib2 | observe, delete, wanted |
| `expired_download_cleaner` | Expired Download Cleaner | filesystem | delete, wanted |
| `quality_info_backfill` | Quality Info Backfill | lib2 | metadata |
| `quality_upgrade_scan` | Quality Upgrade Scan (monitored) | lib2 | observe, wanted |
| `monitored_discography_refresh` | Monitored Discography Refresh | lib2 | discography, wanted |
| `skip_audit_cleanup` | Skip-Audit Cleanup | lib2 | none |

**Bereits bekannte Einstiegspunkte für den Deep-Dive** (nicht als vollständig
zu behandeln — sie sind das Ergebnis der §17-Stichprobe, nicht des Audits):

- `genre_cleanup`, `comma_artist_splitter`: legacy-only (T-11);
- `library_reorganize`, `album_tag_consistency`: erzeugen im Bestand Findings
  mit Legacy-IDs (T-01);
- `metadata_gap_filler`: keine Genre-Beschaffung (T-06);
- alle Jobs mit `tags`-Effekt hängen an der Cover-/Tag-Wahrheit aus
  T-04/T-05/T-07.

**Ergebnisform:** je Werkzeug ein kurzer Absatz mit Verdikt
(*integriert* / *teilintegriert* / *legacy* / *bewusst katalogfrei*), den
konkreten Fehlerbildern und — falls vorhanden — dem Korrekturvertrag. Neue
Bugs kommen in diese Datei, der Bearbeitungsstand ausschließlich in
`library-v2-status.md`.

---

## 19. Ergebnis des werkzeugweisen Deep-Dive (26. Juli 2026, Nacht)

Durchführung des Auftrags aus [§18](#18-auftrag-werkzeugweiser-integrations-deep-dive-offen-nach-17)
über alle 25 registrierten Jobs. Bearbeitungsstand in
[status.md §30](library-v2-status.md#30-werkzeugweiser-deep-dive-t-11-t-12-und-der-post-import-trigger-26-juli-2026-nacht).

### 19.1 Was das Audit als Ganzes ergeben hat

Der Katalogboden ist besser, als die §17-Stichprobe vermuten ließ: 23 der 25
Jobs lasen bereits native Subjects. Die Lücken lagen an zwei Stellen, und beide
sind **Identitäts-**, keine Datenbasisprobleme:

1. **Zwei Jobs lasen wirklich Legacy** — `genre_cleanup` und
   `comma_artist_splitter` ([T-11](#tool26-11)).
2. **Ein Job mintet native IDs in nackter Form** — `library_reorganize`
   ([T-12](#tool26-12), in diesem Audit neu gefunden). Seit T-01 ist eine
   nackte Zahl ausdrücklich eine *Legacy*-ID; ein nativ gemeinter Wert
   verlinkt damit potenziell eine fremde Zeile.

Alle übrigen 22 Jobs schreiben entweder `lib2:<id>`, gar keine Entity-ID
(Dateisystem-Werkzeuge) oder erzeugen überhaupt keine Findings.

**Vollständige Finding-Typ-Matrix** (Erzeuger → Fix-Handler in
`core/repair_worker.py::_fix_handlers`):

| Finding-Typ | Erzeuger | Fix | Bemerkung |
|---|---|---|---|
| `acoustid_mismatch` | `acoustid_scanner` | ✅ | |
| `album_tag_inconsistency` | `album_tag_consistency` | ✅ | |
| `comma_artist_split` | `comma_artist_splitter` | ✅ | seit T-11 nativ |
| `corrupt_audio` | `audio_corruption_detector` | ✅ | |
| `dead_file` | `dead_file_cleaner` | ✅ | |
| `empty_folder` | `empty_folder_cleaner` | ✅ | katalogfrei |
| `expired_download` | `expired_download_cleaner` | ✅ | |
| `fake_lossless` | `fake_lossless_detector` | — | **bewusst report-only** |
| `genre_cleanup` | `genre_cleanup` | ✅ | seit T-11 nativ |
| `metadata_gap` | `metadata_gap_filler` | ✅ | |
| `missing_cover_art` | `missing_cover_art` | ✅ | |
| `missing_discography_release` | `monitored_discography_refresh` | ✅ | |
| `missing_lossy_copy` | `lossy_converter` | ✅ | |
| `missing_lyrics` | `missing_lyrics` | ✅ | |
| `missing_replaygain` / `replaygain_retag` | `replaygain_filler` | ✅ | |
| `orphan_file` | `orphan_file_detector` | ✅ | |
| `path_mismatch` | `library_reorganize` | ✅ | |
| `quality_below_cutoff` | `quality_upgrade_scan` | ✅ | |
| `reorganize_unavailable` | `library_reorganize` | — | **bewusst report-only** |
| `short_preview_track` | `short_preview_track` | ✅ | |
| `stale_index_path` | `path_drift_reconcile` | ✅ | |
| `track_number_mismatch` | `track_number_repair` | ✅ | |
| `unwanted_content` | `live_commentary_cleaner` | ✅ | |

Drei Handler haben **keinen** Erzeuger mehr: `quality_upgrade`,
`missing_discography_track`, `canonical_version`. Das ist kein Fehler, sondern
die Kompatibilitätsfläche für `PRESERVED_RETIRED_FINDING_IDS` bzw. für
Findings des zurückgezogenen `canonical_version_resolve` — offene
Review-Zeilen aus der Zeit vor dem Rückbau bleiben bedienbar.

### 19.2 <a name="tool26-12"></a> T-12 — `library_reorganize` mintet nackte native IDs

**Ort:** `core/repair_jobs/library_reorganize.py` (beide `create_finding`-Aufrufe).

Der Job liest `lib2_albums`/`lib2_tracks` — seine `data_basis`-Deklaration ist
also ehrlich —, schrieb die Zeilen-ID aber unpräfixiert:
`entity_id=str(lib2_track_id)` bzw. `entity_id=str(album_id)`. Seit
[T-01](#tool26-01) löst `_resolve_links` eine nackte Zahl über
`lib2_tracks.legacy_track_id`/`lib2_albums.legacy_album_id` auf. Trägt
irgendeine andere native Zeile diese Zahl als Legacy-Rückverweis, wird sie
zum Subjekt der Reparatur.

Das ist keine theoretische Kollision: `annotate_finding_details` läuft bereits
**bei der Finding-Erzeugung**, der falsche Verweis wird also in die
gespeicherten `details` eingebrannt und nicht erst beim Fix errechnet.
Reproduziert (`tests/repair_jobs/test_library_reorganize_identity.py`): Track 4
ist Gegenstand des Findings, Track 9 trägt `legacy_track_id=4` — ohne Fix
liefert die Auflösung `track_ids=[4, 9]`.

Praktische Folgen bleiben begrenzt, weil `_fix_path_mismatch` ausschließlich
aus `details['from_abs']/['to_abs']` arbeitet und `reorganize_unavailable`
überhaupt keinen Fix hat. Was leidet, ist die Nachbereitung: ein zusätzliches
Maintenance-Event, ein überflüssiger `rescan_files` und eine Historie, die
einen unbeteiligten Track als repariert ausweist.

**Korrekturvertrag:** Beide Aufrufe schreiben `lib2:<id>` und zusätzlich einen
`details['library_v2']`-Block. Bereits gespeicherte Findings der alten Form
bleiben unangetastet — sie werden beim nächsten Scan in der neuen Form neu
erzeugt.

### 19.3 Verdikte je Werkzeug

**Integriert (nativ, auflösbare Identität, korrekte Nachbereitung).**

- `path_drift_reconcile` — liest `lib2_track_files`, Finding `lib2:<file_id>`,
  Fix bewegt nichts und repointet nur den Index. Fehlerbild: eine mehrdeutige
  Zeile trägt bewusst keinen Vorschlag und bleibt unfixbar.
- `metadata_gap_filler`, `missing_cover_art`, `track_number_repair`,
  `acoustid_scanner`, `album_tag_consistency`, `live_commentary_cleaner` —
  die sechs `native_p3`-Überschreibungen. Subjects aus
  `active_file_subjects`/`active_album_subjects`, Findings `lib2:`, Details mit
  `subject_details`.
- `dead_file_cleaner` — meldet ausschließlich `file_state='missing_confirmed'`
  und stützt sich damit auf den Missing-Lifecycle aus `rescan_files`. Der
  Drift-Schutz greift also vor dem Löschvorschlag: eine nur umbenannte Datei
  wird `missing_suspected` und nie bestätigt (Guide §5).
- `orphan_file_detector` — Finding ohne Entity-ID, Subjekt ist der Pfad; genau
  richtig, denn eine Waise hat per Definition keine Katalogzeile.
- `fake_lossless_detector`, `short_preview_track`, `audio_corruption_detector`,
  `lossy_converter`, `missing_lyrics`, `replaygain_filler` — alle über
  `active_file_subjects`, alle mit `lib2:`-Identität.
- `quality_upgrade_scan`, `monitored_discography_refresh`, `skip_audit_cleanup`,
  `monitoring_list_reconcile`, `quality_info_backfill` — lesen und schreiben
  ausschließlich `lib2_*` und brauchen keinen Fix-Umweg: sie mutieren im Scan
  und sind damit ihre eigene Konvergenz.
- `expired_download_cleaner` — `data_basis` ist `filesystem`, aber der Job
  ruft vor jedem Löschen `annotate_finding_details` und bricht ab, wenn die
  V2-Subjekte nicht erfasst werden können. Vorbildlich: Löschen ohne
  auflösbares Subjekt findet nicht statt.
- `library_reorganize` — nach T-12 integriert. Der Auto-Modus konvergiert
  ohnehin nicht über den Job, sondern über `core/reorganize_runner.py`, das
  `lib2_track_files.path` selbst fortschreibt und `sync_repair_change` ruft.
- `genre_cleanup`, `comma_artist_splitter` — nach T-11 integriert.

**Bewusst katalogfrei.**

- `cache_evictor` — löscht Cache-Dateien, `effects={'none'}`. Kein Katalogbezug,
  keine Integration nötig oder erwünscht.
- `empty_folder_cleaner` — `entity_id` ist ein Verzeichnispfad. `subject_unlinked`
  ist hier der **korrekte** Ausgang und genau der Grund, warum
  [T-02](#tool26-02) diesen Zustand meldet statt ihn zum Fehler zu erklären.

**Kein Job mehr mit Verdikt *legacy*.**

### 19.4 Überlappungen (Frage 7) — wer beschafft, wer räumt auf

| Domäne | Beschaffung | Aufräumer | Bemerkung |
|---|---|---|---|
| Cover | `missing_cover_art` | — | `album_tag_consistency` prüft nur Konsistenz |
| Genres | **niemand** | `genre_cleanup` | die Asymmetrie hinter [T-06](#tool26-06) |
| Provider-IDs | `metadata_gap_filler` | — | füllt `isrc`/`musicbrainz_recording_id` |
| Index ↔ Disk | `orphan_file_detector` (adoptiert) | `dead_file_cleaner` (löscht) | `path_drift_reconcile` sitzt bewusst **davor**: es repointet, bevor irgendwer löscht |
| Dateiqualität | `quality_upgrade_scan` | `audio_corruption_detector`, `short_preview_track` | `fake_lossless_detector` meldet nur |
| Wanted-Projektion | `monitoring_list_reconcile` | `skip_audit_cleanup` | |

Die einzige echte Lücke dieser Tabelle ist die Genre-Zeile: ein Aufräumer ohne
Beschaffungsstelle. Sie bleibt nach der Nutzerentscheidung vom 26. Juli
bewusst offen ([T-06](#tool26-06), status.md §30).

### 19.5 Was das Audit **nicht** geprüft hat

Die Fehlerbild-Frage (§18 Punkt 5) ist pro Werkzeug nur konstruktiv
beantwortet, nicht durch Injektion belegt: Restart mitten im Apply,
read-only Root und Windows-/Docker-Pfad-Mapping bleiben Teil des
§9-Release-Gates und sind für keinen der 25 Jobs einzeln durchgespielt worden.

---

## 20. Nutzer-UI- und Funktionale Anforderungen für Library V2 (27. Juli 2026)

Diagnosen und Fix-Aufträge für die vom Nutzer am 27. Juli 2026 gemeldeten UI-Punkte, Fehlerbilder und Spezifikationen.

> **Wichtiger Arbeitsauftrag für die nächste Chat-Session:** Der nächste Chat muss vor der Ausführung aller in Abschnitt 20 genannten Punkte selbstständig weiter im Code recherchieren und bei etwaigen Unklarheiten oder Detailentscheidungen gezielt Gegenfragen an den Nutzer stellen!


### 20.1 <a name="iss27-01"></a> iss27-01 — Interactive Search ist defekt und Quellenauswahl unübersichtlich — Vollständig behoben, 27. Juli 2026 (Nachtrag §21)

**Symptom:** Klick auf Interactive Search bei Alben oder Einzel-Tracks schlägt fehl, zeigt keine Kandidaten oder bricht ab. Auch Automatic Search ist dadurch betroffen. Die Checkboxen („Quality Check“, „Acoustic ID Check“, „Only Show Results with Cutoff“) wirken optisch unansehnlich und die Quellenauswahl ist schwer verständlich.

**Root Cause / Referenz:** Interactive Search nutzt im Backend (`core/search/`) und im WebUI-Binding nicht die stabilen Abfrage- und Filterstrukturen der bestehenden Basic Search (`webui/src/routes/search/`).

**Fix-Vertrag:**
1. Backend-Pipeline für Interactive Search an der Implementierung von Basic Search ausrichten.
2. Quellenauswahl: Standardmäßig alle aktivierten Quellen gleichzeitig durchsuchen und ein einfaches, verständliches Umschalt-Filter-Interface bereitstellen (wie in Basic Search).
3. Automatic Search auf Track-Ebene strikt auf die jeweilige Track-Entity scopen.
4. UI-Redesign der Checkboxen („Quality Check“, „AcoustID Check“, „Cutoff-Filter“) zu modernen Toggle-Controls.

**Umsetzung (27. Juli 2026):** Root-Cause-Recherche zeigte, dass Interactive
Search und Basic Search bereits denselben Backend-Endpunkt
(`/api/search` → `core/search/basic.py::run_basic_search`) treffen — der in
der Doku vermutete Strukturunterschied existiert nicht (mehr). Zwei echte
Bugs gefunden und behoben: (1) `buildSearchQuery` baute für unbetitelte
Tracks eine garantiert leere Anfrage wie `"Artist Track ?"` — fällt jetzt auf
den Albumtitel zurück (`webui/.../library-v2-page.tsx`, Test
`build-search-query.test.ts`). (2) Ohne explizite Quellenauswahl wurde nur
EINE Quelle befragt („configured default“) statt aller aktivierten Quellen —
`InteractiveSearchModal` fragt jetzt bei mehreren konfigurierten Quellen alle
parallel ab und mergt die Ergebnisse; ein einzelner ausgefallener Source
leert nicht mehr die gesamte Liste (`interactive-search.tsx`, Tests in
`interactive-search.test.tsx`).

**Nachtrag (§21, 27. Juli 2026, Folgesitzung):** Punkt 4 (Toggle-Redesign)
und die Multi-Select-Chip-Quellenauswahl sind jetzt ebenfalls umgesetzt —
siehe [§21.4](library-v2-issues.md#iss27-01-toggle) für Details. Der Dropdown
ist komplett durch eine Chip-Reihe ersetzt, echtes Multi-Select statt
Single-Pick/„Alle". Damit ist iss27-01 vollständig abgeschlossen.

### 20.2 <a name="iss27-02"></a> iss27-02 — Tag Gaps Klick-Aktion löst Re-Fetch und Tag-Schreiben nicht aus — Behoben, 27. Juli 2026

**Symptom:** Im Tags Match Werkzeug führt das Klicken auf ein fehlendes Tag (Tag Gap) nicht dazu, dass die entsprechenden Metadaten/Artwork von den Providern neu heruntergeladen und in die physische Audio-Datei geschrieben werden.

**Fix-Vertrag:**
1. Event-Handler und API-Endpunkt für Tag-Gap-Klicks reparieren.
2. Der Klick muss gezielt den Provider-Re-Fetch für dieses Feld starten und das Ergebnis über den Tag-Writer direkt in die physischen Audio-Tags schreiben.
3. Beim Hovern über den Tags-Match-Status/Chip einen informativen Tooltip/Popover anzeigen, der analog zur Metadata-Vorschau exakt auflistet, welche Tags vorhanden sind und welche fehlen.

**Umsetzung (27. Juli 2026):** Root Cause war, dass `write_tags` nur
Feldwerte schreibt, die die Katalog-DB bereits kennt — für einen Artist, der
nie angereichert wurde (siehe
[[library-v2-native-artist-enrich-deadend]]-artige Fälle), stand da schlicht
nichts. Neuer Endpunkt `POST /api/library/v2/tracks/<id>/fill-tag-gaps`
(`api/library_v2.py`) komponiert die vorhandenen Bausteine: läuft
`enrich_native_entity_for_service` in Provider-Prioritätsreihenfolge
(deezer→itunes→spotify→discogs→musicbrainz→jiosaavn→bandcamp) über das
Album, bis einer greift, committet den Treffer und schreibt danach mit
`retag.write_tags` die (jetzt evtl. gefüllten) Feldwerte in die Datei —
Provider-Fehler pro Anbieter sind isoliert (ein Timeout/Fehler bricht die
Kette nicht ab). Frontend-Klick auf „N tag gaps“ ruft jetzt diesen Endpunkt
statt des reinen Write-Jobs (`fillLibraryV2TagGaps` in
`-library-v2.api.ts`, `TrackMetadataGapsCell`). Tests:
`tests/library2/test_api_routes.py::test_fill_tag_gaps_*` (Backend, 5 Fälle)
und `track-feature-badges.test.tsx` (Frontend). Punkt 3 (Hover-Popover) war
schon vor dieser Session über das native `title`-Attribut
(`metadataGapsTooltip`) funktional abgedeckt — eine visuell reichhaltigere
Popover-Variante bleibt offen (reine Politur).

### 20.3 <a name="iss27-03"></a> iss27-03 — Change Photo / Artist-Bild-Fetch unzuverlässig — Teilweise behoben, 27. Juli 2026

**Symptom:** Beim Öffnen des Foto-Pickers („Change Photo“) werden für manche Künstler nicht alle verfügbaren Provider-Bilder geladen oder einzelne Quellen fehlen vollständig in der Auswahl.

**Fix-Vertrag:**
1. Provider-Fetch-Pipeline für Artist-Fotos prüfen: Alle 5 bis 6 konfigurierten Provider (Spotify, MusicBrainz, Deezer, Fanart.tv, iTunes etc.) müssen verlässlich abgefragt werden.
2. Fehlerantworten oder Timeouts einzelner Provider isolieren, sodass funktionierende Quell-Kandidaten im Picker vollständig gerendert werden.

**Umsetzung (27. Juli 2026):** Jeder Provider war bereits einzeln
try/except-isoliert — der eigentliche Root Cause war ein fehlendes
Zeitbudget: `pool.map()` blockierte synchron auf den langsamsten Thread
(iTunes-Rate-Limiter schläft bis zu 60s bei 403, Discogs-Backoff bis zu
30s — beides INNERHALB des Worker-Threads), während der Frontend-Client nur
10s (ky-Default) wartete — ein bereits vollständig erfolgreicher
Backend-Lauf wurde so verworfen. Fix in
`core/metadata/artist_image.py::gather_artist_image_candidates`: statt
`pool.map()` jetzt `ThreadPoolExecutor` + `concurrent.futures.wait(...,
timeout=10)` — ein einzelner langsamer Provider verpasst nur diese Runde,
statt die Antwort für alle zu blockieren. Zusätzlich: MusicBrainz lieferte
trotz vorhandener MBID nie einen Kandidaten (`_CANDIDATE_SKIP_SOURCES`
schließt es aus dem generischen Namens-Suchpfad aus) — der bereits
existierende, exakte `_image_from_musicbrainz_relations`-Resolver wird jetzt
zusätzlich als eigene Kandidatenquelle abgefragt, wenn eine MBID bekannt
ist. Frontend: `fetchLibraryV2ArtistArtOptions`/`fetchLibraryV2AlbumArtOptions`
erhalten `timeout: 20_000` (passend zum neuen Backend-Budget), plus ein
manueller „⟳ Refresh"-Button im Picker-Modal, der den 5-Minuten-Cache mit
`?refresh=1` umgeht (`art-picker-modal.tsx`). **Nicht umgesetzt:** Fanart.tv
ist in keinem der aktuell existierenden Sources verdrahtet (nur im separaten
Video-Enrichment-Modul, `core/video/*`) — das wäre eine neue
Provider-Integration, kein Bugfix, und bleibt bewusst offen. Tests:
`tests/test_artist_image_picker.py` (3 neue Fälle), `art-picker-modal.test.tsx`.

### 20.4 <a name="iss27-04"></a> iss27-04 — Artist-Navigation behält Tab „All Releases" und löst ungewollte Diskografie-Fetches aus — Behoben, 27. Juli 2026

**Symptom:** Wenn der Nutzer in der Artist-Ansicht auf den Tab „All Releases" wechselt und anschließend zu einem anderen Künstler navigiert, bleibt die UI im Tab „All Releases" und löst sofort ein vollständiges Herunterladen der Diskografie des neuen Künstlers aus.

**Root Cause:** In `webui/src/routes/library-v2/-ui/library-v2-page.tsx`, `ArtistDetailView` liest `releasesMode` aus den URL-Suchparametern (`search.releases`). Beim Wechsel von Artist A zu Artist B behält der Router die Suchparameter bei (`releases=all`), wodurch `useEffect` auf den Mount-State anspricht, `shouldAutoFetchDiscography` auf `true` auswertet und automatisch `updateDiscography()` für den neuen Artist auslöst.

**Fix-Vertrag:**
1. Bei jeder Navigation zu einem neuen Artist (ID-Wechsel) muss der `releases`-Suchparameter in der Navigation zwingend auf `'library'` zurückgesetzt (oder weggelassen) werden.
2. Das Laden von „All Releases" darf erst durch einen expliziten Klick des Nutzers auf den Tab ausgelöst werden.

**Umsetzung (27. Juli 2026):** Root Cause exakt wie vermutet: `ArtistDetailView`
wird nicht neu gemountet, wenn nur `search.artist` wechselt, und alle
Navigations-Aufrufe spreadeten die vorherigen Suchparameter (`...p`) ohne
`releases` zurückzusetzen. An allen vier Stellen, die `artist` auf eine neue
ID setzen (`library-v2-page.tsx`: Wanted-Zeile, Artist-Karten-Grid,
Artist-Tabellenzeile, sowie `AlbumDetailView`s „zurück zum Artist"), wird
jetzt zusätzlich `releases: undefined` gesetzt (fällt via Zod-Default auf
`'library'` zurück). Test: `releases-mode.test.ts` (bestehend, weiterhin
grün) — die Navigations-Callsites selbst sind mechanisch und wurden per
Typecheck + bestehender Testsuite abgesichert.

### 20.5 <a name="iss27-05"></a> iss27-05 — Refresh & Scan liest Audio-File-Features und Verifikations-Tags nicht vollständig aus — Bereits behoben, verifiziert 27. Juli 2026

**Symptom:** Ein „Refresh & Scan" im Artist-Kontext führt derzeit zum Teil keine echte Neu-Inspektion der physischen Dateien durch oder erfasst eingebettete Eigenschaften (Audio-Stream-Details wie 24-Bit/44.1kHz, ReplayGain, Lyrics, embedded Cover) sowie im Tag geschriebene Verifikations-Zustände (`HUMAN_VERIFIED`, `ACOUSTICID_VERIFIED`, `RETRY_IMPORT`) nicht verlässlich.

**Fix-Vertrag:**
1. Refresh & Scan im Artist-Kontext strikt auf die Dateien dieses Künstlers scopen.
2. Echte Datei-Neu-Inspektion ausführen: `probe_audio_quality` (Bitrate, Sample Rate, Bit Depth), Tag-Inspection für Features und Verifikations-Tags (`HUMAN_VERIFIED`, `ACOUSTICID_VERIFIED`, `RETRY_IMPORT`).
3. Re-Verify gegen das effektive Quality Profile und Aktualisierung der V2-Datenbank-Zeilen.

**Verifikation (27. Juli 2026):** Dieser Punkt war zum Zeitpunkt der
Meldung bereits durch Commit `0cd7167a6` („fix(library-v2): close the
tag/cover/verification convergence gaps (T-03, T-07, T-09, T-10)“, selber
Tag, vor dieser Session bereits auf `HEAD`) behoben:
`core/library2/scan.py::rescan_files` scoped strikt auf die Artist-Alben,
probt `probe_audio_quality` real pro Datei und persistiert
`verification_status` aus dem `SOULSYNC_VERIFICATION`-Tag korrekt. Die in
diesem Issue genannten Tag-Namen `HUMAN_VERIFIED`/`ACOUSTICID_VERIFIED`/
`RETRY_IMPORT` existieren im Code nicht — die reale, einzige
Verifikations-Tag-Quelle ist `SOULSYNC_VERIFICATION` mit vier Zuständen
(`core/matching/verification_status.py`); die genannten Namen stammen aus
dem noch nicht gebauten F-15/UI-03-Spezifikationstext. Re-verifiziert per
`tests/library2/test_scan_scope.py` (14/14 grün). Keine Code-Änderung nötig.

### 20.6 <a name="iss27-06"></a> iss27-06 — Column Settings Dialog hat exzessives vertikales Scrolling

**Symptom:** Das Modal für die Tabellenspalten-Einstellungen ordnet alle Optionen untereinander in einer langen Liste an, was zu unübersichtlichem vertikalen Scrollen führt.

**Fix-Vertrag:** Redesign des Column Settings Dialogs in ein kompaktes Mehrspalten- oder Tab-Layout (z.B. Tabs für „Sichtbare Spalten“, „Match-Provider“, „Quality & Badges“, „Sortierung“).

### 20.7 <a name="iss27-07"></a> iss27-07 — Preview Re-Tag unübersichtlich bei mehreren Alben

**Symptom:** In der Vorschau von Re-Tag sind die vorgeschlagenen Tag-Änderungen über verschiedene Alben hinweg nicht ausreichend visuell voneinander getrennt.

**Fix-Vertrag:** Re-Tag Preview UI um klare Album-Header und visuelle Sub-Divisions pro Album/EP/Single ergänzen.

### 20.8 <a name="iss27-08"></a> iss27-08 — Maintenance unter Files & Tools unübersichtlich und versteckt

**Symptom:** Werkzeuge unter Files & Tools -> Maintenance (wie Meta Gapfill, Album Tag Consistency) sind optisch versteckt, unübersichtlich und die Nomenklatur „Maintenance“ ist für Nutzer schwer verständlich.

**Fix-Vertrag:** Optische Reorganisation von Maintenance: Eindeutige, verständliche Modul-Bezeichnungen, visuelle Trennung von artist-spezifischen vs. globalen Jobs und verbesserte Zugänglichkeit.

## 21. Interactive Search: 0-Treffer-Bug, Quarantäne-Feedback, Quellen-Chips, Indexer-als-Artist (27. Juli 2026, Folgesitzung)

Der Nutzer meldete am selben Tag, in einer Folgesitzung zu §20/§32, dass
Interactive Search für bestimmte Titel weiterhin 0 Treffer liefert (konkretes
Beispiel unten), fragte nach dem Quarantäne-Verhalten bei deaktivierten
Checks, und meldete einen Indexer-Namen (z.B. „NZBGeek“), der als Artist
angezeigt wird. Auftrag: Interactive Search „bombenfest“ machen und die in
§20.1 offengelassenen UI-Punkte abschließen.

### 21.1 <a name="iss27-09"></a> iss27-09 — Interactive Search liefert 0 Treffer bei Titeln mit verschachtelten Klammern — Behoben, 27. Juli 2026

**Symptom:** Konkretes Nutzerbeispiel: „Drenchill – Freed from Desire (feat.
Indiiana) - DNF Extended Remix (Freed from Desire (feat. Indiiana))“ liefert
0 Suchergebnisse, obwohl der Track uneingeschränkt verfügbar ist.

**Root Cause:** `buildSearchQuery` (`webui/src/routes/library-v2/-ui/library-v2-page.tsx`)
entfernte den anhängenden „(Album)“-Kontext per Regex
`\(([^)]*)\)\s*$` — diese kennt keine verschachtelten Klammern. Enthält der
Album-/Tracktitel selbst eine Klammer (hier: der „(feat. Indiiana)“-Credit),
findet die Regex keinen gültigen Abschluss mehr, und der GESAMTE Tail —
Tracktitel UND der eigentlich zu entfernende, duplizierte Album-Kontext —
landet unverändert in der Suchanfrage. Ergebnis: eine sehr lange, inhaltlich
doppelte Anfrage, die kein realer Dateiname je matcht — bei jedem Titel mit
einem Klammer-Credit (feat./remix-Klammern etc.), nicht nur im gemeldeten
Einzelfall.

**Fix:** Klammertiefen-bewusstes Parsing über eine neue Hilfsfunktion
`splitTrailingParenGroup` (Tiefenzähler von hinten statt einer flachen
Regex) ersetzt den alten Ansatz — findet die tatsächliche äußerste,
balancierte Klammergruppe am Stringende auch dann, wenn ihr Inhalt selbst
Klammern enthält. Ein unbalancierter Rest (z.B. eine offene Klammer ohne
Schluss) wird unverändert gelassen statt verstümmelt. Tests:
`build-search-query.test.ts` (3 neue Fälle, inkl. des exakten gemeldeten
Beispiels und eines Fallback-auf-Album-Falls mit verschachtelten Klammern).

### 21.2 <a name="iss27-10"></a> iss27-10 — Kein Feedback im Interactive-Search-Fenster, wenn ein Grab später doch in Quarantäne landet — Behoben, 27. Juli 2026

**Symptom/Frage des Nutzers:** Beim Klick auf „Download“ in Interactive
Search ist unklar, ob eine Datei trotz deaktiviertem Quality-/AcoustID-Check
noch in der Quarantäne landen kann; falls ja, sollte das Fenster sofort eine
Fehlermeldung zeigen. Erwartetes Verhalten laut Nutzer: Ein deaktivierter
Check soll die Datei trotzdem durch die Pipeline durchwinken (markiert als
„AcoustID nicht verifiziert“ / „Qualitätsprofil nicht erfüllt“), nicht
quarantänisieren.

**Recherche:** Der serverseitige Bypass war bereits korrekt:
`core/imports/pipeline.py::_should_skip_quarantine_check` respektiert
`skip_acoustid`/`quality_check=false` bereits exakt für die Quality- und
AcoustID-Gates (`_skip_quality`, `_skip_acoustid`, ca. Zeile 827 bzw. 952) —
ein deaktivierter Check quarantänisiert nicht, sondern importiert mit
`_acoustid_result='skip'` bzw. einem `user_override`-Journal-Eintrag; keine
Code-Änderung nötig. Andere Gates (Integrity/Silence/Duration-Abgleich) sind
bewusst NICHT über diese zwei Checkboxen abschaltbar (Schutz vor kaputten/
abgeschnittenen Dateien) und können weiterhin quarantänisieren — das ist
gewolltes Verhalten, kein Bug. Die eigentliche Lücke war eine andere: nach
einem Grab zeigt Interactive Search nur den Dispatch-Erfolg („Grabbed ✓“) —
der reale Ausgang (Quarantäne, Import) läuft asynchron in der
Post-Processing-Pipeline (Sekunden bis Minuten später) und war im Fenster
selbst unsichtbar, egal welcher Check aktiv war.

**Fix:** `InteractiveSearchModal` pollt nach einem erfolgreichen Dispatch für
Grabs mit lib2-Track-/Album-Kontext die bereits bestehende
Merged-Pipeline-History (`GET /api/library/v2/tracks|albums/<id>/history`,
`core/library2/history_feed.py`, bereits für den History-Tab genutzt) alle 4s
für bis zu 2 Minuten. Ein frisches Event mit `category='quarantined'` oder
`'failed'` nach Grab-Start zeigt sofort Titel+Detail als Inline-Fehler im
Fenster (Button wechselt zu „Retry“); ein `'imported'`-Event bestätigt
„Grabbed ✓“. Kein Backend-Change nötig — reine Client-Logik
(`classifyGrabOutcome`, eine pure Funktion, unabhängig unit-getestet).
Zwischenzustand „Verifying…“ während des Pollings macht sichtbar, dass der
Ausgang noch offen ist. Tests: `interactive-search.test.tsx` (6 neue Fälle
für `classifyGrabOutcome`, 1 Integrationstest für den vollen
Dispatch-→-Quarantäne-→-Inline-Fehler-Ablauf).

### 21.3 <a name="iss27-11"></a> iss27-11 — Usenet-/Torrent-Indexer (z.B. „NZBGeek“) erscheint als Artist-Name — Behoben, 27. Juli 2026

**Symptom:** Bei Usenet-Suchergebnissen wird gelegentlich der Name des
Indexers (z.B. „NZBGeek“) als Artist angezeigt, obwohl das ein Indexer und
kein Künstler ist.

**Root Cause:** `core/download_plugins/usenet.py::_project_results` und das
identische Pendant in `torrent.py` setzten
`artist=parsed_artist or result.indexer_name or 'Usenet'/'Torrent'` — wenn
`_parse_release_title` im Release-Titel kein „Artist - Title“-Trennzeichen
findet (liefert dann `''` für Artist), fällt der Code auf den
INDEXER-Namen zurück, der eigentlich nur in `_source_metadata['indexer']`
gehört. Der Kommentar an der Stelle erklärte korrekt, WARUM überhaupt ein
nicht-leerer Fallback nötig ist (verhindert, dass
`TrackResult.__post_init__` den Dateinamen — der mit dem opaken
Candidate-Token beginnt — als Artist fehlparst), aber die WAHL des
Fallback-Werts war falsch: ein Indexer ist keine Person/Band.

**Fix:** Fallback auf einen generischen Platzhalter `'Unknown Artist'`
statt des Indexer-Namens, in beiden Plugins (`usenet.py`, `torrent.py`).
Ein bestehender Test
(`test_torrent_project_falls_back_to_indexer_name_when_title_lacks_dash`)
kodifizierte das alte (falsche) Verhalten explizit als erwartet — korrigiert
zu `..._placeholder_when_title_lacks_dash`, plus ein neuer Parity-Test fürs
Usenet-Plugin. Tests: `tests/test_torrent_usenet_plugins.py` (51/51 grün).

### 21.4 <a name="iss27-01-toggle"></a> Abschluss iss27-01 Punkt 4 — Toggle-Redesign & Multi-Select-Quellen-Chips — Behoben, 27. Juli 2026

**Ausgangslage:** iss27-01 (§20.1) hatte den funktionalen Teil (alle
konfigurierten Quellen parallel durchsuchen) bereits behoben, aber zwei rein
visuelle Punkte offengelassen: (4) Toggle-Redesign der Checkboxen, und die
vollständige Multi-Select-Chip-Quellenauswahl statt Dropdown.

**Umsetzung:**
1. **Quellenauswahl:** Der `<select>`-Dropdown ist durch eine Chip-Reihe
   ersetzt (`role="group"`, ein Chip pro konfigurierter Quelle plus ein
   „All sources“-Reset-Chip). Jeder Quellen-Chip ist unabhängig togglebar
   (`excludedSources: Set<string>` statt eines Single-Value-States) — echtes
   Multi-Select, nicht nur Einzelquelle-oder-alle. Ein Guard verhindert, dass
   die letzte verbleibende aktive Quelle abgewählt wird (eine Suche über 0
   Quellen wäre ein Fehlzustand, kein Filter). `run()` durchsucht jetzt genau
   die aktive Teilmenge parallel (bei >1 aktiver Quelle), statt zwingend
   „genau eine oder alle“.
2. **Toggle-Redesign:** Die drei Checkboxen (Quality check, AcoustID check,
   Only show results meeting cutoff) sind jetzt als Slide-Toggles gestylt —
   rein visuell über CSS (`.toggleSwitch` in `library-v2-page.module.css`),
   das zugrundeliegende `<input type="checkbox">` bleibt unverändert (gleiche
   Rolle/Tastatur-Semantik, bestehende Tests unverändert grün).

Tests: `interactive-search.test.tsx` (neuer Multi-Select-Chip-Test, der
bestehende Quellenwahl-Test auf Chip-Interaktion statt `<select>` umgestellt),
`npx vitest run src/routes/library-v2` (186/186 grün), `tsc --noEmit` und
`oxlint --type-check src` sauber.

### Verifikation (§21 gesamt)

- Frontend: `npx vitest run src/routes/library-v2` — 186/186 grün
  (29 Dateien, davon 2 mit neuen Tests: `build-search-query.test.ts`,
  `interactive-search.test.tsx`); `tsc --noEmit -p tsconfig.json` und
  `oxlint --type-check src` sauber (0 Fehler/Warnungen).
- Backend: `tests/test_torrent_usenet_plugins.py` — 51/51 grün.
- Geänderte Dateien: `webui/src/routes/library-v2/-ui/library-v2-page.tsx`,
  `webui/src/routes/library-v2/-ui/interactive-search.tsx`,
  `webui/src/routes/library-v2/-ui/library-v2-page.module.css`,
  `webui/src/routes/library-v2/-ui/build-search-query.test.ts`,
  `webui/src/routes/library-v2/-ui/interactive-search.test.tsx`,
  `core/download_plugins/usenet.py`, `core/download_plugins/torrent.py`,
  `tests/test_torrent_usenet_plugins.py`.

### Einstufung

iss27-01 ist damit vollständig (funktional + visuell) abgeschlossen. Drei
neue, unabhängig gefundene Bugs (iss27-09, iss27-11) sind behoben, plus eine
neue Feedback-Funktion für den Quarantäne-Fall (iss27-10) — dessen
zugrundeliegender Bypass-Mechanismus sich bei der Recherche als bereits
korrekt implementiert herausstellte. Nicht Teil dieser Session: Punkt 8
(Column Settings Layout), iss27-07 (Re-Tag Preview), iss27-08
(Maintenance-Umbenennung) — bewusst unangetastet, reine Design-Entscheidungen
außerhalb des gemeldeten Scopes. Live-Verifikation im Browser gegen einen
echten Soulseek/Usenet-Backend-Stack stand in dieser Session nicht zur
Verfügung (kein laufender `dev.py`); empfohlen vor dem nächsten Nutzer-Test.

## 22. Live-Test-Feedback zu §21/§33: Usenet-Regression, kaputte Toggle-Optik, fehlendes Timeout-Verhalten — Verified, 27. Juli 2026

**Wichtig:** Genau die in §21 als "Live-Verifikation stand nicht zur
Verfügung" markierte Lücke hat sich als real herausgestellt — der Nutzer hat
§21/§33 direkt im Browser getestet und drei konkrete Probleme gemeldet.
Der historische Zwischenstand bleibt unten nachvollziehbar; die spätere
Abschlussdiagnose hat alle drei Punkte behoben und regressionsgeprüft.

### 22.1 <a name="iss27-12"></a> iss27-12 — Usenet liefert seit §21/§33 keine Ergebnisse mehr (Regression) — Verified

**Symptom:** Laut Nutzer lieferte die Usenet-Quelle in Interactive Search
vor den §21/§33-Änderungen noch Ergebnisse; seit dem Umbau der
Quellenauswahl auf Multi-Select-Chips (§21.4) liefert sie gar nichts mehr.

**Abschlussdiagnose:** Der Backend-/`source`-Verdacht war nicht die Ursache.
Die neue UI speicherte **ausgeschlossene** Quellen. Im Defaultzustand waren
„All sources" und alle Einzelchips als gedrückt dargestellt; ein Nutzerklick
auf den gewünschten „Usenet"-Chip schloss Usenet jedoch aus. Die Regression
war damit rein semantisch und durch den alten Test („Deselecting Soulseek
leaves Usenet") sogar auf das falsche Verhalten festgeschrieben.

**Fix:** `selectedSources` ist nun eine positive exakte Auswahl; leer bedeutet
eindeutig „All sources", der erste Einzelklick wählt genau diese Quelle.
Search-Requests senden außerdem die Track-/Album-ID, damit Candidate-Tokens
bereits bei der Suche entity-gebunden sind. Regressionstest: Klick auf
„Usenet" erzeugt ausschließlich `{source: "usenet", lib2_track_id,
lib2_album_id}`.

**Historische Recherche:** Der naheliegendste Verdacht
war, dass `run()` in `interactive-search.tsx` jetzt — anders als vorher —
IMMER einen expliziten `source`-Parameter an `searchSources()` übergibt,
auch im reinen Single-Source-Modus (vorher wurde dort `undefined`
übergeben und die Entscheidung dem Backend überlassen). Das würde
`core/search/basic.py::run_basic_search` vom impliziten
`download_orchestrator.search(query)`-Pfad (Zeile ~75) auf den expliziten
`download_orchestrator.client(source)`-Pfad (Zeile ~53-73) umlenken.
Gegenrecherche in `core/download_orchestrator.py` zeigt aber: im
Single-Source-Modus ist `self._client(self.mode)` (impliziter Pfad,
`DownloadOrchestrator.search()` Zeile ~319) **dieselbe Methode** wie
`self.client(name)`/`self.registry.get(name)` (expliziter Pfad) — beide
lösen exakt denselben Client mit denselben effektiven Default-Argumenten
(`timeout=None`) auf. Diese Theorie ist damit widerlegt, zumindest für den
reinen Single-Source-Fall. Für den Hybrid-Fall (mehrere Quellen inkl.
Usenet parallel) ändert sich durch §21.4 nichts an den gesendeten
Requests: `activeSources.map((s) => searchSources(q, s.name))` ist bei
leerem `excludedSources` bytgleich mit dem alten
`allSources.map((s) => searchSources(q, s.name))`.

Ein echter Prowlarr-/Usenet-Live-E2E bleibt Release-Gate, ist aber für die
bestätigte Chip-Ursache nicht mehr der Blocker.

### 22.2 <a name="iss27-13"></a> iss27-13 — Quality-/AcoustID-/Cutoff-Toggles rendern visuell kaputt — Verified

**Symptom:** Laut Nutzer sehen die drei neuen Toggle-Switches (§21.4) "komplett
kaputt" aus — sichtbar ist nur ein weißer Punkt, UND zusätzlich weiterhin
eine native Checkbox-Box daneben/darunter, statt eines sauberen
Slide-Toggles.

**Abschlussdiagnose und Fix:** `.toggleSwitch::before` lag direkt auf einem
ersetzten `<input type="checkbox">`; Pseudo-Elemente darauf sind
browserabhängig, weshalb native Checkbox und eigener Knopf gleichzeitig
erscheinen konnten. Der echte, zugängliche Checkbox-Input ist nun visuell
geclippt, während Track/Knopf auf einem direkt folgenden `span` liegen.
Chromium-Messung: Input 1×1 px + `clip-path: inset(50%)`, Track 36×22 px,
Knopf 16×16 px; Screenshot zeigt genau einen sauberen Switch.

**Historische Einordnung:** Das ist ein reines CSS-Rendering-Problem, das durch
Unit-/Komponententests (jsdom rendert kein echtes Box-Modell/keine echten
Pseudo-Elemente) nicht auffindbar ist — dafür ist ein echter Browser via
`dev.py` nötig, der in dieser Session nicht zur Verfügung stand. Verdächtig:
`appearance: none` auf `.toggleSwitch` (`library-v2-page.module.css`)
könnte von einer globaleren/spezifischeren Regel überschrieben werden
(Browser-Standard-Checkbox-Rendering + das eigene `::before`
möglicherweise gleichzeitig sichtbar), oder eine fehlende
Browser-Prefix-/Cascade-Reihenfolge lässt die native Checkbox-Box
durchscheinen. Muss live im Browser inspiziert (Devtools Computed Styles)
und korrigiert werden. Diese Browserprüfung ist nun erfolgt.

### 22.3 <a name="iss27-14"></a> iss27-14 — Kein progressives Rendering / unklares Timeout-Verhalten bei langsamer oder nicht-antwortender Quelle — Verified

**Symptom/Frage des Nutzers:** Was passiert, wenn eine Quelle nicht
antwortet? Gibt es ein Timeout? Soulseek braucht/queued teils auch lange.

**Ist-Zustand (recherchiert, noch nicht geändert):** Jede einzelne
`searchSources()`-Anfrage hat ein 90s-Client-Timeout (`ky`,
`-library-v2.api.ts::searchSources`). Im Multi-Source-Fall wartet
`Promise.allSettled(...)` in `run()` aber auf **alle** Quellen, bevor
überhaupt irgendein Ergebnis gerendert wird — eine einzelne hängende
Quelle (z.B. ein langsam antwortendes/gequeutes Soulseek) verzögert damit
die Anzeige bereits fertiger Ergebnisse anderer, schnellerer Quellen
(z.B. Usenet) um bis zu 90 Sekunden, obwohl deren Antwort längst da wäre.
Ein echter Ausfall (Timeout/Fehler) wird zwar sauber toleriert (die
Quelle landet in `failed`, die anderen Ergebnisse werden trotzdem
angezeigt — das ist die iss27-01-Fan-out-Garantie), aber die WARTEZEIT bis
dahin blockiert die gesamte Anzeige.

**Fix:** Ergebnisse werden pro Quelle inkrementell in die Tabelle gemerged.
Solange weitere Quellen laufen, zeigt ein Status die laufende Suche; der
90s-Timeout bleibt pro Quelle erhalten. Ein Run-Sequence-Guard verwirft späte
Antworten einer überholten Suche. Der Regressionstest hält Soulseek künstlich
offen und belegt, dass das fertige Usenet-Ergebnis vorher sichtbar wird.

### Einstufung (§22)

Alle drei Live-Findings sind behoben. Abdeckung: 17 Interactive-Search-
Komponententests, Frontend-Type/Lint/Build und echter Chromium-Render.

## 23. Neu heruntergeladener Track eines bereits gut gemappten Albums hat nur eine Metadaten-Quelle — Verified, 27. Juli 2026

**Symptom/Szenario des Nutzers:** Ein Album ist bei fast allen
Metadaten-Quellen gematcht, ebenso der Artist — das Album ist aber nicht
vollständig (ein Track fehlt). Der Nutzer löst für den fehlenden Track
Automatic oder Interactive Search aus, der Track wird heruntergeladen —
bis hierhin alles gut. Danach muss er manuell „Refresh & Scan" auslösen,
damit die Datei überhaupt erkannt wird. Das eigentliche Problem: der neu
heruntergeladene Track hat danach nur **eine** Metadaten-Quelle
hinterlegt, obwohl Album und Artist bei deutlich mehr Quellen gemappt
sind. Frage des Nutzers: Wie kann das passieren? Gibt es nicht schon ein
Werkzeug, das die Daten vom Album/Artist übernehmen bzw. die anderen
Quellen nachmappen kann? Falls ja, sollte es nach dem Download für genau
diesen einen Track automatisch ausgelöst werden.

**Abschlussdiagnose:** Die offene Kernfrage ist bestätigt:
`fetch_album_tracklist()` probiert explizite Album-Provider-IDs in
Prioritätsreihenfolge, gibt aber nach der **ersten** erfolgreichen Trackliste
zurück. `_persist_tracklist_tracks()` konnte deshalb nur diese eine
Provider-ID pro Track speichern. Album und Artist konnten unabhängig davon
reichhaltig gemappt sein. Es gab keinen bestehenden albumweiten
Multi-Provider-Track-Reconcile.

Die zweite Hälfte des Symptoms war unabhängig davon: Autolink materialisiert
die neue Datei bereits vor dem Import-History-Event in `lib2_track_files`.
Album-/Artist-Queries wurden nach dem Import aber nicht invalidiert, weshalb
die UI bis „Refresh & Scan" den alten `missing`-Stand zeigen konnte.

**Umgesetzte Korrektur:**

- `fetch_matched_album_tracklists()` lädt ausschließlich Tracklisten der
  bereits explizit bestätigten Album-IDs; keine Namenssuche, kein Erraten
  einer Edition.
- `reconcile_album_track_provider_ids()` ordnet konservativ per vorhandener
  Provider-ID, Titel+Disc/Position oder beidseitig eindeutigem Titel zu,
  merged Provider-ID/ISRC/MBID und bewahrt Konflikte.
- `track_reconcile_trigger` führt das albumweit fünf Sekunden entprellt nach
  normalem Import und Post-Move-Recovery aus.
- Imported-History und Queue aktiv→leer invalidieren die Library-v2-
  React-Queries, sodass die autogelinkte Datei ohne manuellen Scan erscheint.

**Historischer Recherche-Zwischenstand:**

- Provider-IDs werden pro Entität in einer `external_ids`-JSON-Spalte
  gehalten (`lib2_artists`, `lib2_albums`, `lib2_tracks` —
  `core/library2/schema.py`); `spotify_id`/`musicbrainz_id` haben
  zusätzlich eigene Spalten. Die im UI sichtbaren „Match Provider"-Chips
  (deezer/itunes/discogs/audiodb/lastfm/genius/tidal/qobuz/amazon/
  jiosaavn/bandcamp/…) lesen aus genau diesem JSON-Feld.
- `core/library2/autolink.py::_find_or_create_track` (der Pfad, über den
  ein fertig heruntergeladener Track in die Library gelinkt wird) versucht
  aktiv, eine BESTEHENDE Track-Zeile wiederzuverwenden (erst per
  Provider-ID-Abgleich, dann per `dedup_title_key`-Titelabgleich) und
  merged beim Treffer nur die EINE neu bekannte Provider-ID zusätzlich
  rein (`_adopt_external_id`), statt bestehende IDs zu überschreiben — das
  ist grundsätzlich der richtige Mechanismus, WENN die bestehende
  Track-Zeile (aus dem ursprünglichen Discography-Fetch) bereits eine
  reichhaltige `external_ids`-Map hätte.
- **Offene Kernfrage, noch nicht verifiziert:** Bekommen einzelne
  Track-Zeilen beim initialen Discography-Fetch (dem Vorgang, der Album +
  Artist gegen „fast alle Quellen" matcht) überhaupt schon eine
  Multi-Provider-`external_ids`-Map, oder wird dort nur eine flache
  Tracklist aus EINER Quelle (z.B. Spotify) angelegt, während die
  reichhaltige Multi-Provider-Anreicherung nur auf Album-/Artist-Ebene
  läuft? Falls letzteres zutrifft, wäre das der eigentliche Root Cause —
  nicht ein Bug im Re-Use-Mechanismus selbst, sondern eine fehlende
  Track-Ebene in der bestehenden Anreicherungs-Pipeline. Muss in
  `core/library2/discography.py` (oder wo auch immer der initiale
  Album-Track-Import passiert) nachvollzogen werden.
- **Vergleichbares Werkzeug existiert für Artists, aber (soweit bisher
  gesehen) NICHT für Tracks/Alben:** `core/library2/native_enrich.py::
  reconcile_unmapped_native_artists` reconciled unabgeglichene ARTIST-IDs
  und wird bereits automatisch nach jedem Import angestoßen
  (`core/library2/unmapped_trigger.py`, siehe §28 Umsetzung, Commit
  `f7303866c`) — aber nur für Artists, nicht für Alben oder einzelne
  Tracks. `core/metadata/album_tracks.py` liefert bereits den Baustein,
  der pro Provider die volle Tracklist eines Albums holen kann (wird u.a.
  von `reconcile_unmapped_native_artists` und vom Tag-Gap-Fill-Endpunkt
  aus [iss27-02](library-v2-issues.md#iss27-02) genutzt) — ob es bereits
  einen HÖHERWERTIGEN Job gibt, der damit gezielt fehlende Track-Provider-IDs
  für ein bereits gematchtes Album nachzieht, konnte in der verfügbaren
  Zeit nicht abschließend verifiziert werden.

### Einstufung

Verified. Gezielte Provider-/Reconcile-/Trigger-Tests, 49 Importtests,
1.075 Library-v2-Tests und der §35-Frontend-Cachetest sind grün.

## 24. Python 3.14.6: gemeinsamer Async-Loop wacht bei Torrent-/Usenet-Aufrufen nicht auf — Verified, 27. Juli 2026

**Symptom:** Candidate-Store- und Torrent-/Usenet-Tests liefen bis zum ersten
`utils.async_helpers.run_async()` und blockierten dann unbegrenzt. Eine
isolierte Reproduktion mit `run_async(asyncio.sleep(0, result=42))` hing
ebenfalls; Faulthandler zeigte den Loop dauerhaft in `selector.select()` und
den Aufrufer in `Future.result()`.

**Root Cause:** Der Selector-Loop wurde im aufrufenden Thread durch
`asyncio.new_event_loop()` erzeugt, danach in einem separaten Thread per
`run_forever()` betrieben. Unter Python 3.14.6 konnte
`run_coroutine_threadsafe()` diesen fremderzeugten Loop in längeren Prozessen
nicht zuverlässig wecken.

**Fix:** Erzeugung und Betrieb erfolgen nun im selben Besitzer-Thread. Jobs
gelangen über eine threadsichere Queue zu einem Loop-Pump, der sie als
getrennte Tasks startet. Das umgeht den verlorenen Cross-Thread-Selector-
Wakeup und erhält die notwendige Parallelität (kein serieller Head-of-Line-
Block). Eine Active-Task-Menge hält zudem eine starke Referenz, solange ein
Job auf I/O wartet; ein GC-Lauf kann ihn daher nicht verwerfen und den
synchronen Aufrufer dauerhaft in `Future.result()` lassen.

**Verifikation:** Async-Bridge 3/3 (Start, Parallelität, Task-Lebenszyklus),
Candidate Store 15/15 und
Torrent-/Usenet-Plugins 51/51 bestanden und beendeten den Prozess sauber.

## 25. Abschluss-Audit: Identitätsgrenzen, Provider-Exaktheit und langlebige Async-Pfade — Verified, 27. Juli 2026

Der abschließende Code-, Lint- und Regressionstest-Audit hat mehrere
voneinander unabhängige Randfälle gefunden, die in kleinen Tests unauffällig
blieben, in realen Bibliotheken oder langlebigen Python-3.14-Prozessen aber
falsche Ergebnisse bzw. einen hängenden Prozess erzeugen konnten.

### 25.1 Versionssymmetrie bei Tracktiteln

**Symptom:** `Track (Producer Remix)` und `Track - Producer Remix` wurden
nicht immer gleich normalisiert. Der Klammerpfad entfernte den gesamten
Qualifier, während der Dash-Pfad nur wenige exakt ausgeschriebene Suffixe
erkannte. Varianten wie `Don Diablo Edit`, `super slowed`,
`Slowed + Reverb`, `Instrumental`, `Vocal`, `Clean`, `Explicit` oder
`Original Mix` konnten deshalb trotz gleicher Aufnahme in der
Audio-Verifikation auseinanderlaufen.

**Fix:** `core.text.title_match` stellt eine gemeinsame
`is_version_qualifier()`-Prüfung bereit. Die Audio-Verifikation verwendet
dieselbe Markersemantik für Klammer- und Dash-Formen. Gewöhnliche
Bindestrich-Titel und nicht versionsbezogene Dash-Zusätze bleiben erhalten.

### 25.2 Exakte Provider-ID-Auflösung

**Symptom:** Der Spotify-Adapter probierte zunächst
`getter(id, allow_fallback=False)`, fing aber jedes `TypeError` ab und rief
danach `getter(id)` erneut auf. Ein **innerhalb** des Providers ausgelöstes
`TypeError` wurde dadurch fälschlich als alte Methodensignatur interpretiert;
der zweite Aufruf konnte die verbotene Namens-Fallbacksuche aktivieren.

**Fix:** Die Signatur wird vor dem Aufruf geprüft. Nur ein nachweislich
unterstützter Parameter wird übergeben; nicht inspizierbare Spotify-Getter
schlagen bewusst geschlossen fehl. Providerfehler führen nicht mehr zu einem
zweiten, weniger exakten Aufruf.

### 25.3 Deterministische Artist-Bildpriorität

**Symptom:** Parallel geladene Bildquellen wurden über das ungeordnete
`done`-Set von `concurrent.futures.wait()` ausgewertet. Lieferten zwei
Quellen dieselbe URL, konnte die schnellere Fallbackquelle statt der
konfigurierten bevorzugten Quelle als Gewinner gespeichert werden.

**Fix:** Die Requests bleiben parallel, ihre Resultate werden nach Abschluss
aber in der konfigurierten Quellenreihenfolge ausgewertet. Laufzeit-Timing
ändert damit weder Priorität noch Attribution.

### 25.4 Server-seitige Torrent-Fetches und Prozess-Shutdown

**Symptom:** Der eigentliche HTTP-Fetch war erfolgreich, doch
`asyncio.run()` konnte unter Python 3.14.6 beim Schließen des impliziten
Default-Executors hängen. Das zeigte sich erst im längeren Testprozess nach
der bereits in §24 korrigierten gemeinsamen Async-Loop-Problematik.

**Fix:** Blockierende Torrent-Fetches laufen in einem kleinen,
prozessweiten Executor. Der Besitzer-Loop pollt das
`concurrent.futures.Future`, statt seinen Default-Executor oder einen
Cross-Thread-Loop-Wakeup zu benötigen. Ein Regressionstest prüft zusätzlich,
dass der Loop keinen Default-Executor anlegt.

### 25.5 Wishlist-Retry-Identität

**Symptom:** Die Fehlerzählung verstand nur historische bare Track-IDs. Bei
der kanonischen Identität `track_id::album_id` konnte ein Fehlversuch
ignoriert oder dem falschen Release zugeordnet werden.

**Fix:** Ein exakter Composite-Key aktualisiert ausschließlich seine
Wishlist-Zeile. Ein alter bare Key bleibt als kompatibler Wildcard erhalten
und aktualisiert die bare sowie alle zugehörigen Composite-Zeilen.

### 25.6 Weitere gefundene Härtungen

- Vier native Repair-Jobs liefern `artist_id` nun auch top-level neben dem
  Thumbnail, sodass Findings exakt zur Artist-Detailseite navigieren.
- Die doppelt definierte qBittorrent-`set_share_limits`-Implementierung wurde
  entfernt; ein Adaptertest schützt die WebUI-API-Felder und
  Minutenumrechnung.
- Ein Closure-Capture im Monitor-Sync, ein nicht-striktes `zip()` sowie
  mehrere stumme Exception-Pfade wurden durch explizite Helfer,
  `strict=True` und Debug-Logging gehärtet.
- Zwei Testfehler wurden als Test-Infrastrukturfehler behoben: ein
  wall-clock-abhängiger Expiry-Test und ein global-state-/timingabhängiger
  Async-Task-Lebenszyklustest.

### Verifikation

- Library-v2-Vollsuite: **1.078 passed**;
- langer Backendlauf vor den letzten zwei Testhärtungen:
  **12.285 passed, 3 skipped, 2 deselected, 2 failed**; die beiden einzigen
  Fehler waren exakt Wishlist-Retry und Async-Task-Lebenszyklus;
- nach den Fixes: Wishlist **51 passed**, Async-/Candidate-/Torrent-Scope
  **79 passed**, Titelmatching **31 passed** sowie alle weiteren betroffenen
  gezielten Suites grün;
- WebUI: **301 passed** in 50 Dateien, Check und Production Build grün;
- Ruff und `git diff --check` grün.

Auf ausdrücklichen Wunsch wurde der etwa zehnminütige Backend-Komplettlauf
nach den beiden isolierten Fixes nicht redundant wiederholt.

## 26. Library-V2-Live-UI-Findings vom 28. Juli 2026

Die folgenden Punkte stammen aus einem erneuten realen Nutzerlauf. Sie öffnen
Teile von UI-03, UI-05, F-13 und F-16 wieder, die in status.md §37 anhand von
Komponenten-/Buildtests als „Verified" geführt wurden. Die Tests belegten den
damaligen Codevertrag, nicht dessen Bedienbarkeit in unterschiedlich breiten
Browserfenstern oder in einer eingeklappten Single-/Ein-Track-Kachel.

### <a name="iss28-01"></a> iss28-01 — AcoustID Check hat keine eigene Track-Spalte

**Symptom:** Die konkrete AcoustID-Prüfung ist im Quality-Bereich bzw. in der
allgemeinen Verification-Darstellung versteckt. Ein Nutzer kann den
AcoustID-Lauf nicht als eigenständige Tabellendimension auswählen und auf
einen Blick von Human-/Force-Verifikation unterscheiden.

**Root Cause:** Der Payload liefert `track.file.acoustid_status` und
`pipeline_result.acoustid_message` bereits separat. `track_table.columns`
kennt jedoch nur `verification`; zusätzlich rendert die Quality-Zelle
denselben `TrackVerificationBadge` nochmals.

**Fix-Vertrag (Statusnomenklatur durch Nutzerfeedback präzisiert):** Eigene
Preference-/Order-Spalte `acoustid`, standardmäßig sichtbar, mit dem
nutzersichtbaren generischen Titel `Check`. Sie verdichtet den effektiven
Prüfweg mit folgender Priorität: `human_verified` → **Human verified** (blau),
AcoustID `pass` → **Verified**, AcoustID `skip` bzw. Force-/Retry-Bypass →
**Skipped**, sonst → **Not scanned**. Ein vorhandener
`pipeline_result.acoustid_message` bleibt im Tooltip als Grund sichtbar.
`verification` bleibt eine getrennte, opt-in Provenienzspalte; die
Quality-Zelle enthält keine Verification-Dopplung.

### <a name="iss28-02"></a> iss28-02 — Pixelbreiten machen Trackspalten blockierend und erzeugen horizontalen Scroll

**Symptom:** Eine Spalte lässt sich zunächst verschieben, später aber nicht
mehr intuitiv zurückschieben. Teilweise muss eine andere Spalte verändert
werden, um wieder Platz zu erhalten. Durch Dragging kann ein horizontaler
Scrollbalken an Kachel bzw. Seite erscheinen.

**Root Cause:** Jede Headerzelle speichert eine unabhängige CSS-Pixelbreite
und setzt gleichzeitig `width`, `minWidth` und `maxWidth`. Die Summe dieser
lokalen Minima ist nicht an die Containerbreite gekoppelt.
`.trackTableWrap { overflow-x: auto }` macht die Überbreite anschließend
sichtbar, statt die Breitenverteilung zu erhalten.

**Fix-Vertrag:** Ein Drag verändert die relative Breite zweier benachbarter
Datenspalten. Alle sichtbaren Spaltengewichte bleiben normalisiert, die
Tabelle verwendet stets exakt die verfügbare Breite und reagiert auf
Browser-Resize ohne gespeicherte Pixel-Falle. Kein horizontaler Scrollbalken
an Seite, Albumkachel oder Tabelle; lange Inhalte werden umgebrochen oder
gekürzt. Pointer-, Keyboard- und Reset-Bedienung bleiben erhalten.

### <a name="iss28-03"></a> iss28-03 — Globale Startseitenaktion ist falsch benannt und formatiert

**Symptom:** Der auf der Library-Startseite ergänzte Block „Automatic Search"
wirkt visuell wie ein fremder, schlecht formatierter Zusatz.

**Fix-Vertrag (korrigiert durch Nutzerfeedback):** Die kompakte
nutzersichtbare Aktion heißt `Automatic Search`. Sie verwendet denselben
neutralen Basis-Buttonstil wie der direkt benachbarte Re-Import-Button und
ergänzt nur das Lupe-Symbol; kein abweichender Primary-/Sonderblock. Der
bestehende Missing-/Upgrade-Ablauf bleibt erhalten. Laufstatus darf die
Headerzeile nicht mit einem unkontrollierten breiten Inline-Block sprengen.

### <a name="iss28-04"></a> iss28-04 — Album-/EP-/Single-Zeilen verbergen ihre Größe

**Symptom:** Die aggregierte Größe ist auf Detail-/Artist-Ebene vorhanden,
fehlt aber an den eingeklappten Album-, EP- und Single-Zeilen.

**Root Cause:** `LibraryV2AlbumSummary.total_size_bytes` wird bereits
geliefert; `AlbumBlock` rendert daraus kein Badge.

**Fix-Vertrag (Farbkorrektur durch Nutzerfeedback):** Jede Release-Zeile zeigt
im Standardzustand ein Größen-Badge mit Symbol und formatiertem Wert. `0 B`
ist ein ehrlicher Wert und wird ebenfalls gezeigt. Die Anzeige ist neutral
grau wie die Größenanzeige beim Artist; Größe ist kein grüner Erfolgsstatus.

### <a name="iss28-05"></a> iss28-05 — Spalteneinstellungen werden in kleinen Release-Kacheln abgeschnitten

**Symptom:** Bei eingeklappten Albums/Singles oder Releases mit nur einem
Track ist der untere Teil des Einstellungsfensters nicht erreichbar.
„Match Providers" ragt seitlich über die Kachel; zum Teil entsteht
horizontaler Scroll.

**Root Cause:** Das absolut positionierte Optionsmenü lebt innerhalb von
`.trackTableWrap`, demselben Container mit Overflow-/Tabellen-Clipping. Sein
zweispaltiges Mindestlayout (`260px + 300px`) ist breiter als schmale
Kacheln, und seine erreichbare Höhe hängt faktisch vom Release-Inhalt ab.

**Fix-Vertrag:** Viewport-gebundenes Dialogfenster außerhalb der
Tabellen-Overflow-Grenze. Der Inhalt darf mehr Höhe und vertikalen Scroll
verwenden, besitzt aber niemals horizontalen Scroll. Sections und
Provider-Checkboxen brechen responsiv auf eine Spalte um; Footer/letzte
Option bleiben auch bei einem einzigen Track erreichbar.

### <a name="iss28-06"></a> iss28-06 — AcoustID-Auslagerung zerstört das interne Quality-Raster

**Symptom:** Nach der Aufteilung von AcoustID und Verification wirken Format,
Bitrate und Quality Profile in der Quality-Spalte zusammengedrückt und
linksbündig. Zuvor hatte jeder Wert einen reservierten, zentrierten Bereich,
wodurch die Anzeigen zeilenübergreifend ruhig ausgerichtet waren.

**Root Cause:** Beim Umbau auf relative Tabellenbreiten wurden die vorhandenen
internen Breiten `140px` (Format/Resolution), `80px` (Bitrate) und `110px`
(Profile) zusammen mit der überflüssigen Verification-Unterzelle entfernt.
Die AcoustID-Trennung erforderte nur das Entfernen der vierten Unterzelle,
nicht die Auflösung des übrigen Quality-Rasters.

**Fix-Vertrag:** Format/Resolution, Bitrate und Quality Profile erhalten ihre
vorherigen reservierten und zentrierten Bereiche zurück. Die Quality-Spalte
bekommt genügend relatives Standardgewicht. In schmalen Browserfenstern
dürfen diese drei Bereiche kontrolliert auf weitere Tabellenzeilen umbrechen
oder bis zur Zellbreite schrumpfen; sie dürfen weder einen horizontalen
Scrollbalken erzeugen noch AcoustID/Verification wieder in Quality einbauen.

### <a name="iss28-07"></a> iss28-07 — Actions-Spalte reserviert unnötig viel Leerraum

**Symptom:** Rechts neben den drei Track-Aktionsicons bleibt auf breiten
Browserfenstern auffällig viel ungenutzter Raum.

**Root Cause:** Das neue `colgroup` reserviert pauschal 11 % für Actions;
zusätzlich erbt der Header weiterhin `.colActions { width: 170px }` aus
anderen Tabellen. Beides ist für drei feste Iconbuttons deutlich zu groß.

**Fix-Vertrag:** Die Tracktabelle erhält einen eigenen kompakten
Actions-Vertrag: 7 % statt 11 %, ohne geerbte 170-px-Breite, mit engerem
zellspezifischem Padding und kompakteren Track-Action-Icons. Die frei
werdenden vier Prozent gehen an die relativ berechneten Datenspalten
(86 %). Alle drei Aktionen bleiben sichtbar und bedienbar; die Tabelle bleibt
bei exakt 100 % und erzeugt keinen horizontalen Scroll.

---

## 27. Finaler Multi-Agent Deep-Dive vor dem PR-Entwurf (28. Juli 2026)

Ausdrücklicher Nutzerauftrag: ein letzter, sehr breiter Bug-Run über die
gesamte `library-overhaul`-Branch, bevor aus dem Draft ein PR wird — mit
explizitem Fokus darauf, dass die kürzlich auf `lib2_*` umgehängten Werkzeuge
(Cover Art Filler, ReplayGain Filler, Metadata Gap Filler, etc.) korrekt
miteinander und mit der übrigen Pipeline zusammenspielen, und dass keine
bestehende Funktionalität verloren geht. Sechs parallele, read-only Agenten
haben je eine unabhängige Domäne durchleuchtet, ohne sich gegenseitig zu
kennen oder Dateien zu teilen:

- **A — Repair-Werkzeuge ↔ Library V2** (`core/repair_jobs/*`, `core/repair_worker.py`):
  Nachlese zu §18/§19, mit Fokus auf die dort explizit ausgesparten
  Fehlerinjektionsszenarien (Crash mid-apply, Multi-File, read-only Root,
  Alias-Gruppen, Multi-Edition, Cross-Job-Races).
- **B — Interactive Search / Multi-Source-Query-Building**: konkreter
  Nutzerbefund — sehr lange Titel und Titel mit Klammern/Sonderzeichen
  scheitern bei manchen Quellen, insbesondere Usenet.
- **C — Artist-/Album-Artwork-Pipeline**: konkreter Nutzerbefund — Foto-Wechsel
  eines Artists funktioniert meistens, aber gelegentlich mit unklarem
  API-Fehler.
- **D — Import-Pipeline & Tag-/Metadata-Schreiben**: Nachlese zur bereits
  behobenen §23/§35-Bugklasse ("nur eine Metadaten-Quelle nach Nachimport").
- **E — Monitoring/Wanted/Watchlist-Wishlist-Reconcile**: Invarianten aus
  Guide §2.2/§2.3/§5 (ADR-02, Quality-Kaskade, Outbox/Reconciler).
- **F — Frontend-Async-State & Download-Client-Adoption**: React-Query-
  Cache-Konsistenz, Restart-Adoption (ADR-07), der in §36 neu geschriebene
  Async-Loop-Pump.

**Ursprünglicher Vorbehalt (28. Juli, vormittags):** Alle folgenden Funde
waren Ergebnisse einer reinen Codeanalyse durch LLM-Agenten — sie wurden
**nicht** durch Tests, Reproduktion oder einen Live-Lauf verifiziert. Guide §6
Punkt 3 gilt uneingeschränkt: vor jedem Fix ein isoliertes
Reproduktionsszenario oder einen Regressionstest herstellen. Ein Teil der
Funde widerspricht zuvor als „abgeschlossen" dokumentierten Ständen (siehe
insbesondere dd28-27, das denselben Identitäts-Bug-Typ wie das angeblich
geschlossene T-12 an einer weiteren Stelle findet) — auch das ist ein Hinweis,
frühere „Verified"-Einträge nicht blind zu vertrauen, sondern jeden Fund gegen
den aktuellen Code neu zu prüfen.

> **Nachtrag (28. Juli 2026, nachts) — abgearbeitet.** Alle 50 Funde wurden
> gegen den aktuellen Code nachgeprüft und behoben; **kein einziger erwies
> sich als Fehlalarm.** Zwei Funde wurden bewusst enger umgesetzt als
> vorgeschlagen (dd28-51, dd28-44), weil die wörtliche Umsetzung
> Hauptanwendungsfälle gebrochen hätte. Vier bestehende Tests pinnten die
> alte, falsche Semantik und wurden mit Begründung umgeschrieben.
> Bearbeitungsstand, Commits, Testdateien und beide Vorbehalte im Detail:
> [status.md §41](library-v2-status.md#41-multi-agent-deep-dive-vor-dem-pr-entwurf--status).
> Die Reihenfolge unten in §27.6 wurde eingehalten.

### 27.1 Kritisch

**dd28-01 (Domäne C) — Artwork-POST hat kein zum Backend passendes Timeout**
*Fundort:* `webui/src/routes/library-v2/-library-v2.api.ts:1501` (Artist),
`:1467` (Album); Backend `core/library2/artwork.py:947` (`apply_manual_artwork`).
*Szenario:* Der `apiClient` setzt nur `retry: 0`; kys Default-`timeout` bleibt
bei 10 000 ms. Das Backend hat dafür keine passende Grenze:
`_download_remote_artwork` erlaubt `(3.05, 15)` s pro Hop über bis zu 5
Redirects plus ein ungebremstes `socket.getaddrinfo`, danach zwei
`optimize=True`-JPEG-Encodes für Bilder bis 40 MP, einen DB-Write und den
Erwerb von `_build_lock`. Sobald das zusammen über 10 s dauert, bricht der
Client ab und zeigt einen rohen ky-Fehler — **während der Server die Änderung
trotzdem fertigstellt.** Die GET-`art-options`-Aufrufe wurden für exakt dieses
Problem bereits auf `timeout: 20_000` angehoben (iss27-03); der POST-Pfad hat
diese Behandlung nie bekommen.
*Warum kritisch:* Trifft den vom Nutzer geschilderten Fehler wortgleich —
„meistens funktioniert es, aber manchmal API-Fehler" ist exakt das erwartete
Verhalten dieses Timeout-Mismatches (Provider-CDN beim zweiten Versuch warm,
Encode schneller o.ä.).

**dd28-02 (Domäne B) — Prowlarr/Usenet-Suche hat ein hartes 15-s-Timeout und
scheitert lautlos als „0 Treffer"**
*Fundort:* `core/prowlarr_client.py:103` (`DEFAULT_TIMEOUT = 15`), abgefangen
in `core/prowlarr_client.py:252-254` → `None` → `core/download_plugins/usenet.py:373-375`
gibt `([], [])` zurück.
*Szenario:* Jeder Prowlarr-Aufruf über 15 s (normal, wenn mehrere
Usenet-Indexer gefächert abgefragt werden; lange/ungewöhnliche Queries sind
die langsamsten) liefert HTTP 200 mit `{"results": []}` — nicht
unterscheidbar von einer echten Nulltreffer-Suche. Soulseek bekommt 60 s+15 s
Puffer, das Frontend erlaubt 90 s, und die vorhandene Nutzer-Einstellung
`download_source.source_search_timeout` (`config/settings.py:1034`) ist bei
HiFi/Qobuz/Deezer/Stream verdrahtet, **aber nicht bei Prowlarr.**
*Warum kritisch:* Exakt der vom Nutzer geschilderte Befund „Usenet wird gar
nicht gecheckt". `usenet.py:358-372` und `torrent.py:157-171` nehmen zwar
einen `timeout`-Parameter gemäß `base.py`-Vertrag entgegen, reichen ihn aber
nie an `self._prowlarr.search(...)` weiter (siehe dd28-05) — die Grenze ist
also auch nicht durch Konfiguration umgehbar.

### 27.2 Hoch

**dd28-03 (C) — `set_field_override` führt bei jedem Aufruf unbedingte
Schema-DDL aus → SQLite-Lock eskaliert zu einem 500er**
`core/library2/metadata_overrides.py:130-151`. `ensure_metadata_overrides_schema`
führt `CREATE TABLE IF NOT EXISTS`, jeden `CREATE INDEX` sowie ein
`DROP TRIGGER`+`CREATE TRIGGER`-Paar **pro Entity-Tabelle bei jedem einzelnen
Aufruf** aus und nimmt dafür den SQLite-Schreiblock. Ein `database is locked`
nach Ablauf des 30-s-Busy-Timeouts (Import/Scan/Post-Processing läuft
parallel) wird von der Route (`api/library_v2.py:2340-2352`, fängt nur
`MetadataOverrideError`) nicht behandelt und landet als generischer HTML-500
beim Client. Zweitbester Treffer für „manchmal API-Fehler, Retry hilft".

**dd28-04 (C) — Offene Schreibtransaktion wird über einen netzwerkblockierenden
Lock gehalten**
`core/library2/artwork.py:978`, `:987`. `set_field_override` öffnet die
Schreibtransaktion; committet wird erst nach `apply_manual_artwork`
(`api/library_v2.py:2343`). Dazwischen kann `with _build_lock(...)` für die
gesamte Dauer eines parallel laufenden `build_artwork` blockieren — der hält
den Lock über einen sequenziellen, budgetlosen Provider-Walk
(`_provider_art_url` → `fetch_artwork_url`, `provider_adapters.py:890-911`)
plus Download plus zwei Encodes. Das Öffnen der Artist-Detailseite stößt
genau so einen Build für einen kalten Artist an, und der Picker wird von
derselben Seite geöffnet — die Kollision ist der Normalfall, nicht die
Ausnahme. Ergebnis: der DB-Schreiblock der gesamten App hängt an einem
Netzwerkaufruf und verlängert dd28-01s 10-s-Fenster zusätzlich.

**dd28-05 (B) — `timeout`-Parameter wird von beiden Prowlarr-Plugins
entgegengenommen und verworfen**
`core/download_plugins/usenet.py:358-372`, `torrent.py:157-171`. Nehmen
`timeout: Optional[int]` gemäß `base.py:74-79`-Vertrag entgegen und reichen
ihn nie an `self._prowlarr.search(...)` weiter — macht dd28-02
konfigurationsseitig unlösbar.

**dd28-06 (B) — Ein Usenet-Fehlschlag ist unsichtbar, sobald irgendeine andere
Quelle Treffer liefert**
`webui/src/routes/library-v2/-ui/interactive-search.tsx:492-507`.
Pro-Quelle-Fehler landen in `failed`, ein Fehler wird aber nur geworfen, wenn
`merged.length === 0 && failed.length > 0`. Liefert Soulseek irgendetwas,
erzeugt ein Usenet-500/-Timeout weder Banner noch Chip-Status noch sonst
etwas — bereits durch einen eigenen Test abgesichert
(`interactive-search.test.tsx:232-273`), aber als bewusstes Verhalten, nicht
als Bug erkannt. In Kombination mit dd28-02 kann Usenet jedes Mal scheitern,
ohne dass es je auffällt.

**dd28-07 (B) — Keine Query-Normalisierung für lange/verklammerte Titel auf
dem Prowlarr-Pfad (Usenet/Torrent)**
`core/download_plugins/usenet.py:366-372`. Sendet `buildSearchQuery`s Output
unverändert. Bei `"Drenchill Freed from Desire (feat. Indiiana) - DNF
Extended Remix"` oder jedem >100-Zeichen-Titel wird jedes Token zum
UND-verknüpften Suchbegriff, ein korrektes NZB mit Titel
`Drenchill-Freed_From_Desire-WEB-2019-FLAC` bekommt null Treffer. Zum
Vergleich: `core/tidal_download_client.py:579-622` versucht bereits bis zu 5
progressiv gekürzte Varianten genau aus diesem Grund. Es gibt **keinerlei**
Längenbehandlung (weder Client noch Server) und keinen
Klammer-Stripping-Fallback für Usenet/Torrent. Trifft exakt den vom Nutzer
gemeldeten Fall „lange Titel + Klammern funktionieren nicht bei manchen
Quellen".

**dd28-08 (D) — Quality-Upgrade/Redownload lässt eine veraltete *primäre*
`lib2_track_files`-Zeile auf eine gelöschte Datei zeigen**
`core/imports/pipeline.py:1616-1625` (Enhance), `:2041-2058`
(Redownload-Hook). Ein Enhance-Grab schreibt nach `<gleicher Stamm>.<neue
Endung>` (`core/imports/paths.py:537-544`) und löscht danach die
Originaldatei. Autolink schlüsselt über `(track_id, path)`
(`core/library2/autolink.py:604-607`) und **fügt eine neue Zeile ein**, ohne
die alte anzufassen. Der Insert-Trigger promotet die neue Zeile nur, wenn der
Track noch keine aktive Primärdatei hat (`core/library2/track_files.py:241-255`)
— die veraltete Zeile bleibt `is_primary=1`/`active`, obwohl die MP3 gelöscht
ist. Jeder lib2-Lesepfad (Retag, ReplayGain, Lyrics, Wishlist-Mirror,
Quality-Eval, Queue-Status) arbeitet danach mit einer nicht existenten Datei;
der Track zeigt zwei Dateien. Der Redownload-Hook hat dieselbe Form: löscht
`old_file_path`, aktualisiert aber nur die **Legacy**-`tracks.file_path`, nie
`lib2_track_files`. Nichts konvergiert das, bis der Nutzer manuell
„Refresh & Scan" auslöst — der wahrscheinlich häufigste einzelne
lib2-Schreibpfad überhaupt (Upgrades).

**dd28-09 (D) — Alias-verlinkter Artist bekommt beim Import ein doppeltes
Album**
`core/library2/autolink.py:202-224` (`_find_or_create_album`),
`core/library2/materialize.py:81-84` (derselbe Defekt im Pre-Download-Pfad).
Der Album-Lookup ist auf `lib2_album_artists WHERE aa.artist_id = <aufgelöster
Artist>` beschränkt und ruft nie `resolve_alias_group` auf, während
`_find_or_create_artist` (`autolink.py:124-192`) anstandslos die
*Alias*-Zeile auflöst. Ein Download, der auf die Rōmaji-Schreibweise
gebucht ist, während das Album unter der Kanji-Kanonik liegt, erzeugt eine
**zweite `lib2_albums`-Zeile** unter dem Alias — Artist-Seite zeigt das Album
doppelt, die Datei landet auf dem Duplikat, das Original bleibt dateilos und
monitored, wodurch die Wanted-Projektion es dauerhaft weiter zum Download
vorschlägt. Untergräbt genau den Zweck des Alias-Features (§40) und kann
Downloads in eine Schleife bringen.

**dd28-10 (D) — Neue Tracks bekommen keine Edition-Zeile; der Backfill pinnt
sie danach auf die Default-Edition**
`core/library2/autolink.py:303-323`, `core/library2/editions.py:387-403`.
Autolink erzeugt `lib2_tracks`, ruft aber nie `ensure_release_track` —
frisch importierte Tracks sind für jeden edition-scoped Konsumenten
unsichtbar (`core/acquisition/bundle_matching.py:193-199`,
`core/acquisition/catalog.py:60-73`). `backfill_editions` läuft nur beim
Schema-Ensure (Prozessstart) oder nach einem Legacy-Import und pinnt jeden
noch nicht zugeordneten Track dann unabhängig von der tatsächlich
heruntergeladenen Edition auf `default_edition_id(album_id)` — idempotent pro
`(edition, track)`, die Fehlzuordnung ist also dauerhaft.

**dd28-11 (E) — Upgrade-Scan liest eine veraltete denormalisierte
Profil-ID statt live aufzulösen**
`core/library2/wishlist_mirror.py:314` (`JOIN quality_profiles qp ON qp.id =
t.quality_profile_id`). Wechselt der Nutzer das app-weite Default-Profil
(z.B. von „Balanced"/`acceptable` auf „Upgrade until top"/`until_cutoff`,
`database/music_database.py:10090`), flippt das nur `is_default` — nichts
schreibt `lib2_tracks.quality_profile_id` neu, die beim Insert per
Schema-Trigger gesetzt wurde. `lib2_upgrade_scan`
(`core/repair_jobs/lib2_upgrade_scan.py:86`) und der manuelle Scan
(`api/library_v2.py:4038`) filtern also weiter nach der alten Policy und
liefern null Kandidaten, während `wanted_views.list_cutoff_unmet` (nutzt
live `w.effective_profile_id`) dieselben Tracks als Cutoff-Unmet listet —
zwei Systemteile widersprechen sich. Exakt die in Guide §2.3 explizit
verbotene Situation „ein später editiertes Profil bleibt durch alte
denormalisierte Wishlist-Flags wirkungslos".

**dd28-12 (E) — Legacy-Wishlist-Zugänge werden vom stündlichen Reconciler
lautlos gelöscht**
`core/library2/monitor_sync.py:806`,
`core/repair_jobs/monitoring_list_reconcile.py:29`
(`default_interval_hours=1`, `auto_fix=True`). Der Mirror ist asymmetrisch:
Wishlist-*Entfernen* hat eine Rückkante nach lib2 (`sync_wishlist_removal`),
Wishlist-*Hinzufügen* nicht — `api/wishlist.py:88`,
`core/wishlist/service.py:166` schreiben nur die Legacy-Tabelle. Ein Track,
der vom Nutzer, der API oder dem Failed-Download-Dialog hinzugefügt wird und
sich zwar auf eine lib2-Zeile abbilden lässt, aber keine lib2-Regel besitzt,
die ihn „wanted" macht, landet im `prune` und wird binnen einer Stunde per
`wishlist_remove` wieder entfernt. Fehlgeschlagene Downloads hören damit
lautlos auf, erneut versucht zu werden.

**dd28-13 (E) — Outbox spielt eine bereits überholte Operation nach einem
transienten Fehler erneut ab**
`core/library2/mirror_outbox.py:202-231`. `drain` iteriert Zeilen in
ID-Reihenfolge, stoppt aber nicht beim ersten Fehler. Scheitert Zeile 100
(`wishlist_add` für Track T) transient (DB gesperrt) und Zeile 101
(`wishlist_remove` für T) gelingt, bleibt Zeile 100 `pending` und wird beim
nächsten Drain erneut abgespielt — ein gerade vom Nutzer entfernter
Wishlist-Eintrag wird wiederbelebt. `retry_failed` (`:254`) verschärft das,
weil es jede alte `failed`-Zeile ohne Ordnungsprüfung auf `pending`,
`attempts=0` zurücksetzt.

**dd28-14 (F) — Usenet-Cancel markiert den Grab als CANCELLED, ohne den
Client je zu kontaktieren**
`core/download_plugins/usenet.py:788-800`. `cancel_confirmed` startet `True`;
die Client-Stufe steckt in `if adapter and job_id:`. Ist `job_id` `None`
(die In-Memory-`active_downloads`-Zeile ist weg — nach einem Neustart, nach
`clear_all_completed_downloads` oder nach einem früheren `remove=True`) oder
der Adapter unkonfiguriert, wird das SAB/NZBGet-Remove lautlos übersprungen
und `_update_grab(status=STATUS_CANCELLED)` läuft trotzdem. Der Rückgabewert
von `adapter.remove()` wird zusätzlich ignoriert (der zentrale Monitor macht
es an der Stelle korrekt: `removed = bool(run_async(...))`,
`client_monitor.py:579`). DB sagt „cancelled", der Client lädt weiter — und
weil „cancelled" terminal ist, adoptiert `_restore_open_grabs` diesen Job nie
wieder. Exakt der umgekehrte Fall des zweistufigen Cancel-Vertrags aus ADR-07,
lautlos und ohne manuelle Client-Bereinigung nicht mehr reparierbar.

**dd28-15 (F) — Restart-Adoption kann einen neuen Grab an einen alten,
bereits abgeschlossenen Client-Job binden**
`core/acquisition/client_monitor.py:169-206`. `unknown_jobs` (Zeile 227)
umfasst jeden Snapshot-Job der Acquisition-Kategorie ohne offenen Grab.
`SabnzbdAdapter._get_all_sync` (`core/usenet_clients/sabnzbd.py:224-236`)
schließt die **SAB-History** ein (`limit=50`) — abgeschlossene/fehlgeschlagene
Jobs früherer Grabs sind also Adoptionskandidaten, und der
`exact_title`-Zweig (Zeile 188) filtert weder nach Status noch nach Alter.
Ein Re-Grab desselben Releases (Quarantäne-Retry, fehlgeschlagener Import),
dessen Submission `submission_unknown` zurückgab, adoptiert damit den alten
History-Job; `reconcile_usenet_snapshot` sieht `state == "completed"` mit
`save_path` und ruft `record_download_completed(output_path=<alter Pfad>)` —
eine Phantom-Completion auf ein bereits konsumiertes/entferntes Verzeichnis,
ohne jeden Fehler.

**dd28-16 (F) — Scoped „Automatic Search" hat weder Run-Sequence-Guard noch
In-Flight-Sperre**
`webui/src/routes/library-v2/-ui/library-v2-page.tsx:4980-4987` (Artist),
`:4615-4625` (Album), Buttons `:5005-5009`, `:7409-7419`. `void
runScopedSearch(...).then(setGrabBanner)` schreibt in ein einziges geteiltes
`grabBanner`. Track-A-Suche gefolgt von Track-B-Suche: Track As langsameres
Endergebnis landet zuletzt und überschreibt Bs Banner — der Nutzer liest das
falsche Ergebnis (ein „ok" über einem echten Fehlschlag). Exakt die Klasse,
die für Interactive Search bereits gefixt wurde
(`interactive-search.tsx:409/475/483/509`, `runSequenceRef`), aber nie hier
oder bei `updateDiscography` (`:4929-4946`, schreibt dasselbe Banner)
angewendet. Zusätzlich sind die Buttons nicht disabled, solange eine Anfrage
läuft — ein Doppelklick doppelt-POSTet; der Server ist zwar idempotent
(`_job_registry.start` → 409), aber der Client rendert den 409 als
„Search failed: …" — ein falscher Fehler über einer tatsächlich laufenden
Suche.

**dd28-17 (F) — `run_async` hat kein Timeout und blockiert den
Monitor-Zykluslock vollständig**
`utils/async_helpers.py:98-111`, `core/acquisition/client_monitor.py:559-586`,
`:641-645`. `future.result()` wartet unbegrenzt. Der Monitor-Thread ruft es
auf, während er `_cycle_lock` (Zeile 546) hält — für
`collect_usenet_snapshot`, jedes `adapter.remove`, und via
`persistent_reconciler_runner` → `_acquisition_client_observations()` auch
für slskd. Ein einziger hängender Client-Aufruf stoppt dauerhaft die
Usenet-Reconciliation, die Import-Pipeline und das Fertigwerden von Cancels;
`status()` meldet weiterhin `running: True, last_error: None` — ein
lautloser Totalstillstand der Persistenzschicht.

**dd28-18 (A) — Multi-Edition-Release-Gruppen korrumpieren
Tracknummer-/Total-Writes**
`core/repair_jobs/native_p3.py:87-93`, `core/repair_jobs/track_number_repair.py:881`.
`canonical_by_album[album_id]` wird aus **allen** `lib2_tracks`-Zeilen der
Release-*Gruppe* gebaut, ohne Edition-Filter — `lib2_albums` ist aber die
Gruppe, die konkrete Nummerierung pro Edition liegt in `lib2_release_tracks`.
Bei Standard- + Deluxe-Pressung ist `api_tracks` die Vereinigung beider
Tracklisten; `disc_total` zählt dann z.B. 28 statt 12, und
`_fix_track_number_tag` schreibt `N/28` in jede Datei. Doppelte Titel über
Editionen hinweg lassen `_match_title_to_api_track` zusätzlich eine beliebige
Editions-Tracknummer wählen. Mit `dry_run: False` werden dabei auch Dateien
umbenannt — unreviewed, deterministisch falsch, auf eine ganze Albumklasse
angewendet.

**dd28-19 (A) — Nicht erreichbarer Storage wird als erfolgreiches Löschen
gemeldet**
`core/repair_worker.py:1990-1999`, `core/library2/maintenance_sync.py:532-556`.
`_remove_native_repair_file` gibt bei nicht auflösbarem Pfad `{'success':
True, 'deleted_file': False}` zurück. Die drei Aufrufer
(`_fix_short_preview_track`, `_fix_corrupt_audio`, `_fix_unwanted_content`,
plus AcoustID-Delete) melden daraufhin unbedingt
`library_v2_file_deleted: True`, `sync_repair_change` setzt `file_state=
'deleted'` und kippt Monitoring/Wishlist. Auf einem ungemounteten NAS oder
bei einem Path-Mapping-Miss löscht das Bestätigen eines dieser Findings
lautlos eine Datei, die real noch existiert, und stößt einen Redownload an.
`dead_file_cleaner` schützt sich exakt dagegen
(`missing_path_root_is_healthy`) — die löschenden Fixes tun es nicht.

**dd28-20 (A) — AcoustID-„Retag" entleert einen Track ohne Wanted-Recompute**
`core/repair_worker.py:3132-3145`, Effekte in
`core/repair_jobs/__init__.py:70`. Das native Retag hängt
`lib2_track_files.track_id` auf einen neu erzeugten Track um; der
ursprüngliche (erwartete) Track bleibt ohne jede Datei. `acoustid_scanner`s
deklarierte Effekte sind `{'observe','tags','metadata'}` — kein `'wanted'` —
und `'retagged'` ist weder in `_DELETE_ACTIONS` noch setzt es
`repair_intent`, also ruft `maintenance_sync.py:588` nie
`recompute_wanted`. Der entleerte Track wird nie als fehlend/wanted
projiziert — das Album liest sich lautlos als vollständig, während ein Track
keine Datei mehr hat.

### 27.3 Mittel

| ID | Domäne | Fundort | Kurzbeschreibung |
|---|---|---|---|
| dd28-22 | C | `art-picker-modal.tsx:147-150` | Re-Pick nach einem Timeout kann die gepinnte Override-URL vom gecachten Bild entkoppeln (Doppelklick-Schutz wird im `catch` zu früh wieder aufgehoben; kein UI-Pfad setzt `force=1`). |
| dd28-23 | C | `art-picker-modal.tsx:140-151` | Ein am Client getimeouteter, serverseitig aber erfolgreicher Apply zeigt „failed", obwohl das Foto bereits gewechselt wurde — kein Query-Invalidate im `catch`-Zweig. |
| dd28-24 | C | `core/library2/artwork.py:367,383`, aufgerufen aus `api/library_v2.py:2101,2133` | Zwei parallele Thumbnail-Requests für denselben Artist schreiben unlocked in denselben `_t.tmp`-Pfad; `is_cached_jpeg` prüft nur die ersten drei Magic-Bytes, ein interleaved Write besteht die Prüfung dauerhaft. |
| dd28-25 | C | `api/library_v2.py:2246-2250` | Album-Art-Route ist weniger gehärtet als die Artist-Route: `retag`/`_job_registry.start` laufen nach dem bereits committeten Pick ungeschützt; jede Exception dort liefert einen 500 für eine Operation, die bereits erfolgreich war. |
| dd28-26 | C | `api/library_v2.py:3479-3490` vs. `core/library2/artwork.py:174-196` | `_delete_artwork_files` ruft nie `forget_artwork_versions` — eine wiederverwendete Entity-ID kann mit `?v=`-Token auf gelöschte Kunst zeigen (7 Tage `immutable`-Header). |
| dd28-27 | A | `core/repair_jobs/fake_lossless_detector.py:150-151` → `core/library2/maintenance_sync.py:188-197` | Finding trägt eine Track-ID unter `entity_type='file'`; `_resolve_links` liest sie als File-ID (ID-Räume überlappen) — dieselbe Bugklasse wie das angeblich geschlossene T-12, heute nur report-only, wird destruktiv, sobald `fake_lossless` einen Fix-Handler bekommt. |
| dd28-28 | A | `core/repair_worker.py:3660-3664` (Erfolg bei 3677) | `path_mismatch`-Fix verschluckt einen fehlgeschlagenen DB-Update mit `except Exception: logger.debug`, meldet aber trotzdem Erfolg — die Datei ist verschoben, `lib2_track_files.path` zeigt noch auf die alte Stelle, `path_drift_reconcile` kann das nicht mehr selbst heilen. |
| dd28-29 | A | `core/repair_jobs/native_p3.py:165-196` | In-Scan-Auto-Fix-Rename hat kein Rollback: schlägt der separate DB-Write nach dem physischen Rename fehl, bleibt die Datei umbenannt, der Katalog zeigt den alten Pfad, `report_change` wird übersprungen. |
| dd28-30 | A | `core/repair_worker.py:864-874`, `:907-932` | `reported_changes` ist In-Memory; bei einem Prozesstod während eines großen Auto-Fix-Laufs (z.B. Tracknummer `dry_run: False`) sind Dateimutationen/DB-Writes bereits committet, aber Rescan/Tag-Cache/Artwork-Invalidierung/History für den ganzen Lauf gehen verloren. `fix_finding` (Einzel-Fix-Pfad) macht das korrekt. |
| dd28-31 | A | `core/metadata/common.py:283` (`{path}.sstmp`), erreichbar über `repair_worker.py:3999-4002` vs. `:899` | Bulk-Fix-Thread und laufender Scan-Auto-Fix können gleichzeitig auf dieselbe Datei schreiben; beide teilen sich einen festen Temp-Dateinamen — der Verlierer fällt in den generischen `except`-Zweig zurück und landet beim nicht-atomaren In-Place-Save, den der atomare Save eigentlich ablösen sollte. |
| dd28-32 | A | `core/repair_worker.py:1807-1824` → `maintenance_sync.py:539-556` | „Tote Referenz entfernen" setzt `monitored=0` mit User-Provenienz für den ganzen Track, auch wenn eine zweite, intakte Datei (z.B. MP3 neben fehlender FLAC) weiterhin vorhanden ist. |
| dd28-33 | A | `core/repair_jobs/native_p3.py:481-491`, `core/repair_worker.py:2697-2698` | Cover-Fix schreibt pro Release-Gruppe nur in einen Ordner (`rep_path` = erste Datei); Mehrordner-Alben (CD1/CD2, getrennte Editionsordner) bleiben in den übrigen Ordnern dauerhaft ohne Sidecar, ohne dass das Finding erneut auftaucht. |
| dd28-34 | B | `core/prowlarr_client.py:52-57` | `DEFAULT_MUSIC_CATEGORIES` lässt 3060 (Audio/Foreign) aus — nicht-lateinische Releases (J-Pop/K-Pop) sind über Usenet grundsätzlich nicht auffindbar, während Soulseek (kein Kategoriefilter) sie findet. |
| dd28-35 | B | `library-v2-page.tsx:5563` + `build-search-query.ts:239-247` | Album-Level Interactive Search entfernt unbedingt eine trailing Klammergruppe — bei `"OK Computer (OKNOTOK 1997 2017)"` oder `"Definitely Maybe (Remastered)"` verschwindet die editionsrelevante Klammer aus der Query; Gegenteil des Track-Bugs, ungetestet. |
| dd28-36 | B | `build-search-query.ts` (`splitTrailingParenGroup`) + `interactive-search.tsx:474` | Ein komplett verklammerter Titel (`"( )"`, `"(Untitled)"`) kollabiert die Query auf den Artist-Namen oder — bei zusätzlich leerem Artist — auf gar nichts; `run()` bricht dann vor `setLoading(true)` ab, UI zeigt sofort „No results" ohne je eine Anfrage zu senden. |
| dd28-37 | B | `core/download_plugins/torrent.py:817-831`, genutzt von `usenet.py:367`/`torrent.py:166` | Ein einziges `prowlarr.indexer_ids`-Allowlist-Setting wird von Usenet und Torrent geteilt; mit Torrent-Indexer-IDs befüllt sucht Usenet dauerhaft nur Torrent-Indexer → 0 Treffer ohne jede Fehlermeldung. |
| dd28-38 | D | `core/library2/retag.py:50-59`, `replaygain.py:39,182`, `lyrics.py:70` | Tags/ReplayGain/Lyrics werden ausschließlich in die *primäre* Datei geschrieben (`primary_order(...) LIMIT 1`); eine sekundäre Datei (FLAC+MP3, oder ein Upgrade-Duplikat wie in dd28-08) bleibt unverändert, obwohl „Write Tags" Erfolg meldet. |
| dd28-39 | D | `core/imports/pipeline.py:1254-1256` vs. `:2001-2017` | `if not artist_context: return` setzt keine Rejection-Flags; der äußere Wrapper loggt „cannot verify, assuming success" und markiert den Task als Completed, obwohl die Datei unimportiert im Downloadordner liegt. |
| dd28-40 | D | `core/imports/pipeline.py:1660-1668` | `create_lossy_copy` überschreibt `_final_processed_path`; bei `delete_original=False` existieren beide Dateien auf Disk, aber lib2 kennt nur die MP3 — die FLAC liest sich als Orphan, Quality-/Cutoff-Bewertung nutzt dauerhaft die verlustbehaftete Kopie. |
| dd28-41 | E | `core/library2/mirror_outbox.py:66-68` | Wird ein Track wanted, aber `_should_queue=False`, passiert nichts — bleibt der Track wanted, ohne dass die Wishlist ihn je entfernt (z.B. Datei extern hinzugefügt oder Profilcutoff gesenkt), erzeugt das dauerhaft überflüssige Download-Versuche. |
| dd28-42 | E | `api/library_v2.py:2618,2662` | Profil-Reassignment ohne `monitor_existing` (Default-UI-Pfad) ändert `quality_profile_id`, ruft aber nie `recompute_wanted` — `lib2_wanted_tracks.effective_profile_id` bleibt bis zum nächsten stündlichen Voll-Recompute (oder für immer, falls der Job deaktiviert ist) auf dem alten Profil stehen. |
| dd28-43 | E | `core/library2/discography.py:138-159,177-178` | `_release_date_key` fällt bei fehlendem Tagesdatum auf `(Jahr,1,1)` zurück und behandelt ein reines Jahresdatum damit wie ein echtes Datum — `monitor_new_items='new'` monitort dadurch entgegen Guide §5 auch undatierte/nur-jahresgenaue Backkatalog-Releases automatisch; die bereits vorhandene strikte `_full_release_date_key` wird hier nicht verwendet. |
| dd28-44 | F | `client_monitor.py:196-205`, `:383-403` | Weil SAB-History `remaining_jobs` flutet, greift der `unique_category_job`-Fallback praktisch nie; nicht-titel-matchende Grabs werden `ambiguous` und nie adoptiert. `submission_unknown` ist von `fail_stale_local_submissions` explizit ausgenommen — der Track bleibt bis zum 24-h-`evidence_ttl_expired` hängen. |
| dd28-45 | F | `library-v2-page.tsx:5963-5971,6648-6657,6683-6687,6596-6605` | `useUiPreferencesMutation` setzt Query-Daten ungeordnet aus jeder Serverantwort; eine langsamere ältere Antwort auf einen früheren Resize kann eine neuere überschreiben — Spaltenbreite „springt zurück". |
| dd28-46 | F | `library-v2-page.tsx:8092-8095` | Tag-Edit invalidiert nur `['library-v2','track-file-tags',trackId]`; die Tag-Gap-Zelle der Trackzeile liest aber aus der Album-Query (`track.metadata_gaps`) und zeigt nach einem erfolgreichen manuellen Tag-Write weiter „N tag gaps". |
| dd28-47 | F | `utils/async_helpers.py:65,26-48` | Der Loop-Pump-Task (`loop.create_task(_pump_jobs())`) wird nirgends referenziert und hat keinen Done-Callback; stirbt er an einer Exception, bleibt der Thread scheinbar am Leben und jeder folgende `run_async`-Aufruf blockiert für immer, ohne sichtbaren Fehler außer einer GC-Zeit-Warnung. |

### 27.4 Niedrig

| ID | Domäne | Fundort | Kurzbeschreibung |
|---|---|---|---|
| dd28-49 | D | `core/imports/guards.py:47-49` vs. `pipeline.py:1776-1779,1880-1883` | Quarantäne-Verzeichnis wird nicht über `docker_resolve_path` aufgelöst wie Retry/Cleanup — bei Docker + Windows-Laufwerkspfad landen Einträge dort, wo Approve/Delete/List sie nicht findet. |
| dd28-50 | D | `core/imports/guards.py:51-58`, `file_ops.py:155-166` | Quarantäne-Dateiname (`<Zeitstempel>_<Stamm>.<Endung>.quarantined`) kollidiert bei zwei Kandidaten derselben Sekunde (normales Multi-Candidate-Retry-Muster); `safe_move_file` überschreibt, ein Kandidat und sein Sidecar gehen verloren. |
| dd28-51 | E | `core/library2/discography.py:178` | Cutoff nutzt das neueste *existierende* Release-Datum statt `discography_synced_at`; ein zwischen Läufen nachgetragenes Backkatalog-Release kann die Latte anheben und ein gleich/früher datiertes, echtes neues Release maskieren. Kein Zeitzonenfehler — beide Seiten sind naive Datumsangaben. |
| dd28-52 | B | `core/tidal_download_client.py:590-591` | Der iss27-09-Regex-Bug (`[^\)\]]*`, bricht bei echt verschachtelten Klammern) lebt in Tidals eigenem Query-Fallback weiter fort — betrifft nur Tidal-Fallback-Suchen mit doppelt verschachtelten Klammern wie `"Song (Live (2015))"`. |
| — | F | `library-v2-page.tsx:5288` (`awaitBulkJobState`) | Nicht gezählt, vom Agenten explizit als Nebenbefund markiert: pollt ohne Unmount-Cancellation weiter, wenn der Nutzer während eines Artist-Refresh wegnavigiert — leichtes Leck, kein Datenrisiko. |

### 27.5 Geprüft und für korrekt befunden

Diese Kategorien wurden gezielt geprüft und **nicht** als fehlerhaft
bestätigt — als negative Evidenz für den Statusabgleich festgehalten:

- **Domäne C:** kein separater Upload-Pfad neben der Provider-URL-Übernahme
  (die vermutete vierte Fehlerklasse entfällt für Artists); der zentrale
  HTTP-Fehlerparser (`webui/src/app/api-client.ts:45-61`) versteht sowohl
  flache als auch verschachtelte `error.message` — die §37-Fehlerklasse
  betrifft den Art-Picker nicht.
- **Domäne B:** `splitTrailingParenGroup` selbst ist korrekt (nested
  Klammern, unbalancierte Klammern, kombinierte Album-/Feat.-Gruppen wurden
  einzeln durchgespielt); Sonderzeichen-Escaping ist über die gesamte Kette
  sauber (kein manuelles String-Interpolieren in URLs, `requests` urlencodet
  korrekt inkl. UTF-8/CJK); dieselbe Query-Zeichenkette wird tatsächlich an
  jede Quelle gesendet — es gibt schlicht keine Pro-Quellen-Normalisierung,
  die kaputt sein könnte.
- **Domäne D:** `fetch_matched_album_tracklists` sammelt bereits korrekt
  *alle* Provider (§23/§35-Fix hält); der Post-Import-Debounce in
  `track_reconcile_trigger.py` ist korrekt album-scoped, keine
  Kollisionsgefahr zwischen zwei Alben.
- **Domäne E:** Monitoring übersteht einen erfolgreichen Download (§2.2 hält,
  `wishlist/resolution.py:43` umgeht bewusst die harte Cascade-Route); die
  Artist-Kaskade fegt explizite Track-Intents nicht mit (`wanted._decide`
  gewichtet `user_explicit`/`wishlist_import` korrekt höher); ein Crash
  zwischen Outbox-Execute und Mark-Processed ist unkritisch (Operationen sind
  idempotent, PK verhindert Duplikate); das Bearbeiten der Ziel-/Cutoff-Werte
  eines *bestehenden* Profils wirkt überall live außer im bereits als
  dd28-11 gemeldeten Sonderfall.
- **Domäne F:** `MonitorToggle`, `MonitoringModal.futureReleasesMutation`,
  Interactive Searchs eigener Grab-Button und Run-Sequence-Guard,
  `UnifiedFileRemovalDialog` sowie der *zentrale* Client-Monitor-Cancel
  (3-Miss-Bestätigung) sind alle korrekt gegen Doppel-Submit/Rollback
  abgesichert; die Restart-Adoption bindet niemals zwei Grabs an einen Job
  *innerhalb desselben Zyklus*; das neue Task-per-Job-Modell im Loop-Pump
  verwirft und vertauscht keine Jobs gegenüber dem alten
  `run_coroutine_threadsafe`-Pfad.
- **Domäne A:** Multi-File-Findings sind korrekt pro Datei dedupliziert
  (nicht pro Entity); read-only Roots werden bei realem `EROFS` sauber
  erkannt und gemeldet statt teilweise zu schreiben; `rescan_files`
  überschreibt keine Katalog-Metadaten, die ein Fix gerade geschrieben hat;
  in keinem geprüften Fix wird eine Alias-/Duplikat-Artist-Zeile
  fälschlich statt der kanonischen bearbeitet.

### 27.6 Empfohlene Abarbeitungsreihenfolge für die nächste Session

1. **Reproduzieren, nicht blind fixen** (Guide §6.3): dd28-01/dd28-02 zuerst
   live nachstellen — das sind exakt die zwei vom Nutzer selbst beobachteten
   Symptome (Artist-Foto-Fehler, Usenet-Suche).
2. **dd28-01 … dd28-07** (Timeout-/Lock-Mismatches Artwork + Search) bilden
   einen zusammenhängenden Block: Backend-Timeouts, Frontend-Timeouts und
   Lock-Reihenfolge sollten gemeinsam pro Domäne gefixt werden, nicht
   einzeln, sonst verschiebt sich das Problem nur.
3. **dd28-08 … dd28-10** (Import/Autolink: stale primary file, Alias-Album-
   Duplikat, fehlende Edition-Zeile) sind Katalog-Integritätsbugs mit
   Datenverlust-/Dopplungs-Charakter — vor jedem weiteren Livetest gegen
   eine reale DB zuerst mit einer **Kopie** reproduzieren (read-only plus
   Backup-Kopie für einen Re-Import-Testlauf, nie direkt gegen die
   Produktiv-DB; siehe Guide §6.1).
4. **dd28-11 … dd28-13** (Wanted/Outbox) vor jedem Release-Gate-Test, weil
   sie bestehende automatisierte Test-Annahmen über Quality-Profile
   möglicherweise widerlegen.
5. **dd28-14 … dd28-17** (Download-Client-Adoption/Cancel/Async-Blocking)
   sind am schwersten isoliert zu reproduzieren (brauchen echten
   SAB/NZBGet/Prowlarr-Stack oder gezieltes Mocking) — dafür Zeit einplanen,
   nicht zwischen Tür und Angel.
6. **dd28-18 … dd28-20** (Repair-Werkzeuge) vor der nächsten
   Produktiv-DB-Verifikation (wie in §27, Vorgänger-Runden) gegen eine
   DB-Kopie mit echten Multi-Edition-Alben durchspielen.
7. Rest (§27.3/§27.4) nach Kapazität; dd28-27 (fake_lossless-Identität)
   zuerst, weil es den bereits als geschlossen geführten T-12 relativiert und
   die Statusdoku entsprechend korrigiert werden sollte, sobald es verifiziert
   ist.

Bearbeitungsstand ausschließlich in
[status.md](library-v2-status.md#41-multi-agent-deep-dive-vor-dem-pr-entwurf--status).

### 27.7 Was die Abarbeitung zusätzlich gezeigt hat

Drei Beobachtungen, die über die einzelnen Funde hinausgehen und für
kommende Runden gelten:

1. **Ein Agentenvorschlag ist eine Diagnose, keine Lösung.** dd28-51 wollte
   `discography_synced_at` als Cutoff *ersetzen*; das hätte jedes Release aus
   einem längeren Sync-Loch verworfen — der bereits vorhandene
   Regressionstest hat genau das sofort aufgedeckt. Der Stamp wurde deshalb
   als zusätzlicher Zulassungspfad umgesetzt.
2. **Grüne Tests waren an vier Stellen Teil des Problems** (Guide §6 Punkt 6):
   sie pinnten den lautlosen Quellenfehlschlag (dd28-06), das Wiederabspielen
   überholter Outbox-Zeilen (dd28-13, zweimal) und die Annahme, dass eine
   denormalisierte `quality_profile_id` ohne `quality_profile_explicit` die
   Kaskade gewinnt (dd28-11). Wer nur „alles grün" prüft, hätte diese vier
   Funde für Fehlalarme gehalten.
3. **Identitäts-Bugs treten in Familien auf.** Nach dd28-27 wurden alle
   übrigen `entity_type='file'`-Findings geprüft (`orphan_file_detector`,
   `track_number_repair` ×2) — dort überall `entity_id=None`, also sauber.
   Dieselbe Systematik lohnt bei jedem neuen Fund dieser Klasse.

## 28. Legacy-Artist-/Discovery-Ansicht nach Library V2 überführen (Auftrag vom 28. Juli 2026, Abend)

Dieser Abschnitt ist ein **Auftrag für die nächste Sitzung**, kein
Bearbeitungsstand. Er ist die letzte Vorbedingung vor PR und Abschaltung der
alten Library: solange ein Nutzer über die Suche noch in der Legacy-Oberfläche
landet, kann die alte Library nicht gelöscht werden.

Findings-Präfix: `ldp-` (*Legacy Discovery Parity*).

### 28.1 Auftrag im Wortlaut (sinngemäß protokolliert)

Der Nutzer hat den Auftrag in eigenen Worten so formuliert (hier gestrafft,
aber inhaltlich unverändert übernommen, weil die Formulierungen den
Abnahmemaßstab bilden):

- „Wenn man über die Suche einen Artist sucht und auf einen Artist klickt, der
  noch nicht in der Library ist, wird man auf die **Legacy Library** verwiesen.
  Das muss auf jeden Fall noch gelöst werden, wenn wir die alte Library
  löschen." Die Ansicht soll erscheinen, aber eben **nicht** in der alten
  Library. Idee: „dass wir das auch auf die Library V2 umstellen. Aber sagen
  wir, man hat einen Artist, von dem hat man noch nichts, dann muss man das
  halt ebenfalls so fetchen."
- Ziel der Umstellung insgesamt: „für dass die Umstellung für die anderen
  einfach ist, dass sie im Prinzip nicht merken, dass es die neue Library ist"
  — und dann die alte Library **komplett löschen**.
- Zur Legacy-Artist-Ansicht: „Erstens laden die Bilder viel, viel schneller.
  Keine Ahnung, wie das geht." Bei einem Artist, der noch nicht in der Library
  ist, steht kurz „Loading Artist Discography" — „das ist ja okay, weil das
  müssen wir ja dann auch machen".
- „Den oberen Teil da mit den Informationen und wie viele Plays, wie viele
  Listeners etc. finde ich sehr gut. Ich würde eigentlich am liebsten die
  gesamte Darstellungsweise kopieren. Also dass du den **Code kopierst**, nicht
  einfach nachbaust, sondern wirklich den Code kopierst und bei uns so
  implementierst." — mit Anpassung, damit es in Library V2 hineinpasst.
- Gleichzeitig: „Die Ansicht, die wir jetzt von Library V2 haben, gefällt mir
  eigentlich schon sehr gut." `My Library` bleibt wie es ist. `All Releases`
  ist inhaltlich gut, aber: „Wenn ich auf All Releases gehe bei Library V2,
  dann dauert es viel länger, bis die Albumbilder nachgeladen werden."
- Deshalb ein **Umschalter nur bei All Releases**: „wenn man All Releases
  anklickt, sollten nebendran noch mal zwei Optionen kommen. Table View, also
  so wie wir jetzt haben, oder so wie Legacy" — also die Kachel-/Card-Optik der
  alten Library.
- Artist-Kopfbereich: „Es kann eigentlich nicht schaden, dass wir neben dem
  Artist ebenfalls diese Informationen darstellen wie Top Tracks, Listeners,
  Plays. Ich weiß, ich habe mal gesagt, dass ich das nicht haben möchte, aber
  das schadet ja nicht." **Harte Auflage:** „Ich will einfach, dass es
  **vertikal nicht noch mehr Platz einnimmt**, als es jetzt einnimmt. Das
  heißt, es muss kompakt sein" — oder über einen Umschalter am Artist-Kopf, mit
  dem man die Darstellung der Artist-Informationen wechseln kann.
- Herkunftsabhängige Vorbelegung: „Wenn man von Search kommt, sollte dann zum
  Beispiel immer diese Legacy-Darstellungsweise angezeigt werden und muss halt
  umgeschaltet werden."
- Top Tracks: „bei Top Tracks sollte einfach stehen **Bookmark**, nicht
  Download. Momentan steht bei Legacy ja *Add to Wishlist*."
- Discovery-Filter: „Sortierfunktionen bei der Discovery-Ansicht — Album, EPs,
  Singles, Live, Compilations, Features etc. finde ich sehr gut. Das sollten
  wir auch implementieren. Eigentlich sollte die All-Releases-Ansicht wie zur
  Discovery-Ansicht umgeschaltet werden können, und wo man ebenfalls umschalten
  kann zwischen Table View und Legacy View, auch mit diesen wunderschönen
  Sortierknöpfen."
- Ausdrücklich **nicht** übernehmen: „so wie die Metadaten-Sources dargestellt
  werden bei der Legacy, finde ich nicht so okay. Ist auch nützlich, aber nicht
  so meins."
- Untersuchungsauftrag: „Untersucht, wieso bei der Legacy — selbst bei Alben,
  die noch nicht in der Mediathek sind — die Albumcovers und Single-Covers viel,
  viel schneller laden. Untersucht das, und dass wir das auch machen."
  Arbeitsweise generell: „Schaue immer, wie es bei der alten gemacht ist, und
  dann implementiere es bei uns, je nachdem noch ein bisschen anpassen."
- Abschlussbedingung: „Erst wenn das implementiert ist, können wir die PR
  machen. Weil dann können wir die alte Library wirklich komplett löschen, weil
  dann haben wir alles implementiert, was die alte Library auch konnte."

### 28.2 Ist-Zustand im Code (verifiziert, 28. Juli 2026)

**Routing Suche → Artist.** `webui/static/search.js` unterscheidet zwei Fälle:

| Trefferart | Zeile | Ziel |
| --- | --- | --- |
| `db_artists` („In Your Library") | search.js:482-484 | `/library-v2?artist=<library_v2_id>` — korrekt, das war der Fix aus §10/§11 |
| `spotify_artists` (Provider-Treffer, nicht in der Library) | search.js:500 | `buildArtistDetailPath(id, source, name)` → `/artist-detail/<source>/<id>` |

Die Route `/artist-detail/$source/$id`
(`webui/src/routes/artist-detail/$source/$id.tsx:35-53`) ist ein reiner
Legacy-Handoff: sie ruft `bridge.navigateToArtistDetail(...)`, das in
`webui/static/library.js:812` die alte Artist-Detail-Seite aufbaut. Genau das
ist das vom Nutzer beobachtete Symptom — es ist kein Fallback-Unfall, sondern
der einzige existierende Pfad für Provider-Artists.

**Library V2 kennt keinen Provider-Artist.** Sämtliche Routen in
`api/library_v2.py` sind auf `<int:artist_id>` typisiert, d. h. auf eine
vorhandene `lib2_artists`-Zeile. Es gibt heute keine Möglichkeit, in V2 einen
Artist ohne Katalogzeile zu betrachten. Das ist die eigentliche strukturelle
Lücke hinter ldp-01/ldp-02 — nicht das Frontend.

**Legacy-Bausteine, die kopiert werden sollen (Fundstellen):**

| Baustein | Fundstelle |
| --- | --- |
| Hero-Markup (Bild, Badges, Genres, Bio, Listeners/Plays, Completion-Bars, Top-Tracks-Sidebar) | `webui/index.html:4565-4655` |
| Hero-Befüllung inkl. Last.fm-Listeners/Playcount | `library.js:1389` (`updateArtistHeroSection`), Stats bei 1475-1495 |
| Top Tracks (2 Pässe: Provider-Popularität, sonst Last.fm) | `library.js:1625-1745`; Endpunkte `/api/artist/<id>/top-tracks`, `/api/artist/0/lastfm-top-tracks?name=` |
| Discography-Filterleiste (Show / Include / Status / Sources) | Markup `webui/index.html:4676ff`; Logik `library.js:2632` (`initializeDiscographyFilters`), `2662` (`reset…`), `2687` (`apply…`) |
| Inhaltsklassifikation Live/Compilation/Featured | `library.js:_classifyReleaseContent` (ca. 2920-2940) — **reine Funktion, 1:1 portierbar** |
| Release-Karte (Kachel) | `library.js:2141` (`createReleaseCard`), Sektionsaufbau `1848` (`populateDiscographySections`) |
| Lazy-Loading der Kachelbilder | `data-bg-src` + `observeLazyBackgrounds` in `webui/static/core.js:225-239` (IntersectionObserver, `rootMargin: 200px`) |
| CSS | `webui/static/style.css`: `.artist-hero-section` ab 29418, `.discography-filters` ab 29074, `.release-card` ab 29186, `.album-card-image` ab 26953, `.discog-card` ab 50582 |
| Provider-Discography-Endpunkt (funktioniert bereits für Nicht-Library-Artists) | `GET /api/artist-detail/<id>?source=<src>&name=<name>` — siehe `library.js:1000-1012` |

**Library-V2-Gegenstücke:**

| Baustein | Fundstelle |
| --- | --- |
| Artist-Kopf | `library-v2-page.tsx` in `ArtistDetailView` (ab 4923), Header endet bei ~5196 |
| `My Library` / `All Releases`-Umschalter | `library-v2-page.tsx:5198-5222` |
| Release-Darstellung | `AlbumGroup` (nur Tabellenform), Aufrufe ab 5225 |
| Artwork-Komponente | `library-v2-page.tsx:400-470` (`Artwork`, `watchPendingArtwork`) |

### 28.3 Untersuchungsergebnis: warum Legacy-Cover schneller laden

Das ist der explizit beauftragte Untersuchungspunkt. Die Ursache ist eindeutig
und liegt **nicht** an Lazy-Loading, Bildgrößen oder Renderkosten, sondern an
zwei völlig verschiedenen Auslieferungswegen.

**Legacy:** `release.image_url` ist die **Provider-CDN-URL** genau so, wie sie
aus der Discography-Antwort kommt (Spotify-/Deezer-CDN). `createReleaseCard`
(`library.js:2167-2174`) hängt sie als `data-bg-src` an die Kachel, der
IntersectionObserver setzt sie beim Sichtbarwerden. Der Browser lädt also `N`
Bilder **direkt vom CDN**, hochparallel über HTTP/2, häufig aus einem bereits
warmen CDN-/Browser-Cache, und der SoulSync-Server ist an keinem einzigen
dieser Requests beteiligt.

**Library V2:** `_apply_artwork_urls` (`api/library_v2.py:281-284`)
**überschreibt jede** `image_url` mit `_artwork_url()`
(`api/library_v2.py:265-278`), also mit
`/api/library/v2/artwork/<kind>/<id>?v=<version>`. Das gilt in
`lib2_get_artist` (`api/library_v2.py:1394-1396`) für den Artist **und für
jedes Album, jede EP, jede Single** — einschließlich der reinen
Discography-Einträge, die in `All Releases` sichtbar werden.

Was bei kaltem Artwork-Cache pro Cover passiert
(`api/library_v2.py:2067-2135`):

1. Fast-Path prüft `thumb_file` / `artwork_file` auf der Platte → nicht da.
2. Der Endpunkt antwortet mit **404 + `X-Artwork-Pending: 1`** und stellt einen
   Hintergrund-Build in die Queue (`schedule_artwork_build`).
3. Der Client (`library-v2-page.tsx:437-451`) rendert den Platzhalter und
   abonniert über `watchPendingArtwork`, bis der Server „ready" meldet.
4. Der Build selbst ist teuer: Provider-Walk, HTTP-Download, **zwei
   JPEG-Encodes** (Vollbild + Thumb).
5. Diese Builds laufen durch einen begrenzten Pool —
   `_precache_max_workers` in `core/library2/artwork.py:899-915`, **Default 6**.

Damit ergibt sich die beobachtete Differenz direkt aus der Architektur: eine
`All Releases`-Seite mit ~100 Releases erzeugt in Legacy ~100 parallele
CDN-GETs, in V2 dagegen ~100 serverseitige Builds durch 6 Worker, also
grob 17 aufeinanderfolgende Wellen aus Download + doppeltem Encode. Das ist
kein Tuning-Problem; auch ein größerer Pool verschiebt es nur.

**Der entscheidende Hebel:** Die Provider-URL ist **bereits im Katalog
vorhanden** — `lib2_albums.image_url` bzw. `lib2_artists.image_url` werden in
`core/library2/queries.py` (u. a. 445/501, 260/290, 1208) sauber selektiert und
über die Override-Auflösung gereicht. Sie wird erst im API-Layer verworfen. Es
muss also nichts neu beschafft werden, es muss nur aufhören, weggeworfen zu
werden.

**Verschärfend:** Der `?v=`-Cache-Buster aus `_artwork_url` sorgt dafür, dass
ein kaltes Cover nicht einmal aus dem HTTP-Cache bedient werden kann, während
die CDN-URL in Legacy praktisch immer cachebar ist.

**Fix-Vertrag ldp-07 (empfohlene Variante A):** Die Serialisierung liefert
zusätzlich zum lokalen `image_url` ein `remote_image_url` mit der
Provider-URL aus dem Katalog. Die `Artwork`-Komponente zeigt sofort
`remote_image_url` an (schneller First Paint, exakt wie Legacy) und wechselt
auf das lokale Endpoint-Bild, sobald dieses als „ready" gemeldet wird bzw. beim
nächsten Render bereits gecacht ist. Damit bleibt die lokale Kopie die
langfristige Wahrheit (NAS/Offline/eigene Coverauswahl bleiben unberührt), aber
kein Nutzer wartet mehr auf einen Kaltstart-Build.

Zwei Zusatzentscheidungen, die dabei zu treffen sind:

- **Verworfene Variante B** — nur den Precache aggressiver fahren: hilft
  genau im beobachteten Fall (erster Besuch einer Discography) nicht.
- **Offene Produktentscheidung:** Sollen reine Discography-Einträge
  („All Releases", nicht besessen, nicht monitored) überhaupt lokal
  gecacht werden? Dagegen spricht, dass jedes Durchblättern den Artwork-Cache
  mit Releases füllt, die der Nutzer nie haben will. Vorschlag: lokaler Cache
  nur für besessene/monitored Entitäten, für den Rest ausschließlich die
  Remote-URL. **Vor Umsetzung mit dem Nutzer klären.**

### 28.4 Zielbild und Einzelfindings

#### <a name="ldp-01"></a> ldp-01 — Suchtreffer für nicht vorhandene Artists landet in der Legacy-Library

**Symptom:** Klick auf einen Provider-Artist in der Suche öffnet die alte
Artist-Detail-Seite (`search.js:500` → `/artist-detail/<source>/<id>` →
Legacy-Shell).

**Fix-Vertrag:** Auch Provider-Treffer routen nach Library V2. Die Route muss
Provider-Identität transportieren können (Quelle + Provider-ID + Name), nicht
nur eine numerische Katalog-ID. `/artist-detail/$source/$id` darf nach der
Umstellung entweder auf V2 weiterleiten oder entfallen.

#### <a name="ldp-02"></a> ldp-02 — Library V2 kann keinen Artist ohne Katalogzeile darstellen

**Symptom/Ursache:** Alle V2-Endpunkte sind auf `<int:artist_id>` typisiert;
für einen Artist ohne `lib2_artists`-Zeile existiert kein Datenpfad.

**Fix-Vertrag:** Ein „Discovery-Modus" der V2-Artist-Ansicht, der einen Artist
allein aus Provider-Daten rendert (Ladezustand „Loading Artist Discography" ist
ausdrücklich in Ordnung). Der bestehende, bereits quellenfähige Endpunkt
`/api/artist-detail/<id>?source=&name=` ist der naheliegende Lieferant —
wiederverwenden statt neu bauen. Zu klären: ob dieser Modus lesend bleibt und
erst bei Monitor/Bookmark eine Katalogzeile materialisiert (bevorzugt, weil es
die Katalog-Hygiene aus §62/§63 nicht gefährdet), oder ob beim Öffnen sofort
angelegt wird. **Mit dem Nutzer klären.**

#### <a name="ldp-03"></a> ldp-03 — `All Releases` braucht einen Ansichtsumschalter Table ↔ Legacy-Karten

**Fix-Vertrag:** Direkt neben dem bestehenden `My Library` / `All Releases`
-Umschalter (`library-v2-page.tsx:5198-5222`) erscheint **nur bei aktivem
`All Releases`** eine zweite Umschaltgruppe: `Table View` (heutige
`AlbumGroup`-Tabelle, Default) und `Legacy View` (Kachelgitter). Das
Kachelgitter wird aus `createReleaseCard` (`library.js:2141`) samt
`.release-card`/`.album-card-image`-CSS übernommen, nicht freihändig
nachgebaut. `My Library` bleibt unverändert.

#### <a name="ldp-04"></a> ldp-04 — Discography-Filterleiste fehlt in V2

**Fix-Vertrag:** Portierung der Legacy-Filterleiste (`Show`: Albums/EPs/
Singles; `Include`: Live/Compilations/Featured; `Status`: All/Owned/Missing) in
die `All Releases`-Ansicht, wirksam in **beiden** Ansichtsmodi. Die
Klassifikation kommt aus `_classifyReleaseContent`, das als reine Funktion 1:1
übernommen wird — damit driften V2 und der Download-Discography-Dialog nicht
auseinander (das war der Sinn von #877).

#### <a name="ldp-05"></a> ldp-05 — Artist-Kopf ohne Listeners/Plays/Top Tracks

**Fix-Vertrag:** Der V2-Artist-Kopf erhält Listeners, Plays und Top Tracks aus
den bestehenden Endpunkten. **Harte Auflage: die Kopfzeile darf vertikal nicht
höher werden als heute.** Zulässige Lösungen: horizontale Verdichtung in die
vorhandene Kopfzeile und/oder ein Umschalter der Kopfdarstellung
(kompakt ↔ Legacy-reich). Herkunftsabhängige Vorbelegung: aus der Suche
kommend wird die Legacy-reiche Darstellung vorbelegt, umschaltbar bleibt sie
trotzdem. Die Auswahl wird wie die übrigen Ansichtseinstellungen persistiert.

#### <a name="ldp-06"></a> ldp-06 — Top-Tracks-Aktion heißt falsch

**Fix-Vertrag:** Die Aktion an einem Top Track heißt **Bookmark** und benutzt
die V2-Monitoring-Semantik (`MonitorToggle`/Wanted). Weder `Download` noch das
Legacy-`Add to Wishlist` (`library.js:2904`) wird übernommen.

#### <a name="ldp-07"></a> ldp-07 — Artwork-Geschwindigkeit

Siehe §28.3. Abnahme: Beim ersten Öffnen von `All Releases` eines Artists mit
kaltem Artwork-Cache sind die Cover **spürbar so schnell** sichtbar wie in der
Legacy-Ansicht, nicht erst nach mehreren Hintergrund-Build-Wellen.

#### <a name="ldp-08"></a> ldp-08 — Metadaten-Quellen-Darstellung NICHT übernehmen

**Vertrag:** Die Legacy-Darstellung der Metadaten-Quellen wird ausdrücklich
nicht kopiert. Die bestehenden V2-`ArtistMatchChips` bleiben.

#### <a name="ldp-09"></a> ldp-09 — Abschlussbedingung

**Vertrag:** ldp-01 bis ldp-07 sind Vorbedingung dafür, den PR zu stellen und
die alte Library zu entfernen. Erst wenn ein Nutzer über die Suche einen
beliebigen Artist — in der Library oder nicht — öffnen kann, ohne je die
Legacy-Oberfläche zu sehen, ist die Funktionsparität hergestellt.

### 28.5 Arbeitsweise: „kopieren statt nachbauen"

Der Nutzer hat explizit verlangt, den Legacy-Code zu **kopieren**, nicht nach
Augenmaß neu zu bauen. Da Legacy Vanilla-JS mit globalem DOM ist und V2
React/TypeScript, heißt „kopieren" konkret:

- **Wörtlich übernehmen:** CSS-Regeln und Klassennamen aus `style.css`,
  Markup-Struktur aus `index.html:4565-4655` und `4676ff`, die reine Funktion
  `_classifyReleaseContent`, die Filterlogik aus `applyDiscographyFilters`, das
  Lazy-Loading-Muster aus `core.js:225-239`, die Top-Tracks-Endpunktnutzung
  samt Zwei-Pass-Fallback aus `library.js:1625-1745`.
- **Anzupassen:** DOM-Mutation → React-State; `getElementId`-Zugriffe →
  Props/Queries; Legacy-Aktionsnamen → V2-Semantik (ldp-06).
- **Nicht zu übernehmen:** die Metadaten-Quellen-Darstellung (ldp-08) und alles,
  was die Kopfzeile vertikal wachsen lässt (ldp-05).

### 28.6 Vor Umsetzungsbeginn zu klären — beantwortet am 28. Juli 2026

Alle drei Punkte wurden vor Umsetzungsbeginn gestellt und vom Nutzer
entschieden. Die Antworten sind Teil des Fix-Vertrags, nicht bloß Kontext:

1. **ldp-02 — rein lesend.** Der Discovery-Modus rendert ausschließlich aus
   Providerdaten; die `lib2_artists`-Zeile entsteht erst bei Bookmark/Monitor
   bzw. beim Öffnen eines Release. Begründung: Katalog-Hygiene aus §62/§63 —
   bloßes Durchblättern darf keine Entities anlegen.
2. **ldp-07 — nur besessen/monitored lokal.** Reine Discography-Einträge
   werden nicht lokal gecacht, weder auf Anfrage noch im Precache; für sie ist
   die Provider-CDN-URL direkt die `image_url`. Besessene/monitored Entitäten
   behalten den lokalen Cache als Wahrheit und bekommen die CDN-URL nur als
   `remote_image_url`-Überbrückung, solange ein kalter Build läuft.
3. **ldp-05 — Umschalter.** Kompakter V2-Kopf (Default) ↔ reicher
   Legacy-Kopf, umschaltbar am Kopf selbst; aus der Suche kommend ist der
   reiche Kopf vorbelegt. Damit ist die harte Auflage „vertikal nicht höher
   als heute" ohne Kompromiss an der Legacy-Optik erfüllt.

### 28.7 Zwei Punkte, die die Umsetzung zusätzlich festlegen musste

Beides fiel erst beim Bauen auf und ist deshalb hier festgehalten, nicht nur
im Code:

- **Ein einziger Umleitungspunkt statt vieler Aufrufer.** `search.js` ist
  nicht die einzige Stelle, die `/artist-detail/<source>/<id>` erzeugt —
  Global Search, Media Player, Playlist-Sync, Similar-Artist-Bubbles und
  `api-monitor.js` bauen dieselbe URL über `buildArtistDetailPath`
  (`init.js:2988`). Die Umstellung passiert deshalb **in der Route**
  (`webui/src/routes/artist-detail/$source/$id.tsx` leitet nach
  `/library-v2?discover=<source>:<id>` um), nicht in den Aufrufern: ein
  Änderungspunkt, und ldp-09 gilt für jeden Einstieg gleichzeitig. Die
  URL-Form bleibt erhalten, damit Links und Browser-History weiter
  funktionieren.
- **`library` ist kein Provider-Namespace.** Der Quellsegmentwert `library`
  transportiert eine opake Legacy-`artists.id`. Der Resolve-Endpunkt löst sie
  über `lib2_artists.legacy_artist_id` auf und reicht sie **nie** als
  Provider-ID an den Autolink-Resolver weiter — sonst landete eine
  Media-Server-ID in einer Provider-ID-Spalte (Guide §2.5).

---

## 29. Multi-Agent-Audit nach dem Upstream-Sync (1. August 2026)

Fünf parallele Read-only-Audits über `library-overhaul` @ `483405764`
(Upgrade-/First-Import-Pfad, Routing/Einstiegspunkte, Library-Aktionen,
lib2-Backend, Datei-/Repair-Operationen). Diagnosen, keine Fixes — der
Remediationstatus gehört in `library-v2-status.md`.

Alle als **verifiziert** markierten Befunde hat der Koordinator unabhängig am
Code nachgeprüft, nicht nur vom Agenten übernommen. Als **SPEKULATIV**
markierte Punkte hat der jeweilige Agent selbst nicht belegen können; sie
brauchen vor jeder Arbeit eine eigene Reproduktion.

Baseline zum Zeitpunkt des Audits: `webui` 36 Dateien / 240 Tests grün,
`tests/library2` 1.193 grün. **Kein einziger Befund unten wird von der
bestehenden Suite gefangen.**

### 29.1 Release-Blocker

#### iss29-A01 — Upgrade landet in einer dauerhaft leeren Library, die sich als „fertig" meldet — VERIFIZIERT

`core/library2/bootstrap.py:274-289` (`try_claim`) schreibt in beiden
UPDATE-Zweigen `resume_watermark=?` neu, löscht aber `resume_stage`,
`resume_rowid` und `resume_run_id` nicht. Der Watermark ist das **einzige**
Invalidierungssignal (`bootstrap.py:231-232`), und `run_bootstrap_if_needed`
liest den Resume-Punkt *vor* dem Claim (`:496-498`) — der Aufrufer erfährt also
nie, dass der Claim genau die Zeile gerade wieder gültig gestempelt hat, die er
verworfen hatte.

Der Agent hat es mit zwei Probe-Skripten gegen den echten Codepfad belegt:

```
after run A : resume_stage='tracks' rowid=60000 run_id='RUN-A' watermark=W1
run B resume point (korrekt invalidiert): None
after run B claim: resume_stage='tracks' rowid=60000 run_id='RUN-A' watermark=W2  <-- neu gestempelt
run C resume point: ResumePoint(stage='tracks', rowid=60000, run_id='RUN-A')
```

Wirkung: der wiederbelebte Punkt nennt `stage='tracks'`, also liefert
`walk_from()` (`core/library2/importer.py:1036-1045`) für `artists` und `albums`
`None` — beide Walks entfallen. Der Tracks-Walk findet dann ein leeres
`album_map` und überspringt jede Zeile (`importer.py:1395-1397`). `mark_done`
stempelt den aktuellen Watermark, `should_stop_autostart` liefert `True`, und
`web_server.py:32719-32720` **verlässt die Autostart-Schleife**:

```
4) tick -> {'success': True, 'stats': {'artists': 0, 'albums': 0, 'tracks': 0}}
   status='done'; lib2 counts 0/0/0; should_stop_autostart: True
5) tick -> {'skipped': 'already_done'}   lib2 weiterhin 0/0/0
```

Das Fenster ist auf einem echten Upgrade realistisch: zwischen Claim und erstem
Checkpoint (`importer.py:1105`) läuft `ensure_library_v2_schema` mit sieben
Full-Table-Pässen (`core/library2/schema.py:824-905`) plus
`_ArtistResolver.seed_existing()` und `_discography_album_index()` — auf einer
100k-Track-Library zig Sekunden, also genau dort, wo ein crash-loopender
Container steht. Über die UI ebenso erreichbar: `POST /api/library/v2/import`
(`api/library_v2.py:5046`) und `POST /api/library/v2/reset` (`:5159`) claimen
dieselbe Zeile; `reset` committet den `DELETE FROM lib2_*`-Wipe vor dem ersten
Checkpoint (`importer.py:1080`).

Fixrichtung: `resume_stage`/`resume_rowid`/`resume_run_id` im selben UPDATE
löschen, wenn `resume_point_for` `None` ergab (die Entscheidung in `try_claim`
hineinreichen) — oder `resume_watermark` nur schreiben, wenn die Zeile gerade
keinen Checkpoint trägt, damit ein unpassendes Paar sich weiter selbst ablehnt.

#### iss29-E01 — Reorganize löscht das Original auch dann, wenn der DB-Update fehlschlug — VERIFIZIERT

`core/library_reorganize.py:1592-1598` erkennt einen DB-Fehler ausschließlich an
einer Exception aus `ctx.update_track_path_fn` und gibt dann `False` zurück. Der
übergebene Callback in `core/reorganize_runner.py:118-120` schluckt aber alles:

```python
        except Exception as db_err:
            logger.warning(f"[Reorganize] DB path update failed for {track_id}: {db_err}")
            return
```

Also läuft `_finalize_track` weiter bis `os.remove(resolved_src)`
(`library_reorganize.py:1624`) und meldet `True`.

Szenario: Reorganize läuft, während die SQLite-Schreibsperre gehalten wird
(Import-Commit, `recompute_wanted`). `with db._get_connection() as conn:`
(`reorganize_runner.py:75`) wirft `database is locked`; die ganze Transaktion —
legacy `tracks.file_path` **und** `lib2_track_files.path` (`:106-116`) — rollt
zurück. Das Original wird trotzdem gelöscht, der Lauf meldet „moved". Beide
Kataloge zeigen auf einen Pfad, den es nicht mehr gibt → lib2-Missing →
`dead_file_cleaner`-Finding → die Wanted-Projektion lädt einen Track neu
herunter, den der Nutzer besaß.

Branch-spezifisch: auf `main` führte dieser Callback eine einzige UPDATE-Anweisung
aus; dieser Branch hat vier weitere in denselben `try` gelegt, sodass ein
lib2-Fehler jetzt auch den Legacy-Write verwirft.

Fixrichtung: re-raisen (oder einen geprüften Bool zurückgeben) und Legacy- und
lib2-Write in getrennte Transaktionen legen.

#### iss29-E02 — Sibling-Format-Move überschreibt eine vorhandene Zieldatei stillschweigend — VERIFIZIERT

`core/library_reorganize.py:2324-2332` baut `sibling_dst` und ruft
`shutil.move` — **ohne** Existenzprüfung; auf POSIX löst das für eine reguläre
Datei zu `os.rename` auf und überschreibt. Der kanonische Move daneben prüft
sehr wohl (`_rename_track_in_place:2004`, `'destination already exists'`,
Docstring: „never silent data loss") — aber die Siblings werden in `:2010-2011`
**vor** dieser Prüfung verschoben, die Absicherung greift also nie für sie.

Szenario: Lossy-Copy aktiv → `Old/05 Song.flac` (in DB) + `Old/05 Song.mp3`
(nicht in DB). Im Ziel liegt bereits ein fremdes `05 - Song.mp3` (früherer
Teillauf, zweite Edition). `new_abs` für die FLAC existiert nicht, die
kanonische Prüfung geht also durch; der Sibling-Zweig zerstört die vorhandene
MP3 ohne Fehler, ohne Log, ohne Zähler. Sekundär: schlägt danach das kanonische
`os.rename` (`:2013`) fehl, sind die Siblings schon verschoben und das Album ist
auf zwei Ordner verteilt.

Fixrichtung: dieselbe Existenzprüfung auf `sibling_dst`; Siblings erst nach
erfolgreichem kanonischen Rename bewegen.

### 29.2 Major

#### iss29-A02 — Heartbeat-Drosselung verwirft genau die Checkpoints, die eine Rowid tragen

`core/library2/bootstrap.py:509-517`: `_progress` verwirft jeden Beat mit
`current != total` innerhalb von `_HEARTBEAT_THROTTLE_SECONDS`. Genau das sind
die Stage-Eröffnungs-Checkpoints (`importer.py:1105`, `:1187`, `:1379`) und der
Finalize-Übergang (`:1569`) — und nur diese persistiert `heartbeat()` überhaupt
(`bootstrap.py:361`: `writes_checkpoint = rowid is not None and bool(run_id)`).
Belegt: nach einem **vollständig erfolgreichen** Lauf steht
`resume_stage='tracks', resume_rowid=0` in der Zeile, nie `'finalizing'`. Der
mig-01-Vertrag aus `library-v2-status.md` §43.1 hält damit für keine Library,
deren Walks in unter 5 s durchlaufen. Allein kostet das nur Doppelarbeit —
zusammen mit iss29-A01 liefert es den giftigen `tracks`/`0`-Wert.
Fixrichtung: einen Beat mit `rowid` nie drosseln.

#### iss29-A03 — `/library` friert während der automatischen Migration auf „Migrating your library…" ein

`webui/src/routes/library/-library-v2.api.ts:1823-1832` pollt nur, solange
`running` oder `artwork_cache.running` gesetzt ist. `running` ist der
In-Process-State, den **ausschließlich** der manuelle Import-Button setzt
(`api/library_v2.py:5040-5041`); der Autostart-Bootstrap rührt ihn nie an.
`bootstrap.status` — der einzige Deskriptor der automatischen Migration
(`:5231`) — wird nicht abgefragt. `refetchOnWindowFocus` ist global `false`
(`webui/src/app/query-client.ts:8`). Der eine Statusabruf sieht
`bootstrap.status === 'running'`, React Query stoppt den Timer, wertet das
Prädikat erst nach einem Fetch neu aus — der nie kommt. Der Nutzer sieht
stundenlang dieselbe Prozentzahl. `import-status.test.tsx:220-320` prüft nur
`describeLibraryV2Migration` als reine Funktion, pinnt das also nicht.

#### iss29-A04 — `mark_done` stempelt den *Nach*-Lauf-Watermark

`core/library2/bootstrap.py:536-544` nimmt `source_watermark(database)` am Ende.
Die drei Walks sind Keyset-Scans zu verschiedenen Zeitpunkten
(`importer.py:1106`, `:1211`, `:1390`), und dazwischen läuft der komplette
`post_import`-Precache. Auto-Import, Wishlist-Downloads und Media-Server-Sync
schreiben derweil in die Legacy-Tabellen. Ein Artist, der nach dem
Artists-Walk entsteht, wird nie gewalkt, aber vom Schluss-Watermark mitgezählt →
nächster Tick `skipped: already_done`, `should_stop_autostart` `True`, Artist für
die restliche Prozesslaufzeit in V2 unsichtbar. Fixrichtung: den vor dem ersten
Walk erfassten Watermark stempeln (den `try_claim` bereits notiert hat).

#### iss29-B01 — Sidebar-„Library" ist aus jeder Library-Unteransicht ein toter Klick — VERIFIZIERT

`webui/static/init.js:3175` `if (!options.forceReload && pageId === currentPage) return;`
und `webui/static/shell-bridge.js:74` `currentPage = pageId;` in
`showReactHost`. `useReactPageShell('library')`
(`webui/src/platform/shell/route-controllers.tsx:48`) ruft `showReactHost('library')`
für **jede** V2-Ansicht, also ist `currentPage` durchgehend `'library'`. Der
Capture-Handler (`shell-bridge.js:230`) `preventDefault()`et den Anker und ruft
`navigateToPage('library')`, das sofort zurückkehrt. Auf `/library?artist=42`
passiert beim Klick auf „Library" gar nichts — keine URL-Änderung, keine
Ansicht, kein Feedback. Genauso bei `?section=wanted` und `?album=7`. Vor dem
Cutover war Artist-Detail eine eigene `pageId`, da funktionierte der Klick.

#### iss29-B02 — `?discover=` / `?discoverName=` sind verwaist, Discovery-Modus unerreichbar

Mit der Rücknahme von ldp-01 (§44.2 im Status-Doc) ist der einzige Produzent
dieser URLs entfallen. Jede verbliebene Zuweisung *löscht* sie
(`library-v2-page.tsx:5764,5765,5787,5788,5810,5842`); die einzige Konstruktion
im Repo steht in der Testfixture `-ui/discovery-artist.test.tsx:24`.
Unerreichbar, aber weiterhin ausgeliefert: `DiscoveryArtistView`
(`library-v2-page.tsx:5723-5905`), `DISCOVERY_ARTIST_VIEW` (`:4871-4875`), der
API-Client-Block (`-library-v2.api.ts:635-760`), vier Backend-Endpunkte
(`GET/POST /api/library/v2/discovery/artist`, `POST …/discovery/track`,
`GET …/discovery/track-status`), der ldp-06-„Bookmark artist"-Flow
(`:5775-5796`) und eine grüne Testsuite für einen Modus, den niemand betreten
kann. **Produktentscheidung nötig:** ersatzlos löschen, oder einen
Einstiegspunkt geben (z. B. „Open in Library" auf Upstreams Artist-Detail für
einen Provider-Artist ohne Katalogzeile).

#### iss29-B03 — Der einzige überlebende Search→Library-Einstieg lädt die ganze App neu

`inLibraryArtistPath()` liefert `/library?artist=<v2id>`, und die Karte ist ein
nackter `<a href>` ohne `onClick` (`-ui/search-results.tsx:203-219`,
`compact-item.tsx:182-197`). Der einzige Dokument-Interceptor
(`shell-bridge.js:214-241`) behandelt nur `.nav-button[data-page]` und
`/artist-detail/`; `/library?...` fällt an den Browser durch, TanStack fängt
rohe Anker nicht ab. Ergebnis: der Treffer *mit* `library_v2_id` erzwingt einen
kompletten Dokument-Load (index.html, alle Vanilla-Bundles, React-Bundle,
Profil-Bootstrap), während die Karte direkt daneben *ohne* `library_v2_id`
in-app navigiert. `webui/static/downloads.js:5326` baut denselben Link von Hand
mit demselben Ergebnis.

#### iss29-C01 — Interactive-Search-Grab-Watcher ist zeitzonenkaputt, Quarantäne-Feedback feuert nie — VERIFIZIERT

`webui/src/routes/library/-ui/interactive-search.tsx:269-286` vergleicht
`Date.parse(e.date)` gegen `Date.now()`. `e.date` ist die rohe DB-Spalte, nie
normalisiert (`core/library2/history_feed.py:252,295,341,379,427`;
`api/library_v2.py:4409-4441` reicht sie durch). Die Spalte ist
`created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP`
(`core/acquisition/history.py:64`), der Insert (`:209-212`) liefert nichts —
also SQLites **UTC**-`CURRENT_TIMESTAMP` im Format `"YYYY-MM-DD HH:MM:SS"`,
Leerzeichen, keine Zone. V8 parst das als **Lokalzeit**. Nachgemessen:

```
$ TZ=Europe/Zurich node -e "…"
Date.parse('2026-07-31 13:04:05') = 2026-07-31T11:04:05.000Z
tatsächliche Bedeutung            = 2026-07-31T13:04:05.000Z
Versatz                           = 2 Stunden
```

Europe/Zurich ist die Zeitzone dieses Projekts — der Fehler feuert also heute.
Östlich von UTC fällt das Quarantäne-Event aus dem `fresh`-Filter, jeder Poll
liefert `pending`, und `:653-655` zeigt am Ende **`Grabbed ✓`** über einer Datei,
die nie ankam — exakt die Regression, die §21.2 / iss27-10 als behoben führt.
Westlich davon invertiert es: eine ältere, fremde Quarantäne wird diesem Grab
zugeschrieben. Dieselbe rohe Zeit rendert das History-Modal
(`library-v2-page.tsx:2203`), zeigt also UTC als Lokalzeit an.

#### iss29-C02 — Refresh & Scan und Automatic Search melden einen *laufenden* Job als Fehler (409 unbehandelt)

`-library-v2.api.ts:328-335` und `:1478-1488` übergeben kein
`throwHttpErrors: false`; `apiClient` ist `ky.create({ retry: 0 })`
(`webui/src/app/api-client.ts:14-17`), der 409 wirft also vor dem Lesen des
Bodys. Der Server liefert die laufende Job-ID sehr wohl zurück
(`api/library_v2.py:4922-4929` bzw. `:4562-4569`), und
`startLibraryV2DiscographyRefresh` (`-library-v2.api.ts:624-633`) zeigt das
gewünschte Muster. Der Button-State ist per-Mount
(`library-v2-page.tsx:675-709`), überlebt also weder Remount noch zweiten Tab
noch die separate `useScopedSearchBanner`-Instanz der Albumseite. Der
`dd28-16`-Kommentar (`:6596-6610`) beansprucht diese Klasse als behoben, deckt
aber nur den In-Session-Fall. `startLibraryV2UpgradeScan` (`:1463-1470`) hat
dieselbe Lücke für die globale Automatic Search.

#### iss29-C03 — Fehlgeschlagener Artist-Listen-Fetch rendert „Your library is empty — Import library" — VERIFIZIERT

`library-v2-page.tsx:3812` `const isEmpty = !artistsQuery.isLoading && artists.length === 0 && !search.q;`
— `isError` wird nirgends gelesen, `retry: 1`
(`webui/src/app/query-client.ts:7`). Nach den Retries ist `isLoading` false und
`data` undefined → `LibraryEmptyState` (`:10137-10164`) erklärt einem Nutzer mit
900 Artists seine Library für leer und bietet einen vollen Import an. Mit Text im
Filterfeld degradiert es zur stumm leeren Liste. `AlbumDetailView`
(`:4750-4753`) macht es richtig — es ist also eine Inkonsistenz, kein Hausstil.

#### iss29-C04 — Fehlgeschlagener Artist-Fetch lässt die Seite dauerhaft auf „Loading…"

`library-v2-page.tsx:6154-6156` hat keinen `isError`-Zweig: `isLoading` false +
`artist` undefined wählt weiter `Loading…`, dauerhaft, ohne Meldung, ohne Retry.
`?artist=999999` (gelöschter Artist, altes Lesezeichen) → `api/library_v2.py:1615`
liefert 404 → die Seite dreht sich für immer. Gleiches Muster in
`AlbumTrackTable` (`:7956-7958`) und `HistoryModal` (`:2185-2188`, das auf einem
Fehlschlag die *falsche* Aussage `No recorded history for this artist yet.`
rendert).

#### iss29-C05 — Wanted-Views behaupten bei fehlgeschlagenem Fetch „every monitored track you want is already on disk"

`library-v2-page.tsx:4332`, `:4395-4405` — `isError` ungelesen. Fällt
`/api/library/v2/wanted` (`api/library_v2.py:1316`) aus, behauptet die
Missing-Ansicht positiv, dass nichts fehlt: eine Tatsachenaussage über die
Library, abgeleitet aus einem gescheiterten Request, auf genau dem Bildschirm,
den der Nutzer konsultiert, um zu entscheiden, ob Automatic Search Arbeit hat.

#### iss29-C06 — `find22-15` („ein Queue-Poll pro Artist") ist verletzt, sein Guard-Test läuft ins Leere — VERIFIZIERT

```
library-v2-page.tsx:6002:  libraryV2QueueStatusQueryOptions('artists', artistId)
library-v2-page.tsx:7880:  libraryV2QueueStatusQueryOptions('albums', albumId)
```

`:7880` sitzt in `AlbumTrackTable`, das pro aufgeklapptem Album einmal montiert
wird (`:6911`), mit `refetchInterval: 3000` (`-library-v2.api.ts:1502-1517`).
Der Guard `-ui/artist-queue-polling.test.ts:12` prüft
`not.toContain("libraryV2QueueStatusQueryOptions('albums', album.id)")` — der
Code schreibt aber `'albums', albumId`, das Literal kommt also nie vor und die
Assertion besteht vakuum. Sechs aufgeklappte Blöcke ≈ 140 Requests/min, jeder
mit `entity_track_ids` + Queue-Scan (`api/library_v2.py:4658-4696`) gegen genau
die SQLite-DB, deren einzelne Schreibsperre der bekannte Engpass dieses Projekts
ist. Die artist-weite Antwort enthält all diese Tracks bereits.
`library-v2-status.md:103` führt find22-15 als Verified.

#### iss29-E03 — Eine gelöschte Datei markiert alle Dateien eines Albums als `deleted`

`core/library2/maintenance_sync.py:532-538` mit der Aufweitung in `:264-278`:
`_resolve_links` expandiert ein `entity_type='album'`-Subject auf **jeden** Track
und **jede** nicht gelöschte Datei des Albums, und der Delete-Zweig setzt sie
alle auf `deleted`. `_fix_unwanted_content` (`core/repair_worker.py:3100-3149`)
löscht aber genau *eine* Datei und liefert `action: 'removed_content'`, das in
`_DELETE_ACTIONS` (`:41-51`) steht. Ein Upgrade bringt offene
album-scope-`unwanted_content`-Findings mit (`live_commentary_cleaner` erzeugt
sie mit `entity_type='album'`, und der Job steht **nicht** in `RETIRED_JOB_IDS`).
Folge: Album gilt als dateilos → `recompute_wanted` (`:593-612`) will das ganze
Album neu → die Wishlist lädt ein vollständig vorhandenes Album erneut, während
die echten Dateien wie Waisen aussehen.

#### iss29-E04 — Destruktives Repair löscht, was der Fuzzy-Pfad-Resolver liefert — ohne Root-Containment, ohne Revalidierung

`core/repair_worker.py:2063-2088` (`_remove_native_repair_file`):
`resolved = target if os.path.isfile(target) else resolve_lib2_path(target, ...)`,
dann `os.remove(resolved)`. Der Suffix-Walk des Resolvers probiert die
Basisverzeichnisse **Transfer-Ordner zuerst** (`core/library/path_resolver.py:101-104`),
und Importe landen unter `soulseek.transfer_path` im selben
`Artist/Album/…`-Layout (`core/imports/paths.py:523`). Szenario: `corrupt_audio`-
Finding auf einer bereits verschwundenen Library-Datei, während unter Transfer
ein frisch heruntergeladener Ersatz liegt → der Walk trifft die Transfer-Kopie
und `os.remove` zerstört den Download, `sync_repair_change` verbucht es als
konvergiert. Der Gegenbeweis für die Absicht steht daneben:
`core/library2/file_delete.py` verlangt Containment in `library.music_paths`
(`_containing_root:110-120`) und revalidiert Größe + mtime gegen ein
Preview-Token (`:568-577`). `_remove_native_repair_file` tut beides nicht;
dasselbe gilt für `_fix_unwanted_content` (`:3128-3131`) und
`_fix_short_preview_track` (`:2203-2212`).

#### iss29-E05 — Reorganize löscht `.lrc`-Lyrics, die Library V2 selbst geschrieben hat

`core/library_reorganize.py:2255` (`_TRACK_SIDECAR_EXTS`) und `:2342-2354`
(`_delete_track_sidecars`, aufgerufen aus `_finalize_track:1627`). Der volle
Reorganize-Lauf staged nur das Audio (`_stage_track:1539`), trägt die `.lrc`
also nicht mit — und löscht sie danach an der Quelle. lib2 schreibt genau diese
Dateien (`core/library2/lyrics.py:99,131`). „Fetch Lyrics" gefolgt von
„Reorganize" auf derselben Seite leert die Lyrics-Spalte. `_fix_path_mismatch`
(`core/repair_worker.py:3729-3738`) und `_rename_to_basename`
(`core/repair_jobs/track_number_repair.py:1058-1064`) *verschieben* das Sidecar —
Reorganize ist der Ausreißer.

#### iss29-E06 — `mark_file_verification_status` scannt die ganze Tabelle und statet das Dateisystem, während die Schreibsperre gehalten wird

`core/library2/verification.py:39-53`, gerufen aus `web_server.py:9357-9362`
*innerhalb* der offenen Schreibtransaktion (`UPDATE library_history` bei `:9350`).
Es liest **jede** `lib2_track_files`-Zeile und ruft `resolve_lib2_path` für jede
nicht passende; `resolve_library_file_path` beginnt mit `os.path.exists`
(`path_resolver.py:232`) und suffix-walkt für fehlende Dateien alle Basisdirs ×
Segmente. Auf einer 30k-Library über SMB/NFS hält ein einziger „Approve"-Klick
die Schreibsperre über ≥30k Netzwerk-Stats — dieselbe Anti-Pattern-Klasse wie
der bekannte Deadlock, an einer neuen lib2-Stelle.

#### iss29-E07 — Abgebrochener atomarer Speichervorgang wird als erfolgreicher Tag-Write gemeldet

`core/metadata/common.py:314-324` liefert `False` für „Original unangetastet,
Tags NICHT geschrieben". Drei dateiverändernde Aufrufer verwerfen den Wert
(`core/tag_writer.py:553-555`, `core/repair_jobs/track_number_repair.py:989,1027`) —
`core/replaygain.py:326` und `core/library/file_tags.py:487` prüfen ihn, der
Vertrag ist also andernorts verstanden. (a) lib2 Write Tags zählt `written` und
persistiert einen Snapshot (`core/library2/retag.py:411-415`) — die UI sagt
„N written", die Datei ist unverändert, die Tag-Gap-Zelle zeigt die Lücke für
immer. (b) Schlimmer: `native_p3.py:247-261` fährt nach dem gescheiterten
Tag-Write unbeirrt mit `_rename_to_basename` fort — die Datei heißt danach
`07 - Song.flac` und trägt TRCK `3`; `fix_finding` löst das Finding auf, es wird
nie wieder erkannt.

### 29.3 Minor

| ID | Befund | Ort |
|---|---|---|
| iss29-A05 | Jeder `/import/status`-Poll nimmt die Schreibsperre (`ensure_bootstrap_schema` endet auf `INSERT OR IGNORE`) — 1×/s pro offenem Tab gegen die Artwork-Worker | `core/library2/bootstrap.py:105-107,176-191` |
| iss29-A06 | Autostart-Backoff verdoppelt sich auch bei No-op-Ticks und wird nie zurückgesetzt; Fresh Install wartet nach einem späten Erst-Scan ~22 min | `web_server.py:32710-32714` |
| iss29-A07 | Toter Feature-Flag-Aufruf, Rückgabewert verworfen (einziger Konsument von `config_get`) | `core/library2/bootstrap.py:485-487` |
| iss29-B04a | YouTube-Videos-Quelle liefert `db_artists: []`, „In Your Library" verschwindet, solange das Icon aktiv ist (vorbestehende Upstream-Form) | `-search.use-controller.ts:236` |
| iss29-B04b | Such-Cache 600 s: ein währenddessen importierter Artist zeigt bis zu 10 min auf Artist-Detail statt V2 | `core/search/cache.py:18` |
| iss29-B04c | Für lib2-native Artists geht `'id': v2_id` an `/api/artist/<id>/image`, den generischen Resolver, der IDs an Provider weiterreicht → falsches Foto | `core/search/orchestrator.py:352-356`, `web_server.py:11475-11477` |
| iss29-B05 | ldp-05-Regression: der Rich-Header-Vorwahl für Such-Ankünfte ist tot, `openArtistSearch()` setzt hart auf `compact`/`library`/`table` zurück | `library-v2-page.tsx:4857-4865` |
| iss29-B06 | Kommentare und Doku behaupten weiter die zurückgenommene Weiterleitung; §42 führt ldp-01 und ldp-09 unverändert als Implemented | `-library-v2.types.ts:45-46`, `library-v2-page.tsx:4868,5908,6132`, `library-v2-status.md` §42 |
| iss29-B07 | `/library-v2` fehlt in `_DEEPLINK_VALID_PAGES`, ein Lesezeichen wird vom Vanilla-Shell zu `dashboard` aufgelöst; React gewinnt das Rennen meist — **Ausgang SPEKULATIV**, die Lücke selbst nicht | `webui/static/init.js:2939-2959` |
| iss29-B08 | Media-Player-„Go to artist" ist bei lib2-Wiedergabe dauerhaft deaktiviert (`artist_id: null` ist korrekt, aber nichts routet auf `/library?artist=`) | `library-v2-page.tsx:8803`, `webui/static/media-player.js:161-171` |
| iss29-B09 | Filterfeld ist unkontrolliert (`defaultValue`), desynct bei Browser-Back von der URL | `library-v2-page.tsx:3845-3857` |
| iss29-B10 | Permission-Gate kann sich selbst im Kreis umleiten, wenn die Home-Page zu `library` normalisiert, `library` aber nicht erlaubt ist — Form vorbestehend (identisch zu Upstream), durch den `library-v2`-Alias neu erreichbar; ~~**SPEKULATIV**~~ **bestätigt: terminiert nicht** (der Routen-Test hängt vitest auf, statt zu scheitern). **Behoben** zentral in `getProfileHomePath` — [Status §47.3](library-v2-status.md#473-iss29-b10--bestätigt-kein-bounce-eine-endlosschleife) | `webui/src/routes/library/route.tsx:17-22`, `webui/src/platform/shell/bridge.ts:45-48` |
| iss29-C07 | Bulk-Bar Monitor/Unmonitor/ReplayGain melden bei Teilerfolg Totalausfall (`Promise.all` statt `allSettled`) | `library-v2-page.tsx:7613-7658` |
| iss29-C08 | UI-04 fordert eine Bulk-Quality-Profile-/inherit-Aktion; die Bulk-Bar hat sie nicht (Backend und Einzelpfad existieren) | `library-v2-page.tsx:7606-7677` |
| iss29-C09 | UI-Preferences-Mutationen scheitern lautlos, kein `onError`, kein Konsument rendert einen — M-12 ist als Implemented geführt | `library-v2-page.tsx:7286-7307` |
| iss29-C10 | Nicht-Admin-Profile bekommen die volle Toolbar angeboten; jeder Klick 403, weil `/library/v2/enabled` kein `can_write` liefert | `api/library_v2.py:264-269,349-351` |
| iss29-E08 | Rename-Kollision wird als erfolgreicher Fix gezählt (`None` bedeutet sowohl „nichts zu tun" als auch „Ziel existiert, übersprungen") | `core/repair_jobs/track_number_repair.py:1040-1053` |
| iss29-E09 | `.lrc` wird beim dd28-29-Rollback nicht mitgerollt — Audio zurück, Lyrics verwaist am neuen Namen | `core/repair_jobs/native_p3.py:279` |
| iss29-E10 | Nativer Track-Number-Fix committet den Katalog, bevor er die Datei prüft; bei nicht gemountetem Root wird umnummeriert, ohne dass sich etwas ändert | `core/repair_worker.py:2465-2504` |
| iss29-A08 | ~~**SPEKULATIV**~~ **bestätigt**: Claim kann während `post_import` veralten → Lease verloren, volle Migration erneut. Der kritische Beat ist nicht der 20-Alben-Beat, sondern der **50-Dateien**-Beat von `precache_tag_cache` — der letzten Stufe vor `mark_done`. **Behoben** durch `_ClaimKeepalive` + `touch_claim` — [Status §47.2](library-v2-status.md#472-iss29-a08--bestätigt-liveness-hing-an-der-gesprächigkeit-der-stufe) | `core/library2/bootstrap.py:530-534`, `core/library2/tag_cache.py:179` |
| iss29-A09 | ~~**SPEKULATIV**~~ **gemessen: kein Defekt.** Working Set bei 100k Tracks / 20k Alben = **29,6 MiB**; `_legacy_rows` walkt bereits in begrenzten Keyset-Batches. Die Maps verhindern doppelte INSERTs beim Re-Import (§62) und werden dafür nicht umgebaut — [Status §47.4](library-v2-status.md#474-iss29-a09--gemessen-kein-defekt) | `core/library2/importer.py:1193-1344` |

### 29.4 Geprüft und korrekt befunden

Ausdrücklich **keine** Befunde, damit der nächste Durchgang sie nicht erneut
untersucht: `awaitBulkJobState` (M-14), der `useScopedSearchBanner`-Sequenzguard
für In-Session-Überlappung, Discography-Refresh-409-Attach und der
`shouldAutoFetchDiscography`-Loop-Guard, die Interactive-Search-Fan-out-/
Teilfehler-/0-Treffer-Pfade, der Force-Confirm-Scope (§52.12.4),
`meetsCutoffOnly`/`profileTargetRank`, `QualityProfilePicker`-Vererbung,
`MirrorStatusBanner`, `user_explicit` plus transaktionale Outbox im
Monitor-Endpunkt; `_reconcile_legacy_snapshot`-Run-ID-Semantik, `_legacy_rows`-
Commit-Grenzen, `checkpoint()`-Sichtbarkeitsreihenfolge, Fresh-Install-Division
durch Null (überall geguardet), `should_stop_autostart` (mig-05),
`reclaim_abandoned_claim` (mig-04), `_guard()` gegen Nicht-Admin-POSTs; der
`/library-v2`-Redirect erhält den kompletten Querystring, `coercedString` deckt
die All-Digits-Falle für `q`/`discover`/`discoverName`, alle übrigen Suchparameter
sind erreichbar und round-trippen, der Route-Loader blockiert nicht auf einem
Fetch-Fehler, Stats verlinkt korrekt in-app auf Upstreams Artist-Detail, und die
`navigateToArtistDetail`-not-called-Assertions pinnen Upstreams React-Ownership,
nicht den zurückgezogenen ldp-01-Vertrag; `core/library2/file_delete.py`
(ADR-05), `_present_track_files`/`writable_file_rows` (dd28-38),
`_fix_orphan_file`, `subject_details` mit `file_id`, und jeder erzeugte
`finding_type` hat einen Handler außer den drei bewusst report-only-Typen.

### 29.5 Nachtrag: lib2-Backend und Datenschicht

Das fünfte Audit lief länger als die übrigen; seine Befunde stehen deshalb hier
gesammelt statt in 29.1–29.3 einsortiert. Prioritätsreihenfolge gilt trotzdem.

#### iss29-D01 (Blocker) — Schreibtransaktion bleibt über Provider-HTTP-Calls offen — VERIFIZIERT

`core/library2/native_enrich.py:255-290`: die Anchor-Schleife ruft
`_persist_identity` (ein blankes `UPDATE lib2_artists`, `:152-154`) und geht dann
zum nächsten Anchor — dessen `anchor_resolver` zwei blockierende
Provider-Roundtrips macht (`core/metadata/album_tracks.py:616-625`). In der
ganzen Funktion `resolve_and_enrich_native_artist` steht **kein einziges
`conn.commit()`** (nachgeprüft). Bei `isolation_level=""` öffnet Pythons
`sqlite3` bei DML implizit eine Transaktion und gibt sie erst beim Commit frei.

Das ist exakt die Klasse, die dieselbe Datei 30 Zeilen später beschreibt
(`native_enrich.py:625-634`): *„Release the writer before the provider walk …
Holding it here deadlocked this thread against itself: the provider clients cache
their responses in the SAME database through their own connection … it then
waited out the full 30 s busy timeout, and every other writer in the process
waited with it."* Siehe auch die bereits dokumentierte Produktivstörung derselben
Ursache.

Betroffene Population: ein unmapped nativer Artist mit ≥2 Provider-Anchors —
also eine Feature-Credit-Zeile auf einem Album, das bereits gegen Spotify *und*
MusicBrainz/Deezer gematcht ist; genau die Gruppe, für die der Job existiert
(`_artist_catalog_anchors`, `:169-204`). Zwei Einstiege: der Button
`POST /api/library/v2/maintenance/reconcile-unmapped-artists`
(`api/library_v2.py:3246-3262`) **und** der automatische Post-Import-Trigger mit
120 s Debounce (`core/library2/unmapped_trigger.py`). Bei *k* Anchors ist das
Fenster (*k*−1) × (Provider-Latenz + bis zu 30 s Busy-Timeout), in dem jeder
andere Writer im Prozess „database is locked" bekommt.

Fixrichtung: wie `enrich_native_entity_for_service` erst alle Anchor-Identitäten
netzseitig einsammeln und dann in einer Transaktion schreiben — oder direkt nach
jedem `_persist_identity` committen; das Re-Read in `:282-284` funktioniert auf
einer frischen Transaktion genauso.

#### iss29-D02 (Major) — Wishlist→lib2-Auflösung ist O(Wishlist-Zeilen × lib2-Tracks) mit einem `json.loads` pro Paar

`core/library2/monitor_sync.py:282-290` materialisiert den ganzen
Track-Katalog und läuft ihn für **jeden** Deskriptor erneut durch, mit
`_provider_ids` → `json.loads` pro Zeile (`:241-258`). Nur Deskriptoren mit
`source_info.lib2_track_id` kürzen ab (`:298-306`) — Zeilen aus der Legacy-
Wishlist-UI, aus Playlist-Downloads oder aus `POST /api/wishlist` haben diesen
Marker nicht. „Clear Wishlist" mit 1.000 Legacy-Zeilen gegen 50k Tracks = 50 Mio.
Iterationen; der stündliche `reconcile_track_wishlist` (`:809-835`) zahlt es
jede Stunde auf einer gehaltenen Verbindung. Fixrichtung: einmalig einen
`{(namespace, value): [track_id, …]}`-Index bauen (ein JSON-Parse pro Track) und
per Dict auflösen; die Ambiguitätserkennung bleibt über die Listenlänge erhalten.

#### iss29-D03 (Major) — `POST /<entity>/<id>/monitor` macht den Provider-Walk auf dem Request-Thread

`api/library_v2.py:2824-2833` ruft `resolve_tracklist` inline. Die Album-Detail-
Route 1.000 Zeilen darüber lehnt genau das ab und begründet es
(`api/library_v2.py:1822-1834`: *„Off the request thread … doing it inline made
opening a release hang the page"*). Das Monitoren eines reinen
Discography-Releases — die normale Lidarr-Geste, für die dieser Branch existiert
— blockiert damit einen Web-Worker über die volle Provider-Kette (bis 25–30 s
Budget), und der Browser sieht ein Timeout auf einem Toggle, das der Server
danach zu Ende führt. Der identische Aufruf im Bulk-Pfad (`:3517`) ist in
Ordnung, weil er bereits in einem Hintergrundjob sitzt. Kein Lock-Problem:
`resolve_tracklist` committet vorher (`completeness.py:493`) und die Route hat
noch nichts geschrieben. Fixrichtung: `_schedule_tracklist_resolve`
(`api/library_v2.py:1724`) wiederverwenden und den Client pollen lassen.

#### iss29-D04 (Major) — Artist-Listen-Suche ist quadratisch in der Artist-Zahl

`core/library2/queries.py:155-170`: ein korreliertes `EXISTS` über
`COALESCE(member.canonical_artist_id, member.id)=a.id` — kein Index kann das
bedienen, der vorhandene ist `idx_lib2_artists_canonical(canonical_artist_id)`
(`core/library2/schema.py:815-818`) — kombiniert mit `LIKE '%…%'`, das
`idx_lib2_artists_name` ebenfalls ausschließt. Dieselbe `where`-Klausel wird in
der `page_artists`-CTE (`queries.py:208-214`) ein zweites Mal ausgewertet.
`GET /api/library/v2/artists?search=a` auf 10.000 Artists ≈ 10⁸ Zeilenvergleiche,
zweimal, auf dem Request-Thread, bei jedem Tastendruck. Nebenbefund in denselben
Zeilen: der Suchbegriff wird ohne `ESCAPE` in das `LIKE`-Muster interpoliert, `%`
und `_` aus der Nutzereingabe wirken also als Wildcards.

#### iss29-D05 (Major, latent) — `resolve_tracklist` committet die Verbindung des *Aufrufers*

`core/library2/completeness.py:469,482,493,525`. Der Commit ist für die Sperre
richtig, aber `conn` gehört dem Aufrufer und die Funktion hat keinen
Eigentumsvertrag. Alle vier heutigen Aufrufer sind sicher — aber jeweils nur
durch ihre Position in der Schleife (`api/library_v2.py:2831` hat noch nichts
geschrieben; `:3517` und `discography.py:730` haben in der Vorrunde committet;
`completeness.py:576` hat eine eigene Verbindung). `mirror_tracks_wishlist`
dokumentiert dieselbe Eigenschaft ausdrücklich für sich selbst
(`wishlist_mirror.py:238-241`); `resolve_tracklist` nicht. Ein harmlos
aussehendes Refactoring — den Monitor-UPDATE über den Resolve-Aufruf ziehen —
committet dann still eine halb angewandte Mutation.

#### Minor (Backend)

| ID | Befund | Ort |
|---|---|---|
| iss29-D06 | `enrich_native_entity_all_services` prüft auf `external_id`, das die aufgerufene Funktion nie zurückgibt → liefert immer `{}`, entgegen dem eigenen Docstring (beide Aufrufer verwerfen den Wert, daher heute folgenlos) | `core/library2/native_enrich.py:684-692,739-740` |
| iss29-D07 | `retry_failed`-Docstring beansprucht einen Supersede-Schutz, den die Anweisung nicht hat; die Garantie kommt tatsächlich erst aus `drain` → bei Backlog > `limit=500` oder einem Aufrufer ohne Drain lebt dd28-13 wieder auf | `core/library2/mirror_outbox.py:388-398` |
| iss29-D08 | `file-tags/edit` wirft `AttributeError` bei `{"key": 5}`; es gibt **keinen** `errorhandler` im Projekt, der Client bekommt Flasks HTML-500 statt der JSON-Fehlerform | `api/library_v2.py:1913-1916` |
| iss29-D09 | Sieben mutierende Routen benutzen `request.json` statt `request.get_json(silent=True)` → HTML 415/400 bei fehlendem `Content-Type` | `api/library_v2.py:1675,2810,3430,3712,3736,4307,4351` |
| iss29-D10 | Verschluckte Tag-Cache-Fehler ohne `conn.rollback()`; die nächste Anweisung ist ein LRClib- bzw. NAS-Zugriff — dieselbe Klasse wie iss29-D01, zweiter Ordnung | `core/library2/lyrics.py:112-119`, `core/library2/replaygain.py:136-144` |
| iss29-D11 | `prune_done` läuft auf dem normalen Mirror-Pfad nie; die Produktiv-DB hält bereits 771 `done`-Zeilen gegen `keep=500`, und `_superseded_ids` scannt diese Historie bei jedem Drain | `core/library2/mirror_outbox.py:401-407` |
| iss29-D12 | N+1 in `/artists/<id>/duplicates`: `_file_summary` zweimal pro Paar statt des vorhandenen `primary_file_rows` | `api/library_v2.py:4227-4248` |
| iss29-D13 | `autolink` fällt für nicht-ASCII-Namen immer auf den Full-Table-Scan zurück (SQLites `lower()` ist ASCII-only), plus ein nicht indexierbares `external_ids LIKE '%…%'` — beides korrekt, aber im Hot Loop jedes fertigen Downloads. **Bestätigt und untertrieben:** `EXPLAIN` meldet für `lower(name)=?` **SCAN**, kein Index kann den Ausdruck bedienen — es scannte also für *jeden* Namen, ASCII eingeschlossen; Nicht-ASCII zahlte den Python-Scan obendrauf (168 ms bei 100k Artists). **Namensteil behoben** über `lib2_artists.name_key` + Index + Backfill (0,004 ms) — [Status §47.1](library-v2-status.md#471-iss29-d13--bestätigt-und-schlimmer-als-beschrieben). **Der `external_ids LIKE`-Teil bleibt offen** (Umfang bewusst begrenzt): weiterhin ein Scan, feuert für Nicht-Spotify-Provider-IDs | `core/library2/autolink.py:147-150,164-174` |

#### Als sauber belegt (Negativnachweis zur Write-Lock-Klasse)

Der Agent hat **jede** aus `core/library2/` erreichbare Provider-/Netzwerkstelle
geprüft und den Writer davor jeweils freigegeben gefunden:
`completeness.py:494`, `native_enrich.py:552/638/651/663/723/949`,
`discography.py:460/730`, `mb_reconcile.py:324`,
`track_identity_reconcile.py:137`, `artwork.py:492/537/1028` (letzteres
absichtlich per dd28-04), sowie die Happy Paths von `replaygain.py` und
`lyrics.py`. iss29-D01 ist die einzige Ausnahme. Ebenfalls geprüft und **kein**
Befund: Rowid-Wiederverwendung (alle lib2-Tabellen sind `AUTOINCREMENT`),
`resolve_alias_group` kann nie leer zurückkommen (also kein `IN ()` in den ~20
Buildern), keine `IN`-Liste überschreitet SQLites Variablenlimit (der einzige
library-weite Fall läuft über das chunk-sichere `sql_util.select_existing_ids`),
`_owned_import`s scheinbar ungebundenes `owner` (gebunden bei
`api/library_v2.py:996`), und `ReportedPathHealth.to_public_dict` redigiert
korrekt.

---

## 30. Finaler Multi-Agent-Audit des Branch-HEADs (4. August 2026)

Dieser Read-only-Durchgang prüfte den finalen Stand `6c7066cbb` gegen Guide,
Featurevertrag und Statusdatei. Drei unabhängige Teilprüfungen deckten
Import/Backend, Search/Upgrades/Repair sowie UI/Navigation/Tools ab. Die
Diagnosen unten sind nach Root Cause getrennt; ihr aktueller
Remediationstatus steht ausschließlich in
[status.md §48](library-v2-status.md#48-finaler-multi-agent-audit-des-branch-heads-4-august-2026).

**Herkunft ist nicht gleich Release-Relevanz:** Dieser Audit beschreibt den
Zustand des Branch-HEADs, nicht automatisch durch den Branch eingeführte
Regressionen. Der Vergleich mit der `dev`-Basis `d0cb43db5` zeigt:
`core/auto_import_worker.py`, `core/imports/routes.py` und
`core/imports/file_ops.py` sind byteidentisch. iss30-I01 sowie I04–I06 sind
daher geerbte Baseline-Fehler. Branch-neu ist die Library-V2-Materialisierung
über `core/library2/autolink.py`; nur iss30-I02/I03 sind in diesem Cluster
Library-V2-Integrationsfehler. Bei I01/I04/I06 vergrößert der Branch allerdings
die Downstream-Folgen; das ändert nicht die Herkunft ihrer Root Causes.

### 30.1 Blocker

#### <a name="iss30-i01"></a> iss30-I01 — Fehlgeschlagenes Replacement löscht die bisherige gute Datei

**Ort:** `core/imports/file_ops.py:142-205`,
`core/imports/pipeline.py:1583-1636,1671`

**Herkunft:** geerbt, keine Library-V2-Regression. `file_ops.py` hat auf Basis
und HEAD denselben Blob `725ab6c0d9`; der Branch erweitert lediglich die
Replacement-/Katalogpfade, die auf diesen gemeinsamen Helper treffen.

`safe_move_file` entfernt ein vorhandenes Ziel vor `os.replace`. Die Pipeline
löscht dasselbe Ziel zusätzlich bereits bei Quality-Upgrade, Enhance, Force
und dem Metadata-Fallback, obwohl der eigentliche Move erst deutlich später
erfolgt. Eine ENOSPC-Failure-Injection reproduziert den Datenverlust:

```text
OSError: [Errno 28] No space
source_exists=True destination_exists=False destination_bytes=LOST
```

Die neue Datei bleibt im Staging erhalten, die verifizierte Bibliotheksdatei
ist jedoch bereits weg. Das widerspricht der Invariante in Guide §5, dass die
alte Datei bis zum vollständig erfolgreichen Import erhalten bleibt.

**Korrekturvertrag:** Kein Pre-Unlink. Same-FS direkt atomar ersetzen;
Cross-FS in eine Geschwister-Tempdatei kopieren, flush/fsync ausführen und erst
dann atomar veröffentlichen. Die Quelle wird ausschließlich nach erfolgreicher
Publikation entfernt. Failure-Injection muss die alten Bytes unverändert
nachweisen.

### 30.2 Major — Import und Autolink

#### <a name="iss30-i02"></a> iss30-I02 — V2-Autolink materialisiert Auto-Import-Tracks als falsche Alben

**Ort:** `core/auto_import_worker.py:1870-1944`,
`core/library2/autolink.py:553-595`

**Herkunft:** Library-V2-Integrationsfehler. Der unveränderte Auto-Importer
liefert seinen bisherigen Kontext; der branch-neue V2-Autolink konsumiert
diesen Kontext falsch.

Der Auto-Importer liefert das kanonische Album top-level als `spotify_album`;
im Track steht nur die Album-ID. Autolink liest dagegen ausschließlich
`track_info.album` und fällt bei dessen Fehlen auf den Tracktitel zurück. Der
Real-SQLite-Repro erzeugte für „Song One“ ein Album „Song One“, keine
Album-Provider-ID und **keine** Zeile für den wirklichen Albumtitel. Ein Album
mit mehreren Tracks kann so in mehrere künstliche Single-Alben zerfallen.

**Korrekturvertrag:** Den Kontext über die gemeinsamen Import-Helper
normalisieren; das kanonische top-level Album und dessen qualifizierte IDs
haben Vorrang. Ein Integrationstest führt zwei Tracks desselben Auto-Imports
durch Pipeline und Autolink und erwartet genau ein Album.

#### <a name="iss30-i03"></a> iss30-I03 — V2-Autolink verliert den kanonischen Auto-Import-Provider

**Ort:** `core/auto_import_worker.py:1871-1876`,
`core/imports/album.py:294-309`, `core/library2/autolink.py:61-91,612-653`

**Herkunft:** Library-V2-Integrationsfehler. Der top-level Provider existierte
bereits vor dem Branch; erst der neue Autolink ignoriert ihn und rät einen
Namensraum.

Importkontexte definieren `source` top-level als autoritativ. Autolink liest
nur `track_info.provider`. Fehlt dieser Wert, klassifiziert die Shape-Heuristik
alphanumerische IDs standardmäßig als Spotify. JioSaavn-, Qobuz- und ähnliche
IDs können dadurch in `spotify_id` landen. Numerische Deezer-IDs fallen nur
zufällig nicht in denselben Fehler.

**Korrekturvertrag:** Provider über `get_import_source(context)` oder explizit
qualifizierte Embedded-Tags bestimmen; unbekannte IDs nie erraten. Tests müssen
JioSaavn, Qobuz und Deezer durch den echten Auto-/Manual-Album-Autolink führen.

#### <a name="iss30-i04"></a> iss30-I04 — Terminal abgelehnte Auto-Imports werden als abgeschlossen gezählt

**Ort:** `core/imports/pipeline.py:453-481`,
`core/auto_import_worker.py:669-686,1954-1955,1991`,
`web_server.py:41326-41330`

**Herkunft:** geerbter Auto-Import-Fehler. Callback-Wiring, bedingungsloses
`processed += 1` und `return processed > 0` stammen aus den April-/Mai-
Commits und sind in der Branchbasis identisch vorhanden.

Der Branch verschärft den vorhandenen Fehler: `1f2c4aa76` ergänzt eine weitere
vom Worker ignorierte Rejection-Klasse, und die falsche grüne History kann nun
zusätzlich von V2-Katalog und Acquisition-Status abweichen. Der Root Cause
bleibt dennoch vorbestehend.

Die gemeinsame Pipeline meldet Integrity-, AcoustID-, Silence-, Race- und
Context-Rejections über Context-Flags und normale Rückkehr. Auto-Import erhöht
nach jedem Callback trotzdem bedingungslos `processed`; bereits ein Callback
macht den ganzen Kandidaten erfolgreich. Ein Repro mit
`_acoustid_quarantined=True` lieferte `process_matches=True`, obwohl kein
Finalpfad entstand und die Quelldatei liegen blieb. Die History zeigt dann
`completed`, und der Folder-Hash wird nicht erneut verarbeitet.

**Korrekturvertrag:** Nach jedem Callback `import_rejection_reason(context)`
und den realen Finalpfad auswerten. Per-Track-Ergebnisse persistieren;
`completed` erst setzen, wenn alle zugeordneten Dateien erfolgreich
synchronisiert sind. Partial und Failed müssen unterscheidbar bleiben.

#### <a name="iss30-i05"></a> iss30-I05 — „Approve“ und „Approve All“ führen nie zum Import

**Ort:** `core/auto_import_worker.py:643-708,962-971,2130-2156`,
`web_server.py:41478-41490`

**Herkunft:** geerbter Auto-Import-Fehler. Sowohl `approve_item` als auch
Approve-All und der erneute Confidence-Pfad existieren unverändert in der
`dev`-Basis.

Approval setzt nur die History-Zeile auf `approved`. Dadurch ist der Ordner
beim nächsten Scan zwar wieder zulässig, läuft aber erneut durch dieselbe
Confidence-Entscheidung und erzeugt wieder `pending_review`. Reproduziert wurde
die Statusfolge `approved → pending_review`, ohne Pipeline-Callback.

**Korrekturvertrag:** Approval als atomare, einmal konsumierbare Entscheidung
am Folder-/Content-Hash speichern und beim Folgescan nur den
Confidence-Grenzwert übersteuern; alle Safety-Gates bleiben aktiv. Approve,
Approve All, Rejection nach Approval und Exactly-once-Konsum benötigen Tests.

#### <a name="iss30-i06"></a> iss30-I06 — Manual Import vertraut beliebigen absoluten Client-Dateipfaden

**Ort:** `core/imports/routes.py:517-546,610-657`,
`web_server.py:41292-41316`

**Herkunft:** geerbter Manual-Import-Fehler, nicht Auto Import und nicht
Library V2. `routes.py` hat auf Basis und HEAD denselben Blob `1d87a2ddcc`.

Album- und Single-Import prüfen nur `os.path.isfile(full_path)`. Ein
manipulierter Request kann deshalb eine beliebige serverlesbare Audiodatei
außerhalb des konfigurierten Stagings an Pipeline, Move, Retag und mögliche
Löschpfade übergeben; Symlink-Escapes sind ebenfalls möglich. Der Repro rief
die Pipeline erfolgreich mit `outside-library.flac` auf.

**Korrekturvertrag:** Staging und Datei mit `Path.resolve(strict=True)`
kanonisieren und Containment prüfen. Bevorzugt sendet der Client nur eine
opaque Scan-ID; der Server löst sie gegen den aktuellen Scan neu auf. Tests:
Outside-Root, `..`, Symlink-Escape und gefälschtes Match-Payload.

### 30.3 Search, Upgrade-Semantik und Repair-Tools

#### <a name="iss30-s01"></a> iss30-S01 — Automatic Search erkennt Upgrades, ersetzt aber nicht nach realer Qualität

**Ort:** `core/library2/wishlist_mirror.py:96-176`,
`core/downloads/task_worker.py:72-106`, `core/downloads/candidates.py:213-240`,
`core/imports/guards.py:164-215`, `core/imports/pipeline.py:1576-1696`,
`core/imports/file_ops.py:445-474`

Der Upgrade-Scan bewertet die vorhandene Primärdatei korrekt genug, um einen
Wishlist-Kandidaten zu erzeugen. Diese Entscheidung wird jedoch nicht als
Replacement-Vertrag bis zum Import transportiert. Der Import prüft nur, ob
die neue Datei irgendeinem Profilziel entspricht, und vergleicht bei gleichem
Zielpfad anschließend ausschließlich grobe Extension-Tiers.

Konkrete Folgen:

- FLAC 16 Bit → FLAC 24 Bit und MP3 128 → MP3 320 gelten als gleiche Stufe und
  werden verworfen;
- MP3 → FLAC landet unter einem anderen Pfad, ohne die alte normale V2-Datei
  sicher zu retiren;
- ein Kandidat kann akzeptabel, aber nicht strikt besser als die vorhandene
  Datei sein;
- das Entfernen über `original_file_path` greift nur für den separaten
  Enhance-Pfad.

Damit ist die UI-Abfolge „Upgrade Scan → Wishlist Process“ zwar korrekt, die
versprochene Lidarr-Semantik aber nicht end-to-end erfüllt.

**Korrekturvertrag:** Eine zentrale serverseitige `UpgradeDecision` vergleicht
beide realen `AudioQuality`-Werte mit effektivem Profil, Rangfolge und Cutoff.
Sie trägt die alte konkrete Track-Datei als Replacement-Ziel bis zum
verifizierten Import. Tests: FLAC16→24, MP3 128→320, MP3→FLAC,
gleich/schlechter, Custom-Rangfolge, Quarantänefehler und exakt eine retirte
Primärdatei.

#### <a name="iss30-s02"></a> iss30-S02 — Track-Interactive-Search kann ein ganzes Album an einen Track binden

**Ort:** `webui/src/routes/library/-ui/interactive-search.tsx:563-591,807-857`,
`webui/src/routes/library/-library-v2.api.ts:2220-2227`,
`web_server.py:7358-7455`, `core/library2/grab_context.py:92-109`,
`core/library2/autolink.py:549-560,614-619`

Der Track-Dialog zeigt Album-Ergebnisse mit aktivem Download-Button. Beim Grab
sendet der Client alle Album-Tracks, der Server gibt jedem Eintrag jedoch
denselben ausgewählten `lib2_track_id`-Kontext. Der Grab-Context überschreibt
Titel, Nummer und Album mit dem Zieltrack; Autolink bindet jede fertige Datei
direkt an diese eine Track-Zeile. Mehrere Dateien können sich damit gegenseitig
überschreiben, überspringen oder falsch demselben Track zugeordnet werden.

**Korrekturvertrag:** Bei gesetzter `lib2_track_id` Album-Ergebnisse
serverseitig ablehnen und in der UI ausblenden. Alternativ darf der Server
exakt einen stark gematchten Bundle-Eintrag extrahieren. Der aktuell tote
`autoGrabBest`-Filter schützt den echten Interactive-Pfad nicht.

#### <a name="iss30-s03"></a> iss30-S03 — Scoped Automatic Search zeigt einen laufenden Job als Fehler

**Ort:** `api/library_v2.py:4597-4605`,
`webui/src/routes/library/-library-v2.api.ts:1486-1512`,
`webui/src/app/api-client.ts:24-33`,
`webui/src/routes/library/-ui/library-v2-page.tsx:6674-6706`

Der Server antwortet bei demselben bereits laufenden Scope korrekt mit 409
und dessen `job_id`. Der globale Helper akzeptiert das mittels
`throwHttpErrors:false`; der scoped Helper nicht. `ky` wirft deshalb vor dem
Body-Parsing, und ein zweiter Tab oder Remount zeigt „Search failed“, obwohl
der richtige Job weiterläuft.

**Korrekturvertrag:** 200 und 409 als Attach-Vertrag behandeln und in beiden
Fällen die `job_id` pollen. API- und UI-Test für den Duplicate-Start ergänzen.

#### <a name="iss30-s04"></a> iss30-S04 — Upgrade-Review und Automatic Search können verschiedene Profile bewerten

**Ort:** `core/library2/wishlist_mirror.py:298-338`,
`core/repair_jobs/lib2_upgrade_scan.py:125-160`

Automatic Search benutzt die effektiv projizierte Track→Album→Artist→Global-
Vererbung. Der Review-Finding-Pfad joint dagegen direkt
`t.quality_profile_id`. Bei Migration, Drift oder unvollständiger Projektion
entstehen andere Cutoffs, übersprungene Findings oder falsche Profilnamen —
gerade in den Reparaturfällen, für die Review existiert.

**Korrekturvertrag:** Beide Modi müssen denselben Live-Resolver und Evaluator
verwenden. Ein Paritätstest deckt jede Vererbungsstufe und absichtlich driftende
Kompatibilitätsspalten ab.

#### <a name="iss30-s05"></a> iss30-S05 — Metadata Gap Filler erreicht nach Track 500 dauerhaft nichts

**Ort:** `core/repair_jobs/native_p3.py:485-526,607-615`

Der aktive native Job schneidet die deterministisch sortierten Subjects mit
`[:500]` ab. Es gibt weder Keyset-Paging noch einen rotierenden Cursor oder
einen Ausschluss bereits offener Findings. Solange die ersten 500 Lücken nicht
behoben werden, untersucht jeder Lauf dieselbe Menge; Track 501+ bleibt
unsichtbar. `estimate_scope` deckelt ebenfalls auf 500 und verbirgt den Rest.

**Korrekturvertrag:** Keyset-Paging über alle Subjects oder persistenter
Cursor; alternativ bestehende aktive Findings aus der Arbeitsmenge entfernen.
Ein Mehrfachlauf-Test mit mindestens 1.001 Subjects beweist Fortschritt.

#### <a name="iss30-s06"></a> iss30-S06 — Prowlarr belegt eine noch 55-fach vorhandene Python-3.14-Default-Executor-Lücke

**Ort:** `core/prowlarr_client.py:141-155,229-259`,
`tests/test_prowlarr_search_hardening.py:135-168`; weitere Treffer in Tidal,
Qobuz, HiFi, Deezer, YouTube, SoundCloud, Lidarr sowie Torrent-/Usenet-Clients
(`rg "run_in_executor\\(None" core`: 55 Call-Sites insgesamt)

`check_connection`, `get_indexers` und insbesondere `search` schicken ihre
synchronen Requests über `loop.run_in_executor(None, ...)`. Genau diese
Default-Executor-Klasse wurde für serverseitige Torrent-Fetches bereits in
§25.4 entfernt: Unter der aktuellen Python-3.14.6-Runtime hängt
`asyncio.run()` anschließend in `Runner.close()` /
`shutdown_default_executor()`.

Der Prowlarr-Pfad reproduziert das unabhängig von Netzwerk und Prowlarr: Der
HTTP-Call ist auf eine sofort zurückkehrende Funktion gemockt, das Resultat ist
fertig, aber der isolierte Test terminiert selbst nach 15 Sekunden nicht. Der
Faulthandler zeigt den Besitzerloop in `asyncio.runners.Runner.close`. Selbst
die minimale Runtime-Probe
`asyncio.run(asyncio.to_thread(lambda: []))` überschreitet 20 Sekunden. Im
Produktionspfad mit langlebigem gemeinsamem Loop kann der Request zuvor
zurückkehren; jeder kurzlebige Besitzerloop sowie ein sauberer Loop-/Prozess-
Shutdown bleiben jedoch gefährdet. Deshalb stoppt auch die Full Suite
reproduzierbar bei 67 Prozent an diesem Test. Der statische Folgeaudit zeigt,
dass Prowlarr nur der erste im Collection-Order erreichte Beweis ist, nicht der
einzige verbliebene Default-Executor-Nutzer.

**Korrekturvertrag:** Einen zentralen begrenzten Prozess-Executor-Helper nach
dem Muster `_fetch_torrent_payload_async` bereitstellen und dessen
`concurrent.futures.Future` aus dem Owner-Loop pollen, ohne dessen
Default-Executor anzulegen. Zuerst müssen Prowlarr Connection, Indexer-Liste
und Search denselben Helper verwenden; danach ist jeder der 55 Treffer zu
migrieren oder mit einer begründeten Besitzerloop-Garantie zu schließen.
Regressionstests prüfen pro Clientfamilie `loop._default_executor is None`,
Erfolg, Timeoutfehler, Cancellation und sauberes `asyncio.run()`-Ende unter
Python 3.14.

### 30.4 UI, Rechte, Tool-Feedback und Browser-Gates

#### <a name="iss30-u01"></a> iss30-U01 — Die laut F-12 entfernte Import-Review-UI ist mutierend erreichbar

**Ort:** `webui/src/routes/library/-library-v2.types.ts:28-30`,
`webui/src/routes/library/route.tsx:38-44`,
`webui/src/routes/library/-ui/library-v2-page.tsx:3757-3761,4041-4345`,
`webui/src/routes/library/-library-v2.api.ts:150-225`

`/library?section=import-review` wird weiterhin validiert, prefetched und als
vollständige View gerendert. Resolve, Rescan, Assignment und Resume mutieren
Backendzustand. Ein Kommentar bestätigt, dass nur der sichtbare Tab entfernt,
der Deep Link aber absichtlich behalten wurde. Das widerspricht Guide §1/§5,
Features F-12 und dem bisherigen Status „Removed/Deleted“.

**Korrekturvertrag:** Section, Loader-Branch, Render-Branch und aktive
UI-Helfer entfernen. Ein negativer Routentest muss den alten Wert auf Artists
normalisieren und **keinen** Acquisition-Request auslösen.

#### <a name="iss30-u02"></a> iss30-U02 — Rich Bulk Edit kann teilweise committen und dennoch Totalausfall melden

**Ort:** `webui/src/routes/library/-ui/library-v2-page.tsx:7909-7952`,
`webui/src/routes/library/-library-v2.api.ts:1045-1061`,
`api/library_v2.py:3791-3864`

Das Modal startet je Track eine eigene Transaktion via fail-fast
`Promise.all`. Bei 200/409-Mischung ist der erste Track dauerhaft geändert,
aber der Catch invalidiert keine Queries und lässt Modal sowie Auswahl stale.
Ein früher Reject setzt `busy=false`, während andere Requests noch laufen;
ein Retry kann sich mit ihnen überlappen.

**Korrekturvertrag:** Bevorzugt ein atomarer Batch-Endpunkt. Andernfalls alle
Requests settlen lassen, immer passend invalidieren, Success-/Failure-IDs
anzeigen und nur das Failed-Subset retryen.

#### <a name="iss30-u03"></a> iss30-U03 — Bulk-Bar bleibt trotz `allSettled` nach Teilerfolg stale

**Ort:** `webui/src/routes/library/-ui/library-v2-page.tsx:7730-7759,7769-7849`

`fanOut` wartet inzwischen korrekt auf alle Requests, wirft bei Teilfehler aber
anschließend. `run` invalidiert nur im Success-Zweig. Erfolgreiche Monitor-,
Unmonitor-, ReplayGain- oder Quality-Profile-Writes sind daher committed,
bleiben in der Tabelle jedoch unsichtbar. Die frühere C07-Remediation löste das
Warten, nicht die Zustandswahrheit.

**Korrekturvertrag:** Settled-Ergebnis statt Throw-only zurückgeben; bei jedem
Teilerfolg invalidieren, Zahlen/IDs ausweisen und Failed-Subset retrybar halten.

#### <a name="iss30-u04"></a> iss30-U04 — `can_write=false` erzeugt keine wirklich read-only Library

**Ort:** `api/library_v2.py:250-270,349-361`,
`webui/src/routes/library/-ui/library-v2-page.tsx:546-694,3704-3746,6805-6817,7762-7866,9044-9141,10303-10475`

Nur `ActionButton` konsumiert den Write-Context. MonitorToggle,
IconActionButton, Bulk-Aktionen, Inline-RG/Lyrics, Import und globale Search-
Mutationen bleiben aktiv und produzieren für Nicht-Admins garantierte 403s.
Schlägt `/enabled` fehl, fällt die Seite sogar optimistisch auf
`canWrite=true` zurück.

**Korrekturvertrag:** Jede Mutation bekommt einen zentralen `requiresWrite`-
Gate und einen finalen Submit-Guard; View/History bleiben verfügbar. Ein
Availability-Fehler ist explizit und niemals default-writable. UI-Test mit
`can_write=false`: keine aktivierbare Mutation und kein POST.

#### <a name="iss30-u05"></a> iss30-U05 — Maintenance-Fehler werden als grüner Null-Erfolg dargestellt

**Ort:** `webui/src/routes/library/-ui/library-v2-page.tsx:2930-2979`,
`webui/src/routes/library/-library-v2.types.ts:539-549`,
`api/library_v2.py:3294-3298,3338-3342`

Unmapped- und Wishlist-Reconcile behandeln jedes `running=false` als Erfolg,
lesen ein fehlendes Resultat als Nullen und ignorieren `status.error`. Ein
Backendfehler wie `database locked` erscheint dadurch als „done, 0“.

**Korrekturvertrag:** Terminales `error` vor Result/Done auswerten, sichtbaren
Retry anbieten und Query-Invalidierung nur nach echtem Erfolg auslösen.

#### <a name="iss30-u06"></a> iss30-U06 — Mehrere Tools verwandeln API-Fehler in valide Empty-States

**Ort:** `webui/src/routes/library/-ui/library-v2-page.tsx:3122-3181,3274-3329,10004-10013,10060-10097`

Duplicates, Artist Files, Track Source Info und Pipeline History prüfen ihren
Query-Fehler nicht. 500/Netzfehler erscheinen als „keine Duplikate“, „keine
Dateien“, „keine Source-Daten“ oder verschwinden vollständig. Das verletzt die
Error-Truthfulness-Invariante und kann einen beschädigten Katalog wie einen
sauberen aussehen lassen.

**Korrekturvertrag:** Eigener Error-/Retry-Zweig pro Tool; Empty-State erst
nach erfolgreicher Response. Je ein 500- und Network-Failure-Test.

#### <a name="iss30-u07"></a> iss30-U07 — Dialoge halten Tastaturfokus nicht im Modal

**Ort:** `webui/src/routes/library/-ui/library-v2-page.tsx:732-770`,
`webui/src/routes/library/-ui/interactive-search.tsx:658-665`

Die Dialoge setzen zwar `role=dialog` und `aria-modal`, besitzen aber keinen
initialen Fokus, Focus-Trap, Escape-Handler oder Focus-Restore. Tab erreicht
den verdeckten Hintergrund; Escape schließt nicht. Die Interactive-Suche hat
zusätzlich kein zugängliches Label am Suchfeld.

**Korrekturvertrag:** Eine gemeinsame zugängliche Dialogkomponente mit
Heading-ID, Initialfokus, Trap, Escape und Restore; Keyboard-Regressionstests.

#### <a name="iss30-u08"></a> iss30-U08 — Playwright prüft entfernte Features und alte Routen

**Ort:** `webui/tests/library-v2.phase-cd.spec.ts:74-108,145-159`,
`webui/tests/pages/artist-detail.spec.ts:5-76`,
`webui/tests/shell-routes.smoke.spec.ts:239-260`

Die Browser-Suite erwartet den entfernten Playlists-Tab, alte Library-Buttons
und ein verbleibendes Legacy-Artist-Detail-DOM. Der aktuelle Vertrag redirectet
`/artist-detail/:source/:id` dagegen bewusst nach
`/library?discover=...`, Library-eigene Artists nach `?artist=...`. Selbst mit
laufendem Server können diese Tests die aktuelle V2 nicht abnehmen.

**Korrekturvertrag:** Specs auf `?artist`/`?discover` und aktuelle Tool-Flows
umschreiben; F-12-Negativtest und Non-Admin-Read-only-E2E ergänzen.

#### <a name="iss30-u09"></a> iss30-U09 — Das vollständige WebUI-Gate ist am Branch-HEAD rot

`npm run check` stoppt an Formatabweichungen in `bridge.ts`,
`library-v2-page.tsx` und `-search.helpers.ts`. Der separat ausgeführte
Typecheck meldet TS2322 in `src/routes/library/-route.test.tsx:119`. Die volle
Vitest-Suite hat zusätzlich 131 kaskadierende Fehler in vier verbliebenen
Legacy-Artist-Detail-Dateien, weil die aktuelle Node-Runtime ein vorhandenes,
aber undefiniertes globales `localStorage` bereitstellt. Letzteres reproduziert
laut früherem Audit identisch auf Upstream und ist kein Beleg für 131
Produktregressionen; es macht das Gate trotzdem unbrauchbar.

**Korrekturvertrag:** Format und Mock-Typ korrigieren; Test-Setup muss
`globalThis.localStorage` unter Node 26 stabil installieren. Danach Full Suite
und die aktualisierten Playwright-Specs gegen eine Testinstanz ausführen.

#### <a name="iss30-u10"></a> iss30-U10 — Ein Legacy-Pin-Test koppelt Routing an ein 400-Zeichen-Fenster

**Ort:** `tests/test_chat_page.py:38-42`, `webui/static/init.js:2939-2950`

Der vollständige Python-Lauf stoppt reproduzierbar in
`TestRouting.test_music_deeplink_and_loader`, obwohl `chat` tatsächlich Element
von `_DEEPLINK_VALID_PAGES` ist und der Loader weiterhin `case 'chat'` samt
`window.ChatPage.open()` enthält. Der Test betrachtet lediglich die ersten 400
Zeichen nach dem Variablennamen. Der ausführlichere Library-V2-Alias-Kommentar
schiebt den späteren Listeneintrag aus diesem Fenster; Produktionsrouting und
die beiden übrigen Assertions sind intakt. Isoliert entsteht derselbe
False-Negative.

**Korrekturvertrag:** Den Set-Initializer syntaktisch bis `]);` abgrenzen oder
das Modul über eine kleine exportierbare Routing-Funktion testen; niemals
Quelltextposition bzw. Kommentar-Länge als Verhaltensvertrag verwenden. Dieser
Testfehler ist kein Produktdefekt, hält aber das Full-Suite-Release-Gate rot.

### 30.5 Geprüft und ohne neuen Befund

- `/library-v2` redirectet mit vollständigem Querystring nach `/library`.
- Search-Owned-Links benutzen `?artist=<v2-id>`; Provider-Treffer laufen über
  den getesteten Artist-Detail-Stub nach `?discover=<source>:<id>`, einschließlich
  numerischer Namen und IDs mit Slash.
- Shell-Ankerinterception erhält Query und Hash und respektiert Permissions.
- Global Automatic Search orchestriert Upgrade-Scan vor Wishlist-Verarbeitung;
  iss30-S01 liegt **nach** dieser korrekten Orchestrierung im
  Qualitäts-/Replacement-Vertrag.
- Interactive-Search-Fan-out, Source-Teilfehler, Run-Sequenz und Outcome-Polling
  sind außerhalb des Album-im-Track-Falls konsistent.
- Der Registry-Override auf den nativen Metadata Gap Filler funktioniert;
  Repair-Fixes laufen grundsätzlich durch Maintenance-Sync, Rescan,
  Invalidation, Wanted und History.
- Bootstrap erhält opaque Legacy-IDs, Resume-Run-ID und Watermark-Vertrag;
  Credits, Editionen und Wanted werden neu projiziert. Direkte V2-IDs haben im
  Autolink Vorrang vor heuristischem Matching.
- `autoGrabBest` ist Runtime-Dead-Code (nur Definition und Unit-Test). Sein
  Track-vs.-Album-Filter schützt daher keinen Produktionspfad.

---

## 31. Adversarialer Folgeaudit der Remediation (4. August 2026)

Nach den ersten iss30-Fixes wurde der neue Upgrade-/Autolink-Vertrag erneut
gegen manipulierte Requests, Parallelität und Transformfehler geprüft. Die
Diagnosen und ihre abschließende Behandlung sind:

| ID | Diagnose | Remediation |
|---|---|---|
| iss31-A01 | Client-`source_info` konnte eine fremde `lib2_track_id` als Upgrade-Ziel vortäuschen | `778c19cf3`: rekursive Sanitization an `/api/download` und `/api/download/matched`; nur ein serverseitiges, nicht JSON-serialisierbares Upgrade-Intent autorisiert Replacement |
| iss31-A02 | Die Qualitätsentscheidung betrachtete die Rohdatei vor Downsample/Lossy-Transform | `778c19cf3`: Transform vor Vergleich; geprüft wird genau das später behaltene Artefakt mit einem live aufgelösten Profilsnapshot |
| iss31-A03 | Zwei gleichzeitige Upgrades konnten dieselbe alte Primary vergleichen und nacheinander veröffentlichen | `778c19cf3`: per-Track-Serialisierung und CAS-Re-read unmittelbar vor Publish |
| iss31-A04 | `fallback_enabled=false` war im zentralen Quality-Vertrag wirkungslos | `56738ab80`: strict fallback, unmatched/unmatched und same-rank Cross-Format zentral definiert und getestet |
| iss31-A05 | Kompatibilitäts-Platzhalter konnten als echte Provider-ID dauerhaft persistiert werden | `56738ab80`: Sentinel-/`lib2-*`-Filter; unbekannte Namespaces matchen nicht quer durch fremde `external_ids` |
| iss31-A06 | Reale Matched-Download-Payloads verlieren teilweise den Metadata-Provider | Sicherer Namensfallback ist aktiv; vollständige Provider-Propagation durch Legacy-Match-UI und beide Download-Endpunkte bleibt offen, siehe Status §49.4 |
| iss31-A07 | Grab-Kontext las denormalisierte statt live effektiv vererbte Profile | `56738ab80`: gemeinsamer Resolver plus Profil-Provenienz |
| iss31-A08 | Ein Album-Candidate-Token konnte als Track erneut gepostet werden | `778c19cf3`: Token bindet `result_kind`; serverseitige Revalidierung lehnt Kind-Spoofing ab |
| iss31-A09 | Ein gemeinsamer Blocking-Pool konnte Download-Control hinter Provideraufrufen verhungern lassen | `f20c7b5f3`: getrennte begrenzte Slow-/Control-Pools; 86 Executor-/Adaptertests |
| iss31-A10 | Unbegrenztes Entfernen des 500er-Caps hätte bei großen Libraries unbounded Providerlast erzeugt | `56738ab80`: persistenter, stabil sortierter 500er-Cursor; 1.001-Subject-Mehrfachlauftest |

Restrisiko von A03: Der Lock ist pro Prozess. Der CAS-Check liegt direkt vor
dem atomaren Publish und verkleinert das Cross-Process-TOCTOU-Fenster, ersetzt
aber keinen datenbankweiten Publish-Lock. Dies ist im nächsten Architekturpass
zu entscheiden, kein verschwiegenes „verified“.

---

## 32. Nezrekas Review von PR #1062 auf einer realen Großbibliothek (10. August 2026)

Erster echter Fremdtest des Branches. Maßstab der Testbibliothek: **4.979
Artists, 69.296 Alben, 307.885 Tracks, 9 GB DB**. Das ist rund eine
Größenordnung über der bisher verwendeten Test-DB, und genau diese Skala hat
die folgenden Befunde freigelegt. Nezrekas Gesamturteil zur Seite selbst ist
positiv („rock solid", `schema.py`-Begründung gegen das alte Schema geprüft und
bestätigt); die Blocker liegen in Migration und Enrichment.

Quelle: <https://github.com/Nezreka/SoulSync/pull/1062#issuecomment> vom
10. August 2026, 20:17 UTC.

### 32.1 Migration bleibt auf großer DB stehen

**Fehlerbild:** Der Bootstrap-Import stand 9 Minuten bei „5/7 · 71%" ohne jede
Logausgabe. Parallel warfen *sämtliche* Enrichment-Worker sowie die Automation
Engine durchgehend `database is locked`; danach scheiterte auch ein
Config-Save mit „Config DB save failed after 6 attempts".

Ein Neustart verschlimmerte die Lage: der zweite Start kam nicht über die
DB-Initialisierung hinaus. Letzte Logzeile 12:05:56, danach 30 Minuten nichts.
Dass überhaupt noch geschrieben wurde, war ausschließlich am WAL ablesbar:

| Zeit | WAL | Rate |
|---|---:|---|
| 12:07 | 85 MB | — |
| 12:14 | 106 MB | ~4 MB/min |
| 12:35 | 135 MB | ~1,4 MB/min |

Der Durchsatz **fällt** also, statt zu steigen; die Haupt-DB blieb konstant bei
9.652 MB. Nach 30 Minuten Abbruch.

#### 32.1.0 Gemeinsame Ursache (Nachtrag 10. August 2026, abends)

Der zweite Durchgang durch den Code hat die vier Einzelbefunde auf **eine**
Ursache zurückgeführt. Sie erklärt jedes beobachtete Symptom, einschließlich des
bis dahin unerklärten zweiten Starts.

**Befund A — der gesamte Schema-Init ist eine einzige Transaktion.**
`MusicDatabase._initialize_database` (`database/music_database.py:277`) läuft
über rund 960 Zeilen und hat **genau ein** `conn.commit()`, am Ende
(`:1239`). Alles dazwischen hält den einzigen SQLite-Writer.

**Befund B — in dieser Transaktion stehen fünf unbegrenzte Voll-Backfills.**
`ensure_library_v2_schema` (aufgerufen `music_database.py:1219`) führt aus:

| Aufruf | Ort | Umfang bei Nezrekas DB |
|---|---|---|
| `_backfill_artist_name_keys` | `schema.py:857` | alle Artists ohne `name_key` |
| `backfill_stable_ids` | `schema.py:871` | alle Alben/Tracks ohne `stable_id`, Zeile für Zeile |
| `backfill_primary_flags` | `schema.py:882` | vier Voll-UPDATEs über `lib2_track_files`, davon eines mit korreliertem Subselect je Track |
| `ensure_wanted_projection` | `schema.py:914` | **vollständiges** `recompute_wanted`, sobald `lib2_wanted_tracks` leer ist und `lib2_tracks` nicht |
| `backfill_editions` | `schema.py:923` | ein `fetchall()` über **alle** nicht materialisierten Tracks, danach 2 Inserts je Track |

Diese Aufrufe sind mit `WHERE … IS NULL` idempotent und kosten auf einer
konvergierten Installation fast nichts. Auf einer **abgebrochenen Migration**
ist genau das Gegenteil der Fall: dort ist alles unkonvergiert.

**Befund C — das erklärt den Stillstand bei „5/7 · 71 %".**
`FINALIZE_STAGE` hat `_FINALIZE_STEPS = 7` (`importer.py:45`); 5/7 = 71 %. Der
Schritt, der nach dem Checkpoint 5 läuft, ist `backfill_editions`
(`importer.py:1612`) — 69.296 Alben plus 307.885 Tracks, ohne
Zwischen-Commit und ohne einen einzigen Progress-Report. Neun Minuten
gehaltener Schreib-Lock bei `busy_timeout = 30000` (`music_database.py:263`)
sind exakt das, was jeder andere Writer als `database is locked` sieht — die
zwölf Enrichment-Worker, die Automation Engine und der Config-Save („after 6
attempts").

**Befund D — das erklärt den zweiten Start.**
Der Abbruch rollte `backfill_editions` zurück (kein Commit war erfolgt). Beim
Neustart macht deshalb der **DB-Init selbst** dieselbe Arbeit noch einmal:
`backfill_editions` über 307.885 Tracks *und* ein volles `recompute_wanted`
(Schritt 7 war nie gelaufen, `lib2_wanted_tracks` also leer) — in der einen
Init-Transaktion, auf dem Startpfad, vor jedem Log. Daraus folgt Zeile für
Zeile, was Nezreka gemessen hat:

- keine Logausgabe → die Backfills loggen erst *nach* der Schleife
- WAL wächst → unbestätigte Writes gehen ausschließlich ins WAL
- Haupt-DB konstant 9.652 MB → eine offene Transaktion kann nicht
  gecheckpointet werden
- Durchsatz fällt → jede `NOT EXISTS`-Prüfung muss den wachsenden WAL-Index
  mitlesen
- „never got past db init" → `_initialize_database_once`
  (`music_database.py:185`) hält zusätzlich `_database_initialization_lock`,
  also blockiert jeder weitere Thread, der `get_database()` ruft, schon in
  Python

**Konsequenz für M03:** Nezrekas Forderung „keep it off the startup path" ist
**richtig** — nur nicht an der Stelle, an der der erste Prüfdurchgang gesucht
hat. Der Bootstrap-Autostart liegt tatsächlich im Daemon-Thread; die
migrationsäquivalente Arbeit im Schema-Ensure liegt es nicht.

| ID | Diagnose | Verifikationsstand | Korrekturvertrag |
|---|---|---|---|
| iss32-M01 | Kein zeitgesteuertes Fortschrittslog. `_progress(stage, current, total, …)` in `core/library2/bootstrap.py:618` schreibt den Fortschritt nur in die Claim-Row (Heartbeat, für die UI). Ins Log gehen ausschließlich „starting" (Zeile 615), „resuming" (612) und „completed" (679). Ein hängender Lauf ist deshalb von einem laufenden nicht unterscheidbar. | **Im Code bestätigt** | Zeitgesteuerte `logger.info`-Ausgabe im Format `41.000/307.885` mindestens alle 30 s, unabhängig davon, wie oft die aktuelle Stage einen Progress-Callback feuert. Der Timer muss auch dann feuern, wenn eine einzelne Stage lange ohne Callback läuft — sonst wird genau der beobachtete Fall wieder nicht sichtbar. |
| iss32-M02 | Enrichment-Worker und Automation Engine konkurrieren während der Migration um den einzigen SQLite-Writer. Ergebnis ist beidseitige Blockade statt Priorisierung. Der Nutzer hat dem ausdrücklich zugestimmt: Worker zuerst stoppen, dann migrieren. **Vorarbeit vorhanden:** alle 16 Worker (`core/*_worker.py`) haben bereits `pause()`/`resume()` und prüfen `self.paused` in ihrer Schleife. Die Automation Engine (`core/automation_engine.py`) hat nur `start()`/`stop()`, dort fehlt das Gegenstück. | **Nicht verifiziert** als alleinige Ursache — der eigentliche Auslöser ist iss32-M05; die Pause bleibt trotzdem richtig und nötig | Ein zentraler Gate-Supervisor pausiert alle Enrichment-Worker und die Automation Engine, solange ein Bootstrap-Lauf aktiv ist, und nimmt sie danach wieder auf. Zustandsträger ist **nicht** ein In-Memory-Flag, sondern die vorhandene Claim-Row `lib2_bootstrap_state` (`status='running'` + frischer Heartbeat): ein abgestürzter Lauf lässt seinen Claim veralten, und die Worker laufen von selbst wieder an. Der Supervisor darf nur die Worker fortsetzen, die **er** pausiert hat — eine vom Nutzer gesetzte Pause bleibt bestehen. |
| iss32-M03 | Nezrekas Forderung „keep it off the startup path so the server comes up first". Der Bootstrap-**Autostart** läuft bereits in einem Daemon-Thread mit 30-s-Vorlauf (`web_server.py:43371` → `_autostart_library_v2_bootstrap_import`, `web_server.py:32742`) — dort liegt das Problem nicht. Es liegt in `ensure_library_v2_schema`, das aus `_initialize_database` heraus (`music_database.py:1219`) fünf unbegrenzte Voll-Backfills **synchron auf dem Startpfad** und in der Init-Transaktion ausführt (Befund B/D oben). | **Bestätigt, an anderer Stelle als vom Review vermutet** | Der Schema-Ensure macht nur noch DDL und konvergierte Billig-Prüfungen. Jeder Backfill mit unbegrenztem Umfang wandert auf den Hintergrundpfad, in Batches mit Commit. Der Server muss hochkommen, bevor irgendein Backfill die erste Zeile schreibt. |
| iss32-M04 | Kein WAL-Checkpoint während der Migration. `wal_checkpoint` kommt im gesamten Repository **kein einziges Mal** vor. Ein 135-MB-WAL ohne Checkpoint erklärt den fallenden Durchsatz unmittelbar: jeder Reader muss den wachsenden WAL durchlaufen. | **Im Code bestätigt** (Grep über alle `*.py`) | Periodischer `PRAGMA wal_checkpoint(TRUNCATE)` in den Commit-Pausen des Bootstrap-Laufs. Ein Checkpoint braucht ein Fenster ohne offene Transaktion — er ist deshalb nur zusammen mit iss32-M05 wirksam, nicht davor. |
| iss32-M05 | **Neu.** Die langen Finalize-Schritte laufen ohne Zwischen-Commit. `backfill_editions` (`importer.py:1612`) materialisiert 69.296 Alben + 307.885 Tracks in einer einzigen offenen Transaktion, `recompute_wanted` (`importer.py:1618`) danach ebenso. Neun Minuten gehaltener Writer sind die direkte Ursache der `database is locked`-Welle und des gescheiterten Config-Saves — nicht nur „Konkurrenz um den Writer". | **Im Code bestätigt** | Beide Schritte arbeiten in Batches fester Größe, committen je Batch, melden je Batch Fortschritt und lassen zwischen den Batches ein Fenster für andere Writer und für den Checkpoint aus M04. |
| iss32-M06 | **Neu.** Der gesamte Schema-Init ist eine einzige Transaktion mit genau einem `conn.commit()` am Ende (`music_database.py:1239`), und `_initialize_database_once` (`:185`) hält währenddessen zusätzlich `_database_initialization_lock`. Ein langsamer Init blockiert damit nicht nur SQLite-Writer, sondern jeden Thread, der überhaupt ein DB-Handle anfordert. | **Im Code bestätigt** | Die lib2-Backfills verlassen diese Transaktion (M03). Ob der Init darüber hinaus in mehrere Transaktionen zerfällt, ist eine separate Entscheidung — für die Migration reicht es, dass nichts Unbegrenztes mehr darin steht. |
| iss32-M08 | **Neu, und der eigentliche Grund für die Dauer.** `_find_recording_by_hard_ids` (`core/library2/editions.py:275`) sucht das Recording per `SELECT id FROM lib2_recordings WHERE isrc=?`. Der zugehörige Index ist **partiell** (`… WHERE isrc IS NOT NULL AND isrc <> ''`), und SQLite darf einen partiellen Index nur verwenden, wenn die WHERE-Klausel der Abfrage die des Index *beweisbar* impliziert. `isrc = ?` beweist mit einem gebundenen Parameter gar nichts, also lautet der Plan `SCAN lib2_recordings` — ein Full Table Scan, **dreimal pro Track**, gegen eine Tabelle, die auf eine Zeile pro Track wächst. Der Backfill ist damit quadratisch. | **Gemessen** auf einem 307.885-Track-Katalog: 200 Probes = 755 ms als Scan, 3,1 ms über den Index. Durchsatz vorher 128 Zeilen/s und **fallend** (bei 58k Zeilen), nachher **flach ~9.000 Zeilen/s** über die gesamte Tabelle | Die Prädikate des partiellen Index in der Abfrage mitschreiben (`AND isrc IS NOT NULL AND isrc <> ''`), ebenso in der Kollisionsprüfung von `_fill_missing_hard_id`. Betroffen sind ausschließlich die drei `lib2_recordings`-Indizes: nur ihr Prädikat enthält `<> ''`. Ein reines `IS NOT NULL` kann SQLite aus `spalte = ?` selbst folgern (Gleichheit mit NULL ist nie wahr), weshalb `idx_lib2_release_tracks_track` und `idx_lib2_editions_default` nie betroffen waren — nachgeprüft. |
| iss32-M07 | **Neu, Nebenbefund.** `_warn_about_stale_sqlite_sidecars` (`music_database.py:202`) führt bei jedem Start, an dem `-wal`/`-shm` existieren — also nach jedem unsauberen Stopp —, ein `PRAGMA quick_check` über die **gesamte** Datenbank aus. Bei 9,6 GB ist das reine Lese-I/O über alle Seiten, auf dem Startpfad, vor jeder lib2-Logzeile. Rein diagnostisch: das Ergebnis wird nur geloggt. | **Im Code bestätigt** | Die Prüfung gehört in einen Hintergrund-Thread oder hinter eine Größen-/Zeitschranke. Sie darf den Start nicht verzögern, weil ihr Ergebnis den Start ohnehin nicht beeinflusst. |

### 32.2 Enrichment darf beim Wechsel auf V2 nicht regressieren

Nezrekas Anforderung ist explizit und nicht verhandelbar: „i don't want to lose
any enrichment functionality or data in the move to v2. v2 should get filled
the same way v1 does now. every artist and album, artwork, genres, bios,
provider ids, **all twelve workers**." Begründung: V1 kann das bereits.

| ID | Diagnose | Verifikationsstand | Korrekturvertrag |
|---|---|---|---|
| iss32-E01 | `resync_entity_from_legacy` ist **nicht verdrahtet**. Die Funktion existiert in `core/library2/enrich.py:123` und steht im `__all__` (Zeile 143), aber der einzige weitere Fundort im Produktivcode ist eine Docstring-Erwähnung in `core/library2/native_enrich.py:7`. Aufrufstellen gibt es nur in `tests/library2/test_enrich_resync.py`. Die zwölf Worker schreiben über `_run_single_enrichment` (`web_server.py:14559`) die Legacy-Row — und nichts spiegelt das Ergebnis nach `lib2_*`. | **Im Code bestätigt** — Nezrekas Vermutung trifft zu | Ein Aufruf hinter `_run_single_enrichment` deckt **nur den manuellen Einzel-Enrich aus der UI** ab. Die eigentliche Masse schreiben die Worker-Schleifen selbst: **137 `UPDATE artists/albums/tracks`-Statements in 14 Worker-Dateien**, und nur etwa die Hälfte davon setzt `updated_at`. Eine Spiegelung, die an `updated_at` oder an einzelnen Aufrufstellen hängt, ist damit nachweislich lückenhaft. Zu liefern ist ein Mechanismus, der **jeden** Legacy-Schreiber erfasst, unabhängig von der Datei — plus ein Regressionstest, der beweist, dass ein echter Worker-Lauf die `lib2_*`-Row verändert, nicht nur, dass `resync_entity_from_legacy` isoliert funktioniert (das belegen die vorhandenen Tests bereits). |
| iss32-E02 | Zwei Klassen von Artists mit unterschiedlicher Enrichment-Tiefe, ohne dass die UI sie unterscheidet: Artists aus der alten Library bekommen alle zwölf Worker; in V2 nativ entstandene Artists (Featured Credits, Wishlist, Discography) haben keine Legacy-Row und bekommen nur `native_enrich` — also Provider-ID, Artwork, Genres. Beide zeigen „matched". | **Bekannt und unabhängig bestätigt**, siehe Memory `library-v2-native-artist-enrich-deadend` (RC1 lösbar, RC2 Compound-Namen teilweise nicht) | Native Artists müssen denselben Enrichment-Pfad erreichen. Zwei Wege sind denkbar und vor der Umsetzung zu entscheiden: (a) für einen nativen Artist bei Bedarf eine Legacy-Row anlegen und den bestehenden Worker-Pfad fahren, oder (b) die zwölf Worker so entkoppeln, dass sie eine `lib2`-Identität direkt bedienen. (a) ist billiger, zementiert aber die Legacy-Tabelle als Pflichtdurchgang — was direkt gegen 32.3 arbeitet. Solange die Lücke besteht, darf die UI nicht beide Zustände als „matched" ausgeben. |
| iss32-E03 | `/api/library/artists` (`web_server.py:9889`) liest weiterhin Legacy: der Handler ruft `database.get_library_artists(...)`. Metadaten-Edits und Enrichment aus der neuen UI erscheinen dort folglich nicht. | **Im Code bestätigt** | Endpunkt auf die V2-Projektion umstellen. Vorher zu klären: welche Consumer hängen daran? Bekannt sind mindestens `tests/test_finding_artist_link_ui.py` (Finding-Artist-Verlinkung baut `/api/library/artists?search=`) und `/api/library/artists/export` (`web_server.py:30763`). Die Antwortform muss erhalten bleiben oder alle Consumer mitwandern. |

### 32.3 Architekturfrage: bleiben `lib2_artists` / `lib2_albums` / `lib2_tracks` Kopien?

Kein Bug, sondern eine Entscheidung, die Nezreka beantwortet haben will, bevor
er merged. Sein Stand:

- `lib2_track_artists` und `lib2_track_files` sind **unstrittig** — die
  Legacy-`tracks` hat nur ein `artist_id` und ein `file_path`, das lässt sich
  nicht anders abbilden.
- `lib2_artists`, `lib2_albums`, `lib2_tracks` sehen für ihn dagegen aus wie
  Kopien. Er ist `lib2_artists` Spalte für Spalte durchgegangen: alles hätte
  auch als zusätzliche Spalte auf der bestehenden `artists`-Tabelle liegen
  können.
- Sein Ziel: **eine** Library. Alles liest und schreibt `lib2`, Legacy ist weg
  oder read-only. Explizit unerwünscht ist der Zustand, in dem beide bleiben
  und sich widersprechen.

Zu liefern ist eine begründete Antwort, kein Code: entweder „Endzustand, und
zwar deshalb …" oder „Übergang, und der Abbau der Legacy-Tabellen sieht so
aus …". Zu beachten ist die Wechselwirkung mit iss32-E02(a): der billige Fix
dort macht die Legacy-Row zum Pflichtdurchgang und damit die Antwort „Legacy
wird read-only" schwerer haltbar. Beide Punkte gehören zusammen beantwortet.

#### 32.3.1 Entscheidung (10. August 2026, Nutzer)

**Die Tabellen sind der Endzustand. Die Doppelung ist es nicht.** Ziel ist
vollständig das, was Nezreka verlangt: eine Bibliothek, alles liest und
schreibt `lib2_*`, die Legacy-Tabellen verschwinden. **Sämtliche Worker und
sämtliche Tools werden auf Library V2 umgeschrieben** — nicht nur die bereits
migrierte Repair-Seite.

**Zeitliche Zuordnung:** Der Umbau der Metadaten-/Enrichment-Worker geschieht
**nicht in diesem PR**. Dieser PR liefert Stufe 1 (siehe unten) und die
Zusage; der Produzenten-Umbau ist ein eigener PR, weil dort der Ingest-Pfad
liegt — geht dabei etwas schief, ist nicht ein Feature kaputt, sondern der
einzige Weg, auf dem Daten in die App kommen.

**Zwei Gründe, warum die zweite Tabellenwelt keine Bequemlichkeit war** (beide
sind beim Spalte-für-Spalte-Vergleich unsichtbar, den Nezreka gemacht hat):

1. **Der Primärschlüssel gehört dem Media-Server.** `artists.id` *ist* der
   ratingKey. Die INSERTs geben die id deshalb explizit an
   (`music_database.py:7060/7238/7521`), und wenn Plex den ratingKey bei einem
   Re-Scan ändert, wird die komplette Zeile unter neuer id neu angelegt und die
   Enrichment-Daten über eine handgepflegte Liste von 22 Spaltennamen
   hinüberkopiert (`music_database.py:7043 ff.`). Eine Bibliothek, die ohne
   Media-Server funktionieren soll, kann ihre Identität nicht von einem
   Media-Server beziehen.
2. **Die Semantik unterscheidet sich, nicht nur der Spaltensatz.**
   `lib2_tracks` enthält Tracks **ohne Datei** (`core/library2/missing_tracks.py`),
   `lib2_albums` Alben mit `origin='discography'`, die niemand besitzt. In der
   alten Welt heißt eine Zeile in `tracks`: diese Datei liegt auf der Platte.
   Ein Merge in die Alt-Tabellen würde diese Bedeutung ändern und damit **656
   Lesestellen in 64 Dateien** still falsch machen, ohne dass ein Compiler oder
   ein vorhandener Test das fängt.

**Bestandsaufnahme (gemessen am 10. August 2026, ohne `tests/`):**

| Bereich | lib2-Bezüge | Legacy-Statements | Stand |
|---|---:|---:|---|
| Repair-Jobs + `repair_worker.py` | 92 (Worker) + ~180 (Jobs) | Reste, siehe unten | **migriert** (P1/P2) |
| Download-/Import-Pfad (`core/imports/side_effects.py`) | 7 | 7 | **dual**, schreibt bereits beide Welten |
| 16 Metadaten-/Enrichment-Worker (`core/*_worker.py`) | **0** | **334** | **nicht angefasst** |
| Media-Server-Scan (`database_update_worker.py` → `music_database.py`) | 0 | 49 Schreib-/195 Lesestellen | **nicht angefasst** |
| Gesamt Produktivcode | — | 656 lesend / 237 schreibend in 64 Dateien | — |

Restliche Legacy-Bindung auf der bereits migrierten Repair-Seite (gehört auf
die Stufe-2-Liste, kein Blocker): `comma_artist_splitter` 0/7,
`genre_cleanup` 0/3, `live_commentary_cleaner` 0/3, `track_number_repair` 3/11,
`album_tag_consistency` 3/8, `metadata_gap_filler` 2/4, `missing_cover_art` 4/4.

**Stufenplan:**

| Stufe | Inhalt | Wann |
|---|---|---|
| 1 | Konsumenten lesen lib2 (E03 als Nachzügler); Legacy→lib2-Spiegel (E01) hält beide Seiten deckungsgleich; Divergenz wird im Integritätsreport als Kennzahl geführt | **dieser PR** |
| 2 | Produzenten schreiben lib2: 16 Enrichment-Worker, Media-Server-Scan, die sieben Repair-Job-Reste. Danach löst sich E02 von selbst auf — native Artists haben keine Sonderrolle mehr | eigener PR, direkt danach |
| 3 | Legacy read-only: Bootstrap-Import stellt sich ab, Spiegel und Shim-Zeilen fallen ersatzlos weg, Tabellen bleiben ein Release lang lesbar stehen | Folge-Release |
| 4 | Legacy-Tabellen droppen | ein Release später |

**Drei Zusagen, die die Doppelung überprüfbar ungefährlich machen, solange sie
besteht** — das ist die eigentliche Antwort auf „both sticking around and
disagreeing with each other":

1. **Einbahnstraße, hart.** Nur Legacy → lib2, nie zurück. Zwei Zeilen können
   nur auseinanderlaufen, wenn beide beschrieben werden.
2. **Divergenz ist eine Kennzahl, kein Vertrauensvorschuss.** Der vorhandene
   read-only Integritätsreport (`build_integrity_report`,
   `core/library2/integrity_reconciler.py`) bekommt einen Check über die
   gespiegelten Felder. Erwartungswert 0; jeder andere Wert ist ein Bug mit
   Zeilennummern.
3. **Stufe 2 vor neuer Feature-Arbeit**, nicht „irgendwann".

### 32.4 `mbid_mismatch_detector` — Findings werden gepruned

| ID | Diagnose | Verifikationsstand | Korrekturvertrag |
|---|---|---|---|
| iss32-S01 | `mbid_mismatch_detector` steht in `RETIRED_JOB_IDS` (`core/repair_jobs/__init__.py:118`), aber **nicht** in `PRESERVED_RETIRED_FINDING_IDS` (ab Zeile 153). `core/repair_worker.py:570` bildet `prune_ids = RETIRED_JOB_IDS - PRESERVED_RETIRED_FINDING_IDS`, folglich werden seine Findings beim Worker-Start gelöscht. | **Im Code bestätigt — und der Befund reicht weiter als die Frage** | Die Nachprüfung ergibt drei Dinge, die die Antwort verschieben: (1) `core/repair_jobs/mbid_mismatch_detector.py` existiert **auf `main`** und ist auf diesem Branch **gelöscht**; (2) der zugehörige Fix-Handler in `core/repair_worker.py` ist mitgelöscht — im ganzen Repo kommt `mbid_mismatch` nur noch als String in `RETIRED_JOB_IDS` vor; (3) **kein** nativer Job ersetzt ihn: eingebettete MusicBrainz-Recording-IDs werden nirgends mehr gegen die MB-API geprüft. Damit greift genau die Begründung, die der `library_reorganize`-Kommentar zwei Zeilen darüber gibt. Der Job stammt zudem von Nezreka selbst (`87b39634a`, 16. März 2026). Nur in `PRESERVED_RETIRED_FINDING_IDS` aufzunehmen, würde Findings erhalten, die mangels Handler niemand mehr beheben kann. Zu entscheiden ist deshalb zwischen „als nativen V2-Job zurückholen" und „bewusst ersatzlos streichen, und das Nezreka so sagen". |

### 32.5 Nachträge aus der Umsetzung (11. August 2026)

Zwei Befunde, die erst beim Nachprüfen der eigenen Arbeit auftauchten, und ein
generisches Muster, das über diesen PR hinaus gilt.

| ID | Diagnose | Verifikationsstand | Korrekturvertrag |
|---|---|---|---|
| iss32-E02a | `native_enrichment_sweep` wurde mit `default_enabled = False` ausgeliefert — dem Muster der übrigen Repair-Jobs folgend. Die sind Opt-in-Diagnosen; dieser ersetzt zwölf *dauerhaft laufende* Worker. Im Zustand „aus" beantwortet er Nezrekas Anforderung mit einem Schalter, den niemand umlegt, und die gemeldete Regression bleibt schlicht bestehen. | **Behoben** | `default_enabled = True`, mit der Begründung direkt am Feld, damit die Abweichung vom Paketmuster nicht als Versehen zurückgedreht wird. |
| iss32-E02b | Derselbe Job schrieb `result.fixed += 1`. `JobResult` (`core/repair_jobs/base.py:107`) ist eine schlichte Dataclass mit `scanned`/`findings_created`/`findings_skipped_dedup`/`auto_fixed`/`errors`/`skipped`. Die Zuweisung legt still ein Attribut an, das niemand liest; `RepairWorker` summiert und loggt ausschließlich `auto_fixed`. Der Job hätte also korrekt gearbeitet und bei **jedem** Lauf null gemeldet — ununterscheidbar von einem Job ohne Arbeit. | **Behoben** | `result.auto_fixed`. Zusätzlich `tests/repair_jobs/test_job_result_fields.py`: statischer Guard über alle Job-Dateien, der jede Zuweisung an ein nicht existierendes `JobResult`-Feld meldet. Eine Dataclass ohne `slots` kann diesen Fehler nicht selbst melden, und eine Verhaltensprüfung müsste jeden Job ausführen. |
| iss32-T01a | Zusage 2 aus §32.3.1 — Divergenz zwischen Legacy und lib2 als Kennzahl im Integritätsreport — ist **nicht umgesetzt**. `core/library2/integrity_reconciler.py` prüft Datei-Index-Divergenz (`index_divergence`), nicht die gespiegelten Enrichment-Felder. | **Behoben** (11. August 2026) | Check über die Felder, die `resync_*_from_legacy` spiegelt, Erwartungswert 0. Umgesetzt als `lib2_mirror_divergence` (Komponente `mirror_divergence`, je Zeile mit Feldnamen und beiden Werten) plus `observed.mirror_checked/mirror_pending/mirror_dangling`. Die verglichene Feldmenge ist **keine zweite Liste**: der Schreibpfad in `enrich.py` ist jetzt deklarativ (`MIRROR_SPECS`), Writer und Audit lesen dieselbe Deklaration. Zeilen, die bereits in `lib2_legacy_dirty` stehen, zählen als `pending` statt als Divergenz — sonst wäre die Kennzahl im Normalbetrieb dauerhaft ungleich 0 und damit wertlos. |
| iss32-T01b | **Neu, und der Grund, warum die erste Messung nichts gemessen hat.** `artists.id` ist `TEXT PRIMARY KEY` (der Media-Server-ratingKey), `lib2_artists.legacy_artist_id` ist `INTEGER`. SQLite bringt beide über die Spalten-Affinität zusammen, ein Python-`dict` mit dem Rohwert als Schlüssel nicht. | **Gemessen** an der echten Bibliothek: 170 von 170 gespiegelten Zeilen wurden als „Legacy-Zeile fehlt" gemeldet, `mirror_checked` war 0 | `legacy_key()` normalisiert beide Seiten auf eine vergleichbare Form. Regressionstest mit der echten Legacy-Schemaform (`id TEXT PRIMARY KEY`) statt der `INTEGER`-Form der Test-Harness — genau diese Abweichung hat der Unit-Test nicht gesehen. |
| iss32-T01c | **Neu, und die eigentliche Entdeckung.** Die Trigger aus iss32-E01 sehen nur Legacy-Schreibvorgänge **nach** ihrer Installation. Alles, was die zwölf Worker vorher geschrieben haben, ist bereits divergent und stellt nichts in die Queue. Eine Kennzahl mit Erwartungswert 0, die kein Mechanismus je auf 0 bringen kann, misst nur noch, wie lange es den Spiegel gibt. | **Gemessen**: 156 von 170 gespiegelten Zeilen der echten Bibliothek divergent, überwiegend Provider-IDs, die Legacy hat und lib2 nicht | `reconcile_divergent` (`legacy_mirror.py`) sucht diesen Rückstand und füllt damit die **vorhandene** Queue — ein zusätzlicher Produzent, kein zweiter Reparaturpfad; geschrieben wird weiter nur vom Drain. Begrenzt und fortsetzbar über einen Cursor wie jeder andere lib2-Backfill (iss32-M05). Der Leerlauf-Tick des Drainers führt ihn aus; ein Tick mit echter Queue-Arbeit drainiert und verschiebt den Sweep. Nachgewiesen gegen einen Snapshot der echten DB: 156 → 0 über den Produktivpfad, Endzustand 170 geprüft / 0 offen / Queue leer. |

**Generisches Muster für künftige Arbeit an partiellen Indizes** (aus
iss32-M08): SQLite darf einen partiellen Index nur verwenden, wenn die
WHERE-Klausel der Abfrage die des Index *beweisbar* impliziert. Aus
`spalte = ?` kann es `spalte IS NOT NULL` folgern (Gleichheit mit NULL ist nie
wahr), aber niemals `spalte <> ''`. Ein Index-Prädikat mit `<> ''` macht also
jede Parameter-Abfrage zum Full Table Scan, ohne dass irgendetwas warnt. Der
Nachweis ist `EXPLAIN QUERY PLAN` gegen eine *große* DB — bei kleinen Tabellen
ist ein Scan schnell und jeder Test grün. Zwei Query-Plan-Tests in
`tests/library2/test_migration_hardening.py` halten das für `lib2_recordings`
fest, einer davon pinnt bewusst, *dass* die naive Form die Falle ist, damit die
„redundanten" Prädikate nicht wegoptimiert werden.

## 33. Rewrite-Audit vom 13. August 2026

Die vollständigen Reproduktionen und Auswirkungen stehen im
[Rewrite-Audit](audits/library-v2-rewrite-audit-2026-08-13.md). Dieses Register
hält alle 25 bestätigten Befunde verbindlich fest; ihr Umsetzungsstand steht
ausschließlich in [Status §51](library-v2-status.md#51-remediation-des-rewrite-audits-vom-13-august-2026).

| ID | Prio | Diagnose | Korrekturvertrag |
|---|---|---|---|
| LV2-AUD-01 | P1 | Attempt-Backfill blieb nach dem ersten 100k-Slice stehen. | Stabil paginieren; Abschluss erst nach allen erfolgreichen Seiten. |
| LV2-AUD-02 | P2 | Legacy-Status für Similar Artists wurde nicht übernommen. | Den abgeleiteten Service samt Timestamp in das Attempt-Ledger säen. |
| LV2-AUD-03 | P2 | Similar-Queue akzeptierte nicht nutzbare Fremd-IDs. | Nur Spotify, iTunes, Deezer und MusicBrainz als Quell-IDs zulassen. |
| LV2-AUD-04 | P2 | Unqualifizierte Graph-IDs konnten providerübergreifend kollidieren. | Scanner und Chat auf die vier unterstützten Namensräume begrenzen. |
| LV2-AUD-05 | P1 | Vorhandene iTunes-/Amazon-/Deezer-IDs ließen die Queue stehen. | Preserved-ID-Kurzpfade als `matched` protokollieren. |
| LV2-AUD-06 | P1 | Title-only-Fallback verschmolz gleichnamige Tracks. | MBID oder Disc-/Trackposition zur Identität verlangen. |
| LV2-AUD-07 | P2 | Ein Medienserver-Pfadwechsel ließ den alten Scan-Pfad aktiv. | Alte Server-Beiträge ausmustern, echte Alternate Files erhalten. |
| LV2-AUD-08 | P2 | Ein Primary-Artist-Wechsel ließ alte Junctions stehen. | Primary-Junction ersetzen bzw. auf `primary` hochstufen. |
| LV2-AUD-09 | P1 | Server-Cleanup löschte gemeinsam genutzte Katalog-/Provider-/Dateidaten. | Medienserver-Beitrag separat markieren und ausschließlich detach-en. |
| LV2-AUD-10 | P2 | Provider-only-Tracks erschienen als nicht vermessene Dateien. | Disk-Usage auf owned Releases/live Files begrenzen. |
| LV2-AUD-11 | P2 | Globale Library-Stats zählten den Provider-Katalog mit. | Dieselbe Owned-Semantik wie `get_statistics()` verwenden. |
| LV2-AUD-12 | P2 | Listening-Lookups mischten `name_key` mit `.lower()`. | Produzenten und Konsumenten durchgehend mit `_name_key()` normalisieren. |
| LV2-AUD-13 | P1 | Public API gab rohe lib2-Spalten an Legacy-Serializer. | Native IDs, Artwork, Status und Metadata zentral auf das API-Schema projizieren. |
| LV2-AUD-14 | P1 | Katalog-PK und Server-ID wurden in einem ungetypten Lookup vermischt. | Getrennte Katalog- und servergescoped Lookups anbieten. |
| LV2-AUD-15 | P1 | Kompatibilitätssuche gab lokale PKs an Medienserver weiter. | An Servergrenzen die gescopete `server_id` zurückgeben. |
| LV2-AUD-16 | P2 | Playlist Explorer behandelte Discography-Alben als owned. | `owned_titles` auf `origin='library'` begrenzen. |
| LV2-AUD-17 | P1 | Duplicate-Merge verlor komplementäre IDs und Nutzerzustände. | JSON per Key mergen und Rules, Attempts, Overrides sowie Snapshots umhängen. |
| LV2-AUD-18 | P1 | Neue Scan-Tracks lieferten kein `inserted`; Reconcile/History liefen nicht. | Insert-vs.-Update wieder liefern und Side Effects nativ auf lib2 ausführen. |
| LV2-AUD-19 | P2 | Gelöschte File-Historie löste weiterhin Trackbesitz auf. | Exakte und Basename-Auflösung auf lebende File-States begrenzen. |
| LV2-AUD-20 | P2 | Playlist-Status zählte Provider-Tracks als lokal. | Batch- und Einzelstatus auf owned Releases begrenzen. |
| LV2-AUD-21 | P1 | Delete-by-path entfernte Track und alle Alternate Files. | Nur die angefragte File-Row über den File-Lifecycle löschen. |
| LV2-AUD-22 | P2 | Album-Issue-Snapshot selektierte nicht vorhandene Legacy-Spalten. | Native Albumspalten unter den stabilen Snapshot-Namen aliasen. |
| LV2-AUD-23 | P2 | Navidrome-Fallback streamte mit dem lokalen PK. | Gespopte `server_id` als Subsonic Song-ID verwenden. |
| LV2-AUD-24 | P2 | Your-Artists-Modal las Legacy-Schlüssel aus rohen lib2-Rows. | Artwork, IDs und Last.fm-Daten aus nativen/JSON-Feldern projizieren. |
| LV2-AUD-25 | P2 | Library-Check gab bei nativen Scans `track_id: null` zurück. | Aktive Server-ID plus separate lib2-Katalog-ID zurückgeben. |

## 34. Finaler Cutover-, Ownership- und Media-Server-Vertrag (14. August 2026)

Dieser Abschnitt ersetzt für den Endzustand die Übergangsarchitektur aus
§32.3.1. Die Legacy-Spiegelung war nur ein Migrationshilfsmittel und darf nach
dem Cutover nicht mehr am Runtime-Pfad hängen.

| ID | Diagnose / verbindlicher Vertrag | Abnahmebedingung |
|---|---|---|
| LV2-CUT-01 | Ein Upgrade muss Legacy automatisch und fortsetzbar nach `lib2_*` importieren. Vom Erkennen der alten Quelle bis zum erfolgreichen Abschluss dürfen keine Library-Worker, Automationen, Scans, Downloads, Imports oder Repair-Schreiber anlaufen. | Persistierter Claim/Heartbeat, Startbarriere vor Worker-Start, HTTP-Jobbarriere und automatisches Fortsetzen nach Erfolg. |
| LV2-CUT-02 | `lib2_*` ist der vollständige Katalog. Eine Zeile beweist keinen Besitz: Provider-, Wanted- und Missing-Tracks dürfen ohne Datei existieren. | `owned` wird ausschließlich aus einer aktiven `lib2_track_files`-Zeile mit Pfad abgeleitet; Wanted bleibt eine eigene Projektion. |
| LV2-CUT-03 | Plex/Jellyfin/Navidrome sind nach dem Cutover keine Import- oder Ownership-Autorität. | Ein Scan darf keine Artist-/Album-/Track-/File-Zeile erzeugen und keinen Dateipfad übernehmen; er darf nur gescopte Server-IDs und technische Beobachtungen auf vorhandene aktive File-Identitäten mappen. |
| LV2-CUT-04 | Neue Katalog-/Besitzdaten entstehen nur durch die Upgrade-/Import-Pipeline. Wishlist-/Watchlist-Akquisitionen zählen erst nach erfolgreichem Download und Import dazu. Provider/Watchlist dürfen Katalogzeilen ohne Besitz anlegen. | Nur der erfolgreiche Importpfad ruft die gemeinsamen Upserts mit explizitem `allow_create=True` auf und stempelt File-Provenienz. |
| LV2-CUT-05 | Server-Cleanup darf nie Katalogidentität oder physisches Eigentum löschen. | Full Refresh, Stale Detection und Orphan Cleanup lösen nur `server_source/server_id`; File-State wird allein durch Datei-/Pfad-Lifecycle entschieden. |
| LV2-CUT-06 | Legacy ist nach dem Cutover keine zweite Bibliothek. | Keine Runtime-SQL-Reads/-Writes, keine Mirror-Trigger, kein Mirror-Drainer und keine Legacy-Divergenzprüfung; ausschließlich der Upgrade-Importer liest die alte Quelle. |
| LV2-CUT-07 | Ein nichtleerer Legacy-Pfad beweist nach einem externen Rename noch keinen gegenwärtigen Besitz. | Vor Gate-Freigabe eindeutigen Filename-Drift automatisch und re-verifiziert repointen; mehrdeutige Treffer niemals wählen, sondern bei `missing_suspected` schützen. |

**Einordnung von LV2-AUD-18:** Der Review-Kommentar verlangte zunächst, neue
Media-Server-Tracks wieder als `inserted` zu behandeln. Mit LV2-CUT-03 ist
dieser Zweig absichtlich unzulässig: Ein Scan kann keinen neuen Track erzeugen.
Die benötigten History-/Embedded-ID-Side-Effects laufen stattdessen im echten
Import-/Downloadpfad, der auch die physische Datei besitzt.

## 35. Post-Cutover-Runtime-Regressionen (14. August 2026)

| ID | Prio | Diagnose | Korrekturvertrag |
|---|---|---|---|
| LV2-RUN-01 | P1 | Ein Repair-Finding konnte nach einem Katalog-Reconcile noch die inzwischen gelöschte `lib2`-Track-ID tragen. Die physische Reparatur gelang, aber der Wanted-Outbox-Schritt brach mit „projection missing or stale“ ab und ließ das Finding endlos pending. | Nicht mehr existierende Finding-IDs ignorieren; für jeden noch existierenden Track bleibt eine fehlende oder veraltete Wanted-Projektion ein harter Fehler. |
| LV2-RUN-02 | P1 | `/api/discover/similar-artists/enrich` liefert Artists als Map nach Provider-ID, der neue React-Hook iterierte sie wie ein Array. Das löste `TypeError: e.artists is not iterable` aus und brachte die gesamte Discover-Seite in die Error Boundary. | Die vorhandene kanonische Map-Projektion `enrichUpdates()` verwenden und den echten Response-Shape im Hook-Test abbilden. |
| LV2-RUN-03 | P2 | Bei einer verifiziert leeren Media-Server-Auswahl wurden Track-Verknüpfungen gelöst, Artist-/Album-Verknüpfungen blieben jedoch servergescoped zurück. | Nach doppelter Empty-Verifikation den vollständigen Serverbeitrag detach-en; Katalogtracks und aktive physische File-Rows ausdrücklich erhalten. |
| LV2-RUN-04 | P2 | Der React-Umbau der Library-Seite entfernte stabile DOM-Anker, die Help-Tour und Download-Integration weiterhin verwenden. | Die bestehenden IDs für Suche, Monitoring, Grid, Pagination und View-Toggle am neuen Renderer bereitstellen und die Tourtexte auf die neue UI-Semantik aktualisieren. |

## 36. Download-Pipeline-Nachträge aus Discover / Your Albums (14. August 2026)

| ID | Prio | Diagnose | Korrekturvertrag |
|---|---|---|---|
| LV2-DL-01 | P1 | `Your Albums` übergibt Tracks und Albumkontext korrekt, setzt aber `is_album_download=true` und aktiviert damit den Album-Bundle-Pfad. Bricht SABnzbd die `addurl`-Antwort mit `Connection reset by peer` ab, liefert das Usenet-Plugin kein `fallback`; der Dispatcher markiert den gesamten Batch terminal als `failed`, bevor eine einzige Per-Track-Task entsteht. | Eine abgebrochene Client-Einreichung als unsicheren Submit behandeln: zuerst bounded gegen Queue/History korrelieren, um Doppelgrabs auszuschließen; nur ein bestätigter externer Job wird weiter beobachtet, andernfalls in den normalen Per-Track-/Quellen-Fallback wechseln. |
| LV2-DL-02 | P2 | Ein terminaler Album-Bundle-Fehler vor der Task-Erzeugung hinterlässt einen Batch ohne `queued`/`searching`/`downloading`-Rows. Die task-zentrierte UI zeigt dadurch nur pauschal `failed` und verbirgt Quelle, Release und konkrete Ursache. | Für den Bundle-Lifecycle eine sichtbare synthetische Statuszeile samt Phase, Quelle, Release und Fehler rendern; ein Batch ohne Tasks darf nicht informationslos erscheinen. |
| LV2-DL-03 | P1 | Die Release-Auswahl akzeptierte für den Basistrack „Legends Never Die“ eine NZB der „Alan Walker Remix“-Version; zuvor wurde „Bitch Lasagna (instrumental)“ für „Bitch Lasagna“ gewählt. Die aktuelle Wortabdeckung bestätigt nur den Kerntitel und behandelt zusätzliche Variantenqualifier als harmlos. | Provider-/Release-Identität und Variantenqualifier gemeinsam prüfen. `remix`, `instrumental`, `live`, `acoustic`, `remaster` usw. dürfen nur gewählt werden, wenn die Anfrage dieselbe Variante verlangt; bei Unsicherheit Bundle ablehnen und Per-Track-Fallback verwenden. |

## 37. Post-Remediation-Audit des lib2-Cutovers (14. August 2026)

Geprüft wurde der aktuelle Branch-Head `c04ac26fd` über die letzten 50 Commits
(`20f43a337..c04ac26fd`). Die 25 Befunde aus §33 wurden dabei nicht erneut als
offen gezählt; der Schwerpunkt lag auf Verhaltensänderungen, die durch ihre
Remediation, den finalen Cutover und die danach gelandeten Regression-Fixes
selbst entstanden oder erst jetzt kritisch wurden.

### LV2-PAUD-01 — P1 — Die Upgrade-Barriere lässt alle Nicht-POST-Mutationen durch

`web_server.py:363-369` beendet den zentralen Upgrade-Guard für jede Methode
außer `POST`, obwohl die Library-v2-API zahlreiche schreibende `PUT`, `PATCH`
und `DELETE`-Routen besitzt. Beispielsweise können während eines laufenden
Legacy-Imports Alias-Links entfernt (`api/library_v2.py:1709`),
Metadaten-Overrides verändert sowie ganze Artists oder Alben gelöscht werden
(`api/library_v2.py:4160-4218`). `_guard()` prüft nur Feature, Seitenrecht und
Adminprofil, nicht den Migrationszustand. Damit können Nutzerwrites mit dem
exklusiven Importer konkurrieren oder gerade importierte Rows wieder löschen;
LV2-CUT-01s HTTP-Jobbarriere gilt nur scheinbar.

**Korrekturvertrag:** Alle nicht sicheren HTTP-Methoden (`POST`, `PUT`,
`PATCH`, `DELETE`) an Katalog-/Import-/Download-/Repair-Grenzen müssen denselben
persistierten Migration-Guard durchlaufen. Ausnahmen werden als kleine
explizite Allowlist geführt; der Bootstrap-Endpunkt bleibt über seinen
Claim/Lease-Mechanismus serialisiert. Regressionstests müssen mindestens einen
destruktiven `DELETE`- und einen `PATCH`-Endpunkt während `migration_required`
mit `409` belegen.

### LV2-PAUD-02 — P1 — Standalone „Full Refresh“ kann die Library nicht mehr neu aufbauen

`_run_soulsync_full_refresh()` verspricht weiterhin „wipe … rebuild“
(`web_server.py:17196-17197`), ruft nach `clear_server_data('soulsync')` aber
`upsert_artist`, `upsert_album` und `upsert_track` ohne `allow_create=True` auf
(`web_server.py:17266-17307`). Seit `dfbc052ad` sind diese Helfer standardmäßig
`mapping-only`; auf einer leeren oder zurückgesetzten DB liefert schon der
erste Artist `None`, und alle Files werden übersprungen. Neue Files in einer
bestehenden Standalone-Library werden aus demselben Grund nicht aufgenommen.
Umgekehrt löscht `clear_server_data` jetzt absichtlich nichts mehr, sondern
entfernt nur Serverstempel: nicht mehr vorhandene Files bleiben deshalb als
aktive Ownership-Zeilen stehen und verlieren dabei den `soulsync`-Scope, über
den der Deep Scan sie später finden würde.

**Korrekturvertrag:** Der Standalone-Full-Refresh ist ein lokaler Importpfad,
kein Media-Server-Mapping. Neu gefundene Files müssen die gemeinsame
Validierungs-/Importpipeline durchlaufen und erst nach erfolgreichem Import
Katalog- und File-Rows erzeugen. Nicht mehr beobachtete Files laufen durch den
zentralen Missing-/File-Lifecycle; ein Refresh darf weder nur remappen noch
Rows direkt löschen. Fresh-DB-, neues-File-, entferntes-File- und
Restart-Tests gehören an diesen Pfad.

### LV2-PAUD-03 — P1 — Ein Import wird trotz fehlgeschlagenem lib2-Write als erfolgreich abgeschlossen

Der nach dem Cutover autoritative Katalogwrite bleibt als „best effort“
implementiert: `link_download_into_library_v2()` fängt jede Exception, loggt
nur auf Debug-Level und gibt `None` zurück (`core/library2/autolink.py:883-885`).
`record_download_provenance()` verschluckt dieses Ergebnis; danach ist auch
`record_soulsync_library_entry()` vollständig fail-open
(`core/imports/side_effects.py:491-495,693-694`) und läuft bei Plex, Jellyfin
oder Navidrome überhaupt nicht. `post_process_matched_download()` markiert den
Task anschließend trotzdem als `completed` und setzt
`_pipeline_import_succeeded=True` (`core/imports/pipeline.py:2065-2068,
2111-2119`). Weil Media-Server-Scans unbekannte Entities nun ausdrücklich
nicht mehr erzeugen, existiert außerhalb des Standalone-Modus kein späterer
Fallback: Das File liegt final auf Disk, Download/Acquisition können terminal
sein, aber Library, Wanted und Repair kennen es nicht.

Der Abschlussaudit fand denselben Denkfehler zusätzlich in den Race-/Duplicate-
Abkürzungen: „Source ist weg, Zieldatei existiert“ und der äußere Verification-
Wrapper behandelten die bloße Dateiexistenz beziehungsweise das Ausbleiben
eines Fehlermarkers als Erfolg. Scheiterte der lib2-Write erst nach dem Move,
konnte genau dieser Fallback den Task beim nächsten Durchlauf doch wieder grün
abschließen.

**Korrekturvertrag:** Das Registrieren der finalen File-Row ist Teil der
Import-Completion-Boundary. Entweder schlägt der Import sichtbar und
restart-sicher fehl, oder ein persistenter, idempotenter Retry-/Outbox-Anker
hält ihn bis zum erfolgreichen lib2-Commit offen. `completed` und Acquisition-
Success dürfen erst danach gesetzt werden. Ein Failure-Injection-Test muss
einen DB-Fehler nach dem physischen Move auslösen und beweisen, dass der Task
nicht terminal erfolgreich und das File nicht kataloglos bleibt. Das gilt
auch für Race Guards, bereits vorhandene Ziele, übersprungene Duplikate und die
Post-Move-Recovery; „Datei existiert“ allein ist kein Completion-Nachweis.

### LV2-PAUD-04 — P2 — Eine Media-Server-Verbindung ist weiterhin falsche Import-Voraussetzung

`is_active_media_server_ready()` lehnt Manual- und Auto-Import ab, sobald der
konfigurierte Plex-/Jellyfin-/Navidrome-Client nicht verbunden ist
(`core/imports/side_effects.py:462-487`). Die Begründung im Docstring beschreibt
noch die alte Architektur: Der Server-Scan müsse das File später in die DB
bringen. Nach LV2-CUT-03 ist genau das verboten; der gemeinsame Importpfad kann
und muss lib2 selbst schreiben. Dadurch blockiert ein optionaler, ausgefallener
Media-Server heute einen vollständig lokalen Import und verletzt die
Media-Server-Unabhängigkeit aus Guide §2.1. Bestehende Tests pinnen noch das
alte Verhalten (`tests/imports/test_import_side_effects.py:733-769`).

**Korrekturvertrag:** Import-Eligibility hängt nur von Storage-, Pfad- und
Pipeline-Gesundheit ab. Eine fehlende Media-Server-Verbindung darf höchstens
die nachgelagerte ID-Projektion als retrybar markieren, nicht den Katalogimport
verhindern. Tests müssen Offline-Import für alle drei externen Servermodi und
die spätere Mapping-Nachlieferung abdecken.

### LV2-PAUD-05 — P1 — Der Standalone Deep Scan umgeht den File-Lifecycle und löscht Katalogidentität

`_run_soulsync_deep_scan()` prüft gespeicherte Pfade mit rohem
`os.path.exists()` (`web_server.py:17443-17450`) und löscht einen vermeintlich
stalen Pfad anschließend direkt aus `lib2_track_files`. Danach löscht er
filelose Tracks sowie leere Alben und Artists physisch aus dem Katalog
(`web_server.py:17470-17498`). Damit gehen File-Historie, Providerdaten,
Monitor-Regeln, Wanted-/Outbox-Zustand und filelose Trackidentität verloren;
Path-Mapping, `missing_suspected`/`missing_confirmed`, Root-Health und der
zentrale Delete-Vertrag werden vollständig umgangen. Der prozentuale
Flood-Guard verhindert nur große Löschwellen, nicht denselben Datenverlust in
kleinen Libraries oder bei einzelnen gemappten Pfaden.

**Korrekturvertrag:** Pfade ausschließlich über `resolve_lib2_path` und den
Root-Health-Guard beobachten. Fehlende Files werden über den gemeinsamen
zweistufigen File-Lifecycle markiert und lösen Wanted/History erst nach
Bestätigung aus; Entity-Rows bleiben als Katalog-/Monitoring-Wahrheit bestehen.
Direkte `DELETE FROM lib2_track_files|tracks|albums|artists`-Statements gehören
aus dem Scan entfernt.

### LV2-PAUD-06 — P1 — Full Refresh entkoppelt alle Server-IDs vor dem ersten verifizierten Fetch

`DatabaseUpdateWorker.run()` ruft bei `full_refresh` zuerst
`clear_server_data(server_type)` auf (`core/database_update_worker.py:119-121`)
und fragt erst danach den Server ab. Liefert `_get_all_artists()` wegen eines
Verbindungsfehlers oder einer transient leeren Antwort nichts, endet der Lauf
sofort (`:140-144`). Der Katalog und die Files bleiben zwar erhalten, aber alle
Artist-/Album-/Track-Server-IDs sind bereits in einer separaten bestätigten
Transaktion entfernt. Playback, Playlist-Sync und Server-Deep-Links verlieren
damit ihre Identität bis zu einem späteren vollständig erfolgreichen Scan. Der
Deep-Scan-Pfad besitzt bereits eine doppelte Empty-Verifikation; der gewöhnliche
Full Refresh nicht.

**Korrekturvertrag:** Zuerst einen verifizierten Server-Snapshot lesen, dann
IDs per Run-Marker/upsert erneuern und erst nach einem vollständig erfolgreichen
Lauf die ungesehenen Stempel detach-en. Fehler, Stop und unbestätigtes Empty
lassen den vorherigen Mapping-Snapshot unverändert; ein bestätigtes leeres
Library-Set wird mindestens doppelt verifiziert.

### LV2-PAUD-07 — P2 — Der `soulsync`-Stempel verhindert einen späteren Media-Server-Wechsel

Standalone-Imports stempeln Artist, Album und Track mit
`server_source='soulsync'` und einer synthetischen `server_id`
(`core/imports/side_effects.py:629-672`). Die mapping-only-Fallbacks akzeptieren
eine namens-/releasegleiche vorhandene Row aber nur, wenn deren `server_id`
leer ist oder `server_source` bereits dem neuen Server entspricht
(`core/library2/media_server_sync.py:70-80,147-156,221-238`). Wechselt der
Nutzer später über die vorhandene Settings-Funktion von Standalone zu Plex,
Jellyfin oder Navidrome, kann der neue Server dieselbe owned Row deshalb nicht
claimen. Eine isolierte SQLite-Reproduktion mit einer aktiven
`soulsync`-Artist-Row ergab für `upsert_artist(... server_source='plex' ...)`
reproduzierbar `None`; der alte Stempel blieb unverändert.

**Korrekturvertrag:** Importprovenienz und Media-Server-Mappings dürfen nicht
dieselbe einzelne Spalte beanspruchen. Server-IDs gehören in eine gescopte
Mapping-Tabelle mit mindestens `(entity_type, entity_id, server_source,
server_id)`; `source='import'` bleibt File-/History-Provenienz. Mindestens der
Wechsel `soulsync → plex/jellyfin/navidrome → soulsync` muss ohne Verlust alter
Mappings und ohne Katalogduplikat getestet werden.

### Verifikation dieses Audits

- Relevante Guide-, Feature-, Status-, Issue-, Handoff- und Rewrite-Audit-
  Dokumente wurden gegen den aktuellen Codevertrag gelesen.
- Die gezielten bestehenden Suiten für Media-Server-Sync, Import-Side-Effects,
  Empty-Deep-Scan und Migration-Hardening bestanden mit **63 Tests**. Das ist
  wichtig, weil es zeigt, dass die Befunde außerhalb der bislang gepinnten
  Fälle liegen; insbesondere erzeugt das Media-Sync-Fixture importierte Rows
  ohne den realen `server_source='soulsync'`-Stempel.
- Die Cross-Source-Blockade wurde zusätzlich gegen eine temporäre
  In-Memory-SQLite-DB mit dem echten Schema reproduziert: vorhandener owned
  Artist `('soulsync', 'import-a')`, Ergebnis der Plex-Zuordnung `None`.

### Umsetzung und Produktentscheidung (14. August 2026)

Die sieben Befunde sind in einem gemeinsamen Remediation-Commit umgesetzt.
Die dabei festgelegte, für Nutzer sichtbare Semantik lautet:

| Befund | Status | Was vorher passiert wäre | Umgesetztes Verhalten |
|---|---|---|---|
| LV2-PAUD-01 | Behoben | Während des Upgrades konnten `PATCH`, `PUT` und `DELETE` weiter Daten ändern; ein laufender Import konnte die Änderung überschreiben oder mit ihr kollidieren. | Der zentrale Guard behandelt jede nicht sichere HTTP-Methode als Write. `GET`, `HEAD` und `OPTIONS` bleiben lesbar; der Bootstrap behält seine eigene Claim-/Lease-Serialisierung. |
| LV2-PAUD-02 | Behoben | „Full Refresh“ fand die Files, konnte auf einer leeren DB aber keine Rows erzeugen und hatte zuvor bereits Serverstempel gelöst. | Der Standalone-Refresh ist jetzt ein ausdrücklicher lokaler Recovery-Import über den nativen Auto-Link/Import-Writer. Er löscht vorher nichts; bereits bekannte, verschwundene Files gehen in den normalen Rescan-Lifecycle. |
| LV2-PAUD-03 | Behoben | Ein File konnte fertig auf Disk liegen und Task/Acquisition wurden trotzdem als erfolgreich beendet, obwohl lib2 es nicht kannte; Race-/Duplicate-Fallbacks konnten denselben Fehler später erneut als Erfolg maskieren. | Die finale File-Registrierung kann am Completion-Pfad nicht mehr fail-open sein. `completed`, `_pipeline_import_succeeded` und Acquisition-Success folgen erst auf eine echte File-Row; Post-Move-Recovery, bereits vorhandene Ziele und der äußere Verification-Wrapper verlangen denselben Nachweis. |
| LV2-PAUD-04 | Behoben | Ein offline Plex/Jellyfin/Navidrome blockierte Manual- und Auto-Import, obwohl der Import vollständig lokal ist. | Die Connectivity-Prüfung bleibt im Media-Server-System, ist aber keine Import-Eligibility mehr. Offline-Import ist erlaubt und die Server-Zuordnung wird bei einem späteren Scan nachgeliefert. |
| LV2-PAUD-05 | Behoben | Deep Scan benutzte rohe Pfade und löschte bei einzelnen vermeintlich fehlenden Files File-, Track-, Album- und Artist-Rows direkt. | DB-Pfade werden zentral aufgelöst; Missing-Beobachtungen gehen durch `rescan_files` und den zweistufigen Lifecycle. Der Scan enthält keine direkten Katalog-Deletes mehr. |
| LV2-PAUD-06 | Behoben | Full Refresh entfernte alle bisherigen Server-IDs, bevor überhaupt klar war, ob der Server erreichbar ist. Bei einem Netzwerkfehler blieben Library und Files zwar da, aber Playback-/Playlist-Zuordnungen waren weg. | Alte Mappings bleiben während Fetch und Verarbeitung aktiv. Fehler und unbestätigtes Empty verändern sie nicht; ein leeres Resultat braucht zwei verifizierte Antworten. Refresh- und Detach-Scopes werden für große Libraries in 500er-SQL-Batches verarbeitet. |
| LV2-PAUD-07 | Behoben | Artist/Album/Track hatten zusammen nur einen Server-Steckplatz. Ein Standalone-Stempel konnte Plex blockieren; Plex und Jellyfin konnten sich gegenseitig überschreiben. | `lib2_media_server_mappings` speichert pro Entity und Server eine eigene Zuordnung. Die UI projiziert die positiven Artist-/Track-Erkennungen als `✓ Plex`, `✓ Jellyfin` oder `✓ Navidrome`; das Entfernen eines Servers löscht nur dessen Mapping. |

Die gewünschte Grenze ist damit explizit: Media-Server dürfen keine Library-
Einträge erzeugen. Sie erkennen ausschließlich bereits importierte Rows,
liefern servereigene IDs/technische Beobachtungen für Playback und Playlist-
Funktionen und machen diese Erkennung in der Library sichtbar.

**Abschlussnachweis:** 2495 Library-v2-/Import-/Completion-Tests, 22 zusätzliche
Scan-/Datenbank-/Standalone-Regressionen und 14 gezielte UI-Tests bestanden.
Python-Compile, Diff-Whitespace-Prüfung, Frontend-Format/Typecheck und
Produktions-Build sind grün (0 neue Frontend-Fehler; 377 bereits vorhandene
Warnungen).
