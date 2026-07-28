import type { EnhancedAlbum, EnhancedTrack } from './-artist-detail.enhanced';

import { extractFormat, formatDurationMs } from './-artist-detail.enhanced';
import { filterJiosaavnEntries } from './-artist-detail.enrichment';

/**
 * The expanded album header, ported from renderExpandedAlbumHeader,
 * _getEnhancedAlbumTrackRows, _trackSlotKey, _normalizeExpectedMissingTrack,
 * getServiceUrl and makeClickableBadge (library.js:3783-4012, 5917).
 */

/** External links per service and entity type; null when the service has none. */
export function getServiceUrl(service: string, entityType: string, id: unknown): string | null {
  if (!id) return null;
  const urls: Record<string, Record<string, string>> = {
    spotify: {
      artist: `https://open.spotify.com/artist/${id}`,
      album: `https://open.spotify.com/album/${id}`,
      track: `https://open.spotify.com/track/${id}`,
    },
    musicbrainz: {
      artist: `https://musicbrainz.org/artist/${id}`,
      album: `https://musicbrainz.org/release/${id}`,
      track: `https://musicbrainz.org/recording/${id}`,
    },
    deezer: {
      artist: `https://www.deezer.com/artist/${id}`,
      album: `https://www.deezer.com/album/${id}`,
      track: `https://www.deezer.com/track/${id}`,
    },
    audiodb: {
      artist: `https://www.theaudiodb.com/artist/${id}`,
      album: `https://www.theaudiodb.com/album/${id}`,
      track: `https://www.theaudiodb.com/track/${id}`,
    },
    itunes: {
      artist: `https://music.apple.com/artist/${id}`,
      album: `https://music.apple.com/album/${id}`,
      track: `https://music.apple.com/song/${id}`,
    },
    // Last.fm, Genius and Bandcamp store a FULL url rather than an id, so the
    // "url" here is the value itself.
    lastfm: { artist: String(id), album: String(id), track: String(id) },
    genius: { artist: String(id), track: String(id) },
    tidal: {
      artist: `https://tidal.com/browse/artist/${id}`,
      album: `https://tidal.com/browse/album/${id}`,
      track: `https://tidal.com/browse/track/${id}`,
    },
    qobuz: {
      artist: `https://www.qobuz.com/artist/${id}`,
      album: `https://www.qobuz.com/album/${id}`,
      track: `https://www.qobuz.com/track/${id}`,
    },
    // Discogs has no per-track page, and Amazon no artist page.
    discogs: {
      artist: `https://www.discogs.com/artist/${id}`,
      album: `https://www.discogs.com/release/${id}`,
    },
    amazon: {
      album: `https://music.amazon.com/albums/${id}`,
      track: `https://music.amazon.com/tracks/${id}`,
    },
    bandcamp: { artist: String(id), album: String(id), track: String(id) },
  };
  return urls[service]?.[entityType] || null;
}

/** MusicBrainz's badge class is abbreviated; everything else uses its own name. */
export function serviceBadgeClass(service: string): string {
  return service === 'musicbrainz' ? 'mb' : service;
}

export interface AlbumIdBadge {
  service: string;
  label: string;
  id: string;
  url: string | null;
  className: string;
  title: string;
}

const ALBUM_ID_FIELDS = [
  { key: 'spotify_album_id', label: 'Spotify', svc: 'spotify' },
  { key: 'musicbrainz_release_id', label: 'MusicBrainz', svc: 'musicbrainz' },
  { key: 'deezer_id', label: 'Deezer', svc: 'deezer' },
  { key: 'jiosaavn_id', label: 'JioSaavn', svc: 'jiosaavn' },
  { key: 'audiodb_id', label: 'AudioDB', svc: 'audiodb' },
  { key: 'discogs_id', label: 'Discogs', svc: 'discogs' },
  { key: 'itunes_album_id', label: 'iTunes', svc: 'itunes' },
  { key: 'lastfm_url', label: 'Last.fm', svc: 'lastfm' },
  { key: 'bandcamp_url', label: 'Bandcamp', svc: 'bandcamp' },
];

