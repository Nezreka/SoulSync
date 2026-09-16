# Status der Review-Behebung — Review vom 2026-09-05

Branch `library-overhaul`. Ausgangslage: [REVIEW.md](REVIEW.md) — **25 bestätigte
Branch-Befunde (4 × P1, 21 × P2)** plus **FI-04**, ein upstream bereits
behobener, hier fehlender Importfix.

**Stand: alle 26 behoben, jeder mit Regressionstest.** Ein Befund gilt hier erst
als erledigt, wenn ein Test existiert, der *ohne* den Fix fehlschlägt. Wo das
gegengeprobt wurde, steht es unten dabei.

| | Anzahl |
|---|---|
| Befunde gesamt (inkl. FI-04) | 26 |
| ✅ behoben + Test | 26 |
| ⬜ offen | 0 |

Nicht Teil dieser Runde (bewusst): die Aufteilung der 11.684-Zeilen-Library-Seite,
das Bundle-Chunking, die neun bereits vor dieser Runde unformatierten
Library-Dateien und die Dead-Code-Kandidaten, die eine Produktentscheidung
brauchen. Zwei Dead-Code-Stellen wurden mitgenommen, weil sie direkt an einem
Fix lagen (siehe unten).

## P1

| ID | Fix |
|---|---|
| **FI-01** | `core/acquisition/main_pipeline_bridge.py` — neue `_pipeline_published()`. Der Erfolgs-Callback läuft nur noch bei `_pipeline_import_succeeded` (der Vertrag, den Auto-Import schon prüft), ohne Ablehnungs-/Quarantänemarker **und** mit nachgewiesener aktiver `lib2_track_files`-Zeile für den Zielpfad. Die Prüfung läuft über die Verbindung des Aufrufers — in Produktion dieselbe DB, die `advance_import` bekommt. |
| **MIG-01** | `core/library2/migration_gate.py` — neue `endpoint_is_blocked()`, die zusätzlich den unqualifizierten Endpoint-Namen vergleicht; `web_server.py` nutzt sie. **Gemessen: 77 der 105 Sperrlisteneinträge griffen nicht**, nicht nur die zwei im Review genannten — die Liste ist nach Handler-Funktionsnamen gepflegt, während Upstream Routen laufend in Blueprints verschiebt. |
| **MIG-02** | `web_server.py` — `start_library_v2_bootstrap_autostart()` (idempotent, ein lebender Loop startet nicht doppelt); Startup nutzt denselben Einstieg. `api/database_admin.py` ruft ihn nach einem Restore, wenn `migration_required()` gilt, und meldet `migration_restarted` in der Antwort. |
| **ARCH-01** | `core/library2/discography.py` — der im Lauf ergänzte Indexeintrag trägt jetzt `musicbrainz_release_group_id`, also denselben Feldvertrag wie die geladenen Zeilen. |

## P2

