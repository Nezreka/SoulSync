import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * #2208: the spotify ban is the server's operational state, the admin's to
 * hear about. the server sends a member a status payload without it; these
 * run the real vanilla handlers to pin the second guard: whatever a member's
 * page is handed, no ban modal, no "restored" toast, no media scan toast.
 */

const read = (path: string) => readFileSync(resolve(process.cwd(), path), 'utf8');
const CORE = read('static/core.js');
const EXTRA = read('static/pages-extra.js');
const PLAYER = read('static/media-player.js');

type Profile = { id: number; is_admin: boolean } | null;

interface Harness {
  status: (data: unknown) => void;
  limit: (info: unknown) => void;
  mediaScan: (data: unknown) => void;
  setShown: (v: boolean) => void;
  shown: () => boolean;
  modal: ReturnType<typeof vi.fn>;
  close: ReturnType<typeof vi.fn>;
  toast: ReturnType<typeof vi.fn>;
}

function harness(profile: Profile): Harness {
  const modal = vi.fn();
  const close = vi.fn();
  const toast = vi.fn();
  const body = `
    let currentProfile = profile;
    let currentPage = 'dashboard';
    let _lastStatusPayload = null;
    let _isSoulsyncStandalone = false;
    let _spotifyRateLimitShown = false;
    let _spotifyInCooldown = false;
    let _rateLimitModalOpen = false;
    let _rateLimitExpiresAt = 0;
    let _lastMediaScanStatus = null;
    const showRateLimitModal = modal;
    const closeRateLimitModal = close;
    const showToast = toast;
    function updateSidebarServiceStatus() {}
    function _updateDlNavBadge() {}
    ${extractFunction('_statusViewerIsAdmin', CORE)}
    ${extractFunction('handleServiceStatusUpdate', CORE)}
    ${extractFunction('handleSpotifyRateLimit', EXTRA)}
    ${extractFunction('updateMediaScanFromData', PLAYER)}
    return {
      status: handleServiceStatusUpdate,
      limit: handleSpotifyRateLimit,
      mediaScan: updateMediaScanFromData,
      setShown: (v) => { _spotifyRateLimitShown = v; },
      shown: () => _spotifyRateLimitShown,
    };
  `;
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const api = new Function('profile', 'modal', 'close', 'toast', body)(
    profile,
    modal,
    close,
    toast,
  ) as Omit<Harness, 'modal' | 'close' | 'toast'>;
  return { ...api, modal, close, toast };
}

const BANNED = {
  metadata_source: { source: 'spotify' },
  spotify: {
    rate_limited: true,
    rate_limit: { active: true, remaining_seconds: 13800, endpoint: 'search_artists' },
  },
};
const COOLDOWN = { metadata_source: { source: 'spotify' }, spotify: { post_ban_cooldown: 30 } };

afterEach(() => {
  vi.restoreAllMocks();
});

describe('the spotify ban modal (#2208)', () => {
  it('a member never gets it, even handed a payload with the ban', () => {
    const h = harness({ id: 4, is_admin: false });
    h.status(BANNED);
    h.limit(BANNED.spotify.rate_limit);
    expect(h.modal).not.toHaveBeenCalled();
    expect(h.toast).not.toHaveBeenCalled();
  });

  it('a member gets no cooldown or restored toast', () => {
    const h = harness({ id: 4, is_admin: false });
    h.setShown(true);
    h.status(COOLDOWN);
    h.status({ metadata_source: { source: 'spotify' }, spotify: {} });
    h.limit(null);
    expect(h.toast).not.toHaveBeenCalled();
  });

  it('a modal raised before the switch to a member closes', () => {
    const h = harness({ id: 4, is_admin: false });
    h.setShown(true);
    h.status(BANNED);
    expect(h.close).toHaveBeenCalled();
    expect(h.shown()).toBe(false);
  });

  it('the admin still gets it, and the restored toast after', () => {
    const h = harness({ id: 1, is_admin: true });
    h.status(BANNED);
    expect(h.modal).toHaveBeenCalledTimes(1);
    h.status({ metadata_source: { source: 'spotify' }, spotify: {} });
    expect(h.toast).toHaveBeenCalledWith('Spotify access restored', 'success');
  });

  it('a second admin (is_admin, not profile 1) gets it too', () => {
    const h = harness({ id: 3, is_admin: true });
    h.limit(BANNED.spotify.rate_limit);
    expect(h.modal).toHaveBeenCalledTimes(1);
  });
});

describe('the media scan toast (#2208)', () => {
  const scanning = { success: true, status: { status: 'scanning' } };
  const idle = { success: true, status: { status: 'idle' } };

  it('is not shown to a member', () => {
    const h = harness({ id: 4, is_admin: false });
    h.mediaScan(scanning);
    h.mediaScan(idle);
    expect(h.toast).not.toHaveBeenCalled();
  });

  it('is shown to the admin', () => {
    const h = harness({ id: 1, is_admin: true });
    h.mediaScan(scanning);
    h.mediaScan(idle);
    expect(h.toast).toHaveBeenCalledWith('✅ Media scan completed', 'success', 3000);
  });
});
