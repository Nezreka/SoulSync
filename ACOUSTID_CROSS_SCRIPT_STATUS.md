# AcoustID Cross-Script / MusicBrainz — Stand 2026-08-25

Branch `library-overhaul`, **7 Commits, nicht gepusht** (`ab90f3a78` … `b72122354`).
Ausgangspunkt: zwei Findings auf dem Produktionsserver, beide Dateien korrekt.

```
Wrong download: "Apetitan" is actually "APETITAN"
Expected "Apetitan" by Sawano Hiroyuki, but audio fingerprint matches
"APETITAN" by 澤野弘之 (fingerprint 100%, title 100%, artist 0%)
```

---

## Die Commits

| Commit | Was |
|---|---|
| `ab90f3a78` | Cross-Script-Signal generisch in `evaluate`; Alias-Write-back; Negativ-Cache abgestellt |
| `7e537b043` | Aliase dürfen nur einer MBID folgen, der der Artist zustimmt |
| `2872ecadb` | Per-Provider-Backfill (`backfill_missing_provider_ids`) + MB ohne Worker durchsuchbar |
| `2bec44c29` | Ein Scan, der nicht bestätigen kann, darf `verified` nicht wegnehmen |
| `33f0fa643` | Scan und Download sind **eine** Pipeline (`verify_audio_file`) |
| `068122754` | Aliase aus der bekannten MBID holen statt nach Namen zu raten |
| `b72122354` | Automatisches Artist-Matching über den eigenen Katalog statt über den Namen |

### 1. Ähnlichkeit über Schriftgrenzen ist keine Evidenz

`core/matching/audio_verification.py` berechnet jetzt pro Dimension
`title_comparable` / `artist_comparable` über `is_cross_script_mismatch`. Eine
unvergleichbare Dimension ist **unbekannt**, nie **durchgefallen**, und kann nie
das sein, worauf ein FAIL beruht. Der `fingerprint >= 0.95`-Boden für
Cross-Script-Titel ist damit weg — er beantwortete die falsche Frage.

Gemessen vorher/nachher (Download-Pfad, gleiche Eingabe):

| | vorher | jetzt |
|---|---|---|
| keine Aliase auflösbar | FAIL | SKIP |
| Aliase vorhanden | PASS | PASS |
| Aliase ohne die Kanji-Form | FAIL | SKIP |

### 2. Eine Pipeline

Der Scanner ruft `AcoustIDVerification.verify_audio_file` auf — den
Einstiegspunkt des Downloads. Verfügbarkeitsprüfung, Lookup, Confidence-Schwelle,
MusicBrainz-Anreicherung titelloser Recordings, Alias-Auflösung, Entscheidung:
einmal. Beim Scanner bleibt nur, was nur er beantworten kann — welche Dateien,
und was mit einem Urteil geschieht.

Der Verifier gibt über das Context-Dict zurück, was sein Lookup gesehen hat
(`_acoustid_recordings`, `_acoustid_best_score`, `_acoustid_recording_mbids`,
`_acoustid_decision`) und nimmt ein optionales `min_score`.

Abgesichert in `tests/test_acoustid_one_pipeline.py`: acht Fälle durch **beide**
Pfade mit Gleichheitsprüfung, plus ein struktureller Test, der fehlschlägt, wenn
im Scanner wieder `evaluate(`, `_resolve_expected_artist_aliases`,
`MIN_ACOUSTID_SCORE` oder `_enrich_recordings_from_musicbrainz` auftaucht.

### 3. Der `verified` → `unverified`-Downgrade

Der Scanner las den Verifizierungsstand aus dem **Datei-Tag** und schrieb in die
**Katalog-Spalte**. Fehlendes Tag ⇒ „ungetaggte Datei" ⇒ deren Regel setzte eine
DB-`verified`-Zeile auf `unverified` und stempelte das Tag gleich mit.

