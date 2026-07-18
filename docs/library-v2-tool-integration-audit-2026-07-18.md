# Library v2 — Tool-Integration-Audit und Migrationsplan (2026-07-18)

## 1. Ziel und Ergebnis

Dieser Deep Dive prüft alle 33 im Repair-Worker registrierten Tools gegen die
optionale Library v2. Geprüft wurden nicht nur deren Namen und UI-Karten,
sondern Scanner-Datenbasis, Findings, Fix-Handler, automatische Mutationen,
Dateioperationen, Quality-/Wanted-Folgen, Artwork-Cache, History und das
Feature-Gating.

Das wichtigste Ergebnis ist eine klare Trennung:

1. **Übergangssicherheit:** Ein Legacy-Tool darf bei aktivierter Library v2
   keinen gemappten Artist/Album/Track/File-Zustand stale zurücklassen. Diese
   Grenze ist mit dem ersten Implementierungspaket zentral geschlossen.
2. **Native Vollständigkeit:** Ein älterer Scanner, der weiterhin aus
   `artists`/`albums`/`tracks` liest, sieht noch keine reinen V2-Entitäten ohne
   Legacy-Rückreferenz. Das ist keine Sicherheitslücke beim Fix, aber eine
   Coverage-Lücke. Diese Migration ist pro Werkzeug unten sichtbar als P1/P2
   markiert und wird nicht fälschlich als „fertig“ bezeichnet.

Alle V2-Zusatzpfade bleiben strikt hinter
`features.library_v2 is True`. Vorhandene `lib2_*`-Tabellen allein aktivieren
nichts.

## 2. Verbindlicher Änderungsvertrag

Jedes Tool besitzt nun im Registry-Code zwei explizite Deklarationen:

- **Datenbasis:** `legacy`, `lib2`, `filesystem` oder `mixed`;
- **V2-Effekte:** `observe`, `metadata`, `tags`, `artwork`, `path`,
  `new_file`, `delete`, `wanted`, `discography` oder `none`.

Ein neues Tool ohne beide Deklarationen kann nicht registriert werden. Für
erfolgreiche Legacy-Reparaturen gilt bei eingeschalteter V2:

```text
Finding/Live-Fix
  → stabile V2-Subjects anhängen
  → Legacy-Mutation ausführen
  → betroffene V2-Projektion aktualisieren
  → nur betroffene Files neu scannen
  → Artwork gegebenenfalls invalidieren
  → Wanted nach Datei-Neu/Entfernung neu berechnen
  → Entity-History-Event schreiben
```

Der Übergangscode liegt absichtlich zentral in
`core/library2/maintenance_sync.py`. Nach Entfernung der alten Library werden
dort die Legacy-ID-Auflösung und Legacy→V2-Projektion gelöscht; der
Change-/History-Vertrag bleibt für native Tools verwendbar.

## 3. Statusbegriffe

- **Nativ:** liest und schreibt das V2-Modell direkt.
- **Brücke:** gemappte Legacy-Entitäten werden nach Findings und Live-Fixes
  sicher mit V2 synchronisiert; reine V2-Entitäten sind noch nicht zwingend im
  Scan-Scope.
- **Dual Read:** Scanner berücksichtigt Legacy und V2, strikt gegated.
- **Neutral:** rein operatives Dateisystem-/Cache-Tool ohne Musikentitäts-
  Mutation.
- **Ablösen:** Funktion soll nach der Übergangsphase durch eine bestehende
  native V2-Funktion ersetzt und nicht ein zweites Mal neu gebaut werden.

## 4. Audit aller 33 Tools

