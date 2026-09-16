# Integration, Tests und Listening-Verträge

Abgeschlossen am 2026-09-05: Review des HEAD `9fade33e54fa3185b75236e85e44531307b7c0df` gegen `upstream/dev` `8889a81c60ffb458a44bdbcf4bc7137d67140cb5`. Merge-Base `c92b8c87e694ce693251fde88d6c3a7c14d0d41c`. Drei bestätigte P2-Funktionsbefunde; Test-/Formatprobleme separat. Kein Anwendungscode geändert. Alle Reproduktionen verwenden temporäre synthetische Datenbanken.

## Bestätigte Funktionsfehler

### INT-01 / P2: Last.fm schreibt native Track-IDs in die Legacy-Spalte

`core/listening_import/lastfm.py:391-395` löst gegen `lib2_tracks` auf, speichert aber `db_track_id`; `_insert_events_deduped` (:414-428) schreibt nur diese Spalte. Aktuelle Leser wie `get_recent_tracks` (`core/stats/queries.py:366`) und `get_genre_breakdown` (`database/music_database.py:4849`) verwenden `lib2_track_id`.

Repro mit Muse/Uprising: Last.fm-Ereignis wird mit `{db_track_id: 1, lib2_track_id: null}` gespeichert; Recent liefert trotz Albumcover `image_url=null`, `artist_db_id=null`, Genre-Auswertung bleibt leer. Der Startup-Backfill interpretiert `db_track_id` weiterhin als *Legacy*-ID (`database/music_database.py:4407-4413`), sodass bei numerischer Kollision zusätzlich eine falsche Track-Verknüpfung möglich ist.

Fixrichtung: Last.fm auf denselben typisierten Ereignisvertrag und Writer wie Server-/Webplayer-Events bringen; bestehende falsch gespeicherte Last.fm-Ereignisse quellenspezifisch reparieren. Native und Legacy-IDs nicht per pauschalem COALESCE vermischen.

### INT-02 / P2: Chart-Detailansicht verwendet weiter die Legacy-ID

`core/stats/queries.py:476` verbindet `lib2_tracks.id` mit `listening_history.db_track_id`. Der Server-Importer setzt seit diesem Branch hingegen `lib2_track_id` (`core/listening_stats_worker.py:203`), ebenso der native Webplayer. Beim Öffnen eines Chartsegments fehlen dadurch Cover, Künstler- und Trackverknüpfung; bei kollidierenden alten IDs kann eine fremde Katalogzeile erscheinen.

Repro: korrekt gespeichertes Plex-Ereignis mit `lib2_track_id=1` erscheint in der Chart-Detailantwort mit `image_url=null`, `artist_db_id=null`, `db_track_id=null`. Der getrennte Recent-Query funktioniert mit dieser Spalte. Fixrichtung: Chart-Detail auf `lh.lib2_track_id` umstellen und den Vertrag Writer→beide Leser testen.

### INT-03 / P2: Compilation-Tracks werden über den Albumkünstler gesucht

[core/search/library_check.py:67](/home/cyran/Projects/05_Soulsync_fork/core/search/library_check.py:67), Zeilen 67–69, verbindet den Track für die Ownership-Identität ausschließlich mit `lib2_albums.primary_artist_id`. [core/stats/queries.py:83](/home/cyran/Projects/05_Soulsync_fork/core/stats/queries.py:83), Zeilen 83–85, verwendet denselben Albumkünstler für die Wiedergabeauflösung. Trackcredits aus `lib2_track_artists` und `track_artist` bleiben unberücksichtigt. Am Merge-base und auf upstream/dev verwendete die entsprechende Trackabfrage `tracks.artist_id`.

