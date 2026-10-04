# Feature-Audit: dev gegenüber library-overhaul

Stand: 4. Oktober 2026. Zwei Agents, zusätzliche unabhängige Quellprüfung und isolierte Reproduktionen. Ausschließlich Audit; keine Anwendungskorrekturen.

**Ergebnis: Die Sorge vor Funktionsverlust ist berechtigt.** Sieben konkrete Paritätslücken sind bestätigt. Besonders relevant sind die nicht wirksame manuelle Albumedition und die automatische Gleichsetzung verschiedener Aufnahmen. Weitere Funktionen wurden absichtlich reduziert; außerdem enthält der neuere dev Features und Fehlerkorrekturen, die noch nicht integriert sind. Diese Kategorien dürfen beim nächsten Merge nicht miteinander verwechselt werden.

Das ist keine Aussage, dass die gesamte Anwendung kaputt ist: Zahlreiche entfernte Legacy-Routen und Jobs haben funktionierende native Gegenstücke. Die gefundenen Lücken liegen überwiegend in der Bedeutung von Daten und Nutzerentscheidungen, nicht im bloßen Vorhandensein eines Buttons oder einer Methode.

## Vergleichsstände und Prüfgrenzen

| Rolle | Fester Git-Stand | Bedeutung |
| --- | --- | --- |
| Unser geprüfter Branch | `0a14ed6f652f6de144ffe1a22b60cbe16d8155fe` | `library-overhaul` bei Auditbeginn |
| Bereits gemergter dev | `732a83fe4c8e6cd1c07ec68d72087e9f4c998706` | Merge-Base mit upstream/dev; entscheidend für tatsächlichen Verlust bereits übernommener Features |
| Aktueller upstream/dev | `5d0a3dbbfdfb788928d595aa175e9e7beea043e7` | Remote-Head durch `git ls-remote upstream refs/heads/dev` verifiziert |
| Parallel beobachteter Branch-Stand | `31071a909189db4fa0c3e1130d5705f6bce4e4ea` | Drei weitere Upgrade-Fixes F06/F07/F08 aus dem anderen Chat; die hier betroffenen Stellen wurden dadurch nicht geändert |

- Der lokale Branch `dev` war deutlich älter als `upstream/dev`. Ein Vergleich ausschließlich mit lokalem `dev` hätte neue Funktionen übersehen.
- Zwischen gemergter Basis und aktuellem upstream/dev liegen **128 Commits, davon 92 ohne Merge-Commits**. Sie sind kein Beweis für 92 fehlende Features: Commit-Abstammung, Source-Port und tatsächliches Verhalten wurden getrennt betrachtet.
- Agent 1 untersuchte bereits übernommene Library-/Artist-/Tools-Funktionen und deren native lib2-Ersatzpfade. Agent 2 untersuchte neuere Repair-, Metadata-, Download-, Video-, Podcast- und Audiobook-Änderungen. Hauptprüfung: API-/Methodeninventar, Playlist-/Wishlist-Verhalten, Bibliotheksgrenzen und unabhängige Reproduktionen.
- Das statische Routeninventar umfasst Deklarationen in `api/` und `web_server.py`. Blueprints können Präfixe hinzufügen; die Zahlen sind kein Runtime-Verfügbarkeitsnachweis. Gezählt wurden 1.393 Deklarationen in der gemergten Basis, 1.400 in neuerem dev und 1.488 bei uns. 19 alte beziehungsweise 26 aktuelle Method-/URL-Kombinationen fehlen wörtlich. Die meisten alten Fälle haben native Ersatzrouten.
- Reproduktionen verwenden unveränderte Funktionskörper aus festen Git-Blobs, kontrollierte Abhängigkeiten und temporäre SQLite-Datenbanken. Kein Start der laufenden Anwendung, keine produktiven Downloads, Dateireparaturen oder Media-Server-Schreibzugriffe.
- Die bestehenden Tests wurden zusätzlich auf dem parallel beobachteten Arbeitsstand ausgeführt. Alle unten genannten 79 Tests bestanden. Das ist keine vollständige HTTP-, Browser-, Docker-, NAS- oder Media-Server-Abnahme.
- Die Quellstellen/Zeilennummern beziehen sich auf den festen geprüften Branch-Stand. Spätere Paralleländerungen sind kein automatisch mitgeprüfter Audit-Stand.
- Dieser Bericht ergänzt [den Upgrade-Audit](upgrade-lib2-audit-2026-10-04.md). Dessen F01–F08 werden hier nicht als neue Findings erneut gezählt. Spätere Behebungen jenes Berichts werden hier nicht abgenommen.

