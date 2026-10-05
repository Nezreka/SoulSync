# Downloads, Library und Tools: Konsistenzuntersuchung

**Implementierungsstand:** Die unten beschriebenen Fehler beziehen sich auf den
untersuchten Commit. Die Umsetzung im aktuellen Arbeitsstand ist im Abschnitt
„Umsetzung“ dokumentiert.

Untersucht am 05.10.2026, Branch `library-overhaul`, Commit `8f0b71dc5`.
Der Checkout wurde von `9b2c0f5b4` per `git pull --ff-only origin library-overhaul`
aktualisiert. Dies ist eine Codeuntersuchung mit lokalen Offline-Reproduktionen.
Es bestand kein Zugang zum Produktionsserver; dessen Version, Einstellungen,
Dateien und konkrete Findings sind noch nicht geprüft.

## Ergebnis

Die beobachteten Unterschiede sind durch den aktuellen Code erklärbar. Der
Downloadstatus, das angezeigte Library-Cover, der Library-Tagstatus und die
Tools-Findings beantworten verschiedene Fragen. Zusätzlich gibt es konkrete
Fehler bei der Weitergabe von Erfolg, beim Tracktotal-Fallback und bei der
Berücksichtigung von Einstellungen. Ein grüner Download oder Refresh garantiert
derzeit keine korrekten, vollständig gespeicherten Metadaten.

| Weg | Tatsächliche Prüfung | Was ein erfolgreicher Lauf nicht belegt |
| --- | --- | --- |
| Download/Import | Verarbeitung, Zielpfad, Library-Registrierung; Metadaten werden vorher angereichert | Dass alle Tagwrites, Coverwrites und Metadatenprüfungen erfolgreich waren |
| Library: Refresh & Scan | Dateiverfügbarkeit, Pfadkorrekturen, Qualität, Tag-Snapshot; Bildcache wird gelöscht | Dass Tracknummern gegen die Release-Tracklist geprüft oder Tools-Findings aktualisiert wurden |
| Library: Tags vorhanden | Erwartete Felder sind im Snapshot nicht leer | Dass vorhandene Werte richtig sind |
| Library: sichtbares Cover | Cache, manuelle Auswahl, eingebettetes Bild oder Provider-Fallback | Dass Album-Bildfeld, Einbettung und Cover-Datei alle vorhanden sind |
| Tools: Cover Art Filler | Album-Bildfeld, Einbettung eines repräsentativen Files und Cover-Datei | Dass ein angezeigtes Cover fehlt; ein einzelner anderer Speicherort kann fehlen |
| Tools: Track Number Repair | Vergleich mit Katalog-Tracklist, Nummer, teilweise Gesamtzahl, Disc und Dateiname | Dass die eigentliche Trackposition falsch ist; allein Gesamtzahl/Dateiname/Disc können einen Befund auslösen |

## Bestätigte Befunde

### 1. Refresh & Scan ruft die Tools-Prüfungen nicht auf

`api/library_v2.py:4989` startet einen eigenen Hintergrundjob.
`api/library_v2.py:5046` ruft `core.library2.scan.rescan_files(..., manual=True)`
auf. Es gibt dort keinen Aufruf von `MissingCoverArtJob`, `TrackNumberRepairJob`
oder eine Revalidierung ihrer Findings.

`core/library2/tag_cache.py:64` berechnet fehlende Tags durch eine reine
Vorhandenseinsprüfung. Eine vorhandene Nummer **3** ist deshalb genauso
"vorhanden" wie die korrekte Nummer **7**. Der Refresh liest die **3** korrekt
neu ein, bewertet ihre Richtigkeit aber nicht. Die UI-Zusammenfassung
`webui/src/routes/library/-ui/library-v2-page.tsx:900` zeigt Datei-/Pfadstatistik,
keine Anzahl geprüfter oder offener Metadatenprobleme.

**Offline-Nachweis:** Echter FLAC-Tag mit Nummer 3 bei Katalogposition 7;
Refresh schreibt eine leere Metadatenlückenliste, Track Number Repair meldet
3 → 7. Die FLAC-Testdatei ist ein synthetischer Metadatencontainer ohne
Audiodaten; hier wird keine Audioqualität getestet.

