import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it } from 'vitest';

import { ReviewPage } from './review-page';
import { fetchMock, reply, finding, auxiliary } from './support/page';
import { unexpectedRequest } from './support/requests';

it.each(['metadata', 'user', undefined])('applies a ready plan (%s)', async (evidence) => {
  const readyFinding = {
    ...finding,
    finding_type: 'reconciliation_metadata',
    title: 'Retag Laid to Rest',
    details: {
      album_id: 10,
      album_title: 'Ashes of the Wake',
      artist_name: 'Lamb of God',
      assignment_evidence: evidence,
      changes: {
        album_artist: { old: 'Various Artists', new: 'Lamb of God' },
        ...(evidence !== 'metadata'
          ? { musicbrainz_recording_id: { old: 'old-id', new: 'new-id' } }
          : {}),
      },
    },
    review_tier: 'ready',
    review_reason:
      evidence === 'user'
        ? 'The assignment was confirmed during review'
        : 'Metadata supports this repair; audio identity is not independently confirmed',
  };
  const plan = {
    ready: 1,
    manual: 0,
    destructive: 0,
    blocked: 0,
    total: 1,
    status: 'ready',
    blocker_reasons: [],
    can_apply_ready: true,
    can_organize: true,
    decision_count: 1,
    categories: ['metadata'],
    decisions: [
      {
        id: 'metadata',
        category: 'metadata',
        label: 'Album artist update',
        risk: 'ready',
        summary: '1 track: Various Artists → Lamb of God.',
        finding_ids: [7],
        action_count: 1,
      },
    ],
  };
  fetchMock.mockImplementation((url: string, options?: RequestInit) => {
    if (url === '/api/repair/finding-types')
      return Promise.resolve(
        reply({
          types: [
            {
              type: 'reconciliation_metadata',
              label: 'Metadata repair',
              verb: 'Apply repair',
              fixable: true,
              destructive: false,
            },
          ],
        }),
      );
    if (url.includes('/reorganize/preview'))
      return Promise.resolve(reply({ success: true, tracks: [] }));
    if (url.startsWith('/api/repair/review-queue/apply-ready?'))
      return Promise.resolve(
        reply({
          signature: 'reviewed',
          action_count: 1,
          file_actions: 0,
          albums: [
            {
              album: 'Ashes of the Wake',
              case_id: 'album:1:ashes',
              actions: [{ id: 7, description: 'Update artist metadata' }],
            },
          ],
        }),
      );
    if (url === '/api/repair/review-queue/apply-ready') {
      expect(JSON.parse(String(options?.body))).toEqual({
        case_id: 'album:1:ashes',
        organize: false,
        signature: 'reviewed',
        case_ids: ['album:1:ashes'],
        organize_case_ids: [],
      });
      return Promise.resolve(reply({ started: true, total: 1 }));
    }
    if (url === '/api/repair/bulk-fix/status')
      return Promise.resolve(reply({ running: false, fixed: 1, failed: 0, total: 1 }));
    const common = auxiliary(url);
    if (common) return Promise.resolve(common);
    if (url.startsWith('/api/repair/review-queue?'))
      return Promise.resolve(
        reply({
          items: [
            {
              case_id: 'album:1:ashes',
              album: 'Ashes of the Wake',
              artist: 'Lamb of God',
              pending: 1,
              priority: 1,
              finding_types: ['reconciliation_metadata'],
              error_count: 0,
              plan,
            },
          ],
          total: 1,
          page: 0,
          limit: 30,
        }),
      );
    if (url.startsWith('/api/repair/review-queue/album%3A1%3Aashes?'))
      return Promise.resolve(
        reply({
          case_id: 'album:1:ashes',
          album: 'Ashes of the Wake',
          artist: 'Lamb of God',
          album_id: 10,
          folder: '/music/Lamb of God/Ashes of the Wake',
          count: 1,
          status: 'pending',
          plan,
          items: [readyFinding],
        }),
      );
    return unexpectedRequest(url);
  });

  render(<ReviewPage />);

  expect((await screen.findAllByText('Various Artists → Lamb of God')).length).toBeGreaterThan(0);
  expect(screen.getByText('Plan ready to apply · 1 action')).toBeTruthy();
  expect(screen.getByText(/All required decisions are planned/)).toBeVisible();
  expect(
    screen.queryByText(
      /passed the album and audio checks|supported by the album and audio evidence/,
    ),
  ).toBeNull();
  expect(screen.queryByRole('heading', { name: '1 question for this album' })).toBeNull();
  expect(screen.queryByText('Review ready changes')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Apply 1 ready change' }));
  await screen.findByRole('dialog', { name: 'Apply ready album plans' });
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(
    fetchMock.mock.calls.filter(
      ([url, options]) =>
        url === '/api/repair/review-queue/apply-ready' && options?.method === 'POST',
    ),
  ).toHaveLength(0);
  expect(screen.queryByText(/Applying album changes/)).toBeNull();
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Apply 1 ready change' })).not.toBeDisabled(),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Apply 1 ready change' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Apply selected plans' }));
  await waitFor(() => expect(screen.getByText('1 album action completed.')).toBeTruthy(), {
    timeout: 3000,
  });
});

it('keeps an album selected when reopening the already visible ready batch', async () => {
  window.history.replaceState(null, '', '/review?mode=ready');
  fetchMock.mockImplementation((url: string) => {
    const common = auxiliary(url);
    if (common) return Promise.resolve(common);
    if (url.startsWith('/api/repair/review-queue?'))
      return Promise.resolve(
        reply({
          items: [
            {
              case_id: 'album:1:10',
              album: 'Ready Album',
              artist: 'Artist',
              pending: 1,
              finding_types: [],
            },
          ],
          total: 1,
          page: 0,
          limit: 30,
        }),
      );
    if (url.startsWith('/api/repair/review-queue/album%3A1%3A10'))
      return Promise.resolve(
        reply({
          case_id: 'album:1:10',
          album: 'Ready Album',
          artist: 'Artist',
          folder: '/music/Album',
          count: 1,
          status: 'pending',
          items: [finding],
        }),
      );
    return unexpectedRequest(url);
  });
  render(<ReviewPage />);
  await screen.findByRole('heading', { name: 'Ready Album' });
  fireEvent.click(screen.getByRole('button', { name: /^Ready to apply/ }));
  expect(screen.getByRole('heading', { name: 'Ready Album' })).toBeTruthy();
});