/** One badge per id the album actually has; a service with no id is skipped. */
export function albumIdBadges(album: EnhancedAlbum): AlbumIdBadge[] {
  return filterJiosaavnEntries(ALBUM_ID_FIELDS, 'svc')
    .filter((field) => album[field.key])
    .map((field) => {
      const id = String(album[field.key]);
      const url = getServiceUrl(field.svc, 'album', id);
      return {
        service: field.svc,
        label: field.label,
        id,
        url,
        className: `enhanced-id-badge ${serviceBadgeClass(field.svc)}`,
        // A badge with no link says so by omitting the "click to open" hint.
        title: url ? `${field.label}: ${id} (click to open)` : `${field.label}: ${id}`,
      };
    });
}

export interface AlbumMatchChip {
  service: string;
  label: string;
  status: string;
  className: string;
  title: string;
}

const ALBUM_MATCH_SERVICES = [
  {
    key: 'spotify_match_status',
    label: 'Spotify',
    attempted: 'spotify_last_attempted',
    svc: 'spotify',
  },
  {
    key: 'musicbrainz_match_status',
    label: 'MB',
    attempted: 'musicbrainz_last_attempted',
    svc: 'musicbrainz',
  },
  {
    key: 'deezer_match_status',
    label: 'Deezer',
    attempted: 'deezer_last_attempted',
    svc: 'deezer',
  },
  {
    key: 'jiosaavn_match_status',
    label: 'JioSaavn',
    attempted: 'jiosaavn_last_attempted',
    svc: 'jiosaavn',
  },
  {
    key: 'audiodb_match_status',
    label: 'AudioDB',
    attempted: 'audiodb_last_attempted',
    svc: 'audiodb',
  },
  {
    key: 'discogs_match_status',
    label: 'Discogs',
    attempted: 'discogs_last_attempted',
    svc: 'discogs',
  },
  {
    key: 'itunes_match_status',
    label: 'iTunes',
    attempted: 'itunes_last_attempted',
    svc: 'itunes',
  },
  {
    key: 'lastfm_match_status',
    label: 'Last.fm',
    attempted: 'lastfm_last_attempted',
    svc: 'lastfm',
  },
  {
    key: 'amazon_match_status',
    label: 'Amazon',
    attempted: 'amazon_last_attempted',
    svc: 'amazon',
  },
  {
    key: 'bandcamp_match_status',
    label: 'Bandcamp',
    attempted: 'bandcamp_last_attempted',
    svc: 'bandcamp',
  },
];

/**
 * A chip per service, ALWAYS — an unmatched service shows an em dash rather
 * than disappearing, because "we never matched this" is the information.
 */
export function albumMatchChips(album: EnhancedAlbum): AlbumMatchChip[] {
  return filterJiosaavnEntries(ALBUM_MATCH_SERVICES, 'svc').map((service) => {
    const status = album[service.key] as string | undefined;
    const attempted = album[service.attempted];
    const state =
      status === 'matched' ? 'matched' : status === 'not_found' ? 'not-found' : 'pending';
    const tip: string[] = [];
    if (attempted) tip.push(`Last: ${new Date(String(attempted)).toLocaleString()}`);
    tip.push('Click to rematch');
    return {
      service: service.svc,
      label: service.label,
      status: status || '—',
      className: `enhanced-match-chip clickable ${state}`,
      title: tip.join(' · '),
    };
  });
}

