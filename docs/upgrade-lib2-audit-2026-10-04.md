# Upgrade-Audit: library-overhaul → lib2

Stand: 4. Oktober 2026. Audit mit zwei Agents und zusätzlicher unabhängiger Prüfung.

**Bewertung: Den automatischen Upgrade-Pfad würde ich in diesem Zustand noch nicht freigeben.** Es gibt acht reproduzierte Fehler, davon drei mit Priorität P1. Mehrere treten auf, obwohl die Migration ausdrücklich `success=True` meldet. Betroffen sind gespeicherte Zuordnungen, die Herkunft absichtlich umgewandelter Dateien und die Bibliothekszuordnung von Reparaturen.

P1 bedeutet hier: vor einer Freigabe beheben, weil ein normaler Bestandsnutzer falsche Zuordnungen, unerwünschte Ersatzdownloads oder Reparaturen für den falschen Besitzer bekommen kann. P2 bedeutet: ebenfalls bestätigter Funktions- oder Erhaltungsfehler, aber mit kleinerem Anwendungsbereich oder geringerer unmittelbarer Auswirkung.

## Prüfstand und Grenzen

- Branch: `library-overhaul`.
- Ausgangsstand: `a0ffe79db`; zuletzt beobachteter paralleler Stand: `4108214cc`. Während des Audits liefen fremde UI-, Worker- und Acquisition-Refactorings weiter. Die Findings und Zeilennummern beziehen sich auf den Ausgangsstand. Die unmittelbar betroffenen Import-, Repair-, Sample- und Match-Resolver-Stellen blieben beim Vergleich unverändert; die spätere Ergänzung in `database/music_database.py` verschiebt dort Zeilennummern. Die neuen parallelen Commits sind kein vollständig mitgeprüfter zweiter Audit-Stand.
- Bei der Abschlusskontrolle waren außerdem parallele, uncommittete Änderungen in `bootstrap.py`, `queries.py`, `retag.py` und `sql_util.py` sichtbar. Sie stammen nicht aus diesem Audit. Diese Datei ist ein Befundbericht zum geprüften Stand und keine Bestätigung, dass spätere Änderungen oder Behebungen bereits verifiziert wurden.
- Vergleichsbasis: lokal vorhandenes `upstream/dev`, Merge-Base `732a83fe4c8e6cd1c07ec68d72087e9f4c998706`. Kein Remote-Fetch; keine Aussage über spätere Upstream-Commits.
- Agent 1: Bootstrap, Import, Schema, Wiederaufnahme und Erhaltung bestehender Daten.
- Agent 2: Repair-Jobs, Metadata-Workers, Automation und Bibliotheksgrenzen.
- Hauptprüfung: Startreihenfolge, API-Sperre, gespeicherte manuelle Matches, unabhängige Reproduktionen und Performance.
- Alle Reproduktionen verwenden temporäre SQLite-Datenbanken beziehungsweise Testdateien unter `/tmp`. Die laufende Anwendung und produktive Musikdateien wurden nicht für Tests gestartet oder bearbeitet.
- Es wurden keine Fehlerbehebungen am Anwendungscode vorgenommen. Der einzige beabsichtigte Repository-Schreibzugriff dieses Audits ist diese neue Datei. Änderungen des parallel arbeitenden Chats wurden weder zurückgesetzt noch übernommen oder committed.
- Keine vollständige Docker-/NAS-/Media-Server-Abnahme und kein Nachweis gegen alle historischen Release-Datenbanken. Die genannten Fehler sind lokal reproduziert; weitere Architektur- und Performance-Vorschläge sind separat gekennzeichnet.

## Was beim gewöhnlichen Upgrade passiert

1. `MusicDatabase` erzeugt beziehungsweise erweitert die `lib2_*`-Tabellen. Umfangreiche Konvergenzläufe wurden aus der synchronen Initialisierung in einen Hintergrundlauf verschoben (`database/music_database.py:1820–1833`).
2. lib2 ist verpflichtend. `features.library_v2=false` wird nur noch als veraltete Einstellung gewarnt und verhindert die Umstellung nicht (`core/library2/feature.py`).
3. `migration_required()` hält katalogbezogene Workers bereits vor dem ersten Bootstrap-Claim zurück. Die HTTP-Sperre blockiert mutierende lib2-Routen und eine Liste weiterer Katalogaktionen. Ein Supervisor pausiert laufende Workers und hält neue Starter zurück (`core/library2/migration_gate.py`, `web_server.py:370–397`).
4. Der automatische Bootstrap nimmt einen persistierten Claim, importiert Legacy-Zeilen in Batches und speichert Wiederaufnahmepunkte. Anschließend folgen unter anderem Monitoring/Wanted-Projektion, Server-Mappings und weitere Konvergenzarbeiten.
5. Danach laufen Tracklist- und Tag-Precache innerhalb des noch gehaltenen Claims. Artwork läuft separat. Erst nach `mark_done()` werden zurückgehaltene Dienste freigegeben (`core/library2/bootstrap.py:828–861`, `core/library2/post_import.py`).

