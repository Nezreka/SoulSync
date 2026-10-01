# soulsync 3.4.9: `dev` → `main`

big discover glow-up, a full player theater, sample studio from your own library, a calmer sync page, and a stack of filing and download fixes. scope: the commits since the 3.4.8 release commit (`7ea9ad9c3`).

## discover

- the top of discover opens like a home page now: a greeting and a grid of what you go back to, then flow, on repeat, your daily mixes, a mood for the hour, repeat rewind and a blend.
- fourteen pills that all just scrolled became one honest section nav. the hero reads like a billboard instead of a form, and the reason line bolds the artists it names.
- new banners about you: your week in music (plays vs last week, a bar per day, streak, who was on repeat), plus rows that lead with you and say why. daily mixes come first and say who's in them.
- music video backdrops: the hero and spotlight play the artist's official video, a bento spotlight and a 9:16 watch rail that plays itself, tap for sound, and a stage that only plays what fits the device (1 on a phone, up to 6 on a strong desktop).
- concerts near you: a country setting under settings > concerts so the inbox stops showing shows in green bay, and the inbox triages like mail.
- real mood mixes (chill, focus, energy, feel good, late night) built from your own albums, where the fake flow moods bar used to be.
- fixes: the label explorer picks labels you actually play instead of the first 30 sqlite found, the genre browser shows art it had all along, album rails show the whole row, the page works on a phone, and broken hero faces fall back to an initial.
- daily mixes were ranking "genres" that were really artist names, so a mix asked the pool for a genre called "Louis Armstrong" and came back empty. and deezer tracks never got genres at all (deezer artists have none, only albums do), so every genre playlist was empty for deezer users. both fixed.

## player theater

