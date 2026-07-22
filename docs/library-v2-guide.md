# Library V2 — Ziel, Philosophie und Architekturleitfaden

Dieses Dokument hält fest, **warum** Library V2 existiert, welche
Produktentscheidungen verbindlich sind und welche Architekturgrenzen beim
Weiterbauen nicht verletzt werden dürfen. Es enthält bewusst keine
Fortschrittsangaben. Der einzige Ort für „offen“, „umgesetzt“, Commit-Hashes
und Testergebnisse ist [library-v2-status.md](library-v2-status.md).

Die fachlichen Feature-Spezifikationen stehen in
[library-v2-features.md](library-v2-features.md), Fehlerbilder und
Root-Cause-Analysen in [library-v2-issues.md](library-v2-issues.md).

---

## 1. Warum dieses Projekt existiert

SoulSyncs bisherige Library ist im Kern ein flacher, read-only Spiegel eines
externen Media-Servers. Das reicht zum Anzeigen vorhandener Songs, aber nicht
für einen Library-Manager, der Besitz, gewünschte Titel, verschiedene
Releases, mehrere Dateien, Upgrades, Downloadversuche, Quarantäne und
Datei-Lifecycle zuverlässig verwalten soll.

Library V2 soll deshalb ein **Lidarr-artiger, DB-zentrierter Library-Manager**
sein, der vollständig auf SoulSyncs eigener Such-, Download-, Processing- und
Tagging-Pipeline läuft. Die Informationsarchitektur und die verständlichen
Arbeitsabläufe dürfen sich an Lidarr orientieren; die Ausführung bleibt jedoch
SoulSync-eigen und nutzt alle konfigurierten Quellen, nicht einen Media-Server
als Autorität.

Das ursprüngliche Rollout-Modell war opt-in und parallel zur Legacy-Library.
Der spätere native Repair-Cutover hat diese Grenze verändert. Daraus folgt ein
verbindlicher Kompatibilitätsauftrag: Solange Legacy-Wishlist, Watchlist,
Importer oder `legacy_*`-Referenzen noch existieren, müssen Migration,
Rollback und Dual-Write-Grenzen ausdrücklich beschrieben und getestet sein.
Ein Feature-Flag darf niemals dazu führen, dass Repair still gar nichts tut
und „keine Findings“ fälschlich wie „alles sauber“ aussieht.

### 1.1 Produktziel

Library V2 stellt einen zusammenhängenden Katalog und eine verständliche
Oberfläche für folgende Wahrheiten bereit:

- stabile Artist-, Album-, Track- und File-Identitäten;
- Release Group, konkrete Edition und Recording als getrennte Konzepte;
- mehrere Dateien pro Track, davon genau eine definierte Primary-Datei;
- reale Datei-Lifecycle-Zustände statt bloßer Pfadstrings;
- Monitoring-Intent als `lib2_monitor_rules → lib2_wanted_tracks`;
- Watchlist und Wishlist als bestehende Benutzer- bzw. Ausführungsgrenzen;
- app-weite Quality Profiles mit nachvollziehbarer Vererbung;
- persistente Search-, Grab-, Download-, Import-, Retry-, Quarantäne- und
  History-Korrelation;
- restart-sichere Beobachtung externer Download-Clients;
- Provider-Snapshots und User-Overrides mit klarer Provenienz;
- sichere Dateiaktionen mit Preview, Root-Prüfung und Journal.

Funktionalität und nachvollziehbare Zustände haben Vorrang vor dekorativer
Song-Masse. Ein Nutzer soll erkennen können: Was besitze ich? Was fehlt? Was
ist monitored? Welche Qualität gilt und woher kommt sie? Was läuft gerade?
Warum wurde etwas abgelehnt? Was geschah mit einer Datei?

### 1.2 Ausdrückliche Nicht-Ziele

Folgende Punkte wurden vom Nutzer ausdrücklich abgelehnt oder zurückgestellt
und dürfen nicht als vermeintliche „Paritätslücke“ ungefragt gebaut werden:

- keine Abhängigkeit von Plex, Jellyfin, Navidrome oder einem anderen
  Media-Server, auch nicht für Artwork;
- kein Kalender bzw. keine Upcoming-Releases-UI;
- keine Artist-Top-Tracks-Sektion;
- kein separates „Add Artist“-System neben Search und Watchlist;
- kein drittes Metadata-Profile-System neben Watchlist-Regeln und
  `monitor_new_items`;
- kein Artist-Mass-Editor als Selbstzweck;
- kein A-Z-Selector, keine rohe Artist-JSON-Inspektor-UI und kein
  Nicht-Admin-Report-Button;
- keine eigene Blocklist- oder Unmapped-Files-Seite, solange vorhandene
  Repair-/Diagnosewege genügen;
- kein „Search on monitor“-Automatismus; gezielte Automatic Search bleibt
  eine bewusste Aktion;
- kein Discography-Batch-Download-Modal;
- kein paralleles Track-Redownload-System; falls später gewünscht, muss es
  neu suchen und erst nach verifiziertem Import atomar ersetzen;
- keine vollständige Legacy-/Lidarr-Parität nur um einer Checkliste willen.

Aufgeschoben sind M3U-/Roster-Export sowie Reidentify/„I Have This“. Sie sind
keine stillschweigend angenommenen Anforderungen.

---

## 2. Nicht verhandelbare Produkt- und Designregeln

### 2.1 Media-Server-Unabhängigkeit

Library V2 muss ohne Media-Server vollständig funktionieren. Artwork folgt
dieser Reihenfolge:

1. ein explizites manuelles Override;
2. bei Artists ein echtes Provider-Artist-Foto;
3. bei Albums/Tracks eingebettetes Cover aus einer vorhandenen Datei;
4. Provider-Artwork als Fallback;
5. bei Artists ein eingebettetes Albumcover als weiterer Fallback;
6. lokaler Cache bzw. Placeholder.

Der verwaltete Cache liegt unter `<db_dir>/lib2_artwork/` und wird über
`/api/library/v2/artwork/<kind>/<id>[?size=thumb]` ausgeliefert. Musikordner
können read-only sein; `artist.jpg`/`cover.jpg` dort sind höchstens optionale
Exports, nie die verlässliche Primärquelle.

### 2.2 Monitoring nutzt vorhandene Systeme

- Artist-Monitoring entspricht der Watchlist.
- Album-, Single- und Track-Monitoring entspricht der Wishlist bzw. der
  daraus abgeleiteten Wanted-Projektion.
- Ein gewishlisteter Track monitort nie automatisch den ganzen Artist oder
  dessen Discography.
- Ein erfolgreicher Download darf Monitoring nicht automatisch entfernen,
  wenn es für spätere Cutoff-Upgrades weiter benötigt wird.
- Quality ist orthogonal zu Monitoring: Das Profil entscheidet, **welche**
  Qualität akzeptiert bzw. gesucht wird, nicht **ob** etwas wanted ist.
- Ein ausdrücklicher Track-Entscheid gewinnt gegen Album-/Artist-Kaskaden.

Watchlist/Wishlist sind während der Übergangsphase bestehende Bedien- und
Ausführungsgrenzen, aber nicht die Track-Intent-Wahrheit. Die autoritative
Kette lautet `lib2_monitor_rules → lib2_wanted_tracks`; Mirroring erfolgt über
eine transaktionale Outbox und einen idempotenten Reconciler.

### 2.3 App-weite Quality Profiles

Library V2 verwendet ausschließlich die app-weite Tabelle
`quality_profiles`. Es gibt keine parallele Kopie. Alle Pipeline-Stufen lösen
das effektive Profil live über den gemeinsamen Resolver auf; denormalisierte
Snapshots einzelner Quality-/AcoustID-Flags sind nicht autoritativ.

Für einen Track gilt diese Priorität:

1. explizites Track-Profil;
2. explizites Album-/Release-Profil;
3. explizites Artist-Profil;
4. Playlist-Default, falls kein höherer expliziter Wert existiert;
5. app-weites Default-Profil.

