# Library V2 — zentraler Status-, Commit- und Verifikations-Tracker

Diese Datei ist der **einzige** Ort für Fortschritt, „offen/erledigt“,
Commit-Referenzen, Teststände und Release-Einschätzung. Guide, Features und
Issues beschreiben ausschließlich Zweck, gewünschtes Verhalten und technische
Diagnosen.

Stand: 28. Juli 2026. Die sieben realen UI-Findings aus §39 öffneten Teile der
am 27. Juli als abgeschlossen geführten UI-03/UI-05/F-13/F-16-Arbeit wieder
und sind im aktuellen Arbeitsstand implementiert und regressionsgeprüft.
Zuvor: einschließlich Foundation-Rebase (§14), der an diesem
Tag umgesetzten Korrekturen §20–§24 (Pfad-Desync, Manual-Match-Timeout,
Orphan-Approve-Materialisierung, F-10-Korrelation, Artwork-Kaltstart-
Nachlieferung), §26 (nativer Subject-Row-Versatz in den Repair-Scannern,
Abbau der vorbestehenden Testfehler), §27 (erster Lauf gegen einen Snapshot
der Produktiv-DB, Album-Twin-Scan für jeden Artist, Frontend-Gate), §28
(Reconcile-Unmapped-Artists-Diagnose: Namens-only-Matching, fehlender
Cooldown — beide anschließend in §30 korrigiert und mit dem
Post-Import-Autotrigger verdrahtet), §29 (Werkzeug-↔-V2-Konvergenz: Legacy-Findings,
Cover-/Tag-Schreibpfad, Verification-Spalte — fünf Korrekturen, Genre-Lücke
als Produktentscheidung offen) sowie §34–§36 (Live-Feedback iss27-12/13/14,
Multi-Provider-Track-Reconcile, sofortige UI-Neuladung und Python-3.14-
Async-Deadlock) und §37 (Abschluss der offenen F-13/F-15/UI-03/UI-05-
Oberflächenpunkte plus zwei unabhängige Webclient-Fehler).
Playlist UI bleibt geparkt. Der werkzeugweise Integrations-Deep-Dive über alle
25 registrierten Repair-Jobs wurde in §30 abgeschlossen
([issues.md §19](library-v2-issues.md#19-ergebnis-des-werkzeugweisen-deep-dive-26-juli-2026-nacht)).

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
| [F-01](library-v2-features.md#feat-artwork) | Media-server-unabhängiges Artwork | Verified | Deep-Dive §28, Security-Fix `80b5af95`, §24 | Picker, Embed, Cache-Bust und Fetch-Hardening gezielt geprüft; Kaltstart-Nachlieferung seit §24 serverseitig getrieben |
| [F-02](library-v2-features.md#feat-monitoring) | Monitoring, Watchlist/Wishlist, Outbox | Verified | P3/§82, Regression-Checkpoint | Bidirektionale Sync-, Reconcile- und Profilgrenzen geprüft |
| [F-03](library-v2-features.md#feat-quality) | App-weite Quality Profiles und Vererbung | Implemented | §53/§60, §14, §15 | Track→Album→Artist→Global verified; Watchlist-Monitor-Mirroring inkl. `quality_profile_id`-Weitergabe an natives Watchlist jetzt verdrahtet (§15) |
| [F-04](library-v2-features.md#feat-discography) | Discography, Tracklists, `monitor_new_items` | Verified | `2249f5d7`, `8f965d31` (später gesquasht) | Content-Filter und nie manuell expandierte Artists abgedeckt |
| [F-05](library-v2-features.md#feat-bootstrap) | Automatischer Initialimport | Verified | Review 4/5, `c2d99eda`, `e9730afe` | Bounded Transactions und Streaming; Owner-/Fresh-Install-Fixes im Regression-Checkpoint |
| [F-06](library-v2-features.md#feat-alias) | Artist Alias Registry und Scope | Verified | `ce7b4516`, `a95e5309` | Listen, Suche, Totals und artist-weite Actions gezielt geprüft |
| [F-07](library-v2-features.md#feat-duplicate) | Artist-/Album-/Edition-Dedup | Implemented | §62/§63, P3, §27 | Album-Twin-Pass läuft seit §27 für jeden Artist, nicht nur für Merge-Survivor; Dry Run gegen die Produktiv-DB gelaufen. Rest: Track-Zeilen-Duplikate (§27 Teil 3) brauchen eine Produktentscheidung |
| [F-08](library-v2-features.md#feat-unmapped) | V2-native/Collaboration Artists | Implemented | §68, Regression M-11, §28 | Enrich/Smart-Split und globale Suche abgedeckt; Reconcile-Job bleibt namensbasiert ohne Strong-ID-Cross-Check und ohne Cooldown, siehe §28 |
| [F-09](library-v2-features.md#feat-playlists) | Library-v2-Playlist-Oberfläche | Deferred | `library-v2-playlist-ui` | Vollständig aus dem aktiven Overhaul entfernt und separat geparkt |
| [F-10](library-v2-features.md#feat-history) | Korrelierte Pipeline-History | Implemented | §35/§37/§57/§58, §17, §23 | Feed, File-Ergebnis und Albumzweig vorhanden; `previous_file_replaced` (§17) sowie `human_verified`/`rejected` über die neue `library_history`-Korrelation (§23) im Eventvokabular. Rest: kein Backfill für Altzeilen |
| [F-11](library-v2-features.md#feat-playback) | Track Playback / Preview | Implemented | §36, Regression H-14 | Bestehender Player reused; typisierte ID-Korrektur im Regression-Checkpoint |
| [F-12](library-v2-features.md#feat-acq-review) | Acquisition Review / Bundle Assignment UI | Removed / Deferred | §31, Entscheidung 27. Juli | `import-review`-Route und UI-Oberfläche per Nutzerentscheidung aus dieser PR entfernt |
| [F-13](library-v2-features.md#feat-search) | Scoped Search, Manual Grab, Acquisition | Verified | §29/§31/§33/§36/§37/§39, [iss28-03](library-v2-issues.md#iss28-03) | Suchablauf verified; globale Startseitenaktion heißt `Automatic Search` und teilt den neutralen Basisstil des Re-Import-Nachbarbuttons, ergänzt um das Automatic-Icon |
| [F-14](library-v2-features.md#feat-files) | Manage Files, Delete, Reorganize, Replacement | Implemented | §30/§54/§60, Review 1, §31 | Delete, File-Scope und Pfadsync abgedeckt; `Reorganize All` Ablauf bei Einstellungsänderung spezifiziert |
| [F-15](library-v2-features.md#feat-metadata) | Refresh, Retag, Metadata, RG/Lyrics | Verified | §28–§37, [iss27-02](library-v2-issues.md#iss27-02), [iss27-05](library-v2-issues.md#iss27-05), [iss27-07](library-v2-issues.md#iss27-07) | Multi-Provider-IDs, Post-Import-Erkennung, Verification-Read, Tag-Gap-Write, Tags-Breakdown und stabile Album/EP/Single-Gruppierung geprüft |
| [F-16](library-v2-features.md#feat-wanted) | Wanted Views, Entity Queue, Diskspace | Verified | §72–§74, `2e227c1b`, §39 | Rollups plus neutrales Größen-Badge mit Symbol in jeder eingeklappten Album-/EP-/Single-Zeile, einschließlich `0 B` |

### UI-Status

| ID | Bereich | Status | Hinweis |
|---|---|---|---|
| [UI-01](library-v2-features.md#ui-icons) | Icons/Nomenklatur | Verified | Automatic=Lupe, Interactive=User, Quality=Stern, Track=Pencil |
| [UI-03](library-v2-features.md#ui-columns) | Table Options / Spalten | Implemented | §39: separate generische Check-Spalte (Verified/Human verified/Skipped/Not scanned); normalisierte benachbarte Relativbreiten; feste zentrierte Quality-Unterbereiche; viewport-gebundener Einstellungsdialog; gezielte Tests und Build grün, manueller Browser-Gate mangels installiertem Chromium ausstehend |
| [UI-04](library-v2-features.md#ui-bulk) | Multi-Select/Bulk Bar | Implemented | Monitor, Profil, RG, Tags, Delete und Rich Bulk Edit |
| [UI-05](library-v2-features.md#ui-actions) | Actions, Nav & Maintenance | Verified | Navigation/Maintenance sowie die als `Automatic Search` benannte und an den Re-Import-Nachbarbutton angeglichene globale Startseitenaktion geprüft (§39) |
| F-12 UI | Acquisition Review | Removed | Per Nutzerentscheidung aus PR entfernt und gelöscht |


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
| [M-09](library-v2-issues.md#m-09) | Deferred | historische Playlist-Diagnose; Code liegt nicht im aktiven Overhaul |
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
| [LV2-012](library-v2-issues.md#lv2-012) | Partial | Code verified; Dry Run gegen einen Produktiv-Snapshot in §27 gelaufen (keine Merge-Kandidaten), schreibender Lauf weiterhin Backup-pflichtig |
| [LV2-013](library-v2-issues.md#lv2-013) | Verified | bewusst read-only Integritätsreport |
| [LV2-014](library-v2-issues.md#lv2-014) | Implemented | später über Regression M-11 geschlossen |
| [LV2-015](library-v2-issues.md#lv2-015) | Deferred | Historische Diagnose; aktive Library-v2-Playlist-Integration wurde geparkt |
| [LV2-016](library-v2-issues.md#lv2-016) | Verified | Default 0 plus Reconcile/Repair |
| [LV2-017](library-v2-issues.md#lv2-017) | Implemented | später über H-13 und Review 1 gehärtet; produktiver Backfill bleibt Dry-Run-abhängig |
| [Orphan Approve](library-v2-issues.md#orphan-bug) | Implemented | Root Cause bestätigt (§16), Korrektur nach §18-Entscheidung umgesetzt (§22) |

Historische Bugcluster-Prüfung:

- erster gezielter Lauf: 163 Backendtests;
- historischer Monitoring/Playlist-Lauf vor dem Branch-Split: 1.453 Tests;
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
| DD-A6 — History Feed | Implemented | §35, §17, §23; Eventvokabular jetzt vollständig |
| DD-A7 — File Pipeline Result | Implemented | §37; die fehlende Acquisition-Korrelation für `human_verified`/`rejected` ist in §23 nachgerüstet |
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
- ~~vollständiger kombinierter Frontend Check/Build auf finalem HEAD~~ —
  erledigt, §27 Teil 4 (Check Exit 0, 269 Tests, Production Build);
- realer Soulseek-/Torrent-/Usenet-E2E;
- Restart während Transfer, Quarantäne, Bundle-Review und Bootstrap;
- Migrations-/Soak-Test auf einer Kopie einer produktiven großen DB — die
  **Migration** ist in §27 Teil 1 auf einem Produktiv-Snapshot fehlerfrei
  gelaufen; der Soak-Test steht weiter aus;
- Windows-/Docker-Path-Mapping und Root-Ausfall;
- produktiver LV2-012/LV2-017 Datenrepair ausschließlich nach Dry Run — der
  Dry Run ist in §27 Teil 1 gelaufen (LV2-012: keine Merge-Kandidaten;
  LV2-017: kein Drift), der schreibende Lauf bleibt offen;
- F-12 Acquisition-Review-Browser-E2E mit mehrdeutigem Bundle und Restart;
- ~~Bestätigung oder Widerlegung des Quarantäne-Approve-Orphan-Bugs~~ —
  bestätigt (§16) und korrigiert (§22).

**Einstufung:** Review-Remediation verifiziert; vollständiger Release-Gate
noch nicht belegt. Neu offen aus dem Produktiv-Lauf: die Track-Zeilen-
Duplikate aus §27 Teil 3 (Produktentscheidung).

---

## 10. Performance-Findings vom 25. Juli

Nutzerbeobachtung: Artist-Liste/Artwork lädt in Library V2 spürbar langsamer
als in der Legacy-Library, auch bei warmem Artwork-Cache. Root-Cause-Diagnose
steht in [library-v2-issues.md §9](library-v2-issues.md#perf25-01); diese
Tabelle enthält ausschließlich den Bearbeitungsstatus.

| # | Finding | Status | Referenz / Bemerkung |
|---:|---|---|---|
| [1](library-v2-issues.md#perf25-01) | `os.stat()` pro Artist im List-Endpoint | Implemented | `1a6758b5` — Versionen aus einem Verzeichnis-Snapshot; jeder verwaltete Write/Delete verwirft ihn explizit |
| [2](library-v2-issues.md#perf25-02) | Kalte Artist-Artwork-Resolution synchron/sequenziell | Implemented | `78bf84c9` — Endpoint antwortet sofort mit Placeholder-Vertrag (404, `no-store`, `X-Artwork-Pending`) und baut im Hintergrund; UI retryt lokal dreimal mit Backoff. Bewusste Abweichung: der sequenzielle Provider-Fallback bleibt, weil Fan-out zusätzliche Provider-Calls kostet für Latenz, die nach der Entkopplung niemand mehr sieht |
| [3](library-v2-issues.md#perf25-03) | `list_artists`-CTEs berechnen live Aggregate, die Legacy nicht kennt | Implemented | `bca2ec04` — Size-Rollup nur bei eingeschalteter (opt-in, default aus) Spalte; Alias-Fold-CTE auf die angeforderte Seite begrenzt |
| [4](library-v2-issues.md#perf25-04) | Precache deckt nicht jeden ersten Seitenbesuch ab | Implemented | `a965e829` — Autolink und Discography-Expand stellen ihre neuen Entities direkt in den Hintergrund-Pool |
| [5](library-v2-issues.md#perf25-05) | Kein Virtualisierungsproblem; Pillow-Doppel-Encode im kalten Pfad, den Legacys eigenständiger Cache nicht macht | Implemented | 5a: Virtualisierung bestätigt unnötig, kein Code. 5b: `d51e85d8` — `optimize=True` nur noch auf dem Listen-Thumbnail |

Verifikation: `tests/library2` + `tests/search` 1.136 bestanden (2 Fehler
vorbestehend in `test_maintenance_sync.py`, auch auf unverändertem Baum);
vollständige WebUI-Suite 252 Tests in 43 Dateien; `oxlint --type-check`,
Production Build und Ruff über alle geänderten Dateien bestanden.

**Einstufung:** Alle fünf Findings implementiert und gezielt geprüft; ein
Messvergleich gegen die produktive große DB steht noch aus. Der Branch-Review
vom 25. Juli hat auf genau diesen Commits dreizehn Nacharbeiten gefunden
(§13) — darunter zwei, die den Perf-Gewinn auf großen Bibliotheken umkehren
(Findings 3, 4), und eine, die ein Cover dauerhaft als Placeholder festnagelt
(Finding 1). Die Findings gelten deshalb als implementiert, aber **nicht** als
abgenommen.

---

## 11. Search-Ergebnis „In Your Library" verlinkt auf alte Library statt Library V2

Nutzerbeobachtung: Klick auf einen bereits vorhandenen Artist im
Search-Ergebnis führt zur alten Library-Detailseite. Root-Cause-Diagnose
steht in [library-v2-issues.md §10](library-v2-issues.md#find25-search-01);
diese Tabelle enthält ausschließlich den Bearbeitungsstatus.

| # | Finding | Status | Referenz / Bemerkung |
|---:|---|---|---|
| [1](library-v2-issues.md#find25-search-01) | Frontend-Link-Logik ist bereits korrekt | No fix needed | Fällt nur zurück, wenn Backend keine `library_v2_id` liefert |
| [2](library-v2-issues.md#find25-search-02) | Orchestrator-Merge verknüpft Legacy- und lib2-Artist nicht zuverlässig | Implemented | `d82ad12b` — eindeutiger Namensmatch als dritte, letzte Verknüpfung plus einmaliger `legacy_artist_id`-Backfill; beidseitig gegen Mehrdeutigkeit abgesichert |

Verifikation: zwei neue Regressionstests (fehlende Verknüpfung wird
repariert; mehrdeutige Namen bleiben bewusst unverknüpft), `tests/search`
vollständig grün.

**Einstufung:** Fix implementiert und gezielt geprüft; produktive Bestätigung
am realen Suchergebnis steht noch aus. Der Branch-Review vom 25. Juli hat zwei
Nacharbeiten an genau diesem Commit gefunden (§13, Findings 5 und 7): die
Eindeutigkeitsprüfung vor dem persistierten Backfill läuft über abgeschnittenen
Ergebnisfenstern, und der Backfill macht den Such-Lesepfad zum Writer. Der Fix
gilt deshalb als implementiert, aber **nicht** als abgenommen.

---

## 12. Fest entschiedene Nicht-Features

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

---

## 13. Branch-Review-Findings vom 25. Juli

Review des Branch-Diffs `library-overhaul` gegen `main` über genau die Commits
aus §10 und §11. Root-Cause-Diagnosen stehen in
[library-v2-issues.md §12](library-v2-issues.md#rev25-01); diese Tabelle
enthält ausschließlich den Bearbeitungsstatus. Dreizehn der fünfzehn Findings
sind am 25. Juli im selben Aufwasch behoben worden; die zwei verbleibenden
(2, 10) brauchten zuerst die in
[features F-01](library-v2-features.md#feat-artwork) skizzierte
Produktentscheidung zum Kaltstart-Vertrag. Diese ist am 26. Juli gefallen
(§18): Finding 2 ist seitdem umgesetzt (§24), Finding 10 bleibt bewusst
zurückgestellt.

| # | Finding | Betroffener Commit | Status | Bemerkung |
|---:|---|---|---|---|
| [1](library-v2-issues.md#rev25-01) | `_background_inflight` leakt beim Verbindungsfehler, Entity bleibt dauerhaft Placeholder | `78bf84c9` | Fixed | Ein `finally` um den gesamten `_run`-Körper inkl. Verbindungsaufbau; Regressionstest mit fehlschlagendem `_get_connection` |
| [2](library-v2-issues.md#rev25-02) | Kaltes Cover kann dauerhaft Placeholder bleiben: 14,5 s Retry-Budget < kalter Build, kein Refetch, `X-Artwork-Pending` ohne Konsument | `78bf84c9` | Fixed | Serverseitig getriebenes Polling ersetzt das feste Retry-Budget, siehe §24 |
| [3](library-v2-issues.md#rev25-03) | Verzeichnis-Snapshot kostet auf großen Bibliotheken mehr Syscalls als die 75 `stat()`, die er ersetzt | `1a6758b5` | Fixed | Whole-Directory-Snapshot ersetzt durch Per-Entity-Mtime-Cache mit Generation-Marker (löst auch Finding 9) |
| [4](library-v2-issues.md#rev25-04) | Voller Artwork-Verzeichnis-Scan auf dem Per-Download-Importpfad | `a965e829` | Fixed | `schedule_missing_artwork` prüft nur noch die eigenen Targets über `artwork_version`, kein Verzeichnis-Scan mehr |
| [5](library-v2-issues.md#rev25-05) | Namens-Backfill persistiert Identität aus Eindeutigkeitsprüfung über `LIMIT 5`/`LIMIT 10` | `d82ad12b` | Fixed | Reconcile prüft Eindeutigkeit ohne `LIMIT` gegen die volle Tabelle, bevor geschrieben wird |
| [6](library-v2-issues.md#rev25-06) | Eingeschaltete Size-Spalte zeigt „—" für jeden Artist | `bca2ec04` | Fixed | Behoben durch Finding 11 (expliziter Parameter statt Preference-Ableitung) |
| [7](library-v2-issues.md#rev25-07) | Such-Lesepfad schreibt und committet | `d82ad12b` | Fixed | Backfill läuft jetzt off-thread mit eigener Verbindung (gleiches Dispatch-Muster wie der MB-Release-Group-Reconcile, §62.6 Stufe 3); die Suche selbst bleibt lesend |
| [8](library-v2-issues.md#rev25-08) | Modulglobaler Executor: eingefrorene Worker-Zahl, kein Shutdown, unbegrenzte Queue | `78bf84c9` | Fixed | Worker-Zahl wird beim nächsten Leerlauf neu gelesen, `shutdown_background_executor()` in `web_server.py`s Shutdown-Pfad verdrahtet, Queue bei 500 gedeckelt |
| [9](library-v2-issues.md#rev25-09) | `forget_artwork_versions` durch parallelen Scan still rücknehmbar | `1a6758b5` | Fixed | Generation-Marker pro Entity statt Directory-Mtime-Vergleich; ein Write kann von einem racenden Read nicht mehr überschrieben werden |
| [10](library-v2-issues.md#rev25-10) | Kein Negativ-Cache; Retries vervierfachen die Last für bildlose Entities | `78bf84c9` | **Open** | Hängt an derselben Kaltstart-Vertrags-Entscheidung wie Finding 2 |
| [11](library-v2-issues.md#rev25-11) | Altitude: UI-Preference entscheidet die Payload der gesamten Artist-Response | `bca2ec04` | Fixed | Expliziter `?include=size`-Parameter, gesetzt von der Tabellen-Ansicht; Query-Key hängt jetzt vom Parameter ab |
| [12](library-v2-issues.md#rev25-12) | `src`-Wechsel committet einen Frame mit altem Retry-Zähler | `78bf84c9` | Fixed | Retry-State wird während des Renders auf `base` synchronisiert (React-Pattern, kein Effect-Delay mehr); leeres `base` bleibt falsy |
| [13](library-v2-issues.md#rev25-13) | Weggefallenes `optimize=True` trifft auch die Detailseiten-Variante | `d51e85d8` | Fixed | `optimize=True` für die Vollvariante wiederhergestellt — einmaliger Build-Zeit-Kosten, dauerhafter Bytegewinn auf jeder Detailseiten-Auslieferung |
| [14](library-v2-issues.md#rev25-14) | Zwei Implementierungen von „ist dieses Artwork gecacht?" | `a965e829` | Fixed | `_cached_artwork_filenames` ist die einzige verbleibende Directory-Scan-Implementierung, nur noch von `precache_all_artwork` genutzt |
| [15](library-v2-issues.md#rev25-15) | Globaler PIL-Patch im Formattest; Verbindungsfehlerpfad ungetestet | `d51e85d8`/`78bf84c9` | Fixed | Test nutzt jetzt die `monkeypatch`-Fixture; Verbindungsfehlerpfad hat einen eigenen Regressionstest (siehe Finding 1) |

Verifikation des Reviews selbst: Findings 1, 5, 6, 7 und 12 wurden zusätzlich
direkt am Code nachgeprüft; die übrigen zehn waren Review-Aussagen ohne eigene
Reproduktion — bei der Umsetzung von 3/4/8/9/14 hat das TDD-Vorgehen einen
zusätzlichen Bug im ersten Entwurf des Generation-Markers gefangen (ein
einzelner globaler statt ein Per-Entity-Zähler hätte jede Invalidierung einer
Entity die Caches aller anderen mit-invalidiert).

**Einstufung:** §10 und §11 bleiben implementiert; 13 von 15 Nacharbeiten aus
dieser Liste sind jetzt ebenfalls umgesetzt und mit gezielten Tests
abgesichert (`tests/library2/test_artwork_*`, `tests/search/test_search_orchestrator.py`,
`tests/library2/test_api_routes.py`, `webui/.../artwork-retry.test.tsx`).
Offen blieben zunächst Finding 2 und 10 — beide warteten auf die
Kaltstart-Vertrags-Entscheidung aus [features F-01](library-v2-features.md#feat-artwork).
Die Entscheidung fiel am 26. Juli (§18): Finding 2 (Nachlieferung) ist
umgesetzt (§24), Finding 10 (Negativ-Cache) bleibt bewusst zurückgestellt und
ist damit der einzige offene Punkt dieser Liste.

---

## 14. Rebase auf den Foundation-Merge (26. Juli)

`library-overhaul` war am 22. Juli in drei unabhängig reviewbare Produkte
gesplittet worden: `quality-profiles-foundation` (natives Watchlist/
Mirrored-Playlist Quality-Profile-Persistenz), `library-overhaul` selbst
(Library-v2-Katalog/Acquisition, dieser Branch) und `library-v2-playlist-ui`
(geparkte Playlist-UI, siehe [F-09](#2-feature-status)). Die
Vor-Split-Sicherung liegt auf Branch/Tag
`backup-library-overhaul-pre-foundation-split-20260722`.

`library-overhaul` wurde anschließend gemäß dem vereinbarten Ablauf (Foundation
zuerst nach `dev` mergen, dann `library-overhaul` darauf rebasen; Konflikte
nach Ownership statt "wer ist neuer" auflösen: Foundation gewinnt natives
Watchlist/Wishlist/Mirror/Sync/Automation, library-overhaul gewinnt
Library-v2-Katalog/Acquisition) auf den aktualisierten `dev`-Branch rebased,
nachdem PR #1076 (`quality-profiles-foundation`) sowie die Misc-Fixes-PR
upstream gemerged wurden. Alle 50 eigenen Commits wurden einzeln neu
appliziert. Die Vor-Rebase-Sicherung liegt auf Branch/Tag
`backup-library-overhaul-pre-dev-rebase-20260725` (lokal und auf `origin`
gepusht).

### Konfliktauflösung — wesentliche Entscheidungen

| Bereich | Entscheidung | Begründung |
|---|---|---|
| `database/music_database.py::add_to_wishlist_detailed` Dedup-Key | library-overhauls composite-first-Algorithmus (P1-09) behalten, nicht Foundations bare-first-Variante | library-overhauls eigener, mit 9 Tests abgesicherter Audit-Fix (`tests/wishlist/test_wishlist_idempotency.py`); Foundations abweichende Erwartungen in `tests/quality/test_wishlist_add_outcome.py` angepasst |
| `set_mirrored_playlist_quality_profile` / mirrored-playlist-Schema | Foundations native Version übernommen; library-overhauls gekoppelten Playlist-Quality-Prototyp über `4f3952ae`+`35ec7dca` sauber entfernt | Split-Doc: Foundation besitzt `mirrored_playlists.quality_profile_id` |
| `_pipeline_shared.py` Wishlist-Trigger | Foundations `apply_backoff`-Parameter MIT library-overhauls `track_ids`/`profile_ids`-Scoping kombiniert | beide Features sind orthogonal (Backoff-Gate vs. Playlist-Scope), keine Konkurrenz |
| `core/repair_jobs/replaygain_filler.py` | Foundations Rescan-Feature (#1060) MIT library-overhauls Subject-Aware-Details (`entity_type='file'` für native Files ohne lib2-Eintrag) kombiniert | orthogonal |
| `core/repair_worker.py::_fix_handlers` | additive Vereinigung beider Job-Listen; `duplicate_tracks`/`_fix_duplicates` bewusst NICHT wiederhergestellt | `duplicate_detector` steht in `RETIRED_JOB_IDS` ohne Preserved-Finding-Pfad — bereits bewusste P2-Konsolidierung, nicht rückgängig gemacht |

### Während der Rekonziliation entdeckte und behobene Bugs

- `add_artist_to_watchlist`/`remove_artist_from_watchlist` in
  `database/music_database.py` hatten nach dem Merge kein
  `raise_on_error`-Signaturparameter mehr, obwohl ihr Exception-Handler
  bereits `if raise_on_error: raise` enthielt UND
  `core/library2/mirror_outbox.py`s `_execute_op` sie mit
  `raise_on_error=True` aufruft — ein reines Merge-Artefakt (Foundations
  schlankere Signatur + library-overhauls Body). Ohne den Fix hätte ein
  fehlgeschlagener Watchlist-Mirror-Vorgang aus dem Library-v2
  Artist-Monitoring-Outbox-Pfad nie einen Retry ausgelöst — ein potenziell
  stiller Reliability-Bug.
- `database.add_to_wishlist(..., raise_on_error=True)` (Bool-Wrapper) hat den
  Parameter nie an `add_to_wishlist_detailed` durchgereicht — ebenfalls inert
  für den Mirror-Outbox-`wishlist_add`-Pfad; wirft jetzt bei `status ==
  "error"`.
- `core/wishlist/service.py::add_track_to_wishlist`/`add_spotify_track_to_wishlist`
  hatten `quality_profile_id` verloren, obwohl `core/wishlist/routes.py`
  (Library-Album-Modal "Add to Wishlist") bzw. der Cancel/Retry-Pfad in
  `web_server.py` (P2-06) es weiterhin übergeben — beide wiederhergestellt.
- `core/repair_jobs/__init__.py`: Foundations `genre_cleanup`/
  `comma_artist_splitter`-Jobs kannten library-overhauls P3-Governance
  (`JOB_DATA_BASIS`/`JOB_LIBRARY_V2_EFFECTS`) noch nicht — Deklarationen
  ergänzt (`lib2` / `{'observe','metadata'}` bzw. `{'observe','tags'}`).
- `tests/wishlist/test_routes.py`: Ein Test-Helper reassignte
  `routes_module.get_wishlist_service` direkt statt über `monkeypatch` —
  leakte über Testdatei-Grenzen hinweg und ließ Foundations neuen
  `tests/acquisition/test_quality_profile_contract.py` nur in Kombination mit
  vorher laufenden Wishlist-Tests fehlschlagen. Autouse-Fixture zur
  Wiederherstellung ergänzt — eine vorbestehende Test-Hygiene-Lücke, durch
  Foundations neuen Test erstmals sichtbar geworden.

### Verifikation

Alle 50 Commits erfolgreich rebased; kein Silent-Drop (Funktionsnamen-Diff
zwischen dem ursprünglichen `library-overhaul` und dem Reko-Ergebnis über den
gesamten geänderten Dateibestand geprüft). Gezielte Backend-Suite
(Quality/Wishlist/Library2/Watchlist/Imports/Repair/RepairJobs/Downloads/
Acquisition/Automation + betroffene Einzeldateien): 3940 passed, 2
pre-existing failed (siehe unten), 3 skipped. Frontend Library-v2-Suite:
154/154 passed.

### Offen — nicht Teil dieser Rekonziliation

- **Pre-existing** (bereits auf dem unrebased `library-overhaul` fehlschlagend,
  nicht durch die Reko verursacht — per Vergleich in einem separaten
  Worktree verifiziert):
  `tests/library2/test_maintenance_sync.py::test_cover_art_scanner_flags_v2_only_album`
  und `::test_metadata_gap_scanner_covers_v2_only_track` scheitern an
  `sqlite3.OperationalError: no such column` (`al.spotify_album_id` bzw.
  `t.isrc`) — vermutlich Schema-/Query-Drift in
  `missing_cover_art.py`/`metadata_gap_filler.py`. Root Cause noch nicht
  untersucht.
  ~~**Nachtrag 26. Juli 2026:** [...] die beiden Tests scheitern also
  wahrscheinlich an einer Test-Fixture ohne vollständige Migrationskette, nicht
  an einem Produktivschema-Fehler.~~ **Widerlegt, 26. Juli 2026:** Die
  fehlenden Spalten waren nur der Auslöser. Dahinter lag ein echter
  Produktfehler in beiden Scannern — die nativen Subject-Zeilen sind gegen die
  Legacy-`SELECT`-Breite verschoben und lassen jeden Scan mit `IndexError`
  abbrechen, **auch auf einer vollständig migrierten DB**. Diagnose in
  [issues.md §14](library-v2-issues.md#nativepad25-01), Umsetzung in §26.
- Ebenfalls bereits vorher fehlschlagend (8 Tests in
  `tests/test_repair_worker_album_fill.py`,
  `tests/test_repair_worker_unknown_artist_path.py`,
  `tests/test_repair_worker_duplicate_delete.py`): testen
  `_fix_unknown_artist`/`_fix_duplicates`/`_perform_album_fill`, die als Teil
  der P1/P2-Tool-Migration bereits entfernt wurden, ohne dass die
  zugehörigen Alt-Tests entfernt/migriert wurden. **Abgebaut in §26.**
- **Thin-Adapter (Artist-Monitoring → natives Watchlist)** war zur Hälfte
  verdrahtet: Monitor-An/Aus mirrorte korrekt, aber die geforderte
  `quality_profile_id`-Weitergabe fehlte. Bewusst nicht in dieser Reko
  nachgezogen (Nutzerentscheidung 26. Juli); geschlossen in §15.

---

## 15. Thin-Adapter `quality_profile_id`-Weitergabe (26. Juli)

Schließt die in §14 offen gelassene Lücke. `core/library2/mirror_outbox.py::
enqueue_artist_watchlist` liest jetzt beim Einschalten des Monitorings
(`monitored=True`) das effektive Katalog-Quality-Profile des Artists über
`core/library2/profile_lookup.py::effective_quality_profile` (dieselbe
Track→Album→Artist→Global-Kaskade, die auch der Artist-Settings-Picker zeigt)
und legt es dem Outbox-Payload als `quality_profile_id` bei. `_execute_op`
reicht den Wert an `database.add_artist_to_watchlist(...,
quality_profile_id=...)` durch — die bereits vorhandene, aber bis dahin nie
von Library v2 aufgerufene Foundation-Methode für genau diesen Zweck.

**Bewusste Grenze:** Dies ist ein einmaliger Push zum Zeitpunkt des
Monitor-Einschaltens, keine dauerhafte Kopplung. Eine spätere Änderung des
Katalog-Artist-Profils propagiert nicht automatisch auf einen bereits
monitorten Artist zurück — das entspricht Guide §2.3 ("Watchlist-Artist- und
native Playlist-Zuweisungen [...] werden nicht als versteckte zusätzliche
Ebene in diese Library-v2-Katalogkaskade eingebaut") und vermeidet eine
überraschende Live-Rückkopplung. Ein Nutzer, der die Watchlist-Zuweisung
danach ändern will, tut das wie gehabt über die native Watchlist-Oberfläche
oder erneutes Aus-/Einschalten des Monitorings.

Albums/EPs/Singles-Flags und die übrigen Watchlist-Content-Filter brauchten
keine Änderung: Die native Watchlist selbst kennt sie nur am
Update-Endpunkt, nicht am Add (`api/watchlist.py::add_to_watchlist` nimmt nur
`artist_id`/`artist_name`/`source`/`quality_profile_id` entgegen), und
Library v2 deckt den Update-Fall bereits korrekt über
`core/library2/artist_settings.py` (`ArtistSettingsModal` →
`PUT /api/library/v2/artists/<id>/settings`) ab, sobald die Watchlist-Row
existiert.

Verifikation: neue/erweiterte Tests in `tests/library2/test_mirror_outbox.py`
(explizites Artist-Profil wird gepusht; Remove-Op bleibt profilfrei),
`tests/library2/test_api_routes.py` (Route-Ebene: mit und ohne explizitem
Katalog-Override), plus Anpassung der betroffenen Fake-DB-Signaturen in
`test_monitor_sync.py`/`test_scoped_search_endpoint.py`/
`test_wishlist_mirror.py`. Gezielter Lauf `tests/library2 tests/watchlist
tests/wishlist tests/quality`: 1490 passed, 2 vorbestehend fehlschlagend
(dieselben Schema-Drift-Fälle aus §14, unverändert).

**Einstufung:** Implementiert und gezielt geprüft; kein Browser-E2E gegen
eine echte Watchlist-UI.

---

## 16. Orphan-Approve Root Cause bestätigt, Korrektur offen (26. Juli)

Die in [library-v2-issues.md §7](library-v2-issues.md#orphan-bug)
beschriebene Arbeitshypothese ist jetzt durch einen deterministischen Test
bewiesen: `tests/library2/test_autolink.py::
test_simple_download_never_gets_a_file_row` (grün — pinnt den bestätigten
Fehler, kein Regressions-Fix in dieser Session).

**Wichtiger Scope-Fund:** Der Fehler ist **nicht quarantäne-spezifisch**.
Jeder erfolgreiche Simple Download (`is_simple_download=True`, kein
Titel/Artist-Match) überspringt `link_download_into_library_v2` strukturell
und bekommt nie eine `lib2_track_files`-Row — Quarantäne-Approve reproduziert
das nur, weil er denselben lückenhaften Context originalgetreu zurückspielt.
Die Sidecar-Serialisierung selbst ist nicht die Ursache (bereits vorher
empirisch ausgeschlossen).

Die Korrektur selbst ist bewusst **nicht** in dieser Session implementiert:
sie braucht eine Produktentscheidung zwischen "Simple Downloads ohne Match in
lib2 materialisieren" und "Orphan Detector um Legacy-Provenance-Erkennung
härten" (Details in der Issue-Datei). Ein roter/beweisender Test allein
autorisiert laut Guide-Arbeitsregel 3 noch keine Korrektur ohne diese
Entscheidung.

**Einstufung:** Root Cause bestätigt und gezielt geprüft; Produktentscheidung
getroffen (§18) und am selben Tag umgesetzt — siehe §22. Der beweisende Test
wurde dabei durch seinen Positiv-Nachfolger ersetzt.

---

## 17. F-10 Eventvokabular — `previous_file_replaced` ergänzt (26. Juli)

Deep-Dive in den Track-Stepper-Rückstand aus DD-A6/DD-A7: Von den in
[features F-10](library-v2-features.md#feat-history) verlangten Schritten
fehlten `human_verified`, `rejected` und `previous_file_replaced` im
`acquisition_history`-Eventvokabular (`core/acquisition/history.py::
EVENT_TYPES`). Alle drei sind jetzt einzeln untersucht statt pauschal
"fehlt":

**`previous_file_replaced` — implementiert.** Alle drei Replace-Zweige in
`core/imports/pipeline.py::post_process_matched_download` (Quality-Replace,
Enhance/Force, metadatenloses Overwrite) markieren jetzt einen
`_replace_reason`; nach erfolgreichem `safe_move_file` journalt
`_journal_previous_file_replaced` → `core/acquisition/pipeline_callback.py::
notify_previous_file_replaced` das Ereignis über dieselbe
`_pipeline_correlation`-Fail-open-Brücke wie `quality_checked`/
`acoustic_id_checked` — ordinäre (nicht Acquisition-getrackte) Importe bleiben
ein Zero-Write-No-op. `core/library2/history_feed.py::EVENT_CATEGORY` zeigt es
als `("imported", "Previous file replaced")`; `recovered_to_staging` bekam
dieselbe fehlende Label-Zuordnung nachgetragen.

**`human_verified`/`rejected` — bewusst NICHT implementiert, geänderte
Einschätzung.** Der ursprüngliche Scope-Vorschlag ("zwei `record_history_event`
Calls in den bestehenden Verification-Routen") ist bei genauerer Prüfung
nicht ausführbar: `record_history_event` verlangt zwingend eine
`request_id`/`candidate_id`/`download_id`-Korrelation, und diese Korrelation
existiert für `/api/verification/<id>/approve` und `.../delete`
(`web_server.py`) nicht — beide operieren nur auf einer
`library_history.id`, die **keine** persistierte Verbindung zurück zur
Acquisition-Seite trägt (`core/acquisition/*.py` referenziert
`library_history_id` an keiner Stelle; die einzige Verknüpfung ist der
transiente In-Memory-`context["_history_id"]` aus demselben Pipeline-Lauf,
der zum späteren Approve-Zeitpunkt längst weg ist). `lib2_entity_history` ist
per CHECK-Constraint auf Merge-/Move-Events geschlossen und passt semantisch
nicht. Eine echte Korrektur bräuchte eine neue persistente Korrelationsspalte
auf `library_history` (Schema- plus Write-Site-Änderung beim Import) — kein
Nachmittags-Task mehr, sondern ein eigener, separat zu planender Schnitt.

Verifikation: `tests/acquisition/test_pipeline_callback.py` (2 neue Tests:
Korrelation erhalten, No-op ohne Marker), `tests/library2/test_history_feed.py`
(1 neuer Test: Feed-Darstellung), `tests/imports/test_import_pipeline.py`
unverändert grün (kein Regressionsrisiko an den drei Replace-Zweigen). Gezielter
Lauf `tests/acquisition tests/imports tests/library2`: siehe Testlauf-Ergebnis
dieser Session.

**Einstufung:** `previous_file_replaced` implementiert und gezielt geprüft.
Die für `human_verified`/`rejected` verlangte Schema-Entscheidung ist in §18
gefallen und in §23 umgesetzt; F-10 ist damit nicht mehr wegen fehlender
Korrelation Partial.

---

## 18. Produktentscheidungen vom 26. Juli 2026

Drei in §13, §16 und §17 offen gelassene Produktentscheidungen sind getroffen.
Diese Tabelle hält ausschließlich fest, dass entschieden wurde und wohin die
jeweilige Entscheidung dokumentiert ist; die fachliche Begründung steht bei
Features/Issues, nicht hier.

| Thema | Entscheidung | Referenz |
|---|---|---|
| Orphan-Approve (§16) | Option 1, Materialisieren: Simple Downloads ohne Titel/Artist-Match bekommen künftig eine Fallback-Entity in lib2 | [issues.md §7](library-v2-issues.md#orphan-bug) |
| Artwork-Kaltstart, Nachlieferung (§13 Finding 2) | Wird umgesetzt; genauer Mechanismus (Polling/Header/Refetch) ist Implementierungsdetail | [features.md F-01](library-v2-features.md#feat-artwork) |
| Artwork-Kaltstart, Negativ-Cache (§13 Finding 10) | Bleibt zurückgestellt, kein Teil dieser Entscheidung | [issues.md rev25-10](library-v2-issues.md#rev25-10) |
| F-10 `human_verified`/`rejected` (§17) | Wird umgesetzt: neue persistente Korrelationsspalte auf `library_history` (`request_id`/`candidate_id`/`download_id`) über dieselbe Fail-open-Bridge wie `previous_file_replaced` | [features.md F-10](library-v2-features.md#feat-history) |

**Einstufung:** Alle drei Korrekturen sind priorisiert, freigegeben und
inzwischen umgesetzt: Orphan-Approve in §22, Artwork-Nachlieferung in §24,
F-10-Korrelation in §23. Der Negativ-Cache (§13 Finding 10) bleibt wie
entschieden zurückgestellt.

---

## 19. Nutzer-Bugreport vom 26. Juli 2026

Diagnose in [issues.md §13](library-v2-issues.md#13-nutzer-bugreport-vom-26-juli-2026).
Diese Tabelle enthält ausschließlich den Bearbeitungsstatus.

| # | Finding | Status | Referenz |
|---:|---|---|---|
| 1 | Metadaten-Scan bleibt „pending" für vorhandene Songs — derselbe Pfad-Desync-Mechanismus wie [LV2-017](library-v2-issues.md#lv2-017), zusätzlich Risiko einer Fehlklassifikation als `missing_confirmed` | Implemented | §20 |
| 2 | Manual Match (Artist) läuft durch synchrone Artwork-Nachladung nach Match-Commit in den 10s-Client-Timeout | Implemented | §21 |

**Einstufung:** Beide Root Causes waren bestätigt (Finding 1 durch Codepfad-
Analyse von `rescan_files`/`resolve_lib2_path`/`metadata_scan_status`,
Finding 2 zusätzlich durch den Default-Timeout in `webui/src/app/api-client.ts`);
beide sind am 26. Juli korrigiert, siehe §20 und §21. Zur Einordnung: Der
bereits vorhandene „Reconcile Unmapped Artists"-Job
([features F-08](library-v2-features.md#feat-unmapped), Button im
Maintenance-Modal der Artist-Seite) deckt Artists ganz ohne Metadaten-Quelle
bereits ab — dafür ist kein neuer Job nötig.

---

## 20. Pfad-Desync: Reconcile-Werkzeug und Missing-Lifecycle-Schutz (26. Juli)

Schließt [pathdrift25-01](library-v2-issues.md#pathdrift25-01) in zwei Teilen.

**Teil 1 — der Scan verwechselt „nicht auflösbar" nicht mehr mit „weg".**
`core/library2/scan.py::rescan_files` fragt für jeden unauflösbaren Pfad
`core/library2/path_drift.py::has_drift_candidate`: liegt im (über den
gemeinsamen Resolver aufgelösten) Verzeichnis eine Datei, die plausibel zu
dieser Zeile gehört? Wenn ja, zählt der Miss weiterhin, aber
`_persist_missing_observation(..., allow_confirm=False)` deckelt den Zustand
bei `missing_suspected`. Damit kann ein physisch vorhandener Song nicht mehr
nach zwei Scans als `missing_confirmed` in der Wanted-/Redownload-Logik
landen. Verschwindet der Kandidat später doch, bestätigt der nächste Scan
sofort — der Zähler läuft unverändert weiter. Neue Statistik: `path_drift`.

**Teil 2 — das in LV2-017 versprochene read-only Backfill-Werkzeug.** Neues
Modul `core/library2/path_drift.py` plus Repair-Job `path_drift_reconcile`
(„Stale Index Paths", Review-only, `default_enabled=False`,
`JOB_LIBRARY_V2_EFFECTS = {observe, path}`) und Fix-Handler
`_fix_stale_index_path` in `core/repair_worker.py`. Bewusste Grenzen:

- Der Scan schreibt nichts und bewegt keine Datei; er schlägt vor.
- Präzision vor Vollständigkeit: gleiche Endung + gleicher Titelschlüssel
  (Numerierung abgeschält, Unicode-erhaltend); eine abweichende Tracknummer
  disqualifiziert, außer die Dateigröße bestätigt die Paarung.
- Mehrere gleich plausible Treffer werden als `ambiguous` gemeldet und nie
  automatisch gewählt (LV2-017-Vertrag); solche Findings sind für den Worker
  bewusst nicht fixbar.
- Ein Kandidat, den bereits eine andere `lib2_track_files`-Zeile indiziert,
  wird nie gestohlen (`claimed`).
- Höchste Konfidenz zuerst: besitzt der Track eine `legacy_track_id`, deren
  `tracks.file_path` real auflöst, ist das der Vorschlag — genau der
  dokumentierte Entstehungsweg des Desyncs.
- `apply_path_drift_fix` prüft alle Vorbedingungen erneut, schreibt den Pfad
  im gespeicherten (Media-Server-)Namensraum — nur der Dateiname wird
  ersetzt — und zieht die Legacy-Zeile nur dann mit, wenn auch sie
  unauflösbar ist (H-11).

Verifikation: `tests/library2/test_path_drift.py` (19 Tests: Matching,
Ambiguität, Unicode, Endung, Claim, Bounded-Scan, Read-only, Apply-Guards,
beide Scan-Lifecycle-Fälle), `tests/repair_jobs/test_path_drift_reconcile.py`
(5 Tests inkl. Worker-Fix und Nachweis, dass keine Datei angefasst wird).

**Einstufung:** Implementiert und gezielt geprüft. Ein Lauf gegen die reale
Produktiv-DB des Nutzers steht aus und bleibt laut Guide §6.1 Backup-/
Dry-Run-pflichtig — der Job ist genau deshalb Review-only und
default-deaktiviert.

---

## 21. Manual Match: Artwork verlässt den Request-Pfad (26. Juli)

Schließt [manualmatch25-01](library-v2-issues.md#manualmatch25-01).
`api/library_v2.py::lib2_native_manual_match` committet den Match jetzt zuerst
und ruft danach `core/library2/native_enrich.py::
schedule_native_artist_artwork` — ein Daemon-Thread mit eigener Verbindung,
derselbe Off-Thread-Dispatch wie beim Legacy↔lib2-Link-Reconcile (§13
Finding 7). Der Artwork-Walk kann so beliebig lange dauern, ohne die Antwort
zu blockieren; weil die Hintergrund-Verbindung eine neue ist, sieht sie den
committeten Match (und der Request hält kein Write-Lock mehr).

Bewusst **nicht** mitgeändert: Der Walk bleibt sequenziell über alle am
Artist gespeicherten Provider-IDs (dieselbe bewusste Abweichung wie
[perf25-02](library-v2-issues.md#perf25-02) — Fan-out kostet zusätzliche
Provider-Calls für Latenz, die nach der Entkopplung niemand mehr sieht), und
die im Request gewählte `service` bestimmt weiterhin nur, welche ID
gespeichert wird. Beides ist nach der Entkopplung nicht mehr
nutzersichtbar; für ein gezielt anderes Bild existiert der Artwork-Picker
(F-01).

Verifikation: `tests/library2/test_api_routes.py` — der Match antwortet,
während der Enrich noch blockiert (Zeitschranke + Thread-Identität), die
Hintergrund-Verbindung sieht den committeten Match, und ein DELETE plant
keinen Walk.

**Einstufung:** Implementiert und gezielt geprüft; kein Browser-E2E.

---

## 22. Orphan-Approve: Simple Downloads werden materialisiert (26. Juli)

Setzt die §18-Entscheidung (Option 1) für
[issues §7](library-v2-issues.md#orphan-bug) um.
`core/library2/autolink.py` bricht bei einem Download ohne Titel/Artist und
ohne V2-Entity nicht mehr ab, sondern leitet eine Identität ab —
`_fallback_identity`:

1. eingebettete Tags der fertig importierten Datei (Grundwahrheit);
2. der Dateiname des Downloads, als `Artist - Titel` geparst (führende
   Track-/Disc-Numerierung wird vorher abgeschält, damit „01 - Song" nicht
   einen Artist namens „01" erzeugt);
3. der reine Dateistamm unter `UNKNOWN_ARTIST`.

Danach läuft der normale `_find_or_create_*`-Pfad, ein bereits existierender
Artist/Album/Track wird also wiederverwendet statt dupliziert. Nur wenn gar
kein Dateiname existiert, bleibt es beim alten Skip.

**Bewusste Grenze 1 — kein Acquisition-Intent:** Über den Fallback *neu
angelegte* Album-/Track-Zeilen starten `monitored=0` (neuer expliziter
Parameter an `_find_or_create_album`/`_find_or_create_track`). Eine geratene
Identität ist eine Beobachtung, kein Intent — sonst könnte „Unknown Artist /
mystery" in die Wanted-Projektion geraten. Trifft der Fallback eine bestehende
Zeile, bleibt deren Monitoring unangetastet.

**Bewusste Grenze 2 — keine geliehene Provider-Identität:** Auf dem
Fallback-Pfad ist `ti` das rohe `search_result`, dessen `id` der Result-Token
der *Quelle* ist (Soulseek/Usenet), keine Musik-Provider-ID. Sie wird jetzt
nicht mehr adoptiert — sonst landete ein Quelltoken in `spotify_id`/
`external_ids` (genau die §62.4-Vergiftung, die Guide §2.5 verbietet). Ein
`SPOTIFY_TRACK_ID`, der aus der Datei selbst gelesen wurde, ist dagegen eine
echte qualifizierte Identität und bleibt erhalten. Dieser Fehler entstand erst
dadurch, dass Simple Downloads diesen Code überhaupt erreichen — vorher brach
der Early Return vorher ab.

Verifikation: `tests/library2/test_autolink.py` (der frühere Beweis-Test
`test_simple_download_never_gets_a_file_row` ist durch
`test_simple_download_is_materialized_from_its_filename` ersetzt, plus
Tag-Vorrang, Unknown-Fall, Monitoring-Grenze, Skip ohne jede Identität, beide
Provider-ID-Fälle) und `tests/test_orphan_file_detector.py::
test_materialized_simple_download_is_no_longer_an_orphan` — der End-to-End-
Nachweis, dass genau derselbe Scan die Datei jetzt als bekannt erkennt.

**Einstufung:** Implementiert und gezielt geprüft. Der ursprüngliche
Nutzerbericht (Quarantäne-Approve) ist damit strukturell mit abgedeckt, weil
er denselben lückenhaften Context zurückspielt; ein realer Quarantäne-
Approve-Durchlauf am echten System steht aus.

---

## 23. F-10: `human_verified`/`rejected` bekommen ihre Korrelation (26. Juli)

Setzt die §18-Entscheidung für
[features F-10](library-v2-features.md#feat-history) um — der in §17 als
„eigener Schnitt" beschriebene Schema-Schritt.

- `library_history` bekommt `acquisition_request_id`,
  `acquisition_candidate_id`, `acquisition_download_id` plus Index
  (`database/music_database.py`, additive `ALTER TABLE`-Migration im
  bestehenden Migrationsblock). Präfix bewusst: die Tabelle führt bereits
  `source_track_id`/`download_source`, ein nacktes `request_id` läse sich dort
  wie ein Legacy-Begriff.
- `core/acquisition/pipeline_callback.py::persist_history_correlation`
  schreibt die Korrelation direkt nach dem History-Insert
  (`core/imports/side_effects.py`) über dieselbe Fail-open-Brücke
  (`_pipeline_correlation`) wie `previous_file_replaced`; ein gewöhnlicher
  Import bleibt ein Zero-Write-No-op.
- `notify_verification_decision` journalt aus den gespeicherten Spalten.
  `/api/verification/<id>/approve` meldet `human_verified`,
  `/api/verification/<id>/delete` meldet `rejected` — bewusst **vor** dem
  Löschen der Zeile, danach gäbe es nichts mehr zu korrelieren.
- `EVENT_TYPES` und `history_feed.EVENT_CATEGORY` kennen beide Events
  („Verified by you" / „Rejected by you").

Verifikation: `tests/acquisition/test_pipeline_callback.py` (5 neue Tests:
Persistenz, No-op ohne Acquisition, beide Entscheidungen, unkorrelierte Zeile
schreibt nichts, unbekannte Entscheidung wird abgelehnt),
`tests/library2/test_history_feed.py` (Feed-Darstellung beider Events).

**Einstufung:** Implementiert und gezielt geprüft. Damit ist F-10 nicht mehr
wegen fehlender Korrelation Partial; die verbleibende Lücke ist nur noch, dass
alte History-Zeilen keine Korrelation nachträglich bekommen (kein Backfill —
die Information existiert für sie nirgends).

---

## 24. Artwork-Kaltstart: Nachlieferung an den gerenderten Client (26. Juli)

Setzt die §18-Entscheidung zu [§13 Finding 2](library-v2-issues.md#rev25-02)
um. Der Mechanismus war ausdrücklich Implementierungsdetail; gewählt wurde
**serverseitig getriebenes Polling statt fixer Client-Retries**, weil ein
`<img>` `X-Artwork-Pending` nicht lesen kann und ein konstantes Retry-Budget
per Definition nicht an die reale Build-Dauer gekoppelt ist.

- `core/library2/artwork.py::artwork_build_states` beantwortet pro Entity
  `ready` (mit Cache-Bust-Version), `pending` (Build läuft/ist eingeplant) oder
  `unavailable` (nichts in Flight, nichts auf Platte).
- `GET /api/library/v2/artwork/status?kind=&ids=` liefert das gebündelt,
  `no-store`, auf 200 IDs gedeckelt.
- `webui/.../artwork-pending.ts` sammelt alle fehlgeschlagenen lokalen Cover
  einer Seite und pollt **einen** Request pro Tick (1,5 s → ×1,6 → max 15 s,
  harte Obergrenze 25 Ticks). `ready` rendert mit neuer Version, `unavailable`
  beendet das Warten sofort, ein Netzwerkfehler nagelt nicht die ganze Seite
  auf den Platzhalter fest.
- Die `Artwork`-Komponente ersetzt die drei festen Retries durch dieses Abo;
  die rev25-12-Invariante (kein Frame mit fremdem Cache-Bust-Suffix) bleibt
  durch Adjust-during-render erhalten, und `v` wird jetzt **ersetzt** statt
  angehängt.

**Bewusste Grenze:** Der Status-Endpoint plant für `unavailable` *keinen*
neuen Build ein. Wiederholte Provider-Walks für bildlose Entities sind
[Finding 10](library-v2-issues.md#rev25-10) (Negativ-Cache), und der bleibt
laut §18 zurückgestellt. Vorher kostete eine Seite mit 75 bildlosen Artists
bis zu 4 × 75 Requests; jetzt ist es ein gebündelter Poll pro Tick, der nach
der ersten `unavailable`-Antwort endet.

Verifikation: `tests/library2/test_artwork_background_build.py`
(ready/pending/unavailable inkl. Übergang nach fehlgeschlagenem Build),
`tests/library2/test_api_routes.py` (Route plus Eingabevalidierung),
`webui/.../artwork-retry.test.tsx` (9 Tests, gegen msw: Nachlieferung,
endgültiges Nein, ein gebündelter Request für mehrere Cover, keine Polls für
Remote-URLs, Fehlertoleranz, beide rev25-12-Invarianten, Mount pollt nicht).

**Einstufung:** Implementiert und gezielt geprüft; die Messung am echten
Kaltstart einer großen Bibliothek steht aus.

---

## 25. Gemeinsamer Testlauf für §20–§24 (26. Juli)

Ein Lauf über alle betroffenen Bereiche, damit die fünf Korrekturen nicht nur
einzeln belegt sind:

- Backend `tests/library2 tests/acquisition tests/imports tests/repair
  tests/repair_jobs tests/wishlist tests/watchlist tests/quality tests/search
  tests/test_orphan_file_detector.py`: **2825 passed, 3 skipped, 3 failed**;
- Frontend vollständige WebUI-Suite: **260 Tests in 44 Dateien** grün;
- `oxfmt --check` und `oxlint --type-check` auf allen geänderten
  Frontend-Dateien: sauber (die zwei vorbestehenden Warnungen in
  `library-v2-page.tsx` und die Formatabweichung in `artist-refresh.test.tsx`
  wurden bewusst nicht mit angefasst — fremde Zeilen);
- Ruff über alle geänderten Python-Dateien: sauber.

Die drei Fehlschläge sind **vorbestehend**, nicht durch diese Arbeit
verursacht:

| Test | Einordnung |
|---|---|
| `tests/library2/test_maintenance_sync.py::test_cover_art_scanner_flags_v2_only_album` | bereits in §14 dokumentiert (Fixture ohne vollständige Migrationskette) — diese Einordnung ist inzwischen widerlegt, es war ein echter Produktfehler, siehe §26 |
| `tests/library2/test_maintenance_sync.py::test_metadata_gap_scanner_covers_v2_only_track` | dito |
| `tests/test_orphan_file_detector.py::test_native_job_is_gated_when_library_v2_is_disabled` | neu als vorbestehend identifiziert: der Test pinnt die Gating-Semantik, die H-18 bewusst entfernt hat (`features.library_v2=false` wird ignoriert). Am `git stash`-sauberen Baum reproduziert. Bisher nirgends notiert; der Test gehört an den Cutover-Vertrag angepasst oder entfernt — umgesetzt in §26 |

---

## 26. Native Repair-Subject-Ausrichtung und Abbau der Test-Schuld (26. Juli)

Ausgangspunkt war die in §25 als „vorbestehend" abgelegte Fehlerliste. Bei der
Untersuchung stellte sich der erste Punkt als echter Produktfehler heraus, nicht
als Fixture-Artefakt.

**Teil 1 — nativer Subject-Row-Versatz (Produktfehler).** Diagnose in
[issues.md §14](library-v2-issues.md#nativepad25-01).
`core/repair_jobs/missing_cover_art.py` und
`core/repair_jobs/metadata_gap_filler.py` hängen ihre Library-v2-nativen
Subjects positionsgleich an die Legacy-Ergebniszeilen an, ließen dabei aber den
`ar.id`-Slot aus. Dadurch verschob sich jede optionale Provider-ID-Spalte um
eine Position, die letzte fiel aus dem Tupel — auf einer real migrierten DB
endet der Scan mit `IndexError`, sobald das **erste** V2-native Album bzw. der
erste V2-native Track drankommt, und reißt den gesamten Job inklusive der
bereits gefundenen Legacy-Zeilen mit. Beide Zeilen setzen den Slot jetzt
explizit auf `None` (ein natives Subject hat keine Legacy-Artist-Zeile; die
native Artist-ID steht ohnehin im `library_v2`-Block des Findings).

Warum das keine Testlücke „nur in der Fixture" war: Die beiden Regressionstests
liefen gegen ein synthetisches Legacy-Schema ohne `albums.spotify_album_id`/
`tracks.isrc`. Dort scheiterte schon die Legacy-Query, und der darauf folgende
`IndexError` wurde als Schema-Drift gelesen. Neue Fixture
`migrated_legacy_db` (`tests/library2/conftest.py`) zieht die Spalten nach, die
eine reale Installation per `ALTER TABLE` bekommt; die schmale `legacy_db`
bleibt unverändert, weil die meisten lib2-Tests positional inserten.

Verifikation: `tests/library2/test_maintenance_sync.py` — die beiden bisher
fehlschlagenden Tests laufen jetzt auf dem migrierten Schema und prüfen
zusätzlich `result.errors == 0`, `details['artist_id'] is None` und die
unverschobenen Per-Source-IDs; zwei neue Tests decken das andere Ende des
Pad-Bereichs ab (unmigriertes Schema: Legacy-Query scheitert, native Abdeckung
läuft trotzdem).

**Teil 2 — Cutover-Vertrag statt Gating-Test.**
`tests/test_orphan_file_detector.py::test_native_job_is_gated_when_library_v2_is_disabled`
pinnte die von [H-18](library-v2-issues.md#h-18) bewusst entfernte
Gating-Semantik. Ersetzt durch
`test_deprecated_false_flag_cannot_silence_the_native_scan`: dieselbe Situation,
aber mit der heute geltenden Erwartung — der ignorierte Flag darf den nativen
Scan nicht stumm schalten (`scanned == 1`, keine Findings, weil die Datei dem
Katalog bekannt ist).

**Teil 3 — Alt-Tests für entfernte Handler abgebaut.** Die acht in §14
genannten Fehlschläge testeten `_fix_unknown_artist`, `_fix_duplicates` und
`_perform_album_fill`, die mit der P1/P2-Tool-Migration entfernt wurden. Vor dem
Löschen wurde für jeden gepinnten Vertrag geprüft, ob er im Nachfolgepfad
weiterlebt:

| Alt-Test | Gepinnter Vertrag | Entscheidung |
|---|---|---|
| `test_repair_worker_unknown_artist_path.py` (2) | #978: ein Media-Server-File darf nicht in den Transfer-Ordner gezogen werden | gelöscht — der überlebende `_fix_path_mismatch` hat den Guard samt eigener Abdeckung in `tests/test_repair_worker_path_mismatch.py` |
| `test_repair_worker_album_fill.py` (3) | Artist-Mismatch beim Kopieren eines Tracks aus einem anderen Album | gelöscht — der native Wanted-/Acquisition-Pfad kopiert nicht aus der eigenen Library, sein Artist-Gate ist das Eligibility Gate (LIB2-F01) |
| `test_repair_worker_duplicate_delete.py` (3 von 5) | ein fehlgeschlagener physischer Delete darf nicht als Erfolg gelten (Docker-PUID, unauflösbarer Pfad) | Vertrag **übernommen**: neuer Test `test_unlink_failure_is_journalled_and_never_reported_as_success` in `tests/library2/test_file_delete.py` (Status `partial`, Item `failed` mit Fehlertext, Datei bleibt liegen, `lib2_track_files` bleibt aktiv) |

Die beiden lebenden `skip_deleted_quarantine`-Tests der dritten Datei sind nach
`tests/test_repair_deleted_quarantine_skip.py` umgezogen — der alte Dateiname
beschrieb eine Engine, die es nicht mehr gibt.

**Gemeinsamer Testlauf.** Erstmals seit dem Foundation-Rebase ohne bekannte
Fehlschläge:

- `tests/library2 tests/repair tests/repair_jobs tests/test_orphan_file_detector.py
  tests/test_repair_deleted_quarantine_skip.py tests/test_repair_worker_path_mismatch.py`:
  **1151 passed, 0 failed**;
- `tests/imports tests/acquisition tests/wishlist tests/watchlist tests/quality
  tests/search`: **1688 passed, 3 skipped, 0 failed**;
- Ruff über alle geänderten Python-Dateien: sauber.

Frontend blieb unangetastet (kein `webui/`-Diff), daher kein Frontend-Lauf.

**Einstufung:** Implementiert und gezielt geprüft. Damit ist die in §14/§25
geführte Liste vorbestehender Fehlschläge vollständig abgebaut. Der Lauf beider
Scanner gegen einen Snapshot der realen Produktiv-DB ist in §27 Teil 1
nachgeholt: 33 Alben bzw. 424 Tracks, `errors=0`, Pad-Breite real 4 — ohne den
Fix wäre der Scan beim ersten der 24 nativen Alben abgebrochen.

---

## 27. Erster Produktiv-DB-Lauf, Album-Twin-Scan und Frontend-Gate (26. Juli)

Diese Session hat den in §9 und in §20/§22/§26 wiederholt offen geführten Lauf
gegen die reale Bibliothek des Nutzers erstmals durchgeführt, die dabei
gefundene echte Lücke geschlossen und zwei kleinere Frontend-Rückstände
abgebaut. Diagnosen in
[issues.md §15](library-v2-issues.md#15-erster-lauf-gegen-die-reale-produktiv-db-26-juli-2026).

### Teil 1 — Lauf gegen einen Snapshot der Produktiv-DB

Ausgeführt auf einem `sqlite3.backup()`-Snapshot (98 MB, 5 Artists, 273
lib2-Alben, 2.048 lib2-Tracks, 270 lib2-Files); die Live-Datei wurde nie zum
Schreiben geöffnet. Ergebnis:

| Prüfung | Ergebnis | schließt |
|---|---|---|
| Schema-Migration auf der gewachsenen DB | fehlerfrei | §9 „Migrations-Test auf einer Kopie einer produktiven DB" (Soak weiterhin offen) |
| `missing_cover_art` | 33 Alben (9 Legacy + 24 nativ), `errors=0` | §26 „Lauf gegen die reale Produktiv-DB steht aus" |
| `metadata_gap_filler` | 424 Tracks, `errors=0` | dito |
| §23-Korrelationsspalten auf `library_history` | alle drei plus Index vorhanden | §23 |
| `path_drift_reconcile` | 2 unauflösbare Zeilen, 0 Drift-Kandidaten | §20 |
| `orphan_file_detector` | 144 Dateien, 0 Orphans | §22 |
| `build_integrity_report` (read-only) | 113 Findings, siehe Teil 3 | LV2-013 |
| `repair_duplicate_artists` (Dry Run auf der Kopie) | 0 Merges — und deckte damit Teil 2 auf | LV2-012 / F-07 |

Die Pad-Breite auf der realen `albums`-Tabelle ist 4 (alle vier optionalen
Provider-ID-Spalten vorhanden). Vor §26 wäre der Cover-Art-Scan also beim
**ersten** der 24 nativen Alben mit `IndexError` abgebrochen — der Fix ist
damit nicht nur gegen die neue Fixture, sondern gegen echte Daten belegt.

### Teil 2 — Album-Twin-Pass läuft jetzt für jeden Artist

[realdb25-01](library-v2-issues.md#realdb25-01):
`core/library2/dedup_repair.py::repair_duplicate_artists` rief
`_fold_albums_within_artist` nur für `touched_artists` auf — also nur für
Artists, die im selben Lauf aus einem Merge hervorgegangen waren. Ein Artist
mit einer sauberen, einmaligen Zeile wurde nie besucht, seine Album-Twins
folglich weder gefoldet noch als Review-Finding erfasst. Auf der realen DB war
das die gesamte Population: drei Album-Paare mit jeweils **identischer**
`stable_id`, davon eines (Justin Bieber „SWAG II") ohne jedes Finding, weil für
diesen Artist auch nie ein MB-Release-Group-Reconcile gelaufen war.

Neu: `_artists_with_album_twins` ermittelt in **einem** Scan über
`lib2_album_artists ⋈ lib2_albums`, welche Artists überhaupt einen
Titel-Twin halten; der Pass läuft dann für die Vereinigung aus diesen und den
Merge-Survivorn. Bewusste Grenzen:

- Die Fold-Regeln sind unverändert. `_is_pristine` und `_counts_compatible`
  entscheiden weiter; alle drei realen Paare tragen auf beiden Seiten Files und
  werden deshalb korrekt **nicht** gemerged, sondern als
  `duplicate_title_unmerged` gemeldet. Der Fix ändert, *für wen* ausgewertet
  wird, nicht *wie*.
- Die Kandidatensuche ist bewusst ein einzelner Scan statt einer
  `_album_rows_for_artist`-Query pro Artist — diese Query trägt zwei
  korrelierte Subselects, und ein Aufruf pro Artist wäre genau die
  Leerlauf-Query-Flut aus [BR-08](library-v2-issues.md#br-08).
- Ein leerer Titelschlüssel gruppiert nie: zwei unbenannte Zeilen sind kein
  Beleg für dieselbe Release.

Verifikation: vier neue Tests in `tests/library2/test_dedup_repair.py` (Fold
ohne Artist-Merge; Review-Finding ohne Artist-Merge; Album vs. gleichnamige
Single bleiben getrennt — DD-G1-Bucket; leere Titel gruppieren nicht). Die
ersten beiden schlugen vor dem Fix fehl. Zusätzlich gegen einen frischen
Snapshot der Produktiv-DB: das bisher unsichtbare „SWAG II"-Paar erscheint
jetzt als Review-Finding (3 → 4 offene Findings), Artist-, Album-, Track- und
File-Zahlen bleiben unverändert — es wurde nichts gemerged und nichts gelöscht.

### Teil 3 — Was der Integritätsreport zusätzlich zeigt (offen)

[realdb25-02](library-v2-issues.md#realdb25-02): 112 Dateien hängen an mehr als
einem Katalog-Track. 103 Gruppen sind die Album-Twins aus Teil 2 plus
legitime Album↔Single-Paare (DD-G1). Die restlichen **21 Gruppen liegen
innerhalb desselben Albums** — Album 1064 führt 41 Track-Zeilen bei
`track_count=21`; katalogweit 80 Album/Titel-Paare mit Mehrfachzeilen und 122
doppelte `lib2_tracks.stable_id`.

Bewusst **kein** Fix in dieser Session: Das Falten von Track-Zeilen berührt
Monitor-Rules, Wanted-Projektion, History und Quality-Zuweisung und braucht
dieselbe Art Produktentscheidung wie §16/§18 (welche Zeile überlebt, was mit
dem Intent der anderen geschieht). Der Zustand ist über den Integritätsreport
bereits sichtbar. **Status: Pending — Produktentscheidung ausstehend.**

### Teil 4 — Frontend-Gate und zwei Altitude-Rückstände

- `npm run check` läuft erstmals seit dem Foundation-Rebase mit Exit-Code 0.
  Die in §25 als „fremde Zeilen" eingeordnete Formatabweichung in
  `artist-refresh.test.tsx` war eine Fehleinordnung: Die Datei existiert nur
  auf diesem Branch (`cea13f6f`), gehört also uns. Damit ist der §9-Punkt
  „vollständiger kombinierter Frontend Check/Build auf finalem HEAD" belegt:
  Check sauber, 269 Tests in 45 Dateien grün, Production Build erfolgreich.
- `detail.rejections` war `Array<Record<string, unknown>>` und wurde per
  `String()` gerendert. Das erzeugte die zwei `no-base-to-string`-Warnungen aus
  §25 und — schwerwiegender — eine unbrauchbare Review-Liste: Der häufigste
  Konflikt `missing_expected_track` trägt keinen Pfad, stand also als nacktes
  „missing expected track" da, ohne zu sagen, **welcher** Track fehlt. Das ist
  genau die Information, für die es die F-12-Review-Oberfläche gibt. Neu:
  `LibraryV2AcquisitionRejection` bildet die tatsächliche Payload von
  `bundle_matching.py::match_bundle` ab, und
  `-ui/acquisition-rejection.ts::describeRejection` erzeugt pro Code die
  identifizierende Zeile (Position + Titel beim fehlenden Track, Pfad + Grund
  bei `ambiguous_position`, Prozentwerte bei `ambiguous_title`/
  `low_confidence`). 9 neue Tests, inklusive des Falls, dass ein verschachtelter
  Wert nie als „[object Object]" ins DOM gelangt.
- `TrackPlayButton` nahm eine `albumId`-Prop entgegen, die nirgends benutzt
  wurde (die Bridge bekommt bewusst `album_id: null`, weil ihr Slot eine
  Legacy-ID erwartet — [H-14](library-v2-issues.md#h-14)). Prop entfernt, die
  Begründung steht jetzt als Kommentar am Nullwert.

### Gemeinsamer Testlauf

- `tests/library2`: **1032 passed** (1028 + 4 neue), 0 failed;
- `tests/imports tests/wishlist`: **913 passed**;
- `tests/repair tests/repair_jobs tests/acquisition tests/search`:
  **568 passed, 3 skipped**, 0 failed;
- Ruff über alle geänderten Python-Dateien: sauber;
- Frontend: `npm run check` Exit 0, **269 Tests in 45 Dateien**, Production
  Build erfolgreich.

**Einstufung:** Teil 1 und 2 implementiert und sowohl gezielt als auch gegen
einen Snapshot der Produktiv-DB geprüft; Teil 4 implementiert und geprüft.
Teil 3 bleibt offen und braucht eine Nutzerentscheidung. Der übrige §9-Gate-
Stand (realer Client-E2E, Restart-Szenarien, Windows-/Docker-Path-Mapping,
F-12-Browser-E2E) ist unverändert.

---

## 28. Reconcile Unmapped Artists — Root Cause dokumentiert, Korrektur ausstehend (26. Juli 2026)

Ausgangspunkt war der Nutzerwunsch, den "Reconcile Unmapped Artists"-Job
([F-08](#2-feature-status)) automatisch nach abgeschlossenen Imports laufen
zu lassen. Bei der Prüfung, ob der Job dafür zuverlässig genug ist, wurden
zwei Root Causes bestätigt; Diagnose und Korrekturverträge stehen in
[issues.md §16](library-v2-issues.md#16-reconcile-unmapped-artists-namensbasiertes-matching-ignoriert-vorhandene-starke-ids-26-juli-2026).

| # | Finding | Status | Referenz |
|---:|---|---|---|
| 1 | Namens-Resolve ignoriert bereits vorhandene starke Provider-IDs auf Album/Track des Artists; stoppt zudem bei der ersten Quelle statt alle durch eine sichere Anker-ID belegten Quellen zu übernehmen | Implemented → [§30](#30-werkzeugweiser-deep-dive-t-11-t-12-und-der-post-import-trigger-26-juli-2026-nacht) | [issues.md Finding 1](library-v2-issues.md#unmappedreconcile26-01) |
| 2 | Keine `last_attempted_at`/Cooldown-Markierung — ein automatisierter, wiederholter Trigger würde dauerhaft ungematchte Artists bei jedem Lauf erneut gegen alle konfigurierten Provider abfragen | Implemented → [§30](#30-werkzeugweiser-deep-dive-t-11-t-12-und-der-post-import-trigger-26-juli-2026-nacht) | [issues.md Finding 2](library-v2-issues.md#unmappedreconcile26-02) |

**Einstufung (Stand dieses Eintrags):** Beide Root Causes bestätigt und
dokumentiert, keine Korrektur in dieser Session. Der vom Nutzer gewünschte
automatische Post-Import-Trigger dieses Jobs baut auf diesen beiden
Korrekturen auf: Finding 1 senkt das Fehlmatch-Risiko eines unbeaufsichtigten
(nicht mehr manuell per Button ausgelösten) Laufs, Finding 2 verhindert
unkontrollierte wiederholte Provider-Anfragen bei hoher Import-Frequenz.

**Nachtrag:** Beide Korrekturen und der Trigger selbst sind in §30 umgesetzt.
Der offene Trigger-Zeitpunkt wurde am 26. Juli vom Nutzer entschieden: nach
**jedem** abgeschlossenen Import, abgesichert durch Debounce und den Cooldown
aus Finding 2.

---

## 29. Werkzeug-↔-Library-V2-Konvergenz: sechs Korrekturen (26. Juli 2026, Abend)

Nutzer-Bugreport: Cover-Art-Finding korrekt erkannt, aber „Fix Finding",
„Refresh & Scan" und Browser-Neustart lassen „2 tag gaps" (`genre`, `cover`)
stehen; ein Klick auf die Lückenzahl meldet „Tags written" und ändert nichts;
„Preview Retag" behauptet „Tags match"; keine Spalte zeigt, **wie** eine Datei
verifiziert wurde. Diagnose und Korrekturverträge stehen in
[issues.md §17](library-v2-issues.md#17-werkzeuge-und-library-v2-konvergieren-nicht-nutzer-bugreport-vom-26-juli-2026-abend);
diese Tabelle enthält ausschließlich den Bearbeitungsstand.

| # | Finding | Status | Umsetzung |
|---|---|---|---|
| [T-01](library-v2-issues.md#tool26-01) | Findings mit Legacy-Entity-ID erreichen Library V2 nie (`subject_unlinked`) | Implemented | `_resolve_links` löst zusätzlich über `legacy_artist_id`/`legacy_album_id`/`legacy_track_id` auf (Textvergleich, kein `int()`); Track-Subjects ohne benanntes File ziehen ihre Files nach, aber nur wenn kein File benannt war (ADR-03) |
| [T-02](library-v2-issues.md#tool26-02) | Nicht-Konvergenz gilt als Erfolg | Implemented | `sync_repair_change` liefert `converged`; `fix_finding` setzt `library_v2_converged=False` und loggt eine Warnung, statt still zu resolven |
| [T-03](library-v2-issues.md#tool26-03) | „N tag gaps" schreibt strukturell nichts, meldet aber Erfolg | Implemented | Fehlendes Cover ist im `write_tags`-Fastpath ein eigener Schreibgrund; die Gap-Zelle liest `written` und meldet „Nothing to write", wenn nichts geschrieben wurde |
| [T-04](library-v2-issues.md#tool26-04) | Preview meldet „Tags match" trotz fehlendem Cover | Implemented | `_db_data_for_row` trägt `thumb_url` nach (Override → `lib2_albums.image_url`), damit `build_tag_diff` die Cover-Zeile ehrlich rendert |
| [T-05](library-v2-issues.md#tool26-05) | `write_tags` kennt nur die Artwork-Cache-Datei | Implemented | `_album_cover_data` materialisiert über `build_artwork` (Guide §2.1-Reihenfolge, eigener Single-Flight-Lock) und nur, wenn überhaupt eine Cover-Quelle existiert |
| [T-06](library-v2-issues.md#tool26-06) | Genre-Lücke katalogseitig unfüllbar | **Bewusst offen** → [§30](#30-werkzeugweiser-deep-dive-t-11-t-12-und-der-post-import-trigger-26-juli-2026-nacht) | Der naheliegende Vertrag („Album-Genres beim Provider holen") wurde gegen die echten Alben geprüft und **widerlegt** — keine Quelle liefert Genres. Der Nutzer hat die drei Entwurfsfragen am 26. Juli mit „offen lassen" beantwortet |
| [T-07](library-v2-issues.md#tool26-07) | Ogg/Opus meldet dauerhaft ein fehlendes Cover | Implemented | `read_file_tags` erkennt `metadata_block_picture` wie `art_apply` — eine Wahrheit für Gap-Anzeige, Scan und Apply |
| [T-08](library-v2-issues.md#tool26-08) | „Refresh & Scan" erneuert keine Provider-Metadaten | Implemented | Der Datei-Pass leistet Tags + Quality-Probe + Missing-Lifecycle und seit T-09 auch Verification; sein Tooltip grenzt den Datei-Scope jetzt ausdrücklich von Enrich/Discography Refresh ab |
| [T-09](library-v2-issues.md#tool26-09) | Verification-Tag wird gelesen und weggeworfen | Implemented | `_persist_verification_observation` adoptiert `SOULSYNC_VERIFICATION`; unbekannte Werte ignoriert, fehlender Tag löscht nichts, `human_verified` wird nie überschrieben |
| [T-10](library-v2-issues.md#tool26-10) | Keine Verification-Spalte | Implemented | Opt-in-Spalte `verification` in `track_table`; leere Zelle erklärt im Tooltip, wie sich der Wert beschaffen lässt |
| [T-11](library-v2-issues.md#tool26-11) | `genre_cleanup`/`comma_artist_splitter` sind legacy-only | Implemented → [§30](#30-werkzeugweiser-deep-dive-t-11-t-12-und-der-post-import-trigger-26-juli-2026-nacht) | Beide Jobs besitzen native `lib2`-Überschreibungen; Regressionen und Datenbasis-Gate sind vorhanden |

### Verifikation

Alle Belege stammen aus Läufen gegen einen `sqlite3.backup()`-Snapshot der
realen Produktiv-DB mit den echten Audiodateien; die Live-DB wurde nie
schreibend geöffnet.

**Ende-zu-Ende, echte FLAC (Cover + Genre-Tag entfernt), vorher → nachher:**

| Schritt | vorher | nachher |
|---|---|---|
| Refresh & Scan erkennt | `["genre","cover"]` | `["genre","cover"]` (unverändert korrekt) |
| Preview Retag | `has_changes False` („Tags match") | `Cover Art: None → Available, changed=True` |
| Klick „N tag gaps" | `written 0, skipped 1` | `written 1` |
| Gaps danach | `["genre","cover"]` | `["genre"]` |

Der verbleibende Genre-Gap ist T-06 und bleibt bewusst offen.

**T-01 gegen die drei real offenen Produktiv-Findings** (vorher alle
`subject_unlinked`):

```
finding 15 album_tag_consistency entity='630009860' -> artists=[30] albums=[1066] tracks=34 files=2
finding 18 album_tag_consistency entity='709335827' -> artists=[28] albums=[1064] tracks=41 files=41
finding 19 library_reorganize    entity='234986381' -> artists=[28] albums=[1174,1344] tracks=2 files=2
```

**T-09, ein einzelner `rescan_files`-Lauf über die Produktiv-Kopie:**

| `verification_status` | vorher | nachher |
|---|---:|---:|
| `(null)` | 199 | **5** |
| `verified` | 57 | 219 |
| `unverified` | 6 | 29 |
| `human_verified` | 8 | 17 |

(Die verbleibenden 5 sind 2 physisch fehlende Dateien und 3 ohne Tag.)

**Testläufe auf diesem Stand:**

- `tests/library2`: 1.050 bestanden (neu: 4 Konvergenz-, 4 Retag-Cover-,
  4 Verification-Heilungs-Tests);
- `tests/repair`, `tests/repair_jobs`, Tag-Writer-Suiten: 170 bestanden;
- `tests/test_tag_writer_cover_detection.py` (neu, echte ffmpeg-Fixtures):
  3 bestanden;
- WebUI-Gesamtsuite: 270 Tests in 45 Dateien bestanden;
- `npm run check` (oxfmt + oxlint --type-check): 0 Fehler, 2 vorbestehende
  Warnungen in unberührten Dateien.

### Einstufung

Die vom Nutzer beschriebene Kette „Werkzeug erkennt richtig → Fix → Library V2
zeigt es trotzdem nicht" ist an fünf Stellen geschlossen und an einer
(T-06 Genre) bewusst offen und ehrlich beschriftet. Der hier beauftragte
Deep-Dive über alle 25 registrierten Jobs wurde anschließend in §30
durchgeführt; T-11 und T-12 sind dort implementiert.

---

## 30. Werkzeugweiser Deep-Dive: T-11, T-12 und der Post-Import-Trigger (26. Juli 2026, Nacht)

Diese Session hat den in §29 beauftragten Deep-Dive über alle 25 registrierten
Jobs durchgeführt, seine beiden Identitätslücken geschlossen und den in §28
offenen Reconcile-Automatismus fertiggebaut. Das Auditergebnis steht in
[issues.md §19](library-v2-issues.md#19-ergebnis-des-werkzeugweisen-deep-dive-26-juli-2026-nacht),
diese Tabelle enthält nur den Bearbeitungsstand.

| # | Punkt | Status | Umsetzung |
|---|---|---|---|
| [T-06](library-v2-issues.md#tool26-06) | Genre-Lücke katalogseitig unfüllbar | **Bewusst offen (Nutzerentscheidung)** | Der Nutzer hat am 26. Juli aus vier vorgelegten Verträgen „offen lassen" gewählt. Kein Artist-Genre-Fallback, kein `metadata_cache_entities`-Rückgriff, kein Schreiben nach `lib2_albums.genres`. Die Gap-Zelle meldet weiterhin ehrlich „Nothing to write" |
| [T-11](library-v2-issues.md#tool26-11) | `genre_cleanup`/`comma_artist_splitter` deklarieren `lib2`, lesen Legacy | Implemented | Beide bekommen eine native Überschreibung in `native_p3.py`; Basisklassen behalten ihre Legacy-Körper für das Rollback-Fenster |
| [T-12](library-v2-issues.md#tool26-12) | `library_reorganize` mintet nackte native IDs | Implemented | Beide `create_finding`-Aufrufe schreiben `lib2:<id>` plus `details['library_v2']` |
| [§16 F1](library-v2-issues.md#unmappedreconcile26-01) | Namens-Resolve ignoriert vorhandene starke IDs | Implemented | Anker-Resolve über Album-/Track-Provider-IDs vor der Namenssuche; jede belegte Quelle wird geschrieben, nicht nur die erste |
| [§16 F2](library-v2-issues.md#unmappedreconcile26-02) | Kein Cooldown für dauerhaft ungematchte Artists | Implemented | `unmapped_last_attempted_at` + `cooldown_hours`-Parameter; jeder Versuch wird gestempelt, auch der fehlgeschlagene |
| §28 | Automatischer Post-Import-Trigger | Implemented | `core/library2/unmapped_trigger.py`, verdrahtet in den Post-Import-Side-Effects |
| §27 Teil 3 | Doppelte Track-Zeilen in der Produktiv-DB | **Zurückgestellt (Nutzerentscheidung)** | Der Nutzer hat klargestellt, dass die lokale DB reines Testmaterial ist und nicht repariert werden muss. Kein Fold-Pass gebaut; der Zustand bleibt über den Integritätsreport sichtbar |

### Teil 1 — T-11: die letzten beiden Legacy-Leser

Beide Basisklassen haben genau vier Stellen, an denen sie den Katalog
berühren. Diese sind zu Methoden extrahiert (`_genre_rows`,
`_comma_artist_rows`, `_finding_identity`, `_library_artist_id`,
`_sample_tracks`, `estimate_scope`) und in `native_p3.py` überschrieben — das
Muster der sechs bereits nativen Job-Identitäten. Bewusst **nicht** angefasst:

- Die Semantik. Genre Cleanup entfernt weiterhin nur (#1057) und erfindet
  nichts; der Comma Splitter behält Whitelist, Voll-String-API-Prüfung und die
  Regel „ein nicht auflösbarer Bestandteil kippt das Finding".
- Die Legacy-Körper. Sie bleiben als Basisimplementierung stehen, weil das
  Rollback-Fenster laut Guide §1 noch offen ist.

Native Besonderheiten: Ein Artist „hält" eine Datei, wenn er auf dem Track
kreditiert **oder** Primary-Artist von dessen Album ist — die zwei Wege, auf
denen der Importer einen komma-verbundenen Tag-String ablegt. Und der leere
Genre-Wert ist nativ `'[]'`, nicht `NULL`: `lib2_artists.genres` und
`lib2_albums.genres` sind `NOT NULL DEFAULT '[]'`, der Fix schreibt daher
immer eine JSON-Liste.

Zusätzlich brauchte T-11 eine Erweiterung an `_resolve_links`: Ein
Artist-Subject ohne Album/Track/File zieht jetzt die Dateien dieses Artists
nach — aber nur, wenn der Job `tags`, `path` oder `new_file` deklariert. Ohne
das liefe nach dem Comma-Split kein `rescan_files` und die Tag-Snapshots
zeigten weiter den alten kombinierten Artist. Mit der Effekt-Schranke bleibt
Genre Cleanup (`observe`, `metadata`) schmal und schleppt keine Diskografie in
einen Rescan (BR-08).

Damit `JOB_DATA_BASIS` nicht wieder zu einem ungeprüften Versprechen wird
(genau die T-11-Ursache), pinnt ein neuer Test in
`tests/repair/test_job_data_basis.py` die Menge der Identitäten, deren
registrierte Implementierung aus `native_p3` stammt — jetzt acht statt sechs.

### Teil 2 — T-12: eine nackte Zahl ist seit T-01 eine Legacy-ID

Im Audit neu gefunden. `library_reorganize` liest nativ, schrieb seine
Zeilen-IDs aber unpräfixiert; seit T-01 wird das als Legacy-Rückverweis
interpretiert. Der Reproduktionsfall steht in issues.md §19.2: Track 9 trägt
`legacy_track_id=4`, das Finding gilt Track 4 — vor dem Fix lieferte die
Auflösung beide. Weil `annotate_finding_details` schon beim Erzeugen läuft,
wurde der falsche Verweis gespeichert, nicht erst beim Fix errechnet.

### Teil 3 — §16: Anker vor Namen, und ein Backoff für das Unlösbare

Finding 1 (Anker-Resolve) war beim Sessionbeginn bereits im Worktree, aber
undokumentiert; §28 führte ihn noch als „Pending". Der Vertrag ist
eingelöst: `_artist_catalog_anchors` sammelt in zwei Queries jede starke
Provider-ID von Alben und Tracks des Artists, `resolve_and_enrich_native_artist`
fragt **jede** so belegte Quelle per ID-Lookup ab und schreibt alle Treffer.
Nur ein Artist ganz ohne Anker fällt auf die alte Namenssuche zurück — dort
bewusst weiter mit Stopp beim ersten Treffer.

Finding 2 ist neu: `unmapped_last_attempted_at` existierte als Spalte, aber
niemand las oder schrieb sie. Jetzt stempelt jeder Versuch — auch der, dessen
Provider-Aufruf geworfen hat, und zwar **nach** dem Rollback, damit ein
kaputter Provider nicht bei jedem Trigger erneut befragt wird.
`_pending_unmapped_artists` filtert nur, wenn ein `cooldown_hours` übergeben
wurde: der manuelle Button bleibt „ganzer Backlog", der Automatismus bekommt
das Fenster.

### Teil 4 — §28: der automatische Trigger

`core/library2/unmapped_trigger.py` hängt in den Post-Import-Side-Effects
direkt hinter dem Library-v2-Autolink — der Stelle, an der neue native
Artists tatsächlich entstehen. Damit deckt ein Hook alle Importwege ab
(Auto-Import, manueller Import, Wishlist-Download, Manual Grab), statt zwei
`import_completed`-Emitter zu patchen und den Download-Pfad zu verfehlen.

Zwei Eigenschaften machen das tragbar:

- **Coalescing.** Der Hook feuert pro Datei; ein 30-Track-Album-Import ergibt
  über ein Debounce-Fenster (Default 120 s) genau einen Lauf. Ein Trigger, der
  *während* eines laufenden Passes eintrifft, wird nicht verworfen, sondern neu
  armiert — der laufende Pass hat seine Kandidatenliste vor diesen Artists
  gelesen.
- **Backoff.** Der Lauf übergibt `cooldown_hours` (Default 168).

Konfigurierbar über `library_v2.unmapped_reconcile.auto_after_import`,
`.debounce_seconds` und `.cooldown_hours`; ohne Eintrag gelten die Defaults.
Der Hook kann nie in die Pipeline werfen — die Datei liegt zu diesem Zeitpunkt
bereits importiert auf der Platte.

Bewusst so gewählte Konsequenz für den **Bootstrap-Import**: Das Debounce ist
leading-edge und armiert sich während eines stundenlangen Massenimports immer
wieder neu, der Job läuft also mehrfach statt einmal am Ende. Die Provider-Last
bleibt trotzdem bei etwa einem Lookup pro neuem unmapped Artist, weil der
Cooldown jede Zeile nach ihrem ersten Versuch für eine Woche ausschließt. Der
Alternativentwurf (nur einmal nach Abschluss der Bootstrap-Phase) wurde
verworfen, weil „die Bootstrap-Phase ist zu Ende" kein Signal ist, das die
Pipeline heute liefert.

---

## 31. Ergänztes Nutzer-Anforderungspaket für Library V2 (27. Juli 2026)

Aufnahme aller am 27. Juli 2026 definierten Nutzeranforderungen, UI-Optimierungen und Bugfix-Aufträge.

> **Regel für die nächste Chat-Session:** Der nächste Chat muss vor der Bearbeitung der hier aufgeführten Punkte selbstständig im Code recherchieren und bei etwaigen Unklarheiten gezielt Gegenfragen stellen!


### Übersichtstabelle der neuen/angepassten Punkte

| # | Anforderung / Modul | Typ | Status | Referenz / Issue | Kurzbeschreibung |
|---:|---|---|---|---|---|
| 1 | Track File Size Column | UI / Feature | **Verified** (§37) | [UI-03](library-v2-features.md#ui-columns) | Eigene sortierbare Spalte für die primäre physische Track-Datei; unabhängig vom Release-Typ |
| 2 | Resizable Table Columns | UI / Feature | **Verified** (§37) | [UI-03](library-v2-features.md#ui-columns) | Persistentes Drag-/Keyboard-Resizing mit Pointer Capture, Grenzen und Doppelklick-Reset |
| 3 | Files & Tools -> Maintenance UX | UI / UX | **Verified** (§37) | [iss27-08](library-v2-issues.md#iss27-08) | „Library Health & Repair“ gruppiert Werkzeuge verständlich und zeigt Artist-/Library-Scope explizit |
| 4 | Reorganize All Mechanismus | Dokumentation | Verified | [guide §5](library-v2-guide.md#5-technische-invarianten) | Ablauf bei Einstellungsänderung (Pfad-Templates, Move-Plan, Path-Sync & History) dokumentiert |
| 5 | Preview Re-Tag UX | UI / UX | **Verified** (§37) | [iss27-07](library-v2-issues.md#iss27-07) | Stabile Gruppierung per Album-ID, visuelle Release-Grenzen, Typ und Änderungszähler |
| 6a | Tags Match Hover Breakdown | UI / Feature | **Verified** (§37) | [F-15](library-v2-features.md#feat-metadata) | Portal-Tooltip bei Hover und Keyboard-Fokus mit vorhandenen/fehlenden Tags und Aktionshinweis |
| 6b | Tag Gap Klick-Aktion Fix | Bugfix | **Implemented** (§32) | [iss27-02](library-v2-issues.md#iss27-02) | Klick auf Tag Gap löst Provider-Re-Fetch und Schreiben der Tags in Datei aus |
| 7 | Artist-scoped Refresh & Scan | Feature / Fix | **Verified** (§32) | [iss27-05](library-v2-issues.md#iss27-05) | Strikter Artist-Scope + physische Datei-Inspektion (Audio Stream Quality, Features, Verification Tags) — war bereits per `0cd7167a6` behoben |
| 8 | Column Settings Layout Redesign | UI / UX | **Verified** (§37) | [iss27-06](library-v2-issues.md#iss27-06) | Kompaktes responsives Mehrspalten-Layout für Spalten, Quality/Größen und Match-Provider |
| 9 | Navigation State Reset bei Artist-Wechsel | UI / UX | **Implemented** (§32) | [iss27-04](library-v2-issues.md#iss27-04) | Beim Betreten eines neuen Artists immer auf „My Library" zurücksetzen (kein Auto-Fetch von All Releases) |
| 10 | Change Photo Provider Reliability | Bugfix | **Implemented** (§32) | [iss27-03](library-v2-issues.md#iss27-03) | Verlässliche Foto-Abfrage über alle 5-6 Metadata Provider ohne Stille Ausfälle — Fanart.tv-Integration bewusst nicht enthalten (neues Feature, kein Fix) |
| 11a | Verification Tag Reader | Backend / Feature | **Verified** (§29/§32) | [F-15](library-v2-features.md#feat-metadata) | Der reale kanonische Tag `SOULSYNC_VERIFICATION` wird eingelesen; die drei ursprünglich genannten Tag-Namen existieren im Produkt nicht |
| 11b | Verification Table Column | UI / Feature | **Verified** (§29/§37) | [UI-03](library-v2-features.md#ui-columns) | Opt-in-Spalte zeigt die vier kanonischen `verification_status`-Zustände und erklärt fehlende Provenienz |
| 12 | Import Review Removal | Decision | Removed | [F-12](library-v2-features.md#feat-acq-review) | `/import-review` Route und UI-Seite vollständig aus diesem PR-Scope gelöscht |
| 13a | Interactive Search UI Redesign & Source Filter | UI / UX | **Implemented** (§33) | [iss27-01](library-v2-issues.md#iss27-01) | Standard durchsucht alle konfigurierten Quellen parallel; Toggle-Redesign + Multi-Select-Quellen-Chips jetzt ebenfalls umgesetzt (§33) |
| 13b | Interactive Search Defekt-Fix | Bugfix | **Implemented** (§32) | [iss27-01](library-v2-issues.md#iss27-01) | Garantiert-leere Anfrage für unbetitelte Tracks behoben (Fallback auf Albumtitel) |
| 14 | Library Header Actions | UI / Feature | **Verified** (§37) | [F-13](library-v2-features.md#feat-search) | „Automatic Search“ kombiniert Missing Wishlist und Cutoff-Unmet-Upgrades ohne Start-Race; Re-Import bleibt erhalten |
| 15 | Referenz auf Basic Search | Dokumentation | Verified | [iss27-01](library-v2-issues.md#iss27-01) | Querverweis in Doku aufgenommen, Basic Search für Search-Overhaul als Vorbild zu nutzen |

### Verifikation

- `tests/library2`: **1.064 bestanden** (1.050 + 5 Cooldown-, 7 Trigger-,
  4 Fan-out-Tests);
- `tests/repair`, `tests/repair_jobs`: **120 bestanden** (108 + 8 T-11-,
  3 T-12-, 1 Registry-Test);
- `tests/imports`: unverändert grün (gemeinsamer Lauf mit den beiden obigen,
  Exit-Code 0);
- Ruff über alle geänderten Dateien: sauber.

Nicht Teil dieses Laufs: das Frontend. Diese Session hat kein `webui/`-File
angefasst; der letzte Stand ist der aus §29.

Neue Testdateien: `tests/library2/test_unmapped_trigger.py`,
`tests/repair_jobs/test_native_genre_and_comma_split.py` (inkl. eines
Ende-zu-Ende-Falls mit echten ffmpeg-FLACs),
`tests/repair_jobs/test_library_reorganize_identity.py`.

### Einstufung

Der §18-Auftrag ist abgearbeitet: kein registrierter Job trägt mehr das
Verdikt *legacy*, und die Finding-Typ-Matrix ist vollständig aufgenommen. Zwei
Punkte bleiben bewusst offen, beide auf ausdrückliche Nutzerentscheidung
(T-06 Genre-Beschaffung, §27 Teil 3 Track-Zeilen-Dedup). Nicht geprüft und
weiterhin Teil des §9-Gates: Failure-Injection pro Werkzeug (Restart im Apply,
read-only Root, Windows-/Docker-Pfad-Mapping) sowie ein realer Lauf des
Post-Import-Triggers gegen laufende Importe.

## 32. Umsetzung der §31-Bugfixes iss27-01/02/03/04/05 (27. Juli 2026)

Fortsetzung von §31: die fünf als „Bugfix"/„Feature / Fix" klassifizierten
Punkte aus der Übersichtstabelle (6b, 7, 9, 10, 13b, teilweise 13a) wurden
recherchiert und umgesetzt; die drei reinen UI/UX-Layout-Punkte (8, iss27-07,
iss27-08) sowie die Fanart.tv-Provider-Integration blieben bewusst
unangetastet — das sind Gestaltungs- bzw. neue-Feature-Entscheidungen, keine
Bugfixes, und die einleitende Regel in §20/§31 verlangt für solche Punkte
explizite Rückfragen an den Nutzer statt eigenmächtiger Designentscheidungen.

Details je Punkt stehen jetzt direkt unter der jeweiligen
`docs/library-v2-issues.md` §20-Unterüberschrift (iss27-01 bis iss27-05,
jeweils mit „Umsetzung"-Absatz). Kurzfassung:

- **iss27-04** (Navigation): `releases`-Suchparameter wird bei jeder
  Navigation auf einen neuen Artist zurückgesetzt (4 Callsites in
  `library-v2-page.tsx`).
- **iss27-01** (Interactive Search): Root Cause war NICHT der in der Doku
  vermutete Strukturunterschied zu Basic Search (beide treffen bereits
  denselben Endpunkt) — echte Bugs waren eine garantiert-leere Anfrage für
  unbetitelte Tracks und eine Einzel-Quellen-Suche ohne Fan-out. Beides
  behoben; das Checkbox/Toggle-Redesign bleibt offen.
- **iss27-02** (Tag Gaps): neuer Endpunkt
  `POST /api/library/v2/tracks/<id>/fill-tag-gaps` komponiert
  `enrich_native_entity_for_service` (Provider-Prioritätswalk) +
  `retag.write_tags` statt nur Letzteres — füllt jetzt Felder, die die
  Katalog-DB noch gar nicht hatte.
- **iss27-03** (Change Photo): Root Cause war ein fehlendes Zeitbudget im
  Provider-Fan-out (`pool.map()` blockierte auf den langsamsten Thread),
  nicht fehlende Fehlerisolation (die war schon da). Bounded
  `concurrent.futures.wait(timeout=10)`, MusicBrainz-Relations-Resolver
  jetzt tatsächlich verdrahtet, Frontend-Timeout auf 20s erhöht,
  manueller Refresh-Button gegen den 5-Minuten-Cache.
- **iss27-05** (Refresh & Scan): bereits vor dieser Session durch
  `0cd7167a6` behoben; nur verifiziert, keine Änderung nötig.

### Verifikation

- Frontend: `npx vitest run` — **278 von 278 Tests grün** (47 Dateien,
  inkl. 2 neuer Testdateien `build-search-query.test.ts`,
  `art-picker-modal.test.tsx`), `oxlint --type-check src` sauber (0 Fehler).
- Backend: `tests/library2`, `tests/metadata`, `tests/test_artist_image_picker.py`
  — alle grün (Exit-Code 0); `ruff check` über alle geänderten
  Python-Dateien sauber.
- Geänderte Dateien: `api/library_v2.py`, `core/metadata/artist_image.py`,
  `webui/src/routes/library-v2/-library-v2.api.ts`,
  `webui/src/routes/library-v2/-ui/{library-v2-page,interactive-search,art-picker-modal}.tsx`
  plus zugehörige Tests.

### Einstufung

Alle fünf als Bugfix/Fix klassifizierten §31-Punkte sind abgeschlossen (vier
umgesetzt, einer als bereits erledigt verifiziert). iss27-01s
UI-Redesign-Anteil (Punkt 13a) ist nur teilweise erledigt — die
funktionale Quellenauswahl (alle Quellen parallel) steht, das visuelle
Toggle-Redesign nicht. Offen und bewusst nicht angefasst: Punkt 8
(Column Settings Layout), iss27-07 (Preview-Re-Tag-Gliederung), iss27-08
(Maintenance-Umbenennung/-Gruppierung) sowie Punkt 6a/11a/11b (Hover-Popover,
Verification-Tag-Reader/-Spalte — 6a ist über das bestehende
`title`-Tooltip bereits funktional abgedeckt, siehe §20.2-Notiz in
`library-v2-issues.md`).

## 33. Interactive Search „bombenfest“: 0-Treffer-Bug, Quarantäne-Feedback, Quellen-Chips, Indexer-als-Artist (27. Juli 2026, Folgesitzung)

Der Nutzer meldete am selben Tag, direkt im Anschluss an §32, dass
Interactive Search für bestimmte Titel weiterhin 0 Treffer liefert, fragte
nach dem Quarantäne-Verhalten bei deaktivierten Checks (Quality/AcoustID),
und meldete einen Usenet-Indexer-Namen, der als Artist angezeigt wird.
Auftrag: Interactive Search vollständig fertigstellen (Fehler beheben +
verbleibende §20.1/§31-UI-Punkte 4/13a abschließen), danach dokumentieren
und committen.

Details je Punkt stehen unter der jeweiligen `docs/library-v2-issues.md`
§21-Unterüberschrift (iss27-09 bis iss27-11, plus §21.4 für den
iss27-01-Abschluss). Kurzfassung:

- **iss27-09** (0-Treffer-Bug): `buildSearchQuery`s Regex zum Entfernen des
  „(Album)“-Suffix kannte keine verschachtelten Klammern — ein Titel mit
  eigenem Klammer-Credit (z.B. „(feat. X)“) ließ die Regex komplett
  fehlschlagen, wodurch der gesamte, duplizierte Tail unverändert in die
  Suchanfrage floss. Fix: klammertiefen-bewusstes Parsing
  (`splitTrailingParenGroup`) statt Regex.
- **iss27-10** (Quarantäne-Feedback): der serverseitige Bypass für
  Quality-/AcoustID-Checks war bereits korrekt (`_should_skip_quarantine_check`
  in `core/imports/pipeline.py`) — keine Code-Änderung nötig. Die Lücke war
  fehlendes Feedback im Fenster selbst: ein Grab zeigte nur den
  Dispatch-Erfolg, nie den asynchronen Pipeline-Ausgang. Fix: Client pollt
  die bestehende Merged-History (`core/library2/history_feed.py`) und zeigt
  ein frisches Quarantäne-/Fehler-Event sofort inline an.
- **iss27-11** (Indexer als Artist): `usenet.py`/`torrent.py` fielen bei
  fehlendem „Artist - Title“-Trennzeichen im Release-Titel auf den
  Indexer-Namen als Artist-Platzhalter zurück. Fix: generischer Platzhalter
  `'Unknown Artist'` statt Indexer-Name.
- **iss27-01 Punkt 4/13a** (Toggle-Redesign & Quellen-Chips): Dropdown durch
  eine echte Multi-Select-Chip-Reihe ersetzt (`excludedSources`-Set statt
  Single-Value); die drei Checkboxen sind jetzt Slide-Toggles (rein
  CSS-visuell, `<input type="checkbox">` bleibt darunter unverändert).

### Verifikation

- Frontend: `npx vitest run src/routes/library-v2` — **186 von 186 Tests
  grün** (29 Dateien); `tsc --noEmit -p tsconfig.json` und
  `oxlint --type-check src` sauber (0 Fehler).
- Backend: `tests/test_torrent_usenet_plugins.py` — 51/51 grün.
- Geänderte Dateien: `webui/src/routes/library-v2/-ui/{library-v2-page,
  interactive-search}.tsx`, `webui/src/routes/library-v2/-ui/library-v2-page.module.css`,
  `webui/src/routes/library-v2/-ui/{build-search-query,interactive-search}.test.ts(x)`,
  `core/download_plugins/{usenet,torrent}.py`, `tests/test_torrent_usenet_plugins.py`.
- Nicht Teil dieser Session: eine Live-Verifikation im Browser gegen einen
  echten Soulseek/Usenet/Prowlarr-Stack (kein laufender `dev.py` in dieser
  Umgebung) — reine Unit-/Integrationstest-Abdeckung plus Typecheck/Lint.
  Empfohlen: kurzer manueller Test über `dev.py` vor dem nächsten
  Produktiv-Einsatz, insbesondere für das neue Quarantäne-Polling (History-
  Endpunkt-Timing) und die Chip-Interaktion.

### Einstufung

iss27-01 (§20.1/§31 Punkt 13a) ist jetzt vollständig — funktional UND
visuell — abgeschlossen. Drei zusätzliche, unabhängig gefundene Probleme
(iss27-09 Query-Bug, iss27-11 Indexer-als-Artist) sind behoben, plus eine
neue Feedback-Funktion für den Quarantäne-Fall (iss27-10). Verbleibende
§20/§31-Punkte (8, iss27-07, iss27-08) sind bewusst unangetastete
Design-Entscheidungen außerhalb des Scopes dieser Session.

**Nachtrag (§34): Der erste echte Live-Test dieser Session hat drei neue
Probleme aufgedeckt** (Usenet-Regression, kaputte Toggle-Optik,
Timeout-Frage) — die Aussage "keine bekannten offenen Funktionsblocker"
oben ist damit überholt, siehe §34.

## 34. Live-Test-Feedback zu §33: Usenet-Regression, kaputte Toggle-Optik, Timeout-Frage — Verified, 27. Juli 2026

Direkt nach dem §33-Push hat der Nutzer live im Browser getestet (statt
wie in §33 dokumentiert nur per Unit-/Integrationstests) und drei konkrete
Probleme gemeldet. **Alle drei sind inzwischen behoben und
regressionsgeprüft.** Details unter `docs/library-v2-issues.md` §22
(iss27-12/13/14); Kurzfassung:

- **iss27-12 (Usenet-Regression):** Root Cause war die invertierte Chip-
  Semantik: im Defaultzustand wirkten „All sources" und jeder Einzelchip
  gedrückt, ein Klick auf „Usenet" fügte Usenet aber zum unsichtbaren
  `excludedSources`-Set hinzu. Die UI verwendet nun eine positive exakte
  Auswahl; ein Klick auf Usenet sucht Usenet. Search-Requests tragen
  zusätzlich die Library-v2-Entity-IDs zur Candidate-Bindung.
- **iss27-13 (Toggle-Optik):** Pseudo-Elemente direkt auf dem ersetzten
  Checkbox-Input waren browserabhängig. Der zugängliche Input ist jetzt
  visuell versteckt, Track und Knopf liegen auf einem Sibling-`span`.
  Echtes Chromium bestätigt 1×1 px geclippten Input, genau einen
  36×22-px-Track und einen 16-px-Knopf.
- **iss27-14 (Timeout-Verhalten):** Multi-Source-Suchen rendern jede
  erfolgreiche Quelle sofort; eine langsame Quelle hält schnelle Ergebnisse
  nicht mehr bis zu ihrem 90s-Timeout zurück. Ein Run-Sequence-Guard verhindert,
  dass eine alte Anfrage neuere Ergebnisse überschreibt.

### Einstufung

Die drei Regressionen sind mit 17 Interactive-Search-Komponententests,
Frontend-Type/Lint/Build und echtem Chromium abgedeckt. Ein echter
Prowlarr-/Usenet-End-to-End-Lauf bleibt trotzdem Teil des Release-Gates, weil
das lokale Testprofil keine Prowlarr-/Usenet-Zugangsdaten enthält.

## 35. Neu heruntergeladener Track eines gut gemappten Albums hat nur eine Metadaten-Quelle — Verified, 27. Juli 2026

Neues, unabhängiges Szenario vom Nutzer (nicht Interactive-Search-UI,
sondern Metadaten-Vollständigkeit nach einem Download): Album + Artist
sind bei fast allen Quellen gemappt, ein einzelner fehlender Track wird
per Automatic/Interactive Search nachgeladen — danach hat aber genau
dieser Track nur EINE Metadaten-Quelle hinterlegt, nicht die vom Album/
Artist bekannten vielen. Zusätzlich muss aktuell manuell „Refresh & Scan"
ausgelöst werden, damit die neue Datei überhaupt erkannt wird. Details
und Abschlussdiagnose unter `docs/library-v2-issues.md` §23.

**Bestätigte Root Cause und Korrektur:**

- `provider_adapters.fetch_album_tracklist()` beendet die Suche bewusst nach
  der ersten erfolgreichen Trackliste. `_persist_tracklist_tracks()` konnte
  daher pro Track nur die IDs dieses einen Providers erhalten, obwohl das
  Album mehrere bestätigte Release-IDs besaß. Ein höherwertiger Track-
  Reconcile existierte nicht.
- `fetch_matched_album_tracklists()` fragt nun alle **explizit bestätigten**
  Album-Provider-IDs ohne Namensfallback ab. Der neue
  `track_identity_reconcile` merged Track-IDs/ISRC/MBID nur bei vorhandener
  ID, Titel+Disc/Position oder beidseitig eindeutigem Titel; Konflikte werden
  gezählt und niemals überschrieben.
- Der Post-Import-Trigger arbeitet albumweise und entprellt (Default 5s), so
  dass ein 30-Track-Import nicht 30 Provider-Runden startet. Normaler Import
  und Post-Move-Recovery verdrahten denselben Hook.
- Die Datei war in der DB bereits direkt nach Autolink sichtbar. Das
  zusätzliche „Refresh & Scan"-Symptom war ein React-Query-Cacheproblem:
  Imported-History sowie Queue aktiv→leer invalidieren nun die Library-v2-
  Abfragen automatisch.

### Einstufung

Verified durch Provider-/Reconcile-/Trigger-Regressionen, 49 Importtests,
die vollständige Library-v2-Suite (1.075 Tests) sowie den §35-Frontendtest.

## 36. Abschlussprüfung und unabhängiger Python-3.14-Async-Deadlock — Verified, 27. Juli 2026

Bei der breiteren Search-/Candidate-Prüfung hing sowohl ein Torrent-Cleanup
als auch `run_async(asyncio.sleep(0))`. Root Cause in
`utils/async_helpers.py`: der gemeinsame Selector-Loop wurde in einem Thread
erzeugt und in einem anderen betrieben; unter Python 3.14.6 konnte
`run_coroutine_threadsafe()` den Loop dann in längeren Prozessen nicht
zuverlässig aufwecken.

Der Loop wird nun im Besitzer-Thread erzeugt. Eine threadsichere Jobqueue
übergibt Coroutines an einen Loop-Pump, der alle wartenden Jobs als getrennte
Tasks startet; die frühere Parallelität bleibt damit erhalten, ohne vom
fehlerhaften Cross-Thread-Selector-Wakeup abzuhängen. Laufende Tasks werden
bis zum Abschluss stark referenziert, damit ein GC-Zyklus keinen wartenden
Aufrufer strandet.

Zusätzlich wurden order-abhängige Library-v2-Tests repariert: Autolink- und
Discography-Tests starten keine fachfremden Artwork-Provider-Futures mehr,
Parser-Assertions verwenden die zentrale Version, und Session-Teardown
beendet verbliebene Background-Pools.

Verifikation:

- `tests/library2`: **1.075 passed**, Prozess beendet sauber;
- Frontend: **292 passed** in 47 Dateien; Formatter/Type/Lint grün
  (zwei bekannte Warnungen außerhalb Library v2), Production Build grün;
- Async Bridge **3 passed**, Candidate Store **15 passed**,
  Torrent/Usenet **51 passed**, Scoped/Manual Search **11 passed**;
- Import Side Effects/Pipeline **49 passed**;
- echtes Chromium: Toggle-Input 1×1/geclippt, Track 36×22, Knopf 16×16;
- `compileall` und `git diff --check` grün.

Nicht als erledigt ausgegeben: echter Prowlarr/SABnzbd-/NZBGet-Live-E2E,
Restart-/Docker-/Windows-Mapping-Gates sowie die bewusst offenen
Designpunkte aus F-13/F-15/UI-03/UI-05.

## 37. Abschluss der F-13/F-15/UI-03/UI-05-Designpunkte und Webclient-Härtung — Verified, 27. Juli 2026

Die vier am Ende von §36 noch offenen Designbereiche wurden gegen Guide,
Features, Issues und den realen Codefluss geprüft und umgesetzt.

### F-13 und UI-05: globales Automatic Search und Repair-UX

- Der Library-Header bietet jetzt `Automatic Search`. Der Client wartet
  zunächst auf den bestehenden `quality_upgrade_scan`-Job und startet erst
  danach die vorhandene Wishlist-Verarbeitung. Damit sind Cutoff-Upgrades vor
  Beginn des gemeinsamen Missing-/Upgrade-Laufs gespiegelt; die umgekehrte
  Reihenfolge hätte ein Race mit dem bereits laufenden Wishlist-Zyklus
  erzeugt.
- Beide vorhandenen Wishlist-Antwortformen werden verstanden: das ältere
  Top-Level-`message` und das öffentliche API-Envelope `data.message`.
- „Maintenance“ heißt nun „Library Health & Repair“. Catalog-/Monitoring-,
  Artist-Datei-/Tag- und globale Scan-Werkzeuge sind visuell getrennt,
  verständlich benannt und tragen einen expliziten Scope.
- Der bereits in §32 verifizierte Navigation-State-Reset bleibt unverändert
  Teil von UI-05.

### UI-03: Track-Dateigröße, persistente Breiten und kompakte Optionen

- `track.file.size` erscheint als opt-in `File size`-Spalte, formatiert und
  numerisch sortierbar. Da Album-, EP- und Single-Details dieselbe
  `AlbumTrackTable` verwenden, gilt die Spalte für alle Release-Typen.
- Alle fachlichen Track-Spalten inklusive `#` und `Title` besitzen
  Pointer-Capture-Resizing, einen Clamp von 48 bis 640 CSS-Pixeln,
  Tastatursteuerung, Doppelklick-Reset und DB-persistierte Breiten.
- Alte gespeicherte `column_order`-Listen werden mit neuen Defaults gemerged.
  Dadurch bleiben neu eingeführte Spalten auffindbar, statt bei bestehenden
  Installationen dauerhaft aus dem Optionsmenü zu verschwinden. Derselbe Fix
  schließt die entsprechende Lücke der Artist-`size`-Spalte.
- Das Optionsmenü ist ein responsives Mehrspalten-Layout für sichtbare
  Spalten, Quality/Größen und Match-Provider. Ein gemeinsamer Reset entfernt
  gesetzte Breiten.
- Die bereits vorhandene Verification-Spalte und der kanonische
  `SOULSYNC_VERIFICATION`-Reader wurden erneut durch die Vollsuite abgedeckt.

### F-15: Preview Re-Tag und Tags-Breakdown

- Die Preview gruppierte vorher nur **benachbarte Zeilen gleichen
  Albumtitels**. Interleavte Rows oder zwei verschiedene Releases mit
  identischem Titel wurden daher falsch geteilt bzw. zusammengeführt. Die
  API liefert nun zusätzlich `album_type`; die UI gruppiert stabil per
  `album_id` und zeigt Album/EP/Single, visuelle Grenzen sowie
  „N of M changing“.
- `tags ✓` und `N tag gaps` verwenden statt eines nativen mehrzeiligen
  `title`-Strings ein portalfähiges Tooltip. Hover und Keyboard-Fokus zeigen
  explizit vorhandene und fehlende Tags sowie die jeweilige Klickwirkung.

### Zwei zusätzlich gefundene Webclient-Fehler

1. Der zentrale HTTP-Fehlerparser verstand `error: "Text"`, nicht aber das
   von 141 öffentlichen API-Callsites verwendete Standardformat
   `error: {code, message}`. Fehler wie „Wishlist processing is already
   running“ wurden deshalb durch einen generischen HTTP-Status ersetzt.
   `readJson` extrahiert nun auch `error.message`.
2. Artist-/Label-Namen in Search-Parametern müssen Zahlen wie `311` weiterhin
   zu Strings normalisieren. Beliebige Objekte wurden dabei jedoch zu
   `[object Object]`. Die Coercion akzeptiert jetzt nur String, Number und
   Boolean; strukturierte Werte fallen sicher auf den leeren Namen zurück.

### Verifikation

- `tests/library2`: **1.078 passed**, 1 bekannte `sqlite3`-
  Deprecation-Warnung;
- WebUI: **301 passed** in 50 Dateien;
- neue/erweiterte Regressionen für File-Size-Sortierung und -Resizing,
  Preference-Migration/-Persistenz, Retag-Release-Gruppierung, Tags-Tooltip,
  Automatic-Search-Reihenfolge, Maintenance-Scope, verschachtelte
  API-Fehler sowie strukturierte Route-Parameter;
- `npm run check`: **0 Warnungen, 0 Fehler**;
- Vite Production Build, Docker-Frontend-Stage, vollständiger Docker-Image-
  Build, Ruff, `compileall` und `git diff --check`: grün.

Die absichtlichen Nicht-Features und externen Release-Gates ändern sich
dadurch nicht: T-06 (Genre-Lücke), Artwork-Negativcache,
Track-Duplikat-Produktentscheidung, Live-Prowlarr/Download-Clients sowie
Restart- und Windows-/Docker-Path-Mapping-Runtime-Gates bleiben bei ihrem
zuvor dokumentierten Stand.

## 38. Vertiefter Abschluss-Audit und Python-3.14-Runtime-Härtung — Verified, 27. Juli 2026

Ein weiterer statischer und dynamischer Audit hat die bestehenden
Library-v2-Verträge an mehreren Systemgrenzen abgesichert:

- Track-Versionen verwenden nun dieselbe Qualifier-Erkennung für Klammer-
  und Dash-Schreibweisen. Das verhindert falsche Quarantäne bei realen
  Remix-/Edit-/Slowed-/Clean-/Explicit-Titeln, ohne normale Bindestrich-Titel
  zu beschädigen.
- Exakte Provider-ID-Lookups erkennen `allow_fallback` vor dem Aufruf über
  die Signatur. Interne Provider-`TypeError`s können keine zweite,
  unkontrollierte Fallbacksuche mehr auslösen.
- Die parallele Artist-Bildsuche respektiert deterministisch die
  konfigurierte Quellenpriorität, auch wenn eine Fallbackquelle schneller
  dieselbe URL liefert.
- Server-seitige Torrent-Downloads verwenden einen begrenzten gemeinsamen
  Worker-Pool, ohne den Default-Executor des Besitzer-Loops anzulegen. Damit
  beendet Python 3.14.6 den längeren Testprozess sauber.
- Wishlist-Retry-Backoff versteht die kanonische
  `track_id::album_id`-Identität und bewahrt die Abwärtskompatibilität alter
  bare Track-IDs.
- Native Findings enthalten durchgehend navigierbare Artist-IDs; der
  qBittorrent-Adapter besitzt nur noch eine getestete Share-Limit-
  Implementierung. Ruff-Funde zu Closure-Capture, nicht-striktem `zip()` und
  stummen Exceptions wurden ebenfalls beseitigt.

Verifikation:

- Library-v2: **1.078 passed**;
- Backend-Komplettlauf vor den letzten zwei isolierten Testhärtungen:
  **12.285 passed, 3 skipped, 2 deselected, 2 failed** in rund zehn Minuten;
- beide verbliebenen Fehler danach gezielt behoben und verifiziert:
  Wishlist **51 passed**, Async-/Candidate-/Torrent-Scope **79 passed**;
- weitere betroffene Scopes: Titelmatching **31 passed**,
  Provider/Monitor **40 passed**, Adapter/Wishlist/Expiry **68 passed**,
  Repair **19 passed**, native Findings **78 passed**;
- WebUI: **301 passed** in 50 Dateien; `npm run check` und Production Build
  grün;
- Ruff grün; abschließende schnelle Syntax-/Diff-Prüfungen grün.

Der redundante zehnminütige Backend-Komplettlauf wurde nach den zwei
zielgenauen Fixes auf Benutzerwunsch nicht erneut gestartet. Die bekannten
nicht-blockierenden Warnungen bleiben die `sqlite3`-Datetime-Deprecation und
der bestehende Vite-Chunkgrößenhinweis. Die absichtlichen Nicht-Features und
externen Release-Gates aus §37 bleiben unverändert offen.

## 39. Reale UI-Regressionsbefunde vom 28. Juli 2026

Die Root-Cause- und Abnahmeverträge stehen in
[issues.md §26](library-v2-issues.md#26-library-v2-live-ui-findings-vom-28-juli-2026).
Diese Tabelle hält den Implementierungsstand nach der Korrekturrunde fest.

| ID | Finding | Status |
|---|---|---|
| [iss28-01](library-v2-issues.md#iss28-01) | Generische Check-Spalte mit Verified/Human verified/Skipped/Not scanned statt versteckter AcoustID-Anzeige | Implemented |
| [iss28-02](library-v2-issues.md#iss28-02) | Relative benachbarte Spaltenbreiten; niemals horizontaler Scroll | Implemented |
| [iss28-03](library-v2-issues.md#iss28-03) | Globale Startseitenaktion als `Automatic Search`, gleich gestaltet wie der Re-Import-Nachbarbutton plus Icon | Implemented |
| [iss28-04](library-v2-issues.md#iss28-04) | Neutrales graues Größen-Badge mit Symbol in jeder Album-/EP-/Single-Zeile | Implemented |
| [iss28-05](library-v2-issues.md#iss28-05) | Viewport-Dialog für Spalten/Quality/Match-Provider, vertikal statt horizontal scrollend | Implemented |
| [iss28-06](library-v2-issues.md#iss28-06) | Zentrierte feste Quality-Unterbereiche trotz separater AcoustID-/Verification-Spalten | Implemented |
| [iss28-07](library-v2-issues.md#iss28-07) | Kompakte Track-Actions-Spalte ohne geerbte 170-px-/11-%-Überbreite | Implemented |

Umgesetzt wurden eine standardmäßig sichtbare `acoustid`-Preference samt
generischer Check-Statuszelle, normalisierte relative Spaltengewichte mit
Nachbarverteilung und Legacy-Pixelmigration, ein festes 100-%-`colgroup`,
viewportgebundene Settings-Portals, das neutrale Release-Größenbadge sowie
die wiederhergestellten zentrierten Quality-Bereiche 140/80/110 px. Die
Actions-Spalte belegt nur noch 7 % statt 11 %, ihr 170-px-Erbe ist
neutralisiert und die Datenspalten teilen sich 86 %. `#` und `Track Title`
bleiben immer sichtbar.

**Prüfung:** WebUI Library v2 **199 passed** in 32 Dateien; die gezielte
Album-/Track-/Header-/Refresh-Runde **12 passed**; `npm run check` ohne Warnungen oder
Fehler; Production Build grün. Preferences **12 passed**; der nach der
Preference-Erweiterung gelaufene vollständige Backend-Scope
`tests/library2` **1079 passed** (eine bekannte
`sqlite3`-Datetime-Deprecation). Die laufende lokale App antwortete unter
Port 8008 mit HTTP 200. Der Playwright-Live-Lauf konnte nicht starten, weil
weder Playwright-Chromium noch ein System-Chromium installiert ist; dadurch
entstand kein Produkt-Testfehler, aber der manuelle visuelle Browser-Gate
bleibt offen.

**Explizit verworfen:** `#` und `Track Title` werden nicht ein-/ausblendbar
gemacht. Diese zwischenzeitlich ausgesprochene Idee wurde im selben
Nutzerfeedback zurückgenommen und ist kein offenes Feature.

## 40. Anschließende Bereinigung weiterer offener Statuspunkte

- [T-11](library-v2-issues.md#tool26-11) war in §29 noch fälschlich Pending,
  obwohl §30 die nativen `genre_cleanup`-/`comma_artist_splitter`-
  Implementierungen samt Tests bereits belegt. Dokumentkopf und §29 zeigen
  jetzt konsistent auf den abgeschlossenen Deep-Dive.
- [T-08](library-v2-issues.md#tool26-08) ist geschlossen: `Refresh & Scan`
  bleibt bewusst der Datei-Pass. Der Button erklärt nun im Tooltip konkret
  „existence, audio quality and embedded tags" und grenzt Provider-Metadaten
  ausdrücklich ab; der Artist-Refresh-Test pinnt diesen Vertrag.