| ID | Fix |
|---|---|
| **MIG-03** | `core/library2/importer.py` — `_require_importable_legacy_source()` validiert alle drei Quelltabellen **vor** dem destruktiven Reset. |
| **MIG-04** | Neue `external_provider_identity_sql()` in `core/library2/provider_ids.py`; die drei Reconcile-Prüfungen erkennen jetzt jede Provider-ID in `external_ids` (Deezer/Tidal/Qobuz …). `isrc`/`upc`/`barcode` bleiben ausgenommen — Produktcodes, keine Identität. |
| **MIG-05** | `_ArtistResolver.upsert_legacy` merged Provider-IDs bei **jedem** Upsert, nicht nur im einmaligen Adoption-Zweig. Ein Replay kann nur noch hinzufügen. *(gegengeprobt)* |
| **FI-02** | `core/library2/autolink.py` — ein Re-Import auf einen Pfad, dessen Zeile `deleted`/`missing_*`/`quarantined` war, reaktiviert sie jetzt (`missing_since`/`missing_scan_count` zurückgesetzt, gemeinsame Primary-Wahl über `set_file_state`). Dieselbe Regel, die der SoulSync-Writer schon immer hatte — nur für den Writer, den jede Nicht-SoulSync-Installation benutzt. *(gegengeprobt)* |
| **FI-03** | Arbeitskopien tragen einen Provenance-Marker (`<datei>.soulsync-acquisition.json`, Import + Track). Eine vom eigenen Lauf getaggte Kopie wird verworfen und aus dem unberührten Original neu erstellt; fremde Inhalte kollidieren weiterhin laut. *(gegengeprobt)* |
| **FI-04** | Upstream-Commit `75384d3d` portiert (`core/imports/file_integrity.py`, `core/hifi_client.py`, `tests/imports/test_lossless_density_guard.py`): Lossless-Dichte ist **Anlass zur Dekodierung**, nicht Urteil; Zero-Length-Zweig lehnt gegen die erwartete Dauer ab; `is_fake_lossless_bitrate` liegt jetzt an einer Stelle. |
| **SYNC-01** | `services/sync_service.py` + `core/watchlist_scanner.py` reichen `quality_profile_id` wieder explizit weiter (am Merge-Base vorhanden, im Branch entfallen). *(gegengeprobt)* |
| **SYNC-02** | Neues `core/wishlist/identity.py` leitet den Release-Key (`<track>::<album>`) an **einer** Stelle ab. Die Existenzprüfung des Mirrors kennt ihn jetzt (Trigger A), und die neue `MusicDatabase.remove_release_from_wishlist()` entfernt genau ein Release statt aller `<track>::%`-Zeilen (Trigger B). Alte, nackt gekeyte Zeilen bleiben entfernbar; eine Zeile, die ein *anderes* Album nennt, ist geschützt. *(gegengeprobt)* |
| **SYNC-03** | `mirror_outbox._entity_key` schlüsselt auf denselben Release-Key; Ops ohne `key` (bereits eingereihte Zeilen überleben einen Neustart) verhalten sich unverändert. *(gegengeprobt)* |
| **SYNC-04** | `autolink` übergibt `ADMIN_PROFILE_ID` an `recompute_wanted` statt der Qualitätsprofil-ID. Der Test, der die falsche Kopplung festschrieb, ist korrigiert und prüft jetzt zusätzlich den Consumer im Admin-Scope. |
| **ACQ-01** | `acquisition_grabs` hat eine `request_attempt`-Spalte (Migration mit Default 0); `find_request_candidate_grab` filtert auf den aktuellen Versuch. Wiederholte Aufrufe innerhalb eines Versuchs teilen weiter ihren Grab (Doppelklick-Schutz), ein ausdrücklich neu gestarteter Versuch legt einen neuen an. |
| **ACQ-02** | Neue `upgrade_intent.carry_upgrade_intent()`; Bridge-Task und Restart-Rebuild tragen den versiegelten Intent. Sie kann keinen Intent erzeugen — nur einen bereits aufgelösten weiterreichen. *(gegengeprobt)* |
| **ACQ-03** | `record_submission_started()` committet vor dem Netzaufruf. `SUBMISSION_IN_FLIGHT_STATES` (`submission_started` + `submission_unknown`) schließt solche Zeilen aus der Restart-Bereinigung aus und macht sie adoptierbar — im zentralen Monitor und im Usenet-Plugin. Fehlender Marker heißt jetzt „nachweislich nichts gesendet“. *(gegengeprobt)* |
| **ARCH-02** | `queries.get_album` zählt in der Missing-Summe nur Zeilen mit echter ID; die versprochenen Slots kommen genau einmal dazu. Die Anzeige erwarteter Slots auf unüberwachten Alben bleibt erhalten. *(gegengeprobt)* |
| **ARCH-03** | `reorganize_plan` bestimmt `total_discs` aus **allen** Katalogpositionen (inkl. effektiver Overrides), bevor nach vorhandenen Dateien gefiltert wird. *(gegengeprobt)* |
| **ARCH-04** | Neue `metadata_overrides.effective_artist_name(s)`; Retag (ARTIST/ALBUMARTIST) und Reorganize (Ordner + Credits) lesen dieselbe effektive Projektion wie die UI. *(gegengeprobt)* |
| **UI-01** | `library-v2-page.tsx` beobachtet `bootstrap.status === 'running'` gleichberechtigt mit `importState.running`; der Abschluss invalidiert den Katalog. Ein `bootstrap`-Fehler überschreibt die Migrations-Meldung nicht mit einer Import-Abschlussmeldung. |
| **UI-02** | Der Sektionswechsel liegt in einer exportierten `librarySectionSearch()`, die auf beiden Seiten `page: 1` setzt — die beiden Buttons können nicht mehr auseinanderlaufen. |
| **UI-03** | `libraryV2ImportStatusQueryOptions` beobachtet einen `failed`-Bootstrap weiter, mit Backoff zwischen 5 s und 60 s. Nur ein echt terminaler Zustand stoppt das Polling. |
| **INT-01** | Last.fm schreibt in `lib2_track_id` (die Katalogspalte), nicht mehr in `db_track_id` (der Namensraum des Medienservers). Dazu eine quellenspezifische, idempotente Reparaturmigration für die bereits falsch abgelegten Zeilen. *(gegengeprobt)* |
| **INT-02** | Die Chart-Detailansicht joint `lh.lib2_track_id` — derselbe Vertrag, den `get_recent_tracks` schon nutzt. Der Test, der die falsche Spalte einsäte, ist korrigiert. |
| **INT-03** | `search/library_check` und `stats/queries` lösen über **alle** Trackcredits auf (relationale Credits, `track_artist`-Text), der Albumartist bleibt Fallback. Ein Muse-Track auf einer Various-Artists-Compilation wird gefunden; ein fremder Artist matcht weiterhin nicht. *(gegengeprobt)* |

