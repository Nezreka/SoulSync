# Upstream 3.5.0 auf Library v2 — Übergabe

Dieser Bericht gehört zum lokalen Merge auf `library-overhaul`. Er dient auch
als Einstieg für eine spätere Claude- oder Codex-Session. Die maßgeblichen
Änderungen stehen im Git-Commit; die frühere Chat-Session ist dafür nicht nötig.

## Herkunft und Umfang

- Stand vor dem Merge: `89921432d`.
- Zusammengeführter Upstream: `732a83fe4c8e6cd1c07ec68d72087e9f4c998706`
  (`upstream/dev`, Release 3.5.0).
- Gemeinsame Basis: `0a8a6911d` (3.4.7).
- 295 Upstream-Commits ohne Merge-Commits; die Zwischenreleases 3.4.8,
  3.4.9 und 3.4.10 sind enthalten.
- Sicherungsbranch: `backup/library-overhaul-pre-sync-20261002`.
- Die 48 ursprünglichen Konflikte sind aufgelöst. Der vorhandene Merge wurde
  weitergeführt. Alle Änderungen werden in einem lokalen Merge-Commit gesammelt.
- Es wurde nichts gepusht.

Das Commit-Audit erfasst alle 295 Commits genau einmal: 218 als übernommen,
61 als angepasst und 16 als begründet nicht anwendbar. Die Einzelprüfungen
stehen in [Audit 1](upstream-sync-3.5.0/audit-1.md),
[Audit 2](upstream-sync-3.5.0/audit-2.md) und
[Audit 3](upstream-sync-3.5.0/audit-3.md). Die Einstufung berücksichtigt spätere
Upstream-Korrekturen und Reverts sowie die lokalen Abschlusskorrekturen.

## Übernommen und an den Katalog angepasst

Sample Studio, Discover mit Mood Mixes, Recipes und Inbox, Download Candidate
Inspector und Decision Log, Wishlist-Bulkaktionen und Retry-Profile, Tools,
Statistiken, Automations, Player Theater und Video Discover sind enthalten.
Die neuen Katalogabfragen verwenden Library v2. Dateien liegen auf
`lib2_track_files`, Künstler-Credits auf `lib2_track_artists`, und Wiedergaben
werden über `listening_history.lib2_track_id` mit dem Katalog verbunden.

Die Konfliktauflösung erhält die Regeln dieses Forks: Imports müssen den
Library-v2-Registrierungsvertrag erfüllen, Wishlist-Deduplizierung erfolgt pro
Bibliothek, Download-Batches tragen die einmal gewählte Bibliothekszuordnung,
und Dateilöschungen laufen über das Library-v2-Journal. Die stillgelegten
Legacy-Endpunkte und Jobs wurden nicht wieder eingeführt.

Die gezielten Portierungsprüfungen B1–B15 aus Claudes Notizen sind berücksichtigt:

| Prüfung | Ergebnis |
| --- | --- |
| B1 | SoulSync-Server-ID-Kollisionen erzeugen eine neue ID, statt zwei Songs zusammenzuführen. |
| B2 | Der Enhanced-Search-Cache berücksichtigt die gewählte Bibliothek. |
| B3 | Lossy-Konvertierung registriert den Ersatz vor dem Löschen der Quelldatei. |
| B4 | Staging übernimmt die Bibliothekszuordnung des ursprünglichen Download-Batches. |
| B5 | Album-Folder-Reuse zählt vorhandene Dateien statt nur angelegter Trackpositionen. |
| B6 | Library-Match berücksichtigt den Media-Server. |
| B7 | Die Titel-Fixes des alten Duplicate Detectors sind nachträglich in die Library-v2-Duplikatschlüssel portiert (siehe Nachportierung). |
| B8 | Inbox-Präsenzprüfungen verwenden Library v2 und die ausgewählte Bibliothek. |
| B9 | Dauerhafte manuelle Matches übersetzen Katalog-IDs vor dem Sync in Server-IDs. |
| B10 | AcoustID-Retag schreibt Track-Künstler statt Album-Künstler. |
| B11 | Canonical Resolver und Reorganize erhalten die Provider-IDs des Künstlers. |
| B12 | Reorganize erhält Albumtyp, sekundäre Typen und die Trackzahl des gesamten Releases. |
| B13 | Die alte Aktion Apply Quality Upgrades bleibt ausgeblendet; siehe Auslassungen. |
| B14 | Redownload akzeptiert `lib2:`-IDs. |
| B15 | Sample Studios leere Suche erhält zuletzt hinzugefügte Tracks. |

