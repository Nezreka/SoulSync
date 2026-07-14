# Library V2 — UI-Anforderungen & Improvements (2026-07-14)

**Konsolidiert aus Nutzerfeedback:** User-Anforderungen zur visuellen Darstellung, Icons, Beschriftungen und Interaktionsmodellen, die die Usability und Klarheit der Library V2 UI verbessern, ohne Backend-/Query-Änderungen zu erfordern.

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
- **Aktuell:** Icon gefällt nicht.
- **Ziel:** Visuelle Hervorhebung — klarere, repräsentativere Grafik.
- **Anwendung:** Album-/Single-Details, Track-Rows, Quality-Profile-Picker.
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
- **Aktuell:** Quality Profile ist nur nach Expand sichtbar.
- **Ziel:** Quality Profile beim **Album/Single in der Übersicht** vor Expand zeigen.
  - z.B. in der Album-Card oder Album-Row: `[Album Title] — Quality: [Profile Name]`
- **Anwendung:** Artist-Detail Album-Tabelle, Album-/Single-Cards.
- **Scope:** UI Query/Projection bestätigen, dass `quality_profile_id` verfügbar ist; ggf. leichte Query-Ergänzung.

### 2.2 Quality Profile beim Artist (explizit sichtbar)
- **Ziel:** Artist-Detail sollte anzeigen, welches Quality Profile dem Artist zugewiesen ist.
- **Anwendung:** Artist-Toolbar oder separater "Default Quality Profile"-Block.
- **Scope:** UI-only (Profil wird bereits gemutet über API).

### 2.3 Unmonitored / One Release / Quality Profile hervorheben
- **Ziel:** Diese Metadaten sollten visuell klarer hervorgehoben werden (nicht als zufällige Text-Strings).
- **Anwendung:** Badges/Tags statt nur Text; Farbkodierung oder Symbole.
- **Scope:** UI CSS/Component-Styling.

### 2.4 Quality-Darstellung einheitlich formatieren
- **Aktuell:** `Flag / 16 Bit 44 kHz / 935 kbps` (Schrägstrich-Trennung visuell unschön).
- **Ziel:** Visuelle Gruppierung in 3 getrennte Komponenten/Blöcke:
  1. **Flag** (Quality Tier, z.B. "Lossless")
  2. **Audio Specs** (Bit Depth + Sample Rate, z.B. "16 Bit 44 kHz")
  3. **Bitrate** (z.B. "935 kbps")
  - Getrennte visuelle Einheiten statt Schrägstrich-Kette.
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
- **Aktuell:** Release-Übersicht zeigt: Title, Artist, Quality, File, Metadata, Actions.
- **Ziel:** Zusätzliche Spalte **"Quality Profile"** — zeigt assigned Profile pro Release.
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

### 5.2 Preview Retag — "File not found" Fehler fixen
- **Aktuell:** Zeigt "No File" oder "File not found on disk" obwohl Download vorhanden.
- **Ziel:** Path-Resolver oder File-State-Lookup korrekt verdrahten.
- **Scope:** Backend-Bug (Roadmap Backend-Findings).

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

---

## 7. Quality Checks & Verification

### 7.1 Verification Flow nach Interactive Search Download
- **Aktuell:** Download wird durchgewunken, Quality Checks bestätigt, aber nicht explizit als "verified" gekennzeichnet.
- **Ziel:** Nach erfolgreichem Interactive Search Download sollte der Verification-Status klar sein.
- **Scope:** UI Feedback/Status-Anzeige.

---

## 8. Comparison mit alter Library (Enhanced View)

### 8.1 Fehlende Metadaten-Anzeige
- **Aktuell:** Alte Library zeigte "Match" (welche Metadaten-Source gematch wurde).
- **Ziel:** Sollte Library V2 auch anzeigen (z.B. "Matched via Spotify" / "Matched via Deezer").
- **Scope:** UI Display (Backend trägt bereits Provenance).

### 8.2 ReplayGain-Funktion
- **Aktuell:** Fehlt in Library V2.
- **Ziel:** Enrich Album/Track mit ReplayGain-Werten (benutzerinitiierte Action).
- **Scope:** Backend-Funktion (nicht UI-only).

### 8.3 Enrich Album/Track Funktion
- **Ziel:** Wie in alter Library — gezielt Album/Track mit Metadaten anreichern.
- **Scope:** Backend-Funktion (API + Worker).

### 8.4 Manual Matching nach Metadaten-Source
- **Ziel:** Ähnlich Plex — bei Match-Fehlern manuell beheben; spezifisch pro Metadaten-Source.
- **Scope:** Backend-Funktion + UI Modal.

---

## 9. Current Status & Notes

- **Priorität:** Die Icons (1.1–1.3) und Nomenklatur (1.4) sollten zuerst gehen (schnell, hoher Impact).
- **Dann:** Visuelle Hierarchie (2.1–2.4) für bessere Lesbarkeit.
- **Backend-Findings parallelize:**
  - Managed Tracks funktioniert nicht → Roadmap
  - Update Discovery (Michael Jackson, Hirokyu Samono) → Roadmap
  - Artist Aliasing/Matching Fehler → Roadmap
  - Manual Matching UI → Roadmap
  - ReplayGain/Enrich → Roadmap

---

## 10. Implementation Order (empfohlen)

1. **Icons umbenennen/ersetzen** (1.1–1.3): Quick Win
2. **Quality-Darstellung reformatieren** (2.4): UI-only
3. **Quality Profile vor Expand anzeigen** (2.1–2.2): leichte Query-Ergänzung
4. **Badges/Tags für Metadata** (2.3): CSS/Component-Styling
5. **Action-Button-Sematik** (1.4, 6.1–6.3): Label + Icon Konsistenz
6. **Quality Profile Spalte** (4.1): Query + UI
7. **Backend-Findings durcharbeiten** (Preview Retag, Managed Tracks, Update Discovery, etc.)