- the small now playing modal is a full player theater: animated backgrounds, a 10-band EQ, and proper playback tools, on the same playback engine (#1388).
- immersive mode takes over the whole modal (click the art, the button, or F), plus 8 new themes and visual options for quality, energy, palettes, dim, auto-cycle and reduce motion (#1389, #1390).
- backgrounds actually follow the song now: a dedicated analyser with real bass/vocal/cymbal bands and spectral-flux beat detection, plus a visual pass on kaleido, battery and bloom (#1392). also fixed the visualizer tap doubling the signal, which made everything ~6 dB louder while it was on.

## sample studio

- new: your library is the sample pack. browse tracks, chop on a waveform editor with a bpm grid and onset detection, pitch and time-stretch, and save to a sample stash (#1374).
- a guided ux pass with suggested chops and ~14x faster analysis, then fx (normalize, reverse, fades, reverb, beat-synced delay) where the preview and the saved chop render the same (#1379, #1383).
- the page was calling a backend that didn't read half of it, so fx came out dry, trim 404'd and the key was never computed. built out for real.
- real stems without torch: the same htdemucs weights run on onnxruntime (~20 MB instead of a ~2 GB torch install), so everyone gets stems. the rough splits sounded bad and are gone.
- path fixes so it finds files the same way playback does, container paths included, plus duration units, stuck analysis and slow search (#1376, #1378).

## sync page

- the auto-sync board got its room back (#1401): counts are chips, the idle monitor is one line, empty intervals are small drop chips, and drop targets glow sky blue while you drag. the dashboard now opens the same manager as the playlists page.
- source tabs you don't use can be hidden (#1402).
- a mirrored card always opens the playlist, so delete mirror and edit source are reachable again (#1403, #1405).
- the three "sync" buttons that did three different things have three names now, and syncs stop announcing every playlist as youtube or spotify (#1404).
- playlist server backups default off. replace sync was making a "<name> Backup" on navidrome/plex/jellyfin every time, on by default, and the setting now says what it actually is (#1406).
- listenbrainz rolling mirrors pick the newest week instead of whichever row was written last (#1407), and the listenbrainz / last.fm discover button does something again.
- the quality profile select stopped hanging on "Loading…", and personalized cards stop offering folder/quality settings they can't save.
- scheduled "process all mirrored playlists" and refresh mirrored used admin's mirrors for every profile, so a non-admin's pipeline ran on nothing and said success. it uses the automation owner's mirrors now (#1411).

## downloads and filing

- a release from the artist page or search files under the section it showed in. deezer's 3-track album showed under Albums and filed as a Single. paths and reorganize now respect the type the source gave, so deezer singles and EPs stay put (thanks SeadogsBooty on discord).
- download discography only counts a single as owned when that single is, instead of when its song is on some album.
- download discography lists exactly what the artist page shows. it used to refetch on its own with no source, so a deezer page with 2 EPs downloaded musicbrainz's underground fan club EPs too.
- a playlist track files under its album's credit, not its singer, so Let It Go lands in Frozen instead of Idina Menzel (#1385). settings now shows the compilation path template that soundtracks were using all along (#1385).
- the download modal: cancelling from outside it or a batch the server dropped ends it properly, and reopening an album after its run starts fresh (#1384, #1386). search results pick up their in-library badge when a download finishes (#1386).
- torrents stopped getting deleted 90 seconds after they sit at 0%. a soulseek rule was removing them and their data, then grabbing them again, 4 times on one private tracker album (thanks Tostadaman on discord). torrents only follow their own stall setting now, and "pause" actually keeps the torrent.
- staging imports name the artist after the folder the album sits in, not the top folder. a torrent client's folder mounted inside staging made every artist "qbittorrent" (thanks Tostadaman on discord).
- a failed deezer download says why now (expired arl, no license token, quality your plan doesn't have) instead of "state: Errored". the reason was always written down, it just never made it to the screen. same for every streaming source (#1349).
- mix, dub and edit only mean remix inside a version qualifier, so 311's "Mix It Up" and "Rub a Dub" stop getting dropped (#1381).
- search tries other itunes stores when the US one doesn't have it, deezer stops skipping songs, and covers sit above the artist (#1398).
- the downloads review tab is readable now: a segmented control with counts, and the attempt count sits by the track name.

## matching (@mandos21)

- acoustid keeps whole titles inside brackets, and wishlist cleanup confirms track and release identity before clearing, so distinct subtitles and punctuation titles don't collapse (#1372).
- the duplicate detector tells numbered tracks and roman numeral parts apart and ignores edition years (#1382).

## companion extension and api

- browser extensions can call the api: cors preflights are answered, api keys pass the login/pin gates for image requests, and a valid key gets the admin profile context instead of 401ing (#1375, #1377, #1380).
- the image proxy normalizes plex/jellyfin/navidrome artwork urls before fetching instead of 502ing (#1373).
- spotify playlists fall back to the public source on the premium 403, link-pasted mirrors resolve by raw id, and cover art shows up on the no-auth paths and never gets wiped on re-mirror (#1394, #1395, #1396).
- a complete per-endpoint api reference for all 87 /api/v1 endpoints, and the readme points at the companion extension (#1397, #1400).

## the rest

- library a-z sort ignores leading punctuation, so "Weird Al" files under W and *NSYNC under N (#1408).
- duplicates: keep best keeps the copy a server playlist points at, and each copy shows "In playlist: ...", so bulk accepting hundreds of duplicates doesn't quietly break playlists (thanks jadux on discord).
- settings > navidrome has an optional playlist account, so the admin's synced playlists can go to a different navidrome user than the one scanning (thanks Cremonies on discord).
- tools: findings show up when a maintenance job finishes and a background run doesn't clear what you ticked (#1386), and the expired download cleaner link lands on the cleaner.
- explorer: scroll scrolls and ctrl/pinch zooms, like google maps, with a lock to flip it back (#1409).
- soulseek stops polling slskd every 6 seconds when nothing's listening (#1387).
- a stray brace in mobile.css was throwing away the next block of mobile styles.

## validation

- fixes shipped with regression tests and green neighboring suites, per their commits and PRs.
- a full green suite run on the release head has not been verified.
