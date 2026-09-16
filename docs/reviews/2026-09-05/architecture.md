# Specialist Review: lib2-Architektur

Abgeschlossen am 2026-09-05. Vier bestätigte Befunde: **1 × P1, 3 × P2**. Alle wurden mit Produktionsfunktionen auf synthetischen In-Memory-Daten reproduziert. Keine Code-/Test-/Config-/Git-Änderungen, keine Live-DB/-Dienste, kein graphify, keine Subagents.

## Bezug und Grenzen

- HEAD: `9fade33e54fa3185b75236e85e44531307b7c0df` (`library-overhaul`).
- `upstream/dev`: `8889a81c60ffb458a44bdbcf4bc7137d67140cb5`; Merge-Base `c92b8c87e694ce693251fde88d6c3a7c14d0d41c`.
- Fokus: lib2-Abfragen, Dateioperationen, Metadaten/Discography/Provider-Identität, Media-Server-Brücke, konkrete API-/UI-Aufrufer, Legacy-Kompatibilität und belegte Kopplung/Vereinheitlichung.
- Migration/Importer/Post-Import, Wishlist/Acquisition, umfassende API-/Listening-Stats-Integration und komplette UI-Prüfung gehören zu anderen Spezialreviews. Einzelne Querreferenzen dienen nur dem Erreichbarkeitsnachweis.
- Zeilen beziehen sich auf den aktuellen HEAD. `core/library2` existiert in `upstream/dev` noch nicht: Alle vier Fehler liegen im Branch. Die Disc-Regression wurde zusätzlich mit dem upstream-Reorganize-Verhalten verglichen.

## Bestätigte Befunde

### ARCH-01 — P1: MusicBrainz-Discography bricht bei zwei neuen Release-Groups ab

**Fundstelle:** [core/library2/discography.py:704](/home/cyran/Projects/05_Soulsync_fork/core/library2/discography.py:704), Zeilen **704–710**. Fehlerauslösung: [Zeile 344](/home/cyran/Projects/05_Soulsync_fork/core/library2/discography.py:344).

**Auslöser:** Ein MusicBrainz-Katalog enthält mindestens zwei bislang unbekannte Release-Groups. Das ist bei einer ersten Discography-Erweiterung üblich; weitere Bedingungen wie Namensgleichheit sind nicht erforderlich.

**Ursache/Folge:** `_existing_release_index()` lädt `musicbrainz_release_group_id` für bereits gespeicherte Releases. Der während desselben Laufs für ein neues Release ergänzte Dictionary-Eintrag lässt dieses Feld weg. Beim nächsten MusicBrainz-Release läuft `_match_existing()` über sämtliche Einträge und liest `row["musicbrainz_release_group_id"]`. Der zweite neue Eintrag erreicht daher nicht einmal INSERT: `KeyError`. Der Lauf erreicht seinen Commit nicht; die geschlossene SQLite-Verbindung verwirft die Änderungen. Wiederholen trifft dieselbe Ursache. Bei Alias-Gruppen wird der Fehler pro Mitglied abgefangen, das betroffene Mitglied bleibt trotzdem unvollständig.

**Erreichbarkeit:** `api/library_v2.py:3255` und `core/repair_jobs/lib2_discography_refresh.py:157` rufen `refresh_artist_discography()` auf; dieser läuft über `_refresh_one_artist()` direkt in `_expand_artist_discography()`. Der Repro ruft den öffentlichen Einstieg `expand_artist_discography()` mit dem echten typed ProviderResult und zwei synthetischen MB-Gruppen auf.

**Beleg:** `musicbrainz_two_new_groups {"exception":"KeyError","key":"'musicbrainz_release_group_id'","uncommitted_albums":1,"transaction_open":true}`. Nach Rollback: null eingefügte Alben. Der Repro behauptet ausdrücklich **nicht**, dass die Expansion zwei Resultate geliefert hätte: Sie bricht vorher ab.

**Fixrichtung:** Für neue und geladene Indexzeilen dieselbe vollständige Datenstruktur verwenden und `musicbrainz_release_group_id=release_group_id` aufnehmen. Ein Test muss mehrere neue Gruppen in **einem** Aufruf verarbeiten und danach einen idempotenten zweiten Sync prüfen. Die bestehenden MB-Tests in `tests/library2/test_discography.py:1487–1560` verwenden jeweils nur eine neue Gruppe.