Das ist eine sinnvolle Grundlage für Wiederaufnahme und Schutz vor konkurrierenden Schreibern. Die folgenden Fälle zeigen aber: Ein vollständig durchgelaufener Tabellenimport ist noch kein Nachweis, dass bestehende Nutzerzustände semantisch erhalten wurden.

## Bestätigte Findings

| ID | Priorität | Fehler | Betroffene Installation |
| --- | --- | --- | --- |
| F01 | P1 | Gespeichertes manuelles Matching zeigt auf einen anderen Song | Alte Server-ID kollidiert mit neuer lib2-ID |
| F02 | P1 | Absichtliche Qualitätsumwandlung verliert ihre Herkunft | Bereits heruntergerechnete oder umgewandelte Dateien mit gespeicherter Provenienz |
| F03 | P1 | Reparatur einer eigenen Bibliothek erzeugt den Ersatzwunsch für Admin | Eigene Bibliothek eines Profils |
| F04 | P2 | Sample-Studio-Stash verliert seine Quellzuordnung | Bestehende gespeicherte Chops/Rezepte |
| F05 | P2 | Cover-Reparatur bearbeitet Kopien anderer Bibliotheken | Dasselbe Album in mehreren Bibliotheken |
| F06 | P2 | Alte Genre-Cleanup-Findings bleiben dauerhaft unbrauchbar | Noch offene Findings aus der alten Version |
| F07 | P2 | Leerer Katalog verhindert Übernahme bestehender Wishlist in native Wanted-Ansicht | Wishlist vorhanden, noch keine Katalogzeilen |
| F08 | P2 | Qualitätsprofil-Remapping überschreibt bereits remappte IDs | Frühe lib2-Version mit paralleler Profiltabelle |

### F01 — Manuelle Zuordnung kann nach dem Upgrade den falschen Song treffen

**Stellen:** `core/sync/match_overrides.py:215–236`, insbesondere 225–230; Verbraucher in `services/sync_service.py:1024–1040` und `core/discovery/sync.py:191–197`.

Vor dem Umstieg enthielt `manual_library_track_matches.library_track_id` die alte Katalog-/Server-ID. Der Importer übersetzt diese Tabelle nicht. Der aktuelle Resolver interpretiert denselben Wert zuerst als neue lib2-ID und verwendet die alte Server-ID nur, wenn keine lib2-Übersetzung gefunden wurde. Beide ID-Räume können sich überschneiden.

**Reproduktion:** Zwei Legacy-Plex-Tracks haben IDs `2` und `100`. Ein gespeicherter manueller Match verweist auf `2` und `/music/chosen.flac`. Der echte automatische Bootstrap erzeugt:

```text
lib2 id=1, legacy_track_id=2,   server_id=2,   title=Chosen Track
lib2 id=2, legacy_track_id=100, server_id=100, title=Other Track
bootstrap success: True
gespeicherter manual-match id: 2
manual_match_server_id(..., 2, 'plex'): 100
erwartet: 2
```

Der Sync materialisiert den falschen, aber vorhandenen Track. Damit greift auch die Selbstheilung über den gespeicherten Dateipfad nicht: Sie wird erst bei einem fehlenden Track versucht. Ein Nutzer kann nach dem Upgrade eine Playlist mit einem anderen Song bekommen, obwohl sein ausdrücklich bestätigter Match weiterhin als gültig behandelt wird.

**Korrekturidee:** Manuelle Matches einmalig über die Legacy-/Server-Mappings und den gespeicherten Dateipfad auf den neuen Katalog umstellen. Mehrdeutige Werte nicht heuristisch als lib2-ID behandeln. Die ID-Art anschließend explizit speichern; ein bloßes Vertauschen der Resolver-Reihenfolge würde neue lib2-Matches wiederum falsch interpretieren können.

**Erforderlicher Regressionstest:** Erfolgreicher echter Bootstrap mit überlappenden IDs; danach tatsächlichen Durable-Match-Verbraucher prüfen. Der ausgewählte Song und sein Dateipfad müssen gleich bleiben.

### F02 — Herkunft absichtlich umgewandelter Dateien geht verloren

**Stellen:** `core/library2/importer.py:1919–1935` und 2170–2175; entsprechende Update-Strecke 2183–2192. Verbraucher: `core/library2/quality_eval.py:87–96`, 154–159 und 281–282.

Upstream besitzt bereits `tracks.acquired_quality_json` und `tracks.retention_json`. Beide Felder fehlen in der Legacy-Projektion und werden nicht in `lib2_track_files` übernommen. Die neue Qualitätsentscheidung benötigt sie, um eine absichtlich reduzierte Datei anhand der ursprünglich erworbenen Qualität als abgeschlossen zu behandeln.

