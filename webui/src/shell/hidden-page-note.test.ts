/**
 * #1909: a page this profile hid still opens from a link. It says so, with a
 * one-click way back, instead of leaving people wondering where it went.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  currentNavPage,
  forgetDismissed,
  hiddenOwner,
  NOTE_ID,
  refreshHiddenPageNote,
  watchHiddenPageNote,
} from './hidden-page-note';

const PAYLOAD = {
  features: [
    { id: 'podcasts', label: 'Podcasts', desc: '', group: 'more', pages: ['podcasts'] },
    { id: 'chat', label: 'Chat & Arcade', desc: '', group: 'more', pages: ['chat', 'video-chat'] },
  ],
  groups: [],
  presets: [],
  off: ['podcasts', 'chat'],
  preset: 'custom',
  hidden_pages: ['podcasts', 'chat', 'video-chat'],
};

function sidebar(active: string, videoActive = '') {
  document.body.innerHTML = `
    <div class="sidebar-scroll">
      <nav class="sidebar-nav">
        <a class="nav-button ${active === 'search' ? 'active' : ''}" data-page="search"></a>
        <a class="nav-button nav-feature-hidden ${active === 'podcasts' ? 'active' : ''}" data-page="podcasts"></a>
      </nav>
      <nav class="sidebar-nav video-nav">
        <a class="nav-button ${videoActive === 'video-chat' ? 'active' : ''}" data-video-page="video-chat"></a>
      </nav>
    </div>`;
}

const note = () => document.getElementById(NOTE_ID);

beforeEach(() => forgetDismissed());
afterEach(() => {
  document.body.innerHTML = '';
  document.body.removeAttribute('data-side');
});

describe('which page is open', () => {
  it('reads the active nav item on the side that is showing', () => {
    sidebar('podcasts', 'video-chat');
    expect(currentNavPage()).toBe('podcasts');
    document.body.setAttribute('data-side', 'video');
    expect(currentNavPage()).toBe('video-chat');
  });

  it('finds the switched-off feature that owns a page', () => {
    expect(hiddenOwner('video-chat', PAYLOAD)?.id).toBe('chat');
    expect(hiddenOwner('search', PAYLOAD)).toBeNull();
    expect(hiddenOwner('podcasts', { ...PAYLOAD, off: [] })).toBeNull();
    expect(hiddenOwner('podcasts', null)).toBeNull();
  });
});

describe('the note', () => {
  it('shows on a hidden page and not on a shown one', () => {
    sidebar('podcasts');
    refreshHiddenPageNote(PAYLOAD, vi.fn());
    expect(note()?.textContent).toContain('Podcasts is hidden from your sidebar.');
    sidebar('search');
    refreshHiddenPageNote(PAYLOAD, vi.fn());
    expect(note()).toBeNull();
  });

  it('Show it turns the feature back on', () => {
    sidebar('podcasts');
    const showIt = vi.fn(() => Promise.resolve());
    refreshHiddenPageNote(PAYLOAD, showIt);
    (note()!.querySelector('.hidden-page-note-show') as HTMLButtonElement).click();
    expect(showIt).toHaveBeenCalledWith('podcasts');
  });

  it('says so when turning it back on fails', async () => {
    sidebar('podcasts');
    refreshHiddenPageNote(PAYLOAD, () => Promise.reject(new Error('down')));
    const show = note()!.querySelector('.hidden-page-note-show') as HTMLButtonElement;
    show.click();
    await vi.waitFor(() => expect(note()!.textContent).toContain("Couldn't turn Podcasts back on"));
    expect(show.disabled).toBe(false);
  });

  it('stays closed on that page once dismissed', () => {
    sidebar('podcasts');
    refreshHiddenPageNote(PAYLOAD, vi.fn());
    (note()!.querySelector('.hidden-page-note-close') as HTMLButtonElement).click();
    expect(note()).toBeNull();
    refreshHiddenPageNote(PAYLOAD, vi.fn());
    expect(note()).toBeNull();
  });

  it('follows navigation through the active class', async () => {
    sidebar('search');
    const refresh = vi.fn(() => refreshHiddenPageNote(PAYLOAD, vi.fn()));
    const stop = watchHiddenPageNote(refresh);
    await vi.waitFor(() => expect(refresh).toHaveBeenCalled());
    expect(note()).toBeNull();
    // the shell moves `active` the way setActivePageChrome does
    document.querySelector('[data-page="search"]')!.classList.remove('active');
    document.querySelector('[data-page="podcasts"]')!.classList.add('active');
    await vi.waitFor(() => expect(note()).not.toBeNull());
    stop();
  });
});