**Einordnung:** Neu im Branch; die lib2-Discography existiert upstream nicht.

### ARCH-02 — P2: Album-Detail zählt erwartete fehlende Tracks doppelt

**Fundstelle:** [core/library2/queries.py:1881](/home/cyran/Projects/05_Soulsync_fork/core/library2/queries.py:1881), Zeilen **1881–1884**.

**Auslöser:** Überwachtes Album (`monitored=1`) mit `expected_track_count` größer als der Anzahl materialisierter `lib2_tracks`. Beispielsweise sind zwei Tracks erwartet, aber noch keine Trackzeilen vorhanden. Der Zustand ist während ausstehender/fehlgeschlagener Tracklist-Auflösung regulär erreichbar.

**Ursache/Folge:** `get_album()` ergänzt zuerst fehlende Platzhalter in `tracks` (1830–1841). `_missing_track_placeholder()` übernimmt das Monitoring des Albums. Die abschließende Summe zählt diese fehlenden überwachten Platzhalter bereits und addiert anschließend dieselben `total-known_count` Slots erneut. Die Antwort kann mehr fehlende Tracks als Gesamttracks enthalten.

**Erreichbarkeit:** `GET /api/library/v2/albums/<id>` → `api/library_v2.py:1870` → `Q.get_album()`. Auch mit `resolve=1` liefert die API sofort den aktuellen Katalogstand und löst im Hintergrund auf, sodass der fehlerhafte Zwischenstand sichtbar bleibt.

**Beleg:** Echter `get_album()`-Aufruf ergibt `{"track_count":2,"tracks_present":0,"tracks_missing":4}`.

**Fixrichtung:** Fehlende reale Trackzeilen vor dem Ergänzen von Platzhaltern zählen oder in der Summe nur Einträge mit realer ID berücksichtigen; die noch nicht materialisierten Slots genau einmal hinzufügen. Die bestehende Absicht, erwartete Slots auch auf unüberwachten Albumzeilen auszuweisen, dabei erhalten. Tests sollten überwachte und unüberwachte Alben, leere und teilweise materialisierte Tracklisten sowie Artist-/Album-Projektion vergleichen. Der bestehende Slot-Test `test_queries.py:739` verwendet ein unüberwachtes importiertes Album; die zusätzlichen Slot-Tests in `test_missing_counts_monitored_only.py` prüfen die Artist-Zusammenfassung.

**Einordnung:** Neu im Branch; lib2-Query-/Platzhalterpfad fehlt upstream.

### ARCH-03 — P2: Reorganize deklariert unvollständig vorhandene Multi-Disc-Alben als Single-Disc

**Fundstelle:** [core/library2/reorganize_plan.py:179](/home/cyran/Projects/05_Soulsync_fork/core/library2/reorganize_plan.py:179), Zeilen **179–182**; vorausgehender Dateifilter: **161**.

**Auslöser:** Im Katalog sind Tracks für Disc 1 und Disc 2 vorhanden, eine lokale Datei gibt es erst auf Disc 1. Das Album kann bereits korrekt in `Disc 1/` organisiert sein.

**Ursache/Folge:** Der Planner entfernt vor der Disc-Berechnung sämtliche Trackzeilen ohne Datei und berechnet `total_discs` aus der verbleibenden Menge. Er übergibt damit `1` an `_build_post_process_context()`, das diese Zahl mit `total_discs_declared=True` als verbindlich kennzeichnet (`core/library_reorganize.py:1189–1194`). Der gemeinsame Pfadgenerator unterdrückt daraufhin die Disc-Erkennung und den Disc-Unterordner (`core/imports/paths.py:798–808`, `906–923`). Reorganize verschiebt Disc 1 aus dem Disc-Ordner; sobald die erste Datei von Disc 2 hinzugefügt wird, berechnet derselbe Lauf wieder den Disc-Ordner.

**Erreichbarkeit:** Die lib2-Reorganize-Vorschau benutzt `reorganize_bridge.catalogue_preview_fn()`. Der Produktionsrunner verwendet denselben Planner über `reorganize_album_rename_only(..., preview_fn=catalogue_preview_fn)` in `core/reorganize_runner.py:269–287`. Die Repair-Job-Vorschau läuft ebenfalls über die lib2-Brücke (`core/repair_jobs/library_reorganize.py:94–125`).

