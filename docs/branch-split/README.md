# Library overhaul branch split

The Library overhaul was split because one branch had become the owner of
three independently reviewable products:

| Branch | Responsibility | Merge order |
| --- | --- | --- |
| `quality-profiles-foundation` | Native Watchlist and mirrored-playlist Quality Profile persistence and execution | First |
| `library-overhaul` | Library v2 Artists, Wanted, Import Review, catalogue, acquisition, and file management | After Foundation |
| `library-v2-playlist-ui` | Parked Library v2 Playlist UI and its dedicated tests | Later |

The pre-split state is preserved by branch
`backup-library-overhaul-pre-foundation-split-20260722` and annotated tag
`backup/library-overhaul-pre-foundation-split-20260722`, both at
`4f3952ae9cedf6919759f2774584b2632453d882`.

See [LIBRARY_OVERHAUL.md](LIBRARY_OVERHAUL.md) for the active branch contract.
After Foundation is rebased into this line, its detailed contract is in
`QUALITY_PROFILES_FOUNDATION.md` from the Foundation branch.
