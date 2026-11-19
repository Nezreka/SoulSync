import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * #2196: the enrichment workers are the admins'. the dashboards drop the orbs
 * for a member (the React tests cover that); these run the vanilla scripts
 * behind them: the video worker buttons never ask the status routes for a
 * member, and neither Manage Workers modal opens for one, whatever calls it.
 */

const read = (path: string) => readFileSync(resolve(process.cwd(), path), 'utf8');
const VIDEO_BUTTONS = read('static/video/video-enrichment.js');
const VIDEO_MANAGER = read('static/video/video-enrichment-manager.js');
const MUSIC_MANAGER = read('static/enrichment-manager.js');

type Ctx = { profileId: number; isAdmin: boolean } | null;
type Win = Window & {
  getCurrentProfileContext?: () => Ctx;
  videoEnrichmentPrime?: () => void;
};

const fetchMock = vi.fn((..._args: unknown[]) => new Promise<Response>(() => {}));

function as(ctx: Ctx) {
  (window as Win).getCurrentProfileContext = () => ctx;
}

function run(source: string) {
  // the scripts read getCurrentProfileContext as a bare global, the way
  // init.js publishes it
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  new Function(source)();
}

const asked = () => fetchMock.mock.calls.map((call) => String(call[0]));

beforeEach(() => {
  fetchMock.mockClear();
  vi.stubGlobal('fetch', fetchMock);
  document.body.setAttribute('data-side', 'video');
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete (window as Win).getCurrentProfileContext;
  delete (window as Win).videoEnrichmentPrime;
  document.body.removeAttribute('data-side');
  document.body.innerHTML = '';
});

describe('the video worker buttons', () => {
  it("never ask a member's status, on load or on the page's prime", () => {
    as({ profileId: 4, isAdmin: false });
    run(VIDEO_BUTTONS);
    (window as Win).videoEnrichmentPrime?.();
    document.dispatchEvent(new Event('visibilitychange'));
    expect(asked().filter((url) => url.includes('/api/video/enrichment'))).toEqual([]);
  });

  it('fail closed before the profile is known', () => {
    as(null);
    run(VIDEO_BUTTONS);
    expect(asked()).toEqual([]);
  });

  it('prime an admin with the status bundle', () => {
    as({ profileId: 1, isAdmin: true });
    run(VIDEO_BUTTONS);
    expect(asked()).toContain('/api/video/enrichment/status-all');
  });
});

describe('the video Manage Workers modal', () => {
  it('does not open for a member', () => {
    as({ profileId: 4, isAdmin: false });
    run(VIDEO_MANAGER);
    document.dispatchEvent(new CustomEvent('soulsync:video-open-workers'));
    expect(document.getElementById('vem-overlay')).toBeNull();
    expect(asked()).toEqual([]);
  });

  it('opens for an admin', () => {
    as({ profileId: 1, isAdmin: true });
    run(VIDEO_MANAGER);
    document.dispatchEvent(new CustomEvent('soulsync:video-open-workers'));
    expect(document.getElementById('vem-overlay')).not.toBeNull();
  });
});

describe('the music Manage Workers modal', () => {
  const open = async (ctx: Ctx) => {
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    const fn = new Function(
      'getCurrentProfileContext',
      `${extractFunction('_emViewerIsAdmin', MUSIC_MANAGER)}\n` +
        `${extractFunction('openEnrichmentManager', MUSIC_MANAGER)}\n` +
        'return openEnrichmentManager;',
    )(() => ctx) as () => Promise<void>;
    await fn().catch(() => {});
  };

  it('does not open for a member', async () => {
    await open({ profileId: 4, isAdmin: false });
    expect(document.getElementById('enrichment-manager-overlay')).toBeNull();
    expect(asked()).toEqual([]);
  });

  it('starts building for an admin', async () => {
    await open({ profileId: 1, isAdmin: true });
    expect(document.getElementById('enrichment-manager-overlay')).not.toBeNull();
  });
});
