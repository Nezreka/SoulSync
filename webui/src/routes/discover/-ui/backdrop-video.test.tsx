import { act, render, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { YouTubePlayerOptions } from '../../artist-detail/-artist-detail.youtube-api';

/** a stand-in for youtube's player: records how it was built, fires what we tell it. */
const players: { options: YouTubePlayerOptions; destroyed: boolean; muted: boolean }[] = [];
let apiAvailable = true;

class FakePlayer {
  record: (typeof players)[number];
  constructor(_el: HTMLElement, options: YouTubePlayerOptions) {
    this.record = { options, destroyed: false, muted: false };
    players.push(this.record);
  }
  mute() {
    this.record.muted = true;
  }
  playVideo() {}
  pauseVideo() {}
  loadVideoById() {}
  destroy() {
    this.record.destroyed = true;
  }
}

vi.mock('../../artist-detail/-artist-detail.youtube-api', async (importOriginal) => {
  const actual =
    await importOriginal<typeof import('../../artist-detail/-artist-detail.youtube-api')>();
  return {
    ...actual,
    loadYouTubeIframeApi: () =>
      Promise.resolve(
        apiAvailable
          ? { Player: FakePlayer, PlayerState: { ENDED: 0, PLAYING: 1, PAUSED: 2 } }
          : null,
      ),
  };
});

const { BackdropVideo } = await import('./backdrop-video');

afterEach(() => {
  players.length = 0;
  apiAvailable = true;
});

function fire(event: 'onReady' | 'onStateChange' | 'onError', data = 0) {
  const p = players[players.length - 1];
  act(() =>
    p.options.events?.[event]?.({
      data,
      target: new FakePlayer(document.body, p.options) as never,
    }),
  );
}

describe('BackdropVideo', () => {
  it('builds no player unless its banner holds the stage', async () => {
    const { rerender } = render(<BackdropVideo videoId="v1" playing={false} />);
    rerender(<BackdropVideo videoId={null} playing />);
    await new Promise((r) => setTimeout(r, 0));
    expect(players).toHaveLength(0);
  });

  it('asks youtube for a muted, looping, cookie-less, chrome-less player', async () => {
    render(<BackdropVideo videoId="v1" playing />);
    await waitFor(() => expect(players).toHaveLength(1));
    const { videoId, host, playerVars } = players[0].options;
    expect(videoId).toBe('v1');
    expect(host).toBe('https://www.youtube-nocookie.com');
    expect(playerVars).toMatchObject({
      mute: 1,
      autoplay: 1,
      loop: 1,
      playlist: 'v1',
      controls: 0,
    });
  });

  it('stays invisible until youtube says it is actually playing', async () => {
    const { container } = render(<BackdropVideo videoId="v1" playing />);
    await waitFor(() => expect(players).toHaveLength(1));
    const wrap = container.querySelector('.dsc-backdrop-video')!;
    fire('onReady');
    expect(wrap).not.toHaveClass('ready');
    fire('onStateChange', 3); // buffering
    expect(wrap).not.toHaveClass('ready');
    fire('onStateChange', 1); // playing
    expect(wrap).toHaveClass('ready');
  });

  it('a refused embed hands the banner back to its artwork', async () => {
    const onUnplayable = vi.fn();
    const { container } = render(
      <BackdropVideo videoId="v1" playing onUnplayable={onUnplayable} />,
    );
    await waitFor(() => expect(players).toHaveLength(1));
    fire('onError', 150);
    expect(onUnplayable).toHaveBeenCalledWith('v1');
    expect(container.querySelector('.dsc-backdrop-video')).not.toHaveClass('ready');
  });

  it('tears the player down when the banner loses the stage', async () => {
    const { rerender } = render(<BackdropVideo videoId="v1" playing />);
    await waitFor(() => expect(players).toHaveLength(1));
    rerender(<BackdropVideo videoId="v1" playing={false} />);
    expect(players[0].destroyed).toBe(true);
  });

  it('counts as unplayable when youtube itself cannot load', async () => {
    apiAvailable = false;
    const onUnplayable = vi.fn();
    render(<BackdropVideo videoId="v1" playing onUnplayable={onUnplayable} />);
    await waitFor(() => expect(onUnplayable).toHaveBeenCalledWith('v1'));
  });
});
