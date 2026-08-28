# Übergabe: Corrupt-Audio-Findings und inkonsistente Download-Organisation

Stand: 2026-08-24  
Status: untersucht und dokumentiert, noch nicht behoben

## 1. Corrupt File Detector erzeugt Findings für inzwischen fehlende Dateien

### Beobachtung

Der Corrupt File Detector zeigt unter anderem diese Findings:

```text
Corrupt file: Unknown - 01 - i walk this earth all by myself
/app/Transfer/EKKSTACY/NEGATIVE/01 - i walk this earth all by myself.flac
FLAC__STREAM_DECODER_ERROR_STATUS_LOST_SYNC after processing 6418432 samples

Corrupt file: Unknown - 02 - Shoot to Thrill
/app/Transfer/AC_DC/Back In Black/02 - Shoot to Thrill.flac
FLAC__STREAM_DECODER_ERROR_STATUS_LOST_SYNC after processing 0 samples

Corrupt file: Unknown - 01 - Miss YOU!
/app/Transfer/CORPSE/Miss YOU!/01 - Miss YOU!.flac
FLAC__STREAM_DECODER_ERROR_STATUS_LOST_SYNC after processing 4094958 samples
```

Zum Zeitpunkt der Kontrolle existieren diese Pfade nicht mehr auf der Platte.

### Bisheriges Untersuchungsergebnis

Die Findings sind mit `entity_type=file`, ohne Track-ID und mit `Unknown` als
Künstler angelegt. Damit stammen sie aus dem direkten Dateisystem-Walk des
Transfer-Ordners und nicht aus einer alten Track-Zeile der Musikdatenbank.

Die konkrete Ausgabe von `flac -t` zeigt, dass die Dateien beim Decode-Test
tatsächlich geöffnet wurden. Der Scanner erfindet die Dateinamen daher nicht.
Wahrscheinlich wurden die Dateien während des Scans oder danach verschoben,
ersetzt oder entfernt.

Der Scanner prüft nach dem Decode-Test nicht noch einmal, ob derselbe Pfad und
dieselbe Datei weiterhin existieren. Anschließend wird ein Finding mit dem
ursprünglich erfassten Pfad gespeichert:

- `core/repair_jobs/audio_corruption_detector.py`, insbesondere Zeilen 248–294

Offene Corrupt-Audio-Findings werden nach einem vollständigen späteren Scan
nicht automatisch geschlossen, wenn ihr Pfad inzwischen verschwunden ist.
Dadurch bleibt eine veraltete Momentaufnahme in der Findings-UI stehen.

Zusätzlich ist ein solches Dateisystem-Finding aktuell nicht reparierbar: Der
Scanner erzeugt es ohne `entity_id`, aber `_fix_corrupt_audio()` verlangt eine
Track-ID und antwortet andernfalls mit `No track ID associated with this
finding`:

- `core/repair_worker.py`, insbesondere Zeilen 2936–2950

Die UI-Aussage „approve to delete and re-download“ ist für diese `file`-Findings
somit falsch. Ein Fix-Versuch kann keinen Track erneut auf die Wishlist setzen
und das Finding bleibt voraussichtlich pending.

### Gewünschtes Verhalten

1. Vor dem Anlegen des Findings den Pfad erneut prüfen.
2. Idealerweise vor und nach `flac -t` Dateiidentität, Größe und Mtime
   vergleichen, damit ein paralleler Move/Replace nicht als aktuelles Finding
   gespeichert wird.
3. Bei einem vollständigen, nicht eingeschränkten Scan pending Findings für
   nicht mehr vorhandene Dateien als `obsolete` schließen.
4. Dateisystem-only-Findings entweder nicht als „Re-download“ anbieten oder
   ihnen eine ausdrücklich passende Aktion geben. Ohne Track-Zuordnung darf die
   UI keine automatische Neu-Anforderung versprechen.
5. Regressionstests für „Datei verschwindet während/nach Decode“ und für
   `entity_type=file` ohne Track-ID ergänzen.

Die vorhandenen zwölf Tests in
`tests/repair_jobs/test_audio_corruption_detector.py` laufen aktuell durch,
decken diese beiden Fälle aber nicht ab.

## 2. Frische Downloads werden anders organisiert als ein Reorganize-Lauf

### Konkretes Beispiel

Ein frisch heruntergeladenes Album beziehungsweise seine Tracks erscheinen
unter anderem mit diesen Pfaden:

```text
/Transfer/Sawano Hiroyuki/TV Anime _Attack on Titan Season 2_ (Original Soundtrack)/Disc 1/02 - Apetitan.flac

./Transfer/Sawano Hiroyuki/TV Anime _Attack on Titan Season 2_ (Original Soundtrack)/Disc 1/03 - You See Big Girl _ T_T.flac
```

