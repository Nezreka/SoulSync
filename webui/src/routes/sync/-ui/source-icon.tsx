/**
 * A source's real brand mark.
 *
 * All twelve exist already — Spotify, Tidal, Deezer, Qobuz, YouTube, Beatport,
 * iTunes, ListenBrainz, Last.fm, SoulSync Discovery, Mirrored and Import — as
 * inline-SVG background images on `.<source>-icon` classes in style.css. Until
 * now only the tab strip used them, so everywhere else fell back to emoji: a
 * Spotify playlist wore 🎵 and a Deezer one wore 🎧, which say nothing about
 * the service and read as placeholder art.
 *
 * The class is the asset. This renders the same `tab-icon <source>-icon` pair
 * the strip uses, so a brand mark only ever has to be updated in one place.
 */

/** Vertical/source id → the sprite class that carries its mark. */
const SOURCE_ICON_CLASSES: Readonly<Record<string, string>> = {
  spotify: 'spotify-icon',
  spotify_public: 'spotify-icon',
  'spotify-public': 'spotify-icon',
  tidal: 'tidal-icon',
  qobuz: 'qobuz-icon',
  deezer: 'deezer-icon',
  'deezer-link': 'deezer-icon',
  deezer_arl: 'deezer-icon',
  youtube: 'youtube-icon',
  beatport: 'beatport-icon',
  itunes: 'itunes-icon',
  itunes_link: 'itunes-icon',
  'itunes-link': 'itunes-icon',
  listenbrainz: 'listenbrainz-icon',
  'listenbrainz-sync': 'listenbrainz-icon',
  lastfm: 'lastfm-icon',
  'lastfm-sync': 'lastfm-icon',
  soulsync_discovery: 'soulsync-discovery-icon',
  'soulsync-discovery-sync': 'soulsync-discovery-icon',
  mirrored: 'mirrored-icon',
  file: 'import-file-icon',
  'import-file': 'import-file-icon',
};

/** The sprite class for a source, or null when we have no mark for it. */
export function sourceIconClass(source: string | null | undefined): string | null {
  return SOURCE_ICON_CLASSES[source ?? ''] ?? null;
}

export interface SourceIconProps {
  source: string | null | undefined;
  /** Drawn when the source has no brand mark — a generic list glyph. */
  fallback?: string;
}

export function SourceIcon({ source, fallback = '📋' }: SourceIconProps) {
  const cls = sourceIconClass(source);
  if (!cls) return <span aria-hidden="true">{fallback}</span>;
  return <span className={`tab-icon ${cls}`} aria-hidden="true" />;
}
