/**
 * Every page's base help: title, one line on what it is for, and the docs
 * articles worth reading there. Article titles and ledes are the docs pages'
 * own (webui/static/docs-content). P5 merges live answers over these.
 */

import type { ArticleRef } from './index';

import { registerPageHelp } from './index';

const A = {
  firstSetup: {
    id: 'gs-first-setup',
    title: 'First-Time Setup',
    lede: 'From a fresh install to a working library',
  },
  interface: {
    id: 'gs-interface',
    title: 'Understanding the Interface',
    lede: 'Where everything lives',
  },
  folders: {
    id: 'gs-folders',
    title: 'Folder Setup (Downloads & Music Library)',
    lede: 'The three folders, and the Docker pitfalls',
  },
  docker: { id: 'gs-docker', title: 'Docker & Deployment', lede: 'Volumes, PUID and PGID, ports' },
  connecting: {
    id: 'gs-connecting',
    title: 'Connecting Services',
    lede: 'What each service is for',
  },
  wfFirst: {
    id: 'wf-first',
    title: 'What Should I Do First?',
    lede: 'Five workflows that cover most of it',
  },
  wfDownload: {
    id: 'wf-download',
    title: 'How to: Download an Album',
    lede: 'Search, pick, download',
  },
  wfAuto: {
    id: 'wf-auto',
    title: 'How to: Set Up Auto-Downloads',
    lede: 'New releases from artists you follow',
  },
  dashOverview: {
    id: 'dash-overview',
    title: 'Overview & Stats',
    lede: 'Library stats, alerts and sync pipelines',
  },
  workers: {
    id: 'dash-workers',
    title: 'Enrichment Workers',
    lede: 'Metadata from every connected service',
  },
  searchEnhanced: {
    id: 'search-enhanced',
    title: 'Enhanced Search',
    lede: 'Artists, albums and tracks from your metadata source',
  },
  searchBasic: {
    id: 'search-basic',
    title: 'Basic Search',
    lede: 'Files straight from your download sources',
  },
  sources: {
    id: 'search-sources',
    title: 'Download Sources',
    lede: 'Soulseek, Tidal, Qobuz, HiFi, YouTube, Deezer and Hybrid',
  },
  manager: {
    id: 'search-manager',
    title: 'Download Manager',
    lede: 'Queue, progress and per-track status',
  },
  downloadsDeep: {
    id: 'dash-downloads-deep',
    title: 'Active Downloads in Depth',
    lede: 'Batches, clients and the review pipeline',
  },
  postprocess: {
    id: 'search-postprocess',
    title: 'Post-Processing Pipeline',
    lede: 'Verified, tagged, organized, scanned',
  },
  quality: { id: 'search-quality', title: 'Quality Profiles', lede: 'What counts as good enough' },
  discHero: {
    id: 'disc-hero',
    title: 'Featured Artists',
    lede: 'Recommended artists from your watchlist',
  },
  discPlaylists: {
    id: 'disc-playlists',
    title: 'Discovery Playlists',
    lede: 'Made from your discovery pool and listening',
  },
  libStandard: {
    id: 'lib-standard',
    title: 'Standard View',
    lede: 'Browse, filter and select your collection',
  },
  libManage: {
    id: 'lib-enhanced',
    title: 'Library Management',
    lede: 'Metadata and file versions',
  },
  libMissing: { id: 'lib-missing', title: 'Download Missing Tracks', lede: 'Fill gaps in albums' },
  syncOverview: {
    id: 'sync-overview',
    title: 'Playlists Overview',
    lede: 'Import from every service you use',
  },
  mirrored: {
    id: 'sync-mirrored',
    title: 'Mirrored Playlists',
    lede: 'Refreshable, schedulable, download-aware',
  },
  explorer: {
    id: 'sync-explorer',
    title: 'Playlist Explorer',
    lede: 'Every playlist in one visual tree',
  },
  watchlist: {
    id: 'art-watchlist',
    title: 'Watchlist',
    lede: 'Follow artists, catch every new release',
  },
  scanning: {
    id: 'art-scanning',
    title: 'New Release Scanning',
    lede: 'Daily scans, or run one now',
  },
  wishlist: { id: 'art-wishlist', title: 'Wishlist', lede: 'Tracks waiting to be downloaded' },
  artistDetail: {
    id: 'art-detail',
    title: 'Artist Detail & Discography',
    lede: 'Completion, filters, per-release downloads',
  },
  label: { id: 'label-page', title: 'The Label Page', lede: 'Releases, ownership and following' },
  importFolder: {
    id: 'imp-setup',
    title: 'Import Folder',
    lede: 'Drop files in, SoulSync files them',
  },
  importInbox: {
    id: 'imp-workflow',
    title: 'The Inbox',
    lede: 'One row per item and what it can do',
  },
  autoOverview: {
    id: 'auto-overview',
    title: 'Automations Overview',
    lede: 'Schedules, events and chained workflows',
  },
  autoBuilder: { id: 'auto-builder', title: 'Builder', lede: 'Trigger, action, follow-ups' },
  autoSystem: { id: 'auto-system', title: 'System Automations', lede: 'Built-in maintenance' },
  repair: {
    id: 'dash-repair',
    title: 'Repair & Maintenance',
    lede: 'Jobs that find and fix library problems',
  },
  backup: {
    id: 'dash-backup',
    title: 'Backup Manager',
    lede: 'Create, download and restore backups',
  },
  stats: {
    id: 'dash-stats-deep',
    title: 'Stats in Depth',
    lede: 'Listening, Year in Listening, library health',
  },
  setServices: {
    id: 'set-services',
    title: 'Service Credentials',
    lede: 'Metadata, matching and enrichment',
  },
  setMedia: {
    id: 'set-media',
    title: 'Media Servers',
    lede: 'Plex, Jellyfin, Navidrome or standalone',
  },
  setDownload: {
    id: 'set-download',
    title: 'Download Settings',
    lede: 'Sources, modes and fallbacks',
  },
  setProcessing: {
    id: 'set-processing',
    title: 'Processing & Organization',
    lede: 'Verified, tagged and filed',
  },
  libIssues: {
    id: 'lib-issues',
    title: 'Library Issues',
    lede: 'Problems people reported by hand',
  },
  requests: { id: 'prof-requests', title: 'Requests', lede: 'Members ask, admins approve' },
  permissions: {
    id: 'prof-permissions',
    title: 'What Each Permission Does',
    lede: 'Every row in the editor, by category',
  },
  people: {
    id: 'prof-people',
    title: 'People and Access',
    lede: 'Policy, templates and personal changes',
  },
  templates: {
    id: 'prof-templates',
    title: 'Templates',
    lede: 'Adult, Teen, Kids, Guest and your own',
  },
  reqQuality: {
    id: 'prof-quality',
    title: 'Request Quality',
    lede: 'Which quality each person can ask for',
  },
  members: {
    id: 'prof-members',
    title: 'What Members See',
    lede: 'Only what they can use',
  },
  accounts: {
    id: 'prof-accounts',
    title: 'Your Accounts',
    lede: 'Your own services and server identity',
  },
  features: {
    id: 'prof-features',
    title: 'Show Only What You Use',
    lede: 'Hide the parts you do not need',
  },
  tsCommon: { id: 'ts-common', title: 'Common Issues', lede: 'Quick fixes for frequent problems' },
  tsLogs: { id: 'ts-logs', title: 'Logs', lede: 'Where the logs are and what each holds' },
  tsReporting: {
    id: 'ts-reporting',
    title: 'Getting Help',
    lede: 'Report a bug so it gets fixed fast',
  },
  chatRooms: {
    id: 'chat-rooms',
    title: 'Chat Rooms',
    lede: 'Soulseek rooms and the community room',
  },
  podcasts: {
    id: 'pod-overview',
    title: 'Podcasts: Overview',
    lede: 'Follow shows, auto-download episodes',
  },
  audiobooks: {
    id: 'abk-overview',
    title: 'Audiobooks: Overview',
    lede: 'Browse, wishlist, grab, keep a library',
  },
  studio: {
    id: 'studio-overview',
    title: 'Sample Studio Overview',
    lede: 'Chop, pitch and stash samples',
  },
  hydra: { id: 'hydra-overview', title: 'What Hydrabase Is', lede: 'A P2P metadata network' },
  vidWhat: {
    id: 'vid-what',
    title: 'What the Video Side Is',
    lede: 'Movies and TV, built into SoulSync',
  },
  vidSources: {
    id: 'vid-sources',
    title: 'Video Download Sources',
    lede: 'Indexers, clients and library paths',
  },
  vdash: { id: 'vdash-overview', title: 'Video Dashboard', lede: 'What you watch, and the system' },
  vsearch: { id: 'vsearch-search', title: 'Searching', lede: 'Movies, shows, people and studios' },
  vdisc: { id: 'vdisc-foryou', title: 'For You & Taste', lede: 'Rows built from your library' },
  vlib: { id: 'vlib-browse', title: 'Browsing & Filters', lede: 'Posters, titles or files' },
  vlibManage: {
    id: 'vlib-manage',
    title: 'Manage Panel',
    lede: 'Metadata, locks, profiles, monitoring',
  },
  vwatch: {
    id: 'vwatch-follow',
    title: 'Following People & Studios',
    lede: 'New titles flow into your wishlist',
  },
  vwish: {
    id: 'vwish-wanted',
    title: 'Wanted & Cutoff-Unmet',
    lede: 'What the video side still wants',
  },
  vdl: {
    id: 'vdl-queue',
    title: 'The Download Queue',
    lede: 'Speed, ETA and state for everything in flight',
  },
  vdlQuality: {
    id: 'vdl-quality',
    title: 'Quality Profiles & Formats',
    lede: 'Ladders, cutoffs, custom formats',
  },
  vreq: {
    id: 'vreq-flow',
    title: 'Request, approve, wishlist',
    lede: 'How a request becomes a download',
  },
  vcal: {
    id: 'vcal-grid',
    title: 'Week, Agenda & Month',
    lede: 'Every airing and release, and where it stands',
  },
  vauto: {
    id: 'vauto-shared',
    title: 'Video Automations',
    lede: 'Scheduled tasks and event workflows',
  },
  vtools: {
    id: 'vtool-repair',
    title: 'Library Maintenance',
    lede: 'Find library problems and fix them',
  },
  vimport: {
    id: 'vimp-folder',
    title: 'Video Import Folder',
    lede: 'Drop files in, they get filed',
  },
  vset: {
    id: 'vset-server',
    title: 'Server & Libraries',
    lede: 'Connect Plex or Jellyfin, pick libraries',
  },
  vaccess: { id: 'vid-access', title: 'Per-Profile Side Access', lede: 'Music, video, or both' },
} satisfies Record<string, ArticleRef>;

