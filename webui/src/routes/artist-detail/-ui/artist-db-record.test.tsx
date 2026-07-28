import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ArtistDbRecord } from './artist-db-record';

const RECORD = {
  success: true,
  artist_id: 42,
  counts: { albums: 3, tracks: 40 },
  record: {
    name: 'Aphex Twin',
    spotify_match_status: 'matched',
    deezer_artist_id: null,
    meta: { a: 1 },
  },
};

function stubRecord(body: unknown = RECORD) {
  vi.stubGlobal(
    'fetch',
    vi.fn(
      async () =>
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
    ),
  );
}

const openModal = async () => {
  fireEvent.click(document.getElementById('artist-db-record-btn') as HTMLElement);
  await screen.findByText('Aphex Twin', { selector: '.arec-sub' });
};

beforeEach(() => {
  window.showToast = vi.fn();
  stubRecord();
});

afterEach(() => {
  vi.unstubAllGlobals();
  // NOT document.body.innerHTML = '': these render through a portal, and
  // wiping the body out from under Testing Library's own cleanup makes it throw
  // "The node to be removed is not a child of this node". cleanup() unmounts
  // the tree, which takes the portal with it.
  cleanup();
});

describe('the DB Record button', () => {
  it('is there for a library artist', () => {
    render(<ArtistDbRecord artist={{ id: 42, name: 'Aphex Twin' }} isSourceArtist={false} />);
    expect(document.getElementById('artist-db-record-btn')).not.toBeNull();
  });

  it('is absent for a source artist and for an artist with no id', () => {
    const { rerender } = render(
      <ArtistDbRecord artist={{ id: 'sp1', name: 'X' }} isSourceArtist={true} />,
    );
    expect(document.getElementById('artist-db-record-btn')).toBeNull();
    rerender(<ArtistDbRecord artist={{ name: 'X' }} isSourceArtist={false} />);
    expect(document.getElementById('artist-db-record-btn')).toBeNull();
  });

  it('does not fetch the record until it is clicked', async () => {
    render(<ArtistDbRecord artist={{ id: 42, name: 'Aphex Twin' }} isSourceArtist={false} />);
    await new Promise((r) => setTimeout(r, 20));
    expect(fetch).not.toHaveBeenCalled();
  });
});

