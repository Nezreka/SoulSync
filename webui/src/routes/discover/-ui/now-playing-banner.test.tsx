import { fireEvent, render, renderHook, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { cleanTitle, NowPlayingBanner, useNowPlaying } from './now-playing-banner';

afterEach(() => {
  delete window.getCurrentTrack;
  document.body.innerHTML = '';
});

const TRACK = { title: 'id1||Nightcall', artist: 'Kavinsky', artist_id: 42, image_url: '/a.jpg' };

describe('cleanTitle', () => {
  it("drops the player's id prefix", () => {
    expect(cleanTitle('id1||Nightcall')).toBe('Nightcall');
    expect(cleanTitle('Plain')).toBe('Plain');
    expect(cleanTitle(undefined)).toBe('');
  });
});

describe('NowPlayingBanner', () => {
  it('is not there when nothing is loaded', () => {
    const { container } = render(
      <NowPlayingBanner state={{ track: null, playing: false }} onMoreLikeThis={vi.fn()} />,
    );
    expect(container.innerHTML).toBe('');
  });

  it('shows the track, says playing or paused, and offers more like it', () => {
    const more = vi.fn();
    const { rerender, container } = render(
      <NowPlayingBanner
        state={{ track: TRACK, playing: true }}
        onMoreLikeThis={more}
        artistHref="/a/42"
      />,
    );
    expect(screen.getByText('Nightcall')).toBeInTheDocument();
    expect(screen.getByText('Now playing')).toBeInTheDocument();
    expect(screen.getByText('Kavinsky').closest('a')).toHaveAttribute('href', '/a/42');
    fireEvent.click(screen.getByText('More like this'));
    expect(more).toHaveBeenCalledWith(TRACK);
    rerender(<NowPlayingBanner state={{ track: TRACK, playing: false }} onMoreLikeThis={more} />);
    expect(screen.getByText('Paused')).toBeInTheDocument();
    expect(container.querySelector('.dsc-now')).not.toHaveClass('playing');
  });

  it('has no radio button for a track without a library artist', () => {
    render(
      <NowPlayingBanner
        state={{ track: { title: 'x', artist: 'y' }, playing: true }}
        onMoreLikeThis={vi.fn()}
      />,
    );
    expect(screen.queryByText('More like this')).toBeNull();
  });
});

describe('useNowPlaying', () => {
  it("reads the player's track and whether the audio is playing", () => {
    window.getCurrentTrack = () => TRACK;
    const audio = document.createElement('audio');
    audio.id = 'audio-player';
    document.body.appendChild(audio);
    const { result } = renderHook(() => useNowPlaying(60_000));
    expect(result.current.track).toBe(TRACK);
    expect(result.current.playing).toBe(false); // a fresh audio element is paused
  });
});
