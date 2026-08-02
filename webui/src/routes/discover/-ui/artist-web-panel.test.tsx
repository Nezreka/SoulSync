import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { WebArtistCard, WebDiscoveryCard, WebGenreCard } from '../-discover.artist-web.panel';

import {
  ArtWebArtistCard,
  ArtWebDiscoveryCard,
  ArtWebFirstRunHint,
  ArtWebGenreCard,
  ArtWebHelpModal,
  ArtWebPanel,
  ArtWebPathCard,
  ArtWebPathHint,
} from './artist-web-panel';

/**
 * The Artist Web's side panel.
 *
 * The three cards are the same box showing three genuinely different things, and
 * most of what can go wrong is a control appearing where it must not: an expand
 * button on a discovery candidate (which always comes back empty), a play button
 * on a node with no library id, a detail link built from a server name. Those are
 * the cases here.
 */

afterEach(cleanup);

const artist = (over: Partial<WebArtistCard> = {}): WebArtistCard => ({
  key: 'artist:12',
  label: 'Aphex Twin',
  color: '#1db954',
  connections: 14,
  popularity: 73,
  primaryGenre: 'IDM',
  artistId: 12,
  detailPath: '/artist-detail/library/12',
  canPlayRadio: true,
  canExpand: false,
  expanded: false,
  ...over,
});

describe('the panel shell', () => {
  it('keeps the ids the vanilla styling and scroll behaviour target', () => {
    const { container } = render(
      <ArtWebPanel>
        <span>body</span>
      </ArtWebPanel>,
    );
    expect(container.querySelector('#artweb-panel')).not.toBeNull();
    expect(container.querySelector('#artweb-panel-body')).not.toBeNull();
  });

  it('anchors itself absolutely, and does not reposition its container', () => {
    // The container is `position: fixed; inset: 0` in CSS. An inline `relative`
    // on it collapses the fullscreen overlay and displaces the sigma canvas,
    // which freezes every click — the vanilla carries a warning about it.
    const host = document.createElement('div');
    document.body.append(host);
    render(
      <ArtWebPanel>
        <span>body</span>
      </ArtWebPanel>,
      { container: host },
    );
    expect(host.style.position).toBe('');
    expect((host.querySelector('#artweb-panel') as HTMLElement).style.position).toBe('absolute');
  });
});

// ── The owned-artist card ────────────────────────────────────────────────────

describe('the artist card', () => {
  const handlers = () => ({
    onClose: vi.fn(),
    onPlayRadio: vi.fn(),
    onExpand: vi.fn(),
    onExploreInMap: vi.fn(),
  });

  it('shows the name, genre and both stats', () => {
    render(<ArtWebArtistCard card={artist()} {...handlers()} />);
    expect(screen.getByText('Aphex Twin')).toBeInTheDocument();
    expect(screen.getByText('IDM')).toBeInTheDocument();
    expect(screen.getByText('73')).toBeInTheDocument();
    expect(screen.getByText('14')).toBeInTheDocument();
  });

  it('fills the popularity meter to the score', () => {
    const { container } = render(
      <ArtWebArtistCard card={artist({ popularity: 40 })} {...handlers()} />,
    );
    const bar = container.querySelector('.artweb-card-meter > div') as HTMLElement;
    expect(bar.style.width).toBe('40%');
  });

  it('offers radio only when the node has a library id', () => {
    render(<ArtWebArtistCard card={artist({ canPlayRadio: false })} {...handlers()} />);
    expect(screen.queryByText('▶ Play radio')).toBeNull();
  });

  it('offers the detail link only when there is one to offer', () => {
    const { container, rerender } = render(<ArtWebArtistCard card={artist()} {...handlers()} />);
    expect(container.querySelector('a.artweb-btn-link')).toHaveAttribute(
      'href',
      // The LIBRARY route. These are all owned artists, and using a.source (the
      // server name, 'plex') is what produced the broken /artist-detail/plex/… .
      '/artist-detail/library/12',
    );
    rerender(<ArtWebArtistCard card={artist({ detailPath: null })} {...handlers()} />);
    expect(container.querySelector('a.artweb-btn-link')).toBeNull();
  });

  it('offers expand only on the discovery lens, and says when it is done', () => {
    const { rerender } = render(<ArtWebArtistCard card={artist()} {...handlers()} />);
    expect(document.getElementById('artweb-expand-btn')).toBeNull();

    rerender(<ArtWebArtistCard card={artist({ canExpand: true })} {...handlers()} />);
    expect(document.getElementById('artweb-expand-btn')!.textContent).toBe('Expand connections ✦');

    rerender(
      <ArtWebArtistCard card={artist({ canExpand: true, expanded: true })} {...handlers()} />,
    );
    expect(document.getElementById('artweb-expand-btn')!.textContent).toBe('Expanded ✓');
  });

  it('demotes Explore-in-Map to a ghost button when expand takes the accent', () => {
    // Two solid accent buttons stacked read as two primary actions.
    const { container, rerender } = render(<ArtWebArtistCard card={artist()} {...handlers()} />);
    expect(container.querySelector('.artweb-card-actions .artweb-btn-lens')).not.toBeNull();
    rerender(<ArtWebArtistCard card={artist({ canExpand: true })} {...handlers()} />);
    expect(screen.getByText('Explore in Artist Map →')).toHaveClass('artweb-btn-ghost');
  });

  it('shows the glyph until a photo arrives, then the photo', () => {
    const { container, rerender } = render(<ArtWebArtistCard card={artist()} {...handlers()} />);
    expect(container.querySelector('#artweb-avatar img')).toBeNull();
    rerender(<ArtWebArtistCard card={artist()} imageUrl="/img/aphex.jpg" {...handlers()} />);
    expect(container.querySelector('#artweb-avatar img')).toHaveAttribute('src', '/img/aphex.jpg');
  });

  it('reports every action with the node it belongs to', () => {
    const h = handlers();
    render(<ArtWebArtistCard card={artist({ canExpand: true })} {...h} />);
    fireEvent.click(screen.getByText('▶ Play radio'));
    fireEvent.click(document.getElementById('artweb-expand-btn')!);
    fireEvent.click(screen.getByText('Explore in Artist Map →'));
    fireEvent.click(screen.getByText('✕ Close'));
    expect(h.onPlayRadio).toHaveBeenCalledWith('artist:12');
    expect(h.onExpand).toHaveBeenCalledWith('artist:12');
    // The MAP searches by name, not key — it has no idea what a graph key is.
    expect(h.onExploreInMap).toHaveBeenCalledWith('Aphex Twin');
    expect(h.onClose).toHaveBeenCalled();
  });
});

