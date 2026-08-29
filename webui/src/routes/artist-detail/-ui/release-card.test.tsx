import { cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { DiscographyRelease } from '../-artist-detail.types';

import { ReleaseCard } from './release-card';

function renderCard(
  release: DiscographyRelease,
  opts: Partial<{ mb: boolean; source: boolean }> = {},
) {
  const onOpen = vi.fn();
  const onPlay = vi.fn();
  render(
    <ReleaseCard
      release={release}
      isMusicBrainz={opts.mb ?? false}
      isSourceArtist={opts.source ?? false}
      onOpen={onOpen}
      onPlay={onPlay}
    />,
  );
  return { onOpen, onPlay, card: document.querySelector('.release-card') as HTMLElement };
}

afterEach(() => {
  // NOT document.body.innerHTML = '': anything rendered through BodyPortal
  // lives there, and wiping the body out from under Testing Library's cleanup
  // makes it throw "The node to be removed is not a child of this node".
  cleanup();
  vi.unstubAllGlobals();
});

describe('ReleaseCard markup', () => {
  it('keeps both class names and the vanilla data attributes', () => {
    const { card } = renderCard({ id: 7, title: 'Kid A', album_type: 'album', owned: true });
    expect(card.className).toBe('release-card album-card');
    expect(card.dataset.releaseId).toBe('7');
    expect(card.dataset.albumId).toBe('7');
    expect(card.dataset.albumName).toBe('Kid A');
    expect(card.dataset.albumType).toBe('album');
  });

  it('defaults album_type to album, matching the vanilla', () => {
    expect(renderCard({ id: 1, title: 'X' }).card.dataset.albumType).toBe('album');
  });

  it('renders the data-is-* flags the CSS and tour select on', () => {
    const { card } = renderCard({ id: 1, title: 'Live at Leeds' });
    expect(card.dataset.isLive).toBe('true');
    expect(card.dataset.isCompilation).toBe('false');
    expect(card.dataset.isFeatured).toBe('false');
  });

  it('recomputes live-ness from secondary_types on MusicBrainz', () => {
    const { card } = renderCard(
      { id: 1, title: 'Live Through This', secondary_types: [] },
      { mb: true },
    );
    expect(card.dataset.isLive).toBe('false');
  });

  it('lazy-loads artwork via data-bg-src, not an inline background', () => {
    const { card } = renderCard({ id: 1, title: 'X', image_url: 'a.jpg' });
    const image = card.querySelector('.album-card-image') as HTMLElement;
    expect(image.dataset.bgSrc).toBe('a.jpg');
    expect(image.style.backgroundImage).toBe('');
  });

  it('omits data-bg-src entirely for a blank url', () => {
    const { card } = renderCard({ id: 1, title: 'X', image_url: '   ' });
    expect((card.querySelector('.album-card-image') as HTMLElement).dataset.bgSrc).toBeUndefined();
  });

  it('shows the completion overlay, and drops it for a source artist', () => {
    expect(
      renderCard({ id: 1, title: 'X', owned: false }).card.querySelector(
        '.completion-overlay .completion-status',
      )?.textContent,
    ).toBe('Missing');
    document.body.innerHTML = '';
    expect(
      renderCard({ id: 1, title: 'X', owned: false }, { source: true }).card.querySelector(
        '.completion-overlay',
      ),
    ).toBeNull();
  });

  it('renders the explicit badge only for exactly true', () => {
    expect(
      renderCard({ id: 1, title: 'X', explicit: true }).card.querySelector('.explicit-badge'),
    ).not.toBeNull();
    document.body.innerHTML = '';
    expect(renderCard({ id: 1, title: 'X' }).card.querySelector('.explicit-badge')).toBeNull();
    document.body.innerHTML = '';
    // Truthy is NOT enough — only `=== true`. Without this case a Boolean()
    // check would pass every assertion above.
    expect(
      renderCard({ id: 1, title: 'X', explicit: 1 as never }).card.querySelector('.explicit-badge'),
    ).toBeNull();
  });

  it('omits the year element when there is no usable year', () => {
    expect(renderCard({ id: 1, title: 'X' }).card.querySelector('.album-card-year')).toBeNull();
    document.body.innerHTML = '';
    expect(
      renderCard({ id: 1, title: 'X', release_date: '1994-01-01' }).card.querySelector(
        '.album-card-year',
      )?.textContent,
    ).toBe('1994');
  });
});

describe('ReleaseCard interaction', () => {
  it('opens the release on click', () => {
    const { onOpen, card } = renderCard({ id: 1, title: 'Kid A', owned: true });
    fireEvent.click(card);
    expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ title: 'Kid A' }));
  });

  it('opens MusicBrainz WITHOUT also opening the release', () => {
    // The icon sits inside the card, so without stopPropagation both fire.
    const open = vi.fn();
    vi.stubGlobal('open', open);
    const { onOpen, card } = renderCard({ id: 1, title: 'X', musicbrainz_release_id: 'mbid' });
    fireEvent.click(card.querySelector('.mb-card-icon')!);
    expect(open).toHaveBeenCalledWith('https://musicbrainz.org/release/mbid', '_blank');
    expect(onOpen).not.toHaveBeenCalled();
  });

  it('plays the album without also opening its modal', () => {
    const { onOpen, onPlay, card } = renderCard({ id: 1, title: 'Kid A', owned: true });
    fireEvent.click(card.querySelector('.release-card-play-btn')!);
    expect(onPlay).toHaveBeenCalledWith(expect.objectContaining({ title: 'Kid A' }));
    expect(onOpen).not.toHaveBeenCalled();
  });

  it('has no MusicBrainz icon without a release id', () => {
    expect(renderCard({ id: 1, title: 'X' }).card.querySelector('.mb-card-icon')).toBeNull();
  });
});

describe('gap-fill cards (#1067)', () => {
  const gap = { id: 'g1', title: 'Gap Album', owned: false, _gap_source: 'itunes' };

  it('names the source it will open from', () => {
    renderCard(gap);
    const badge = document.querySelector('.gapfill-source-badge') as HTMLElement;
    expect(badge.textContent).toBe('Apple Music');
    expect(badge.title).toBe('Only listed on Apple Music — opens and downloads from there');
  });

  it('carries the gapfill-card class alongside the normal state classes', () => {
    renderCard(gap);
    const card = document.querySelector('.release-card') as HTMLElement;
    expect(card.className).toContain('gapfill-card');
    // Still a missing card in every other respect.
    expect(card.className).toContain('missing');
  });

  it('leaves ordinary cards unbadged', () => {
    renderCard({ id: 1, title: 'Normal', owned: true });
    expect(document.querySelector('.gapfill-source-badge')).toBeNull();
    expect(document.querySelector('.release-card')?.className).not.toContain('gapfill-card');
  });
});