Weitere Anpassungen betreffen den Suspect-Album-Tag-Job, Corrupt File Detector
mit Ergebnis-Cache und paralleler Prüfung, Quarantäne über das Löschjournal,
Finding-Gruppen mit Fehlerzahlen und Katalog-Artwork, geschützte Cleanup-Roots,
Lossy-Tags und die Library-v2-Abfragen der neuen Mixes, Labels und Künstlerbilder.

## Beim Abschluss zusätzlich abgesichert

- Sample-Dateiauswahl und Hintergrundjobs behalten die Bibliothekszuordnung.
- Eine im Hintergrund gestartete Inbox-Aktualisierung behält den Kontext der
  ausgewählten Bibliothek.
- Server-Mappings bleiben erhalten, wenn eine leere oder unvollständige
  Serverantwort während eines Rescans vorliegt.
- Die Sync-Präsenzprüfung unterscheidet gleiche Track-IDs verschiedener Server.
- Reorganize übergibt gespeicherte Release-Typen und sekundäre Typen an die
  Pfadvorlagen, einschließlich `$albumtype` und `$atypes`.
- Die neue Album-Inspection-Tray bot für Fake-Lossless-Findings zunächst keine
  Reparatur an; die Nachportierung ersetzt das durch die Library-v2-Aktion.
- Tools-Playbooks verwenden die tatsächlich registrierten Job-IDs, damit
  etwa Lyrics, Cover und Tracknummern wirklich bearbeitet werden.
- Fehlgeschlagene Streaming-Abfragen im Download-Monitor ergeben einen
  unbekannten Status, statt laufende Downloads als verschwunden zu behandeln.
- Unveränderte Sample-Schnitte werden ohne ungültigen Rubber-Band-CLI-Aufruf
  gerendert. Engine-Tests hängen nicht mehr von der Installation auf dem Host ab.

## Nachportierung (2026-10-03)

Der erste Abschluss hatte mehrere Upstream-Fixes mit „Job stillgelegt“
abgelehnt. Das ist kein Grund (siehe [Merge-Playbook](upstream-merge-playbook.md));
geprüft wurde, welche Library-v2-Stelle denselben Zweck erfüllt.

