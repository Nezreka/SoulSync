**SoulSync 2.8.51** 🩹 quick hotfix on top of 2.8.5

**#983** — on a fresh install, opening a watchlist artist's settings could error out with `no such column: preferred_metadata_source` (a restart worked around it). first-run setup was rebuilding the watchlist table and dropping a couple of newer columns; they survive now, so it works from the first boot. upgraders were never affected.

grab it the usual way — `docker pull` the `latest`/`2.8.51` tag.