**Reproduktion:** Auf Platte liegt FLAC 16 Bit / 44,1 kHz. Die gespeicherte Herkunft besagt: FLAC 24 Bit / 96 kHz erworben und durch eine absichtliche Downsample-Transformation ersetzt. Das Profil verlangt 24/96 bis zum Cutoff.

```text
vorher: meets_profile=True,  upgrade_candidate=False
bootstrap success: True
importiert: acquired_quality_json=None, retention_json=None
nachher: meets_profile=False, upgrade_candidate=True
erneuter 24/96-Download: replacement allowed=True
```

Damit kann das Update bereits erfüllte Wünsche wieder zu Qualitäts-Upgrades machen. Bei weiterhin aktiver Transformation entsteht unnötiger Download-/Umwandlungsaufwand; die wiederholte Upgrade-Schleife wurde hier nicht über einen echten Downloadclient ausgeführt, ihre erforderliche Fehlentscheidung aber reproduziert.

**Korrekturidee:** Beide Felder optional aus dem Legacy-Schema projizieren und auf die zugehörige importierte Datei übertragen. Beim Wiederaufnehmen bereits kopierte Provenienz erhalten und keine neuere native Herkunft überschreiben. Keinen pauschalen Rückschluss aus der aktuellen Dateiendung auf die erworbene Qualität ziehen.

**Erforderlicher Regressionstest:** Gleicher Quality-/Cutoff-Verdict vor und nach Import, einschließlich absichtlich heruntergerechneter FLAC- und lossy-retained-Dateien sowie unterbrochenem Import.

### F03 — Re-download einer eigenen Bibliothek wird für Admin eingereiht

**Stellen:** `core/library2/maintenance_sync.py:680–696`, 750–765.

Die generische Repair-Konvergenz verwendet `ADMIN_PROFILE_ID` für Monitor-Regel, Wanted-Neuberechnung und Wishlist-Mirror. Der Besitzer der konkret reparierten Datei bleibt unberücksichtigt.

**Reproduktion:** Profil 2 besitzt die Datei eines nativen `dead_file`-Findings und hat für den Track eine unmonitored-Regel. Innerhalb `library_scope(2)` wird über den öffentlichen Worker `fix_finding(..., 'redownload')` ausgeführt.

```text
Ergebnis: success=True, converged=True, wishlist_mirrored=1
Profil 2: wanted=0, Wishlist count=0
Profil 1: neue monitored=1-Regel, wanted=1, Wishlist count=1
```

Die Reparatur meldet Erfolg, während die gewünschte Bibliothek keinen Ersatz bekommt. Der Ersatzwunsch gehört stattdessen zum Admin. Entfernen- und Re-download-Intents weiterer Reparaturen laufen durch dieselbe Grenze; diese weiteren Handler wurden nicht jeweils separat vollständig reproduziert.

**Korrekturidee:** Besitzer aus den konkreten `lib2_track_files` und den im Finding gespeicherten File-IDs bestimmen. Regel, Wanted-Projektion und Wishlist mit diesem Besitzer ausführen. Bei mehreren Besitzern muss eine Aktion ausdrücklich die ausgewählten Dateien beziehungsweise Bibliotheken benennen; sie darf nicht still auf Admin zurückfallen.

**Erforderlicher Regressionstest:** Repair über öffentlichen Worker und Bulk-Fix für shared, Profil 2 und Profil 3. Kontrollieren, wem der Wishlist-Eintrag gehört und in welchem Zielordner ein späterer Import landen würde.

### F04 — Gespeicherte Sample-Studio-Rezepte behalten alte Track-IDs

**Stelle:** `core/sample/store.py:356–364`; fehlende Umstellung der Sample-Tabellen im Legacy-Finalize.

`sample_stash.track_id` verweist nach dem Upgrade unverändert auf die alte Track-ID. `_STASH_SELECT` joint jetzt unmittelbar auf `lib2_tracks.id`. Damit wird derselbe gespeicherte Wert in einem anderen ID-Raum gelesen.

**Reproduktion:** Legacy-Track `100` wird zu lib2-Track `1`; ein vorhandener Stash-Eintrag behält `track_id=100`.

```text
bootstrap success: True
importiert: id=1, legacy_track_id=100, title=Original Track
stash: track_id=100, track_title='', artist_name=''
analysis für lib2 id=1: None
sample_stems enthält weiterhin track_id=100
```

Der gespeicherte Chop als Audiodatei bleibt erhalten. Seine Quellzuordnung und das Wiederöffnen des Rezepts funktionieren jedoch nicht mehr. Wenn die neue ID `100` für einen anderen Track existiert, kann der Join stattdessen dessen Titel und Künstler liefern.