**Beleg mit echtem gemeinsamen Pfadgenerator, `create_dirs=False`:**

```text
Katalog enthält Discs 1+2; nur Disc 1 hat eine Datei:
/synthetic/Old Name/Old Name - Two Discs/01 - Disc One Song.flac

Nach Hinzufügen einer synthetischen Dateizeile für Disc 2, gleicher Track:
/synthetic/Old Name/Old Name - Two Discs/Disc 1/01 - Disc One Song.flac
```

Es wurden keine Audiodateien erstellt oder verschoben. Der Repro bestätigt sowohl den deklarierten Context-Wert `1` als auch seine tatsächliche Wirkung auf den Zielpfad.

**Fixrichtung:** Die Disc-Anzahl aus allen bekannten Katalogpositionen bestimmen, einschließlich effektiver Disc-Overrides, bevor die ausführbaren Dateischritte gefiltert werden. Soweit nur Edition/Tracklist die Disc-Anzahl kennt, diese Quelle konsistent einbeziehen. Der vorhandene Test `test_reorganize_plan.py:148` legt für beide Discs Dateien an und kann diesen Fehler nicht finden.

**Einordnung:** Branch-Regression gegenüber upstream: dessen `core/library_reorganize.py:1010–1013` bestimmt die Disc-Anzahl aus der ganzen Provider-Tracklist; der Single-Disc-Heuristik steht bei bereits vorhandenen Disc-Ordnern `_already_organized_by_disc()` entgegen (1042–1043). Der neue Offline-Ansatz ist sinnvoll, verliert hier aber bereits lokal bekannte Informationen.

### ARCH-04 — P2: Erreichbare Artist-Namenskorrektur fehlt in Retag und Reorganize

**Fundstelle:** [core/library2/retag.py:179](/home/cyran/Projects/05_Soulsync_fork/core/library2/retag.py:179), Zeilen **179–184**. Zweiter betroffener Verbraucher: [core/library2/reorganize_plan.py:159](/home/cyran/Projects/05_Soulsync_fork/core/library2/reorganize_plan.py:159), **159**; dortige `_credited_artists()` ebenfalls **100–107**.

**Auslöser:** Im Artist-Edit-Dialog wird der Künstlername von `Old Name` auf `Corrected Artist` korrigiert; danach werden Tags geschrieben oder Dateien reorganisiert.

**Ursache/Folge:** Die UI-/API-Projektion liest den Artist-Override korrekt. Retag lädt dagegen Albumartist und Trackcredits direkt aus `lib2_artists.name`; `_apply_overrides()` kennt nur Track-/Release-Group-Overrides. Reorganize projiziert ebenfalls nur Album und Track. Dadurch schlägt Retag weiterhin alte ARTIST/ALBUMARTIST-Werte vor und schreibt sie gegebenenfalls zurück; Reorganize erzeugt weiterhin Ordner mit dem alten Namen. Der Handwert wird nicht als manuell geschützt erkannt.

**Erreichbarkeit vollständig verfolgt:** `EditArtistModal` in `webui/src/routes/library/-ui/library-v2-page.tsx:3095–3137` stellt ausdrücklich ein Namensfeld bereit und speichert über `updateLibraryV2MetadataOverrides('artist', ...)`. `PATCH /api/library/v2/metadata-overrides/artist/<id>` persistiert den Override (`api/library_v2.py:3884–3945`). Tag-Preview ruft `retag.track_contexts()` auf (4918); `POST /api/library/v2/tags/write` ruft `retag.write_tags()` auf (5014–5017). Reorganize-Aufrufkette siehe ARCH-03.

**Beleg:** Nach echtem `set_field_override(..., entity_type='artist', field_name='name')` liefert `get_album()` den Namen `Corrected Artist`; `retag.track_contexts()` liefert gleichzeitig `artist_name='Old Name'` und `track_artist='Old Name'`. Reorganize meldet `artist='Old Name'`, auch sein realer Zielpfad enthält diesen Namen.

