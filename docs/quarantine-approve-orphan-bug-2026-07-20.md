# Quarantäne-Approve → später als Orphan File erkannt (offen, 2026-07-20)

## Symptom (User-Report, reproduzierbar erlebt, nicht nur einmalig)

1. Ein Song liegt in der Quarantäne (Integrity-/AcoustID-/Bitdepth-Check hatte
   ihn zuvor abgelehnt).
2. User klickt "Approve" (One-Click, `/api/quarantine/<id>/approve`).
3. Der Song wird erfolgreich importiert und erscheint korrekt in der Library.
4. **Später** führt der User einen Orphan-Scan durch (`orphan_file_detector`
   Repair-Job) — derselbe, bereits erfolgreich importierte Song wird als
   „Orphan file: …" gemeldet.

Kein Rename-Schritt involviert. Kein offensichtlicher Bezug zu einem
Neustart/Crash. Der User hat das nicht nur auf diesem Branch, sondern
**bereits zuvor** (also auch auf einem älteren/anderen Stand) erlebt — es ist
kein branch-lokales Library-v2-Problem, sondern etwas im generischen
Reimport-Pfad.

## Bereits ausgeschlossen (empirisch getestet, nicht nur vermutet)

**1. Sidecar-JSON-Serialisierung verliert `track_info` nicht.**
Getestet: ein realistischer `context` (mit `track_info.name/artists/album`)
überlebt `serialize_quarantine_context()` → `json.dumps` → `json.loads`
verlustfrei. Kein Datenverlust beim Sidecar-Roundtrip.

**2. Kein stale `_final_processed_path`/`_final_path` beim Reimport.**
Alle vier `move_to_quarantine(...)`-Aufrufstellen in
`core/imports/pipeline.py` (Zeilen ~610, ~682, ~802, ~984 — Integrity,
Duration, Quality, AcoustID) feuern **bevor** der finale Move
(`safe_move_file(file_path, final_path)`, Zeile ~1433) passiert.
`_final_processed_path`/`_final_path` werden erst danach gesetzt (Zeilen
1299/1426/1433/1471/1487). Der im Sidecar gespeicherte Kontext enthält zum
Quarantäne-Zeitpunkt also **nie** einen bereits berechneten Zielpfad — der
Reimport muss (und tut es) den Zielpfad frisch berechnen. Keine
Pfad-Wiederverwendung eines alten/falschen Ziels.

## Nicht verwechseln mit: dem bereits gefixten Acquisition-Journal

`core/acquisition/recovery.py` (`acquisition_quarantine_recoveries`-Journal,
`prepare_quarantine_recovery`/`finalize_quarantine_recovery`) löst ein
**anderes** Problem: Crash-Atomicity beim "Recover to Staging"-Fallback
(dünne Legacy-Sidecars ohne eingebetteten Kontext) — verhindert einen
Orphan/Duplikat bei einem Absturz **mitten im Move**. Das ist branch-lokal
(hängt am Acquisition-Schema) und bewusst NICHT Teil des PR-Splits an
upstream (siehe `docs/library-overhaul-branch-review-2026-07-19.md`, Teil C,
"Geprüft und als untrennbar verworfen").

Das hier beschriebene Problem ist etwas anderes: **kein Crash beteiligt**,
der Song importiert erfolgreich und vollständig — wird aber trotzdem später
als Orphan erkannt.

## Übrig gebliebene, noch NICHT bestätigte Hypothese

`core/library2/autolink.py::link_download_into_library_v2()` bricht früh ab:

```python
if not direct_track_id and not direct_album_id and (not title or not artist_name):
    return None
```

D.h. ohne direkte lib2-Track/Album-ID (aus `lib2_entity`/`source_info`) UND
ohne Titel+Artist in `track_info` wird **gar keine** `lib2_track_files`-Zeile
angelegt — die (Legacy-)Library-Registrierung
(`record_soulsync_library_entry` / `record_library_history_download`) läuft
davon unabhängig weiter und kann trotzdem erfolgreich sein (der Song
erscheint also korrekt in der UI/Legacy-Library), nur eben ohne
lib2-Gegenstück.

