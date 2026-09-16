# Specialist review: Wishlist / Watchlist / Sync

Abgeschlossen am 2026-09-05. Branch `library-overhaul`, HEAD `9fade33e54fa3185b75236e85e44531307b7c0df`; Vergleich `upstream/dev` `8889a81c60ffb458a44bdbcf4bc7137d67140cb5`; Merge-base `c92b8c87e694ce693251fde88d6c3a7c14d0d41c`.

**Ergebnis: vier bestätigte P2-Befunde.** Keine P1-Einstufung im geprüften Ausschnitt. Zusätzlich ein sicher unerreichbarer Codeblock als P3-Bereinigungskandidat. Alle folgenden Fehler sind durch den Branch eingeführt: SYNC-01 entfernt bereits am Merge-base und auf upstream/dev vorhandene Weitergabe; SYNC-02/03 betreffen die neue Library-v2-Outbox, SYNC-04 den neuen Autolink-Pfad; beide Module existieren auf upstream/dev nicht.

## Verifikation

[Repro-Script](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/sync_repro.py), [vollständiges Ergebnis](/home/cyran/Projects/05_Soulsync_fork/docs/reviews/2026-09-05/sync_repro_result.txt).

Ausgeführt mit Exit-Code **0**:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python docs/reviews/2026-09-05/sync_repro.py
```

Das Script lädt zuerst die Isolation aus `tests/conftest.py`: Musik-/Video-Datenbank und Konfigurationsauflösung zeigen auf temporäre Pfade. Es verwendet die echte `MusicDatabase`, ihre Schemainitialisierung und Wishlist-Schreib-/Lesefunktionen sowie die echten Mirror-/Reconcile-Funktionen. Ausgehende Socket-Verbindungen sind im Prozess gesperrt. Beim vollständigen `PlaylistSyncService.sync_playlist` werden nur Medienserverzugriff/Matching und die Service-Instanzbeschaffung ersetzt; WishlistService, Reverse-Sync und Persistenz laufen real. Der Watchlist-Fall ruft die echte Scanner-Add-Methode mit einem isolierten Artist-Descriptor auf. Der Dateizustand wird in der synthetischen DB gesetzt; kein echter Scan oder Download findet statt.

Die Assertions charakterisieren die gefundenen Fehler, nicht das gewünschte Verhalten. Eine anfängliche Annahme, dass der gemeinsame Mirror zwei Album-Rows anlegt, scheiterte. Die Untersuchung zeigte den produktiven Supersession-Fehler SYNC-03; die finale Repro prüft ihn ausdrücklich und erzeugt die zwei Rows für den anschließenden Remove-Versuch durch getrennte Mirror-Läufe.

## SYNC-01 — P2 — Playlist-/Watchlist-Qualitätsprofil geht beim Wishlisting verloren

**Reviewstellen:** [services/sync_service.py:597](/home/cyran/Projects/05_Soulsync_fork/services/sync_service.py:597), Zeilen **597–598**; gleichartige zweite Stelle [core/watchlist_scanner.py:2515](/home/cyran/Projects/05_Soulsync_fork/core/watchlist_scanner.py:2515), Zeilen **2515–2516**.

**Trigger:** Die Playlist bzw. der Watchlist-Artist hat ein vom globalen Default abweichendes `quality_profile_id`; ein fehlender Track wird neu in die Wishlist aufgenommen.

**Fehler und Folge:** Beide Aufrufer übergeben den expliziten Profilparameter nicht mehr. Der Playlist-Payload kann das richtige Profil weiterhin enthalten, aber `WishlistService` reicht ausschließlich das separate Argument weiter (`core/wishlist/service.py:213–219`). Die Datenbank löst dieses Argument auf (`database/music_database.py:10930–10932`); bei `None` speichert sie den globalen Default. Der spätere Download-Payload verwendet die gespeicherte Spalte (`core/wishlist/service.py:287`). Damit gelten für den Download andere Qualitäts-/Upgrade-Vorgaben als für die ausgewählte Quelle.

**Reale DB-Repro:** Angefordert Profil **4**, Default **3**; nach Playlist-Sync steht **3** in `review-track::review-album`. Der echte Watchlist-Scanner speichert ebenfalls **3** statt **4**.

**Kompensation geprüft:** Ein anschließender stündlicher `reconcile_track_wishlist` korrigiert nichts: `scanned=0`, `refreshed=0`, Profil bleibt **3**. Die Wanted-Projektion ist bereits aktuell; `monitor_sync.py:949–950` überspringt den vorhandenen Row. Ein ausdrücklich erzwungener `mirror_projected_tracks_wishlist` für den Track korrigiert tatsächlich auf **4**. Ein weiterer Playlist-Sync erhält diese korrekte Spalte, weil ein Update ohne explizites Profil sie nicht überschreibt. Der Befund betrifft somit besonders neu eingereihte Tracks und fehlende Profilaktualisierungen; nicht jeder bereits korrekt gespiegelte Row ist betroffen.

**Fixrichtung:** Die entfernte explizite Weitergabe an beiden Stellen wiederherstellen. Quelle → Wishlist-Spalte → Download-Payload gemeinsam absichern; die bloße Existenz eines Profilfelds im JSON reicht nicht.

## SYNC-02 — P2 — Mirror-Rücknahme verwendet eine andere Identität als der Wishlist-Insert

**Reviewstelle:** [core/library2/mirror_outbox.py:108](/home/cyran/Projects/05_Soulsync_fork/core/library2/mirror_outbox.py:108), Zeilen **108–114**. Zugehöriger enger SQL-Nachweis: [mirror_outbox.py:57](/home/cyran/Projects/05_Soulsync_fork/core/library2/mirror_outbox.py:57), Zeilen **57–58**.

**Trigger A:** Standardkonfiguration `wishlist.allow_duplicate_tracks=True`, Track mit Album-ID ist wishlisted; anschließend erhält er eine Datei, die das Profil erfüllt. Dasselbe Problem betrifft die Rücknahme nach einer passenden Qualitätsänderung.

**Fehler:** Der Datenbank-Insert speichert kanonisch `track_id::album_id` (`database/music_database.py:11008–11012`). Der Mirror prüft vor der Rücknahme nur `payload['id']`, also den nackten Track-Key. Diese exakte Existenzabfrage findet den Composite-Row nicht und überspringt den Remove.

**Reale DB-Repro:** `review-track::review-album` wird durch den echten Mirror angelegt. Nach einer aktiven FLAC-Datei liefert `track_wishlist_payload['_should_queue']` **False**. Trotzdem lässt `sync_scanned_tracks_wishlist` den Row stehen und meldet `mirrored=0`. Kontrollversuch: Derselbe Row unter altem nacktem Key wird vom selben Pfad erfolgreich entfernt. Der erfüllte Track bleibt andernfalls als Downloadauftrag sichtbar und kann erneut verarbeitet werden; die Wishlist-Batches verwenden `force_download_all=True` (`core/wishlist/processing.py:144`).

**Trigger B / gegenläufige Konsequenz:** Zwei Album-Rows desselben Provider-Tracks existieren; nur einer wird unmonitored. Der Mirror übergibt auch hier den nackten Key an `remove_from_wishlist`. Diese DB-Funktion entfernt absichtlich alle `track_id::%`-Rows (`database/music_database.py:11123–11126`). Die reale Repro legt beide Album-Rows durch getrennte Mirror-Läufe an und setzt nur Track 1 auf unwanted. Anschließend sind **beide** Wishlist-Rows gelöscht, obwohl Track 2 weiter wanted ist. Ein späterer Reconcile kann den zweiten wiederherstellen; die unmittelbare Entfernung ist dennoch falsch.

**Fixrichtung:** Den release-spezifischen Wishlist-Key gemeinsam für Insert, Existenzprüfung und Mirror-Remove ableiten. Den breiten Cleanup für erfolgreiche Recording-Downloads nicht unverändert als Operation für ein einzelnes Release verwenden. Eine bloße Erweiterung der Existenzabfrage um `LIKE` würde Trigger A maskieren und die zu breite Entfernung aus Trigger B beibehalten.

## SYNC-03 — P2 — Outbox verwirft eigenständige Album-Aufträge als überholt

**Reviewstelle:** [core/library2/mirror_outbox.py:233](/home/cyran/Projects/05_Soulsync_fork/core/library2/mirror_outbox.py:233), Zeilen **233–236**.

**Trigger:** Zwei gewünschte Releases enthalten denselben Provider-Track-Key; ein gemeinsamer Monitor-/Reconcile-Lauf stellt beide Adds in die Outbox. Die Wishlist unterstützt ihre getrennten Album-Keys ausdrücklich.

**Fehler und Folge:** `_entity_key` reduziert beide Ops auf `('wishlist', payload.id)`. `_superseded_ids` behandelt dadurch das zweite Album als neuere Absicht für das erste und verwirft dessen Add vor der Persistenz. Das ist von SYNC-02 getrennt: Hier geht schon der Add verloren, auch ohne Datei oder Remove.

**Reale DB-Repro:** Wanted-Track-IDs **1 und 2**, Album-IDs `review-album` / `review-other-album`, identische Provider-Track-ID. Nach `mirror_projected_tracks_wishlist(..., [1, 2])` existiert nur `review-track::review-other-album`; Outbox-Statusfolge **`superseded`, `done`**. Ein separater späterer Mirror des ersten Tracks legt die fehlende Album-Row korrekt an. Der Fehler hängt somit vom gemeinsamen Drain ab und kann durch spätere Läufe zeitweise verdeckt werden.

**Fixrichtung:** Supersession auf dieselbe vollständige Identität wie die Queue ausrichten, einschließlich Album und vorgesehenem Profil-/Provider-Scope. Im vorliegenden Nachweis ist die Album-Kollision bestätigt; zusätzliche Cross-Profile-/Cross-Provider-Kollisionen wurden nicht als separate Befunde behauptet.

## SYNC-04 — P2 — Autolink verwechselt Qualitätsprofil mit Benutzerprofil und lässt die Admin-Projektion aus

**Reviewstelle:** [core/library2/autolink.py:952](/home/cyran/Projects/05_Soulsync_fork/core/library2/autolink.py:952), Zeilen **952–953**. Vom Parent vorgeschlagen, hier durch den echten Autolink-/DB-Pfad unabhängig verifiziert.

**Trigger:** Das globale Standard-Qualitätsprofil hat eine ID ungleich 1. Ein fertiggestellter Download erzeugt über Autolink einen neuen Katalog-Track, für den noch keine Admin-Wanted-Projektion existiert. In der Repro ist das Album unter Benutzerprofil 1 ausdrücklich monitored; der neue MP3-Track benötigt gemäß globalem FLAC-Profil ein Upgrade.

**Fehler:** `recompute_wanted(conn, profile_id=default_quality_profile_id(conn), ...)` übergibt eine ID aus `quality_profiles` als Benutzerprofil-ID. Die Monitor-Regeln und alle regulären Library-v2-Consumer verwenden gemäß `ADMIN_PROFILE_ID` das Benutzerprofil 1. Die Qualitätsprofil-ID gehört in `effective_profile_id` und die Qualitätskaskade; sie ersetzt nicht den Besitzer der Monitoring-Absicht.

**Reale DB-Repro:** Default-Qualitätsprofil **3**, neuer Track **2**. Nach `link_download_into_library_v2` existiert ausschließlich `{profile_id: 3, wanted: 0, effective_profile_id: 3}`. Der Admin-Status meldet **missing=1, consumer_ready=false**; `track_wanted_states(..., profile_id=1)` wirft `wanted projection missing or stale for tracks: 2`. Die echte `list_cutoff_unmet(..., profile_id=1)` liefert **0** statt des upgradebedürftigen Tracks. Nach korrektem `recompute_wanted(..., profile_id=1)` ist derselbe Track wanted und die Cutoff-Liste enthält **1** Eintrag. Für Autolink wurden nur Artwork-Hintergrundarbeit und Audio-Probe ersetzt; Track-/File-Anlage, Regeln, Projektion und Listenabfrage laufen gegen die echte isolierte Datenbank.

**Kompensation und Grenze:** `ensure_wanted_projection` repariert diese Lücke nicht: Es sieht bereits Rows mit aktueller Projektionsversion, ohne deren Admin-Abdeckung zu prüfen (`wanted.py:417–424`). Ein regulärer vollständiger Admin-Reconcile kann sie später schließen. Bei einem *vorher über `materialize_track_intent` materialisierten* Track bleibt dessen bestehende Admin-Projektion erhalten; Autolink legt dann zusätzlich die falsche Profil-3-Row an. Diese Kontrollvariante wurde ebenfalls reproduziert, Profilfolge **[1, 3]**. Es wird daher keine Löschung bereits bestehender Admin-Projektionen behauptet.

**Fixrichtung:** Für den Wanted-Scope `ADMIN_PROFILE_ID` verwenden und die Qualitätsprofilauflösung separat belassen. Den Test [test_autolink.py:576](/home/cyran/Projects/05_Soulsync_fork/tests/library2/test_autolink.py:576) korrigieren: Er schreibt aktuell gerade die falsche Namespace-Kopplung als Sollverhalten fest. Erwartung: Benutzerprofil 1, effektives Qualitätsprofil 2; zusätzlich den Consumer mit Admin-Scope prüfen. Dieser Test erklärt, weshalb die vorhandene Suite den Vertragsfehler akzeptiert.

## Architektur-Stärken

- `core/library2/wanted.py:82–102` beschreibt und implementiert eine klare Priorität expliziter Track-Entscheidungen gegenüber geerbten Regeln. Die Projektion trennt Monitoring-Absicht von der live ausgewerteten Frage, ob tatsächlich etwas heruntergeladen werden muss. Projektionsversion und unveränderte Rows überspringende Upserts erleichtern Diagnose und begrenzen Schreiblast.
- Die transaktionale Outbox macht Monitor-Änderung und Mirror-Absicht gemeinsam haltbar. Fehlversuche bleiben sichtbar/retrybar; die Idee, ältere überholte Ops nicht wiederzubeleben, ist richtig. SYNC-02/03 betreffen ihre konkrete Identitätsgrenze.
- `track_wishlist_payload` vereinheitlicht Qualitätsprofilauflösung und Queue-Eignung für direkten Search-/Mirror-Payload. Der gemeinsame `_run_wishlist_cycle` (`core/wishlist/processing.py:156`) reduziert Drift zwischen manueller und automatischer Verarbeitung.
- Reverse-Sync grenzt explizites Entfernen von erfolgreichem Download-Cleanup ab. Die Adapter schützen den dokumentierten Admin-Scope (`monitor_sync.py:557–560`); der Reconciler unterdrückt Pruning während aktiver Bootstrap-Migration. Das sind sinnvolle Schutzmechanismen für Zustands- und Upgrade-Robustheit.

## Vereinheitlichung und toter Code

- **P3, sicher unerreichbar:** [database/music_database.py:11067](/home/cyran/Projects/05_Soulsync_fork/database/music_database.py:11067), Zeilen **11067–11092**. Der zweite `if existing is not None` dupliziert den vorherigen Update-Pfad; dieser hat bei identischer Bedingung schon in Zeile 11065 zurückgegeben. Zwischen den Bedingungen ändert sich `existing` nicht. Entfernen reduziert widersprüchliche Bool-/Outcome-Verträge. Kein eigener Laufzeitfehler behauptet.
- Einen zentralen, expliziten Wishlist-Identitätstyp bzw. Helper für Provider, Track, Album und Benutzerprofil verwenden. Aktuell unterscheiden sich DB-Composite-Key, nackter Outbox-Key und Descriptor-Auflösung; die drei Repros zeigen die praktischen Folgen.
- `core/wishlist_service.py` ist ein weiterhin genutzter Compatibility-Shim, kein blind löschbarer toter Code. Importstellen schrittweise vereinheitlichen; neue Logik gehört in `core/wishlist/service.py`.
- `monitor_sync`-Adapter heißen/documentieren sich noch als „feature-gated“ und nehmen `config_manager` an, obwohl ihre aktuellen Guards nur Profil/Descriptor prüfen. Dies als API-/Dokumentationsbereinigung behandeln, nicht als unbewiesenen Feature-Gate-Bug. Die Add-Adapter fehlen außerdem im modulweiten `__all__`, werden aber direkt importiert; kein aktueller Ausfall daraus abgeleitet.
- Katalog- und Medienserver-IDs konsequent als getrennte Verträge dokumentieren. In `services/sync_service.py` und `core/library/manual_library_match.py` stehen noch Kommentare, nach denen die Library-ID immer die RatingKey sei. Live-Server-/Fallback-Verhalten wurde hier nicht reproduziert und ist deshalb kein weiterer Befund.

## Abdeckung und Grenzen

Geprüft: relevante Änderungen und Aufrufer in `core/wishlist*`, `core/watchlist*`, `core/sync`, `services/sync_service.py`, `core/library2/{wishlist_mirror,monitor_sync,wanted,wanted_views,mirror_outbox}`, angrenzende Datenbank-/API- und Profil-Payload-Verträge sowie abschließend Autolink/Materialisierung → Wanted. Schwerpunkt: Wanted/Monitoring, Qualität, Queue-Rücknahme, Composite- und Server-ID-Grenzen. Der Bericht ersetzt nicht die übrige Whole-Branch-Prüfung des Parent.

Keine Code-, Test-, Konfigurations- oder Git-Änderungen; nur `sync*`-Berichts-/Repro-Dateien im zugewiesenen Verzeichnis. Kein Graphify, keine Subagenten, keine Live-Datenbanken/-Services. Keine Browser-UI-, Provider-, reale Import-/Datei- oder Serverintegration ausgeführt. Nicht abschließend verifizierte Zwischenhypothesen wurden aus der Befundliste entfernt. Listening-Stats-ID-Split sowie der Compilation-Artist-Befund **INT-03** bleiben ausdrücklich beim Parent und werden nicht doppelt gezählt.

Parent-Nachweis, hier nicht erneut ausgeführt: **18.228 Python-Tests bestanden**, drei als stale/fixture eingeordnete Fehler, davon zwei upstream reproduziert; **7.795 Vitest-Tests bestanden**, **Build und Ruff bestanden**. Die gezielten Repros zeigen Lücken trotz dieser breiten Absicherung: persistiertes Qualitätsprofil nach echtem Quelle→Wishlist-Aufruf, kanonische Composite-Keys bei Erfüllung/Rücknahme und mehrere Album-Intents im selben Outbox-Drain.