**Fixrichtung:** Effektive Artist-Projektion für Albumartist und alle Trackcredits gemeinsam laden und sowohl für Tags als auch für Pfadkontexte verwenden. Ein Integrationstest sollte Artist-Edit → API-Ansicht → Retag-Context → Reorganize-Context prüfen. Album-/Track-Override-Tests allein reichen nicht.

**Einordnung:** Neu im Branch; die Feld-Override-Schicht existiert upstream nicht. Keine Behauptung, upstream habe diese neue Funktion bereits unterstützt.

## Architektur-Stärken

- **Katalog und Dateibesitz sind explizit getrennt.** `lib2_track_files` besitzt Lebenszyklus, Rollen und mehrere Dateien pro Track. `track_files.py` bündelt Qualitätsreihenfolge und Primary-Auswahl; Trigger erfassen auch Schreibpfade außerhalb eines einzelnen Services. Das ist belastbarer als ein einzelner nullable Trackpfad.
- **Media-Server-Zuordnungen sind separat und providerqualifiziert.** `media_mappings.py:23–35` hat eigene Eindeutigkeiten pro Entity/Server und Server-ID. `media_server_sync.py` arbeitet bei normalen Scans mit `allow_create=False`; Scans sind Beobachter vorhandenen Dateibesitzes. Die alten `server_source/server_id`-Spalten sind ausdrücklich Kompatibilitätsprojektionen. Keine zusätzliche konkrete Regression in dieser Brücke bestätigt.
- **Manuelle Metadatenwerte bleiben vom Provider-Baselinewert getrennt.** `metadata_overrides.py` validiert Felder und Admin-Profil; die Projektion ist stapelbar. ARCH-04 betrifft fehlende Anwendung in zwei Verbrauchern, nicht das Grundmodell.
- **Provider-Snapshots haben einen nachvollziehbaren Vertrag.** Typed Resultate tragen Parser-Version, Vollständigkeit und Cursor; `discography.py:716–739` unterdrückt das Pruning bei partiellen Antworten. Trennung von MusicBrainz-Release und Release-Group ist konzeptionell vorhanden; ARCH-01 zeigt eine unvollständige Übernahme in den Arbeitsindex.
- **Dateilöschen besitzt Vorschau und Journal.** `file_delete.py` prüft konfigurierte Roots, unterscheidet fehlende Dateien von unsicher aufgelösten Pfaden, validiert vor Unlink erneut und schreibt den Zustand `deleting` vorher dauerhaft. Crash-Recovery ist gesondert angelegt. Dieser Review hat den tatsächlichen Unlink bewusst nicht ausgeführt.
- **Performance und Cutover werden strukturell berücksichtigt.** `queries.py` stapelt Metadaten-/Dateiprojektionen und grenzt große Aggregationen auf Seiten ein; `artist_rollup.py` hält Sortierwerte separat. Der Legacy-SQL-Ratchet hat Baseline `reads:0,writes:0` und zählt Upgrade-Zugriffe gesondert. Er ist ein statischer Schutz mit dokumentierten Grenzen, kein Beweis über jeden dynamisch aufgebauten SQL-String.

## Belegte Dead-Code-/Vereinheitlichungskandidaten