### 2. Library kann ein Cover anzeigen, während Tools "Missing artwork" meldet

`core/library2/artwork.py:680` kann das Library-Bild aus eingebetteter Albumart
bauen. Dieser Cacheaufbau füllt das Albumfeld `image_url` nicht automatisch.
Der Cover-Job meldet in `core/repair_jobs/missing_cover_art.py:76` dagegen auch
ein leeres Album-Bildfeld als eigene Lücke, selbst wenn Einbettung und Sidecar
vorhanden sind. Der Befund heißt trotzdem pauschal "Missing artwork".

**Offline-Nachweis:** Eingebettetes PNG und `cover.png` vorhanden, Album-Bildfeld
leer. Der echte Library-Artwork-Builder erstellt erfolgreich ein JPEG ohne
Provider-Abfrage; Refresh findet keine fehlenden Tags. Der Cover-Job meldet für
dasselbe Album `db_missing=True`, `embed_missing=False`.

Die Tools-Detailansicht `webui/src/routes/tools/-ui/finding-detail.tsx:765` zeigt
die konkreten booleschen Lückengründe nicht an. `sidecar_missing` wird vom Job
außerdem berechnet, aber nicht als eigenes Detailfeld gespeichert. Das macht
einen reparierbaren Speicherortfehler für den Nutzer zu "kein Bild".

### 3. Cover-Job ignoriert die deaktivierte Einbettung

Der Download respektiert `metadata_enhancement.embed_album_art`
(`core/metadata/enrichment.py:247`). Der Cover-Job setzt `embed_missing` in
`core/repair_jobs/missing_cover_art.py:77` unabhängig von dieser Einstellung.
Für die Sidecar-Einstellung existiert hingegen eine entsprechende Bedingung.

**Offline-Nachweis:** Einbettung bewusst deaktiviert, Album-Bildfeld und
`cover.png` vorhanden, Datei ohne eingebettetes Bild: Der Job erzeugt dennoch
einen Befund wegen `embed_missing=True`.

### 4. Unbekannte Album-Gesamtzahl wird zu 1 und produziert neue Mismatches

`core/metadata/source.py:1386` und `:1393` verwenden
`album_ctx.get("total_tracks", 1)`. Wenn das Feld fehlt, entsteht eine behauptete
Gesamtzahl 1, statt "unbekannt". Andere Stellen behandeln 0/None als unbekannt
und können die Gesamtzahl korrekt weglassen.

**Offline-Nachweis:** Matched-Kontext mit Trackposition 7, Albumname und ohne
`total_tracks`: Die echte Source-Aufbereitung erzeugt `total_tracks=1`; der
ID3-Formatter erzeugt `7/1`. Mit echten Mutagen-ID3-Frames meldet Track Number
Repair gegen eine 13-Track-Liste einen Mismatch **allein für 1 → 13**. Die
aktuelle und korrekte Trackposition sind beide 7.

Zusätzlicher Formatunterschied: Der neue Vorbis-Writer speichert `TRACKNUMBER`
und `TRACKTOTAL` getrennt. Der Repair-Reader
`core/repair_jobs/track_number_repair.py:594` liest für FLAC/Vorbis nur
`tracknumber`, nicht `tracktotal`. Ein FLAC mit Tracknummer 7 und falschem
`tracktotal=1` erzeugt gegen dieselbe Liste **keinen** Befund. Für Ogg Opus
fehlt dort außerdem die Formatbehandlung; dieser letzte Punkt ist nur statisch
beobachtet und nicht mit einer Opus-Datei reproduziert.

### 5. Erfolg wird über mehrere Grenzen hinweg zu optimistisch weitergegeben

- `core/metadata/enrichment.py:114` und `:266` ignorieren das boolesche Ergebnis
  von `save_audio_file`. Diese Funktion darf bei einem abgebrochenen atomaren
  Write ausdrücklich `False` zurückgeben und die Originaldatei unverändert lassen.
