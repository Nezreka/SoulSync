import { QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { createElement, type ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import { useHeroVideo } from './-discover.use-hero-video';
import { videoStage } from './-discover.video-stage';

let fire: (ratio: number) => void = () => {};
class FakeObserver {
  constructor(cb: (entries: { intersectionRatio: number }[]) => void) {
    fire = (ratio) => cb([{ intersectionRatio: ratio }]);
  }
  observe() {}
  disconnect() {}
}

afterEach(() => vi.unstubAllGlobals());

/** every lookup answers with a per-artist id, and counts how often it ran */
function serveVideos() {
  const seen: { calls: number; titles: (string | null)[] } = { calls: 0, titles: [] };
  server.use(
    http.get('*/api/discover/backdrop-video', ({ request }) => {
      seen.calls += 1;
      const params = new URL(request.url).searchParams;
      seen.titles.push(params.get('title'));
      return HttpResponse.json({ success: true, video_id: `v-${params.get('artist')}` });
    }),
  );
  return seen;
}

function mount(artist: string | null, videosOn = true, record?: (string | null)[]) {
  const client = createTestQueryClient();
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client }, children);
  const view = renderHook(
    ({ a, on }: { a: string | null; on: boolean }) => {
      const hero = useHeroVideo(a, on);
      // every render's exposed id, so a test can catch a stale one showing
      // for even a single commit
      record?.push(hero.videoId);
      return hero;
    },
    { wrapper, initialProps: { a: artist, on: videosOn } },
  );
  return { ...view, client };
}

/** the hero on screen: the stage's observer sees most of it */
function seeIt(result: { current: ReturnType<typeof useHeroVideo> }) {
  act(() => result.current.ref(document.createElement('div')));
  act(() => fire(0.9));
}

