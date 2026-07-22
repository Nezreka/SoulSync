# Library v2 — Tool-Integration-Audit und Migrationsplan (2026-07-18)

> **Aktueller Stand — P3 Runtime umgesetzt am 2026-07-18, automatischer
> Initialimport-Bootstrap umgesetzt am 2026-07-19** (siehe
> [`library-v2.md`](library-v2.md) §80). Die Abschnitte 3–6 dokumentieren den
> historischen Ausgangspunkt und die P0–P2-Entscheidungen. Der verbindliche
> P3-Endzustand steht in Abschnitt 7: ursprünglich 19 registrierte Jobs; seit
> §82 sind es 20 durch den wieder erforderlichen neutralen Reconcile-Job. Keine registrierte
> Legacy-/Mixed-Datenbasis, native Library-v2-Subjects und neutrale Job-IDs.
> Punkt 7 der P3-Abschlusscheckliste (automatischer Initialimport) ist damit
> erledigt; die physische Entfernung der `legacy_*`-Spalten und des Importers
> (Punkt 5) bleibt weiterhin gemäß geplantem Datenmigrations-/Rollback-Fenster
> offen.
>
> **Nachtrag 2026-07-19 (siehe [`library-v2.md`](library-v2.md) §81):**
> Abschnitt 4 (Tabelle) und Abschnitt 6 (P2-„Offen"-Liste/§5.3) beschrieben
> Duplicate Detector, Album Completeness, Canonical Version Resolve, MBID
> Mismatch Detector, Single/Album Dedup, Fix Unknown Artists und Library
> Re-tag fälschlich weiterhin als offene Brücken-Tools, obwohl sie laut
> Abschnitt 7 Punkt 2 bereits seit dem P3-Commit vom 2026-07-18 retiriert
> waren — nur ihre toten Modul-Dateien/Dispatch-Einträge/Tests waren noch
> nicht physisch entfernt. Beides ist jetzt korrigiert bzw. nachgeholt;
> Library Reorganize ebenso (nur der Scan-Job-Wrapper, nicht die aktive
> Reorganize-Engine). Expired Download Cleaner ist ebenfalls retiriert,
> aber ohne klar identifizierten 1:1-nativen Nachfolger.
>
> Nachtrag 2026-07-19 (`library-v2.md` §82): Solange Watchlist/Wishlist
> aktive Übergangsgrenzen sind, bleibt periodische Reparatur erforderlich.
> Die früher getrennten Jobs `lib2_mirror_reconcile` und
> `lib2_wishlist_reconcile` kehren nicht unter ihren internen Namen zurück;
> der neutrale kombinierte Job `monitoring_list_reconcile` drainiert die
> Outbox und repariert Artist⇄Watchlist sowie Wanted-Track⇄Wishlist.

## 1. Ziel und Ergebnis

Dieser Deep Dive prüfte ursprünglich alle 33 im Repair-Worker registrierten
Tools gegen die optionale Library v2. P3 hatte die Runtime-Registry auf 19
native bzw. rein operative Jobs konsolidiert; §82 ergänzt den kombinierten
Reconcile-Job als zwanzigsten. Geprüft wurden nicht nur Namen und UI-Karten,
sondern Scanner-Datenbasis, Findings, Fix-Handler, automatische Mutationen,
Dateioperationen, Quality-/Wanted-Folgen, Artwork-Cache, History und das
Feature-Gating.

Das historische Zwischenergebnis war eine klare Trennung:

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

Jedes registrierte Tool besitzt im Registry-Code zwei explizite Deklarationen:

- **Datenbasis:** ausschließlich `lib2` oder `filesystem`;
- **V2-Effekte:** `observe`, `metadata`, `tags`, `artwork`, `path`,
  `new_file`, `delete`, `wanted`, `discography` oder `none`.

Ein neues Tool ohne beide Deklarationen kann nicht registriert werden. Für
native Reparaturen gilt:

```text
Finding/Live-Fix
  → stabile V2-Subjects verwenden
  → native V2-Mutation ausführen
  → nur betroffene Files neu scannen
  → Artwork gegebenenfalls invalidieren
  → Wanted nach Datei-Neu/Entfernung neu berechnen
  → Entity-History-Event schreiben
```

Der native Change-/History-Vertrag liegt zentral in
`core/library2/maintenance_sync.py`. Legacy-ID-Auflösung,
Legacy→V2-Projektion und Reimports sind daraus entfernt.

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

## 4. Historischer Audit aller 33 Tools vor P3

| # | Tool | Stand nach diesem Paket | Offene native Zielmigration |
|---:|---|---|---|
| 1 | Track Number Repair | **Brücke, P1 nativ (2026-07-18).** Findings und Live-Reparaturen melden Metadaten-, Tag- und Pfadänderung; V2-File wird gezielt neu gelesen und History aktualisiert; V2-Ordner außerhalb des Transfer-Walks werden erfasst. | Erledigt (P1). |
| 2 | Cache Maintenance | **Neutral.** Entfernt nur abgelaufene/junk/orphaned Metadaten-Caches; keine Library-Entität. | Keine V2-Migration. |
| 3 | Orphan File Detector | **Dual Read.** V2-Dateipfade und -Identitäten werden nur bei exakt aktiviertem Flag berücksichtigt. Mass-Orphan-Guard bleibt bestehen. | P1: gemeinsame normalisierte File-Index-Abfrage statt Suffix-Heuristik; echter Orphan bleibt bis zum normalen Staging-Import absichtlich ohne V2-Subject. |
| 4 | Dead File Cleaner | **Brücke.** Entfernen/Re-download markiert das gemappte V2-File deleted und berechnet Wanted neu. | P1: V2-File-Lifecycle (`missing_suspected`/`missing_confirmed`) als Scannerquelle; nicht parallel zur Integrity-Reconciliation neu erfinden. |
| 5 | Duplicate Detector | **ENTFERNT (P3, 2026-07-18; Modul physisch entfernt 2026-07-19).** Ersetzt durch die native Dedup-Reparatur (`core/library2/dedup_repair.py`, §62/§63) — läuft automatisch am Ende jedes Imports und on-demand über den Maintenance-Endpoint; Artist-/Album-Duplikate werden gemerged statt nur nachträglich als Finding gemeldet. | Erledigt. |
| 6 | AcoustID Scanner | **Dual Read, P1 nativ (2026-07-18).** Aktive V2-Primary-Files ohne Legacy-Backref werden direkt gescannt; Verification-Status wird nativ auf `lib2_track_files` persistiert, Finding-Fixes melden sich zusätzlich an V2. | Erledigt (P1). |
| 7 | Cover Art Filler | **Dual Read, P1 nativ (2026-07-18).** V2-Alben werden über den Album-Enumerator direkt erfasst; der Fix schreibt nativ auf `lib2_albums`/`lib2_artists` über die bestehende V2-Artwork-Resolverkette. | Erledigt (P1). |
| 8 | Lyrics Filler | **Dual Read, P1 nativ (2026-07-18).** Legacy-Files plus aktive V2-Files ohne Legacy-File-Backref über den gemeinsamen V2-Subject-Enumerator; `.lrc`/embedded Lyrics führen zu gezieltem V2-Rescan und sichtbarem History-Event. | Erledigt (P1). |
| 9 | ReplayGain Filler | **Dual Read, P1 nativ (2026-07-18).** Legacy-Files plus aktive V2-Files ohne Legacy-File-Backref über denselben Enumerator; geschriebene Tags aktualisieren `has_replaygain` über gezielten Rescan und erscheinen in Track/Album/Artist-History. | Erledigt (P1). |
| 10 | Empty Folder Cleaner | **Neutral.** Löscht nur leere/junk-only Verzeichnisse; Quarantäne-Schutz bleibt. | Keine Katalogmigration; Root-Health-Gate weiter verbindlich. |
| 11 | Expired Download Cleaner | **ENTFERNT (P3, 2026-07-18; Modul physisch entfernt 2026-07-19).** Kein dedizierter nativer Nachfolger identifiziert — Retention läuft nicht mehr als eigener periodischer Scan; File-Löschung/Wanted-Neuberechnung bleibt generisch über die zentrale V2-Lifecycle-Brücke abgedeckt, falls ein anderer Pfad eine abgelaufene Datei entfernt. | Erledigt (retired ohne 1:1-Ersatz). |
| 12 | Metadata Gap Filler | **Dual Read, P1 nativ (2026-07-18).** IDs/Metadaten/Tags werden auf gemappte V2-Subjects projiziert und neu gescannt; der Fix schreibt zusätzlich nativ auf `lib2_tracks`. | Erledigt (P1). |
| 13 | Album Completeness | **ENTFERNT (P3, 2026-07-18; Modul physisch entfernt 2026-07-19).** Ersetzt durch native V2-Completeness (`core/library2/completeness.py` — kanonische Tracklist-Auflösung materialisiert fehlende Tracks als monitorbare Platzhalter) plus die globalen Wanted-Views (Missing/Cutoff, §74) mit manuellem Grab statt einem separaten periodischen Scan-Job. | Erledigt. |
| 14 | Fake Lossless Detector | **Dual Read (observe-only), P1 nativ (2026-07-18).** Alle aktiven V2-Files werden statt nur Filesystem-/Transfer-Scope geprüft; Path-Findings erhalten V2-Subjects. Das Tool verändert absichtlich nichts automatisch — ein späterer Fix muss über Review/Replacement laufen. | Erledigt (P1). |
| 15 | Quality Check — flag only | **ENTFERNT (P2, 2026-07-18).** Ersetzt durch `lib2_upgrade_scan` mode=`review` (`quality_below_cutoff`-Findings). | Erledigt. |
| 16 | Library Reorganize | **ENTFERNT als eigener Repair-Job (P3, 2026-07-18; Modul physisch entfernt 2026-07-19).** Die dry-run-Scan-Job-Wrapper-Schicht ist weg; die zugrundeliegende Planner-/Queue-Engine (`core/library_reorganize.py`, `core/reorganize_queue.py`, `core/reorganize_runner.py`) bleibt unverändert aktiv und wird bereits nativ über `core/library2/reorganize_bridge.py` (Album-/Artist-Reorganize-Aktionen in der V2-UI) angesprochen. Das `path_mismatch`-Finding + sein Fix-Handler bleiben in `repair_worker.py`, weil der native Reorganize-Runner sie weiterhin über `sync_repair_change` erzeugt. | Erledigt. |
| 17 | MBID Mismatch Detector | **ENTFERNT (P3, 2026-07-18; Modul physisch entfernt 2026-07-19).** Ersetzt durch die Namespace-Sanitize-Stufe der nativen Dedup-Reparatur (`core/library2/dedup_repair.py::_sanitize_provider_namespaces`, §62/§63) plus die Release-Edition-Verwaltung (`core/library2/editions.py`). | Erledigt. |
| 18 | Single/Album Dedup | **ENTFERNT (P3, 2026-07-18; Modul physisch entfernt 2026-07-19).** Ersetzt durch die album-interne Fold-Stufe der nativen Dedup-Reparatur (`core/library2/dedup_repair.py`, §62.6 Stufe 3). | Erledigt. |
| 19 | Lossy Converter | **Dual Read, P1 nativ (2026-07-18).** V2-Files werden direkt enumeriert; der neue Output-Pfad wird als zusätzliches V2-File desselben Tracks registriert und gescannt, Replace-original markiert das alte File deleted. | Erledigt (P1). |
| 20 | Album Tag Consistency | **Dual Read, P1 nativ (2026-07-18).** V2 Album→Track→File wird direkt gelesen; korrigierte Tags/Metadaten führen zu gezieltem V2-Rescan und History. | Erledigt (P1). |
| 21 | Live/Commentary Cleaner | **Brücke.** Nutzerbestätigtes Entfernen aktualisiert V2-File und Wanted. | P2: V2-Policy-Query; niedrige Priorität, da bewusst heuristisch und reviewpflichtig. |
| 22 | Fix Unknown Artists | **ENTFERNT (P3, 2026-07-18; Modul physisch entfernt 2026-07-19).** Ersetzt durch die native Artist-Enrichment/Smart-Split-Maschine (`core/library2/native_enrich.py`, §68) plus Manual-Match — deckt sowohl legacy-gebackte als auch V2-only Artists ab. | Erledigt. |
| 23 | Discography Backfill | **ENTFERNT (P2, 2026-07-18).** Ersetzt durch V2 Discography Refresh + Monitoring/Wanted + Wanted-Views (Missing/Cutoff, manueller Grab). | Erledigt. |
| 24 | Resolve Canonical Album Versions | **ENTFERNT (P3, 2026-07-18; Modul physisch entfernt 2026-07-19).** Ersetzt durch die Release-Edition-/MusicBrainz-Reconcile-Stufe der nativen Dedup-Reparatur (`core/library2/dedup_repair.py`, `core/library2/editions.py`, §62/§63). | Erledigt. |
| 25 | Library Re-tag | **ENTFERNT (P3, 2026-07-18; Modul physisch entfernt 2026-07-19).** Ersetzt durch das bereits native V2 Retag Preview/Write (`core/library2/retag.py`, UI: `retag-modal.tsx` auf der Album-/Artist-Seite) — schreibt lib2-DB-Metadaten direkt in die Datei-Tags; kein zweiter Fresh-Pull-Scan-Job mehr nötig. | Erledigt. |
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

### 5.3 Weitere spätere Ablösungen — erledigt (P3, 2026-07-18/19)

Alle vier hier ursprünglich vorgemerkten Ablösungen sind inzwischen
umgesetzt und die Legacy-Module physisch entfernt (siehe die korrigierte
Tabelle in Abschnitt 4 sowie §81 in
[`library-v2.md`](library-v2.md)):

- Single/Album Dedup → native Dedup-Reparatur (`core/library2/dedup_repair.py`).
- Canonical Version Resolve + MBID Mismatch → dieselbe native Dedup-
  Reparatur plus `core/library2/editions.py`.
- Legacy Library Re-tag → V2 Retag Preview/Write (`core/library2/retag.py`).
- Album Completeness → V2 Completeness (`core/library2/completeness.py`)
  + Wanted/Acquisition (globale Wanted-Views, §74).

Dieser Abschnitt (5.3) wurde ursprünglich VOR der tatsächlichen P3-Umsetzung
geschrieben und danach nicht mehr aktualisiert — Abschnitt 7 Punkt 2 belegt
den fertigen Zustand bereits seit 2026-07-18; die Tabelle in Abschnitt 4 und
dieser Abschnitt hatten das nur nie nachgezogen.

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
  Job benutzt).