Explizite und geerbte Werte müssen getrennt erkennbar bleiben. UI, Wanted,
Search-Ranking, Import-Gate und Upgrade-Evaluation benutzen denselben
serverseitigen Resolver und zeigen Profil **plus Herkunft**. Eine Kaskade darf
keine explizite Kindentscheidung überschreiben.

Treffen zwei gleichrangige Playlist-Profile auf denselben Track, wird der
Konflikt sichtbar gemacht und vom Nutzer entschieden; „last write wins“ oder
eine unsichtbare automatische Wahl ist verboten.

### 2.4 Datenbank als Source of Truth

Jede Datei-Location liegt als eigener Datensatz in `lib2_track_files`.
Filesystem, Legacy-Spiegel und externe Clients sind beobachtete Systeme, aber
nicht die einzige Katalogwahrheit. Ein physischer Move, Delete, Replacement
oder Quarantäneübergang ist erst abgeschlossen, wenn Disk und alle
autoritativen Indizes atomar oder restart-sicher synchron sind.

Mehrere Dateien pro Track sind erlaubt, etwa FLAC und MP3 oder alte und neue
Datei während eines Upgrades. `is_primary` und eine deterministische
Auswahlstrategie verhindern `ORDER BY id LIMIT 1`-Zufall. Zustände wie
`active`, `missing_suspected`, `missing_confirmed`, `quarantined` und
`deleted` bleiben auditierbar.

### 2.5 Provider-Identitäten

Provider-IDs sind immer qualifiziert (`spotify`, `musicbrainz`, `deezer`,
`itunes`, …). Eine Fallback-Anfrage, die tatsächlich Deezer liefert, darf
keine Deezer-ID in ein Spotify-Feld schreiben. Alle konfliktfreien IDs einer
Entity bleiben erhalten; die Provider-Reihenfolge bestimmt nur, wen man
zuerst fragt.

Starke IDs schlagen Namensheuristiken. Namen dürfen nur dann als Fallback
dienen, wenn keine widersprechende starke Identität existiert. Nicht-lateinische
Schriften müssen Unicode-erhaltend normalisiert werden.

### 2.6 Admin-Grenze

Library V2 besitzt einen globalen, admin-gesteuerten Katalog- und
Monitoring-Intent. Andere Haushaltsprofile behalten ihre eigene Legacy-
Watchlist/Wishlist, dürfen aber nicht den globalen V2-Intent oder die
Quality-Zuweisungen des Admins mutieren. Diese Grenze wird serverseitig
erzwungen und nicht nur durch ausgeblendete UI dargestellt.

---

## 3. Reuse-First-Philosophie

Library V2 ist eine neue Katalog- und Orchestrierungsschicht, **keine zweite
Download-App innerhalb von SoulSync**. Bestehende, kampferprobte Services
werden wiederverwendet oder so extrahiert, dass Legacy und V2 dieselbe
Semantik aufrufen.

### 3.1 Was zwingend geteilt bleibt

- Search-Mode, Source- und Protokoll-Prioritäten;
- `download_source.hybrid_order`, `best_quality` und Source-Fallback;
- Candidate-Walk, Retry, Backoff und Next-Candidate-Verhalten;
- Quality Targets, Cutoff, Upgrade-Policy und Fallback;
- Stability-, Integrity-, AcoustID-, Tagging-, Conversion- und Import-Checks;
- Quarantäne, Approval und precise Blocklisting;
- Tag Writer, ReplayGain-, Lyrics- und Repair-Implementierungen;
- die vorhandene Watchlist-/Wishlist-Ausführung;
- Pfad-Mapping und Root-Health-Prüfung.

Wichtige wiederverwendete Einstiegspunkte sind unter anderem:

- Watchlist: `database/music_database.py`, `core/watchlist_scanner.py`;
- Wishlist: `core/wishlist/service.py`, `POST /api/wishlist/process`;
- Search/Download: `core/search/orchestrator.py`,
  `core/download_orchestrator.py`, `core/downloads/task_worker.py`;
