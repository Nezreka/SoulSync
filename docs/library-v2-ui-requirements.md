# Library V2 — UI-Anforderungen & Improvements (2026-07-14)

**Konsolidiert aus Nutzerfeedback:** User-Anforderungen zur visuellen Darstellung, Icons, Beschriftungen und Interaktionsmodellen, die die Usability und Klarheit der Library V2 UI verbessern, ohne Backend-/Query-Änderungen zu erfordern.

> **Update 2026-07-17:** Das verbindliche neuere Nutzer-Review steht in
> `docs/library-v2.md`, §52. Es erweitert den Scope ausdrücklich über reine
> UI-Arbeit hinaus (Profilherkunft, Early Search→Library-v2-Materialisierung,
> Pipeline-Correlation/History) und ersetzt widersprechende ältere Aussagen,
> insbesondere das reine Info-Icon am Track und getrennte Monitoring-
> Oberflächen. Diese Datei bleibt Detailreferenz für nicht widersprechende
> UI-Anforderungen.

---

## 1. Icon-Konsistenz & Nomenklatur (Lidarr-Alignment)

### 1.1 Interactive Search Icon
- **Aktuell:** Icon gefällt nicht.
- **Ziel:** Lidarr-Konvention verwenden — **Menschen-Icon** für Interactive Search (manuell).
- **Betroffene Komponenten:** Artist-Toolbar, Album-Blocks, Einzeltrack-Zeilen.
- **Scope:** UI-only, keine Backend-Auswirkung.

### 1.2 Automatic Search Icon
- **Ziel:** Lidarr-Konvention — **Lupe/Magnifying Glass** für automatisierte Suche.
- **Anwendung:** Toolbar-Button, Global-Search-Action ("Search All Monitored" sollte Automatic-Konvention haben).
- **Scope:** UI-only.

### 1.3 Quality Profile Icon
- **Status:** **Abgeschlossen & Festgelegt** (Beibehalten)
- **Entscheidung:** Das **Stern-Icon** (`star`) wird beibehalten. Es gefällt dem Nutzer sehr gut und wird weiterhin als Quality Profile Icon verwendet. Keine Änderungen vornehmen.
- **Scope:** UI-only.

### 1.4 Action-Buttons ("Grab", "Search", "Download")
- **Aktuell:** Verwirrend mehrere Actions nebeneinander ("Grab and Download" ist unklar).
- **Ziel:** Lidarr-Konvention: nur zwei primäre Track-/Album-Actions:
  - **"Automatic Search"** (Lupe) — beste verfügbare Source automatisch wählen
  - **"Interactive Search"** (Mensch) — Nutzer wählt manuell
- **Klarheit:** Jeder Button sollte genau einen Intent vermitteln.
- **Scope:** UI Layout/Semantik. Backend-Integration unverändert.

---

## 2. Visuelle Hierarchie & Hervorhebung

### 2.1 Quality Profile Visibility vor Expand
- **Status 2026-07-17:** **Umgesetzt und um Herkunft erweitert (§53).** Album-
  und Track-Badges zeigen neben dem effektiven Profil auch `Album`, `Artist`
  oder `App default`; der Picker kann explizite Overrides auf Vererbung
  zurücksetzen.
- **Früher:** Quality Profile war nur nach Expand sichtbar.
- **Ziel:** Quality Profile beim **Album/Single in der Übersicht** vor Expand zeigen.
  - z.B. in der Album-Card oder Album-Row: `[Album Title] — Quality: [Profile Name]`
- **Anwendung:** Artist-Detail Album-Tabelle, Album-/Single-Cards.
- **Scope:** UI Query/Projection bestätigen, dass `quality_profile_id` verfügbar ist; ggf. leichte Query-Ergänzung.

### 2.2 Quality Profile beim Artist (explizit sichtbar)
- **Status 2026-07-17:** **Umgesetzt und um Herkunft erweitert (§53).**
- **Ziel:** Artist-Detail sollte anzeigen, welches Quality Profile dem Artist zugewiesen ist.
- **Anwendung:** Artist-Toolbar oder separater "Default Quality Profile"-Block.
- **Scope:** UI-only (Profil wird bereits gemutet über API).

### 2.3 Unmonitored / One Release / Quality Profile hervorheben
- **Ziel:** Diese Metadaten sollten visuell klarer hervorgehoben werden (nicht als zufällige Text-Strings).
- **Anwendung:** Badges/Tags statt nur Text; Farbkodierung oder Symbole.
- **Scope:** UI CSS/Component-Styling.

### 2.4 Quality-Darstellung formatieren
- **Status:** **Abgeschlossen & Festgelegt** (Beibehalten)
- **Entscheidung:** Die ursprüngliche Formatierung (z.B. `FLAC · 24bit/96kHz` und Bitrate getrennt) ist optimal und wird komplett beibehalten. Keine Änderungen am Layout oder Trennung vornehmen.
- **Scope:** UI CSS/Layout.

---

## 3. Manage Tracks Funktionalität