**Korrektur (2026-07-19, siehe §81 in [`library-v2.md`](library-v2.md)):**
Dieser Abschnitt behauptete bis hierher fälschlich, Single/Album Dedup,
Album Completeness, Canonical/MBID-Reconcile, Library Re-tag und Unknown
Artist seien noch offene Brücken-Tools. Tatsächlich stehen alle fünf
bereits seit dem P3-Commit vom 2026-07-18 in `RETIRED_JOB_IDS`
(`core/repair_jobs/__init__.py`) — Abschnitt 7 Punkt 2 unten hat das korrekt
festgehalten, nur diese Stelle wurde nie nachgezogen. Am 2026-07-19 wurden
zusätzlich ihre Modul-Dateien, die zugehörigen `repair_worker.py`-Dispatch-
Einträge/Fix-Handler und toten Tests physisch entfernt (siehe die
korrigierte Tabelle in Abschnitt 4). Live/Commentary Cleaner ist davon NICHT
betroffen — das Tool ist weiterhin aktiv registriert (`live_commentary_cleaner`
in `JOB_DATA_BASIS`), niedrige Priorität bleibt zutreffend.

Die tote `discography_backfill`-Sonderbehandlung in
`webui/static/enrichment.js` (Zeile ~3583) wurde entgegen der obigen Notiz
NICHT mit P3 entfernt und bleibt zusammen mit den analogen toten
Renderer-Fällen der neu retirierten Tools (Duplicate Detector, Album
Completeness, Canonical Version, Library Re-tag) bewusst unangetastet —
dieselbe Vorsicht wie beim ursprünglichen P2-Commit, der die alte
vanilla-JS-Oberfläche ebenfalls nicht anfasste. Alle diese Fälle sind
unerreichbar (kein Job registriert ⇒ keine Findings dieses Typs können mehr
entstehen), aber noch nicht aufgeräumt.

