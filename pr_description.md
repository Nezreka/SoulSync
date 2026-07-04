# soulsync 2.8.5 — `dev` → `main`

a focused bug-fix release. no new features — just a batch of import, library, repair, and webui fixes, several from user reports this week.

---

## 🐛 fixes

### import & metadata
- **Import & Stats were a black screen for some people (#979)** — those two pages are the React-built ones, loaded as module scripts. on setups where the OS handed the `.js` bundle over with the wrong MIME type (common on Windows), the browser refused to run it and the page went black while everything else worked. we now force the correct type ourselves, so the OS can't blank those pages.
- **iTunes singles landed in "Unknown Artist" with no album tag (#980)** — an iTunes single reached the importer with no album name, so it dropped into `Unknown Artist/Unknown Album`, got no album tag, and didn't match its release. a single's album is effectively its own title now, so it files correctly and tags properly.
- **single-disc albums got a "01-" on every track (#981)** — `$disc`/`$discnum` in a file template always stamped the disc number, even on a 1-disc album. they're smart now (like `$cdnum`): empty on single-disc, shown only for real multi-disc sets. applies to both fresh imports and rename/reorganize.

### library safety & repair
- **never delete a configured root folder during cleanup (#976)** — the empty-dir cleanup could remove a folder you'd set as a root; it leaves configured roots alone now, and self-heals a missing staging/import folder if a sweep removed it.
- **repair "Fix All" for libraries outside the transfer path (#978)** — path-mismatch "Fix All" did nothing for libraries stored outside the transfer folder; it works everywhere now, updates the DB by track id for media-server parity, and the unknown-artist fix no longer yanks media-server files into the transfer folder.
- **discography backfill only touches artists you own (#977)** — the repair backfill was reaching beyond your owned artists; scoped back to what you actually have.

enjoy 🎶
