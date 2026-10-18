const STORAGE_KEY = 'soulsync-review-musicbrainz-browser-url';

export function musicBrainzBrowserBase(): string {
  if (typeof window === 'undefined') return 'https://musicbrainz.org';
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    if (!value) return 'https://musicbrainz.org';
    const url = new URL(value);
    if (!['http:', 'https:'].includes(url.protocol)) return 'https://musicbrainz.org';
    return `${url.origin}${url.pathname.replace(/\/$/, '')}`;
  } catch {
    return 'https://musicbrainz.org';
  }
}

export function setMusicBrainzBrowserBase(value: string): void {
  const trimmed = value.trim();
  if (!trimmed) {
    window.localStorage.removeItem(STORAGE_KEY);
    return;
  }
  const url = new URL(trimmed);
  if (!['http:', 'https:'].includes(url.protocol)) throw new Error('Use an HTTP or HTTPS URL');
  window.localStorage.setItem(STORAGE_KEY, `${url.origin}${url.pathname.replace(/\/$/, '')}`);
}

export function musicBrainzBrowserUrl(kind: 'release' | 'recording', id: string): string {
  return `${musicBrainzBrowserBase()}/${kind}/${encodeURIComponent(id)}`;
}
