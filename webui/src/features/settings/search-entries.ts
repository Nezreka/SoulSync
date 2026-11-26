/**
 * What help search finds in Settings (help plan 4.4): one entry per thing a
 * person would look for, with the tab it sits on and where on that tab.
 *
 *   anchor.id       an element id in the tab's markup (the input, its row)
 *   anchor.heading  a section title or heading on the tab, matched by text
 *   open            the card lives in a modal: a service tile (Connections)
 *                   or a source tile (Sources) opens it first; or it is on
 *                   one of the People tab's parts (Global policy, Templates,
 *                   ...), which openPeopleSettings brings up first
 *   side            only that side's settings page shows it (video-side.css
 *                   hides [data-music-only] / [data-video-only] blocks)
 *   file            the tab file the anchor is in; search-entries.test.ts
 *                   reads it, so an anchor that moves or goes fails there
 *
 * Labels are the field's own words where it has them. Settings is admin
 * only; a member's own things live in My Account (ACCOUNT_ENTRIES).
 */

import { nav, resolveTab } from './nav';
import { TAB_TITLES } from './shell';

export type SettingsTab =
  | 'connections'
  | 'downloads'
  | 'quality'
  | 'library'
  | 'discovery'
  | 'people'
  | 'devices'
  | 'appearance'
  | 'advanced';

export type Anchor = { id: string } | { heading: string };

export type PeoplePart = 'people' | 'policy' | 'templates' | 'invites' | 'activity';

export type Opener =
  | { service: string; kind?: 'music' | 'video' }
  | { source: string }
  | { people: PeoplePart };

export interface SettingsEntry {
  /** stable: recents remember it */
  id: string;
  label: string;
  tab: SettingsTab;
  anchor: Anchor;
  file: string;
  /** the words after "Settings › Tab": what it is for, in a few words */
  hint?: string;
  keywords?: string;
  side?: 'music' | 'video';
  open?: Opener;
}

type Row = Omit<SettingsEntry, 'id'> & { id?: string };

function slug(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '');
}

function entries(rows: Row[]): SettingsEntry[] {
  return rows.map((r) => ({
    ...r,
    id:
      r.id ??
      `setting:${r.tab}:${'id' in r.anchor ? r.anchor.id : slug(r.anchor.heading)}${
        r.side ? `:${r.side}` : ''
      }`,
  }));
}

const id = (value: string): Anchor => ({ id: value });
const heading = (value: string): Anchor => ({ heading: value });