describe('useHeroVideo', () => {
  it('looks the video up artist-only, once seen, and never plays it on its own', async () => {
    vi.stubGlobal('IntersectionObserver', FakeObserver);
    const seen = serveVideos();
    const { result } = mount('Tool');
    expect(seen.calls).toBe(0);
    expect(result.current.videoId).toBeNull();
    seeIt(result);
    await waitFor(() => expect(result.current.videoId).toBe('v-Tool'));
    expect(seen.calls).toBe(1);
    // the artist-only lookup: no title, the server's top official video
    expect(seen.titles).toEqual([null]);
    // on screen with a video, and still nothing moves: the slot registers
    // no video with the stage until the reader asks for the preview
    expect(result.current.preview).toBe(false);
    expect(result.current.playing).toBe(false);
    expect(result.current.soundOn).toBe(false);
  });

  it('the preview takes the sound, pauses soulsync, and closes cleanly', async () => {
    vi.stubGlobal('IntersectionObserver', FakeObserver);
    serveVideos();
    const audio = document.createElement('audio');
    audio.id = 'audio-player';
    document.body.appendChild(audio);
    Object.defineProperty(audio, 'paused', { value: false, configurable: true });
    const pause = vi.spyOn(audio, 'pause').mockImplementation(() => {});
    const { result } = mount('Tool');
    seeIt(result);
    await waitFor(() => expect(result.current.videoId).toBe('v-Tool'));

    act(() => result.current.togglePreview());
    expect(result.current.preview).toBe(true);
    expect(pause).toHaveBeenCalledTimes(1);
    // the stage granted the slot, and the slot owns the sound
    expect(result.current.playing).toBe(true);
    expect(result.current.soundOn).toBe(true);
    // not live until youtube says so: the still must not fade yet
    expect(result.current.videoLive).toBe(false);
    act(() => result.current.onLiveChange(true));
    expect(result.current.videoLive).toBe(true);

    // closing by hand is the graceful exit: the sound goes back at once,
    // the preview itself ends once the 1.4s crossfade has run
    vi.useFakeTimers();
    try {
      act(() => result.current.togglePreview());
      expect(result.current.closing).toBe(true);
      expect(result.current.preview).toBe(true);
      expect(result.current.soundOn).toBe(false);
      act(() => {
        vi.advanceTimersByTime(1400);
      });
      expect(result.current.closing).toBe(false);
      expect(result.current.preview).toBe(false);
      expect(result.current.playing).toBe(false);
      expect(result.current.soundOn).toBe(false);
      expect(result.current.videoLive).toBe(false);
    } finally {
      vi.useRealTimers();
    }
    audio.remove();
  });

  it('holds the preview open until the exit fade has fully run', async () => {
    vi.stubGlobal('IntersectionObserver', FakeObserver);
    serveVideos();
    const { result } = mount('Tool');
    seeIt(result);
    await waitFor(() => expect(result.current.videoId).toBe('v-Tool'));
    act(() => result.current.togglePreview());
    act(() => result.current.onLiveChange(true));

    vi.useFakeTimers();
    try {
      act(() => result.current.togglePreview());
      expect(result.current.closing).toBe(true);
      // still mounted and playing through the fade: unmounting it now
      // would hard-cut the video the fade is supposed to carry out
      expect(result.current.preview).toBe(true);
      expect(result.current.playing).toBe(true);
      act(() => {
        vi.advanceTimersByTime(1399);
      });
      expect(result.current.preview).toBe(true);
      expect(result.current.closing).toBe(true);
      act(() => {
        vi.advanceTimersByTime(1);
      });
      expect(result.current.preview).toBe(false);
      expect(result.current.closing).toBe(false);
      expect(result.current.playing).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it('closes cleanly even when the video never became live', async () => {
    vi.stubGlobal('IntersectionObserver', FakeObserver);
    serveVideos();
    const { result } = mount('Tool');
    seeIt(result);
    await waitFor(() => expect(result.current.videoId).toBe('v-Tool'));
    act(() => result.current.togglePreview());
    expect(result.current.videoLive).toBe(false);

    vi.useFakeTimers();
    try {
      act(() => result.current.togglePreview());
      expect(result.current.closing).toBe(true);
      act(() => {
        vi.advanceTimersByTime(1400);
      });
      expect(result.current.preview).toBe(false);
      expect(result.current.closing).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it('reopening mid-fade cancels the close and takes the sound back', async () => {
    vi.stubGlobal('IntersectionObserver', FakeObserver);
    serveVideos();
    const { result } = mount('Tool');
    seeIt(result);
    await waitFor(() => expect(result.current.videoId).toBe('v-Tool'));
    act(() => result.current.togglePreview());
    act(() => result.current.onLiveChange(true));

    vi.useFakeTimers();
    try {
      act(() => result.current.togglePreview());
      expect(result.current.closing).toBe(true);
      expect(result.current.soundOn).toBe(false);
      act(() => {
        vi.advanceTimersByTime(700);
      });
      act(() => result.current.togglePreview());
      expect(result.current.closing).toBe(false);
      expect(result.current.preview).toBe(true);
      expect(result.current.soundOn).toBe(true);
      expect(result.current.playing).toBe(true);
      // the cancelled finalize must not land later and close it anyway
      act(() => {
        vi.advanceTimersByTime(5000);
      });
      expect(result.current.preview).toBe(true);
      expect(result.current.closing).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it('reopening mid-fade keeps the seat pin for what comes after', async () => {
    vi.stubGlobal('IntersectionObserver', FakeObserver);
    serveVideos();
    const { result } = mount('Tool');
    // on screen, but only just: three fifths of the hero showing
    act(() => result.current.ref(document.createElement('div')));
    act(() => fire(0.6));
    await waitFor(() => expect(result.current.videoId).toBe('v-Tool'));
    act(() => result.current.togglePreview());
    act(() => result.current.onLiveChange(true));
    expect(result.current.playing).toBe(true);
    expect(result.current.soundOn).toBe(true);

    const budget = videoStage.budget;
    try {
      // a one-video device, and a promo below fully on screen
      act(() => {
        videoStage.budget = 1;
        videoStage.set('promo-rival', { hasVideo: true, ratio: 1 });
      });
      vi.useFakeTimers();
      try {
        act(() => result.current.togglePreview());
        expect(result.current.closing).toBe(true);
        act(() => {
          vi.advanceTimersByTime(700);
        });
        // changed their mind mid-fade: the preview goes on
        act(() => result.current.togglePreview());
        expect(result.current.closing).toBe(false);
        expect(result.current.preview).toBe(true);
        expect(result.current.soundOn).toBe(true);
      } finally {
        vi.useRealTimers();
      }
      // soulsync's own player takes the sound back. the pin is held for
      // the preview's whole life, so the hero keeps the one seat here.
      // if the reopen handed the pin back with the cancelled close, the
      // rival (1.0 against the hero's 0.75) takes the seat now and the
      // preview strands over a still.
      act(() => videoStage.setSound(null));
      expect(result.current.soundOn).toBe(false);
      expect(result.current.preview).toBe(true);
      expect(result.current.playing).toBe(true);
      expect(videoStage.isLive('promo-rival')).toBe(false);
    } finally {
      act(() => {
        videoStage.remove('promo-rival');
        videoStage.budget = budget;
      });
    }
  });

  it('scrolling away mid-fade ends it at once and cancels the fade timer', async () => {
    vi.stubGlobal('IntersectionObserver', FakeObserver);
    serveVideos();
    const { result } = mount('Tool');
    seeIt(result);
    await waitFor(() => expect(result.current.videoId).toBe('v-Tool'));
    act(() => result.current.togglePreview());
    act(() => result.current.onLiveChange(true));

    vi.useFakeTimers();
    try {
      act(() => result.current.togglePreview());
      expect(result.current.closing).toBe(true);
      act(() => fire(0.2));
      expect(result.current.preview).toBe(false);
      expect(result.current.closing).toBe(false);
      expect(result.current.videoLive).toBe(false);
      expect(result.current.soundOn).toBe(false);
      // nothing is left pending: time passing changes nothing
      act(() => {
        vi.advanceTimersByTime(5000);
      });
      expect(result.current.preview).toBe(false);
      expect(result.current.closing).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it('holds its seat through the exit fade when a fuller banner wants it', async () => {
    vi.stubGlobal('IntersectionObserver', FakeObserver);
    serveVideos();
    const { result } = mount('Tool');
    // on screen, but only just: three fifths of the hero showing
    act(() => result.current.ref(document.createElement('div')));
    act(() => fire(0.6));
    await waitFor(() => expect(result.current.videoId).toBe('v-Tool'));
    act(() => result.current.togglePreview());
    act(() => result.current.onLiveChange(true));
    expect(result.current.playing).toBe(true);
    expect(result.current.soundOn).toBe(true);

    const budget = videoStage.budget;
    try {
      // a one-video device, and a promo below fully on screen. while the
      // hero owns the sound it keeps the seat regardless, so the contest
      // only starts when the close hands the sound back
      act(() => {
        videoStage.budget = 1;
        videoStage.set('promo-rival', { hasVideo: true, ratio: 1 });
      });
      expect(result.current.playing).toBe(true);
      expect(videoStage.isLive('promo-rival')).toBe(false);

      vi.useFakeTimers();
      try {
        act(() => result.current.togglePreview());
        expect(result.current.closing).toBe(true);
        expect(result.current.soundOn).toBe(false);
        // the fade's whole point is the player staying mounted under it:
        // the seat pin, held since the preview opened, holds the seat.
        // without it the rival (fully showing, against the hero's
        // 0.6) takes the one seat here and the video hard-cuts.
        expect(result.current.playing).toBe(true);
        expect(videoStage.isLive('promo-rival')).toBe(false);
        act(() => {
          vi.advanceTimersByTime(1399);
        });
        expect(result.current.playing).toBe(true);
        act(() => {
          vi.advanceTimersByTime(1);
        });
        expect(result.current.preview).toBe(false);
        expect(result.current.closing).toBe(false);
        expect(result.current.playing).toBe(false);
        // the pin is handed back with the seat: the rival plays now
        expect(videoStage.isLive('promo-rival')).toBe(true);
      } finally {
        vi.useRealTimers();
      }
    } finally {
      act(() => {
        videoStage.remove('promo-rival');
        videoStage.budget = budget;
      });
    }
  });

  it('keeps its seat when soulsync’s player takes the sound back mid-preview', async () => {
    vi.stubGlobal('IntersectionObserver', FakeObserver);
    serveVideos();
    const { result } = mount('Tool');
    // on screen, but only just: three fifths of the hero showing
    act(() => result.current.ref(document.createElement('div')));
    act(() => fire(0.6));
    await waitFor(() => expect(result.current.videoId).toBe('v-Tool'));
    act(() => result.current.togglePreview());
    act(() => result.current.onLiveChange(true));
    expect(result.current.playing).toBe(true);
    expect(result.current.soundOn).toBe(true);

    const budget = videoStage.budget;
    try {
      // a one-video device, and a promo below fully on screen. while the
      // hero owns the sound it keeps the seat regardless — the contest
      // starts when soulsync's own player takes the sound back, which is
      // the stage clearing its sound owner (what the stage's capture-phase
      // `play` listener does when the audio player starts a track).
      act(() => {
        videoStage.budget = 1;
        videoStage.set('promo-rival', { hasVideo: true, ratio: 1 });
        videoStage.setSound(null);
      });
      expect(result.current.soundOn).toBe(false);
      // the header's promise: the video stays, muted, until the reader
      // closes it. soundless at 0.6 the hero scores 0.75 against the
      // rival's 1.0 — only the seat pin, held since the preview opened,
      // keeps the player mounted. without it the preview strands: a lit
      // close button over a still, the rotation held, nothing playing.
      expect(result.current.preview).toBe(true);
      expect(result.current.playing).toBe(true);
      expect(result.current.videoLive).toBe(true);
      expect(videoStage.isLive('promo-rival')).toBe(false);
    } finally {
      act(() => {
        videoStage.remove('promo-rival');
        videoStage.budget = budget;
      });
    }
  });

  it('the stage switching off mid-preview closes it at once, no fade', async () => {
    vi.stubGlobal('IntersectionObserver', FakeObserver);
    serveVideos();
    const { result } = mount('Tool');
    seeIt(result);
    await waitFor(() => expect(result.current.videoId).toBe('v-Tool'));
    act(() => result.current.togglePreview());
    act(() => result.current.onLiveChange(true));
    expect(result.current.preview).toBe(true);

    try {
      // reduced motion arriving is the stage switching off under a lit
      // "close preview" button: left alone it would sit there forever,
      // holding the rotation over a player that can never play
      act(() => {
        videoStage.reducedMotion = true;
        videoStage.touch();
      });
      expect(result.current.preview).toBe(false);
      expect(result.current.closing).toBe(false);
      expect(result.current.playing).toBe(false);
      expect(result.current.soundOn).toBe(false);
    } finally {
      // the stage is a shared singleton: hand it back on
      act(() => {
        videoStage.reducedMotion = false;
        videoStage.touch();
      });
    }
  });

  it('stops at once when a refetch answers with no video mid-preview', async () => {
    vi.stubGlobal('IntersectionObserver', FakeObserver);
    let gone = false;
    server.use(
      http.get('*/api/discover/backdrop-video', () =>
        HttpResponse.json({ success: true, video_id: gone ? null : 'v-Tool' }),
      ),
    );
    const { result, client } = mount('Tool');
    seeIt(result);
    await waitFor(() => expect(result.current.videoId).toBe('v-Tool'));
    act(() => result.current.togglePreview());
    act(() => result.current.onLiveChange(true));
    expect(result.current.preview).toBe(true);
    expect(result.current.playing).toBe(true);
    expect(result.current.soundOn).toBe(true);

    // the block list and the feedback menu invalidate the whole discover
    // prefix, and this refetch answers that the artist has no video
    // after all. left alone the preview would sit on the stage's seat
    // and sound over nothing, and the toggle keys off the id — with the
    // id gone there would be no button left to close it with.
    gone = true;
    await act(async () => {
      await client.invalidateQueries({ queryKey: ['discover'] });
    });
    await waitFor(() => expect(result.current.preview).toBe(false));
    expect(result.current.closing).toBe(false);
    expect(result.current.playing).toBe(false);
    expect(result.current.soundOn).toBe(false);
    expect(result.current.videoLive).toBe(false);
    expect(result.current.videoId).toBeNull();
  });

  it('hands out no video id while the stage is off, and will not open a preview', async () => {
    vi.stubGlobal('IntersectionObserver', FakeObserver);
    const seen = serveVideos();
    act(() => {
      videoStage.reducedMotion = true;
      videoStage.touch();
    });
    try {
      const { result } = mount('Tool');
      seeIt(result);
      // the lookup still answers; the hook just won't hand the id out
      // while the stage is off, so no button exists for a preview that
      // could never play — a rendered button here would click into a
      // startPreview that refuses, silently.
      await waitFor(() => expect(seen.calls).toBe(1));
      expect(result.current.videoId).toBeNull();
      act(() => result.current.togglePreview());
      expect(result.current.preview).toBe(false);
      expect(result.current.playing).toBe(false);
      expect(result.current.soundOn).toBe(false);
      // the stage back on (the tab visible again, the motion preference
      // lifted): the id — and with it the button — returns.
      act(() => {
        videoStage.reducedMotion = false;
        videoStage.touch();
      });
      await waitFor(() => expect(result.current.videoId).toBe('v-Tool'));
    } finally {
      act(() => {
        videoStage.reducedMotion = false;
        videoStage.touch();
      });
    }
  });

  it('a slide change never exposes the previous artist’s video, not even for one render', async () => {
    vi.stubGlobal('IntersectionObserver', FakeObserver);
    serveVideos();
    const exposed: (string | null)[] = [];
    const { result, rerender } = mount('Tool', true, exposed);
    seeIt(result);
    await waitFor(() => expect(result.current.videoId).toBe('v-Tool'));

    const mark = exposed.length;
    rerender({ a: 'Other', on: true });
    // the id is derived from the lookup, not mirrored through state: the
    // old mirror lagged one commit and showed the last artist's video
    // (and its button) on the new slide
    expect(exposed.slice(mark)).not.toContain('v-Tool');
    expect(result.current.videoId).toBeNull();
    await waitFor(() => expect(result.current.videoId).toBe('v-Other'));
  });

  it('a new artist ends the preview and looks up their own video', async () => {
    vi.stubGlobal('IntersectionObserver', FakeObserver);
    serveVideos();
    const { result, rerender } = mount('Tool');
    seeIt(result);
    await waitFor(() => expect(result.current.videoId).toBe('v-Tool'));
    act(() => result.current.togglePreview());
    expect(result.current.preview).toBe(true);

    rerender({ a: 'Other', on: true });
    expect(result.current.preview).toBe(false);
    expect(result.current.soundOn).toBe(false);
    expect(result.current.playing).toBe(false);
    await waitFor(() => expect(result.current.videoId).toBe('v-Other'));
    expect(result.current.preview).toBe(false);
  });

  it('scrolling the hero out of view ends the preview', async () => {
    vi.stubGlobal('IntersectionObserver', FakeObserver);
    serveVideos();
    const { result } = mount('Tool');
    seeIt(result);
    await waitFor(() => expect(result.current.videoId).toBe('v-Tool'));
    act(() => result.current.togglePreview());
    act(() => result.current.onLiveChange(true));
    expect(result.current.preview).toBe(true);

    act(() => fire(0.2));
    expect(result.current.preview).toBe(false);
    expect(result.current.playing).toBe(false);
    expect(result.current.soundOn).toBe(false);
    expect(result.current.videoLive).toBe(false);
  });

  it('a video youtube refuses closes the preview and is never offered again', async () => {
    vi.stubGlobal('IntersectionObserver', FakeObserver);
    serveVideos();
    const { result } = mount('Tool');
    seeIt(result);
    await waitFor(() => expect(result.current.videoId).toBe('v-Tool'));
    act(() => result.current.togglePreview());
    expect(result.current.preview).toBe(true);

    act(() => result.current.onUnplayable('v-Tool'));
    expect(result.current.preview).toBe(false);
    expect(result.current.playing).toBe(false);
    expect(result.current.videoId).toBeNull();
  });

  it('with backdrops off there is no fetch and no video, and asking does nothing', async () => {
    vi.stubGlobal('IntersectionObserver', FakeObserver);
    const seen = serveVideos();
    const { result } = mount('Tool', false);
    seeIt(result);
    await new Promise((r) => setTimeout(r, 20));
    expect(seen.calls).toBe(0);
    expect(result.current.videoId).toBeNull();
    act(() => result.current.togglePreview());
    expect(result.current.preview).toBe(false);
  });

  it('switching backdrops off mid-preview closes it', async () => {
    vi.stubGlobal('IntersectionObserver', FakeObserver);
    serveVideos();
    const { result, rerender } = mount('Tool', true);
    seeIt(result);
    await waitFor(() => expect(result.current.videoId).toBe('v-Tool'));
    act(() => result.current.togglePreview());
    expect(result.current.preview).toBe(true);

    rerender({ a: 'Tool', on: false });
    expect(result.current.preview).toBe(false);
    expect(result.current.playing).toBe(false);
    expect(result.current.videoId).toBeNull();
  });
});