| # | Tool | Stand nach diesem Paket | Offene native Zielmigration |
|---:|---|---|---|
| 1 | Track Number Repair | **Brücke, P1 nativ (2026-07-18).** Findings und Live-Reparaturen melden Metadaten-, Tag- und Pfadänderung; V2-File wird gezielt neu gelesen und History aktualisiert; V2-Ordner außerhalb des Transfer-Walks werden erfasst. | Erledigt (P1). |
| 2 | Cache Maintenance | **Neutral.** Entfernt nur abgelaufene/junk/orphaned Metadaten-Caches; keine Library-Entität. | Keine V2-Migration. |
| 3 | Orphan File Detector | **Dual Read.** V2-Dateipfade und -Identitäten werden nur bei exakt aktiviertem Flag berücksichtigt. Mass-Orphan-Guard bleibt bestehen. | P1: gemeinsame normalisierte File-Index-Abfrage statt Suffix-Heuristik; echter Orphan bleibt bis zum normalen Staging-Import absichtlich ohne V2-Subject. |
| 4 | Dead File Cleaner | **Brücke.** Entfernen/Re-download markiert das gemappte V2-File deleted und berechnet Wanted neu. | P1: V2-File-Lifecycle (`missing_suspected`/`missing_confirmed`) als Scannerquelle; nicht parallel zur Integrity-Reconciliation neu erfinden. |
| 5 | Duplicate Detector | **Brücke.** Nutzerbestätigte Deletes werden in V2 und Wanted gespiegelt. | P2: durch V2-Canonical-/Managed-Tracks-Beziehungen ersetzen; V2-only und mehrere Files pro Track nativ bewerten. |
| 6 | AcoustID Scanner | **Dual Read, P1 nativ (2026-07-18).** Aktive V2-Primary-Files ohne Legacy-Backref werden direkt gescannt; Verification-Status wird nativ auf `lib2_track_files` persistiert, Finding-Fixes melden sich zusätzlich an V2. | Erledigt (P1). |
| 7 | Cover Art Filler | **Dual Read, P1 nativ (2026-07-18).** V2-Alben werden über den Album-Enumerator direkt erfasst; der Fix schreibt nativ auf `lib2_albums`/`lib2_artists` über die bestehende V2-Artwork-Resolverkette. | Erledigt (P1). |
| 8 | Lyrics Filler | **Dual Read, P1 nativ (2026-07-18).** Legacy-Files plus aktive V2-Files ohne Legacy-File-Backref über den gemeinsamen V2-Subject-Enumerator; `.lrc`/embedded Lyrics führen zu gezieltem V2-Rescan und sichtbarem History-Event. | Erledigt (P1). |
| 9 | ReplayGain Filler | **Dual Read, P1 nativ (2026-07-18).** Legacy-Files plus aktive V2-Files ohne Legacy-File-Backref über denselben Enumerator; geschriebene Tags aktualisieren `has_replaygain` über gezielten Rescan und erscheinen in Track/Album/Artist-History. | Erledigt (P1). |
| 10 | Empty Folder Cleaner | **Neutral.** Löscht nur leere/junk-only Verzeichnisse; Quarantäne-Schutz bleibt. | Keine Katalogmigration; Root-Health-Gate weiter verbindlich. |
| 11 | Expired Download Cleaner | **Brücke.** automatische Deletes melden Datei/Wanted/History. | P2: Retention langfristig aus Acquisition-Origin/History ableiten; nicht als allgemeiner V2-File-Cleaner verwenden. |
| 12 | Metadata Gap Filler | **Dual Read, P1 nativ (2026-07-18).** IDs/Metadaten/Tags werden auf gemappte V2-Subjects projiziert und neu gescannt; der Fix schreibt zusätzlich nativ auf `lib2_tracks`. | Erledigt (P1). |
| 13 | Album Completeness | **Brücke plus Import.** Copy/Move neuer Legacy-Tracks löst den idempotenten Legacy→V2-Import, Rescan und Wanted-Recompute aus. | P2/Ablösen: native V2-Completeness + Wanted/Acquisition als Quelle; keine zweite Missing-Track-Engine. |
| 14 | Fake Lossless Detector | **Dual Read (observe-only), P1 nativ (2026-07-18).** Alle aktiven V2-Files werden statt nur Filesystem-/Transfer-Scope geprüft; Path-Findings erhalten V2-Subjects. Das Tool verändert absichtlich nichts automatisch — ein späterer Fix muss über Review/Replacement laufen. | Erledigt (P1). |
| 15 | Quality Check — flag only | **ENTFERNT (P2, 2026-07-18).** Ersetzt durch `lib2_upgrade_scan` mode=`review` (`quality_below_cutoff`-Findings). | Erledigt. |
| 16 | Library Reorganize | **Brücke.** Dry-run-Findings sind V2-verknüpft; Queue-Moves laufen nun über die strikt gegatete zentrale Pfad-/Rescan-/History-Grenze. | P1: Planner muss V2-IDs/Files ohne Legacy-Backref akzeptieren; bestehende V2-Reorganize-API weiterverwenden. |
| 17 | MBID Mismatch Detector | **Brücke.** Retag/ID-Clear/Album-Korrektur aktualisiert gemappte V2-Files und Metadaten. | P2: mit V2 Release-/Recording-Reconcile zusammenführen, sobald dessen manuelle Review-Semantik gleichwertig ist. |
| 18 | Single/Album Dedup | **Brücke.** bestätigte Single-Entfernung markiert V2-Files deleted und Wanted neu. | P2/Ablösen: Managed Tracks + Canonical Relationships sind das native Zielmodell. |
| 19 | Lossy Converter | **Dual Read, P1 nativ (2026-07-18).** V2-Files werden direkt enumeriert; der neue Output-Pfad wird als zusätzliches V2-File desselben Tracks registriert und gescannt, Replace-original markiert das alte File deleted. | Erledigt (P1). |
| 20 | Album Tag Consistency | **Dual Read, P1 nativ (2026-07-18).** V2 Album→Track→File wird direkt gelesen; korrigierte Tags/Metadaten führen zu gezieltem V2-Rescan und History. | Erledigt (P1). |
| 21 | Live/Commentary Cleaner | **Brücke.** Nutzerbestätigtes Entfernen aktualisiert V2-File und Wanted. | P2: V2-Policy-Query; niedrige Priorität, da bewusst heuristisch und reviewpflichtig. |
| 22 | Fix Unknown Artists | **Brücke plus Import.** Live/Finding-Fix meldet Änderungen; wegen geänderter Artist-/Album-Junctions wird idempotent neu importiert, danach rescant und History aktualisiert. | P2: V2-native Enrichment/Manual Match übernimmt V2-only Fälle; Legacy-Job später ablösen. |
| 23 | Discography Backfill | **ENTFERNT (P2, 2026-07-18).** Ersetzt durch V2 Discography Refresh + Monitoring/Wanted + Wanted-Views (Missing/Cutoff, manueller Grab). | Erledigt. |
| 24 | Resolve Canonical Album Versions | **Brücke.** Finding-Fix und Live-Pin melden Albumänderung in Entity History. | P2/Ablösen: V2 Release-Edition-/MusicBrainz-Reconcile wird alleinige Canonical-Quelle. |
| 25 | Library Re-tag | **Brücke.** Jeder Live-Retag meldet Metadata/Tags/Artwork; Cache wird invalidiert und Files werden rescant. | P1/Ablösen: Repair-Karte soll den bereits nativen V2 Retag Preview/Write aufrufen; alten Scanner danach entfernen. |
| 26 | Quality Upgrade Finder — active | **ENTFERNT (P2, 2026-07-18).** Ersetzt durch `lib2_upgrade_scan` mode=`automatic` (native Upgrade-Queue). | Erledigt. |
| 27 | Preview Clip Cleanup | **Dual Read, P1 nativ (2026-07-18).** Aktive V2-Files werden direkt nach Dauer/Identity geprüft; Delete+Rewishlist markiert V2-File deleted und Wanted neu. | Erledigt (P1). |
| 28 | Corrupt File Detector | **Dual Read, P1 nativ (2026-07-18).** Aktive V2-Files werden direkt decode-getestet (Root-Health/Path-Resolver); Delete+Rewishlist markiert V2-File deleted und Wanted neu. | Erledigt (P1). |
| 29 | Quality Upgrade Scan (monitored), intern `lib2_upgrade_scan` | **Nativ und gegated.** Monitored/Wanted gegen effektives Profil/Cutoff; Modus `automatic` (Upgrade-Queue) oder `review` (Findings). Einziger Quality-Evaluator seit P2. | Behalten; internen Prefix nach Legacy-Removal entfernen. |
| 30 | Skip-Audit Cleanup, intern `lib2_skips_cleanup` | **Nativ und gegated.** räumt nur abgelaufene manuelle Skip-Audit-Zeilen auf. | Behalten. Nicht mit Quality Scan zusammenführen. |
| 31 | Monitored Discography Refresh, intern `lib2_discography_refresh` | **Nativ und gegated.** erweitert monitored Artists und projiziert `monitor_new_items`/Wanted. | Behalten; Zielersatz für Legacy Discography Backfill. |
| 32 | Watchlist/Wishlist Mirror Reconcile, intern `lib2_mirror_reconcile` | **Nativ, gegated, transitional.** retryt persistente Watchlist/Wishlist-Mirror-Outbox. | Nach Entfernung der alten Watchlist/Wishlist-Ausgänge löschen oder durch native Consumer-Outbox ersetzen. |
| 33 | Monitored Wishlist Reconcile, intern `lib2_wishlist_reconcile` | **Nativ, gegated, transitional.** stellt verlorene Legacy-Wishlist-Einträge für monitored+missing wieder her. | Mit alter Wishlist vollständig löschen; native Acquisition/Wanted bleibt. |

