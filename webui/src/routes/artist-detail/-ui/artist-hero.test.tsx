import { fireEvent, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { StreamCounts } from '../-artist-detail.completion';
import type { ArtistInfo, Discography } from '../-artist-detail.types';

import { emptyStreamCounts } from '../-artist-detail.completion';
import { ArtistHero } from './artist-hero';

function renderHero(
  artist: ArtistInfo,
  discography: Discography = {},
  isSourceArtist = false,
  stream: {
    counts?: StreamCounts | null;
    completed?: boolean;
    enrichment?: Record<string, unknown>;
  } = {},
) {
  return render(
    <ArtistHero
      artist={artist}
      discography={discography}
      isSourceArtist={isSourceArtist}
      streamCounts={stream.counts}
      streamCompleted={stream.completed}
      enrichment={stream.enrichment}
    />,
  );
}

function countsWith(
  owned: Partial<Record<'albums' | 'eps' | 'singles', number>>,
  total: Partial<Record<'albums' | 'eps' | 'singles', number>>,
): StreamCounts {
  const counts = emptyStreamCounts();
  Object.assign(counts.owned, owned);
  Object.assign(counts.total, total);
  return counts;
}

const img = () => document.getElementById('artist-detail-image') as HTMLImageElement;
const fallback = () => document.getElementById('artist-detail-image-fallback') as HTMLElement;

afterEach(() => {
  document.body.innerHTML = '';
  delete window.playArtistRadio;
  delete window.openArtistArtPicker;
  delete window.openDiscographyModal;
});

describe('ArtistHero markup', () => {
  it('renders the ids the guided tour anchors to', () => {
    renderHero({ name: 'Aphex Twin' });
    for (const id of [
      'artist-hero-section',
      'artist-detail-name',
      'artist-hero-badges',
      'artist-genres',
    ]) {
      expect(document.getElementById(id)).not.toBeNull();
    }
  });

  it('blurs the primary image behind the hero', () => {
    renderHero({ name: 'A', image_url: 'a.jpg' });
    const bg = document.getElementById('artist-detail-hero-bg') as HTMLElement;
    // jsdom re-serialises the quote style, so match on the url itself.
    expect(bg.style.backgroundImage).toMatch(/^url\(["']?a\.jpg["']?\)$/);
  });

  it('falls back to release art for the background when the artist has no photo', () => {
    renderHero({ name: 'A' }, { albums: [{ image_url: 'rel.jpg' }] });
    const bg = document.getElementById('artist-detail-hero-bg') as HTMLElement;
    expect(bg.style.backgroundImage).toMatch(/^url\(["']?rel\.jpg["']?\)$/);
  });
});

describe('artist photo fallback chain', () => {
  it('steps photo -> Deezer -> release -> icon', () => {
    renderHero(
      { name: 'A', image_url: 'a.jpg', deezer_id: 7 },
      { albums: [{ image_url: 'rel.jpg' }] },
    );
    expect(img().getAttribute('src')).toBe('a.jpg');

    fireEvent.error(img());
    expect(img().getAttribute('src')).toBe('https://api.deezer.com/artist/7/image?size=big');

    fireEvent.error(img());
    expect(img().getAttribute('src')).toBe('rel.jpg');

    fireEvent.error(img());
    expect(img().style.display).toBe('none');
    expect(fallback().style.display).toBe('flex');
  });

  it('shows the icon immediately when there is no image at all', () => {
    renderHero({ name: 'A' });
    expect(img().style.display).toBe('none');
    expect(fallback().style.display).toBe('flex');
  });

  it('resets the stage when the artist changes', () => {
    // Navigating from an artist whose image failed to one with a good image
    // must not start at the fallback.
    const { rerender } = renderHero({ name: 'A', image_url: 'bad.jpg' });
    fireEvent.error(img());
    expect(fallback().style.display).toBe('flex');

    rerender(
      <ArtistHero
        artist={{ name: 'B', image_url: 'good.jpg' }}
        discography={{}}
        isSourceArtist={false}
      />,
    );
    expect(img().getAttribute('src')).toBe('good.jpg');
    expect(img().style.display).toBe('block');
  });
});

describe('badges', () => {
  it('links a provider with a url and does not link one without', () => {
    renderHero({ name: 'A', spotify_artist_id: 'sp', amazon_id: 'az' });
    const badges = document.querySelectorAll('.artist-hero-badge');
    expect(badges[0].tagName).toBe('A');
    expect(badges[0].getAttribute('target')).toBe('_blank');
    expect(badges[1].tagName).toBe('DIV');
  });

  it('shows the text fallback when a logo fails to load', () => {
    renderHero({ name: 'A', spotify_artist_id: 'sp' });
    fireEvent.error(document.querySelector('.artist-hero-badge img')!);
    expect(document.querySelector('.artist-hero-badge')?.textContent).toBe('SP');
  });
});

describe('genres and bio', () => {
  it('dims Last.fm tags but not real genres', () => {
    renderHero({ name: 'A', genres: ['IDM'], lastfm_tags: ['electronic'] });
    const tags = document.querySelectorAll('.genre-tag');
    expect((tags[0] as HTMLElement).style.opacity).toBe('');
    expect((tags[1] as HTMLElement).style.opacity).toBe('0.6');
  });

  it('toggles the bio between Read more and Show less', () => {
    renderHero({ name: 'A', lastfm_bio: 'A band. <a href="x">Read more on Last.fm</a>' });
    const toggle = document.querySelector('.artist-hero-bio-toggle')!;
    expect(document.querySelector('.bio-text')?.textContent).toBe('A band.');
    expect(toggle.textContent).toBe('Read more');
    fireEvent.click(toggle);
    expect(document.querySelector('.artist-hero-bio')?.className).toContain('expanded');
    expect(document.querySelector('.artist-hero-bio-toggle')?.textContent).toBe('Show less');
  });

  it('omits the bio block entirely when only a link remains', () => {
    renderHero({ name: 'A', lastfm_bio: '<a href="x">Read more on Last.fm</a>' });
    expect(document.getElementById('artist-hero-bio')).toBeNull();
  });
});

describe('stats and actions', () => {
  it('hides a Last.fm stat that is absent rather than showing 0', () => {
    renderHero({ name: 'A', lastfm_listeners: 1200000 });
    expect(document.getElementById('artist-hero-listeners')?.textContent).toContain('1.2M');
    expect(document.getElementById('artist-hero-playcount')).toBeNull();

    // BOTH branches need an absent case: asserting only on the missing
    // playcount cannot detect the listeners branch always rendering.
    document.body.innerHTML = '';
    renderHero({ name: 'A', lastfm_playcount: 3400 });
    expect(document.getElementById('artist-hero-listeners')).toBeNull();
    expect(document.getElementById('artist-hero-playcount')?.textContent).toContain('3.4K');

    document.body.innerHTML = '';
    renderHero({ name: 'A' });
    expect(document.querySelectorAll('.artist-hero-stat')).toHaveLength(0);
  });

  it('hides the completion bars for a source artist', () => {
    // They own nothing, so every bar would read 0/0.
    renderHero({ name: 'A' }, { albums: [{ owned: false }] }, true);
    expect(document.querySelector('.collection-overview')).toBeNull();
  });

  it('shows a bar per category for a library artist', () => {
    renderHero({ name: 'A' }, { albums: [{ owned: true }, { owned: false }] });
    expect(document.getElementById('albums-stats')?.textContent).toBe('1/2');
    expect((document.getElementById('albums-completion-fill') as HTMLElement).style.width).toBe(
      '50%',
    );
  });

  it('marks the bar as checking while ownership is pending', () => {
    renderHero({ name: 'A' }, { albums: [{ owned: null }] });
    const fill = document.getElementById('albums-completion-fill') as HTMLElement;
    expect(fill.className).toContain('checking');
    expect(fill.style.width).toBe('100%');
  });

  it('hides Download Discography when there is nothing to download', () => {
    renderHero({ name: 'A' }, {});
    expect((document.getElementById('discog-download-wrap') as HTMLElement).style.display).toBe(
      'none',
    );
    document.body.innerHTML = '';
    renderHero({ name: 'A' }, { albums: [{ id: 1 }] });
    expect((document.getElementById('discog-download-wrap') as HTMLElement).style.display).toBe('');
  });

  it('invokes the vanilla globals rather than reimplementing them', () => {
    const radio = vi.fn();
    const picker = vi.fn();
    const discog = vi.fn();
    window.playArtistRadio = radio;
    window.openArtistArtPicker = picker;
    window.openDiscographyModal = discog;
    renderHero({ name: 'A' }, { albums: [{ id: 1 }] });

    fireEvent.click(document.getElementById('library-artist-radio-btn')!);
    expect(radio).toHaveBeenCalled();
    fireEvent.click(document.querySelector('.artist-image-container')!);
    expect(picker).toHaveBeenCalled();
    fireEvent.click(document.getElementById('discog-download-btn')!);
    expect(discog).toHaveBeenCalled();
  });

  it('does not throw when those globals are absent', () => {
    expect(() => {
      renderHero({ name: 'A' }, { albums: [{ id: 1 }] });
      fireEvent.click(document.getElementById('library-artist-radio-btn')!);
    }).not.toThrow();
  });
});

const barText = (bucket: string) => document.getElementById(`${bucket}-stats`)?.textContent;
const barFill = (bucket: string) =>
  document.getElementById(`${bucket}-completion-fill`) as HTMLElement;

describe('completion bars across the three stream states', () => {
  const DISC: Discography = {
    albums: [
      { id: 1, owned: null },
      { id: 2, owned: null },
      { id: 3, owned: null },
    ],
  };

  it('shows the pending state before the stream reports anything', () => {
    renderHero({ name: 'A' }, DISC);
    expect(barText('albums')).toBe('...');
    expect(barFill('albums').className).toContain('checking');
  });

  it('shows resolved-so-far while the stream runs, NOT the whole bucket', () => {
    // One of three albums checked, and it was owned: "1/1", not "1/3".
    renderHero({ name: 'A' }, DISC, false, { counts: countsWith({ albums: 1 }, { albums: 1 }) });
    expect(barText('albums')).toBe('1/1');
    expect(barFill('albums').className).not.toContain('checking');
    expect(barFill('albums').style.width).toBe('100%');
  });

  it('recounts from the merged discography once the stream completes', () => {
    const merged: Discography = {
      albums: [
        { id: 1, owned: true },
        { id: 2, owned: false },
        { id: 3, owned: null },
      ],
    };
    // The running tallies said 1/1; the recount sees the third album never
    // resolved and reports 1/2 rather than falling back to "...".
    renderHero({ name: 'A' }, merged, false, {
      counts: countsWith({ albums: 1 }, { albums: 1 }),
      completed: true,
    });
    expect(barText('albums')).toBe('1/2');
    expect(barFill('albums').style.width).toBe('50%');
  });
});

describe('artist format tags', () => {
  const counts = () => {
    const c = emptyStreamCounts();
    c.formats.add('MP3');
    c.formats.add('FLAC');
    return c;
  };

  it('renders sorted format tags after the stream completes', () => {
    renderHero({ name: 'A' }, {}, false, { counts: counts(), completed: true });
    const tags = [...document.querySelectorAll('.artist-formats .artist-format-tag')];
    expect(tags.map((n) => n.textContent)).toEqual(['FLAC', 'MP3']);
  });

  it('stays hidden while the stream is still running', () => {
    // The set fills up as events arrive, but the vanilla only built the block
    // on the terminal frame — tags must not appear and grow mid-stream.
    renderHero({ name: 'A' }, {}, false, { counts: counts(), completed: false });
    expect(document.querySelector('.artist-formats')).toBeNull();
  });

  it('renders no empty block when nothing reported a format', () => {
    renderHero({ name: 'A' }, {}, false, { counts: emptyStreamCounts(), completed: true });
    expect(document.querySelector('.artist-formats')).toBeNull();
  });

  it('sits between the genre chips and the bio', () => {
    renderHero({ name: 'A', genres: ['idm'], lastfm_bio: 'words' }, {}, false, {
      counts: counts(),
      completed: true,
    });
    const info = document.querySelector('.artist-info') as HTMLElement;
    const order = [...info.children].map((n) => n.className);
    expect(order.indexOf('artist-formats')).toBe(order.indexOf('artist-genres-container') + 1);
    expect(order.indexOf('artist-hero-bio')).toBe(order.indexOf('artist-formats') + 1);
  });
});

describe('hero elements the vanilla globals reach for by id', () => {
  it('renders the watchlist button initializeLibraryWatchlistButton wires', () => {
    // The global installs its own onclick and toggles `watching`; without the
    // element it silently returns and the button never appears.
    renderHero({ name: 'A' });
    const btn = document.getElementById('library-artist-watchlist-btn');
    expect(btn).not.toBeNull();
    expect(btn?.querySelector('.watchlist-text')?.textContent).toBe('Add to Watchlist');
  });

  it('renders the enhance button hidden, for checkArtistEnhanceEligibility to reveal', () => {
    // It unhides the button and rewrites .enhance-text with a count.
    renderHero({ name: 'A' });
    const btn = document.getElementById('library-artist-enhance-btn');
    expect(btn?.className).toContain('hidden');
    expect(btn?.querySelector('.enhance-text')).not.toBeNull();
  });

  it('opens the enhance modal on click', () => {
    window.openEnhanceQualityModal = vi.fn();
    renderHero({ name: 'A' });
    fireEvent.click(document.getElementById('library-artist-enhance-btn') as HTMLElement);
    expect(window.openEnhanceQualityModal).toHaveBeenCalled();
    delete window.openEnhanceQualityModal;
  });

  it('places the actions in the vanilla order', () => {
    renderHero({ name: 'A' }, { albums: [{ id: 1 }] });
    const actions = document.querySelector('.artist-hero-actions') as HTMLElement;
    expect([...actions.children].map((n) => n.id)).toEqual([
      'library-artist-radio-btn',
      'library-artist-watchlist-btn',
      'discog-download-wrap',
      'library-artist-enhance-btn',
    ]);
  });

  it('renders the enrichment rings LAST inside the artist info column', () => {
    renderHero({ name: 'A' }, {}, false, {
      enrichment: { total_tracks: 5, spotify: 100 },
    });
    const info = document.querySelector('.artist-info') as HTMLElement;
    expect(info.lastElementChild?.id).toBe('artist-enrichment-coverage');
  });

  it('leaves the coverage block out when the artist has no enrichment data', () => {
    renderHero({ name: 'A' });
    expect(document.getElementById('artist-enrichment-coverage')).toBeNull();
  });
});