- Import: `core/imports/pipeline.py`, `file_integrity.py`, `guards.py`,
  `quarantine.py`;
- Tagging/Repair: `core/tag_writer.py`, `core/repair_jobs/`;
- Artwork: `core/metadata/art_apply.py`, `artist_image.py`, `art_lookup.py`.

### 3.2 Was Library V2 ergänzen darf

V2 ergänzt nur Informationen, die die Main-Pipeline fachlich nicht kennt:

- persistente Acquisition-Request/Grab/Import/History-Korrelation;
- Release-Group-, Edition- und Recording-Kontext;
- Bundle-Inventar und Edition/Track-Matching;
- restart-sichere Adoption externer Client-Jobs;
- atomare Writes in `lib2_*` **nach** erfolgreicher gemeinsamer Pipeline;
- Entity- und File-History für fehlgeschlagene wie erfolgreiche Versuche.

Ein Bundle-Importer ist daher nur Koordinator. Er inventarisiert Output,
ordnet Files den erwarteten Tracks zu und delegiert jedes File an die
gemeinsame Post-Processing-Pipeline. Er darf Quality, AcoustID, Quarantäne,
Retry oder Finalization nicht selbst nachimplementieren.

### 3.3 Eligibility Gate statt zweiter Decision Engine

Der Name „Decision Engine“ war irreführend. Source- und Quality-Entscheidungen
gehören in die gemeinsame Pipeline. Das `Entity-Eligibility-Gate` hat nur zwei
Aufgaben:

1. prüfen, ob ein bereits source-/quality-seitig zugelassener Kandidat zur
   konkret angefragten Edition passt;
2. einen einzelnen, ausdrücklich überschreibbaren Ablehnungsgrund über eine
   auditierte Admin-Force-Aktion erlauben.

Persistenz und History sind eigene Module; das Gate filtert, es ist nicht das
Journal.

### 3.4 Force-Grab und Quarantäne

Ein Force-Grab speichert exakt den übergangenen Reason-Code. Meldet der reale
File-Check später denselben Grund, gilt die bereits erteilte Zustimmung und
die Pipeline darf diesen einen Check fortsetzen. Ein anderer Grund — etwa
Integrity, AcoustID oder falscher Artist — geht normal in die Quarantäne.
Force ist kein globales „alle Checks überspringen“.

### 3.5 Generische Verbesserungen werden separat gedacht

Wenn Acquisition-Arbeit auch der bestehenden Main-Pipeline unabhängig von
Library V2 nützt, gehört sie in einen kleinen, separat reviewbaren
Main-Pipeline-Änderungssatz. Historische Beispiele und Kandidaten:

- Path-Mapping-/Root-Health-Diagnose;
- Usenet Minimum Age und Retention mit deaktivierten Defaults und Settings-UI;
- source-/indexer-/GUID-genaues Blocklisting mit Reason, Expiry und Audit;
- append-only Download-Audit-History;
- Client-Monitor-Reconciliation als geteilter Algorithmus.

Diese Regel hält den Library-V2-Änderungssatz kleiner und verhindert, dass
allgemeine Sicherheits- oder Reliability-Verbesserungen an einen
experimentellen Katalog-Cutover gekoppelt werden.

### 3.6 Warum Quality Profiles aus Library V2 herausgelöst wurden

Quality Profiles waren zunächst im parallelen `lib2_*`-Schema mitgebaut. Das
war fachlich falsch und erschwerte einen unabhängigen Upstream-Review: Profile
sind eine app-weite Pipelinefähigkeit, keine Library-V2-Eigenschaft. Deshalb
wurde das allgemeine Schema nach `core/quality/schema.py` extrahiert und von
`database/music_database.py` direkt initialisiert. Eine Installation soll
Quality Profiles nutzen können, ohne je eine `lib2_*`-Tabelle anzulegen.

Die Extraktion war bewusst eine echte Subtraktion: V2-Route, V2-Schema und
V2-spezifische Per-Track-Links gehören nicht in den eigenständigen
Quality-Profile-Änderungssatz. Library V2 referenziert danach nur noch die
app-weite Tabelle und ergänzt ihren Track-/Album-/Artist-Kontext.