## 5. Überschneidungen und Konsolidierungsentscheidungen

### 5.1 Die drei Quality-Werkzeuge

Sie benutzen ähnliche Quality-Regeln, haben aber heute unterschiedliche
Produktsemantik:

| Tool | Bewertung | Suche | Entscheidung |
|---|---|---|---|
| Quality Check (flag only) | Legacy/File | nein | Nutzer pro Finding |
| Quality Upgrade Finder | Legacy/File | sucht vorab Replacement | Nutzer bestätigt Proposal |
| Automatic Upgrade Scan (monitored) | native Wanted/Cutoff | queued native Upgrade-Intent | automatisch gemäß Monitoring/Profil |

**Erledigt (2026-07-18):** Der Zielzustand ist umgesetzt — `lib2_upgrade_scan`
ist der einzige native Evaluator mit den Ausführungsmodi `review` und
`automatic`; die beiden Legacy-Jobs sind entfernt.

### 5.2 Discography Backfill vs. V2 Discography Refresh

Auch diese sind noch nicht dasselbe:

- Backfill vergleicht die Legacy-Library mit Provider-Tracks und erzeugt
  einzelne Findings/Wishlist-Aktionen.
- V2 Refresh aktualisiert den Providerkatalog monitored Artists und wendet
  `monitor_new_items` sowie die Wanted-Projektion an.