Analyse- und Stem-Caches sind ebenfalls noch unter alten IDs gespeichert. Der aktuelle Source-Signature-Vergleich erzwingt bei alten oder fehlenden Signaturen einen Neuaufbau; eine stille Wiederverwendung falscher alter Stem-Audiodateien wurde daher nicht bestätigt und wird hier nicht behauptet.

**Korrekturidee:** Dauerhafte Stash-Verweise einmalig anhand der Legacy-/Datei-Zuordnung remappen. Wiederherstellbare Analyse-/Stem-Caches entweder eindeutig mit migrieren oder ausdrücklich invalidieren. Gespeicherte Chop-Dateien und Rezepte dabei erhalten. Die Migration nicht auf Tabellen mit Fremdschlüsseldefinition beschränken: Diese Verweise sind logisch, aber nicht als FK deklariert.

**Erforderlicher Regressionstest:** Bestehenden Chop nach echtem Bootstrap auflisten und mit identischem Ursprung wieder öffnen; auch den Fall einer wiederverwendeten alten numerischen ID prüfen.

### F05 — Cover-Reparatur erweitert eine Bibliothek auf alle Dateikopien

**Stelle:** `core/repair_worker.py:4378–4384`.

Der Scanner begrenzt das Album-Subject auf die ausgewählte Bibliothek. Der Fixer sammelt dagegen sämtliche aktiven Dateipfade des lib2-Albums ohne Besitzerfilter.

**Reproduktion:** Ein lib2-Track besitzt eine shared-Datei und eine Datei von Profil 2. Unter `library_scope(2)` gibt `active_album_subjects()` nur den privaten repräsentativen Pfad zurück. Der Artwork-Fixer übergibt danach beide Pfade an `apply_art_to_album_files` und meldet `embedded into 2/2 file(s)`.

Im Probe wurden die Writer-Argumente abgefangen; keine echte Cover-Datei wurde heruntergeladen oder eingebettet. Der Produktionspfad würde damit aber auch die shared-Kopie bearbeiten. Die Bibliotheksauswahl begrenzt die tatsächliche Schreibaktion nicht.

**Korrekturidee:** Die ausgewählte Bibliothek beziehungsweise konkrete File-IDs im Finding festhalten und die Album-Erweiterung daran begrenzen. Besitzer vor dem Schreiben nochmals gegen die aktuellen File-Zeilen prüfen. Ein transienter Thread-Scope allein genügt für persistierte beziehungsweise später ausgeführte Findings nicht.

**Erforderlicher Regressionstest:** Drei Kopien desselben Albums in shared und zwei privaten Bibliotheken. Nur Dateien und Sidecars der ausgewählten Bibliothek dürfen Writer-Argumente werden.

### F06 — Bestehende Genre-Cleanup-Findings bleiben mit defektem Fix-Button liegen

**Stellen:** `core/repair_worker.py:340–354`, 887–894 und 2636–2639.

`genre_cleanup` fehlt in `NATIVE_SUBJECT_FINDING_TYPES`, obwohl sein aktueller Fixer Legacy-Subjects ablehnt. Die Startup-Bereinigung kennt den Typ deshalb nicht. Upstream hat solche Findings mit einer unpräfigierten alten Entity-ID gespeichert.

**Reproduktion:** Ein pending `genre_cleanup` für Artist `123` wird angelegt. Beide Startup-Prunes werden ausgeführt. Danach:

```text
Finding bleibt: entity_id='123', status='pending'
fix_finding: success=False, stale_subject=True
Fehler: finding predates Library v2; re-run scan
```

Ein Re-scan erzeugt einen nativen `lib2:<id>`-Subject, entfernt aber den alten unbrauchbaren Eintrag nicht durch diese Bereinigung. Die bestehende Konsistenzprüfung zählt die Typen manuell auf und wiederholt dieselbe Auslassung.

**Korrekturidee:** Den Typ kurzfristig in dieselbe Umstellung aufnehmen. Langfristig Subject-Art und Legacy-Behandlung am Handler deklarieren und Bereinigung sowie Tests daraus ableiten. Eine zweite manuell gepflegte Typenliste ist eine wiederkehrende Fehlerquelle.

**Erforderlicher Regressionstest:** Persistiertes Upstream-Finding über Worker-Start bis zur erneuten Scan-/Fix-Nutzung prüfen; keine unbrauchbaren pending Alt-Einträge.

### F07 — Wishlist ohne bestehende Katalogzeilen wird nicht automatisch materialisiert

**Stellen:** `core/library2/bootstrap.py:209–232`, 781–784; `seed_wishlist_tracks()` wird nur im Import-Finalize erreicht.

Die Quellprüfung berücksichtigt ausschließlich Zeilen in `artists`, `albums` und `tracks`. Sind alle drei leer, beendet sich der Bootstrap als `empty_source`, auch wenn der Bestandsnutzer eine gültige Wishlist hat.

