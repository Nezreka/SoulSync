/**
 * Server Activity API types. Mirrors scrap/CONTRACT.md and the python in
 * core/server_activity/. Every route is admin only (#2155) except the artwork
 * proxy. Times are unix seconds (float) unless the name ends in _ms.
 */

/** soulsync is its own player (the screens that play through SoulSync) */
export type ServerKind = 'plex' | 'jellyfin' | 'soulsync';
export type MediaKind =
  | 'movie'
  | 'episode'
  | 'track'
  | 'audiobook'
  | 'podcast'
  | 'clip'
  | 'live'
  | 'other';
export type PlayMethod = 'direct_play' | 'direct_stream' | 'transcode';
export type PlayState = 'playing' | 'paused' | 'buffering';

// ── live ──────────────────────────────────────────────────────────────────

export interface VideoSide {
  codec: string; // "HEVC", "H.264"
  resolution: string; // "4K", "1080p", "" when unknown
  width: number | null;
  height: number | null;
  profile: string | null; // "Main 10"
  bit_depth: number | null;
  hdr: string | null; // "HDR10" | "HDR10+" | "Dolby Vision" | "HLG" | null (SDR or unknown)
  bitrate_kbps: number | null;
  frame_rate: string | null;
  label: string; // "4K HEVC Main 10 · HDR10"
}

export interface AudioSide {
  codec: string; // "TrueHD", "FLAC", "DTS-HD MA"
  channels: number | null;
  channel_layout: string | null; // "7.1", "Stereo"
  language: string | null;
  bit_depth: number | null;
  sample_rate_hz: number | null;
  bitrate_kbps: number | null;
  atmos: boolean;
  lossless: boolean;
  hires: boolean; // lossless and (> 16 bit or > 48 kHz): the one tinted badge
  quality: string; // "FLAC 24-bit · 96 kHz" | "AAC 64 kbps"
  label: string; // "TrueHD Atmos 7.1 · English"
}

export interface SubtitleSide {
  codec: string;
  language: string | null;
  kind: 'image' | 'styled' | 'text';
  label: string; // "English (SRT)"
}

export interface StreamPair<T> {
  source: T | null; // the file as stored
  stream: T | null; // what is sent
  decision: string; // "direct" | "copy" | "transcode" | ""
  changed: boolean;
}

export interface Playback {
  /** null: the player never said how it plays (an older app) */
  method: PlayMethod | null;
  method_label: 'Direct Play' | 'Direct Stream' | 'Transcode' | null;
  video: StreamPair<VideoSide> | null;
  audio: StreamPair<AudioSide> | null;
  subtitles: {
    source: SubtitleSide;
    stream: { label: string }; // "Sent as text" | "Burned in" | "Converted to text" | "Sent as is"
    decision: string; // "copy" | "burn" | "transcode" | "direct"
    changed: boolean;
  } | null;
  container: { source: string | null; stream: string | null; changed: boolean };
  bitrate_kbps: { source: number | null; stream: number | null };
  summary: { quality: string | null; change: string | null }; // "4K HEVC", "4K → 1080p"
}

export interface ReasonItem {
  code: string;
  label: string;
}

export interface Reason {
  codes: string[];
  items: ReasonItem[];
  inferred: boolean; // true: worked out by SoulSync (plex states none)
  text: string; // one plain sentence
}

export interface Transcoder {
  hw: boolean;
  hw_decode: string | null; // "Intel Quick Sync"
  hw_encode: string | null;
  hw_full_pipeline: boolean;
  fell_back?: string | null; // soulsync: the GPU failed and this runs on the CPU
  speed: number | null; // 2.4
  buffered_s: number | null; // plex only
  throttled: boolean | null; // plex only; true = "resting"
  progress_pct: number | null; // null: soulsync's converter doesn't say
  video: string | null;
  audio: string | null;
}

export interface GeoPlace {
  city: string | null;
  region: string | null;
  country: string | null;
  code: string | null; // ISO country code
  timezone: string | null;
  lat: number | null;
  lon: number | null;
  label: string; // "Chicago, Illinois, US"
  short: string; // "Chicago, IL", "Leeds, UK"
}

export interface Connection {
  ip: string | null;
  public_ip: string | null;
  remote: boolean;
  secure: boolean | null; // plex only
  relayed: boolean | null; // plex only
  location: GeoPlace | null; // remote viewers only, when geo lookup is on and the place is known
}

export interface Player {
  product: string; // "Plex for Apple TV", "Plexamp", "Jellyfin Web"
  version: string | null;
  platform: string | null;
  platform_version: string | null;
  device: string | null;
  model: string | null;
  name: string | null; // the device's own name: "Living room"
}

export interface BookProgress {
  chapter: {
    index: number;
    count: number;
    title: string | null;
    position_ms: number;
    duration_ms: number;
  };
  position_ms: number; // in the whole book
  duration_ms: number;
  percent: number;
  left_ms: number;
}

