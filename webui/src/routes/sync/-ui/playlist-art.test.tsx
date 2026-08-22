/**
 * Cover art on the sync cards.
 *
 * The point of these is the FALLBACK: a card that had a glyph before must still
 * have one in every case where art is missing or broken, because the glyph is
 * the only thing carrying the source's identity on cards that show no badge.
 */

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { PlaylistArt, PlaylistCollage, playlistArtUrl, playlistCoverTiles } from './playlist-art';

describe('PlaylistArt', () => {
  it('renders the cover when there is one', () => {
    render(<PlaylistArt url="/api/image-cache/abc" glyph="🎵" />);
    const img = screen.getByRole('presentation', { hidden: true }) as HTMLImageElement;
    expect(img.tagName).toBe('IMG');
    expect(screen.queryByText('🎵')).toBeNull();
  });

  it('asks the cache for a card-sized copy rather than the CDN master', () => {
    render(<PlaylistArt url="/api/image-cache/abc" glyph="🎵" />);
    const img = document.querySelector('img') as HTMLImageElement;
    expect(img.getAttribute('src')).toBe('/api/image-cache/abc?v=card');
  });

  it('leaves a foreign url alone — only our own cache understands ?v=', () => {
    render(<PlaylistArt url="https://i.scdn.co/image/xyz" glyph="🎵" />);
    const img = document.querySelector('img') as HTMLImageElement;
    expect(img.getAttribute('src')).toBe('https://i.scdn.co/image/xyz');
  });

  it('falls back to the glyph when there is no art at all', () => {
    render(<PlaylistArt glyph="🎵" />);
    expect(screen.getByText('🎵')).toBeTruthy();
    expect(document.querySelector('img')).toBeNull();
  });

  it('treats an empty string as no art, not as a broken image', () => {
    render(<PlaylistArt url="" glyph="🌊" />);
    expect(screen.getByText('🌊')).toBeTruthy();
    expect(document.querySelector('img')).toBeNull();
  });

  it('falls back to the glyph when the image fails to load', () => {
    render(<PlaylistArt url="/api/image-cache/dead" glyph="🎧" />);
    fireEvent.error(document.querySelector('img') as HTMLImageElement);
    expect(screen.getByText('🎧')).toBeTruthy();
    expect(document.querySelector('img')).toBeNull();
  });

  it('retries when the url CHANGES after a failure — a re-synced mirror gets new art', () => {
    const { rerender } = render(<PlaylistArt url="/api/image-cache/dead" glyph="🎵" />);
    fireEvent.error(document.querySelector('img') as HTMLImageElement);
    expect(document.querySelector('img')).toBeNull();

    rerender(<PlaylistArt url="/api/image-cache/fresh" glyph="🎵" />);
    const img = document.querySelector('img') as HTMLImageElement;
    expect(img).toBeTruthy();
    expect(img.getAttribute('src')).toBe('/api/image-cache/fresh?v=card');
  });

  it('does NOT retry the same url that just failed', () => {
    const { rerender } = render(<PlaylistArt url="/api/image-cache/dead" glyph="🎵" />);
    fireEvent.error(document.querySelector('img') as HTMLImageElement);
    rerender(<PlaylistArt url="/api/image-cache/dead" glyph="🎵" />);
    expect(document.querySelector('img')).toBeNull();
    expect(screen.getByText('🎵')).toBeTruthy();
  });

  it('is decorative — empty alt, so a screen reader reads the name, not the file', () => {
    render(<PlaylistArt url="/api/image-cache/abc" glyph="🎵" />);
    const img = document.querySelector('img') as HTMLImageElement;
    expect(img.getAttribute('alt')).toBe('');
    expect(img.getAttribute('loading')).toBe('lazy');
  });
});

describe('playlistArtUrl', () => {
  it('reads image_url off the loosely-typed rows this page carries', () => {
    expect(playlistArtUrl({ image_url: '/api/image-cache/x' })).toBe('/api/image-cache/x');
  });

  it('treats an empty string as absent — several sources write "" not NULL', () => {
    expect(playlistArtUrl({ image_url: '' })).toBeUndefined();
  });

  it('is safe on the shapes a SELECT * or an untyped payload can produce', () => {
    for (const row of [null, undefined, 'a string', 42, {}, { image_url: null }, { image_url: 7 }]) {
      expect(playlistArtUrl(row)).toBeUndefined();
    }
  });
});

describe('playlistCoverTiles', () => {
  it('parses the JSON column the backfill writes', () => {
    expect(playlistCoverTiles({ cover_tiles: '["a.jpg","b.jpg"]' })).toEqual(['a.jpg', 'b.jpg']);
  });

  it('accepts an already-parsed array', () => {
    expect(playlistCoverTiles({ cover_tiles: ['a.jpg'] })).toEqual(['a.jpg']);
  });

  it('is empty for the shapes a nullable JSON column really produces', () => {
    for (const row of [null, undefined, {}, { cover_tiles: null }, { cover_tiles: '' },
                       { cover_tiles: '[]' }, { cover_tiles: 'not json' },
                       { cover_tiles: '{"a":1}' }, { cover_tiles: 42 }]) {
      expect(playlistCoverTiles(row)).toEqual([]);
    }
  });

  it('drops empty entries rather than rendering blank tiles', () => {
    expect(playlistCoverTiles({ cover_tiles: '["a.jpg","",null]' })).toEqual(['a.jpg']);
  });
});

describe('PlaylistCollage', () => {
  const four = ['a.jpg', 'b.jpg', 'c.jpg', 'd.jpg'];

  it('makes a 2x2 from four covers', () => {
    const { container } = render(<PlaylistCollage tiles={four} glyph="🎵" />);
    expect(container.querySelectorAll('.playlist-card-collage img')).toHaveLength(4);
  });

  it('uses only the first four when there are more', () => {
    const { container } = render(<PlaylistCollage tiles={[...four, 'e.jpg']} glyph="🎵" />);
    expect(container.querySelectorAll('img')).toHaveLength(4);
  });

  it('shows ONE cover for one to three, never a 2x2 with holes', () => {
    // A 2x2 with two blanks reads as a broken image; one cover reads as a cover.
    for (const tiles of [['a.jpg'], ['a.jpg', 'b.jpg'], ['a.jpg', 'b.jpg', 'c.jpg']]) {
      const { container, unmount } = render(<PlaylistCollage tiles={tiles} glyph="🎵" />);
      expect(container.querySelector('.playlist-card-collage')).toBeNull();
      expect(container.querySelectorAll('img')).toHaveLength(1);
      unmount();
    }
  });

  it('falls through to the brand mark with no tiles at all', () => {
    const { container } = render(<PlaylistCollage tiles={[]} fallback={<span>MARK</span>} />);
    expect(container.querySelectorAll('img')).toHaveLength(0);
    expect(container.textContent).toBe('MARK');
  });

  it('asks the cache for card-sized copies, like every other cover here', () => {
    const { container } = render(
      <PlaylistCollage tiles={['/api/image-cache/a', '/api/image-cache/b', '/api/image-cache/c', '/api/image-cache/d']} />,
    );
    expect(container.querySelector('img')?.getAttribute('src')).toBe('/api/image-cache/a?v=card');
  });
});
