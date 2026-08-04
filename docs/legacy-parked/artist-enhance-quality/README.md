# Geparkt: Artist „Enhance Quality" und „Write Artist Image"

Wie bei [Re-identify](../reidentify/README.md): **kein aktiver Code**, sondern
die wörtliche Kopie zweier Funktionen, die nur von der gelöschten alten
Artist-Detail-Seite aus erreichbar waren. Beide hingen an DOM-Knoten, die es
nicht mehr gibt (`#library-artist-enhance-btn`, `#library-artist-write-image-btn`),
und an `artistDetailPageState`, dem Zustand jener Seite.

Anders als Re-identify wurden sie **nicht** ausdrücklich zum Nachbauen
vorgemerkt — sie sind hier, weil sie beim Löschen der alten Library still
mitgestorben wären und ihre Backends weiterlaufen. Ob sie in Library V2 zurück
sollen, ist eine offene Produktentscheidung.

## Enhance Quality (`enhance-quality-modal.js`)

Prüfte die Tonqualität aller Tracks eines Artists, zeigte den Button nur, wenn
Tracks unter der eingestellten Mindeststufe lagen, und ließ den Nutzer eine
Auswahl davon zum Neu-Beschaffen einreichen.

- `GET /api/library/artist/<id>/quality-analysis` — Tierauswertung je Track
- `POST /api/library/artist/<id>/enhance` — Auswahl einreichen

**Überschneidung mit V2:** Library V2 löst dasselbe Bedürfnis anders — über
Quality Profiles mit Cutoff plus „Automatic Search", das genau die Dateien
unterhalb des Cutoffs neu sucht. Ein Nachbau sollte deshalb zuerst klären, ob
er überhaupt noch gebraucht wird oder nur eine Artist-gefilterte Ansicht der
Cutoff-Unmet-Liste wäre.

## Write Artist Image (`write-artist-image.js`)

Schrieb `artist.jpg` in den Artist-Ordner (Issue #572), damit Navidrome — das
keine API für Artistbilder hat — beim nächsten Scan ein echtes Foto findet.
Plex/Jellyfin lesen die Datei ebenfalls als Fallback.

- `POST /api/artist/<id>/write-image-to-disk` (mit `overwrite`-Bestätigung,
  wenn schon eine handverlesene `artist.jpg` existiert)

**Spannung mit dem Guide:** Library V2 ist ausdrücklich media-server-unabhängig
und hält `artist.jpg`/`cover.jpg` im Musikordner für einen optionalen Export,
nie für die verlässliche Quelle (Guide §2.1). Ein Nachbau wäre also genau das:
ein bewusster Export-Knopf, keine Artwork-Pipeline.

## Was noch mitging, aber wirklich weg ist

Diese Funktionen der alten Library haben in V2 eine Entsprechung oder wurden
ausdrücklich als Nicht-Ziel entschieden und sind nicht geparkt: Watch All
Unwatched (V2: Bulk-Monitoring), Artist-Export als JSON/CSV/Text, der
A-Z-Sprungselektor (Guide §1.2, ausdrückliches Nicht-Ziel) und die
Enhanced-View-Bulk-Aktionen (V2: Retag-, Reorganize- und Bulk-Edit-Modals).
