# Library V2 — Features, Nutzerentscheidungen und Spezifikationen

Dieses Dokument beschreibt **was** Library V2 fachlich leisten soll, **wie**
sich die Funktionen für den Nutzer verhalten und **warum** die jeweilige
Lösung gewählt wurde. Es enthält keinen Fortschrittstracker. Status,
Commit-Hashes und Teststände stehen ausschließlich in
[library-v2-status.md](library-v2-status.md). Fehlerdiagnosen stehen in
[library-v2-issues.md](library-v2-issues.md), die übergreifenden
Architekturregeln in [library-v2-guide.md](library-v2-guide.md).

---

## 1. Ursprünglicher Phasenplan und Produktform

Der Phasenplan bleibt als Begründung der Produktform erhalten, auch wenn die
konkrete Reihenfolge später durch Audits und Nutzerfeedback angepasst wurde.

### Phase A — Katalog, Look-and-Feel, Artwork und Monitoring

- full-width React/TanStack-Oberfläche unter
  `webui/src/routes/library-v2/`;
- keine globale Suchbox innerhalb der Library-Seite;
- Artists, Albums/Singles und Tracks in Lidarr-artigen Tabellen;
- media-server-unabhängiges Artwork aus Files und Providern;
- Artist-Monitoring über Watchlist, Release-/Track-Monitoring über Wanted und
  Wishlist;
- Refresh & Scan liest reale Files/Tags und aktualisiert den Katalog.

### Phase B — Interactive und Automatic Search

- Suche über alle konfigurierten Quellen in ihrer echten Priorität;
- verständliche Ergebnistabelle mit Source, Titel, Artist, Album, Dauer,
  Quality, Format, Größe, Alter, Verfügbarkeit, Score und Warnungen;
- Interactive Search lässt den Nutzer auswählen;
- Automatic Search wählt serverseitig den besten zulässigen Kandidaten;
- jeder Download läuft durch dieselbe Search-/Retry-/Import-Pipeline.

### Phase C — Retag, Maintenance und Manual Import

- Tag-Preview und explizites Schreiben;
- Metadata-Enrichment, ReplayGain, Lyrics und Verifikation;
- Manual Import durch die gemeinsame Pipeline;
- file- und artist-gescopte Maintenance-Aktionen.

### Phase D — Quality, Editionen und Dateiverwaltung

- app-weite Quality Profiles mit Track→Album→Artist→Playlist→Global-
  Vererbung;
- Single-/Album- und Edition-/Recording-Modell;
- Manage Track Files, Duplicate-Reconcile, Reorganize und Delete;
- sichere Replacement- und Upgrade-Semantik.

### Phase E — Wanted, Auto-Sync und Playlists

- gescopte Suche monitored/wanted Items;
- periodische Upgrades und Discography-Refresh;
- Playlist-Intents ohne globales Wishlist-Bleed;
- globale Missing-/Cutoff-Unmet-Sichten.

### Acquisition-Phasen

Die spätere Acquisition-Schicht ergänzt persistente Requests, Kandidaten,
Grabs, externe Client-Korrelation, Bundle-Inventory, Review und History. Sie
ersetzt nicht die vorhandene Download-/Import-Pipeline. Die genaue
Reuse-Grenze steht im Guide.

---

## 2. Kataloggrundlage und Informationsmodell

Library V2 ist kein hübscher Spiegel, sondern ein rekonstruierbarer Katalog.
Die zentrale Struktur umfasst:

- Artists mit qualifizierten Provider-IDs und Alias-Gruppen;
- Albums als Release Groups;
- konkrete Release Editions mit Provider- und Tracklist-Snapshots;
- Recordings mit harten IDs;
- Tracks als Library-Entities und Release-Track-Verknüpfungen;
- mehrere `lib2_track_files` pro Track mit Primary-Datei und Lifecycle;
- Artist-/Album-/Track-Credits als Junctions statt zerlegter Textstrings;
- Missing Tracks als echte fileless Rows mit Titel und Monitor-Aktion;
- `lib2_monitor_rules` und materialisierte `lib2_wanted_tracks`;
- Metadata-Overrides und Match-Provenienz getrennt von Providerdaten;
- Entity-, File-, Acquisition- und Delete-History.

Der Importer darf vorhandene Discography-Rows claimen, wenn später echte
Files eintreffen. So bleiben Monitoring und Release-Identität erhalten und es
entstehen keine zweiten „owned“ Albums neben Provider-Platzhaltern.

`library_provider_snapshots` speichert Payload, Completeness, Cursor/Page-Count,
Parser-Version, ETag/Version, stabilen Hash und Provenienz. Ein partieller
Snapshot darf nie verschwundene Releases vortäuschen und prunen.

---

## 3. Feature-Spezifikationen

### <a name="feat-artwork"></a> F-01 — Media-server-unabhängiges Artwork

#### Nutzerziel

Cover und Artistbilder müssen auch in einem reinen SoulSync-Setup ohne Plex,
Jellyfin oder Navidrome zuverlässig erscheinen, auswählbar sein und nach einer
Änderung sofort sichtbar werden.

#### Auflösung und Speicherung

- Manuelles Override gewinnt immer.
- Album/Track: Embedded Cover primär, Provider-Cover als Fallback.
- Artist: Provider-Artist-Foto primär, Embedded-Albumcover als Fallback.
- Cache: `<db_dir>/lib2_artwork/`.
- Serve: `/api/library/v2/artwork/<kind>/<id>[?size=thumb]`.
- Thumbnails werden lokal erzeugt; externe URLs sind keine dauerhafte UI-
  Abhängigkeit.

