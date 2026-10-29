import { http, HttpResponse } from 'msw';

/** Background services loaded by the real shell during review journeys. */
const idleServices: Record<string, Record<string, unknown>> = {
  '/api/settings': { success: true, settings: {} },
  '/api/server-activity': { success: true, sessions: [], enabled: false },
  '/api/auth/oidc/available': { enabled: false, oidc_only: false },
  '/api/profiles/me/oidc': { success: true, enabled: false, connected: false },
  '/api/security/oidc-only': { success: true, oidc_only: false },
  '/api/video/scan/status': { state: 'idle' },
  '/api/video/issues/counts': { success: true, counts: { open: 0, total: 0 } },
  '/api/video/watchlist/counts': { success: true, show: 0, person: 0, studio: 0 },
  '/api/video/youtube/channels': { success: true, channels: [] },
  '/api/video/wishlist/counts': { success: true, total: 0 },
  '/api/requests/music/counts': { success: true, pending: 0, updates: 0 },
  '/api/discover/inbox/counts': { success: true, pending: 0, total: 0 },
  '/api/video/requests/counts': { success: true, pending: 0, updates: 0 },
  '/api/wishlist/count': { success: true, count: 0 },
  '/api/watchlist/count': { success: true, count: 0 },
  '/api/update-check': { update_available: false },
  '/api/artist_bubbles/hydrate': { success: true, bubbles: {}, stats: {} },
  '/api/search_bubbles/hydrate': { success: true, bubbles: {}, stats: {} },
  '/api/discover_downloads/hydrate': { success: true, downloads: {} },
  '/api/issues/counts': { success: true, counts: { open: 0, total: 0 } },
  '/api/video/overlays/apply/status': { running: false, phase: 'idle' },
  '/api/video/collections/sync/status': { running: false, phase: 'idle' },
  '/api/video/collections/posters/regenerate/status': { running: false, phase: 'idle' },
  '/api/video/bulk/status': { running: false, phase: 'idle' },
};

export const reviewShellHandlers = Object.entries(idleServices).map(([path, body]) =>
  http.get(`*${path}`, () => HttpResponse.json(body)),
);