- Der Rückgabewert der Cover-Einbettung wird in `:248` ebenfalls ignoriert.
- Nach `verify_metadata_written` in `:268` folgt auch bei negativem Ergebnis
  `return True`. Die Prüfung selbst kontrolliert hauptsächlich Titel/Artist,
  keine Release-Position oder vollständige Cover-Persistenz.
- `core/imports/pipeline.py:2166` ignoriert den Rückgabewert der Anreicherung.
  Der spätere Erfolgspfad setzt in `:2983` `metadata_enhanced=True` unabhängig
  davon. Er verlangt registrierte Library-Datei und vorhandenen Zielpfad, nicht
  eine erfolgreiche gemeinsame Metadatenvalidierung.
- `download_cover_art` kann bei fehlendem Bild, Fetch- oder Schreibfehler
  zurückkehren; der Import läuft weiter. Bei einer Quelle ohne verfügbare Art
  ist "Audiodatei erfolgreich, Art unvollständig" legitim, aber muss sichtbar sein.

**Offline-Nachweis:** Der echte Enricher wird mit `save_audio_file=False` als
injiziertem Write-Abbruch ausgeführt. Er meldet trotzdem Erfolg; der neue Titel
steht anschließend nachweislich nicht auf Disk. Die Weitergabe im vollständigen
Downloadweg wurde statisch verfolgt; kein vollständiger Download wurde ausgeführt.

### 6. Extern korrigierte Probleme können als alte Findings stehen bleiben

Ein Refresh aktualisiert keine Tools-Findings. Auch der allgemeine Cleanup nach
einem Tool-Lauf (`core/repair_worker.py:1373`) entfernt nur Befunde, deren Datei
verschwunden ist: `retire_vanished_findings` in `:2237`.
Eine noch vorhandene, inzwischen korrekt getaggte Datei passiert die Abfrage
in `:2279`; der Befund bleibt pending. Der saubere Prüfdurchlauf erzeugt einfach
keinen neuen Befund und schließt damit nicht automatisch den alten.

**Offline-Nachweis:** Pending-Tracknummer-Finding bei inzwischen korrekter Datei;
Refresh und tatsächlicher Vanished-Findings-Cleanup lassen es pending. Das
belegt diese zwei Wege; es schließt zusätzliche spezifische Aufräumwege anderer
Jobs nicht aus. Eine Reparatur über den Finding-Fix hat einen eigenen Statuspfad.

### 7. Release-Edition und Release-Gruppe können unterschiedlich bewertet werden

`_api_tracks_for_subject` in `core/repair_jobs/track_number_repair.py:1682`
verwendet bei höchstens einer Edition weiterhin die Gruppen-Trackliste,
nicht die vorhandene Edition. Unterscheiden sich beide, kann das Tool eine
eigentlich passende Editionsnummer als falsch beurteilen.

**Offline-Nachweis:** Gruppenposition 3, einzelne Editionsposition 7, Track der
Edition zugeordnet: Der Helper wählt 3. Dies belegt die Auswahlregel mit
synthetisch widersprüchlichen Katalogdaten. Ob solche Daten für die betroffenen
Downloads in Produktion existieren, ist noch offen.

## Warum neue Downloads überhaupt betroffen sein können

Ein Download kann bereits gute Quell-Tags enthalten, aber der Import baut neue
Tags aus dem Match-/Download-Kontext. Unvollständige oder anders zugeordnete
Release-Metadaten können dann Werte wie die falsche Gesamtzahl liefern. Sind
weder Quellnummer noch Metadaten verfügbar, enthält die Pipeline weiterhin
Fallbacks auf Dateiname, begrenzte Verzeichnisreihenfolge und letztlich 1
(`core/imports/pipeline.py:2074` bis `:2091`). Das sind Schätzungen, keine
bestätigte Release-Position. Die Tools vergleichen später gegen eine andere,
gegebenenfalls vollständigere Katalogansicht.