## A. Bestätigte Lücken gegenüber bereits gemergten Features

P2 bezeichnet hier bestätigte Funktions-/Erhaltungsfehler mit konkretem Szenario. Besonders A01/A02 sollten vor einer Freigabe korrigiert werden: Sie können bewusste Nutzerentscheidungen unwirksam machen beziehungsweise fehlende Musik verbergen. Es wurde kein tatsächliches Löschen einer Musikdatei durch A02 nachgewiesen.

| ID | Priorität | Beobachtung | Einordnung |
| --- | --- | --- | --- |
| A01 | P2 | Manuell korrigierte Albumedition wird angezeigt, von Tracklist/Reparatur jedoch nicht verwendet | Fehler im nativen Ersatz; außerdem Verlust übernommener Canonical-Pins |
| A02 | P2 | Automatischer Single↔Album-Link verwechselt unterschiedliche Aufnahmen und unterdrückt Missing | Fehler im nativen Ersatz und veränderte Review-Semantik |
| A03 | P2 | Unknown-Artist-Reparatur kann eine gemischte Sammlung nicht mehr pro Track auflösen | Entfernter Job ohne gleichwertiges Gegenstück |
| A04 | P2 | Credits für später hinzukommende Gastkünstler gehen verloren | Bewusst vereinfachte Speicherung mit tatsächlichem Feature-Verlust |
| A05 | P2 | Alte Compilations ohne Performer-Credit können nicht aus vorhandenen Tags nachgeheilt werden | Bereits dokumentierte, jetzt funktional bestätigte Bestandslücke |
| A06 | P2 | Öffentliche Künstler-API ignoriert die Bibliotheksgrenze im lib2-Datenreader | Beim Ersatz der alten Datenabfrage verlorene Filtersemantik |
| A07 | P2 | Globale Albumansicht mit Album-Sortierung fehlt | Bereits dokumentierte offene UI-Paritätslücke |

### A01 — Match-Chip und tatsächlich verwendete Albumedition widersprechen sich