#### Verhalten beim Cover-Pick

Ein Album-Cover-Override aktualisiert Cache und URL-Version. Wenn „Embed into
files“ gewählt ist, schreibt der bestehende Retag-Pfad das Cover auch dann in
Files, wenn Text-Tags unverändert sind. Ein reiner Text-Diff-Fastpath darf die
Cover-Aktualisierung nicht verschlucken. Mutable Artwork-URLs dürfen nicht mit
`immutable` ohne Versionsparameter ausgeliefert werden.

Der Picker zeigt die aktuelle Auswahl, Provider-Kandidaten und eine Paste-URL-
Option. Signierte externe URLs dürfen nicht durch pauschal angehängte Query-
Parameter beschädigt werden.

#### Sicherheit

Remote Artwork wird nicht als beliebiges SSRF-Ziel akzeptiert. Scheme,
Redirect-Ziele, private/loopback Netze, Content-Length, gestreamte Bytezahl und
Bilddimensionen werden begrenzt. Besser sind serverseitig ausgegebene
Candidate-Identifier statt frei eingereichter URLs.

---

### <a name="feat-monitoring"></a> F-02 — Ein Monitoring-Modell

#### Fachliche Bedeutung

- Bookmark am Artist = Watchlist-Mitgliedschaft.
- Release-/Track-Monitoring = konkreter Wanted-Intent.
- Quality Profile = zulässige Qualität und Upgrade-Ziel, nicht Monitoring.
- Ein einzelner Track nimmt den Parent-Artist nicht automatisch in die
  Watchlist auf.
- Ein Download entfernt Monitoring nicht, weil es für spätere Upgrades weiter
  gebraucht werden kann.

#### Artist Settings

Neben einem gebookmarkten Artist öffnet ein Gear die **bestehenden Watchlist
Artist Settings** über denselben API-Vertrag, keine reduzierte Kopie. Enthalten
sind:

- Artist-Quality-Profile;
- Auto-download new releases;
- Albums, EPs, Singles, Live, Remixes, Acoustic, Compilations und
  Instrumentals;
- Lookback/Zeitraum;
- bevorzugter Metadatenprovider;
- aktueller Provider-Match und manuelles Re-Matching;
- Behandlung neu entdeckter Releases.

Aktionen auf bereits bekannte Releases bleiben sprachlich von Regeln für
zukünftige Releases getrennt. Das Bookmark erklärt im Tooltip ausdrücklich
die Watchlist-Wirkung.

#### Synchronisationsvertrag

V2-Intent wird mit derselben Transaktion in die Mirror-Outbox geschrieben.
Der kombinierte Reconciler:

1. drainiert pending Outbox-Operationen;
2. repariert Artist⇄Watchlist, wobei `user_explicit` gewinnt;
3. projiziert Wanted-Tracks in die Wishlist und entfernt nicht mehr Wanted.

Dieser kombinierte Job heißt `monitoring_list_reconcile`; die früheren
internen IDs für Mirror- und Wishlist-Reconcile werden auf ihn migriert.

Ein user-facing Remove schreibt eine neuere, supersedierende Remove-Operation,
damit ein alter pending Add den Eintrag nicht wiederbelebt. Interne Cleanup-
Removes nach erfolgreichem Download sind keine Nutzer-Demonitor-Aktion.

#### `monitor_new_items`

- Erste Expansion monitort nie automatisch den Backkatalog.
- `all` monitort bei Re-Expansion alle neu entdeckten zulässigen Releases.
- `new` monitort nur Releases mit bekanntem Datum nach dem bisherigen
  jüngsten Release.
- Undatierter oder verspätet gelieferter Backkatalog bleibt bei `new`
  unmonitored.
- Content-Type-Filter aus Artist Settings gelten auch im nativen
  Discography-Refresh.

---

### <a name="feat-quality"></a> F-03 — App-weite Quality Profiles und Upgrades

#### Ein Modell, keine Parallelkopie

Library V2 referenziert die app-weite Tabelle `quality_profiles`. Jede
Pipeline-Stufe lädt die Profilwerte live. Wishlist-Rows speichern nur den
Pointer, nicht eingefrorene Kopien von AcoustID-, Fallback- oder Downsample-
Flags.

#### Vererbung und Herkunft

Priorität: Track → Album → Artist → Playlist → Global. Das API-Modell liefert
mindestens:

```text
effective_profile = {
  id,
  source: track | album | artist | playlist | global,
  source_id,
  explicit
}
```

Ein Picker kann „Use inherited profile“ wählen. Parent-Änderungen berechnen
geerbte Kinder neu, ohne explizite Overrides zu löschen. Eine Profil-Löschung
entfernt das Explicit-Flag und lässt den normalen Fallback neu greifen.

#### Profilbewertung

Die serverseitige Bewertung berücksichtigt Ranked Targets,
`upgrade_policy`, `acceptable`, `until_cutoff`, `until_top`, Cutoff-Index,
Fallback, Bit-Tiefe, Sample-Rate und source-seitig verfügbare Fakten. Fehlende
Fakten dürfen einen Kandidaten nicht fälschlich ablehnen; Hi-Res-Ziele
benötigen positive Evidenz.

#### Upgrade-Semantik

- Existing Files werden nur ersetzt, wenn das Profil Upgrades erlaubt und
  der Kandidat serverseitig besser ist.
