export const SERVICE_NAMES: Record<string, string> = {
  spotify: 'Spotify',
  musicbrainz: 'MusicBrainz',
  deezer: 'Deezer',
  discogs: 'Discogs',
  audiodb: 'AudioDB',
  itunes: 'Apple Music',
  lastfm: 'Last.fm',
  genius: 'Genius',
  tidal: 'Tidal',
  qobuz: 'Qobuz',
  amazon: 'Amazon',
  bandcamp: 'Bandcamp',
  jiosaavn: 'JioSaavn',
};

/** two letters per service, the same in every row so the eye learns them once. */
export const SERVICE_MONOGRAMS: Record<string, string> = {
  spotify: 'SP',
  musicbrainz: 'MB',
  deezer: 'DZ',
  discogs: 'DC',
  audiodb: 'AD',
  itunes: 'IT',
  lastfm: 'LF',
  genius: 'GE',
  tidal: 'TD',
  qobuz: 'QB',
  amazon: 'AZ',
  bandcamp: 'BC',
  jiosaavn: 'JS',
};

export function sourceState(status: string | undefined): 'matched' | 'not-found' | 'pending' {
  return status === 'matched' ? 'matched' : status === 'not_found' ? 'not-found' : 'pending';
}

export function serviceName(service: string, fallback = ''): string {
  return SERVICE_NAMES[service] ?? fallback ?? service;
}

export function monogram(service: string, fallback: string): string {
  return SERVICE_MONOGRAMS[service] ?? fallback.slice(0, 2).toUpperCase();
}