export const SETTINGS_ENTRIES: SettingsEntry[] = entries([
  // ── Connections: media server ──
  {
    label: 'Media server',
    tab: 'connections',
    anchor: heading('Server connections'),
    file: 'connections-servers.tsx',
    hint: 'Plex, Jellyfin, Navidrome or none',
    keywords: 'plex jellyfin emby navidrome music assistant standalone server connect',
    side: 'music',
  },
  {
    label: 'Plex server URL',
    tab: 'connections',
    anchor: id('plex-url'),
    file: 'connections-servers.tsx',
    hint: 'Plex',
    keywords: 'plex address host',
    side: 'music',
  },
  {
    label: 'Plex token',
    tab: 'connections',
    anchor: id('plex-token'),
    file: 'connections-servers.tsx',
    hint: 'Plex',
    keywords: 'plex sign in link',
    side: 'music',
  },
  {
    label: 'Plex music library',
    tab: 'connections',
    anchor: id('plex-music-library'),
    file: 'connections-servers.tsx',
    hint: 'which Plex library is music',
    keywords: 'plex section',
    side: 'music',
  },
  {
    label: 'Jellyfin server URL',
    tab: 'connections',
    anchor: id('jellyfin-url'),
    file: 'connections-servers.tsx',
    hint: 'Jellyfin',
    keywords: 'jellyfin emby address host',
    side: 'music',
  },
  {
    label: 'Jellyfin API key',
    tab: 'connections',
    anchor: id('jellyfin-api-key'),
    file: 'connections-servers.tsx',
    hint: 'Jellyfin',
    keywords: 'jellyfin emby token',
    side: 'music',
  },
  {
    label: 'Jellyfin user',
    tab: 'connections',
    anchor: id('jellyfin-user'),
    file: 'connections-servers.tsx',
    hint: 'whose library to scan',
    keywords: 'jellyfin emby account library to scan',
    side: 'music',
  },
  {
    label: 'Jellyfin music library',
    tab: 'connections',
    anchor: id('jellyfin-music-library'),
    file: 'connections-servers.tsx',
    hint: 'Jellyfin',
    keywords: 'jellyfin emby library',
    side: 'music',
  },
  {
    label: 'Jellyfin API timeout',
    tab: 'connections',
    anchor: id('jellyfin-timeout'),
    file: 'connections-servers.tsx',
    hint: 'seconds before giving up',
    keywords: 'jellyfin slow',
    side: 'music',
  },
  {
    label: 'Navidrome server URL',
    tab: 'connections',
    anchor: id('navidrome-url'),
    file: 'connections-servers.tsx',
    hint: 'Navidrome',
    keywords: 'navidrome subsonic address',
    side: 'music',
  },
  {
    label: 'Navidrome login',
    tab: 'connections',
    anchor: id('navidrome-username'),
    file: 'connections-servers.tsx',
    hint: 'username and password',
    keywords: 'navidrome subsonic user password',
    side: 'music',
  },
  {
    label: 'Navidrome music library',
    tab: 'connections',
    anchor: id('navidrome-music-folder'),
    file: 'connections-servers.tsx',
    hint: 'Navidrome',
    keywords: 'navidrome folder',
    side: 'music',
  },
  {
    label: 'Navidrome playlist account',
    tab: 'connections',
    anchor: id('navidrome-playlist-username'),
    file: 'connections-servers.tsx',
    hint: 'who owns synced playlists',
    keywords: 'navidrome playlists owner',
    side: 'music',
  },
  {
    label: 'Music Assistant URL',
    tab: 'connections',
    anchor: id('music_assistant-url'),
    file: 'connections-servers.tsx',
    hint: 'Music Assistant',
    keywords: 'home assistant',
    side: 'music',
  },
  {
    label: 'Video server',
    tab: 'connections',
    anchor: id('stg-video-server-card'),
    file: 'connections-video-server.tsx',
    hint: 'Plex or Jellyfin for movies and TV',
    keywords: 'plex jellyfin emby movies tv shows server connect',
    side: 'video',
  },
  {
    label: 'Server Activity',
    tab: 'advanced',
    anchor: heading('Now playing'),
    file: 'server-activity-settings.tsx',
    hint: 'who is playing what, upload speed',
    keywords: 'tautulli streams now playing bandwidth',
  },
  {
    label: 'Server Activity upload speed',
    tab: 'advanced',
    anchor: id('sa-upload-mbps'),
    file: 'server-activity-settings.tsx',
    hint: 'Mbps',
    keywords: 'bandwidth upload',
  },
  {
    label: 'Keep play history for',
    tab: 'advanced',
    anchor: id('sa-retention'),
    file: 'server-activity-settings.tsx',
    hint: 'Server Activity',
    keywords: 'retention plays history',
  },

  // ── Connections: metadata and services ──
  {
    label: 'Primary metadata source',
    tab: 'connections',
    anchor: id('metadata-fallback-source'),
    file: 'connections-metadata.tsx',
    hint: 'where artist and album details come from',
    keywords: 'spotify deezer itunes apple musicbrainz discogs default source fallback',
    side: 'music',
  },
  {
    label: 'Library discography source',
    tab: 'connections',
    anchor: id('library-discography-source'),
    file: 'connections-metadata.tsx',
    hint: 'whose discography the library shows',
    keywords: 'albums releases metadata',
    side: 'music',
  },
  {
    label: 'Spotify client ID and secret',
    tab: 'connections',
    anchor: id('spotify-client-id'),
    file: 'connections-metadata.tsx',
    hint: 'Spotify',
    keywords: 'spotify api app credentials oauth',
    side: 'music',
    open: { service: 'spotify' },
  },
  {
    label: 'Tidal client ID',
    tab: 'connections',
    anchor: id('tidal-client-id'),
    file: 'connections-metadata.tsx',
    hint: 'Tidal playlists and metadata',
    keywords: 'tidal api credentials',
    side: 'music',
    open: { service: 'tidal' },
  },
  {
    label: 'Discogs token',
    tab: 'connections',
    anchor: id('discogs-token'),
    file: 'connections-metadata.tsx',
    hint: 'Discogs',
    keywords: 'discogs personal access token',
    side: 'music',
    open: { service: 'discogs' },
  },
  {
    label: 'Qobuz token',
    tab: 'connections',
    anchor: id('qobuz-connection-token'),
    file: 'connections-metadata.tsx',
    hint: 'Qobuz metadata and enrichment',
    keywords: 'qobuz login',
    side: 'music',
    open: { service: 'qobuz' },
  },
  {
    label: 'MusicBrainz server URL',
    tab: 'connections',
    anchor: id('musicbrainz-base-url'),
    file: 'connections-metadata.tsx',
    hint: 'MusicBrainz, or your own mirror',
    keywords: 'musicbrainz mirror local',
    side: 'music',
    open: { service: 'musicbrainz' },
  },
  {
    label: 'MusicBrainz request rate',
    tab: 'connections',
    anchor: id('musicbrainz-rate-mode'),
    file: 'connections-metadata.tsx',
    hint: 'requests per second',
    keywords: 'musicbrainz rate limit speed',
    side: 'music',
    open: { service: 'musicbrainz' },
  },
  {
    label: 'Hydrabase',
    tab: 'connections',
    anchor: id('hydrabase-url'),
    file: 'connections-metadata.tsx',
    hint: 'WebSocket URL and API key',
    keywords: 'hydrabase',
    side: 'music',
    open: { service: 'hydrabase' },
  },
  {
    label: 'Last.fm API key',
    tab: 'connections',
    anchor: id('lastfm-api-key'),
    file: 'connections-extras.tsx',
    hint: 'Last.fm',
    keywords: 'lastfm last fm scrobble secret',
    side: 'music',
    open: { service: 'lastfm' },
  },
  {
    label: 'Scrobble to Last.fm',
    tab: 'connections',
    anchor: id('lastfm-scrobble-enabled'),
    file: 'connections-extras.tsx',
    hint: 'Last.fm',
    keywords: 'lastfm scrobbling plays',
    side: 'music',
    open: { service: 'lastfm' },
  },
  {
    label: 'ListenBrainz token',
    tab: 'connections',
    anchor: id('listenbrainz-token'),
    file: 'connections-extras.tsx',
    hint: 'ListenBrainz',
    keywords: 'listenbrainz scrobble user token',
    side: 'music',
    open: { service: 'listenbrainz' },
  },
  {
    label: 'ListenBrainz address (self-hosted, Maloja, Koito)',
    tab: 'connections',
    anchor: id('listenbrainz-base-url'),
    file: 'connections-extras.tsx',
    hint: 'ListenBrainz',
    keywords: 'listenbrainz maloja koito self-hosted scrobbler api base url server',
    side: 'music',
    open: { service: 'listenbrainz' },
  },
  {
    label: 'Genius access token',
    tab: 'connections',
    anchor: id('genius-access-token'),
    file: 'connections-extras.tsx',
    hint: 'lyrics and annotations',
    keywords: 'genius lyrics',
    side: 'music',
    open: { service: 'genius' },
  },
  {
    label: 'Link your AcoustID account',
    tab: 'connections',
    anchor: id('acoustid-link-row'),
    file: 'connections-extras.tsx',
    hint: 'needed for lookups from 2027',
    keywords: 'acoustid account link user key fingerprint',
    side: 'music',
    open: { service: 'acoustid' },
  },
  {
    label: 'AcoustID user key',
    tab: 'connections',
    anchor: id('acoustid-user-key'),
    file: 'connections-extras.tsx',
    hint: 'your own AcoustID key',
    keywords: 'acoustid user api key account',
    side: 'music',
    open: { service: 'acoustid' },
  },
  {
    label: 'AcoustID API key',
    tab: 'connections',
    anchor: id('acoustid-api-key'),
    file: 'connections-extras.tsx',
    hint: 'fingerprint verification',
    keywords: 'acoustid fingerprint chromaprint verify',
    side: 'music',
    open: { service: 'acoustid' },
  },
  {
    label: 'Pollinations API key',
    tab: 'connections',
    anchor: id('pollinations-api-key'),
    file: 'connections-extras.tsx',
    hint: 'playlist cover art',
    keywords: 'pollinations ai cover art',
    side: 'music',
    open: { service: 'pollinations' },
  },
  {
    label: 'fanart.tv API key',
    tab: 'connections',
    anchor: id('artist-fanart-api-key'),
    file: 'connections-extras.tsx',
    hint: 'artist artwork',
    keywords: 'fanart artist images',
    side: 'music',
    open: { service: 'artist-fanart' },
  },
  {
    label: 'Concerts near you',
    tab: 'connections',
    anchor: id('concerts-country'),
    file: 'connections-extras.tsx',
    hint: 'Ticketmaster and Setlist.fm',
    keywords: 'concerts gigs tours ticketmaster setlist country',
    side: 'music',
    open: { service: 'concerts' },
  },
  {
    label: 'MetaSync',
    tab: 'connections',
    anchor: { id: 'metasync-node-url' },
    file: 'connections-metasync.tsx',
    hint: 'the shared metadata network',
    keywords: 'metasync node pool network',
  },
  {
    label: 'TMDB',
    tab: 'connections',
    anchor: heading('TMDB'),
    file: 'connections-video-api.tsx',
    hint: 'movie and show details',
    keywords: 'tmdb themoviedb api key',
    side: 'video',
    open: { service: 'tmdb', kind: 'video' },
  },
  {
    label: 'TVDB',
    tab: 'connections',
    anchor: heading('TVDB'),
    file: 'connections-video-api.tsx',
    hint: 'episode numbering',
    keywords: 'tvdb thetvdb api key anime',
    side: 'video',
    open: { service: 'tvdb', kind: 'video' },
  },
  {
    label: 'OpenSubtitles',
    tab: 'connections',
    anchor: heading('OpenSubtitles (Subtitles)'),
    file: 'connections-video-api.tsx',
    hint: 'subtitles',
    keywords: 'subtitles subs opensubtitles login',
    side: 'video',
    open: { service: 'opensubtitles', kind: 'video' },
  },
  {
    label: 'OMDb',
    tab: 'connections',
    anchor: heading('OMDb (Ratings)'),
    file: 'connections-video-api.tsx',
    hint: 'IMDb and Rotten Tomatoes ratings',
    keywords: 'omdb imdb rotten tomatoes ratings',
    side: 'video',
    open: { service: 'omdb', kind: 'video' },
  },
  {
    label: 'Trakt',
    tab: 'connections',
    anchor: heading('Trakt (Audience Rating)'),
    file: 'connections-video-api.tsx',
    hint: 'audience rating',
    keywords: 'trakt',
    side: 'video',
    open: { service: 'trakt', kind: 'video' },
  },
  {
    label: 'Notifications',
    tab: 'connections',
    anchor: heading('Notifications'),
    file: 'video-row-lists.tsx',
    hint: 'Discord, Telegram, ntfy and more',
    keywords: 'notify discord telegram pushbullet gotify ntfy webhook alerts',
    side: 'video',
  },

  // ── Sources ──
  {
    label: 'Soulseek (slskd) URL',
    tab: 'downloads',
    anchor: id('soulseek-url'),
    file: 'source-panels.tsx',
    hint: 'slskd',
    keywords: 'slskd soulseek address host',
    open: { source: 'soulseek' },
  },
  {
    label: 'Soulseek (slskd) API key',
    tab: 'downloads',
    anchor: id('soulseek-api-key'),
    file: 'source-panels.tsx',
    hint: 'slskd',
    keywords: 'slskd soulseek key',
    open: { source: 'soulseek' },
  },
  {
    label: 'Minimum peer upload speed',
    tab: 'downloads',
    anchor: id('soulseek-min-peer-speed'),
    file: 'source-panels.tsx',
    hint: 'Soulseek',
    keywords: 'slskd slow uploaders speed',
    open: { source: 'soulseek' },
  },
  {
    label: 'Max peer queue length',
    tab: 'downloads',
    anchor: id('soulseek-max-peer-queue'),
    file: 'source-panels.tsx',
    hint: 'Soulseek',
    keywords: 'slskd queued uploaders',
    open: { source: 'soulseek' },
  },
  {
    label: 'slskd cleanup scope',
    tab: 'downloads',
    anchor: id('soulseek-cleanup-scope'),
    file: 'source-panels.tsx',
    hint: 'Soulseek',
    keywords: 'slskd clear finished transfers',
    open: { source: 'soulseek' },
  },
  {
    label: 'Lidarr URL',
    tab: 'downloads',
    anchor: id('lidarr-url'),
    file: 'source-panels.tsx',
    hint: 'Lidarr',
    keywords: 'lidarr api key',
    open: { source: 'lidarr' },
  },
  {
    label: 'Qobuz download token',
    tab: 'downloads',
    anchor: id('qobuz-download-token'),
    file: 'source-panels.tsx',
    hint: 'Qobuz downloads',
    keywords: 'qobuz login hires',
    open: { source: 'qobuz' },
  },
  {
    label: 'SoundCloud OAuth token',
    tab: 'downloads',
    anchor: id('soundcloud-download-oauth-token'),
    file: 'source-panels.tsx',
    hint: 'SoundCloud',
    keywords: 'soundcloud go',
    open: { source: 'soundcloud' },
  },
  {
    label: 'YouTube browser cookies',
    tab: 'downloads',
    anchor: id('youtube-cookies-browser'),
    file: 'source-panels-more.tsx',
    hint: 'YouTube',
    keywords: 'youtube cookies sign in bot age restricted yt-dlp',
    open: { source: 'youtube' },
  },
  {
    label: 'yt-dlp release channel',
    tab: 'downloads',
    anchor: id('ytdlp-channel'),
    file: 'source-panels-more.tsx',
    hint: 'YouTube',
    keywords: 'ytdlp youtube nightly stable update',
    open: { source: 'youtube' },
  },
  {
    label: 'Download whole albums from YouTube Music',
    tab: 'downloads',
    anchor: id('youtube-album-bundle'),
    file: 'source-panels-more.tsx',
    hint: 'YouTube',
    keywords: 'youtube music album',
    open: { source: 'youtube' },
  },
  {
    label: 'Prowlarr',
    tab: 'downloads',
    anchor: id('prowlarr-url'),
    file: 'source-panels-more.tsx',
    hint: 'indexers for torrents and usenet',
    keywords: 'prowlarr indexers jackett api key',
    open: { source: 'prowlarr' },
  },
  {
    label: 'Torrent client',
    tab: 'downloads',
    anchor: id('torrent-client-url'),
    file: 'source-panels-more.tsx',
    hint: 'qBittorrent, Transmission, Deluge',
    keywords: 'torrent qbittorrent transmission deluge client',
    open: { source: 'torrent' },
  },
  {
    label: 'Torrent download folder',
    tab: 'downloads',
    anchor: id('torrent-download-path'),
    file: 'source-panels-more.tsx',
    hint: 'where the client saves',
    keywords: 'torrent path save folder',
    open: { source: 'torrent' },
  },
  {
    label: 'Usenet client',
    tab: 'downloads',
    anchor: id('usenet-client-url'),
    file: 'source-panels-more.tsx',
    hint: 'SABnzbd or NZBGet',
    keywords: 'usenet sabnzbd nzbget nzb client',
    open: { source: 'usenet' },
  },
  {
    label: 'Usenet download folder',
    tab: 'downloads',
    anchor: id('usenet-download-path'),
    file: 'source-panels-more.tsx',
    hint: 'where the client saves',
    keywords: 'usenet sabnzbd path folder',
    open: { source: 'usenet' },
  },

  // ── Downloads ──
  {
    label: 'Download Behaviour',
    tab: 'downloads',
    anchor: heading('Download behaviour'),
    file: 'downloads/behaviour-view.tsx',
    hint: 'concurrency, timeouts, preview source',
    keywords: 'downloads queue',
  },
  {
    label: 'Concurrent downloads',
    tab: 'downloads',
    anchor: id('max-concurrent-downloads'),
    file: 'downloads/behaviour-view.tsx',
    hint: 'how many at once',
    keywords: 'parallel simultaneous at once',
  },
  {
    label: 'Source search timeout',
    tab: 'downloads',
    anchor: id('source-search-timeout'),
    file: 'downloads/behaviour-view.tsx',
    hint: 'seconds per source',
    keywords: 'search slow timeout',
  },
  {
    label: 'Stream and preview source',
    tab: 'downloads',
    anchor: id('stream-source'),
    file: 'downloads/behaviour-view.tsx',
    hint: 'what plays a preview',
    keywords: 'preview play stream',
  },
  {
    label: 'Retry Logic',
    tab: 'downloads',
    anchor: heading('Retry logic'),
    file: 'downloads/behaviour-view.tsx',
    hint: 'next-best candidate, exhaustive retry',
    keywords: 'retry retries failed mismatch exhaustive',
  },
  {
    label: 'Album Publishing',
    tab: 'downloads',
    anchor: heading('Album publishing'),
    file: 'downloads/behaviour-view.tsx',
    hint: 'publish albums only when complete',
    keywords: 'atomic complete albums partial',
  },
  {
    label: 'Filtering',
    tab: 'downloads',
    anchor: heading('Explicit content'),
    file: 'downloads/filtering-view.tsx',
    hint: 'explicit, size, genres',
    keywords: 'filter block',
  },
  {
    label: 'Allow explicit content',
    tab: 'downloads',
    anchor: id('allow-explicit'),
    file: 'downloads/filtering-view.tsx',
    hint: 'Filtering',
    keywords: 'explicit clean parental',
  },
  {
    label: 'Maximum music download size',
    tab: 'downloads',
    anchor: id('music-max-mb-per-minute'),
    file: 'downloads/filtering-view.tsx',
    hint: 'MB per minute',
    keywords: 'size limit large files',
  },
  {
    label: 'Preferred version',
    tab: 'downloads',
    anchor: id('preferred-version'),
    file: 'downloads/filtering-view.tsx',
    hint: 'remaster, deluxe, original',
    keywords: 'version remaster deluxe live',
  },
  {
    label: 'Genre whitelist',
    tab: 'downloads',
    anchor: id('genre-whitelist-enabled'),
    file: 'downloads/filtering-view.tsx',
    hint: 'strict genre filtering',
    keywords: 'genres allow list',
  },
  {
    label: 'Download source order',
    tab: 'downloads',
    anchor: heading('Download order'),
    file: 'downloads/sources-view.tsx',
    hint: 'which source is tried first',
    keywords: 'chain hybrid priority order fallback sources',
  },

  // ── Quality ──
  {
    label: 'Music quality profile',
    tab: 'quality',
    anchor: heading('Quality profiles'),
    file: 'quality-tab.tsx',
    hint: 'formats, bit depth, bitrate',
    keywords: 'flac mp3 lossless hires bitrate profile quality',
  },
  {
    label: 'Upgrade until cutoff',
    tab: 'quality',
    anchor: id('quality-upgrade-cutoff'),
    file: 'quality-tab.tsx',
    hint: 'keep upgrading until this',
    keywords: 'upgrade cutoff better quality',
  },
  {
    label: 'Accept off-list quality',
    tab: 'quality',
    anchor: id('quality-fallback-enabled'),
    file: 'quality-tab.tsx',
    hint: 'when nothing on the list exists',
    keywords: 'fallback quality',
  },
  {
    label: 'Replace lower quality files on import',
    tab: 'quality',
    anchor: id('import-replace-lower-quality'),
    file: 'quality-tab.tsx',
    hint: 'Quality on Import',
    keywords: 'upgrade replace import',
  },
  {
    label: 'Downsample Hi-Res FLAC',
    tab: 'quality',
    anchor: id('downsample-hires'),
    file: 'quality-tab.tsx',
    hint: 'to CD quality',
    keywords: '24 bit 16 bit 44.1 hires downsample',
  },
  {
    label: 'Lossy copy of FLAC downloads',
    tab: 'quality',
    anchor: id('lossy-copy-enabled'),
    file: 'quality-tab.tsx',
    hint: 'Post-Download Conversion',
    keywords: 'mp3 opus aac convert transcode lossy copy',
  },
  {
    label: 'Only AcoustID-verified tracks',
    tab: 'quality',
    anchor: id('acoustid-require-verified'),
    file: 'quality-tab.tsx',
    hint: 'Audio Verification',
    keywords: 'acoustid fingerprint verify wrong song',
  },
  {
    label: 'Verify real audio with ffmpeg',
    tab: 'quality',
    anchor: id('audio-completeness-check'),
    file: 'quality-tab.tsx',
    hint: 'Audio Verification',
    keywords: 'truncated broken files ffmpeg',
  },
  {
    label: 'Video quality profiles',
    tab: 'quality',
    anchor: id('vq-profile-select'),
    file: 'video-quality.tsx',
    hint: 'resolutions, sources, custom formats',
    keywords: '1080p 2160p 4k remux bluray web quality profile movies tv',
  },
  {
    label: 'Custom formats',
    tab: 'quality',
    anchor: heading('Custom formats'),
    file: 'video-quality.tsx',
    hint: 'scores for release tags',
    keywords: 'custom formats score hdr dolby vision x265',
  },
  {
    label: 'YouTube quality',
    tab: 'quality',
    anchor: id('yq-resolution'),
    file: 'video-quality.tsx',
    hint: 'channel downloads',
    keywords: 'youtube resolution',
  },

  // ── Library ──
  {
    label: 'Download path',
    tab: 'library',
    anchor: id('download-path'),
    file: 'library-folders.tsx',
    hint: 'where downloads arrive (input)',
    keywords: 'download folder input slskd downloads path docker',
  },
  {
    label: 'Music library folder',
    tab: 'library',
    anchor: id('transfer-path'),
    file: 'library-folders.tsx',
    hint: 'where finished music is filed (output)',
    keywords: 'transfer library folder output music path destination',
  },
  {
    label: 'Import folder',
    tab: 'library',
    anchor: id('staging-path'),
    file: 'library-folders.tsx',
    hint: 'files to import',
    keywords: 'import staging folder path',
  },
  {
    label: 'Music videos folder',
    tab: 'library',
    anchor: id('music-videos-path'),
    file: 'library-folders.tsx',
    hint: 'Folders',
    keywords: 'music videos path',
  },
  {
    label: 'Podcasts folder',
    tab: 'library',
    anchor: id('podcasts-path'),
    file: 'library-folders.tsx',
    hint: 'where episodes live',
    keywords: 'podcasts path',
  },
  {
    label: 'Audiobooks folder',
    tab: 'library',
    anchor: id('audiobooks-path'),
    file: 'library-audiobooks.tsx',
    hint: 'where finished books live',
    keywords: 'audiobooks path',
  },
  {
    label: 'M3U path prefix rewrite',
    tab: 'library',
    anchor: id('m3u-entry-base-path'),
    file: 'library-folders.tsx',
    hint: 'playlist paths for your player',
    keywords: 'm3u playlist path prefix',
  },
  {
    label: 'Video library folders',
    tab: 'library',
    anchor: heading("Your library's folders"),
    file: 'library-video-folders.tsx',
    hint: 'paths for movies and TV',
    keywords: 'movies tv shows folders path drives docker',
  },
  {
    label: 'Video download folder',
    tab: 'library',
    anchor: id('video-download-path'),
    file: 'library-video-folders.tsx',
    hint: 'where video downloads arrive',
    keywords: 'movies tv download path',
  },
  {
    label: '4K libraries',
    tab: 'library',
    anchor: heading('4K libraries'),
    file: 'library-video-folders.tsx',
    hint: 'a second copy in 4K',
    keywords: '4k uhd 2160p library',
  },
  {
    label: 'Extra video folders',
    tab: 'library',
    anchor: heading('Extra folders'),
    file: 'library-video-folders.tsx',
    hint: 'more movie and TV folders to manage',
    keywords: 'additional extra movies tv folders second drive standalone',
  },
  {
    label: 'Album path template',
    tab: 'library',
    anchor: id('template-album-path'),
    file: 'library-organization.tsx',
    hint: 'how files are named',
    keywords: 'naming template folder structure rename organize',
  },
  {
    label: 'Single path template',
    tab: 'library',
    anchor: id('template-single-path'),
    file: 'library-organization.tsx',
    hint: 'how singles are named',
    keywords: 'naming template singles',
  },
  {
    label: 'Compilation path template',
    tab: 'library',
    anchor: id('template-compilation-path'),
    file: 'library-organization.tsx',
    hint: 'various artists',
    keywords: 'naming template compilations various artists',
  },
  {
    label: 'Playlist path template',
    tab: 'library',
    anchor: id('template-playlist-path'),
    file: 'library-organization.tsx',
    hint: 'playlist folders',
    keywords: 'naming template playlists',
  },
  {
    label: 'Multi-disc folder label',
    tab: 'library',
    anchor: id('disc-label'),
    file: 'library-organization.tsx',
    hint: 'CD 1, Disc 1',
    keywords: 'disc cd multi',
  },
  {
    label: 'Artist tag separator',
    tab: 'library',
    anchor: id('artist-separator'),
    file: 'library-organization.tsx',
    hint: 'how several artists are joined',
    keywords: 'featuring feat multiple artists tags',
  },
  {
    label: 'Collaborative album artist',
    tab: 'library',
    anchor: id('collab-artist-mode'),
    file: 'library-organization.tsx',
    hint: 'albums by two artists',
    keywords: 'collab collaboration album artist',
  },
  {
    label: 'Movie naming template',
    tab: 'library',
    anchor: id('vo-movie-template'),
    file: 'library-video-organization.tsx',
    hint: 'how movie files are named',
    keywords: 'naming template rename movies',
  },
  {
    label: 'Episode naming template',
    tab: 'library',
    anchor: id('vo-episode-template'),
    file: 'library-video-organization.tsx',
    hint: 'how episodes are named',
    keywords: 'naming template rename tv episodes',
  },
  {
    label: 'Anime episode naming template',
    tab: 'library',
    anchor: id('vo-anime-episode-template'),
    file: 'library-video-organization.tsx',
    hint: 'how anime episodes are named',
    keywords: 'naming template rename anime absolute episodes',
  },
  {
    label: 'Recycle deleted video files',
    tab: 'library',
    anchor: id('vo-recycle'),
    file: 'library-video-organization.tsx',
    hint: 'instead of erasing them',
    keywords: 'recycle bin trash delete',
  },
  {
    label: 'Download subtitles',
    tab: 'library',
    anchor: id('vo-subs-dl'),
    file: 'library-video-organization.tsx',
    hint: 'subtitles for movies and TV',
    keywords: 'subtitles subs srt languages',
  },
  {
    label: 'Post-Processing',
    tab: 'library',
    anchor: id('metadata-enabled'),
    file: 'library-processing.tsx',
    hint: 'tagging, artwork, lyrics',
    keywords: 'tags tagging cover art lyrics replaygain',
  },
  {
    label: 'Import',
    tab: 'library',
    anchor: id('import-folder-artist-override'),
    file: 'library-processing.tsx',
    hint: 'auto-import and matching',
    keywords: 'auto import match',
  },
  {
    label: 'Import default search source',
    tab: 'library',
    anchor: id('import-default-search-source'),
    file: 'library-processing.tsx',
    hint: 'where Import searches first',
    keywords: 'import search source musicbrainz default identify match any source',
  },
  {
    label: 'Duration tolerance',
    tab: 'library',
    anchor: id('duration-tolerance-seconds'),
    file: 'library-processing.tsx',
    hint: 'seconds a match may differ',
    keywords: 'length duration match tolerance',
  },
  {
    label: 'Audible marketplace',
    tab: 'library',
    anchor: id('audiobook-marketplace'),
    file: 'library-audiobooks.tsx',
    hint: 'Audiobooks',
    keywords: 'audible region country',
  },

  // ── Discovery ──
  {
    label: 'Playlist sync mode',
    tab: 'discovery',
    anchor: id('playlist-sync-mode'),
    file: 'discovery-tab.tsx',
    hint: 'Playlists',
    keywords: 'sync mirror playlists',
  },
  {
    label: 'Add missing tracks to the wishlist during sync',
    tab: 'discovery',
    anchor: id('playlist-sync-wishlist-missing'),
    file: 'discovery-tab.tsx',
    hint: 'Playlists',
    keywords: 'wishlist missing sync',
  },
  {
    label: 'Auto-save M3U files',
    tab: 'discovery',
    anchor: id('m3u-export-enabled'),
    file: 'discovery-tab.tsx',
    hint: 'Playlists',
    keywords: 'm3u export playlist file',
  },
  {
    label: 'Listening stats',
    tab: 'discovery',
    anchor: heading('Listening stats'),
    file: 'discovery-tab.tsx',
    hint: 'poll your server for plays',
    keywords: 'listening history plays stats scrobbles',
  },
  {
    label: 'Discovery lookback period',
    tab: 'discovery',
    anchor: id('discovery-lookback-period'),
    file: 'discovery-tab.tsx',
    hint: 'how far back to scan',
    keywords: 'new releases lookback discover',
    side: 'music',
  },
  {
    label: 'Hemisphere',
    tab: 'discovery',
    anchor: id('discovery-hemisphere'),
    file: 'discovery-tab.tsx',
    hint: 'seasonal playlists',
    keywords: 'seasons north south',
    side: 'music',
  },
  {
    label: 'Adventurousness',
    tab: 'discovery',
    anchor: id('discover-adventurousness'),
    file: 'discovery-tab.tsx',
    hint: 'safe or surprising picks',
    keywords: 'recommendations discover',
  },
  {
    label: 'Import lists',
    tab: 'discovery',
    anchor: heading('Import lists'),
    file: 'video-row-lists.tsx',
    hint: 'Trakt, IMDb, Letterboxd lists',
    keywords: 'lists trakt imdb letterboxd mdblist import',
    side: 'video',
  },

  // ── Appearance ──
  {
    label: 'Accent color',
    tab: 'appearance',
    anchor: id('accent-preset'),
    file: 'appearance-tab.tsx',
    hint: 'the app color',
    keywords: 'theme colour color accent',
  },
  {
    label: 'Sidebar visualizer',
    tab: 'appearance',
    anchor: id('sidebar-visualizer-type'),
    file: 'appearance-tab.tsx',
    hint: 'audio reactive edge',
    keywords: 'visualizer animation',
  },
  {
    label: 'Background particles',
    tab: 'appearance',
    anchor: id('particles-enabled'),
    file: 'appearance-tab.tsx',
    hint: 'eye candy',
    keywords: 'particles animation gpu',
  },
  {
    label: 'Reduce visual effects',
    tab: 'appearance',
    anchor: id('reduce-effects-enabled'),
    file: 'appearance-tab.tsx',
    hint: 'calmer and faster',
    keywords: 'animations motion performance slow',
  },
  {
    label: 'Maximum performance',
    tab: 'appearance',
    anchor: id('max-performance-enabled'),
    file: 'appearance-tab.tsx',
    hint: 'every effect off',
    keywords: 'performance slow lag fast',
  },
  {
    label: 'Sidebar weather',
    tab: 'appearance',
    anchor: id('sw-enabled'),
    file: 'appearance-tab.tsx',
    hint: 'live weather behind the sidebar',
    keywords: 'weather rain snow location holidays',
  },
  {
    label: 'Show only what you use',
    tab: 'appearance',
    anchor: heading('Show only what you use'),
    file: 'appearance-tab.tsx',
    hint: 'hide pages you never open',
    keywords: 'customize sidebar hide features pages',
  },

  // ── System ──
  {
    label: 'Autosave',
    tab: 'advanced',
    anchor: id('settings-autosave-enabled'),
    file: 'system-tab.tsx',
    hint: 'General',
    keywords: 'save automatically',
  },
  {
    label: 'Log level',
    tab: 'advanced',
    anchor: id('log-level-select'),
    file: 'system-tab.tsx',
    hint: 'General, Logging',
    keywords: 'debug verbose logging logs',
  },
  {
    label: 'Concurrent workers',
    tab: 'advanced',
    anchor: id('max-workers'),
    file: 'system-tab.tsx',
    hint: 'General, Database updates',
    keywords: 'workers threads database',
  },
  {
    // the id it had before settings-bic phase 6: recents remember it
    id: 'setting:advanced:database-maintenance',
    label: 'Database maintenance',
    tab: 'advanced',
    anchor: id('db-maintenance-info'),
    file: 'system-tab.tsx',
    hint: 'size, tidy up, biggest tables',
    keywords: 'database vacuum sqlite size compact wal tidy tables',
  },
  {
    label: 'Compact the database',
    tab: 'advanced',
    anchor: id('db-vacuum-btn'),
    file: 'system-tab.tsx',
    hint: 'Maintenance, danger zone',
    keywords: 'vacuum compact shrink database',
  },
  {
    label: 'Clear quarantine',
    tab: 'advanced',
    anchor: id('quarantine-clear-btn'),
    file: 'system-tab.tsx',
    hint: 'Maintenance, danger zone',
    keywords: 'quarantine acoustid failed downloads delete',
  },
  {
    // the id it had before settings-bic phase 6: recents remember it
    id: 'setting:advanced:soulsync-api',
    label: 'API keys',
    tab: 'advanced',
    anchor: heading('API keys'),
    file: 'system-tab.tsx',
    hint: 'REST keys and inbound endpoints',
    keywords: 'api key rest token lidarr seerr overseerr mcp',
  },
  {
    label: 'Artwork cache',
    tab: 'advanced',
    anchor: heading('Artwork cache'),
    file: 'system-tab.tsx',
    hint: 'local copies of cover art',
    keywords: 'image cache artwork covers posters size',
  },
  {
    label: 'Developer mode',
    tab: 'advanced',
    anchor: heading('Developer mode'),
    file: 'system-tab.tsx',
    hint: 'advanced log viewer',
    keywords: 'developer debug',
  },
  {
    // the id it had before settings-bic phase 6: recents remember it
    id: 'setting:advanced:security-access',
    label: 'Security and access',
    tab: 'advanced',
    anchor: heading('Sign-in'),
    file: 'system-security.tsx',
    hint: 'PIN, passwords, sign-in',
    keywords: 'security pin password login lock sign in',
  },
  {
    label: 'Admin PIN',
    tab: 'advanced',
    anchor: id('security-new-pin'),
    file: 'system-security.tsx',
    hint: 'Security, Lock with a PIN',
    keywords: 'pin lock code',
  },
  {
    label: 'Login password',
    tab: 'advanced',
    anchor: id('security-login-password'),
    file: 'system-security.tsx',
    hint: 'Security, Sign-in',
    keywords: 'password login sign in',
  },
  {
    // the id it had before settings-bic phase 6: recents remember it
    id: 'setting:advanced:security-oidc-issuer',
    label: 'Single sign-on (OIDC)',
    tab: 'advanced',
    anchor: id('security-oidc-signin'),
    file: 'system-security.tsx',
    hint: 'Authentik, Authelia, Keycloak',
    keywords: 'oidc sso openid authentik authelia keycloak',
  },
  {
    label: 'Reverse proxy auth header',
    tab: 'advanced',
    anchor: id('security-auth-proxy-header'),
    file: 'system-security.tsx',
    hint: 'trusted header sign-in',
    keywords: 'reverse proxy remote user header nginx traefik',
  },
  {
    label: 'Allowed WebSocket origins',
    tab: 'advanced',
    anchor: id('security-cors-origins'),
    file: 'system-security.tsx',
    hint: 'CORS',
    keywords: 'cors websocket origin domain',
  },
  {
    label: 'Experimental features',
    tab: 'advanced',
    anchor: heading('Experimental'),
    file: 'connections-metadata.tsx',
    hint: 'JioSaavn, Bandcamp',
    keywords: 'experimental preview jiosaavn bandcamp',
    side: 'music',
  },

  // ── People (#2166): the parts along the top of the tab. a part's own
  // pane id where it has one, else its button (people-tab.tsx PILLS) ──
  {
    label: 'People',
    tab: 'people',
    anchor: id('ppl-pill-people'),
    file: 'people/people-tab.tsx',
    open: { people: 'people' },
    hint: 'everyone who uses SoulSync here',
    keywords: 'people profiles users members family manage add person edit access permissions',
  },
  {
    label: 'Global policy',
    tab: 'people',
    anchor: id('ppl-pol-pane'),
    file: 'people/policy-screen.tsx',
    open: { people: 'policy' },
    hint: 'the most any member can have',
    keywords: 'policy ceiling limit everyone members cap permissions',
  },
  {
    label: 'Templates',
    tab: 'people',
    anchor: id('ppl-tpl-pane'),
    file: 'people/templates-screen.tsx',
    open: { people: 'templates' },
    hint: 'Adult, Teen, Kids, Guest and your own',
    keywords: 'template preset adult teen kids guest presets permissions sidebar look',
  },
  {
    id: 'setting:people:signup-template',
    label: 'New people start as',
    tab: 'people',
    anchor: id('ppl-tpl-pane'),
    file: 'people/templates-screen.tsx',
    open: { people: 'templates' },
    hint: 'the template for Plex and OIDC sign-ups',
    keywords: 'signup sign up register new profiles plex oidc default template start',
  },
  {
    id: 'setting:people:moderator',
    label: 'Moderator',
    tab: 'people',
    anchor: id('ppl-tpl-pane'),
    file: 'people/templates-screen.tsx',
    open: { people: 'templates' },
    hint: 'Requests & moderation, in a template or a person',
    keywords: 'moderator moderate approve decline requests issues see everyone',
  },
  {
    id: 'setting:people:request-quality',
    label: 'Request quality',
    tab: 'people',
    anchor: id('ppl-pol-pane'),
    file: 'people/policy-screen.tsx',
    open: { people: 'policy' },
    hint: 'quality members may pick, 4K always asks',
    keywords: 'quality 4k request ask needs my ok starts on movies tv profile',
  },
  {
    label: 'Invites',
    tab: 'people',
    anchor: id('ppl-pill-invites'),
    file: 'people/people-tab.tsx',
    open: { people: 'invites' },
    hint: 'links that let someone join',
    keywords: 'invite link join share token',
  },
  {
    label: 'Activity',
    tab: 'people',
    anchor: id('ppl-pill-activity'),
    file: 'people/people-tab.tsx',
    open: { people: 'activity' },
    hint: 'who changed what',
    keywords: 'activity audit log history changes',
  },

  // ── Devices: listen anywhere (devices/devices-tab.tsx) ──
  {
    label: 'Add a device',
    tab: 'devices',
    anchor: id('devices-add'),
    file: 'devices/devices-tab.tsx',
    hint: 'pair a phone, car or speaker app with a code',
    keywords:
      'device devices pair pairing code qr phone app car speaker listen anywhere unpair revoke',
  },
  {
    label: 'Set up a music app',
    tab: 'devices',
    anchor: id('music-app-set-up'),
    file: 'devices/music-apps.tsx',
    hint: 'play your library in a Subsonic or OpenSubsonic app',
    keywords:
      'music app apps subsonic opensubsonic app password api key username server stream offline symfonium feishin amperfy',
  },
  {
    label: "Stream files SoulSync can't read through your media server",
    tab: 'devices',
    anchor: id('music-app-format'),
    file: 'devices/music-apps.tsx',
    hint: 'music apps: convert to mp3 or opus, and play through plex, jellyfin or navidrome',
    keywords:
      'transcode convert mp3 opus bitrate bridge media server plex jellyfin navidrome cant read',
  },
  {
    label: 'Songs converted at once',
    tab: 'devices',
    anchor: id('music-app-slots'),
    file: 'devices/music-apps.tsx',
    hint: 'music apps: how many songs convert together, one past that waits its turn',
    keywords: 'transcode convert slots ffmpeg cpu busy at once mobile data wait',
  },
  {
    id: 'setting:devices:remote-address',
    label: 'Remote address',
    tab: 'devices',
    anchor: id('devices-remote-url'),
    file: 'devices/devices-tab.tsx',
    hint: 'how devices reach this server from outside',
    keywords:
      'remote address domain tunnel port forward reachability reachable outside https tailscale',
  },
  {
    id: 'setting:advanced:server-fingerprint',
    label: 'Server fingerprint',
    tab: 'advanced',
    anchor: id('security-server-fingerprint'),
    file: 'devices/server-fingerprint.tsx',
    hint: "this server's own ID, shown in paired apps",
    keywords: 'fingerprint server identity lookup id key server_key',
  },

  // ── Logs ──
  {
    label: 'Live logs',
    tab: 'advanced',
    anchor: id('log-viewer-autoscroll'),
    file: 'logs-tab.tsx',
    hint: 'streaming log viewer',
    keywords: 'logs errors debug console viewer',
  },
]);