- Kein besserer zulässiger Kandidat bedeutet keine Mutation.
- Das alte File bleibt bis nach Quality, AcoustID, Tagging und erfolgreichem
  Import bestehen.
- Nach Erfolg wird nur die alte Datei derselben Track-Entity entfernt.
- Review- und Automatic-Modus benutzen denselben Evaluator; sie unterscheiden
  sich nur darin, ob ein Finding oder ein Wanted-/Search-Intent erzeugt wird.

Playlist-Quality-Konflikte werden nicht unsichtbar aufgelöst. Bei zwei
gleichrangigen, unterschiedlichen Playlist-Profilen zeigt die UI einen
Konflikt und verlangt eine explizite Wahl.

---

### <a name="feat-discography"></a> F-04 — Discography, Tracklists und Discovery

`expand_artist_discography` holt den vollständigen Providerkatalog über die
geteilten Metadata-Adapter. Releases werden per qualifizierter Provider-ID,
normalisiertem Titel und passendem Release-Bucket gematcht. Ein Album und eine
Single mit gleichem Titel dürfen nie bucket-übergreifend zusammenfallen oder
ihre Provider-ID überschreiben.

Die Artist-Ansicht bietet:

- „My Library“ und „All Releases“;
- Albums, EPs und Singles in getrennten Abschnitten;
- Monitor all / Unmonitor all als beobachtbare Background-Jobs;
- Update Discography;
- „not in library“-Kennzeichnung;
- automatisches Laden, wenn „All Releases“ Startzustand ist.

Beim Monitoring eines unowned Release wird zuerst dessen Tracklist
materialisiert. Provider-Tracklist-Einträge sind echte fileless Track-Rows,
nicht bloße Zähler. Tracklist-Snapshots sind an die Default-Edition und deren
IDs gebunden; ein Editions-/Providerwechsel invalidiert den alten Cache.

Der periodische Refresh berücksichtigt auch Artists, die noch nie manuell
expanded wurden. Filter für Live, Remix, Acoustic, Compilation und
Instrumental werden aus denselben Artist Settings gelesen wie im
Watchlist-Pfad.

---

### <a name="feat-bootstrap"></a> F-05 — Automatischer Initialimport

Eine bestehende Installation darf nicht erst durch Öffnen der UI in V2
materialisiert werden. Beim aktivierten Katalog startet serverseitig ein
idempotenter Initialimport.

#### Persistenter Laufvertrag

`lib2_bootstrap_state` hält Run-/Owner-Token, Phase, Zähler, Attempts,
Heartbeat, Fehler und Quell-Watermark. Ein Claim ist owner-gefenced; ein alter
Owner darf nach stale Reclaim weder `done` noch `failed` des neuen Runs
schreiben.

Ein leeres Fresh Install wird nicht für immer als erledigt markiert. `done`
ist an eine Quell-Watermark gebunden und wird neu bewertet, wenn später
Legacy-/Media-Daten erscheinen.

Der Import:

- liest nur benötigte Legacy-Spalten;
- streamt in begrenzten Batches statt `fetchall()` großer Text-/Lyrics-Rows;
- committet restart-sichere Batches, damit SQLite nicht minutenlang einen
  globalen Write-Lock hält;
- aktualisiert Heartbeats sichtbar außerhalb der Arbeitstransaktion;
- teilt Claim und Progress mit dem manuellen Import-Endpoint;
- ist bei Wiederholung idempotent und reconciliert Pfad-/Löschänderungen.

Die UI pollt ohne künstliches Zehn-Minuten-Ende, kann nach Reload reattachen
und zeigt Serverphase, Zähler, Prozent und terminalen Fehler wahrheitsgemäß.

---

### <a name="feat-alias"></a> F-06 — Artist Alias Registry und Aktions-Scope

Aliases verbinden verschiedene Artist-Zeilen zu einer kanonischen Gruppe,
ohne die eigentliche Provider-Identität zu verlieren. Suche nach Aliasnamen
findet den Canonical Artist; Listenstatistiken, Releases, Tracks und Bytes
werden über die Gruppe aggregiert.

Alle artist-weiten Aktionen benutzen denselben Alias-Resolver wie die
Anzeige:

- Refresh & Scan;
- Retag und Reorganize;
- Bulk-Monitoring;
- Wanted und Quality;
- Duplicates/Manage Files;
- History.

Bewusst engere Delete-Semantik muss in Preview und UI sichtbar benannt sein;
sie darf nicht still einen kleineren Scope als die sichtbare Seite verwenden.
Alias-Heuristiken dürfen nicht automatisch Artists nur aufgrund schwacher
Namensähnlichkeit zusammenführen.

---

### <a name="feat-duplicate"></a> F-07 — Provider-Divergenz und Duplicate-Reconcile

Der reale „Hiroyuki Sawano“-Fall zeigte vier gestapelte Ursachen:

1. dieselbe Release Group erscheint bei Providern unter übersetztem und
   originalem Titel;
2. Titelmatching ist für CJK, Featured-Suffixe und OST-Varianten zu schwach;
3. Deezer-/iTunes-IDs wurden in Spotify-Felder geschrieben;
4. doppelte Artist-Rows multiplizieren Album-Duplikate.

Die Reparatur folgt fünf Stufen:

1. **Matching-Härtung:** qualifizierte IDs, Release-Bucket und Unicode-
   Normalisierung; keine stille ID-Überschreibung.
2. **Alternative Releases als Editionen:** verschiedene Provider-Releases
   werden nicht sofort zu verschiedenen Albums.
