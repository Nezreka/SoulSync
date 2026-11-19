import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ADULT, clearPerms, stubPerms } from '@/test/perms';

import { fetchBlocklist, fetchRecycleBin } from '../-audiobooks.api';
import { AudiobookReviewModal } from './audiobook-review-modal';

vi.mock('../-audiobooks.api', () => ({
  fetchRecycleBin: vi.fn(),
  fetchBlocklist: vi.fn(),
  clearBlocklist: vi.fn(),
  emptyRecycleBin: vi.fn(),
  purgeRecycledBook: vi.fn(),
  restoreRecycledBook: vi.fn(),
  unblockRelease: vi.fn(),
}));
vi.mock('./audiobook-overlay', () => ({
  AudiobookOverlay: ({ children }: { children: React.ReactNode }) => (
    <div role="dialog">{children}</div>
  ),
}));

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(fetchRecycleBin).mockResolvedValue({ entries: [], keepDays: 7 });
  vi.mocked(fetchBlocklist).mockResolvedValue([]);
});
afterEach(() => {
  cleanup();
  clearPerms();
});

describe('the bin (#2201)', () => {
  it('the admin gets the recycle bin and the blocklist', async () => {
    stubPerms(ADULT, { isAdmin: true });
    render(<AudiobookReviewModal onClose={() => {}} />);
    await screen.findByText('The bin is empty');
    expect(screen.getByRole('button', { name: /Recycle bin/ })).toBeTruthy();
    expect(fetchRecycleBin).toHaveBeenCalledOnce();
  });

  it('a member who gets books keeps their blocklist and never reads the recycle bin', async () => {
    stubPerms(ADULT);
    render(<AudiobookReviewModal onClose={() => {}} />);
    await screen.findByRole('button', { name: /Blocklist/ });
    expect(screen.queryByRole('button', { name: /Recycle bin/ })).toBeNull();
    expect(screen.queryByText('The bin is empty')).toBeNull();
    expect(fetchRecycleBin).not.toHaveBeenCalled();
    expect(fetchBlocklist).toHaveBeenCalledOnce();
  });
});