**Erledigt (2026-07-18):** Der Backfill ist entfernt. Review und explizite
Materialisierung laufen über die native Discography-/Wanted-UI (globale
Missing-/Cutoff-Views mit manuellem Grab, `core/library2/materialize.py`).

### 5.3 Weitere spätere Ablösungen

- Single/Album Dedup → V2 Managed Tracks/Canonical Relationships.
- Canonical Version Resolve + MBID Mismatch → V2 Edition/Recording Reconcile.
- Legacy Library Re-tag → V2 Retag Preview/Write.
- Album Completeness → V2 Completeness + Wanted/Acquisition.

## 6. Findings und Priorität

### P0 — in diesem Paket umgesetzt

- exhaustives Registry-Manifest für Datenbasis und V2-Effekte;
- stabile V2-IDs in allen neu erzeugten Legacy-Repair-Findings;
- zentrale, strikt gegatete Repair→V2-Synchronisation;
- gezielter Rescan per `file_ids` statt Full-Library-Scan;
- Metadaten-, Pfad-, Verification-, Lyrics-, ReplayGain- und Tag-Projektion;
- neue derivative Files, Deletes, Artwork-Invalidation und Wanted-Recompute;
- History-Events auf Artist, Album und Track;
- Live-Mutationsmeldungen für Track Number, AcoustID, Expired Cleaner,
  Unknown Artist, Library Re-tag und Canonical Resolve;