/**
 * A member's own settings: My Account (shell/my-accounts.ts), the one place
 * a member changes anything. The headings are that modal's own ids.
 */
export interface AccountEntry {
  id: string;
  label: string;
  hint: string;
  keywords: string;
  /** a heading id in the My Account modal */
  anchor: string;
}

export const ACCOUNT_ENTRIES: AccountEntry[] = [
  {
    id: 'account:server',
    label: 'Your media server login',
    hint: 'My Account',
    keywords: 'plex jellyfin navidrome user library account login who am i',
    anchor: 'ma-server-h',
  },
  {
    id: 'account:services',
    label: 'Your music services',
    hint: 'My Account · Spotify, Tidal, ListenBrainz, Last.fm',
    keywords: 'spotify tidal listenbrainz lastfm last fm scrobble connect playlists',
    anchor: 'ma-services-h',
  },
  {
    id: 'account:signin',
    label: 'Sign-in',
    hint: 'My Account',
    keywords: 'sso oidc login password',
    anchor: 'ma-signin-h',
  },
  {
    id: 'account:weather',
    label: 'Your sidebar weather',
    hint: 'My Account',
    keywords: 'weather location rain snow',
    anchor: 'ma-weather-h',
  },
  {
    id: 'account:listening',
    label: 'Your listening',
    hint: 'My Account · share listening, private session',
    keywords: 'share listening blend private session privacy scrobble history',
    anchor: 'ma-privacy-h',
  },
];

