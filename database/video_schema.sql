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
    tmdb_id              INTEGER UNIQUE,
    imdb_id              TEXT,
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
    poster_url           TEXT,
    backdrop_url         TEXT,
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

-- ── Content: TV (shows → seasons → episodes) ────────────────────────────────
CREATE TABLE IF NOT EXISTS shows (
    id                 INTEGER PRIMARY KEY,
    tvdb_id            INTEGER UNIQUE,
    tmdb_id            INTEGER,
    imdb_id            TEXT,
    title              TEXT NOT NULL,
    sort_title         TEXT,
    year               INTEGER,
    overview           TEXT,
    status             TEXT,             -- continuing | ended | upcoming
    network            TEXT,
    runtime_minutes    INTEGER,
    content_rating     TEXT,
    poster_url         TEXT,
    backdrop_url       TEXT,
    monitored          INTEGER NOT NULL DEFAULT 1,   -- "following" (watchlist)
    quality_profile_id INTEGER REFERENCES quality_profiles(id) ON DELETE SET NULL,
    root_folder_id     INTEGER REFERENCES root_folders(id)     ON DELETE SET NULL,
    path               TEXT,
    added_at           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_shows_tvdb      ON shows(tvdb_id);
CREATE INDEX IF NOT EXISTS idx_shows_monitored ON shows(monitored);

CREATE TABLE IF NOT EXISTS seasons (
    id            INTEGER PRIMARY KEY,
    show_id       INTEGER NOT NULL REFERENCES shows(id) ON DELETE CASCADE,
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
    season_number   INTEGER NOT NULL,
    episode_number  INTEGER NOT NULL,
    title           TEXT,
    overview        TEXT,
    air_date        TEXT,             -- ISO date — drives the Calendar
    runtime_minutes INTEGER,
    tvdb_id         INTEGER,
    monitored       INTEGER NOT NULL DEFAULT 1,
    has_file        INTEGER NOT NULL DEFAULT 0,
    UNIQUE (show_id, season_number, episode_number)
);
CREATE INDEX IF NOT EXISTS idx_episodes_show     ON episodes(show_id);
CREATE INDEX IF NOT EXISTS idx_episodes_air      ON episodes(air_date);
CREATE INDEX IF NOT EXISTS idx_episodes_wanted   ON episodes(monitored, has_file);

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
