import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { PromoBanner, tiltFrom } from './promo-banner';

function props(over: Partial<Parameters<typeof PromoBanner>[0]> = {}) {
  return {
    kind: 'artist',
    eyebrow: 'An artist you should know',
    title: 'Kavinsky',
    subtitle: 'Because you have Daft Punk',
    art: '/k.jpg',
    actions: <button type="button">View artist</button>,
    videoId: null,
    playing: false,
    ...over,
  };
}

describe('PromoBanner', () => {
  it('lays out by size: a portrait card is all art, no separate cover', () => {
    const { container, rerender } = render(<PromoBanner {...props({ size: 'feature' })} />);
    expect(container.querySelector('.dsc-promo')).toHaveClass('dsc-promo--feature');
    expect(container.querySelector('.dsc-promo-cover')).not.toBeNull();
    rerender(<PromoBanner {...props({ size: 'portrait' })} />);
    expect(container.querySelector('.dsc-promo')).toHaveClass('dsc-promo--portrait');
    expect(container.querySelector('.dsc-promo-cover')).toBeNull();
  });

  it('tells the stage when the pointer is on it', () => {
    const onHoverChange = vi.fn();
    const { container } = render(<PromoBanner {...props({ onHoverChange })} />);
    const banner = container.querySelector('.dsc-promo')!;
    fireEvent.mouseEnter(banner);
    fireEvent.mouseLeave(banner);
    expect(onHoverChange.mock.calls).toEqual([[true], [false]]);
  });

  it('marks itself live only while its video holds the stage', () => {
    const { container, rerender } = render(
      <PromoBanner {...props({ videoId: 'v1', playing: false })} />,
    );
    expect(container.querySelector('.dsc-promo')).not.toHaveClass('is-live');
    rerender(<PromoBanner {...props({ videoId: 'v1', playing: true })} />);
    expect(container.querySelector('.dsc-promo')).toHaveClass('is-live');
  });

  it('says what it advertises, with its cover and its action', () => {
    const { container } = render(<PromoBanner {...props({ round: true })} />);
    expect(
      screen.getByRole('region', { name: 'An artist you should know: Kavinsky' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('heading').textContent).toBe('Kavinsky');
    expect(screen.getByText('Because you have Daft Punk')).toBeInTheDocument();
    expect(screen.getByText('View artist')).toBeInTheDocument();
    expect(container.querySelector('.dsc-promo-cover')).toHaveClass('round');
    expect(container.querySelector('.dsc-promo')).toHaveClass('dsc-promo--artist');
  });

  it('is always moving: the artwork drifts even with no video', () => {
    const { container } = render(<PromoBanner {...props()} />);
    const drift = container.querySelector('.dsc-promo-drift') as HTMLElement;
    expect(drift.style.backgroundImage).toContain('/k.jpg');
    expect(container.querySelector('.dsc-backdrop-video')).toBeNull();
  });

  it('leaves the cover out rather than show an empty square with no art', () => {
    const { container } = render(<PromoBanner {...props({ art: null })} />);
    expect(container.querySelector('.dsc-promo-cover')).toBeNull();
    expect(container.querySelector('.dsc-promo-drift')).toBeNull();
  });

  it('glows the colour of its artwork', () => {
    const { container } = render(<PromoBanner {...props({ glowRgb: '10, 20, 30' })} />);
    expect(
      (container.querySelector('.dsc-promo') as HTMLElement).style.getPropertyValue('--promo-rgb'),
    ).toBe('10, 20, 30');
  });

  it('offers sound only while its video is live, and pauses soulsync when you take it', () => {
    const audio = document.createElement('audio');
    audio.id = 'audio-player';
    document.body.appendChild(audio);
    Object.defineProperty(audio, 'paused', { value: false, configurable: true });
    const pause = vi.spyOn(audio, 'pause').mockImplementation(() => {});
    const onSoundChange = vi.fn();
    const { container, rerender } = render(
      <PromoBanner {...props({ videoId: 'v1', playing: false, onSoundChange })} />,
    );
    expect(screen.queryByLabelText('Play this video with sound')).toBeNull();
    rerender(<PromoBanner {...props({ videoId: 'v1', playing: true, onSoundChange })} />);
    fireEvent.click(screen.getByLabelText('Play this video with sound'));
    expect(pause).toHaveBeenCalledTimes(1);
    expect(onSoundChange).toHaveBeenLastCalledWith(true);
    rerender(
      <PromoBanner {...props({ videoId: 'v1', playing: true, soundOn: true, onSoundChange })} />,
    );
    expect(container.querySelector('.dsc-promo')).toHaveClass('has-sound');
    fireEvent.click(screen.getByLabelText('Mute this video'));
    expect(onSoundChange).toHaveBeenLastCalledWith(false);
    expect(pause).toHaveBeenCalledTimes(1);
    audio.remove();
  });

  it('switches video backgrounds and says which way', () => {
    const onToggle = vi.fn();
    render(<PromoBanner {...props({ videoToggle: { on: true, onToggle } })} />);
    fireEvent.click(screen.getByLabelText('Turn video backgrounds off'));
    expect(onToggle).toHaveBeenCalledTimes(1);
  });
});

describe('tiltFrom', () => {
  const rect = { left: 100, top: 50, width: 200, height: 100 };
  it('is -1..1 from the centre, clamped outside the box', () => {
    expect(tiltFrom(200, 100, rect)).toEqual({ px: 0, py: 0 });
    expect(tiltFrom(100, 50, rect)).toEqual({ px: -1, py: -1 });
    expect(tiltFrom(250, 125, rect)).toEqual({ px: 0.5, py: 0.5 });
    expect(tiltFrom(900, -40, rect)).toEqual({ px: 1, py: -1 });
  });
  it('a box with no size stays flat', () => {
    expect(tiltFrom(5, 5, { left: 0, top: 0, width: 0, height: 0 })).toEqual({ px: 0, py: 0 });
  });
});