describe('the record modal', () => {
  beforeEach(() => {
    render(<ArtistDbRecord artist={{ id: 42, name: 'Aphex Twin' }} isSourceArtist={false} />);
  });

  it('lists every field, with empty values marked', async () => {
    await openModal();
    const rows = [...document.querySelectorAll('.arec-row')];
    expect(rows).toHaveLength(4);
    const empty = rows.find((r) => r.className.includes('is-empty'));
    expect(empty?.querySelector('.arec-key')?.textContent).toBe('deezer_artist_id');
    expect(empty?.querySelector('.arec-null')?.textContent).toBe('null');
    // Objects render as compact JSON.
    expect(document.querySelector('.arec-json')?.textContent).toBe('{"a":1}');
  });

  it('summarises the record in the footer', async () => {
    await openModal();
    const footer = document.getElementById('arec-footer') as HTMLElement;
    expect(footer.textContent).toContain('4 fields');
    expect(footer.textContent).toContain('3 albums');
    expect(footer.textContent).toContain('1 sources matched');
    expect(footer.textContent).toContain('id 42');
  });

  it('filters rows by field name, hiding rather than removing them', async () => {
    await openModal();
    fireEvent.change(document.getElementById('arec-filter') as HTMLElement, {
      target: { value: 'deezer' },
    });
    const rows = [...document.querySelectorAll('.arec-row')] as HTMLElement[];
    expect(rows).toHaveLength(4);
    expect(rows.filter((r) => r.style.display !== 'none')).toHaveLength(1);
  });

  it('switches to a highlighted JSON view and hides the filter box', async () => {
    await openModal();
    fireEvent.click(document.querySelector('[data-tab="json"]') as HTMLElement);

    expect(document.querySelector('.arec-code .tok-key')?.textContent).toBe('"name":');
    expect(document.querySelector('.arec-row')).toBeNull();
    // visibility, not display — the action buttons must not shift.
    expect((document.getElementById('arec-filter') as HTMLElement).style.visibility).toBe('hidden');
  });

  it('keeps the typed filter when coming back from the JSON tab', async () => {
    await openModal();
    fireEvent.change(document.getElementById('arec-filter') as HTMLElement, {
      target: { value: 'deezer' },
    });
    fireEvent.click(document.querySelector('[data-tab="json"]') as HTMLElement);
    fireEvent.click(document.querySelector('[data-tab="fields"]') as HTMLElement);

    const rows = [...document.querySelectorAll('.arec-row')] as HTMLElement[];
    expect(rows.filter((r) => r.style.display !== 'none')).toHaveLength(1);
  });

  it('shows an error instead of an empty record when the request fails', async () => {
    stubRecord({ success: false, error: 'no such artist' });
    fireEvent.click(document.getElementById('artist-db-record-btn') as HTMLElement);
    await screen.findByText('Could not load record: no such artist');
  });

  it('closes on the close button, on Escape, and on a backdrop click', async () => {
    await openModal();
    fireEvent.click(document.getElementById('arec-close') as HTMLElement);
    await waitFor(() => expect(document.getElementById('artist-record-overlay')).toBeNull());

    await openModal();
    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(document.getElementById('artist-record-overlay')).toBeNull());

    await openModal();
    fireEvent.click(document.getElementById('artist-record-overlay') as HTMLElement);
    await waitFor(() => expect(document.getElementById('artist-record-overlay')).toBeNull());
  });

  it('does NOT close on a click inside the card', async () => {
    await openModal();
    fireEvent.click(document.querySelector('.arec-card') as HTMLElement);
    expect(document.getElementById('artist-record-overlay')).not.toBeNull();
  });

  it('stops listening for Escape once closed', async () => {
    // Counted rather than observed through behaviour: a leaked handler still
    // closes the modal (same onClose identity), so only the listener balance
    // shows the leak — and it accumulates one per open across a session.
    const added = vi.spyOn(document, 'addEventListener');
    const removed = vi.spyOn(document, 'removeEventListener');
    const keydowns = (spy: typeof added) =>
      spy.mock.calls.filter(([type]) => type === 'keydown').length;

    await openModal();
    expect(keydowns(added)).toBe(1);

    fireEvent.click(document.getElementById('arec-close') as HTMLElement);
    await waitFor(() => expect(document.getElementById('artist-record-overlay')).toBeNull());
    expect(keydowns(removed)).toBe(1);

    added.mockRestore();
    removed.mockRestore();
  });

  it('copies the whole record as pretty JSON', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    vi.stubGlobal('isSecureContext', true);
    await openModal();

    fireEvent.click(document.getElementById('arec-copy') as HTMLElement);
    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith(JSON.stringify(RECORD.record, null, 2)),
    );
    expect(window.showToast).toHaveBeenCalledWith('Full record copied as JSON', 'success');
  });

  it('copies a single row value', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    vi.stubGlobal('isSecureContext', true);
    await openModal();

    fireEvent.click(document.querySelectorAll('.arec-rowcopy')[0]);
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('Aphex Twin'));
    expect(window.showToast).toHaveBeenCalledWith('Value copied', 'success');
  });

  it('reports the saved filename', async () => {
    vi.stubGlobal('URL', { createObjectURL: () => 'blob:x', revokeObjectURL: vi.fn() });
    await openModal();

    fireEvent.click(document.getElementById('arec-download') as HTMLElement);
    expect(window.showToast).toHaveBeenCalledWith('Saved Aphex_Twin_db_record.json', 'success');
  });
});
