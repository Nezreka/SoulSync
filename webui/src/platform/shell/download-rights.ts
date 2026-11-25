import { permMode, usePermMode, type PermMode } from './my-permissions';

/**
 * getting music, three ways (#2166): download right away, ask first (a
 * wishlist add is a request an admin approves), or can't request (the
 * control isn't there at all). the answer is the profile's permission map
 * (./my-permissions.ts), which falls back to canDownload() in init.js until
 * the server answers. read it at render or click time, never cache it: a
 * profile switch or an admin's change moves it.
 */
export function musicGetMode(): PermMode {
  return permMode('music');
}

/** won't get it right away: asks first, or can't ask at all. callers that
 *  offer a Request must also check profileCantRequest(). */
export function profileAsksFirst(): boolean {
  return musicGetMode() !== 'download';
}

/** getting music isn't part of this profile: no Download, no Request */
export function profileCantRequest(): boolean {
  return musicGetMode() === 'none';
}

/** the verb for a get-this-album/track control: Request or Download. */
export function acquireVerb(): 'Request' | 'Download' {
  return profileAsksFirst() ? 'Request' : 'Download';
}

/** the same, as a hook: re-renders when the profile or its map changes */
export function useMusicGet(): {
  mode: PermMode;
  asksFirst: boolean;
  cantRequest: boolean;
  verb: 'Request' | 'Download';
} {
  const mode = usePermMode('music');
  return {
    mode,
    asksFirst: mode !== 'download',
    cantRequest: mode === 'none',
    verb: mode === 'download' ? 'Download' : 'Request',
  };
}