export const ALBUM_ENRICH_SERVICES = [
  { id: 'spotify', label: 'Spotify', icon: '🟢' },
  { id: 'musicbrainz', label: 'MusicBrainz', icon: '🟠' },
  { id: 'deezer', label: 'Deezer', icon: '🟣' },
  { id: 'jiosaavn', label: 'JioSaavn', icon: '🎵' },
  { id: 'discogs', label: 'Discogs', icon: '🟤' },
  { id: 'audiodb', label: 'AudioDB', icon: '🔵' },
  { id: 'itunes', label: 'iTunes', icon: '🔴' },
  { id: 'lastfm', label: 'Last.fm', icon: '⚪' },
  { id: 'genius', label: 'Genius', icon: '🟡' },
  { id: 'bandcamp', label: 'Bandcamp', icon: '🔹' },
];

export function albumEnrichServices() {
  return filterJiosaavnEntries(ALBUM_ENRICH_SERVICES, 'id');
}

/**
 * The disc:track slot a row occupies.
 *
 * Used ONLY to decide whether an expected-missing track is already owned —
 * never as a render key. See getAlbumTrackRows.
 */
export function trackSlotKey(track: Record<string, unknown>): string {
  const disc = Number(track.disc_number || track.expected_disc_number || 1);
  const num = Number(track.track_number || track.expected_track_number || 0);
  return `${disc}:${num}`;
}

export interface MissingTrackRow extends EnhancedTrack {
  _hasActionableContext: boolean;
  _missingExpected: true;
  _sourceTrack: Record<string, unknown>;
}

/**
 * An expected-but-missing track, flattened into the shape a track row expects.
 *
 * `_hasActionableContext` is the gate: without a title, a track number and SOME
 * source id there is nothing the row's actions could act on, so the row is
 * dropped rather than rendered as an inert placeholder.
 */
export function normalizeExpectedMissingTrack(
  source: Record<string, unknown>,
  album: EnhancedAlbum,
): MissingTrackRow {
  const title = (source.title || source.name || `Track ${source.track_number || '?'}`) as string;
  const sourceTrackId = (source.track_id || source.id || source.source_track_id || '') as string;
  const hasActionableContext = Boolean(
    title &&
    source.track_number &&
    (sourceTrackId ||
      source.spotify_track_id ||
      source.deezer_id ||
      source.itunes_track_id ||
      source.musicbrainz_recording_id),
  );
  return {
    id: `missing-${album.id}-${source.disc_number || 1}-${source.track_number || ''}`,
    title,
    track_number: (source.track_number || source.position || '') as string,
    disc_number: (source.disc_number || 1) as number,
    duration: (source.duration || source.duration_ms || 0) as number,
    spotify_track_id: source.spotify_track_id || (source.source === 'spotify' ? sourceTrackId : ''),
    deezer_id: source.deezer_id || (source.source === 'deezer' ? sourceTrackId : ''),
    itunes_track_id: source.itunes_track_id || (source.source === 'itunes' ? sourceTrackId : ''),
    musicbrainz_recording_id:
      source.musicbrainz_recording_id || (source.source === 'musicbrainz' ? sourceTrackId : ''),
    source: (source.source || source.metadata_source || '') as string,
    track_id: sourceTrackId,
    album_id: (source.album_id || source.source_album_id || '') as string,
    artists: (source.artists || source.artist_names || []) as unknown,
    _hasActionableContext: hasActionableContext,
    _missingExpected: true,
    _sourceTrack: source,
  };
}

/**
 * Owned tracks merged with expected-missing ones, sorted disc then track.
 *
 * Owned rows are keyed by track ID, NEVER by disc:track slot (#1051). Multi-disc
 * albums whose tags all claim disc 1 make disc1-trackN and disc2-trackN share a
 * slot; keying the map by slot silently overwrote one with the other and tracks
 * vanished from the table. The slot set is still what decides whether an
 * expected-missing row is already owned.
 */
