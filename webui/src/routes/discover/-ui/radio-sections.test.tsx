import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { LastfmRadioSectionProps, ListenBrainzSectionProps } from './radio-sections';

import { LastfmRadioSection, ListenBrainzSection } from './radio-sections';

/**
 * The Last.fm Radio and ListenBrainz sections.
 *
 * Both had invented markup in their first version — made-up row classes with no
 * artwork, a made-up tab strip with disabled placeholder tabs, a one-line
 * connect prompt — and passed full mutation passes, because the tests asserted
 * the invention. Everything here now pins the vanilla's real renderers:
 * decade-styled tabs that exist ONLY when they have data, the `.lb-empty-state`
 * connect card, and result rows with artwork and a meta column.
 */

afterEach(cleanup);

const mix = (key: string, title: string) => ({ key, title, trackCount: 10 });

// ── Last.fm Radio ────────────────────────────────────────────────────────────

function lastfm(over: Partial<LastfmRadioSectionProps> = {}): LastfmRadioSectionProps {
  return {
    query: '',
    results: [],
    dropdownOpen: false,
    mixes: [],
    loaded: true,
    onQueryChange: vi.fn(),
    onPick: vi.fn(),
    onClear: vi.fn(),
    onOpenMix: vi.fn(),
    ...over,
  };
}

describe('Last.fm Radio', () => {
  it('renders under the VANILLA section id, not the layout key', () => {
    const { container } = render(<LastfmRadioSection {...lastfm()} />);
    expect(container.querySelector('#lastfm-radio-section')).not.toBeNull();
    expect(container.querySelector('#lastfm-radio')).toBeNull();
    expect(container.querySelector('#lastfm-radio-input')).not.toBeNull();
    expect(container.querySelector('#lastfm-radio-playlists')).not.toBeNull();
  });

  it('keeps the dropdown closed until told to open', () => {
    const { container } = render(
      <LastfmRadioSection {...lastfm({ results: [{ name: 'Xtal', artist: 'Aphex Twin' }] })} />,
    );
    expect(container.querySelector('#lastfm-radio-dropdown')).toBeNull();
  });

  it('shows the mini spinner while the search is in flight', () => {
    const { container } = render(
      <LastfmRadioSection {...lastfm({ dropdownOpen: true, searching: true })} />,
    );
    expect(
      container.querySelector('.lastfm-radio-searching .server-search-spinner'),
    ).not.toBeNull();
    expect(container.querySelector('.lastfm-radio-result')).toBeNull();
  });

  it('renders rows as artwork plus a meta column, in the vanilla classes', () => {
    const { container } = render(
      <LastfmRadioSection
        {...lastfm({
          dropdownOpen: true,
          results: [
            { name: 'Xtal', artist: 'Aphex Twin', image_url: '/img/x.jpg', listeners: 120_000 },
          ],
        })}
      />,
    );
    const row = container.querySelector('.lastfm-radio-result')!;
    expect(row.querySelector('.lastfm-radio-result-art img')).toHaveAttribute('src', '/img/x.jpg');
    expect(row.querySelector('.lastfm-radio-result-track')!.textContent).toBe('Xtal');
    // artist and listener count share ONE line (3276) — there is no separate
    // listeners span; the vanilla computes one and never renders it.
    expect(row.querySelector('.lastfm-radio-result-artist')!.textContent).toBe(
      'Aphex Twin · 120,000 listeners',
    );
  });

  it('falls back to the empty-art block, and on a broken image', () => {
    const { container } = render(
      <LastfmRadioSection
        {...lastfm({
          dropdownOpen: true,
          results: [
            { name: 'A', artist: 'B' },
            { name: 'C', artist: 'D', image_url: '/img/dead.jpg' },
          ],
        })}
      />,
    );
    expect(container.querySelectorAll('.lastfm-radio-art-empty')).toHaveLength(1);
    fireEvent.error(container.querySelector('.lastfm-radio-result-art img')!);
    expect(container.querySelectorAll('.lastfm-radio-art-empty')).toHaveLength(2);
  });

  it('drops the listener text entirely at zero', () => {
    const { container } = render(
      <LastfmRadioSection
        {...lastfm({
          dropdownOpen: true,
          results: [{ name: 'Xtal', artist: 'Aphex Twin', listeners: 0 }],
        })}
      />,
    );
    expect(container.querySelector('.lastfm-radio-result-artist')!.textContent).toBe('Aphex Twin');
  });

  it('reports typing, picking and Escape', () => {
    const p = lastfm({ dropdownOpen: true, results: [{ name: 'Xtal', artist: 'Aphex Twin' }] });
    const { container } = render(<LastfmRadioSection {...p} />);
    const input = container.querySelector('#lastfm-radio-input')!;
    fireEvent.change(input, { target: { value: 'xtal' } });
    expect(p.onQueryChange).toHaveBeenCalledWith('xtal');
    fireEvent.click(container.querySelector('.lastfm-radio-result')!);
    expect(p.onPick).toHaveBeenCalledWith({ name: 'Xtal', artist: 'Aphex Twin' });
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(p.onClear).toHaveBeenCalled();
    fireEvent.keyDown(input, { key: 'a' });
    expect(p.onClear).toHaveBeenCalledTimes(1);
  });

  it('locks the input while a radio is generating', () => {
    const { container } = render(<LastfmRadioSection {...lastfm({ generating: true })} />);
    expect(container.querySelector('#lastfm-radio-input')).toBeDisabled();
  });

  it('says a radio is BUILDING while generating — a pick is not silence', () => {
    const { container } = render(<LastfmRadioSection {...lastfm({ generating: true })} />);
    const block = container.querySelector('.lastfm-radio-generating');
    expect(block).not.toBeNull();
    expect(block!.querySelector('.server-search-spinner')).not.toBeNull();
    expect(container.querySelector('.lastfm-radio-generating')).toHaveTextContent(
      'Building your radio',
    );
    // and never while idle
    cleanup();
    const idle = render(<LastfmRadioSection {...lastfm()} />).container;
    expect(idle.querySelector('.lastfm-radio-generating')).toBeNull();
  });

  it('a click OUTSIDE dismisses the open dropdown; inside does not', () => {
    const p = lastfm({
      dropdownOpen: true,
      results: [{ name: 'Xtal', artist: 'Aphex Twin' }],
      onDismiss: vi.fn(),
    });
    const { container } = render(<LastfmRadioSection {...p} />);
    fireEvent.pointerDown(container.querySelector('#lastfm-radio-input')!);
    expect(p.onDismiss).not.toHaveBeenCalled();
    fireEvent.pointerDown(document.body);
    expect(p.onDismiss).toHaveBeenCalledTimes(1);
    // dismiss keeps the query — it must NOT route through clear
    expect(p.onClear).not.toHaveBeenCalled();
  });

  it('with NO dismiss handler an outside click falls back to clear', () => {
    const p = lastfm({ dropdownOpen: true, results: [{ name: 'Xtal', artist: 'Aphex Twin' }] });
    render(<LastfmRadioSection {...p} />);
    fireEvent.pointerDown(document.body);
    expect(p.onClear).toHaveBeenCalledTimes(1);
  });

  it('a CLOSED dropdown listens for nothing outside', () => {
    const p = lastfm({ onDismiss: vi.fn() });
    render(<LastfmRadioSection {...p} />);
    fireEvent.pointerDown(document.body);
    expect(p.onDismiss).not.toHaveBeenCalled();
    expect(p.onClear).not.toHaveBeenCalled();
  });

  it('renders generated radios as mix cards', () => {
    const p = lastfm({ mixes: [mix('lastfm_1', 'Radio: Xtal')] });
    const { container } = render(<LastfmRadioSection {...p} />);
    fireEvent.click(container.querySelector('.mix-card-open')!);
    expect(p.onOpenMix).toHaveBeenCalledWith('lastfm_1');
  });
});