export function tabTitle(tab: SettingsTab): string {
  return TAB_TITLES[tab]?.title ?? tab;
}

// ── landing on one ─────────────────────────────────────────────────────

/** host.tsx puts the two modal openers on window when Settings mounts */
type Openers = {
  openServiceModal?: (id: string) => void;
  openSourceModal?: (id: string) => void;
  openPeopleSettings?: (part: PeoplePart) => void;
};
const openers = () => window as unknown as Openers;

const SETTLE_MS = 100;
const SETTLE_TRIES = 40;

function wait(test: () => boolean): Promise<boolean> {
  return new Promise((resolve) => {
    let tries = 0;
    const attempt = () => {
      if (test()) return resolve(true);
      tries += 1;
      if (tries >= SETTLE_TRIES) return resolve(false);
      setTimeout(attempt, SETTLE_MS);
    };
    attempt();
  });
}

function textOf(el: Element): string {
  return (el.textContent || '').replace(/\s+/g, ' ').trim().toLowerCase();
}

/** the anchor on the page (in the settings page, or in a modal it opened) */
export function findAnchor(anchor: Anchor, root: ParentNode = document): HTMLElement | null {
  if ('id' in anchor) {
    const el = (root as Document).getElementById
      ? (root as Document).getElementById(anchor.id)
      : root.querySelector<HTMLElement>(`#${CSS.escape(anchor.id)}`);
    return el as HTMLElement | null;
  }
  const want = anchor.heading.toLowerCase();
  const heads = Array.from(
    root.querySelectorAll<HTMLElement>(
      '#settings-page h3, #settings-page h4, #settings-page .service-title, [role="dialog"] h3, [role="dialog"] h4',
    ),
  );
  return heads.find((h) => textOf(h) === want) ?? null;
}

