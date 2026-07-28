import { fireEvent, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { ArtistInfo, Discography } from '../-artist-detail.types';

import { ArtistHero } from './artist-hero';

function renderHero(artist: ArtistInfo, discography: Discography = {}, isSourceArtist = false) {
  return render(
    <ArtistHero artist={artist} discography={discography} isSourceArtist={isSourceArtist} />,
  );
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