// ── ListenBrainz ─────────────────────────────────────────────────────────────

function lb(over: Partial<ListenBrainzSectionProps> = {}): ListenBrainzSectionProps {
  return {
    username: 'boulder',
    activeTab: 'recommendations',
    hasData: { recommendations: true, user: true, collaborative: false },
    mixes: [mix('lb_1', 'Weekly Jams')],
    loaded: true,
    onSelectTab: vi.fn(),
    onSelectGroup: vi.fn(),
    onRefresh: vi.fn(),
    onConnect: vi.fn(),
    onOpenMix: vi.fn(),
    ...over,
  };
}

describe('ListenBrainz', () => {
  it('names the user in the subtitle, and falls back without one', () => {
    const { rerender } = render(<ListenBrainzSection {...lb()} />);
    expect(screen.getByText('Playlists for boulder')).toBeInTheDocument();
    rerender(<ListenBrainzSection {...lb({ username: null })} />);
    expect(screen.getByText('Playlists from ListenBrainz')).toBeInTheDocument();
  });

  it('renders tabs in the DECADE styling, only for tabs that have data', () => {
    // The vanilla reuses .decade-tabs-inner/.decade-tab (3461-3471) and simply
    // does not render a dataless tab — no disabled placeholders.
    const { container } = render(<ListenBrainzSection {...lb()} />);
    expect(container.querySelector('.decade-tabs-inner')).not.toBeNull();
    const tabs = [...container.querySelectorAll('.decade-tab')];
    expect(tabs).toHaveLength(2);
    expect(tabs.map((t) => t.getAttribute('data-tab'))).toEqual(['recommendations', 'user']);
    expect(container.querySelector('[data-tab="recommendations"]')).toHaveClass('active');
  });

  it('selects a tab', () => {
    const p = lb();
    const { container } = render(<ListenBrainzSection {...p} />);
    fireEvent.click(container.querySelector('[data-tab="user"]')!);
    expect(p.onSelectTab).toHaveBeenCalledWith('user');
  });

  it('shows the FULL connect card when no tab has anything', () => {
    // Not a one-liner: the vanilla card says why, offers the settings button,
    // and points at where the token lives (3479-3489).
    const p = lb({
      hasData: { recommendations: false, user: false, collaborative: false },
      mixes: [],
    });
    const { container } = render(<ListenBrainzSection {...p} />);
    const card = container.querySelector('.lb-empty-state')!;
    expect(card.querySelector('.lb-empty-icon')!.textContent).toBe('🧠');
    expect(card.querySelector('h3')!.textContent).toBe('Connect ListenBrainz');
    fireEvent.click(card.querySelector('.lb-connect-btn')!);
    expect(p.onConnect).toHaveBeenCalled();
    expect(card.querySelector('.lb-empty-help a')).toHaveAttribute(
      'href',
      'https://listenbrainz.org/profile/',
    );
  });

  it('distinguishes a failed load from "not connected"', () => {
    const { container } = render(<ListenBrainzSection {...lb({ error: true, mixes: [] })} />);
    expect(container.querySelector('.discover-empty p')!.textContent).toBe(
      'Failed to load playlists',
    );
    expect(container.querySelector('.lb-empty-state')).toBeNull();
  });

  it('shows a spinner instead of tabs while loading, and no connect card', () => {
    const { container } = render(<ListenBrainzSection {...lb({ loading: true })} />);
    expect(container.querySelector('.loading-spinner')).not.toBeNull();
    expect(container.querySelector('.decade-tab')).toBeNull();
    expect(container.querySelector('.lb-empty-state')).toBeNull();
  });

  it('renders sub-tabs in the vanilla bar, only when given groups', () => {
    const { container, rerender } = render(<ListenBrainzSection {...lb()} />);
    expect(container.querySelector('#lb-subtabs-bar')).toBeNull();
    rerender(
      <ListenBrainzSection
        {...lb({
          groups: [
            { name: 'Daily Jams', count: 2 },
            { name: 'Weekly', count: 4 },
          ],
          activeGroup: 'Weekly',
        })}
      />,
    );
    // The bar carries the DECADE strip class + the tabs its decade styling
    // (3702, 3707) — a bare .lb-subtab is a JS hook with no stylesheet rule,
    // which is exactly how the first draft rendered an unstyled strip.
    expect(container.querySelector('#lb-subtabs-bar')).toHaveClass('decade-tabs-inner');
    const subs = [...container.querySelectorAll('#lb-subtabs-bar .decade-tab.lb-subtab')];
    expect(subs).toHaveLength(2);
    expect(subs.map((s) => s.getAttribute('data-group'))).toEqual(['Daily Jams', 'Weekly']);
    // The label carries the count (3710): "Weekly (4)", not a bare name.
    expect(subs.map((s) => s.textContent)).toEqual(['Daily Jams (2)', 'Weekly (4)']);
    expect(screen.getByText('Weekly (4)')).toHaveClass('active');
  });

  it('selects a group by its NAME, not its label', () => {
    const p = lb({
      groups: [
        { name: 'Daily Jams', count: 2 },
        { name: 'Weekly', count: 4 },
      ],
      activeGroup: 'Weekly',
    });
    render(<ListenBrainzSection {...p} />);
    fireEvent.click(screen.getByText('Daily Jams (2)'));
    expect(p.onSelectGroup).toHaveBeenCalledWith('Daily Jams');
  });

  it('renders playlists as mix cards in a PLAIN discover-grid', () => {
    // The vanilla wraps the cards in `.discover-grid` (3634) — no bespoke grid
    // id, which the first draft invented.
    const p = lb();
    const { container } = render(<ListenBrainzSection {...p} />);
    expect(container.querySelector('#listenbrainz-tab-content .discover-grid')).not.toBeNull();
    expect(container.querySelector('#listenbrainz-grid')).toBeNull();
    fireEvent.click(container.querySelector('.mix-card-open')!);
    expect(p.onOpenMix).toHaveBeenCalledWith('lb_1');
  });

  it('refreshes', () => {
    const p = lb();
    render(<ListenBrainzSection {...p} />);
    fireEvent.click(screen.getByTitle('Refresh playlists from ListenBrainz'));
    expect(p.onRefresh).toHaveBeenCalled();
  });
});
