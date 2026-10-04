# Upstream-PR-Kandidaten (aus dem 3.5.0-Sync)

**Stand 2026-10-04:** Nr. 1–5 plus Completion-Streams (#1199) und Cancel-Profil
sind als PR #1507 (`fix/dev-bugs-from-library-v2`) an upstream gegangen.
Offen bleibt nur der ungeprüfte Verdacht unten.

Fehler, die auch in `upstream/dev` (Nezreka/SoulSync) stecken. Bei uns behoben;
für Upstream je ein eigener Branch auf `upstream/dev`, kleiner Fix plus Test,
der ohne den Fix fehlschlägt. Geprüft per Code-Lektüre; nur Nr. 3 lokal
nachgestellt.

## 1. Album-Tray öffnet den Redownload für einen fremden Track (destruktiv)

- `core/repair_jobs/fake_lossless_detector.py`: Findings mit `entity_id=None`.
- `webui/src/routes/tools/-ui/album-inspection-tray.tsx`: `isRedownloadFinding`
  enthält `fake_lossless`, prüft aber keine `entity_id`.
- `findings-surface.tsx` / `operations-studio.tsx`: Modal bekommt
  `entity_id || String(finding.id)` → Finding-ID wird als Track-ID benutzt.
- `core/library/redownload.py`: `SELECT file_path FROM tracks WHERE id = ?` mit
  dieser ID, `delete_old_file` ist standardmäßig an → ersetzt/löscht die Datei
  eines unbeteiligten Tracks.
- Nebenbei: „Resolve Album“ fixt alle pending Findings (auch ohne Handler) und
  meldet trotzdem Erfolg; Blurb „Review only.“ widerspricht dem Backend-Verb
  „Re-download FLAC“.
- Fix: Modal nur mit `entity_id` anbieten, nie auf `finding.id` ausweichen;
  Fix-All nur über Typen mit Fix-Label.

## 2. Download-Monitor: fehlgeschlagene Streaming-Abfrage = leere Liste

- `core/downloads/monitor.py` (`_get_live_transfers`): Bei slskd-Fehler wird
  `_LIVE_TRANSFERS_FETCH_FAILED` zurückgegeben, bei
  `engine.get_all_downloads(...)`-Fehler nur geloggt.
- Folge: laufende Streaming-Downloads fehlen in der Liste → nach 3 Ticks
  „Download disappeared from transfer list 3 times“ (Upstreams eigener
  Kommentar in `_check_all_downloads` beschreibt genau diesen Schaden).
- Fix: in beiden `except`-Zweigen `_LIVE_TRANSFERS_FETCH_FAILED` zurückgeben.

## 3. Sample Studio: unveränderter Chop scheitert mit installiertem Rubber Band

- `core/sample/render.py` `_apply_rubberband_cli`: ohne Pitch/Tempo wird
  `rubberband -q in.wav out.wav` aufgerufen → Exit 2 („must specify at least one
  ratio option“, lokal mit 4.0.0 nachgestellt) → RuntimeError.
- Dockerfile installiert `rubberband-cli`; Version im Image nicht geprüft.
- Tests gehen von fehlendem Rubber Band aus und scheitern auf Hosts mit CLI.
- Fix: No-op früh zurückgeben (`abs(pitch) < 0.01 and abs(tempo-1) <= 1e-3`);
  Tests simulieren das fehlende CLI explizit. Vorlage: unser `core/sample/render.py`.

## 4. Operations Studio: Playbooks nutzen nicht existierende Job-IDs

- `webui/src/routes/tools/-ui/operations-studio.tsx` (`STRATEGIC_PILLARS`,
  `PLAYBOOK_PRESETS`): IDs werden über `jobMap` gefiltert und still verworfen.
- Falsch → richtig: `lyrics_fetcher` → `missing_lyrics`, `artwork_fetcher` →
  `missing_cover_art`, `genre_tag_cleaner` → `genre_cleanup`,
  `track_number_fixer` → `track_number_repair`, `mbid_resolver` →
  `mbid_mismatch_detector`, `duplicate_finder` → `duplicate_detector`,
  `empty_folder_remover` → `empty_folder_cleaner`, `quality_upgrade_detector` →
  `quality_upgrade_scanner`/`quality_upgrade`, `short_preview_detector` →
  `short_preview_track`.
- Test: jede ID in Pillars/Playbooks existiert in der Job-Registry.

## 5. Reassign-Modal: Bild-URL ungeschützt im CSS-`url()`

- `webui/src/routes/artist-detail/-ui/reassign-modal.tsx` setzt
  `backgroundImage: `url('${imageUrl}')`` (Hero, Ergebnisliste, Alben) mit
  Provider-URLs. Ein `'` oder `\` in der URL beendet das CSS-Literal.
- Unsere frühere Kopie (Library v2) hatte dafür `cssUrl()` (FE-10); seit
  2026-10-04 nutzt Library v2 upstreams Modal, der Schutz fehlt also auch bei uns.
- Fix: `cssUrl(url)` escaped `\` und `'`, entfernt Zeilenumbrüche; Test mit
  einer URL, die ein `'` enthält.

## Ungeprüfter Verdacht

Upstreams „Re-download FLAC“ behält die Fake-FLAC und legt nur eine
Wishlist-Zeile an. Prüfen, ob der Download dann überhaupt startet bzw. die
vorhandene „FLAC“ ersetzt, oder als „schon vorhanden“ übersprungen wird.