### 3.1 Manage Tracks erweitern
- **Aktuell:** Eingeschränkt; "No Single Album Duplicates found" funktioniert nicht reliabel.
- **Ziel:** Wie in Lidarr — Duplikate/Single↔Album-Paare können:
  - Gelöscht werden (explizite Delete-Action)
  - Verschoben/konsolidiert werden
  - Relative Pfade mit Quality info sichtbar machen
- **Scope:** Teils UI (Delete-Buttons), teils Backend (müsste Roadmap-Punkt werden).

### 3.2 Delete-Action in Managed Tracks (Top-Level)
- **Ziel:** Managed Tracks Modal sollte am oberen Rand eine "Delete selected" Action haben.
- **Scope:** UI.

---

## 4. Release/Album-Detail Metadaten-Spalten

### 4.1 Quality Profile Spalte hinzufügen
- **Status:** **Abgeschlossen & Festgelegt** (Beibehalten)
- **Entscheidung:** Das Quality Profile wird wie ursprünglich direkt in der "Quality"-Zelle dargestellt und nicht in eine separate Spalte ausgelagert. Das Layout ist so optimal.
- **Scope:** UI/Query (bei Album-Detail laden).

### 4.2 Quality Profile pro Track änderbar machen
- **Ziel:** Im Track-Detail oder Track-Row sollte es möglich sein, das Quality Profile zu ändern.
- **Scope:** Backend müsste Track-Level-Profile-Assignment unterstützen (derzeit Artist-/Album-Scope); UI ergänzen.

---

## 5. Managed Tracks Display & Lifecycle

### 5.1 Managed Tracks als echte Liste statt Duplikat-Paare-Ansicht
- **Ziel:** Wie in Lidarr — alle Versionen eines Tracks mit relativen Pfaden, Quality, Monitor-State sichtbar.
- **Anwendung:** Separate "Managed Tracks" Sektion mit Tabelle (Path | Quality | Monitor | Actions).
- **Scope:** Größerer UI-Refactor; Backend-Query (`GET /api/library/v2/artists/<id>/managed-tracks`) notwendig.

### 5.2 Preview Retag — "File not found" Fehler beheben & UI-Gruppierung
- **Status:** **Abgeschlossen**
- **Details:** 
  - Tracks ohne Datei (`No file` oder `File not found on disk`) werden im Preview-Retag-Modal ausgeblendet, sodass nur vorhandene Dateien gelistet werden.
  - Die verbleibenden Tracks werden im Modal dezent nach Alben gruppiert dargestellt.
  - Fehlerhafte Änderungsalarme bei Datumsangaben (z.B. Mismatch wegen Sekunden `08:00` vs. `08:00:00`) und Genres (keine Überschreibung von detaillierten Genres durch generische Provider-Genres) wurden behoben.
- **Scope:** Backend-Bug (tag_writer.py) + UI (retag-modal.tsx).

---

## 6. Suchfunktion & Quellen-Auswahl

### 6.1 "Search All Monitored" → "Search" (umbenennen)
- **Ziel:** Globale Aktion sollte "Search" statt "Search All Monitored" heißen; Tooltip erklärt „searches entire monitored wishlist".
- **Scope:** UI Label.

### 6.2 "Search All Upgrades" → "Search Upgrades" (umbenennen)
- **Ziel:** Analog zu oben.
- **Scope:** UI Label.

### 6.3 "Automatic Search" Beschriftung
- **Ziel:** Bei globalen Actions verwenden (konsistent mit Track-Level Actions).
- **Scope:** UI Label + Icon.

### 6.4 Artist-spezifische automatische Suche (Lidarr-Style)
- **Status:** **Offen / Ausstehend** (Ausführliches Konzept erfasst)
- **Ziel:** Der "Automatic Search"-Button in der Artist-Detailansicht (und entsprechend bei Alben) darf **nicht** die gesamte globale Wishlist verarbeiten. Er soll sich wie in Lidarr verhalten:
  - Sucht **ausschließlich** nach gemonitorten Titeln des **spezifischen Künstlers/Albums**.
  - Führt automatische Upgrades durch, sofern das Quality Profile dies erlaubt.
  - Eine globale Suche ("Automatic Search (Global)") soll weiterhin existieren, jedoch nur im globalen Dashboard bzw. der Wishlist-Ansicht, nicht auf Artist-/Album-Ebene.
- **Scope:** Backend-Erweiterung (Einschränkung des Such-Job-Scopes auf den spezifischen Artist/Album) + UI-Verdrahtung.

---

## 7. Quality Checks & Verification

### 7.1 Verification Flow nach Interactive Search Download
- **Status:** **Abgeschlossen**
- **Details:** Der Verification-Status ist über `TrackVerificationBadge` voll integriert (Anzeige von `AcoustID ✓`, `AcoustID Human`, `AcoustID Bypassed` und `AcoustID Unverified`).
- **Scope:** UI Feedback/Status-Anzeige.

---

## 8. Comparison mit alter Library (Enhanced View)

