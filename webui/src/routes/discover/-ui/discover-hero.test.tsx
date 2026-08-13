import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { DiscoverHeroArtist } from '../-discover.types';
import type { DiscoverSectionProps } from './discover-section';

import { DiscoverHero } from './discover-hero';
import { DiscoverSection } from './discover-section';

/**
 * The hero billboard and the shared section shell.
 *
 * The hero's conditionals all encode a decision that is invisible in the markup:
 * a popularity of ZERO is an absence and not a score, one artist is not a
 * slideshow, and an empty hero has to say what to do rather than sit blank.
 */

afterEach(cleanup);

const artist = (over: Partial<DiscoverHeroArtist> = {}): DiscoverHeroArtist => ({
  artist_id: 'sp1',
  artist_name: 'Aphex Twin',
  image_url: '/img/aphex.jpg',
  genres: ['idm', 'ambient', 'electronic', 'techno'],
  popularity: 84,
  ...over,
});

function heroProps(over: Partial<Parameters<typeof DiscoverHero>[0]> = {}) {
  return {
    artist: artist(),
    count: 5,
    index: 2,
    watchlist: null,
    watchAllPhase: 'idle' as const,
    discographyHref: '/artist-detail/spotify/sp1',
    onNavigate: vi.fn(),
    onJump: vi.fn(),
    onToggleWatchlist: vi.fn(),
    onWatchAll: vi.fn(),
    onViewRecommended: vi.fn(),
    onOpenBlacklist: vi.fn(),
    ...over,
  };
}

// ── The hero ─────────────────────────────────────────────────────────────────