## Mitgenommene Dead-Code-Stellen

Nur die zwei, die direkt an einem Fix lagen:

- `database/music_database.py` — der zweite, unerreichbare `if existing is not None:`-Block in `add_to_wishlist_detailed` (er gab zudem ein nacktes `False` in eine Funktion zurück, die ein Outcome-Dict liefert). Entfernt.
- `tests/downloads/test_downloads_task_worker.py` — die veraltete Query-Anzahl aus der Verifikations-Tabelle des Reviews. Der Test liest die Anzahl jetzt aus dem Task, statt eine eingefrorene Zahl zu behaupten. Dieser Test existiert upstream nicht; er ist branch-eigen und war nur nach einer übernommenen Upstream-Änderung veraltet.
- `tests/test_prowlarr_throttle.py::test_the_manual_video_endpoints_pass_a_bound` — **entfernt.** Dieser Test steht wortgleich auf `upstream/dev` und schlägt dort genauso fehl: er zählt zwei direkte `prowlarr_search`-Aufrufe, während beide Endpunkte inzwischen durch den gemeinsamen `_torrent_lane_hits`-Helper gehen — er schlug also ausgerechnet wegen des Refactorings fehl, das die Drift beseitigt hat, die er finden sollte. Er gehört Upstream, nicht diesem Branch; eine hiesige Umschreibung wäre nur ein Merge-Konflikt beim nächsten Sync. Die geprüfte Eigenschaft ist damit vorerst ungedeckt — die Behebung gehört als eigener Beitrag nach upstream.

Die übrigen Kandidaten aus der Review-Tabelle (`catalogue_refresh`, `autoGrabBest`, `fetchLibraryV2AlbumReorganizeSources`, `featured_from_title`, `load_rank_candidates_by_quality`, `reorganize_bridge`-Quellenmodus, doppelte Korrelationsleser) bleiben offen — sie brauchen jeweils eine Entscheidung, keinen Bugfix.

## Verifikation

| Prüfung | Ergebnis |
|---|---|
| Vollständiges pytest (`-n 8 --timeout=120`) | **18.322 bestanden, 4 übersprungen**. Die einzigen Fehler sind die bekannten Parallel-Isolationsflakes: pro Lauf trifft es ein anderes Paar (`test_candidate_store` bzw. `test_deezer_throttle`/`test_video_seed_rules`), und alle bestehen einzeln. |
| Die drei Baseline-Fehler des Reviews | `test_downloads_task_worker` ist **repariert**; `test_prowlarr_throttle::test_the_manual_video_endpoints_pass_a_bound` ist **entfernt** (schlägt unverändert auf `upstream/dev` fehl und gehört dorthin); `test_blocklist_api` besteht. |
| Vitest `src/routes/library` | **381 bestanden, 46 Dateien.** |
| Ruff (ohne `docs/`) | **bestanden.** In `docs/reviews/.../migration_repro.py` bleibt ein B007 — Review-Artefakt, kein Anwendungscode. |
| `tsc --noEmit` | Keine neuen Fehler. Es bleiben genau die neun Testcode-Fehler, die der Review auch auf `upstream/dev` gemessen hat. |
| `oxfmt --check` | Die beiden Dateien, die meine Änderungen berührt haben, sind formatiert. Die neun bereits vorher beanstandeten Library-Pfade sind bewusst **nicht** angefasst — ein Reformat von `library-v2-page.tsx` (11.7k Zeilen) würde die Bugfixes darin begraben und gehört in einen eigenen mechanischen Commit. |

