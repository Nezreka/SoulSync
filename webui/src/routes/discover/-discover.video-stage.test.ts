import { describe, expect, it, vi } from 'vitest';

import {
  chooseLive,
  liveBudget,
  MIN_VISIBLE,
  VideoStage,
  type SlotState,
} from './-discover.video-stage';

const slots = (entries: [string, SlotState][]) => new Map(entries);

describe('chooseLive', () => {
  it('plays the most visible banners with a video, up to the budget', () => {
    const s = slots([
      ['a', { ratio: 0.6, hasVideo: true }],
      ['b', { ratio: 1, hasVideo: true }],
      ['c', { ratio: 0.9, hasVideo: true }],
      ['d', { ratio: 1, hasVideo: false }],
    ]);
    expect(chooseLive(s, true, 2)).toEqual(['b', 'c']);
    expect(chooseLive(s, true, 6)).toEqual(['b', 'c', 'a']);
  });

  it('needs at least half a banner on screen', () => {
    expect(MIN_VISIBLE).toBe(0.5);
    expect(chooseLive(slots([['a', { ratio: 0.49, hasVideo: true }]]), true, 4)).toEqual([]);
    expect(chooseLive(slots([['a', { ratio: 0.5, hasVideo: true }]]), true, 4)).toEqual(['a']);
  });

  it('a hovered card jumps the queue, even partly off screen', () => {
    const s = slots([
      ['feature', { ratio: 1, hasVideo: true }],
      ['card', { ratio: 0.3, hasVideo: true, hover: true }],
    ]);
    expect(chooseLive(s, true, 1)).toEqual(['card']);
    // pointing at a card with no video changes nothing
    s.set('card', { ratio: 1, hasVideo: false, hover: true });
    expect(chooseLive(s, true, 1)).toEqual(['feature']);
  });

  it('the banner with sound comes first, and lets go once fully off screen', () => {
    const s = slots([
      ['loud', { ratio: 0.2, hasVideo: true }],
      ['card', { ratio: 1, hasVideo: true, hover: true }],
    ]);
    expect(chooseLive(s, true, 1, 'loud')).toEqual(['loud']);
    s.set('loud', { ratio: 0, hasVideo: true });
    expect(chooseLive(s, true, 1, 'loud')).toEqual(['card']);
  });

  it("a live banner keeps its player over one that's only a bit more visible", () => {
    const s = slots([
      ['new', { ratio: 0.8, hasVideo: true }],
      ['old', { ratio: 0.7, hasVideo: true }],
    ]);
    expect(chooseLive(s, true, 1)).toEqual(['new']);
    expect(chooseLive(s, true, 1, null, new Set(['old']))).toEqual(['old']);
    s.set('new', { ratio: 1, hasVideo: true });
    expect(chooseLive(s, true, 1, null, new Set(['old']))).toEqual(['new']);
  });

  it('plays nothing when disabled', () => {
    expect(chooseLive(slots([['a', { ratio: 1, hasVideo: true }]]), false, 6)).toEqual([]);
  });
});

describe('liveBudget', () => {
  it('scales with the machine, and drops to one on a phone or data saver', () => {
    expect(liveBudget({ hardwareConcurrency: 12, deviceMemory: 8 }, false)).toBe(6);
    expect(liveBudget({ hardwareConcurrency: 4 }, false)).toBe(4);
    expect(liveBudget({ hardwareConcurrency: 2 }, false)).toBe(2);
    expect(liveBudget({ hardwareConcurrency: 16, deviceMemory: 2 }, false)).toBe(2);
    expect(liveBudget({ hardwareConcurrency: 16 }, true)).toBe(1);
    expect(liveBudget({ hardwareConcurrency: 16, connection: { saveData: true } }, false)).toBe(1);
  });
});

describe('VideoStage', () => {
  it('hands players over as banners scroll, and tells its listeners', () => {
    const stage = new VideoStage();
    stage.budget = 2;
    const heard = vi.fn();
    stage.subscribe(heard);
    stage.set('a', { ratio: 1, hasVideo: true });
    stage.set('b', { ratio: 1, hasVideo: true });
    stage.set('c', { ratio: 1, hasVideo: true });
    expect([...stage.getLive()]).toEqual(['a', 'b']);
    stage.set('a', { ratio: 0.1 });
    expect([...stage.getLive()].sort()).toEqual(['b', 'c']);
    stage.remove('b');
    stage.remove('c');
    expect(stage.getLive().size).toBe(0);
    // a, a+b, b+c, c, nobody
    expect(heard).toHaveBeenCalledTimes(5);
  });

  it('one banner has the sound at a time, and it goes with the video', () => {
    const stage = new VideoStage();
    stage.budget = 3;
    stage.set('a', { ratio: 1, hasVideo: true });
    stage.set('b', { ratio: 1, hasVideo: true });
    stage.setSound('a');
    expect(stage.getSound()).toBe('a');
    stage.setSound('b');
    expect(stage.getSound()).toBe('b');
    stage.set('b', { ratio: 0 });
    expect(stage.getSound()).toBeNull();
  });

  it('stops everything while the tab is hidden, with reduced motion, or switched off', () => {
    const stage = new VideoStage();
    stage.set('hero', { ratio: 1, hasVideo: true });
    stage.pageVisible = false;
    stage.recompute();
    expect(stage.isLive('hero')).toBe(false);
    stage.pageVisible = true;
    stage.reducedMotion = true;
    stage.recompute();
    expect(stage.isLive('hero')).toBe(false);
    stage.reducedMotion = false;
    stage.userEnabled = false;
    stage.recompute();
    expect(stage.isLive('hero')).toBe(false);
    stage.userEnabled = true;
    stage.recompute();
    expect(stage.isLive('hero')).toBe(true);
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

  it('useVideoSlot plays a banner on screen once it has a video, and hands over the sound', async () => {
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
      act(() => result.current.setSound(true));
      expect(result.current.soundOn).toBe(true);
      // soulsync's own player starts: the video goes quiet
      const audio = document.createElement('audio');
      audio.id = 'audio-player';
      document.body.appendChild(audio);
      act(() => {
        audio.dispatchEvent(new Event('play'));
      });
      audio.remove();
      expect(result.current.soundOn).toBe(false);
      expect(result.current.playing).toBe(true);
      act(() => fire(0.2));
      expect(result.current.playing).toBe(false); // scrolled mostly away
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