- Reorganize-Queue über dieselbe gegatete Grenze;
- Orphan-Detector-Dual-Read strikt an das Feature-Flag gebunden;
- gemeinsamer strikt gegateter V2-File-Subject-Enumerator; ReplayGain und
  Lyrics erfassen damit bereits V2-only/derivative Files;
- UI-Badges zeigen `Library`, `Library + files` oder `Files` statt ständig
  „Library v2“ bzw. „Legacy + files“; auch die fünf nativen Repair-Karten
  haben bereits neutrale Produktnamen, ihre stabilen internen Job-IDs bleiben
  für die Übergangsphase `lib2_*`.

### P1 — native File-Tool-Coverage — UMGESETZT (2026-07-18)

Der gemeinsame V2-Subject-Enumerator (`v2_uncovered_file_subjects`, ergänzt um
vollen Track-/Album-/Provider-Kontext) und sein Album-Pendant
(`v2_uncovered_album_subjects`) liefern aktive V2-Subjects ohne
Legacy-Backref. Darauf umgestellt sind jetzt: ReplayGain, Lyrics, AcoustID
(inkl. nativer Verification-Persistenz auf `lib2_track_files`), Cover Art
(Album-Enumerator, nativer Fix auf `lib2_albums`/`lib2_artists`), Corruption,
Preview Clip, Lossy Converter, Fake Lossless, Metadata Gap (inkl. nativem
Fix auf `lib2_tracks`), Album Tag Consistency und Track Number Repair
(V2-Ordner außerhalb des Transfer-Walks). Die Delete+Rewishlist-Fixes laden
ihren Redownload-Payload für `lib2:`-Subjects nativ aus dem V2-Katalog
(`_load_lib2_redownload_row`); File-State/Wanted übernimmt die zentrale
Brücke. Ein fehlschlagender Legacy-Query bricht die native Coverage in
keinem migrierten Scanner mehr ab.

### P2 — semantische Konsolidierung — TEILWEISE UMGESETZT (2026-07-18)

Umgesetzt:

- **Quality-Familie:** `lib2_upgrade_scan` („Quality Upgrade Scan
  (monitored)") ist der einzige Evaluator und hat die beiden Modi
  `automatic` (queued direkt) und `review` (erzeugt
  `quality_below_cutoff`-Findings; der Fix queued den Upgrade-Search pro
  Track). Beide Legacy-Jobs (`quality_upgrade_scanner`, `quality_upgrade`)
  sind aus Registry und Code entfernt; die Cutoff-Semantik-Parität bleibt
  durch den eingefrorenen Orakel-Test in `test_legacy_parity_contract.py`
  belegt. Die Automation-Aktion `start_quality_scan` triggert jetzt den
  nativen Scan.
- **Discography:** `discography_backfill` ist entfernt; native Abdeckung
  sind `lib2_discography_refresh` + Monitoring/Wanted + die globalen
  Wanted-Views (Missing/Cutoff) mit manuellem Grab.
- Entfernte Jobs stehen in `RETIRED_JOB_IDS` (Registry); der Worker räumt
  deren pendente Findings beim Start deterministisch ab, Resolved-History
  bleibt erhalten.
- Mit entfernt: `core/discovery/quality_scanner.py` (nur noch vom entfernten
  Job benutzt). Tote Karten-Sonderfälle in `webui/static/enrichment.js`
  verschwinden mit der Legacy-UI in P3.

Offen (Brücke bleibt sicher; Ablösung erst nach nachgewiesener
Gleichwertigkeit der Review-/Safety-Aktionen): Single/Album Dedup, Album
Completeness, Canonical/MBID-Reconcile, Library Re-tag (Karte soll den
nativen V2-Retag Preview/Write aufrufen), Unknown Artist, Live/Commentary,
Duplicate Detector.

### P3 — Legacy-Removal

Siehe den konkreten Löschplan im nächsten Abschnitt. Die zentrale Brücke macht
diese Phase bewusst mechanisch statt zu einer erneuten 33-Tool-Suche.

## 7. Löschplan für die alte Library

Wenn die alte Library freigegeben wird, erfolgt die Entfernung in dieser
Reihenfolge:

1. Sicherstellen, dass kein Tool mehr `data_basis='legacy'` oder `mixed` mit
   Legacy-Katalogabfrage besitzt.
2. Abgelöste Jobs aus Registry/UI entfernen: beide Legacy-Quality-Jobs,
   Discography Backfill, Single/Album Dedup, Canonical Version Resolve und
   Legacy Retag; weitere nur nach P2-Acceptance.
3. `lib2_mirror_reconcile` und `lib2_wishlist_reconcile` mit alter
   Watchlist/Wishlist entfernen.
4. Aus `maintenance_sync.py` Legacy-ID-Auflösung,
   `_sync_legacy_projection()` und die seltenen Reimports löschen. Event-
   Schema und nativen Change-Vertrag behalten.
5. `legacy_artist_id`, `legacy_album_id`, `legacy_track_id` und den
   Legacy-Importer erst nach einem Datenmigrations-/Rollback-Fenster entfernen.
6. Interne `data_basis`-UI-Hinweise entfernen und die verbliebenen stabilen
   `lib2_*`-Job-IDs neutral migrieren; nutzerseitige Namen sind bereits neutral.

Die zwei eindeutig **nur übergangsbedingten** V2-Jobs sind damit heute klar:
Mirror Reconcile und Wishlist Reconcile. Discography Refresh, Upgrade Scan und
Skip-Audit Cleanup sind native Funktionen und bleiben, nur ohne „V2“ im Namen.

## 8. Acceptance-Kriterien

Für jedes mutierende Tool gelten dieselben Tests:

1. Feature off: keine `lib2_*`-Mutation und kein Maintenance-Event.
2. Feature on + gemappter Subject: passende V2-ID am Finding.
3. Tag/Metadata/Verification: gezielter File-Rescan aktualisiert Snapshot.
4. Cover: Entity-Artwork-Cache wird invalidiert.
5. Move: V2-Pfad und History stimmen nach erfolgreicher Dateioperation.
6. New file: neuer V2-File-Row, Quality/Tags gescannt.
7. Delete/Replacement: alter File-State deleted, Wanted neu berechnet.
8. V2-only: der später nativ migrierte Scanner findet das Subject ohne
   Legacy-Rückreferenz.
9. Fehlgeschlagener Repair-Fix: keine V2-Erfolgsmeldung.
10. Integration-Fehler: Original-Fix wird nicht zurückgerollt oder als
    ungeschehen dargestellt; Diagnose bleibt im Result/Log sichtbar.

## 9. Verifikation dieses ersten Pakets

Neue Regressionstests decken Finding-Annotation, striktes Feature-off,
AcoustID-Snapshot/History, Delete/Wanted, neue derivative Files,
Artwork-Invalidation, Registry-Vollständigkeit und Reorganize-
Pfadsynchronisation sowie V2-only ReplayGain-/Lyrics-Findings ab.

- vollständige `tests/library2`: **852 passed**;
- breite Repair-/Repair-Job-Auswahl: **309 passed**, vier bestehende
  Python-3.12-SQLite-Deprecation-Warnungen;
- Library-v2-Frontend: **141 passed** in 24 Vitest-Dateien;
- `ruff check`, gezieltes `oxfmt --check` und `oxlint --type-check`: sauber;
- Vite Production-Build: erfolgreich (nur bestehender Chunk-Size-Hinweis).

Der repositoryweite `npm run check` stoppt weiterhin an einer bereits vor
diesem Paket unformatierten, hier nicht veränderten Datei
`track-feature-badges.test.tsx`; alle in diesem Paket berührten Frontend-
Dateien bestehen Formatter und Type-Lint einzeln.