function visible(el: HTMLElement): boolean {
  return !!(el.offsetParent || el.getClientRects().length);
}

/** a collapsed section, a closed media tab, another pill, a closed "More
 *  options": open what hides it */
export function reveal(el: HTMLElement): void {
  // a pill of a rebuilt tab: its panel says which
  const view = el.closest<HTMLElement>('[data-stg-view]');
  if (view?.hidden && view.dataset.stgTab) nav.go(view.dataset.stgTab, view.dataset.stgView);
  // anything hidden with a button that controls it: a "More options" body,
  // a kind's panel (Music / Video)
  for (
    let hidden = el.closest<HTMLElement>('[hidden][id]');
    hidden;
    hidden = hidden.parentElement?.closest<HTMLElement>('[hidden][id]') ?? null
  ) {
    document
      .querySelector<HTMLElement>(
        `[aria-controls="${CSS.escape(hidden.id)}"]:not([aria-expanded="true"]):not([aria-selected="true"])`,
      )
      ?.click();
  }
  // the anchor is a section's own header: open it
  const header = el.closest<HTMLElement>('.settings-section-header');
  if (header?.classList.contains('collapsed')) header.click();
  const body = el.closest<HTMLElement>('.settings-section-body');
  if (body?.classList.contains('collapsed')) {
    const head = body.previousElementSibling as HTMLElement | null;
    if (head?.classList.contains('settings-section-header')) head.click();
    else {
      body.classList.remove('collapsed');
      body.style.display = '';
    }
  }
  // the Library media cards are React tabs: a click moves their own state
  const panel = el.closest<HTMLElement>('[role="tabpanel"]');
  if (panel?.id && (panel.hidden || !visible(panel))) {
    document.querySelector<HTMLElement>(`[aria-controls="${CSS.escape(panel.id)}"]`)?.click();
  }
}

