/**
 * The tour engine and its layer: steps in order, the right page on the
 * right side, waits for slow pages, skips what isn't there (and says so
 * once), keys that don't leak to the player, and the ways in (the sheet's
 * Tour row, the event, window.SoulSyncTours).
 */
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { Tour } from './tours';

import { HomeView } from '../home';
import { useHelpStore } from '../store';
import {
  ANCHOR_WAIT_MS,
  DONE_KEY,
  OPTIONAL_WAIT_MS,
  doneTours,
  resetTourLog,
  startTour,
  useTourStore,
} from './engine';
import { START_TOUR_EVENT, installTours, unmountTourLayer } from './index';
import { TourLayer } from './layer';
import { GAP, MARGIN, holeFor, placeCard } from './place';
import { TOURS } from './tours';

// ── a page with anchors that have a size (jsdom lays nothing out) ───────

function anchor(id: string, tag = 'div'): HTMLElement {
  const el = document.createElement(tag);
  el.setAttribute('data-tour', id);
  el.setAttribute('data-size', '');
  el.textContent = id;
  document.body.appendChild(el);
  return el;
}

beforeEach(() => {
  vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
    const sized = this.hasAttribute('data-size');
    return {
      x: 100,
      y: 100,
      top: 100,
      left: 100,
      width: sized ? 200 : 0,
      height: sized ? 40 : 0,
      right: sized ? 300 : 100,
      bottom: sized ? 140 : 100,
      toJSON: () => ({}),
    } as DOMRect;
  });
  useHelpStore.setState(useHelpStore.getInitialState(), true);
  resetTourLog();
  localStorage.removeItem(DONE_KEY);
  document.body.removeAttribute('data-side');
  document.body.removeAttribute('data-video-page');
  window.history.replaceState(null, '', '/dashboard');
});

afterEach(() => {
  act(() => {
    if (useTourStore.getState().tour) useTourStore.setState({ tour: null, phase: 'idle' });
  });
  cleanup();
  unmountTourLayer();
  document.body.innerHTML = '';
  vi.useRealTimers();
  vi.restoreAllMocks();
  delete window.navigateToPage;
  delete window._switchAppSide;
  delete window.SoulSyncWebShellBridge;
  delete window.showToast;
});

const tour = (steps: Tour['steps'], id = 'test'): Tour => ({
  id,
  title: 'Test tour',
  detail: '',
  pages: [],
  steps,
});

const step = (anchorId: string, extra: Partial<Tour['steps'][number]> = {}) => ({
  anchor: anchorId as never,
  title: `Title ${anchorId}`,
  body: `Body ${anchorId}`,
  ...extra,
});

async function settle(ms = 0) {
  await act(async () => {
    if (vi.isFakeTimers()) await vi.advanceTimersByTimeAsync(ms);
    else await new Promise((r) => setTimeout(r, ms));
  });
}