**Reproduktion:** Leere vorhandene Legacy-Katalogtabellen und ein vollständiger Wishlist-Track mit Spotify-Daten:

```text
automatischer Bootstrap: skipped='empty_source'
State: waiting_for_source; Autostart darf sich beenden
Wishlist-Zeilen: 1
native Missing total: 0
stündliches reconcile_track_wishlist: scanned=0, wanted=0, wishlisted=0
erneuter Bootstrap: skipped='empty_source'
direkter Import: wishlist_tracks=1; native Missing total danach=1
```

Die alte Wishlist wird nicht gelöscht und ihre Verarbeitung bleibt grundsätzlich möglich. Der Fehler ist die fehlende native Katalog-/Wanted-Materialisierung: Ein Nutzer sieht nach dem normalen Upgrade dort keinen bestehenden Wunsch, bis ein anderer Materialisierungspfad oder manueller Import greift.

**Korrekturidee:** Unabhängige Legacy-Nutzerzustände auch ohne bereits eingescannte Katalogzeilen migrieren. Einen Wish-/Monitor-Materialisierungslauf bereitstellen, der sowohl mit leeren als auch mit gar nicht vorhandenen Legacy-Katalogtabellen funktioniert. Wishlist-/Watchlist-Bestände bei der Readiness-Prüfung ausdrücklich berücksichtigen; der Watchlist-Fall ist hier noch nicht separat reproduziert.

**Erforderlicher Regressionstest:** Wishlist-only-Installation upgraden, native Ansicht prüfen und ohne manuellen Reimport weiterverarbeiten. Separat eine wirklich neue Installation ohne Legacy-Tabellen testen.

### F08 — Umbenummerung früher lib2-Qualitätsprofile kaskadiert

**Stelle:** `core/library2/schema.py:635–649`.

Dieser Fall betrifft frühe lib2-Installationen mit `lib2_quality_profiles`, nicht eine gewöhnliche Upstream-Datenbank ohne diese Tabelle. Die Migration remappt mit mehreren aufeinanderfolgenden `UPDATE ... WHERE quality_profile_id=old_id`. Wenn eine Ziel-ID die Quell-ID einer späteren Änderung ist, werden bereits remappte Zeilen erneut verändert.

**Reproduktion mit vollständigem aktuellem lib2-Schema:**

```text
alte lib2-Profile: Balanced=1, Upgrade until top quality=2
App-Profile:      Balanced=2, Upgrade until top quality=1
vorher: Artist A → alt 1; Artist B → alt 2
nachher: Artist A → App 1; Artist B → App 1
erwartet: Artist A → App 2; Artist B → App 1
lib2_quality_profiles wurde bereits gelöscht
```

Die Zuordnung von Artist A geht verloren, und die alte Mapping-Tabelle ist anschließend weg. Die gleiche Update-Schleife wird auch für Album- und Track-Zuordnungen verwendet.

**Korrekturidee:** Pro Tabelle ein einziges `CASE`-Update oder eine temporäre Mapping-Tabelle verwenden, sodass alle Übersetzungen den unveränderten Ausgangswert lesen. Vollständiges Mapping und Transaktion prüfen, bevor die alte Tabelle gelöscht wird.

**Erforderlicher Regressionstest:** ID-Tausch `1→2, 2→1`, längere Zyklen, unveränderte IDs und unbekannte Profilnamen, danach unveränderte semantische Profilzuordnung und sicherer zweiter Start.

## Architekturverbesserungen aus den Findings

Diese Punkte sind Designvorschläge. Sie wurden nicht implementiert und sind keine zusätzlichen bestätigten Bugs.

### A01 — Migration als Erhaltung aller Nutzerzustände modellieren

Aktuell kann der Katalogimport erfolgreich sein, obwohl Matches, Stash oder Qualitätsprovenienz semantisch verloren gehen. Ein versioniertes Migrationsmanifest sollte alle dauerhaften Referenzen aufführen: Quelltabelle/-spalte, ID-Namespace, Ziel, Erhaltungsregel und gewünschte Behandlung nicht auflösbarer Zeilen. Dazu gehören ausdrücklich Verweise ohne SQLite-FK.

Jeder Abschnitt braucht einen persistierten Abschlussmarker und einen wiederholbaren Ablauf. Vor `ready` sollten Erhaltungsprüfungen laufen: Matches treffen denselben Server-Track/Dateipfad; Stash-Einträge behalten ihren Ursprung; Transformationsprovenienz ist übernommen; Wishlist/Monitor-Intent gehört weiterhin demselben Profil. Reine Zeilenzahlen sind dafür unzureichend.

Nicht auflösbare Nutzerdaten sollten mit Grund erhalten bleiben und sichtbar werden. Sie still als gültige neue IDs zu interpretieren ist besonders gefährlich. Das gilt unabhängig davon, ob der SQLite-Integritätscheck erfolgreich ist.