export type SessionLink =
  | { kind: 'movie'; id: number | string; source: 'library' | 'tmdb' }
  | {
      kind: 'show';
      id: number | string;
      source: 'library' | 'tmdb';
      season?: number | null;
      episode?: number | null;
    }
  | {
      kind: 'music';
      id: number;
      artist_id: number;
      album_id: number;
      track_id: number;
      source: 'library';
    }
  | { kind: 'audiobook'; id: string; source: 'library' };

export interface Session {
  id: string; // "plex:27" | "jellyfin:<id>" | "soulsync:<profile>:<screen>"
  server: ServerKind;
  server_name: string;
  session_key: string; // what stop/message take
  kind: MediaKind;
  activity: 'watching' | 'listening';
  state: PlayState;
  title: string;
  subtitle: string;
  year: number | null;
  show: {
    title: string;
    season: number | null;
    episode: number | null;
    season_title: string | null;
  } | null;
  music: {
    artist: string;
    album_artist: string;
    album: string;
    track: number | null;
    disc: number | null;
  } | null;
  book: {
    author: string | null;
    narrator: string | null;
    title: string;
    progress: BookProgress | null;
    asin?: string | null;
  } | null;
  /** the guide channel a soulsync screen is tuned to */
  channel?: { id: string; name: string; number: number | null } | null;
  thumb: string; // pass to /api/server-activity/image?path= (soulsync's: its own url)
  art: string;
  /** the proxy query for the poster, with the item's family to fall back on */
  image?: string | null;
  /** the proxy query for the backdrop */
  backdrop?: string | null;
  duration_ms: number;
  offset_ms: number;
  progress_pct: number;
  user: string;
  user_id: string;
  user_avatar: string | null; // ready-made url, admin only
  player: Player;
  connection: Connection;
  bandwidth_kbps: number;
  playback: Playback;
  reason: Reason | null; // null for direct play
  transcoder: Transcoder | null;
  controls: { stop: boolean; message: boolean; stop_note: string | null };
  link: SessionLink | null;
  keys: { rating_key: string | null; parent_key: string | null; grandparent_key: string | null };
  // from the play recorder
  started_at: number | null;
  paused_ms: number;
  pause_count: number;
  buffering_ms: number;
  play_key: string | null;
  group_key: string | null;
  // the pre-overhaul drawer's fields, kept until it is replaced
  media_type: string;
  grandparent: string;
  location: 'lan' | 'wan';
  stream: {
    method: string;
    video: string;
    audio: string;
    resolution: string;
    bitrate_kbps: number;
    transcode_progress: number;
    throttled: boolean;
    hw: boolean;
    container: string;
  };
}

export interface ServerInfo {
  server: ServerKind;
  name: string; // "Basement"
  version: string | null;
  reachable: boolean;
  plex_pass: boolean | null;
}

export interface Pulse {
  streams: number;
  watching: number;
  listening: number;
  playing: number;
  paused: number;
  direct_play: number;
  direct_stream: number;
  transcodes: number;
  transcoders: { running: number; hw: number; sw: number };
  total_bandwidth_kbps: number;
  remote_bandwidth_kbps: number;
  local_bandwidth_kbps: number;
  upload_cap_kbps: number | null; // null: no cap set, draw no bar
  lan: number;
  wan: number;
  cpu: { host_pct: number; process_pct: number; at: number } | null; // plex only, fresh samples only
}

export interface BandwidthSeries {
  window_s: number; // 600
  points: { t: number; total_kbps: number; remote_kbps: number; streams: number }[];
  peak_kbps: number;
  peak_remote_kbps: number;
}

export interface ActivityPayload {
  ok: boolean;
  reason?: 'no_server' | 'unreachable' | 'error';
  message?: string;
  at: number;
  server: { name: string; version: string; platform: string };
  servers: ServerInfo[];
  summary: Pulse;
  bandwidth: BandwidthSeries;
  sessions: Session[];
  /** how old this answer is when served from the shared snapshot */
  age_ms?: number;
}

// ── history ───────────────────────────────────────────────────────────────

export interface PlayRow {
  id: number;
  play_key: string;
  kind: MediaKind;
  title: string;
  parent_title: string | null;
  grandparent_title: string | null;
  season: number | null;
  episode: number | null;
  track: number | null;
  chapter: { index: number; count: number; title: string | null } | null;
  started_at: number;
  stopped_at: number | null;
  live: boolean;
  played_ms: number;
  paused_ms: number;
  buffering_ms: number;
  pause_count: number;
  duration_ms: number | null;
  percent: number | null;
  book_percent: number | null;
  method: PlayMethod | null;
  method_label: string | undefined;
  quality: string | null;
  change: string | null;
  source: 'live' | 'plex_history';
}

