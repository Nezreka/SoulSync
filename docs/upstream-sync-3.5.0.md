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
| B7 | Änderungen am alten Duplicate Detector entfallen, weil dieser Job stillgelegt ist. |
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
- Die neue Album-Inspection-Tray bietet für Fake-Lossless-Findings keine
  Reparatur oder Redownload-Aktion an.
- Tools-Playbooks verwenden die tatsächlich registrierten Job-IDs, damit
  etwa Lyrics, Cover und Tracknummern wirklich bearbeitet werden.
- Fehlgeschlagene Streaming-Abfragen im Download-Monitor ergeben einen
  unbekannten Status, statt laufende Downloads als verschwunden zu behandeln.
- Unveränderte Sample-Schnitte werden ohne ungültigen Rubber-Band-CLI-Aufruf
  gerendert. Engine-Tests hängen nicht mehr von der Installation auf dem Host ab.

## Bewusst nicht portiert und warum

| Upstream-Funktion | Grund und Verhalten dieses Forks |
| --- | --- |
| Alte Library-Grid- und Album-Browse-Oberfläche, `/api/library/albums` | Die Library-Seite wird vollständig von Library v2 bereitgestellt. Die Legacy-Oberfläche würde einen zweiten, unpassenden Katalogpfad einführen. |
| Duplicate Detector und dessen Titel-Normalisierungsänderungen | Der Job ist hier stillgelegt. Library-v2-Duplikate beruhen auf Recording-Identität und den bestehenden Katalogfunktionen. |
| Quality Upgrade / Quality Upgrade Scanner und Single Album Dedup / Unknown Artist Fixer | Diese Legacy-Jobs bleiben stillgelegt. Sie setzen das alte Datenmodell und dessen Findings voraus. Ihre Tests und UI-Einstiege werden nicht wiederhergestellt. |
| „Upgrades show up in the library“ und Apply Quality Upgrades | Die Upstream-Funktion verarbeitet Findings der stillgelegten Quality-Jobs. Library v2 behandelt gewünschte Qualität über seine vorhandenen Wanted-Funktionen. Der Kompatibilitätshandler bleibt registriert, die neue Aktion wird nicht angeboten. |
| Fake-Lossless-Fix und zugehöriger Redownload-/Bulk-Fix-Einstieg | Der Detector bleibt zur Prüfung sichtbar. Automatischer Ersatz wurde nicht als Library-v2-Dateitransaktion portiert; die bestehende Entscheidung dieses Branches bleibt deshalb „review-only“. |
| Bereits entfernte Legacy-Sync-, Delete-, Incremental-Update- und Test-Endpunkte | Der Fork hat diese Wege schon vor diesem Merge durch native Library-v2-Wege ersetzt oder stillgelegt. Ihre Wiederherstellung wäre ein Rückschritt beim Katalogwechsel. |
| Upstream-Änderungen zu #1418/#1420, die Upstream selbst zurückgenommen hat | Der endgültige Upstream-Stand enthält die Reverts. Zurückgenommene Zwischenstände werden nicht separat wieder eingebaut. |
| Vollständige Übernahme der Interpunktions-Sortierung in der nativen Library-v2-A–Z-Liste | Die Kompatibilitäts-Künstler-API übernimmt die Upstream-Korrektur. Die native Seite verwendet ihre vorhandenen `sort_name`-Werte; Namen wie `*NSYNC` können dort weiterhin vor Buchstaben stehen. Eine Änderung der nativen Sortierschlüssel und vorhandenen Katalogwerte bleibt eine separate Folgeaufgabe. |

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
