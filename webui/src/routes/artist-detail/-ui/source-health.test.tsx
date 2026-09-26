import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { matchedSummary, monogram, SourceHealth, SourceMeter, sourceState } from './source-health';

afterEach(() => cleanup());

const ENTRIES = [
  {
    service: 'spotify',
    label: 'Spotify',
    status: 'matched',
    title: 'Last: x · Click to rematch',
    url: 'https://open.spotify.com/album/1',
  },
  { service: 'musicbrainz', label: 'MB', status: 'not_found', title: 'Click to rematch' },
  { service: 'deezer', label: 'Deezer', status: 'pending', title: '' },
];

describe('helpers', () => {
  it('maps a raw status onto three states', () => {
    expect(sourceState('matched')).toBe('matched');
    expect(sourceState('not_found')).toBe('not-found');
    expect(sourceState('pending')).toBe('pending');
    expect(sourceState(undefined)).toBe('pending');
  });

  it('monograms known services and falls back to the label', () => {
    expect(monogram('musicbrainz', 'MB')).toBe('MB');
    expect(monogram('somethingnew', 'Xylo')).toBe('XY');
  });

  it('summarises the matched count', () => {
    expect(matchedSummary(ENTRIES)).toBe('1 of 3 matched');
  });
});

describe('SourceHealth', () => {
  it('renders one dot per service with its state, and the summary', () => {
    render(<SourceHealth entries={ENTRIES} onRematch={vi.fn()} />);
    const dots = [...document.querySelectorAll('.lib-source')];
    expect(dots.map((d) => d.textContent)).toEqual(['SP', 'MB', 'DZ']);
    expect(dots[0].className).toContain('matched');
    expect(dots[1].className).toContain('not-found');
    expect(dots[2].className).toContain('pending');
    expect(document.querySelector('.lib-sources-summary')?.textContent).toBe('1 of 3 matched');
  });

  it('a matched dot offers open + rematch; an unmatched one only find', () => {
    const onRematch = vi.fn();
    render(<SourceHealth entries={ENTRIES} onRematch={onRematch} />);
    fireEvent.click(document.querySelectorAll('.lib-source')[0]);
    expect([...document.querySelectorAll('.lib-menu-item')].map((n) => n.textContent)).toEqual([
      'Open on Spotify',
      'Rematch on Spotify…',
    ]);
    fireEvent.click(screen.getByText('Rematch on Spotify…'));
    expect(onRematch).toHaveBeenCalledWith('spotify');

    fireEvent.click(document.querySelectorAll('.lib-source')[1]);
    expect([...document.querySelectorAll('.lib-menu-item')].map((n) => n.textContent)).toEqual([
      'Find on MusicBrainz…',
    ]);
  });

  it('without a rematch handler an unlinked dot is inert', () => {
    render(<SourceHealth entries={ENTRIES} />);
    fireEvent.click(document.querySelectorAll('.lib-source')[1]);
    expect(document.querySelector('.lib-menu')).toBeNull();
    expect(document.querySelectorAll('.lib-source')[1].className).not.toContain('clickable');
  });

  it('renders nothing for an empty list', () => {
    const { container } = render(<SourceHealth entries={[]} />);
    expect(container.innerHTML).toBe('');
  });
});

describe('SourceMeter', () => {
  const TRACK = [
    { service: 'spotify', label: 'SP', matched: true, title: 'spotify: sp1' },
    { service: 'musicbrainz', label: 'MB', matched: false, title: 'musicbrainz: no match' },
    { service: 'deezer', label: 'Dz', matched: true, title: 'deezer: d1' },
  ];

  it('shows the fraction and fills the bar to match', () => {
    render(<SourceMeter entries={TRACK} />);
    expect(document.querySelector('.lib-meter-text')?.textContent).toBe('2/3');
    expect((document.querySelector('.lib-meter-fill') as HTMLElement).style.width).toBe('67%');
    expect(document.querySelector('.lib-meter')?.className).toContain('good');
  });

  it('lists every service behind a click and hands the pick to onMatch', () => {
    const onMatch = vi.fn();
    render(<SourceMeter entries={TRACK} onMatch={onMatch} />);
    fireEvent.click(document.querySelector('.lib-meter') as HTMLElement);
    const items = [...document.querySelectorAll('.lib-menu .enhanced-track-match-chip')];
    expect(items).toHaveLength(3);
    expect(items[1].className).toContain('not-found');
    fireEvent.click(items[1]);
    expect(onMatch).toHaveBeenCalledWith('musicbrainz');
  });

  it('is a plain status marker without onMatch', () => {
    render(<SourceMeter entries={TRACK} />);
    fireEvent.click(document.querySelector('.lib-meter') as HTMLElement);
    expect(document.querySelector('.lib-menu')).toBeNull();
  });
});