Trigger: Ein vorhandener Muse-Track liegt auf einer Various-Artists-Compilation. Der Katalog kennt sowohl `track_artist='Muse'` als auch den relationalen Muse-Trackcredit und eine aktive Dateizeile. Eine Suche bzw. ein Listening-Event nennt korrekt den Trackkünstler Muse. Die neue Query bildet jedoch ausschließlich den Schlüssel aus Titel + Various Artists. Die Suche meldet den vorhandenen Song als fehlend; Stats kann seine lokale Wiedergabe nicht auflösen. Ein daraus resultierender tatsächlicher Doppeldownload wurde nicht ausgeführt.

Repro mit echten `check_library_presence`- und `resolve_track`-Aufrufen auf dieser synthetischen DB: `in_library=false`, `stats_play_resolution=None`. Es ist kein Namensnormalisierungsproblem: Der genaue Trackcredit steht in beiden Katalogdarstellungen. Fixrichtung: Trackcredits und Albumcredits getrennt projizieren; die gemeinsame Trackauflösung muss die passenden Trackartists berücksichtigen und darf den Albumartist nur als definierten Fallback verwenden. Regressionstest mit Various Artists, einem abweichenden Trackartist und aktiver Datei.

Alle drei Befunde sind neue Inkonsistenzen der Library-v2-Umstellung. Reproduktion: `PYTHONPATH=. .venv/bin/python docs/reviews/2026-09-05/evidence/repro_listening_ids.py`; Ausgabe in [evidence/repro-listening.log](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/evidence/repro-listening.log). Der Lauf endet mit Exit 0, weil die Assertions die bestehenden Fehler bestätigen. Die verwendeten Musik-/Video-/Konfigurationspfade liegen in einem automatisch entfernten temporären Verzeichnis. Keine Live-Daten, Musikdateien oder Medienserver verwendet.

## Tests und Build

- Vollständiger Python-Lauf: **18.228 bestanden, 3 fehlgeschlagen, 4 übersprungen, 7 deselektiert**, Dauer 700,81 s. `evidence/pytest-full.log`. Die 3 Fehler treten auch gemeinsam isoliert auf.
- Vollständiger Vitest-Lauf: **7.795 Tests in 395 Dateien bestanden**, Dauer 156,96 s. `evidence/vitest-full.log`. Ein früherer Lauf hatte einen anschließend isoliert und im neuen Gesamtlauf bestandenen Timing-Fehler in mirrored-tab; kein bestätigter Produktfehler daraus.
- Produktionsbuild aus archiviertem HEAD erfolgreich, getrennt vom laufenden Arbeitsbaum. Hauptbundle ca. 2,58 MB / 721 KB gzip; Shell ca. 69 KB / 19 KB gzip. `evidence/build.log`.
- Ruff und Python-Compilecheck erfolgreich; Frontend-Format-/Typecheck nicht grün. Aktueller und Upstream-Vergleich sind separat protokolliert.
- Umgebung: Python 3.14.7 und Node 26.8.1, lokal installierte Dependencies. CI verwendet Python 3.11 und Node 24. Kein frischer npm-ci-/Container-/Live-Mediaserver-Test.

### Einordnung der Python-Fehler

1. `tests/downloads/test_downloads_task_worker.py:495` erwartet drei Queries, während die gemeinsame Suche zwei erzeugt. Der neue Branch-Test wurde nicht an die von upstream übernommene Reduktion breiter Suchabfragen angepasst. Status-/Retry-Callback laufen; konkrete Assertion erwartet die veraltete Anzahl. Branch-eigene CI-Lücke, kein Nachweis eines kaputten Downloads.
2. `tests/blocklist/test_blocklist_api.py:31` patcht `web_server._search_service`, obwohl die extrahierte Route `api.discover_routes._search_service` verwendet. Test-Fixture wird umgangen. Gleicher Fehler auf unverändertem `upstream/dev` reproduziert.
3. `tests/test_prowlarr_throttle.py:269` zählt zwei direkte AST-Aufrufe, obwohl beide Endpunkte inzwischen dieselbe gebundene Hilfsfunktion nutzen. Gleicher Fehler auf unverändertem `upstream/dev` reproduziert. Das Zeitlimit wird weiter gesetzt; Test sollte Verhalten über beide Endpunkte verifizieren.

