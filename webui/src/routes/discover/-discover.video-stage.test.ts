import { describe, expect, it, vi } from 'vitest';

import { chooseActive, MIN_VISIBLE, VideoStage, type SlotState } from './-discover.video-stage';

const slots = (entries: [string, SlotState][]) => new Map(entries);

describe('chooseActive', () => {
  it('gives the stage to the most visible banner that has a video', () => {
    expect(
      chooseActive(
        slots([
          ['hero', { ratio: 0.6, hasVideo: true }],
          ['spotlight', { ratio: 0.9, hasVideo: true }],
        ]),
        true,
      ),
    ).toBe('spotlight');
  });

  it('never picks a banner without a video, however visible', () => {
    expect(
      chooseActive(
        slots([
          ['hero', { ratio: 1, hasVideo: false }],
          ['spotlight', { ratio: 0.7, hasVideo: true }],
        ]),
        true,
      ),
    ).toBe('spotlight');
  });

  it('needs at least half a banner on screen', () => {
    expect(MIN_VISIBLE).toBe(0.5);
    expect(chooseActive(slots([['hero', { ratio: 0.49, hasVideo: true }]]), true)).toBeNull();
    expect(chooseActive(slots([['hero', { ratio: 0.5, hasVideo: true }]]), true)).toBe('hero');
  });

  it('plays nothing when disabled', () => {
    expect(chooseActive(slots([['hero', { ratio: 1, hasVideo: true }]]), false)).toBeNull();
  });
});

describe('VideoStage', () => {
  it('hands over as banners scroll, and tells its listeners', () => {
    const stage = new VideoStage();
    const heard = vi.fn();
    stage.subscribe(heard);
    stage.set('hero', { ratio: 1, hasVideo: true });
    expect(stage.getActive()).toBe('hero');
    stage.set('hero', { ratio: 0.1 });
    stage.set('spotlight', { ratio: 0.8, hasVideo: true });
    expect(stage.getActive()).toBe('spotlight');
    stage.remove('spotlight');
    expect(stage.getActive()).toBeNull();
    // hero, nobody (hero scrolled to 10%), spotlight, nobody
    expect(heard).toHaveBeenCalledTimes(4);
  });

  it('stops everything while the tab is hidden, with reduced motion, or switched off', () => {
    const stage = new VideoStage();
    stage.set('hero', { ratio: 1, hasVideo: true });
    stage.pageVisible = false;
    stage.recompute();
    expect(stage.getActive()).toBeNull();
    stage.pageVisible = true;
    stage.reducedMotion = true;
    stage.recompute();
    expect(stage.getActive()).toBeNull();
    stage.reducedMotion = false;
    stage.userEnabled = false;
    stage.recompute();
    expect(stage.getActive()).toBeNull();
    stage.userEnabled = true;
    stage.recompute();
    expect(stage.getActive()).toBe('hero');
  });
});

describe('the hooks', () => {
  it('useVideoBackdropsEnabled remembers the switch', async () => {
    const { act, renderHook } = await import('@testing-library/react');
    const { useVideoBackdropsEnabled } = await import('./-discover.video-stage');
    const stage = new VideoStage();
    const { result } = renderHook(() => useVideoBackdropsEnabled(stage));
    act(() => result.current[1](false));
    expect(result.current[0]).toBe(false);
    expect(window.localStorage.getItem('soulsync.discover.videoBackdrops')).toBe('off');
    act(() => result.current[1](true));
    expect(result.current[0]).toBe(true);
  });

  it('useVideoSlot plays the one banner on screen, and only once it has a video', async () => {
    const { act, renderHook } = await import('@testing-library/react');
    const { useVideoSlot, videoStage } = await import('./-discover.video-stage');
    let fire: (ratio: number) => void = () => {};
    class FakeObserver {
      constructor(cb: (entries: { intersectionRatio: number }[]) => void) {
        fire = (ratio) => cb([{ intersectionRatio: ratio }]);
      }
      observe() {}
      disconnect() {}
    }
    vi.stubGlobal('IntersectionObserver', FakeObserver);
    try {
      expect(videoStage).toBeInstanceOf(VideoStage);
      const stage = new VideoStage();
      const { result, rerender } = renderHook(({ has }) => useVideoSlot('hero', has, stage), {
        initialProps: { has: false },
      });
      act(() => result.current.ref(document.createElement('div')));
      act(() => fire(0.9));
      expect(result.current.seen).toBe(true);
      expect(result.current.playing).toBe(false); // no video yet
      rerender({ has: true });
      expect(result.current.playing).toBe(true);
      act(() => fire(0.2));
      expect(result.current.playing).toBe(false); // scrolled mostly away
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
