/**
 * The Library discography source's choices. The picker itself sits under the
 * primary metadata source in Settings > Connections, and is React
 * (tabs/connections-metadata.tsx, SETTINGS_PORT.md step 10a).
 */

export const LIBRARY_DISCOGRAPHY_SOURCE_OPTIONS = [
  { value: 'primary', label: 'Use primary metadata source' },
  { value: 'automatic', label: 'Automatic commercial catalogue' },
  { value: 'itunes', label: 'iTunes' },
  { value: 'deezer', label: 'Deezer' },
  { value: 'musicbrainz', label: 'MusicBrainz' },
  { value: 'spotify', label: 'Spotify' },
] as const;

export type LibraryDiscographySource = (typeof LIBRARY_DISCOGRAPHY_SOURCE_OPTIONS)[number]['value'];

const VALID_SOURCES = new Set<string>(
  LIBRARY_DISCOGRAPHY_SOURCE_OPTIONS.map((option) => option.value),
);

export function normalizeLibraryDiscographySource(value: unknown): LibraryDiscographySource {
  const normalized = typeof value === 'string' ? value.trim().toLowerCase() : '';
  return VALID_SOURCES.has(normalized) ? (normalized as LibraryDiscographySource) : 'primary';
}