Beispieldaten aus der UI:

```text
02 - Apetitan.flac
36.2 MB
FLAC · 16bit/44.1kHz
Verified
tags ✓

Track 3
You See Big Girl / T:T
5:59
```

Auffällig sind bereits unterschiedliche Root-Darstellungen (`/Transfer` und
`./Transfer`). Nach manuellem Reorganize wird der Download laut Beobachtung
korrekt verschoben. Der genaue Pfad vor und nach Reorganize muss beim nächsten
Lauf noch nebeneinander protokolliert werden.

### Relevante Einstellungen

```text
Download Folder (input):       ./downloads
Music Library Folder (output): ./Transfer
Import Folder:                 ./Staging
Music Videos Dir:              ./MusicVideos
Playlists Folder:              ./Playlists

Custom file organization: enabled
Album Path Template:  $albumartist/$album/$track - $title
Single Path Template: $albumartist/$albumartist - $title/$title
Multi-Disc Folder Label: Disc (e.g. Disc 1/)
Collaborative Album Artist: First Listed Artist
Artist Tag Separator: , (comma)
```

Die Ablage in `Disc 1/` kann durch die separate Einstellung „Multi-Disc Folder
Label: Disc“ beabsichtigt sein. Sie ist aus Nutzersicht für dieses Album sogar
sinnvoll. Trotzdem ist das Verhalten erklärungsbedürftig, weil das Album-Path-
Template selbst keinen `$disc`-Teil enthält und die frische Download-Pipeline
offenbar nicht dasselbe Ergebnis wie Reorganize liefert.

### Kernfrage

Warum verwenden die Download-/Import-Pipeline und das Reorganize-Werkzeug für
dieselbe Datei nicht dieselbe kanonische Zielpfadberechnung?

Ein frisch abgeschlossener Download sollte bereits genau an dem Ziel landen,
das ein direkt danach ausgeführter Reorganize-Lauf berechnet. Reorganize sollte
in diesem Fall keine Änderung mehr finden; der Vorgang muss idempotent sein.

### Mögliche Ursache, noch nicht bestätigt

Es könnten unterschiedliche Pfad-Builder beziehungsweise unterschiedlich
normalisierte Einstellungen verwendet werden:

- `/app/Transfer/...` in Corrupt-Audio-Findings,
- `/Transfer/...` in einer UI-Darstellung,
- `./Transfer/...` in einer anderen UI-Darstellung und in den Einstellungen.

Zu prüfen ist außerdem, ob die Download-Pipeline die Multi-Disc-Information
oder den Albumtyp zu einem anderen Zeitpunkt beziehungsweise aus einer anderen
Metadatenquelle liest als Reorganize.

## 3. Weitere Reorganize- und Pfad-UI-Probleme

### Reorganize muss zweimal ausgelöst werden

Der Reorganize-Vorgang muss weiterhin zweimal gestartet werden:

1. Der erste Klick endet mit einem Fehler.
2. Der zweite Klick funktioniert und verschiebt/benennt die Datei korrekt.

Die exakte Fehlermeldung beziehungsweise die API-Antwort des ersten Laufs ist
noch zu erfassen. Zu prüfen ist insbesondere, ob der erste Lauf trotz Fehler
bereits Zustand, Metadaten oder Pfade vorbereitet, auf denen der zweite Lauf
dann erfolgreich aufbaut.

### „Rename only“ soll standardmäßig aktiv sein

Die Checkbox `Rename only` soll beim Öffnen des Reorganize-Dialogs immer
standardmäßig aktiviert sein. Der Nutzer kann sie bei Bedarf bewusst
deaktivieren.

### Zielpfad im Reorganize-Preview wird abgeschnitten

Im Preview ist der vollständige Zielpfad nicht lesbar. Erwartet wird mindestens
eine der folgenden Darstellungen:

- umbrechbarer beziehungsweise horizontal scrollbarerer Pfad,
- vollständiger Pfad im Tooltip,
- aufklappbare Detailansicht,
- Copy-Button für den vollständigen Pfad.

### Unnötige Anzeige des Library-Roots

In normalen Library- und Reorganize-Ansichten ist `./Transfer/` für die
Orientierung meist nicht relevant. Primär sollte ein Pfad relativ zum
konfigurierten Music Library Folder angezeigt werden, zum Beispiel:

```text
Sawano Hiroyuki/TV Anime _Attack on Titan Season 2_ (Original Soundtrack)/Disc 1/03 - You See Big Girl _ T_T.flac
```

Der vollständige technische Pfad kann für Diagnosezwecke weiterhin per
Tooltip, Detailansicht oder Copy-Button verfügbar bleiben. Vor der Kürzung muss
der Root kanonisch normalisiert werden, damit `/app/Transfer`, `/Transfer` und
`./Transfer` nicht unterschiedlich behandelt werden.