export function flash(el: HTMLElement): void {
  const target = el.closest<HTMLElement>('[data-row], .form-group, .settings-group') ?? el;
  target.scrollIntoView?.({ block: 'center', behavior: 'auto' });
  target.classList.add('stg-search-target');
  window.setTimeout(() => target.classList.remove('stg-search-target'), 2000);
  const control = el.matches('input, select, textarea')
    ? el
    : el.querySelector<HTMLElement>('input, select, textarea');
  // a hidden or disabled control won't take focus: the row takes it instead
  control?.focus({ preventScroll: true });
  if (!control || document.activeElement !== control) {
    if (!target.hasAttribute('tabindex')) target.tabIndex = -1;
    target.focus({ preventScroll: true });
  }
}

/** go to Settings, open the tab (and the modal), and show the field */
export async function landSetting(entry: SettingsEntry, side: 'music' | 'video'): Promise<boolean> {
  const goTo = side === 'video' ? 'video-settings' : 'settings';
  if (goTo === 'video-settings') {
    document.querySelector<HTMLElement>('.nav-button[data-video-page="video-settings"]')?.click();
  } else {
    void window.navigateToPage?.('settings');
  }
  // an entry may still name a tab by a key that went (a saved recent)
  const place = resolveTab(entry.tab);
  const ready = await wait(
    () =>
      !!document.querySelector(`#settings-page .stg-tab[data-tab="${place.tab}"]`) &&
      typeof window.switchSettingsTab === 'function',
  );
  if (!ready) return false;
  window.switchSettingsTab?.(entry.tab);
  if (entry.open && 'service' in entry.open) {
    if (entry.open.kind) window.switchServiceKind?.(entry.open.kind);
    openers().openServiceModal?.(entry.open.service);
  } else if (entry.open && 'source' in entry.open) {
    openers().openSourceModal?.(entry.open.source);
  } else if (entry.open && 'people' in entry.open) {
    openers().openPeopleSettings?.(entry.open.people);
  }
  let el: HTMLElement | null = null;
  await wait(() => {
    el = findAnchor(entry.anchor);
    return !!el;
  });
  if (!el) return false;
  reveal(el);
  // the click above re-renders a React card: find it again before scrolling
  await new Promise((r) => setTimeout(r, 30));
  el = findAnchor(entry.anchor) ?? el;
  flash(el);
  return true;
}

/** open My Account and bring the section up */
export async function landAccount(entry: AccountEntry): Promise<boolean> {
  window.openMyAccountsModal?.();
  const found = await wait(() => !!document.getElementById(entry.anchor));
  const el = document.getElementById(entry.anchor);
  if (!found || !el) return false;
  el.scrollIntoView?.({ block: 'start', behavior: 'auto' });
  return true;
}