### P3 — Legacy-Removal — UMGESETZT (2026-07-18)

Die Runtime-Grenze ist vollständig nativ. Provider-IDs werden als
provider-qualifizierte Maps durch Subject-Enumeration, Match/Enrichment,
Tracklist/Completeness, Materialisierung, Wanted-Mirror und Autolink getragen.
Ein angefragter Spotify-Pfad, der tatsächlich Deezer oder iTunes liefert,
persistiert und meldet die tatsächliche Quelle; fremde IDs gelangen nie in
Spotify-Spalten.

## 7. P3-Abschlusscheckliste

1. **Erledigt:** Die Registry akzeptiert nur `lib2` und `filesystem`; alle 20
   registrierten Jobs erfüllen den nativen V2-Effektvertrag. Retired-Module
   bleiben im Rollback-Fenster importierbar, können sich aber nicht erneut
   registrieren.
2. **Erledigt:** Abgelöste Quality-/Discography-/Dedup-/Completeness-/Reorg-/
   Retag-/Canonical-/Unknown-Artist-Jobs sind aus Registry und Maintenance-UI
   entfernt. Native Ersatzpfade sind allein sichtbar.
3. **Aktualisiert 2026-07-19 (§82):** `lib2_mirror_reconcile` und
   `lib2_wishlist_reconcile` sowie ihre alten Scheduler-IDs bleiben entfernt.
   Solange Wishlist/Watchlist aktive Übergangsgrenzen sind, übernimmt
   `monitoring_list_reconcile` neutral und kombiniert Outbox-Retry sowie die
   Artist⇄Watchlist-/Wanted-Track⇄Wishlist-Invarianten.