describe('the hero', () => {
  it('shows the artist name from artist_name', () => {
    // NOT `name`. The response has no such field, and the interface's index
    // signature means tsc would never have said so.
    render(<DiscoverHero {...heroProps()} />);
    expect(screen.getByText('Aphex Twin')).toBeInTheDocument();
  });

  it('shows at most three genres, as .genre-tag pills in .hero-genres', () => {
    const { container } = render(<DiscoverHero {...heroProps()} />);
    expect(container.querySelectorAll('.hero-genres .genre-tag')).toHaveLength(3);
    expect(screen.queryByText('techno')).toBeNull();
  });

  it('drops the genres item entirely when there are none', () => {
    const { container } = render(
      <DiscoverHero {...heroProps({ artist: artist({ genres: [] }) })} />,
    );
    expect(container.querySelector('.hero-genres')).toBeNull();
  });

  it('subtitles with the per-artist REASON, carrying the full list as a title', () => {
    // The vanilla sets the subtitle to the "because you have X, Y" line per
    // artist (468); static copy is only the pre-load placeholder.
    const { container } = render(
      <DiscoverHero
        {...heroProps({ artist: artist({ because: ['Squarepusher', 'Autechre'] } as never) })}
      />,
    );
    const sub = container.querySelector('#discover-hero-subtitle')!;
    expect(sub.textContent).toBe('Because you have Squarepusher & Autechre');
    expect(sub).toHaveAttribute('title', 'In your library: Squarepusher, Autechre');
  });

  it('treats a popularity of zero as an absence, not a score', () => {
    // The server sends 0 for artists it knows nothing about; a zero tile reads
    // as a judgement rather than missing data.
    const { container } = render(
      <DiscoverHero {...heroProps({ artist: artist({ popularity: 0 }) })} />,
    );
    expect(container.querySelector('.hero-popularity')).toBeNull();
  });

  it('renders the popularity tile with the vanilla markup, banded', () => {
    // .hero-meta-item.hero-popularity.{band} with icon / "N/100" / label
    // (476-484) — the first draft invented flat spans and "84% match".
    const { container } = render(<DiscoverHero {...heroProps()} />);
    const tile = container.querySelector('.hero-meta-item.hero-popularity')!;
    expect(tile).toHaveClass('high');
    expect(tile.querySelector('.meta-icon')!.textContent).toBe('⭐');
    expect(tile.querySelector('.meta-value')!.textContent).toBe('84/100');
    expect(tile.querySelector('.meta-label')!.textContent).toBe('Popularity');
    // And the whole meta block sits inside the content wrapper (474).
    expect(container.querySelector('.discover-hero-meta-content')).not.toBeNull();
  });

  it.each([
    [84, 'high'],
    [60, 'medium'],
    [20, 'low'],
  ])('bands a popularity of %s as %s', (popularity, band) => {
    const { container } = render(
      <DiscoverHero {...heroProps({ artist: artist({ popularity }) })} />,
    );
    expect(container.querySelector('.hero-popularity')).toHaveClass(band);
  });

  it('hides the arrows and dots when there is only one artist', () => {
    const { container } = render(<DiscoverHero {...heroProps({ count: 1, index: 0 })} />);
    expect(container.querySelectorAll('.discover-hero-nav')).toHaveLength(0);
    expect(container.querySelectorAll('.hero-indicator')).toHaveLength(0);
  });

  it('marks exactly one dot as current', () => {
    const { container } = render(<DiscoverHero {...heroProps()} />);
    const dots = [...container.querySelectorAll('.hero-indicator')];
    expect(dots).toHaveLength(5);
    expect(dots.filter((d) => d.classList.contains('active'))).toHaveLength(1);
    expect(dots[2]).toHaveClass('active');
  });

  it('labels the dots one-based, for screen readers', () => {
    const { container } = render(<DiscoverHero {...heroProps()} />);
    expect(container.querySelectorAll('.hero-indicator')[0]).toHaveAttribute(
      'aria-label',
      'Go to slide 1',
    );
  });

  it('navigates and jumps', () => {
    const p = heroProps();
    const { container } = render(<DiscoverHero {...p} />);
    fireEvent.click(container.querySelector('.discover-hero-nav-prev')!);
    expect(p.onNavigate).toHaveBeenCalledWith(-1);
    fireEvent.click(container.querySelector('.discover-hero-nav-next')!);
    expect(p.onNavigate).toHaveBeenLastCalledWith(1);
    fireEvent.click(container.querySelectorAll('.hero-indicator')[4]);
    expect(p.onJump).toHaveBeenCalledWith(4);
  });

  it('explains what to do when there is nothing to feature', () => {
    const { container } = render(<DiscoverHero {...heroProps({ artist: null })} />);
    expect(screen.getByText('No Recommendations Yet')).toBeInTheDocument();
    expect(
      screen.getByText('Run a watchlist scan to generate personalized recommendations'),
    ).toBeInTheDocument();
    // No artist means no artist actions — a discography link to nowhere and a
    // watchlist button for nobody.
    expect(container.querySelector('.discover-hero-actions')).toBeNull();
  });

  it('falls back to the placeholder with no image', () => {
    const { container } = render(
      <DiscoverHero {...heroProps({ artist: artist({ image_url: undefined }) })} />,
    );
    expect(container.querySelector('.hero-image-placeholder')).toBeInTheDocument();
    expect(container.querySelector('#discover-hero-image img')).toBeNull();
    // And no inline background either — `url(undefined)` renders as a broken
    // image request on every hero rotation.
    expect(
      (container.querySelector('#discover-hero-bg') as HTMLElement).style.backgroundImage,
    ).toBe('');
  });

  it('paints the artwork into both the backdrop and the portrait', () => {
    const { container } = render(<DiscoverHero {...heroProps()} />);
    expect(container.querySelector('#discover-hero-image img')).toHaveAttribute(
      'src',
      '/img/aphex.jpg',
    );
    const bg = container.querySelector('#discover-hero-bg') as HTMLElement;
    expect(bg.style.backgroundImage).toContain('/img/aphex.jpg');
    // cover/center ride along inline, exactly as the vanilla sets them (509-510).
    expect(bg.style.backgroundSize).toBe('cover');
    expect(bg.style.backgroundPosition).toBe('center center'); //  as the CSSOM normalises it
  });

  it('leaves the watchlist button alone when the check has not answered', () => {
    // null is not "not watching" — a failed check says nothing about
    // membership, so the class must not claim either way.
    const { container } = render(<DiscoverHero {...heroProps({ watchlist: null })} />);
    const btn = container.querySelector('#discover-hero-add')!;
    expect(btn).not.toHaveClass('watching');
    expect(screen.getByText('Add to Watchlist')).toBeInTheDocument();
  });

  it('reflects a resolved watching state', () => {
    const { container } = render(
      <DiscoverHero
        {...heroProps({
          watchlist: { icon: '👁️', label: 'Watching...', watching: true },
        })}
      />,
    );
    expect(container.querySelector('#discover-hero-add')).toHaveClass('watching');
    expect(screen.getByText('Watching...')).toBeInTheDocument();
  });

  it.each([
    ['idle', 'Watch All', false],
    ['busy', 'Adding...', true],
    ['done', 'All Watched', true],
  ] as const)('renders Watch All in the %s state', (phase, label, disabled) => {
    render(<DiscoverHero {...heroProps({ watchAllPhase: phase })} />);
    const btn = document.getElementById('discover-hero-watch-all') as HTMLButtonElement;
    expect(btn.textContent).toContain(label);
    // busy AND done are both disabled: one stops a double batch-add, the other
    // stops a no-op post the user cannot tell from a failure.
    expect(btn.disabled).toBe(disabled);
  });

  it('marks the all-watched state for the stylesheet', () => {
    const { rerender } = render(<DiscoverHero {...heroProps({ watchAllPhase: 'busy' })} />);
    expect(document.getElementById('discover-hero-watch-all')).not.toHaveClass('all-watched');
    rerender(<DiscoverHero {...heroProps({ watchAllPhase: 'done' })} />);
    expect(document.getElementById('discover-hero-watch-all')).toHaveClass('all-watched');
  });

  it('links the discography where it was told to', () => {
    render(<DiscoverHero {...heroProps()} />);
    expect(document.getElementById('discover-hero-discography')).toHaveAttribute(
      'href',
      '/artist-detail/spotify/sp1',
    );
  });

  it('wires the remaining buttons', () => {
    const p = heroProps();
    const { container } = render(<DiscoverHero {...p} />);
    fireEvent.click(container.querySelector('#discover-hero-add')!);
    fireEvent.click(container.querySelector('#discover-hero-watch-all')!);
    fireEvent.click(container.querySelector('#discover-hero-view-all')!);
    fireEvent.click(container.querySelector('.discover-blacklist-btn')!);
    expect(p.onToggleWatchlist).toHaveBeenCalled();
    expect(p.onWatchAll).toHaveBeenCalled();
    expect(p.onViewRecommended).toHaveBeenCalled();
    expect(p.onOpenBlacklist).toHaveBeenCalled();
  });
});

