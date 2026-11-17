/**
 * What's new: the release from release-notes.ts, what is new since this
 * browser last looked, Show me only where it can go, and the full notes.
 */
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ViewProps } from './view-props';

import { RELEASES } from './release-notes';
import { useHelpStore } from './store';
import { useTourStore } from './tours/engine';
import { unmountTourLayer } from './tours/index';
import { WhatsNewView, resetSeenSnapshot, showMeAction } from './whats-new';

const SEEN = 'soulsync_helper_version_seen';
const PROPS: ViewProps = {
  entry: { view: 'whats-new' },
  query: '',
  isAdmin: true,
  page: 'dashboard',
};
const release = RELEASES[0];

function setBuild(version = '3.6.7') {
  useHelpStore.setState({
    update: {
      update_available: false,
      current_version: version,
      latest_version: version,
      severity: null,
      release_url: null,
      is_docker: true,
    } as never,
  });
}

beforeEach(() => {
  useHelpStore.setState(useHelpStore.getInitialState(), true);
  localStorage.removeItem(SEEN);
  resetSeenSnapshot(null);
  setBuild();
});

afterEach(() => {
  cleanup();
  unmountTourLayer();
  document.body.innerHTML = '';
  vi.restoreAllMocks();
  delete window.navigateToPage;
  delete window.SoulSyncWebShellBridge;
});

describe('the header and the list', () => {
  it('names the version, its month and headline, and calls every item new on a first look', () => {
    render(<WhatsNewView {...PROPS} />);
    expect(screen.getByText(`${release.version} · ${release.month}`)).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: release.headline })).toBeInTheDocument();
    expect(
      screen.getByText(
        `New since you last looked: ${release.items.length} things. The full notes are on the version button.`,
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'New for you' })).toBeInTheDocument();
    const items = document.querySelectorAll('li[data-new]');
    expect(items).toHaveLength(release.items.length);
    expect(screen.getByText(release.earlier!.desc)).toBeInTheDocument();
  });

  it('marks the version seen, and says so the next time', () => {
    const { unmount } = render(<WhatsNewView {...PROPS} />);
    expect(localStorage.getItem(SEEN)).toBe('3.6.7');
    unmount();
    render(<WhatsNewView {...PROPS} />);
    expect(screen.getByText(/You have seen everything in 3\.6\.7/)).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'In 3.6.7' })).toBeInTheDocument();
    expect(document.querySelectorAll('li[data-new]')).toHaveLength(0);
  });

  it('reads "since you last looked" from when the app loaded, not after Home marked it', () => {
    // the app loaded having seen 3.6.6; Home's tile then wrote 3.6.7 on the way in
    resetSeenSnapshot('3.6.6');
    localStorage.setItem(SEEN, '3.6.7');
    render(<WhatsNewView {...PROPS} />);
    expect(screen.getByRole('heading', { name: 'New for you' })).toBeInTheDocument();
  });

  it('falls back to the version button when the server version isn’t in yet', () => {
    useHelpStore.setState({ update: null });
    document.body.innerHTML = '<button class="version-button">v3.6.7</button>';
    render(<WhatsNewView {...PROPS} />);
    expect(screen.getByRole('heading', { name: release.headline })).toBeInTheDocument();
    expect(localStorage.getItem(SEEN)).toBe('3.6.7');
  });

  it('Full release notes closes the sheet and opens the version modal', () => {
    const versionBtn = document.createElement('button');
    versionBtn.className = 'version-button';
    const opened = vi.fn();
    versionBtn.addEventListener('click', opened);
    document.body.appendChild(versionBtn);
    useHelpStore.setState({ open: true });
    render(<WhatsNewView {...PROPS} />);
    fireEvent.click(screen.getByRole('button', { name: 'Full release notes' }));
    expect(opened).toHaveBeenCalled();
    expect(useHelpStore.getState().open).toBe(false);
  });
});

describe('Show me', () => {
  it('goes to an item’s page, keeping the sheet open beside it', () => {
    window.navigateToPage = vi.fn() as never;
    useHelpStore.setState({ open: true });
    render(<WhatsNewView {...PROPS} />);
    const item = screen.getByRole('heading', { name: 'Other versions' }).closest('li')!;
    fireEvent.click(within(item).getByRole('button', { name: 'Show me: Other versions' }));
    expect(window.navigateToPage).toHaveBeenCalledWith('library', undefined);
    expect(useHelpStore.getState().open).toBe(true);
  });

  it('is not offered for a page this profile can’t open, or an item with nowhere to go', () => {
    window.SoulSyncWebShellBridge = { isPageAllowed: (id: string) => id !== 'settings' } as never;
    render(<WhatsNewView {...PROPS} />);
    const settings = screen.getByRole('heading', { name: 'Settings, rebuilt' }).closest('li')!;
    expect(within(settings).queryByRole('button', { name: /Show me/ })).toBeNull();
    const bands = screen.getByRole('heading', { name: 'Band names stay whole' }).closest('li')!;
    expect(within(bands).queryByRole('button', { name: /Show me/ })).toBeNull();
  });

  it('spotlights an anchor that is on screen (Server Activity), and not one that isn’t', () => {
    const item = release.items.find((i) => i.anchor === 'shell.activity')!;
    expect(showMeAction(item)).toBeNull();

    const btn = document.createElement('button');
    btn.setAttribute('data-tour', 'shell.activity');
    document.body.appendChild(btn);
    vi.spyOn(btn, 'getBoundingClientRect').mockReturnValue({
      top: 900,
      left: 1300,
      width: 44,
      height: 44,
      right: 1344,
      bottom: 944,
      x: 1300,
      y: 900,
      toJSON: () => ({}),
    } as DOMRect);
    useHelpStore.setState({ open: true });
    const action = showMeAction(item)!;
    expect(action).not.toBeNull();
    act(() => action());
    expect(useHelpStore.getState().open).toBe(false);
    const state = useTourStore.getState();
    expect(state.adHoc).toBe(true);
    expect(state.tour?.steps[0]).toMatchObject({
      anchor: 'shell.activity',
      title: item.title,
      body: item.desc,
    });
  });

  it('switches to the video side for a video page', () => {
    document.body.setAttribute('data-side', 'music');
    const switchSide = vi.fn();
    window._switchAppSide = switchSide;
    const nav = document.createElement('a');
    nav.className = 'nav-button';
    nav.setAttribute('data-video-page', 'video-discover');
    const clicked = vi.fn();
    nav.addEventListener('click', clicked);
    document.body.appendChild(nav);
    const discover = release.items.find((i) => i.page === 'video-discover')!;
    showMeAction(discover)!();
    expect(switchSide).toHaveBeenCalledWith('video');
    expect(clicked).toHaveBeenCalled();
    delete window._switchAppSide;
    document.body.removeAttribute('data-side');
  });
});