### A02 — ID-Namespace verbindlich machen

Neue dauerhafte Verweise sollten ihre ID-Art mitführen: etwa `lib2`-Track-ID, Server-Track-ID plus Server-Typ, Legacy-Track-ID oder konkrete File-ID. Der bestehende `lib2:<id>`-Ansatz bei Repair-Subjects zeigt bereits das richtige Prinzip.

Für Mehrdeutigkeiten bei Bestandsdaten einmalig mit Legacy-/Server-/Datei-Zuordnung übersetzen. Resolver dürfen eine bloße Zahl nicht anhand der zufälligen Existenz einer Zeile umdeuten. Möglichst einen gemeinsamen Resolver statt unterschiedlicher Fallback-Reihenfolgen pro Sync-Pfad verwenden.

### A03 — Bibliotheksgrenze am Auftrag und an jeder Schreibaktion festhalten

Job, Finding und Repair-Intent sollten eine explizite Bibliothek sowie konkrete File-IDs speichern. Beim Ausführen muss der Besitzer noch einmal aus den aktuellen Zeilen geprüft werden; inzwischen verschobene oder gelöschte Dateien dürfen den Auftrag nicht auf eine andere Bibliothek erweitern.

Wanted, Monitoring, Wishlist, Ersatzdownload, Zielordner und Dateimutationen müssen dieselbe Bibliothek verwenden. Thread-Scope ist dafür zusätzlich nützlich, ersetzt aber keine persistierte Zuordnung. Bulk-Fix und wiederhergestellte Queues müssen dieselbe Regel erfüllen. Dedup-/Job-Schlüssel sollten die Bibliothek einbeziehen, sofern unterschiedliche Bibliotheken getrennte Aufträge erzeugen können.

### A04 — Katalog-Readiness und optionale Anreicherung auseinanderziehen

Der automatische Bootstrap hält den Claim auch während der Tracklist-Provider-Abfragen und des Lesens sämtlicher Dateitags. Daher bleiben katalogbezogene Dienste länger zurückgestellt als für den reinen, lokalen Datenumstieg nötig (`bootstrap.py:841–858`, `post_import.py`, `completeness.py:884–921`).

Vorschlag: lokale Datenübernahme, gespeicherte Tracklist-Materialisierung und notwendige Erhaltungsprüfungen als klaren kritischen Abschnitt behandeln. Danach Runtime freigeben und externe Ergänzungen/Cache-Aufbau als wiederaufnehmbare Jobs ausführen. Voraussetzung ist eine präzise Definition, welche fehlenden Daten für korrekte Wanted-/Download-Entscheidungen noch kritisch sind; die Sperre darf nicht bloß früher gelöst werden.

Das UI sollte getrennt zeigen: Katalog nutzbar, noch ausstehende Tags/Tracklists/Artwork und tatsächlicher Migrationsfehler. Offline-Provider dürfen die lokale Upgrade-Nutzbarkeit nicht auf unbestimmte Zeit verlängern.

### A05 — Readiness mit expliziten Phasen und Fehlerklassen

Eine weiterentwickelte Ablaufsteuerung könnte `detected → prepared → copying → references → validated → ready` persistent abbilden. Jeder Zustand braucht definierte Wiederaufnahme- und Freigaberegeln. Optionale Cachefehler dürfen separat weiterlaufen; fehlgeschlagene Erhaltung kritischer Nutzerdaten darf nicht als fertiges Upgrade gelten.

Vor Änderungen lässt sich ein verifizierter Upgrade-Snapshot mit der bereits vorhandenen SQLite-Backup-API erwägen (`core/db_integrity.py`). Das ist eine Architekturidee; dieses Audit hat keinen konkreten Datenbankkorruptionsfall reproduziert. Ebenso Disk-full, fehlende Mounts und nicht schreibbare Pfade gezielt als Fehlerklassen testen, statt nur allgemeine Retry-Schleifen zu bewerten.

### A06 — Eine deklarierte Job-/Subject-Kompatibilität

Handlers sollten Subject-Art, Dateieffekte und Legacy-Verhalten gemeinsam deklarieren. Startup-Pruning, Migration und Konsistenztests können diese Deklaration verwenden. So entsteht nicht wie bei Genre Cleanup eine zweite, abweichende Liste. Bei unverändertem Nutzerwillen ist eine sichere Umstellung sinnvoller als ein endlos sichtbarer Alt-Fix-Button.

Die vorhandene Prüfung auf Legacy-SQL-Zugriffe bleibt nützlich, beweist aber keine erhaltene Semantik: Ein Join auf die neue Tabelle mit einer alten ID ist syntaktisch vollständig lib2 und trotzdem falsch.

## Performance

### PERF01 — Begrenzte Workers, unbeschränkte Anzahl geplanter Aufgaben

