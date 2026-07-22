# Library V2 — zentraler Status-, Commit- und Verifikations-Tracker

Diese Datei ist der **einzige** Ort für Fortschritt, „offen/erledigt“,
Commit-Referenzen, Teststände und Release-Einschätzung. Guide, Features und
Issues beschreiben ausschließlich Zweck, gewünschtes Verhalten und technische
Diagnosen.

Stand: 22. Juli 2026, einschließlich der Review-Remediation bis `aabf5445`
und der Dokumentkonsolidierung danach.

## 1. Statusbegriffe

| Begriff | Bedeutung |
|---|---|
| Verified | Implementiert und durch die in dieser Datei genannte gezielte bzw. vollständige Prüfung belegt |
| Implemented | Code vorhanden und gezielt geprüft; keine Aussage über vollständigen Release-Gate |
| Partial | Ein klar benannter Teil fehlt weiterhin |
| Pending | Noch nicht implementiert bzw. Root Cause noch nicht bestätigt |
| Decision only | Produktentscheidung ist festgehalten; es gibt absichtlich kein Feature |
| Deferred | Bewusst zurückgestellt |

„Implemented“ oder „Verified“ bedeutet nicht automatisch „production ready“.
Der Release-Gate-Stand steht in Abschnitt 8.

---

## 2. Feature-Status