**Quellen:** `core/library2/match_status.py:333–436`, `core/library2/editions.py:248–267`, `core/library2/completeness.py:173–235`, `core/repair_jobs/track_number_repair.py:1313–1352`. Bereits gemergtes Feature: `2d2ee34df` (#758), Canonical-Verwendung im Repair-Pfad `f5752e3dc`.

Ein Nutzer korrigiert ein Album von DELUXE auf REGULAR. Der native Setter verändert die Album-Provider-ID, das Attempt-Ledger und die Match-Provenienz. Die konkrete Default-Edition bleibt unverändert. Completeness und Track Number Repair lesen bevorzugt diese Edition. Zudem erzeugt der Edition-Backfill sie aus den normalen Album-IDs und ignoriert vorhandene gesperrte `canonical_source`/`canonical_album_id`-Auswahlen.

**Reproduktion mit den echten Produktionsfunktionen:**

```text
übernommener gesperrter Canonical-Pin: REGULAR
erzeugte Default-Edition:             DELUXE
manueller set_library_v2_match:       REGULAR
anschließend angezeigter Match-Chip:  REGULAR
anschließend verwendete Tracklist:   DELUXE
```

Damit bleibt die Korrektur für die tatsächlich verwendete Tracklist wirkungslos. Falsche Fehltrack-Ergebnisse und falsche Nummerierungspläne sind mögliche Folgen. Die Probe hat keine Audio-Tags verändert.

**Verbesserung:** Eine autoritative, sperrbare Edition-Auswahl. Manueller Album-Match muss die konkrete Edition atomar aktualisieren/auswählen und gebundene Tracklist-Snapshots invalidieren. Gespeicherte Canonical-Pins beim Upgrade ausdrücklich übernehmen. Test von Match → Edition → Tracklist → Repair-Plan, einschließlich gesperrter Bestandsauswahl.

### A02 — Ähnlichkeit wird automatisch als bestätigte Duplikatentscheidung behandelt

**Quellen:** `core/library2/importer.py:2251`, `:2391–2430`; `core/library2/duplicate_relationship.py:119–134`; `core/library2/wanted_views.py:29–40`, `:121–126`. Gemergtes dev-Gegenstück: `a31dd9b81`, `single_album_dedup` erzeugte optionale Review-Findings mit `auto_fix=False`.

Der Importer gruppiert nach Primärkünstler und normalisiertem Titel. Bei Album+Single setzt er sofort `canonical_track_id`. Er prüft weder Dauer noch Recording-Identität und nutzt den nativen Validator nicht. Missing interpretiert den Link anschließend als absichtliche Konsolidierung, sobald der Partner eine Datei besitzt.

**Reproduktion:** Gleicher Künstler, gleicher Titel; Studioaufnahme 180 Sekunden mit `studio-id`, andere Aufnahme 300 Sekunden mit `live-id`.

```text
nativer manueller Validator:  Track durations differ too much
automatischer Importer:       1 Link erstellt
canonical_track_id:           Studioaufnahme
Missing-Konsolidierungsregel: True
```

Eine fehlende andere Aufnahme wird dadurch verborgen. Die Probe bestätigt die echte SQL-Ausschlussregel; sie weist keine physische Dateilöschung nach.

**Verbesserung:** Gemeinsame Recording-/Dauer-Prüfung für Importer und Commands. Unsichere Titelähnlichkeit als Vorschlag speichern. Ein Kandidaten-Link darf Missing nicht unterdrücken; dafür braucht es belastbare Recording-Identität oder explizite Nutzerkonsolidierung. Tests für abweichende Dauer, ISRC, MBID und unterschiedliche Versionen.

### A03 — Unknown Artist braucht Recovery pro Track, Enrichment pro Künstler reicht nicht

**Quellen:** Gemergter `core/repair_jobs/unknown_artist_fixer.py:331–365` (`52a5d9301`, `7d823db13`); bei uns `core/library2/native_enrich.py:167–215`, `:249–306`, `:323–465`; `core/library2/scan.py:202–264`.

Der frühere Fixer identifizierte jeden Track zuerst aus eingebetteten Artist-Tags, danach aus Provider-Track-IDs und schließlich aus der Titelsuche. Daraus entstanden Review und getrennte Artist-/Album-Zuordnungen. Der native Enrichment-Pfad identifiziert eine gemeinsame Artist-Zeile. Er sammelt nur einen Anchor je Provider und schreibt IDs, Bild und Genres. Er korrigiert weder den Placeholder-Namen noch die Zuordnung einzelner Tracks. Tag Scan speichert Beobachtungen, übernimmt die Performer jedoch nicht in die Credits.

**Reproduktion:** Unter „Unknown Artist“ liegen ein Track von A und einer von B. Der alte Tag-Resolver liefert A korrekt. Der native Pfad fragt nur `track-a`, meldet `success=True`, lässt `name='Unknown Artist'` und beide Track-Credits auf derselben Artist-Zeile.

**Verbesserung:** Native Recovery pro Datei/Track mit Tag-/Provider-Evidenz, Preview und atomarer Credit-/Album-Neuzuordnung. Gemischte Placeholder-Sammlungen und benutzerdefinierte Unknown-Namen testen. Smart Split und ein manueller Match sind allein kein Ersatz für diese Recovery.

### A04 — Gastkünstler später hinzufügen stellt verworfene Credits nicht wieder her

**Quellen:** Gemergter `core/library/artist_credits.py:1–23`, Credit-Features `6ec2ece64` und `55b3f0edd`; bei uns `core/library2/provider_credits.py:1–17`, `:50–75`.

Dev speichert Provider-Credits unabhängig davon, ob ein lokaler Artist existiert. Bei späterem Provider-Match des Gastkünstlers werden dessen Auftritte über den Join sichtbar. Unser Linker verwirft den Credit, solange dieser Artist fehlt. Die Datei dokumentiert ausdrücklich, dass erst ein weiterer Match den Credit wieder hinzufügen kann.

**Reproduktion:** Dev speichert A+B. Native Speicherung verwirft B. Nach Hinzufügen von korrekt gematchtem B existieren weiterhin **0 Track-Credits für B**.

**Verbesserung:** Provider-Credits als Snapshot behalten; Junctions beim späteren Artist-Match materialisieren oder beim Lesen auflösen. Das erfordert keine zusätzlichen Phantom-Artists im Grid. Tests für Track und Album zuerst, Gastkünstler später.

### A05 — Compilation-Performer aus vorhandenen Tags wird nicht nachgeheilt

**Quellen:** Gemergter `duplicate_detector.py:238–278`, `ba545eb3a` (#1315); bei uns `core/library2/importer.py:2097–2155`, `core/library2/scan.py:202–264`, `core/library2/duplicate_relationship.py:107–114`. Bereits als Restrisiko im [Sync-Bericht](upstream-sync-3.5.0.md) genannt.

Für alte Compilation-Tracks mit leerem Legacy-`track_artist` bleiben beim Import nur Various-Artists-Credits. Eingebettete Performer-Tags können korrekt vorhanden sein; der native Tag Scan cachet sie lediglich. Der entfernte dev-Duplicate-Detector hatte dafür eine Tag-Heilung.

**Reproduktion:** Derselbe Song unter Various Artists und Performer A wird vom nativen manuellen Linker mit `Tracks do not share an artist` abgelehnt. Nach Einfügen des tatsächlichen Performer-Credits akzeptiert derselbe Validator das Paar.

Das ist ein enger Bestandsfall, keine Aussage, dass normale Compilations grundsätzlich falsch importiert werden.

**Verbesserung:** Begrenzte Credit-Heilung aus Tags für Compilation-/Placeholder-Fallbacks, einschließlich Herkunft und Album-Junction. Den Sicherheitsvalidator beibehalten und die fehlende Evidenz ergänzen.

### A06 — v1-Künstlerabfrage hat beim lib2-Port den Library-Filter verloren

**Quellen:** `api/library.py:15–45`; `database/music_database.py:17684–17849`. Die gemergte Legacy-Abfrage enthielt `_current_scope_sql('a.owner_profile_id')`. Die native UI-/Compatibility-Abfrage `core/library2/queries.py:269` enthält bereits eine scoped Alternative.

Die öffentliche API verwendet weiterhin `get_library_artists`. Dessen lib2-Abfrage liest globale Artists, Album- und Trackcounts ohne Sichtbarkeits-/File-Owner-Prädikat. `profile_id` wirkt auf die Watchlist, nicht auf die gelesene Bibliothek. Ein bloßes Setzen des Request-Scope würde diese SQL-Abfrage deshalb noch nicht korrigieren.

**Reproduktion:** Temporärer Katalog mit „Shared Only“ und „Own Profile 2“, jeweils mit Datei in der passenden Bibliothek. `get_library_artists(profile_id=2)` liefert **beide Artists**. Die Probe verwendet den echten Reader; ausschließlich Legacy-ID-Projektion und Sortausdruck wurden vereinfacht.

Die API-Keys sind administrativ; dies ist kein nachgewiesener Rechtegewinn eines unprivilegierten Users. Es ist eine verlorene Bibliotheks-/Profilsemantik und kann externe Clients falsche Bestände/Counts anzeigen lassen. Neuer dev hat zusätzlich explizite API-Scope-Fixes (`c01058dc3`, `f0e369d87`); diese allein ersetzen die fehlenden lib2-Prädikate nicht.

**Verbesserung:** API und native UI auf denselben scoped Reader abbilden, API-Profil ausdrücklich in Library-Scope übersetzen und anschließend zurücksetzen. Counts mit derselben Sichtbarkeitsregel berechnen. Test über den echten API-Endpunkt mit Shared-, Own- und dateilosen Discography-Rows.

### A07 — Globales Album-Grid bleibt eine echte offene Paritätslücke

**Quellen:** Dev-Features `7fdd98ac2`, `78b9318cf`, `4431ac2ab`; gemergtes `GET /api/library/albums`, `get_library_albums`; bei uns native Library-Route und `library-v2-page.tsx`. Im [Sync-Bericht](upstream-sync-3.5.0.md) bereits ausdrücklich offen.

Dev konnte die ganze Bibliothek nach Alben durchsuchen und nach Titel, Jahr oder zuletzt hinzugefügt sortieren. Unser Einstieg listet Künstler; Albumansichten existieren innerhalb eines Artists. Die v1-Alben-API und ein Album-Inspection-Tray ersetzen das globale Browse-Grid nicht.

**Verbesserung:** Native globale Albumabfrage und Browse-Umschalter mit Paging, denselben Library-Filtern und stabiler Sortierung. Keine Wiederherstellung alter SQL-/UI-Pfade erforderlich.

## B. Schon früher vorhandene Fehler: neuere dev-Korrekturen fehlen

Diese Fehler sind relevant für normale Nutzung, wurden aber nicht durch den lib2-Merge neu eingeführt. Bei B01/B02 und DATE-Writer stimmen die geprüften Produktions-Funktionskörper von uns und gemergtem dev per AST überein. Bei B03 wurde dieselbe fehlerhafte Guard-Entscheidung auf beiden Ständen ausgeführt.

| ID | Priorität | Fehler bei uns | Neuere dev-Korrektur / Stellen |
| --- | --- | --- | --- |
| B01 | P1 | Soulseek-Cleanup/Cancel erfasst fremde Downloads und Suchen einer gemeinsam genutzten slskd-Instanz | `5ba93cc67`, `f588291e1`; `core/soulseek_client.py:1321`, `:1387`, `:1485`, `:1527`; automatisierte Aufrufer in `handlers/download_cleanup.py` |
| B02 | P1 | Podcast-Watchlist löst Downloads aus, obwohl ihr Besitzer kein Downloadrecht hat | `b71cd56b9`; `api/podcasts.py:998`, `:1010–1029`; `core/podcast_automation.py:167`, `:248` |
| B03 | P2 | Wishlist-Cleanup akzeptiert bei Discography-/Watchlist-Wünschen denselben Song auf einer anderen Veröffentlichung | `5473660a8`; `core/wishlist/processing.py:800`; `library_match.py` kennt die neue Source-Type-Guard noch nicht |
| B04 | P2 | Metadata-Writer ersetzt ein vollständiges Datum durch eine weniger genaue Jahreszahl | `e4a21db17`; `core/metadata/source.py:915–916` |
| B05 | P2 | Discovery-Pool-Playlistfilter filtert nur die failed-Liste; matched-Liste und Stats bleiben global | `561acc438`; `api/mirrored_playlists.py:824–826`; `database/music_database.py:19476`, `:19549` |

**Isolierte Gegenproben:**

```text
B01 ours/gemergter dev: DELETE own UND foreign; completed-Cleanup benutzt all/completed
    neuer dev:          DELETE nur eigene slskd-ID; History prune nur älteste eigene Suche
B02 Besitzer 2, can_download=False: ours/base queued=1; neuer dev queued=0
B03 gleiches Lied nur als Single vorhanden, Album weiterhin unbesessen:
    ours/base: source_type=discography/watchlist/watchlist_label -> entfernt
    neuer dev: diese drei Typen require_album=True -> bleibt erhalten
    source_type=album: auf allen drei Ständen korrekt erhalten
B04 DATE=1991-08-12, Provider liefert DATE=1991:
    ours/base schreibt 1991; neuer dev erhält 1991-08-12
B05 Playlist A ausgewählt: matched=[Alpha aus A, Beta aus B]; failed=[Failed A]
    Stats matched=2, failed=2; für A erwartet jeweils 1
```

**Port-Ideen:** B01 Ownership ausschließlich per tatsächlicher slskd-ID, unbekannte/fremde IDs erhalten. B02 Besitzerrecht direkt vor dem Queueing erneut prüfen, auch bei bestehenden Watchlists nach Rechteentzug. B03 den Release-Kontext für alle albumbezogenen Source Types durchreichen. B04 Datumspräzision erhalten, Edition und Originaldatum getrennt modellieren. B05 Filter und Counts über dieselbe normalisierte Playlist-Key-Menge bilden.

## C. Neue dev-Funktionen: noch nicht integriert

Diese Tabelle enthält konkrete fehlende Fähigkeiten und größere Verhaltensänderungen nach der Merge-Base. Sie ist keine Liste versehentlich verlorener Merge-Features und kein Anspruch, jede reine Text-/Layoutänderung der 92 Nicht-Merge-Commits einzeln abzunehmen.

| Funktion | Neue dev-Commits | Befund / vorhandene Grenze |
| --- | --- | --- |
| Global „Wishlist missing tracks on sync“ + Override je Klick | `5f3131668`, `f657fa308` | Setting-Resolution/Adapter fehlen. Probe: globale Einstellung false → unser Adapter reicht skip_wishlist=false weiter, dev true. Eigener Automation-Skip funktioniert weiterhin. |
| Server-Playlist nach Rename über gespeicherte ID verfolgen | `980e58260` | Neuer `core/sync/mirrored_server_link.py` und Service-Wiring fehlen. Bisheriger Namensabgleich bleibt; neuer Port ist zunächst Navidrome-spezifisch. |
| Sichererer Sync-Default Reconcile | `19ff27330` | Reconcile als Modus ist vorhanden; `normalize_sync_mode` und Service-Default bleiben replace. Bestehende gespeicherte Modi nicht beim Port ungefragt verändern. |
| v1 Recently Played / Curated Playlists / Mirrored Playlists | `774ddcf4e`, `e59a69220`, `c97233157` plus Folgefixes | Fünf neue GET-Routen fehlen. Minimaler echter Flask-Blueprint liefert für die drei Einstiegsrouten 404. Bereits bestehende v1-Artist-/Album-/Track-APIs sind vorhanden. |
| Manual Library Match mit unmatched Wanted vorbefüllen | `647afd94a` | Neue unmatched-Route und neue Worklist fehlen; vorhandener manueller Match bleibt nutzbar. |
| Discovery-Matches nach Match-% sortieren / Cache gesammelt leeren | `8b49f1f9b` | Neue Clear-Route fehlt; einzelne Cache-Einträge können weiterhin gelöscht werden. Filterfehler separat B05. |
| BPM-Backfill-Job mit Review | `60bc2f201`, `95f6f46d2`, `1ee0443bd` | Kein Job/Finding-Handler. Vorhandenes Provider-BPM und Sample-Analyse ersetzen fehlende-BPM-Backfill mit lokaler Analyse nicht. |
| Artist-NFO beim Import + Backfill | `e4a21db17`, `1e295abe3` | Neuer Sidecar-Writer, Import-Hook, Job und opt-in-Setting fehlen. |
| Album Release Year Alignment mit Tag-/Folder-Plan | `5d0a3dbbf` | Neuer Job/Finding fehlt. Retag/Reorganize nutzen das gespeicherte Katalogjahr und ermitteln kein kanonisches Originaljahr. |
| Repair-Scheduling über AutomationEngine | `1dc7d9aa8` | Neue vereinheitlichte Scheduling-Architektur fehlt. Die bisherigen intervallbasierten Repair-Scheduler existieren weiterhin; kein belegter genereller Ausfall von geplanten Jobs. |
| Bestätigung beim Deaktivieren von Dry Run | `dea1f572f` | In React-Tools noch direkte Checkbox-Speicherung, keine neue Bestätigung. Vorhandene Preview-/Effects-Grenzen bleiben nutzbar. |
| Originaldatum optional als DATE | `e4a21db17` | `musicbrainz.use_original_date_for_date` fehlt im Writer/Settings. ORIGINALDATE/ORIGINALYEAR als separate Tags bereits vorhanden. |
| MusicBrainz-Barcodesuche und UPC als Editionspriorität im Consistency-Matcher | `0f83cd409` | UPC im nativen Editionsmodell vorhanden; neuer Barcode-Such-/Scoring-Pfad fehlt. Probe: falsche US-Ausgabe gewinnt 74:72; mit neuem UPC-Bonus richtige GB-Ausgabe 122:74. |
| Release-Junk ohne Audio standardmäßig bereinigen | `164b77abb` | .nfo/.sfv/.m3u bleiben bei Standardeinstellung liegen; breiter opt-in-Modus vorhanden. Predicate-Probe auf beiden Git-Ständen. |
| Aktueller AudioDB-Free-Key | `0328b557b` | Client verwendet weiter `/json/2`, dev `/json/123`. Behauptete Stilllegung aus dev-Fix, keine Live-Provider-Abnahme. |
| Audible-Storefront als globale Einstellung | `114902986` | Request-Marketplace funktioniert; globale Einstellung und Übergabe an Watchlist-Scans fehlen. |
| Episode-/YouTube-Requests | `b71cd56b9`, `348bacbec` | API akzeptiert nur Movie/Show. Beide neuen Payload-Typen im echten extrahierten Endpoint-Body mit HTTP 400 abgelehnt. |
| Video-Calendar Wishlist-/Library-Badges auf Grid/Hero/Movie-Rail | `68818dcf2` | Erweiterung/Event-Refresh fehlt; Agenda/Modal besitzen bereits Acquisition-Status. |

Ein unveränderter Cherry-Pick dieser Features ist wegen der neuen lib2-Datenidentitäten und Mutation-Grenzen nicht durchgehend geeignet. Besonders NFO/Year/BPM-Jobs brauchen native Maintenance-Subjects und konkrete File-/Owner-IDs.

## D. Geprüfte Gegenstücke und bewusst reduzierte Fähigkeiten

| Feature / auffälliger Legacy-Verlust | Ergebnis |
| --- | --- |
| Artist-Grid, Paging, Sortierung | Native Library vorhanden; API-Scope-Sonderfall A06. |
| Artist Top Tracks | Vorhanden im optionalen Rich Header, `library-v2-page.tsx:6000`, `:6425`. Ältere Dokuentscheidung „nicht übernehmen“ ist überholt. |
| Similar Artists, Rich Hero, Concerts | Gemeinsame Artist-/Hero-Komponenten vorhanden; Quellen geprüft, keine Live-Provider-Abnahme. |
| Export/M3U | `export-modal.tsx:156` vorhanden. Ältere Doku „zurückgestellt“ ist überholt. |
| Appears On / Collabs | Native Artist-Seite traversiert Album- und Track-Artist-Junctions (`queries.py:957–965`). Source-only Detailseite unterdrückt den entfernten Legacy-Request (`artist-detail-page.tsx:538`). A04 betrifft zukünftige, verworfene Credits. |
| Completeness, Wanted Missing/Cutoff | Native Gegenstücke vorhanden; Editions- und False-Link-Grenzen A01/A02. |
| ReplayGain, Source Info, Tag Preview/Write | Native `api/library/v2/...`-Routen und Module vorhanden. Entfernte Legacy-URLs sind dafür kein Feature-Verlustbeweis. |
| Smart Delete / Bulk-/File-Management | Native Preview-/Delete-/Track-File-Pfade vorhanden. |
| Reorganize / Retag / Auto-Link nach Download | Native Plan-/Apply-Pfade und registrierte Jobs vorhanden; Auto-Link-Quelle untersucht. Kein zusätzlicher bestätigter Auto-Link-Befund. |
| Album Inspection Tray | Vorhanden, native Track-/File-Identität angepasst. |
| Quality-Upgrades | Native Wanted-/Quality-Audit-Pfade vorhanden; Apply Quality Upgrades und Review-UI vorhanden. |
| Untargeted Format standardmäßig belassen | Äquivalenter nativer Port vorhanden: `quality_eval.py:232–244`, `quality_profile_audit.py:81–87`, Worker :3103/:3190. Kein Wiederbeleben des alten Scanners erforderlich. |
| Unterbrochener Quality-Scan | Nativer Stopped-Pfad vorhanden (`quality_profile_audit.py:61–63`, Worker :1333–1335). |
| Vom dev-Bad-Merge entfernte DB-Methoden | `get_all_automations` und vorhandene manuelle Match-Lookups bei uns bereits erhalten/wiederhergestellt. Diese bekannten dev-Mergefehler nicht erneut als fehlend zählen. |
| Fuzzy Duplicate Detector / automatisches Keep Best | Absichtlich enger: sichere/manuelle Beziehungen und Dateiauswahl; Playlist-Hinweise vorhanden. Automatisches Keep Best inklusive automatischer Playlist-Kopie ist kein vollständig gleichwertiges natives Feature. |
| Native Artist Mass Editor / eigener Metadata-Profile-Vertrag / Raw Artist Inspector | In `library-v2-features.md` als Produktreduktion dokumentiert. Rückkehr zur alten UI nicht implizit versprechen; existierende native Edit-/Inspection-Möglichkeiten sind enger. |

Die Tabelle in `docs/library-v2-features.md` §6 ist ausdrücklich eine Produktentscheidung, keine aktuelle Statusliste. Die Beispiele Top Tracks und M3U zeigen, warum alleinige Doku-/Dateinamenprüfung falsche Verlustmeldungen erzeugt.

## E. Architektur und Performance

1. **Parität als Nutzerverhalten festhalten.** Pro dev-Feature Trigger, sichtbares Ergebnis, Dateninvariante, native Umsetzung und gezielten Test verknüpfen. Status getrennt: übernommen, äquivalent portiert, bewusst reduziert, noch offen. Eine entfernte Datei oder ein fehlender Commit reicht für keine Entscheidung.
2. **Eine Edition-Auswahl für alle Verbraucher.** Match-Chips, Completeness, Retag, Track Number Repair und Reorganize sollen dieselbe konkrete, versionierte Auswahl benutzen. Cache-Key an Edition/Revision binden. Vermeidet widersprüchliche Modelle und unnötige Tracklist-Fetches; Gewinn noch nicht gemessen.
3. **Kandidaten von Nutzerentscheidungen trennen.** Duplikatähnlichkeit, bestätigte Recording-Identität und bewusst entfernte Datei separat speichern. Nur die letztgenannten Zustände dürfen Missing unterdrücken. Gemeinsame Validierung für Importer/API/Jobs.
4. **Credits behalten und gezielt nachverbinden.** Provider-Credit-Snapshots ohne Phantom-Artist-Rows speichern. Beim Artist-Match indexiert nach Provider-ID nachverbinden. Das vermeidet vollständiges Re-Matching der Bibliothek, um Gastauftritte wiederzufinden.
5. **Scoped Datenreader wiederverwenden.** API und UI sollen dieselbe Bibliothekssicht und Counts verwenden. Dadurch entfällt die Pflege zweier großer Künstlerabfragen; globale Aggregationen für einen kleinen privaten Bestand können entfallen. Keine Benchmark-Aussage.
6. **Neue Jobs mit erklärten Effects und Ownern portieren.** Registry, Finding, Preview und Fix müssen denselben native Subject-Vertrag erfüllen. Originaljahr und Editionsjahr getrennt halten; Artist-NFO nicht versehentlich als beliebigen Release-Junk löschen.
7. **Scheduling als Migration behandeln.** Bei späterem AutomationEngine-Port vorhandene Intervalle/Toggles idempotent übernehmen; eine Scheduling-Quelle, keine doppelt laufenden Jobs. Master-Pause, manuelle Starts, Neustart und verschiedene Bibliotheken testen.

## F. Nachweise und empfohlene Reihenfolge

Die folgenden Hilfsdateien liegen nur unter `/tmp` und sind kein produktiver Code. Die Kernfälle und Ergebnisse stehen oben, damit der Bericht auch nach deren Entfernung verständlich bleibt.

```bash
.venv/bin/python /tmp/soulsync-feature-audit/probe_lib2_feature_parity.py
# 5 native Paritätsproben: Unknown-Recovery, zukünftige Credits, Compilation,
# manuelle Edition, automatischer Duplikatlink mit Missing-SQL.

.venv/bin/python /tmp/soulsync-feature-audit/probe_root_feature_parity.py
# Adapter-/DB-/Blueprint-Proben: Sync-Toggle, Discoveryfilter, API-Scope,
# neue v1-Routen und albumbezogene Cleanup-Guards auf drei Git-Ständen.

.venv/bin/python /tmp/feature_parity_delta_probe.py
# Soulseek, Podcast-Rechte, DATE, Episode-/YouTube-Payloads,
# Barcode-Scoring und Empty-Folder-Prädikate.

.venv/bin/python -m pytest -q \
  tests/library2/test_provider_credits.py \
  tests/library2/test_release_editions.py \
  tests/library2/test_match_status.py \
  tests/library2/test_legacy_api_artists_profile_scope.py \
  tests/wishlist/test_cleanup.py \
  tests/automation/test_handlers_playlist.py
# 79 passed in 2.92s
```

Die bestehenden Tests sichern etliche native Teilfunktionen ab. Die zusätzlich reproduzierten Verhaltensübergänge fehlen bislang: manueller Match bis zur genutzten Edition, automatische Gleichsetzung trotz Recording-Unterschieden, Gastkünstler erst nach ursprünglichem Match sowie API-Aufruf bis zur konkreten Bibliothekssicht.

**Reihenfolge:** A01/A02 und B01/B02 zuerst; anschließend A06 und B03, Unknown-Recovery/Credits und die übrigen Datenfehler. Album-Grid sowie neuere dev-Funktionen als explizite Paritätsarbeit planen. Eine Freigabe sollte gezielte Bestands-/End-to-End-Szenarien verlangen; dieser Audit liefert keine Garantie vollständiger Fehlerfreiheit.

**Offene Prüffrage:** Bulk Best-Fit Canonical Resolver: Legacy-Job entfernt, Resolver-/Legacy-Aufrufer noch vorhanden. Kein ausreichend belegtes natives Review-Gegenstück gefunden. Nicht als zusätzlichen bestätigten Verlust gezählt.
