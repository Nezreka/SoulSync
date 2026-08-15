/**
 * Your Year in Listening — the payload of `GET /api/stats/year`.
 *
 * Mirrors `MusicDatabase.get_year_in_listening`. Every field the backend
 * always sends is required here; the ones it can legitimately leave null
 * (a leader for a silent month, a peak day on an empty install) are nullable
 * rather than optional, so a missing key is a contract break rather than
 * something the UI quietly papers over.
 */

export interface YearPeriod {
  start: string;
  end: string;
  label: string;
  months: number;
}

export interface YearTotals {
  plays: number;
  minutes: number;
  artists: number;
  albums: number;
  tracks: number;
  active_days: number;
}

export interface YearMonth {
  month: string;
  label: string;
  plays: number;
  minutes: number;
  top_artist: string | null;
}

/** Artist-shaped rows carry what `core/stats/enrich.py` attaches: artwork
 *  plus the id the surface needs to link through to artist detail. NOTE `id`
 *  is a TEXT column in `artists`, so it arrives as a string. */
export interface YearArtist {
  name: string;
  plays: number;
  months_on_top: number;
  image_url?: string | null;
  id?: string | number | null;
  global_listeners?: number | null;
  soul_id?: string | null;
}

export interface YearAlbum {
  name: string;
  artist: string | null;
  plays: number;
  image_url?: string | null;
  id?: string | number | null;
  artist_id?: string | number | null;
}

export interface YearTrack {
  name: string;
  artist: string | null;
  album: string | null;
  plays: number;
  first_played: string | null;
  last_played: string | null;
  image_url?: string | null;
  id?: string | number | null;
  artist_id?: string | number | null;
}

export interface YearDiscovery {
  name: string;
  first_played: string | null;
  plays: number;
  image_url?: string | null;
  id?: string | number | null;
  soul_id?: string | null;
}

export interface YearInListening {
  period: YearPeriod;
  has_data: boolean;
  totals: YearTotals;
  months: YearMonth[];
  top_artists: YearArtist[];
  top_albums: YearAlbum[];
  top_tracks: YearTrack[];
  discoveries: YearDiscovery[];
  peak_day: { date: string | null; plays: number };
  top_hour: { hour: number | null; plays: number };
  cached?: boolean;
}

export interface YearInListeningPayload extends YearInListening {
  success: boolean;
  error?: string;
}

/** The kinds of slide the story can contain, in the order they can appear. */
export type YearSlideKind =
  | 'opening'
  | 'totals'
  | 'months'
  | 'top-artist'
  | 'artist-countdown'
  | 'top-albums'
  | 'top-track'
  | 'discoveries'
  | 'when'
  | 'card';