describe('walking a tour', () => {
  it('shows each step on its anchor, with Next, Back and Done, and remembers it was done', async () => {
    anchor('shell.nav');
    anchor('shell.help');
    render(<TourLayer />);
    act(() => void startTour(tour([step('shell.nav'), step('shell.help')], 'walk')));
    await settle();

    expect(await screen.findByRole('dialog', { name: 'Title shell.nav' })).toBeInTheDocument();
    expect(screen.getByText('1 of 2')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Back' })).toBeNull();
    expect(document.activeElement).toHaveTextContent('Next');

    fireEvent.click(screen.getByRole('button', { name: /Next/ }));
    expect(await screen.findByRole('dialog', { name: 'Title shell.help' })).toBeInTheDocument();
    expect(screen.getByText('2 of 2')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Back' }));
    expect(await screen.findByRole('dialog', { name: 'Title shell.nav' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Next/ }));
    await screen.findByRole('dialog', { name: 'Title shell.help' });
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(doneTours()).toEqual(['walk']);
  });

  it('counts only the steps that are there: a missing optional one is left out of "1 of 2"', async () => {
    anchor('shell.nav');
    anchor('shell.help');
    render(<TourLayer />);
    act(
      () =>
        void startTour(
          tour([step('shell.menu', { optional: true }), step('shell.nav'), step('shell.help')]),
        ),
    );
    await screen.findByRole('dialog', { name: 'Title shell.nav' });
    expect(screen.getByText('1 of 2')).toBeInTheDocument();
    // nothing on screen before it, so no Back
    expect(screen.queryByRole('button', { name: 'Back' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /Next/ }));
    await screen.findByRole('dialog', { name: 'Title shell.help' });
    expect(screen.getByText('2 of 2')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Done' })).toBeInTheDocument();
  });

  it('draws a hole over the target, framed by four panels, and a ring on it', async () => {
    anchor('shell.nav');
    const { container } = render(<TourLayer />);
    act(() => void startTour(tour([step('shell.nav')])));
    await screen.findByRole('dialog', { name: 'Title shell.nav' });
    await settle(30);
    const panels = container.querySelectorAll('[aria-hidden="true"]');
    // four panels and the ring
    expect(panels.length).toBeGreaterThanOrEqual(5);
  });

  it('skips a step whose anchor never shows, and logs it once', async () => {
    vi.useFakeTimers();
    const info = vi.spyOn(console, 'info').mockImplementation(() => {});
    anchor('shell.help');
    render(<TourLayer />);
    act(() => void startTour(tour([step('shell.nav'), step('shell.help')], 'skip')));
    await settle(ANCHOR_WAIT_MS + 200);
    expect(screen.getByRole('dialog', { name: 'Title shell.help' })).toBeInTheDocument();
    expect(info).toHaveBeenCalledWith('[tour] skip: skipped shell.nav (not on screen)');

    // a second run doesn't log it again
    act(() => void startTour(tour([step('shell.nav'), step('shell.help')], 'skip')));
    await settle(ANCHOR_WAIT_MS + 200);
    expect(info).toHaveBeenCalledTimes(1);
  });

  it('waits for an anchor a slow page adds later', async () => {
    vi.useFakeTimers();
    render(<TourLayer />);
    // on its page already: the page's own content can come late
    act(() => void startTour(tour([step('search.field', { page: 'dashboard' })])));
    await settle(1500);
    expect(screen.queryByRole('dialog', { name: 'Title search.field' })).toBeNull();
    anchor('search.field');
    await settle(200);
    expect(screen.getByRole('dialog', { name: 'Title search.field' })).toBeInTheDocument();
  });

  it('gives an optional step only a moment, quietly', async () => {
    vi.useFakeTimers();
    const info = vi.spyOn(console, 'info').mockImplementation(() => {});
    anchor('shell.help');
    render(<TourLayer />);
    act(
      () => void startTour(tour([step('shell.profile', { optional: true }), step('shell.help')])),
    );
    await settle(OPTIONAL_WAIT_MS + 200);
    expect(screen.getByRole('dialog', { name: 'Title shell.help' })).toBeInTheDocument();
    expect(info).not.toHaveBeenCalled();
  });

  it('says so when nothing in the tour is on screen', async () => {
    vi.useFakeTimers();
    vi.spyOn(console, 'info').mockImplementation(() => {});
    const toast = vi.fn();
    window.showToast = toast;
    render(<TourLayer />);
    act(() => void startTour(tour([step('shell.nav')], 'empty')));
    await settle(ANCHOR_WAIT_MS + 200);
    expect(useTourStore.getState().tour).toBeNull();
    expect(toast).toHaveBeenCalledWith(
      'Nothing in that tour is on screen for you right now.',
      'info',
    );
    expect(doneTours()).toEqual([]);
  });
});

describe('getting to the page', () => {
  it('goes to a music page first and waits for it', async () => {
    window.navigateToPage = vi.fn(async (page: string) => {
      setTimeout(() => {
        window.history.replaceState(null, '', `/${page}`);
        anchor('search.field');
      }, 300);
    }) as never;
    render(<TourLayer />);
    act(() => void startTour(tour([step('search.field', { page: 'search' })])));
    expect(
      await screen.findByRole('dialog', { name: 'Title search.field' }, { timeout: 3000 }),
    ).toBeInTheDocument();
    expect(window.navigateToPage).toHaveBeenCalledWith('search', undefined);
  });

  it('switches to the video side for a video page, then opens it from the video nav', async () => {
    document.body.setAttribute('data-side', 'music');
    const nav = document.createElement('a');
    nav.className = 'nav-button';
    nav.setAttribute('data-video-page', 'video-requests');
    nav.addEventListener('click', () => {
      document.body.setAttribute('data-video-page', 'video-requests');
      anchor('video.requests');
    });
    document.body.appendChild(nav);
    window._switchAppSide = vi.fn((side) => document.body.setAttribute('data-side', side));
    render(<TourLayer />);
    act(() => void startTour(tour([step('video.requests', { page: 'video-requests' })])));
    expect(await screen.findByRole('dialog', { name: 'Title video.requests' })).toBeInTheDocument();
    expect(window._switchAppSide).toHaveBeenCalledWith('video');
  });

  it('skips a page this profile may not open, without going there', async () => {
    vi.useFakeTimers();
    vi.spyOn(console, 'info').mockImplementation(() => {});
    window.navigateToPage = vi.fn() as never;
    window.SoulSyncWebShellBridge = { isPageAllowed: (id: string) => id !== 'settings' } as never;
    anchor('shell.help');
    render(<TourLayer />);
    act(() => void startTour(tour([step('shell.nav', { page: 'settings' }), step('shell.help')])));
    await settle(200);
    expect(screen.getByRole('dialog', { name: 'Title shell.help' })).toBeInTheDocument();
    expect(window.navigateToPage).not.toHaveBeenCalled();
  });

  it('asks for an open window to close first, then carries on', async () => {
    const modal = document.createElement('div');
    modal.className = 'download-missing-modal';
    modal.setAttribute('data-size', '');
    document.body.appendChild(modal);
    anchor('downloads.header');
    render(<TourLayer />);
    act(
      () =>
        void startTour(
          tour([
            step('downloads.header', {
              waitUntilGone: {
                selector: '.download-missing-modal',
                say: 'Close the download window first.',
              },
            }),
          ]),
        ),
    );
    expect(await screen.findByText('Close the download window first.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Skip this step' })).toBeInTheDocument();
    modal.remove();
    expect(
      await screen.findByRole('dialog', { name: 'Title downloads.header' }),
    ).toBeInTheDocument();
  });

  it('moves on by itself when the user does the thing', async () => {
    anchor('search.results');
    render(<TourLayer />);
    act(
      () =>
        void startTour(
          tour([
            step('search.results', { advanceOn: 'download.window' }),
            step('download.window', { optional: true }),
          ]),
        ),
    );
    await screen.findByRole('dialog', { name: 'Title search.results' });
    expect(screen.getByText('Waiting for you')).toBeInTheDocument();
    anchor('download.window');
    expect(
      await screen.findByRole('dialog', { name: 'Title download.window' }),
    ).toBeInTheDocument();
  });
});

describe('keys', () => {
  it('→ and ← move the tour and the player underneath never hears them', async () => {
    anchor('shell.nav');
    anchor('shell.help');
    const player = vi.fn();
    document.addEventListener('keydown', player);
    render(<TourLayer />);
    act(() => void startTour(tour([step('shell.nav'), step('shell.help')])));
    await screen.findByRole('dialog', { name: 'Title shell.nav' });

    fireEvent.keyDown(document.body, { key: 'ArrowRight' });
    expect(await screen.findByRole('dialog', { name: 'Title shell.help' })).toBeInTheDocument();
    fireEvent.keyDown(document.body, { key: 'ArrowLeft' });
    expect(await screen.findByRole('dialog', { name: 'Title shell.nav' })).toBeInTheDocument();
    expect(player).not.toHaveBeenCalled();
    document.removeEventListener('keydown', player);
  });

  it('leaves arrows alone while typing in a field the tour points at', async () => {
    const field = anchor('search.field', 'input');
    anchor('shell.help');
    render(<TourLayer />);
    act(() => void startTour(tour([step('search.field'), step('shell.help')])));
    await screen.findByRole('dialog', { name: 'Title search.field' });
    fireEvent.keyDown(field, { key: 'ArrowRight' });
    await settle(50);
    expect(screen.getByRole('dialog', { name: 'Title search.field' })).toBeInTheDocument();
  });

  it('Esc ends it and focus goes back where it was', async () => {
    const before = document.createElement('button');
    before.textContent = 'before';
    document.body.appendChild(before);
    before.focus();
    anchor('shell.nav');
    render(<TourLayer />);
    act(() => void startTour(tour([step('shell.nav')])));
    await screen.findByRole('dialog', { name: 'Title shell.nav' });
    fireEvent.keyDown(document.body, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.activeElement).toBe(before);
    expect(doneTours()).toEqual([]);
  });
});

describe('ways in', () => {
  it('the sheet’s Tour row closes the sheet and starts the tour', async () => {
    installTours();
    anchor('search.field');
    window.history.replaceState(null, '', '/search');
    useHelpStore.setState({ open: true });
    render(<HomeView entry={{ view: 'home' }} query="" isAdmin={false} page="search" />);
    const row = screen.getByRole('button', { name: /Tour: Your first download/ });
    act(() => {
      fireEvent.click(row);
    });
    expect(useHelpStore.getState().open).toBe(false);
    expect(useTourStore.getState().tour?.id).toBe('first-download');
    expect(document.getElementById('soulsync-tour-root')).not.toBeNull();
  });

  it('ss:help-start-tour and window.SoulSyncTours start a tour by id', () => {
    installTours();
    anchor('shell.help');
    act(
      () =>
        void window.dispatchEvent(
          new CustomEvent(START_TOUR_EVENT, { detail: { id: 'getting-around' } }),
        ),
    );
    expect(useTourStore.getState().tour?.id).toBe('getting-around');
    act(() => window.SoulSyncTours!.end());
    expect(window.SoulSyncTours!.list().map((t) => t.id)).toEqual(TOURS.map((t) => t.id));
    act(() => void window.SoulSyncTours!.start('first-video-request'));
    expect(useTourStore.getState().tour?.id).toBe('first-video-request');
    expect(window.SoulSyncTours!.start('nope')).toBe(false);
  });

  it('offers each tour on the pages it is about', () => {
    installTours();
    for (const t of TOURS) {
      for (const page of t.pages) {
        cleanup();
        render(<HomeView entry={{ view: 'home' }} query="" isAdmin page={page} />);
        expect(
          screen.getByRole('button', { name: new RegExp(`Tour: ${t.title}`) }),
        ).toBeInTheDocument();
      }
    }
  });
});

describe('where the card goes', () => {
  const view = { width: 1440, height: 900 };
  const card = { width: 340, height: 160 };

  it('below a target with room under it, centred on it', () => {
    const p = placeCard({ top: 100, left: 600, width: 200, height: 40 }, card, view, false);
    expect(p.side).toBe('below');
    expect(p.top).toBe(140 + GAP);
    expect(p.left).toBe(700 - 170);
  });

  it('above a target at the bottom (the ? button), kept inside the window', () => {
    const p = placeCard({ top: 840, left: 1380, width: 44, height: 44 }, card, view, false);
    expect(p.side).toBe('above');
    expect(p.left).toBe(view.width - card.width - MARGIN);
  });

  it('beside a tall target (the sidebar)', () => {
    const p = placeCard({ top: 0, left: 0, width: 240, height: 900 }, card, view, false);
    expect(p.side).toBe('right');
    expect(p.left).toBe(240 + GAP);
  });

  it('docks on a phone, to the top when the target sits low', () => {
    const phone = { width: 390, height: 844 };
    expect(placeCard({ top: 100, left: 0, width: 300, height: 40 }, card, phone, true).side).toBe(
      'dock-bottom',
    );
    expect(placeCard({ top: 780, left: 330, width: 44, height: 44 }, card, phone, true).side).toBe(
      'dock-top',
    );
  });

  it('the hole never leaves the window', () => {
    expect(holeFor({ top: -20, left: -5, width: 100, height: 60 }, view)).toEqual({
      top: 0,
      left: 0,
      width: 101,
      height: 46,
    });
  });
});