// ── The section shell ────────────────────────────────────────────────────────

describe('the section shell', () => {
  const section = (over: Partial<DiscoverSectionProps> = {}): DiscoverSectionProps => ({
    id: 'your-artists-section',
    title: 'Your Artists',
    count: 3,
    loaded: true,
    children: <div className="rows">rows</div>,
    ...over,
  });

  it('uses the section id VERBATIM as the element id', () => {
    // These ids already end in '-section'; appending another would detach every
    // stylesheet rule and scroll target that names them.
    const { container } = render(<DiscoverSection {...section()} />);
    expect(container.querySelector('#your-artists-section')).not.toBeNull();
    expect(container.querySelector('#your-artists-section-section')).toBeNull();
  });

  it('renders the header, subtitle and actions', () => {
    render(
      <DiscoverSection
        {...section({ subtitle: 'Artists you follow', actions: <button type="button">Go</button> })}
      />,
    );
    expect(screen.getByText('Your Artists')).toBeInTheDocument();
    expect(screen.getByText('Artists you follow')).toBeInTheDocument();
    expect(screen.getByText('Go')).toBeInTheDocument();
  });

  it('omits the subtitle and actions when it has none', () => {
    const { container } = render(<DiscoverSection {...section()} />);
    expect(container.querySelector('.discover-section-subtitle')).toBeNull();
    expect(container.querySelector('.discover-section-actions')).toBeNull();
  });

  it('vanishes entirely when a hide-when-empty section has no rows', () => {
    const { container } = render(<DiscoverSection {...section({ count: 0 })} />);
    expect(container.querySelector('.discover-section')).toBeNull();
  });

  it('stays and explains when an empty-state section has no rows', () => {
    // These messages tell the user what to DO, which is lost if the section
    // simply disappears — and the markup is the controller's own
    // `.discover-empty > p`, which style.css actually styles.
    const { container } = render(
      <DiscoverSection {...section({ id: 'recent-releases', count: 0 })} />,
    );
    expect(container.querySelector('.discover-empty > p')!.textContent).toBe(
      'No recent releases found',
    );
  });

  it('stays out of the layout until its load completes', () => {
    // An empty-state section is NOT shown while loading. That is what stops a
    // flash of "No recent releases found" on every cold page load — the vanilla
    // loader only creates the section once it has an answer.
    const { container, rerender } = render(
      <DiscoverSection {...section({ id: 'recent-releases', count: 0, loaded: false })} />,
    );
    expect(container.querySelector('.discover-section')).toBeNull();

    rerender(<DiscoverSection {...section({ id: 'recent-releases', count: 0, loaded: true })} />);
    expect(screen.getByText('No recent releases found')).toBeInTheDocument();
  });

  it('shows rows that arrived before the load flag did', () => {
    // hasItems wins over `loaded`: content is proof the fetch answered, and
    // hiding a populated shelf on a stale flag is the worse failure.
    const { container } = render(
      <DiscoverSection {...section({ id: 'recent-releases', count: 4, loaded: false })} />,
    );
    expect(container.querySelector('.rows')).not.toBeNull();
  });

  it('renders its children when it has rows', () => {
    const { container } = render(<DiscoverSection {...section()} />);
    expect(container.querySelector('.rows')).not.toBeNull();
    expect(container.querySelector('.discover-section-empty')).toBeNull();
  });

  it('lets a caller override the empty message', () => {
    render(
      <DiscoverSection
        {...section({ id: 'recent-releases', count: 0, emptyMessage: 'Nothing new this week' })}
      />,
    );
    expect(screen.getByText('Nothing new this week')).toBeInTheDocument();
  });

  it('always renders the controls that are not shelves', () => {
    const { container } = render(<DiscoverSection {...section({ id: 'adv-wave', count: 0 })} />);
    expect(container.querySelector('#adv-wave')).not.toBeNull();
  });
});