// ── The genre card ───────────────────────────────────────────────────────────

describe('the genre card', () => {
  const card = (over: Partial<WebGenreCard> = {}): WebGenreCard => ({
    genre: 'Techno',
    color: '#e91e63',
    members: [
      { key: 'a', label: 'Jeff Mills', pop: 70 },
      { key: 'b', label: 'Robert Hood', pop: 60 },
    ],
    total: 44,
    ...over,
  });

  it('reports the TRUE total, not the number of rows shown', () => {
    // Members are capped at thirty; printing the capped length would make every
    // large genre claim exactly thirty artists.
    render(<ArtWebGenreCard card={card()} onClose={vi.fn()} onGoToArtist={vi.fn()} />);
    expect(screen.getByText('44 artists in your library')).toBeInTheDocument();
  });

  it('singularises a one-artist genre', () => {
    render(
      <ArtWebGenreCard
        card={card({ total: 1, members: [{ key: 'a', label: 'Solo', pop: 1 }] })}
        onClose={vi.fn()}
        onGoToArtist={vi.fn()}
      />,
    );
    expect(screen.getByText('1 artist in your library')).toBeInTheDocument();
  });

  it('numbers the members in the order given', () => {
    const { container } = render(
      <ArtWebGenreCard card={card()} onClose={vi.fn()} onGoToArtist={vi.fn()} />,
    );
    const ranks = [...container.querySelectorAll('.artweb-member-rank')].map((r) => r.textContent);
    expect(ranks).toEqual(['1', '2']);
  });

  it('says so when a hub has no artists', () => {
    render(
      <ArtWebGenreCard
        card={card({ members: [], total: 0 })}
        onClose={vi.fn()}
        onGoToArtist={vi.fn()}
      />,
    );
    expect(screen.getByText('No artists')).toBeInTheDocument();
  });

  it('reports a member pick by key', () => {
    const onGoToArtist = vi.fn();
    render(<ArtWebGenreCard card={card()} onClose={vi.fn()} onGoToArtist={onGoToArtist} />);
    fireEvent.click(screen.getByText('Robert Hood'));
    expect(onGoToArtist).toHaveBeenCalledWith('b');
  });
});

// ── The discovery card ───────────────────────────────────────────────────────