export function getAlbumTrackRows(album: EnhancedAlbum): EnhancedTrack[] {
  const owned = Array.isArray(album.tracks) ? album.tracks : [];
  const rows = new Map<string, EnhancedTrack>();
  const ownedSlots = new Set<string>();

  for (const track of owned) {
    rows.set(`owned:${track.id}`, track);
    ownedSlots.add(trackSlotKey(track));
  }

  const explicitMissing = Array.isArray(album.missing_tracks) ? album.missing_tracks : [];
  for (const missing of explicitMissing) {
    const row = normalizeExpectedMissingTrack(missing as Record<string, unknown>, album);
    const key = trackSlotKey(row as Record<string, unknown>);
    if (row._hasActionableContext && !ownedSlots.has(key) && !rows.has(`missing:${key}`)) {
      rows.set(`missing:${key}`, row);
    }
  }

  return [...rows.values()].sort((a, b) => {
    const discDelta = Number(a.disc_number || 1) - Number(b.disc_number || 1);
    if (discDelta !== 0) return discDelta;
    const trackDelta = Number(a.track_number || 0) - Number(b.track_number || 0);
    if (trackDelta !== 0) return trackDelta;
    return String(a.title || '').localeCompare(String(b.title || ''));
  });
}

/**
 * The header's meta line.
 *
 * The track count reads "owned/expected" only when the album is INCOMPLETE;
 * a complete album shows a plain count rather than "12/12 tracks". Expected is
 * the largest of what we own, what we can show, and what the source claims — so
 * a source under-reporting its own tracklist cannot make a complete album look
 * over-full.
 */
export function expandedHeaderDetails(album: EnhancedAlbum, rows: EnhancedTrack[]): string {
  const details: string[] = [];
  if (album.year) details.push(String(album.year));

  const ownedCount = album.tracks ? album.tracks.length : 0;
  const expectedCount = Math.max(
    ownedCount,
    rows.length,
    Number(album.api_track_count || album.track_count || 0),
  );
  const missingCount = rows.filter((row) => (row as MissingTrackRow)._missingExpected).length;

  if (album._canonicalTracksLoading) details.push('checking tracklist');
  if (expectedCount > ownedCount) details.push(`${ownedCount}/${expectedCount} tracks`);
  else details.push(`${ownedCount} track${ownedCount !== 1 ? 's' : ''}`);
  if (missingCount > 0) details.push(`${missingCount} missing`);

  let durationMs = 0;
  for (const track of album.tracks ?? []) durationMs += track.duration || 0;
  if (durationMs > 0) details.push(formatDurationMs(durationMs));

  if (album.label) details.push(String(album.label));
  if (album.record_type) details.push(String(album.record_type).toUpperCase());

  return details.join(' · ');
}

export interface AlbumMetaField {
  key: string;
  label: string;
  value: string;
  type?: string;
  placeholder?: string;
}

/**
 * The editable album metadata fields, in the vanilla's order.
 *
 * `genres` is a comma-joined string in the form and an ARRAY on the record;
 * `explicit` is the string '1'/'0' rather than a checkbox.
 */
export function albumMetaFields(album: EnhancedAlbum): AlbumMetaField[] {
  return [
    { key: 'title', label: 'Title', value: String(album.title || '') },
    { key: 'year', label: 'Year', value: String(album.year || ''), type: 'number' },
    {
      key: 'release_date',
      label: 'Release Date',
      value: String(album.release_date || ''),
      placeholder: 'YYYY-MM-DD',
    },
    {
      key: 'genres',
      label: 'Genres',
      value: Array.isArray(album.genres) ? album.genres.join(', ') : String(album.genres || ''),
    },
    { key: 'label', label: 'Label', value: String(album.label || '') },
    { key: 'style', label: 'Style', value: String(album.style || '') },
    { key: 'mood', label: 'Mood', value: String(album.mood || '') },
    { key: 'record_type', label: 'Type', value: String(album.record_type || 'album') },
    { key: 'explicit', label: 'Explicit', value: album.explicit ? '1' : '0' },
  ];
}

/** #824: a release date may be just the year, year-month, or the full date. */
export const RELEASE_DATE_PATTERN = /^\d{4}(-\d{2}(-\d{2})?)?$/;