### 8.1 Fehlende Metadaten-Anzeige
- **Status:** **Abgeschlossen**
- **Details:** Match-Chips (z. B. *"Matched via Spotify"*, *"Deezer"* etc.) werden für Tracks und Alben vollständig gerendert.
- **Scope:** UI Display (Backend trägt bereits Provenance).

### 8.2 ReplayGain-Funktion
- **Status:** **Abgeschlossen**
- **Details:** Sowohl auf Album- als auch auf Track-Ebene sind Buttons zur ReplayGain-Analyse und Tag-Schreibung implementiert und mit dem Backend verdrahtet.
- **Scope:** Backend-Funktion (nicht UI-only).

### 8.3 Enrich Album/Track Funktion
- **Ziel:** Wie in alter Library — gezielt Album/Track mit Metadaten anreichern.
- **Scope:** Backend-Funktion (API + Worker).
- **Design-Überlegungen (2026-07-16):**
  - *Artist-Level-Enrichment* holt nur Artist-spezifische Metadaten (Bio, Genre, IDs, Bilder), führt aber kein Deep-Enrichment aller Tracks des Künstlers aus.
  - *Album-Level-Enrichment* holt Album-Metadaten und aktualisiert alle Tracks dieses Albums.
  - *Track-Level-Enrichment* existiert im Backend/API, ist aber in der UI weggelassen worden, um visuelle Überladung zu vermeiden.
  - *Nutzer-Feedback:* Der Nutzer möchte ungern zusätzliche Buttons in der Track-Tabelle haben. Falls Track-Enrichment gebraucht wird, muss eine alternative, platzsparende Lösung gefunden werden.

### 8.4 Manual Matching nach Metadaten-Source
- **Ziel:** Ähnlich Plex — bei Match-Fehlern manuell beheben; spezifisch pro Metadaten-Source.
- **Scope:** Backend-Funktion + UI Modal.

### 8.5 Legacy-Import von Dateieigenschaften (ReplayGain, Lyrics)
- **Status:** **Abgeschlossen** (2026-07-16, siehe docs/library-v2.md §25.2)
- **Ziel:** Beim Importieren aus der alten Bibliothek (`import_legacy_library`) werden die Track-Feature-Flags (`has_replaygain` / `has_lyrics`) der Dateien nicht direkt übernommen. Sie werden erst nach einem manuellen "Refresh & Scan" in der UI sichtbar.
- **Fix:** Neue `precache_tag_cache()`-Stage (`core/library2/tag_cache.py`) liest die Tags direkt nach dem Import (bounded ThreadPoolExecutor, gleiches Muster wie Artwork-/Tracklist-Precache).
- **Scope:** `core/library2/tag_cache.py`, `api/library_v2.py`.

---

## 9. Current Status & Notes

- **Ergebnisse der Design-Finalisierung:**
  - **Quality Profile Icon (1.3):** Festgelegt auf das **Stern-Icon** (`star`) – keine weiteren Änderungen.
  - **Quality Profile Position (4.1):** Bleibt integriert in der **Quality-Spalte** – keine separate Spalte erstellen.
  - **Quality-Darstellung (2.4):** Bleibt in der ursprünglichen Form (z.B. `FLAC · 24bit/96kHz` und Bitrate getrennt) – keine Änderungen am Layout oder Trennung vornehmen.
  - **Zustand:** Diese UI-Fragen sind damit **abgeschlossen** und müssen in Folgesitzungen nicht erneut angefasst werden.

---

## 10. Status der Implementierung

1. **Icons & Nomenklatur** (1.1, 1.2, 1.4, 6.1–6.3): **Abgeschlossen** (Lidarr-Konventionen für Suche und Icons sind aktiv).
2. **Quality Profile Icon** (1.3): **Abgeschlossen** (Entscheidung: Stern-Icon beibehalten).
3. **Quality Profile vor Expand** (2.1–2.2): **Abgeschlossen** (Wird in AlbumBlock mit dem Stern-Icon gerendert).
4. **Metadata-Tags** (2.3): **Abgeschlossen** (detailLabel-Styling ist aktiv).
5. **Quality-Darstellung** (2.4): **Abgeschlossen** (Entscheidung: Ursprüngliches Layout beibehalten).
6. **Quality Profile Spalte** (4.1): **Abgeschlossen** (Entscheidung: In Quality-Spalte belassen).
7. **Backend-Findings** (8.3, 8.4 etc.): Roadmap-Punkte für zukünftige Sitzungen.
8. **Artist-spezifische automatische Suche** (6.4): **Offen / Ausstehend** (Aufteilung in artist-spezifisch vs. global benötigt Backend-Erweiterung).
9. **Verification Flow, Match-Quelle und ReplayGain** (7.1, 8.1, 8.2): **Abgeschlossen** (Badges, Chips und ReplayGain-Aktionen sind voll funktionsfähig).
10. **Legacy-Import von Dateieigenschaften** (8.5): **Abgeschlossen** (`precache_tag_cache()` liest Tags direkt nach dem Import, siehe §25.2).
11. **Preview Retag** (5.2): **Abgeschlossen** (Tracks ohne Datei ausgeblendet, dezent nach Alben gruppiert, falsche Änderungsalarme behoben).
