/**
 * a profile without download rights asks instead: its wishlist adds are
 * requests an admin approves. canDownload() lives in init.js (a classic
 * script, so it's a window function). read it at render or click time,
 * never cache it: a profile switch changes the answer.
 */
export function profileAsksFirst(): boolean {
  const can = (window as unknown as { canDownload?: () => boolean }).canDownload;
  return typeof can === 'function' && !can();
}

/** the verb for a get-this-album/track control: Request or Download. */
export function acquireVerb(): 'Request' | 'Download' {
  return profileAsksFirst() ? 'Request' : 'Download';
}
