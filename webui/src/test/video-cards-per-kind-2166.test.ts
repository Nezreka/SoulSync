/**
 * #2166 follow-ups 18 and 20: the video side's get / ask controls follow the
 * profile's mode for that kind of title (movies, shows, youtube each have
 * their own), not the one music switch. VideoRequests.canGet / mayAsk answer;
 * until they're loaded the old canDownload() stands in.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { extractFunction } from './vanilla-extract';

const GET = readFileSync(resolve(process.cwd(), 'static/video/video-get-modal.js'), 'utf8');
const DETAIL = readFileSync(resolve(process.cwd(), 'static/video/video-detail.js'), 'utf8');
const YT = readFileSync(resolve(process.cwd(), 'static/video/video-youtube.js'), 'utf8');

type Modes = Record<string, 'download' | 'ask' | 'none'>;

function cards(modes: Modes | null, musicCanDownload = true) {
  const w = window as unknown as Record<string, unknown>;
  w.VideoRequests = modes
    ? {
        canGet: (k: string) => modes[k === 'episode' ? 'show' : k] === 'download',
        mayAsk: (k: string) => modes[k === 'episode' ? 'show' : k] !== 'none',
        cardButton: (o: { kind: string }) =>
          modes[o.kind] === 'none' ? '' : `<button data-vreq-card data-kind="${o.kind}"></button>`,
      }
    : undefined;
  w.VideoWatchlist = undefined;
  vi.stubGlobal('canDownload', () => musicCanDownload);
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  return new Function(
    `function btn(o) { return '<button class="vget-btn" data-vget-kind="' + o.kind + '"></button>'; }
     function isAiring() { return false; }
     ${extractFunction('canGetKind', GET)}
     ${extractFunction('cardButton', GET)}
     return cardButton;`,
  )() as (o: Record<string, unknown>) => string;
}

afterEach(() => {
  vi.unstubAllGlobals();
  delete (window as unknown as Record<string, unknown>).VideoRequests;
});

describe('the video card controls', () => {
  it('a movie downloader with music on ask first still gets the Get button', () => {
    const card = cards({ movie: 'download', show: 'ask' }, false);
    expect(card({ kind: 'movie', tmdbId: 9, title: 'Heat' })).toContain('vget-btn');
    // and asks for shows
    const show = card({ kind: 'show', tmdbId: 5, title: 'X', status: 'Ended' });
    expect(show).toContain('data-vreq-card');
    expect(show).not.toContain('vget-btn');
  });

  it('a music downloader who asks first for movies gets the ask button, not Get', () => {
    const card = cards({ movie: 'ask', show: 'download' }, true);
    const movie = card({ kind: 'movie', tmdbId: 9, title: 'Heat' });
    expect(movie).toContain('data-vreq-card');
    expect(movie).not.toContain('vget-btn');
  });

  it("a kind the profile can't request has no control at all", () => {
    const card = cards({ movie: 'none', show: 'download' });
    expect(card({ kind: 'movie', tmdbId: 9, title: 'Heat' })).toBe('');
  });

  it('owned titles have nothing to ask for', () => {
    const card = cards({ movie: 'ask', show: 'ask' });
    expect(card({ kind: 'movie', tmdbId: 9, source: 'library' })).toBe('');
  });

  it('falls back to the old switch until the modes are loaded', () => {
    expect(cards(null, true)({ kind: 'movie', tmdbId: 9 })).toContain('vget-btn');
    expect(cards(null, false)({ kind: 'movie', tmdbId: 9 })).toBe('');
  });
});

describe('the detail page and youtube', () => {
  it('detail asks VideoRequests per kind, with the old switch as the fallback', () => {
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    const vdCanGet = new Function(`${extractFunction('vdCanGet', DETAIL)}\nreturn vdCanGet;`)() as (
      k: string,
    ) => boolean;
    const w = window as unknown as Record<string, unknown>;
    w.VideoRequests = { canGet: (k: string) => k === 'youtube' };
    vi.stubGlobal('canDownload', () => false);
    expect(vdCanGet('youtube')).toBe(true);
    expect(vdCanGet('show')).toBe(false);
    delete w.VideoRequests;
    vi.stubGlobal('canDownload', () => true);
    expect(vdCanGet('show')).toBe(true);
  });

  it('every get decision on the detail page asks per kind', () => {
    // the title's actions, the show's episode rows, the youtube rows and bar
    expect(extractFunction('renderActions', DETAIL)).toContain('vdCanGet(d.kind)');
    expect(DETAIL).toContain("canDownload: vdCanGet('show'),");
    expect(extractFunction('ytEpisodeRow', DETAIL)).toContain(
      "window.VideoRequests.canGet('youtube')",
    );
    expect(extractFunction('seasonActionsHtml', DETAIL)).toContain(
      "window.VideoRequests.canGet('youtube')",
    );
  });

  it('following a channel follows the youtube mode', () => {
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    const downloadsOff = new Function(
      `${extractFunction('downloadsOff', YT)}\nreturn downloadsOff;`,
    )() as () => boolean;
    const w = window as unknown as Record<string, unknown>;
    vi.stubGlobal('showToast', () => {});
    vi.stubGlobal('canDownload', () => false);
    w.VideoRequests = { canGet: (k: string) => k === 'youtube' };
    expect(downloadsOff()).toBe(false);
    w.VideoRequests = { canGet: () => false };
    vi.stubGlobal('canDownload', () => true);
    expect(downloadsOff()).toBe(true);
  });
});

describe('calendar and the video dashboard', () => {
  it.each(['src/routes/calendar/route.tsx', 'src/routes/dashboard/route.tsx'])(
    '%s offers the episode get buttons from the TV mode',
    (file) => {
      const src = readFileSync(resolve(process.cwd(), file), 'utf8');
      expect(src).toContain("usePermMode('tv')");
      expect(src).toContain("canDownload={tvGet === 'download'}");
      expect(src).not.toContain('profileAsksFirst');
    },
  );
});
