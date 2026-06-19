-- ============================================================================
-- SoulSync — VIDEO side schema  (database/video_library.db)
--
-- ISOLATION: this is a SEPARATE SQLite file from the music library. The video
-- code owns it exclusively; music never opens it and it never references music
-- tables. A bug, migration, or reset here cannot touch music data, and the two
-- never contend for the same write lock.
--
-- DESIGN PRINCIPLES (deliberately avoiding the music DB's known pain points):
--   * No polymorphic (entity_type, entity_id) keys. Where a row can belong to a
--     movie OR an episode OR a youtube video, we use separate nullable FKs with
--     a CHECK that exactly one is set — real foreign keys, real cascades.
--   * No "source id" blob / naming spaghetti. External ids are a few explicit,
--     well-named, indexed columns (tmdb_id, tvdb_id, imdb_id, youtube_id).
--   * No metadata dumping-ground column. Structured config that is genuinely a
--     list (quality profile contents) is small, ordered JSON; everything else
--     is a real column.
--   * Watchlist / Wishlist / Calendar are DERIVED VIEWS over monitored + file
--     state, not standalone tables — so they can't drift out of sync with the
--     library the way a duplicated table does. (See note in design summary.)
--
-- Run order matters (FKs reference earlier tables). The init module executes
-- this whole file inside one transaction with foreign_keys ON.
-- ============================================================================

-- ── Meta ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- ── Configuration ───────────────────────────────────────────────────────────
-- Root folders: where each kind of library content is stored on disk.
CREATE TABLE IF NOT EXISTS root_folders (
    id           INTEGER PRIMARY KEY,
    path         TEXT NOT NULL UNIQUE,
    content_kind TEXT NOT NULL CHECK (content_kind IN ('movie', 'show', 'youtube')),
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Quality profiles: an ordered, named acceptance ladder (Radarr/Sonarr-style).
-- `items` is a small JSON array of allowed quality names, best-first; `cutoff`
-- is the name we stop upgrading at. JSON is appropriate here — it is genuinely
-- an ordered list of config, not a metadata grab-bag.
CREATE TABLE IF NOT EXISTS quality_profiles (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    cutoff     TEXT,
    items      TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Video-side settings. KEY/VALUE for now; at the end-of-branch settings.db
-- consolidation these migrate into the shared config store. Value is JSON.
CREATE TABLE IF NOT EXISTS video_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ── Content: Movies ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS movies (
    id                   INTEGER PRIMARY KEY,
    server_source        TEXT,            -- 'plex' | 'jellyfin' (NULL = not on a server yet, e.g. wishlist)
    server_id            TEXT,            -- media server native id (Plex ratingKey / Jellyfin Item Id)
    tmdb_id              INTEGER,         -- not unique: same film can sit in >1 library
    imdb_id              TEXT,
    tmdb_match_status    TEXT,            -- enrichment: NULL=pending | matched | not_found | error
    tmdb_last_attempted  TEXT,
    title                TEXT NOT NULL,
    sort_title           TEXT,
    year                 INTEGER,
    overview             TEXT,
    runtime_minutes      INTEGER,
    status               TEXT,            -- announced | in_production | released
    release_date         TEXT,            -- primary/theatrical (ISO date)
    digital_release_date TEXT,
    studio               TEXT,
    content_rating       TEXT,            -- e.g. PG-13
    tagline              TEXT,
    rating               REAL,            -- TMDB audience score (0-10)
    rating_critic        REAL,            -- critic score (0-100) when offered
    imdb_rating          REAL,            -- IMDb (0-10, via OMDb)
    rt_rating            INTEGER,         -- Rotten Tomatoes (0-100)
    metacritic           INTEGER,         -- Metacritic (0-100)
    ratings_synced       INTEGER NOT NULL DEFAULT 0,   -- OMDb ratings fetched?
    poster_url           TEXT,
    backdrop_url         TEXT,
    logo_url             TEXT,            -- transparent title logo (clearlogo)
    monitored            INTEGER NOT NULL DEFAULT 1,   -- tracked for acquisition
    has_file             INTEGER NOT NULL DEFAULT 0,   -- owned? (denormalized)
    quality_profile_id   INTEGER REFERENCES quality_profiles(id) ON DELETE SET NULL,
    root_folder_id       INTEGER REFERENCES root_folders(id)     ON DELETE SET NULL,
    path                 TEXT,            -- folder on disk once owned
    added_at             TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_movies_tmdb       ON movies(tmdb_id);
CREATE INDEX IF NOT EXISTS idx_movies_monitored  ON movies(monitored, has_file);
CREATE INDEX IF NOT EXISTS idx_movies_release    ON movies(release_date);
-- Upsert/stale-removal key: the server's native id. Multiple NULLs are allowed
-- (wishlist items not yet on a server), so this never blocks non-server rows.
CREATE UNIQUE INDEX IF NOT EXISTS ux_movies_server ON movies(server_source, server_id);

-- ── Content: TV (shows → seasons → episodes) ────────────────────────────────
CREATE TABLE IF NOT EXISTS shows (
    id                 INTEGER PRIMARY KEY,
    server_source      TEXT,             -- 'plex' | 'jellyfin' (NULL = not on a server yet)
    server_id          TEXT,             -- media server native id
    tvdb_id            INTEGER,          -- not unique (same series can sit in >1 library)
    tmdb_id            INTEGER,
    imdb_id            TEXT,
    tmdb_match_status  TEXT,             -- enrichment match state per source
    tmdb_last_attempted TEXT,
    tvdb_match_status  TEXT,
    tvdb_last_attempted TEXT,
    title              TEXT NOT NULL,
    sort_title         TEXT,
    year               INTEGER,
    overview           TEXT,
    status             TEXT,             -- continuing | ended | upcoming
    network            TEXT,
    airs_time          TEXT,             -- TVDB show air time, e.g. "21:00" (network local)
    runtime_minutes    INTEGER,
    content_rating     TEXT,
    tagline            TEXT,
    rating             REAL,             -- TMDB audience score (0-10)
    imdb_rating        REAL,             -- IMDb (0-10, via OMDb)
    rt_rating          INTEGER,          -- Rotten Tomatoes (0-100)
    metacritic         INTEGER,          -- Metacritic (0-100)
    ratings_synced     INTEGER NOT NULL DEFAULT 0,   -- OMDb ratings fetched?
    first_air_date     TEXT,
    last_air_date      TEXT,
    poster_url         TEXT,
    backdrop_url       TEXT,
    logo_url           TEXT,             -- transparent title logo (clearlogo)
    episodes_synced    INTEGER NOT NULL DEFAULT 0,   -- full episode list pulled from metadata?
    monitored          INTEGER NOT NULL DEFAULT 1,   -- "following" (watchlist)
    quality_profile_id INTEGER REFERENCES quality_profiles(id) ON DELETE SET NULL,
    root_folder_id     INTEGER REFERENCES root_folders(id)     ON DELETE SET NULL,
    path               TEXT,
    added_at           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_shows_tvdb      ON shows(tvdb_id);
CREATE INDEX IF NOT EXISTS idx_shows_monitored ON shows(monitored);
CREATE UNIQUE INDEX IF NOT EXISTS ux_shows_server ON shows(server_source, server_id);

CREATE TABLE IF NOT EXISTS seasons (
    id            INTEGER PRIMARY KEY,
    show_id       INTEGER NOT NULL REFERENCES shows(id) ON DELETE CASCADE,
    server_id     TEXT,             -- media server native id (for reference/refresh)
    season_number INTEGER NOT NULL,
    title         TEXT,
    overview      TEXT,
    poster_url    TEXT,
    monitored     INTEGER NOT NULL DEFAULT 1,
    UNIQUE (show_id, season_number)
);
CREATE INDEX IF NOT EXISTS idx_seasons_show ON seasons(show_id);

CREATE TABLE IF NOT EXISTS episodes (
    id              INTEGER PRIMARY KEY,
    show_id         INTEGER NOT NULL REFERENCES shows(id)   ON DELETE CASCADE,
    season_id       INTEGER NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
    server_source   TEXT,             -- 'plex' | 'jellyfin'
    server_id       TEXT,             -- media server native id
    season_number   INTEGER NOT NULL,
    episode_number  INTEGER NOT NULL,
    title           TEXT,
    overview        TEXT,
    air_date        TEXT,             -- ISO date — drives the Calendar
    runtime_minutes INTEGER,
    still_url       TEXT,             -- per-episode thumbnail (server image path)
    rating          REAL,             -- audience score (0-10)
    tvdb_id         INTEGER,
    monitored       INTEGER NOT NULL DEFAULT 1,
    has_file        INTEGER NOT NULL DEFAULT 0,
    UNIQUE (show_id, season_number, episode_number)
);
CREATE INDEX IF NOT EXISTS idx_episodes_show     ON episodes(show_id);
CREATE INDEX IF NOT EXISTS idx_episodes_air      ON episodes(air_date);
CREATE INDEX IF NOT EXISTS idx_episodes_wanted   ON episodes(monitored, has_file);

-- ── Genres (normalised many-to-many; no comma-blob) ─────────────────────────
CREATE TABLE IF NOT EXISTS genres (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);
CREATE TABLE IF NOT EXISTS movie_genres (
    movie_id INTEGER NOT NULL REFERENCES movies(id) ON DELETE CASCADE,
    genre_id INTEGER NOT NULL REFERENCES genres(id) ON DELETE CASCADE,
    PRIMARY KEY (movie_id, genre_id)
);
CREATE TABLE IF NOT EXISTS show_genres (
    show_id  INTEGER NOT NULL REFERENCES shows(id)  ON DELETE CASCADE,
    genre_id INTEGER NOT NULL REFERENCES genres(id) ON DELETE CASCADE,
    PRIMARY KEY (show_id, genre_id)
);
CREATE INDEX IF NOT EXISTS idx_movie_genres_genre ON movie_genres(genre_id);
CREATE INDEX IF NOT EXISTS idx_show_genres_genre  ON show_genres(genre_id);

-- ── People + credits (cast & crew; normalised, no blob) ─────────────────────
-- A person appears in many titles; deduped by their provider id. Each credit
-- belongs to exactly one movie OR show (separate nullable FKs + CHECK, no
-- polymorphic id), mirroring the media_files/downloads pattern.
CREATE TABLE IF NOT EXISTS people (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL,
    tmdb_id   INTEGER UNIQUE,
    photo_url TEXT
);
CREATE INDEX IF NOT EXISTS idx_people_name ON people(name);

CREATE TABLE IF NOT EXISTS credits (
    id         INTEGER PRIMARY KEY,
    person_id  INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    movie_id   INTEGER REFERENCES movies(id) ON DELETE CASCADE,
    show_id    INTEGER REFERENCES shows(id)  ON DELETE CASCADE,
    department TEXT NOT NULL,        -- 'cast' | 'crew'
    job        TEXT,                 -- Director | Writer | Creator (crew); 'Actor' (cast)
    character  TEXT,                 -- the role played (cast)
    sort_order INTEGER NOT NULL DEFAULT 0,
    CHECK ((movie_id IS NOT NULL) + (show_id IS NOT NULL) = 1)
);
CREATE INDEX IF NOT EXISTS idx_credits_movie  ON credits(movie_id);
CREATE INDEX IF NOT EXISTS idx_credits_show   ON credits(show_id);
CREATE INDEX IF NOT EXISTS idx_credits_person ON credits(person_id);

-- ── Content: YouTube (channels → videos) ────────────────────────────────────
CREATE TABLE IF NOT EXISTS channels (
    id                 INTEGER PRIMARY KEY,
    youtube_id         TEXT NOT NULL UNIQUE,    -- channel id
    title              TEXT NOT NULL,
    handle             TEXT,                    -- @handle
    description        TEXT,
    avatar_url         TEXT,
    banner_url         TEXT,
    monitored          INTEGER NOT NULL DEFAULT 1,   -- "subscribed" (watchlist)
    quality_profile_id INTEGER REFERENCES quality_profiles(id) ON DELETE SET NULL,
    root_folder_id     INTEGER REFERENCES root_folders(id)     ON DELETE SET NULL,
    path               TEXT,
    added_at           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_channels_monitored ON channels(monitored);

CREATE TABLE IF NOT EXISTS channel_videos (
    id               INTEGER PRIMARY KEY,
    channel_id       INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    youtube_id       TEXT NOT NULL UNIQUE,      -- video id
    title            TEXT NOT NULL,
    description      TEXT,
    published_at     TEXT,             -- ISO datetime — drives feed/Calendar
    duration_seconds INTEGER,
    thumbnail_url    TEXT,
    monitored        INTEGER NOT NULL DEFAULT 1,
    has_file         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_channel_videos_channel   ON channel_videos(channel_id);
CREATE INDEX IF NOT EXISTS idx_channel_videos_published ON channel_videos(published_at);
CREATE INDEX IF NOT EXISTS idx_channel_videos_wanted    ON channel_videos(monitored, has_file);

-- Cheap persistent cache of YouTube video upload dates (the flat listing omits
-- them). Filled from the channel RSS feed + any per-video metadata fetch, so the
-- channel page's year-seasons fill in over time without re-fetching. Standalone
-- (no channels FK) since the bridge stores channels in video_watchlist.
CREATE TABLE IF NOT EXISTS youtube_video_dates (
    youtube_id   TEXT PRIMARY KEY,
    published_at TEXT,
    cached_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Tracks which followed channels have had their full upload dates fetched (by the
-- background enricher) so we don't re-sweep them constantly.
CREATE TABLE IF NOT EXISTS youtube_channel_enrichment (
    channel_id  TEXT PRIMARY KEY,
    enriched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    date_count  INTEGER NOT NULL DEFAULT 0,
    method      TEXT                        -- 'innertube' | 'fallback'; NULL = legacy → re-enrich once
);

-- Remembered per-channel catalog so re-opening a channel (especially a watchlisted
-- one) is instant: served cache-first, then a background re-stream refreshes it.
-- Upload dates stay in youtube_video_dates (merged on read); this holds the list.
CREATE TABLE IF NOT EXISTS youtube_channel_videos (
    channel_id    TEXT NOT NULL,
    youtube_id    TEXT NOT NULL,
    title         TEXT,
    thumbnail_url TEXT,
    duration      TEXT,                  -- overlay badge, e.g. "12:34"
    view_count    INTEGER,               -- approximate (parsed from "2.6M views")
    cached_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (channel_id, youtube_id)
);
CREATE INDEX IF NOT EXISTS idx_ycv_channel ON youtube_channel_videos(channel_id);

-- Remembered channel metadata (avatar/subs/tags/banner) so the header renders
-- instantly on re-open without a yt-dlp re-fetch.
CREATE TABLE IF NOT EXISTS youtube_channel_meta (
    channel_id       TEXT PRIMARY KEY,
    title            TEXT,
    handle           TEXT,
    description      TEXT,
    avatar_url       TEXT,
    banner_url       TEXT,
    subscriber_count INTEGER,
    view_count       INTEGER,
    tags             TEXT,                  -- JSON array
    cached_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Per-video supplementary stats from the no-key YouTube enrichers (keyed by
-- youtube_id, NOT by channel, so a video shared across playlists is enriched
-- once). Merged onto the cached catalog on read.
--   ryd_*  Return YouTube Dislike  -> like/dislike estimates
--   sb_*   SponsorBlock            -> crowd-sourced segments (in youtube_video_segments)
-- status columns: NULL = pending, 'ok' | 'not_found' | 'error'.
CREATE TABLE IF NOT EXISTS youtube_video_stats (
    youtube_id      TEXT PRIMARY KEY,
    like_count      INTEGER,
    dislike_count   INTEGER,
    ryd_status      TEXT,
    ryd_attempted   TEXT,
    sb_status       TEXT,
    sb_attempted    TEXT,
    dearrow_title   TEXT,                  -- DeArrow crowd-sourced better title
    dearrow_status  TEXT,
    dearrow_attempted TEXT
);

-- SponsorBlock crowd segments (sponsor/intro/outro/selfpromo/…) for a video.
CREATE TABLE IF NOT EXISTS youtube_video_segments (
    youtube_id TEXT NOT NULL,
    category   TEXT NOT NULL,           -- sponsor | intro | outro | selfpromo | interaction | music_offtopic | preview | filler | poi_highlight | chapter
    start_sec  REAL NOT NULL,
    end_sec    REAL NOT NULL,
    votes      INTEGER,
    uuid       TEXT NOT NULL,
    PRIMARY KEY (youtube_id, uuid)
);
CREATE INDEX IF NOT EXISTS idx_yvseg_video ON youtube_video_segments(youtube_id);

-- ── Owned media files (the Library = content that has a file) ────────────────
-- Exactly one owner FK is set (no polymorphic id). 1 row per physical file;
-- usually 1:1 with its content, but the table allows history/extras.
CREATE TABLE IF NOT EXISTS media_files (
    id             INTEGER PRIMARY KEY,
    movie_id       INTEGER REFERENCES movies(id)         ON DELETE CASCADE,
    episode_id     INTEGER REFERENCES episodes(id)       ON DELETE CASCADE,
    video_id       INTEGER REFERENCES channel_videos(id) ON DELETE CASCADE,
    relative_path  TEXT NOT NULL,
    size_bytes     INTEGER,
    resolution     TEXT,             -- 480p | 720p | 1080p | 2160p
    video_codec    TEXT,
    audio_codec    TEXT,
    release_source TEXT,             -- bluray | web-dl | webrip | hdtv | youtube
    quality        TEXT,             -- resolved quality name
    runtime_seconds INTEGER,
    added_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((movie_id IS NOT NULL) + (episode_id IS NOT NULL) + (video_id IS NOT NULL) = 1)
);
CREATE INDEX IF NOT EXISTS idx_media_files_movie   ON media_files(movie_id);
CREATE INDEX IF NOT EXISTS idx_media_files_episode ON media_files(episode_id);
CREATE INDEX IF NOT EXISTS idx_media_files_video   ON media_files(video_id);

-- ── Downloads (active queue + history) ──────────────────────────────────────
-- One target per row: a movie, a single episode, a whole season (pack), or a
-- youtube video. Exactly one FK set (CHECK), no polymorphic id.
CREATE TABLE IF NOT EXISTS downloads (
    id                 INTEGER PRIMARY KEY,
    movie_id           INTEGER REFERENCES movies(id)         ON DELETE SET NULL,
    episode_id         INTEGER REFERENCES episodes(id)       ON DELETE SET NULL,
    season_id          INTEGER REFERENCES seasons(id)        ON DELETE SET NULL,
    video_id           INTEGER REFERENCES channel_videos(id) ON DELETE SET NULL,
    title              TEXT NOT NULL,        -- display label
    release_title      TEXT,                 -- actual release / nzb / torrent name
    source             TEXT,                 -- torrent | usenet | youtube
    client             TEXT,                 -- qbittorrent | sabnzbd | yt-dlp ...
    client_download_id TEXT,                 -- hash / nzo_id to poll the client
    indexer            TEXT,
    status             TEXT NOT NULL DEFAULT 'queued'
                       CHECK (status IN ('queued','downloading','importing',
                                         'completed','failed','paused')),
    quality            TEXT,
    size_bytes         INTEGER,
    downloaded_bytes   INTEGER NOT NULL DEFAULT 0,
    progress           REAL    NOT NULL DEFAULT 0,   -- 0..100
    download_speed_bps INTEGER NOT NULL DEFAULT 0,
    eta_seconds        INTEGER,
    error_message      TEXT,
    added_at           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at         TEXT,
    completed_at       TEXT,
    CHECK ((movie_id IS NOT NULL) + (episode_id IS NOT NULL)
         + (season_id IS NOT NULL) + (video_id IS NOT NULL) = 1)
);
CREATE INDEX IF NOT EXISTS idx_downloads_status    ON downloads(status);
CREATE INDEX IF NOT EXISTS idx_downloads_completed ON downloads(completed_at);

-- ── Activity feed (dashboard "Recent Activity") ─────────────────────────────
CREATE TABLE IF NOT EXISTS activity (
    id         INTEGER PRIMARY KEY,
    event_type TEXT NOT NULL,        -- added | grabbed | imported | failed | renamed
    message    TEXT NOT NULL,
    movie_id   INTEGER REFERENCES movies(id)         ON DELETE SET NULL,
    episode_id INTEGER REFERENCES episodes(id)       ON DELETE SET NULL,
    video_id   INTEGER REFERENCES channel_videos(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_activity_created ON activity(created_at);

-- ── User watchlist (curated follow-list: shows + people) ────────────────────
-- DISTINCT from the library-derived v_watchlist below: this is the user's
-- explicit follow-list and may include shows/people that are NOT in the library
-- yet (the whole point of following someone). Keyed on the stable cross-context
-- tmdb_id that both shows and people carry. The monitoring/discovery engine is a
-- later phase — this table just records membership + enough to render + link.
CREATE TABLE IF NOT EXISTS video_watchlist (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,             -- 'show' | 'person' | 'channel' (youtube)
    tmdb_id     INTEGER NOT NULL,          -- tmdb id; for non-tmdb sources a stable surrogate of source_id
    title       TEXT NOT NULL,             -- show title / person name / channel title
    poster_url  TEXT,                      -- poster (show) / photo (person) / avatar (channel)
    library_id  INTEGER,                   -- shows.id when owned (else NULL)
    -- generic source bridge: 'tmdb' (default) or 'youtube'; source_id = native id
    -- (channel youtube id) for non-tmdb rows. One table, both worlds.
    source      TEXT NOT NULL DEFAULT 'tmdb',
    source_id   TEXT,
    -- 'follow' = explicit user follow. 'mute' = a TOMBSTONE: the user
    -- un-followed something that is on the watchlist by default (an actively
    -- airing library show), so the default must not re-add it. Library shows
    -- that are still airing are watched by default WITHOUT a row here.
    state       TEXT NOT NULL DEFAULT 'follow',
    date_added  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(kind, tmdb_id)
);
CREATE INDEX IF NOT EXISTS idx_video_watchlist_kind ON video_watchlist(kind);

-- WISHLIST (curated 'get this') — atomic units are MOVIES and EPISODES. Adding a
-- whole show or a season just expands into episode rows. Upcoming (un-aired)
-- episodes do NOT live here; the watchlist/calendar promote them once they air,
-- so the wishlist only ever holds things you can actually acquire right now.
CREATE TABLE IF NOT EXISTS video_wishlist (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    kind           TEXT NOT NULL,            -- 'movie' | 'episode' | 'video' (youtube)
    tmdb_id        INTEGER NOT NULL,         -- movie's tmdb id | the SHOW's tmdb id (episode) | channel surrogate (video)
    title          TEXT NOT NULL,            -- movie title | show title | channel title (video rows)
    poster_url     TEXT,                     -- movie/show poster | channel avatar (video rows)
    year           INTEGER,                  -- movie year (movie rows)
    season_number  INTEGER,                  -- episode rows
    episode_number INTEGER,                  -- episode rows
    episode_title  TEXT,                     -- episode rows | video title (video rows)
    still_url      TEXT,                     -- episode still | video thumbnail (video rows)
    episode_overview  TEXT,                  -- episode synopsis | video description (video rows)
    season_poster_url TEXT,                  -- the episode's SEASON poster (episode rows)
    air_date       TEXT,                     -- episode air date | video published_at (video rows)
    status         TEXT NOT NULL DEFAULT 'wanted',  -- wanted|searching|downloading|downloaded|failed
    library_id     INTEGER,                  -- owned movies.id/shows.id when re-downloading
    server_source  TEXT,                     -- server context that added it (informational)
    -- generic source bridge (mirrors video_watchlist). For 'video' rows:
    -- source='youtube', source_id=video youtube id, parent_source_id=channel youtube id.
    source         TEXT NOT NULL DEFAULT 'tmdb',
    source_id      TEXT,
    parent_source_id TEXT,                   -- owning channel's youtube id (video rows)
    date_added     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
-- one row per movie, one per (show, season, episode), one per youtube video —
-- partial uniques so the shapes don't collide and re-adding is an idempotent upsert.
CREATE UNIQUE INDEX IF NOT EXISTS idx_video_wishlist_movie
    ON video_wishlist(tmdb_id) WHERE kind = 'movie';
CREATE UNIQUE INDEX IF NOT EXISTS idx_video_wishlist_episode
    ON video_wishlist(tmdb_id, season_number, episode_number) WHERE kind = 'episode';
CREATE INDEX IF NOT EXISTS idx_video_wishlist_show ON video_wishlist(tmdb_id) WHERE kind = 'episode';
-- NOTE: the source_id / parent_source_id partial indexes are created in code
-- (VideoDatabase._ensure_indexes) AFTER the column migrations run — they can't
-- live here because this script runs via executescript() BEFORE the ALTERs, so
-- on an upgraded DB the columns wouldn't exist yet.

-- ── Derived views: Watchlist / Wishlist / Calendar ──────────────────────────
-- WATCHLIST = things you follow for NEW content: monitored shows + channels.
CREATE VIEW IF NOT EXISTS v_watchlist AS
    SELECT 'show'    AS kind, id, title, status, poster_url, monitored
      FROM shows    WHERE monitored = 1
    UNION ALL
    SELECT 'channel' AS kind, id, title, NULL AS status, avatar_url AS poster_url, monitored
      FROM channels WHERE monitored = 1;

-- WISHLIST = wanted-but-missing: monitored movies without a file + monitored
-- episodes that have aired but aren't owned.
CREATE VIEW IF NOT EXISTS v_wishlist AS
    SELECT 'movie'   AS kind, m.id AS ref_id, m.title AS title,
           NULL AS parent_title, m.release_date AS due_date
      FROM movies m
     WHERE m.monitored = 1 AND m.has_file = 0
    UNION ALL
    SELECT 'episode' AS kind, e.id AS ref_id,
           e.title AS title, s.title AS parent_title, e.air_date AS due_date
      FROM episodes e
      JOIN shows s ON s.id = e.show_id
     WHERE e.monitored = 1 AND e.has_file = 0
       AND e.air_date IS NOT NULL AND e.air_date <= date('now');

-- CALENDAR = dated items (episode air dates, movie releases, channel uploads).
CREATE VIEW IF NOT EXISTS v_calendar AS
    SELECT 'episode' AS kind, e.id AS ref_id, e.air_date AS date,
           e.title AS title, s.title AS parent_title
      FROM episodes e JOIN shows s ON s.id = e.show_id
     WHERE e.air_date IS NOT NULL
    UNION ALL
    SELECT 'movie' AS kind, m.id AS ref_id, m.release_date AS date,
           m.title AS title, NULL AS parent_title
      FROM movies m WHERE m.release_date IS NOT NULL
    UNION ALL
    SELECT 'video' AS kind, v.id AS ref_id, v.published_at AS date,
           v.title AS title, c.title AS parent_title
      FROM channel_videos v JOIN channels c ON c.id = v.channel_id
     WHERE v.published_at IS NOT NULL;

-- DOWNLOADS — every grab initiated from the video side lands here (movies/tv/youtube).
-- The pipeline starts the download, watches it, and on completion moves the file to the
-- per-type library folder and marks it completed. Status: queued|downloading|completed|failed.
CREATE TABLE IF NOT EXISTS video_downloads (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL,                 -- movie | show | youtube
    title         TEXT,                          -- human title (e.g. the movie name)
    release_title TEXT,                          -- the release/file being grabbed
    source        TEXT,                          -- soulseek | torrent | usenet
    username      TEXT,                          -- slskd uploader (for the grab + status)
    filename      TEXT,                          -- slskd remote filename (full path)
    size_bytes    INTEGER DEFAULT 0,
    quality_label TEXT,
    media_id      TEXT,                          -- the movie/show id (for the detail-page link)
    media_source  TEXT,                          -- library | tmdb
    year          INTEGER,
    poster_url    TEXT,                          -- poster for the Downloads card
    target_dir    TEXT,                          -- destination library folder
    dest_path     TEXT,                          -- final moved path (set on completion)
    status        TEXT NOT NULL DEFAULT 'downloading',
    progress      REAL DEFAULT 0,
    error         TEXT,
    created_at    TEXT DEFAULT (datetime('now')),
    updated_at    TEXT DEFAULT (datetime('now')),
    completed_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_video_downloads_status ON video_downloads(status);