describe('the discovery card', () => {
  const card = (over: Partial<WebDiscoveryCard> = {}): WebDiscoveryCard => ({
    key: 'disc:99',
    label: 'Boards of Canada',
    imageUrl: '/img/boc.jpg',
    genres: ['downtempo', 'idm'],
    detailPath: '/artist-detail/spotify/abc',
    canPreview: true,
    ...over,
  });

  const handlers = () => ({
    onClose: vi.fn(),
    onTogglePreview: vi.fn(),
    onAddToWatchlist: vi.fn(),
  });

  it('marks the artist as unowned and lists its genres', () => {
    render(
      <ArtWebDiscoveryCard card={card()} previewLabel="▶ Preview top track" {...handlers()} />,
    );
    expect(screen.getByText('Not in your library')).toBeInTheDocument();
    expect(screen.getByText('downtempo')).toBeInTheDocument();
    expect(screen.getByText('idm')).toBeInTheDocument();
  });

  it('never offers to expand a candidate', () => {
    // similar_artists only holds rows for artists SoulSync fetched similars for,
    // so expanding an unowned candidate always returns empty — 0 of 176 on real
    // data. A button that reliably does nothing is worse than no button.
    render(<ArtWebDiscoveryCard card={card()} previewLabel="x" {...handlers()} />);
    expect(document.getElementById('artweb-expand-btn')).toBeNull();
  });

  it('offers preview only where a Deezer id exists', () => {
    const { rerender } = render(
      <ArtWebDiscoveryCard card={card()} previewLabel="▶ Preview top track" {...handlers()} />,
    );
    expect(document.getElementById('artweb-preview-btn')).not.toBeNull();
    rerender(
      <ArtWebDiscoveryCard card={card({ canPreview: false })} previewLabel="x" {...handlers()} />,
    );
    expect(document.getElementById('artweb-preview-btn')).toBeNull();
  });

  it('shows whatever label the preview is currently in', () => {
    const { rerender } = render(
      <ArtWebDiscoveryCard
        card={card()}
        previewLabel="Loading preview…"
        previewBusy
        {...handlers()}
      />,
    );
    const btn = () => document.getElementById('artweb-preview-btn') as HTMLButtonElement;
    expect(btn().textContent).toBe('Loading preview…');
    expect(btn().disabled).toBe(true);
    rerender(<ArtWebDiscoveryCard card={card()} previewLabel="⏸ Roygbiv" {...handlers()} />);
    expect(btn().textContent).toBe('⏸ Roygbiv');
    expect(btn().disabled).toBe(false);
  });

  it('shows the candidate photo the payload carried', () => {
    const { container } = render(
      <ArtWebDiscoveryCard card={card()} previewLabel="x" {...handlers()} />,
    );
    // The counterpart to the fallback below — without this, never rendering the
    // image at all would still pass.
    expect(container.querySelector('.artweb-avatar img')).toHaveAttribute('src', '/img/boc.jpg');
  });

  it('falls back to the glyph with no image', () => {
    const { container } = render(
      <ArtWebDiscoveryCard card={card({ imageUrl: null })} previewLabel="x" {...handlers()} />,
    );
    expect(container.querySelector('.artweb-avatar img')).toBeNull();
    expect(container.querySelector('.artweb-avatar-glyph')).not.toBeNull();
  });

  it('omits the pill row entirely when there are no genres', () => {
    const { container } = render(
      <ArtWebDiscoveryCard card={card({ genres: [] })} previewLabel="x" {...handlers()} />,
    );
    expect(container.querySelector('.artweb-card-pills')).toBeNull();
  });

  it('links to the synthesised detail page when one is available', () => {
    const { container, rerender } = render(
      <ArtWebDiscoveryCard card={card()} previewLabel="x" {...handlers()} />,
    );
    expect(container.querySelector('a.artweb-btn-link')).toHaveAttribute(
      'href',
      '/artist-detail/spotify/abc',
    );
    rerender(
      <ArtWebDiscoveryCard card={card({ detailPath: null })} previewLabel="x" {...handlers()} />,
    );
    expect(container.querySelector('a.artweb-btn-link')).toBeNull();
  });

  it('reports add and preview by key', () => {
    const h = handlers();
    render(<ArtWebDiscoveryCard card={card()} previewLabel="x" {...h} />);
    fireEvent.click(document.getElementById('artweb-add-btn')!);
    fireEvent.click(document.getElementById('artweb-preview-btn')!);
    expect(h.onAddToWatchlist).toHaveBeenCalledWith('disc:99');
    expect(h.onTogglePreview).toHaveBeenCalledWith('disc:99');
  });
});

// ── The path card ────────────────────────────────────────────────────────────