export interface AlbumMetaDiff {
  updates: Record<string, unknown>;
  /** True when a non-empty release date does not parse; nothing is saved. */
  invalidDate: boolean;
}

/**
 * Only what CHANGED, per field type.
 *
 * The diff matters: sending every field would overwrite columns another process
 * touched between load and save, and the empty-vs-null distinction is what
 * lets a user clear a field rather than merely blank the input.
 */
export function albumMetaUpdates(
  album: EnhancedAlbum,
  values: Record<string, string>,
): AlbumMetaDiff {
  const updates: Record<string, unknown> = {};
  let invalidDate = false;

  for (const [field, raw] of Object.entries(values)) {
    const value = String(raw ?? '').trim();

    if (field === 'genres') {
      const next = value
        ? value
            .split(',')
            .map((g) => g.trim())
            .filter(Boolean)
        : [];
      const original = Array.isArray(album.genres) ? album.genres : [];
      if (JSON.stringify(next) !== JSON.stringify(original)) updates[field] = next;
    } else if (field === 'year' || field === 'explicit' || field === 'track_count') {
      const numeric = value !== '' ? parseInt(value, 10) : null;
      // `|| null` treats a stored 0 (or an absent field) as null, so a
      // non-explicit album ALWAYS reports explicit:0 as a change. Verbatim
      // vanilla: it means a save on such an album is never a no-op, and the
      // "no changes" branch is only reachable for an explicit album. Fixing it
      // would change what gets written on every save.
      if (numeric !== (album[field] || null)) updates[field] = numeric;
    } else if (field === 'release_date') {
      if (value && !RELEASE_DATE_PATTERN.test(value)) {
        invalidDate = true;
        continue;
      }
      if ((value || '') !== (album.release_date || '')) updates[field] = value || null;
    } else if ((value || '') !== (album[field] || '')) {
      updates[field] = value || null;
    }
  }

  return { updates, invalidDate };
}

/**
 * Loose title key for owned-to-canonical matching.
 *
 * Mirrors core.library_reorganize._normalize_title: drop only the featured
 * credit, then treat every OTHER separator as whitespace. Keeping bracket
 * CONTENT rather than deleting it is what makes "X (Main Theme)" and
 * "X - Main Theme" line up.
 */
export function normTitleForMatch(value: unknown): string {
  return String(value || '')
    .toLowerCase()
    .replace(/[([]\s*(?:feat|ft|featuring)\b[^)\]]*[)\]]/g, ' ')
    .replace(/\s+(?:feat|ft|featuring)\b\.?\s.*$/g, ' ')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

/** Which source's tracklist counts as canonical, in priority order. */
export function getAlbumCanonicalSource(
  album: EnhancedAlbum,
): { source: string; id: string } | null {
  const priority: [string, string][] = [
    ['spotify', 'spotify_album_id'],
    ['deezer', 'deezer_id'],
    ['itunes', 'itunes_album_id'],
    ['musicbrainz', 'musicbrainz_release_id'],
    ['discogs', 'discogs_id'],
    ['tidal', 'tidal_id'],
    ['qobuz', 'qobuz_id'],
  ];
  for (const [source, key] of priority) {
    if (album[key]) return { source, id: String(album[key]) };
  }
  return null;
}

/**
 * Which canonical tracks we do not own.
 *
 * #916: multi-disc albums store disc_number = 1 for EVERY track (the scanner
 * does not split discs), so a strict slot match flags every canonical disc-2+
 * track as missing. Each canonical track matches by SLOT first, then falls back
 * to a normalised TITLE against any unused owned track — consuming each owned
 * track once, so genuine missings and duplicate titles still count correctly.
 */