| ID | Feature | Status | Referenz | Abdeckung / Rest |
|---|---|---|---|---|
| [F-01](library-v2-features.md#feat-artwork) | Media-server-unabhängiges Artwork | Verified | Deep-Dive §28, Security-Fix `80b5af95` | Picker, Embed, Cache-Bust und Fetch-Hardening gezielt geprüft |
| [F-02](library-v2-features.md#feat-monitoring) | Monitoring, Watchlist/Wishlist, Outbox | Verified | P3/§82, Regression-Checkpoint | Bidirektionale Sync-, Reconcile- und Profilgrenzen geprüft |
| [F-03](library-v2-features.md#feat-quality) | App-weite Quality Profiles und Vererbung | Partial | §53/§60 | Track→Album→Artist→Global verified; Playlist-Quality-Profile und Konflikt-UI fehlen |
| [F-04](library-v2-features.md#feat-discography) | Discography, Tracklists, `monitor_new_items` | Verified | `2249f5d7`, `8f965d31` (später gesquasht) | Content-Filter und nie manuell expandierte Artists abgedeckt |
| [F-05](library-v2-features.md#feat-bootstrap) | Automatischer Initialimport | Verified | Review 4/5, `c2d99eda`, `e9730afe` | Bounded Transactions und Streaming; Owner-/Fresh-Install-Fixes im Regression-Checkpoint |
| [F-06](library-v2-features.md#feat-alias) | Artist Alias Registry und Scope | Verified | `ce7b4516`, `a95e5309` | Listen, Suche, Totals und artist-weite Actions gezielt geprüft |
| [F-07](library-v2-features.md#feat-duplicate) | Artist-/Album-/Edition-Dedup | Implemented | §62/§63, P3 | Code und gezielte Tests vorhanden; produktive Datenreparatur bleibt Backup/Dry-Run-abhängig |
| [F-08](library-v2-features.md#feat-unmapped) | V2-native/Collaboration Artists | Implemented | §68, Regression M-11 | Enrich/Smart-Split und globale Suche abgedeckt |
| [F-09](library-v2-features.md#feat-playlists) | Playlist-scoped Pipeline | Partial | LV2-015, Regression M-09 | Scope/Multi-Profil implemented; Quality-Konfliktmodell fehlt |
| [F-10](library-v2-features.md#feat-history) | Korrelierte Pipeline-History | Partial | §35/§37/§57/§58 | Feed, File-Ergebnis und Albumzweig vorhanden; vollständiger Track-Stepper/Eventvokabular nicht vollständig belegt |
| [F-11](library-v2-features.md#feat-playback) | Track Playback / Preview | Implemented | §36, Regression H-14 | Bestehender Player reused; typisierte ID-Korrektur im Regression-Checkpoint |
| [F-12](library-v2-features.md#feat-acq-review) | Acquisition Review / Bundle Assignment UI | Implemented | Regression-Checkpoint `ee30247a`, im aktuellen Squash `fb0096ce` | `import-review`-Route, Queue/Detail, Assignments und Resolve/Rescan/Resume vorhanden; vollständiger Browser-E2E fehlt |
| [F-13](library-v2-features.md#feat-search) | Scoped Search, Manual Grab, Acquisition | Implemented | §29/§53/§55/§60/§71 | Scoped/Transient Search, Force-Audit und gemeinsame Pipeline gezielt geprüft |
| [F-14](library-v2-features.md#feat-files) | Manage Files, Delete, Reorganize, Replacement | Implemented | §30/§54/§60, Review 1 | Gemeinsamer Delete-Vertrag, File-Scope und Pfadsync abgedeckt |
| [F-15](library-v2-features.md#feat-metadata) | Refresh, Retag, Metadata, RG/Lyrics | Verified | §28–§37, Review 9/10/17 | Review-spezifische Regressionen plus WebUI-Suite |
| [F-16](library-v2-features.md#feat-wanted) | Wanted Views, Entity Queue, Diskspace | Verified | §72–§74, `2e227c1b` | Entity Rollups und ein Queue-Poll pro Artist-Seite geprüft |

### UI-Status

| ID | Bereich | Status | Hinweis |
|---|---|---|---|
| [UI-01](library-v2-features.md#ui-icons) | Icons/Nomenklatur | Verified | Automatic=Lupe, Interactive=User, Quality=Stern, Track=Pencil |
| [UI-03](library-v2-features.md#ui-columns) | Table Options / Spalten | Implemented | Preferences/Sort/Provider vorhanden; Resize bewusst Deferred |
| [UI-04](library-v2-features.md#ui-bulk) | Multi-Select/Bulk Bar | Implemented | Monitor, Profil, RG, Tags, Delete und Rich Bulk Edit |
| F-12 UI | Acquisition Review | Implemented | Frontend-Consumer und Review-Oberfläche vorhanden; Browser-E2E bleibt Release-Gate |

---

## 3. Review-Findings vom 22. Juli

Alle 17 Findings wurden in eigenen Commits korrigiert. Die Issue-Datei
enthält die Diagnose; diese Tabelle enthält ausschließlich Remediationstatus.

| # | Finding | Status | Commit | Prüfung |
|---:|---|---|---|---|
| [1](library-v2-issues.md#find22-01) | Exaktes Reorganize-File | Verified | `4622f624` | spezifisch |
| [2](library-v2-issues.md#find22-02) | Import-Dispatch serialisieren | Verified | `d6d37eb2` | spezifisch |
| [3](library-v2-issues.md#find22-03) | Expiry-Delete mit V2 synchronisieren | Verified | `804538c7` | spezifisch |
| [4](library-v2-issues.md#find22-04) | Bootstrap bounded committen | Verified | `c2d99eda` | spezifisch |
| [5](library-v2-issues.md#find22-05) | Bootstrap-Rows streamen | Verified | `e9730afe` | spezifisch |
| [6](library-v2-issues.md#find22-06) | Artwork-Fetch härten | Verified | `80b5af95` | spezifisch |
| [7](library-v2-issues.md#find22-07) | Enrich Artist-Kontext | Verified | `280716d9` | spezifisch |
| [8](library-v2-issues.md#find22-08) | Artist-Rollups begrenzen | Verified | `6c827c33` | spezifisch |
| [9](library-v2-issues.md#find22-09) | Unicode Enrich | Verified | `abfa27a7` | spezifisch |
| [10](library-v2-issues.md#find22-10) | Enrich Metadata-Vertrag | Verified | `87b990bb` | spezifisch |
| [11](library-v2-issues.md#find22-11) | Outbox-Fehler propagieren | Verified | `088e1dc7` | spezifisch |
| [12](library-v2-issues.md#find22-12) | Alias-Suche/Totals | Verified | `ce7b4516` | spezifisch |
| [13](library-v2-issues.md#find22-13) | Alias-Aktionsscope | Verified | `a95e5309` | spezifisch |
| [14](library-v2-issues.md#find22-14) | Album-Credits rebuilden | Verified | `bdc478a5` | spezifisch |
| [15](library-v2-issues.md#find22-15) | Ein Queue-Poll pro Artist | Verified | `2e227c1b` | spezifisch |
| [16](library-v2-issues.md#find22-16) | Working Copy per Inhalt prüfen | Verified | `9592159f` | spezifisch |
| [17](library-v2-issues.md#find22-17) | Refresh & Scan als Job | Verified | `7ded959c` | spezifisch |

Verifikation dieses Review-Pakets:

- 396 finding-spezifische Backend-Regressionen bestanden;
- vollständige WebUI-Suite: 251 Tests in 42 Dateien bestanden;
- Ruff über alle geänderten Python-Dateien bestanden;
- `git diff --check origin/library-overhaul..HEAD` bestanden.

Zwei breitere Baseline-Fehler lagen in unveränderten Repair-Job-Testschemas;
die Acquisition-Gesamtsuite blockierte unter Python 3.14.6 in der unveränderten
Async-Bridge. Diese Einschränkungen verhindern, die Review-Prüfung als
vollständige Repository-Release-Zertifizierung darzustellen.

---

## 4. Regression-Audit vom 21. Juli

Die jüngste alte Regression-Doku enthält oben einen späteren
Implementierungs-Checkpoint, während die einzelnen Finding-Texte darunter
noch ihren ursprünglichen „OFFEN“-Stand bewahren. Für den Status gilt der
**neuere Checkpoint**, nicht die historischen Inline-Marker.

Die Remediation wurde vor dem späteren Branch-Squash aufgebaut; ihr
zusammengeführter Baum ist im Squash `fb0096ce` enthalten. Wo ein eigener
stabiler Commit bekannt ist, wird er zusätzlich genannt.

### Kritische und hohe Findings

| ID | Status | Referenz / Bemerkung |
|---|---|---|
| [C-01](library-v2-issues.md#c-01) | Implemented | Upstream-Verhalten `64736c1a` semantisch integriert |
| [H-01](library-v2-issues.md#h-01) | Implemented | Job-ID-/Settings-Migration im Regression-Checkpoint |
| [H-02](library-v2-issues.md#h-02) | Implemented | bestehende Automation bleibt Review |
| [H-03](library-v2-issues.md#h-03) | Implemented | Bootstrap Owner-Fencing |
| [H-04](library-v2-issues.md#h-04) | Implemented | Fresh-Install Watermark |
| [H-05](library-v2-issues.md#h-05) | Implemented | Admin-/Profilgrenze |
| [H-06](library-v2-issues.md#h-06) | Implemented | Composite-Identität |
| [H-07](library-v2-issues.md#h-07) | Implemented | Provider-qualifiziertes Artist-Matching |
| [H-08](library-v2-issues.md#h-08) | Implemented | Repair-Intent bleibt erhalten |
| [H-09](library-v2-issues.md#h-09) | Implemented | Syncfehler behält Retry-Anker |
| [H-10](library-v2-issues.md#h-10) | Implemented | vollständige Tracklist als Soll |
| [H-11](library-v2-issues.md#h-11) | Implemented | Legacy/V2 Compatibility-Write |
| [H-12](library-v2-issues.md#h-12) | Implemented | File-ID/Fingerprint-Dedup |
| [H-13](library-v2-issues.md#h-13) | Implemented | Pfadsync; spätere Review-Härtung `4622f624` |
| [H-14](library-v2-issues.md#h-14) | Implemented | typisierte Playback-IDs |
| [H-15](library-v2-issues.md#h-15) | Verified | später zusätzlich `a95e5309` |
| [H-16](library-v2-issues.md#h-16) | Implemented | ACL/Page-Migration |
| H-17 | Reclassified | jetzt Feature [F-12](library-v2-features.md#feat-acq-review), Implemented; Browser-E2E ausstehend |
| [H-18](library-v2-issues.md#h-18) | Implemented | zentraler nicht still abschaltbarer Cutover-Vertrag |

### Mittlere und niedrige Findings

| ID | Status | Bemerkung |
|---|---|---|
| [M-01](library-v2-issues.md#m-01) | Implemented | Legacy Source-Fallback |
| [M-02](library-v2-issues.md#m-02) | Implemented | zweiphasiger Album-Grab |
| [M-03](library-v2-issues.md#m-03) | Implemented | Candidate bleibt retrybar |
| [M-04](library-v2-issues.md#m-04) | Implemented | Disc-Nummer im Autolink |
| [M-05](library-v2-issues.md#m-05) | Implemented | Profilvererbung nach Delete |
| [M-06](library-v2-issues.md#m-06) | Implemented | Finding-Fingerprint |
| [M-07](library-v2-issues.md#m-07) | Implemented | Filesystem-Coverage für Fake-Lossless, Converter, Tracknummer, RG, Corruption; Cutoff absichtlich katalogabhängig |
| [M-08](library-v2-issues.md#m-08) | Implemented | Expired Cleaner und Reorganize als sichtbare Review/Apply-Jobs; alte IDs wieder verwendbar |
| [M-09](library-v2-issues.md#m-09) | Implemented | albumgenauer Playlist-Scope |
| [M-10](library-v2-issues.md#m-10) | Implemented | idempotenter Teilmigrations-Reconcile |
| [M-11](library-v2-issues.md#m-11) | Implemented | V2-Artists in globaler Suche |
| [M-12](library-v2-issues.md#m-12) | Implemented | UI Rollback/Retry |
| [M-13](library-v2-issues.md#m-13) | Implemented | zentraler Feature-Vertrag |
| [M-14](library-v2-issues.md#m-14) | Implemented | wahrheitsgemäßes Langläufer-Polling |
| [M-15](library-v2-issues.md#m-15) | Implemented | Safe Queue-ID-Parser |
| [L-01](library-v2-issues.md#l-01) | Verified | Config-Backup aus Handoff entfernt |
| [L-02](library-v2-issues.md#l-02) | Verified | MP3-Artefakt aus Handoff entfernt |

Checkpoint-Prüfung: 132 gezielte Python-Tests und 11 Frontendtests bestanden.
Zu diesem Zeitpunkt fehlten vollständige Backend-/Frontend-Suiten, realer
Client-E2E und produktionsnaher Migrations-/Restart-Soak. Die spätere Review-
Remediation ergänzt die in Abschnitt 3 genannten Prüfungen, ersetzt aber
keinen vollständigen externen E2E.

### Acquisition-Reuse-Audit

| ID | Status | Referenz |
|---|---|---|
| [LIB2-F01](library-v2-issues.md#lib2-f01) | Verified | gemeinsame Selection-/Source-Policy |
| [LIB2-F02](library-v2-issues.md#lib2-f02) | Verified | direkter Bundle-Write entfernt; Shared Pipeline Bridge |
| [LIB2-F03](library-v2-issues.md#lib2-f03) | Verified | gemeinsamer Profile-/Import-Gate-Vertrag |
| [LIB2-F04](library-v2-issues.md#lib2-f04) | Verified | persistenter Next-Candidate-/Source-Retry |
| [LIB2-F05](library-v2-issues.md#lib2-f05) | Implemented | ein Upgrade-Evaluator, Compatibility Wishlist Adapter |
| [LIB2-F06](library-v2-issues.md#lib2-f06) | Verified | Force/Quarantäne-Brücke `6ea7f3e2` |
| [LIB2-F07](library-v2-issues.md#lib2-f07) | Verified | Retry-Journal/Restart-Resume `e3eca302`, `899536db`, `364262bf` |
| [LIB2-F08](library-v2-issues.md#lib2-f08) | Verified | Paritätsvertrag `d921c1eb`; 8.081 Tests, 2 deselected im damaligen Full Run |

---

## 5. LV2-Bugcluster

| ID | Status | Referenz / verbleibende Betriebsaktion |
|---|---|---|
| [LV2-001](library-v2-issues.md#lv2-001) | Verified | transienter Track-Search, Failure requeue-t nicht |
| [LV2-002](library-v2-issues.md#lv2-002) | Verified | terminaler Status gewinnt gegen stale Context |
| [LV2-003](library-v2-issues.md#lv2-003) | Implemented | zentrale Runtime-Hooks |
| [LV2-004](library-v2-issues.md#lv2-004) | Verified | Post-Move-Recovery |
| [LV2-005](library-v2-issues.md#lv2-005) | Implemented | echter Restart-/Sidecar-E2E bleibt Release-Gate |
| [LV2-006](library-v2-issues.md#lv2-006) | Verified | evidenzbasierte Acquisition-Reconciliation |
| [LV2-007](library-v2-issues.md#lv2-007) | Verified | V2-only File im Orphan Detector |
| [LV2-008](library-v2-issues.md#lv2-008) | Verified | Verification-Sync |
| [LV2-009](library-v2-issues.md#lv2-009) | Verified | Recovery-Journal und Resume |
| [LV2-010](library-v2-issues.md#lv2-010) | Verified | `missing_suspected` UI/API |
| [LV2-011](library-v2-issues.md#lv2-011) | Verified | `w/` Parsing |
| [LV2-012](library-v2-issues.md#lv2-012) | Partial | Code verified; produktiver Merge/Datenrepair erfordert Backup und Dry Run |
| [LV2-013](library-v2-issues.md#lv2-013) | Verified | bewusst read-only Integritätsreport |
| [LV2-014](library-v2-issues.md#lv2-014) | Implemented | später über Regression M-11 geschlossen |
| [LV2-015](library-v2-issues.md#lv2-015) | Verified | Playlist-Scope fail closed |
| [LV2-016](library-v2-issues.md#lv2-016) | Verified | Default 0 plus Reconcile/Repair |
| [LV2-017](library-v2-issues.md#lv2-017) | Implemented | später über H-13 und Review 1 gehärtet; produktiver Backfill bleibt Dry-Run-abhängig |
| [Orphan Approve](library-v2-issues.md#orphan-bug) | Pending | Hypothese noch durch den beschriebenen Zwei-Pfad-Reproduktionstest zu bestätigen |

Historische Bugcluster-Prüfung:

- erster gezielter Lauf: 163 Backendtests;
- Monitoring/Playlist-Ergänzung: 1.453 Tests;
- breiter Library/Wishlist/Import/Acquisition-Lauf: 1.970 bestanden, 3
  übersprungen;
- Frontend Library-V2: 141 Tests in 24 Dateien;
- kein mutierender Lauf gegen die produktive DB.

---

## 6. Deep-Dive- und Branch-Review-Status

### Deep-Dive

| Gruppe | Status | Referenz |
|---|---|---|
| DD-A1/A2 — Cover Embed/Cache | Verified | §28 |
| DD-A3/A4 — scoped Search/serverseitiges Ranking | Verified | §29 |
| DD-A5 — BPM/Duration | Verified | §29 |
| DD-A6 — History Feed | Implemented | §35; vollständiger Track-Stepper bleibt F-10 Partial |
| DD-A7 — File Pipeline Result | Partial | §37; granularer gesamter Versuch bleibt F-10 Partial |
| DD-A8/A9 — Provider-Filter/Artist Picker | Verified | §29 |
| DD-G1–G6 | Verified | §28 |
| DD-G7 | Verified | §29 |
| DD-G8 | Verified | §30/§38 |
| UI B1–B7 | Implemented | §29–§31/§54 |
| D2 Provider-Modal-Merge | Deferred | kein notwendiger eigener Scope |
| Interactive-Search konfigurierbare Spalten | Deferred | Nutzen bei sieben Spalten zu klein |

### Historische Monolith-Diagnosen

| Diagnose | Status | Referenz |
|---|---|---|
| [Source Info ID-/Provenienzauflösung](library-v2-issues.md#hist-source-info) | Implemented | frühere §16.1-/§47-Korrektur |
| [Teil-Import monitort Parent](library-v2-issues.md#hist-partial-monitor) | Verified | frühere §16.2-/§22-Korrektur |
| [Tracknummer-Kollision/Healing](library-v2-issues.md#hist-track-number) | Verified | frühere §17.2/§19 |
| [Release-Date-Normalisierung](library-v2-issues.md#hist-date) | Implemented | frühere §17.3/§18.7 |
| [All-Releases-Initialload](library-v2-issues.md#hist-all-releases) | Verified | frühere §17.4/§21 |
| [Metadata-Status bei Missing](library-v2-issues.md#hist-metadata-missing) | Implemented | frühere §17.5/§18.8 |
| [Import-Performance/Precache](library-v2-issues.md#hist-import-performance) | Verified | frühere §17.6/§20/§66 |
| [Importer-Metadatenverlust](library-v2-issues.md#hist-import-data-loss) | Verified | frühere §17.7/§22/§23 |
| [Physischer Tag-/Coverstatus](library-v2-issues.md#hist-tag-status) | Implemented | früheres LV2-TAG-STATUS-01/02 |
| [Lyrics stale/path-mapped File](library-v2-issues.md#hist-lyrics-path) | Implemented | früheres LV2-LYRICS-01 plus H-13 |
| [Stale Dev-Bundle/Startpfad](library-v2-issues.md#hist-dev-environment) | Decision only | Diagnose-/Reproduktionsregel, kein Produktfix |

### Branch Review

| ID | Status | Commit/Notiz |
|---|---|---|
| [BR-01](library-v2-issues.md#br-01) | Implemented | Content-Filter `2249f5d7` (später gesquasht) |
| [BR-02](library-v2-issues.md#br-02) | Implemented | nie expandierte Artists `8f965d31` (später gesquasht) |
| [BR-03](library-v2-issues.md#br-03) | Implemented | Cover-/Retag-Serialisierung `fe6e3345` (später gesquasht) |
| [BR-04](library-v2-issues.md#br-04) | Implemented | Enrich-Matching-Härtung `f3af95aa`/Squash |
| [BR-05](library-v2-issues.md#br-05) | Implemented | kanonische Watchlist-Normalisierung |
| [BR-06](library-v2-issues.md#br-06) | Implemented | clientseitiger Best-Pick durch scoped Server-Search ersetzt |
| [BR-07](library-v2-issues.md#br-07) | Implemented | Component-Artist Default gehärtet |
| [BR-08](library-v2-issues.md#br-08) | Verified | Delta-Reconcile/No-op Guards plus Review-Finding 15 |
| [BR-09](library-v2-issues.md#br-09) | Partial | PRAGMA und erreichbarer IN-Crash gefixt; restliche SQL-Helper-Migration, Scope-Objekt und granularer Automation-Progress Deferred |

---

## 7. Tool-Migration und Cutover

Der P3-Stand stellte die Registry auf native V2-/Filesystem-Subjects um und
entfernte parallele Legacy-Entscheidungslogik. Der spätere Regression-Audit
hat aus Kompatibilitätsgründen zwei zuvor retirierte Nutzerverträge wieder
sichtbar gemacht: Expired Download Cleaner und Library Reorganize besitzen
wieder verwendbare IDs sowie Review/Apply-Pfade. Dieser neuere Stand ersetzt
die ältere reine Retirement-Tabelle.

| Bereich | Status |
|---|---|
| Native File-Subject-Coverage | Implemented |
| Quality Review/Automatic als ein Evaluator | Implemented |
| Native Discography/Wanted | Implemented |
| Monitoring List Reconcile | Implemented |
| Provider-qualifizierte Identitäten | Implemented |
| Automatischer Initialimport | Verified |
| Alte Job-ID-/Settings-Migration | Implemented im Regression-Checkpoint |
| Expired/Reorganize sichtbarer Kompatibilitätspfad | Implemented im Regression-Checkpoint |
| Physische Entfernung `legacy_artist_id`, `legacy_album_id`, `legacy_track_id` und Legacy-Importer | Deferred bis explizites Datenmigrations-/Rollback-Fenster |

Historischer P3-Verifikationsstand vor den späteren Regression-Fixes:

- 1.300 Backendtests über Library V2, Repair, Jobs und Automation;
- 237 Frontendtests;
- Frontend Check und Production Build;
- Registry-Audit ohne registrierte Legacy-/Mixed-Datenbasis.

---

## 8. Upstream-Integration und PR-Split-Handoff

### Semantisch integrierter Upstream-Rückstand

Der Regression-Checkpoint dokumentiert die folgenden nach der ursprünglichen
Branch-Divergenz entstandenen Verhaltensfixes als semantisch integriert. Diese
Tabelle bewahrt den früheren Handoff, ohne die Findings erneut in der
Issue-Datei zu duplizieren.

| Referenz | Verhalten | Status |
|---|---|---|
| `64736c1a` | Null-Header-/Preview-Schutz beim Replacement | Integrated |
| `fffdc4ea`, `d5c4d920` | Force Download ersetzt tatsächlich; eigener Replace-Batch-Key | Integrated |
| `da1d3293` | bestätigter Manual Import wird nicht vom automatischen Quality-Veto blockiert | Integrated |
| `cd2254bc` | Template-Änderungen führen zu realem Reorganize | Integrated |
| `3d809c64` | eigene Files nicht wegen Provider-Duration-Drift quarantänisieren | Integrated |
| `9ddcbd3f` | Downloads-Folder-Bleed, späte Cancel-Landings und falsches Stuck verhindern | Integrated |
| `decf8175` | Torrent-Save-Path anhand Inhalt statt bloßer Existenz verifizieren | Integrated |
| `0800fdbb` | Minimum-Free-Disk-Guard | Integrated |
| `b73bcc8e` | `.torrent` serverseitig laden; private Indexer-URL nicht an Browser geben | Integrated |
| `4344fbc9` | Preview Repair erkennt Null-Length-Header | Integrated |
| aktueller Artist-Image-Stack | ID-aware Artistbilder statt name-only Helper | Integrated |
| `6365b6b1` | `.lrc`-Sidecars mitbewegen | Integrated |
| `ebfd2883` | Multi-Artist-Singles unter Hauptartist ablegen | Integrated |
| `f73c915e` | exakte Albumidentifikation über IDs/ISRC-Konsens | Integrated |
| `73a6940a` | Multi-Disc-Kollision und editierbare Disc-Nummer | Integrated |
| `841c6c91` | Write Tags berührt nur betroffene Files | Integrated |
| `c767fc15` | Corrupt File Detector findet Files zuverlässig | Integrated |
| `eb958e10` | qBittorrent 5 stop/start | Integrated |
| `a9efaed3`, `d5efb299` | Torrent-Seeding-Lifecycle und Enforcement-Modus | Integrated |
| `7704bf32` | Playlist-Matches 0,70–0,79 zählen als matched | Integrated |
| `92c9ec26` | Rescue für stale Plex-`ratingKey` | Integrated |
| `f10ed9c7`, `6646861d` | Scheduled Watchlist umfasst Labels; Label-Count bricht Scan nicht ab | Integrated |

### Historisch als eigenständige Upstream-PRs identifizierte Änderungen

Diese Liste beschreibt die Review-/Split-Einschätzung vor dem großen Branch-
Squash. Sie ist ein Handoff, keine Behauptung, dass bereits ein separater PR
geöffnet oder gemergt wurde.

| Commit | Inhalt | Split-Einschätzung |
|---|---|---|
| `62a8848d` | Opaque Candidate Tokens für Torrent/Usenet-Links | sauber unabhängig; Security zuerst |
| `ba4e8569` | Bundle Completion erst nach stabilen Polls | unabhängig, Doku-Hunk trennen |
| `7bdd5fdc` | Python-3.14 Async-Bridge-Race | sauber unabhängig |
| `dbb3b84e` | Tracknummer-Fallback statt Kollaps auf Track 1 | sauber unabhängig; Datenverlustschutz |
| `d8f51a0f` | Tags für Simple Downloads | sauber unabhängig |
| `815253e8` | echte SABnzbd-Kategorieprüfung | sauber unabhängig |
| `76085876` | getrennte Retry-Budgets pro Release-Source | sauber unabhängig |
| `c9a7df90` Python-Hälfte | Retag Date/Genre False Positives | UI-Hunk trennen |
| `dcee311c` Backend-Hälfte | Automation Progress auf 0–100 begrenzen | V2-UI-Hunk trennen |
| `ec64f83c` | Quality-Profil-Löschung räumt Referenzen | erst zusammen mit M-05-Vererbungsfix extrahieren |

Nicht standalone: Schema ohne Importer/Queries, UI ohne API/Schema, Wanted
ohne Outbox/Reverse-Sync, Acquisition ohne Review-UI/Shared Pipeline sowie
Job-Retirements ohne Settings-Migration und Rolloutvertrag.

---

## 9. Aktuelle Release-Einschätzung

### Dokumentationsstand

Die vier Dokumente sind wieder nach Verantwortlichkeit getrennt:

- Guide: Zweck, Philosophie, ADRs und Invarianten;
- Features: gewünschtes Verhalten und Nutzerentscheidungen;
- Issues: Symptome, Root Causes und Korrekturverträge;
- Status: ausschließlich Fortschritt, Commits, Tests und Release-Gate.

### Technischer Gate-Stand

Die 17 Review-Findings sind gezielt verifiziert und die WebUI-Suite dieses
Pakets war vollständig grün. Trotzdem ist kein uneingeschränktes
Production-Release-Zertifikat dokumentiert, solange folgende Punkte fehlen
oder nicht erneut auf dem finalen Clean HEAD belegt sind:

- vollständige Python-Suite ohne Async-Bridge-Blockade;
- vollständiger kombinierter Frontend Check/Build auf finalem HEAD;
- realer Soulseek-/Torrent-/Usenet-E2E;
- Restart während Transfer, Quarantäne, Bundle-Review und Bootstrap;
- Migrations-/Soak-Test auf einer Kopie einer produktiven großen DB;
- Windows-/Docker-Path-Mapping und Root-Ausfall;
- produktiver LV2-012/LV2-017 Datenrepair ausschließlich nach Dry Run;
- F-12 Acquisition-Review-Browser-E2E mit mehrdeutigem Bundle und Restart;
- Bestätigung oder Widerlegung des Quarantäne-Approve-Orphan-Bugs.

**Einstufung:** Review-Remediation verifiziert; vollständiger Release-Gate
noch nicht belegt.

---

## 10. Fest entschiedene Nicht-Features

Diese Einträge sind nicht „offen“ und dürfen deshalb nicht in Issue- oder
Pending-Tabellen zurückwandern:

| Thema | Status |
|---|---|
| Calendar / Upcoming Releases | Decision only — abgelehnt |
| Artist Top Tracks | Decision only — abgelehnt |
| Add Artist parallel zu Search/Watchlist | Decision only — abgelehnt |
| Drittes Metadata Profile | Decision only — abgelehnt |
| Artist Mass Editor | Decision only — abgelehnt |
| A-Z-/Raw Inspector-/Non-admin Report UI | Decision only — abgelehnt |
| Separate Blocklist-/Unmapped-Files-UI | Decision only — abgelehnt |
| Search on Monitor | Decision only — abgelehnt |
| Discography Batch Download Modal | Decision only — abgelehnt |
| M3U/Roster Export | Deferred |
| Track Redownload Modal | Deferred |
| Reidentify / I Have This | Deferred |
| Resizable Columns | Deferred |