Dabei wurde eine wichtige Semantik korrigiert: Wishlist speichert nur
`quality_profile_id`. Jede Pipeline-Stufe löst Settings live auf. Besonders
`acoustid_required=False` bedeutet lenienter Verification-Vertrag, nicht
„AcoustID vollständig überspringen“. Deep Verify, Replacement, Downsample und
Lossy Copy müssen dasselbe Profil verwenden wie Search und Import.

Dieses Split-Muster gilt auch für Main-Pipeline-Hardening: Ein generischer
Fix wird zuerst unabhängig reviewbar gemacht; V2 rebased anschließend auf
den geteilten Service, statt eine zweite Variante zu behalten.

---

## 4. Architekturentscheidungen (ADR)

### ADR-01 — Library V2 ist admin-gesteuert

Es gibt genau einen maßgeblichen V2-Monitoring-Intent. User-Profile sind eine
andere Achse als app-weite Quality Profiles. Nicht-Admin-Pfade dürfen keine
V2-Materialisierung oder Admin-Quality-Zuweisung auslösen.

### ADR-02 — Wanted hat eine eigene Autorität

`lib2_monitor_rules → lib2_wanted_tracks` ist die Track-Intent-Wahrheit.
Watchlist/Wishlist bleiben während des Cutovers über eine transaktionale
Outbox und periodische Reconciliation angebunden. Best-effort Dual Writes
ohne Retry-Anker sind nicht zulässig.

### ADR-03 — Multi-File mit definierter Primary-Datei

Mehrere Files pro Track sind erlaubt. Primary-Auswahl bevorzugt aktive,
verlustfreie und qualitativ bessere Dateien und verwendet Aktualität nur als
Tiebreaker. File-semantische Findings referenzieren eine File-ID, nicht bloß
die Track-ID.

### ADR-04 — Release Group und Edition sind getrennt

`lib2_albums` repräsentiert die Release Group. Editionen,
`lib2_recordings` und `lib2_release_tracks` bilden konkrete Releases und
Recordings ab. Recordings werden nur über harte IDs wie ISRC,
MusicBrainz-Recording oder Spotify-ID zusammengeführt; Titelähnlichkeit allein
darf Live-, Remix- oder Remaster-Aufnahmen nicht verschmelzen. Unsichere
Canonical-Entscheidungen gehören in Review.

### ADR-05 — Katalogentfernung und physisches Löschen sind getrennt

„Aus der Library entfernen“ und „Datei auf Disk löschen“ sind zwei getrennte
Commands, auch wenn die UI sie in einem gemeinsamen Dialog anbietet.
Physisches Löschen verlangt Preview, Snapshot-Token, Root-Safety,
Bestätigung, Journal und Crash-Recovery. Ein Entity-Delete darf erst nach
erfolgreicher Dateioperation committen.

### ADR-06 — Providerdaten und User-Overrides bleiben getrennt

Providerwerte tragen Provenance und Snapshot-Version. Ein konkretes
User-Override gewinnt, ohne Provider-Refresh dauerhaft zu blockieren oder
überschrieben zu werden. Ein blindes `COALESCE`, das einmalige Fehler für
immer einfriert, ist kein gültiges Merge-Modell.

### ADR-07 — Externer Client ist die Live-Queue

Live-Fortschritt wird aus dem Download-Client gelesen; V2 persistiert
Korrelation, Intent und Lifecycle, aber keine zweite fragile Queue-Kopie.
Nach Neustart werden Jobs per Client-ID bzw. eindeutigem Fallback adoptiert.
Cancel ist zweistufig: Client-Aktion plus persistente Zustandsänderung.

### ADR-08 — Downloadquellen deklarieren Fähigkeiten

Quellen deklarieren explizit `recording_download` oder
`release_bundle_download`, ID-Suche, Queue-/Cancel-Fähigkeit und verfügbare
Quality-Metadaten. Username- oder Dateiname-Heuristiken dürfen nicht raten, ob
ein Kandidat Track oder Album-Bundle ist.