describe('the path card', () => {
  const rows = (n: number) =>
    Array.from({ length: n }, (_, i) => ({
      key: `k${i}`,
      label: `Artist ${i}`,
      color: '#1db954',
      tag: i === 0 ? 'start' : i === n - 1 ? 'end' : '',
    }));

  it('counts hops, not nodes', () => {
    render(<ArtWebPathCard rows={rows(4)} onDone={vi.fn()} onCameraTo={vi.fn()} />);
    expect(screen.getByText('3 hops apart')).toBeInTheDocument();
    expect(screen.getByText('via 2 artists in between')).toBeInTheDocument();
  });

  it('singularises one hop and calls it direct', () => {
    render(<ArtWebPathCard rows={rows(2)} onDone={vi.fn()} onCameraTo={vi.fn()} />);
    expect(screen.getByText('1 hop apart')).toBeInTheDocument();
    expect(screen.getByText('directly similar')).toBeInTheDocument();
  });

  it('singularises a single artist in between', () => {
    render(<ArtWebPathCard rows={rows(3)} onDone={vi.fn()} onCameraTo={vi.fn()} />);
    expect(screen.getByText('via 1 artist in between')).toBeInTheDocument();
  });

  it('marks and rings only the two ends', () => {
    const { container } = render(
      <ArtWebPathCard rows={rows(4)} onDone={vi.fn()} onCameraTo={vi.fn()} />,
    );
    expect([...container.querySelectorAll('.artweb-path-tag')].map((t) => t.textContent)).toEqual([
      'start',
      'end',
    ]);
    const weights = [...container.querySelectorAll('.artweb-path-name')].map(
      (n) => (n as HTMLElement).style.fontWeight,
    );
    expect(weights).toEqual(['800', '600', '600', '800']);
  });

  it('draws one connector fewer than it has rows', () => {
    const { container } = render(
      <ArtWebPathCard rows={rows(4)} onDone={vi.fn()} onCameraTo={vi.fn()} />,
    );
    expect(container.querySelectorAll('.artweb-path-link')).toHaveLength(3);
  });

  it('flies the camera to a row without selecting it', () => {
    const onCameraTo = vi.fn();
    render(<ArtWebPathCard rows={rows(3)} onDone={vi.fn()} onCameraTo={onCameraTo} />);
    fireEvent.click(screen.getByText('Artist 1'));
    expect(onCameraTo).toHaveBeenCalledWith('k1');
  });

  it('closes with Done, not Close', () => {
    const onDone = vi.fn();
    render(<ArtWebPathCard rows={rows(2)} onDone={onDone} onCameraTo={vi.fn()} />);
    fireEvent.click(screen.getByText('✕ Done'));
    expect(onDone).toHaveBeenCalled();
  });
});

// ── The guide ────────────────────────────────────────────────────────────────

describe('the guide', () => {
  it('explains all three lenses and lists the shortcuts', () => {
    const { container } = render(<ArtWebHelpModal onClose={vi.fn()} />);
    for (const h of ['Three lenses', 'Explore', 'Tools']) {
      expect(screen.getByText(h)).toBeInTheDocument();
    }
    expect(container.querySelectorAll('.artmap-shortcut')).toHaveLength(4);
    expect([...container.querySelectorAll('kbd')].map((k) => k.textContent)).toEqual([
      'S',
      'F',
      '0',
      '+',
      '-',
      'Esc',
    ]);
  });

  it('closes on the backdrop but not on the card', () => {
    const onClose = vi.fn();
    const { container } = render(<ArtWebHelpModal onClose={onClose} />);
    fireEvent.click(container.querySelector('.artweb-help-modal')!);
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(container.querySelector('#artweb-help-overlay')!);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('closes on the × button', () => {
    const onClose = vi.fn();
    render(<ArtWebHelpModal onClose={onClose} />);
    fireEvent.click(screen.getByLabelText('Close'));
    expect(onClose).toHaveBeenCalled();
  });

  it('sits above the overlay it explains', () => {
    const { container } = render(<ArtWebHelpModal onClose={vi.fn()} />);
    expect((container.querySelector('#artweb-help-overlay') as HTMLElement).style.zIndex).toBe(
      '10002',
    );
  });
});

// ── The hints ────────────────────────────────────────────────────────────────

describe('the hints', () => {
  it('renders the path hint as markup, since the bolding is the point', () => {
    const { container } = render(<ArtWebPathHint html="Start: <b>Aphex Twin</b> — now click" />);
    expect(container.querySelector('#artweb-path-hint b')?.textContent).toBe('Aphex Twin');
  });

  it('fades the first-run pill rather than removing it mid-transition', () => {
    // Removing it outright would cut the 400ms fade to nothing.
    const { container, rerender } = render(<ArtWebFirstRunHint fading={false} />);
    const el = () => container.querySelector('#artweb-firstrun-hint') as HTMLElement;
    expect(el().style.opacity).toBe('1');
    rerender(<ArtWebFirstRunHint fading />);
    expect(el().style.opacity).toBe('0');
    expect(el()).toBeInTheDocument();
  });
});
