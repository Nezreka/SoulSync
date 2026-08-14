# Handoff — Library-v2-Rewrite-Audit

Vollständiger Audit: [`library-v2-rewrite-audit-2026-08-13.md`](library-v2-rewrite-audit-2026-08-13.md)

## Startpunkt für den nächsten Chat

- Branch/Stand: `library-overhaul` @ `7595b6dd4`.
- Audit-Range: `eb69f18fb^..7595b6dd4`; Worker-/Service-Kern:
  `20f43a337^..7595b6dd4`.
- Keine Produktdatei wurde im Audit verändert; nur diese beiden Audit-Dokumente
  wurden ergänzt.
- Nicht pauschal alle verbleibenden Legacy-Treffer „fixen“: bewusst geparkte,
  noch nicht migrierte Flächen sind kein Befund für sich. Jede Änderung gegen
  die Library-v2-Verträge und bestehende Aufrufer testen.

## Empfohlene Reihenfolge

### Block 1 — Datenverlust und falsche Identität

1. **LV2-AUD-09**: `clear_server_data`, stale track und removed content auf
   Server-Beitrag statt ganze gemischte Katalogzeile umstellen.
2. **LV2-AUD-21**: Delete-by-path dateizeilenbezogen machen.
3. **LV2-AUD-17**: Duplicate-Merge per-key/per-state mergen und abhängige
   Tabellen (`lib2_monitor_rules`, Attempts/Provenance usw.) umhängen.
4. **LV2-AUD-06**: Track-Fallback um Disc/Track/MBID bzw. belastbare Identität
   erweitern.
5. **LV2-AUD-14/15/23/25**: ID-Domänen durchgängig typisieren; an jedem
   Medienserver-Aufrufer `server_source + server_id`, an jedem Katalogpfad
   `lib2 id` verwenden.

### Block 2 — Worker-Liveness und neue Track-Side-Effects

6. **LV2-AUD-01/02**: Attempt-Backfill paginierbar, wiederaufnehmbar und um
   Derived-Services ergänzen; Completion erst nach Erfolg speichern.
7. **LV2-AUD-03/05**: Worker-Universen auf tatsächlich verwendbare IDs
   beschränken und jeden Existing-ID-Return im Ledger abschließen.
8. **LV2-AUD-18**: Insert-vs-Update wieder explizit liefern; Post-Scan-
   Reconcile vollständig auf lib2-Dateizeilen portieren und History-Parität
   wiederherstellen.

### Block 3 — Projektionen und Ownership

9. **LV2-AUD-13/22/24**: gemeinsame Legacy-kompatible Projektionen für API,
   Snapshots und Discover verwenden statt rohe lib2-Zeilen zu mappen.
10. **LV2-AUD-10/11/16/20**: jede „owned/in library“-Abfrage auf
    `origin='library'` beziehungsweise eine live owned-Datei begrenzen.
11. **LV2-AUD-07/08/19**: Dateilifecycle und Artist-Junctions beim Scan
    konsistent ersetzen, nicht nur ergänzen.
12. **LV2-AUD-04/12**: Provider-IDs qualifizieren und `name_key` auf beiden
    Seiten desselben Lookups verwenden.

## Regressionstests, die zuerst ergänzt werden sollten

- Attempt-Backfill mit `limit < row_count`, zwei Läufen, Prozessneustart und
  transientem Fehler; explizit `similar_artists_match_status`.
- Similar-Worker mit ausschließlich Discogs/Amazon-ID sowie numerischen
  Cross-Provider-ID-Kollisionen.
- Zwei gleichnamige Tracks auf unterschiedlichen Discs; RatingKey-Wechsel;
  Pfadwechsel mit einer echten alternativen Keeper-Datei.
- Full Refresh eines Artists, der owned Album, Provider-only Album/Track,
  Provider-IDs, Monitor-Regel und Soulseek-Alternate gleichzeitig besitzt.
- Duplicate-Merge mit komplementären `external_ids`, `enrichment`, monitored
  state und Profile-Regeln.
- Suche/Sync mit absichtlich kollidierenden `lib2_tracks.id` und
  `server_id`; je ein Plex-, Jellyfin- und Navidrome-Aufrufer.
- API-Contract-Snapshots für Artist/Album/Track mit allen alten Schlüsseln.
- Origin-Tests für Disk Usage, `/api/library/stats`, Playlist Explorer und
  mirrored playlist counts.
- Unicode-Namen (`Straße`, Akzente) für Listening-History und Top Tracks.
- Multi-file-Track: einen Pfad löschen, der andere bleibt spielbar und primär.
- Album-Issue-Snapshot und Your-Artists-Modal mit lib2-native Enrichment.

## Bereits gelaufene Checks

Gezielte Backend-Suites: 264 + 59 + 24 + 27 + 33 Tests grün. Frontend-
Querschnitt: 469 Tests grün; Build grün; Check ohne Fehler (377 vorhandene
Warnungen). Ein Full-Pytest-Lauf hing in der Umgebung und wurde abgebrochen.

## Definition of done für die Reparaturrunde

- Kein destruktiver Scan löscht Provider-/Import-/Keeper-Zustand einer gemischten
  Katalogzeile.
- Kein Medienserver-API-Aufruf erhält einen lib2-PK als Server-ID.
- Jeder Worker-Durchlauf setzt Ledger-Zustand oder lässt die Entität bewusst
  unselektierbar; keine erste Zeile kann die Queue verhungern lassen.
- Öffentliche API und bestehende UI-Responses behalten ihre alten Schlüssel.
- „Owned/in library“ lässt sich nicht allein durch Provider-Diskografie erfüllen.
- Neue Scan-Tracks erzeugen wieder History und v2-native Embedded-ID-
  Reconciliation.