Vor dem Cross-Script-Fix unerreichbar, weil FAIL `verification_status` in Ruhe
lässt (nur `acoustid_status='fail'` → „Mismatch"). Deshalb las die Reihenfolge
sich, als hätte der Fix es verursacht — er hat es freigelegt.

Jetzt: Stand = Tag **oder** Spalte; ein fehlendes Tag wird **geheilt**.

**Lehre für später:** wer ändert, *was* ein Urteil ist, muss alles prüfen, was
jeden einzelnen Urteilswert konsumiert.

### 4. Die Alias-Brücke war nie deterministisch

`lookup_artist_aliases` riet per Live-Namenssuche hinter einem Trust-Gate. Der
Kommentar im Code nennt genau diesen Artist als Verlustfall: für
`Sawano Hiroyuki` führt eine Decoy-Entität auf dem kombinierten Score (0.82,
knapp unter 0.85), während der echte `澤野弘之` mb_score 100 / kombiniert 0.30
hat und zuletzt sortiert wird. Der Notausgang verlangt entscheidende
MB-Relevanzscores — und die sind nicht stabil.

Dazu drei weitere Kippschalter, alle behoben:
* Cache-Key war der **exakte** Namens-String (binär, kein `COLLATE NOCASE`)
* ein *fehlgeschlagener* Lookup wurde als „keine Aliase" gecacht, 30 Tage
  (`_search_and_score_artists` liefert jetzt `None` bei Fehler; echte Antworten
  werden als `{'aliases': [], 'resolved': True}` markiert, alte unmarkierte
  Zeilen bekommen genau einen Retry)
* erfolgreiche Lookups landeten nur im namens-verschlüsselten Cache, nie am
  Artist (`_persist_artist_identity`, abgesichert durch `provider_id_conflict`)

**Und die eigentliche Lösung:** kennt die Katalog-Zeile die MBID, wird nicht mehr
gesucht — `_artist_row_mbid` → `fetch_artist_aliases(mbid)` → persistieren. Eine
Identität statt einer Ähnlichkeit.

### 5. Damit die MBID auch ohne Handarbeit dort landet

* `match_artist` sucht jetzt non-strict nach, wenn strict leer bleibt (#586, war
  nur in `lookup_artist_aliases` gefixt)
* Cross-Script-Kandidaten werden über den **eigenen Katalog** gematcht: MB-Score
  ≥ 90 **und** mindestens ein besessenes Album in den Release-Groups dieser
  Entität. Ohne besessene Alben: kein Match, kein Raten.
* `backfill_missing_provider_ids` füllt Lücken **pro Provider** (vorher galt ein
  Artist mit Spotify-ID als fertig) und zieht aus `lib2_provider_attempts` über
  `worker_queue.next_pending` — derselbe Ledger wie die zwölf Worker, deshalb
  gefahrlos parallel. Läuft als Phase 2 von `native_enrichment_sweep`
  (`backfill_batch_size`, Default 100).
* MusicBrainz ist ohne Worker durchsuchbar (`get_musicbrainz_service()`) und
  immer in `_library_v2_configured_match_services` — vorher fiel es bei
  fehlgeschlagener Worker-Init **still** aus jedem Enrich-Lauf.

---

## Testlage

* volle Suite: **17150 passed**; die 12 Failures des Laufs seriell nachgefahren
  → 111 passed, 1 failed
* verbleibend nur `discovery/test_discovery_endpoints::test_start_sync_happy_path`
  — Baseline, per `git stash` gegen den unveränderten Stand bestätigt
* die übrigen waren Contention (drei parallele pytest-Läufe; die
  `test_torrent_share_limits`-Failures sagen wörtlich `Failed: Timeout`)
* betroffene Bereiche seriell nach dem letzten Commit: **2836 passed**
* ruff sauber

⚠️ Unter `-n 8` rotiert eine Gruppe von Tests nicht-deterministisch
(`test_candidate_store`, `test_import_staging`, `test_tidal_collection_tracks`,
`test_enrich_endpoint`, `test_confirmed_search_route`). Immer seriell
gegenprüfen, bevor man etwas für eine Regression hält.

---

## Offen — hier weitermachen

### Auf dem Server nachschauen

```sql
-- hat der Artist eine MBID? Dann greift die neue deterministische Alias-Stufe
SELECT id, name, musicbrainz_id, aliases FROM lib2_artists WHERE name LIKE '%awano%';

-- was der MB-Worker über ihn denkt; 'not_found' ⇒ 30-Tage-Sperre
SELECT service, status, last_attempted_at FROM lib2_provider_attempts
 WHERE entity_type='artist' AND entity_id=<id>;

-- vergiftete Alias-Cache-Zeilen (empty + confidence 0 + keine MBID)
SELECT entity_name, musicbrainz_id, match_confidence, last_updated, metadata_json
  FROM musicbrainz_cache WHERE entity_type='artist_aliases';
```

### Nicht erledigt

1. **Altlast-Heilung.** Dateien, die der Scanner vor `2bec44c29` fälschlich auf
   `unverified` gesetzt hat, korrigieren sich **nicht** von selbst — der Code
   kann nicht wissen, ob `unverified` von ihm oder aus einem echten Befund
   stammt. Bräuchte einen gezielten Heilungsschritt über die betroffenen Zeilen.
2. **MB-Worker-Durchsatz.** 1 Request/Sekunde, Queue-Reihenfolge Phase außen /
   Entity-Typ innen (alle nie angefassten Artists vor dem ersten Album, alle
   Alben vor dem ersten Track). Auf einem frisch befüllten Server brauchen
   Tracks dadurch Wochen. `retry_days = 30` für `not_found`, ohne UI zum
   Zurücksetzen — nur das Löschen der Ledger-Zeile hilft sofort.
3. **Manueller MB-Lookup läuft in ein Timeout.** `core/musicbrainz_client.py:25-26`
   hat einen **modulglobalen** Rate-Limiter (1 req/s), geteilt mit dem
   Hintergrund-Worker; eine manuelle Suche stellt sich dahinter an und hat
   danach 10 s eigenes HTTP-Timeout — gegen die 10 s des Frontends.
4. **`enrich_native_entity_all_services` schluckt Provider-Fehler** auf
   Debug-Level. Der Backfill schreibt stattdessen `error` in
   `lib2_provider_attempts` (sichtbar, treibt Retry), der Enrich-Walk nicht.
5. **Namensdreher-Dublette** `Sawano Hiroyuki` vs `Hiroyuki Sawano` als zwei
   Artist-Zeilen — siehe `duplicate-artist-name-ordering`. `get_artist_aliases`
   sucht exakt nach Namen, findet die Aliase der anderen Zeile also nicht.
6. **`canonical_artist_id` speist die Alias-Auflösung nicht.** Das „Link alias"
   im UI (§40) und `lib2_artists.aliases` sind getrennte Mechanismen; ein
   UI-Link ändert am AcoustID-Ergebnis nichts. Sinnvoller Zusatz: die
   Alias-Auflösung dem `canonical_artist_id` folgen lassen.

### Zwei Dinge, die *keine* Bugs sind (nicht nochmal untersuchen)

* **„AcoustID hat es verifiziert, aber ich finde den Song manuell nicht auf
  MusicBrainz."** Der Download *sucht* MB gar nicht — er fingerprintet, und
  AcoustID liefert die Recording-MBID direkt. Die Namenssuche scheitert, weil
  MBs kanonischer Artist die Kanji-Form ist (#586).
* **`skip` ist kein Fehler.** Es heißt „geprüft, keine Aussage". Die Datei behält
  ihr `verified` aus dem Download; die Check-Spalte zeigt „Skipped" mit
  Begründung. Wird zu `pass`, sobald die Aliase auflösbar sind.
