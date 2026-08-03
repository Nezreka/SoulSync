import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ReidentifyModal } from './reidentify-modal';

/**
 * The re-identify modal flow: active-source tabs auto-search on open,
 * ISRC-first results, confirm stages the re-file and toasts.
 */

afterEach(() => {
  vi.unstubAllGlobals();
  delete window.showToast;
  cleanup();
});

function mount() {
  const onClose = vi.fn();
  render(
    <ReidentifyModal
      trackId={9}
      trackTitle="Xtal"
      artistName="Aphex Twin"
      albumTitle="SAW 85-92"
      imageUrl="cover.jpg"
      onClose={onClose}
    />,
  );
  return { onClose };
}

describe('ReidentifyModal', () => {
  it('auto-searches the ACTIVE source and ranks ISRC hits first', async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
        const url = String(input);
        calls.push(url);
        if (url.endsWith('/sources')) {
          return new Response(
            JSON.stringify({
              sources: [
                { source: 'spotify', label: 'Spotify' },
                { source: 'deezer', label: 'Deezer', active: true },
              ],
            }),
          );
        }
        return new Response(
          JSON.stringify({
            results: [
              { track_title: 'Xtal', album_name: 'Compilation X', album_type: 'album' },
              { track_title: 'Xtal', album_name: 'Xtal Single', album_type: 'single', isrc: 'GB1' },
            ],
          }),
        );
      }),
    );
    mount();
    await screen.findByText('Deezer');
    // Active flag wins over declaration order.
    expect(document.querySelector('.reid-tab.active')?.textContent).toBe('Deezer');
    await screen.findByText('Xtal Single');
    expect(calls[1]).toContain('source=deezer');
    expect(calls[1]).toContain('q=Xtal%20Aphex%20Twin');
    const rows = [...document.querySelectorAll('.reid-result-release')];
    expect(rows[0].textContent).toContain('Xtal Single');
  });

  it('confirm is gated on a selection and stages through /apply', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith('/sources')) {
          return new Response(
            JSON.stringify({ sources: [{ source: 'spotify', label: 'Spotify' }] }),
          );
        }
        if (url.includes('/search')) {
          return new Response(
            JSON.stringify({
              results: [
                { track_title: 'Xtal', album_name: 'SAW', source: 'spotify', track_id: 't1' },
              ],
            }),
          );
        }
        const body = JSON.parse(String(init?.body));
        expect(body).toEqual({
          library_track_id: 9,
          source: 'spotify',
          track_id: 't1',
          replace: true,
        });
        return new Response(JSON.stringify({ success: true, album_name: 'SAW' }));
      }),
    );
    window.showToast = vi.fn() as never;
    const { onClose } = mount();
    await screen.findByText('SAW');
    const confirm = document.getElementById('reid-confirm-btn') as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);

    fireEvent.click(document.querySelector('.reid-result') as HTMLElement);
    expect(confirm.disabled).toBe(false);
    fireEvent.click(confirm);
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith(
        "Re-filing under “SAW” — it'll update after the next import pass.",
        'success',
      ),
    );
    expect(onClose).toHaveBeenCalled();
  });

  it('no configured sources → the empty state, no search', async () => {
    const spy = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Response(JSON.stringify({ sources: [] })),
    );
    vi.stubGlobal('fetch', spy);
    mount();
    await screen.findByText('No metadata sources available');
    expect(screen.getByText('No configured metadata source to search.')).toBeTruthy();
    expect(spy).toHaveBeenCalledTimes(1);
  });
});
