# Retag als Job, Reorganize nur noch Reorganize

Stand: 2026-08-25 · Branch `library-overhaul` · Status: in Arbeit

Beschlossen im Gespräch, hier festgehalten damit es nicht wieder erarbeitet
werden muss. Vorgeschichte: `BUG_REPORT_CORRUPT_AUDIO_AND_DOWNLOAD_ORGANIZATION.md`.

---

## 1. Das Grundproblem

Reorganize schickt eine Datei, die der Nutzer **schon besitzt**, durch die
Download-Post-Processing-Pipeline — also durch eine *Aufnahmeprüfung* für
Dateien unbekannter Herkunft. Symptome, alle belegt:

* AcoustID quarantänierte eine Library-Datei wegen ihres eigenen Fingerprints
  (Kanji `澤野弘之` vs. Romaji `Sawano Hiroyuki`) → Report §6.1.
* Vier nachgerüstete Opt-outs im Reorganize-Kontext
  (`core/library_reorganize.py:1160-1200`), jedes nach einem eigenen Bug:
  `is_local_import` (#804), `_library_reorganize`, `_no_album_folder_reuse`
  (#829), und mit PR #1182 kommt `_skip_quarantine_check: 'acoustid'` dazu.
* Voller Modus **kopiert** jede Datei erst ins Staging → ~800 MB I/O für ein
  20-Track-FLAC-Album, und ein Fehlschlag lässt ~40 MB/Track in der Quarantäne.
* Provider-Live-Call pro Preview → 4,4 s Laufzeit und `Invalid base62 id`-400er
  (Report §6.3).

Und: die Aufgabe, die der volle Modus zusätzlich erledigt (Tags schreiben),
**gibt es auf beiden Branches schon separat** — auf `dev` als
`library_retag`-Job, auf `library-overhaul` als `core/library2/retag.py`.

## 2. Die Trennung

```
Manual Match   → setzt nur die Identität (Provider-ID)
Retag          → Provider → lib2-Katalog → Datei-Tags   (beides reviewbar)
Reorganize     → lib2-Katalog → Pfad                     (offline, idempotent)
```

Folge, die sichtbar ist: **nach einem Manual Match ändert sich der Pfad nicht
mehr von allein.** Dazwischen muss ein Retag-Lauf. Gewollt.

## 3. Reorganize = nur Pfad

* Zielpfad aus `lib2_tracks`/`lib2_albums` **inkl. Override-Schicht**
  (`effective["title"]` — genau das, was die UI anzeigt), plus Template.
* Kein Provider-Call, kein Staging, kein Re-Tag, keine Aufnahmeprüfung.
* Damit fallen weg: der `no_source_id`-Riegel (Alben ohne Quelle sind heute
  komplett ausgeschlossen), die Preview-Latenz, die Spotify-400er, und die drei
  `_keep_user_*`-Sonderfälle (`:1107`, `:1115`, `:1143`) — die waren jeweils
  Nachrüstungen, weil sich der lokale Wert als der richtige erwies.
* Sidecars (`.lrc`, `cover.jpg`) müssen mitwandern. `_rename_track_in_place`
  nimmt heute nur Geschwister-**Audio** (`_find_sibling_audio_files`).
* Quelle/Modus-Wähler im Dialog entfällt.

## 4. Retag = Job + Engine

**Engine bleibt** in `core/library2/retag.py`. Grund: 28 Job-Dateien
importieren aus `core.library2`, genau eine library2-Datei importiert aus
`repair_jobs`. Die Richtung ist etabliert. Läge die Engine unter
`repair_jobs/`, würde der Library-V2-**Dialog** vom Wartungspaket abhängen
(`core/repair_jobs/__init__.py` ist eine Registry mit Import-Nebenwirkungen).

**Job kommt neu** als `core/repair_jobs/library_retag.py`:
`@register_job`, `supports_file_scope = True`, Dry-run-Default, Findings pro
Track mit old→new. Dort, wo alle 29 Jobs liegen.

**Fehlende Hälfte für dev-Parität:** unsere Engine geht nur
lib2-DB → Datei-Tags. dev's Job konnte Provider → Datei
(`get_album_tracks_for_source` + `match_source_tracks` + `plan_track`).
Es fehlt **Provider → lib2-Katalog** (Titel, Tracknummern). Nichts auf
`library-overhaul` macht das: `manual-match` setzt nur die ID,
`enrich_native_entity_for_service` schreibt IDs + `image_url`.

Ebenfalls von dev nachzuziehen: `depth: full` (`embed_source_ids`),
`lyrics: fetch`, `cover_art`-Modi, `mode: fill_missing` vs. `overwrite`.

## 5. Hand sticht Provider — überall

lib2 hat eine Override-Schicht (`lib2_metadata_overrides`), und jeder Leseweg
projiziert sie (`project_metadata` → `effective`). **`retag.py` kannte sie
nicht** und las `t.title` roh aus der Basiszeile. Wer einen Titel in der UI
korrigierte und dann Retag laufen ließ, bekam den alten Titel in die Datei —
die Seite zeigte weiter den korrigierten. Bestehender Bug, kein hypothetischer.

Regel: der Override gewinnt standardmäßig, **aber der Nutzer entscheidet
pro Feld.**

### Diff-Zeile

`build_tag_diff` liefert schon `{field, file_value, db_value, changed,
protected}` — `protected` ist der Präzedenzfall (#800-Placeholder-Guard:
„Feld wird bewusst zurückgehalten"). Erweitert um:

```
{ field: 'Title',
  file_value:     'Vogel Im Kafig',        ← steht jetzt in der Datei
  db_value:       'Vogel im Käfig',        ← wird geschrieben (= dein Override)
  provider_value: 'Vogel im Käfig (OST)',  ← was der Katalog wollte
  manual: true,
  changed: true }
```

Annotiert wird in `tag_preview` (lib2), **nicht** in `core/tag_writer.py` —
das ist mit dev geteilt, und die Sorge gehört nach lib2.

### Preview-UI

Track-Checkbox bleibt für den Normalfall. Eine Zeile mit `manual`-Konflikt
klappt auf und bietet pro Feld „meins behalten / neues nehmen". Keine
Feldmatrix über 500 Tracks.

Write-API: `write_tags(..., overwrite_manual=[(track_id, field), …])` —
explizite Freigabeliste, kein globaler Schalter.

### Findings + Bulk

Der Job schreibt in die Finding-Details, welche Felder `manual` sind.
`fix_action`:

* ohne / `'safe'` → manuelle Felder bleiben, das Ergebnis nennt die
  übersprungenen
* `'overwrite_manual'` → alles

Bulk über den bestehenden Prompt-Mechanismus (`promptOrphan` macht das für
Orphan-Files genauso), mit Zähler:

```
23 Findings, davon 4 mit manuell gesetzten Feldern
[ Nur normale übernehmen (19) ]  [ Alles übernehmen, auch manuelle (23) ]
```

## 6. Branches

* **`library-overhaul`**: alles oben.
* **Neuer Branch auf #1182** (`fix/path-organization-unification`), nicht auf
  `upstream/dev` — sonst garantierter Konflikt in `core/library_reorganize.py`
  (#1182 ändert dort 117+/16−). Inhalt: nur die Reorganize-Änderung.
  Begründung für den PR: *dev hat den `library_retag`-Job bereits, Reorganize
  taggt also ein zweites Mal.*
* dev und library-overhaul strikt auseinanderhalten.

## 7. Nebenbefund: toter `library_retag`-Finding-Typ — ERLEDIGT

Der Typ hat wieder einen Job und einen Handler, damit ist der Widerspruch weg.
Was unten stand, bleibt als Begründung stehen.

Auf `library-overhaul` ist der Job in `RETIRED_JOB_IDS`
(`core/repair_jobs/__init__.py:129`) und es gibt keinen Fix-Handler. Backend ist
sauber — `get_finding_type_catalog` setzt `verb: None`, wenn der Typ nicht in
`_fix_handlers()` steht.

Das **Frontend** liest aber seine statische Tabelle: `-tools.core.ts:250` hat
weiter `library_retag: 'Apply Tags'`, und `findingFixLabel` (der Button pro
Zeile) liest genau die. Klick → `No fix available for finding type:
library_retag`. Erreichbar nur, solange alte Findings existieren
(`_prune_retired_job_findings` räumt sie beim Worker-Start).

Löst sich auf, sobald der neue Job den Typ wieder bedient. Beim Aufräumen
beachten: `tests/test_repair_inbox_groups.py:204` erzwingt, dass
`FINDING_TYPE_META` und `FINDING_TYPE_BLURBS` (`-tools.groups.ts`) exakt
dieselbe Slug-Menge haben, in beide Richtungen.

---

## Fortschritt

- [x] **5** Engine liest `effective` statt Basiszeile: `_OVERRIDE_FIELDS` +
      `_apply_overrides` in `core/library2/retag.py`, `db_data['_manual_fields']`
      trägt den Katalogwert. 4 Tests in `tests/library2/test_retag.py`.
- [x] **5** `tag_preview` annotiert die Diff-Zeilen: `_annotate_manual` +
      `_MANUAL_DIFF_KEYS`, plus `entry['has_manual_conflict']` für den
      Bulk-Zähler. 3 Tests.
- [x] **5** `write_tags(overwrite_manual=…)`: `True` oder `[(track_id, field)]`,
      Rohwert-Erhalt (`track_number` bleibt int), plus Durchreichung im
      Write-Endpunkt (`api/library_v2.py`). 5 Tests.
- [x] **3** Reorganize pfad-only: `core/library2/reorganize_plan.py` (neu, 9 Tests),
      `catalogue_preview_fn` in der Bridge, Runner nimmt immer den Mover,
      `.lrc` wandert über `move_companion_sidecars` mit (der Helfer, den auch
      der Import nutzt). Modal ohne Quelle/Modus/Rename-only, API-Body leer.
      Offen als Nachtrag: ein Ordner-`cover.jpg` bleibt liegen (kein
      Datenverlust — `cleanup_empty_directories` löscht keinen Ordner, der es
      noch enthält).
- [x] **4** `core/repair_jobs/library_retag.py`: `@register_job`,
      `supports_file_scope`, Dry-run (Scan schreibt nie), Findings pro Track
      mit `diff`/`manual_fields`; Fix-Handler `_fix_library_retag` mit
      `fix_action='overwrite_manual'`. Aus `RETIRED_JOB_IDS` raus, in
      `JOB_DATA_BASIS`/`JOB_LIBRARY_V2_EFFECTS`/`JOB_CATEGORIES`/
      `NATIVE_SUBJECT_FINDING_TYPES` rein. 7 + 5 Tests. Damit erledigt sich
      auch der tote Finding-Typ aus Abschnitt 7.
- [x] **4** Provider → Katalog: neues `core/library2/catalogue_refresh.py`
      (`refresh_preview` / `apply_refresh` / `album_source`). Positions-Matching
      Disc+Tracknummer, Titel-Ähnlichkeit als Rückfall (`_TITLE_THRESHOLD`
      0.6), jede Quell-Zeile nur einmal vergeben. Leerer Provider-Wert ist kein
      Vorschlag; nicht gefundener Track kommt als `matched: False` zurück statt
      zu verschwinden. Freigabe **löscht den Override** statt nur die Basiszeile
      zu schreiben — sonst gewinnt er weiter auf jedem Lesepfad.
      `apply_refresh` committet nicht. 10 Tests.
      **Noch von nirgends erreichbar** — siehe „Als Nächstes" unten.
- [x] **5** Preview-UI: Feldauswahl bei Konflikt. Diff-Zeile trägt jetzt auch
      `manual_key` (den db_data-Schlüssel, den `overwrite_manual` nachschlägt —
      weder `field` noch `file_key` ist das). `ManualFieldChoice` zeigt beide
      Werte, `writeLibraryV2Tags(ids, embedCover, overwriteManual)` schickt nur
      die tatsächlich freigegebenen Paare, und nur für Tracks die auch
      geschrieben werden.
- [x] **5** Findings + Bulk mit zwei Aktionen: `manual_conflicts` pro Gruppe
      (`json_extract`, nur `pending`), `RetagPrompt` mit „Keep My Edits (N) /
      Overwrite My Edits Too (N)", pro Zeile nur wenn die Zeile einen Konflikt
      trägt. `safe` wird als `null` gesendet — der Handler tut das ohnehin.
- [ ] **6** Branch auf #1182

---

## Stand 2026-08-25, Sessionende

### Commits auf `library-overhaul` (nicht gepusht)

| Commit | Inhalt |
| --- | --- |
| `d8525bde5` | Die vier §6.4-Punkte aus dem Bug-Report (Vorarbeit, anderes Thema) |
| `c22845482` | Retag liest `effective` statt Basiszeile — der Override-Bug |
| `6ffcb8e04` | `write_tags(overwrite_manual=…)` |
| `28624da38` | Reorganize verschiebt nur noch |
| `9183fdf7f` | `library_retag` als Job zurück, scoped |
| `035f41303` | Bulk mit zwei Aktionen + `manual_conflicts` pro Gruppe |
| `9a0316567` | Feldauswahl im Retag-Dialog |
| `66c287a4a` | Provider → Katalog (`catalogue_refresh.py`) |

Vor dem Push auf **einen** Commit squashen (HARD RULE für diesen Branch,
sonst versteckt GitHub die PR-Konversationen).

### ⚠️ Fremde Arbeit auf demselben Branch

Während dieser Session hat eine **parallele Session** auf `library-overhaul`
mitgearbeitet und committet. Stand bei Sessionende:

* `a27013450 Fix multi-artist library credits` sitzt **über** unseren Commits
  und ist **nicht** unserer (Artist-Namen-Normalisierung,
  `DISCOGRAPHY_PARSER_VERSION` `/1` → `/2`, `provider_adapters.py`,
  `discography.py`, `completeness.py` + Tests).
* Im Working Tree lagen danach erneut fremde Änderungen an
  `api/library_v2.py` und `tests/library2/test_api_routes.py`.

**Konsequenz für den Squash:** nicht blind `HEAD~N` zusammenfassen. Unsere acht
Commits sind die oben aufgelistete Kette `c22845482 … 66c287a4a`; alles davor
und danach kann fremd sein. Vor dem Squash `git log --oneline` prüfen und die
Grenzen an den Hashes festmachen, nicht an einer Anzahl.

Der rote `tests/library2/test_discography.py::test_expand_records_normalized_provider_snapshot`
gehörte zu dieser fremden Arbeit, nicht zu uns — auf dem Baum ohne ihre
Änderungen war er grün.

### Testlage

* Python: `tests/library2/` + `tests/repair_jobs/` + `tests/repair/` = 2090 grün
  (plus der eine fremde rote oben). Volle Suite zuletzt `17087 passed`, dazu die
  zwei bekannten Vorbestehenden (`test_confirmed_search_route`,
  `test_discovery_endpoints`) und gelegentliche Parallel-Flakes, die einzeln
  grün laufen.
* Frontend: `library/` 306, `tools/` 553 grün. `npm run check` auf Baseline
  402 Warnings / 0 Errors.
* Immer `-n 8 --timeout=120`; ein blankes `pytest tests/` hängt sporadisch.

### Als Nächstes (neuer Branch)

1. **Endpunkt-Paar für `catalogue_refresh`.** Das Modul ist fertig und
   getestet, aber von nirgends aufrufbar. Ohne das bleibt
   „Match → Refresh → Reorganize" für den Nutzer ein Versprechen. Vorschlag:
   `POST /api/library/v2/albums/<id>/catalogue-refresh/preview` und
   `POST /api/library/v2/albums/<id>/catalogue-refresh`
   (Body `{overwrite_manual?}`), Muster wie `lib2_album_reorganize_preview`
   (`_guard()` / `_conn()` / `commit` / `rollback` / `close`).
   Danach in den Retag-Dialog als zweiter Reiter oder vorgelagerter Schritt.
2. **Branch auf #1182** (`fix/path-organization-unification`, Kopf `f92ce7816`)
   mit **nur** der Reorganize-Änderung. PR-Begründung: dev hat den
   `library_retag`-Job bereits, Reorganize taggt also ein zweites Mal — und der
   volle Modus schickt eine Datei, die der Nutzer besitzt, durch eine
   Aufnahmeprüfung. Nicht auf `upstream/dev` basieren: #1182 ändert
   `core/library_reorganize.py` um 117+/16−, das kollidiert garantiert.
3. **Ordner-`cover.jpg`** wandert beim Reorganize nicht mit (kein Datenverlust,
   `cleanup_empty_directories` löscht keinen Ordner, der es noch enthält).
4. **Beim dev-Merge**: der AcoustID-Skip-Zweig muss „nicht ausgeführt" von
   „ausgeführt, unklar" trennen — sonst stuft jeder Reorganize `verified` auf
   `unverified` herunter. Details in Abschnitt 5 des Bug-Reports bzw.
   `core/imports/pipeline.py:1315` → `verification_status.py:38` →
   `autolink.py:822` (`COALESCE`).