`core/repair_jobs/orphan_file_detector.py` baut seine "bekannten Pfade" **nur**
aus `lib2_track_files` (via `active_file_subjects()`, siehe
`core/library2/maintenance_subjects.py`) plus einem Tag-/Dateiname-Fallback
gegen `lib2_tracks`/`lib2_albums`/`lib2_artists` (nicht gegen die Legacy-
Tabellen!). Fehlt die lib2-Zeile komplett, kann **keiner** der drei
Fallback-Mechanismen (Pfad-Suffix, Tag-Match, Dateiname-Parse) den Song
finden → garantierter, nicht-intermittierender Orphan-Fund bei jedem Scan.

**Wann könnte `track_info` leer/unzureichend sein?** Bei „Simple Downloads"
(Direkt-Grab über die Such-Seite ohne Provider-Anreicherung) ist
`track_info` in den existierenden Tests (`tests/imports/test_import_pipeline.py`,
z. B. `test_verification_wrapper_handles_simple_download`) explizit `{}`.
Ob das auch der reale Pfad ist, über den der User quarantänierte Songs
approved hat, ist **nicht geklärt** — das war der offene Punkt, an dem die
Untersuchung in der letzten Session unterbrochen wurde.

## Bereits vorhandene Test-Infrastruktur (kein Library-v2-Schema nötig)

`tests/imports/test_import_pipeline.py` hat bereits einen funktionierenden,
lib2-freien Test-Harness für `post_process_matched_download[_with_verification]`
(gemockte `runtime`, `_Config`, `_FakeAcoustidVerifier`, `_ImmediateThread`,
kein `imported_conn`/`lib2_enabled`-Fixture nötig). Siehe insbesondere:

- `test_verification_wrapper_handles_simple_download` (Zeile ~104) — Muster
  für einen erfolgreichen Simple-Download-Durchlauf.
- `test_quarantine_failure_preserves_file_instead_of_deleting` (Zeile ~831) —
  Muster, wie man den echten Quarantäne-Trigger (nicht gemockt) über
  `check_audio_integrity` erzwingt.

Ein sauberer Reproduktionstest sollte diese beiden Muster kombinieren:
echte Quarantäne erzwingen → `approve_quarantine_entry()` real aufrufen →
`post_process_matched_download` erneut mit dem restaurierten Kontext laufen
lassen (bypass via `_skip_quarantine_check='all'`, wie die echte Approve-Route
es setzt) → prüfen, ob `link_download_into_library_v2` (ggf. gespyt statt
gemockt) tatsächlich aufgerufen wird und eine `lib2_track_files`-Zeile
erzeugt — für BEIDE Fälle (Simple Download mit leerem `track_info` UND
regulärer Match mit vollem `track_info`), um zu sehen, ob nur der eine oder
auch der andere Fall betroffen ist.

## Relevante Dateien/Zeilen (Stand 2026-07-20, Branch `library-overhaul`)

- `web_server.py:8563` — `/api/quarantine/<entry_id>/approve` (One-Click Approve, dispatcht Reimport async via `threading.Thread`)
- `web_server.py:9177` — `/api/quarantine/<entry_id>/recover` (Fallback für dünne Sidecars)
- `core/imports/quarantine.py:469` — `approve_quarantine_entry()`
- `core/imports/quarantine.py:527` — `recover_to_staging()`
- `core/imports/pipeline.py:490` — `post_process_matched_download()`
- `core/imports/pipeline.py:610/682/802/984` — die vier `move_to_quarantine(...)`-Aufrufstellen (alle vor dem finalen Move)
- `core/imports/side_effects.py:281` — `record_download_provenance()`, ruft `link_download_into_library_v2` auf (Zeile ~397-398)
- `core/library2/autolink.py:355` — `link_download_into_library_v2()`, der Early-Return-Guard
- `core/library2/maintenance_subjects.py:60` — `active_file_subjects()`, Quelle der "bekannten Pfade" für den Orphan-Scan
- `core/repair_jobs/orphan_file_detector.py:123` — der Scan selbst (Pfad-Suffix → Tag-Fallback → Dateiname-Fallback)

## Nächster Schritt

Reproduktionstest wie oben beschrieben bauen (lib2-frei, nur mit
`tests/imports/test_import_pipeline.py`-Harness + gespytem
`link_download_into_library_v2`), für beide Download-Arten. Erst wenn der
Test rot ist und den Mechanismus zeigt, den Fix angehen (systematic-debugging:
kein Fix ohne bestätigte Root Cause).