3. **Release-Group-Reconcile:** MusicBrainz/harte IDs und vorsichtige
   natürliche Schlüssel führen sichere Gruppen zusammen.
4. **Artist-Dedup:** starke Provider-IDs verhindern und reparieren doppelte
   Artists; Konflikte bleiben Review.
5. **Namespace-Sanierung:** falsch einsortierte historische IDs werden in
   den tatsächlichen Provider-Namespace verschoben.

Nach Artist-Merge werden Albumgruppen erneut gefaltet. `dedup_title_key`
entfernt harmlose Featured-Annotationen, darf aber Live/Remix/Remaster oder
numerisch verschiedene Releases nicht verschmelzen. Ein Dry Run zeigt
Survivor/Duplicate-Paare; produktive Datenreparatur verlangt Backup und
explizite Freigabe.

---

### <a name="feat-unmapped"></a> F-08 — V2-native und Collaboration Artists

Ein V2-only Artist ohne Legacy-Rückreferenz muss enrichbar, matchbar,
suchbar, reparierbar und in der globalen Suche als „In Your Library“
erkennbar sein.

Artist-Credits werden strukturiert gesplittet. `feat.`, `ft.`, `featuring`,
`with` und `w/` sind explizite Collaboration-Separatoren; ein allgemeiner
Slash darf `Odetari w/ 9lives` nicht in `Odetari w` zerlegen.

Unbekannte mehrdeutige Band-Credits erzeugen keine Phantom-Artists. Starke
Provider-IDs werden vor Namen ausgewertet; ein Fallback-Provider wird mit
seiner tatsächlichen Herkunft gespeichert. Neue Component Artists starten
unmonitored, sofern keine Watchlist oder ausdrückliche Regel anderes
begründet.

Manual Match zeigt Current Match und Kandidaten als verständliche Karten mit
Bild, Provider, ID/Copy, Genres, Follower/Popularity soweit vorhanden,
Provenienz (`automatic`, `manual`, `legacy`) und Release-Kontext. Ein manuell
bestätigter Match bleibt gegenüber späteren automatischen Writes sticky.

---

### <a name="feat-playlists"></a> F-09 — Playlist-Intents und Profilkonflikte

„Run Pipeline“ für eine Playlist darf in der Wishlist-Phase ausschließlich
die tatsächlich verarbeiteten Playlist-Tracks und deren User-Profile
dispatchen. Wenn keine Trackidentität ermittelt werden kann, gilt fail closed;
die globale Wishlist ist nie ein Fallback.

Albumidentität bleibt Teil des Scopes. `track::album-a` darf nicht als
`track::album-b` dispatcht werden. Bei mehreren User-Profilen wird jeder Track
unter seinem eigenen `profile_id` gebucht und ausgeführt.

Ein bestätigter Playlist-Intent benutzt denselben Early-Materialization-
Resolver wie Search, Watchlist und manuelle Wishlist. Ein Playlist-Quality-
Profile ist nur Default für unentschiedene Tracks. Unterschiedliche
Playlist-Defaults auf derselben Entity erzeugen eine Konflikt-UI.

---

### <a name="feat-history"></a> F-10 — Korrelierte Pipeline-History

History ist keine Downloadliste, sondern erklärt den gesamten Versuch:

```text
search_requested
→ candidates_evaluated
→ candidate_selected / manual_grab
→ quality_checked
→ acoustic_id_checked
→ download_started / download_finished
→ quarantined [optional]
→ human_verified / rejected / retried [optional]
→ imported
→ previous_file_replaced [upgrade only]
```

Jeder Schritt trägt Zeitpunkt, Entity-/File-Scope, Actor, Source/Kandidat,
Entscheidung, strukturierten Grund und relevante Vorher-/Nachher-Quality.
Nicht ausgeführte Checks sind `not_run` oder `skipped` mit Grund, nicht bloß
fehlende Daten.

`core/library2/history_feed.py` vereinigt:

- `acquisition_history`;
- `lib2_entity_history`;
- `lib2_file_delete_operations` und Items;
- `lib2_manual_skips`;
- Legacy `track_downloads` als Fallback.

Der Feed ist nach Artist, Album, Track und File filterbar. Fehlgeschlagene
Versuche bleiben sichtbar, auch wenn nie eine neue File-Zeile entstand.
File-Zeilen speichern zusätzlich das kompakte Pipeline-Ergebnis inklusive
Quality-Profil/Fallback, AcoustID-Grund und Verification-State.

---

### <a name="feat-playback"></a> F-11 — Track Playback / Preview

Die Track-Tabelle verwendet den bestehenden Player über die Shell-Bridge;
kein zweiter Player wird gebaut. IDs sind typisiert:

- `lib2_track_id` für Katalogidentität;
- `legacy_track_id` für Legacy-Verknüpfung;
- `server_track_id` für Media-Server-Streaming.

V2-only Files werden über den V2-Pfadresolver abgespielt. Eine lokale V2-ID
darf nie blind im Legacy-Feld `id` landen, da sonst ein zufällig gleich
nummerierter Track abgespielt oder protokolliert werden kann.

---

### <a name="feat-acq-review"></a> F-12 — Acquisition Review / manuelle Bundle-Zuordnung

Das Backend besitzt Requests, Grabs, Imports, Blocklist, Path Health,
Correlation Coverage und `/acquisition/imports/<id>/resolve`. Das Feature ist
erst als Produkt vollständig, wenn diese Fähigkeiten im WebUI nutzbar sind.

