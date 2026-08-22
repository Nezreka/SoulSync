/**
 * The Add-playlist sheet.
 *
 * The behaviour that matters is ROUTING: every link must land on the tab that
 * already owns its service, so nothing about the existing loaders changes. The
 * tab ids are asserted against the real SYNC_TABS table rather than against
 * strings typed here — routing someone to a tab that does not exist would be a
 * silent no-op.
 */

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SYNC_TABS } from '../-sync.shell';
import { ADD_PLAYLIST_ACCOUNTS, AddPlaylistSheet } from './add-playlist-sheet';

function renderSheet() {
  const onRoute = vi.fn();
  const onClose = vi.fn();
  const { unmount } = render(
    <AddPlaylistSheet anchor={{ top: 80, left: 400 }} onRoute={onRoute} onClose={onClose} />,
  );
  const input = screen.getByLabelText('Paste a link') as HTMLInputElement;
  const type = (value: string) => fireEvent.change(input, { target: { value } });
  const add = () => fireEvent.click(screen.getByRole('button', { name: 'Add' }));
  return { onRoute, onClose, input, type, add, unmount };
}

const TAB_IDS = new Set(SYNC_TABS.map((t) => t.id));

describe('routing a pasted link', () => {
  it('sends each service to the tab that already loads it', () => {
    const cases: [string, string][] = [
      ['https://open.spotify.com/playlist/abc', 'spotify-public'],
      ['https://music.apple.com/us/album/blonde/1146195596', 'itunes-link'],
      ['https://www.deezer.com/playlist/908622995', 'deezer-link'],
      ['https://www.youtube.com/playlist?list=PLabc', 'youtube'],
    ];
    for (const [url, tab] of cases) {
      const { onRoute, onClose, type, add, unmount } = renderSheet();
      type(url);
      add();
      expect(onRoute).toHaveBeenCalledWith(tab, url);
      expect(onClose).toHaveBeenCalled();
      // UNMOUNT, not .remove(): detaching the node leaves the popover's
      // outside-click and Escape listeners on `document`, and a stale listener
      // whose ref points at a detached node treats every later click as an
      // outside one.
      unmount();
    }
  });

  it('routes Deezer to `deezer-link`, NOT to the ARL account tab', () => {
    // The vertical is `deezer` but the paste-a-link TAB is `deezer-link`; the
    // account tab of the same service is a different list entirely.
    const { onRoute, type, add } = renderSheet();
    type('https://www.deezer.com/playlist/1');
    add();
    expect(onRoute).toHaveBeenCalledWith('deezer-link', expect.any(String));
    expect(onRoute).not.toHaveBeenCalledWith('deezer', expect.anything());
  });

  it('trims the pasted value, which is what routing and parsing both receive', () => {
    const { onRoute, type, add } = renderSheet();
    type('   https://open.spotify.com/playlist/abc   ');
    add();
    expect(onRoute).toHaveBeenCalledWith('spotify-public', 'https://open.spotify.com/playlist/abc');
  });

  it('Enter submits, so the sheet works without reaching for the mouse', () => {
    const { onRoute, input } = renderSheet();
    fireEvent.change(input, { target: { value: 'https://open.spotify.com/playlist/abc' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onRoute).toHaveBeenCalledWith('spotify-public', expect.any(String));
  });
});

describe('what it says while you type', () => {
  it('names the service as soon as the link is recognisable', () => {
    const { type } = renderSheet();
    type('https://www.deezer.com/playlist/1');
    expect(screen.getByText('Deezer recognised')).toBeInTheDocument();
  });

  it('stays quiet about errors until you actually submit', () => {
    // Telling someone their link is wrong halfway through pasting it is noise.
    const { type } = renderSheet();
    type('https://open.spot');
    expect(screen.queryByText(/not one we can read/)).toBeNull();
  });

  it('explains the problem on submit, and does NOT route', () => {
    const { onRoute, onClose, type, add } = renderSheet();
    type('https://tidal.com/browse/playlist/abc');
    add();
    expect(screen.getByText(/not one we can read/)).toBeInTheDocument();
    expect(onRoute).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('gives the Deezer share link its own actionable message', () => {
    const { onRoute, type, add } = renderSheet();
    type('https://link.deezer.com/s/30abcdef');
    add();
    expect(screen.getByText(/share link/)).toBeInTheDocument();
    expect(onRoute).not.toHaveBeenCalled();
  });

  it('clears the error once you start editing again', () => {
    const { type, add } = renderSheet();
    type('nonsense');
    add();
    expect(screen.getByText(/not one we can read/)).toBeInTheDocument();
    type('https://open.spotify.com/playlist/abc');
    expect(screen.queryByText(/not one we can read/)).toBeNull();
    expect(screen.getByText('Spotify recognised')).toBeInTheDocument();
  });
});

describe('the other two ways in', () => {
  it('every account shortcut names a REAL tab', () => {
    for (const account of ADD_PLAYLIST_ACCOUNTS) {
      expect(TAB_IDS).toContain(account.tab);
    }
  });

  it('an account shortcut opens that tab with no url to parse', () => {
    const { onRoute, onClose } = renderSheet();
    fireEvent.click(screen.getByRole('button', { name: /Tidal/ }));
    expect(onRoute).toHaveBeenCalledWith('tidal');
    expect(onClose).toHaveBeenCalled();
  });

  it('the file route opens the importer', () => {
    const { onRoute } = renderSheet();
    fireEvent.click(screen.getByRole('button', { name: /Import CSV/ }));
    expect(onRoute).toHaveBeenCalledWith('import-file');
    expect(TAB_IDS).toContain('import-file');
  });
});

describe('dismissal', () => {
  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
  afterEach(() => vi.useRealTimers());

  it('closes from the ×, from an outside click and from Escape — not from itself', () => {
    const onRoute = vi.fn();
    const onClose = vi.fn();
    const { container } = render(
      <AddPlaylistSheet anchor={{ top: 80, left: 400 }} onRoute={onRoute} onClose={onClose} />,
    );

    // A click inside must not dismiss it.
    fireEvent.click(screen.getByRole('dialog'));
    expect(onClose).not.toHaveBeenCalled();

    // The outside-click listener is attached on a timer, so the click that
    // opened the popover cannot immediately close it.
    act(() => {
      vi.advanceTimersByTime(1);
    });
    fireEvent.click(document.body);
    expect(onClose).toHaveBeenCalledTimes(1);

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(2);

    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(onClose).toHaveBeenCalledTimes(3);
    expect(container).toBeTruthy();
  });
});