Was diese Runde **nicht** belegt, weil es Laufzeit braucht und nicht Tests: die
sechs Abnahmepunkte am Ende von [REVIEW.md](REVIEW.md) — echter Upgrade auf einer
Kopie realer Legacy-Daten, Crash-Recovery mit getrennten Prozessen, echter
Usenet-/Medienserver-Betrieb, Browser-Durchgang der Migrations-UI. Die Fixes sind
so gebaut, dass diese Läufe sie prüfen können; ausgeführt sind sie hier nicht.


## Nachtrag: Upstream-Merge 3.3.3 (nach den Fixes)

Die Fixes sind als ein Commit (`030f7fa5c`) festgehalten, danach wurde
`upstream/dev` (`ab94f544f`, 34 Commits) gemergt (`feec196b4`).

**Befund vorweg:** der frühere „Merge upstream/dev 3.3.3" hatte `c92b8c87e` als
zweiten Parent — ein Commit aus unserer eigenen Historie. Die Ancestry hat 3.3.3
also nie als integriert geführt, und der Inhalt war auch nicht da. Dieser Merge
holt die echten 34 Commits und setzt einen Parent, der das aussagt.

Portiert statt nur aufgelöst:

| Stelle | Was Upstream tat | Warum ein reiner Merge falsch gewesen wäre |
|---|---|---|
| `core/quality/selection.py` | neue `profile_id_for_library_track` — Redownload lief auf dem App-Default und ersetzte damit Dateien strenger zugewiesener Tracks | liest `tracks.quality_profile_id`; die ID dieser Route ist hier eine `lib2_tracks`-ID → hätte für **jeden** Track None geliefert und genau den Bug wieder eingeführt. Läuft jetzt über die Katalog-Kaskade; Upstreams Tests auf lib2 umgesetzt |
| `core/discovery/playable.py` | ein Batch-Scan statt eines `LOWER(title)`-Scans pro Mix-Eintrag | die Optimierung lag auf der Legacy-Query, die hier niemand ausführt — auf die v2-Query portiert, inkl. Preferred-File-Auswahl |
| `core/download_plugins/candidate_store.py` | Kandidaten-**Metadaten** (Indexer-Kategorien) am Token | unser Store gewinnt strukturell (SQLite, Profil-/Entity-Bindung), aber ohne die Metadaten wäre `evaluate_release` still auf Titel-Only zurückgefallen. `metadata_json` + `resolve_with_metadata` unter denselben Bindungsprüfungen ergänzt |
| `core/async_utils.py` | `contextvars`-Kopie in den Worker | orthogonal zu unserem Python-3.14.6-Workaround — beides gilt jetzt |
| `core/download_orchestrator.py` + `core/download_engine/engine.py` | `quality_profile_id` durch Suche und Download, `quality_profile_context` um den Transfer | unsere Dispatch-Auflösung liegt an **einer** Engine-Grenze statt in einem Per-Source-if/else — das Delta ist dort eingebaut |
| `core/downloads/candidates.py` | Profil aus `track_info['quality_profile_id']` | ausdrücklich **nicht** unser `task_profile_id` — das ist das Benutzerprofil. Zwei Namensräume, die gleich aussehen |

Von beiden Seiten behalten: die Torrent-/Usenet-Plugins behalten unsere
Token-Bindung pro Result-Kind **und** tragen Upstreams Indexer-Kategorien; die
Qualitätslesung wird Upstreams evidenzbasierte `audio_quality_from_release`.

`test_the_manual_video_endpoints_pass_a_bound` ist wieder da — Upstream hat die
veraltete AST-Zählung selbst repariert. Das bestätigt die Entscheidung, ihn zu
entfernen statt lokal umzuschreiben.

Schranken nach dem Merge: der Deleted-Path-Audit meldet nichts Neues (150 Pfade,
20 bereits entschieden), und der Legacy-Usage-Ratchet steht wieder bei 0 Reads —
er hatte den Legacy-Fallback erwischt, den ich beim Port kurzzeitig stehen ließ.

Verifikation nach dem Merge: **18.692 pytest**, **8.040 vitest in 409 Dateien**,
Ruff sauber, keine neuen Typecheck-Fehler. Nur die bekannten
Parallel-Isolationsflakes, pro Lauf ein anderes Set, alle einzeln grün.