#### Nutzerfall

Ein Soulseek-/Bundle-Grab enthält mehrere Dateien, die nicht eindeutig den
erwarteten Edition-Tracks zugeordnet werden können. Der Import wartet auf
`assignments`; ohne UI wäre er permanent blockiert.

#### Oberfläche

Die Review-Ansicht zeigt:

- Request, Edition und erwartete Tracklist;
- inventarisierte Dateien mit Pfad, Dauer, Disc/Track, Quality und Tags;
- automatische Matches, Confidence und Konfliktgründe;
- unzugeordnete bzw. mehrfach beanspruchte Files/Tracks;
- manuelle Zuordnung per klarer Track-Auswahl;
- Validation vor Submit (jede Datei/Track-Kardinalität, Duplicate-Guard);
- Rescan, Resume und Resolve mit nachvollziehbarer Wirkung;
- History und den nächsten Pipeline-Zustand.

Nach Resolve läuft derselbe Import-Dispatcher unter einem per-import Claim.
Die UI benötigt ein Browser-E2E vom mehrdeutigen Bundle bis zum
abgeschlossenen Import sowie Restart/Resume-Abdeckung.

---

### <a name="feat-search"></a> F-13 — Scoped Search, Manual Grab und Acquisition

#### Automatic Search

- Artist-/Album-Scope sucht nur wanted Missing- und zulässige Upgrade-Tracks.
- Direkter Track-Scope ist einmaliger Nutzer-Intent und darf auch unmonitored
  laufen, ohne Monitorstatus oder persistente Wishlist zu ändern.
- Scope-Auflösung und Candidate-Wahl sind serverseitig.
- Es gibt keinen clientseitigen „best pick“-Algorithmus.
- Ein gescopeter Fehler fällt nie auf globale Wishlist-Verarbeitung zurück.

#### Interactive Search

Interactive Search zeigt Source-Familie, Titel, Artist, Quality, Größe,
Alter, Availability und Profile-Hints. Der Nutzer wählt, aber die Datei läuft
danach durch dieselbe Pipeline. Die UI entscheidet Quality nicht selbst.

Kandidaten außerhalb des Profils werden mit rotem Fail-Grund gezeigt und nur
nach einer separaten, expliziten Force-Bestätigung dispatcht. „Skip AcoustID“
und „Force Quality“ sind benannte, auditierte Overrides; sie gelten nur für
den konkret bestätigten Check.

#### Early Materialization

Sobald ein Search-/Wishlist-/Playlist-/Watchlist-Intent verbindlich
geschrieben wird, materialisiert der Server vor Search/Download idempotent:

1. Artist über stabile Provider-ID;
2. Release/Edition;
3. Track und Credits;
4. explizites/effektives Quality Profile;
5. Correlation-ID und Intent.

Ein unverbindlicher Klick auf ein Suchresultat materialisiert nichts. Die
frühe Entity stellt sicher, dass Quality-Fail, Quarantäne und Not Found an
einem sichtbaren Track hängen.

#### Retry und Blocklisting

Ein Candidate wird erst nach erfolgreicher Preparation als verbraucht
markiert. Ein File-Level-Fail blocklistet präzise Source/Indexer/GUID plus
Reason und wechselt zum nächsten Candidate derselben oder nachrangigen Source.
Retry-State überlebt einen Neustart. Album-Grabs werden zweiphasig vorbereitet
und erst dispatcht, wenn alle Tracks vorbereitet sind; kein Teilstart darf
anschließend als „nichts gestartet“ gemeldet werden.

#### Persistentes Acquisition-Modell

Die Orchestrierung hält ihre Zustände restart-sicher und korrelierbar:

- **Request:** Entity-/Edition-Scope, Actor, effektives Profil und Intent;
- **Candidate/Decision Run:** normalisierte Source-Fakten, Reason-Codes,
  Rejection/Override und Engine-Version;
- **Grab:** Client-ID, Dedupe-Key, Downloadstatus und Adoption;
- **Import:** erwartete Edition, File-Inventar, automatische bzw. manuelle
  Assignments und per-File-Ergebnis;
- **History:** append-only Events von Search bis finalem Import/Fail;
- **Blocklist:** Source/Indexer/GUID, Reason, Expiry und Audit;
- **Recovery:** Quarantäne-/Staging-Journal und Resume-Kontext.

Read-only Diagnosegrenzen wie `/acquisition/path-health` und
`/acquisition/correlation-coverage` erklären Mapping-/Client-Probleme, ohne
reale Serverpfade oder Secrets offenzulegen.

Der externe Client ist die Live-Queue. Nach einem Restart adoptiert der
Monitor vorhandene Jobs über Client-ID und nur bei Eindeutigkeit über Titel-
Fallback. Ein `legacy_dispatched`-Zustand ist kein Beweis für laufenden
Download; Runtime-, Client-, Quarantäne-, Import- und File-Evidenz werden
reconciliert.

Ein Import wird erst `completed`, nachdem jede zugeordnete Datei die gemeinsame
Pipeline erfolgreich beendet und der Katalog den realen finalen Pfad kennt.
Unklare Assignments werden `needs_review` statt willkürlich gematcht. Resume
und periodischer Monitor teilen denselben Import-Claim.

#### Manual Import