| Upstream | Library-v2-Port |
| --- | --- |
| #1315 / ce83731b8: `Rabbit Run - From "8 Mile" Soundtrack` = `Rabbit Run` | `strip_provenance_tail` in `core/library2/duplicate_relationship.py`; wirkt in `_normalized_title` (manuelles Verknüpfen) und `importer.dedup_title_key` (Single↔Album-Verknüpfung beim Import). Klammerform `(from the vault)` und `Far-from Home` bleiben eigene Titel. |
| 558d96864 / 4f09b31b8: Reorganize legt ein Release dort ab, wo der Download es ablegte | Der Pfadbauer legt seine Typentscheidung (`type`, `total_tracks`, `source`, `locked`) im Kontext ab; beide Import-Schreiber speichern sie als `lib2_albums.filed_release`. Der Reorganize-Planer verwendet sie; ein von Hand gesetzter Typ gewinnt, ohne gespeicherte Entscheidung teilt er nach Trackzahl. |
| Fake-Lossless-Fix („Re-download FLAC“) | `_fix_fake_lossless`: Die Datei (`lib2:<Datei-ID>`) wandert über das Löschjournal in die Quarantäne (`.deleted`, wiederherstellbar), der Track wird wieder gewünscht. Upstream behält die Datei und legt eine Wishlist-Zeile an; hier hielte die behauptete FLAC-Qualität die Wanted-Projektion zufrieden. `delete` quarantäniert ohne Ersatz. Dateien außerhalb des Katalogs werden nur auf ausdrücklichen Wunsch entfernt. Tools-Tray, Finding-Liste und Redownload-Dialog suchen über den Track der Datei, nie über die Datei-ID. |
| 33da6be49: A–Z ignoriert führende Satzzeichen | Native Library-v2-Künstlerliste (Sortierung `name`, leerer `sort_name` fällt auf den Namen zurück) und Album-Reihenfolge auf der Künstlerseite verwenden denselben Schlüssel wie die Kompatibilitäts-API. |
| e573bd5fc: Keep Best behält die Kopie, auf die eine Playlist zeigt | Library v2 hat kein automatisches Keep Best. Die Duplikatliste in Manage Tracks nennt pro Version die Server-Playlists (über `lib2_media_server_mappings`), damit die gelistete Kopie behalten wird. |
| Quality-Review und Apply Quality Upgrades | Bereits in `f461c4442` nativ portiert, siehe [Library-v2-Qualitätsupgrades](library-v2-quality-upgrades.md). |

## Bewusst nicht portiert und warum

| Upstream-Funktion | Grund und Verhalten dieses Forks |
| --- | --- |
| 1af23bea2, dfb031a2d, 6f52569c5: Nummern-, römische Teil- und Editionsjahr-Regeln des Duplicate Detectors | Sie schützen dessen unscharfe Ähnlichkeitsgruppierung. Library v2 verknüpft nur gleiche normalisierte Titelschlüssel; Teilnummern und Jahre bleiben im Schlüssel. „Part 1“ und „Part 2“ können nicht zusammenfallen, „Part II“ gegen „Part 2“ ist höchstens eine fehlende, nie eine falsche Verknüpfung. |
| ba545eb3a: fehlender `track_artist` auf Compilations aus Datei-Tags | Library v2 führt Track-Credits in `lib2_track_artists` (Importer, Server-Sync, Provider-Credits). Restrisiko: ein aus Legacy übernommener Compilation-Track ohne Credit kann nicht manuell mit der Kopie des Interpreten verknüpft werden („Tracks do not share an artist“). |
| Quality Upgrade / Quality Upgrade Scanner, Single Album Dedup, Unknown Artist Fixer als Jobs | Ersetzt durch Wanted-Projektion, Quality Profile Audit und die Single↔Album-Verknüpfung des Importers. |
| Bereits entfernte Legacy-Sync-, Delete-, Incremental-Update- und Test-Endpunkte | Der Fork hat diese Wege schon vor diesem Merge durch native Library-v2-Wege ersetzt oder stillgelegt. Ihre Wiederherstellung wäre ein Rückschritt beim Katalogwechsel. |
| Upstream-Änderungen zu #1418/#1420, die Upstream selbst zurückgenommen hat | Der endgültige Upstream-Stand enthält die Reverts. Zurückgenommene Zwischenstände werden nicht separat wieder eingebaut. |

## Offene Entscheidung

| Upstream | Stand |
| --- | --- |
| 7fdd98ac2, 78b9318cf, 4431ac2ab: Library nach Alben durchsuchen (Album-Grid mit Sortierung A–Z, Jahr, zuletzt hinzugefügt) | Library v2 listet nur Künstler. Ein Port wäre eine neue Albenansicht in Library v2 mit eigener Katalogabfrage, keine Wiederherstellung der Legacy-Seite. Nicht als „erledigt“ markiert in `scripts/deleted_path_reviewed.json`, damit es sichtbar bleibt. |