export interface HistoryGroup {
  group_key: string;
  kind: MediaKind;
  title: string;
  subtitle: string;
  count: number;
  count_label: string | null; // "3 episodes", "11 tracks"
  started_at: number;
  stopped_at: number | null;
  live: boolean;
  played_ms: number;
  paused_ms: number;
  buffering_ms: number;
  pause_count: number;
  percent: number | null;
  finished: number;
  book_percent: number | null;
  method: PlayMethod | null; // the worst seen in the group
  method_label: string | undefined;
  quality: string | null;
  change: string | null;
  reasons: ReasonItem[];
  user: string;
  user_avatar: string | null;
  player: {
    product: string | null;
    name: string | null;
    device: string | null;
    platform: string | null;
    version: string | null;
  };
  connection: { remote: boolean; ip: string | null; location: string | null };
  server: ServerKind;
  server_name: string | null;
  thumb: string | null;
  /** the proxy query for the poster (the show's for a run of episodes) */
  image?: string | null;
  link: SessionLink | null;
  source: 'live' | 'plex_history';
  plays: PlayRow[];
}

export interface HistoryPayload {
  ok: boolean;
  reason?: string;
  filters: {
    kind: string | null;
    user: string | null;
    method: string | null;
    server: string | null;
    start: number | null;
    end: number | null;
    q: string | null;
  };
  summary: {
    plays: number;
    played_ms: number;
    people: number;
    converted_pct: number | null;
    away_places: number;
    first_at: number | null;
  };
  days: { date: string; plays: number; played_ms: number }[];
  groups: HistoryGroup[];
  page: number;
  per_page: number;
  has_more: boolean;
  facets: {
    users: { user: string; avatar: string | null; plays: number }[];
    servers: ServerKind[];
  } | null;
  log_started_at: number | null;
}

export interface TimelineBand {
  state: PlayState | 'between';
  start: number;
  end: number;
}

export interface HistoryGroupDetail extends HistoryGroup {
  ok: boolean;
  timeline: TimelineBand[];
  quality_detail: {
    video: [string | null, string | null];
    audio: [string | null, string | null];
    subtitles: [string | null, string | null];
    container: [string | null, string | null];
    bitrate_kbps: [number | null, number | null];
  } | null;
  method_changes: { at: number; from: PlayMethod; to: PlayMethod }[];
  reasons_inferred: boolean;
  hw_engine: string | null;
  gaps: number;
  sent_kb: number;
  peak_kbps: number;
}

// ── stats ─────────────────────────────────────────────────────────────────

export interface ShelfItem {
  title: string;
  thumb: string | null;
  image?: string | null;
  hours: number;
  plays: number;
  people: number;
  link: SessionLink | null;
  year?: number | null; // films
  episodes?: number | null; // shows, podcasts
  tracks?: number; // artists
  kind?: 'audiobook' | 'podcast'; // books
  user?: string | null;
  percent?: number | null;
  finished?: boolean | null;
}

export interface PersonCard {
  user: string;
  avatar: string | null;
  hours: number;
  plays: number;
  spark: number[]; // 14 days of hours, oldest first
  mostly: { title: string; kind: MediaKind } | null;
  devices: string[];
  away_pct: number | null;
  top_location: string | null;
  lossless_pct: number | null;
  last_seen: number | null;
  live: boolean;
}

export interface StatsPayload {
  ok: boolean;
  reason?: string;
  days: 7 | 30 | 90 | 365;
  tz: string;
  range: { start: number; end: number };
  headline: {
    people: number;
    hours: number;
    top_kind: 'tv' | 'films' | 'music' | 'books' | 'other' | null;
    busiest_daypart: 'morning' | 'afternoon' | 'evening' | 'night' | null;
    converted_pct: number | null;
    converted_one_in: number | null;
    top_converted_user: string | null;
    top_converted_user_away_pct: number | null;
    servers: ServerKind[];
  };
  tiles: {
    plays: { value: number; prev: number; change_pct: number | null };
    hours: {
      value: number;
      by_kind: { tv: number; films: number; music: number; books: number; other: number };
      estimated_hours: number;
    };
    most_at_once: { value: number; at: number | null } | null;
    played_as_is: { pct: number | null; plays: number; known: number };
    sent_remotely: { gb: number; peak_kbps: number | null; peak_at: number | null };
  };
  per_day: { date: string; weekday: number; weekend: boolean; hours: number; plays: number }[];
  methods: Record<PlayMethod, { plays: number; pct: number | null }>;
  reasons: { code: string; label: string; plays: number }[];
  hardware: { hw: number; total: number };
  heatmap: {
    cells: number[][]; // [weekday 0 = Monday][hour 0..23] hours
    max: number;
    busiest: { weekday: number; hour_start: number; hour_end: number } | null;
    quietest: { weekday: number; hour: number };
    dayparts: { morning: number; afternoon: number; evening: number; night: number };
    busiest_daypart: string | null;
  };
  players: { product: string; plays: number; hours: number }[];
  locations: { label: string; home: boolean; plays: number; hours: number }[];
  people: PersonCard[];
  top: { films: ShelfItem[]; shows: ShelfItem[]; artists: ShelfItem[]; books: ShelfItem[] };
  unknown_method_plays: number;
}

// ── controls and settings ─────────────────────────────────────────────────

export interface ControlResult {
  ok: boolean;
  error?: string;
  code?: 'plex_pass';
  message_shown?: boolean;
}

export interface ServerActivitySettings {
  upload_mbps: number | null;
  geo_lookup: boolean;
  retention_days: number; // 0 = forever
  stop_presets: string[];
}