Ein vom Nutzer ausdrücklich bestätigter Manual Import ist ein eigener Intent
und darf nicht nachträglich von einem automatischen Quality-Veto so behandelt
werden, als hätte der Nutzer keine Auswahl getroffen. Integrity, Pfadsicherheit
und die übrigen nicht überschriebenen Checks bleiben aktiv; Override und Actor
werden historisiert. Auch Manual Import führt durch denselben File-
Processing-, Autolink- und History-Vertrag.

---

### <a name="feat-files"></a> F-14 — Manage Track Files, Delete, Reorganize und Replacement

#### Manage Track Files

Der kanonische Dialog hat zwei Sichten:

- Duplicates/Canonical Links für Single↔Album- und Edition-Beziehungen;
- alle Track Files mit relativem Pfad, Größe, Quality, Datum, Primary- und
  Lifecycle-State.

Checkboxen erlauben Monitor/Unmonitor, Profil, ReplayGain, Write Tags und
Delete. Ein File-Move trägt stabile `lib2_file_id`; mehrere Files werden nicht
versehentlich auf denselben Pfad kollabiert.

#### Delete

Album-/Artist-Shortcuts und „Manage Tracks → Delete Selected“ öffnen dieselbe
Komponente und denselben Backend-Vertrag:

- **nur aus Library entfernen** — Disk bleibt unangetastet;
- **permanent löschen** — Files plus Library-Einträge nach destruktiver
  Bestätigung.

Die Vorschau zeigt Track-/File-Anzahl, Gesamtgröße und gruppierte, mittig
gekürzte Pfade mit Tooltip/Copy/Reveal. DB-only bleibt auch bei unsicheren
Pfaden möglich. Permanent Delete ist fail closed und journalisiert Actor,
File-IDs, Pfade und reales Ergebnis.

#### Reorganize

Preview und Apply verwenden die bestehende Planner-/Queue-Engine. Album- und
Artist-Scope zeigen Queue-Status, Fehler, Cancel und Clear. Nach einem Move
werden Legacy- und V2-Pfad derselben konkreten File-ID atomar/restart-sicher
aktualisiert; `.lrc`-Sidecars bewegen sich mit. Single-Disc und Multi-Disc
behalten korrekte Disc-/Track-Nummern.

#### Replacement

Die alte Datei bleibt bis nach verifiziertem Import. Bei Erfolg wird sie
sofort entfernt, sofern kein Recycle-Bin-Vertrag konfiguriert ist. Kürzere
Preview-/Null-Header-Dateien dürfen nie eine vollständige Datei ersetzen.

---

### <a name="feat-metadata"></a> F-15 — Metadata, Retag, Features und Matching

#### Refresh & Scan

Liest reale Files über `probe_audio_quality` und Tag-Cache. Aktualisiert
Sample Rate, Bit Depth, Bitrate, Format, Größe, Quality Tier, ReplayGain,
Lyrics und Verification. Fehlende Pfade werden über Root Health gestuft, nie
blind als deleted behandelt. Der Lauf ist beobachtbarer Background-Job mit
echtem Fehlerzustand.

#### Retag und Edit

- Preview zeigt nur vorhandene Files und gruppiert nach Album.
- Datumsformat- und Genre-Substring-Unterschiede erzeugen keine falschen
  Änderungen.
- Write Tags berührt nur betroffene Files.
- Rich Edit umfasst BPM, Style, Mood, Label und Explicit.
- Bulk Edit schreibt nur fachlich gemeinsam sinnvolle Felder.
- Track-Detail verwendet Pencil/Settings, weil es editierbar ist.

#### ReplayGain und Lyrics

RG/LR-Badges werden immer gezeigt: grün vorhanden, grau fehlend, pending mit
Spinner. Klick auf fehlendes RG startet track-/album-gescopte Analyse; Klick
auf Lyrics öffnet oder fetches über die bestehende Lyrics-Implementierung.
`lyrics`, `unsyncedlyrics` und `.lrc`-Sidecars führen nicht zu
widersprüchlichen Anzeigen.

#### Enrich und Manual Match

Artist-Enrich lädt Artistdaten, Album-Enrich Album plus Tracks. Track-Enrich
bleibt platzsparend über Detail-/Provider-UI erreichbar statt als weiterer
Button in jeder Row. Bei common Titles verlangt automatisches Matching Artist-
und Albumkontext sowie eine Ambiguitätsmarge. CJK-/Unicode-Titel bleiben
erhalten. Enrich aktualisiert auch bei bereits vorhandener Provider-ID die
dokumentierten Felder statt nur ID/Artwork.

---

### <a name="feat-wanted"></a> F-16 — Wanted Views, Queue-Sichtbarkeit und Speicherbedarf

- Globale Views „Missing“ und „Cutoff Unmet“ zeigen den gesamten Katalog und
  bieten gescopte Search-/Manual-Grab-Aktionen.
- Laufende Grabs erscheinen direkt an Album- und Track-Zeilen.
- Eine Artist-Seite pollt eine gemeinsame artist-scoped Statusmap, nicht pro
  Albumblock alle drei Sekunden einen eigenen Endpoint.
- Album-Rollups vermeiden N+1-Abfragen und malformed IDs dürfen den gesamten
  Queue-Endpoint nicht brechen.
- Artist und Album zeigen belegten Speicherplatz, aber nicht zwingend den
  absoluten Root-Pfad.
- `missing_suspected` wird amber als „checking missing“ sichtbar; erst
  `missing_confirmed` gilt als Missing/Wanted-Auslöser.

---

## 4. UI/UX-Anforderungen und festgehaltene Entscheidungen