Coverbytes können unabhängig voneinander in Datei, Ordner und UI-Cache landen.
Ein Fetch kann beim Import scheitern und beim späteren Anzeigen funktionieren;
das nachträgliche Anzeigen repariert die Originaldatei nicht. Welcher dieser
Wege die konkret beobachteten Downloads betrifft, braucht Produktionsbelege.

## Empfohlene Reparaturarchitektur

Die drei Aufrufer sollten **dieselbe lesende Validierung** für denselben
Datei-/Release-Scope verwenden. Das bedeutet nicht, sämtliche Tools-Jobs beim
Refresh auszuführen: Einige Jobs schreiben, reorganisieren oder verursachen
große externe Lookups.

1. Gemeinsamer Prüfdienst für Dateitags, Release-Edition, Titel/Track/Disc/Totals
   und Artwork-Speicherorte. Einheitliche Ergebnisse: korrekt, fehlt, abweichend,
   unbekannt, nicht geprüft oder gemäß Einstellung nicht erforderlich.
2. Beim Download nach dem letzten Write von Disk zurücklesen, Ergebnisse
   speichern und Erfolg getrennt nach Dateiimport und Metadatenzustand melden.
   Unbekannte Nummern/Gesamtzahlen nicht als bestätigte 1 speichern.
3. Refresh ruft dieselbe lesende Prüfung im begrenzten Scope auf und zeigt
   die Anzahl offener Probleme. Bestehende Findings mit nachgewiesen behobenem
   Problem schließen; bei fehlender Lesbarkeit oder fehlender Referenz bleiben
   sie offen/unbekannt. Kein pauschales Löschen nach einem teilweise fehlgeschlagenen Scan.
4. Tools konsumieren dieselben Ergebnisse. Zusätzliche Reparaturvorschläge und
   teure Analysen bleiben separat. Album-Bildfeld, Cache, Einbettung und Sidecar
   in den Findingtexten getrennt benennen und Konfiguration respektieren.
5. Ein gemeinsamer, editionsgebundener Metadatenpayload für Import und Prüfung;
   gleiche Reader für ID3, Vorbis, MP4 und Opus, einschließlich getrennter Totals.

## Präzisiertes Soll-Verhalten der Library-Tabelle

Der Nutzer hat am 05.10.2026 ausdrücklich präzisiert: Prüfergebnisse sollen
direkt in den zuständigen Datenbanktabellen gespeichert und in den vorhandenen
Spalten **File size**, **Quality**, **Check**, **Metadata** sichtbar werden.
Metadata muss die erkannten Probleme anzeigen und anklickbar bleiben; die
passenden Fixes sollen dort oder über Tools ausführbar sein.

| Spalte | Persistierte Grundlage und gewünschte Bedeutung |
| --- | --- |
| File size | Tatsächliche Größe der konkreten Datei in `lib2_track_files.size` |
| Quality | Gemessene Format-/Bitrate-/Sample-Rate-/Bit-Depth-Werte und daraus abgeleitete Qualität in `lib2_track_files` |
| Check | Tatsächlicher Identitäts-/Verifizierungszustand (`verification_status`, `acoustid_status`); ein Tagscan allein bestätigt keine Audioidentität |
| Metadata | Gemeinsam ermittelte fehlende **und abweichende** Metadaten, Prüfstatus und offene, dem File/Track/Album zugeordnete Befunde |

Die bestehenden Library-v2-Tabellen in der Musikdatenbank bleiben die Grundlage.
`lib2_track_files` hält beobachtete Dateifakten und Tag-Snapshots;
`lib2_tracks`, `lib2_albums` und Release-Editionen halten die Referenz-/Sollwerte.
Ein Scan darf einen falschen Dateitag nicht ungeprüft zum richtigen Referenzwert
machen. Die gemeinsame Persistenzgrenze aktualisiert Dateifakten, prüfbezogene
Snapshots und zugehörige `repair_findings` konsistent. Die Metadata-Zusammenfassung
wird aus diesen gespeicherten Ergebnissen abgeleitet; Library und Tools dürfen
keine voneinander unabhängigen Fehlerlisten führen. Ob zusätzliche Prüfspalten
oder eine eigene Ergebnistabelle erforderlich sind, ist beim Implementieren
festzulegen; es wurde bisher keine Schemaänderung vorgenommen.