4. **Erledigt:** `maintenance_sync.py` enthält nur native ID-Auflösung,
   File-Rescan, Artwork-Invalidation, Wanted-Recompute und History-Events;
   keine Legacy-Projektion und keinen Importer-Aufruf.
5. **Bewusst offen:** `legacy_artist_id`, `legacy_album_id`, `legacy_track_id`
   und der Legacy-Importer werden erst nach dem expliziten Datenmigrations-/
   Rollback-Fenster physisch gelöscht. Sie sind keine Runtime-Autorität mehr.
6. **Erledigt:** `data_basis` wird nicht mehr über API/UI präsentiert. Die
   nativen Job-IDs heißen `quality_upgrade_scan`, `skip_audit_cleanup` und
   `monitored_discography_refresh`; gespeicherte alte IDs werden lesend
   migriert.
7. **Erledigt (2026-07-19), siehe [`library-v2.md`](library-v2.md) §80.** Beim
   ersten Start mit aktiviertem `features.library_v2` löst
   `core/library2/bootstrap.py` serverseitig einen automatischen, idempotenten
   Initialimport (`import_legacy_library`) aus — ohne dass jemand die
   Library-v2-UI öffnen muss. Ein persistierter Single-Row-Status
   (`lib2_bootstrap_state`) übersteht einen Neustart, ein optimistisches
   Compare-and-Swap auf `(status, heartbeat_at)` sperrt gegen Doppelstarts, ein
   gealterter `running`-Claim (Heartbeat >600s) ist zurückeroberbar
   (Wiederaufnahme), und ein Fehlschlag bleibt mit Fehlertext claimbar für den
   nächsten Retry. Der bestehende manuelle Import-Endpoint teilt sich denselben
   Claim, damit er nie parallel zum Hintergrund-Bootstrap läuft.

   Die Legacy-Wishlist darf während dieser Übergangsphase als bestehende
   Acquisition-/Retry-Queue weiterlaufen. Playlist-Sync, Wishlist und die alten
   Search-/Download-Routen müssen jedoch vor einer physischen Entfernung der
   Legacy-Library auf native Materialisierung bzw. eine bewusst dokumentierte
   Dual-Write-Grenze umgestellt werden.

