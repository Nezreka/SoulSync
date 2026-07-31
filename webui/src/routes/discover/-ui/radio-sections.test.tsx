import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { LastfmRadioSectionProps, ListenBrainzSectionProps } from './radio-sections';

import { LastfmRadioSection, ListenBrainzSection } from './radio-sections';

/**
 * The Last.fm Radio and ListenBrainz sections.
 *
 * Neither owns a card — both render mix cards. What they own is their controls,
 * and the case that matters most is ListenBrainz's two DIFFERENT empty states:
 * "this category is empty" and "you have not connected an account" are
 * different problems, and one message for both sends the user to the wrong fix.
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
  it('renders its search box even with no radios yet', () => {
    // This section IS its own search UI; hiding it when empty would leave no
    // way to make the first one.
    const { container } = render(<LastfmRadioSection {...lastfm()} />);
    expect(container.querySelector('#lastfm-radio-input')).not.toBeNull();
    expect(container.querySelector('#lastfm-radio-playlists')).not.toBeNull();
  });

  it('keeps the dropdown closed until it is told to open', () => {
    const { container } = render(
      <LastfmRadioSection {...lastfm({ results: [{ name: 'Xtal', artist: 'Aphex Twin' }] })} />,
    );
    expect(container.querySelector('#lastfm-radio-dropdown')).toBeNull();
  });

  it('lists results with artist and listener counts', () => {
    render(
      <LastfmRadioSection
        {...lastfm({
          dropdownOpen: true,
          results: [{ name: 'Xtal', artist: 'Aphex Twin', listeners: 120_000 }],
        })}
      />,
    );
    expect(screen.getByText('Xtal')).toBeInTheDocument();
    expect(screen.getByText('Aphex Twin · 120,000 listeners')).toBeInTheDocument();
  });

  it('drops the listener line entirely at zero', () => {
    // "0 listeners" reads as a judgement of the track.
    const { container } = render(
      <LastfmRadioSection
        {...lastfm({
          dropdownOpen: true,
          results: [{ name: 'Xtal', artist: 'Aphex Twin', listeners: 0 }],
        })}
      />,
    );
    expect(container.querySelector('.lastfm-result-listeners')).toBeNull();
    expect(container.querySelector('.lastfm-result-sub')!.textContent).toBe('Aphex Twin');
  });

  it('reports typing, picking and Escape', () => {
    const p = lastfm({ dropdownOpen: true, results: [{ name: 'Xtal', artist: 'Aphex Twin' }] });
    const { container } = render(<LastfmRadioSection {...p} />);
    const input = container.querySelector('#lastfm-radio-input')!;
    fireEvent.change(input, { target: { value: 'xtal' } });
    expect(p.onQueryChange).toHaveBeenCalledWith('xtal');

    fireEvent.click(screen.getByText('Xtal'));
    expect(p.onPick).toHaveBeenCalledWith({ name: 'Xtal', artist: 'Aphex Twin' });

    fireEvent.keyDown(input, { key: 'Escape' });
    expect(p.onClear).toHaveBeenCalled();
  });

  it('does not clear on other keys', () => {
    const p = lastfm();
    const { container } = render(<LastfmRadioSection {...p} />);
    fireEvent.keyDown(container.querySelector('#lastfm-radio-input')!, { key: 'a' });
    expect(p.onClear).not.toHaveBeenCalled();
  });

  it('locks the input while a radio is being generated', () => {
    const { container } = render(<LastfmRadioSection {...lastfm({ generating: true })} />);
    expect(container.querySelector('#lastfm-radio-input')).toBeDisabled();
  });

  it('renders generated radios as mix cards', () => {
    const p = lastfm({ mixes: [mix('lastfm_1', 'Radio: Xtal')] });
    const { container } = render(<LastfmRadioSection {...p} />);
    fireEvent.click(container.querySelector('.discover-mix-card')!);
    expect(screen.getByText('Radio: Xtal')).toBeInTheDocument();
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

  it('renders all three tabs, marking the active one', () => {
    const { container } = render(<ListenBrainzSection {...lb()} />);
    const tabs = [...container.querySelectorAll('.listenbrainz-tab')];
    expect(tabs).toHaveLength(3);
    expect(tabs.filter((t) => t.classList.contains('active'))).toHaveLength(1);
    expect(container.querySelector('[data-lb-tab="recommendations"]')).toHaveClass('active');
  });

  it('shows a tab with no data but does not let you select it', () => {
    // Hiding it would make the tab set jump around between refreshes.
    const p = lb();
    const { container } = render(<ListenBrainzSection {...p} />);
    const dead = container.querySelector('[data-lb-tab="collaborative"]') as HTMLButtonElement;
    expect(dead).toBeInTheDocument();
    expect(dead.disabled).toBe(true);
    fireEvent.click(dead);
    expect(p.onSelectTab).not.toHaveBeenCalled();
  });

  it('selects a tab that does have data', () => {
    const p = lb();
    const { container } = render(<ListenBrainzSection {...p} />);
    fireEvent.click(container.querySelector('[data-lb-tab="user"]')!);
    expect(p.onSelectTab).toHaveBeenCalledWith('user');
  });

  it('asks the user to CONNECT when no tab has anything', () => {
    render(
      <ListenBrainzSection
        {...lb({
          hasData: { recommendations: false, user: false, collaborative: false },
          mixes: [],
        })}
      />,
    );
    expect(screen.getByText('Connect ListenBrainz')).toBeInTheDocument();
    expect(screen.queryByText('No playlists in this category')).toBeNull();
  });

  it('says the CATEGORY is empty when other tabs do have data', () => {
    // The same message for both would send a connected user off to reconnect.
    render(<ListenBrainzSection {...lb({ mixes: [] })} />);
    expect(screen.getByText('No playlists in this category')).toBeInTheDocument();
    expect(screen.queryByText('Connect ListenBrainz')).toBeNull();
  });

  it('shows a spinner instead of tabs while loading', () => {
    const { container } = render(<ListenBrainzSection {...lb({ loading: true })} />);
    expect(container.querySelector('.loading-spinner')).not.toBeNull();
    expect(container.querySelectorAll('.listenbrainz-tab')).toHaveLength(0);
    // And no "connect" prompt mid-load — it has not answered yet.
    expect(screen.queryByText('Connect ListenBrainz')).toBeNull();
  });

  it('renders sub-tabs only when it was given groups', () => {
    const { container, rerender } = render(<ListenBrainzSection {...lb()} />);
    expect(container.querySelector('.listenbrainz-sub-tabs')).toBeNull();

    rerender(
      <ListenBrainzSection {...lb({ groups: ['Daily Jams', 'Weekly'], activeGroup: 'Weekly' })} />,
    );
    const subs = [...container.querySelectorAll('.listenbrainz-sub-tab-btn')];
    expect(subs).toHaveLength(2);
    expect(subs.filter((s) => s.classList.contains('active'))).toHaveLength(1);
    expect(screen.getByText('Weekly')).toHaveClass('active');
  });

  it('selects a group', () => {
    const p = lb({ groups: ['Daily Jams', 'Weekly'], activeGroup: 'Weekly' });
    render(<ListenBrainzSection {...p} />);
    fireEvent.click(screen.getByText('Daily Jams'));
    expect(p.onSelectGroup).toHaveBeenCalledWith('Daily Jams');
  });

  it('renders playlists as mix cards and opens them by key', () => {
    const p = lb();
    const { container } = render(<ListenBrainzSection {...p} />);
    fireEvent.click(container.querySelector('.discover-mix-card')!);
    expect(p.onOpenMix).toHaveBeenCalledWith('lb_1');
  });

  it('refreshes', () => {
    const p = lb();
    render(<ListenBrainzSection {...p} />);
    fireEvent.click(screen.getByTitle('Refresh playlists from ListenBrainz'));
    expect(p.onRefresh).toHaveBeenCalled();
  });
});