Der bereits vorhandene Metadata-Klick startet derzeit `fill-tag-gaps`
(`webui/src/routes/library/-ui/library-v2-page.tsx:9735`,
`api/library_v2.py:4916`): Provider-Anreicherung und Tagwrite. Das ist der
passende UI-Einstieg, aber noch kein universeller Fixpfad für Tools-Findings.
Er soll den konkreten gespeicherten Befund über denselben Reparaturdienst wie
Tools beheben, mit demselben begrenzten Scope und denselben Einstellungen.

Gewünschter Ablauf: **Refresh → prüfen und persistieren → Metadata zeigt Probleme
→ Klick dort oder in Tools → passenden Fix ausführen → Datei zurücklesen und
erneut prüfen → Befundstatus persistieren → beide Ansichten aktualisieren.**
Eine klare, sichere Reparatur kann direkt gestartet werden. Wenn beispielsweise
die Release-Edition ungeklärt ist, muss die nötige Auswahl sichtbar werden,
bevor eine Nummer geraten wird. `Metadata ✓` erscheint erst nach positiver
Nachprüfung; Teilfehler und unprüfbare Werte bleiben sichtbar. Retag und Organize
nutzen dieselbe Nachprüfung für die von ihnen tatsächlich veränderten Dateien.

### Normale Tools-Jobs als gleichberechtigte Ergebnislieferanten

Die weitere Nutzerpräzisierung gilt für alle normalen Tools-Jobs: Unabhängig
davon, ob ein Lauf manuell, geplant oder über eine Automation gestartet wurde,
sollen seine Befunde und erfolgreichen Änderungen über dieselbe Persistenzgrenze
in der Musikdatenbank landen und in der Library sichtbar werden. Die Tools
schreiben Findings bereits in `repair_findings`; die fehlende gemeinsame
Auswertung und Aktualisierung der Library muss ergänzt werden.

Ergebnisse sollen pro verarbeitetem Objekt oder in kurzen, abgeschlossenen
Batches gespeichert werden. Ein langer Library-weiten Job darf die Anzeige
bereits bestätigter Befunde nicht bis zum Ende verzögern. Nach erfolgreicher
Persistenz aktualisiert ein gemeinsames Änderungssignal die betroffenen
Library-Zeilen und Tools-Ansichten; erneutes Laden liest denselben Zustand aus
der Datenbank. Datei-, Track-, Album- und Benutzer-Library-Zuordnung sowie
Prüfquelle und Zeitpunkt müssen erhalten bleiben.

Metadatenbefunde erscheinen in Metadata; Audio-/Identitätsbefunde im passenden
Check-Status oder dessen Details; gemessene Datei-/Qualitätswerte in den
entsprechenden Spalten. Erfolgreiche Reparaturen schließen nur nach erneuter
Prüfung die tatsächlich behobenen Befunde. Ein fehlgeschlagener Tool-Lauf wird
als Jobfehler dargestellt und darf nicht pauschal alle Tracks als defekt
markieren. Ein nachweislich dateibezogener Lese-/Schreibfehler wird zusätzlich
am betroffenen Objekt sichtbar. Unvollständige Prüfläufe liefern keinen
pauschalen grünen Status und schließen keine ungeprüften alten Befunde.

## Umsetzung

Die gemeinsame lesende Validierung liegt in `core/library2/validation.py` und
verwendet die vorhandenen Tagreader, Retag-Payloads, Editionstabellen und
`repair_findings`. Es wurde keine neue Datenbanktabelle eingeführt.

- Import einschließlich letzter nachträglicher Tagwrites und Companion-Dateien,
  Refresh, Retag und die Metadaten-Tools persistieren dieselben Prüfzustände.
  Tatsächliche Dateigröße wird auch bei fehlgeschlagenem Qualitätsprobe gespeichert;
  die Quality-Anzeige verwendet die Dateispalten.