### Doppelte Einstellung „Minimum free disk space“

In der übermittelten Settings-Ansicht erscheint `Minimum free disk space (GB)`
zweimal mit demselben Wert `5` und derselben Beschreibung. Prüfen, ob das Feld
tatsächlich doppelt gerendert wird oder nur beim Kopieren der UI doppelt in den
Text gelangt ist.

## 4. Prüfliste für die Fortsetzung

1. Einen neuen Multi-Disc-Download beobachten und Eingangs-, Import- und
   endgültigen Pfad inklusive Zeitstempeln protokollieren.
2. Direkt danach Reorganize im Preview öffnen und Quell-/Zielpfad vollständig
   sichern.
3. Die Fehlermeldung und Backend-Antwort des ersten Reorganize-Klicks erfassen.
4. Zielpfadberechnung der Download-/Import-Pipeline mit der des Reorganizers
   vergleichen und auf eine gemeinsame Funktion zusammenführen.
5. Verhalten von `Multi-Disc Folder Label` gegenüber dem Album-Template
   eindeutig definieren und in der UI erklären.
6. Pfadnormalisierung für relative, absolute und Container-Pfade prüfen.
7. Reorganize-Preview lesbar machen und `Rename only` standardmäßig aktivieren.
8. Corrupt-Audio-Race und stale Finding-Lifecycle wie in Abschnitt 1 beheben.


---

# 5. Rechercheergebnis (2026-08-24, Code-Analyse)

Legende: **BESTÄTIGT** = im Code bzw. per Ausführung nachgewiesen ·
**WAHRSCHEINLICH** = starke, aber noch nicht am Live-System belegte Hypothese ·
**OFFEN** = braucht Daten vom laufenden Container.

## 5.0 Die gemeinsame Wurzel: drei Schreibweisen desselben Pfades

**BESTÄTIGT — und sie erklärt Abschnitt 2 *und* die Pfad-UI-Beschwerden aus
Abschnitt 3.**

