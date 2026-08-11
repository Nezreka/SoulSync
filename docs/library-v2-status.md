# Library V2 — zentraler Status-, Commit- und Verifikations-Tracker

Diese Datei ist der **einzige** Ort für Fortschritt, „offen/erledigt“,
Commit-Referenzen, Teststände und Release-Einschätzung. Guide, Features und
Issues beschreiben ausschließlich Zweck, gewünschtes Verhalten und technische
Diagnosen.

Stand: 4. August 2026. Letzter Arbeitscheckpoint: Remediation und Übergabe in §49
([Issues §30](library-v2-issues.md#30-finaler-multi-agent-audit-des-branch-heads-4-august-2026)).
Der Audit aus §48 wurde in vier geprüften Commits remediated. Offen bleiben die
Provider-Propagation aus §49.4, das Cross-Process-TOCTOU-Risiko aus Issues §31
und der finale Full-Suite-/Live-Browser-Gate. Bei Widersprüchen gilt §49.

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
| [F-01](library-v2-features.md#feat-artwork) | Media-server-unabhängiges Artwork | Verified | Deep-Dive §28, Security-Fix `80b5af95`, §24, §42 | Picker, Embed, Cache-Bust und Fetch-Hardening gezielt geprüft; Kaltstart-Nachlieferung seit §24 serverseitig getrieben; seit §42 (ldp-07) überbrückt die Provider-CDN-URL den kalten Build und reine Discography-Zeilen werden bewusst nicht lokal gecacht |
| [F-02](library-v2-features.md#feat-monitoring) | Monitoring, Watchlist/Wishlist, Outbox | Verified | P3/§82, Regression-Checkpoint | Bidirektionale Sync-, Reconcile- und Profilgrenzen geprüft |
| [F-03](library-v2-features.md#feat-quality) | App-weite Quality Profiles und Vererbung | Implemented | §53/§60, §14, §15 | Track→Album→Artist→Global verified; Watchlist-Monitor-Mirroring inkl. `quality_profile_id`-Weitergabe an natives Watchlist jetzt verdrahtet (§15) |
| [F-04](library-v2-features.md#feat-discography) | Discography, Tracklists, `monitor_new_items` | Verified | `2249f5d7`, `8f965d31` (später gesquasht), §42 | Content-Filter und nie manuell expandierte Artists abgedeckt; seit §42 zusätzlich der rein lesende Discovery-Modus (ldp-02), der Table-↔-Legacy-Umschalter und die portierte Filterleiste |
| [F-05](library-v2-features.md#feat-bootstrap) | Automatischer Initialimport | Verified | Review 4/5, `c2d99eda`, `e9730afe` | Bounded Transactions und Streaming; Owner-/Fresh-Install-Fixes im Regression-Checkpoint |
| [F-06](library-v2-features.md#feat-alias) | Artist Alias Registry und Scope | Verified | `ce7b4516`, `a95e5309` | Listen, Suche, Totals und artist-weite Actions gezielt geprüft |
| [F-07](library-v2-features.md#feat-duplicate) | Artist-/Album-/Edition-Dedup | Implemented | §62/§63, P3, §27 | Album-Twin-Pass läuft seit §27 für jeden Artist, nicht nur für Merge-Survivor; Dry Run gegen die Produktiv-DB gelaufen. Rest: Track-Zeilen-Duplikate (§27 Teil 3) brauchen eine Produktentscheidung |
| [F-08](library-v2-features.md#feat-unmapped) | V2-native/Collaboration Artists | Implemented | §68, Regression M-11, §28 | Enrich/Smart-Split und globale Suche abgedeckt; Reconcile-Job bleibt namensbasiert ohne Strong-ID-Cross-Check und ohne Cooldown, siehe §28 |
| [F-09](library-v2-features.md#feat-playlists) | Library-v2-Playlist-Oberfläche | Deferred | `library-v2-playlist-ui` | Vollständig aus dem aktiven Overhaul entfernt und separat geparkt |
| [F-10](library-v2-features.md#feat-history) | Korrelierte Pipeline-History | Implemented | §35/§37/§57/§58, §17, §23, §49 | Feed, File-Ergebnis, Albumzweig und Korrelation vorhanden; Auto-Import terminalisiert nun wahrheitsgemäß als completed/partial/failed |
| [F-11](library-v2-features.md#feat-playback) | Track Playback / Preview | Implemented | §36, Regression H-14 | Bestehender Player reused; typisierte ID-Korrektur im Regression-Checkpoint |
| [F-12](library-v2-features.md#feat-acq-review) | Acquisition Review / Bundle Assignment UI | Verified removed | §49 | Deep-Link, Loader, UI und mutierende Client-Helfer entfernt; Routentest verhindert Wiederkehr |
| [F-13](library-v2-features.md#feat-search) | Scoped Search, Manual Grab, Acquisition | Implemented | §49, Issues §31 | Automatic Upgrades, Track-vs.-Album-Grenze, 409-Attach und servergebundene Candidate-Kinds geprüft; vollständige Provider-Propagation bleibt Restpunkt |
| [F-14](library-v2-features.md#feat-files) | Manage Files, Delete, Reorganize, Replacement | Implemented | §49, Issues §31 | Atomare Dateioperationen, Transform-before-compare, per-Track-Lock und CAS schützen die Primary; Cross-Process-Minifenster ist dokumentiert |
| [F-15](library-v2-features.md#feat-metadata) | Refresh, Retag, Metadata, RG/Lyrics | Implemented | §49 | Meta Gap Filler rotiert in begrenzten 500er-Seiten; Toolfehler besitzen sichtbare Retry-Zustände |
| [F-16](library-v2-features.md#feat-wanted) | Wanted Views, Entity Queue, Diskspace | Verified | §72–§74, `2e227c1b`, §39 | Rollups plus neutrales Größen-Badge mit Symbol in jeder eingeklappten Album-/EP-/Single-Zeile, einschließlich `0 B` |

### UI-Status

| ID | Bereich | Status | Hinweis |
|---|---|---|---|
| [UI-01](library-v2-features.md#ui-icons) | Icons/Nomenklatur | Verified | Automatic=Lupe, Interactive=User, Quality=Stern, Track=Pencil |
| [UI-03](library-v2-features.md#ui-columns) | Table Options / Spalten | Implemented | §39: separate generische Check-Spalte (Verified/Human verified/Skipped/Not scanned); normalisierte benachbarte Relativbreiten; feste zentrierte Quality-Unterbereiche; viewport-gebundener Einstellungsdialog; gezielte Tests und Build grün, manueller Browser-Gate mangels installiertem Chromium ausstehend |
| [UI-04](library-v2-features.md#ui-bulk) | Multi-Select/Bulk Bar | Verified | §49: Teilerfolge werden vollständig abgewartet, invalidiert und als Failed-Subset retrybar gehalten |
| [UI-05](library-v2-features.md#ui-actions) | Actions, Nav & Maintenance | Verified | §49: Mutationen fail-closed; Read-only-History bleibt verfügbar; Fehler sind sichtbar und retrybar |
| [UI-09](library-v2-features.md#ui-artist-header) | Artist-Kopf kompakt ↔ legacy-reich | Implemented | §42: Umschalter am Kopf, aus der Suche reich vorbelegt; Listeners/Plays/Bio namensbasiert, Top-Track-Aktion heißt `Bookmark` (ldp-05/ldp-06); Metadaten-Quellen-Panel bewusst nicht portiert (ldp-08) |
| F-12 UI | Acquisition Review | Verified removed | Alter Section-Wert normalisiert auf Artists und löst keinen Acquisition-Request aus |


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
| [15](library-v2-issues.md#find22-15) | Ein Queue-Poll pro Artist | Verified | `2e227c1b`, §46 | spezifisch; der Vertrag war zwischenzeitlich verletzt und sein Guard-Test vakuum (iss29-C06) — beides in §46 behoben, der Test prüft jetzt die tatsächliche Schreibweise |
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

---

## 41. Multi-Agent Deep-Dive vor dem PR-Entwurf — Status

Vollständige Diagnosen unter
[issues.md §27](library-v2-issues.md#27-finaler-multi-agent-deep-dive-vor-dem-pr-entwurf-28-juli-2026).
Sechs parallele Read-only-Agenten haben je eine Domäne durchleuchtet
(Repair-Werkzeuge, Interactive Search, Artwork, Import/Tagging,
Monitoring/Wanted/Wishlist, Frontend-Async/Download-Client-Adoption) und
insgesamt 50 Funde (2 kritisch, 18 hoch, 26 mittel, 4 niedrig) plus mehrere
gezielt geprüfte, für korrekt befundene Bereiche gemeldet.

**Status: alle 50 Funde sind gegen den aktuellen Code nachgeprüft und
behoben** (28. Juli 2026, fünf Commits entlang der Domänengrenzen). Jeder
Fund wurde vor dem Fix am realen Code verifiziert — keiner erwies sich als
Fehlalarm. Details je Domäne in §41.1–§41.6.

Verifikation nach Guide §6: Regressionstests liegen in
`tests/library2/test_artwork_contention.py`,
`tests/test_prowlarr_search_hardening.py`,
`tests/library2/test_multi_file_convergence.py`,
`tests/library2/test_monitoring_projection_findings.py`,
`tests/acquisition/test_client_boundary_findings.py`,
`tests/repair_jobs/test_section27_repair_findings.py`,
plus Ergänzungen in `tests/imports/test_quarantine_management.py`,
`tests/library2/test_mirror_outbox.py`, `tests/test_atomic_audio_save.py`
und `webui/src/routes/library-v2/-ui/build-search-query.test.ts`.
Die zentralen Fixes (dd28-04, dd28-11) wurden zusätzlich durch temporäres
Zurückdrehen der Änderung gegengeprüft: die Tests schlagen ohne den Fix
nachweislich fehl.

Vier bestehende Tests pinnten alte, falsche Semantik und wurden mit
Begründung umgeschrieben statt „grün gehalten“ (Guide §6 Punkt 6):

- `interactive-search.test.tsx` akzeptierte einen lautlosen Quellenfehlschlag
  (dd28-06) — verlangt jetzt den Hinweis.
- `test_monitor_sync.py` pinnte das Wiederabspielen einer überholten
  Outbox-Operation (dd28-13) — verlangt jetzt das Überspringen.
- `test_mirror_outbox.py::test_artist_watchlist_ops` verließ sich auf dasselbe
  Replay-Verhalten und drainiert jetzt zwischen Add und Remove.
- `test_lib2_upgrade_scan.py` setzte nur die denormalisierte
  `quality_profile_id` ohne `quality_profile_explicit` und erwartete, dass die
  Kaskade sie trotzdem übernimmt (dd28-11) — genau der Zustand, den Guide §2.3
  als nicht autoritativ bezeichnet.

Zwei Funde wurden bewusst enger gefasst als der Agent vorschlug:

- **dd28-51:** Der Vorschlag, `discography_synced_at` als Cutoff zu *ersetzen*,
  hätte jedes seit dem letzten Lauf erschienene Release aus einem längeren
  Sync-Loch verworfen (der vorhandene Regressionstest belegt das). Der Stamp
  ist deshalb ein *zusätzlicher* Zulassungspfad, nie eine Verschärfung.
- **dd28-44:** Die `submission_unknown`-Ausnahme in
  `fail_stale_local_submissions` bleibt absichtlich bestehen — der Client kann
  den Job angenommen haben. Die eigentliche Ursache (History flutet die
  Adoptionskandidaten) ist mit dd28-15 behoben; die 24-h-`evidence_ttl_expired`
  bleibt als Auffanglinie.

Die zwei vom Nutzer selbst live beobachteten Symptome sind identifiziert:
Artist-Foto-Wechsel-API-Fehler ist dd28-01 (Timeout-Mismatch zwischen
10-s-Frontend-Default und einem potenziell langsameren Backend-Pfad ohne
eigenes Limit), lange/verklammerte Titel scheitern bei Usenet aus einer
Kombination von dd28-02 (hartes 15-s-Prowlarr-Timeout, lautlos als
0-Treffer gemeldet) und dd28-07 (keine Query-Längen-/Klammer-Normalisierung
auf dem Prowlarr-Pfad, im Gegensatz zu Tidals bereits vorhandener
Retry-Leiter).

Ein Fund relativierte einen zuvor als geschlossen geführten Stand: dd28-27
fand in `fake_lossless_detector` dieselbe Identitäts-Bugklasse wie das
laut [§19.2](library-v2-issues.md#tool26-12) geschlossene T-12 (nackte
numerische ID statt `lib2:`-Präfix, dort aber unter `entity_type='file'`
statt `'album'`/`'track'`). Bestätigt und behoben: das Finding trug die
Track-ID unter `entity_type='file'`, und `_resolve_links` löste sie gegen
`lib2_track_files` auf. Eine Nachprüfung aller übrigen
`entity_type='file'`-Findings (`orphan_file_detector`,
`track_number_repair` an zwei Stellen) ergab überall `entity_id=None` — T-12
selbst bleibt also geschlossen, `fake_lossless_detector` war die einzige
weitere Fundstelle.

### 41.1 Domäne C — Artwork (dd28-01/03/04/22/23/24/25/26)

`600f1b7c8`. Der vom Nutzer gemeldete „manchmal API-Fehler beim
Foto-Wechsel" hatte vier zusammenwirkende Ursachen, deshalb der gemeinsame
Block (issues.md §27.6 Punkt 2):

- **dd28-01:** Die Apply-POSTs hatten kein eigenes Timeout; kys 10-s-Default
  brach ab, während der Server fertig lief. Jetzt 45 s, passend zum realen
  Aufwand (bis zu 5 revalidierte Redirect-Hops à (3.05, 15) s, zwei
  `optimize=True`-Encodes, DB-Write, Build-Lock).
- **dd28-03:** `ensure_metadata_overrides_schema` führte bei *jedem* Aufruf
  DDL aus (inkl. `DROP TRIGGER`+`CREATE TRIGGER` pro Entity-Tabelle) und nahm
  dafür den SQLite-Schreiblock. Ein `sqlite_master`-Lesecheck kürzt den
  Normalfall ab; eine belegte DB antwortet 503-JSON statt HTML-500.
- **dd28-04:** `apply_manual_artwork` öffnete die Schreibtransaktion **vor**
  dem Warten auf `_build_lock`. Reihenfolge umgedreht; zusätzlich hat der
  Provider-Walk in `build_artwork` jetzt ein Budget
  (`PROVIDER_WALK_BUDGET_S`), damit die Lock-Haltedauer überhaupt eine obere
  Schranke hat.
- **dd28-22** ist damit miterledigt: Override und Cache-Datei liegen jetzt in
  derselben Lock-Sektion und können nicht mehr auseinanderlaufen.
- **dd28-23/24/25/26:** Invalidate im `catch`-Zweig plus ehrliche
  Timeout-Meldung, per-Writer-Temp-Dateien, gehärtete Album-Art-Route
  (ein bereits committeter Pick kann keinen 500 mehr erzeugen),
  `forget_artwork_versions` beim Entity-Delete.

### 41.2 Domäne B — Interactive Search (dd28-02/05/06/07/34/35/36/37/52)

`600f1b7c8`. Der zweite Nutzerbefund („Usenet wird gar nicht gecheckt",
lange/verklammerte Titel):

- **dd28-02/05:** Prowlarr bekam ein eigenes Suchbudget
  (`DEFAULT_SEARCH_TIMEOUT`, respektiert `download_source.source_search_timeout`)
  und wirft `ProwlarrSearchError` statt „0 Treffer" zurückzugeben. Beide
  Plugins reichen ihren `timeout` jetzt durch.
- **dd28-07/52:** Neue `core/download_plugins/query_variants.py` mit einer
  progressiven Query-Leiter und *balance-bewusstem* Klammer-Stripping. Tidals
  eigener Fallback nutzt dieselben Helfer, womit der iss27-09-Regexfehler bei
  echt verschachtelten Klammern verschwindet.
- **dd28-34:** Kategorie 3060 (Audio/Foreign) wird mitgesucht — nicht-lateinische
  Releases waren über Prowlarr strukturell unauffindbar. 3020/3030 bleiben
  bewusst draußen: anderes Medium, keine Schrift-/Regionfrage.
- **dd28-37:** `prowlarr.indexer_ids` wird pro Protokoll gefiltert. Eine
  Allowlist, die eine Quelle gar nicht bedienen kann, fällt auf „alle
  aktivierten Indexer" zurück statt auf „keine".
- **dd28-06/35/36:** Pro-Quelle-Fehlschläge werden gemeldet (Warnbanner neben
  den Ergebnissen, nicht statt ihrer); Album-Suche behält die eigene
  Klammergruppe des Albumtitels (`entity` unterscheidet Track- von
  Album-Scope); ein vollständig verklammerter Titel kollabiert nicht mehr auf
  eine leere Query.

### 41.3 Domäne D — Import/Autolink (dd28-08/09/10/38/39/40/49/50)

`1f2c4aa76`. Katalogintegrität; laut issues.md §27.6 Punkt 3 vor jedem
weiteren Livetest zu beheben:

- **dd28-08:** Neue `retire_replaced_files` in `core/library2/track_files.py`.
  Die Pipeline meldet die von ihr gelöschten Pfade (`_replaced_file_paths`);
  zusätzlich werden verschwundene Dateien defensiv stillgelegt — aber nur bei
  gesundem Storage-Root (Guide §5).
- **dd28-09:** Der Album-Lookup spannt jetzt die Alias-Gruppe auf. Damit
  entfällt sowohl das Doppel-Album als auch die Download-Schleife, die es
  auslöste.
- **dd28-10:** Neue `attach_track_to_edition`; Autolink hängt neue Tracks
  sofort an eine Edition, statt sie dem Backfill zu überlassen.
- **dd28-38:** Neue `writable_file_rows`; Tags/ReplayGain/Lyrics erreichen
  alle Dateien eines Tracks. ReplayGain analysiert dabei jede Datei einzeln —
  eine FLAC und ihr MP3-Transkodat haben nicht dieselbe Lautheit.
- **dd28-39/40/49/50:** Explizites Fehl-Flag statt „assuming success";
  behaltenes verlustfreies Original wird als zweite Datei verlinkt;
  Quarantäne-Pfad über `docker_resolve_path`; kollisionsfreie Entry-Namen.

### 41.4 Domäne E — Monitoring/Wanted (dd28-11/12/13/41/42/43/51)

`702f7ec8c`. Vor dem Release-Gate zu beheben, weil sie bestehende
Testannahmen über Quality Profiles widerlegen (issues.md §27.6 Punkt 4) —
was sich bestätigt hat, siehe die Testkorrekturen oben.

- **dd28-11/42:** Upgrade-Scan folgt `wt.effective_profile_id`; die
  Profil-Zuweisung reprojiziert auch ohne `monitor_existing`.
- **dd28-12:** Neue Vorwärtskante `sync_wishlist_addition` —
  bewusst oberhalb der DB-Schicht, damit die Mirror-Outbox sich nicht selbst
  füttert.
- **dd28-13:** Outbox-Zeilen, die eine neuere Operation derselben Entität
  bereits überholt hat, werden als `superseded` markiert statt abgespielt.
- **dd28-41:** Ein monitored Track ohne Erwerbsbedarf wird aus der Wishlist
  zurückgezogen — aber nur, wenn dort wirklich eine Zeile liegt.
- **dd28-43/51:** `monitor_new_items='new'` verlangt ein vollständiges Datum;
  der Sync-Stamp ist ein zusätzlicher Zulassungspfad (siehe Vorbehalt oben).

### 41.5 Domäne F — Client-Adoption und Frontend-Async (dd28-14…17, 44…47)

`a064252ad`. Am schwersten zu reproduzieren (issues.md §27.6 Punkt 5),
deshalb mit gezieltem Mocking statt echtem SAB/NZBGet-Stack getestet:

- **dd28-14:** Cancel bestätigt nur noch, was der Client bestätigt hat, holt
  die Job-ID notfalls aus dem persistierten Grab und bleibt sonst
  `cancel_pending`.
- **dd28-15/44:** Terminale (History-)Jobs sind keine Adoptionskandidaten
  mehr. Das behebt zugleich den nie greifenden
  `unique_category_job`-Fallback.
- **dd28-17/47:** `run_async` kennt ein optionales Timeout (Default weiter
  unbegrenzt, weil legitime Langläufer denselben Weg nehmen); der Monitor
  nutzt es überall, wo er `_cycle_lock` hält. Der Loop-Pump wird referenziert
  und überwacht.
- **dd28-16/45/46:** Ein gemeinsamer `useScopedSearchBanner`-Hook mit
  Run-Sequence-Guard und In-Flight-Sperre; geordnete
  UI-Preferences-Antworten; Tag-Edit invalidiert auch die Album-Query.

### 41.6 Domäne A — Repair-Werkzeuge (dd28-18…20, 27…33)

`4f2e14872`. Vor der nächsten Produktiv-DB-Verifikation zu beheben
(issues.md §27.6 Punkt 6). Der schwerwiegendste Fund ist dd28-18: bei
Multi-Edition-Releasegruppen schrieb der Tracknummer-Fix deterministisch
falsche Werte in *jede* Datei und benannte sie bei `dry_run: False` auch um.
Neu ist die Regel, dass eine nicht eindeutig bestimmbare Edition **kein**
Finding erzeugt — eine plausibel aussehende falsche Nummer ist schlimmer als
gar keine Meldung.

## 42. Legacy-Artist-/Discovery-Parität (ldp-01…ldp-09) — Implemented, 28. Juli 2026

Auftrag, Ist-Analyse und Fix-Verträge in
[issues.md §28](library-v2-issues.md#28-legacy-artist-discovery-ansicht-nach-library-v2-überführen-auftrag-vom-28-juli-2026-abend);
Zielverhalten in [features F-01](library-v2-features.md#feat-artwork),
[F-04](library-v2-features.md#feat-discography) und
[UI-09](library-v2-features.md#ui-artist-header). Diese Tabelle enthält
ausschließlich den Bearbeitungsstatus.

Die drei offenen Zuschnittsfragen aus issues.md §28.6 wurden vor
Umsetzungsbeginn gestellt und beantwortet (rein lesender Discovery-Modus,
lokaler Artwork-Cache nur für besessene/monitored Entitäten, Umschalter am
Artist-Kopf).

| ID | Finding | Status | Umsetzung |
|---|---|---|---|
| [ldp-01](library-v2-issues.md#ldp-01) | Suchtreffer landet in der Legacy-Library | Implemented (zurückgenommen §44.2, **wiederhergestellt §46**) | `/artist-detail/$source/$id` leitet nach `/library?discover=<source>:<id>` um. Bewusst in der Route, nicht in `search.js`: alle sechs Aufrufer bauen dieselbe URL über `buildArtistDetailPath` (issues.md §28.7). Beim Upstream-Sync entfernt, weil Upstream dieselbe Route mit einer eigenen React-Seite belegt hatte; auf Nutzerentscheidung wiederhergestellt (§46.1) — Upstreams Artist-Seite ist damit bewusst unerreichbar |
| [ldp-02](library-v2-issues.md#ldp-02) | V2 kann keinen Artist ohne Katalogzeile darstellen | Implemented | Discovery-Ansicht aus dem bestehenden `/api/artist-detail/<id>?source=&name=`; neuer `GET/POST /api/library/v2/discovery/artist` (Resolve read-only / Materialisieren) plus `POST …/discovery/track` für ldp-06. Resolve nutzt `find_or_create_artist(create=False)` — dieselbe Identitätslogik wie jeder andere Einstieg, ohne Write |
| [ldp-03](library-v2-issues.md#ldp-03) | `All Releases` braucht Table ↔ Legacy-Karten | Implemented | Zweite Umschaltgruppe, nur bei `All Releases`; Kachelgitter mit den Legacy-Klassen (`release-card album-card`, `album-card-image`, `completion-overlay`), also ohne eine Zeile neues CSS |
| [ldp-04](library-v2-issues.md#ldp-04) | Discography-Filterleiste fehlt | Implemented | `discography-filters.ts` — `classifyReleaseContent` 1:1 aus `library.js` portiert (#877 bleibt eine Quelle), Filterleiste mit den Legacy-Klassen, wirksam in Table- **und** Kachelansicht |
| [ldp-05](library-v2-issues.md#ldp-05) | Artist-Kopf ohne Listeners/Plays/Top Tracks | Implemented | Reicher Legacy-Kopf hinter einem Umschalter (Default kompakt, aus der Suche vorbelegt reich); neuer `GET /api/artist/lastfm-info?name=` liefert Listeners/Plays/Bio namensbasiert, weil V2-native Artists keine Legacy-Zeile haben; `provider_ids` neu in der Artist-Serialisierung für die Top-Tracks-Auflösung |
| [ldp-06](library-v2-issues.md#ldp-06) | Top-Tracks-Aktion heißt falsch | Implemented | Aktion heißt `Bookmark`; sie materialisiert Artist/Album/Track und ruft danach den bestehenden `tracks/<id>/monitor`-Pfad — kein zweiter Weg zu Wanted/Wishlist |
| [ldp-07](library-v2-issues.md#ldp-07) | Artwork-Geschwindigkeit | Implemented | `_apply_artwork_urls` wirft die Provider-URL nicht mehr weg (`remote_image_url`) und lässt sie bei reinen Discography-Zeilen sogar die primäre `image_url` bleiben; `precache_all_artwork` überspringt dieselben Zeilen. `Artwork` zeigt die CDN-URL statt des Platzhalters, solange ein kalter Build läuft, mit eigenem Fehlerzustand für die Remote-URL |
| [ldp-08](library-v2-issues.md#ldp-08) | Metadaten-Quellen NICHT übernehmen | Verified | Nicht portiert; `ArtistMatchChips` bleiben auch im reichen Kopf |
| [ldp-09](library-v2-issues.md#ldp-09) | Abschlussbedingung | Implemented (zurückgenommen §44.2, **wiederhergestellt §46**) | Über die Suche ist kein Weg mehr in die Legacy-Artist-Oberfläche offen; Regressionstest hält fest, dass `navigateToArtistDetail` nicht mehr aufgerufen wird |

### Bewusste Entscheidungen in der Umsetzung

| Thema | Entscheidung | Begründung |
|---|---|---|
| Wohin die Umstellung greift | In die Route, nicht in die einzelnen Aufrufer | Ein Änderungspunkt deckt Search, Global Search, Media Player, Playlist-Sync, Similar-Artist-Bubbles und `api-monitor.js` gleichzeitig ab; die URL-Form bleibt für Links/History erhalten |
| Optik | Die globalen Legacy-CSS-Klassen wiederverwenden statt nachbauen | `style.css` ist ungescopet und wird von derselben Seite geladen, in der die React-App hängt — die portierten Komponenten sind dadurch pixelgleich, ohne neues CSS. Genau die vom Nutzer verlangte Arbeitsweise „kopieren statt nachbauen" |
| Lazy Loading | Legacy-`observeLazyBackgrounds` wiederverwenden, mit direktem Fallback | Nach dem Löschen der alten Library darf das Kachelgitter nicht bildlos werden |
| Klick auf eine Discovery-Kachel | Adoptiert den Artist und übergibt an die reguläre V2-Ansicht, **ohne** ihn zu monitoren | Ein Klick ist eine ausdrückliche Nutzerhandlung, kein passives Blättern — und danach greift die vollständige V2-Maschinerie statt einer zweiten, halben Release-Oberfläche. Monitoring bleibt der Bookmark-Aktion vorbehalten (Guide §2.2: ein Release öffnen ist kein Artist-Intent) |
| Ownership-Badge im Discovery-Modus | Weggelassen | Es gibt keine Library zum Vergleich; ein dauerhaftes „Checking…" wäre eine Falschaussage. Legacy verhielt sich für Source-Artists genauso |
| Persistenz der Ansichtswahl | Route-Suchparameter (`releaseView`, `header`) | Konsistent mit `view` und `releases`; `lib2_ui_preferences` trägt Spalten-/Provider-Sichtbarkeit, keine Seitenmodi |

### Verifikation

- Backend: `tests/library2` 1.123 bestanden (8 neue: Artwork-Auslieferungsweg
  inkl. Nicht-HTTP-Pfad, Discovery-Resolve read-only, idempotentes
  Materialisieren, Legacy-ID-Schutz, Admin-Grenze, leere Identität,
  Precache-Ausschluss); `tests/search`, `tests/wishlist`, `tests/imports`
  1.075 bestanden; Ruff über alle geänderten Python-Dateien bestanden.
- Frontend: vollständige Suite 327 Tests in 53 Dateien bestanden — 19 neue
  (16 in drei neuen Dateien: Filter-Portierung, Discovery-Modus,
  Ansichts-/Kopfumschalter; plus 3 für den Artwork-Provider-Fallback);
  `oxfmt --check` und `oxlint --type-check` ohne neue Befunde; Production
  Build erfolgreich.
- Bewusst ersetzt statt repariert: die sechs Tests in
  `webui/src/routes/artist-detail/-route.test.tsx` und eine Assertion in
  `stats/-route.test.tsx` pinnten die Legacy-Übergabe, also genau das
  Verhalten, das ldp-01 entfernt (Guide §6 Regel 6).

**Offen:** ein realer Browser-Durchlauf gegen die Produktiv-DB (Suchtreffer →
Discovery → Bookmark → V2-Artist) sowie der Geschwindigkeitsvergleich der
Cover in `All Releases` gegen die Legacy-Ansicht. Die physische Löschung der
alten Library ist damit freigegeben, aber nicht Teil dieses Änderungssatzes.

---

## 43. Library V2 wird die Library — Cutover und Löschung der alten Oberfläche (28. Juli 2026)

Auftrag des Nutzers: die alte Library verschwindet vollständig aus dem
Frontend, Library V2 heißt und liegt fortan schlicht „Library", das Opt-in
entfällt, und wer aktualisiert, bekommt eine Migration, die von allein läuft
und nach einem Abbruch weitermacht statt neu anzufangen.

Die Arbeit liegt auf dem Zweig `library-legacy-removal` (Kopie von
`library-overhaul`), damit die eigentlichen Features auf `library-overhaul`
weiter testbar bleiben. Die beiden Migrations-Commits sind bereits nach
`library-overhaul` übernommen, weil sie nichts entfernen; die Löschung bleibt
bis zur Freigabe auf dem Zweig.

### 43.1 Migration einer aktualisierenden Installation

| # | Befund | Status | Umsetzung |
|---|---|---|---|
| mig-01 | Ein Absturz mitten in der Migration begann beim nächsten Start wieder beim ersten Artist | Implemented | Jeder Batch-Heartbeat schreibt zusätzlich die committete Walk-Position (`resume_stage`, `resume_rowid`, `resume_run_id`); der nächste Versuch setzt dort auf. Nach den drei Walks rückt der Checkpoint auf `finalizing` vor, damit ein Abbruch in der Nachbereitung die Quelltabellen nicht erneut abläuft |
| mig-02 | Ein Resume hätte die bereits migrierten Zeilen entwertet | Implemented | `ResumePoint.run_id`: Der Importer ist ein Snapshot-Reconciler — er löst jede Legacy-Zeile ab, die *dieser* Lauf nicht gesehen hat. Ein Resume mit frischer Run-ID hätte alles vom abgestürzten Versuch Migrierte von seiner Legacy-ID getrennt und beim nächsten Lauf als Dublette neu importiert. Ein Test hält genau diesen Schaden fest |
| mig-03 | Ein Checkpoint über eine veränderte Quelle ist bedeutungslos | Implemented | `resume_point_for` vergleicht das beim Claim gestempelte `resume_watermark` mit dem aktuellen; nach einem Media-Server-Scan startet der Lauf sauber neu |
| mig-04 | Nach einem Neustart blockierte der tote Claim des Vorgängerprozesses bis zu zehn Minuten | Implemented | `reclaim_abandoned_claim`: SoulSync läuft mit genau einem Anwendungsprozess (`workers = 1`), also kann ein Claim, dessen Heartbeat älter ist als der Start dieses Prozesses, keinem lebenden Importer gehören. Der Checkpoint bleibt dabei erhalten |
| mig-05 | Auf einer frischen Installation beendete sich der Autostart-Thread endgültig | Implemented | `should_stop_autostart`: `waiting_for_source` ist Erfolg *ohne* Migration; nur eine tatsächlich migrierte Library beendet die Schleife. Vorher erreichte der erste Media-Server-Scan `lib2_*` erst nach dem nächsten Neustart |
| mig-06 | Tracklist-, Tag- und Artwork-Precache hingen allein am Import-Knopf | Implemented | `core/library2/post_import.py` — beide Aufrufer (Knopf und Autostart) teilen sich dieselbe Kette, jede Stufe best effort. Eine Installation, die sich selbst migriert hat, bekam vorher Zeilen, aber keine aufgelösten Tracklisten, keine Tag-Fakten und kein Artwork |
| mig-07 | Die Seite konnte eine laufende Hintergrundmigration nicht benennen | Implemented | Der Statusendpunkt trug den persistierten Bootstrap-Zustand längst, der Client ignorierte ihn. Leerzustand, Import-Knopf und Fortschrittsanzeige sprechen jetzt von „Migrating your library", der Knopf sperrt sich, solange die Migration den Claim hält, und ein Fehlschlag wird mit seinem Grund genannt |

### 43.2 Cutover der Oberfläche

| Thema | Entscheidung | Begründung |
|---|---|---|
| Route | `/library` ist die V2-Seite; `/library-v2` bleibt als Weiterleitung mit vollständigem Query-String | Wer die Seite im Opt-in benutzt hat, hat `/library-v2` in Lesezeichen, History und offenen Tabs |
| Verzeichnis vs. Dateinamen | `src/routes/library-v2/` → `src/routes/library/`, die Dateien darin behalten ihre `-library-v2.*`-Namen | Rund 60 Module umzubenennen hätte den Cutover in Rauschen begraben, ohne Verhalten zu ändern; die internen Namen sieht kein Nutzer |
| Berechtigung | Schlicht `library` | Der Sonderfall, der `library-v2` die `library`-Berechtigung erben ließ, entfällt. `library-v2` bleibt als Alias in `LEGACY_PROFILE_PAGE_ALIASES`, damit ein alter persistierter Wert nicht auf eine unbekannte Seite zeigt |
| Sidebar | Ein Eintrag statt zweier, ohne Feature-Probe | `revealLibraryV2NavIfEnabled` und der versteckte zweite Eintrag sind ersatzlos weg |

### 43.3 Was aus `library.js` weiterlebt

`webui/static/library-shared.js` (447 Zeilen statt 10.185). Es enthält
ausschließlich, was von *anderen* Seiten benutzt wird: `_esc`,
`playLibraryTrack`, `navigateToArtistDetail` und den Dialog „Manual Library
Match", der von der Sync- und der Tools-Seite geöffnet wird.

`navigateToArtistDetail` ist auf vier Zeilen geschrumpft: Der ganze
Legacy-Seitenzustand (Label-Stack, Reload-Wächter) beschrieb eine Seite, die es
nicht mehr gibt; die Route `/artist-detail/:source/:id` löst der React-Router
ohnehin in die Library auf.

Mitgelöscht, weil ihr einziger Einstieg im entfernten Markup lag: die
Sidebar-Breadcrumb, die Library-Download-Bubbles in `shared-helpers.js`, die
Hover-Hilfe auf Library-DOM-IDs und die Tour-Schritte (die Tour beschreibt die
Library jetzt vom Sidebar-Eintrag aus — eine React-Seite mit gehashten
Klassennamen bietet nichts Stabiles zum Anheften).

### 43.4 Verlorene Funktionen

| Funktion | Verbleib |
|---|---|
| Re-identify (#889) | Geparkt in `docs/legacy-parked/reidentify/`, ausdrücklich zum Nachbauen in V2 vorgemerkt. Backend (`/api/reidentify/*`, `core/imports/rematch_*.py`) läuft unverändert weiter |
| Artist „Enhance Quality" | Geparkt in `docs/legacy-parked/artist-enhance-quality/`. Offene Produktfrage: V2 löst dasselbe über Quality-Profile mit Cutoff plus Automatic Search |
| „Write Artist Image" (#572) | Ebenda. Steht in Spannung zu Guide §2.1 (Media-Server-Unabhängigkeit) — ein Nachbau wäre ein bewusster Export-Knopf |
| Watch All Unwatched | Ersatzlos; V2 hat Bulk-Monitoring |
| Artist-Export (JSON/CSV/Text) | Ersatzlos |
| A-Z-Sprungselektor | Ersatzlos; ausdrückliches Nicht-Ziel (Guide §1.2) |

### 43.5 `style.css` bleibt unangetastet

Die Video-Library und die Label-Seite verwenden die Klassen der
Musik-Library weiter (`library-artists-grid`, `alphabet-selector`,
`library-search-input`, `library-pagination`, `library-artist-watchlist-btn`
…), und die V2-Discovery-Ansicht wurde bewusst auf ihnen gebaut (§42). Totes
CSS schadet nicht; ein Aufräumen wäre eine eigene, gezielt zu verifizierende
Arbeit.

### 43.6 Verifikation

- Backend: `tests/library2`, `tests/imports`, `tests/acquisition`,
  `tests/wishlist` — 2.405 bestanden, 3 übersprungen. 27 neue Tests in
  `tests/library2/test_bootstrap_resume.py` (Resume über Stages und Zeilen,
  Run-ID-Schutz mit beiden Richtungen, Watermark-Invalidierung, Reclaim,
  Post-Import-Hook, Autostart-Abbruchbedingung).
- Frontend: vollständige Suite 342 Tests in 54 Dateien bestanden (12 neue:
  Migrationsanzeige, Leerzustand, `/library-v2`-Weiterleitung mit
  Suchparametern); `oxfmt --check` und `oxlint --type-check` ohne neue
  Befunde (zwei Warnungen bestanden schon auf sauberem HEAD); Production
  Build erfolgreich.
- Reale Anwendung (laufender `dev.py`, Chromium): `/library` rendert die
  V2-Seite mit aktivem Sidebar-Eintrag, `/library-v2?section=wanted` leitet
  unter Erhalt der Parameter weiter, ein Provider-Artist landet weiter im
  Discovery-Modus mit reichem Kopf, „Manual Library Match" öffnet aus Sync,
  und Search/Watchlist/Sync/Tools/Stats/Dashboard laden ohne einen einzigen
  JS-Fehler.
- Bewusst ersetzt statt repariert: die Tests, die `/library-v2` als Ziel
  festhielten, pinnten genau das, was dieser Cutover verschiebt (Guide §6
  Regel 6).

**Offen:** ein Durchlauf gegen eine Kopie der Produktiv-DB — insbesondere ein
Neustart mitten in der Migration auf einer großen Library, um den
Resume-Checkpoint unter realer Datenmenge zu belegen.

---

## 44. Upstream-Sync auf `Nezreka/SoulSync:dev` (31. Juli 2026)

Der Branch wurde per **Merge** (nicht Rebase) auf `upstream/dev` @ `d0cb43db5`
gebracht. Merge-Base war `d78755ca6` („bump to 3.1.8"): 197 Upstream-Commits
gegen 109 eigene. Ein Rebase hätte jede der 109 Änderungen einzeln gegen einen
in der Zwischenzeit vollständig nach React portierten WebUI-Baum stellen
müssen; der Merge löst dieselbe Menge Konflikte genau einmal. Vorheriger Stand
liegt als `backup/library-overhaul-pre-upstream-sync-20260731`.

### 44.1 Was Upstream in der Zwischenzeit gebaut hat

Der Kern ist eine **React-Migration des gesamten WebUI**: Watchlist, Wishlist,
Automations, Library, Artist Detail, Label Detail, Search (enhanced *und*
basic) und Active Downloads sind jetzt React-Routen, die jeweilige
Vanilla-Seite ist gelöscht. Dazu kamen Chat/Arcade (Discord-artige Räume,
serverlose Spiele), Discover-Caching, Spotify-Rate-Limit-Härtung und
Video-Fixes. Backendseitig ist der Zuwachs klein und berührt V2 nicht
(`api/chat.py`, `core/metadata/cache.py`, `core/search/basic.py`,
`database/music_database.py` +187 Zeilen, alle konfliktfrei automatisch
gemergt).

### 44.2 Die eine echte Kollision: `/library` und `/artist-detail`

Beide Seiten haben unabhängig voneinander `/library` nach React portiert —
wir als Library V2, Upstream als originalgetreuen Port der alten Liste
(`-library.api.ts`, `-ui/library-page.tsx`, `library-artist-card.tsx`, …).
Zwei React-Seiten können dieselbe Route nicht besitzen.

- **`/library` bleibt Library V2.** Der Upstream-Port ist damit unerreichbar
  und wurde gelöscht statt als toter Code stehen zu lassen; er ist genau die
  Seite, die diese PR ersetzt.
- **`/artist-detail` gehört wieder Upstream.** Unsere ldp-01-Weiterleitung
  (jede Artist-URL landete im V2-Discovery-Modus) stammt aus der Zeit, als
  diese Seite eine Vanilla-Seite der gelöschten Library war. Sie ist jetzt
  eine gepflegte React-Seite mit eigenen Features (Gap-Fill #1067/#1071,
  DB-Record-Inspector, Enhanced View, Inline-Edit). Die Weiterleitung hätte
  all das unerreichbar gemacht, deshalb ist sie zurückgenommen.
- **Erhalten bleibt der gezielte Einstiegspunkt:** ein Suchtreffer unter
  „In Your Library" öffnet Library V2, sobald V2 den Artist kennt
  (`library_v2_id`), sonst unverändert Artist Detail. Das war §11 und lebte
  in der gelöschten `search.js`; es ist jetzt als `inLibraryArtistPath()` in
  `-search.helpers.ts` portiert und getestet. Dasselbe gilt für die globale
  Suche in `downloads.js`.

### 44.3 Zurückgenommene Löschungen

Weil Upstreams React-Artist-Detail auf Vanilla-Globals aufsetzt, mussten
Löschungen dieses Branches rückgängig gemacht werden:

- `webui/static/library.js` ist wieder da (Upstream-Fassung, 7.703 Zeilen:
  Artist-Detail-State, Discography-Modal, Enhanced View, Watch-All-Modal).
  Unser `library-shared.js` war ein 447-Zeilen-Auszug daraus und ist
  entfallen; die einzige inhaltliche Abweichung — die typisierten
  `lib2_track_id`/`legacy_track_id`/`server_track_id` in `playLibraryTrack` —
  ist in `library.js` übernommen.
- `stats-automations.js` (Enhance-Quality-Modal, `playArtistRadio`,
  `writeArtistImageToDisk`) und der Breadcrumb-Aufruf in `shell-bridge.js`
  sind wiederhergestellt; `tests/test_artist_detail_cross_file_contract.py`
  hält beide Seiten dieser Naht fest.
- Die Enhanced-Bulk-Edit-, Tag-Preview-, Reorganize- und
  Re-identify-Modale in `index.html` sind wieder eingesetzt.

Erhalten bleiben die Download-Bubbles auf der Library-Seite: die V2-Seite
rendert jetzt den `[data-library-downloads-host]`-Host und ruft
`showLibraryDownloadsSection()`, und `ss:library-changed` wird über den neuen
Hook `-library-v2.live.ts` gehört.

### 44.4 Drei Fehler, die der Sync aufgedeckt hat

Alle drei bestanden schon vor dem Merge auf diesem Branch und wurden von
Upstreams neuen Guard-Tests sichtbar gemacht:

1. `downloads.js` rief `isConfirmedSearchIntentModal()` hinter einem
   `typeof === 'function'`-Guard auf — die Funktion war beim Aufräumen aus
   `shared-helpers.js` verschwunden, der Zweig also stiller toter Code und
   das Quality-Profil des Confirmed-Search-Modals wirkungslos. Wieder
   hergestellt (`tests/test_vanilla_globals_resolve.py`).
2. `libraryV2SearchSchema.q` war ein blankes `z.string().default('').catch('')`:
   TanStack JSON-parst Suchparameter, ein Filter wie „123" kam als NUMBER an,
   wurde verworfen und die Liste zeigte kommentarlos alles. Jetzt
   `coercedString` wie `discover`/`discoverName`.
3. Die vier Library-Tour-Schritte in `helper.js` ankerten auf
   `[data-page="library"]` ohne Klassen-Token, was
   `tests/test_helper_tours.py` nicht validieren kann. Jetzt
   `.nav-button[data-page="library"]` wie jeder andere Sidebar-Anker.

### 44.5 Verifikation

- Frontend: `tsc --noEmit` sauber, Production Build erfolgreich,
  `oxfmt --check` + `oxlint --type-check` mit **298 Warnungen / 0 Fehlern** —
  exakt der Stand von sauberem `upstream/dev`, also keine neu eingeführten.
- Vitest: 1.970 bestanden / 143 Dateien. Die 131 Fehlschläge in vier
  `artist-detail`-Dateien sind **auf unverändertem `upstream/dev` identisch
  reproduziert** (lokale Node-Runtime liefert kein `localStorage`); sie sind
  keine Folge des Merges.
- Pytest: `tests/library2` 1.193, `tests/library`+`search`+`wishlist`+
  `metadata` 1.324, `tests/downloads`+`repair_jobs`+`quality`+`imports` 1.761,
  sowie alle 61 Top-Level-Dateien, die eine der gemergten Frontend-Dateien
  lesen, 984 — alles bestanden.

---

## 45. Multi-Agent-Audit nach dem Upstream-Sync — Arbeitsliste (1. August 2026)

Fünf parallele Read-only-Audits über `483405764`. Die vollständigen Diagnosen
stehen in [issues.md §29](library-v2-issues.md#29-multi-agent-audit-nach-dem-upstream-sync-1-august-2026);
diese Tabelle ist ausschließlich der Remediationstatus. **Abgearbeitet in §46**
— der Stand unten ist der Endstand, nicht die ursprüngliche Pending-Liste.

Die vier mit *verifiziert* markierten Blocker hat der Koordinator unabhängig am
Code nachgeprüft, nicht nur vom Agenten übernommen. Wichtig für die
Priorisierung: **keiner dieser Befunde wurde von der bestehenden Testsuite
gefangen** (webui 240 Tests grün, `tests/library2` 1.193 grün) — deshalb ist zu
jedem Blocker ein Regressionstest entstanden, der vor dem Fix nachweislich
fehlschlägt.

### 45.1 Vor dem PR-Ready zu erledigen

| ID | Kurz | Status |
|---|---|---|
| [iss29-A01](library-v2-issues.md#iss29-a01) | Upgrade landet in dauerhaft leerer Library, meldet sich als „done" (`try_claim` stempelt den Watermark neu, ohne den Checkpoint zu löschen) — *verifiziert* | **Behoben** (§46.2) |
| [iss29-D01](library-v2-issues.md#iss29-d01-blocker--schreibtransaktion-bleibt-über-provider-http-calls-offen--verifiziert) | Schreibtransaktion über Provider-Calls offen in der Anchor-Schleife — dieselbe Deadlock-Klasse wie die bekannte Produktivstörung — *verifiziert* | **Behoben** (§46.3) |
| [iss29-E01](library-v2-issues.md#iss29-e01) | Reorganize löscht das Original, obwohl der DB-Update fehlschlug — *verifiziert* | **Behoben** (§46.4) |
| [iss29-E02](library-v2-issues.md#iss29-e02) | Sibling-Move überschreibt eine vorhandene Datei stillschweigend — *verifiziert* | **Behoben** (§46.4) |
| [iss29-E04](library-v2-issues.md#iss29-e04) | Destruktives Repair löscht Fuzzy-Resolver-Treffer ohne Root-Containment (kann frische Downloads im Transfer-Ordner treffen) | **Behoben** (§46.5) |
| [iss29-E03](library-v2-issues.md#iss29-e03) | Eine gelöschte Datei markiert alle Dateien des Albums als `deleted` → Re-Download eines vollständigen Albums | **Behoben** (§46.5) |

### 45.2 Wichtig, aber nicht release-blockierend

Alle behoben (§46.6–§46.8): `iss29-A02`, `A03`, `A04`, `A05`, `A06`,
`B01`, `B03`, `B04a`, `B04b`, `B04c`, `B05`, `B07`, `B08`, `B09`,
`C01`–`C10`, `E05`, `E06`, `E07`, `E08`, `E09`, `E10`, `D02`–`D12`.

`iss29-A07` war **kein** Befund: der Rückgabewert wird zu Recht verworfen, der
Aufruf existiert für die einmalige Deprecation-Warnung. Die Absicht ist jetzt
im Code dokumentiert statt implizit.

### 45.3 Produktentscheidung — getroffen

[iss29-B02](library-v2-issues.md#iss29-b02): Der Nutzer hat entschieden, dass ein
Suchtreffer in Library V2 landen soll — auch für einen Artist, den die Library
noch nicht kennt. Damit ist **ldp-01 wiederhergestellt** statt der Discovery-Code
gelöscht; `iss29-B05` (Rich-Header-Vorwahl) hängt daran und ist mit erledigt.
Konsequenz, ausdrücklich so gewollt: Upstreams eigene React-Artist-Seite
(7.165 Zeilen, u. a. Gap-Fill #1067/#1071, DB-Record-Inspector, Enhanced View,
Inline-Edit) ist über die Suche nicht mehr erreichbar. Siehe §46.1.

### 45.4 Dokumentationsschuld

[iss29-B06](library-v2-issues.md#iss29-b06): erledigt — §42 führt `ldp-01`/`ldp-09`
jetzt mit ihrer Rücknahme UND ihrer Wiederherstellung, die vier Code-Kommentare
stimmen wieder (die Weiterleitung existiert tatsächlich), `find22-15` (§3) trägt
den Hinweis auf die zwischenzeitliche Verletzung, und `M-12` ist durch den
`onError` aus §46.7 gedeckt.

### 45.5 Bewusst offen geblieben — **inzwischen abgearbeitet in §47**

Der Stand unten ist der Grund, aus dem diese vier am 1. August zunächst liegen
blieben. Die fehlende Reproduktion ist in [§47](#47-abarbeitung-der-vier-in-455-vertagten-befunde-1-august-2026)
nachgeholt; Ergebnis: drei echte Fehler, ein Fehlalarm.

| ID | Warum damals vertagt | Ausgang |
|---|---|---|
| [iss29-A08](library-v2-issues.md#293-minor) | **SPEKULATIV** — der Agent konnte es selbst nicht belegen. Das Doc verlangt eine eigene Reproduktion vor jeder Arbeit; die wurde nicht durchgeführt, also wurde nichts geändert | **Bestätigt, behoben** (§47.2) — kritisch ist nicht der 20-Alben-Beat, sondern der 50-Dateien-Beat der letzten Stufe |
| [iss29-A09](library-v2-issues.md#293-minor) | **SPEKULATIV**, ausdrücklich als „bounded, nicht quadratisch, ungemessen" beschrieben. Ohne Messung wäre jede Änderung eine Wette | **Kein Defekt** (§47.4) — gemessen: 29,6 MiB bei 100k Tracks. Geschlossen, nicht vertagt |
| [iss29-B10](library-v2-issues.md#293-minor) | **SPEKULATIV** und formgleich zu Upstream. Ein Eingriff in Upstreams Permission-Gate ohne belegten Fall erzeugt nur Sync-Konflikte | **Bestätigt, behoben** (§47.3) — reproduziert als Endlosschleife, nicht als Bounce; zentral in `bridge.ts` |
| [iss29-D13](library-v2-issues.md#minor-backend) | Sauber zu beheben nur mit einer neuen normalisierten Namensspalte + Index (Schema-Migration und Anpassung aller Schreibpfade). Das ist eigene Arbeit mit eigenem Testbedarf, nicht der Abschluss dieser Runde — bewusst vertagt statt am Sitzungsende hineingehastet | **Bestätigt, behoben** (§47.1) — „alle Schreibpfade" waren vier INSERTs und ein UPDATE; 169 ms → 0,004 ms |


## 46. Abarbeitung des Multi-Agent-Audits (1. August 2026)

Alle Befunde aus §45 / [issues.md §29](library-v2-issues.md#29-multi-agent-audit-nach-dem-upstream-sync-1-august-2026)
umgesetzt, bis auf die vier in §45.5 begründeten. Jeder Blocker hat einen
Regressionstest, der **vor** dem Fix nachweislich fehlschlägt — die bestehende
Suite fing keinen einzigen dieser Befunde.

Endstand: `tests/library2` 1.219 grün (vorher 1.193), `webui` 1.974 grün
(vorher 1.971). Die vier `artist-detail`-Dateien mit „localStorage is not
available" (131 Tests) sind unverändert die bekannte lokale Node-Störung, kein
Tree-Problem — sie reproduzieren identisch auf sauberem `upstream/dev`.

### 46.1 ldp-01 wiederhergestellt (iss29-B02, B03, B05)

Nutzerentscheidung: ein Suchtreffer soll in Library V2 landen, auch für einen
Artist ohne Katalogzeile. `/artist-detail/$source/$id` ist damit wieder der
50-Zeilen-Redirect-Stub nach `/library?discover=<source>:<id>`, wie vor dem
Sync. Dass Upstreams gepflegte React-Artist-Seite dadurch unerreichbar wird,
ist bekannt und gewollt; sie liegt weiter im Tree und ist ein Ein-Zeilen-Revert
entfernt.

Mitgezogen:

- **B03** — ein `<a href="/library?...">` löste einen vollen Dokument-Load aus,
  weil weder TanStack rohe Anker abfängt noch der Shell-Interceptor diesen Pfad
  kannte. Neu: `SoulSyncWebRouter.navigateToHref` plus ein Zweig in
  `_handleShellLinkClick`, der jede React-Route in-app übernimmt — deckt auch
  die von Hand gebauten Links in `downloads.js` ab.
- **B01** — dieselbe Naht: die Sidebar-„Library" war aus jeder Unteransicht ein
  toter Klick, weil `navigateToPage` bei `pageId === currentPage` sofort
  zurückkehrt. React-Seiten gehen jetzt über ihren Basispfad, was den Guard
  umgeht und die Sub-View-Parameter fallen lässt — genau das, was der Klick meint.
- **B05** — `inLibraryArtistPath` trägt die ldp-05-Vorwahl
  (`releases=all&releaseView=cards&header=rich`), damit derselbe Artist nicht
  unterschiedlich aussieht, je nachdem ob V2 ihn schon gemappt hat.

Die fünf Tests in `artist-detail/-route.test.tsx`, die Upstreams Seite pinnten,
sind **ersetzt** (nicht repariert) — sie pinnen jetzt den Redirect. Einer davon
hat dabei einen echten Fund produziert: TanStack JSON-kodiert Suchparameter, ein
reiner Ziffernname steht also als `discoverName=%22311%22` auf der Leitung. Die
Assertion prüft deshalb den *geparsten* Wert, nicht den Querystring.

### 46.2 Migration: Checkpoint, Heartbeat, Watermark (A01, A02, A04, A05, A06)

- **A01** (Blocker): `try_claim` löscht `resume_stage`/`resume_rowid`/`resume_run_id`,
  wenn der gespeicherte `resume_watermark` nicht der ist, den der Claim gerade
  stempelt. Die Invariante ist damit lokal und gilt für jeden Aufrufer: ein
  Checkpoint überlebt einen Claim nur, wenn er gegen denselben Quell-Snapshot
  entstand. Zwei Tests — der wiederbelebte Punkt UND das echte Resume.
- **A02**: ein Beat mit `rowid` wird nie gedrosselt. Nur solche Beats persistiert
  `heartbeat` überhaupt; die Drosselung verwarf also genau die Checkpoints, und
  ein vollständig erfolgreicher Lauf endete mit `resume_stage='tracks', rowid=0`
  — dem Wert, der A01 von Doppelarbeit zu leerer Library macht.
- **A04**: `mark_done` stempelt den **vor** dem ersten Walk erfassten Watermark.
  Ein Artist, der während des Laufs entsteht, bleibt damit sichtbar offen und
  wird beim nächsten Tick gewalkt.
- **A05**: `ensure_bootstrap_schema` schreibt nur noch, wenn die Zeile fehlt —
  vorher nahm jeder `/import/status`-Poll (1×/s pro Tab) die Schreibsperre.
- **A06**: der Autostart-Backoff wird nach einem Tick mit Fortschritt
  zurückgesetzt, statt sich auch bei No-ops bis 30 min zu verdoppeln.

### 46.3 Schreibsperre über Provider-Calls (D01)

`resolve_and_enrich_native_artist` sammelt jetzt **alle** Anchor-Identitäten
netzseitig ein und schreibt sie danach — dieselbe Form, die
`enrich_native_entity_for_service` 300 Zeilen weiter unten bereits beschreibt.
Vorher öffnete `_persist_identity` (blankes UPDATE, `isolation_level=""`) eine
Transaktion, die über den nächsten Provider-Roundtrip offen blieb: die
Provider-Clients cachen ihre Antworten in **derselben** Datenbank, der Writer
wartete also auf sich selbst.

Der Test misst `conn.in_transaction` **während** des Provider-Calls, nicht die
Aufrufreihenfolge — ein Refactoring, das den verschränkten Write zurückbringt,
fällt damit auf, wie auch immer die Schleife geschrieben ist.

### 46.4 Reorganize (E01, E02, E05, E07)

- **E01** (Blocker): der Callback in `reorganize_runner` schluckt nichts mehr.
  `_finalize_track` erkennt einen fehlgeschlagenen DB-Update ausschließlich an
  einer Exception und löscht danach das Original — der geschluckte Fehler
  zerstörte also die einzige Kopie und meldete „moved". Legacy- und lib2-Write
  liegen jetzt in **getrennten** Transaktionen: auf `main` war das eine einzige
  Anweisung, dieser Branch hatte vier weitere in denselben `try` gelegt.
- **E02** (Blocker): der Sibling-Move prüft die Zieldatei (`shutil.move` löst auf
  POSIX zu `os.rename` auf und überschreibt lautlos) und läuft erst **nach** dem
  erfolgreichen kanonischen Rename — vorher lag das Album bei einem Fehlschlag
  auf zwei Ordner verteilt.
- **E05**: `.lrc` & Co. werden mitgenommen statt gelöscht. Reorganize war der
  einzige Mover im Projekt, der das Sidecar wegwarf — und traf damit genau die
  Dateien, die lib2 selbst schreibt.
- **E07**: `save_audio_file`s `False` („Original unangetastet, Tags NICHT
  geschrieben") wird an allen drei dateiverändernden Aufrufern ausgewertet. Im
  nativen P3-Pfad bricht der Fix jetzt ab, statt die Datei umzubenennen und das
  Finding aufzulösen.

### 46.5 Destruktives Repair (E03, E04, E06, E08, E09, E10)

- **E03** (Blocker): der Delete-Zweig in `sync_repair_change` benutzt
  `links["direct_files"]` — nur das, was die Änderung **konkret** benannt hat.
  Der Album-Fan-out von `_resolve_links` existiert für den Rescan-Scope; ihn zum
  Löschen zu benutzen hieß, dass eine entfernte Datei das ganze Album als
  dateilos markierte und die Wishlist ein vollständig vorhandenes Album erneut
  herunterlud.
- **E04** (Blocker): neue gemeinsame Schranke
  `fuzzy_resolved_path_is_deletable` — ein vom Resolver **geratener** Pfad muss
  in einem konfigurierten Library-Root liegen. Der Suffix-Walk probiert den
  Transfer-Ordner zuerst, und Importe liegen dort im selben Layout; ein Finding
  auf einer verschwundenen Library-Datei traf also den frischen Ersatz-Download.
  Angewandt an allen drei Löschstellen, fail-closed ohne konfigurierte Roots.
- **E06**: `mark_file_verification_status` versucht zuerst den indizierten
  Gleichheits-Match (`idx_lib2_track_files_path`) und fällt nur bei Fehlschlag
  auf den Resolver-Walk zurück; der Aufruf liegt außerdem nicht mehr in der
  offenen Schreibtransaktion des „Approve"-Endpunkts. Der Fallback bleibt
  bewusst vollständig — der Resolver, nicht diese Funktion, definiert
  „dieselbe Datei". Ein erster Versuch, den Walk über Basenames zu verengen,
  wurde verworfen, weil er einen echten Vertrag gebrochen hätte.
- **E08**: `rename_to_basename_result` trennt „schon richtig benannt" von
  „Rename verweigert"; beides kam vorher als `None` an und wurde als Fix gezählt.
- **E09**: der dd28-29-Rollback nimmt das `.lrc` mit zurück.
- **E10**: der Katalog-Write kommt erst, nachdem die Datei als erreichbar
  nachgewiesen ist — vorher wurde bei nicht gemountetem Root umnummeriert,
  während die Datei ihre alte Nummer behielt.

### 46.6 UI-Fehlerzustände (A03, C01–C06)

- **A03**: das Import-Status-Polling beobachtet `bootstrap.status`. `running`
  setzt nur der manuelle Import-Button; die automatische Migration fasste ihn nie
  an, also stoppte React Query den Timer nach dem ersten Fetch und die Seite fror
  auf derselben Prozentzahl ein.
- **C01**: die Zeitstempel verlassen den Server als ISO-8601-UTC (`_iso_utc`).
  SQLites `CURRENT_TIMESTAMP` ist UTC ohne Zone, V8 liest das als **Lokalzeit** —
  in Europe/Zurich zwei Stunden daneben. Der Grab-Watcher filtert nach
  „neuer als mein Start", das Quarantäne-Event fiel also aus dem Filter und die
  UI meldete am Ende **Grabbed ✓** für eine Datei, die nie ankam. Serverseitig
  behoben, damit nicht jeder Konsument denselben Fehler wiederholt.
- **C02**: 409 („Job läuft bereits") wird gelesen statt geworfen — der Server
  liefert die laufende Job-ID im Body.
- **C03/C04/C05**: `isError` wird überall ausgewertet. Kein „Your library is
  empty" mehr für einen Nutzer mit 900 Artists, kein ewiges „Loading…" nach einem
  404, und die Wanted-Ansicht behauptet nicht mehr „alles schon auf Platte",
  wenn der Request gescheitert ist.
- **C06**: eine Queue-Abfrage pro Artist; das Ergebnis wird an die Alben
  durchgereicht. Der Guard-Test prüfte ein Literal, das im Code gar nicht
  vorkam, und bestand deshalb vakuum — er ist neu geschrieben und prüft die
  tatsächliche Schreibweise.

### 46.7 UI-Aktionen (C07–C10, B04, B07, B08, B09)

- **C07**: `Promise.allSettled` statt `Promise.all` — ein Teilerfolg wird als
  solcher gemeldet, statt 39 angewandte Änderungen als Totalausfall auszugeben.
- **C08**: die Bulk-Bar hat die Quality-Profile-Aktion (inkl. „Inherit"), die
  UI-04 fordert und die Backend und Einzelpfad längst konnten.
- **C09**: `useUiPreferencesMutation` hat einen `onError` — vorher scheiterte
  eine Präferenz lautlos und war nach dem nächsten Laden wieder weg (M-12 war
  als implementiert geführt).
- **C10**: `/library/v2/enabled` liefert `can_write`, und der gemeinsame
  `ActionButton` wertet es aus. Ein Nicht-Admin bekam vorher die volle Toolbar,
  jeder Klick 403.
- **B04a/b/c**: „In Your Library" überlebt den YouTube-Tab (lokales Ergebnis,
  quellenunabhängig); der Suchcache ist durch den ldp-01-Redirect entschärft
  (Discovery löst live auf); und ein lib2-nativer Artist wird nicht mehr gegen
  den generischen Provider-Bild-Resolver aufgelöst, der IDs an Provider
  weiterreicht und zuverlässig ein fremdes Gesicht lieferte.
- **B07**: `/library-v2` steht in `_DEEPLINK_VALID_PAGES`.
- **B08**: die lib2-Artist-ID reist mit zum Player, „Go to artist" ist damit bei
  V2-Wiedergabe nicht mehr dauerhaft deaktiviert.
- **B09**: die Filterfelder sind kontrolliert und folgen der URL — Browser-Back
  desynchronisierte sie vorher dauerhaft.

### 46.8 lib2-Backend (D02–D12)

- **D02**: die Wishlist→lib2-Auflösung indiziert den Katalog **einmal** statt ihn
  pro Deskriptor erneut zu durchlaufen (1.000 × 50k Iterationen mit je einem
  `json.loads`). Die Ambiguitätserkennung bleibt exakt erhalten — der Index
  zeigt auf Listen.
- **D03**: der Monitor-Endpunkt schiebt den Provider-Walk in
  `_schedule_tracklist_resolve`, statt einen Web-Worker über die volle
  Provider-Kette zu blockieren. Die Album-Detail-Route lehnt genau das seit
  langem ab und begründet es.
- **D04**: die Artist-Suche ist indizierbar formuliert
  (`canonical_artist_id = a.id OR (… IS NULL AND member.id = a.id)` statt eines
  `COALESCE`, das kein Index bedienen kann) und escaped `%`/`_`, die vorher als
  Wildcards aus der Nutzereingabe wirkten.
- **D05**: `resolve_tracklist` dokumentiert, dass es die Verbindung des
  **Aufrufers** committet — wie `mirror_tracks_wishlist` es für sich selbst tut.
  Alle vier heutigen Aufrufer sind nur durch ihre Position sicher.
- **D06**: `enrich_native_entity_all_services` liest `provider_id` statt des nie
  gelieferten `external_id`. Der zugehörige Test hatte die falsche Vertragsseite
  gepinnt (sein Stub gab `external_id` zurück) — Defekt und Guard hoben sich
  gegenseitig auf.
- **D07**: `retry_failed` beschreibt, woher die Supersede-Garantie wirklich
  kommt (aus `drain`) und wodurch sie verloren geht.
- **D08/D09**: `file-tags/edit` validiert den Typ von `key`, und sieben Routen
  benutzen `request.get_json(silent=True)` — es gibt projektweit keinen
  `errorhandler`, ein Client bekam sonst Flasks HTML-500 statt der JSON-Form.
- **D10**: verschluckte Tag-Cache-Fehler rollen zurück, bevor die nächste
  Anweisung LRClib bzw. das NAS anspricht.
- **D11**: `prune_done` läuft am Ende eines `drain`, der etwas getan hat. Vorher
  lief es auf dem normalen Pfad nie, und `_superseded_ids` scannte die wachsende
  Historie bei jedem Drain.
- **D12**: `/artists/<id>/duplicates` benutzt das vorhandene
  `primary_file_rows` statt zwei Abfragen pro Paar.


## 47. Abarbeitung der vier in §45.5 vertagten Befunde (1. August 2026)

§45.5 hat vier Befunde bewusst offen gelassen: drei als **SPEKULATIV** (der
Agent konnte sie nicht belegen, und das Doc verlangt eine eigene Reproduktion
vor jeder Arbeit) und einen als zu groß für den Sitzungsschluss. Diese Runde
holt genau die Reproduktion nach, die damals gefehlt hat — und kommt dabei
zu **drei** Fehlern und **einem** Fehlalarm.

Die Reihenfolge ist Absicht: erst messen, dann entscheiden, ob überhaupt etwas
zu ändern ist. Bei `iss29-A09` lautet die Antwort nein, und das ist ein
Ergebnis, kein übersprungener Punkt.

### 47.1 iss29-D13 — bestätigt, und schlimmer als beschrieben

Der Befund sagte, `autolink` falle für Nicht-ASCII-Namen auf den Full-Table-Scan
zurück. `EXPLAIN QUERY PLAN` sagt: der „schnelle Pfad" war nie schnell.

```
SELECT id FROM lib2_artists WHERE lower(name) = ?   -> SCAN lib2_artists
SELECT id FROM lib2_artists WHERE name_key  = ?     -> SEARCH … USING COVERING INDEX
```

Kein Index kann `lower(name)` bedienen, also scannt **jede** Namensauflösung die
Tabelle — auch für ASCII. Nicht-ASCII zahlt zusätzlich den Python-Scan, weil
SQLites `lower()` ASCII-only ist: `'ЛЮБЭ'`, `'Μέλισσες'`, `'İzel'` bleiben dort
unverändert, während `normalize_name` casefoldet. Zwei volle Scans pro fertigem
Download.

Gemessen (synthetische Library, `scratchpad/verify_d13.py`):

| Artists | vorher (Nicht-ASCII) | nachher | Faktor |
|---|---|---|---|
| 5.000 | 8,7 ms | 0,004 ms | ~2.000× |
| 30.000 | 52,6 ms | 0,004 ms | ~13.000× |
| 60.000 | 103,5 ms | 0,012 ms | ~8.700× |
| 100.000 | 168,6 ms | 0,004 ms | ~40.000× |

**Umsetzung.** `lib2_artists.name_key` (= `normalize_name(name)`, nie angezeigt)
plus `idx_lib2_artists_name_key`, additive Spaltenmigration und ein Backfill in
`ensure_library_v2_schema`. Die Sorge aus §45.5 („Anpassung aller Schreibpfade")
war zu pessimistisch: es sind **vier** produktive INSERTs und **ein** UPDATE mit
`name=` — `autolink.py`, `native_enrich.py` und zweimal `importer.py`.

Der Python-Scan bleibt als Backstop, aber auf `WHERE name_key IS NULL`
eingeschränkt. Auf einer migrierten DB ist das ein Index-Seek über eine leere
Menge (`SEARCH … USING INDEX`), kein Scan; gleichzeitig findet er weiterhin
alles, was ein nicht angepasster Schreibpfad, ein direkter SQL-Insert oder eine
Ad-hoc-Reparatur hinterlässt. Der Backfill ist absichtlich **nicht** einmalig,
sondern läuft bei jedem Start über die schlüssellosen Zeilen — er hält damit den
Backstop leer, statt sich auf Vollständigkeit der Schreibpfade zu verlassen.

Backfill von 60.000 Zeilen: 318 ms, einmalig.

Nicht angefasst: das `external_ids LIKE '%…%'` derselben Funktion. Es ist
weiterhin ein Scan und feuert für Nicht-Spotify-Provider-IDs. Der Nutzer hat den
Umfang bewusst auf den Namensschlüssel begrenzt; der Punkt bleibt in
[issues.md §29.3](library-v2-issues.md#293-minor) offen geführt.

### 47.2 iss29-A08 — bestätigt: Liveness hing an der Gesprächigkeit der Stufe

Die Lease wurde ausschließlich von Progress-Callbacks verlängert. Der Befund
nannte 20 Alben; der tatsächlich kritische Pfad ist die **letzte** Stufe vor
`mark_done`: `precache_tag_cache` schlägt alle **50** Dateien an
(`tag_cache.py:179`). Fünfzig Tag-Reads über einen langsamen oder hängenden
Netzwerk-Mount überschreiten `STALE_AFTER_SECONDS = 600` ohne Mühe. Die Folge
ist nicht kosmetisch: ein anderer Prozess beansprucht die Migration, `mark_done`
scheitert mit „Bootstrap lease was lost before completion", und die vollständige
Migration läuft erneut.

**Umsetzung.** `_ClaimKeepalive` — ein Daemon-Thread, der die Lease alle 60 s
verlängert, solange der Lauf lebt, gestartet nach `try_claim` und in einem
`finally` vor `mark_done` gestoppt. Liveness folgt damit dem Lauf, nicht der
Stufe.

Zwei Eigenschaften, die den Unterschied zu einem naiven Heartbeat ausmachen:

- Der Beat geht über ein neues, absichtlich engeres `touch_claim`, das **nur**
  `heartbeat_at` schreibt. `heartbeat(...)` ohne Argumente hätte Stage,
  `current_count` und `total_count` auf NULL/0 zurückgesetzt — die UI hätte
  während der Migration ihren Fortschritt verloren. Der Resume-Checkpoint bleibt
  ebenfalls unberührt: ein Liveness-Beat ist kein Fortschritt und darf keine
  Walk-Position beschreiben, die kein Walk erreicht hat.
- Der Thread hört auf zu schlagen, sobald `touch_claim` `False` liefert — eine
  entwendete Lease darf nicht unter ihrem neuen Eigentümer weiterverlängert
  werden.

Bewusst in Kauf genommen: ein *hängender, aber lebender* Prozess hält seine
Lease jetzt unbegrenzt. Das ist richtig — zwei gleichzeitige Migrationen sind
schlimmer als eine blockierte —, und der Fall „Prozess tot" ist weiterhin über
`reclaim_abandoned_claim(process_started_at=…)` beim Neustart abgedeckt.

### 47.3 iss29-B10 — bestätigt: kein Bounce, eine Endlosschleife

Reproduziert, und deutlicher als erwartet: der Routen-Test hängt vitest auf,
statt zu scheitern. Die Weiterleitung terminiert nicht.

`getProfileHomePath` liefert die Home-Page zurück, ohne zu prüfen, ob das Profil
sie überhaupt öffnen darf. Jeder React-Routen-Guard leitet genau dorthin um,
wenn er den Zugriff verweigert — bei `home_page = library` (oder dem
Alt-Seiten-Id `library-v2`, den der Shell auf `library` normalisiert) und einem
`allowed_pages` ohne `library` schickt `/library` also auf `/library`.

Die Vanilla-Shell prüft das seit jeher (`navigateToPage`, `init.js:3186`:
`home !== currentPage && isPageAllowed(home)`); die React-Guards haben die
ungeprüfte Variante geerbt. Die Form ist unverändert Upstream — gegen `main`
verifiziert —, neu erreichbar wurde sie durch den `library-v2`-Alias.

**Umsetzung** (Nutzerentscheidung: zentral). `getProfileHomePath` fällt auf die
erste erlaubte Route der Manifest-Reihenfolge zurück, überspringt dabei
`artist-detail`/`label-detail` (Detailrouten brauchen eine Entity-ID in der URL
und sind nie eine Landeseite) und endet notfalls auf `/help`, das der
Berechtigungs-Gate der Shell bedingungslos erlaubt. Eine kleine Änderung an
einer geteilten Datei deckt damit alle ~10 Routen-Guards ab.

### 47.4 iss29-A09 — gemessen, kein Defekt

Der Befund beschrieb sich selbst als „bounded, nicht quadratisch, ungemessen".
Die fehlende Messung ist nachgeholt.

Working Set des Importers bei **100.000 Tracks / 20.000 Alben**: **29,6 MiB**
(`existing_files` + `track_map` + `album_map` + die beiden Album-Maps). Der
Zeilen-Walk selbst wächst nicht mit — `_legacy_rows` ist ein Keyset-Scan in
begrenzten Batches.

30 MiB rechtfertigen nicht, genau die Maps umzubauen, die doppelte INSERTs beim
Re-Import verhindern (§62). Der Punkt ist damit **geschlossen: gemessen, kein
Defekt** — nicht vertagt.

### Verifikation

Jeder der drei Fehler hat einen Regressionstest, der **vor** dem Fix
nachweislich fehlschlägt:

| Test | vorher |
|---|---|
| `test_existing_artist_is_found_without_scanning_the_table` (7 Fälle) | 4 rot |
| `test_new_artist_row_carries_its_normalized_key` | rot |
| `test_schema_migration_backfills_the_key_for_existing_rows` | rot |
| `test_rows_without_a_key_are_still_matched` | rot |
| `test_silent_post_import_keeps_the_claim_alive` | rot |
| `test_keepalive_never_moves_the_resume_checkpoint` | rot |
| `test_keepalive_stops_once_the_run_is_over` | rot |
| `getProfileHomePath` — Gate-Fälle (bridge.test.ts, 2 Fälle) | rot |
| `does not bounce a denied profile back into /library` | **hängt** |

Suiten nach dem Fix: `tests/library2` 1.233 grün; `tests/acquisition`,
`tests/imports`, `tests/repair_jobs`, `tests/search`, `tests/wishlist`,
`tests/quality` 1.768 grün / 3 skipped; webui-Typecheck und -Lint sauber.

Die vier roten webui-Dateien unter `src/routes/artist-detail/` sind
**vorbestehend** und keine Regression: sie scheitern an `localStorage` in dieser
Node-Umgebung (`--localstorage-file` nicht gesetzt) und fallen mit exakt
denselben 131 Fehlern aus, wenn man den Änderungssatz wegstasht.

---

## 48. Finaler Multi-Agent-Audit des Branch-HEADs (4. August 2026)

### 48.1 Scope und Release-Entscheid

Geprüft wurde `library-overhaul` auf `6c7066cbb9566118a20236bd9cb46d842589ebb2`
gegen `library-v2-guide.md`, `library-v2-features.md` und den bisherigen
Statusvertrag. Drei parallele Reviews untersuchten:

1. Import, Auto-Import, Autolink, File-Operationen und Bootstrap;
2. Automatic/Interactive Search, Quality Upgrades, History und native
   Repair-Tools;
3. React-UI, Shell/Redirects, API-Tool-Calls, Berechtigungen, Bulk-Aktionen und
   Browser-Gates.

**Release-Entscheid: nicht PR-/release-ready.** Der HEAD enthält einen
reproduzierbaren Datenverlust-Blocker und einundzwanzig weitere offene Findings.
Dieser Durchgang hat sie diagnostiziert und in
[Issues §30](library-v2-issues.md#30-finaler-multi-agent-audit-des-branch-heads-4-august-2026)
mit Korrekturverträgen festgehalten; Produktcode wurde in diesem Audit bewusst
nicht verändert.

Die 22 Findings sind Findings **am HEAD**, nicht 22 durch `library-overhaul`
eingeführte Regressionen. Der Auftrag schloss ausdrücklich auch das bereits
vorhandene Importverhalten ein; deshalb bleiben geerbte Probleme
release-relevant, werden aber im Folgenden getrennt attribuiert.

### 48.2 Verbindlicher Finding-Status

| Herkunft im Import-Cluster | Findings | Einordnung |
|---|---|---|
| Branch-neue Library-V2-Integration | I02, I03 | Der neue V2-Autolink konsumiert den unveränderten Legacy-Importkontext falsch |
| Aus `dev` geerbte gemeinsame Importpfade | I01, I04, I05, I06 | Root Causes bereits auf Basis `d0cb43db5` vorhanden; I04/I05 sind Auto Import, I06 ist Manual Import, I01 ist der gemeinsame File-Helper. Der Branch vergrößert bei I01/I04/I06 lediglich die V2-Downstream-Folgen |

`core/auto_import_worker.py`, `core/imports/routes.py` und
`core/imports/file_ops.py` sind zwischen Basis und HEAD byteidentisch. Die
Herkunft der Search-, Tool- und UI-Findings steht jeweils im verlinkten
Detail-Finding; die Tabelle hier bewertet weiterhin ihre Wirkung am HEAD.

| ID | Priorität | Status | Auswirkung |
|---|---|---|---|
| [iss30-I01](library-v2-issues.md#iss30-i01) | **Blocker** | Offen — reproduziert | ENOSPC/Move-Fehler kann die bisherige gute Datei vor dem Replacement löschen |
| [iss30-I02](library-v2-issues.md#iss30-i02) | Major | Offen — Real-SQLite-Repro | Der branch-neue V2-Autolink materialisiert Auto-Import-Tracks als künstliche Tracktitel-Alben |
| [iss30-I03](library-v2-issues.md#iss30-i03) | Major | Offen — reproduziert | Der branch-neue V2-Autolink ignoriert den Legacy-Provider; IDs können im falschen Provider-Namensraum landen |
| [iss30-I04](library-v2-issues.md#iss30-i04) | Major | Offen — reproduziert | Terminal abgelehnte Auto-Imports erscheinen als `completed` und werden nicht erneut verarbeitet |
| [iss30-I05](library-v2-issues.md#iss30-i05) | Major | Offen — Real-SQLite-Repro | Approve/Approve All läuft zurück nach `pending_review`, ohne Import |
| [iss30-I06](library-v2-issues.md#iss30-i06) | Major/Security | Offen — reproduziert | Manual Import akzeptiert beliebige absolute bzw. per Symlink entkommene Serverpfade |
| [iss30-S01](library-v2-issues.md#iss30-s01) | Major | Offen — End-to-end-Pfad bestätigt | Automatic Search findet Upgrades, der Import ersetzt aber nicht anhand der realen Qualitätsverbesserung |
| [iss30-S02](library-v2-issues.md#iss30-s02) | Major | Offen — Produktionspfad bestätigt | Track-Interactive-Search kann ein komplettes Album an eine Track-ID binden |
| [iss30-S03](library-v2-issues.md#iss30-s03) | Medium | Offen — API/UI-Vertrag bestätigt | Ein bereits laufender scoped Search-Job (409) wird als Fehler statt als Attach behandelt |
| [iss30-S04](library-v2-issues.md#iss30-s04) | Medium | Offen — Resolverpfade bestätigt | Upgrade Review und Automatic Search können verschiedene effektive Profile bewerten |
| [iss30-S05](library-v2-issues.md#iss30-s05) | Medium | Offen — Algorithmus bestätigt | Metadata Gap Filler wiederholt stets die ersten 500 Subjects; spätere Tracks verhungern |
| [iss30-S06](library-v2-issues.md#iss30-s06) | Major/Lifecycle | Offen — isoliert reproduziert | Prowlarr belegt die Python-3.14.6-Shutdown-Lücke; statisch bleiben 55 Default-Executor-Call-Sites |
| [iss30-U01](library-v2-issues.md#iss30-u01) | Major | Offen — Route bestätigt | Die als entfernt dokumentierte F-12-Import-Review ist per Deep Link voll mutierend erreichbar |
| [iss30-U02](library-v2-issues.md#iss30-u02) | Major | Offen — Requestmodell bestätigt | Rich Bulk Edit kann teilweise committen und gleichzeitig Totalausfall/stale UI melden |
| [iss30-U03](library-v2-issues.md#iss30-u03) | Major | Offen — Requestmodell bestätigt | Bulk-Bar invalidiert nach Teilerfolg nicht; erfolgreiche Writes bleiben unsichtbar |
| [iss30-U04](library-v2-issues.md#iss30-u04) | Major | Offen — Komponenten-Audit | `can_write=false` deaktiviert den Großteil der Library-Mutationen nicht |
| [iss30-U05](library-v2-issues.md#iss30-u05) | Medium | Offen — Pollingpfad bestätigt | Maintenance-Backendfehler erscheinen als grüner Null-Erfolg |
| [iss30-U06](library-v2-issues.md#iss30-u06) | Medium | Offen — Komponenten-Audit | Duplicates/Files/Source/History rendern API-Fehler als valide Empty-States |
| [iss30-U07](library-v2-issues.md#iss30-u07) | Medium/A11y | Offen — Komponenten-Audit | Dialoge besitzen keinen Focus-Trap, Escape- oder Focus-Restore-Vertrag |
| [iss30-U08](library-v2-issues.md#iss30-u08) | Test-Gate | Offen — Specs geprüft | Playwright erwartet entfernte Tabs, Buttons und die alte Artist-Detail-Route |
| [iss30-U09](library-v2-issues.md#iss30-u09) | Test-Gate | Offen — reproduziert | Format, ein TypeScript-Mock und Node-26-`localStorage` halten das vollständige WebUI-Gate rot |
| [iss30-U10](library-v2-issues.md#iss30-u10) | Test-Gate | Offen — isoliert reproduziert | Python-Pin-Test verwechselt einen längeren Kommentar mit fehlendem Chat-Routing |

### 48.3 Ergebnis entlang der angefragten Produktverträge

| Vertrag | Ergebnis am HEAD |
|---|---|
| Import und Autolink | **Nicht freigabefähig:** V2-Integrationsfehler I02/I03 sowie die geerbten gemeinsamen Importfehler I01/I04–I06 sind am HEAD offen |
| `/library-v2` und Search-Weiterleitungen | **Verifiziert:** Alias erhält Querystring; Library-Treffer gehen über `?artist=`, Provider-Treffer über den Stub nach `?discover=` |
| Tool-Calls in der UI | **Teilweise korrekt:** Native Registry und Repair-Fix-Pipeline stimmen; Error-Truthfulness, Write-Gating und mehrere Pollingpfade nicht |
| Automatic Search wie Lidarr | **Nur teilweise:** Upgrade-Scan läuft vor Wishlist-Verarbeitung und respektiert Profil/Cutoff bei der Queue-Bildung; der reale Import vergleicht bzw. ersetzt nicht zuverlässig nach besserer Qualität |
| Interactive Search | **Teilweise:** Fan-out und Outcome-Polling stimmen; Album-Ergebnisse im Track-Dialog verletzen den Entity-Vertrag, Prowlarr gefährdet den Loop-/Prozess-Shutdown |
| Search History / Pipeline History | **Library-V2-F-10 implementiert:** Feed und Korrelation sind vorhanden. Separat kann die geerbte Auto-Import-History abgelehnte Dateien fälschlich als abgeschlossen journalisieren (I04) |
| Bulk Edits | **Nicht zuverlässig:** Teilerfolge sind nicht atomar und bleiben in zwei UI-Pfaden stale |
| Metadata Gap Filler und angepasste Tools | **Teilweise:** Nativer Override aktiv, aber Gap-Filler ohne Fortschritt nach Subject 500 und mehrere Toolfehler werden verschluckt |
| Read-only- und Fehler-UI | **Nicht erfüllt:** Mutationen bleiben aktiv; Backendfehler können als Erfolg oder leerer Datenbestand erscheinen |

### 48.4 Verifikation am unveränderten Produktcode

| Prüfung | Ergebnis |
|---|---|
| Relevante Backend-Suiten: `tests/library2 imports acquisition search repair repair_jobs wishlist watchlist quality` | **3.083 passed, 3 skipped** |
| Import-/Pipeline-/Auto-Import-Fokus des Teilreviews | **318 passed**: 119 Import/Pipeline/Routes/Auto, 78 Autolink/Multi-file, 121 Importer/Bootstrap |
| Vollständige Python-Suite, `--maxfail=1` | **6.853 passed, 3 skipped, 2 deselected**, dann iss30-U10; isoliert identischer False-Negative |
| Vollständige Python-Suite ohne exakt iss30-U10 | Erreicht **67 %**, stoppt dann reproduzierbar in iss30-S06; 45-s-Faulthandler bestätigt `Runner.close`/Default-Executor |
| Isolierter Prowlarr-Test / minimale Runtime-Probe | Test terminiert nach **15 s** nicht; `asyncio.run(asyncio.to_thread(lambda: []))` überschreitet **20 s** unter Python 3.14.6 |
| Full Suite ohne U10 und die 31 Prowlarr-Hardening-Tests aus S06 | **12.675 passed, 3 skipped, 3 deselected**, 674 Warnungen in 644,21 s |
| Aktuelle React-Routen: Library, Search, Shell und Artist-Detail-Redirect | **60 Dateien / 696 Tests passed** |
| Vollständige Vitest-Suite | **1.978 passed, 131 failed** in vier Legacy-Artist-Detail-Dateien wegen undefiniertem Node-26-`localStorage` |
| `npm run build` | **passed**; 1,56-MB-Hauptchunk mit Größenwarnung |
| `npm run check` plus separater Typecheck | **failed**: drei Formatdateien; TS2322 im Library-Routen-Mock; Oxlint zusätzlich 298 Warnungen |
| Playwright | **35 Tests in 7 Dateien kompilieren/listen**; kein Live-Lauf ohne Server auf `localhost:8008`, zudem iss30-U08-Stale-Specs |
| Failure-Injection / Real-SQLite-Proofs | iss30-I01, I02, I03, I04, I05 und I06 reproduziert |
| `git diff --check` | **passed** |

Die 3.083 grünen Fachtests sind wertvolle Negativnachweise, widerlegen die
gezielten Reproduktionen aber nicht: Die problematischen Failure-, Payload- und
Partial-Success-Formen fehlen in der bisherigen Testsuite.

### 48.5 Reihenfolge zum erneuten Schließen des Gates

1. **P0:** iss30-I01 atomar und failure-safe korrigieren; erst danach weitere
   Replacement-/Upgrade-Tests ausführen.
2. **P1 Import:** I02–I06 mit gemeinsamen Import-Context-Helpern,
   exactly-once Approval und serverseitigem Staging-Containment schließen.
3. **P1 Search:** S01/S02 als echte Entity-/Quality-Verträge bis zum Import
   transportieren; S06 ohne Loop-Default-Executor schließen; danach S03/S04
   und Gap-Filler-Paging.
4. **P1 UI:** U01 entfernen; Bulk-Partial-Success, `can_write` und ehrliche
   Tool-/Maintenance-Fehler zentralisieren.
5. **Gate:** U08–U10 reparieren, vollständige Python-/Vitest-/Typecheck-Gates
   grün ausführen und die aktualisierte Playwright-Suite gegen eine reale
   Testinstanz laufen lassen.

---

## 49. Remediation- und Übergabecheckpoint (4. August 2026)

### 49.1 Gesicherte Commits

| Commit | Inhalt | Verifikation |
|---|---|---|
| `3535e3d73` | U01–U10: entfernte Import-Review-UI, fail-closed Rechte, Bulk-Teilerfolge, ehrliche Tool-/Maintenance-Fehler, Dialoge, Search-/Artist-Redirects und aktuelle E2E-Verträge | 144 Vitest-Dateien / 2.066 Tests; Library 37 / 255; `npm run check`; Build; 25 Chat-Tests |
| `56738ab80` | I02/I03 sowie S01/S04/S05: provider-sicherer Autolink, zentrale Upgrade-/Fallback-/Cutoff-Entscheidung, live effektive Profile und rotierendes 500er-Meta-Gap-Paging | 112 fokussierte Tests; Agent-Gate zusätzlich 33 angrenzende Tests |
| `f20c7b5f3` | S06/U10 und Test-Runtime: getrennte Slow-/Control-Executors ohne Loop-Default-Executor, robuste Throttle-/Chat-/Node-26-Gates | 86 Executor-/Torrent-/Usenet-Tests plus Soulseek-Throttle |
| `778c19cf3` | I01/I04–I06 und iss31 Upgrade-Härtung: atomare File-Ops, exactly-once Approval, Staging-Containment, serverseitiger Upgrade-Intent, Transform-before-compare, per-Track-Lock/CAS und Candidate-Kind-Bindung | 221 Security-/Pipeline-Tests; 781 Import-/Chat-Tests; 1 Import-UI-Test |

Alle Commits liegen auf `library-overhaul`. Graphify wurde auf ausdrücklichen
Wunsch nicht benutzt.

### 49.2 Herkunft und Cherry-Pick-Grenze

Auto Import, atomare File-Ops, Manual-Import-Containment, S06, U10 und der
Node-26-Testfallback beheben aus `dev` geerbte Fehler; sie sind keine
Library-V2-Features. Der Branch `fix/pre-library-v2-audit-bugs` basiert direkt
auf `dev` und enthält mit `c1fc84cd7` genau einen geprüften Commit ohne
Library-V2-Abhängigkeiten. Auf `library-overhaul` sind dieselben gemeinsamen
Fixes bereits in `f20c7b5f3`/`778c19cf3` enthalten; ein doppelter Cherry-pick
ist dort weder nötig noch sinnvoll.

### 49.3 Aktueller Gate-Stand

| Prüfung | Ergebnis |
|---|---|
| Vollständige WebUI-Vitest-Suite | **2.066 passed** |
| Library-V2-Vitest | **255 passed** |
| `npm run check` / `npm run build` | **passed**; nur bestehende Warnungen bzw. Chunk-Hinweis |
| Playwright Discovery | **38 Tests in 7 Dateien**; kein Live-Browserlauf dokumentiert |
| Autolink/Profile/Upgrade-Scan/Gap-Paging Fokus | **112 passed** |
| Executor + Torrent-/Usenet-Adapter Fokus | **86 passed**; kein Loop-Default-Executor |
| Import-/Chat-Suite | **781 passed** |
| Upgrade-Intent/Pipeline/Candidate/Grab Fokus | **221 passed** |
| Separater `dev`-Branch | **866 Python + 1 UI passed**, exakt 1 Commit |
| Python Full Suite vor den letzten Backend-Änderungen | **12.736 passed, 5 failed, 3 skipped, 2 deselected** |

Der Full-Suite-Lauf ist kein finales Gate: Er lief parallel zu späteren
Änderungen. Ein Materialize-Test wurde durch den danach committed Autolink-Fix
adressiert; drei Manual-Import-Tests verwenden Pfade außerhalb der nun
erzwungenen Staging-Grenze; der Soulseek-Test sah noch den inzwischen entfernten
10-ms-Polling-Helper. Alle fünf müssen im abschließenden Lauf neu bewertet und
nicht einfach ausgeblendet werden.

### 49.4 Verbleibende Reihenfolge für den nächsten Chat

1. Qualifizierten Metadata-Provider durch `/api/download`,
   `/api/download/matched`, Match-Suggestions und den Library-V2-Client bis in
   den Importkontext propagieren. Der Autolink fällt bis dahin sicher auf Namen
   zurück, kann bei Namensdrift aber legitime IDs verlieren.
2. Danach vollständige Python-Suite, vollständige WebUI-Gates, relevante
   Playwright-Live-Flows und `git diff --check` erneut ausführen.

---

## 50. Nezrekas Review von PR #1062 — Arbeitsliste (10. August 2026)

Nezreka hat den Branch am 10. August 2026 auf seiner realen Bibliothek getestet
(4.979 Artists / 69.296 Alben / 307.885 Tracks / 9 GB DB) und die Befunde als
PR-Kommentar hinterlegt. Diagnosen, Verifikationsstand und Korrekturverträge
stehen in [library-v2-issues.md §32](library-v2-issues.md). Hier steht nur der
Status.

Sein Urteil zur Seite selbst ist positiv; `schema.py` hat er gegen das alte
Schema geprüft und für richtig befunden. Die Blocker sind Migration und
Enrichment: „just want the migration sorted and the enrichment not to
regress."

### 50.1 Status

| ID | Kurzfassung | Status |
|---|---|---|
| iss32-M01 | Zeitgesteuertes Fortschrittslog für den Bootstrap-Import | **Erledigt** — `_ProgressTicker`, alle 30 s, inkl. „no progress in …“ |
| iss32-M02 | Enrichment-Worker/Automation Engine während der Migration pausieren | **Erledigt** — `MigrationPauseSupervisor` + `pause_for_migration` in der Automation Engine |
| iss32-M03 | Migration von der Startup-Reihenfolge entkoppeln | **Erledigt** — `run_backfills=False` im Init, Konvergenz im Daemon-Thread |
| iss32-M04 | WAL-Checkpoint während der Migration | **Erledigt** — `core/library2/wal.py`, alle 20 Batches + Abschluss-Checkpoint |
| iss32-M05 | `backfill_editions`/`recompute_wanted` ohne Zwischen-Commit | **Erledigt** — Keyset-Batches mit Commit in editions/stable_ids/wanted, auch im Importer-Finalize |
| iss32-M06 | Schema-Init ist eine einzige Transaktion + Python-weiter Init-Lock | **Teilweise** — die unbegrenzte Arbeit ist raus; die Init-Transaktion bleibt bewusst eine |
| iss32-M07 | `PRAGMA quick_check` über die ganze DB bei jedem Start mit Sidecars | **Erledigt** — `quick_check` im Daemon-Thread |
| iss32-M08 | Partieller Index unbenutzbar → `backfill_editions` quadratisch | **Erledigt** — Index-Prädikat in der Abfrage mitgeschrieben; 128 Zeilen/s und fallend → flach ~9.000 Zeilen/s |
| iss32-E01 | `resync_entity_from_legacy` verdrahten | **Erledigt** — Trigger + Dirty-Queue + Drainer; Bios und Provider-IDs ergänzt |
| iss32-E02 | Native Artists erreichen nur `native_enrich` statt aller zwölf Worker | **Erledigt, mit benannter Restlücke** — nativer Sweep-Job + `enrichment_depth`; Provider-Bios rein nativer Artists erst in Stufe 2 |
| iss32-E03 | `/api/library/artists` liest weiterhin Legacy | **Erledigt** — liest lib2, Antwortform und ID-Vertrag erhalten |
| iss32-T01 | Sind `lib2_artists`/`lib2_albums`/`lib2_tracks` Endzustand oder Übergang? | **Entschieden 10. August 2026** — Tabellen sind Endzustand, Doppelung nicht. Alle Worker und alle Tools werden auf lib2 umgeschrieben, Legacy verschwindet. Stufenplan in [issues §32.3.1](library-v2-issues.md); Produzenten-Umbau (Stufe 2) bewusst in einem eigenen PR |
| iss32-S01 | `mbid_mismatch_detector` nicht in `PRESERVED_RETIRED_FINDING_IDS` | **Erledigt** — als nativer V2-Job zurückgeholt, beide Fix-Handler wieder da |

### 50.1a Umsetzungsstand Block 1 (Migration) — 10./11. August 2026

| ID | Umsetzung | Ort |
|---|---|---|
| iss32-M03 | `ensure_library_v2_schema(connection, run_backfills=False)`; die fünf Voll-Backfills wandern nach `run_library_v2_backfills` und werden aus `_autostart_library_v2_bootstrap_import` heraus im Daemon-Thread gefahren | `core/library2/schema.py`, `database/music_database.py:1219`, `web_server.py` |
| iss32-M05 | `backfill_editions`, `backfill_stable_ids` und `recompute_wanted` laufen als Keyset-Walks in Batches mit Commit je Batch; die Finalize-Schritte des Importers reichen Connection + Progress durch | `core/library2/editions.py`, `stable_ids.py`, `wanted.py`, `importer.py:1571 ff.` |
| iss32-M01 | `_ProgressTicker` — Logausgabe alle 30 s unabhängig von der Callback-Frequenz, mit „no progress in …" wenn der Zähler steht; Finalize meldet Sub-Stages (`finalizing:editions`) statt nur „5/7" | `core/library2/bootstrap.py` |
| iss32-M04 | `checkpoint_wal` / `PeriodicCheckpointer`, alle 20 Batches `PRAGMA wal_checkpoint(TRUNCATE)`, plus ein Abschluss-Checkpoint nach dem Lauf | `core/library2/wal.py` |
| iss32-M02 | `MigrationPauseSupervisor` pausiert die 16 Worker und die Automation Engine, solange die Claim-Row lebt oder eine In-Process-Konvergenz läuft; setzt nur fort, was er selbst pausiert hat | `core/library2/migration_gate.py`, `core/automation_engine.py` |
| iss32-M07 | `PRAGMA quick_check` läuft in `_run_sidecar_health_check` in einem Daemon-Thread statt auf dem Startpfad | `database/music_database.py:187 ff.` |
| iss32-M08 | Die Prädikate des partiellen Index werden in der Abfrage mitgeschrieben, damit SQLite ihn überhaupt verwenden darf | `core/library2/editions.py:275/288` |
| iss32-M06 | Teilweise: die unbegrenzte Arbeit ist aus der Init-Transaktion draußen. Die Transaktion selbst bleibt eine — bewusst, siehe Korrekturvertrag | — |

**Nachgemessener Zusatzbefund:** Batch-Commits allein reichen nicht. Bei 20.000
Tracks wartete ein konkurrierender Writer trotz Commit je Batch weiterhin die
volle Laufzeit, weil die Schleife den Lock innerhalb von Mikrosekunden wieder
nimmt und SQLites Busy-Handler auf 100-ms-Polling zurückfällt. `PeriodicCheckpointer`
pausiert deshalb nach jedem Commit um einen gedeckelten Bruchteil der
Batch-Dauer (15 %, max. 50 ms).

**Messung bei Nezrekas Skala** (synthetischer Katalog: 4.979 Artists / 69.296
Alben / **307.885 Tracks**, 600-MB-DB). Der Schritt, in dem seine Migration
stand — `backfill_editions`:

| | vorher | nachher |
|---|---:|---:|
| Durchsatz bei 23k materialisierten Zeilen | 323 Zeilen/s | ~9.000 Zeilen/s |
| Durchsatz bei 58k | 128 Zeilen/s, weiter fallend | ~9.000 Zeilen/s |
| Durchsatz bei 230k | ~35 Zeilen/s (live gemessen) | ~9.000 Zeilen/s |
| Restliche 246.185 Zeilen | hochgerechnet ~2 h | **27 s** |

Der Durchsatz ist nach dem M08-Fix **flach über die gesamte Tabelle** — genau
das war vorher nicht so, und genau deshalb sah der Lauf wie ein Hänger aus.

**Sperrverhalten bei voller Skala**, mit einem konkurrierenden Writer, der tut
was Config-Save und Worker taten (kleines UPDATE alle 50 ms, `busy_timeout`
30 s wie in der App). Beide Seiten laufen **mit** dem M08-Fix, damit die
Messung das Sperrverhalten isoliert und nicht nur M08 nochmal zeigt:

| 307.885 Tracks / 69.296 Alben, 799-MB-DB | vorher (eine Transaktion) | nachher |
|---|---:|---:|
| Dauer | 36,0 s | 37,6 s |
| WAL-Spitze | **227 MB** | **8 MB** |
| durchgekommene Fremd-Writes | **5** | **430** |
| mit `database is locked` gescheitert | 1 | **0** |
| längste Wartezeit eines Fremd-Writes | **30,0 s** (= Timeout, also aufgegeben) | 1,9 s |

Die 227 MB WAL und der eine Writer, der nach 30 s aufgibt, sind exakt Nezrekas
Fehlerbild — 135 MB und weiter wachsend, „Config DB save failed after 6
attempts". Der Preis dafür sind 1,6 s mehr Laufzeit.

Messung bei 20.000 Tracks / 2.000 Alben (`scale_bench.py`, konkurrierender
Writer mit denselben 30 s `busy_timeout` wie die App):

| | vorher | nachher |
|---|---:|---:|
| Dauer `backfill_editions` | 19,3 s | 22,4 s |
| WAL-Spitze | 12,5 MB | 5,8 MB |
| durchgekommene Fremd-Writes | 2 | 25 |
| längste Wartezeit eines Fremd-Writes | 19,2 s | 6,1 s |
| `ensure_library_v2_schema` auf dem Startpfad | 19,4 s | 0,1 s |

Regressionstests: `tests/library2/test_migration_hardening.py` (20 Tests).

### 50.1b Umsetzungsstand Block 2 + 3 (Enrichment, Tabellen, Small) — 11. August 2026

| ID | Umsetzung | Ort |
|---|---|---|
| iss32-E01 | Trigger auf `artists`/`albums`/`tracks` mit `WHEN`-Klausel auf genau den gespiegelten Spalten → `lib2_legacy_dirty` → `MirrorDrainer` (30 s) plus Sofort-Drain nach dem manuellen UI-Enrich. **Kein Worker angefasst** — erfasst alle 137 Schreibstellen. `resync_*_from_legacy` mitgewachsen: Provider-IDs (`external_ids` + die promoteten Spalten) und Provider-Bios (`enrichment`) wurden vorher **gar nicht** gespiegelt | `core/library2/legacy_mirror.py` (neu), `enrich.py`, `schema.py`, `web_server.py` |
| iss32-E03 | `/api/library/artists` liest `legacy_api_artists_page` aus lib2. Antwortform Feld für Feld erhalten; `id` bleibt bewusst die Legacy-ID (Consumer geben sie an `navigateToArtistDetail`), `lib2_artist_id` kommt dazu. Fällt auf den Legacy-Reader zurück, solange der Katalog leer ist | `core/library2/queries.py`, `web_server.py:9889` |
| iss32-E02 | Nativer Sweep-Job statt Legacy-Shim: `native_enrichment_sweep` läuft `enrich_native_entity_all_services` über native Artists ohne Katalog-Provider-ID, in Batches. Der native Pfad konnte das längst — es hat ihn nur nie jemand periodisch ausgelöst. Zusätzlich `enrichment_depth: full\|native` im Artist-Read, damit die UI nicht länger beide als „matched" ausgibt | `core/repair_jobs/native_enrichment_sweep.py` (neu), `queries.py` |
| iss32-T01 | Beantwortet, siehe [issues §32.3.1](library-v2-issues.md) | Doku |
| iss32-S01 | `mbid_mismatch_detector` zurück — **als nativer V2-Job**: Subjects aus `active_file_subjects`, Pfade über `resolve_lib2_path`, Entity-IDs `lib2:<track_id>`; beide Fix-Handler (`mbid_mismatch`, `album_mbid_mismatch`) wieder da. Die Tag-IO ist unverändert aus Nezrekas Original übernommen — sie hat nie Legacy-Tabellen angefasst | `core/repair_jobs/mbid_mismatch_detector.py`, `core/repair_worker.py`, `core/repair_jobs/__init__.py` |

**Bewusst offen und benannt, nicht vergessen:** die Provider-**Bios**
(Last.fm-Bio/Listeners/Similar, Genius-Description, Discogs-Members) stehen in
`lib2_artists.enrichment` und werden ausschließlich von den Last.fm-, Genius-
und Discogs-Workern geschrieben — gegen Legacy-Zeilen. Ein Artist **mit**
Legacy-Zeile bekommt sie jetzt über den Spiegel (E01). Ein rein nativer Artist
bekommt sie erst, wenn diese drei Worker nativ sind (Stufe 2). `enrichment_depth`
sagt das an der Schnittstelle, statt Parität zu behaupten.

Regressionstests: `tests/library2/test_legacy_mirror.py` (13 Tests — Trigger,
Drain, Artwork/Genres/Bios/Provider-IDs, E03-Antwortform).

### 50.2 Reihenfolge (aktualisiert nach der Diagnose vom 10. August, abends)

M03 ist diagnostiziert — die Ursache steht in [issues §32.1.0](library-v2-issues.md).
Damit fällt der ursprüngliche Schritt 1 weg und die Migrationsarbeit ordnet sich
neu, entlang der Ursachenkette statt entlang der Symptomliste:

1. **iss32-M03 + iss32-M05 zuerst, zusammen.** Beide sind dieselbe Ursache aus
   zwei Richtungen: unbegrenzte Arbeit in einer offenen Transaktion. Getrennt
   gefixt bringt jede Hälfte für sich wenig — der Startpfad bliebe blockiert
   bzw. der Writer weiter minutenlang gehalten.
2. **iss32-M01.** Ohne zeitgesteuertes Fortschrittslog ist jeder weitere Lauf
   wieder blind — insbesondere der Verifikationslauf für Schritt 1.
3. **iss32-M04.** Braucht die Commit-Fenster aus M05; vorher hat ein Checkpoint
   nichts zu tun.
4. **iss32-M02.** Richtig und vom Nutzer gewollt, aber nach 1–3 nicht mehr die
   Rettung, sondern die Absicherung.
5. **iss32-M06/M07** als Aufräumarbeiten am Startpfad.
6. **Migrationslauf gegen eine Kopie einer möglichst großen DB.** Die eigene
   Test-DB reicht nachweislich nicht.
7. **iss32-T01 ist entschieden** ([issues §32.3.1](library-v2-issues.md)):
   Endzustand lib2, alle Worker und Tools wandern, Legacy verschwindet — der
   Produzenten-Umbau aber in einem eigenen PR. Für diesen PR bleibt Stufe 1:
   **iss32-E01** (Spiegel) → **iss32-E03** (letzter Legacy-Leser) →
   **iss32-E02** als Übergangslösung, deren Ablaufdatum Stufe 2 ist.
8. **iss32-S01** zum Schluss.

### 50.3 Was dieser Review *nicht* bemängelt hat

Explizit positiv bzw. unbeanstandet: die Library-V2-Seite selbst, die
`schema.py`-Begründung (gegen das alte Schema gegengeprüft), sowie
`lib2_track_artists` und `lib2_track_files` als sachlich notwendige neue
Tabellen. Diese Punkte sind nicht erneut aufzurollen.

### 50.4 Übergabe — was nach dem 11. August 2026 offen ist

Alle neun Punkte aus Nezrekas Review sind erledigt und in `57d238ea5` gepusht.
Was hier steht, ist der Rest — sortiert nach Verbindlichkeit, nicht nach Größe.

#### 50.4.1 Zugesagt und nicht gebaut — **erledigt am 11. August 2026** (`7aacec830`)

**Der Divergenz-Check fehlte.** [issues §32.3.1](library-v2-issues.md) gibt
Nezreka drei Zusagen; Zusage 2 lautet, dass die Abweichung zwischen Legacy und
lib2 in den gespiegelten Feldern eine *Kennzahl* im vorhandenen read-only
Integritätsreport wird (Erwartungswert 0, jeder andere Wert ein Bug mit
Zeilennummern). Umgesetzt als `lib2_mirror_divergence` je Zeile plus
`observed.mirror_checked/mirror_pending/mirror_dangling`; Details in
[issues §32.5](library-v2-issues.md) (iss32-T01a).

Die verglichene Feldmenge ist bewusst **keine zweite Liste**: `enrich.py` ist
jetzt deklarativ (`MIRROR_SPECS`), Spiegel und Audit lesen dieselbe Erklärung.
Zwei Befunde kamen erst aus der Messung gegen die echte Bibliothek, nicht aus
den Tests — sie sind der Grund, warum Regel 5 aus dem Leitfaden auf einer
echten DB besteht:

- **Der erste Lauf hat nichts gemessen.** `artists.id` ist `TEXT`, der lib2-Link
  `INTEGER`; SQLite gleicht das über die Spalten-Affinität aus, ein Python-Dict
  nicht. 170 von 170 Zeilen wurden als „Legacy-Zeile fehlt" gemeldet
  (iss32-T01b).
- **Die Kennzahl stand danach auf 156 von 170 — und konnte 0 nie erreichen.**
  Die Trigger aus iss32-E01 sehen nur, was *nach* ihrer Installation geschrieben
  wird; der gesamte Rückstand der zwölf Worker enqueued nichts. Deshalb gehört
  `reconcile_divergent` dazu: ein begrenzter, fortsetzbarer Sweep, der den
  Rückstand in die **vorhandene** Queue stellt (iss32-T01c). Gegen einen
  Snapshot der echten DB über den Produktivpfad nachgewiesen: 156 → 0,
  Endzustand 170 geprüft / 0 offen / Queue leer.

#### 50.4.2 Zwei Fehler in `native_enrichment_sweep`, gefunden und behoben

Beide beim Nachprüfen der eigenen Arbeit gefunden, nicht durch einen Test:

- **`default_enabled = False`** — ein Kategorienfehler. Die übrigen Jobs des
  Pakets sind Opt-in-Diagnosen; wenn sie nie laufen, ist nichts kaputt. Dieser
  ersetzt zwölf *dauerhaft laufende* Worker. Ausgeliefert im Zustand „aus"
  beantwortet er „v2 should get filled the same way v1 does now" mit einem
  Schalter, den niemand umlegt — die gemeldete Regression wäre schlicht
  geblieben. Jetzt `True`, mit Begründung am Feld.
- **`result.fixed += 1`** statt `result.auto_fixed`. `JobResult` ist eine
  schlichte Dataclass: die Zuweisung legt stillschweigend ein Attribut an, das
  niemand liest. `RepairWorker` summiert und loggt ausschließlich
  `auto_fixed` — der Job hätte also korrekt angereichert und bei **jedem** Lauf
  null gemeldet, also ausgesehen wie ein Job ohne Arbeit. Kein anderer Job im
  Repo benutzt `fixed`; der Name war erfunden.

Abgesichert durch `tests/repair_jobs/test_job_result_fields.py`: ein statischer
Guard über alle Job-Dateien, der jede Zuweisung an ein nicht existierendes
`JobResult`-Feld meldet. Eine Verhaltensprüfung hätte jeden Job ausführen
müssen, um dasselbe zu leisten.

#### 50.4.3 Gebaut, aber ohne Oberfläche

`enrichment_depth` (`full` | `native`) steht im Artist-Read. Nezrekas
Beschwerde war „both show as matched so you can't tell them apart" — die API
sagt es jetzt, die UI zeigt es weiterhin nicht. Kleine Frontend-Änderung.

#### 50.4.4 Legacy-Löschung: Bereitschaftsmessung vom 11. August 2026

Der Nutzer hat freigegeben, Legacy-Code zu entfernen, *sobald* sicher ist, dass
alles umgestellt ist. Das ist es nicht. Produktivcode, ohne `tests/`:

| | Anzahl |
|---|---:|
| Lesestellen auf `artists`/`albums`/`tracks` | **656** |
| Schreibstellen | **238** |
| beteiligte Dateien | **65** |

Entscheidend sind nicht die Zahlen, sondern **wer** noch schreibt: der
Media-Server-Scan (`database/music_database.py:7060/7238/7521`) ist der
einzige Weg, auf dem überhaupt Zeilen entstehen, und die 16 Enrichment-Worker
haben zusammen 334 Legacy-Statements und **null** lib2-Bezüge. Eine Löschung
heute nimmt der App die Datenaufnahme. Die Reihenfolge ist erzwungen:
Produzenten → Leser → löschen.

**Auch der eine Kandidat, der als tot galt, ist es nicht mehr.** Der PR-Kommentar
vom 5. August nennt die alte Library-React-Schicht importlos. Nachgemessen am
11. August (Tests nicht mitgezählt): `-library.types` 3 Importe,
`-library.export` und `-library.watch-all` je 2, `-library.api` und
`-library.helpers` je 1. Nur `-library.live` ist wirklich importlos.
Vermutlich Folge des Upstream-Syncs. **Vor dem Löschen erneut messen, nicht dem
alten Kommentar glauben.**

**Die Ratsche ist gebaut** (`eee086f2d`, 11. August 2026).
`tests/library2/test_legacy_usage_ratchet.py` samt `legacy_usage.py` und
`legacy_usage_baseline.json`. Entscheidung des Nutzers: **die Lesestellen werden
mitgeratscht, nicht nur die Schreibstellen** — Legacy soll vollständig
verschwinden. Beide Zahlen sind fixiert, beide haben das Ziel 0.

Basiswerte nach der Zählregel dieses Tests: **647 lesend / 239 schreibend**.
Die kleine Abweichung zu 656/238 oben ist die Zählregel selbst: die Ratsche
schließt die Migrations-Hilfstabelle `artists_new` aus und zählt ein `DELETE`
einmal als Schreibstelle statt zusätzlich als das `FROM`, das darin steckt. Der
Test schlägt **in beide Richtungen** fehl: Wachstum ist die Regression,
derentwegen er existiert; ein Rückgang schlägt ebenfalls fehl, mit der
Anweisung, den Basiswert im selben Commit zu senken — nur so bleibt die Ratsche
stramm und die Zahl wird zum Fortschrittsbalken.

#### 50.4.4.1 Wo die Zahl als Nächstes fällt

Die Zahl sinkt erst, wenn das SQL in `database/music_database.py` verschwindet,
und das setzt voraus, dass seine Aufrufer weg sind. Gemessen am 11. August:

- `MusicDatabase.search_artists` hat genau **einen** Produktiv-Aufrufer:
  `core/search/orchestrator.py:_build_db_artists`. Dort wird Legacy zuerst
  gesucht und lib2-native Artists werden hineingemischt — auf lib2 umgestellt
  entfällt das Mischen ersatzlos.
- `api_get_artist` hat genau einen: `api/library.py`.

**Falle, bevor jemand das für einen Dreizeiler hält:** `api/library.py` kann
*nicht* einfach auf `legacy_api_artists_page` umgestellt werden. Der Endpunkt
übergibt `profile_id`, die lib2-Seite kennt keine Profil-Skopierung — der
`watchlist=`-Filter eines Nicht-Admin-Profils würde damit stillschweigend seine
Bedeutung ändern (Leitfaden §2.6). Das ist eine Entscheidung, kein Austausch.

#### 50.4.4.2 Die Media-Server-Frage zerfällt in zwei Hälften

Nur eine davon ist offen:

- **Der Scan, der Zeilen erzeugt** (`music_database.py:7083/7126/7261/7304/7544`)
  — dort geht es darum, ob ein Media-Server überhaupt lib2-Zeilen anlegen darf.
  Haltung des Nutzers: **nein**. Wird Nezreka direkt gefragt, bevor gebaut wird.
- **Der Metadata-Update-Worker** war nur ein **Leser** und ist umgestellt
  (`b32395159`). Er ist der einzige Pfad, auf dem überhaupt etwas zum
  Media-Server zurückfließt: Genres aus der gespeicherten Artist-Zeile und deren
  Spotify-ID als Abkürzung. Fotos und Album-Artwork kommen immer direkt von
  Spotify, und `update_artist_biography` schreibt keine Bio, sondern stempelt
  nur ein `-updatedAt`-Datum in die Summary des Servers — die gesammelten
  Last.fm-/Genius-/Discogs-Bios haben SoulSync also nie verlassen.

#### 50.4.4.3 Was der Spiegel noch fallen lässt (gemessen 11. August 2026)

Beim Prüfen, ob `MIRROR_SPECS` vollständig ist, kam eine Lücke heraus, die die
Divergenz-Kennzahl selbst **nicht** sehen kann: sie vergleicht, was die
Deklaration nennt. Eine Spalte, die in der Deklaration fehlt, fehlt damit auch
in der Messung. Genau so fiel `artists.lastfm_playcount` durch — Last.fm
schreibt sie, nichts trug sie nach lib2, und weder Test noch Kennzahl sagten es.
Behoben und in beide Richtungen festgenagelt
(`tests/library2/test_mirror_declaration.py`).

Der Bestand danach: von 63 Legacy-Artist-Spalten sind 30 gespiegelt, von 76
Album-Spalten 19, von 74 Track-Spalten 19. Der größte Teil des Rests ist
**Buchhaltung, die nicht gespiegelt werden soll** (`*_match_status`,
`*_last_attempted`, Struktur- und Fremdschlüssel, `server_source`). Aber nicht
alles davon:

| Entität | Noch nicht gespiegelte Nutzdaten |
|---|---|
| Artist | `soul_id` (eine Provider-Identität, die `SERVICES` nicht abbildet) |
| Album | `lastfm_listeners/playcount/tags/wiki`, `discogs_genres/styles/label/catno/country/rating/rating_count`, `bandcamp_id/tags/label`, `soul_id`, `record_type`, `release_date`, `copyright` |
| Track | `lastfm_listeners/playcount/tags`, `genius_description/url`, `bandcamp_id/tags/label`, `soul_id`, `track_artist`, `year`, `disc_number` |

**Der Blocker ist Schema, nicht Fleiß:** nur `lib2_artists` hat eine
`enrichment`-Spalte. `lib2_albums` und `lib2_tracks` haben keine, also braucht
die Album-/Track-Nutzlast erst eine Spalte, bevor sie deklariert werden kann.
Ein Teil hat schon ein Zuhause (`lib2_albums.release_date`,
`lib2_tracks.copyright`, `lib2_tracks.disc_number`) und ist rein deklarativ
nachzutragen. Gemessen an Nezrekas Anforderung („i don't want to lose any
enrichment functionality or data") gehört das vor die Legacy-Löschung, nicht
danach.

#### 50.4.4.4 Stufe 2 hat begonnen — Reihenfolge und Stand (11. August 2026)

Nutzerentscheidung: `enrichment`-Spalten für Albums/Tracks **ja** (erledigt,
`eb69f18fb`), Profil-Skopierung von `api/library.py` **parkiert**, dann **alle
Worker und Tools** auf lib2, **danach alle Lesestellen**.

Der Umbau der 16 Worker hatte eine Voraussetzung, die vorher niemand benannt
hatte: **ein Worker enriched nicht „alles", er wählt einen Batch** — und diese
Wahl trifft Legacy über zwei Spalten je Provider,
`<service>_match_status` und `<service>_last_attempted`. Ein Worker, der lib2
liest, konnte „noch nie versucht" nicht von „am Dienstag versucht, Provider
kennt ihn nicht" unterscheiden. Er hätte jeden Provider in jedem Zyklus erneut
nach jeder Entität gefragt.

`core/library2/provider_attempts.py` (`20f43a337`) schließt das: ein Ledger je
`(entity_type, entity_id, service)` statt Spaltenpaare je Tabelle — dieselbe
Begründung wie für lib2 selbst, denn im Legacy-Schema kostete Bandcamp drei
`ALTER TABLE`, und die 26 Buchhaltungsspalten auf `artists` sind der Hauptgrund,
warum diese Tabelle 63 Spalten hat. Am echten Bestand nachgewiesen: **1.053
Versuchszeilen** aus Legacy übernommen, danach bietet `due_entities` die von
Legacy bereits gematchten Entitäten nicht mehr an.

Bewusst **nicht** im Spiegel: `*_last_attempted` wird bei jedem Provider-Aufruf
geschrieben; würde der Trigger darauf feuern, stünde bei jedem Enrichment-Zyklus
die ganze Bibliothek in der Queue. Die Altdaten werden einmalig gesät, im
Leerlauf-Tick des Drainers statt auf dem Startpfad (iss32-M03).

#### 50.4.4.5 Zwei Worker sind umgestellt, das Rezept steht

`lastfm` (`1d56e062b`) und `genius` (`819535c51`) enthalten **null**
Legacy-Statements mehr, je durch einen Test an der Datei selbst festgenagelt. Die
Ratsche ist zum ersten Mal gefallen: **647/239 → 626/229**.

Drei geteilte Teile, absichtlich nicht je Worker kopiert — die restlichen
vierzehn brauchen genau dasselbe:

| Teil | Aufgabe |
|---|---|
| `library2/worker_queue.py` | Batch-Auswahl: Reihenfolge Artist→Album→Track, Pinned-Group-Override, Retry-Fenster. Nur `not_found` wird wiederholt, `error` nicht — sonst wird ein Provider-Ausfall zur Endlosschleife. |
| `library2/provider_writes.py` | Der Schreibpfad. Drei Spaltenmodi, weil die Provider sich wirklich unterscheiden: `payload` → `enrichment[service]`, `backfill` → nur solange leer (Last.fm-Artwork ist ein Fallback), `columns` → schreibt durch (Genius-Lyrics; `None` lässt trotzdem stehen, damit ein fehlgeschlagener Abruf vorhandene Lyrics nicht löscht). |
| `enrich.MIGRATED_SERVICES` | Die Übergabe, und sie ist **Pflicht**, nicht Kosmetik. |

**Warum die Übergabe Pflicht ist:** Zusage 1 (der Spiegel läuft nur in eine
Richtung) ist genau so lange sicher, wie Legacy der einzige Schreiber dieser
Felder ist. Sobald der Worker lib2 schreibt, drückt der nächste Drain den alten
Legacy-Wert über den frischen nativen — und die Divergenz-Kennzahl meldet die
korrekte Ausgabe des Workers als Defekt. Der Service verlässt den Spiegel
deshalb im selben Commit, der seinen Worker verschiebt. Die Menge ist damit der
Fortschrittsmarker für Stufe 2: ein Eintrag je umgestelltem Worker, und wenn alle
drin sind, hat der Spiegel keine Arbeit mehr.

**Ein echtes Loch, das der Deklarations-Wächter gefunden hat:**
`tracks.genius_lyrics` ist eine gespiegelte *Skalarspalte*, nicht Teil des
Enrichment-Buckets — `MIGRATED_SERVICES` griff dort nicht, der Drain hätte die
Lyrics des Workers beim nächsten Tick wieder überschrieben. `active_scalars`
schließt das über dieselbe Präfix-Regel wie `watched_columns`. Ohne den Wächter
wäre das unsichtbar geblieben: das Symptom ist ein Wert, der still zurückspringt.

**Rest, nach Größe:** `repair_worker` 42, `spotify` 35, `itunes` 35, `qobuz` 33,
`tidal` 32, `deezer` 30, `audiodb` 24, `amazon` 20, `jiosaavn` 20,
`musicbrainz` 17, `soulid` 17, `discogs` 14, `bandcamp` 13,
`listening_stats` 11, `similar_artists` 4. Mechanisch, aber je Worker mit eigenem
Test — und die Ratsche ist das Maß: sie fällt erst, wenn das SQL wirklich
verschwindet, nicht wenn nur ein Aufrufer wechselt.

#### 50.4.5 PR-Hygiene, aus dem eigenen Kommentar vom 5. August

- Die `docs/`-Notizen sollten laut eigenem PR-Kommentar gar nicht in diesem PR
  sein („those are my notes, not project documentation"). Commit `57d238ea5`
  enthält ~300 Zeilen davon. Vor dem Merge entweder herausnehmen oder die
  Meinungsänderung aussprechen — nicht stillschweigend lassen.
- `library-v2-page.tsx` mit 10,6k Zeilen ist noch nicht in Module zerlegt.
- Der Antworttext an Nezreka für den PR ist noch nicht geschrieben. Die drei
  Punkte, die für ihn den Unterschied machen: (1) es hing nicht, es war
  quadratisch — iss32-M08; (2) seine „keep it off the startup path"-Forderung
  war richtig, nur an anderer Stelle als zuerst vermutet; (3) die Messwerte bei
  *seiner* Skala, nicht bei unserer.

#### 50.4.6 Stufe 2 und danach

- 16 Enrichment-Worker + Media-Server-Scan auf lib2 (eigener PR — dort liegt
  der Ingest-Pfad)
- Sieben Repair-Jobs mit Legacy-Resten: `comma_artist_splitter` 0/7,
  `genre_cleanup` 0/3, `live_commentary_cleaner` 0/3, `track_number_repair`
  3/11, `album_tag_consistency` 3/8, `metadata_gap_filler` 2/4,
  `missing_cover_art` 4/4
- Provider-Bios für rein native Artists — löst sich mit Last.fm/Genius/Discogs
  von selbst auf
- Danach Stufe 3 (Legacy read-only, Spiegel und Trigger fallen weg) und
  Stufe 4 (Tabellen droppen)
- `iss32-M06` bleibt bewusst teilweise offen: die unbegrenzte Arbeit ist aus
  `_initialize_database` heraus, die eine große Transaktion bleibt
