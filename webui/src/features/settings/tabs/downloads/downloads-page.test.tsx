/**
 * The Downloads page: its three pills stay in the page, and the address and
 * the sources model agree on the kind and on the open sheet.
 */

import { act, fireEvent, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { nav } from '../../nav';
import { createSettingsStore, type SettingsStore } from '../../settings-store';
import { type SourcesModel, createSourcesModel } from '../sources-model';
import { DownloadsPage } from './downloads-page';

let store: SettingsStore;
let model: SourcesModel;

beforeEach(() => {
  nav.reset();
  store = createSettingsStore({ post: () => Promise.resolve({ success: true }), delay: 60_000 });
  model = createSourcesModel({ store, autoTestDelay: 60_000 });
  vi.stubGlobal(
    'fetch',
    vi.fn(() =>
      Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ download_mode: 'torrent', success: true }),
      }),
    ),
  );
  model.load({ download_source: { mode: 'hybrid', hybrid_order: ['soulseek', 'youtube'] } });
});
afterEach(() => {
  vi.unstubAllGlobals();
  document.body.removeAttribute('data-side');
  document.body.classList.remove('src-modal-open');
});

const page = () => render(<DownloadsPage store={store} model={model} opened={1} active />);
const panel = (view: string) =>
  document.getElementById(`stg-view-downloads-${view}`) as HTMLElement;
const settle = () => act(() => new Promise((resolve) => setTimeout(resolve, 0)));

describe('the pills', () => {
  it('are all in the page, one on screen, and an edit survives a switch', async () => {
    act(() => nav.go('downloads', 'behaviour'));
    page();
    await settle();
    expect(panel('behaviour').hidden).toBe(false);
    expect(panel('sources').hidden).toBe(true);
    expect(panel('filtering').hidden).toBe(true);
    const timeout = document.getElementById('source-search-timeout') as HTMLInputElement;
    fireEvent.change(timeout, { target: { value: '33' } });
    act(() => nav.view('filtering'));
    expect(panel('filtering').hidden).toBe(false);
    // typed on a pill that is now hidden: still there, still waiting to save
    expect((document.getElementById('source-search-timeout') as HTMLInputElement).value).toBe('33');
    expect(store.getState().pending.download_source).toEqual({ source_search_timeout: 33 });
    act(() => nav.go('library'));
    expect(document.querySelector<HTMLElement>('[data-stg-new="downloads"]')!.hidden).toBe(true);
    act(() => nav.go('downloads', 'behaviour'));
    expect((document.getElementById('source-search-timeout') as HTMLInputElement).value).toBe('33');
  });

  it("Behaviour shows the side's kind, or the one the address names", async () => {
    act(() => nav.go('downloads', 'behaviour'));
    page();
    await settle();
    const music = document.getElementById('stg-behaviour-music') as HTMLElement;
    const video = document.getElementById('stg-behaviour-video') as HTMLElement;
    expect([music.hidden, video.hidden]).toEqual([false, true]);
    fireEvent.click(document.querySelector('#stg-behaviour-kind [data-value="video"]')!);
    expect(nav.get().kind).toBe('video');
    expect([music.hidden, video.hidden]).toEqual([true, false]);
    // the music rows are hidden, not gone
    expect(document.getElementById('retry-exhaustive')).toBeTruthy();
  });
});

describe('the address and the sources model', () => {
  it('a kind in the address is shown, even against the side', async () => {
    document.body.setAttribute('data-side', 'video');
    act(() => nav.fromAddress({ tab: 'downloads', view: 'sources', kind: 'music' }));
    page();
    await settle();
    expect(model.getState().kind).toBe('music');
    expect(model.getState().order).toEqual(['soulseek', 'youtube']);
  });

  it('picking a kind writes it to the address, in place', async () => {
    act(() => nav.go('downloads', 'sources'));
    page();
    await settle();
    const heard = vi.fn();
    const off = nav.subscribe(heard);
    fireEvent.click(document.querySelector('#dlchain-tabs [data-value="audiobooks"]')!);
    await settle();
    expect(nav.get().kind).toBe('audiobooks');
    expect(heard).toHaveBeenLastCalledWith(nav.get(), 'replace');
    off();
  });

  it('a sheet named in the address opens, and closing it clears the address', async () => {
    act(() => nav.fromAddress({ tab: 'downloads', view: 'sources', open: 'soulseek' }));
    page();
    await settle();
    expect(model.getState().open).toBe('soulseek');
    expect(document.getElementById('source-config-modal')!.hidden).toBe(false);
    act(() => model.closeModal());
    await settle();
    expect(nav.get().open).toBeNull();
  });

  it('a source opened from anywhere else lands on Downloads > Sources with its sheet', async () => {
    act(() => nav.go('library'));
    page();
    await settle();
    // window.openSourceModal('soulseek'), from a missing-source chip
    act(() => model.openModal('soulseek'));
    await settle();
    expect(nav.get()).toMatchObject({ tab: 'downloads', view: 'sources', open: 'soulseek' });
    expect(model.getState().open).toBe('soulseek');
  });

  it('the search going to the tab and opening the sheet in one go keeps the sheet', async () => {
    act(() => nav.go('connections'));
    page();
    await settle();
    // shell/search.tsx landHit: nav.go, then window.openSourceModal, one render
    act(() => {
      nav.go('downloads', 'sources');
      model.openModal('lidarr');
    });
    await settle();
    expect(model.getState().open).toBe('lidarr');
    expect(nav.get()).toMatchObject({ tab: 'downloads', view: 'sources', open: 'lidarr' });
    // and Back to no sheet still closes it
    act(() => nav.fromAddress({ tab: 'downloads', view: 'sources' }));
    await settle();
    expect(model.getState().open).toBeNull();
  });

  it('leaving the tab closes the sheet; a name that is no source is dropped', async () => {
    act(() => nav.fromAddress({ tab: 'downloads', view: 'sources', open: 'nonsense' }));
    page();
    await settle();
    expect(model.getState().open).toBeNull();
    expect(nav.get().open).toBeNull();
    act(() => model.openModal('youtube'));
    await settle();
    act(() => nav.go('quality'));
    await settle();
    expect(model.getState().open).toBeNull();
  });
});