### <a name="ui-icons"></a> UI-01 — Icons und Nomenklatur

- **Automatic Search:** Lupe.
- **Interactive Search:** Menschen-/User-Icon.
- **Quality Profile:** Stern; diese Entscheidung nicht erneut ändern.
- **Table Options:** Zahnrad.
- **Track Detail/Edit:** Pencil oder Settings, nicht reines Info-Icon.
- Jeder Button vermittelt genau einen Intent; „Grab and Download“-Mischlabels
  werden vermieden.

### UI-02 — Visuelle Hierarchie

- Quality Profile ist vor Expand sichtbar und zeigt seine Herkunft.
- Artist Settings zeigen das Artist-Profil explizit.
- Unmonitored, One Release und Quality erscheinen als klare Badges.
- Quality bleibt im Format `FLAC · 24bit/96kHz`, Bitrate getrennt; keine
  zusätzliche Quality-Profile-Spalte.
- Match-Chips zeigen standardmäßig nur konfigurierte Provider.

### <a name="ui-columns"></a> UI-03 — Konfigurierbare Tabellen

Ein richtiges Options-Modal persistiert Einstellungen pro Profil:

- Spalten: #, Disc, Artists, Match, Quality, Features, Metadata, Duration,
  BPM, File, Format/Bitrate;
- sichtbare Match-Provider;
- Feature-Badges;
- Artist-Tabellenfelder Quality, Genres und Added.

Track-Tabellen sind clientseitig sortierbar. Resizable Columns wurden bewusst
aufgeschoben; eine spätere Umsetzung braucht Pointer-Capture, Tastaturzugang,
Min/Max-Breiten, Doppelklick-Reset und persistierte Breiten.

### <a name="ui-bulk"></a> UI-04 — Bulk-Aktionen

Checkbox-Selektion und Bulk-Bar bieten:

- Monitor/Unmonitor;
- Quality Profile bzw. „inherit“;
- ReplayGain;
- Write Tags;
- Delete Files Preview;
- Bulk Edit für Style, Mood, BPM und Explicit.

Eine gemeinsame Tracknummer für viele verschiedene Tracks wird nicht als
Bulk-Feld angeboten.

### UI-05 — Entrümpelte Actions

Albumzeilen zeigen nur Automatic Search, Interactive Search und Overflow;
der Titel führt zur Detailseite. Retag, Reorganize, Cover, Enrich und Delete
liegen im Overflow und im Detail-Header.

Die Artist-Toolbar trennt:

- primär: Refresh & Scan, scoped Automatic Search, Interactive Search,
  Update Discography;
- Files/Tools: Retag, Reorganize, Maintenance, Manual Import, Enrich;
- Entity: Manage Tracks, History, Settings, Edit, Delete.

Globale Search-/Upgrade-Aktionen gehören auf Library/Wanted, nicht in einen
Artist-Kontext.

### UI-06 — Search-Ergebnisse

Source-Badges unterscheiden Usenet, Torrent, Streaming und P2P. Availability
bedeutet je Source Grabs, Seeders oder Slots/Queue. Age hat lesbares Label und
Rohdatum im Tooltip. Alle Spalten sind sortierbar; Default ist Quality mit
Size-Tiebreak.

Eine optionale „Only show results meeting cutoff“-Filterung darf Resultate
ohne ausreichende Quality-Fakten nicht fälschlich verstecken.

### UI-07 — Track-Detail und Pipeline

Tabs:

- Info/Pipeline mit chronologischem Stepper;
- Tags im lesbaren, gruppierten Quarantäne-Inspect-Format;
- Quality mit effektivem Profil, Herkunft und Override/Inheritance;
- Metadata und Lyrics editierbar;
- Source/Provenance mit Service, User, File, Quality und History.

Der Metadata-Status zeigt nicht „all expected tags present“ für einen Track,
der gar kein File besitzt. Ein kompakter Quality-/Feature-Badge darf Details
im Tooltip bündeln, muss Missing, ungeprüft, externes Cover und echte
File-Tags aber auseinanderhalten.

### UI-08 — Zugriffsrechte

Library V2 erbt mindestens das bestehende `library`-Recht oder besitzt einen
migrierten eigenen Page-Key. `allowed_pages` darf nicht clientseitig
übergangen werden. Sensitive File-/History-Reads werden serverseitig
autorisiert.

---

## 5. Repair- und Tool-Integration

### 5.1 Verbindlicher Tool-Vertrag

Jedes registrierte Tool deklariert:

- Datenbasis: `lib2` oder `filesystem`;
- Effekte: `observe`, `metadata`, `tags`, `artwork`, `path`, `new_file`,
  `delete`, `wanted`, `discography` oder `none`.

Native Mutation folgt dieser Kette:

```text
Finding/Live-Fix
→ stabile V2-Subjects
→ native Mutation
→ nur betroffene Files neu scannen
→ Artwork gegebenenfalls invalidieren
→ Wanted nach New/Delete neu berechnen
→ Entity-/File-History schreiben
```

Ein Fixfehler darf kein Erfolgsevent erzeugen. File-semantische Findings
verwenden File-IDs und Fingerprints; Track-semantische Findings verwenden die
Primary-Datei bzw. Track-ID.

### 5.2 Tool-Matrix und Zielsemantik

