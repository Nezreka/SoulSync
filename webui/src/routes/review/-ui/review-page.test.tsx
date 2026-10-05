import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ReviewPage } from './review-page';

const fetchMock = vi.fn();

const finding = {
  id: 7,
  job_id: 'genre_cleanup',
  finding_type: 'genre_cleanup',
  severity: 'info',
  status: 'pending',
  title: 'Clean album genres',
  description: 'Remove one unwanted tag',
  file_path: '/music/Lamb of God/Ashes of the Wake/01.flac',
  details: {
    album_title: 'Ashes of the Wake',
    artist_name: 'Lamb of God',
    kept_genres: ['Metal'],
    removed_genres: ['Other'],
  },
  evidence_token: 'current-evidence',
};

function reply(body: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => body };
}

beforeEach(() => {
  window.history.replaceState(null, '', '/review');
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

describe('review queue', () => {
  it('selects the first case on the next queue page', async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url === '/api/repair/finding-types') return Promise.resolve(reply({ types: [] }));
      if (url.startsWith('/api/repair/review-queue?')) {
        const page = Number(new URL(url, 'http://localhost').searchParams.get('page'));
        const album = page === 0 ? 'Ashes of the Wake' : 'OK Computer';
        const caseId = page === 0 ? 'album:1:ashes' : 'album:1:ok';
        return Promise.resolve(
          reply({
            items: [
              {
                case_id: caseId,
                album,
                artist: page === 0 ? 'Lamb of God' : 'Radiohead',
                pending: 1,
                priority: 1,
                finding_types: ['genre_cleanup'],
                error_count: 0,
              },
            ],
            total: 2,
            page,
            limit: 1,
          }),
        );
      }
      if (url.endsWith('/album%3A1%3Aashes') || url.endsWith('/album%3A1%3Aok')) {
        const later = url.endsWith('/album%3A1%3Aok');
        return Promise.resolve(
          reply({
            case_id: later ? 'album:1:ok' : 'album:1:ashes',
            album: later ? 'OK Computer' : 'Ashes of the Wake',
            artist: later ? 'Radiohead' : 'Lamb of God',
            folder: null,
            count: 1,
            status: 'pending',
            items: [finding],
          }),
        );
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    render(<ReviewPage />);
    await screen.findByRole('heading', { name: 'Ashes of the Wake' });
    fireEvent.click(screen.getByRole('button', { name: /^Next$/ }));
    await screen.findByRole('heading', { name: 'OK Computer' });
    expect(window.location.search).toContain('album%3A1%3Aok');
  });

  it('queues the reconciliation scan from the review screen', async () => {
    fetchMock.mockImplementation((url: string, options?: RequestInit) => {
      if (url === '/api/repair/jobs/library_reconciliation/run') {
        expect(options?.method).toBe('POST');
        return Promise.resolve(reply({ success: true }));
      }
      if (url === '/api/repair/finding-types') return Promise.resolve(reply({ types: [] }));
      if (url.startsWith('/api/repair/review-queue?'))
        return Promise.resolve(reply({ items: [], total: 0, page: 0, limit: 30 }));
      throw new Error(`Unexpected request: ${url}`);
    });
    render(<ReviewPage />);
    fireEvent.click(screen.getByRole('button', { name: 'Scan library' }));
    await waitFor(() => expect(screen.getByRole('status').textContent).toContain('queued'));
  });

  it('shows evidence, applies the current finding, and advances to the next album', async () => {
    let applied = false;
    fetchMock.mockImplementation((url: string, options?: RequestInit) => {
      if (url === '/api/repair/finding-types')
        return Promise.resolve(
          reply({
            types: [
              {
                type: 'genre_cleanup',
                label: 'Genre Cleanup',
                verb: 'Apply Genres',
                fixable: true,
                destructive: false,
              },
            ],
          }),
        );
      if (url.startsWith('/api/repair/review-queue?'))
        return Promise.resolve(
          reply({
            items: applied
              ? [
                  {
                    case_id: 'album:1:next',
                    album: 'OK Computer',
                    artist: 'Radiohead',
                    pending: 1,
                    priority: 1,
                    finding_types: ['missing_lyrics'],
                    error_count: 0,
                  },
                ]
              : [
                  {
                    case_id: 'album:1:ashes',
                    album: 'Ashes of the Wake',
                    artist: 'Lamb of God',
                    pending: 1,
                    priority: 2,
                    finding_types: ['genre_cleanup'],
                    error_count: 0,
                  },
                  {
                    case_id: 'album:1:next',
                    album: 'OK Computer',
                    artist: 'Radiohead',
                    pending: 1,
                    priority: 1,
                    finding_types: ['missing_lyrics'],
                    error_count: 0,
                  },
                ],
            total: applied ? 1 : 2,
            page: 0,
            limit: 30,
          }),
        );
      if (url.endsWith('/album%3A1%3Aashes/findings/7/apply')) {
        const body = JSON.parse(options?.body as string) as Record<string, unknown>;
        expect(body).toMatchObject({ action: 'fix', evidence_token: 'current-evidence' });
        applied = true;
        return Promise.resolve(reply({ success: true, message: 'Genres applied' }));
      }
      if (url.endsWith('/album%3A1%3Aashes'))
        return Promise.resolve(
          applied
            ? reply({ error: 'Review case not found' }, 404)
            : reply({
                case_id: 'album:1:ashes',
                album: 'Ashes of the Wake',
                artist: 'Lamb of God',
                folder: '/music/Lamb of God/Ashes of the Wake',
                count: 1,
                status: 'pending',
                items: [finding],
              }),
        );
      if (url.endsWith('/album%3A1%3Anext'))
        return Promise.resolve(
          reply({
            case_id: 'album:1:next',
            album: 'OK Computer',
            artist: 'Radiohead',
            folder: '/music/Radiohead/OK Computer',
            count: 1,
            status: 'pending',
            items: [{ ...finding, id: 8, title: 'Missing lyrics', finding_type: 'missing_lyrics' }],
          }),
        );
      throw new Error(`Unexpected request: ${url}`);
    });
    render(<ReviewPage />);
    await screen.findByText('Remove one unwanted tag');
    expect(screen.getByText('Other')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Apply Genres' }));
    await screen.findByText('Genres applied');
    await waitFor(() => expect(screen.getByRole('heading', { name: 'OK Computer' })).toBeTruthy());
  });

  it('keeps a failed repair on the current decision and shows the error', async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url === '/api/repair/finding-types')
        return Promise.resolve(
          reply({
            types: [
              {
                type: 'genre_cleanup',
                label: 'Genre Cleanup',
                verb: 'Apply Genres',
                fixable: true,
                destructive: false,
              },
            ],
          }),
        );
      if (url.startsWith('/api/repair/review-queue?'))
        return Promise.resolve(
          reply({
            items: [
              {
                case_id: 'album:1:ashes',
                album: 'Ashes of the Wake',
                artist: 'Lamb of God',
                pending: 1,
                priority: 2,
                finding_types: ['genre_cleanup'],
                error_count: 0,
              },
            ],
            total: 1,
            page: 0,
            limit: 30,
          }),
        );
      if (url.endsWith('/findings/7/apply'))
        return Promise.resolve(
          reply(
            {
              success: false,
              error: 'File is locked',
            },
            400,
          ),
        );
      if (url.endsWith('/album%3A1%3Aashes'))
        return Promise.resolve(
          reply({
            case_id: 'album:1:ashes',
            album: 'Ashes of the Wake',
            artist: 'Lamb of God',
            folder: null,
            count: 1,
            status: 'pending',
            items: [finding],
          }),
        );
      throw new Error(`Unexpected request: ${url}`);
    });
    render(<ReviewPage />);
    await screen.findByText('Remove one unwanted tag');
    fireEvent.click(screen.getByRole('button', { name: 'Apply Genres' }));
    await screen.findByRole('alert');
    expect(screen.getByRole('alert').textContent).toContain('File is locked');
    expect(screen.getByText('Remove one unwanted tag')).toBeTruthy();
  });
});