registerPageHelp({
  id: 'dashboard',
  icon: 'home',
  title: 'Dashboard',
  summary: 'Your week, the services SoulSync talks to, and what is running right now.',
  articles: [A.dashOverview, A.wfFirst],
  adminArticles: [A.workers],
});
registerPageHelp({
  id: 'search',
  icon: 'search',
  title: 'Search',
  summary:
    'Find an artist, album or track and download it, from your metadata source or straight from your download sources.',
  articles: [A.searchEnhanced, A.wfDownload, A.searchBasic],
});
registerPageHelp({
  id: 'discover',
  icon: 'compass',
  title: 'Discover',
  summary: 'New music picked from the artists you follow and what you listen to.',
  articles: [A.discHero, A.discPlaylists],
});
registerPageHelp({
  id: 'library',
  icon: 'grid',
  title: 'Library',
  summary:
    'Everything you own, by artist, album or song, with what is missing and what could be better.',
  articles: [A.libStandard, A.libManage, A.libMissing],
});
registerPageHelp({
  id: 'sync',
  icon: 'list',
  title: 'Playlists',
  summary:
    'Playlists from every service you use, mirrored here and kept in sync with your library.',
  articles: [A.syncOverview, A.mirrored],
});
registerPageHelp({
  id: 'playlist-explorer',
  icon: 'network',
  title: 'Playlist Explorer',
  summary: 'Every playlist across all your sources in one tree, to pick what to mirror.',
  articles: [A.explorer, A.syncOverview],
});
registerPageHelp({
  id: 'watchlist',
  icon: 'heart',
  title: 'Watchlist',
  summary: 'The artists and labels you follow. SoulSync checks them for new releases.',
  articles: [A.watchlist, A.scanning, A.wfAuto],
});
registerPageHelp({
  id: 'wishlist',
  icon: 'heart',
  title: 'Wishlist',
  summary: 'What SoulSync is waiting to download, from scans, playlist syncs and your own picks.',
  articles: [A.wishlist, A.wfAuto],
});
registerPageHelp({
  id: 'active-downloads',
  icon: 'down',
  title: 'Downloads',
  summary:
    'Everything SoulSync is fetching, what each one is doing right now, and what happened to the ones that finished.',
  articles: [A.manager, A.sources, A.downloadsDeep],
  adminArticles: [A.postprocess],
});
registerPageHelp({
  id: 'import',
  icon: 'inbox',
  title: 'Import',
  summary:
    'Music you already have: drop it in the import folder and SoulSync identifies and files it.',
  articles: [A.importFolder, A.importInbox],
});
registerPageHelp({
  id: 'automations',
  icon: 'refresh',
  title: 'Automations',
  summary: 'What SoulSync does on its own, when it does it, and what happened last time.',
  articles: [A.autoOverview, A.autoBuilder, A.autoSystem],
});
registerPageHelp({
  id: 'tools',
  icon: 'sliders',
  title: 'Tools',
  summary: 'Maintenance jobs that find library problems, plus backups and one-off fixes.',
  articles: [A.repair, A.backup],
});
registerPageHelp({
  id: 'stats',
  icon: 'chart',
  title: 'Stats',
  summary: 'What you listen to, how it changes over the year, and how healthy the library is.',
  articles: [A.stats],
});
registerPageHelp({
  id: 'settings',
  icon: 'gear',
  title: 'Settings',
  summary: 'How SoulSync connects, downloads, files and tags your music.',
  memberSummary: 'Your own accounts, sign-in and what you see.',
  articles: [A.accounts, A.features, A.members],
  adminArticles: [A.setMedia, A.setDownload, A.people, A.templates, A.folders, A.docker],
});
registerPageHelp({
  id: 'issues',
  icon: 'flag',
  title: 'Issues',
  summary: 'Problems people reported on a song, album or title, and what happened to each one.',
  memberSummary: 'What you reported to your admin, and what happened to each one.',
  articles: [A.libIssues],
});
registerPageHelp({
  id: 'requests',
  icon: 'inbox',
  title: 'Requests',
  summary: 'What people asked for, waiting for a yes, and where each approved one is now.',
  memberSummary: 'What you asked for, whether your admin said yes, and where each one is now.',
  articles: [A.requests, A.vaccess],
  adminArticles: [A.reqQuality],
});
registerPageHelp({
  id: 'help',
  icon: 'book',
  title: 'Help & Docs',
  summary: 'Every SoulSync guide in one place. Press / to search them.',
  articles: [A.firstSetup, A.tsCommon],
  adminArticles: [A.tsReporting],
});
registerPageHelp({
  id: 'artist-detail',
  icon: 'user',
  title: 'Artist',
  summary: 'One artist: what you have, what is missing, and their releases to download.',
  articles: [A.artistDetail, A.libMissing],
});
registerPageHelp({
  id: 'label-detail',
  icon: 'tag',
  title: 'Label',
  summary: 'A record label’s catalog, what you own of it, and a follow button.',
  articles: [A.label],
});
registerPageHelp({
  id: 'chat',
  icon: 'chat',
  title: 'Chat',
  summary: 'Soulseek rooms, private messages and the community room.',
  articles: [A.chatRooms],
});
registerPageHelp({
  id: 'podcasts',
  icon: 'mic',
  title: 'Podcasts',
  summary: 'Follow shows and let new episodes download on their own.',
  articles: [A.podcasts],
});
registerPageHelp({
  id: 'audiobooks',
  icon: 'book',
  title: 'Audiobooks',
  summary: 'Browse, wishlist and keep a library of audiobooks.',
  articles: [A.audiobooks],
});
registerPageHelp({
  id: 'sample-studio',
  icon: 'wave',
  title: 'Sample Studio',
  summary: 'Chop any track in your library into samples, by stem if you like.',
  articles: [A.studio],
});
registerPageHelp({
  id: 'hydrabase',
  icon: 'network',
  title: 'Hydrabase',
  summary: 'A console for the Hydrabase metadata network.',
  articles: [A.hydra],
});

