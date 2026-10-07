import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';

import {
  fetchDownloads,
  grabRelease,
  pollReleaseSearch,
  startReleaseSearch,
} from '../-audiobooks.api';
import { AudiobookReleasesModal } from './audiobook-releases-modal';

vi.mock('../-audiobooks.api', () => ({
  startReleaseSearch: vi.fn(),
  pollReleaseSearch: vi.fn(),
  cancelReleaseSearch: vi.fn(),
  fetchDownloads: vi.fn(),
  fetchReleaseContents: vi.fn(),
  grabRelease: vi.fn(),
  blockRelease: vi.fn(),
  addToWishlist: vi.fn(),
}));
vi.mock('./audiobook-overlay', () => ({
  AudiobookOverlay: ({ children }: { children: React.ReactNode }) => (
    <div role="dialog">{children}</div>
  ),
}));
vi.mock('@tanstack/react-router', () => ({
  Link: ({ children }: { children: React.ReactNode }) => <a href="#">{children}</a>,
}));

beforeEach(() => {
  vi.mocked(startReleaseSearch).mockReset();
  vi.mocked(startReleaseSearch).mockResolvedValue({
    id: 'job',
    pollMs: 1,
    defaultQuery: 'Ed Greenwood The Reckoning',
  });
  vi.mocked(pollReleaseSearch).mockResolvedValue({
    releases: [],
    stage: '',
    complete: true,
    error: '',
    expired: false,
  });
});

const box = () => screen.getByRole('searchbox', { name: 'Search query' }) as HTMLInputElement;

it('starts the box with the automatic query and reruns with what the user typed', async () => {
  render(<AudiobookReleasesModal asin="B1" title="The Reckoning" onClose={() => {}} />);
  await waitFor(() => expect(box().value).toBe('Ed Greenwood The Reckoning'));
  expect(startReleaseSearch).toHaveBeenLastCalledWith('B1', '');

  fireEvent.change(box(), { target: { value: 'The Reckoning Part 1 of 2 GraphicAudio' } });
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  await waitFor(() =>
    expect(startReleaseSearch).toHaveBeenLastCalledWith(
      'B1',
      'The Reckoning Part 1 of 2 GraphicAudio',
    ),
  );
  expect(screen.getByText(/Searching for exactly what you typed/)).toBeTruthy();

  fireEvent.click(screen.getByRole('button', { name: 'Reset' }));
  await waitFor(() => expect(startReleaseSearch).toHaveBeenLastCalledWith('B1', ''));
  expect(box().value).toBe('Ed Greenwood The Reckoning');
});

it('searching the untouched default runs the full automatic search, not a narrower copy', async () => {
  render(<AudiobookReleasesModal asin="B1" title="The Reckoning" onClose={() => {}} />);
  await waitFor(() => expect(box().value).toBe('Ed Greenwood The Reckoning'));
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  await waitFor(() => expect(startReleaseSearch).toHaveBeenCalledTimes(2));
  expect(startReleaseSearch).toHaveBeenLastCalledWith('B1', '');
});

it('says so when the client already had the torrent', async () => {
  vi.mocked(pollReleaseSearch).mockResolvedValue({
    releases: [
      {
        title: 'The Reckoning M4B',
        guid: 'g1',
        protocol: 'torrent',
        indexer: 'MAM',
        size_bytes: 1,
        reasons: [],
      } as never,
    ],
    stage: '',
    complete: true,
    error: '',
    expired: false,
  });
  vi.mocked(grabRelease).mockResolvedValue({ ok: true, error: '', ref: 'h', adopted: true });
  vi.mocked(fetchDownloads).mockResolvedValue([]);
  render(<AudiobookReleasesModal asin="B1" title="The Reckoning" onClose={() => {}} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Download' }));
  expect(await screen.findByText(/Already in your download client/)).toBeTruthy();
});
