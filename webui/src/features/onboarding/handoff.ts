/**
 * A playlist link pasted during setup, handed to the sync page across the
 * reload that ends setup. Session storage, read once.
 */

const KEY = 'soulsync:onboarding-playlist';

export function stashSetupPlaylistLink(url: string): void {
  try {
    if (url.trim()) window.sessionStorage.setItem(KEY, url.trim());
  } catch {
    // the link is only a convenience
  }
}

export function takeSetupPlaylistLink(): string | null {
  try {
    const url = window.sessionStorage.getItem(KEY);
    if (url) window.sessionStorage.removeItem(KEY);
    return url;
  } catch {
    return null;
  }
}
