# Retag & Reorganize auf `dev` — Bestandsaufnahme

Branch: `fix/reorganize-retag-split`, aufgesetzt auf `upstream/dev` (`8442b0ff8`).
Vorgeschichte: `RETAG_JOB_AND_REORGANIZE_SPLIT.md` (Plan, auf `library-overhaul` umgesetzt),
`BUG_REPORT_CORRUPT_AUDIO_AND_DOWNLOAD_ORGANIZATION.md`.

Alle Befunde unten sind am Code auf diesem Branch nachgeprüft; die vier
markierten „belegt" sind mit laufendem Code reproduziert, nicht nur gelesen.

---

## 0. Was sich seit dem Plan geändert hat

PR **#1182 (`fix/path-organization-unification`) ist inzwischen in `upstream/dev`
gemerged** (`git merge-base --is-ancestor f92ce7816 upstream/dev` → ja). Punkt 6
des alten Plans („neuer Branch auf #1182, nicht auf dev, sonst garantierter
Konflikt") ist damit erledigt: `dev` **ist** jetzt die richtige Basis. Lokales
`dev` war 432 Commits zurück und wurde auf `upstream/dev` vorgespult.

Seither hat `dev` an den relevanten Dateien fast nichts angefasst
(`core/library_reorganize.py` +11/−6, `library_retag.py` +1).

---

## 1. Die Landkarte: „Retag" heißt auf dev drei verschiedene Dinge

| Ort | Was es ist | Zustand |
| --- | --- | --- |
| `core/repair_jobs/library_retag.py` (574 Z.) + `core/library/retag_planner.py` (218 Z.) | Der **Job** — Provider → Datei-Tags, Findings pro Album | lebendig, `default_enabled = False` |
| `core/tag_writer.py` (842 Z.) — `build_tag_diff` / `write_tags_to_file` | Der **kanonische Schreiber** samt Diff, den die Library-UI benutzt | lebendig, geteilt |
| `api/retag.py` (77 Z.) + Tabellen `retag_groups` / `retag_tracks` | Eine **zweite, ältere „Retag-Queue"** | **tot** |

Zum dritten: `add_retag_group` / `add_retag_track` in `database/music_database.py`
haben **keinen einzigen Aufrufer** außerhalb der Datei selbst. Fünf
API-Endpunkte, zwei Tabellen, drei Indizes bedienen eine Warteschlange, die
niemand mehr füllt. Das ist kein Bug, aber es ist der Grund, warum „der
Retag-Job" im Repo schwer zu finden ist.

---

## 2. Der Kernbefund: zwei Diff-Engines, ein Schreiber

Der Job entscheidet **nicht** mit derselben Logik, mit der er hinterher
schreibt.

```
Job:      _read_tags (soulsync_client)  →  plan_track (retag_planner)  →  db_data
Schreiber:                                 write_tags_to_file (tag_writer)  →  Datei
UI-Diff:  read_file_tags (tag_writer)   →  build_tag_diff (tag_writer)
```

`build_tag_diff` und `write_tags_to_file` teilen sich ihre Schutzregeln —
ausdrücklich, mit Kommentar („applies the SAME check … so this preview always
matches the actual write outcome"). `plan_track` kennt **keine** davon:

* den **#800-Platzhalter-Riegel** (`guard_placeholder_overwrite`) — „Various
  Artists" / „[Unknown Album]" darf einen echten Wert nicht überschreiben
* den **Genre-Subset-Riegel** (`genre_write_value_is_subset_of_existing`) — eine
  generische Provider-Gattung darf eine reichere Datei-Liste nicht eindampfen
* die **#824-Datumsnormalisierung** (`_normalize_date_str`) — `2011-05-03`
  vs. `2011-05-03T00:00:00Z`

Dazu lesen beide Seiten die Datei mit **verschiedenen Lesern**:
`_read_tags` nimmt mutagen `easy=True` und davon jeweils nur den **ersten** Wert
(`tags.get('genre', [''])[0]`), `read_file_tags` liest formatspezifisch
(`TCON`, Vorbis, MP4-Atom).

### Folge 1 — Findings, die nie verschwinden (belegt)

`_create_finding` aktualisiert eine bestehende `pending`-Zeile **in place**.
Ein Finding, dessen versprochene Änderung der Schreiber ablehnt, wird also bei
jedem Scan neu bestätigt und bleibt für immer stehen. Reproduziert:

```
Datei-Genre  'Rock, Pop, Indie'   Provider ['Rock','Pop']
  → plan_track:  {'genre': {'old': 'Rock, Pop, Indie', 'new': 'Rock, Pop'}}   (Finding)
  → Schreiber:   genre_write_value_is_subset_of_existing(...) == True         (schreibt nicht)

Datei-Albumartist 'Real Band'     Provider-Album 'Various Artists'
  → plan_track:  {'artist': {'old': 'Real Band', 'new': 'Various Artists'}}   (Finding)
  → Schreiber:   guard_placeholder_overwrite(...) is None                     (schreibt nicht)
```

Der Nutzer klickt „Apply Tags", bekommt Erfolg gemeldet (`written` zählt die
Datei, weil andere Felder geschrieben wurden), und dasselbe Finding steht beim
nächsten Lauf wieder da. Im zweiten Fall ist die versprochene Änderung
obendrein die, die er gerade **nicht** will.

### Folge 2 — was der Job strukturell nicht sehen kann (belegt)

`_current_value('artist')` vergleicht gegen `album_artist`, geschrieben wird
aber `artist_name` (und `track_artist` nur, wenn er vom Albumartist abweicht).
Eine Datei mit korrektem `albumartist` und falschem `artist` erzeugt deshalb
**keinen** Artist-Eintrag im Diff:

```
Datei: albumartist='A', artist='WRONG'  →  plan_track meldet nur das Genre.
```

Genau der Fall, den `build_tag_diff` mit eigenen Zeilen „Artist" **und**
„Album Artist" abbildet.

---

## 3. Die Voreinstellung erzeugt ein Finding pro Album — dauerhaft

`default_settings` hat `cover_art: 'replace'`. `_cover_action` gibt bei
`'replace'` immer `'replace'` zurück, sobald **irgendeine** Cover-URL existiert —
ohne zu prüfen, ob schon Artwork da ist. Und die Abbruchbedingung lautet:

```python
if (not tag_change_tracks and not cover_action and not lyrics_action) or not track_plans:
    result.skipped += 1; return
```

`cover_action` ist wahr → nie übersprungen. **Jedes** Album mit Quell-ID und
Bild bekommt ein Finding, auch wenn die Tags perfekt sind, und es wird bei jedem
Scan erneuert. Die Suite gibt das implizit zu: der einzige „nichts zu tun"-Test
(`test_scan_skips_album_already_correct:225`) setzt eigens `cover_art: 'skip'`.

Auf einer 2000-Alben-Bibliothek heißt das: 2000 Findings, konvergiert nie,
und jedes Apply lädt das Cover neu herunter und schreibt es in alle Tracks.

Nebenwirkung: `cover.jpg` landet in `last_dir` — dem Ordner des **zuletzt**
erfolgreich geschriebenen Tracks. Bei einem Mehrfach-Disc-Album mit
Disc-Unterordnern also in genau einem Disc-Ordner.

---

## 4. Der Job schreibt Dateien, aber nie den Katalog

`apply_track_plans` ruft `write_tags_to_file` und (bei `depth: full`)
`embed_source_ids`. Die Datenbank fasst es **nicht** an. Nach einem Retag zeigt
die Library-Seite weiter den alten Titel, während die Datei den neuen trägt —
bis irgendwann ein Library-Scan darüber läuft. Die Richtung „Provider →
Katalog" gibt es auf dev überhaupt nicht; auf `library-overhaul` ist sie
inzwischen als `core/library2/catalogue_refresh.py` gebaut (und dort noch nicht
verdrahtet).

---

## 5. Wer darf überhaupt — drei Quell-ID-Listen, drei Antworten

| Definition | Quellen |
| --- | --- |
| `library_reorganize._ALBUM_ID_COLUMNS` | spotify, itunes, deezer, **discogs**, **hydrabase**, musicbrainz |
| `track_number_repair._SOURCE_ALBUM_ID_COLUMNS` | spotify, itunes, deezer, discogs, hydrabase — *kein* musicbrainz |
| `library_retag._ALBUM_SOURCE_COLUMNS` | spotify, itunes, deezer, musicbrainz |

Ein per Discogs oder Hydrabase gematchtes Album kann reorganisiert, aber nicht
retagged werden — ohne Meldung, es fällt einfach durch `continue`.

Dazu: `setting_options['source']` bietet `deezer` an, aber `_add_source_ids`
kennt für Deezer **keinen** Track-Schlüssel (nur album-seitig `deezer_id` in
`_FULL_META_ID_KEYS`). Ein Deezer-Retag stempelt also nie eine Track-ID ein.

---

## 6. Laufzeit und Fehlerbild des Scans

* Die SQL holt **alle** Alben mit Titel; der Quell-ID-Filter passiert erst in
  Python. `estimate_scope` zählt ebenfalls alle → Fortschrittsbalken und ETA
  beziehen sich auf eine Menge, die der Job zu großen Teilen sofort verwirft.
* Pro Album **zwei Live-Provider-Calls** (`get_album_for_source` +
  `get_album_tracks_for_source`), dazu pro Track ein `_read_tags` von Platte.
  `check_stop()` greift nur zwischen Alben.
* `depth: 'full'` verschiebt die eigentliche Arbeit ins **Apply**:
  `embed_source_ids` macht dort pro Track Multi-Source-Abfragen, von denen im
  Finding nichts stand. Und es speichert die Datei ein **zweites** Mal
  (`write_tags_to_file` … dann `audio.save()`).
* Das Finding trägt den kompletten Schreib-Payload (`tracks[].db_data`,
  `full_meta`, `lyrics_meta`, aufgelöste absolute Pfade) als JSON in
  `repair_findings.details_json`.
* `_fix_library_retag` löst den bereits im Scan aufgelösten Pfad **noch einmal**
  über `_resolve_file_path` auf.

---

## 7. Reorganize auf dev — der Grund, warum die Trennung überhaupt ansteht

`core/library_reorganize.py` (2504 Z.) schickt im Vollmodus jede Datei durch
`_post_process_matched_download`, also durch die **Aufnahmeprüfung für
Downloads**:

1. `_stage_track` **kopiert** jede Datei in ein UUID-Unterverzeichnis im Staging
   (≈800 MB I/O für ein 20-Track-FLAC-Album).
2. Post-Processing schreibt **erneut Tags** — dieselbe Aufgabe, die der
   `library_retag`-Job schon hat, nur mit anderem Feldsatz und anderer
   Wahrheitsquelle.
3. `_build_post_process_context` trägt inzwischen **vier** nachgerüstete
   Opt-outs, jeder nach einem eigenen Bug (`core/library_reorganize.py:1182-1204`):
   `is_local_import` (#804), `_skip_quarantine_check: 'acoustid'` (#1182),
   `_no_album_folder_reuse` (#829) — plus die drei `_keep_user_*`-Sonderfälle
   (`:1096`, `:1104`, `:1132`), die alle sagen: *hier hatte der lokale Wert
   recht.*
4. `no_source_id` schließt Alben ohne Quell-ID **komplett** aus (`:1284`).
5. Der Preview macht pro Aufruf einen Live-Provider-Call.

Und `reorganize_album_rename_only` (`:2099`, #875) existiert bereits als der
Modus, der nur verschiebt — er ist nur eine Checkbox im Dialog statt das
Verhalten.

Auf `library-overhaul` ist das schon aufgelöst (`core/library2/reorganize_plan.py`,
Commit `28624da38`); auf dev nicht.

---

## 8. Bewertung — was davon ein Bug ist und was Design

**Echte Bugs (Nutzer sieht Falsches):**

1. Nicht konvergierende Findings durch Genre-Subset und #800-Platzhalter (§2).
2. Falscher `artist` bei richtigem `albumartist` wird nie erkannt (§2).
3. Ein Finding pro Album durch `cover_art: 'replace'` als Voreinstellung (§3).
4. `cover.jpg` nur im Ordner des letzten Tracks (§3).
5. Discogs-/Hydrabase-Alben fallen stumm durch (§5); Deezer ohne Track-ID (§5).

**Struktur, die die Bugs erzeugt:**

6. Zwei Diff-Engines und zwei Tag-Leser für dieselbe Entscheidung (§2).
7. Retag schreibt Dateien, nie den Katalog (§4).
8. Reorganize taggt ein zweites Mal, über die Download-Aufnahmeprüfung (§7).

**Altlast:**

9. `api/retag.py` + `retag_groups`/`retag_tracks` — tot (§1).

---

## 9. Vorschlag für die Reihenfolge (noch nicht umgesetzt)

1. **`plan_track` auf `build_tag_diff` umstellen** statt einer eigenen
   Diff-Logik. Löst 1, 2 und 6 in einem Schritt, ist rein additiv und lässt
   sich gegen die vorhandenen 10 Planner-Tests absichern.
2. **`cover_art`-Default auf `fill_missing`**, und `_cover_action('replace')`
   nur dann ein Finding erzeugen lassen, wenn es auch Tag-Drift gibt oder der
   Nutzer den Job manuell angestoßen hat. Löst 3.
3. **Reorganize = nur Pfad** (Portierung von `28624da38` auf dev-Struktur, d. h.
   `reorganize_album_rename_only` wird der einzige Weg). Löst 8, macht die vier
   Opt-outs aus §7 überflüssig und hebt den `no_source_id`-Riegel auf.
4. Die kleinen: Quell-ID-Liste vereinheitlichen, `cover.jpg` pro Disc-Ordner,
   Deezer-Track-ID.
5. Tote Retag-Queue entfernen — separat, damit der PR sauber bleibt.

Offen und **nicht** vorentschieden: ob 3 in denselben PR gehört wie 1/2. Der
alte Plan sagt „nur die Reorganize-Änderung"; dagegen spricht, dass die
PR-Begründung („dev hat den Retag-Job schon") nur trägt, wenn der Retag-Job
danach auch tatsächlich das tut, was er verspricht.