Zusätzlicher Provider-Vertrag: Alle vorhandenen IDs von Spotify,
MusicBrainz, Deezer, iTunes und weiteren registrierten Quellen bleiben pro
Entity erhalten. Auswahlreihenfolgen bestimmen nur, welcher vorhandene
Provider zuerst gefragt wird, niemals den Namespace eines Ergebnisses.

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

## 9. P3-Verifikation

- `tests/library2`, `tests/repair`, `tests/repair_jobs`, `tests/automation`
  plus P3-Guard-Suiten: **1300 passed**; eine bestehende
  SQLite-Deprecation-Warnung und zwei bestehende Async-Mock-Warnungen;
- Frontend: **237 passed** in 40 Vitest-Dateien;
- `npm run check`: Formatter, Type-Lint und Oxlint vollständig sauber;
- `npm run build`: Produktions-Build erfolgreich;
- `node --check webui/static/enrichment.js`: sauber;
- statischer P3-Registry-Audit vor §82: **19 Jobs**, davon 17 `lib2` und zwei rein
  operative `filesystem`-Jobs; keine registrierte Legacy-/Mixed-Basis;
- Provider-Regressionen belegen Deezer-/iTunes-Tracklists, tatsächliche
  Fallback-Quelle, MusicBrainz/CAA-Artwork, provider-qualifizierte
  Enrichment-, Materialisierungs-, Wishlist- und Autolink-IDs.