---

## 5. Technische Invarianten

- Jeder V2-Dateizugriff läuft über
  `core/library2/paths.resolve_lib2_path`; gespeicherte Pfade können die
  Media-Server-Sicht sein.
- Background-Threads rufen nie `_profile()` auf. Das aktive Profil wird im
  Request-Kontext aufgelöst und explizit weitergegeben.
- SQLite: V2-Flag-/Intent-Write committen und Write-Lock freigeben, bevor
  Watchlist/Wishlist-Methoden eigene Connections öffnen.
- Bulk-Re-Monitor verwendet `_NOT_CONSOLIDATED_SQL`; bewusst zur kanonischen
  Duplikatseite verschobene Files werden nicht erneut wanted.
- „Search Monitored“ läuft über `POST /api/wishlist/process`, nie als blinder
  Direktgrab.
- Ein File an einem `origin='discography'`-Album promoted dessen Origin zu
  `library`. Sichtbarkeit „My Library“ bedeutet `origin='library' OR
  monitored`.
- Erste Discography-Expansion monitort nie automatisch den gesamten
  Backkatalog. Re-Expansion wird über `discography_synced_at` erkannt.
- `monitor_new_items='new'` nimmt nur eindeutig neue, datierte Releases nach
  dem vorherigen Cutoff; undatierter oder verspätet gelieferter Backkatalog
  bleibt unmonitored.
- Quality-Profile-ID `1` wird nie hart codiert. Fallback läuft über
  `default_quality_profile_id`.
- Legacy-IDs sind opaque `TEXT`, nicht zwingend Zahlen. Media-Server-IDs
  können numerisch, UUID-förmig oder Spotify-Base62 sein; kein V2-/Importer-
  Pfad darf sie ungeprüft mit `int()` konvertieren.
- `acoustid_required` steuert die Strenge des Verification-Gates, nicht das
  vollständige Überspringen von AcoustID. Einen Check ganz zu überspringen
  bleibt eine explizite, auditierte Per-Download-Nutzerentscheidung.
- Profile speichern einen Pointer und lösen Einstellungen live auf. Ein
  später editiertes Profil darf nicht durch alte denormalisierte Wishlist-
  Flags wirkungslos bleiben.
- Terminale Tasks dürfen durch stale Runtime-Kontexte nicht wieder als aktiv
  erscheinen.
- Ein fehlender Pfad wird bei gesundem Root zuerst `missing_suspected` und
  erst nach Bestätigung `missing_confirmed`. Ein ungesunder Root bestätigt
  niemals einen Miss.
- Direkte Track-Automatic-Search ist transienter Nutzer-Intent: Sie darf einen
  unmonitored Track suchen, ohne Monitoring oder Wishlist dauerhaft zu
  verändern.
- Artist-/Album-Automatic-Search bleibt wanted-only und darf nie die globale
  Wishlist als versteckten Fallback starten.
- Ein bestätigter Search-/Playlist-/Watchlist-/Acquisition-Intent
  materialisiert Artist, Release und Track idempotent **vor** dem Download.
- Eine alte Datei bleibt bei Replacement bis zum vollständig verifizierten
  Import erhalten. Danach wird nur die alte Datei derselben Track-Entity
  entfernt; Remix/Live/Remaster bleiben eigene Entities.
- Ein Import ist erst abgeschlossen, wenn Disk, V2, Legacy-/Media-Projektion,
  Runtime und Acquisition entweder synchron oder explizit fehlgeschlagen sind.

---

## 6. Arbeits- und Verifikationsregeln

1. Vor Änderungen Branch-HEAD, Upstream und Merge-Base prüfen.
2. Bestehende fremde Worktree-Änderungen nicht überschreiben.
3. Vor einem Bugfix einen Regressionstest oder ein isoliertes
   Reproduktionsszenario herstellen.
4. Overhaul-Regressionen und nach der Divergenz entstandene Upstream-Fixes
   getrennt bewerten.