| Kandidat | Nachweis und Einordnung | Richtung |
|---|---|---|
| `core/library2/catalogue_refresh.py` | Repository-weite Suche nach Modul sowie `refresh_preview`/`apply_refresh` findet externe Python-Aufrufer nur in `tests/library2/test_catalogue_refresh.py`. Keine API-/Worker-/UI-Verkabelung gefunden. Das ist derzeit **nur durch Tests aufgerufener, nicht integrierter Code**. Ob er absichtlich experimentell zurückbehalten wurde, lässt sich aus Erreichbarkeit allein nicht entscheiden. **Kein zusätzlicher Produktionsbug daraus abgeleitet.** | Als experimentell kennzeichnen oder mit bewusstem Vertrag integrieren; andernfalls samt ausschließlich dafür bestehenden Tests entfernen. Nicht als bereits nutzbaren Provider→Katalog-Schritt dokumentieren. |
| Alter Reorganize-Staging-Orchestrator und Provider-Planung | Der aktuelle Runner importiert `reorganize_album` noch (`core/reorganize_runner.py:68`), ruft aber ausschließlich den Renamer mit lib2-Preview auf (269–287). Alte `plan_album_reorganize`/`preview_album_reorganize` leben innerhalb `core/library_reorganize.py` weiter; die alte Preview bleibt Default-Fallback des Renamers (2199). Tests rufen diese Pfade direkt. | Erreichbare Shared-Helfer/Dateimover aus dem großen Modul extrahieren; danach unbenutzten Staging-Einstieg und ungenutzte Importe separat bewerten. **Nicht das ganze Modul löschen:** der lib2-Planner benutzt Context-, Pfad- und Dateihilfen daraus. |
| Obsolete Reorganize-Quellenmodi | `reorganize_bridge.py:119–120` erklärt `source`/`mode` für inert. `core/repair_jobs/library_reorganize.py:127–132` enthält dennoch einen `no_source_id`→`mode='tags'`-Fallback, den der neue Planner nicht erzeugt. Quellenpicker-Helfer existieren weiter. | Kompatibilitätsannahme an der API-Grenze behalten, interne tote Verzweigung und irreführende Quellenabhängigkeit abbauen. |
| Mehrfache Artist-/Override-Projektion | `queries._track_artists[_many]` projiziert Handwerte; `retag._credited_artists` und `reorganize_plan._credited_artists` lesen rohe Namen. Das ist der direkte Ursprung von ARCH-04. | Eine gemeinsame, nach Artist-ID stapelbare Projektion für UI, Tags und Pfade. Keine weitere ad-hoc-Kopie. |
| Dictionary-Index neben typed ProviderResult | Providerantworten sind typed, im Discography-Arbeitsindex existieren dagegen zwei handgeschriebene Zeilenformen: SELECT-Projektion und Insert-Dictionary. ARCH-01 ist die konkrete Folge dieser Drift. | Eine gemeinsame Row-/Index-Konstruktion mit vollständigem Feldvertrag. |
| Runtime-Helfer aus dem Upgrade-Importer | `media_server_sync._name_key()` importiert `importer.normalize_name`; auch Discography importiert gemeinsame Normalisierung aus diesem großen Modul. Die Abhängigkeit ist real, auch wenn sie keinen Legacy-SQL-Aufruf ausführt. | Reine Identitäts-/Namensnormalisierung in ein kleines gemeinsam genutztes Modul verschieben. Upgrade-SQL-Ratchet dabei nicht mit Modul-Erreichbarkeit verwechseln. |

## Verifikation und Coverage-Lücken

Repro: [architecture_repro.py](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/architecture_repro.py). Vom Repository-Root ausführbar:

```bash
.venv/bin/python -B docs/reviews/2026-09-05/architecture_repro.py
```

Letzter Lauf: **Exit 0, alle vier Fehlerbehauptungen reproduziert**, einschließlich des realen Pfadgenerators im Dry-run. Der Python-Audit-Hook blockiert Verbindungen zu jeder nicht-in-memory SQLite-Datenbank, Netzwerkzugriffe und schreibendes Dateiöffnen. Settings/Providergrenzen sind ersetzt; alle Katalog-/Query-/Plannerfunktionen sind Produktionscode. Beim MB-Repro ist nur `close()` des In-Memory-Handles unterdrückt, damit der nicht committete Zustand inspizierbar bleibt; Rollback und null persistierte Alben werden ausdrücklich geprüft. Das Skript verifiziert bestehende Fehler, es meldet keine bestandenen Fix-Tests.

Der mitgeteilte Gesamtstand des übergeordneten Reviews: **18.228 Python-Tests bestanden, 3 stale/fixture failures (2 upstream reproduziert); 7.795 Vitest-Tests, Build und Ruff bestanden.** Diese Läufe wurden hier nicht wiederholt; die drei Fehler gehören nicht zu diesem Spezialbericht.

Nicht abgedeckt: reale Musikdatei-Mutationen, Media-Server-/Provider-Netzwerkprotokolle, Langläufer-/Race-Repros, Lastmessungen großer echter Bibliotheken, Upgrade-Wiederaufnahme, vollständige UI-/API-/Security- oder Branch-Vollständigkeit. Keine Vollständigkeitsbehauptung über 975 geänderte Dateien. Unbestätigte Zwischenhypothesen wurden nicht als Befunde übernommen.
