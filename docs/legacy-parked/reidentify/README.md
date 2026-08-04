# Geparkt: Re-identify Track (#889)

Diese Dateien sind **kein aktiver Code**. Sie sind die wörtliche Kopie der
Legacy-Oberfläche für *Re-identify* aus dem Moment, bevor die alte Library
gelöscht wurde — damit das Feature später in Library V2 nachgebaut werden kann
und man beim Nachbau nachsehen kann, wie es sich vorher verhalten hat.

Nichts hier wird ausgeliefert oder geladen. Ausgangsstand ist der Commit
unmittelbar vor der Löschung (`git log --diff-filter=D -- webui/static/library.js`).

## Was das Feature tat

Ein Admin öffnete zu einem bereits importierten Track „Re-identify" und legte
ihn unter einem anderen Release ab (Single / EP / Album). Der Dialog durchsuchte
jede konfigurierte Metadatenquelle (Tabs, aktive Quelle vorbelegt); beim
Bestätigen wurde die Datei gestaged und ein einmalig gültiger Hinweis
geschrieben, den der Auto-Import-Worker konsumiert. Optional wurde die
Originaldatei nach dem Re-Import gelöscht (Checkbox „Replace the original file").

## Dateien

| Datei | Herkunft | Inhalt |
|---|---|---|
| `reidentify.js` | `webui/static/library.js` | `reidState`, `openReidentifyModal`, `closeReidentifyModal`, `runReidentifySearch`, `confirmReidentify` |
| `reidentify-modal.html` | `webui/index.html` | Markup von `#reid-modal-overlay` |
| `reidentify.css` | `webui/static/style.css` | alle `.reid-*`-Regeln |
| `enhanced-view-hook.js.txt` | `webui/static/library.js` | die Stelle in der Enhanced-View, an der der Button den Dialog öffnete (Kontext: wie Track/Album/Artwork übergeben wurden) |

## Das Backend lebt weiter

Nur die Oberfläche wurde entfernt. Diese Endpunkte und Module sind unverändert
im Betrieb und sind beim Nachbau in V2 direkt wiederverwendbar:

- `GET /api/reidentify/sources` — verfügbare Metadatenquellen
- `GET /api/reidentify/search?source=&q=` — Releases einer Quelle suchen
- `POST /api/reidentify/apply` — Datei stagen + Rematch-Hinweis schreiben
- `core/imports/rematch_search.py`, `rematch_hints.py`, `rematch_apply.py`

Ein V2-Nachbau muss also im Wesentlichen nur den Dialog neu bauen und dieselben
drei Endpunkte aufrufen. Zusätzlich zu klären ist dabei, woher die V2-Variante
`track.id` nimmt: der Legacy-Aufruf übergab die **Legacy**-Track-ID, V2-Zeilen
tragen ihre eigene `lib2_track_id` plus `legacy_track_id`.

## Verwandt, aber *nicht* geparkt

Der Dialog „Manual Library Match" lag ebenfalls in `library.js`, wird aber von
der Sync- und der Tools-Seite benutzt und ist deshalb weiter aktiver Code — er
ist nach `webui/static/library-shared.js` umgezogen, nicht hierher.