5. Ein Fix ist erst belastbar, wenn Altverhalten, V2-Verhalten, Upgrade,
   Wiederholung und Restart geprüft wurden.
6. Grüne vorhandene Tests allein belegen nichts, wenn sie eine alte falsche
   Semantik pinnen oder den Grenzfall nicht auslösen.
7. Keine Platzhalter-MVPs für sicherheits- oder lifecycle-relevante Flows.
8. Mutierende Dateioperationen immer mit Root-, Mapping-, Restart- und
   Failure-Injection-Fällen prüfen.

### 6.1 Lokale Verifikation

- Docker-Build: `docker build -t soulsync:dev .`
- Frontend-Builder/Typecheck: `docker build --target webui-builder .`
- gezielte Python-Suite: `pytest tests/library2`
- breitere relevante Suiten: `tests/acquisition`, `tests/imports`,
  `tests/wishlist`, `tests/repair`, `tests/repair_jobs`, `tests/automation`
- Frontend: Tests, Formatter/Type/Lint und Production Build
- realer E2E-Lauf mit einer **Kopie** von Config/DB und gemounteter Musik

Nie mit einem Host-`sqlite3` auf eine live in Docker gebundene DB zugreifen;
das kann die Anwendung blockieren. Produktive Daten werden für Audits nicht
mutiert. Datenreparaturen beginnen mit Backup und read-only Dry Run.

### 6.2 Release-Gate

Vor einem Release werden mindestens geprüft:

- Clean-HEAD Backend- und Frontend-Suiten;
- frische Installation und Upgrade einer bestehenden Installation;
- leere, große und teilmigrierte Datenbank;
- Restart während Bootstrap, Transfer, Quarantäne und Import;
- Soulseek-, Torrent- und Usenet-Client-Adoption;
- Windows-/Docker-Pfad-Mappings und ungesunder Storage-Root;
- Delete/Recycle/Recovery und Replacement;
- Jobs über fünf Minuten ohne erfundenen UI-Endstatus;
- mehrdeutiger Bundle-Import über die Acquisition-Review-UI.

Die jeweilige aktuelle Freigabeentscheidung gehört ausschließlich in die
Statusdatei.

---

## 7. Dokumentationsgrenzen und Quellenabdeckung

Die frühere Doku bestand aus neun Dateien mit rund 12.000 Zeilen. Ihre Inhalte
sind jetzt nach Bedeutung verteilt:

| Alte Quelle | Neue autoritative Stelle |
|---|---|
| `library-v2.md` — Ziel, Regeln, ADRs, Nutzerentscheidungen | Guide und Features; Implementierungs-/Verifikationshistorie im Status |
| `library-v2-review-findings-2026-07-22.md` | Issues; Remediation und Tests im Status |
| `library-overhaul-regression-audit-2026-07-21.md` | Guide (Kompatibilitätsvertrag), Issues (Findings), Status (Checkpoint/Release-Gate) |
| `library-overhaul-branch-review-2026-07-19.md` | Issues; PR-Split-/Cleanup-Zustand im Status |
| `library-v2-bug-tracker-2026-07-18.md` | Guide (Invarianten), Issues (Diagnosen), Status (Umsetzung/Abnahme) |
| `library-v2-tool-integration-audit-2026-07-18.md` | Features (Tool-Vertrag), Status (Migration/Tests) |
| `library-v2-deep-dive-findings-2026-07-16.md` | Features (angenommene UX/Parity), Issues (Bugs/Root Causes), Status (Historie) |
| `library-v2-ui-requirements.md` | Features (Nutzeranforderungen und UI-Entscheidungen), Status (Umsetzung) |
| `quarantine-approve-orphan-bug-2026-07-20.md` | Issues (vollständige offene Diagnose), Status (aktueller Stand) |

Bei Widersprüchen gilt die neuere ausdrückliche Nutzerentscheidung. Besonders
das Nutzerreview vom 17. Juli ersetzt ältere Vorschläge zu Profilvererbung,
Artist Settings, Artwork-Reihenfolge, Search, Track-Detail und Delete-UX.