export function deriveMissingTracks(
  album: EnhancedAlbum,
  canonicalTracks: Record<string, unknown>[],
): Record<string, unknown>[] {
  const owned = (album.tracks ?? []).map((track) => ({
    slot: trackSlotKey(track as Record<string, unknown>),
    title: normTitleForMatch(track.title || track.name),
    used: false,
  }));

  // '1:0' is the "no slot at all" key; indexing it would match everything.
  const slotIndex = new Map<string, number>();
  owned.forEach((entry, index) => {
    if (entry.slot !== '1:0' && !slotIndex.has(entry.slot)) slotIndex.set(entry.slot, index);
  });

  const missing: Record<string, unknown>[] = [];
  for (const track of canonicalTracks ?? []) {
    // Indexing the slotless '1:0' key above is dead defensively — a canonical
    // track with that key is skipped below, so the entry could never be looked
    // up. Kept because the vanilla wrote it.
    const key = trackSlotKey(track);
    const normalized = normalizeExpectedMissingTrack(track, album);
    if (key === '1:0' || !normalized._hasActionableContext) continue;

    const slotMatch = slotIndex.get(key);
    if (slotMatch != null && !owned[slotMatch].used) {
      owned[slotMatch].used = true;
      continue;
    }

    const title = normTitleForMatch(track.name || track.title);
    if (title) {
      const match = owned.find((entry) => !entry.used && entry.title === title);
      if (match) {
        match.used = true;
        continue;
      }
    }

    missing.push({
      ...track,
      name: track.name || track.title,
      duration_ms: track.duration_ms || track.duration || 0,
    });
  }
  return missing;
}

/** Flatten an /api/album/<id>/tracks payload into canonical track rows. */
export function normalizeCanonicalTracks(
  tracks: Record<string, unknown>[],
  source: string,
  albumSourceId: string,
  payloadSource?: string,
): Record<string, unknown>[] {
  return (tracks ?? []).map((track, index) => ({
    ...track,
    title: track.title || track.name || `Track ${track.track_number || index + 1}`,
    name: track.name || track.title || `Track ${track.track_number || index + 1}`,
    track_number: track.track_number || index + 1,
    disc_number: track.disc_number || 1,
    duration: track.duration || track.duration_ms || 0,
    source: payloadSource || source,
    track_id: track.id || track.track_id || '',
    // A source with no per-track id still needs a stable key, so one is
    // synthesised from the album and the slot.
    id:
      track.id ||
      track.track_id ||
      `${source}:${albumSourceId}:${track.disc_number || 1}:${track.track_number || index + 1}`,
  }));
}

export interface TrackMatchChip {
  service: string;
  label: string;
  matched: boolean;
  className: string;
  title: string;
}

const TRACK_MATCH_SERVICES = [
  { svc: 'spotify', col: 'spotify_track_id', label: 'SP' },
  { svc: 'musicbrainz', col: 'musicbrainz_recording_id', label: 'MB' },
  { svc: 'deezer', col: 'deezer_id', label: 'Dz' },
  { svc: 'jiosaavn', col: 'jiosaavn_id', label: 'JS' },
  { svc: 'audiodb', col: 'audiodb_id', label: 'ADB' },
  { svc: 'itunes', col: 'itunes_track_id', label: 'iT' },
  { svc: 'lastfm', col: 'lastfm_url', label: 'LFM' },
  { svc: 'genius', col: 'genius_id', label: 'Gen' },
  { svc: 'bandcamp', col: 'bandcamp_url', label: 'BC' },
];

/** A chip per service, matched or not — the gaps are the point. */
export function trackMatchChips(track: EnhancedTrack): TrackMatchChip[] {
  return filterJiosaavnEntries(TRACK_MATCH_SERVICES, 'svc').map((service) => {
    const id = track[service.col];
    const matched = Boolean(id);
    return {
      service: service.svc,
      label: service.label,
      matched,
      className: `enhanced-track-match-chip ${matched ? 'matched' : 'not-found'}`,
      title: matched ? `${service.svc}: ${id}` : `${service.svc}: no match`,
    };
  });
}

