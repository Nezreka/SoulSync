# Interactive Search: Fehler und Überarbeitung

Stand: 28.08.2026

## Gefundene Fehler

- Das Modal war für Release-Namen, Qualitätsdetails und mehrere Clients zu eng und zu unruhig.
- Während einer parallelen Suche war nicht erkennbar, welcher Client fertig, noch aktiv oder fehlgeschlagen war.
- Terminale Transferzustände wie `Completed, Succeeded` wurden als `Queued` klassifiziert; ein kurzer Cleanup-Overlap konnte den Status zusätzlich wieder einblenden.
- Ein erfolgreicher Download-Dispatch wurde teilweise als fertiger Download angezeigt, obwohl Import oder Quarantäne noch offen waren.
- Prowlarr-Release-Namen wurden heuristisch an Bindestrichen getrennt und konnten dadurch falsche Artist-/Titelspalten erzeugen.
- Seeder, Peer-Slots und Grabs waren in einer gemeinsamen Availability-Spalte vermischt; Seeder sind für Usenet nicht aussagekräftig.
- Aktuelles slskd liefert `sampleRate` und `bitDepth` direkt am Suchtreffer; SoulSync las dort nur `bitRate` und suchte die Auflösung im alten `attributes`-Fallback. Dadurch wurde z. B. `FLAC 24/48 kHz` nur als `FLAC` angezeigt.

## Umsetzung

- Breites, responsives Modal mit sauberem Header, rundem Close-Button, sticky Tabellenkopf und horizontalem Scrollen auf kleinen Displays.
- Live-Status je Such-Client: `Searching`, `Finished` oder `Failed`, inklusive Trefferzahl und Laufzeit; schnelle Ergebnisse erscheinen sofort.
- Downloadknopf zeigt `Queued`, `Downloading n%`, `Processing`, `Verifying`, `Imported` oder einen konkreten Fehler. Ein bloßer Dispatch heißt nur `Started`.
- Terminale und bereits verarbeitete Transfers verschwinden aus der aktiven Queue statt erneut als `Queued` zu erscheinen.
- Wie in Lidarr bleibt der rohe Prowlarr-Release-Titel getrennt; bei Library-v2-Suchen stammen Artist/Album/Track aus dem serverseitig validierten Suchkontext.
- Capability-basierte Spalten: Torrent zeigt Seeder/Leecher, Soulseek Slots/Queue, Usenet keine Seeder. `Artist`, `Size`, `Age`, `Peers / seeders` und `Grabs` sind benutzerseitig ein-/ausblendbar und werden gespeichert.
- Soulseek übernimmt Bitrate, Sample-Rate und Bit-Tiefe aus dem aktuellen slskd-Filemodell und behält den alten Attribute-Fallback für ältere Antworten.

## Prowlarr-Teilfehler

Prowlarr startet die ausgewählten Indexer parallel, wartet mit `Task.WhenAll` auf die ganze Gruppe und fängt Fehler je Indexer ab. Ein normal fehlgeschlagener Indexer darf die übrigen Treffer daher nicht verwerfen. Problematisch ist ein hängender Indexer: Der gemeinsame HTTP-Request bleibt bis zu dessen Timeout offen und kann vorher am SoulSync-Read-Timeout scheitern. SoulSync sucht deshalb nach erfolgreicher Indexer-Auflösung jeden Indexer separat und parallel; Treffer der fertigen Indexer bleiben erhalten, nur ein Totalausfall wird als Fehler gemeldet. Kann Prowlarr seine Indexerliste nicht liefern, bleibt als Kompatibilitäts-Fallback der gemeinsame Request.

## Prüfung

- 131 gezielte Python-Tests und 21 Interactive-Search-UI-Tests bestanden.
- Produktions-Build bestanden; Desktop-Render (1600×950) und Mobile-Render (390×844) visuell auf Breite, Scrollen, Spalten und Statusdarstellung geprüft.