| Tool | Verbindliche V2-Semantik |
|---|---|
| Track Number Repair | vollständige Edition als Soll; konkrete Files als Subjects; Disc-/Track-Writes in V2 und Compatibility-Grenze |
| Cache Maintenance | rein operativ, keine Musikentity-Mutation |
| Orphan File Detector | Legacy-, V2- und Filesystem-Index berücksichtigen; Root-Health-Guard; echte Orphans bleiben reviewbar |
| Dead File Cleaner | File-State und expliziten remove/redownload-Intent bewahren; Wanted passend neu berechnen |
| Duplicate Detector | durch native Dedup-/Edition-Reparatur ersetzt, keine zweite Finding-Maschine |
| AcoustID Scanner | aktive V2-Primary-Files; Verification nativ persistieren |
| Cover Art Filler | V2-Albums/Artists über gemeinsamen Resolver |
| Lyrics Filler | V2-Files, embedded und LRC; Rescan und History |
| ReplayGain Filler | V2-Files; Tag-Cache über File-ID aktualisieren |
| Empty Folder Cleaner | operativ; Quarantäne- und Root-Schutz |
| Expired Download Cleaner | Delete immer durch zentrale V2-Lifecycle-Grenze; Retention als sichtbarer Vertrag |
| Metadata Gap Filler | native Track-Metadaten plus gezielter Rescan |
| Album Completeness | native Tracklist/Placeholder plus Wanted Views |
| Fake Lossless | katalogisierte und lose Files beobachten; kein automatischer destruktiver Fix |
| Quality Review/Upgrade | ein Evaluator mit `review` und `automatic`, identische Cutoff-Semantik |
| Library Reorganize | bestehende Planner-/Queue-Engine; Review/Apply; V2-Pfad atomar synchronisieren |
| MBID/Canonical/Dedup | native Edition-/Namespace-/Dedup-Reparatur |
| Lossy Converter | neues File registrieren; ersetztes Original lifecycle-sicher löschen |
| Album Tag Consistency | Album→Track→File nativ, Rescan und History |
| Live/Commentary Cleaner | reviewpflichtig; Delete synchronisiert V2/Wanted |
| Fix Unknown Artists | native Enrich-/Smart-Split-/Manual-Match-Maschine |
| Discography Backfill | nativer Discography Refresh, Monitor-Regeln und Wanted Views |
| Library Re-tag | native Preview/Write-Komponente, keine zweite Joblogik |
| Preview Clip Cleanup | Decoded Duration; Delete/Rewishlist über Lifecycle |
| Corrupt File Detector | Root-/Path-sicherer Decode-Test auf V2- und Filesystem-Subjects |
| Skip Audit Cleanup | ausschließlich abgelaufene manuelle Skip-Zeilen |
| Monitoring List Reconcile | Outbox, Artist⇄Watchlist und Wanted⇄Wishlist in einem neutralen Job |

Lose/unindexierte Files dürfen durch den nativen Cutover nicht Fake-Lossless,
Converter, Tracknummer, ReplayGain oder Corruption verlieren. Wo Cutoff ohne
Katalogkontext nicht bewertbar ist, zeigt der Scanner gemessene Fakten und
verlangt zuerst Import/Zuordnung.

### 5.3 Tool-Acceptance

Für mutierende Tools werden mindestens geprüft:

1. deaktivierter Katalog führt nicht zu still falschem „clean“;
2. gemapptes V2-Subject trägt stabile V2-/File-ID;
3. Tag/Metadata/Verification aktualisieren den Snapshot;
4. Artwork invalidiert den richtigen Entity-Cache;
5. Move aktualisiert Disk, V2-Pfad und History;
6. New File erzeugt File-Row und Scan;
7. Delete/Replacement setzt Lifecycle und Wanted korrekt;
8. V2-only Subject wird ohne Legacy-Backref gefunden;
9. Fixfehler erzeugt keinen V2-Erfolg;
10. Integration-Fehler bleibt als Retry-/Diagnoseanker sichtbar.

---

## 6. Angenommene und abgelehnte Paritätsentscheidungen

| Element | Produktentscheidung |
|---|---|
| Track Playback | übernehmen; bestehenden Player wiederverwenden |
| Artist Top Tracks | nicht übernehmen |
| Discography Batch Download | nicht übernehmen |
| Track Redownload Modal | zurückgestellt; nur sichere Replacement-Semantik zulässig |
| Smart Delete | übernehmen als gemeinsamer DB-only/permanent Dialog |
| A-Z/Source-Header | nicht übernehmen; Textsuche/Paging genügen |
| Inline Table Edit | nicht übernehmen; Modale sind sicherer |
| Bulk Selection/Edit | übernehmen |
| Non-admin Report | nicht übernehmen; V2 ist admin-gesteuert |
| Watch All Unwatched | nicht übernehmen |
| Raw Artist Inspector | nicht übernehmen |
| Export/M3U | zurückgestellt |
| Reorganize Queue Panel | übernehmen |
| Add Artist | nicht übernehmen; Search/Watchlist sind der Eingang |
| Wanted Missing/Cutoff Views | übernehmen |
| Artist Mass Editor | nicht übernehmen |
| Metadata Profile | nicht übernehmen |
| Calendar | nicht übernehmen |
| Queue at Entity | übernehmen |
| Blocklist UI | nicht übernehmen |
| Diskspace | übernehmen; absolute Pfade nicht erforderlich |
| Unmapped Files UI | nicht übernehmen; Repair-Diagnose genügt |
| Search on Monitor | nicht übernehmen |

Diese Tabelle ist eine Produktentscheidung, kein Status. Ob angenommene
Elemente bereits geliefert sind, steht nur in der Statusdatei.