Die Vermutung aus dem Report („unterschiedliche Pfad-Builder") stimmt nur zur
Hälfte. Download-Pipeline *und* Reorganize rufen **denselben** Builder auf —
`core/imports/paths.py:577 build_final_path_for_track` (Reorganize über
`core/library2/reorganize_bridge.py:97`, Download über die Import-Pipeline).
Der Builder ist also bereits kanonisch.

Auseinander laufen die **Wurzelpfade**, weil `soulseek.transfer_path` als
relativer Wert (`./Transfer` — der ausgelieferte Default und der Wert im
Container) an drei Stellen unterschiedlich normalisiert wird:

| Erzeuger | Code | Ergebnisform |
| --- | --- | --- |
| Album-Downloads + Reorganize | `core/imports/paths.py:591` — `docker_resolve_path(...)` als **roher String** | `./Transfer/...` |
| Single-/Simple-Downloads | `core/imports/paths.py:128` — `Path(docker_resolve_path(...))` (`Path` schluckt das `./`) | `Transfer/...` |
| Repair-Dateisystem-Scan | `core/repair_jobs/filesystem_subjects.py:19` — `_path_key()` = `realpath(normpath(...))` | `/app/Transfer/...` |

`docker_resolve_path` (`core/imports/paths.py:112`) macht Pfade **nicht**
absolut — es bildet ausschließlich Windows-Laufwerksbuchstaben ab. Dasselbe
gilt für `RepairWorker._resolve_path` (`core/repair_worker.py:5273`).

Nachgewiesen per Ausführung mit `transfer_path = "./Transfer"`:

```text
transfer_dir (Album-Builder, :591)   './Transfer'
transfer_dir (Simple-Builder, :128)  'Transfer'
new_full (Album)                     './Transfer/Sawano Hiroyuki/.../Disc 1/02 - Apetitan.flac'
```

Genau die drei Formen aus dem Report. Folgen:

1. **`lib2_track_files.path` enthält gemischte Formen.** Reorganize schreibt
   über `_update_track_path` (`core/reorganize_runner.py:120`) den vom Builder
   gelieferten **relativen** Pfad in den Katalog. Alles, was danach `os.rename`
   oder `os.path.isfile` auf diesen Wert anwendet, hängt am CWD des Prozesses.
2. **Die Preview-Anzeige ist asymmetrisch.** In
   `core/library_reorganize.py:1373` wird `new_path` nur dann relativ
   dargestellt, wenn `new_full.startswith(transfer_dir)` — das trifft beim
   Album-Builder zu. `_trim_to_transfer` (`:1440`) prüft dieselbe Bedingung für
   den **aufgelösten** aktuellen Pfad, der aber über
   `core/library/path_resolver.py:155` absolut gemacht wurde (`/app/Transfer/...`)
   → `startswith('./Transfer')` ist **False** → die Spalte „Current path" fällt
   auf den rohen DB-Wert zurück. Deshalb steht in einer Spalte ein sauberer
   relativer Pfad und in der anderen der volle technische.
3. Im Container ist das nicht kaputt, weil das CWD `/app` ist. Lokal fällt es
   nicht auf, weil `config/config.json` hier **absolute** Pfade enthält
   (`/home/cyran/Projects/05_Soulsync_fork/Transfer`) — deshalb ist der Fehler
   nur in der Docker-Instanz sichtbar.

**Fix-Richtung:** `transfer_path`/`download_path` genau einmal beim Auslesen
kanonisch absolut machen (`os.path.abspath` bzw. `realpath`) — die Logik
existiert bereits in `core/library/path_resolver.py:135-166` und muss nur an
den Pfad-Builder und den Reorganize-Bridge-Getter gezogen werden. Erst danach
ist eine Kürzung „relativ zum Library-Root" in der UI überhaupt korrekt
möglich.

## 5.1 Abschnitt 1 — Corrupt-Audio-Findings

### a) Kein Re-Check nach dem Decode-Test — BESTÄTIGT

`core/repair_jobs/audio_corruption_detector.py:246-294`: zwischen
`check_flac_integrity(resolved)` und `context.create_finding(...)` findet
**keine** erneute Existenz-/Identitätsprüfung statt. Weder Größe noch Mtime
werden vor dem Test erfasst, es gibt also auch nichts zu vergleichen.

Nebenbefund: das Finding speichert `file_path=row['file_path']` (den **rohen**
Pfad), getestet wurde aber `resolved`. Bei Dateisystem-Zeilen sind beide
identisch, bei lib2-Zeilen nicht.

### b) Kein Obsoleszenz-Sweep nach einem vollständigen Scan — BESTÄTIGT

`_create_finding` (`core/repair_worker.py:1374-1530`) kann pending Findings nur
**anlegen oder auffrischen**. `_run_job` (`:1054-1266`) hat keinen
Abschluss-Schritt, der pending Findings schließt, die im aktuellen Lauf nicht
mehr aufgetreten sind. Es gibt genau zwei Prune-Läufe, beide beim
Worker-Start und beide nicht zuständig:

* `_prune_retired_job_findings` (`:764`) — nur stillgelegte Jobs.
* `_prune_stale_legacy_findings` (`:792`) — nur Zeilen mit
  `entity_id NOT LIKE 'lib2:%'` **und** `entity_id IS NOT NULL`. Ein
  Dateisystem-Finding hat `entity_id = NULL` und entgeht dem Filter.

Der `stale=True`-Mechanismus (#1143, z. B. `core/repair_worker.py:3641`)
existiert und würde genau das Richtige tun (`resolve_finding(action='obsolete')`
in `:1928`) — er wird beim Corrupt-Audio-Pfad aber nie erreicht, siehe (c).

### c) Dateisystem-Findings sind grundsätzlich unreparierbar — BESTÄTIGT

`_fix_corrupt_audio` (`core/repair_worker.py:2936-2942`) bricht bei
`not entity_id` mit `'No track ID associated with this finding'` ab — **bevor**
der `stale`-Pfad erreichbar wäre. Der Report ist hier exakt richtig, und es ist
schlimmer als beschrieben:

* Der Fehlschlag ist nicht `stale`, also bleibt das Finding **pending** und
  wird bei jedem „Fix all" erneut versucht.
* `FINDING_TYPE_META` (`:102`) vergibt `verb: 'Re-download'` **pro Typ**, nicht
  pro Zeile — die UI bietet den Button also auch für `entity_type=file` an.
* Die Beschreibung („approve to delete it and re-download") wird in
  `audio_corruption_detector.py:290-294` unbedingt gebaut, unabhängig von
  `entity_type`.
* Identisches Muster in `_fix_short_preview_track` (`:2911`) — der Fix sollte
  beide Handler abdecken.

### d) Warum die Dateien verschwunden sind — WAHRSCHEINLICH

Alle drei Findings liegen unter `/app/Transfer/<Artist>/<Album>/NN - Titel.flac`,
also in der Library-Struktur des Nutzer-Templates (`$albumartist/$album/...`),
und sind trotzdem **nicht** im Katalog (sonst hätte
`filesystem_audio_files` sie ausgeschlossen — die Ausschlussliste greift sowohl
über `_path_key` als auch über den 4-Segment-`_suffix_key`, beide matchen bei
gemischten Wurzeln korrekt). Das heißt: es waren Dateien **im Transfer-Ordner
ohne lib2-Zeile**.

Das passt zu zwei Szenarien, die beide ein LOST_SYNC erzeugen:

1. Der Decode-Test lief, während die Import-Pipeline die Datei noch nach
   Transfer kopierte (`shutil.move` über Gerätegrenzen = Copy+Delete) — eine
   halb geschriebene FLAC liefert exakt
   `LOST_SYNC after processing N samples`, und `N=0` bei AC/DC passt zu „ganz
   am Anfang der Kopie erwischt".
2. Der Integritäts-Check der Import-Pipeline hat die Datei danach in die
   Quarantäne verschoben bzw. verworfen.

In beiden Fällen ist der Pfad hinterher weg — und der Scanner hat einen echten
Decode-Fehler gesehen, erfindet also nichts. Zur endgültigen Entscheidung
zwischen (1) und (2) braucht es die Container-Logs zum Scan-Zeitfenster.

### e) Testabdeckung — BESTÄTIGT

`tests/repair_jobs/test_audio_corruption_detector.py` hat 12 Tests
(5 × Integrity, 7 × Scan). Weder „Datei verschwindet während/nach dem Decode"
noch „`entity_type=file` ohne Track-ID" ist abgedeckt.

## 5.2 Abschnitt 2 — Download vs. Reorganize

Der Builder ist derselbe (siehe 5.0). Divergent ist der **Kontext**, den beide
Seiten hineinreichen. Zwei Ursachen, beide bestätigt.

### a) Die #1080-Single-Disc-Kappung — BESTÄTIGT, und sie erklärt das `Disc 1/`

`core/library_reorganize.py:1054-1068` setzt `total_discs = 1` zurück, wenn

* `library.reorganize_preserve_casing` an ist (Default **True**,
  `:473-482`), **und**
* die Tracknummern auf der Platte sich nicht wiederholen, **und**
* `max(Tracknummern) <= Anzahl der Disc-1-Tracks der Quelle`.

Die Download-Pipeline kennt diese Kappung nicht:
`core/imports/paths.py:694-712` nimmt `album_context['total_discs']` bzw. das
Maximum aus der Provider-Tracklist — ungefiltert. Und
`core/imports/paths.py:797` legt bei `total_discs > 1` (und ohne `$disc`/`$cdnum`
im Template) den `Disc N`-Ordner an.

**Konsequenz:** Bei einem gerade erst heruntergeladenen Mehrfach-Disc-Album
liegt zunächst nur Disc 1 auf der Platte. Die Nummern sind dann eindeutig und
liegen innerhalb von Disc 1 → die Kappung greift **zwangsläufig** → Reorganize
berechnet einen Zielpfad **ohne** `Disc 1/`, die Download-Pipeline hatte einen
**mit**. Genau die beobachtete Nicht-Idempotenz.

Schlimmer: sobald Disc 2 nachläuft, greift die Kappung nicht mehr, und ein
späterer Reorganize schiebt alles wieder **in** `Disc N/`-Ordner. Das Layout des
Albums oszilliert also mit dem Füllstand.

### b) Die #829-Ordner-Wiederverwendung — BESTÄTIGT

`core/imports/paths.py:749-767`: die Download-Pipeline darf einen bereits
existierenden Albumordner wiederverwenden (`resolve_existing_album_folder`).
Reorganize schaltet das per `'_no_album_folder_reuse': True`
(`core/library_reorganize.py:1200`) **absichtlich** ab — mit einer im Code
dokumentierten Begründung („its whole job is to move albums OUT of the folder
they currently sit in").

**Konsequenz:** Sobald die Wiederverwendung greift (Single-Disc-Album, dessen
Ordner schon existiert und vom aktuellen Template abweicht), ist
„Download-Ziel == Reorganize-Ziel" **konstruktionsbedingt unmöglich**. Der
geforderte Idempotenz-Vertrag steht damit in direktem Widerspruch zu #829 —
das ist eine Produktentscheidung, keine reine Bugfix-Frage.

### Antwort auf die Kernfrage

Es gibt bereits eine gemeinsame kanonische Zielpfadberechnung. Idempotent ist
sie nicht, weil (a) Reorganize eine Disc-Heuristik anwendet, die die
Download-Pipeline nicht kennt, und (b) die Download-Pipeline eine
Ordner-Wiederverwendung anwendet, die Reorganize bewusst abschaltet. Zu
entscheiden ist deshalb *zuerst*, welche der beiden Seiten recht hat — erst
danach lohnt Code.

## 5.3 Abschnitt 3 — Reorganize- und UI-Probleme

### a) Reorganize muss zweimal ausgelöst werden — OFFEN, aber ein Nebenbefund ist bestätigt

Die Ursache ist ohne die Fehlermeldung des ersten Laufs nicht entscheidbar.
So lässt sie sich einfangen:

* UI: Der Text steht entweder in der Zeile aus
  `webui/src/routes/library/-ui/reorganize-modal.tsx:249` (Apply-Request
  fehlgeschlagen) oder in `ReorganizeQueueStatusLine` (`:88-92`,
  „Reorganize failed (<status>)") — die beiden bedeuten **völlig
  verschiedene Dinge**, deshalb bitte notieren, welche der beiden es ist.
* Backend: `docker logs` filtern auf `[Queue] Finished` und
  `[Queue] Runner raised` (`core/reorganize_queue.py:384-421`) sowie auf
  `[Reorganize/rename]` (`core/library_reorganize.py:2160-2172`).
* API: die JSON-Antwort von `POST /api/library/v2/albums/<id>/reorganize`
  (`api/library_v2.py:2276`) und danach der Eintrag aus
  `GET /api/library/reorganize/queue`.

**Bestätigter Nebenbefund im selben Pfad:** `reorganize_album_rename_only`
(`core/library_reorganize.py:2146`) prüft vor dem Verschieben `matched`,
`unchanged`, `collision` und `new_path_abs` — aber **nicht** `file_exists`.
Ein Track, dessen Datei nicht aufgelöst werden konnte
(`current_path_abs = ''`, gesetzt in `:1320`), läuft deshalb in
`_rename_track_in_place('', ...)`. Zur Laufzeit verifiziert:

```text
dir exists before: False
result: False "[Errno 2] No such file or directory: '' -> '.../Artist/Album/01 - x.flac'"
dir exists after : True
```

Der Lauf scheitert **und** legt den leeren Zielordner an (`os.makedirs` in
`:2058` läuft vor dem `os.rename`). Das ist genau die Art von Zustand, auf der
ein zweiter Klick anders aufsetzt als der erste — und es ist ein Rückfall in
den Leere-Ordner-Fehler #767, den die Preview mit `create_dirs=False` extra
vermeidet.

### b) `Rename only` soll Default sein — BESTÄTIGT (Einzeiler)

`webui/src/routes/library/-ui/reorganize-modal.tsx:164` —
`useState(false)` → `useState(true)`.

Zu beachten: der Preview-Query-Key (`:176-177`) enthält `renameOnly` **nicht**.
Solange die Preview für beide Modi dasselbe berechnet, ist das korrekt; wenn
der Default kippt, sollte das bewusst so bleiben und nicht versehentlich
divergieren.

### c) Zielpfad im Preview abgeschnitten — BESTÄTIGT

`webui/src/routes/library/-ui/library-v2-page.module.css:3964-3971`:

```css
.filePathCell { max-width: 260px; overflow: hidden; text-overflow: clip;
                white-space: nowrap; }
```

`text-overflow: clip` (nicht `ellipsis`) → es gibt nicht einmal ein „…", das
Abschneiden ist unsichtbar. Ein `title`-Tooltip ist vorhanden
(`reorganize-modal.tsx:276` und `:279`), ein Copy-Button nicht. Dieselbe Klasse
trifft die Library-Tabellenspalte `file_path` (`library-v2-page.tsx:9501-9510`),
die zusätzlich den **rohen** gespeicherten Pfad zeigt.

### d) Unnötige Anzeige des Library-Roots — BESTÄTIGT, hängt an 5.0

Die Preview kürzt bereits — aber nur die Spalte „New path", weil nur dort der
`startswith`-Vergleich aufgeht (Detail in 5.0 Punkt 2). Die Kürzung ist ein
roher String-Präfixvergleich ohne Normalisierung; sie wird erst verlässlich,
wenn der Root nach 5.0 kanonisiert ist. Die Library-Tabellenspalte
`file_path` kürzt gar nicht.

### e) Doppelte Einstellung „Minimum free disk space" — BESTÄTIGT

`webui/index.html:5117-5121` und `webui/index.html:5123-5127` sind ein
**wortwörtliches Duplikat**, inklusive derselben `id="min-free-disk-gb"`.
Da `getElementById` nur das erste Element liefert, ist das zweite Feld tot:
es wird beim Laden nicht befüllt und beim Speichern nicht gelesen. Der Nutzer
kann dort etwas eintippen, das folgenlos verworfen wird.

Eingeschleppt mit `fa6a58ded` („feat(library-v2): squash library overhaul onto
current dev"). Kein Kopierfehler in der Fehlermeldung — der Fix ist, den
zweiten Block zu löschen.

Nicht betroffen: `webui/index.html:3262` — das ist das eigenständige
Video-Pendant (`id="vo-min-free"`).

## 5.4 Was als Nächstes gebraucht wird

1. **Die Fehlermeldung des ersten Reorganize-Klicks** — der einzige echte
   Blocker (Aufnahmeanleitung in 5.3 a).
2. **Container-Logs zum Zeitfenster des Corrupt-Scans** — entscheidet zwischen
   „Race mit laufendem Import" und „Quarantäne danach" (5.1 d).
3. **Eine Produktentscheidung zu 5.2 b**: soll Download-Ordner-Wiederverwendung
   (#829) oder Reorganize-Idempotenz gewinnen? Beides gleichzeitig geht nicht.
4. **Eine Produktentscheidung zu 5.2 a**: soll die #1080-Kappung auch für die
   Download-Pipeline gelten (dann kein `Disc 1/` mehr bei Single-Disc-Layout),
   oder soll Reorganize sie bei unvollständigen Alben aussetzen?

Unabhängig davon und ohne Rückfrage umsetzbar: 5.1 a/b/c (+ Tests), 5.3 b,
5.3 c, 5.3 e und der `file_exists`-Guard aus 5.3 a.

---

# 6. Nachtrag 2026-08-24 abends: Live-Logs, Zwei-Klick-Ursache, Restliste

Abschnitt 5.3 a hatte die Ursache des Zwei-Klick-Problems als **OFFEN** markiert
und mehrere Kandidaten aufgelistet. Die Live-Logs vom 24.08. 19:03–19:04 zeigen:
es war keiner davon.

## 6.1 Zwei-Klick-Reorganize — GELÖST

```text
19:03:33  [Queue] Starting 'TV Anime "Attack on Titan Season 2"...'
19:03:46  Waiting for file to stabilise: 02 - Apetitan.flac (38008230 bytes)
19:03:49  AcoustID verification result: fail - Audio mismatch:
          'APETITAN' by '澤野弘之' — expected artist not found
19:03:49  File quarantined: downloads/ss_quarantine/...02 - Apetitan.flac.quarantined
19:03:49  [Queue] Finished — status=failed, moved=0, skipped=0, failed=2
19:04:16  [Queue] Starting ... (zweiter Klick, 'Rename only' angehakt)
19:04:19  Library v2 file rescan: 1 probed, 1 updated
19:04:20  [Queue] Finished — status=done, moved=2, failed=0
```

Reorganize im **vollen** Modus legt eine Kopie der Library-Datei ins Staging und
schickt sie durch die komplette Download-Post-Processing-Pipeline — inklusive
**AcoustID-Identitätsprüfung**. Der Fingerprint liefert den Künstler in Kanji
(`澤野弘之`), erwartet wird das Romaji `Sawano Hiroyuki` → „expected artist not
found" → **Quarantäne** → `status=failed`.

Der zweite Klick mit `Rename only` umgeht die Pipeline vollständig. Es war also
nie ein halb vorbereiteter Zustand, auf dem der zweite Lauf aufbaut, sondern
schlicht **zwei verschiedene Codepfade**.

`is_local_import: True` (`core/library_reorganize.py:1188`) deckte nur die
Dauer-Prüfung ab (`core/imports/file_integrity.py:150`), nicht die
AcoustID-Prüfung.

**Nebenwirkung:** jeder fehlgeschlagene Versuch lässt ~40 MB pro Track in
`downloads/ss_quarantine/` liegen, und die Quarantäne-Liste füllt sich mit
Dateien, die der Nutzer besitzt. Vor dem nächsten Lauf aufräumen.

**Behoben** (Branch `fix/path-organization-unification`, Commit 6): der
Reorganize-Kontext setzt `'_skip_quarantine_check': 'acoustid'`. Größen- und
Korruptionsprüfung laufen weiter.

## 6.2 Korrektur zu 5.1 d und zur Datenverlust-Zuordnung

Der Verdacht aus 5.1 d („Race mit laufendem Import" vs. „Quarantäne danach")
ist damit zugunsten von **Quarantäne** entschieden — nur nicht durch den
Import, sondern durch den **Reorganize** selbst.

Ebenso: der Datenverlust-Pfad (`_update_track_path` schluckt, `_finalize_track`
löscht danach) ist real und behoben, aber die vorliegenden Logs zeigen ihn
NICHT — der Lauf endete mit `moved=0`, es wurde nichts gelöscht. Das früher
berichtete „Song wieder missing" lässt sich ohne das Log jenes Moments keinem
konkreten Ereignis zuordnen.

Der wahrscheinlichste Kandidat dafür bleibt `reorganize_album_rename_only`
(`core/library_reorganize.py:2173`, auf `library-overhaul` unverändert): ein
fehlgeschlagener Katalog-Update wird geloggt („a scan will reconcile") und der
Track trotzdem als `moved` gezählt. Bei `Rename only` gibt es keine zweite
Kopie — Datei verschoben, `lib2_track_files.path` alt → `missing_suspected` →
`missing_confirmed` → wanted → Re-Download. Und weil `Rename only` der einzige
Modus ist, der vor 6.1 überhaupt durchlief, ist genau das der benutzte Pfad.

## 6.3 Nebenbefund: `Invalid base62 id` bei jedem Reorganize-Preview

Im selben Log, 3–4× pro Preview:

```text
soulsync.spotify_client - ERROR - get_artist_albums:2199 -
Error fetching artist albums via Spotify: http status: 400, code: -1 -
https://api.spotify.com/v1/artists/126/albums?... Invalid base62 id
```

`core/spotify_worker.py` (im `pick_artist_by_catalog`-Aufruf) ruft
`self.client.get_artist_albums(a.id)` für **jeden** Kandidaten auf — und zwar
**bevor** die Prüfung zwei Zeilen darunter greift:

```python
if not self._is_spotify_id(best_obj.id):
    logger.warning(f"Rejecting non-Spotify ID '{best_obj.id}' ... (iTunes fallback leak)")
```

Numerische iTunes-IDs (hier `126`) gehen also an den Spotify-Endpoint, bevor sie
verworfen werden. Folgen: garantierter 400 pro Kandidat, ein ERROR im Log für
etwas, das kein Fehler des Nutzers ist, und ein Teil der gemessenen
Preview-Laufzeit (`Slow request: POST .../reorganize/preview -> 200 in 4410.5ms`).

Der Fix wäre, die Kandidaten **vor** dem Katalog-Abgleich auf gültige
Spotify-IDs zu filtern statt danach. Bewusst NICHT im aktuellen Branch — er
gehört nicht zur Pfad-/Organisations-Vereinheitlichung.

Nicht untersucht, aber verwandt: die AcoustID-Künstlerprüfung kennt kein
Kanji↔Romaji. Für Reorganize ist das nach 6.1 gegenstandslos, für einen
**frischen Download** japanischer Künstler bleibt es bestehen.

## 6.4 Restliste

### Auf `fix/path-organization-unification` erledigt (6 Commits, Suite grün)

| # | Fix |
| --- | --- |
| 1 | Katalog-Update wirft (inkl. rowcount); Rename-only macht den Move bei DB-Fehler rückgängig; unauflösbare Quelle wird übersprungen statt leere Zielordner anzulegen |
| 2 | `config_root_path()` — Library, Downloads, Staging, MusicVideos, Playlists einmal kanonisch absolut; `import.staging_path`-Key korrigiert; symmetrische Pfadkürzung im Preview |
| 3 | `#1080`-Kappung greift nicht mehr bei bereits nach Disc organisierten Alben; deklariertes `total_discs` wird nicht mehr durch einen Live-Provider-Lookup überschrieben |
| 4 | `$disc`/`$discnum` funktionieren in Ordner-Segmenten; Hilfetexte korrigiert |
| 5 | Corrupt-Detector vergleicht Größe+Mtime um den Decode-Test; `retire_vanished_findings()` schließt verschwundene Findings (nur wenn der Ordner erreichbar ist) |
| 6 | Reorganize quarantäniert keine Library-Datei mehr wegen ihres eigenen Fingerprints |

### Offen — nur `library-overhaul` (kein Dev-Pendant)

1. **`entity_type=file`-Findings sind unreparierbar.** `_fix_corrupt_audio` und
   `_fix_short_preview_track` brechen bei fehlender `entity_id` ab, bevor der
   `stale`-Pfad erreichbar wäre → Finding bleibt `pending`. `FINDING_TYPE_META`
   vergibt `verb` pro Typ, die UI bietet also „Re-download" auch dort an.
2. **Doppeltes `Minimum free disk space`-Feld**, `webui/index.html:5117` und
   `:5123`, identische `id="min-free-disk-gb"` → das zweite Feld ist tot.
3. **Pfad-Anzeige**: `.filePathCell` (`library-v2-page.module.css:3964`) nutzt
   `text-overflow: clip` statt `ellipsis` und zeigt den Rohpfad inklusive Root.
   Betrifft Library-Tabelle und Reorganize-Preview. Kein Copy-Button.
4. **`Rename only` Default** (`reorganize-modal.tsx:164`): `useState(false)`.

### Offen — Produktentscheidung

5. **Bestandspfade normalisieren?** Nach Fix 2 werden neue Pfade absolut
   geschrieben; bestehende `./Transfer/...`-Zeilen bleiben bis zu einem Rescan
   relativ. Der Resolver kommt mit beidem klar, aber die Tabelle bleibt gemischt.
   Eine einmalige Normalisierung wäre möglich und ist nicht getroffen.
6. **`#829` Ordner-Wiederverwendung** bleibt wie sie ist — bei einer leeren
   Library findet der Resolver keinen Ordner und Multi-Disc überspringt Reuse,
   also kann im gemeldeten Szenario nichts driften. Bewusst so entschieden.

### Offen — außerhalb des Scopes

7. `spotify_worker` sendet iTunes-IDs an Spotify (6.3).
8. AcoustID-Künstlervergleich ohne Kanji↔Romaji (6.3).