// ── video side ──────────────────────────────────────────────────────────

registerPageHelp({
  id: 'video-dashboard',
  icon: 'film',
  title: 'Video Dashboard',
  summary: 'What you are watching, what landed this week, and what needs you.',
  articles: [A.vdash, A.vidWhat],
});
registerPageHelp({
  id: 'video-search',
  icon: 'search',
  title: 'Video Search',
  summary: 'Movies, shows, people and studios in one search, with what you already own.',
  articles: [A.vsearch],
});
registerPageHelp({
  id: 'video-discover',
  icon: 'compass',
  title: 'Video Discover',
  summary: 'Picks built from your library’s taste, and gaps in the franchises you started.',
  articles: [A.vdisc],
});
registerPageHelp({
  id: 'video-calendar',
  icon: 'calendar',
  title: 'Calendar',
  summary:
    'Every episode and movie release, and whether you have it, are getting it, or nothing is.',
  articles: [A.vcal],
});
registerPageHelp({
  id: 'video-library',
  icon: 'film',
  title: 'Video Library',
  summary: 'Your movies and shows, as posters, titles or files, with tools to fix and move them.',
  articles: [A.vlib, A.vlibManage],
});
registerPageHelp({
  id: 'video-watchlist',
  icon: 'heart',
  title: 'Video Watchlist',
  summary: 'The people, studios and channels you follow. Their new titles flow into your wishlist.',
  articles: [A.vwatch],
});
registerPageHelp({
  id: 'video-wishlist',
  icon: 'heart',
  title: 'Video Wishlist',
  summary: 'Everything the video side wants and does not have yet at the quality you asked for.',
  articles: [A.vwish],
});
registerPageHelp({
  id: 'video-requests',
  icon: 'inbox',
  title: 'Requests',
  summary: 'Movies and shows people asked for, and where each one is now.',
  memberSummary: 'What you asked for, whether your admin said yes, and where each one is now.',
  articles: [A.vreq, A.vaccess],
});
registerPageHelp({
  id: 'video-downloads',
  icon: 'down',
  title: 'Video Downloads',
  summary: 'Everything in flight with speed, ETA and state, and the history of every grab.',
  articles: [A.vdl, A.vdlQuality, A.vidSources],
});
registerPageHelp({
  id: 'video-import',
  icon: 'inbox',
  title: 'Video Import',
  summary: 'Movies and episodes from the import folder, and the downloads that need a hand.',
  articles: [A.vimport],
});
registerPageHelp({
  id: 'video-automations',
  icon: 'refresh',
  title: 'Video Automations',
  summary: 'Scheduled tasks and event workflows for the video side.',
  articles: [A.vauto],
});
registerPageHelp({
  id: 'video-tools',
  icon: 'sliders',
  title: 'Video Tools',
  summary: 'Overlays, collections, maintenance jobs and backups for the video side.',
  articles: [A.vtools],
});
registerPageHelp({
  id: 'video-settings',
  icon: 'gear',
  title: 'Video Settings',
  summary: 'Your media server and libraries, indexers, download clients, subtitles and keys.',
  articles: [A.vset, A.vidSources],
});
registerPageHelp({
  id: 'video-issues',
  icon: 'flag',
  title: 'Video Issues',
  summary: 'Problems people reported on a movie or show, and what happened to each one.',
  articles: [A.libIssues],
});
registerPageHelp({
  id: 'video-chat',
  icon: 'chat',
  title: 'Chat',
  summary: 'Soulseek rooms, private messages and the community room.',
  articles: [A.chatRooms],
});
registerPageHelp({
  id: 'video-help',
  icon: 'book',
  title: 'Help & Docs',
  summary: 'Every SoulSync guide in one place. Press / to search them.',
  articles: [A.vidWhat, A.tsCommon],
});

export { A as ARTICLES };
