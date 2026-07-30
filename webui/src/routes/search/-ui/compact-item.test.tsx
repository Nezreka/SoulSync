import { cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { CompactItemProps } from './compact-item';

import { CompactItem, ResultSection } from './compact-item';

function renderItem(over: Partial<CompactItemProps> = {}) {
  render(<CompactItem kind="album" name="Drukqs" meta="Aphex Twin" placeholder="💿" {...over} />);
  return document.querySelector('.enh-compact-item') as HTMLElement;
}

afterEach(() => {
  cleanup();
  delete window.extractImageColors;
  delete window.applyDynamicGlow;
});

describe('CompactItem', () => {
  it('wears the compound class its kind needs, never the bare one', () => {
    // `.album-card` and `.artist-card` alone belong to the discography grids on
    // other pages; only the compound `.enh-compact-item.album-card` selector is
    // styled for search. A bare class here inherits a 300px stacked card.
    expect(renderItem({ kind: 'album' }).className).toBe('enh-compact-item album-card');
    cleanup();
    expect(renderItem({ kind: 'track' }).className).toBe('enh-compact-item track-item');
    cleanup();
    expect(renderItem({ kind: 'artist' }).className).toBe('enh-compact-item artist-card');
    cleanup();
    expect(renderItem({ kind: 'label' }).className).toBe('enh-compact-item label-card artist-card');
  });

  it('uses its kind for the image and placeholder classes too', () => {
    renderItem({ kind: 'track', image: 'https://cdn/a.jpg' });
    expect(document.querySelector('img')?.className).toBe('enh-item-image track-cover');
    cleanup();
    renderItem({ kind: 'track' });
    expect(document.querySelector('div[data-lazy-image]')?.className).toBe(
      'enh-item-image-placeholder track-placeholder',
    );
  });

  it('is keyboard-operable when it is clickable', () => {
    const onClick = vi.fn();
    const card = renderItem({ onClick });
    expect(card.getAttribute('role')).toBe('button');
    expect(card.getAttribute('tabindex')).toBe('0');

    fireEvent.keyDown(card, { key: 'Enter' });
    fireEvent.keyDown(card, { key: ' ' });
    fireEvent.keyDown(card, { key: 'x' });
    expect(onClick).toHaveBeenCalledTimes(2);
  });

  it('is not announced as a button when it does nothing', () => {
    const card = renderItem();
    expect(card.getAttribute('role')).toBeNull();
    expect(card.getAttribute('tabindex')).toBeNull();
  });

  it('renders artists and labels as anchors, but not albums or tracks', () => {
    // A middle-clickable link is the whole reason these two are anchors; an
    // album opens a modal, so it stays a div.
    renderItem({ kind: 'artist', href: '/artist-detail/spotify/1' });
    expect(document.querySelector('a')?.getAttribute('href')).toBe('/artist-detail/spotify/1');
    cleanup();
    renderItem({ kind: 'album', href: '/nope' });
    expect(document.querySelector('a')).toBeNull();
  });

  it('shows a duration only on a track', () => {
    renderItem({ kind: 'track', duration: '3:35' });
    expect(document.querySelector('.enh-item-duration')?.textContent).toContain('3:35');
    cleanup();
    // An album's total runtime would land in the track row's slot; the vanilla
    // never rendered one there.
    renderItem({ kind: 'album', duration: '3:35' });
    expect(document.querySelector('.enh-item-duration')).toBeNull();
  });

  it('marks a resolved artist image as not needing one', () => {
    renderItem({
      kind: 'artist',
      artistId: 'sp1',
      artistName: 'Aphex',
      image: 'https://cdn/a.jpg',
    });
    const card = document.querySelector('[data-artist-id="sp1"]') as HTMLElement;
    expect(card.getAttribute('data-needs-image')).toBe('false');
    expect(card.getAttribute('data-artist-name')).toBe('Aphex');
  });

  it('carries no artist data attributes when there is no artist id', () => {
    // The lazy loader queries `[data-needs-image="true"]` app-wide; an album card
    // answering that query would be asked to resolve an artist photo.
    expect(renderItem().hasAttribute('data-needs-image')).toBe(false);
  });

  it('glows from its artwork — on an ALBUM, not just an artist', () => {
    // renderCompactSection ran this for every card with an image
    // (shared-helpers.js:748-753), and the stylesheet turns the sampled palette
    // into the hover border and shadow of album, track and artist cards alike.
    const applyDynamicGlow = vi.fn();
    window.extractImageColors = vi.fn((_url: string, cb: (colors: unknown) => void) => {
      cb(['#111', '#222']);
    }) as never;
    window.applyDynamicGlow = applyDynamicGlow as never;

    const card = renderItem({ kind: 'album', image: 'https://cdn/cover.jpg' });

    expect(window.extractImageColors).toHaveBeenCalledWith(
      'https://cdn/cover.jpg',
      expect.any(Function),
    );
    expect(applyDynamicGlow.mock.calls[0][0]).toBe(card);
  });

  it('does not glow a card that has no artwork', () => {
    window.extractImageColors = vi.fn() as never;
    renderItem({ kind: 'album' });
    expect(window.extractImageColors).not.toHaveBeenCalled();
  });

  it('stops glowing when the image turns out to be broken', () => {
    // A 404 falls back to the placeholder; sampling the dead url would paint the
    // card from nothing.
    window.extractImageColors = vi.fn() as never;
    renderItem({ kind: 'album', image: 'https://cdn/missing.jpg' });
    (window.extractImageColors as unknown as { mockClear: () => void }).mockClear();

    fireEvent.error(document.querySelector('img') as HTMLImageElement);
    expect(window.extractImageColors).not.toHaveBeenCalled();
  });

  it('still renders when sampling the palette throws', () => {
    // Reading pixels off a CORS-opaque image throws; the card must survive it.
    window.extractImageColors = vi.fn(() => {
      throw new Error('canvas is tainted');
    }) as never;
    expect(renderItem({ kind: 'album', image: 'https://cdn/cover.jpg' })).not.toBeNull();
  });

  it('stacks extra badges alongside the source badge', () => {
    renderItem({
      badge: { text: 'Spotify', className: 'enh-badge-spotify' },
      extraBadges: [
        { text: 'In Library', className: 'enh-item-lib-badge' },
        { text: 'Wishlisted', className: 'enh-item-wishlist-badge' },
      ],
    });
    expect(document.querySelector('.enh-item-badge.enh-badge-spotify')).not.toBeNull();
    expect(document.querySelector('.enh-item-lib-badge')).not.toBeNull();
    expect(document.querySelector('.enh-item-wishlist-badge')).not.toBeNull();
  });
});

describe('ResultSection', () => {
  it('renders nothing at all when the count is zero', () => {
    render(
      <ResultSection id="s" listId="l" countId="c" icon="💿" title="Albums" kind="album" count={0}>
        <div>child</div>
      </ResultSection>,
    );
    // Not merely hidden — absent, children included.
    expect(document.getElementById('s')).toBeNull();
    expect(document.body.textContent).toBe('');
  });

  it('gives each kind its own grid class', () => {
    const section = (kind: 'album' | 'track' | 'artist') =>
      render(
        <ResultSection id="s" listId="l" countId="c" icon="x" title="T" kind={kind} count={1}>
          <div>child</div>
        </ResultSection>,
      );

    section('album');
    expect(document.getElementById('l')?.className).toBe('enh-compact-list enh-albums-grid');
    cleanup();
    section('track');
    expect(document.getElementById('l')?.className).toBe('enh-compact-list enh-tracks-list');
    cleanup();
    section('artist');
    expect(document.getElementById('l')?.className).toBe('enh-compact-list enh-artists-grid');
  });

  it('titles itself with a heading, as the vanilla did', () => {
    // The stylesheet keys on the class alone, so this is purely the document
    // outline; a page of six unheaded lists is worse to navigate by screen
    // reader for no styling gain.
    render(
      <ResultSection id="s" listId="l" countId="c" icon="💿" title="Albums" kind="album" count={1}>
        <div>child</div>
      </ResultSection>,
    );
    expect(document.querySelector('.enh-section-title')?.tagName).toBe('H4');
  });

  it('shows the count the header advertises', () => {
    render(
      <ResultSection id="s" listId="l" countId="c" icon="💿" title="Albums" kind="album" count={7}>
        <div>child</div>
      </ResultSection>,
    );
    expect(document.getElementById('c')?.textContent).toBe('7');
    expect(document.querySelector('.enh-section-title')?.textContent).toBe('Albums');
  });
});