Upstream-Repro: `evidence/upstream-pytest-failures.log`.

### Format und Typecheck gegenüber Upstream

`oxfmt --check src`: 47 beanstandete Dateien im Branch, 63 auf upstream/dev. Die kleinere Gesamtzahl bedeutet nicht, dass der Branch formatfrei wäre: neun beanstandete Library-Pfade sind gegenüber dem Upstream-Ergebnis hinzugekommen. Es handelt sich um `-bitrate.ts`, `-library-v2.api.ts`, `-library-v2.play.test.ts`, `-library-v2.play.ts`, `-library-v2.service-links.ts`, `-ui/artist-releases-view.test.tsx`, `-ui/library-v2-page.module.css`, `-ui/library-v2-page.tsx` und `-ui/library-v2-play-buttons.test.tsx`, jeweils unter `webui/src/routes/library/`.

`oxlint --type-check src`: Branch 413 Warnungen und neun Fehler; upstream 719 Warnungen und zwölf Fehler. Die neun verbleibenden Branch-Fehler betreffen Testcode und sind im Upstream-Lauf ebenfalls vorhanden: `delete` auf nicht-optionalen Browser-Globals und fehlendes `now` in Aufrufen von `ServerDisambigModal`. Keine zusätzlichen Typecheck-Fehler gegenüber dieser Vergleichsbasis identifiziert. Die Warnungen wurden nicht sämtlich als einzelne Befunde auditiert. `npm run check` ist damit weiterhin nicht grün; Formatfixes allein genügen nicht.

Logs: `evidence/format.log`, `evidence/upstream-format.log`, `evidence/oxlint.log`, `evidence/upstream-oxlint.log`. Die neun Library-Formatabweichungen und die neue veraltete Python-Assertion sind vor Integration zu bereinigen; geerbte Fehler ausdrücklich mitführen oder im passenden Upstream-Kontext korrigieren.

## Integrationsgrenze

975 geänderte Dateien gegenüber Merge-Base, 179.848 hinzugefügte und 63.323 entfernte Zeilen. Aktueller Upstream enthält 15 zusätzliche Commits, Branch 264 eigene. Review-Basis wurde frisch gefetcht; spätere Upstream-Änderungen nach diesen SHAs sind nicht eingeschlossen. Der Branch ist daher nicht gleichbedeutend mit „aktuell vollständig gegen Upstream integriert“.

Der fehlende Upstream-Commit `75384d3d18d7e106a705e20c48fb801e48845f29` verbessert den universellen Audio-Integritätscheck; konkrete Einordnung unter FI-04 in `file-import.md`. Die übrigen Upstream-Commits betreffen insbesondere Video-/Chat-/Overlay-Verhalten und Testausführung. Sie wurden inventarisiert, aber nicht als bereits portiert bestätigt.

`scripts/deleted_path_upstream_audit.py` wurde ausgeführt: **150 gelöschte Pfade geprüft, nichts Neues, 20 bereits erfasste Entscheidungen**. Dies ist ein Abgleich mit dem bestehenden Entscheidungsledger, keine erneute Bestätigung jeder alten Entscheidung oder vollständiger Feature-Parität. Das Skript wird derzeit nicht aus einem Workflow unter `.github` aufgerufen; als feste Schranke beim nächsten Upstream-Abgleich verwenden.

Eine textuelle Vorschau mit `git merge-tree --trivial-merge` zeigte keine Konfliktmarker. Dies ersetzt weder einen echten Merge noch dessen Tests; es wurde kein Merge vorgenommen. Die Review-Feststellungen sind am unveränderten Branch-HEAD verifiziert, nicht an einem hypothetischen Merge-Ergebnis.