- Referenzwerte bleiben von gelesenen Dateitags getrennt. Eine eindeutig
  zugeordnete Edition liefert Albumtitel, Trackposition, Disc und bekannte Totals;
  mehrdeutige oder unvollständige Referenzen bleiben unbekannt.
- Artist „Refresh & Scan“ umfasst alle zugehörigen Alben und Dateiversionen der
  aktuellen Library. Retag- und Organize-Preview verwenden dieselbe Editions-
  und Override-Referenz; Organize rät bei unbekannter Position keinen Dateinamen.
- Gemeinsame Nummernreader berücksichtigen ID3, MP4 und getrennte Vorbis-/Opus-Totals.
  Unbekannte Album-Gesamtzahlen werden nicht zu 1. Abgebrochene Writes und negative
  Nachprüfungen melden keinen Metadaten-Erfolg.
- Album-Bildfeld, eingebettetes Bild und Sidecar werden einzeln bewertet und in
  Tools erläutert. Deaktivierte Einbettung bzw. Sidecar-Erstellung erzeugt dafür
  keinen Pflichtbefund. Ein UI-Covercache ersetzt kein Album-Bildfeld.
- Metadata zeigt persistierte Lücken, Abweichungen und zugeordnete Tools-Befunde;
  Check zeigt zusätzliche Audio-/Identitätsbefunde. Der Metadata-Klick verwendet
  denselben Reparaturworker wie Tools. Ungeklärte Editionen verlangen Re-identify.
- Befunde werden nur bei positiver Nachprüfung ihrer konkreten Anforderungen
  geschlossen; Leseausfälle und fehlende Referenzen halten sie offen. Dateiimport
  und Metadatenzustand erscheinen getrennt in Downloads, auch im Verlauf.
- Änderungen werden nach Commit pro Objekt oder Scanbatch signalisiert. Library
  und Tools laden die gespeicherten Ergebnisse während langer Jobs gedrosselt nach.
  Datei-/Track-/Album-Zuordnung, Library-Owner, Prüfquelle und Zeitpunkt bleiben erhalten.
- Wishlist-Adds materialisieren und monitoren den konkreten Library-Track in
  derselben Transaktion wie den Queue-Eintrag, auch bei direktem DB-Aufruf oder
  erneutem Hinzufügen. „Clear all“ demonitoriert die erfassten Tracks atomar;
  Artists ohne aktive Dateien und ohne Monitoring-Absicht verschwinden aus der
  Library-Liste. Der Katalog bleibt für Identitäten und Historie erhalten.
  Manueller Playlist-Sync kann geleerte Tracks erneut anfordern; automatische
  Läufe respektieren weiterhin die Ignore-Liste. „Monitoring List Reconcile“
  repariert bestehende externe Wishlist-Einträge vor der Ableitung der Queue.

Neue Offline-Akzeptanztests stehen in `tests/library2/test_metadata_consistency.py`
und `tests/wishlist/test_library_monitoring_consistency.py`. Der abschließende
Wishlist-/Monitoring-Lauf bestand mit 93 Tests, einschließlich 140 Tracks über
Clear all und erneuten manuellen Playlist-Sync sowie parallelem Add während Clear.
Gezielte Backend-Regressionen und 153 UI-Tests bestanden; Python-Lint, TypeScript-
Typprüfung und beide Web-Builds bestanden ebenfalls. Die vollständige Web-Lintprüfung
meldete keine Fehler und 521 Warnungen. Keine Live-Downloads oder Produktionsprüfung;
Standalone-Tests, die den vollständigen `web_server` importieren, waren wegen fehlender
optionaler Laufzeitabhängigkeiten in der minimalen Testumgebung nicht ausführbar.

## Historischer Nachweis vor der Umsetzung

89 vorhandene, gezielt ausgewählte Tests bestanden:

```powershell
.venv/Scripts/python.exe -m pytest -o addopts='' `
  tests/test_missing_cover_art.py tests/test_track_number_repair.py `
  tests/imports/test_track_number_resolver.py tests/metadata/test_track_number_format.py `
  tests/library2/test_scan_scope.py -q -x --tb=short
