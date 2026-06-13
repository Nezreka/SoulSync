# Plan: Global Quality System — `feature/global-quality-system`

## Was bereits implementiert ist (dieser Commit)

### 1. `core/quality/model.py` — Das Herzstück
Source-agnostisches Datenmodell. **Jede** Quelle mappt ihre Ergebnisse auf `AudioQuality`, der gleiche Ranker läuft für alle.

```
AudioQuality(format, bitrate, sample_rate, bit_depth)
QualityTarget(format, bit_depth, min_sample_rate, min_bitrate, label)
filter_and_rank(candidates, targets)  ← eine Funktion für alle Quellen
```

### 2. Soulseek — echte Werte statt Heuristik
slskd `attributes` werden jetzt ausgelesen:
- `type 4` = sample_rate (Hz)
- `type 5` = bit_depth (bits)

Vorher: kbps-Schwellwert-Hack (1450 kbps = "wahrscheinlich 24-bit"). Jetzt: echte Werte direkt aus dem Protokoll.

### 3. Quality Profile v3
Statt `qualities: {flac: {enabled, priority, bit_depth}}` jetzt eine **geordnete Liste**:
```json
"ranked_targets": [
  {"label": "FLAC 24-bit/192kHz", "format": "flac", "bit_depth": 24, "min_sample_rate": 192000},
  {"label": "FLAC 24-bit/96kHz",  "format": "flac", "bit_depth": 24, "min_sample_rate": 96000},
  {"label": "FLAC 24-bit/44.1kHz","format": "flac", "bit_depth": 24, "min_sample_rate": 44100},
  {"label": "FLAC 16-bit",        "format": "flac", "bit_depth": 16},
  {"label": "MP3 320kbps",        "format": "mp3",  "min_bitrate": 320},
  ...
]
```
v2-Profile werden automatisch migriert.

### 4. Post-Download Verifikation (alle Quellen)
`probe_audio_quality()` liest die **echte heruntergeladene Datei** mit mutagen:
- FLAC: sample_rate + bit_depth + bitrate
- MP3: bitrate + sample_rate
- M4A/AAC/OGG/OPUS/WAV: bitrate + sample_rate

`check_quality_target()` prüft gegen die `ranked_targets`:
- Match → akzeptiert
- Kein Match + `fallback_enabled=False` → Quarantäne
- Nach Quarantäne → **Retry mit nächstem Kandidaten** (wie AcoustID)

---

## Was noch fehlt — nächste Schritte

### A) Andere Quellen anbinden (Source-Mapper)
Diese Quellen geben keine echten Werte, haben aber definierte Tier-Strings die wir mappen:

| Quelle | Tier-String | AudioQuality |
|--------|------------|--------------|
| Tidal | `'hires'` | `flac, 24bit, 96kHz` |
| Tidal | `'lossless'` | `flac, 16bit, 44.1kHz` |
| HiFi | gleich wie Tidal | — |
| Deezer | `'flac'` | `flac, 16bit, 44.1kHz` |

**Wo**: `core/quality/model.py` — `TIDAL_TIER_MAP`, `DEEZER_TIER_MAP` hinzufügen
**Warum**: Damit `filter_and_rank()` auch für diese Quellen entscheiden kann ob eine Quelle das Ziel erfüllt (z.B. "ich will 24-bit, Tidal lossless reicht nicht")

**Qobuz ist Sonderfall** — liefert echte `maximum_sampling_rate` + `maximum_bit_depth` aus der API, diese direkt in `AudioQuality` befüllen.

### B) UI — Ranked Targets als editierbare Liste
Aktuell zeigt das Settings-UI noch die alte `qualities`-Ansicht (FLAC on/off, MP3 320 on/off).

Neu: Drag & Drop Prioritätsliste in den Settings:
```
[↕] FLAC 24-bit/192kHz   [🗑]
[↕] FLAC 24-bit/96kHz    [🗑]
[↕] FLAC 24-bit/44.1kHz  [🗑]
[↕] FLAC 16-bit           [🗑]
[↕] MP3 320kbps           [🗑]
[+ Ziel hinzufügen]
```

**Wo**: `webui/static/settings.js` + `webui/index.html`
**API**: `GET/POST /api/quality-profile` existiert bereits, liefert jetzt v3

### C) Quarantäne-UI — Grund anzeigen
Wenn eine Datei wegen Quality quarantiniert wird, sollte das UI den Grund klar anzeigen:
- Welches Target wurde gesucht
- Was die Datei tatsächlich hat
- "Approve" Button setzt `_skip_quarantine_check = 'quality'`

### D) Tests
- `tests/quality/test_model.py` — `AudioQuality.matches_target()`, `filter_and_rank()`, Migration
- `tests/imports/test_quality_guard.py` — `check_quality_target()` mit verschiedenen Szenarien

---

## Branch auf neuem PC holen

```bash
git clone https://github.com/nick2000713/SoulSync.git
cd SoulSync
git fetch origin
git checkout feature/global-quality-system
```

Oder wenn der Fork schon geklont ist:
```bash
git fetch origin
git checkout feature/global-quality-system
```
