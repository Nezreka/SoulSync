# Library v2 — Arbeits-Kontext für die Weiterarbeit (Stand 2026-07-07)

Dieses Dokument bündelt den Kontext, der sonst nur in der lokalen Assistenten-Memory
lag, damit die Arbeit an `library-overhaul` von jedem Rechner aus fortgesetzt werden
kann. Die drei maßgeblichen Dokumente:

1. **[docs/library-v2-plan.md](library-v2-plan.md)** — der vollständige, revidierte Plan
   (Phasen A–E, locked decisions, Reuse-Index, Verifikations-Regeln).
2. **[core/library2/STATUS.md](../core/library2/STATUS.md)** — was tatsächlich gebaut &
   verifiziert ist, inkl. des „2026-07-07 review-fix pass" und der offenen TODOs.
3. **[docs/library-v2-branch-review-2026-07-06.md](library-v2-branch-review-2026-07-06.md)**
   — der Deep-Dive-Review (alle Findings B1–B9 / M1–M16 mit Begründungen; Update-Block
   oben markiert den Fix-Stand).

## Nicht verhandelbare Designregeln

- **NIE Media-Server-abhängig** (Plex/Jellyfin/Navidrome) — auch nicht für Artwork.
  Artwork = Embedded-Cover aus der Datei (primär) → Metadata-Provider (Fallback) →
  Disk-Cache `<db_dir>/lib2_artwork/`, serviert via `/api/library/v2/artwork/…`.
- **Monitoring spiegelt die Bestandssysteme** über interne DB-Calls: Artist-Monitor ⇄
  Watchlist; Album/Single/Track-Monitor ⇄ Wishlist. Ein Artist ist NUR monitored, wenn
  er auf der Watchlist steht; ein gewishlisteter Song monitored nie den ganzen Artist.
- **Quality-Profile = die app-weite `quality_profiles`-Tabelle**, nie eine
  Parallelkopie. Jeder Mirror-Aufruf trägt `quality_profile_id`; die Pipeline löst das
  Profil LIVE über `core/quality/selection.load_profile_by_id` auf.
- **DB ist Source of Truth**; jede Datei-Location liegt pro File in `lib2_track_files`.
- **SoulSync-Funktionen wiederverwenden**, nicht neu erfinden (Suche/Download/Tagging/
  Repair) — siehe „Reused assets"-Index im Plan.

## Invarianten aus dem Fix-Pass 2026-07-07 (beim Weiterbauen nicht brechen)

- **Jeder** lib2-Dateizugriff läuft über `core/library2/paths.resolve_lib2_path`
  (gespeicherte Pfade sind die Media-Server-Sicht; roher `os.path.exists` bricht
  path-gemappte Setups).
- Background-Threads dürfen **nie** `_profile()` aufrufen — das aktive Nutzerprofil im
  Request-Kontext auflösen und explizit in den Thread reichen (sonst stiller Fallback
  auf Profil 1).
- SQLite-Lock-Regel: das `lib2_*`-Flag-Update committen und den Write-Lock freigeben,
  **bevor** Watchlist-/Wishlist-Methoden laufen (die öffnen eigene Connections).
- Bulk-Re-Monitor-Pfade wenden `_NOT_CONSOLIDATED_SQL` an (api/library_v2.py): Tracks,
  deren Datei bewusst zur kanonischen Duplikat-Seite verschoben wurde, werden nicht
  wieder „wanted".
- „Search Monitored" = `POST /api/wishlist/process` (nie Blind-Auto-Grab).
- Eine Datei an einem `origin='discography'`-Album ⇒ Origin wird zu `'library'`
  (Sichtbarkeitsregel „My Library" = origin='library' ODER monitored).
- `monitor_new_items`-Enforcement: Erstexpansion monitored nie automatisch; die
  Re-Expansions-Erkennung hängt am Marker `lib2_artists.discography_synced_at`
  (nicht an übrig gebliebenen pristinen Provider-Rows).
- Profil-IDs nie hart auf 1 — Fallbacks über
  `core/library2/profile_lookup.default_quality_profile_id`.

## Umgebung / Verifikation

- Verifizieren nur via Docker: `docker build -t soulsync:dev .` mit realer
  Config/DB-Kopie + gemounteter Musik; Feature-Flag `features.library_v2=true`.
  Frontend-Typecheck: `docker build --target webui-builder`.
- Pure-Python-Tests laufen ohne App-Stack: `pytest tests/library2` (inkl.
  Flask-Routen-Tests in `test_api_routes.py`); zuletzt voll grün
  (7411 passed gesamt, 2026-07-07).
- Windows+Docker-Gotcha: nie mit einem Host-`sqlite3` auf die live gebundene
  Container-DB lesen (kann die laufende App locken).

## Bewusst offene Roadmap (nicht vergessen, nicht versehentlich „fixen")

1. **Monitor-Provenance**: Album-Monitoring, das Re-Imports unabhängig vom
   Track-Wishlist-Zustand überlebt (Provenance-/Mode-Spalten statt Ableitung).
2. **Job-Registry**: heute teilen sich Bulk-Monitor/Retag/Upgrade-Scan EINEN globalen
   Job-Slot (`_job_state`) — vor Multi-User-Nutzung auf Job-IDs umstellen.
3. **Breiteres Metadaten-Edit** (Titel/Jahr/Artists) über den Release-Type-Edit hinaus;
   deep-linkbare Album-Detail-Ansicht.
4. **Artist-Scope für Reorganize/Dedup** (brauchen Pfad-Scoping, kein SQL-Filter).
5. **Playlists** (Phase E, bewusst zuletzt).