export interface TrackColumn {
  label: string;
  cls: string;
  sortField?: string;
}

/**
 * The track table's columns.
 *
 * An admin gets a leading select-all cell (not in this list), write-tag and
 * delete columns; everyone else gets a single report column instead.
 *
 * The header's admin `col-delete` does NOT match the body's
 * `col-track-actions` — verbatim from the vanilla, where the two drifted.
 */
export function trackColumns(admin: boolean): TrackColumn[] {
  return [
    { label: '', cls: 'col-play' },
    { label: '#', cls: 'col-num', sortField: 'track_number' },
    { label: 'Disc', cls: 'col-disc', sortField: 'disc_number' },
    { label: 'Title', cls: 'col-title', sortField: 'title' },
    { label: 'Duration', cls: 'col-duration', sortField: 'duration' },
    { label: 'Format', cls: 'col-format', sortField: 'format' },
    { label: 'Bitrate', cls: 'col-bitrate', sortField: 'bitrate' },
    { label: 'BPM', cls: 'col-bpm', sortField: 'bpm' },
    { label: 'File', cls: 'col-path' },
    { label: 'Match', cls: 'col-match' },
    { label: '', cls: 'col-queue' },
    ...(admin
      ? [
          { label: '', cls: 'col-writetag' },
          { label: '', cls: 'col-delete' },
        ]
      : [{ label: '', cls: 'col-report' }]),
    { label: '', cls: 'col-mobile-actions' },
  ];
}

export interface TrackSort {
  field: string;
  ascending: boolean;
}

/** The sort arrow appended to a sorted column's label. */
export function sortIndicator(column: TrackColumn, sort: TrackSort | undefined): string {
  if (!sort || sort.field !== column.sortField) return column.label;
  return `${column.label}${sort.ascending ? ' ▲' : ' ▼'}`;
}

const NUMERIC_SORT_FIELDS = ['track_number', 'disc_number', 'bpm', 'bitrate', 'duration'];

/**
 * Sort the album's OWNED tracks, as sortEnhancedTracks did.
 *
 * NOTE: this does not change the rendered order. The vanilla sorted
 * album.tracks and then fed the table from _getEnhancedAlbumTrackRows, which
 * re-sorts by disc, then track, then title — so a column click updates the
 * arrow and nothing else. Reproduced verbatim rather than "fixed", because
 * making the columns actually sort is a behaviour change, not a port. See the
 * note in enhanced-track-table.tsx.
 */
export function sortTracks(
  tracks: EnhancedTrack[],
  field: string,
  ascending: boolean,
): EnhancedTrack[] {
  return [...tracks].sort((a, b) => {
    let valueA: unknown = field === 'format' ? extractFormat(a.file_path) : a[field];
    let valueB: unknown = field === 'format' ? extractFormat(b.file_path) : b[field];

    // A null always sinks, whichever direction the sort runs.
    if (valueA == null) return 1;
    if (valueB == null) return -1;

    if (NUMERIC_SORT_FIELDS.includes(field)) {
      return ascending ? Number(valueA) - Number(valueB) : Number(valueB) - Number(valueA);
    }
    valueA = String(valueA).toLowerCase();
    valueB = String(valueB).toLowerCase();
    return ascending
      ? (valueA as string).localeCompare(valueB as string)
      : (valueB as string).localeCompare(valueA as string);
  });
}

/** 320+ is high, 192+ medium, anything else low. */
export function bitrateClass(bitrate: unknown): string {
  const value = Number(bitrate) || 0;
  return value >= 320 ? 'high' : value >= 192 ? 'medium' : 'low';
}

/** The base name of the file, or a "missing" note for an unowned row. */
export function trackFileName(track: EnhancedTrack): string {
  const path = track.file_path || '-';
  if ((track as { _missingExpected?: boolean })._missingExpected) return 'Missing from library';
  return path !== '-' ? String(path).split(/[\\/]/).pop() || '-' : '-';
}
