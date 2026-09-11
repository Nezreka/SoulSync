import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  deleteLibraryBook,
  fetchLibrary,
  scanLibrary,
  fetchLibraryMatches,
  saveLibraryMatch,
} from '../-audiobooks.api';
import { AudiobookLibraryModal } from './audiobook-library-modal';

vi.mock('../-audiobooks.api', () => ({
  fetchLibrary: vi.fn(),
  scanLibrary: vi.fn(),
  deleteLibraryBook: vi.fn(),
  fetchLibraryMatches: vi.fn(),
  saveLibraryMatch: vi.fn(),
}));
vi.mock('./audiobook-overlay', () => ({
  AudiobookOverlay: ({ children }: { children: React.ReactNode }) => (
    <div role="dialog">{children}</div>
  ),
}));
vi.mock('@tanstack/react-router', () => ({
  Link: ({ children, onClick }: { children: React.ReactNode; onClick?: () => void }) => (
    <a href="#" onClick={onClick}>
      {children}
    </a>
  ),
}));

const book = {
  asin: 'local:one',
  title: 'A Local Book',
  author: 'An Author',
  narrator: 'A Narrator',
  series_title: '',
  series_sequence: '',
  path: '/books/Local Book',
  file_count: 2,
  size_bytes: 104857600,
  audio_format: 'mp3',
  runtime_minutes: 90,
  imported_at: 1,
};
const response = {
  books: [book],
  totalBytes: book.size_bytes,
  root: '/books',
  scan: { status: 'never' as const },
};

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(fetchLibrary).mockResolvedValue(response);
});

describe('audiobook library', () => {
  it('shows local books and filters by narrator', async () => {
    render(<AudiobookLibraryModal onClose={() => {}} />);
    await screen.findByRole('heading', { name: 'A Local Book' });
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'A Narrator' } });
    expect(screen.getByRole('heading', { name: 'A Local Book' })).toBeInTheDocument();
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'Missing' } });
    expect(screen.getByText('No books match these filters.')).toBeInTheDocument();
  });

  it('starts the standard automation and prevents a second queued scan', async () => {
    vi.mocked(scanLibrary).mockResolvedValue();
    render(<AudiobookLibraryModal onClose={() => {}} />);
    await screen.findByRole('heading', { name: 'A Local Book' });
    fireEvent.click(screen.getByRole('button', { name: 'Scan folder' }));
    await waitFor(() => expect(scanLibrary).toHaveBeenCalledOnce());
    expect(screen.getByRole('button', { name: 'Scanning…' })).toBeDisabled();
  });

  it('shows a load failure instead of pretending the library is empty', async () => {
    vi.mocked(fetchLibrary).mockRejectedValue(new Error('Folder service unavailable'));
    render(<AudiobookLibraryModal onClose={() => {}} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Folder service unavailable');
    expect(screen.queryByText('Your books belong here')).not.toBeInTheDocument();
  });

  it('requires confirmation and updates the size after a successful deletion', async () => {
    vi.mocked(deleteLibraryBook).mockResolvedValue({ ok: true, recycled: true, error: '' });
    render(<AudiobookLibraryModal onClose={() => {}} />);
    await screen.findByRole('heading', { name: 'A Local Book' });
    fireEvent.click(screen.getByText('File details'));
    fireEvent.click(screen.getByRole('button', { name: 'Delete from disk' }));
    expect(deleteLibraryBook).not.toHaveBeenCalled();
    vi.mocked(fetchLibrary).mockResolvedValue({ ...response, books: [], totalBytes: 0 });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm delete' }));
    await screen.findByText('0 books · 0 MB on disk');
    expect(deleteLibraryBook).toHaveBeenCalledWith('local:one');
  });

  it('keeps a book visible when deletion fails', async () => {
    vi.mocked(deleteLibraryBook).mockResolvedValue({
      ok: false,
      recycled: false,
      error: 'Permission denied',
    });
    render(<AudiobookLibraryModal onClose={() => {}} />);
    await screen.findByRole('heading', { name: 'A Local Book' });
    fireEvent.click(screen.getByText('File details'));
    fireEvent.click(screen.getByRole('button', { name: 'Delete from disk' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm delete' }));
    expect(await screen.findByRole('status')).toHaveTextContent('Permission denied');
    expect(
      within(screen.getByRole('list')).getByRole('heading', { name: 'A Local Book' }),
    ).toBeInTheDocument();
  });
  it('distinguishes proven downloads from disk discoveries and unknown history', async () => {
    vi.mocked(fetchLibrary).mockResolvedValue({
      ...response,
      books: [
        { ...book, origin: 'soulsync' },
        { ...book, asin: 'local:two', title: 'Disk Book', origin: 'disk' },
        { ...book, asin: 'local:three', title: 'Old Book', origin: 'unknown' },
      ],
    });
    render(<AudiobookLibraryModal onClose={() => {}} />);
    await screen.findByRole('heading', { name: 'Disk Book' });
    fireEvent.change(screen.getByLabelText('Filter by download origin'), {
      target: { value: 'soulsync' },
    });
    expect(screen.getByRole('heading', { name: 'A Local Book' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Disk Book' })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Old Book' })).not.toBeInTheDocument();
  });

  it('searches editions and saves a confirmation with the reviewed snapshot', async () => {
    const snapshot = {
      scan_signature: 'files-v2',
      match_revision: 3,
      candidates: [
        {
          book: { asin: 'B000000001', title: 'Catalogue Book', author_names: ['An Author'] },
          score: 98,
          evidence: ['Narrator agrees'],
          conflicts: [],
          automatic_eligible: true,
        },
      ],
    };
    vi.mocked(fetchLibraryMatches).mockResolvedValue(snapshot);
    vi.mocked(saveLibraryMatch).mockResolvedValue();
    render(<AudiobookLibraryModal onClose={() => {}} />);
    await screen.findByRole('heading', { name: 'A Local Book' });
    fireEvent.click(screen.getByRole('button', { name: 'Unmatched' }));
    fireEvent.change(screen.getByLabelText('Search catalogue or enter ASIN'), {
      target: { value: 'B000000001' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Find editions' }));
    await screen.findByRole('heading', { name: 'Catalogue Book' });
    expect(fetchLibraryMatches).toHaveBeenCalledWith('local:one', 'B000000001');
    fireEvent.click(screen.getByRole('button', { name: 'Use this edition' }));
    await waitFor(() =>
      expect(saveLibraryMatch).toHaveBeenCalledWith('local:one', snapshot, 'confirm', 'B000000001'),
    );
    await screen.findByRole('heading', { name: 'A Local Book' });
  });
});