**Stellen:** `core/library2/tag_cache.py:129–139`, 167–173; gleicher Aufbau bei `core/library2/completeness.py:867–870`.

Tag-Precache lädt alle Zeilen, baut eine zweite `pending`-Liste und erzeugt sofort ein Future für jede Datei. Eine Worker-Zahl von drei begrenzt nur die gleichzeitig laufenden Aufgaben, nicht den gespeicherten Arbeitsvorrat. Tracklist-Precache plant entsprechend alle Album-IDs auf einmal ein.

**Messung des echten `precache_tag_cache` mit `tracemalloc`:** Temporärer DB-Adapter liefert die File-Zeilen; Path-Resolution gibt sofort `None` zurück. Es findet kein Lesen von Musikdateien, kein Netzwerk und kein Tag-Schreiben statt.

| Anzahl Dateien | Peak der von Python erfassten Allokationen | Laufzeit im synthetischen Probe |
| --- | --- | --- |
| 5.000 | 10,4 MiB | 0,13 s |
| 30.000 | 62,8 MiB | 0,81 s |

Damit ist ein mit der Bibliotheksgröße wachsender Speicherbedarf bereits ohne Audio-/Tag-I/O nachgewiesen. Dies sind keine Messungen des gesamten Prozess-RSS oder der realen Migrationsdauer und kein bestätigter OOM.

**Verbesserung:** File-/Album-Zeilen in Seiten lesen; nur ein kleines Vielfaches der Worker-Zahl gleichzeitig als Futures halten und abgeschlossene Aufgaben sofort freigeben. So hängt der Arbeitsvorrat vom Parallelitätslimit statt von der kompletten Sammlung ab. Aufbewahrte Ergebnisdaten ebenfalls begrenzen.

### PERF02 — Ein DB-Open und Commit pro erfolgreich gelesener Tag-Datei

**Stelle:** `core/library2/tag_cache.py:146–162`.

Jede Datei öffnet nach der Pfadauflösung eine Connection und committet den Cache einzeln. Alle Tag-Workers konkurrieren damit um denselben SQLite-Schreiber. Eine mögliche Verbesserung ist paralleles Lesen außerhalb der DB mit einem einzigen Writer, der kleine Ergebnisbatches committed. Batches begrenzen, Fortschritt weiter melden und Wiederaufnahme erhalten.

Das Verbesserungspotenzial folgt aus der aktuellen Schleife; ein realer Geschwindigkeitsgewinn wurde in diesem Audit nicht gemessen. Eine höhere Worker-Zahl allein kann den Schreibengpass nicht aufheben und sollte erst nach Messung auf langsamen Mounts verändert werden.

### PERF03 — Wartungssubjekte seitenweise verarbeiten

Verschiedene Wartungsjobs materialisieren breite Join-Ergebnisse für Scan und Scope-Ermittlung. Bei großen Bibliotheken sind seitenweises Verarbeiten und wiederverwendbare kleine Scope-Abfragen ein möglicher weiterer Gewinn. Auch dies ist eine ungemessene Optimierungsidee, kein zusätzlicher Fehlernachweis.

## Verifikation und Reproduktionen

Die bestehenden Prüfungen sind grün, obwohl die neuen Fälle fehlschlagen. Das ist eine konkrete Lücke in den Fixtures und Erhaltungsprüfungen, kein Gegenbeweis zu den Findings.

| Bestehender Testlauf | Ergebnis |
| --- | --- |
| Migration-Hardening, Migration-Review-Fixes, Bootstrap/Resume, Feature-Cutover, Two-Libraries-Upgrade, Migration-Ledger | 117 passed |
| Repair, Repair-Jobs, Maintenance-Sync, Quality-Audit-Ownership, Automation | 841 passed, 2 warnings |
| lib2 Worker-Queue, Worker-Support, portierte Metadata-Workers, Provider-Writes | 197 passed |
| Zusätzlicher Agent-Lauf: Bootstrap, Resume, Import-Fidelity, Two-Libraries-Import, Schema | 99 passed |

Diese Läufe überschneiden sich teilweise. Die Zahlen werden deshalb nicht als Anzahl unterschiedlicher Tests addiert.

Hauptlauf für die Upgrade-Prüfungen:

```bash
.venv/bin/python -m pytest -q \
  tests/library2/test_migration_hardening.py \
  tests/library2/test_migration_review_fixes.py \
  tests/library2/test_bootstrap_resume.py \
  tests/library2/test_bootstrap_import.py \
  tests/library2/test_feature_cutover.py \
  tests/library2/test_two_libraries_upgrade.py \
  tests/test_db_migration_ledger.py \
  -o cache_dir=/tmp/soulsync-upgrade-audit-pytest-cache
```

Die drei neuen Repair-Probes prüfen das gewünschte Verhalten und scheitern jeweils an der eigentlichen Behauptung:

```text
test_redownload_private_dead_file:
  erwartete Wishlist von Profil 2: 1; tatsächlich: 0
test_cover_art_keeps_files_in_selected_library:
  erwartete Writer-Pfade: private Datei; tatsächlich: shared + private Datei
test_persisted_genre_cleanup_remains_dead_after_upgrade:
  Legacy-Finding bleibt pending und sein Fix meldet stale_subject
3 failed in 0.81s
```

Temporäre Probe-Dateien für diese Session:

- `/tmp/test_lib2_audit_private_repair.py` — F03, F05, F06.
- `/tmp/soulsync-upgrade-audit/probe_manual_match.py` — F01.
- `/tmp/soulsync-upgrade-audit/probe_retention.py` — F02.
- `/tmp/soulsync-upgrade-audit/probe_sample.py` — F04.
- `/tmp/soulsync-upgrade-audit/probe_empty_wishlist.py` — F07.
- `/tmp/soulsync-upgrade-audit/probe_profile_remap.py` — F08.
- `/tmp/soulsync-upgrade-audit/probe_tag_queue_memory.py` — PERF01.

Die Probe-Dateien liegen bewusst außerhalb des Repositorys und sind nicht dauerhaft versioniert. Die Fixtures, erwarteten Ergebnisse und beobachteten Fehler stehen deshalb zusätzlich direkt bei jedem Finding in dieser Datei. Für die spätere Behebung sollten daraus permanente Regressionstests werden.

Ein ergänzender kombinierter Sync-/Import-Fidelity-Lauf wurde nach 30 erfolgreichen Tests und einer weiteren Fehlermarkierung wegen fehlenden Fortschritts abgebrochen; er wird nicht als bestandener Lauf gewertet. Ein separater diagnostischer Lauf mit `--timeout=10 -x` bestätigt einen Timeout in `tests/sync/test_sync_durable_match.py::test_durable_match_used_when_volatile_cache_wiped`. Der Hauptthread wartet in `asyncio.run → run_until_complete → selectors.EpollSelector.poll`; der gezeigte Executor-Thread wartet auf weitere Arbeit. Der Lauf meldet `1 failed, 1 warning` und benötigt trotz des 10-Sekunden-Testlimits insgesamt 310 Sekunden. Die Ursache wurde nicht abschließend geklärt. Dieser Timeout ist ein offener Verifikationspunkt und kein zusätzlich bestätigter Upgrade-Produktfehler. Log: `/tmp/soulsync-upgrade-audit/sync-durable-diagnostic.log`.

Beim ersten Upgrade-Testlauf starteten einzelne Artwork-Background-Tasks nach dem Testabschluss Provider-Abfragen; diese scheiterten an der verfügbaren Netzwerkauflösung und erzeugten Shutdown-Logging-Fehler. Die 117 Tests selbst bestanden. Daraus wird keine Aussage über die Live-Erreichbarkeit dieser Provider abgeleitet.

## Empfohlene Reihenfolge und Freigabekriterien

1. F01–F03 beheben und gezielte Regressionstests hinzufügen: Identität, Transformationsprovenienz und Besitzer müssen das Upgrade überstehen.
2. F04–F08 mit Tests für echte Bestandszustände korrigieren. Behebungen insbesondere als wiederholbare Datenmigration für bereits teilweise umgestellte Installationen entwerfen.
3. Eine kleine historisch realistische Upgrade-Fixture aufbauen: sparse numerische und TEXT-Server-IDs, manuelle Matches, Stash, Provenienz, Wishlist, offene Findings, shared/private Dateikopien und verschiedene Qualitätsprofil-IDs.
4. Prozessabbruch nach jedem Batch und in jedem Finalize-Abschnitt erzwingen. Wiederaufnahme und erneuten Start prüfen: gleiche Nutzerentscheidungen, keine Duplikate, keine fremden Dateien, keine verlorenen Aufträge.
5. Mount-Ausfall, nicht schreibbare Ziele, Disk-full und offline Provider prüfen. Ein fehlender Mount darf nicht als Bestätigung fehlender Musik oder als Anlass für massenhafte Ersatzdownloads dienen.
6. Erst danach optionale Hydration aus dem kritischen Upgrade-Abschnitt herauslösen und PERF01/PERF02 mit einer großen Fixture messen.
7. Vor Freigabe einen gewöhnlichen Container-Upgrade samt Neustart gegen eine Kopie einer verifizierten Release-Datenbank durchspielen. Ohne manuelle Import-Klicks müssen Library, Playlist-Sync, Wishlist, Automation und Tools für denselben Nutzer und dieselbe Bibliothek weiter funktionieren.

Freigabekriterium ist erhaltenes Nutzerverhalten einschließlich gespeicherter Entscheidungen. `success=True`, eine sichtbare Library und eine grüne bestehende Testsuite allein erfüllen dieses Kriterium für die oben reproduzierten Fälle noch nicht.