```

Sieben zusätzliche Offline-Beobachtungen bestanden:

```powershell
.venv/Scripts/python.exe tools/repro_library_tools_consistency.py
```

Das Repro-Skript dokumentiert bewusst das **jetzige Verhalten**, keine
gewünschten Regressionserwartungen. Nach einem Fix dürfen diese Beobachtungen
fehlschlagen und sollten in Soll-Verhaltenstests überführt werden. Es isoliert
Konfiguration/Datenbanken in temporären Verzeichnissen, verwendet synthetische
Metadaten und ersetzt externe Provider-Abfragen. Keine Live-Downloads,
Produktionstests, vollständige CI-Suite oder Audioqualitätsbenchmarks wurden
ausgeführt. Testumgebung: Windows, Python 3.12.10, Mutagen 1.47.0,
Requests 2.33.1; übrige kleine Test-Abhängigkeiten lokal in ignorierter `.venv`.
An Anwendungscode wurde für diese Untersuchung nichts geändert.

## Begrenzte Produktionsdiagnose

Für den ersten Abgleich reicht ein auf wenige betroffene Tracks begrenzter,
vor dem Teilen geprüfter Export. Benötigt werden:

- SoulSync-Commit oder Container-Image-Digest und Zeitpunkt des Downloads;
- Downloadquelle, Medienserver, Track-/Album-ID und konkretes Finding inklusive
  `details_json`, Status und Zeitstempel;
- erlaubte Felder aus `lib2_track_files`, `lib2_tracks`, `lib2_albums` und den
  zugehörigen Release-Editionen: Nummern, Totals, Bildfeld, Tag-/Gap-Snapshots
  und Dateizuordnung; persönliche Pfade bei Bedarf anonymisieren;
- tatsächliche Datei-Tags sowie Ergebnis "eingebettetes Bild vorhanden?" und
  Cover-Datei vorhanden; die Musikdatei selbst ist dafür nicht nötig;
- nur die relevanten Metadateneinstellungen, z. B. Einbettung,
  Sidecar-Download, Quellenreihenfolge und Tagging aktiviert;
- passende Logausschnitte rund um genau diesen Import, vorab auf Tokens,
  Cookies, Auth-Header, private URLs und andere persönliche Daten prüfen.

Kein kompletter Config-/Datenbankexport: Die Datenbank kann Zugangsdaten,
Sessions, Integrationen und persönliche Daten enthalten. Bei SQLite im
laufenden WAL-Betrieb keinen unkoordinierten Einzelfile-Copy als konsistente
Sicherung voraussetzen; einen passenden Backup-/Exportweg verwenden.

Soll direkter Diagnosezugriff eingerichtet werden, ist ein separater,
unprivilegierter Diagnosecontainer mit ausschließlich ausgewählten **Kopien**
als Read-only-Mounts eine geeignete Begrenzung. Kein Zugriff auf Docker-Socket,
Unraid-Webinterface, Host-SSH, vollständige Shares oder produktive Config;
keine produktiven Zugangsdaten und keine allgemeine Hostshell. Einen konkreten
Zugangsweg erst anhand der vorhandenen Netzwerk-/Containerkonfiguration planen.
Read-only-Mounts schützen vor Dateischreibzugriff, verhindern aber nicht das
Lesen vertraulicher Inhalte — deshalb sind Auswahl und Bereinigung erforderlich.
[Docker: Read-only Bind Mounts](https://docs.docker.com/engine/storage/bind-mounts/#use-a-read-only-bind-mount)
und [Docker: Daemon-Sicherheit](https://docs.docker.com/engine/security/#docker-daemon-attack-surface).

Ein normaler SoulSync-API-Key ist **keine** geeignete Lesebeschränkung: Der
aktuelle Code setzt für gültige Keys `g.is_admin=True` und `g.can_download=True`
(`api/auth.py:105` sowie `:163`). Ein reiner Zugriff auf die SoulSync-Weboberfläche
ist ebenfalls noch kein garantierter Read-only-Diagnosezugang. Es wurde hier
kein Produktionszugriff eingerichtet oder genutzt.
