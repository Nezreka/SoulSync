**SoulSync 2.8.5** is out 🎉

a focused bug-fix release — thanks to everyone who reported this week 🙏

🖤 **Import & Stats black screen (#979)** — on some setups (mostly Windows) those two pages loaded blank because the OS handed the app's JS bundle over with the wrong file type and the browser refused to run it. we force the correct type now, so they load for everyone.

🎵 **iTunes singles fixed (#980)** — iTunes singles were landing in "Unknown Artist" with no album tag. they now file into the right artist/album and tag properly.

💿 **no more "01-" on single-disc albums (#981)** — `$disc`/`$discnum` in a file template was stamping a disc number even on 1-disc albums. now they vanish on single-disc (like `$cdnum`) and only show for real multi-disc sets — for both import and rename/reorganize.

🛟 **library safety + repair** — cleanup never deletes a configured root folder and self-heals a missing staging folder (#976) · repair "Fix All" now works for libraries outside the transfer path and stops pulling media-server files into transfer (#978) · discography backfill only touches artists you own (#977).

enjoy! 🎶
