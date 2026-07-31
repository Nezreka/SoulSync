import { cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { EnhancedAlbum } from '../-artist-detail.enhanced';

import { AlbumMetaRow } from './album-meta-row';

const ALBUM: EnhancedAlbum = {
  id: 7,
  title: 'SAW 85-92',
  year: 1992,
  genres: ['ambient'],
  label: 'Apollo',
  record_type: 'album',
};

let body: unknown = null;

function stubPut(result: unknown = { success: true, updated_fields: ['label'] }) {
  body = null;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
      body = init?.body ? JSON.parse(String(init.body)) : null;
      return new Response(JSON.stringify(result), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }),
  );
}

function renderRow(album: EnhancedAlbum = ALBUM, isAdmin = true) {
  const onSaved = vi.fn();
  const view = render(<AlbumMetaRow album={album} isAdmin={isAdmin} onSaved={onSaved} />);
  return { onSaved, ...view };
}

const input = (field: string) =>
  document.querySelector(`[data-field="${field}"]`) as HTMLInputElement;

beforeEach(() => {
  window.showToast = vi.fn();
  stubPut();
});

afterEach(() => {
  vi.unstubAllGlobals();
  // NOT document.body.innerHTML = '': anything rendered through BodyPortal
  // lives there, and wiping the body out from under Testing Library's cleanup
  // makes it throw "The node to be removed is not a child of this node".
  cleanup();
});

describe('the read-only row', () => {
  it('shows values as text for a non-admin, with no inputs or Save', () => {
    renderRow(ALBUM, false);
    expect(document.querySelector('.enhanced-album-meta-input')).toBeNull();
    expect(document.querySelector('.enhanced-album-save-btn')).toBeNull();
    expect(document.querySelector('.enhanced-album-meta-value')?.textContent).toBe('SAW 85-92');
  });

  it('shows an em dash for an empty value rather than a blank gap', () => {
    renderRow({ id: 7 }, false);
    const values = [...document.querySelectorAll('.enhanced-album-meta-value')].map(
      (n) => n.textContent,
    );
    expect(values[0]).toBe('—');
  });
});

describe('the editable row', () => {
  it('seeds each input from the album', () => {
    renderRow();
    expect(input('title').value).toBe('SAW 85-92');
    expect(input('year').value).toBe('1992');
    expect(input('genres').value).toBe('ambient');
    expect(input('explicit').value).toBe('0');
  });

  it('does not let a click in a field collapse the album', () => {
    const onRowClick = vi.fn();
    render(
      <div onClick={onRowClick}>
        <AlbumMetaRow album={ALBUM} isAdmin onSaved={vi.fn()} />
      </div>,
    );
    fireEvent.click(input('title'));
    expect(onRowClick).not.toHaveBeenCalled();
  });

  it('PUTs only the changed fields', () => {
    renderRow();
    fireEvent.change(input('label'), { target: { value: 'Warp' } });
    fireEvent.click(document.querySelector('.enhanced-album-save-btn') as HTMLElement);
    // explicit:0 rides along on every save for a non-explicit album — see
    // albumMetaUpdates.
    expect(body).toEqual({ label: 'Warp', explicit: 0 });
  });

  it('applies the update so the row above it follows', async () => {
    const { onSaved } = renderRow();
    fireEvent.change(input('label'), { target: { value: 'Warp' } });
    fireEvent.click(document.querySelector('.enhanced-album-save-btn') as HTMLElement);

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith({ label: 'Warp', explicit: 0 }));
    expect(window.showToast).toHaveBeenCalledWith('Album metadata saved (label)', 'success');
  });

  it('refuses a malformed release date without sending anything', () => {
    renderRow();
    fireEvent.change(input('release_date'), { target: { value: '08/11/1992' } });
    fireEvent.click(document.querySelector('.enhanced-album-save-btn') as HTMLElement);

    expect(fetch).not.toHaveBeenCalled();
    expect(window.showToast).toHaveBeenCalledWith(
      'Release Date must be YYYY-MM-DD (or just YYYY)',
      'error',
    );
  });

  it('says so — as an ERROR — when nothing changed', () => {
    // Silence would read as a save that quietly failed. Only reachable for an
    // EXPLICIT album: otherwise explicit:0 always counts as a change.
    renderRow({ ...ALBUM, explicit: 1 });
    fireEvent.click(document.querySelector('.enhanced-album-save-btn') as HTMLElement);
    expect(fetch).not.toHaveBeenCalled();
    expect(window.showToast).toHaveBeenCalledWith('No album changes to save', 'error');
  });

  it('surfaces a rejected save instead of claiming success', async () => {
    stubPut({ success: false, error: 'locked' });
    const { onSaved } = renderRow();
    fireEvent.change(input('label'), { target: { value: 'Warp' } });
    fireEvent.click(document.querySelector('.enhanced-album-save-btn') as HTMLElement);

    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith('Failed to save: locked', 'error'),
    );
    expect(onSaved).not.toHaveBeenCalled();
  });

  it('reseeds the inputs when a refetch replaces the album', () => {
    const { rerender } = renderRow();
    fireEvent.change(input('label'), { target: { value: 'typing…' } });

    rerender(<AlbumMetaRow album={{ ...ALBUM, label: 'Rephlex' }} isAdmin onSaved={vi.fn()} />);
    // Stale edits must not survive a real refetch.
    expect(input('label').value).toBe('Rephlex');
  });
});