Die ID-Typen bleiben bewusst getrennt: Katalog-ID für Library-v2-Objekte,
Server-ID für Server-Playlists. `get_track_by_server_id()` materialisiert einen
Server-Track; `get_track_by_id()` ist kein austauschbarer Ersatz.

## Prüfung und verbleibende Grenzen

| Prüfung | Ergebnis |
| --- | --- |
| Vollständige Python-Suite in den CI-Testgruppen | Erfolgreich: alle Unterverzeichnisse und die vier Slices von `tests/*.py` wurden geprüft. Die vier Hauptverzeichnis-Gruppen bestehen mit 3.808 / 2.247 / 2.700 / 3.025 Tests. Video: 910; Wishlist: 318. |
| Download-Tests nach der letzten Monitor-Korrektur | 1.529 bestanden, in zwei frischen Prozessen (1.012 + 517). |
| Sample Studio und dauerhafte Sync-Match-Fixtures | 188 bestanden; zusätzlich sind die vollständigen Sample- und Sync-Gruppen im Gesamtlauf grün. |
| Ruff, gesamtes Repository | Keine Fehler. |
| Python `compileall` für die CI-Ziele | Erfolgreich. |
| `webui`: `npm run check` | Keine Fehler; 517 Warnungen. |
| `webui`: `npm test` | 561 Dateien, 9.974 Tests bestanden. |
| `webui`: `npm run build` | Hauptanwendung und Shell erfolgreich gebaut; Hinweis auf große JS-Chunks bleibt. |
| Merge und Artefakte | Keine ungelösten Konflikte, kein Whitespace-Fehler, kein Drift des generierten Route-Trees. |

Für Python wurden dieselben Verzeichnisgruppen und dieselben vier Root-Slices
wie in `scripts/run_tests_chunked.sh` verwendet. Nach einer Unterbrechung des
langen Runner-Prozesses wurden die noch fehlenden Gruppen separat fortgesetzt.
Alle Gruppen sind vollständig erfasst; neue und korrigierte Regressionstests
wurden zusätzlich gezielt ausgeführt. Keine fehlgeschlagenen Tests wurden
durch neue Skip-Marker ausgeblendet.

Ein einzelner großer Pytest-Prozess ist hier kein verlässlicher Prüfmodus:
Dateien können Hintergrundworker und den gemeinsamen Async-Loop beeinflussen.
Der zusätzliche komplette Download-Lauf hing nach 1.012 Ergebnissen am
Source-Stream; beide vollständigen Dateigruppen bestanden getrennt. Lokale
Socket-Schreibzugriffe waren außerdem innerhalb der Ausführungssandbox
blockiert. Die maßgeblichen finalen Läufe fanden außerhalb dieser Einschränkung
mit den temporären Testdatenbanken und der isolierten Testkonfiguration statt.
Test-Shutdown-Logging und Deprecation-Warnungen sind weiterhin vorhanden.

Live-Integrationstests gegen SoundCloud und YouTube bleiben durch die vorhandene
Standardkonfiguration ausgeschlossen. Docker-Deployment- und echte Media-Server-
Akzeptanztests wurden nicht ausgeführt. Persistierte Sample-Analysis-/Stem-
Caches sind weiterhin pro Katalog-Track gespeichert: beim Wechsel zwischen
abweichenden Bibliothekskopien kann eine Neuberechnung erfolgen; Quellenidentität
und Bibliotheksprüfung verhindern die Nutzung fremder Audio-Artefakte.

## Weiterarbeit

Eine folgende Session sollte diesen Bericht und den Merge-Commit lesen. Die
Änderungen sind lokal; ein Push war ausdrücklich nicht beauftragt. Vor einer
späteren Veröffentlichung sind die üblichen Deployment-Prüfungen der jeweiligen
Installation erforderlich.
